#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Shared Airlock Console agent tool contract.

Owns exact tool definitions, input validation, and result projection for the
Console HTTP tool API and the stdio MCP wrapper. Standard library only.
"""

from __future__ import annotations

from copy import deepcopy
import math
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

MAX_SESSION_ID_CHARS = 64
MAX_PROPOSAL_ID_CHARS = 128
MAX_MODEL_ID_CHARS = 200
MAX_KIND_CHARS = 64
MAX_STATUS_CHARS = 32
MIN_REASON_CHARS = 8
MAX_REASON_CHARS = 400
MAX_CHAIN_SOURCES = 64
MAX_CHAIN_PEERS = 8
MAX_ARRAY_ITEMS = 256
MAX_OBJECT_KEYS = 256
MAX_INPUT_DEPTH = 8
MAX_INPUT_NODES = 512
MAX_PROJECTED_STRING_CHARS = 4096
MAX_PROJECTED_ARRAY_ITEMS = 256
MAX_PROJECTED_OBJECT_KEYS = 128
MAX_EVENTS = 256
MAX_SESSIONS = 128
MAX_ROUTES = 128
MAX_PROPOSALS = 64
MAX_HEADROOM = 32
MAX_WORKERS = 64
MAX_COOLDOWNS = 64
MAX_HISTORY_SESSIONS = 500
MAX_SUBAGENTS = 128
MAX_ACTIVITY_ITEMS = 64
MAX_SERIES_ROWS = 400
MAX_RANK_ROWS = 64
MAX_QUERY_ROWS = 500
MAX_FILTER_CHARS = 120
MAX_COUNT_MAP_KEYS = 128

DEFAULT_CONSOLE_PORT = 4783
DEFAULT_CONSOLE_URL = f"http://127.0.0.1:{DEFAULT_CONSOLE_PORT}"
CONSOLE_ADDRESS_BASENAME = "console-address.json"


def default_console_runtime_root() -> Path:
    """Return the shared per-user runtime root used by Console clients."""

    if os.name == "nt":
        fallback = Path.home() / "AppData" / "Local"
        base = Path(os.environ.get("LOCALAPPDATA", fallback))
        return base / "Airlock"
    fallback = Path.home() / ".local" / "state"
    base = Path(os.environ.get("XDG_STATE_HOME", fallback))
    return base / "airlock"


def default_console_address_file() -> Path:
    """Return the default Console address marker path."""

    return default_console_runtime_root() / CONSOLE_ADDRESS_BASENAME


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
PROPOSAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\-\[\]]{0,199}$")
KIND_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,64}$")
HISTORY_ID_PATTERN = re.compile(r"^hx-[A-Za-z0-9._-]{1,12}$")
AGENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{4,64}$")
PROVIDER_PATTERN = re.compile(r"^[a-z0-9_-]{1,32}$")
# Kept identical to airlock_console_history.QUERY_DIMENSIONS / QUERY_MEASURES;
# tests/test-console.py compares the two.
QUERY_DIMENSIONS = (
    "session", "project", "model", "provider", "agent_type", "tool",
    "day", "week", "month", "weekday", "branch", "entrypoint",
)
QUERY_MEASURES = (
    "sessions", "prompts", "replies", "tool_calls", "output_tokens",
    "compactions", "agent_launches", "peak_context_max",
)
USAGE_GROUPS = ("day", "week", "month")
STATUS_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,32}$")
TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$"
)

PROPOSAL_KINDS = frozenset({"session_handoff", "chain_change"})
PROPOSAL_STATUSES = frozenset({
    "pending",
    "applying",
    "applied",
    "rejected",
    "expired",
    "superseded",
    "conflicted",
    "failed",
})
SESSION_STATES = frozenset({"running", "blocked", "idle", "ended"})
BLOCKED_REASONS = frozenset({
    "rate_limit",
    "provider_cooldown",
    "context_overflow",
    "chain_exhausted",
})
ROUTE_CATEGORIES = frozenset({"included", "extra", "metered", "unknown"})
ROUTE_STATUSES = frozenset({"ready", "cooling", "unavailable"})
COOLDOWN_SCOPES = frozenset({"model", "provider"})

FORBIDDEN_NAME_FRAGMENTS = (
    "token",
    "secret",
    "credential",
    "account",
    "csrf",
    "header",
    "body",
    "prompt",
    "response",
    "control",
    "nonce",
    "cookie",
    "url",
    "workdir",
)

HISTORY_TOOLS = (
    "airlock_list_history",
    "airlock_get_history_session",
    "airlock_list_subagents",
    "airlock_get_subagent_feed",
    "airlock_get_usage",
    "airlock_query_history",
)

READ_ONLY_TOOLS = frozenset({
    "airlock_list_sessions",
    "airlock_get_session",
    "airlock_get_session_events",
    "airlock_get_routes",
    "airlock_get_headroom",
    "airlock_get_global_failover_chain",
    "airlock_list_proposals",
    "airlock_get_proposal",
    *HISTORY_TOOLS,
})

# History results carry conversation titles, branch names, subagent
# descriptions, and activity previews written by models and people, so every
# history tool is untrusted content.
UNTRUSTED_CONTENT_TOOLS = frozenset({
    "airlock_get_session",
    "airlock_get_session_events",
    "airlock_list_proposals",
    "airlock_get_proposal",
    "airlock_propose_session_handoff",
    "airlock_propose_restore_root",
    "airlock_propose_chain_change",
    *HISTORY_TOOLS,
})

TOOL_NAMES = (
    "airlock_list_sessions",
    "airlock_get_session",
    "airlock_get_session_events",
    "airlock_get_routes",
    "airlock_get_headroom",
    "airlock_get_global_failover_chain",
    "airlock_list_proposals",
    "airlock_get_proposal",
    "airlock_propose_session_handoff",
    "airlock_propose_restore_root",
    "airlock_propose_chain_change",
    *HISTORY_TOOLS,
)


class ToolContractError(ValueError):
    """Safe tool-contract failure with a stable code and message."""

    def __init__(self, code: str, message: str) -> None:
        if not isinstance(code, str) or not code or len(code) > 64:
            code = "invalid_request"
        if not isinstance(message, str) or not message:
            message = "invalid tool request"
        message = message[:400]
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return self.message


def _empty_object_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


def _session_id_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": MAX_SESSION_ID_CHARS,
        "pattern": SESSION_ID_PATTERN.pattern,
        "description": "Airlock session instance id.",
    }


def _proposal_id_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": MAX_PROPOSAL_ID_CHARS,
        "pattern": PROPOSAL_ID_PATTERN.pattern,
        "description": "Console proposal id.",
    }


def _model_id_schema(*, description: str) -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": MAX_MODEL_ID_CHARS,
        "pattern": MODEL_ID_PATTERN.pattern,
        "description": description,
    }


def _reason_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": MIN_REASON_CHARS,
        "maxLength": MAX_REASON_CHARS,
        "description": "Human-readable reason for the proposal.",
    }


def _timestamp_schema(*, description: str) -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 20,
        "maxLength": 40,
        "pattern": TIMESTAMP_PATTERN.pattern,
        "description": description,
    }


def _history_id_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 4,
        "maxLength": 15,
        "pattern": HISTORY_ID_PATTERN.pattern,
        "description": "History id of a past or present session, as airlock_list_history returns it (hx-...).",
    }


def _agent_id_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 4,
        "maxLength": 64,
        "pattern": AGENT_ID_PATTERN.pattern,
        "description": "Subagent id, as airlock_list_subagents returns it.",
    }


def _filter_schema(description: str, *, max_chars: int = MAX_FILTER_CHARS) -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": max_chars,
        "description": description,
    }


def _limit_schema(maximum: int, *, description: str) -> dict[str, Any]:
    return {
        "type": "integer",
        "minimum": 1,
        "maximum": maximum,
        "description": description,
    }


def _annotations(*, read_only: bool, untrusted: bool) -> dict[str, Any]:
    annotations: dict[str, Any] = {"readOnlyHint": bool(read_only)}
    if untrusted:
        annotations["untrustedContentHint"] = True
    return annotations


def _tool(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    *,
    read_only: bool,
    untrusted: bool,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": input_schema,
        "annotations": _annotations(read_only=read_only, untrusted=untrusted),
    }


_CHAIN_PEER_SCHEMA = {
    "type": "string",
    "minLength": 1,
    "maxLength": MAX_MODEL_ID_CHARS,
    "pattern": MODEL_ID_PATTERN.pattern,
}

_CHAINS_SCHEMA = {
    "type": "object",
    "minProperties": 1,
    "maxProperties": MAX_CHAIN_SOURCES,
    "additionalProperties": {
        "type": "array",
        "maxItems": MAX_CHAIN_PEERS,
        "items": _CHAIN_PEER_SCHEMA,
    },
    "description": (
        "Map of source model id to ordered failover peers. "
        "Peers cannot include the source or duplicates."
    ),
}

_TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    _tool(
        "airlock_list_sessions",
        "List live Airlock Console sessions with public summary fields.",
        _empty_object_schema(),
        read_only=True,
        untrusted=False,
    ),
    _tool(
        "airlock_get_session",
        "Get one Airlock session detail, including routes, chains, and events.",
        {
            "type": "object",
            "properties": {"session_id": _session_id_schema()},
            "required": ["session_id"],
            "additionalProperties": False,
        },
        read_only=True,
        untrusted=True,
    ),
    _tool(
        "airlock_get_session_events",
        "List public events for one Airlock session, optionally filtered.",
        {
            "type": "object",
            "properties": {
                "session_id": _session_id_schema(),
                "kind": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_KIND_CHARS,
                    "pattern": KIND_PATTERN.pattern,
                    "description": "Optional event kind filter.",
                },
                "model": _model_id_schema(description="Optional model filter."),
                "since": _timestamp_schema(
                    description="Optional inclusive lower bound timestamp."
                ),
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
        read_only=True,
        untrusted=True,
    ),
    _tool(
        "airlock_get_routes",
        "List route status rows, optionally limited to one session.",
        {
            "type": "object",
            "properties": {"session_id": _session_id_schema()},
            "additionalProperties": False,
        },
        read_only=True,
        untrusted=False,
    ),
    _tool(
        "airlock_get_headroom",
        "List provider headroom rows known to Airlock Console.",
        _empty_object_schema(),
        read_only=True,
        untrusted=False,
    ),
    _tool(
        "airlock_get_global_failover_chain",
        "Read the current global failover chain map and digest.",
        _empty_object_schema(),
        read_only=True,
        untrusted=False,
    ),
    _tool(
        "airlock_list_proposals",
        "List Console proposals, optionally filtered by session, kind, or status.",
        {
            "type": "object",
            "properties": {
                "session_id": _session_id_schema(),
                "kind": {
                    "type": "string",
                    "enum": sorted(PROPOSAL_KINDS),
                    "description": "Optional proposal kind filter.",
                },
                "status": {
                    "type": "string",
                    "enum": sorted(PROPOSAL_STATUSES),
                    "description": "Optional proposal status filter.",
                },
            },
            "additionalProperties": False,
        },
        read_only=True,
        untrusted=True,
    ),
    _tool(
        "airlock_get_proposal",
        "Get one Console proposal by id.",
        {
            "type": "object",
            "properties": {"proposal_id": _proposal_id_schema()},
            "required": ["proposal_id"],
            "additionalProperties": False,
        },
        read_only=True,
        untrusted=True,
    ),
    _tool(
        "airlock_propose_session_handoff",
        "Create a session handoff proposal for a person to review in Console.",
        {
            "type": "object",
            "properties": {
                "session_id": _session_id_schema(),
                "target_model": _model_id_schema(
                    description="Exact model id to pin after approval."
                ),
                "reason": _reason_schema(),
                "allow_metered": {
                    "type": "boolean",
                    "description": "Allow a metered target when true.",
                },
            },
            "required": ["session_id", "target_model", "reason"],
            "additionalProperties": False,
        },
        read_only=False,
        untrusted=True,
    ),
    _tool(
        "airlock_propose_restore_root",
        "Create a proposal to restore the session root model after a pin.",
        {
            "type": "object",
            "properties": {
                "session_id": _session_id_schema(),
                "reason": _reason_schema(),
            },
            "required": ["session_id", "reason"],
            "additionalProperties": False,
        },
        read_only=False,
        untrusted=True,
    ),
    _tool(
        "airlock_propose_chain_change",
        "Create a global failover chain change proposal for human review.",
        {
            "type": "object",
            "properties": {
                "chains": _CHAINS_SCHEMA,
                "reason": _reason_schema(),
            },
            "required": ["chains", "reason"],
            "additionalProperties": False,
        },
        read_only=False,
        untrusted=True,
    ),
    _tool(
        "airlock_list_history",
        "List past and present sessions from this machine's transcript index: "
        "project, title, branch, models, turns, tool calls, compactions, peak "
        "context against the window, subagent count. Filter by project, model, "
        "time, or words in the title.",
        {
            "type": "object",
            "properties": {
                "project": _filter_schema("Exact project directory name, case-insensitive."),
                "model": _filter_schema("Substring of a model id the session used."),
                "since": _timestamp_schema(description="Only sessions active at or after this time."),
                "until": _timestamp_schema(description="Only sessions started at or before this time."),
                "q": _filter_schema("Words to find in the title, project, branch, or session id."),
                "limit": _limit_schema(MAX_HISTORY_SESSIONS, description="Most recent N sessions, default 100."),
            },
            "additionalProperties": False,
        },
        read_only=True,
        untrusted=True,
    ),
    _tool(
        "airlock_get_history_session",
        "Get one indexed session in full: facts, the subagents it launched, "
        "when it ran through an Airlock router, per-model compactions, tool "
        "and subagent-type counts, and its latest activity.",
        {
            "type": "object",
            "properties": {"history_id": _history_id_schema()},
            "required": ["history_id"],
            "additionalProperties": False,
        },
        read_only=True,
        untrusted=True,
    ),
    _tool(
        "airlock_list_subagents",
        "List the subagents one indexed session launched, with type, model, "
        "turns, tool calls, peak context, and timing.",
        {
            "type": "object",
            "properties": {"history_id": _history_id_schema()},
            "required": ["history_id"],
            "additionalProperties": False,
        },
        read_only=True,
        untrusted=True,
    ),
    _tool(
        "airlock_get_subagent_feed",
        "Read one subagent's latest activity from its own transcript: its "
        "model, context, and the last turns and tool calls with short previews.",
        {
            "type": "object",
            "properties": {
                "history_id": _history_id_schema(),
                "agent_id": _agent_id_schema(),
            },
            "required": ["history_id", "agent_id"],
            "additionalProperties": False,
        },
        read_only=True,
        untrusted=True,
    ),
    _tool(
        "airlock_get_usage",
        "Activity over time from the transcript index, grouped by day, week, "
        "or month: turns, replies by provider, tool calls, output tokens, "
        "compactions, sessions active, and rankings of subagent types, tools, "
        "models, projects, and the largest contexts. Counts, never cost.",
        {
            "type": "object",
            "properties": {
                "group": {"type": "string", "enum": list(USAGE_GROUPS), "description": "Period size, default day."},
                "project": _filter_schema("Exact project directory name, case-insensitive."),
                "model": _filter_schema("Substring of a model id the session used."),
                "since": _timestamp_schema(description="First day to count, inclusive."),
                "until": _timestamp_schema(description="Last day to count, inclusive."),
            },
            "additionalProperties": False,
        },
        read_only=True,
        untrusted=True,
    ),
    _tool(
        "airlock_query_history",
        "Answer any counting question about this machine's sessions: group "
        "every measure by one dimension under filters. Example: which model "
        "compacted most in project X is dimension=model, project=X, "
        "order_by=compactions. Dimensions: session, project, model, provider, "
        "agent_type, tool, day, week, month, weekday, branch, entrypoint. "
        "Measures: sessions, prompts, replies, tool_calls, output_tokens, "
        "compactions, agent_launches, peak_context_max; a measure the "
        "dimension cannot attribute is null. Filters select whole sessions; "
        "since and until then select the days inside them.",
        {
            "type": "object",
            "properties": {
                "dimension": {"type": "string", "enum": list(QUERY_DIMENSIONS), "description": "What each row is."},
                "project": _filter_schema("Exact project directory name, case-insensitive."),
                "model": _filter_schema("Substring of a model id."),
                "provider": {"type": "string", "minLength": 1, "maxLength": 32, "pattern": PROVIDER_PATTERN.pattern,
                             "description": "Provider key such as anthropic, openai, grok, openrouter."},
                "agent_type": _filter_schema("Substring of a subagent type name."),
                "tool": _filter_schema("Substring of a tool name."),
                "branch": _filter_schema("Substring of a git branch name."),
                "entrypoint": _filter_schema("Exact Claude Code entrypoint, such as cli or sdk-cli.", max_chars=40),
                "since": _timestamp_schema(description="First day to count, inclusive."),
                "until": _timestamp_schema(description="Last day to count, inclusive."),
                "q": _filter_schema("Words to find in the title, project, branch, or session id."),
                "order_by": {"type": "string", "enum": ["key", *QUERY_MEASURES],
                             "description": "Sort measure; default is the dimension's main measure, or key for time dimensions."},
                "descending": {"type": "boolean", "description": "Sort direction, default true (false for time dimensions)."},
                "limit": _limit_schema(MAX_QUERY_ROWS, description="Rows to return, default 50."),
            },
            "required": ["dimension"],
            "additionalProperties": False,
        },
        read_only=True,
        untrusted=True,
    ),
)

_TOOL_BY_NAME: Mapping[str, Mapping[str, Any]] = MappingProxyType({
    tool["name"]: MappingProxyType({
        "name": tool["name"],
        "description": tool["description"],
        "inputSchema": MappingProxyType(deepcopy(tool["inputSchema"])),
        "annotations": MappingProxyType(dict(tool["annotations"])),
    })
    for tool in _TOOL_DEFINITIONS
})


def tool_manifest() -> list[dict[str, Any]]:
    """Return an independent deep copy of every tool definition."""

    return deepcopy(list(_TOOL_DEFINITIONS))


def _raise(code: str, message: str) -> None:
    raise ToolContractError(code, message)


def _count_nodes(value: object, *, depth: int, counter: list[int]) -> None:
    counter[0] += 1
    if counter[0] > MAX_INPUT_NODES:
        _raise("invalid_arguments", "arguments are too large")
    if depth > MAX_INPUT_DEPTH:
        _raise("invalid_arguments", "arguments nest too deeply")
    if isinstance(value, dict):
        if len(value) > MAX_OBJECT_KEYS:
            _raise("invalid_arguments", "object has too many keys")
        for item in value.values():
            _count_nodes(item, depth=depth + 1, counter=counter)
    elif isinstance(value, list):
        if len(value) > MAX_ARRAY_ITEMS:
            _raise("invalid_arguments", "array has too many items")
        for item in value:
            _count_nodes(item, depth=depth + 1, counter=counter)


def _require_object(value: object, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        _raise("invalid_arguments", f"{label} must be an object")
    return value


def _reject_extra_keys(payload: Mapping[str, Any], allowed: set[str]) -> None:
    extra = set(payload) - allowed
    if extra:
        _raise("invalid_arguments", "arguments contain unsupported fields")


def _require_keys(payload: Mapping[str, Any], required: set[str]) -> None:
    missing = required - set(payload)
    if missing:
        _raise("invalid_arguments", "arguments are missing required fields")


def _as_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        _raise("invalid_arguments", f"{label} must be a boolean")
    return value


def _as_string(value: object, label: str, *, min_chars: int, max_chars: int) -> str:
    if type(value) is not str:
        _raise("invalid_arguments", f"{label} must be a string")
    if len(value) < min_chars or len(value) > max_chars:
        _raise("invalid_arguments", f"{label} length is out of bounds")
    if any(ord(char) < 32 for char in value):
        _raise("invalid_arguments", f"{label} contains unsafe characters")
    return value


def _validate_session_id(value: object, label: str = "session_id") -> str:
    text = _as_string(value, label, min_chars=1, max_chars=MAX_SESSION_ID_CHARS)
    if SESSION_ID_PATTERN.fullmatch(text) is None:
        _raise("invalid_arguments", f"{label} is not a valid session id")
    return text


def _validate_proposal_id(value: object, label: str = "proposal_id") -> str:
    text = _as_string(value, label, min_chars=1, max_chars=MAX_PROPOSAL_ID_CHARS)
    if PROPOSAL_ID_PATTERN.fullmatch(text) is None:
        _raise("invalid_arguments", f"{label} is not a valid proposal id")
    return text


def _wire_model_id(model: str) -> str:
    """Strip a trailing [1m] instruction so alias forms collide."""

    if model.endswith("[1m]"):
        return model.removesuffix("[1m]")
    return model


def _validate_model_id(value: object, label: str) -> str:
    text = _as_string(value, label, min_chars=1, max_chars=MAX_MODEL_ID_CHARS)
    if not text.isascii() or MODEL_ID_PATTERN.fullmatch(text) is None:
        _raise("invalid_arguments", f"{label} is not a valid model id")
    return text


def _validate_reason(value: object) -> str:
    text = _as_string(
        value,
        "reason",
        min_chars=MIN_REASON_CHARS,
        max_chars=MAX_REASON_CHARS,
    )
    if not text.strip():
        _raise("invalid_arguments", "reason must contain visible text")
    return text


def _validate_timestamp(value: object, label: str) -> str:
    text = _as_string(value, label, min_chars=20, max_chars=40)
    if TIMESTAMP_PATTERN.fullmatch(text) is None:
        _raise("invalid_arguments", f"{label} is not a valid timestamp")
    return text


def _validate_kind_filter(value: object) -> str:
    text = _as_string(value, "kind", min_chars=1, max_chars=MAX_KIND_CHARS)
    if KIND_PATTERN.fullmatch(text) is None:
        _raise("invalid_arguments", "kind is not a valid filter")
    return text


def _validate_proposal_kind(value: object) -> str:
    text = _as_string(value, "kind", min_chars=1, max_chars=MAX_KIND_CHARS)
    if text not in PROPOSAL_KINDS:
        _raise("invalid_arguments", "kind is not a supported proposal kind")
    return text


def _validate_proposal_status(value: object) -> str:
    text = _as_string(value, "status", min_chars=1, max_chars=MAX_STATUS_CHARS)
    if text not in PROPOSAL_STATUSES:
        _raise("invalid_arguments", "status is not a supported proposal status")
    return text


def _validate_chains(value: object) -> dict[str, list[str]]:
    if type(value) is not dict:
        _raise("invalid_arguments", "chains must be an object")
    if not value:
        _raise("invalid_arguments", "chains must name at least one source")
    if len(value) > MAX_CHAIN_SOURCES:
        _raise("invalid_arguments", "chains names too many sources")
    chains: dict[str, list[str]] = {}
    seen_wire_sources: set[str] = set()
    for raw_source, raw_peers in value.items():
        source = _validate_model_id(raw_source, "chains source")
        wire_source = _wire_model_id(source)
        if wire_source in seen_wire_sources:
            _raise(
                "invalid_arguments",
                "chains names duplicate sources that share a wire id",
            )
        seen_wire_sources.add(wire_source)
        if type(raw_peers) is not list:
            _raise("invalid_arguments", "chain peers must be an array")
        if len(raw_peers) > MAX_CHAIN_PEERS:
            _raise("invalid_arguments", "chain peer list is too long")
        peers: list[str] = []
        seen: set[str] = set()
        source_forms = {source, wire_source}
        for raw_peer in raw_peers:
            peer = _validate_model_id(raw_peer, "chain peer")
            wire_peer = _wire_model_id(peer)
            if peer == source or wire_peer in source_forms:
                _raise("invalid_arguments", "chain peer cannot equal its source")
            if peer in seen or wire_peer in {_wire_model_id(item) for item in seen}:
                _raise("invalid_arguments", "chain peers must be unique")
            seen.add(peer)
            peers.append(peer)
        chains[source] = peers
    return chains


def _validate_filter(value: object, label: str, *, max_chars: int = MAX_FILTER_CHARS) -> str:
    text = _as_string(value, label, min_chars=1, max_chars=max_chars)
    if not text.strip():
        _raise("invalid_arguments", f"{label} must contain visible text")
    return text.strip()


def _validate_history_id(value: object, label: str = "history_id") -> str:
    text = _as_string(value, label, min_chars=4, max_chars=15)
    if HISTORY_ID_PATTERN.fullmatch(text) is None:
        _raise("invalid_arguments", f"{label} is not a valid history id")
    return text


def _validate_agent_id(value: object, label: str = "agent_id") -> str:
    text = _as_string(value, label, min_chars=4, max_chars=64)
    if AGENT_ID_PATTERN.fullmatch(text) is None or ".." in text:
        _raise("invalid_arguments", f"{label} is not a valid subagent id")
    return text


def _validate_limit(value: object, label: str, *, maximum: int) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        _raise("invalid_arguments", f"{label} is out of bounds")
    return value


def _validate_choice(value: object, label: str, choices: tuple[str, ...]) -> str:
    text = _as_string(value, label, min_chars=1, max_chars=32)
    if text not in choices:
        _raise("invalid_arguments", f"{label} is not a supported value")
    return text


def _validate_history_filters(payload: Mapping[str, Any], normalized: dict[str, Any]) -> None:
    if "project" in payload:
        normalized["project"] = _validate_filter(payload["project"], "project")
    if "model" in payload:
        normalized["model"] = _validate_filter(payload["model"], "model")
    if "since" in payload:
        normalized["since"] = _validate_timestamp(payload["since"], "since")
    if "until" in payload:
        normalized["until"] = _validate_timestamp(payload["until"], "until")
    if "q" in payload:
        normalized["q"] = _validate_filter(payload["q"], "q")


def _validate_arguments(name: str, arguments: object) -> dict[str, Any]:
    payload = _require_object(arguments, "arguments")
    _count_nodes(payload, depth=0, counter=[0])

    if name == "airlock_list_history":
        _reject_extra_keys(payload, {"project", "model", "since", "until", "q", "limit"})
        normalized: dict[str, Any] = {}
        _validate_history_filters(payload, normalized)
        if "limit" in payload:
            normalized["limit"] = _validate_limit(payload["limit"], "limit", maximum=MAX_HISTORY_SESSIONS)
        return normalized

    if name in {"airlock_get_history_session", "airlock_list_subagents"}:
        _require_keys(payload, {"history_id"})
        _reject_extra_keys(payload, {"history_id"})
        return {"history_id": _validate_history_id(payload["history_id"])}

    if name == "airlock_get_subagent_feed":
        _require_keys(payload, {"history_id", "agent_id"})
        _reject_extra_keys(payload, {"history_id", "agent_id"})
        return {
            "history_id": _validate_history_id(payload["history_id"]),
            "agent_id": _validate_agent_id(payload["agent_id"]),
        }

    if name == "airlock_get_usage":
        _reject_extra_keys(payload, {"group", "project", "model", "since", "until"})
        normalized = {}
        if "group" in payload:
            normalized["group"] = _validate_choice(payload["group"], "group", USAGE_GROUPS)
        _validate_history_filters(payload, normalized)
        return normalized

    if name == "airlock_query_history":
        _require_keys(payload, {"dimension"})
        _reject_extra_keys(payload, {
            "dimension", "project", "model", "provider", "agent_type", "tool", "branch",
            "entrypoint", "since", "until", "q", "order_by", "descending", "limit",
        })
        normalized = {"dimension": _validate_choice(payload["dimension"], "dimension", QUERY_DIMENSIONS)}
        _validate_history_filters(payload, normalized)
        if "provider" in payload:
            text = _as_string(payload["provider"], "provider", min_chars=1, max_chars=32)
            if PROVIDER_PATTERN.fullmatch(text) is None:
                _raise("invalid_arguments", "provider is not a valid provider key")
            normalized["provider"] = text
        for key in ("agent_type", "tool", "branch"):
            if key in payload:
                normalized[key] = _validate_filter(payload[key], key)
        if "entrypoint" in payload:
            normalized["entrypoint"] = _validate_filter(payload["entrypoint"], "entrypoint", max_chars=40)
        if "order_by" in payload:
            normalized["order_by"] = _validate_choice(payload["order_by"], "order_by", ("key", *QUERY_MEASURES))
        if "descending" in payload:
            normalized["descending"] = _as_bool(payload["descending"], "descending")
        if "limit" in payload:
            normalized["limit"] = _validate_limit(payload["limit"], "limit", maximum=MAX_QUERY_ROWS)
        return normalized

    if name == "airlock_list_sessions":
        _reject_extra_keys(payload, set())
        return {}

    if name == "airlock_get_session":
        _require_keys(payload, {"session_id"})
        _reject_extra_keys(payload, {"session_id"})
        return {"session_id": _validate_session_id(payload["session_id"])}

    if name == "airlock_get_session_events":
        _require_keys(payload, {"session_id"})
        _reject_extra_keys(payload, {"session_id", "kind", "model", "since"})
        normalized: dict[str, Any] = {
            "session_id": _validate_session_id(payload["session_id"])
        }
        if "kind" in payload:
            normalized["kind"] = _validate_kind_filter(payload["kind"])
        if "model" in payload:
            normalized["model"] = _validate_model_id(payload["model"], "model")
        if "since" in payload:
            normalized["since"] = _validate_timestamp(payload["since"], "since")
        return normalized

    if name == "airlock_get_routes":
        _reject_extra_keys(payload, {"session_id"})
        if "session_id" not in payload:
            return {}
        return {"session_id": _validate_session_id(payload["session_id"])}

    if name == "airlock_get_headroom":
        _reject_extra_keys(payload, set())
        return {}

    if name == "airlock_get_global_failover_chain":
        _reject_extra_keys(payload, set())
        return {}

    if name == "airlock_list_proposals":
        _reject_extra_keys(payload, {"session_id", "kind", "status"})
        normalized = {}
        if "session_id" in payload:
            normalized["session_id"] = _validate_session_id(payload["session_id"])
        if "kind" in payload:
            normalized["kind"] = _validate_proposal_kind(payload["kind"])
        if "status" in payload:
            normalized["status"] = _validate_proposal_status(payload["status"])
        return normalized

    if name == "airlock_get_proposal":
        _require_keys(payload, {"proposal_id"})
        _reject_extra_keys(payload, {"proposal_id"})
        return {"proposal_id": _validate_proposal_id(payload["proposal_id"])}

    if name == "airlock_propose_session_handoff":
        _require_keys(payload, {"session_id", "target_model", "reason"})
        _reject_extra_keys(
            payload, {"session_id", "target_model", "reason", "allow_metered"}
        )
        normalized = {
            "session_id": _validate_session_id(payload["session_id"]),
            "target_model": _validate_model_id(payload["target_model"], "target_model"),
            "reason": _validate_reason(payload["reason"]),
        }
        if "allow_metered" in payload:
            normalized["allow_metered"] = _as_bool(
                payload["allow_metered"], "allow_metered"
            )
        return normalized

    if name == "airlock_propose_restore_root":
        _require_keys(payload, {"session_id", "reason"})
        _reject_extra_keys(payload, {"session_id", "reason"})
        return {
            "session_id": _validate_session_id(payload["session_id"]),
            "reason": _validate_reason(payload["reason"]),
        }

    if name == "airlock_propose_chain_change":
        _require_keys(payload, {"chains", "reason"})
        _reject_extra_keys(payload, {"chains", "reason"})
        return {
            "chains": _validate_chains(payload["chains"]),
            "reason": _validate_reason(payload["reason"]),
        }

    _raise("unknown_tool", "unknown tool")
    return {}


def validate_tool_call(name: object, arguments: object) -> dict[str, Any]:
    """Validate and normalize a tool call. Never echoes arbitrary input."""

    if type(name) is not str or not name:
        _raise("unknown_tool", "unknown tool")
    if name not in _TOOL_BY_NAME:
        _raise("unknown_tool", "unknown tool")
    if arguments is None:
        arguments = {}
    return _validate_arguments(name, arguments)


def _name_forbidden(name: str) -> bool:
    lowered = name.lower()
    return any(fragment in lowered for fragment in FORBIDDEN_NAME_FRAGMENTS)


def _project_number(value: object) -> int | float | None:
    if type(value) is bool:
        return None
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            return None
        return value
    return None


def _project_string(value: object, *, max_chars: int = MAX_PROJECTED_STRING_CHARS) -> str | None:
    if type(value) is not str:
        return None
    if len(value) > max_chars:
        return value[:max_chars]
    return value


def _project_bool(value: object) -> bool | None:
    if type(value) is bool:
        return value
    return None


_KEEP_NULL = object()

# Keys that contain a forbidden fragment but are plain counters, never content.
SAFE_COUNTER_KEYS = frozenset({
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "context_input_tokens",
    "prompts",
})


def _project_mapping(
    value: object,
    schema: Mapping[str, Any],
    *,
    max_keys: int = MAX_PROJECTED_OBJECT_KEYS,
) -> dict[str, Any] | None:
    if type(value) is not dict:
        return None
    projected: dict[str, Any] = {}
    for key, child_schema in schema.items():
        if key not in value:
            continue
        if _name_forbidden(key) and key not in SAFE_COUNTER_KEYS:
            continue
        child = _project_value(value.get(key), child_schema)
        if child is _KEEP_NULL:
            projected[key] = None
        elif child is not None:
            projected[key] = child
        if len(projected) >= max_keys:
            break
    return projected


def _project_list(
    value: object,
    item_schema: Any,
    *,
    max_items: int,
) -> list[Any] | None:
    if type(value) is not list:
        return None
    items: list[Any] = []
    for raw in value[:max_items]:
        item = _project_value(raw, item_schema)
        if item is not None:
            items.append(item)
    return items


def _project_count_map(value: object) -> dict[str, int] | None:
    """A bounded map of name to non-negative integer, such as tool counts."""
    if type(value) is not dict:
        return None
    projected: dict[str, int] = {}
    for raw_key, raw_count in value.items():
        if len(projected) >= MAX_COUNT_MAP_KEYS:
            break
        key = _project_string(raw_key, max_chars=MAX_MODEL_ID_CHARS)
        if key is None or not key or _name_forbidden(key):
            continue
        if any(ord(char) < 32 for char in key):
            continue
        count = _project_number(raw_count)
        if type(count) is not int or count < 0:
            continue
        projected[key] = count
    return projected


def _project_value(value: object, schema: Any) -> Any:
    if isinstance(schema, tuple) and schema and schema[0] == "nullable":
        if value is None:
            return _KEEP_NULL
        return _project_value(value, schema[1])
    if isinstance(schema, tuple) and schema and schema[0] == "count_map":
        return _project_count_map(value)
    if schema == "string":
        return _project_string(value)
    if schema == "boolean":
        return _project_bool(value)
    if schema == "number":
        return _project_number(value)
    if schema == "integer":
        number = _project_number(value)
        if type(number) is int:
            return number
        return None
    if isinstance(schema, tuple) and schema and schema[0] == "enum":
        text = _project_string(value, max_chars=64)
        if text in schema[1]:
            return text
        return None
    if isinstance(schema, dict) and schema.get("__list__") is True:
        return _project_list(
            value,
            schema["item"],
            max_items=int(schema.get("max_items", MAX_PROJECTED_ARRAY_ITEMS)),
        )
    if isinstance(schema, dict) and schema.get("__map_list__") is True:
        if type(value) is not dict:
            return None
        projected: dict[str, Any] = {}
        for raw_key, raw_peers in list(value.items())[: MAX_CHAIN_SOURCES]:
            key = _project_string(raw_key, max_chars=MAX_MODEL_ID_CHARS)
            if key is None or not key.isascii() or MODEL_ID_PATTERN.fullmatch(key) is None:
                continue
            if _name_forbidden(key):
                continue
            peers = _project_list(
                raw_peers,
                "string",
                max_items=MAX_CHAIN_PEERS,
            )
            if peers is None:
                continue
            clean_peers: list[str] = []
            seen: set[str] = set()
            for peer in peers:
                if type(peer) is not str:
                    continue
                if not peer.isascii() or MODEL_ID_PATTERN.fullmatch(peer) is None:
                    continue
                if peer == key or peer in seen:
                    continue
                seen.add(peer)
                clean_peers.append(peer[:MAX_MODEL_ID_CHARS])
            projected[key] = clean_peers
        return projected
    if isinstance(schema, dict):
        return _project_mapping(value, schema)
    return None


_CONTEXT_SCHEMA = {
    "input_tokens": "integer",
    "window": "integer",
    "model": "string",
    "observed_at": "string",
}

_WORKER_SCHEMA = {
    "model": "string",
    "requests": "integer",
}

_ROUTE_SCHEMA = {
    "model": "string",
    "short_name": "string",
    "provider": "string",
    "category": ("enum", ROUTE_CATEGORIES),
    "metered": "boolean",
    "context_window": "integer",
    "effort_ceiling": "string",
    "status": ("enum", ROUTE_STATUSES),
    "cooldown_remaining_seconds": "integer",
    "fits_context": "boolean",
    "sessions_using": {"__list__": True, "item": "string", "max_items": 64},
}

_COOLDOWN_SCHEMA = {
    "scope": ("enum", COOLDOWN_SCOPES),
    "model": "string",
    "provider": "string",
    "remaining_seconds": "integer",
    "until": "string",
}

_USAGE_SCHEMA = {
    "provider": "string",
    "model": "string",
    "requests": "integer",
    "completed": "integer",
    "errors": "integer",
    "input_tokens": "integer",
    "output_tokens": "integer",
    "cache_read_input_tokens": "integer",
}

_EVENT_USAGE_SCHEMA = {
    "input_tokens": "integer",
    "output_tokens": "integer",
    "cache_read_input_tokens": "integer",
    "cache_creation_input_tokens": "integer",
}

_EVENT_SCHEMA = {
    "timestamp": "string",
    "kind": "string",
    "model": "string",
    "provider": "string",
    "status": "integer",
    "outcome": "string",
    "failover_from": "string",
    "to_model": "string",
    "from_model": "string",
    "models_considered": "integer",
    "reason": "string",
    "remaining_seconds": "integer",
    "duration_ms": "integer",
    "usage": _EVENT_USAGE_SCHEMA,
}

_LAST_HANDOFF_SCHEMA = {
    "at": "string",
    "from_model": "string",
    "to_model": "string",
    "reason": "string",
}

_SESSION_SUMMARY_SCHEMA = {
    "id": "string",
    "state": ("enum", SESSION_STATES),
    "blocked_reason": ("enum", BLOCKED_REASONS),
    "profile": "string",
    "root_model": "string",
    "root_provider": "string",
    "active_model": "string",
    "pinned_model": "string",
    "project": "string",
    "started_at": "string",
    "last_activity_at": "string",
    "context": _CONTEXT_SCHEMA,
    "workers": {"__list__": True, "item": _WORKER_SCHEMA, "max_items": MAX_WORKERS},
    "recent_handoffs": "integer",
}

_SESSION_DETAIL_SCHEMA = {
    **_SESSION_SUMMARY_SCHEMA,
    "routes": {"__list__": True, "item": _ROUTE_SCHEMA, "max_items": MAX_ROUTES},
    "cooldowns": {
        "__list__": True,
        "item": _COOLDOWN_SCHEMA,
        "max_items": MAX_COOLDOWNS,
    },
    "chains": {"__map_list__": True},
    "usage": {"__list__": True, "item": _USAGE_SCHEMA, "max_items": MAX_ROUTES},
    "events": {"__list__": True, "item": _EVENT_SCHEMA, "max_items": MAX_EVENTS},
    "last_handoff": _LAST_HANDOFF_SCHEMA,
}

_HEADROOM_SCHEMA = {
    "provider": "string",
    "window": "string",
    "used_percent": "number",
    "resets_at": "string",
    "source": "string",
}

_SAFE_ERROR_SCHEMA = {
    "code": "string",
    "message": "string",
}

_APPLICATION_SCHEMA = {
    "attempts": "integer",
    "started_at": "string",
    "finished_at": "string",
    "router_instance_id": "string",
    "previous_pinned_model": "string",
    "pinned_model": "string",
    "changed": "boolean",
    "already_applied": "boolean",
}

_BASIS_SCHEMA = {
    "observed_at": "string",
    "active_model": "string",
    "pinned_model": "string",
    "context_input_tokens": "integer",
    "route_status": "string",
}

_PROPOSAL_SCHEMA = {
    "id": "string",
    "kind": ("enum", PROPOSAL_KINDS),
    "session_id": "string",
    "operation": "string",
    "target_model": "string",
    "reason": "string",
    "allow_metered": "boolean",
    "chains": {"__map_list__": True},
    "base_digest": "string",
    "created_by": "string",
    "created_at": "string",
    "expires_at": "string",
    "revision": "integer",
    "status": ("enum", PROPOSAL_STATUSES),
    "basis": _BASIS_SCHEMA,
    "application": _APPLICATION_SCHEMA,
    "last_error": _SAFE_ERROR_SCHEMA,
}

_GLOBAL_CHAIN_SCHEMA = {
    "chains": ("nullable", {"__map_list__": True}),
    "digest": ("nullable", "string"),
    "notice": "string",
    "unavailable": "boolean",
    "reason": "string",
}

_INT_OR_NULL = ("nullable", "integer")
_STR_OR_NULL = ("nullable", "string")

_HISTORY_ITEM_SCHEMA = {
    "id": "string",
    "session_id": "string",
    "project": "string",
    "title": _STR_OR_NULL,
    "branch": _STR_OR_NULL,
    "models": {"__list__": True, "item": "string", "max_items": 32},
    "primary_model": _STR_OR_NULL,
    "provider": "string",
    "started_at": _STR_OR_NULL,
    "last_activity_at": _STR_OR_NULL,
    "prompts": "integer",
    "replies": "integer",
    "tool_calls": "integer",
    "compactions": "integer",
    "peak_context": _INT_OR_NULL,
    "last_context": _INT_OR_NULL,
    "window": _INT_OR_NULL,
    "output_tokens": "integer",
    "subagents": "integer",
    "entrypoint": _STR_OR_NULL,
    "version": _STR_OR_NULL,
}

_SUBAGENT_SCHEMA = {
    "id": "string",
    "agent_type": _STR_OR_NULL,
    "description": _STR_OR_NULL,
    "model": _STR_OR_NULL,
    "background": "boolean",
    "status": _STR_OR_NULL,
    "started_at": _STR_OR_NULL,
    "finished_at": _STR_OR_NULL,
    "last_activity_at": _STR_OR_NULL,
    "prompts": "integer",
    "replies": "integer",
    "tool_calls": "integer",
    "peak_context": _INT_OR_NULL,
    "output_tokens": "integer",
    "depth": "integer",
}

_PERIOD_SCHEMA = {
    "kind": "string",
    "from": "string",
    "to": "string",
    "profile": _STR_OR_NULL,
    "root_model": _STR_OR_NULL,
    "open": "boolean",
}

_ACTIVITY_SCHEMA = {
    "at": "string",
    "kind": ("enum", frozenset({"prompt", "reply", "tool"})),
    "tool": "string",
    "preview": _STR_OR_NULL,
}

_HISTORY_DETAIL_SCHEMA = {
    **_HISTORY_ITEM_SCHEMA,
    "agents": {"__list__": True, "item": _SUBAGENT_SCHEMA, "max_items": MAX_SUBAGENTS},
    "periods": {"__list__": True, "item": _PERIOD_SCHEMA, "max_items": 64},
    "airlock_inferred": "boolean",
    "compactions_by_model": ("count_map",),
    "tools": ("count_map",),
    "agent_types": ("count_map",),
    "activity": {"__list__": True, "item": _ACTIVITY_SCHEMA, "max_items": MAX_ACTIVITY_ITEMS},
}

_HISTORY_CONTEXT_SCHEMA = {
    "input_tokens": _INT_OR_NULL,
    "window": _INT_OR_NULL,
}

_SUBAGENT_FEED_SCHEMA = {
    "id": "string",
    "model": _STR_OR_NULL,
    "last_activity_at": _STR_OR_NULL,
    "context": ("nullable", _HISTORY_CONTEXT_SCHEMA),
    "activity": {"__list__": True, "item": _ACTIVITY_SCHEMA, "max_items": MAX_ACTIVITY_ITEMS},
}

_USAGE_TOTALS_SCHEMA = {
    "sessions": "integer",
    "prompts": "integer",
    "replies": "integer",
    "tool_calls": "integer",
    "output_tokens": "integer",
    "compactions": "integer",
    "subagents": "integer",
    "peak_context_max": "integer",
}

_USAGE_PERIOD_SCHEMA = {
    "period": "string",
    "sessions": "integer",
    "prompts": "integer",
    "replies": "integer",
    "tool_calls": "integer",
    "output_tokens": "integer",
    "compactions": "integer",
    "by_provider": ("count_map",),
}

_NAMED_COUNT_SCHEMA = {"name": "string", "count": "integer"}

_USAGE_REPORT_SCHEMA = {
    "generated_at": "string",
    "group": ("enum", frozenset(USAGE_GROUPS)),
    "since": _STR_OR_NULL,
    "until": _STR_OR_NULL,
    "filters": {"project": _STR_OR_NULL, "model": _STR_OR_NULL},
    "totals": _USAGE_TOTALS_SCHEMA,
    "series": {"__list__": True, "item": _USAGE_PERIOD_SCHEMA, "max_items": MAX_SERIES_ROWS},
    "agent_types": {"__list__": True, "item": _NAMED_COUNT_SCHEMA, "max_items": MAX_RANK_ROWS},
    "tools": {"__list__": True, "item": _NAMED_COUNT_SCHEMA, "max_items": MAX_RANK_ROWS},
    "projects": {
        "__list__": True,
        "item": {"name": "string", "sessions": "integer", "prompts": "integer", "tool_calls": "integer", "output_tokens": "integer"},
        "max_items": MAX_RANK_ROWS,
    },
    "models": {
        "__list__": True,
        "item": {"name": "string", "replies": "integer", "provider": "string"},
        "max_items": MAX_RANK_ROWS,
    },
    "peaks": {
        "__list__": True,
        "item": {"id": _STR_OR_NULL, "project": "string", "title": _STR_OR_NULL, "peak_context": "integer", "window": _INT_OR_NULL},
        "max_items": MAX_RANK_ROWS,
    },
    "peak_buckets": {
        "under_25": "integer",
        "25_to_50": "integer",
        "50_to_75": "integer",
        "over_75": "integer",
        "unknown": "integer",
    },
}

_QUERY_ROW_SCHEMA = {
    "key": "string",
    "id": _STR_OR_NULL,
    "label": _STR_OR_NULL,
    "sessions": "integer",
    "prompts": _INT_OR_NULL,
    "replies": _INT_OR_NULL,
    "tool_calls": _INT_OR_NULL,
    "output_tokens": _INT_OR_NULL,
    "compactions": _INT_OR_NULL,
    "agent_launches": _INT_OR_NULL,
    "peak_context_max": _INT_OR_NULL,
}

_QUERY_RESULT_SCHEMA = {
    "generated_at": "string",
    "dimension": ("enum", frozenset(QUERY_DIMENSIONS)),
    "order_by": "string",
    "descending": "boolean",
    "since": _STR_OR_NULL,
    "until": _STR_OR_NULL,
    "filters": {
        "project": _STR_OR_NULL, "model": _STR_OR_NULL, "provider": _STR_OR_NULL,
        "agent_type": _STR_OR_NULL, "tool": _STR_OR_NULL, "branch": _STR_OR_NULL,
        "entrypoint": _STR_OR_NULL, "q": _STR_OR_NULL,
    },
    "sessions_matched": "integer",
    "total_rows": "integer",
    "truncated": "boolean",
    "rows": {"__list__": True, "item": _QUERY_ROW_SCHEMA, "max_items": MAX_QUERY_ROWS},
    "notes": {"__list__": True, "item": "string", "max_items": 8},
}

_HISTORY_LISTING_SCHEMA = {
    "generated_at": "string",
    "refreshed_at": _STR_OR_NULL,
    "total": "integer",
    "first_activity_at": _STR_OR_NULL,
    "last_activity_at": _STR_OR_NULL,
    "projects": {"__list__": True, "item": "string", "max_items": MAX_PROJECTED_ARRAY_ITEMS},
    "models": {"__list__": True, "item": "string", "max_items": MAX_PROJECTED_ARRAY_ITEMS},
    "sessions": {"__list__": True, "item": _HISTORY_ITEM_SCHEMA, "max_items": MAX_HISTORY_SESSIONS},
}

_RESULT_SCHEMAS: dict[str, Any] = {
    "airlock_list_history": _HISTORY_LISTING_SCHEMA,
    "airlock_get_history_session": _HISTORY_DETAIL_SCHEMA,
    "airlock_list_subagents": {
        "subagents": {"__list__": True, "item": _SUBAGENT_SCHEMA, "max_items": MAX_SUBAGENTS},
    },
    "airlock_get_subagent_feed": _SUBAGENT_FEED_SCHEMA,
    "airlock_get_usage": _USAGE_REPORT_SCHEMA,
    "airlock_query_history": _QUERY_RESULT_SCHEMA,
    "airlock_list_sessions": {
        "sessions": {
            "__list__": True,
            "item": _SESSION_SUMMARY_SCHEMA,
            "max_items": MAX_SESSIONS,
        }
    },
    "airlock_get_session": _SESSION_DETAIL_SCHEMA,
    "airlock_get_session_events": {
        "events": {
            "__list__": True,
            "item": _EVENT_SCHEMA,
            "max_items": MAX_EVENTS,
        }
    },
    "airlock_get_routes": {
        "routes": {
            "__list__": True,
            "item": _ROUTE_SCHEMA,
            "max_items": MAX_ROUTES,
        }
    },
    "airlock_get_headroom": {
        "headroom": {
            "__list__": True,
            "item": _HEADROOM_SCHEMA,
            "max_items": MAX_HEADROOM,
        }
    },
    "airlock_get_global_failover_chain": _GLOBAL_CHAIN_SCHEMA,
    "airlock_list_proposals": {
        "proposals": {
            "__list__": True,
            "item": _PROPOSAL_SCHEMA,
            "max_items": MAX_PROPOSALS,
        }
    },
    "airlock_get_proposal": _PROPOSAL_SCHEMA,
    "airlock_propose_session_handoff": _PROPOSAL_SCHEMA,
    "airlock_propose_restore_root": _PROPOSAL_SCHEMA,
    "airlock_propose_chain_change": _PROPOSAL_SCHEMA,
}


def _strip_forbidden_keys(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, child in value.items():
            if type(key) is not str:
                continue
            if _name_forbidden(key) and key not in SAFE_COUNTER_KEYS:
                continue
            cleaned[key] = _strip_forbidden_keys(child)
        return cleaned
    if isinstance(value, list):
        return [_strip_forbidden_keys(item) for item in value]
    return value


def project_tool_result(name: object, public_console_payload: object) -> dict[str, Any]:
    """Allowlist and bound a console-public tool result."""

    if type(name) is not str or name not in _RESULT_SCHEMAS:
        _raise("unknown_tool", "unknown tool")
    schema = _RESULT_SCHEMAS[name]
    projected = _project_value(public_console_payload, schema)
    if projected is None:
        if isinstance(schema, dict) and schema.get("__list__") is not True:
            projected = {}
        else:
            _raise("invalid_result", "tool result was not a safe object")
    if type(projected) is not dict:
        _raise("invalid_result", "tool result was not a safe object")
    return _strip_forbidden_keys(projected)


__all__ = [
    "HISTORY_TOOLS",
    "QUERY_DIMENSIONS",
    "QUERY_MEASURES",
    "USAGE_GROUPS",
    "CONSOLE_ADDRESS_BASENAME",
    "DEFAULT_CONSOLE_PORT",
    "DEFAULT_CONSOLE_URL",
    "FORBIDDEN_NAME_FRAGMENTS",
    "MAX_CHAIN_PEERS",
    "MAX_CHAIN_SOURCES",
    "MAX_REASON_CHARS",
    "MIN_REASON_CHARS",
    "PROPOSAL_KINDS",
    "PROPOSAL_STATUSES",
    "READ_ONLY_TOOLS",
    "TOOL_NAMES",
    "ToolContractError",
    "UNTRUSTED_CONTENT_TOOLS",
    "default_console_address_file",
    "default_console_runtime_root",
    "project_tool_result",
    "tool_manifest",
    "validate_tool_call",
]
