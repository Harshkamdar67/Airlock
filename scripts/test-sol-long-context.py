#!/usr/bin/env python3
"""Run one explicitly authorized, synthetic Sol request above 300k tokens."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

MODEL = "gpt-5.6-sol"
WIRE_MODEL = "gpt-5.6-sol"
AUTHORIZATION = (
    "provider=openai;model=gpt-5.6-sol;repository-files=none;"
    "public-web=off;extra-usage=off;fast=off;workers=0"
)
EARLY_MARKER = "AIRLOCK_EARLY_4B1F6D28"
LATE_MARKER = "AIRLOCK_LATE_9C73A5E2"
PAYLOAD_CHARACTERS = 950_000
MIN_OBSERVED_INPUT_TOKENS = 300_000
MAX_STREAM_BYTES = 4 * 1024 * 1024
USAGE_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)


class ProofError(RuntimeError):
    pass


def build_prompt(payload_characters: int = PAYLOAD_CHARACTERS) -> str:
    if payload_characters < 1:
        raise ProofError("synthetic payload size must be positive")
    chunks: list[str] = []
    size = 0
    counter = 0
    while size < payload_characters:
        digest = hashlib.sha256(f"airlock-long-context-{counter}".encode("ascii")).hexdigest()
        chunk = digest + "\n"
        chunks.append(chunk)
        size += len(chunk)
        counter += 1
    payload = "".join(chunks)[:payload_characters]
    return (
        "This is a synthetic context-window test. Do not use tools. Read to the end.\n"
        f"Remember this exact early marker: {EARLY_MARKER}\n"
        "Synthetic payload begins:\n"
        f"{payload}\n"
        "Synthetic payload ends.\n"
        f"Remember this exact late marker: {LATE_MARKER}\n"
        "Reply with exactly one line containing the early marker, one space, and the late marker.\n"
    )


def command_for_launcher(launcher: str) -> list[str]:
    arguments = [
        "openai",
        "sol",
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        "1",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--tools",
        "",
    ]
    path = Path(launcher)
    if os.name == "nt" and path.suffix.lower() in {".cmd", ".bat"}:
        powershell_launcher = path.with_suffix(".ps1")
        if not powershell_launcher.is_file():
            raise ProofError("installed Windows PowerShell launcher was not found")
        return [
            "powershell", "-NoProfile", "-File", str(powershell_launcher), *arguments
        ]
    if os.name == "nt" and path.suffix.lower() == ".ps1":
        return ["powershell", "-NoProfile", "-File", str(path), *arguments]
    return [str(path), *arguments]


def text_from_stream_event(event: dict[str, Any]) -> str:
    texts: list[str] = []
    result = event.get("result")
    if isinstance(result, str):
        texts.append(result)
    message = event.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str):
                        texts.append(text)
    return "\n".join(texts)


def sanitized_stream_result(raw_output: str) -> dict[str, Any]:
    if len(raw_output.encode("utf-8")) > MAX_STREAM_BYTES:
        raise ProofError("Claude Code returned an unexpectedly large stream")
    usage = {field: 0 for field in USAGE_FIELDS}
    models: set[str] = set()
    early_seen = False
    late_seen = False
    result_seen = False
    for raw_line in raw_output.splitlines():
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ProofError("Claude Code returned invalid stream JSON") from exc
        if not isinstance(event, dict):
            raise ProofError("Claude Code returned an invalid stream event")
        if event.get("type") == "result":
            result_seen = True
        model = event.get("model")
        if isinstance(model, str):
            models.add(model)
        message = event.get("message")
        if isinstance(message, dict):
            message_model = message.get("model")
            if isinstance(message_model, str):
                models.add(message_model)
        for candidate in (event.get("usage"), message.get("usage") if isinstance(message, dict) else None):
            if not isinstance(candidate, dict):
                continue
            for field in USAGE_FIELDS:
                value = candidate.get(field)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    usage[field] = max(usage[field], value)
        text = text_from_stream_event(event)
        early_seen = early_seen or EARLY_MARKER in text
        late_seen = late_seen or LATE_MARKER in text
    observed_input = (
        usage["input_tokens"]
        + usage["cache_creation_input_tokens"]
        + usage["cache_read_input_tokens"]
    )
    return {
        "provider": "openai",
        "configured_model": MODEL,
        "observed_models": sorted(models),
        "result_seen": result_seen,
        "early_marker_seen": early_seen,
        "late_marker_seen": late_seen,
        "usage": usage,
        "observed_input_and_cache_tokens": observed_input,
    }


def validate_proof(result: dict[str, Any]) -> None:
    observed_models = result.get("observed_models")
    if not isinstance(observed_models, list) or not set(observed_models).intersection(
        {MODEL, WIRE_MODEL}
    ):
        raise ProofError("Claude Code did not identify the configured Sol route")
    if not result.get("result_seen"):
        raise ProofError("Claude Code did not emit a final result event")
    if not result.get("early_marker_seen") or not result.get("late_marker_seen"):
        raise ProofError("Sol did not return both distant synthetic markers")
    observed = result.get("observed_input_and_cache_tokens")
    if not isinstance(observed, int) or observed <= MIN_OBSERVED_INPUT_TOKENS:
        raise ProofError("provider-reported input and cache usage did not exceed 300,000 tokens")


def resolve_launcher(raw_launcher: str | None) -> str:
    launcher = raw_launcher or shutil.which("airlock")
    if not launcher:
        raise ProofError("installed airlock launcher was not found")
    path = Path(launcher)
    if not path.is_file():
        raise ProofError("installed airlock launcher is not a file")
    return str(path.resolve())


def sanitized_failure_category(raw_output: str, raw_error: str) -> str:
    """Classify a failed CLI run without returning provider or prompt text."""
    combined = (raw_output + "\n" + raw_error).lower()
    categories = (
        ("context_limit", ("context window", "prompt is too long", "input too long", "maximum context")),
        ("rate_limited", ("rate limit", "usage limit", "quota")),
        ("authentication", ("authentication", "not logged in", "unauthorized")),
        ("model_unavailable", ("model not found", "model is not available", "unsupported model")),
        ("invalid_cli", ("unknown option", "requires a value", "invalid argument")),
    )
    for category, markers in categories:
        if any(marker in combined for marker in markers):
            return category
    return "request_failed"


def proof_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ if base is None else base)
    for variable in (
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
        "CLAUDE_CODE_DISABLE_1M_CONTEXT",
        "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT",
        "AIRLOCK_CONTEXT_WINDOW",
    ):
        environment.pop(variable, None)
    environment.update({
        "AIRLOCK_MAX_CONCURRENT_SUBAGENTS": "off",
        "AIRLOCK_OPENAI_FAST": "off",
        "AIRLOCK_ANTHROPIC_FAST": "off",
        "AIRLOCK_EXTRA_USAGE_POLICY": "never",
        "AIRLOCK_FAILOVER_POLICY": "never",
        "AIRLOCK_SWARM_FAST": "off",
    })
    return environment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authorized-scope",
        help="Exact approved provider/model/files/web/usage/Fast/worker scope",
    )
    parser.add_argument("--airlock", help="Installed Airlock launcher path")
    args = parser.parse_args()
    if args.authorized_scope != AUTHORIZATION:
        print(
            "long-context proof: explicit authorization is missing or does not match the fixed safe scope",
            file=sys.stderr,
        )
        return 2
    try:
        launcher = resolve_launcher(args.airlock)
        prompt = build_prompt()
        environment = proof_environment()
        with tempfile.TemporaryDirectory(prefix="airlock-sol-context-") as directory:
            completed = subprocess.run(
                command_for_launcher(launcher),
                input=prompt,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=directory,
                env=environment,
                timeout=30 * 60,
                check=False,
            )
        prompt = ""
        if completed.returncode != 0:
            category = sanitized_failure_category(completed.stdout, completed.stderr)
            raise ProofError(
                f"installed Airlock/Claude Code exited with status {completed.returncode} "
                f"({category})"
            )
        result = sanitized_stream_result(completed.stdout)
        validate_proof(result)
    except subprocess.TimeoutExpired:
        print("long-context proof: request timed out", file=sys.stderr)
        return 1
    except (OSError, ProofError) as exc:
        print(f"long-context proof: {exc}", file=sys.stderr)
        return 1

    safe_result = {
        "provider": result["provider"],
        "model": result["configured_model"],
        "fast": False,
        "extra_usage": False,
        "workers": 0,
        "repository_files": "none",
        "public_web": False,
        "observed_input_and_cache_tokens": result["observed_input_and_cache_tokens"],
        "output_tokens": result["usage"]["output_tokens"],
        "early_marker_seen": result["early_marker_seen"],
        "late_marker_seen": result["late_marker_seen"],
        "passed": True,
    }
    print(json.dumps(safe_result, separators=(",", ":"), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
