#!/usr/bin/env python3
"""Offline integration tests for registry-backed OpenRouter workers."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ACCESS_PATH = ROOT / "bin" / "airlock-access.py"
SPEC = importlib.util.spec_from_file_location("airlock_access_openrouter", ACCESS_PATH)
assert SPEC is not None and SPEC.loader is not None
ACCESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCESS)


def registry_entry(**changes: object) -> dict[str, object]:
    now = int(time.time())
    value: dict[str, object] = {
        "route": "declared-sonnet",
        "model": "anthropic/claude-sonnet-4.5",
        "endpoint_provider": "anthropic",
        "provider_name": "Anthropic",
        "provider_slug": "anthropic",
        "quantization": "bf16",
        "canonical_slug": "anthropic/claude-sonnet-4.5-20250929",
        "alias_target": None,
        "supported_parameters": ["tool_choice", "tools"],
        "expiration_date": None,
        "checked_at": now,
        "enabled": True,
    }
    value.update(changes)
    return value


def preset_registry_entry(**changes: object) -> dict[str, object]:
    preset = ACCESS.OPENROUTER_PRESETS.preset_by_name("kimi-k3")
    assert preset is not None
    value = registry_entry(
        route=preset.route,
        model=preset.model,
        endpoint_provider=preset.endpoint_provider,
        provider_name=preset.provider_name,
        provider_slug=preset.provider_slug,
        quantization=preset.quantization,
        canonical_slug=preset.canonical_slug,
    )
    value.update(changes)
    return value


def catalog_paths() -> dict[str, str]:
    return {
        "openai_direct": str(ROOT / "config" / "openai-direct-agents.json"),
        "anthropic_direct": str(ROOT / "config" / "anthropic-direct-agents.json"),
        "openai_wrappers": str(ROOT / "config" / "hybrid-agents.json"),
        "anthropic_wrappers": str(ROOT / "config" / "claude-agents.json"),
        "grok_direct": str(ROOT / "config" / "grok-agents.json"),
        "grok_wrappers": str(ROOT / "config" / "grok-agents.json"),
    }


class OpenRouterAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = self.root / "config"
        self.registry = self.root / "openrouter-registry.json"
        self.environment = patch.dict(os.environ, {
            "AIRLOCK_CONFIG_FILE": str(self.config),
            "AIRLOCK_ACCESS_FILE": str(self.root / "access.json"),
            "AIRLOCK_OPENROUTER_REGISTRY_FILE": str(self.registry),
            "AIRLOCK_ACCESS_GROK_AUTH": "1",
        }, clear=False)
        self.environment.start()
        # A suite run inside an Airlock session inherits its live
        # configuration, including the saved Agent depth; tests must see only
        # the overrides above.
        for name in list(os.environ):
            if name.startswith("AIRLOCK_") and name not in {
                "AIRLOCK_CONFIG_FILE",
                "AIRLOCK_ACCESS_FILE",
                "AIRLOCK_OPENROUTER_REGISTRY_FILE",
                "AIRLOCK_ACCESS_GROK_AUTH",
            }:
                os.environ.pop(name, None)

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def write_registry(self, *entries: dict[str, object]) -> None:
        self.registry.write_text(json.dumps({
            "schema_version": 1,
            "models": list(entries),
        }), encoding="utf-8")

    def test_enabled_route_list_and_exact_resolver_are_offline_machine_json(self) -> None:
        selected = registry_entry()
        disabled = registry_entry(
            route="disabled-route",
            model="vendor/disabled-model",
            canonical_slug="vendor/disabled-model-20260810",
            enabled=False,
        )
        self.write_registry(disabled, selected)
        policy = ACCESS.load_policy()

        listed = ACCESS.list_enabled_openrouter_routes(policy)
        self.assertEqual(listed, [{
            "route": "declared-sonnet",
            "agent": "airlock-or-declared-sonnet",
            "model": "anthropic/claude-sonnet-4.5",
            "endpoint_provider": "anthropic",
            "provider_name": "Anthropic",
            "provider_slug": "anthropic",
            "quantization": "bf16",
            "canonical_slug": "anthropic/claude-sonnet-4.5-20250929",
        }])
        resolved = ACCESS.resolve_openrouter_route(policy, "declared-sonnet")
        self.assertEqual(ACCESS._openrouter_route_fields(resolved), listed[0])
        for route in ("declared", "DECLARED-SONNET", "disabled-route", "missing"):
            with self.subTest(route=route), self.assertRaisesRegex(
                ACCESS.AccessError, "unknown or disabled"
            ):
                ACCESS.resolve_openrouter_route(policy, route)

        environment = dict(os.environ)
        listed_cli = subprocess.run(
            [sys.executable, str(ACCESS_PATH), "openrouter-routes"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=False,
        )
        self.assertEqual(listed_cli.returncode, 0, listed_cli.stderr)
        self.assertEqual(json.loads(listed_cli.stdout), listed)
        resolved_cli = subprocess.run(
            [sys.executable, str(ACCESS_PATH), "openrouter-resolve", "declared-sonnet"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=False,
        )
        self.assertEqual(resolved_cli.returncode, 0, resolved_cli.stderr)
        self.assertEqual(json.loads(resolved_cli.stdout), listed[0])

    def test_openrouter_pure_requires_route_rejects_unexpected_route_and_has_empty_catalogs(self) -> None:
        self.write_registry(registry_entry())
        policy = ACCESS.load_policy()
        self.assertEqual(ACCESS.PROFILE_COMPONENTS["openrouter-pure"], ())
        self.assertIsNone(ACCESS.PROFILE_COMPONENTS.get("not-a-profile"))

        missing_route_calls = (
            lambda: ACCESS.enabled_profile_workers(policy, "openrouter-pure"),
            lambda: ACCESS.render_profile(policy, "openrouter-pure", {}),
            lambda: ACCESS.render_profile_from_paths(policy, "openrouter-pure", {}),
            lambda: ACCESS.proxy_picker_models(policy, "openrouter-pure"),
            lambda: ACCESS.profile_guidance(policy, "openrouter-pure"),
            lambda: ACCESS.session_route_policy(policy, "openrouter-pure"),
            lambda: ACCESS.build_session_snapshot(
                policy, "openrouter-pure", "anthropic/claude-sonnet-4.5"
            ),
        )
        for call in missing_route_calls:
            with self.subTest(call=call), self.assertRaisesRegex(
                ACCESS.AccessError, "requires an exact OpenRouter root route"
            ):
                call()

        rendered = ACCESS.render_profile_from_paths(
            policy,
            "openrouter-pure",
            {},
            openrouter_root_route="declared-sonnet",
        )
        self.assertEqual(set(rendered), {"airlock-or-declared-sonnet"})
        with self.assertRaisesRegex(ACCESS.AccessError, "unknown session profile"):
            ACCESS.render_profile(policy, "not-a-profile", {})

        unexpected_route_calls = (
            lambda: ACCESS.enabled_profile_workers(
                policy,
                "hybrid-anthropic-root",
                openrouter_root_route="declared-sonnet",
            ),
            lambda: ACCESS.render_profile(
                policy,
                "openai-pure",
                {},
                openrouter_root_route="declared-sonnet",
            ),
            lambda: ACCESS.session_route_policy(
                policy,
                "hybrid-openai-root",
                openrouter_root_route="declared-sonnet",
            ),
            lambda: ACCESS.profile_guidance(
                policy,
                "grok-pure",
                openrouter_root_route="declared-sonnet",
            ),
        )
        for call in unexpected_route_calls:
            with self.subTest(call=call), self.assertRaisesRegex(
                ACCESS.AccessError, "not valid for session profile"
            ):
                call()

    def test_openrouter_pure_root_and_other_route_gating_across_policies(self) -> None:
        self.write_registry(
            registry_entry(),
            registry_entry(
                route="other-route",
                model="vendor/other-model",
                endpoint_provider="deepinfra/turbo",
                provider_name="DeepInfra",
                provider_slug="deepinfra",
                canonical_slug="vendor/other-model-20260810",
            ),
        )
        policy = ACCESS.load_policy()
        root_model = "anthropic/claude-sonnet-4.5"
        other_model = "vendor/other-model"
        for extra_policy in ("never", "ask", "allow"):
            with self.subTest(extra_policy=extra_policy):
                policy["policies"]["extra_usage"] = extra_policy
                workers = ACCESS.enabled_profile_workers(
                    policy,
                    "openrouter-pure",
                    openrouter_root_route="declared-sonnet",
                )
                by_model = {worker["model"]: worker for worker in workers}
                self.assertEqual(by_model[root_model]["access"], "included")
                if extra_policy == "never":
                    self.assertEqual(set(by_model), {root_model})
                else:
                    self.assertEqual(set(by_model), {root_model, other_model})
                    self.assertEqual(by_model[other_model]["access"], "extra")

                route_policy = ACCESS.session_route_policy(
                    policy,
                    "openrouter-pure",
                    openrouter_root_route="declared-sonnet",
                )
                self.assertEqual(
                    set(route_policy["picker_models"].values()), {root_model}
                )
                self.assertEqual(route_policy["discovery_model"], root_model)
                self.assertEqual(
                    ACCESS.discovery_model(
                        policy,
                        "openrouter-pure",
                        openrouter_root_route="declared-sonnet",
                    ),
                    root_model,
                )
                self.assertNotIn(root_model, route_policy["extra_model_ids"])
                self.assertNotIn(
                    "airlock-or-declared-sonnet",
                    route_policy["extra_agent_names"],
                )
                if extra_policy == "ask":
                    self.assertEqual(route_policy["extra_model_ids"], [other_model])
                    self.assertEqual(
                        route_policy["extra_agent_names"],
                        ["airlock-or-other-route"],
                    )
                else:
                    self.assertEqual(route_policy["extra_model_ids"], [])
                    self.assertEqual(route_policy["extra_agent_names"], [])

                rendered = ACCESS.render_profile(
                    policy,
                    "openrouter-pure",
                    {},
                    openrouter_root_route="declared-sonnet",
                )
                root_description = rendered["airlock-or-declared-sonnet"]["description"]
                self.assertIn("explicitly selected route for normal root traffic", root_description)
                self.assertIn("does not verify", root_description)
                if extra_policy != "never":
                    other_description = rendered["airlock-or-other-route"]["description"]
                    if extra_policy == "ask":
                        self.assertIn("explicit extra-usage authorization", other_description)
                    else:
                        self.assertIn("without per-call confirmation", other_description)

        guidance = ACCESS.profile_guidance(
            policy,
            "openrouter-pure",
            openrouter_root_route="declared-sonnet",
        )
        self.assertIn("carry normal root traffic", guidance)
        self.assertIn("not marked extra in this session policy", guidance)
        self.assertIn("No other OpenRouter route may enter a built-in alias", guidance)
        self.assertIn("does not claim a cheaper, smaller, or more capable route", guidance)
        self.assertIn("does not infer OpenRouter model capability", guidance)

    def test_openrouter_pure_snapshot_has_exact_non_extra_root_and_other_semantics(self) -> None:
        self.write_registry(
            registry_entry(endpoint_provider="deepinfra/turbo"),
            registry_entry(
                route="other-route",
                model="vendor/other-model",
                endpoint_provider="provider/pinned",
                provider_name="Pinned Provider",
                provider_slug="pinned-provider",
                quantization="fp8",
                canonical_slug="vendor/other-model-20260810",
            ),
        )
        policy = ACCESS.load_policy()
        for extra_policy in ("never", "ask", "allow"):
            with self.subTest(extra_policy=extra_policy):
                policy["policies"]["extra_usage"] = extra_policy
                snapshot = ACCESS.build_session_snapshot(
                    policy,
                    "openrouter-pure",
                    "anthropic/claude-sonnet-4.5",
                    openrouter_root_route="declared-sonnet",
                )
                self.assertEqual(snapshot.protocol_version, 5)
                self.assertEqual(snapshot.root_provider, "openrouter")
                self.assertEqual(snapshot.root_model, "anthropic/claude-sonnet-4.5")
                self.assertEqual(
                    snapshot.routes["anthropic/claude-sonnet-4.5"], "openrouter"
                )
                root = snapshot.agents["airlock-or-declared-sonnet"]
                self.assertEqual(root.model, "anthropic/claude-sonnet-4.5")
                self.assertEqual(root.provider, "openrouter")
                self.assertFalse(root.extra_usage)
                metadata = snapshot.openrouter["anthropic/claude-sonnet-4.5"]
                self.assertEqual(metadata.endpoint_provider, "deepinfra/turbo")
                self.assertEqual(metadata.provider_name, "Anthropic")
                self.assertEqual(metadata.provider_slug, "anthropic")
                self.assertEqual(metadata.quantization, "bf16")
                self.assertEqual(
                    metadata.canonical_slug,
                    "anthropic/claude-sonnet-4.5-20250929",
                )
                if extra_policy == "never":
                    self.assertEqual(
                        set(snapshot.agents), {"airlock-or-declared-sonnet"}
                    )
                    self.assertEqual(
                        set(snapshot.openrouter), {"anthropic/claude-sonnet-4.5"}
                    )
                else:
                    other = snapshot.agents["airlock-or-other-route"]
                    self.assertIs(other.extra_usage, extra_policy == "ask")
                    self.assertEqual(
                        snapshot.openrouter["vendor/other-model"].endpoint_provider,
                        "provider/pinned",
                    )

        path, digest = ACCESS.write_session_snapshot(
            policy,
            "openrouter-pure",
            "anthropic/claude-sonnet-4.5",
            self.root / "openrouter-pure-runtime",
            openrouter_root_route="declared-sonnet",
        )
        written = ACCESS.POLICY_SCHEMA.load_session_snapshot(path, digest)
        self.assertEqual(written.root_provider, "openrouter")
        self.assertFalse(
            written.agents["airlock-or-declared-sonnet"].extra_usage
        )

        with self.assertRaisesRegex(ACCESS.AccessError, "does not match"):
            ACCESS.build_session_snapshot(
                policy,
                "openrouter-pure",
                "vendor/other-model",
                openrouter_root_route="declared-sonnet",
            )

    def test_openrouter_pure_rejects_unknown_and_disabled_root_routes(self) -> None:
        self.write_registry(
            registry_entry(enabled=False),
            registry_entry(
                route="enabled-route",
                model="vendor/enabled-model",
                canonical_slug="vendor/enabled-model-20260810",
            ),
        )
        policy = ACCESS.load_policy()
        for route in ("declared-sonnet", "enabled", "ENABLED-ROUTE", "missing"):
            with self.subTest(route=route), self.assertRaisesRegex(
                ACCESS.AccessError, "unknown or disabled"
            ):
                ACCESS.enabled_profile_workers(
                    policy,
                    "openrouter-pure",
                    openrouter_root_route=route,
                )

    def test_hybrid_openrouter_exposure_is_unchanged_by_root_profile(self) -> None:
        self.write_registry(registry_entry())
        policy = ACCESS.load_policy()
        static_policy = ACCESS.apply_runtime_overrides(ACCESS.load_cached_policy())
        for extra_policy in ("never", "ask", "allow"):
            with self.subTest(extra_policy=extra_policy):
                policy["policies"]["extra_usage"] = extra_policy
                static_policy["policies"]["extra_usage"] = extra_policy
                workers = ACCESS.enabled_profile_workers(
                    policy, "hybrid-anthropic-root"
                )
                static_workers = ACCESS.enabled_profile_workers(
                    static_policy, "hybrid-anthropic-root"
                )
                self.assertEqual(
                    [worker for worker in workers if worker["provider"] != "openrouter"],
                    static_workers,
                )
                openrouter = [
                    worker for worker in workers if worker["provider"] == "openrouter"
                ]
                if extra_policy == "never":
                    self.assertEqual(openrouter, [])
                else:
                    self.assertEqual(len(openrouter), 1)
                    self.assertEqual(openrouter[0]["access"], "extra")

    def test_missing_registry_preserves_existing_profile_outputs(self) -> None:
        policy = ACCESS.load_policy()
        without_registry = ACCESS.apply_runtime_overrides(ACCESS.load_cached_policy())
        self.assertEqual(
            ACCESS.session_route_policy(policy, "hybrid-anthropic-root"),
            ACCESS.session_route_policy(without_registry, "hybrid-anthropic-root"),
        )
        self.assertEqual(
            ACCESS.render_profile_from_paths(
                policy, "hybrid-anthropic-root", catalog_paths()
            ),
            ACCESS.render_profile_from_paths(
                without_registry, "hybrid-anthropic-root", catalog_paths()
            ),
        )
        self.assertEqual(
            ACCESS.profile_guidance(policy, "hybrid-anthropic-root"),
            ACCESS.profile_guidance(without_registry, "hybrid-anthropic-root"),
        )
        rendered = ACCESS.render_profile_from_paths(
            policy, "hybrid-anthropic-root", catalog_paths()
        )
        self.assertNotIn("airlock-or-kimi-k3", rendered)

    def test_enabled_entry_adds_one_exact_hybrid_worker_and_route(self) -> None:
        self.write_registry(registry_entry())
        policy = ACCESS.load_policy()
        workers = ACCESS.enabled_profile_workers(policy, "hybrid-anthropic-root")
        worker = next(item for item in workers if item["provider"] == "openrouter")
        self.assertEqual(worker["agent"], "airlock-or-declared-sonnet")
        self.assertEqual(worker["model"], "anthropic/claude-sonnet-4.5")
        self.assertEqual(worker["endpoint_provider"], "anthropic")
        self.assertEqual(worker["access"], "extra")

        routes = ACCESS.session_route_policy(policy, "hybrid-anthropic-root")
        self.assertEqual(
            routes["routes"]["anthropic/claude-sonnet-4.5"], "openrouter"
        )
        self.assertIn("airlock-or-declared-sonnet", routes["agent_names"])
        self.assertIn("airlock-or-declared-sonnet", routes["extra_agent_names"])
        self.assertIn(
            "anthropic/claude-sonnet-4.5", routes["extra_model_ids"]
        )

    def test_generated_agent_is_neutral_exact_and_cannot_spawn(self) -> None:
        self.write_registry(registry_entry(endpoint_provider="deepinfra/turbo"))
        policy = ACCESS.load_policy()
        rendered = ACCESS.render_profile_from_paths(
            policy, "hybrid-openai-root", catalog_paths()
        )
        agent = rendered["airlock-or-declared-sonnet"]
        self.assertEqual(agent["model"], "anthropic/claude-sonnet-4.5")
        self.assertEqual(agent["disallowedTools"], ["Agent"])
        self.assertNotIn("effort", agent)
        self.assertIn("deepinfra/turbo", agent["description"])
        self.assertIn("does not verify", agent["description"])
        self.assertIn("explicit extra-usage authorization", agent["description"])
        self.assertNotIn("Community-derived guidance", agent["description"])
        self.assertNotIn("frontier", agent["description"])

        policy["policies"]["extra_usage"] = "allow"
        allowed = ACCESS.render_profile_from_paths(
            policy,
            "hybrid-openai-root",
            catalog_paths(),
        )["airlock-or-declared-sonnet"]
        self.assertIn(
            "allows extra usage without per-call confirmation",
            allowed["description"],
        )
        self.assertNotIn(
            "explicit extra-usage authorization",
            allowed["description"],
        )

        names = ACCESS.managed_agent_names(rendered, policy)
        self.assertIn("airlock-or-declared-sonnet", names)
        settings = ACCESS.managed_session_settings_json(
            json.dumps(rendered), policy=policy
        )
        self.assertIn("pinned OpenRouter endpoints", settings)

    def test_matching_preset_adds_fixed_guidance_only_to_description(self) -> None:
        preset = ACCESS.OPENROUTER_PRESETS.preset_by_name("kimi-k3")
        assert preset is not None
        self.write_registry(preset_registry_entry())
        policy = ACCESS.load_policy()
        rendered = ACCESS.render_profile_from_paths(
            policy, "hybrid-openai-root", catalog_paths()
        )
        agent = rendered["airlock-or-kimi-k3"]
        self.assertEqual(agent["model"], preset.model)
        self.assertEqual(agent["disallowedTools"], ["Agent"])
        self.assertNotIn("effort", agent)
        self.assertIn(preset.endpoint_provider, agent["description"])
        self.assertIn("does not verify", agent["description"])
        self.assertIn("Community-derived guidance (unverified", agent["description"])
        self.assertIn(preset.evidence_date, agent["description"])
        self.assertIn(preset.suggested_use, agent["description"])
        self.assertIn(preset.tradeoffs, agent["description"])
        self.assertNotIn(preset.canonical_slug, agent["description"])
        for forbidden in (
            preset.evidence_date,
            preset.suggested_use,
            preset.tradeoffs,
            preset.canonical_slug,
        ):
            self.assertNotIn(forbidden, agent["prompt"])
        self.assertNotIn("cheapest", agent["description"].lower())

        serialized_route_policy = json.dumps(
            ACCESS.session_route_policy(policy, "hybrid-openai-root"),
            sort_keys=True,
        )
        for forbidden in (
            preset.evidence_date,
            preset.suggested_use,
            preset.tradeoffs,
            preset.canonical_slug,
        ):
            self.assertNotIn(forbidden, serialized_route_policy)

    def test_preset_guidance_requires_all_frozen_routing_metadata(self) -> None:
        preset = ACCESS.OPENROUTER_PRESETS.preset_by_name("kimi-k3")
        assert preset is not None
        cases = (
            {"endpoint_provider": "provider/different"},
            {"provider_name": "Different Provider"},
            {"provider_slug": "different-provider"},
            {"quantization": "fp8"},
            {"canonical_slug": "moonshotai/kimi-k3-20260810"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.write_registry(preset_registry_entry(**changes))
                rendered = ACCESS.render_profile_from_paths(
                    ACCESS.load_policy(),
                    "hybrid-openai-root",
                    catalog_paths(),
                )
                description = rendered["airlock-or-kimi-k3"]["description"]
                self.assertIn("does not verify", description)
                self.assertNotIn("Community-derived guidance", description)
                self.assertNotIn(preset.suggested_use, description)
                self.assertNotIn(preset.tradeoffs, description)

    def test_pure_profiles_and_disabled_or_never_entries_do_not_expose_workers(self) -> None:
        self.write_registry(registry_entry())
        policy = ACCESS.load_policy()
        for profile in ("openai-pure", "grok-pure"):
            self.assertFalse(any(
                item["provider"] == "openrouter"
                for item in ACCESS.enabled_profile_workers(policy, profile)
            ))

        self.write_registry(registry_entry(enabled=False))
        policy = ACCESS.load_policy()
        self.assertFalse(any(
            item["provider"] == "openrouter"
            for item in ACCESS.enabled_profile_workers(policy, "hybrid-openai-root")
        ))

        self.write_registry(registry_entry())
        policy = ACCESS.load_policy()
        policy["policies"]["extra_usage"] = "never"
        self.assertFalse(any(
            item["provider"] == "openrouter"
            for item in ACCESS.enabled_profile_workers(policy, "hybrid-openai-root")
        ))

    def test_session_snapshot_freezes_routes_agents_and_endpoint(self) -> None:
        self.write_registry(registry_entry(endpoint_provider="deepinfra/turbo"))
        policy = ACCESS.load_policy()
        path, digest = ACCESS.write_session_snapshot(
            policy,
            "hybrid-anthropic-root",
            "claude-sonnet-5[1m]",
            self.root / "runtime",
        )
        snapshot = ACCESS.POLICY_SCHEMA.load_session_snapshot(path, digest)
        self.assertEqual(snapshot.root_model, "claude-sonnet-5[1m]")
        self.assertEqual(snapshot.root_provider, "anthropic")
        self.assertEqual(
            snapshot.routes["anthropic/claude-sonnet-4.5"], "openrouter"
        )
        self.assertEqual(
            snapshot.agents["airlock-or-declared-sonnet"].model,
            "anthropic/claude-sonnet-4.5",
        )
        self.assertTrue(
            snapshot.agents["airlock-or-declared-sonnet"].extra_usage
        )
        metadata = snapshot.openrouter["anthropic/claude-sonnet-4.5"]
        self.assertEqual(metadata.endpoint_provider, "deepinfra/turbo")
        self.assertEqual(metadata.provider_name, "Anthropic")
        self.assertEqual(metadata.provider_slug, "anthropic")
        self.assertEqual(metadata.quantization, "bf16")
        self.assertEqual(
            metadata.canonical_slug,
            "anthropic/claude-sonnet-4.5-20250929",
        )
        raw_snapshot = path.read_text(encoding="ascii")
        self.assertNotIn("sk-or", raw_snapshot)
        self.assertIn("canonical_slug", raw_snapshot)
        self.assertIn("20250929", raw_snapshot)
        with self.assertRaisesRegex(ACCESS.AccessError, "root model"):
            ACCESS.build_session_snapshot(
                policy, "hybrid-anthropic-root", "gpt-5.6-sol"
            )

    def test_preset_snapshot_includes_routing_metadata_but_excludes_guidance(self) -> None:
        preset = ACCESS.OPENROUTER_PRESETS.preset_by_name("kimi-k3")
        assert preset is not None
        self.write_registry(preset_registry_entry())
        policy = ACCESS.load_policy()
        path, digest = ACCESS.write_session_snapshot(
            policy,
            "hybrid-anthropic-root",
            "claude-sonnet-5[1m]",
            self.root / "preset-runtime",
        )
        snapshot = ACCESS.POLICY_SCHEMA.load_session_snapshot(path, digest)
        self.assertEqual(
            snapshot.routes[preset.model],
            "openrouter",
        )
        self.assertEqual(
            snapshot.agents["airlock-or-kimi-k3"].model,
            preset.model,
        )
        metadata = snapshot.openrouter[preset.model]
        self.assertEqual(metadata.endpoint_provider, preset.endpoint_provider)
        self.assertEqual(metadata.provider_name, preset.provider_name)
        self.assertEqual(metadata.provider_slug, preset.provider_slug)
        self.assertEqual(metadata.quantization, preset.quantization)
        self.assertEqual(metadata.canonical_slug, preset.canonical_slug)
        raw_snapshot = path.read_text(encoding="ascii")
        self.assertIn(preset.canonical_slug, raw_snapshot)
        for forbidden in (
            preset.evidence_date,
            preset.suggested_use,
            preset.tradeoffs,
            "Community-derived guidance",
        ):
            self.assertNotIn(forbidden, raw_snapshot)

    def test_snapshot_includes_a_valid_root_that_is_not_a_worker(self) -> None:
        snapshot = ACCESS.build_session_snapshot(
            ACCESS.load_policy(), "openai-pure", "gpt-5.3-codex-spark"
        )
        self.assertEqual(snapshot.root_model, "gpt-5.3-codex-spark")
        self.assertEqual(snapshot.routes["gpt-5.3-codex-spark"], "openai")
        self.assertFalse(any(
            agent.model == "gpt-5.3-codex-spark"
            for agent in snapshot.agents.values()
        ))

    def test_session_artifact_writer_preserves_bytes_and_cleans_securely(self) -> None:
        runtime = self.root / "artifact-runtime"
        content = "synthetic guidance π".encode("utf-8")
        path, digest, size = ACCESS.write_session_artifact(
            content, "guidance-", ".txt", runtime
        )
        self.assertEqual(path.parent, runtime)
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(size, len(content))
        self.assertEqual(len(digest), 64)
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
        ACCESS.delete_session_artifact(path, digest, size, runtime)
        self.assertFalse(path.exists())

    def test_session_artifact_cleanup_rejects_wrong_identity_size_and_digest(self) -> None:
        runtime = self.root / "artifact-validation-runtime"
        content = b"synthetic settings"
        path, digest, size = ACCESS.write_session_artifact(
            content, "settings-", ".json", runtime
        )
        with self.assertRaisesRegex(ACCESS.AccessError, "digest"):
            ACCESS.delete_session_artifact(path, "invalid", size, runtime)
        with self.assertRaisesRegex(ACCESS.AccessError, "changed"):
            ACCESS.delete_session_artifact(path, digest, size + 1, runtime)
        path.write_bytes(b"changed settings!!")
        self.assertEqual(path.stat().st_size, size)
        with self.assertRaisesRegex(ACCESS.AccessError, "changed"):
            ACCESS.delete_session_artifact(path, digest, size, runtime)
        self.assertTrue(path.exists())

    def test_session_artifact_writer_rejects_unsafe_inputs_and_removes_partials(self) -> None:
        unsafe_runtime = self.root / "artifact-runtime-file"
        unsafe_runtime.write_text("not a directory", encoding="utf-8")
        with self.assertRaisesRegex(ACCESS.AccessError, "safe directory"):
            ACCESS.write_session_artifact(
                b"content", "settings-", ".json", unsafe_runtime
            )
        with self.assertRaisesRegex(ACCESS.AccessError, "prefix"):
            ACCESS.write_session_artifact(b"content", "../unsafe-", ".json")
        with self.assertRaisesRegex(ACCESS.AccessError, "suffix"):
            ACCESS.write_session_artifact(b"content", "settings-", ".ini")
        with self.assertRaisesRegex(ACCESS.AccessError, "too large"):
            ACCESS.write_session_artifact(
                b"x" * (ACCESS.MAX_SESSION_ARTIFACT_BYTES + 1),
                "settings-",
                ".json",
            )

        runtime = self.root / "failed-artifact-runtime"
        with patch.object(ACCESS.os, "chmod", side_effect=OSError("blocked")):
            with self.assertRaisesRegex(
                ACCESS.AccessError, "could not be written securely"
            ):
                ACCESS.write_session_artifact(
                    b"content", "settings-", ".json", runtime
                )
        self.assertEqual(list(runtime.glob("settings-*.json")), [])

        interrupted_runtime = self.root / "interrupted-artifact-runtime"
        with patch.object(ACCESS.os, "fsync", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                ACCESS.write_session_artifact(
                    b"content", "guidance-", ".txt", interrupted_runtime
                )
        self.assertEqual(list(interrupted_runtime.glob("guidance-*.txt")), [])

    def test_snapshot_writer_removes_partial_file_on_failure(self) -> None:
        policy = ACCESS.load_policy()
        runtime = self.root / "failed-runtime"
        with patch.object(ACCESS.os, "chmod", side_effect=OSError("blocked")):
            with self.assertRaisesRegex(
                ACCESS.AccessError, "could not be written securely"
            ):
                ACCESS.write_session_snapshot(
                    policy,
                    "hybrid-anthropic-root",
                    "claude-sonnet-5[1m]",
                    runtime,
                )
        self.assertEqual(list(runtime.glob("session-*.json")), [])

    def test_snapshot_cleanup_validates_runtime_path_and_digest(self) -> None:
        policy = ACCESS.load_policy()
        runtime = self.root / "runtime"
        with patch.dict(
            os.environ, {"AIRLOCK_SESSION_RUNTIME_DIR": str(runtime)}, clear=False
        ):
            path, digest = ACCESS.write_session_snapshot(
                policy,
                "hybrid-anthropic-root",
                "claude-sonnet-5[1m]",
                runtime,
            )
            with self.assertRaises(ACCESS.AccessError):
                ACCESS.delete_session_snapshot(path, "0" * 64)
            self.assertTrue(path.exists())
            ACCESS.delete_session_snapshot(path, digest)
            self.assertFalse(path.exists())

            outside, outside_digest = ACCESS.write_session_snapshot(
                policy,
                "hybrid-anthropic-root",
                "claude-sonnet-5[1m]",
                self.root / "outside",
            )
            with self.assertRaisesRegex(ACCESS.AccessError, "outside"):
                ACCESS.delete_session_snapshot(outside, outside_digest)
            self.assertTrue(outside.exists())

    def test_unregistered_dynamic_agent_and_invalid_registry_fail_closed(self) -> None:
        self.write_registry(
            registry_entry(),
            registry_entry(
                route="other-route",
                model="vendor/other-model",
                canonical_slug="vendor/other-model-20260810",
            ),
        )
        policy = ACCESS.load_policy()
        with self.assertRaises(ACCESS.AccessError):
            ACCESS.managed_agent_names({
                "airlock-or-not-declared": {"model": "vendor/model"}
            }, policy)

        policy["policies"]["extra_usage"] = "never"
        self.assertEqual(
            ACCESS.managed_agent_names({
                "airlock-or-other-route": {"model": "vendor/other-model"}
            }, policy),
            ["airlock-or-other-route"],
        )
        rendered = ACCESS.render_profile(
            policy,
            "openrouter-pure",
            {},
            openrouter_root_route="declared-sonnet",
        )
        self.assertEqual(set(rendered), {"airlock-or-declared-sonnet"})

        self.registry.write_text('{"schema_version":1,"models":[],"extra":true}', encoding="utf-8")
        with self.assertRaisesRegex(ACCESS.AccessError, "OpenRouter registry is invalid"):
            ACCESS.load_policy()

    def test_guidance_marks_declared_route_as_unknown_and_extra(self) -> None:
        self.write_registry(registry_entry())
        guidance = ACCESS.profile_guidance(
            ACCESS.load_policy(), "hybrid-anthropic-root"
        )
        self.assertIn("openrouter", guidance.lower())
        self.assertIn("registry=pinned/key=separate/usage=extra", guidance)
        self.assertIn("capability, context window, or relative cost", guidance)
        self.assertIn("Extra usage authorized: yes", guidance)


if __name__ == "__main__":
    unittest.main()
