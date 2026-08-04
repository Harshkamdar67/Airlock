#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Block raw access to sensitive env and credential files."""

from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
import re
import sys

from file_safety import (
    FileSafetyError,
    contains_high_confidence_credential,
    env_projection,
    is_sensitive_credential_path,
    is_sensitive_env_path,
)

MAX_EVENT_BYTES = 1024 * 1024
MAX_SCAN_BYTES = 1024 * 1024
MAX_SCAN_ENTRIES = 50_000
ENV_REFERENCE = re.compile(
    r"(?i)(?:^|[\\/\s'\"])(\.env(?:\.[A-Za-z0-9_.-]+)?)(?=$|[^A-Za-z0-9_.-])"
)
CREDENTIAL_REFERENCE = re.compile(
    r"(?i)(?:^|[\\/:\s'\"])(?:\.netrc|\.npmrc|\.pypirc|\.credentials\.json|"
    r"credentials\.json|secrets\.json|service-account\.json|id_(?:rsa|dsa|ecdsa|ed25519)|"
    r"[^\\/\s'\"]+\.(?:key|p12|pfx))(?=$|[^A-Za-z0-9_.-])"
)


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, separators=(",", ":"), ensure_ascii=True))


def resolve_path(raw: object, cwd: Path) -> Path | None:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    try:
        return Path(os.path.abspath(candidate))
    except OSError:
        return None


def safe_file_payload(path: Path) -> bytes | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SCAN_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None


def sensitive_kind(path: Path) -> str | None:
    normalized = str(path).replace("\\", "/")
    if path.is_symlink() and (
        is_sensitive_env_path(normalized)
        or is_sensitive_credential_path(normalized)
    ):
        return "credential"
    if is_sensitive_env_path(normalized):
        return "env"
    if is_sensitive_credential_path(normalized):
        return "credential"
    payload = safe_file_payload(path)
    if payload is not None and contains_high_confidence_credential(normalized, payload):
        return "credential"
    return None


def directory_sensitive_files(root: Path) -> tuple[list[tuple[Path, str]], bool]:
    candidates: list[tuple[Path, str]] = []
    seen = 0
    try:
        for directory, names, files in os.walk(root, followlinks=False):
            names[:] = [
                name for name in names
                if name not in {".git", "node_modules", "__pycache__"}
            ]
            for name in files:
                seen += 1
                if seen > MAX_SCAN_ENTRIES:
                    return candidates, True
                path = Path(directory) / name
                kind = sensitive_kind(path)
                if kind:
                    candidates.append((path, kind))
    except OSError:
        return candidates, True
    return candidates, False


def glob_matches(path: Path, root: Path, pattern: str) -> bool:
    normalized_pattern = pattern.replace("\\", "/")
    try:
        relative = str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        relative = path.name
    patterns = [normalized_pattern]
    if normalized_pattern.startswith("**/"):
        patterns.append(normalized_pattern[3:])
    return any(
        fnmatch.fnmatch(relative, candidate)
        or fnmatch.fnmatch(path.name, candidate)
        or Path(relative).match(candidate)
        for candidate in patterns
    )


def search_can_reach_sensitive(
    tool_name: str,
    tool_input: dict[str, object],
    cwd: Path,
) -> bool:
    raw_path = tool_input.get("path")
    root = resolve_path(raw_path, cwd) if raw_path is not None else cwd
    if not root or not root.is_dir():
        return False
    candidates, incomplete = directory_sensitive_files(root)
    pattern = tool_input.get("glob") if tool_name == "Grep" else tool_input.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return bool(candidates) or incomplete
    return any(glob_matches(path, root, pattern) for path, _ in candidates) or incomplete


def env_projection_reason(path: Path) -> str:
    payload = safe_file_payload(path)
    if payload is None:
        return (
            "Airlock blocked raw sensitive-file access because the env path "
            "is missing, oversized, non-regular, or linked. No values were exposed."
        )
    try:
        projection = env_projection(str(path), payload).decode("utf-8").strip()
    except (FileSafetyError, UnicodeError):
        return (
            "Airlock blocked raw sensitive-file access because the env file "
            "could not be projected safely. No values were exposed."
        )
    return (
        "Airlock replaced raw sensitive-file access with this key-only "
        f"projection: {projection}"
    )


def explicit_sensitive_path(
    tool_name: str,
    tool_input: dict[str, object],
    cwd: Path,
) -> tuple[Path, str] | None:
    if tool_name == "Read":
        path = resolve_path(tool_input.get("file_path"), cwd)
        if path:
            kind = sensitive_kind(path)
            return (path, kind) if kind else None
    if tool_name == "Grep":
        path = resolve_path(tool_input.get("path"), cwd)
        if path and not path.is_dir():
            kind = sensitive_kind(path)
            return (path, kind) if kind else None
    return None


def bash_sensitive_kind(command: object) -> str | None:
    if not isinstance(command, str):
        return None
    normalized = command.replace("\\", "/")
    if ENV_REFERENCE.search(command) or any(
        name and name in normalized
        for name in os.environ.get("AIRLOCK_SENSITIVE_ENV_FILES", "").split(",")
    ):
        return "env"
    if CREDENTIAL_REFERENCE.search(command) or any(
        name and name in normalized
        for name in os.environ.get("AIRLOCK_SENSITIVE_FILES", "").split(",")
    ):
        return "credential"
    return None


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_EVENT_BYTES + 1)
    if len(raw) > MAX_EVENT_BYTES:
        deny("Airlock blocked an oversized sensitive-file tool event.")
        return 0
    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        deny("Airlock blocked malformed sensitive-file tool input.")
        return 0
    if not isinstance(event, dict):
        deny("Airlock blocked malformed sensitive-file tool input.")
        return 0
    tool_name = event.get("tool_name")
    tool_input = event.get("tool_input")
    if tool_name not in {"Read", "Grep", "Glob", "Bash"} or not isinstance(tool_input, dict):
        return 0
    cwd = resolve_path(event.get("cwd"), Path.cwd()) or Path.cwd()
    explicit = explicit_sensitive_path(tool_name, tool_input, cwd)
    if explicit:
        path, kind = explicit
        if kind == "env":
            deny(env_projection_reason(path))
        else:
            deny(
                "Airlock blocked access to a credential-bearing file. "
                "No path or value was exposed."
            )
        return 0
    if tool_name in {"Grep", "Glob"} and search_can_reach_sensitive(
        tool_name, tool_input, cwd
    ):
        deny(
            "Airlock blocked a search that could return raw sensitive env or "
            "credential data. Scope the search to known safe files. No values were exposed."
        )
        return 0
    if tool_name == "Bash":
        kind = bash_sensitive_kind(tool_input.get("command"))
        if kind == "env":
            deny(
                "Airlock blocked shell access to a sensitive env file. Use Read "
                "on the exact file to receive its key-only projection. No values were exposed."
            )
        elif kind == "credential":
            deny(
                "Airlock blocked shell access to a credential-bearing file. "
                "No path or value was exposed."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
