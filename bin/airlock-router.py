#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Session-scoped model router for native hybrid Claude Code sessions."""

from __future__ import annotations

import argparse
from collections import deque
import ctypes
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socketserver
import ssl
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import urlsplit
import zlib

MANAGED_BUNDLE_VERSION = "2026.08.05.5"
MAX_REQUEST_BYTES = 64 * 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 10
RESPONSE_HEADER_TIMEOUT_SECONDS = 10 * 60
STREAM_TIMEOUT_SECONDS = 60 * 60
READY_TIMEOUT_SECONDS = 30
MAX_DIAGNOSTIC_EVENTS = 256
MAX_USAGE_LINE_BYTES = 256 * 1024
MAX_USAGE_BODY_BYTES = 1024 * 1024
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
OPENAI_PRIVATE_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-api-key",
}
ALLOWED_POST_PATHS = {
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/v1/images/generations",
}


class RouterError(RuntimeError):
    pass


class InvalidRequestError(RouterError):
    pass


class UpstreamError(RouterError):
    pass


class UsageObserver:
    """Read only the token counts out of a response that is already forwarded.

    The observer never changes, delays, or stores forwarded content. It keeps one
    bounded buffer, reads only the integer fields of an Anthropic ``usage``
    object, and drops everything else. Any parsing problem disables the observer
    for the rest of the response instead of affecting the stream.
    """

    def __init__(self) -> None:
        self.totals: dict[str, int] = {}
        self.buffer = bytearray()
        self.streaming = False
        self.skipping = False
        self.disabled = False
        self.seen = False
        self.decoder: Any = None

    def configure(self, content_type: str, content_encoding: str) -> None:
        """Decide how to read a response that has already been forwarded.

        Anthropic answers with ``Content-Encoding: gzip``, so a byte-level
        observer sees compressed data and can never find a usage object. The
        forwarded bytes are untouched either way; only this observer decodes a
        copy so that it can read the token counts.
        """
        self.streaming = "event-stream" in content_type
        encoding = content_encoding.strip().lower()
        if not encoding or encoding == "identity":
            return
        if encoding in {"gzip", "x-gzip"}:
            self.decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            return
        if encoding == "deflate":
            self.decoder = zlib.decompressobj()
            return
        # Brotli, zstd, or several encodings chained together. The standard
        # library cannot read those, and a wrong guess is worse than reporting
        # nothing, so the observer stands down and usage is recorded as absent.
        self.disable()

    def disable(self) -> None:
        self.disabled = True
        self.buffer = bytearray()
        self.decoder = None

    def observe(self, chunk: bytes) -> None:
        if self.disabled:
            return
        try:
            if self.decoder is not None:
                # Bound the output so a hostile or broken upstream cannot make
                # the observer allocate without limit.
                chunk = self.decoder.decompress(chunk, MAX_USAGE_BODY_BYTES)
                if self.decoder.unconsumed_tail:
                    self.disable()
                    return
                if not chunk:
                    return
            if not self.streaming:
                if len(self.buffer) + len(chunk) > MAX_USAGE_BODY_BYTES:
                    self.disable()
                    return
                self.buffer.extend(chunk)
                return
            self.buffer.extend(chunk)
            while True:
                index = self.buffer.find(b"\n")
                if index < 0:
                    break
                line = bytes(self.buffer[:index])
                del self.buffer[: index + 1]
                if self.skipping:
                    self.skipping = False
                    continue
                self.consume_line(line)
            if len(self.buffer) > MAX_USAGE_LINE_BYTES:
                self.buffer.clear()
                self.skipping = True
        except Exception:
            self.disable()

    def finish(self) -> None:
        if self.disabled:
            return
        try:
            if self.decoder is not None:
                # Clearing the decoder first means this re-entry takes the
                # already-decoded path instead of decompressing twice.
                tail = self.decoder.flush()
                self.decoder = None
                if tail:
                    self.observe(tail)
                    if self.disabled:
                        return
            if self.streaming:
                if self.buffer and not self.skipping:
                    self.consume_line(bytes(self.buffer))
                self.buffer.clear()
                return
            if not self.buffer:
                return
            payload = json.loads(bytes(self.buffer))
            self.buffer.clear()
            if isinstance(payload, dict):
                self.merge(payload.get("usage"))
        except Exception:
            self.disable()

    def consume_line(self, line: bytes) -> None:
        if not line.startswith(b"data:"):
            return
        payload = line[len(b"data:") :].strip()
        if not payload or payload == b"[DONE]":
            return
        if b'"usage"' not in payload:
            return
        try:
            event = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(event, dict):
            return
        self.merge(event.get("usage"))
        message = event.get("message")
        if isinstance(message, dict):
            self.merge(message.get("usage"))

    def merge(self, usage: object) -> None:
        if not isinstance(usage, dict):
            return
        for field in USAGE_FIELDS:
            value = usage.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                continue
            self.seen = True
            self.totals[field] = max(self.totals.get(field, 0), value)

    def snapshot(self) -> dict[str, int] | None:
        if not self.seen:
            return None
        return {
            field: self.totals[field] for field in USAGE_FIELDS if field in self.totals
        }


class RouterConfig:
    def __init__(
        self,
        routes: dict[str, str],
        openai_url: str,
        anthropic_url: str,
        *,
        production: bool = True,
    ) -> None:
        if not routes or any(
            not isinstance(model, str)
            or not model
            or provider not in {"openai", "anthropic", "grok"}
            for model, provider in routes.items()
        ):
            raise RouterError("router model routes are invalid")
        self.routes = dict(routes)
        # GPT (Codex) and Grok subscription models share the loopback proxy;
        # the proxy selects the upstream from the model ID and its own OAuth store.
        self.openai = parse_upstream(openai_url, "openai", production=production)
        self.anthropic = parse_upstream(
            anthropic_url, "anthropic", production=production
        )


class RouterServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], config: RouterConfig) -> None:
        super().__init__(address, RouterHandler)
        self.config = config
        self.instance_id = secrets.token_hex(8)
        self.diagnostics: deque[dict[str, object]] = deque(
            maxlen=MAX_DIAGNOSTIC_EVENTS
        )
        self.diagnostics_lock = threading.Lock()

    def server_bind(self) -> None:
        # http.server's own server_bind resolves the bound address with
        # socket.getfqdn, which is a reverse DNS lookup. On a machine whose
        # resolver is slow or unreachable that call can block for tens of
        # seconds, and the session then fails to start for a reason that has
        # nothing to do with routing. The router only ever binds loopback, so
        # the literal address is both accurate and the only value that keeps
        # startup entirely local.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port

    def record_diagnostic(self, event: dict[str, object]) -> None:
        with self.diagnostics_lock:
            self.diagnostics.append(dict(event))

    def diagnostic_snapshot(self) -> list[dict[str, object]]:
        with self.diagnostics_lock:
            return [dict(event) for event in self.diagnostics]


class RouterHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AirlockRouter"
    sys_version = ""

    @property
    def router(self) -> RouterServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_HEAD(self) -> None:
        if urlsplit(self.path).path != "/":
            self.send_error_response(404, "not_found", "Route not found")
            return
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/healthz":
            self.send_json(200, {
                "ok": True,
                "bundle_version": MANAGED_BUNDLE_VERSION,
                "instance_id": self.router.instance_id,
            })
            return
        if path == "/v1/models":
            models = [
                {"id": model, "display_name": model}
                for model in sorted(self.router.config.routes)
            ]
            self.send_json(200, {"data": models, "has_more": False})
            return
        if path == "/diagnostics":
            self.send_json(200, {
                "instance_id": self.router.instance_id,
                "events": self.router.diagnostic_snapshot(),
            })
            return
        self.send_error_response(404, "not_found", "Route not found")

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path not in ALLOWED_POST_PATHS:
            self.send_error_response(404, "not_found", "Route not found")
            return
        started = time.monotonic()
        body_size = 0
        model: str | None = None
        provider: str | None = None
        try:
            body = self.read_body()
            body_size = len(body)
            model = request_model(body)
            provider = self.router.config.routes.get(model)
            if provider is None:
                raise InvalidRequestError("Model is not enabled for this session")
            status, response_bytes, outcome, usage = self.forward(provider, body)
            self.record_request(
                provider,
                model,
                status,
                body_size,
                response_bytes,
                started,
                outcome,
                usage,
            )
        except InvalidRequestError as exc:
            self.safe_error_response(400, "invalid_request_error", str(exc))
        except UpstreamError:
            if provider is not None and model is not None:
                self.record_request(
                    provider, model, 502, body_size, 0, started, "upstream_error"
                )
            self.safe_error_response(
                502, "api_error", "The selected model upstream is unavailable"
            )
        except (ConnectionError, OSError, ssl.SSLError, http.client.HTTPException):
            if provider is not None and model is not None:
                self.record_request(
                    provider, model, 502, body_size, 0, started, "upstream_error"
                )
            self.safe_error_response(
                502, "api_error", "The selected model upstream is unavailable"
            )

    def record_request(
        self,
        provider: str,
        model: str,
        status: int,
        request_bytes: int,
        response_bytes: int,
        started: float,
        outcome: str,
        usage: dict[str, int] | None = None,
    ) -> None:
        event: dict[str, object] = {
            "provider": provider,
            "model": model,
            "status": status,
            "request_bytes": request_bytes,
            "response_bytes": response_bytes,
            "duration_ms": max(0, round((time.monotonic() - started) * 1000)),
            "outcome": outcome,
        }
        if usage:
            event["usage"] = dict(usage)
        self.router.record_diagnostic(event)

    def safe_error_response(self, status: int, kind: str, message: str) -> None:
        try:
            self.send_error_response(status, kind, message)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            self.close_connection = True

    def read_body(self) -> bytes:
        if self.headers.get("transfer-encoding"):
            raise InvalidRequestError("Chunked request bodies are not supported")
        raw_length = self.headers.get("content-length")
        if raw_length is None:
            raise InvalidRequestError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise InvalidRequestError("Content-Length is invalid") from exc
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise InvalidRequestError("Request body size is invalid")
        body = self.rfile.read(length)
        if len(body) != length:
            raise InvalidRequestError("Request body ended early")
        return body

    def forward(
        self, provider: str, body: bytes
    ) -> tuple[int, int, str, dict[str, int] | None]:
        if provider in {"openai", "grok"}:
            upstream = self.router.config.openai
        else:
            upstream = self.router.config.anthropic
        connection = make_connection(upstream)
        headers = forwarded_headers(self.headers.items(), provider, len(body))
        target = upstream_path(upstream, self.path)
        response: http.client.HTTPResponse | None = None
        headers_sent = False
        status = 502
        response_bytes = 0
        observer = UsageObserver()
        try:
            connection.connect()
            if connection.sock is not None:
                connection.sock.settimeout(RESPONSE_HEADER_TIMEOUT_SECONDS)
            connection.request("POST", target, body=body, headers=headers)
            response = connection.getresponse()
            status = response.status
            if 300 <= status < 400:
                raise UpstreamError("Upstream redirects are not allowed")
            set_stream_timeout(connection, response)
            expected_length = response.getheader("content-length")
            content_type = (response.getheader("content-type") or "").lower()
            observer.configure(
                content_type, response.getheader("content-encoding") or ""
            )
            self.send_response(status, response.reason)
            for name, value in response.getheaders():
                lowered = name.lower()
                if lowered in HOP_BY_HOP_HEADERS or lowered == "server":
                    continue
                self.send_header(name, value)
            self.send_header("connection", "close")
            self.end_headers()
            headers_sent = True
            self.close_connection = True
            while True:
                chunk = response.read1(16 * 1024)
                if not chunk:
                    break
                response_bytes += len(chunk)
                self.wfile.write(chunk)
                self.wfile.flush()
                observer.observe(chunk)
            observer.finish()
            outcome = "completed"
            if expected_length is not None:
                try:
                    if response_bytes != int(expected_length):
                        outcome = "upstream_interrupted"
                except ValueError:
                    outcome = "upstream_interrupted"
            return status, response_bytes, outcome, observer.snapshot()
        except UpstreamError:
            raise
        except TimeoutError:
            if not headers_sent:
                raise UpstreamError("Upstream connection timed out")
            self.close_connection = True
            return status, response_bytes, "upstream_timeout", observer.snapshot()
        except (BrokenPipeError, ConnectionResetError):
            if not headers_sent:
                raise UpstreamError("Upstream connection failed")
            self.close_connection = True
            return (
                status,
                response_bytes,
                "connection_interrupted",
                observer.snapshot(),
            )
        except (ConnectionError, OSError, ssl.SSLError, http.client.HTTPException):
            if not headers_sent:
                raise UpstreamError("Upstream connection failed")
            self.close_connection = True
            return status, response_bytes, "upstream_interrupted", observer.snapshot()
        finally:
            if response is not None:
                response.close()
            connection.close()

    def send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)

    def send_error_response(self, status: int, kind: str, message: str) -> None:
        self.send_json(status, {
            "type": "error",
            "error": {"type": kind, "message": message},
        })


def parse_upstream(
    value: str, provider: str, *, production: bool
) -> tuple[str, str, int, str]:
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RouterError(f"{provider} upstream URL is invalid")
    if not parsed.hostname or parsed.scheme not in {"http", "https"}:
        raise RouterError(f"{provider} upstream URL is invalid")
    if production:
        if provider == "openai" and (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
        ):
            raise RouterError("OpenAI upstream must be loopback HTTP")
        if provider == "anthropic" and (
            parsed.scheme != "https" or parsed.hostname != "api.anthropic.com"
        ):
            raise RouterError("Anthropic upstream must be the official HTTPS API")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    prefix = parsed.path.rstrip("/")
    return parsed.scheme, parsed.hostname, port, prefix


def request_model(body: bytes) -> str:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidRequestError("Request body must be UTF-8 JSON") from exc
    model = payload.get("model") if isinstance(payload, dict) else None
    if not isinstance(model, str) or not model:
        raise InvalidRequestError("Request model is missing")
    return model


def forwarded_headers(
    incoming: Any, provider: str, content_length: int
) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in incoming:
        lowered = name.lower()
        if lowered in HOP_BY_HOP_HEADERS or lowered in {"host", "content-length"}:
            continue
        if provider in {"openai", "grok"} and (
            lowered in OPENAI_PRIVATE_HEADERS or lowered == "anthropic-beta"
        ):
            continue
        result[name] = value
    result["content-length"] = str(content_length)
    result["connection"] = "close"
    if provider in {"openai", "grok"}:
        result["authorization"] = "Bearer unused"
    return result


def upstream_path(upstream: tuple[str, str, int, str], request_path: str) -> str:
    prefix = upstream[3]
    return f"{prefix}{request_path}" if prefix else request_path


def valid_router_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and port is not None
        and 0 < port < 65536
        and parsed.path in {"", "/"}
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def set_stream_timeout(
    connection: http.client.HTTPConnection,
    response: http.client.HTTPResponse,
) -> None:
    stream_socket = connection.sock
    if stream_socket is None and response.fp is not None:
        raw = getattr(response.fp, "raw", None)
        stream_socket = getattr(raw, "_sock", None)
    if stream_socket is not None:
        stream_socket.settimeout(STREAM_TIMEOUT_SECONDS)


def make_connection(
    upstream: tuple[str, str, int, str]
) -> http.client.HTTPConnection:
    scheme, host, port, _prefix = upstream
    if scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            host,
            port,
            timeout=CONNECT_TIMEOUT_SECONDS,
            context=ssl.create_default_context(),
        )
    else:
        connection = http.client.HTTPConnection(
            host, port, timeout=CONNECT_TIMEOUT_SECONDS
        )
    return connection


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
        process_query_limited_information, False, pid
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(  # type: ignore[attr-defined]
            handle, ctypes.byref(exit_code)
        ):
            return False
        return exit_code.value == 259
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]


def monitor_parent(server: RouterServer, parent_pid: int) -> None:
    while process_alive(parent_pid):
        time.sleep(1)
    server.shutdown()


def safe_runtime_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Airlock" / "router-startups"
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state / "airlock" / "router-startups"


def write_ready(path: Path, server: RouterServer) -> None:
    payload = json.dumps({
        "pid": os.getpid(),
        "url": f"http://127.0.0.1:{server.server_address[1]}",
        "instance_id": server.instance_id,
        "bundle_version": MANAGED_BUNDLE_VERSION,
    }, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    # Publish through a rename so the waiting parent never observes a file
    # that exists but is still empty, and never tries to delete a handle this
    # process still holds open. Both are visible races: a partial read fails
    # to parse, and on Windows the delete raises a sharing violation. The
    # staging name lives in the same private directory as the final name.
    staging = path.with_name(path.name + ".tmp")
    descriptor = os.open(staging, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
    except BaseException:
        try:
            staging.unlink()
        except OSError:
            pass
        raise


def parse_routes(raw: str) -> dict[str, str]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RouterError("router routes are invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RouterError("router routes must be an object")
    return payload


def minimal_child_environment() -> dict[str, str]:
    keep = {
        "COMSPEC",
        "HOME",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
        "XDG_STATE_HOME",
    }
    environment = {key: value for key, value in os.environ.items() if key in keep}
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def start_router(args: argparse.Namespace) -> int:
    if not process_alive(args.parent_pid):
        raise RouterError("router owner process is not running")
    root = safe_runtime_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.is_dir():
        raise RouterError("router startup state is unsafe")
    if os.name != "nt" and root.stat().st_mode & 0o077:
        root.chmod(0o700)
    ready = root / f"ready-{secrets.token_hex(16)}.json"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "serve",
        "--parent-pid",
        str(args.parent_pid),
        "--ready-file",
        str(ready),
        "--routes-json",
        args.routes_json,
        "--openai-url",
        args.openai_url,
        "--anthropic-url",
        args.anthropic_url,
    ]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": minimal_child_environment(),
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    startup_complete = False
    try:
        while time.monotonic() < deadline:
            if ready.is_file() and not ready.is_symlink():
                payload = json.loads(ready.read_text(encoding="ascii"))
                if (
                    payload.get("pid") != process.pid
                    or payload.get("bundle_version") != MANAGED_BUNDLE_VERSION
                    or not valid_router_url(payload.get("url"))
                ):
                    raise RouterError("router startup identity is invalid")
                startup_complete = True
                sys.stdout.write(payload["url"] + "\n")
                return 0
            if process.poll() is not None:
                raise RouterError("router stopped during startup")
            time.sleep(0.05)
        raise RouterError("router did not become ready")
    finally:
        try:
            ready.unlink()
        except OSError:
            # The marker carries no secret and lives in a private directory,
            # so a session that has already started must not fail just
            # because something else briefly held the file.
            pass
        if not startup_complete and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def serve_router(args: argparse.Namespace) -> int:
    if not process_alive(args.parent_pid):
        raise RouterError("router owner process is not running")
    config = RouterConfig(
        parse_routes(args.routes_json),
        args.openai_url,
        args.anthropic_url,
        production=True,
    )
    ready = Path(args.ready_file)
    if ready.parent.resolve() != safe_runtime_root().resolve() or ready.exists():
        raise RouterError("router ready path is unsafe")
    server = RouterServer(("127.0.0.1", 0), config)
    monitor = threading.Thread(
        target=monitor_parent,
        args=(server, args.parent_pid),
        daemon=True,
    )
    monitor.start()
    write_ready(ready, server)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="airlock-router")
    subparsers = result.add_subparsers(dest="command", required=True)
    for name in ("start", "serve"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--parent-pid", type=int, required=True)
        sub.add_argument("--routes-json", required=True)
        sub.add_argument("--openai-url", default="http://127.0.0.1:18765")
        sub.add_argument("--anthropic-url", default="https://api.anthropic.com")
        if name == "serve":
            sub.add_argument("--ready-file", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "start":
            return start_router(args)
        return serve_router(args)
    except (OSError, RouterError, ValueError, json.JSONDecodeError) as exc:
        print(f"airlock-router: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
