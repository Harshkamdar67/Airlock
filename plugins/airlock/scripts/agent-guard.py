#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Fail-closed, task-blind validation for Airlock Agent tool calls."""

from __future__ import annotations

import json
import os
import sys

MAX_EVENT_BYTES = 1024 * 1024
MANAGED_BUNDLE_VERSION = "2026.08.09.1"
MANAGED_PROTOCOL_VERSION = 3
EXTRA_USAGE_MARKER = "Extra usage authorized: yes"
BUILTIN_AGENT_TYPES = {"Explore", "Plan", "general-purpose"}
FAMILY_MODEL_VARIABLES = {
    "fable": "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
}
GROK_AGENTS = {"airlock-grok", "airlock-composer"}
OPENAI_AGENTS = {"airlock-sol", "airlock-terra", "airlock-luna", "airlock-luna-fast"}
ANTHROPIC_AGENTS = {"airlock-opus", "airlock-sonnet", "airlock-fable", "airlock-haiku"}
PROFILE_AGENTS = {
    "openai-pure": set(OPENAI_AGENTS),
    "grok-pure": set(GROK_AGENTS),
    "hybrid-openai-root": OPENAI_AGENTS | ANTHROPIC_AGENTS | GROK_AGENTS,
    "hybrid-anthropic-root": OPENAI_AGENTS | ANTHROPIC_AGENTS | GROK_AGENTS,
    "hybrid-grok-root": OPENAI_AGENTS | ANTHROPIC_AGENTS | GROK_AGENTS,
}
OPENAI_MODELS = {
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.6-luna-fast",
}
# These have to be the exact IDs the launcher enables, because a value the
# launcher sends that is missing here invalidates the whole permission set and
# denies every Agent call. OpenAI worker IDs are bare because no OpenAI route
# has a recorded successful proof for a context larger than 300000 tokens.
# Opus 5, Sonnet 5, and Fable 5 carry the [1m] suffix so Claude Code keeps
# their native 1M window from behind the session router. Haiku 4.5 is a genuine
# 200000 token model and carries no suffix.
ANTHROPIC_MODELS = {
    "claude-opus-5[1m]",
    "claude-sonnet-5[1m]",
    "claude-fable-5[1m]",
    "claude-haiku-4-5-20251001",
}
GROK_MODELS = {
    "grok-4.5",
    "grok-composer-2.5-fast",
}
PROFILE_MODELS = {
    "openai-pure": OPENAI_MODELS,
    "grok-pure": GROK_MODELS,
    "hybrid-openai-root": OPENAI_MODELS | ANTHROPIC_MODELS | GROK_MODELS,
    "hybrid-anthropic-root": OPENAI_MODELS | ANTHROPIC_MODELS | GROK_MODELS,
    "hybrid-grok-root": OPENAI_MODELS | ANTHROPIC_MODELS | GROK_MODELS,
}


def configured_values(
    variable: str,
    permitted: set[str],
    *,
    required: bool,
) -> set[str] | None:
    if variable not in os.environ:
        return None if required else set()
    raw = os.environ.get(variable, "")
    values = raw.split(",") if raw else []
    if (
        (required and not values)
        or len(values) != len(set(values))
        or any(not value or value not in permitted for value in values)
    ):
        return None
    return set(values)


def configured_agents(profile: str | None) -> set[str] | None:
    profile_agents = PROFILE_AGENTS.get(profile)
    if profile_agents is None:
        return None
    configured = configured_values(
        "AIRLOCK_ALLOWED_AGENT_NAMES", profile_agents, required=False
    )
    return profile_agents if "AIRLOCK_ALLOWED_AGENT_NAMES" not in os.environ else configured


def configured_models(profile: str | None) -> set[str] | None:
    profile_models = PROFILE_MODELS.get(profile)
    if profile_models is None:
        return None
    return configured_values(
        "AIRLOCK_ALLOWED_AGENT_MODELS", profile_models, required=True
    )


def configured_extra_agents(profile: str | None) -> set[str] | None:
    profile_agents = PROFILE_AGENTS.get(profile)
    if profile_agents is None:
        return None
    return configured_values(
        "AIRLOCK_EXTRA_USAGE_AGENT_NAMES", profile_agents, required=False
    )


def configured_extra_models(profile: str | None) -> set[str] | None:
    profile_models = PROFILE_MODELS.get(profile)
    if profile_models is None:
        return None
    return configured_values(
        "AIRLOCK_EXTRA_USAGE_AGENT_MODELS", profile_models, required=False
    )


def configured_family_models(
    allowed_models: set[str] | None,
    extra_models: set[str] | None,
) -> dict[str, str] | None:
    if allowed_models is None or extra_models is None:
        return None
    family_models: dict[str, str] = {}
    for family, variable in FAMILY_MODEL_VARIABLES.items():
        model = os.environ.get(variable)
        if not model or model not in allowed_models or model in extra_models:
            return None
        family_models[family] = model
    discovery_model = os.environ.get("AIRLOCK_DISCOVERY_MODEL")
    if discovery_model and family_models["haiku"] != discovery_model:
        return None
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
    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        deny("Airlock blocked malformed Agent input.")
        return 0
    profile = os.environ.get("AIRLOCK_ACTIVE_PROFILE")
    allowed_agents = configured_agents(profile)
    extra_agents = configured_extra_agents(profile)
    extra_models = configured_extra_models(profile)
    if allowed_agents is None or extra_agents is None or extra_models is None:
        deny("Airlock blocked Agent because the active session permission set is invalid.")
        return 0
    if not isinstance(event, dict) or event.get("tool_name") != "Agent":
        deny("Airlock Agent guard received an unexpected tool event.")
        return 0
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        deny("Airlock blocked malformed Agent tool input.")
        return 0
    subagent_type = tool_input.get("subagent_type")
    if subagent_type in BUILTIN_AGENT_TYPES:
        allowed_models = configured_models(profile)
        family_models = configured_family_models(allowed_models, extra_models)
        if family_models is None:
            deny("Airlock blocked Agent because the built-in family model map is invalid.")
            return 0
        if "model" not in tool_input:
            discovery_model = os.environ.get("AIRLOCK_DISCOVERY_MODEL")
            root_model = os.environ.get("AIRLOCK_ROOT_MODEL")
            if discovery_model is None:
                return 0
            if (
                subagent_type == "Explore"
                and discovery_model
                and root_model
                and discovery_model != root_model
            ):
                deny(
                    "Airlock blocked unpinned Explore to avoid spending the orchestrator on routine discovery. "
                    f'Retry with model: "haiku" ({discovery_model}).'
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
        if allowed_models is None or resolved_model not in allowed_models:
            deny(
                "Airlock allows built-in Agent model overrides only through a configured family alias."
            )
            return 0
        if resolved_model in extra_models and not has_extra_usage_marker(tool_input):
            deny(
                "Airlock requires explicit extra-usage confirmation for this model."
            )
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
        deny(
            "Airlock requires explicit extra-usage confirmation for this Agent."
        )
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
