#!/usr/bin/env python3
"""Offline tests for open-model registry management."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
SPEC = importlib.util.spec_from_file_location(
    "airlock_openmodel_test", BIN / "airlock_openmodel.py"
)
assert SPEC is not None and SPEC.loader is not None
openmodel = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = openmodel
SPEC.loader.exec_module(openmodel)
import airlock_policy as policy  # noqa: E402

PRIVATE_MODEL = "D:/Private Models/Synthetic Model.gguf"


def declared_registry() -> dict[str, object]:
    return {
        "schema_version": 1,
        "endpoints": [{
            "id": "desk-llama",
            "base_url": "http://127.0.0.1:18093/v1",
            "trust": "loopback",
            "protocol": "openai-chat-completions-v1",
            "auth": "none",
            "max_concurrency": 1,
            "enabled": True,
        }],
        "models": [{
            "route": "synthetic-route",
            "endpoint": "desk-llama",
            "upstream_model": PRIVATE_MODEL,
            "accepted_response_models": [PRIVATE_MODEL],
            "context_window": 32768,
            "max_output_tokens": 8192,
            "streaming": True,
            "tools": "single",
            "tool_choice": ["auto"],
            "worker": True,
            "enabled": True,
        }],
    }


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            **(headers or {}),
        }
        self.read_limit: int | None = None

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name, default)

    def read(self, limit: int) -> bytes:
        self.read_limit = limit
        return self.body[:limit]


class FakeConnection:
    response = FakeResponse(b'{"data":[]}')
    instances: list["FakeConnection"] = []

    def __init__(self, host: str, port: int, *, timeout: int) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.request_args: tuple[object, ...] | None = None
        self.request_kwargs: dict[str, object] | None = None
        self.closed = False
        self.__class__.instances.append(self)

    def request(self, *args: object, **kwargs: object) -> None:
        self.request_args = args
        self.request_kwargs = kwargs

    def getresponse(self) -> FakeResponse:
        return self.__class__.response

    def close(self) -> None:
        self.closed = True


class ManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeConnection.instances.clear()

    def test_add_and_list_commands_never_print_private_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openmodel-registry.json"
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    openmodel.add_endpoint(
                        path,
                        "desk-llama",
                        "http://127.0.0.1:18093/v1",
                        max_concurrency=1,
                        enabled=True,
                    ),
                    0,
                )
                self.assertEqual(
                    openmodel.add_route(
                        path,
                        "synthetic-route",
                        "desk-llama",
                        PRIVATE_MODEL,
                        accepted_response_models=[PRIVATE_MODEL],
                        context_window=32768,
                        max_output_tokens=8192,
                        streaming=True,
                        tools="single",
                        tool_choice=["auto"],
                        worker=True,
                        enabled=True,
                    ),
                    0,
                )
                openmodel.list_endpoints(path)
                openmodel.list_routes(path)
            text = output.getvalue()
            self.assertIn("desk-llama", text)
            self.assertIn("synthetic-route", text)
            self.assertNotIn("127.0.0.1", text)
            self.assertNotIn(PRIVATE_MODEL, text)
            registry = policy.load_openmodel_registry(path)
            self.assertEqual(registry.models[0].upstream_model, PRIVATE_MODEL)

    def test_main_reads_private_add_values_only_from_bounded_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openmodel-registry.json"
            endpoint_args = [
                "--registry",
                str(path),
                "endpoint",
                "add",
                "desk-llama",
                "--stdin",
                "--max-concurrency",
                "1",
            ]
            endpoint_stdin = mock.Mock()
            endpoint_stdin.buffer = io.BytesIO(json.dumps({
                "base_url": "http://127.0.0.1:18093/v1",
            }).encode("utf-8"))
            output = io.StringIO()
            with (
                mock.patch.object(openmodel.sys, "stdin", endpoint_stdin),
                redirect_stdout(output),
            ):
                self.assertEqual(openmodel.main(endpoint_args), 0)

            route_args = [
                "--registry",
                str(path),
                "add",
                "synthetic-route",
                "desk-llama",
                "--stdin",
                "--context-window",
                "32768",
                "--max-output-tokens",
                "8192",
                "--streaming",
                "--tools",
                "single",
                "--tool-choice",
                "auto",
                "--worker",
            ]
            route_stdin = mock.Mock()
            route_stdin.buffer = io.BytesIO(json.dumps({
                "upstream_model": PRIVATE_MODEL,
                "accepted_response_models": [PRIVATE_MODEL],
            }).encode("utf-8"))
            with (
                mock.patch.object(openmodel.sys, "stdin", route_stdin),
                redirect_stdout(output),
            ):
                self.assertEqual(openmodel.main(route_args), 0)

            command_text = " ".join(endpoint_args + route_args)
            self.assertNotIn("127.0.0.1", command_text)
            self.assertNotIn(PRIVATE_MODEL, command_text)
            self.assertNotIn("127.0.0.1", output.getvalue())
            self.assertNotIn(PRIVATE_MODEL, output.getvalue())
            registry = policy.load_openmodel_registry(path)
            self.assertEqual(registry.endpoints[0].base_url, "http://127.0.0.1:18093/v1")
            self.assertEqual(registry.models[0].upstream_model, PRIVATE_MODEL)
            self.assertEqual(
                registry.models[0].accepted_response_models, (PRIVATE_MODEL,)
            )

    def test_private_stdin_is_bounded_exact_and_sanitized(self) -> None:
        endpoint_cases = (
            b"{",
            b"\xff",
            json.dumps({
                "base_url": "http://127.0.0.1:18093/v1",
                "private-extra": PRIVATE_MODEL,
            }).encode("utf-8"),
            b'{"base_url":"first","base_url":"second"}',
            b"x" * (openmodel.MAX_PRIVATE_INPUT_BYTES + 1),
        )
        for raw in endpoint_cases:
            with self.subTest(raw_size=len(raw)):
                with self.assertRaises(openmodel.OpenModelError) as caught:
                    openmodel.endpoint_private_input(
                        from_stdin=True, input_stream=io.BytesIO(raw)
                    )
                self.assertNotIn(PRIVATE_MODEL, str(caught.exception))
                self.assertNotIn("127.0.0.1", str(caught.exception))

        route_cases = (
            json.dumps({"upstream_model": PRIVATE_MODEL}).encode("utf-8"),
            json.dumps({
                "upstream_model": PRIVATE_MODEL,
                "accepted_response_models": "not-an-array",
            }).encode("utf-8"),
            json.dumps({
                "upstream_model": PRIVATE_MODEL,
                "accepted_response_models": [PRIVATE_MODEL, PRIVATE_MODEL],
            }).encode("utf-8"),
        )
        for raw in route_cases:
            with self.subTest(raw=raw[:30]):
                with self.assertRaises(openmodel.OpenModelError) as caught:
                    openmodel.route_private_input(
                        from_stdin=True, input_stream=io.BytesIO(raw)
                    )
                self.assertNotIn(PRIVATE_MODEL, str(caught.exception))

    def test_private_stdin_tolerates_a_utf8_byte_order_mark(self) -> None:
        # Windows PowerShell 5.1 pipes strings into native commands with a
        # UTF-8 BOM on a UTF-8 console; the JSON after it must still be read.
        body = json.dumps({"base_url": "http://127.0.0.1:18093/v1"}).encode("utf-8")
        plain = openmodel.endpoint_private_input(
            from_stdin=True, input_stream=io.BytesIO(body + b"\r\n")
        )
        with_bom = openmodel.endpoint_private_input(
            from_stdin=True, input_stream=io.BytesIO(b"\xef\xbb\xbf" + body + b"\r\n")
        )
        self.assertEqual(with_bom, plain)
        # Only a leading mark is data-free; one inside the payload is still bad input.
        with self.assertRaises(openmodel.OpenModelError):
            openmodel.endpoint_private_input(
                from_stdin=True, input_stream=io.BytesIO(b"{\xef\xbb\xbf" + body[1:])
            )

    def test_hidden_private_input_defaults_response_identity_to_upstream(self) -> None:
        with mock.patch.object(
            openmodel, "_read_hidden", side_effect=[PRIVATE_MODEL, ""]
        ):
            upstream, accepted = openmodel.route_private_input(from_stdin=False)
        self.assertEqual(upstream, PRIVATE_MODEL)
        self.assertEqual(accepted, [PRIVATE_MODEL])

    def test_hidden_character_collection_is_bounded_while_reading(self) -> None:
        edited = iter("private-typo\x08e\n")
        self.assertEqual(
            openmodel._collect_hidden_input(lambda: next(edited)),
            "private-type",
        )

        exact = iter(
            "\U0001f642" * openmodel.MAX_HIDDEN_INPUT_CHARS + "\n"
        )
        self.assertEqual(
            len(openmodel._collect_hidden_input(lambda: next(exact))),
            openmodel.MAX_HIDDEN_INPUT_CHARS,
        )

        oversized = iter(
            "x" * (openmodel.MAX_HIDDEN_INPUT_CHARS + 3) + "\nAFTER"
        )
        with self.assertRaisesRegex(
            openmodel.OpenModelError, "private input is too large"
        ):
            openmodel._collect_hidden_input(lambda: next(oversized))
        self.assertEqual(next(oversized), "A")

        malformed = iter(["two characters", "\n"])
        with self.assertRaisesRegex(
            openmodel.OpenModelError, "private input was not accepted"
        ):
            openmodel._collect_hidden_input(lambda: next(malformed))

    def test_noninteractive_private_input_requires_stdin_mode(self) -> None:
        input_stream = mock.Mock()
        input_stream.isatty.return_value = False
        error_stream = mock.Mock()
        error_stream.isatty.return_value = True
        with (
            mock.patch.object(openmodel.sys, "stdin", input_stream),
            mock.patch.object(openmodel.sys, "stderr", error_stream),
            self.assertRaisesRegex(openmodel.OpenModelError, "--stdin"),
        ):
            openmodel.endpoint_private_input(from_stdin=False)

    def test_route_capabilities_are_not_inferred(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openmodel-registry.json"
            openmodel.add_endpoint(
                path,
                "desk-llama",
                "http://127.0.0.1:18093/v1",
                max_concurrency=1,
                enabled=True,
            )
            with self.assertRaisesRegex(openmodel.OpenModelError, "must declare auto"):
                openmodel.add_route(
                    path,
                    "synthetic-route",
                    "desk-llama",
                    PRIVATE_MODEL,
                    accepted_response_models=[PRIVATE_MODEL],
                    context_window=32768,
                    max_output_tokens=8192,
                    streaming=True,
                    tools="single",
                    tool_choice=[],
                    worker=True,
                    enabled=True,
                )

    def test_duplicate_route_capabilities_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openmodel-registry.json"
            openmodel.add_endpoint(
                path,
                "desk-llama",
                "http://127.0.0.1:18093/v1",
                max_concurrency=1,
                enabled=True,
            )
            arguments = {
                "context_window": 32768,
                "max_output_tokens": 8192,
                "streaming": True,
                "tools": "single",
                "worker": True,
                "enabled": True,
            }
            with self.assertRaisesRegex(
                openmodel.OpenModelError, "identities must not contain duplicates"
            ):
                openmodel.add_route(
                    path,
                    "synthetic-route",
                    "desk-llama",
                    PRIVATE_MODEL,
                    accepted_response_models=[PRIVATE_MODEL, PRIVATE_MODEL],
                    tool_choice=["auto"],
                    **arguments,
                )
            with self.assertRaisesRegex(
                openmodel.OpenModelError, "modes must not contain duplicates"
            ):
                openmodel.add_route(
                    path,
                    "synthetic-route",
                    "desk-llama",
                    PRIVATE_MODEL,
                    accepted_response_models=[PRIVATE_MODEL],
                    tool_choice=["auto", "auto"],
                    **arguments,
                )
            self.assertEqual(policy.load_openmodel_registry(path).models, ())

    def test_endpoint_removal_refuses_references_then_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openmodel-registry.json"
            policy.write_openmodel_registry(path, declared_registry())
            with self.assertRaisesRegex(openmodel.OpenModelError, "still used"):
                openmodel.remove_endpoint(path, "desk-llama", approved=True)
            output = io.StringIO()
            with redirect_stdout(output):
                openmodel.remove_route(path, "synthetic-route", approved=True)
                openmodel.remove_endpoint(path, "desk-llama", approved=True)
            registry = policy.load_openmodel_registry(path)
            self.assertEqual(registry.models, ())
            self.assertEqual(registry.endpoints, ())

    def test_noninteractive_remove_requires_yes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openmodel-registry.json"
            policy.write_openmodel_registry(path, declared_registry())
            stream = mock.Mock()
            stream.isatty.return_value = False
            with self.assertRaisesRegex(openmodel.OpenModelError, "--yes"):
                openmodel.confirm("Remove?", approved=False, input_stream=stream)

    def test_check_uses_safe_output_and_exact_configured_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openmodel-registry.json"
            policy.write_openmodel_registry(path, declared_registry())
            output = io.StringIO()
            with redirect_stdout(output):
                result = openmodel.check_route(
                    path,
                    "synthetic-route",
                    fetch=lambda _url: ("another", PRIVATE_MODEL),
                )
            self.assertEqual(result, 0)
            text = output.getvalue()
            self.assertIn("synthetic-route", text)
            self.assertNotIn(PRIVATE_MODEL, text)
            self.assertNotIn("127.0.0.1", text)
            with self.assertRaises(openmodel.OpenModelError) as caught:
                openmodel.check_route(
                    path,
                    "synthetic-route",
                    fetch=lambda _url: ("different",),
                )
            self.assertNotIn(PRIVATE_MODEL, str(caught.exception))

    def test_check_refuses_disabled_or_unknown_routes_without_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openmodel-registry.json"
            value = declared_registry()
            value["models"][0]["enabled"] = False  # type: ignore[index]
            policy.write_openmodel_registry(path, value)
            fetch = mock.Mock(side_effect=AssertionError("must stay offline"))
            with self.assertRaisesRegex(openmodel.OpenModelError, "disabled"):
                openmodel.check_route(path, "synthetic-route", fetch=fetch)
            with self.assertRaisesRegex(openmodel.OpenModelError, "does not exist"):
                openmodel.check_route(path, "missing", fetch=fetch)
            fetch.assert_not_called()

    def test_catalog_fetch_is_fixed_bounded_credential_free_and_proxy_free(self) -> None:
        body = json.dumps({"data": [{"id": PRIVATE_MODEL}]}).encode("utf-8")
        FakeConnection.response = FakeResponse(body)
        identities = openmodel.fetch_catalog_ids(
            "http://127.0.0.1:18093/v1",
            connection_factory=FakeConnection,
            timeout=7,
        )
        self.assertEqual(identities, (PRIVATE_MODEL,))
        connection = FakeConnection.instances[-1]
        self.assertEqual((connection.host, connection.port), ("127.0.0.1", 18093))
        self.assertEqual(connection.timeout, 7)
        self.assertEqual(connection.request_args, ("GET", "/v1/models"))
        headers = connection.request_kwargs["headers"]  # type: ignore[index]
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["Accept-Encoding"], "identity")
        self.assertTrue(connection.closed)
        self.assertEqual(
            FakeConnection.response.read_limit,
            openmodel.MAX_CATALOG_BYTES + 1,
        )

    def test_catalog_fetch_rejects_redirect_shape_encoding_and_oversize(self) -> None:
        cases = [
            FakeResponse(b"", status=302),
            FakeResponse(b"not json"),
            FakeResponse(b"{}"),
            FakeResponse(b'{"data":{}}'),
            FakeResponse(b'{"data":[{}]}'),
            FakeResponse(
                b'{"data":[]}', headers={"Content-Encoding": "gzip"}
            ),
            FakeResponse(
                b'{"data":[]}', headers={"Content-Type": "text/plain"}
            ),
            FakeResponse(
                b'{"data":[]}',
                headers={"Content-Length": str(openmodel.MAX_CATALOG_BYTES + 1)},
            ),
        ]
        for response in cases:
            with self.subTest(status=response.status, body=response.body[:20]):
                FakeConnection.response = response
                with self.assertRaises(openmodel.OpenModelError):
                    openmodel.fetch_catalog_ids(
                        "http://127.0.0.1:18093/v1",
                        connection_factory=FakeConnection,
                    )

    def test_doctor_is_offline_and_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openmodel-registry.json"
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(openmodel.doctor_status(path), 0)
            self.assertEqual(output.getvalue(), "STATE=absent\n")
            policy.write_openmodel_registry(path, declared_registry())
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(openmodel.doctor_status(path), 0)
            text = output.getvalue()
            self.assertIn("STATE=valid", text)
            self.assertIn("ACTIVE_ENDPOINT_COUNT=1", text)
            self.assertIn("ACTIVE_ROUTE_COUNT=1", text)
            self.assertIn("ROUTE=synthetic-route", text)
            self.assertNotIn(PRIVATE_MODEL, text)
            self.assertNotIn("127.0.0.1", text)

            path.write_text(
                json.dumps({
                    "schema_version": 1,
                    "endpoints": [],
                    "models": [],
                    PRIVATE_MODEL: "must stay private",
                }),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(openmodel.doctor_status(path), 2)
            self.assertEqual(
                output.getvalue(),
                "STATE=invalid\nDETAIL=registry failed validation\n",
            )
            self.assertNotIn(PRIVATE_MODEL, output.getvalue())

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(
                    openmodel.main(["--registry", str(path), "list"]),
                    2,
                )
            self.assertIn("open-model registry is invalid", stderr.getvalue())
            self.assertNotIn(PRIVATE_MODEL, stderr.getvalue())

    def test_main_requires_all_capability_switches_and_sanitizes_errors(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = openmodel.main(["--registry", "unused", "add", "route"])
        self.assertEqual(result, 2)
        self.assertIn("usage:", stderr.getvalue())
        self.assertNotIn(PRIVATE_MODEL, stderr.getvalue())

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = openmodel.main([
                "--registry",
                "unused",
                "list",
                PRIVATE_MODEL,
            ])
        self.assertEqual(result, 2)
        self.assertIn("invalid arguments", stderr.getvalue())
        self.assertNotIn(PRIVATE_MODEL, stderr.getvalue())

        for legacy in (
            [
                "--registry", "unused", "endpoint", "add", "desk-llama",
                "http://127.0.0.1:18093/v1", "--max-concurrency", "1",
            ],
            [
                "--registry", "unused", "add", "synthetic-route", "desk-llama",
                PRIVATE_MODEL, "--context-window", "32768",
                "--max-output-tokens", "8192", "--streaming", "--tools",
                "single", "--tool-choice", "auto", "--worker",
            ],
        ):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = openmodel.main(legacy)
            self.assertEqual(result, 2)
            self.assertIn("invalid arguments", stderr.getvalue())
            self.assertNotIn(PRIVATE_MODEL, stderr.getvalue())
            self.assertNotIn("127.0.0.1", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
