#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Show a short, failure-silent router summary when an Airlock session ends."""

from __future__ import annotations

from datetime import datetime
import http.client
import json
import os
import re
import sys
from urllib.parse import urlsplit

ROUTER_ENV = "AIRLOCK_SESSION_ROUTER_URL"
FETCH_TIMEOUT_SECONDS = 0.35
MAX_DIAGNOSTICS_BYTES = 256 * 1024
MAX_EVENTS = 256
MAX_MESSAGE_LINES = 6


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


def fetch_diagnostics(value: object) -> dict[str, object] | None:
    address = router_address(value)
    if address is None:
        return None
    connection = http.client.HTTPConnection(
        address[0], address[1], timeout=FETCH_TIMEOUT_SECONDS
    )
    try:
        connection.request("GET", "/diagnostics", headers={"Accept": "application/json"})
        response = connection.getresponse()
        content_type = (
            response.getheader("content-type", "").split(";", 1)[0].strip()
        )
        if response.status != 200 or content_type != "application/json":
            return None
        body = response.read(MAX_DIAGNOSTICS_BYTES + 1)
        if len(body) > MAX_DIAGNOSTICS_BYTES:
            return None
    except (OSError, http.client.HTTPException):
        return None
    finally:
        connection.close()
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def safe_model(value: object) -> str | None:
    if isinstance(value, str) and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:+/\-\[\]]{0,127}", value
    ):
        return value
    return None


def safe_provider(value: object) -> str | None:
    if isinstance(value, str) and value in {
        "anthropic", "openai", "grok", "openrouter"
    }:
        return value
    return None


def safe_timestamp(value: object) -> str | None:
    if not isinstance(value, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value
    ) is None:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return value[11:19] + "Z"


def safe_kind(value: object) -> str | None:
    if (
        isinstance(value, str)
        and 1 <= len(value) <= 64
        and all(" " <= character <= "~" for character in value)
    ):
        return value
    return None


def _safe_bounded_int(value: object, minimum: int, maximum: int) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if minimum <= value <= maximum else None
    return None


def action_sentence(kind: str, event: dict[str, object]) -> str | None:
    model = safe_model(event.get("model"))
    if kind == "session_model_pinned":
        provider = safe_provider(event.get("provider"))
        if model is not None and provider is not None:
            return f"Pinned {model} ({provider}) as the session root."
    elif kind == "router_restarted":
        return (
            "The session router stopped and was restarted on the same"
            " address. Recent actions before that point are not recorded."
        )
    elif kind == "rate_limit_failover_attempted":
        source = safe_model(event.get("from_model"))
        target = safe_model(event.get("to_model"))
        if source is not None and target is not None:
            return f"{source} hit a rate limit; trying {target}."
    elif kind == "rate_limit_failover_succeeded":
        source = safe_model(event.get("from_model"))
        target = safe_model(event.get("to_model"))
        if source is not None and target is not None:
            return f"{source} hit a rate limit; continued on {target}."
    elif kind == "rate_limit_chain_exhausted":
        count = event.get("models_considered")
        if (
            model is not None
            and isinstance(count, int)
            and not isinstance(count, bool)
            and 1 <= count <= 32
        ):
            noun = "model" if count == 1 else "models"
            line = f"The failover chain for {model} exhausted {count} {noun}."
            wait = _safe_bounded_int(event.get("retry_after"), 1, 300)
            if wait is not None:
                line += f" Retry in about {wait}s."
            return line
    elif kind == "rate_limit_cooldown_skipped" and model is not None:
        return f"Skipped {model} because its rate-limit cooldown is active."
    elif kind == "rate_limit_provider_cooldown":
        provider = safe_provider(event.get("provider"))
        if provider is not None and model is not None:
            return (
                f"{model} was the second {provider} model to hit a rate limit,"
                " so the whole subscription is cooling down."
            )
        return None
    elif kind == "anthropic_rate_limit_passthrough":
        if model is not None:
            return (
                f"{model} hit an Anthropic rate limit; passed it to Claude"
                " Code unchanged instead of handing off."
            )
        return None
    elif kind == "background_model_substituted":
        requested = safe_model(event.get("requested"))
        if requested is not None and model is not None:
            return (
                f"{requested} is not enabled here, so Claude Code's background"
                f" request was served by {model}."
            )
        return None
    elif kind == "model_not_enabled":
        if model is not None:
            return f"Refused a request for {model}: not enabled in this session."
        return None
    elif kind == "upstream_context_overflow":
        if model is None:
            return None
        prompt = _safe_bounded_int(event.get("prompt_tokens"), 1, 100_000_000)
        limit = _safe_bounded_int(event.get("limit_tokens"), 1, 100_000_000)
        if prompt is not None and limit is not None:
            return (
                f"{model} rejected the request: {prompt} tokens exceed its"
                f" {limit}-token context window."
            )
        return f"{model} rejected the request because it exceeds the context window."
    elif kind == "failover_overflow_skipped":
        source = safe_model(event.get("from_model"))
        target = safe_model(event.get("to_model"))
        estimated = _safe_bounded_int(event.get("estimated_tokens"), 1, 100_000_000)
        if source is not None and target is not None and estimated is not None:
            return (
                f"Skipped {target}: about {estimated} tokens will not fit its"
                " context window."
            )
        return None
    elif kind == "failover_overflow_attempted":
        source = safe_model(event.get("from_model"))
        target = safe_model(event.get("to_model"))
        if source is not None and target is not None:
            return f"{source} could not fit the conversation; trying {target}."
        return None
    elif kind == "failover_overflow_succeeded":
        source = safe_model(event.get("from_model"))
        target = safe_model(event.get("to_model"))
        if source is not None and target is not None:
            return f"{source} overflowed; continued on {target}."
        return None
    elif kind == "failover_shrink_compacted":
        target = safe_model(event.get("target_model"))
        compactor = safe_model(event.get("compactor_model"))
        if target is not None and compactor is not None:
            return f"Condensed earlier history through {compactor} before retrying on {target}."
        return None
    elif kind == "failover_shrink_truncated":
        target = safe_model(event.get("target_model"))
        if target is not None:
            return f"Trimmed older history so the handoff fits {target}."
        return None
    elif kind == "failover_shrink_failed":
        target = safe_model(event.get("target_model"))
        if target is not None:
            return f"Could not shrink the conversation for {target}; the handoff failed."
        return None
    elif kind == "overflow_chain_exhausted":
        count = event.get("models_considered")
        if (
            model is not None
            and isinstance(count, int)
            and not isinstance(count, bool)
            and 1 <= count <= 32
        ):
            noun = "model" if count == 1 else "models"
            return (
                f"No enabled model could fit the conversation after {count}"
                f" {noun} were tried."
            )
    elif kind == "openrouter_effort_clamped" and model is not None:
        return f"Clamped OpenRouter effort for {model} from max to high."
    elif kind == "sanitized_error_substituted":
        provider = safe_provider(event.get("provider"))
        if model is not None and provider is not None:
            return f"Replaced the {provider} error for {model} with a safe local message."
    elif kind == "openrouter_server_tools_stripped":
        count = event.get("removed_count")
        if (
            model is not None
            and isinstance(count, int)
            and not isinstance(count, bool)
            and 1 <= count <= 64
        ):
            noun = "tool" if count == 1 else "tools"
            return f"Removed {count} unsupported OpenRouter server {noun} for {model}."
    return None


def notice_message(payload: dict[str, object]) -> str | None:
    profile = payload.get("profile")
    root_model = safe_model(payload.get("root_model"))
    root_provider = safe_provider(payload.get("root_provider"))
    events = payload.get("events")
    if (
        not isinstance(profile, str)
        or len(profile) > 64
        or re.fullmatch(
            r"[a-z0-9]+(?:[._-][a-z0-9]+)*", profile, re.ASCII
        ) is None
        or root_model is None
        or root_provider is None
        or not isinstance(events, list)
        or len(events) > MAX_EVENTS
    ):
        return None

    actions: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        timestamp = safe_timestamp(event.get("timestamp"))
        kind = safe_kind(event.get("kind"))
        if timestamp is None or kind is None:
            continue
        sentence = action_sentence(kind, event)
        if sentence is None:
            sentence = f"Router action: {kind}."
        actions.append(f"{timestamp} - {sentence}")

    lines = [f"Airlock router: {profile} profile; root {root_model} ({root_provider})"]
    lines.extend(actions[-(MAX_MESSAGE_LINES - 1):])
    return "\n".join(lines[:MAX_MESSAGE_LINES])


def main() -> int:
    try:
        payload = fetch_diagnostics(os.environ.get(ROUTER_ENV))
        if payload is None:
            return 0
        message = notice_message(payload)
        if message is not None:
            sys.stdout.write(
                json.dumps({"systemMessage": message}, separators=(",", ":"), ensure_ascii=True)
                + "\n"
            )
    except BaseException:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
