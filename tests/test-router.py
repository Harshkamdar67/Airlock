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

ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "bin" / "airlock-router.py"
SPEC = importlib.util.spec_from_file_location("airlock_router_test", ROUTER)
assert SPEC is not None and SPEC.loader is not None
router = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(router)


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
        self.threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (self.openai, self.anthropic)
        ]
        for thread in self.threads:
            thread.start()
        config = router.RouterConfig(
            {
                "gpt-test": "openai",
                "claude-test": "anthropic",
            },
            f"http://127.0.0.1:{self.openai.server_address[1]}",
            f"http://127.0.0.1:{self.anthropic.server_address[1]}",
            production=False,
        )
        self.gateway = router.RouterServer(("127.0.0.1", 0), config)
        self.gateway_thread = threading.Thread(
            target=self.gateway.serve_forever, daemon=True
        )
        self.gateway_thread.start()

    def tearDown(self) -> None:
        self.gateway.shutdown()
        self.gateway.server_close()
        for server in (self.openai, self.anthropic):
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
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(response)["error"]["type"], "api_error")
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

    def test_usage_is_observed_from_a_json_response(self) -> None:
        self.anthropic.mode = "usage_json"
        status, _payload, _elapsed = self.request("claude-test")
        self.assertEqual(status, 200)
        _diagnostics_status, diagnostics = self.get_json("/diagnostics")
        event = diagnostics["events"][-1]
        self.assertEqual(event["usage"], {"input_tokens": 11, "output_tokens": 22})

    def test_missing_upstream_usage_is_recorded_as_absent(self) -> None:
        self.openai.mode = "no_usage_stream"
        status, payload, _elapsed = self.request("gpt-test")
        self.assertEqual(status, 200)
        self.assertIn(b'"message_stop"', payload)
        _diagnostics_status, diagnostics = self.get_json("/diagnostics")
        event = diagnostics["events"][-1]
        self.assertEqual(event["outcome"], "completed")
        self.assertNotIn("usage", event)

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
            {"gpt-test", "claude-test"},
        )
        connection.close()

    def test_cli_router_stops_after_owning_process_exits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = directory
            owner = subprocess.Popen([
                sys.executable,
                "-c",
                "import time; time.sleep(30)",
            ])
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(ROUTER),
                        "start",
                        "--parent-pid",
                        str(owner.pid),
                        "--routes-json",
                        json.dumps({"gpt-test": "openai"}),
                    ],
                    env=environment,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    timeout=15,
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
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROUTER),
                    "start",
                    "--parent-pid",
                    "2147483647",
                    "--routes-json",
                    json.dumps({"gpt-test": "openai"}),
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


if __name__ == "__main__":
    unittest.main()
