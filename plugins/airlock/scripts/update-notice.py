#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Render a cached Airlock update as a user-only SessionStart message."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import sys
import time

NOTICE_LIMIT = 4096
MANIFEST_LIMIT = 64 * 1024
NOTICE_TTL_SECONDS = 7 * 24 * 60 * 60
FUTURE_TOLERANCE_SECONDS = 5 * 60
NOTICE_KEYS = {
    "schema_version",
    "kind",
    "checked_at",
    "current_version",
    "available_version",
    "release_url",
}
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def read_regular_file(path: Path, limit: int) -> bytes | None:
    try:
        initial = path.lstat()
        if not stat.S_ISREG(initial.st_mode) or initial.st_size > limit:
            return None
        flags = os.O_RDONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > limit:
                return None
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                raw = handle.read(limit + 1)
            return raw if len(raw) <= limit else None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except (OSError, ValueError):
        return None


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def read_json_object(path: Path, limit: int) -> dict[str, object] | None:
    raw = read_regular_file(path, limit)
    if raw is None:
        return None
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def parse_semver(value: str) -> tuple[tuple[int, int, int], tuple[str, ...] | None] | None:
    match = SEMVER_PATTERN.fullmatch(value)
    if match is None:
        return None
    prerelease_text = match.group(4)
    prerelease = None if prerelease_text is None else tuple(prerelease_text.split("."))
    if prerelease is not None and any(
        identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")
        for identifier in prerelease
    ):
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))), prerelease


def compare_identifiers(left: str, right: str) -> int:
    if left == right:
        return 0
    left_numeric = left.isdigit()
    right_numeric = right.isdigit()
    if left_numeric and right_numeric:
        return 1 if int(left) > int(right) else -1
    if left_numeric != right_numeric:
        return -1 if left_numeric else 1
    return 1 if left > right else -1


def is_newer(available: str, current: str) -> bool:
    available_parsed = parse_semver(available)
    current_parsed = parse_semver(current)
    if available_parsed is None or current_parsed is None:
        return False
    available_core, available_prerelease = available_parsed
    current_core, current_prerelease = current_parsed
    if available_core != current_core:
        return available_core > current_core
    if available_prerelease is None:
        return current_prerelease is not None
    if current_prerelease is None:
        return False
    for available_item, current_item in zip(available_prerelease, current_prerelease):
        comparison = compare_identifiers(available_item, current_item)
        if comparison:
            return comparison > 0
    return len(available_prerelease) > len(current_prerelease)


def installed_version() -> str | None:
    manifest = Path(__file__).parent.parent / ".claude-plugin" / "plugin.json"
    value = read_json_object(manifest, MANIFEST_LIMIT)
    if value is None or value.get("name") != "airlock":
        return None
    version = value.get("version")
    if type(version) is not str or parse_semver(version) is None:
        return None
    return version


def notice_message(now: float | None = None) -> str | None:
    notice_text = os.environ.get("AIRLOCK_UPDATE_NOTICE_FILE")
    if not notice_text or "\x00" in notice_text:
        return None
    notice = read_json_object(Path(notice_text), NOTICE_LIMIT)
    if notice is None or set(notice) != NOTICE_KEYS:
        return None
    if notice.get("schema_version") != 1 or notice.get("kind") != "airlock-update-notice":
        return None
    checked_at = notice.get("checked_at")
    current = notice.get("current_version")
    available = notice.get("available_version")
    release_url = notice.get("release_url")
    if type(checked_at) is not int or any(type(value) is not str for value in (current, available, release_url)):
        return None
    if len(current) > 64 or len(available) > 64:
        return None
    installed = installed_version()
    current_parsed = parse_semver(current)
    available_parsed = parse_semver(available)
    if (
        installed is None
        or current != installed
        or current_parsed is None
        or available_parsed is None
        or not is_newer(available, current)
        or (current_parsed[1] is None and available_parsed[1] is not None)
    ):
        return None
    expected_url = (
        "https://github.com/Harshkamdar67/Airlock/releases/tag/"
        f"v{available}"
    )
    if release_url != expected_url:
        return None
    current_time = time.time() if now is None else now
    if checked_at < 0 or checked_at > current_time + FUTURE_TOLERANCE_SECONDS:
        return None
    if current_time - checked_at > NOTICE_TTL_SECONDS:
        return None
    return (
        f"Airlock {available} is available (installed: {current}). "
        f"Exit this session, then run airlock update. Release: {release_url}"
    )


def main() -> int:
    try:
        message = notice_message()
        if message is not None:
            sys.stdout.write(json.dumps({"systemMessage": message}, separators=(",", ":")) + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
