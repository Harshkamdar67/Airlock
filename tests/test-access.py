#!/usr/bin/env python3
"""Non-model tests for sanitized usage state and effort recommendations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("airlock_access_test", ROOT / "bin" / "airlock-access.py")
assert SPEC and SPEC.loader
ACCESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCESS)
REMOVED_TRANSPORT_FILES = (
    "bin/airlock-delegate",
    "bin/airlock-delegate.cmd",
    "bin/airlock-delegate.py",
    "bin/airlock-workflow",
    "bin/airlock-workflow.cmd",
    "bin/airlock-workflow.py",
    "bin/airlock-child",
    "bin/airlock-child.cmd",
    "bin/airlock-child.py",
    "bin/airlock-check",
    "bin/airlock-check.cmd",
    "bin/airlock-check.py",
    "bin/airlock_runtime.py",
)


class AccessUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "config"
        self.access = self.root / "access.json"
        self.environment = patch.dict(os.environ, {
            "AIRLOCK_CONFIG_FILE": str(self.config),
            "AIRLOCK_ACCESS_FILE": str(self.access),
            "AIRLOCK_CLAUDE_STATE_FILE": str(self.root / "claude.json"),
            "AIRLOCK_PROXY_FAST_CAPABLE": "0",
            # Pin the Grok probe so no test shells out to a real proxy that may
            # or may not be installed on the machine running the suite.
            "AIRLOCK_ACCESS_GROK_AUTH": "1",
        }, clear=False)
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp.cleanup()

    def bundle_components(self, platform: str = "posix") -> list[str]:
        bundle = json.loads((ROOT / "config" / "managed-bundle.json").read_text(encoding="utf-8"))
        names = bundle["platforms"]["common"] + bundle["platforms"][platform]
        return [f"{name}={ROOT / name}" for name in names]

    def test_managed_bundle_accepts_exact_repository_components(self) -> None:
        for platform in ("posix", "windows"):
            with self.subTest(platform=platform):
                expected = self.bundle_components(platform)
                result = ACCESS.validate_managed_bundle(
                    ROOT / "config" / "managed-bundle.json",
                    platform,
                    expected,
                )
                self.assertEqual(result["protocol_version"], 3)
                self.assertEqual(result["components_checked"], len(expected))
                self.assertGreater(result["components_checked"], 15)

    def test_managed_bundle_rejects_stale_marker_and_changed_component(self) -> None:
        original = json.loads((ROOT / "config" / "managed-bundle.json").read_text(encoding="utf-8"))
        stale = self.root / "stale.json"
        original["bundle_version"] = "old"
        stale.write_text(json.dumps(original), encoding="utf-8")
        with self.assertRaisesRegex(ACCESS.AccessError, "bundle is stale"):
            ACCESS.validate_managed_bundle(stale, "posix", self.bundle_components())

        changed = self.root / "airlock-hybrid.py"
        changed.write_text("changed\n", encoding="utf-8")
        components = self.bundle_components()
        key = "bin/airlock-hybrid.py="
        components = [
            key + str(changed) if item.startswith(key) else item
            for item in components
        ]
        with self.assertRaisesRegex(ACCESS.AccessError, "stale or changed"):
            ACCESS.validate_managed_bundle(
                ROOT / "config" / "managed-bundle.json", "posix", components
            )

    def test_schema_one_migrates_with_usage_defaults(self) -> None:
        self.access.write_text(json.dumps({
            "schema_version": 1,
            "providers": {"openai": {"detected_plan": "prolite"}},
        }), encoding="utf-8")
        policy = ACCESS.load_cached_policy()
        self.assertEqual(policy["schema_version"], 2)
        self.assertEqual(policy["providers"]["openai"]["usage"]["source"], "not_checked")

    def test_bucket_validation_deduplicates_and_recomputes_remaining(self) -> None:
        usage = ACCESS._normalize_usage("openai", {
            "source": "codex_app_server",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "buckets": [
                {"limit_id": "codex", "window": "primary", "used_percent": 12.5,
                 "remaining_percent": 99, "window_duration_mins": 300, "resets_at": 2_000_000_000},
                {"limit_id": "codex", "window": "primary", "used_percent": 90,
                 "window_duration_mins": 300, "resets_at": 2_000_000_000},
                {"limit_id": "bad space", "window": "primary", "used_percent": 1,
                 "window_duration_mins": 300, "resets_at": 2_000_000_000},
                {"limit_id": "weekly", "window": "secondary", "used_percent": 101,
                 "window_duration_mins": 10080, "resets_at": 2_000_000_000},
            ],
            "credit_state": "available",
        })
        self.assertEqual(len(usage["buckets"]), 1)
        self.assertEqual(usage["buckets"][0]["remaining_percent"], 87.5)

    def test_codex_refresh_uses_only_documented_rate_limit_read(self) -> None:
        request_log = self.root / "requests.jsonl"
        server = self.root / "app-server"
        server.write_text(
            """import json, os, sys
log = os.environ['AIRLOCK_TEST_REQUEST_LOG']
for line in sys.stdin:
    value = json.loads(line)
    with open(log, 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(value) + '\\n')
    if value.get('method') == 'initialize':
        print(json.dumps({'id': value['id'], 'result': {}}), flush=True)
    elif value.get('method') == 'account/rateLimits/read':
        print(json.dumps({'id': value['id'], 'result': {
            'planType': 'prolite',
            'rateLimitsByLimitId': {'codex': {
                'primary': {'usedPercent': 12, 'windowDurationMins': 300, 'resetsAt': 2000000000},
                'secondary': {'usedPercent': 34, 'windowDurationMins': 10080, 'resetsAt': 2000000100}}},
            'credits': {'hasCredits': False, 'unlimited': False}}}), flush=True)
""",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {
            "AIRLOCK_ACCESS_CODEX": sys.executable,
            "AIRLOCK_TEST_REQUEST_LOG": str(request_log),
        }, clear=False):
            old_cwd = Path.cwd()
            os.chdir(self.root)
            try:
                policy, refreshed = ACCESS.refresh_usage()
            finally:
                os.chdir(old_cwd)
        self.assertTrue(refreshed)
        requests = [json.loads(line) for line in request_log.read_text(encoding="utf-8").splitlines()]
        methods = [item["method"] for item in requests]
        self.assertEqual(methods, ["initialize", "initialized", "account/rateLimits/read"])
        self.assertNotIn("refreshToken", json.dumps(requests))
        usage = policy["providers"]["openai"]["usage"]
        self.assertEqual(len(usage["buckets"]), 2)
        self.assertEqual(usage["buckets"][0]["remaining_percent"], 88.0)
        self.assertEqual(usage["credit_state"], "unavailable")
        self.assertEqual(policy["providers"]["openai"]["detected_plan"], "prolite")
        raw_cache = self.access.read_text(encoding="utf-8")
        self.assertNotIn("account/rateLimits/read", raw_cache)

    def test_failed_refresh_preserves_cached_snapshot(self) -> None:
        policy = ACCESS.default_policy()
        policy["providers"]["openai"]["usage"] = ACCESS._normalize_usage("openai", {
            "source": "codex_app_server", "checked_at": datetime.now(timezone.utc).isoformat(),
            "buckets": [{"limit_id": "codex", "window": "primary", "used_percent": 10,
                         "window_duration_mins": 300, "resets_at": 2_000_000_000}],
        })
        ACCESS._write_policy(policy, self.access)
        with patch.dict(os.environ, {"AIRLOCK_ACCESS_CODEX": str(self.root / "missing-codex")}, clear=False):
            loaded, refreshed = ACCESS.refresh_usage()
        self.assertFalse(refreshed)
        self.assertEqual(loaded["providers"]["openai"]["usage"]["buckets"][0]["remaining_percent"], 90.0)

    def test_usage_refresh_due_uses_shared_fifteen_minute_window(self) -> None:
        now = datetime.now(timezone.utc)
        policy = ACCESS.default_policy()
        self.assertTrue(ACCESS.usage_refresh_due(policy, now=now))
        policy["providers"]["openai"]["usage"] = ACCESS._normalize_usage("openai", {
            "source": "codex_app_server",
            "checked_at": (now - timedelta(minutes=5)).isoformat(),
            "buckets": [],
        })
        self.assertFalse(ACCESS.usage_refresh_due(policy, now=now))
        policy["providers"]["openai"]["usage"]["checked_at"] = (
            now - timedelta(minutes=16)
        ).isoformat()
        self.assertTrue(ACCESS.usage_refresh_due(policy, now=now))

    def test_usage_display_auto_refreshes_stale_cache_and_preserves_fallback(self) -> None:
        policy = ACCESS.default_policy()
        policy["providers"]["openai"]["usage"] = ACCESS._normalize_usage("openai", {
            "source": "codex_app_server",
            "checked_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "buckets": [{"limit_id": "codex", "window": "primary", "used_percent": 10,
                         "window_duration_mins": 300, "resets_at": 2_000_000_000}],
        })
        with patch.object(ACCESS, "load_policy", return_value=policy), patch.object(
            ACCESS, "refresh_usage", return_value=(policy, False)
        ) as refresh:
            displayed, attempted, refreshed = ACCESS.usage_policy_for_display("show")
        refresh.assert_called_once()
        self.assertTrue(attempted)
        self.assertFalse(refreshed)
        self.assertEqual(
            displayed["providers"]["openai"]["usage"]["buckets"][0]["remaining_percent"],
            90.0,
        )

    def test_usage_display_keeps_fresh_cache_without_app_server(self) -> None:
        policy = ACCESS.default_policy()
        policy["providers"]["openai"]["usage"] = ACCESS._normalize_usage("openai", {
            "source": "codex_app_server",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "buckets": [],
        })
        with patch.object(ACCESS, "load_policy", return_value=policy), patch.object(
            ACCESS, "refresh_usage"
        ) as refresh:
            displayed, attempted, refreshed = ACCESS.usage_policy_for_display(None)
        refresh.assert_not_called()
        self.assertIs(displayed, policy)
        self.assertFalse(attempted)
        self.assertFalse(refreshed)

    def test_cached_display_starts_no_app_server(self) -> None:
        marker = self.root / "should-not-exist"
        with patch.dict(os.environ, {"AIRLOCK_ACCESS_CODEX": str(marker)}, clear=False):
            lines = ACCESS.usage_lines(ACCESS.load_policy())
        self.assertFalse(marker.exists())
        self.assertIn("Remaining quota windows: unknown", "\n".join(lines))

    def test_capacity_mapping_and_user_override(self) -> None:
        policy = ACCESS.default_policy()
        policy["providers"]["openai"]["detected_plan"] = "prolite"
        signal = ACCESS.capacity_signal(policy, "openai")
        self.assertEqual((signal["multiplier"], signal["source"]), (5, "inferred"))
        self.config.write_text("AIRLOCK_OPENAI_CAPACITY=20x\n", encoding="utf-8")
        signal = ACCESS.capacity_signal(policy, "openai")
        self.assertEqual((signal["multiplier"], signal["source"]), (20, "user_override"))
        self.config.write_text("", encoding="utf-8")
        policy["providers"]["anthropic"]["detected_plan"] = "max20x"
        policy["providers"]["anthropic"]["plan_source"] = "status_field"
        claude = ACCESS.capacity_signal(policy, "anthropic")
        self.assertEqual((claude["multiplier"], claude["source"]), (20, "status_field"))

    def test_effort_matrix_and_explicit_precedence(self) -> None:
        now = datetime.now(timezone.utc)
        policy = ACCESS.default_policy()
        policy["policies"]["routing"] = "quality"
        policy["providers"]["openai"]["usage"] = ACCESS._normalize_usage("openai", {
            "source": "codex_app_server", "checked_at": now.isoformat(),
            "buckets": [{"limit_id": "codex", "window": "primary", "used_percent": 94,
                         "window_duration_mins": 300, "resets_at": 2_000_000_000}],
        })
        recommended = ACCESS.recommend_effort(policy, "openai", "standard")
        self.assertEqual(recommended["effort"], "low")
        critical = ACCESS.recommend_effort(policy, "openai", "critical")
        self.assertEqual(critical["effort"], "high")
        explicit = ACCESS.recommend_effort(policy, "openai", "low", "max")
        self.assertEqual((explicit["effort"], explicit["effort_source"]), ("max", "explicit"))
        policy["providers"]["openai"]["usage"]["checked_at"] = (
            now - timedelta(hours=2)
        ).isoformat()
        stale = ACCESS.recommend_effort(policy, "openai", "standard")
        self.assertEqual(stale["effort"], "high")
        self.assertIn("no fresh", stale["rationale"])

    def test_capacity_changes_escalation_tolerance_and_allowed_efforts_win(self) -> None:
        policy = ACCESS.default_policy()
        policy["policies"]["routing"] = "quality"
        policy["providers"]["openai"]["usage"] = ACCESS._normalize_usage("openai", {
            "source": "codex_app_server", "checked_at": datetime.now(timezone.utc).isoformat(),
            "buckets": [{"limit_id": "codex", "window": "primary", "used_percent": 65,
                         "window_duration_mins": 300, "resets_at": 2_000_000_000}],
        })
        strict = ACCESS.recommend_effort(policy, "openai", "critical")
        self.assertEqual(strict["effort"], "high")
        self.config.write_text("AIRLOCK_OPENAI_CAPACITY=20x\n", encoding="utf-8")
        roomy = ACCESS.recommend_effort(policy, "openai", "critical")
        self.assertEqual(roomy["effort"], "max")
        policy["policies"]["allowed_efforts"]["openai"] = ["low", "medium"]
        constrained = ACCESS.recommend_effort(policy, "openai", "critical")
        self.assertEqual(constrained["effort"], "medium")

    def test_atomic_config_updates_and_defaults_clear_overrides(self) -> None:
        self.config.write_bytes(b"# keep\r\nOTHER=value\r\nAIRLOCK_OPENAI_CAPACITY=5x\r\n")
        ACCESS.write_flat_config_overrides({
            "AIRLOCK_MAX_CONCURRENT_SUBAGENTS": "off",
            "AIRLOCK_OPENAI_CAPACITY": None,
        })
        raw = self.config.read_bytes()
        self.assertIn(b"# keep\r\n", raw)
        self.assertIn(b"OTHER=value\r\n", raw)
        self.assertIn(b"AIRLOCK_MAX_CONCURRENT_SUBAGENTS=off\r\n", raw)
        self.assertNotIn(b"AIRLOCK_OPENAI_CAPACITY", raw)
    def test_adaptive_worker_policy_overrides_round_trip(self) -> None:
        ACCESS.write_flat_config_overrides({
            "AIRLOCK_FAILOVER_POLICY": "never",
            "AIRLOCK_DESCENDANT_POLICY": "off",
            "AIRLOCK_MAX_DESCENDANTS_PER_WORKER": "4",
            "AIRLOCK_MAX_CONCURRENT_DESCENDANTS": "5",
            "AIRLOCK_MAX_REPAIR_ROUNDS": "1",
        })
        policy = ACCESS.load_policy()
        self.assertEqual(policy["policies"]["failover"], "never")
        self.assertEqual(policy["policies"]["descendants"], "off")
        self.assertEqual(policy["policies"]["max_descendants"], 4)
        self.assertEqual(policy["policies"]["max_concurrent_descendants"], 5)
        self.assertEqual(policy["policies"]["repair_rounds"], 1)
        lines = "\n".join(ACCESS.mode_status_lines())
        self.assertIn("Failover: never", lines)
        self.assertIn("Agent nesting: off for named Agents; root spawn depth=1", lines)
        self.assertNotIn("Descendants:", lines)
        self.assertNotIn("Repair rounds:", lines)
        usage_lines = "\n".join(ACCESS.status_lines(policy))
        self.assertIn("Models (access/capability/usage):", usage_lines)
        self.assertNotIn("Delegated efforts:", usage_lines)

    def test_portfolio_guidance_uses_roles_headroom_and_ceiling(self) -> None:
        policy = ACCESS.default_policy()
        policy["policies"]["routing"] = "balanced"
        guidance = ACCESS.portfolio_guidance(policy, "hybrid-openai-root")
        self.assertIn("recommended initial breadth=2", guidance)
        self.assertIn("configured ceiling=Claude Code native default", guidance)
        self.assertIn("use airlock-sonnet for deep repository research", guidance)
        self.assertIn("prefer airlock-opus for difficult architecture, UI/UX design", guidance)
        self.assertIn("prefer airlock-sol for difficult implementation", guidance)
        self.assertIn("use airlock-luna for high-volume discovery", guidance)
        self.assertIn("Automatic armies start the full background native Agent batch of airlock-luna workers", guidance)
        self.assertIn("One stronger airlock-sol or airlock-opus Agent, or the root, reviews", guidance)
        self.assertIn("use airlock-terra for adversarial review", guidance)
        self.assertIn("soft routing preferences, not provider stereotypes", guidance)
        self.assertIn("explicit user choice wins", guidance)
        self.assertIn("Compare rendered results and accessibility for frontend work", guidance)
        self.assertIn("require a reproducer and causal explanation for backend bugs", guidance)
        self.assertIn("before-and-after measurements for performance work", guidance)
        self.assertIn("independent tools plus manual verification for security work", guidance)
        self.assertIn("No model is a source of record", guidance)
        self.assertIn("ceiling is never a target", guidance)

        self.config.write_text("AIRLOCK_MAX_CONCURRENT_SUBAGENTS=1\n", encoding="utf-8")
        constrained = ACCESS.portfolio_guidance(policy, "hybrid-openai-root")
        self.assertIn("recommended initial breadth=1", constrained)
        self.assertIn("configured ceiling=1 concurrent", constrained)

    def test_profile_guidance_has_orchestration_progress_and_full_results(self) -> None:
        policy = ACCESS.default_policy()
        for profile in ("openai-pure", "hybrid-openai-root", "hybrid-anthropic-root"):
            with self.subTest(profile=profile):
                guidance = ACCESS.profile_guidance(policy, profile)
                self.assertIn("Orchestration: choose the smallest effective path", guidance)
                self.assertIn("Built-in Explore, Plan, and general-purpose accept Claude Code's", guidance)
                self.assertIn("pass `model=haiku` (resolved to gpt-5.6-luna)", guidance)
                self.assertIn("Omit `model` only when inheriting the orchestrator", guidance)
                self.assertIn("Use built-in Plan for read-only technical design", guidance)
                self.assertIn("Use built-in general-purpose for multi-step work", guidance)
                self.assertIn("For an automatic army, launch multiple exact airlock-luna", guidance)
                self.assertIn("Agent calls with `run_in_background: true`", guidance)
                self.assertIn("useful non-overlapping batch before waiting", guidance)
                self.assertIn("They run at the session effort unless they are pinned", guidance)
                self.assertIn("Use Claude Code Workflow only when the user explicitly requests", guidance)
                self.assertIn("Do not overlap direct Agent fan-out with Workflow", guidance)
                self.assertIn("Automatic high-volume swarms are limited to airlock-luna", guidance)
                self.assertIn("Claude Code's Agent card, model identity, usage", guidance)
                self.assertIn("Difficult implementation shards must have explicit file ownership", guidance)
                self.assertIn("Named airlock-* Agents already bind their exact model", guidance)
                self.assertIn("Native Agent results: use background execution only when work is independent", guidance)
                self.assertIn("Preserve the full technical result", guidance)
                self.assertIn("does not prove the task is semantically complete", guidance)
                self.assertIn("state the immediate goal and approach", guidance)
                self.assertIn("meaningful phase changes", guidance)
                self.assertIn("Do not narrate every read", guidance)
                self.assertIn("skipped checks and reasons", guidance)
                self.assertIn("exact-output or machine-readable contract", guidance)
                # This guard is about context cost, not a hard limit. A real
                # session carries longer plan, headroom, and Fast eligibility
                # strings than the defaults used here, so leave room for them.
                self.assertLess(len(guidance), 14_000)
        pure = ACCESS.profile_guidance(policy, "openai-pure")
        self.assertIn("this OpenAI-only profile has no Anthropic worker", pure)
        # Handoff wording is per provider, so a profile only carries the rules
        # for the workers it can actually reach.
        self.assertIn("returns only its final report", pure)
        self.assertIn("GPT workers read the action word literally", pure)
        self.assertNotIn("Claude workers plan first", pure)
        hybrid = ACCESS.profile_guidance(policy, "hybrid-openai-root")
        self.assertIn("GPT workers read the action word literally", hybrid)
        self.assertIn("Claude workers plan first", hybrid)
        self.assertIn("substantial visual and interaction design is Anthropic-first and Opus-led", hybrid)
        self.assertIn("keyboard navigation, selection mechanics", hybrid)
        self.assertIn("Start airlock-opus", hybrid)
        self.assertIn("material new interaction pattern such as keyboard selection", hybrid)
        self.assertIn("routing requirement when Opus is enabled and eligible", hybrid)
        self.assertIn("generic work-directly rule does not override it", hybrid)
        self.assertIn("Use airlock-sonnet", hybrid)
        self.assertIn("Start with Opus for new design judgment", hybrid)
        self.assertIn("split the roles automatically", hybrid)
        self.assertIn("Do not wait for the user to request this split", hybrid)
        self.assertIn("Before working directly on a multi-part request", hybrid)
        self.assertIn("A coupled final result does not make every phase coupled", hybrid)
        self.assertIn("Before assigning WebSearch, check effort compatibility", hybrid)
        self.assertIn("high or below", hybrid)
        self.assertIn("Extra usage authorized: yes", hybrid)

    def test_ui_ux_guidance_respects_access_and_confirmation(self) -> None:
        policy = ACCESS.default_policy()
        policy["providers"]["anthropic"]["models"]["opus"]["access"] = "extra"
        workers = ACCESS.enabled_profile_workers(policy, "hybrid-openai-root")
        guidance = ACCESS.ui_ux_guidance(policy, "hybrid-openai-root", workers)
        self.assertIn("airlock-opus after explicit extra-usage confirmation", guidance)
        self.assertIn("airlock-sonnet", guidance)

        policy["providers"]["anthropic"]["models"]["opus"]["access"] = "unavailable"
        workers = ACCESS.enabled_profile_workers(policy, "hybrid-openai-root")
        fallback = ACCESS.ui_ux_guidance(policy, "hybrid-openai-root", workers)
        self.assertNotIn("airlock-opus", fallback)
        self.assertIn("airlock-sonnet", fallback)

    def test_profile_guidance_uses_free_form_delegation_without_task_markers(self) -> None:
        guidance = ACCESS.profile_guidance(ACCESS.default_policy(), "hybrid-openai-root")
        self.assertIn("one natural, self-contained query", guidance)
        self.assertIn("Do not add task kind, risk, or selection markers", guidance)
        self.assertNotIn("Delegated task kind:", guidance)
        self.assertNotIn("Delegated risk:", guidance)
        self.assertNotIn("result.report", guidance)
        self.assertFalse(hasattr(ACCESS, "DELEGATED_TASK_KINDS"))

    def test_legacy_transport_files_are_removed(self) -> None:
        for name in REMOVED_TRANSPORT_FILES:
            with self.subTest(name=name):
                self.assertFalse((ROOT / name).exists())

    def test_luna_fast_is_plan_and_proxy_gated_without_sol_fast_swarm_fallback(self) -> None:
        policy = ACCESS.default_policy()
        policy["policies"]["openai_fast"] = "on"
        policy["providers"]["openai"]["detected_plan"] = "free"
        with patch.object(ACCESS, "proxy_fast_capability", return_value={
            "supported": True, "source": "test", "version": "0.1.22",
        }):
            status = ACCESS.fast_route_status(policy)
            self.assertEqual(status["selected_route"], "luna")
            policy["providers"]["openai"]["detected_plan"] = "prolite"
            status = ACCESS.fast_route_status(policy)
            self.assertEqual(status["selected_route"], "luna-fast")
            self.assertNotIn("sol-fast", str(status))
            policy["policies"]["swarm_fast"] = "off"
            self.assertEqual(ACCESS.fast_route_status(policy)["selected_route"], "luna")
            policy["policies"]["swarm_fast"] = "on"
            policy["providers"]["openai"]["detected_plan"] = "unknown"
            self.assertIsNone(ACCESS.fast_route_status(policy)["selected_route"])

    def test_family_slots_stay_in_session_and_exclude_unconfirmed_extra_usage(self) -> None:
        policy = ACCESS.default_policy()
        for model in policy["providers"]["grok"]["models"].values():
            model["access"] = "unknown"
        self.assertEqual(
            ACCESS.discovery_model(policy, "grok-pure"),
            "grok-composer-2.5-fast",
        )
        self.assertEqual(ACCESS.proxy_picker_models(policy, "grok-pure"), {
            "fable": "grok-4.5",
            "opus": "grok-4.5",
            "sonnet": "grok-composer-2.5-fast",
            "haiku": "grok-composer-2.5-fast",
        })
        hybrid = ACCESS.session_route_policy(policy, "hybrid-grok-root")
        self.assertEqual(set(hybrid["picker_models"]), {"fable", "opus", "sonnet", "haiku"})
        self.assertEqual(hybrid["picker_models"]["haiku"], hybrid["discovery_model"])
        self.assertTrue(set(hybrid["picker_models"].values()) <= set(hybrid["model_ids"]))

        # An Agent family alias cannot carry Airlock's explicit extra-usage
        # marker. If Terra requires confirmation, its Sonnet slot must fall
        # back to an ordinary enabled model instead of bypassing the policy.
        policy["providers"]["openai"]["models"]["terra"]["access"] = "extra"
        policy["policies"]["extra_usage"] = "ask"
        picker = ACCESS.proxy_picker_models(policy, "openai-pure")
        self.assertEqual(picker["sonnet"], "gpt-5.6-sol")
        self.assertNotIn("gpt-5.6-terra", picker.values())
        policy["policies"]["extra_usage"] = "allow"
        self.assertEqual(
            ACCESS.proxy_picker_models(policy, "openai-pure")["sonnet"],
            "gpt-5.6-terra",
        )

        policy = ACCESS.default_policy()
        policy["providers"]["openai"]["models"]["luna"]["access"] = "extra"
        policy["policies"]["extra_usage"] = "ask"
        self.assertEqual(
            ACCESS.discovery_model(policy, "openai-pure"),
            "gpt-5.6-terra",
        )
        self.assertEqual(
            ACCESS.proxy_picker_models(policy, "openai-pure")["haiku"],
            "gpt-5.6-terra",
        )
        policy["policies"]["extra_usage"] = "allow"
        self.assertEqual(
            ACCESS.discovery_model(policy, "openai-pure"),
            "gpt-5.6-luna",
        )
        self.assertEqual(
            ACCESS.proxy_picker_models(policy, "openai-pure")["haiku"],
            "gpt-5.6-luna",
        )

    def test_openai_catalog_is_bare_and_claude_window_suffixes_remain(self) -> None:
        openai_models = [profile["model"] for profile in ACCESS.MODEL_PROFILES["openai"].values()]
        self.assertTrue(openai_models)
        self.assertTrue(all(model.startswith("gpt-") and not model.endswith("[1m]") for model in openai_models))
        for route in ("opus", "sonnet", "fable"):
            self.assertTrue(ACCESS.MODEL_PROFILES["anthropic"][route]["model"].endswith("[1m]"))

        # Claude Code only grants Opus 5, Sonnet 5, and Fable 5 their native 1M
        # window when ANTHROPIC_BASE_URL is unset or points at api.anthropic.com.
        # Airlock always points it at the session router, which silently drops
        # every one of them to 200000. The [1m] suffix is the only lever that
        # still reaches 1M from behind the router, so dropping it here would
        # cost four fifths of the window without any visible failure.
        anthropic = ACCESS.MODEL_PROFILES["anthropic"]
        for route in ("opus", "sonnet", "fable"):
            with self.subTest(route=route):
                self.assertTrue(anthropic[route]["model"].endswith("[1m]"))
        # Haiku 4.5 has no native 1M window, so claiming one would let the
        # session grow past what the model actually accepts.
        self.assertFalse(anthropic["haiku"]["model"].endswith("[1m]"))
        # Native Claude models use the suffix to request their larger context
        # window, while legacy GPT suffixes normalize defensively to bare IDs.
        self.assertEqual(ACCESS.wire_model_id("claude-opus-5[1m]"), "claude-opus-5")
        self.assertEqual(ACCESS.wire_model_id("gpt-5.6-terra"), "gpt-5.6-terra")
        self.assertEqual(ACCESS.wire_model_id("gpt-5.6-terra[1m]"), "gpt-5.6-terra")
        self.assertEqual(ACCESS.wire_model_id("grok-4.5"), "grok-4.5")

    def test_session_routes_follow_profile_access_fast_and_extra_policy(self) -> None:
        policy = ACCESS.default_policy()
        policy["policies"]["openai_fast"] = "on"
        with patch.object(ACCESS, "proxy_fast_capability", return_value={
            "supported": True, "source": "test", "version": "0.1.22",
        }):
            pure = ACCESS.session_route_policy(policy, "openai-pure")
            self.assertEqual(set(pure["routes"].values()), {"openai"})
            self.assertEqual(pure["routes"]["gpt-5.6-sol"], "openai")
            self.assertEqual(pure["routes"]["gpt-5.6-terra"], "openai")
            self.assertEqual(
                set(pure["model_ids"]),
                {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"},
            )
            self.assertIn("gpt-5.6-sol", pure["model_ids"])
            self.assertEqual(pure["discovery_model"], "gpt-5.6-luna")
            self.assertEqual(
                ACCESS.session_route_field(policy, "openai-pure", "discovery-model"),
                "gpt-5.6-luna",
            )
            self.assertEqual(pure["picker_models"], {
                "fable": "gpt-5.6-sol",
                "opus": "gpt-5.6-sol",
                "sonnet": "gpt-5.6-terra",
                "haiku": "gpt-5.6-luna",
            })
            self.assertEqual(
                ACCESS.session_route_field(policy, "openai-pure", "picker-models"),
                "fable=gpt-5.6-sol\nhaiku=gpt-5.6-luna\n"
                "opus=gpt-5.6-sol\nsonnet=gpt-5.6-terra",
            )
            hybrid = ACCESS.session_route_policy(policy, "hybrid-openai-root")
            self.assertEqual(hybrid["discovery_model"], "gpt-5.6-luna")
            self.assertEqual(hybrid["picker_models"], {
                "fable": "gpt-5.6-sol",
                "opus": "claude-opus-5[1m]",
                "sonnet": "claude-sonnet-5[1m]",
                "haiku": "gpt-5.6-luna",
            })
            self.assertTrue(
                set(hybrid["picker_models"].values()) <= set(hybrid["model_ids"])
            )
            self.assertEqual(hybrid["routes"]["claude-opus-5"], "anthropic")
            # The Anthropic ids carry a [1m] suffix so Claude Code keeps their
            # native 1M window from behind the router, and Claude Code strips
            # that suffix before the request leaves. Both forms have to route.
            self.assertEqual(hybrid["routes"]["claude-opus-5[1m]"], "anthropic")
            self.assertEqual(hybrid["routes"]["gpt-5.6-sol"], "openai")
            self.assertIn("gpt-5.6-sol", hybrid["model_ids"])
            self.assertNotIn("claude-fable-5[1m]", hybrid["model_ids"])
            self.assertNotIn("gpt-5.6-luna-fast", hybrid["model_ids"])

            policy["providers"]["anthropic"]["models"]["fable"]["access"] = "extra"
            hybrid = ACCESS.session_route_policy(policy, "hybrid-openai-root")
            self.assertIn("claude-fable-5[1m]", hybrid["extra_model_ids"])
            self.assertIn("airlock-fable", hybrid["extra_agent_names"])

            policy["providers"]["openai"]["detected_plan"] = "pro"
            hybrid = ACCESS.session_route_policy(policy, "hybrid-openai-root")
            self.assertIn("gpt-5.6-luna-fast", hybrid["model_ids"])
            policy["policies"]["swarm_fast"] = "off"
            hybrid = ACCESS.session_route_policy(policy, "hybrid-openai-root")
            self.assertNotIn("gpt-5.6-luna-fast", hybrid["model_ids"])

            policy["policies"]["extra_usage"] = "never"
            hybrid = ACCESS.session_route_policy(policy, "hybrid-openai-root")
            self.assertNotIn("claude-fable-5[1m]", hybrid["model_ids"])

    def test_portfolio_guidance_is_conservative_and_hides_disabled_workers(self) -> None:
        policy = ACCESS.default_policy()
        policy["policies"]["routing"] = "quality"
        policy["providers"]["anthropic"]["models"]["sonnet"]["access"] = "unavailable"
        policy["providers"]["openai"]["usage"] = ACCESS._normalize_usage("openai", {
            "source": "codex_app_server",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "buckets": [{
                "limit_id": "codex", "window": "primary", "used_percent": 92,
                "window_duration_mins": 300, "resets_at": 2_000_000_000,
            }],
        })
        headroom = ACCESS.provider_headroom(policy, "openai")
        self.assertEqual(headroom["state"], "critical")
        guidance = ACCESS.portfolio_guidance(policy, "hybrid-openai-root")
        self.assertIn("recommended initial breadth=2", guidance)
        self.assertIn("openai=critical (8.00% remaining", guidance)
        self.assertNotIn("airlock-sonnet", guidance)
        self.assertIn("no enabled native deep-research specialist", guidance)
        self.assertIn("Unknown or stale headroom is conservative", guidance)

    def test_managed_agent_names_are_exact_sorted_and_fail_closed(self) -> None:
        rendered = {
            "airlock-sonnet": {"model": "inherit"},
            "airlock-luna": {"model": "inherit"},
            "airlock-sol": {"model": "inherit"},
        }
        self.assertEqual(
            ACCESS.managed_agent_names(rendered),
            ["airlock-luna", "airlock-sol", "airlock-sonnet"],
        )
        self.assertEqual(
            ACCESS.managed_agent_names_json(json.dumps(rendered)),
            ["airlock-luna", "airlock-sol", "airlock-sonnet"],
        )
        for invalid in ({}, [], {"Explore": {}}, {"airlock-sol": "invalid"}):
            with self.subTest(invalid=invalid), self.assertRaises(ACCESS.AccessError):
                ACCESS.managed_agent_names(invalid)
        with self.assertRaises(ACCESS.AccessError):
            ACCESS.managed_agent_names_json("not-json")

    def test_native_catalogs_bind_exact_models_efforts_and_tools(self) -> None:
        expected = {
            profile["agent"]: (profile["model"], profile["effort"])
            for provider in ACCESS.MODEL_PROFILES.values()
            for profile in provider.values()
        }
        for catalog_name in (
            "openai-direct-agents.json",
            "anthropic-direct-agents.json",
            "hybrid-agents.json",
            "claude-agents.json",
            "grok-agents.json",
        ):
            catalog = json.loads((ROOT / "config" / catalog_name).read_text(encoding="utf-8"))
            # Underscore keys carry file metadata such as the managed marker
            # that lets the installer recognise its own file, not Agent
            # definitions. The loader drops them and so does this check.
            self.assertEqual(
                catalog.get("_comment"),
                "Managed by https://github.com/Harshkamdar67/Airlock",
                f"{catalog_name} must carry the managed marker or the installer "
                "will refuse to update it",
            )
            catalog = {
                key: value for key, value in catalog.items()
                if not key.startswith("_")
            }
            with self.subTest(catalog=catalog_name):
                self.assertTrue(catalog)
                for name, agent in catalog.items():
                    prompt = agent["prompt"]
                    self.assertEqual(
                        (agent["model"], agent["effort"]), expected[name]
                    )
                    self.assertNotIn("tools", agent)
                    self.assertNotIn("permissionMode", agent)
                    self.assertEqual(agent["disallowedTools"], ["Agent"])
                    self.assertIn("native Claude Code tools", prompt)
                    self.assertIn("preserve unrelated work", prompt)
                    self.assertIn("Never access or expose credentials", prompt)
                    self.assertIn("Do not invoke another Agent", prompt)
                    self.assertNotIn("airlock-delegate", prompt)
                    self.assertNotIn("airlock-workflow", prompt)
                    self.assertNotIn("transport wrapper", prompt)
                    self.assertNotIn("request file", prompt)
                    self.assertNotIn("--task-kind", prompt)
                    self.assertNotIn("result.report", prompt)
                if "airlock-luna" in catalog:
                    self.assertEqual(catalog["airlock-luna"]["effort"], "max")
                    self.assertIn(
                        "explicit file ownership",
                        catalog["airlock-luna"]["prompt"],
                    )
                if "airlock-opus" in catalog:
                    self.assertIn("UI/UX design and visual direction", catalog["airlock-opus"]["description"])
                    self.assertIn("design-system work", catalog["airlock-opus"]["description"])
                if "airlock-sonnet" in catalog:
                    self.assertIn("design-system-aligned UI implementation", catalog["airlock-sonnet"]["description"])

    def test_native_catalog_renderer_rejects_legacy_or_mismatched_entries(self) -> None:
        policy = ACCESS.default_policy()
        source = json.loads(
            (ROOT / "config" / "openai-direct-agents.json").read_text(encoding="utf-8")
        )
        mutations = (
            ("model", "inherit"),
            ("effort", "low"),
            ("tools", ["Write", "Bash"]),
            ("permissionMode", "acceptEdits"),
            ("prompt", "Use airlock-delegate for this request file."),
        )
        for key, value in mutations:
            catalog = json.loads(json.dumps(source))
            catalog["airlock-sol"][key] = value
            with self.subTest(key=key), self.assertRaises(ACCESS.AccessError):
                ACCESS.render_provider_agents(policy, "openai", catalog)

    def test_worker_effort_inherits_the_session_level_unless_it_is_pinned(self) -> None:
        source = json.loads(
            (ROOT / "config" / "openai-direct-agents.json").read_text(encoding="utf-8")
        )

        # With no configuration a worker carries no effort of its own, which is
        # what lets /effort move the root and every worker together.
        rendered = ACCESS.render_provider_agents(ACCESS.load_policy(), "openai", source)
        for name, agent in rendered.items():
            with self.subTest(agent=name):
                self.assertNotIn("effort", agent)
                self.assertIn("native effort: inherits the session level", agent["description"])

        self.config.write_text("AIRLOCK_WORKER_EFFORT=high\n", encoding="utf-8")
        shared = ACCESS.render_provider_agents(ACCESS.load_policy(), "openai", source)
        for name, agent in shared.items():
            with self.subTest(agent=name):
                self.assertEqual(agent["effort"], "high")
                self.assertIn("pinned native effort: high", agent["description"])

        # A route key pins one worker and wins over the shared setting.
        self.config.write_text(
            "AIRLOCK_WORKER_EFFORT=high\nAIRLOCK_EFFORT_LUNA=max\n", encoding="utf-8"
        )
        mixed = ACCESS.render_provider_agents(ACCESS.load_policy(), "openai", source)
        self.assertEqual(mixed["airlock-luna"]["effort"], "max")
        self.assertEqual(mixed["airlock-sol"]["effort"], "high")

        self.config.write_text(
            "AIRLOCK_WORKER_EFFORT=high\nAIRLOCK_EFFORT_LUNA=inherit\n", encoding="utf-8"
        )
        released = ACCESS.render_provider_agents(ACCESS.load_policy(), "openai", source)
        self.assertNotIn("effort", released["airlock-luna"])
        self.assertEqual(released["airlock-sol"]["effort"], "high")

        # An unusable value leaves the default alone rather than failing the session.
        self.config.write_text("AIRLOCK_WORKER_EFFORT=turbo\n", encoding="utf-8")
        ignored = ACCESS.render_provider_agents(ACCESS.load_policy(), "openai", source)
        self.assertNotIn("effort", ignored["airlock-luna"])

    def test_routing_guidance_reports_worker_effort_and_the_effort_knob(self) -> None:
        guidance = ACCESS.routing_guidance(ACCESS.load_policy(), "native")
        self.assertIn("luna=inherit", guidance)
        self.assertIn(
            "A worker set to inherit follows the session level, so /effort changes the root "
            "and every inheriting worker together, including mid-session",
            guidance,
        )
        self.assertIn("pinned to a named level keeps that level regardless of /effort", guidance)

        self.config.write_text("AIRLOCK_EFFORT_LUNA=max\n", encoding="utf-8")
        pinned = ACCESS.routing_guidance(ACCESS.load_policy(), "native")
        self.assertIn("luna=max", pinned)

    def test_full_hybrid_agent_profile_stays_within_windows_command_limit(self) -> None:
        policy = ACCESS.default_policy()
        policy["policies"]["extra_usage"] = "allow"
        policy["policies"]["openai_fast"] = "on"
        policy["providers"]["openai"]["detected_plan"] = "pro"
        for provider in ("openai", "anthropic"):
            for model in policy["providers"][provider]["models"].values():
                model["access"] = "unknown"
        catalogs = {
            "openai_direct": json.loads((ROOT / "config" / "openai-direct-agents.json").read_text(encoding="utf-8")),
            "openai_wrappers": json.loads((ROOT / "config" / "hybrid-agents.json").read_text(encoding="utf-8")),
            "anthropic_direct": json.loads((ROOT / "config" / "anthropic-direct-agents.json").read_text(encoding="utf-8")),
            "anthropic_wrappers": json.loads((ROOT / "config" / "claude-agents.json").read_text(encoding="utf-8")),
            "grok_direct": json.loads((ROOT / "config" / "grok-agents.json").read_text(encoding="utf-8")),
            "grok_wrappers": json.loads((ROOT / "config" / "grok-agents.json").read_text(encoding="utf-8")),
        }
        with patch.object(ACCESS, "proxy_fast_capability", return_value={
            "supported": True, "source": "test", "version": "0.1.22",
        }):
            for profile in ("hybrid-openai-root", "hybrid-anthropic-root", "hybrid-grok-root"):
                with self.subTest(profile=profile):
                    if profile == "hybrid-grok-root":
                        for model in policy["providers"]["grok"]["models"].values():
                            model["access"] = "unknown"
                    rendered = ACCESS.render_profile(policy, profile, catalogs)
                    serialized = json.dumps(rendered, separators=(",", ":"), ensure_ascii=True)
                    # Grok defaults unavailable unless enabled; hybrid-grok-root enables above.
                    expected = 10 if profile == "hybrid-grok-root" else 8
                    self.assertEqual(len(rendered), expected)
                    luna_description = rendered["airlock-luna"]["description"]
                    self.assertIn("transport: native", luna_description)
                    self.assertIn("native effort: inherits the session level", luna_description)
                    self.assertIn("use run_in_background=true", luna_description)
                    self.assertIn("useful non-overlapping batch before waiting", luna_description)
                    self.assertNotIn("automatic Luna army", rendered["airlock-sol"]["description"])
                    self.assertEqual(rendered["airlock-sol"]["model"], "gpt-5.6-sol")
                    self.assertEqual(rendered["airlock-opus"]["model"], "claude-opus-5[1m]")
                    for agent in rendered.values():
                        self.assertNotIn("tools", agent)
                        self.assertNotIn("permissionMode", agent)
                        self.assertNotIn("airlock-delegate", agent["prompt"])
                    self.assertLessEqual(len(serialized.encode("utf-8")), 24 * 1024)

    def test_managed_session_settings_trust_only_rendered_providers(self) -> None:
        openai = json.loads(ACCESS.managed_session_settings_json(json.dumps({
            "airlock-sol": {}, "airlock-luna": {},
        })))
        self.assertEqual(set(openai), {"autoMode"})
        environment = openai["autoMode"]["environment"]
        self.assertEqual(environment[0], "$defaults")
        self.assertIn("OpenAI models", environment[1])
        self.assertNotIn("Anthropic Claude", environment[1])
        self.assertIn("airlock-luna, airlock-sol", environment[1])
        self.assertIn("eligible non-ignored untracked regular files", environment[1])
        self.assertIn("built-in Explore, Plan, and general-purpose Agent types", environment[1])
        self.assertIn("inherit the orchestrator model", environment[1])
        self.assertIn("schema-valid family alias", environment[1])
        self.assertIn("Git-ignored or unsafe paths", environment[1])
        self.assertNotIn("airlock-delegate", environment[1])

        mixed = json.loads(ACCESS.managed_session_settings_json(json.dumps({
            "airlock-sol": {}, "airlock-sonnet": {},
        })))
        context = mixed["autoMode"]["environment"][1]
        self.assertIn("OpenAI models", context)
        self.assertIn("Anthropic Claude", context)
        self.assertIn("Credentials", context)

        fast_on = json.loads(ACCESS.managed_session_settings_json(
            json.dumps({"airlock-opus": {}}), "on"
        ))
        self.assertIs(fast_on["fastMode"], True)
        fast_off = json.loads(ACCESS.managed_session_settings_json(
            json.dumps({"airlock-opus": {}}), "off"
        ))
        self.assertIs(fast_off["fastMode"], False)
        with self.assertRaises(ACCESS.AccessError):
            ACCESS.managed_session_settings_json(json.dumps({"airlock-opus": {}}), "maybe")


class GrokAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {
            "AIRLOCK_CONFIG_FILE": str(self.root / "config"),
            "AIRLOCK_ACCESS_FILE": str(self.root / "access.json"),
            "AIRLOCK_CLAUDE_STATE_FILE": str(self.root / "claude.json"),
            "AIRLOCK_PROXY_FAST_CAPABLE": "0",
        }, clear=False)
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp.cleanup()

    def grok_access(self, authenticated: object, **environment: str) -> dict[str, str]:
        # Gating reads the cached login state rather than probing, so a launch
        # never pays for a subprocess. refresh_usage is what fills the cache.
        policy = ACCESS.default_policy()
        policy["providers"]["grok"]["authenticated"] = authenticated
        with patch.dict(os.environ, environment, clear=False):
            policy = ACCESS.apply_runtime_overrides(policy)
        return {
            route: state["access"]
            for route, state in policy["providers"]["grok"]["models"].items()
        }

    def test_environment_override_reports_both_answers(self) -> None:
        for value, expected in (("1", True), ("yes", True), ("0", False), ("no", False)):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"AIRLOCK_ACCESS_GROK_AUTH": value}, clear=False):
                    status = ACCESS.grok_auth_status()
                self.assertIs(status["authenticated"], expected)
                self.assertEqual(status["source"], "environment")

    def test_missing_proxy_reports_unknown_rather_than_logged_out(self) -> None:
        # "Not installed" must stay distinguishable from "installed and logged
        # out", because only the latter is allowed to disable configured routes.
        with patch.dict(os.environ, {"AIRLOCK_ACCESS_GROK_AUTH": ""}, clear=False):
            with patch.object(ACCESS.shutil, "which", return_value=None):
                status = ACCESS.grok_auth_status()
        self.assertIsNone(status["authenticated"])
        self.assertEqual(status["source"], "not_found")

    def test_nonzero_proxy_status_reads_as_logged_out(self) -> None:
        completed = subprocess.CompletedProcess(["proxy"], 1, "", "not logged in")
        with patch.dict(os.environ, {
            "AIRLOCK_ACCESS_GROK_AUTH": "", "AIRLOCK_ACCESS_PROXY": "proxy",
        }, clear=False):
            with patch.object(ACCESS, "_run_status", return_value=completed):
                status = ACCESS.grok_auth_status()
        self.assertIs(status["authenticated"], False)
        self.assertEqual(status["source"], "proxy_status")

    def test_confirmed_logged_out_grok_overrides_configured_routes(self) -> None:
        access = self.grok_access(False, AIRLOCK_GROK_MODELS="grok,composer")
        self.assertEqual(access, {"grok": "unavailable", "composer": "unavailable"})

    def test_unknown_grok_auth_keeps_configured_routes(self) -> None:
        # A machine that has never run a refresh must keep whatever the config
        # asked for. Silently dropping workers on an unknown answer would make
        # the enable look like it failed.
        access = self.grok_access(None, AIRLOCK_GROK_MODELS="grok,composer")
        self.assertEqual(access, {"grok": "unknown", "composer": "unknown"})

    def test_grok_routes_stay_off_without_an_explicit_enable(self) -> None:
        access = self.grok_access(True)
        self.assertEqual(access, {"grok": "unavailable", "composer": "unavailable"})

    def test_grok_probe_is_not_a_usage_refresh(self) -> None:
        # Grok login state is account state. Counting it as a usage refresh
        # would restart the shared fifteen-minute window on every launch.
        ACCESS._write_policy(ACCESS.default_policy(), self.root / "access.json")
        with patch.dict(os.environ, {
            "AIRLOCK_ACCESS_GROK_AUTH": "1",
            "AIRLOCK_ACCESS_CODEX": str(self.root / "missing-codex"),
        }, clear=False):
            _, refreshed = ACCESS.refresh_usage()
        self.assertFalse(refreshed)
        cached = json.loads((self.root / "access.json").read_text(encoding="utf-8"))
        self.assertIs(cached["providers"]["grok"]["authenticated"], True)


class SessionUsageTests(unittest.TestCase):
    def payload(self) -> dict[str, object]:
        return {
            "instance_id": "0123456789abcdef",
            "events": [
                {
                    "provider": "openai",
                    "model": "gpt-5.6-sol",
                    "prompt": "must-not-leak",
                    "authorization": "must-not-leak-either",
                }
            ],
            "summary": [
                {
                    "provider": "openai",
                    "model": "gpt-5.6-sol",
                    "requests": 2,
                    "completed": 1,
                    "errors": 1,
                    "usage_events": 1,
                    "input_tokens": 0,
                    "cache_creation_input_tokens": 11,
                    "cache_read_input_tokens": 320000,
                    "output_tokens": 7,
                }
            ],
        }

    def test_session_usage_keeps_only_valid_cumulative_counts(self) -> None:
        report = ACCESS.session_usage_report(self.payload())
        self.assertEqual(report["source"], "airlock-router")
        self.assertFalse(report["billing"])
        self.assertEqual(report["bounded_event_window"], 1)
        self.assertEqual(report["groups"][0]["cache_read_input_tokens"], 320000)
        rendered = json.dumps(report)
        self.assertNotIn("must-not-leak", rendered)
        lines = "\n".join(ACCESS.session_usage_lines(report))
        self.assertIn("provider-reported, not a bill", lines)
        self.assertIn("cache-read=320000", lines)
        self.assertIn("Claude Code may still show zero", lines)
        self.assertNotIn("must-not-leak", lines)

    def test_session_usage_rejects_non_loopback_and_deceptive_urls(self) -> None:
        self.assertEqual(
            ACCESS.validate_session_router_url("http://127.0.0.1:28471"),
            ("127.0.0.1", 28471),
        )
        for rejected in (
            "https://127.0.0.1:28471",
            "http://localhost:28471",
            "http://127.0.0.1:28471/diagnostics",
            "http://127.0.0.1:28471?target=evil",
            "http://user@127.0.0.1:28471",
            "http://127.0.0.1:0",
            "http://127.0.0.1:65536",
            "http://127.0.0.1:28471.evil.test",
        ):
            with self.subTest(rejected=rejected), self.assertRaises(ACCESS.AccessError):
                ACCESS.validate_session_router_url(rejected)

    def test_session_usage_rejects_invented_or_inconsistent_counts(self) -> None:
        for field, value in (
            ("input_tokens", -1),
            ("output_tokens", True),
            ("requests", 3),
            ("usage_events", 3),
        ):
            payload = self.payload()
            payload["summary"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ACCESS.AccessError):
                ACCESS.session_usage_report(payload)

    def test_session_usage_cli_fetches_only_the_loopback_diagnostics_path(self) -> None:
        body = json.dumps(self.payload(), separators=(",", ":")).encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != "/diagnostics":
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "airlock-access.py"),
                    "session-usage",
                    "--router-url",
                    f"http://127.0.0.1:{server.server_address[1]}",
                    "--json",
                ],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["groups"][0]["output_tokens"], 7)
        self.assertNotIn("must-not-leak", completed.stdout)

    def test_session_usage_rejects_unbounded_or_unexpected_diagnostics(self) -> None:
        payload = self.payload()
        payload["events"] = [{}] * (ACCESS.MAX_SESSION_DIAGNOSTIC_EVENTS + 1)
        with self.assertRaises(ACCESS.AccessError):
            ACCESS.session_usage_report(payload)
        payload = self.payload()
        payload["raw_response"] = "must-not-be-accepted"
        with self.assertRaises(ACCESS.AccessError):
            ACCESS.session_usage_report(payload)


if __name__ == "__main__":
    unittest.main()
