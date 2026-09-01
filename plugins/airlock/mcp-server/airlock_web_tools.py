#!/usr/bin/env python3
"""Airlock web tools: a local stdio MCP server with web_search and web_fetch.

Built-in WebSearch and WebFetch execute on Anthropic's API, so a GPT, Grok, or
OpenRouter root cannot use them. This server gives every Airlock profile the
same abilities through two ordinary tools:

- ``web_search`` searches DuckDuckGo's HTML endpoint and returns ranked links
  with short descriptions.
- ``web_fetch`` reads one public web page and returns its readable text, so
  the calling model summarizes the page in context instead of relying on a
  separate background model. The tool is registered under the name
  ``fetch_page``.

The server is standard-library only and speaks newline-delimited JSON-RPC 2.0
on stdin and stdout, which is the transport Claude Code uses for stdio MCP
servers. It makes outbound requests to the public web only, refuses private
network addresses, caps response sizes and times, and keeps no state.

DuckDuckGo does not publish a free general-results API. Like the popular
``ddgs`` package, this module queries the HTML endpoint directly, so a markup
change there can break ``web_search`` until it is updated.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import sys
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
    urlopen,
)

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "airlock-web-tools"
SERVER_VERSION = "0.1.0"

USER_AGENT = "Mozilla/5.0 (compatible; AirlockWebTools/0.1; +https://github.com/Harshkamdar67/Airlock)"
CONNECT_TIMEOUT_SECONDS = 20
MAX_SEARCH_RESULTS = 12
DEFAULT_SEARCH_RESULTS = 6
MAX_FETCH_BYTES = 2 * 1024 * 1024
DEFAULT_FETCH_CHARS = 20000
MAX_FETCH_CHARS = 100000

SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"

TEXT_CONTENT_PREFIXES = ("text/",)
JSON_CONTENT_TYPES = ("application/json", "application/xml", "application/xhtml+xml")


class ToolError(Exception):
    """A tool failure that should reach the model as a readable message."""


def decode_duckduckgo_link(url: str) -> str:
    """Resolve a DuckDuckGo redirect link to the real target URL."""

    if url.startswith("//"):
        url = "https:" + url
    parts = urlsplit(url)
    if parts.hostname and parts.hostname.endswith("duckduckgo.com") and parts.path.startswith("/l/"):
        targets = parse_qs(parts.query).get("uddg")
        if targets:
            return targets[0]
    return url


class DuckDuckGoResultParser(HTMLParser):
    """Collect ranked result titles, URLs, and snippets from the HTML page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._href = ""

    @staticmethod
    def _classes(attributes: list[tuple[str, str | None]]) -> set[str]:
        for name, value in attributes:
            if name == "class" and value:
                return set(value.split())
        return set()

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        classes = self._classes(attributes)
        if tag == "a" and "result__a" in classes:
            href = dict(attributes).get("href", "")
            self._capture = "title"
            self._buffer = []
            self._href = href
        elif tag == "a" and "result__snippet" in classes and self.results:
            self._capture = "snippet"
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._capture or tag != "a":
            return
        text = " ".join("".join(self._buffer).split())
        if self._capture == "title" and text and self._href:
            self.results.append({
                "title": text,
                "url": decode_duckduckgo_link(self._href),
            })
        elif self._capture == "snippet" and text and self.results:
            self.results[-1].setdefault("description", text)
        self._capture = None
        self._buffer = []


def parse_search_results(html_text: str, limit: int) -> list[dict[str, str]]:
    parser = DuckDuckGoResultParser()
    try:
        parser.feed(html_text)
    except Exception as error:  # noqa: BLE001 - malformed upstream markup
        raise ToolError(f"could not parse the search results page: {error}") from error
    return parser.results[:limit]


def http_post_form(url: str, fields: dict[str, str]) -> tuple[int, str, str]:
    """POST form fields and return (status, content type, decoded body)."""

    request = Request(
        url,
        data=urlencode(fields).encode("utf-8"),
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html",
        },
        method="POST",
    )
    with urlopen(request, timeout=CONNECT_TIMEOUT_SECONDS) as response:
        status = response.status
        content_type = response.headers.get("Content-Type", "")
        body = response.read(MAX_FETCH_BYTES + 1)
    return status, content_type, body.decode("utf-8", errors="replace")


def assert_public_url(url: str) -> None:
    """Refuse anything but public http(s) addresses, including redirects."""

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ToolError("only http and https URLs are allowed")
    host = parts.hostname
    if not host:
        raise ToolError(f"URL has no host: {url}")
    try:
        addresses = socket.getaddrinfo(host, None)
    except OSError as error:
        raise ToolError(f"could not resolve {host}: {error}") from error
    for info in addresses:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ToolError(
                f"{host} resolves to a private network address, which is refused"
            )


class PublicRedirectHandler(HTTPRedirectHandler):
    """Re-run the private-address refusal on every redirect hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        assert_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


BLOCK_TAGS = frozenset((
    "p", "div", "br", "li", "tr", "section", "article", "header", "footer",
    "table", "pre", "blockquote", "ul", "ol", "h1", "h2", "h3", "h4", "h5",
    "h6", "form", "figure", "figcaption", "main", "aside", "dl", "dd", "dt",
))
SKIP_TAGS = frozenset(("script", "style", "template", "noscript", "svg"))
# "head" stays readable on purpose: the only text a head normally carries is
# the page title, which fetch_page reports. Style and script skip themselves.


class ReadableTextExtractor(HTMLParser):
    """Turn an HTML page into plain readable text without dependencies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.lines: list[str] = []
        self._line: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def flush_line(self) -> None:
        text = " ".join("".join(self._line).split())
        self._line = []
        if text:
            self.lines.append(text)

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
        elif tag in BLOCK_TAGS:
            self.flush_line()

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
        elif tag in BLOCK_TAGS:
            self.flush_line()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
        else:
            self._line.append(data)


def html_to_readable_text(html_text: str) -> tuple[str, str]:
    extractor = ReadableTextExtractor()
    try:
        extractor.feed(html_text)
    except Exception as error:  # noqa: BLE001 - tolerate broken markup
        raise ToolError(f"could not read the page markup: {error}") from error
    extractor.flush_line()
    title = " ".join(extractor.title.split())
    return title, "\n".join(extractor.lines)


def http_get(url: str) -> tuple[int, str, bytes]:
    """GET a URL with redirect checks and a hard byte cap."""

    assert_public_url(url)
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html, text/*;q=0.9, */*;q=0.1"},
    )
    opener = build_opener(PublicRedirectHandler())
    try:
        with opener.open(request, timeout=CONNECT_TIMEOUT_SECONDS) as response:
            status = response.status
            content_type = response.headers.get("Content-Type", "")
            body = response.read(MAX_FETCH_BYTES + 1)
    except ToolError:
        raise
    except Exception as error:  # noqa: BLE001 - surface a readable reason
        raise ToolError(f"request failed: {error}") from error
    return status, content_type, body


def run_web_search(arguments: dict[str, object]) -> str:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ToolError("web_search needs a non-empty query string")
    raw_limit = arguments.get("max_results", DEFAULT_SEARCH_RESULTS)
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
        raise ToolError("max_results must be a whole number")
    limit = max(1, min(int(raw_limit), MAX_SEARCH_RESULTS))
    try:
        _, content_type, body = http_post_form(SEARCH_ENDPOINT, {"q": query.strip()})
    except Exception as error:  # noqa: BLE001
        raise ToolError(
            f"DuckDuckGo search request failed: {error}. "
            "The endpoint sometimes rate limits or blocks automated clients."
        ) from error
    if "text/html" not in content_type:
        raise ToolError(
            f"DuckDuckGo returned an unexpected content type: {content_type or 'none'}"
        )
    results = parse_search_results(body, limit)
    if not results:
        raise ToolError(
            "the search results page contained no results, either because the "
            "query found nothing or because DuckDuckGo changed its markup"
        )
    lines = [f"Results for: {query.strip()}", ""]
    for index, result in enumerate(results, start=1):
        lines.append(f"{index}. {result['title']}")
        lines.append(f"   {result['url']}")
        if result.get("description"):
            lines.append(f"   {result['description']}")
    lines.append("")
    lines.append("Read any of these with the fetch_page tool.")
    return "\n".join(lines)


def run_fetch_page(arguments: dict[str, object]) -> str:
    url = arguments.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ToolError("fetch_page needs a non-empty URL string")
    url = url.strip()
    raw_chars = arguments.get("max_chars", DEFAULT_FETCH_CHARS)
    if isinstance(raw_chars, bool) or not isinstance(raw_chars, int):
        raise ToolError("max_chars must be a whole number")
    max_chars = max(200, min(int(raw_chars), MAX_FETCH_CHARS))

    status, content_type, body = http_get(url)
    lowered = content_type.split(";")[0].strip().lower()
    allowed = lowered.startswith(TEXT_CONTENT_PREFIXES) or lowered in JSON_CONTENT_TYPES
    if not allowed:
        return (
            f"{url} returned status {status} with content type {lowered or 'unknown'}, "
            f"and {len(body)} bytes were available before the cap. Binary and "
            "unsupported content is not converted."
        )
    truncated_bytes = len(body) > MAX_FETCH_BYTES
    text_body = body.decode("utf-8", errors="replace")
    title, readable = html_to_readable_text(text_body)
    note = f"Fetched {url} (status {status})."
    if title:
        note += f" Page title: {title}"
    output_lines = [note, ""]
    if readable:
        if len(readable) > max_chars:
            readable = readable[:max_chars] + "\n[truncated]"
        output_lines.append(readable)
    else:
        output_lines.append("(no readable text was found in the page)")
    if truncated_bytes:
        output_lines.append("[response was cut off at the size cap]")
    return "\n".join(output_lines)


TOOLS = [
    {
        "name": "web_search",
        "description": (
            "Search the public web with DuckDuckGo and get ranked results with "
            "titles, URLs, and short descriptions. Use fetch_page to read one."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_SEARCH_RESULTS,
                    "default": DEFAULT_SEARCH_RESULTS,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_page",
        "description": (
            "Fetch one public web page over http(s) and return its readable "
            "text, so you can summarize it in context. Refuses private network "
            "addresses and binary content."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The http(s) URL to read."},
                "max_chars": {
                    "type": "integer",
                    "minimum": 200,
                    "maximum": MAX_FETCH_CHARS,
                    "default": DEFAULT_FETCH_CHARS,
                },
            },
            "required": ["url"],
        },
    },
]


def dispatch_tool(name: str, arguments: dict[str, object]) -> tuple[str, bool]:
    try:
        if name == "web_search":
            return run_web_search(arguments), False
        if name == "fetch_page":
            return run_fetch_page(arguments), False
        return f"Unknown tool: {name}", True
    except ToolError as error:
        return str(error), True


def handle_message(message: dict[str, object]) -> dict[str, object] | None:
    method = message.get("method")
    if not isinstance(method, str):
        return None
    if method.startswith("notifications/"):
        return None
    identifier = message.get("id")
    reply_id = identifier if isinstance(identifier, (int, str)) else 0
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params")
        arguments: dict[str, object] = {}
        name = ""
        if isinstance(params, dict):
            raw_name = params.get("name")
            name = raw_name if isinstance(raw_name, str) else ""
            raw_arguments = params.get("arguments")
            if isinstance(raw_arguments, dict):
                arguments = raw_arguments
        text, is_error = dispatch_tool(name, arguments)
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


def serve(stream_in=None, stream_out=None) -> int:
    """Serve MCP requests until stdin closes. Returns a process exit code."""

    input_stream = stream_in if stream_in is not None else sys.stdin
    output_stream = stream_out if stream_out is not None else sys.stdout
    for line in input_stream:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        reply = handle_message(message)
        if reply is None:
            continue
        output_stream.write(json.dumps(reply, separators=(",", ":")) + "\n")
        output_stream.flush()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
