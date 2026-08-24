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


def fake_post(url: str, fields: dict[str, str]) -> tuple[int, str, str]:
    del url, fields
    return 200, "text/html; charset=utf-8", SEARCH_PAGE


def fake_html_get(url: str) -> tuple[int, str, bytes]:
    del url
    return 200, "text/html; charset=utf-8", PAGE_HTML.encode("utf-8")


class RunWebSearchTests(unittest.TestCase):
    def search_with(
        self,
        arguments: dict[str, object],
        poster= fake_post,
    ) -> str:
        with mock.patch.object(web_tools, "http_post_form", poster):
            return web_tools.run_web_search(arguments)

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

    def test_rejects_bad_max_results(self) -> None:
        with self.assertRaises(ToolError):
            self.search_with({"query": "q", "max_results": True})
        with self.assertRaises(ToolError):
            self.search_with({"query": "q", "max_results": "many"})

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


class RunFetchPageTests(unittest.TestCase):
    def run_fetch(self, arguments: dict[str, object]) -> str:
        with mock.patch.object(web_tools, "http_get", fake_html_get):
            return web_tools.run_fetch_page(arguments)

    def test_returns_note_title_and_text(self) -> None:
        output = self.run_fetch({"url": "https://example.org/page"})
        self.assertTrue(output.startswith("Fetched https://example.org/page (status 200)."))
        self.assertIn("Page title: Example Page", output)
        self.assertIn("First paragraph with & entity.", output)

    def test_truncates_long_text(self) -> None:
        long_page = (
            "<html><head><title>Long</title></head><body><p>"
            + ("word " * 120)
            + "</p></body></html>"
        ).encode("utf-8")

        def get(url: str) -> tuple[int, str, bytes]:
            del url
            return 200, "text/html", long_page

        with mock.patch.object(web_tools, "http_get", get):
            output = web_tools.run_fetch_page(
                {"url": "https://example.org/long", "max_chars": 210}
            )
        self.assertIn("[truncated]", output)
        self.assertLess(len(output), 1000)

    def test_reports_unsupported_content_types(self) -> None:
        def get(url: str) -> tuple[int, str, bytes]:
            del url
            return 200, "image/png", b"\x89PNG\r\n"

        with mock.patch.object(web_tools, "http_get", get):
            output = web_tools.run_fetch_page({"url": "https://example.org/i.png"})
        self.assertIn("image/png", output)
        self.assertIn("not converted", output)

    def test_flags_size_cap_cutoff(self) -> None:
        big = b"<html><body>" + b"x" * (web_tools.MAX_FETCH_BYTES + 1) + b"</body></html>"

        def get(url: str) -> tuple[int, str, bytes]:
            del url
            return 200, "text/html", big

        with mock.patch.object(web_tools, "http_get", get):
            output = web_tools.run_fetch_page(
                {"url": "https://example.org/big", "max_chars": 100000}
            )
        self.assertIn("[response was cut off at the size cap]", output)

    def test_requires_url(self) -> None:
        with self.assertRaises(ToolError):
            self.run_fetch({})
        with self.assertRaises(ToolError):
            self.run_fetch({"url": ""})

    def test_rejects_bad_max_chars(self) -> None:
        with self.assertRaises(ToolError):
            self.run_fetch({"url": "https://example.org/", "max_chars": False})
        with self.assertRaises(ToolError):
            self.run_fetch({"url": "https://example.org/", "max_chars": 1.5})

    def test_json_content_is_returned_verbatim(self) -> None:
        def get(url: str) -> tuple[int, str, bytes]:
            del url
            return 200, "application/json", b'{"ok": true}'

        with mock.patch.object(web_tools, "http_get", get):
            output = web_tools.run_fetch_page({"url": "https://example.org/api"})
        self.assertIn('{"ok": true}', output)


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
        with mock.patch.object(web_tools, "http_get", fake_html_get):
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


if __name__ == "__main__":
    unittest.main()
