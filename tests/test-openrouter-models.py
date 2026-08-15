#!/usr/bin/env python3
"""Offline tests for OpenRouter registry-management commands."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from urllib import error

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
SPEC = importlib.util.spec_from_file_location(
    "airlock_openrouter_models", BIN / "airlock_openrouter_models.py"
)
assert SPEC is not None and SPEC.loader is not None
models = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = models
SPEC.loader.exec_module(models)
policy = models.policy
presets = models.presets

NOW = 1_786_320_000
MODEL = "anthropic/claude-sonnet-4.5"
CANONICAL_SLUG = "anthropic/claude-sonnet-4.5-20250929"
ENDPOINT = "deepinfra/turbo"
PROVIDER_NAME = "DeepInfra"
PROVIDER_SLUG = "deepinfra"
QUANTIZATION = "fp8"

# Frozen curated presets: exact name/route/model/canonical/endpoint + evidence.
EXPECTED_PRESETS = (
    {
        "name": "kimi-k3",
        "route": "kimi-k3",
        "model": "moonshotai/kimi-k3",
        "canonical_slug": "moonshotai/kimi-k3-20260715",
        "endpoint_provider": "digitalocean",
        "provider_name": "DigitalOcean",
        "provider_slug": "digitalocean",
        "quantization": "unknown",
        "evidence_date": "2026-08-10",
    },
    {
        "name": "deepseek-v4-flash-0731",
        "route": "deepseek-v4-flash-0731",
        "model": "deepseek/deepseek-v4-flash-0731",
        "canonical_slug": "deepseek/deepseek-v4-flash-20260731",
        "endpoint_provider": "deepinfra/fp4",
        "provider_name": "DeepInfra",
        "provider_slug": "deepinfra",
        "quantization": "fp4",
        "evidence_date": "2026-08-10",
    },
    {
        "name": "qwen-3-6-27b",
        "route": "qwen-3-6-27b",
        "model": "qwen/qwen3.6-27b",
        "canonical_slug": "qwen/qwen3.6-27b-20260422",
        "endpoint_provider": "chutes/fp8",
        "provider_name": "Chutes",
        "provider_slug": "chutes",
        "quantization": "fp8",
        "evidence_date": "2026-08-10",
    },
)


def selection(
    parameters: tuple[str, ...] = ("tool_choice", "tools"),
    *,
    expiration_date: str | None = None,
    model: str = MODEL,
    endpoint_provider: str = ENDPOINT,
    provider_name: str = PROVIDER_NAME,
    provider_slug: str = PROVIDER_SLUG,
    quantization: str = QUANTIZATION,
    canonical_slug: str | None = None,
) -> models.CatalogSelection:
    return models.CatalogSelection(
        model=model,
        endpoint_provider=endpoint_provider,
        provider_name=provider_name,
        provider_slug=provider_slug,
        quantization=quantization,
        canonical_slug=canonical_slug or (
            CANONICAL_SLUG if model == MODEL else model
        ),
        supported_parameters=parameters,
        expiration_date=expiration_date,
    )


def entry(
    route: str = "sonnet",
    *,
    checked_at: int = NOW,
    expiration_date: str | None = None,
) -> dict[str, object]:
    return selection().entry(route, checked_at) | {
        "expiration_date": expiration_date,
    }


def registry(*entries: dict[str, object]) -> dict[str, object]:
    return {"schema_version": 1, "models": list(entries)}


class FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200, headers: dict[str, str] | None = None):
        self.body = body
        self.status = status
        self.headers = Message()
        for name, value in (headers or {"Content-Type": "application/json"}).items():
            self.headers[name] = value

    def getcode(self) -> int:
        return self.status

    def read(self, limit: int) -> bytes:
        return self.body[:limit]

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeOpener:
    def __init__(self, result: object):
        self.result = result
        self.requests: list[object] = []

    def open(self, requested: object, timeout: int) -> object:
        self.requests.append((requested, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class CatalogFetchTests(unittest.TestCase):
    def test_catalog_fetch_is_pinned_bounded_and_credential_free(self) -> None:
        response = FakeResponse(b'{"data":{}}')
        opener = FakeOpener(response)
        result = models.fetch_catalog_json("/model/vendor/model", opener=opener)
        self.assertEqual(result, {"data": {}})
        requested, timeout = opener.requests[0]
        self.assertEqual(requested.full_url, "https://openrouter.ai/api/v1/model/vendor/model")
        self.assertEqual(timeout, models.NETWORK_TIMEOUT_SECONDS)
        headers = {name.lower(): value for name, value in requested.header_items()}
        self.assertEqual(headers["accept-encoding"], "identity")
        self.assertNotIn("authorization", headers)
        self.assertNotIn("x-api-key", headers)

    def test_catalog_fetch_rejects_redirect_compression_type_size_and_duplicates(self) -> None:
        redirected = error.HTTPError(
            "https://openrouter.ai/api/v1/model/v/m",
            302,
            "redirect",
            Message(),
            None,
        )
        cases = [
            FakeOpener(redirected),
            FakeOpener(FakeResponse(b"{}", headers={
                "Content-Type": "application/json", "Content-Encoding": "gzip"
            })),
            FakeOpener(FakeResponse(b"{}", headers={"Content-Type": "text/html"})),
            FakeOpener(FakeResponse(b"{}", headers={
                "Content-Type": "application/json",
                "Content-Length": str(models.MAX_CATALOG_BYTES + 1),
            })),
            FakeOpener(FakeResponse(b'{"data":{},"data":{}}')),
        ]
        for opener in cases:
            with self.subTest(index=cases.index(opener)):
                with self.assertRaises(models.ModelsError):
                    models.fetch_catalog_json("/model/v/m", opener=opener)

    def test_catalog_url_rejects_unpinned_base(self) -> None:
        original = models.OPENROUTER_API_BASE
        try:
            models.OPENROUTER_API_BASE = "http://127.0.0.1:8000/api/v1"
            with self.assertRaises(models.ModelsError):
                models.fetch_catalog_json("/model/v/m", opener=FakeOpener(FakeResponse(b"{}")))
        finally:
            models.OPENROUTER_API_BASE = original


class SelectionTests(unittest.TestCase):
    def payloads(self, **model_changes: object) -> dict[str, object]:
        model_data: dict[str, object] = {
            "id": MODEL,
            "canonical_slug": CANONICAL_SLUG,
            "alias_target": None,
            "supported_parameters": ["tools", "temperature", "tool_choice"],
            "expiration_date": None,
        }
        model_data.update(model_changes)
        return {
            "/model/anthropic/claude-sonnet-4.5": {"data": model_data},
            "/models/anthropic/claude-sonnet-4.5/endpoints": {
                "data": {
                    "id": MODEL,
                    "endpoints": [{
                    "tag": ENDPOINT,
                    "provider_name": PROVIDER_NAME,
                    "quantization": QUANTIZATION,
                    "model_id": MODEL,
                    "supported_parameters": ["tool_choice", "tools", "top_p"],
                }]}
            },
            "/providers": {
                "data": [{"name": PROVIDER_NAME, "slug": PROVIDER_SLUG}]
            },
        }

    def test_routable_identity_provenance_and_endpoint_are_verified(self) -> None:
        payloads = self.payloads()
        seen: list[str] = []

        def fetch(path: str) -> object:
            seen.append(path)
            return payloads[path]

        result = models.verify_catalog_selection(MODEL, ENDPOINT, fetch=fetch)
        self.assertEqual(result.model, MODEL)
        self.assertEqual(result.canonical_slug, CANONICAL_SLUG)
        self.assertEqual(result.endpoint_provider, ENDPOINT)
        self.assertEqual(result.provider_name, PROVIDER_NAME)
        self.assertEqual(result.provider_slug, PROVIDER_SLUG)
        self.assertEqual(result.quantization, QUANTIZATION)
        self.assertEqual(result.supported_parameters, ("tool_choice", "tools"))
        self.assertEqual(seen, list(payloads))

    def test_provider_and_quantization_must_identify_one_endpoint(self) -> None:
        payloads = self.payloads()
        endpoint_data = payloads[
            "/models/anthropic/claude-sonnet-4.5/endpoints"
        ]["data"]
        endpoint_data["endpoints"].append({
            "tag": "deepinfra/second",
            "provider_name": PROVIDER_NAME,
            "quantization": QUANTIZATION,
            "model_id": MODEL,
            "supported_parameters": ["tool_choice", "tools"],
        })
        with self.assertRaisesRegex(models.ModelsError, "pinned uniquely"):
            models.verify_catalog_selection(
                MODEL,
                ENDPOINT,
                fetch=lambda path: payloads[path],
            )

    def test_provider_registry_name_to_slug_mapping_is_exact_and_unique(self) -> None:
        cases = []
        missing = self.payloads()
        missing["/providers"] = {"data": [{"name": "Other", "slug": "other"}]}
        cases.append(missing)
        duplicate = self.payloads()
        duplicate["/providers"] = {
            "data": [
                {"name": PROVIDER_NAME, "slug": PROVIDER_SLUG},
                {"name": PROVIDER_NAME, "slug": "deepinfra-second"},
            ]
        }
        cases.append(duplicate)
        malformed = self.payloads()
        malformed["/providers"] = {
            "data": [{"name": PROVIDER_NAME, "slug": "Deep Infra"}]
        }
        cases.append(malformed)
        for payloads in cases:
            with self.subTest(providers=payloads["/providers"]):
                with self.assertRaises(models.ModelsError):
                    models.verify_catalog_selection(
                        MODEL,
                        ENDPOINT,
                        fetch=lambda path, values=payloads: values[path],
                    )

    def test_real_deepseek_routable_id_and_canonical_slug_shape_is_accepted(self) -> None:
        model = "deepseek/deepseek-v4-flash-0731"
        canonical_slug = "deepseek/deepseek-v4-flash-20260731"
        endpoint = "streamlake"
        payloads = {
            "/model/deepseek/deepseek-v4-flash-0731": {
                "data": {
                    "id": model,
                    "canonical_slug": canonical_slug,
                    "alias_target": None,
                    "supported_parameters": ["tool_choice", "tools"],
                    "expiration_date": None,
                }
            },
            "/models/deepseek/deepseek-v4-flash-0731/endpoints": {
                "data": {
                    "id": model,
                    "endpoints": [{
                        "tag": endpoint,
                        "provider_name": "StreamLake",
                        "quantization": "fp8",
                        "model_id": model,
                        "supported_parameters": ["tool_choice", "tools"],
                    }],
                }
            },
            "/providers": {
                "data": [{"name": "StreamLake", "slug": "streamlake"}]
            },
        }
        result = models.verify_catalog_selection(
            model,
            endpoint,
            fetch=lambda path: payloads[path],
        )
        self.assertEqual(result.model, model)
        self.assertEqual(result.canonical_slug, canonical_slug)

    def test_alias_target_omitted_or_null_is_accepted(self) -> None:
        for payloads in (
            self.payloads(alias_target=None),
            (
                lambda value: (
                    value["/model/anthropic/claude-sonnet-4.5"]["data"].pop(
                        "alias_target"
                    ),
                    value,
                )[1]
            )(self.payloads()),
        ):
            with self.subTest(payloads=payloads):
                result = models.verify_catalog_selection(
                    MODEL,
                    ENDPOINT,
                    fetch=lambda path, values=payloads: values[path],
                )
                self.assertEqual(result.model, MODEL)
                self.assertEqual(result.canonical_slug, CANONICAL_SLUG)

    def test_alias_identity_endpoint_and_tools_fail_closed(self) -> None:
        cases = [
            self.payloads(alias_target="anthropic/newer"),
            self.payloads(canonical_slug="anthropic/Bad Slug"),
            self.payloads(id="anthropic/other"),
            self.payloads(supported_parameters=["tools"]),
        ]
        missing_endpoint = self.payloads()
        missing_endpoint["/models/anthropic/claude-sonnet-4.5/endpoints"] = {
            "data": {"id": MODEL, "endpoints": []}
        }
        cases.append(missing_endpoint)
        wrong_endpoint_id = self.payloads()
        wrong_endpoint_id["/models/anthropic/claude-sonnet-4.5/endpoints"][
            "data"
        ]["id"] = "anthropic/other"
        cases.append(wrong_endpoint_id)
        wrong_model = self.payloads()
        wrong_model["/models/anthropic/claude-sonnet-4.5/endpoints"] = {
            "data": {
                "id": MODEL,
                "endpoints": [{
                    "tag": ENDPOINT,
                    "model_id": "anthropic/other",
                    "supported_parameters": ["tool_choice", "tools"],
                }],
            }
        }
        cases.append(wrong_model)
        for index, payloads in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaises(models.ModelsError):
                    models.verify_catalog_selection(
                        MODEL, ENDPOINT, fetch=lambda path, values=payloads: values[path]
                    )


class RegistryCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "openrouter-registry.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, value: dict[str, object], *, require_fresh: bool = True) -> None:
        policy.write_openrouter_registry(
            self.path,
            value,
            now=NOW,
            require_fresh=require_fresh,
        )

    def test_curated_presets_are_frozen_and_unique(self) -> None:
        actual = tuple({
            "name": item.name,
            "route": item.route,
            "model": item.model,
            "canonical_slug": item.canonical_slug,
            "endpoint_provider": item.endpoint_provider,
            "provider_name": item.provider_name,
            "provider_slug": item.provider_slug,
            "quantization": item.quantization,
            "evidence_date": item.evidence_date,
        } for item in presets.PRESETS)
        self.assertEqual(actual, EXPECTED_PRESETS)
        self.assertEqual(set(presets.PRESETS_BY_NAME), {
            item["name"] for item in EXPECTED_PRESETS
        })
        self.assertEqual(set(presets.PRESETS_BY_MODEL), {
            item["model"] for item in EXPECTED_PRESETS
        })

    def test_presets_listing_is_offline_stateless_and_cautious(self) -> None:
        output = io.StringIO()
        with mock.patch.object(
            models,
            "fetch_catalog_json",
            side_effect=AssertionError("network access is not allowed"),
        ), redirect_stdout(output):
            self.assertEqual(models.list_presets(), 0)
        rendered = output.getvalue()
        self.assertFalse(self.path.exists())
        self.assertIn("opt-in", rendered)
        self.assertIn("not enabled by setup", rendered)
        self.assertIn("community-derived, unverified", rendered)
        self.assertIn("not capability, price, or availability guarantees", rendered)
        for expected in EXPECTED_PRESETS:
            self.assertIn(
                f"{expected['name']}\t{expected['model']}\t"
                f"{expected['endpoint_provider']}\t"
                f"{expected['provider_name']}\t{expected['provider_slug']}\t"
                f"{expected['quantization']}\t"
                f"evidence {expected['evidence_date']}",
                rendered,
            )
            preset = presets.preset_by_name(expected["name"])
            assert preset is not None
            self.assertIn(f"Suggested use: {preset.suggested_use}", rendered)
            self.assertIn(f"Tradeoffs: {preset.tradeoffs}", rendered)
        self.assertNotIn("cheapest", rendered.lower())

    def test_list_is_offline_and_can_show_stale_entries(self) -> None:
        stale = entry(checked_at=NOW - 31 * 24 * 60 * 60)
        self.write(registry(stale), require_fresh=False)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(models.list_models(self.path), 0)
        self.assertIn(
            f"sonnet\t{MODEL}\t{ENDPOINT}\t{PROVIDER_NAME}\t"
            f"{PROVIDER_SLUG}\t{QUANTIZATION}\tyes",
            output.getvalue(),
        )

    def test_doctor_status_is_offline_sanitized_and_reports_freshness(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(models.doctor_status(self.path, now_fn=lambda: NOW), 0)
        self.assertEqual(output.getvalue(), "STATE=absent\n")

        self.write(registry(entry()))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(models.doctor_status(self.path, now_fn=lambda: NOW), 0)
        self.assertEqual(
            output.getvalue(),
            "STATE=valid\nCOUNT=1\n"
            f"MODEL=sonnet\t{MODEL}\t{ENDPOINT}\tenabled\n",
        )

        stale = entry(checked_at=NOW - 31 * 24 * 60 * 60)
        self.write(registry(stale), require_fresh=False)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(models.doctor_status(self.path, now_fn=lambda: NOW), 0)
        self.assertIn("STATE=stale\nCOUNT=1\n", output.getvalue())
        self.assertIn("DETAIL=models[0].checked_at is older than 30 days", output.getvalue())

    def test_doctor_status_rejects_invalid_registry(self) -> None:
        self.path.write_text('{"schema_version":1,"models":[],"extra":true}\n')
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(models.doctor_status(self.path, now_fn=lambda: NOW), 2)
        self.assertIn("STATE=invalid\n", output.getvalue())
        self.assertIn("DETAIL=registry has invalid fields", output.getvalue())

    def test_add_requires_confirmation_before_catalog_fetch(self) -> None:
        calls: list[object] = []
        with mock.patch.object(models.sys, "stdin", io.StringIO()):
            with self.assertRaisesRegex(models.ModelsError, "--yes"):
                models.add_model(
                    self.path,
                    "sonnet",
                    MODEL,
                    ENDPOINT,
                    approved=False,
                    now_fn=lambda: NOW,
                    verify=lambda *args: calls.append(args),
                )
        self.assertEqual(calls, [])
        self.assertFalse(self.path.exists())

    def test_add_preset_requires_confirmation_before_catalog_fetch(self) -> None:
        calls: list[object] = []
        with mock.patch.object(models.sys, "stdin", io.StringIO()):
            with self.assertRaisesRegex(models.ModelsError, "--yes"):
                models.add_preset(
                    self.path,
                    "kimi-k3",
                    approved=False,
                    now_fn=lambda: NOW,
                    verify=lambda *args: calls.append(args),
                )
        self.assertEqual(calls, [])
        self.assertFalse(self.path.exists())

    def test_unknown_preset_fails_before_registry_or_catalog_access(self) -> None:
        calls: list[object] = []
        with self.assertRaisesRegex(
            models.ModelsError,
            "unknown OpenRouter preset.*kimi-k3.*deepseek-v4-flash-0731.*qwen-3-6-27b",
        ):
            models.add_preset(
                self.path,
                "not-managed",
                approved=True,
                now_fn=lambda: NOW,
                verify=lambda *args: calls.append(args),
            )
        self.assertEqual(calls, [])
        self.assertFalse(self.path.exists())

    def test_add_validates_selection_and_writes_registry(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = models.add_model(
                self.path,
                "sonnet",
                MODEL,
                ENDPOINT,
                approved=True,
                now_fn=lambda: NOW,
                verify=lambda model, endpoint_provider: selection(),
            )
        self.assertEqual(result, 0)
        loaded = policy.load_openrouter_registry(self.path, now=NOW)
        self.assertEqual(loaded.models[0].agent_name, "airlock-or-sonnet")
        self.assertIn("Added airlock-or-sonnet", output.getvalue())

    def test_add_preset_uses_frozen_values_without_guidance_in_registry(self) -> None:
        preset = presets.preset_by_name("kimi-k3")
        assert preset is not None
        calls: list[tuple[str, str]] = []

        def verify(model: str, endpoint_provider: str) -> models.CatalogSelection:
            calls.append((model, endpoint_provider))
            return selection(
                model=model,
                endpoint_provider=endpoint_provider,
                provider_name=preset.provider_name,
                provider_slug=preset.provider_slug,
                quantization=preset.quantization,
                canonical_slug=preset.canonical_slug,
            )

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(models.add_preset(
                self.path,
                preset.name,
                approved=True,
                now_fn=lambda: NOW,
                verify=verify,
            ), 0)
        self.assertEqual(calls, [(preset.model, preset.endpoint_provider)])
        loaded = policy.load_openrouter_registry(self.path, now=NOW)
        self.assertEqual(len(loaded.models), 1)
        saved = loaded.models[0]
        self.assertEqual(saved.route, preset.route)
        self.assertEqual(saved.agent_name, "airlock-or-kimi-k3")
        self.assertEqual(saved.model, preset.model)
        self.assertEqual(saved.canonical_slug, preset.canonical_slug)
        self.assertEqual(saved.endpoint_provider, preset.endpoint_provider)
        self.assertEqual(saved.provider_name, preset.provider_name)
        self.assertEqual(saved.provider_slug, preset.provider_slug)
        self.assertEqual(saved.quantization, preset.quantization)
        raw = self.path.read_text(encoding="ascii")
        self.assertNotIn(preset.suggested_use, raw)
        self.assertNotIn(preset.tradeoffs, raw)
        self.assertNotIn("suggested_use", raw)
        self.assertNotIn("tradeoffs", raw)
        self.assertIn("Added airlock-or-kimi-k3", output.getvalue())

    def test_add_preset_rejects_identity_endpoint_and_canonical_drift(self) -> None:
        preset = presets.preset_by_name("kimi-k3")
        assert preset is not None
        cases = (
            selection(
                model="vendor/different-model",
                endpoint_provider=preset.endpoint_provider,
                canonical_slug="vendor/different-model-20260810",
            ),
            selection(
                model=preset.model,
                endpoint_provider="provider/different",
                canonical_slug=preset.canonical_slug,
            ),
            selection(
                model=preset.model,
                endpoint_provider=preset.endpoint_provider,
                provider_name="Different Provider",
                provider_slug=preset.provider_slug,
                quantization=preset.quantization,
                canonical_slug=preset.canonical_slug,
            ),
            selection(
                model=preset.model,
                endpoint_provider=preset.endpoint_provider,
                provider_name=preset.provider_name,
                provider_slug="different-provider",
                quantization=preset.quantization,
                canonical_slug=preset.canonical_slug,
            ),
            selection(
                model=preset.model,
                endpoint_provider=preset.endpoint_provider,
                provider_name=preset.provider_name,
                provider_slug=preset.provider_slug,
                quantization="fp8",
                canonical_slug=preset.canonical_slug,
            ),
            selection(
                model=preset.model,
                endpoint_provider=preset.endpoint_provider,
                provider_name=preset.provider_name,
                provider_slug=preset.provider_slug,
                quantization=preset.quantization,
                canonical_slug="moonshotai/kimi-k3-20260810",
            ),
        )
        for selected in cases:
            with self.subTest(selection=selected):
                with self.assertRaisesRegex(
                    models.ModelsError,
                    "identity or endpoint changed|different canonical model",
                ):
                    models.add_preset(
                        self.path,
                        preset.name,
                        approved=True,
                        now_fn=lambda: NOW,
                        verify=lambda *args, value=selected: value,
                    )
                self.assertFalse(self.path.exists())

    def test_add_rejects_concurrent_registry_change_without_losing_winner(self) -> None:
        concurrent = selection(
            model="vendor/concurrent",
            endpoint_provider="provider",
        ).entry("concurrent", NOW)
        original_cas = policy.compare_and_swap_openrouter_registry

        def race(*args: object, **kwargs: object) -> object:
            policy.write_openrouter_registry(
                self.path,
                registry(concurrent),
                now=NOW,
            )
            return original_cas(*args, **kwargs)

        with mock.patch.object(
            policy,
            "compare_and_swap_openrouter_registry",
            side_effect=race,
        ):
            with self.assertRaisesRegex(models.ModelsError, "changed during"):
                models.add_model(
                    self.path,
                    "sonnet",
                    MODEL,
                    ENDPOINT,
                    approved=True,
                    now_fn=lambda: NOW,
                    verify=lambda model, endpoint_provider: selection(),
                )
        loaded = policy.load_openrouter_registry(self.path, now=NOW)
        self.assertEqual([item.route for item in loaded.models], ["concurrent"])

    def test_add_rejects_invalid_and_duplicate_values_before_fetch(self) -> None:
        self.write(registry(entry()))
        calls: list[object] = []
        for route, model, endpoint in [
            ("Bad Route", "vendor/new", "provider"),
            ("sonnet", "vendor/new", "provider"),
            ("other", MODEL, "provider"),
        ]:
            with self.subTest(route=route, model=model):
                with self.assertRaises(models.ModelsError):
                    models.add_model(
                        self.path,
                        route,
                        model,
                        endpoint,
                        approved=True,
                        now_fn=lambda: NOW,
                        verify=lambda *args: calls.append(args),
                    )
        self.assertEqual(calls, [])

    def test_remove_repairs_stale_registry_without_network(self) -> None:
        stale = entry(checked_at=NOW - 31 * 24 * 60 * 60)
        self.write(registry(stale), require_fresh=False)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(models.remove_model(self.path, "sonnet", approved=True), 0)
        loaded = policy.load_openrouter_registry(self.path, now=NOW)
        self.assertEqual(loaded.models, ())
        self.assertIn("Removed airlock-or-sonnet", output.getvalue())

    def test_remove_rejects_concurrent_add_without_deleting_it(self) -> None:
        self.write(registry(entry()))
        concurrent = selection(
            model="vendor/concurrent",
            endpoint_provider="provider",
        ).entry("concurrent", NOW)
        original_cas = policy.compare_and_swap_openrouter_registry

        def race(*args: object, **kwargs: object) -> object:
            policy.write_openrouter_registry(
                self.path,
                registry(entry(), concurrent),
                now=NOW,
            )
            return original_cas(*args, **kwargs)

        with mock.patch.object(
            policy,
            "compare_and_swap_openrouter_registry",
            side_effect=race,
        ):
            with self.assertRaisesRegex(models.ModelsError, "changed during"):
                models.remove_model(self.path, "sonnet", approved=True)
        loaded = policy.load_openrouter_registry(self.path, now=NOW)
        self.assertEqual(
            [item.route for item in loaded.models],
            ["sonnet", "concurrent"],
        )

    def test_refresh_reports_without_write_then_applies_explicitly(self) -> None:
        stale = entry(checked_at=NOW - 31 * 24 * 60 * 60)
        self.write(registry(stale), require_fresh=False)
        before = self.path.read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            models.refresh_models(
                self.path,
                None,
                approved=True,
                apply=False,
                now_fn=lambda: NOW,
                verify=lambda model, endpoint_provider: selection(
                    ("temperature", "tool_choice", "tools")
                ),
            )
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn("metadata changed", output.getvalue())
        self.assertIn("--apply", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            models.refresh_models(
                self.path,
                "sonnet",
                approved=True,
                apply=True,
                now_fn=lambda: NOW,
                verify=lambda model, endpoint_provider: selection(
                    ("temperature", "tool_choice", "tools")
                ),
            )
        loaded = policy.load_openrouter_registry(self.path, now=NOW)
        self.assertEqual(loaded.models[0].checked_at, NOW)
        self.assertEqual(
            loaded.models[0].supported_parameters,
            ("temperature", "tool_choice", "tools"),
        )

    def test_refresh_rejects_concurrent_remove_without_resurrecting_route(self) -> None:
        self.write(registry(entry()))
        original_cas = policy.compare_and_swap_openrouter_registry

        def race(*args: object, **kwargs: object) -> object:
            policy.write_openrouter_registry(
                self.path,
                registry(),
                now=NOW,
            )
            return original_cas(*args, **kwargs)

        with mock.patch.object(
            policy,
            "compare_and_swap_openrouter_registry",
            side_effect=race,
        ):
            with self.assertRaisesRegex(models.ModelsError, "changed during"):
                models.refresh_models(
                    self.path,
                    "sonnet",
                    approved=True,
                    apply=True,
                    now_fn=lambda: NOW,
                    verify=lambda model, endpoint_provider: selection(),
                )
        loaded = policy.load_openrouter_registry(self.path, now=NOW)
        self.assertEqual(loaded.models, ())

    def test_refresh_rejects_canonical_slug_drift_without_writing(self) -> None:
        self.write(registry(entry()))
        before = self.path.read_bytes()
        for apply in (False, True):
            output = io.StringIO()
            with self.subTest(apply=apply), redirect_stdout(output):
                with self.assertRaisesRegex(
                    models.ModelsError,
                    "different canonical model",
                ):
                    models.refresh_models(
                        self.path,
                        "sonnet",
                        approved=True,
                        apply=apply,
                        now_fn=lambda: NOW,
                        verify=lambda model, endpoint_provider: selection(
                            model=model,
                            endpoint_provider=endpoint_provider,
                            canonical_slug="anthropic/claude-sonnet-4.5-20260810",
                        ),
                    )
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(self.path.read_bytes(), before)

    def test_refresh_rejects_provider_or_quantization_drift_without_writing(self) -> None:
        self.write(registry(entry()))
        before = self.path.read_bytes()
        cases = (
            {"provider_name": "Different Provider"},
            {"provider_slug": "different-provider"},
            {"quantization": "fp4"},
        )
        for changes in cases:
            for apply in (False, True):
                with self.subTest(changes=changes, apply=apply):
                    with self.assertRaisesRegex(
                        models.ModelsError,
                        "endpoint routing metadata changed",
                    ):
                        models.refresh_models(
                            self.path,
                            "sonnet",
                            approved=True,
                            apply=apply,
                            now_fn=lambda: NOW,
                            verify=lambda model, endpoint_provider, values=changes: selection(
                                model=model,
                                endpoint_provider=endpoint_provider,
                                **values,
                            ),
                        )
                    self.assertEqual(self.path.read_bytes(), before)

    def test_refresh_apply_rejects_expired_catalog_metadata(self) -> None:
        self.write(registry(entry()))
        before = self.path.read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            with self.assertRaisesRegex(models.ModelsError, "expired"):
                models.refresh_models(
                    self.path,
                    "sonnet",
                    approved=True,
                    apply=True,
                    now_fn=lambda: NOW,
                    verify=lambda model, endpoint_provider: selection(
                        expiration_date="2026-08-09"
                    ),
                )
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(output.getvalue(), "")

    def test_targeted_refresh_cannot_leave_another_stale_entry(self) -> None:
        stale_checked_at = NOW - 31 * 24 * 60 * 60
        first = entry(checked_at=stale_checked_at)
        second = selection(
            model="vendor/second-model", endpoint_provider="provider"
        ).entry("second", stale_checked_at)
        self.write(registry(first, second), require_fresh=False)
        before = self.path.read_bytes()
        output = io.StringIO()
        calls: list[tuple[str, str]] = []
        with redirect_stdout(output):
            with self.assertRaisesRegex(
                models.ModelsError,
                "cannot apply a targeted refresh.*second",
            ):
                models.refresh_models(
                    self.path,
                    "sonnet",
                    approved=True,
                    apply=True,
                    now_fn=lambda: NOW,
                    verify=lambda model, endpoint_provider: (
                        calls.append((model, endpoint_provider)) or selection(
                            model=model,
                            endpoint_provider=endpoint_provider,
                        )
                    ),
                )
        self.assertEqual(calls, [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(output.getvalue(), "")

    def test_main_dispatches_preset_commands_without_catalog_access(self) -> None:
        output = io.StringIO()
        with mock.patch.object(
            models,
            "fetch_catalog_json",
            side_effect=AssertionError("network access is not allowed"),
        ), redirect_stdout(output):
            self.assertEqual(models.main([
                "--registry", str(self.path), "presets",
            ]), 0)
        self.assertIn("kimi-k3", output.getvalue())
        self.assertFalse(self.path.exists())

        with mock.patch.object(models, "add_preset", return_value=0) as add:
            self.assertEqual(models.main([
                "--registry", str(self.path), "add-preset", "kimi-k3", "--yes",
            ]), 0)
        add.assert_called_once_with(
            self.path,
            "kimi-k3",
            approved=True,
        )

    def test_main_rejects_unsupported_arguments_without_network(self) -> None:
        errors = io.StringIO()
        with redirect_stderr(errors):
            result = models.main([
                "--registry", str(self.path), "refresh", "--online"
            ])
        self.assertEqual(result, 2)
        self.assertIn("unrecognized arguments", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
