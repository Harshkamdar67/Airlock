#!/usr/bin/env python3
"""Session history for the Airlock Console.

An incremental index over the transcripts Claude Code keeps under
``~/.claude/projects``. For every session it records what a person asks about
a conversation after the fact: when it ran, on which models, how many turns
and tool calls it took, how large the context grew, how often it was
compacted, which subagents it spawned, and when it ran through Airlock rather
than plain Claude Code. The index keeps counts, timestamps, titles, and
identifiers only. It never stores prompts, replies, or tool output.

The scan is incremental. Each transcript is read from the byte offset the
previous scan stopped at, so a long history costs one full pass and then only
the new lines. The state is written to a private file under the console
runtime root so a restart does not repeat the pass.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = 3
CACHE_FILENAME = "console-history.json"
MAX_DAYS_PER_SESSION = 400
MAX_TOOL_NAMES = 64
MAX_AGENT_TYPES = 64
MAX_QUERY_ROWS = 500
QUERY_DIMENSIONS = (
    "session", "project", "model", "provider", "agent_type", "tool",
    "day", "week", "month", "weekday", "branch", "entrypoint",
)
QUERY_MEASURES = (
    "sessions", "prompts", "replies", "tool_calls", "output_tokens",
    "compactions", "agent_launches", "peak_context_max",
)
QUERY_DEFAULT_ORDER = {
    "session": "prompts", "project": "prompts", "model": "replies", "provider": "replies",
    "agent_type": "agent_launches", "tool": "tool_calls", "weekday": "prompts",
    "branch": "prompts", "entrypoint": "prompts",
}
LAUNCH_LOG_FILENAME = "launches.jsonl"
MAX_LINE_BYTES = 8 * 1024 * 1024
MAX_SCAN_BYTES_PER_REFRESH = 96 * 1024 * 1024
MAX_PROJECT_DIRS = 500
MAX_FILES_PER_DIR = 400
MAX_SESSIONS = 5000
MAX_AGENTS_PER_SESSION = 200
MAX_LAUNCH_RECORDS = 20000
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
AGENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{3,63}$")
AGENT_TOOL_NAMES = {"Agent", "Task"}
HISTORY_ID_PREFIX = "hx-"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not (10 <= len(value) <= 40):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def bounded_str(value: object, limit: int = 400) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


def non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _weekday_name(day: str) -> str:
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%a")
    except ValueError:
        return "unknown"


def project_name(workdir: str | None) -> str:
    if not workdir:
        return ""
    trimmed = workdir.rstrip("\\/")
    return re.split(r"[\\/]", trimmed)[-1] if trimmed else workdir


def normalized_workdir(value: object) -> str | None:
    text = bounded_str(value)
    if not text:
        return None
    return os.path.normcase(os.path.normpath(text.replace("/", os.sep)))


def model_provider(model: str | None) -> str:
    lowered = (model or "").lower()
    if lowered.startswith("gpt-") or lowered.startswith("o1") or lowered.startswith("o3"):
        return "openai"
    if lowered.startswith("grok-"):
        return "grok"
    if lowered.startswith("openmodel/"):
        return "openmodel"
    if "/" in lowered:
        return "openrouter"
    return "anthropic"


# ---- one transcript ------------------------------------------------------


def empty_stats() -> dict[str, Any]:
    return {
        "session_id": None,
        "cwd": None,
        "title": None,
        "branch": None,
        "version": None,
        "entrypoints": {},
        "models": {},
        "first_at": None,
        "last_at": None,
        "prompts": 0,
        "replies": 0,
        "tool_calls": 0,
        "compactions": 0,
        "compact_summaries": 0,
        "peak_context": None,
        "last_context": None,
        "output_tokens": 0,
        "agents": {},
        "agent_calls": 0,
        "days": {},
        "tools": {},
        "agent_types": {},
        "compactions_by_model": {},
        "_pending_agents": {},
        "_last_model": None,
    }


def _day_bucket(stats: dict[str, Any], stamp: datetime | None) -> dict[str, Any] | None:
    """The per-day counters a timestamp falls into, bounded per session."""
    if stamp is None:
        return None
    key = stamp.strftime("%Y-%m-%d")
    days = stats.setdefault("days", {})
    bucket = days.get(key)
    if bucket is None:
        if len(days) >= MAX_DAYS_PER_SESSION:
            return None
        # p prompts, r replies, t tool calls, o output tokens, c compactions;
        # m replies, tm tool calls, om output tokens, cm compactions by model;
        # a subagent launches by type; tl tool calls by tool name.
        bucket = {"p": 0, "r": 0, "t": 0, "o": 0, "c": 0, "m": {}, "a": {}, "tm": {}, "om": {}, "cm": {}, "tl": {}}
        days[key] = bucket
    return bucket


def _count(table: dict[str, int], key: str | None, limit: int, amount: int = 1) -> None:
    if not key or amount <= 0:
        return
    if key in table or len(table) < limit:
        table[key] = table.get(key, 0) + amount


def _note_time(stats: dict[str, Any], stamp: datetime | None) -> None:
    if stamp is None:
        return
    iso = format_timestamp(stamp)
    if stats["first_at"] is None or iso < stats["first_at"]:
        stats["first_at"] = iso
    if stats["last_at"] is None or iso > stats["last_at"]:
        stats["last_at"] = iso


def _usage_context(usage: object) -> int | None:
    if not isinstance(usage, dict):
        return None
    parts = [
        non_negative_int(usage.get(key))
        for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    ]
    if all(part is None for part in parts):
        return None
    return sum(part or 0 for part in parts)


def update_stats(stats: dict[str, Any], record: dict[str, Any], *, own_chain: bool = False) -> None:
    """Fold one transcript record into the running statistics.

    ``own_chain`` is set for a subagent's own transcript, where every record
    is marked as a sidechain and is nevertheless the conversation to count.
    """
    kind = record.get("type")
    stamp = parse_timestamp(record.get("timestamp"))
    sidechain = record.get("isSidechain") is True and not own_chain
    if stats["session_id"] is None:
        candidate = bounded_str(record.get("sessionId"), 64)
        if candidate and SESSION_ID_PATTERN.match(candidate):
            stats["session_id"] = candidate
    if stats["cwd"] is None:
        stats["cwd"] = bounded_str(record.get("cwd"))
    if kind == "ai-title":
        title = bounded_str(record.get("aiTitle"), 160)
        if title:
            stats["title"] = " ".join(title.split())
        return
    branch = bounded_str(record.get("gitBranch"), 120)
    if branch:
        stats["branch"] = branch
    version = bounded_str(record.get("version"), 40)
    if version:
        stats["version"] = version
    entry = bounded_str(record.get("entrypoint"), 40)
    if entry and not sidechain:
        stats["entrypoints"][entry] = stats["entrypoints"].get(entry, 0) + 1
    if kind == "system":
        if record.get("subtype") == "compact_boundary":
            stats["compactions"] += 1
            _note_time(stats, stamp)
            bucket = _day_bucket(stats, stamp)
            if bucket is not None:
                bucket["c"] += 1
            # The model whose context filled is the one that replied last.
            last_model = stats.get("_last_model")
            if last_model:
                _count(stats.setdefault("compactions_by_model", {}), last_model, 32)
                if bucket is not None:
                    _count(bucket.setdefault("cm", {}), last_model, 32)
        return
    if sidechain:
        return
    message = record.get("message")
    if kind == "user":
        _note_time(stats, stamp)
        if record.get("isCompactSummary") is True:
            stats["compact_summaries"] += 1
            return
        content = message.get("content") if isinstance(message, dict) else None
        bucket = _day_bucket(stats, stamp)
        if isinstance(content, str):
            stats["prompts"] += 1
            if bucket is not None:
                bucket["p"] += 1
        elif isinstance(content, list):
            if any(isinstance(b, dict) and b.get("type") == "text" for b in content):
                stats["prompts"] += 1
                if bucket is not None:
                    bucket["p"] += 1
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool_use_id = bounded_str(block.get("tool_use_id"), 120)
                pending = stats["_pending_agents"].pop(tool_use_id, None) if tool_use_id else None
                result = record.get("toolUseResult")
                if pending is None and not isinstance(result, dict):
                    continue
                agent_id = bounded_str(result.get("agentId"), 64) if isinstance(result, dict) else None
                if agent_id and not AGENT_ID_PATTERN.match(agent_id):
                    agent_id = None
                if agent_id is None and pending is None:
                    continue
                if agent_id is None:
                    agent_id = f"pending-{tool_use_id}"
                if len(stats["agents"]) >= MAX_AGENTS_PER_SESSION and agent_id not in stats["agents"]:
                    continue
                entry_record = stats["agents"].setdefault(agent_id, {})
                if pending:
                    entry_record.update(pending)
                if isinstance(result, dict):
                    model = bounded_str(result.get("resolvedModel"), 160)
                    if model:
                        entry_record["model"] = model
                    status = bounded_str(result.get("status"), 40)
                    if status:
                        entry_record["status"] = status
                    if isinstance(result.get("isAsync"), bool):
                        entry_record["background"] = result["isAsync"]
                    description = bounded_str(result.get("description"), 160)
                    if description and not entry_record.get("description"):
                        entry_record["description"] = description
                if stamp is not None:
                    entry_record["finished_at"] = format_timestamp(stamp)
        return
    if kind == "assistant" and isinstance(message, dict):
        _note_time(stats, stamp)
        stats["replies"] += 1
        bucket = _day_bucket(stats, stamp)
        if bucket is not None:
            bucket["r"] += 1
        model = bounded_str(message.get("model"), 160)
        # Claude Code writes "<synthetic>" for messages it made up itself.
        if model and not model.startswith("<"):
            stats["models"][model] = stats["models"].get(model, 0) + 1
            stats["_last_model"] = model
            if bucket is not None:
                _count(bucket["m"], model, 32)
        else:
            model = None
        context = _usage_context(message.get("usage"))
        if context is not None:
            stats["last_context"] = context
            if stats["peak_context"] is None or context > stats["peak_context"]:
                stats["peak_context"] = context
        usage = message.get("usage")
        if isinstance(usage, dict):
            output = non_negative_int(usage.get("output_tokens"))
            if output:
                stats["output_tokens"] += output
                if bucket is not None:
                    bucket["o"] += output
                    if model:
                        _count(bucket.setdefault("om", {}), model, 32, output)
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                stats["tool_calls"] += 1
                if bucket is not None:
                    bucket["t"] += 1
                    if model:
                        _count(bucket.setdefault("tm", {}), model, 32)
                name = bounded_str(block.get("name"), 64)
                _count(stats.setdefault("tools", {}), name, MAX_TOOL_NAMES)
                if bucket is not None:
                    _count(bucket.setdefault("tl", {}), name, 48)
                if name in AGENT_TOOL_NAMES:
                    stats["agent_calls"] += 1
                    arguments = block.get("input")
                    tool_use_id = bounded_str(block.get("id"), 120)
                    agent_type = bounded_str(arguments.get("subagent_type"), 80) if isinstance(arguments, dict) else None
                    _count(stats.setdefault("agent_types", {}), agent_type or "general-purpose", MAX_AGENT_TYPES)
                    if bucket is not None:
                        _count(bucket["a"], agent_type or "general-purpose", 32)
                    pending = {
                        "agent_type": agent_type,
                        "description": bounded_str(arguments.get("description"), 160) if isinstance(arguments, dict) else None,
                        "background": bool(arguments.get("run_in_background")) if isinstance(arguments, dict) else False,
                        "started_at": format_timestamp(stamp) if stamp else None,
                    }
                    if tool_use_id and len(stats["_pending_agents"]) < MAX_AGENTS_PER_SESSION:
                        stats["_pending_agents"][tool_use_id] = pending


def scan_transcript(
    path: Path, entry: dict[str, Any] | None, *, own_chain: bool = False
) -> dict[str, Any] | None:
    """Advance the index entry for one transcript. Returns the new entry.

    The entry carries the byte offset already folded in and the partial line
    left over, so the next call continues where this one stopped. A file that
    shrank is scanned again from the start.
    """
    try:
        details = path.stat()
        if not stat.S_ISREG(details.st_mode) or path.is_symlink():
            return None
    except OSError:
        return None
    entry = dict(entry or {})
    stats = entry.get("stats")
    offset = non_negative_int(entry.get("offset")) or 0
    if not isinstance(stats, dict) or offset > details.st_size:
        stats = empty_stats()
        offset = 0
        entry["carry"] = ""
    stats.setdefault("_pending_agents", {})
    carry = entry.get("carry") if isinstance(entry.get("carry"), str) else ""
    if offset == details.st_size and entry.get("mtime") == details.st_mtime:
        entry.update({"stats": stats, "offset": offset, "size": details.st_size, "mtime": details.st_mtime, "carry": carry})
        return entry
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            buffer = carry.encode("utf-8", "replace")
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                buffer += chunk
                offset += len(chunk)
                while True:
                    newline = buffer.find(b"\n")
                    if newline < 0:
                        break
                    line = buffer[:newline]
                    buffer = buffer[newline + 1 :]
                    if 0 < len(line) <= MAX_LINE_BYTES and line.lstrip().startswith(b"{"):
                        try:
                            record = json.loads(line.decode("utf-8", "replace"))
                        except ValueError:
                            continue
                        if isinstance(record, dict):
                            update_stats(stats, record, own_chain=own_chain)
                if len(buffer) > MAX_LINE_BYTES:
                    buffer = b""
            carry = buffer.decode("utf-8", "replace") if len(buffer) <= MAX_LINE_BYTES else ""
    except OSError:
        return None
    entry.update({"stats": stats, "offset": offset, "size": details.st_size, "mtime": details.st_mtime, "carry": carry})
    return entry


# ---- launch log ------------------------------------------------------------


def read_launches(path: Path) -> list[dict[str, Any]]:
    """Router start and stop records, oldest first, bounded."""
    records: list[dict[str, Any]] = []
    for candidate in (path.with_suffix(".1.jsonl"), path):
        try:
            if not candidate.is_file() or candidate.is_symlink():
                continue
            with candidate.open("rb") as handle:
                for raw in handle:
                    if len(raw) > 4096:
                        continue
                    try:
                        record = json.loads(raw.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    if not isinstance(record, dict) or record.get("schema_version") != 1:
                        continue
                    event = record.get("event")
                    at = parse_timestamp(record.get("at"))
                    instance = bounded_str(record.get("instance_id"), 64)
                    if event not in {"start", "stop"} or at is None or not instance:
                        continue
                    records.append({
                        "event": event,
                        "at": at,
                        "instance_id": instance,
                        "workdir": bounded_str(record.get("workdir")),
                        "profile": bounded_str(record.get("profile"), 64),
                        "root_model": bounded_str(record.get("root_model"), 160),
                    })
                    if len(records) >= MAX_LAUNCH_RECORDS:
                        return records
        except OSError:
            continue
    return records


def airlock_periods(
    launches: list[dict[str, Any]],
    workdir: str | None,
    first_at: str | None,
    last_at: str | None,
    now: datetime,
) -> list[dict[str, Any]]:
    """When a directory's sessions ran through an Airlock router.

    Each period is one router's lifetime. A router without a stop record is
    still running, so its period ends now. Periods outside the session's own
    span are dropped.
    """
    key = normalized_workdir(workdir)
    if key is None:
        return []
    begin = parse_timestamp(first_at)
    end = parse_timestamp(last_at)
    open_routers: dict[str, dict[str, Any]] = {}
    periods: list[dict[str, Any]] = []
    for record in launches:
        if normalized_workdir(record.get("workdir")) != key:
            continue
        if record["event"] == "start":
            open_routers[record["instance_id"]] = record
            continue
        started = open_routers.pop(record["instance_id"], None)
        if started is None:
            continue
        periods.append({
            "from": started["at"], "to": record["at"], "profile": started.get("profile"),
            "root_model": started.get("root_model"), "instance_id": record["instance_id"],
        })
    for started in open_routers.values():
        periods.append({
            "from": started["at"], "to": now, "profile": started.get("profile"),
            "root_model": started.get("root_model"), "instance_id": started["instance_id"],
            "open": True,
        })
    kept: list[dict[str, Any]] = []
    for period in sorted(periods, key=lambda item: item["from"]):
        if end is not None and period["from"] > end:
            continue
        if begin is not None and period["to"] < begin:
            continue
        kept.append({
            "kind": "airlock",
            "from": format_timestamp(period["from"]),
            "to": format_timestamp(period["to"]),
            "profile": period.get("profile"),
            "root_model": period.get("root_model"),
            "open": bool(period.get("open")),
        })
    return kept


# ---- the index -------------------------------------------------------------


class HistoryIndex:
    """Incremental index of every session transcript on this machine."""

    def __init__(
        self,
        projects_root: Path,
        runtime_root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        window_for: Callable[[str | None], int | None] | None = None,
    ) -> None:
        self.projects_root = projects_root
        self.runtime_root = runtime_root
        self.cache_path = runtime_root / CACHE_FILENAME
        self.launch_log_path = runtime_root / LAUNCH_LOG_FILENAME
        self.clock = clock or utc_now
        self.window_for = window_for or (lambda _model: None)
        self.files: dict[str, dict[str, Any]] = {}
        self.launches: list[dict[str, Any]] = []
        self.last_refresh: datetime | None = None
        self._load_cache()

    # -- persistence ---------------------------------------------------------

    def _load_cache(self) -> None:
        try:
            if not self.cache_path.is_file() or self.cache_path.is_symlink():
                return
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            return
        files = payload.get("files")
        if isinstance(files, dict):
            self.files = {
                key: value for key, value in files.items()
                if isinstance(key, str) and isinstance(value, dict) and isinstance(value.get("stats"), dict)
            }

    def save(self) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "files": self.files}
        try:
            self.runtime_root.mkdir(parents=True, exist_ok=True)
            fd, staging = tempfile.mkstemp(prefix=".console-history.", suffix=".tmp", dir=str(self.runtime_root))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, separators=(",", ":"))
                try:
                    os.chmod(staging, 0o600)
                except OSError:
                    pass
                os.replace(staging, self.cache_path)
            except BaseException:
                try:
                    os.unlink(staging)
                except OSError:
                    pass
                raise
        except (OSError, ValueError, TypeError):
            return

    # -- scanning ------------------------------------------------------------

    def _transcript_paths(self) -> list[Path]:
        paths: list[Path] = []
        try:
            if not self.projects_root.is_dir() or self.projects_root.is_symlink():
                return paths
            directories = sorted(self.projects_root.iterdir())[:MAX_PROJECT_DIRS]
        except OSError:
            return paths
        for directory in directories:
            try:
                if not directory.is_dir() or directory.is_symlink():
                    continue
                files = [
                    entry for entry in directory.iterdir()
                    if entry.suffix == ".jsonl" and not entry.name.startswith(".")
                ][:MAX_FILES_PER_DIR]
            except OSError:
                continue
            paths.extend(files)
        return paths

    def refresh(self, *, budget_bytes: int = MAX_SCAN_BYTES_PER_REFRESH) -> int:
        """Fold new transcript bytes into the index. Returns bytes scanned."""
        scanned = 0
        seen: set[str] = set()
        for path in self._transcript_paths():
            key = str(path)
            seen.add(key)
            entry = self.files.get(key)
            try:
                details = path.stat()
            except OSError:
                continue
            if entry and entry.get("offset") == details.st_size and entry.get("mtime") == details.st_mtime:
                continue
            if scanned >= budget_bytes:
                continue
            before = non_negative_int((entry or {}).get("offset")) or 0
            updated = scan_transcript(path, entry)
            if updated is None:
                continue
            scanned += max(0, (updated.get("offset") or 0) - before)
            self.files[key] = updated
        for key in list(self.files):
            if key not in seen:
                del self.files[key]
        if len(self.files) > MAX_SESSIONS:
            ordered = sorted(self.files.items(), key=lambda item: (item[1].get("stats") or {}).get("last_at") or "")
            for key, _entry in ordered[: len(self.files) - MAX_SESSIONS]:
                del self.files[key]
        self.launches = read_launches(self.launch_log_path)
        self.last_refresh = self.clock()
        self.save()
        return scanned

    # -- views ----------------------------------------------------------------

    @staticmethod
    def history_id(session_id: str) -> str:
        return HISTORY_ID_PREFIX + session_id[:12]

    def _summary(self, key: str, entry: dict[str, Any]) -> dict[str, Any] | None:
        stats = entry.get("stats") or {}
        session_id = stats.get("session_id")
        if not session_id:
            return None
        models = stats.get("models") or {}
        primary = max(models.items(), key=lambda item: item[1])[0] if models else None
        # A session can hand off between models with different windows, and
        # the peak may have happened on the largest one, so report the largest
        # window any of its models had. A peak that still exceeds it means the
        # window is not known, not that the session ran past it.
        windows = [w for w in (self.window_for(model) for model in models) if w]
        window = max(windows) if windows else None
        peak = stats.get("peak_context")
        if window and peak and peak > window:
            window = None
        agents = stats.get("agents") or {}
        return {
            "id": self.history_id(session_id),
            "session_id": session_id,
            "project": project_name(stats.get("cwd")),
            "workdir": stats.get("cwd"),
            "title": stats.get("title"),
            "branch": stats.get("branch"),
            "models": sorted(models, key=lambda model: -models[model]),
            "primary_model": primary,
            "provider": model_provider(primary),
            "started_at": stats.get("first_at"),
            "last_activity_at": stats.get("last_at"),
            "prompts": stats.get("prompts", 0),
            "replies": stats.get("replies", 0),
            "tool_calls": stats.get("tool_calls", 0),
            "compactions": max(stats.get("compactions", 0), stats.get("compact_summaries", 0)),
            "peak_context": stats.get("peak_context"),
            "last_context": stats.get("last_context"),
            "window": window,
            "output_tokens": stats.get("output_tokens", 0),
            "subagents": len(agents),
            "entrypoint": max(stats.get("entrypoints", {}).items(), key=lambda item: item[1])[0] if stats.get("entrypoints") else None,
            "version": stats.get("version"),
            "path": key,
        }

    def summaries(self) -> list[dict[str, Any]]:
        items = []
        for key, entry in self.files.items():
            summary = self._summary(key, entry)
            if summary is not None:
                items.append(summary)
        items.sort(key=lambda item: item.get("last_activity_at") or "", reverse=True)
        return items

    def find(self, history_id: str) -> tuple[str, dict[str, Any]] | None:
        for key, entry in self.files.items():
            stats = entry.get("stats") or {}
            session_id = stats.get("session_id")
            if session_id and self.history_id(session_id) == history_id:
                return key, entry
        return None

    def find_by_session_id(self, session_id: str) -> tuple[str, dict[str, Any]] | None:
        for key, entry in self.files.items():
            if (entry.get("stats") or {}).get("session_id") == session_id:
                return key, entry
        return None

    def filtered(
        self,
        *,
        project: str | None = None,
        model: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        query: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        needle = (query or "").strip().lower()
        wanted_project = (project or "").strip().lower()
        wanted_model = (model or "").strip().lower()
        out: list[dict[str, Any]] = []
        for item in self.summaries():
            if wanted_project and (item.get("project") or "").lower() != wanted_project:
                continue
            if wanted_model and not any(wanted_model in m.lower() for m in item.get("models") or []):
                continue
            last = parse_timestamp(item.get("last_activity_at"))
            first = parse_timestamp(item.get("started_at"))
            if since is not None and (last is None or last < since):
                continue
            if until is not None and (first is None or first > until):
                continue
            if needle:
                hay = " ".join(
                    str(item.get(field) or "") for field in ("title", "project", "branch", "workdir", "session_id")
                ).lower()
                if needle not in hay:
                    continue
            out.append(item)
            if len(out) >= limit:
                break
        return out

    def detail(self, history_id: str, now: datetime | None = None) -> dict[str, Any] | None:
        found = self.find(history_id)
        if found is None:
            return None
        key, entry = found
        summary = self._summary(key, entry)
        if summary is None:
            return None
        stats = entry.get("stats") or {}
        agents = []
        for agent_id, record in (stats.get("agents") or {}).items():
            agents.append({"id": agent_id, **{k: v for k, v in record.items()}})
        agents.sort(key=lambda item: item.get("started_at") or "")
        summary["agents"] = agents
        summary["periods"] = airlock_periods(
            self.launches, stats.get("cwd"), stats.get("first_at"), stats.get("last_at"), now or self.clock()
        )
        summary["airlock_inferred"] = any(model_provider(m) != "anthropic" for m in summary.get("models") or [])
        summary["compactions_by_model"] = dict(stats.get("compactions_by_model") or {})
        summary["tools"] = dict(stats.get("tools") or {})
        summary["agent_types"] = dict(stats.get("agent_types") or {})
        return summary

    def stats_for_session(self, session_id: str, now: datetime | None = None) -> dict[str, Any] | None:
        """The history facts for a live session, by Claude Code session id."""
        found = self.find_by_session_id(session_id)
        if found is None:
            return None
        return self.detail(self.history_id(session_id), now)

    def stats_for_workdir(self, workdir: str | None, now: datetime | None = None) -> dict[str, Any] | None:
        """The most recently active session in a directory, for routed sessions."""
        key = normalized_workdir(workdir)
        if key is None:
            return None
        best: dict[str, Any] | None = None
        for item in self.summaries():
            if normalized_workdir(item.get("workdir")) != key:
                continue
            best = item
            break
        if best is None:
            return None
        return self.detail(best["id"], now)

    # -- usage ------------------------------------------------------------------

    @staticmethod
    def _period_key(day: str, group: str) -> str:
        if group == "month":
            return day[:7]
        if group == "week":
            try:
                year, week, _weekday = datetime.strptime(day, "%Y-%m-%d").isocalendar()
            except ValueError:
                return day
            return f"{year}-W{week:02d}"
        return day

    def usage(
        self,
        *,
        group: str = "day",
        project: str | None = None,
        model: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Counts over time and rankings, from the per-day buckets of every session.

        Everything is a count of things that happened, never a cost estimate:
        prompts, replies, tool calls, output tokens, compactions, sessions active,
        and which models, subagent types, tools, and projects carried the work.
        """
        group = group if group in {"day", "week", "month"} else "day"
        wanted_project = (project or "").strip().lower()
        wanted_model = (model or "").strip().lower()
        since_key = since.strftime("%Y-%m-%d") if since else None
        until_key = until.strftime("%Y-%m-%d") if until else None
        periods: dict[str, dict[str, Any]] = {}
        totals = {
            "sessions": 0, "prompts": 0, "replies": 0, "tool_calls": 0, "output_tokens": 0,
            "compactions": 0, "subagents": 0, "peak_context_max": 0,
        }
        agent_types: dict[str, int] = {}
        tools: dict[str, int] = {}
        projects: dict[str, dict[str, Any]] = {}
        models: dict[str, int] = {}
        peaks: list[dict[str, Any]] = []
        for key, entry in self.files.items():
            stats = entry.get("stats") or {}
            if not stats.get("session_id"):
                continue
            item_project = project_name(stats.get("cwd"))
            if wanted_project and item_project.lower() != wanted_project:
                continue
            session_models = stats.get("models") or {}
            if wanted_model and not any(wanted_model in m.lower() for m in session_models):
                continue
            days = stats.get("days") or {}
            in_range = {
                day: bucket for day, bucket in days.items()
                if (since_key is None or day >= since_key) and (until_key is None or day <= until_key)
            }
            if not in_range:
                continue
            totals["sessions"] += 1
            summary = self._summary(key, entry) or {}
            counted_agents = len(stats.get("agents") or {})
            totals["subagents"] += counted_agents
            peak = stats.get("peak_context")
            if isinstance(peak, int):
                totals["peak_context_max"] = max(totals["peak_context_max"], peak)
                peaks.append({
                    "id": summary.get("id"), "project": item_project, "title": stats.get("title"),
                    "peak_context": peak, "window": summary.get("window"),
                })
            for name, count in (stats.get("agent_types") or {}).items():
                agent_types[name] = agent_types.get(name, 0) + count
            for name, count in (stats.get("tools") or {}).items():
                tools[name] = tools.get(name, 0) + count
            project_row = projects.setdefault(item_project, {"name": item_project, "sessions": 0, "prompts": 0, "tool_calls": 0, "output_tokens": 0})
            project_row["sessions"] += 1
            for day, bucket in in_range.items():
                period = periods.setdefault(self._period_key(day, group), {
                    "period": self._period_key(day, group), "sessions": set(), "prompts": 0, "replies": 0,
                    "tool_calls": 0, "output_tokens": 0, "compactions": 0, "by_provider": {},
                })
                period["sessions"].add(stats["session_id"])
                for source, target in (("p", "prompts"), ("r", "replies"), ("t", "tool_calls"), ("o", "output_tokens"), ("c", "compactions")):
                    value = bucket.get(source, 0)
                    period[target] += value
                    if target in totals:
                        totals[target] += value
                    if target in project_row:
                        project_row[target] += value
                for model_name, replies in (bucket.get("m") or {}).items():
                    provider = model_provider(model_name)
                    period["by_provider"][provider] = period["by_provider"].get(provider, 0) + replies
                    models[model_name] = models.get(model_name, 0) + replies
        series = []
        for period_key in sorted(periods):
            row = dict(periods[period_key])
            row["sessions"] = len(row["sessions"])
            series.append(row)
        peaks.sort(key=lambda item: item["peak_context"], reverse=True)
        # How close sessions came to their window: the compaction-territory
        # question, as a distribution over every session in range.
        peak_buckets = {"under_25": 0, "25_to_50": 0, "50_to_75": 0, "over_75": 0, "unknown": 0}
        for item in peaks:
            window = item.get("window")
            if not window:
                peak_buckets["unknown"] += 1
                continue
            share = item["peak_context"] / window
            key = "under_25" if share < 0.25 else "25_to_50" if share < 0.5 else "50_to_75" if share < 0.75 else "over_75"
            peak_buckets[key] += 1
        peak_buckets["unknown"] += max(0, totals["sessions"] - len(peaks))
        return {
            "generated_at": format_timestamp(now or self.clock()),
            "group": group,
            "peak_buckets": peak_buckets,
            "since": format_timestamp(since) if since else None,
            "until": format_timestamp(until) if until else None,
            "filters": {"project": project or None, "model": model or None},
            "totals": totals,
            "series": series,
            "agent_types": sorted(({"name": k, "count": v} for k, v in agent_types.items()), key=lambda r: -r["count"])[:20],
            "tools": sorted(({"name": k, "count": v} for k, v in tools.items()), key=lambda r: -r["count"])[:20],
            "projects": sorted(projects.values(), key=lambda r: (-r["prompts"], -r["tool_calls"]))[:20],
            "models": sorted(
                ({"name": k, "replies": v, "provider": model_provider(k)} for k, v in models.items()),
                key=lambda r: -r["replies"],
            )[:20],
            "peaks": peaks[:10],
        }

    # -- general query ------------------------------------------------------

    def query(
        self,
        *,
        dimension: str = "project",
        project: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        agent_type: str | None = None,
        tool: str | None = None,
        branch: str | None = None,
        entrypoint: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        query: str | None = None,
        order_by: str | None = None,
        descending: bool = True,
        limit: int = 50,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Group every counted thing by one dimension, under session filters.

        This is the tool an agent reaches for when no fixed report answers the
        question: "which model compacted most in project X" is
        ``dimension="model", project="X", order_by="compactions"``. Filters
        select sessions; ``since``/``until`` then select the days inside them.
        A measure the dimension cannot attribute is null, never zero.
        """
        if dimension not in QUERY_DIMENSIONS:
            raise ValueError("unknown dimension")
        if order_by is not None and order_by != "key" and order_by not in QUERY_MEASURES:
            raise ValueError("unknown order_by")
        limit = max(1, min(int(limit), MAX_QUERY_ROWS))
        wanted_project = (project or "").strip().lower()
        wanted_model = (model or "").strip().lower()
        wanted_provider = (provider or "").strip().lower()
        wanted_agent = (agent_type or "").strip().lower()
        wanted_tool = (tool or "").strip().lower()
        wanted_branch = (branch or "").strip().lower()
        wanted_entry = (entrypoint or "").strip().lower()
        needle = (query or "").strip().lower()
        since_key = since.strftime("%Y-%m-%d") if since else None
        until_key = until.strftime("%Y-%m-%d") if until else None
        per_model = dimension in {"model", "provider"}
        session_like = dimension in {"session", "project", "branch", "entrypoint", "day", "week", "month", "weekday"}
        rows: dict[str, dict[str, Any]] = {}

        def row_for(key: str, *, history_id: str | None = None, label: str | None = None) -> dict[str, Any]:
            row = rows.get(key)
            if row is None:
                row = {
                    "key": key, "id": history_id, "label": label, "sessions": set(),
                    "prompts": 0 if session_like else None,
                    "replies": 0 if session_like or per_model else None,
                    "tool_calls": 0 if session_like or per_model or dimension == "tool" else None,
                    "output_tokens": 0 if session_like or per_model else None,
                    "compactions": 0 if session_like or per_model else None,
                    "agent_launches": 0 if session_like or dimension == "agent_type" else None,
                    "peak_context_max": 0 if session_like else None,
                }
                rows[key] = row
            return row

        def add(row: dict[str, Any], measure: str, amount: int) -> None:
            if row.get(measure) is None:
                return
            row[measure] += amount

        sessions_matched = 0
        for key, entry in self.files.items():
            stats = entry.get("stats") or {}
            session_id = stats.get("session_id")
            if not session_id:
                continue
            item_project = project_name(stats.get("cwd"))
            if wanted_project and item_project.lower() != wanted_project:
                continue
            session_models = stats.get("models") or {}
            if wanted_model and not any(wanted_model in m.lower() for m in session_models):
                continue
            if wanted_provider and not any(model_provider(m) == wanted_provider for m in session_models):
                continue
            if wanted_agent and not any(wanted_agent in a.lower() for a in stats.get("agent_types") or {}):
                continue
            if wanted_tool and not any(wanted_tool in t.lower() for t in stats.get("tools") or {}):
                continue
            item_branch = stats.get("branch") or ""
            if wanted_branch and wanted_branch not in item_branch.lower():
                continue
            entrypoints = stats.get("entrypoints") or {}
            item_entry = max(entrypoints.items(), key=lambda kv: kv[1])[0] if entrypoints else ""
            if wanted_entry and item_entry.lower() != wanted_entry:
                continue
            if needle:
                hay = " ".join(str(stats.get(f) or "") for f in ("title", "branch", "cwd", "session_id")).lower()
                if needle not in hay and needle not in item_project.lower():
                    continue
            days = stats.get("days") or {}
            in_range = {
                day: bucket for day, bucket in days.items()
                if (since_key is None or day >= since_key) and (until_key is None or day <= until_key)
            }
            if not in_range:
                continue
            sessions_matched += 1
            history_id = self.history_id(session_id)
            peak = stats.get("peak_context") if isinstance(stats.get("peak_context"), int) else 0
            for day, bucket in in_range.items():
                if session_like:
                    if dimension == "session":
                        row = row_for(history_id, history_id=history_id, label=stats.get("title") or item_project)
                    elif dimension == "project":
                        row = row_for(item_project or "unknown")
                    elif dimension == "branch":
                        row = row_for(item_branch or "unknown")
                    elif dimension == "entrypoint":
                        row = row_for(item_entry or "unknown")
                    elif dimension == "weekday":
                        row = row_for(_weekday_name(day))
                    else:
                        row = row_for(self._period_key(day, dimension))
                    row["sessions"].add(session_id)
                    add(row, "prompts", bucket.get("p", 0))
                    add(row, "replies", bucket.get("r", 0))
                    add(row, "tool_calls", bucket.get("t", 0))
                    add(row, "output_tokens", bucket.get("o", 0))
                    add(row, "compactions", bucket.get("c", 0))
                    add(row, "agent_launches", sum((bucket.get("a") or {}).values()))
                    row["peak_context_max"] = max(row["peak_context_max"], peak)
                elif per_model:
                    names = (
                        set(bucket.get("m") or {}) | set(bucket.get("tm") or {})
                        | set(bucket.get("om") or {}) | set(bucket.get("cm") or {})
                    )
                    for name in names:
                        if wanted_model and wanted_model not in name.lower():
                            continue
                        if wanted_provider and model_provider(name) != wanted_provider:
                            continue
                        row = row_for(model_provider(name) if dimension == "provider" else name)
                        row["sessions"].add(session_id)
                        add(row, "replies", (bucket.get("m") or {}).get(name, 0))
                        add(row, "tool_calls", (bucket.get("tm") or {}).get(name, 0))
                        add(row, "output_tokens", (bucket.get("om") or {}).get(name, 0))
                        add(row, "compactions", (bucket.get("cm") or {}).get(name, 0))
                elif dimension == "agent_type":
                    for name, count in (bucket.get("a") or {}).items():
                        if wanted_agent and wanted_agent not in name.lower():
                            continue
                        row = row_for(name)
                        row["sessions"].add(session_id)
                        add(row, "agent_launches", count)
                elif dimension == "tool":
                    for name, count in (bucket.get("tl") or {}).items():
                        if wanted_tool and wanted_tool not in name.lower():
                            continue
                        row = row_for(name)
                        row["sessions"].add(session_id)
                        add(row, "tool_calls", count)
        out = []
        for row in rows.values():
            row = dict(row)
            row["sessions"] = len(row["sessions"])
            out.append(row)
        time_dimension = dimension in {"day", "week", "month"}
        if order_by is None:
            order_by = "key" if time_dimension else QUERY_DEFAULT_ORDER.get(dimension, "prompts")
            if time_dimension:
                descending = False
        if order_by == "key":
            out.sort(key=lambda r: str(r["key"]), reverse=descending)
        else:
            out.sort(key=lambda r: (
                r.get(order_by) is None,
                -(r.get(order_by) or 0) if descending else (r.get(order_by) or 0),
                str(r["key"]),
            ))
        total_rows = len(out)
        out = out[:limit]
        notes = [
            "Counts come from the session transcripts on this machine; they are activity, not cost.",
            "Filters select whole sessions; since and until then select the days inside them.",
            "A null measure is one this dimension cannot attribute, not a zero.",
        ]
        if per_model:
            notes.append("A compaction is attributed to the model that replied last before it.")
        if dimension == "tool":
            notes.append("Tool rows count calls per tool name per day, capped at 48 names per session-day.")
        return {
            "generated_at": format_timestamp(now or self.clock()),
            "dimension": dimension,
            "order_by": order_by,
            "descending": descending,
            "since": format_timestamp(since) if since else None,
            "until": format_timestamp(until) if until else None,
            "filters": {
                "project": project or None, "model": model or None, "provider": provider or None,
                "agent_type": agent_type or None, "tool": tool or None, "branch": branch or None,
                "entrypoint": entrypoint or None, "q": query or None,
            },
            "sessions_matched": sessions_matched,
            "total_rows": total_rows,
            "truncated": total_rows > len(out),
            "rows": out,
            "notes": notes,
        }

    # -- subagents ----------------------------------------------------------

    def subagent_dir(self, history_id: str) -> Path | None:
        found = self.find(history_id)
        if found is None:
            return None
        key, entry = found
        session_id = (entry.get("stats") or {}).get("session_id")
        if not session_id:
            return None
        directory = Path(key).with_suffix("") / "subagents"
        try:
            if not directory.is_dir() or directory.is_symlink():
                return None
        except OSError:
            return None
        return directory

    def subagents(self, history_id: str) -> list[dict[str, Any]]:
        """Every subagent transcript beside the session, with its meta file."""
        directory = self.subagent_dir(history_id)
        detail = self.detail(history_id) or {}
        known = {agent["id"]: agent for agent in detail.get("agents") or []}
        if directory is None:
            return list(known.values())
        items: list[dict[str, Any]] = []
        try:
            files = sorted(directory.iterdir())[: MAX_AGENTS_PER_SESSION * 2]
        except OSError:
            return list(known.values())
        for path in files:
            if not path.name.startswith("agent-") or path.suffix != ".jsonl":
                continue
            agent_id = path.name[len("agent-") : -len(".jsonl")]
            if not AGENT_ID_PATTERN.match(agent_id):
                continue
            item: dict[str, Any] = {"id": agent_id, **known.get(agent_id, {})}
            meta_path = path.with_name(f"agent-{agent_id}.meta.json")
            try:
                if meta_path.is_file() and not meta_path.is_symlink() and meta_path.stat().st_size <= 64 * 1024:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if isinstance(meta, dict):
                        item.setdefault("agent_type", bounded_str(meta.get("agentType"), 80))
                        item.setdefault("description", bounded_str(meta.get("description"), 160))
                        depth = non_negative_int(meta.get("spawnDepth")) if not isinstance(meta.get("spawnDepth"), str) else None
                        if depth is not None:
                            item["depth"] = depth
            except (OSError, ValueError):
                pass
            scanned = scan_transcript(path, None, own_chain=True) or {}
            agent_stats = scanned.get("stats") or {}
            models = agent_stats.get("models") or {}
            if models and not item.get("model"):
                item["model"] = max(models.items(), key=lambda pair: pair[1])[0]
            item["prompts"] = agent_stats.get("prompts", 0)
            item["replies"] = agent_stats.get("replies", 0)
            item["tool_calls"] = agent_stats.get("tool_calls", 0)
            item["peak_context"] = agent_stats.get("peak_context")
            item["output_tokens"] = agent_stats.get("output_tokens", 0)
            if not item.get("started_at"):
                item["started_at"] = agent_stats.get("first_at")
            item["last_activity_at"] = agent_stats.get("last_at")
            # The main transcript only knows when a background agent was
            # launched; its own file says when it last spoke.
            if agent_stats.get("last_at"):
                item["finished_at"] = agent_stats["last_at"]
            item["path"] = str(path)
            items.append(item)
        listed = {item["id"] for item in items}
        for agent_id, agent in known.items():
            if agent_id not in listed:
                items.append(agent)
        items.sort(key=lambda item: item.get("started_at") or "", reverse=True)
        return items

    def subagent_path(self, history_id: str, agent_id: str) -> Path | None:
        if not AGENT_ID_PATTERN.match(agent_id):
            return None
        directory = self.subagent_dir(history_id)
        if directory is None:
            return None
        path = directory / f"agent-{agent_id}.jsonl"
        try:
            if path.is_file() and not path.is_symlink():
                return path
        except OSError:
            return None
        return None
