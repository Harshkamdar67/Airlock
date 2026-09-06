#!/usr/bin/env python3
"""Offline tests for the private open-model registry and snapshot schema."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "bin" / "airlock_policy.py"
SPEC = importlib.util.spec_from_file_location("airlock_policy_openmodel_test", POLICY_PATH)
assert SPEC is not None and SPEC.loader is not None
policy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = policy
SPEC.loader.exec_module(policy)


def valid_endpoint(**changes: object) -> dict[str, object]:
    endpoint: dict[str, object] = {
        "id": "desk-llama",
        "base_url": "http://127.0.0.1:18093/v1",
        "trust": "loopback",
        "protocol": "openai-chat-completions-v1",
        "auth": "none",
        "max_concurrency": 1,
        "enabled": True,
    }
    endpoint.update(changes)
    return endpoint


def valid_model(**changes: object) -> dict[str, object]:
    model: dict[str, object] = {
        "route": "synthetic-route",
        "endpoint": "desk-llama",
        "upstream_model": "D:/Models/Synthetic Model.gguf",
        "accepted_response_models": ["D:/Models/Synthetic Model.gguf"],
        "context_window": 32768,
        "max_output_tokens": 8192,
        "streaming": True,
        "tools": "single",
        "tool_choice": ["auto"],
        "worker": True,
        "enabled": True,
    }
    model.update(changes)
    return model


def valid_registry(
    *,
    endpoints: list[dict[str, object]] | None = None,
    models: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "endpoints": [valid_endpoint()] if endpoints is None else endpoints,
        "models": [valid_model()] if models is None else models,
    }


def snapshot_metadata() -> dict[str, object]:
    model = valid_model()
    return {
        "endpoints": {
            "desk-llama": {
                "base_url": "http://127.0.0.1:18093/v1",
                "trust": "loopback",
                "protocol": "openai-chat-completions-v1",
                "auth": "none",
                "max_concurrency": 1,
            }
        },
        "models": {
            "openmodel/synthetic-route": {
                key: model[key]
                for key in (
                    "endpoint",
                    "upstream_model",
                    "accepted_response_models",
                    "context_window",
                    "max_output_tokens",
                    "streaming",
                    "tools",
                    "tool_choice",
                )
            }
        },
    }


def valid_snapshot() -> dict[str, object]:
    return {
        "schema_version": 1,
        "protocol_version": 6,
        "profile": "openmodel-pure",
        "root_model": "openmodel/synthetic-route",
        "root_provider": "openmodel",
        "routes": {"openmodel/synthetic-route": "openmodel"},
        "agents": {
            "airlock-om-synthetic-route": {
                "model": "openmodel/synthetic-route",
                "provider": "openmodel",
                "extra_usage": False,
            }
        },
        "openrouter": {},
        "openmodel": snapshot_metadata(),
        "failover": {},
        "context_windows": {"openmodel/synthetic-route": 32768},
        "route_categories": {},
        "effort_ceilings": {},
        "compactors": {},
        "overflow_shrink": "auto",
    }


class RegistryValidationTests(unittest.TestCase):
    def assert_invalid(self, value: object, message: str | None = None) -> None:
        with self.assertRaises(policy.PolicyValidationError) as caught:
            policy.validate_openmodel_registry(value)
        if message is not None:
            self.assertIn(message, str(caught.exception))

    def test_valid_registry_is_immutable_canonical_and_derives_safe_names(self) -> None:
        registry = policy.validate_openmodel_registry(valid_registry())
        self.assertEqual(registry.endpoints[0].id, "desk-llama")
        self.assertEqual(registry.models[0].wire_model, "openmodel/synthetic-route")
        self.assertEqual(registry.models[0].agent_name, "airlock-om-synthetic-route")
        self.assertEqual(registry.models[0].tool_choice, ("auto",))
        self.assertEqual(registry.active_models(), registry.models)
        self.assertEqual(
            registry.digest(),
            policy.sha256_bytes(registry.canonical_bytes()),
        )
        with self.assertRaises(Exception):
            registry.models[0].route = "changed"

    def test_empty_registry_is_valid(self) -> None:
        registry = policy.validate_openmodel_registry(
            valid_registry(endpoints=[], models=[])
        )
        self.assertEqual(registry.endpoints, ())
        self.assertEqual(registry.models, ())

    def test_disabled_endpoint_or_model_is_not_active(self) -> None:
        endpoint_disabled = policy.validate_openmodel_registry(
            valid_registry(endpoints=[valid_endpoint(enabled=False)])
        )
        self.assertEqual(endpoint_disabled.active_models(), ())
        model_disabled = policy.validate_openmodel_registry(
            valid_registry(models=[valid_model(enabled=False)])
        )
        self.assertEqual(model_disabled.active_models(), ())

    def test_top_level_and_entry_fields_are_exact(self) -> None:
        for value in [
            [],
            {"schema_version": 1, "endpoints": []},
            {"schema_version": 2, "endpoints": [], "models": []},
            {"schema_version": True, "endpoints": [], "models": []},
            {"schema_version": 1, "endpoints": {}, "models": []},
            {"schema_version": 1, "endpoints": [], "models": {}},
        ]:
            with self.subTest(value=value):
                self.assert_invalid(value)
        endpoint = valid_endpoint()
        endpoint["headers"] = {"Authorization": "secret"}
        self.assert_invalid(valid_registry(endpoints=[endpoint]), "unknown")
        model = valid_model()
        model["prompt"] = "untrusted"
        self.assert_invalid(valid_registry(models=[model]), "unknown")

    def test_only_canonical_literal_loopback_urls_are_accepted(self) -> None:
        for port in ["1", "80", "18093", "65535"]:
            with self.subTest(port=port):
                registry = policy.validate_openmodel_registry(
                    valid_registry(
                        endpoints=[
                            valid_endpoint(base_url=f"http://127.0.0.1:{port}/v1")
                        ]
                    )
                )
                self.assertEqual(
                    registry.endpoints[0].base_url,
                    f"http://127.0.0.1:{port}/v1",
                )
        invalid = [
            "http://localhost:18093/v1",
            "http://127.0.0.2:18093/v1",
            "http://127.0.0.1/v1",
            "http://127.0.0.1:018093/v1",
            "http://127.0.0.1:0/v1",
            "http://127.0.0.1:65536/v1",
            "https://127.0.0.1:18093/v1",
            "HTTP://127.0.0.1:18093/v1",
            "http://user@127.0.0.1:18093/v1",
            "http://127.0.0.1:18093/v1/",
            "http://127.0.0.1:18093/v1/chat/completions",
            "http://127.0.0.1:18093/%76%31",
            "http://127.0.0.1:18093/v1?x=1",
            "http://127.0.0.1:18093/v1#fragment",
            "http://[::1]:18093/v1",
        ]
        for base_url in invalid:
            with self.subTest(base_url=base_url):
                self.assert_invalid(
                    valid_registry(endpoints=[valid_endpoint(base_url=base_url)])
                )

    def test_endpoint_contract_and_limits_are_strict(self) -> None:
        for field, values in {
            "id": ["", "Upper", "two--parts", "a" * 41],
            "trust": ["local", True],
            "protocol": ["openai", None],
            "auth": ["bearer", False],
            "max_concurrency": [0, 65, True, "1"],
            "enabled": [0, "yes", None],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assert_invalid(
                        valid_registry(endpoints=[valid_endpoint(**{field: value})])
                    )

    def test_private_model_identity_is_printable_bounded_and_not_route_shaped(self) -> None:
        values = [
            "D:/Models/Synthetic Model.gguf",
            "模型/本地.gguf",
            "vendor:model with spaces",
        ]
        for identity in values:
            with self.subTest(identity=identity):
                registry = policy.validate_openmodel_registry(
                    valid_registry(
                        models=[
                            valid_model(
                                upstream_model=identity,
                                accepted_response_models=[identity],
                            )
                        ]
                    )
                )
                self.assertEqual(registry.models[0].upstream_model, identity)
        for identity in [
            "",
            " leading",
            "trailing ",
            "line\nbreak",
            "nul\0byte",
            "bidi\u202etext",
            "a" * (policy.MAX_OPENMODEL_IDENTITY_CHARS + 1),
        ]:
            with self.subTest(identity=repr(identity)[:40]):
                self.assert_invalid(
                    valid_registry(
                        models=[
                            valid_model(
                                upstream_model=identity,
                                accepted_response_models=[identity],
                            )
                        ]
                    )
                )

    def test_capability_combinations_are_explicit(self) -> None:
        for tools, choices in [
            ("none", []),
            ("single", ["auto"]),
            ("parallel", ["auto", "named", "none", "required"]),
        ]:
            with self.subTest(tools=tools):
                model = policy.validate_openmodel_registry(
                    valid_registry(models=[valid_model(tools=tools, tool_choice=choices)])
                ).models[0]
                self.assertEqual(model.tools, tools)
                self.assertEqual(model.tool_choice, tuple(choices))
        for tools, choices in [
            ("none", ["auto"]),
            ("single", []),
            ("single", ["none"]),
            ("single", ["auto", "auto"]),
            ("single", ["required", "auto"]),
            ("unknown", []),
            ("single", ["auto", "unsupported"]),
        ]:
            with self.subTest(tools=tools, choices=choices):
                self.assert_invalid(
                    valid_registry(models=[valid_model(tools=tools, tool_choice=choices)])
                )

    def test_model_references_limits_and_boolean_fields_are_strict(self) -> None:
        cases = [
            valid_model(endpoint="missing"),
            valid_model(route="Upper"),
            valid_model(context_window=999),
            valid_model(context_window=policy.MAX_CONTEXT_WINDOW_TOKENS + 1),
            valid_model(max_output_tokens=0),
            valid_model(max_output_tokens=170241),
            valid_model(streaming=1),
            valid_model(worker="yes"),
            valid_model(enabled=1),
            valid_model(accepted_response_models=[]),
            valid_model(accepted_response_models=["z", "a"]),
            valid_model(accepted_response_models=["same", "same"]),
        ]
        for model in cases:
            with self.subTest(model=str(model)[:100]):
                self.assert_invalid(valid_registry(models=[model]))

    def test_duplicate_urls_and_ambiguous_model_bindings_are_rejected(self) -> None:
        duplicate_url = valid_registry(
            endpoints=[valid_endpoint(), valid_endpoint(id="second")]
        )
        self.assert_invalid(duplicate_url, "duplicate open-model endpoint URL")
        endpoints = [
            valid_endpoint(),
            valid_endpoint(id="second", base_url="http://127.0.0.1:8094/v1"),
        ]
        duplicate_request = valid_registry(
            endpoints=endpoints,
            models=[valid_model(), valid_model(route="second")],
        )
        self.assert_invalid(duplicate_request, "upstream model binding")
        ambiguous_response = valid_registry(
            endpoints=endpoints,
            models=[
                valid_model(),
                valid_model(
                    route="second",
                    upstream_model="different",
                    accepted_response_models=["D:/Models/Synthetic Model.gguf"],
                ),
            ],
        )
        self.assert_invalid(ambiguous_response, "ambiguous")
        separate_endpoint = policy.validate_openmodel_registry(
            valid_registry(
                endpoints=endpoints,
                models=[
                    valid_model(),
                    valid_model(
                        route="second",
                        endpoint="second",
                    ),
                ],
            )
        )
        self.assertEqual(len(separate_endpoint.models), 2)


class RegistryFileTests(unittest.TestCase):
    def test_private_writer_loader_and_compare_and_swap_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "private" / "openmodel-registry.json"
            original = policy.write_openmodel_registry(path, valid_registry())
            self.assertEqual(policy.load_openmodel_registry(path), original)
            self.assertEqual(path.read_bytes(), original.canonical_bytes() + b"\n")
            updated_value = valid_registry(
                models=[valid_model(context_window=131072)]
            )
            updated_value["models"][0]["max_output_tokens"] = 4096  # type: ignore[index]
            updated = policy.compare_and_swap_openmodel_registry(
                path, original.digest(), updated_value
            )
            self.assertEqual(policy.load_openmodel_registry(path), updated)
            self.assertTrue((path.parent / ".openmodel-registry.lock").is_file())
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_compare_and_swap_rejects_concurrent_change_and_missing_is_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "openmodel-registry.json"
            original = policy.write_openmodel_registry(path, valid_registry())
            policy.write_openmodel_registry(
                path,
                valid_registry(models=[valid_model(worker=False)]),
            )
            before = path.read_bytes()
            with self.assertRaises(policy.RegistryConflictError):
                policy.compare_and_swap_openmodel_registry(
                    path, original.digest(), valid_registry()
                )
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaises(policy.RegistryNotFoundError):
                policy.load_openmodel_registry(Path(temporary_text) / "missing.json")


class SnapshotValidationTests(unittest.TestCase):
    def assert_invalid(self, value: object, message: str | None = None) -> None:
        with self.assertRaises(policy.PolicyValidationError) as caught:
            policy.validate_session_snapshot(value)
        if message is not None:
            self.assertIn(message, str(caught.exception))

    def test_valid_openmodel_snapshot_is_immutable_and_canonical(self) -> None:
        snapshot = policy.validate_session_snapshot(valid_snapshot())
        self.assertEqual(snapshot.root_provider, "openmodel")
        self.assertEqual(snapshot.root_model, "openmodel/synthetic-route")
        self.assertEqual(
            snapshot.openmodel.models["openmodel/synthetic-route"].endpoint,
            "desk-llama",
        )
        self.assertEqual(
            snapshot.openmodel.endpoints["desk-llama"].max_concurrency,
            1,
        )
        self.assertEqual(snapshot.to_dict(), valid_snapshot())
        with self.assertRaises(TypeError):
            snapshot.openmodel.models["new"] = snapshot.openmodel.models["openmodel/synthetic-route"]

    def test_old_snapshot_without_openmodel_metadata_remains_valid(self) -> None:
        value = {
            "schema_version": 1,
            "protocol_version": 5,
            "profile": "openai-pure",
            "root_model": "gpt-5.6-sol",
            "root_provider": "openai",
            "routes": {"gpt-5.6-sol": "openai"},
            "agents": {},
            "openrouter": {},
        }
        snapshot = policy.validate_session_snapshot(value)
        self.assertEqual(snapshot.openmodel.models, {})
        self.assertEqual(snapshot.openmodel.endpoints, {})

    def test_openmodel_metadata_must_exactly_cover_routes_and_endpoints(self) -> None:
        missing_model = valid_snapshot()
        missing_model["openmodel"] = deepcopy(missing_model["openmodel"])
        missing_model["openmodel"]["models"] = {}  # type: ignore[index]
        self.assert_invalid(missing_model, "routes and metadata")

        dangling_endpoint = valid_snapshot()
        dangling_endpoint["openmodel"] = deepcopy(dangling_endpoint["openmodel"])
        dangling_endpoint["openmodel"]["endpoints"]["unused"] = {  # type: ignore[index]
            "base_url": "http://127.0.0.1:8094/v1",
            "trust": "loopback",
            "protocol": "openai-chat-completions-v1",
            "auth": "none",
            "max_concurrency": 1,
        }
        self.assert_invalid(dangling_endpoint, "referenced exactly")

        credential = valid_snapshot()
        credential["openmodel"] = deepcopy(credential["openmodel"])
        credential["openmodel"]["endpoints"]["desk-llama"]["api_key"] = "secret"  # type: ignore[index]
        self.assert_invalid(credential, "unknown")

    def test_context_window_must_match_private_route_metadata(self) -> None:
        missing = valid_snapshot()
        missing["context_windows"] = {}
        self.assert_invalid(missing, "must match")
        mismatch = valid_snapshot()
        mismatch["context_windows"] = {"openmodel/synthetic-route": 131072}
        self.assert_invalid(mismatch, "must match")

    def test_openmodel_agents_are_generated_and_never_extra_usage(self) -> None:
        wrong_name = valid_snapshot()
        wrong_name["agents"] = {
            "airlock-local": {
                "model": "openmodel/synthetic-route",
                "provider": "openmodel",
                "extra_usage": False,
            }
        }
        self.assert_invalid(wrong_name, "generated open-model Agent name")
        extra = valid_snapshot()
        extra["agents"] = deepcopy(extra["agents"])
        extra["agents"]["airlock-om-synthetic-route"]["extra_usage"] = True  # type: ignore[index]
        self.assert_invalid(extra, "local compute")

    def test_openmodel_routes_cannot_enter_failover_or_compactors(self) -> None:
        as_source = valid_snapshot()
        as_source["routes"] = {
            **as_source["routes"],  # type: ignore[dict-item]
            "gpt-5.6-sol": "openai",
        }
        as_source["agents"] = {
            **as_source["agents"],  # type: ignore[dict-item]
            "airlock-sol": {
                "model": "gpt-5.6-sol",
                "provider": "openai",
                "extra_usage": False,
            },
        }
        as_source["failover"] = {"openmodel/synthetic-route": ["gpt-5.6-sol"]}
        self.assert_invalid(as_source, "must not appear")

        as_peer = deepcopy(as_source)
        as_peer["failover"] = {"gpt-5.6-sol": ["openmodel/synthetic-route"]}
        self.assert_invalid(as_peer, "must not appear")

        compactor = valid_snapshot()
        compactor["compactors"] = {"openmodel": "openmodel/synthetic-route"}
        self.assert_invalid(compactor, "compactors keys")

    def test_openmodel_wire_ids_are_provider_specific(self) -> None:
        for root_model in ["synthetic-route", "openmodel/Upper", "openmodel/a/b"]:
            value = valid_snapshot()
            value["root_model"] = root_model
            value["routes"] = {root_model: "openmodel"}
            value["agents"] = {}
            value["openmodel"] = {"endpoints": {}, "models": {}}
            value["context_windows"] = {}
            with self.subTest(root_model=root_model):
                self.assert_invalid(value)


if __name__ == "__main__":
    unittest.main()
