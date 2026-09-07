#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Session-scoped model router for native hybrid Claude Code sessions."""

from __future__ import annotations

import argparse
from collections import deque, OrderedDict
import ctypes
from datetime import datetime, timezone
import gzip
import hashlib
import hmac
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
import stat
import subprocess
import sys
import tempfile
import threading
import time
from types import MappingProxyType
from typing import Any, NamedTuple
import unicodedata
from urllib.parse import urlsplit
import zlib

MANAGED_BUNDLE_VERSION = "2026.09.06.1"
MANAGED_PROTOCOL_VERSION = 6
MAX_REQUEST_BYTES = 64 * 1024 * 1024
MAX_CONTROL_REQUEST_BYTES = 4 * 1024
MAX_CONTROL_JSON_DEPTH = 4
CONTROL_ENDPOINTS = frozenset({"/control/pin", "/control/unpin"})
CONNECT_TIMEOUT_SECONDS = 10
RESPONSE_HEADER_TIMEOUT_SECONDS = 10 * 60
STREAM_TIMEOUT_SECONDS = 60 * 60
READY_TIMEOUT_SECONDS = 30
MAX_DIAGNOSTIC_EVENTS = 256
MAX_WORKDIR_CHARS = 32_768
ROUTE_CATEGORIES = frozenset({"included", "extra", "metered", "unknown"})
ROUTE_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})
# A restart reclaims the port the dead router held. The kernel can still be
# releasing it for a moment after the owner goes away, so a fixed-port bind
# is retried briefly before it is called a failure.
REBIND_TIMEOUT_SECONDS = 10.0
REBIND_RETRY_SECONDS = 0.2
WATCH_INTERVAL_SECONDS = 1.0
MAX_ROUTER_RESTARTS = 5
MAX_USAGE_LINE_BYTES = 256 * 1024
MAX_USAGE_BODY_BYTES = 1024 * 1024
MAX_OPENROUTER_IDENTITY_PREFIX_BYTES = 256 * 1024
MAX_UPSTREAM_REASON_CHARS = 200
MAX_UPSTREAM_DETAIL_CHARS = 320
MAX_OPENROUTER_JSON_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_OPENMODEL_JSON_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_OPENMODEL_ERROR_RESPONSE_BYTES = 64 * 1024
MAX_OPENMODEL_ENDPOINTS = 16
MAX_OPENMODEL_ROUTES = 32
# At most this many handler threads may wait behind an endpoint's active
# generation slots. Further requests fail locally instead of creating an
# unbounded ThreadingHTTPServer queue under saturation.
MAX_OPENMODEL_WAITERS_PER_ENDPOINT = 4
OPENMODEL_ERROR_TIMEOUT_SECONDS = 2
OPENMODEL_SLOT_TIMEOUT_SECONDS = RESPONSE_HEADER_TIMEOUT_SECONDS
OPENMODEL_DOWNSTREAM_WRITE_TIMEOUT_SECONDS = 30
MAX_OPENMODEL_IDENTITY_CHARS = 1024
MAX_OPENMODEL_IDENTITY_BYTES = 4096
OPENMODEL_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
# The signed snapshot schema already accepts only this exact endpoint form. The
# router validates it again as it materializes transport state so a direct
# RouterConfig construction cannot create a wider network boundary.
OPENMODEL_BASE_URL_PATTERN = re.compile(
    r"http://127\.0\.0\.1:([1-9][0-9]{0,4})/v1", re.ASCII
)
OPENMODEL_NAME_PATTERN = re.compile(
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*", re.ASCII
)
# 402 joins the rate-limit statuses because it means the same thing to a
# router: this model cannot serve the request now, so try the next peer.
# xAI answers 402 when a Grok subscription's usage balance is spent, and
# OpenRouter answers it when credits run out. Neither is a rate limit in
# the literal sense, and neither clears on its own the way a 429 does, so
# the shared cooldown will keep re-testing a model that stays unavailable.
RATE_LIMIT_STATUS_CODES = frozenset({402, 429, 529})
# Statuses that can carry a context-overflow rejection. 413 is what the
# subscription proxy answers for Codex context-window failures; most other
# providers answer 400, so the body must agree before anything is classified.
OVERFLOW_STATUS_CODES = frozenset({400, 413})
MAX_OVERFLOW_PEEK_BYTES = 64 * 1024
MAX_OVERFLOW_TEXT_CHARS = MAX_OVERFLOW_PEEK_BYTES
# Curated, high-precision overflow phrases. A status alone is not enough:
# an ordinary malformed-request 400 must keep breaking the chain exactly as
# it always has, so the body has to name the context limit. LiteLLM's public
# issue tracker documents how phrase-only matching misses provider variants;
# status-first gating plus this list keeps false positives rare while real
# overflows from every shipped route classify cleanly.
OVERFLOW_PHRASES = (
    "prompt is too long",
    "context_length_exceeded",
    "context length exceeded",
    "maximum context length",
    "request_too_large",
    "context window",
)
PROMPT_TOO_LONG_COUNTS_RE = re.compile(
    r"(\d[\d,]*)\s*tokens?\s*>\s*(\d[\d,]*)", re.ASCII
)
MAX_CONTEXT_LENGTH_COUNTS_RE = re.compile(
    r"maximum context length is (\d[\d,]*) tokens", re.ASCII
)
REQUESTED_TOKENS_RE = re.compile(r"[Rr]equested (\d[\d,]*) tokens", re.ASCII)
MAX_FAILOVER_HOPS = 3
# Overflow-aware handoff: when no failover peer's context window fits the
# conversation, the router compresses history through the destination
# provider's economy worker (or truncates) and retries once.
SHRINK_CHUNK_TARGET_TOKENS = 120_000
SHRINK_OUTPUT_RESERVE_TOKENS = 16_000
SHRINK_BRIEF_RESERVE_TOKENS = 12_000
SHRINK_RECENT_TAIL_TOKENS = 24_000
SHRINK_MAX_CHUNKS = 8
SHRINK_MAX_EPISODE_INPUT_TOKENS = 900_000
SHRINK_COMPACTOR_MAX_TOKENS = 8192
SHRINK_MERGE_MAX_TOKENS = 8192
SHRINK_CACHE_ENTRIES = 8
SHRINK_CALL_TIMEOUT_SECONDS = 10 * 60
SHRINK_PING_INTERVAL_SECONDS = 15.0
SHRINK_TAIL_MIN_UNITS = 1
DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 60.0
# A subscription meters every model it serves from one usage pool, so a
# limit on one of them usually means the rest are gone too and walking to
# a same-provider peer buys nothing but a doomed round trip. OpenRouter
# bills each route separately, so its routes stay independent.
SUBSCRIPTION_PROVIDERS = frozenset({"anthropic", "openai", "grok"})
# One rejection is not evidence of a shared pool, and skipping a model
# that would have answered is worse than one wasted round trip. A second
# distinct model of the same provider failing inside the window is the
# evidence that escalates the cooldown to cover the whole provider.
PROVIDER_COOLDOWN_MIN_MODELS = 2
MAX_RATE_LIMIT_COOLDOWN_SECONDS = 300.0
MAX_FAILOVER_PEERS_PER_MODEL = 8
OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER = "https://github.com/Harshkamdar67/Airlock"
OPENROUTER_TITLE = "Airlock"
OPENROUTER_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
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


class ControlError(RouterError):
    """A fixed, non-secret response for a rejected control request."""

    def __init__(self, status: int, kind: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind


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


class OpenModelClientWriteError(RouterError):
    """A local client stopped accepting translated response bytes."""


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


class ContextOverflowError(RouterError):
    """The upstream rejected the request because it cannot fit its window.

    Raised before any response byte reaches the client so the handler can
    walk the failover chain exactly as it does for rate limits, and shrink
    the conversation when no peer is large enough either. Carrying the
    parsed token counts lets the walk pre-skip peers whose known window is
    already too small.
    """

    def __init__(
        self,
        status: int,
        prompt_tokens: int | None,
        limit_tokens: int | None,
    ) -> None:
        super().__init__(f"upstream returned HTTP {status} context overflow")
        self.status = status
        self.prompt_tokens = prompt_tokens
        self.limit_tokens = limit_tokens


class ShrinkUnavailableError(RouterError):
    """Overflow shrinking could not run or produce a usable request."""


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


def disclose_handoff(
    system: object, source_model: str, target_model: str
) -> object:
    """Append a factual note that this request was handed to a replacement.

    The caller's system prompt describes the model that was asked for, so a
    replacement left uninformed answers in that model's name and reports the
    wrong identity when asked directly. Both IDs come from the validated
    route table, never from request content, so nothing here can be steered
    by the conversation. The note is appended rather than inserted so any
    cached prefix in front of it stays intact.
    """
    note = (
        f"Routing note from Airlock: {source_model} was unavailable for this"
        f" request, so it was handed to {target_model}, which is serving this"
        " turn. Any earlier statement in this prompt about which model you are"
        f" refers to {source_model}. You are {target_model}. If you are asked"
        " which model you are, say so accurately."
    )
    if system is None:
        return note
    if isinstance(system, str):
        return f"{system}\n\n{note}"
    if isinstance(system, list):
        return [*system, {"type": "text", "text": note}]
    # An unexpected shape is left exactly as it is: a malformed system field
    # is the upstream's business, and rewriting it could invalidate the call.
    return system


def utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def retry_after_seconds(value: float | None) -> int | None:
    """Round a cooldown hint into a whole-second header value.

    Anything below a second becomes 1 rather than 0, because a zero would
    invite an immediate retry into a limit that has not lifted yet.
    """
    if value is None or value != value or value < 0:
        return None
    return max(1, min(int(value + 0.5), int(MAX_RATE_LIMIT_COOLDOWN_SECONDS)))


class RateLimitCooldowns:
    """Tracks models that recently answered 429 so later requests skip them.

    A cooled-down model is not contacted again until the window expires, which
    turns a repeated multi-minute backoff hang into a one-time cost. Entries
    live only in router process memory; a restarted session starts clean.
    """

    def __init__(self) -> None:
        self._until: dict[str, float] = {}
        self._provider_until: dict[str, float] = {}
        # provider -> {model: deadline}, the evidence that a subscription's
        # whole pool is spent rather than one model being busy.
        self._witnessed: dict[str, dict[str, float]] = {}
        self._lock = threading.Lock()

    def mark(
        self,
        model: str,
        retry_after: float | None,
        provider: str | None = None,
    ) -> tuple[float, bool]:
        """Cool ``model``, escalating to its provider once evidence allows.

        Returns (seconds, escalated). Escalation happens only for a
        subscription provider and only once a second distinct model of it
        has been limited inside the window.
        """
        seconds = (
            DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS
            if retry_after is None
            else max(retry_after, 1.0)
        )
        now = time.monotonic()
        deadline = now + seconds
        escalated = False
        with self._lock:
            self._until[model] = deadline
            if provider in SUBSCRIPTION_PROVIDERS:
                seen = self._witnessed.setdefault(provider, {})
                seen[model] = deadline
                for stale in [m for m, d in seen.items() if d <= now]:
                    del seen[stale]
                if len(seen) >= PROVIDER_COOLDOWN_MIN_MODELS:
                    previous = self._provider_until.get(provider)
                    escalated = previous is None or previous <= now
                    self._provider_until[provider] = max(
                        deadline, previous or 0.0
                    )
        return seconds, escalated

    def active(self, model: str, provider: str | None = None) -> bool:
        now = time.monotonic()
        with self._lock:
            if provider is not None:
                provider_deadline = self._provider_until.get(provider)
                if provider_deadline is not None:
                    if provider_deadline > now:
                        return True
                    del self._provider_until[provider]
                    self._witnessed.pop(provider, None)
            deadline = self._until.get(model)
            if deadline is None:
                return False
            if deadline <= now:
                del self._until[model]
                return False
            return True

    def remaining(self, model: str, provider: str | None = None) -> float | None:
        """Seconds until ``model`` is worth trying again, if it is cooling.

        A skip decided from local cooldown state has no upstream header
        behind it, so this is the only honest retry hint available for that
        case. The provider deadline wins when it is the later of the two,
        because a cooling subscription gates the model regardless.
        """
        now = time.monotonic()
        with self._lock:
            deadlines = [
                deadline
                for deadline in (
                    self._until.get(model),
                    self._provider_until.get(provider) if provider else None,
                )
                if deadline is not None and deadline > now
            ]
        if not deadlines:
            return None
        return max(deadlines) - now

    def active_models(self) -> list[str]:
        now = time.monotonic()
        with self._lock:
            expired = [m for m, d in self._until.items() if d <= now]
            for m in expired:
                del self._until[m]
            return sorted(self._until)

    def active_providers(self) -> list[str]:
        now = time.monotonic()
        with self._lock:
            expired = [p for p, d in self._provider_until.items() if d <= now]
            for p in expired:
                del self._provider_until[p]
                self._witnessed.pop(p, None)
            return sorted(self._provider_until)

    def diagnostic_entries(
        self, routes: dict[str, str]
    ) -> list[dict[str, object]]:
        """Return one bounded, metadata-only row for each live deadline."""
        now = time.monotonic()
        with self._lock:
            expired_models = [m for m, d in self._until.items() if d <= now]
            for model in expired_models:
                del self._until[model]
            expired_providers = [
                provider
                for provider, deadline in self._provider_until.items()
                if deadline <= now
            ]
            for provider in expired_providers:
                del self._provider_until[provider]
                self._witnessed.pop(provider, None)
            entries: list[dict[str, object]] = [
                {
                    "scope": "model",
                    "model": model,
                    "provider": routes[model],
                    "remaining_seconds": max(
                        1, int(self._until[model] - now + 0.5)
                    ),
                }
                for model in sorted(self._until)
                if model in routes
            ]
            entries.extend(
                {
                    "scope": "provider",
                    "provider": provider,
                    "remaining_seconds": max(
                        1, int(self._provider_until[provider] - now + 0.5)
                    ),
                }
                for provider in sorted(self._provider_until)
            )
        return entries


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


_OPENMODEL_ADAPTER: Any = None
_OPENMODEL_ADAPTER_LOCK = threading.Lock()


def load_openmodel_adapter():
    """Load the managed pure protocol adapter without expanding its authority."""

    global _OPENMODEL_ADAPTER
    with _OPENMODEL_ADAPTER_LOCK:
        if _OPENMODEL_ADAPTER is not None:
            return _OPENMODEL_ADAPTER
        path = Path(__file__).with_name("airlock_openmodel_adapter.py")
        try:
            if path.is_symlink() or not path.is_file():
                raise RouterError("managed open-model adapter is unavailable")
            spec = importlib.util.spec_from_file_location(
                "airlock_router_openmodel_adapter", path
            )
            if spec is None or spec.loader is None:
                raise RouterError("managed open-model adapter is unavailable")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
        except RouterError:
            raise
        except Exception as exc:
            raise RouterError("managed open-model adapter is unavailable") from exc
        _OPENMODEL_ADAPTER = module
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


class OpenRouterEffortClamp(NamedTuple):
    requested: str
    forwarded: str
    ceiling: str


class OpenRouterRoute(NamedTuple):
    endpoint_provider: str
    provider_name: str
    provider_slug: str
    quantization: str
    canonical_slug: str
    effort_ceiling: str = "high"


class OpenModelEndpoint(NamedTuple):
    """One immutable loopback transport endpoint from the signed snapshot."""

    base_url: str
    trust: str
    protocol: str
    auth: str
    max_concurrency: int

    @property
    def port(self) -> int:
        match = OPENMODEL_BASE_URL_PATTERN.fullmatch(self.base_url)
        if match is None:  # Construction is validated by RouterConfig.
            raise RouterError("open-model endpoint configuration is invalid")
        return int(match.group(1))


class OpenModelRoute(NamedTuple):
    """Immutable protocol capabilities and private binding for one wire model."""

    endpoint: str
    upstream_model: str
    accepted_response_models: tuple[str, ...]
    context_window: int
    max_output_tokens: int
    streaming: bool
    tools: str
    tool_choice: tuple[str, ...]


def _valid_openmodel_identity(value: object) -> bool:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > MAX_OPENMODEL_IDENTITY_CHARS
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= MAX_OPENMODEL_IDENTITY_BYTES
    except UnicodeEncodeError:
        return False


def _validate_openmodel_endpoint(value: object) -> OpenModelEndpoint:
    if type(value) is not OpenModelEndpoint:
        raise RouterError("open-model endpoint configuration is invalid")
    match = (
        OPENMODEL_BASE_URL_PATTERN.fullmatch(value.base_url)
        if type(value.base_url) is str
        else None
    )
    if (
        match is None
        or int(match.group(1)) > 65535
        or value.trust != "loopback"
        or value.protocol != "openai-chat-completions-v1"
        or value.auth != "none"
        or type(value.max_concurrency) is not int
        or not 1 <= value.max_concurrency <= 64
    ):
        raise RouterError("open-model endpoint configuration is invalid")
    return OpenModelEndpoint(
        base_url=value.base_url,
        trust=value.trust,
        protocol=value.protocol,
        auth=value.auth,
        max_concurrency=value.max_concurrency,
    )


def _validate_openmodel_route(
    model: object,
    value: object,
    endpoint_ids: set[str],
    adapter: Any,
) -> tuple[OpenModelRoute, Any]:
    if (
        type(model) is not str
        or not model.startswith("openmodel/")
        or len(model) > len("openmodel/") + 40
        or OPENMODEL_NAME_PATTERN.fullmatch(model.removeprefix("openmodel/")) is None
        or type(value) is not OpenModelRoute
        or type(value.endpoint) is not str
        or value.endpoint not in endpoint_ids
        or not _valid_openmodel_identity(value.upstream_model)
    ):
        raise RouterError("open-model route configuration is invalid")
    if (
        type(value.accepted_response_models) not in {tuple, list}
        or not 1 <= len(value.accepted_response_models) <= 16
        or type(value.tool_choice) not in {tuple, list}
        or len(value.tool_choice) > 4
    ):
        # Check concrete bounded containers before copying them. In particular,
        # never tuple() a caller-supplied generator during startup validation.
        raise RouterError("open-model route configuration is invalid")
    accepted = tuple(value.accepted_response_models)
    choices = tuple(value.tool_choice)
    if (
        any(not _valid_openmodel_identity(identity) for identity in accepted)
        or type(value.context_window) is not int
        or not 1000 <= value.context_window <= 10_000_000
        or type(value.max_output_tokens) is not int
        or not 1 <= value.max_output_tokens <= value.context_window
        or type(value.streaming) is not bool
        or type(value.tools) is not str
        or value.tools not in {"none", "single", "parallel"}
        or any(type(choice) is not str for choice in choices)
        or any(choice not in {"auto", "named", "none", "required"} for choice in choices)
        or (value.tools == "none" and bool(choices))
        or (value.tools != "none" and "auto" not in choices)
    ):
        raise RouterError("open-model route configuration is invalid")
    if (
        accepted != tuple(sorted(accepted))
        or len(accepted) != len(set(accepted))
        or choices != tuple(sorted(choices))
        or len(choices) != len(set(choices))
    ):
        raise RouterError("open-model route configuration is invalid")
    normalized = OpenModelRoute(
        endpoint=value.endpoint,
        upstream_model=value.upstream_model,
        accepted_response_models=accepted,
        context_window=value.context_window,
        max_output_tokens=value.max_output_tokens,
        streaming=value.streaming,
        tools=value.tools,
        tool_choice=choices,
    )
    try:
        capabilities = adapter.OpenModelCapabilities(
            context_window=normalized.context_window,
            max_output_tokens=normalized.max_output_tokens,
            supports_streaming=normalized.streaming,
            tool_support=normalized.tools,
            declared_tool_choice_modes=normalized.tool_choice,
        )
    except Exception as exc:
        raise RouterError("open-model route configuration is invalid") from exc
    return normalized, capabilities


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
        openmodel_endpoints: dict[str, OpenModelEndpoint] | None = None,
        openmodel: dict[str, OpenModelRoute] | None = None,
        failover: dict[str, tuple[str, ...]] | None = None,
        profile: str = "hybrid",
        root_model: str | None = None,
        context_windows: dict[str, int] | None = None,
        route_categories: dict[str, str] | None = None,
        effort_ceilings: dict[str, str] | None = None,
        compactors: dict[str, str] | None = None,
        overflow_shrink: str = "auto",
        anthropic_rate_limit: str = "native",
        background_model: str | None = None,
    ) -> None:
        if not routes or any(
            not isinstance(model, str)
            or not model
            or provider not in {
                "openai", "anthropic", "grok", "openrouter", "openmodel"
            }
            for model, provider in routes.items()
        ):
            raise RouterError("router model routes are invalid")
        if (
            not isinstance(profile, str)
            or not 1 <= len(profile) <= 64
            or re.fullmatch(
                r"[a-z0-9]+(?:[._-][a-z0-9]+)*", profile, re.ASCII
            ) is None
        ):
            raise RouterError("router profile is invalid")
        if root_model is None:
            root_model = next(iter(routes))
        if root_model not in routes:
            raise RouterError("router root model is invalid")
        failover_map: dict[str, tuple[str, ...]] = {}
        for source, peers in dict(failover or {}).items():
            peer_list = tuple(peers)
            if (
                source not in routes
                or routes[source] == "openmodel"
                or not peer_list
                or len(peer_list) > MAX_FAILOVER_PEERS_PER_MODEL
                or len(set(peer_list)) != len(peer_list)
                or any(
                    peer == source
                    or peer not in routes
                    or routes.get(peer) == "openmodel"
                    for peer in peer_list
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
        if any(
            not isinstance(route, OpenRouterRoute)
            or route.effort_ceiling not in OPENROUTER_EFFORT_LEVELS
            for route in metadata.values()
        ):
            raise RouterError("OpenRouter route metadata is invalid")
        if openrouter_routes and openrouter_key is None:
            raise RouterError("OpenRouter credential is missing")

        openmodel_route_ids = {
            model for model, provider in routes.items() if provider == "openmodel"
        }
        if openmodel_endpoints is None:
            raw_openmodel_endpoints: dict[str, OpenModelEndpoint] = {}
        elif (
            type(openmodel_endpoints) is not dict
            or len(openmodel_endpoints) > MAX_OPENMODEL_ENDPOINTS
        ):
            raise RouterError("open-model configuration is invalid")
        else:
            raw_openmodel_endpoints = dict(openmodel_endpoints)
        if openmodel is None:
            raw_openmodel_routes: dict[str, OpenModelRoute] = {}
        elif type(openmodel) is not dict or len(openmodel) > MAX_OPENMODEL_ROUTES:
            raise RouterError("open-model configuration is invalid")
        else:
            raw_openmodel_routes = dict(openmodel)
        if set(raw_openmodel_routes) != openmodel_route_ids:
            raise RouterError("open-model routes and metadata do not agree")
        adapter = load_openmodel_adapter() if openmodel_route_ids else None
        normalized_endpoints: dict[str, OpenModelEndpoint] = {}
        endpoint_urls: set[str] = set()
        for endpoint_id, endpoint in raw_openmodel_endpoints.items():
            if (
                type(endpoint_id) is not str
                or not 1 <= len(endpoint_id) <= 40
                or OPENMODEL_NAME_PATTERN.fullmatch(endpoint_id) is None
            ):
                raise RouterError("open-model endpoint configuration is invalid")
            normalized = _validate_openmodel_endpoint(endpoint)
            if normalized.base_url in endpoint_urls:
                raise RouterError("open-model endpoint configuration is invalid")
            endpoint_urls.add(normalized.base_url)
            normalized_endpoints[endpoint_id] = normalized
        normalized_openmodel: dict[str, OpenModelRoute] = {}
        openmodel_capabilities: dict[str, Any] = {}
        request_bindings: set[tuple[str, str]] = set()
        response_bindings: set[tuple[str, str]] = set()
        referenced_endpoints: set[str] = set()
        for openmodel_id, route in raw_openmodel_routes.items():
            normalized, capabilities = _validate_openmodel_route(
                openmodel_id, route, set(normalized_endpoints), adapter
            )
            request_binding = (normalized.endpoint, normalized.upstream_model)
            accepted_bindings = {
                (normalized.endpoint, identity)
                for identity in normalized.accepted_response_models
            }
            if (
                request_binding in request_bindings
                or accepted_bindings & response_bindings
            ):
                raise RouterError("open-model route configuration is invalid")
            request_bindings.add(request_binding)
            response_bindings.update(accepted_bindings)
            referenced_endpoints.add(normalized.endpoint)
            normalized_openmodel[openmodel_id] = normalized
            openmodel_capabilities[openmodel_id] = capabilities
        if set(normalized_endpoints) != referenced_endpoints:
            raise RouterError("open-model endpoint configuration is invalid")

        self.routes = dict(routes)
        self.profile = profile
        self.root_model = root_model
        self.root_provider = routes[root_model]
        self.failover = failover_map
        self.openrouter_routes = metadata
        self.openrouter_key = bytes(openrouter_key) if openrouter_key is not None else None
        self.openmodel_endpoints = MappingProxyType(normalized_endpoints)
        self.openmodel_routes = MappingProxyType(normalized_openmodel)
        self.openmodel_capabilities = MappingProxyType(openmodel_capabilities)
        self.openmodel_adapter = adapter
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
        windows: dict[str, int] = dict(context_windows or {})
        for model, window in windows.items():
            if (
                model not in routes
                or type(window) is not int
                or isinstance(window, bool)
                or not 1000 <= window <= 10_000_000
            ):
                raise RouterError("context windows are invalid")
        compactors_map: dict[str, str] = dict(compactors or {})
        for provider, model in compactors_map.items():
            if (
                provider not in {"anthropic", "openai", "grok"}
                or model not in routes
                or routes[model] != provider
            ):
                raise RouterError("compactor assignments are invalid")
        if overflow_shrink not in {"auto", "truncate", "summarize", "off"}:
            raise RouterError("overflow shrink mode is invalid")
        if route_categories is not None and type(route_categories) is not dict:
            raise RouterError("route categories are invalid")
        raw_categories = dict(route_categories or {})
        if any(
            model not in routes or category not in ROUTE_CATEGORIES
            for model, category in raw_categories.items()
        ):
            raise RouterError("route categories are invalid")
        if effort_ceilings is not None and type(effort_ceilings) is not dict:
            raise RouterError("effort ceilings are invalid")
        raw_effort_ceilings = dict(effort_ceilings or {})
        if any(
            model not in routes or ceiling not in ROUTE_EFFORT_LEVELS
            for model, ceiling in raw_effort_ceilings.items()
        ):
            raise RouterError("effort ceilings are invalid")
        self.context_windows = windows
        self.route_categories = {
            model: raw_categories.get(model, "unknown")
            for model in routes
        }
        self.effort_ceilings = raw_effort_ceilings
        self.compactors = compactors_map
        self.overflow_shrink = overflow_shrink
        # Claude Code handles Anthropic's own 429 natively: it recognizes a
        # subscription limit, reports the reset, and can resume afterwards.
        # Converting that into a handoff spends another provider to dodge a
        # wait the client already knows how to sit out, and replacing the
        # response hides the fields that handling reads. "native" therefore
        # forwards an Anthropic 429 untouched. 529 and 402 still hand off,
        # because an overloaded server or a spent balance is not a wait.
        if anthropic_rate_limit not in {"native", "handoff"}:
            raise RouterError("anthropic rate limit mode is invalid")
        self.anthropic_rate_limit = anthropic_rate_limit
        # No chains at all means this session does no handoff, either because
        # the policy switched it off or because nothing has a peer. Either
        # way there is nothing to walk, so a limit is left as the provider
        # sent it rather than replaced with a local stand-in.
        self.failover_disabled = not self.failover
        # Claude Code runs compaction and other background steps on a Haiku
        # model ID directly, ignoring the family slot Airlock seats for it. A
        # session that does not enable Haiku as a worker has no such route, so
        # those requests were refused and compaction died with nothing to show
        # for it. This is the model such a request is served by instead: the
        # same seat Claude Code was already told to use.
        # Local open-model routes are intentionally request-only. They must not
        # become Claude Code's implicit background/compaction substitute, which
        # would silently send unrelated work to an unsigned local service.
        if background_model is not None and type(background_model) is not str:
            raise RouterError("router background model is invalid")
        if (
            background_model in self.routes
            and self.routes[background_model] == "openmodel"
        ):
            raise RouterError("open-model routes cannot be background substitutes")
        self.background_model = (
            background_model
            if background_model in self.routes
            else None
        )


class RouterServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        config: RouterConfig,
        *,
        owner_pid: int | None = None,
        workdir: str | None = None,
    ) -> None:
        super().__init__(address, RouterHandler)
        self.config = config
        self.started_at = utc_timestamp()
        self.owner_pid = os.getppid() if owner_pid is None else owner_pid
        self.workdir = workdir
        self.last_request_at: str | None = None
        self.routing_lock = threading.Lock()
        self.pinned_model: str | None = None
        self.context_observation: dict[str, object] | None = None
        self.openmodel_endpoint_slots = MappingProxyType({
            endpoint_id: threading.BoundedSemaphore(endpoint.max_concurrency)
            for endpoint_id, endpoint in config.openmodel_endpoints.items()
        })
        self.openmodel_endpoint_waiters = MappingProxyType({
            endpoint_id: threading.BoundedSemaphore(
                MAX_OPENMODEL_WAITERS_PER_ENDPOINT
            )
            for endpoint_id in config.openmodel_endpoints
        })
        self.instance_id = secrets.token_hex(8)
        # This capability is written only to the private registry. It must not
        # enter diagnostics, the ready marker, request forwarding, or errors.
        self.control_token = secrets.token_urlsafe(32)
        self.rate_limits = RateLimitCooldowns()
        self.diagnostics: deque[dict[str, object]] = deque(
            maxlen=MAX_DIAGNOSTIC_EVENTS
        )
        self.usage_summary: dict[tuple[str, str], dict[str, object]] = {}
        # Last observed per-response token totals, used to estimate an
        # overflowing conversation's size when the upstream error body does
        # not state exact counts.
        self.last_usage: dict[tuple[str, str], dict[str, int]] = {}
        self.shrink_cache: "OrderedDict[bytes, str]" = OrderedDict()
        self.diagnostics_lock = threading.Lock()
        self.record_diagnostic({
            "kind": "session_root_selected",
            "model": config.root_model,
            "provider": config.root_provider,
        })

    def snapshot_pinned_model(self) -> str | None:
        """Return the pin one request must use for its complete resolution."""
        with self.routing_lock:
            return self.pinned_model

    def pin_route(self, model: str) -> dict[str, object]:
        """Pin future requests after one short, current-state validation."""
        with self.routing_lock:
            provider = self.config.routes.get(model)
            if provider is None:
                raise ControlError(
                    400,
                    "model_not_enabled",
                    "Model is not enabled for this session",
                )
            window = self.config.context_windows.get(model)
            with self.diagnostics_lock:
                context = (
                    None
                    if self.context_observation is None
                    else dict(self.context_observation)
                )
            observed = context.get("input_tokens") if context is not None else None
            known_fit = type(observed) is int and type(window) is int
            previous = self.pinned_model
            if previous == model:
                # A response may be lost after the transition. Retrying the
                # same target must confirm convergence even if the route began
                # cooling or the conversation grew in the meantime.
                context_check = (
                    "too_large"
                    if known_fit and observed > window
                    else "fits" if known_fit else "unknown"
                )
                return {
                    "ok": True,
                    "action": "pin",
                    "instance_id": self.instance_id,
                    "changed": False,
                    "previous_pinned_model": previous,
                    "pinned_model": model,
                    "route": {
                        "model": model,
                        "provider": provider,
                        "context_window": window,
                        "context_check": context_check,
                    },
                }
            if self.rate_limits.active(model, provider):
                raise ControlError(
                    409,
                    "route_cooling",
                    "The selected route is cooling",
                )
            if known_fit and observed > window:
                raise ControlError(
                    409,
                    "context_too_large",
                    "The conversation does not fit the selected route",
                )
            context_check = "fits" if known_fit else "unknown"
            self.pinned_model = model
            self.record_diagnostic({
                "kind": "session_model_pinned",
                "model": model,
                "provider": provider,
            })
            return {
                "ok": True,
                "action": "pin",
                "instance_id": self.instance_id,
                "changed": True,
                "previous_pinned_model": previous,
                "pinned_model": model,
                "route": {
                    "model": model,
                    "provider": provider,
                    "context_window": window,
                    "context_check": context_check,
                },
            }

    def unpin_route(self) -> dict[str, object]:
        """Restore ordinary per-request routing without consulting cooldowns."""
        with self.routing_lock:
            previous = self.pinned_model
            changed = previous is not None
            self.pinned_model = None
            if changed:
                assert previous is not None
                self.record_diagnostic({
                    "kind": "session_model_unpinned",
                    "model": previous,
                    "provider": self.config.routes[previous],
                })
            return {
                "ok": True,
                "action": "unpin",
                "instance_id": self.instance_id,
                "changed": changed,
                "previous_pinned_model": previous,
                "pinned_model": None,
                "route": None,
            }

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

    def _diagnostic_value(self, value: object) -> object:
        """Remove an accidentally echoed capability from bounded events."""
        if isinstance(value, str):
            return value.replace(self.control_token, "[redacted]")
        if isinstance(value, dict):
            return {
                self._diagnostic_value(key): self._diagnostic_value(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._diagnostic_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._diagnostic_value(item) for item in value)
        return value

    def record_diagnostic(
        self,
        event: dict[str, object],
        *,
        observe_context: bool = True,
    ) -> None:
        stored = self._diagnostic_value(event)
        assert isinstance(stored, dict)
        stored["timestamp"] = utc_timestamp()
        with self.diagnostics_lock:
            self.diagnostics.append(stored)
            provider = stored.get("provider")
            model = stored.get("model")
            status = stored.get("status")
            outcome = stored.get("outcome")
            # Action events can name a provider and model, but only request
            # events belong in cumulative usage totals.
            if (
                not isinstance(provider, str)
                or not isinstance(model, str)
                or isinstance(status, bool)
                or not isinstance(status, int)
                or not isinstance(outcome, str)
            ):
                return
            self.last_request_at = str(stored["timestamp"])
            if outcome == "completed" and observe_context:
                usage = stored.get("usage")
                input_tokens = 0
                observed_input = False
                if isinstance(usage, dict):
                    fields = ["input_tokens"]
                    # Only native Anthropic usage reports cache reads and
                    # creations outside input_tokens, so only there do they
                    # add up. Every OpenAI-shaped upstream (the proxy's
                    # OpenAI, Grok and OpenRouter routes, and the open-model
                    # adapter) maps prompt_tokens to input_tokens whole and
                    # reports its cached tokens as a subset of that number;
                    # adding them there counts the cache twice.
                    if provider == "anthropic":
                        fields.extend((
                            "cache_read_input_tokens",
                            "cache_creation_input_tokens",
                        ))
                    for field in fields:
                        value = usage.get(field)
                        if (
                            isinstance(value, int)
                            and not isinstance(value, bool)
                            and value >= 0
                        ):
                            observed_input = True
                            input_tokens += value
                self.context_observation = (
                    {
                        "model": model,
                        "input_tokens": input_tokens,
                        "observed_at": str(stored["timestamp"]),
                    }
                    if observed_input
                    else None
                )
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
            if outcome == "completed":
                summary["completed"] += 1
            else:
                summary["errors"] += 1
            usage = stored.get("usage")
            if isinstance(usage, dict):
                summary["usage_events"] += 1
                for field in USAGE_FIELDS:
                    value = usage.get(field)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        summary[field] += value
                self.last_usage[key] = {
                    field: int(usage[field])
                    for field in USAGE_FIELDS
                    if isinstance(usage.get(field), int)
                    and not isinstance(usage.get(field), bool)
                }

    def diagnostic_report(self) -> dict[str, object]:
        # Controls use this same routing-then-diagnostics order. Taking both
        # briefly keeps a pin and its transition event in one coherent view.
        with self.routing_lock:
            pinned_model = self.pinned_model
            with self.diagnostics_lock:
                last_request_at = self.last_request_at
                context = (
                    None
                    if self.context_observation is None
                    else dict(self.context_observation)
                )
                events = [dict(event) for event in self.diagnostics]
                summary = [
                    dict(self.usage_summary[key])
                    for key in sorted(self.usage_summary)
                ]
        return {
            "instance_id": self.instance_id,
            "profile": self.config.profile,
            "root_model": self.config.root_model,
            "root_provider": self.config.root_provider,
            "started_at": self.started_at,
            "owner_pid": self.owner_pid,
            "workdir": self.workdir,
            "last_request_at": last_request_at,
            "pinned_model": pinned_model,
            "routes": [
                {
                    "model": model,
                    "provider": self.config.routes[model],
                    "category": self.config.route_categories[model],
                    "context_window": self.config.context_windows.get(model),
                    "effort_ceiling": self.config.effort_ceilings.get(model),
                    "metered": self.config.route_categories[model] in {
                        "extra", "metered"
                    },
                }
                for model in sorted(self.config.routes)
            ],
            "cooldowns": self.rate_limits.diagnostic_entries(
                self.config.routes
            ),
            "chains": {
                model: list(self.config.failover[model])
                for model in sorted(self.config.failover)
            },
            "context": context,
            "rate_limit_cooldowns": self.rate_limits.active_models(),
            "rate_limit_provider_cooldowns": (
                self.rate_limits.active_providers()
            ),
            "events": events,
            "summary": summary,
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

    @staticmethod
    def _is_control_path(path: str) -> bool:
        return path == "/control" or path.startswith("/control/")

    def _single_header(self, name: str) -> str | None:
        values = self.headers.get_all(name) or []
        return values[0] if len(values) == 1 else None

    def _control_authorized(self) -> bool:
        token = self._single_header("X-Airlock-Control-Token")
        origins = self.headers.get_all("Origin") or []
        if token is None or len(origins) > 1:
            return False
        if origins and not control_origin_allowed(origins[0]):
            return False
        try:
            supplied = token.encode("utf-8")
        except UnicodeError:
            return False
        return hmac.compare_digest(
            supplied, self.router.control_token.encode("ascii")
        )

    def handle_control(self, path: str) -> None:
        if not self._control_authorized():
            self.send_control_error(
                403, "forbidden", "Control request is forbidden"
            )
            return
        content_type = self._single_header("Content-Type")
        if content_type is None or not control_json_content_type_allowed(
            content_type
        ):
            self.send_control_error(
                400, "invalid_request", "Control request is invalid"
            )
            return
        try:
            body = self.read_control_body()
            payload = control_request_payload(body)
            if path == "/control/pin":
                if set(payload) != {"model"} or type(payload["model"]) is not str:
                    raise ControlError(
                        400, "invalid_request", "Control request is invalid"
                    )
                result = self.router.pin_route(payload["model"])
            else:
                if payload:
                    raise ControlError(
                        400, "invalid_request", "Control request is invalid"
                    )
                result = self.router.unpin_route()
        except ControlError as exc:
            self.send_control_error(exc.status, exc.kind, str(exc))
            return
        self.send_control_json(200, result)

    def do_HEAD(self) -> None:
        path = urlsplit(self.path).path
        if self._is_control_path(path):
            self.send_control_error(404, "not_found", "Route not found")
            return
        if path != "/":
            self.send_error_response(404, "not_found", "Route not found")
            return
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()

    def _unsupported_method(self) -> None:
        path = urlsplit(self.path).path
        if self._is_control_path(path):
            self.send_control_error(404, "not_found", "Route not found")
            return
        self.send_error(501, "Unsupported method")

    do_CONNECT = _unsupported_method
    do_DELETE = _unsupported_method
    do_OPTIONS = _unsupported_method
    do_PATCH = _unsupported_method
    do_PUT = _unsupported_method
    do_TRACE = _unsupported_method

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if self._is_control_path(path):
            self.send_control_error(404, "not_found", "Route not found")
            return
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
        target = urlsplit(self.path)
        path = target.path
        if self._is_control_path(path):
            if path not in CONTROL_ENDPOINTS or target.query or target.fragment:
                self.send_control_error(404, "not_found", "Route not found")
            else:
                self.handle_control(path)
            return
        if path not in ALLOWED_POST_PATHS:
            self.send_error_response(404, "not_found", "Route not found")
            return
        started = time.monotonic()
        body_size = 0
        model: str | None = None
        provider: str | None = None
        attempt_model: str | None = None
        visited: set[str] = set()
        considered: set[str] = set()
        # Initialized before the try because the terminal handlers consult it:
        # a request rejected during validation never reaches the attempt loop,
        # and no stream has been committed in that case.
        shrunk_committed = False
        self._context_observation_eligible = False
        try:
            body = self.read_body()
            body, model, provider = self.resolve_request_route(body)
            original_body = body
            body_size = len(body)
            if provider == "openmodel":
                if path != "/v1/messages":
                    raise InvalidRequestError(
                        "Open-model routes support only the Messages operation"
                    )
                translated_body, streaming, response_constraints = (
                    self.prepare_openmodel_request(body, model)
                )
                status, response_bytes, outcome, usage = self.forward_openmodel(
                    model,
                    translated_body,
                    streaming=streaming,
                    response_constraints=response_constraints,
                )
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
                return
            if provider == "openrouter" and path != "/v1/messages":
                raise InvalidRequestError(
                    "OpenRouter supports only the Messages operation in this release"
                )
            if provider == "openrouter":
                body, stripped_tools, effort_clamp = prepare_openrouter_request(
                    body,
                    model,
                    self.router.config.openrouter_routes[model],
                )
                if stripped_tools:
                    self.router.record_diagnostic({
                        "kind": "openrouter_server_tools_stripped",
                        "model": model,
                        "removed_count": len(stripped_tools),
                    })
                if effort_clamp:
                    self.record_openrouter_effort_clamp(model, effort_clamp)
            attempt_model = model
            attempt_provider = provider
            working_body = body
            visited = {model}
            considered = {model}
            hops = 0
            failover_from: str | None = None
            failover_reason: str | None = None
            shrunk_attempted = False
            shrunk_committed = False
            while True:
                # A model still cooling down from an earlier 429 is skipped
                # outright, so repeated background calls pay no doomed round
                # trip once one limit has been seen.
                if self.router.rate_limits.active(attempt_model, attempt_provider):
                    if shrunk_committed:
                        raise RateLimitedError(429, None)
                    self.record_cooldown_skip(attempt_model)
                    skipped = (
                        self.next_failover_model(
                            model, visited, considered
                        )
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
                        # No upstream answered, so the only honest retry hint
                        # is how long the local cooldown still has to run.
                        raise RateLimitedError(
                            429,
                            self.router.rate_limits.remaining(
                                attempt_model, attempt_provider
                            ),
                        )
                    self.record_failover_attempt(attempt_model, skipped)
                    working_body = self.retarget_request_body(
                        original_body, skipped, model
                    )
                    visited.add(skipped)
                    failover_from = failover_from or model
                    failover_reason = failover_reason or "rate_limit"
                    attempt_model = skipped
                    attempt_provider = self.router.config.routes[skipped]
                    hops += 1
                    continue
                try:
                    status, response_bytes, outcome, usage = self.forward(
                        attempt_provider,
                        attempt_model,
                        working_body,
                        skip_response_headers=shrunk_committed,
                    )
                    break
                except RateLimitedError as exc:
                    if shrunk_committed:
                        raise
                    _seconds, escalated = self.router.rate_limits.mark(
                        attempt_model, exc.retry_after, attempt_provider
                    )
                    if escalated:
                        # A second model of this subscription failed, so
                        # the pool itself is spent: stop probing its peers.
                        self.router.record_diagnostic({
                            "kind": "rate_limit_provider_cooldown",
                            "provider": attempt_provider,
                            "model": attempt_model,
                        })
                    self.record_request(
                        attempt_provider,
                        attempt_model,
                        exc.status,
                        body_size,
                        0,
                        started,
                        "upstream_rate_limited",
                    )
                    self.record_sanitized_error(
                        attempt_provider, attempt_model, exc.status
                    )
                    nxt = (
                        self.next_failover_model(
                            model, visited, considered
                        )
                        if hops < MAX_FAILOVER_HOPS
                        else None
                    )
                    if nxt is None:
                        raise
                    self.record_failover_attempt(attempt_model, nxt)
                    working_body = self.retarget_request_body(
                        original_body, nxt, model
                    )
                    visited.add(nxt)
                    failover_from = failover_from or model
                    failover_reason = failover_reason or "rate_limit"
                    attempt_model = nxt
                    attempt_provider = self.router.config.routes[nxt]
                    hops += 1
                except ContextOverflowError as exc:
                    if shrunk_committed:
                        raise
                    estimate = self._overflow_estimate(
                        attempt_provider, attempt_model, exc
                    )
                    overflow_event: dict[str, object] = {
                        "kind": "upstream_context_overflow",
                        "provider": attempt_provider,
                        "model": attempt_model,
                        "status": exc.status,
                    }
                    if exc.prompt_tokens is not None:
                        overflow_event["prompt_tokens"] = exc.prompt_tokens
                    if exc.limit_tokens is not None:
                        overflow_event["limit_tokens"] = exc.limit_tokens
                    self.router.record_diagnostic(overflow_event)
                    self.record_request(
                        attempt_provider,
                        attempt_model,
                        exc.status,
                        body_size,
                        0,
                        started,
                        "upstream_context_overflow",
                    )
                    nxt = (
                        self.next_failover_model(
                            model,
                            visited,
                            considered,
                            min_window=estimate,
                        )
                        if hops < MAX_FAILOVER_HOPS
                        else None
                    )
                    source_model = attempt_model
                    if nxt is None and not shrunk_attempted:
                        # Nothing left to walk toward: one chance to shrink
                        # the conversation and retry the best candidate.
                        target = self._shrink_target(
                            model,
                            visited,
                            considered,
                            estimate,
                            overflowed_model=source_model,
                        )
                        if target is not None:
                            shrunk_attempted = True
                            shrunk_provider, shrunk_body, committed = (
                                self._prepare_shrunk_request(
                                    original_body,
                                    provider,
                                    model,
                                    target,
                                    estimate,
                                )
                            )
                            if not shrunk_body and not committed:
                                raise
                            if not shrunk_body and committed:
                                # The failure was already delivered inside
                                # the committed SSE stream.
                                return
                            shrunk_committed = committed
                            working_body = shrunk_body
                            visited.add(target)
                            considered.add(target)
                            failover_from = failover_from or model
                            failover_reason = "overflow"
                            attempt_model = target
                            attempt_provider = shrunk_provider
                            hops += 1
                            self.router.record_diagnostic({
                                "kind": "failover_overflow_attempted",
                                "from_model": source_model,
                                "to_model": target,
                                "shrunk": True,
                            })
                            continue
                    if nxt is None:
                        raise
                    self.router.record_diagnostic({
                        "kind": "failover_overflow_attempted",
                        "from_model": source_model,
                        "to_model": nxt,
                    })
                    working_body = self.retarget_request_body(
                        original_body, nxt, model
                    )
                    visited.add(nxt)
                    failover_from = failover_from or model
                    failover_reason = "overflow"
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
                observe_context=(
                    self._context_observation_eligible and not shrunk_attempted
                ),
            )
            if (
                failover_from is not None
                and 200 <= status < 300
                and outcome == "completed"
            ):
                self.router.record_diagnostic({
                    "kind": (
                        "failover_overflow_succeeded"
                        if failover_reason == "overflow"
                        else "rate_limit_failover_succeeded"
                    ),
                    "from_model": failover_from,
                    "to_model": attempt_model,
                    "hops": hops,
                })
        except RateLimitedError as exc:
            hint = retry_after_seconds(exc.retry_after)
            if provider is not None and model is not None:
                exhausted_event: dict[str, object] = {
                    "kind": "rate_limit_chain_exhausted",
                    "model": model,
                    "last_model": attempt_model or model,
                    "models_considered": max(1, len(considered)),
                }
                if hint is not None:
                    exhausted_event["retry_after"] = hint
                self.router.record_diagnostic(exhausted_event)
                self.record_request(
                    provider, model, 429, body_size, 0, started,
                    "rate_limit_exhausted",
                )
            # considered is seeded with the requested model, so anything above
            # one means real peers existed and were all unusable.
            if len(considered) > 1:
                message = (
                    "Every model in this request's category is rate limited"
                    " right now; retry shortly or switch models."
                )
            else:
                # Nothing was tried after the first model, so blaming the
                # category would be false. A model alone in its category,
                # such as a metered one, always lands here.
                message = (
                    "This model is rate limited and has no failover peer in"
                    " its usage category; retry shortly or switch models."
                )
            if shrunk_committed:
                self._emit_stream_error(429, "rate_limit_error", message)
            else:
                self.safe_error_response(429, "rate_limit_error", message, hint)
        except ContextOverflowError:
            if provider is not None and model is not None:
                self.router.record_diagnostic({
                    "kind": "overflow_chain_exhausted",
                    "model": model,
                    "last_model": attempt_model or model,
                    "models_considered": max(1, len(considered)),
                })
                self.record_request(
                    provider, model, 400, body_size, 0, started,
                    "overflow_exhausted",
                )
            message = (
                "The conversation does not fit any enabled model for this"
                " request; compact the session or enable a larger-window"
                " model."
            )
            if shrunk_committed:
                self._emit_stream_error(
                    400, "invalid_request_error", message
                )
            else:
                self.safe_error_response(
                    400, "invalid_request_error", message
                )
        except InvalidRequestError as exc:
            if shrunk_committed:
                self._emit_stream_error(400, "invalid_request_error", str(exc))
            else:
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
                self.record_sanitized_error(provider, model, status)
                self.record_request(
                    provider, model, status, body_size, 0, started, outcome
                )
            if shrunk_committed:
                self._emit_stream_error(status, error_type, detail)
            else:
                self.safe_error_response(status, error_type, detail)
        except (ConnectionError, OSError, ssl.SSLError, http.client.HTTPException):
            if provider is not None and model is not None:
                self.record_sanitized_error(provider, model, 502)
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
        observe_context: bool | None = None,
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
        if observe_context is None:
            observe_context = self._context_observation_eligible
        self.router.record_diagnostic(
            event, observe_context=observe_context
        )

    def record_openrouter_effort_clamp(
        self, model: str, clamp: OpenRouterEffortClamp
    ) -> None:
        self.router.record_diagnostic({
            "kind": "openrouter_effort_clamped",
            "model": model,
            "requested": clamp.requested,
            "forwarded": clamp.forwarded,
            "ceiling": clamp.ceiling,
        })

    def record_cooldown_skip(self, model: str) -> None:
        self.router.record_diagnostic({
            "kind": "rate_limit_cooldown_skipped",
            "model": model,
            "provider": self.router.config.routes[model],
        })

    def record_failover_attempt(self, source: str, target: str) -> None:
        self.router.record_diagnostic({
            "kind": "rate_limit_failover_attempted",
            "from_model": source,
            "to_model": target,
        })

    def record_sanitized_error(
        self, provider: str, model: str, status: int
    ) -> None:
        self.router.record_diagnostic({
            "kind": "sanitized_error_substituted",
            "provider": provider,
            "model": model,
            "status": status,
        })

    def next_failover_model(
        self,
        source: str,
        visited: set[str],
        considered: set[str] | None = None,
        *,
        min_window: int | None = None,
    ) -> str | None:
        """Return the first healthy peer left in ``source``'s own chain.

        ``source`` is the model the request asked for, not the hop that just
        failed. One request consumes one list, top to bottom, which is what a
        declared chain promises. Walking the failed hop's list instead made
        the second peer come from somewhere the caller never named.

        When ``min_window`` carries an estimated conversation size, peers
        whose known context window cannot hold it are skipped with a
        dedicated diagnostic instead of being handed a doomed round trip.
        Peers without known window metadata are always attempted.
        """
        for peer in self.router.config.failover.get(source, ()):
            if peer not in self.router.config.routes:
                continue
            if self.router.config.routes[peer] == "openmodel":
                continue
            if (
                self.router.config.routes[peer] == "openrouter"
                and urlsplit(self.path).path != "/v1/messages"
            ):
                continue
            if considered is not None:
                considered.add(peer)
            if peer in visited:
                continue
            if (
                min_window is not None
                and peer in self.router.config.context_windows
                and self.router.config.context_windows[peer] < min_window
            ):
                self.record_overflow_skip(source, peer, min_window)
                continue
            if self.router.rate_limits.active(
                peer, self.router.config.routes.get(peer)
            ):
                self.record_cooldown_skip(peer)
                continue
            return peer
        return None

    def record_overflow_skip(
        self, source: str, peer: str, estimated_tokens: int
    ) -> None:
        self.router.record_diagnostic({
            "kind": "failover_overflow_skipped",
            "from_model": source,
            "to_model": peer,
            "estimated_tokens": estimated_tokens,
            "peer_window": self.router.config.context_windows.get(peer),
        })

    def rewrite_request_body(
        self, body: bytes, target_model: str, source_model: str | None = None
    ) -> bytes:
        """Change only the model identity before transport preparation."""
        payload = request_payload(body)
        payload.pop("provider", None)
        payload["model"] = target_model
        if source_model is not None and source_model != target_model:
            payload["system"] = disclose_handoff(
                payload.get("system"), source_model, target_model
            )
        try:
            return json.dumps(
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

    def resolve_request_route(
        self, body: bytes
    ) -> tuple[bytes, str, str]:
        """Resolve one request and snapshot its effective pin exactly once."""
        requested_model = request_model(body)
        resolved_model = requested_model
        provider = self.router.config.routes.get(resolved_model)
        used_background_substitution = False
        if provider is None:
            substitute = self.background_substitute(requested_model)
            if substitute is None:
                # Unknown requests stay invalid even while a pin exists. A pin
                # is an override for enabled request paths, not an allowlist
                # bypass for arbitrary model IDs.
                self.router.record_diagnostic({
                    "kind": "model_not_enabled",
                    "model": requested_model,
                })
                raise InvalidRequestError(
                    "Model is not enabled for this session"
                )
            self.router.record_diagnostic({
                "kind": "background_model_substituted",
                "requested": requested_model,
                "model": substitute,
            })
            resolved_model = substitute
            provider = self.router.config.routes[resolved_model]
            used_background_substitution = True
            body = self.rewrite_request_body(body, resolved_model)

        pinned_model = self.router.snapshot_pinned_model()
        effective_model = pinned_model or resolved_model
        session_foreground_model = pinned_model or self.router.config.root_model
        configured_background = self.router.config.background_model
        directly_requested_background = (
            configured_background is not None
            and requested_model == configured_background
            and requested_model != self.router.config.root_model
        )
        self._context_observation_eligible = (
            not used_background_substitution
            and not directly_requested_background
            and effective_model == session_foreground_model
        )
        if effective_model != resolved_model:
            body = self.rewrite_request_body(
                body, effective_model, resolved_model
            )
        return body, effective_model, self.router.config.routes[effective_model]

    def retarget_request_body(
        self, body: bytes, target_model: str, source_model: str | None = None
    ) -> bytes:
        """Rewrite an already validated request onto another routed model.

        The OpenRouter ``provider`` pin belongs to the route that produced it,
        so it is dropped before the new transport prepares its own. Hopping
        onto an OpenRouter route runs the same preparation every direct
        request to that route would receive.

        The replacement is also told that it is the replacement. Its system
        prompt still describes the model that was asked for, so without this
        it answers as that model and states its identity wrongly when asked.
        """
        cleaned = self.rewrite_request_body(
            body, target_model, source_model
        )
        if self.router.config.routes[target_model] == "openrouter":
            prepared, stripped_tools, effort_clamp = prepare_openrouter_request(
                cleaned,
                target_model,
                self.router.config.openrouter_routes[target_model],
            )
            if stripped_tools:
                self.router.record_diagnostic({
                    "kind": "openrouter_server_tools_stripped",
                    "model": target_model,
                    "removed_count": len(stripped_tools),
                })
            if effort_clamp:
                self.record_openrouter_effort_clamp(target_model, effort_clamp)
            return prepared
        return cleaned

    # Claude Code's own background steps, compaction most visibly, ask for a
    # Haiku model by its exact ID instead of using the family slot Airlock
    # seats. Only that family is substituted: anything else stays refused, so
    # a session can never quietly answer as a model nobody enabled.
    BACKGROUND_SUBSTITUTABLE = re.compile(
        r"claude-haiku-[0-9][0-9A-Za-z.\-]{0,32}(\[1m\])?$", re.ASCII
    )

    def background_substitute(self, model: str) -> str | None:
        """The seated background model to serve an unrouted Haiku request."""
        target = self.router.config.background_model
        if (
            target is None
            or target == model
            or self.router.config.routes.get(target) == "openmodel"
        ):
            return None
        if self.BACKGROUND_SUBSTITUTABLE.fullmatch(model) is None:
            return None
        return target

    def rate_limit_passes_through(
        self, provider: str, status: int, stream_committed: bool
    ) -> bool:
        """Whether this response should reach the client untouched.

        Two cases pass through, and neither may run once a committed shrink
        stream owns the socket, because the client is mid-SSE there and a
        JSON error body would corrupt the stream.

        The first is Anthropic's own 429. Claude Code understands its own
        provider's limits, so replacing that response removes handling that
        already works.

        The second is any Anthropic limit once handoff is switched off. With
        no chain to walk there is nothing to gain by rewriting the answer, so
        the client is left exactly as it would be without Airlock present.

        Other providers are deliberately excluded even then. Their error
        bodies are foreign text arriving at a client that expects Anthropic's
        shape, and reflecting those is what the sanitizer exists to prevent.
        They keep the safe local message, which now carries the upstream's
        Retry-After, so the client still backs off on real timing.
        """
        if stream_committed or provider != "anthropic":
            return False
        if self.router.config.failover_disabled:
            return status in RATE_LIMIT_STATUS_CODES
        return (
            status == 429
            and self.router.config.anthropic_rate_limit == "native"
        )

    def safe_error_response(
        self, status: int, kind: str, message: str, retry_after: int | None = None
    ) -> None:
        try:
            self.send_error_response(status, kind, message, retry_after)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            self.close_connection = True

    # ------------------------------------------------------------------
    # Overflow-aware handoff. When a classified context overflow survives
    # the failover walk (no peer fits, or every fitting peer also
    # overflowed), the conversation is compressed once and retried on the
    # best remaining candidate: chunked compaction through the destination
    # provider's own economy worker when one is enabled, else pairing-aware
    # truncation. Every failure cascades downward to the honest terminal
    # error; nothing ever loops.

    def _overflow_estimate(
        self,
        provider: str,
        model: str,
        exc: ContextOverflowError | None,
    ) -> int | None:
        """Best available estimate of the conversation's prompt size."""
        if exc is not None and exc.prompt_tokens:
            return exc.prompt_tokens
        last = self.router.last_usage.get((provider, model))
        if last:
            total = last.get("input_tokens", 0)
            # Same rule as the context observation: cache counts add to the
            # prompt only on native Anthropic usage. Elsewhere input_tokens
            # already contains them.
            if provider == "anthropic":
                total += (
                    last.get("cache_creation_input_tokens", 0)
                    + last.get("cache_read_input_tokens", 0)
                )
            if total > 0:
                return total
        return None

    def _shrink_target(
        self,
        original_model: str,
        visited: set[str],
        considered: set[str],
        estimated_tokens: int | None,
        overflowed_model: str | None = None,
    ) -> str | None:
        """Pick the best remaining handoff candidate for a shrunk retry.

        Bare-root overflow stays client-managed: with no peers in play there
        is no handoff to save, and Claude Code already compacts against its
        own window. Among chain candidates the largest known window wins so
        the retry has the most room; unknown-window candidates come last.

        The model that just overflowed is itself a candidate. A peer reached
        after a rate limit is already in ``visited``, and rejecting it here
        would strand the very case this feature exists for: a long
        conversation whose root is rate limited, handed to a smaller peer
        that cannot hold it. Having been tried at full size says nothing
        about whether it fits once the history is condensed.
        """
        cfg = self.router.config
        peers = {
            peer
            for source in considered
            for peer in cfg.failover.get(source, ())
            if peer != original_model
        }
        if not peers and overflowed_model in {None, original_model}:
            return None

        def window_rank(model: str) -> tuple[int, int]:
            window = cfg.context_windows.get(model)
            if window is None:
                return 1, 0
            too_small = (
                1 if estimated_tokens is not None and window < estimated_tokens else 0
            )
            return 0, -window + too_small * 10_000_000

        candidates: list[str] = []
        if (
            overflowed_model is not None
            and overflowed_model != original_model
            and overflowed_model in cfg.routes
        ):
            candidates.append(overflowed_model)
        for source in considered:
            for peer in cfg.failover.get(source, ()):
                if (
                    peer not in candidates
                    and peer in cfg.routes
                    and peer not in visited
                    and peer != original_model
                ):
                    candidates.append(peer)
        if not candidates:
            return None
        candidates.sort(key=window_rank)
        return candidates[0]

    def _unit_tokens(self, unit: list[Any]) -> int:
        """Token estimate for one atomic unit, charging images a flat cost."""
        raw = canonical_unit_bytes(unit)
        images = sum(
            1
            for message in unit
            if isinstance(message, dict)
            for block in _message_blocks(message)
            if isinstance(block, dict) and block.get("type") == "image"
        )
        return estimate_tokens_from_bytes(raw) + images * 1600

    def _split_history(
        self, payload: dict[str, Any]
    ) -> tuple[Any, list[list[Any]]]:
        """Return (system, units) from an Anthropic-shaped payload."""
        system = payload.get("system")
        messages = payload.get("messages")
        units = message_units(messages) if isinstance(messages, list) else []
        return system, units

    def _tail_units(
        self, units: list[list[Any]], budget_tokens: int
    ) -> tuple[list[list[Any]], list[list[Any]]]:
        """Split units into (dropped head, verbatim tail) within a budget.

        The tail keeps whole units from the end while they fit. At least one
        unit always remains so the request never becomes empty.
        """
        tail: list[list[Any]] = []
        used = 0
        index = len(units)
        while index > SHRINK_TAIL_MIN_UNITS or (index > 0 and not tail):
            unit = units[index - 1]
            cost = sum(self._unit_tokens([m]) for m in unit if True)
            if tail and used + cost > budget_tokens:
                break
            tail.append(unit)
            used += cost
            index -= 1
            if used >= budget_tokens:
                break
        tail.reverse()
        head = units[:index]
        return head, tail

    def _rebuild_payload(
        self,
        payload: dict[str, Any],
        target_model: str,
        brief_text: str | None,
        tail: list[list[Any]],
    ) -> dict[str, Any]:
        """Assemble the shrunk payload: system verbatim, brief, recent tail."""
        rebuilt: dict[str, Any] = strip_cache_control_deep(dict(payload))
        messages: list[dict[str, Any]] = []
        if brief_text:
            messages.append({
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": (
                        "[Earlier conversation condensed due to context window "
                        "limits. Details before this note may be summarized "
                        f"or missing.]\n\n{brief_text}"
                    ),
                }],
            })
        for unit in tail:
            for message in unit:
                if not isinstance(message, dict):
                    continue
                entry = strip_cache_control_deep(dict(message))
                content = entry.get("content")
                if isinstance(content, list):
                    blocks: list[Any] = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "image":
                            blocks.append({
                                "type": "text",
                                "text": "[image omitted during context compaction]",
                            })
                        else:
                            blocks.append(block)
                    entry["content"] = blocks
                messages.append(entry)
        if messages and messages[0].get("role") != "user":
            messages.insert(0, {
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": "[Earlier turns omitted to fit the context window.]",
                }],
            })
        rebuilt["messages"] = messages
        rebuilt["model"] = target_model
        return rebuilt

    def _payload_bytes(self, payload: dict[str, Any]) -> bytes:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")

    def _truncate_for_window(
        self, payload: dict[str, Any], target_model: str
    ) -> bytes | None:
        """Pairing-aware drop-oldest fallback sized to the target window."""
        cfg = self.router.config
        window = cfg.context_windows.get(target_model)
        if window is None:
            return None
        max_tokens = payload.get("max_tokens")
        headroom = max_tokens if isinstance(max_tokens, int) and 0 < max_tokens < window else 4096
        budget = int(window * 0.85) - headroom - SHRINK_BRIEF_RESERVE_TOKENS
        if budget < 1000:
            return None
        _system, units = self._split_history(payload)
        if not units:
            return None
        _head, tail = self._tail_units(units, budget)
        rebuilt = self._rebuild_payload(payload, target_model, None, tail)
        return self._payload_bytes(rebuilt)

    def _start_shrink_stream(self) -> "_StreamPinger":
        """Open a 200 SSE response with keepalive pings for long compactions."""
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.end_headers()
        self.close_connection = True
        pinger = _StreamPinger(
            self.wfile, interval_seconds=SHRINK_PING_INTERVAL_SECONDS
        )
        pinger.start()
        return pinger

    def _emit_stream_error(self, status: int, kind: str, message: str) -> None:
        """Deliver a terminal error inside an already-started SSE stream."""
        payload = json.dumps({
            "type": "error",
            "error": {"type": kind, "message": message},
        }).encode("ascii")
        try:
            self.wfile.write(
                b"event: error\n"
                b"data: " + payload + b"\n\n"
            )
            self.wfile.flush()
        except (TimeoutError, BrokenPipeError, ConnectionAbortedError,
                ConnectionResetError):
            self.close_connection = True

    def _call_compactor(
        self,
        provider: str,
        model: str,
        system_text: str,
        user_text: str,
    ) -> str:
        """Run one bounded compactor round trip and return its text output."""
        upstream = (
            self.router.config.openai
            if provider in {"openai", "grok"}
            else self.router.config.anthropic
        )
        if upstream is None:
            raise ShrinkUnavailableError("compactor upstream is unavailable")
        body = self._payload_bytes({
            "model": model,
            "max_tokens": SHRINK_COMPACTOR_MAX_TOKENS,
            "system": system_text,
            "messages": [{"role": "user", "content": user_text}],
        })
        connection = make_connection(upstream)
        headers = forwarded_headers(self.headers.items(), provider, len(body))
        headers["content-type"] = "application/json"
        target = upstream_path(upstream, "/v1/messages")
        try:
            connection.connect()
            if connection.sock is not None:
                connection.sock.settimeout(SHRINK_CALL_TIMEOUT_SECONDS)
            connection.request("POST", target, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(MAX_OPENROUTER_JSON_RESPONSE_BYTES + 1)
            if len(raw) > MAX_OPENROUTER_JSON_RESPONSE_BYTES:
                raise ShrinkUnavailableError("compactor response is too large")
            if not 200 <= response.status < 300:
                raise ShrinkUnavailableError(
                    f"compactor returned HTTP {response.status}"
                )
            payload = strict_json_loads(raw)
            text_parts: list[str] = []
            content = payload.get("content") if isinstance(payload, dict) else None
            if isinstance(content, list):
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "text"
                        and isinstance(block.get("text"), str)
                    ):
                        text_parts.append(block["text"])
            if not text_parts:
                raise ShrinkUnavailableError("compactor returned no text")
            return "\n".join(text_parts)
        except ShrinkUnavailableError:
            raise
        except InvalidRequestError as exc:
            raise ShrinkUnavailableError(str(exc)) from exc
        except TimeoutError as exc:
            raise ShrinkUnavailableError("compactor call timed out") from exc
        except (ConnectionError, OSError, ssl.SSLError, http.client.HTTPException) as exc:
            raise ShrinkUnavailableError("compactor connection failed") from exc
        finally:
            connection.close()

    COMPACTOR_SYSTEM_PROMPT = (
        "You compress working transcripts so another model can continue the "
        "work with minimal loss. Preserve facts, decisions, file paths, "
        "commands, errors, and open questions. Never invent content."
    )

    def _compact_chunk_prompt(self, segment_json: str, prior_notes: str) -> str:
        parts = [
            "Compress the following transcript segment into dense structured",
            "notes under these headings: Goal, Decisions, Files touched,",
            "Commands run, Errors seen, Open threads.",
        ]
        if prior_notes:
            parts.append(
                "Notes from earlier segments follow; carry their key points "
                "forward and merge them with this segment's notes.\n"
                f"<earlier_notes>\n{prior_notes}\n</earlier_notes>"
            )
        parts.append(f"<segment>\n{segment_json}\n</segment>")
        return "\n".join(parts)

    def _merge_prompt(self, briefs: list[str], prior_brief: str | None) -> str:
        joined = "\n\n---\n\n".join(briefs)
        lines = [
            "Merge the following sectioned notes into one concise brief that",
            "lets another model continue this work. Keep the same headings:",
            "Goal, Decisions, Files touched, Commands run, Errors seen, Open",
            "threads. Remove repetition; never invent content.",
        ]
        if prior_brief:
            lines.append(f"<previous_brief>\n{prior_brief}\n</previous_brief>")
        lines.append(f"<notes>\n{joined}\n</notes>")
        return "\n".join(lines)

    def _chunk_units(
        self,
        head: list[list[dict[str, Any]]],
        start: int,
        chunk_budget: int,
    ) -> tuple[list[tuple[list[dict[str, Any]], int | None]], int] | None:
        """Group units from ``start`` onward into compactor-sized chunks.

        Returns (chunks, episode_input_tokens), where each chunk pairs its
        flat message list with the unit boundary its summary may be cached
        at, or None when the chunk ends part way through a unit. Returns
        None outright when one message alone overflows the compactor, which
        leaves the request to the truncation fallback.
        """
        chunks: list[tuple[list[dict[str, Any]], int | None]] = []
        current: list[dict[str, Any]] = []
        current_total = 0
        episode_input = 0
        for position in range(start, len(head)):
            unit = head[position]
            unit_cost = sum(self._unit_tokens([m]) for m in unit if True)
            episode_input += unit_cost
            if current and current_total + unit_cost > chunk_budget:
                # This flush lands exactly where the previous unit ended,
                # so the summary covers whole units and can be cached.
                chunks.append((current, position - 1))
                current = []
                current_total = 0
            if unit_cost <= chunk_budget:
                current.extend(unit)
                current_total += unit_cost
                continue
            # A unit larger than one chunk still has to be summarized, and
            # splitting it is safe: chunks reach the compactor as inert
            # JSON, so tool pairing only has to survive in the reassembled
            # request, never here.
            for message in unit:
                cost = self._unit_tokens([message])
                if cost > chunk_budget:
                    return None
                if current and current_total + cost > chunk_budget:
                    chunks.append((current, None))
                    current = []
                    current_total = 0
                current.append(message)
                current_total += cost
        if current:
            chunks.append((current, len(head) - 1))
        return chunks, episode_input

    def _compact_history(
        self,
        payload: dict[str, Any],
        target_model: str,
        compactor_model: str,
        provider: str,
    ) -> tuple[str, int] | None:
        """Compress old history into one brief via parallel chunk summaries.

        Returns (brief_text, input_tokens_spent) or None when the history
        needs no compaction (nothing older than the retained tail).
        """
        window = self.router.config.context_windows.get(compactor_model)
        chunk_budget = (
            int(window * 0.8) - SHRINK_OUTPUT_RESERVE_TOKENS
            if window
            else SHRINK_CHUNK_TARGET_TOKENS
        )
        chunk_budget = min(chunk_budget, SHRINK_CHUNK_TARGET_TOKENS)
        if chunk_budget < 4000:
            return None
        _system, units = self._split_history(payload)
        if not units:
            return None
        tail_budget = SHRINK_RECENT_TAIL_TOKENS
        head, tail = self._tail_units(units, tail_budget)
        if not head:
            return None

        # Screen the whole head before spending anything on cache lookups:
        # an episode too big to compact stays too big however much of it a
        # previous turn already paid for.
        prescreen = self._chunk_units(head, 0, chunk_budget)
        if prescreen is None:
            return None
        prescreen_chunks, prescreen_input = prescreen
        if len(prescreen_chunks) > SHRINK_MAX_CHUNKS:
            return None
        if prescreen_input > SHRINK_MAX_EPISODE_INPUT_TOKENS:
            return None

        # Cumulative hashes let a later turn reuse the brief already paid
        # for over the units it shares with this episode: conversation
        # growth only re-compacts the new tail, never the shared prefix.
        sanitized_head = [sanitize_unit_for_compaction(unit) for unit in head]
        rolling = hashlib.sha256(canonical_unit_bytes([
            self.router.config.profile,
            compactor_model,
        ])).digest()
        boundary_hashes: list[bytes] = []
        for sanitized in sanitized_head:
            rolling = hashlib.sha256(
                rolling + canonical_unit_bytes(sanitized)
            ).digest()
            boundary_hashes.append(rolling)
        prior_brief: str | None = None
        start = 0
        for boundary in range(len(head) - 1, -1, -1):
            cached = self.router.shrink_cache.get(boundary_hashes[boundary])
            if cached is not None:
                self.router.shrink_cache.move_to_end(boundary_hashes[boundary])
                prior_brief = cached
                start = boundary + 1
                break

        selected = self._chunk_units(head, start, chunk_budget)
        if selected is None:
            return None
        chunks, episode_input = selected
        if len(chunks) > SHRINK_MAX_CHUNKS:
            return None
        if episode_input > SHRINK_MAX_EPISODE_INPUT_TOKENS:
            return None
        if not chunks and prior_brief is not None:
            return prior_brief, 0

        spent = 0
        chunk_briefs: list[str] = []
        for chunk_messages, cache_position in chunks:
            sanitized = sanitize_unit_for_compaction(chunk_messages)
            summary = self._call_compactor(
                provider,
                compactor_model,
                self.COMPACTOR_SYSTEM_PROMPT,
                self._compact_chunk_prompt(
                    self._payload_bytes({"segment": sanitized}).decode("utf-8"),
                    prior_brief or "",
                ),
            )
            spent += sum(self._unit_tokens([m]) for m in chunk_messages if True)
            if cache_position is not None:
                # Only a chunk that ends on a unit boundary describes whole
                # units, so only that one is safe to replay next turn.
                key = boundary_hashes[cache_position]
                self.router.shrink_cache[key] = summary
                self.router.shrink_cache.move_to_end(key)
                while len(self.router.shrink_cache) > SHRINK_CACHE_ENTRIES:
                    self.router.shrink_cache.popitem(last=False)
            chunk_briefs.append(summary)
            prior_brief = summary

        if not chunk_briefs:
            return None
        final_brief = self._call_compactor(
            provider,
            compactor_model,
            self.COMPACTOR_SYSTEM_PROMPT,
            self._merge_prompt(chunk_briefs, None),
        )
        return final_brief, spent

    def _prepare_shrunk_request(
        self,
        original_body: bytes,
        original_provider: str,
        original_model: str,
        target_model: str,
        estimated_tokens: int | None,
    ) -> tuple[str, bytes, bool]:
        """Produce the shrunk request body for one retry on ``target_model``.

        Returns (target_provider, body_bytes, committed_stream). A committed
        stream means SSE headers and pings are already flowing to the client,
        so any later failure must be reported inside the stream rather than
        as an HTTP status.
        """
        cfg = self.router.config
        mode = cfg.overflow_shrink
        provider = cfg.routes[target_model]
        if mode == "off":
            # The knob promises an honest failure instead of silent history
            # loss, so off never shrinks by any means.
            return provider, b"", False
        payload = request_payload(original_body)
        compactor_model = (
            cfg.compactors.get(provider) if mode in {"auto", "summarize"} else None
        )
        if compactor_model:
            # Keepalive pings only make sense to a client that asked for a
            # stream. A non-streaming caller expects one JSON body, so its
            # response stays uncommitted and the compaction runs inline: it
            # waits longer, but it receives the answer it asked for in the
            # shape it asked for.
            streaming = payload.get("stream") is True
            pinger = self._start_shrink_stream() if streaming else None
            result = None
            try:
                result = self._compact_history(
                    payload, target_model, compactor_model, provider
                )
                if result is not None:
                    brief_text, spent = result
                    _system, units = self._split_history(payload)
                    _head, tail = self._tail_units(units, SHRINK_RECENT_TAIL_TOKENS)
                    rebuilt = self._rebuild_payload(
                        payload, target_model, brief_text, tail
                    )
                    self.router.record_diagnostic({
                        "kind": "failover_shrink_compacted",
                        "target_model": target_model,
                        "compactor_model": compactor_model,
                        "estimated_input_tokens": spent,
                    })
                    body = self._payload_bytes(rebuilt)
                    if pinger is not None:
                        pinger.stop()
                    return (
                        provider,
                        self.retarget_request_body(body, target_model),
                        streaming,
                    )
            except ShrinkUnavailableError as exc:
                pass
            except (ConnectionError, OSError, ssl.SSLError, http.client.HTTPException, TimeoutError):
                if pinger is not None:
                    pinger.stop()
                raise
            if pinger is not None:
                pinger.stop()
            # Compaction unavailable or unnecessary: fall back to truncation.
            # A streaming caller is already committed to its stream here; a
            # non-streaming one still owns an unsent response.
            truncated = self._truncate_for_window(payload, target_model)
            if truncated is None:
                self.router.record_diagnostic({
                    "kind": "failover_shrink_failed",
                    "target_model": target_model,
                    "reason": "no_shrink_path",
                })
                if streaming:
                    self._emit_stream_error(
                        502,
                        "api_error",
                        "Context could not be compacted or reduced for this handoff",
                    )
                    return provider, b"", True
                return provider, b"", False
            self.router.record_diagnostic({
                "kind": "failover_shrink_truncated",
                "target_model": target_model,
                "reason": "compactor_unavailable" if result is None and compactor_model else "tail_only",
            })
            return (
                provider,
                self.retarget_request_body(truncated, target_model),
                streaming,
            )
        # No compactor: truncation path, nothing committed yet.
        if mode == "summarize":
            return provider, b"", False
        truncated = self._truncate_for_window(payload, target_model)
        if truncated is None:
            return provider, b"", False
        self.router.record_diagnostic({
            "kind": "failover_shrink_truncated",
            "target_model": target_model,
            "reason": "no_compactor",
        })
        return (
            provider,
            self.retarget_request_body(truncated, target_model),
            False,
        )


    def read_control_body(self) -> bytes:
        if self.headers.get("transfer-encoding"):
            raise ControlError(
                400, "invalid_request", "Control request is invalid"
            )
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1 or re.fullmatch(r"[0-9]+", lengths[0]) is None:
            raise ControlError(
                400, "invalid_request", "Control request is invalid"
            )
        normalized_length = lengths[0].lstrip("0") or "0"
        maximum = str(MAX_CONTROL_REQUEST_BYTES)
        if (
            len(normalized_length) > len(maximum)
            or (
                len(normalized_length) == len(maximum)
                and normalized_length > maximum
            )
        ):
            raise ControlError(
                413, "request_too_large", "Control request body is too large"
            )
        length = int(normalized_length)
        if length <= 0:
            raise ControlError(
                400, "invalid_request", "Control request is invalid"
            )
        body = self.rfile.read(length)
        if len(body) != length:
            raise ControlError(
                400, "invalid_request", "Control request is invalid"
            )
        self._control_body_consumed = True
        return body

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

    def prepare_openmodel_request(
        self, body: bytes, model: str
    ) -> tuple[bytes, bool, Any]:
        """Translate one Anthropic request before opening a loopback socket."""

        route = self.router.config.openmodel_routes[model]
        capabilities = self.router.config.openmodel_capabilities[model]
        adapter = self.router.config.openmodel_adapter
        if adapter is None:
            raise InvalidRequestError("Open-model routing is unavailable")
        payload = request_payload(body)
        try:
            translated = adapter.translate_request(
                payload,
                upstream_model=route.upstream_model,
                capabilities=capabilities,
            )
            response_constraints = adapter.derive_response_constraints(translated)
            encoded = json.dumps(
                translated,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii")
        except adapter.AdapterError as exc:
            raise InvalidRequestError(str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise InvalidRequestError(
                "Open-model request translation failed"
            ) from exc
        if len(encoded) > MAX_REQUEST_BYTES:
            raise InvalidRequestError("Translated request body is too large")
        return encoded, translated.get("stream") is True, response_constraints

    def _start_openmodel_stream(self) -> None:
        # The endpoint slot remains held while bytes are delivered. Bound writes
        # before committing the 200 so a client that stops reading cannot pin a
        # local generation slot indefinitely.
        self.connection.settimeout(OPENMODEL_DOWNSTREAM_WRITE_TIMEOUT_SECONDS)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.end_headers()
        self.close_connection = True

    def _write_openmodel_stream_frame(self, frame: bytes) -> None:
        try:
            self.wfile.write(frame)
            self.wfile.flush()
        except OSError as exc:
            raise OpenModelClientWriteError from exc

    def _write_openmodel_response(self, body: bytes) -> None:
        # Non-stream responses keep the endpoint slot until the full body is
        # delivered too. Apply the same bound before headers so a client that
        # stops reading cannot pin the local server through a JSON response.
        self.connection.settimeout(OPENMODEL_DOWNSTREAM_WRITE_TIMEOUT_SECONDS)
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except OSError as exc:
            raise OpenModelClientWriteError from exc

    def _emit_openmodel_protocol_error(self) -> None:
        self._emit_stream_error(
            502,
            "api_error",
            "The selected open-model upstream returned an invalid response",
        )
        self.close_connection = True

    def forward_openmodel(
        self,
        model: str,
        body: bytes,
        *,
        streaming: bool,
        response_constraints: Any,
    ) -> tuple[int, int, str, dict[str, int] | None]:
        """Call one signed loopback Chat Completions endpoint and translate it."""

        route = self.router.config.openmodel_routes[model]
        endpoint = self.router.config.openmodel_endpoints[route.endpoint]
        capabilities = self.router.config.openmodel_capabilities[model]
        adapter = self.router.config.openmodel_adapter
        if adapter is None:
            raise UpstreamError("Open-model routing is unavailable", retryable=False)
        semaphore = self.router.openmodel_endpoint_slots[route.endpoint]
        waiter_semaphore = self.router.openmodel_endpoint_waiters[route.endpoint]
        connection: http.client.HTTPConnection | None = None
        response: http.client.HTTPResponse | None = None
        headers_sent = False
        downstream_bytes = 0
        upstream_bytes = 0
        observer = UsageObserver()
        acquired = False
        waiting = False
        try:
            if semaphore.acquire(blocking=False):
                acquired = True
            else:
                if not waiter_semaphore.acquire(blocking=False):
                    self.record_sanitized_error("openmodel", model, 503)
                    self.safe_error_response(
                        503,
                        "overloaded_error",
                        "The selected open-model route is at local capacity",
                    )
                    return 503, 0, "endpoint_capacity_rejected", None
                waiting = True
                if not semaphore.acquire(timeout=OPENMODEL_SLOT_TIMEOUT_SECONDS):
                    self.record_sanitized_error("openmodel", model, 503)
                    self.safe_error_response(
                        503,
                        "overloaded_error",
                        "The selected open-model route is at local capacity",
                    )
                    return 503, 0, "endpoint_capacity_timeout", None
                acquired = True
                waiter_semaphore.release()
                waiting = False
            connection = http.client.HTTPConnection(
                "127.0.0.1", endpoint.port, timeout=CONNECT_TIMEOUT_SECONDS
            )
            connection.connect()
            if connection.sock is not None:
                connection.sock.settimeout(RESPONSE_HEADER_TIMEOUT_SECONDS)
            connection.request(
                "POST",
                OPENMODEL_CHAT_COMPLETIONS_PATH,
                body=body,
                headers=openmodel_headers(len(body)),
            )
            response = connection.getresponse()
            status = response.status
            if 300 <= status < 400:
                consume_openmodel_error(connection, response)
                self.record_sanitized_error("openmodel", model, 400)
                self.safe_error_response(
                    400,
                    "invalid_request_error",
                    "Open-model upstream redirects are not allowed",
                )
                return 400, 0, "upstream_rejected", None
            if not 200 <= status < 300:
                consume_openmodel_error(connection, response)
                local_status = status if 400 <= status <= 599 else 502
                kind = (
                    "rate_limit_error"
                    if status in RATE_LIMIT_STATUS_CODES
                    else "api_error"
                )
                self.record_sanitized_error("openmodel", model, local_status)
                self.safe_error_response(
                    local_status,
                    kind,
                    "The selected open-model upstream rejected the request",
                )
                return local_status, 0, "upstream_error", None

            set_stream_timeout(connection, response)
            encoding = (response.getheader("content-encoding") or "").strip().lower()
            if encoding not in {"", "identity"}:
                raise UpstreamError(
                    "Open-model upstream response encoding is unsupported",
                    retryable=False,
                )
            expected_length = openmodel_content_length(response)
            if (
                expected_length is not None
                and expected_length > MAX_OPENMODEL_JSON_RESPONSE_BYTES
            ):
                raise UpstreamError(
                    "Open-model upstream response is too large", retryable=False
                )
            content_type = (response.getheader("content-type") or "").lower()

            if streaming:
                if content_type.split(";", 1)[0].strip() != "text/event-stream":
                    raise UpstreamError(
                        "Open-model upstream response type is unsupported",
                        retryable=False,
                    )
                translator = adapter.OpenModelSSETranslator(
                    wire_model=model,
                    accepted_response_models=route.accepted_response_models,
                    capabilities=capabilities,
                    response_constraints=response_constraints,
                )
                observer.configure("text/event-stream", "identity")
                while True:
                    chunk = response.read1(16 * 1024)
                    if not chunk:
                        break
                    upstream_bytes += len(chunk)
                    if upstream_bytes > MAX_OPENMODEL_JSON_RESPONSE_BYTES:
                        if headers_sent:
                            self._emit_openmodel_protocol_error()
                            return 200, downstream_bytes, "upstream_error", observer.snapshot()
                        raise UpstreamError(
                            "Open-model upstream response is too large",
                            retryable=False,
                        )
                    try:
                        frames = translator.feed(chunk)
                    except adapter.AdapterError as exc:
                        if headers_sent:
                            self._emit_openmodel_protocol_error()
                            return 200, downstream_bytes, "upstream_error", observer.snapshot()
                        raise UpstreamError(
                            "Open-model upstream response is invalid",
                            retryable=False,
                        ) from exc
                    if not frames:
                        continue
                    if not translator.identity_validated:
                        raise UpstreamError(
                            "Open-model upstream response is invalid",
                            retryable=False,
                        )
                    if not headers_sent:
                        self._start_openmodel_stream()
                        headers_sent = True
                    for frame in frames:
                        downstream_bytes += len(frame)
                        self._write_openmodel_stream_frame(frame)
                        if translator.upstream_usage_received:
                            observer.observe(frame)
                if expected_length is not None and upstream_bytes != expected_length:
                    if headers_sent:
                        self._emit_openmodel_protocol_error()
                        return 200, downstream_bytes, "upstream_interrupted", observer.snapshot()
                    raise UpstreamError(
                        "Open-model upstream response ended early",
                        retryable=False,
                    )
                try:
                    frames = translator.finish()
                except adapter.AdapterError as exc:
                    if headers_sent:
                        self._emit_openmodel_protocol_error()
                        return 200, downstream_bytes, "upstream_error", observer.snapshot()
                    raise UpstreamError(
                        "Open-model upstream response is invalid",
                        retryable=False,
                    ) from exc
                if frames:
                    if not translator.identity_validated:
                        raise UpstreamError(
                            "Open-model upstream response is invalid",
                            retryable=False,
                        )
                    if not headers_sent:
                        self._start_openmodel_stream()
                        headers_sent = True
                    for frame in frames:
                        downstream_bytes += len(frame)
                        self._write_openmodel_stream_frame(frame)
                        if translator.upstream_usage_received:
                            observer.observe(frame)
                if not headers_sent:
                    raise UpstreamError(
                        "Open-model upstream response is invalid",
                        retryable=False,
                    )
                if translator.upstream_usage_received:
                    observer.finish()
                return 200, downstream_bytes, "completed", observer.snapshot()

            if content_type.split(";", 1)[0].strip() != "application/json":
                raise UpstreamError(
                    "Open-model upstream response type is unsupported",
                    retryable=False,
                )
            raw = bytearray()
            while True:
                chunk = response.read1(16 * 1024)
                if not chunk:
                    break
                upstream_bytes += len(chunk)
                if upstream_bytes > MAX_OPENMODEL_JSON_RESPONSE_BYTES:
                    raise UpstreamError(
                        "Open-model upstream response is too large",
                        retryable=False,
                    )
                raw.extend(chunk)
            if expected_length is not None and upstream_bytes != expected_length:
                raise UpstreamError(
                    "Open-model upstream response ended early", retryable=False
                )
            try:
                completion = strict_json_loads(bytes(raw))
                translated_response = adapter.translate_nonstream_response(
                    completion,
                    wire_model=model,
                    accepted_response_models=route.accepted_response_models,
                    capabilities=capabilities,
                    response_constraints=response_constraints,
                )
                downstream = json.dumps(
                    translated_response,
                    separators=(",", ":"),
                    ensure_ascii=True,
                    allow_nan=False,
                ).encode("ascii")
            except (InvalidRequestError, adapter.AdapterError) as exc:
                raise UpstreamError(
                    "Open-model upstream response is invalid", retryable=False
                ) from exc
            except (TypeError, ValueError) as exc:
                raise UpstreamError(
                    "Open-model upstream response is invalid", retryable=False
                ) from exc
            downstream_bytes = len(downstream)
            self._write_openmodel_response(downstream)
            headers_sent = True
            usage = translated_response.get("usage")
            return (
                200,
                downstream_bytes,
                "completed",
                dict(usage) if isinstance(usage, dict) else None,
            )
        except OpenModelClientWriteError:
            self.close_connection = True
            return 200, downstream_bytes, "connection_interrupted", observer.snapshot()
        except UpstreamError:
            raise
        except TimeoutError:
            if not headers_sent:
                raise UpstreamError("Open-model upstream connection timed out")
            self.close_connection = True
            return 200, downstream_bytes, "upstream_timeout", observer.snapshot()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            if not headers_sent:
                raise UpstreamError("Open-model upstream connection failed")
            self.close_connection = True
            return 200, downstream_bytes, "connection_interrupted", observer.snapshot()
        except http.client.HTTPException:
            if not headers_sent:
                raise UpstreamError("Open-model upstream connection failed")
            self._emit_openmodel_protocol_error()
            return 200, downstream_bytes, "upstream_interrupted", observer.snapshot()
        except (ConnectionError, OSError):
            if not headers_sent:
                raise UpstreamError("Open-model upstream connection failed")
            self.close_connection = True
            return 200, downstream_bytes, "upstream_interrupted", observer.snapshot()
        finally:
            try:
                if response is not None:
                    try:
                        response.close()
                    except OSError:
                        pass
            finally:
                try:
                    if connection is not None:
                        try:
                            connection.close()
                        except OSError:
                            pass
                finally:
                    try:
                        if waiting:
                            waiter_semaphore.release()
                    finally:
                        if acquired:
                            semaphore.release()

    def forward(
        self,
        provider: str,
        model: str,
        body: bytes,
        *,
        skip_response_headers: bool = False,
    ) -> tuple[int, int, str, dict[str, int] | None]:
        if provider == "openrouter":
            return self.forward_openrouter(
                model, body, skip_response_headers=skip_response_headers
            )
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
        # A committed shrink stream already owns the client socket: its 200
        # and SSE headers went out, so only body bytes may follow and any
        # later failure must stay inside the stream.
        headers_sent = skip_response_headers
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
            prefix = b""
            if self.rate_limit_passes_through(
                provider, status, skip_response_headers
            ):
                # Fall through to the ordinary verbatim replay below, which
                # forwards the status, every upstream header, and the body,
                # so the client sees exactly what it would without Airlock.
                self.router.record_diagnostic({
                    "kind": "anthropic_rate_limit_passthrough",
                    "provider": provider,
                    "model": model,
                    "status": status,
                })
            elif status in RATE_LIMIT_STATUS_CODES:
                raise RateLimitedError(
                    status, parse_retry_after(response.getheader("retry-after"))
                )
            if 300 <= status < 400:
                raise UpstreamError("Upstream redirects are not allowed", retryable=False)
            # A possible context overflow must be classified before any
            # response byte reaches the client, so a bounded slice of the
            # error body is read up front. Unrecognized bodies replay verbatim
            # and stream exactly as they always have.
            if status in OVERFLOW_STATUS_CODES:
                peek = bytearray()
                while len(peek) < MAX_OVERFLOW_PEEK_BYTES:
                    chunk = response.read1(16 * 1024)
                    if not chunk:
                        break
                    peek.extend(chunk)
                counts = classify_overflow(
                    status,
                    decode_overflow_body(
                        bytes(peek), response.getheader("content-encoding") or ""
                    ),
                )
                if counts is not None:
                    raise ContextOverflowError(status, counts[0], counts[1])
                prefix = bytes(peek)
            set_stream_timeout(connection, response)
            expected_length = response.getheader("content-length")
            content_type = (response.getheader("content-type") or "").lower()
            observer.configure(
                content_type, response.getheader("content-encoding") or ""
            )
            if not headers_sent:
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
            if prefix:
                response_bytes += len(prefix)
                self.wfile.write(prefix)
                self.wfile.flush()
                observer.observe(prefix)
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
        self,
        model: str,
        body: bytes,
        *,
        skip_response_headers: bool = False,
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
        # Same committed-shrink rule as forward(): once the client owns an
        # open SSE stream, never emit a second status line.
        headers_sent = skip_response_headers
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
                error_body = bytearray()
                while len(error_body) < MAX_OVERFLOW_PEEK_BYTES:
                    try:
                        chunk = response.read1(16 * 1024)
                    except (ConnectionError, OSError, ssl.SSLError, http.client.HTTPException):
                        break
                    if not chunk:
                        break
                    error_body.extend(chunk)
                counts = classify_overflow(
                    status,
                    decode_overflow_body(
                        bytes(error_body),
                        response.getheader("content-encoding") or "",
                    ),
                )
                if counts is not None:
                    raise ContextOverflowError(status, counts[0], counts[1])
                self.record_sanitized_error("openrouter", model, local_status)
                message = "The selected OpenRouter upstream rejected the request"
                if skip_response_headers:
                    self._emit_stream_error(local_status, "api_error", message)
                    self.close_connection = True
                else:
                    self.send_error_response(local_status, "api_error", message)
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
                if not headers_sent:
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
                    if not headers_sent:
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

    def _discard_control_input_best_effort(self) -> None:
        """Drain a small sent body so Windows can deliver the JSON response.

        Closing a TCP socket with unread request bytes can reset it on Windows,
        discarding a safe 4xx response the caller must be able to inspect. A
        short timeout keeps malformed or incomplete clients from occupying a
        handler thread while ordinary local requests drain immediately.
        """
        if getattr(self, "_control_body_consumed", False):
            return
        lengths = self.headers.get_all("Content-Length") or []
        target: int | None = None
        for value in lengths:
            if re.fullmatch(r"[0-9]+", value) is None:
                continue
            normalized = value.lstrip("0") or "0"
            maximum = str(MAX_CONTROL_REQUEST_BYTES + 1)
            if (
                len(normalized) > len(maximum)
                or (len(normalized) == len(maximum) and normalized > maximum)
            ):
                target = MAX_CONTROL_REQUEST_BYTES + 1
            else:
                target = int(normalized)
            break
        previous_timeout = self.connection.gettimeout()
        try:
            self.connection.settimeout(0.05)
            if target is None:
                read_available = getattr(self.rfile, "read1", None)
                if read_available is not None:
                    read_available(MAX_CONTROL_REQUEST_BYTES + 1)
            elif target:
                self.rfile.read(target)
        except (OSError, ValueError):
            pass
        finally:
            self._control_body_consumed = True
            try:
                self.connection.settimeout(previous_timeout)
            except OSError:
                pass

    def send_control_json(self, status: int, payload: object) -> None:
        self._discard_control_input_best_effort()
        body = json.dumps(
            payload,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("connection", "close")
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_control_error(self, status: int, kind: str, message: str) -> None:
        self.send_control_json(status, {
            "type": "error",
            "error": {"type": kind, "message": message},
        })

    def send_json(
        self, status: int, payload: object, retry_after: int | None = None
    ) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        if retry_after is not None:
            # Synthesized from a parsed number, never copied from upstream
            # bytes, so this stays inside the fixed-output rule while still
            # telling the client when the request is worth repeating.
            self.send_header("retry-after", str(retry_after))
        self.send_header("connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)

    def send_error_response(
        self, status: int, kind: str, message: str, retry_after: int | None = None
    ) -> None:
        self.send_json(status, {
            "type": "error",
            "error": {"type": kind, "message": message},
        }, retry_after)


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


def control_json_content_type_allowed(value: str) -> bool:
    """Accept valid JSON media type parameters without malformed tails."""
    token = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
    quoted = r'"(?:[\t\x20-\x21\x23-\x7e]|\\[\t\x20-\x7e])*"'
    pattern = re.compile(
        rf"application/json(?:[ \t]*;[ \t]*{token}[ \t]*="
        rf"[ \t]*(?:{token}|{quoted}))*[ \t]*",
        re.ASCII | re.IGNORECASE,
    )
    return pattern.fullmatch(value) is not None


def control_origin_allowed(value: str) -> bool:
    """Accept only the exact serialization of a loopback HTTP origin."""
    if not value or value != value.strip():
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        return False
    host = "[::1]" if parsed.hostname == "::1" else parsed.hostname
    expected = f"http://{host}"
    if port is not None:
        expected += f":{port}"
    return value == expected


def _control_json_within_depth(value: object, depth: int = 0) -> bool:
    if depth > MAX_CONTROL_JSON_DEPTH:
        return False
    if isinstance(value, dict):
        return all(
            _control_json_within_depth(item, depth + 1)
            for item in value.values()
        )
    if isinstance(value, list):
        return all(
            _control_json_within_depth(item, depth + 1)
            for item in value
        )
    return True


def control_request_payload(raw: bytes) -> dict[str, Any]:
    try:
        payload = strict_json_loads(raw)
    except (InvalidRequestError, RecursionError) as exc:
        raise ControlError(
            400, "invalid_request", "Control request is invalid"
        ) from exc
    if type(payload) is not dict or not _control_json_within_depth(payload):
        raise ControlError(
            400, "invalid_request", "Control request is invalid"
        )
    return payload


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


def decode_overflow_body(body: bytes, content_encoding: str) -> str:
    """Decode a bounded upstream error body into lowercase text.

    Error bodies can arrive gzipped, so the classifier decodes its own copy.
    Anything undecodable yields an empty string and therefore no
    classification, which keeps unknown shapes failing closed.
    """
    data = body
    encoding = (content_encoding or "").strip().lower()
    try:
        if encoding in {"gzip", "x-gzip"}:
            data = gzip.decompress(data)
        elif encoding not in {"", "identity"}:
            return ""
        return data[:MAX_OVERFLOW_TEXT_CHARS].decode("utf-8", errors="replace").lower()
    except (OSError, EOFError, zlib.error, UnicodeError):
        return ""


def classify_overflow(
    status: int, body_text: str
) -> tuple[int | None, int | None] | None:
    """Return parsed (prompt_tokens, limit_tokens) when the body is overflow.

    The status gate happens first: only 400 and 413 responses are even
    considered. Within them the body must name the context limit using a
    curated phrase; anything else returns None so ordinary bad requests keep
    breaking the chain unchanged.
    """
    if status not in OVERFLOW_STATUS_CODES or not body_text:
        return None
    if not any(phrase in body_text for phrase in OVERFLOW_PHRASES):
        return None
    match = PROMPT_TOO_LONG_COUNTS_RE.search(body_text)
    if match is not None:
        prompt = int(match.group(1).replace(",", ""))
        limit = int(match.group(2).replace(",", ""))
        return prompt, limit
    match = MAX_CONTEXT_LENGTH_COUNTS_RE.search(body_text)
    if match is not None:
        limit = int(match.group(1).replace(",", ""))
        requested = REQUESTED_TOKENS_RE.search(body_text)
        prompt = int(requested.group(1).replace(",", "")) if requested else None
        return prompt, limit
    return None, None


def estimate_tokens_from_bytes(raw: bytes) -> int:
    """Conservative byte-derived token estimate (roughly three bytes per token).

    Overestimating keeps chunking and truncation on the safe side of the
    window; the router has no tokenizer and must never depend on one.
    """
    return len(raw) // 3


def message_units(messages: list[Any]) -> list[list[dict[str, Any]]]:
    """Group messages into atomic handoff units that respect tool pairing.

    An assistant turn that carries tool_use blocks stays glued to the
    following user turns carrying their tool_result blocks, because both
    providers and semantics require the pair to travel together. Every other
    message becomes its own unit. The input list is never mutated.
    """
    units: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if not isinstance(message, dict):
            unit = [message]
            units.append(unit)
            index += 1
            continue
        unit = [message]
        index += 1
        if _message_has_tool_use(message):
            while index < len(messages):
                follower = messages[index]
                if isinstance(follower, dict) and _message_has_tool_result(follower):
                    unit.append(follower)
                    index += 1
                    if not _message_has_tool_use(follower):
                        continue
                    # A chained tool_use inside a result turn keeps pulling
                    # its own results too.
                    continue
                break
        units.append(unit)
    return units


def _message_blocks(message: dict[str, Any]) -> list[Any]:
    content = message.get("content")
    if isinstance(content, list):
        return content
    return []


def _message_has_tool_use(message: dict[str, Any]) -> bool:
    return any(
        isinstance(block, dict) and block.get("type") == "tool_use"
        for block in _message_blocks(message)
    )


def _message_has_tool_result(message: dict[str, Any]) -> bool:
    return any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in _message_blocks(message)
    )


def sanitize_unit_for_compaction(unit: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy a unit safe to send to the compactor: images become placeholders
    and cache_control markers are dropped."""
    cleaned: list[dict[str, Any]] = []
    for message in unit:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            blocks: list[Any] = []
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "image"
                ):
                    blocks.append({
                        "type": "text",
                        "text": "[image omitted during context compaction]",
                    })
                elif isinstance(block, dict) and "cache_control" in block:
                    stripped = {
                        key: value
                        for key, value in block.items()
                        if key != "cache_control"
                    }
                    blocks.append(stripped)
                else:
                    blocks.append(block)
            new_message = {**message, "content": blocks}
        elif isinstance(content, str):
            new_message = dict(message)
        else:
            continue
        new_message.pop("cache_control", None)
        cleaned.append(new_message)
    return cleaned


def strip_cache_control_deep(value: Any) -> Any:
    """Remove cache_control markers anywhere inside copied JSON structures."""

    if isinstance(value, dict):
        return {
            key: strip_cache_control_deep(item)
            for key, item in value.items()
            if key != "cache_control"
        }
    if isinstance(value, list):
        return [strip_cache_control_deep(item) for item in value]
    return value


def canonical_unit_bytes(value: Any) -> bytes:
    """Stable bytes for hashing transcript units of arbitrary block shape."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
            default=str,
        ).encode("ascii")
    except (TypeError, ValueError):
        return repr(value).encode("ascii")


class _StreamPinger:
    """Emit SSE comment pings so the client never idles during a long shrink.

    The pinger owns the connection between its start and stop; the handler
    stops it before writing any real stream content.
    """

    def __init__(self, wfile: Any, interval_seconds: float) -> None:
        self._wfile = wfile
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._wfile.write(b": ping\n\n")
                self._wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


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


def normalize_openrouter_effort(
    payload: dict[str, Any], ceiling: str
) -> OpenRouterEffortClamp | None:
    """Clamp a recognized effort to the resolved OpenRouter route ceiling."""

    if ceiling not in OPENROUTER_EFFORT_LEVELS:
        raise InvalidRequestError("OpenRouter route effort ceiling is invalid")
    output_config = payload.get("output_config")
    if not isinstance(output_config, dict):
        return None
    requested = output_config.get("effort")
    if requested not in OPENROUTER_EFFORT_LEVELS:
        return None
    if OPENROUTER_EFFORT_LEVELS.index(requested) <= OPENROUTER_EFFORT_LEVELS.index(ceiling):
        return None
    output_config["effort"] = ceiling
    return OpenRouterEffortClamp(requested, ceiling, ceiling)


def prepare_openrouter_request(
    body: bytes, model: str, route: OpenRouterRoute
) -> tuple[bytes, list[str], OpenRouterEffortClamp | None]:
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
    effort_clamp = normalize_openrouter_effort(payload, route.effort_ceiling)
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
            effort_clamp,
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


def openmodel_headers(content_length: int) -> dict[str, str]:
    """The complete fixed header set for an unauthenticated loopback model."""

    return {
        "accept": "application/json, text/event-stream",
        "accept-encoding": "identity",
        "connection": "close",
        "content-length": str(content_length),
        "content-type": "application/json",
    }


def openmodel_content_length(response: http.client.HTTPResponse) -> int | None:
    value = response.getheader("content-length")
    transfer_encoding = response.getheader("transfer-encoding")
    if value is not None and transfer_encoding is not None:
        raise UpstreamError(
            "Open-model upstream response framing is invalid", retryable=False
        )
    if (
        transfer_encoding is not None
        and transfer_encoding.strip().lower() != "chunked"
    ):
        raise UpstreamError(
            "Open-model upstream response framing is invalid", retryable=False
        )
    if value is None:
        return None
    text = value.strip()
    if (
        not text
        or len(text) > 20
        or not text.isascii()
        or not text.isdecimal()
    ):
        raise UpstreamError(
            "Open-model upstream response length is invalid", retryable=False
        )
    length = int(text)
    if length < 0:
        raise UpstreamError(
            "Open-model upstream response length is invalid", retryable=False
        )
    return length


def consume_openmodel_error(
    connection: http.client.HTTPConnection,
    response: http.client.HTTPResponse,
) -> None:
    """Consume one byte- and wall-clock-bounded prefix without inspecting it."""

    response_socket = connection.sock
    if response_socket is None and response.fp is not None:
        raw = getattr(response.fp, "raw", None)
        response_socket = getattr(raw, "_sock", None)
    if response_socket is None:
        return
    remaining = MAX_OPENMODEL_ERROR_RESPONSE_BYTES
    deadline = time.monotonic() + OPENMODEL_ERROR_TIMEOUT_SECONDS
    try:
        while remaining > 0:
            time_left = deadline - time.monotonic()
            if time_left <= 0:
                return
            response_socket.settimeout(time_left)
            chunk = response.read1(min(16 * 1024, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
    except (TimeoutError, OSError, http.client.HTTPException):
        # The response body is untrusted and never needed. A stalled or malformed
        # body must not delay the sanitized local error beyond its small budget.
        return


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
        if lowered in HOP_BY_HOP_HEADERS or lowered in {
            "host", "content-length", "x-airlock-control-token"
        }:
            continue
        # A compressed body cannot survive a short read. The router relays
        # upstream headers verbatim, so a stream that ends early still
        # carries Content-Encoding: gzip, and the client reports an opaque
        # decompression failure it cannot act on instead of the dropped
        # transport it actually was. Identity trades compression on this hop
        # for a truncated body that still reads as far as it got, which is
        # what makes the failure reportable and retryable. The OpenRouter
        # path already holds this line.
        if lowered == "accept-encoding":
            continue
        if provider in {"openai", "grok"} and (
            lowered in OPENAI_PRIVATE_HEADERS or lowered == "anthropic-beta"
        ):
            continue
        result[name] = value
    result["accept-encoding"] = "identity"
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


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(marker and attributes & marker)


def _protect_windows_private_path(path: Path) -> None:
    """Apply the managed current-user-only Windows DACL without a shell."""
    if os.name != "nt":
        return
    try:
        load_policy_schema().protect_private_path(path)
    except Exception as exc:
        raise RouterError("router session registry permissions are unsafe") from exc


def prepare_private_runtime_directory(path: Path, label: str) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or _is_reparse_point(metadata)
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        raise RouterError(f"{label} state is unsafe")
    if os.name == "nt":
        _protect_windows_private_path(path)
    else:
        get_effective_uid = getattr(os, "geteuid", None)
        if get_effective_uid is not None and metadata.st_uid != get_effective_uid():
            raise RouterError(f"{label} state is unsafe")
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            path.chmod(0o700)
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or _is_reparse_point(metadata)
        or not stat.S_ISDIR(metadata.st_mode)
        or (os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o700)
    ):
        raise RouterError(f"{label} state is unsafe")
    return path


def session_registry_root() -> Path:
    return safe_runtime_root().parent / "sessions"


def _require_safe_registry_target(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    get_effective_uid = getattr(os, "geteuid", None)
    wrong_posix_owner = (
        os.name != "nt"
        and get_effective_uid is not None
        and metadata.st_uid != get_effective_uid()
    )
    if (
        stat.S_ISLNK(metadata.st_mode)
        or _is_reparse_point(metadata)
        or not stat.S_ISREG(metadata.st_mode)
        or wrong_posix_owner
    ):
        raise RouterError("router session registry target is unsafe")


def _protect_registry_file(path: Path) -> None:
    _require_safe_registry_target(path)
    if os.name == "nt":
        _protect_windows_private_path(path)
    else:
        os.chmod(path, 0o600, follow_symlinks=False)
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or _is_reparse_point(metadata)
        or not stat.S_ISREG(metadata.st_mode)
        or (os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o600)
    ):
        raise RouterError("router session registry target is unsafe")


def _fsync_directory_best_effort(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def write_session_registry(server: RouterServer) -> Path:
    root = prepare_private_runtime_directory(
        session_registry_root(), "router session registry"
    )
    path = root / f"{server.instance_id}.json"
    _require_safe_registry_target(path)
    payload = json.dumps({
        "schema_version": 2,
        "instance_id": server.instance_id,
        "url": f"http://127.0.0.1:{server.server_address[1]}",
        "owner_pid": server.owner_pid,
        "router_pid": os.getpid(),
        "started_at": server.started_at,
        "profile": server.config.profile,
        "root_model": server.config.root_model,
        "root_provider": server.config.root_provider,
        "workdir": server.workdir,
        "control_token": server.control_token,
    }, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    staging = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(staging, flags, 0o600)
    try:
        # On Windows a creation mode does not constrain the inherited DACL.
        # Protect the empty staging file before any capability bytes exist.
        _protect_registry_file(staging)
    except BaseException:
        os.close(descriptor)
        try:
            staging.unlink()
        except OSError:
            pass
        raise
    published = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Replacement is intentional only for a regular stale entry owned by
        # this instance ID. Reparse points and non-regular targets are refused.
        _require_safe_registry_target(path)
        os.replace(staging, path)
        published = True
        _protect_registry_file(path)
        _fsync_directory_best_effort(root)
    except BaseException:
        try:
            staging.unlink()
        except OSError:
            pass
        if published:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    return path


def remove_session_registry(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def read_ready_payload(path: Path) -> dict[str, object] | None:
    """The startup marker, or None while it is not readable yet.

    The child publishes this through a rename. Windows can still refuse the
    read for the instant that rename is in flight, and a half-written file
    will not parse. Neither is a failure, only "not yet", so both answer None
    and the caller polls again. Treating them as errors aborted the launch
    with a bare "Permission denied".
    """
    try:
        raw = path.read_text(encoding="ascii")
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


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
    anthropic_rate_limit: str = "native",
    background_model: str | None = None,
) -> RouterConfig:
    openrouter = {
        model: OpenRouterRoute(
            endpoint_provider=metadata.endpoint_provider,
            provider_name=metadata.provider_name,
            provider_slug=metadata.provider_slug,
            quantization=metadata.quantization,
            canonical_slug=metadata.canonical_slug,
            effort_ceiling=metadata.effort_ceiling,
        )
        for model, metadata in snapshot.openrouter.items()
    }
    openmodel_endpoints = {
        endpoint_id: OpenModelEndpoint(
            base_url=metadata.base_url,
            trust=metadata.trust,
            protocol=metadata.protocol,
            auth=metadata.auth,
            max_concurrency=metadata.max_concurrency,
        )
        for endpoint_id, metadata in snapshot.openmodel.endpoints.items()
    }
    openmodel = {
        model: OpenModelRoute(
            endpoint=metadata.endpoint,
            upstream_model=metadata.upstream_model,
            accepted_response_models=tuple(metadata.accepted_response_models),
            context_window=metadata.context_window,
            max_output_tokens=metadata.max_output_tokens,
            streaming=metadata.streaming,
            tools=metadata.tools,
            tool_choice=tuple(metadata.tool_choice),
        )
        for model, metadata in snapshot.openmodel.models.items()
    }
    key = load_openrouter_key() if openrouter else None
    return RouterConfig(
        dict(snapshot.routes),
        openai_url,
        anthropic_url,
        production=True,
        openrouter=openrouter,
        openrouter_key=key,
        openmodel_endpoints=openmodel_endpoints,
        openmodel=openmodel,
        failover={
            model: tuple(peers) for model, peers in snapshot.failover.items()
        },
        context_windows=dict(snapshot.context_windows),
        route_categories=dict(snapshot.route_categories),
        effort_ceilings=dict(snapshot.effort_ceilings),
        compactors=dict(snapshot.compactors),
        overflow_shrink=snapshot.overflow_shrink,
        profile=snapshot.profile,
        root_model=snapshot.root_model,
        anthropic_rate_limit=anthropic_rate_limit,
        background_model=background_model,
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


def router_child_python() -> str:
    """Use the real Windows interpreter so Popen's pid is the router's pid.

    A Windows virtual-environment ``python.exe`` may be a launcher that starts
    the base interpreter as a grandchild. ``Popen.pid`` then names the launcher
    while the ready marker correctly names the serving process, causing the
    startup identity check and later supervision to reject a healthy router.
    The router is standard-library-only, so running its daemon on the base
    interpreter is equivalent and preserves a one-process identity.
    """

    if os.name == "nt":
        candidate = getattr(sys, "_base_executable", "")
        if isinstance(candidate, str) and candidate and Path(candidate).is_file():
            return candidate
    return sys.executable


def launch_router(args: argparse.Namespace) -> tuple[str, int]:
    """Start one router daemon and return its address and process id.

    The process id is what a supervisor watches. A port probe cannot
    stand in for it: a host that drops packets to closed ports, rather
    than refusing them, makes a dead router look like a slow one.
    """
    if not process_alive(args.parent_pid):
        raise RouterError("router owner process is not running")
    snapshot = load_router_snapshot(args.snapshot, args.snapshot_sha256)
    if any(provider in {"openai", "grok"} for provider in snapshot.routes.values()) and not args.openai_url:
        raise RouterError("subscription proxy upstream is required")
    if snapshot.openrouter:
        load_openrouter_key()
    workdir = validated_workdir(getattr(args, "workdir", None))
    root = prepare_private_runtime_directory(
        safe_runtime_root(), "router startup"
    )
    ready = root / f"ready-{secrets.token_hex(16)}.json"
    command = [
        router_child_python(),
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
        "--port",
        str(validated_port(args.port)),
    ]
    if workdir is not None:
        command.extend(("--workdir", workdir))
    if args.openai_url:
        command.extend(("--openai-url", args.openai_url))
    # The daemon runs with a scrubbed environment, so the launcher resolves
    # saved config and environment precedence before this process and passes
    # the validated choice as an argument. The watch carries the same argument
    # so a restarted router cannot silently fall back to native passthrough.
    if args.anthropic_rate_limit == "handoff":
        command.extend(("--anthropic-rate-limit", "handoff"))
    # The seat Claude Code was told to use for background work. The daemon
    # runs with a scrubbed environment, so it has to travel as an argument.
    # A caller that already knows the seat says so outright. Only the shell
    # launcher, which exports these before starting the router, can rely on
    # the environment: the Windows launcher keeps the session's model
    # variables in the child environment it builds and never in its own, so
    # reading them here would find nothing and leave the seat unset.
    background = (
        args.background_model
        or os.environ.get("ANTHROPIC_SMALL_FAST_MODEL")
        or os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL")
        or ""
    ).strip()
    if (
        background
        and len(background) <= 128
        and background.isprintable()
        and not background.startswith("openmodel/")
    ):
        command.extend(("--background-model", background))
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
                payload = read_ready_payload(ready)
                if payload is None:
                    time.sleep(0.05)
                    continue
                if (
                    payload.get("pid") != process.pid
                    or payload.get("bundle_version") != MANAGED_BUNDLE_VERSION
                    or not valid_router_url(payload.get("url"))
                ):
                    # These are local, non-secret marker fields. Naming the
                    # failed check turns an otherwise opaque launch failure
                    # into something an installer or test can repair without
                    # exposing the full marker.
                    failures = []
                    if payload.get("pid") != process.pid:
                        failures.append(
                            f"pid expected {process.pid}, got {payload.get('pid')}"
                        )
                    if payload.get("bundle_version") != MANAGED_BUNDLE_VERSION:
                        failures.append("bundle")
                    if not valid_router_url(payload.get("url")):
                        failures.append("url")
                    raise RouterError(
                        "router startup identity is invalid: " + ", ".join(failures)
                    )
                startup_complete = True
                return str(payload["url"]), int(process.pid)
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


def validated_workdir(value: object) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_WORKDIR_CHARS
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise RouterError("router workdir is invalid")
    return value


def validated_port(value: object) -> int:
    """A loopback port the router may bind, or 0 for any free port."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise RouterError("router port is invalid")
    if value == 0:
        return 0
    if not 1 <= value <= 65535:
        raise RouterError("router port is invalid")
    return value


def bind_router(
    port: int,
    config: "RouterConfig",
    *,
    owner_pid: int | None = None,
    workdir: str | None = None,
) -> "RouterServer":
    """Bind the router, waiting out a port a dead owner has not yet released.

    A restart must reclaim the exact port the session was told to use, and
    the kernel can hold that port for a moment after the previous process
    dies. Only a requested port is worth waiting for: port 0 either
    succeeds at once or is a real failure.
    """
    if port == 0:
        return RouterServer(
            ("127.0.0.1", 0),
            config,
            owner_pid=owner_pid,
            workdir=workdir,
        )
    deadline = time.monotonic() + REBIND_TIMEOUT_SECONDS
    while True:
        try:
            return RouterServer(
                ("127.0.0.1", port),
                config,
                owner_pid=owner_pid,
                workdir=workdir,
            )
        except OSError:
            if time.monotonic() >= deadline:
                raise RouterError("router port could not be reclaimed") from None
            time.sleep(REBIND_RETRY_SECONDS)


def start_router(args: argparse.Namespace) -> int:
    """Start a router and report its address, and its pid when asked."""
    address, pid = launch_router(args)
    sys.stdout.write(address + "\n")
    if getattr(args, "print_pid", False):
        sys.stdout.write(f"{pid}\n")
    return 0


def watch_router(args: argparse.Namespace) -> int:
    """Put a router back on the session address when its process dies.

    Claude Code reads the router address once, at startup, so a router that
    dies mid-session takes every later request with it and the client sees
    only a refused connection that names no cause. The router is an
    ordinary python process holding an ephemeral port, so anything that
    sweeps processes by image name takes it down. This watch outlives it
    and rebinds the same port, which is free precisely because the owner is
    gone. Restarts are capped so a router that cannot stay up fails the
    session once instead of looping.
    """
    port = validated_port(args.port)
    if port == 0:
        raise RouterError("router watch needs the address to hold")
    if args.router_pid <= 0:
        raise RouterError("router watch needs the router it is watching")
    router_pid = args.router_pid
    restarts = 0
    while process_alive(args.parent_pid):
        time.sleep(WATCH_INTERVAL_SECONDS)
        if not process_alive(args.parent_pid):
            break
        if process_alive(router_pid):
            continue
        if restarts >= MAX_ROUTER_RESTARTS:
            return 0
        restarts += 1
        try:
            address, router_pid = launch_router(args)
        except (OSError, RouterError, ValueError, json.JSONDecodeError):
            # A failed restart means the snapshot is gone, the session is
            # ending, or something else took the port. None of those improve
            # by trying again.
            return 0
        if address != f"http://127.0.0.1:{port}":
            # A router on another address cannot serve a session that was
            # already told where to look.
            return 0
    return 0


def serve_router(args: argparse.Namespace) -> int:
    if not process_alive(args.parent_pid):
        raise RouterError("router owner process is not running")
    snapshot = load_router_snapshot(args.snapshot, args.snapshot_sha256)
    config = router_config_from_snapshot(
        snapshot,
        args.openai_url,
        args.anthropic_url,
        args.anthropic_rate_limit,
        args.background_model,
    )
    ready = Path(args.ready_file)
    if ready.parent.resolve() != safe_runtime_root().resolve() or ready.exists():
        raise RouterError("router ready path is unsafe")
    server = bind_router(
        validated_port(args.port),
        config,
        owner_pid=args.parent_pid,
        workdir=validated_workdir(args.workdir),
    )
    monitor = threading.Thread(
        target=monitor_parent,
        args=(server, args.parent_pid),
        daemon=True,
    )
    monitor.start()
    if args.port != 0:
        # The in-memory history of the previous instance died with it, so
        # say why the record starts here rather than letting cooldowns and
        # totals appear to reset for no reason.
        server.record_diagnostic({"kind": "router_restarted"})
    registry: Path | None = None
    try:
        registry = write_session_registry(server)
        append_launch_record(server, "start")
        write_ready(ready, server)
        server.serve_forever(poll_interval=0.25)
    finally:
        try:
            if registry is not None:
                remove_session_registry(registry)
                append_launch_record(server, "stop")
        finally:
            server.server_close()
    return 0


LAUNCH_LOG_FILENAME = "launches.jsonl"
LAUNCH_LOG_MAX_BYTES = 4 * 1024 * 1024


def launch_log_path() -> Path:
    return session_registry_root().parent / LAUNCH_LOG_FILENAME


def append_launch_record(server: RouterServer, event: str) -> None:
    """Append one line saying this router started or stopped for a directory.

    The console overlays these on a session so a person can see when the
    session ran through Airlock and when it ran as plain Claude Code. The
    transcript itself carries no such trace. The log is private to the user,
    holds no prompts or credentials, and is rotated once when it grows past
    a few megabytes. A failure here must never affect the router.
    """
    try:
        path = launch_log_path()
        prepare_private_runtime_directory(path.parent, "router launch log")
        record = json.dumps({
            "schema_version": 1,
            "event": event,
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "instance_id": server.instance_id,
            "owner_pid": server.owner_pid,
            "workdir": server.workdir,
            "profile": server.config.profile,
            "root_model": server.config.root_model,
            "root_provider": server.config.root_provider,
        }, separators=(",", ":"), ensure_ascii=True)
        try:
            if path.is_symlink() or (path.exists() and not path.is_file()):
                return
            if path.exists() and path.stat().st_size > LAUNCH_LOG_MAX_BYTES:
                rotated = path.with_suffix(".1.jsonl")
                if rotated.exists():
                    rotated.unlink()
                path.replace(rotated)
        except OSError:
            return
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        fd = os.open(path, flags, 0o600)
        try:
            os.write(fd, (record + "\n").encode("ascii"))
        finally:
            os.close(fd)
    except Exception:
        return


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="airlock-router")
    subparsers = result.add_subparsers(dest="command", required=True)
    for name in ("start", "serve", "watch"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--parent-pid", type=int, required=True)
        sub.add_argument("--snapshot", required=True)
        sub.add_argument("--snapshot-sha256", required=True)
        sub.add_argument("--openai-url")
        sub.add_argument("--anthropic-url", default="https://api.anthropic.com")
        sub.add_argument(
            "--anthropic-rate-limit",
            choices=("native", "handoff"),
            default="native",
        )
        sub.add_argument("--background-model", default=None)
        sub.add_argument("--workdir", default=None)
        # A restart has to land on the port the session was already told to
        # use, because Claude Code reads ANTHROPIC_BASE_URL once at startup.
        # Zero keeps the original behaviour of taking whatever port is free.
        sub.add_argument("--port", type=int, default=0)
        if name == "start":
            sub.add_argument("--print-pid", action="store_true")
        if name == "serve":
            sub.add_argument("--ready-file", required=True)
        if name == "watch":
            # The process to watch. A port probe cannot stand in for it: a
            # host that drops packets to closed ports rather than refusing
            # them makes a dead router look like a slow one.
            sub.add_argument("--router-pid", type=int, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "start":
            return start_router(args)
        if args.command == "watch":
            return watch_router(args)
        return serve_router(args)
    except (OSError, RouterError, ValueError, json.JSONDecodeError) as exc:
        print(f"airlock-router: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
