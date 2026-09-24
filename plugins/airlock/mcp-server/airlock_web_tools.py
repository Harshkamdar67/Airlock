#!/usr/bin/env python3
"""Airlock web tools: a local stdio MCP server with web_search and web_fetch.

Built-in WebSearch and WebFetch execute on Anthropic's API, so a GPT, Grok, or
OpenRouter root cannot use them. This server gives every Airlock profile the
same abilities through two ordinary tools:

- ``web_search`` runs one query, or up to five in parallel, against one or
  more search backends, merges and de-duplicates the links, and returns them
  with short descriptions. It can restrict results to a recent time window.
- ``web_fetch`` reads one public web page and returns its readable text as
  light markdown, so the calling model summarizes the page in context instead
  of relying on a separate background model. It can page through long
  documents and check whether exact phrases appear on a page. The tool is
  registered under the name ``fetch_page``.

Search backends:

- ``duckduckgo`` (default) queries DuckDuckGo's HTML endpoint.
- ``wikipedia`` queries the official MediaWiki search API.
- ``searxng`` queries the JSON API of a SearXNG instance the user runs and
  names in ``AIRLOCK_WEB_SEARXNG_URL``.
- ``ddgs`` runs the third-party ``ddgs`` metasearch package in a separate
  Python interpreter the user installed it into and names in
  ``AIRLOCK_WEB_DDGS_PYTHON``. Only its search functions are used; pages are
  always read by this server's own guarded fetcher.

``AIRLOCK_WEB_SEARCH_BACKENDS`` sets the default backend list. The server is
standard-library only and speaks newline-delimited JSON-RPC 2.0 on stdin and
stdout, which is the transport Claude Code uses for stdio MCP servers. Tool
calls run on a small thread pool, so parallel workers sharing one session do
not queue behind each other. Outbound requests go to the public web only:
every connection is made to an address that was checked at connect time, so a
host cannot pass the check with one address and connect with another.
Responses are capped in size and time, and a short in-memory cache avoids
repeating identical requests within one session.

DuckDuckGo does not publish a free general-results API. Like the popular
``ddgs`` package, the default backend queries the HTML endpoint directly, so
a markup change there can break it until it is updated.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
)

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "airlock-web-tools"
SERVER_VERSION = "0.2.0"

USER_AGENT = "Mozilla/5.0 (compatible; AirlockWebTools/0.2; +https://github.com/Harshkamdar67/Airlock)"
CONNECT_TIMEOUT_SECONDS = 20
MAX_SEARCH_RESULTS = 12
DEFAULT_SEARCH_RESULTS = 6
MAX_QUERIES = 5
MAX_PAGE = 5
MAX_FETCH_BYTES = 2 * 1024 * 1024
DEFAULT_FETCH_CHARS = 20000
MAX_FETCH_CHARS = 100000
MAX_FIND_PHRASES = 5
MAX_FIND_PHRASE_CHARS = 500
FIND_CONTEXT_CHARS = 200
TOOL_WORKERS = 8
SEARCH_WORKERS = 8
CACHE_TTL_SECONDS = 600
CACHE_MAX_ENTRIES = 64
DDGS_TIMEOUT_SECONDS = 45

SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
WIKIPEDIA_ENDPOINT = "https://{lang}.wikipedia.org/w/api.php"

TEXT_CONTENT_PREFIXES = ("text/",)
JSON_CONTENT_TYPES = ("application/json", "application/xml", "application/xhtml+xml")
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

BACKENDS = ("duckduckgo", "wikipedia", "searxng", "ddgs")
ALWAYS_AVAILABLE_BACKENDS = ("duckduckgo", "wikipedia")
DEFAULT_BACKENDS = ("duckduckgo",)
NEWS_BACKENDS = ("searxng", "ddgs")
CATEGORIES = ("text", "news")
TIMELIMITS = {"d": "day", "w": "week", "m": "month", "y": "year"}
REGION_PATTERN = re.compile(r"^[a-z]{2}-[a-z]{2}$")
DDGS_ENGINE_PATTERN = re.compile(r"^[a-z]+(,[a-z]+)*$")

# Minimum spacing between requests to one backend. A worker swarm shares
# this server, and DuckDuckGo blocks clients that fire bursts of searches.
BACKEND_MIN_INTERVAL_SECONDS = {
    "duckduckgo": 1.0,
    "wikipedia": 0.2,
    "searxng": 0.0,
    "ddgs": 0.5,
}

# The ddgs backend runs in the user's own interpreter so its compiled
# dependencies never load into this process. It receives one JSON request on
# stdin, calls only the search functions, and prints one JSON reply.
DDGS_HELPER = r"""
import json, sys
request = json.loads(sys.stdin.read())
try:
    from ddgs import DDGS
except Exception as error:
    print(json.dumps({"error": "ddgs is not importable: %s" % error}))
    sys.exit(0)
try:
    client = DDGS(timeout=request["timeout"])
    method = client.news if request["category"] == "news" else client.text
    kwargs = {
        "region": request["region"],
        "safesearch": "moderate",
        "timelimit": request["timelimit"],
        "max_results": request["max_results"],
        "page": request["page"],
        "backend": request["backend"],
    }
    results = method(request["query"], **kwargs)
    print(json.dumps({"results": results}, default=str))
except Exception as error:
    print(json.dumps({"error": "%s: %s" % (type(error).__name__, error)}))
"""


class ToolError(Exception):
    """A tool failure that should reach the model as a readable message."""


# ---------------------------------------------------------------------------
# Settings


class Settings:
    """Backend configuration read from the environment at call time."""

    def __init__(
        self,
        backends: tuple[str, ...],
        searxng_url: str | None,
        ddgs_python: str | None,
        ddgs_engines: str,
    ) -> None:
        self.backends = backends
        self.searxng_url = searxng_url
        self.ddgs_python = ddgs_python
        self.ddgs_engines = ddgs_engines

    def available(self) -> tuple[str, ...]:
        names = list(ALWAYS_AVAILABLE_BACKENDS)
        if self.searxng_url:
            names.append("searxng")
        if self.ddgs_python:
            names.append("ddgs")
        return tuple(name for name in BACKENDS if name in names)


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    """Read backend settings. Invalid values are ignored, never fatal."""

    env = os.environ if environ is None else environ

    searxng_url = None
    raw_searxng = env.get("AIRLOCK_WEB_SEARXNG_URL", "").strip()
    if raw_searxng:
        parts = urlsplit(raw_searxng)
        if parts.scheme in ("http", "https") and parts.hostname:
            searxng_url = raw_searxng.rstrip("/")

    ddgs_python = None
    raw_python = env.get("AIRLOCK_WEB_DDGS_PYTHON", "").strip()
    if raw_python and os.path.isabs(raw_python) and os.path.isfile(raw_python):
        ddgs_python = raw_python

    ddgs_engines = "auto"
    raw_engines = env.get("AIRLOCK_WEB_DDGS_ENGINES", "").strip().lower().replace(" ", "")
    if raw_engines and DDGS_ENGINE_PATTERN.match(raw_engines):
        ddgs_engines = raw_engines

    settings = Settings(DEFAULT_BACKENDS, searxng_url, ddgs_python, ddgs_engines)
    raw_backends = env.get("AIRLOCK_WEB_SEARCH_BACKENDS", "").strip().lower()
    if raw_backends:
        chosen = []
        for name in raw_backends.replace(" ", "").split(","):
            if name in settings.available() and name not in chosen:
                chosen.append(name)
        if chosen:
            settings.backends = tuple(chosen)
    return settings


# ---------------------------------------------------------------------------
# Address checks and pinned connections


def address_is_refused(raw_address: str) -> bool:
    """True for any address that is not a public unicast internet address."""

    address = ipaddress.ip_address(raw_address.split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return (
        not address.is_global
        or address.is_multicast
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_private
        or address.is_reserved
    )


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
        if address_is_refused(info[4][0]):
            raise ToolError(
                f"{host} resolves to a private network address, which is refused"
            )


def connect_to_public_address(
    address: tuple[str, int],
    timeout: object = socket._GLOBAL_DEFAULT_TIMEOUT,  # noqa: SLF001
    source_address: tuple[str, int] | None = None,
) -> socket.socket:
    """Resolve, check, and connect to the checked address in one step.

    Checking a host name and then letting the HTTP library resolve it again
    leaves a window in which a hostile DNS server can answer the second
    lookup with a private address. Connecting to the exact address that was
    checked closes that window.
    """

    host, port = address
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except OSError as error:
        raise ToolError(f"could not resolve {host}: {error}") from error
    if not infos:
        raise ToolError(f"could not resolve {host}")
    for info in infos:
        if address_is_refused(info[4][0]):
            raise ToolError(
                f"{host} resolves to a private network address, which is refused"
            )
    last_error: OSError | None = None
    for family, kind, proto, _, sockaddr in infos:
        sock = socket.socket(family, kind, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:  # noqa: SLF001
                sock.settimeout(timeout)  # type: ignore[arg-type]
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as error:
            last_error = error
            sock.close()
    raise last_error if last_error else OSError(f"could not connect to {host}")


class PinnedHTTPConnection(http.client.HTTPConnection):
    """An HTTP connection that only ever reaches a checked public address.

    When the request goes through a configured proxy, the connection's host
    is that proxy. A proxy is the user's own infrastructure, so it is reached
    normally, and the target was already checked by assert_public_url.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._create_connection = self._pinned_create_connection

    def _pinned_create_connection(self, address, timeout, source_address=None):  # noqa: ANN001
        if self._tunnel_host:
            return socket.create_connection(address, timeout, source_address)
        return connect_to_public_address(address, timeout, source_address)


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """The HTTPS counterpart; TLS still verifies the original host name."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._create_connection = self._pinned_create_connection

    def _pinned_create_connection(self, address, timeout, source_address=None):  # noqa: ANN001
        if self._tunnel_host:
            return socket.create_connection(address, timeout, source_address)
        return connect_to_public_address(address, timeout, source_address)


class PinnedHTTPHandler(HTTPHandler):
    def http_open(self, req):  # noqa: ANN001, ANN201
        if req.has_proxy():
            return self.do_open(http.client.HTTPConnection, req)
        return self.do_open(PinnedHTTPConnection, req)


class PinnedHTTPSHandler(HTTPSHandler):
    def https_open(self, req):  # noqa: ANN001, ANN201
        return self.do_open(PinnedHTTPSConnection, req, context=self._context)


class PublicRedirectHandler(HTTPRedirectHandler):
    """Re-run the private-address refusal on every redirect hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        assert_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_opener():  # noqa: ANN201
    return build_opener(
        PinnedHTTPHandler(),
        PinnedHTTPSHandler(context=ssl.create_default_context()),
        PublicRedirectHandler(),
    )


class Response:
    def __init__(self, status: int, content_type: str, body: bytes, final_url: str) -> None:
        self.status = status
        self.content_type = content_type
        self.body = body
        self.final_url = final_url


def http_request(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    trusted: bool = False,
) -> Response:
    """Send one request with a hard byte cap.

    ``trusted`` is only for an address the user configured, such as their
    own SearXNG instance, which may live on a private network. Addresses the
    model supplies are never trusted.
    """

    if not trusted:
        assert_public_url(url)
    request_headers = {"User-Agent": USER_AGENT}
    request_headers.update(headers or {})
    request = Request(
        url,
        data=data,
        headers=request_headers,
        method="POST" if data is not None else "GET",
    )
    opener = build_opener() if trusted else public_opener()
    try:
        with opener.open(request, timeout=CONNECT_TIMEOUT_SECONDS) as response:
            return Response(
                response.status,
                response.headers.get("Content-Type", ""),
                response.read(MAX_FETCH_BYTES + 1),
                response.geturl(),
            )
    except HTTPError as error:
        try:
            body = error.read(MAX_FETCH_BYTES + 1)
        except Exception:  # noqa: BLE001 - an unreadable error body is empty
            body = b""
        return Response(
            error.code,
            error.headers.get("Content-Type", "") if error.headers else "",
            body,
            error.geturl() or url,
        )
    except ToolError:
        raise
    except Exception as error:  # noqa: BLE001 - surface a readable reason
        raise ToolError(f"request failed: {error}") from error


def http_post_form(url: str, fields: dict[str, str]) -> tuple[int, str, str]:
    """POST form fields and return (status, content type, decoded body)."""

    response = http_request(
        url,
        data=urlencode(fields).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html",
        },
    )
    return response.status, response.content_type, response.body.decode("utf-8", errors="replace")


def fetch_response(url: str) -> Response:
    """GET a public URL with redirect checks and a hard byte cap."""

    return http_request(
        url, headers={"Accept": "text/html, text/*;q=0.9, */*;q=0.1"}
    )


def http_get_json(url: str, *, trusted: bool = False) -> object:
    response = http_request(url, headers={"Accept": "application/json"}, trusted=trusted)
    if response.status != 200:
        raise ToolError(f"HTTP {response.status}")
    try:
        return json.loads(response.body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as error:
        raise ToolError("the reply was not JSON") from error


# ---------------------------------------------------------------------------
# Small shared state: cache and per-backend pacing


class TtlCache:
    def __init__(self, ttl: float = CACHE_TTL_SECONDS, max_entries: int = CACHE_MAX_ENTRIES) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        self._items: dict[object, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: object) -> object | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            stamp, value = item
            if time.monotonic() - stamp > self.ttl:
                del self._items[key]
                return None
            return value

    def put(self, key: object, value: object) -> None:
        with self._lock:
            if len(self._items) >= self.max_entries and key not in self._items:
                oldest = min(self._items, key=lambda k: self._items[k][0])
                del self._items[oldest]
            self._items[key] = (time.monotonic(), value)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


SEARCH_CACHE = TtlCache()
FETCH_CACHE = TtlCache(max_entries=16)

_PACE_LOCKS = {name: threading.Lock() for name in BACKENDS}
_LAST_REQUEST: dict[str, float] = {}


def pace(backend: str) -> None:
    """Hold a caller until the backend's minimum spacing has passed."""

    interval = BACKEND_MIN_INTERVAL_SECONDS.get(backend, 0.0)
    if interval <= 0:
        return
    with _PACE_LOCKS[backend]:
        wait = _LAST_REQUEST.get(backend, 0.0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _LAST_REQUEST[backend] = time.monotonic()


# ---------------------------------------------------------------------------
# Search backends


class SearchResult:
    def __init__(
        self,
        title: str,
        url: str,
        snippet: str = "",
        date: str = "",
        source: str = "",
    ) -> None:
        self.title = title
        self.url = url
        self.snippet = snippet
        self.date = date
        self.source = source


class SearchRequest:
    def __init__(
        self,
        query: str,
        limit: int,
        category: str,
        timelimit: str | None,
        region: str | None,
        page: int,
    ) -> None:
        self.query = query
        self.limit = limit
        self.category = category
        self.timelimit = timelimit
        self.region = region
        self.page = page

    def key(self, backend: str) -> tuple:
        return (backend, self.query, self.limit, self.category, self.timelimit, self.region, self.page)


class BackendSkipped(Exception):
    """The backend cannot serve this kind of request; not a failure."""


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
            self._href = href or ""
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
            url = decode_duckduckgo_link(self._href)
            # Sponsored links go through an ad redirect, not to a result.
            if not url.startswith("https://duckduckgo.com/y.js"):
                self.results.append({"title": text, "url": url})
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


def search_duckduckgo(request: SearchRequest, settings: Settings) -> list[SearchResult]:
    del settings
    if request.category != "text":
        raise BackendSkipped("duckduckgo serves text results only")
    fields = {"q": request.query, "b": ""}
    if request.region:
        fields["kl"] = request.region
    if request.timelimit:
        fields["df"] = request.timelimit
    if request.page > 1:
        fields["s"] = str(10 + (request.page - 2) * 15)
    pace("duckduckgo")
    try:
        status, content_type, body = http_post_form(SEARCH_ENDPOINT, fields)
    except Exception as error:  # noqa: BLE001
        raise ToolError(
            f"DuckDuckGo search request failed: {error}. "
            "The endpoint sometimes rate limits or blocks automated clients."
        ) from error
    if status in (202, 403, 429) or "anomaly-modal" in body:
        raise ToolError(
            "DuckDuckGo is refusing automated searches right now (status "
            f"{status}). Wait a minute, send fewer searches at once, or use "
            "another backend."
        )
    if "text/html" not in content_type:
        raise ToolError(
            f"DuckDuckGo returned an unexpected content type: {content_type or 'none'}"
        )
    parsed = parse_search_results(body, request.limit)
    if not parsed and "result__a" not in body and "No results" not in body and "no-results" not in body:
        raise ToolError(
            "the search results page contained no results, either because the "
            "query found nothing or because DuckDuckGo changed its markup"
        )
    return [
        SearchResult(item["title"], item["url"], item.get("description", ""), source="duckduckgo")
        for item in parsed
    ]


def strip_tags(text: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", text).replace("&quot;", '"').replace("&amp;", "&").split())


def search_wikipedia(request: SearchRequest, settings: Settings) -> list[SearchResult]:
    del settings
    if request.category != "text":
        raise BackendSkipped("wikipedia serves reference articles, not news")
    lang = (request.region or "us-en").split("-", 1)[1]
    if request.region == "wt-wt":  # "no region" has no Wikipedia of its own
        lang = "en"
    params = {
        "action": "query",
        "list": "search",
        "srsearch": request.query,
        "srlimit": str(request.limit),
        "sroffset": str((request.page - 1) * request.limit),
        "srprop": "snippet|timestamp",
        "format": "json",
        "utf8": "1",
    }
    pace("wikipedia")
    payload = http_get_json(WIKIPEDIA_ENDPOINT.format(lang=lang) + "?" + urlencode(params))
    hits = []
    if isinstance(payload, dict):
        query = payload.get("query")
        if isinstance(query, dict) and isinstance(query.get("search"), list):
            hits = query["search"]
    results = []
    for hit in hits:
        if not isinstance(hit, dict) or not isinstance(hit.get("title"), str):
            continue
        title = hit["title"]
        url = f"https://{lang}.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))
        edited = hit.get("timestamp") if isinstance(hit.get("timestamp"), str) else ""
        results.append(SearchResult(
            title,
            url,
            strip_tags(hit.get("snippet", "") if isinstance(hit.get("snippet"), str) else ""),
            f"edited {edited[:10]}" if edited else "",
            "wikipedia",
        ))
    return results


def search_searxng(request: SearchRequest, settings: Settings) -> list[SearchResult]:
    if not settings.searxng_url:
        raise BackendSkipped("no SearXNG instance is configured")
    params = {
        "q": request.query,
        "format": "json",
        "pageno": str(request.page),
        "categories": "news" if request.category == "news" else "general",
    }
    if request.timelimit:
        params["time_range"] = TIMELIMITS[request.timelimit]
    if request.region:
        params["language"] = request.region.split("-", 1)[1]
    pace("searxng")
    try:
        payload = http_get_json(settings.searxng_url + "/search?" + urlencode(params), trusted=True)
    except ToolError as error:
        raise ToolError(
            f"SearXNG request failed: {error}. The instance must allow the json "
            "format under search.formats in its settings."
        ) from error
    items = payload.get("results") if isinstance(payload, dict) else None
    results = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        title = item.get("title")
        if not isinstance(url, str) or not isinstance(title, str):
            continue
        engines = item.get("engines")
        engine_names = ",".join(e for e in engines if isinstance(e, str)) if isinstance(engines, list) else ""
        date = item.get("publishedDate")
        results.append(SearchResult(
            " ".join(title.split()),
            url,
            " ".join(str(item.get("content") or "").split()),
            date[:10] if isinstance(date, str) else "",
            f"searxng:{engine_names}" if engine_names else "searxng",
        ))
        if len(results) >= request.limit:
            break
    return results


def search_ddgs(request: SearchRequest, settings: Settings) -> list[SearchResult]:
    if not settings.ddgs_python:
        raise BackendSkipped("no ddgs interpreter is configured")
    job = {
        "query": request.query,
        "category": request.category,
        "region": request.region or "us-en",
        "timelimit": request.timelimit,
        "max_results": request.limit,
        "page": request.page,
        "backend": settings.ddgs_engines,
        "timeout": 10,
    }
    pace("ddgs")
    try:
        completed = subprocess.run(
            [settings.ddgs_python, "-I", "-c", DDGS_HELPER],
            input=json.dumps(job),
            capture_output=True,
            text=True,
            timeout=DDGS_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ToolError("ddgs search timed out") from error
    except OSError as error:
        raise ToolError(f"could not start the ddgs interpreter: {error}") from error
    try:
        reply = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise ToolError(f"ddgs exited with status {completed.returncode} and no reply") from error
    if not isinstance(reply, dict):
        raise ToolError("ddgs returned an unexpected reply")
    if isinstance(reply.get("error"), str):
        raise ToolError(f"ddgs search failed: {reply['error']}")
    items = reply.get("results")
    results = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        url = item.get("href") or item.get("url")
        title = item.get("title")
        if not isinstance(url, str) or not isinstance(title, str):
            continue
        date = item.get("date")
        publisher = item.get("source")
        source = "ddgs"
        if isinstance(publisher, str) and publisher.strip():
            source = f"ddgs:{publisher.strip()}"
        results.append(SearchResult(
            " ".join(title.split()),
            url,
            " ".join(str(item.get("body") or "").split()),
            date[:10] if isinstance(date, str) else "",
            source,
        ))
    return results[: request.limit]


SEARCH_BACKENDS = {
    "duckduckgo": search_duckduckgo,
    "wikipedia": search_wikipedia,
    "searxng": search_searxng,
    "ddgs": search_ddgs,
}


def normalize_url(url: str) -> str:
    """A comparison key that treats trivially different links as one."""

    parts = urlsplit(url.strip())
    query = "&".join(
        piece for piece in parts.query.split("&")
        if piece and not piece.lower().startswith(("utm_", "fbclid=", "gclid="))
    )
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    # http and https copies of one page are the same result.
    return urlunsplit(("https", host, parts.path.rstrip("/") or "/", query, ""))


def run_backend(name: str, request: SearchRequest, settings: Settings) -> list[SearchResult]:
    key = request.key(name)
    cached = SEARCH_CACHE.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]
    results = SEARCH_BACKENDS[name](request, settings)
    SEARCH_CACHE.put(key, results)
    return results


def merge_results(per_backend: list[list[SearchResult]], limit: int, seen: set[str]) -> list[SearchResult]:
    """Interleave backends in rank order and drop links already shown."""

    merged: list[SearchResult] = []
    depth = max((len(results) for results in per_backend), default=0)
    for rank in range(depth):
        for results in per_backend:
            if rank >= len(results) or len(merged) >= limit:
                continue
            result = results[rank]
            key = normalize_url(result.url)
            if key in seen:
                continue
            seen.add(key)
            merged.append(result)
    return merged


def _optional_int(arguments: dict[str, object], name: str, default: int, low: int, high: int) -> int:
    raw = arguments.get(name, default)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ToolError(f"{name} must be a whole number")
    return max(low, min(int(raw), high))


def _query_list(arguments: dict[str, object]) -> list[str]:
    queries: list[str] = []
    single = arguments.get("query")
    if single is not None:
        if not isinstance(single, str):
            raise ToolError("query must be a string")
        if single.strip():
            queries.append(single.strip())
    many = arguments.get("queries")
    if many is not None:
        if not isinstance(many, list) or not all(isinstance(q, str) for q in many):
            raise ToolError("queries must be a list of strings")
        queries.extend(q.strip() for q in many if q.strip())
    unique: list[str] = []
    for query in queries:
        if query not in unique:
            unique.append(query)
    if not unique:
        raise ToolError("web_search needs a non-empty query string or a queries list")
    if len(unique) > MAX_QUERIES:
        raise ToolError(f"web_search takes at most {MAX_QUERIES} queries per call")
    return unique


def run_web_search(arguments: dict[str, object], settings: Settings | None = None) -> str:
    settings = settings or load_settings()
    queries = _query_list(arguments)
    limit = _optional_int(arguments, "max_results", DEFAULT_SEARCH_RESULTS, 1, MAX_SEARCH_RESULTS)
    page = _optional_int(arguments, "page", 1, 1, MAX_PAGE)

    category = arguments.get("category", "text")
    if category not in CATEGORIES:
        raise ToolError("category must be text or news")
    timelimit = arguments.get("timelimit")
    if timelimit is not None and timelimit not in TIMELIMITS:
        raise ToolError("timelimit must be d, w, m, or y (past day, week, month, or year)")
    region = arguments.get("region")
    if region is not None and (not isinstance(region, str) or not REGION_PATTERN.match(region.lower())):
        raise ToolError("region must look like us-en, uk-en, de-de, or wt-wt")
    region = region.lower() if isinstance(region, str) else None

    available = settings.available()
    requested = arguments.get("backends")
    if requested is None:
        backends = [name for name in settings.backends if name in available]
        if category == "news":
            backends = [name for name in available if name in NEWS_BACKENDS]
    else:
        if not isinstance(requested, list) or not all(isinstance(b, str) for b in requested) or not requested:
            raise ToolError("backends must be a non-empty list of backend names")
        backends = []
        for name in requested:
            if name not in available:
                raise ToolError(
                    f"backend {name!r} is not available here. Available: {', '.join(available)}"
                )
            if name not in backends:
                backends.append(name)
    if not backends:
        raise ToolError(
            "news search needs a SearXNG instance (AIRLOCK_WEB_SEARXNG_URL) or the "
            "ddgs backend (AIRLOCK_WEB_DDGS_PYTHON). Use category text with a "
            "timelimit instead."
        )

    requests = [
        SearchRequest(query, limit, str(category), timelimit if isinstance(timelimit, str) else None, region, page)
        for query in queries
    ]
    jobs = [(q_index, name) for q_index in range(len(requests)) for name in backends]
    outcomes: dict[tuple[int, str], object] = {}

    def work(job: tuple[int, str]) -> tuple[tuple[int, str], object]:
        q_index, name = job
        try:
            return job, run_backend(name, requests[q_index], settings)
        except (ToolError, BackendSkipped) as error:
            return job, error

    if len(jobs) == 1:
        key, value = work(jobs[0])
        outcomes[key] = value
    else:
        with ThreadPoolExecutor(max_workers=min(SEARCH_WORKERS, len(jobs))) as pool:
            for key, value in pool.map(work, jobs):
                outcomes[key] = value

    failures = [
        (queries[q], name, value) for (q, name), value in outcomes.items()
        if isinstance(value, ToolError)
    ]
    if all(isinstance(value, (ToolError, BackendSkipped)) for value in outcomes.values()):
        messages = sorted({str(value) for value in outcomes.values()})
        raise ToolError("; ".join(messages))

    lines: list[str] = []
    seen: set[str] = set()
    for q_index, query in enumerate(queries):
        per_backend = [
            outcomes[(q_index, name)] for name in backends
            if isinstance(outcomes.get((q_index, name)), list)
        ]
        merged = merge_results(per_backend, limit, seen)  # type: ignore[arg-type]
        if lines:
            lines.append("")
        lines.append(f"Results for: {query}")
        lines.append("")
        if not merged:
            lines.append("(no new results)")
        for index, result in enumerate(merged, start=1):
            lines.append(f"{index}. {result.title}")
            lines.append(f"   {result.url}")
            # Name the backend only when several were merged; always show a date.
            source = result.source if len(backends) > 1 else ""
            tags = " · ".join(tag for tag in (source, result.date) if tag)
            description = result.snippet
            if tags:
                description = f"[{tags}] {description}".strip()
            if description:
                lines.append(f"   {description}")
    if failures:
        lines.append("")
        lines.append("Notes:")
        for query, name, error in failures:
            prefix = f"{name}" if len(queries) == 1 else f"{name} ({query})"
            lines.append(f"- {prefix}: {error}")
    lines.append("")
    lines.append("Read any of these with the fetch_page tool.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Page reading


BLOCK_TAGS = frozenset((
    "p", "div", "br", "li", "tr", "section", "article", "header", "footer",
    "table", "pre", "blockquote", "ul", "ol", "h1", "h2", "h3", "h4", "h5",
    "h6", "form", "figure", "figcaption", "main", "aside", "dl", "dd", "dt",
))
SKIP_TAGS = frozenset((
    "script", "style", "template", "noscript", "svg", "nav", "button",
    "select", "iframe",
))
HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
# "head" stays readable on purpose: the only text a head normally carries is
# the page title, which fetch_page reports. Style and script skip themselves.

PUBLISHED_META = frozenset((
    "article:published_time", "og:published_time", "datepublished", "date",
    "pubdate", "publishdate", "publish-date", "dc.date", "dc.date.issued",
    "dcterms.date", "dcterms.created", "citation_publication_date",
    "citation_date", "citation_online_date", "sailthru.date", "parsely-pub-date",
))
MODIFIED_META = frozenset((
    "article:modified_time", "og:updated_time", "datemodified", "last-modified",
    "dcterms.modified",
))
LD_JSON_DATE = re.compile(r'"(datePublished|dateModified)"\s*:\s*"([^"]{4,40})"')


class ReadableTextExtractor(HTMLParser):
    """Turn an HTML page into readable text or light markdown.

    Markdown keeps headings, list items, preformatted blocks, and links, which
    a research worker needs for citations and follow-up reading. Plain text
    mode drops the markup and the link targets.
    """

    def __init__(self, base_url: str = "", markdown: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.markdown = markdown
        self.title = ""
        self.published = ""
        self.modified = ""
        self.lines: list[str] = []
        self._line: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._pre_depth = 0
        self._pre_buffer: list[str] = []
        self._link_href: str | None = None
        self._link_text: list[str] = []
        self._ld_json = False
        self._ld_buffer: list[str] = []

    def flush_line(self) -> None:
        text = " ".join("".join(self._line).split())
        self._line = []
        if text and text not in ("-", "- "):
            self.lines.append(text)

    def _meta(self, attributes: dict[str, str | None]) -> None:
        name = (attributes.get("property") or attributes.get("name") or attributes.get("itemprop") or "").lower()
        content = (attributes.get("content") or "").strip()
        if not name or not content:
            return
        if name in PUBLISHED_META and not self.published:
            self.published = content[:40]
        elif name in MODIFIED_META and not self.modified:
            self.modified = content[:40]

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        attrs = dict(attributes)
        if tag == "script" and (attrs.get("type") or "").lower() == "application/ld+json":
            self._ld_json = True
            self._ld_buffer = []
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "meta":
            self._meta(attrs)
            return
        if (
            tag == "time"
            and not self.published
            and attrs.get("datetime")
            and ("pubdate" in attrs or (attrs.get("itemprop") or "").lower() == "datepublished")
        ):
            self.published = (attrs.get("datetime") or "")[:40]
        if tag == "title":
            self._in_title = True
            return
        if self._pre_depth:
            if tag == "pre":
                self._pre_depth += 1
            return
        if tag == "pre" and self.markdown:
            self.flush_line()
            self._pre_depth = 1
            self._pre_buffer = []
            return
        if tag in BLOCK_TAGS:
            self.flush_line()
        if not self.markdown:
            return
        if tag in HEADING_TAGS:
            self._line.append("#" * HEADING_TAGS[tag] + " ")
        elif tag == "li":
            self._line.append("- ")
        elif tag == "a":
            href = (attrs.get("href") or "").strip()
            if href and not href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
                target = urljoin(self.base_url, href) if self.base_url else href
                if urlsplit(target).scheme in ("http", "https"):
                    self._link_href = target
                    self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._ld_json:
            self._ld_json = False
            for kind, value in LD_JSON_DATE.findall("".join(self._ld_buffer)):
                if kind == "datePublished" and not self.published:
                    self.published = value
                elif kind == "dateModified" and not self.modified:
                    self.modified = value
        if tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
            return
        if self._pre_depth:
            if tag == "pre":
                self._pre_depth -= 1
                if not self._pre_depth:
                    block = "".join(self._pre_buffer).strip("\n")
                    if block.strip():
                        self.lines.append("```\n" + block + "\n```")
                    self._pre_buffer = []
            return
        if tag == "a" and self._link_href is not None:
            text = " ".join("".join(self._link_text).split())
            if text:
                self._line.append(f"[{text}]({self._link_href})")
            self._link_href = None
            self._link_text = []
            return
        if tag in BLOCK_TAGS:
            self.flush_line()

    def handle_data(self, data: str) -> None:
        if self._ld_json:
            self._ld_buffer.append(data)
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
        elif self._pre_depth:
            self._pre_buffer.append(data)
        elif self._link_href is not None:
            self._link_text.append(data)
        else:
            self._line.append(data)


def read_html(html_text: str, base_url: str = "", markdown: bool = False) -> ReadableTextExtractor:
    extractor = ReadableTextExtractor(base_url, markdown)
    try:
        extractor.feed(html_text)
        extractor.close()
    except Exception as error:  # noqa: BLE001 - tolerate broken markup
        raise ToolError(f"could not read the page markup: {error}") from error
    extractor.flush_line()
    return extractor


def html_to_readable_text(html_text: str) -> tuple[str, str]:
    extractor = read_html(html_text)
    title = " ".join(extractor.title.split())
    return title, "\n".join(extractor.lines)


QUOTE_FOLDS = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ",
})


def fold(text: str) -> str:
    return " ".join(text.translate(QUOTE_FOLDS).casefold().split())


def find_phrases(text: str, phrases: list[str]) -> list[str]:
    """Report whether each phrase appears in the page, with context.

    Matching ignores case, runs of whitespace, and curly versus straight
    quotes, because pages and quotations rarely agree on those.
    """

    flat = " ".join(text.translate(QUOTE_FOLDS).split())
    haystack = flat.casefold()
    lines = []
    for phrase in phrases:
        needle = fold(phrase)
        position = haystack.find(needle)
        if position < 0:
            lines.append(f'NOT FOUND: "{phrase}"')
            continue
        start = max(0, position - FIND_CONTEXT_CHARS)
        end = min(len(flat), position + len(needle) + FIND_CONTEXT_CHARS)
        context = flat[start:end]
        lines.append(f'FOUND: "{phrase}"')
        lines.append(f"   ...{context}...")
    return lines


def _find_list(arguments: dict[str, object]) -> list[str]:
    raw = arguments.get("find")
    if raw is None:
        return []
    phrases = [raw] if isinstance(raw, str) else raw
    if not isinstance(phrases, list) or not all(isinstance(p, str) for p in phrases):
        raise ToolError("find must be a string or a list of strings")
    cleaned = [p.strip() for p in phrases if p.strip()]
    if not cleaned:
        raise ToolError("find needs at least one non-empty phrase")
    if len(cleaned) > MAX_FIND_PHRASES:
        raise ToolError(f"find takes at most {MAX_FIND_PHRASES} phrases")
    if any(len(p) > MAX_FIND_PHRASE_CHARS for p in cleaned):
        raise ToolError(f"each find phrase must be at most {MAX_FIND_PHRASE_CHARS} characters")
    return cleaned


def run_fetch_page(arguments: dict[str, object]) -> str:
    url = arguments.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ToolError("fetch_page needs a non-empty URL string")
    url = url.strip()
    max_chars = _optional_int(arguments, "max_chars", DEFAULT_FETCH_CHARS, 200, MAX_FETCH_CHARS)
    raw_start = arguments.get("start_char", 0)
    if isinstance(raw_start, bool) or not isinstance(raw_start, int) or raw_start < 0:
        raise ToolError("start_char must be a whole number of zero or more")
    output_format = arguments.get("format", "markdown")
    if output_format not in ("markdown", "text"):
        raise ToolError("format must be markdown or text")
    phrases = _find_list(arguments)

    cached = FETCH_CACHE.get(url)
    if isinstance(cached, Response):
        response = cached
    else:
        response = fetch_response(url)
        if response.status == 200:
            FETCH_CACHE.put(url, response)
    status, content_type, body = response.status, response.content_type, response.body

    lowered = content_type.split(";")[0].strip().lower()
    allowed = lowered.startswith(TEXT_CONTENT_PREFIXES) or lowered in JSON_CONTENT_TYPES
    if not allowed:
        return (
            f"{url} returned status {status} with content type {lowered or 'unknown'}, "
            f"and {len(body)} bytes were available before the cap. Binary and "
            "unsupported content is not converted."
        )
    truncated_bytes = len(body) > MAX_FETCH_BYTES
    text_body = body[:MAX_FETCH_BYTES].decode("utf-8", errors="replace")

    title = ""
    published = modified = ""
    if lowered in HTML_CONTENT_TYPES:
        extractor = read_html(text_body, response.final_url or url, output_format == "markdown")
        title = " ".join(extractor.title.split())
        published, modified = extractor.published, extractor.modified
        readable = "\n".join(extractor.lines)
    else:
        readable = text_body.strip()

    note = f"Fetched {url} (status {status})."
    if response.final_url and response.final_url != url:
        note += f" Final URL: {response.final_url}"
    if title:
        note += f" Page title: {title}"
    output_lines = [note]
    dates = "; ".join(
        f"{label}: {value}" for label, value in (("Published", published), ("Modified", modified)) if value
    )
    if dates:
        output_lines.append(dates)
    output_lines.append("")

    if phrases:
        output_lines.extend(find_phrases(readable, phrases))
        if truncated_bytes:
            output_lines.append("[response was cut off at the size cap; later text was not searched]")
        return "\n".join(output_lines)

    total = len(readable)
    if not readable:
        output_lines.append("(no readable text was found in the page)")
    elif raw_start >= total:
        output_lines.append(f"(start_char {raw_start} is past the end; the page has {total} characters)")
    else:
        end = min(total, raw_start + max_chars)
        output_lines.append(readable[raw_start:end])
        if end < total or raw_start:
            output_lines.append(
                f"[truncated] Showing characters {raw_start}-{end} of {total}."
                + (f" Continue with start_char={end}." if end < total else "")
            )
    if truncated_bytes:
        output_lines.append("[response was cut off at the size cap]")
    return "\n".join(output_lines)


# ---------------------------------------------------------------------------
# MCP protocol


TOOLS = [
    {
        "name": "web_search",
        "description": (
            "Search the public web and get ranked results with titles, URLs, and "
            "short descriptions. Pass up to 5 related queries in `queries` to run "
            "them in parallel in one call; links already shown for an earlier "
            "query are not repeated. Use `timelimit` for recent material and "
            "`page` for deeper results. Use fetch_page to read a result."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "One search query."},
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": MAX_QUERIES,
                    "description": "Several queries to run in parallel instead of one.",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_SEARCH_RESULTS,
                    "default": DEFAULT_SEARCH_RESULTS,
                    "description": "Results per query.",
                },
                "timelimit": {
                    "type": "string",
                    "enum": list(TIMELIMITS),
                    "description": "Only results from the past day, week, month, or year.",
                },
                "category": {
                    "type": "string",
                    "enum": list(CATEGORIES),
                    "default": "text",
                    "description": "news needs a SearXNG or ddgs backend.",
                },
                "region": {
                    "type": "string",
                    "description": "Region and language such as us-en, uk-en, de-de, or wt-wt for none.",
                },
                "page": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_PAGE,
                    "default": 1,
                },
                "backends": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(BACKENDS)},
                    "description": (
                        "Search backends to query and merge. duckduckgo and wikipedia "
                        "always work; searxng and ddgs only when the user configured "
                        "them. Omit to use the configured default."
                    ),
                },
            },
        },
    },
    {
        "name": "fetch_page",
        "description": (
            "Fetch one public web page over http(s) and return its readable text "
            "as light markdown with headings and links, plus its publication date "
            "when the page declares one. Page through long documents with "
            "`start_char`. Pass `find` with exact phrases to check whether a "
            "quotation really appears on the page instead of reading all of it. "
            "Refuses private network addresses and binary content."
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
                "start_char": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": "Where to start reading, to continue a truncated page.",
                },
                "format": {
                    "type": "string",
                    "enum": ["markdown", "text"],
                    "default": "markdown",
                },
                "find": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": MAX_FIND_PHRASES,
                    "description": "Up to 5 exact phrases to look for on the page.",
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
    except Exception as error:  # noqa: BLE001 - one bad call must not end the server
        return f"{name} failed unexpectedly: {type(error).__name__}: {error}", True


def _reply_id(message: dict[str, object]) -> object:
    identifier = message.get("id")
    return identifier if isinstance(identifier, (int, str)) else 0


def tool_call_reply(message: dict[str, object]) -> dict[str, object]:
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
    return {
        "jsonrpc": "2.0",
        "id": _reply_id(message),
        "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
    }


def handle_message(message: dict[str, object]) -> dict[str, object] | None:
    method = message.get("method")
    if not isinstance(method, str):
        return None
    if method.startswith("notifications/"):
        return None
    reply_id = _reply_id(message)
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        return tool_call_reply(message)
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
    """Serve MCP requests until stdin closes. Returns a process exit code.

    Tool calls run on a thread pool and reply as they finish, which JSON-RPC
    allows because every reply carries its request id. Everything else is
    answered in order on the reading thread.
    """

    input_stream = stream_in if stream_in is not None else sys.stdin
    output_stream = stream_out if stream_out is not None else sys.stdout
    write_lock = threading.Lock()

    def send(reply: dict[str, object]) -> None:
        with write_lock:
            output_stream.write(json.dumps(reply, separators=(",", ":")) + "\n")
            output_stream.flush()

    def run_call(message: dict[str, object]) -> None:
        send(tool_call_reply(message))

    with ThreadPoolExecutor(max_workers=TOOL_WORKERS, thread_name_prefix="web-tools") as pool:
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
            if message.get("method") == "tools/call":
                pool.submit(run_call, message)
                continue
            reply = handle_message(message)
            if reply is not None:
                send(reply)
    return 0


if __name__ == "__main__":
    sys.exit(serve())
