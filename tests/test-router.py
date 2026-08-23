#!/usr/bin/env python3
"""Protocol tests for the session-scoped native model router."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import zlib

ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "bin" / "airlock-router.py"
SPEC = importlib.util.spec_from_file_location("airlock_router_test", ROUTER)
assert SPEC is not None and SPEC.loader is not None
router = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(router)
POLICY = router.load_policy_schema()


def write_test_snapshot(
    directory: str | Path,
    *,
    routes: dict[str, str] | None = None,
    profile: str = "openai-pure",
    openrouter: dict[str, dict[str, str]] | None = None,
    agents: dict[str, dict[str, object]] | None = None,
) -> tuple[Path, str]:
    routes = routes or {"gpt-test": "openai"}
    root_model, root_provider = next(iter(routes.items()))
    snapshot = POLICY.validate_session_snapshot({
        "schema_version": 1,
        "protocol_version": router.MANAGED_PROTOCOL_VERSION,
        "profile": profile,
        "root_model": root_model,
        "root_provider": root_provider,
        "routes": routes,
        "agents": agents or {
            "airlock-test": {
                "model": root_model,
                "provider": root_provider,
                "extra_usage": False,
            }
        },
        "openrouter": openrouter or {},
    })
    path = Path(directory) / "session.json"
    path.write_bytes(snapshot.canonical_bytes())
    return path, snapshot.digest()


TEST_OPENROUTER_METADATA = {
    "vendor/model-test": {
        "endpoint_provider": "deepinfra/fp4",
        "provider_name": "DeepInfra",
        "provider_slug": "deepinfra",
        "quantization": "fp4",
        "canonical_slug": "vendor/model-test-20260810",
    }
}


class RecordingServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), RecordingHandler)
        self.requests: list[dict[str, object]] = []
        self.mode = "json"
        self.delay = 0.25
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0


class RecordingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def recorder(self) -> RecordingServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers["content-length"])
        body = self.rfile.read(length)
        with self.recorder.lock:
            self.recorder.requests.append({
                "path": self.path,
                "headers": {
                    name.lower(): value for name, value in self.headers.items()
                },
                "body": body,
            })
        if self.recorder.mode.startswith("or_"):
            request = json.loads(body)
            response_model = request["model"]
            if self.recorder.mode == "or_list_model_json":
                response_model = []
            elif self.recorder.mode == "or_object_model_json":
                response_model = {"id": "vendor/model-test"}
            elif "mismatch" in self.recorder.mode:
                response_model = "vendor/different-model"
            elif "canonical" in self.recorder.mode:
                response_model = "vendor/model-test-20260810"
            if self.recorder.mode in {
                "or_stream",
                "or_mismatch_stream",
                "or_canonical_stream",
            }:
                start = json.dumps({
                    "type": "message_start",
                    "message": {
                        "id": "msg_openrouter",
                        "model": response_model,
                        "content": [],
                        "usage": {"input_tokens": 5, "output_tokens": 1},
                    },
                }, separators=(",", ":"))
                payload = (
                    "event: message_start\ndata: " + start + "\n\n"
                    'event: message_stop\ndata: {"type":"message_stop"}\n\n'
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("connection", "close")
                self.end_headers()
                try:
                    for index in range(0, len(payload), 5):
                        self.wfile.write(payload[index : index + 5])
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    pass
                self.close_connection = True
                return
            if self.recorder.mode == "or_malformed":
                response = b'{"model":'
            else:
                response = json.dumps({
                    "id": "msg_openrouter",
                    "model": response_model,
                    "content": [{"type": "text", "text": "hello"}],
                    "usage": {"input_tokens": 7, "output_tokens": 9},
                }, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            return
        if self.recorder.mode == "redirect":
            self.send_response(307)
            self.send_header("location", "http://invalid.example/v1/messages")
            self.send_header("content-length", "0")
            self.end_headers()
            return
        if self.recorder.mode == "headers_delay":
            time.sleep(self.recorder.delay)
        if self.recorder.mode == "error":
            response = b'{"type":"error","error":{"type":"rate_limit_error"}}'
            self.send_response(429)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            return
        if self.recorder.mode == "stream":
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(b"event: message_start\ndata: one\n\n")
            self.wfile.flush()
            time.sleep(self.recorder.delay)
            try:
                self.wfile.write(b"event: message_stop\ndata: two\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass
            self.close_connection = True
            return
        if self.recorder.mode == "partial":
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", "100")
            self.end_headers()
            self.wfile.write(b'{"partial":true}')
            self.wfile.flush()
            self.close_connection = True
            return
        if self.recorder.mode in {
            "gzip_usage_stream",
            "gzip_usage_json",
            "unreadable_encoding_stream",
        }:
            # Anthropic answers real traffic with Content-Encoding: gzip, so a
            # byte-level observer sees compressed data and can never find a
            # usage object unless it decodes a copy first.
            if self.recorder.mode == "gzip_usage_json":
                body = json.dumps({
                    "id": "msg_test",
                    "content": [{"type": "text", "text": "hello"}],
                    "usage": {"input_tokens": 31, "output_tokens": 42},
                }, separators=(",", ":")).encode("utf-8")
                content_type = "application/json"
            else:
                start = json.dumps({
                    "type": "message_start",
                    "message": {
                        "id": "msg_test",
                        "content": [],
                        "usage": {"input_tokens": 900, "output_tokens": 1},
                    },
                }, separators=(",", ":"))
                delta = json.dumps({
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 250},
                }, separators=(",", ":"))
                body = (
                    "event: message_start\ndata: " + start + "\n\n"
                    "event: message_delta\ndata: " + delta + "\n\n"
                    'event: message_stop\ndata: {"type":"message_stop"}\n\n'
                ).encode("utf-8")
                content_type = "text/event-stream"
            if self.recorder.mode == "unreadable_encoding_stream":
                encoded = b"encoding the standard library cannot read"
                encoding = "br"
            else:
                compressor = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
                encoded = compressor.compress(body) + compressor.flush()
                encoding = "gzip"
            self.send_response(200)
            self.send_header("content-type", content_type)
            self.send_header("content-encoding", encoding)
            self.send_header("content-length", str(len(encoded)))
            self.send_header("connection", "close")
            self.end_headers()
            try:
                for index in range(0, len(encoded), 7):
                    self.wfile.write(encoded[index : index + 7])
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass
            self.close_connection = True
            return
        if self.recorder.mode in {"usage_stream", "no_usage_stream"}:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            if self.recorder.mode == "usage_stream":
                start = json.dumps({
                    "type": "message_start",
                    "message": {
                        "id": "msg_test",
                        "content": [],
                        "usage": {
                            "input_tokens": 1200,
                            "output_tokens": 1,
                            "cache_read_input_tokens": 64,
                        },
                    },
                }, separators=(",", ":"))
                delta = json.dumps({
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 350},
                }, separators=(",", ":"))
            else:
                start = json.dumps({
                    "type": "message_start",
                    "message": {"id": "msg_test", "content": []},
                }, separators=(",", ":"))
                delta = json.dumps({
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                }, separators=(",", ":"))
            payload = (
                "event: message_start\ndata: " + start + "\n\n"
                "event: message_delta\ndata: " + delta + "\n\n"
                'event: message_stop\ndata: {"type":"message_stop"}\n\n'
            ).encode("utf-8")
            for index in range(0, len(payload), 7):
                self.wfile.write(payload[index : index + 7])
                self.wfile.flush()
            self.close_connection = True
            return
        if self.recorder.mode == "usage_json":
            response = json.dumps({
                "id": "msg_test",
                "content": [{"type": "text", "text": "hello"}],
                "usage": {"input_tokens": 11, "output_tokens": 22},
            }, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            return
        if self.recorder.mode == "delay":
            with self.recorder.lock:
                self.recorder.active += 1
                self.recorder.max_active = max(
                    self.recorder.max_active, self.recorder.active
                )
            try:
                time.sleep(self.recorder.delay)
            finally:
                with self.recorder.lock:
                    self.recorder.active -= 1
        response = json.dumps({"ok": True, "path": self.path}).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(response)))
        self.end_headers()
        try:
            self.wfile.write(response)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass


class RouterProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.openai = RecordingServer()
        self.anthropic = RecordingServer()
        self.openrouter = RecordingServer()
        self.openrouter.mode = "or_json"
        self.threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (self.openai, self.anthropic, self.openrouter)
        ]
        for thread in self.threads:
            thread.start()
        config = router.RouterConfig(
            {
                "gpt-test": "openai",
                "claude-test": "anthropic",
                "grok-test": "grok",
                "vendor/model-test": "openrouter",
                "qwen/qwen3.6-27b": "openrouter",
            },
            f"http://127.0.0.1:{self.openai.server_address[1]}",
            f"http://127.0.0.1:{self.anthropic.server_address[1]}",
            production=False,
            openrouter={
                "vendor/model-test": router.OpenRouterRoute(
                    endpoint_provider="deepinfra/fp4",
                    provider_name="DeepInfra",
                    provider_slug="deepinfra",
                    quantization="fp4",
                    canonical_slug="vendor/model-test-20260810",
                ),
                "qwen/qwen3.6-27b": router.OpenRouterRoute(
                    endpoint_provider="chutes/fp8",
                    provider_name="Chutes",
                    provider_slug="chutes",
                    quantization="fp8",
                    canonical_slug="qwen/qwen3.6-27b-20260422",
                ),
            },
            openrouter_key=b"sk-or-v1-SENTINEL_ROUTER_KEY_123456",
            openrouter_url=f"http://127.0.0.1:{self.openrouter.server_address[1]}/api/v1",
        )
        self.gateway = router.RouterServer(("127.0.0.1", 0), config)
        self.gateway_thread = threading.Thread(
            target=self.gateway.serve_forever, daemon=True
        )
        self.gateway_thread.start()

    def tearDown(self) -> None:
        self.gateway.shutdown()
        self.gateway.server_close()
        for server in (self.openai, self.anthropic, self.openrouter):
            server.shutdown()
            server.server_close()

    def request(
        self,
        model: str,
        *,
        path: str = "/v1/messages?beta=true",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 5,
    ) -> tuple[int, bytes, float]:
        if body is None:
            body = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": "hello"}],
            }, separators=(",", ":")).encode("utf-8")
        request_headers = {
            "content-type": "application/json",
            "authorization": "Bearer synthetic-claude-oauth",
            "x-api-key": "synthetic-api-key",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "synthetic-oauth-capability,tools-beta",
            "x-claude-code-session-id": "session-test",
            "x-claude-code-agent-id": "agent-test",
        }
        if headers:
            request_headers.update(headers)
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_address[1], timeout=timeout
        )
        started = time.monotonic()
        connection.request("POST", path, body=body, headers=request_headers)
        response = connection.getresponse()
        first = response.read1(64)
        first_elapsed = time.monotonic() - started
        remaining = response.read()
        status = response.status
        response.close()
        connection.close()
        return status, first + remaining, first_elapsed

    def get_json(self, path: str) -> tuple[int, dict[str, object]]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_address[1], timeout=5
        )
        connection.request("GET", path)
        response = connection.getresponse()
        status = response.status
        payload = json.loads(response.read())
        response.close()
        connection.close()
        return status, payload

    def test_anthropic_route_preserves_body_auth_and_attribution(self) -> None:
        status, response, _elapsed = self.request("claude-test")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(response)["ok"])
        self.assertEqual(self.openai.requests, [])
        self.assertEqual(len(self.anthropic.requests), 1)
        recorded = self.anthropic.requests[0]
        headers = recorded["headers"]
        self.assertEqual(headers["authorization"], "Bearer synthetic-claude-oauth")
        self.assertEqual(headers["x-api-key"], "synthetic-api-key")
        self.assertEqual(
            headers["anthropic-beta"],
            "synthetic-oauth-capability,tools-beta",
        )
        self.assertEqual(headers["x-claude-code-agent-id"], "agent-test")
        self.assertEqual(recorded["path"], "/v1/messages?beta=true")
        self.assertEqual(
            json.loads(recorded["body"])["messages"][0]["content"], "hello"
        )

    def test_openai_route_strips_claude_credentials_and_oauth_capability(self) -> None:
        status, response, _elapsed = self.request("gpt-test")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(response)["ok"])
        self.assertEqual(self.anthropic.requests, [])
        self.assertEqual(len(self.openai.requests), 1)
        recorded = self.openai.requests[0]
        headers = recorded["headers"]
        self.assertEqual(headers["authorization"], "Bearer unused")
        self.assertNotIn("x-api-key", headers)
        self.assertNotIn("anthropic-beta", headers)
        self.assertEqual(headers["x-claude-code-session-id"], "session-test")
        self.assertEqual(headers["x-claude-code-agent-id"], "agent-test")

    def test_grok_route_uses_the_loopback_proxy_without_claude_credentials(self) -> None:
        # Grok shares the loopback proxy with Codex, so it must inherit the same
        # credential stripping. A Claude token reaching this hop would cross the
        # boundary the whole project exists to hold.
        status, response, _elapsed = self.request("grok-test")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(response)["ok"])
        self.assertEqual(self.anthropic.requests, [])
        self.assertEqual(len(self.openai.requests), 1)
        headers = self.openai.requests[0]["headers"]
        self.assertEqual(headers["authorization"], "Bearer unused")
        self.assertNotIn("x-api-key", headers)
        self.assertNotIn("anthropic-beta", headers)
        self.assertNotIn("cookie", headers)
        self.assertEqual(headers["x-claude-code-session-id"], "session-test")

    def test_openrouter_route_pins_model_endpoint_and_credential_boundary(self) -> None:
        status, response, _elapsed = self.request("vendor/model-test")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response)["model"], "vendor/model-test")
        self.assertEqual(len(self.openrouter.requests), 1)
        recorded = self.openrouter.requests[0]
        self.assertEqual(recorded["path"], "/api/v1/messages")
        headers = recorded["headers"]
        self.assertEqual(
            headers["authorization"],
            "Bearer sk-or-v1-SENTINEL_ROUTER_KEY_123456",
        )
        self.assertEqual(headers["http-referer"], router.OPENROUTER_REFERER)
        self.assertEqual(headers["x-openrouter-title"], router.OPENROUTER_TITLE)
        self.assertEqual(headers["accept-encoding"], "identity")
        self.assertNotIn("x-api-key", headers)
        self.assertNotIn("anthropic-beta", headers)
        self.assertNotIn("x-claude-code-session-id", headers)
        payload = json.loads(recorded["body"])
        self.assertEqual(payload["model"], "vendor/model-test")
        self.assertNotIn("canonical_slug", payload)
        self.assertEqual(payload["provider"], {
            "only": ["deepinfra"],
            "quantizations": ["fp4"],
            "allow_fallbacks": False,
            "require_parameters": False,
        })

    def test_openrouter_preserves_native_optional_parameters(self) -> None:
        original = {
            "model": "vendor/model-test",
            "max_tokens": 1024,
            "system": [{
                "type": "text",
                "text": "synthetic system text",
                "cache_control": {"type": "ephemeral"},
            }],
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [{
                "name": "lookup",
                "description": "synthetic tool",
                "input_schema": {"type": "object", "properties": {}},
            }],
            "tool_choice": {"type": "auto"},
            "metadata": {"user_id": "synthetic-user"},
            "stream": True,
            "thinking": {"type": "enabled", "budget_tokens": 128},
        }
        body = json.dumps(original, separators=(",", ":")).encode("utf-8")
        status, _response, _elapsed = self.request(
            "vendor/model-test", body=body
        )
        self.assertEqual(status, 200)
        payload = json.loads(self.openrouter.requests[0]["body"])
        provider = payload.pop("provider")
        self.assertEqual(payload, original)
        self.assertEqual(provider, {
            "only": ["deepinfra"],
            "quantizations": ["fp4"],
            "allow_fallbacks": False,
            "require_parameters": False,
        })

    def test_openrouter_lifts_native_system_role_into_system_blocks(self) -> None:
        original_system = [{
            "type": "text",
            "text": "synthetic system text",
            "cache_control": {"type": "ephemeral"},
        }]
        user_message = {
            "role": "user",
            "content": [{"type": "text", "text": "hello"}],
        }
        body = json.dumps({
            "model": "qwen/qwen3.6-27b",
            "max_tokens": 32000,
            "system": original_system,
            "messages": [
                user_message,
                {"role": "system", "content": "custom model notice"},
            ],
            "tools": [{
                "name": "lookup",
                "description": "synthetic tool",
                "input_schema": {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            }],
            "metadata": {"user_id": "synthetic-user"},
            "stream": True,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "high"},
        }, separators=(",", ":")).encode("utf-8")
        status, response, _elapsed = self.request(
            "qwen/qwen3.6-27b", body=body
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response)["model"], "qwen/qwen3.6-27b")
        payload = json.loads(self.openrouter.requests[0]["body"])
        self.assertEqual(payload["messages"], [user_message])
        self.assertEqual(payload["system"], original_system + [{
            "type": "text", "text": "custom model notice",
        }])
        self.assertEqual(payload["thinking"], {"type": "adaptive"})
        self.assertEqual(payload["output_config"], {"effort": "high"})
        self.assertEqual(payload["provider"], {
            "only": ["chutes"],
            "quantizations": ["fp8"],
            "allow_fallbacks": False,
            "require_parameters": False,
        })

    def test_openrouter_lifts_text_blocks_after_string_system_content(self) -> None:
        lifted = {
            "type": "text",
            "text": "custom model notice",
            "cache_control": {"type": "ephemeral"},
        }
        body = json.dumps({
            "model": "qwen/qwen3.6-27b",
            "system": "base system text",
            "messages": [
                {"role": "system", "content": "first custom notice"},
                {"role": "user", "content": "hello"},
                {"role": "system", "content": [lifted]},
            ],
        }, separators=(",", ":")).encode("utf-8")
        status, _response, _elapsed = self.request(
            "qwen/qwen3.6-27b", body=body
        )
        self.assertEqual(status, 200)
        payload = json.loads(self.openrouter.requests[0]["body"])
        self.assertEqual(payload["messages"], [
            {"role": "user", "content": "hello"},
        ])
        self.assertEqual(payload["system"], [
            {"type": "text", "text": "base system text"},
            {"type": "text", "text": "first custom notice"},
            lifted,
        ])

    def test_openrouter_rejects_unsafe_system_role_normalization(self) -> None:
        cases = (
            {
                "model": "qwen/qwen3.6-27b",
                "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "system", "content": []},
                ],
            },
            {
                "model": "qwen/qwen3.6-27b",
                "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "system", "content": {"text": "notice"}},
                ],
            },
            {
                "model": "qwen/qwen3.6-27b",
                "messages": [
                    {"role": "user", "content": "hello"},
                    {
                        "role": "system",
                        "content": "notice",
                        "unknown": True,
                    },
                ],
            },
            {
                "model": "qwen/qwen3.6-27b",
                "messages": [{"role": "system", "content": "notice"}],
            },
            {
                "model": "qwen/qwen3.6-27b",
                "system": {"text": "invalid"},
                "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "system", "content": "notice"},
                ],
            },
        )
        for payload in cases:
            with self.subTest(payload=payload):
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                status, response, _elapsed = self.request(
                    "qwen/qwen3.6-27b", body=body
                )
                self.assertEqual(status, 400)
                self.assertEqual(
                    json.loads(response)["error"]["type"],
                    "invalid_request_error",
                )
        self.assertEqual(self.openrouter.requests, [])

    def test_openrouter_count_tokens_is_explicitly_unsupported(self) -> None:
        status, response, _elapsed = self.request(
            "vendor/model-test", path="/v1/messages/count_tokens"
        )
        self.assertEqual(status, 400)
        self.assertIn("only the Messages operation", response.decode("utf-8"))
        self.assertEqual(self.openrouter.requests, [])

    def test_openrouter_rejects_caller_routing_and_duplicate_keys(self) -> None:
        for field, value in (
            ("canonical_slug", "vendor/model-canonical"),
            ("provider", {"only": ["other"]}),
            ("models", ["vendor/fallback"]),
            ("plugins", [{"id": "router"}]),
            ("transforms", ["middle-out"]),
        ):
            with self.subTest(field=field):
                body = json.dumps({
                    "model": "vendor/model-test", field: value,
                }).encode("utf-8")
                status, _response, _elapsed = self.request(
                    "vendor/model-test", body=body
                )
                self.assertEqual(status, 400)
        duplicate = b'{"model":"vendor/model-test","model":"vendor/other"}'
        status, _response, _elapsed = self.request(
            "vendor/model-test", body=duplicate
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.openrouter.requests, [])

    def test_openrouter_accepts_only_routable_or_canonical_json_model(self) -> None:
        self.openrouter.mode = "or_canonical_json"
        status, response, _elapsed = self.request("vendor/model-test")
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(response)["model"], "vendor/model-test-20260810"
        )
        self.openrouter.mode = "or_mismatch_json"
        status, response, _elapsed = self.request("vendor/model-test")
        # A malformed or mismatched upstream response fails the same way every
        # time, so the router reports it as non-retryable instead of letting
        # Claude Code resend the whole conversation ten times.
        self.assertEqual(status, 400)
        self.assertNotIn("different-model", response.decode("utf-8"))
        self.openrouter.mode = "or_malformed"
        status, response, _elapsed = self.request("vendor/model-test")
        self.assertEqual(status, 400)
        self.assertEqual(
            json.loads(response)["error"]["type"], "invalid_request_error"
        )
        for mode in ("or_list_model_json", "or_object_model_json"):
            with self.subTest(mode=mode):
                self.openrouter.mode = mode
                status, response, _elapsed = self.request("vendor/model-test")
                self.assertEqual(status, 400)
                self.assertEqual(
                    json.loads(response)["error"]["type"], "invalid_request_error"
                )
                self.assertNotIn("vendor/model-test", response.decode("utf-8"))

    def test_openrouter_accepts_only_routable_or_canonical_sse_model(self) -> None:
        self.openrouter.mode = "or_stream"
        status, response, _elapsed = self.request("vendor/model-test")
        self.assertEqual(status, 200)
        self.assertIn(b'"model":"vendor/model-test"', response)
        self.openrouter.mode = "or_canonical_stream"
        status, response, _elapsed = self.request("vendor/model-test")
        self.assertEqual(status, 200)
        self.assertIn(b'"model":"vendor/model-test-20260810"', response)
        self.openrouter.mode = "or_mismatch_stream"
        status, response, _elapsed = self.request("vendor/model-test")
        self.assertEqual(status, 400)
        self.assertNotIn("different-model", response.decode("utf-8"))

    def test_openrouter_rejects_compressed_success_before_forwarding(self) -> None:
        self.openrouter.mode = "gzip_usage_json"
        status, response, _elapsed = self.request("vendor/model-test")
        self.assertEqual(status, 400)
        self.assertEqual(
            json.loads(response)["error"]["type"], "invalid_request_error"
        )

    def test_openrouter_error_body_is_not_reflected(self) -> None:
        self.openrouter.mode = "error"
        status, response, _elapsed = self.request("vendor/model-test")
        self.assertEqual(status, 429)
        self.assertNotIn("rate_limit_error", response.decode("utf-8"))
        self.assertIn("rejected the request", response.decode("utf-8"))

    def test_grok_route_is_recorded_with_its_own_provider_label(self) -> None:
        # Grok and Codex share an upstream, so the diagnostics label is the only
        # way a parity check can tell the two apart.
        status, _response, _elapsed = self.request("grok-test")
        self.assertEqual(status, 200)
        _diagnostics_status, diagnostics = self.get_json("/diagnostics")
        event = diagnostics["events"][-1]
        self.assertEqual(event["provider"], "grok")
        self.assertEqual(event["model"], "grok-test")

    def test_count_tokens_uses_the_model_route(self) -> None:
        status, _response, _elapsed = self.request(
            "claude-test", path="/v1/messages/count_tokens"
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.anthropic.requests[0]["path"], "/v1/messages/count_tokens")

    def test_unknown_model_fails_without_upstream_request(self) -> None:
        status, response, _elapsed = self.request("unknown-model")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(response)["error"]["type"], "invalid_request_error")
        self.assertEqual(self.openai.requests, [])
        self.assertEqual(self.anthropic.requests, [])

    def test_upstream_redirect_is_not_followed(self) -> None:
        self.anthropic.mode = "redirect"
        status, response, _elapsed = self.request("claude-test")
        # Terminal, so it is reported non-retryable and names its own reason.
        # The reason is a fixed string this repository owns; nothing from the
        # upstream response is echoed back.
        self.assertEqual(status, 400)
        self.assertEqual(
            json.loads(response)["error"]["type"], "invalid_request_error"
        )
        self.assertEqual(
            json.loads(response)["error"]["message"],
            "Upstream redirects are not allowed",
        )
        self.assertNotIn("invalid.example", response.decode("utf-8"))
        self.assertEqual(len(self.anthropic.requests), 1)

    def test_sse_is_streamed_before_the_upstream_finishes(self) -> None:
        self.anthropic.mode = "stream"
        status, response, first_elapsed = self.request("claude-test")
        self.assertEqual(status, 200)
        self.assertLess(first_elapsed, 0.2)
        self.assertIn(b"message_start", response)
        self.assertIn(b"message_stop", response)

    def test_stream_timeout_is_separate_from_connection_timeout(self) -> None:
        self.anthropic.mode = "stream"
        self.anthropic.delay = 0.2
        with (
            mock.patch.object(router, "CONNECT_TIMEOUT_SECONDS", 0.05),
            mock.patch.object(router, "RESPONSE_HEADER_TIMEOUT_SECONDS", 0.05),
            mock.patch.object(router, "STREAM_TIMEOUT_SECONDS", 1.0),
        ):
            status, response, _elapsed = self.request("claude-test", timeout=2)
        self.assertEqual(status, 200)
        self.assertIn(b"message_start", response)
        self.assertIn(b"message_stop", response)
        self.assertGreater(router.RESPONSE_HEADER_TIMEOUT_SECONDS, 120)
        self.assertGreater(router.STREAM_TIMEOUT_SECONDS, 120)

    def test_stream_timeout_closes_only_the_incomplete_response(self) -> None:
        self.anthropic.mode = "stream"
        self.anthropic.delay = 0.25
        with (
            mock.patch.object(router, "CONNECT_TIMEOUT_SECONDS", 0.05),
            mock.patch.object(router, "RESPONSE_HEADER_TIMEOUT_SECONDS", 0.05),
            mock.patch.object(router, "STREAM_TIMEOUT_SECONDS", 0.05),
        ):
            status, response, _elapsed = self.request("claude-test", timeout=2)
        self.assertEqual(status, 200)
        self.assertIn(b"message_start", response)
        self.assertNotIn(b"message_stop", response)
        diagnostics_status, diagnostics = self.get_json("/diagnostics")
        self.assertEqual(diagnostics_status, 200)
        event = diagnostics["events"][-1]
        self.assertEqual(event["outcome"], "upstream_timeout")

    def test_upstream_status_and_error_body_pass_through(self) -> None:
        self.anthropic.mode = "error"
        status, response, _elapsed = self.request("claude-test")
        self.assertEqual(status, 429)
        self.assertEqual(json.loads(response)["error"]["type"], "rate_limit_error")

    # A timeout may succeed on a retry, so it keeps the retryable 502. Only a
    # deterministic failure is downgraded to a non-retryable status.
    def test_upstream_header_timeout_becomes_sanitized_502(self) -> None:
        self.anthropic.mode = "headers_delay"
        self.anthropic.delay = 0.2
        with mock.patch.object(router, "RESPONSE_HEADER_TIMEOUT_SECONDS", 0.05):
            status, response, _elapsed = self.request("claude-test", timeout=2)
        self.assertEqual(status, 502)
        payload = json.loads(response)
        self.assertEqual(payload["error"]["type"], "api_error")
        self.assertNotIn("timed out", payload["error"]["message"].lower())

    def test_malformed_and_oversized_requests_never_reach_upstream(self) -> None:
        status, response, _elapsed = self.request("claude-test", body=b"{")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(response)["error"]["type"], "invalid_request_error")
        with mock.patch.object(router, "MAX_REQUEST_BYTES", 8):
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.gateway.server_address[1], timeout=2
            )
            connection.putrequest("POST", "/v1/messages")
            connection.putheader("content-type", "application/json")
            connection.putheader("content-length", "23")
            connection.endheaders()
            oversized = connection.getresponse()
            status = oversized.status
            response = oversized.read()
            oversized.close()
            connection.close()
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(response)["error"]["type"], "invalid_request_error")
        self.assertEqual(self.openai.requests, [])
        self.assertEqual(self.anthropic.requests, [])

    def test_partial_upstream_response_does_not_break_the_router(self) -> None:
        self.anthropic.mode = "partial"
        body = b'{"model":"claude-test"}'
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_address[1], timeout=2
        )
        connection.request(
            "POST",
            "/v1/messages",
            body=body,
            headers={"content-type": "application/json"},
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        with self.assertRaises(http.client.IncompleteRead) as captured:
            response.read()
        self.assertIn(b'"partial":true', captured.exception.partial)
        response.close()
        connection.close()
        self.anthropic.mode = "json"
        status, _response, _elapsed = self.request("claude-test")
        self.assertEqual(status, 200)

    def test_client_disconnect_does_not_break_the_router(self) -> None:
        self.anthropic.mode = "stream"
        self.anthropic.delay = 0.2
        body = b'{"model":"claude-test"}'
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_address[1], timeout=2
        )
        connection.request(
            "POST",
            "/v1/messages",
            body=body,
            headers={"content-type": "application/json"},
        )
        response = connection.getresponse()
        self.assertIn(b"message_start", response.read1(64))
        response.close()
        connection.close()
        time.sleep(0.3)
        self.anthropic.mode = "json"
        status, _response, _elapsed = self.request("claude-test")
        self.assertEqual(status, 200)

    def test_concurrent_requests_are_served_in_parallel(self) -> None:
        self.openai.mode = "delay"
        self.openai.delay = 0.1
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _index: self.request("gpt-test"), range(6)))
        self.assertTrue(all(status == 200 for status, _body, _time in results))
        self.assertEqual(len(self.openai.requests), 6)
        self.assertGreater(self.openai.max_active, 1)

    def test_usage_is_observed_from_a_streaming_response(self) -> None:
        self.openai.mode = "usage_stream"
        status, payload, _elapsed = self.request("gpt-test")
        self.assertEqual(status, 200)
        self.assertIn(b'"input_tokens":1200', payload)
        self.assertIn(b'"message_stop"', payload)
        _diagnostics_status, diagnostics = self.get_json("/diagnostics")
        event = diagnostics["events"][-1]
        self.assertEqual(event["provider"], "openai")
        self.assertEqual(
            event["usage"],
            {
                "input_tokens": 1200,
                "output_tokens": 350,
                "cache_read_input_tokens": 64,
            },
        )
        summary = diagnostics["summary"][-1]
        self.assertEqual(summary["requests"], 1)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(summary["usage_events"], 1)
        self.assertEqual(summary["input_tokens"], 1200)
        self.assertEqual(summary["cache_read_input_tokens"], 64)
        self.assertEqual(summary["output_tokens"], 350)

    def test_usage_is_observed_from_a_json_response(self) -> None:
        self.anthropic.mode = "usage_json"
        status, _payload, _elapsed = self.request("claude-test")
        self.assertEqual(status, 200)
        _diagnostics_status, diagnostics = self.get_json("/diagnostics")
        event = diagnostics["events"][-1]
        self.assertEqual(event["usage"], {"input_tokens": 11, "output_tokens": 22})

    def test_usage_is_observed_from_a_compressed_streaming_response(self) -> None:
        self.anthropic.mode = "gzip_usage_stream"
        status, payload, _elapsed = self.request("claude-test")
        self.assertEqual(status, 200)
        # The client still receives the untouched compressed bytes. Only the
        # observer decodes a copy.
        self.assertNotIn(b'"message_stop"', payload)
        self.assertIn(b'"message_stop"', zlib.decompress(payload, 16 + zlib.MAX_WBITS))
        _diagnostics_status, diagnostics = self.get_json("/diagnostics")
        event = diagnostics["events"][-1]
        self.assertEqual(event["provider"], "anthropic")
        self.assertEqual(event["usage"], {"input_tokens": 900, "output_tokens": 250})

    def test_usage_is_observed_from_a_compressed_json_response(self) -> None:
        self.anthropic.mode = "gzip_usage_json"
        status, _payload, _elapsed = self.request("claude-test")
        self.assertEqual(status, 200)
        _diagnostics_status, diagnostics = self.get_json("/diagnostics")
        event = diagnostics["events"][-1]
        self.assertEqual(event["usage"], {"input_tokens": 31, "output_tokens": 42})

    def test_unreadable_content_encoding_records_no_usage(self) -> None:
        self.anthropic.mode = "unreadable_encoding_stream"
        status, payload, _elapsed = self.request("claude-test")
        self.assertEqual(status, 200)
        # Forwarding must be unaffected even when usage cannot be read.
        self.assertEqual(payload, b"encoding the standard library cannot read")
        _diagnostics_status, diagnostics = self.get_json("/diagnostics")
        event = diagnostics["events"][-1]
        self.assertEqual(event["outcome"], "completed")
        self.assertNotIn("usage", event)

    def test_usage_is_observed_for_the_grok_route(self) -> None:
        self.openai.mode = "usage_stream"
        status, _payload, _elapsed = self.request("grok-test")
        self.assertEqual(status, 200)
        _diagnostics_status, diagnostics = self.get_json("/diagnostics")
        event = diagnostics["events"][-1]
        self.assertEqual(event["provider"], "grok")
        self.assertEqual(
            event["usage"],
            {
                "input_tokens": 1200,
                "output_tokens": 350,
                "cache_read_input_tokens": 64,
            },
        )

    def test_missing_upstream_usage_is_recorded_as_absent(self) -> None:
        self.openai.mode = "no_usage_stream"
        status, payload, _elapsed = self.request("gpt-test")
        self.assertEqual(status, 200)
        self.assertIn(b'"message_stop"', payload)
        _diagnostics_status, diagnostics = self.get_json("/diagnostics")
        event = diagnostics["events"][-1]
        self.assertEqual(event["outcome"], "completed")
        self.assertNotIn("usage", event)

    def test_cumulative_usage_outlives_the_bounded_event_window(self) -> None:
        def record(_index: int) -> None:
            self.gateway.record_diagnostic({
                "provider": "openai",
                "model": "gpt-test",
                "status": 200,
                "outcome": "completed",
                "usage": {
                    "input_tokens": 1,
                    "cache_creation_input_tokens": 2,
                    "cache_read_input_tokens": 3,
                    "output_tokens": 4,
                },
            })

        request_count = router.MAX_DIAGNOSTIC_EVENTS + 44
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(record, range(request_count)))
        report = self.gateway.diagnostic_report()
        self.assertEqual(len(report["events"]), router.MAX_DIAGNOSTIC_EVENTS)
        summary = report["summary"][0]
        self.assertEqual(summary["requests"], request_count)
        self.assertEqual(summary["completed"], request_count)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(summary["usage_events"], request_count)
        self.assertEqual(summary["input_tokens"], request_count)
        self.assertEqual(summary["cache_creation_input_tokens"], request_count * 2)
        self.assertEqual(summary["cache_read_input_tokens"], request_count * 3)
        self.assertEqual(summary["output_tokens"], request_count * 4)

    def test_usage_observer_ignores_content_and_malformed_events(self) -> None:
        observer = router.UsageObserver()
        observer.streaming = True
        observer.observe(b'event: ping\ndata: {"type":"ping"}\n\n')
        observer.observe(b'data: not-json-at-all "usage"\n')
        observer.observe(b'data: {"usage":{"input_tokens":"12"}}\n')
        observer.observe(b'data: {"usage":{"output_tokens":true}}\n')
        observer.observe(b'data: {"usage":{"input_tokens":-5}}\n')
        self.assertIsNone(observer.snapshot())
        observer.observe(b'data: {"usage":{"input_tokens":7}}\n')
        observer.finish()
        self.assertEqual(observer.snapshot(), {"input_tokens": 7})

    def test_usage_observer_recovers_after_an_oversized_line(self) -> None:
        observer = router.UsageObserver()
        observer.streaming = True
        observer.observe(
            b'data: {"usage":{"input_tokens":'
            + b"9" * (router.MAX_USAGE_LINE_BYTES + 16)
        )
        observer.observe(b"}}\n")
        observer.observe(b'data: {"usage":{"output_tokens":5}}\n')
        observer.finish()
        self.assertEqual(observer.snapshot(), {"output_tokens": 5})

    def test_diagnostics_contain_only_sanitized_request_metadata(self) -> None:
        status, _response, _elapsed = self.request("claude-test")
        self.assertEqual(status, 200)
        diagnostics_status, diagnostics = self.get_json("/diagnostics")
        self.assertEqual(diagnostics_status, 200)
        self.assertEqual(diagnostics["instance_id"], self.gateway.instance_id)
        event = diagnostics["events"][-1]
        self.assertEqual(event["provider"], "anthropic")
        self.assertEqual(event["model"], "claude-test")
        self.assertEqual(event["status"], 200)
        self.assertGreater(event["request_bytes"], 0)
        self.assertGreater(event["response_bytes"], 0)
        serialized = json.dumps(diagnostics)
        for private_value in (
            "synthetic-claude-oauth",
            "synthetic-api-key",
            "synthetic-oauth-capability",
            "hello",
            "messages",
            "authorization",
        ):
            self.assertNotIn(private_value, serialized)

    def test_health_and_models_are_local(self) -> None:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_address[1], timeout=5
        )
        connection.request("GET", "/healthz")
        health = connection.getresponse()
        self.assertEqual(health.status, 200)
        self.assertTrue(json.loads(health.read())["ok"])
        connection.close()

        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_address[1], timeout=5
        )
        connection.request("GET", "/v1/models")
        models = connection.getresponse()
        self.assertEqual(models.status, 200)
        self.assertEqual(
            {entry["id"] for entry in json.loads(models.read())["data"]},
            {
                "gpt-test",
                "claude-test",
                "grok-test",
                "vendor/model-test",
                "qwen/qwen3.6-27b",
            },
        )
        connection.close()

    def test_cli_router_stops_after_owning_process_exits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = directory
            # The owner must outlive the router's own startup budget, and the
            # wait below must exceed it too, so a slow runner reports a real
            # failure instead of a test-imposed timeout. The owner is always
            # terminated in the finally block, so the long sleep costs nothing.
            snapshot, digest = write_test_snapshot(directory)
            owner = subprocess.Popen([
                sys.executable,
                "-c",
                "import time; time.sleep(120)",
            ])
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(ROUTER),
                        "start",
                        "--parent-pid",
                        str(owner.pid),
                        "--snapshot",
                        str(snapshot),
                        "--snapshot-sha256",
                        digest,
                        "--openai-url",
                        "http://127.0.0.1:49152",
                    ],
                    env=environment,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                address = completed.stdout.strip().removeprefix("http://")
                host, port = address.rsplit(":", 1)
                connection = http.client.HTTPConnection(host, int(port), timeout=3)
                connection.request("GET", "/healthz")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                connection.close()
            finally:
                owner.terminate()
                owner.wait(timeout=5)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                connection = None
                try:
                    connection = http.client.HTTPConnection(host, int(port), timeout=0.2)
                    connection.request("GET", "/healthz")
                    response = connection.getresponse()
                    response.read()
                except OSError:
                    break
                finally:
                    if connection is not None:
                        connection.close()
                time.sleep(0.1)
            else:
                self.fail("router remained available after its owning process exited")

    def test_router_start_rejects_a_missing_owner_without_spawning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = directory
            snapshot, digest = write_test_snapshot(directory)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROUTER),
                    "start",
                    "--parent-pid",
                    "2147483647",
                    "--snapshot",
                    str(snapshot),
                    "--snapshot-sha256",
                    digest,
                ],
                env=environment,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=5,
                check=False,
            )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("owner process is not running", completed.stderr)
        self.assertEqual(completed.stdout, "")

    def test_openrouter_only_startup_and_config_need_no_subscription_upstream(self) -> None:
        routes = {"vendor/model-test": "openrouter"}
        with tempfile.TemporaryDirectory() as directory:
            snapshot, digest = write_test_snapshot(
                directory,
                routes=routes,
                profile="openrouter-pure",
                openrouter=TEST_OPENROUTER_METADATA,
            )
            loaded = router.load_router_snapshot(str(snapshot), digest)
            with mock.patch.object(
                router,
                "load_openrouter_key",
                return_value=b"sk-or-v1-SENTINEL_STARTUP_KEY",
            ):
                config = router.router_config_from_snapshot(
                    loaded, None, "https://api.anthropic.com"
                )
                self.assertIsNone(config.openai)
                self.assertEqual(config.routes, routes)

                process = mock.Mock()
                process.pid = 4242
                process.poll.return_value = None

                def launch(command: list[str], **_kwargs: object) -> mock.Mock:
                    ready = Path(command[command.index("--ready-file") + 1])
                    ready.write_text(json.dumps({
                        "pid": process.pid,
                        "bundle_version": router.MANAGED_BUNDLE_VERSION,
                        "url": "http://127.0.0.1:39123",
                    }), encoding="ascii")
                    return process

                with (
                    mock.patch.object(router, "process_alive", return_value=True),
                    mock.patch.object(
                        router.subprocess, "Popen", side_effect=launch
                    ) as popen,
                    mock.patch.object(
                        router, "safe_runtime_root", return_value=Path(directory)
                    ),
                ):
                    args = router.parser().parse_args([
                        "start",
                        "--parent-pid",
                        "1234",
                        "--snapshot",
                        str(snapshot),
                        "--snapshot-sha256",
                        digest,
                    ])
                    self.assertEqual(router.start_router(args), 0)

                command = popen.call_args.args[0]
                self.assertNotIn("--openai-url", command)

    def test_subscription_routes_require_an_openai_upstream_at_startup_and_config(self) -> None:
        for provider in ("openai", "grok"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                model = "gpt-test" if provider == "openai" else "grok-test"
                snapshot, digest = write_test_snapshot(
                    directory, routes={model: provider}
                )
                loaded = router.load_router_snapshot(str(snapshot), digest)
                with self.assertRaisesRegex(
                    router.RouterError, "subscription proxy upstream is required"
                ):
                    router.router_config_from_snapshot(
                        loaded, None, "https://api.anthropic.com"
                    )

                args = router.parser().parse_args([
                    "start",
                    "--parent-pid",
                    "1234",
                    "--snapshot",
                    str(snapshot),
                    "--snapshot-sha256",
                    digest,
                ])
                with (
                    mock.patch.object(router, "process_alive", return_value=True),
                    mock.patch.object(router.subprocess, "Popen") as popen,
                    self.assertRaisesRegex(
                        router.RouterError,
                        "subscription proxy upstream is required",
                    ),
                ):
                    router.start_router(args)
                popen.assert_not_called()

    def test_hybrid_snapshot_keeps_subscription_and_openrouter_upstreams(self) -> None:
        routes = {
            "gpt-test": "openai",
            "grok-test": "grok",
            "vendor/model-test": "openrouter",
        }
        agents = {
            "airlock-gpt": {
                "model": "gpt-test", "provider": "openai", "extra_usage": False,
            },
            "airlock-grok": {
                "model": "grok-test", "provider": "grok", "extra_usage": False,
            },
            "airlock-openrouter": {
                "model": "vendor/model-test",
                "provider": "openrouter",
                "extra_usage": True,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            snapshot, digest = write_test_snapshot(
                directory,
                routes=routes,
                profile="hybrid",
                openrouter=TEST_OPENROUTER_METADATA,
                agents=agents,
            )
            loaded = router.load_router_snapshot(str(snapshot), digest)
            with mock.patch.object(
                router,
                "load_openrouter_key",
                return_value=b"sk-or-v1-SENTINEL_HYBRID_KEY",
            ):
                config = router.router_config_from_snapshot(
                    loaded,
                    "http://127.0.0.1:49152",
                    "https://api.anthropic.com",
                )

        self.assertEqual(config.openai, ("http", "127.0.0.1", 49152, ""))
        self.assertEqual(config.anthropic, ("https", "api.anthropic.com", 443, ""))
        self.assertEqual(config.openrouter, ("https", "openrouter.ai", 443, "/api/v1"))
        self.assertEqual(config.openrouter_key, b"sk-or-v1-SENTINEL_HYBRID_KEY")

        with tempfile.TemporaryDirectory() as directory:
            snapshot, digest = write_test_snapshot(directory)
            loaded = router.load_router_snapshot(str(snapshot), digest)
            self.assertEqual(loaded.routes["gpt-test"], "openai")
            snapshot.write_bytes(snapshot.read_bytes() + b"\n")
            # Canonical whitespace is intentionally digest-equivalent.
            router.load_router_snapshot(str(snapshot), digest)
            value = loaded.to_dict()
            value["routes"] = {"gpt-other": "openai"}
            value["root_model"] = "gpt-other"
            value["agents"]["airlock-test"]["model"] = "gpt-other"
            snapshot.write_bytes(POLICY.canonical_json_bytes(value))
            with self.assertRaisesRegex(router.RouterError, "snapshot is invalid"):
                router.load_router_snapshot(str(snapshot), digest)

    def test_openrouter_production_upstream_is_exactly_pinned(self) -> None:
        self.assertEqual(
            router.parse_upstream(
                router.OPENROUTER_URL, "openrouter", production=True
            ),
            ("https", "openrouter.ai", 443, "/api/v1"),
        )
        for invalid in (
            "http://openrouter.ai/api/v1",
            "https://example.com/api/v1",
            "https://openrouter.ai:444/api/v1",
            "https://openrouter.ai/api/v2",
            "https://user@openrouter.ai/api/v1",
            "https://openrouter.ai/api/v1?x=1",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(router.RouterError):
                    router.parse_upstream(invalid, "openrouter", production=True)

    def test_production_upstreams_fail_closed(self) -> None:
        self.assertTrue(router.valid_router_url("http://127.0.0.1:49152"))
        for invalid in (
            "https://127.0.0.1:49152",
            "http://localhost:49152",
            "http://127.0.0.1:49152/path",
            "http://127.0.0.1:49152?query=yes",
            "http://user@127.0.0.1:49152",
        ):
            self.assertFalse(router.valid_router_url(invalid))
        with self.assertRaises(router.RouterError):
            router.RouterConfig(
                {"gpt-test": "openai"},
                "http://example.com",
                "https://api.anthropic.com",
            )
        with self.assertRaises(router.RouterError):
            router.RouterConfig(
                {"claude-test": "anthropic"},
                "http://127.0.0.1:18765",
                "https://example.com",
            )

    def test_binding_the_router_never_waits_on_reverse_dns(self) -> None:
        # http.server resolves the bound address with socket.getfqdn, which
        # is a reverse DNS lookup that can stall for tens of seconds on a
        # machine with an unreachable resolver. A loopback router must not
        # depend on name resolution to start.
        def unreachable_resolver(*args: object, **kwargs: object) -> str:
            raise AssertionError("router startup performed a DNS lookup")

        config = router.RouterConfig(
            {"gpt-test": "openai"},
            "http://127.0.0.1:18765",
            "https://api.anthropic.com",
        )
        with mock.patch("socket.getfqdn", unreachable_resolver):
            server = router.RouterServer(("127.0.0.1", 0), config)
        try:
            self.assertEqual(server.server_name, "127.0.0.1")
            self.assertEqual(server.server_port, server.server_address[1])
        finally:
            server.server_close()

    def test_ready_marker_is_published_atomically(self) -> None:
        # The parent polls for this file and reads it as soon as it appears,
        # so it must never be visible while it is still empty, and the
        # writing handle must be closed before the name exists.
        config = router.RouterConfig(
            {"gpt-test": "openai"},
            "http://127.0.0.1:18765",
            "https://api.anthropic.com",
        )
        server = router.RouterServer(("127.0.0.1", 0), config)
        try:
            with tempfile.TemporaryDirectory() as directory:
                ready = Path(directory) / "ready-test.json"
                observed: list[int] = []
                real_replace = os.replace

                def watch_replace(source: object, target: object) -> None:
                    observed.append(ready.exists())
                    real_replace(source, target)

                with mock.patch("os.replace", watch_replace):
                    router.write_ready(ready, server)

                self.assertEqual(observed, [False])
                payload = json.loads(ready.read_text(encoding="ascii"))
                self.assertEqual(payload["pid"], os.getpid())
                self.assertEqual(
                    payload["bundle_version"], router.MANAGED_BUNDLE_VERSION
                )
                self.assertTrue(router.valid_router_url(payload["url"]))
                # The staging file must not survive a successful publish, and
                # the marker must be deletable straight away on every
                # platform, including Windows.
                self.assertEqual(
                    sorted(entry.name for entry in Path(directory).iterdir()),
                    ["ready-test.json"],
                )
                ready.unlink()
        finally:
            server.server_close()

    def test_a_failed_ready_write_leaves_nothing_behind(self) -> None:
        config = router.RouterConfig(
            {"gpt-test": "openai"},
            "http://127.0.0.1:18765",
            "https://api.anthropic.com",
        )
        server = router.RouterServer(("127.0.0.1", 0), config)
        try:
            with tempfile.TemporaryDirectory() as directory:
                ready = Path(directory) / "ready-test.json"

                def failing_replace(source: object, target: object) -> None:
                    raise OSError("publish failed")

                with mock.patch("os.replace", failing_replace):
                    with self.assertRaises(OSError):
                        router.write_ready(ready, server)

                self.assertEqual(list(Path(directory).iterdir()), [])
        finally:
            server.server_close()


class OpenRouterStreamIdentityTests(unittest.TestCase):
    """Identity scanning over the leading SSE blocks of an OpenRouter stream."""

    MODEL = "stealth/ox-alpha"
    START = (
        "event: message_start\n"
        'data: {"type":"message_start","message":{"model":"stealth/ox-alpha"}}'
    )

    def prefix(self, *blocks: str) -> bytes:
        # The trailing partial block mirrors a real read that stops mid-stream.
        return ("\n\n".join(blocks) + "\n\nevent: partial\n").encode()

    def test_message_start_yields_the_model_identity(self) -> None:
        self.assertEqual(
            router.openrouter_sse_model(self.prefix(self.START), self.MODEL),
            self.MODEL,
        )

    def test_ping_before_message_start_is_skipped(self) -> None:
        stream = self.prefix('event: ping\ndata: {"type":"ping"}', self.START)
        self.assertEqual(router.openrouter_sse_model(stream, self.MODEL), self.MODEL)

    def test_processing_comment_before_message_start_is_skipped(self) -> None:
        stream = self.prefix(": OPENROUTER PROCESSING", self.START)
        self.assertEqual(router.openrouter_sse_model(stream, self.MODEL), self.MODEL)

    def test_upstream_error_event_reports_its_own_reason(self) -> None:
        stream = self.prefix(
            'event: error\ndata: {"type":"error","error":'
            '{"code":429,"message":"Rate limit exceeded"}}'
        )
        with self.assertRaises(router.UpstreamError) as caught:
            router.openrouter_sse_model(stream, self.MODEL)
        message = str(caught.exception)
        self.assertIn("upstream reported an error", message)
        self.assertIn("code 429", message)
        self.assertIn("Rate limit exceeded", message)
        self.assertIn(self.MODEL, message)
        self.assertFalse(caught.exception.retryable)

    def test_upstream_error_reason_is_bounded_and_printable(self) -> None:
        payload = json.dumps({
            "type": "error",
            "error": {"message": ("x" * 400) + "\x07 tail"},
        })
        stream = self.prefix("event: error\ndata: " + payload)
        with self.assertRaises(router.UpstreamError) as caught:
            router.openrouter_sse_model(stream, self.MODEL)
        message = str(caught.exception)
        self.assertNotIn("\x07", message)
        self.assertLess(len(message), 400)

    def test_unexpected_first_event_names_what_arrived(self) -> None:
        stream = self.prefix('event: weird\ndata: {"type":"weird"}')
        with self.assertRaises(router.UpstreamError) as caught:
            router.openrouter_sse_model(stream, self.MODEL)
        message = str(caught.exception)
        self.assertIn("did not start with message_start", message)
        self.assertIn("weird", message)
        self.assertIn(self.MODEL, message)

    def test_missing_model_identity_is_rejected(self) -> None:
        stream = self.prefix(
            'event: message_start\ndata: {"type":"message_start","message":{}}'
        )
        with self.assertRaises(router.UpstreamError):
            router.openrouter_sse_model(stream, self.MODEL)

    def test_incomplete_prefix_asks_for_more_bytes(self) -> None:
        self.assertIsNone(
            router.openrouter_sse_model(b"event: message_start\ndata: {", self.MODEL)
        )


if __name__ == "__main__":
    unittest.main()
