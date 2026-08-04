#!/usr/bin/env python3
"""Exercise the guided setup flow through a real POSIX terminal."""

from __future__ import annotations

import errno
import os
from pathlib import Path
import re
import select
import shutil
import signal
import tempfile
import time


if os.name != "posix":
    print("Setup PTY test skipped: POSIX PTY support is unavailable.")
    raise SystemExit(0)

try:
    import fcntl
    import pty
    import struct
    import termios
except ImportError:
    print("Setup PTY test skipped: Python PTY module is unavailable.")
    raise SystemExit(0)


REPO_ROOT = Path(__file__).resolve().parent.parent
SETUP_SCRIPT = REPO_ROOT / "scripts" / "setup.sh"

# One Enter for each question in the recommended path: session profile,
# orchestrator, worker pool, session effort, worker effort, extra usage,
# routing, parallel workers, Advanced, Codex OAuth, proxy service, and apply.
ENTER_PRESSES = 12

# The first row of the ASCII wordmark, used to prove that branding appears on a
# normal terminal and is replaced by a plain text mark on a narrow one.
BRAND_ROW = " ###   ###  ####  #      ###   #### #   #"

temp_root = Path(tempfile.mkdtemp(prefix="al."))


def set_window_size(fd: int, rows: int, columns: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))


def run_wizard(
    config_dir: Path,
    *,
    columns: int = 80,
    rows: int = 40,
    env: dict[str, str | None] | None = None,
    pipe_stdout: bool = False,
    timeout: float = 30.0,
) -> tuple[int, str]:
    """Run the wizard with a real terminal on stdin and return exit code and output.

    The window size is applied to the terminal before the fork so the child
    always sees the intended width, and stdout can be sent to a pipe instead to
    cover the redirected-stream case.
    """
    master_fd, slave_fd = pty.openpty()
    set_window_size(slave_fd, rows, columns)
    pipe_read = -1
    pipe_write = -1
    if pipe_stdout:
        pipe_read, pipe_write = os.pipe()

    environment = os.environ.copy()
    # Remove the inherited hints so the wizard has to measure the terminal.
    environment.pop("COLUMNS", None)
    environment.pop("LINES", None)
    environment.update(
        {
            "AIRLOCK_CONFIG_DIR": str(config_dir),
            "NO_COLOR": "1",
            "TERM": "dumb",
        }
    )
    for key, value in (env or {}).items():
        if value is None:
            environment.pop(key, None)
        else:
            environment[key] = value

    child_pid = os.fork()
    if child_pid == 0:
        try:
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            os.dup2(slave_fd, 0)
            os.dup2(pipe_write if pipe_stdout else slave_fd, 1)
            os.dup2(pipe_write if pipe_stdout else slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            os.close(master_fd)
            if pipe_stdout:
                os.close(pipe_read)
                if pipe_write > 2:
                    os.close(pipe_write)
            os.execvpe(
                "bash",
                ["bash", str(SETUP_SCRIPT), "--without-agent", "--config-only"],
                environment,
            )
        except BaseException:  # pragma: no cover - the child cannot report back
            os._exit(127)

    os.close(slave_fd)
    if pipe_stdout:
        os.close(pipe_write)
    read_fd = pipe_read if pipe_stdout else master_fd
    output_parts: list[bytes] = []
    status: int | None = None
    try:
        os.write(master_fd, b"\n" * ENTER_PRESSES)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([read_fd], [], [], 0.2)
            if not ready:
                continue
            try:
                chunk = os.read(read_fd, 65536)
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
            raise AssertionError(
                f"guided setup did not finish within {timeout:g} seconds at "
                f"{columns} columns"
            )
    finally:
        os.close(master_fd)
        if pipe_read >= 0:
            os.close(pipe_read)

    if status is None:
        _, status = os.waitpid(child_pid, 0)
    output = b"".join(output_parts).decode("utf-8", errors="replace").replace("\r", "")
    return os.waitstatus_to_exitcode(status), output


ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return ANSI_PATTERN.sub("", text)


def normalize(text: str) -> str:
    """Collapse runs of whitespace so wrapped prose can be matched as one phrase."""
    return " ".join(text.split())


def require_phrases(label: str, output: str, phrases: tuple[str, ...]) -> None:
    normalized = normalize(output)
    for phrase in phrases:
        if normalize(phrase) not in normalized:
            raise AssertionError(f"{label} omitted {phrase!r}:\n{output}")


def refuse_phrases(label: str, output: str, phrases: tuple[str, ...]) -> None:
    normalized = normalize(output)
    for phrase in phrases:
        if normalize(phrase) in normalized:
            raise AssertionError(f"{label} exposed {phrase!r}:\n{output}")


def require_lines(label: str, output: str, lines: tuple[str, ...]) -> None:
    actual = output.split("\n")
    for line in lines:
        if line not in actual:
            raise AssertionError(f"{label} omitted the exact line {line!r}:\n{output}")


def require_no_ansi(label: str, output: str) -> None:
    if "\x1b" in output:
        raise AssertionError(f"{label} emitted a raw escape sequence:\n{output!r}")


def require_width(label: str, output: str, width: int) -> None:
    """Every wrapped line must fit the terminal.

    Wrapping is word based, so a single unbreakable token such as a long
    temporary path is allowed to run past the edge.
    """
    for line in output.split("\n"):
        if len(line) > width and " " in line.strip():
            raise AssertionError(
                f"{label} wrote a {len(line)} column line in a {width} column "
                f"terminal:\n{line!r}"
            )


def require_exit(label: str, exit_code: int, output: str) -> None:
    if exit_code != 0:
        raise AssertionError(f"{label} exited with {exit_code}:\n{output}")


BRANDING_AND_INTRODUCTION = (
    "Airlock setup wizard",
    "Airlock runs OpenAI and Anthropic models in one Claude Code session, with "
    "credentials that never cross.",
    "This wizard asks six short sets of questions and ends with a review screen. "
    "Nothing on your machine changes until you accept that screen.",
    "It does not change native Claude Code, native Codex, global settings, global "
    "hooks, registered plugins, or MCP configuration, and it never reads, copies, "
    "or stores a login token.",
    "At every question, press Enter to accept the choice marked with >.",
    "Type ? to see the choices again.",
)

SECTION_HIERARCHY = (
    "STEP 1 OF 6 SESSION AND ORCHESTRATOR",
    "[#-----]",
    "STEP 2 OF 6 WORKER POOL",
    "[##----]",
    "STEP 3 OF 6 EFFORT",
    "[###---]",
    "STEP 4 OF 6 SAFETY AND BUDGET",
    "[####--]",
    "STEP 5 OF 6 INSTALLATION",
    "[#####-]",
    "STEP 6 OF 6 REVIEW",
    "[######]",
)

CHOICE_PRESENTATION = (
    "[recommended]",
    "Press Enter to accept 1) Hybrid: Claude and GPT together",
    "Choice [1-2, name, or ?]:",
    "Choice [1-4, name, or ?]:",
    # Claude Fable 5 has to be reachable without hunting through Advanced.
    "Balanced pool plus Claude Fable 5",
    "Everything in the balanced pool and Claude Fable 5 (claude-fable-5). Fable "
    "can use extra usage, so it is not on by default.",
    "Pick any mix, including Claude Fable 5 and Claude Haiku 4.5.",
    "Efficient frontier work. May require extra usage.",
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
    "Architecture, security, and visual direction. Premium usage.",
    "Access depends on the connected plans and is checked again when a session starts.",
    "Claude Code does not expose per-call Agent effort.",
    "workers either follow the session level or keep a setup-time pin.",
    "Answer [Y/n, Enter = yes]:",
    "Answer [y/N, Enter = no]:",
)

# The installation section has to separate the Claude Code prerequisite from the
# Codex OAuth that belongs to the local proxy.
INSTALLATION_WORDING = (
    "Two separate sign-ins are involved, and Airlock changes neither one",
    "Claude Code is a prerequisite. Install it and sign in with the normal "
    "`claude` command yourself. Airlock never changes or reads that sign-in.",
    "Codex OAuth belongs to the local `claude-code-proxy`. It is what gives "
    "Airlock access to the OpenAI models, through `claude-code-proxy codex auth "
    "login`.",
    "Start Codex OAuth for the local proxy, only if the proxy reports that Codex "
    "login is missing?",
)

REVIEW_SCREEN = (
    "Nothing on your machine has changed yet. Check these settings, then accept "
    "them or start over.",
    "Session",
    "Workers",
    "Safety and budget",
    "Install actions",
    "Codex OAuth: yes, only if Codex login is missing",
    "When you accept, Airlock will:",
    "1. Write the configuration file shown above. An existing file that differs "
    "is backed up first.",
    "2. Stop there, because --config-only was requested. Nothing is installed.",
    "Airlock will not:",
    "touch your Claude Code sign-in, native Claude Code, or native Codex",
    "change global settings, global hooks, registered plugins, or MCP configuration",
    "read, copy, or store any login token",
    "Press Enter to apply. Type n to quit without changes, or s to start over.",
    "Apply this configuration? [Y/n/s]:",
)

# Advanced stays closed unless it is asked for, and the old wording that mixed up
# Claude Code sign-in with Codex OAuth must not come back.
HIDDEN_OR_RETIRED = (
    "ADVANCED SETTINGS",
    "`airlock bg` is a separate convenience command",
    "The utility model handles lightweight Claude Code requests",
    "Luna swarm Fast processing",
    "browser OAuth",
    "Browser login:",
)

EXPECTED_CONFIG = (
    "AIRLOCK_DEFAULT_PROFILE=hybrid\n",
    "AIRLOCK_HYBRID_MODEL=sonnet\n",
    "AIRLOCK_MAIN_EFFORT=high\n",
    "AIRLOCK_WORKER_EFFORT=inherit\n",
    "AIRLOCK_ANTHROPIC_MODELS=opus,sonnet\n",
    "AIRLOCK_OPENAI_MODELS=sol,terra,luna\n",
)


def check_config(label: str, config_dir: Path) -> None:
    config = (config_dir / "config").read_text(encoding="utf-8")
    for line in EXPECTED_CONFIG:
        if line not in config:
            raise AssertionError(f"{label} config omitted {line.strip()!r}")


try:
    # A normal 80 column terminal with color turned off.
    plain_dir = temp_root / "plain"
    exit_code, output = run_wizard(plain_dir)
    require_exit("plain run", exit_code, output)
    require_no_ansi("plain run", output)
    require_width("plain run", output, 80)
    if BRAND_ROW not in output:
        raise AssertionError(f"plain run omitted the ASCII wordmark:\n{output}")
    require_phrases("plain run", output, BRANDING_AND_INTRODUCTION)
    require_phrases("plain run", output, SECTION_HIERARCHY)
    require_phrases("plain run", output, CHOICE_PRESENTATION)
    require_phrases("plain run", output, INSTALLATION_WORDING)
    require_phrases("plain run", output, REVIEW_SCREEN)
    require_lines(
        "plain run",
        output,
        (
            "  Default command:    airlock -> Claude Sonnet 5 (claude-sonnet-5)",
            "  Session profile:    hybrid: Claude and GPT workers",
            "  Worker effort:      follow session /effort",
        ),
    )
    refuse_phrases("plain run", output, HIDDEN_OR_RETIRED)
    check_config("plain run", plain_dir)

    # The same run on a terminal that supports color.
    color_dir = temp_root / "color"
    exit_code, color_output = run_wizard(
        color_dir, env={"NO_COLOR": None, "TERM": "xterm-256color"}
    )
    require_exit("color run", exit_code, color_output)
    if "\x1b[" not in color_output:
        raise AssertionError(f"color run produced no styling:\n{color_output}")
    # Color is decoration only, so the visible text and the layout must match
    # the plain run once the escape sequences are removed.
    visible = strip_ansi(color_output)
    require_width("color run", visible, 80)
    require_phrases("color run", visible, SECTION_HIERARCHY)
    require_phrases("color run", visible, CHOICE_PRESENTATION)
    require_phrases("color run", visible, INSTALLATION_WORDING)
    require_phrases("color run", visible, REVIEW_SCREEN)
    check_config("color run", color_dir)

    # A color capable terminal with the output redirected to a pipe.
    piped_dir = temp_root / "piped"
    exit_code, piped_output = run_wizard(
        piped_dir,
        env={"NO_COLOR": None, "TERM": "xterm-256color"},
        pipe_stdout=True,
    )
    require_exit("redirected run", exit_code, piped_output)
    require_no_ansi("redirected run", piped_output)
    require_phrases("redirected run", piped_output, SECTION_HIERARCHY)
    require_phrases("redirected run", piped_output, REVIEW_SCREEN)
    check_config("redirected run", piped_dir)

    # A narrow terminal falls back to a text mark and a stacked layout.
    narrow_dir = temp_root / "narrow"
    exit_code, narrow_output = run_wizard(narrow_dir, columns=40)
    require_exit("narrow run", exit_code, narrow_output)
    require_no_ansi("narrow run", narrow_output)
    require_width("narrow run", narrow_output, 40)
    if BRAND_ROW in narrow_output:
        raise AssertionError("narrow run drew the wide ASCII wordmark")
    require_lines(
        "narrow run",
        narrow_output,
        ("AIRLOCK", "STEP 1 OF 6  [#-----]", "SESSION AND ORCHESTRATOR"),
    )
    require_phrases("narrow run", narrow_output, INSTALLATION_WORDING)
    require_phrases("narrow run", narrow_output, REVIEW_SCREEN)
    refuse_phrases("narrow run", narrow_output, HIDDEN_OR_RETIRED)
    check_config("narrow run", narrow_dir)
finally:
    shutil.rmtree(temp_root, ignore_errors=True)

print("All setup wizard PTY tests passed.")
