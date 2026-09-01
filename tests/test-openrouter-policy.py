#!/usr/bin/env python3
"""Offline tests for strict OpenRouter registry and session policy."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "bin" / "airlock_policy.py"
SPEC = importlib.util.spec_from_file_location("airlock_policy", POLICY_PATH)
assert SPEC is not None and SPEC.loader is not None
policy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = policy
SPEC.loader.exec_module(policy)

NOW = 1_767_225_600  # 2026-01-01 00:00:00 UTC
DAY = 24 * 60 * 60


def valid_entry(**changes: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "route": "sonnet-or",
        "model": "anthropic/claude-sonnet-4.5",
        "endpoint_provider": "anthropic",
        "provider_name": "Anthropic",
        "provider_slug": "anthropic",
        "quantization": "bf16",
        "canonical_slug": "anthropic/claude-sonnet-4.5-20250929",
        "alias_target": None,
        "supported_parameters": ["temperature", "tool_choice", "tools"],
        "expiration_date": "2026-01-01",
        "checked_at": NOW,
        "enabled": True,
    }
    entry.update(changes)
    return entry


def valid_registry(*entries: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "models": list(entries) if entries else [valid_entry()],
    }


def valid_snapshot() -> dict[str, object]:
    return {
        "schema_version": 1,
        "protocol_version": 3,
        "profile": "hybrid",
        "root_model": "claude-opus-4-6[1m]",
        "root_provider": "anthropic",
        "routes": {
            "claude-opus-4-6[1m]": "anthropic",
            "gpt-5.6-sol": "openai",
            "anthropic/claude-sonnet-4.5": "openrouter",
        },
        "agents": {
            "airlock-sol": {
                "model": "gpt-5.6-sol",
                "provider": "openai",
                "extra_usage": False,
            },
            "airlock-or-sonnet": {
                "model": "anthropic/claude-sonnet-4.5",
                "provider": "openrouter",
                "extra_usage": True,
            },
        },
        "openrouter": {
            "anthropic/claude-sonnet-4.5": {
                "endpoint_provider": "anthropic",
                "provider_name": "Anthropic",
                "provider_slug": "anthropic",
                "quantization": "bf16",
                "canonical_slug": "anthropic/claude-sonnet-4.5-20250929",
            }
        },
    }


class PolicyTestCase(unittest.TestCase):
    def assert_invalid_registry(
        self, registry: object, message: str | None = None
    ) -> None:
        with self.assertRaises(policy.PolicyValidationError) as caught:
            policy.validate_openrouter_registry(registry, now=NOW)
        if message is not None:
            self.assertIn(message, str(caught.exception))

    def assert_invalid_snapshot(
        self, snapshot: object, message: str | None = None
    ) -> None:
        with self.assertRaises(policy.PolicyValidationError) as caught:
            policy.validate_session_snapshot(snapshot)
        if message is not None:
            self.assertIn(message, str(caught.exception))


class JsonUtilityTests(PolicyTestCase):
    def test_duplicate_keys_are_rejected_at_every_object_depth(self) -> None:
        values = [
            b'{"schema_version":1,"schema_version":1,"models":[]}',
            (
                b'{"schema_version":1,"models":[{"route":"one",'
                b'"route":"two"}]}'
            ),
            b'{"outer":{"inner":1,"inner":2}}',
        ]
        for raw in values:
            with self.subTest(raw=raw):
                with self.assertRaises(policy.PolicyValidationError) as caught:
                    policy.load_json_bytes(raw)
                self.assertIn("duplicate JSON object key", str(caught.exception))

    def test_malformed_utf8_bom_nonfinite_and_trailing_json_are_rejected(self) -> None:
        values = [
            b"\xff",
            b"\xef\xbb\xbf{}",
            b'{"number":NaN}',
            b'{"number":Infinity}',
            b"{} trailing",
            b"",
        ]
        for raw in values:
            with self.subTest(raw=raw):
                with self.assertRaises(policy.PolicyValidationError):
                    policy.load_json_bytes(raw)

    def test_json_byte_limit_is_exact(self) -> None:
        accepted = b" " * (policy.MAX_POLICY_BYTES - 2) + b"{}"
        self.assertEqual(policy.load_json_bytes(accepted), {})
        with self.assertRaises(policy.PolicyValidationError):
            policy.load_json_bytes(accepted + b" ")

    def test_canonical_json_and_digest_are_stable(self) -> None:
        first = {"z": [3, 2, 1], "a": {"unicode": "café"}}
        second = {"a": {"unicode": "café"}, "z": [3, 2, 1]}
        expected = b'{"a":{"unicode":"caf\\u00e9"},"z":[3,2,1]}'
        self.assertEqual(policy.canonical_json_bytes(first), expected)
        self.assertEqual(policy.canonical_json_bytes(second), expected)
        digest = hashlib.sha256(expected).hexdigest()
        self.assertEqual(policy.sha256_bytes(expected), digest)
        self.assertEqual(policy.canonical_sha256(first), digest)
        with self.assertRaises(TypeError):
            policy.sha256_bytes("not bytes")
        with self.assertRaises(policy.PolicyValidationError):
            policy.canonical_json_bytes({"bad": float("nan")})


class RegistryValidationTests(PolicyTestCase):
    def test_valid_registry_is_immutable_and_derives_agent_name(self) -> None:
        registry = policy.validate_openrouter_registry(valid_registry(), now=NOW)
        self.assertEqual(registry.schema_version, 1)
        self.assertIsInstance(registry.models, tuple)
        self.assertEqual(registry.models[0].agent_name, "airlock-or-sonnet-or")
        self.assertIsInstance(registry.models[0].supported_parameters, tuple)
        with self.assertRaises(Exception):
            registry.models[0].route = "changed"

    def test_empty_registry_is_valid(self) -> None:
        registry = policy.validate_openrouter_registry(
            {"schema_version": 1, "models": []}, now=NOW
        )
        self.assertEqual(registry.models, ())
        self.assertEqual(
            registry.canonical_bytes(), b'{"models":[],"schema_version":1}'
        )

    def test_registry_canonical_and_digest_ignore_json_formatting(self) -> None:
        value = valid_registry()
        raw_one = json.dumps(value, indent=2).encode("utf-8")
        raw_two = json.dumps(
            {"models": value["models"], "schema_version": 1},
            separators=(",", ":"),
        ).encode("utf-8")
        first = policy.validate_openrouter_registry(
            policy.load_json_bytes(raw_one), now=NOW
        )
        second = policy.validate_openrouter_registry(
            policy.load_json_bytes(raw_two), now=NOW
        )
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertEqual(first.digest(), second.digest())
        self.assertEqual(
            first.digest(), hashlib.sha256(first.canonical_bytes()).hexdigest()
        )

    def test_top_level_shape_version_and_count_are_strict(self) -> None:
        ten = [
            valid_entry(
                route=f"route-{index}",
                model=f"vendor/model-{index}",
                canonical_slug=f"vendor/model-{index}",
            )
            for index in range(10)
        ]
        result = policy.validate_openrouter_registry(valid_registry(*ten), now=NOW)
        self.assertEqual(len(result.models), 10)
        cases = [
            [],
            {"schema_version": 1},
            {"schema_version": 1, "models": [], "roots": []},
            {"schema_version": True, "models": []},
            {"schema_version": 2, "models": []},
            {"schema_version": 1, "models": {}},
            {
                "schema_version": 1,
                "models": [valid_entry(route=f"route-{index}", model=f"v/m-{index}", canonical_slug=f"v/m-{index}") for index in range(11)],
            },
        ]
        for value in cases:
            with self.subTest(value=str(value)[:80]):
                self.assert_invalid_registry(value)

    def test_entry_fields_are_exact_and_free_form_policy_is_rejected(self) -> None:
        for forbidden in [
            "prompt",
            "effort",
            "context",
            "billing",
            "description",
            "fallbacks",
            "roots",
        ]:
            entry = valid_entry()
            entry[forbidden] = "untrusted text"
            with self.subTest(field=forbidden):
                self.assert_invalid_registry(valid_registry(entry), "unknown")
        entry = valid_entry()
        del entry["enabled"]
        self.assert_invalid_registry(valid_registry(entry), "missing")
        self.assert_invalid_registry(valid_registry("not an object"))  # type: ignore[arg-type]

    def test_route_validation_and_boundaries(self) -> None:
        valid_routes = ["a", "a" * 40, "a1-b2"]
        for route in valid_routes:
            with self.subTest(valid=route):
                entry = valid_entry(route=route)
                result = policy.validate_openrouter_registry(
                    valid_registry(entry), now=NOW
                )
                self.assertEqual(result.models[0].agent_name, f"airlock-or-{route}")
        invalid_routes = [
            "",
            "a" * 41,
            "Upper",
            "under_score",
            "-leading",
            "trailing-",
            "two--hyphens",
            "white space",
            "line\nbreak",
        ]
        for route in invalid_routes:
            with self.subTest(invalid=route):
                self.assert_invalid_registry(valid_registry(valid_entry(route=route)))

    def test_canonical_model_id_validation_and_boundaries(self) -> None:
        valid_models = [
            "a/b",
            "a/" + "b" * 158,
            "vendor/model.v2_test:stable-name",
        ]
        for model in valid_models:
            with self.subTest(valid=model[:30]):
                registry = valid_registry(
                    valid_entry(model=model, canonical_slug=model)
                )
                result = policy.validate_openrouter_registry(registry, now=NOW)
                self.assertEqual(result.models[0].model, model)
        invalid_models = [
            "a/" + "b" * 159,
            "ab",
            "a/b/c",
            "/model",
            "vendor/",
            "Vendor/model",
            "vendor/Model",
            "vendor/model name",
            "vendor/model\nname",
            "vendor/modèle",
            "vendor/model[1m]",
            "vendor/model..v2",
            "vendor/-model",
            "vendor/model-",
            "vendor/model:free",
            "vendor/model:extended",
            "openrouter/auto",
            "router/fixed",
            "vendor/latest",
            "vendor/model-auto",
        ]
        for model in invalid_models:
            with self.subTest(invalid=model[:40]):
                self.assert_invalid_registry(
                    valid_registry(valid_entry(model=model, canonical_slug=model))
                )

    def test_canonical_slug_is_validated_as_independent_provenance(self) -> None:
        registry = policy.validate_openrouter_registry(
            valid_registry(
                valid_entry(
                    model="deepseek/deepseek-v4-flash-0731",
                    canonical_slug="deepseek/deepseek-v4-flash-20260731",
                )
            ),
            now=NOW,
        )
        self.assertEqual(
            registry.models[0].canonical_slug,
            "deepseek/deepseek-v4-flash-20260731",
        )
        for canonical_slug in [
            True,
            "anthropic/Bad Slug",
            "anthropic",
            "anthropic/model/extra",
            "anthropic/-model",
            "anthropic/model-",
        ]:
            with self.subTest(canonical_slug=canonical_slug):
                self.assert_invalid_registry(
                    valid_registry(valid_entry(canonical_slug=canonical_slug))
                )

    def test_endpoint_provider_is_a_bounded_lower_case_slug(self) -> None:
        for provider in [
            "a",
            "a" * 80,
            "provider-name.v2_test",
            "deepinfra/turbo",
            "google-vertex/us-east5",
        ]:
            with self.subTest(valid=provider[:20]):
                result = policy.validate_openrouter_registry(
                    valid_registry(valid_entry(endpoint_provider=provider)), now=NOW
                )
                self.assertEqual(result.models[0].endpoint_provider, provider)
        for provider in [
            "",
            "a" * 81,
            "Anthropic",
            "two words",
            "leading-",
            "-trailing",
            "two..dots",
            "/provider",
            "provider/",
            "provider//turbo",
            "provider/region/extra",
            "provider/-region",
            "line\nbreak",
        ]:
            with self.subTest(invalid=provider[:20]):
                self.assert_invalid_registry(
                    valid_registry(valid_entry(endpoint_provider=provider))
                )

    def test_provider_name_and_quantization_are_bounded_routing_tokens(self) -> None:
        for provider_name in [
            "DeepInfra",
            "Amazon Bedrock",
            "Together AI",
            "A&B",
            "Provider-2",
        ]:
            with self.subTest(provider_name=provider_name):
                result = policy.validate_openrouter_registry(
                    valid_registry(valid_entry(provider_name=provider_name)), now=NOW
                )
                self.assertEqual(result.models[0].provider_name, provider_name)
        for provider_name in [
            "",
            "a" * 81,
            " leading",
            "trailing ",
            "two  spaces",
            "line\nbreak",
            "name,other",
            "{provider}",
        ]:
            with self.subTest(provider_name=provider_name):
                self.assert_invalid_registry(
                    valid_registry(valid_entry(provider_name=provider_name))
                )
        for provider_slug in ["deepinfra", "amazon-bedrock", "provider_2"]:
            with self.subTest(provider_slug=provider_slug):
                result = policy.validate_openrouter_registry(
                    valid_registry(valid_entry(provider_slug=provider_slug)), now=NOW
                )
                self.assertEqual(result.models[0].provider_slug, provider_slug)
        for provider_slug in [
            "",
            "a" * 81,
            "DeepInfra",
            "two words",
            "provider/region",
            "leading-",
        ]:
            with self.subTest(provider_slug=provider_slug):
                self.assert_invalid_registry(
                    valid_registry(valid_entry(provider_slug=provider_slug))
                )
        for quantization in ["fp4", "fp8", "bf16", "int8", "unknown"]:
            with self.subTest(quantization=quantization):
                result = policy.validate_openrouter_registry(
                    valid_registry(valid_entry(quantization=quantization)), now=NOW
                )
                self.assertEqual(result.models[0].quantization, quantization)
        for quantization in ["", "FP4", "two words", "fp4/fp8", "a" * 33]:
            with self.subTest(quantization=quantization):
                self.assert_invalid_registry(
                    valid_registry(valid_entry(quantization=quantization))
                )

    def test_alias_target_must_be_null(self) -> None:
        for alias in ["vendor/latest", "", False, []]:
            with self.subTest(alias=alias):
                self.assert_invalid_registry(
                    valid_registry(valid_entry(alias_target=alias)), "must be null"
                )

    def test_supported_parameters_are_safe_sorted_unique_and_required(self) -> None:
        valid = ["a", "response_format", "tool_choice", "tools", "z" * 64]
        result = policy.validate_openrouter_registry(
            valid_registry(valid_entry(supported_parameters=valid)), now=NOW
        )
        self.assertEqual(result.models[0].supported_parameters, tuple(valid))
        maximum = sorted(
            [f"p{index:02d}" for index in range(62)] + ["tool_choice", "tools"]
        )
        result = policy.validate_openrouter_registry(
            valid_registry(valid_entry(supported_parameters=maximum)), now=NOW
        )
        self.assertEqual(len(result.models[0].supported_parameters), 64)
        cases = [
            "tools,tool_choice",
            ["tools", "tool_choice"],
            ["tool_choice", "tools", "tools"],
            ["tools"],
            ["tool_choice"],
            ["Tool_choice", "tools"],
            ["bad-name", "tool_choice", "tools"],
            ["bad\nname", "tool_choice", "tools"],
            ["a" * 65, "tool_choice", "tools"],
            sorted([f"p{index:02d}" for index in range(63)] + ["tool_choice", "tools"]),
        ]
        for parameters in cases:
            with self.subTest(parameters=str(parameters)[:80]):
                self.assert_invalid_registry(
                    valid_registry(valid_entry(supported_parameters=parameters))
                )

    def test_expiration_date_uses_injected_utc_date(self) -> None:
        for expiration in [None, "2026-01-01", "2026-01-02"]:
            with self.subTest(valid=expiration):
                policy.validate_openrouter_registry(
                    valid_registry(valid_entry(expiration_date=expiration)), now=NOW
                )
        for expiration in [
            "2025-12-31",
            "2026-1-01",
            "2026-01-1",
            "2026-02-30",
            "2026-01-01T00:00:00Z",
            True,
        ]:
            with self.subTest(invalid=expiration):
                self.assert_invalid_registry(
                    valid_registry(valid_entry(expiration_date=expiration))
                )
        aware_now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        policy.validate_openrouter_registry(valid_registry(), now=aware_now)
        with self.assertRaises(policy.PolicyValidationError):
            policy.validate_openrouter_registry(
                valid_registry(), now=datetime(2026, 1, 1)
            )

    def test_checked_at_boundaries_and_strict_integer(self) -> None:
        accepted = [1, NOW - 30 * DAY, NOW, NOW + DAY]
        for checked_at in accepted:
            with self.subTest(valid=checked_at):
                expiration = None if checked_at == 1 else "2026-01-01"
                policy.validate_openrouter_registry(
                    valid_registry(
                        valid_entry(
                            checked_at=checked_at,
                            expiration_date=expiration,
                        )
                    ),
                    now=NOW if checked_at != 1 else 1,
                )
        for checked_at in [0, -1, True, float(NOW), str(NOW), NOW - 30 * DAY - 1, NOW + DAY + 1]:
            with self.subTest(invalid=checked_at):
                self.assert_invalid_registry(
                    valid_registry(valid_entry(checked_at=checked_at))
                )
        for bad_now in [True, -1, float("inf"), datetime(2026, 1, 1)]:
            with self.subTest(now=bad_now):
                with self.assertRaises(policy.PolicyValidationError):
                    policy.validate_openrouter_registry(valid_registry(), now=bad_now)

    def test_enabled_is_a_strict_boolean(self) -> None:
        for enabled in [True, False]:
            result = policy.validate_openrouter_registry(
                valid_registry(valid_entry(enabled=enabled)), now=NOW
            )
            self.assertIs(result.models[0].enabled, enabled)
        for enabled in [0, 1, "true", None]:
            with self.subTest(enabled=enabled):
                self.assert_invalid_registry(
                    valid_registry(valid_entry(enabled=enabled))
                )

    def test_duplicate_route_model_and_generated_agent_are_rejected(self) -> None:
        duplicate_route = valid_entry(
            model="vendor/second", canonical_slug="vendor/second"
        )
        self.assert_invalid_registry(
            valid_registry(valid_entry(), duplicate_route), "duplicate route"
        )
        duplicate_model = valid_entry(route="second")
        self.assert_invalid_registry(
            valid_registry(valid_entry(), duplicate_model), "duplicate model"
        )
        # Agent names are a deterministic one-to-one function of validated routes.
        # A duplicate route therefore also proves duplicate Agent rejection.
        self.assertEqual(
            policy.validate_openrouter_registry(valid_registry(), now=NOW).models[0].agent_name,
            "airlock-or-sonnet-or",
        )


class RegistryFileTests(PolicyTestCase):
    def test_writer_round_trips_canonical_private_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "private" / "openrouter-registry.json"
            written = policy.write_openrouter_registry(
                path, valid_registry(), now=NOW
            )
            loaded = policy.load_openrouter_registry(path, now=NOW)
            self.assertEqual(loaded, written)
            self.assertEqual(path.read_bytes(), written.canonical_bytes() + b"\n")
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_compare_and_swap_round_trips_when_version_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "private" / "openrouter-registry.json"
            original = policy.write_openrouter_registry(
                path,
                valid_registry(),
                now=NOW,
            )
            updated = valid_registry(
                valid_entry(
                    route="second",
                    model="vendor/second",
                    canonical_slug="vendor/second-20260101",
                )
            )
            written = policy.compare_and_swap_openrouter_registry(
                path,
                original.digest(),
                updated,
                now=NOW,
            )
            self.assertEqual(path.read_bytes(), written.canonical_bytes() + b"\n")
            self.assertTrue(
                (path.parent / ".openrouter-registry.lock").is_file()
            )
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(
                    (path.parent / ".openrouter-registry.lock").stat().st_mode
                    & 0o777,
                    0o600,
                )

    def test_compare_and_swap_rejects_changed_and_absent_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openrouter-registry.json"
            original = policy.write_openrouter_registry(
                path,
                valid_registry(),
                now=NOW,
            )
            concurrent = valid_registry(
                valid_entry(
                    route="concurrent",
                    model="vendor/concurrent",
                    canonical_slug="vendor/concurrent-20260101",
                )
            )
            policy.write_openrouter_registry(path, concurrent, now=NOW)
            before = path.read_bytes()
            with self.assertRaises(policy.RegistryConflictError):
                policy.compare_and_swap_openrouter_registry(
                    path,
                    original.digest(),
                    valid_registry(),
                    now=NOW,
                )
            self.assertEqual(path.read_bytes(), before)

            path.write_bytes(b"not json")
            with self.assertRaises(policy.PolicyValidationError) as caught:
                policy.compare_and_swap_openrouter_registry(
                    path,
                    original.digest(),
                    valid_registry(),
                    now=NOW,
                )
            self.assertNotIsInstance(
                caught.exception,
                policy.RegistryConflictError,
            )

            missing = Path(temporary_text) / "missing.json"
            policy.write_openrouter_registry(
                missing,
                {"schema_version": 1, "models": []},
                now=NOW,
            )
            before = missing.read_bytes()
            with self.assertRaises(policy.RegistryConflictError):
                policy.compare_and_swap_openrouter_registry(
                    missing,
                    None,
                    valid_registry(),
                    now=NOW,
                )
            self.assertEqual(missing.read_bytes(), before)

    def test_two_compare_and_swap_writers_cannot_lose_an_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openrouter-registry.json"
            original = policy.write_openrouter_registry(
                path,
                valid_registry(),
                now=NOW,
            )
            barrier = threading.Barrier(2)
            outcomes: list[str] = []

            def update(route: str) -> None:
                candidate = valid_registry(
                    valid_entry(
                        route=route,
                        model=f"vendor/{route}",
                        canonical_slug=f"vendor/{route}-20260101",
                    )
                )
                barrier.wait()
                try:
                    policy.compare_and_swap_openrouter_registry(
                        path,
                        original.digest(),
                        candidate,
                        now=NOW,
                    )
                except policy.RegistryConflictError:
                    outcomes.append("conflict")
                else:
                    outcomes.append(route)

            threads = [
                threading.Thread(target=update, args=(route,))
                for route in ("first", "second")
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(outcomes.count("conflict"), 1)
            committed = [item for item in outcomes if item != "conflict"]
            self.assertEqual(len(committed), 1)
            loaded = policy.load_openrouter_registry(path, now=NOW)
            self.assertEqual(loaded.models[0].route, committed[0])

    def test_compare_and_swap_reports_lock_contention_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openrouter-registry.json"
            original = policy.write_openrouter_registry(
                path,
                valid_registry(),
                now=NOW,
            )
            with policy._openrouter_registry_lock(path):
                with mock.patch.object(
                    policy,
                    "REGISTRY_LOCK_TIMEOUT_SECONDS",
                    0,
                ):
                    with self.assertRaisesRegex(
                        policy.RegistryBusyError,
                        "operation is in progress",
                    ):
                        policy.compare_and_swap_openrouter_registry(
                            path,
                            original.digest(),
                            valid_registry(),
                            now=NOW,
                        )

    def test_writer_validates_before_replacing_existing_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openrouter-registry.json"
            original = valid_registry()
            policy.write_openrouter_registry(path, original, now=NOW)
            before = path.read_bytes()
            invalid = valid_registry(valid_entry(enabled="yes"))
            with self.assertRaises(policy.PolicyValidationError):
                policy.write_openrouter_registry(path, invalid, now=NOW)
            self.assertEqual(path.read_bytes(), before)

    def test_writer_cleans_up_temporary_file_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            parent = Path(temporary_text)
            path = parent / "openrouter-registry.json"
            path.write_bytes(b"original\n")
            with mock.patch.object(
                policy.os, "replace", side_effect=OSError("blocked")
            ):
                with self.assertRaises(policy.PolicyFileError):
                    policy.write_openrouter_registry(path, valid_registry(), now=NOW)
            self.assertEqual(path.read_bytes(), b"original\n")
            self.assertEqual(
                list(parent.glob(".openrouter-registry.*.tmp")), []
            )

    @unittest.skipIf(os.name == "nt", "directory fsync is POSIX-only")
    def test_post_replace_sync_failure_reports_committed_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openrouter-registry.json"
            original = policy.write_openrouter_registry(
                path, valid_registry(), now=NOW
            )
            updated = valid_registry(valid_entry(
                route="second",
                model="vendor/second",
                canonical_slug="vendor/second",
            ))
            with mock.patch.object(
                policy.os, "fsync", side_effect=[None, OSError("sync failed")]
            ):
                with self.assertRaisesRegex(
                    policy.PolicyDurabilityError, "was replaced"
                ):
                    policy.compare_and_swap_openrouter_registry(
                        path,
                        original.digest(),
                        updated,
                        now=NOW,
                    )
            loaded = policy.load_openrouter_registry(path, now=NOW)
            self.assertEqual(loaded.models[0].route, "second")

    def test_writer_rejects_unsafe_targets_and_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            directory_target = temporary / "registry.json"
            directory_target.mkdir()
            with self.assertRaises(policy.PolicyFileError):
                policy.write_openrouter_registry(
                    directory_target, valid_registry(), now=NOW
                )

            source = temporary / "source.json"
            source.write_text("original\n", encoding="utf-8")
            link = temporary / "link.json"
            try:
                link.symlink_to(source)
            except OSError:
                pass
            else:
                with self.assertRaises(policy.PolicyFileError):
                    policy.write_openrouter_registry(
                        link, valid_registry(), now=NOW
                    )
                self.assertEqual(source.read_text(encoding="utf-8"), "original\n")

            plain_parent = temporary / "plain-parent"
            plain_parent.write_text("not a directory\n", encoding="utf-8")
            with self.assertRaises(policy.PolicyFileError):
                policy.write_openrouter_registry(
                    plain_parent / "registry.json", valid_registry(), now=NOW
                )

    def test_management_load_can_open_stale_registry_for_repair(self) -> None:
        stale = valid_registry(valid_entry(
            expiration_date="2025-12-01", checked_at=NOW - 31 * DAY
        ))
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openrouter-registry.json"
            path.write_text(json.dumps(stale), encoding="utf-8")
            with self.assertRaises(policy.PolicyValidationError):
                policy.load_openrouter_registry(path, now=NOW)
            loaded = policy.load_openrouter_registry(
                path, now=NOW, require_fresh=False
            )
            self.assertEqual(loaded.models[0].route, "sonnet-or")
            policy.write_openrouter_registry(
                path, loaded, now=NOW, require_fresh=False
            )

    def test_load_regular_file_and_exact_size_boundary(self) -> None:
        raw = json.dumps(valid_registry()).encode("utf-8")
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openrouter-registry.json"
            path.write_bytes(raw)
            registry = policy.load_openrouter_registry(path, now=NOW)
            self.assertEqual(registry.models[0].model, "anthropic/claude-sonnet-4.5")
            padding = policy.MAX_POLICY_BYTES - len(raw)
            path.write_bytes(raw + b" " * padding)
            registry = policy.load_openrouter_registry(path, now=NOW)
            self.assertEqual(len(registry.models), 1)

    def test_missing_is_distinct_from_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "missing.json"
            with self.assertRaises(policy.RegistryNotFoundError):
                policy.load_openrouter_registry(path, now=NOW)
            path.write_bytes(b"not json")
            with self.assertRaises(policy.PolicyValidationError) as caught:
                policy.load_openrouter_registry(path, now=NOW)
            self.assertNotIsInstance(caught.exception, policy.RegistryNotFoundError)

    def test_symlink_directory_nonregular_and_oversize_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            directory = temporary / "directory.json"
            directory.mkdir()
            with self.assertRaises(policy.PolicyFileError):
                policy.load_openrouter_registry(directory, now=NOW)

            oversized = temporary / "oversized.json"
            oversized.write_bytes(b" " * (policy.MAX_POLICY_BYTES + 1))
            with self.assertRaises(policy.PolicyFileError):
                policy.load_openrouter_registry(oversized, now=NOW)

            source = temporary / "source.json"
            source.write_text(json.dumps(valid_registry()), encoding="utf-8")
            link = temporary / "link.json"
            try:
                link.symlink_to(source)
            except OSError:
                pass
            else:
                with self.assertRaises(policy.PolicyFileError):
                    policy.load_openrouter_registry(link, now=NOW)

            if hasattr(os, "mkfifo") and os.name != "nt":
                fifo = temporary / "fifo.json"
                os.mkfifo(fifo)
                with self.assertRaises(policy.PolicyFileError):
                    policy.load_openrouter_registry(fifo, now=NOW)

    @unittest.skipUnless(os.name == "nt", "Windows ACLs are unavailable")
    def test_windows_writer_protects_file_and_rejects_broad_write_acl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            parent = Path(temporary_text) / "private"
            path = parent / "openrouter-registry.json"
            policy.write_openrouter_registry(path, valid_registry(), now=NOW)
            policy.load_openrouter_registry(path, now=NOW)

            changed = subprocess.run(
                ["icacls", str(path), "/grant", "*S-1-1-0:(M)"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.assertEqual(changed.returncode, 0)
            with self.assertRaisesRegex(
                policy.PolicyFileError, "another Windows principal"
            ):
                policy.load_openrouter_registry(path, now=NOW)

            second_parent = Path(temporary_text) / "second-private"
            second_path = second_parent / "openrouter-registry.json"
            policy.write_openrouter_registry(second_path, valid_registry(), now=NOW)
            changed = subprocess.run(
                ["icacls", str(second_parent), "/grant", "*S-1-1-0:(M)"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.assertEqual(changed.returncode, 0)
            with self.assertRaisesRegex(
                policy.PolicyFileError, "another Windows principal"
            ):
                policy.load_openrouter_registry(second_path, now=NOW)

    @unittest.skipUnless(hasattr(os, "geteuid"), "POSIX ownership is unavailable")
    def test_posix_owner_and_write_permissions_are_strict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openrouter-registry.json"
            path.write_text(json.dumps(valid_registry()), encoding="utf-8")
            path.chmod(0o600)
            policy.load_openrouter_registry(path, now=NOW)

            path.chmod(0o620)
            with self.assertRaises(policy.PolicyFileError) as caught:
                policy.load_openrouter_registry(path, now=NOW)
            self.assertIn("group or other", str(caught.exception))

            path.chmod(0o600)
            with mock.patch.object(
                policy.os, "geteuid", return_value=os.geteuid() + 1
            ):
                with self.assertRaises(policy.PolicyFileError) as caught:
                    policy.load_openrouter_registry(path, now=NOW)
            self.assertIn("effective user", str(caught.exception))

    def test_file_loader_rejects_bom_malformed_utf8_and_duplicate_keys(self) -> None:
        values = [
            b"\xef\xbb\xbf{}",
            b"\xff",
            b'{"schema_version":1,"schema_version":1,"models":[]}',
        ]
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openrouter-registry.json"
            for raw in values:
                with self.subTest(raw=raw):
                    path.write_bytes(raw)
                    with self.assertRaises(policy.PolicyValidationError):
                        policy.load_openrouter_registry(path, now=NOW)


class SnapshotValidationTests(PolicyTestCase):
    def test_valid_snapshot_is_immutable_canonical_and_stable(self) -> None:
        value = valid_snapshot()
        snapshot = policy.validate_session_snapshot(value)
        self.assertEqual(snapshot.root_model, "claude-opus-4-6[1m]")
        self.assertEqual(snapshot.to_dict()["root_model"], snapshot.root_model)
        self.assertEqual(snapshot.routes["gpt-5.6-sol"], "openai")
        metadata = snapshot.openrouter["anthropic/claude-sonnet-4.5"]
        self.assertEqual(metadata.endpoint_provider, "anthropic")
        self.assertEqual(metadata.provider_name, "Anthropic")
        self.assertEqual(metadata.provider_slug, "anthropic")
        self.assertEqual(metadata.quantization, "bf16")
        self.assertEqual(
            metadata.canonical_slug,
            "anthropic/claude-sonnet-4.5-20250929",
        )
        self.assertIn(b"canonical_slug", snapshot.canonical_bytes())
        with self.assertRaises(TypeError):
            snapshot.routes["new"] = "openai"
        reordered = {
            "openrouter": value["openrouter"],
            "agents": value["agents"],
            "routes": value["routes"],
            "root_model": value["root_model"],
            "root_provider": value["root_provider"],
            "profile": value["profile"],
            "protocol_version": value["protocol_version"],
            "schema_version": value["schema_version"],
        }
        second = policy.validate_session_snapshot(reordered)
        self.assertEqual(snapshot.canonical_bytes(), second.canonical_bytes())
        self.assertEqual(snapshot.digest(), second.digest())
        self.assertEqual(
            snapshot.digest(), hashlib.sha256(snapshot.canonical_bytes()).hexdigest()
        )

    def test_snapshot_bytes_reject_duplicates_at_each_map_depth(self) -> None:
        values = [
            b'{"schema_version":1,"schema_version":1}',
            b'{"routes":{"model":"openai","model":"grok"}}',
            b'{"agents":{"worker":{"model":"one","model":"two"}}}',
            b'{"openrouter":{"v/m":{"endpoint_provider":"one","endpoint_provider":"two"}}}',
        ]
        for raw in values:
            with self.subTest(raw=raw):
                with self.assertRaises(policy.PolicyValidationError) as caught:
                    policy.load_session_snapshot_bytes(raw)
                self.assertIn("duplicate JSON object key", str(caught.exception))

    def test_snapshot_top_level_fields_and_scalars_are_strict(self) -> None:
        cases: list[object] = [[], {"schema_version": 1}]
        extra = valid_snapshot()
        extra["roots"] = []
        cases.append(extra)
        missing_root = valid_snapshot()
        del missing_root["root_model"]
        cases.append(missing_root)
        for field, bad in [
            ("schema_version", True),
            ("schema_version", 2),
            ("protocol_version", True),
            ("protocol_version", 0),
            ("profile", "Hybrid Profile"),
            ("profile", ""),
            ("root_model", "bad model"),
            ("root_model", "missing-model"),
            ("root_provider", "other"),
        ]:
            value = valid_snapshot()
            value[field] = bad
            cases.append(value)
        for value in cases:
            with self.subTest(value=str(value)[:100]):
                self.assert_invalid_snapshot(value)

    def test_routes_are_exact_and_root_provider_must_agree(self) -> None:
        cases = []
        for routes in [
            [],
            {"bad model": "anthropic"},
            {"Model": "anthropic"},
            {"model": "other"},
            {"m" * 161: "anthropic"},
        ]:
            value = valid_snapshot()
            value["routes"] = routes
            value["agents"] = {}
            value["openrouter"] = {}
            cases.append(value)
        mismatch = valid_snapshot()
        mismatch["root_provider"] = "grok"
        cases.append(mismatch)
        for value in cases:
            with self.subTest(value=str(value)[:100]):
                self.assert_invalid_snapshot(value)

    def test_agent_fields_references_and_provider_agreement_are_strict(self) -> None:
        cases = []
        bad_name = valid_snapshot()
        bad_name["agents"] = {"worker": {"model": "gpt-5.6-sol", "provider": "openai"}}
        cases.append(bad_name)
        malformed_name = valid_snapshot()
        malformed_name["agents"] = {"Airlock Worker": {"model": "gpt-5.6-sol", "provider": "openai"}}
        cases.append(malformed_name)
        unknown_field = valid_snapshot()
        unknown_field["agents"] = {"airlock-sol": {"model": "gpt-5.6-sol", "provider": "openai", "effort": "high"}}
        cases.append(unknown_field)
        missing_route = valid_snapshot()
        missing_route["agents"] = {"airlock-sol": {"model": "gpt-missing", "provider": "openai"}}
        cases.append(missing_route)
        disagreement = valid_snapshot()
        disagreement["agents"] = deepcopy(disagreement["agents"])
        disagreement["agents"]["airlock-sol"]["provider"] = "grok"  # type: ignore[index]
        cases.append(disagreement)
        bad_extra_usage = valid_snapshot()
        bad_extra_usage["agents"] = deepcopy(bad_extra_usage["agents"])
        bad_extra_usage["agents"]["airlock-sol"]["extra_usage"] = 1  # type: ignore[index]
        cases.append(bad_extra_usage)
        for value in cases:
            with self.subTest(value=str(value)[:120]):
                self.assert_invalid_snapshot(value)

    def test_snapshot_agent_count_boundary_is_sixty_four(self) -> None:
        value = valid_snapshot()
        value["routes"] = {"claude-opus-4-6[1m]": "anthropic"}
        value["openrouter"] = {}
        value["agents"] = {
            f"airlock-worker-{index}": {
                "model": "claude-opus-4-6[1m]",
                "provider": "anthropic",
                "extra_usage": False,
            }
            for index in range(64)
        }
        result = policy.validate_session_snapshot(value)
        self.assertEqual(len(result.agents), 64)
        value["agents"]["airlock-overflow-worker"] = {  # type: ignore[index]
            "model": "claude-opus-4-6[1m]",
            "provider": "anthropic",
            "extra_usage": False,
        }
        self.assert_invalid_snapshot(value, "at most 64")

    def test_openrouter_metadata_is_exact_referenced_and_complete(self) -> None:
        unknown = valid_snapshot()
        unknown["openrouter"] = {
            "anthropic/claude-sonnet-4.5": {
                "endpoint_provider": "anthropic",
                "alias_target": None,
            }
        }
        missing = valid_snapshot()
        missing["openrouter"] = {}
        wrong_route = valid_snapshot()
        wrong_route["openrouter"] = {
            "gpt-5.6-sol": {"endpoint_provider": "openai"},
            "anthropic/claude-sonnet-4.5": {"endpoint_provider": "anthropic"},
        }
        dangling = valid_snapshot()
        dangling["openrouter"] = {
            "vendor/unknown": {"endpoint_provider": "provider"},
            "anthropic/claude-sonnet-4.5": {"endpoint_provider": "anthropic"},
        }
        unsafe_provider = valid_snapshot()
        unsafe_provider["openrouter"] = {
            "anthropic/claude-sonnet-4.5": {"endpoint_provider": "Anthropic Inc."}
        }
        moving_route = valid_snapshot()
        moving_route["routes"] = deepcopy(moving_route["routes"])
        del moving_route["routes"]["anthropic/claude-sonnet-4.5"]  # type: ignore[index]
        moving_route["routes"]["vendor/model:free"] = "openrouter"  # type: ignore[index]
        moving_route["agents"] = deepcopy(moving_route["agents"])
        moving_route["agents"]["airlock-or-sonnet"]["model"] = "vendor/model:free"  # type: ignore[index]
        moving_route["openrouter"] = {
            "vendor/model:free": {"endpoint_provider": "provider"}
        }
        for value in [
            unknown,
            missing,
            wrong_route,
            dangling,
            unsafe_provider,
            moving_route,
        ]:
            with self.subTest(value=str(value)[:140]):
                self.assert_invalid_snapshot(value)

    def test_every_non_root_route_must_be_referenced_by_an_agent(self) -> None:
        unreferenced = valid_snapshot()
        unreferenced["routes"] = deepcopy(unreferenced["routes"])
        unreferenced["routes"]["claude-sonnet-4-6"] = "anthropic"  # type: ignore[index]
        self.assert_invalid_snapshot(unreferenced, "unreferenced non-root routes")

        root_agent = valid_snapshot()
        root_agent["agents"] = deepcopy(root_agent["agents"])
        root_agent["agents"]["airlock-root"] = {  # type: ignore[index]
            "model": "claude-opus-4-6[1m]",
            "provider": "anthropic",
            "extra_usage": False,
        }
        result = policy.validate_session_snapshot(root_agent)
        self.assertEqual(
            result.agents["airlock-root"].model, result.root_model
        )

    def test_one_m_suffix_wire_alias_requires_derivation_and_provider_agreement(self) -> None:
        alias = valid_snapshot()
        alias["routes"] = deepcopy(alias["routes"])
        alias["routes"]["claude-opus-4-6"] = "anthropic"  # type: ignore[index]
        result = policy.validate_session_snapshot(alias)
        self.assertEqual(result.routes["claude-opus-4-6"], "anthropic")

        wrong_provider = deepcopy(alias)
        wrong_provider["routes"]["claude-opus-4-6"] = "openai"  # type: ignore[index]
        self.assert_invalid_snapshot(wrong_provider, "canonical openai model ID")

        unrelated = valid_snapshot()
        unrelated["routes"] = deepcopy(unrelated["routes"])
        unrelated["routes"]["gpt-5.6"] = "openai"  # type: ignore[index]
        self.assert_invalid_snapshot(unrelated, "unreferenced non-root routes")

    def test_snapshot_canonical_size_limit_and_raw_size_limit(self) -> None:
        with mock.patch.object(policy, "MAX_POLICY_BYTES", 1):
            self.assert_invalid_snapshot(valid_snapshot(), "canonical snapshot")
        with self.assertRaises(policy.PolicyValidationError):
            policy.load_session_snapshot_bytes(b" " * (policy.MAX_POLICY_BYTES + 1))

    def test_snapshot_rejects_non_object_maps_and_nested_values(self) -> None:
        for field in ["routes", "agents", "openrouter"]:
            value = valid_snapshot()
            value[field] = []
            with self.subTest(field=field):
                self.assert_invalid_snapshot(value)
        value = valid_snapshot()
        value["agents"] = {"airlock-sol": "gpt-5.6-sol"}
        self.assert_invalid_snapshot(value)
        value = valid_snapshot()
        value["openrouter"] = {"anthropic/claude-sonnet-4.5": "anthropic"}
        self.assert_invalid_snapshot(value)
    def test_snapshot_file_requires_exact_canonical_digest(self) -> None:
        snapshot = policy.validate_session_snapshot(valid_snapshot())
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "session.json"
            path.write_bytes(snapshot.canonical_bytes())
            loaded = policy.load_session_snapshot(path, snapshot.digest())
            self.assertEqual(loaded.canonical_bytes(), snapshot.canonical_bytes())
            for digest in ["", "A" * 64, "0" * 63, "g" * 64]:
                with self.subTest(digest=digest[:8]):
                    with self.assertRaises(policy.PolicyValidationError):
                        policy.load_session_snapshot(path, digest)
            with self.assertRaisesRegex(
                policy.PolicyValidationError, "does not match"
            ):
                policy.load_session_snapshot(path, "0" * 64)
            path.unlink()
            with self.assertRaises(policy.PolicyFileError):
                policy.load_session_snapshot(path, snapshot.digest())


if __name__ == "__main__":
    unittest.main()
