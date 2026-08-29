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
import re
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
    failover: dict[str, list[str]] | None = None,
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
        "failover": failover or {},
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
DIAGNOSTIC_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)


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
        self.rate_limited_models: set[str] = set()
        # Models answering 402: a spent Grok subscription balance or an
        # OpenRouter account out of credits, which must hand off like a 429.
        self.payment_required_models: set[str] = set()
        # model -> ("anthropic" | "proxy" | "plain", prompt_tokens, limit_tokens)
        self.overflow_models: dict[str, tuple[str, int, int]] = {}
        # Optional byte ceiling per model. A real provider rejects only an
        # oversized request, so a shrunk retry gets through; without this the
        # stub would refuse every attempt including the condensed one.
        self.overflow_max_bytes: dict[str, int] = {}
        self.compactor_models: set[str] = set()
        self.compactor_text = "Goal: continue the work."


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
        try:
            requested_model = json.loads(body).get("model")
        except ValueError:
            requested_model = None
        with self.recorder.lock:
            self.recorder.requests.append({
                "path": self.path,
                "headers": {
                    name.lower(): value for name, value in self.headers.items()
                },
                "body": body,
            })
            limited = requested_model in self.recorder.rate_limited_models
            unpaid = requested_model in self.recorder.payment_required_models
        if unpaid:
            payload = json.dumps({
                "type": "error",
                "error": {
                    "type": "permission_error",
                    "message": "usage balance exhausted",
                },
            }, separators=(",", ":")).encode("utf-8")
            self.send_response(402)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if limited:
            payload = json.dumps({
                "type": "error",
                "error": {
                    "type": "rate_limit_error",
                    "message": "slow down",
                },
            }, separators=(",", ":")).encode("utf-8")
            self.send_response(429)
            self.send_header("content-type", "application/json")
            self.send_header("retry-after", "30")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        overflow = self.recorder.overflow_models.get(requested_model)
        ceiling = self.recorder.overflow_max_bytes.get(requested_model)
        if overflow is not None and (ceiling is None or len(body) > ceiling):
            style, prompt, limit = overflow
            if style == "anthropic":
                payload = json.dumps({
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": (
                            f"prompt is too long: {prompt} tokens > "
                            f"{limit} maximum"
                        ),
                    },
                }, separators=(",", ":")).encode("utf-8")
                status = 400
            elif style == "proxy":
                payload = json.dumps({
                    "error": {
                        "code": "request_too_large",
                        "message": (
                            f"This model's maximum context length is {limit}"
                            f" tokens. However, you requested {prompt} tokens"
                            " in the messages."
                        ),
                    },
                }, separators=(",", ":")).encode("utf-8")
                status = 413
            else:
                payload = json.dumps({
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "max_tokens: Field required",
                    },
                }, separators=(",", ":")).encode("utf-8")
                status = 400
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.recorder.mode.startswith("or_"):
            if self.recorder.mode == "or_rejected":
                response = b'{"error":{"message":"PRIVATE_UPSTREAM_DETAIL"}}'
                self.send_response(403)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
                return
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
        if requested_model in self.recorder.compactor_models:
            # Identify a compaction call by its prompt markers, not by the
            # presence of a system field: the shrunk retry carries the
            # caller's own system prompt too.
            text = body.decode("utf-8", errors="replace")
            if "<segment>" in text or "<notes>" in text:
                response = json.dumps({
                    "id": "msg_compact",
                    "model": requested_model,
                    "content": [{
                        "type": "text",
                        "text": self.recorder.compactor_text,
                    }],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }, separators=(",", ":")).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
                return
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

    def test_openrouter_clamps_max_effort_to_high_and_records_diagnostic(self) -> None:
        original = {
            "model": "vendor/model-test",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
            "thinking": {"type": "adaptive", "display": "omitted"},
            "output_config": {"effort": "max"},
        }
        body = json.dumps(original, separators=(",", ":")).encode("utf-8")
        status, _response, _elapsed = self.request(
            "vendor/model-test", body=body
        )
        self.assertEqual(status, 200)
        payload = json.loads(self.openrouter.requests[0]["body"])
        self.assertEqual(payload["thinking"], original["thinking"])
        self.assertEqual(payload["output_config"], {"effort": "high"})
        _diagnostics_status, diagnostics = self.get_json("/diagnostics")
        clamped_events = [
            event for event in diagnostics["events"]
            if event.get("kind") == "openrouter_effort_clamped"
        ]
        self.assertEqual(len(clamped_events), 1)
        self.assertEqual(
            {key: value for key, value in clamped_events[0].items() if key != "timestamp"},
            {
                "kind": "openrouter_effort_clamped",
                "model": "vendor/model-test",
                "requested": "max",
                "forwarded": "high",
                "ceiling": "high",
            },
        )
        self.assertRegex(
            clamped_events[0]["timestamp"], DIAGNOSTIC_TIMESTAMP_PATTERN
        )

    def test_openrouter_strips_server_tools_and_records_diagnostic(self) -> None:
        original = {
            "model": "vendor/model-test",
            "max_tokens": 1024,
            "system": "synthetic system text",
            "messages": [
                {"role": "user", "content": "search for it"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "partial answer"},
                        {
                            "type": "server_tool_use",
                            "id": "srvtoolu_01",
                            "name": "web_search",
                            "input": {"query": "earlier query"},
                        },
                        {
                            "type": "web_search_tool_result",
                            "tool_use_id": "srvtoolu_01",
                            "content": [{"type": "text", "text": "old result"}],
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "client_01", "content": "kept"},
                        {"type": "text", "text": "and continue"},
                    ],
                },
            ],
            "tools": [
                {
                    "name": "lookup",
                    "description": "synthetic tool",
                    "input_schema": {"type": "object", "properties": {}},
                },
                {"type": "web_search_20250305", "name": "web_search"},
                {"type": "web_fetch_20250910", "name": "web_fetch"},
            ],
            "stream": False,
        }
        body = json.dumps(original, separators=(",", ":")).encode("utf-8")
        status, _response, _elapsed = self.request(
            "vendor/model-test", body=body
        )
        self.assertEqual(status, 200)
        payload = json.loads(self.openrouter.requests[0]["body"])
        payload.pop("provider")
        self.assertEqual(payload["tools"], [original["tools"][0]])
        assistant = payload["messages"][1]
        self.assertEqual(
            [block["type"] for block in assistant["content"]], ["text"]
        )
        # Client tool results survive; only the server-side variants go.
        user_blocks = payload["messages"][2]["content"]
        self.assertEqual(len(user_blocks), 2)
        _diagnostics_status, diagnostics = self.get_json("/diagnostics")
        stripped_events = [
            event for event in diagnostics["events"]
            if event.get("kind") == "openrouter_server_tools_stripped"
        ]
        self.assertEqual(len(stripped_events), 1)
        self.assertEqual(stripped_events[0]["removed_count"], 4)
        self.assertNotIn("removed", stripped_events[0])
        self.assertRegex(
            stripped_events[0]["timestamp"], DIAGNOSTIC_TIMESTAMP_PATTERN
        )
        serialized = json.dumps(diagnostics)
        for request_value in (
            "earlier query", "old result", "web_search", "web_fetch"
        ):
            self.assertNotIn(request_value, serialized)

    def test_openrouter_server_only_tools_are_dropped_entirely(self) -> None:
        original = {
            "model": "vendor/model-test",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [{"type": "code_execution_20250825", "name": "code_execution"}],
            "stream": False,
        }
        body = json.dumps(original, separators=(",", ":")).encode("utf-8")
        status, _response, _elapsed = self.request(
            "vendor/model-test", body=body
        )
        self.assertEqual(status, 200)
        payload = json.loads(self.openrouter.requests[0]["body"])
        payload.pop("provider")
        self.assertNotIn("tools", payload)
        self.assertNotIn("tool_choice", payload)

    def test_openrouter_keep_server_tools_switch_preserves_declarations(self) -> None:
        original = {
            "model": "vendor/model-test",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [
                {
                    "name": "lookup",
                    "description": "synthetic tool",
                    "input_schema": {"type": "object", "properties": {}},
                },
                {"type": "web_search_20250305", "name": "web_search"},
            ],
            "stream": False,
        }
        body = json.dumps(original, separators=(",", ":")).encode("utf-8")
        previous = os.environ.get(router.KEEP_SERVER_TOOLS_VARIABLE)
        os.environ[router.KEEP_SERVER_TOOLS_VARIABLE] = "1"
        try:
            status, response, _elapsed = self.request(
                "vendor/model-test", body=body
            )
        finally:
            if previous is None:
                os.environ.pop(router.KEEP_SERVER_TOOLS_VARIABLE, None)
            else:
                os.environ[router.KEEP_SERVER_TOOLS_VARIABLE] = previous
        self.assertEqual(status, 200)
        payload = json.loads(self.openrouter.requests[0]["body"])
        payload.pop("provider")
        self.assertEqual(payload["tools"], original["tools"])

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
        # Claude Code resend the whole conversation ten times. The bounded,
        # printable received identity remains available for diagnosis.
        self.assertEqual(status, 400)
        self.assertIn("different-model", response.decode("utf-8"))
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
                # Naming the expected route is intended; the malformed
                # upstream value itself is still never reflected.
                self.assertIn("no model identity", response.decode("utf-8"))

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
        # The bounded, printable received identity is named on purpose so a
        # masked or wrong upstream response is diagnosable from the session.
        self.assertIn("different-model", response.decode("utf-8"))

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
        # A 429 stays a 429 for Claude Code's backoff, carrying Airlock's
        # bounded wording instead of reflected upstream text. This asserts the
        # ending both exhaustion messages share, because which one applies
        # depends on whether the route has peers and is covered elsewhere.
        self.assertEqual(status, 429)
        self.assertIn(
            "retry shortly or switch models", response.decode("utf-8")
        )
        self.assertNotIn("rejected the request", response.decode("utf-8"))
        _diagnostics_status, diagnostics = self.get_json("/diagnostics")
        sanitized = [
            event for event in diagnostics["events"]
            if event.get("kind") == "sanitized_error_substituted"
        ]
        self.assertEqual(len(sanitized), 1)
        self.assertEqual(sanitized[0]["provider"], "openrouter")
        self.assertEqual(sanitized[0]["model"], "vendor/model-test")
        self.assertEqual(sanitized[0]["status"], 429)
        self.assertRegex(sanitized[0]["timestamp"], DIAGNOSTIC_TIMESTAMP_PATTERN)
        self.assertNotIn("slow down", json.dumps(diagnostics))

    def test_openrouter_non_rate_error_is_replaced_and_recorded(self) -> None:
        self.openrouter.mode = "or_rejected"
        status, response, _elapsed = self.request("vendor/model-test")
        self.assertEqual(status, 403)
        self.assertIn(b"rejected the request", response)
        self.assertNotIn(b"PRIVATE_UPSTREAM_DETAIL", response)
        _diagnostics_status, diagnostics = self.get_json("/diagnostics")
        sanitized = [
            event for event in diagnostics["events"]
            if event.get("kind") == "sanitized_error_substituted"
        ]
        self.assertEqual(len(sanitized), 1)
        self.assertEqual(sanitized[0]["provider"], "openrouter")
        self.assertEqual(sanitized[0]["model"], "vendor/model-test")
        self.assertEqual(sanitized[0]["status"], 403)
        self.assertRegex(sanitized[0]["timestamp"], DIAGNOSTIC_TIMESTAMP_PATTERN)
        self.assertNotIn("PRIVATE_UPSTREAM_DETAIL", json.dumps(diagnostics))

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
        # A 429 stays a 429 so Claude Code's own backoff still applies, but
        # the body carries Airlock's bounded wording rather than reflected
        # upstream text.
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
        # The bounded reason survives so a slow upstream stays diagnosable.
        self.assertIn("timed out", payload["error"]["message"].lower())

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

    def test_session_start_pins_root_model_without_counting_a_request(self) -> None:
        diagnostics_status, diagnostics = self.get_json("/diagnostics")
        self.assertEqual(diagnostics_status, 200)
        self.assertEqual(diagnostics["profile"], "hybrid")
        self.assertEqual(diagnostics["root_model"], "gpt-test")
        self.assertEqual(diagnostics["root_provider"], "openai")
        self.assertEqual(diagnostics["summary"], [])
        self.assertEqual(len(diagnostics["events"]), 1)
        pinned = diagnostics["events"][0]
        self.assertEqual(pinned["kind"], "session_model_pinned")
        self.assertEqual(pinned["profile"], "hybrid")
        self.assertEqual(pinned["model"], "gpt-test")
        self.assertEqual(pinned["provider"], "openai")
        self.assertRegex(pinned["timestamp"], DIAGNOSTIC_TIMESTAMP_PATTERN)

    def test_diagnostics_contain_only_sanitized_request_metadata(self) -> None:
        status, _response, _elapsed = self.request("claude-test")
        self.assertEqual(status, 200)
        diagnostics_status, diagnostics = self.get_json("/diagnostics")
        self.assertEqual(diagnostics_status, 200)
        self.assertEqual(diagnostics["instance_id"], self.gateway.instance_id)
        self.assertEqual(diagnostics["profile"], "hybrid")
        self.assertEqual(diagnostics["root_model"], "gpt-test")
        self.assertEqual(diagnostics["root_provider"], "openai")
        event = diagnostics["events"][-1]
        self.assertEqual(event["provider"], "anthropic")
        self.assertEqual(event["model"], "claude-test")
        self.assertEqual(event["status"], 200)
        self.assertGreater(event["request_bytes"], 0)
        self.assertGreater(event["response_bytes"], 0)
        for diagnostic_event in diagnostics["events"]:
            self.assertRegex(
                diagnostic_event["timestamp"], DIAGNOSTIC_TIMESTAMP_PATTERN
            )
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
                self.assertEqual(config.profile, "openrouter-pure")
                self.assertEqual(config.root_model, "vendor/model-test")
                self.assertEqual(config.root_provider, "openrouter")

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
        self.assertEqual(config.profile, "hybrid")
        self.assertEqual(config.root_model, "gpt-test")
        self.assertEqual(config.root_provider, "openai")

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


class RateLimitFailoverTests(unittest.TestCase):
    """Rate-limit handoff: 429s retry on a healthy same-category peer."""

    def setUp(self) -> None:
        self.openai = RecordingServer()
        self.anthropic = RecordingServer()
        self.threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (self.openai, self.anthropic)
        ]
        for thread in self.threads:
            thread.start()
        self.config = router.RouterConfig(
            {
                "gpt-test": "openai",
                # Two more OpenAI seats so a single subscription pool can be
                # exercised: provider escalation needs same-provider peers.
                "gpt-two": "openai",
                "gpt-three": "openai",
                "grok-test": "grok",
                "claude-test": "anthropic",
            },
            f"http://127.0.0.1:{self.openai.server_address[1]}",
            f"http://127.0.0.1:{self.anthropic.server_address[1]}",
            production=False,
        )

    def tearDown(self) -> None:
        if getattr(self, "gateway", None) is not None:
            self.gateway.shutdown()
            self.gateway.server_close()
        for server in (self.openai, self.anthropic):
            server.shutdown()
            server.server_close()

    def start_gateway(
        self,
        failover: dict[str, tuple[str, ...]],
        anthropic_rate_limit: str = "native",
        background_model: str | None = None,
    ) -> None:
        rebuilt = router.RouterConfig(
            {
                "gpt-test": "openai",
                # Two more OpenAI seats so a single subscription pool can be
                # exercised: provider escalation needs same-provider peers.
                "gpt-two": "openai",
                "gpt-three": "openai",
                "grok-test": "grok",
                "claude-test": "anthropic",
            },
            f"http://127.0.0.1:{self.openai.server_address[1]}",
            f"http://127.0.0.1:{self.anthropic.server_address[1]}",
            production=False,
            failover=failover,
            anthropic_rate_limit=anthropic_rate_limit,
            background_model=background_model,
        )
        self.gateway = router.RouterServer(("127.0.0.1", 0), rebuilt)
        threading.Thread(
            target=self.gateway.serve_forever, daemon=True
        ).start()

    def request_full(self, model: str) -> tuple[int, bytes, dict[str, str]]:
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "hello"}],
        }, separators=(",", ":")).encode("utf-8")
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_address[1], timeout=5
        )
        connection.request(
            "POST",
            "/v1/messages?beta=true",
            body=body,
            headers={
                "content-type": "application/json",
                "authorization": "Bearer synthetic-claude-oauth",
                "anthropic-version": "2023-06-01",
            },
        )
        response = connection.getresponse()
        payload = response.read()
        status = response.status
        headers = {name.lower(): value for name, value in response.getheaders()}
        response.close()
        connection.close()
        return status, payload, headers

    def request(self, model: str) -> tuple[int, bytes]:
        status, payload, _headers = self.request_full(model)
        return status, payload

    def raw_request(self, body: bytes) -> tuple[int, bytes, dict[str, str]]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_address[1], timeout=5
        )
        connection.request(
            "POST", "/v1/messages", body=body,
            headers={
                "content-type": "application/json",
                "authorization": "Bearer synthetic-claude-oauth",
                "anthropic-version": "2023-06-01",
            },
        )
        response = connection.getresponse()
        payload = response.read()
        status = response.status
        headers = {n.lower(): v for n, v in response.getheaders()}
        response.close()
        connection.close()
        return status, payload, headers

    def diagnostics(self) -> list[dict[str, object]]:
        return list(self.gateway.diagnostic_report()["events"])

    def wait_for_events(self, count: int) -> list[dict[str, object]]:
        """Wait for the server thread to finish recording after a response.

        The router streams the response before it records the request event,
        so a client that has the full body can still race the bookkeeping.
        """
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            events = self.diagnostics()
            if len(events) >= count:
                return events
            time.sleep(0.02)
        return self.diagnostics()

    def wait_for_matching_events(
        self, field: str, value: str, count: int = 1
    ) -> list[dict[str, object]]:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            events = self.diagnostics()
            if sum(event.get(field) == value for event in events) >= count:
                return events
            time.sleep(0.02)
        return self.diagnostics()

    def test_payment_required_hands_off_like_a_rate_limit(self) -> None:
        """402 walks the chain instead of ending the request.

        xAI answers 402 when a Grok subscription balance is spent and
        OpenRouter answers it when credits run out. Both used to reach the
        client untouched, so a declared chain never engaged.
        """
        self.start_gateway({"gpt-test": ("grok-test",)})
        self.openai.payment_required_models = {"gpt-test"}
        status, payload = self.request("gpt-test")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])
        served = [json.loads(r["body"])["model"] for r in self.openai.requests]
        self.assertEqual(served, ["gpt-test", "grok-test"])
        events = self.wait_for_matching_events(
            "kind", "rate_limit_failover_succeeded"
        )
        outcomes = [
            (event["model"], event["outcome"])
            for event in events
            if "outcome" in event
        ]
        self.assertIn(("gpt-test", "upstream_rate_limited"), outcomes)
        sanitized = [
            event for event in events
            if event.get("kind") == "sanitized_error_substituted"
        ]
        self.assertEqual(sanitized[0]["status"], 402)

    def test_payment_required_without_a_chain_still_reaches_the_client(self) -> None:
        """With nothing to hand off to, the 402 must not become a 200."""
        self.start_gateway({})
        self.openai.payment_required_models = {"gpt-test"}
        status, payload = self.request("gpt-test")
        # Chain exhaustion answers 429 so Claude Code's own backoff applies,
        # exactly as it already does for a real rate limit.
        self.assertEqual(status, 429)
        self.assertEqual(
            json.loads(payload)["error"]["type"], "rate_limit_error"
        )

    def test_rate_limit_fails_over_to_the_next_same_category_peer(self) -> None:
        self.start_gateway({"gpt-test": ("grok-test",)})
        self.openai.rate_limited_models = {"gpt-test"}
        status, payload = self.request("gpt-test")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])
        served = [json.loads(r["body"])["model"] for r in self.openai.requests]
        self.assertEqual(served, ["gpt-test", "grok-test"])
        events = self.wait_for_matching_events(
            "kind", "rate_limit_failover_succeeded"
        )
        outcomes = [
            (event["model"], event["outcome"])
            for event in events
            if "outcome" in event
        ]
        self.assertIn(("gpt-test", "upstream_rate_limited"), outcomes)
        completed = [
            event for event in events if event.get("outcome") == "completed"
        ]
        self.assertEqual(completed[0]["model"], "grok-test")
        self.assertEqual(completed[0]["failover_from"], "gpt-test")
        actions = [event for event in events if "kind" in event]
        action_kinds = [event["kind"] for event in actions]
        self.assertLess(
            action_kinds.index("rate_limit_failover_attempted"),
            action_kinds.index("rate_limit_failover_succeeded"),
        )
        attempted = next(
            event for event in actions
            if event["kind"] == "rate_limit_failover_attempted"
        )
        succeeded = next(
            event for event in actions
            if event["kind"] == "rate_limit_failover_succeeded"
        )
        self.assertEqual(
            (attempted["from_model"], attempted["to_model"]),
            ("gpt-test", "grok-test"),
        )
        self.assertEqual(
            (succeeded["from_model"], succeeded["to_model"], succeeded["hops"]),
            ("gpt-test", "grok-test", 1),
        )
        for action in actions:
            self.assertRegex(action["timestamp"], DIAGNOSTIC_TIMESTAMP_PATTERN)

    def test_cooldown_skips_the_limited_model_on_later_requests(self) -> None:
        self.start_gateway({"gpt-test": ("grok-test",)})
        self.openai.rate_limited_models = {"gpt-test"}
        first_status, _ = self.request("gpt-test")
        self.assertEqual(first_status, 200)
        self.wait_for_matching_events("kind", "rate_limit_failover_succeeded")
        before = len(self.openai.requests)
        second_status, _ = self.request("gpt-test")
        self.assertEqual(second_status, 200)
        # The cooled-down model is not contacted again; only the peer is.
        self.assertEqual(len(self.openai.requests), before + 1)
        self.assertEqual(
            json.loads(self.openai.requests[-1]["body"])["model"], "grok-test"
        )
        events = self.wait_for_matching_events(
            "kind", "rate_limit_failover_succeeded", count=2
        )
        outcomes = [event.get("outcome") for event in events]
        self.assertIn("rate_limit_cooldown_skip", outcomes)
        cooldown_actions = [
            event for event in events
            if event.get("kind") == "rate_limit_cooldown_skipped"
        ]
        self.assertEqual(cooldown_actions[-1]["model"], "gpt-test")
        self.assertRegex(
            cooldown_actions[-1]["timestamp"], DIAGNOSTIC_TIMESTAMP_PATTERN
        )

    def test_second_limited_model_cools_the_whole_subscription(self) -> None:
        """A subscription meters one pool, so stop probing after evidence.

        The first rejection cools only that model, because one busy model
        is not proof the pool is spent. A second distinct model of the same
        provider is that proof, and every remaining seat is then skipped
        without a round trip.
        """
        self.start_gateway({
            "gpt-test": ("gpt-two",),
            "gpt-two": ("gpt-three",),
        })
        self.openai.rate_limited_models = {"gpt-test", "gpt-two", "gpt-three"}
        status, _payload = self.request("gpt-test")
        self.assertEqual(status, 429)
        events = self.wait_for_matching_events(
            "kind", "rate_limit_chain_exhausted"
        )
        served = [json.loads(r["body"])["model"] for r in self.openai.requests]
        # gpt-three is never contacted: the provider cooled after gpt-two.
        self.assertEqual(served, ["gpt-test", "gpt-two"])
        escalations = [
            e for e in events
            if e.get("kind") == "rate_limit_provider_cooldown"
        ]
        self.assertEqual(len(escalations), 1)
        self.assertEqual(escalations[0]["provider"], "openai")
        self.assertEqual(escalations[0]["model"], "gpt-two")
        skipped = [
            e for e in events
            if e.get("kind") == "rate_limit_cooldown_skipped"
        ]
        self.assertIn("gpt-three", [e["model"] for e in skipped])

    def test_one_limited_model_does_not_cool_its_provider(self) -> None:
        """A single rejection must not strand a seat that still answers.

        Anthropic meters Opus separately from Sonnet, so escalating on the
        first 429 would throw away the most useful handoff there is.
        """
        self.start_gateway({"gpt-test": ("gpt-two",)})
        self.openai.rate_limited_models = {"gpt-test"}
        status, payload = self.request("gpt-test")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])
        served = [json.loads(r["body"])["model"] for r in self.openai.requests]
        self.assertEqual(served, ["gpt-test", "gpt-two"])
        events = self.wait_for_matching_events(
            "kind", "rate_limit_failover_succeeded"
        )
        self.assertEqual(
            [e for e in events if e.get("kind") == "rate_limit_provider_cooldown"],
            [],
        )

    def test_rate_limit_exhaustion_answers_429_with_a_clear_type(self) -> None:
        self.start_gateway({"gpt-test": ("grok-test",)})
        self.openai.rate_limited_models = {"gpt-test", "grok-test"}
        status, payload = self.request("gpt-test")
        self.assertEqual(status, 429)
        self.assertIn(b"rate_limit_error", payload)
        events = self.wait_for_matching_events(
            "outcome", "rate_limit_exhausted"
        )
        outcomes = [event.get("outcome") for event in events]
        self.assertEqual(outcomes.count("upstream_rate_limited"), 2)
        self.assertIn("rate_limit_exhausted", outcomes)
        exhausted = [
            event for event in events
            if event.get("kind") == "rate_limit_chain_exhausted"
        ]
        self.assertEqual(len(exhausted), 1)
        self.assertEqual(exhausted[0]["model"], "gpt-test")
        self.assertEqual(exhausted[0]["last_model"], "grok-test")
        self.assertEqual(exhausted[0]["models_considered"], 2)
        self.assertRegex(exhausted[0]["timestamp"], DIAGNOSTIC_TIMESTAMP_PATTERN)

    def test_an_anthropic_rate_limit_reaches_claude_code_untouched(self) -> None:
        # Claude Code recognizes an Anthropic limit and can wait it out, but
        # only if it sees the real response. Replacing it spends another
        # provider to dodge a wait and hides the fields that handling reads.
        self.start_gateway({"claude-test": ("gpt-test",)})
        self.anthropic.rate_limited_models = {"claude-test"}
        before = len(self.openai.requests)
        status, payload, headers = self.request_full("claude-test")
        self.assertEqual(status, 429)
        # The upstream body and its headers survive verbatim.
        self.assertIn(b"slow down", payload)
        self.assertEqual(headers.get("retry-after"), "30")
        # No handoff happened, so the peer was never charged for the dodge.
        self.assertEqual(len(self.openai.requests), before)
        events = self.wait_for_matching_events(
            "kind", "anthropic_rate_limit_passthrough"
        )
        passed = [
            event for event in events
            if event.get("kind") == "anthropic_rate_limit_passthrough"
        ]
        self.assertEqual(passed[0]["model"], "claude-test")

    def test_anthropic_handoff_mode_restores_the_walk(self) -> None:
        self.start_gateway(
            {"claude-test": ("gpt-test",)}, anthropic_rate_limit="handoff"
        )
        self.anthropic.rate_limited_models = {"claude-test"}
        status, _payload, _headers = self.request_full("claude-test")
        self.assertEqual(status, 200)
        models = [
            json.loads(request["body"])["model"]
            for request in self.openai.requests
        ]
        self.assertEqual(models, ["gpt-test"])

    def test_the_mode_survives_the_scrubbed_daemon_environment(self) -> None:
        # The daemon is spawned with an allowlisted environment, so a mode
        # read from a variable inside the child would always be the default.
        # It has to travel as an argument, and the allowlist must not quietly
        # start carrying Airlock variables instead.
        self.assertNotIn(
            "AIRLOCK_ANTHROPIC_RATE_LIMIT", router.minimal_child_environment()
        )
        arguments = router.parser().parse_args([
            "serve",
            "--parent-pid", "1",
            "--snapshot", "s",
            "--snapshot-sha256", "0" * 64,
            "--ready-file", "r",
            "--anthropic-rate-limit", "handoff",
        ])
        self.assertEqual(arguments.anthropic_rate_limit, "handoff")
        default = router.parser().parse_args([
            "serve",
            "--parent-pid", "1",
            "--snapshot", "s",
            "--snapshot-sha256", "0" * 64,
            "--ready-file", "r",
        ])
        self.assertEqual(default.anthropic_rate_limit, "native")

    def test_other_providers_and_other_statuses_still_hand_off(self) -> None:
        # The passthrough is narrow on purpose: an overloaded server or a
        # spent balance is not a wait, and no other provider has client-side
        # limit handling to defer to.
        self.start_gateway({"gpt-test": ("claude-test",)})
        self.openai.rate_limited_models = {"gpt-test"}
        status, _payload = self.request("gpt-test")
        self.assertEqual(status, 200)
        kinds = [event.get("kind") for event in self.wait_for_events(2)]
        self.assertNotIn("anthropic_rate_limit_passthrough", kinds)

    def test_background_haiku_request_is_served_by_the_seated_seat(self) -> None:
        # Reproduces a real failure: Claude Code runs compaction on a Haiku
        # model ID directly rather than the family slot Airlock seats, so a
        # session that does not enable Haiku as a worker answered 400 and
        # compaction died with "Model is not enabled for this session".
        self.start_gateway({}, background_model="claude-test")
        body = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "messages": [{"role": "user", "content": "summarize"}],
        }, separators=(",", ":")).encode("utf-8")
        status, payload, _headers = self.raw_request(body)
        self.assertEqual(status, 200, payload[:200])
        served = [json.loads(r["body"])["model"] for r in self.anthropic.requests]
        self.assertEqual(served, ["claude-test"])
        events = self.wait_for_matching_events(
            "kind", "background_model_substituted"
        )
        substituted = [
            e for e in events if e.get("kind") == "background_model_substituted"
        ]
        self.assertEqual(substituted[0]["requested"], "claude-haiku-4-5-20251001")
        self.assertEqual(substituted[0]["model"], "claude-test")

    def test_an_unknown_model_is_still_refused_and_now_recorded(self) -> None:
        # Substitution is limited to the Haiku family. Everything else stays
        # refused, and the refusal is recorded so it can be diagnosed at all.
        self.start_gateway({}, background_model="claude-test")
        body = json.dumps({
            "model": "claude-opus-9",
            "messages": [{"role": "user", "content": "hi"}],
        }, separators=(",", ":")).encode("utf-8")
        status, payload, _headers = self.raw_request(body)
        self.assertEqual(status, 400)
        self.assertIn(b"Model is not enabled for this session", payload)
        self.assertEqual(self.anthropic.requests, [])
        events = self.wait_for_matching_events("kind", "model_not_enabled")
        refused = [e for e in events if e.get("kind") == "model_not_enabled"]
        self.assertEqual(refused[0]["model"], "claude-opus-9")

    def test_without_a_seated_seat_the_haiku_request_is_refused(self) -> None:
        self.start_gateway({})
        body = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "messages": [{"role": "user", "content": "hi"}],
        }, separators=(",", ":")).encode("utf-8")
        status, payload, _headers = self.raw_request(body)
        self.assertEqual(status, 400)
        self.assertIn(b"Model is not enabled for this session", payload)

    def test_the_replacement_is_told_it_is_the_replacement(self) -> None:
        # Proven live: a handed-off worker kept answering as the model that
        # was asked for and named it when asked directly, because its system
        # prompt still described that model and nothing corrected it.
        self.start_gateway({"gpt-test": ("grok-test",)})
        self.openai.rate_limited_models = {"gpt-test"}
        status, _payload = self.request("gpt-test")
        self.assertEqual(status, 200)
        served = [
            json.loads(request["body"]) for request in self.openai.requests
            if json.loads(request["body"])["model"] == "grok-test"
        ]
        self.assertEqual(len(served), 1)
        system = served[0]["system"]
        text = system if isinstance(system, str) else json.dumps(system)
        self.assertIn("gpt-test was unavailable", text)
        self.assertIn("You are grok-test", text)
        # The model that was asked for is never told this about itself.
        first = json.loads(self.openai.requests[0]["body"])
        self.assertEqual(first["model"], "gpt-test")
        self.assertNotIn("Routing note", json.dumps(first.get("system", "")))

    def test_disabling_handoff_leaves_an_anthropic_limit_alone(self) -> None:
        # With no chain there is nothing to walk, so replacing the provider's
        # own answer only removes information the client could have used.
        self.start_gateway({})
        self.anthropic.rate_limited_models = {"claude-test"}
        status, payload, headers = self.request_full("claude-test")
        self.assertEqual(status, 429)
        self.assertIn(b"slow down", payload)
        self.assertEqual(headers.get("retry-after"), "30")

    def test_disabling_handoff_still_sanitizes_other_providers(self) -> None:
        # A Grok or OpenRouter error body is foreign text arriving at a client
        # that expects Anthropic's shape, so it stays replaced even with no
        # chain. The retry timing still gets through.
        self.start_gateway({})
        self.openai.rate_limited_models = {"gpt-test"}
        status, payload, headers = self.request_full("gpt-test")
        self.assertEqual(status, 429)
        self.assertNotIn(b"slow down", payload)
        self.assertIn(b"no failover peer", payload)
        self.assertEqual(headers.get("retry-after"), "30")

    def test_exhaustion_tells_the_client_when_to_retry(self) -> None:
        # Without this header the client cannot tell a temporary limit from a
        # permanent one, and cannot schedule its own resume. The value is
        # rebuilt from the parsed number rather than copied from upstream.
        self.start_gateway({"gpt-test": ("grok-test",)})
        self.openai.rate_limited_models = {"gpt-test", "grok-test"}
        status, _payload, headers = self.request_full("gpt-test")
        self.assertEqual(status, 429)
        self.assertEqual(headers.get("retry-after"), "30")
        events = self.wait_for_matching_events(
            "outcome", "rate_limit_exhausted"
        )
        exhausted = [
            event for event in events
            if event.get("kind") == "rate_limit_chain_exhausted"
        ]
        self.assertEqual(exhausted[0]["retry_after"], 30)

    def test_a_cooldown_only_exhaustion_still_carries_a_retry_hint(self) -> None:
        # The second request contacts nobody, so there is no upstream header
        # to pass on. The remaining local cooldown is the honest substitute.
        self.start_gateway({"gpt-test": ("grok-test",)})
        self.openai.rate_limited_models = {"gpt-test", "grok-test"}
        self.request("gpt-test")
        self.wait_for_matching_events("outcome", "rate_limit_exhausted")
        before = len(self.openai.requests)

        status, _payload, headers = self.request_full("gpt-test")
        self.assertEqual(status, 429)
        self.assertEqual(len(self.openai.requests), before)
        hint = headers.get("retry-after")
        self.assertIsNotNone(hint)
        self.assertTrue(1 <= int(hint) <= 30, hint)

    def test_exhausted_cooldowns_make_no_later_upstream_request(self) -> None:
        self.start_gateway({"gpt-test": ("grok-test",)})
        self.openai.rate_limited_models = {"gpt-test", "grok-test"}
        first_status, _ = self.request("gpt-test")
        self.assertEqual(first_status, 429)
        first_events = self.wait_for_matching_events(
            "outcome", "rate_limit_exhausted"
        )
        before_events = len(first_events)
        before = len(self.openai.requests)

        second_status, payload = self.request("gpt-test")
        self.assertEqual(second_status, 429)
        self.assertIn(b"rate_limit_error", payload)
        self.assertEqual(len(self.openai.requests), before)
        all_events = self.wait_for_matching_events(
            "outcome", "rate_limit_exhausted", count=2
        )
        later_events = all_events[before_events:]
        later_outcomes = [event.get("outcome") for event in later_events]
        self.assertIn("rate_limit_cooldown_skip", later_outcomes)
        self.assertIn("rate_limit_exhausted", later_outcomes)
        self.assertNotIn("upstream_rate_limited", later_outcomes)
        cooldown_models = [
            event["model"] for event in later_events
            if event.get("kind") == "rate_limit_cooldown_skipped"
        ]
        self.assertEqual(cooldown_models, ["gpt-test", "grok-test"])
        later_exhausted = [
            event for event in later_events
            if event.get("kind") == "rate_limit_chain_exhausted"
        ]
        self.assertEqual(len(later_exhausted), 1)
        self.assertEqual(later_exhausted[0]["models_considered"], 2)

    def test_without_chains_a_rate_limit_is_sanitized_not_reflected(self) -> None:
        self.start_gateway({})
        self.openai.rate_limited_models = {"gpt-test"}
        status, payload = self.request("gpt-test")
        # No chain configured: the client still sees a real 429 so its own
        # backoff applies, but the upstream body is not reflected and the
        # message names the situation plainly. Nothing was tried after the
        # first model, so it must not claim the whole category is limited.
        self.assertEqual(status, 429)
        self.assertIn(b"no failover peer", payload)
        self.assertNotIn(b"Every model", payload)
        self.assertNotIn(b"slow down", payload)
        events = self.wait_for_matching_events(
            "outcome", "rate_limit_exhausted"
        )
        outcomes = [event.get("outcome") for event in events]
        self.assertIn("upstream_rate_limited", outcomes)
        self.assertIn("rate_limit_exhausted", outcomes)
        kinds = [event.get("kind") for event in events]
        self.assertIn("sanitized_error_substituted", kinds)
        self.assertIn("rate_limit_chain_exhausted", kinds)

    def test_hop_off_openrouter_strips_the_provider_pin(self) -> None:
        or_server = RecordingServer()
        or_thread = threading.Thread(target=or_server.serve_forever, daemon=True)
        or_thread.start()
        try:
            rebuilt = router.RouterConfig(
                {
                    "qwen/qwen3.6-27b": "openrouter",
                    "gpt-test": "openai",
                },
                f"http://127.0.0.1:{self.openai.server_address[1]}",
                f"http://127.0.0.1:{self.anthropic.server_address[1]}",
                production=False,
                openrouter={
                    "qwen/qwen3.6-27b": router.OpenRouterRoute(
                        endpoint_provider="chutes/fp8",
                        provider_name="Chutes",
                        provider_slug="chutes",
                        quantization="fp8",
                        canonical_slug="qwen/qwen3.6-27b-20260422",
                    ),
                },
                openrouter_key=b"sk-or-v1-SENTINEL_ROUTER_KEY_123456",
                openrouter_url=(
                    f"http://127.0.0.1:{or_server.server_address[1]}/api/v1"
                ),
                failover={"qwen/qwen3.6-27b": ("gpt-test",)},
            )
            self.gateway = router.RouterServer(("127.0.0.1", 0), rebuilt)
            threading.Thread(
                target=self.gateway.serve_forever, daemon=True
            ).start()
            or_server.rate_limited_models = {"qwen/qwen3.6-27b"}
            status, payload = self.request("qwen/qwen3.6-27b")
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(payload)["ok"])
            forwarded = json.loads(self.openai.requests[-1]["body"])
            self.assertEqual(forwarded["model"], "gpt-test")
            self.assertNotIn("provider", forwarded)
        finally:
            or_server.shutdown()
            or_server.server_close()


class ContextOverflowHandoffTests(unittest.TestCase):
    """Classified context overflow walks the chain, then shrinks once."""

    def setUp(self) -> None:
        self.openai = RecordingServer()
        self.anthropic = RecordingServer()
        self.threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (self.openai, self.anthropic)
        ]
        for thread in self.threads:
            thread.start()
        self.routes = {
            "gpt-test": "openai",
            "grok-test": "grok",
            "claude-test": "anthropic",
            "luna-test": "openai",
            "small-test": "openai",
            "big-test": "anthropic",
        }

    def tearDown(self) -> None:
        if getattr(self, "gateway", None) is not None:
            self.gateway.shutdown()
            self.gateway.server_close()
        for server in (self.openai, self.anthropic):
            server.shutdown()
            server.server_close()

    def start_gateway(
        self,
        failover: dict[str, tuple[str, ...]],
        *,
        windows: dict[str, int] | None = None,
        compactors: dict[str, str] | None = None,
        shrink: str = "auto",
        anthropic_rate_limit: str = "native",
    ) -> None:
        rebuilt = router.RouterConfig(
            self.routes,
            f"http://127.0.0.1:{self.openai.server_address[1]}",
            f"http://127.0.0.1:{self.anthropic.server_address[1]}",
            production=False,
            failover=failover,
            context_windows=windows,
            compactors=compactors,
            overflow_shrink=shrink,
            anthropic_rate_limit=anthropic_rate_limit,
        )
        self.gateway = router.RouterServer(("127.0.0.1", 0), rebuilt)
        threading.Thread(
            target=self.gateway.serve_forever, daemon=True
        ).start()

    def request(
        self,
        model: str,
        *,
        messages: list[dict[str, object]] | None = None,
        system: str | None = "You are helpful.",
    ) -> tuple[int, bytes]:
        payload: dict[str, object] = {"model": model, "stream": True}
        if system is not None:
            payload["system"] = system
        payload["messages"] = (
            messages
            if messages is not None
            else [{"role": "user", "content": "hello"}]
        )
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_address[1], timeout=15
        )
        connection.request(
            "POST",
            "/v1/messages?beta=true",
            body=body,
            headers={
                "content-type": "application/json",
                "authorization": "Bearer synthetic-claude-oauth",
                "anthropic-version": "2023-06-01",
            },
        )
        response = connection.getresponse()
        raw = response.read()
        status = response.status
        response.close()
        connection.close()
        return status, raw

    def diagnostics(self) -> list[dict[str, object]]:
        return list(self.gateway.diagnostic_report()["events"])

    def wait_for_kind(
        self, kind: str, count: int = 1
    ) -> list[dict[str, object]]:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            events = self.diagnostics()
            if sum(e.get("kind") == kind for e in events) >= count:
                return events
            time.sleep(0.02)
        return self.diagnostics()

    def served_models(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for server, provider in ((self.openai, "openai"), (self.anthropic, "anthropic")):
            for entry in server.requests:
                try:
                    pairs.append(
                        (provider, json.loads(entry["body"])["model"])
                    )
                except ValueError:
                    continue
        return pairs

    @staticmethod
    def message_text(message: dict[str, object]) -> str:
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict)
            )
        return ""

    def compactor_calls(self, server: RecordingServer) -> list[dict[str, object]]:
        calls: list[dict[str, object]] = []
        for entry in server.requests:
            body = entry["body"]
            if b"<segment>" not in body and b"<notes>" not in body:
                continue
            try:
                calls.append(json.loads(body))
            except ValueError:
                continue
        return calls

    def test_anthropic_phrase_overflow_advances_the_chain(self) -> None:
        self.openai.overflow_models["gpt-test"] = ("anthropic", 233153, 200000)
        self.start_gateway({"gpt-test": ("grok-test",)})
        status, payload = self.request("gpt-test")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])
        self.assertEqual(
            self.served_models(),
            [("openai", "gpt-test"), ("openai", "grok-test")],
        )
        events = self.wait_for_kind("failover_overflow_succeeded")
        kinds = [e["kind"] for e in events if "kind" in e]
        self.assertIn("upstream_context_overflow", kinds)
        overflow = next(
            e for e in events if e.get("kind") == "upstream_context_overflow"
        )
        self.assertEqual(overflow["model"], "gpt-test")
        self.assertEqual(overflow["status"], 400)
        self.assertEqual(overflow["prompt_tokens"], 233153)
        self.assertEqual(overflow["limit_tokens"], 200000)
        attempted = next(
            e for e in events
            if e.get("kind") == "failover_overflow_attempted"
        )
        self.assertEqual(
            (attempted["from_model"], attempted["to_model"]),
            ("gpt-test", "grok-test"),
        )
        succeeded = next(
            e for e in events
            if e.get("kind") == "failover_overflow_succeeded"
        )
        self.assertEqual(
            (succeeded["from_model"], succeeded["to_model"], succeeded["hops"]),
            ("gpt-test", "grok-test", 1),
        )
        outcomes = [
            (e["model"], e.get("outcome"))
            for e in events
            if "outcome" in e
        ]
        self.assertIn(("gpt-test", "upstream_context_overflow"), outcomes)
        completed = [
            e for e in events if e.get("outcome") == "completed"
        ]
        self.assertEqual(completed[-1]["model"], "grok-test")

    def test_proxy_413_overflow_also_walks_with_counts(self) -> None:
        self.openai.overflow_models["gpt-test"] = ("proxy", 300000, 200000)
        self.start_gateway({"gpt-test": ("claude-test",)})
        status, payload = self.request("gpt-test")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])
        self.assertEqual(
            self.served_models(),
            [("openai", "gpt-test"), ("anthropic", "claude-test")],
        )
        events = self.wait_for_kind("failover_overflow_succeeded")
        overflow = next(
            e for e in events if e.get("kind") == "upstream_context_overflow"
        )
        self.assertEqual(overflow["status"], 413)
        self.assertEqual(overflow["prompt_tokens"], 300000)
        self.assertEqual(overflow["limit_tokens"], 200000)

    def test_unrecognized_400_breaks_the_chain_unchanged(self) -> None:
        self.openai.overflow_models["gpt-test"] = ("plain", 1, 1)
        self.start_gateway({"gpt-test": ("claude-test",)})
        status, payload = self.request("gpt-test")
        # Fail closed: an ordinary malformed-request 400 streams through
        # verbatim and never advances the chain.
        self.assertEqual(status, 400)
        self.assertIn(b"max_tokens: Field required", payload)
        self.assertEqual(self.served_models(), [("openai", "gpt-test")])
        time.sleep(0.1)
        events = self.diagnostics()
        self.assertNotIn(
            "upstream_context_overflow",
            [e.get("kind") for e in events],
        )
        self.assertNotIn(
            "failover_overflow_attempted",
            [e.get("kind") for e in events],
        )

    def test_known_too_small_peer_is_prescreened_and_skipped(self) -> None:
        self.openai.overflow_models["gpt-test"] = ("anthropic", 300000, 400000)
        self.start_gateway(
            {"gpt-test": ("small-test", "big-test")},
            windows={"gpt-test": 400000, "small-test": 50000, "big-test": 1000000},
        )
        status, payload = self.request("gpt-test")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])
        served = [model for _provider, model in self.served_models()]
        self.assertEqual(served, ["gpt-test", "big-test"])
        events = self.wait_for_kind("failover_overflow_succeeded")
        skipped = next(
            e for e in events
            if e.get("kind") == "failover_overflow_skipped"
        )
        self.assertEqual(skipped["from_model"], "gpt-test")
        self.assertEqual(skipped["to_model"], "small-test")
        self.assertEqual(skipped["estimated_tokens"], 300000)
        self.assertEqual(skipped["peer_window"], 50000)

    def test_shrink_compacts_when_no_peer_fits(self) -> None:
        self.openai.compactor_models = {"luna-test"}
        self.openai.overflow_models["gpt-test"] = ("anthropic", 300000, 400000)
        self.start_gateway(
            {"gpt-test": ("luna-test",)},
            windows={"gpt-test": 400000, "luna-test": 200000},
            compactors={"openai": "luna-test"},
        )
        filler_a = "OLD-A " + "x" * 45000
        filler_b = "OLD-B " + "y" * 45000
        messages = [
            {"role": "user", "content": filler_a},
            {"role": "assistant", "content": filler_b},
            {
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "read_file",
                    "input": {"path": "a.txt"},
                }],
            },
            {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "file contents Z",
                }],
            },
            {"role": "user", "content": "What changed in a.txt?"},
        ]
        status, payload = self.request("gpt-test", messages=messages)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])
        events = self.wait_for_kind("failover_shrink_compacted")
        kinds = [e["kind"] for e in events if "kind" in e]
        self.assertIn("failover_overflow_skipped", kinds)
        compacted = next(
            e for e in events
            if e.get("kind") == "failover_shrink_compacted"
        )
        self.assertEqual(compacted["target_model"], "luna-test")
        self.assertEqual(compacted["compactor_model"], "luna-test")
        succeeded = next(
            e for e in events
            if e.get("kind") == "failover_overflow_succeeded"
        )
        self.assertEqual(
            (succeeded["from_model"], succeeded["to_model"]),
            ("gpt-test", "luna-test"),
        )
        shrunk_hop = next(
            e for e in events
            if e.get("kind") == "failover_overflow_attempted"
        )
        self.assertTrue(shrunk_hop["shrunk"])
        # Compaction ran through the economy worker, then one retry landed.
        calls = self.compactor_calls(self.openai)
        self.assertGreaterEqual(len(calls), 2)
        retry = json.loads(self.openai.requests[-1]["body"])
        self.assertEqual(retry["model"], "luna-test")
        self.assertEqual(retry["system"], "You are helpful.")
        rebuilt_messages = retry["messages"]
        first = rebuilt_messages[0]
        self.assertEqual(first["role"], "user")
        self.assertIn(
            "Earlier conversation condensed due to context window limits",
            first["content"][0]["text"],
        )
        self.assertIn("Goal: continue the work.", first["content"][0]["text"])
        texts = [self.message_text(m) for m in rebuilt_messages[1:]]
        joined = " ".join(texts)
        # The dropped head is gone; the retained tail keeps OLD-B verbatim.
        self.assertNotIn("OLD-A", joined)
        self.assertIn("OLD-B", joined)
        serialized = json.dumps(rebuilt_messages)
        self.assertIn("file contents Z", serialized)
        # The recent tail stays verbatim and the tool pair survives intact.
        self.assertIn("What changed in a.txt?", joined)
        tool_use_positions = [
            index for index, message in enumerate(rebuilt_messages)
            if isinstance(message.get("content"), list)
            and any(
                isinstance(block, dict) and block.get("type") == "tool_use"
                for block in message["content"]
            )
        ]
        self.assertEqual(tool_use_positions, [2])
        tool_result_message = rebuilt_messages[3]
        self.assertEqual(
            tool_result_message["content"][0].get("tool_use_id"), "toolu_1"
        )

    def test_second_turn_reuses_the_cached_prefix_brief(self) -> None:
        self.openai.compactor_models = {"luna-test"}
        self.openai.overflow_models["gpt-test"] = ("anthropic", 300000, 400000)
        self.start_gateway(
            {"gpt-test": ("luna-test",)},
            windows={"gpt-test": 400000, "luna-test": 200000},
            compactors={"openai": "luna-test"},
        )

        def conversation(markers: list[str], final: str) -> list[dict[str, object]]:
            built: list[dict[str, object]] = []
            role_cycle = ["user", "assistant"]
            for index, marker in enumerate(markers):
                built.append({
                    "role": role_cycle[index % 2],
                    "content": marker + " " + "z" * 40000,
                })
            built.append({"role": "user", "content": final})
            return built

        turn_one_markers = ["MARK-ALPHA", "MARK-BETA"]
        status, _payload = self.request(
            "gpt-test",
            messages=conversation(turn_one_markers, "final question one"),
        )
        self.assertEqual(status, 200)
        self.wait_for_kind("failover_shrink_compacted")
        before = len(self.openai.requests)
        turn_two_markers = turn_one_markers + ["MARK-GAMMA"]
        status, _payload = self.request(
            "gpt-test",
            messages=conversation(turn_two_markers, "final question two"),
        )
        self.assertEqual(status, 200)
        self.wait_for_kind("failover_shrink_compacted", count=2)
        new_entries = self.openai.requests[before:]
        chunk_calls = [
            json.loads(entry["body"]) for entry in new_entries
            if b"<segment>" in entry["body"]
        ]
        self.assertEqual(len(chunk_calls), 1)
        segment_text = json.dumps(chunk_calls[0])
        # The shared prefix brief was reused: only the oldest unit that fell
        # out of the retained tail this round is re-compacted, so ALPHA never
        # travels again.
        self.assertNotIn("MARK-ALPHA", segment_text)
        self.assertIn("MARK-BETA", segment_text)
        merge_calls = [
            json.loads(entry["body"]) for entry in new_entries
            if b"<notes>" in entry["body"] and b"<segment>" not in entry["body"]
        ]
        self.assertEqual(len(merge_calls), 1)
        # GAMMA is newer history; it rides verbatim in the retry's tail.
        retry_body = new_entries[-1]["body"].decode("utf-8")
        self.assertIn("MARK-GAMMA", retry_body)
        self.assertIn("Earlier conversation condensed", retry_body)

    def test_rate_limited_root_hands_a_too_large_conversation_to_a_peer(self) -> None:
        # The scenario this feature exists for: the root is rate limited for
        # the week, so the chain walks to a peer, and the conversation is
        # larger than that peer's window. The peer was already visited by the
        # rate-limit hop, but having been tried at full size says nothing
        # about whether it fits once history is condensed.
        self.openai.compactor_models = {"luna-test"}
        self.anthropic.rate_limited_models = {"claude-test"}
        self.openai.overflow_models["gpt-test"] = ("anthropic", 300000, 272000)
        # Like a real peer: refuse the full conversation, accept it condensed.
        # The ceiling sits between the two so the shrink has to do real work.
        self.openai.overflow_max_bytes["gpt-test"] = 60000
        self.start_gateway(
            {"claude-test": ("gpt-test",)},
            windows={
                "claude-test": 1000000,
                "gpt-test": 272000,
                "luna-test": 272000,
            },
            compactors={"openai": "luna-test"},
            # An Anthropic 429 reaches the client untouched by default, so an
            # Anthropic root only walks the chain under this opt-in. The
            # mechanism under test is provider independent; this is simply
            # the shortest way to reach it.
            anthropic_rate_limit="handoff",
        )
        # Bulk sits in the old history; the recent turns are small, which is
        # what a long working session actually looks like.
        messages = [
            {"role": "user", "content": "OLDEST " + "a" * 45000},
            {"role": "assistant", "content": "MIDDLE " + "b" * 45000},
            {"role": "user", "content": "a short recent follow up"},
            {"role": "assistant", "content": "a short recent reply"},
            {"role": "user", "content": "the actual current question"},
        ]
        status, payload = self.request("claude-test", messages=messages)
        self.assertEqual(status, 200)
        # This caller streams, so the shrunk retry is delivered inside the
        # committed stream rather than as a fresh JSON response.
        self.assertIn(b'"ok"', payload)
        self.assertNotIn(b"invalid_request_error", payload)
        events = self.wait_for_kind("failover_overflow_succeeded")
        kinds = [e["kind"] for e in events if "kind" in e]
        # Rate limit first, then the size rejection, then compaction.
        self.assertIn("rate_limit_failover_attempted", kinds)
        self.assertIn("upstream_context_overflow", kinds)
        self.assertIn("failover_shrink_compacted", kinds)
        self.assertLess(
            kinds.index("rate_limit_failover_attempted"),
            kinds.index("failover_shrink_compacted"),
        )
        # The retry lands on the same peer, now carrying condensed history.
        retry = json.loads(self.openai.requests[-1]["body"])
        self.assertEqual(retry["model"], "gpt-test")
        serialized = json.dumps(retry["messages"])
        self.assertNotIn("OLDEST", serialized)
        self.assertIn("the actual current question", serialized)
        self.assertIn("Earlier conversation condensed", serialized)
        # The retry is materially smaller than the request that overflowed.
        full_attempt = len(self.openai.requests[0]["body"])
        shrunk_retry = len(self.openai.requests[-1]["body"])
        self.assertLess(shrunk_retry, full_attempt)
        self.assertLessEqual(
            shrunk_retry, self.openai.overflow_max_bytes["gpt-test"]
        )

    def test_non_streaming_caller_gets_json_not_an_event_stream(self) -> None:
        # A caller that never asked for a stream must receive one ordinary
        # JSON body carrying the answer, not SSE headers and not the
        # compactor's internal summary.
        self.openai.compactor_models = {"luna-test"}
        self.openai.overflow_models["gpt-test"] = ("anthropic", 300000, 400000)
        self.start_gateway(
            {"gpt-test": ("luna-test",)},
            windows={"gpt-test": 400000, "luna-test": 200000},
            compactors={"openai": "luna-test"},
        )
        messages = [
            {"role": "user", "content": "OLD-A " + "x" * 45000},
            {"role": "assistant", "content": "OLD-B " + "y" * 45000},
            {"role": "user", "content": "the real question"},
        ]
        body = json.dumps({
            "model": "gpt-test",
            "system": "You are helpful.",
            "messages": messages,
        }, separators=(",", ":")).encode("utf-8")
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_address[1], timeout=15
        )
        connection.request(
            "POST",
            "/v1/messages?beta=true",
            body=body,
            headers={
                "content-type": "application/json",
                "authorization": "Bearer synthetic-claude-oauth",
                "anthropic-version": "2023-06-01",
            },
        )
        response = connection.getresponse()
        status = response.status
        content_type = response.getheader("content-type") or ""
        raw = response.read()
        response.close()
        connection.close()
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        self.assertNotIn("event-stream", content_type)
        # The upstream's own answer, not a compaction brief.
        self.assertTrue(json.loads(raw)["ok"])
        self.wait_for_kind("failover_shrink_compacted")
        retry = json.loads(self.openai.requests[-1]["body"])
        self.assertEqual(retry["model"], "luna-test")
        self.assertIn("the real question", json.dumps(retry["messages"]))

    def test_truncate_fallback_runs_without_a_compactor(self) -> None:
        self.openai.overflow_models["gpt-test"] = ("anthropic", 300000, 400000)
        self.start_gateway(
            {"gpt-test": ("luna-test",)},
            windows={"gpt-test": 400000, "luna-test": 200000},
        )
        messages = [
            {"role": "user", "content": "OLD-A " + "x" * 40000},
            {"role": "user", "content": "RECENT " + "y" * 900},
            {"role": "user", "content": "final question"},
        ]
        status, payload = self.request("gpt-test", messages=messages)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])
        events = self.wait_for_kind("failover_shrink_truncated")
        truncated = next(
            e for e in events
            if e.get("kind") == "failover_shrink_truncated"
        )
        self.assertEqual(truncated["reason"], "no_compactor")
        self.assertEqual(truncated["target_model"], "luna-test")
        retry = json.loads(self.openai.requests[-1]["body"])
        self.assertEqual(retry["model"], "luna-test")
        flattened = json.dumps(retry["messages"])
        self.assertNotIn("OLD-A", flattened)
        self.assertIn("final question", flattened)

    def test_no_chunk_exceeds_the_compactor_budget(self) -> None:
        """A unit bigger than one chunk is split, never sent whole.

        Chunking used to append an oversized unit unconditionally, so one
        large turn could hand the compactor more than its own window.
        """
        self.openai.compactor_models = {"luna-test"}
        self.openai.overflow_models["gpt-test"] = ("anthropic", 300000, 400000)
        self.start_gateway(
            {"gpt-test": ("luna-test",)},
            windows={"gpt-test": 400000, "luna-test": 60000},
            compactors={"openai": "luna-test"},
        )
        # luna-test window 60000 gives a 32000-token chunk budget. The tool
        # pair below is larger than that as a unit while each of its two
        # messages still fits, so only splitting the unit keeps it in bounds.
        messages = [
            {"role": "user", "content": "OLD-A " + "x" * 30000},
            {
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu_big",
                    "name": "read_file",
                    "input": {"path": "huge.txt", "pattern": "p" * 60000},
                }],
            },
            {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu_big",
                    "content": "HUGE " + "q" * 60000,
                }],
            },
            {"role": "user", "content": "RECENT " + "z" * 900},
            {"role": "user", "content": "final question"},
        ]
        status, payload = self.request("gpt-test", messages=messages)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])
        self.wait_for_kind("failover_overflow_succeeded")
        budget = min(int(60000 * 0.8) - 16000, 120000)
        seen_compactor_call = False
        for entry in self.openai.requests:
            body = entry["body"]
            text = body.decode("utf-8", errors="replace")
            if "<segment>" not in text:
                continue
            seen_compactor_call = True
            self.assertLessEqual(len(body) // 3, budget, "chunk over budget")
        self.assertTrue(seen_compactor_call, "no compactor call was made")

    def test_off_mode_and_empty_chains_fail_closed_honestly(self) -> None:
        self.openai.overflow_models["gpt-test"] = ("anthropic", 300000, 400000)
        self.start_gateway(
            {"gpt-test": ("luna-test",)},
            windows={"gpt-test": 400000, "luna-test": 200000},
            shrink="off",
        )
        status, payload = self.request("gpt-test")
        self.assertEqual(status, 400)
        self.assertIn(b"The conversation does not fit any enabled model", payload)
        events = self.wait_for_kind("overflow_chain_exhausted")
        exhausted = next(
            e for e in events
            if e.get("kind") == "overflow_chain_exhausted"
        )
        self.assertEqual(exhausted["model"], "gpt-test")
        self.assertEqual(exhausted["models_considered"], 2)
        served = [model for _provider, model in self.served_models()]
        self.assertEqual(served, ["gpt-test"])

        # Without any chain there is nothing to walk or shrink either; bare
        # root overflow stays client-managed exactly as before this feature.
        self.gateway.shutdown()
        self.gateway.server_close()
        self.start_gateway({})
        status, payload = self.request("gpt-test")
        self.assertEqual(status, 400)
        self.assertIn(b"The conversation does not fit any enabled model", payload)
        events = self.wait_for_kind("overflow_chain_exhausted", count=2)
        exhausted = [e for e in events if e.get("kind") == "overflow_chain_exhausted"][-1]
        self.assertEqual(exhausted["models_considered"], 1)


class FailoverPlumbingTests(unittest.TestCase):
    """Unit coverage for Retry-After parsing and cooldown bookkeeping."""

    def test_retry_after_accepts_bounded_delta_seconds(self) -> None:
        self.assertIsNone(router.parse_retry_after(None))
        self.assertIsNone(router.parse_retry_after(""))
        self.assertIsNone(router.parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT"))
        self.assertIsNone(router.parse_retry_after("-5"))
        self.assertIsNone(router.parse_retry_after("nan"))
        self.assertIsNone(router.parse_retry_after("9" * 40))
        self.assertEqual(router.parse_retry_after("30"), 30.0)
        self.assertEqual(
            router.parse_retry_after("99999"),
            router.MAX_RATE_LIMIT_COOLDOWN_SECONDS,
        )

    def test_cooldowns_expire_after_their_window(self) -> None:
        cooldowns = router.RateLimitCooldowns()
        now = time.monotonic()
        with mock.patch.object(router.time, "monotonic", return_value=now):
            cooldowns.mark("m-a", None)
            cooldowns.mark("m-b", 2.0)
            self.assertTrue(cooldowns.active("m-a"))
            self.assertEqual(cooldowns.active_models(), ["m-a", "m-b"])
        with mock.patch.object(
            router.time, "monotonic", return_value=now + 30
        ):
            self.assertTrue(cooldowns.active("m-a"))
            self.assertFalse(cooldowns.active("m-b"))
        with mock.patch.object(
            router.time,
            "monotonic",
            return_value=now + router.DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS + 1,
        ):
            self.assertFalse(cooldowns.active("m-a"))
            self.assertEqual(cooldowns.active_models(), [])


class SnapshotFailoverValidationTests(unittest.TestCase):
    """The snapshot schema bounds failover chains to real routed models."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, failover):
        return write_test_snapshot(
            self.tmp.name,
            routes={"gpt-test": "openai", "claude-test": "anthropic"},
            agents={
                "airlock-test": {
                    "model": "gpt-test",
                    "provider": "openai",
                    "extra_usage": False,
                },
                "airlock-other": {
                    "model": "claude-test",
                    "provider": "anthropic",
                    "extra_usage": False,
                },
            },
            failover=failover,
        )

    def test_valid_chain_round_trips(self) -> None:
        path, digest = self.write({"gpt-test": ["claude-test"]})
        snapshot = POLICY.load_session_snapshot(str(path), digest)
        self.assertEqual(snapshot.failover["gpt-test"], ("claude-test",))

    def test_missing_failover_defaults_to_empty(self) -> None:
        raw = POLICY.validate_session_snapshot({
            "schema_version": 1,
            "protocol_version": 1,
            "profile": "openai-pure",
            "root_model": "gpt-test",
            "root_provider": "openai",
            "routes": {"gpt-test": "openai"},
            "agents": {
                "airlock-test": {
                    "model": "gpt-test",
                    "provider": "openai",
                    "extra_usage": False,
                }
            },
            "openrouter": {},
        })
        self.assertEqual(dict(raw.failover), {})

    def test_self_chains_unknown_peers_and_duplicates_are_rejected(self) -> None:
        for bad in (
            {"gpt-test": ["gpt-test"]},
            {"gpt-test": ["not-a-route"]},
            {"gpt-test": ["claude-test", "claude-test"]},
            {"unknown-model": ["claude-test"]},
            {"gpt-test": []},
            {"gpt-test": "claude-test"},
        ):
            with self.assertRaises(POLICY.PolicyValidationError):
                self.write(bad)


if __name__ == "__main__":
    unittest.main()
