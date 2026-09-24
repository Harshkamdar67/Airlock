#!/usr/bin/env python3
"""Offline tests for the bundled airlock-web-tools MCP server."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "plugins" / "airlock" / "mcp-server" / "airlock_web_tools.py"

spec = importlib.util.spec_from_file_location("airlock_web_tools", SERVER_PATH)
if spec is None or spec.loader is None:  # pragma: no cover - import machinery
    raise SystemExit(f"could not load {SERVER_PATH}")
web_tools = importlib.util.module_from_spec(spec)
sys.modules["airlock_web_tools"] = web_tools
spec.loader.exec_module(web_tools)

ToolError = web_tools.ToolError


SEARCH_PAGE = """
<html><head><title>DuckDuckGo Search</title></head><body>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fone">
    First  Result
  </a>
  <a class="result__snippet">The first &amp; best result.</a>
</div>
<div class="result">
  <a class="result__a" href="https://example.net/two">Second</a>
  <a class="result__snippet">Another snippet here.</a>
</div>
</body></html>
"""

PAGE_HTML = """
<html>
<head><title>Example Page</title><style>body { color: red; }</style></head>
<body>
<script>var secret = "script text";</script>
<h1>Welcome</h1>
<p>First   paragraph with &amp; entity.</p>
<noscript>noscript text</noscript>
<p>Second paragraph</p>
</body>
</html>
"""


def public_getaddrinfo(host: str, port: object) -> list[tuple]:
    del host, port
    return [(-1, -1, -1, "", ("93.184.216.34", 0))]


class DecodeLinkTests(unittest.TestCase):
    def test_resolves_redirect_link(self) -> None:
        self.assertEqual(
            web_tools.decode_duckduckgo_link(
                "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fone"
            ),
            "https://example.org/one",
        )

    def test_passes_plain_links_through(self) -> None:
        self.assertEqual(
            web_tools.decode_duckduckgo_link("https://example.net/two"),
            "https://example.net/two",
        )

    def test_keeps_redirect_without_target(self) -> None:
        self.assertEqual(
            web_tools.decode_duckduckgo_link("https://duckduckgo.com/l/?x=1"),
            "https://duckduckgo.com/l/?x=1",
        )


class ParseSearchResultsTests(unittest.TestCase):
    def test_parses_titles_urls_and_snippets(self) -> None:
        results = web_tools.parse_search_results(SEARCH_PAGE, limit=10)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "First Result")
        self.assertEqual(results[0]["url"], "https://example.org/one")
        self.assertEqual(results[0]["description"], "The first & best result.")
        self.assertEqual(results[1]["url"], "https://example.net/two")

    def test_limit_truncates(self) -> None:
        results = web_tools.parse_search_results(SEARCH_PAGE, limit=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "First Result")


class AssertPublicUrlTests(unittest.TestCase):
    def test_refuses_non_http_schemes(self) -> None:
        with self.assertRaises(ToolError):
            web_tools.assert_public_url("ftp://example.org/file")

    def test_refuses_missing_host(self) -> None:
        with self.assertRaises(ToolError):
            web_tools.assert_public_url("not-a-url")

    def test_refuses_loopback_literal(self) -> None:
        with self.assertRaises(ToolError):
            web_tools.assert_public_url("http://127.0.0.1:8080/x")

    def test_refuses_private_and_link_local_literals(self) -> None:
        for url in (
            "http://10.1.2.3/",
            "http://192.168.0.9/",
            "http://172.16.5.5/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://0.0.0.0/",
        ):
            with self.assertRaises(ToolError, msg=url):
                web_tools.assert_public_url(url)

    def test_refuses_localhost_name(self) -> None:
        with self.assertRaises(ToolError):
            web_tools.assert_public_url("http://localhost/x")

    def test_accepts_resolved_public_address(self) -> None:
        with mock.patch.object(web_tools.socket, "getaddrinfo", public_getaddrinfo):
            web_tools.assert_public_url("https://example.org/page")


class ReadableTextTests(unittest.TestCase):
    def test_extracts_title_and_body_text(self) -> None:
        title, text = web_tools.html_to_readable_text(PAGE_HTML)
        self.assertEqual(title, "Example Page")
        self.assertIn("Welcome", text)
        self.assertIn("First paragraph with & entity.", text)
        self.assertIn("Second paragraph", text)
        self.assertNotIn("script text", text)
        self.assertNotIn("color: red", text)
        self.assertNotIn("noscript text", text)


class WebToolsCase(unittest.TestCase):
    """Isolate each test from the server's cache and request pacing."""

    def setUp(self) -> None:
        web_tools.SEARCH_CACHE.clear()
        web_tools.FETCH_CACHE.clear()
        pacing = mock.patch.dict(
            web_tools.BACKEND_MIN_INTERVAL_SECONDS,
            {name: 0.0 for name in web_tools.BACKENDS},
        )
        pacing.start()
        self.addCleanup(pacing.stop)


def settings_with(**overrides: object):  # noqa: ANN201
    settings = web_tools.load_settings({})
    for name, value in overrides.items():
        setattr(settings, name, value)
    return settings


def fake_post(url: str, fields: dict[str, str]) -> tuple[int, str, str]:
    del url, fields
    return 200, "text/html; charset=utf-8", SEARCH_PAGE


def response(body: bytes | str, content_type: str = "text/html; charset=utf-8",
             status: int = 200, final_url: str = ""):  # noqa: ANN201
    if isinstance(body, str):
        body = body.encode("utf-8")
    return web_tools.Response(status, content_type, body, final_url)


def fake_html_fetch(url: str):  # noqa: ANN201
    return response(PAGE_HTML, final_url=url)


class AddressTests(unittest.TestCase):
    def test_refuses_non_global_ranges(self) -> None:
        for address in (
            "100.64.1.1",        # shared address space (carrier-grade NAT, Tailscale)
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "::ffff:127.0.0.1",  # IPv4-mapped loopback
            "::ffff:10.0.0.1",
            "fc00::1",
            "fe80::1%eth0",
            "224.0.0.1",
            "0.0.0.0",
        ):
            self.assertTrue(web_tools.address_is_refused(address), address)

    def test_accepts_public_addresses(self) -> None:
        for address in ("93.184.216.34", "8.8.8.8", "2001:4860:4860::8888"):
            self.assertFalse(web_tools.address_is_refused(address), address)

    def test_pinned_connect_refuses_private_answer_without_connecting(self) -> None:
        def private_answer(host: str, port: object, *args: object) -> list[tuple]:
            del host, port, args
            return [(2, 1, 6, "", ("127.0.0.1", 80))]

        with mock.patch.object(web_tools.socket, "getaddrinfo", private_answer), \
                mock.patch.object(web_tools.socket, "socket") as socket_factory:
            with self.assertRaises(ToolError):
                web_tools.connect_to_public_address(("rebind.example", 80), 5)
        socket_factory.assert_not_called()

    def test_pinned_connect_uses_the_checked_address(self) -> None:
        def public_answer(host: str, port: object, *args: object) -> list[tuple]:
            del host, args
            return [(2, 1, 6, "", ("93.184.216.34", port))]

        fake_socket = mock.MagicMock()
        with mock.patch.object(web_tools.socket, "getaddrinfo", public_answer), \
                mock.patch.object(web_tools.socket, "socket", return_value=fake_socket):
            sock = web_tools.connect_to_public_address(("example.org", 443), 5)
        self.assertIs(sock, fake_socket)
        fake_socket.connect.assert_called_once_with(("93.184.216.34", 443))

    def test_pinned_connection_checks_at_connect_time(self) -> None:
        def private_answer(host: str, port: object, *args: object) -> list[tuple]:
            del host, port, args
            return [(2, 1, 6, "", ("10.0.0.5", 80))]

        connection = web_tools.PinnedHTTPConnection("rebind.example", 80, timeout=5)
        with mock.patch.object(web_tools.socket, "getaddrinfo", private_answer):
            with self.assertRaises(ToolError):
                connection.connect()

    def test_pinned_connection_reaches_a_configured_proxy_normally(self) -> None:
        connection = web_tools.PinnedHTTPSConnection("proxy.internal", 3128, timeout=5)
        connection.set_tunnel("example.org", 443)
        with mock.patch.object(
            web_tools.socket, "create_connection", side_effect=OSError("stop here")
        ) as create:
            with self.assertRaises(OSError):
                connection.connect()
        create.assert_called_once()
        self.assertEqual(create.call_args[0][0], ("proxy.internal", 3128))


class SettingsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        settings = web_tools.load_settings({})
        self.assertEqual(settings.backends, ("duckduckgo",))
        self.assertEqual(settings.available(), ("duckduckgo", "wikipedia"))
        self.assertIsNone(settings.searxng_url)
        self.assertIsNone(settings.ddgs_python)
        self.assertEqual(settings.ddgs_engines, "auto")

    def test_configured_backends_are_filtered_to_available_ones(self) -> None:
        settings = web_tools.load_settings({
            "AIRLOCK_WEB_SEARCH_BACKENDS": "wikipedia, searxng,duckduckgo,bogus,wikipedia",
        })
        self.assertEqual(settings.backends, ("wikipedia", "duckduckgo"))

    def test_searxng_and_ddgs_join_when_configured(self) -> None:
        settings = web_tools.load_settings({
            "AIRLOCK_WEB_SEARXNG_URL": "http://127.0.0.1:8888/",
            "AIRLOCK_WEB_DDGS_PYTHON": sys.executable,
            "AIRLOCK_WEB_DDGS_ENGINES": "Brave, Mojeek",
            "AIRLOCK_WEB_SEARCH_BACKENDS": "searxng,ddgs",
        })
        self.assertEqual(settings.searxng_url, "http://127.0.0.1:8888")
        self.assertEqual(settings.ddgs_python, sys.executable)
        self.assertEqual(settings.ddgs_engines, "brave,mojeek")
        self.assertEqual(settings.backends, ("searxng", "ddgs"))

    def test_invalid_values_are_ignored(self) -> None:
        settings = web_tools.load_settings({
            "AIRLOCK_WEB_SEARXNG_URL": "file:///etc/passwd",
            "AIRLOCK_WEB_DDGS_PYTHON": "python3",
            "AIRLOCK_WEB_DDGS_ENGINES": "brave;rm -rf",
            "AIRLOCK_WEB_SEARCH_BACKENDS": "nothing-valid",
        })
        self.assertIsNone(settings.searxng_url)
        self.assertIsNone(settings.ddgs_python)
        self.assertEqual(settings.ddgs_engines, "auto")
        self.assertEqual(settings.backends, ("duckduckgo",))


class RunWebSearchTests(WebToolsCase):
    def search_with(
        self,
        arguments: dict[str, object],
        poster=fake_post,
        settings=None,
    ) -> str:
        with mock.patch.object(web_tools, "http_post_form", poster):
            return web_tools.run_web_search(arguments, settings or settings_with())

    def test_formats_numbered_results(self) -> None:
        output = self.search_with({"query": "example"})
        self.assertIn("Results for: example", output)
        self.assertIn("1. First Result", output)
        self.assertIn("https://example.org/one", output)
        self.assertIn("The first & best result.", output)
        self.assertIn("Read any of these with the fetch_page tool.", output)

    def test_requires_query(self) -> None:
        with self.assertRaises(ToolError):
            self.search_with({})
        with self.assertRaises(ToolError):
            self.search_with({"query": "   "})
        with self.assertRaises(ToolError):
            self.search_with({"queries": ["", "  "]})

    def test_rejects_bad_max_results(self) -> None:
        with self.assertRaises(ToolError):
            self.search_with({"query": "q", "max_results": True})
        with self.assertRaises(ToolError):
            self.search_with({"query": "q", "max_results": "many"})

    def test_rejects_bad_filters(self) -> None:
        for arguments in (
            {"query": "q", "timelimit": "h"},
            {"query": "q", "region": "english"},
            {"query": "q", "category": "images"},
            {"query": "q", "page": 0.5},
            {"query": "q", "queries": "not a list"},
            {"queries": ["a", "b", "c", "d", "e", "f"]},
            {"query": "q", "backends": []},
            {"query": "q", "backends": ["searxng"]},
        ):
            with self.assertRaises(ToolError, msg=str(arguments)):
                self.search_with(arguments)

    def test_passes_filters_to_duckduckgo(self) -> None:
        seen: list[dict[str, str]] = []

        def post(url: str, fields: dict[str, str]) -> tuple[int, str, str]:
            del url
            seen.append(dict(fields))
            return 200, "text/html", SEARCH_PAGE

        self.search_with(
            {"query": "q", "timelimit": "w", "region": "UK-EN", "page": 3}, post
        )
        self.assertEqual(seen[0]["q"], "q")
        self.assertEqual(seen[0]["df"], "w")
        self.assertEqual(seen[0]["kl"], "uk-en")
        self.assertEqual(seen[0]["s"], "25")

    def test_reports_non_html_content_type(self) -> None:
        def post(url: str, fields: dict[str, str]) -> tuple[int, str, str]:
            del url, fields
            return 200, "application/json", "{}"

        with self.assertRaises(ToolError):
            self.search_with({"query": "q"}, post)

    def test_reports_empty_result_page(self) -> None:
        def post(url: str, fields: dict[str, str]) -> tuple[int, str, str]:
            del url, fields
            return 200, "text/html", "<html><body></body></html>"

        with self.assertRaises(ToolError):
            self.search_with({"query": "q"}, post)

    def test_reports_a_bot_challenge_clearly(self) -> None:
        def post(url: str, fields: dict[str, str]) -> tuple[int, str, str]:
            del url, fields
            return 202, "text/html", "<html><div class='anomaly-modal'></div></html>"

        with self.assertRaisesRegex(ToolError, "refusing automated searches"):
            self.search_with({"query": "q"}, post)

    def test_drops_sponsored_links(self) -> None:
        page = SEARCH_PAGE.replace(
            "https://example.net/two",
            "https://duckduckgo.com/y.js?ad_domain=spam.example",
        )

        def post(url: str, fields: dict[str, str]) -> tuple[int, str, str]:
            del url, fields
            return 200, "text/html", page

        output = self.search_with({"query": "q"}, post)
        self.assertNotIn("y.js", output)
        self.assertIn("https://example.org/one", output)

    def test_several_queries_run_and_do_not_repeat_links(self) -> None:
        output = self.search_with({"queries": ["alpha", "beta"]})
        self.assertIn("Results for: alpha", output)
        self.assertIn("Results for: beta", output)
        self.assertEqual(output.count("https://example.org/one"), 1)
        self.assertIn("(no new results)", output)

    def test_merges_backends_and_notes_a_failing_one(self) -> None:
        def wiki(request, settings):  # noqa: ANN001, ANN202
            del request, settings
            return [
                web_tools.SearchResult("Topic", "https://en.wikipedia.org/wiki/Topic",
                                       "About the topic", "edited 2026-09-01", "wikipedia"),
                web_tools.SearchResult("Dup", "http://www.example.org/one/", "", "", "wikipedia"),
            ]

        def broken(request, settings):  # noqa: ANN001, ANN202
            del request, settings
            raise ToolError("searx is down")

        backends = dict(web_tools.SEARCH_BACKENDS, wikipedia=wiki, searxng=broken)
        settings = settings_with(searxng_url="http://127.0.0.1:8888")
        with mock.patch.dict(web_tools.SEARCH_BACKENDS, backends):
            output = self.search_with(
                {"query": "topic", "backends": ["duckduckgo", "wikipedia", "searxng"]},
                settings=settings,
            )
        self.assertIn("https://en.wikipedia.org/wiki/Topic", output)
        self.assertIn("[wikipedia · edited 2026-09-01] About the topic", output)
        self.assertIn("[duckduckgo] The first & best result.", output)
        # The same page from two backends is listed once.
        self.assertEqual(output.count("example.org/one"), 1)
        self.assertIn("Notes:", output)
        self.assertIn("searxng: searx is down", output)

    def test_raises_when_every_backend_fails(self) -> None:
        def broken(request, settings):  # noqa: ANN001, ANN202
            del request, settings
            raise ToolError("offline")

        with mock.patch.dict(web_tools.SEARCH_BACKENDS, {"duckduckgo": broken}):
            with self.assertRaisesRegex(ToolError, "offline"):
                self.search_with({"query": "q"})

    def test_news_needs_a_news_backend(self) -> None:
        with self.assertRaisesRegex(ToolError, "news search needs"):
            self.search_with({"query": "q", "category": "news"})

    def test_successful_searches_are_cached(self) -> None:
        calls: list[str] = []

        def post(url: str, fields: dict[str, str]) -> tuple[int, str, str]:
            del url
            calls.append(fields["q"])
            return 200, "text/html", SEARCH_PAGE

        self.search_with({"query": "same"}, post)
        self.search_with({"query": "same"}, post)
        self.assertEqual(calls, ["same"])


class WikipediaBackendTests(WebToolsCase):
    def test_parses_search_api_reply(self) -> None:
        seen: list[str] = []

        def get_json(url: str, *, trusted: bool = False) -> object:
            seen.append(url)
            self.assertFalse(trusted)
            return {"query": {"search": [
                {"title": "Solar power", "snippet": "<span class=\"searchmatch\">Solar</span> energy &amp; more",
                 "timestamp": "2026-08-30T10:00:00Z"},
                {"title": 7},
            ]}}

        request = web_tools.SearchRequest("solar", 5, "text", None, "de-de", 2)
        with mock.patch.object(web_tools, "http_get_json", get_json):
            results = web_tools.search_wikipedia(request, settings_with())
        self.assertTrue(seen[0].startswith("https://de.wikipedia.org/w/api.php?"))
        self.assertIn("sroffset=5", seen[0])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://de.wikipedia.org/wiki/Solar_power")
        self.assertEqual(results[0].snippet, "Solar energy & more")
        self.assertEqual(results[0].date, "edited 2026-08-30")

    def test_no_region_searches_english_wikipedia(self) -> None:
        seen: list[str] = []

        def get_json(url: str, *, trusted: bool = False) -> object:
            del trusted
            seen.append(url)
            return {"query": {"search": []}}

        request = web_tools.SearchRequest("solar", 5, "text", None, "wt-wt", 1)
        with mock.patch.object(web_tools, "http_get_json", get_json):
            web_tools.search_wikipedia(request, settings_with())
        self.assertTrue(seen[0].startswith("https://en.wikipedia.org/"))


class SearxngBackendTests(WebToolsCase):
    def test_queries_the_configured_instance_as_trusted(self) -> None:
        seen: list[tuple[str, bool]] = []

        def get_json(url: str, *, trusted: bool = False) -> object:
            seen.append((url, trusted))
            return {"results": [
                {"url": "https://news.example/a", "title": " Headline ", "content": "Body",
                 "publishedDate": "2026-09-20T08:00:00", "engines": ["bing news", "yahoo news"]},
                {"url": 5, "title": "bad"},
            ]}

        settings = settings_with(searxng_url="http://127.0.0.1:8888")
        request = web_tools.SearchRequest("event", 5, "news", "d", "us-en", 1)
        with mock.patch.object(web_tools, "http_get_json", get_json):
            results = web_tools.search_searxng(request, settings)
        url, trusted = seen[0]
        self.assertTrue(trusted)
        self.assertTrue(url.startswith("http://127.0.0.1:8888/search?"))
        self.assertIn("format=json", url)
        self.assertIn("categories=news", url)
        self.assertIn("time_range=day", url)
        self.assertEqual(results[0].title, "Headline")
        self.assertEqual(results[0].date, "2026-09-20")
        self.assertEqual(results[0].source, "searxng:bing news,yahoo news")
        self.assertEqual(len(results), 1)


class DdgsBackendTests(WebToolsCase):
    def test_maps_text_and_news_results(self) -> None:
        reply = json.dumps({"results": [
            {"title": "A", "href": "https://a.example/", "body": "alpha"},
            {"title": "B", "url": "https://b.example/", "body": "beta",
             "date": "2026-09-21T00:00:00+00:00", "source": "Example News"},
            {"title": None, "href": "https://c.example/"},
        ]})
        completed = web_tools.subprocess.CompletedProcess([], 0, stdout=reply + "\n", stderr="")
        settings = settings_with(ddgs_python=sys.executable, ddgs_engines="brave,mojeek")
        request = web_tools.SearchRequest("q", 5, "news", "w", None, 1)
        with mock.patch.object(web_tools.subprocess, "run", return_value=completed) as run:
            results = web_tools.search_ddgs(request, settings)
        command = run.call_args[0][0]
        self.assertEqual(command[:3], [sys.executable, "-I", "-c"])
        job = json.loads(run.call_args[1]["input"])
        self.assertEqual(job["backend"], "brave,mojeek")
        self.assertEqual(job["category"], "news")
        self.assertEqual(job["timelimit"], "w")
        self.assertEqual([r.url for r in results], ["https://a.example/", "https://b.example/"])
        self.assertEqual(results[1].source, "ddgs:Example News")
        self.assertEqual(results[1].date, "2026-09-21")

    def test_reports_helper_errors(self) -> None:
        completed = web_tools.subprocess.CompletedProcess(
            [], 0, stdout=json.dumps({"error": "RatelimitException: slow down"}), stderr=""
        )
        settings = settings_with(ddgs_python=sys.executable)
        request = web_tools.SearchRequest("q", 5, "text", None, None, 1)
        with mock.patch.object(web_tools.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(ToolError, "slow down"):
                web_tools.search_ddgs(request, settings)

    def test_real_helper_without_ddgs_installed_says_so(self) -> None:
        settings = settings_with(ddgs_python=sys.executable)
        request = web_tools.SearchRequest("q", 5, "text", None, None, 1)
        try:
            import ddgs  # noqa: F401
        except ImportError:
            with self.assertRaisesRegex(ToolError, "ddgs is not importable"):
                web_tools.search_ddgs(request, settings)
        else:  # pragma: no cover - only on machines that have ddgs
            self.skipTest("ddgs is installed here, so the helper would search")


FETCH_PAGE = """
<html><head><title>Report</title>
<meta property="article:published_time" content="2026-09-01T12:00:00Z">
<script type="application/ld+json">{"dateModified": "2026-09-10"}</script>
</head><body>
<nav><a href="/home">Home</a> menu</nav>
<h2>Findings</h2>
<p>The rate rose by 12% in 2026, according to the “annual survey”.</p>
<ul><li>First point</li><li>See <a href="/details?id=1">the details</a></li></ul>
<pre>line one
  line two</pre>
</body></html>
"""


class RunFetchPageTests(WebToolsCase):
    def run_fetch(self, arguments: dict[str, object], fetcher=fake_html_fetch) -> str:
        with mock.patch.object(web_tools, "fetch_response", fetcher):
            return web_tools.run_fetch_page(arguments)

    def test_returns_note_title_and_text(self) -> None:
        output = self.run_fetch({"url": "https://example.org/page"})
        self.assertTrue(output.startswith("Fetched https://example.org/page (status 200)."))
        self.assertIn("Page title: Example Page", output)
        self.assertIn("First paragraph with & entity.", output)
        self.assertIn("# Welcome", output)

    def test_markdown_keeps_structure_links_and_dates(self) -> None:
        output = self.run_fetch(
            {"url": "https://site.example/a/report"},
            lambda url: response(FETCH_PAGE, final_url="https://site.example/a/report?x=1"),
        )
        self.assertIn("Final URL: https://site.example/a/report?x=1", output)
        self.assertIn("Published: 2026-09-01T12:00:00Z; Modified: 2026-09-10", output)
        self.assertIn("## Findings", output)
        self.assertIn("- First point", output)
        self.assertIn("[the details](https://site.example/details?id=1)", output)
        self.assertIn("```\nline one\n  line two\n```", output)
        self.assertNotIn("menu", output)

    def test_only_a_marked_time_counts_as_publication(self) -> None:
        page = ("<html><body><time datetime='2020-01-01'>comment</time>"
                "<time itemprop='datePublished' datetime='2026-05-05'>May</time></body></html>")
        output = self.run_fetch(
            {"url": "https://site.example/t"}, lambda url: response(page, final_url=url)
        )
        self.assertIn("Published: 2026-05-05", output)
        self.assertNotIn("2020-01-01", output.splitlines()[1])

    def test_text_format_drops_markup(self) -> None:
        output = self.run_fetch(
            {"url": "https://site.example/r", "format": "text"},
            lambda url: response(FETCH_PAGE, final_url=url),
        )
        self.assertIn("Findings", output)
        self.assertNotIn("## Findings", output)
        self.assertNotIn("](https://", output)
        self.assertIn("the details", output)

    def test_find_reports_found_and_missing_phrases(self) -> None:
        output = self.run_fetch(
            {"url": "https://site.example/r",
             "find": ['rose by 12% in 2026, according to the "annual  survey"', "fell by 40%"]},
            lambda url: response(FETCH_PAGE, final_url=url),
        )
        self.assertIn('FOUND: "rose by 12% in 2026', output)
        self.assertIn('NOT FOUND: "fell by 40%"', output)
        self.assertNotIn("First point", output.split("FOUND", 1)[0])

    def test_rejects_bad_find(self) -> None:
        for find in (5, [], [""], ["a"] * 6, ["x" * 501]):
            with self.assertRaises(ToolError, msg=str(find)):
                self.run_fetch({"url": "https://example.org/", "find": find})

    def test_pages_through_long_text(self) -> None:
        long_page = "<html><body><p>" + ("word " * 120) + "</p></body></html>"

        def fetcher(url: str):  # noqa: ANN202
            return response(long_page, final_url=url)

        first = self.run_fetch({"url": "https://example.org/long", "max_chars": 210}, fetcher)
        self.assertIn("[truncated] Showing characters 0-210 of 599. Continue with start_char=210.", first)
        self.assertLess(len(first), 1000)
        last = self.run_fetch(
            {"url": "https://example.org/long", "max_chars": 500, "start_char": 210}, fetcher
        )
        self.assertIn("Showing characters 210-599 of 599.", last)
        self.assertNotIn("Continue with", last)
        past = self.run_fetch({"url": "https://example.org/long", "start_char": 5000}, fetcher)
        self.assertIn("is past the end", past)

    def test_rejects_bad_start_char(self) -> None:
        for value in (-1, True, 1.5):
            with self.assertRaises(ToolError):
                self.run_fetch({"url": "https://example.org/", "start_char": value})

    def test_repeat_reads_use_the_cache(self) -> None:
        calls: list[str] = []

        def fetcher(url: str):  # noqa: ANN202
            calls.append(url)
            return response(PAGE_HTML, final_url=url)

        self.run_fetch({"url": "https://example.org/c"}, fetcher)
        self.run_fetch({"url": "https://example.org/c", "start_char": 10}, fetcher)
        self.assertEqual(calls, ["https://example.org/c"])

    def test_error_statuses_are_reported_not_cached(self) -> None:
        calls: list[str] = []

        def fetcher(url: str):  # noqa: ANN202
            calls.append(url)
            return response("<html><body><p>Gone</p></body></html>", status=404, final_url=url)

        output = self.run_fetch({"url": "https://example.org/missing"}, fetcher)
        self.assertIn("(status 404)", output)
        self.run_fetch({"url": "https://example.org/missing"}, fetcher)
        self.assertEqual(len(calls), 2)

    def test_reports_unsupported_content_types(self) -> None:
        output = self.run_fetch(
            {"url": "https://example.org/i.png"},
            lambda url: response(b"\x89PNG\r\n", "image/png", final_url=url),
        )
        self.assertIn("image/png", output)
        self.assertIn("not converted", output)

    def test_flags_size_cap_cutoff(self) -> None:
        big = b"<html><body>" + b"x" * (web_tools.MAX_FETCH_BYTES + 1) + b"</body></html>"
        output = self.run_fetch(
            {"url": "https://example.org/big", "max_chars": 100000},
            lambda url: response(big, "text/html", final_url=url),
        )
        self.assertIn("[response was cut off at the size cap]", output)

    def test_requires_url(self) -> None:
        with self.assertRaises(ToolError):
            self.run_fetch({})
        with self.assertRaises(ToolError):
            self.run_fetch({"url": ""})

    def test_rejects_bad_max_chars_and_format(self) -> None:
        with self.assertRaises(ToolError):
            self.run_fetch({"url": "https://example.org/", "max_chars": False})
        with self.assertRaises(ToolError):
            self.run_fetch({"url": "https://example.org/", "max_chars": 1.5})
        with self.assertRaises(ToolError):
            self.run_fetch({"url": "https://example.org/", "format": "pdf"})

    def test_json_and_plain_text_are_returned_verbatim(self) -> None:
        output = self.run_fetch(
            {"url": "https://example.org/api"},
            lambda url: response(b'{"ok": true, "html": "<b>x</b>"}', "application/json", final_url=url),
        )
        self.assertIn('{"ok": true, "html": "<b>x</b>"}', output)
        plain = self.run_fetch(
            {"url": "https://example.org/notes.txt"},
            lambda url: response(b"a <tag> in text", "text/plain", final_url=url),
        )
        self.assertIn("a <tag> in text", plain)

    def test_fetch_refuses_private_targets_before_any_request(self) -> None:
        with self.assertRaises(ToolError):
            web_tools.run_fetch_page({"url": "http://127.0.0.1:18765/v1/messages"})


class ProtocolTests(unittest.TestCase):
    def test_initialize_reply(self) -> None:
        reply = web_tools.handle_message(
            {"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": {}}
        )
        assert reply is not None
        self.assertEqual(reply["id"], 7)
        result = reply["result"]
        assert isinstance(result, dict)
        self.assertEqual(result["protocolVersion"], web_tools.PROTOCOL_VERSION)
        self.assertEqual(result["serverInfo"]["name"], "airlock-web-tools")

    def test_tools_list_names_both_tools(self) -> None:
        reply = web_tools.handle_message({"jsonrpc": "2.0", "id": "a", "method": "tools/list"})
        assert reply is not None
        names = [tool["name"] for tool in reply["result"]["tools"]]
        self.assertEqual(names, ["web_search", "fetch_page"])

    def test_unknown_method_is_error(self) -> None:
        reply = web_tools.handle_message({"jsonrpc": "2.0", "id": 3, "method": "nope"})
        assert reply is not None
        self.assertEqual(reply["error"]["code"], -32601)

    def test_notifications_return_none(self) -> None:
        self.assertIsNone(
            web_tools.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )

    def test_ping_returns_empty_result(self) -> None:
        reply = web_tools.handle_message({"jsonrpc": "2.0", "id": 9, "method": "ping"})
        assert reply is not None
        self.assertEqual(reply["result"], {})

    def test_tool_call_dispatches_by_name(self) -> None:
        message = {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "fetch_page", "arguments": {"url": "https://example.org/"}},
        }
        web_tools.FETCH_CACHE.clear()
        with mock.patch.object(web_tools, "fetch_response", fake_html_fetch):
            reply = web_tools.handle_message(message)
        assert reply is not None
        self.assertFalse(reply["result"]["isError"])
        self.assertIn("Example Page", reply["result"]["content"][0]["text"])

    def test_tool_errors_come_back_as_iserror(self) -> None:
        message = {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {"name": "web_search", "arguments": {}},
        }
        reply = web_tools.handle_message(message)
        assert reply is not None
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("query", reply["result"]["content"][0]["text"])


class ServeTests(unittest.TestCase):
    def test_serves_lines_until_close_and_skips_garbage(self) -> None:
        lines = "\n".join((
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            "not json at all",
            "",
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ))
        input_stream = io.StringIO(lines + "\n")
        output_stream = io.StringIO()
        exit_code = web_tools.serve(stream_in=input_stream, stream_out=output_stream)
        self.assertEqual(exit_code, 0)
        replies = [
            json.loads(line)
            for line in output_stream.getvalue().splitlines()
            if line.strip()
        ]
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[0]["id"], 1)
        self.assertEqual(replies[1]["id"], 2)
        self.assertEqual([t["name"] for t in replies[1]["result"]["tools"]],
                         ["web_search", "fetch_page"])


    def test_tool_calls_run_concurrently_and_reply_by_id(self) -> None:
        import threading

        both_started = threading.Barrier(2, timeout=5)

        def slow_search(arguments, settings=None):  # noqa: ANN001, ANN202
            del settings
            both_started.wait()
            return "searched " + str(arguments.get("query"))

        lines = "\n".join(
            json.dumps({"jsonrpc": "2.0", "id": ident, "method": "tools/call",
                        "params": {"name": "web_search", "arguments": {"query": ident}}})
            for ident in ("first", "second")
        )
        output_stream = io.StringIO()
        with mock.patch.object(web_tools, "run_web_search", slow_search):
            web_tools.serve(stream_in=io.StringIO(lines + "\n"), stream_out=output_stream)
        replies = {
            reply["id"]: reply["result"]["content"][0]["text"]
            for reply in map(json.loads, output_stream.getvalue().splitlines())
        }
        # Both calls were in flight at once, or the barrier would have timed out.
        self.assertEqual(replies, {"first": "searched first", "second": "searched second"})

    def test_unexpected_tool_failure_stays_a_tool_error(self) -> None:
        with mock.patch.object(web_tools, "run_fetch_page", side_effect=RuntimeError("boom")):
            text, is_error = web_tools.dispatch_tool("fetch_page", {"url": "https://x.example/"})
        self.assertTrue(is_error)
        self.assertIn("RuntimeError: boom", text)


if __name__ == "__main__":
    unittest.main()
