#!/usr/bin/env python3
"""Offline access-policy integration tests for loopback open models."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ACCESS_PATH = ROOT / "bin" / "airlock-access.py"
SPEC = importlib.util.spec_from_file_location("airlock_access_openmodel", ACCESS_PATH)
assert SPEC is not None and SPEC.loader is not None
ACCESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCESS)

PRIVATE_URL = "http://127.0.0.1:18094/v1"
PRIVATE_MODEL = "D:/Private Models/Synthetic Open Model.gguf"
ROOT_ROUTE = "local-root"
ROOT_MODEL = f"openmodel/{ROOT_ROUTE}"


def registry_value(*, worker: bool = True) -> dict[str, object]:
    return {
        "schema_version": 1,
        "endpoints": [{
            "id": "private-endpoint",
            "base_url": PRIVATE_URL,
            "trust": "loopback",
            "protocol": "openai-chat-completions-v1",
            "auth": "none",
            "max_concurrency": 1,
            "enabled": True,
        }],
        "models": [{
            "route": ROOT_ROUTE,
            "endpoint": "private-endpoint",
            "upstream_model": PRIVATE_MODEL,
            "accepted_response_models": [PRIVATE_MODEL],
            "context_window": 131072,
            "max_output_tokens": 8192,
            "streaming": True,
            "tools": "single",
            "tool_choice": ["auto"],
            "worker": worker,
            "enabled": True,
        }],
    }


def catalog_paths() -> dict[str, str]:
    return {
        "openai_direct": str(ROOT / "config" / "openai-direct-agents.json"),
        "anthropic_direct": str(ROOT / "config" / "anthropic-direct-agents.json"),
        "openai_wrappers": str(ROOT / "config" / "hybrid-agents.json"),
        "anthropic_wrappers": str(ROOT / "config" / "claude-agents.json"),
        "grok_direct": str(ROOT / "config" / "grok-agents.json"),
        "grok_wrappers": str(ROOT / "config" / "grok-agents.json"),
    }


class OpenModelAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.registry = self.root / "openmodel-registry.json"
        self.failover = self.root / "failover.json"
        allowed = {
            "AIRLOCK_CONFIG_FILE": str(self.root / "config"),
            "AIRLOCK_ACCESS_FILE": str(self.root / "access.json"),
            "AIRLOCK_OPENMODEL_REGISTRY_FILE": str(self.registry),
            "AIRLOCK_OPENROUTER_REGISTRY_FILE": str(
                self.root / "openrouter-registry.json"
            ),
            "AIRLOCK_FAILOVER_FILE": str(self.failover),
            "AIRLOCK_ACCESS_GROK_AUTH": "1",
            "AIRLOCK_GROK_MODELS": "grok,composer",
        }
        self.environment = patch.dict(os.environ, allowed, clear=False)
        self.environment.start()
        for name in list(os.environ):
            if name.startswith("AIRLOCK_") and name not in allowed:
                os.environ.pop(name, None)

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def write_registry(self, *, worker: bool = True) -> None:
        ACCESS.POLICY_SCHEMA.write_openmodel_registry(
            self.registry, registry_value(worker=worker)
        )

    def test_missing_registry_leaves_existing_profiles_unchanged(self) -> None:
        policy = ACCESS.load_policy()
        self.assertEqual(policy["_openmodel_registry"].models, ())
        self.assertEqual(ACCESS.list_enabled_openmodel_routes(policy), [])
        route_policy = ACCESS.session_route_policy(policy, "openai-pure")
        self.assertNotIn("openmodel", route_policy["routes"].values())
        self.assertFalse(any(
            name.startswith("airlock-om-")
            for name in route_policy["agent_names"]
        ))

    def test_declared_model_and_agent_identity_collisions_fail_closed(self) -> None:
        self.write_registry()
        policy = ACCESS.load_policy()
        policy["_custom_models"] = (
            ACCESS.CustomModelEntry(
                model_id=ROOT_MODEL,
                provider="openrouter",
                effort_ceiling="high",
                context_window=None,
                cost="standard",
                enabled=True,
                agent_name="airlock-custom-openrouter-openmodel-local-root",
            ),
        )
        with self.assertRaisesRegex(ACCESS.AccessError, "model identity"):
            ACCESS.validate_declared_identity_collisions(policy)

        policy["_custom_models"] = (
            ACCESS.CustomModelEntry(
                model_id="synthetic/other",
                provider="openrouter",
                effort_ceiling="high",
                context_window=None,
                cost="standard",
                enabled=True,
                agent_name="airlock-om-local-root",
            ),
        )
        with self.assertRaisesRegex(ACCESS.AccessError, "Agent identity"):
            ACCESS.validate_declared_identity_collisions(policy)

    def test_safe_route_projection_and_cli_never_expose_private_values(self) -> None:
        self.write_registry()
        policy = ACCESS.load_policy()
        expected = [{
            "route": ROOT_ROUTE,
            "agent": "airlock-om-local-root",
            "model": ROOT_MODEL,
            "context_window": 131072,
            "max_output_tokens": 8192,
            "streaming": True,
            "tools": "single",
            "tool_choice": ["auto"],
            "worker": True,
        }]
        self.assertEqual(ACCESS.list_enabled_openmodel_routes(policy), expected)
        self.assertEqual(
            ACCESS._openmodel_route_fields(
                ACCESS.resolve_openmodel_route(policy, ROOT_ROUTE)
            ),
            expected[0],
        )
        for command in (
            ["openmodel-routes"],
            ["openmodel-resolve", ROOT_ROUTE],
        ):
            completed = subprocess.run(
                [sys.executable, str(ACCESS_PATH), *command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=os.environ.copy(),
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertNotIn(PRIVATE_URL, completed.stdout + completed.stderr)
            self.assertNotIn(PRIVATE_MODEL, completed.stdout + completed.stderr)

    def test_invalid_registry_diagnostics_never_expose_private_values(self) -> None:
        self.write_registry()
        value = registry_value()
        value[PRIVATE_MODEL] = "must stay private"
        self.registry.write_text(json.dumps(value), encoding="utf-8")

        with self.assertRaises(ACCESS.AccessError) as caught:
            ACCESS.load_policy()
        self.assertEqual(str(caught.exception), "open-model registry is invalid")
        self.assertNotIn(PRIVATE_MODEL, str(caught.exception))

        completed = subprocess.run(
            [sys.executable, str(ACCESS_PATH), "openmodel-routes"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=os.environ.copy(),
            check=False,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("open-model registry is invalid", completed.stderr)
        self.assertNotIn(PRIVATE_MODEL, completed.stdout + completed.stderr)

    def test_openmodel_profiles_require_only_their_exact_selector(self) -> None:
        self.write_registry()
        policy = ACCESS.load_policy()
        calls = (
            lambda: ACCESS.enabled_profile_workers(policy, "openmodel-pure"),
            lambda: ACCESS.render_profile(policy, "openmodel-pure", {}),
            lambda: ACCESS.proxy_picker_models(policy, "openmodel-pure"),
            lambda: ACCESS.session_route_policy(policy, "openmodel-pure"),
            lambda: ACCESS.profile_guidance(policy, "openmodel-pure"),
            lambda: ACCESS.build_session_snapshot(
                policy, "openmodel-pure", ROOT_MODEL
            ),
        )
        for call in calls:
            with self.subTest(call=call), self.assertRaisesRegex(
                ACCESS.AccessError, "requires an exact open-model root route"
            ):
                call()
        with self.assertRaisesRegex(ACCESS.AccessError, "not valid"):
            ACCESS.session_route_policy(
                policy,
                "openai-pure",
                openmodel_root_route=ROOT_ROUTE,
            )
        with self.assertRaisesRegex(ACCESS.AccessError, "unknown or disabled"):
            ACCESS.session_route_policy(
                policy,
                "openmodel-pure",
                openmodel_root_route="missing",
            )

    def test_pure_profile_maps_only_the_root_and_never_leaks_private_values(self) -> None:
        self.write_registry()
        policy = ACCESS.load_policy()
        route_policy = ACCESS.session_route_policy(
            policy,
            "openmodel-pure",
            openmodel_root_route=ROOT_ROUTE,
        )
        self.assertEqual(
            route_policy["picker_models"],
            {family: ROOT_MODEL for family in ("fable", "opus", "sonnet", "haiku")},
        )
        self.assertEqual(route_policy["discovery_model"], ROOT_MODEL)
        self.assertEqual(route_policy["model_ids"], [ROOT_MODEL])
        self.assertEqual(route_policy["routes"], {ROOT_MODEL: "openmodel"})
        self.assertEqual(
            route_policy["agent_names"], ["airlock-om-local-root"]
        )
        self.assertEqual(route_policy["extra_agent_names"], [])
        self.assertEqual(route_policy["extra_model_ids"], [])

        rendered = ACCESS.render_profile_from_paths(
            policy,
            "openmodel-pure",
            catalog_paths(),
            openmodel_root_route=ROOT_ROUTE,
        )
        self.assertEqual(set(rendered), {"airlock-om-local-root"})
        definition = rendered["airlock-om-local-root"]
        self.assertEqual(definition["model"], ROOT_MODEL)
        self.assertNotIn("effort", definition)
        self.assertNotIn("thinking", json.dumps(definition).lower())
        guidance = ACCESS.profile_guidance(
            policy,
            "openmodel-pure",
            openmodel_root_route=ROOT_ROUTE,
        )
        public_text = json.dumps(route_policy) + json.dumps(rendered) + guidance
        self.assertNotIn(PRIVATE_URL, public_text)
        self.assertNotIn(PRIVATE_MODEL, public_text)

    def test_snapshot_freezes_exact_private_metadata_and_excludes_automation(self) -> None:
        self.write_registry()
        policy = ACCESS.load_policy()
        snapshot = ACCESS.build_session_snapshot(
            policy,
            "openmodel-pure",
            ROOT_MODEL,
            openmodel_root_route=ROOT_ROUTE,
        )
        self.assertEqual(snapshot.protocol_version, 6)
        self.assertEqual(snapshot.root_provider, "openmodel")
        self.assertEqual(snapshot.context_windows[ROOT_MODEL], 131072)
        self.assertEqual(snapshot.openmodel.endpoints["private-endpoint"].base_url, PRIVATE_URL)
        metadata = snapshot.openmodel.models[ROOT_MODEL]
        self.assertEqual(metadata.upstream_model, PRIVATE_MODEL)
        self.assertEqual(metadata.accepted_response_models, (PRIVATE_MODEL,))
        self.assertFalse(snapshot.agents["airlock-om-local-root"].extra_usage)
        self.assertEqual(snapshot.failover, {})
        self.assertEqual(snapshot.compactors, {})
        with self.assertRaisesRegex(ACCESS.AccessError, "does not match"):
            ACCESS.build_session_snapshot(
                policy,
                "openmodel-pure",
                "openmodel/other",
                openmodel_root_route=ROOT_ROUTE,
            )

    def test_hybrid_root_keeps_normal_family_discovery_and_compaction_seats(self) -> None:
        self.write_registry()
        policy = ACCESS.load_policy()
        route_policy = ACCESS.session_route_policy(
            policy,
            "hybrid-openmodel-root",
            openmodel_root_route=ROOT_ROUTE,
        )
        self.assertEqual(route_policy["routes"][ROOT_MODEL], "openmodel")
        self.assertNotIn(ROOT_MODEL, route_policy["picker_models"].values())
        self.assertNotEqual(route_policy["discovery_model"], ROOT_MODEL)
        snapshot = ACCESS.build_session_snapshot(
            policy,
            "hybrid-openmodel-root",
            ROOT_MODEL,
            openmodel_root_route=ROOT_ROUTE,
        )
        self.assertEqual(snapshot.root_provider, "openmodel")
        self.assertTrue(any(provider != "openmodel" for provider in snapshot.routes.values()))
        self.assertNotIn(ROOT_MODEL, snapshot.failover)
        self.assertFalse(any(
            ROOT_MODEL in peers for peers in snapshot.failover.values()
        ))
        self.assertNotIn("openmodel", snapshot.compactors)
        self.assertNotIn(ROOT_MODEL, snapshot.compactors.values())

    def test_worker_false_still_supports_a_pure_root_without_named_agents(self) -> None:
        self.write_registry(worker=False)
        policy = ACCESS.load_policy()
        rendered = ACCESS.render_profile(
            policy,
            "openmodel-pure",
            {},
            openmodel_root_route=ROOT_ROUTE,
        )
        self.assertEqual(rendered, {})
        route_policy = ACCESS.session_route_policy(
            policy,
            "openmodel-pure",
            openmodel_root_route=ROOT_ROUTE,
        )
        self.assertEqual(route_policy["model_ids"], [ROOT_MODEL])
        self.assertEqual(route_policy["agent_names"], [])
        snapshot = ACCESS.build_session_snapshot(
            policy,
            "openmodel-pure",
            ROOT_MODEL,
            openmodel_root_route=ROOT_ROUTE,
        )
        self.assertEqual(snapshot.agents, {})
        settings = json.loads(ACCESS.managed_session_settings_json(
            "{}",
            policy=policy,
            profile="openmodel-pure",
        ))
        context = settings["autoMode"]["environment"][1]
        self.assertIn("local open models", context)
        self.assertIn("No named airlock-* Agents", context)
        with self.assertRaises(ACCESS.AccessError):
            ACCESS.managed_agent_names({})
        completed = subprocess.run(
            [
                sys.executable,
                str(ACCESS_PATH),
                "managed-agent-names",
                "--agents-json",
                "{}",
                "--profile",
                "openmodel-pure",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=os.environ.copy(),
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "\n")
        completed = subprocess.run(
            [
                sys.executable,
                str(ACCESS_PATH),
                "managed-agent-names",
                "--agents-json",
                "{}",
                "--profile",
                "hybrid-openmodel-root",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=os.environ.copy(),
            check=False,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("empty or invalid", completed.stderr)

    def test_declared_failover_cannot_use_a_local_source_or_peer(self) -> None:
        self.write_registry()
        policy = ACCESS.load_policy()
        nonlocal_model = next(
            worker["model"]
            for worker in ACCESS.enabled_profile_workers(
                policy,
                "hybrid-openmodel-root",
                openmodel_root_route=ROOT_ROUTE,
            )
            if worker["provider"] != "openmodel"
        )
        for chains in (
            {ROOT_MODEL: [nonlocal_model]},
            {nonlocal_model: [ROOT_MODEL]},
        ):
            with self.subTest(chains=chains):
                self.failover.write_text(json.dumps({
                    "schema_version": 1,
                    "chains": chains,
                }), encoding="utf-8")
                with self.assertRaisesRegex(
                    ACCESS.AccessError,
                    "unknown model|openmodel routes must not appear",
                ):
                    ACCESS.build_session_snapshot(
                        policy,
                        "hybrid-openmodel-root",
                        ROOT_MODEL,
                        openmodel_root_route=ROOT_ROUTE,
                    )


if __name__ == "__main__":
    unittest.main()
