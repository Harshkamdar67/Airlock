#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Fail-closed, task-blind validation for Airlock Agent tool calls."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys

MAX_EVENT_BYTES = 1024 * 1024
MAX_POLICY_HELPER_BYTES = 1024 * 1024
MANAGED_BUNDLE_VERSION = "2026.08.11.3"
MANAGED_PROTOCOL_VERSION = 5
EXTRA_USAGE_MARKER = "Extra usage authorized: yes"
BUILTIN_AGENT_TYPES = {"Explore", "Plan", "general-purpose"}
FAMILY_MODEL_VARIABLES = {
    "fable": "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
}


def load_policy_schema():
    raw_path = os.environ.get("AIRLOCK_POLICY_HELPER")
    if not raw_path:
        return None
    path = Path(raw_path)
    try:
        if (
            path.name != "airlock_policy.py"
            or path.is_symlink()
            or not path.is_file()
            or not 0 < path.stat().st_size <= MAX_POLICY_HELPER_BYTES
        ):
            return None
        spec = importlib.util.spec_from_file_location("airlock_guard_policy", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


def load_session_permission_set():
    schema = load_policy_schema()
    path = os.environ.get("AIRLOCK_SESSION_SNAPSHOT")
    digest = os.environ.get("AIRLOCK_SESSION_SNAPSHOT_SHA256")
    if schema is None or not path or not digest:
        return None
    try:
        snapshot = schema.load_session_snapshot(path, digest)
    except Exception:
        return None
    profile = os.environ.get("AIRLOCK_ACTIVE_PROFILE")
    root_model = os.environ.get("AIRLOCK_ROOT_MODEL")
    if (
        snapshot.protocol_version != MANAGED_PROTOCOL_VERSION
        or snapshot.profile != profile
        or snapshot.root_model != root_model
    ):
        return None
    allowed_agents = set(snapshot.agents)
    allowed_models = {agent.model for agent in snapshot.agents.values()}
    builtin_models = {
        model
        for model in allowed_models
        if snapshot.routes.get(model) != "openrouter"
    }
    if snapshot.profile == "openrouter-pure":
        root_agents = [
            agent
            for agent in snapshot.agents.values()
            if agent.provider == "openrouter"
            and agent.model == snapshot.root_model
            and not agent.extra_usage
        ]
        if (
            snapshot.root_provider != "openrouter"
            or snapshot.routes.get(snapshot.root_model) != "openrouter"
            or set(snapshot.routes.values()) != {"openrouter"}
            or any(agent.provider != "openrouter" for agent in snapshot.agents.values())
            or len(root_agents) != 1
        ):
            return None
        builtin_models = {snapshot.root_model}
    extra_agents = {
        name for name, agent in snapshot.agents.items() if agent.extra_usage
    }
    extra_models = {
        agent.model for agent in snapshot.agents.values() if agent.extra_usage
    }
    if not allowed_agents or not allowed_models:
        return None
    expected = {
        "AIRLOCK_ALLOWED_AGENT_NAMES": allowed_agents,
        "AIRLOCK_ALLOWED_AGENT_MODELS": allowed_models,
        "AIRLOCK_EXTRA_USAGE_AGENT_NAMES": extra_agents,
        "AIRLOCK_EXTRA_USAGE_AGENT_MODELS": extra_models,
    }
    for variable, values in expected.items():
        raw = os.environ.get(variable)
        configured = raw.split(",") if raw else []
        if (
            raw is None
            or len(configured) != len(set(configured))
            or set(configured) != values
        ):
            return None
    return (
        schema,
        snapshot,
        allowed_agents,
        allowed_models,
        builtin_models,
        extra_agents,
        extra_models,
    )


def configured_family_models(
    allowed_models: set[str], extra_models: set[str]
) -> dict[str, str] | None:
    family_models: dict[str, str] = {}
    for family, variable in FAMILY_MODEL_VARIABLES.items():
        model = os.environ.get(variable)
        if not model or model not in allowed_models or model in extra_models:
            return None
        family_models[family] = model
    return family_models


def has_extra_usage_marker(tool_input: dict[str, object]) -> bool:
    prompt = tool_input.get("prompt")
    return isinstance(prompt, str) and EXTRA_USAGE_MARKER in prompt


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, separators=(",", ":")))


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_EVENT_BYTES + 1)
    if len(raw) > MAX_EVENT_BYTES:
        deny("Airlock blocked an oversized Agent event.")
        return 0
    permission_set = load_session_permission_set()
    if permission_set is None:
        deny("Airlock blocked Agent because the active session permission set is invalid.")
        return 0
    (
        schema,
        snapshot,
        allowed_agents,
        allowed_models,
        builtin_models,
        extra_agents,
        extra_models,
    ) = permission_set
    try:
        event = schema.load_json_bytes(raw, max_bytes=MAX_EVENT_BYTES)
    except Exception:
        deny("Airlock blocked malformed Agent input.")
        return 0
    if not isinstance(event, dict) or event.get("tool_name") != "Agent":
        deny("Airlock Agent guard received an unexpected tool event.")
        return 0
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        deny("Airlock blocked malformed Agent tool input.")
        return 0
    subagent_type = tool_input.get("subagent_type")
    # A nested call carries the calling Agent's identity; a root call does not.
    # A worker may only fan out to its own type, which pins every descendant to
    # the model the root already chose for it. This holds whatever the depth
    # setting is: at depth 1 a worker has no Agent tool, so this never fires.
    caller_agent_type = event.get("agent_type")
    if caller_agent_type is not None:
        if not isinstance(caller_agent_type, str) or not caller_agent_type:
            deny("Airlock blocked Agent because the calling Agent identity is invalid.")
            return 0
        if subagent_type != caller_agent_type:
            deny(
                "Airlock allows a worker to spawn only its own Agent type, so every "
                "descendant keeps the model the root selected."
            )
            return 0
        if "model" in tool_input:
            deny("Airlock does not allow a model override on a nested Agent call.")
            return 0
    if subagent_type in BUILTIN_AGENT_TYPES:
        if snapshot.profile == "openrouter-pure" and (
            os.environ.get("AIRLOCK_DISCOVERY_MODEL") != snapshot.root_model
            or os.environ.get("ANTHROPIC_SMALL_FAST_MODEL") != snapshot.root_model
        ):
            deny("Airlock blocked Agent because the OpenRouter root model map is invalid.")
            return 0
        family_models = configured_family_models(builtin_models, extra_models)
        if family_models is None:
            deny("Airlock blocked Agent because the built-in family model map is invalid.")
            return 0
        if "model" not in tool_input:
            # The nudge names the model the caller actually receives, which is
            # whatever the Haiku family slot holds. Reading it from the slot
            # rather than from AIRLOCK_DISCOVERY_MODEL keeps the advice true by
            # construction: a profile that has Claude routes seats a Claude
            # model in that slot so Claude Code's own background work keeps
            # running, and that model is not always the discovery model.
            if os.environ.get("AIRLOCK_DISCOVERY_MODEL") is None:
                return 0
            haiku_model = family_models["haiku"]
            if (
                subagent_type == "Explore"
                and haiku_model
                and snapshot.root_model
                and haiku_model != snapshot.root_model
            ):
                deny(
                    "Airlock blocked unpinned Explore to avoid spending the orchestrator on routine discovery. "
                    f'Retry with model: "haiku" ({haiku_model}).'
                )
                return 0
            return 0
        model = tool_input.get("model")
        if not isinstance(model, str):
            deny(
                "Airlock allows built-in Agent model overrides only through a configured family alias."
            )
            return 0
        resolved_model = family_models.get(model, model)
        if resolved_model not in builtin_models:
            deny(
                "Airlock allows built-in Agent model overrides only through a configured family alias."
            )
            return 0
        if resolved_model in extra_models and not has_extra_usage_marker(tool_input):
            deny("Airlock requires explicit extra-usage confirmation for this model.")
            return 0
        return 0
    if not isinstance(subagent_type, str) or subagent_type not in allowed_agents:
        deny(
            "Airlock allows built-in Explore, Plan, and general-purpose plus enabled native airlock-* Agents only."
        )
        return 0
    if "model" in tool_input:
        deny(
            "Airlock blocks caller model overrides for named airlock-* Agents because their identity binds an exact model."
        )
        return 0
    if subagent_type in extra_agents and not has_extra_usage_marker(tool_input):
        deny("Airlock requires explicit extra-usage confirmation for this Agent.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
