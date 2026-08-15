#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Finalize an armed, session-local Airlock Fast handoff on clean exit."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys

MAX_EVENT_BYTES = 4096
HELPER_NAME = "airlock-access.py"
FINALIZE_COMMAND = "fast-transition-finalize"
CHANNEL_ENV = "AIRLOCK_FAST_TRANSITION_CHANNEL"
NONCE_ENV = "AIRLOCK_FAST_TRANSITION_NONCE"
HELPER_ENV = "AIRLOCK_ACCESS_HELPER"
PYTHON_ENV = "AIRLOCK_PYTHON"


def _read_event() -> dict[str, object] | None:
    try:
        raw = sys.stdin.buffer.read(MAX_EVENT_BYTES + 1)
        if len(raw) > MAX_EVENT_BYTES:
            return None
        value = json.loads(raw.decode("utf-8"))
    except (AttributeError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _safe_helper() -> Path | None:
    raw = os.environ.get(HELPER_ENV)
    if not raw or "\x00" in raw:
        return None
    path = Path(raw).expanduser()
    try:
        details = path.lstat()
        if (
            path.name != HELPER_NAME
            or path.is_symlink()
            or not stat.S_ISREG(details.st_mode)
            or details.st_size <= 0
        ):
            return None
    except OSError:
        return None
    return path


def _python_candidates() -> list[str]:
    configured = os.environ.get(PYTHON_ENV)
    return [configured] if configured else ["python3", "python"]


def _usable(candidate: str) -> bool:
    try:
        completed = subprocess.run(
            [candidate, "-c", "import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def main() -> int:
    event = _read_event()
    if event is None or event.get("reason") != "prompt_input_exit":
        return 0
    session_id = event.get("session_id")
    cwd = event.get("cwd")
    if not isinstance(session_id, str) or not isinstance(cwd, str):
        return 0
    channel = os.environ.get(CHANNEL_ENV)
    nonce = os.environ.get(NONCE_ENV)
    if not channel or not nonce or "\x00" in channel or "\x00" in nonce:
        return 0
    helper = _safe_helper()
    if helper is None:
        return 0
    payload = json.dumps(
        {"session_id": session_id, "cwd": cwd, "reason": "prompt_input_exit"},
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    for candidate in _python_candidates():
        if not candidate or not _usable(candidate):
            continue
        try:
            subprocess.run(
                [candidate, str(helper), FINALIZE_COMMAND],
                input=payload,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                env=os.environ.copy(),
            )
        except (OSError, subprocess.SubprocessError):
            pass
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
