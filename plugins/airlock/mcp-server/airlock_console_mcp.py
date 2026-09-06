#!/usr/bin/env python3
# Managed by https://github.com/Harshkamdar67/Airlock
"""Airlock Console tools: stdio MCP thin client for the local Console API.

Loads the shared tool contract from a managed bin helper, validates calls
locally, and POSTs to http://127.0.0.1:4783/api/tools/call. Standard library
only. Speaks newline-delimited JSON-RPC 2.0 on stdin and stdout.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "airlock-console-tools"
SERVER_VERSION = "0.1.0"
DEFAULT_CONSOLE_URL = "http://127.0.0.1:4783"
CONTRACT_BASENAME = "airlock_console_tools.py"
CONSOLE_ADDRESS_BASENAME = "console-address.json"
MAX_CONSOLE_ADDRESS_BYTES = 16 * 1024
MAX_PROCESS_ID = 0xFFFFFFFF
CONSOLE_ADDRESS_KEYS = frozenset({"schema_version", "url", "pid", "console_id"})
CONSOLE_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CONSOLE_URL_PATTERN = re.compile(r"^http://127\.0\.0\.1:([1-9][0-9]{0,4})$")
REQUEST_TIMEOUT_SECONDS = 5.0
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
MAX_STDIN_LINE_BYTES = 256 * 1024
MAX_JSON_DEPTH = 12
MAX_JSON_NODES = 4096

CONSOLE_ABSENT_MESSAGE = (
    "Airlock Console is not running at 127.0.0.1:4783. "
    "Start it with `airlock console`, then try again."
)
HTTP_ERROR_MESSAGE = "Airlock Console returned an error for this tool call."
MALFORMED_RESPONSE_MESSAGE = "Airlock Console returned a malformed tool result."
OVERSIZED_RESPONSE_MESSAGE = "Airlock Console returned an oversized tool result."
INVALID_URL_MESSAGE = "console URL must be exact http://127.0.0.1:PORT"


class ConsoleToolsError(RuntimeError):
    """A bounded, safe failure for the MCP wrapper."""


class ConsoleConnectionError(ConsoleToolsError):
    """The selected Console address did not accept a connection."""


def default_console_runtime_root() -> Path:
    """Return the per-user runtime root shared with Airlock Console."""

    if os.name == "nt":
        fallback = Path.home() / "AppData" / "Local"
        base = Path(os.environ.get("LOCALAPPDATA", fallback))
        return base / "Airlock"
    fallback = Path.home() / ".local" / "state"
    base = Path(os.environ.get("XDG_STATE_HOME", fallback))
    return base / "airlock"


def default_console_address_file() -> Path:
    """Return the default discovery marker path."""

    return default_console_runtime_root() / CONSOLE_ADDRESS_BASENAME


def parse_console_url(value: str) -> str:
    """Accept only exact http://127.0.0.1:PORT."""

    if type(value) is not str or len(value) > 32:
        raise ConsoleToolsError(INVALID_URL_MESSAGE)
    match = CONSOLE_URL_PATTERN.fullmatch(value)
    if match is None:
        raise ConsoleToolsError(INVALID_URL_MESSAGE)
    port = int(match.group(1))
    if not 1 <= port <= 65535:
        raise ConsoleToolsError(INVALID_URL_MESSAGE)
    return f"http://127.0.0.1:{port}"


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _is_reparse_point(details: os.stat_result) -> bool:
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _read_regular_file_no_follow(path: Path, max_bytes: int) -> bytes | None:
    """Read a bounded regular file without following its final symlink."""

    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode) or _is_reparse_point(before):
            return None
        flags = os.O_RDONLY
        for name in ("O_BINARY", "O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW"):
            flags |= getattr(os, name, 0)
        descriptor = os.open(path, flags)
    except (OSError, TypeError, ValueError):
        return None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _is_reparse_point(opened):
            return None
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            return None
        if opened.st_size < 0 or opened.st_size > max_bytes:
            return None
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(4096, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > max_bytes:
            return None
        return raw
    except (OSError, OverflowError, MemoryError):
        return None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def load_console_address(path: Path | None = None) -> str | None:
    """Read one strict v1 address marker, returning only its normalized URL."""

    marker = default_console_address_file() if path is None else Path(path)
    raw = _read_regular_file_no_follow(marker, MAX_CONSOLE_ADDRESS_BYTES)
    if raw is None:
        return None
    try:
        text = raw.decode("utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=_strict_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite number")
            ),
        )
    except (UnicodeDecodeError, ValueError, RecursionError, MemoryError):
        return None
    if type(payload) is not dict or set(payload) != CONSOLE_ADDRESS_KEYS:
        return None
    if type(payload.get("schema_version")) is not int:
        return None
    if payload["schema_version"] != 1:
        return None
    pid = payload.get("pid")
    if type(pid) is not int or not 1 <= pid <= MAX_PROCESS_ID:
        return None
    console_id = payload.get("console_id")
    if type(console_id) is not str or CONSOLE_ID_PATTERN.fullmatch(console_id) is None:
        return None
    try:
        return parse_console_url(payload.get("url"))
    except ConsoleToolsError:
        return None


def console_candidates(
    explicit_url: str | None,
    discovery_file: Path | None,
) -> list[str]:
    """Return the exact ordered addresses eligible for one tool call."""

    if explicit_url is not None:
        return [parse_console_url(explicit_url)]
    discovered = load_console_address(discovery_file)
    if discovered is None or discovered == DEFAULT_CONSOLE_URL:
        return [DEFAULT_CONSOLE_URL]
    return [discovered, DEFAULT_CONSOLE_URL]


def load_contract(path: Path):
    """Load the shared contract module from a regular non-symlink file."""

    if path.name != CONTRACT_BASENAME:
        raise ConsoleToolsError(
            f"contract path basename must be {CONTRACT_BASENAME}"
        )
    try:
        if path.is_symlink() or not path.is_file():
            raise ConsoleToolsError("contract path must be a regular file")
    except OSError as error:
        raise ConsoleToolsError("contract path is not readable") from error
    spec = importlib.util.spec_from_file_location("airlock_console_tools", path)
    if spec is None or spec.loader is None:
        raise ConsoleToolsError("could not load console tools contract")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    required = (
        "tool_manifest",
        "validate_tool_call",
        "project_tool_result",
        "ToolContractError",
        "default_console_address_file",
        "default_console_runtime_root",
    )
    for name in required:
        if not hasattr(module, name):
            raise ConsoleToolsError("console tools contract is incomplete")
    return module


def _count_json_nodes(value: object, *, depth: int, counter: list[int]) -> None:
    counter[0] += 1
    if counter[0] > MAX_JSON_NODES:
        raise ConsoleToolsError(MALFORMED_RESPONSE_MESSAGE)
    if depth > MAX_JSON_DEPTH:
        raise ConsoleToolsError(MALFORMED_RESPONSE_MESSAGE)
    if isinstance(value, dict):
        for item in value.values():
            _count_json_nodes(item, depth=depth + 1, counter=counter)
    elif isinstance(value, list):
        for item in value:
            _count_json_nodes(item, depth=depth + 1, counter=counter)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ConsoleToolsError(MALFORMED_RESPONSE_MESSAGE)


def parse_json_bytes(raw: bytes, *, max_bytes: int = MAX_RESPONSE_BYTES) -> object:
    if len(raw) > max_bytes:
        raise ConsoleToolsError(OVERSIZED_RESPONSE_MESSAGE)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ConsoleToolsError(MALFORMED_RESPONSE_MESSAGE) from error
    try:
        payload = json.loads(
            text,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ConsoleToolsError(MALFORMED_RESPONSE_MESSAGE)
            ),
        )
    except ConsoleToolsError:
        raise
    except RecursionError as error:
        raise ConsoleToolsError(MALFORMED_RESPONSE_MESSAGE) from error
    except MemoryError as error:
        raise ConsoleToolsError(MALFORMED_RESPONSE_MESSAGE) from error
    except ValueError as error:
        raise ConsoleToolsError(MALFORMED_RESPONSE_MESSAGE) from error
    try:
        _count_json_nodes(payload, depth=0, counter=[0])
    except RecursionError as error:
        raise ConsoleToolsError(MALFORMED_RESPONSE_MESSAGE) from error
    return payload


class _RefuseRedirectHandler(HTTPRedirectHandler):
    """Refuse every redirect; Console tools never follow Location headers."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        del req, fp, code, msg, headers, newurl
        raise ConsoleToolsError(HTTP_ERROR_MESSAGE)


def build_console_opener():
    """Return a urlopen-compatible opener that never follows redirects."""

    return build_opener(_RefuseRedirectHandler)


def post_tool_call(
    console_url: str,
    name: str,
    arguments: dict[str, Any],
    *,
    opener: Callable[..., object] | None = None,
) -> object:
    """POST {name, arguments} to Console /api/tools/call."""

    target = parse_console_url(console_url) + "/api/tools/call"
    body = json.dumps(
        {"name": name, "arguments": arguments},
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:
        raise ConsoleToolsError("tool call payload is too large")
    request = Request(
        target,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    open_url = opener or build_console_opener().open
    try:
        with open_url(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            content_type = ""
            try:
                content_type = response.headers.get("Content-Type", "")
            except Exception:  # noqa: BLE001
                content_type = ""
    except ConsoleToolsError:
        raise
    except urllib.error.HTTPError as error:
        try:
            error.read(MAX_RESPONSE_BYTES)
        except Exception:  # noqa: BLE001
            pass
        raise ConsoleToolsError(HTTP_ERROR_MESSAGE) from error
    except urllib.error.URLError as error:
        raise ConsoleConnectionError(CONSOLE_ABSENT_MESSAGE) from error
    except TimeoutError as error:
        raise ConsoleConnectionError(CONSOLE_ABSENT_MESSAGE) from error
    except OSError as error:
        raise ConsoleConnectionError(CONSOLE_ABSENT_MESSAGE) from error

    if len(raw) > MAX_RESPONSE_BYTES:
        raise ConsoleToolsError(OVERSIZED_RESPONSE_MESSAGE)
    if "json" not in content_type.lower():
        # Some test doubles omit Content-Type; still require parseable JSON.
        pass
    payload = parse_json_bytes(raw)
    if type(payload) is not dict:
        raise ConsoleToolsError(MALFORMED_RESPONSE_MESSAGE)
    if "result" in payload:
        return payload["result"]
    return payload


def iter_bounded_stdin_lines(
    stream_in, *, max_bytes: int = MAX_STDIN_LINE_BYTES
) -> Iterator[str]:
    """Yield newline-delimited stdin text, discarding oversized lines safely."""

    buffer = ""
    while True:
        chunk = stream_in.read(4096)
        if chunk == "" or chunk is None:
            if buffer:
                encoded = buffer.encode("utf-8", errors="surrogatepass")
                if len(encoded) <= max_bytes:
                    yield buffer
            break
        if not isinstance(chunk, str):
            try:
                chunk = chunk.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                continue
        buffer += chunk
        while True:
            newline_at = buffer.find("\n")
            if newline_at < 0:
                encoded = buffer.encode("utf-8", errors="surrogatepass")
                if len(encoded) > max_bytes:
                    buffer = ""
                break
            line = buffer[:newline_at]
            buffer = buffer[newline_at + 1 :]
            encoded = line.encode("utf-8", errors="surrogatepass")
            if len(encoded) > max_bytes:
                continue
            yield line


class ConsoleToolsServer:
    """Line-delimited MCP server backed by the shared console tool contract."""

    def __init__(
        self,
        contract,
        *,
        console_url: str | None = None,
        discovery_file: Path | None = None,
        post_call: Callable[[str, str, dict[str, Any]], object] | None = None,
    ) -> None:
        self.contract = contract
        self.console_url = (
            parse_console_url(console_url) if console_url is not None else None
        )
        self.discovery_file = (
            Path(discovery_file) if discovery_file is not None else None
        )
        self._post_call = post_call

    def tools(self) -> list[dict[str, Any]]:
        return self.contract.tool_manifest()

    def _console_candidates(self) -> list[str]:
        if self.console_url is not None:
            return [self.console_url]
        marker = self.discovery_file
        if marker is None:
            marker = Path(self.contract.default_console_address_file())
        return console_candidates(None, marker)

    def _post_to_console(
        self,
        console_url: str,
        name: str,
        arguments: dict[str, Any],
    ) -> object:
        if self._post_call is None:
            return post_tool_call(console_url, name, arguments)
        try:
            return self._post_call(console_url, name, arguments)
        except ConsoleToolsError:
            raise
        except urllib.error.HTTPError as error:
            raise ConsoleToolsError(HTTP_ERROR_MESSAGE) from error
        except urllib.error.URLError as error:
            raise ConsoleConnectionError(CONSOLE_ABSENT_MESSAGE) from error
        except TimeoutError as error:
            raise ConsoleConnectionError(CONSOLE_ABSENT_MESSAGE) from error
        except OSError as error:
            raise ConsoleConnectionError(CONSOLE_ABSENT_MESSAGE) from error

    def call_tool(self, name: str, arguments: object) -> tuple[str, bool]:
        try:
            normalized = self.contract.validate_tool_call(name, arguments)
        except self.contract.ToolContractError as error:
            return str(error), True
        try:
            connection_error: ConsoleConnectionError | None = None
            for candidate in self._console_candidates():
                try:
                    payload = self._post_to_console(candidate, name, normalized)
                except ConsoleConnectionError as error:
                    connection_error = error
                    continue
                break
            else:
                if connection_error is None:  # pragma: no cover
                    raise ConsoleConnectionError(CONSOLE_ABSENT_MESSAGE)
                raise connection_error
            projected = self.contract.project_tool_result(name, payload)
        except ConsoleToolsError as error:
            return str(error), True
        except self.contract.ToolContractError as error:
            return str(error), True
        except Exception:  # noqa: BLE001
            return MALFORMED_RESPONSE_MESSAGE, True
        return json.dumps(projected, separators=(",", ":"), ensure_ascii=True), False

    def handle_message(self, message: dict[str, object]) -> dict[str, object] | None:
        method = message.get("method")
        if not isinstance(method, str):
            return None
        if method.startswith("notifications/"):
            return None
        identifier = message.get("id")
        reply_id = identifier if isinstance(identifier, (int, str)) else 0
        if method == "initialize":
            result: dict[str, object] = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        elif method == "tools/list":
            result = {"tools": self.tools()}
        elif method == "tools/call":
            params = message.get("params")
            name = ""
            arguments: object = {}
            if isinstance(params, dict):
                raw_name = params.get("name")
                name = raw_name if isinstance(raw_name, str) else ""
                raw_arguments = params.get("arguments")
                if raw_arguments is not None:
                    arguments = raw_arguments
            text, is_error = self.call_tool(name, arguments)
            result = {
                "content": [{"type": "text", "text": text}],
                "isError": is_error,
            }
        elif method == "ping":
            result = {}
        else:
            return {
                "jsonrpc": "2.0",
                "id": reply_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        return {"jsonrpc": "2.0", "id": reply_id, "result": result}


def serve(
    stream_in=None,
    stream_out=None,
    *,
    contract=None,
    console_url: str | None = None,
    discovery_file: Path | None = None,
    post_call: Callable[[str, str, dict[str, Any]], object] | None = None,
) -> int:
    """Serve MCP requests until stdin closes."""

    if contract is None:
        raise ConsoleToolsError("contract module is required")
    server = ConsoleToolsServer(
        contract,
        console_url=console_url,
        discovery_file=discovery_file,
        post_call=post_call,
    )
    input_stream = stream_in if stream_in is not None else sys.stdin
    output_stream = stream_out if stream_out is not None else sys.stdout
    for line in iter_bounded_stdin_lines(input_stream):
        line = line.strip()
        if not line:
            continue
        try:
            message = parse_json_bytes(
                line.encode("utf-8"),
                max_bytes=MAX_STDIN_LINE_BYTES,
            )
        except ConsoleToolsError:
            continue
        if not isinstance(message, dict):
            continue
        reply = server.handle_message(message)
        if reply is None:
            continue
        output_stream.write(json.dumps(reply, separators=(",", ":")) + "\n")
        output_stream.flush()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airlock-console-tools",
        description="Airlock Console MCP tools over stdio.",
    )
    parser.add_argument(
        "--contract",
        required=True,
        help="Path to airlock_console_tools.py",
    )
    parser.add_argument(
        "--console-url",
        default=None,
        help=(
            "Exact http://127.0.0.1:PORT Console base URL; disables discovery "
            "and fallback"
        ),
    )
    parser.add_argument(
        "--discovery-file",
        default=None,
        help="Path to a console-address.json discovery marker",
    )
    return parser


def main(
    argv: list[str] | None = None,
    stream_in=None,
    stream_out=None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        console_url = (
            parse_console_url(args.console_url)
            if args.console_url is not None
            else None
        )
        contract = load_contract(Path(args.contract))
    except ConsoleToolsError as error:
        sys.stderr.write(str(error) + "\n")
        return 2
    return serve(
        stream_in=stream_in,
        stream_out=stream_out,
        contract=contract,
        console_url=console_url,
        discovery_file=(
            Path(args.discovery_file) if args.discovery_file is not None else None
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
