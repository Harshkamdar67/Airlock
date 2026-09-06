#!/usr/bin/env python3
"""Offline tests for Airlock Console tool contract and MCP wrapper."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import math
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from unittest import mock
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "bin" / "airlock_console_tools.py"
MCP_PATH = ROOT / "plugins" / "airlock" / "mcp-server" / "airlock_console_mcp.py"

contract_spec = importlib.util.spec_from_file_location(
    "airlock_console_tools", CONTRACT_PATH
)
if contract_spec is None or contract_spec.loader is None:  # pragma: no cover
    raise SystemExit(f"could not load {CONTRACT_PATH}")
tools = importlib.util.module_from_spec(contract_spec)
sys.modules["airlock_console_tools"] = tools
contract_spec.loader.exec_module(tools)

mcp_spec = importlib.util.spec_from_file_location("airlock_console_mcp", MCP_PATH)
if mcp_spec is None or mcp_spec.loader is None:  # pragma: no cover
    raise SystemExit(f"could not load {MCP_PATH}")
mcp = importlib.util.module_from_spec(mcp_spec)
sys.modules["airlock_console_mcp"] = mcp
mcp_spec.loader.exec_module(mcp)

ToolContractError = tools.ToolContractError
EXPECTED_NAMES = list(tools.TOOL_NAMES)
FORBIDDEN_WORDS = (
    "approve",
    "reject",
    "edit",
    "execute",
    "pin",
    "unpin",
    "resume",
    "terminal",
    "http",
    "mutation",
)


def load_contract_from_path(path: Path):
    return mcp.load_contract(path)


def write_console_address(
    path: Path,
    url: str,
    *,
    pid: int = 12345,
    console_id: str = "a" * 64,
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "url": url,
                "pid": pid,
                "console_id": console_id,
            }
        ),
        encoding="utf-8",
    )


class ManifestTests(unittest.TestCase):
    def test_exact_seventeen_names(self) -> None:
        names = [tool["name"] for tool in tools.tool_manifest()]
        self.assertEqual(names, EXPECTED_NAMES)
        self.assertEqual(len(names), 17)
        self.assertEqual(len(set(names)), 17)
        # The six history tools come last and are all read-only.
        self.assertEqual(tuple(names[-6:]), tools.HISTORY_TOOLS)
        for name in tools.HISTORY_TOOLS:
            self.assertIn(name, tools.READ_ONLY_TOOLS)
            self.assertIn(name, tools.UNTRUSTED_CONTENT_TOOLS)

    def test_no_forbidden_mutation_tools(self) -> None:
        joined = " ".join(EXPECTED_NAMES).lower()
        for word in FORBIDDEN_WORDS:
            self.assertNotIn(word, joined)
        for tool in tools.tool_manifest():
            lowered = (tool["name"] + " " + tool["description"]).lower()
            for word in ("approve", "reject", "execute", "unpin", "resume"):
                self.assertNotIn(word, lowered)

    def test_read_only_and_untrusted_annotations(self) -> None:
        for tool in tools.tool_manifest():
            annotations = tool["annotations"]
            self.assertIn("readOnlyHint", annotations)
            if tool["name"] in tools.READ_ONLY_TOOLS:
                self.assertIs(annotations["readOnlyHint"], True)
            else:
                self.assertIs(annotations["readOnlyHint"], False)
            if tool["name"] in tools.UNTRUSTED_CONTENT_TOOLS:
                self.assertIs(annotations.get("untrustedContentHint"), True)
            else:
                self.assertNotIn("untrustedContentHint", annotations)

    def test_manifest_returns_independent_copies(self) -> None:
        first = tools.tool_manifest()
        second = tools.tool_manifest()
        self.assertEqual(first, second)
        first[0]["name"] = "mutated"
        first[0]["inputSchema"]["properties"] = {"x": {"type": "string"}}
        self.assertNotEqual(first[0]["name"], second[0]["name"])
        third = tools.tool_manifest()
        self.assertEqual(third[0]["name"], EXPECTED_NAMES[0])
        self.assertNotIn("x", third[0]["inputSchema"].get("properties", {}))

    def test_every_schema_rejects_additional_properties(self) -> None:
        for tool in tools.tool_manifest():
            schema = tool["inputSchema"]
            self.assertEqual(schema.get("type"), "object")
            self.assertIs(schema.get("additionalProperties"), False)
            self._assert_no_open_objects(schema)

    def _assert_no_open_objects(self, node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "additionalProperties" in node:
                value = node["additionalProperties"]
                if value is not False and not isinstance(value, dict):
                    self.fail(f"unexpected additionalProperties: {value!r}")
            for child in node.values():
                self._assert_no_open_objects(child)
        elif isinstance(node, list):
            for child in node:
                self._assert_no_open_objects(child)


class ValidateToolCallTests(unittest.TestCase):
    def assert_invalid(self, name: str, arguments: object) -> None:
        with self.assertRaises(ToolContractError) as ctx:
            tools.validate_tool_call(name, arguments)
        self.assertTrue(ctx.exception.code)
        self.assertTrue(ctx.exception.message)
        # Never echo arbitrary bad input.
        if isinstance(arguments, dict):
            for value in arguments.values():
                if isinstance(value, str) and len(value) > 20 and "\x00" in value:
                    self.assertNotIn(value, ctx.exception.message)

    def test_unknown_tool(self) -> None:
        self.assert_invalid("airlock_approve_proposal", {})
        self.assert_invalid("", {})
        with self.assertRaises(ToolContractError):
            tools.validate_tool_call(None, {})

    def test_non_object_arguments(self) -> None:
        self.assert_invalid("airlock_list_sessions", [])
        self.assert_invalid("airlock_list_sessions", "x")
        self.assert_invalid("airlock_get_session", True)

    def test_empty_object_tools(self) -> None:
        for name in (
            "airlock_list_sessions",
            "airlock_get_headroom",
            "airlock_get_global_failover_chain",
        ):
            self.assertEqual(tools.validate_tool_call(name, {}), {})
            self.assert_invalid(name, {"extra": 1})

    def test_history_tool_arguments(self) -> None:
        self.assertEqual(tools.validate_tool_call("airlock_list_history", {}), {})
        self.assertEqual(
            tools.validate_tool_call("airlock_list_history", {"project": " claudex ", "limit": 5, "q": "retry"}),
            {"project": "claudex", "limit": 5, "q": "retry"},
        )
        self.assert_invalid("airlock_list_history", {"limit": 0})
        self.assert_invalid("airlock_list_history", {"limit": 501})
        self.assert_invalid("airlock_list_history", {"limit": True})
        self.assert_invalid("airlock_list_history", {"project": "   "})
        self.assert_invalid("airlock_list_history", {"project": "a\x00b"})
        self.assert_invalid("airlock_list_history", {"since": "yesterday"})
        self.assert_invalid("airlock_list_history", {"workdir": "C:/x"})
        self.assertEqual(
            tools.validate_tool_call("airlock_get_history_session", {"history_id": "hx-0f3c9d2e-aaa"}),
            {"history_id": "hx-0f3c9d2e-aaa"},
        )
        self.assert_invalid("airlock_get_history_session", {"history_id": "0f3c9d2e-aaa"})
        self.assert_invalid("airlock_get_history_session", {"history_id": "hx-../../etc"})
        self.assert_invalid("airlock_list_subagents", {})
        self.assertEqual(
            tools.validate_tool_call("airlock_get_subagent_feed", {"history_id": "hx-0f3c9d2e-aaa", "agent_id": "abcdef0123"}),
            {"history_id": "hx-0f3c9d2e-aaa", "agent_id": "abcdef0123"},
        )
        self.assert_invalid("airlock_get_subagent_feed", {"history_id": "hx-0f3c9d2e-aaa", "agent_id": "..%2Fx"})
        self.assert_invalid("airlock_get_subagent_feed", {"history_id": "hx-0f3c9d2e-aaa", "agent_id": "a/b/c"})
        self.assert_invalid("airlock_get_subagent_feed", {"history_id": "hx-0f3c9d2e-aaa", "agent_id": "ab"})
        self.assertEqual(tools.validate_tool_call("airlock_get_usage", {"group": "month"}), {"group": "month"})
        self.assert_invalid("airlock_get_usage", {"group": "year"})
        self.assert_invalid("airlock_get_usage", {"dimension": "model"})
        query = tools.validate_tool_call("airlock_query_history", {
            "dimension": "model", "project": "claudex", "order_by": "compactions", "descending": True, "limit": 10,
        })
        self.assertEqual(query, {"dimension": "model", "project": "claudex", "order_by": "compactions", "descending": True, "limit": 10})
        self.assert_invalid("airlock_query_history", {})
        self.assert_invalid("airlock_query_history", {"dimension": "user"})
        self.assert_invalid("airlock_query_history", {"dimension": "model", "order_by": "cost"})
        self.assert_invalid("airlock_query_history", {"dimension": "model", "provider": "Open AI"})
        self.assert_invalid("airlock_query_history", {"dimension": "model", "descending": "yes"})
        self.assert_invalid("airlock_query_history", {"dimension": "model", "limit": 1000})
        self.assert_invalid("airlock_query_history", {"dimension": "model", "sql": "select 1"})

    def test_get_session_bounds(self) -> None:
        self.assertEqual(
            tools.validate_tool_call(
                "airlock_get_session", {"session_id": "r-8f2c1a"}
            ),
            {"session_id": "r-8f2c1a"},
        )
        self.assert_invalid("airlock_get_session", {})
        self.assert_invalid(
            "airlock_get_session", {"session_id": "r-8f2c1a", "extra": True}
        )
        self.assert_invalid("airlock_get_session", {"session_id": "bad id"})
        self.assert_invalid("airlock_get_session", {"session_id": "x" * 65})
        self.assert_invalid("airlock_get_session", {"session_id": "sess/../x"})

    def test_get_session_events_filters(self) -> None:
        self.assertEqual(
            tools.validate_tool_call(
                "airlock_get_session_events",
                {
                    "session_id": "r-8f2c1a",
                    "kind": "rate_limit_failover_attempted",
                    "model": "gpt-5.6-sol",
                    "since": "2026-09-05T09:00:00Z",
                },
            ),
            {
                "session_id": "r-8f2c1a",
                "kind": "rate_limit_failover_attempted",
                "model": "gpt-5.6-sol",
                "since": "2026-09-05T09:00:00Z",
            },
        )
        self.assert_invalid(
            "airlock_get_session_events",
            {"session_id": "r-8f2c1a", "since": "yesterday"},
        )
        self.assert_invalid(
            "airlock_get_session_events",
            {"session_id": "r-8f2c1a", "kind": "bad kind"},
        )

    def test_bool_rejected_as_number_and_non_finite(self) -> None:
        self.assert_invalid(
            "airlock_propose_session_handoff",
            {
                "session_id": "r-8f2c1a",
                "target_model": "claude-opus-5[1m]",
                "reason": "Need a frontier peer now.",
                "allow_metered": 1,
            },
        )
        deep = {"session_id": "r-8f2c1a"}
        current = deep
        for _ in range(20):
            nxt: dict[str, Any] = {}
            current["nested"] = nxt
            current = nxt
        self.assert_invalid("airlock_get_session", deep)

    def test_list_proposals_enums(self) -> None:
        self.assertEqual(
            tools.validate_tool_call(
                "airlock_list_proposals",
                {
                    "session_id": "r-8f2c1a",
                    "kind": "session_handoff",
                    "status": "pending",
                },
            ),
            {
                "session_id": "r-8f2c1a",
                "kind": "session_handoff",
                "status": "pending",
            },
        )
        self.assert_invalid(
            "airlock_list_proposals", {"kind": "approve"}
        )
        self.assert_invalid(
            "airlock_list_proposals", {"status": "running"}
        )

    def test_propose_session_handoff(self) -> None:
        self.assertEqual(
            tools.validate_tool_call(
                "airlock_propose_session_handoff",
                {
                    "session_id": "r-8f2c1a",
                    "target_model": "claude-opus-5[1m]",
                    "reason": "Active provider is cooling.",
                    "allow_metered": False,
                },
            ),
            {
                "session_id": "r-8f2c1a",
                "target_model": "claude-opus-5[1m]",
                "reason": "Active provider is cooling.",
                "allow_metered": False,
            },
        )
        self.assert_invalid(
            "airlock_propose_session_handoff",
            {
                "session_id": "r-8f2c1a",
                "target_model": "claude-opus-5[1m]",
                "reason": "short",
            },
        )
        self.assert_invalid(
            "airlock_propose_session_handoff",
            {
                "session_id": "r-8f2c1a",
                "target_model": "claude-opus-5[1m]",
                "reason": "x" * 401,
            },
        )

    def test_propose_restore_root(self) -> None:
        self.assertEqual(
            tools.validate_tool_call(
                "airlock_propose_restore_root",
                {
                    "session_id": "r-8f2c1a",
                    "reason": "Restore the original root model.",
                },
            ),
            {
                "session_id": "r-8f2c1a",
                "reason": "Restore the original root model.",
            },
        )

    def test_propose_chain_change_caps_and_peers(self) -> None:
        valid = tools.validate_tool_call(
            "airlock_propose_chain_change",
            {
                "chains": {
                    "gpt-5.6-sol": ["claude-opus-5[1m]", "grok-4.6"],
                    "claude-opus-5[1m]": ["gpt-5.6-sol"],
                },
                "reason": "Prefer another frontier peer before metered.",
            },
        )
        self.assertEqual(
            valid["chains"]["gpt-5.6-sol"],
            ["claude-opus-5[1m]", "grok-4.6"],
        )
        self.assert_invalid(
            "airlock_propose_chain_change",
            {
                "chains": {"gpt-5.6-sol": ["gpt-5.6-sol"]},
                "reason": "Self peer should fail validation hard.",
            },
        )
        self.assert_invalid(
            "airlock_propose_chain_change",
            {
                "chains": {"gpt-5.6-sol": ["grok-4.6", "grok-4.6"]},
                "reason": "Duplicate peers should fail validation.",
            },
        )
        too_many_peers = {
            "chains": {
                "gpt-5.6-sol": [f"model-{index}" for index in range(9)]
            },
            "reason": "Too many peers should fail validation.",
        }
        self.assert_invalid("airlock_propose_chain_change", too_many_peers)
        too_many_sources = {
            "chains": {f"model-{index}": [] for index in range(65)},
            "reason": "Too many sources should fail validation.",
        }
        self.assert_invalid("airlock_propose_chain_change", too_many_sources)
        self.assert_invalid(
            "airlock_propose_chain_change",
            {
                "chains": {},
                "reason": "Empty chains object should fail closed.",
            },
        )
        for chains in (
            {
                "claude-opus-5": ["gpt-5.6-sol"],
                "claude-opus-5[1m]": ["gpt-5.6-luna"],
            },
            {
                "claude-opus-5[1m]": ["gpt-5.6-luna"],
                "claude-opus-5": ["gpt-5.6-sol"],
            },
        ):
            self.assert_invalid(
                "airlock_propose_chain_change",
                {
                    "chains": chains,
                    "reason": "Alias duplicate sources must fail validation.",
                },
            )

    def test_oversized_array_rejected(self) -> None:
        self.assert_invalid(
            "airlock_propose_chain_change",
            {
                "chains": {"gpt-5.6-sol": ["x"] * 300},
                "reason": "Oversized peer array should fail closed.",
            },
        )


def malicious_blob() -> dict[str, Any]:
    return {
        "id": "r-8f2c1a",
        "state": "blocked",
        "blocked_reason": "rate_limit",
        "profile": "hybrid-anthropic-root",
        "root_model": "claude-fable-5-1[1m]",
        "root_provider": "anthropic",
        "active_model": "claude-opus-5[1m]",
        "project": "claudex",
        "started_at": "2026-09-05T08:41:03Z",
        "last_activity_at": "2026-09-05T09:02:11Z",
        "context": {"input_tokens": 100, "window": 1000},
        "workers": [{"model": "gpt-5.6-luna", "requests": 2}],
        "recent_handoffs": 1,
        "workdir": "C:\\secret\\path",
        "url": "http://127.0.0.1:39123",
        "token": "leak-token",
        "secret": "leak-secret",
        "credential": "leak-credential",
        "account": "leak-account",
        "csrf": "leak-csrf",
        "header": {"Authorization": "Bearer x"},
        "body": {"prompt": "do not leak"},
        "prompt": "system prompt",
        "response": "model response",
        "control": "control-token",
        "nonce": "nonce-value",
        "cookie": "session=1",
        "error": {"body": "upstream boom", "token": "nested-token"},
        "routes": [
            {
                "model": "gpt-5.6-sol",
                "short_name": "sol",
                "provider": "openai",
                "category": "included",
                "metered": False,
                "context_window": 400000,
                "effort_ceiling": "xhigh",
                "status": "ready",
                "sessions_using": ["r-8f2c1a"],
                "token": "route-token",
                "url": "http://evil",
            }
        ],
        "cooldowns": [
            {
                "scope": "model",
                "model": "gpt-5.6-sol",
                "provider": "openai",
                "remaining_seconds": 10,
                "secret": "cooldown-secret",
            }
        ],
        "chains": {"gpt-5.6-sol": ["gpt-5.6-terra"], "token": ["nope"]},
        "usage": [
            {
                "provider": "openai",
                "model": "gpt-5.6-luna",
                "requests": 14,
                "completed": 14,
                "errors": 0,
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_read_input_tokens": 3,
                "credential": "usage-cred",
            }
        ],
        "events": [
            {
                "timestamp": "2026-09-05T08:58:40Z",
                "kind": "rate_limit_failover_attempted",
                "model": "gpt-5.6-terra",
                "provider": "openai",
                "failover_from": "gpt-5.6-sol",
                "models_considered": 2,
                "prompt": "event-prompt",
                "body": "event-body",
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 6,
                    "token": 9,
                },
            }
        ],
        "last_handoff": {
            "at": "2026-09-05T08:58:40Z",
            "from_model": "gpt-5.6-sol",
            "to_model": "gpt-5.6-terra",
            "reason": "rate_limit",
            "csrf": "no",
        },
        "nested": {
            "deeper": {
                "token": "deep-token",
                "secret": "deep-secret",
                "workdir": "/tmp",
                "url": "http://x",
            }
        },
        "nan": math.nan,
        "inf": math.inf,
    }


def assert_no_forbidden(test: unittest.TestCase, value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = key.lower()
            for fragment in tools.FORBIDDEN_NAME_FRAGMENTS:
                if fragment in lowered and key not in tools.SAFE_COUNTER_KEYS:
                    test.fail(f"forbidden key survived projection: {key}")
            assert_no_forbidden(test, child)
    elif isinstance(value, list):
        for child in value:
            assert_no_forbidden(test, child)


class ProjectToolResultTests(unittest.TestCase):
    def test_each_tool_drops_forbidden_and_keeps_safe(self) -> None:
        blob = malicious_blob()
        cases = {
            "airlock_list_sessions": {"sessions": [blob]},
            "airlock_get_session": blob,
            "airlock_get_session_events": {"events": blob["events"]},
            "airlock_get_routes": {"routes": blob["routes"]},
            "airlock_get_headroom": {
                "headroom": [
                    {
                        "provider": "anthropic",
                        "window": "5h",
                        "used_percent": 10,
                        "resets_at": "2026-09-05T11:00:00Z",
                        "source": "status_line",
                        "token": "nope",
                        "account": "acct",
                    }
                ]
            },
            "airlock_get_global_failover_chain": {
                "chains": blob["chains"],
                "digest": "abc",
                "notice": "Applies to new sessions only.",
                "csrf": "nope",
            },
            "airlock_list_proposals": {
                "proposals": [
                    {
                        "id": "shp_1",
                        "kind": "session_handoff",
                        "session_id": "r-8f2c1a",
                        "operation": "pin",
                        "target_model": "claude-opus-5[1m]",
                        "reason": "Need a frontier peer now.",
                        "allow_metered": False,
                        "created_by": "agent",
                        "created_at": "2026-09-05T09:10:00Z",
                        "expires_at": "2026-09-05T09:25:00Z",
                        "revision": 1,
                        "status": "pending",
                        "last_error": {
                            "code": "route_cooling",
                            "message": "Target is cooling.",
                            "body": "raw",
                            "token": "x",
                        },
                        "token": "proposal-token",
                        "workdir": "/tmp",
                    }
                ]
            },
            "airlock_get_proposal": {
                "id": "shp_1",
                "kind": "session_handoff",
                "session_id": "r-8f2c1a",
                "operation": "pin",
                "target_model": "claude-opus-5[1m]",
                "reason": "Need a frontier peer now.",
                "status": "pending",
                "revision": 1,
                "last_error": {"code": "conflict", "message": "Changed."},
                "secret": "x",
            },
            "airlock_propose_session_handoff": {
                "id": "shp_1",
                "kind": "session_handoff",
                "session_id": "r-8f2c1a",
                "operation": "pin",
                "target_model": "claude-opus-5[1m]",
                "reason": "Need a frontier peer now.",
                "status": "pending",
                "revision": 1,
                "csrf": "x",
            },
            "airlock_propose_restore_root": {
                "id": "shp_2",
                "kind": "session_handoff",
                "session_id": "r-8f2c1a",
                "operation": "restore_root",
                "reason": "Restore the original root model.",
                "status": "pending",
                "revision": 1,
                "control": "x",
            },
            "airlock_propose_chain_change": {
                "id": "ccp_1",
                "kind": "chain_change",
                "chains": {"gpt-5.6-sol": ["claude-opus-5[1m]"]},
                "reason": "Prefer another frontier peer before metered.",
                "status": "pending",
                "revision": 1,
                "base_digest": "digest",
                "cookie": "x",
            },
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                projected = tools.project_tool_result(name, payload)
                assert_no_forbidden(self, projected)
                text = json.dumps(projected)
                for bad in (
                    "leak-token",
                    "leak-secret",
                    "leak-credential",
                    "leak-account",
                    "leak-csrf",
                    "system prompt",
                    "model response",
                    "control-token",
                    "nonce-value",
                    "session=1",
                    "upstream boom",
                    "C:\\\\secret\\\\path",
                    "http://127.0.0.1:39123",
                    "http://evil",
                ):
                    self.assertNotIn(bad, text)
                if name == "airlock_list_sessions":
                    self.assertEqual(projected["sessions"][0]["id"], "r-8f2c1a")
                    self.assertEqual(projected["sessions"][0]["project"], "claudex")
                    self.assertNotIn("workdir", projected["sessions"][0])
                    self.assertNotIn("url", projected["sessions"][0])
                if name == "airlock_get_session":
                    self.assertEqual(projected["id"], "r-8f2c1a")
                    self.assertIn("events", projected)
                    self.assertEqual(
                        projected["events"][0]["kind"],
                        "rate_limit_failover_attempted",
                    )
                if name == "airlock_list_proposals":
                    proposal = projected["proposals"][0]
                    self.assertEqual(proposal["id"], "shp_1")
                    self.assertEqual(proposal["last_error"]["code"], "route_cooling")
                    self.assertNotIn("body", proposal["last_error"])
                if name == "airlock_get_global_failover_chain":
                    self.assertEqual(
                        projected["chains"]["gpt-5.6-sol"], ["gpt-5.6-terra"]
                    )
                    self.assertEqual(projected["digest"], "abc")

    def test_history_results_keep_counts_and_drop_paths(self) -> None:
        item = {
            "id": "hx-0f3c9d2e-aaa", "session_id": "0f3c9d2e-aaaa-4bbb-8ccc-dddddddddddd", "project": "claudex",
            "workdir": "C:\\secret\\path", "path": "C:\\secret\\path\\x.jsonl", "title": "Fix the retry loop",
            "branch": "main", "models": ["claude-fable-5-1"], "primary_model": "claude-fable-5-1", "provider": "anthropic",
            "started_at": "2026-09-05T09:00:00Z", "last_activity_at": "2026-09-05T10:00:00Z", "prompts": 12, "replies": 40,
            "tool_calls": 33, "compactions": 1, "peak_context": 520500, "last_context": 30000, "window": 1000000,
            "output_tokens": 300, "subagents": 1, "entrypoint": "cli", "version": "2.1.0", "token": "leak-token",
        }
        listing = tools.project_tool_result("airlock_list_history", {
            "generated_at": "2026-09-05T10:00:00Z", "refreshed_at": None, "total": 1, "projects": ["claudex"],
            "models": ["claude-fable-5-1"], "sessions": [item], "csrf": "leak-csrf",
        })
        assert_no_forbidden(self, listing)
        self.assertEqual(listing["sessions"][0]["prompts"], 12)
        self.assertEqual(listing["sessions"][0]["peak_context"], 520500)
        self.assertNotIn("workdir", listing["sessions"][0])
        self.assertNotIn("path", listing["sessions"][0])
        self.assertIsNone(listing["refreshed_at"])
        detail = tools.project_tool_result("airlock_get_history_session", {
            **item,
            "agents": [{"id": "a1b2c3d4e5f6a7b8c", "agent_type": "airlock-sol", "description": "Fix tests", "model": "gpt-5.6-sol",
                        "background": True, "status": "async_launched", "prompts": 1, "replies": 2, "tool_calls": 3,
                        "peak_context": 700, "output_tokens": 50, "depth": 1, "path": "C:\\secret\\path"}],
            "periods": [{"kind": "airlock", "from": "2026-09-05T09:00:00Z", "to": "2026-09-05T10:00:00Z", "profile": "hybrid",
                         "root_model": "claude-fable-5-1", "open": False}],
            "airlock_inferred": False,
            "compactions_by_model": {"claude-fable-5-1": 1, "system prompt": 9, "bad": -1, "worse": "x"},
            "tools": {"Bash": 20, "Edit": 13},
            "agent_types": {"airlock-sol": 1},
            "activity": [{"at": "2026-09-05T09:59:00Z", "kind": "tool", "tool": "Bash", "preview": "Run the suite"},
                         {"at": "2026-09-05T09:58:00Z", "kind": "shout", "preview": "dropped"}],
        })
        assert_no_forbidden(self, detail)
        self.assertEqual(detail["compactions_by_model"], {"claude-fable-5-1": 1})
        self.assertEqual(detail["tools"], {"Bash": 20, "Edit": 13})
        self.assertEqual(detail["agents"][0]["agent_type"], "airlock-sol")
        self.assertNotIn("path", detail["agents"][0])
        # Projection is an allowlist per field: an unknown kind is dropped
        # from its item, the item itself stays.
        self.assertEqual(detail["activity"][0]["kind"], "tool")
        self.assertNotIn("kind", detail["activity"][1])
        feed = tools.project_tool_result("airlock_get_subagent_feed", {
            "id": "a1b2c3d4e5f6a7b8c", "model": "gpt-5.6-sol", "last_activity_at": None,
            "context": {"input_tokens": 77, "window": 272000}, "activity": [], "url": "http://evil",
        })
        assert_no_forbidden(self, feed)
        self.assertEqual(feed["context"], {"input_tokens": 77, "window": 272000})
        self.assertIsNone(feed["last_activity_at"])
        usage = tools.project_tool_result("airlock_get_usage", {
            "generated_at": "2026-09-05T10:00:00Z", "group": "day", "since": None, "until": None,
            "filters": {"project": None, "model": None},
            "totals": {"sessions": 2, "prompts": 3, "replies": 5, "tool_calls": 3, "output_tokens": 340, "compactions": 1,
                       "subagents": 1, "peak_context_max": 520500},
            "series": [{"period": "2026-09-05", "sessions": 1, "prompts": 2, "replies": 3, "tool_calls": 2, "output_tokens": 300,
                        "compactions": 1, "by_provider": {"anthropic": 3}}],
            "agent_types": [{"name": "airlock-sol", "count": 1}], "tools": [{"name": "Bash", "count": 2}],
            "projects": [{"name": "claudex", "sessions": 1, "prompts": 2, "tool_calls": 2, "output_tokens": 300}],
            "models": [{"name": "claude-fable-5-1", "replies": 3, "provider": "anthropic"}],
            "peaks": [{"id": "hx-0f3c9d2e-aaa", "project": "claudex", "title": "Fix", "peak_context": 520500, "window": 1000000}],
            "peak_buckets": {"under_25": 0, "25_to_50": 0, "50_to_75": 1, "over_75": 0, "unknown": 1},
        })
        assert_no_forbidden(self, usage)
        self.assertEqual(usage["totals"]["prompts"], 3)
        self.assertEqual(usage["series"][0]["by_provider"], {"anthropic": 3})
        self.assertEqual(usage["peak_buckets"]["50_to_75"], 1)
        result = tools.project_tool_result("airlock_query_history", {
            "generated_at": "2026-09-05T10:00:00Z", "dimension": "model", "order_by": "compactions", "descending": True,
            "since": None, "until": None,
            "filters": {"project": "claudex", "model": None, "provider": None, "agent_type": None, "tool": None,
                        "branch": None, "entrypoint": None, "q": None},
            "sessions_matched": 2, "total_rows": 1, "truncated": False,
            "rows": [{"key": "claude-fable-5-1", "id": None, "label": None, "sessions": 2, "prompts": None, "replies": 3,
                      "tool_calls": 2, "output_tokens": 300, "compactions": 1, "agent_launches": None, "peak_context_max": None,
                      "workdir": "C:\\secret\\path"}],
            "notes": ["Counts, not cost."],
        })
        assert_no_forbidden(self, result)
        row = result["rows"][0]
        self.assertEqual(row["compactions"], 1)
        self.assertIsNone(row["prompts"])
        self.assertIsNone(row["agent_launches"])
        self.assertNotIn("workdir", row)
        self.assertEqual(result["filters"]["project"], "claudex")
        self.assertIsNone(result["filters"]["model"])

    def test_unavailable_global_chain_keeps_null_fields(self) -> None:
        projected = tools.project_tool_result(
            "airlock_get_global_failover_chain",
            {
                "chains": None,
                "digest": None,
                "notice": "Applies to new sessions only.",
                "unavailable": True,
                "reason": "unreadable",
                "csrf": "nope",
                "token": "leak-token",
            },
        )
        self.assertIsNone(projected["chains"])
        self.assertIsNone(projected["digest"])
        self.assertIs(projected["unavailable"], True)
        self.assertEqual(projected["reason"], "unreadable")
        self.assertEqual(projected["notice"], "Applies to new sessions only.")
        self.assertNotIn("csrf", projected)
        self.assertNotIn("token", projected)

        helper_missing = tools.project_tool_result(
            "airlock_get_global_failover_chain",
            {
                "chains": None,
                "digest": None,
                "notice": "Applies to new sessions only.",
                "unavailable": True,
                "reason": "helper_unavailable",
            },
        )
        self.assertIsNone(helper_missing["chains"])
        self.assertIsNone(helper_missing["digest"])
        self.assertEqual(helper_missing["reason"], "helper_unavailable")

    def test_non_finite_numbers_dropped(self) -> None:
        projected = tools.project_tool_result(
            "airlock_get_headroom",
            {
                "headroom": [
                    {
                        "provider": "anthropic",
                        "window": "5h",
                        "used_percent": math.nan,
                        "resets_at": None,
                        "source": "unknown",
                    }
                ]
            },
        )
        self.assertNotIn("used_percent", projected["headroom"][0])


class McpProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = mcp.ConsoleToolsServer(tools, console_url="http://127.0.0.1:4783")

    def test_initialize_list_ping_unknown_notification(self) -> None:
        init = self.server.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        assert init is not None
        self.assertEqual(init["result"]["protocolVersion"], mcp.PROTOCOL_VERSION)
        self.assertEqual(init["result"]["serverInfo"]["name"], "airlock-console-tools")

        listed = self.server.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        assert listed is not None
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertEqual(names, EXPECTED_NAMES)
        self.assertEqual(listed["result"]["tools"], tools.tool_manifest())

        ping = self.server.handle_message(
            {"jsonrpc": "2.0", "id": 3, "method": "ping"}
        )
        assert ping is not None
        self.assertEqual(ping["result"], {})

        unknown = self.server.handle_message(
            {"jsonrpc": "2.0", "id": 4, "method": "nope"}
        )
        assert unknown is not None
        self.assertEqual(unknown["error"]["code"], -32601)

        self.assertIsNone(
            self.server.handle_message(
                {"jsonrpc": "2.0", "method": "notifications/initialized"}
            )
        )

    def test_listed_definitions_match_shared_manifest(self) -> None:
        listed = self.server.tools()
        shared = tools.tool_manifest()
        self.assertEqual(listed, shared)
        self.assertEqual(
            json.dumps(listed, sort_keys=True, separators=(",", ":")),
            json.dumps(shared, sort_keys=True, separators=(",", ":")),
        )


class FakeConsoleServer:
    def __init__(self, payload: object, *, status: int = 200, raw: bytes | None = None):
        self.payload = payload
        self.status = status
        self.raw = raw
        self.requests: list[dict[str, Any]] = []
        self._httpd: HTTPServer | None = None
        self.thread: Thread | None = None
        self.url = ""

    def start(self) -> str:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                parent.requests.append(
                    {
                        "path": self.path,
                        "headers": {k: v for k, v in self.headers.items()},
                        "body": body,
                    }
                )
                raw = parent.raw
                if raw is None:
                    raw = json.dumps(parent.payload).encode("utf-8")
                self.send_response(parent.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                del format, args

        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{port}"
        self.thread = Thread(target=self._httpd.serve_forever, daemon=True)
        self.thread.start()
        return self.url

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)


class McpHttpTests(unittest.TestCase):
    def test_successful_call_posts_normalized_json_without_auth_headers(self) -> None:
        fake = FakeConsoleServer(
            {
                "id": "r-8f2c1a",
                "state": "idle",
                "project": "claudex",
                "root_model": "claude-fable-5-1[1m]",
                "token": "should-drop",
                "workdir": "C:\\secret",
                "url": "http://127.0.0.1:9",
            }
        )
        url = fake.start()
        try:
            server = mcp.ConsoleToolsServer(tools, console_url=url)
            reply = server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {
                        "name": "airlock_get_session",
                        "arguments": {"session_id": "r-8f2c1a", "extra": 1},
                    },
                }
            )
            # Local validation rejects extra before HTTP.
            assert reply is not None
            self.assertTrue(reply["result"]["isError"])
            self.assertEqual(fake.requests, [])

            reply = server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "tools/call",
                    "params": {
                        "name": "airlock_get_session",
                        "arguments": {"session_id": "r-8f2c1a"},
                    },
                }
            )
            assert reply is not None
            self.assertFalse(reply["result"]["isError"])
            body = json.loads(reply["result"]["content"][0]["text"])
            self.assertEqual(body["id"], "r-8f2c1a")
            self.assertNotIn("token", body)
            self.assertNotIn("workdir", body)
            self.assertNotIn("url", body)
            self.assertEqual(len(fake.requests), 1)
            request = fake.requests[0]
            self.assertEqual(request["path"], "/api/tools/call")
            headers = {k.lower(): v for k, v in request["headers"].items()}
            self.assertEqual(headers.get("content-type"), "application/json")
            self.assertNotIn("origin", headers)
            self.assertNotIn("authorization", headers)
            self.assertNotIn("x-airlock-csrf", headers)
            self.assertNotIn("cookie", headers)
            payload = json.loads(request["body"].decode("utf-8"))
            self.assertEqual(
                payload,
                {"name": "airlock_get_session", "arguments": {"session_id": "r-8f2c1a"}},
            )
        finally:
            fake.stop()

    def test_console_absence_guidance(self) -> None:
        server = mcp.ConsoleToolsServer(
            tools, console_url="http://127.0.0.1:1"
        )
        reply = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {
                    "name": "airlock_list_sessions",
                    "arguments": {},
                },
            }
        )
        assert reply is not None
        self.assertTrue(reply["result"]["isError"])
        self.assertEqual(
            reply["result"]["content"][0]["text"],
            mcp.CONSOLE_ABSENT_MESSAGE,
        )

    def test_http_error_suppresses_body(self) -> None:
        fake = FakeConsoleServer({"error": "secret-body", "token": "x"}, status=500)
        url = fake.start()
        try:
            server = mcp.ConsoleToolsServer(tools, console_url=url)
            reply = server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 12,
                    "method": "tools/call",
                    "params": {
                        "name": "airlock_list_sessions",
                        "arguments": {},
                    },
                }
            )
            assert reply is not None
            self.assertTrue(reply["result"]["isError"])
            text = reply["result"]["content"][0]["text"]
            self.assertEqual(text, mcp.HTTP_ERROR_MESSAGE)
            self.assertNotIn("secret-body", text)
            self.assertNotIn("token", text)
        finally:
            fake.stop()

    def test_oversized_and_malformed_responses(self) -> None:
        huge = FakeConsoleServer({}, raw=b"{" + (b"x" * (mcp.MAX_RESPONSE_BYTES + 10)))
        url = huge.start()
        try:
            server = mcp.ConsoleToolsServer(tools, console_url=url)
            reply = server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 13,
                    "method": "tools/call",
                    "params": {"name": "airlock_list_sessions", "arguments": {}},
                }
            )
            assert reply is not None
            self.assertTrue(reply["result"]["isError"])
            self.assertIn(
                reply["result"]["content"][0]["text"],
                {mcp.OVERSIZED_RESPONSE_MESSAGE, mcp.MALFORMED_RESPONSE_MESSAGE},
            )
        finally:
            huge.stop()

        bad = FakeConsoleServer({}, raw=b"not-json")
        url = bad.start()
        try:
            server = mcp.ConsoleToolsServer(tools, console_url=url)
            reply = server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 14,
                    "method": "tools/call",
                    "params": {"name": "airlock_get_headroom", "arguments": {}},
                }
            )
            assert reply is not None
            self.assertTrue(reply["result"]["isError"])
            self.assertEqual(
                reply["result"]["content"][0]["text"],
                mcp.MALFORMED_RESPONSE_MESSAGE,
            )
        finally:
            bad.stop()

    def test_console_url_validation(self) -> None:
        with self.assertRaises(mcp.ConsoleToolsError):
            mcp.parse_console_url("http://127.0.0.1:4783/api")
        with self.assertRaises(mcp.ConsoleToolsError):
            mcp.parse_console_url("http://127.0.0.1:4783/")
        with self.assertRaises(mcp.ConsoleToolsError):
            mcp.parse_console_url("http://user:pass@127.0.0.1:4783")
        with self.assertRaises(mcp.ConsoleToolsError):
            mcp.parse_console_url("http://127.0.0.1:4783?x=1")
        with self.assertRaises(mcp.ConsoleToolsError):
            mcp.parse_console_url("https://127.0.0.1:4783")
        with self.assertRaises(mcp.ConsoleToolsError):
            mcp.parse_console_url("http://localhost:4783")
        self.assertEqual(
            mcp.parse_console_url("http://127.0.0.1:4790"),
            "http://127.0.0.1:4790",
        )

    def test_main_and_serve_in_memory(self) -> None:
        lines = "\n".join(
            (
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
                "not-json",
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            )
        )
        output = io.StringIO()
        code = mcp.serve(
            stream_in=io.StringIO(lines + "\n"),
            stream_out=output,
            contract=tools,
            console_url="http://127.0.0.1:4783",
        )
        self.assertEqual(code, 0)
        replies = [
            json.loads(line)
            for line in output.getvalue().splitlines()
            if line.strip()
        ]
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[0]["id"], 1)
        self.assertEqual(
            [tool["name"] for tool in replies[1]["result"]["tools"]],
            EXPECTED_NAMES,
        )

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "not_the_contract.py"
            bad.write_text("x=1\n", encoding="utf-8")
            stderr = io.StringIO()
            with mock.patch.object(sys, "stderr", stderr):
                code = mcp.main(
                    ["--contract", str(bad), "--console-url", "http://127.0.0.1:4783"],
                    stream_in=io.StringIO(""),
                    stream_out=io.StringIO(),
                )
            self.assertEqual(code, 2)
            self.assertIn("basename", stderr.getvalue())

            good_out = io.StringIO()
            code = mcp.main(
                [
                    "--contract",
                    str(CONTRACT_PATH),
                    "--console-url",
                    "http://127.0.0.1:4783",
                ],
                stream_in=io.StringIO(
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
                ),
                stream_out=good_out,
            )
            self.assertEqual(code, 0)
            self.assertIn('"id":1', good_out.getvalue().replace(" ", ""))


class ConsoleDiscoveryTests(unittest.TestCase):
    def test_shared_default_runtime_and_address_helpers(self) -> None:
        self.assertEqual(tools.DEFAULT_CONSOLE_URL, mcp.DEFAULT_CONSOLE_URL)
        self.assertEqual(
            tools.default_console_address_file(),
            tools.default_console_runtime_root() / "console-address.json",
        )
        self.assertEqual(
            mcp.default_console_address_file(),
            mcp.default_console_runtime_root() / "console-address.json",
        )
        self.assertEqual(
            tools.default_console_address_file(),
            mcp.default_console_address_file(),
        )

    def test_cli_omission_enables_discovery_file(self) -> None:
        args = mcp.build_parser().parse_args(
            ["--contract", str(CONTRACT_PATH), "--discovery-file", "custom.json"]
        )
        self.assertIsNone(args.console_url)
        self.assertEqual(args.discovery_file, "custom.json")

    def test_discovers_custom_port_4900(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "console-address.json"
            write_console_address(marker, "http://127.0.0.1:4900")
            calls: list[str] = []

            def post(url: str, name: str, arguments: dict[str, Any]) -> object:
                del name, arguments
                calls.append(url)
                return {"sessions": []}

            server = mcp.ConsoleToolsServer(
                tools,
                discovery_file=marker,
                post_call=post,
            )
            text, is_error = server.call_tool("airlock_list_sessions", {})
            self.assertFalse(is_error)
            self.assertEqual(json.loads(text), {"sessions": []})
            self.assertEqual(calls, ["http://127.0.0.1:4900"])

    def test_rereads_changed_marker_on_every_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "console-address.json"
            calls: list[str] = []

            def post(url: str, name: str, arguments: dict[str, Any]) -> object:
                del name, arguments
                calls.append(url)
                return {"sessions": []}

            server = mcp.ConsoleToolsServer(
                tools,
                discovery_file=marker,
                post_call=post,
            )
            write_console_address(marker, "http://127.0.0.1:4900")
            self.assertFalse(server.call_tool("airlock_list_sessions", {})[1])
            write_console_address(marker, "http://127.0.0.1:4901")
            self.assertFalse(server.call_tool("airlock_list_sessions", {})[1])
            self.assertEqual(
                calls,
                ["http://127.0.0.1:4900", "http://127.0.0.1:4901"],
            )

    def test_stale_custom_address_falls_back_to_default_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "console-address.json"
            write_console_address(marker, "http://127.0.0.1:4900")
            calls: list[str] = []

            def post(url: str, name: str, arguments: dict[str, Any]) -> object:
                del name, arguments
                calls.append(url)
                if url != mcp.DEFAULT_CONSOLE_URL:
                    raise mcp.ConsoleConnectionError(mcp.CONSOLE_ABSENT_MESSAGE)
                return {"sessions": []}

            server = mcp.ConsoleToolsServer(
                tools,
                discovery_file=marker,
                post_call=post,
            )
            text, is_error = server.call_tool("airlock_list_sessions", {})
            self.assertFalse(is_error)
            self.assertEqual(json.loads(text), {"sessions": []})
            self.assertEqual(
                calls,
                ["http://127.0.0.1:4900", mcp.DEFAULT_CONSOLE_URL],
            )

    def test_answering_custom_console_error_does_not_fall_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "console-address.json"
            write_console_address(marker, "http://127.0.0.1:4900")
            calls: list[str] = []

            def post(url: str, name: str, arguments: dict[str, Any]) -> object:
                del name, arguments
                calls.append(url)
                raise mcp.ConsoleToolsError(mcp.HTTP_ERROR_MESSAGE)

            server = mcp.ConsoleToolsServer(
                tools,
                discovery_file=marker,
                post_call=post,
            )
            text, is_error = server.call_tool("airlock_list_sessions", {})
            self.assertTrue(is_error)
            self.assertEqual(text, mcp.HTTP_ERROR_MESSAGE)
            self.assertEqual(calls, ["http://127.0.0.1:4900"])

    def test_explicit_override_wins_without_reading_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "console-address.json"
            write_console_address(marker, "http://127.0.0.1:4900")
            calls: list[str] = []

            def post(url: str, name: str, arguments: dict[str, Any]) -> object:
                del name, arguments
                calls.append(url)
                return {"sessions": []}

            server = mcp.ConsoleToolsServer(
                tools,
                console_url="http://127.0.0.1:4902",
                discovery_file=marker,
                post_call=post,
            )
            with mock.patch.object(
                mcp,
                "load_console_address",
                side_effect=AssertionError("explicit URL read discovery marker"),
            ):
                text, is_error = server.call_tool("airlock_list_sessions", {})
            self.assertFalse(is_error)
            self.assertEqual(json.loads(text), {"sessions": []})
            self.assertEqual(calls, ["http://127.0.0.1:4902"])
    def test_missing_malformed_and_symlink_markers_use_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing-console-address.json"
            self.assertEqual(
                mcp.console_candidates(None, missing),
                [mcp.DEFAULT_CONSOLE_URL],
            )

            malformed = root / "malformed-console-address.json"
            malformed.write_text(
                json.dumps({"schema_version": 0, "url": "http://127.0.0.1:4900"}),
                encoding="utf-8",
            )
            self.assertEqual(
                mcp.console_candidates(None, malformed),
                [mcp.DEFAULT_CONSOLE_URL],
            )

            target = root / "real-console-address.json"
            write_console_address(target, "http://127.0.0.1:4900")
            link = root / "linked-console-address.json"
            try:
                link.symlink_to(target)
            except OSError:
                return
            self.assertIsNone(mcp.load_console_address(link))
            self.assertEqual(
                mcp.console_candidates(None, link),
                [mcp.DEFAULT_CONSOLE_URL],
            )

    def test_reparse_marker_is_rejected_before_and_after_open(self) -> None:
        reparse_flag = 0x400
        regular = mock.Mock(
            st_mode=mcp.stat.S_IFREG,
            st_file_attributes=0,
            st_dev=1,
            st_ino=2,
            st_size=10,
        )
        reparse = mock.Mock(
            st_mode=mcp.stat.S_IFREG,
            st_file_attributes=reparse_flag,
            st_dev=1,
            st_ino=2,
            st_size=10,
        )
        marker = Path("console-address.json")
        with mock.patch.object(
            mcp.stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            reparse_flag,
            create=True,
        ):
            with mock.patch.object(mcp.os, "lstat", return_value=reparse):
                with mock.patch.object(mcp.os, "open") as open_file:
                    self.assertIsNone(
                        mcp._read_regular_file_no_follow(marker, 1024)
                    )
                    open_file.assert_not_called()

            with mock.patch.object(mcp.os, "lstat", return_value=regular):
                with mock.patch.object(mcp.os, "open", return_value=7):
                    with mock.patch.object(mcp.os, "fstat", return_value=reparse):
                        with mock.patch.object(mcp.os, "close") as close_file:
                            self.assertIsNone(
                                mcp._read_regular_file_no_follow(marker, 1024)
                            )
                            close_file.assert_called_once_with(7)

    def test_address_loader_is_bounded_and_schema_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "console-address.json"
            write_console_address(marker, "http://127.0.0.1:4900")
            self.assertEqual(
                mcp.load_console_address(marker),
                "http://127.0.0.1:4900",
            )
            write_console_address(marker, "http://127.0.0.1:4900/")
            self.assertIsNone(mcp.load_console_address(marker))

            invalid_payloads: list[object] = [
                {
                    "schema_version": 1,
                    "url": "http://127.0.0.1:4900",
                    "pid": 12345,
                    "console_id": "a" * 64,
                    "extra": "not allowed",
                },
                {
                    "schema_version": True,
                    "url": "http://127.0.0.1:4900",
                    "pid": 12345,
                    "console_id": "a" * 64,
                },
                {
                    "schema_version": 1,
                    "url": "http://127.0.0.1:4900",
                    "pid": True,
                    "console_id": "a" * 64,
                },
                {
                    "schema_version": 1,
                    "url": "http://127.0.0.1:4900",
                    "pid": mcp.MAX_PROCESS_ID + 1,
                    "console_id": "a" * 64,
                },
                {
                    "schema_version": 1,
                    "url": "http://localhost:4900",
                    "pid": 12345,
                    "console_id": "a" * 64,
                },
                {
                    "schema_version": 1,
                    "url": "http://127.0.0.1:4900/path",
                    "pid": 12345,
                    "console_id": "A" * 64,
                },
            ]
            for payload in invalid_payloads:
                marker.write_text(json.dumps(payload), encoding="utf-8")
                self.assertIsNone(mcp.load_console_address(marker))

            marker.write_text(
                '{"schema_version":1,"schema_version":1,'
                '"url":"http://127.0.0.1:4900","pid":1,'
                '"console_id":"' + ("a" * 64) + '"}',
                encoding="utf-8",
            )
            self.assertIsNone(mcp.load_console_address(marker))

            marker.write_bytes(b"x" * (mcp.MAX_CONSOLE_ADDRESS_BYTES + 1))
            self.assertIsNone(mcp.load_console_address(marker))

    def test_marker_fields_never_enter_request_result_or_log(self) -> None:
        fake = FakeConsoleServer({"sessions": []})
        url = fake.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                marker = Path(tmp) / "console-address.json"
                marker_console_id = "feedface" * 8
                marker_pid = 987654321
                write_console_address(
                    marker,
                    url,
                    pid=marker_pid,
                    console_id=marker_console_id,
                )
                server = mcp.ConsoleToolsServer(tools, discovery_file=marker)
                stderr = io.StringIO()
                with mock.patch.object(sys, "stderr", stderr):
                    text, is_error = server.call_tool("airlock_list_sessions", {})
                self.assertFalse(is_error)
                self.assertEqual(json.loads(text), {"sessions": []})
                self.assertEqual(len(fake.requests), 1)
                request = fake.requests[0]
                exposed = "\n".join(
                    (
                        request["body"].decode("utf-8"),
                        json.dumps(request["headers"], sort_keys=True),
                        text,
                        stderr.getvalue(),
                    )
                )
                self.assertNotIn(marker_console_id, exposed)
                self.assertNotIn(str(marker_pid), exposed)
                self.assertNotIn("schema_version", exposed)
                self.assertNotIn("console_id", exposed)
        finally:
            fake.stop()


class ContractLoadTests(unittest.TestCase):
    def test_rejects_symlink_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "airlock_console_tools.py"
            target.write_text(CONTRACT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            link = Path(tmp) / "link_airlock_console_tools.py"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlinks unavailable")
            # Basename still wrong for link name; rename style check:
            named = Path(tmp) / "airlock_console_tools.py.link"
            try:
                named.unlink()
            except FileNotFoundError:
                pass
            # Use a symlink that keeps the required basename.
            linked = Path(tmp) / "subdir"
            linked.mkdir()
            linked_contract = linked / "airlock_console_tools.py"
            try:
                linked_contract.symlink_to(target)
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.assertRaises(mcp.ConsoleToolsError):
                mcp.load_contract(linked_contract)


class RedirectRefusalTests(unittest.TestCase):
    def _redirect_server(self, location: str) -> FakeConsoleServer:
        class Redirecting(FakeConsoleServer):
            def start(self) -> str:
                parent = self

                class Handler(BaseHTTPRequestHandler):
                    def do_POST(self) -> None:  # noqa: N802
                        length = int(self.headers.get("Content-Length", "0"))
                        body = self.rfile.read(length)
                        parent.requests.append(
                            {
                                "path": self.path,
                                "headers": {k: v for k, v in self.headers.items()},
                                "body": body,
                            }
                        )
                        self.send_response(302)
                        self.send_header("Location", location)
                        self.send_header("Content-Length", "0")
                        self.end_headers()

                    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                        del format, args

                self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
                port = self._httpd.server_address[1]
                self.url = f"http://127.0.0.1:{port}"
                self.thread = Thread(target=self._httpd.serve_forever, daemon=True)
                self.thread.start()
                return self.url

        server = Redirecting({})
        return server

    def test_refuses_external_and_loopback_redirects(self) -> None:
        sink_hits: list[str] = []

        class Sink(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                sink_hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def do_POST(self) -> None:  # noqa: N802
                sink_hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                del format, args

        sink = HTTPServer(("127.0.0.1", 0), Sink)
        sink_thread = Thread(target=sink.serve_forever, daemon=True)
        sink_thread.start()
        sink_url = f"http://127.0.0.1:{sink.server_address[1]}/api/tools/call"
        try:
            for location in ("https://example.com/steal", sink_url):
                fake = self._redirect_server(location)
                url = fake.start()
                try:
                    with self.assertRaises(mcp.ConsoleToolsError) as ctx:
                        mcp.post_tool_call(url, "airlock_list_sessions", {})
                    self.assertEqual(str(ctx.exception), mcp.HTTP_ERROR_MESSAGE)
                    self.assertEqual(len(fake.requests), 1)
                    self.assertEqual(sink_hits, [])
                finally:
                    fake.stop()
        finally:
            sink.shutdown()
            sink.server_close()
            sink_thread.join(timeout=2)


class BoundedStdinTests(unittest.TestCase):
    def test_oversized_line_and_deep_nesting_keep_serving(self) -> None:
        deep = "[" * 2000 + "]" * 2000
        oversized = "x" * (mcp.MAX_STDIN_LINE_BYTES + 32)
        lines = "\n".join(
            (
                oversized,
                deep,
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            )
        )
        output = io.StringIO()
        code = mcp.serve(
            stream_in=io.StringIO(lines + "\n"),
            stream_out=output,
            contract=tools,
            console_url="http://127.0.0.1:4783",
        )
        self.assertEqual(code, 0)
        replies = [
            json.loads(line)
            for line in output.getvalue().splitlines()
            if line.strip()
        ]
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["id"], 1)
        self.assertEqual(replies[0]["result"], {})


if __name__ == "__main__":
    unittest.main()
