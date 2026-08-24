#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Session-scoped model router for native hybrid Claude Code sessions."""

from __future__ import annotations

import argparse
from collections import deque
import ctypes
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, NamedTuple
from urllib.parse import urlsplit
import zlib

MANAGED_BUNDLE_VERSION = "2026.08.11.3"
MANAGED_PROTOCOL_VERSION = 5
MAX_REQUEST_BYTES = 64 * 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 10
RESPONSE_HEADER_TIMEOUT_SECONDS = 10 * 60
STREAM_TIMEOUT_SECONDS = 60 * 60
READY_TIMEOUT_SECONDS = 30
MAX_DIAGNOSTIC_EVENTS = 256
MAX_USAGE_LINE_BYTES = 256 * 1024
MAX_USAGE_BODY_BYTES = 1024 * 1024
MAX_OPENROUTER_IDENTITY_PREFIX_BYTES = 256 * 1024
MAX_UPSTREAM_REASON_CHARS = 200
MAX_UPSTREAM_DETAIL_CHARS = 320
MAX_OPENROUTER_JSON_RESPONSE_BYTES = 64 * 1024 * 1024
RATE_LIMIT_STATUS_CODES = frozenset({429, 529})
MAX_FAILOVER_HOPS = 3
DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 60.0
MAX_RATE_LIMIT_COOLDOWN_SECONDS = 300.0
MAX_FAILOVER_PEERS_PER_MODEL = 8
OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER = "https://github.com/Harshkamdar67/Airlock"
OPENROUTER_TITLE = "Airlock"
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
    """An upstream failure.

    ``retryable`` says whether sending the same request again could plausibly
    succeed. A dropped connection or a timeout is worth another attempt. A
    refused credential, a redirect, or a malformed upstream response is not:
    the next attempt fails the same way. Claude Code retries a 5xx up to ten
    times, and each attempt resends the whole conversation, so a deterministic
    failure reported as 5xx costs ten full-size requests and still fails.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class RateLimitedError(RouterError):
    """The upstream answered 429 or 529 before any response body was sent.

    Carrying the status and Retry-After hint lets the handler cool the model
    down and retry the same request on a healthy same-category peer instead of
    forwarding the rate limit to Claude Code, whose own backoff loop is what
    made background calls such as WebFetch appear to hang for minutes.
    """

    def __init__(self, status: int, retry_after: float | None) -> None:
        super().__init__(f"upstream returned HTTP {status}")
        self.status = status
        self.retry_after = retry_after


def parse_retry_after(value: str | None) -> float | None:
    """Return a bounded cooldown hint from a Retry-After header value."""
    if not value:
        return None
    text = value.strip()
    if not text or len(text) > 32:
        return None
    try:
        seconds = float(text)
    except ValueError:
        # HTTP-date form is intentionally ignored; the default applies.
        return None
    if seconds < 0 or seconds != seconds:
        return None
    return min(seconds, MAX_RATE_LIMIT_COOLDOWN_SECONDS)


class RateLimitCooldowns:
    """Tracks models that recently answered 429 so later requests skip them.

    A cooled-down model is not contacted again until the window expires, which
    turns a repeated multi-minute backoff hang into a one-time cost. Entries
    live only in router process memory; a restarted session starts clean.
    """

    def __init__(self) -> None:
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def mark(self, model: str, retry_after: float | None) -> float:
        seconds = (
            DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS
            if retry_after is None
            else max(retry_after, 1.0)
        )
        with self._lock:
            self._until[model] = time.monotonic() + seconds
        return seconds

    def active(self, model: str) -> bool:
        now = time.monotonic()
        with self._lock:
            deadline = self._until.get(model)
            if deadline is None:
                return False
            if deadline <= now:
                del self._until[model]
                return False
            return True

    def active_models(self) -> list[str]:
        now = time.monotonic()
        with self._lock:
            expired = [m for m, d in self._until.items() if d <= now]
            for m in expired:
                del self._until[m]
            return sorted(self._until)


def load_policy_schema():
    path = Path(__file__).with_name("airlock_policy.py")
    try:
        if path.is_symlink() or not path.is_file():
            raise RouterError("managed router policy helper is unavailable")
        spec = importlib.util.spec_from_file_location("airlock_router_policy", path)
        if spec is None or spec.loader is None:
            raise RouterError("managed router policy helper is unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except RouterError:
        raise
    except Exception as exc:
        raise RouterError("managed router policy helper is unavailable") from exc
    return module


def load_router_snapshot(path: str, digest: str):
    schema = load_policy_schema()
    try:
        snapshot = schema.load_session_snapshot(path, digest)
    except Exception as exc:
        raise RouterError("session policy snapshot is invalid") from exc
    if snapshot.protocol_version != MANAGED_PROTOCOL_VERSION:
        raise RouterError("session policy snapshot protocol is incompatible")
    return snapshot


def load_openrouter_key() -> bytes:
    path = Path(__file__).with_name("airlock_openrouter_auth.py")
    try:
        if path.is_symlink() or not path.is_file():
            raise RouterError("OpenRouter credential helper is unavailable")
        spec = importlib.util.spec_from_file_location("airlock_router_openrouter_auth", path)
        if spec is None or spec.loader is None:
            raise RouterError("OpenRouter credential helper is unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        key = module.load_key()
    except RouterError:
        raise
    except Exception as exc:
        raise RouterError("OpenRouter credential is unavailable") from exc
    if key is None:
        raise RouterError("OpenRouter credential is missing")
    try:
        return module.validate_key(key)
    except Exception as exc:
        raise RouterError("OpenRouter credential is invalid") from exc


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


class OpenRouterRoute(NamedTuple):
    endpoint_provider: str
    provider_name: str
    provider_slug: str
    quantization: str
    canonical_slug: str


class RouterConfig:
    def __init__(
        self,
        routes: dict[str, str],
        openai_url: str | None,
        anthropic_url: str,
        *,
        production: bool = True,
        openrouter: dict[str, OpenRouterRoute] | None = None,
        openrouter_key: bytes | None = None,
        openrouter_url: str = OPENROUTER_URL,
        failover: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        if not routes or any(
            not isinstance(model, str)
            or not model
            or provider not in {"openai", "anthropic", "grok", "openrouter"}
            for model, provider in routes.items()
        ):
            raise RouterError("router model routes are invalid")
        failover_map: dict[str, tuple[str, ...]] = {}
        for source, peers in dict(failover or {}).items():
            peer_list = tuple(peers)
            if (
                source not in routes
                or not peer_list
                or len(peer_list) > MAX_FAILOVER_PEERS_PER_MODEL
                or len(set(peer_list)) != len(peer_list)
                or any(
                    peer == source or peer not in routes for peer in peer_list
                )
            ):
                raise RouterError("failover chains are invalid")
            failover_map[source] = peer_list
        openrouter_routes = {
            model for model, provider in routes.items() if provider == "openrouter"
        }
        metadata = dict(openrouter or {})
        if set(metadata) != openrouter_routes:
            raise RouterError("OpenRouter routes and endpoint pins do not agree")
        if any(not isinstance(route, OpenRouterRoute) for route in metadata.values()):
            raise RouterError("OpenRouter route metadata is invalid")
        if openrouter_routes and openrouter_key is None:
            raise RouterError("OpenRouter credential is missing")
        self.routes = dict(routes)
        self.failover = failover_map
        self.openrouter_routes = metadata
        self.openrouter_key = bytes(openrouter_key) if openrouter_key is not None else None
        # GPT (Codex) and Grok subscription models share the loopback proxy;
        # OpenRouter-only snapshots deliberately have no subscription upstream.
        subscription_routes = {
            model for model, provider in routes.items()
            if provider in {"openai", "grok"}
        }
        if subscription_routes and not openai_url:
            raise RouterError("subscription proxy upstream is required")
        self.openai = (
            parse_upstream(openai_url, "openai", production=production)
            if openai_url
            else None
        )
        self.anthropic = parse_upstream(
            anthropic_url, "anthropic", production=production
        )
        self.openrouter = parse_upstream(
            openrouter_url, "openrouter", production=production
        )


class RouterServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], config: RouterConfig) -> None:
        super().__init__(address, RouterHandler)
        self.config = config
        self.instance_id = secrets.token_hex(8)
        self.rate_limits = RateLimitCooldowns()
        self.diagnostics: deque[dict[str, object]] = deque(
            maxlen=MAX_DIAGNOSTIC_EVENTS
        )
        self.usage_summary: dict[tuple[str, str], dict[str, object]] = {}
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
            provider = event.get("provider")
            model = event.get("model")
            if not isinstance(provider, str) or not isinstance(model, str):
                return
            key = (provider, model)
            summary = self.usage_summary.setdefault(key, {
                "provider": provider,
                "model": model,
                "requests": 0,
                "completed": 0,
                "errors": 0,
                "usage_events": 0,
                **{field: 0 for field in USAGE_FIELDS},
            })
            summary["requests"] += 1
            if event.get("outcome") == "completed":
                summary["completed"] += 1
            else:
                summary["errors"] += 1
            usage = event.get("usage")
            if isinstance(usage, dict):
                summary["usage_events"] += 1
                for field in USAGE_FIELDS:
                    value = usage.get(field)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        summary[field] += value

    def diagnostic_report(self) -> dict[str, object]:
        with self.diagnostics_lock:
            return {
                "instance_id": self.instance_id,
                "rate_limit_cooldowns": self.rate_limits.active_models(),
                "events": [dict(event) for event in self.diagnostics],
                "summary": [
                    dict(self.usage_summary[key])
                    for key in sorted(self.usage_summary)
                ],
            }


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
            self.send_json(200, self.router.diagnostic_report())
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
            if provider == "openrouter" and path != "/v1/messages":
                raise InvalidRequestError(
                    "OpenRouter supports only the Messages operation in this release"
                )
            if provider == "openrouter":
                body, stripped_tools = prepare_openrouter_request(
                    body,
                    model,
                    self.router.config.openrouter_routes[model],
                )
                if stripped_tools:
                    self.router.record_diagnostic({
                        "kind": "openrouter_server_tools_stripped",
                        "model": model,
                        "removed": stripped_tools,
                    })
            attempt_model = model
            attempt_provider = provider
            working_body = body
            visited = {model}
            hops = 0
            failover_from: str | None = None
            while True:
                # A model still cooling down from an earlier 429 is skipped
                # outright, so repeated background calls pay no doomed round
                # trip once one limit has been seen.
                if self.router.rate_limits.active(attempt_model):
                    skipped = (
                        self.next_failover_model(attempt_model, visited)
                        if hops < MAX_FAILOVER_HOPS
                        else None
                    )
                    self.record_request(
                        attempt_provider,
                        attempt_model,
                        429,
                        body_size,
                        0,
                        started,
                        "rate_limit_cooldown_skip",
                    )
                    if skipped is None:
                        raise RateLimitedError(429, None)
                    working_body = self.retarget_request_body(
                        working_body, skipped
                    )
                    visited.add(skipped)
                    failover_from = failover_from or model
                    attempt_model = skipped
                    attempt_provider = self.router.config.routes[skipped]
                    hops += 1
                    continue
                try:
                    status, response_bytes, outcome, usage = self.forward(
                        attempt_provider, attempt_model, working_body
                    )
                    break
                except RateLimitedError as exc:
                    self.router.rate_limits.mark(attempt_model, exc.retry_after)
                    self.record_request(
                        attempt_provider,
                        attempt_model,
                        exc.status,
                        body_size,
                        0,
                        started,
                        "upstream_rate_limited",
                    )
                    nxt = (
                        self.next_failover_model(attempt_model, visited)
                        if hops < MAX_FAILOVER_HOPS
                        else None
                    )
                    if nxt is None:
                        raise
                    working_body = self.retarget_request_body(working_body, nxt)
                    visited.add(nxt)
                    failover_from = failover_from or model
                    attempt_model = nxt
                    attempt_provider = self.router.config.routes[nxt]
                    hops += 1
            self.record_request(
                attempt_provider,
                attempt_model,
                status,
                body_size,
                response_bytes,
                started,
                outcome,
                usage,
                extra=(
                    {"failover_from": failover_from} if failover_from else None
                ),
            )
        except RateLimitedError:
            if provider is not None and model is not None:
                self.record_request(
                    provider, model, 429, body_size, 0, started,
                    "rate_limit_exhausted",
                )
            self.safe_error_response(
                429,
                "rate_limit_error",
                "Every model in this request's category is rate limited right"
                " now; retry shortly or switch models.",
            )
        except InvalidRequestError as exc:
            self.safe_error_response(400, "invalid_request_error", str(exc))
        except UpstreamError as exc:
            # Retryable failures stay 502 so Claude Code can try again.
            # Deterministic failures return a non-retryable status, which stops
            # ten pointless full-size retries. Messages are fixed strings from
            # this file or bounded printable model identities; arbitrary
            # upstream response bodies are never reflected.
            if exc.retryable:
                status, error_type = 502, "api_error"
                detail = upstream_failure_message(exc)
                outcome = "upstream_error"
            else:
                status, error_type = 400, "invalid_request_error"
                detail = str(exc)
                outcome = "upstream_rejected"
            if provider is not None and model is not None:
                self.record_request(
                    provider, model, status, body_size, 0, started, outcome
                )
            self.safe_error_response(status, error_type, detail)
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
        extra: dict[str, object] | None = None,
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
        if extra:
            event.update(extra)
        self.router.record_diagnostic(event)

    def next_failover_model(self, source: str, visited: set[str]) -> str | None:
        """Return the first healthy same-category peer for a rate-limited model."""
        for peer in self.router.config.failover.get(source, ()):
            if peer in visited or peer not in self.router.config.routes:
                continue
            if self.router.rate_limits.active(peer):
                continue
            return peer
        return None

    def retarget_request_body(self, body: bytes, target_model: str) -> bytes:
        """Rewrite an already validated request onto another routed model.

        The OpenRouter ``provider`` pin belongs to the route that produced it,
        so it is dropped before the new transport prepares its own. Hopping
        onto an OpenRouter route runs the same preparation every direct
        request to that route would receive.
        """
        payload = request_payload(body)
        payload.pop("provider", None)
        payload["model"] = target_model
        try:
            cleaned = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii")
        except (TypeError, ValueError) as exc:
            raise InvalidRequestError(
                "Request body could not be re-targeted"
            ) from exc
        if self.router.config.routes[target_model] == "openrouter":
            prepared, stripped_tools = prepare_openrouter_request(
                cleaned,
                target_model,
                self.router.config.openrouter_routes[target_model],
            )
            if stripped_tools:
                self.router.record_diagnostic({
                    "kind": "openrouter_server_tools_stripped",
                    "model": target_model,
                    "removed": stripped_tools,
                })
            return prepared
        return cleaned

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
        self, provider: str, model: str, body: bytes
    ) -> tuple[int, int, str, dict[str, int] | None]:
        if provider == "openrouter":
            return self.forward_openrouter(model, body)
        if provider in {"openai", "grok"}:
            upstream = self.router.config.openai
            if upstream is None:
                raise UpstreamError("Subscription proxy upstream is unavailable")
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
            if status in RATE_LIMIT_STATUS_CODES:
                raise RateLimitedError(
                    status, parse_retry_after(response.getheader("retry-after"))
                )
            if 300 <= status < 400:
                raise UpstreamError("Upstream redirects are not allowed", retryable=False)
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

    def forward_openrouter(
        self, model: str, body: bytes
    ) -> tuple[int, int, str, dict[str, int] | None]:
        upstream = self.router.config.openrouter
        route = self.router.config.openrouter_routes[model]
        allowed_response_models = {model, route.canonical_slug}
        key = self.router.config.openrouter_key
        if key is None:
            raise UpstreamError("OpenRouter credential is unavailable", retryable=False)
        connection = make_connection(upstream)
        headers = openrouter_headers(key, len(body))
        target = f"{upstream[3]}/messages"
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
            if status in RATE_LIMIT_STATUS_CODES:
                raise RateLimitedError(
                    status, parse_retry_after(response.getheader("retry-after"))
                )
            if 300 <= status < 400:
                raise UpstreamError("OpenRouter redirects are not allowed", retryable=False)
            set_stream_timeout(connection, response)
            if not 200 <= status < 300:
                local_status = status if 400 <= status <= 599 else 502
                self.send_error_response(
                    local_status,
                    "api_error",
                    "The selected OpenRouter upstream rejected the request",
                )
                return local_status, 0, "upstream_error", None
            encoding = (response.getheader("content-encoding") or "").strip().lower()
            if encoding not in {"", "identity"}:
                raise UpstreamError("OpenRouter response encoding is unsupported", retryable=False)
            content_type = (response.getheader("content-type") or "").lower()
            expected_length = response.getheader("content-length")
            observer.configure(content_type, encoding)
            if "text/event-stream" in content_type:
                prefix = bytearray()
                while True:
                    chunk = response.read1(16 * 1024)
                    if not chunk:
                        raise UpstreamError(
                            f"OpenRouter route {model} stream ended before model identity",
                            retryable=False,
                        )
                    prefix.extend(chunk)
                    if len(prefix) > MAX_OPENROUTER_IDENTITY_PREFIX_BYTES:
                        raise UpstreamError(
                            f"OpenRouter route {model} stream model identity is too large",
                            retryable=False,
                        )
                    response_model = openrouter_sse_model(bytes(prefix), model)
                    if response_model is None:
                        continue
                    if response_model not in allowed_response_models:
                        raise UpstreamError(
                            openrouter_model_mismatch(model, response_model),
                            retryable=False,
                        )
                    break
                self.send_response(status, response.reason)
                self.send_header("content-type", "text/event-stream")
                self.send_header("connection", "close")
                self.end_headers()
                headers_sent = True
                self.close_connection = True
                self.wfile.write(prefix)
                self.wfile.flush()
                response_bytes = len(prefix)
                observer.observe(prefix)
                while True:
                    chunk = response.read1(16 * 1024)
                    if not chunk:
                        break
                    response_bytes += len(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    observer.observe(chunk)
            elif content_type.split(";", 1)[0].strip() == "application/json":
                with tempfile.TemporaryFile(mode="w+b") as spool:
                    while True:
                        chunk = response.read1(16 * 1024)
                        if not chunk:
                            break
                        response_bytes += len(chunk)
                        if response_bytes > MAX_OPENROUTER_JSON_RESPONSE_BYTES:
                            raise UpstreamError("OpenRouter JSON response is too large", retryable=False)
                        spool.write(chunk)
                        observer.observe(chunk)
                    if expected_length is not None:
                        try:
                            if response_bytes != int(expected_length):
                                raise UpstreamError("OpenRouter JSON response ended early", retryable=False)
                        except ValueError as exc:
                            raise UpstreamError("OpenRouter response length is invalid", retryable=False) from exc
                    spool.seek(0)
                    try:
                        payload = strict_json_loads(spool.read())
                    except InvalidRequestError as exc:
                        raise UpstreamError("OpenRouter JSON response is invalid", retryable=False) from exc
                    response_model = (
                        payload.get("model") if isinstance(payload, dict) else None
                    )
                    if (
                        not isinstance(response_model, str)
                        or response_model not in allowed_response_models
                    ):
                        raise UpstreamError(
                            openrouter_model_mismatch(model, response_model),
                            retryable=False,
                        )
                    self.send_response(status, response.reason)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(response_bytes))
                    self.send_header("connection", "close")
                    self.end_headers()
                    headers_sent = True
                    self.close_connection = True
                    spool.seek(0)
                    while True:
                        chunk = spool.read(16 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                    self.wfile.flush()
            else:
                raise UpstreamError("OpenRouter response type is unsupported", retryable=False)
            observer.finish()
            return status, response_bytes, "completed", observer.snapshot()
        except UpstreamError:
            raise
        except TimeoutError:
            if not headers_sent:
                raise UpstreamError("OpenRouter connection timed out")
            self.close_connection = True
            return status, response_bytes, "upstream_timeout", observer.snapshot()
        except (BrokenPipeError, ConnectionResetError):
            if not headers_sent:
                raise UpstreamError("OpenRouter connection failed")
            self.close_connection = True
            return (
                status,
                response_bytes,
                "connection_interrupted",
                observer.snapshot(),
            )
        except (ConnectionError, OSError, ssl.SSLError, http.client.HTTPException):
            if not headers_sent:
                raise UpstreamError("OpenRouter connection failed")
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
        if provider == "openrouter" and (
            parsed.scheme != "https"
            or parsed.hostname != "openrouter.ai"
            or parsed.port not in {None, 443}
            or parsed.path.rstrip("/") != "/api/v1"
        ):
            raise RouterError("OpenRouter upstream must be the official HTTPS API")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    prefix = parsed.path.rstrip("/")
    return parsed.scheme, parsed.hostname, port, prefix


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def strict_json_loads(raw: bytes) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite JSON number")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise InvalidRequestError("Request body must be strict UTF-8 JSON") from exc


def request_payload(body: bytes) -> dict[str, Any]:
    payload = strict_json_loads(body)
    if not isinstance(payload, dict):
        raise InvalidRequestError("Request body must be a JSON object")
    return payload


def request_model(body: bytes) -> str:
    model = request_payload(body).get("model")
    if not isinstance(model, str) or not model:
        raise InvalidRequestError("Request model is missing")
    return model


def _normalize_openrouter_system_messages(payload: dict[str, Any]) -> None:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return
    retained: list[Any] = []
    lifted: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "system":
            retained.append(message)
            continue
        if set(message) != {"role", "content"}:
            raise InvalidRequestError(
                "OpenRouter system-role messages contain unsupported fields"
            )
        content = message.get("content")
        if isinstance(content, str):
            lifted.append({"type": "text", "text": content})
            continue
        if (
            isinstance(content, list)
            and content
            and all(
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                for block in content
            )
        ):
            lifted.extend(content)
            continue
        raise InvalidRequestError(
            "OpenRouter system-role message content is unsupported"
        )
    if not lifted:
        return
    if not retained:
        raise InvalidRequestError(
            "OpenRouter request messages must include a user or assistant message"
        )
    system = payload.get("system")
    if system is None:
        system_blocks: list[Any] = []
    elif isinstance(system, str):
        system_blocks = [{"type": "text", "text": system}]
    elif isinstance(system, list):
        system_blocks = list(system)
    else:
        raise InvalidRequestError("OpenRouter system content is unsupported")
    payload["messages"] = retained
    payload["system"] = system_blocks + lifted


# Anthropic server tools (web search, code execution, and friends) execute
# inside Anthropic's API. Pinned OpenRouter upstreams do not speak that
# dialect, so a forwarded declaration or history block would bounce with an
# opaque upstream rejection. Strip them before forwarding: fresh sessions are
# steered to Airlock's local web tools by managed settings, and stale sessions
# lose declarations the route could never honor anyway.
SERVER_TOOL_TYPE_PATTERN = re.compile(
    r"^(?:web_search|web_fetch|code_execution|computer|text_editor|bash|memory)"
    r"_\d{8}$"
)
SERVER_TOOL_RESULT_SUFFIX = "_tool_result"
KEEP_SERVER_TOOLS_VARIABLE = "AIRLOCK_OPENROUTER_KEEP_SERVER_TOOLS"


def _is_server_tool_declaration(tool: Any) -> bool:
    if not isinstance(tool, dict):
        return False
    tool_type = tool.get("type")
    if not isinstance(tool_type, str):
        return False
    return SERVER_TOOL_TYPE_PATTERN.match(tool_type) is not None


def _is_server_tool_result_block(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    block_type = block.get("type")
    if not isinstance(block_type, str):
        return False
    if block_type == "server_tool_use":
        return True
    # Client tool results are typed exactly "tool_result"; every prefixed
    # "<server tool>_tool_result" variant comes from server-side execution.
    return (
        block_type != "tool_result" and block_type.endswith(SERVER_TOOL_RESULT_SUFFIX)
    )


def strip_anthropic_server_tools(payload: dict[str, Any]) -> list[str]:
    """Remove server-tool declarations and history blocks in place.

    Returns the removed entry names and block types so the caller can record
    a diagnostic event instead of failing silently.
    """
    removed: list[str] = []
    tools = payload.get("tools")
    if isinstance(tools, list):
        kept = [tool for tool in tools if not _is_server_tool_declaration(tool)]
        if len(kept) != len(tools):
            removed.extend(
                str(tool.get("name") or tool.get("type"))
                for tool in tools
                if _is_server_tool_declaration(tool)
            )
            if kept:
                payload["tools"] = kept
            else:
                payload.pop("tools", None)
                payload.pop("tool_choice", None)
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            kept_blocks = [
                block for block in content if not _is_server_tool_result_block(block)
            ]
            if len(kept_blocks) == len(content):
                continue
            removed.extend(
                str(block.get("type"))
                for block in content
                if _is_server_tool_result_block(block)
            )
            if kept_blocks:
                message["content"] = kept_blocks
            else:
                message["content"] = [{
                    "type": "text",
                    "text": "[server tool exchange removed for this route]",
                }]
    return removed


def prepare_openrouter_request(
    body: bytes, model: str, route: OpenRouterRoute
) -> tuple[bytes, list[str]]:
    payload = request_payload(body)
    if payload.get("model") != model:
        raise InvalidRequestError("OpenRouter request model does not match its route")
    forbidden = {
        "canonical_slug",
        "fallback_models",
        "models",
        "plugins",
        "provider",
        "route",
        "transforms",
    }
    present = sorted(forbidden & set(payload))
    if present:
        raise InvalidRequestError(
            f"OpenRouter routing fields are managed by Airlock: {present!r}"
        )
    if "stream" in payload and type(payload["stream"]) is not bool:
        raise InvalidRequestError("OpenRouter stream must be a boolean")
    _normalize_openrouter_system_messages(payload)
    stripped: list[str] = []
    if os.environ.get(KEEP_SERVER_TOOLS_VARIABLE, "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        stripped = strip_anthropic_server_tools(payload)
    payload["provider"] = {
        "only": [route.provider_slug],
        "quantizations": [route.quantization],
        "allow_fallbacks": False,
        "require_parameters": False,
    }
    try:
        return (
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii"),
            stripped,
        )
    except (TypeError, ValueError) as exc:
        raise InvalidRequestError("OpenRouter request body is invalid") from exc


def openrouter_server_tools(payload: Any) -> list[str]:
    """Return the sorted server-side tool types declared in a request.

    A client tool carries no type, or the explicit "custom" type. Anything else
    is executed by the provider rather than by Claude Code, which OpenRouter
    routes cannot do.
    """

    tools = payload.get("tools") if isinstance(payload, dict) else None
    if not isinstance(tools, list):
        return []
    found: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        kind = tool.get("type")
        if isinstance(kind, str) and kind and kind != "custom":
            cleaned = "".join(
                character
                for character in kind
                if character.isascii() and 0x20 <= ord(character) < 0x7F
            ).strip()
            found.add(cleaned[:64] if cleaned else "unnamed")
    return sorted(found)


def openrouter_model_mismatch(expected: str, received: Any) -> str:
    """Describe a response whose model is not the declared route.

    The received value comes from the provider, so it is bounded and stripped
    to printable ASCII before it reaches operator output.
    """

    if isinstance(received, str):
        cleaned = "".join(
            character
            for character in received
            if character.isascii() and 0x20 <= ord(character) < 0x7F
        ).strip()
    else:
        cleaned = ""
    got = cleaned[:MAX_UPSTREAM_REASON_CHARS] if cleaned else "no model identity"
    return (
        f"OpenRouter response model does not match route {expected} "
        f"(received {got})"
    )


def upstream_failure_message(exc: BaseException) -> str:
    """Return a bounded, printable 502 message that keeps the real reason."""

    base = "The selected model upstream is unavailable"
    detail = "".join(
        character
        for character in str(exc)
        if character.isascii() and 0x20 <= ord(character) < 0x7F
    ).strip()
    if not detail:
        return base
    return f"{base}: {detail[:MAX_UPSTREAM_DETAIL_CHARS]}"


def openrouter_upstream_reason(payload: Any) -> str:
    """Return a short, printable reason from an upstream SSE error event.

    The text comes from the provider, so it is bounded and stripped to printable
    ASCII before it reaches operator output.
    """

    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return ""
    parts: list[str] = []
    code = error.get("code")
    if isinstance(code, (int, str)) and not isinstance(code, bool) and str(code):
        parts.append(f"code {str(code)[:32]}")
    message = error.get("message")
    if isinstance(message, str):
        cleaned = "".join(
            character
            for character in message
            if character.isascii() and 0x20 <= ord(character) < 0x7F
        ).strip()
        if cleaned:
            parts.append(cleaned[:MAX_UPSTREAM_REASON_CHARS])
    if not parts:
        return ""
    joined = "; ".join(parts)
    return f" ({joined})"


def openrouter_sse_model(prefix: bytes, model: str | None = None) -> str | None:
    normalized = prefix.replace(b"\r\n", b"\n")
    blocks = normalized.split(b"\n\n")
    for block in blocks[:-1]:
        if not block.strip() or all(
            not line or line.startswith(b":") for line in block.split(b"\n")
        ):
            continue
        event_name: bytes | None = None
        data_lines: list[bytes] = []
        for line in block.split(b"\n"):
            if line.startswith(b"event:"):
                event_name = line[6:].strip()
            elif line.startswith(b"data:"):
                data_lines.append(line[5:].lstrip())
        label = f"OpenRouter route {model}" if model else "OpenRouter"
        if not data_lines:
            raise UpstreamError(f"{label} stream omitted event data", retryable=False)
        try:
            payload = strict_json_loads(b"\n".join(data_lines))
        except InvalidRequestError as exc:
            raise UpstreamError(f"{label} stream identity is invalid", retryable=False) from exc
        payload_type = payload.get("type") if isinstance(payload, dict) else None
        # A ping carries no identity and may legitimately precede message_start.
        if event_name == b"ping" or payload_type == "ping":
            continue
        # An upstream error event explains itself. Report that instead of a
        # protocol complaint that hides the real reason.
        if event_name == b"error" or payload_type == "error":
            raise UpstreamError(
                f"{label} upstream reported an error"
                f"{openrouter_upstream_reason(payload)}",
                retryable=False,
            )
        if event_name != b"message_start" and payload_type != "message_start":
            observed = payload_type if isinstance(payload_type, str) else (
                event_name.decode("ascii", "replace") if event_name else "unknown"
            )
            raise UpstreamError(
                f"{label} stream did not start with message_start "
                f"(first event: {observed[:64]})",
                retryable=False,
            )
        message = payload.get("message")
        model = message.get("model") if isinstance(message, dict) else None
        if not isinstance(model, str) or not model:
            raise UpstreamError(f"{label} stream model identity is missing", retryable=False)
        return model
    return None


def openrouter_headers(key: bytes, content_length: int) -> dict[str, str]:
    try:
        credential = key.decode("ascii")
    except UnicodeDecodeError as exc:
        raise RouterError("OpenRouter credential is invalid") from exc
    return {
        "accept": "application/json, text/event-stream",
        "accept-encoding": "identity",
        "authorization": f"Bearer {credential}",
        "connection": "close",
        "content-length": str(content_length),
        "content-type": "application/json",
        "http-referer": OPENROUTER_REFERER,
        "x-openrouter-title": OPENROUTER_TITLE,
    }


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


def router_config_from_snapshot(
    snapshot: Any,
    openai_url: str | None,
    anthropic_url: str,
) -> RouterConfig:
    openrouter = {
        model: OpenRouterRoute(
            endpoint_provider=metadata.endpoint_provider,
            provider_name=metadata.provider_name,
            provider_slug=metadata.provider_slug,
            quantization=metadata.quantization,
            canonical_slug=metadata.canonical_slug,
        )
        for model, metadata in snapshot.openrouter.items()
    }
    key = load_openrouter_key() if openrouter else None
    return RouterConfig(
        dict(snapshot.routes),
        openai_url,
        anthropic_url,
        production=True,
        openrouter=openrouter,
        openrouter_key=key,
        failover={
            model: tuple(peers) for model, peers in snapshot.failover.items()
        },
    )


def minimal_child_environment() -> dict[str, str]:
    keep = {
        "COMSPEC",
        "DBUS_SESSION_BUS_ADDRESS",
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
        "XDG_RUNTIME_DIR",
        "XDG_STATE_HOME",
    }
    environment = {key: value for key, value in os.environ.items() if key in keep}
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def start_router(args: argparse.Namespace) -> int:
    if not process_alive(args.parent_pid):
        raise RouterError("router owner process is not running")
    snapshot = load_router_snapshot(args.snapshot, args.snapshot_sha256)
    if any(provider in {"openai", "grok"} for provider in snapshot.routes.values()) and not args.openai_url:
        raise RouterError("subscription proxy upstream is required")
    if snapshot.openrouter:
        load_openrouter_key()
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
        "--snapshot",
        args.snapshot,
        "--snapshot-sha256",
        args.snapshot_sha256,
        "--anthropic-url",
        args.anthropic_url,
    ]
    if args.openai_url:
        command.extend(("--openai-url", args.openai_url))
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
    snapshot = load_router_snapshot(args.snapshot, args.snapshot_sha256)
    config = router_config_from_snapshot(
        snapshot,
        args.openai_url,
        args.anthropic_url,
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
        sub.add_argument("--snapshot", required=True)
        sub.add_argument("--snapshot-sha256", required=True)
        sub.add_argument("--openai-url")
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
