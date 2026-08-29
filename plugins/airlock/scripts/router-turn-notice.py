#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Tell the user, at the end of a turn, when another model answered.

A handoff is invisible from inside Claude Code: it sends one request and
receives one answer, so a reply produced by a replacement model looks exactly
like a reply from the model that was asked for. This prints a short notice
naming both, so a switch is never silent. It reports only what the session
router recorded since the previous turn, and stays silent on every failure.
"""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

ROUTER_ENV = "AIRLOCK_SESSION_ROUTER_URL"
SNAPSHOT_ENV = "AIRLOCK_SESSION_SNAPSHOT"
FETCH_TIMEOUT_SECONDS = 0.35
MAX_DIAGNOSTICS_BYTES = 256 * 1024
MAX_EVENTS = 256
MAX_NOTICE_LINES = 4
MODEL_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:+/\-\[\]]{0,127}"
# Kinds that mean the answer came from somewhere other than the model asked
# for, or that a limit was deliberately left for Claude Code to handle.
REPORTED_KINDS = (
    "rate_limit_failover_succeeded",
    "failover_overflow_succeeded",
    "rate_limit_chain_exhausted",
    "anthropic_rate_limit_passthrough",
)


def safe_model(value: object) -> str | None:
    if isinstance(value, str) and re.fullmatch(MODEL_PATTERN, value):
        return value
    return None


def router_address(value: object) -> tuple[str, int] | None:
    if not isinstance(value, str) or re.fullmatch(
        r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", value
    ) is None:
        return None
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port is None
        or not 1 <= port <= 65535
    ):
        return None
    return "127.0.0.1", port


def fetch_events(value: object) -> list[object] | None:
    address = router_address(value)
    if address is None:
        return None
    connection = http.client.HTTPConnection(
        address[0], address[1], timeout=FETCH_TIMEOUT_SECONDS
    )
    try:
        connection.request(
            "GET", "/diagnostics", headers={"Accept": "application/json"}
        )
        response = connection.getresponse()
        content_type = (
            response.getheader("content-type", "").split(";", 1)[0].strip()
        )
        if response.status != 200 or content_type != "application/json":
            return None
        payload = json.loads(response.read(MAX_DIAGNOSTICS_BYTES + 1))
    finally:
        connection.close()
    if not isinstance(payload, dict):
        return None
    events = payload.get("events")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        return None
    return events


def marker_path() -> Path | None:
    """The snapshot's own directory is already a hardened session location."""
    snapshot = os.environ.get(SNAPSHOT_ENV)
    if not snapshot:
        return None
    path = Path(snapshot)
    if path.name.startswith(".") or not path.parent.is_dir():
        return None
    return path.with_name(path.name + ".turn-notice")


def previously_reported(path: Path | None) -> int:
    if path is None or not path.is_file() or path.is_symlink():
        return 0
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0
    return value if value >= 0 else 0


def remember(path: Path | None, count: int) -> None:
    if path is None:
        return
    try:
        path.write_text(str(count), encoding="ascii")
    except OSError:
        pass


def sentence(event: dict[str, object]) -> str | None:
    kind = event.get("kind")
    source = safe_model(event.get("from_model"))
    target = safe_model(event.get("to_model"))
    model = safe_model(event.get("model"))
    if kind == "rate_limit_failover_succeeded" and source and target:
        return (
            f"{source} was rate limited, so {target} answered instead."
            f" This turn's reply came from {target}, not {source}."
        )
    if kind == "failover_overflow_succeeded" and source and target:
        return (
            f"The conversation did not fit {source}, so {target} answered"
            f" instead. This turn's reply came from {target}."
        )
    if kind == "rate_limit_chain_exhausted" and model:
        return (
            f"{model} and every replacement Airlock could try were rate"
            " limited, so nothing answered."
        )
    if kind == "anthropic_rate_limit_passthrough" and model:
        return (
            f"{model} hit an Anthropic rate limit. Airlock left it for Claude"
            " Code to handle rather than switching models."
        )
    return None


def notice(events: list[object], already: int) -> tuple[str | None, int]:
    sentences: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("kind") not in REPORTED_KINDS:
            continue
        line = sentence(event)
        if line is not None:
            sentences.append(line)
    total = len(sentences)
    # A rotated event window can leave fewer than were reported before; treat
    # that as a fresh start rather than reporting old lines a second time.
    if total <= already:
        return None, total
    fresh = sentences[already:]
    body = "\n".join(fresh[-MAX_NOTICE_LINES:])
    return f"Airlock routing\n{body}", total


def main() -> int:
    try:
        events = fetch_events(os.environ.get(ROUTER_ENV))
        if events is None:
            return 0
        path = marker_path()
        message, total = notice(events, previously_reported(path))
        remember(path, total)
        if message is not None:
            sys.stdout.write(
                json.dumps(
                    {"systemMessage": message},
                    separators=(",", ":"),
                    ensure_ascii=True,
                )
                + "\n"
            )
    except BaseException:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
