#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Manage Airlock's private loopback open-model registry."""

from __future__ import annotations

import argparse
import http.client
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable

import airlock_policy as policy

MAX_CATALOG_BYTES = 1024 * 1024
MAX_CATALOG_MODELS = 256
MAX_ACCEPTED_RESPONSE_MODELS = 16
MAX_PRIVATE_INPUT_BYTES = 64 * 1024
MAX_HIDDEN_INPUT_CHARS = 1024
MAX_HIDDEN_INPUT_BYTES = 4096
NETWORK_TIMEOUT_SECONDS = 10


class OpenModelError(RuntimeError):
    """Raised when local open-model management cannot complete safely."""


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise OpenModelError(
            f"invalid arguments\n{self.format_usage().strip()}"
        )


def _read_private_json(
    expected_fields: set[str],
    *,
    input_stream: Any | None = None,
) -> dict[str, Any]:
    source = sys.stdin.buffer if input_stream is None else input_stream
    try:
        raw = source.read(MAX_PRIVATE_INPUT_BYTES + 1)
    except (OSError, AttributeError) as exc:
        raise OpenModelError("open-model private input could not be read") from exc
    if isinstance(raw, str):
        try:
            raw = raw.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise OpenModelError("open-model private input is invalid") from exc
    if type(raw) is not bytes or len(raw) > MAX_PRIVATE_INPUT_BYTES:
        raise OpenModelError("open-model private input is too large")
    try:
        payload = policy.load_json_bytes(
            raw, max_bytes=MAX_PRIVATE_INPUT_BYTES
        )
    except policy.PolicyValidationError as exc:
        raise OpenModelError("open-model private input is invalid") from exc
    if type(payload) is not dict or set(payload) != expected_fields:
        raise OpenModelError("open-model private input schema is invalid")
    return payload


def _collect_hidden_input(read_character: Callable[[], str]) -> str:
    """Collect one hidden line without ever retaining more than its safe limit."""

    characters: list[str] = []
    encoded_bytes = 0
    too_large = False
    invalid = False
    while True:
        character = read_character()
        if character == "":
            raise EOFError
        if type(character) is not str or len(character) != 1:
            invalid = True
            continue
        if character in {"\r", "\n"}:
            break
        if character == "\x03":
            raise KeyboardInterrupt
        if character == "\x04":
            raise EOFError
        if character in {"\x08", "\x7f"}:
            if not too_large and characters:
                removed = characters.pop()
                encoded_bytes -= len(removed.encode("utf-8", errors="strict"))
            continue
        if character == "\x15":
            if not too_large:
                characters.clear()
                encoded_bytes = 0
            continue
        try:
            character_bytes = len(character.encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            invalid = True
            continue
        if (
            too_large
            or len(characters) >= MAX_HIDDEN_INPUT_CHARS
            or encoded_bytes + character_bytes > MAX_HIDDEN_INPUT_BYTES
        ):
            # Keep draining through the newline so a pasted suffix cannot become
            # the caller's next shell command, but retain none of the excess.
            too_large = True
            continue
        characters.append(character)
        encoded_bytes += character_bytes
    if too_large:
        raise OpenModelError("open-model private input is too large")
    if invalid:
        raise OpenModelError("open-model private input was not accepted")
    return "".join(characters)


def _read_hidden_windows(prompt: str) -> str:
    import msvcrt

    pending: list[str] = []

    def read_character() -> str:
        if pending:
            return pending.pop()
        character = msvcrt.getwch()
        if character in {"\x00", "\xe0"}:
            # A console function key arrives as a two-unit sequence. Consume it
            # and return an invalid control character rather than model data.
            msvcrt.getwch()
            return "\x00"
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDBFF:
            following = msvcrt.getwch()
            following_codepoint = ord(following)
            if 0xDC00 <= following_codepoint <= 0xDFFF:
                return chr(
                    0x10000
                    + ((codepoint - 0xD800) << 10)
                    + (following_codepoint - 0xDC00)
                )
            pending.append(following)
            return "\x00"
        if 0xDC00 <= codepoint <= 0xDFFF:
            return "\x00"
        return character

    sys.stderr.write(prompt)
    sys.stderr.flush()
    try:
        return _collect_hidden_input(read_character)
    finally:
        sys.stderr.write("\n")
        sys.stderr.flush()


def _read_hidden_posix(prompt: str) -> str:
    import termios

    descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(descriptor)
    hidden = previous.copy()
    hidden[6] = previous[6].copy()
    hidden[3] &= ~(termios.ECHO | termios.ICANON)
    hidden[6][termios.VMIN] = 1
    hidden[6][termios.VTIME] = 0
    prompt_written = False
    try:
        termios.tcsetattr(descriptor, termios.TCSAFLUSH, hidden)
        sys.stderr.write(prompt)
        sys.stderr.flush()
        prompt_written = True
        return _collect_hidden_input(lambda: sys.stdin.read(1))
    finally:
        termios.tcsetattr(descriptor, termios.TCSAFLUSH, previous)
        if prompt_written:
            sys.stderr.write("\n")
            sys.stderr.flush()


def _read_hidden(prompt: str) -> str:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise OpenModelError(
            "private input requires a terminal; rerun with --stdin"
        )
    try:
        if os.name == "nt":
            return _read_hidden_windows(prompt)
        return _read_hidden_posix(prompt)
    except OpenModelError:
        raise
    except (
        EOFError,
        KeyboardInterrupt,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        raise OpenModelError("open-model private input was not accepted") from exc


def _validated_endpoint_private_input(payload: dict[str, Any]) -> str:
    base_url = payload.get("base_url")
    try:
        return policy.validate_openmodel_base_url(base_url)
    except policy.PolicyValidationError as exc:
        raise OpenModelError(str(exc)) from exc


def endpoint_private_input(
    *,
    from_stdin: bool,
    input_stream: Any | None = None,
) -> str:
    if from_stdin:
        payload = _read_private_json(
            {"base_url"}, input_stream=input_stream
        )
    else:
        payload = {"base_url": _read_hidden("Loopback base URL: ")}
    return _validated_endpoint_private_input(payload)


def _validated_route_private_input(
    payload: dict[str, Any],
) -> tuple[str, list[str]]:
    upstream_model = payload.get("upstream_model")
    accepted = payload.get("accepted_response_models")
    if (
        type(accepted) is not list
        or not 1 <= len(accepted) <= MAX_ACCEPTED_RESPONSE_MODELS
        or any(type(identity) is not str for identity in accepted)
    ):
        raise OpenModelError("open-model private input schema is invalid")
    try:
        validated_upstream = policy.validate_openmodel_identity(
            upstream_model, "upstream model identity"
        )
        validated_accepted = [
            policy.validate_openmodel_identity(
                identity, f"accepted response model identity {index}"
            )
            for index, identity in enumerate(accepted)
        ]
    except policy.PolicyValidationError as exc:
        raise OpenModelError(str(exc)) from exc
    if len(validated_accepted) != len(set(validated_accepted)):
        raise OpenModelError(
            "accepted response model identities must not contain duplicates"
        )
    return validated_upstream, validated_accepted


def route_private_input(
    *,
    from_stdin: bool,
    input_stream: Any | None = None,
) -> tuple[str, list[str]]:
    if from_stdin:
        payload = _read_private_json(
            {"upstream_model", "accepted_response_models"},
            input_stream=input_stream,
        )
    else:
        upstream_model = _read_hidden("Upstream model identity: ")
        first = _read_hidden(
            "Accepted response model identity (blank uses upstream): "
        )
        if not first:
            accepted = [upstream_model]
        else:
            accepted = [first]
            for index in range(1, MAX_ACCEPTED_RESPONSE_MODELS):
                additional = _read_hidden(
                    f"Additional accepted response identity {index + 1} "
                    "(blank finishes): "
                )
                if not additional:
                    break
                accepted.append(additional)
        payload = {
            "upstream_model": upstream_model,
            "accepted_response_models": accepted,
        }
    return _validated_route_private_input(payload)


def load_registry_version(
    path: Path,
) -> tuple[policy.OpenModelRegistry, str | None]:
    try:
        registry = policy.load_openmodel_registry(path)
    except policy.RegistryNotFoundError:
        return policy.OpenModelRegistry(
            schema_version=1,
            endpoints=(),
            models=(),
        ), None
    except policy.PolicyValidationError as exc:
        raise OpenModelError("open-model registry is invalid") from exc
    return registry, registry.digest()


def commit_registry_update(
    path: Path,
    expected_digest: str | None,
    value: dict[str, Any],
) -> policy.OpenModelRegistry:
    try:
        return policy.compare_and_swap_openmodel_registry(
            path, expected_digest, value
        )
    except (policy.RegistryConflictError, policy.RegistryBusyError) as exc:
        raise OpenModelError(str(exc)) from exc
    except policy.PolicyValidationError as exc:
        raise OpenModelError(f"open-model registry update is invalid: {exc}") from exc


def confirm(
    question: str,
    *,
    approved: bool,
    input_stream: Any | None = None,
) -> None:
    if approved:
        return
    stream = input_stream or sys.stdin
    if not stream.isatty():
        raise OpenModelError("confirmation is required; rerun with --yes")
    print(f"{question} [y/N] ", end="", flush=True)
    answer = stream.readline().strip().lower()
    if answer not in {"y", "yes"}:
        raise OpenModelError("operation cancelled")


def list_endpoints(path: Path) -> int:
    registry, _digest = load_registry_version(path)
    if not registry.endpoints:
        print("No open-model endpoints are declared.")
        return 0
    print("ENDPOINT\tMAX_CONCURRENCY\tENABLED\tROUTES")
    for endpoint in registry.endpoints:
        route_count = sum(
            entry.endpoint == endpoint.id for entry in registry.models
        )
        print(
            f"{endpoint.id}\t{endpoint.max_concurrency}\t"
            f"{'yes' if endpoint.enabled else 'no'}\t{route_count}"
        )
    return 0


def add_endpoint(
    path: Path,
    endpoint_id: str,
    base_url: str,
    *,
    max_concurrency: int,
    enabled: bool,
) -> int:
    registry, expected_digest = load_registry_version(path)
    if len(registry.endpoints) >= policy.MAX_OPENMODEL_ENDPOINTS:
        raise OpenModelError(
            f"open-model registry already has {policy.MAX_OPENMODEL_ENDPOINTS} endpoints"
        )
    if any(endpoint.id == endpoint_id for endpoint in registry.endpoints):
        raise OpenModelError(f"open-model endpoint already exists: {endpoint_id}")
    value = registry.to_dict()
    value["endpoints"].append({
        "id": endpoint_id,
        "base_url": base_url,
        "trust": "loopback",
        "protocol": "openai-chat-completions-v1",
        "auth": "none",
        "max_concurrency": max_concurrency,
        "enabled": enabled,
    })
    commit_registry_update(path, expected_digest, value)
    state = "enabled" if enabled else "disabled"
    print(f"Added open-model endpoint {endpoint_id} ({state}).")
    return 0


def remove_endpoint(
    path: Path,
    endpoint_id: str,
    *,
    approved: bool,
) -> int:
    registry, expected_digest = load_registry_version(path)
    if not any(endpoint.id == endpoint_id for endpoint in registry.endpoints):
        raise OpenModelError(f"open-model endpoint does not exist: {endpoint_id}")
    references = sorted(
        entry.route for entry in registry.models if entry.endpoint == endpoint_id
    )
    if references:
        raise OpenModelError(
            f"open-model endpoint is still used by {len(references)} route(s): "
            f"{', '.join(references)}"
        )
    confirm(f"Remove open-model endpoint {endpoint_id}?", approved=approved)
    value = registry.to_dict()
    value["endpoints"] = [
        endpoint
        for endpoint in value["endpoints"]
        if endpoint["id"] != endpoint_id
    ]
    commit_registry_update(path, expected_digest, value)
    print(f"Removed open-model endpoint {endpoint_id}.")
    return 0


def list_routes(path: Path) -> int:
    registry, _digest = load_registry_version(path)
    if not registry.models:
        print("No open-model routes are declared.")
        return 0
    endpoints = {endpoint.id: endpoint for endpoint in registry.endpoints}
    print(
        "ROUTE\tENDPOINT\tCONTEXT\tMAX_OUTPUT\tSTREAMING\tTOOLS\t"
        "TOOL_CHOICE\tWORKER\tSTATE"
    )
    for entry in registry.models:
        endpoint = endpoints[entry.endpoint]
        active = entry.enabled and endpoint.enabled
        if active:
            state = "enabled"
        elif not entry.enabled:
            state = "route-disabled"
        else:
            state = "endpoint-disabled"
        print(
            f"{entry.route}\t{entry.endpoint}\t{entry.context_window}\t"
            f"{entry.max_output_tokens}\t"
            f"{'yes' if entry.streaming else 'no'}\t{entry.tools}\t"
            f"{','.join(entry.tool_choice) or '-'}\t"
            f"{'yes' if entry.worker else 'no'}\t{state}"
        )
    return 0


def add_route(
    path: Path,
    route: str,
    endpoint_id: str,
    upstream_model: str,
    *,
    accepted_response_models: list[str],
    context_window: int,
    max_output_tokens: int,
    streaming: bool,
    tools: str,
    tool_choice: list[str],
    worker: bool,
    enabled: bool,
) -> int:
    registry, expected_digest = load_registry_version(path)
    if len(registry.models) >= policy.MAX_OPENMODEL_MODELS:
        raise OpenModelError(
            f"open-model registry already has {policy.MAX_OPENMODEL_MODELS} routes"
        )
    if any(entry.route == route for entry in registry.models):
        raise OpenModelError(f"open-model route already exists: {route}")
    if not any(endpoint.id == endpoint_id for endpoint in registry.endpoints):
        raise OpenModelError(f"open-model endpoint does not exist: {endpoint_id}")
    if len(accepted_response_models) != len(set(accepted_response_models)):
        raise OpenModelError(
            "accepted response model identities must not contain duplicates"
        )
    if len(tool_choice) != len(set(tool_choice)):
        raise OpenModelError("tool choice modes must not contain duplicates")
    value = registry.to_dict()
    value["models"].append({
        "route": route,
        "endpoint": endpoint_id,
        "upstream_model": upstream_model,
        "accepted_response_models": sorted(accepted_response_models),
        "context_window": context_window,
        "max_output_tokens": max_output_tokens,
        "streaming": streaming,
        "tools": tools,
        "tool_choice": sorted(tool_choice),
        "worker": worker,
        "enabled": enabled,
    })
    commit_registry_update(path, expected_digest, value)
    state = "enabled" if enabled else "disabled"
    print(f"Added airlock-om-{route} on endpoint {endpoint_id} ({state}).")
    return 0


def remove_route(path: Path, route: str, *, approved: bool) -> int:
    registry, expected_digest = load_registry_version(path)
    if not any(entry.route == route for entry in registry.models):
        raise OpenModelError(f"open-model route does not exist: {route}")
    confirm(f"Remove open-model route {route}?", approved=approved)
    value = registry.to_dict()
    value["models"] = [
        entry for entry in value["models"] if entry["route"] != route
    ]
    commit_registry_update(path, expected_digest, value)
    print(f"Removed airlock-om-{route}.")
    return 0


def _catalog_port(base_url: str) -> int:
    validated = policy.validate_openmodel_base_url(base_url)
    match = re.fullmatch(
        r"http://127\.0\.0\.1:([1-9][0-9]{0,4})/v1",
        validated,
        re.ASCII,
    )
    assert match is not None
    return int(match.group(1))


def fetch_catalog_ids(
    base_url: str,
    *,
    connection_factory: Callable[..., Any] = http.client.HTTPConnection,
    timeout: int = NETWORK_TIMEOUT_SECONDS,
) -> tuple[str, ...]:
    """Fetch one bounded loopback model catalog without credentials or proxies."""

    port = _catalog_port(base_url)
    connection = connection_factory("127.0.0.1", port, timeout=timeout)
    response: Any | None = None
    try:
        connection.request(
            "GET",
            "/v1/models",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "User-Agent": "Airlock open-model check",
            },
        )
        response = connection.getresponse()
        status = response.status
        if 300 <= status < 400:
            raise OpenModelError("open-model catalog redirected unexpectedly")
        if status != 200:
            raise OpenModelError(f"open-model catalog returned HTTP {status}")
        encoding = response.getheader("Content-Encoding", "").strip().lower()
        if encoding not in {"", "identity"}:
            raise OpenModelError("open-model catalog returned compressed content")
        content_type = response.getheader("Content-Type", "")
        content_type = content_type.split(";", 1)[0].strip().lower()
        if content_type not in {"application/json", "application/problem+json"}:
            raise OpenModelError("open-model catalog returned a non-JSON response")
        length_text = response.getheader("Content-Length")
        if length_text:
            try:
                length = int(length_text)
            except ValueError as exc:
                raise OpenModelError("open-model catalog returned an invalid length") from exc
            if length < 0 or length > MAX_CATALOG_BYTES:
                raise OpenModelError("open-model catalog response exceeds its size limit")
        raw = response.read(MAX_CATALOG_BYTES + 1)
    except OpenModelError:
        raise
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise OpenModelError("open-model catalog request failed") from exc
    finally:
        connection.close()
    if len(raw) > MAX_CATALOG_BYTES:
        raise OpenModelError("open-model catalog response exceeds its size limit")
    try:
        payload = policy.load_json_bytes(raw, max_bytes=MAX_CATALOG_BYTES)
    except policy.PolicyValidationError as exc:
        raise OpenModelError("open-model catalog response is invalid") from exc
    if type(payload) is not dict or type(payload.get("data")) is not list:
        raise OpenModelError("open-model catalog response has an invalid shape")
    rows = payload["data"]
    if len(rows) > MAX_CATALOG_MODELS:
        raise OpenModelError("open-model catalog contains too many models")
    identities: list[str] = []
    for index, row in enumerate(rows):
        if type(row) is not dict or "id" not in row:
            raise OpenModelError("open-model catalog contains an invalid model entry")
        try:
            identity = policy.validate_openmodel_identity(
                row["id"], f"catalog data[{index}].id"
            )
        except policy.PolicyValidationError as exc:
            raise OpenModelError(
                "open-model catalog contains an invalid model identity"
            ) from exc
        identities.append(identity)
    return tuple(identities)


def check_route(
    path: Path,
    route: str,
    *,
    fetch: Callable[[str], tuple[str, ...]] = fetch_catalog_ids,
) -> int:
    registry, _digest = load_registry_version(path)
    entry = next((item for item in registry.models if item.route == route), None)
    if entry is None:
        raise OpenModelError(f"open-model route does not exist: {route}")
    endpoint = registry.endpoint(entry.endpoint)
    if endpoint is None:
        raise OpenModelError("open-model route has no declared endpoint")
    if not entry.enabled or not endpoint.enabled:
        raise OpenModelError(f"open-model route is disabled: {route}")
    identities = fetch(endpoint.base_url)
    if entry.upstream_model not in identities:
        raise OpenModelError(
            f"open-model route {route} did not match its configured catalog identity"
        )
    print(f"Open-model route {route}: loopback catalog identity matches.")
    return 0


def doctor_status(path: Path) -> int:
    """Print a sanitized offline registry summary for Doctor."""

    try:
        registry = policy.load_openmodel_registry(path)
    except policy.RegistryNotFoundError:
        print("STATE=absent")
        return 0
    except policy.PolicyValidationError:
        print("STATE=invalid")
        print("DETAIL=registry failed validation")
        return 2
    enabled_endpoints = {
        endpoint.id for endpoint in registry.endpoints if endpoint.enabled
    }
    active = {entry.route for entry in registry.active_models()}
    print("STATE=valid")
    print(f"ENDPOINT_COUNT={len(registry.endpoints)}")
    print(f"ACTIVE_ENDPOINT_COUNT={len(enabled_endpoints)}")
    print(f"ROUTE_COUNT={len(registry.models)}")
    print(f"ACTIVE_ROUTE_COUNT={len(active)}")
    for endpoint in registry.endpoints:
        state = "enabled" if endpoint.enabled else "disabled"
        print(
            f"ENDPOINT={endpoint.id}\t{state}\t"
            f"max-concurrency={endpoint.max_concurrency}"
        )
    for entry in registry.models:
        state = "active" if entry.route in active else "disabled"
        print(f"ROUTE={entry.route}\tendpoint={entry.endpoint}\t{state}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(prog="airlock open-model")
    parser.add_argument("--registry", required=True, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="action", required=True)

    endpoint = commands.add_parser("endpoint")
    endpoint_commands = endpoint.add_subparsers(
        dest="endpoint_action", required=True
    )
    endpoint_commands.add_parser("list")
    endpoint_add = endpoint_commands.add_parser("add")
    endpoint_add.add_argument("id")
    endpoint_add.add_argument(
        "--stdin",
        action="store_true",
        help="read private endpoint JSON from standard input",
    )
    endpoint_add.add_argument("--max-concurrency", required=True, type=int)
    endpoint_add.add_argument("--disabled", action="store_true")
    endpoint_remove = endpoint_commands.add_parser("remove")
    endpoint_remove.add_argument("id")
    endpoint_remove.add_argument("--yes", action="store_true")

    commands.add_parser("list")
    route_add = commands.add_parser("add")
    route_add.add_argument("route")
    route_add.add_argument("endpoint")
    route_add.add_argument(
        "--stdin",
        action="store_true",
        help="read private model identity JSON from standard input",
    )
    route_add.add_argument("--context-window", required=True, type=int)
    route_add.add_argument("--max-output-tokens", required=True, type=int)
    streaming = route_add.add_mutually_exclusive_group(required=True)
    streaming.add_argument("--streaming", action="store_true", dest="streaming")
    streaming.add_argument("--no-streaming", action="store_false", dest="streaming")
    route_add.add_argument(
        "--tools", required=True, choices=("none", "single", "parallel")
    )
    route_add.add_argument(
        "--tool-choice", action="append", default=[]
    )
    worker = route_add.add_mutually_exclusive_group(required=True)
    worker.add_argument("--worker", action="store_true", dest="worker")
    worker.add_argument("--no-worker", action="store_false", dest="worker")
    route_add.add_argument("--disabled", action="store_true")

    route_remove = commands.add_parser("remove")
    route_remove.add_argument("route")
    route_remove.add_argument("--yes", action="store_true")

    check = commands.add_parser("check")
    check.add_argument("route")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if (
        len(arguments) == 3
        and arguments[0] == "--registry"
        and arguments[2] == "_doctor-status"
    ):
        try:
            return doctor_status(Path(arguments[1]).expanduser())
        except policy.PolicyValidationError as exc:
            print(f"airlock open-model: {exc}", file=sys.stderr)
            return 2
    try:
        args = build_parser().parse_args(arguments)
        path = Path(args.registry).expanduser()
        if args.action == "endpoint":
            if args.endpoint_action == "list":
                return list_endpoints(path)
            if args.endpoint_action == "add":
                base_url = endpoint_private_input(from_stdin=args.stdin)
                return add_endpoint(
                    path,
                    args.id,
                    base_url,
                    max_concurrency=args.max_concurrency,
                    enabled=not args.disabled,
                )
            if args.endpoint_action == "remove":
                return remove_endpoint(path, args.id, approved=args.yes)
        if args.action == "list":
            return list_routes(path)
        if args.action == "add":
            upstream_model, accepted_response_models = route_private_input(
                from_stdin=args.stdin
            )
            return add_route(
                path,
                args.route,
                args.endpoint,
                upstream_model,
                accepted_response_models=accepted_response_models,
                context_window=args.context_window,
                max_output_tokens=args.max_output_tokens,
                streaming=args.streaming,
                tools=args.tools,
                tool_choice=args.tool_choice,
                worker=args.worker,
                enabled=not args.disabled,
            )
        if args.action == "remove":
            return remove_route(path, args.route, approved=args.yes)
        if args.action == "check":
            return check_route(path, args.route)
        raise OpenModelError("unsupported open-model action")
    except (OpenModelError, policy.PolicyValidationError) as exc:
        print(f"airlock open-model: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
