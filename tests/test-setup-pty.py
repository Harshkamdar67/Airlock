#!/usr/bin/env python3
"""Exercise the guided setup flow through a real POSIX terminal."""

from __future__ import annotations

import errno
import os
from pathlib import Path
import select
import shutil
import signal
import sys
import tempfile
import time


if os.name != "posix":
    print("Setup PTY test skipped: POSIX PTY support is unavailable.")
    raise SystemExit(0)

try:
    import pty
except ImportError:
    print("Setup PTY test skipped: Python PTY module is unavailable.")
    raise SystemExit(0)


repo_root = Path(__file__).resolve().parent.parent
setup_script = repo_root / "scripts" / "setup.sh"
temp_root = Path(tempfile.mkdtemp(prefix="airlock-setup-pty-test."))
config_dir = temp_root / "config"
master_fd = -1
child_pid = -1

try:
    child_pid, master_fd = pty.fork()
    if child_pid == 0:
        environment = os.environ.copy()
        environment.update(
            {
                "AIRLOCK_CONFIG_DIR": str(config_dir),
                "NO_COLOR": "1",
                "TERM": "dumb",
            }
        )
        os.execvpe(
            "bash",
            [
                "bash",
                str(setup_script),
                "--without-agent",
                "--config-only",
            ],
            environment,
        )

    # Accept every recommended choice: hybrid Sonnet, balanced workers, high
    # effort with worker inheritance, conservative policies, no Advanced
    # settings, default installation actions, and the final confirmation.
    os.write(master_fd, b"\n" * 12)
    output_parts: list[bytes] = []
    deadline = time.monotonic() + 30
    status: int | None = None
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master_fd], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(master_fd, 65536)
            except OSError as error:
                if error.errno == errno.EIO:
                    chunk = b""
                else:
                    raise
            if chunk:
                output_parts.append(chunk)
            else:
                _, status = os.waitpid(child_pid, 0)
                break
    else:
        os.kill(child_pid, signal.SIGTERM)
        os.waitpid(child_pid, 0)
        raise AssertionError("guided setup did not finish within 30 seconds")

    if status is None:
        _, status = os.waitpid(child_pid, 0)
    exit_code = os.waitstatus_to_exitcode(status)
    output = b"".join(output_parts).decode("utf-8", errors="replace").replace("\r", "")
    if exit_code != 0:
        raise AssertionError(f"guided setup exited with {exit_code}:\n{output}")

    required_text = (
        "AIRLOCK SETUP",
        "[1/6] SESSION AND ORCHESTRATOR",
        "[2/6] WORKER POOL",
        "[3/6] EFFORT",
        "[4/6] SAFETY AND BUDGET",
        "[5/6] INSTALLATION",
        "[6/6] REVIEW",
        "Claude Sonnet 5",
        "claude-sonnet-5",
        "Claude Opus 5",
        "claude-opus-5",
        "Claude Fable 5",
        "claude-fable-5",
        "Claude Haiku 4.5",
        "claude-haiku-4-5-20251001",
        "GPT-5.6 Sol",
        "gpt-5.6-sol[1m]",
        "GPT-5.6 Terra",
        "gpt-5.6-terra[1m]",
        "GPT-5.6 Luna",
        "gpt-5.6-luna[1m]",
        "architecture, security, and visual direction - premium usage",
        "Access depends on the connected plans and is checked again when a session starts.",
        "Claude Code does not expose per-call Agent effort.",
        "workers either follow the session level or keep a setup-time pin.",
        "Default command:    airlock -> Claude Sonnet 5 (claude-sonnet-5)",
        "Worker effort:      follow session /effort",
    )
    for text in required_text:
        if text not in output:
            raise AssertionError(f"guided setup output omitted {text!r}:\n{output}")

    forbidden_text = (
        "\x1b[",
        "ADVANCED SETTINGS",
        "`airlock bg` is a separate convenience command",
        "The utility model handles lightweight Claude Code requests",
    )
    for text in forbidden_text:
        if text in output:
            raise AssertionError(f"guided setup exposed hidden or styled text {text!r}")

    config = (config_dir / "config").read_text(encoding="utf-8")
    for line in (
        "AIRLOCK_DEFAULT_PROFILE=hybrid\n",
        "AIRLOCK_HYBRID_MODEL=sonnet\n",
        "AIRLOCK_MAIN_EFFORT=high\n",
        "AIRLOCK_WORKER_EFFORT=inherit\n",
        "AIRLOCK_ANTHROPIC_MODELS=opus,sonnet\n",
        "AIRLOCK_OPENAI_MODELS=sol,terra,luna\n",
    ):
        if line not in config:
            raise AssertionError(f"guided setup config omitted {line.strip()!r}")
finally:
    if master_fd >= 0:
        os.close(master_fd)
    shutil.rmtree(temp_root, ignore_errors=True)

print("All setup wizard PTY tests passed.")
