#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Credential-free subscription awareness and mixed-provider routing policy."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import importlib.util
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
from urllib.parse import urlsplit


def _load_policy_schema() -> Any:
    path = Path(__file__).with_name("airlock_policy.py")
    spec = importlib.util.spec_from_file_location("airlock_managed_policy", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("managed policy module is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


POLICY_SCHEMA = _load_policy_schema()


def _load_openrouter_presets() -> Any:
    path = Path(__file__).with_name("airlock_openrouter_presets.py")
    spec = importlib.util.spec_from_file_location(
        "airlock_managed_openrouter_presets", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("managed OpenRouter preset module is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    had_policy_module = "airlock_policy" in sys.modules
    previous_policy_module = sys.modules.get("airlock_policy")
    sys.modules["airlock_policy"] = POLICY_SCHEMA
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    finally:
        if had_policy_module:
            sys.modules["airlock_policy"] = previous_policy_module
        else:
            sys.modules.pop("airlock_policy", None)
    return module


OPENROUTER_PRESETS = _load_openrouter_presets()

SCHEMA_VERSION = 2
MANAGED_BUNDLE_SCHEMA_VERSION = 1
MANAGED_BUNDLE_VERSION = "2026.08.22.3"
MANAGED_PROTOCOL_VERSION = 5
MAX_MANAGED_BUNDLE_BYTES = 128 * 1024
MAX_MANAGED_COMPONENT_BYTES = 16 * 1024 * 1024
READABLE_SCHEMA_VERSIONS = {1, SCHEMA_VERSION}
MAX_POLICY_BYTES = 128 * 1024
MAX_CLAUDE_STATE_BYTES = 10 * 1024 * 1024
MAX_SESSION_DIAGNOSTICS_BYTES = 512 * 1024
MAX_SESSION_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_FAST_TRANSITION_BYTES = 4096
MAX_FAST_TRANSITION_STDIN_BYTES = 4096
FAST_TRANSITION_TTL_SECONDS = 120
FAST_TRANSITION_SCHEMA_VERSION = 1
FAST_TRANSITION_ROUTE = "sol-fast"
FAST_TRANSITION_MODEL = "gpt-5.6-sol-fast"
FAST_TRANSITION_ENV_CHANNEL = "AIRLOCK_FAST_TRANSITION_CHANNEL"
FAST_TRANSITION_ENV_NONCE = "AIRLOCK_FAST_TRANSITION_NONCE"
SESSION_DIAGNOSTICS_TIMEOUT_SECONDS = 3
MAX_SESSION_DIAGNOSTIC_EVENTS = 256
SESSION_USAGE_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)
APP_SERVER_TIMEOUT_SECONDS = 15
VALID_ACCESS = {"included", "extra", "unavailable", "unknown"}
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# A worker with no effort of its own inherits the session level, so /effort moves
# the root and every worker together. A named level pins that worker instead.
INHERIT_EFFORT = "inherit"
VALID_WORKER_EFFORTS = (INHERIT_EFFORT,) + VALID_EFFORTS
VALID_EXTRA_POLICIES = {"ask", "never", "allow"}
VALID_FAILOVER_POLICIES = {"ask", "never", "allow"}
VALID_DESCENDANT_POLICIES = {"bounded", "off"}
VALID_ROUTING_POLICIES = {"balanced", "quality", "economy"}
VALID_SWARM_FAST_POLICIES = {"auto", "on", "off"}
VALID_PROVIDER_FAST_POLICIES = {"on", "off"}
VALID_RISKS = ("low", "standard", "high", "critical")
VALID_CLAUDE_PLANS = {"unknown", "pro", "max5x", "max20x"}
VALID_OPENAI_CAPACITIES = {"auto", "1x", "5x", "20x"}
DEFAULT_MAX_CONCURRENT_SUBAGENTS = "off"
DEFAULT_SWARM_FAST = "auto"
DEFAULT_OPENAI_FAST = "off"
DEFAULT_ANTHROPIC_FAST = "off"
DEFAULT_AGENT_DEPTH = "1"
DEFAULT_DESCENDANT_POLICY = "bounded"
DEFAULT_MAX_DESCENDANTS = "2"
DEFAULT_MAX_CONCURRENT_DESCENDANTS = "3"
DEFAULT_MAX_REPAIR_ROUNDS = "2"
MAX_CONFIGURED_SUBAGENTS = 20
MAX_REPAIR_ROUNDS = 5
USAGE_FRESH_SECONDS = 15 * 60
OPENAI_PLAN_MAP_VERSION = "2026-07"
OPENAI_PLAN_CAPACITY = {
    "prolite": (5, "inferred"),
    "pro": (20, "inferred"),
}
OPENAI_FAST_ELIGIBLE_PLANS = {"prolite", "pro"}
MINIMUM_FAST_PROXY_VERSION = (0, 1, 22)
VALID_AGENT_DEPTHS = frozenset({"1", "2"})


NESTED_AGENT_RULE = (
    "You may invoke Agent, but only to spawn your own Agent type, so every "
    "worker you start runs your model. Never pass a model override, and keep "
    "fan-out to what the task needs."
)


def agent_depth_note(depth: str) -> str:
    """Describe the configured Agent spawn depth in one line."""

    if depth == "2":
        return (
            "Agent depth: 2; named Agents may spawn only their own type, so every "
            "descendant keeps the model the root chose"
        )
    return "Agent depth: 1; named Agents cannot invoke Agent"


def agent_guidance_clause(policy: dict) -> str:
    """Describe fan-out rules for the session guidance text."""

    if policy["policies"].get("agent_depth") == "2":
        return (
            "Agents may invoke Agent, but only to spawn their own Agent type, so a "
            "worker's descendants all run the model you chose for it. Choose the model "
            "for each worker at the root. "
        )
    return (
        "Agents cannot invoke Agent, and the native session spawn depth is one. Keep "
        "every fan-out decision at the root. "
    )
MODE_CONFIG_KEYS = {
    "routing": "AIRLOCK_ROUTING_POLICY",
    "extra_usage": "AIRLOCK_EXTRA_USAGE_POLICY",
    "max_agents": "AIRLOCK_MAX_CONCURRENT_SUBAGENTS",
    "openai_fast": "AIRLOCK_OPENAI_FAST",
    "anthropic_fast": "AIRLOCK_ANTHROPIC_FAST",
    "swarm_fast": "AIRLOCK_SWARM_FAST",
    "failover": "AIRLOCK_FAILOVER_POLICY",
    "agent_depth": "AIRLOCK_AGENT_DEPTH",
    "descendants": "AIRLOCK_DESCENDANT_POLICY",
    "max_descendants": "AIRLOCK_MAX_DESCENDANTS_PER_WORKER",
    "max_total_descendants": "AIRLOCK_MAX_CONCURRENT_DESCENDANTS",
    "repair_rounds": "AIRLOCK_MAX_REPAIR_ROUNDS",
}
USAGE_CONFIG_KEYS = {
    "claude_plan": "AIRLOCK_ANTHROPIC_PLAN",
    "openai_capacity": "AIRLOCK_OPENAI_CAPACITY",
}
EFFORT_BASELINES = {
    "economy": {"low": "low", "standard": "low", "high": "medium", "critical": "high"},
    "balanced": {"low": "low", "standard": "medium", "high": "high", "critical": "xhigh"},
    "quality": {"low": "medium", "standard": "high", "high": "xhigh", "critical": "max"},
}
ROUTING_OBJECTIVES = {
    "balanced": "Balance task fit, capability, access, and relative usage; use a stronger eligible worker when the expected benefit justifies it.",
    "quality": "Prefer a higher-capability eligible worker when it is materially useful for correctness, depth, or difficult synthesis.",
    "economy": "Prefer an adequate lower-relative-usage eligible worker and escalate only when task risk or complexity warrants it.",
}
PROVIDER_ROUTES = {
    "anthropic": ("opus", "sonnet", "fable", "haiku"),
    "openai": ("sol", "terra", "luna", "luna-fast"),
    "grok": ("grok", "composer"),
}
MODE_PROVIDERS = {"proxy": "anthropic", "native": "openai"}
PROFILE_COMPONENTS = {
    "openrouter-pure": (),
    "openai-pure": (("openai", "openai_direct"),),
    "grok-pure": (("grok", "grok_direct"),),
    "hybrid-openai-root": (
        ("openai", "openai_direct"),
        ("anthropic", "anthropic_wrappers"),
        ("grok", "grok_wrappers"),
    ),
    "hybrid-anthropic-root": (
        ("anthropic", "anthropic_direct"),
        ("openai", "openai_wrappers"),
        ("grok", "grok_wrappers"),
    ),
    "hybrid-grok-root": (
        ("grok", "grok_direct"),
        ("openai", "openai_wrappers"),
        ("anthropic", "anthropic_wrappers"),
    ),
    "hybrid-openrouter-root": (
        ("openai", "openai_wrappers"),
        ("anthropic", "anthropic_wrappers"),
        ("grok", "grok_wrappers"),
    ),
}
PROFILE_ROOT_PROVIDERS = {
    "openrouter-pure": "openrouter",
    "openai-pure": "openai",
    "grok-pure": "grok",
    "hybrid-openai-root": "openai",
    "hybrid-anthropic-root": "anthropic",
    "hybrid-grok-root": "grok",
    "hybrid-openrouter-root": "openrouter",
}
CATALOG_EXPECTED_AGENTS = {
    "openai_direct": {"airlock-sol", "airlock-terra", "airlock-luna", "airlock-luna-fast"},
    "openai_wrappers": {"airlock-sol", "airlock-terra", "airlock-luna", "airlock-luna-fast"},
    "anthropic_direct": {"airlock-opus", "airlock-sonnet", "airlock-fable", "airlock-haiku"},
    "anthropic_wrappers": {"airlock-opus", "airlock-sonnet", "airlock-fable", "airlock-haiku"},
    "grok_direct": {"airlock-grok", "airlock-composer"},
    "grok_wrappers": {"airlock-grok", "airlock-composer"},
}
MAX_AGENT_CATALOG_BYTES = 24 * 1024
MAX_RENDERED_AGENTS_BYTES = 24 * 1024
MODEL_PROFILES = {
    # Claude Code only grants Opus 5, Sonnet 5, and Fable 5 their native 1M
    # window when ANTHROPIC_BASE_URL is unset or points at api.anthropic.com,
    # and Airlock always points it at the session router. The [1m] suffix is
    # the one lever that still reaches 1M from behind the router. Haiku 4.5 is
    # a genuine 200000 model and must not carry the suffix.
    "anthropic": {
        "opus": {
            "agent": "airlock-opus", "model": "claude-opus-5[1m]", "effort": "xhigh",
            "capability": "frontier", "cost": "premium",
            "strength": "difficult architecture, UI/UX design and visual direction, product-flow and design-system work, long-horizon planning, complex debugging, security reasoning, high-impact review, and synthesis",
        },
        "sonnet": {
            "agent": "airlock-sonnet", "model": "claude-sonnet-5[1m]", "effort": "high",
            "capability": "general", "cost": "standard",
            "strength": "deep repository research, requirements synthesis, broad code review, documentation, design-system-aligned UI implementation, iterative frontend refinement, ambiguous debugging, and balanced implementation",
        },
        "fable": {
            "agent": "airlock-fable", "model": "claude-fable-5[1m]", "effort": "high",
            "capability": "frontier-efficient", "cost": "metered",
            "strength": "efficient frontier implementation, orchestration, and analysis when the route is enabled or explicitly selected",
        },
        "haiku": {
            "agent": "airlock-haiku", "model": "claude-haiku-4-5-20251001", "effort": "medium",
            "capability": "utility", "cost": "economical",
            "strength": "fast bounded utility work when the route is enabled or explicitly selected",
        },
    },
    "openai": {
        "sol": {
            "agent": "airlock-sol", "model": "gpt-5.6-sol", "effort": "xhigh",
            "capability": "frontier", "cost": "premium",
            "strength": "difficult implementation, cross-file integration, backend and API work, test-driven repair, measured performance work, difficult debugging, and synthesis",
        },
        "terra": {
            "agent": "airlock-terra", "model": "gpt-5.6-terra", "effort": "high",
            "capability": "general", "cost": "standard",
            "strength": "independent second opinions, adversarial review, competing designs, and debugging hypotheses",
        },
        "luna": {
            "agent": "airlock-luna", "model": "gpt-5.6-luna", "effort": "max",
            "capability": "utility", "cost": "economical",
            "strength": "high-volume discovery, webpage reading, extraction, lookup, summarization, test or log triage, and small mechanical work",
        },
        "luna-fast": {
            "agent": "airlock-luna-fast", "model": "gpt-5.6-luna-fast", "effort": "max",
            "capability": "utility", "cost": "economical-fast",
            "strength": "priority-processed high-volume discovery, reading, extraction, lookup, summarization, and triage on eligible OpenAI plans",
        },
    },
    "grok": {
        "grok": {
            "agent": "airlock-grok", "model": "grok-4.5", "effort": "xhigh",
            "capability": "frontier", "cost": "premium",
            "strength": "long-horizon agentic coding, tool-heavy and terminal work, multi-step debugging that must hold context across many turns, and token-efficient execution on a Grok subscription",
        },
        "composer": {
            "agent": "airlock-composer", "model": "grok-composer-2.5-fast", "effort": "high",
            "capability": "general", "cost": "economical",
            "strength": "fast low-latency agentic coding loops, automated debugging, web implementation, and bounded multi-step edits that need speed more than depth",
        },
    },
}
MANAGED_AGENT_NAMES = frozenset(
    profile["agent"]
    for provider in MODEL_PROFILES.values()
    for profile in provider.values()
)
OPENAI_MANAGED_AGENT_NAMES = frozenset(
    profile["agent"] for profile in MODEL_PROFILES["openai"].values()
)
ANTHROPIC_MANAGED_AGENT_NAMES = frozenset(
    profile["agent"] for profile in MODEL_PROFILES["anthropic"].values()
)
GROK_MANAGED_AGENT_NAMES = frozenset(
    profile["agent"] for profile in MODEL_PROFILES["grok"].values()
)
PLAN_KEYS = ("plan", "tier", "subscriptionPlan", "subscriptionTier")


class AccessError(RuntimeError):
    pass


def config_path() -> Path:
    configured = os.environ.get("AIRLOCK_CONFIG_FILE")
    if configured:
        return Path(configured).expanduser()
    configured_dir = os.environ.get("AIRLOCK_CONFIG_DIR")
    if configured_dir:
        return Path(configured_dir).expanduser() / "config"
    xdg_root = os.environ.get("XDG_CONFIG_HOME")
    if xdg_root:
        return Path(xdg_root).expanduser() / "airlock" / "config"

    home = Path.home()
    standard_root = home / ".config" / "airlock"
    fallback_root = home / ".airlock"
    standard_parent = standard_root.parent
    if (fallback_root / "config").is_file() or fallback_root.is_dir():
        return fallback_root / "config"
    if standard_root.is_dir() and os.access(standard_root, os.W_OK):
        return standard_root / "config"
    if (
        not standard_root.exists()
        and standard_parent.is_dir()
        and os.access(standard_parent, os.W_OK)
    ):
        return standard_root / "config"
    if not standard_parent.exists() and os.access(home, os.W_OK):
        return standard_root / "config"
    return fallback_root / "config"


def openrouter_registry_path() -> Path:
    configured = os.environ.get("AIRLOCK_OPENROUTER_REGISTRY_FILE")
    if configured:
        return Path(configured).expanduser()
    return config_path().parent / "openrouter-registry.json"


def load_openrouter_registry() -> Any:
    path = openrouter_registry_path()
    try:
        return POLICY_SCHEMA.load_openrouter_registry(path)
    except POLICY_SCHEMA.RegistryNotFoundError:
        return POLICY_SCHEMA.validate_openrouter_registry({
            "schema_version": 1,
            "models": [],
        })
    except POLICY_SCHEMA.PolicyValidationError as exc:
        raise AccessError(f"OpenRouter registry is invalid: {exc}") from exc


def access_path() -> Path:
    configured = os.environ.get("AIRLOCK_ACCESS_FILE")
    if configured:
        return Path(configured).expanduser()
    return config_path().parent / "access.json"


def _default_usage(provider: str) -> dict[str, object]:
    return {
        "source": "not_checked",
        "checked_at": None,
        "buckets": [],
        "credit_state": "unknown" if provider == "openai" else "not_exposed",
    }


def default_policy() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "policies": {
            "extra_usage": "ask",
            "agent_depth": DEFAULT_AGENT_DEPTH,
            "routing": "balanced",
            "openai_fast": DEFAULT_OPENAI_FAST,
            "anthropic_fast": DEFAULT_ANTHROPIC_FAST,
            "swarm_fast": DEFAULT_SWARM_FAST,
            "failover": "ask",
            "descendants": "bounded",
            "max_descendants": 2,
            "max_concurrent_descendants": 3,
            "repair_rounds": 2,
            "allowed_efforts": {
                "anthropic": list(VALID_EFFORTS),
                "openai": list(VALID_EFFORTS),
                "grok": list(VALID_EFFORTS),
            },
            "worker_effort": {
                route: INHERIT_EFFORT
                for routes in PROVIDER_ROUTES.values()
                for route in routes
            },
        },
        "providers": {
            "anthropic": {
                "authenticated": None,
                "detected_plan": "unknown",
                "plan_source": "not_checked",
                "account_metadata": {},
                "usage": _default_usage("anthropic"),
                "models": {
                    "opus": {"access": "unknown"},
                    "sonnet": {"access": "unknown"},
                    "fable": {"access": "unavailable"},
                    "haiku": {"access": "unknown"},
                },
            },
            "openai": {
                "authenticated": None,
                "detected_plan": "unknown",
                "plan_source": "not_checked",
                "account_metadata": {},
                "usage": _default_usage("openai"),
                "models": {
                    route: {"access": "unknown"} for route in PROVIDER_ROUTES["openai"]
                },
            },
            "grok": {
                "authenticated": None,
                "detected_plan": "unknown",
                "plan_source": "not_checked",
                "account_metadata": {},
                "usage": _default_usage("grok"),
                # Off until AIRLOCK_GROK_MODELS enables routes (pure grok launch enables them).
                "models": {
                    route: {"access": "unavailable"} for route in PROVIDER_ROUTES["grok"]
                },
            },
        },
    }


def read_flat_config(path: Path | None = None) -> dict[str, str]:
    target = path or config_path()
    if not target.is_file() or target.is_symlink():
        return {}
    values: dict[str, str] = {}
    try:
        for raw_line in target.read_text(encoding="utf-8-sig").splitlines():
            if not raw_line or raw_line.startswith("#") or "=" not in raw_line:
                continue
            key, value = raw_line.split("=", 1)
            if key.startswith("AIRLOCK_"):
                values[key] = value.rstrip("\r")
    except (OSError, UnicodeError):
        return {}
    return values


def _valid_max_agents(value: object) -> bool:
    if value == "off":
        return True
    return _valid_positive_limit(value)


def _valid_positive_limit(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]?", value):
        return False
    return 1 <= int(value) <= MAX_CONFIGURED_SUBAGENTS


def _valid_repair_rounds(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[0-5]", value):
        return False
    return 0 <= int(value) <= MAX_REPAIR_ROUNDS


def mode_state() -> dict[str, object]:
    config = read_flat_config()
    defaults = {
        "routing": "balanced",
        "extra_usage": "ask",
        "max_agents": DEFAULT_MAX_CONCURRENT_SUBAGENTS,
        "openai_fast": DEFAULT_OPENAI_FAST,
        "anthropic_fast": DEFAULT_ANTHROPIC_FAST,
        "swarm_fast": DEFAULT_SWARM_FAST,
        "failover": "ask",
        "agent_depth": DEFAULT_AGENT_DEPTH,
        "descendants": DEFAULT_DESCENDANT_POLICY,
        "max_descendants": DEFAULT_MAX_DESCENDANTS,
        "max_total_descendants": DEFAULT_MAX_CONCURRENT_DESCENDANTS,
        "repair_rounds": DEFAULT_MAX_REPAIR_ROUNDS,
    }
    validators: dict[str, object] = {
        "routing": VALID_ROUTING_POLICIES,
        "extra_usage": VALID_EXTRA_POLICIES,
        "max_agents": _valid_max_agents,
        "openai_fast": VALID_PROVIDER_FAST_POLICIES,
        "anthropic_fast": VALID_PROVIDER_FAST_POLICIES,
        "swarm_fast": VALID_SWARM_FAST_POLICIES,
        "failover": VALID_FAILOVER_POLICIES,
        "agent_depth": VALID_AGENT_DEPTHS,
        "descendants": VALID_DESCENDANT_POLICIES,
        "max_descendants": _valid_positive_limit,
        "max_total_descendants": _valid_positive_limit,
        "repair_rounds": _valid_repair_rounds,
    }

    def valid(name: str, value: object) -> bool:
        validator = validators[name]
        return validator(value) if callable(validator) else value in validator

    saved: dict[str, str] = {}
    effective: dict[str, str] = {}
    sources: dict[str, str] = {}
    environment: dict[str, str | None] = {}
    ignored = []
    for name, key in MODE_CONFIG_KEYS.items():
        raw_saved = config.get(key)
        saved[name] = raw_saved if valid(name, raw_saved) else defaults[name]
        sources[name] = "config" if valid(name, raw_saved) else "default"
        raw_environment = os.environ.get(key)
        environment[name] = raw_environment if valid(name, raw_environment) else None
        if raw_environment is not None and environment[name] is None:
            ignored.append(f"{key}={raw_environment}")
        effective[name] = environment[name] or saved[name]
    return {
        "config_path": config_path(),
        "saved_routing": saved["routing"],
        "saved_extra_usage": saved["extra_usage"],
        "saved_agent_depth": saved["agent_depth"],
        "saved_max_agents": saved["max_agents"],
        "saved_openai_fast": saved["openai_fast"],
        "saved_anthropic_fast": saved["anthropic_fast"],
        "saved_swarm_fast": saved["swarm_fast"],
        "saved_failover": saved["failover"],
        "saved_descendants": saved["descendants"],
        "saved_max_descendants": saved["max_descendants"],
        "saved_max_total_descendants": saved["max_total_descendants"],
        "saved_repair_rounds": saved["repair_rounds"],
        "saved_routing_source": sources["routing"],
        "saved_extra_source": sources["extra_usage"],
        "saved_max_source": sources["max_agents"],
        "saved_openai_fast_source": sources["openai_fast"],
        "saved_anthropic_fast_source": sources["anthropic_fast"],
        "saved_swarm_fast_source": sources["swarm_fast"],
        "effective_routing": effective["routing"],
        "effective_extra_usage": effective["extra_usage"],
        "effective_max_agents": effective["max_agents"],
        "effective_openai_fast": effective["openai_fast"],
        "effective_anthropic_fast": effective["anthropic_fast"],
        "effective_swarm_fast": effective["swarm_fast"],
        "effective_failover": effective["failover"],
        "effective_agent_depth": effective["agent_depth"],
        "effective_descendants": effective["descendants"],
        "effective_max_descendants": effective["max_descendants"],
        "effective_max_total_descendants": effective["max_total_descendants"],
        "effective_repair_rounds": effective["repair_rounds"],
        "routing_environment_override": environment["routing"],
        "extra_environment_override": environment["extra_usage"],
        "max_environment_override": environment["max_agents"],
        "openai_fast_environment_override": environment["openai_fast"],
        "anthropic_fast_environment_override": environment["anthropic_fast"],
        "swarm_fast_environment_override": environment["swarm_fast"],
        "ignored_environment": ignored,
    }


def mode_status_lines(updated: bool = False) -> list[str]:
    state = mode_state()
    routing_note = f"saved from {state['saved_routing_source']}"
    extra_note = f"saved from {state['saved_extra_source']}"
    max_note = f"saved from {state['saved_max_source']}"
    openai_fast_note = f"saved from {state['saved_openai_fast_source']}"
    anthropic_fast_note = f"saved from {state['saved_anthropic_fast_source']}"
    swarm_fast_note = f"saved from {state['saved_swarm_fast_source']}"
    if state["routing_environment_override"]:
        routing_note += f"; environment override={state['routing_environment_override']}"
    if state["extra_environment_override"]:
        extra_note += f"; environment override={state['extra_environment_override']}"
    if state["max_environment_override"]:
        max_note += f"; environment override={state['max_environment_override']}"
    if state["openai_fast_environment_override"]:
        openai_fast_note += f"; environment override={state['openai_fast_environment_override']}"
    if state["anthropic_fast_environment_override"]:
        anthropic_fast_note += f"; environment override={state['anthropic_fast_environment_override']}"
    if state["swarm_fast_environment_override"]:
        swarm_fast_note += f"; environment override={state['swarm_fast_environment_override']}"
    lines = []
    if updated:
        lines.append("Airlock mode updated.")
    lines.extend([
        f"Config: {state['config_path']}",
        f"Routing: {state['effective_routing']} ({routing_note})",
        f"Extra usage: {state['effective_extra_usage']} ({extra_note})",
        f"Failover: {state['effective_failover']}",
        f"Max concurrent top-level subagents: {state['effective_max_agents']} ({max_note})",
        f"OpenAI Fast routes: {state['effective_openai_fast']} ({openai_fast_note})",
        f"Anthropic Fast startup: {state['effective_anthropic_fast']} ({anthropic_fast_note})",
        f"Luna swarm Fast selection: {state['effective_swarm_fast']} ({swarm_fast_note})",
        agent_depth_note(state["effective_agent_depth"]),
        "Defaults: routing=balanced, extra-usage=ask, failover=ask, max-agents=off, provider Fast=off, swarm-fast=auto",
    ])
    if (
        state["effective_routing"] == "economy"
        and state["effective_extra_usage"] == "never"
        and state["effective_openai_fast"] == "off"
        and state["effective_anthropic_fast"] == "off"
        and state["effective_failover"] == "never"
    ):
        lines.append("Preset: budget")
    elif (
        state["effective_routing"] == "balanced"
        and state["effective_extra_usage"] == "ask"
        and state["effective_max_agents"] == DEFAULT_MAX_CONCURRENT_SUBAGENTS
        and state["effective_openai_fast"] == DEFAULT_OPENAI_FAST
        and state["effective_anthropic_fast"] == DEFAULT_ANTHROPIC_FAST
        and state["effective_swarm_fast"] == DEFAULT_SWARM_FAST
        and state["effective_failover"] == "ask"
    ):
        lines.append("Preset: defaults")
    if state["ignored_environment"]:
        lines.append(
            "Ignored invalid environment override(s): "
            + ", ".join(state["ignored_environment"])
        )
    lines.append(
        "Changes apply to newly launched airlock sessions; exit and relaunch active sessions."
    )
    return lines


def write_flat_config_overrides(
    updates: dict[str, str | None], path: Path | None = None
) -> None:
    target = path or config_path()
    allowed: dict[str, object] = {
        MODE_CONFIG_KEYS["routing"]: VALID_ROUTING_POLICIES,
        MODE_CONFIG_KEYS["extra_usage"]: VALID_EXTRA_POLICIES,
        MODE_CONFIG_KEYS["max_agents"]: _valid_max_agents,
        MODE_CONFIG_KEYS["openai_fast"]: VALID_PROVIDER_FAST_POLICIES,
        MODE_CONFIG_KEYS["anthropic_fast"]: VALID_PROVIDER_FAST_POLICIES,
        MODE_CONFIG_KEYS["swarm_fast"]: VALID_SWARM_FAST_POLICIES,
        MODE_CONFIG_KEYS["failover"]: VALID_FAILOVER_POLICIES,
        MODE_CONFIG_KEYS["agent_depth"]: VALID_AGENT_DEPTHS,
        MODE_CONFIG_KEYS["descendants"]: VALID_DESCENDANT_POLICIES,
        MODE_CONFIG_KEYS["max_descendants"]: _valid_positive_limit,
        MODE_CONFIG_KEYS["max_total_descendants"]: _valid_positive_limit,
        MODE_CONFIG_KEYS["repair_rounds"]: _valid_repair_rounds,
        USAGE_CONFIG_KEYS["claude_plan"]: VALID_CLAUDE_PLANS,
        USAGE_CONFIG_KEYS["openai_capacity"]: VALID_OPENAI_CAPACITIES,
    }
    if not updates:
        raise AccessError("config update requires at least one setting")
    for key, value in updates.items():
        validator = allowed.get(key)
        if validator is None:
            raise AccessError(f"invalid Airlock setting: {key}={value}")
        if value is None:
            continue
        valid = validator(value) if callable(validator) else value in validator
        if not valid:
            raise AccessError(f"invalid Airlock setting: {key}={value}")
    if target.is_symlink():
        raise AccessError(f"refusing to replace symlinked config: {target}")
    if target.exists() and not target.is_file():
        raise AccessError(f"config target is not a regular file: {target}")

    raw = ""
    existing_mode = 0o644
    if target.exists():
        if target.stat().st_size > MAX_POLICY_BYTES:
            raise AccessError("config file is too large")
        try:
            with target.open("r", encoding="utf-8-sig", newline="") as stream:
                raw = stream.read()
            existing_mode = target.stat().st_mode & 0o777
        except (OSError, UnicodeError) as exc:
            raise AccessError(f"config could not be read: {target}") from exc
    newline = "\r\n" if "\r\n" in raw else "\n"
    if raw:
        lines = raw.splitlines(keepends=True)
    else:
        lines = [
            "# Managed by https://github.com/Harshkamdar67/Airlock" + newline,
            "# Mode settings written by airlock. Do not put credentials in this file." + newline,
        ]

    for key, value in updates.items():
        matching = []
        for index, line in enumerate(lines):
            content = line.rstrip("\r\n")
            if "=" in content and content.split("=", 1)[0] == key:
                matching.append(index)
        if value is None:
            for index in reversed(matching):
                del lines[index]
            continue
        replacement = f"{key}={value}"
        if matching:
            index = matching[-1]
            ending = lines[index][len(lines[index].rstrip("\r\n")):]
            lines[index] = replacement + ending
        else:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += newline
            lines.append(replacement + newline)

    serialized = "".join(lines)
    if len(serialized.encode("utf-8")) > MAX_POLICY_BYTES:
        raise AccessError("updated config exceeds its size limit")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".config.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, existing_mode)
        os.replace(temporary_name, target)
    except OSError as exc:
        raise AccessError(f"config could not be updated: {target}") from exc
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def parse_mode_update(args: argparse.Namespace) -> dict[str, str | None] | None:
    action = args.mode_action or "show"
    value = args.mode_value
    updates: dict[str, str | None] = {}
    legacy_names = (
        "descendants", "max_descendants", "max_total_descendants", "repair_rounds",
    )
    if action in {"nesting", "max-descendants", "max-total-descendants", "repair-rounds"} or any(
        getattr(args, name, None) is not None for name in legacy_names
    ):
        raise AccessError(
            "legacy descendant and repair settings are unavailable in native Agent mode; fan-out stays at the root"
        )
    option_names = (
        "routing", "extra_usage", "max_agents", "openai_fast", "anthropic_fast",
        "swarm_fast", "failover",
    )
    options_used = any(getattr(args, name, None) for name in option_names)
    if action == "show":
        if value or options_used:
            raise AccessError("usage: airlock mode [show]")
        return None
    if action in VALID_ROUTING_POLICIES:
        if value or options_used:
            raise AccessError(f"usage: airlock mode {action}")
        return {MODE_CONFIG_KEYS["routing"]: action}
    if action == "budget":
        if value or options_used:
            raise AccessError("usage: airlock mode budget")
        return {
            MODE_CONFIG_KEYS["routing"]: "economy",
            MODE_CONFIG_KEYS["extra_usage"]: "never",
            MODE_CONFIG_KEYS["openai_fast"]: "off",
            MODE_CONFIG_KEYS["anthropic_fast"]: "off",
            MODE_CONFIG_KEYS["failover"]: "never",
        }
    if action == "defaults":
        if value or options_used:
            raise AccessError("usage: airlock mode defaults")
        return {
            MODE_CONFIG_KEYS["routing"]: "balanced",
            MODE_CONFIG_KEYS["extra_usage"]: "ask",
            MODE_CONFIG_KEYS["max_agents"]: DEFAULT_MAX_CONCURRENT_SUBAGENTS,
            MODE_CONFIG_KEYS["openai_fast"]: DEFAULT_OPENAI_FAST,
            MODE_CONFIG_KEYS["anthropic_fast"]: DEFAULT_ANTHROPIC_FAST,
            MODE_CONFIG_KEYS["swarm_fast"]: DEFAULT_SWARM_FAST,
            MODE_CONFIG_KEYS["failover"]: "ask",
            MODE_CONFIG_KEYS["agent_depth"]: DEFAULT_AGENT_DEPTH,
            MODE_CONFIG_KEYS["descendants"]: None,
            MODE_CONFIG_KEYS["max_descendants"]: None,
            MODE_CONFIG_KEYS["max_total_descendants"]: None,
            MODE_CONFIG_KEYS["repair_rounds"]: None,
        }
    if action == "fast":
        if value not in {"all", "openai", "anthropic", "off"} or options_used:
            raise AccessError("usage: airlock mode fast all|openai|anthropic|off")
        return {
            MODE_CONFIG_KEYS["openai_fast"]: "on" if value in {"all", "openai"} else "off",
            MODE_CONFIG_KEYS["anthropic_fast"]: "on" if value in {"all", "anthropic"} else "off",
        }
    simple_actions: dict[str, tuple[str, object, str]] = {
        "routing": ("routing", VALID_ROUTING_POLICIES, "balanced|economy|quality"),
        "extra-usage": ("extra_usage", VALID_EXTRA_POLICIES, "ask|never|allow"),
        "openai-fast": ("openai_fast", VALID_PROVIDER_FAST_POLICIES, "on|off"),
        "anthropic-fast": ("anthropic_fast", VALID_PROVIDER_FAST_POLICIES, "on|off"),
        "swarm-fast": ("swarm_fast", VALID_SWARM_FAST_POLICIES, "auto|on|off"),
        "failover": ("failover", VALID_FAILOVER_POLICIES, "ask|never|allow"),
        "depth": ("agent_depth", VALID_AGENT_DEPTHS, "1|2"),
    }
    if action in simple_actions:
        name, validator, expected = simple_actions[action]
        if value not in validator or options_used:
            raise AccessError(f"usage: airlock mode {action} {expected}")
        return {MODE_CONFIG_KEYS[name]: value}
    numeric_actions: dict[str, tuple[str, object, str]] = {
        "max-agents": ("max_agents", _valid_max_agents, "off|1..20"),
    }
    if action in numeric_actions:
        name, validator, expected = numeric_actions[action]
        if not validator(value) or options_used:
            raise AccessError(f"usage: airlock mode {action} {expected}")
        return {MODE_CONFIG_KEYS[name]: value}
    if action == "set":
        if value:
            raise AccessError(
                "usage: airlock mode set [--routing VALUE] [--extra-usage VALUE] [--max-agents VALUE] [--openai-fast VALUE] [--anthropic-fast VALUE] [--swarm-fast VALUE] [--failover VALUE]"
            )
        validators: dict[str, object] = {
            "routing": VALID_ROUTING_POLICIES,
            "extra_usage": VALID_EXTRA_POLICIES,
            "max_agents": _valid_max_agents,
            "openai_fast": VALID_PROVIDER_FAST_POLICIES,
            "anthropic_fast": VALID_PROVIDER_FAST_POLICIES,
            "swarm_fast": VALID_SWARM_FAST_POLICIES,
            "failover": VALID_FAILOVER_POLICIES,
        }
        for name, validator in validators.items():
            candidate = getattr(args, name, None)
            if candidate is None:
                continue
            valid = validator(candidate) if callable(validator) else candidate in validator
            if not valid:
                raise AccessError(f"invalid --{name.replace('_', '-')} value: {candidate}")
            updates[MODE_CONFIG_KEYS[name]] = candidate
        if not updates:
            raise AccessError("mode set requires at least one option")
        return updates
    raise AccessError(
        "usage: airlock mode [show|budget|defaults|balanced|economy|quality|routing|extra-usage|fast|openai-fast|anthropic-fast|swarm-fast|failover|max-agents|set]"
    )


def _clean_plan(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    if not cleaned or len(cleaned) > 80 or not re.fullmatch(r"[a-z0-9][a-z0-9 ._+/-]*", cleaned):
        return None
    return cleaned


def _normalize_account_metadata(provider: str, raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        return {}
    allowed = {
        "anthropic": {
            "subscription_type", "billing_type", "extra_usage_enabled",
            "organization_rate_limit_tier", "user_rate_limit_tier", "seat_tier",
        },
        "openai": {"account_type"},
        "grok": set(),
    }[provider]
    result: dict[str, object] = {}
    for key in allowed:
        value = raw.get(key)
        if key == "extra_usage_enabled":
            if isinstance(value, bool):
                result[key] = value
            continue
        if value is None:
            continue
        cleaned = _clean_plan(value)
        if cleaned:
            result[key] = cleaned
    return result


def _normalize_usage_bucket(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None
    limit_id = raw.get("limit_id")
    window = raw.get("window")
    used = raw.get("used_percent")
    duration = raw.get("window_duration_mins")
    resets_at = raw.get("resets_at")
    if not isinstance(limit_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", limit_id):
        return None
    if not isinstance(window, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,40}", window):
        return None
    if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 <= float(used) <= 100:
        return None
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 < float(duration) <= 525_600:
        return None
    if isinstance(resets_at, bool) or not isinstance(resets_at, (int, float, str)):
        return None
    if isinstance(resets_at, (int, float)) and not 0 <= float(resets_at) <= 10**14:
        return None
    if isinstance(resets_at, str):
        if len(resets_at) > 80 or not re.fullmatch(r"[0-9T:+.Z-]+", resets_at):
            return None
    used_percent = round(float(used), 2)
    return {
        "limit_id": limit_id,
        "window": window,
        "used_percent": used_percent,
        "remaining_percent": round(100.0 - used_percent, 2),
        "window_duration_mins": round(float(duration), 2),
        "resets_at": resets_at,
    }


def _parse_checked_at(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _normalize_usage(provider: str, raw: object) -> dict[str, object]:
    result = _default_usage(provider)
    if not isinstance(raw, dict):
        return result
    source = raw.get("source")
    valid_sources = {"not_checked", "codex_app_server", "refresh_failed", "not_exposed"}
    if source in valid_sources:
        result["source"] = source
    checked = _parse_checked_at(raw.get("checked_at"))
    if checked:
        result["checked_at"] = checked.isoformat().replace("+00:00", "Z")
    buckets: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    raw_buckets = raw.get("buckets")
    if isinstance(raw_buckets, list):
        for item in raw_buckets:
            bucket = _normalize_usage_bucket(item)
            if not bucket:
                continue
            key = (str(bucket["limit_id"]), str(bucket["window"]))
            if key in seen:
                continue
            seen.add(key)
            buckets.append(bucket)
    result["buckets"] = buckets
    credit_state = raw.get("credit_state")
    if credit_state in {"available", "unavailable", "unlimited", "unknown", "not_exposed"}:
        result["credit_state"] = credit_state
    return result


def _normalize_model_map(provider: str, raw: object) -> dict[str, dict[str, str]]:
    defaults = default_policy()["providers"][provider]["models"]
    result: dict[str, dict[str, str]] = {}
    source = raw if isinstance(raw, dict) else {}
    for route in PROVIDER_ROUTES[provider]:
        entry = source.get(route)
        access = entry.get("access") if isinstance(entry, dict) else None
        if access not in VALID_ACCESS:
            access = defaults[route]["access"]
        result[route] = {"access": access}
    return result


def load_cached_policy(path: Path | None = None) -> dict[str, Any]:
    target = path or access_path()
    policy = default_policy()
    if target.is_file() and not target.is_symlink() and target.stat().st_size <= MAX_POLICY_BYTES:
        try:
            raw = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raw = None
        if isinstance(raw, dict) and raw.get("schema_version") in READABLE_SCHEMA_VERSIONS:
            raw_policies = raw.get("policies")
            if isinstance(raw_policies, dict):
                extra = raw_policies.get("extra_usage")
                routing = raw_policies.get("routing")
                swarm_fast = raw_policies.get("swarm_fast")
                openai_fast = raw_policies.get("openai_fast")
                anthropic_fast = raw_policies.get("anthropic_fast")
                if extra in VALID_EXTRA_POLICIES:
                    policy["policies"]["extra_usage"] = extra
                if routing in VALID_ROUTING_POLICIES:
                    policy["policies"]["routing"] = routing
                if openai_fast in VALID_PROVIDER_FAST_POLICIES:
                    policy["policies"]["openai_fast"] = openai_fast
                if anthropic_fast in VALID_PROVIDER_FAST_POLICIES:
                    policy["policies"]["anthropic_fast"] = anthropic_fast
                if swarm_fast in VALID_SWARM_FAST_POLICIES:
                    policy["policies"]["swarm_fast"] = swarm_fast
                raw_efforts = raw_policies.get("allowed_efforts")
                if isinstance(raw_efforts, dict):
                    for provider in PROVIDER_ROUTES:
                        efforts = raw_efforts.get(provider)
                        if isinstance(efforts, list) and efforts and all(item in VALID_EFFORTS for item in efforts):
                            policy["policies"]["allowed_efforts"][provider] = list(dict.fromkeys(efforts))
            raw_providers = raw.get("providers")
            if isinstance(raw_providers, dict):
                for provider in PROVIDER_ROUTES:
                    source = raw_providers.get(provider)
                    if not isinstance(source, dict):
                        continue
                    authenticated = source.get("authenticated")
                    if authenticated in (True, False, None):
                        policy["providers"][provider]["authenticated"] = authenticated
                    plan = _clean_plan(source.get("detected_plan")) or "unknown"
                    policy["providers"][provider]["detected_plan"] = plan
                    plan_source = source.get("plan_source")
                    if plan_source in {
                        "status_field", "codex_app_server", "local_metadata",
                        "not_exposed", "not_checked", "status_failed",
                    }:
                        policy["providers"][provider]["plan_source"] = plan_source
                    policy["providers"][provider]["account_metadata"] = _normalize_account_metadata(
                        provider, source.get("account_metadata")
                    )
                    policy["providers"][provider]["usage"] = _normalize_usage(
                        provider, source.get("usage")
                    )
                    policy["providers"][provider]["models"] = _normalize_model_map(provider, source.get("models"))

    policy["schema_version"] = SCHEMA_VERSION
    return policy


def _csv(value: str | None, valid: tuple[str, ...]) -> list[str] | None:
    if value is None:
        return None
    parsed = [item.strip().lower() for item in value.split(",") if item.strip()]
    if any(item not in valid for item in parsed):
        return None
    return list(dict.fromkeys(parsed))


def apply_config_overrides(policy: dict[str, Any], config: dict[str, str]) -> dict[str, Any]:
    extra = config.get("AIRLOCK_EXTRA_USAGE_POLICY")
    routing = config.get("AIRLOCK_ROUTING_POLICY")
    openai_fast = config.get("AIRLOCK_OPENAI_FAST")
    anthropic_fast = config.get("AIRLOCK_ANTHROPIC_FAST")
    swarm_fast = config.get("AIRLOCK_SWARM_FAST")
    failover = config.get("AIRLOCK_FAILOVER_POLICY")
    descendants = config.get("AIRLOCK_DESCENDANT_POLICY")
    max_descendants = config.get("AIRLOCK_MAX_DESCENDANTS_PER_WORKER")
    max_total_descendants = config.get("AIRLOCK_MAX_CONCURRENT_DESCENDANTS")
    repair_rounds = config.get("AIRLOCK_MAX_REPAIR_ROUNDS")
    agent_depth = config.get("AIRLOCK_AGENT_DEPTH")
    if extra in VALID_EXTRA_POLICIES:
        policy["policies"]["extra_usage"] = extra
    if agent_depth in VALID_AGENT_DEPTHS:
        policy["policies"]["agent_depth"] = agent_depth
    if routing in VALID_ROUTING_POLICIES:
        policy["policies"]["routing"] = routing
    if openai_fast in VALID_PROVIDER_FAST_POLICIES:
        policy["policies"]["openai_fast"] = openai_fast
    if anthropic_fast in VALID_PROVIDER_FAST_POLICIES:
        policy["policies"]["anthropic_fast"] = anthropic_fast
    if swarm_fast in VALID_SWARM_FAST_POLICIES:
        policy["policies"]["swarm_fast"] = swarm_fast
    if failover in VALID_FAILOVER_POLICIES:
        policy["policies"]["failover"] = failover
    if descendants in VALID_DESCENDANT_POLICIES:
        policy["policies"]["descendants"] = descendants
    if _valid_positive_limit(max_descendants):
        policy["policies"]["max_descendants"] = int(max_descendants)
    if _valid_positive_limit(max_total_descendants):
        policy["policies"]["max_concurrent_descendants"] = int(max_total_descendants)
    if _valid_repair_rounds(repair_rounds):
        policy["policies"]["repair_rounds"] = int(repair_rounds)

    # AIRLOCK_WORKER_EFFORT sets every worker at once; AIRLOCK_EFFORT_<ROUTE>
    # pins one worker and wins over it. Both accept "inherit" to follow /effort.
    shared_effort = config.get("AIRLOCK_WORKER_EFFORT")
    if isinstance(shared_effort, str) and shared_effort.strip().lower() in VALID_WORKER_EFFORTS:
        for route in policy["policies"]["worker_effort"]:
            policy["policies"]["worker_effort"][route] = shared_effort.strip().lower()
    for routes in PROVIDER_ROUTES.values():
        for route in routes:
            key = f"AIRLOCK_EFFORT_{route.replace('-', '_').upper()}"
            value = config.get(key)
            if isinstance(value, str) and value.strip().lower() in VALID_WORKER_EFFORTS:
                policy["policies"]["worker_effort"][route] = value.strip().lower()

    for provider, prefix in (
        ("anthropic", "ANTHROPIC"),
        ("openai", "OPENAI"),
        ("grok", "GROK"),
    ):
        routes = PROVIDER_ROUTES[provider]
        enabled = _csv(config.get(f"AIRLOCK_{prefix}_MODELS"), routes)
        included = _csv(config.get(f"AIRLOCK_{prefix}_INCLUDED_MODELS"), routes) or []
        extra_routes = _csv(config.get(f"AIRLOCK_{prefix}_EXTRA_MODELS"), routes) or []
        unavailable = _csv(config.get(f"AIRLOCK_{prefix}_UNAVAILABLE_MODELS"), routes) or []
        efforts = _csv(config.get(f"AIRLOCK_{prefix}_DELEGATE_EFFORTS"), VALID_EFFORTS)
        if efforts:
            policy["policies"]["allowed_efforts"][provider] = efforts
        for route in routes:
            state = policy["providers"][provider]["models"][route]
            if enabled is not None and route not in enabled:
                state["access"] = "unavailable"
                continue
            if route in unavailable:
                state["access"] = "unavailable"
            elif route in extra_routes:
                state["access"] = "extra"
            elif route in included:
                state["access"] = "included"
            elif enabled is not None and state["access"] == "unavailable":
                state["access"] = "unknown"
    return policy


def apply_runtime_overrides(policy: dict[str, Any]) -> dict[str, Any]:
    defaults = default_policy()["policies"]
    for name in (
        "extra_usage", "routing", "openai_fast", "anthropic_fast", "swarm_fast",
        "failover", "descendants", "max_descendants", "max_concurrent_descendants",
        "repair_rounds",
    ):
        policy["policies"][name] = defaults[name]
    # Worker effort is a live preference rather than cached account state, so it
    # is rebuilt from the defaults on every load and never read back from disk.
    policy["policies"]["worker_effort"] = dict(defaults["worker_effort"])
    apply_config_overrides(policy, read_flat_config())
    apply_config_overrides(policy, dict(os.environ))
    # A confirmed logged-out Grok proxy wins over any enable the config asked
    # for. Advertising a worker whose provider has no login only produces a
    # failed request one Agent call later.
    if policy["providers"]["grok"].get("authenticated") is False:
        for state in policy["providers"]["grok"]["models"].values():
            state["access"] = "unavailable"
    return policy


def load_policy(path: Path | None = None) -> dict[str, Any]:
    policy = apply_runtime_overrides(load_cached_policy(path))
    policy["_openrouter_registry"] = load_openrouter_registry()
    return policy


def _run_status(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def read_claude_account_metadata(path: Path | None = None) -> dict[str, object]:
    target = path or Path(os.environ.get("AIRLOCK_CLAUDE_STATE_FILE", Path.home() / ".claude.json"))
    try:
        if not target.is_file() or target.is_symlink() or target.stat().st_size > MAX_CLAUDE_STATE_BYTES:
            return {}
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    account = payload.get("oauthAccount") if isinstance(payload, dict) else None
    if not isinstance(account, dict):
        return {}
    raw = {
        "subscription_type": account.get("subscriptionType"),
        "billing_type": account.get("billingType"),
        "extra_usage_enabled": account.get("hasExtraUsageEnabled"),
        "organization_rate_limit_tier": account.get("organizationRateLimitTier"),
        "user_rate_limit_tier": account.get("userRateLimitTier"),
        "seat_tier": account.get("seatTier"),
    }
    return _normalize_account_metadata("anthropic", raw)


def _wait_for_app_server_response(lines: queue.Queue[str], identifier: int) -> dict[str, object] | None:
    deadline = time.monotonic() + APP_SERVER_TIMEOUT_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            return None
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("id") == identifier:
            return payload


def _codex_app_server_call(
    executable: str, method: str, params: dict[str, object] | None = None
) -> dict[str, object] | None:
    try:
        process = subprocess.Popen(
            [executable, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        return None
    lines: queue.Queue[str] = queue.Queue()

    def read_stdout() -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            lines.put(line)

    threading.Thread(target=read_stdout, daemon=True).start()

    def send(payload: dict[str, object]) -> bool:
        if process.stdin is None:
            return False
        try:
            process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            return False
        return True

    try:
        if not send({
            "method": "initialize", "id": 0,
            "params": {"clientInfo": {
                "name": "airlock-usage", "title": "Airlock usage", "version": "0.1.0-beta.6",
            }},
        }):
            return None
        initialized = _wait_for_app_server_response(lines, 0)
        if not initialized or "error" in initialized:
            return None
        if not send({"method": "initialized", "params": {}}):
            return None
        if not send({"method": method, "id": 1, "params": params or {}}):
            return None
        response = _wait_for_app_server_response(lines, 1)
        if not isinstance(response, dict) or "error" in response:
            return None
        result = response.get("result")
        return result if isinstance(result, dict) else None
    finally:
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        try:
            if process.poll() is None:
                process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        for stream in (process.stdout,):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass


def read_codex_account(executable: str) -> dict[str, str] | None:
    result = _codex_app_server_call(
        executable, "account/read", {"refreshToken": False}
    )
    account = result.get("account") if isinstance(result, dict) else None
    if not isinstance(account, dict):
        return None
    account_type = _clean_plan(account.get("type"))
    plan = _clean_plan(account.get("planType"))
    if not account_type:
        return None
    output = {"account_type": account_type}
    if plan:
        output["plan"] = plan
    return output


def _window_bucket(limit_id: str, window: str, raw: object) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None
    return _normalize_usage_bucket({
        "limit_id": limit_id,
        "window": window,
        "used_percent": raw.get("usedPercent"),
        "window_duration_mins": raw.get("windowDurationMins"),
        "resets_at": raw.get("resetsAt"),
    })


def _extract_rate_limit_buckets(result: dict[str, object]) -> list[dict[str, object]]:
    buckets: list[dict[str, object]] = []
    grouped = result.get("rateLimitsByLimitId")
    if isinstance(grouped, dict):
        sources = [(str(limit_id), entry) for limit_id, entry in sorted(grouped.items())]
    else:
        legacy = result.get("rateLimits")
        if isinstance(legacy, dict) and any(
            key in legacy for key in ("primary", "secondary", "primaryWindow", "secondaryWindow")
        ):
            limit_id = legacy.get("limitId")
            sources = [(str(limit_id) if isinstance(limit_id, str) else "codex", legacy)]
        elif isinstance(legacy, list):
            sources = []
            for index, entry in enumerate(legacy):
                limit_id = entry.get("limitId") if isinstance(entry, dict) else None
                sources.append((str(limit_id) if isinstance(limit_id, str) else f"limit-{index + 1}", entry))
        else:
            sources = []
    for limit_id, entry in sources:
        if not isinstance(entry, dict):
            continue
        for canonical, aliases in (
            ("primary", ("primary", "primaryWindow")),
            ("secondary", ("secondary", "secondaryWindow")),
        ):
            raw_window = next((entry.get(alias) for alias in aliases if isinstance(entry.get(alias), dict)), None)
            bucket = _window_bucket(limit_id, canonical, raw_window)
            if bucket:
                buckets.append(bucket)
    normalized = _normalize_usage("openai", {"buckets": buckets})
    return normalized["buckets"]  # type: ignore[return-value]


def _credit_state(result: dict[str, object]) -> str:
    credits = result.get("credits")
    rate_limits = result.get("rateLimits")
    if not isinstance(credits, dict) and isinstance(rate_limits, dict):
        credits = rate_limits.get("credits")
    if not isinstance(credits, dict):
        return "unknown"
    if credits.get("unlimited") is True:
        return "unlimited"
    if credits.get("hasCredits") is True:
        return "available"
    if credits.get("hasCredits") is False:
        return "unavailable"
    return "unknown"


def read_codex_usage(executable: str) -> tuple[dict[str, object], str | None] | None:
    result = _codex_app_server_call(executable, "account/rateLimits/read")
    if not isinstance(result, dict):
        return None
    rate_limits = result.get("rateLimits")
    plan = _clean_plan(result.get("planType"))
    if not plan and isinstance(rate_limits, dict):
        plan = _clean_plan(rate_limits.get("planType"))
    usage = {
        "source": "codex_app_server",
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "buckets": _extract_rate_limit_buckets(result),
        "credit_state": _credit_state(result),
    }
    return _normalize_usage("openai", usage), plan


def detect_anthropic() -> dict[str, object]:
    metadata = read_claude_account_metadata()
    local_plan = _clean_plan(metadata.get("subscription_type"))
    executable = os.environ.get("AIRLOCK_ACCESS_CLAUDE") or shutil.which("claude.exe") or shutil.which("claude")
    if not executable:
        return {
            "authenticated": False,
            "detected_plan": local_plan or "unknown",
            "plan_source": "local_metadata" if local_plan else "status_failed",
            "account_metadata": metadata,
        }
    completed = _run_status([executable, "auth", "status", "--json"])
    if completed is None or completed.returncode != 0:
        return {
            "authenticated": False,
            "detected_plan": local_plan or "unknown",
            "plan_source": "local_metadata" if local_plan else "status_failed",
            "account_metadata": metadata,
        }
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict):
        return {
            "authenticated": False,
            "detected_plan": local_plan or "unknown",
            "plan_source": "local_metadata" if local_plan else "status_failed",
            "account_metadata": metadata,
        }
    plan = next((_clean_plan(payload.get(key)) for key in PLAN_KEYS if _clean_plan(payload.get(key))), None)
    detected_plan = plan or local_plan or "unknown"
    plan_source = "status_field" if plan else ("local_metadata" if local_plan else "not_exposed")
    return {
        "authenticated": payload.get("loggedIn") is True,
        "detected_plan": detected_plan,
        "plan_source": plan_source,
        "account_metadata": metadata,
    }


def detect_openai() -> dict[str, object]:
    codex = (
        os.environ.get("AIRLOCK_ACCESS_CODEX")
        or shutil.which("codex.exe") or shutil.which("codex.cmd") or shutil.which("codex")
    )
    if codex:
        account = read_codex_account(codex)
        if account:
            plan = account.get("plan") or "unknown"
            return {
                "authenticated": True,
                "detected_plan": plan,
                "plan_source": "codex_app_server" if plan != "unknown" else "not_exposed",
                "account_metadata": {"account_type": account["account_type"]},
            }

    executable = os.environ.get("AIRLOCK_ACCESS_PROXY") or shutil.which("claude-code-proxy.exe") or shutil.which("claude-code-proxy")
    if not executable:
        return {
            "authenticated": False, "detected_plan": "unknown",
            "plan_source": "status_failed", "account_metadata": {},
        }
    completed = _run_status([executable, "codex", "auth", "status"])
    if completed is None:
        return {
            "authenticated": False, "detected_plan": "unknown",
            "plan_source": "status_failed", "account_metadata": {},
        }
    output = completed.stdout
    negative = bool(re.search(r"\b(not authenticated|not logged in|logged out)\b", output, re.IGNORECASE))
    plan = None
    for line in output.splitlines():
        match = re.fullmatch(r"\s*(?:plan|tier|subscription(?: plan| tier)?)\s*:\s*(.+?)\s*", line, re.IGNORECASE)
        if match:
            plan = _clean_plan(match.group(1))
            if plan:
                break
    return {
        "authenticated": completed.returncode == 0 and not negative,
        "detected_plan": plan or "unknown",
        "plan_source": "status_field" if plan else ("not_exposed" if completed.returncode == 0 else "status_failed"),
        "account_metadata": {},
    }


def _write_policy(policy: dict[str, Any], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise AccessError(f"refusing to replace symlink: {target}")
    if target.exists() and not target.is_file():
        raise AccessError(f"access target is not a regular file: {target}")
    policy["schema_version"] = SCHEMA_VERSION
    serialized = json.dumps(policy, indent=2, ensure_ascii=True) + "\n"
    if len(serialized.encode("utf-8")) > MAX_POLICY_BYTES:
        raise AccessError("access policy exceeds its size limit")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".access.", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, target)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def refresh_policy(path: Path | None = None) -> dict[str, Any]:
    target = path or access_path()
    policy = load_cached_policy(target)
    policy["providers"]["anthropic"].update(detect_anthropic())
    policy["providers"]["openai"].update(detect_openai())
    _write_policy(policy, target)
    policy = apply_runtime_overrides(policy)
    policy["_openrouter_registry"] = load_openrouter_registry()
    return policy


def load_or_refresh_policy(path: Path | None = None) -> dict[str, Any]:
    """Compatibility name: loading is deliberately cache-only."""
    return load_policy(path or access_path())


def usage_preferences(config: dict[str, str] | None = None) -> dict[str, str]:
    values = dict(config if config is not None else read_flat_config())
    claude_plan = values.get(USAGE_CONFIG_KEYS["claude_plan"], "unknown")
    openai_capacity = values.get(USAGE_CONFIG_KEYS["openai_capacity"], "auto")
    if claude_plan not in VALID_CLAUDE_PLANS:
        claude_plan = "unknown"
    if openai_capacity not in VALID_OPENAI_CAPACITIES:
        openai_capacity = "auto"
    env_claude = os.environ.get(USAGE_CONFIG_KEYS["claude_plan"])
    env_openai = os.environ.get(USAGE_CONFIG_KEYS["openai_capacity"])
    if env_claude in VALID_CLAUDE_PLANS:
        claude_plan = env_claude
    if env_openai in VALID_OPENAI_CAPACITIES:
        openai_capacity = env_openai
    return {"claude_plan": claude_plan, "openai_capacity": openai_capacity}


def capacity_signal(policy: dict[str, Any], provider: str) -> dict[str, object]:
    preferences = usage_preferences()
    if provider == "anthropic":
        plan = preferences["claude_plan"]
        if plan != "unknown":
            return {
                "plan": plan,
                "multiplier": {"pro": 1, "max5x": 5, "max20x": 20}[plan],
                "source": "user_override",
                "confidence": "explicit",
            }
        detected = str(policy["providers"]["anthropic"]["detected_plan"]).lower()
        if detected in {"pro", "max5x", "max20x"}:
            return {
                "plan": detected,
                "multiplier": {"pro": 1, "max5x": 5, "max20x": 20}[detected],
                "source": policy["providers"]["anthropic"]["plan_source"],
                "confidence": "detected",
            }
        return {
            "plan": "unknown",
            "multiplier": None,
            "source": "unknown",
            "confidence": "unknown",
        }
    override = preferences["openai_capacity"]
    if override != "auto":
        return {
            "plan": policy["providers"]["openai"]["detected_plan"],
            "multiplier": int(override[:-1]),
            "source": "user_override",
            "confidence": "explicit",
        }
    detected = str(policy["providers"]["openai"]["detected_plan"]).lower()
    inferred = OPENAI_PLAN_CAPACITY.get(detected)
    if inferred:
        multiplier, confidence = inferred
        return {
            "plan": detected,
            "multiplier": multiplier,
            "source": "inferred",
            "confidence": confidence,
            "mapping_version": OPENAI_PLAN_MAP_VERSION,
        }
    return {
        "plan": detected,
        "multiplier": None,
        "source": "unknown",
        "confidence": "unknown",
    }


def usage_age_seconds(usage: object, now: datetime | None = None) -> int | None:
    if not isinstance(usage, dict):
        return None
    checked = _parse_checked_at(usage.get("checked_at"))
    if not checked:
        return None
    reference = now or datetime.now(timezone.utc)
    return max(0, int((reference - checked).total_seconds()))


def usage_refresh_due(policy: dict[str, Any], now: datetime | None = None) -> bool:
    usage = policy.get("providers", {}).get("openai", {}).get("usage")
    if not isinstance(usage, dict) or usage.get("source") != "codex_app_server":
        return True
    age = usage_age_seconds(usage, now=now)
    return age is None or age > USAGE_FRESH_SECONDS


def _age_label(age: int | None) -> str:
    if age is None:
        return "never"
    if age < 60:
        return f"{age}s"
    if age < 3600:
        return f"{age // 60}m"
    if age < 86_400:
        return f"{age // 3600}h"
    return f"{age // 86_400}d"


def refresh_usage(path: Path | None = None) -> tuple[dict[str, Any], bool]:
    target = path or access_path()
    policy = load_cached_policy(target)
    metadata = read_claude_account_metadata()
    if metadata:
        policy["providers"]["anthropic"]["account_metadata"] = metadata
        local_plan = _clean_plan(metadata.get("subscription_type"))
        if local_plan:
            policy["providers"]["anthropic"]["detected_plan"] = local_plan
            policy["providers"]["anthropic"]["plan_source"] = "local_metadata"
    codex = (
        os.environ.get("AIRLOCK_ACCESS_CODEX")
        or shutil.which("codex.exe") or shutil.which("codex.cmd") or shutil.which("codex")
    )
    refreshed = False
    if codex:
        snapshot = read_codex_usage(codex)
        if snapshot:
            usage, plan = snapshot
            policy["providers"]["openai"]["usage"] = usage
            policy["providers"]["openai"]["authenticated"] = True
            if plan:
                policy["providers"]["openai"]["detected_plan"] = plan
                policy["providers"]["openai"]["plan_source"] = "codex_app_server"
            refreshed = True
    # Grok login state is account state, not usage. It is tracked separately so
    # that probing it never counts as a usage refresh, which would otherwise
    # restart the shared fifteen-minute window and report a refresh that did
    # not happen.
    grok = grok_auth_status()
    grok_changed = False
    if grok["source"] != "not_found":
        grok_state = policy["providers"]["grok"]
        if (
            grok_state.get("authenticated") != grok["authenticated"]
            or grok_state.get("plan_source") != grok["source"]
        ):
            grok_state["authenticated"] = grok["authenticated"]
            grok_state["plan_source"] = str(grok["source"])
            grok_changed = True
    if refreshed or metadata or grok_changed:
        _write_policy(policy, target)
    return apply_runtime_overrides(policy), refreshed


def usage_policy_for_display(
    action: str | None, path: Path | None = None,
) -> tuple[dict[str, Any], bool, bool]:
    policy = load_policy(path or access_path())
    should_refresh = action == "refresh" or (
        action in {None, "show"} and usage_refresh_due(policy)
    )
    if not should_refresh:
        return policy, False, False
    refreshed_policy, refreshed = refresh_usage(path)
    return refreshed_policy, True, refreshed


def parse_usage_update(args: argparse.Namespace) -> dict[str, str | None] | None:
    action = args.usage_action or "show"
    if action in {"show", "refresh"}:
        if args.claude_plan or args.openai_capacity:
            raise AccessError(f"usage: airlock usage {action if action != 'show' else ''}".rstrip())
        return None
    if action == "defaults":
        if args.claude_plan or args.openai_capacity:
            raise AccessError("usage: airlock usage defaults")
        return {
            USAGE_CONFIG_KEYS["claude_plan"]: None,
            USAGE_CONFIG_KEYS["openai_capacity"]: None,
        }
    if action == "set":
        updates: dict[str, str | None] = {}
        if args.claude_plan:
            updates[USAGE_CONFIG_KEYS["claude_plan"]] = args.claude_plan
        if args.openai_capacity:
            updates[USAGE_CONFIG_KEYS["openai_capacity"]] = args.openai_capacity
        if not updates:
            raise AccessError("usage set requires --claude-plan and/or --openai-capacity")
        return updates
    raise AccessError("usage: airlock usage [show|refresh|set|defaults]")


def _reset_label(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if seconds > 10**12:
            seconds /= 1000
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            return "unknown"
    return str(value)


def usage_lines(policy: dict[str, Any], refresh_failed: bool = False) -> list[str]:
    preferences = usage_preferences()
    openai = policy["providers"]["openai"]
    usage = openai.get("usage", _default_usage("openai"))
    age = usage_age_seconds(usage)
    capacity = capacity_signal(policy, "openai")
    lines = ["Airlock subscription usage (sanitized)"]
    if refresh_failed:
        lines.append("Refresh: failed; showing the last cached snapshot.")
    capacity_label = f"{capacity['multiplier']}x" if capacity["multiplier"] else "unknown"
    lines.append(
        f"OpenAI/Codex: plan={openai['detected_plan']} (source={openai['plan_source']}); "
        f"capacity={capacity_label} (source={capacity['source']}, confidence={capacity['confidence']})"
    )
    lines.append(
        f"  Usage source={usage.get('source', 'not_checked')}; cache age={_age_label(age)}; "
        f"credits={usage.get('credit_state', 'unknown')}"
    )
    buckets = usage.get("buckets") if isinstance(usage, dict) else None
    if isinstance(buckets, list) and buckets:
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            lines.append(
                f"  {bucket['limit_id']}/{bucket['window']}: remaining={bucket['remaining_percent']:.2f}%; "
                f"used={bucket['used_percent']:.2f}%; window={bucket['window_duration_mins']:g}m; "
                f"resets={_reset_label(bucket['resets_at'])}"
            )
    else:
        lines.append("  Remaining quota windows: unknown (run `airlock usage refresh`).")
    anthropic = policy["providers"]["anthropic"]
    claude_capacity = capacity_signal(policy, "anthropic")
    detected = anthropic["detected_plan"]
    configured = preferences["claude_plan"]
    lines.append(
        f"Anthropic/Claude: detected-plan={detected} (source={anthropic['plan_source']}); "
        f"configured-tier={configured}; effective-tier={claude_capacity['plan']} (source={claude_capacity['source']})"
    )
    metadata = anthropic.get("account_metadata", {})
    signals = []
    if isinstance(metadata, dict):
        if metadata.get("billing_type"):
            signals.append(f"billing={metadata['billing_type']}")
        if isinstance(metadata.get("extra_usage_enabled"), bool):
            signals.append(f"extra-usage={'enabled' if metadata['extra_usage_enabled'] else 'disabled'}")
        if metadata.get("organization_rate_limit_tier"):
            signals.append(f"rate-tier={metadata['organization_rate_limit_tier']}")
    if signals:
        lines.append(f"  Sanitized signals: {', '.join(signals)}")
    lines.append("  Remaining subscription allocation: unknown; use native Claude `/usage` for Anthropic's interactive view.")
    lines.append(f"OpenAI prolite->5x and pro->20x are Airlock inference map {OPENAI_PLAN_MAP_VERSION}, not vendor guarantees; explicit overrides win.")
    lines.append("Extra-use credits are reported separately and never counted as subscription capacity.")
    return lines


def _allowed_automatic_effort(allowed: list[str], desired: str) -> str:
    desired_index = VALID_EFFORTS.index(desired)
    candidates = [item for item in allowed if VALID_EFFORTS.index(item) <= desired_index]
    if candidates:
        return max(candidates, key=VALID_EFFORTS.index)
    return min(allowed, key=VALID_EFFORTS.index)


def recommend_effort(
    policy: dict[str, Any], provider: str, risk: str,
    explicit_effort: str | None = None,
) -> dict[str, object]:
    if provider not in PROVIDER_ROUTES:
        raise AccessError(f"unknown provider: {provider}")
    if risk not in VALID_RISKS:
        raise AccessError("risk must be low, standard, high, or critical")
    allowed = policy["policies"]["allowed_efforts"][provider]
    usage = policy["providers"][provider].get("usage", _default_usage(provider))
    age = usage_age_seconds(usage)
    source = usage.get("source", "not_checked") if isinstance(usage, dict) else "not_checked"
    capacity = capacity_signal(policy, provider)
    if explicit_effort is not None:
        if explicit_effort not in VALID_EFFORTS:
            raise AccessError("effort must be low, medium, high, xhigh, or max")
        if explicit_effort not in allowed:
            raise AccessError(f"effort {explicit_effort} is disabled for {provider} workers")
        return {
            "effort": explicit_effort,
            "effort_source": "explicit",
            "risk": risk,
            "usage_source": source,
            "usage_age_seconds": age,
            "capacity_multiplier": capacity["multiplier"],
            "capacity_source": capacity["source"],
            "capacity_mapping_version": capacity.get("mapping_version"),
            "rationale": "Explicit delegated effort was honored without adjustment.",
        }
    routing = policy["policies"]["routing"]
    desired = EFFORT_BASELINES[routing][risk]
    rationale = [f"{routing} baseline for {risk} risk is {desired}"]
    buckets = usage.get("buckets") if isinstance(usage, dict) else None
    remaining = None
    if age is not None and age <= USAGE_FRESH_SECONDS and isinstance(buckets, list) and buckets:
        values = [item.get("remaining_percent") for item in buckets if isinstance(item, dict)]
        numeric = [float(item) for item in values if isinstance(item, (int, float)) and not isinstance(item, bool)]
        if numeric:
            remaining = min(numeric)
    if remaining is not None:
        multiplier = capacity.get("multiplier")
        if multiplier == 20:
            thresholds = (30, 15, 5)
        elif multiplier == 5:
            thresholds = (40, 20, 8)
        else:
            thresholds = (50, 25, 10)
        high_threshold, medium_threshold, low_threshold = thresholds
        cap = None
        if remaining < low_threshold:
            cap = "low"
        elif remaining < medium_threshold:
            cap = "medium"
        elif remaining < high_threshold:
            cap = "high"
        if cap and VALID_EFFORTS.index(desired) > VALID_EFFORTS.index(cap):
            desired = cap
            rationale.append(f"fresh constrained window has {remaining:.2f}% remaining, capping effort at {cap}")
        else:
            rationale.append(f"fresh constrained window has {remaining:.2f}% remaining")
    else:
        rationale.append("no fresh remaining-quota value was available")
    floor = {"high": "medium", "critical": "high"}.get(risk)
    if floor and VALID_EFFORTS.index(desired) < VALID_EFFORTS.index(floor):
        desired = floor
        rationale.append(f"{risk}-risk floor raised effort to {floor}")
    selected = _allowed_automatic_effort(allowed, desired)
    if selected != desired:
        rationale.append(f"allowed-effort policy selected {selected}")
    return {
        "effort": selected,
        "effort_source": "recommended",
        "risk": risk,
        "usage_source": source,
        "usage_age_seconds": age,
        "capacity_multiplier": capacity["multiplier"],
        "capacity_source": capacity["source"],
        "capacity_mapping_version": capacity.get("mapping_version"),
        "rationale": "; ".join(rationale) + ".",
    }


def proxy_fast_capability() -> dict[str, object]:
    override = os.environ.get("AIRLOCK_PROXY_FAST_CAPABLE", "").strip().lower()
    if override in {"1", "true", "yes"}:
        return {"supported": True, "source": "environment", "version": None}
    if override in {"0", "false", "no"}:
        return {"supported": False, "source": "environment", "version": None}
    executable = (
        os.environ.get("AIRLOCK_ACCESS_PROXY")
        or shutil.which("claude-code-proxy.exe")
        or shutil.which("claude-code-proxy")
    )
    if not executable:
        return {"supported": False, "source": "not_found", "version": None}
    completed = _run_status([executable, "--version"])
    output = "" if completed is None else f"{completed.stdout}\n{completed.stderr}"
    match = re.search(r"\b([0-9]+)\.([0-9]+)\.([0-9]+)\b", output)
    if completed is None or completed.returncode != 0 or not match:
        return {"supported": False, "source": "version_unknown", "version": None}
    version = tuple(int(part) for part in match.groups())
    return {
        "supported": version >= MINIMUM_FAST_PROXY_VERSION,
        "source": "proxy_version",
        "version": ".".join(str(part) for part in version),
    }


def grok_auth_status() -> dict[str, object]:
    """Ask the local proxy whether it holds a usable Grok login.

    ``authenticated`` stays None when the answer is genuinely unknown so a
    caller can tell "checked and logged out" apart from "never checked". Only a
    confirmed negative should disable routes: a missing proxy or a probe that
    times out must not quietly remove workers the user configured.

    A proxy too old to know the `grok` subcommand exits non-zero, which reads as
    not authenticated. That is the correct answer for routing, because Grok
    cannot work through that proxy either way.
    """
    override = os.environ.get("AIRLOCK_ACCESS_GROK_AUTH", "").strip().lower()
    if override in {"1", "true", "yes"}:
        return {"authenticated": True, "source": "environment"}
    if override in {"0", "false", "no"}:
        return {"authenticated": False, "source": "environment"}
    executable = (
        os.environ.get("AIRLOCK_ACCESS_PROXY")
        or shutil.which("claude-code-proxy.exe")
        or shutil.which("claude-code-proxy")
    )
    if not executable:
        return {"authenticated": None, "source": "not_found"}
    completed = _run_status([executable, "grok", "auth", "status"])
    if completed is None:
        return {"authenticated": None, "source": "probe_failed"}
    return {
        "authenticated": completed.returncode == 0,
        "source": "proxy_status",
    }


def explicit_fast_status(
    policy: dict[str, Any], route: str, *, ephemeral: bool = False
) -> dict[str, object]:
    if route not in {"sol-fast", "luna-fast"}:
        raise AccessError(f"unknown Fast route: {route}")
    if ephemeral and route != FAST_TRANSITION_ROUTE:
        raise AccessError("ephemeral Fast eligibility is available only for sol-fast")
    fast_enabled = policy.get("policies", {}).get("openai_fast") == "on"
    plan = str(policy["providers"]["openai"].get("detected_plan", "unknown")).lower()
    capability = proxy_fast_capability()
    plan_eligible = plan in OPENAI_FAST_ELIGIBLE_PLANS
    policy_allows = fast_enabled or ephemeral
    eligible = policy_allows and plan_eligible and capability["supported"] is True
    if not policy_allows:
        reason = "OpenAI Fast routes are disabled by policy."
    elif eligible and ephemeral and not fast_enabled:
        reason = (
            f"Session-local sol-fast is eligible because OpenAI plan {plan} "
            "and proxy Fast support are verified."
        )
    elif eligible:
        reason = f"OpenAI plan {plan} and proxy Fast support are verified."
    elif not plan_eligible:
        reason = "Fast processing requires a sanitized prolite or pro OpenAI plan."
    else:
        reason = "Proxy Fast support could not be verified."
    return {
        "route": route,
        "eligible": eligible,
        "ephemeral": ephemeral,
        "enabled_by_policy": fast_enabled,
        "plan": plan,
        "plan_eligible": plan_eligible,
        "proxy_supported": capability["supported"],
        "proxy_source": capability["source"],
        "proxy_version": capability["version"],
        "reason": reason,
    }


def fast_route_status(policy: dict[str, Any]) -> dict[str, object]:
    requested = str(policy.get("policies", {}).get("swarm_fast", DEFAULT_SWARM_FAST))
    if requested not in VALID_SWARM_FAST_POLICIES:
        requested = DEFAULT_SWARM_FAST
    plan = str(policy["providers"]["openai"].get("detected_plan", "unknown")).lower()
    plan_eligible = plan in OPENAI_FAST_ELIGIBLE_PLANS
    capability = proxy_fast_capability()
    proxy_supported = capability["supported"] is True
    fast_enabled = policy.get("policies", {}).get("openai_fast") == "on"
    if not fast_enabled:
        selected_route = "luna"
        reason = "OpenAI Fast routes are disabled by provider policy."
    elif requested == "off":
        selected_route = "luna"
        reason = "Fast processing is disabled by policy."
    elif plan_eligible and proxy_supported:
        selected_route = "luna-fast"
        reason = f"OpenAI plan {plan} and proxy Fast support are verified."
    elif requested == "on":
        selected_route = None
        if not plan_eligible:
            reason = "Fast processing was forced on, but the sanitized OpenAI plan is not eligible."
        else:
            reason = "Fast processing was forced on, but proxy Fast support could not be verified."
    else:
        selected_route = "luna"
        if not plan_eligible:
            reason = "Automatic Fast processing requires a sanitized prolite or pro OpenAI plan."
        else:
            reason = "Automatic Fast processing fell back to standard Luna because proxy Fast support could not be verified."
    return {
        "requested": requested,
        "enabled_by_policy": fast_enabled,
        "selected_route": selected_route,
        "plan": plan,
        "plan_eligible": plan_eligible,
        "proxy_supported": proxy_supported,
        "proxy_source": capability["source"],
        "proxy_version": capability["version"],
        "reason": reason,
    }


def model_access(policy: dict[str, Any], provider: str, route: str) -> str:
    if provider == "openai" and route == "luna-fast":
        if fast_route_status(policy)["selected_route"] != "luna-fast":
            return "unavailable"
        return policy["providers"][provider]["models"]["luna"]["access"]
    return policy["providers"][provider]["models"][route]["access"]


def default_hybrid_root(policy: dict[str, Any]) -> str:
    """Resolve the reserved ``auto`` hybrid root under the local access policy.

    Fable wins when its Anthropic access class is neither ``extra`` nor
    ``unavailable``, otherwise Opus wins under the same rule, and Sonnet is
    the final fallback. Auto selection therefore never picks an extra-class
    model and never creates silent metered spend, whatever the extra-usage
    policy says.
    """
    for route in ("fable", "opus"):
        access = model_access(policy, "anthropic", route)
        if access not in ("extra", "unavailable"):
            return route
    return "sonnet"


def delegation_error(policy: dict[str, Any], provider: str, route: str, effort: str, extra_authorized: bool) -> tuple[str, str] | None:
    if effort not in VALID_EFFORTS:
        return "invalid_effort", "Effort must be low, medium, high, xhigh, or max"
    if effort not in policy["policies"]["allowed_efforts"][provider]:
        return "effort_not_allowed", f"Effort {effort} is disabled for {provider} workers"
    access = model_access(policy, provider, route)
    extra_policy = policy["policies"]["extra_usage"]
    if access == "unavailable" or (access == "extra" and extra_policy == "never"):
        return "model_unavailable_by_policy", f"{route} is disabled by the local access policy"
    if access == "extra" and extra_policy == "ask" and not extra_authorized:
        return "extra_usage_confirmation_required", f"{route} is marked extra usage and requires explicit confirmation"
    return None


def provider_headroom(policy: dict[str, Any], provider: str) -> dict[str, object]:
    provider_state = policy.get("providers", {}).get(provider, {})
    usage = provider_state.get("usage", _default_usage(provider))
    age = usage_age_seconds(usage)
    source = usage.get("source", "not_checked") if isinstance(usage, dict) else "not_checked"
    buckets = usage.get("buckets") if isinstance(usage, dict) else None
    remaining: float | None = None
    if age is not None and age <= USAGE_FRESH_SECONDS and isinstance(buckets, list):
        values = [
            float(item["remaining_percent"])
            for item in buckets
            if isinstance(item, dict)
            and isinstance(item.get("remaining_percent"), (int, float))
            and not isinstance(item.get("remaining_percent"), bool)
        ]
        if values:
            remaining = min(values)
    if remaining is None:
        state = "stale" if age is not None and age > USAGE_FRESH_SECONDS else "unknown"
    elif remaining < 10:
        state = "critical"
    elif remaining < 25:
        state = "low"
    elif remaining < 50:
        state = "moderate"
    else:
        state = "healthy"
    return {
        "state": state,
        "remaining_percent": round(remaining, 2) if remaining is not None else None,
        "source": source,
        "age_seconds": age,
    }


def _openrouter_route_fields(entry: Any) -> dict[str, str]:
    """Return only validated launcher-safe fields from one registry entry."""
    return {
        "route": entry.route,
        "agent": entry.agent_name,
        "model": entry.model,
        "endpoint_provider": entry.endpoint_provider,
        "provider_name": entry.provider_name,
        "provider_slug": entry.provider_slug,
        "quantization": entry.quantization,
        "canonical_slug": entry.canonical_slug,
    }


def list_enabled_openrouter_routes(policy: dict[str, Any]) -> list[dict[str, str]]:
    """List enabled routes from the already validated in-memory registry."""
    registry = policy.get("_openrouter_registry")
    entries = getattr(registry, "models", ())
    return [
        _openrouter_route_fields(entry)
        for entry in sorted(entries, key=lambda item: item.route)
        if entry.enabled
    ]


def resolve_openrouter_route(policy: dict[str, Any], route: str) -> Any:
    """Resolve one exact enabled route from the validated registry, or fail closed."""
    registry = policy.get("_openrouter_registry")
    entries = getattr(registry, "models", ())
    if isinstance(route, str):
        for entry in entries:
            if entry.route == route and entry.enabled:
                return entry
    raise AccessError(f"unknown or disabled OpenRouter route: {route!r}")


def _validate_openrouter_root_route(
    policy: dict[str, Any], profile: str, openrouter_root_route: str | None
) -> Any | None:
    if profile == "openrouter-pure":
        if openrouter_root_route is None:
            raise AccessError("openrouter-pure requires an exact OpenRouter root route")
        return resolve_openrouter_route(policy, openrouter_root_route)
    if profile == "hybrid-openrouter-root":
        if openrouter_root_route is None:
            raise AccessError(
                "hybrid-openrouter-root requires an exact OpenRouter root route"
            )
        return resolve_openrouter_route(policy, openrouter_root_route)
    if openrouter_root_route is not None:
        raise AccessError(
            f"OpenRouter root route is not valid for session profile: {profile}"
        )
    return None


def enabled_openrouter_workers(
    policy: dict[str, Any],
    profile: str,
    *,
    openrouter_root_route: str | None = None,
) -> list[dict[str, str]]:
    root_entry = _validate_openrouter_root_route(
        policy, profile, openrouter_root_route
    )
    if profile != "openrouter-pure" and not profile.startswith("hybrid-"):
        return []
    registry = policy.get("_openrouter_registry")
    entries = getattr(registry, "models", ())
    if not entries:
        return []
    extra_policy = policy["policies"]["extra_usage"]
    workers: list[dict[str, str]] = []
    for entry in entries:
        if not entry.enabled:
            continue
        selected_root = root_entry is not None and entry.route == root_entry.route
        if not selected_root and extra_policy == "never":
            continue
        workers.append({
            "provider": "openrouter",
            "route": f"or-{entry.route}",
            "agent": entry.agent_name,
            "model": entry.model,
            "effort": INHERIT_EFFORT,
            "access": "included" if selected_root else "extra",
            "capability": "unverified",
            "cost": "unknown",
            "strength": (
                "user-declared OpenRouter route; Airlock does not infer its "
                "capability, best use, context window, or relative cost"
            ),
            "endpoint_provider": entry.endpoint_provider,
            "provider_name": entry.provider_name,
            "provider_slug": entry.provider_slug,
            "quantization": entry.quantization,
            "canonical_slug": entry.canonical_slug,
        })
    return workers


def enabled_profile_workers(
    policy: dict[str, Any],
    profile: str,
    *,
    openrouter_root_route: str | None = None,
) -> list[dict[str, str]]:
    components = PROFILE_COMPONENTS.get(profile)
    if components is None:
        raise AccessError(f"unknown session profile: {profile}")
    _validate_openrouter_root_route(policy, profile, openrouter_root_route)
    extra_policy = policy["policies"]["extra_usage"]
    workers: list[dict[str, str]] = []
    for provider, catalog_name in components:
        expected_agents = CATALOG_EXPECTED_AGENTS[catalog_name]
        for route in PROVIDER_ROUTES[provider]:
            access = model_access(policy, provider, route)
            model_profile = MODEL_PROFILES[provider][route]
            if model_profile["agent"] not in expected_agents:
                continue
            if access == "unavailable" or (access == "extra" and extra_policy == "never"):
                continue
            workers.append({
                "provider": provider,
                "route": route,
                "agent": model_profile["agent"],
                "model": model_profile["model"],
                "effort": policy["policies"]["worker_effort"].get(route, INHERIT_EFFORT),
                "access": access,
                "capability": model_profile["capability"],
                "cost": model_profile["cost"],
                "strength": model_profile["strength"],
            })
    workers.extend(enabled_openrouter_workers(
        policy,
        profile,
        openrouter_root_route=openrouter_root_route,
    ))
    if len(workers) > MAX_CONFIGURED_SUBAGENTS:
        raise AccessError(
            f"{profile} enables more than {MAX_CONFIGURED_SUBAGENTS} named Agents"
        )
    return workers


def wire_model_id(model: str) -> str:
    """Return the model ID that reaches the provider.

    Claude Code uses ``[1m]`` only for the supported native Claude models;
    it strips that instruction before sending the request. Legacy GPT IDs
    carrying the old suffix are normalized to their bare ID as a defensive
    compatibility measure, but every canonical OpenAI catalog and route is
    bare.
    """
    if model.endswith("[1m]"):
        return model.removesuffix("[1m]")
    return model


DISCOVERY_ROUTES = (
    "luna",
    "composer",
    "haiku",
    "luna-fast",
    "terra",
    "sonnet",
    "grok",
    "sol",
    "fable",
    "opus",
)


def discovery_model_from_workers(
    policy: dict[str, Any], workers: list[dict[str, str]]
) -> str | None:
    """Choose a cheap exact model that never bypasses extra-usage approval."""
    require_confirmation = policy["policies"]["extra_usage"] == "ask"
    by_route = {
        worker["route"]: worker["model"]
        for worker in workers
        if not (require_confirmation and worker["access"] == "extra")
    }
    for route in DISCOVERY_ROUTES:
        model = by_route.get(route)
        if model:
            return model
    return None


def discovery_model(
    policy: dict[str, Any],
    profile: str,
    *,
    openrouter_root_route: str | None = None,
) -> str | None:
    workers = enabled_profile_workers(
        policy,
        profile,
        openrouter_root_route=openrouter_root_route,
    )
    if profile == "openrouter-pure":
        return resolve_openrouter_route(policy, openrouter_root_route).model
    return discovery_model_from_workers(policy, workers)


def proxy_picker_models(
    policy: dict[str, Any],
    profile: str,
    *,
    openrouter_root_route: str | None = None,
) -> dict[str, str]:
    """Map Claude Code's four Agent/model family aliases to exact enabled models.

    Claude Code's Agent tool accepts family aliases rather than arbitrary model
    IDs. Every slot therefore has to resolve inside the active route policy, and
    the Haiku slot is reserved for the economical discovery model. Routes gated
    behind explicit extra-usage confirmation are excluded because a model alias
    has no place to carry that confirmation marker.
    """
    workers = [
        worker for worker in enabled_profile_workers(
            policy,
            profile,
            openrouter_root_route=openrouter_root_route,
        )
        if not (
            worker["access"] == "extra"
            and policy["policies"]["extra_usage"] == "ask"
        )
    ]
    by_route = {worker["route"]: worker["model"] for worker in workers}

    def first(*routes: str) -> str:
        for route in routes:
            model = by_route.get(route)
            if model:
                return model
        raise AccessError(f"{profile} has no model eligible for a Claude Code family slot")

    if profile == "openrouter-pure":
        root = resolve_openrouter_route(policy, str(openrouter_root_route)).model
        return {family: root for family in ("fable", "opus", "sonnet", "haiku")}
    if profile == "openai-pure":
        fable = opus = first("sol", "terra", "luna", "luna-fast")
        sonnet = first("terra", "sol", "luna", "luna-fast")
    elif profile == "grok-pure":
        fable = opus = first("grok", "composer")
        sonnet = first("composer", "grok")
    else:
        fable = first(
            "fable", "sol", "opus", "grok", "sonnet", "terra",
            "composer", "luna", "haiku", "luna-fast",
        )
        opus = first(
            "opus", "sol", "grok", "fable", "sonnet", "terra",
            "composer", "luna", "haiku", "luna-fast",
        )
        sonnet = first(
            "sonnet", "terra", "fable", "sol", "opus", "composer",
            "luna", "haiku", "grok", "luna-fast",
        )
    utility = discovery_model_from_workers(policy, workers)
    if utility is None:
        raise AccessError(f"{profile} has no model eligible for the discovery slot")
    return {
        "fable": fable,
        "opus": opus,
        "sonnet": sonnet,
        "haiku": utility,
    }


def session_route_policy(
    policy: dict[str, Any],
    profile: str,
    *,
    openrouter_root_route: str | None = None,
) -> dict[str, object]:
    workers = enabled_profile_workers(
        policy,
        profile,
        openrouter_root_route=openrouter_root_route,
    )
    routes: dict[str, str] = {}
    model_ids: list[str] = []
    agent_names: list[str] = []
    extra_agent_names: list[str] = []
    extra_model_ids: list[str] = []
    require_confirmation = policy["policies"]["extra_usage"] == "ask"
    for worker in workers:
        model = worker["model"]
        provider = worker["provider"]
        model_ids.append(model)
        for routed_model in {model, wire_model_id(model)}:
            if routed_model in routes and routes[routed_model] != provider:
                raise AccessError(f"model route is ambiguous: {routed_model}")
            routes[routed_model] = provider
        agent_names.append(worker["agent"])
        if require_confirmation and worker["access"] == "extra":
            extra_agent_names.append(worker["agent"])
            extra_model_ids.append(model)
    if not routes or len(agent_names) != len(set(agent_names)):
        raise AccessError("native session route policy is empty or ambiguous")
    discovery = (
        resolve_openrouter_route(policy, str(openrouter_root_route)).model
        if profile == "openrouter-pure"
        else discovery_model_from_workers(policy, workers)
    )
    return {
        "routes": {model: routes[model] for model in sorted(routes)},
        "model_ids": sorted(model_ids),
        "agent_names": sorted(agent_names),
        "extra_model_ids": sorted(extra_model_ids),
        "extra_agent_names": sorted(extra_agent_names),
        "picker_models": proxy_picker_models(
            policy,
            profile,
            openrouter_root_route=openrouter_root_route,
        ),
        "discovery_model": discovery,
    }


def build_session_snapshot(
    policy: dict[str, Any],
    profile: str,
    root_model: str,
    *,
    openrouter_root_route: str | None = None,
) -> Any:
    root_provider = PROFILE_ROOT_PROVIDERS.get(profile)
    if root_provider is None:
        raise AccessError(f"unknown session profile: {profile}")
    root_entry = _validate_openrouter_root_route(
        policy, profile, openrouter_root_route
    )
    if root_entry is not None and root_model != root_entry.model:
        raise AccessError(
            "session root model does not match the selected OpenRouter route"
        )
    route_policy = session_route_policy(
        policy,
        profile,
        openrouter_root_route=openrouter_root_route,
    )
    raw_routes = route_policy["routes"]
    if not isinstance(raw_routes, dict):
        raise AccessError("session route policy is invalid")
    routes = dict(raw_routes)
    existing_provider = routes.get(root_model)
    if existing_provider is not None and existing_provider != root_provider:
        raise AccessError(
            f"session root model route disagrees with {root_provider}: {root_model}"
        )
    routes[root_model] = root_provider
    workers = enabled_profile_workers(
        policy,
        profile,
        openrouter_root_route=openrouter_root_route,
    )
    extra_agents = set(route_policy["extra_agent_names"])
    agents = {
        worker["agent"]: {
            "model": worker["model"],
            "provider": worker["provider"],
            "extra_usage": worker["agent"] in extra_agents,
        }
        for worker in workers
    }
    if root_entry is not None:
        root_agent = agents.get(root_entry.agent_name)
        if (
            not isinstance(root_agent, dict)
            or root_agent.get("model") != root_model
            or root_agent.get("provider") != "openrouter"
            or root_agent.get("extra_usage") is not False
        ):
            raise AccessError("selected OpenRouter root Agent is missing or extra-gated")
    openrouter = {
        worker["model"]: {
            "endpoint_provider": worker["endpoint_provider"],
            "provider_name": worker["provider_name"],
            "provider_slug": worker["provider_slug"],
            "quantization": worker["quantization"],
            "canonical_slug": worker["canonical_slug"],
        }
        for worker in workers
        if worker["provider"] == "openrouter"
    }
    try:
        return POLICY_SCHEMA.validate_session_snapshot({
            "schema_version": 1,
            "protocol_version": MANAGED_PROTOCOL_VERSION,
            "profile": profile,
            "root_model": root_model,
            "root_provider": root_provider,
            "routes": routes,
            "agents": agents,
            "openrouter": openrouter,
        })
    except POLICY_SCHEMA.PolicyValidationError as exc:
        raise AccessError(f"session policy snapshot is invalid: {exc}") from exc


def session_runtime_dir() -> Path:
    configured = os.environ.get("AIRLOCK_SESSION_RUNTIME_DIR")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        root = Path(local) if local else Path.home() / "AppData" / "Local"
        return root / "Airlock" / "sessions"
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "airlock"
    return config_path().parent / "runtime"


def _prepare_session_runtime_dir(directory: Path | None = None) -> Path:
    target_dir = directory or session_runtime_dir()
    if (
        target_dir.is_symlink()
        or (
            target_dir.exists()
            and (
                not target_dir.is_dir()
                or _is_reparse_point(target_dir.lstat())
            )
        )
    ):
        raise AccessError("session runtime path is not a safe directory")
    target_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    if os.name != "nt":
        details = target_dir.stat()
        if hasattr(os, "geteuid") and details.st_uid != os.geteuid():
            raise AccessError("session runtime directory is not owned by this user")
        if details.st_mode & 0o077:
            os.chmod(target_dir, 0o700)
    return target_dir


def _is_reparse_point(details: os.stat_result) -> bool:
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _validate_fast_transition_channel(channel: object) -> str:
    if not isinstance(channel, str) or not re.fullmatch(
        r"fast-transition-[0-9a-f]{32}\.json", channel
    ):
        raise AccessError("Fast transition channel is invalid")
    return channel


def _validate_fast_transition_nonce(nonce: object) -> str:
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{64}", nonce):
        raise AccessError("Fast transition nonce is invalid")
    return nonce


def _validate_fast_transition_pid(pid: object) -> int:
    if isinstance(pid, bool):
        raise AccessError("Fast transition launcher PID is invalid")
    try:
        parsed = int(pid)
    except (TypeError, ValueError) as exc:
        raise AccessError("Fast transition launcher PID is invalid") from exc
    if str(parsed) != str(pid) or not 1 <= parsed <= 0xFFFFFFFF:
        raise AccessError("Fast transition launcher PID is invalid")
    return parsed


def _canonical_fast_transition_cwd(cwd: object) -> str:
    if not isinstance(cwd, (str, os.PathLike)):
        raise AccessError("Fast transition cwd is invalid")
    raw = os.fspath(cwd)
    if not raw or len(raw) > 4096 or any(character in raw for character in "\x00\r\n"):
        raise AccessError("Fast transition cwd is invalid")
    try:
        resolved = Path(raw).expanduser().resolve(strict=True)
    except OSError as exc:
        raise AccessError("Fast transition cwd is unavailable") from exc
    if not resolved.is_dir():
        raise AccessError("Fast transition cwd is not a directory")
    canonical = os.path.normcase(str(resolved)) if os.name == "nt" else str(resolved)
    if len(canonical) > 4096:
        raise AccessError("Fast transition cwd is invalid")
    return canonical


def _validate_fast_transition_session_id(session_id: object) -> str:
    if not isinstance(session_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", session_id
    ):
        raise AccessError("Fast transition session ID is invalid")
    return session_id


def _fast_transition_credentials(
    channel: str | None, nonce: str | None
) -> tuple[str, str]:
    explicit = channel is not None or nonce is not None
    if explicit and (channel is None or nonce is None):
        raise AccessError("Fast transition channel and nonce must be supplied together")
    selected_channel = channel if explicit else os.environ.get(FAST_TRANSITION_ENV_CHANNEL)
    selected_nonce = nonce if explicit else os.environ.get(FAST_TRANSITION_ENV_NONCE)
    return (
        _validate_fast_transition_channel(selected_channel),
        _validate_fast_transition_nonce(selected_nonce),
    )


def _fast_transition_path(channel: str) -> Path:
    runtime_dir = _prepare_session_runtime_dir()
    return runtime_dir / _validate_fast_transition_channel(channel)


@contextmanager
def _fast_transition_lock(channel: str) -> Any:
    runtime_dir = _prepare_session_runtime_dir()
    lock_path = runtime_dir / f".{_validate_fast_transition_channel(channel)}.lock"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise AccessError("Fast transition channel is busy") from exc
    try:
        os.close(descriptor)
        os.chmod(lock_path, 0o600)
        yield
    finally:
        try:
            details = lock_path.lstat()
            if not lock_path.is_symlink() and stat.S_ISREG(details.st_mode):
                lock_path.unlink()
        except OSError:
            pass


def _fast_transition_file_details(path: Path) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as exc:
        raise AccessError("Fast transition channel is missing") from exc
    if (
        path.is_symlink()
        or _is_reparse_point(details)
        or not stat.S_ISREG(details.st_mode)
        or details.st_size > MAX_FAST_TRANSITION_BYTES
    ):
        raise AccessError("Fast transition channel is unsafe")
    return details


def _load_fast_transition(channel: str) -> tuple[Path, dict[str, object]]:
    path = _fast_transition_path(channel)
    before = _fast_transition_file_details(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise AccessError("Fast transition channel changed while reading")
            content = stream.read(MAX_FAST_TRANSITION_BYTES + 1)
        after = _fast_transition_file_details(path)
    except AccessError:
        raise
    except OSError as exc:
        raise AccessError("Fast transition channel could not be read securely") from exc
    if (
        (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        or len(content) > MAX_FAST_TRANSITION_BYTES
        or len(content) != before.st_size
    ):
        raise AccessError("Fast transition channel changed while reading")
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AccessError("Fast transition channel contains invalid JSON") from exc
    if not isinstance(value, dict):
        raise AccessError("Fast transition channel must contain a JSON object")
    common = {
        "schema_version", "state", "route", "model", "channel",
        "nonce_sha256", "launcher_pid", "cwd", "created_at", "expires_at",
    }
    state = value.get("state")
    expected = common | (
        {"session_id", "armed_at"}
        if state in {"armed", "ready", "consumed"}
        else set()
    )
    if state not in {"pending", "armed", "ready", "consumed"} or set(value) != expected:
        raise AccessError("Fast transition channel schema is invalid")
    if (
        value.get("schema_version") != FAST_TRANSITION_SCHEMA_VERSION
        or value.get("route") != FAST_TRANSITION_ROUTE
        or value.get("model") != FAST_TRANSITION_MODEL
        or value.get("channel") != channel
        or not isinstance(value.get("nonce_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("nonce_sha256")))
        or isinstance(value.get("created_at"), bool)
        or not isinstance(value.get("created_at"), int)
        or not 0 <= value["created_at"] <= 0x7FFFFFFFFFFFFFFF
        or isinstance(value.get("expires_at"), bool)
        or not isinstance(value.get("expires_at"), int)
        or not 0 <= value["expires_at"] <= 0x7FFFFFFFFFFFFFFF
    ):
        raise AccessError("Fast transition channel schema is invalid")
    if state == "pending":
        if value["expires_at"] != 0:
            raise AccessError("Fast transition pending state must not expire")
    elif (
        isinstance(value.get("armed_at"), bool)
        or not isinstance(value.get("armed_at"), int)
        or not 0 <= value["armed_at"] <= 0x7FFFFFFFFFFFFFFF
        or value["armed_at"] < value["created_at"]
        or value["expires_at"] - value["armed_at"] != FAST_TRANSITION_TTL_SECONDS
    ):
        raise AccessError("Fast transition channel schema is invalid")
    _validate_fast_transition_pid(value.get("launcher_pid"))
    canonical_cwd = _canonical_fast_transition_cwd(value.get("cwd"))
    if not hmac.compare_digest(canonical_cwd, str(value.get("cwd"))):
        raise AccessError("Fast transition channel cwd is not canonical")
    if state != "pending":
        _validate_fast_transition_session_id(value.get("session_id"))
    return path, value


def _write_fast_transition(path: Path, value: dict[str, object], *, exclusive: bool) -> None:
    content = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(content) > MAX_FAST_TRANSITION_BYTES:
        raise AccessError("Fast transition state exceeds its size limit")
    target_dir = _prepare_session_runtime_dir()
    if path.parent.resolve(strict=True) != target_dir.resolve(strict=True):
        raise AccessError("Fast transition channel is outside the runtime directory")
    if exclusive:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        created = False
        try:
            descriptor = os.open(path, flags, 0o600)
            created = True
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(path, 0o600)
            POLICY_SCHEMA.protect_private_path(path)
            return
        except (OSError, POLICY_SCHEMA.PolicyValidationError) as exc:
            if created:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise AccessError("Fast transition channel could not be created exclusively") from exc
    _fast_transition_file_details(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".fast-transition-", suffix=".tmp", dir=target_dir
    )
    replaced = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, 0o600)
        POLICY_SCHEMA.protect_private_path(temporary_name)
        _fast_transition_file_details(path)
        os.replace(temporary_name, path)
        replaced = True
    except (OSError, POLICY_SCHEMA.PolicyValidationError) as exc:
        raise AccessError("Fast transition state could not be replaced atomically") from exc
    finally:
        if not replaced:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def _validate_fast_transition_binding(
    value: dict[str, object], nonce: str, launcher_pid: object, cwd: object,
    *, check_expiry: bool = True, now: int | None = None,
) -> None:
    digest = hashlib.sha256(nonce.encode("ascii")).hexdigest()
    if not hmac.compare_digest(digest, str(value["nonce_sha256"])):
        raise AccessError("Fast transition nonce does not match")
    if _validate_fast_transition_pid(launcher_pid) != value["launcher_pid"]:
        raise AccessError("Fast transition launcher PID does not match")
    canonical_cwd = _canonical_fast_transition_cwd(cwd)
    if not hmac.compare_digest(canonical_cwd, str(value["cwd"])):
        raise AccessError("Fast transition cwd does not match")
    current = int(time.time()) if now is None else now
    if isinstance(current, bool) or not isinstance(current, int) or current < 0:
        raise AccessError("Fast transition validation time is invalid")
    if (
        check_expiry
        and value.get("state") != "pending"
        and current > value["expires_at"]
    ):
        raise AccessError("Fast transition channel has expired")


def fast_transition_create(
    launcher_pid: object, cwd: object, *, now: int | None = None,
) -> dict[str, str]:
    pid = _validate_fast_transition_pid(launcher_pid)
    canonical_cwd = _canonical_fast_transition_cwd(cwd)
    created_at = int(time.time()) if now is None else now
    if (
        isinstance(created_at, bool)
        or not isinstance(created_at, int)
        or not 0 <= created_at <= 0x7FFFFFFFFFFFFFFF - FAST_TRANSITION_TTL_SECONDS
    ):
        raise AccessError("Fast transition creation time is invalid")
    runtime_dir = _prepare_session_runtime_dir()
    for _attempt in range(8):
        channel = f"fast-transition-{secrets.token_hex(16)}.json"
        nonce = secrets.token_hex(32)
        value: dict[str, object] = {
            "schema_version": FAST_TRANSITION_SCHEMA_VERSION,
            "state": "pending",
            "route": FAST_TRANSITION_ROUTE,
            "model": FAST_TRANSITION_MODEL,
            "channel": channel,
            "nonce_sha256": hashlib.sha256(nonce.encode("ascii")).hexdigest(),
            "launcher_pid": pid,
            "cwd": canonical_cwd,
            "created_at": created_at,
            "expires_at": 0,
        }
        try:
            _write_fast_transition(runtime_dir / channel, value, exclusive=True)
        except AccessError:
            continue
        return {"channel": channel, "nonce": nonce}
    raise AccessError("Fast transition channel could not be allocated")


def fast_transition_arm(
    session_id: object, channel: str | None = None, nonce: str | None = None,
    *, now: int | None = None,
) -> None:
    selected_channel, selected_nonce = _fast_transition_credentials(channel, nonce)
    with _fast_transition_lock(selected_channel):
        path, value = _load_fast_transition(selected_channel)
        digest = hashlib.sha256(selected_nonce.encode("ascii")).hexdigest()
        if not hmac.compare_digest(digest, str(value["nonce_sha256"])):
            raise AccessError("Fast transition nonce does not match")
        current = int(time.time()) if now is None else now
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            raise AccessError("Fast transition validation time is invalid")
        if value["state"] != "pending":
            raise AccessError("Fast transition can be armed only from pending state")
        if current < value["created_at"]:
            raise AccessError("Fast transition arm time predates creation")
        if current > 0x7FFFFFFFFFFFFFFF - FAST_TRANSITION_TTL_SECONDS:
            raise AccessError("Fast transition arm time is invalid")
        value["state"] = "armed"
        value["session_id"] = _validate_fast_transition_session_id(session_id)
        value["armed_at"] = current
        value["expires_at"] = current + FAST_TRANSITION_TTL_SECONDS
        _write_fast_transition(path, value, exclusive=False)


def _read_fast_transition_finalize_input(stream: Any = None) -> dict[str, str]:
    source = sys.stdin.buffer if stream is None else stream
    try:
        content = source.read(MAX_FAST_TRANSITION_STDIN_BYTES + 1)
    except (OSError, AttributeError) as exc:
        raise AccessError("Fast transition finalize input could not be read") from exc
    if isinstance(content, str):
        content = content.encode("utf-8")
    if not isinstance(content, bytes) or len(content) > MAX_FAST_TRANSITION_STDIN_BYTES:
        raise AccessError("Fast transition finalize input is too large")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AccessError("Fast transition finalize input is invalid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"session_id", "cwd", "reason"}:
        raise AccessError("Fast transition finalize input schema is invalid")
    session_id = _validate_fast_transition_session_id(payload.get("session_id"))
    cwd = _canonical_fast_transition_cwd(payload.get("cwd"))
    reason = payload.get("reason")
    if reason != "prompt_input_exit":
        raise AccessError("Fast transition requires a clean prompt_input_exit")
    return {"session_id": session_id, "cwd": cwd, "reason": reason}


def fast_transition_finalize(
    payload: dict[str, str], channel: str | None = None, nonce: str | None = None,
    *, now: int | None = None,
) -> None:
    if not isinstance(payload, dict) or set(payload) != {"session_id", "cwd", "reason"}:
        raise AccessError("Fast transition finalize input schema is invalid")
    session_id = _validate_fast_transition_session_id(payload.get("session_id"))
    canonical_cwd = _canonical_fast_transition_cwd(payload.get("cwd"))
    if payload.get("reason") != "prompt_input_exit":
        raise AccessError("Fast transition requires a clean prompt_input_exit")
    selected_channel, selected_nonce = _fast_transition_credentials(channel, nonce)
    with _fast_transition_lock(selected_channel):
        path, value = _load_fast_transition(selected_channel)
        digest = hashlib.sha256(selected_nonce.encode("ascii")).hexdigest()
        if not hmac.compare_digest(digest, str(value["nonce_sha256"])):
            raise AccessError("Fast transition nonce does not match")
        current = int(time.time()) if now is None else now
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            raise AccessError("Fast transition validation time is invalid")
        if value["state"] != "armed":
            raise AccessError("Fast transition can be finalized only from armed state")
        if current > value["expires_at"]:
            raise AccessError("Fast transition channel has expired")
        if not hmac.compare_digest(session_id, str(value["session_id"])):
            raise AccessError("Fast transition session ID does not match")
        if not hmac.compare_digest(canonical_cwd, str(value["cwd"])):
            raise AccessError("Fast transition cwd does not match")
        value["state"] = "ready"
        _write_fast_transition(path, value, exclusive=False)


def fast_transition_consume(
    launcher_pid: object, cwd: object, channel: str | None = None,
    nonce: str | None = None, *, now: int | None = None,
    if_ready: bool = False,
) -> str | None:
    selected_channel, selected_nonce = _fast_transition_credentials(channel, nonce)
    with _fast_transition_lock(selected_channel):
        path, value = _load_fast_transition(selected_channel)
        _validate_fast_transition_binding(
            value, selected_nonce, launcher_pid, cwd, now=now
        )
        if if_ready and value["state"] == "pending":
            return None
        if if_ready and value["state"] == "armed":
            raise AccessError(
                "Fast transition was armed but SessionEnd did not finalize it"
            )
        if value["state"] != "ready":
            raise AccessError("Fast transition can be consumed only from ready state")
        session_id = _validate_fast_transition_session_id(value.get("session_id"))
        value["state"] = "consumed"
        _write_fast_transition(path, value, exclusive=False)
        try:
            path.unlink()
        except OSError as exc:
            raise AccessError("Fast transition was consumed but could not be removed") from exc
    return session_id


def fast_transition_cleanup(
    launcher_pid: object, cwd: object, channel: str | None = None,
    nonce: str | None = None,
) -> None:
    selected_channel, selected_nonce = _fast_transition_credentials(channel, nonce)
    with _fast_transition_lock(selected_channel):
        path, value = _load_fast_transition(selected_channel)
        _validate_fast_transition_binding(
            value, selected_nonce, launcher_pid, cwd, check_expiry=False
        )
        try:
            path.unlink()
        except OSError as exc:
            raise AccessError("Fast transition channel could not be cleaned up") from exc


def write_session_artifact(
    content: bytes,
    prefix: str,
    suffix: str,
    directory: Path | None = None,
) -> tuple[Path, str, int]:
    if not isinstance(content, bytes) or len(content) > MAX_SESSION_ARTIFACT_BYTES:
        raise AccessError("session artifact content is invalid or too large")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}-", prefix):
        raise AccessError("session artifact prefix is invalid")
    if suffix not in {".json", ".txt"}:
        raise AccessError("session artifact suffix is invalid")

    temporary_path: Path | None = None
    descriptor = -1
    try:
        target_dir = _prepare_session_runtime_dir(directory)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=prefix, suffix=suffix, dir=target_dir
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, 0o600)
    except BaseException as exc:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        if isinstance(exc, AccessError):
            raise
        if isinstance(exc, OSError):
            raise AccessError("session artifact could not be written securely") from exc
        raise
    if temporary_path is None:
        raise AccessError("session artifact could not be written securely")
    return temporary_path, hashlib.sha256(content).hexdigest(), len(content)


def delete_session_artifact(
    path: Path,
    expected_digest: str,
    expected_size: int,
    directory: Path | None = None,
) -> None:
    target = path.expanduser()
    runtime_dir = (directory or session_runtime_dir()).expanduser()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise AccessError("session artifact digest is invalid")
    if not 0 <= expected_size <= MAX_SESSION_ARTIFACT_BYTES:
        raise AccessError("session artifact size is invalid")
    try:
        if not runtime_dir.exists() or runtime_dir.is_symlink():
            raise AccessError("session runtime path is not a safe directory")
        if target.parent.resolve(strict=True) != runtime_dir.resolve(strict=True):
            raise AccessError("session artifact is outside the runtime directory")
        before = target.lstat()
        if (
            target.is_symlink()
            or not target.is_file()
            or before.st_size != expected_size
        ):
            raise AccessError("session artifact changed before cleanup")
        with target.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise AccessError("session artifact changed before cleanup")
            content = stream.read(expected_size + 1)
        after = target.lstat()
        if (
            target.is_symlink()
            or not target.is_file()
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or after.st_size != expected_size
            or len(content) != expected_size
            or not hmac.compare_digest(
                hashlib.sha256(content).hexdigest(), expected_digest
            )
        ):
            raise AccessError("session artifact changed before cleanup")
        target.unlink()
    except AccessError:
        raise
    except OSError as exc:
        raise AccessError("session artifact could not be removed securely") from exc


def write_session_snapshot(
    policy: dict[str, Any],
    profile: str,
    root_model: str,
    directory: Path | None = None,
    *,
    openrouter_root_route: str | None = None,
) -> tuple[Path, str]:
    snapshot = build_session_snapshot(
        policy,
        profile,
        root_model,
        openrouter_root_route=openrouter_root_route,
    )
    temporary_path: Path | None = None
    descriptor = -1
    try:
        target_dir = _prepare_session_runtime_dir(directory)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="session-", suffix=".json", dir=target_dir
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(snapshot.canonical_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, 0o600)
        POLICY_SCHEMA.protect_private_path(temporary_path)
    except (AccessError, OSError, POLICY_SCHEMA.PolicyValidationError) as exc:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        if isinstance(exc, AccessError):
            raise
        raise AccessError("session policy snapshot could not be written securely") from exc
    if temporary_path is None:
        raise AccessError("session policy snapshot could not be written securely")
    return temporary_path, snapshot.digest()


def delete_session_snapshot(path: Path, expected_digest: str) -> None:
    target = path.expanduser()
    runtime_dir = session_runtime_dir().expanduser()
    try:
        if not runtime_dir.exists() or runtime_dir.is_symlink():
            raise AccessError("session runtime path is not a safe directory")
        if target.parent.resolve(strict=True) != runtime_dir.resolve(strict=True):
            raise AccessError("session policy snapshot is outside the runtime directory")
        before = target.lstat()
        POLICY_SCHEMA.load_session_snapshot(target, expected_digest)
        after = target.lstat()
        if (
            target.is_symlink()
            or not target.is_file()
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise AccessError("session policy snapshot changed before cleanup")
        target.unlink()
    except AccessError:
        raise
    except (OSError, POLICY_SCHEMA.PolicyValidationError) as exc:
        raise AccessError("session policy snapshot could not be removed securely") from exc


def validate_session_router_url(raw_url: str) -> tuple[str, int]:
    """Accept only Airlock's unauthenticated IPv4 loopback router address."""
    if not isinstance(raw_url, str) or not re.fullmatch(
        r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", raw_url
    ):
        raise AccessError(
            "session usage requires an active Airlock hybrid router on 127.0.0.1"
        )
    parsed = urlsplit(raw_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise AccessError("session router port is invalid") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port is None
        or not 1 <= port <= 65535
    ):
        raise AccessError(
            "session usage requires an active Airlock hybrid router on 127.0.0.1"
        )
    return "127.0.0.1", port


def fetch_session_diagnostics(router_url: str) -> dict[str, Any]:
    host, port = validate_session_router_url(router_url)
    connection = http.client.HTTPConnection(
        host, port, timeout=SESSION_DIAGNOSTICS_TIMEOUT_SECONDS
    )
    try:
        connection.request("GET", "/diagnostics", headers={"Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise AccessError("active Airlock session diagnostics are unavailable")
        content_type = response.getheader("content-type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            raise AccessError("active Airlock session diagnostics returned an invalid type")
        body = response.read(MAX_SESSION_DIAGNOSTICS_BYTES + 1)
        if len(body) > MAX_SESSION_DIAGNOSTICS_BYTES:
            raise AccessError("active Airlock session diagnostics are too large")
    except (OSError, http.client.HTTPException) as exc:
        raise AccessError("active Airlock session diagnostics are unavailable") from exc
    finally:
        connection.close()
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AccessError("active Airlock session diagnostics are invalid") from exc
    if not isinstance(payload, dict):
        raise AccessError("active Airlock session diagnostics are invalid")
    return payload


def session_usage_report(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and retain only cumulative counts from router diagnostics."""
    if set(payload) != {"instance_id", "events", "summary"}:
        raise AccessError("active Airlock session diagnostics have an invalid shape")
    instance_id = payload.get("instance_id")
    events = payload.get("events")
    raw_summary = payload.get("summary")
    if not isinstance(instance_id, str) or not re.fullmatch(r"[0-9a-f]{16}", instance_id):
        raise AccessError("active Airlock session diagnostics have an invalid instance")
    if not isinstance(events, list) or len(events) > MAX_SESSION_DIAGNOSTIC_EVENTS:
        raise AccessError("active Airlock session diagnostics have an invalid event window")
    if not isinstance(raw_summary, list) or len(raw_summary) > 128:
        raise AccessError("active Airlock session usage summary is invalid")

    expected_fields = {
        "provider", "model", "requests", "completed", "errors", "usage_events",
        *SESSION_USAGE_FIELDS,
    }
    groups: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_group in raw_summary:
        if not isinstance(raw_group, dict) or set(raw_group) != expected_fields:
            raise AccessError("active Airlock session usage summary is invalid")
        provider = raw_group.get("provider")
        model = raw_group.get("model")
        if provider == "openrouter":
            continue
        if provider not in PROVIDER_ROUTES or not isinstance(model, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:+\-\[\]]{0,127}", model
        ):
            raise AccessError("active Airlock session usage route is invalid")
        key = (provider, model)
        if key in seen:
            raise AccessError("active Airlock session usage route is duplicated")
        seen.add(key)
        counts: dict[str, int] = {}
        for field in expected_fields - {"provider", "model"}:
            value = raw_group.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 10**15
            ):
                raise AccessError("active Airlock session usage count is invalid")
            counts[field] = value
        if (
            counts["completed"] + counts["errors"] != counts["requests"]
            or counts["usage_events"] > counts["requests"]
        ):
            raise AccessError("active Airlock session usage counts are inconsistent")
        groups.append({"provider": provider, "model": model, **counts})
    groups.sort(key=lambda group: (group["provider"], group["model"]))
    return {
        "source": "airlock-router",
        "scope": "current-session",
        "billing": False,
        "bounded_event_window": len(events),
        "groups": groups,
    }


def session_usage_lines(report: dict[str, Any]) -> list[str]:
    lines = [
        "Airlock observed usage for the current hybrid session (provider-reported, not a bill):"
    ]
    groups = report.get("groups")
    if not isinstance(groups, list) or not groups:
        lines.append("  No routed model requests have completed yet.")
    else:
        for group in groups:
            lines.append(
                f"  {group['provider']}/{group['model']}: "
                f"requests={group['requests']}, completed={group['completed']}, "
                f"errors={group['errors']}, usage-observed={group['usage_events']}, "
                f"input={group['input_tokens']}, "
                f"cache-write={group['cache_creation_input_tokens']}, "
                f"cache-read={group['cache_read_input_tokens']}, "
                f"output={group['output_tokens']}"
            )
    lines.append(
        "OpenRouter usage is not included because it belongs to a separate account."
    )
    lines.append(
        "These counts come from upstream usage fields. Claude Code may still show zero "
        "on native Agent cards for custom OpenAI or Grok model IDs."
    )
    return lines


def session_route_field(
    policy: dict[str, Any],
    profile: str,
    field: str,
    *,
    openrouter_root_route: str | None = None,
) -> str:
    route_policy = session_route_policy(
        policy,
        profile,
        openrouter_root_route=openrouter_root_route,
    )
    if field == "routes":
        return json.dumps(
            route_policy["routes"], separators=(",", ":"), ensure_ascii=True
        )
    if field == "picker-models":
        picker_models = route_policy.get("picker_models")
        if not isinstance(picker_models, dict) or any(
            family not in {"fable", "opus", "sonnet", "haiku"}
            or not isinstance(model, str) or not model
            for family, model in picker_models.items()
        ):
            raise AccessError("native session picker model map is invalid")
        return "\n".join(
            f"{family}={picker_models[family]}" for family in sorted(picker_models)
        )
    if field == "discovery-model":
        model = route_policy.get("discovery_model")
        if model is None:
            return ""
        if not isinstance(model, str) or model not in route_policy["model_ids"]:
            raise AccessError("native session discovery model is invalid")
        return model
    values = route_policy.get(field.replace("-", "_"))
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise AccessError("native session route field is invalid")
    return ",".join(values)


def ui_ux_guidance(policy: dict[str, Any], profile: str, workers: list[dict[str, str]]) -> str:
    if profile == "openrouter-pure":
        return (
            "UI/UX routing: this OpenRouter-only profile has no verified design specialist. "
            "Do not infer capability or cost from the selected model or substitute another route; "
            "follow the user's exact selected root and verify rendered behavior and accessibility."
        )
    if profile == "openai-pure":
        return (
            "UI/UX routing: this OpenAI-only profile has no Anthropic worker. Do not invoke or "
            "claim an Anthropic route; follow the user's exact eligible choice and the enabled OpenAI pool."
        )
    if profile == "grok-pure":
        return (
            "UI/UX routing: this Grok-only profile has no Anthropic worker. Do not invoke or "
            "claim an Anthropic or OpenAI route; follow the user's exact eligible choice and the enabled Grok pool."
        )
    by_route = {worker["route"]: worker for worker in workers}
    opus = by_route.get("opus")
    sonnet = by_route.get("sonnet")
    if not opus and not sonnet:
        return (
            "UI/UX routing: no native Anthropic design worker is enabled. Do not substitute a "
            "disabled route; use the root or ask about an eligible route only when the choice blocks the task."
        )
    parts = [
        "UI/UX routing: an exact user model choice wins. Subject to enabled routes and extra-usage policy, "
        "substantial visual and interaction design is Anthropic-first and Opus-led. Material keyboard navigation, "
        "selection mechanics, onboarding flow, and information hierarchy are design judgment even when bounded. "
        "Small copy or spacing fixes may stay with the root; material user-facing changes are not generic work."
    ]
    if opus:
        confirmation = (
            " after explicit extra-usage confirmation"
            if opus["access"] == "extra" and policy["policies"]["extra_usage"] == "ask"
            else ""
        )
        parts.append(
            f"Start {opus['agent']}{confirmation} before substantial visual, product-flow, design-system, "
            "cross-surface, or material new interaction pattern such as keyboard selection work. This is a "
            "routing requirement when Opus is enabled and eligible; the generic work-directly rule does not "
            "override it. Let Opus own direction and tightly coupled implementation."
        )
    if sonnet:
        parts.append(
            f"Use {sonnet['agent']} for work that follows an existing design system, iterative refinement, "
            "bounded component work, or as the design fallback when Opus is not eligible."
        )
    if opus and sonnet:
        parts.append("Start with Opus for new design judgment; do not launch both by default.")
    parts.append(
        "For mixed UI and systems requests, split the roles automatically: the Anthropic worker owns interaction, "
        "and the root or best implementation route owns separable non-UI work. A shared result does not make every "
        "phase coupled. Do not wait for the user to request this split. The root integrates and verifies behavior "
        "and accessibility."
    )
    return " ".join(parts)


SWARM_ROUTES = ("luna", "luna-fast", "composer")


def swarm_plan(workers: list[dict[str, str]]) -> dict[str, str]:
    """Describe automatic fan-out using only the routes this session enabled.

    Automatic armies stay restricted to the economical high-volume routes. When
    none of them are enabled the session must be told that plainly rather than
    handed instructions for an agent it cannot start.
    """
    by_route = {worker["route"]: worker["agent"] for worker in workers}
    swarm = [by_route[route] for route in SWARM_ROUTES if route in by_route]
    never = sorted(
        agent for route, agent in by_route.items() if route not in SWARM_ROUTES
    )
    strong = [
        by_route[route] for route in ("sol", "opus", "grok") if route in by_route
    ]
    if swarm:
        launch = (
            "For an automatic army, launch multiple "
            + " or ".join(f"exact {agent}" for agent in swarm)
            + " Agent calls with `run_in_background: true`. Start the useful "
            "non-overlapping batch before waiting so its cards overlap in Claude "
            "Code's native Agent UI, then collect every complete response before "
            "synthesis. Automatic high-volume swarms are limited to "
            + " and ".join(swarm)
            + " for independent, non-overlapping work with low cross-shard "
            "reasoning. They run at the session effort unless they are pinned."
        )
        armies = (
            "Automatic armies start the full background native Agent batch of "
            + " or ".join(swarm)
            + " workers before waiting. Shards must be independent, "
            "non-overlapping, high-volume work with low cross-shard reasoning."
        )
    else:
        launch = (
            "This session has no automatic swarm route enabled, so do not "
            "multiply any worker automatically. Fan out only across distinct "
            "roles, one Agent per role."
        )
        armies = (
            "No enabled route may be multiplied automatically in this session."
        )
    if never:
        forbid = f" Never automatically swarm {', '.join(never)}."
    else:
        forbid = ""
    if strong:
        synthesis = (
            " One stronger " + " or ".join(strong) + " Agent, or the root, "
            "reviews, integrates, tests, and synthesizes the full results."
        )
    else:
        synthesis = " The root reviews, integrates, tests, and synthesizes the full results."
    return {
        "launch": launch + forbid,
        "armies": armies + forbid + synthesis,
        "synthesis": synthesis,
    }


def root_orchestration_guidance(
    policy: dict[str, Any],
    workers: list[dict[str, str]],
    *,
    profile: str = "",
    openrouter_root_route: str | None = None,
) -> str:
    plan = swarm_plan(workers)
    # Only openrouter-pure maps every family alias to the selected root. A
    # hybrid OpenRouter root keeps normal wrapper-backed family slots, so it
    # must take the ordinary discovery wording below.
    pure_openrouter_root = (
        profile == "openrouter-pure" and openrouter_root_route is not None
    )
    recommended_discovery = (
        resolve_openrouter_route(policy, openrouter_root_route).model
        if pure_openrouter_root
        else discovery_model_from_workers(policy, workers)
    )
    if pure_openrouter_root:
        discovery_guidance = (
            "Built-in Explore, Plan, and general-purpose accept Claude Code's fable, opus, sonnet, and haiku family aliases; "
            f"Airlock resolves every alias to the exact selected OpenRouter root model {recommended_discovery}. "
            "Passing `model=haiku` selects that same root for bounded read-only discovery and does not claim a cheaper, "
            "smaller, or more capable route. No other OpenRouter route may enter a built-in alias. Named airlock-* Agents "
            "remain the exact-model interface for separately enabled routes. "
        )
    elif recommended_discovery:
        discovery_guidance = (
            "Built-in Explore, Plan, and general-purpose accept Claude Code's fable, opus, sonnet, and haiku family aliases; "
            "Airlock resolves every alias to an exact model enabled for this session. "
            f"For routine Plan Mode and bounded read-only discovery, pass `model=haiku` (resolved to {recommended_discovery}) "
            "instead of omitting the model and spending the orchestrator. Omit `model` only when inheriting the orchestrator "
            "is deliberate. Named airlock-* Agents remain the exact-model interface. "
        )
    else:
        discovery_guidance = (
            "This session has no non-confirmation discovery model, so do not choose one automatically; built-in "
            "Explore inherits the orchestrator unless the user authorizes an enabled named airlock-* Agent. "
        )
    return (
        "Orchestration: choose the smallest effective path. Before working directly on a multi-part request, split it "
        "by skill and route independent parts. A coupled final result does not make every "
        "phase coupled. Work directly only for small single-role work, inseparable edits, integration, or synthesis. "
        + discovery_guidance
        + "Use built-in Plan for read-only technical design after context and do not duplicate "
        "the same discovery in Explore and Plan. Use built-in general-purpose for multi-step work in the native runtime. Start one native "
        "airlock-* Agent for a separable task that benefits from its exact model. "
        + plan["launch"]
        + " Use Claude Code Workflow only when the user explicitly requests "
        "multi-agent orchestration. Do not overlap direct Agent fan-out with Workflow; any explicit top-level limit is an "
        "aggregate ceiling. Difficult implementation "
        "shards must have explicit file ownership, no-touch boundaries, and acceptance checks."
        + plan["synthesis"]
        + " Start with the fewest useful shards and "
        "expand only when coverage requires it. Do "
        "not swarm coupled edits, architecture, security judgment, cross-file integration, or final synthesis. An explicit "
        "user model choice always wins. Do not silently retry with a different provider or model. Named airlock-* "
        + agent_guidance_clause(policy)
        +         "The root owns the phase-level task list. Give each worker a natural, self-contained query with its goal, relevant "
        "paths, constraints, settled decisions, side-effect permissions, evidence requirements, and acceptance checks. Do "
        "not add task classifications, selection markers, or transport JSON. For public-web research include exact `Public "
        "web research authorized: yes` and never include repository content, local paths, credentials, tokens, untracked "
        "data, or private prompt content. Before assigning WebSearch, check effort compatibility: its internal search model "
        "can reject xhigh or max when search-side thinking is disabled. Run search discovery with an eligible worker at "
        "high or below, or give the design worker exact public URLs to read with WebFetch. Do not retry the same effort "
        "compatibility error."
    )


def worker_handoff_guidance(
    policy: dict[str, Any],
    profile: str,
    *,
    openrouter_root_route: str | None = None,
) -> str:
    providers = {
        worker["provider"]
        for worker in enabled_profile_workers(
            policy,
            profile,
            openrouter_root_route=openrouter_root_route,
        )
    }
    parts = [
        "Worker handoff wording: a worker sees only the query you send and returns only its final report, "
        "so name the goal, the paths, the constraints, and what the report must contain."
    ]
    if "openai" in providers:
        parts.append(
            "GPT workers read the action word literally. Answer, explain, review, and diagnose mean look and "
            "report without editing, and diagnose stops at the cause; use change, build, fix, or implement "
            "when the worker should edit files, and name the files it owns. In an isolated worktree, say the "
            "uncommitted changes are the starting point of the task, because a GPT worker otherwise treats "
            "unfamiliar edits as the user's own and works around them. Do not hand one a task whose main "
            "step is waiting on a long command."
        )
    if "anthropic" in providers:
        parts.append(
            "Claude workers plan first and work the problem out themselves, so send the question and the "
            "evidence rather than a conclusion to confirm, and name the browser or test evidence you want "
            "for a user-visible change."
        )
    return " ".join(parts)


def managed_result_guidance() -> str:
    return (
        "Native Agent results: use background execution only when work is independent and collect every complete result "
        "before synthesis. Claude Code's Agent card, model identity, usage, cancellation, tool activity, and worktree state "
        "are authoritative. A successful Agent call does not prove the task is semantically complete, so inspect its "
        "evidence, checks, caveats, uncertainties, and limitations before accepting or integrating it. Preserve the full "
        "technical result when handing it back to the root; do not replace it with a transport summary."
    )


def root_communication_guidance() -> str:
    return (
        "User communication: unless an exact-output or machine-readable contract forbids extra prose, state "
        "the immediate goal and approach before substantive tool use. During nontrivial work, update the user "
        "at meaningful phase changes, important discoveries, changed assumptions, blockers, required decisions, "
        "and major verification outcomes. Before a long foreground worker call, say what is being delegated and "
        "why. Do not narrate every read, search, command, heartbeat, or repetitive low-level action; a short task "
        "may need only the opening and final answer. The final answer must proportionately explain whether the "
        "request was completed, files and behavior changed or that no files changed, important decisions, checks "
        "and exact outcomes, skipped checks and reasons, limitations or residual risk, and next steps or explicitly "
        "none. Report failures and partial work plainly."
    )


def portfolio_guidance(
    policy: dict[str, Any],
    profile: str,
    *,
    openrouter_root_route: str | None = None,
) -> str:
    workers = enabled_profile_workers(
        policy,
        profile,
        openrouter_root_route=openrouter_root_route,
    )
    routing = policy["policies"]["routing"]
    mode = mode_state()
    configured_max = str(mode["effective_max_agents"])
    initial_breadth = {"economy": 1, "balanced": 2, "quality": 3}[routing]
    if configured_max != "off":
        initial_breadth = min(initial_breadth, int(configured_max))
    providers = sorted({worker["provider"] for worker in workers})
    headrooms = {provider: provider_headroom(policy, provider) for provider in providers}
    if routing == "quality" and headrooms and all(
        item["state"] in {"unknown", "stale", "low", "critical"}
        for item in headrooms.values()
    ):
        initial_breadth = min(initial_breadth, 2)
    role_map = "; ".join(
        f"{worker['agent']} ({worker['provider']}/{worker['cost']}): {worker['strength']}"
        for worker in workers
    ) or "none"
    headroom_parts = []
    for provider, headroom in headrooms.items():
        remaining = headroom["remaining_percent"]
        detail = f"{remaining:.2f}% remaining" if isinstance(remaining, float) else "remaining unknown"
        headroom_parts.append(
            f"{provider}={headroom['state']} ({detail}; source={headroom['source']})"
        )
    by_route = {worker["route"]: worker["agent"] for worker in workers}
    role_rules = []
    if by_route.get("sonnet"):
        role_rules.append(
            f"use {by_route['sonnet']} for deep repository research, requirements synthesis, broad review, documentation, design-system-aligned UI implementation, iterative frontend refinement, ambiguous debugging, and balanced implementation"
        )
    if by_route.get("opus"):
        role_rules.append(
            f"prefer {by_route['opus']} for difficult architecture, UI/UX design and visual direction, product-flow or design-system work, long-horizon planning, complex root-cause analysis, security reasoning, high-impact review, or final synthesis"
        )
    if by_route.get("sol"):
        role_rules.append(
            f"prefer {by_route['sol']} for difficult implementation, cross-file integration, backend and API work, test-driven repair, or measured performance work"
        )
    if by_route.get("terra"):
        role_rules.append(
            f"use {by_route['terra']} for adversarial review, independent second opinions, competing designs, or alternative debugging hypotheses"
        )
    if by_route.get("luna"):
        role_rules.append(
            f"use {by_route['luna']} for high-volume discovery, webpage reading, extraction, lookup, summarization, test or log triage, and small mechanical work"
        )
    if by_route.get("luna-fast"):
        role_rules.append(
            f"use {by_route['luna-fast']} only for eligible automatic native Agent Luna armies or an explicit Fast request"
        )
    if by_route.get("fable"):
        role_rules.append(
            f"keep {by_route['fable']} available for an explicit focused choice, but do not select it for automatic swarms"
        )
    if by_route.get("haiku"):
        role_rules.append(
            f"keep {by_route['haiku']} available for an explicit bounded utility choice, but do not select it for automatic swarms"
        )
    if by_route.get("grok"):
        role_rules.append(
            f"prefer {by_route['grok']} for long-horizon agentic work that must hold context across many "
            "tool calls, terminal and command-line heavy tasks, and multi-step debugging where the loop "
            "matters more than a single deep answer; it is not the default choice for novel architecture "
            "or security judgment"
        )
    if by_route.get("composer"):
        role_rules.append(
            f"use {by_route['composer']} for fast edit-run-check coding loops, automated debugging, and "
            "bounded multi-step implementation where latency matters more than depth; it is a coding "
            "worker, so do not route plain summarization to it when a cheaper discovery route is enabled"
        )
    if not by_route.get("sonnet"):
        role_rules.append("no enabled native deep-research specialist is available; do not imitate one with a large inherited workflow")
    ceiling = (
        "Claude Code native default" if configured_max == "off" else f"{configured_max} concurrent top-level workers"
    )
    # Fast eligibility is an OpenAI Luna concept, not a property of every swarm
    # route. A session without a Luna route gains nothing from the detail and
    # should not be told about a model it cannot call.
    if any(worker["route"] in {"luna", "luna-fast"} for worker in workers):
        fast = fast_route_status(policy)
        fast_detail = (
            f"Luna swarm Fast policy={fast['requested']}; selected route={fast['selected_route'] or 'none'}; "
            f"plan={fast['plan']}; proxy support={'verified' if fast['proxy_supported'] else 'unverified'}. {fast['reason']} "
        )
    else:
        fast_detail = ""
    return (
        f"Portfolio policy: configured ceiling={ceiling}; recommended initial breadth={initial_breadth} for {routing} routing. "
        "The ceiling is never a target. Start with the fewest orthogonal roles and expand only when useful. Native "
        "concurrency does not authorize wasteful fan-out. "
        f"Enabled role map: {role_map}. Provider headroom: {', '.join(headroom_parts) or 'none'}. {fast_detail}"
        "Unknown or stale headroom is conservative, never unlimited; avoid automatic fan-out on a provider with low or critical headroom. "
        + ("Role guidance: " + "; ".join(role_rules) + ". " if role_rules else "")
        + swarm_plan(workers)["armies"] + " "
        + "These are soft routing preferences, not provider stereotypes; an explicit user choice wins. Route by task evidence and the exact harness. Compare rendered results and accessibility for frontend work, require a reproducer and causal explanation for backend bugs, require before-and-after measurements for performance work, and require independent tools plus manual verification for security work. Separate planning, implementation, and review for architecture or large refactors. Use cross-provider review only when it adds an independent error mode and usage permits it. No model is a source of record: verify citations, APIs, tests, migrations, and production assumptions. "
        + "Choose roles before models and diversify providers only for distinct work or independent error modes, not to consume every enabled model."
    )


def routing_guidance(policy: dict[str, Any], mode: str) -> str:
    provider = MODE_PROVIDERS[mode]
    provider_state = policy["providers"][provider]
    plan = provider_state["detected_plan"]
    extra_policy = policy["policies"]["extra_usage"]
    routing = policy["policies"]["routing"]
    objective = ROUTING_OBJECTIVES[routing]
    visible = []
    efforts = []
    for route in PROVIDER_ROUTES[provider]:
        access = model_access(policy, provider, route)
        if access == "unavailable" or (access == "extra" and extra_policy == "never"):
            continue
        profile = MODEL_PROFILES[provider][route]
        visible.append(f"{route}={access}/{profile['capability']}/{profile['cost']}")
        efforts.append(f"{route}={policy['policies']['worker_effort'].get(route, INHERIT_EFFORT)}")
    return (
        f"Airlock user-selected native worker pool for {provider}: {', '.join(visible) or 'none enabled'}. "
        f"Routing preference: {routing}. {objective} This preference is advisory and never enables a disabled worker "
        f"or bypasses extra-usage policy. Login plan signal: {plan}; extra-usage policy: {extra_policy}. These labels "
        "are descriptive options, not vendor guarantees. The orchestrator may choose by task fit only among the enabled "
        "workers shown above. Named airlock-* Agents use exact models. Their effort settings are: "
        f"{', '.join(efforts) or 'none'}. A worker set to inherit follows the session level, so /effort changes the root "
        "and every inheriting worker together, including mid-session. A worker pinned to a named level keeps that level "
        "regardless of /effort. Built-in Explore, "
        "Plan, and general-purpose inherit the orchestrator model unless the Agent call supplies a schema-valid "
        "family alias owned by the active session. For an enabled extra worker under ask policy, include exact `Extra usage authorized: yes` "
        "only after confirmation; an explicit matching Agent request counts as confirmation. Send a natural, self-contained "
        "query without task classifications, selection markers, or a transport schema. The configured worker limit is a "
        "ceiling, not a fan-out target. When it is off, Claude Code's native default applies. Automatic high-volume armies "
        "use a useful background batch of exact Luna or eligible Luna Fast Agents, never Sol Fast or "
        "an Anthropic swarm. "
        + managed_result_guidance() + " "
        + root_communication_guidance()
    )


def provider_signal_summary(policy: dict[str, Any], provider: str) -> str:
    state = policy["providers"][provider]
    parts = [f"plan={state['detected_plan']}"]
    metadata = state.get("account_metadata", {})
    if isinstance(metadata, dict):
        if provider == "openai" and metadata.get("account_type"):
            parts.append(f"account={metadata['account_type']}")
        if provider == "anthropic":
            if metadata.get("billing_type"):
                parts.append(f"billing={metadata['billing_type']}")
            if isinstance(metadata.get("extra_usage_enabled"), bool):
                parts.append(f"extra-usage={'enabled' if metadata['extra_usage_enabled'] else 'disabled'}")
            if metadata.get("organization_rate_limit_tier"):
                parts.append(f"rate-tier={metadata['organization_rate_limit_tier']}")
    return "/".join(parts)


def profile_guidance(
    policy: dict[str, Any],
    profile: str,
    *,
    openrouter_root_route: str | None = None,
) -> str:
    if profile not in PROFILE_COMPONENTS:
        raise AccessError(f"unknown session profile: {profile}")
    routing = policy["policies"]["routing"]
    objective = ROUTING_OBJECTIVES[routing]
    enabled_workers = enabled_profile_workers(
        policy,
        profile,
        openrouter_root_route=openrouter_root_route,
    )
    workers = [
        f"{worker['route']}={worker['provider']}/native/{worker['access']}/"
        f"{worker['capability']}/{worker['cost']}"
        for worker in enabled_workers
    ]
    enabled_providers = {PROFILE_ROOT_PROVIDERS[profile]} | {
        worker["provider"] for worker in enabled_workers
    }
    signals = ", ".join(
        f"{provider}({provider_signal_summary(policy, provider)})"
        for provider in ("openai", "anthropic", "grok")
        if provider in enabled_providers and provider in policy.get("providers", {})
    )
    if "openrouter" in enabled_providers:
        if profile == "openrouter-pure":
            openrouter_signal = (
                "openrouter(registry=pinned/key=separate/root=explicitly-selected)"
            )
        elif profile == "hybrid-openrouter-root":
            openrouter_signal = (
                "openrouter(registry=pinned/key=separate/root=selected-hybrid-root/"
                "other-routes=extra)"
            )
        else:
            openrouter_signal = (
                "openrouter(registry=pinned/key=separate/usage=extra)"
            )
        signals = ", ".join(filter(None, (signals, openrouter_signal)))
    if profile == "openrouter-pure":
        selected = resolve_openrouter_route(policy, str(openrouter_root_route))
        extra_policy = policy["policies"]["extra_usage"]
        if extra_policy == "never":
            other_routes = "Other OpenRouter routes are omitted by the active extra-usage policy."
        elif extra_policy == "ask":
            other_routes = (
                "Other enabled OpenRouter Agents are separate extra-usage choices that require "
                "explicit authorization."
            )
        else:
            other_routes = (
                "Other enabled OpenRouter Agents are separate extra-usage choices allowed without "
                "per-call confirmation."
            )
        boundary = (
            "Provider boundary: this is an OpenRouter-only native session. The exact user-selected "
            f"route {selected.route} and model {selected.model} carry normal root traffic through the "
            f"single pinned endpoint provider {selected.endpoint_provider}. Its named Agent is the "
            "same explicitly selected route and is not marked extra in this session policy. "
            + other_routes
            + " Built-in Explore, Plan, and general-purpose may use only the selected root through "
            "their session-owned family aliases. No other OpenRouter route may enter a built-in alias. "
            "Do not invoke or claim an Anthropic, OpenAI Codex, or Grok route. Airlock does not infer "
            "OpenRouter model capability, best use, context window, availability, or relative cost."
        )
    elif profile == "openai-pure":
        boundary = (
            "Provider boundary: this is an OpenAI-only native session. The root, built-in Agents, and exact "
            "airlock-* Agents may use only enabled OpenAI model IDs. Do not invoke or claim an Anthropic or Grok route."
        )
    elif profile == "grok-pure":
        boundary = (
            "Provider boundary: this is a Grok-only native session through the local subscription proxy. The root, "
            "built-in Agents, and exact airlock-* Agents may use only enabled Grok model IDs. Do not invoke or claim "
            "an Anthropic or OpenAI Codex route."
        )
    else:
        root_provider = PROFILE_ROOT_PROVIDERS[profile]
        # Describe only the providers this session actually enabled. Naming a
        # route the user never logged into invites the orchestrator to attempt
        # it and fail at request time.
        proxy_providers = [
            name for name in ("openai", "grok") if name in enabled_providers
        ]
        routes = []
        if "anthropic" in enabled_providers:
            routes.append("Exact Claude IDs go only to Anthropic.")
        if proxy_providers == ["openai", "grok"]:
            routes.append(
                "Exact GPT and Grok IDs go only to the loopback subscription proxy "
                "(Codex OAuth for GPT; Grok OAuth for Grok)."
            )
        elif proxy_providers == ["openai"]:
            routes.append(
                "Exact GPT IDs go only to the loopback subscription proxy on Codex OAuth."
            )
        elif proxy_providers == ["grok"]:
            routes.append(
                "Exact Grok IDs go only to the loopback subscription proxy on Grok OAuth."
            )
        if "openrouter" in enabled_providers:
            routes.append(
                "Exact user-declared OpenRouter IDs go only to OpenRouter through the single "
                "endpoint provider pinned in the local registry. OpenRouter uses a separate key; "
                "Airlock does not infer these models' capability, context window, or relative cost."
            )
        if profile == "hybrid-openrouter-root":
            selected = resolve_openrouter_route(policy, str(openrouter_root_route))
            routes.append(
                f"The root itself is the user-selected OpenRouter route {selected.route} with model "
                f"{selected.model} through endpoint provider {selected.endpoint_provider}. The fable, opus, "
                "sonnet, and haiku family aliases stay wrapper backed and never carry an OpenRouter model; "
                "Claude Code's single custom model option carries the root."
            )
        excluded = [
            label
            for name, label in (
                ("anthropic", "Anthropic"),
                ("openai", "OpenAI Codex"),
                ("grok", "Grok"),
            )
            if name not in enabled_providers
        ]
        if excluded:
            routes.append(
                f"This session has no {' or '.join(excluded)} route enabled, so do not "
                "invoke or claim one."
            )
        boundary = (
            f"Provider boundary: this hybrid root starts on {root_provider}. One session-scoped loopback router keeps "
            "every enabled provider inside the same Claude Code process. "
            + " ".join(routes)
            + " Named airlock-* Agents use their exact model. Built-in Explore, Plan, and general-purpose inherit the "
            "orchestrator model unless a schema-valid family alias owned by this profile is supplied."
        )
    metadata = (
        "Native Agent handoff: send the selected worker one natural, self-contained query. Do not add task kind, risk, "
        "or selection markers, and do not request transport JSON. Named airlock-* Agents already bind their exact model; "
        "do not pass a caller model override. After required confirmation, include exact `Extra usage authorized: yes`. "
        "Repository tasks may reference tracked and eligible non-ignored untracked regular files. Never include credentials, "
        "token material, raw sensitive values, ignored or unsafe paths, or files outside the repository."
    )
    failure_policy = (
        "Failure policy: never silently retry on a different provider or model. If failover is disabled, stop. If it is "
        "set to ask, request confirmation before changing the route. Never fail over away from a worktree with partial edits."
    )
    skill_policy = (
        "The claude-api skill is blocked by default because its large attachment can overflow an Agent context during "
        "model routing. Native routing through Airlock is not Claude API application development; do not retry "
        "that skill unless the session was launched with AIRLOCK_ALLOW_CLAUDE_API_SKILL=1."
    )
    return "\n\n".join((
        (
            f"Airlock session profile: {profile}. Sanitized account signals: {signals}. "
            f"User-enabled workers: {', '.join(workers) or 'none'}. Capability, limitation, access, and "
            "usage labels are descriptive routing information, not vendor benchmarks. Choose only among "
            "enabled workers; never substitute a disabled model. "
            f"Extra-usage policy: {policy['policies']['extra_usage']}; failover policy: "
            f"{policy['policies']['failover']}; routing preference: {routing}. {objective} The routing "
            "preference is advisory and never overrides explicit choices, access, or confirmation."
        ),
        boundary,
        metadata,
        failure_policy,
        portfolio_guidance(
            policy,
            profile,
            openrouter_root_route=openrouter_root_route,
        ),
        ui_ux_guidance(policy, profile, enabled_workers),
        root_orchestration_guidance(
            policy,
            enabled_workers,
            profile=profile,
            openrouter_root_route=openrouter_root_route,
        ),
        worker_handoff_guidance(
            policy,
            profile,
            openrouter_root_route=openrouter_root_route,
        ),
        managed_result_guidance(),
        root_communication_guidance(),
        skill_policy,
    ))


def render_provider_agents(
    policy: dict[str, Any], provider: str, agents: dict[str, Any]
) -> dict[str, Any]:
    extra_policy = policy["policies"]["extra_usage"]
    rendered: dict[str, Any] = {}
    for route in PROVIDER_ROUTES[provider]:
        profile = MODEL_PROFILES[provider][route]
        name = profile["agent"]
        entry = agents.get(name)
        if not isinstance(entry, dict):
            continue
        access = model_access(policy, provider, route)
        if access == "unavailable" or (access == "extra" and extra_policy == "never"):
            continue
        copy = dict(entry)
        disallowed = copy.get("disallowedTools")
        if (
            copy.get("model") != profile["model"]
            or copy.get("effort") != profile["effort"]
            or "tools" in copy
            or "permissionMode" in copy
            or disallowed != ["Agent"]
        ):
            raise AccessError(f"agent catalog entry is not an exact-model native Agent: {name}")
        # The catalog records the pinned default so the shipped file stays exact
        # and hash-checkable. Dropping the key here is what lets a worker inherit
        # the session level, which is how /effort reaches workers mid-session.
        if policy["policies"].get("agent_depth") == "2":
            copy.pop("disallowedTools", None)
            copy["tools"] = ["*"]
            prompt = copy.get("prompt")
            if isinstance(prompt, str):
                copy["prompt"] = prompt.replace(
                    "Do not invoke another Agent.", NESTED_AGENT_RULE
                )
        worker_effort = policy["policies"]["worker_effort"].get(route, INHERIT_EFFORT)
        if worker_effort == INHERIT_EFFORT:
            copy.pop("effort", None)
            effort_note = "native effort: inherits the session level, so /effort moves it"
        else:
            copy["effort"] = worker_effort
            effort_note = f"pinned native effort: {worker_effort}"
        description = copy.get("description")
        prompt = copy.get("prompt")
        if not isinstance(description, str) or not description.strip():
            raise AccessError(f"agent catalog entry has no description: {name}")
        if not isinstance(prompt, str) or not prompt.strip():
            raise AccessError(f"agent catalog entry has no native worker prompt: {name}")
        if any(
            forbidden in prompt
            for forbidden in (
                "airlock-delegate",
                "airlock-workflow",
                "transport wrapper",
                "request file",
            )
        ):
            raise AccessError(f"agent catalog entry still contains legacy transport instructions: {name}")
        confirmation = (
            " Automatic use requires explicit extra-usage confirmation."
            if access == "extra" and extra_policy == "ask"
            else ""
        )
        invocation = (
            f" Invoke this worker through its exact session-scoped Agent name, {name}; "
            "do not substitute a built-in Agent or pass a model override. It runs inside Claude Code's "
            "native Agent lifecycle with native tools, background execution, cancellation, task cards, "
            "usage reporting, and optional worktree isolation."
        )
        if provider == "openai" and route in {"luna", "luna-fast"}:
            invocation += (
                " For an automatic Luna army, use run_in_background=true for every independent call, "
                "start the useful non-overlapping batch before waiting, and collect every complete result "
                "for stronger-model review and synthesis."
            )
        copy["description"] = (
            f"{description} Access: {access}; exact model: {profile['model']}; exact route: {route}; "
            f"{effort_note}; capability class: {profile['capability']}; "
            f"relative usage class: {profile['cost']}; transport: native.{confirmation}{invocation}"
        ).strip()
        rendered[name] = copy
    return rendered


def render_agents(policy: dict[str, Any], mode: str, agents: dict[str, Any]) -> dict[str, Any]:
    return render_provider_agents(policy, MODE_PROVIDERS[mode], agents)


def read_agent_catalog(path: Path, catalog_name: str) -> dict[str, Any]:
    if catalog_name not in CATALOG_EXPECTED_AGENTS:
        raise AccessError(f"unknown agent catalog: {catalog_name}")
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_AGENT_CATALOG_BYTES:
            raise AccessError(f"agent catalog is missing, unsafe, or too large: {catalog_name}")
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AccessError(f"agent catalog is invalid JSON: {catalog_name}") from exc
    expected = CATALOG_EXPECTED_AGENTS[catalog_name]
    if not isinstance(payload, dict):
        raise AccessError(f"agent catalog has unexpected definitions: {catalog_name}")
    # Underscore keys carry file metadata such as the managed marker, never an
    # Agent definition, so they are dropped before the exact-agent-set check.
    definitions = {
        key: value for key, value in payload.items() if not key.startswith("_")
    }
    if set(definitions) != expected:
        raise AccessError(f"agent catalog has unexpected definitions: {catalog_name}")
    return definitions


def render_openrouter_agents(
    policy: dict[str, Any],
    profile: str,
    *,
    openrouter_root_route: str | None = None,
) -> dict[str, Any]:
    rendered: dict[str, Any] = {}
    requires_confirmation = policy["policies"]["extra_usage"] == "ask"
    for worker in enabled_openrouter_workers(
        policy,
        profile,
        openrouter_root_route=openrouter_root_route,
    ):
        selected_root = worker["access"] == "included"
        if selected_root and profile == "openrouter-pure":
            usage_sentence = (
                "This is the explicitly selected route for normal root traffic; its named "
                "Agent uses the same route without extra-usage gating."
            )
        elif selected_root:
            usage_sentence = (
                "This is the explicitly selected route and this session's hybrid root model; its "
                "named Agent uses the same route without extra-usage gating, while the wrapper-backed "
                "family aliases never resolve to an OpenRouter model."
            )
        elif requires_confirmation:
            usage_sentence = (
                "This is an additional OpenRouter route. Under the current policy, use it "
                "only after explicit extra-usage authorization."
            )
        else:
            usage_sentence = (
                "This is an additional OpenRouter route. The current saved policy allows "
                "extra usage without per-call confirmation."
            )
        name = worker["agent"]
        model = worker["model"]
        endpoint = worker["endpoint_provider"]
        preset = OPENROUTER_PRESETS.preset_by_model(model)
        preset_sentence = ""
        if (
            preset is not None
            and worker["endpoint_provider"] == preset.endpoint_provider
            and worker["provider_name"] == preset.provider_name
            and worker["provider_slug"] == preset.provider_slug
            and worker["quantization"] == preset.quantization
            and worker["canonical_slug"] == preset.canonical_slug
        ):
            preset_sentence = (
                f" Community-derived guidance (unverified, researched "
                f"{preset.evidence_date}) suggests {preset.suggested_use}; "
                f"reported tradeoffs: {preset.tradeoffs}."
            )
        rendered[name] = {
            "description": (
                f"User-declared OpenRouter worker for exact model {model} through pinned "
                f"endpoint {endpoint}. Airlock does not verify its capability, context window, "
                f"best use, availability, or relative cost. {usage_sentence}"
                f"{preset_sentence}"
            ),
            "prompt": (
                "Complete the assigned task with the available tools and return the full technical "
                "result. This is a user-declared OpenRouter route. Do not infer provider guarantees, "
                "change model or endpoint, invoke Agent, expose credentials, or broaden the task."
            ),
            "model": model,
        }
        if policy["policies"].get("agent_depth") == "2":
            rendered[name]["tools"] = ["*"]
            rendered[name]["prompt"] = rendered[name]["prompt"].replace(
                "change model or endpoint, invoke Agent, expose credentials",
                "change model or endpoint, expose credentials",
            ) + " " + NESTED_AGENT_RULE
        else:
            rendered[name]["disallowedTools"] = ["Agent"]
    return rendered


def render_profile(
    policy: dict[str, Any],
    profile: str,
    catalogs: dict[str, dict[str, Any]],
    *,
    openrouter_root_route: str | None = None,
) -> dict[str, Any]:
    components = PROFILE_COMPONENTS.get(profile)
    if components is None:
        raise AccessError(f"unknown session profile: {profile}")
    _validate_openrouter_root_route(policy, profile, openrouter_root_route)
    rendered: dict[str, Any] = {}
    for provider, catalog_name in components:
        catalog = catalogs.get(catalog_name)
        if not isinstance(catalog, dict):
            raise AccessError(f"missing agent catalog: {catalog_name}")
        component = render_provider_agents(policy, provider, catalog)
        duplicates = set(rendered).intersection(component)
        if duplicates:
            raise AccessError(f"duplicate agent definitions: {', '.join(sorted(duplicates))}")
        rendered.update(component)
    openrouter_agents = render_openrouter_agents(
        policy,
        profile,
        openrouter_root_route=openrouter_root_route,
    )
    duplicates = set(rendered).intersection(openrouter_agents)
    if duplicates:
        raise AccessError(f"duplicate agent definitions: {', '.join(sorted(duplicates))}")
    rendered.update(openrouter_agents)
    if not rendered:
        raise AccessError("user policy disables every worker in this session profile")
    serialized = json.dumps(rendered, separators=(",", ":"), ensure_ascii=True)
    if len(serialized.encode("utf-8")) > MAX_RENDERED_AGENTS_BYTES:
        raise AccessError("rendered agent profile exceeds the safe command-line limit")
    return rendered


def managed_agent_name_set(policy: dict[str, Any] | None = None) -> set[str]:
    names = set(MANAGED_AGENT_NAMES)
    if policy is not None:
        registry = policy.get("_openrouter_registry")
        entries = getattr(registry, "models", ())
        names.update(entry.agent_name for entry in entries if entry.enabled)
    return names


def managed_agent_names(
    rendered_profile: object, policy: dict[str, Any] | None = None
) -> list[str]:
    if not isinstance(rendered_profile, dict) or not rendered_profile:
        raise AccessError("rendered agent profile is empty or invalid")
    names = sorted(rendered_profile)
    expected_names = managed_agent_name_set(policy)
    if any(
        not isinstance(name, str)
        or name not in expected_names
        or not isinstance(rendered_profile[name], dict)
        for name in names
    ):
        raise AccessError("rendered agent profile contains an unexpected worker")
    return names


def managed_agent_names_json(
    serialized: str, policy: dict[str, Any] | None = None
) -> list[str]:
    if not isinstance(serialized, str) or len(serialized.encode("utf-8")) > MAX_RENDERED_AGENTS_BYTES:
        raise AccessError("rendered agent profile is missing or too large")
    try:
        rendered = json.loads(serialized)
    except json.JSONDecodeError as exc:
        raise AccessError("rendered agent profile is invalid JSON") from exc
    return managed_agent_names(rendered, policy)


def managed_session_settings_json(
    serialized: str,
    fast_mode: str = "inherit",
    policy: dict[str, Any] | None = None,
) -> str:
    if fast_mode not in {"inherit", "on", "off"}:
        raise AccessError("managed session Fast mode must be inherit, on, or off")
    names = managed_agent_names_json(serialized, policy)
    enabled = set(names)
    services: list[str] = []
    if enabled.intersection(OPENAI_MANAGED_AGENT_NAMES):
        services.append(
            "OpenAI models reached through Claude Code's native Agent runtime and the active loopback OpenAI route"
        )
    if enabled.intersection(ANTHROPIC_MANAGED_AGENT_NAMES):
        services.append(
            "Anthropic Claude models reached through Claude Code's native Agent runtime and the official Anthropic route"
        )
    if enabled.intersection(GROK_MANAGED_AGENT_NAMES):
        services.append(
            "Grok models reached through Claude Code's native Agent runtime and the active loopback subscription proxy"
        )
    if any(name.startswith("airlock-or-") for name in enabled):
        services.append(
            "user-declared OpenRouter models reached through Claude Code's native Agent runtime, the active loopback router, and exact pinned OpenRouter endpoints"
        )
    if not services:
        raise AccessError("rendered Agent profile has no native provider")
    context = (
        "Key internal services: "
        + "; ".join(services)
        + " are trusted processors for tracked files and eligible non-ignored untracked regular files from the current "
        "working Git repository only when invoked through these exact native Agents: "
        + ", ".join(names)
        + ". The current session root provider and built-in Explore, Plan, and general-purpose Agent types are "
        "trusted processors for the same repository scope. Built-ins inherit the orchestrator model unless a "
        "schema-valid family alias owned by the active session is supplied. Credentials, OAuth or token material, "
        "raw sensitive values, Git-ignored or unsafe paths, files outside the working repository, unknown or bare "
        "Agent names, and every other external destination remain outside the trusted boundary."
    )
    settings: dict[str, object] = {"autoMode": {"environment": ["$defaults", context]}}
    if fast_mode != "inherit":
        settings["fastMode"] = fast_mode == "on"
    return json.dumps(settings, separators=(",", ":"), ensure_ascii=True)


def render_profile_from_paths(
    policy: dict[str, Any],
    profile: str,
    catalog_paths: dict[str, object],
    *,
    openrouter_root_route: str | None = None,
) -> dict[str, Any]:
    components = PROFILE_COMPONENTS.get(profile)
    if components is None:
        raise AccessError(f"unknown session profile: {profile}")
    required = {name for _, name in components}
    catalogs: dict[str, dict[str, Any]] = {}
    for catalog_name in required:
        raw_path = catalog_paths.get(catalog_name)
        if not isinstance(raw_path, str) or not raw_path:
            raise AccessError(f"missing agent catalog path: {catalog_name}")
        catalogs[catalog_name] = read_agent_catalog(Path(raw_path).expanduser(), catalog_name)
    return render_profile(
        policy,
        profile,
        catalogs,
        openrouter_root_route=openrouter_root_route,
    )


def status_lines(policy: dict[str, Any]) -> list[str]:
    lines = [
        f"Routing policy: {policy['policies']['routing']}",
        f"Extra usage: {policy['policies']['extra_usage']}",
        f"OpenAI Fast routes: {policy['policies']['openai_fast']}",
        f"Anthropic Fast startup: {policy['policies']['anthropic_fast']}",
        f"Luna swarm Fast selection: {policy['policies']['swarm_fast']}",
        f"Failover: {policy['policies']['failover']}",
        agent_depth_note(policy["policies"]["agent_depth"]),
    ]
    for provider in ("anthropic", "openai"):
        state = policy["providers"][provider]
        auth = "authenticated" if state["authenticated"] is True else ("not authenticated" if state["authenticated"] is False else "not checked")
        label = "Anthropic" if provider == "anthropic" else "OpenAI"
        lines.append(f"{label}: {auth}; plan: {state['detected_plan']}")
        metadata = state.get("account_metadata", {})
        if provider == "anthropic" and isinstance(metadata, dict):
            details = []
            if metadata.get("billing_type"):
                details.append(f"billing={metadata['billing_type']}")
            if isinstance(metadata.get("extra_usage_enabled"), bool):
                details.append(f"extra-usage={'enabled' if metadata['extra_usage_enabled'] else 'disabled'}")
            if metadata.get("organization_rate_limit_tier"):
                details.append(f"rate-tier={metadata['organization_rate_limit_tier']}")
            if metadata.get("seat_tier"):
                details.append(f"seat-tier={metadata['seat_tier']}")
            if details:
                lines.append(f"  Sanitized account signals: {', '.join(details)}")
        elif provider == "openai" and isinstance(metadata, dict) and metadata.get("account_type"):
            lines.append(f"  Account type: {metadata['account_type']}")
        models = ", ".join(
            f"{route}={model_access(policy, provider, route)}/{MODEL_PROFILES[provider][route]['capability']}/{MODEL_PROFILES[provider][route]['cost']}"
            for route in PROVIDER_ROUTES[provider]
        )
        lines.append(f"  Models (access/capability/usage): {models}")
    fast = fast_route_status(policy)
    lines.append(
        f"Luna swarm route: {fast['selected_route'] or 'blocked'}; plan={fast['plan']}; "
        f"proxy-fast={'verified' if fast['proxy_supported'] else 'unverified'}; {fast['reason']}"
    )
    lines.append("Worker availability comes only from the configured user pool; account signals never change it.")
    if all(policy["providers"][provider]["detected_plan"] == "unknown" for provider in PROVIDER_ROUTES):
        lines.append("Exact plan tiers are not present in the available sanitized account metadata; no tier was guessed.")
    return lines


def _managed_json(path: Path, *, maximum: int, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise AccessError(f"{label} is missing or unsafe: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise AccessError(f"cannot inspect {label}: {path}") from exc
    if size <= 0 or size > maximum:
        raise AccessError(f"{label} is empty or too large: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AccessError(f"{label} is invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise AccessError(f"{label} must contain a JSON object: {path}")
    return value


def _component_map(specifications: list[str]) -> dict[str, Path]:
    components: dict[str, Path] = {}
    for specification in specifications:
        key, separator, raw_path = specification.partition("=")
        if (
            not separator
            or not key
            or not raw_path
            or key in components
            or key.startswith("/")
            or "\\" in key
            or any(part in {"", ".", ".."} for part in key.split("/"))
        ):
            raise AccessError("managed component mappings must be unique key=path values")
        components[key] = Path(raw_path)
    return components


def validate_managed_bundle(
    bundle_path: Path,
    platform: str,
    component_specifications: list[str],
) -> dict[str, Any]:
    bundle = _managed_json(
        bundle_path, maximum=MAX_MANAGED_BUNDLE_BYTES, label="managed bundle marker"
    )
    expected_fields = {
        "schema_version", "bundle_version", "protocol_version", "managed_by",
        "components", "platforms",
    }
    if set(bundle) != expected_fields:
        raise AccessError("managed bundle marker has unexpected fields")
    if bundle.get("schema_version") != MANAGED_BUNDLE_SCHEMA_VERSION:
        raise AccessError("managed bundle schema is unsupported")
    if bundle.get("bundle_version") != MANAGED_BUNDLE_VERSION:
        raise AccessError(
            f"managed bundle is stale (expected {MANAGED_BUNDLE_VERSION})"
        )
    if bundle.get("protocol_version") != MANAGED_PROTOCOL_VERSION:
        raise AccessError(
            f"managed delegation protocol is stale (expected {MANAGED_PROTOCOL_VERSION})"
        )
    if bundle.get("managed_by") != "Managed by https://github.com/Harshkamdar67/Airlock":
        raise AccessError("managed bundle marker has an unknown owner")

    digests = bundle.get("components")
    platforms = bundle.get("platforms")
    if not isinstance(digests, dict) or not isinstance(platforms, dict):
        raise AccessError("managed bundle component metadata is invalid")
    if set(platforms) != {"common", "posix", "windows"} or platform not in {"posix", "windows"}:
        raise AccessError("managed bundle platform metadata is invalid")
    lists: dict[str, list[str]] = {}
    for name in ("common", "posix", "windows"):
        value = platforms.get(name)
        if (
            not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)
            or len(value) != len(set(value))
        ):
            raise AccessError("managed bundle platform component list is invalid")
        lists[name] = value
    all_names = lists["common"] + lists["posix"] + lists["windows"]
    if len(all_names) != len(set(all_names)) or set(digests) != set(all_names):
        raise AccessError("managed bundle component inventory is inconsistent")
    for key, digest in digests.items():
        if not re.fullmatch(r"[0-9a-f]{64}", digest if isinstance(digest, str) else ""):
            raise AccessError(f"managed bundle digest is invalid for {key}")

    expected = set(lists["common"] + lists[platform])
    supplied = _component_map(component_specifications)
    if set(supplied) != expected:
        missing = sorted(expected - set(supplied))
        unexpected = sorted(set(supplied) - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise AccessError("managed bundle component mapping mismatch: " + "; ".join(details))

    for key in sorted(expected):
        path = supplied[key]
        if path.is_symlink() or not path.is_file():
            raise AccessError(f"managed component is missing or unsafe: {key}")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise AccessError(f"cannot inspect managed component: {key}") from exc
        if size <= 0 or size > MAX_MANAGED_COMPONENT_BYTES:
            raise AccessError(f"managed component is empty or too large: {key}")
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
        except OSError as exc:
            raise AccessError(f"cannot read managed component: {key}") from exc
        if digest.hexdigest() != digests[key]:
            raise AccessError(f"managed component is stale or changed: {key}")
    return {
        "bundle_version": MANAGED_BUNDLE_VERSION,
        "protocol_version": MANAGED_PROTOCOL_VERSION,
        "platform": platform,
        "components_checked": len(expected),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Show or refresh credential-free Airlock access policy")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("show")
    subparsers.add_parser("refresh")
    mode_parser = subparsers.add_parser("mode")
    mode_parser.add_argument("mode_action", nargs="?")
    mode_parser.add_argument("mode_value", nargs="?")
    mode_parser.add_argument("--routing", choices=sorted(VALID_ROUTING_POLICIES))
    mode_parser.add_argument("--extra-usage", choices=sorted(VALID_EXTRA_POLICIES))
    mode_parser.add_argument("--max-agents")
    mode_parser.add_argument("--openai-fast", choices=sorted(VALID_PROVIDER_FAST_POLICIES))
    mode_parser.add_argument("--anthropic-fast", choices=sorted(VALID_PROVIDER_FAST_POLICIES))
    mode_parser.add_argument("--swarm-fast", choices=sorted(VALID_SWARM_FAST_POLICIES))
    mode_parser.add_argument("--failover", choices=sorted(VALID_FAILOVER_POLICIES))
    mode_parser.add_argument("--descendants", choices=sorted(VALID_DESCENDANT_POLICIES))
    mode_parser.add_argument("--max-descendants")
    mode_parser.add_argument("--max-total-descendants")
    mode_parser.add_argument("--repair-rounds")
    usage_parser = subparsers.add_parser("usage")
    usage_parser.add_argument("usage_action", nargs="?")
    usage_parser.add_argument("--claude-plan", choices=sorted(VALID_CLAUDE_PLANS))
    usage_parser.add_argument("--openai-capacity", choices=sorted(VALID_OPENAI_CAPACITIES))
    session_usage_parser = subparsers.add_parser("session-usage")
    session_usage_parser.add_argument("--router-url", required=True)
    session_usage_parser.add_argument("--json", action="store_true")
    recommend_parser = subparsers.add_parser("recommend-effort")
    recommend_parser.add_argument("--provider", choices=sorted(PROVIDER_ROUTES), required=True)
    recommend_parser.add_argument("--risk", choices=VALID_RISKS, required=True)
    recommend_parser.add_argument("--effort", choices=VALID_EFFORTS)
    capacity_parser = subparsers.add_parser("capacity-tier")
    capacity_parser.add_argument("provider", choices=sorted(PROVIDER_ROUTES))
    fast_check = subparsers.add_parser("fast-check")
    fast_check.add_argument("--route", choices=("sol-fast", "luna-fast"), required=True)
    fast_check.add_argument("--ephemeral", action="store_true")
    fast_check.add_argument("--quiet", action="store_true")
    transition_create = subparsers.add_parser("fast-transition-create")
    transition_create.add_argument("--launcher-pid", required=True)
    transition_create.add_argument("--cwd", required=True)
    transition_arm = subparsers.add_parser("fast-transition-arm")
    transition_arm.add_argument("--session-id", required=True)
    transition_arm.add_argument("--channel")
    transition_arm.add_argument("--nonce")
    transition_finalize = subparsers.add_parser("fast-transition-finalize")
    transition_finalize.add_argument("--channel")
    transition_finalize.add_argument("--nonce")
    transition_consume = subparsers.add_parser("fast-transition-consume")
    transition_consume.add_argument("--launcher-pid", required=True)
    transition_consume.add_argument("--cwd", required=True)
    transition_consume.add_argument("--channel")
    transition_consume.add_argument("--nonce")
    transition_consume.add_argument("--if-ready", action="store_true")
    transition_cleanup = subparsers.add_parser("fast-transition-cleanup")
    transition_cleanup.add_argument("--launcher-pid", required=True)
    transition_cleanup.add_argument("--cwd", required=True)
    transition_cleanup.add_argument("--channel")
    transition_cleanup.add_argument("--nonce")
    render = subparsers.add_parser("render-agents")
    render.add_argument("--mode", choices=sorted(MODE_PROVIDERS), required=True)
    render.add_argument("--agents-file", type=Path, required=True)
    guidance = subparsers.add_parser("guidance")
    guidance.add_argument("--mode", choices=sorted(MODE_PROVIDERS), required=True)
    profile_render = subparsers.add_parser("render-profile")
    profile_render.add_argument("--profile", choices=sorted(PROFILE_COMPONENTS), required=True)
    profile_render.add_argument("--openrouter-root-route")
    profile_render.add_argument("--openai-direct-file")
    profile_render.add_argument("--anthropic-direct-file")
    profile_render.add_argument("--openai-wrappers-file")
    profile_render.add_argument("--anthropic-wrappers-file")
    profile_render.add_argument("--grok-direct-file")
    profile_render.add_argument("--grok-wrappers-file")
    agent_names = subparsers.add_parser("managed-agent-names")
    agent_names.add_argument("--agents-json", required=True)
    session_settings = subparsers.add_parser("managed-session-settings")
    session_settings.add_argument("--agents-json", required=True)
    session_settings.add_argument(
        "--fast-mode", choices=("inherit", "on", "off"), default="inherit"
    )
    session_routes = subparsers.add_parser("session-routes")
    session_routes.add_argument("--profile", choices=sorted(PROFILE_COMPONENTS), required=True)
    session_routes.add_argument("--openrouter-root-route")
    session_routes.add_argument(
        "--field",
        choices=("routes", "model-ids", "agent-names", "extra-model-ids", "extra-agent-names", "picker-models", "discovery-model"),
        default="routes",
    )
    session_snapshot = subparsers.add_parser("session-snapshot")
    session_snapshot.add_argument(
        "--profile", choices=sorted(PROFILE_COMPONENTS), required=True
    )
    session_snapshot.add_argument("--root-model", required=True)
    session_snapshot.add_argument("--openrouter-root-route")
    snapshot_delete = subparsers.add_parser("session-snapshot-delete")
    snapshot_delete.add_argument("--snapshot", type=Path, required=True)
    snapshot_delete.add_argument("--snapshot-sha256", required=True)
    profile_help = subparsers.add_parser("profile-guidance")
    profile_help.add_argument("--profile", choices=sorted(PROFILE_COMPONENTS), required=True)
    profile_help.add_argument("--openrouter-root-route")
    subparsers.add_parser("openrouter-routes")
    openrouter_resolve = subparsers.add_parser("openrouter-resolve")
    openrouter_resolve.add_argument("route")
    subparsers.add_parser("hybrid-default-root")
    bundle_check = subparsers.add_parser("bundle-check")
    bundle_check.add_argument("--bundle", type=Path, required=True)
    bundle_check.add_argument("--platform", choices=("posix", "windows"), required=True)
    bundle_check.add_argument("--component", action="append", default=[])
    bundle_check.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    try:
        if args.command == "bundle-check":
            result = validate_managed_bundle(
                args.bundle, args.platform, args.component
            )
            if not args.quiet:
                print(
                    "Managed bundle "
                    f"{result['bundle_version']} is current; "
                    f"protocol {result['protocol_version']}; "
                    f"{result['components_checked']} components checked."
                )
            return 0
        if args.command == "managed-agent-names":
            policy = load_policy()
            print("\n".join(managed_agent_names_json(args.agents_json, policy)))
            return 0
        if args.command == "managed-session-settings":
            policy = load_policy()
            print(managed_session_settings_json(
                args.agents_json, args.fast_mode, policy
            ))
            return 0
        if args.command == "refresh":
            policy = refresh_policy()
            print("Access status refreshed without reading credentials or making model requests.")
            print("\n".join(status_lines(policy)))
            return 0
        if args.command == "mode":
            updates = parse_mode_update(args)
            if updates is not None:
                write_flat_config_overrides(updates)
            print("\n".join(mode_status_lines(updated=updates is not None)))
            return 0
        if args.command == "usage":
            updates = parse_usage_update(args)
            updated = updates is not None
            if updates is not None:
                write_flat_config_overrides(updates)
            policy, refresh_attempted, refreshed = usage_policy_for_display(
                args.usage_action
            )
            refresh_failed = refresh_attempted and not refreshed
            if updated:
                print("Airlock usage preferences updated.")
            print("\n".join(usage_lines(policy, refresh_failed=refresh_failed)))
            return 0
        if args.command == "session-usage":
            report = session_usage_report(fetch_session_diagnostics(args.router_url))
            if args.json:
                print(json.dumps(report, separators=(",", ":"), ensure_ascii=True))
            else:
                print("\n".join(session_usage_lines(report)))
            return 0
        if args.command == "recommend-effort":
            policy = load_policy()
            recommendation = recommend_effort(
                policy, args.provider, args.risk, args.effort
            )
            print(json.dumps(recommendation, separators=(",", ":"), ensure_ascii=True))
            return 0
        if args.command == "capacity-tier":
            policy = load_policy()
            print(capacity_signal(policy, args.provider)["plan"])
            return 0
        if args.command == "fast-check":
            policy = load_policy()
            status = explicit_fast_status(
                policy, args.route, ephemeral=args.ephemeral
            )
            if not status["eligible"]:
                raise AccessError(str(status["reason"]))
            if not args.quiet:
                print(json.dumps(status, separators=(",", ":"), ensure_ascii=True))
            return 0
        if args.command == "fast-transition-create":
            metadata = fast_transition_create(args.launcher_pid, args.cwd)
            print(f"{metadata['channel']}\t{metadata['nonce']}")
            return 0
        if args.command == "fast-transition-arm":
            fast_transition_arm(args.session_id, args.channel, args.nonce)
            return 0
        if args.command == "fast-transition-finalize":
            payload = _read_fast_transition_finalize_input()
            fast_transition_finalize(payload, args.channel, args.nonce)
            return 0
        if args.command == "fast-transition-consume":
            session_id = fast_transition_consume(
                args.launcher_pid, args.cwd, args.channel, args.nonce,
                if_ready=args.if_ready,
            )
            if session_id is not None:
                print(session_id)
            return 0
        if args.command == "fast-transition-cleanup":
            fast_transition_cleanup(
                args.launcher_pid, args.cwd, args.channel, args.nonce
            )
            return 0
        if args.command == "session-snapshot-delete":
            delete_session_snapshot(args.snapshot, args.snapshot_sha256)
            return 0
        policy = load_or_refresh_policy()
        if args.command == "openrouter-routes":
            print(json.dumps(
                list_enabled_openrouter_routes(policy),
                separators=(",", ":"),
                ensure_ascii=True,
            ))
            return 0
        if args.command == "openrouter-resolve":
            print(json.dumps(
                _openrouter_route_fields(
                    resolve_openrouter_route(policy, args.route)
                ),
                separators=(",", ":"),
                ensure_ascii=True,
            ))
            return 0
        if args.command == "hybrid-default-root":
            print(default_hybrid_root(policy))
            return 0
        if args.command == "session-snapshot":
            snapshot_path, snapshot_digest = write_session_snapshot(
                policy,
                args.profile,
                args.root_model,
                openrouter_root_route=args.openrouter_root_route,
            )
            if any(character in str(snapshot_path) for character in "\r\n\t"):
                snapshot_path.unlink(missing_ok=True)
                raise AccessError("session runtime path contains a control character")
            print(f"{snapshot_path}\t{snapshot_digest}")
            return 0
        if args.command == "session-routes":
            print(session_route_field(
                policy,
                args.profile,
                args.field,
                openrouter_root_route=args.openrouter_root_route,
            ))
            return 0
        if args.command == "render-profile":
            catalog_paths = {
                "openai_direct": args.openai_direct_file,
                "anthropic_direct": args.anthropic_direct_file,
                "openai_wrappers": args.openai_wrappers_file,
                "anthropic_wrappers": args.anthropic_wrappers_file,
                "grok_direct": args.grok_direct_file,
                "grok_wrappers": args.grok_wrappers_file,
            }
            rendered_profile = render_profile_from_paths(
                policy,
                args.profile,
                catalog_paths,
                openrouter_root_route=args.openrouter_root_route,
            )
            print(json.dumps(rendered_profile, separators=(",", ":"), ensure_ascii=True))
            return 0
        if args.command == "profile-guidance":
            print(profile_guidance(
                policy,
                args.profile,
                openrouter_root_route=args.openrouter_root_route,
            ))
            return 0
        if args.command == "render-agents":
            target = args.agents_file
            if not target.is_file() or target.is_symlink() or target.stat().st_size > 24 * 1024:
                raise AccessError("agent configuration is missing, unsafe, or too large")
            try:
                agents = json.loads(target.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise AccessError("agent configuration is invalid JSON") from exc
            if not isinstance(agents, dict):
                raise AccessError("agent configuration must be an object")
            print(json.dumps(render_agents(policy, args.mode, agents), separators=(",", ":"), ensure_ascii=True))
            return 0
        if args.command == "guidance":
            print(routing_guidance(policy, args.mode))
            return 0
        print("\n".join(status_lines(policy)))
        return 0
    except AccessError as error:
        command_name = (
            args.command if args.command in {"mode", "usage", "session-usage"} else "access"
        )
        print(f"airlock {command_name}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
