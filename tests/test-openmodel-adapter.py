#!/usr/bin/env python3
"""Focused offline tests for the pure open-model protocol adapter."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = ROOT / "bin" / "airlock_openmodel_adapter.py"
FIXTURES = ROOT / "tests" / "fixtures" / "openmodel"
SPEC = importlib.util.spec_from_file_location("airlock_openmodel_adapter", ADAPTER_PATH)
assert SPEC is not None and SPEC.loader is not None
adapter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = adapter
SPEC.loader.exec_module(adapter)

UPSTREAM_MODEL = "local/open-model-synthetic"
WIRE_MODEL = "wire-open-model"


def capabilities(
    *,
    streaming: bool = True,
    tool_support: str = "parallel",
    modes: tuple[str, ...] = ("auto",),
) -> adapter.OpenModelCapabilities:
    return adapter.OpenModelCapabilities(
        context_window=170_000,
        max_output_tokens=4096,
        supports_streaming=streaming,
        tool_support=tool_support,
        declared_tool_choice_modes=modes,
    )


def basic_request(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "model": WIRE_MODEL,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hello."}],
    }
    body.update(changes)
    return body


def function_tool(name: str = "lookup_weather") -> dict[str, object]:
    return {
        "name": name,
        "description": "Return synthetic weather.",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
            "additionalProperties": False,
        },
    }


def response_constraints(
    *,
    capabilities_value: adapter.OpenModelCapabilities | None = None,
    tool_names: tuple[str, ...] = ("lookup_weather", "set_units"),
    tool_choice: dict[str, object] | None = None,
) -> adapter.OpenModelResponseConstraints:
    route_capabilities = capabilities_value or capabilities()
    body = basic_request()
    if tool_names:
        body["tools"] = [function_tool(name) for name in tool_names]
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    translated = adapter.translate_request(
        body,
        upstream_model=UPSTREAM_MODEL,
        capabilities=route_capabilities,
    )
    return adapter.derive_response_constraints(translated)


def load_json_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def sse_event(value: object, *, newline: bytes = b"\n") -> bytes:
    encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return b"data: " + encoded + newline + newline


def stream_chunk(
    *,
    model: object = UPSTREAM_MODEL,
    delta: object | None = None,
    finish_reason: object = None,
    usage: object = None,
    include_model: bool = True,
    choices: object | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "synthetic-stream",
        "object": "chat.completion.chunk",
        "created": 1785700000,
    }
    if include_model:
        value["model"] = model
    if choices is None:
        value["choices"] = [
            {
                "index": 0,
                "delta": {} if delta is None else delta,
                "finish_reason": finish_reason,
            }
        ]
    else:
        value["choices"] = choices
    if usage is not None:
        value["usage"] = usage
    return value


def usage(prompt: int = 20, completion: int = 5, cached: int = 3) -> dict[str, object]:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {"cached_tokens": cached},
    }


def parse_frames(frames: list[bytes]) -> list[tuple[str, dict[str, object]]]:
    parsed: list[tuple[str, dict[str, object]]] = []
    for frame in frames:
        lines = frame.rstrip(b"\n").splitlines()
        if len(lines) != 2 or not lines[0].startswith(b"event: ") or not lines[1].startswith(b"data: "):
            raise AssertionError(f"invalid downstream SSE frame: {frame!r}")
        event = lines[0][len(b"event: ") :].decode("ascii")
        value = json.loads(lines[1][len(b"data: ") :])
        parsed.append((event, value))
    return parsed


class CapabilityTests(unittest.TestCase):
    def test_capabilities_are_validated_and_deeply_frozen(self) -> None:
        value = capabilities(modes=("auto", "required", "auto"))
        self.assertEqual(value.declared_tool_choice_modes, frozenset({"auto", "required"}))
        with self.assertRaises(FrozenInstanceError):
            value.max_output_tokens = 1
        for changes in [
            {"context_window": True},
            {"context_window": 0},
            {"max_output_tokens": 170_001},
            {"supports_streaming": 1},
            {"tool_support": "many"},
            {"tool_support": []},
            {"declared_tool_choice_modes": "auto"},
            {"declared_tool_choice_modes": ("auto", "forced")},
        ]:
            arguments = {
                "context_window": 170_000,
                "max_output_tokens": 4096,
                "supports_streaming": True,
                "tool_support": "parallel",
                "declared_tool_choice_modes": ("auto",),
            }
            arguments.update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(adapter.AdapterError):
                    adapter.OpenModelCapabilities(**arguments)

    def test_response_constraints_are_validated_and_deeply_frozen(self) -> None:
        value = adapter.OpenModelResponseConstraints(
            declared_tool_names=["lookup_weather", "lookup_weather"],
            tool_choice_mode="named",
            required_tool_name="lookup_weather",
            max_tool_calls=1,
        )
        self.assertEqual(value.declared_tool_names, frozenset({"lookup_weather"}))
        with self.assertRaises(FrozenInstanceError):
            value.max_tool_calls = 2
        invalid = [
            {"declared_tool_names": "lookup_weather"},
            {"declared_tool_names": ["bad name"]},
            {"tool_choice_mode": "any"},
            {"required_tool_name": "missing"},
            {"tool_choice_mode": "auto", "required_tool_name": "lookup_weather"},
            {"max_tool_calls": True},
            {"max_tool_calls": adapter.MAX_TOOL_CALLS + 1},
            {"tool_choice_mode": "none", "required_tool_name": None},
            {"declared_tool_names": [], "tool_choice_mode": "auto"},
        ]
        baseline: dict[str, object] = {
            "declared_tool_names": ["lookup_weather"],
            "tool_choice_mode": "named",
            "required_tool_name": "lookup_weather",
            "max_tool_calls": 1,
        }
        for changes in invalid:
            arguments = dict(baseline)
            arguments.update(changes)
            with self.subTest(changes=changes), self.assertRaises(adapter.AdapterError):
                adapter.OpenModelResponseConstraints(**arguments)

    def test_constraints_are_derived_from_and_detached_from_translated_request(self) -> None:
        caps = capabilities(modes=("auto", "none", "required", "named"))
        cases = [
            ((), None, "none", None, 0),
            (("lookup_weather",), None, "auto", None, adapter.MAX_TOOL_CALLS),
            (("lookup_weather",), {"type": "none"}, "none", None, 0),
            (("lookup_weather",), {"type": "any"}, "required", None, adapter.MAX_TOOL_CALLS),
            (
                ("lookup_weather", "set_units"),
                {"type": "tool", "name": "lookup_weather"},
                "named",
                "lookup_weather",
                adapter.MAX_TOOL_CALLS,
            ),
            (
                ("lookup_weather",),
                {"type": "auto", "disable_parallel_tool_use": True},
                "auto",
                None,
                1,
            ),
        ]
        for names, choice, mode, required_name, maximum in cases:
            with self.subTest(mode=mode, names=names):
                body = basic_request()
                if names:
                    body["tools"] = [function_tool(name) for name in names]
                if choice is not None:
                    body["tool_choice"] = choice
                translated = adapter.translate_request(
                    body, upstream_model=UPSTREAM_MODEL, capabilities=caps
                )
                constraints = adapter.derive_response_constraints(translated)
                self.assertEqual(constraints.declared_tool_names, frozenset(names))
                self.assertEqual(constraints.tool_choice_mode, mode)
                self.assertEqual(constraints.required_tool_name, required_name)
                self.assertEqual(constraints.max_tool_calls, maximum)
                if names:
                    translated["tools"][0]["function"]["name"] = "mutated"
                    self.assertEqual(
                        constraints.declared_tool_names, frozenset(names)
                    )

    def test_constraint_derivation_rejects_nontranslated_shapes(self) -> None:
        valid = adapter.translate_request(
            basic_request(tools=[function_tool()]),
            upstream_model=UPSTREAM_MODEL,
            capabilities=capabilities(),
        )
        invalid = [
            None,
            {"tool_choice": "auto"},
            {**valid, "tools": []},
            {**valid, "tool_choice": "unsupported"},
            {**valid, "parallel_tool_calls": 1},
            {
                **valid,
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "missing"},
                },
            },
        ]
        for value in invalid:
            with self.subTest(value=str(value)[:100]), self.assertRaises(
                adapter.AdapterError
            ):
                adapter.derive_response_constraints(value)


class RequestTranslationTests(unittest.TestCase):
    def test_private_upstream_identity_accepts_spaces_and_unicode(self) -> None:
        private_identity = "D:/Private Models/模型 Q4.gguf"
        result = adapter.translate_request(
            basic_request(),
            upstream_model=private_identity,
            capabilities=capabilities(),
        )
        self.assertEqual(result["model"], private_identity)
        for invalid in (
            " leading",
            "trailing ",
            "line\nbreak",
            "bidi\u202emodel",
            "x" * (adapter.MAX_PRIVATE_MODEL_ID_CHARS + 1),
        ):
            with self.subTest(invalid=repr(invalid)[:40]), self.assertRaises(
                adapter.AdapterError
            ):
                adapter.translate_request(
                    basic_request(),
                    upstream_model=invalid,
                    capabilities=capabilities(),
                )

    def test_probe_round_trip_request_is_allowlisted_ordered_and_fresh(self) -> None:
        fixture = load_json_fixture("tool-round-trip.json")
        source = fixture["anthropic_request"]
        original = deepcopy(source)
        result = adapter.translate_request(
            source,
            upstream_model=UPSTREAM_MODEL,
            capabilities=capabilities(),
        )
        self.assertEqual(source, original)
        self.assertEqual(result["model"], UPSTREAM_MODEL)
        self.assertEqual(result["max_tokens"], 4096)
        self.assertEqual(
            [message["role"] for message in result["messages"]],
            ["system", "system", "user", "assistant", "tool", "user"],
        )
        self.assertEqual(
            [message["content"] for message in result["messages"][:2]],
            [
                "Use tools only when needed.",
                "Synthetic Claude Code custom-model notice.",
            ],
        )
        assistant = result["messages"][3]
        self.assertEqual(assistant["content"], "I will check.")
        self.assertEqual(
            json.loads(assistant["tool_calls"][0]["function"]["arguments"]),
            {"city": "Exampleville"},
        )
        self.assertEqual(result["messages"][4], {
            "role": "tool",
            "tool_call_id": "toolu_synthetic_history",
            "content": "18 C and clear",
        })
        self.assertEqual(result["tool_choice"], "auto")
        self.assertIs(result["parallel_tool_calls"], True)
        self.assertEqual(result["tools"][0]["type"], "function")
        self.assertEqual(result["tools"][0]["function"]["parameters"]["type"], "object")
        self.assertNotIn("metadata", result)
        self.assertNotIn("cache_control", json.dumps(result))

        result["tools"][0]["function"]["parameters"]["type"] = "changed"
        self.assertEqual(source, original)

    def test_mixed_nonerror_tool_results_and_user_text_preserve_order(self) -> None:
        body = basic_request(
            messages=[
                {"role": "user", "content": "Start."},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_ordered",
                            "name": "lookup_weather",
                            "input": {"city": "Exampleville"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_ordered",
                            "content": "Result.",
                            "is_error": False,
                        },
                        {"type": "text", "text": "After."},
                    ],
                },
            ]
        )
        result = adapter.translate_request(
            body, upstream_model=UPSTREAM_MODEL, capabilities=capabilities()
        )
        self.assertEqual(
            result["messages"][-2:],
            [
                {
                    "role": "tool",
                    "tool_call_id": "toolu_ordered",
                    "content": "Result.",
                },
                {"role": "user", "content": "After."},
            ],
        )

    def test_tool_results_must_immediately_and_completely_follow_calls(self) -> None:
        first_call = {
            "type": "tool_use",
            "id": "toolu_first",
            "name": "lookup_weather",
            "input": {},
        }
        second_call = {
            "type": "tool_use",
            "id": "toolu_second",
            "name": "set_units",
            "input": {},
        }
        first_result = {
            "type": "tool_result",
            "tool_use_id": "toolu_first",
            "content": "First result.",
        }
        second_result = {
            "type": "tool_result",
            "tool_use_id": "toolu_second",
            "content": "Second result.",
        }
        invalid_histories = {
            "missing": [
                {"role": "user", "content": "Start."},
                {"role": "assistant", "content": [first_call]},
            ],
            "intervening-message": [
                {"role": "user", "content": "Start."},
                {"role": "assistant", "content": [first_call]},
                {"role": "user", "content": "Not a tool result."},
                {"role": "user", "content": [first_result]},
            ],
            "intervening-system-message": [
                {"role": "user", "content": "Start."},
                {"role": "assistant", "content": [first_call]},
                {"role": "system", "content": "Late notice."},
                {"role": "user", "content": [first_result]},
            ],
            "text-before-result": [
                {"role": "user", "content": "Start."},
                {"role": "assistant", "content": [first_call]},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Before."},
                        first_result,
                    ],
                },
            ],
            "partial-parallel-results": [
                {"role": "user", "content": "Start."},
                {"role": "assistant", "content": [first_call, second_call]},
                {"role": "user", "content": [first_result]},
            ],
            "late-duplicate": [
                {"role": "user", "content": "Start."},
                {"role": "assistant", "content": [first_call]},
                {"role": "user", "content": [first_result]},
                {"role": "user", "content": [first_result]},
            ],
            "result-after-text": [
                {"role": "user", "content": "Start."},
                {"role": "assistant", "content": [first_call, second_call]},
                {
                    "role": "user",
                    "content": [
                        first_result,
                        {"type": "text", "text": "Too soon."},
                        second_result,
                    ],
                },
            ],
        }
        for name, messages in invalid_histories.items():
            with self.subTest(name=name), self.assertRaises(adapter.AdapterError):
                adapter.translate_request(
                    basic_request(messages=messages),
                    upstream_model=UPSTREAM_MODEL,
                    capabilities=capabilities(tool_support="parallel"),
                )

    def test_assistant_tool_history_preserves_only_text_before_calls(self) -> None:
        first_call = {
            "type": "tool_use",
            "id": "toolu_first",
            "name": "lookup_weather",
            "input": {"city": "Exampleville"},
        }
        second_call = {
            "type": "tool_use",
            "id": "toolu_second",
            "name": "set_units",
            "input": {"unit": "celsius"},
        }
        parallel = adapter.translate_request(
            basic_request(
                messages=[
                    {"role": "user", "content": "Start."},
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "First, "},
                            {"type": "text", "text": "check both."},
                            first_call,
                            second_call,
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_first",
                                "content": "First result.",
                            },
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_second",
                                "content": "Second result.",
                            },
                        ],
                    },
                ]
            ),
            upstream_model=UPSTREAM_MODEL,
            capabilities=capabilities(tool_support="parallel"),
        )
        self.assertEqual(
            parallel["messages"][1]["content"], "First, check both."
        )
        self.assertEqual(
            [call["id"] for call in parallel["messages"][1]["tool_calls"]],
            ["toolu_first", "toolu_second"],
        )

        single = adapter.translate_request(
            basic_request(
                messages=[
                    {"role": "user", "content": "Start."},
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Check once."}, first_call
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_first",
                                "content": "One result.",
                            }
                        ],
                    },
                ]
            ),
            upstream_model=UPSTREAM_MODEL,
            capabilities=capabilities(tool_support="single"),
        )
        self.assertEqual(single["messages"][1]["content"], "Check once.")

        with self.assertRaises(adapter.AdapterError):
            adapter.translate_request(
                basic_request(
                    messages=[
                        {"role": "user", "content": "Start."},
                        {"role": "assistant", "content": [first_call]},
                    ]
                ),
                upstream_model=UPSTREAM_MODEL,
                capabilities=capabilities(tool_support="none", modes=()),
            )

    def test_assistant_text_after_or_between_tool_calls_is_rejected(self) -> None:
        first_call = {
            "type": "tool_use",
            "id": "toolu_first",
            "name": "lookup_weather",
            "input": {},
        }
        second_call = {
            "type": "tool_use",
            "id": "toolu_second",
            "name": "set_units",
            "input": {},
        }
        cases = {
            "after": [first_call, {"type": "text", "text": "Too late."}],
            "between": [
                {"type": "text", "text": "Before."},
                first_call,
                {"type": "text", "text": "Between."},
                second_call,
            ],
        }
        for name, content in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(
                adapter.AdapterError,
                "assistant text after a tool use is unsupported by Chat Completions",
            ):
                adapter.translate_request(
                    basic_request(
                        messages=[
                            {"role": "user", "content": "Start."},
                            {"role": "assistant", "content": content},
                        ]
                    ),
                    upstream_model=UPSTREAM_MODEL,
                    capabilities=capabilities(),
                )

    def test_error_tool_results_are_rejected_without_rewriting_content(self) -> None:
        tool_use = {
            "type": "tool_use",
            "id": "toolu_failed",
            "name": "lookup_weather",
            "input": {},
        }
        body = basic_request(
            messages=[
                {"role": "user", "content": "Start."},
                {"role": "assistant", "content": [tool_use]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_failed",
                            "content": "private failure detail",
                            "is_error": True,
                        }
                    ],
                },
            ]
        )
        for tool_support in ("single", "parallel"):
            with self.subTest(tool_support=tool_support), self.assertRaisesRegex(
                adapter.AdapterError,
                "failed tool results are unsupported by Chat Completions",
            ) as caught:
                adapter.translate_request(
                    body,
                    upstream_model=UPSTREAM_MODEL,
                    capabilities=capabilities(tool_support=tool_support),
                )
            self.assertNotIn("private failure detail", str(caught.exception))

        with self.assertRaises(adapter.AdapterError):
            adapter.translate_request(
                body,
                upstream_model=UPSTREAM_MODEL,
                capabilities=capabilities(tool_support="none", modes=()),
            )

    def test_system_role_notice_has_strict_lifting_semantics(self) -> None:
        valid = basic_request(
            system="Normal.",
            messages=[
                {"role": "system", "content": [{"type": "text", "text": "Notice."}]},
                {"role": "user", "content": "Question."},
            ],
        )
        result = adapter.translate_request(
            valid, upstream_model=UPSTREAM_MODEL, capabilities=capabilities()
        )
        self.assertEqual(
            result["messages"][:2],
            [
                {"role": "system", "content": "Normal."},
                {"role": "system", "content": "Notice."},
            ],
        )
        invalid_messages = [
            [{"role": "system", "content": "Only."}],
            [
                {"role": "system", "content": "Notice.", "name": "extra"},
                {"role": "user", "content": "Question."},
            ],
            [
                {
                    "role": "system",
                    "content": [{"type": "text", "text": "Notice.", "extra": True}],
                },
                {"role": "user", "content": "Question."},
            ],
            [
                {"role": "system", "content": [{"type": "image", "source": {}}]},
                {"role": "user", "content": "Question."},
            ],
        ]
        for messages in invalid_messages:
            with self.subTest(messages=messages):
                with self.assertRaises(adapter.AdapterError):
                    adapter.translate_request(
                        basic_request(messages=messages),
                        upstream_model=UPSTREAM_MODEL,
                        capabilities=capabilities(),
                    )

    def test_unsupported_content_and_semantic_fields_fail_closed(self) -> None:
        blocks = [
            {"type": "image", "source": {"type": "base64", "data": "AA=="}},
            {"type": "document", "source": {"type": "text", "data": "x"}},
            {"type": "audio", "data": "x"},
            {"type": "thinking", "thinking": "hidden", "signature": "fake"},
            {"type": "redacted_thinking", "data": "fake"},
        ]
        for block in blocks:
            role = "assistant" if block["type"] in {"thinking", "redacted_thinking"} else "user"
            messages = [{"role": "user", "content": "Start."}]
            messages.append({"role": role, "content": [block]})
            with self.subTest(block=block["type"]):
                with self.assertRaises(adapter.AdapterError):
                    adapter.translate_request(
                        basic_request(messages=messages),
                        upstream_model=UPSTREAM_MODEL,
                        capabilities=capabilities(),
                    )
        for field in ["thinking", "effort", "output_config", "service_tier", "mcp_servers"]:
            with self.subTest(field=field):
                body = basic_request()
                body[field] = {"unsupported": True}
                with self.assertRaises(adapter.AdapterError):
                    adapter.translate_request(
                        body,
                        upstream_model=UPSTREAM_MODEL,
                        capabilities=capabilities(),
                    )

    def test_only_client_function_tool_definitions_are_accepted(self) -> None:
        invalid_tools = [
            [{"type": "web_search_20250305", "name": "web_search"}],
            [{"name": "missing_schema"}],
            [{"name": "bad_schema", "input_schema": []}],
            [{"name": "bad_schema", "input_schema": {}}],
            [{"name": "bad field", "input_schema": {"type": "object"}}],
            [{"name": "okay", "input_schema": {}, "defer_loading": True}],
            [function_tool("same"), function_tool("same")],
        ]
        for tools in invalid_tools:
            with self.subTest(tools=tools):
                with self.assertRaises(adapter.AdapterError):
                    adapter.translate_request(
                        basic_request(tools=tools),
                        upstream_model=UPSTREAM_MODEL,
                        capabilities=capabilities(),
                    )
        with self.assertRaises(adapter.AdapterError):
            adapter.translate_request(
                basic_request(tools=[function_tool()]),
                upstream_model=UPSTREAM_MODEL,
                capabilities=capabilities(tool_support="none", modes=()),
            )

    def test_tool_choice_is_mapped_only_when_the_mode_is_declared(self) -> None:
        cases = [
            ({"type": "tool", "name": "lookup_weather"}, "named"),
            ({"type": "any"}, "required"),
            ({"type": "none"}, "none"),
        ]
        for choice, mode in cases:
            with self.subTest(mode=mode):
                with self.assertRaises(adapter.AdapterError):
                    adapter.translate_request(
                        basic_request(tools=[function_tool()], tool_choice=choice),
                        upstream_model=UPSTREAM_MODEL,
                        capabilities=capabilities(modes=("auto",)),
                    )
        required = adapter.translate_request(
            basic_request(tools=[function_tool()], tool_choice={"type": "any"}),
            upstream_model=UPSTREAM_MODEL,
            capabilities=capabilities(modes=("auto", "required")),
        )
        self.assertEqual(required["tool_choice"], "required")
        none = adapter.translate_request(
            basic_request(tools=[function_tool()], tool_choice={"type": "none"}),
            upstream_model=UPSTREAM_MODEL,
            capabilities=capabilities(modes=("auto", "none")),
        )
        self.assertEqual(none["tool_choice"], "none")
        self.assertNotIn("parallel_tool_calls", none)

    def test_named_choice_mapping_requires_an_explicit_hypothetical_capability(self) -> None:
        result = adapter.translate_request(
            basic_request(
                tools=[function_tool()],
                tool_choice={"type": "tool", "name": "lookup_weather"},
            ),
            upstream_model=UPSTREAM_MODEL,
            capabilities=capabilities(modes=("auto", "named")),
        )
        self.assertEqual(
            result["tool_choice"],
            {"type": "function", "function": {"name": "lookup_weather"}},
        )

    def test_parallel_tool_flag_is_sent_only_for_parallel_capability(self) -> None:
        single = adapter.translate_request(
            basic_request(tools=[function_tool()]),
            upstream_model=UPSTREAM_MODEL,
            capabilities=capabilities(tool_support="single"),
        )
        self.assertNotIn("parallel_tool_calls", single)
        disabled = adapter.translate_request(
            basic_request(
                tools=[function_tool()],
                tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            ),
            upstream_model=UPSTREAM_MODEL,
            capabilities=capabilities(),
        )
        self.assertIs(disabled["parallel_tool_calls"], False)
        with self.assertRaises(adapter.AdapterError):
            adapter.translate_request(
                basic_request(
                    tools=[function_tool()],
                    tool_choice={"type": "auto", "disable_parallel_tool_use": False},
                ),
                upstream_model=UPSTREAM_MODEL,
                capabilities=capabilities(tool_support="single"),
            )

    def test_scalar_translation_clamping_and_stream_options_are_strict(self) -> None:
        body = basic_request(
            max_tokens=9000,
            temperature=0.25,
            top_p=0.9,
            stop_sequences=["END", "STOP"],
            stream=True,
            metadata={"user_id": "synthetic"},
        )
        result = adapter.translate_request(
            body, upstream_model=UPSTREAM_MODEL, capabilities=capabilities()
        )
        self.assertEqual(result["max_tokens"], 4096)
        self.assertEqual(result["temperature"], 0.25)
        self.assertEqual(result["top_p"], 0.9)
        self.assertEqual(result["stop"], ["END", "STOP"])
        self.assertEqual(result["stream_options"], {"include_usage": True})
        self.assertNotIn("metadata", result)
        empty_stops = adapter.translate_request(
            basic_request(stop_sequences=[]),
            upstream_model=UPSTREAM_MODEL,
            capabilities=capabilities(),
        )
        self.assertNotIn("stop", empty_stops)

        invalid_changes = [
            {"max_tokens": True},
            {"max_tokens": 0},
            {"temperature": True},
            {"temperature": float("nan")},
            {"temperature": 1.01},
            {"top_p": -0.01},
            {"stop_sequences": ["x"] * (adapter.MAX_STOP_SEQUENCES + 1)},
            {"stop_sequences": [""]},
            {"stream": 1},
        ]
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                with self.assertRaises(adapter.AdapterError):
                    adapter.translate_request(
                        basic_request(**changes),
                        upstream_model=UPSTREAM_MODEL,
                        capabilities=capabilities(),
                    )
        with self.assertRaises(adapter.AdapterError):
            adapter.translate_request(
                basic_request(stream=True),
                upstream_model=UPSTREAM_MODEL,
                capabilities=capabilities(streaming=False),
            )
        not_streaming = adapter.translate_request(
            basic_request(stream=False),
            upstream_model=UPSTREAM_MODEL,
            capabilities=capabilities(streaming=False),
        )
        self.assertIs(not_streaming["stream"], False)
        self.assertNotIn("stream_options", not_streaming)

    def test_history_shape_ids_and_bounds_are_enforced(self) -> None:
        invalid = [
            basic_request(system=None),
            basic_request(messages=[{"role": "assistant", "content": "No user."}]),
            basic_request(messages=[]),
            basic_request(messages=[{"role": "user", "content": []}]),
            basic_request(messages=[{"role": "user", "content": "x", "extra": 1}]),
            basic_request(
                messages=[
                    {"role": "user", "content": "Start."},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_missing",
                                "content": "x",
                            }
                        ],
                    },
                ]
            ),
            basic_request(messages=[{"role": "user", "content": "x"}] * (adapter.MAX_MESSAGES + 1)),
            basic_request(stop_sequences=["x" * (adapter.MAX_STOP_SEQUENCE_BYTES + 1)]),
        ]
        for body in invalid:
            with self.subTest(reason=str(body)[:80]):
                with self.assertRaises(adapter.AdapterError):
                    adapter.translate_request(
                        body,
                        upstream_model=UPSTREAM_MODEL,
                        capabilities=capabilities(),
                    )


class NonStreamResponseTests(unittest.TestCase):
    def test_private_response_identity_accepts_spaces_and_unicode(self) -> None:
        private_identity = "D:/Private Models/模型 Q4.gguf"
        source = load_json_fixture("nonstream-text.json")
        source["model"] = private_identity
        result = adapter.translate_nonstream_response(
            source,
            wire_model=WIRE_MODEL,
            accepted_response_models=(private_identity,),
            capabilities=capabilities(),
            response_constraints=response_constraints(),
        )
        self.assertEqual(result["model"], WIRE_MODEL)
        self.assertNotIn(private_identity, json.dumps(result))

    def test_probe_text_response_is_canonical_and_hides_reasoning(self) -> None:
        source = load_json_fixture("nonstream-text.json")
        result = adapter.translate_nonstream_response(
            source,
            wire_model=WIRE_MODEL,
            accepted_response_models={UPSTREAM_MODEL},
            capabilities=capabilities(),
            response_constraints=response_constraints(),
        )
        self.assertRegex(result["id"], r"^msg_[0-9a-f]{24}$")
        self.assertEqual(result["type"], "message")
        self.assertEqual(result["role"], "assistant")
        self.assertEqual(result["model"], WIRE_MODEL)
        self.assertEqual(
            result["content"],
            [{"type": "text", "text": "Synthetic visible answer."}],
        )
        self.assertEqual(result["stop_reason"], "end_turn")
        self.assertIsNone(result["stop_sequence"])
        self.assertEqual(
            result["usage"],
            {
                "input_tokens": 21,
                "output_tokens": 7,
                "cache_read_input_tokens": 5,
            },
        )
        self.assertNotIn("reasoning", json.dumps(result))
        self.assertNotIn(UPSTREAM_MODEL, json.dumps(result))

    def test_probe_tool_response_parses_object_and_replaces_upstream_ids(self) -> None:
        source = load_json_fixture("tool-round-trip.json")["chat_completion"]
        result = adapter.translate_nonstream_response(
            source,
            wire_model=WIRE_MODEL,
            accepted_response_models=(UPSTREAM_MODEL,),
            capabilities=capabilities(),
            response_constraints=response_constraints(),
        )
        self.assertEqual(result["stop_reason"], "tool_use")
        self.assertEqual(len(result["content"]), 1)
        block = result["content"][0]
        self.assertEqual(block["type"], "tool_use")
        self.assertRegex(block["id"], r"^toolu_[0-9a-f]{24}$")
        self.assertNotEqual(block["id"], "call_private_synthetic")
        self.assertEqual(block["name"], "lookup_weather")
        self.assertEqual(block["input"], {"city": "Exampleville"})
        encoded = json.dumps(result)
        self.assertNotIn("call_private_synthetic", encoded)
        self.assertNotIn(UPSTREAM_MODEL, encoded)

    def test_response_tool_count_obeys_declared_capability(self) -> None:
        single_call = load_json_fixture("tool-round-trip.json")["chat_completion"]
        result = adapter.translate_nonstream_response(
            single_call,
            wire_model=WIRE_MODEL,
            accepted_response_models={UPSTREAM_MODEL},
            capabilities=capabilities(tool_support="single"),
            response_constraints=response_constraints(),
        )
        self.assertEqual(result["stop_reason"], "tool_use")

        with self.assertRaises(adapter.AdapterError):
            adapter.translate_nonstream_response(
                single_call,
                wire_model=WIRE_MODEL,
                accepted_response_models={UPSTREAM_MODEL},
                capabilities=capabilities(tool_support="none"),
                response_constraints=response_constraints(),
            )

        parallel_calls = deepcopy(single_call)
        second = deepcopy(
            parallel_calls["choices"][0]["message"]["tool_calls"][0]
        )
        second["id"] = "call_private_second"
        second["function"]["name"] = "set_units"
        second["function"]["arguments"] = '{"unit":"celsius"}'
        parallel_calls["choices"][0]["message"]["tool_calls"].append(second)
        with self.assertRaises(adapter.AdapterError):
            adapter.translate_nonstream_response(
                parallel_calls,
                wire_model=WIRE_MODEL,
                accepted_response_models={UPSTREAM_MODEL},
                capabilities=capabilities(tool_support="single"),
                response_constraints=response_constraints(),
            )
        result = adapter.translate_nonstream_response(
            parallel_calls,
            wire_model=WIRE_MODEL,
            accepted_response_models={UPSTREAM_MODEL},
            capabilities=capabilities(tool_support="parallel"),
            response_constraints=response_constraints(),
        )
        self.assertEqual(len(result["content"]), 2)

    def test_request_constraints_reject_forbidden_nonstream_tool_behavior(self) -> None:
        tool_response = load_json_fixture("tool-round-trip.json")["chat_completion"]
        text_response = load_json_fixture("nonstream-text.json")
        parallel_response = deepcopy(tool_response)
        second = deepcopy(
            parallel_response["choices"][0]["message"]["tool_calls"][0]
        )
        second["id"] = "call_private_second"
        parallel_response["choices"][0]["message"]["tool_calls"].append(second)

        no_tools = response_constraints(tool_names=())
        none_caps = capabilities(modes=("auto", "none"))
        none_choice = response_constraints(
            capabilities_value=none_caps,
            tool_names=("lookup_weather",),
            tool_choice={"type": "none"},
        )
        only_lookup = response_constraints(tool_names=("lookup_weather",))
        named_caps = capabilities(modes=("auto", "named"))
        forced_lookup = response_constraints(
            capabilities_value=named_caps,
            tool_choice={"type": "tool", "name": "lookup_weather"},
        )
        required_caps = capabilities(modes=("auto", "required"))
        required = response_constraints(
            capabilities_value=required_caps,
            tool_names=("lookup_weather",),
            tool_choice={"type": "any"},
        )
        single_response = response_constraints(
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
        )

        wrong_name = deepcopy(tool_response)
        wrong_name["choices"][0]["message"]["tool_calls"][0]["function"][
            "name"
        ] = "set_units"
        cases = [
            (tool_response, no_tools),
            (tool_response, none_choice),
            (wrong_name, only_lookup),
            (wrong_name, forced_lookup),
            (text_response, required),
            (
                text_response,
                response_constraints(
                    capabilities_value=named_caps,
                    tool_choice={"type": "tool", "name": "lookup_weather"},
                ),
            ),
            (parallel_response, single_response),
        ]
        for response, constraints in cases:
            with self.subTest(constraints=constraints), self.assertRaises(
                adapter.AdapterError
            ):
                adapter.translate_nonstream_response(
                    response,
                    wire_model=WIRE_MODEL,
                    accepted_response_models={UPSTREAM_MODEL},
                    capabilities=capabilities(),
                    response_constraints=constraints,
                )

        named_success = adapter.translate_nonstream_response(
            tool_response,
            wire_model=WIRE_MODEL,
            accepted_response_models={UPSTREAM_MODEL},
            capabilities=capabilities(),
            response_constraints=forced_lookup,
        )
        self.assertEqual(named_success["content"][0]["name"], "lookup_weather")
        for constraints in (required, single_response):
            with self.subTest(allowed_constraints=constraints):
                result = adapter.translate_nonstream_response(
                    tool_response,
                    wire_model=WIRE_MODEL,
                    accepted_response_models={UPSTREAM_MODEL},
                    capabilities=capabilities(),
                    response_constraints=constraints,
                )
                self.assertEqual(result["stop_reason"], "tool_use")

    def test_response_constraints_argument_is_explicit_and_type_checked(self) -> None:
        source = load_json_fixture("nonstream-text.json")
        with self.assertRaises(TypeError):
            adapter.translate_nonstream_response(
                source,
                wire_model=WIRE_MODEL,
                accepted_response_models={UPSTREAM_MODEL},
                capabilities=capabilities(),
            )
        with self.assertRaises(adapter.AdapterError):
            adapter.translate_nonstream_response(
                source,
                wire_model=WIRE_MODEL,
                accepted_response_models={UPSTREAM_MODEL},
                capabilities=capabilities(),
                response_constraints=object(),
            )

    def test_model_identity_mismatch_is_exact_and_sanitized(self) -> None:
        value = load_json_fixture("nonstream-text.json")
        private_identity = "private/provider-identity"
        value["model"] = private_identity
        with self.assertRaises(adapter.AdapterError) as caught:
            adapter.translate_nonstream_response(
                value,
                wire_model=WIRE_MODEL,
                accepted_response_models={UPSTREAM_MODEL},
                capabilities=capabilities(),
                response_constraints=response_constraints(),
            )
        self.assertNotIn(private_identity, str(caught.exception))
        self.assertNotIn(UPSTREAM_MODEL, str(caught.exception))
        with self.assertRaises(adapter.AdapterError):
            adapter.translate_nonstream_response(
                value,
                wire_model=WIRE_MODEL,
                accepted_response_models=set(),
                capabilities=capabilities(),
                response_constraints=response_constraints(),
            )

    def test_finish_reasons_and_choice_shape_are_strict(self) -> None:
        base = load_json_fixture("nonstream-text.json")
        for upstream, downstream in [
            ("stop", "end_turn"),
            ("length", "max_tokens"),
        ]:
            value = deepcopy(base)
            value["choices"][0]["finish_reason"] = upstream
            result = adapter.translate_nonstream_response(
                value,
                wire_model=WIRE_MODEL,
                accepted_response_models={UPSTREAM_MODEL},
                capabilities=capabilities(),
                response_constraints=response_constraints(),
            )
            self.assertEqual(result["stop_reason"], downstream)
        invalid_values = []
        value = deepcopy(base)
        value["choices"][0]["finish_reason"] = "content_filter"
        invalid_values.append(value)
        value = deepcopy(base)
        value["choices"].append(deepcopy(value["choices"][0]))
        invalid_values.append(value)
        value = deepcopy(base)
        value["object"] = "response"
        invalid_values.append(value)
        value = deepcopy(base)
        value["choices"][0]["message"]["role"] = "tool"
        invalid_values.append(value)
        value = deepcopy(base)
        del value["id"]
        invalid_values.append(value)
        value = deepcopy(base)
        value["created"] = True
        invalid_values.append(value)
        value = deepcopy(base)
        value["choices"][0]["index"] = False
        invalid_values.append(value)
        for value in invalid_values:
            with self.subTest(value=str(value)[:80]):
                with self.assertRaises(adapter.AdapterError):
                    adapter.translate_nonstream_response(
                        value,
                        wire_model=WIRE_MODEL,
                        accepted_response_models={UPSTREAM_MODEL},
                        capabilities=capabilities(),
                        response_constraints=response_constraints(),
                    )

    def test_tool_arguments_are_bounded_strict_json_objects(self) -> None:
        base = load_json_fixture("tool-round-trip.json")["chat_completion"]
        invalid_arguments = [
            "not-json",
            "[]",
            '{"a":1,"a":2}',
            '{"a":NaN}',
            "{} trailing",
        ]
        for arguments in invalid_arguments:
            value = deepcopy(base)
            value["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = arguments
            with self.subTest(arguments=arguments):
                with self.assertRaises(adapter.AdapterError):
                    adapter.translate_nonstream_response(
                        value,
                        wire_model=WIRE_MODEL,
                        accepted_response_models={UPSTREAM_MODEL},
                        capabilities=capabilities(),
                        response_constraints=response_constraints(),
                    )
        value = deepcopy(base)
        value["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = " " * (adapter.MAX_TOOL_ARGUMENT_BYTES + 1)
        with self.assertRaises(adapter.AdapterError):
            adapter.translate_nonstream_response(
                value,
                wire_model=WIRE_MODEL,
                accepted_response_models={UPSTREAM_MODEL},
                capabilities=capabilities(),
                response_constraints=response_constraints(),
            )

    def test_usage_is_required_nonnegative_and_maps_cached_tokens(self) -> None:
        base = load_json_fixture("nonstream-text.json")
        invalid_usages = [
            None,
            {},
            {"prompt_tokens": True, "completion_tokens": 1},
            {"prompt_tokens": 1, "completion_tokens": -1},
            {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "prompt_tokens_details": {"cached_tokens": 2},
            },
        ]
        for bad_usage in invalid_usages:
            value = deepcopy(base)
            value["usage"] = bad_usage
            with self.subTest(usage=bad_usage):
                with self.assertRaises(adapter.AdapterError):
                    adapter.translate_nonstream_response(
                        value,
                        wire_model=WIRE_MODEL,
                        accepted_response_models={UPSTREAM_MODEL},
                        capabilities=capabilities(),
                        response_constraints=response_constraints(),
                    )


class SSETranslationTests(unittest.TestCase):
    def translator(
        self,
        *,
        tool_support: str = "parallel",
        modes: tuple[str, ...] = ("auto",),
        response_constraints_value: adapter.OpenModelResponseConstraints | None = None,
    ) -> adapter.OpenModelSSETranslator:
        route_capabilities = capabilities(tool_support=tool_support, modes=modes)
        constraints = response_constraints_value or response_constraints(
            capabilities_value=route_capabilities,
            tool_names=() if tool_support == "none" else ("lookup_weather", "set_units"),
        )
        return adapter.OpenModelSSETranslator(
            wire_model=WIRE_MODEL,
            accepted_response_models={UPSTREAM_MODEL},
            capabilities=route_capabilities,
            response_constraints=constraints,
        )

    def test_probe_text_stream_handles_arbitrary_chunks_reasoning_and_terminal_usage(self) -> None:
        raw = (FIXTURES / "stream-text.sse").read_bytes()
        translator = self.translator()
        frames: list[bytes] = []
        for byte in raw:
            frames.extend(translator.feed(bytes([byte])))
        frames.extend(translator.finish())
        parsed = parse_frames(frames)
        self.assertEqual(
            [event for event, _ in parsed],
            [
                "message_start",
                "content_block_start",
                "content_block_delta",
                "content_block_delta",
                "content_block_stop",
                "message_delta",
                "message_stop",
            ],
        )
        deltas = [
            value["delta"]["text"]
            for event, value in parsed
            if event == "content_block_delta"
        ]
        self.assertEqual(deltas, ["Synthetic ", "visible answer."])
        start = parsed[0][1]["message"]
        self.assertEqual(start["model"], WIRE_MODEL)
        self.assertRegex(start["id"], r"^msg_[0-9a-f]{24}$")
        final = parsed[-2][1]
        self.assertEqual(final["delta"]["stop_reason"], "end_turn")
        self.assertEqual(
            final["usage"],
            {
                "input_tokens": 24,
                "output_tokens": 9,
                "cache_read_input_tokens": 6,
            },
        )
        encoded = b"".join(frames).decode("utf-8")
        self.assertNotIn("reasoning", encoded)
        self.assertNotIn(UPSTREAM_MODEL, encoded)
        self.assertTrue(translator.output_committed)
        self.assertTrue(translator.identity_validated)
        self.assertTrue(translator.upstream_usage_received)
        self.assertTrue(translator.terminated)

    def test_finish_reason_waits_for_later_usage_event(self) -> None:
        translator = self.translator()
        first = parse_frames(
            translator.feed(sse_event(stream_chunk(delta={"content": "Text."})))
        )
        self.assertEqual([event for event, _ in first], [
            "message_start", "content_block_start", "content_block_delta"
        ])
        stopped = parse_frames(
            translator.feed(
                sse_event(
                    stream_chunk(delta={}, finish_reason="stop")
                )
            )
        )
        self.assertEqual([event for event, _ in stopped], ["content_block_stop"])
        self.assertNotIn("message_delta", [event for event, _ in stopped])
        self.assertFalse(translator.upstream_usage_received)
        terminal = parse_frames(
            translator.feed(
                sse_event(
                    stream_chunk(choices=[], usage=usage(13, 4, 2))
                )
            )
        )
        self.assertEqual(
            [event for event, _ in terminal], ["message_delta", "message_stop"]
        )
        self.assertEqual(terminal[0][1]["usage"]["input_tokens"], 13)
        self.assertTrue(translator.upstream_usage_received)
        self.assertEqual(translator.feed(b"data: [DONE]\n\n"), [])
        self.assertEqual(translator.finish(), [])

        early_usage = self.translator()
        with self.assertRaises(adapter.AdapterError) as caught:
            early_usage.feed(
                sse_event(stream_chunk(choices=[], usage=usage(13, 4, 2)))
            )
        self.assertFalse(caught.exception.output_committed)

    def test_done_or_transport_eof_proves_usage_is_absent(self) -> None:
        for terminal in [b"data: [DONE]\n\n", None]:
            with self.subTest(terminal=terminal):
                translator = self.translator()
                translator.feed(sse_event(stream_chunk(delta={"content": "x"})))
                before = parse_frames(
                    translator.feed(
                        sse_event(stream_chunk(delta={}, finish_reason="stop"))
                    )
                )
                self.assertEqual([event for event, _ in before], ["content_block_stop"])
                raw_frames = translator.finish() if terminal is None else translator.feed(terminal)
                parsed = parse_frames(raw_frames)
                self.assertEqual(
                    [event for event, _ in parsed], ["message_delta", "message_stop"]
                )
                self.assertEqual(parsed[0][1]["usage"], {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 0,
                })
                self.assertFalse(translator.upstream_usage_received)

    def test_output_is_buffered_until_an_exact_model_identity_arrives(self) -> None:
        translator = self.translator()
        prefix = translator.feed(
            sse_event(
                stream_chunk(
                    include_model=False,
                    delta={"content": "Buffered."},
                )
            )
        )
        self.assertEqual(prefix, [])
        self.assertFalse(translator.output_committed)
        released = parse_frames(
            translator.feed(sse_event(stream_chunk(delta={})))
        )
        self.assertEqual(
            [event for event, _ in released],
            ["message_start", "content_block_start", "content_block_delta"],
        )
        self.assertEqual(released[-1][1]["delta"]["text"], "Buffered.")

    def test_identity_mismatch_before_output_is_uncommitted_and_sanitized(self) -> None:
        private_identity = "private/model-before"
        translator = self.translator()
        with self.assertRaises(adapter.AdapterError) as caught:
            translator.feed(
                sse_event(stream_chunk(model=private_identity, delta={"content": "x"}))
            )
        self.assertFalse(caught.exception.output_committed)
        self.assertFalse(translator.output_committed)
        self.assertTrue(translator.terminated)
        self.assertNotIn(private_identity, str(caught.exception))
        self.assertNotIn(UPSTREAM_MODEL, str(caught.exception))

    def test_identity_mismatch_after_output_has_distinct_committed_state(self) -> None:
        private_identity = "private/model-after"
        translator = self.translator()
        produced = translator.feed(sse_event(stream_chunk(delta={"content": "x"})))
        self.assertTrue(produced)
        self.assertTrue(translator.output_committed)
        with self.assertRaises(adapter.AdapterError) as caught:
            translator.feed(sse_event(stream_chunk(model=private_identity, delta={})))
        self.assertTrue(caught.exception.output_committed)
        self.assertTrue(caught.exception.committed)
        self.assertIn("after output was committed", str(caught.exception))
        self.assertNotIn(private_identity, str(caught.exception))
        self.assertNotIn(UPSTREAM_MODEL, str(caught.exception))

    def test_stream_tool_count_obeys_declared_capability(self) -> None:
        first = {
            "index": 0,
            "id": "call_first",
            "type": "function",
            "function": {"name": "lookup_weather", "arguments": "{}"},
        }
        event = sse_event(
            stream_chunk(delta={"tool_calls": [first]})
        )
        with self.assertRaises(adapter.AdapterError):
            self.translator(tool_support="none").feed(event)

        translator = self.translator(tool_support="single")
        frames = translator.feed(event)
        frames.extend(
            translator.feed(
                sse_event(stream_chunk(delta={}, finish_reason="tool_calls"))
            )
        )
        frames.extend(translator.feed(b"data: [DONE]\n\n"))
        self.assertEqual(
            len(
                [
                    value
                    for name, value in parse_frames(frames)
                    if name == "content_block_start"
                ]
            ),
            1,
        )

        second = deepcopy(first)
        second["index"] = 1
        second["id"] = "call_second"
        second["function"]["name"] = "set_units"
        parallel_event = sse_event(
            stream_chunk(delta={"tool_calls": [first, second]})
        )
        with self.assertRaises(adapter.AdapterError):
            self.translator(tool_support="single").feed(parallel_event)

    def test_stream_request_constraints_reject_forbidden_tool_behavior(self) -> None:
        lookup = {
            "index": 0,
            "id": "call_lookup",
            "type": "function",
            "function": {"name": "lookup_weather", "arguments": "{}"},
        }
        units = {
            "index": 0,
            "id": "call_units",
            "type": "function",
            "function": {"name": "set_units", "arguments": "{}"},
        }
        no_tools = response_constraints(tool_names=())
        none_modes = ("auto", "none")
        none_choice = response_constraints(
            capabilities_value=capabilities(modes=none_modes),
            tool_names=("lookup_weather",),
            tool_choice={"type": "none"},
        )
        only_lookup = response_constraints(tool_names=("lookup_weather",))
        named_modes = ("auto", "named")
        forced_lookup = response_constraints(
            capabilities_value=capabilities(modes=named_modes),
            tool_choice={"type": "tool", "name": "lookup_weather"},
        )
        single_response = response_constraints(
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
        )

        rejected_fragments = [
            (no_tools, ("auto",), lookup),
            (none_choice, none_modes, lookup),
            (only_lookup, ("auto",), units),
            (forced_lookup, named_modes, units),
        ]
        for constraints, modes, fragment in rejected_fragments:
            with self.subTest(constraints=constraints), self.assertRaises(
                adapter.AdapterError
            ):
                self.translator(
                    modes=modes, response_constraints_value=constraints
                ).feed(
                    sse_event(stream_chunk(delta={"tool_calls": [fragment]}))
                )

        second = deepcopy(lookup)
        second["index"] = 1
        second["id"] = "call_second"
        with self.assertRaises(adapter.AdapterError):
            self.translator(response_constraints_value=single_response).feed(
                sse_event(
                    stream_chunk(delta={"tool_calls": [lookup, second]})
                )
            )

        required_modes = ("auto", "required")
        required = response_constraints(
            capabilities_value=capabilities(modes=required_modes),
            tool_names=("lookup_weather",),
            tool_choice={"type": "any"},
        )
        named = response_constraints(
            capabilities_value=capabilities(modes=named_modes),
            tool_names=("lookup_weather",),
            tool_choice={"type": "tool", "name": "lookup_weather"},
        )
        for constraints, modes in [(required, required_modes), (named, named_modes)]:
            translator = self.translator(
                modes=modes, response_constraints_value=constraints
            )
            translator.feed(sse_event(stream_chunk(delta={"content": "text"})))
            with self.subTest(constraints=constraints), self.assertRaises(
                adapter.AdapterError
            ) as caught:
                translator.feed(
                    sse_event(stream_chunk(delta={}, finish_reason="stop"))
                )
            self.assertTrue(caught.exception.output_committed)

        translator = self.translator(
            modes=named_modes, response_constraints_value=named
        )
        frames = translator.feed(
            sse_event(stream_chunk(delta={"tool_calls": [lookup]}))
        )
        frames.extend(
            translator.feed(
                sse_event(stream_chunk(delta={}, finish_reason="tool_calls"))
            )
        )
        frames.extend(translator.feed(b"data: [DONE]\n\n"))
        starts = [
            value
            for event, value in parse_frames(frames)
            if event == "content_block_start"
        ]
        self.assertEqual([value["content_block"]["name"] for value in starts], ["lookup_weather"])

    def test_sse_constraints_argument_is_explicit_and_type_checked(self) -> None:
        with self.assertRaises(TypeError):
            adapter.OpenModelSSETranslator(
                wire_model=WIRE_MODEL,
                accepted_response_models={UPSTREAM_MODEL},
                capabilities=capabilities(),
            )
        with self.assertRaises(adapter.AdapterError):
            adapter.OpenModelSSETranslator(
                wire_model=WIRE_MODEL,
                accepted_response_models={UPSTREAM_MODEL},
                capabilities=capabilities(),
                response_constraints=object(),
            )

    def test_fragmented_interleaved_tool_arguments_are_tracked_and_ids_are_replaced(self) -> None:
        raw = (FIXTURES / "stream-tool-arguments.sse").read_bytes()
        translator = self.translator()
        frames: list[bytes] = []
        chunk_sizes = [1, 2, 7, 3, 19, 5, 31]
        offset = 0
        index = 0
        while offset < len(raw):
            size = chunk_sizes[index % len(chunk_sizes)]
            frames.extend(translator.feed(raw[offset : offset + size]))
            offset += size
            index += 1
        frames.extend(translator.finish())
        parsed = parse_frames(frames)
        starts = [
            value for event, value in parsed if event == "content_block_start"
        ]
        self.assertEqual(len(starts), 2)
        self.assertEqual(
            [value["content_block"]["name"] for value in starts],
            ["lookup_weather", "set_units"],
        )
        for value in starts:
            self.assertRegex(value["content_block"]["id"], r"^toolu_[0-9a-f]{24}$")
        pieces: dict[int, list[str]] = {0: [], 1: []}
        for event, value in parsed:
            if event == "content_block_delta":
                pieces[value["index"]].append(value["delta"]["partial_json"])
        self.assertEqual(json.loads("".join(pieces[0])), {"city": "Exampleville"})
        self.assertEqual(json.loads("".join(pieces[1])), {"unit": "celsius"})
        self.assertEqual(
            [value["index"] for event, value in parsed if event == "content_block_stop"],
            [0, 1],
        )
        final = [value for event, value in parsed if event == "message_delta"][0]
        self.assertEqual(final["delta"]["stop_reason"], "tool_use")
        encoded = b"".join(frames).decode("utf-8")
        self.assertNotIn("call_private_zero", encoded)
        self.assertNotIn("call_private_one", encoded)
        self.assertNotIn(UPSTREAM_MODEL, encoded)

    def test_only_first_tool_fragment_may_carry_identity_or_name(self) -> None:
        translator = self.translator()
        first = stream_chunk(
            delta={
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_first",
                        "type": "function",
                        "function": {"name": "lookup_weather", "arguments": "{"},
                    }
                ]
            }
        )
        self.assertTrue(translator.feed(sse_event(first)))
        repeated = stream_chunk(
            delta={
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_repeated",
                        "function": {"name": "lookup_weather", "arguments": "}"},
                    }
                ]
            }
        )
        with self.assertRaises(adapter.AdapterError) as caught:
            translator.feed(sse_event(repeated))
        self.assertTrue(caught.exception.output_committed)

    def test_invalid_reassembled_tool_json_fails_with_committed_state(self) -> None:
        translator = self.translator()
        first = stream_chunk(
            delta={
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_invalid_json",
                        "type": "function",
                        "function": {"name": "lookup_weather", "arguments": "{"},
                    }
                ]
            }
        )
        self.assertTrue(translator.feed(sse_event(first)))
        with self.assertRaises(adapter.AdapterError) as caught:
            translator.feed(
                sse_event(stream_chunk(delta={}, finish_reason="tool_calls"))
            )
        self.assertTrue(caught.exception.output_committed)
        self.assertTrue(translator.terminated)

    def test_sse_comments_crlf_and_multiline_data_are_supported(self) -> None:
        value = stream_chunk(delta={"content": "Split."})
        raw_json = json.dumps(value, separators=(",", ":"))
        split = raw_json.index(",\"choices\"") + 1
        raw = (
            b": comment\r\n"
            + b"event: message\r\n"
            + b"data: "
            + raw_json[:split].encode("utf-8")
            + b"\r\n"
            + b"data: "
            + raw_json[split:].encode("utf-8")
            + b"\r\n\r\n"
        )
        parsed = parse_frames(self.translator().feed(raw))
        self.assertEqual(
            [event for event, _ in parsed],
            ["message_start", "content_block_start", "content_block_delta"],
        )

    def test_sse_line_prefix_total_argument_and_tool_index_bounds(self) -> None:
        translator = self.translator()
        with self.assertRaises(adapter.AdapterError) as caught:
            translator.feed(b"x" * (adapter.MAX_SSE_LINE_BYTES + 1))
        self.assertFalse(caught.exception.output_committed)

        with mock.patch.object(adapter, "MAX_SSE_IDENTITY_PREFIX_BYTES", 32):
            translator = self.translator()
            with self.assertRaises(adapter.AdapterError):
                translator.feed(b":" + b"x" * 32)

        first_event = sse_event(stream_chunk(delta={}))
        with mock.patch.object(
            adapter, "MAX_SSE_IDENTITY_PREFIX_BYTES", len(first_event)
        ):
            translator = self.translator()
            produced = translator.feed(first_event + b":" + b"x" * 128 + b"\n")
            self.assertTrue(produced)
            self.assertTrue(translator.identity_validated)

        with mock.patch.object(adapter, "MAX_SSE_TOTAL_BYTES", 16):
            translator = self.translator()
            with self.assertRaises(adapter.AdapterError):
                translator.feed(b":" + b"x" * 16)

        with mock.patch.object(adapter, "MAX_TOOL_ARGUMENT_BYTES", 4):
            translator = self.translator()
            oversized = stream_chunk(
                delta={
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_bound",
                            "type": "function",
                            "function": {"name": "bounded", "arguments": "12345"},
                        }
                    ]
                }
            )
            with self.assertRaises(adapter.AdapterError):
                translator.feed(sse_event(oversized))

        translator = self.translator()
        bad_index = stream_chunk(
            delta={
                "tool_calls": [
                    {
                        "index": adapter.MAX_TOOL_CALLS,
                        "id": "call_index",
                        "type": "function",
                        "function": {"name": "bounded", "arguments": "{}"},
                    }
                ]
            }
        )
        with self.assertRaises(adapter.AdapterError):
            translator.feed(sse_event(bad_index))

    def test_malformed_stream_and_missing_finish_reason_fail_closed(self) -> None:
        malformed_inputs = [
            b"data: not-json\n\n",
            b"data: {\"object\":\"chat.completion.chunk\",\"object\":\"duplicate\"}\n\n",
            b"unknown: field\n\n",
        ]
        for raw in malformed_inputs:
            with self.subTest(raw=raw):
                translator = self.translator()
                with self.assertRaises(adapter.AdapterError) as caught:
                    translator.feed(raw)
                self.assertFalse(caught.exception.output_committed)
        translator = self.translator()
        translator.feed(sse_event(stream_chunk(delta={"content": "unfinished"})))
        with self.assertRaises(adapter.AdapterError) as caught:
            translator.finish()
        self.assertTrue(caught.exception.output_committed)


if __name__ == "__main__":
    unittest.main()
