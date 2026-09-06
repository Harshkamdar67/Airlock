#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Local-only Airlock console: session discovery, JSON API, and SSE stream."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit


CONSOLE_VERSION = "0.1.0"
DEFAULT_PORT = 4783
CONSOLE_ADDRESS_FILENAME = "console-address.json"
CONSOLE_LOCK_FILENAME = "console.lock"
CONSOLE_ADDRESS_SCHEMA_VERSION = 1
MAX_CONSOLE_ADDRESS_BYTES = 16 * 1024
CONSOLE_ADDRESS_KEYS = frozenset({"schema_version", "url", "pid", "console_id"})
_LOCK_BUSY_ERRNOS = {errno.EACCES, errno.EAGAIN}
for _lock_errno_name in ("EDEADLK", "EWOULDBLOCK"):
    _lock_errno = getattr(errno, _lock_errno_name, None)
    if _lock_errno is not None:
        _LOCK_BUSY_ERRNOS.add(_lock_errno)
CONSOLE_ID_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
MAX_PROCESS_ID = 0xFFFFFFFF
MAX_REGISTRY_BYTES = 16 * 1024
MAX_ROUTER_BODY_BYTES = 1024 * 1024
MAX_CONSOLE_PROBE_BYTES = 4096
MAX_MUTATION_BODY_BYTES = 128 * 1024
MAX_CONTROL_BODY_BYTES = 4096
MAX_CONTROL_RESPONSE_BYTES = 16 * 1024
MAX_HELPER_BYTES = 16 * 1024 * 1024
MAX_JSON_INT_DIGITS = 16
MAX_SAFE_INT = 10 ** 15
MAX_REMAINING_SECONDS = 30 * 24 * 60 * 60
PROPOSAL_TTL_SECONDS = 15 * 60
PROPOSAL_RETENTION_SECONDS = 60 * 60
MAX_PROPOSALS = 64
MIN_REASON_CHARS = 8
MAX_REASON_CHARS = 400
MIN_CONTROL_TOKEN_CHARS = 40
MAX_CONTROL_TOKEN_CHARS = 128
CSRF_HEADER = "X-Airlock-CSRF"
CONTROL_TOKEN_HEADER = "X-Airlock-Control-Token"
CHAIN_NOTICE = (
    "Applies to sessions started after this change. "
    "Running sessions keep their frozen chains."
)
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; base-uri 'none'; object-src 'none'; "
    "frame-ancestors 'none'; form-action 'self'; connect-src 'self'"
)
ROUTER_FETCH_TIMEOUT_SECONDS = 1.0
SCAN_FETCH_TIMEOUT_SECONDS = 0.3
CONSOLE_PROBE_DEADLINE_SECONDS = 0.5
POLL_INTERVAL_SECONDS = 1.0
SSE_COALESCE_SECONDS = 0.5
SSE_HEARTBEAT_SECONDS = 15.0
SSE_MAX_SECONDS = 15 * 60
ENDED_VISIBLE_SECONDS = 5 * 60
RUNNING_WINDOW_SECONDS = 5 * 60
BLOCK_EVENT_WINDOW_SECONDS = 10 * 60
# Activity, block, and event timestamps more than this far ahead of the
# console clock are ignored. Small allowance covers local clock skew; a
# 2099 last_request_at must not make a session look running forever.
FUTURE_SKEW_SECONDS = 120
MAX_STRING_CHARS = 4096
MAX_INSTANCE_ID_CHARS = 64
INSTANCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$"
)
VERSION_TOKEN_PATTERN = re.compile(r"^(?:\d+(?:\.\d+)*|\d{8})$")
# Matches the policy snapshot exact-id grammar: token-like, no whitespace
# or free-form prose, long enough for Claude IDs with a [1m] suffix.
MODEL_PATTERN = re.compile(r"[a-z0-9][a-z0-9._:/\-\[\]]{0,159}\Z", re.ASCII)
# Narrow live-key prefixes. Ordinary model IDs such as claude-sonnet-5 or
# gpt-5.6-sol do not match; values like sk-live... or vendor/sk-... do.
CREDENTIAL_PREFIX_RE = re.compile(r"(?i)(?:^|/)(?:sk-|sk_|gsk_|xai-|rk-)")
PROVIDER_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z", re.ASCII)
PROFILE_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z", re.ASCII)
KIND_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z", re.ASCII)
EFFORT_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,15}\Z", re.ASCII)
MD_SPECIAL_PATTERN = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>])")
SHORT_NAME_PREFIXES = {"claude", "gpt", "grok"}
SHORT_NAME_SKIP_TOKENS = {"fast", "slow", "preview", "latest"}
ROUTE_CATEGORIES = frozenset({"included", "extra", "metered", "unknown"})
BLOCKED_REASONS = frozenset({
    "rate_limit",
    "provider_cooldown",
    "context_overflow",
    "chain_exhausted",
})
KNOWN_EVENT_KINDS = frozenset({
    "rate_limit_failover_attempted",
    "rate_limit_failover_succeeded",
    "rate_limit_cooldown_skipped",
    "rate_limit_provider_cooldown",
    "rate_limit_chain_exhausted",
    "failover_overflow_attempted",
    "failover_overflow_succeeded",
    "failover_overflow_skipped",
    "failover_shrink_compacted",
    "failover_shrink_truncated",
    "failover_shrink_failed",
    "overflow_chain_exhausted",
    "upstream_context_overflow",
    "openrouter_effort_clamped",
    "openrouter_server_tools_stripped",
    "sanitized_error_substituted",
    "background_model_substituted",
    "anthropic_rate_limit_passthrough",
    "model_not_enabled",
    "session_model_pinned",
    "session_model_unpinned",
    "session_root_selected",
    "router_restarted",
})
CONTROL_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{40,128}$")
PROPOSAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
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
ACTIVE_PROPOSAL_STATUSES = frozenset({
    "pending",
    "applying",
    "failed",
    "conflicted",
})
EDITABLE_PROPOSAL_STATUSES = frozenset({"pending", "failed", "conflicted"})
HANDOFF_OPERATIONS = frozenset({"pin", "restore_root"})
CREATED_BY_VALUES = frozenset({"human", "agent"})
HUMAN_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH"})
AUDIT_KINDS = frozenset({
    "proposal_created",
    "proposal_edited",
    "proposal_rejected",
    "proposal_expired",
    "proposal_apply_started",
    "proposal_apply_succeeded",
    "proposal_apply_failed",
    "proposal_apply_conflicted",
    "chain_directly_applied",
})
CHAIN_EXHAUSTED_KINDS = frozenset({
    "rate_limit_chain_exhausted",
})
OVERFLOW_BLOCK_KINDS = frozenset({
    "overflow_chain_exhausted",
    "upstream_context_overflow",
})
HANDOFF_ATTEMPT_KINDS = frozenset({
    "rate_limit_failover_attempted",
    "failover_overflow_attempted",
})
HANDOFF_SUCCESS_KINDS = frozenset({
    "rate_limit_failover_succeeded",
    "failover_overflow_succeeded",
})
OVERFLOW_HANDOFF_KINDS = frozenset({
    "failover_overflow_attempted",
    "failover_overflow_succeeded",
})
EVENT_OUTCOMES = frozenset({
    "completed",
    "rate_limited",
    "upstream_rate_limited",
    "rate_limit_exhausted",
    "overflow_exhausted",
    "upstream_error",
    "upstream_rejected",
    "upstream_interrupted",
})
EVENT_REASONS = frozenset({
    "context_window",
    "no_shrink_path",
    "compactor_unavailable",
    "no_compactor",
    "tail_only",
    "rate_limit",
    "overflow",
})
EVENT_KEYS = frozenset({
    "timestamp",
    "kind",
    "model",
    "provider",
    "status",
    "outcome",
    "failover_from",
    "to_model",
    "from_model",
    "models_considered",
    "reason",
    "remaining_seconds",
    "usage",
    "duration_ms",
})
USAGE_FIELDS = frozenset({
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
})
USAGE_SUMMARY_KEYS = (
    "provider",
    "model",
    "requests",
    "completed",
    "errors",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
)
REPORT_FILENAME = "airlock-session-report.md"
HEADROOM_WINDOW = {
    "openrouter": "credits",
}
REGISTRY_KEYS = (
    "schema_version",
    "instance_id",
    "url",
    "owner_pid",
    "router_pid",
    "started_at",
    "profile",
    "root_model",
    "root_provider",
    "workdir",
)
STATIC_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}
MISSING_SITE_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Airlock Console</title>
</head>
<body>
<h1>Airlock Console</h1>
<p>The console page is not installed at the expected location, so this process is serving the JSON API only.</p>
<p>Try <a href="/api/overview">/api/overview</a> or rebuild the page under console/dist.</p>
</body>
</html>
"""
SECURITY_HEADERS = (
    ("x-content-type-options", "nosniff"),
    ("content-security-policy", CONTENT_SECURITY_POLICY),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "no-referrer"),
)


class ConsoleError(RuntimeError):
    """A user-facing console failure."""


class _ConsoleTermination(BaseException):
    """Internal signal used to leave the serving loop through its cleanup."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or TIMESTAMP_PATTERN.match(value) is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)


def _parse_json_int(text: str) -> int:
    if len(text) > MAX_JSON_INT_DIGITS:
        raise ValueError("integer too large")
    value = int(text)
    if abs(value) > MAX_SAFE_INT:
        raise ValueError("integer too large")
    return value


def _parse_json_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or abs(value) > MAX_SAFE_INT:
        raise ValueError("non-finite or oversized number")
    return value


def parse_json(raw: bytes | bytearray | str) -> object | None:
    """Decode JSON, rejecting hostile nesting and non-finite numbers."""
    try:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(
            text,
            parse_int=_parse_json_int,
            parse_float=_parse_json_float,
            parse_constant=lambda _name: (_ for _ in ()).throw(ValueError("non-finite constant")),
        )
    except (UnicodeDecodeError, ValueError, RecursionError, OverflowError, MemoryError):
        return None


def parse_json_object(raw: bytes | bytearray | str) -> dict[str, Any] | None:
    payload = parse_json(raw)
    return payload if isinstance(payload, dict) else None


def is_int(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) <= MAX_SAFE_INT
    )


def is_non_empty_str(value: object, max_chars: int = MAX_STRING_CHARS) -> bool:
    return isinstance(value, str) and 0 < len(value) <= max_chars


def bounded_str(value: object, max_chars: int = MAX_STRING_CHARS) -> str | None:
    if isinstance(value, str) and 0 < len(value) <= max_chars:
        return value
    return None


def non_negative_int(value: object) -> int | None:
    if is_int(value) and 0 <= value <= MAX_SAFE_INT:
        return value
    return None


def positive_int(value: object) -> int | None:
    if is_int(value) and 0 < value <= MAX_SAFE_INT:
        return value
    return None


def coerce_remaining(value: object) -> int | None:
    if is_int(value) and 0 <= value <= MAX_REMAINING_SECONDS:
        return value
    if (
        isinstance(value, float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= MAX_REMAINING_SECONDS
    ):
        try:
            return int(round(value))
        except (OverflowError, ValueError):
            return None
    return None


def http_status_code(value: object) -> int | None:
    number = non_negative_int(value)
    if number is None or number > 999:
        return None
    return number


def looks_like_credential(value: str) -> bool:
    return CREDENTIAL_PREFIX_RE.search(value) is not None


def safe_model(value: object) -> str | None:
    text = bounded_str(value, 160)
    if text is None or MODEL_PATTERN.match(text) is None:
        return None
    if looks_like_credential(text):
        return None
    return text


def safe_provider(value: object) -> str | None:
    text = bounded_str(value, 32)
    if text is None or PROVIDER_PATTERN.match(text) is None:
        return None
    if looks_like_credential(text):
        return None
    return text


def safe_profile(value: object) -> str | None:
    text = bounded_str(value, 64)
    if text is None or PROFILE_PATTERN.match(text) is None:
        return None
    return text


def safe_kind(value: object) -> str | None:
    text = bounded_str(value, 64)
    if text is None or KIND_PATTERN.match(text) is None:
        return None
    return text


def safe_outcome(value: object) -> str | None:
    text = bounded_str(value, 64)
    if text is None or text not in EVENT_OUTCOMES:
        return None
    return text


def safe_reason(value: object) -> str | None:
    text = bounded_str(value, 64)
    if text is None or text not in EVENT_REASONS:
        return None
    return text


def safe_effort(value: object) -> str | None:
    text = bounded_str(value, 16)
    if text is None or EFFORT_PATTERN.match(text) is None:
        return None
    return text


def timestamp_usable(value: object, now: datetime) -> datetime | None:
    stamp = parse_timestamp(value)
    if stamp is None:
        return None
    if stamp > now + timedelta(seconds=FUTURE_SKEW_SECONDS):
        return None
    return stamp


def _sanitize_markdown_text(value: object) -> str:
    raw = "" if value is None else str(value)
    cleaned: list[str] = []
    for char in raw:
        code = ord(char)
        if char == "\n" or char == "\r" or code < 32 or code == 127:
            if cleaned and cleaned[-1] != " ":
                cleaned.append(" ")
            continue
        cleaned.append(char)
    return "".join(cleaned).strip()


def markdown_code_span(value: object) -> str:
    """Render an identifier inside a Markdown code span.

    Identifiers, model IDs, providers, paths, project names, digests, and
    timestamps go here. Backticks and control characters are stripped so the
    span cannot break out; hyphens and dots stay readable.
    """
    text = _sanitize_markdown_text(value).replace("`", "")
    if not text:
        text = "unknown"
    return f"`{text}`"


def markdown_escape(value: object) -> str:
    """Escape a prose fragment for Markdown.

    Always escape backslash, backtick, asterisk, underscore, square
    brackets, parentheses, angle brackets, exclamation mark, pipe, and
    hash. Escape hyphen, plus, and a digit-followed-by-dot only at the
    start of the fragment, where they would begin a line. Never escape
    hyphens, dots, or plus signs mid-sentence.
    """
    raw = _sanitize_markdown_text(value)
    special = set("\\`*_[]()<>!|#")
    out: list[str] = []
    for index, char in enumerate(raw):
        if char in special:
            out.append("\\")
        elif index == 0 and char in "-+":
            out.append("\\")
        elif (
            index == 0
            and char.isdigit()
            and index + 1 < len(raw)
            and raw[index + 1] == "."
        ):
            out.append("\\")
            out.append(char)
            continue
        out.append(char)
    return "".join(out)


def open_regular_file(path: Path, max_bytes: int) -> bytes | None:
    """Read a regular file from an opened handle without following links.

    Uses O_NOFOLLOW when the platform provides it, then fstat on the open
    handle so a replacement between lstat and read cannot change the type
    or size we accepted. Residual Windows limit: CPython does not expose
    FILE_FLAG_OPEN_REPARSE_POINT here, so some reparse points may still be
    followed on Windows. Closing that fully would need extra Win32 flags
    this file does not add.
    """
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size < 0:
            return None
        if info.st_size > max_bytes:
            return None
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            piece = os.read(fd, remaining)
            if not piece:
                break
            chunks.append(piece)
            remaining -= len(piece)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            return None
        return data
    except OSError:
        return None
    finally:
        os.close(fd)


def short_name(model: str) -> str:
    name = model.split("/")[-1]
    name = re.sub(r"\[[^\]]*\]", "", name).strip()
    if not name:
        return model
    tokens = [token for token in name.split("-") if token]
    kept: list[str] = []
    for index, token in enumerate(tokens):
        lowered = token.lower()
        if index == 0 and lowered in SHORT_NAME_PREFIXES:
            continue
        if VERSION_TOKEN_PATTERN.match(token):
            continue
        if lowered in SHORT_NAME_SKIP_TOKENS:
            continue
        kept.append(token)
    if not kept:
        return tokens[0]
    return "-".join(kept)


def display_name(model: str | None) -> str:
    if not model:
        return "the active model"
    name = short_name(model)
    return name[:1].upper() + name[1:] if name else model


def project_name(workdir: str | None) -> str | None:
    if not workdir:
        return None
    name = Path(workdir).name
    return name or None


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


def is_loopback_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if port is not None and not 0 < port < 65536:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.path in {"", "/"}
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def process_alive(pid: int) -> bool:
    """True when a process with this pid currently exists.

    PID reuse is not fully solvable here. The registry only stores owner_pid
    and router_pid, not a process-creation identity, so a new process that
    inherits a recently-exited pid can keep a dead session looking live until
    the router URL stops answering. Closing that needs router-side identity
    and is out of scope for this file.
    """
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
    import ctypes

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


def safe_runtime_root() -> Path:
    """The router-startups directory; sessions live beside it."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Airlock" / "router-startups"
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state / "airlock" / "router-startups"


def console_runtime_root() -> Path:
    return safe_runtime_root().parent


def console_address_path(runtime_root: Path | None = None) -> Path:
    """Return the singleton discovery marker path for the console runtime."""
    return (runtime_root or console_runtime_root()) / CONSOLE_ADDRESS_FILENAME


def console_lock_path(runtime_root: Path | None = None) -> Path:
    """Return the process-lifetime advisory lock path for the console runtime."""
    return (runtime_root or console_runtime_root()) / CONSOLE_LOCK_FILENAME


def shared_console_defaults(tools_helper: Any | None) -> tuple[int, Path]:
    """Use the shared client contract when it is complete, else local defaults."""
    fallback = (DEFAULT_PORT, console_address_path())
    if tools_helper is None:
        return fallback
    try:
        port = getattr(tools_helper, "DEFAULT_CONSOLE_PORT")
        url = getattr(tools_helper, "DEFAULT_CONSOLE_URL")
        basename = getattr(tools_helper, "CONSOLE_ADDRESS_BASENAME")
        runtime_root = tools_helper.default_console_runtime_root()
        address_file = tools_helper.default_console_address_file()
        if (
            not is_int(port)
            or not 1 <= port <= 65535
            or url != f"http://127.0.0.1:{port}"
            or basename != CONSOLE_ADDRESS_FILENAME
            or not isinstance(runtime_root, Path)
            or not isinstance(address_file, Path)
            or not address_file.is_absolute()
            or address_file != runtime_root / basename
        ):
            return fallback
        return port, address_file
    except Exception:
        return fallback


def _is_reparse_point(details: os.stat_result) -> bool:
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _runtime_root_details(runtime_root: Path) -> os.stat_result:
    try:
        details = os.lstat(runtime_root)
    except OSError as error:
        raise ConsoleError("console runtime root is unavailable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or _is_reparse_point(details)
    ):
        raise ConsoleError("console runtime root is not a safe directory")
    if os.name != "nt" and hasattr(os, "geteuid"):
        if details.st_uid != os.geteuid():
            raise ConsoleError("console runtime root is not owned by this user")
    return details


def _prepare_console_runtime_root(runtime_root: Path) -> Path:
    try:
        before = os.lstat(runtime_root)
    except FileNotFoundError:
        before = None
    except OSError as error:
        raise ConsoleError("console runtime root is unavailable") from error
    if before is not None and (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISDIR(before.st_mode)
        or _is_reparse_point(before)
    ):
        raise ConsoleError("console runtime root is not a safe directory")
    try:
        runtime_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError as error:
        raise ConsoleError("console runtime root could not be created") from error
    details = _runtime_root_details(runtime_root)
    if os.name != "nt":
        try:
            os.chmod(runtime_root, 0o700)
        except OSError as error:
            raise ConsoleError("console runtime root could not be made private") from error
        details = _runtime_root_details(runtime_root)
        if stat.S_IMODE(details.st_mode) != 0o700:
            raise ConsoleError("console runtime root is not private")
    return runtime_root


def _console_address_details(
    path: Path,
    *,
    require_private: bool,
) -> os.stat_result:
    try:
        details = os.lstat(path)
    except OSError as error:
        raise ConsoleError("console address marker is unavailable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or _is_reparse_point(details)
        or details.st_size < 0
        or details.st_size > MAX_CONSOLE_ADDRESS_BYTES
    ):
        raise ConsoleError("console address marker is not a safe regular file")
    if (
        require_private
        and os.name != "nt"
        and stat.S_IMODE(details.st_mode) != 0o600
    ):
        raise ConsoleError("console address marker is not private")
    return details


def _validate_existing_console_address(path: Path) -> None:
    try:
        details = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise ConsoleError("console address marker is unavailable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or _is_reparse_point(details)
    ):
        raise ConsoleError("console address marker is not a safe regular file")


def _protect_console_path(
    path: Path,
    access_helper: Any | None,
    *,
    kind: str = "address marker",
) -> None:
    policy = getattr(access_helper, "POLICY_SCHEMA", None)
    protect = getattr(policy, "protect_private_path", None)
    if not callable(protect):
        return
    try:
        protect(path)
    except Exception as error:
        raise ConsoleError(f"console {kind} could not be made private") from error


def _fsync_console_directory(runtime_root: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = -1
    try:
        descriptor = os.open(runtime_root, flags)
        os.fsync(descriptor)
    except OSError:
        # The file data itself is already durable. Some filesystems do not
        # support directory fsync, so directory-entry durability is best effort.
        pass
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _normalize_console_address_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or not 1 <= port <= 65535
        or parsed.path != ""
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return None
    canonical = f"http://127.0.0.1:{port}"
    if value != canonical:
        return None
    return canonical


def _validate_console_address_payload(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict) or set(payload) != CONSOLE_ADDRESS_KEYS:
        return None
    schema_version = payload.get("schema_version")
    pid = payload.get("pid")
    console_id = payload.get("console_id")
    url = _normalize_console_address_url(payload.get("url"))
    if (
        type(schema_version) is not int
        or schema_version != CONSOLE_ADDRESS_SCHEMA_VERSION
        or not is_int(pid)
        or not 1 <= pid <= MAX_PROCESS_ID
        or not isinstance(console_id, str)
        or CONSOLE_ID_PATTERN.fullmatch(console_id) is None
        or url is None
    ):
        return None
    return {
        "schema_version": CONSOLE_ADDRESS_SCHEMA_VERSION,
        "url": url,
        "pid": pid,
        "console_id": console_id,
    }


def _read_console_address_snapshot(
    path: Path,
) -> tuple[dict[str, object], os.stat_result] | None:
    target = Path(path)
    try:
        _runtime_root_details(target.parent)
        before = _console_address_details(target, require_private=True)
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or _is_reparse_point(opened)
                or opened.st_size < 0
                or opened.st_size > MAX_CONSOLE_ADDRESS_BYTES
                or not _same_file(before, opened)
            ):
                return None
            chunks: list[bytes] = []
            remaining = MAX_CONSOLE_ADDRESS_BYTES + 1
            while remaining > 0:
                piece = os.read(descriptor, remaining)
                if not piece:
                    break
                chunks.append(piece)
                remaining -= len(piece)
        finally:
            os.close(descriptor)
        after = _console_address_details(target, require_private=True)
    except (OSError, ConsoleError):
        return None
    content = b"".join(chunks)
    if (
        len(content) > MAX_CONSOLE_ADDRESS_BYTES
        or len(content) != before.st_size
        or not _same_file(before, after)
    ):
        return None
    payload = _validate_console_address_payload(parse_json(content))
    if payload is None:
        return None
    return payload, after


def read_console_address(path: Path) -> dict[str, object] | None:
    """Read a private, exact-schema address marker without following links."""
    snapshot = _read_console_address_snapshot(path)
    return None if snapshot is None else snapshot[0]


def write_console_address(
    path: Path,
    bound_port: int,
    console_id: str,
    pid: int = os.getpid(),
    *,
    access_helper: Any | None = None,
) -> None:
    """Atomically publish this process's loopback console address."""
    if not is_int(bound_port) or not 1 <= bound_port <= 65535:
        raise ConsoleError("console address port is invalid")
    if not is_int(pid) or not 1 <= pid <= MAX_PROCESS_ID:
        raise ConsoleError("console address pid is invalid")
    if not isinstance(console_id, str) or CONSOLE_ID_PATTERN.fullmatch(console_id) is None:
        raise ConsoleError("console id is invalid")
    target = Path(path)
    if not target.is_absolute():
        raise ConsoleError("console address path must be absolute")
    runtime_root = _prepare_console_runtime_root(target.parent)
    _validate_existing_console_address(target)
    payload: dict[str, object] = {
        "schema_version": CONSOLE_ADDRESS_SCHEMA_VERSION,
        "url": f"http://127.0.0.1:{bound_port}",
        "pid": pid,
        "console_id": console_id,
    }
    content = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("ascii")
    if len(content) > MAX_CONSOLE_ADDRESS_BYTES:
        raise ConsoleError("console address marker is too large")

    descriptor = -1
    temporary_path: Path | None = None
    written_details: os.stat_result | None = None
    replaced = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        for _ in range(32):
            candidate = runtime_root / (
                f".{CONSOLE_ADDRESS_FILENAME}.{secrets.token_hex(8)}.tmp"
            )
            try:
                descriptor = os.open(candidate, flags, 0o600)
                temporary_path = candidate
                break
            except FileExistsError:
                continue
        if temporary_path is None or descriptor < 0:
            raise ConsoleError("console address marker could not be created securely")
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _is_reparse_point(opened):
            raise ConsoleError("console address temporary is not a safe regular file")
        # Apply the private mode and Windows DACL before any marker bytes exist.
        os.chmod(temporary_path, 0o600)
        _protect_console_path(temporary_path, access_helper)
        if os.name != "nt" and stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
            raise ConsoleError("console address temporary is not private")
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            written_details = os.fstat(stream.fileno())
        if (
            written_details is None
            or not stat.S_ISREG(written_details.st_mode)
            or _is_reparse_point(written_details)
            or written_details.st_size != len(content)
            or written_details.st_size > MAX_CONSOLE_ADDRESS_BYTES
        ):
            raise ConsoleError("console address marker could not be written securely")
        _validate_existing_console_address(target)
        os.replace(temporary_path, target)
        replaced = True
        temporary_path = None
        published = _console_address_details(target, require_private=True)
        if not _same_file(written_details, published):
            raise ConsoleError("console address marker changed during publication")
        _fsync_console_directory(runtime_root)
    except ConsoleError:
        raise
    except OSError as error:
        raise ConsoleError("console address marker could not be written securely") from error
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None and not replaced:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def remove_console_address(path: Path, console_id: str) -> bool:
    """Remove the marker only while it still names this console instance."""
    if not isinstance(console_id, str) or CONSOLE_ID_PATTERN.fullmatch(console_id) is None:
        return False
    target = Path(path)
    snapshot = _read_console_address_snapshot(target)
    if snapshot is None:
        return False
    payload, observed = snapshot
    current_id = payload.get("console_id")
    if not isinstance(current_id, str) or not hmac.compare_digest(current_id, console_id):
        return False
    try:
        current = _console_address_details(target, require_private=True)
        if not _same_file(observed, current):
            return False
        target.unlink()
        _fsync_console_directory(target.parent)
        return True
    except (OSError, ConsoleError):
        return False


class ConsoleLock:
    """Process-lifetime private advisory lock for one console runtime root."""

    def __init__(self, path: Path, descriptor: int) -> None:
        self.path = path
        self.descriptor = descriptor
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        descriptor = self.descriptor
        self.descriptor = -1
        if descriptor < 0:
            return
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(descriptor)
        except OSError:
            pass


def _busy_console_error(runtime_root: Path) -> ConsoleError:
    marker = read_console_address(console_address_path(runtime_root))
    if marker is not None:
        url = marker.get("url")
        if isinstance(url, str) and _normalize_console_address_url(url) == url:
            return ConsoleError(f"console already running at {url}")
    return ConsoleError("another console is already starting")


def acquire_console_lock(
    runtime_root: Path,
    *,
    access_helper: Any | None = None,
) -> ConsoleLock:
    """Acquire the nonblocking process-lifetime lock for this runtime root."""

    prepared = _prepare_console_runtime_root(runtime_root)
    lock_path = console_lock_path(prepared)
    if not lock_path.is_absolute():
        raise ConsoleError("console lock path must be absolute")

    try:
        before = os.lstat(lock_path)
    except FileNotFoundError:
        before = None
    except OSError as error:
        raise ConsoleError("console lock is unavailable") from error
    if before is not None and (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or _is_reparse_point(before)
    ):
        raise ConsoleError("console lock is not a safe regular file")

    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    locked = False
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _is_reparse_point(opened):
            raise ConsoleError("console lock is not a safe regular file")
        current = os.lstat(lock_path)
        if before is not None and before.st_ino and current.st_ino and (
            before.st_dev != current.st_dev or before.st_ino != current.st_ino
        ):
            raise ConsoleError("console lock changed while it was opened")
        if opened.st_ino and current.st_ino and (
            opened.st_dev != current.st_dev or opened.st_ino != current.st_ino
        ):
            raise ConsoleError("console lock changed while it was opened")
        os.chmod(lock_path, 0o600)
        _protect_console_path(lock_path, access_helper, kind="lock")
        if os.name != "nt" and stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
            raise ConsoleError("console lock is not private")

        if os.name == "nt":
            import msvcrt

            if opened.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
                opened = os.fstat(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        holder = ConsoleLock(lock_path, descriptor)
        descriptor = -1
        return holder
    except ConsoleError:
        raise
    except OSError as error:
        if error.errno in _LOCK_BUSY_ERRNOS:
            raise _busy_console_error(prepared) from None
        raise ConsoleError("console lock could not be acquired") from error
    finally:
        if descriptor >= 0:
            if locked:
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(descriptor)
            except OSError:
                pass


def default_site_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "console" / "dist"


def listening_loopback_ports() -> set[int]:
    """TCP ports with a listener on loopback, or empty if they cannot be listed."""
    if os.name == "nt":
        return _listening_ports_netstat()
    ports = _listening_ports_proc()
    if ports:
        return ports
    return _listening_ports_netstat()


def _parse_listen_port(address: str) -> int | None:
    text = address.strip()
    if text.startswith("[") and "]" in text:
        host = text[1:text.index("]")].lower()
        rest = text[text.index("]") + 1:]
        if not rest.startswith(":"):
            return None
        port_text = rest[1:]
    elif text.count(":") == 1:
        host, port_text = text.split(":", 1)
        host = host.lower()
    else:
        return None
    try:
        port = int(port_text)
    except ValueError:
        return None
    if not 0 < port < 65536:
        return None
    if host in {"127.0.0.1", "localhost", "::1", "0.0.0.0", "*", "::"}:
        return port
    return None


def _listening_ports_netstat() -> set[int]:
    command = (
        ["netstat", "-ano", "-p", "TCP"]
        if os.name == "nt"
        else ["netstat", "-lnt"]
    )
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    ports: set[int] = set()
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if "listen" not in lowered:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        # Local address is the second field on both Windows netstat -ano
        # and POSIX netstat -lnt.
        port = _parse_listen_port(parts[1])
        if port is not None:
            ports.add(port)
    return ports


def _proc_hex_port(local_address: str) -> int | None:
    if ":" not in local_address:
        return None
    host_hex, port_hex = local_address.rsplit(":", 1)
    try:
        port = int(port_hex, 16)
    except ValueError:
        return None
    if not 0 < port < 65536:
        return None
    host_hex = host_hex.lower()
    loopback = {
        "0100007f",
        "7f000001",
        "00000000000000000000000001000000",
        "0000000000000000ffff00000100007f",
    }
    wildcard = {
        "00000000",
        "00000000000000000000000000000000",
    }
    if host_hex not in loopback and host_hex not in wildcard:
        # IPv4-mapped and native IPv6 loopback appear with different word
        # order depending on the kernel, so also accept any ::1 shape.
        if host_hex.endswith("000000000000000000000001") or host_hex == "0100007f":
            return port
        return None
    return port


def _listening_ports_proc() -> set[int]:
    ports: set[int] = set()
    for path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            raw = path.read_text(encoding="ascii", errors="replace")
        except OSError:
            continue
        for line in raw.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 4:
                continue
            # 0A is TCP_LISTEN.
            if parts[3] != "0A":
                continue
            port = _proc_hex_port(parts[1])
            if port is not None:
                ports.add(port)
    return ports


def fetch_router_json(
    url: str,
    path: str,
    timeout: float = ROUTER_FETCH_TIMEOUT_SECONDS,
) -> tuple[str, dict[str, Any]] | None:
    """GET a loopback router path. Returns (Server header, object) or None."""
    if not valid_router_url(url):
        return None
    parsed = urlsplit(url)
    connection: http.client.HTTPConnection | None = None
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", parsed.port, timeout=timeout
        )
        connection.request("GET", path, headers={"accept": "application/json"})
        response = connection.getresponse()
        server = response.getheader("Server") or ""
        length_header = response.getheader("Content-Length")
        if length_header is not None:
            try:
                length = int(length_header)
            except ValueError:
                return None
            if length < 0 or length > MAX_ROUTER_BODY_BYTES:
                response.read(1)
                return None
        body = response.read(MAX_ROUTER_BODY_BYTES + 1)
        if len(body) > MAX_ROUTER_BODY_BYTES:
            return None
        if response.status != 200:
            return None
        payload = parse_json_object(body)
        if payload is None:
            return None
        return server, payload
    except (OSError, http.client.HTTPException, ValueError, TimeoutError, RecursionError, OverflowError):
        return None
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass


def probe_airlock_router(url: str, timeout: float = ROUTER_FETCH_TIMEOUT_SECONDS) -> dict[str, Any] | None:
    fetched = fetch_router_json(url, "/healthz", timeout=timeout)
    if fetched is None:
        return None
    server, payload = fetched
    if not server.startswith("AirlockRouter"):
        return None
    if payload.get("ok") is not True:
        return None
    instance_id = bounded_str(payload.get("instance_id"), MAX_INSTANCE_ID_CHARS)
    if instance_id is None or INSTANCE_ID_PATTERN.match(instance_id) is None:
        return None
    return payload


def parse_control_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if len(value) < MIN_CONTROL_TOKEN_CHARS or len(value) > MAX_CONTROL_TOKEN_CHARS:
        return None
    if CONTROL_TOKEN_PATTERN.fullmatch(value) is None:
        return None
    # The token is a random secret by design, so the credential-prefix
    # heuristic used for displayed strings must not apply here: a token that
    # happens to start with "sk_" would otherwise hide a live session.
    return value


def validate_registry_payload(
    payload: object, filename: str
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    schema_version = payload.get("schema_version")
    if schema_version not in {1, 2}:
        return None
    instance_id = bounded_str(payload.get("instance_id"), MAX_INSTANCE_ID_CHARS)
    if instance_id is None or INSTANCE_ID_PATTERN.match(instance_id) is None:
        return None
    if Path(filename).stem != instance_id:
        return None
    if not valid_router_url(payload.get("url")):
        return None
    owner_pid = positive_int(payload.get("owner_pid"))
    router_pid = positive_int(payload.get("router_pid"))
    if owner_pid is None or router_pid is None:
        return None
    started_at = payload.get("started_at")
    if parse_timestamp(started_at) is None:
        return None
    profile = safe_profile(payload.get("profile"))
    root_model = safe_model(payload.get("root_model"))
    root_provider = safe_provider(payload.get("root_provider"))
    workdir = bounded_str(payload.get("workdir"))
    if None in {profile, root_model, root_provider, workdir}:
        return None
    parsed = {
        "schema_version": schema_version,
        "instance_id": instance_id,
        "url": str(payload["url"]).rstrip("/"),
        "owner_pid": owner_pid,
        "router_pid": router_pid,
        "started_at": started_at,
        "profile": profile,
        "root_model": root_model,
        "root_provider": root_provider,
        "workdir": workdir,
    }
    if schema_version == 2:
        control_token = parse_control_token(payload.get("control_token"))
        if control_token is None:
            return None
        parsed["control_token"] = control_token
    return parsed


def load_registry_files(directory: Path) -> list[dict[str, Any]]:
    try:
        if not directory.is_dir() or directory.is_symlink():
            return []
        names = sorted(directory.iterdir())
    except OSError:
        return []
    entries: list[dict[str, Any]] = []
    for path in names:
        if path.suffix != ".json" or path.name.endswith(".tmp.json"):
            continue
        raw = open_regular_file(path, MAX_REGISTRY_BYTES)
        if raw is None:
            continue
        payload = parse_json(raw)
        entry = validate_registry_payload(payload, path.name)
        if entry is not None:
            entries.append(entry)
    return entries


def filter_usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    usage: dict[str, int] = {}
    for key in USAGE_FIELDS:
        if key not in value:
            continue
        number = non_negative_int(value.get(key))
        if number is None:
            continue
        usage[key] = number
    return usage or None


def filter_event(raw: object, now: datetime | None = None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    stamp = parse_timestamp(raw.get("timestamp"))
    if stamp is None:
        return None
    if now is not None and stamp > now + timedelta(seconds=FUTURE_SKEW_SECONDS):
        return None
    event: dict[str, Any] = {"timestamp": raw["timestamp"], "_at": stamp}
    for key in EVENT_KEYS:
        if key == "timestamp" or key not in raw:
            continue
        value = raw[key]
        if key == "usage":
            usage = filter_usage(value)
            if usage is not None:
                event["usage"] = usage
            continue
        if key == "status":
            number = http_status_code(value)
            if number is not None:
                event[key] = number
            continue
        if key in {"models_considered", "duration_ms"}:
            number = non_negative_int(value)
            if number is not None:
                event[key] = number
            continue
        if key == "remaining_seconds":
            remaining = coerce_remaining(value)
            if remaining is not None:
                event[key] = remaining
            continue
        if key == "kind":
            kind = safe_kind(value)
            if kind is not None:
                event[key] = kind
            continue
        if key in {"model", "failover_from", "to_model", "from_model"}:
            model = safe_model(value)
            if model is not None:
                event[key] = model
            continue
        if key == "provider":
            provider = safe_provider(value)
            if provider is not None:
                event[key] = provider
            continue
        if key == "outcome":
            outcome = safe_outcome(value)
            if outcome is not None:
                event[key] = outcome
            continue
        if key == "reason":
            reason = safe_reason(value)
            if reason is not None:
                event[key] = reason
    if "to_model" not in event:
        target = safe_model(raw.get("target_model"))
        if target is not None:
            event["to_model"] = target
    return event


def public_event(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key in EVENT_KEYS}


def filter_events(raw: object, now: datetime | None = None) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    events = [event for event in (filter_event(item, now) for item in raw) if event]
    events.sort(key=lambda item: (item["_at"], item["timestamp"]))
    for event in events:
        event.pop("_at", None)
    return events


def model_ids_equivalent(left: object, right: object) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return wire_model_id(left) == wire_model_id(right)


def preferred_public_model(existing: str | None, candidate: str) -> str:
    if existing is None:
        return candidate
    if candidate.endswith("[1m]") and not existing.endswith("[1m]"):
        return candidate
    return existing


def _merge_route_facts(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current)
    merged["model"] = preferred_public_model(current.get("model"), incoming["model"])
    for key in ("provider", "context_window", "effort_ceiling"):
        if merged.get(key) in {None} and incoming.get(key) not in {None}:
            merged[key] = incoming.get(key)
    if merged.get("category") in {None, "unknown"} and incoming.get("category") not in {None, "unknown"}:
        merged["category"] = incoming.get("category")
    if merged.get("metered") is None and incoming.get("metered") is not None:
        merged["metered"] = incoming.get("metered")
    return merged


def _provider_for_model(
    model: str,
    *,
    root_model: str | None,
    root_provider: str | None,
    routes: list[dict[str, Any]],
    summary: list[dict[str, Any]],
) -> str | None:
    for route in routes:
        route_model = route.get("model")
        if isinstance(route_model, str) and model_ids_equivalent(route_model, model):
            if isinstance(route.get("provider"), str):
                return route["provider"]
    for row in summary:
        row_model = row.get("model")
        if isinstance(row_model, str) and model_ids_equivalent(row_model, model):
            if isinstance(row.get("provider"), str):
                return row["provider"]
    if root_model and model_ids_equivalent(model, root_model) and root_provider:
        return root_provider
    return None


def parse_diagnostic_routes(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    routes: list[dict[str, Any]] = []
    index_by_wire: dict[str, int] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        model = safe_model(item.get("model"))
        if model is None:
            continue
        provider = safe_provider(item.get("provider"))
        category = item.get("category")
        if category not in ROUTE_CATEGORIES:
            category = "unknown"
        metered = item.get("metered")
        if not isinstance(metered, bool):
            metered = None
        window = positive_int(item.get("context_window"))
        effort = safe_effort(item.get("effort_ceiling"))
        incoming = {
            "model": model,
            "provider": provider,
            "category": category,
            "metered": metered,
            "context_window": window,
            "effort_ceiling": effort,
        }
        wire = wire_model_id(model)
        existing_index = index_by_wire.get(wire)
        if existing_index is None:
            index_by_wire[wire] = len(routes)
            routes.append(incoming)
            continue
        routes[existing_index] = _merge_route_facts(routes[existing_index], incoming)
    return routes


def fallback_routes_from_models(
    models: list[str],
    *,
    root_model: str | None,
    root_provider: str | None,
    summary: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    index_by_wire: dict[str, int] = {}
    for model in models:
        model = safe_model(model)
        if model is None:
            continue
        provider = _provider_for_model(
            model,
            root_model=root_model,
            root_provider=root_provider,
            routes=[],
            summary=summary,
        )
        incoming = {
            "model": model,
            "provider": provider,
            "category": "unknown",
            "metered": None,
            "context_window": None,
            "effort_ceiling": None,
        }
        wire = wire_model_id(model)
        existing_index = index_by_wire.get(wire)
        if existing_index is None:
            index_by_wire[wire] = len(routes)
            routes.append(incoming)
            continue
        routes[existing_index] = _merge_route_facts(routes[existing_index], incoming)
    return routes


def _merge_usage_row(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current)
    merged["model"] = preferred_public_model(current.get("model"), incoming["model"])
    if merged.get("provider") in {None} and incoming.get("provider") not in {None}:
        merged["provider"] = incoming.get("provider")
    for key in USAGE_SUMMARY_KEYS:
        if key in {"provider", "model"}:
            continue
        if merged.get(key) is None and incoming.get(key) is not None:
            merged[key] = incoming.get(key)
    return merged


def parse_summary(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    index_by_wire: dict[str, int] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        model = safe_model(item.get("model"))
        provider = safe_provider(item.get("provider"))
        if model is None or provider is None:
            continue
        row: dict[str, Any] = {"provider": provider, "model": model}
        for key in USAGE_SUMMARY_KEYS:
            if key in {"provider", "model"}:
                continue
            if key not in item:
                row[key] = None
                continue
            row[key] = non_negative_int(item.get(key))
        wire = wire_model_id(model)
        existing_index = index_by_wire.get(wire)
        if existing_index is None:
            index_by_wire[wire] = len(rows)
            rows.append(row)
            continue
        rows[existing_index] = _merge_usage_row(rows[existing_index], row)
    return rows


def parse_cooldowns(
    diagnostics: dict[str, Any],
    routes: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    raw = diagnostics.get("cooldowns")
    parsed: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            scope = item.get("scope")
            if scope not in {"model", "provider"}:
                continue
            provider = safe_provider(item.get("provider"))
            model = safe_model(item.get("model"))
            if scope == "model" and model is None:
                continue
            if scope == "provider" and provider is None:
                continue
            remaining = coerce_remaining(item.get("remaining_seconds"))
            if remaining == 0:
                continue
            until = None
            if remaining is not None:
                until = format_timestamp(now + timedelta(seconds=remaining))
            cooldown: dict[str, Any] = {
                "scope": scope,
                "remaining_seconds": remaining,
                "until": until,
            }
            if provider is not None:
                cooldown["provider"] = provider
            if model is not None:
                cooldown["model"] = model
            parsed.append(cooldown)
        return parsed
    models = diagnostics.get("rate_limit_cooldowns")
    providers = diagnostics.get("rate_limit_provider_cooldowns")
    if isinstance(models, list):
        for model in models:
            text = safe_model(model)
            if text is None:
                continue
            provider = _provider_for_model(
                text,
                root_model=safe_model(diagnostics.get("root_model")),
                root_provider=safe_provider(diagnostics.get("root_provider")),
                routes=routes,
                summary=parse_summary(diagnostics.get("summary")),
            )
            parsed.append({
                "scope": "model",
                "model": text,
                "provider": provider,
                "remaining_seconds": None,
                "until": None,
            })
    if isinstance(providers, list):
        for provider in providers:
            text = safe_provider(provider)
            if text is None:
                continue
            parsed.append({
                "scope": "provider",
                "provider": text,
                "remaining_seconds": None,
                "until": None,
            })
    return parsed


def parse_chains(raw: object) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    chains: dict[str, list[str]] = {}
    for key, value in raw.items():
        source = safe_model(key)
        if source is None or not isinstance(value, list):
            continue
        peers = [item for item in (safe_model(peer) for peer in value) if item]
        chains[source] = peers
    return chains


def parse_models_list(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    models: list[str] = []
    index_by_wire: dict[str, int] = {}
    for item in data:
        model = None
        if isinstance(item, dict):
            model = safe_model(item.get("id"))
        elif isinstance(item, str):
            model = safe_model(item)
        if model is None:
            continue
        wire = wire_model_id(model)
        existing_index = index_by_wire.get(wire)
        if existing_index is None:
            index_by_wire[wire] = len(models)
            models.append(model)
            continue
        models[existing_index] = preferred_public_model(models[existing_index], model)
    return models


class ControlError(ConsoleError):
    """Safe control-plane failure with an HTTP status and public type."""

    def __init__(
        self,
        status: int,
        kind: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.message = message
        self.extra = extra or {}


class SessionRecord:
    def __init__(
        self,
        instance_id: str,
        url: str,
        owner_pid: int,
        router_pid: int | None,
        registry: dict[str, Any] | None,
        diagnostics: dict[str, Any] | None = None,
        models: list[str] | None = None,
        ended_at: datetime | None = None,
        source: str = "registry",
        registry_path: Path | None = None,
        control_token: str | None = None,
    ) -> None:
        self.instance_id = instance_id
        self.url = url
        self.owner_pid = owner_pid
        self.router_pid = router_pid
        self.registry = registry
        self.diagnostics = diagnostics
        self.models = list(models or [])
        self.ended_at = ended_at
        self.source = source
        self.registry_path = registry_path
        self.control_token = control_token

    @property
    def controllable(self) -> bool:
        registry = self.registry or {}
        return (
            self.ended_at is None
            and registry.get("schema_version") == 2
            and isinstance(self.control_token, str)
            and parse_control_token(self.control_token) is not None
        )


def sibling_helper_path(filename: str) -> Path:
    return Path(__file__).resolve().with_name(filename)


def load_helper_module(path: Path, module_name: str, required: tuple[str, ...]) -> Any:
    try:
        if path.is_symlink() or not path.is_file():
            raise ConsoleError(f"{module_name} helper is not a regular file")
    except OSError as error:
        raise ConsoleError(f"{module_name} helper is not readable") from error
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ConsoleError(f"{module_name} helper could not be loaded")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise ConsoleError(f"{module_name} helper could not be imported") from error
    for name in required:
        if not hasattr(module, name):
            raise ConsoleError(f"{module_name} helper is incomplete")
    return module


def load_access_helper(path: Path | None = None) -> Any:
    return load_helper_module(
        path or sibling_helper_path("airlock-access.py"),
        "airlock_console_access_helper",
        (
            "read_failover_chains_state",
            "compare_and_swap_failover_chains",
            "AccessError",
        ),
    )


def load_tools_helper(path: Path | None = None) -> Any:
    return load_helper_module(
        path or sibling_helper_path("airlock_console_tools.py"),
        "airlock_console_tools_helper",
        (
            "tool_manifest",
            "validate_tool_call",
            "project_tool_result",
            "ToolContractError",
        ),
    )


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def new_proposal_id(kind: str) -> str:
    prefix = "shp" if kind == "session_handoff" else "ccp"
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def json_content_type(value: str | None) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return value.split(";", 1)[0].strip().lower() == "application/json"


def _ascii_digest(provided: str, expected: str) -> bool:
    try:
        supplied = provided.encode("utf-8")
        wanted = expected.encode("ascii")
    except (UnicodeError, TypeError, ValueError):
        return False
    if len(supplied) != len(wanted):
        hmac.compare_digest(wanted, wanted)
        return False
    return hmac.compare_digest(supplied, wanted)


def host_matches_loopback_port(host: str | None, port: int) -> bool:
    if not isinstance(host, str) or not host:
        return False
    expected = f"127.0.0.1:{port}"
    return _ascii_digest(host.strip().lower(), expected)


def origin_matches_console(origin: str | None, port: int) -> bool:
    if not isinstance(origin, str) or not origin:
        return False
    expected = f"http://127.0.0.1:{port}"
    return _ascii_digest(origin, expected)


def host_header_hostname(host: str | None) -> str | None:
    if not isinstance(host, str) or not host.strip():
        return None
    text = host.strip()
    if text.startswith("["):
        closing = text.find("]")
        if closing < 0:
            return None
        return text[: closing + 1].lower()
    if ":" in text:
        hostname, port = text.rsplit(":", 1)
        if port.isdigit():
            return hostname.lower()
    return text.lower()


def host_is_loopback_ipv4(host: str | None) -> bool:
    return host_header_hostname(host) == "127.0.0.1"


def tokens_match(provided: str | None, expected: str) -> bool:
    if not isinstance(provided, str) or not provided:
        return False
    try:
        supplied = provided.encode("utf-8")
        wanted = expected.encode("utf-8")
    except (UnicodeError, TypeError, ValueError):
        return False
    if len(supplied) != len(wanted):
        hmac.compare_digest(wanted, wanted)
        return False
    return hmac.compare_digest(supplied, wanted)


def wire_model_id(model: str) -> str:
    """Return the provider wire form of a model ID.

    Claude Code strips a trailing ``[1m]`` instruction before the request
    reaches the provider, so ``claude-opus-5`` and ``claude-opus-5[1m]``
    name the same source. Console treats those forms as duplicates on
    chain writes without importing the access helper.
    """
    if model.endswith("[1m]"):
        return model.removesuffix("[1m]")
    return model


def public_chain_map(chains: object) -> dict[str, list[str]]:
    """Project already-stored helper chains for public JSON.

    Mutation inputs must use ``require_chain_map``. This helper never
    invents an empty map from missing input; callers pass helper output.
    """
    if not isinstance(chains, dict):
        return {}
    public: dict[str, list[str]] = {}
    for raw_source, raw_peers in chains.items():
        source = safe_model(raw_source)
        if source is None:
            continue
        if isinstance(raw_peers, (list, tuple)):
            peers = [peer for peer in (safe_model(item) for item in raw_peers) if peer]
        else:
            continue
        public[source] = peers
    return public


def require_chain_map(chains: object) -> dict[str, list[str]]:
    """Strict chain-map validation for proposals and direct saves.

    ``chains`` must be a JSON object. Invalid sources or peers fail
    closed with 400. Sources equivalent under ``wire_model_id`` are
    duplicates. A literal ``{}`` is the only explicit clear.
    """
    if not isinstance(chains, dict):
        raise ControlError(400, "invalid_request", "chains must be a JSON object")
    public: dict[str, list[str]] = {}
    seen_wire_sources: set[str] = set()
    for raw_source, raw_peers in chains.items():
        source = safe_model(raw_source)
        if source is None:
            raise ControlError(400, "invalid_request", "chain source is invalid")
        wire_source = wire_model_id(source)
        if wire_source in seen_wire_sources:
            raise ControlError(
                400, "invalid_request", "chain source duplicates an equivalent model"
            )
        seen_wire_sources.add(wire_source)
        if not isinstance(raw_peers, list):
            raise ControlError(400, "invalid_request", "chain peers must be an array")
        peers: list[str] = []
        seen_wire_peers: set[str] = set()
        for raw_peer in raw_peers:
            peer = safe_model(raw_peer)
            if peer is None:
                raise ControlError(400, "invalid_request", "chain peer is invalid")
            wire_peer = wire_model_id(peer)
            if wire_peer in seen_wire_peers:
                raise ControlError(
                    400, "invalid_request", "chain peer duplicates an equivalent model"
                )
            if wire_peer == wire_source:
                raise ControlError(
                    400, "invalid_request", "chain peer cannot equal its source"
                )
            seen_wire_peers.add(wire_peer)
            peers.append(peer)
        public[source] = peers
    return public


def empty_chain_digest() -> str:
    payload = {"schema_version": 1, "chains": {}}
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inject_csrf_meta(body: bytes, token: str) -> bytes:
    marker = b"</head>"
    index = body.lower().rfind(marker)
    if index < 0:
        return body
    meta = b'<meta name="airlock-csrf" content="' + token.encode("ascii") + b'">'
    return body[:index] + meta + body[index:]


def public_error_payload(kind: str, message: str) -> dict[str, Any]:
    return {
        "type": "error",
        "error": {"type": kind, "message": message[:400]},
    }


class RouterControlClient:
    """Loopback pin/unpin client. Tokens never appear in returned values."""

    def __init__(self, timeout: float = ROUTER_FETCH_TIMEOUT_SECONDS) -> None:
        self.timeout = timeout

    def pin(
        self, url: str, token: str, instance_id: str, model: str
    ) -> dict[str, Any]:
        return self._call(
            url,
            token,
            instance_id,
            "/control/pin",
            {"model": model},
            "pin",
            requested_model=model,
        )

    def unpin(self, url: str, token: str, instance_id: str) -> dict[str, Any]:
        return self._call(
            url, token, instance_id, "/control/unpin", {}, "unpin", requested_model=None
        )

    def _call(
        self,
        url: str,
        token: str,
        instance_id: str,
        path: str,
        payload: dict[str, Any],
        action: str,
        requested_model: str | None = None,
    ) -> dict[str, Any]:
        if not valid_router_url(url):
            raise ControlError(404, "not_found", "Router is not reachable")
        if parse_control_token(token) is None:
            raise ControlError(403, "forbidden", "Control token is missing")
        parsed = urlsplit(url)
        body = json.dumps(
            payload, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("utf-8")
        if len(body) > MAX_CONTROL_BODY_BYTES:
            raise ControlError(413, "request_too_large", "Control request is too large")
        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            CONTROL_TOKEN_HEADER: token,
            "origin": f"http://127.0.0.1:{parsed.port}",
        }
        connection: http.client.HTTPConnection | None = None
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", parsed.port, timeout=self.timeout
            )
            connection.request("POST", path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(MAX_CONTROL_RESPONSE_BYTES + 1)
        except (OSError, http.client.HTTPException, TimeoutError) as error:
            raise ControlError(404, "not_found", "Router is not reachable") from error
        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass
        if len(raw) > MAX_CONTROL_RESPONSE_BYTES:
            raise ControlError(413, "request_too_large", "Control response is too large")
        parsed_json = parse_json_object(raw) or {}
        if response.status == 200:
            return self._success(
                parsed_json, instance_id, action, requested_model=requested_model
            )
        kind = None
        error_obj = parsed_json.get("error")
        if isinstance(error_obj, dict):
            kind = bounded_str(error_obj.get("type"), 64)
        if kind is None:
            kind = bounded_str(parsed_json.get("type"), 64)
        mapping = {
            400: kind if kind in {"invalid_request", "model_not_enabled"} else "invalid_request",
            403: "forbidden",
            404: "not_found",
            409: kind if kind in {"route_cooling", "context_too_large"} else "conflict",
            413: "request_too_large",
        }
        safe_kind = mapping.get(response.status, "not_found")
        messages = {
            "invalid_request": "Control request is invalid",
            "model_not_enabled": "Model is not enabled",
            "forbidden": "Control request was refused",
            "not_found": "Router is not reachable",
            "route_cooling": "Target route is cooling",
            "context_too_large": "Conversation does not fit the target window",
            "conflict": "Control request conflicted",
            "request_too_large": "Control request is too large",
        }
        raise ControlError(response.status if response.status in mapping else 404, safe_kind, messages[safe_kind])

    def _success(
        self,
        payload: dict[str, Any],
        instance_id: str,
        action: str,
        requested_model: str | None = None,
    ) -> dict[str, Any]:
        if payload.get("ok") is not True:
            raise ControlError(404, "not_found", "Router is not reachable")
        if "action" not in payload or "instance_id" not in payload:
            raise ControlError(404, "not_found", "Router is not reachable")
        returned_id = bounded_str(payload.get("instance_id"), MAX_INSTANCE_ID_CHARS)
        if returned_id != instance_id:
            raise ControlError(404, "not_found", "Router instance changed")
        returned_action = bounded_str(payload.get("action"), 16)
        if returned_action != action:
            raise ControlError(404, "not_found", "Router is not reachable")
        if "pinned_model" not in payload or "route" not in payload:
            raise ControlError(404, "not_found", "Router is not reachable")
        if action == "pin":
            if requested_model is None:
                raise ControlError(404, "not_found", "Router is not reachable")
            pinned = safe_model(payload.get("pinned_model"))
            route_raw = payload.get("route")
            if not isinstance(route_raw, dict):
                raise ControlError(404, "not_found", "Router is not reachable")
            route_model = safe_model(route_raw.get("model"))
            if pinned != requested_model or route_model != requested_model:
                raise ControlError(404, "not_found", "Router is not reachable")
            context_check = route_raw.get("context_check")
            if context_check not in {"fits", "unknown", "too_large", None}:
                context_check = "unknown"
            return {
                "ok": True,
                "action": "pin",
                "instance_id": instance_id,
                "changed": payload.get("changed") is True,
                "previous_pinned_model": safe_model(payload.get("previous_pinned_model")),
                "pinned_model": pinned,
                "route": {
                    "model": route_model,
                    "provider": safe_provider(route_raw.get("provider")),
                    "context_window": non_negative_int(route_raw.get("context_window")),
                    "context_check": context_check,
                },
            }
        if payload.get("pinned_model") is not None:
            raise ControlError(404, "not_found", "Router is not reachable")
        if payload.get("route") is not None:
            raise ControlError(404, "not_found", "Router is not reachable")
        return {
            "ok": True,
            "action": "unpin",
            "instance_id": instance_id,
            "changed": payload.get("changed") is True,
            "previous_pinned_model": safe_model(payload.get("previous_pinned_model")),
            "pinned_model": None,
            "route": None,
        }


class ChainBackend:
    def __init__(self, helper: Any | None = None, path: Path | None = None) -> None:
        self.helper = helper
        self.path = path

    def snapshot(self) -> dict[str, Any]:
        helper = self.helper
        if helper is None:
            return {
                "chains": None,
                "digest": None,
                "unavailable": True,
                "reason": "helper_unavailable",
                "notice": CHAIN_NOTICE,
            }
        try:
            if self.path is None:
                current = helper.read_failover_chains_state()
            else:
                current = helper.read_failover_chains_state(self.path)
        except Exception:
            return {
                "chains": None,
                "digest": None,
                "unavailable": True,
                "reason": "unreadable",
                "notice": CHAIN_NOTICE,
            }
        chains = public_chain_map(getattr(current, "chains", {}))
        digest = bounded_str(getattr(current, "digest", None), 64)
        if digest is None or SHA256_HEX_PATTERN.fullmatch(digest) is None:
            return {
                "chains": None,
                "digest": None,
                "unavailable": True,
                "reason": "unreadable",
                "notice": CHAIN_NOTICE,
            }
        return {"chains": chains, "digest": digest, "notice": CHAIN_NOTICE}

    def save(self, chains: dict[str, list[str]], expected_digest: str) -> dict[str, Any]:
        helper = self.helper
        if helper is None:
            raise ControlError(404, "not_found", "Chain storage is unavailable")
        if SHA256_HEX_PATTERN.fullmatch(expected_digest) is None:
            raise ControlError(400, "invalid_request", "expected digest is invalid")
        try:
            result = helper.compare_and_swap_failover_chains(
                chains, expected_digest, path=self.path
            )
        except Exception as error:
            conflict_type = getattr(helper, "FailoverConflictError", None)
            is_conflict = (
                isinstance(conflict_type, type)
                and issubclass(conflict_type, BaseException)
                and isinstance(error, conflict_type)
            )
            if not is_conflict and conflict_type is None and "changed" in str(error):
                is_conflict = True
            if is_conflict:
                raise ControlError(
                    409,
                    "chain_conflict",
                    "failover chain changed during this operation",
                    extra={"current": self.snapshot()},
                ) from error
            raise ControlError(400, "invalid_request", "failover chain is invalid") from error
        return {
            "chains": public_chain_map(getattr(result, "chains", chains)),
            "digest": bounded_str(getattr(result, "current_digest", None), 64)
            or empty_chain_digest(),
            "notice": CHAIN_NOTICE,
            "changed": getattr(result, "changed", True) is True,
        }


class Proposal:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.lock = threading.Lock()
        self.data = payload

    def public(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.data))


class ProposalStore:
    def __init__(self, clock: Callable[[], datetime]) -> None:
        self.clock = clock
        self.lock = threading.Lock()
        self._items: dict[str, Proposal] = {}
        self._audit: list[dict[str, Any]] = []

    def summaries(self) -> list[dict[str, Any]]:
        items = []
        for proposal in self._ordered():
            data = proposal.data
            row = {
                "id": data.get("id"),
                "kind": data.get("kind"),
                "status": data.get("status"),
                "created_by": data.get("created_by"),
                "created_at": data.get("created_at"),
                "expires_at": data.get("expires_at"),
                "revision": data.get("revision"),
            }
            if data.get("kind") == "session_handoff":
                row["session_id"] = data.get("session_id")
                row["operation"] = data.get("operation")
                row["target_model"] = data.get("target_model")
            else:
                row["base_digest"] = data.get("base_digest")
            items.append(row)
        return items

    def public_all(self) -> list[dict[str, Any]]:
        return [item.public() for item in self._ordered()]

    def public_one(self, proposal_id: str) -> dict[str, Any] | None:
        proposal = self._items.get(proposal_id)
        if proposal is None:
            return None
        return proposal.public()

    def public_for_session(self, session_id: str) -> list[dict[str, Any]]:
        return [
            item.public()
            for item in self._ordered()
            if item.data.get("session_id") == session_id
        ]

    def current_handoff(self, session_id: str) -> dict[str, Any] | None:
        for item in reversed(self._ordered()):
            data = item.data
            if (
                data.get("kind") == "session_handoff"
                and data.get("session_id") == session_id
                and data.get("status") in ACTIVE_PROPOSAL_STATUSES
            ):
                return item.public()
        return None

    def get(self, proposal_id: str) -> Proposal | None:
        return self._items.get(proposal_id)

    def expire_and_prune(self, now: datetime | None = None) -> bool:
        now = now or self.clock()
        with self.lock:
            return self._expire_and_prune_locked(now)

    def create_handoff(
        self,
        *,
        session_id: str,
        operation: str,
        target_model: str | None,
        reason: str,
        allow_metered: bool,
        created_by: str,
        basis: dict[str, Any],
    ) -> dict[str, Any]:
        now = self.clock()
        payload = {
            "id": new_proposal_id("session_handoff"),
            "kind": "session_handoff",
            "session_id": session_id,
            "operation": operation,
            "target_model": target_model,
            "reason": reason,
            "allow_metered": allow_metered,
            "created_by": created_by,
            "created_at": format_timestamp(now),
            "expires_at": format_timestamp(now + timedelta(seconds=PROPOSAL_TTL_SECONDS)),
            "revision": 1,
            "status": "pending",
            "basis": basis,
            "application": None,
            "last_error": None,
        }
        return self._insert(payload, created_by)

    def create_chain_change(
        self,
        *,
        chains: dict[str, list[str]],
        base_digest: str,
        reason: str,
        created_by: str,
    ) -> dict[str, Any]:
        now = self.clock()
        payload = {
            "id": new_proposal_id("chain_change"),
            "kind": "chain_change",
            "chains": require_chain_map(chains),
            "base_digest": base_digest,
            "reason": reason,
            "created_by": created_by,
            "created_at": format_timestamp(now),
            "expires_at": format_timestamp(now + timedelta(seconds=PROPOSAL_TTL_SECONDS)),
            "revision": 1,
            "status": "pending",
            "application": None,
            "last_error": None,
        }
        return self._insert(payload, created_by)

    def edit(self, proposal_id: str, expected_revision: int, fields: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self._expire_and_prune_locked(self.clock())
            proposal = self._require_locked(proposal_id)
            with proposal.lock:
                data = proposal.data
                if data.get("status") not in EDITABLE_PROPOSAL_STATUSES:
                    raise ControlError(409, "route_state_changed", "Proposal cannot be edited")
                if data.get("revision") != expected_revision:
                    raise ControlError(409, "stale_revision", "Proposal revision does not match")
                if data.get("kind") == "session_handoff":
                    if "target_model" in fields:
                        data["target_model"] = fields["target_model"]
                    if "reason" in fields:
                        data["reason"] = fields["reason"]
                    if "allow_metered" in fields:
                        data["allow_metered"] = fields["allow_metered"]
                else:
                    if "chains" in fields:
                        data["chains"] = require_chain_map(fields["chains"])
                    if "reason" in fields:
                        data["reason"] = fields["reason"]
                    if "base_digest" in fields:
                        data["base_digest"] = fields["base_digest"]
                data["revision"] = expected_revision + 1
                data["status"] = "pending"
                data["last_error"] = None
                data["expires_at"] = format_timestamp(
                    self.clock() + timedelta(seconds=PROPOSAL_TTL_SECONDS)
                )
                self._audit_locked("proposal_edited", proposal, actor_channel="human")
                return proposal.public()

    def reject(self, proposal_id: str) -> dict[str, Any]:
        with self.lock:
            self._expire_and_prune_locked(self.clock())
            proposal = self._require_locked(proposal_id)
            with proposal.lock:
                data = proposal.data
                if data.get("status") != "pending":
                    raise ControlError(409, "route_state_changed", "Proposal cannot be rejected")
                data["status"] = "rejected"
                data["last_error"] = None
                self._audit_locked("proposal_rejected", proposal, actor_channel="human")
                return proposal.public()

    def begin_apply(self, proposal_id: str) -> Proposal:
        with self.lock:
            self._expire_and_prune_locked(self.clock())
            proposal = self._require_locked(proposal_id)
            if not proposal.lock.acquire(blocking=False):
                raise ControlError(
                    409, "application_in_progress", "Proposal is already applying"
                )
            try:
                data = proposal.data
                if data.get("status") == "applied":
                    application = dict(data.get("application") or {})
                    application["already_applied"] = True
                    data["application"] = application
                    return proposal
                if data.get("status") == "applying":
                    raise ControlError(
                        409, "application_in_progress", "Proposal is already applying"
                    )
                if data.get("status") not in {"pending", "failed"}:
                    raise ControlError(409, "route_state_changed", "Proposal cannot be applied")
                expires = parse_timestamp(data.get("expires_at"))
                if expires is not None and expires <= self.clock():
                    data["status"] = "expired"
                    raise ControlError(409, "route_state_changed", "Proposal expired before approval")
                data["status"] = "applying"
                data["application"] = {
                    "attempts": int((data.get("application") or {}).get("attempts") or 0) + 1,
                    "started_at": format_timestamp(self.clock()),
                    "finished_at": None,
                    "router_instance_id": data.get("session_id"),
                    "previous_pinned_model": None,
                    "pinned_model": None,
                    "changed": False,
                    "already_applied": False,
                }
                data["last_error"] = None
                self._audit_locked("proposal_apply_started", proposal, actor_channel="human")
                return proposal
            except Exception:
                proposal.lock.release()
                raise

    def finish_apply(
        self,
        proposal: Proposal,
        *,
        status: str,
        application: dict[str, Any] | None = None,
        error: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            data = proposal.data
            data["status"] = status
            current = dict(data.get("application") or {})
            if application:
                current.update(application)
            current["finished_at"] = format_timestamp(self.clock())
            data["application"] = current
            data["last_error"] = error
            kind = {
                "applied": "proposal_apply_succeeded",
                "failed": "proposal_apply_failed",
                "conflicted": "proposal_apply_conflicted",
            }.get(status)
            if kind:
                self._audit_locked(kind, proposal, actor_channel="human", error_code=(error or {}).get("code"))
            return proposal.public()
        finally:
            if proposal.lock.locked():
                proposal.lock.release()

    def record_chain_direct(self, digest: str) -> None:
        self._audit_locked_simple(
            "chain_directly_applied",
            actor_channel="human",
            extra={"target_model": None, "status": "applied"},
        )

    def audit_log(self) -> list[dict[str, Any]]:
        return list(self._audit)

    def _insert(self, payload: dict[str, Any], created_by: str) -> dict[str, Any]:
        with self.lock:
            self._expire_and_prune_locked(self.clock())
            self._reject_active_locked(
                payload["kind"],
                session_id=payload.get("session_id") if payload.get("kind") == "session_handoff" else None,
            )
            if len(self._items) >= MAX_PROPOSALS:
                raise ControlError(409, "route_state_changed", "Too many proposals")
            proposal = Proposal(payload)
            self._items[payload["id"]] = proposal
            self._audit_locked(
                "proposal_created",
                proposal,
                actor_channel="agent" if created_by == "agent" else "human",
            )
            return proposal.public()

    def _expire_and_prune_locked(self, now: datetime) -> bool:
        changed = False
        for proposal in list(self._items.values()):
            data = proposal.data
            expires = parse_timestamp(data.get("expires_at"))
            if (
                data.get("status") == "pending"
                and expires is not None
                and expires <= now
            ):
                data["status"] = "expired"
                data["last_error"] = {
                    "code": "expired",
                    "message": "Proposal expired before approval",
                }
                self._audit_locked(
                    "proposal_expired",
                    proposal,
                    actor_channel="console",
                )
                changed = True
        terminal_cutoff = now - timedelta(seconds=PROPOSAL_RETENTION_SECONDS)
        keep: dict[str, Proposal] = {}
        for proposal_id, proposal in self._items.items():
            data = proposal.data
            status = data.get("status")
            if status in {"applied", "rejected", "expired", "failed", "conflicted", "superseded"}:
                finished = parse_timestamp(
                    (data.get("application") or {}).get("finished_at")
                ) or parse_timestamp(data.get("expires_at"))
                if finished is not None and finished < terminal_cutoff:
                    changed = True
                    continue
            keep[proposal_id] = proposal
        if len(keep) > MAX_PROPOSALS:
            ordered = sorted(
                keep.values(),
                key=lambda item: item.data.get("created_at") or "",
            )
            overflow = len(keep) - MAX_PROPOSALS
            dropped = 0
            remaining: dict[str, Proposal] = {}
            for proposal in ordered:
                status = proposal.data.get("status")
                if (
                    dropped < overflow
                    and status not in {"pending", "applying"}
                ):
                    dropped += 1
                    changed = True
                    continue
                remaining[proposal.data["id"]] = proposal
            keep = remaining
        self._items = keep
        return changed

    def _reject_active_locked(self, kind: str, session_id: str | None = None) -> None:
        for proposal in self._items.values():
            data = proposal.data
            if data.get("kind") != kind:
                continue
            if session_id is not None and data.get("session_id") != session_id:
                continue
            if data.get("status") in ACTIVE_PROPOSAL_STATUSES:
                raise ControlError(
                    409, "active_proposal_exists", "An active proposal already exists"
                )

    def _require(self, proposal_id: str) -> Proposal:
        with self.lock:
            return self._require_locked(proposal_id)

    def _require_locked(self, proposal_id: str) -> Proposal:
        if not isinstance(proposal_id, str) or PROPOSAL_ID_PATTERN.fullmatch(proposal_id) is None:
            raise ControlError(404, "not_found", "Proposal not found")
        proposal = self._items.get(proposal_id)
        if proposal is None:
            raise ControlError(404, "not_found", "Proposal not found")
        return proposal

    def _ordered(self) -> list[Proposal]:
        return sorted(
            self._items.values(),
            key=lambda item: (item.data.get("created_at") or "", item.data.get("id") or ""),
        )

    def _audit_locked(
        self,
        kind: str,
        proposal: Proposal,
        *,
        actor_channel: str,
        error_code: str | None = None,
    ) -> None:
        data = proposal.data
        self._audit.append({
            "at": format_timestamp(self.clock()),
            "kind": kind,
            "actor_channel": actor_channel,
            "proposal_id": data.get("id"),
            "proposal_kind": data.get("kind"),
            "session_id": data.get("session_id"),
            "revision": data.get("revision"),
            "operation": data.get("operation"),
            "from_model": (data.get("basis") or {}).get("active_model"),
            "target_model": data.get("target_model"),
            "status": data.get("status"),
            "error_code": error_code,
        })
        if len(self._audit) > 256:
            self._audit = self._audit[-256:]

    def _audit_locked_simple(
        self,
        kind: str,
        *,
        actor_channel: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        row = {
            "at": format_timestamp(self.clock()),
            "kind": kind,
            "actor_channel": actor_channel,
            "proposal_id": None,
            "proposal_kind": "chain_change",
            "session_id": None,
            "revision": None,
            "operation": None,
            "from_model": None,
            "target_model": None,
            "status": "applied",
            "error_code": None,
        }
        if extra:
            row.update(extra)
        self._audit.append(row)


def validate_reason_text(value: object) -> str:
    if not isinstance(value, str):
        raise ControlError(400, "invalid_request", "reason must be a string")
    if len(value) < MIN_REASON_CHARS or len(value) > MAX_REASON_CHARS:
        raise ControlError(400, "invalid_request", "reason length is out of bounds")
    if any(ord(char) < 32 for char in value) or not value.strip():
        raise ControlError(400, "invalid_request", "reason must contain visible text")
    return value


def _event_at(event: dict[str, Any] | None) -> datetime | None:
    if event is None:
        return None
    stamp = event.get("_at")
    if isinstance(stamp, datetime):
        return stamp
    return parse_timestamp(event.get("timestamp"))


def _newest_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not events:
        return None
    return max(events, key=lambda item: (_event_at(item) or datetime.min.replace(tzinfo=timezone.utc), item.get("timestamp") or ""))


def _event_in_window(
    event: dict[str, Any] | None, now: datetime, seconds: float
) -> bool:
    if event is None:
        return False
    stamp = _event_at(event)
    if stamp is None:
        return False
    skew = timedelta(seconds=FUTURE_SKEW_SECONDS)
    if stamp > now + skew:
        return False
    age = (now - stamp).total_seconds()
    if age < 0:
        # Within the documented future-skew allowance: treat as current.
        return True
    return age <= seconds


def last_request_time(
    diagnostics: dict[str, Any] | None,
    events: list[dict[str, Any]],
    now: datetime,
) -> datetime | None:
    latest: datetime | None = None
    if diagnostics is not None:
        stamp = timestamp_usable(diagnostics.get("last_request_at"), now)
        if stamp is not None:
            latest = stamp
    for event in events:
        if "status" not in event or "outcome" not in event:
            continue
        stamp = _event_at(event)
        if stamp is None or stamp > now + timedelta(seconds=FUTURE_SKEW_SECONDS):
            continue
        if latest is None or stamp > latest:
            latest = stamp
    return latest


def derive_active_model(
    diagnostics: dict[str, Any] | None,
    registry: dict[str, Any] | None,
) -> str | None:
    pinned = None
    if diagnostics is not None:
        pinned = safe_model(diagnostics.get("pinned_model"))
        if pinned is not None:
            return pinned
        root = safe_model(diagnostics.get("root_model"))
        if root is not None:
            return root
    if registry is not None:
        return safe_model(registry.get("root_model"))
    return None


def cooldown_remaining_for(
    model: str | None,
    provider: str | None,
    cooldowns: list[dict[str, Any]],
) -> tuple[int | None, bool, bool]:
    """Return (remaining, model_hit, provider_hit). remaining None means unknown."""
    model_hit = False
    provider_hit = False
    remainings: list[int] = []
    for item in cooldowns:
        scope = item.get("scope")
        remaining = item.get("remaining_seconds")
        expired = is_int(remaining) and remaining <= 0
        if (
            scope == "model"
            and model
            and isinstance(item.get("model"), str)
            and model_ids_equivalent(item.get("model"), model)
        ):
            if expired:
                continue
            model_hit = True
            if is_int(remaining) and remaining > 0:
                remainings.append(remaining)
        if scope == "provider" and provider and item.get("provider") == provider:
            if expired:
                continue
            provider_hit = True
            if is_int(remaining) and remaining > 0:
                remainings.append(remaining)
    if remainings:
        return max(remainings), model_hit, provider_hit
    return None, model_hit, provider_hit


def derive_state(
    *,
    ended: bool,
    active_model: str | None,
    active_provider: str | None,
    cooldowns: list[dict[str, Any]],
    events: list[dict[str, Any]],
    last_activity: datetime | None,
    now: datetime,
) -> tuple[str, str | None]:
    if ended:
        return "ended", None
    overflow_reason = unresolved_overflow_reason(events, now)
    chain_reason = unresolved_chain_reason(events, now)
    if overflow_reason is not None:
        return "blocked", overflow_reason
    if chain_reason is not None:
        return "blocked", chain_reason
    remaining, model_hit, provider_hit = cooldown_remaining_for(
        active_model, active_provider, cooldowns
    )
    cooling = model_hit or provider_hit or (remaining is not None and remaining > 0)
    if cooling:
        # A provider cooldown gates the active model even when the model
        # itself also has a timer. Chain exhaustion already won above.
        if provider_hit:
            return "blocked", "provider_cooldown"
        return "blocked", "rate_limit"
    if last_activity is not None:
        age = (now - last_activity).total_seconds()
        if age <= RUNNING_WINDOW_SECONDS:
            return "running", None
    return "idle", None


def overflow_resolved_after(event: dict[str, Any], later: dict[str, Any]) -> bool:
    later_at = _event_at(later)
    event_at = _event_at(event)
    if later_at is None or event_at is None or later_at <= event_at:
        return False
    if later.get("kind") in HANDOFF_SUCCESS_KINDS:
        return True
    return later.get("outcome") == "completed"


def unresolved_overflow_reason(events: list[dict[str, Any]], now: datetime) -> str | None:
    blocking: dict[str, Any] | None = None
    for event in events:
        if event.get("kind") not in OVERFLOW_BLOCK_KINDS:
            continue
        if not _event_in_window(event, now, BLOCK_EVENT_WINDOW_SECONDS):
            continue
        blocking = event
    if blocking is None:
        return None
    for event in events:
        if overflow_resolved_after(blocking, event):
            return None
    return "context_overflow"


def unresolved_chain_reason(events: list[dict[str, Any]], now: datetime) -> str | None:
    blocking: dict[str, Any] | None = None
    for event in events:
        if event.get("kind") not in CHAIN_EXHAUSTED_KINDS:
            continue
        if not _event_in_window(event, now, BLOCK_EVENT_WINDOW_SECONDS):
            continue
        blocking = event
    if blocking is None:
        return None
    for event in events:
        if overflow_resolved_after(blocking, event):
            return None
    return "chain_exhausted"


def derive_workers(summary: list[dict[str, Any]], active_model: str | None) -> list[dict[str, Any]]:
    workers: list[dict[str, Any]] = []
    for row in summary:
        if row.get("model") == active_model:
            continue
        if (
            isinstance(row.get("model"), str)
            and active_model
            and model_ids_equivalent(row["model"], active_model)
        ):
            continue
        workers.append({
            "model": row["model"],
            "requests": row.get("requests"),
        })
    return workers


def context_from_events(events: list[dict[str, Any]]) -> tuple[int | None, str | None]:
    """The input side of the most recent completed request, if usage was recorded."""
    for event in reversed(events):
        if event.get("outcome") != "completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        total = 0
        found = False
        for key in (
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            value = non_negative_int(usage.get(key))
            if value is None:
                continue
            total += value
            found = True
        if found:
            return total, safe_model(event.get("model"))
    return None, None


def derive_context(
    diagnostics: dict[str, Any] | None,
    routes: list[dict[str, Any]],
    active_model: str | None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    input_tokens = None
    window = None
    model = active_model
    if diagnostics is not None:
        raw = diagnostics.get("context")
        if isinstance(raw, dict):
            input_tokens = non_negative_int(raw.get("input_tokens"))
            context_model = safe_model(raw.get("model"))
            if context_model is not None:
                model = context_model
    if input_tokens is None and events:
        event_tokens, event_model = context_from_events(events)
        input_tokens = event_tokens
        if model is None and event_model is not None:
            model = event_model
    if model is not None:
        for route in routes:
            route_model = route.get("model")
            if isinstance(route_model, str) and model_ids_equivalent(route_model, model):
                window = route.get("context_window")
                break
    return {"input_tokens": input_tokens, "window": window}


def derive_last_handoff(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    last: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None
    for event in events:
        kind = event.get("kind")
        if kind in HANDOFF_ATTEMPT_KINDS:
            pending = {
                "at": event.get("timestamp"),
                "from_model": event.get("failover_from") or event.get("from_model"),
                "to_model": event.get("to_model") or event.get("model"),
                "reason": "overflow" if kind in OVERFLOW_HANDOFF_KINDS else "rate_limit",
            }
            continue
        if kind in HANDOFF_SUCCESS_KINDS:
            last = {
                "at": (pending or {}).get("at") or event.get("timestamp"),
                "from_model": (
                    event.get("from_model")
                    or event.get("failover_from")
                    or (pending or {}).get("from_model")
                ),
                "to_model": (
                    event.get("to_model")
                    or event.get("model")
                    or (pending or {}).get("to_model")
                ),
                "reason": "overflow" if kind in OVERFLOW_HANDOFF_KINDS else "rate_limit",
            }
            pending = None
            continue
        if event.get("outcome") == "completed" and event.get("failover_from"):
            if pending and pending.get("from_model") == event.get("failover_from"):
                last = dict(pending)
            else:
                last = {
                    "at": event.get("timestamp"),
                    "from_model": event.get("failover_from"),
                    "to_model": event.get("model"),
                    "reason": (pending or {}).get("reason") or "rate_limit",
                }
            pending = None
    if last is None:
        return None
    if not last.get("from_model") or not last.get("to_model") or not last.get("at"):
        return None
    return last


def recent_handoff_count(events: list[dict[str, Any]], now: datetime) -> int:
    del now
    count = 0
    for event in events:
        kind = event.get("kind")
        if kind in HANDOFF_ATTEMPT_KINDS or kind in HANDOFF_SUCCESS_KINDS:
            count += 1
    return count


def attention_summary(state: str, reason: str | None, active_model: str | None) -> str:
    name = display_name(active_model)
    if reason == "chain_exhausted":
        return f"{name} is rate limited; chain exhausted"
    if reason == "rate_limit":
        return f"{name} is rate limited"
    if reason == "provider_cooldown":
        return f"{name}'s provider is in cooldown"
    if reason == "context_overflow":
        return "Conversation does not fit any enabled model"
    return f"{name} is blocked"


def route_runtime_status(
    model: str,
    provider: str | None,
    cooldowns: list[dict[str, Any]],
) -> tuple[str, int | None]:
    remaining, model_hit, provider_hit = cooldown_remaining_for(
        model, provider, cooldowns
    )
    if remaining is not None and remaining > 0:
        return "cooling", remaining
    if model_hit or provider_hit:
        # The router reported a cooldown without a timer. That is still a
        # skip, but the page cannot honestly count it down.
        return "cooling", None
    return "ready", None


def build_route_status(
    route: dict[str, Any],
    *,
    cooldowns: list[dict[str, Any]],
    sessions_using: list[str],
    input_tokens: int | None | object = Ellipsis,
) -> dict[str, Any]:
    model = route["model"]
    provider = route.get("provider")
    status, remaining = route_runtime_status(model, provider, cooldowns)
    payload: dict[str, Any] = {
        "model": model,
        "short_name": short_name(model),
        "provider": provider,
        "category": route.get("category") or "unknown",
        "metered": route.get("metered"),
        "context_window": route.get("context_window"),
        "effort_ceiling": route.get("effort_ceiling"),
        "status": status,
        "cooldown_remaining_seconds": remaining,
        "sessions_using": sessions_using,
    }
    if input_tokens is not Ellipsis:
        window = route.get("context_window")
        if input_tokens is None or window is None:
            payload["fits_context"] = None
        else:
            payload["fits_context"] = input_tokens <= window
    return payload


STATE_ORDER = {"blocked": 0, "running": 1, "idle": 2, "ended": 3}


def derive_session_view(
    record: SessionRecord,
    now: datetime,
    *,
    include_detail: bool,
    sessions_using: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    registry = record.registry or {}
    diagnostics = record.diagnostics if isinstance(record.diagnostics, dict) else None
    summary = parse_summary(diagnostics.get("summary") if diagnostics else None)
    routes = []
    if diagnostics is not None:
        routes = parse_diagnostic_routes(diagnostics.get("routes"))
    if not routes:
        routes = fallback_routes_from_models(
            record.models,
            root_model=safe_model(
                (diagnostics or {}).get("root_model") or registry.get("root_model")
            ),
            root_provider=safe_provider(
                (diagnostics or {}).get("root_provider") or registry.get("root_provider")
            ),
            summary=summary,
        )
    events = filter_events(diagnostics.get("events") if diagnostics else None, now)
    cooldowns = parse_cooldowns(diagnostics or {}, routes, now)
    chains = parse_chains(diagnostics.get("chains") if diagnostics else None)
    profile = safe_profile(
        (diagnostics or {}).get("profile") or registry.get("profile")
    )
    root_model = safe_model(
        (diagnostics or {}).get("root_model") or registry.get("root_model")
    )
    root_provider = safe_provider(
        (diagnostics or {}).get("root_provider") or registry.get("root_provider")
    )
    workdir = bounded_str(
        (diagnostics or {}).get("workdir") or registry.get("workdir")
    )
    started_at = None
    if diagnostics is not None:
        started_at = diagnostics.get("started_at")
        if parse_timestamp(started_at) is None:
            started_at = None
    if started_at is None:
        started_at = registry.get("started_at")
        if parse_timestamp(started_at) is None:
            started_at = None
    active_model = derive_active_model(diagnostics, record.registry)
    active_provider = _provider_for_model(
        active_model or "",
        root_model=root_model,
        root_provider=root_provider,
        routes=routes,
        summary=summary,
    )
    last_request = last_request_time(diagnostics, events, now)
    newest = _newest_event(events)
    newest_at = timestamp_usable(newest.get("timestamp") if newest else None, now)
    started_stamp = timestamp_usable(started_at, now)
    last_activity = last_request or newest_at or started_stamp
    ended = record.ended_at is not None
    state, blocked_reason = derive_state(
        ended=ended,
        active_model=active_model,
        active_provider=active_provider,
        cooldowns=cooldowns,
        events=events,
        last_activity=last_request,
        now=now,
    )
    if blocked_reason not in BLOCKED_REASONS:
        blocked_reason = None if state != "blocked" else blocked_reason
    context = derive_context(diagnostics, routes, active_model, events)
    using = sessions_using or {}
    session = {
        "id": record.instance_id,
        "state": state,
        "blocked_reason": blocked_reason if state == "blocked" else None,
        "profile": profile,
        "root_model": root_model,
        "root_provider": root_provider,
        "active_model": active_model,
        "workdir": workdir,
        "project": project_name(workdir),
        "started_at": started_at,
        "last_activity_at": format_timestamp(last_activity) if last_activity else None,
        "context": context,
        "workers": derive_workers(summary, active_model),
        "recent_handoffs": recent_handoff_count(events, now),
        "url": record.url,
    }
    if include_detail:
        session["routes"] = [
            build_route_status(
                route,
                cooldowns=cooldowns,
                sessions_using=using.get(wire_model_id(route["model"]), []),
                input_tokens=context.get("input_tokens"),
            )
            for route in routes
        ]
        session["cooldowns"] = cooldowns
        session["chains"] = chains
        session["usage"] = [
            {key: row.get(key) for key in USAGE_SUMMARY_KEYS}
            for row in summary
        ]
        session["events"] = [public_event(event) for event in events]
        session["last_handoff"] = derive_last_handoff(events)
    return session


def collect_session_routes(
    records: list[SessionRecord], now: datetime
) -> tuple[list[dict[str, Any]], dict[str, list[str]], dict[str, list[dict[str, Any]]]]:
    using: dict[str, list[str]] = {}
    per_session_routes: dict[str, list[dict[str, Any]]] = {}
    per_session_cooldowns: dict[str, list[dict[str, Any]]] = {}
    summaries: dict[str, dict[str, Any]] = {}
    for record in records:
        try:
            view = derive_session_view(record, now, include_detail=True)
        except (RecursionError, OverflowError, ValueError, TypeError, MemoryError):
            continue
        summaries[record.instance_id] = view
        ended = record.ended_at is not None or view.get("state") == "ended"
        active = view.get("active_model")
        if isinstance(active, str) and not ended:
            bucket = using.setdefault(wire_model_id(active), [])
            if record.instance_id not in bucket:
                bucket.append(record.instance_id)
        per_session_routes[record.instance_id] = []
        diagnostics = record.diagnostics if isinstance(record.diagnostics, dict) else {}
        summary = parse_summary(diagnostics.get("summary"))
        routes = parse_diagnostic_routes(diagnostics.get("routes"))
        if not routes:
            routes = fallback_routes_from_models(
                record.models,
                root_model=view.get("root_model"),
                root_provider=view.get("root_provider"),
                summary=summary,
            )
        cooldowns = parse_cooldowns(diagnostics, routes, now)
        per_session_routes[record.instance_id] = routes
        if not ended:
            per_session_cooldowns[record.instance_id] = cooldowns
    merged: dict[str, dict[str, Any]] = {}
    merged_cooldowns: list[dict[str, Any]] = []
    order: list[str] = []
    for record in records:
        for route in per_session_routes.get(record.instance_id, []):
            model = route["model"]
            wire = wire_model_id(model)
            if wire not in merged:
                merged[wire] = dict(route)
                order.append(wire)
            else:
                merged[wire] = _merge_route_facts(merged[wire], route)
        merged_cooldowns.extend(per_session_cooldowns.get(record.instance_id, []))
    routes = [
        build_route_status(
            merged[wire],
            cooldowns=merged_cooldowns,
            sessions_using=using.get(wire, []),
        )
        for wire in order
    ]
    return routes, using, summaries


def derive_headroom(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    providers: list[str] = []
    seen: set[str] = set()
    for route in routes:
        provider = route.get("provider")
        if not isinstance(provider, str) or provider in seen:
            continue
        seen.add(provider)
        providers.append(provider)
    return [
        {
            "provider": provider,
            "window": HEADROOM_WINDOW.get(provider, "5h"),
            "used_percent": None,
            "resets_at": None,
            "source": "unknown",
        }
        for provider in providers
    ]


def derive_attention(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for session in sessions:
        if session.get("state") != "blocked":
            continue
        items.append({
            "kind": "session_blocked",
            "session_id": session.get("id"),
            "since": session.get("last_activity_at"),
            "summary": attention_summary(
                session.get("state"),
                session.get("blocked_reason"),
                session.get("active_model"),
            ),
        })
    return items


def derive_overview(records: list[SessionRecord], now: datetime) -> dict[str, Any]:
    global_routes, using, _summaries = collect_session_routes(records, now)
    sessions = []
    for record in records:
        try:
            sessions.append(
                derive_session_view(
                    record, now, include_detail=False, sessions_using=using
                )
            )
        except (RecursionError, OverflowError, ValueError, TypeError, MemoryError):
            continue
    sessions.sort(
        key=lambda item: (
            STATE_ORDER.get(item.get("state"), 9),
            parse_timestamp(item.get("last_activity_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
            item.get("id") or "",
        )
    )
    return {
        "generated_at": format_timestamp(now),
        "console_version": CONSOLE_VERSION,
        "sessions": sessions,
        "routes": global_routes,
        "headroom": derive_headroom(global_routes),
        "attention": derive_attention(sessions),
    }


def event_sentence(event: dict[str, Any]) -> str:
    kind = event.get("kind")
    model = event.get("model")
    named = display_name(model) if model else None
    from_model = event.get("failover_from") or event.get("from_model")
    to_model = event.get("to_model") or (
        model if kind in HANDOFF_ATTEMPT_KINDS or kind in HANDOFF_SUCCESS_KINDS else None
    )
    target_name = display_name(to_model) if to_model else named
    considered = event.get("models_considered")
    if kind == "rate_limit_failover_attempted":
        source = display_name(from_model)
        target = display_name(to_model)
        if is_int(considered):
            return (
                f"{source} was rate limited, handed off to {target} after "
                f"considering {considered} routes"
            )
        return f"{source} was rate limited, handed off to {target}"
    if kind == "rate_limit_cooldown_skipped":
        return f"{named or 'A route'} was skipped because it is cooling down"
    if kind == "rate_limit_provider_cooldown":
        provider = event.get("provider") or "the provider"
        return f"{provider} entered a provider cooldown"
    if kind == "rate_limit_chain_exhausted":
        extra = (
            f" after considering {considered} routes" if is_int(considered) else ""
        )
        return f"{named or 'The active model'}'s rate-limit chain is exhausted{extra}"
    if kind == "failover_overflow_attempted":
        return (
            f"{display_name(from_model)} overflowed; trying {display_name(to_model)}"
        )
    if kind == "failover_overflow_skipped":
        reason = event.get("reason")
        suffix = f" ({reason})" if reason else ""
        return f"{named or 'A route'} was skipped because it cannot hold this context{suffix}"
    if kind == "failover_shrink_compacted":
        return f"History was compacted so {target_name or 'the next model'} could fit it"
    if kind == "failover_shrink_truncated":
        return f"History was truncated so {target_name or 'the next model'} could fit it"
    if kind == "failover_shrink_failed":
        return "History could not be compacted or truncated"
    if kind == "overflow_chain_exhausted":
        extra = (
            f" after considering {considered} routes" if is_int(considered) else ""
        )
        return f"The conversation does not fit any enabled model{extra}"
    if kind == "upstream_context_overflow":
        return f"{named or 'The active model'} rejected the request as too large"
    if kind == "openrouter_effort_clamped":
        return f"OpenRouter effort was clamped for {named or 'a route'}"
    if kind == "openrouter_server_tools_stripped":
        return f"Server tools were stripped for {named or 'an OpenRouter route'}"
    if kind == "sanitized_error_substituted":
        return f"A provider error for {named or 'a route'} was replaced with a local message"
    if kind == "background_model_substituted":
        return f"A background model was substituted with {named or 'an enabled route'}"
    if kind == "anthropic_rate_limit_passthrough":
        return "Anthropic rate limit headers were passed through"
    if kind == "model_not_enabled":
        return f"{named or 'A model'} is not enabled for this session"
    if kind == "rate_limit_failover_succeeded":
        source = display_name(from_model)
        target = display_name(to_model)
        if from_model and to_model:
            return f"{target} took over from {source} after a rate limit"
        return "The rate-limit handoff succeeded"
    if kind == "failover_overflow_succeeded":
        source = display_name(from_model)
        target = display_name(to_model)
        if from_model and to_model:
            return (
                f"{target} took over from {source} after the conversation "
                "was too large"
            )
        return "The context-overflow handoff succeeded"
    if kind == "session_root_selected":
        return f"The session started on {named or 'its root model'}"
    if kind == "session_model_pinned":
        return f"Session pinned {named or 'its root model'}"
    if kind == "session_model_unpinned":
        return (
            f"The session returned to its root model from "
            f"{named or 'its pinned model'}"
        )
    if kind == "router_restarted":
        return "The router restarted and in-memory history begins here"
    if kind is None:
        outcome = event.get("outcome")
        status = event.get("status")
        subject = named or "A request"
        if outcome == "completed":
            return f"{subject} completed a request"
        if outcome in {"rate_limited", "upstream_rate_limited"}:
            return f"{subject} was rate limited"
        if outcome:
            extra = f" (status {status})" if is_int(status) else ""
            return f"{subject} ended with {outcome}{extra}"
        if is_int(status):
            return f"{subject} returned status {status}"
        return f"{subject} made a request"
    return str(kind)


def render_report(detail: dict[str, Any]) -> str:
    session_id = markdown_code_span(detail.get("id") or "unknown")
    state = markdown_escape(detail.get("state") or "unknown")
    reason = detail.get("blocked_reason")
    state_text = state if not reason else f"{state} ({markdown_escape(reason)})"
    active = markdown_code_span(detail.get("active_model") or "unknown")
    provider = markdown_code_span(detail.get("root_provider") or "unknown")
    context = detail.get("context") or {}
    tokens = context.get("input_tokens")
    window = context.get("window")
    if tokens is None and window is None:
        context_text = "Context size is unknown."
    elif tokens is None:
        context_text = (
            f"Context window is {markdown_code_span(window)} tokens; used input tokens are unknown."
        )
    elif window is None:
        context_text = (
            f"Context used is {markdown_code_span(tokens)} input tokens; the window is unknown."
        )
    else:
        context_text = (
            f"Context used is {markdown_code_span(tokens)} of {markdown_code_span(window)} input tokens."
        )
    lines = [
        f"# Airlock session {session_id}",
        "",
        f"State: {state_text}.",
        f"Active model: {active} ({provider}).",
        context_text,
        (
            f"Started at {markdown_code_span(detail.get('started_at') or 'unknown')}. "
            f"Last activity at {markdown_code_span(detail.get('last_activity_at') or 'unknown')}."
        ),
        f"Project: {markdown_code_span(detail.get('project') or 'unknown')}.",
        "",
        "## Cooldowns",
        "",
    ]
    cooldowns = detail.get("cooldowns") or []
    if not cooldowns:
        lines.append("No cooldowns are recorded.")
    else:
        for item in cooldowns:
            scope = item.get("scope")
            remaining = item.get("remaining_seconds")
            until = markdown_code_span(item.get("until") or "an unknown time")
            if scope == "provider":
                target = markdown_code_span(item.get("provider") or "a provider")
                subject = f"{target} provider"
            else:
                subject = markdown_code_span(item.get("model") or "a model")
            if remaining is None:
                lines.append(f"- {subject} is cooling until {until}.")
            else:
                lines.append(
                    f"- {subject} is cooling until {until} ({markdown_code_span(remaining)} s left)."
                )
    lines.extend(["", "## Chains", ""])
    snapshot = detail.get("chain_snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}
    if snapshot.get("unavailable") is True:
        lines.append("The handoff chain file could not be read.")
    else:
        chains = detail.get("chains") or {}
        if not chains:
            lines.append("No handoff chains are recorded.")
        else:
            for source, peers in chains.items():
                joined = (
                    ", ".join(markdown_code_span(peer) for peer in peers)
                    if peers
                    else "(empty)"
                )
                lines.append(f"- {markdown_code_span(source)}: {joined}")
    lines.extend(["", "## Timeline", ""])
    events = detail.get("events") or []
    if not events:
        lines.append("No diagnostic events are recorded.")
    else:
        for event in events:
            stamp = markdown_code_span(event.get("timestamp") or "unknown time")
            lines.append(f"- {stamp}: {markdown_escape(event_sentence(event))}.")
    lines.append("")
    return "\n".join(lines)


# ---- native Claude Code sessions ------------------------------------------
# A session started with plain ``claude`` has no Airlock router, so nothing
# writes a registry file for it. It still answers the first question a person
# brings to this page, "what is running on this machine", so the console lists
# it read-only. Claude Code keeps one transcript file per session under
# ``~/.claude/projects/<encoded cwd>/<session id>.jsonl`` and appends a record
# on every turn, each carrying the working directory, the session id, and a
# timestamp. The console reads only the tail of that file, keeps only those
# three fields plus the model name of the last assistant record, and discards
# the rest. Prompts and tool output are never retained or shown.

NATIVE_ID_PREFIX = "cc-"
NATIVE_PROFILE = "claude-code"
NATIVE_TAIL_BYTES = 256 * 1024
NATIVE_TAIL_MAX_BYTES = 8 * 1024 * 1024
NATIVE_MAX_PROJECT_DIRS = 500
NATIVE_MAX_FILES_PER_DIR = 64
NATIVE_MAX_FILES_SCANNED = 4000
NATIVE_MAX_AGE_SECONDS = 12 * 3600
NATIVE_RUNNING_SECONDS = 90
NATIVE_PREVIEW_CHARS = 160
NATIVE_ACTIVITY_ITEMS = 20
HISTORY_REFRESH_SECONDS = 15.0
HISTORY_ID_PREFIX = "hx-"
HISTORY_FACT_KEYS = (
    "id", "session_id", "started_at", "last_activity_at", "prompts", "replies", "tool_calls",
    "compactions", "peak_context", "last_context", "window", "output_tokens", "subagents",
    "models", "periods", "airlock_inferred", "entrypoint", "version",
)
NATIVE_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")


def claude_projects_root() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    root = Path(base) if base else Path.home() / ".claude"
    return root / "projects"


def count_native_claude_processes() -> int | None:
    """How many plain Claude Code processes are alive, or None when unknown."""
    try:
        if os.name == "nt":
            output = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq claude.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5, check=False,
            ).stdout
            return sum(1 for line in output.splitlines() if line.startswith('"claude.exe"'))
        output = subprocess.run(
            ["pgrep", "-x", "claude"], capture_output=True, text=True, timeout=5, check=False,
        ).stdout
        return sum(1 for line in output.splitlines() if line.strip().isdigit())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _normalized_workdir(value: object) -> str | None:
    text = bounded_str(value)
    if not text:
        return None
    return os.path.normcase(os.path.normpath(text.replace("/", os.sep)))


def _native_model_provider(model: str) -> str:
    lowered = model.lower()
    if lowered.startswith("gpt-") or lowered.startswith("o1") or lowered.startswith("o3"):
        return "openai"
    if lowered.startswith("grok-"):
        return "grok"
    if lowered.startswith("openmodel/"):
        return "openmodel"
    if "/" in lowered:
        return "openrouter"
    return "anthropic"


# Windows a native Claude Code session gets for the models it can be on.
# Claude Code grants the Claude 5 family its native 1M window when it talks to
# Anthropic directly, which is exactly the plain-session case.
NATIVE_WINDOWS: tuple[tuple[str, int], ...] = (
    ("claude-haiku-4-5", 200_000),
    ("claude-", 1_000_000),
    ("gpt-6-astra", 922_000),
    ("gpt-", 272_000),
    ("grok-composer", 256_000),
    ("grok-", 500_000),
)


def native_window_for(model: str | None) -> int | None:
    if not model:
        return None
    lowered = model.lower()
    for prefix, window in NATIVE_WINDOWS:
        if lowered.startswith(prefix):
            return window
    return None


def previews_enabled() -> bool:
    return os.environ.get("AIRLOCK_CONSOLE_PREVIEWS", "").strip().lower() != "off"


def _preview(value: object, limit: int = NATIVE_PREVIEW_CHARS) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _tool_hint(name: str, arguments: object) -> str | None:
    """A short, non-sensitive hint of what a tool call was about."""
    if not isinstance(arguments, dict):
        return None
    for key in ("description", "file_path", "path", "pattern", "query", "url", "command"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            if key in {"file_path", "path"}:
                value = value.replace("\\", "/").rsplit("/", 1)[-1]
            return _preview(value, 80)
    return None


def _activity_items(
    record: dict[str, Any], stamp: datetime, *, previews: bool, own_chain: bool = False
) -> list[dict[str, Any]]:
    """Activity rows for one transcript record, newest last inside the record.

    ``own_chain`` reads a subagent's own transcript, where every record is a
    sidechain record and is the conversation to show.
    """
    if record.get("isSidechain") is True and not own_chain:
        return []
    message = record.get("message")
    if not isinstance(message, dict):
        return []
    kind = record.get("type")
    content = message.get("content")
    at = format_timestamp(stamp)
    items: list[dict[str, Any]] = []
    if kind == "user":
        if isinstance(content, str):
            # Claude Code injects harness notices as user turns, wrapped in
            # angle-bracket tags. They are not something the person said.
            if content.lstrip().startswith("<"):
                return []
            items.append({"at": at, "kind": "prompt", "preview": _preview(content) if previews else None})
        elif isinstance(content, list):
            texts = [block.get("text") for block in content if isinstance(block, dict) and block.get("type") == "text"]
            if texts:
                items.append({"at": at, "kind": "prompt", "preview": _preview(texts[0]) if previews else None})
        return items
    if kind == "assistant" and isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                items.append({"at": at, "kind": "reply", "preview": _preview(block.get("text")) if previews else None})
            elif block_type == "tool_use":
                name = bounded_str(block.get("name"), 64)
                if name:
                    hint = _tool_hint(name, block.get("input")) if previews else None
                    items.append({"at": at, "kind": "tool", "tool": name, "preview": hint})
    return items


def read_native_transcript_tail(
    path: Path, *, previews: bool | None = None, own_chain: bool = False
) -> dict[str, Any] | None:
    """The session facts the console keeps from a Claude Code transcript.

    Session id, working directory, last timestamp, the last reply's model, the
    session's own title when Claude Code wrote one, the git branch, the token
    usage of the last reply (which is the live context size), and a short
    activity feed of the latest prompts, replies, and tool calls. Previews are
    one line each and can be switched off; nothing else is retained.
    """
    try:
        details = path.stat()
        if not stat.S_ISREG(details.st_mode) or path.is_symlink():
            return None
    except OSError:
        return None
    show_previews = previews_enabled() if previews is None else previews
    facts: dict[str, Any] = {
        "session_id": None, "cwd": None, "last_at": None, "model": None,
        "title": None, "branch": None, "usage": None,
    }
    activity: list[dict[str, Any]] = []
    wanted_activity = NATIVE_ACTIVITY_ITEMS

    def consume(line: bytes) -> bool:
        text = line.strip()
        if not text.startswith(b"{"):
            return False
        try:
            record = json.loads(text.decode("utf-8", "replace"))
        except ValueError:
            return False
        if not isinstance(record, dict):
            return False
        kind = record.get("type")
        if facts["title"] is None and kind == "ai-title":
            facts["title"] = _preview(record.get("aiTitle"), 120)
        stamp = parse_timestamp(record.get("timestamp"))
        if facts["last_at"] is None:
            candidate_id = bounded_str(record.get("sessionId"), 64)
            candidate_cwd = bounded_str(record.get("cwd"))
            if (
                stamp is not None
                and candidate_id
                and NATIVE_SESSION_ID_PATTERN.match(candidate_id)
                and candidate_cwd
            ):
                facts["last_at"], facts["session_id"], facts["cwd"] = stamp, candidate_id, candidate_cwd
        if facts["branch"] is None:
            facts["branch"] = bounded_str(record.get("gitBranch"), 120)
        if kind == "assistant":
            message = record.get("message")
            if isinstance(message, dict):
                if facts["model"] is None:
                    facts["model"] = safe_model(message.get("model"))
                usage = message.get("usage")
                if facts["usage"] is None and isinstance(usage, dict) and (own_chain or not record.get("isSidechain")):
                    parts = [
                        non_negative_int(usage.get(key))
                        for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
                    ]
                    if any(part is not None for part in parts):
                        facts["usage"] = {
                            "context_tokens": sum(part or 0 for part in parts),
                            "output_tokens": non_negative_int(usage.get("output_tokens")),
                        }
        if stamp is not None and len(activity) < wanted_activity:
            # Walking backwards, so prepend keeps oldest first.
            for item in reversed(_activity_items(record, stamp, previews=show_previews, own_chain=own_chain)):
                activity.insert(0, item)
                if len(activity) >= wanted_activity:
                    break
        return (
            facts["last_at"] is not None
            and facts["model"] is not None
            and facts["usage"] is not None
            and len(activity) >= wanted_activity
        )

    # A single tool result can be far larger than one chunk, so walk backwards
    # a chunk at a time and only ever parse complete lines. The total read is
    # bounded so a pathological file cannot hold the poll loop.
    try:
        with path.open("rb") as handle:
            position = details.st_size
            carry = b""
            read_total = 0
            done = False
            while position > 0 and not done and read_total < NATIVE_TAIL_MAX_BYTES:
                step = min(NATIVE_TAIL_BYTES, position)
                position -= step
                handle.seek(position)
                block = handle.read(step) + carry
                read_total += step
                head, sep, rest = block.partition(b"\n")
                lines = rest.split(b"\n") if sep else []
                if position == 0:
                    lines.insert(0, head)
                    carry = b""
                else:
                    carry = head
                for line in reversed(lines):
                    if consume(line):
                        done = True
                        break
    except OSError:
        return None
    if facts["last_at"] is None or facts["session_id"] is None or facts["cwd"] is None:
        return None
    facts["activity"] = activity[-wanted_activity:]
    return facts


def transcript_context(info: dict[str, Any], window: int | None = None) -> dict[str, Any] | None:
    usage = info.get("usage")
    if not isinstance(usage, dict):
        return None
    return {
        "input_tokens": usage.get("context_tokens"),
        "window": window if window is not None else native_window_for(info.get("model")),
    }


def discover_native_sessions(
    now: datetime,
    *,
    projects_root: Path,
    live_count: int | None,
    routed_workdirs: set[str],
    transcripts: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Summaries for plain Claude Code sessions, newest first.

    ``live_count`` bounds the answer to the number of Claude processes that
    exist right now, so a transcript left behind by a closed session is never
    shown. When the count is unknown the age window alone decides. Sessions
    whose directory matches a routed Airlock session are that session's own
    transcript: they are skipped here and handed back through ``transcripts``
    so the routed session can show the same activity and context.
    """
    if transcripts is not None:
        transcripts.clear()
    try:
        if not projects_root.is_dir() or projects_root.is_symlink():
            return []
        project_dirs = sorted(projects_root.iterdir())[:NATIVE_MAX_PROJECT_DIRS]
    except OSError:
        return []
    candidates: list[tuple[float, Path]] = []
    cutoff = now.timestamp() - NATIVE_MAX_AGE_SECONDS
    for directory in project_dirs:
        try:
            if not directory.is_dir() or directory.is_symlink():
                continue
            files = [
                entry for entry in directory.iterdir()
                if entry.suffix == ".jsonl" and not entry.name.startswith(".")
            ]
        except OSError:
            continue
        # A busy project folder holds every past transcript, so keep the
        # newest files rather than the first ones the directory lists.
        stamped: list[tuple[float, Path]] = []
        for entry in files[:NATIVE_MAX_FILES_SCANNED]:
            try:
                modified = entry.stat().st_mtime
            except OSError:
                continue
            if modified >= cutoff:
                stamped.append((modified, entry))
        stamped.sort(key=lambda item: item[0], reverse=True)
        candidates.extend(stamped[:NATIVE_MAX_FILES_PER_DIR])
    candidates.sort(key=lambda item: item[0], reverse=True)
    limit = live_count if isinstance(live_count, int) and live_count > 0 else len(candidates)
    # File times pick which transcripts are worth reading; the transcript's own
    # last timestamp decides the order and the cut, since it is the honest one.
    found: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for modified, path in candidates[: max(limit * 4, 16) + len(routed_workdirs)]:
        info = read_native_transcript_tail(path)
        if info is None:
            continue
        # Claude Code also appends records that carry no timestamp, so the
        # file's own modification time is the honest "last touched" signal
        # whenever it is newer than the last stamped record.
        touched = datetime.fromtimestamp(modified, tz=timezone.utc)
        if touched > info["last_at"]:
            info["last_at"] = touched
        if info["last_at"].timestamp() < cutoff:
            continue
        workdir_key = _normalized_workdir(info["cwd"])
        if workdir_key in routed_workdirs:
            if transcripts is not None and workdir_key and workdir_key not in transcripts:
                transcripts[workdir_key] = info
            continue
        session_id = NATIVE_ID_PREFIX + info["session_id"][:12]
        if session_id in seen_ids:
            continue
        seen_ids.add(session_id)
        info["id"] = session_id
        found.append(info)
    if live_count == 0:
        return []
    found.sort(key=lambda item: item["last_at"], reverse=True)
    sessions: list[dict[str, Any]] = []
    for info in found[:limit]:
        session_id = info["id"]
        age = (now - info["last_at"]).total_seconds()
        model = info["model"] or "claude"
        # A plain Claude Code session can only have answered on a Claude model.
        # A GPT or Grok id means the session went through Airlock on a
        # router-less profile (OpenAI-only or Grok-only), which the page
        # should say rather than calling it plain Claude Code.
        provider = _native_model_provider(model)
        sessions.append({
            "id": session_id,
            "state": "running" if age <= NATIVE_RUNNING_SECONDS else "idle",
            "blocked_reason": None,
            "profile": NATIVE_PROFILE if provider == "anthropic" else f"{provider}-pure",
            "root_model": model,
            "root_provider": provider,
            "active_model": model,
            "workdir": info["cwd"],
            "project": project_name(info["cwd"]),
            "started_at": None,
            "last_activity_at": format_timestamp(info["last_at"]),
            "context": transcript_context(info),
            "workers": [],
            "recent_handoffs": 0,
            "url": "",
            "source": "claude-code" if provider == "anthropic" else "airlock-direct",
            "title": info.get("title"),
            "branch": info.get("branch"),
            "_activity": info.get("activity") or [],
            "_session_id": info["session_id"],
            "history_id": HISTORY_ID_PREFIX + info["session_id"][:12],
        })
    return sessions


def native_session_summary(session: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in session.items() if not key.startswith("_")}


def native_session_detail(summary: dict[str, Any]) -> dict[str, Any]:
    detail = native_session_summary(summary)
    detail.update({
        "routes": [],
        "cooldowns": {},
        "chains": {},
        "usage": [],
        "events": [],
        "last_handoff": None,
        "controllable": False,
        "pinned_model": None,
        "proposals": [],
        "current_handoff": None,
        "activity": list(summary.get("_activity") or []),
    })
    return detail


class ConsoleState:
    def __init__(
        self,
        runtime_root: Path,
        *,
        scan: bool = False,
        native: bool = True,
        native_projects_root: Path | None = None,
        native_process_count: Callable[[], int | None] | None = None,
        history: Any | None = None,
        history_interval: float = HISTORY_REFRESH_SECONDS,
        clock: Callable[[], datetime] | None = None,
        alive: Callable[[int], bool] | None = None,
        listen_ports: Callable[[], set[int]] | None = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        coalesce_seconds: float = SSE_COALESCE_SECONDS,
        heartbeat_seconds: float = SSE_HEARTBEAT_SECONDS,
        control_client: RouterControlClient | None = None,
        chains: ChainBackend | None = None,
        tools: Any | None = None,
        csrf_token: str | None = None,
    ) -> None:
        self.runtime_root = runtime_root
        self.sessions_dir = runtime_root / "sessions"
        self.scan = scan
        self.native = native
        self.native_projects_root = native_projects_root or claude_projects_root()
        self.native_process_count = native_process_count or count_native_claude_processes
        self._native: list[dict[str, Any]] = []
        # Transcript facts for routed sessions, keyed by normalized workdir.
        self._transcripts: dict[str, dict[str, Any]] = {}
        # The session history index, refreshed on its own thread because a
        # first pass over a long history can take seconds.
        self.history = history
        self.history_interval = history_interval
        self._history_thread: threading.Thread | None = None
        self.clock = clock or utc_now
        self.alive = alive or process_alive
        self.listen_ports = listen_ports or listening_loopback_ports
        self.poll_interval = poll_interval
        self.coalesce_seconds = coalesce_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.control_client = control_client or RouterControlClient()
        self.chains = chains or ChainBackend()
        self.tools = tools
        self.csrf_token = csrf_token or new_csrf_token()
        self.proposals = ProposalStore(self.clock)
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.records: dict[str, SessionRecord] = {}
        self._overview: dict[str, Any] = self._decorate_overview(
            derive_overview([], self.clock())
        )
        self._fingerprint = self._overview_fingerprint(self._overview)
        self.generation = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.refresh()
        self._thread = threading.Thread(target=self._poll_loop, name="airlock-console-poll", daemon=True)
        self._thread.start()
        if self.history is not None:
            self._history_thread = threading.Thread(
                target=self._history_loop, name="airlock-console-history", daemon=True
            )
            self._history_thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self.changed:
            self.changed.notify_all()
        for thread in (self._thread, self._history_thread):
            if thread is not None:
                thread.join(timeout=2)

    def _history_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.history.refresh()
            except Exception:
                pass
            if self._stop.wait(self.history_interval):
                return

    def history_facts(self, *, session_id: str | None = None, workdir: str | None = None) -> dict[str, Any] | None:
        """The history index's view of a session, trimmed to public facts."""
        if self.history is None:
            return None
        try:
            found = None
            if session_id:
                found = self.history.stats_for_session(session_id, self.clock())
            if found is None and workdir:
                found = self.history.stats_for_workdir(workdir, self.clock())
        except Exception:
            return None
        if not isinstance(found, dict):
            return None
        return {key: found.get(key) for key in HISTORY_FACT_KEYS if key in found}

    # -- history, shared by the HTTP handler and the agent tools ------------

    def _require_history(self) -> Any:
        if self.history is None:
            raise ControlError(
                503,
                "history_unavailable",
                "Session history is not available on this console",
            )
        return self.history

    def history_listing(
        self,
        *,
        project: str | None = None,
        model: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        query: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        history = self._require_history()
        items = history.filtered(
            project=project, model=model, since=since, until=until, query=query,
            limit=max(1, min(int(limit), 1000)),
        )
        summaries = history.summaries()
        projects = sorted({item.get("project") or "" for item in summaries} - {""})
        models = sorted({m for item in summaries for m in item.get("models") or []})
        starts = [item.get("started_at") for item in summaries if item.get("started_at")]
        lasts = [item.get("last_activity_at") for item in summaries if item.get("last_activity_at")]
        return {
            "generated_at": format_timestamp(self.clock()),
            "refreshed_at": format_timestamp(history.last_refresh) if history.last_refresh else None,
            "total": len(history.files),
            "first_activity_at": min(starts) if starts else None,
            "last_activity_at": max(lasts) if lasts else None,
            "projects": projects,
            "models": models,
            "sessions": [{k: v for k, v in item.items() if k != "path"} for item in items],
        }

    def history_session(self, history_id: str) -> dict[str, Any] | None:
        history = self._require_history()
        detail = history.detail(history_id, self.clock())
        if detail is None:
            return None
        transcript = read_native_transcript_tail(Path(detail["path"]))
        detail["activity"] = list((transcript or {}).get("activity") or [])
        detail.pop("path", None)
        return detail

    def history_subagents(self, history_id: str) -> list[dict[str, Any]] | None:
        history = self._require_history()
        if history.find(history_id) is None:
            return None
        return [{k: v for k, v in item.items() if k != "path"} for item in history.subagents(history_id)]

    def history_subagent_feed(self, history_id: str, agent_id: str) -> dict[str, Any] | None:
        history = self._require_history()
        agent_path = history.subagent_path(history_id, agent_id)
        if agent_path is None:
            return None
        transcript = read_native_transcript_tail(agent_path, own_chain=True)
        return {
            "id": agent_id,
            "model": (transcript or {}).get("model"),
            "last_activity_at": format_timestamp(transcript["last_at"]) if transcript and transcript.get("last_at") else None,
            "context": transcript_context(transcript) if transcript else None,
            "activity": list((transcript or {}).get("activity") or []),
        }

    def history_usage(self, **filters: Any) -> dict[str, Any]:
        history = self._require_history()
        return history.usage(now=self.clock(), **filters)

    def history_query(self, **filters: Any) -> dict[str, Any]:
        history = self._require_history()
        try:
            return history.query(now=self.clock(), **filters)
        except (ValueError, TypeError) as error:
            raise ControlError(400, "invalid_request", "History query is invalid") from error

    def _poll_loop(self) -> None:
        while not self._stop.wait(self.poll_interval):
            try:
                self.refresh()
            except Exception:
                continue

    def sessions_dir_entries(self) -> list[dict[str, Any]]:
        return load_registry_files(self.sessions_dir)

    def _synthetic_registry(self, health: dict[str, Any], url: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "instance_id": health["instance_id"],
            "url": url,
            "owner_pid": positive_int(health.get("owner_pid")) or 0,
            "router_pid": positive_int(health.get("pid")) or 0,
            "started_at": None,
            "profile": None,
            "root_model": None,
            "root_provider": None,
            "workdir": None,
        }

    def _scan_routers(self) -> list[tuple[str, dict[str, Any]]]:
        found: list[tuple[str, dict[str, Any]]] = []
        try:
            ports = self.listen_ports()
        except Exception:
            return found
        for port in sorted(ports):
            url = f"http://127.0.0.1:{port}"
            health = probe_airlock_router(url, timeout=SCAN_FETCH_TIMEOUT_SECONDS)
            if health is None:
                continue
            found.append((url, health))
        return found

    def _unlink_registry(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            if path.is_symlink() or not path.is_file():
                return
            path.unlink()
        except OSError:
            return

    def _load_router(self, record: SessionRecord) -> bool:
        health = probe_airlock_router(record.url)
        if health is None:
            return False
        if health.get("instance_id") != record.instance_id:
            return False
        try:
            diagnostics = fetch_router_json(record.url, "/diagnostics")
            if diagnostics is not None and diagnostics[1]:
                record.diagnostics = diagnostics[1]
            models = fetch_router_json(record.url, "/v1/models")
            if models is not None:
                record.models = parse_models_list(models[1])
        except (RecursionError, OverflowError, ValueError, TypeError, MemoryError):
            return True
        return True

    def refresh(self) -> None:
        now = self.clock()
        registry_entries = self.sessions_dir_entries()
        discovered: dict[str, SessionRecord] = {}
        for entry in registry_entries:
            instance_id = entry["instance_id"]
            path = self.sessions_dir / f"{instance_id}.json"
            previous = self.records.get(instance_id)
            record = SessionRecord(
                instance_id=instance_id,
                url=entry["url"],
                owner_pid=entry["owner_pid"],
                router_pid=entry["router_pid"],
                registry=entry,
                source="registry",
                registry_path=path,
                control_token=entry.get("control_token"),
            )
            if previous is not None and previous.instance_id == instance_id:
                record.diagnostics = previous.diagnostics
                record.models = previous.models
                record.ended_at = previous.ended_at
                if record.control_token is None:
                    record.control_token = previous.control_token
            discovered[instance_id] = record
        if self.scan:
            for url, health in self._scan_routers():
                instance_id = health["instance_id"]
                if instance_id in discovered:
                    continue
                previous = self.records.get(instance_id)
                owner_pid = positive_int(health.get("owner_pid")) or (
                    previous.owner_pid if previous else 0
                )
                record = SessionRecord(
                    instance_id=instance_id,
                    url=url,
                    owner_pid=owner_pid,
                    router_pid=positive_int(health.get("pid")),
                    registry=self._synthetic_registry(health, url),
                    source="scan",
                )
                if previous is not None:
                    record.diagnostics = previous.diagnostics
                    record.models = previous.models
                    record.ended_at = previous.ended_at
                    record.control_token = previous.control_token
                    if previous.registry and previous.source == "registry":
                        record.registry = previous.registry
                discovered[instance_id] = record
        for instance_id, previous in list(self.records.items()):
            if instance_id in discovered:
                continue
            if previous.ended_at is None:
                previous.ended_at = now
            discovered[instance_id] = previous
        live_records: dict[str, SessionRecord] = {}
        for instance_id, record in discovered.items():
            owner_missing = record.owner_pid > 0 and not self.alive(record.owner_pid)
            answering = False
            if not owner_missing:
                answering = self._load_router(record)
            if owner_missing or not answering:
                if record.ended_at is None:
                    record.ended_at = now
                    self._unlink_registry(record.registry_path)
                elapsed = (now - record.ended_at).total_seconds()
                if elapsed > ENDED_VISIBLE_SECONDS:
                    continue
            else:
                record.ended_at = None
            live_records[instance_id] = record
        self.proposals.expire_and_prune(now)
        if self.native:
            routed = {
                _normalized_workdir(
                    ((record.diagnostics or {}).get("workdir") or (record.registry or {}).get("workdir"))
                )
                for record in live_records.values()
                if record.ended_at is None
            }
            routed.discard(None)
            try:
                transcripts: dict[str, dict[str, Any]] = {}
                self._native = discover_native_sessions(
                    now,
                    projects_root=self.native_projects_root,
                    live_count=self.native_process_count(),
                    routed_workdirs={w for w in routed if w},
                    transcripts=transcripts,
                )
                self._transcripts = transcripts
            except (OSError, ValueError, TypeError, RecursionError, MemoryError):
                self._native = []
                self._transcripts = {}
        try:
            overview = self._decorate_overview(
                derive_overview(list(live_records.values()), now)
            )
            fingerprint = self._overview_fingerprint(overview)
        except (RecursionError, OverflowError, ValueError, TypeError, MemoryError):
            with self.changed:
                self.records = live_records
            return
        with self.changed:
            self.records = live_records
            self._overview = overview
            if fingerprint != self._fingerprint:
                self._fingerprint = fingerprint
                self.generation += 1
                self.changed.notify_all()

    @staticmethod
    def _overview_fingerprint(overview: dict[str, Any]) -> str:
        payload = dict(overview)
        payload.pop("generated_at", None)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)

    def snapshot(self) -> tuple[int, dict[str, Any]]:
        with self.changed:
            return self.generation, json.loads(json.dumps(self._overview))

    def overview(self) -> dict[str, Any]:
        return self.snapshot()[1]

    def session_summaries(self) -> list[dict[str, Any]]:
        return list(self.overview().get("sessions") or [])

    def global_routes(self) -> list[dict[str, Any]]:
        return list(self.overview().get("routes") or [])

    def session_count(self) -> int:
        with self.lock:
            return len(self.records) + len(self._native)

    def native_session(self, session_id: str) -> dict[str, Any] | None:
        for session in self._native:
            if session.get("id") == session_id:
                detail = native_session_detail(session)
                detail["history"] = self.history_facts(
                    session_id=session.get("_session_id"), workdir=session.get("workdir")
                )
                return detail
        return None

    def session_detail(self, session_id: str) -> dict[str, Any] | None:
        now = self.clock()
        with self.lock:
            record = self.records.get(session_id)
            records = list(self.records.values())
        if record is None:
            return self.native_session(session_id)
        _routes, using, _summaries = collect_session_routes(records, now)
        try:
            view = derive_session_view(
                record, now, include_detail=True, sessions_using=using
            )
        except (RecursionError, OverflowError, ValueError, TypeError, MemoryError):
            return None
        return self._decorate_session_detail(record, view)

    def session_events(
        self,
        session_id: str,
        *,
        kind: str | None = None,
        model: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]] | None:
        detail = self.session_detail(session_id)
        if detail is None:
            return None
        events = list(detail.get("events") or [])
        if kind:
            events = [event for event in events if event.get("kind") == kind]
        if model:
            events = [event for event in events if event.get("model") == model]
        if since:
            since_at = parse_timestamp(since)
            if since_at is None:
                return events
            events = [
                event
                for event in events
                if (parse_timestamp(event.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc))
                >= since_at
            ]
        return events

    def _decorate_overview(self, overview: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.chains.snapshot()
        native = list(self._native)
        for session in overview.get("sessions") or []:
            self._enrich_from_transcript(session)
        if native:
            known = {session.get("id") for session in overview.get("sessions") or []}
            merged = list(overview.get("sessions") or []) + [
                native_session_summary(session) for session in native if session.get("id") not in known
            ]
            merged.sort(
                key=lambda item: (
                    STATE_ORDER.get(item.get("state"), 9),
                    parse_timestamp(item.get("last_activity_at"))
                    or datetime.min.replace(tzinfo=timezone.utc),
                    item.get("id") or "",
                )
            )
            overview["sessions"] = merged
        overview["proposals"] = self.proposals.summaries()
        overview["chain_digest"] = snapshot.get("digest")
        return overview

    def _transcript_for(self, session: dict[str, Any]) -> dict[str, Any] | None:
        key = _normalized_workdir(session.get("workdir"))
        if not key:
            return None
        return self._transcripts.get(key)

    def _enrich_from_transcript(self, session: dict[str, Any], *, detail: bool = False) -> None:
        """Give a routed session the facts only its transcript knows.

        The router sees requests, not the conversation, so the session's own
        title, branch, and the live context size of its last reply come from
        the transcript Claude Code keeps for it. Router facts always win when
        both exist.
        """
        info = self._transcript_for(session)
        if info is None:
            if detail:
                session.setdefault("activity", [])
            return
        session.setdefault("title", info.get("title"))
        session.setdefault("branch", info.get("branch"))
        if info.get("session_id"):
            session.setdefault("history_id", HISTORY_ID_PREFIX + str(info["session_id"])[:12])
        context = session.get("context")
        if not isinstance(context, dict) or context.get("input_tokens") is None:
            window = context.get("window") if isinstance(context, dict) else None
            filled = transcript_context(info, window)
            if filled is not None:
                session["context"] = filled
        if detail:
            session["activity"] = list(info.get("activity") or [])
            session["history"] = self.history_facts(
                session_id=info.get("session_id"), workdir=session.get("workdir")
            )

    def _decorate_session_detail(
        self, record: SessionRecord, view: dict[str, Any]
    ) -> dict[str, Any]:
        self._enrich_from_transcript(view, detail=True)
        view["controllable"] = record.controllable
        view["pinned_model"] = safe_model(
            (record.diagnostics or {}).get("pinned_model")
        )
        view["proposals"] = self.proposals.public_for_session(record.instance_id)
        view["current_handoff"] = self.proposals.current_handoff(record.instance_id)
        return view

    def notify_change(self) -> None:
        now = self.clock()
        self.proposals.expire_and_prune(now)
        with self.lock:
            records = list(self.records.values())
        try:
            overview = self._decorate_overview(derive_overview(records, now))
            fingerprint = self._overview_fingerprint(overview)
        except (RecursionError, OverflowError, ValueError, TypeError, MemoryError):
            return
        with self.changed:
            self._overview = overview
            if fingerprint != self._fingerprint:
                self._fingerprint = fingerprint
                self.generation += 1
                self.changed.notify_all()

    def record_for(self, session_id: str) -> SessionRecord | None:
        with self.lock:
            return self.records.get(session_id)

    def handoff_basis(self, session_id: str, target_model: str | None) -> dict[str, Any]:
        detail = self.session_detail(session_id)
        if detail is None:
            raise ControlError(404, "not_found", "Session not found")
        route_status = None
        if target_model:
            for route in detail.get("routes") or []:
                if model_ids_equivalent(route.get("model"), target_model):
                    route_status = route.get("status")
                    break
        context = detail.get("context") or {}
        return {
            "observed_at": format_timestamp(self.clock()),
            "active_model": detail.get("active_model"),
            "pinned_model": detail.get("pinned_model"),
            "context_input_tokens": context.get("input_tokens"),
            "route_status": route_status,
        }

    def validate_pin_target(
        self,
        session_id: str,
        target_model: str,
        *,
        allow_metered: bool,
        human_opt_in: bool,
        allow_already_active: bool = False,
    ) -> dict[str, Any]:
        record = self.record_for(session_id)
        if record is None:
            if self.native_session(session_id) is not None:
                raise ControlError(
                    409,
                    "not_routed",
                    "This session runs plain Claude Code without an Airlock router, "
                    "so it has no routes to move between; start it with airlock to route it",
                )
            raise ControlError(404, "not_found", "Session not found")
        if record.ended_at is not None:
            raise ControlError(409, "route_state_changed", "Session has ended")
        detail = self.session_detail(session_id)
        if detail is None:
            raise ControlError(404, "not_found", "Session not found")
        routes = list(detail.get("routes") or [])
        match = next(
            (
                route
                for route in routes
                if model_ids_equivalent(route.get("model"), target_model)
            ),
            None,
        )
        if match is None:
            raise ControlError(400, "model_not_enabled", "Target model is not enabled")
        already_active = model_ids_equivalent(
            detail.get("active_model"), target_model
        ) or model_ids_equivalent(detail.get("pinned_model"), target_model)
        if already_active and not allow_already_active:
            raise ControlError(409, "route_state_changed", "Target model is already active")
        if match.get("status") == "cooling":
            raise ControlError(409, "route_cooling", "Target route is cooling")
        if match.get("fits_context") is False:
            raise ControlError(409, "context_too_large", "Conversation does not fit the target window")
        if match.get("metered") is True and not allow_metered:
            raise ControlError(400, "invalid_request", "Metered target requires allow_metered")
        return match

    def validate_restore_root(
        self, session_id: str, *, allow_already_unpinned: bool = False
    ) -> None:
        detail = self.session_detail(session_id)
        if detail is None:
            raise ControlError(404, "not_found", "Session not found")
        if detail.get("pinned_model") is None and not allow_already_unpinned:
            raise ControlError(409, "route_state_changed", "Session is not pinned")

    def create_handoff_proposal(
        self,
        *,
        session_id: str,
        operation: str,
        target_model: str | None,
        reason: str,
        allow_metered: bool,
        created_by: str,
        human_opt_in: bool = False,
    ) -> dict[str, Any]:
        if operation not in HANDOFF_OPERATIONS:
            raise ControlError(400, "invalid_request", "operation is invalid")
        record = self.record_for(session_id)
        if record is None:
            if self.native_session(session_id) is not None:
                raise ControlError(
                    409,
                    "not_routed",
                    "This session runs plain Claude Code without an Airlock router, "
                    "so it has no routes to move between; start it with airlock to route it",
                )
            raise ControlError(404, "not_found", "Session not found")
        if not record.controllable:
            raise ControlError(403, "forbidden", "Session is not controllable")
        if operation == "pin":
            if not isinstance(target_model, str) or safe_model(target_model) is None:
                raise ControlError(400, "invalid_request", "target_model is invalid")
            self.validate_pin_target(
                session_id,
                target_model,
                allow_metered=allow_metered,
                human_opt_in=False,
            )
        else:
            target_model = None
            self.validate_restore_root(session_id)
        reason = validate_reason_text(reason)
        basis = self.handoff_basis(session_id, target_model)
        proposal = self.proposals.create_handoff(
            session_id=session_id,
            operation=operation,
            target_model=target_model,
            reason=reason,
            allow_metered=allow_metered,
            created_by=created_by,
            basis=basis,
        )
        self.notify_change()
        return proposal

    def create_chain_proposal(
        self,
        *,
        chains: dict[str, list[str]],
        reason: str,
        created_by: str,
    ) -> dict[str, Any]:
        reason = validate_reason_text(reason)
        snapshot = self.chains.snapshot()
        proposal = self.proposals.create_chain_change(
            chains=require_chain_map(chains),
            base_digest=snapshot["digest"],
            reason=reason,
            created_by=created_by,
        )
        self.notify_change()
        return proposal

    def apply_proposal(self, proposal_id: str) -> dict[str, Any]:
        existing = self.proposals.get(proposal_id)
        if existing is not None and existing.data.get("status") == "applied":
            public = existing.public()
            application = dict(public.get("application") or {})
            application["already_applied"] = True
            public["application"] = application
            return public
        proposal = self.proposals.begin_apply(proposal_id)
        data = proposal.data
        if data.get("status") == "applied":
            public = proposal.public()
            if proposal.lock.locked():
                proposal.lock.release()
            return public
        try:
            if data.get("kind") == "session_handoff":
                result = self._apply_handoff(data)
            else:
                result = self._apply_chain(data)
        except ControlError as error:
            status = "conflicted" if error.status == 409 else "failed"
            public = self.proposals.finish_apply(
                proposal,
                status=status,
                error={"code": error.kind, "message": error.message},
            )
            self.notify_change()
            return public
        except Exception:
            public = self.proposals.finish_apply(
                proposal,
                status="failed",
                error={"code": "failed", "message": "Proposal could not be applied"},
            )
            self.notify_change()
            return public
        public = self.proposals.finish_apply(
            proposal,
            status="applied",
            application=result,
        )
        self.notify_change()
        return public

    def _apply_handoff(self, data: dict[str, Any]) -> dict[str, Any]:
        session_id = data.get("session_id")
        if not isinstance(session_id, str):
            raise ControlError(404, "not_found", "Session not found")
        record = self.record_for(session_id)
        if record is None or record.ended_at is not None:
            raise ControlError(404, "not_found", "Session not found")
        if not record.controllable or parse_control_token(record.control_token) is None:
            raise ControlError(403, "forbidden", "Session is not controllable")
        health = probe_airlock_router(record.url)
        if health is None or health.get("instance_id") != record.instance_id:
            raise ControlError(404, "not_found", "Router instance changed")
        operation = data.get("operation")
        if operation == "pin":
            target = safe_model(data.get("target_model"))
            if target is None:
                raise ControlError(400, "invalid_request", "target_model is invalid")
            self.validate_pin_target(
                session_id,
                target,
                allow_metered=bool(data.get("allow_metered")),
                human_opt_in=True,
                allow_already_active=True,
            )
            result = self.control_client.pin(
                record.url, record.control_token or "", record.instance_id, target
            )
        elif operation == "restore_root":
            self.validate_restore_root(session_id, allow_already_unpinned=True)
            result = self.control_client.unpin(
                record.url, record.control_token or "", record.instance_id
            )
        else:
            raise ControlError(400, "invalid_request", "operation is invalid")
        if result.get("instance_id") != record.instance_id:
            raise ControlError(404, "not_found", "Router instance changed")
        self._load_router(record)
        return {
            "router_instance_id": record.instance_id,
            "previous_pinned_model": result.get("previous_pinned_model"),
            "pinned_model": result.get("pinned_model"),
            "changed": result.get("changed") is True,
            "already_applied": result.get("changed") is False,
        }

    def _apply_chain(self, data: dict[str, Any]) -> dict[str, Any]:
        digest = data.get("base_digest")
        if not isinstance(digest, str) or SHA256_HEX_PATTERN.fullmatch(digest) is None:
            raise ControlError(400, "invalid_request", "expected digest is invalid")
        snapshot = self.chains.snapshot()
        if snapshot.get("digest") != digest:
            raise ControlError(
                409,
                "chain_conflict",
                "failover chain changed during this operation",
                extra={"current": snapshot},
            )
        saved = self.chains.save(require_chain_map(data.get("chains")), digest)
        return {
            "router_instance_id": None,
            "previous_pinned_model": None,
            "pinned_model": None,
            "changed": saved.get("changed") is True,
            "already_applied": saved.get("changed") is False,
        }

    def save_chains(self, chains: dict[str, list[str]], expected_digest: str) -> dict[str, Any]:
        saved = self.chains.save(require_chain_map(chains), expected_digest)
        self.proposals.record_chain_direct(saved.get("digest") or "")
        self.notify_change()
        return {
            "chains": saved.get("chains") or {},
            "digest": saved.get("digest"),
            "notice": CHAIN_NOTICE,
            "changed": saved.get("changed") is True,
        }

    def dispatch_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "airlock_list_sessions":
            return {"sessions": self.session_summaries()}
        if name == "airlock_get_session":
            detail = self.session_detail(arguments["session_id"])
            if detail is None:
                raise ControlError(404, "not_found", "Session not found")
            return detail
        if name == "airlock_get_session_events":
            events = self.session_events(
                arguments["session_id"],
                kind=arguments.get("kind"),
                model=arguments.get("model"),
                since=arguments.get("since"),
            )
            if events is None:
                raise ControlError(404, "not_found", "Session not found")
            return {"events": events}
        if name == "airlock_get_routes":
            session_id = arguments.get("session_id")
            if session_id:
                detail = self.session_detail(session_id)
                if detail is None:
                    raise ControlError(404, "not_found", "Session not found")
                return {"routes": list(detail.get("routes") or [])}
            return {"routes": self.global_routes()}
        if name == "airlock_get_headroom":
            return {"headroom": list(self.overview().get("headroom") or [])}
        if name == "airlock_get_global_failover_chain":
            return self.chains.snapshot()
        if name == "airlock_list_proposals":
            items = self.proposals.public_all()
            session_id = arguments.get("session_id")
            kind = arguments.get("kind")
            status = arguments.get("status")
            if session_id:
                items = [item for item in items if item.get("session_id") == session_id]
            if kind:
                items = [item for item in items if item.get("kind") == kind]
            if status:
                items = [item for item in items if item.get("status") == status]
            return {"proposals": items}
        if name == "airlock_get_proposal":
            proposal = self.proposals.public_one(arguments["proposal_id"])
            if proposal is None:
                raise ControlError(404, "not_found", "Proposal not found")
            return proposal
        if name == "airlock_propose_session_handoff":
            return self.create_handoff_proposal(
                session_id=arguments["session_id"],
                operation="pin",
                target_model=arguments["target_model"],
                reason=arguments["reason"],
                allow_metered=bool(arguments.get("allow_metered", False)),
                created_by="agent",
            )
        if name == "airlock_propose_restore_root":
            return self.create_handoff_proposal(
                session_id=arguments["session_id"],
                operation="restore_root",
                target_model=None,
                reason=arguments["reason"],
                allow_metered=False,
                created_by="agent",
            )
        if name == "airlock_propose_chain_change":
            return self.create_chain_proposal(
                chains=arguments["chains"],
                reason=arguments["reason"],
                created_by="agent",
            )
        if name == "airlock_list_history":
            return self.history_listing(
                project=arguments.get("project"),
                model=arguments.get("model"),
                since=parse_timestamp(arguments.get("since")),
                until=parse_timestamp(arguments.get("until")),
                query=arguments.get("q"),
                limit=int(arguments.get("limit") or 100),
            )
        if name == "airlock_get_history_session":
            detail = self.history_session(arguments["history_id"])
            if detail is None:
                raise ControlError(404, "not_found", "Session not found")
            return detail
        if name == "airlock_list_subagents":
            agents = self.history_subagents(arguments["history_id"])
            if agents is None:
                raise ControlError(404, "not_found", "Session not found")
            return {"subagents": agents}
        if name == "airlock_get_subagent_feed":
            feed = self.history_subagent_feed(arguments["history_id"], arguments["agent_id"])
            if feed is None:
                raise ControlError(404, "not_found", "Subagent transcript not found")
            return feed
        if name == "airlock_get_usage":
            return self.history_usage(
                group=arguments.get("group") or "day",
                project=arguments.get("project"),
                model=arguments.get("model"),
                since=parse_timestamp(arguments.get("since")),
                until=parse_timestamp(arguments.get("until")),
            )
        if name == "airlock_query_history":
            return self.history_query(
                dimension=arguments["dimension"],
                project=arguments.get("project"),
                model=arguments.get("model"),
                provider=arguments.get("provider"),
                agent_type=arguments.get("agent_type"),
                tool=arguments.get("tool"),
                branch=arguments.get("branch"),
                entrypoint=arguments.get("entrypoint"),
                since=parse_timestamp(arguments.get("since")),
                until=parse_timestamp(arguments.get("until")),
                query=arguments.get("q"),
                order_by=arguments.get("order_by"),
                descending=bool(arguments.get("descending", True)),
                limit=int(arguments.get("limit") or 50),
            )
        raise ControlError(404, "not_found", "unknown tool")

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def wait_for_generation(self, current: int, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        while True:
            with self.changed:
                if self.generation != current or self._stop.is_set():
                    return self.generation
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self.generation
                self.changed.wait(remaining)


class ConsoleHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AirlockConsole"
    sys_version = ""

    @property
    def console(self) -> ConsoleServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def version_string(self) -> str:
        return self.server_version

    def origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None or origin == "":
            return True
        return is_loopback_origin(origin)

    def refuse_origin(self) -> None:
        self.send_json(403, {
            "type": "error",
            "error": {"type": "forbidden", "message": "Origin is not loopback"},
        })

    def send_security_headers(self) -> None:
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)

    def send_json(self, status: int, payload: object) -> None:
        body = json.dumps(
            payload, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.send_security_headers()
        self.send_header("connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)

    def send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        cache: str | None = "no-store",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        if cache:
            self.send_header("cache-control", cache)
        self.send_security_headers()
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.send_header("connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)

    def send_error_payload(
        self,
        status: int,
        kind: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "type": "error",
            "error": {"type": kind, "message": message},
        }
        if extra:
            payload.update(extra)
        self.send_json(status, payload)

    def _canonical_redirect_location(self) -> str:
        return f"http://127.0.0.1:{int(self.console.server_port)}/"

    def _enforce_canonical_host(self, path: str) -> bool:
        host = self.headers.get("Host")
        if isinstance(host, str) and host and not host.isascii():
            self.send_error_payload(403, "forbidden", "Host does not match the console")
            return True
        if host_is_loopback_ipv4(host):
            return False
        if path in {"/", "/index.html"}:
            location = self._canonical_redirect_location()
            self.send_response(302)
            self.send_header("location", location)
            self.send_header("content-length", "0")
            self.send_header("cache-control", "no-store")
            self.send_security_headers()
            self.send_header("connection", "close")
            self.end_headers()
            self.close_connection = True
            return True
        if path.startswith("/api"):
            self.send_error_payload(404, "not_found", "Route not found")
            return True
        self.send_error_payload(404, "not_found", "Route not found")
        return True

    def _send_unexpected_failure(self, status: int, kind: str, message: str) -> None:
        try:
            self.send_error_payload(status, kind, message)
        except Exception:
            return

    def _handle_history(self, path: str, query: dict[str, list[str]]) -> None:
        """Session history: the index, one session, its subagents, one feed,
        usage over time, and the general query. The agent tools answer from
        the same ConsoleState methods, so both channels agree."""
        state = self.console.state
        if state.history is None:
            self.send_error_payload(404, "not_found", "Session history is not available")
            return
        first = lambda key: (query.get(key, [None])[0] or "").strip() or None  # noqa: E731
        try:
            if path == "/api/usage":
                self.send_json(200, state.history_usage(
                    group=first("group") or "day",
                    project=first("project"),
                    model=first("model"),
                    since=parse_timestamp(first("since")),
                    until=parse_timestamp(first("until")),
                ))
                return
            if path == "/api/query":
                descending = first("descending")
                self.send_json(200, state.history_query(
                    dimension=first("dimension") or "project",
                    project=first("project"),
                    model=first("model"),
                    provider=first("provider"),
                    agent_type=first("agent_type"),
                    tool=first("tool"),
                    branch=first("branch"),
                    entrypoint=first("entrypoint"),
                    since=parse_timestamp(first("since")),
                    until=parse_timestamp(first("until")),
                    query=first("q"),
                    order_by=first("order_by"),
                    descending=descending is None or descending.lower() not in {"0", "false", "no"},
                    limit=int(first("limit") or 50),
                ))
                return
            if path == "/api/history":
                limit = non_negative_int(int(first("limit") or 200)) or 200
                self.send_json(200, state.history_listing(
                    project=first("project"),
                    model=first("model"),
                    since=parse_timestamp(first("since")),
                    until=parse_timestamp(first("until")),
                    query=first("q"),
                    limit=min(limit, 1000),
                ))
                return
            detail_match = re.fullmatch(r"/api/history/(hx-[A-Za-z0-9._-]{1,12})", path)
            if detail_match:
                detail = state.history_session(detail_match.group(1))
                if detail is None:
                    self.send_error_payload(404, "not_found", "Session not found")
                    return
                self.send_json(200, detail)
                return
            agents_match = re.fullmatch(r"/api/history/(hx-[A-Za-z0-9._-]{1,12})/subagents", path)
            if agents_match:
                agents = state.history_subagents(agents_match.group(1))
                if agents is None:
                    self.send_error_payload(404, "not_found", "Session not found")
                    return
                self.send_json(200, agents)
                return
            feed_match = re.fullmatch(
                r"/api/history/(hx-[A-Za-z0-9._-]{1,12})/subagents/([A-Za-z0-9._-]{4,64})", path
            )
            if feed_match:
                feed = state.history_subagent_feed(feed_match.group(1), feed_match.group(2))
                if feed is None:
                    self.send_error_payload(404, "not_found", "Subagent transcript not found")
                    return
                self.send_json(200, feed)
                return
        except ControlError as error:
            self.send_error_payload(error.status, error.kind, str(error))
            return
        except (ValueError, TypeError, OSError):
            self.send_error_payload(400, "invalid_request", "History request is invalid")
            return
        self.send_error_payload(404, "not_found", "Not found")

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        try:
            if self._enforce_canonical_host(path):
                return
            if not self.origin_allowed():
                self.refuse_origin()
                return
        except (TypeError, UnicodeError, ValueError, AttributeError):
            self._send_unexpected_failure(403, "forbidden", "Request is forbidden")
            return
        except Exception:
            self._send_unexpected_failure(400, "invalid_request", "Request is invalid")
            return
        query = parse_qs(parsed.query)
        if path == "/healthz":
            self.send_json(200, {
                "ok": True,
                "sessions": self.console.state.session_count(),
            })
            return
        if path == "/api/overview":
            self.send_json(200, self.console.state.overview())
            return
        if path == "/api/sessions":
            self.send_json(200, self.console.state.session_summaries())
            return
        if path == "/api/routes":
            self.send_json(200, self.console.state.global_routes())
            return
        if path == "/api/stream":
            self.stream()
            return
        if path == "/api/chains":
            self.send_json(200, self.console.state.chains.snapshot())
            return
        if path == "/api/proposals":
            self.send_json(200, {"proposals": self.console.state.proposals.public_all()})
            return
        proposal_match = re.fullmatch(r"/api/proposals/([^/]+)", path)
        if proposal_match:
            proposal_id = proposal_match.group(1)
            proposal = self.console.state.proposals.public_one(proposal_id)
            if proposal is None:
                self.send_error_payload(404, "not_found", "Proposal not found")
                return
            self.send_json(200, proposal)
            return
        if path == "/api/tools/manifest":
            tools = self.console.state.tools
            if tools is None:
                self.send_error_payload(404, "not_found", "Tools helper is unavailable")
                return
            self.send_json(200, {"tools": tools.tool_manifest()})
            return
        session_match = re.fullmatch(r"/api/sessions/([^/]+)", path)
        if session_match:
            session_id = session_match.group(1)
            if INSTANCE_ID_PATTERN.match(session_id) is None:
                self.send_error_payload(404, "not_found", "Session not found")
                return
            detail = self.console.state.session_detail(session_id)
            if detail is None:
                self.send_error_payload(404, "not_found", "Session not found")
                return
            self.send_json(200, detail)
            return
        events_match = re.fullmatch(r"/api/sessions/([^/]+)/events", path)
        if events_match:
            session_id = events_match.group(1)
            if INSTANCE_ID_PATTERN.match(session_id) is None:
                self.send_error_payload(404, "not_found", "Session not found")
                return
            kind = query.get("kind", [None])[0]
            model = query.get("model", [None])[0]
            since = query.get("since", [None])[0]
            events = self.console.state.session_events(
                session_id, kind=kind, model=model, since=since
            )
            if events is None:
                self.send_error_payload(404, "not_found", "Session not found")
                return
            self.send_json(200, events)
            return
        if path == "/api/history" or path.startswith("/api/history/") or path in {"/api/usage", "/api/query"}:
            self._handle_history(path, query)
            return
        report_match = re.fullmatch(r"/api/sessions/([^/]+)/report\.md", path)
        if report_match:
            session_id = report_match.group(1)
            if INSTANCE_ID_PATTERN.match(session_id) is None:
                self.send_error_payload(404, "not_found", "Session not found")
                return
            detail = self.console.state.session_detail(session_id)
            if detail is None:
                self.send_error_payload(404, "not_found", "Session not found")
                return
            detail = dict(detail)
            detail["chain_snapshot"] = self.console.state.chains.snapshot()
            body = render_report(detail).encode("utf-8")
            self.send_bytes(
                200,
                body,
                "text/markdown; charset=utf-8",
                extra_headers={
                    "content-disposition": f'attachment; filename="{REPORT_FILENAME}"',
                },
            )
            return
        if path.startswith("/api"):
            self.send_error_payload(404, "not_found", "Route not found")
            return
        self.serve_site(path)

    def stream(self) -> None:
        try:
            origin = self.headers.get("Origin")
            host = self.headers.get("Host")
            if origin not in {None, ""} and not is_loopback_origin(origin):
                self.refuse_origin()
                return
            if host not in {None, ""} and not host_is_loopback_ipv4(host):
                self.send_error_payload(403, "forbidden", "Host does not match the console")
                return
        except (TypeError, UnicodeError, ValueError, AttributeError):
            self._send_unexpected_failure(403, "forbidden", "Request is forbidden")
            return
        except Exception:
            self._send_unexpected_failure(400, "invalid_request", "Request is invalid")
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_security_headers()
        self.send_header("connection", "close")
        self.send_header("x-accel-buffering", "no")
        self.end_headers()
        self.close_connection = True
        state = self.console.state
        started = time.monotonic()

        def write_event(name: str, payload: object) -> None:
            data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            chunk = f"event: {name}\ndata: {data}\n\n".encode("utf-8")
            self.wfile.write(chunk)
            self.wfile.flush()

        generation, overview = state.snapshot()
        write_event("overview", overview)
        last_sent = time.monotonic()
        try:
            while not state.stopped:
                elapsed_total = time.monotonic() - started
                if elapsed_total >= SSE_MAX_SECONDS:
                    return
                wait_for = min(
                    state.heartbeat_seconds,
                    SSE_MAX_SECONDS - elapsed_total,
                )
                if wait_for <= 0:
                    return
                new_generation = state.wait_for_generation(generation, wait_for)
                if state.stopped:
                    return
                if new_generation != generation:
                    elapsed = time.monotonic() - last_sent
                    remaining = state.coalesce_seconds - elapsed
                    if remaining > 0:
                        with state.changed:
                            if not state.stopped:
                                state.changed.wait(remaining)
                        if state.stopped:
                            return
                    generation, overview = state.snapshot()
                    write_event("overview", overview)
                    last_sent = time.monotonic()
                else:
                    write_event("heartbeat", {})
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            try:
                self.connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def serve_site(self, path: str) -> None:
        site = self.console.site
        if site is None or not site.is_dir():
            body = MISSING_SITE_PAGE.encode("utf-8")
            if path in {"/", "/index.html"}:
                body = inject_csrf_meta(body, self.console.state.csrf_token)
            self.send_bytes(
                503,
                body,
                "text/html; charset=utf-8",
            )
            return
        relative = "index.html" if path == "/" else path.lstrip("/")
        if "\\" in relative or ".." in Path(relative).parts:
            self.send_error_payload(404, "not_found", "Route not found")
            return
        candidate = site / relative
        try:
            candidate.parent.resolve().relative_to(site.resolve())
        except (ValueError, OSError):
            self.send_error_payload(404, "not_found", "Route not found")
            return
        body = open_regular_file(candidate, MAX_ROUTER_BODY_BYTES)
        if body is None:
            self.send_bytes(
                404,
                b"Not found\n",
                "text/plain; charset=utf-8",
            )
            return
        content_type = STATIC_TYPES.get(candidate.suffix.lower(), "application/octet-stream")
        if path in {"/", "/index.html"} and candidate.suffix.lower() == ".html":
            body = inject_csrf_meta(body, self.console.state.csrf_token)
        self.send_bytes(200, body, content_type, cache="no-store")

    def do_POST(self) -> None:
        self._mutate("POST")

    def do_PUT(self) -> None:
        self._mutate("PUT")

    def do_PATCH(self) -> None:
        self._mutate("PATCH")

    def _mutate(self, method: str) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        if self._enforce_canonical_host(path):
            return
        if path.startswith("/api/tools/"):
            if method != "POST" or path != "/api/tools/call":
                self.send_error_payload(404, "not_found", "Route not found")
                return
            self._handle_tool_call()
            return
        if not path.startswith("/api/human/"):
            self.send_error_payload(404, "not_found", "Route not found")
            return
        try:
            payload = self._require_human_mutation()
            self.console.state.proposals.expire_and_prune()
            if method == "POST" and path == "/api/human/session-handoffs":
                self._handle_human_handoff(payload)
                return
            if method == "PUT" and path == "/api/human/chains":
                self._handle_human_chains(payload)
                return
            proposal_edit = re.fullmatch(r"/api/human/proposals/([^/]+)", path)
            if method == "PATCH" and proposal_edit:
                self._handle_human_edit(proposal_edit.group(1), payload)
                return
            proposal_approve = re.fullmatch(r"/api/human/proposals/([^/]+)/approve", path)
            if method == "POST" and proposal_approve:
                if payload:
                    raise ControlError(400, "invalid_request", "approve accepts no extra fields")
                result = self.console.state.apply_proposal(proposal_approve.group(1))
                self.send_json(200, result)
                return
            proposal_reject = re.fullmatch(r"/api/human/proposals/([^/]+)/reject", path)
            if method == "POST" and proposal_reject:
                if payload:
                    raise ControlError(400, "invalid_request", "reject accepts no extra fields")
                result = self.console.state.proposals.reject(proposal_reject.group(1))
                self.console.state.notify_change()
                self.send_json(200, result)
                return
            self.send_error_payload(404, "not_found", "Route not found")
        except ControlError as error:
            extra = dict(error.extra)
            if extra.get("current") is None and error.kind == "chain_conflict":
                extra["current"] = self.console.state.chains.snapshot()
            self.send_error_payload(error.status, error.kind, error.message, extra or None)
        except (TypeError, UnicodeError, ValueError, AttributeError):
            self._send_unexpected_failure(403, "forbidden", "Request is forbidden")
        except Exception:
            self._send_unexpected_failure(400, "invalid_request", "Request is invalid")

    def _require_human_mutation(self) -> dict[str, Any]:
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        port = int(self.console.server_port)
        if origin is None or origin == "":
            raise ControlError(403, "forbidden", "Origin is required")
        if not origin_matches_console(origin, port):
            raise ControlError(403, "forbidden", "Origin is not the console origin")
        if not host_matches_loopback_port(host, port):
            raise ControlError(403, "forbidden", "Host does not match the console")
        if not json_content_type(self.headers.get("Content-Type")):
            raise ControlError(400, "invalid_request", "Content-Type must be application/json")
        csrf = self.headers.get(CSRF_HEADER)
        if not tokens_match(csrf, self.console.state.csrf_token):
            raise ControlError(403, "forbidden", "CSRF token is invalid")
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            raise ControlError(400, "invalid_request", "Content-Length is required")
        try:
            length = int(length_header)
        except ValueError as error:
            raise ControlError(400, "invalid_request", "Content-Length is invalid") from error
        if length < 0:
            raise ControlError(400, "invalid_request", "Content-Length is invalid")
        if length > MAX_MUTATION_BODY_BYTES:
            raise ControlError(413, "request_too_large", "Request body is too large")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ControlError(400, "invalid_request", "Request body is incomplete")
        if length == 0:
            return {}
        payload = parse_json_object(body)
        if payload is None:
            raise ControlError(400, "invalid_request", "Request body is not a JSON object")
        return payload

    def _handle_human_handoff(self, payload: dict[str, Any]) -> None:
        allowed = {"session_id", "operation", "target_model", "reason", "allow_metered"}
        extra = set(payload) - allowed
        if extra:
            raise ControlError(400, "invalid_request", "unsupported fields")
        session_id = payload.get("session_id")
        operation = payload.get("operation")
        if not isinstance(session_id, str) or INSTANCE_ID_PATTERN.match(session_id) is None:
            raise ControlError(400, "invalid_request", "session_id is invalid")
        if operation not in HANDOFF_OPERATIONS:
            raise ControlError(400, "invalid_request", "operation is invalid")
        allow_metered = payload.get("allow_metered", False)
        if type(allow_metered) is not bool:
            raise ControlError(400, "invalid_request", "allow_metered must be a boolean")
        target_model = payload.get("target_model")
        if operation == "pin":
            if not isinstance(target_model, str) or safe_model(target_model) is None:
                raise ControlError(400, "invalid_request", "target_model is invalid")
        elif target_model is not None:
            raise ControlError(400, "invalid_request", "restore_root cannot name a target")
        result = self.console.state.create_handoff_proposal(
            session_id=session_id,
            operation=operation,
            target_model=target_model if operation == "pin" else None,
            reason=payload.get("reason"),
            allow_metered=allow_metered,
            created_by="human",
            human_opt_in=True,
        )
        self.send_json(200, result)

    def _handle_human_edit(self, proposal_id: str, payload: dict[str, Any]) -> None:
        expected = payload.get("expected_revision")
        if not is_int(expected) or expected < 1:
            raise ControlError(400, "invalid_request", "expected_revision is invalid")
        fields = dict(payload)
        fields.pop("expected_revision", None)
        if "operation" in fields:
            raise ControlError(400, "invalid_request", "operation cannot be edited")
        allowed = {"target_model", "reason", "allow_metered", "chains", "base_digest"}
        extra = set(fields) - allowed
        if extra:
            raise ControlError(400, "invalid_request", "unsupported fields")
        if "reason" in fields:
            fields["reason"] = validate_reason_text(fields["reason"])
        if "target_model" in fields:
            model = safe_model(fields["target_model"])
            if model is None:
                raise ControlError(400, "invalid_request", "target_model is invalid")
            fields["target_model"] = model
        if "allow_metered" in fields and type(fields["allow_metered"]) is not bool:
            raise ControlError(400, "invalid_request", "allow_metered must be a boolean")
        if "chains" in fields:
            fields["chains"] = require_chain_map(fields["chains"])
        if "base_digest" in fields:
            digest = fields["base_digest"]
            if not isinstance(digest, str) or SHA256_HEX_PATTERN.fullmatch(digest) is None:
                raise ControlError(400, "invalid_request", "expected digest is invalid")
        result = self.console.state.proposals.edit(proposal_id, expected, fields)
        self.console.state.notify_change()
        self.send_json(200, result)

    def _handle_human_chains(self, payload: dict[str, Any]) -> None:
        extra = set(payload) - {"chains", "expected_digest"}
        if extra:
            raise ControlError(400, "invalid_request", "unsupported fields")
        digest = payload.get("expected_digest")
        if not isinstance(digest, str) or SHA256_HEX_PATTERN.fullmatch(digest) is None:
            raise ControlError(400, "invalid_request", "expected digest is invalid")
        if "chains" not in payload:
            raise ControlError(400, "invalid_request", "chains must be a JSON object")
        chains = require_chain_map(payload.get("chains"))
        result = self.console.state.save_chains(chains, digest)
        self.send_json(200, result)

    def _handle_tool_call(self) -> None:
        origin = self.headers.get("Origin")
        if origin not in {None, ""} and not is_loopback_origin(origin):
            self.refuse_origin()
            return
        if not json_content_type(self.headers.get("Content-Type")):
            self.send_error_payload(400, "invalid_request", "Content-Type must be application/json")
            return
        if self.headers.get(CSRF_HEADER):
            self.send_error_payload(403, "forbidden", "Agent tools cannot carry a CSRF token")
            return
        length_header = self.headers.get("Content-Length")
        try:
            length = int(length_header or "0")
        except ValueError:
            self.send_error_payload(400, "invalid_request", "Content-Length is invalid")
            return
        if length < 0 or length > MAX_MUTATION_BODY_BYTES:
            self.send_error_payload(413, "request_too_large", "Request body is too large")
            return
        body = self.rfile.read(length)
        payload = parse_json_object(body)
        if payload is None:
            self.send_error_payload(400, "invalid_request", "Request body is not a JSON object")
            return
        extra = set(payload) - {"name", "arguments"}
        if extra:
            self.send_error_payload(400, "invalid_request", "unsupported fields")
            return
        tools = self.console.state.tools
        if tools is None:
            self.send_error_payload(404, "not_found", "Tools helper is unavailable")
            return
        try:
            normalized = tools.validate_tool_call(payload.get("name"), payload.get("arguments"))
            result = self.console.state.dispatch_tool(payload["name"], normalized)
            projected = tools.project_tool_result(payload["name"], result)
            self.send_json(200, projected)
        except tools.ToolContractError as error:
            status = 400
            if error.code == "unknown_tool":
                status = 404
            self.send_error_payload(status, error.code, error.message)
        except ControlError as error:
            self.send_error_payload(error.status, error.kind, error.message)


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], state: ConsoleState, site: Path | None) -> None:
        super().__init__(address, ConsoleHandler)
        self.state = state
        self.site = site

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def probe_airlock_console(port: int) -> bool:
    connection: http.client.HTTPConnection | None = None
    deadline = time.monotonic() + CONSOLE_PROBE_DEADLINE_SECONDS
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", port, timeout=CONSOLE_PROBE_DEADLINE_SECONDS
        )
        connection.request("GET", "/healthz")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if connection.sock is not None:
            connection.sock.settimeout(remaining)
        response = connection.getresponse()
        server = response.getheader("Server") or ""
        if not server.startswith("AirlockConsole"):
            response.read(1)
            return False
        length_header = response.getheader("Content-Length")
        if length_header is not None:
            try:
                length = int(length_header)
            except ValueError:
                response.read(1)
                return False
            if length < 0 or length > MAX_CONSOLE_PROBE_BYTES:
                response.read(1)
                return False
        body = response.read(MAX_CONSOLE_PROBE_BYTES + 1)
        if len(body) > MAX_CONSOLE_PROBE_BYTES:
            return False
        if response.status != 200:
            return False
        payload = parse_json_object(body)
        if payload is None or payload.get("ok") is not True:
            return False
        sessions = payload.get("sessions")
        if sessions is not None and non_negative_int(sessions) is None:
            return False
        return True
    except (OSError, http.client.HTTPException, ValueError, TimeoutError):
        return False
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass


def bind_console(port: int, state: ConsoleState, site: Path | None) -> ConsoleServer:
    if port == 0:
        return ConsoleServer(("127.0.0.1", 0), state, site)
    if not 1 <= port <= 65535:
        raise ConsoleError("console port is invalid")
    try:
        return ConsoleServer(("127.0.0.1", port), state, site)
    except OSError:
        if probe_airlock_console(port):
            raise ConsoleError(
                f"console already running at http://127.0.0.1:{port}"
            ) from None
        raise ConsoleError(
            f"port {port} is in use by something that is not an Airlock console"
        ) from None


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="airlock-console")
    result.add_argument("--port", type=int, default=None)
    result.add_argument("--site", default=None)
    result.add_argument("--scan", action="store_true")
    result.add_argument("--once", action="store_true")
    result.add_argument("--access-helper", default=None)
    result.add_argument("--tools-helper", default=None)
    return result


def resolve_site(value: str | None) -> Path | None:
    if value:
        return Path(value)
    return default_site_dir()


def run_once(state: ConsoleState) -> int:
    try:
        state.refresh()
        json.dump(state.overview(), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    except (RecursionError, OverflowError, ValueError, TypeError, MemoryError) as error:
        raise ConsoleError("console overview could not be derived") from error
    return 0


def serve(
    state: ConsoleState,
    port: int,
    site: Path | None,
    *,
    access_helper: Any | None = None,
    address_path: Path | None = None,
) -> int:
    marker_path = address_path or console_address_path(state.runtime_root)
    console_id = secrets.token_hex(32)
    lock: ConsoleLock | None = None
    server: ConsoleServer | None = None
    state_started = False
    previous_signals: list[tuple[int, Any]] = []
    termination_requested = False

    def terminate(_signum: int, _frame: Any) -> None:
        nonlocal termination_requested
        if termination_requested:
            return
        termination_requested = True
        raise _ConsoleTermination()

    def install_shutdown_signals() -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        names = ("SIGINT", "SIGBREAK") if os.name == "nt" else ("SIGTERM", "SIGINT")
        for name in names:
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                previous = signal.signal(sig, terminate)
            except (OSError, RuntimeError, ValueError):
                continue
            previous_signals.append((sig, previous))

    def restore_shutdown_signals() -> None:
        while previous_signals:
            sig, previous = previous_signals.pop()
            try:
                signal.signal(sig, previous)
            except (OSError, RuntimeError, ValueError):
                continue

    try:
        lock = acquire_console_lock(marker_path.parent, access_helper=access_helper)
        install_shutdown_signals()
        server = bind_console(port, state, site)
        write_console_address(
            marker_path,
            int(server.server_port),
            console_id,
            access_helper=access_helper,
        )
        state_started = True
        state.start()
        server.serve_forever(poll_interval=0.25)
    except (_ConsoleTermination, KeyboardInterrupt):
        pass
    finally:
        try:
            if state_started:
                state.stop()
        finally:
            try:
                remove_console_address(marker_path, console_id)
            finally:
                try:
                    if server is not None:
                        server.server_close()
                finally:
                    try:
                        if lock is not None:
                            lock.release()
                    finally:
                        restore_shutdown_signals()
    return 0


def main() -> int:
    args = parser().parse_args()
    try:
        access_helper = None
        tools_helper = None
        try:
            access_helper = load_access_helper(
                Path(args.access_helper) if args.access_helper else None
            )
        except ConsoleError:
            access_helper = None
        try:
            tools_helper = load_tools_helper(
                Path(args.tools_helper) if args.tools_helper else None
            )
        except ConsoleError:
            tools_helper = None
        history = None
        try:
            history_module = load_helper_module(
                sibling_helper_path("airlock_console_history.py"),
                "airlock_console_history",
                ("HistoryIndex",),
            )
            history = history_module.HistoryIndex(
                claude_projects_root(),
                console_runtime_root(),
                window_for=native_window_for,
            )
        except (ConsoleError, OSError, ValueError, TypeError):
            history = None
        default_port, default_address_path = shared_console_defaults(tools_helper)
        state = ConsoleState(
            console_runtime_root(),
            scan=args.scan,
            history=history,
            # Plain Claude Code sessions are listed read-only unless switched
            # off for this console.
            native=os.environ.get("AIRLOCK_CONSOLE_NATIVE", "").strip().lower() != "off",
            chains=ChainBackend(access_helper),
            tools=tools_helper,
        )
        if args.once:
            return run_once(state)
        return serve(
            state,
            args.port if args.port is not None else default_port,
            resolve_site(args.site),
            access_helper=access_helper,
            address_path=default_address_path,
        )
    except (OSError, ConsoleError, ValueError, json.JSONDecodeError) as exc:
        print(f"airlock-console: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
