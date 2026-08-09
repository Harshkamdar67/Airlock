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

# One Enter for each question in the recommended path: session profile, Grok
# subscription, orchestrator, worker pool, session effort, worker effort, extra
# usage, Fast startup, routing, parallel workers, Advanced, Codex OAuth, proxy
# service, and apply.
ENTER_PRESSES = 14

# The first row of the ASCII wordmark, used to prove that branding appears on a
# normal terminal and is replaced by a plain text mark on a narrow one.
BRAND_ROW = " ###   ###  ####  #      ###   #### #   #"

temp_root = Path(tempfile.mkdtemp(prefix="airlock-setup-wizard-portability-long-path-"))

# Real terminal escape sequences for the arrow keys, sent one keystroke at a
# time exactly as a terminal emulator would.
KEY_UP = b"\x1b[A"
KEY_DOWN = b"\x1b[B"
KEY_ENTER = b"\n"

# How long the child has to be quiet before the next keystroke is delivered.
# Waiting for the wizard to finish drawing keeps every keystroke in the same
# order a person would produce, and keeps the terminal from echoing input that
# has not been asked for yet.
IDLE_GAP = 0.3


def set_window_size(fd: int, rows: int, columns: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))


def disable_terminal_echo(fd: int) -> None:
    """Stop the terminal from echoing, so only what the wizard writes is captured."""
    attributes = termios.tcgetattr(fd)
    attributes[3] = attributes[3] & ~(termios.ECHO | termios.ECHONL)
    termios.tcsetattr(fd, termios.TCSANOW, attributes)


def wait_for_child(child_pid: int, timeout: float) -> int | None:
    """Reap a child without allowing waitpid itself to hang the test job."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
        if waited_pid == child_pid:
            return status
        time.sleep(0.05)
    return None


def terminate_child_group(child_pid: int) -> int:
    """Bound termination of the wizard and anything it started."""
    status = wait_for_child(child_pid, 0.1)
    if status is not None:
        return status
    try:
        os.killpg(child_pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(child_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    status = wait_for_child(child_pid, 2.0)
    if status is not None:
        return status
    try:
        os.killpg(child_pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    status = wait_for_child(child_pid, 2.0)
    if status is None:
        raise AssertionError("guided setup process group could not be reaped")
    return status


def run_wizard(
    config_dir: Path,
    *,
    columns: int = 80,
    rows: int = 40,
    env: dict[str, str | None] | None = None,
    pipe_stdout: bool = False,
    timeout: float = 30.0,
    keystrokes: list[bytes] | None = None,
    echo_input: bool = False,
) -> tuple[int, str]:
    """Run the wizard with a real terminal on stdin and return exit code and output.

    The window size is applied to the terminal before the fork so the child
    always sees the intended width, and stdout can be sent to a pipe instead to
    cover the redirected-stream case.

    With no `keystrokes`, one Enter is sent for each question. Every key waits
    for visible output from the wizard and then for the output to become quiet.
    This matches normal typing and avoids racing the child's controlling-terminal
    setup on macOS.
    """
    run_label = config_dir.name
    print(f"Setup PTY: starting {run_label}", flush=True)
    master_fd, slave_fd = pty.openpty()
    set_window_size(slave_fd, rows, columns)
    if not echo_input:
        disable_terminal_echo(slave_fd)
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
        if keystrokes is None:
            pending = [KEY_ENTER] * ENTER_PRESSES
        else:
            pending = list(keystrokes)
        output_since_key = False
        quiet_since = time.monotonic()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            waited_pid, child_status = os.waitpid(child_pid, os.WNOHANG)
            if waited_pid == child_pid:
                status = child_status
                # Pipe EOF can be delayed on macOS by a short-lived inherited
                # descriptor. The wizard process is authoritative, so drain
                # everything already buffered and stop waiting for EOF.
                while True:
                    ready, _, _ = select.select([read_fd], [], [], 0)
                    if not ready:
                        break
                    try:
                        chunk = os.read(read_fd, 65536)
                    except OSError as error:
                        if error.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    output_parts.append(chunk)
                break
            ready, _, _ = select.select([read_fd], [], [], 0.1)
            if not ready:
                if (
                    pending
                    and output_since_key
                    and time.monotonic() - quiet_since >= IDLE_GAP
                ):
                    os.write(master_fd, pending.pop(0))
                    output_since_key = False
                    quiet_since = time.monotonic()
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
                output_since_key = True
                quiet_since = time.monotonic()
            else:
                status = wait_for_child(child_pid, 1.0)
                if status is not None:
                    break
                time.sleep(0.05)
        else:
            status = terminate_child_group(child_pid)
            partial_output = (
                b"".join(output_parts)
                .decode("utf-8", errors="replace")
                .replace("\r", "")
            )
            raise AssertionError(
                f"guided setup {run_label} did not finish within {timeout:g} "
                f"seconds at {columns} columns with {len(pending)} keystroke(s) "
                f"left; terminated with {os.waitstatus_to_exitcode(status)}:\n"
                f"{partial_output[-4000:]}"
            )
    finally:
        os.close(master_fd)
        if pipe_read >= 0:
            os.close(pipe_read)

    if status is None:
        status = wait_for_child(child_pid, 2.0)
    if status is None:
        status = terminate_child_group(child_pid)
        raise AssertionError(f"guided setup {run_label} closed output but did not exit")
    output = b"".join(output_parts).decode("utf-8", errors="replace").replace("\r", "")
    exit_code = os.waitstatus_to_exitcode(status)
    print(f"Setup PTY: finished {run_label} with exit {exit_code}", flush=True)
    return exit_code, output


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


# Every escape sequence the wizard is allowed to write, by its final byte:
# `m` sets color, `A` moves the cursor up over the option block it just drew,
# and `J` erases from there to the end of the screen. Anything else, such as a
# full screen clear, an absolute cursor move, the alternate screen buffer, or
# the save and restore cursor pair, would be able to destroy the wizard context
# above the block.
ALLOWED_ESCAPE_FINALS = ("m", "A", "J")
CSI_PATTERN = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])")
REPAINT_PATTERN = re.compile(r"\x1b\[[0-9]*[AJ]")


def require_known_escapes(label: str, output: str, rows: int) -> None:
    for match in CSI_PATTERN.finditer(output):
        if match.group(2) not in ALLOWED_ESCAPE_FINALS:
            raise AssertionError(
                f"{label} wrote the unexpected escape sequence {match.group(0)!r}"
            )
        if match.group(2) == "A":
            distance = int(match.group(1) or "1")
            if distance >= rows:
                raise AssertionError(
                    f"{label} moved the cursor up {distance} rows on a {rows} row "
                    "terminal, which would reach past the option block"
                )
    for stray in ("\x1b7", "\x1b8", "\x1bc"):
        if stray in output:
            raise AssertionError(f"{label} wrote the escape sequence {stray!r}")


def visible_after_repaints(text: str) -> str:
    """Turn each in-place repaint into a line break so layout can still be checked."""
    return strip_ansi(REPAINT_PATTERN.sub("\n", text))


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
    "In a normal terminal the Up and Down arrow keys move that marker.",
    "You can always type a number or a name instead.",
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
    "Press Enter to accept 1) GPT-5.6 Sol",
    "Choice [1-3, name, or ?]:",
    "Choice [1-4, name, or ?]:",
    # Claude Fable 5 has to be reachable without hunting through Advanced.
    "Balanced pool plus Claude Fable 5",
    "Everything in the balanced pool and Claude Fable 5 (claude-fable-5[1m]). Fable "
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
    "gpt-5.6-sol",
    "GPT-5.6 Terra",
    "gpt-5.6-terra",
    "GPT-5.6 Luna",
    "gpt-5.6-luna",
    "Architecture, security, and visual direction. Premium usage.",
    "Access depends on the connected plans and is checked again when a session starts.",
    "Claude Code does not expose per-call Agent effort.",
    "workers either follow the session level or keep a setup-time pin.",
    "Fast startup",
    "On where supported for both",
    "This uses paid Anthropic usage credits from the first token.",
    "Answer [Y/n, Enter = yes]:",
    "Answer [y/N, Enter = no]:",
)

GROK_SUBSCRIPTION_AND_APPLY = (
    "Grok subscription",
    "Make Grok models available in hybrid sessions?",
    "Answer [y/N, Enter = no]:",
    "Apply this configuration? [Y/n/s]:",
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
    "Fast startup: OpenAI off; Anthropic off",
    "Install actions",
    "Codex OAuth: yes, only if Codex login is missing",
    "When you accept, Airlock will:",
    "1. Write the configuration file shown above. An existing file that differs "
    "is backed up first.",
    "2. Stop there, because --config-only was requested. Nothing is installed.",
    "Airlock will not:",
    "touch your Claude Code sign-in, native Claude Code, or native Codex",
    "change global settings, global hooks, registered plugins, or MCP configuration",
    "read, print, copy, or expose any login token; the upstream proxy keeps its "
    "OAuth data private",
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
    "AIRLOCK_HYBRID_MODEL=sol\n",
    "AIRLOCK_MAIN_EFFORT=high\n",
    "AIRLOCK_WORKER_EFFORT=inherit\n",
    "AIRLOCK_ANTHROPIC_MODELS=opus,sonnet\n",
    "AIRLOCK_OPENAI_MODELS=sol,terra,luna\n",
    "AIRLOCK_GROK_MODELS=\n",
    "AIRLOCK_OPENAI_FAST=off\n",
    "AIRLOCK_ANTHROPIC_FAST=off\n",
    "AIRLOCK_PROXY_CONFIG_DIR=\n",
    "AIRLOCK_PROXY_STATE_HOME=\n",
)


# The keyboard runs move three separate markers, one of them by wrapping off the
# top of the list and one by wrapping off the bottom, so a stuck or clamped
# marker cannot pass.
KEYBOARD_SELECTION = (
    "The Up and Down arrow keys move the > marker.",
    "Press Enter to accept 4) Extra high",
    "Press Enter to accept 3) Allow extra usage",
    "Press Enter to accept 2) Quality first",
    "Starting effort: xhigh",
    "Extra usage: allow",
    "Routing preference: quality",
)

KEYBOARD_CONFIG = (
    "AIRLOCK_DEFAULT_PROFILE=hybrid\n",
    "AIRLOCK_MAIN_EFFORT=xhigh\n",
    "AIRLOCK_EXTRA_USAGE_POLICY=allow\n",
    "AIRLOCK_ROUTING_POLICY=quality\n",
    "AIRLOCK_GROK_MODELS=\n",
)


def check_config_lines(label: str, config_dir: Path, lines: tuple[str, ...]) -> None:
    config = (config_dir / "config").read_text(encoding="utf-8")
    for line in lines:
        if line not in config:
            raise AssertionError(f"{label} config omitted {line.strip()!r}")


def check_config(label: str, config_dir: Path) -> None:
    check_config_lines(label, config_dir, EXPECTED_CONFIG)


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
    require_phrases("plain run", output, GROK_SUBSCRIPTION_AND_APPLY)
    require_phrases("plain run", output, INSTALLATION_WORDING)
    require_phrases("plain run", output, REVIEW_SCREEN)
    require_lines(
        "plain run",
        output,
        (
            "  Default command:    airlock -> GPT-5.6 Sol (gpt-5.6-sol)",
            "  Session profile:    hybrid: Claude and GPT workers",
            "  Worker effort:      follow session /effort",
            # A long unbreakable path, of the kind macOS hands out for a
            # temporary directory, drops to its own indented line instead of
            # running past the aligned column.
            "  Config path:",
            f"      {plain_dir / 'config'}",
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
    require_phrases("color run", visible, GROK_SUBSCRIPTION_AND_APPLY)
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
    require_phrases(
        "redirected run", piped_output, GROK_SUBSCRIPTION_AND_APPLY
    )
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
        (
            "AIRLOCK",
            "STEP 1 OF 6  [#-----]",
            "SESSION AND ORCHESTRATOR",
            "  Config path:",
            f"      {narrow_dir / 'config'}",
        ),
    )
    require_phrases("narrow run", narrow_output, INSTALLATION_WORDING)
    require_phrases(
        "narrow run", narrow_output, GROK_SUBSCRIPTION_AND_APPLY
    )
    require_phrases("narrow run", narrow_output, REVIEW_SCREEN)
    refuse_phrases("narrow run", narrow_output, HIDDEN_OR_RETIRED)
    check_config("narrow run", narrow_dir)

    # A color capable terminal driven with real arrow keys. Every keystroke is
    # the byte sequence a terminal emulator sends, delivered only once the
    # wizard has finished drawing the question it belongs to.
    keyboard_script = (
        [KEY_ENTER]  # session profile: hybrid
        + [KEY_ENTER]  # Grok subscription: no
        + [KEY_ENTER]  # orchestrator: GPT-5.6 Sol
        + [KEY_ENTER]  # worker pool: balanced
        + [KEY_DOWN, KEY_ENTER]  # session effort: 3) High -> 4) Extra high
        + [KEY_ENTER]  # worker effort: follow session
        + [KEY_UP, KEY_ENTER]  # extra usage: wrap up from 1) to 3)
        + [KEY_ENTER]  # Fast startup: off for both providers
        + [KEY_DOWN] * 4 + [KEY_ENTER]  # routing: wrap down past 3) back to 2)
        + [KEY_ENTER]  # parallel workers: Claude Code default
        + [KEY_ENTER]  # Advanced settings: no
        + [KEY_ENTER]  # Codex OAuth: yes if login is missing
        + [KEY_ENTER]  # proxy service: start automatically
        + [KEY_ENTER]  # apply configuration
    )
    keyboard_dir = temp_root / "keyboard"
    exit_code, keyboard_output = run_wizard(
        keyboard_dir,
        env={"NO_COLOR": None, "TERM": "xterm-256color"},
        keystrokes=keyboard_script,
        echo_input=False,
        timeout=120.0,
    )
    require_exit("keyboard run", exit_code, keyboard_output)
    # Only color, cursor up, and erase below, and never further up than the
    # block that was just drawn.
    require_known_escapes("keyboard run", keyboard_output, 40)
    if "\x1b[J" not in keyboard_output:
        raise AssertionError(
            f"keyboard run never repainted an option block:\n{keyboard_output!r}"
        )
    if re.search(r"\x1b\[[0-9]+A", keyboard_output) is None:
        raise AssertionError(
            f"keyboard run never moved the cursor back over a block:\n{keyboard_output!r}"
        )
    keyboard_visible = visible_after_repaints(keyboard_output)
    require_width("keyboard run", keyboard_visible, 80)
    # The wizard context above the repainted block has to survive.
    if BRAND_ROW not in keyboard_visible:
        raise AssertionError(f"keyboard run lost the wordmark:\n{keyboard_visible}")
    require_phrases("keyboard run", keyboard_visible, BRANDING_AND_INTRODUCTION)
    require_phrases("keyboard run", keyboard_visible, SECTION_HIERARCHY)
    require_phrases("keyboard run", keyboard_visible, KEYBOARD_SELECTION)
    require_phrases(
        "keyboard run", keyboard_visible, GROK_SUBSCRIPTION_AND_APPLY
    )
    require_phrases("keyboard run", keyboard_visible, REVIEW_SCREEN)
    check_config_lines("keyboard run", keyboard_dir, KEYBOARD_CONFIG)

    # The same keystrokes with NO_COLOR set. The arrow keys still move the
    # marker, the block is reprinted below instead of repainted in place, and
    # not one escape sequence reaches the terminal. Typing ? and a number has to
    # keep working in the same mode.
    plain_keyboard_script = (
        [KEY_ENTER]  # session profile: hybrid
        + [KEY_ENTER]  # Grok subscription: no
        + [b"?", b"\n", b"5", b"\n"]  # orchestrator: show the list, then pick 5
        + [KEY_ENTER]  # worker pool: balanced
        + [KEY_DOWN, KEY_ENTER]  # session effort: 3) High -> 4) Extra high
        + [KEY_ENTER]  # worker effort: follow session
        + [KEY_UP, KEY_ENTER]  # extra usage: wrap up from 1) to 3)
        + [KEY_ENTER]  # Fast startup: off for both providers
        + [KEY_DOWN] * 4 + [KEY_ENTER]  # routing: wrap down past 3) back to 2)
        + [KEY_ENTER]  # parallel workers: Claude Code default
        + [KEY_ENTER]  # Advanced settings: no
        + [KEY_ENTER]  # Codex OAuth: yes if login is missing
        + [KEY_ENTER]  # proxy service: start automatically
        + [KEY_ENTER]  # apply configuration
    )
    plain_keyboard_dir = temp_root / "plain-keyboard"
    exit_code, plain_keyboard_output = run_wizard(
        plain_keyboard_dir,
        env={"TERM": "xterm-256color"},
        keystrokes=plain_keyboard_script,
        echo_input=False,
        timeout=120.0,
    )
    require_exit("plain keyboard run", exit_code, plain_keyboard_output)
    require_no_ansi("plain keyboard run", plain_keyboard_output)
    require_width("plain keyboard run", plain_keyboard_output, 80)
    require_phrases(
        "plain keyboard run", plain_keyboard_output, GROK_SUBSCRIPTION_AND_APPLY
    )
    require_phrases("plain keyboard run", plain_keyboard_output, KEYBOARD_SELECTION)
    require_lines(
        "plain keyboard run",
        plain_keyboard_output,
        (
            # The marker itself moved, including both wraparound cases.
            "  >  4) Extra high",
            "  >  3) Allow extra usage",
            "  >  2) Quality first",
            # The review screen agrees with what the keys selected.
            "  Starting effort:    xhigh",
            "  Extra usage:        allow",
            "  Routing preference: quality",
            # Typing an exact number still selects, even in keystroke mode.
            "  Default command:    airlock -> Claude Opus 5 (claude-opus-5[1m])",
        ),
    )
    check_config_lines(
        "plain keyboard run",
        plain_keyboard_dir,
        KEYBOARD_CONFIG + ("AIRLOCK_HYBRID_MODEL=opus\n",),
    )
finally:
    shutil.rmtree(temp_root, ignore_errors=True)

print("All setup wizard PTY tests passed.")
