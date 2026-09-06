#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Pure Anthropic Messages to OpenAI Chat Completions translation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
import secrets
from typing import Any, Collection, Literal
import unicodedata


MAX_MODEL_ID_CHARS = 256
MAX_PRIVATE_MODEL_ID_CHARS = 1024
MAX_PRIVATE_MODEL_ID_BYTES = 4096
MAX_MESSAGES = 1024
MAX_TRANSLATED_MESSAGES = 4096
MAX_CONTENT_BLOCKS = 4096
MAX_TOOLS = 128
MAX_TOOL_CALLS = 64
MAX_TOOL_NAME_CHARS = 64
MAX_TOOL_ID_CHARS = 128
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_TEXT_BYTES = 32 * 1024 * 1024
MAX_SCHEMA_BYTES = 4 * 1024 * 1024
MAX_TOOL_ARGUMENT_BYTES = 2 * 1024 * 1024
MAX_REQUEST_JSON_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_JSON_BYTES = 64 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 200_000
MAX_JSON_INTEGER_BITS = 4096
MAX_STOP_SEQUENCES = 4
MAX_STOP_SEQUENCE_BYTES = 4096
MAX_SSE_LINE_BYTES = 256 * 1024
MAX_SSE_EVENT_BYTES = 2 * 1024 * 1024
MAX_SSE_IDENTITY_PREFIX_BYTES = 256 * 1024
MAX_SSE_TOTAL_BYTES = 64 * 1024 * 1024
MAX_STREAM_TEXT_BYTES = 32 * 1024 * 1024
MAX_TOTAL_TOOL_ARGUMENT_BYTES = 8 * 1024 * 1024
MAX_STREAM_TOOL_ARGUMENT_BYTES = MAX_TOTAL_TOOL_ARGUMENT_BYTES
MAX_ACCEPTED_RESPONSE_MODELS = 16
MAX_TOKEN_COUNT = (1 << 63) - 1
MAX_CONTEXT_WINDOW = 10_000_000

_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MODEL_ID_PATTERN = re.compile(r"^[!-~]{1,256}$")
_TOOL_SUPPORT_VALUES = frozenset({"none", "single", "parallel"})
_TOOL_CHOICE_MODES = frozenset({"auto", "none", "required", "named"})
_FINISH_REASON_MAP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
}


class AdapterError(ValueError):
    """A sanitized protocol error, optionally after stream output was committed."""

    def __init__(self, message: str, *, output_committed: bool = False) -> None:
        super().__init__(message)
        self.output_committed = output_committed
        self.committed = output_committed


@dataclass(frozen=True)
class OpenModelCapabilities:
    context_window: int
    max_output_tokens: int
    supports_streaming: bool
    tool_support: Literal["none", "single", "parallel"]
    declared_tool_choice_modes: Collection[str]

    def __post_init__(self) -> None:
        if (
            type(self.context_window) is not int
            or not 1 <= self.context_window <= MAX_CONTEXT_WINDOW
        ):
            raise AdapterError("open-model context window is invalid")
        if (
            type(self.max_output_tokens) is not int
            or not 1 <= self.max_output_tokens <= self.context_window
        ):
            raise AdapterError("open-model output limit is invalid")
        if type(self.supports_streaming) is not bool:
            raise AdapterError("open-model streaming capability is invalid")
        if type(self.tool_support) is not str or self.tool_support not in _TOOL_SUPPORT_VALUES:
            raise AdapterError("open-model tool capability is invalid")
        modes = self.declared_tool_choice_modes
        if not isinstance(modes, (tuple, list, set, frozenset)) or len(modes) > len(_TOOL_CHOICE_MODES):
            raise AdapterError("open-model tool-choice capabilities are invalid")
        try:
            frozen_modes = frozenset(modes)
        except TypeError as exc:
            raise AdapterError(
                "open-model tool-choice capabilities are invalid"
            ) from exc
        if (
            any(type(mode) is not str for mode in frozen_modes)
            or not frozen_modes <= _TOOL_CHOICE_MODES
        ):
            raise AdapterError("open-model tool-choice capabilities are invalid")
        object.__setattr__(self, "declared_tool_choice_modes", frozen_modes)


@dataclass(frozen=True)
class OpenModelResponseConstraints:
    """Immutable response rules derived from one translated request."""

    declared_tool_names: Collection[str]
    tool_choice_mode: Literal["auto", "none", "required", "named"]
    required_tool_name: str | None
    max_tool_calls: int

    def __post_init__(self) -> None:
        names = self.declared_tool_names
        if (
            not isinstance(names, (tuple, list, set, frozenset))
            or len(names) > MAX_TOOLS
        ):
            raise AdapterError("open-model response constraints are invalid")
        try:
            frozen_names = frozenset(names)
        except TypeError as exc:
            raise AdapterError("open-model response constraints are invalid") from exc
        if any(
            type(name) is not str or _TOOL_NAME_PATTERN.fullmatch(name) is None
            for name in frozen_names
        ):
            raise AdapterError("open-model response constraints are invalid")
        if (
            type(self.tool_choice_mode) is not str
            or self.tool_choice_mode not in _TOOL_CHOICE_MODES
        ):
            raise AdapterError("open-model response constraints are invalid")
        if (
            type(self.max_tool_calls) is not int
            or not 0 <= self.max_tool_calls <= MAX_TOOL_CALLS
        ):
            raise AdapterError("open-model response constraints are invalid")
        if self.tool_choice_mode == "named":
            if (
                type(self.required_tool_name) is not str
                or self.required_tool_name not in frozen_names
            ):
                raise AdapterError("open-model response constraints are invalid")
        elif self.required_tool_name is not None:
            raise AdapterError("open-model response constraints are invalid")
        if not frozen_names:
            if self.tool_choice_mode != "none" or self.max_tool_calls != 0:
                raise AdapterError("open-model response constraints are invalid")
        elif self.tool_choice_mode == "none":
            if self.max_tool_calls != 0:
                raise AdapterError("open-model response constraints are invalid")
        elif self.max_tool_calls == 0:
            raise AdapterError("open-model response constraints are invalid")
        object.__setattr__(self, "declared_tool_names", frozen_names)


class _DuplicateKeyError(ValueError):
    pass


class _TextBudget:
    def __init__(self) -> None:
        self.total = 0

    def add_bytes(self, size: int, location: str, *, limit: int) -> None:
        if size > limit:
            raise AdapterError(f"{location} is too large")
        self.total += size
        if self.total > MAX_TOTAL_TEXT_BYTES:
            raise AdapterError("request content is too large")

    def add(self, value: str, location: str, *, limit: int = MAX_TEXT_BYTES) -> str:
        self.add_bytes(len(_utf8(value, location)), location, limit=limit)
        return value


def _utf8(value: str, location: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise AdapterError(f"{location} is invalid") from exc


def _bounded_string(
    value: Any,
    location: str,
    *,
    allow_empty: bool = True,
    max_bytes: int = MAX_TEXT_BYTES,
) -> str:
    if type(value) is not str or (not allow_empty and not value):
        raise AdapterError(f"{location} is invalid")
    if len(_utf8(value, location)) > max_bytes:
        raise AdapterError(f"{location} is too large")
    return value


def _model_id(value: Any, location: str) -> str:
    if type(value) is not str or _MODEL_ID_PATTERN.fullmatch(value) is None:
        raise AdapterError(f"{location} is invalid")
    return value


def _private_model_identity(value: Any, location: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > MAX_PRIVATE_MODEL_ID_CHARS
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise AdapterError(f"{location} is invalid")
    if len(_utf8(value, location)) > MAX_PRIVATE_MODEL_ID_BYTES:
        raise AdapterError(f"{location} is too large")
    return value


def _tool_name(value: Any, location: str) -> str:
    if type(value) is not str or _TOOL_NAME_PATTERN.fullmatch(value) is None:
        raise AdapterError(f"{location} is invalid")
    return value


def _tool_id(value: Any, location: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= MAX_TOOL_ID_CHARS
        or not value.isascii()
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
    ):
        raise AdapterError(f"{location} is invalid")
    return value


def _upstream_tool_id(value: Any, location: str) -> str:
    value = _bounded_string(value, location, allow_empty=False, max_bytes=1024)
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise AdapterError(f"{location} is invalid")
    return value


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    raise ValueError


def _clone_json(
    value: Any,
    location: str,
    *,
    depth: int = 0,
    count: list[int] | None = None,
) -> Any:
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
        raise AdapterError(f"{location} is too complex")
    if value is None or type(value) is bool or type(value) is str:
        if type(value) is str:
            _bounded_string(value, location)
        return value
    if type(value) is int:
        if value.bit_length() > MAX_JSON_INTEGER_BITS:
            raise AdapterError(f"{location} contains an oversized integer")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise AdapterError(f"{location} contains a non-finite number")
        return value
    if type(value) is list:
        return [
            _clone_json(item, location, depth=depth + 1, count=count)
            for item in value
        ]
    if type(value) is dict:
        result: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise AdapterError(f"{location} contains a non-string key")
            _bounded_string(key, location)
            result[key] = _clone_json(
                item, location, depth=depth + 1, count=count
            )
        return result
    raise AdapterError(f"{location} is not JSON-compatible")


def _json_bytes(value: Any, location: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise AdapterError(f"{location} is not valid JSON") from exc


def _strict_json_object(raw: str, location: str, *, max_bytes: int) -> dict[str, Any]:
    if len(_utf8(raw, location)) > max_bytes:
        raise AdapterError(f"{location} is too large")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except (_DuplicateKeyError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise AdapterError(f"{location} is invalid JSON") from exc
    if type(value) is not dict:
        raise AdapterError(f"{location} must be a JSON object")
    return _clone_json(value, location)


def _validate_cache_control(value: Any, location: str) -> None:
    if type(value) is not dict or not {"type"} <= set(value) <= {"type", "ttl"}:
        raise AdapterError(f"{location} is invalid")
    if value.get("type") != "ephemeral":
        raise AdapterError(f"{location} is invalid")
    if "ttl" in value and (
        type(value["ttl"]) is not str or value["ttl"] not in {"5m", "1h"}
    ):
        raise AdapterError(f"{location} is invalid")


def _text_block(
    block: Any,
    location: str,
    budget: _TextBudget,
    *,
    allow_cache_control: bool,
) -> str:
    if type(block) is not dict:
        raise AdapterError(f"{location} is invalid")
    allowed = {"type", "text"}
    if allow_cache_control:
        allowed.add("cache_control")
    if set(block) - allowed or set(block) < {"type", "text"}:
        raise AdapterError(f"{location} contains unsupported fields")
    if block.get("type") != "text":
        raise AdapterError(f"{location} is not text")
    if "cache_control" in block:
        _validate_cache_control(block["cache_control"], f"{location} cache control")
    text = _bounded_string(block.get("text"), f"{location} text")
    return budget.add(text, f"{location} text")


def _system_parts(value: Any, budget: _TextBudget) -> list[str]:
    if type(value) is str:
        return [budget.add(_bounded_string(value, "system"), "system")]
    if type(value) is not list or not value or len(value) > MAX_CONTENT_BLOCKS:
        raise AdapterError("system content is invalid")
    return [
        _text_block(
            block,
            f"system block {index}",
            budget,
            allow_cache_control=True,
        )
        for index, block in enumerate(value)
    ]


def _lift_system_messages(
    messages: list[Any], budget: _TextBudget
) -> tuple[list[Any], list[str]]:
    retained: list[Any] = []
    lifted: list[str] = []
    seen_non_system = False
    for index, message in enumerate(messages):
        if type(message) is not dict or message.get("role") != "system":
            retained.append(message)
            seen_non_system = True
            continue
        if seen_non_system:
            raise AdapterError("system-role messages must precede message history")
        if set(message) != {"role", "content"}:
            raise AdapterError("system-role messages contain unsupported fields")
        content = message.get("content")
        if type(content) is str:
            if len(lifted) >= MAX_CONTENT_BLOCKS:
                raise AdapterError("too many lifted system text blocks")
            lifted.append(
                budget.add(
                    _bounded_string(content, f"system-role message {index}"),
                    f"system-role message {index}",
                )
            )
            continue
        if type(content) is not list or not content or len(content) > MAX_CONTENT_BLOCKS:
            raise AdapterError("system-role message content is unsupported")
        if len(lifted) + len(content) > MAX_CONTENT_BLOCKS:
            raise AdapterError("too many lifted system text blocks")
        for block_index, block in enumerate(content):
            lifted.append(
                _text_block(
                    block,
                    f"system-role message {index} block {block_index}",
                    budget,
                    allow_cache_control=False,
                )
            )
    if lifted and not retained:
        raise AdapterError("messages must include a user or assistant message")
    return retained, lifted


def _tool_result_content(value: Any, location: str, budget: _TextBudget) -> str:
    if type(value) is str:
        return budget.add(_bounded_string(value, location), location)
    if type(value) is not list or len(value) > MAX_CONTENT_BLOCKS:
        raise AdapterError(f"{location} is invalid")
    return "".join(
        _text_block(
            block,
            f"{location} block {index}",
            budget,
            allow_cache_control=True,
        )
        for index, block in enumerate(value)
    )


def _assistant_message(
    content: Any,
    location: str,
    budget: _TextBudget,
    capabilities: OpenModelCapabilities,
    known_tool_ids: set[str],
) -> dict[str, Any]:
    if type(content) is str:
        text = budget.add(_bounded_string(content, location), location)
        return {"role": "assistant", "content": text}
    if type(content) is not list or not content or len(content) > MAX_CONTENT_BLOCKS:
        raise AdapterError(f"{location} is invalid")
    text_parts: list[str] = []
    calls: list[dict[str, Any]] = []
    for index, block in enumerate(content):
        block_location = f"{location} block {index}"
        if type(block) is not dict or type(block.get("type")) is not str:
            raise AdapterError(f"{block_location} is invalid")
        block_type = block["type"]
        if block_type == "text":
            # Chat Completions has one assistant content field, which always
            # precedes its tool_calls. It cannot preserve text after or between
            # tool_use blocks, so reject that history rather than reordering it.
            if calls:
                raise AdapterError(
                    "assistant text after a tool use is unsupported by Chat Completions"
                )
            text_parts.append(
                _text_block(
                    block,
                    block_location,
                    budget,
                    allow_cache_control=True,
                )
            )
            continue
        if block_type != "tool_use":
            raise AdapterError(f"{block_location} type is unsupported")
        if capabilities.tool_support == "none":
            raise AdapterError("tool history is unsupported by this route")
        allowed = {"type", "id", "name", "input", "cache_control"}
        if set(block) != {"type", "id", "name", "input"} and not (
            set(block) == allowed
        ):
            raise AdapterError(f"{block_location} contains unsupported fields")
        if "cache_control" in block:
            _validate_cache_control(
                block["cache_control"], f"{block_location} cache control"
            )
        call_id = _tool_id(block.get("id"), f"{block_location} id")
        if call_id in known_tool_ids:
            raise AdapterError("tool-use IDs must be unique")
        known_tool_ids.add(call_id)
        name = _tool_name(block.get("name"), f"{block_location} name")
        input_value = block.get("input")
        if type(input_value) is not dict:
            raise AdapterError(f"{block_location} input must be a JSON object")
        cloned_input = _clone_json(input_value, f"{block_location} input")
        arguments = _json_bytes(cloned_input, f"{block_location} input").decode("ascii")
        budget.add_bytes(
            len(arguments),
            f"{block_location} input",
            limit=MAX_TOOL_ARGUMENT_BYTES,
        )
        calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
    if not text_parts and not calls:
        raise AdapterError(f"{location} is empty")
    if capabilities.tool_support == "single" and len(calls) > 1:
        raise AdapterError("parallel tool history is unsupported by this route")
    if len(calls) > MAX_TOOL_CALLS:
        raise AdapterError("too many tool calls in one assistant message")
    result: dict[str, Any] = {"role": "assistant"}
    if calls:
        result["content"] = "".join(text_parts) if text_parts else None
        result["tool_calls"] = calls
    else:
        result["content"] = "".join(text_parts)
    return result


def _user_messages(
    content: Any,
    location: str,
    budget: _TextBudget,
    capabilities: OpenModelCapabilities,
    known_tool_ids: set[str],
    answered_tool_ids: set[str],
    pending_tool_ids: set[str],
) -> list[dict[str, Any]]:
    if type(content) is str:
        if pending_tool_ids:
            raise AdapterError(
                "tool results must immediately follow their assistant tool uses"
            )
        text = budget.add(_bounded_string(content, location), location)
        return [{"role": "user", "content": text}]
    if type(content) is not list or not content or len(content) > MAX_CONTENT_BLOCKS:
        raise AdapterError(f"{location} is invalid")
    result: list[dict[str, Any]] = []
    text_parts: list[str] = []
    remaining_tool_ids = set(pending_tool_ids)

    def flush_text() -> None:
        if text_parts:
            result.append({"role": "user", "content": "".join(text_parts)})
            text_parts.clear()

    for index, block in enumerate(content):
        block_location = f"{location} block {index}"
        if type(block) is not dict or type(block.get("type")) is not str:
            raise AdapterError(f"{block_location} is invalid")
        block_type = block["type"]
        if block_type == "text":
            if remaining_tool_ids:
                raise AdapterError(
                    "tool results must precede user text and answer every pending tool use"
                )
            text_parts.append(
                _text_block(
                    block,
                    block_location,
                    budget,
                    allow_cache_control=True,
                )
            )
            continue
        if block_type != "tool_result":
            raise AdapterError(f"{block_location} type is unsupported")
        if capabilities.tool_support == "none":
            raise AdapterError("tool history is unsupported by this route")
        allowed = {"type", "tool_use_id", "content", "is_error", "cache_control"}
        required = {"type", "tool_use_id"}
        if set(block) - allowed or not required <= set(block):
            raise AdapterError(f"{block_location} contains unsupported fields")
        if "is_error" in block and type(block["is_error"]) is not bool:
            raise AdapterError(f"{block_location} is_error is invalid")
        # Chat Completions tool messages have no error flag. Prefixing or
        # otherwise rewriting content would invent semantics, so failed tool
        # results are rejected instead of being represented as successes.
        if block.get("is_error") is True:
            raise AdapterError(
                "failed tool results are unsupported by Chat Completions"
            )
        if "cache_control" in block:
            _validate_cache_control(
                block["cache_control"], f"{block_location} cache control"
            )
        call_id = _tool_id(block.get("tool_use_id"), f"{block_location} tool ID")
        if call_id not in known_tool_ids:
            raise AdapterError("tool result does not match an earlier tool use")
        if call_id in answered_tool_ids:
            raise AdapterError("tool result is duplicated")
        if not pending_tool_ids:
            raise AdapterError(
                "tool results must immediately follow their assistant tool uses"
            )
        if text_parts or call_id not in remaining_tool_ids:
            raise AdapterError(
                "tool results must immediately answer the pending assistant tool uses"
            )
        answered_tool_ids.add(call_id)
        remaining_tool_ids.remove(call_id)
        tool_content = (
            _tool_result_content(
                block["content"], f"{block_location} content", budget
            )
            if "content" in block
            else ""
        )
        result.append(
            {"role": "tool", "tool_call_id": call_id, "content": tool_content}
        )
    if remaining_tool_ids:
        raise AdapterError("every pending tool use must have an immediate result")
    flush_text()
    if not result:
        raise AdapterError(f"{location} is empty")
    return result


def _translate_tools(
    value: Any, budget: _TextBudget
) -> tuple[list[dict[str, Any]], set[str]]:
    if type(value) is not list or len(value) > MAX_TOOLS:
        raise AdapterError("tools are invalid")
    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, tool in enumerate(value):
        location = f"tool {index}"
        if type(tool) is not dict:
            raise AdapterError(f"{location} is invalid")
        allowed = {"type", "name", "description", "input_schema", "cache_control"}
        required = {"name", "input_schema"}
        if set(tool) - allowed or not required <= set(tool):
            raise AdapterError(f"{location} contains unsupported fields")
        if "type" in tool and tool["type"] != "custom":
            raise AdapterError(f"{location} is not a client function tool")
        if "cache_control" in tool:
            _validate_cache_control(tool["cache_control"], f"{location} cache control")
        name = _tool_name(tool.get("name"), f"{location} name")
        if name in names:
            raise AdapterError("tool names must be unique")
        names.add(name)
        function: dict[str, Any] = {"name": name}
        if "description" in tool:
            description = _bounded_string(tool["description"], f"{location} description")
            function["description"] = budget.add(
                description, f"{location} description"
            )
        schema = tool.get("input_schema")
        if type(schema) is not dict or schema.get("type") != "object":
            raise AdapterError(f"{location} input schema must describe an object")
        cloned_schema = _clone_json(schema, f"{location} input schema")
        schema_bytes = _json_bytes(cloned_schema, f"{location} input schema")
        budget.add_bytes(
            len(schema_bytes),
            f"{location} input schema",
            limit=MAX_SCHEMA_BYTES,
        )
        function["parameters"] = cloned_schema
        result.append({"type": "function", "function": function})
    return result, names


def _translate_tool_choice(
    value: Any,
    *,
    capabilities: OpenModelCapabilities,
    tool_names: set[str],
) -> tuple[Any, bool | None]:
    if type(value) is not dict or type(value.get("type")) is not str:
        raise AdapterError("tool choice is invalid")
    choice_type = value["type"]
    allowed = {"type", "disable_parallel_tool_use"}
    if choice_type == "tool":
        allowed.add("name")
    if set(value) - allowed:
        raise AdapterError("tool choice contains unsupported fields")
    if "disable_parallel_tool_use" in value and type(value["disable_parallel_tool_use"]) is not bool:
        raise AdapterError("tool choice parallel setting is invalid")
    disable_parallel = value.get("disable_parallel_tool_use", False)
    if choice_type == "auto":
        mapped_mode = "auto"
        mapped: Any = "auto"
    elif choice_type == "none":
        if "name" in value or "disable_parallel_tool_use" in value:
            raise AdapterError("none tool choice contains unsupported fields")
        mapped_mode = "none"
        mapped = "none"
    elif choice_type == "any":
        mapped_mode = "required"
        mapped = "required"
    elif choice_type == "tool":
        if "name" not in value:
            raise AdapterError("named tool choice is missing a name")
        name = _tool_name(value["name"], "named tool choice")
        if name not in tool_names:
            raise AdapterError("named tool choice does not match a declared tool")
        mapped_mode = "named"
        mapped = {"type": "function", "function": {"name": name}}
    else:
        raise AdapterError("tool choice type is unsupported")
    if mapped_mode not in capabilities.declared_tool_choice_modes:
        raise AdapterError("tool choice mode is unsupported by this route")
    parallel: bool | None = None
    if mapped_mode != "none":
        if capabilities.tool_support == "parallel":
            parallel = not disable_parallel
        elif not disable_parallel and "disable_parallel_tool_use" in value:
            raise AdapterError("parallel tool calls are unsupported by this route")
    return mapped, parallel


def _validate_metadata(value: Any) -> None:
    if type(value) is not dict or set(value) - {"user_id"}:
        raise AdapterError("metadata is invalid")
    if "user_id" in value:
        _bounded_string(
            value["user_id"], "metadata user ID", allow_empty=False, max_bytes=256
        )


def _finite_number(value: Any, location: str, minimum: float, maximum: float) -> int | float:
    if type(value) is int:
        valid = minimum <= value <= maximum
    elif type(value) is float:
        valid = math.isfinite(value) and minimum <= value <= maximum
    else:
        raise AdapterError(f"{location} is invalid")
    if not valid:
        raise AdapterError(f"{location} is out of range")
    return value


def translate_request(
    anthropic_body: Any,
    *,
    upstream_model: str,
    capabilities: OpenModelCapabilities,
) -> dict[str, Any]:
    """Translate one validated Messages request into a fresh OpenAI payload."""

    if not isinstance(capabilities, OpenModelCapabilities):
        raise AdapterError("open-model capabilities are invalid")
    upstream_model = _private_model_identity(upstream_model, "upstream model")
    if type(anthropic_body) is not dict:
        raise AdapterError("Messages request must be an object")
    if any(type(key) is not str for key in anthropic_body):
        raise AdapterError("Messages request fields are invalid")
    allowed_fields = {
        "model",
        "max_tokens",
        "messages",
        "system",
        "tools",
        "tool_choice",
        "temperature",
        "top_p",
        "stop_sequences",
        "stream",
        "metadata",
    }
    unknown = set(anthropic_body) - allowed_fields
    if unknown:
        raise AdapterError(
            f"Messages request field {sorted(unknown)[0]!r} is unsupported"
        )
    required = {"model", "max_tokens", "messages"}
    if not required <= set(anthropic_body):
        raise AdapterError("Messages request is missing a required field")
    _model_id(anthropic_body.get("model"), "Messages model")
    max_tokens = anthropic_body.get("max_tokens")
    if type(max_tokens) is not int or max_tokens <= 0:
        raise AdapterError("max_tokens is invalid")
    messages_value = anthropic_body.get("messages")
    if (
        type(messages_value) is not list
        or not messages_value
        or len(messages_value) > MAX_MESSAGES
    ):
        raise AdapterError("messages are invalid")
    if "metadata" in anthropic_body:
        _validate_metadata(anthropic_body["metadata"])

    budget = _TextBudget()
    system_parts = (
        _system_parts(anthropic_body["system"], budget)
        if "system" in anthropic_body
        else []
    )
    messages, lifted = _lift_system_messages(messages_value, budget)
    if len(system_parts) + len(lifted) > MAX_CONTENT_BLOCKS:
        raise AdapterError("too many system text blocks")
    system_parts.extend(lifted)
    if not messages:
        raise AdapterError("messages must include a user or assistant message")

    translated_tools: list[dict[str, Any]] | None = None
    tool_names: set[str] = set()
    if "tools" in anthropic_body:
        validated_tools, validated_tool_names = _translate_tools(
            anthropic_body["tools"], budget
        )
        if capabilities.tool_support == "none":
            raise AdapterError("tools are unsupported by this route")
        translated_tools = validated_tools
        tool_names = validated_tool_names

    payload: dict[str, Any] = {
        "model": upstream_model,
        "messages": [
            {"role": "system", "content": part} for part in system_parts
        ],
        "max_tokens": min(max_tokens, capabilities.max_output_tokens),
    }
    known_tool_ids: set[str] = set()
    answered_tool_ids: set[str] = set()
    pending_tool_ids: set[str] = set()
    first_role: str | None = None
    for index, message in enumerate(messages):
        location = f"message {index}"
        if type(message) is not dict or set(message) != {"role", "content"}:
            raise AdapterError(f"{location} is invalid")
        role = message.get("role")
        if type(role) is not str or role not in {"user", "assistant"}:
            raise AdapterError(f"{location} role is unsupported")
        if first_role is None:
            first_role = role
        if role == "assistant":
            if pending_tool_ids:
                raise AdapterError(
                    "tool results must immediately follow their assistant tool uses"
                )
            translated_assistant = _assistant_message(
                message.get("content"),
                f"{location} content",
                budget,
                capabilities,
                known_tool_ids,
            )
            payload["messages"].append(translated_assistant)
            pending_tool_ids = {
                call["id"] for call in translated_assistant.get("tool_calls", [])
            }
        else:
            payload["messages"].extend(
                _user_messages(
                    message.get("content"),
                    f"{location} content",
                    budget,
                    capabilities,
                    known_tool_ids,
                    answered_tool_ids,
                    pending_tool_ids,
                )
            )
            pending_tool_ids.clear()
        if len(payload["messages"]) > MAX_TRANSLATED_MESSAGES:
            raise AdapterError("translated request has too many messages")
    if first_role != "user":
        raise AdapterError("Messages history must start with a user message")
    if pending_tool_ids:
        raise AdapterError("every pending tool use must have an immediate result")

    if translated_tools:
        payload["tools"] = translated_tools
    if "tool_choice" in anthropic_body:
        if capabilities.tool_support == "none":
            raise AdapterError("tool choice is unsupported by this route")
        if not translated_tools:
            raise AdapterError("tool choice requires declared tools")
        mapped_choice, parallel = _translate_tool_choice(
            anthropic_body["tool_choice"],
            capabilities=capabilities,
            tool_names=tool_names,
        )
        payload["tool_choice"] = mapped_choice
        if parallel is not None:
            payload["parallel_tool_calls"] = parallel
    elif translated_tools:
        if "auto" not in capabilities.declared_tool_choice_modes:
            raise AdapterError("automatic tool choice is unsupported by this route")
        payload["tool_choice"] = "auto"
        if capabilities.tool_support == "parallel":
            payload["parallel_tool_calls"] = True
    if "temperature" in anthropic_body:
        payload["temperature"] = _finite_number(
            anthropic_body["temperature"], "temperature", 0.0, 1.0
        )
    if "top_p" in anthropic_body:
        payload["top_p"] = _finite_number(
            anthropic_body["top_p"], "top_p", 0.0, 1.0
        )
    if "stop_sequences" in anthropic_body:
        stops = anthropic_body["stop_sequences"]
        if type(stops) is not list or len(stops) > MAX_STOP_SEQUENCES:
            raise AdapterError("stop_sequences is invalid")
        if stops:
            payload["stop"] = [
                _bounded_string(
                    stop,
                    f"stop sequence {index}",
                    allow_empty=False,
                    max_bytes=MAX_STOP_SEQUENCE_BYTES,
                )
                for index, stop in enumerate(stops)
            ]
    if "stream" in anthropic_body:
        stream = anthropic_body["stream"]
        if type(stream) is not bool:
            raise AdapterError("stream is invalid")
        if stream and not capabilities.supports_streaming:
            raise AdapterError("streaming is unsupported by this route")
        payload["stream"] = stream
        if stream:
            payload["stream_options"] = {"include_usage": True}

    if len(_json_bytes(payload, "translated request")) > MAX_REQUEST_JSON_BYTES:
        raise AdapterError("translated request is too large")
    return payload


def derive_response_constraints(
    translated_request: Any,
) -> OpenModelResponseConstraints:
    """Freeze response rules from one freshly translated Chat Completions request."""

    if type(translated_request) is not dict:
        raise AdapterError("translated request response constraints are invalid")
    if "tools" not in translated_request:
        if "tool_choice" in translated_request or "parallel_tool_calls" in translated_request:
            raise AdapterError("translated request response constraints are invalid")
        return OpenModelResponseConstraints(
            declared_tool_names=frozenset(),
            tool_choice_mode="none",
            required_tool_name=None,
            max_tool_calls=0,
        )

    tools = translated_request["tools"]
    if type(tools) is not list or not tools or len(tools) > MAX_TOOLS:
        raise AdapterError("translated request response constraints are invalid")
    names: set[str] = set()
    for tool in tools:
        if type(tool) is not dict or set(tool) != {"type", "function"}:
            raise AdapterError("translated request response constraints are invalid")
        function = tool.get("function")
        if tool.get("type") != "function" or type(function) is not dict:
            raise AdapterError("translated request response constraints are invalid")
        if not {"name", "parameters"} <= set(function) <= {
            "name",
            "description",
            "parameters",
        }:
            raise AdapterError("translated request response constraints are invalid")
        try:
            name = _tool_name(function.get("name"), "translated tool name")
        except AdapterError as exc:
            raise AdapterError("translated request response constraints are invalid") from exc
        if name in names:
            raise AdapterError("translated request response constraints are invalid")
        names.add(name)

    choice = translated_request.get("tool_choice")
    required_name: str | None = None
    if type(choice) is str and choice in {"auto", "none", "required"}:
        mode = choice
    elif type(choice) is dict and set(choice) == {"type", "function"}:
        function = choice.get("function")
        if (
            choice.get("type") != "function"
            or type(function) is not dict
            or set(function) != {"name"}
        ):
            raise AdapterError("translated request response constraints are invalid")
        try:
            required_name = _tool_name(
                function.get("name"), "translated named tool choice"
            )
        except AdapterError as exc:
            raise AdapterError("translated request response constraints are invalid") from exc
        if required_name not in names:
            raise AdapterError("translated request response constraints are invalid")
        mode = "named"
    else:
        raise AdapterError("translated request response constraints are invalid")

    parallel = translated_request.get("parallel_tool_calls")
    if "parallel_tool_calls" in translated_request and type(parallel) is not bool:
        raise AdapterError("translated request response constraints are invalid")
    if mode == "none":
        if parallel is True:
            raise AdapterError("translated request response constraints are invalid")
        max_tool_calls = 0
    else:
        max_tool_calls = 1 if parallel is False else MAX_TOOL_CALLS
    return OpenModelResponseConstraints(
        declared_tool_names=names,
        tool_choice_mode=mode,
        required_tool_name=required_name,
        max_tool_calls=max_tool_calls,
    )


def _accepted_models(value: Collection[str]) -> frozenset[str]:
    if (
        not isinstance(value, (tuple, list, set, frozenset))
        or not value
        or len(value) > MAX_ACCEPTED_RESPONSE_MODELS
    ):
        raise AdapterError("accepted response model identities are invalid")
    try:
        models = frozenset(value)
    except TypeError as exc:
        raise AdapterError("accepted response model identities are invalid") from exc
    if not models:
        raise AdapterError("accepted response model identities are empty")
    for model in models:
        try:
            _private_model_identity(model, "accepted response model identity")
        except AdapterError as exc:
            raise AdapterError("accepted response model identities are invalid") from exc
    return models


def _validate_response_model(value: Any, accepted: frozenset[str]) -> None:
    if type(value) is not str or value not in accepted:
        raise AdapterError("upstream response model identity mismatch")


def _nonnegative_integer(value: Any, location: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_TOKEN_COUNT:
        raise AdapterError(f"{location} is invalid")
    return value


def _usage(value: Any) -> dict[str, int]:
    if type(value) is not dict:
        raise AdapterError("upstream usage is invalid")
    prompt = _nonnegative_integer(value.get("prompt_tokens"), "prompt token usage")
    completion = _nonnegative_integer(
        value.get("completion_tokens"), "completion token usage"
    )
    if "total_tokens" in value:
        total = _nonnegative_integer(value["total_tokens"], "total token usage")
        if total != prompt + completion:
            raise AdapterError("total token usage is inconsistent")
    cached = 0
    if "prompt_tokens_details" in value and value["prompt_tokens_details"] is not None:
        details = value["prompt_tokens_details"]
        if type(details) is not dict:
            raise AdapterError("cached prompt token usage is invalid")
        if "cached_tokens" in details:
            cached = _nonnegative_integer(
                details["cached_tokens"], "cached prompt token usage"
            )
    if cached > prompt:
        raise AdapterError("cached prompt token usage is invalid")
    return {
        "input_tokens": prompt,
        "output_tokens": completion,
        "cache_read_input_tokens": cached,
    }


def _safe_message_id() -> str:
    return "msg_" + secrets.token_hex(12)


def _safe_tool_use_id() -> str:
    return "toolu_" + secrets.token_hex(12)


def _response_tool_blocks(
    value: Any,
    capabilities: OpenModelCapabilities,
    response_constraints: OpenModelResponseConstraints,
) -> list[dict[str, Any]]:
    if type(value) is not list or not value or len(value) > MAX_TOOL_CALLS:
        raise AdapterError("upstream tool calls are invalid")
    if capabilities.tool_support == "none":
        raise AdapterError("upstream returned tools unsupported by this route")
    if capabilities.tool_support == "single" and len(value) > 1:
        raise AdapterError("upstream returned parallel tools unsupported by this route")
    if len(value) > response_constraints.max_tool_calls:
        raise AdapterError("upstream tool calls violate the request constraints")
    blocks: list[dict[str, Any]] = []
    total_argument_bytes = 0
    for index, call in enumerate(value):
        location = f"upstream tool call {index}"
        if type(call) is not dict or set(call) - {"id", "type", "function", "index"}:
            raise AdapterError(f"{location} is invalid")
        if "index" in call and (type(call["index"]) is not int or call["index"] != index):
            raise AdapterError(f"{location} index is invalid")
        _upstream_tool_id(call.get("id"), f"{location} ID")
        if call.get("type") != "function" or type(call.get("function")) is not dict:
            raise AdapterError(f"{location} is not a function call")
        function = call["function"]
        if set(function) != {"name", "arguments"}:
            raise AdapterError(f"{location} function is invalid")
        name = _tool_name(function.get("name"), f"{location} name")
        if name not in response_constraints.declared_tool_names:
            raise AdapterError("upstream returned an undeclared tool")
        if (
            response_constraints.required_tool_name is not None
            and name != response_constraints.required_tool_name
        ):
            raise AdapterError("upstream returned a tool other than the forced tool")
        arguments = _bounded_string(
            function.get("arguments"),
            f"{location} arguments",
            max_bytes=MAX_TOOL_ARGUMENT_BYTES,
        )
        total_argument_bytes += len(_utf8(arguments, f"{location} arguments"))
        if total_argument_bytes > MAX_TOTAL_TOOL_ARGUMENT_BYTES:
            raise AdapterError("upstream tool-call arguments are too large")
        parsed = _strict_json_object(
            arguments,
            f"{location} arguments",
            max_bytes=MAX_TOOL_ARGUMENT_BYTES,
        )
        blocks.append(
            {
                "type": "tool_use",
                "id": _safe_tool_use_id(),
                "name": name,
                "input": parsed,
            }
        )
    return blocks


def translate_nonstream_response(
    chat_completion: Any,
    *,
    wire_model: str,
    accepted_response_models: Collection[str],
    capabilities: OpenModelCapabilities,
    response_constraints: OpenModelResponseConstraints,
) -> dict[str, Any]:
    """Translate one non-streaming Chat Completion into a Messages response."""

    if not isinstance(capabilities, OpenModelCapabilities):
        raise AdapterError("open-model capabilities are invalid")
    if not isinstance(response_constraints, OpenModelResponseConstraints):
        raise AdapterError("open-model response constraints are invalid")
    wire_model = _model_id(wire_model, "wire model")
    accepted = _accepted_models(accepted_response_models)
    if type(chat_completion) is not dict:
        raise AdapterError("upstream response is invalid")
    if len(_json_bytes(chat_completion, "upstream response")) > MAX_RESPONSE_JSON_BYTES:
        raise AdapterError("upstream response is too large")
    if chat_completion.get("object") != "chat.completion":
        raise AdapterError("upstream response object is invalid")
    _bounded_string(
        chat_completion.get("id"),
        "upstream response ID",
        allow_empty=False,
        max_bytes=1024,
    )
    created = chat_completion.get("created")
    if type(created) is not int or not 0 <= created <= MAX_TOKEN_COUNT:
        raise AdapterError("upstream response timestamp is invalid")
    _validate_response_model(chat_completion.get("model"), accepted)
    choices = chat_completion.get("choices")
    if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
        raise AdapterError("upstream response must contain exactly one choice")
    choice = choices[0]
    if type(choice.get("index")) is not int or choice["index"] != 0:
        raise AdapterError("upstream response choice index is invalid")
    if set(choice) - {"index", "message", "finish_reason", "logprobs"}:
        raise AdapterError("upstream response choice contains unsupported fields")
    if "logprobs" in choice and choice["logprobs"] is not None:
        raise AdapterError("upstream response log probabilities are unsupported")
    finish_reason = choice.get("finish_reason")
    if type(finish_reason) is not str or finish_reason not in _FINISH_REASON_MAP:
        raise AdapterError("upstream finish reason is unsupported")
    message = choice.get("message")
    if (
        type(message) is not dict
        or not {"role", "content"} <= set(message)
        or message.get("role") != "assistant"
    ):
        raise AdapterError("upstream assistant message is invalid")
    allowed_message_fields = {"role", "content", "tool_calls", "reasoning_content"}
    if set(message) - allowed_message_fields:
        raise AdapterError("upstream assistant message contains unsupported fields")
    if "reasoning_content" in message and message["reasoning_content"] is not None:
        _bounded_string(message["reasoning_content"], "upstream reasoning content")
    content = message.get("content")
    if content is not None and type(content) is not str:
        raise AdapterError("upstream visible content is invalid")
    blocks: list[dict[str, Any]] = []
    if content:
        _bounded_string(content, "upstream visible content")
        blocks.append({"type": "text", "text": content})
    tool_blocks: list[dict[str, Any]] = []
    if "tool_calls" in message and message["tool_calls"] is not None:
        tool_blocks = _response_tool_blocks(
            message["tool_calls"], capabilities, response_constraints
        )
        blocks.extend(tool_blocks)
    if finish_reason == "tool_calls" and not tool_blocks:
        raise AdapterError("tool-call finish reason has no tool call")
    if tool_blocks and finish_reason != "tool_calls":
        raise AdapterError("upstream tool calls have an inconsistent finish reason")
    if (
        response_constraints.tool_choice_mode in {"required", "named"}
        and not tool_blocks
    ):
        raise AdapterError("upstream response omitted a required tool call")
    return {
        "id": _safe_message_id(),
        "type": "message",
        "role": "assistant",
        "model": wire_model,
        "content": blocks,
        "stop_reason": _FINISH_REASON_MAP[finish_reason],
        "stop_sequence": None,
        "usage": _usage(chat_completion.get("usage")),
    }


def _sse_frame(event: str, value: dict[str, Any]) -> bytes:
    return (
        b"event: "
        + event.encode("ascii")
        + b"\n"
        + b"data: "
        + _json_bytes(value, "downstream stream event")
        + b"\n\n"
    )


def translate_nonstream_response_to_sse(
    chat_completion: Any,
    *,
    wire_model: str,
    accepted_response_models: Collection[str],
    capabilities: OpenModelCapabilities,
    response_constraints: OpenModelResponseConstraints,
) -> tuple[list[bytes], dict[str, int]]:
    """Translate one complete Chat Completion into bounded Messages SSE."""

    translated = translate_nonstream_response(
        chat_completion,
        wire_model=wire_model,
        accepted_response_models=accepted_response_models,
        capabilities=capabilities,
        response_constraints=response_constraints,
    )
    frames: list[bytes] = []
    total_bytes = 0

    def append(event: str, value: dict[str, Any]) -> None:
        nonlocal total_bytes
        frame = _sse_frame(event, value)
        total_bytes += len(frame)
        if total_bytes > MAX_SSE_TOTAL_BYTES:
            raise AdapterError("translated downstream stream is too large")
        frames.append(frame)

    append(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": translated["id"],
                "type": "message",
                "role": "assistant",
                "model": translated["model"],
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        },
    )
    for index, block in enumerate(translated["content"]):
        if block["type"] == "text":
            append(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            append(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "text_delta", "text": block["text"]},
                },
            )
        else:
            append(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {
                        "type": "tool_use",
                        "id": block["id"],
                        "name": block["name"],
                        "input": {},
                    },
                },
            )
            append(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": _json_bytes(
                            block["input"], "translated tool input"
                        ).decode("ascii"),
                    },
                },
            )
        append(
            "content_block_stop",
            {"type": "content_block_stop", "index": index},
        )
    usage = dict(translated["usage"])
    append(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": translated["stop_reason"],
                "stop_sequence": None,
            },
            "usage": usage,
        },
    )
    append("message_stop", {"type": "message_stop"})
    return frames, usage


@dataclass
class _StreamTool:
    upstream_index: int
    downstream_index: int
    name: str
    downstream_id: str
    argument_parts: list[str]
    argument_bytes: int = 0


class OpenModelSSETranslator:
    """Incrementally translate OpenAI Chat Completion SSE into Anthropic SSE."""

    def __init__(
        self,
        *,
        wire_model: str,
        accepted_response_models: Collection[str],
        capabilities: OpenModelCapabilities,
        response_constraints: OpenModelResponseConstraints,
    ) -> None:
        if not isinstance(capabilities, OpenModelCapabilities):
            raise AdapterError("open-model capabilities are invalid")
        if not isinstance(response_constraints, OpenModelResponseConstraints):
            raise AdapterError("open-model response constraints are invalid")
        self._wire_model = _model_id(wire_model, "wire model")
        self._accepted_models = _accepted_models(accepted_response_models)
        self._capabilities = capabilities
        self._response_constraints = response_constraints
        self._line_buffer = bytearray()
        self._event_data = bytearray()
        self._event_has_data = False
        self._event_bytes = 0
        self._total_bytes = 0
        self._identity_prefix_bytes = 0
        self._identity_validated = False
        self._pending_frames: list[bytes] = []
        self._pending_frame_bytes = 0
        self._message_started = False
        self._text_block_index: int | None = None
        self._text_block_open = False
        self._tool_blocks_started = False
        self._tools: dict[int, _StreamTool] = {}
        self._stream_text_bytes = 0
        self._stream_argument_bytes = 0
        self._pending_finish_reason: str | None = None
        self._stored_usage: dict[str, int] | None = None
        self._blocks_closed = False
        self._finalized = False
        self._done = False
        self._terminated = False
        self._output_committed = False

    @property
    def output_committed(self) -> bool:
        return self._output_committed

    @property
    def identity_validated(self) -> bool:
        return self._identity_validated

    @property
    def upstream_usage_received(self) -> bool:
        return self._stored_usage is not None

    @property
    def terminated(self) -> bool:
        return self._terminated or self._done

    def _record(self, frame: bytes, output: list[bytes]) -> None:
        if self._identity_validated:
            output.append(frame)
            return
        self._pending_frame_bytes += len(frame)
        if self._pending_frame_bytes > MAX_SSE_IDENTITY_PREFIX_BYTES:
            raise AdapterError("upstream stream output before model identity is too large")
        self._pending_frames.append(frame)

    def _ensure_message_start(self, output: list[bytes]) -> None:
        if self._message_started:
            return
        self._message_started = True
        self._record(
            _sse_frame(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": _safe_message_id(),
                        "type": "message",
                        "role": "assistant",
                        "model": self._wire_model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "cache_read_input_tokens": 0,
                        },
                    },
                },
            ),
            output,
        )

    def _validate_identity(self, value: Any, output: list[bytes]) -> None:
        _validate_response_model(value, self._accepted_models)
        if not self._identity_validated:
            self._identity_validated = True
            output.extend(self._pending_frames)
            self._pending_frames.clear()
            self._pending_frame_bytes = 0

    def _open_text(self, output: list[bytes]) -> int:
        self._ensure_message_start(output)
        if self._tool_blocks_started:
            raise AdapterError("visible text after tool calls is unsupported")
        if self._text_block_index is None:
            self._text_block_index = 0
            self._text_block_open = True
            self._record(
                _sse_frame(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                ),
                output,
            )
        return self._text_block_index

    def _emit_text(self, value: Any, output: list[bytes]) -> None:
        text = _bounded_string(value, "upstream stream content")
        if not text:
            return
        encoded_length = len(_utf8(text, "upstream stream content"))
        self._stream_text_bytes += encoded_length
        if self._stream_text_bytes > MAX_STREAM_TEXT_BYTES:
            raise AdapterError("upstream stream content is too large")
        index = self._open_text(output)
        self._record(
            _sse_frame(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "text_delta", "text": text},
                },
            ),
            output,
        )

    def _close_text(self, output: list[bytes]) -> None:
        if not self._text_block_open:
            return
        self._text_block_open = False
        assert self._text_block_index is not None
        self._record(
            _sse_frame(
                "content_block_stop",
                {"type": "content_block_stop", "index": self._text_block_index},
            ),
            output,
        )

    def _tool_fragment(self, value: Any, output: list[bytes]) -> None:
        if type(value) is not list or not 1 <= len(value) <= MAX_TOOL_CALLS:
            raise AdapterError("upstream stream tool calls are invalid")
        if self._capabilities.tool_support == "none":
            raise AdapterError("upstream returned tools unsupported by this route")
        if self._response_constraints.max_tool_calls == 0:
            raise AdapterError("upstream tool calls violate the request constraints")
        self._ensure_message_start(output)
        self._close_text(output)
        self._tool_blocks_started = True
        event_indices: set[int] = set()
        for fragment in value:
            if type(fragment) is not dict or type(fragment.get("index")) is not int:
                raise AdapterError("upstream stream tool fragment is invalid")
            upstream_index = fragment["index"]
            if not 0 <= upstream_index < MAX_TOOL_CALLS:
                raise AdapterError("upstream stream tool index is invalid")
            if upstream_index in event_indices:
                raise AdapterError("upstream stream repeated a tool index in one event")
            event_indices.add(upstream_index)
            existing = self._tools.get(upstream_index)
            if existing is None:
                if (
                    self._capabilities.tool_support == "single"
                    and self._tools
                ):
                    raise AdapterError(
                        "upstream returned parallel tools unsupported by this route"
                    )
                if len(self._tools) >= self._response_constraints.max_tool_calls:
                    raise AdapterError(
                        "upstream tool calls violate the request constraints"
                    )
                if len(self._tools) >= MAX_TOOL_CALLS:
                    raise AdapterError("upstream stream has too many tool calls")
                if set(fragment) != {"index", "id", "type", "function"}:
                    raise AdapterError("first upstream tool fragment is invalid")
                _upstream_tool_id(fragment.get("id"), "upstream stream tool ID")
                if fragment.get("type") != "function" or type(fragment.get("function")) is not dict:
                    raise AdapterError("upstream stream tool fragment is not a function")
                function = fragment["function"]
                if set(function) != {"name", "arguments"}:
                    raise AdapterError("first upstream tool function fragment is invalid")
                name = _tool_name(function.get("name"), "upstream stream tool name")
                if name not in self._response_constraints.declared_tool_names:
                    raise AdapterError("upstream returned an undeclared tool")
                if (
                    self._response_constraints.required_tool_name is not None
                    and name != self._response_constraints.required_tool_name
                ):
                    raise AdapterError(
                        "upstream returned a tool other than the forced tool"
                    )
                arguments = _bounded_string(
                    function.get("arguments"), "upstream stream tool arguments"
                )
                downstream_index = (1 if self._text_block_index is not None else 0) + len(self._tools)
                existing = _StreamTool(
                    upstream_index=upstream_index,
                    downstream_index=downstream_index,
                    name=name,
                    downstream_id=_safe_tool_use_id(),
                    argument_parts=[],
                )
                self._tools[upstream_index] = existing
                self._record(
                    _sse_frame(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": downstream_index,
                            "content_block": {
                                "type": "tool_use",
                                "id": existing.downstream_id,
                                "name": name,
                                "input": {},
                            },
                        },
                    ),
                    output,
                )
            else:
                if set(fragment) != {"index", "function"}:
                    raise AdapterError("later upstream tool fragment is invalid")
                function = fragment.get("function")
                if type(function) is not dict or set(function) != {"arguments"}:
                    raise AdapterError("later upstream tool function fragment is invalid")
                arguments = _bounded_string(
                    function.get("arguments"), "upstream stream tool arguments"
                )
            argument_length = len(_utf8(arguments, "upstream stream tool arguments"))
            existing.argument_bytes += argument_length
            self._stream_argument_bytes += argument_length
            if existing.argument_bytes > MAX_TOOL_ARGUMENT_BYTES:
                raise AdapterError("upstream stream tool arguments are too large")
            if self._stream_argument_bytes > MAX_STREAM_TOOL_ARGUMENT_BYTES:
                raise AdapterError("upstream stream tool state is too large")
            existing.argument_parts.append(arguments)
            if arguments:
                self._record(
                    _sse_frame(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": existing.downstream_index,
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": arguments,
                            },
                        },
                    ),
                    output,
                )

    def _close_blocks(self, output: list[bytes]) -> None:
        if self._blocks_closed:
            return
        self._close_text(output)
        for tool in sorted(self._tools.values(), key=lambda item: item.downstream_index):
            arguments = "".join(tool.argument_parts)
            _strict_json_object(
                arguments,
                "upstream stream tool arguments",
                max_bytes=MAX_TOOL_ARGUMENT_BYTES,
            )
            self._record(
                _sse_frame(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": tool.downstream_index},
                ),
                output,
            )
        self._blocks_closed = True

    def _store_finish_reason(self, value: Any, output: list[bytes]) -> None:
        if type(value) is not str or value not in _FINISH_REASON_MAP:
            raise AdapterError("upstream stream finish reason is unsupported")
        mapped = _FINISH_REASON_MAP[value]
        if self._pending_finish_reason is not None:
            raise AdapterError("upstream stream repeated its finish reason")
        if value == "tool_calls" and not self._tools:
            raise AdapterError("tool-call finish reason has no tool call")
        if self._tools and value != "tool_calls":
            raise AdapterError("streamed tool calls have an inconsistent finish reason")
        if (
            self._response_constraints.tool_choice_mode in {"required", "named"}
            and not self._tools
        ):
            raise AdapterError("upstream response omitted a required tool call")
        self._pending_finish_reason = mapped
        self._close_blocks(output)

    def _finalize(self, output: list[bytes]) -> None:
        if self._finalized:
            return
        if self._pending_finish_reason is None:
            raise AdapterError("upstream stream ended without a finish reason")
        if not self._identity_validated:
            raise AdapterError("upstream stream ended before model identity")
        self._ensure_message_start(output)
        self._close_blocks(output)
        usage = self._stored_usage or {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        self._record(
            _sse_frame(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {
                        "stop_reason": self._pending_finish_reason,
                        "stop_sequence": None,
                    },
                    "usage": usage,
                },
            ),
            output,
        )
        self._record(
            _sse_frame("message_stop", {"type": "message_stop"}), output
        )
        self._finalized = True

    def _handle_json_event(self, value: Any, output: list[bytes]) -> None:
        if self._finalized:
            raise AdapterError("upstream stream continued after finalization")
        if type(value) is not dict or value.get("object") != "chat.completion.chunk":
            raise AdapterError("upstream stream event is invalid")
        _bounded_string(
            value.get("id"),
            "upstream stream ID",
            allow_empty=False,
            max_bytes=1024,
        )
        created = value.get("created")
        if type(created) is not int or not 0 <= created <= MAX_TOKEN_COUNT:
            raise AdapterError("upstream stream timestamp is invalid")
        if "model" in value:
            self._validate_identity(value["model"], output)
        if self._identity_validated:
            self._ensure_message_start(output)
        event_usage: dict[str, int] | None = None
        if "usage" in value and value["usage"] is not None:
            event_usage = _usage(value["usage"])
        choices = value.get("choices")
        if type(choices) is not list:
            raise AdapterError("upstream stream choices are invalid")
        if not choices:
            if event_usage is None:
                raise AdapterError("empty upstream stream event has no usage")
            if self._pending_finish_reason is None:
                raise AdapterError("upstream stream usage arrived before finish reason")
            if self._stored_usage is not None:
                raise AdapterError("upstream stream repeated usage")
            self._stored_usage = event_usage
            self._finalize(output)
            return
        if len(choices) != 1 or type(choices[0]) is not dict:
            raise AdapterError("upstream stream must contain one choice")
        if self._pending_finish_reason is not None:
            raise AdapterError("upstream stream content followed its finish reason")
        choice = choices[0]
        if choice.get("index") != 0 or type(choice.get("delta")) is not dict:
            raise AdapterError("upstream stream choice is invalid")
        if set(choice) - {"index", "delta", "finish_reason", "logprobs"}:
            raise AdapterError("upstream stream choice contains unsupported fields")
        if "logprobs" in choice and choice["logprobs"] is not None:
            raise AdapterError("upstream stream log probabilities are unsupported")
        delta = choice["delta"]
        if set(delta) - {"role", "content", "reasoning_content", "tool_calls"}:
            raise AdapterError("upstream stream delta contains unsupported fields")
        if "role" in delta and delta["role"] != "assistant":
            raise AdapterError("upstream stream role is invalid")
        if "reasoning_content" in delta and delta["reasoning_content"] is not None:
            _bounded_string(
                delta["reasoning_content"], "upstream stream reasoning content"
            )
        if "content" in delta and delta["content"] is not None:
            self._emit_text(delta["content"], output)
        if "tool_calls" in delta and delta["tool_calls"] is not None:
            self._tool_fragment(delta["tool_calls"], output)
        finish_reason = choice.get("finish_reason")
        if event_usage is not None and finish_reason is None:
            raise AdapterError("upstream stream usage arrived before finish reason")
        if finish_reason is not None:
            self._store_finish_reason(finish_reason, output)
            if event_usage is not None:
                if self._stored_usage is not None:
                    raise AdapterError("upstream stream repeated usage")
                self._stored_usage = event_usage
                self._finalize(output)

    def _dispatch_event(self, output: list[bytes]) -> None:
        if not self._event_has_data:
            self._event_bytes = 0
            return
        raw = bytes(self._event_data)
        self._event_data.clear()
        self._event_has_data = False
        self._event_bytes = 0
        if raw == b"[DONE]":
            if self._done:
                raise AdapterError("upstream stream repeated its end marker")
            if not self._finalized:
                self._finalize(output)
            self._done = True
            return
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AdapterError("upstream stream JSON is not UTF-8") from exc
        try:
            value = json.loads(
                text,
                object_pairs_hook=_json_pairs,
                parse_constant=_reject_json_constant,
            )
        except (_DuplicateKeyError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise AdapterError("upstream stream event is invalid JSON") from exc
        self._handle_json_event(value, output)

    def _handle_line(self, line: bytes, output: list[bytes]) -> None:
        if line.endswith(b"\r"):
            line = line[:-1]
        if self._done:
            if not line or line.startswith(b":"):
                return
            raise AdapterError("upstream stream continued after its end marker")
        if not line:
            self._dispatch_event(output)
            return
        if line.startswith(b":"):
            return
        if b":" in line:
            field, raw_value = line.split(b":", 1)
            if raw_value.startswith(b" "):
                raw_value = raw_value[1:]
        else:
            field, raw_value = line, b""
        if field == b"data":
            separator_bytes = 1 if self._event_has_data else 0
            self._event_bytes += separator_bytes + len(raw_value)
            if self._event_bytes > MAX_SSE_EVENT_BYTES:
                raise AdapterError("upstream stream event is too large")
            if separator_bytes:
                self._event_data.extend(b"\n")
            self._event_data.extend(raw_value)
            self._event_has_data = True
            return
        if field == b"event":
            if raw_value not in {b"", b"message"}:
                raise AdapterError("upstream stream event type is unsupported")
            return
        if field == b"id":
            if len(raw_value) > 1024 or b"\x00" in raw_value:
                raise AdapterError("upstream stream event ID is invalid")
            return
        if field == b"retry":
            if raw_value and not raw_value.isdigit():
                raise AdapterError("upstream stream retry value is invalid")
            return
        raise AdapterError("upstream stream field is unsupported")

    def _run(self, operation: Any) -> list[bytes]:
        if self._terminated:
            raise AdapterError(
                "upstream stream translator is terminated",
                output_committed=self._output_committed,
            )
        output: list[bytes] = []
        try:
            operation(output)
        except AdapterError as exc:
            self._terminated = True
            if self._output_committed:
                raise AdapterError(
                    "upstream stream failed after output was committed",
                    output_committed=True,
                ) from exc
            raise AdapterError(str(exc), output_committed=False) from exc
        if output:
            self._output_committed = True
        return output

    def feed(self, data: bytes) -> list[bytes]:
        """Consume an arbitrary SSE byte chunk and return complete downstream frames."""

        if type(data) is not bytes:
            def reject_chunk(_: list[bytes]) -> None:
                raise AdapterError("upstream stream chunk must be bytes")

            return self._run(reject_chunk)

        def consume(output: list[bytes]) -> None:
            self._total_bytes += len(data)
            if self._total_bytes > MAX_SSE_TOTAL_BYTES:
                raise AdapterError("upstream stream is too large")
            if self._done:
                if data.strip(b" \t\r\n"):
                    raise AdapterError("upstream stream continued after its end marker")
                return
            self._line_buffer.extend(data)
            start = 0
            while True:
                newline = self._line_buffer.find(b"\n", start)
                if newline < 0:
                    break
                line_length = newline - start
                if line_length > MAX_SSE_LINE_BYTES:
                    raise AdapterError("upstream stream line is too large")
                if not self._identity_validated:
                    self._identity_prefix_bytes += line_length + 1
                    if self._identity_prefix_bytes > MAX_SSE_IDENTITY_PREFIX_BYTES:
                        raise AdapterError("upstream stream model prefix is too large")
                line = bytes(self._line_buffer[start:newline])
                start = newline + 1
                self._handle_line(line, output)
            if start:
                del self._line_buffer[:start]
            if len(self._line_buffer) > MAX_SSE_LINE_BYTES:
                raise AdapterError("upstream stream line is too large")
            if (
                not self._identity_validated
                and self._identity_prefix_bytes + len(self._line_buffer)
                > MAX_SSE_IDENTITY_PREFIX_BYTES
            ):
                raise AdapterError("upstream stream model prefix is too large")

        return self._run(consume)

    def finish(self) -> list[bytes]:
        """Finish an SSE stream at transport EOF."""

        def complete(output: list[bytes]) -> None:
            if self._line_buffer:
                if len(self._line_buffer) > MAX_SSE_LINE_BYTES:
                    raise AdapterError("upstream stream line is too large")
                line = bytes(self._line_buffer)
                self._line_buffer.clear()
                self._handle_line(line, output)
            if self._event_has_data:
                self._dispatch_event(output)
            if not self._done and not self._finalized:
                self._finalize(output)
            self._done = True

        return self._run(complete)


__all__ = [
    "AdapterError",
    "OpenModelCapabilities",
    "OpenModelResponseConstraints",
    "OpenModelSSETranslator",
    "translate_request",
    "derive_response_constraints",
    "translate_nonstream_response",
    "translate_nonstream_response_to_sse",
    "MAX_MODEL_ID_CHARS",
    "MAX_PRIVATE_MODEL_ID_CHARS",
    "MAX_PRIVATE_MODEL_ID_BYTES",
    "MAX_MESSAGES",
    "MAX_TRANSLATED_MESSAGES",
    "MAX_CONTENT_BLOCKS",
    "MAX_TOOLS",
    "MAX_TOOL_CALLS",
    "MAX_TOOL_NAME_CHARS",
    "MAX_TOOL_ID_CHARS",
    "MAX_TEXT_BYTES",
    "MAX_TOTAL_TEXT_BYTES",
    "MAX_SCHEMA_BYTES",
    "MAX_TOOL_ARGUMENT_BYTES",
    "MAX_TOTAL_TOOL_ARGUMENT_BYTES",
    "MAX_REQUEST_JSON_BYTES",
    "MAX_RESPONSE_JSON_BYTES",
    "MAX_JSON_DEPTH",
    "MAX_JSON_NODES",
    "MAX_JSON_INTEGER_BITS",
    "MAX_STOP_SEQUENCES",
    "MAX_STOP_SEQUENCE_BYTES",
    "MAX_SSE_LINE_BYTES",
    "MAX_SSE_EVENT_BYTES",
    "MAX_SSE_IDENTITY_PREFIX_BYTES",
    "MAX_SSE_TOTAL_BYTES",
    "MAX_STREAM_TEXT_BYTES",
    "MAX_STREAM_TOOL_ARGUMENT_BYTES",
    "MAX_ACCEPTED_RESPONSE_MODELS",
    "MAX_TOKEN_COUNT",
    "MAX_CONTEXT_WINDOW",
]
