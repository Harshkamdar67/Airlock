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
from io import BytesIO

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


def scrub_ambient_airlock_environment(keep: set[str]) -> None:
    """Drop inherited Airlock variables so tests see only their own overrides.

    A suite run inside a live Airlock session inherits that session's
    configuration, such as AIRLOCK_AGENT_DEPTH or the Fast transition
    credentials. Call this after starting the per-test environment patch.
    """
    for name in list(os.environ):
        if name.startswith("AIRLOCK_") and name not in keep:
            os.environ.pop(name, None)


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
        scrub_ambient_airlock_environment({
            "AIRLOCK_CONFIG_FILE",
            "AIRLOCK_ACCESS_FILE",
            "AIRLOCK_CLAUDE_STATE_FILE",
            "AIRLOCK_PROXY_FAST_CAPABLE",
            "AIRLOCK_ACCESS_GROK_AUTH",
        })

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
                self.assertEqual(result["protocol_version"], 5)
                self.assertEqual(result["components_checked"], len(expected))
                self.assertGreater(result["components_checked"], 15)

    def test_managed_bundle_includes_openrouter_presets_helper(self) -> None:
        bundle = json.loads((ROOT / "config" / "managed-bundle.json").read_text(encoding="utf-8"))
        self.assertIn("bin/airlock_openrouter_presets.py", bundle["components"])
        self.assertIn("bin/airlock_openrouter_presets.py", bundle["platforms"]["common"])
        self.assertTrue(
            (ROOT / "bin" / "airlock_openrouter_presets.py").is_file(),
            "OpenRouter presets helper must exist as a managed repository file",
        )

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

    def test_anthropic_rate_limit_mode_is_persistent_and_overridable(self) -> None:
        ACCESS.write_flat_config_overrides({
            "AIRLOCK_ANTHROPIC_RATE_LIMIT": "handoff",
        })
        state = ACCESS.mode_state()
        self.assertEqual(state["saved_anthropic_rate_limit"], "handoff")
        self.assertEqual(state["effective_anthropic_rate_limit"], "handoff")
        self.assertIn(
            "Anthropic rate limits: handoff",
            "\n".join(ACCESS.mode_status_lines()),
        )
        with patch.dict(
            os.environ, {"AIRLOCK_ANTHROPIC_RATE_LIMIT": "native"}, clear=False
        ):
            self.assertEqual(
                ACCESS.mode_state()["effective_anthropic_rate_limit"], "native"
            )

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
        self.assertIn("Agent depth: 1; named Agents cannot invoke Agent", lines)
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
                # The guidance names what `model=haiku` actually resolves to.
                # A hybrid profile seats Claude Haiku in that slot so Claude
                # Code's own background work, such as WebFetch's page-reading
                # step, keeps running; a pure profile has no Claude model and
                # keeps the discovery model.
                alias_model = (
                    "claude-haiku-4-5-20251001"
                    if profile.startswith("hybrid-")
                    else "gpt-5.6-luna"
                )
                self.assertIn(
                    f"pass `model=haiku` (resolved to {alias_model})", guidance
                )
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
            "fable": "grok-4.6",
            "opus": "grok-4.6",
            "sonnet": "grok-composer-2.5-fast",
            "haiku": "grok-composer-2.5-fast",
        })
        hybrid = ACCESS.session_route_policy(policy, "hybrid-grok-root")
        self.assertEqual(set(hybrid["picker_models"]), {"fable", "opus", "sonnet", "haiku"})
        # Claude Code spends the Haiku slot on its own background work, such as
        # the page-reading step of WebFetch, and rejects an ID it does not
        # recognize. A hybrid profile therefore seats a Claude model there
        # instead of the economical discovery model, which keeps its own
        # AIRLOCK_DISCOVERY_MODEL channel.
        self.assertTrue(hybrid["picker_models"]["haiku"].startswith("claude-"))
        self.assertNotEqual(hybrid["picker_models"]["haiku"], hybrid["discovery_model"])
        self.assertEqual(hybrid["discovery_model"], "gpt-5.6-luna")
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

    def test_auto_hybrid_root_never_selects_extra_or_unavailable_models(self) -> None:
        policy = ACCESS.default_policy()
        # Fresh policy state: Fable is unavailable and Opus is unknown, so the
        # reserved auto root lands on Opus without touching the network.
        self.assertEqual(ACCESS.default_hybrid_root(policy), "opus")
        policy["providers"]["anthropic"]["models"]["fable"]["access"] = "included"
        self.assertEqual(ACCESS.default_hybrid_root(policy), "fable")

        # An extra-class model never wins, even when extra usage is allowed
        # outright, because a family alias cannot carry a confirmation marker.
        policy["providers"]["anthropic"]["models"]["fable"]["access"] = "extra"
        policy["policies"]["extra_usage"] = "allow"
        self.assertEqual(ACCESS.default_hybrid_root(policy), "opus")
        policy["providers"]["anthropic"]["models"]["opus"]["access"] = "extra"
        self.assertEqual(ACCESS.default_hybrid_root(policy), "sonnet")

        # Sonnet is the unconditional final fallback whatever its own class.
        policy["providers"]["anthropic"]["models"]["sonnet"]["access"] = "unavailable"
        self.assertEqual(ACCESS.default_hybrid_root(policy), "sonnet")

        rendered = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "airlock-access.py"), "hybrid-default-root"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=dict(os.environ),
            check=False,
        )
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        # The subprocess reads the same empty access state as the default
        # policy above, so it prints Opus.
        self.assertEqual(rendered.stdout.strip(), "opus")

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
        self.assertEqual(ACCESS.wire_model_id("grok-4.6"), "grok-4.6")
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
            # The Haiku slot holds a Claude model so Claude Code's own
            # background work keeps running; discovery stays on Luna.
            self.assertEqual(hybrid["picker_models"], {
                "fable": "gpt-5.6-sol",
                "opus": "claude-opus-5[1m]",
                "sonnet": "claude-sonnet-5[1m]",
                "haiku": "claude-haiku-4-5-20251001",
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
            self.assertNotIn("claude-fable-5-1[1m]", hybrid["model_ids"])
            self.assertNotIn("gpt-5.6-luna-fast", hybrid["model_ids"])

            policy["providers"]["anthropic"]["models"]["fable"]["access"] = "extra"
            hybrid = ACCESS.session_route_policy(policy, "hybrid-openai-root")
            self.assertIn("claude-fable-5-1[1m]", hybrid["extra_model_ids"])
            self.assertIn("airlock-fable", hybrid["extra_agent_names"])

            policy["providers"]["openai"]["detected_plan"] = "pro"
            hybrid = ACCESS.session_route_policy(policy, "hybrid-openai-root")
            self.assertIn("gpt-5.6-luna-fast", hybrid["model_ids"])
            policy["policies"]["swarm_fast"] = "off"
            hybrid = ACCESS.session_route_policy(policy, "hybrid-openai-root")
            self.assertNotIn("gpt-5.6-luna-fast", hybrid["model_ids"])

            policy["policies"]["extra_usage"] = "never"
            hybrid = ACCESS.session_route_policy(policy, "hybrid-openai-root")
            self.assertNotIn("claude-fable-5-1[1m]", hybrid["model_ids"])

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

    def test_managed_session_settings_web_tools_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / "airlock_web_tools.py"
            script.write_text("# server\n", encoding="utf-8")
            agents = json.dumps({"airlock-luna": {}})

            pure = json.loads(ACCESS.managed_session_settings_json(
                agents, web_tools_script=str(script), profile="grok-pure",
            ))
            self.assertEqual(set(pure), {"autoMode", "permissions", "mcpServers"})
            self.assertEqual(
                pure["permissions"],
                {
                    "allow": [
                        "mcp__airlock-web-tools__web_search",
                        "mcp__airlock-web-tools__fetch_page",
                    ],
                    "deny": ["WebSearch", "WebFetch"],
                },
            )
            self.assertEqual(
                pure["mcpServers"]["airlock-web-tools"]["command"],
                sys.executable,
            )

            hybrid = json.loads(ACCESS.managed_session_settings_json(
                agents, web_tools_script=str(script), profile="hybrid-grok-root",
            ))
            self.assertEqual(set(hybrid), {"autoMode", "permissions", "mcpServers"})
            self.assertEqual(hybrid["permissions"], {
                "allow": [
                    "mcp__airlock-web-tools__web_search",
                    "mcp__airlock-web-tools__fetch_page",
                ],
                "deny": ["WebSearch"],
            })

            anthropic = json.loads(ACCESS.managed_session_settings_json(
                agents, web_tools_script=str(script), profile="hybrid-anthropic-root",
            ))
            self.assertEqual(set(anthropic), {"autoMode"})
            self.assertNotIn("permissions", anthropic)
            self.assertNotIn("mcpServers", anthropic)

            with patch.dict(os.environ, {"AIRLOCK_WEB_TOOLS": "off"}):
                disabled = json.loads(ACCESS.managed_session_settings_json(
                    agents, web_tools_script=str(script), profile="grok-pure",
                ))
            self.assertEqual(set(disabled), {"autoMode"})

            with self.assertRaises(ACCESS.AccessError):
                ACCESS.managed_session_settings_json(
                    agents, web_tools_script=str(script), profile="not-a-profile",
                )

    def test_web_tools_mcp_config_file_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / "airlock_web_tools.py"
            script.write_text("# server\n", encoding="utf-8")
            runtime = Path(temp) / "runtime"
            environment = {
                "AIRLOCK_SESSION_RUNTIME_DIR": str(runtime),
            }

            with patch.dict(os.environ, environment):
                output = ACCESS.write_web_tools_mcp_config(
                    str(script), profile="openrouter-pure"
                )
            self.assertNotIn("\n", output)
            path_text, digest, size_text = output.split("\t")
            self.assertEqual(len(digest), 64)
            self.assertGreater(int(size_text), 0)
            payload = json.loads(
                Path(path_text).read_text(encoding="utf-8")
            )
            entry = payload["mcpServers"]["airlock-web-tools"]
            self.assertEqual(entry["command"], sys.executable)
            self.assertEqual(entry["args"], [str(script.resolve())])
            with patch.dict(os.environ, environment):
                ACCESS.delete_session_artifact(
                    Path(path_text), digest, int(size_text)
                )
            self.assertFalse(Path(path_text).exists())

            with patch.dict(os.environ, environment):
                empty = ACCESS.write_web_tools_mcp_config(
                    str(script), profile="hybrid-anthropic-root"
                )
            self.assertEqual(empty, "")

            with patch.dict(os.environ, {
                **environment,
                "AIRLOCK_WEB_TOOLS": "off",
            }):
                disabled = ACCESS.write_web_tools_mcp_config(
                    str(script), profile="grok-pure"
                )
            self.assertEqual(disabled, "")

            missing = str(Path(temp) / "absent-server.py")
            with patch.dict(os.environ, environment):
                absent = ACCESS.write_web_tools_mcp_config(
                    missing, profile="grok-pure"
                )
            self.assertEqual(absent, "")

            with patch.dict(os.environ, environment):
                with self.assertRaises(ACCESS.AccessError):
                    ACCESS.write_web_tools_mcp_config(
                        str(script), profile="not-a-profile"
                    )


class FastTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.config = self.root / "config"
        self.access = self.root / "access.json"
        self.cwd = self.root / "repo"
        self.cwd.mkdir()
        self.other_cwd = self.root / "other"
        self.other_cwd.mkdir()
        self.pid = 4242
        self.now = 2_000_000_000
        self.environment = patch.dict(os.environ, {
            "AIRLOCK_CONFIG_FILE": str(self.config),
            "AIRLOCK_ACCESS_FILE": str(self.access),
            "AIRLOCK_SESSION_RUNTIME_DIR": str(self.runtime),
            "AIRLOCK_CLAUDE_STATE_FILE": str(self.root / "claude.json"),
            "AIRLOCK_PROXY_FAST_CAPABLE": "1",
            "AIRLOCK_ACCESS_GROK_AUTH": "1",
        }, clear=False)
        self.environment.start()
        scrub_ambient_airlock_environment({
            "AIRLOCK_CONFIG_FILE",
            "AIRLOCK_ACCESS_FILE",
            "AIRLOCK_SESSION_RUNTIME_DIR",
            "AIRLOCK_CLAUDE_STATE_FILE",
            "AIRLOCK_PROXY_FAST_CAPABLE",
            "AIRLOCK_ACCESS_GROK_AUTH",
        })

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp.cleanup()

    def eligible_policy(self, plan: str = "prolite") -> dict[str, object]:
        policy = ACCESS.default_policy()
        policy["providers"]["openai"]["detected_plan"] = plan
        policy["policies"]["openai_fast"] = "off"
        return policy

    def create(self) -> dict[str, str]:
        return ACCESS.fast_transition_create(
            self.pid, self.cwd, now=self.now
        )

    def payload(self, session_id: str = "session-123", **updates: str) -> dict[str, str]:
        payload = {
            "session_id": session_id,
            "cwd": str(self.cwd.resolve()),
            "reason": "prompt_input_exit",
        }
        payload.update(updates)
        return payload

    def arm(self, metadata: dict[str, str], session_id: str = "session-123") -> None:
        ACCESS.fast_transition_arm(
            session_id, metadata["channel"], metadata["nonce"], now=self.now,
        )

    def finalize(self, metadata: dict[str, str], payload: dict[str, str] | None = None) -> None:
        ACCESS.fast_transition_finalize(
            payload or self.payload(), metadata["channel"], metadata["nonce"],
            now=self.now,
        )

    def test_ephemeral_fast_ignores_saved_off_only_for_sol_fast(self) -> None:
        self.config.write_text("AIRLOCK_OPENAI_FAST=off\n", encoding="utf-8")
        before = self.config.read_bytes()
        policy = self.eligible_policy()
        with patch.object(ACCESS, "proxy_fast_capability", return_value={
            "supported": True, "source": "test", "version": "0.1.22",
        }):
            normal = ACCESS.explicit_fast_status(policy, "sol-fast")
            ephemeral = ACCESS.explicit_fast_status(
                policy, "sol-fast", ephemeral=True
            )
            self.assertFalse(normal["eligible"])
            self.assertTrue(ephemeral["eligible"])
            self.assertFalse(ephemeral["enabled_by_policy"])
            with self.assertRaisesRegex(ACCESS.AccessError, "only for sol-fast"):
                ACCESS.explicit_fast_status(policy, "luna-fast", ephemeral=True)
        self.assertEqual(self.config.read_bytes(), before)

    def test_ephemeral_fast_still_refuses_plan_and_proxy(self) -> None:
        with patch.object(ACCESS, "proxy_fast_capability", return_value={
            "supported": True, "source": "test", "version": "0.1.22",
        }):
            status = ACCESS.explicit_fast_status(
                self.eligible_policy("free"), "sol-fast", ephemeral=True
            )
        self.assertFalse(status["eligible"])
        self.assertIn("prolite or pro", status["reason"])
        with patch.object(ACCESS, "proxy_fast_capability", return_value={
            "supported": False, "source": "test", "version": "0.1.21",
        }):
            status = ACCESS.explicit_fast_status(
                self.eligible_policy(), "sol-fast", ephemeral=True
            )
        self.assertFalse(status["eligible"])
        self.assertIn("could not be verified", status["reason"])

    def test_fast_check_cli_ephemeral_is_narrow_and_machine_safe(self) -> None:
        policy = self.eligible_policy()
        ACCESS._write_policy(policy, self.access)
        self.config.write_text("AIRLOCK_OPENAI_FAST=off\n", encoding="utf-8")
        environment = os.environ.copy()
        environment["AIRLOCK_PROXY_FAST_CAPABLE"] = "1"
        command = [
            sys.executable, str(ROOT / "bin" / "airlock-access.py"),
            "fast-check", "--route", "sol-fast", "--quiet",
        ]
        normal = subprocess.run(
            command, text=True, capture_output=True, timeout=5,
            check=False, env=environment,
        )
        ephemeral = subprocess.run(
            command + ["--ephemeral"], text=True, capture_output=True,
            timeout=5, check=False, env=environment,
        )
        self.assertEqual(normal.returncode, 1)
        self.assertEqual(ephemeral.returncode, 0, ephemeral.stderr)
        self.assertEqual(ephemeral.stdout, "")
        luna = subprocess.run(
            [
                sys.executable, str(ROOT / "bin" / "airlock-access.py"),
                "fast-check", "--route", "luna-fast", "--ephemeral", "--quiet",
            ],
            text=True, capture_output=True, timeout=5, check=False,
            env=environment,
        )
        self.assertEqual(luna.returncode, 1)
        self.assertIn("only for sol-fast", luna.stderr)

    def test_create_does_not_require_fast_eligibility(self) -> None:
        with patch.object(
            ACCESS, "load_policy", side_effect=AssertionError("must not load policy")
        ), patch.object(
            ACCESS, "proxy_fast_capability", side_effect=AssertionError("must not probe proxy")
        ):
            metadata = ACCESS.fast_transition_create(
                self.pid, self.cwd, now=self.now
            )
        state = json.loads((self.runtime / metadata["channel"]).read_text())
        self.assertEqual((state["route"], state["model"]), (
            "sol-fast", "gpt-5.6-sol-fast",
        ))
        ACCESS.fast_transition_cleanup(
            self.pid, self.cwd, metadata["channel"], metadata["nonce"]
        )

    def test_create_arm_finalize_consume_happy_path_and_no_config_mutation(self) -> None:
        self.config.write_text("AIRLOCK_OPENAI_FAST=off\n# keep\n", encoding="utf-8")
        before = self.config.read_bytes()
        metadata = self.create()
        self.assertEqual(set(metadata), {"channel", "nonce"})
        self.assertRegex(metadata["channel"], r"^fast-transition-[0-9a-f]{32}\.json$")
        self.assertRegex(metadata["nonce"], r"^[0-9a-f]{64}$")
        channel_path = self.runtime / metadata["channel"]
        state = json.loads(channel_path.read_text(encoding="utf-8"))
        self.assertEqual((state["state"], state["route"], state["model"]), (
            "pending", "sol-fast", "gpt-5.6-sol-fast",
        ))
        self.assertEqual(state["expires_at"], 0)
        self.assertNotIn("armed_at", state)
        self.assertNotIn(metadata["nonce"], channel_path.read_text(encoding="utf-8"))
        self.arm(metadata)
        self.assertEqual(json.loads(channel_path.read_text())["state"], "armed")
        self.finalize(metadata)
        self.assertEqual(json.loads(channel_path.read_text())["state"], "ready")
        session_id = ACCESS.fast_transition_consume(
            self.pid, self.cwd, metadata["channel"], metadata["nonce"], now=self.now
        )
        self.assertEqual(session_id, "session-123")
        self.assertFalse(channel_path.exists())
        with self.assertRaises(ACCESS.AccessError):
            ACCESS.fast_transition_consume(
                self.pid, self.cwd, metadata["channel"], metadata["nonce"], now=self.now
            )
        self.assertEqual(self.config.read_bytes(), before)

    def test_transition_cli_round_trip_uses_only_metadata_and_session_id(self) -> None:
        policy = self.eligible_policy()
        ACCESS._write_policy(policy, self.access)
        self.config.write_text("AIRLOCK_OPENAI_FAST=off\n", encoding="utf-8")
        environment = os.environ.copy()
        environment["AIRLOCK_PROXY_FAST_CAPABLE"] = "1"
        base = [sys.executable, str(ROOT / "bin" / "airlock-access.py")]
        create = subprocess.run(
            base + [
                "fast-transition-create", "--launcher-pid", str(self.pid),
                "--cwd", str(self.cwd),
            ],
            text=True, capture_output=True, timeout=5, check=False,
            env=environment,
        )
        self.assertEqual(create.returncode, 0, create.stderr)
        self.assertRegex(
            create.stdout,
            r"^fast-transition-[0-9a-f]{32}\.json\t[0-9a-f]{64}\n$",
        )
        channel, nonce = create.stdout.rstrip("\n").split("\t")
        metadata = {"channel": channel, "nonce": nonce}
        inherited = {
            **environment,
            ACCESS.FAST_TRANSITION_ENV_CHANNEL: metadata["channel"],
            ACCESS.FAST_TRANSITION_ENV_NONCE: metadata["nonce"],
        }
        arm = subprocess.run(
            base + ["fast-transition-arm", "--session-id", "session-cli"],
            text=True, capture_output=True, timeout=5, check=False, env=inherited,
        )
        self.assertEqual(arm.returncode, 0, arm.stderr)
        self.assertEqual(arm.stdout, "")
        rejected_old_arm = subprocess.run(
            base + [
                "fast-transition-arm", "--session-id", "session-cli",
                "--launcher-pid", str(self.pid), "--cwd", str(self.cwd),
            ],
            text=True, capture_output=True, timeout=5, check=False, env=inherited,
        )
        self.assertNotEqual(rejected_old_arm.returncode, 0)
        self.assertIn("unrecognized arguments", rejected_old_arm.stderr)
        finalize = subprocess.run(
            base + ["fast-transition-finalize"],
            input=json.dumps(self.payload(session_id="session-cli")),
            text=True, capture_output=True, timeout=5, check=False, env=inherited,
        )
        self.assertEqual(finalize.returncode, 0, finalize.stderr)
        self.assertEqual(finalize.stdout, "")
        consume = subprocess.run(
            base + [
                "fast-transition-consume", "--launcher-pid", str(self.pid),
                "--cwd", str(self.cwd),
            ],
            text=True, capture_output=True, timeout=5, check=False, env=inherited,
        )
        self.assertEqual(consume.returncode, 0, consume.stderr)
        self.assertEqual(consume.stdout, "session-cli\n")

    def test_if_ready_consume_is_quiet_for_pending_and_surfaces_unfinalized_arm(self) -> None:
        metadata = self.create()
        pending = ACCESS.fast_transition_consume(
            self.pid, self.cwd, metadata["channel"], metadata["nonce"],
            now=self.now, if_ready=True,
        )
        self.assertIsNone(pending)
        self.assertTrue((self.runtime / metadata["channel"]).exists())
        self.arm(metadata)
        with self.assertRaisesRegex(
            ACCESS.AccessError, "armed but SessionEnd did not finalize"
        ):
            ACCESS.fast_transition_consume(
                self.pid, self.cwd, metadata["channel"], metadata["nonce"],
                now=self.now, if_ready=True,
            )
        ACCESS.fast_transition_cleanup(
            self.pid, self.cwd, metadata["channel"], metadata["nonce"]
        )

        metadata = self.create()
        inherited = {
            **os.environ,
            ACCESS.FAST_TRANSITION_ENV_CHANNEL: metadata["channel"],
            ACCESS.FAST_TRANSITION_ENV_NONCE: metadata["nonce"],
        }
        completed = subprocess.run(
            [
                sys.executable, str(ROOT / "bin" / "airlock-access.py"),
                "fast-transition-consume", "--launcher-pid", str(self.pid),
                "--cwd", str(self.cwd), "--if-ready",
            ],
            text=True, capture_output=True, timeout=5, check=False, env=inherited,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")
        ACCESS.fast_transition_cleanup(
            self.pid, self.cwd, metadata["channel"], metadata["nonce"]
        )

    def test_arm_uses_only_channel_nonce_and_validated_session_id(self) -> None:
        metadata = self.create()
        with self.assertRaisesRegex(ACCESS.AccessError, "nonce does not match"):
            ACCESS.fast_transition_arm(
                "session-123", metadata["channel"], "0" * 64, now=self.now
            )
        self.assertEqual(
            json.loads((self.runtime / metadata["channel"]).read_text())["state"],
            "pending",
        )

        with patch.dict(os.environ, {
            ACCESS.FAST_TRANSITION_ENV_CHANNEL: metadata["channel"],
            ACCESS.FAST_TRANSITION_ENV_NONCE: metadata["nonce"],
        }, clear=False):
            ACCESS.fast_transition_arm("session-123", now=self.now)
        state = json.loads((self.runtime / metadata["channel"]).read_text())
        self.assertEqual(state["state"], "armed")
        self.assertEqual(state["session_id"], "session-123")

    def test_finalize_rejects_wrong_session_cwd_reason_and_invalid_transition(self) -> None:
        cases = (
            ("session", self.payload(session_id="other-session")),
            ("cwd", self.payload(cwd=str(self.other_cwd.resolve()))),
            ("reason", self.payload(reason="other")),
        )
        for label, payload in cases:
            metadata = self.create()
            self.arm(metadata)
            with self.subTest(label=label), self.assertRaises(ACCESS.AccessError):
                ACCESS.fast_transition_finalize(
                    payload, metadata["channel"], metadata["nonce"], now=self.now
                )
            self.assertEqual(
                json.loads((self.runtime / metadata["channel"]).read_text())["state"],
                "armed",
            )
            ACCESS.fast_transition_cleanup(
                self.pid, self.cwd, metadata["channel"], metadata["nonce"]
            )

        metadata = self.create()
        with self.assertRaisesRegex(ACCESS.AccessError, "only from armed"):
            self.finalize(metadata)

    def test_finalize_input_is_bounded_exact_json_and_does_not_parse_transcript(self) -> None:
        parsed = ACCESS._read_fast_transition_finalize_input(BytesIO(
            json.dumps(self.payload()).encode("utf-8")
        ))
        self.assertEqual(parsed["session_id"], self.payload()["session_id"])
        self.assertEqual(parsed["reason"], "prompt_input_exit")
        self.assertEqual(parsed["cwd"], ACCESS._canonical_fast_transition_cwd(self.cwd))
        for raw in (
            b"not-json",
            json.dumps({**self.payload(), "transcript_path": "secret"}).encode(),
            b"{" + b" " * ACCESS.MAX_FAST_TRANSITION_STDIN_BYTES + b"}",
        ):
            with self.subTest(size=len(raw)), self.assertRaises(ACCESS.AccessError):
                ACCESS._read_fast_transition_finalize_input(BytesIO(raw))

    def test_pending_can_arm_hours_later_and_ttl_starts_at_arm(self) -> None:
        hours_later = self.now + 6 * 60 * 60
        metadata = self.create()
        ACCESS.fast_transition_arm(
            "session-123", metadata["channel"], metadata["nonce"],
            now=hours_later,
        )
        state = json.loads((self.runtime / metadata["channel"]).read_text())
        self.assertEqual(state["armed_at"], hours_later)
        self.assertEqual(
            state["expires_at"], hours_later + ACCESS.FAST_TRANSITION_TTL_SECONDS
        )
        with self.assertRaisesRegex(ACCESS.AccessError, "expired"):
            ACCESS.fast_transition_finalize(
                self.payload(), metadata["channel"], metadata["nonce"],
                now=hours_later + ACCESS.FAST_TRANSITION_TTL_SECONDS + 1,
            )
        ACCESS.fast_transition_cleanup(
            self.pid, self.cwd, metadata["channel"], metadata["nonce"]
        )

        metadata = self.create()
        ACCESS.fast_transition_arm(
            "session-123", metadata["channel"], metadata["nonce"],
            now=hours_later,
        )
        ACCESS.fast_transition_finalize(
            self.payload(), metadata["channel"], metadata["nonce"],
            now=hours_later,
        )
        with self.assertRaisesRegex(ACCESS.AccessError, "expired"):
            ACCESS.fast_transition_consume(
                self.pid, self.cwd, metadata["channel"], metadata["nonce"],
                now=hours_later + ACCESS.FAST_TRANSITION_TTL_SECONDS + 1,
            )
        ACCESS.fast_transition_cleanup(
            self.pid, self.cwd, metadata["channel"], metadata["nonce"]
        )

    def test_replay_and_consume_mismatches_fail_closed(self) -> None:
        for field in ("nonce", "pid", "cwd"):
            metadata = self.create()
            self.arm(metadata)
            self.finalize(metadata)
            with self.subTest(field=field), self.assertRaises(ACCESS.AccessError):
                ACCESS.fast_transition_consume(
                    self.pid + (1 if field == "pid" else 0),
                    self.other_cwd if field == "cwd" else self.cwd,
                    metadata["channel"],
                    "f" * 64 if field == "nonce" else metadata["nonce"],
                    now=self.now,
                )
            self.assertTrue((self.runtime / metadata["channel"]).exists())
            ACCESS.fast_transition_cleanup(
                self.pid, self.cwd, metadata["channel"], metadata["nonce"]
            )

    def test_malformed_symlink_and_cleanup(self) -> None:
        metadata = self.create()
        channel_path = self.runtime / metadata["channel"]
        channel_path.write_text("not-json", encoding="utf-8")
        with self.assertRaisesRegex(ACCESS.AccessError, "invalid JSON"):
            ACCESS.fast_transition_arm(
                "session-123", metadata["channel"], metadata["nonce"]
            )
        channel_path.write_bytes(b"x" * (ACCESS.MAX_FAST_TRANSITION_BYTES + 1))
        with self.assertRaisesRegex(ACCESS.AccessError, "unsafe"):
            ACCESS.fast_transition_arm(
                "session-123", metadata["channel"], metadata["nonce"]
            )
        channel_path.unlink()

        metadata = self.create()
        ACCESS.fast_transition_cleanup(
            self.pid, self.cwd, metadata["channel"], metadata["nonce"]
        )
        self.assertFalse((self.runtime / metadata["channel"]).exists())

        if hasattr(os, "symlink"):
            target = self.root / "target.json"
            target.write_text("{}", encoding="utf-8")
            channel = "fast-transition-" + "a" * 32 + ".json"
            link = self.runtime / channel
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlink creation is unavailable")
            with self.assertRaisesRegex(ACCESS.AccessError, "unsafe"):
                ACCESS.fast_transition_arm("session-123", channel, "a" * 64)


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
        scrub_ambient_airlock_environment({
            "AIRLOCK_CONFIG_FILE",
            "AIRLOCK_ACCESS_FILE",
            "AIRLOCK_CLAUDE_STATE_FILE",
            "AIRLOCK_PROXY_FAST_CAPABLE",
        })

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
        self.assertIn("OpenRouter usage is not included", lines)
        self.assertNotIn("must-not-leak", lines)

    def test_session_usage_omits_openrouter_groups(self) -> None:
        payload = self.payload()
        payload["summary"].append({
            "provider": "openrouter",
            "model": "deepseek/deepseek-v4-flash-0731",
            "requests": 1,
            "completed": 1,
            "errors": 0,
            "usage_events": 1,
            "input_tokens": 19,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 5,
        })
        report = ACCESS.session_usage_report(payload)
        self.assertEqual(
            [(group["provider"], group["model"]) for group in report["groups"]],
            [("openai", "gpt-5.6-sol")],
        )
        self.assertNotIn("deepseek/", json.dumps(report))

    def test_session_usage_still_rejects_unknown_providers(self) -> None:
        payload = self.payload()
        payload["summary"][0]["provider"] = "unknown"
        with self.assertRaises(ACCESS.AccessError):
            ACCESS.session_usage_report(payload)

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

    def test_session_usage_accepts_and_bounds_rate_limit_cooldowns(self) -> None:
        # Older routers omit the key entirely.
        report = ACCESS.session_usage_report(self.payload())
        self.assertEqual(report["cooldowns"], [])
        payload = self.payload()
        payload["rate_limit_cooldowns"] = ["gpt-5.6-luna", "gpt-5.6-luna", "grok-test"]
        report = ACCESS.session_usage_report(payload)
        self.assertEqual(report["cooldowns"], ["gpt-5.6-luna", "grok-test"])
        lines = "\n".join(ACCESS.session_usage_lines(report))
        self.assertIn("Cooling down after rate limits", lines)
        self.assertIn("gpt-5.6-luna, grok-test", lines)
        quiet = "\n".join(ACCESS.session_usage_lines(self.report_without_cds()))
        self.assertNotIn("Cooling down", quiet)

    def report_without_cds(self) -> dict[str, object]:
        return ACCESS.session_usage_report(self.payload())

    def test_session_usage_rejects_invalid_cooldown_entries(self) -> None:
        for cooldowns in (
            "gpt-5.6-luna",
            [42],
            ["bad model name with spaces"],
            ["x" * 200],
            ["ok"] * 33,
        ):
            payload = self.payload()
            payload["rate_limit_cooldowns"] = cooldowns
            with self.subTest(cooldowns=cooldowns), self.assertRaises(
                ACCESS.AccessError
            ):
                ACCESS.session_usage_report(payload)


class FailoverChainTests(unittest.TestCase):
    """Rate-limit failover chains stay inside a cost category."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "config"
        self.access = self.root / "access.json"
        self.environment = patch.dict(os.environ, {
            "AIRLOCK_CONFIG_FILE": str(self.config),
            "AIRLOCK_ACCESS_FILE": str(self.access),
            "AIRLOCK_FAILOVER_FILE": str(self.root / "failover.json"),
            "AIRLOCK_MODELS_FILE": str(self.root / "models.json"),
            "AIRLOCK_OPENROUTER_REGISTRY_FILE": str(
                self.root / "openrouter-registry.json"
            ),
            "AIRLOCK_PROXY_FAST_CAPABLE": "0",
        }, clear=False)
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp.cleanup()

    def policy(self, *, failover="ask", extra_usage="ask") -> dict[str, object]:
        return {
            "policies": {
                "failover": failover,
                "extra_usage": extra_usage,
            }
        }

    def worker(self, route, model, provider, cost, access="included"):
        return {
            "route": route,
            "agent": f"airlock-{route}",
            "model": model,
            "provider": provider,
            "access": access,
            "cost": cost,
        }

    def routes(self):
        return {
            "gpt-5.6-luna": "openai",
            "grok-composer-2.5-fast": "grok",
            "claude-haiku-4-5-20251001": "anthropic",
            "gpt-5.6-sol": "openai",
            "claude-opus-5[1m]": "anthropic",
            "claude-opus-5": "anthropic",
            "vendor/model-test": "openrouter",
        }

    def test_economical_models_chain_within_their_category(self) -> None:
        workers = [
            self.worker("luna", "gpt-5.6-luna", "openai", "economical"),
            self.worker("composer", "grok-composer-2.5-fast", "grok", "economical"),
            self.worker(
                "haiku", "claude-haiku-4-5-20251001", "anthropic", "economical"
            ),
        ]
        chains = ACCESS.session_failover_chains(
            self.policy(), workers, self.routes()
        )
        self.assertEqual(chains["gpt-5.6-luna"], [
            "grok-composer-2.5-fast",
            "claude-haiku-4-5-20251001",
        ])
        self.assertEqual(chains["grok-composer-2.5-fast"], [
            "gpt-5.6-luna",
            "claude-haiku-4-5-20251001",
        ])

    def test_premium_and_standard_categories_do_not_mix(self) -> None:
        workers = [
            self.worker("luna", "gpt-5.6-luna", "openai", "economical"),
            self.worker("sol", "gpt-5.6-sol", "openai", "premium"),
            self.worker("opus", "claude-opus-5[1m]", "anthropic", "premium"),
            self.worker("terra", "gpt-5.6-terra", "openai", "standard"),
        ]
        chains = ACCESS.session_failover_chains(
            self.policy(), workers, self.routes()
        )
        self.assertEqual(chains["gpt-5.6-sol"], ["claude-opus-5"])
        self.assertEqual(chains["claude-opus-5[1m]"], ["gpt-5.6-sol"])
        self.assertEqual(chains["claude-opus-5"], ["gpt-5.6-sol"])
        # A lone standard worker has no same-category peer and no chain.
        self.assertNotIn("gpt-5.6-terra", chains)
        self.assertNotIn("gpt-5.6-luna", chains)

    def test_extra_gated_targets_require_the_allow_policy(self) -> None:
        workers = [
            self.worker("luna", "gpt-5.6-luna", "openai", "economical"),
            self.worker(
                "or-a", "vendor/model-a", "openrouter", "unknown", access="extra"
            ),
            self.worker(
                "or-b", "vendor/model-b", "openrouter", "unknown", access="extra"
            ),
        ]
        routes = {
            "gpt-5.6-luna": "openai",
            "vendor/model-a": "openrouter",
            "vendor/model-b": "openrouter",
        }
        ask = ACCESS.session_failover_chains(
            self.policy(extra_usage="ask"), workers, routes
        )
        # Under ask the extra-gated pair is excluded and luna is alone in its
        # category, so no chain forms at all.
        self.assertEqual(ask, {})
        allow = ACCESS.session_failover_chains(
            self.policy(extra_usage="allow"), workers, routes
        )
        # Under allow the two extra-gated models of one category chain to
        # each other; luna still has no same-category peer.
        self.assertEqual(allow["vendor/model-a"], ["vendor/model-b"])
        self.assertEqual(allow["vendor/model-b"], ["vendor/model-a"])
        self.assertNotIn("gpt-5.6-luna", allow)

    def test_never_policy_disables_all_chains(self) -> None:
        workers = [
            self.worker("luna", "gpt-5.6-luna", "openai", "economical"),
            self.worker("composer", "grok-composer-2.5-fast", "grok", "economical"),
        ]
        chains = ACCESS.session_failover_chains(
            self.policy(failover="never"), workers, self.routes()
        )
        self.assertEqual(chains, {})

    def test_peers_outside_the_route_table_are_dropped(self) -> None:
        workers = [
            self.worker("luna", "gpt-5.6-luna", "openai", "economical"),
            self.worker("composer", "grok-composer-2.5-fast", "grok", "economical"),
        ]
        chains = ACCESS.session_failover_chains(
            self.policy(),
            workers,
            {"gpt-5.6-luna": "openai"},
        )
        self.assertEqual(chains, {})

    def test_every_chain_peer_has_a_known_context_window(self) -> None:
        """A peer the overflow pre-screen cannot size is a wasted round trip.

        Chains name peers by wire ID, so a window filed only under the
        ``[1m]`` form leaves the peer the router actually retargets to
        looking size-unknown. That silently disabled the pre-screen for
        every 1M Claude model, which are the most common premium peers.
        This asserts the two maps agree rather than asserting any number,
        so it keeps holding when windows change.
        """
        policy = ACCESS.load_policy()
        for profile, root in (
            ("hybrid-anthropic-root", "claude-opus-5[1m]"),
            ("hybrid-openai-root", "gpt-5.6-sol"),
        ):
            with self.subTest(profile=profile):
                snapshot = ACCESS.build_session_snapshot(policy, profile, root)
                windows = snapshot.context_windows
                missing = sorted({
                    peer
                    for peers in snapshot.failover.values()
                    for peer in peers
                    # A declared OpenRouter route has no window on purpose:
                    # Airlock does not infer sizes it cannot verify.
                    if snapshot.routes.get(peer) != "openrouter"
                    and peer not in windows
                })
                self.assertEqual(missing, [])

    def test_handoff_editing_uses_route_names_not_model_ids(self) -> None:
        """Editing the tree by hand meant knowing exact IDs, suffix and all.

        The point of the command is that a person types "sol" and "opus",
        never "gpt-5.6-sol" or "claude-opus-5[1m]", and that a typo is
        answered with the list of names that would have worked.
        """
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        target = Path(temporary.name) / "failover.json"
        with patch.dict(os.environ, {"AIRLOCK_FAILOVER_FILE": str(target)}):
            profile = "hybrid-anthropic-root"
            lines = ACCESS.run_handoff("set", ["sol", "opus"], profile)
            self.assertIn("sol now hands off to opus.", lines[0])
            written = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(written["schema_version"], 1)
            self.assertEqual(
                written["chains"], {"gpt-5.6-sol": ["claude-opus-5"]}
            )
            # The tree marks a declared order so it is distinguishable.
            tree = chr(10).join(ACCESS.run_handoff("show", [], profile))
            self.assertRegex(tree, r"sol\s+->\s+opus\s+\[yours\]")

            ACCESS.run_handoff("off", ["sol"], profile)
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8"))["chains"],
                {"gpt-5.6-sol": []},
            )

            ACCESS.run_handoff("clear", ["sol"], profile)
            self.assertFalse(target.exists())

            with self.assertRaisesRegex(ACCESS.AccessError, "unknown route: slo"):
                ACCESS.run_handoff("set", ["slo", "opus"], profile)
            with self.assertRaisesRegex(ACCESS.AccessError, "cannot hand off to itself"):
                ACCESS.run_handoff("set", ["sol", "sol"], profile)

    def test_recommended_handoff_drops_routes_you_have_not_connected(self) -> None:
        """The suggested tree has to survive a partly connected setup.

        It names every route Airlock ships, but most people enable a subset.
        A peer that is not enabled must disappear rather than be written into
        failover.json, where it would fail the next launch, and a source left
        with no reachable peer must be skipped rather than written empty,
        because an empty list means "never hand off".
        """
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        target = Path(temporary.name) / "failover.json"
        with patch.dict(os.environ, {"AIRLOCK_FAILOVER_FILE": str(target)}):
            policy = ACCESS.load_policy()
            workers = ACCESS.handoff_workers(policy, "openai-pure")
            routes = {w["route"] for w in workers}
            self.assertNotIn("opus", routes)

            chains = ACCESS.recommended_handoff_chains(workers)
            flat = {peer for peers in chains.values() for peer in peers}
            self.assertTrue(flat)
            for peer in flat:
                self.assertIn(
                    peer,
                    {ACCESS.wire_model_id(w["model"]) for w in workers},
                    "a peer that is not enabled must never be written",
                )
            self.assertNotIn([], list(chains.values()))

            # Whatever it writes must satisfy the loader that reads it back.
            ACCESS.write_failover_chains(chains)
            self.assertEqual(
                ACCESS.load_failover_chains(target),
                {k: tuple(v) for k, v in chains.items()},
            )

    def test_recommended_handoff_applies_live_enabled_route_config(self) -> None:
        """The cache records account probes; config owns enabled route lists."""

        self.config.write_text(
            "AIRLOCK_ANTHROPIC_MODELS=opus,sonnet,fable\n"
            "AIRLOCK_OPENAI_MODELS=sol,terra,luna\n"
            "AIRLOCK_GROK_MODELS=grok,composer\n",
            encoding="utf-8",
        )
        cached = ACCESS.default_policy()
        cached["providers"]["grok"]["authenticated"] = True
        ACCESS._write_policy(cached, self.access)
        target = self.root / "recommended-failover.json"
        with patch.dict(os.environ, {"AIRLOCK_FAILOVER_FILE": str(target)}):
            ACCESS.run_handoff(
                "recommended", [], "hybrid-anthropic-root"
            )
        chains = json.loads(target.read_text(encoding="utf-8"))["chains"]
        self.assertEqual(
            chains["claude-fable-5-1"],
            ["gpt-5.6-sol", "claude-opus-5", "grok-4.6"],
        )
        self.assertEqual(
            chains["gpt-5.6-sol"],
            ["claude-opus-5", "grok-4.6", "claude-fable-5-1"],
        )
        self.assertIn("grok-4.6", chains)
        self.assertIn("grok-composer-2.5-fast", chains)

    def test_shipped_grok_routes_stay_inside_the_proxy_catalog(self) -> None:
        """A rate-limited root must never fail over to an unknown Grok ID.

        The tested claude-code-proxy 0.1.35 catalog lists grok-4.5, grok-4.6,
        and grok-composer-2.5-fast. Pinning a flagship the installed proxy
        does not carry makes every same-category failover hop onto that ID,
        and the proxy then rejects the whole conversation with `Unknown
        model`. That is what happened when beta.7 pinned grok-4.6 against a
        0.1.22 proxy. This guard fails the suite the moment a profile or
        managed agent pins something the catalog does not list.
        """
        proxy_catalog = {"grok-4.5", "grok-4.6", "grok-composer-2.5-fast"}
        shipped = [
            profile["model"]
            for provider in ACCESS.MODEL_PROFILES.values()
            for profile in provider.values()
            if profile["model"].startswith("grok-")
        ]
        self.assertEqual(set(shipped), {"grok-4.6", "grok-composer-2.5-fast"})
        for model in shipped:
            self.assertIn(model, proxy_catalog)
        with open(
            ROOT / "config" / "grok-agents.json",
            encoding="utf-8",
        ) as handle:
            agents = json.load(handle)
        managed = [
            definition["model"]
            for name, definition in agents.items()
            if not name.startswith("_") and str(definition.get("model", "")).startswith("grok-")
        ]
        self.assertEqual(managed, ["grok-4.6", "grok-composer-2.5-fast"])

    def test_premium_failover_chains_use_a_supported_grok_id(self) -> None:
        """Opus hitting 429 must hop to an ID the installed proxy serves.

        Regression for the installed-session failure where an Opus rate limit
        failed over onto a Grok ID the proxy did not carry, the proxy answered
        `Unknown model`, and that surfaced as a 400 on the conversation.
        """
        policy = {"policies": {"failover": "ask", "extra_usage": "ask"}}
        providers = {
            "opus": ("anthropic", ACCESS.MODEL_PROFILES["anthropic"]["opus"]),
            "sol": ("openai", ACCESS.MODEL_PROFILES["openai"]["sol"]),
            "grok": ("grok", ACCESS.MODEL_PROFILES["grok"]["grok"]),
        }
        workers = []
        for route, (provider, profile) in providers.items():
            workers.append({
                "route": route,
                "agent": profile["agent"],
                "model": profile["model"],
                "provider": provider,
                "access": "included",
                "cost": profile["cost"],
            })
        # A real session registers the Claude wire form alongside each full ID.
        routes = {
            "claude-opus-5[1m]": "anthropic",
            "claude-opus-5": "anthropic",
            "gpt-5.6-sol": "openai",
            "grok-4.6": "grok",
        }
        chains = ACCESS.session_failover_chains(policy, workers, routes)
        self.assertEqual(chains["claude-opus-5"], ["grok-4.6", "gpt-5.6-sol"])
        self.assertEqual(chains["gpt-5.6-sol"], ["grok-4.6", "claude-opus-5"])
        # Whatever the shipped pin is, every Grok hop a chain produces has to
        # be an ID the tested proxy catalogs list.
        proxy_catalog = {"grok-4.5", "grok-4.6", "grok-composer-2.5-fast"}
        for chain in chains.values():
            for hop in chain:
                if hop.startswith("grok-"):
                    self.assertIn(hop, proxy_catalog)


class DeclaredFailoverChainTests(unittest.TestCase):
    """A user-owned failover.json replaces derived chains for its sources."""

    def policy(self, *, failover="ask", extra_usage="ask") -> dict[str, object]:
        return {
            "policies": {
                "failover": failover,
                "extra_usage": extra_usage,
            }
        }

    def worker(self, route, model, provider, cost, access="included"):
        return {
            "route": route,
            "agent": f"airlock-{route}",
            "model": model,
            "provider": provider,
            "access": access,
            "cost": cost,
        }

    def routes(self):
        return {
            "gpt-5.6-luna": "openai",
            "grok-composer-2.5-fast": "grok",
            "claude-haiku-4-5-20251001": "anthropic",
            "gpt-5.6-sol": "openai",
            "claude-opus-5[1m]": "anthropic",
            "claude-opus-5": "anthropic",
            "vendor/model-test": "openrouter",
        }

    def workers(self):
        return [
            self.worker("luna", "gpt-5.6-luna", "openai", "economical"),
            self.worker(
                "composer", "grok-composer-2.5-fast", "grok", "economical"
            ),
            self.worker(
                "haiku", "claude-haiku-4-5-20251001", "anthropic", "economical"
            ),
            self.worker("sol", "gpt-5.6-sol", "openai", "premium"),
            self.worker("opus", "claude-opus-5[1m]", "anthropic", "premium"),
        ]

    def test_declared_chain_replaces_derived_verbatim(self) -> None:
        declared = {"claude-opus-5[1m]": ("gpt-5.6-luna", "grok-composer-2.5-fast")}
        chains = ACCESS.session_failover_chains(
            self.policy(),
            self.workers(),
            self.routes(),
            declared=declared,
        )
        # Verbatim order wins over the derived premium chain.
        self.assertEqual(chains["claude-opus-5[1m]"], [
            "gpt-5.6-luna",
            "grok-composer-2.5-fast",
        ])
        self.assertEqual(chains["claude-opus-5"], [
            "gpt-5.6-luna",
            "grok-composer-2.5-fast",
        ])
        # Undeclared sources keep their derived chains.
        self.assertEqual(chains["gpt-5.6-sol"], ["claude-opus-5"])

    def test_declared_chain_may_cross_categories_and_reverse_order(self) -> None:
        declared = {
            "gpt-5.6-sol": (
                "claude-haiku-4-5-20251001",
                "gpt-5.6-luna",
                "claude-opus-5[1m]",
            ),
        }
        chains = ACCESS.session_failover_chains(
            self.policy(),
            self.workers(),
            self.routes(),
            declared=declared,
        )
        self.assertEqual(chains["gpt-5.6-sol"], [
            "claude-haiku-4-5-20251001",
            "gpt-5.6-luna",
            "claude-opus-5",
        ])

    def test_empty_peer_list_opts_the_source_out(self) -> None:
        declared = {"gpt-5.6-sol": ()}
        chains = ACCESS.session_failover_chains(
            self.policy(),
            self.workers(),
            self.routes(),
            declared=declared,
        )
        self.assertNotIn("gpt-5.6-sol", chains)
        # Opting out one source does not strip it from other chains.
        self.assertEqual(chains["claude-haiku-4-5-20251001"], [
            "gpt-5.6-luna",
            "grok-composer-2.5-fast",
        ])

    def test_extra_gated_peers_still_require_the_allow_policy(self) -> None:
        workers = self.workers() + [
            self.worker(
                "or-a", "vendor/model-a", "openrouter", "unknown", access="extra"
            ),
        ]
        routes = dict(self.routes(), **{"vendor/model-a": "openrouter"})
        declared = {"claude-opus-5[1m]": ("vendor/model-a", "gpt-5.6-sol")}
        ask = ACCESS.session_failover_chains(
            self.policy(extra_usage="ask"), workers, routes, declared=declared
        )
        # The extra peer is filtered; the remaining named peer survives.
        self.assertEqual(ask["claude-opus-5[1m]"], ["gpt-5.6-sol"])
        allow = ACCESS.session_failover_chains(
            self.policy(extra_usage="allow"), workers, routes, declared=declared
        )
        self.assertEqual(allow["claude-opus-5[1m]"], ["vendor/model-a", "gpt-5.6-sol"])

    def test_never_policy_kills_declared_chains_too(self) -> None:
        declared = {"gpt-5.6-sol": ("gpt-5.6-luna",)}
        chains = ACCESS.session_failover_chains(
            self.policy(failover="never"),
            self.workers(),
            self.routes(),
            declared=declared,
        )
        self.assertEqual(chains, {})

    def test_unrouted_sources_and_peers_are_dropped(self) -> None:
        declared = {
            "vendor/model-test": ("gpt-5.6-luna",),
            "gpt-5.6-sol": ("grok-composer-2.5-fast",),
        }
        chains = ACCESS.session_failover_chains(
            self.policy(),
            self.workers(),
            {"gpt-5.6-luna": "openai", "gpt-5.6-sol": "openai"},
            declared=declared,
        )
        # vendor/model-test is unrouted here so its declaration is inert;
        # sol's composer peer is not a session worker and is filtered.
        self.assertNotIn("vendor/model-test", chains)
        self.assertEqual(chains["gpt-5.6-sol"], [])

    def test_self_and_duplicate_peers_are_filtered_at_runtime(self) -> None:
        declared = {
            "gpt-5.6-sol": ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-luna"),
        }
        chains = ACCESS.session_failover_chains(
            self.policy(),
            self.workers(),
            self.routes(),
            declared=declared,
        )
        self.assertEqual(chains["gpt-5.6-sol"], ["gpt-5.6-luna"])


class FailoverFileTests(unittest.TestCase):
    """failover.json loads with the same fail-closed rules as models.json."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "failover.json"

    def load(self):
        with patch.dict(os.environ, {"AIRLOCK_FAILOVER_FILE": str(self.path)}):
            return ACCESS.load_failover_chains()

    def test_missing_file_is_an_empty_catalog(self) -> None:
        self.assertEqual(self.load(), {})

    def test_valid_file_round_trips(self) -> None:
        self.path.write_text(
            json.dumps({
                "schema_version": 1,
                "chains": {
                    "claude-opus-5[1m]": ["gpt-5.6-sol", "gpt-5.6-luna"],
                    "gpt-5.6-sol": [],
                },
            }),
            encoding="utf-8",
        )
        self.assertEqual(
            self.load(),
            {
                "claude-opus-5[1m]": ("gpt-5.6-sol", "gpt-5.6-luna"),
                "gpt-5.6-sol": (),
            },
        )

    def test_a_retired_default_stays_accepted_but_inert(self) -> None:
        # The Fable route moved from claude-fable-5 to claude-fable-5-1. A
        # failover.json written before that still names the old ID; failing
        # the launch over it would punish the user for our upgrade, so the
        # retired name loads fine and simply never matches a live route.
        self.path.write_text(
            json.dumps({
                "schema_version": 1,
                "chains": {
                    "claude-fable-5": ["gpt-5.6-sol"],
                    "gpt-5.6-sol": ["claude-opus-5", "claude-fable-5"],
                },
            }),
            encoding="utf-8",
        )
        self.assertEqual(
            self.load(),
            {
                "claude-fable-5": ("gpt-5.6-sol",),
                "gpt-5.6-sol": ("claude-opus-5", "claude-fable-5"),
            },
        )

    def test_unknown_source_fails_closed_naming_the_key(self) -> None:
        self.path.write_text(
            json.dumps({
                "schema_version": 1,
                "chains": {"totally-made-up": ["gpt-5.6-sol"]},
            }),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ACCESS.AccessError, "totally-made-up"):
            self.load()

    def test_unknown_peer_fails_closed_naming_the_index(self) -> None:
        self.path.write_text(
            json.dumps({
                "schema_version": 1,
                "chains": {"gpt-5.6-sol": ["nope/nope"]},
            }),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ACCESS.AccessError, r"chains\[.gpt-5\.6-sol.\]\[0\]"):
            self.load()

    def test_wrong_schema_version_fails_closed(self) -> None:
        self.path.write_text(
            json.dumps({"schema_version": 2, "chains": {}}), encoding="utf-8"
        )
        with self.assertRaisesRegex(ACCESS.AccessError, "schema_version"):
            self.load()

    def test_self_chain_fails_closed(self) -> None:
        self.path.write_text(
            json.dumps({
                "schema_version": 1,
                "chains": {"gpt-5.6-sol": ["gpt-5.6-sol"]},
            }),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ACCESS.AccessError, "itself"):
            self.load()

    def test_repeated_peer_fails_closed(self) -> None:
        self.path.write_text(
            json.dumps({
                "schema_version": 1,
                "chains": {
                    "gpt-5.6-sol": ["gpt-5.6-luna", "gpt-5.6-luna"],
                },
            }),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ACCESS.AccessError, "repeats"):
            self.load()

    def test_garbage_json_fails_closed(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(ACCESS.AccessError, "failover.json is invalid"):
            self.load()

    def test_symlink_refused(self) -> None:
        target = Path(self.tmp.name) / "real.json"
        target.write_text("{}", encoding="utf-8")
        try:
            self.path.symlink_to(target)
        except OSError:
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ACCESS.AccessError, "safe regular file"):
            self.load()


class DeclaredSnapshotIntegrationTests(unittest.TestCase):
    """The snapshot mint picks up failover.json through the single call site."""

    def test_session_failover_chains_lazy_loads_the_file(self) -> None:
        workers = [
            {
                "route": "sol",
                "agent": "airlock-sol",
                "model": "gpt-5.6-sol",
                "provider": "openai",
                "access": "included",
                "cost": "premium",
            },
            {
                "route": "luna",
                "agent": "airlock-luna",
                "model": "gpt-5.6-luna",
                "provider": "openai",
                "access": "included",
                "cost": "economical",
            },
        ]
        routes = {"gpt-5.6-sol": "openai", "gpt-5.6-luna": "openai"}
        policy = {"policies": {"failover": "ask", "extra_usage": "ask"}}
        declared = {"gpt-5.6-sol": ("gpt-5.6-luna",)}
        original = ACCESS.load_failover_chains
        seen: list[bool] = []

        def spy():
            seen.append(True)
            return declared

        ACCESS.load_failover_chains = spy
        try:
            chains = ACCESS.session_failover_chains(policy, workers, routes)
        finally:
            ACCESS.load_failover_chains = original
        self.assertEqual(seen, [True])
        # sol's derived chain is replaced by the declared one; luna has no
        # same-category peer and no declaration, so it keeps no chain.
        self.assertEqual(chains["gpt-5.6-sol"], ["gpt-5.6-luna"])
        self.assertNotIn("gpt-5.6-luna", chains)


class OpenRouterLastResortPeerTests(unittest.TestCase):
    """The declared OpenRouter root serves as a deliberate last-resort peer."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(
            os.environ,
            {"AIRLOCK_FAILOVER_FILE": str(Path(self.temp.name) / "failover.json")},
            clear=False,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp.cleanup()

    @staticmethod
    def _policy() -> dict[str, object]:
        return {"policies": {"failover": "ask", "extra_usage": "ask"}}

    @staticmethod
    def _worker(route, model, provider, cost, access="included"):
        return {
            "route": route,
            "agent": f"airlock-{route}",
            "model": model,
            "provider": provider,
            "access": access,
            "cost": cost,
        }

    def _workers(self):
        return [
            self._worker("grok", "grok-4.6", "grok", "premium"),
            self._worker("sol", "gpt-5.6-sol", "openai", "premium"),
            self._worker("opus", "claude-opus-5[1m]", "anthropic", "premium"),
            self._worker("ox", "stealth/ox-alpha", "openrouter", "standard"),
        ]

    def _routes(self):
        return {
            "grok-4.6": "grok",
            "gpt-5.6-sol": "openai",
            "claude-opus-5[1m]": "anthropic",
            "claude-opus-5": "anthropic",
            "stealth/ox-alpha": "openrouter",
        }

    def test_premium_chains_end_with_the_openrouter_peer(self) -> None:
        chains = ACCESS.session_failover_chains(
            self._policy(), self._workers(), self._routes()
        )
        self.assertEqual(
            chains["claude-opus-5[1m]"],
            ["grok-4.6", "gpt-5.6-sol", "stealth/ox-alpha"],
        )
        self.assertEqual(
            chains["gpt-5.6-sol"],
            ["grok-4.6", "claude-opus-5", "stealth/ox-alpha"],
        )

    def test_openrouter_sources_never_receive_the_peer(self) -> None:
        chains = ACCESS.session_failover_chains(
            self._policy(), self._workers(), self._routes()
        )
        self.assertNotIn("stealth/ox-alpha", chains)

    def test_the_off_switch_removes_the_peer(self) -> None:
        with patch.dict(os.environ, {"AIRLOCK_OPENROUTER_CHAIN_PEER": "off"}):
            chains = ACCESS.session_failover_chains(
                self._policy(), self._workers(), self._routes()
            )
        self.assertEqual(chains["claude-opus-5[1m]"], ["grok-4.6", "gpt-5.6-sol"])
        self.assertNotIn("stealth/ox-alpha", json.dumps(chains))


class CustomModelDeclarationTests(unittest.TestCase):
    """models.json declarations validate strictly and render as agents."""

    def _entry(self, **overrides):
        entry = {
            "id": "grok-4.7",
            "provider": "grok",
            "effort_ceiling": "xhigh",
            "context_window": None,
            "cost": "premium",
            "enabled": True,
        }
        entry.update(overrides)
        return entry

    def _document(self, *entries):
        return {"schema_version": 1, "models": list(entries)}

    def test_a_valid_declaration_produces_a_safe_agent_name(self) -> None:
        entries = ACCESS.validate_custom_models(self._document(self._entry()))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].model_id, "grok-4.7")
        self.assertEqual(entries[0].agent_name, "airlock-custom-grok-grok-4-7")
        self.assertEqual(entries[0].effort_ceiling, "xhigh")

    def test_disabled_entries_are_kept_but_marked(self) -> None:
        entries = ACCESS.validate_custom_models(
            self._document(self._entry(enabled=False))
        )
        self.assertFalse(entries[0].enabled)

    def test_duplicate_ids_are_rejected(self) -> None:
        with self.assertRaisesRegex(ACCESS.AccessError, "duplicates"):
            ACCESS.validate_custom_models(
                self._document(self._entry(), self._entry())
            )

    def test_unknown_providers_and_ceilings_are_rejected(self) -> None:
        with self.assertRaisesRegex(ACCESS.AccessError, "provider"):
            ACCESS.validate_custom_models(
                self._document(self._entry(provider="anthropic"))
            )
        with self.assertRaisesRegex(ACCESS.AccessError, "effort_ceiling"):
            ACCESS.validate_custom_models(
                self._document(self._entry(effort_ceiling="ultra"))
            )

    def test_codex_ids_must_be_canonical_gpt_ids(self) -> None:
        with self.assertRaisesRegex(ACCESS.AccessError, "gpt-"):
            ACCESS.validate_custom_models(
                self._document(self._entry(provider="codex"))
            )

    def test_openrouter_ids_must_have_two_segments(self) -> None:
        with self.assertRaisesRegex(ACCESS.AccessError, "two-segment"):
            ACCESS.validate_custom_models(
                self._document(self._entry(provider="openrouter"))
            )

    def test_load_missing_file_yields_no_entries(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            target = Path(directory) / "models.json"
            self.assertEqual(ACCESS.load_custom_models(target), ())

    def test_load_reads_enabled_entries_from_disk(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            target = Path(directory) / "models.json"
            target.write_text(json.dumps(self._document(self._entry())), encoding="utf-8")
            entries = ACCESS.load_custom_models(target)
        self.assertEqual([entry.model_id for entry in entries], ["grok-4.7"])

    def test_malformed_documents_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            target = Path(directory) / "models.json"
            target.write_text("{ not json", encoding="utf-8")
            with self.assertRaises(ACCESS.AccessError):
                ACCESS.load_custom_models(target)

    def test_enabled_filter_honors_the_flag(self) -> None:
        entries = ACCESS.validate_custom_models(self._document(
            self._entry(),
            self._entry(id="x-ai/grok-5", provider="openrouter", enabled=False),
        ))
        policy = {"_custom_models": entries}
        self.assertEqual(
            [entry.model_id for entry in ACCESS.enabled_custom_models(policy)],
            ["grok-4.7"],
        )


class RouterStatusLinesTests(unittest.TestCase):
    """The status renderer stays honest about what each event recorded."""

    @staticmethod
    def _payload(events: list[dict[str, object]]) -> dict[str, object]:
        return {
            "profile": "hybrid-openai-root",
            "root_model": "gpt-5.6-sol",
            "root_provider": "openai",
            "events": events,
        }

    def test_renders_each_recorded_event_shape(self) -> None:
        lines = ACCESS.router_status_lines(self._payload([
            {"timestamp": "2026-08-26T10:00:01Z", "kind": "session_model_pinned",
             "model": "gpt-5.6-sol", "provider": "openai"},
            {"timestamp": "2026-08-26T10:00:02Z", "kind": "openrouter_effort_clamped",
             "model": "stealth/ox-alpha", "requested": "max", "forwarded": "high"},
            {"timestamp": "2026-08-26T10:00:03Z", "kind": "openrouter_effort_clamped",
             "model": "stealth/ox-alpha", "requested": "xhigh",
             "forwarded": "high", "ceiling": "medium"},
            {"timestamp": "2026-08-26T10:00:04Z", "kind": "future_action_v2",
             "message": "SHOULD_NOT_PRINT", "credential": "SENTINEL_SECRET"},
        ]))
        self.assertEqual(lines, [
            "Airlock router: hybrid-openai-root profile; root gpt-5.6-sol (openai)",
            "10:00:01Z - Pinned gpt-5.6-sol (openai) as the session root.",
            # Routers older than per-route ceilings recorded no ceiling; the
            # line must not invent one.
            "10:00:02Z - Clamped OpenRouter effort for stealth/ox-alpha from max to high.",
            "10:00:03Z - Clamped OpenRouter effort for stealth/ox-alpha from xhigh to high"
            " (route ceiling medium).",
            # Unknown kinds fall back to the kind alone and never to event fields.
            "10:00:04Z - Router action: future_action_v2.",
        ])

    def test_keeps_only_the_newest_actions_within_the_bound(self) -> None:
        events = [
            {"timestamp": f"2026-08-26T10:00:{minute:02d}Z", "kind": "future_action_v1"}
            for minute in range(12)
        ]
        lines = ACCESS.router_status_lines(self._payload(events))
        self.assertEqual(len(lines), ACCESS.MAX_STATUS_LINES)
        self.assertEqual(lines[1], "10:00:03Z - Router action: future_action_v1.")
        self.assertEqual(lines[-1], "10:00:11Z - Router action: future_action_v1.")

    def test_reports_an_empty_event_window_without_inventing_lines(self) -> None:
        lines = ACCESS.router_status_lines(self._payload([]))
        self.assertEqual(lines, [
            "Airlock router: hybrid-openai-root profile; root gpt-5.6-sol (openai)",
            "No router actions have been recorded yet.",
        ])

    def test_renders_the_handoff_and_cooldown_events(self) -> None:
        # A recorded kind that has no label falls through to the bare kind
        # name, which reads as a bug report rather than an explanation, so
        # every handoff event the router emits is pinned here.
        lines = ACCESS.router_status_lines(self._payload([
            {"timestamp": "2026-08-26T10:00:01Z", "kind": "rate_limit_cooldown_skipped",
             "model": "gpt-5.6-sol"},
            {"timestamp": "2026-08-26T10:00:02Z", "kind": "rate_limit_provider_cooldown",
             "model": "gpt-5.6-terra", "provider": "openai"},
            {"timestamp": "2026-08-26T10:00:03Z", "kind": "rate_limit_provider_cooldown",
             "model": "gpt-5.6-terra", "provider": "SENTINEL_PROVIDER"},
            {"timestamp": "2026-08-26T10:00:04Z", "kind": "rate_limit_chain_exhausted",
             "model": "gpt-5.6-sol", "models_considered": 2, "retry_after": 30},
            {"timestamp": "2026-08-26T10:00:05Z", "kind": "rate_limit_chain_exhausted",
             "model": "gpt-5.6-sol", "models_considered": 2, "retry_after": 99999},
            {"timestamp": "2026-08-26T10:00:06Z",
             "kind": "anthropic_rate_limit_passthrough",
             "model": "claude-opus-5", "provider": "anthropic", "status": 429},
        ]))
        self.assertEqual(lines, [
            "Airlock router: hybrid-openai-root profile; root gpt-5.6-sol (openai)",
            "10:00:01Z - Skipped gpt-5.6-sol because its rate-limit cooldown is active.",
            "10:00:02Z - gpt-5.6-terra was the second openai model to hit a rate limit,"
            " so the whole subscription is cooling down.",
            # An unknown provider is never reflected into the line.
            "10:00:03Z - Router action: rate_limit_provider_cooldown.",
            "10:00:04Z - The failover chain for gpt-5.6-sol exhausted 2 models."
            " Retry in about 30s.",
            # Out of range, so the line drops the hint rather than printing it.
            "10:00:05Z - The failover chain for gpt-5.6-sol exhausted 2 models.",
            "10:00:06Z - claude-opus-5 hit an Anthropic rate limit; passed it to"
            " Claude Code unchanged instead of handing off.",
        ])

    def test_rejects_oversized_or_malformed_payloads(self) -> None:
        with self.assertRaises(ACCESS.AccessError):
            ACCESS.router_status_lines(self._payload([
                {"timestamp": "2026-08-26T10:00:01Z", "kind": "future_action_v1"},
            ] * (ACCESS.MAX_SESSION_DIAGNOSTIC_EVENTS + 1)))
        with self.assertRaises(ACCESS.AccessError):
            ACCESS.router_status_lines({"profile": "", "root_model": "gpt-5.6-sol",
                                        "root_provider": "openai", "events": []})


if __name__ == "__main__":
    unittest.main()
