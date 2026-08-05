#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Fail-closed, task-blind validation for Airlock Agent tool calls."""

from __future__ import annotations

import json
import os
import sys

MAX_EVENT_BYTES = 1024 * 1024
MANAGED_BUNDLE_VERSION = "2026.08.05.5"
MANAGED_PROTOCOL_VERSION = 3
EXTRA_USAGE_MARKER = "Extra usage authorized: yes"
BUILTIN_AGENT_TYPES = {"Explore", "Plan", "general-purpose"}
PROFILE_AGENTS = {
    "openai-pure": {"airlock-sol", "airlock-terra", "airlock-luna", "airlock-luna-fast"},
    "hybrid-openai-root": {
        "airlock-sol", "airlock-terra", "airlock-luna", "airlock-luna-fast",
        "airlock-opus", "airlock-sonnet", "airlock-fable", "airlock-haiku",
    },
    "hybrid-anthropic-root": {
        "airlock-sol", "airlock-terra", "airlock-luna", "airlock-luna-fast",
        "airlock-opus", "airlock-sonnet", "airlock-fable", "airlock-haiku",
    },
}
OPENAI_MODELS = {
    "gpt-5.6-sol[1m]",
    "gpt-5.6-terra[1m]",
    "gpt-5.6-luna[1m]",
    "gpt-5.6-luna-fast[1m]",
}
ANTHROPIC_MODELS = {
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-haiku-4-5-20251001",
}
PROFILE_MODELS = {
    "openai-pure": OPENAI_MODELS,
    "hybrid-openai-root": OPENAI_MODELS | ANTHROPIC_MODELS,
    "hybrid-anthropic-root": OPENAI_MODELS | ANTHROPIC_MODELS,
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
        if "model" not in tool_input:
            return 0
        model = tool_input.get("model")
        allowed_models = configured_models(profile)
        if (
            allowed_models is None
            or not isinstance(model, str)
            or model not in allowed_models
        ):
            deny(
                "Airlock allows built-in Agent model overrides only for exact model IDs enabled in this session."
            )
            return 0
        if model in extra_models and not has_extra_usage_marker(tool_input):
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
