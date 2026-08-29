#!/usr/bin/env python3
"""Unit tests for exact Windows launch environment and protected argv."""

from __future__ import annotations

import contextlib
import importlib.util
import io
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
SPEC = importlib.util.spec_from_file_location("airlock_hybrid_test", ROOT / "bin" / "airlock-hybrid.py")
assert SPEC and SPEC.loader
HYBRID = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HYBRID)

ROUTE_POLICY = {
    "routes": {
        "claude-opus-5": "anthropic",
        "gpt-5.6-luna": "openai",
        "gpt-5.6-sol": "openai",
    },
    "model_ids": ["claude-opus-5", "gpt-5.6-luna", "gpt-5.6-sol"],
    "agent_names": ["airlock-luna", "airlock-opus", "airlock-sol"],
    "extra_model_ids": [],
    "extra_agent_names": [],
    "discovery_model": "gpt-5.6-luna",
    "picker_models": {
        "fable": "gpt-5.6-sol",
        "opus": "gpt-5.6-sol",
        "sonnet": "gpt-5.6-sol",
        "haiku": "gpt-5.6-luna",
    },
}
ORP_ROOT_MODEL = "vendor/root-model"
ORP_ROUTE_POLICY = {
    "routes": {
        ORP_ROOT_MODEL: "openrouter",
        "vendor/extra-model": "openrouter",
    },
    "model_ids": [ORP_ROOT_MODEL, "vendor/extra-model"],
    "agent_names": ["airlock-or-root", "airlock-or-extra"],
    "extra_model_ids": ["vendor/extra-model"],
    "extra_agent_names": ["airlock-or-extra"],
    "discovery_model": ORP_ROOT_MODEL,
    "picker_models": {
        "fable": ORP_ROOT_MODEL,
        "opus": ORP_ROOT_MODEL,
        "sonnet": ORP_ROOT_MODEL,
        "haiku": ORP_ROOT_MODEL,
    },
}

POLICY_HELPER = ROOT / "bin" / "airlock_policy.py"
SNAPSHOT_PATH = ROOT / "tests" / "fixtures" / "session-snapshot.json"
SNAPSHOT_DIGEST = "a" * 64


def build_child_environment(*args, **kwargs):
    kwargs.setdefault("policy_helper", POLICY_HELPER)
    kwargs.setdefault("snapshot_path", SNAPSHOT_PATH)
    kwargs.setdefault("snapshot_digest", SNAPSHOT_DIGEST)
    return HYBRID.build_child_environment(*args, **kwargs)


class RouterStartReasonTests(unittest.TestCase):
    def test_only_the_routers_own_printable_reason_is_reported(self) -> None:
        self.assertEqual(
            HYBRID.router_start_reason(
                "traceback noise\nairlock-router: session policy snapshot is invalid\n"
            ),
            ": session policy snapshot is invalid",
        )
        self.assertEqual(HYBRID.router_start_reason(None), "")
        self.assertEqual(HYBRID.router_start_reason(""), "")
        self.assertEqual(HYBRID.router_start_reason("unexpected output"), "")
        self.assertEqual(
            HYBRID.router_start_reason("airlock-router: bad\x07reason"), ""
        )
        self.assertEqual(
            len(HYBRID.router_start_reason("airlock-router: " + "x" * 500)), 202
        )


class HybridLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        # A suite run inside an Airlock session can inherit armed Fast
        # transition credentials; launcher tests must start without them.
        patcher = patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(HYBRID.FAST_TRANSITION_CHANNEL_ENV, None)
        os.environ.pop(HYBRID.FAST_TRANSITION_NONCE_ENV, None)

    def test_hybrid_environment_uses_router_profile_depth_cap_and_allowlists(self) -> None:
        with patch.dict(os.environ, {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:18765",
            "ANTHROPIC_MODEL": "gpt-5.6-sol",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-5",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-5",
            "ANTHROPIC_DEFAULT_FABLE_MODEL": "gpt-5.6-sol",
            "ANTHROPIC_DEFAULT_FABLE_MODEL_NAME": "wrong inherited label",
            "ANTHROPIC_DEFAULT_FABLE_MODEL_DESCRIPTION": "wrong inherited description",
            "ANTHROPIC_DEFAULT_FABLE_MODEL_SUPPORTED_CAPABILITIES": "effort",
            "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "19",
            "CLAUDE_CODE_SUBAGENT_MODEL": "claude-haiku-4-5-20251001",
        }, clear=True):
            environment = build_child_environment(
                "hybrid-anthropic-root",
                "3",
                proxy_url="http://127.0.0.1:18765",
                root_model="claude-opus-5",
                root_name="Claude Opus 5",
                context_window="272000",
                route_policy=ROUTE_POLICY,
                router_url="http://127.0.0.1:28471",
            )
        self.assertEqual(environment["ANTHROPIC_BASE_URL"], "http://127.0.0.1:28471")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", environment)
        self.assertNotIn("ANTHROPIC_API_KEY", environment)
        self.assertNotIn("ANTHROPIC_MODEL", environment)
        self.assertEqual(environment["ANTHROPIC_DEFAULT_FABLE_MODEL"], "gpt-5.6-sol")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_OPUS_MODEL"], "gpt-5.6-sol")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_SONNET_MODEL"], "gpt-5.6-sol")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "gpt-5.6-luna")
        self.assertEqual(environment["ANTHROPIC_SMALL_FAST_MODEL"], "gpt-5.6-luna")
        for family in ("FABLE", "OPUS", "SONNET", "HAIKU"):
            variable = f"ANTHROPIC_DEFAULT_{family}_MODEL"
            self.assertEqual(environment[f"{variable}_NAME"], environment[variable])
            self.assertIn("Airlock exact route", environment[f"{variable}_DESCRIPTION"])
            self.assertEqual(
                environment[f"{variable}_SUPPORTED_CAPABILITIES"],
                "effort,xhigh_effort,max_effort",
            )
        self.assertNotEqual(
            environment["ANTHROPIC_DEFAULT_FABLE_MODEL_NAME"], "wrong inherited label"
        )
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", environment)
        self.assertEqual(environment["AIRLOCK_ACTIVE_PROFILE"], "hybrid-anthropic-root")
        self.assertEqual(
            environment["AIRLOCK_ACCESS_HELPER"],
            str((ROOT / "bin" / "airlock-access.py").resolve()),
        )
        self.assertEqual(environment["AIRLOCK_PYTHON"], sys.executable)
        self.assertEqual(environment["AIRLOCK_POLICY_HELPER"], str(POLICY_HELPER))
        self.assertEqual(environment["AIRLOCK_SESSION_SNAPSHOT"], str(SNAPSHOT_PATH))
        self.assertEqual(
            environment["AIRLOCK_SESSION_SNAPSHOT_SHA256"], SNAPSHOT_DIGEST
        )
        self.assertEqual(environment["AIRLOCK_SESSION_ROUTER_URL"], "http://127.0.0.1:28471")
        self.assertEqual(environment["AIRLOCK_ROOT_MODEL"], "claude-opus-5")
        self.assertEqual(environment["AIRLOCK_DISCOVERY_MODEL"], "gpt-5.6-luna")
        self.assertEqual(environment["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"], "3")
        self.assertEqual(environment["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"], "1")
        self.assertEqual(
            environment["AIRLOCK_ALLOWED_AGENT_MODELS"],
            "claude-opus-5,gpt-5.6-luna,gpt-5.6-sol",
        )
        self.assertEqual(
            environment["AIRLOCK_ALLOWED_AGENT_NAMES"],
            "airlock-luna,airlock-opus,airlock-sol",
        )

    def build(
        self,
        profile: str,
        root_model: str,
        *,
        context_window: str = "272000",
        preset_environment: dict[str, str] | None = None,
        force_context_window: bool = False,
    ) -> dict[str, str]:
        """Build a child environment for one profile with the shared defaults."""
        base = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:18765"}
        if preset_environment:
            base.update(preset_environment)
        with patch.dict(os.environ, base, clear=True):
            return build_child_environment(
                profile,
                "3",
                proxy_url="http://127.0.0.1:18765",
                root_model=root_model,
                root_name="Root",
                context_window=context_window,
                route_policy=ROUTE_POLICY,
                router_url="http://127.0.0.1:28471",
                force_context_window=force_context_window,
            )

    def test_anthropic_root_keeps_claude_codes_own_auto_compact_window(self) -> None:
        # Claude Code resolves this variable ahead of its own per-model tuning and
        # locks the /config control while it is set, so an Anthropic root must not
        # receive it at all.
        environment = self.build("hybrid-anthropic-root", "claude-opus-5")
        self.assertNotIn("CLAUDE_CODE_AUTO_COMPACT_WINDOW", environment)

    def test_bare_sol_root_keeps_the_conservative_window(self) -> None:
        environment = self.build("hybrid-openai-root", "gpt-5.6-sol")
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", environment)
        self.assertEqual(environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "272000")

    def test_explicit_airlock_window_can_override_a_bare_sol_root(self) -> None:
        environment = self.build(
            "hybrid-openai-root",
            "gpt-5.6-sol",
            context_window="450000",
            force_context_window=True,
        )
        self.assertEqual(environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "450000")

    def test_grok_root_keeps_the_conservative_window(self) -> None:
        # No shipped root declares a hard limit; the flagship Grok route keeps
        # the conservative saved fallback like every other custom root.
        environment = self.build("hybrid-grok-root", "grok-4.6")
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", environment)
        self.assertEqual(environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "272000")

    def test_composer_root_keeps_the_conservative_auto_compact_window(self) -> None:
        environment = self.build("hybrid-grok-root", "grok-composer-2.5-fast")
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", environment)
        self.assertEqual(environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "272000")

    def test_user_compact_window_still_wins_on_the_grok_flagship(self) -> None:
        environment = self.build(
            "hybrid-grok-root",
            "grok-4.6",
            preset_environment={"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "450000"},
        )
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", environment)
        self.assertEqual(environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "450000")

    def test_auto_context_window_never_sets_the_variable(self) -> None:
        environment = self.build(
            "hybrid-openai-root", "gpt-5.6-sol", context_window="auto"
        )
        self.assertNotIn("CLAUDE_CODE_AUTO_COMPACT_WINDOW", environment)

    def test_a_window_the_user_set_is_preserved_on_every_root(self) -> None:
        for profile, root_model in (
            ("hybrid-anthropic-root", "claude-opus-5"),
            ("hybrid-openai-root", "gpt-5.6-sol"),
        ):
            with self.subTest(profile=profile):
                environment = self.build(
                    profile,
                    root_model,
                    preset_environment={"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "450000"},
                )
                self.assertEqual(
                    environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "450000"
                )

    def test_only_windows_claude_code_honours_are_accepted(self) -> None:
        # The bridge is a second gate behind two launchers, so its shape rule has
        # to match theirs exactly rather than merely being close.
        for accepted in ("auto", "100000", "272000", "999999", "1000000"):
            with self.subTest(accepted=accepted):
                self.assertTrue(HYBRID.context_window_is_valid(accepted))
        for rejected in (
            "",
            "auto ",
            "99999",
            "1000001",
            "2000000",
            "0272000",
            "+272000",
            " 272000 ",
            "272000\n",
            "notanumber",
            # str.isdigit is true here, but int() raises on it.
            "²²²²²²",
            # Arabic-Indic digits parse as 272000 but are not what anyone typed.
            "٢٧٢٠٠٠",
        ):
            with self.subTest(rejected=rejected):
                self.assertFalse(HYBRID.context_window_is_valid(rejected))

    def test_effort_capabilities_are_declared_for_gpt_roots_only(self) -> None:
        with patch.dict(os.environ, {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:18765",
            "ANTHROPIC_MODEL": "gpt-5.6-sol",
        }, clear=True):
            direct = build_child_environment(
                "openai-pure",
                "off",
                proxy_url="http://127.0.0.1:18765",
                root_model="gpt-5.6-sol",
                root_name="GPT-5.6 Sol",
                context_window="272000",
                route_policy=ROUTE_POLICY,
                router_url=None,
            )
        self.assertEqual(
            direct["ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES"],
            "effort,xhigh_effort,max_effort",
        )
        self.assertEqual(
            direct["ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES"],
            "effort,xhigh_effort,max_effort",
        )

        with patch.dict(os.environ, {
            "AIRLOCK_GPT_EFFORT_CAPABILITIES": "effort,max_effort",
        }, clear=True):
            gpt_root = build_child_environment(
                "hybrid-openai-root",
                "off",
                proxy_url="http://127.0.0.1:18765",
                root_model="gpt-5.6-sol",
                root_name="GPT-5.6 Sol",
                context_window="272000",
                route_policy=ROUTE_POLICY,
                router_url="http://127.0.0.1:28471",
            )
        self.assertEqual(
            gpt_root["ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES"],
            "effort,max_effort",
        )

        with patch.dict(os.environ, {
            "ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES": "effort",
        }, clear=True):
            claude_root = build_child_environment(
                "hybrid-anthropic-root",
                "off",
                proxy_url="http://127.0.0.1:18765",
                root_model="claude-opus-5",
                root_name="Claude Opus 5",
                context_window="272000",
                route_policy=ROUTE_POLICY,
                router_url="http://127.0.0.1:28471",
            )
        self.assertNotIn(
            "ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES", claude_root
        )

    def test_hybrid_environment_rejects_explicit_api_credentials(self) -> None:
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "synthetic"}, clear=True):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                build_child_environment(
                    "hybrid-openai-root",
                    "off",
                    proxy_url="http://127.0.0.1:18765",
                    root_model="gpt-5.6-sol",
                    root_name="GPT-5.6 Sol",
                    context_window="272000",
                    route_policy=ROUTE_POLICY,
                    router_url="http://127.0.0.1:28471",
                )

    def test_hybrid_environment_keeps_openrouter_key_router_only(self) -> None:
        with patch.dict(
            os.environ, {"OPENROUTER_API_KEY": "synthetic-openrouter"}, clear=True
        ):
            environment = build_child_environment(
                "hybrid-openai-root",
                "off",
                proxy_url="http://127.0.0.1:18765",
                root_model="gpt-5.6-sol",
                root_name="GPT-5.6 Sol",
                context_window="272000",
                route_policy=ROUTE_POLICY,
                router_url="http://127.0.0.1:28471",
            )
        self.assertNotIn("OPENROUTER_API_KEY", environment)

    def test_openrouter_environment_is_router_only_and_credential_free(self) -> None:
        inherited = {
            "ANTHROPIC_API_KEY": "synthetic-anthropic",
            "ANTHROPIC_AUTH_TOKEN": "synthetic-token",
            "CLAUDE_CODE_OAUTH_TOKEN": "synthetic-oauth",
            "OPENAI_API_KEY": "synthetic-openai",
            "CODEX_API_KEY": "synthetic-codex",
            "XAI_API_KEY": "synthetic-xai",
            "GROK_API_KEY": "synthetic-grok",
            "OPENROUTER_API_KEY": "synthetic-openrouter",
            "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT": "1",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "450000",
            "ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES": "effort",
            "AIRLOCK_HYBRID": "1",
        }
        with patch.dict(os.environ, inherited, clear=True):
            environment = build_child_environment(
                "openrouter-pure",
                "off",
                proxy_url=None,
                root_model=ORP_ROOT_MODEL,
                root_name="OpenRouter root",
                context_window="272000",
                route_policy=ORP_ROUTE_POLICY,
                router_url="http://127.0.0.1:28471",
            )
        for variable in HYBRID.ORP_CREDENTIAL_VARIABLES | {"ANTHROPIC_API_KEY"}:
            self.assertNotIn(variable, environment)
        self.assertEqual(environment["ANTHROPIC_AUTH_TOKEN"], "unused")
        self.assertEqual(environment["ANTHROPIC_BASE_URL"], "http://127.0.0.1:28471")
        self.assertEqual(environment["AIRLOCK_SESSION_ROUTER_URL"], "http://127.0.0.1:28471")
        self.assertEqual(environment["ANTHROPIC_MODEL"], ORP_ROOT_MODEL)
        self.assertEqual(environment["ANTHROPIC_SMALL_FAST_MODEL"], ORP_ROOT_MODEL)
        self.assertEqual(environment["AIRLOCK_DISCOVERY_MODEL"], ORP_ROOT_MODEL)
        for family in ("FABLE", "OPUS", "SONNET", "HAIKU"):
            variable = f"ANTHROPIC_DEFAULT_{family}_MODEL"
            self.assertEqual(environment[variable], ORP_ROOT_MODEL)
            self.assertEqual(
                environment[f"{variable}_SUPPORTED_CAPABILITIES"],
                HYBRID.DEFAULT_GPT_EFFORT_CAPABILITIES,
            )
        self.assertEqual(
            environment["ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES"],
            HYBRID.DEFAULT_GPT_EFFORT_CAPABILITIES,
        )
        self.assertEqual(environment["CLAUDE_CODE_ALWAYS_ENABLE_EFFORT"], "1")
        # Auto mode's classifier hard-codes Claude Sonnet 5, which this route
        # cannot serve. The route serves only its root, so that root is also
        # the cheapest available classifier target.
        self.assertEqual(environment["CLAUDE_CODE_AUTO_MODE_MODEL"], ORP_ROOT_MODEL)
        self.assertNotIn("CLAUDE_CODE_AUTO_COMPACT_WINDOW", environment)
        self.assertNotIn("AIRLOCK_HYBRID", environment)
        self.assertEqual(environment["AIRLOCK_EXTRA_USAGE_AGENT_NAMES"], "airlock-or-extra")

    def test_openrouter_auto_mode_model_knob(self) -> None:
        common = dict(
            proxy_url=None,
            root_model=ORP_ROOT_MODEL,
            root_name="OpenRouter root",
            context_window="272000",
            route_policy=ORP_ROUTE_POLICY,
            router_url="http://127.0.0.1:28471",
        )
        cases = [
            ({}, ORP_ROOT_MODEL),
            ({"AIRLOCK_AUTO_MODE_MODEL": ""}, ORP_ROOT_MODEL),
            ({"AIRLOCK_AUTO_MODE_MODEL": "gpt-5.6-sol"}, "gpt-5.6-sol"),
            ({"AIRLOCK_AUTO_MODE_MODEL": "off"}, None),
            ({"AIRLOCK_AUTO_MODE_MODEL": "none"}, None),
        ]
        for inherited, expected in cases:
            with self.subTest(inherited=sorted(inherited)):
                with patch.dict(os.environ, inherited, clear=True):
                    environment = build_child_environment(
                        "openrouter-pure", "off", **common
                    )
                if expected is None:
                    self.assertNotIn("CLAUDE_CODE_AUTO_MODE_MODEL", environment)
                else:
                    self.assertEqual(environment["CLAUDE_CODE_AUTO_MODE_MODEL"], expected)

    def test_auto_mode_classifier_prefers_the_small_seat_over_the_root(self) -> None:
        # A GPT-rooted hybrid serves the economical Luna model through the
        # small fast seat. Classifications are frequent background work, so
        # they must ride that seat instead of burning the premium root.
        environment = self.build("hybrid-openai-root", "gpt-5.6-sol")
        self.assertEqual(environment["ANTHROPIC_SMALL_FAST_MODEL"], "gpt-5.6-luna")
        self.assertEqual(environment["CLAUDE_CODE_AUTO_MODE_MODEL"], "gpt-5.6-luna")

    def test_auto_mode_classifier_stays_stock_for_an_anthropic_root(self) -> None:
        # The stock classifier target is Claude Sonnet 5, which an Anthropic
        # route serves natively, so no override is needed or wanted.
        environment = self.build("hybrid-anthropic-root", "claude-opus-5")
        self.assertNotIn("CLAUDE_CODE_AUTO_MODE_MODEL", environment)

    def test_auto_mode_classifier_falls_back_to_discovery_then_root(self) -> None:
        # The launcher helpers normally seat a small fast model before this
        # runs. The chain still has to hold when a route serves nothing
        # smaller than its root.
        cases = [
            (
                {"ANTHROPIC_SMALL_FAST_MODEL": "gpt-5.6-luna"},
                "gpt-5.6-luna",
            ),
            (
                {"ANTHROPIC_DEFAULT_HAIKU_MODEL": "grok-composer-2.5-fast"},
                "grok-composer-2.5-fast",
            ),
            ({"AIRLOCK_DISCOVERY_MODEL": "gpt-5.6-luna"}, "gpt-5.6-luna"),
            ({}, "vendor/root-model"),
        ]
        for inherited, expected in cases:
            with self.subTest(inherited=sorted(inherited)):
                environment = dict(inherited)
                HYBRID.apply_auto_mode_model(environment, "vendor/root-model")
                self.assertEqual(environment["CLAUDE_CODE_AUTO_MODE_MODEL"], expected)

    def test_openrouter_environment_rejects_proxy_upstream(self) -> None:
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_child_environment(
                "openrouter-pure",
                "off",
                proxy_url="http://127.0.0.1:18765",
                root_model=ORP_ROOT_MODEL,
                root_name="OpenRouter root",
                context_window="272000",
                route_policy=ORP_ROUTE_POLICY,
                router_url="http://127.0.0.1:28471",
            )

    def test_openrouter_model_overrides_are_rejected(self) -> None:
        for arguments in (["--model", ORP_ROOT_MODEL], ["-m", ORP_ROOT_MODEL], [f"--model={ORP_ROOT_MODEL}"]):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                HYBRID.reject_openrouter_model_overrides(arguments)
        HYBRID.reject_openrouter_model_overrides(["-r", "--effort", "high"])

    def test_openrouter_main_derives_root_from_one_loaded_policy(self) -> None:
        policy = object()
        artifact_access = HYBRID.load_access_module()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        runtime = Path(temporary.name) / "session runtime"
        root_entry = type(
            "RootEntry",
            (),
            {"route": "root", "model": ORP_ROOT_MODEL},
        )()

        class Access:
            def __init__(self) -> None:
                self.load_calls = 0
                self.deleted = False
                self.artifacts_deleted: list[Path] = []

            def load_or_refresh_policy(self):
                self.load_calls += 1
                return policy

            def resolve_openrouter_route(self, received_policy, route):
                self.assert_policy(received_policy)
                self.route = route
                return root_entry

            def session_route_policy(self, received_policy, profile, **kwargs):
                self.assert_policy(received_policy)
                self.session_call = (profile, kwargs)
                return ORP_ROUTE_POLICY

            def profile_guidance(self, received_policy, profile, **kwargs):
                self.assert_policy(received_policy)
                self.guidance_call = (profile, kwargs)
                return "ORP guidance"

            def write_session_artifact(self, content, prefix, suffix):
                return artifact_access.write_session_artifact(
                    content, prefix, suffix, runtime
                )

            def write_web_tools_mcp_config(self, script, profile=None):
                return ""

            def delete_session_artifact(self, path, digest, size):
                artifact_access.delete_session_artifact(
                    path, digest, size, runtime
                )
                self.artifacts_deleted.append(path)

            def write_session_snapshot(self, received_policy, profile, root_model, **kwargs):
                self.assert_policy(received_policy)
                self.snapshot_call = (profile, root_model, kwargs)
                return SNAPSHOT_PATH, SNAPSHOT_DIGEST

            def delete_session_snapshot(self, path, digest):
                self.deleted = path == SNAPSHOT_PATH and digest == SNAPSHOT_DIGEST

            @staticmethod
            def assert_policy(received_policy):
                if received_policy is not policy:
                    raise AssertionError("bridge reloaded or changed the validated policy")

        access = Access()
        request = {
            "profile": "openrouter-pure",
            "openrouter_root_route": "root",
            "plugin_dir": str(ROOT / "plugins" / "airlock"),
            "max_agents": "off",
            "context_window": "272000",
            "fast_mode": "off",
            "claude": sys.executable,
            "catalog_files": {},
            "router_helper": str(ROOT / "bin" / "airlock-router.py"),
            "args": ["-r"],
        }
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        captured: dict[str, object] = {}

        def launch_claude(command, **kwargs):
            settings_path = Path(command[command.index("--settings") + 1])
            guidance_path = Path(
                command[command.index("--append-system-prompt-file") + 1]
            )
            captured["command"] = list(command)
            captured["settings_path"] = settings_path
            captured["guidance_path"] = guidance_path
            captured["settings"] = settings_path.read_text(encoding="utf-8")
            captured["guidance"] = guidance_path.read_text(encoding="utf-8")
            return completed

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(HYBRID, "resolve_managed_request", return_value=Path("request.json")))
            stack.enter_context(patch.object(HYBRID, "read_request", return_value=request))
            stack.enter_context(patch.object(HYBRID, "validate_plugin_path", return_value=ROOT / "plugins" / "airlock"))
            stack.enter_context(patch.object(HYBRID, "load_access_module", return_value=access))
            render = stack.enter_context(patch.object(HYBRID, "render_agents", return_value='{"airlock-or-root":{}}'))
            stack.enter_context(patch.object(HYBRID, "managed_agent_permissions", return_value=(ORP_ROUTE_POLICY["agent_names"], ["Agent(airlock-or-root)", "Agent(airlock-or-extra)"])))
            stack.enter_context(patch.object(HYBRID, "managed_session_settings", return_value="{}"))
            stack.enter_context(patch.object(HYBRID, "validate_policy_helper_path", return_value=POLICY_HELPER))
            stack.enter_context(patch.object(HYBRID, "validate_router_path", return_value=ROOT / "bin" / "airlock-router.py"))
            start_router = stack.enter_context(patch.object(HYBRID, "start_native_router", return_value="http://127.0.0.1:28471"))
            build_environment = stack.enter_context(patch.object(HYBRID, "build_child_environment", return_value={}))
            run_claude = stack.enter_context(patch.object(HYBRID.subprocess, "run", side_effect=launch_claude))
            stack.enter_context(patch.object(sys, "argv", ["airlock-hybrid.py", "--request-file", "request.json"]))
            self.assertEqual(HYBRID.main(), 0)

        self.assertEqual(access.load_calls, 1)
        self.assertEqual(access.route, "root")
        self.assertEqual(access.session_call, ("openrouter-pure", {"openrouter_root_route": "root"}))
        self.assertEqual(access.guidance_call, ("openrouter-pure", {"openrouter_root_route": "root"}))
        self.assertEqual(
            access.snapshot_call,
            ("openrouter-pure", ORP_ROOT_MODEL, {"openrouter_root_route": "root"}),
        )
        self.assertTrue(access.deleted)
        self.assertEqual(len(access.artifacts_deleted), 2)
        self.assertEqual(captured["settings"], "{}")
        self.assertEqual(captured["guidance"], "ORP guidance")
        self.assertFalse(Path(captured["settings_path"]).exists())
        self.assertFalse(Path(captured["guidance_path"]).exists())
        self.assertEqual(render.call_args.kwargs["openrouter_root_route"], "root")
        self.assertIsNone(start_router.call_args.args[3])
        self.assertEqual(build_environment.call_args.kwargs["root_model"], ORP_ROOT_MODEL)
        self.assertEqual(build_environment.call_args.kwargs["root_name"], "OpenRouter root")
        self.assertEqual(build_environment.call_args.kwargs["proxy_url"], None)
        command = run_claude.call_args.args[0]
        self.assertEqual(command[command.index("--settings") + 1], str(captured["settings_path"]))
        self.assertEqual(command[command.index("--agents") + 1], '{"airlock-or-root":{}}')
        self.assertNotIn("--append-system-prompt", command)
        self.assertEqual(command.count("--append-system-prompt-file"), 1)
        self.assertEqual(command.count("--model"), 1)
        self.assertEqual(command[command.index("--model") + 1], ORP_ROOT_MODEL)
        self.assertIn("-r", command)

    def test_launch_hands_web_tools_to_mcp_config_and_cleans_up(self) -> None:
        artifact_access = HYBRID.load_access_module()
        request = {
            "profile": "openai-pure",
            "plugin_dir": str(ROOT / "plugins" / "airlock"),
            "max_agents": "off",
            "proxy_url": "http://127.0.0.1:18765",
            "root_model": "gpt-5.6-sol",
            "root_name": "GPT-5.6 Sol",
            "context_window": "272000",
            "fast_mode": "off",
            "claude": sys.executable,
            "catalog_files": {},
            "args": [],
        }

        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"

            class Access:
                def __init__(self) -> None:
                    self.deleted_artifacts: list[Path] = []
                    self.mcp_calls: list[tuple[str, str | None]] = []

                @staticmethod
                def load_or_refresh_policy():
                    return object()

                @staticmethod
                def session_route_policy(received_policy, profile, **kwargs):
                    return ROUTE_POLICY

                @staticmethod
                def profile_guidance(received_policy, profile, **kwargs):
                    return "routing guidance"

                @staticmethod
                def write_session_artifact(content, prefix, suffix):
                    return artifact_access.write_session_artifact(
                        content, prefix, suffix, runtime
                    )

                def write_web_tools_mcp_config(self, script, profile=None):
                    self.mcp_calls.append((script, profile))
                    payload = json.dumps(
                        {"mcpServers": {"airlock-web-tools": {
                            "command": sys.executable,
                            "args": [script],
                        }}}
                    ).encode("utf-8")
                    path, digest, size = artifact_access.write_session_artifact(
                        payload, "airlock-mcp-", ".json", runtime
                    )
                    return f"{path}\t{digest}\t{size}"

                def delete_session_artifact(self, path, digest, size):
                    artifact_access.delete_session_artifact(
                        path, digest, size, runtime
                    )
                    self.deleted_artifacts.append(path)

                @staticmethod
                def delete_session_snapshot(path, digest):
                    artifact_access.delete_session_snapshot(path, digest)

                @staticmethod
                def write_session_snapshot(*args, **kwargs):
                    runtime.mkdir(parents=True, exist_ok=True)
                    path = runtime / "snapshot-openai-pure.json"
                    path.write_text("{}", encoding="utf-8")
                    return path, SNAPSHOT_DIGEST

            access = Access()
            captured: dict[str, object] = {}

            completed = subprocess.CompletedProcess(args=[], returncode=0)

            def launch_claude(command, **kwargs):
                captured["command"] = list(command)
                index = command.index("--mcp-config")
                mcp_path = Path(command[index + 1])
                captured["mcp_path"] = mcp_path
                captured["mcp_content"] = mcp_path.read_text(encoding="utf-8")
                return completed

            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(HYBRID, "resolve_managed_request", return_value=Path("request.json")))
                stack.enter_context(patch.object(HYBRID, "read_request", return_value=request))
                stack.enter_context(patch.object(HYBRID, "validate_plugin_path", return_value=ROOT / "plugins" / "airlock"))
                stack.enter_context(patch.object(HYBRID, "load_access_module", return_value=access))
                stack.enter_context(patch.object(HYBRID, "render_agents", return_value='{"airlock-sol":{}}'))
                stack.enter_context(patch.object(HYBRID, "managed_agent_permissions", return_value=(ROUTE_POLICY["agent_names"], ["Agent(airlock-sol)"])))
                stack.enter_context(patch.object(HYBRID, "managed_session_settings", return_value="{}"))
                stack.enter_context(patch.object(HYBRID, "validate_policy_helper_path", return_value=POLICY_HELPER))
                stack.enter_context(patch.object(HYBRID, "build_child_environment", return_value={}))
                run_claude = stack.enter_context(patch.object(HYBRID.subprocess, "run", side_effect=launch_claude))
                stack.enter_context(patch.object(sys, "argv", ["airlock-hybrid.py", "--request-file", "request.json"]))
                self.assertEqual(HYBRID.main(), 0)

            expected_script = str(
                ROOT / "plugins" / "airlock" / "mcp-server" / "airlock_web_tools.py"
            )
            self.assertEqual(access.mcp_calls, [(expected_script, "openai-pure")])
            command = run_claude.call_args.args[0]
            self.assertEqual(command.count("--mcp-config"), 1)
            self.assertEqual(
                command[command.index("--mcp-config") + 1],
                str(captured["mcp_path"]),
            )
            self.assertIn('"airlock-web-tools"', captured["mcp_content"])
            # All three managed artifacts are removed after the child exits.
            self.assertEqual(len(access.deleted_artifacts), 3)
            self.assertFalse(Path(captured["mcp_path"]).exists())

    def test_plain_openai_stays_direct_and_off_removes_only_cap(self) -> None:
        with patch.dict(os.environ, {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:18765",
            "ANTHROPIC_AUTH_TOKEN": "unused",
            "ANTHROPIC_MODEL": "gpt-5.6-sol",
            "AIRLOCK_SESSION_ROUTER_URL": "http://127.0.0.1:9999",
            "ANTHROPIC_DEFAULT_FABLE_MODEL": "claude-fable-5",
            "ANTHROPIC_DEFAULT_FABLE_MODEL_NAME": "wrong inherited label",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION": "wrong inherited description",
            "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "19",
            "CLAUDE_CODE_SUBAGENT_MODEL": "claude-haiku-4-5-20251001",
        }, clear=True):
            environment = build_child_environment(
                "openai-pure",
                "off",
                proxy_url="http://127.0.0.1:18765",
                root_model="gpt-5.6-sol",
                root_name="GPT-5.6 Sol",
                context_window="272000",
                route_policy=ROUTE_POLICY,
                router_url=None,
            )
        self.assertNotIn("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", environment)
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", environment)
        self.assertEqual(environment["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"], "1")
        self.assertEqual(environment["ANTHROPIC_BASE_URL"], "http://127.0.0.1:18765")
        self.assertNotIn("AIRLOCK_SESSION_ROUTER_URL", environment)
        self.assertEqual(environment["AIRLOCK_POLICY_HELPER"], str(POLICY_HELPER))
        self.assertEqual(environment["AIRLOCK_SESSION_SNAPSHOT"], str(SNAPSHOT_PATH))
        self.assertEqual(
            environment["AIRLOCK_SESSION_SNAPSHOT_SHA256"], SNAPSHOT_DIGEST
        )
        self.assertEqual(environment["ANTHROPIC_AUTH_TOKEN"], "unused")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_FABLE_MODEL"], "gpt-5.6-sol")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_OPUS_MODEL"], "gpt-5.6-sol")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_SONNET_MODEL"], "gpt-5.6-sol")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "gpt-5.6-luna")
        self.assertEqual(environment["ANTHROPIC_SMALL_FAST_MODEL"], "gpt-5.6-luna")
        for family in ("FABLE", "OPUS", "SONNET", "HAIKU"):
            variable = f"ANTHROPIC_DEFAULT_{family}_MODEL"
            self.assertEqual(environment[f"{variable}_NAME"], environment[variable])
            self.assertIn("Airlock exact route", environment[f"{variable}_DESCRIPTION"])
            self.assertEqual(
                environment[f"{variable}_SUPPORTED_CAPABILITIES"],
                "effort,xhigh_effort,max_effort",
            )

    def test_router_start_uses_snapshot_digest_and_parent(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="http://127.0.0.1:28471\n", stderr=""
        )
        with patch.object(HYBRID.subprocess, "run", return_value=completed) as run:
            address = HYBRID.start_native_router(
                ROOT / "bin" / "airlock-router.py",
                SNAPSHOT_PATH,
                SNAPSHOT_DIGEST,
                "http://127.0.0.1:18765",
            )
        self.assertEqual(address, "http://127.0.0.1:28471")
        command = run.call_args.args[0]
        self.assertIn("--parent-pid", command)
        self.assertIn(str(os.getpid()), command)
        self.assertEqual(
            command[command.index("--snapshot") + 1], str(SNAPSHOT_PATH)
        )
        self.assertEqual(
            command[command.index("--snapshot-sha256") + 1], SNAPSHOT_DIGEST
        )
        self.assertNotIn("--routes-json", command)
        self.assertEqual(
            command[command.index("--openai-url") + 1],
            "http://127.0.0.1:18765",
        )

    def test_openrouter_router_start_omits_subscription_upstream(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="http://127.0.0.1:28471\n", stderr=""
        )
        with patch.object(HYBRID.subprocess, "run", return_value=completed) as run:
            address = HYBRID.start_native_router(
                ROOT / "bin" / "airlock-router.py",
                SNAPSHOT_PATH,
                SNAPSHOT_DIGEST,
                None,
            )
        self.assertEqual(address, "http://127.0.0.1:28471")
        self.assertNotIn("--openai-url", run.call_args.args[0])

    def test_exact_native_agent_permissions_are_sorted_and_narrow(self) -> None:
        access = HYBRID.load_access_module()
        policy = access.default_policy()
        agents_json = '{"airlock-sonnet":{},"airlock-luna":{},"airlock-sol":{}}'
        names, rules = HYBRID.managed_agent_permissions(
            access, agents_json, policy
        )
        self.assertEqual(names, ["airlock-luna", "airlock-sol", "airlock-sonnet"])
        self.assertEqual(rules, [
            "Agent(Explore)",
            "Agent(Plan)",
            "Agent(general-purpose)",
            "Agent(airlock-luna)",
            "Agent(airlock-sol)",
            "Agent(airlock-sonnet)",
        ])
        self.assertNotIn("Agent", rules)
        self.assertNotIn("Agent(*)", rules)
        self.assertNotIn("Bash(airlock-delegate *)", rules)
        self.assertNotIn("Bash(airlock-workflow *)", rules)
        settings = json.loads(
            HYBRID.managed_session_settings(
                access, agents_json, "inherit", policy
            )
        )
        self.assertEqual(settings["autoMode"]["environment"][0], "$defaults")
        self.assertNotIn("fastMode", settings)
        context = settings["autoMode"]["environment"][1]
        self.assertIn("OpenAI models", context)
        self.assertIn("Anthropic Claude", context)
        self.assertIn("eligible non-ignored untracked regular files", context)
        self.assertIn("built-in Explore, Plan, and general-purpose Agent types", context)
        self.assertIn("Git-ignored or unsafe paths", context)
        self.assertTrue(
            json.loads(
                HYBRID.managed_session_settings(
                    access, agents_json, "on", policy
                )
            )[
                "fastMode"
            ]
        )
        self.assertFalse(
            json.loads(
                HYBRID.managed_session_settings(
                    access, agents_json, "off", policy
                )
            )[
                "fastMode"
            ]
        )
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            HYBRID.managed_agent_permissions(
                access, '{"Explore":{}}', policy
            )
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            HYBRID.managed_session_settings(
                access, '{"Explore":{}}', "off", policy
            )
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            HYBRID.managed_session_settings(
                access, agents_json, "invalid", policy
            )

    def test_managed_session_settings_validate_the_web_tools_matrix(self) -> None:
        access = HYBRID.load_access_module()
        policy = access.default_policy()
        agents_json = '{"airlock-luna":{}}'
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / "airlock_web_tools.py"
            script.write_text("# server\n", encoding="utf-8")

            pure = json.loads(HYBRID.managed_session_settings(
                access, agents_json, "inherit", policy,
                web_tools_script=str(script), profile="grok-pure",
            ))
            self.assertEqual(pure["permissions"], {
                "allow": [
                    "mcp__airlock-web-tools__web_search",
                    "mcp__airlock-web-tools__fetch_page",
                ],
                "deny": ["WebSearch", "WebFetch"],
            })
            self.assertEqual(
                pure["mcpServers"]["airlock-web-tools"]["args"],
                [str(Path(script).resolve())],
            )

            hybrid = json.loads(HYBRID.managed_session_settings(
                access, agents_json, "inherit", policy,
                web_tools_script=str(script), profile="hybrid-grok-root",
            ))
            self.assertEqual(hybrid["permissions"], {
                "allow": [
                    "mcp__airlock-web-tools__web_search",
                    "mcp__airlock-web-tools__fetch_page",
                ],
                "deny": ["WebSearch"],
            })

            anthropic = json.loads(HYBRID.managed_session_settings(
                access, agents_json, "inherit", policy,
                web_tools_script=str(script), profile="hybrid-anthropic-root",
            ))
            self.assertNotIn("permissions", anthropic)
            self.assertNotIn("mcpServers", anthropic)

            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                HYBRID.managed_session_settings(
                    access, agents_json, "inherit", policy,
                    web_tools_script=str(script), profile="not-a-profile",
                )

    def test_openrouter_permissions_use_the_loaded_policy(self) -> None:
        access = HYBRID.load_access_module()
        policy = access.default_policy()
        policy["_openrouter_registry"] = (
            access.POLICY_SCHEMA.validate_openrouter_registry({
                "schema_version": 1,
                "models": [{
                    "route": "resume-test",
                    "model": "vendor/model-test",
                    "endpoint_provider": "digitalocean",
                    "provider_name": "DigitalOcean",
                    "provider_slug": "digitalocean",
                    "quantization": "unknown",
                    "canonical_slug": "vendor/model-test-20260810",
                    "alias_target": None,
                    "supported_parameters": ["tool_choice", "tools"],
                    "expiration_date": None,
                    "checked_at": int(time.time()),
                    "enabled": True,
                }],
            })
        )
        agents_json = '{"airlock-or-resume-test":{}}'
        names, rules = HYBRID.managed_agent_permissions(
            access, agents_json, policy
        )
        self.assertEqual(names, ["airlock-or-resume-test"])
        self.assertIn("Agent(airlock-or-resume-test)", rules)
        settings = json.loads(
            HYBRID.managed_session_settings(
                access, agents_json, "inherit", policy
            )
        )
        context = settings["autoMode"]["environment"][1]
        self.assertIn("user-declared OpenRouter models", context)
        self.assertEqual(HYBRID.validate_child_args(["-r"]), ["-r"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            HYBRID.managed_agent_permissions(
                access, '{"airlock-or-undeclared":{}}', policy
            )

    def test_combine_routing_guidance_preserves_custom_prompt_and_order(self) -> None:
        self.assertEqual(
            HYBRID.combine_routing_guidance(["--model", "root"], "native"),
            (["--model", "root"], "native"),
        )
        separate = HYBRID.combine_routing_guidance(
            ["--model", "root", "--append-system-prompt", "user", "task"], "native",
        )
        self.assertEqual(separate, (["--model", "root", "task"], "user\n\nnative"))
        equals = HYBRID.combine_routing_guidance(
            ["--append-system-prompt=first", "--append-system-prompt", "second"], "native",
        )
        self.assertEqual(equals, ([], "first\n\nsecond\n\nnative"))
        empty = HYBRID.combine_routing_guidance(
            ["--append-system-prompt=", "task"], "native"
        )
        self.assertEqual(empty, (["task"], "native"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            HYBRID.combine_routing_guidance(["--append-system-prompt"], "native")

    def test_windows_command_line_preflight_counts_exact_utf16_units(self) -> None:
        self.assertEqual(HYBRID.windows_command_line_units(["a b"]), 6)
        self.assertEqual(HYBRID.windows_command_line_units(["a😀b"]), 5)
        HYBRID.validate_windows_command_line(
            ["x" * (HYBRID.WINDOWS_COMMAND_LINE_MAX_UNITS - 1)]
        )

        stderr = io.StringIO()
        private_marker = "private-command-marker"
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            HYBRID.validate_windows_command_line([
                private_marker,
                "x" * (HYBRID.WINDOWS_COMMAND_LINE_MAX_UNITS - 1),
            ])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("too long for Windows", stderr.getvalue())
        self.assertIn("32767", stderr.getvalue())
        self.assertNotIn(private_marker, stderr.getvalue())

    def test_oversized_main_cleans_artifacts_before_snapshot_or_router_start(self) -> None:
        policy = object()
        artifact_access = HYBRID.load_access_module()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        runtime = Path(temporary.name) / "overflow runtime"

        class Access:
            def __init__(self) -> None:
                self.deleted: list[Path] = []
                self.snapshot_calls = 0

            @staticmethod
            def load_or_refresh_policy():
                return policy

            @staticmethod
            def resolve_openrouter_route(received_policy, route):
                if received_policy is not policy or route != "root":
                    raise AssertionError("unexpected policy or route")
                return type(
                    "RootEntry", (), {"route": "root", "model": ORP_ROOT_MODEL}
                )()

            @staticmethod
            def session_route_policy(received_policy, profile, **kwargs):
                if received_policy is not policy:
                    raise AssertionError("unexpected policy")
                return ORP_ROUTE_POLICY

            @staticmethod
            def profile_guidance(received_policy, profile, **kwargs):
                if received_policy is not policy:
                    raise AssertionError("unexpected policy")
                return "ORP guidance"

            @staticmethod
            def write_session_artifact(content, prefix, suffix):
                return artifact_access.write_session_artifact(
                    content, prefix, suffix, runtime
                )

            @staticmethod
            def write_web_tools_mcp_config(script, profile=None):
                return ""

            def delete_session_artifact(self, path, digest, size):
                artifact_access.delete_session_artifact(path, digest, size, runtime)
                self.deleted.append(path)

            def write_session_snapshot(self, *args, **kwargs):
                self.snapshot_calls += 1
                raise AssertionError("snapshot must not be created before preflight")

        access = Access()
        request = {
            "profile": "openrouter-pure",
            "openrouter_root_route": "root",
            "plugin_dir": str(ROOT / "plugins" / "airlock"),
            "max_agents": "off",
            "context_window": "272000",
            "fast_mode": "off",
            "claude": sys.executable,
            "catalog_files": {},
            "router_helper": str(ROOT / "bin" / "airlock-router.py"),
            "args": ["x" * HYBRID.WINDOWS_COMMAND_LINE_MAX_UNITS],
        }
        stderr = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(HYBRID, "resolve_managed_request", return_value=Path("request.json")))
            stack.enter_context(patch.object(HYBRID, "read_request", return_value=request))
            stack.enter_context(patch.object(HYBRID, "validate_plugin_path", return_value=ROOT / "plugins" / "airlock"))
            stack.enter_context(patch.object(HYBRID, "load_access_module", return_value=access))
            stack.enter_context(patch.object(HYBRID, "render_agents", return_value='{"airlock-or-root":{}}'))
            stack.enter_context(patch.object(HYBRID, "managed_agent_permissions", return_value=(ORP_ROUTE_POLICY["agent_names"], ["Agent(airlock-or-root)"])))
            stack.enter_context(patch.object(HYBRID, "managed_session_settings", return_value="{}"))
            stack.enter_context(patch.object(HYBRID, "validate_policy_helper_path", return_value=POLICY_HELPER))
            stack.enter_context(patch.object(HYBRID, "validate_router_path", return_value=ROOT / "bin" / "airlock-router.py"))
            start_router = stack.enter_context(patch.object(HYBRID, "start_native_router"))
            build_environment = stack.enter_context(patch.object(HYBRID, "build_child_environment"))
            run_claude = stack.enter_context(patch.object(HYBRID.subprocess, "run"))
            stack.enter_context(patch.object(sys, "argv", ["airlock-hybrid.py", "--request-file", "request.json"]))
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                HYBRID.main()

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("too long for Windows", stderr.getvalue())
        self.assertEqual(access.snapshot_calls, 0)
        start_router.assert_not_called()
        build_environment.assert_not_called()
        run_claude.assert_not_called()
        self.assertEqual(len(access.deleted), 2)
        self.assertTrue(all(not path.exists() for path in access.deleted))

    def test_launch_failures_clean_settings_guidance_and_snapshot(self) -> None:
        artifact_access = HYBRID.load_access_module()
        request = {
            "profile": "openai-pure",
            "plugin_dir": str(ROOT / "plugins" / "airlock"),
            "max_agents": "off",
            "proxy_url": "http://127.0.0.1:18765",
            "root_model": "gpt-5.6-sol",
            "root_name": "GPT-5.6 Sol",
            "context_window": "272000",
            "fast_mode": "off",
            "claude": sys.executable,
            "catalog_files": {},
            "args": ["-r"],
        }

        for failure in (OSError("synthetic launch failure"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temporary:
                runtime = Path(temporary) / "runtime"

                class Access:
                    def __init__(self) -> None:
                        self.deleted_artifacts: list[Path] = []
                        self.snapshot_deleted = False

                    @staticmethod
                    def load_or_refresh_policy():
                        return object()

                    @staticmethod
                    def session_route_policy(received_policy, profile, **kwargs):
                        return ROUTE_POLICY

                    @staticmethod
                    def profile_guidance(received_policy, profile, **kwargs):
                        return "routing guidance"

                    @staticmethod
                    def write_session_artifact(content, prefix, suffix):
                        return artifact_access.write_session_artifact(
                            content, prefix, suffix, runtime
                        )

                    @staticmethod
                    def write_web_tools_mcp_config(script, profile=None):
                        return ""

                    def delete_session_artifact(self, path, digest, size):
                        artifact_access.delete_session_artifact(
                            path, digest, size, runtime
                        )
                        self.deleted_artifacts.append(path)

                    @staticmethod
                    def write_session_snapshot(*args, **kwargs):
                        return SNAPSHOT_PATH, SNAPSHOT_DIGEST

                    def delete_session_snapshot(self, path, digest):
                        self.snapshot_deleted = (
                            path == SNAPSHOT_PATH and digest == SNAPSHOT_DIGEST
                        )

                access = Access()
                stderr = io.StringIO()
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(HYBRID, "resolve_managed_request", return_value=Path("request.json")))
                    stack.enter_context(patch.object(HYBRID, "read_request", return_value=request))
                    stack.enter_context(patch.object(HYBRID, "validate_plugin_path", return_value=ROOT / "plugins" / "airlock"))
                    stack.enter_context(patch.object(HYBRID, "load_access_module", return_value=access))
                    stack.enter_context(patch.object(HYBRID, "render_agents", return_value='{"airlock-sol":{}}'))
                    stack.enter_context(patch.object(HYBRID, "managed_agent_permissions", return_value=(ROUTE_POLICY["agent_names"], ["Agent(airlock-sol)"])))
                    stack.enter_context(patch.object(HYBRID, "managed_session_settings", return_value='{"fastMode":false}'))
                    stack.enter_context(patch.object(HYBRID, "validate_policy_helper_path", return_value=POLICY_HELPER))
                    stack.enter_context(patch.object(HYBRID, "build_child_environment", return_value={}))
                    stack.enter_context(patch.object(HYBRID.subprocess, "run", side_effect=failure))
                    stack.enter_context(patch.object(sys, "argv", ["airlock-hybrid.py", "--request-file", "request.json"]))
                    with contextlib.redirect_stderr(stderr):
                        if isinstance(failure, KeyboardInterrupt):
                            with self.assertRaises(KeyboardInterrupt):
                                HYBRID.main()
                        else:
                            with self.assertRaises(SystemExit):
                                HYBRID.main()

                self.assertEqual(len(access.deleted_artifacts), 2)
                self.assertTrue(all(not path.exists() for path in access.deleted_artifacts))
                self.assertTrue(access.snapshot_deleted)

    def test_disabled_router_root_reports_specific_policy_error(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            HYBRID.validate_root_route(
                {"routes": {"claude-opus-5[1m]": "anthropic"}},
                "hybrid-anthropic-root",
                "claude-fable-5[1m]",
            )
        self.assertEqual(raised.exception.code, 2)
        self.assertIn(
            "session root model 'claude-fable-5[1m]' is not enabled by the active route policy",
            stderr.getvalue(),
        )

    def test_protected_options_are_rejected(self) -> None:
        for option in HYBRID.PROTECTED_OPTIONS:
            with self.subTest(option=option), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                HYBRID.validate_child_args([option, "value"])
            with self.subTest(option=option + "="), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                HYBRID.validate_child_args([f"{option}=value"])


class FastTransitionBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runtime = Path(self.temporary.name) / "runtime"
        self.artifact_access = HYBRID.load_access_module()
        self.policy = object()
        self.channel = "fast-transition-" + "a" * 32 + ".json"
        self.nonce = "b" * 64
        self.cwd = str(ROOT.resolve())
        self.request = {
            "profile": "openai-pure",
            "plugin_dir": str(ROOT / "plugins" / "airlock"),
            "max_agents": "off",
            "proxy_url": "http://127.0.0.1:18765",
            "root_model": "gpt-5.6-sol",
            "root_name": "GPT-5.6 Sol",
            "context_window": "272000",
            "fast_mode": "off",
            "claude": sys.executable,
            "catalog_files": {},
            "args": ["--model", "gpt-5.6-sol", "--effort", "high"],
            "fast_transition_launcher_pid": 4242,
            "fast_transition_cwd": self.cwd,
        }

    def run_bridge(
        self,
        consume_result: str | None = None,
        *,
        first_exit: int = 0,
        consume_error: Exception | None = None,
        eligible: bool = True,
    ) -> tuple[object, list[dict[str, object]], list[Path], list[Path]]:
        runtime = self.runtime
        artifact_access = self.artifact_access
        policy = self.policy
        records: list[dict[str, object]] = []
        snapshots: list[Path] = []
        deleted_snapshots: list[Path] = []

        class Access:
            AccessError = RuntimeError
            load_calls = 0
            consume_calls: list[tuple[object, ...]] = []
            eligibility_calls: list[tuple[object, ...]] = []

            def load_or_refresh_policy(self):
                self.load_calls += 1
                return policy

            @staticmethod
            def session_route_policy(received_policy, profile, **kwargs):
                if received_policy is not policy or profile != "openai-pure":
                    raise AssertionError("unexpected policy or profile")
                return ROUTE_POLICY

            @staticmethod
            def profile_guidance(received_policy, profile, **kwargs):
                return "routing guidance"

            @staticmethod
            def write_session_artifact(content, prefix, suffix):
                return artifact_access.write_session_artifact(
                    content, prefix, suffix, runtime
                )

            @staticmethod
            def write_web_tools_mcp_config(script, profile=None):
                return ""

            @staticmethod
            def delete_session_artifact(path, digest, size):
                artifact_access.delete_session_artifact(path, digest, size, runtime)

            @staticmethod
            def write_session_snapshot(*args, **kwargs):
                runtime.mkdir(parents=True, exist_ok=True)
                path = runtime / f"snapshot-{len(snapshots)}.json"
                path.write_text("{}", encoding="utf-8")
                snapshots.append(path)
                return path, SNAPSHOT_DIGEST

            @staticmethod
            def delete_session_snapshot(path, digest):
                deleted_snapshots.append(path)
                path.unlink(missing_ok=True)

            def fast_transition_consume(self, *args, **kwargs):
                self.consume_calls.append((*args, kwargs))
                if records:
                    records[0]["consume"] = (*args, kwargs)
                if consume_error is not None:
                    raise consume_error
                return consume_result

            def explicit_fast_status(self, *args, **kwargs):
                self.eligibility_calls.append((*args, kwargs))
                return {
                    "eligible": eligible,
                    "reason": "synthetic Fast eligibility failure",
                }

        access = Access()

        def launch(command, **kwargs):
            settings = Path(command[command.index("--settings") + 1])
            guidance = Path(
                command[command.index("--append-system-prompt-file") + 1]
            )
            records.append({
                "command": list(command),
                "env": dict(kwargs["env"]),
                "settings": settings,
                "guidance": guidance,
                "settings_exists": settings.is_file(),
                "guidance_exists": guidance.is_file(),
                "snapshot": Path(kwargs["env"]["AIRLOCK_SESSION_SNAPSHOT"]),
            })
            return subprocess.CompletedProcess(
                command, first_exit if len(records) == 1 else 0
            )

        inherited = {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:18765",
            "ANTHROPIC_MODEL": "gpt-5.6-sol",
            HYBRID.FAST_TRANSITION_CHANNEL_ENV: self.channel,
            HYBRID.FAST_TRANSITION_NONCE_ENV: self.nonce,
        }
        stack = contextlib.ExitStack()
        stack.enter_context(patch.dict(os.environ, inherited, clear=True))
        stack.enter_context(patch.object(HYBRID, "validate_plugin_path", return_value=ROOT / "plugins" / "airlock"))
        stack.enter_context(patch.object(HYBRID, "render_agents", return_value='{"airlock-sol":{}}'))
        stack.enter_context(patch.object(HYBRID, "managed_agent_permissions", return_value=(ROUTE_POLICY["agent_names"], ["Agent(airlock-sol)"])))
        stack.enter_context(patch.object(HYBRID, "managed_session_settings", return_value='{"fastMode":false}'))
        stack.enter_context(patch.object(HYBRID, "validate_policy_helper_path", return_value=POLICY_HELPER))
        stack.enter_context(patch.object(HYBRID.subprocess, "run", side_effect=launch))
        with stack:
            try:
                outcome: object = HYBRID.main(dict(self.request), access)
            except SystemExit as error:
                outcome = error
        return outcome, records, snapshots, deleted_snapshots

    def test_pending_returns_normally_without_relaunch(self) -> None:
        outcome, records, snapshots, deleted = self.run_bridge(None)
        self.assertEqual(outcome, 0)
        self.assertEqual(len(records), 1)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(deleted, snapshots)

    def test_ready_consumes_exact_binding_and_resumes_once_with_fresh_artifacts(self) -> None:
        session_id = "session-exact-123"
        outcome, records, snapshots, deleted = self.run_bridge(session_id)
        self.assertEqual(outcome, 0)
        self.assertEqual(len(records), 2)
        first, resumed = records
        self.assertEqual(
            first["env"][HYBRID.FAST_TRANSITION_CHANNEL_ENV], self.channel
        )
        self.assertEqual(
            first["consume"],
            (4242, self.cwd, self.channel, self.nonce, {"if_ready": True}),
        )
        self.assertNotIn(HYBRID.FAST_TRANSITION_CHANNEL_ENV, resumed["env"])
        self.assertNotIn(HYBRID.FAST_TRANSITION_NONCE_ENV, resumed["env"])
        self.assertEqual(resumed["env"]["AIRLOCK_ACTIVE_PROFILE"], "openai-pure")
        self.assertEqual(resumed["env"]["AIRLOCK_ROOT_MODEL"], "gpt-5.6-sol-fast")
        self.assertEqual(resumed["env"]["AIRLOCK_EPHEMERAL_OPENAI_FAST"], "1")
        command = resumed["command"]
        prompt_index = command.index("--append-system-prompt-file")
        self.assertEqual(command[prompt_index - 6:prompt_index], [
            "--model", "gpt-5.6-sol-fast",
            "--effort", "high",
            "--resume", session_id,
        ])
        self.assertNotEqual(first["settings"], resumed["settings"])
        self.assertNotEqual(first["guidance"], resumed["guidance"])
        self.assertNotEqual(first["snapshot"], resumed["snapshot"])
        self.assertTrue(first["settings_exists"] and resumed["settings_exists"])
        self.assertTrue(first["guidance_exists"] and resumed["guidance_exists"])
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(deleted, snapshots)
        self.assertTrue(all(not path.exists() for path in snapshots))

    def test_nonzero_child_does_not_consume_or_resume(self) -> None:
        outcome, records, snapshots, _deleted = self.run_bridge(
            "session-ignored", first_exit=17
        )
        self.assertEqual(outcome, 17)
        self.assertEqual(len(records), 1)
        self.assertEqual(len(snapshots), 1)

    def test_armed_without_finalize_surfaces_sanitized_helper_error(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            outcome, records, _snapshots, _deleted = self.run_bridge(
                consume_error=RuntimeError(
                    "Fast transition was armed but SessionEnd did not finalize it"
                )
            )
        self.assertIsInstance(outcome, SystemExit)
        self.assertEqual(len(records), 1)
        self.assertIn(
            "airlock: Fast transition was armed but SessionEnd did not finalize it",
            stderr.getvalue(),
        )

    def test_eligibility_failure_has_no_fallback_relaunch(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            outcome, records, _snapshots, _deleted = self.run_bridge(
                "session-consumed", eligible=False
            )
        self.assertIsInstance(outcome, SystemExit)
        self.assertEqual(len(records), 1)
        self.assertIn("synthetic Fast eligibility failure", stderr.getvalue())

    def test_transition_nonce_is_rejected_in_request(self) -> None:
        request = dict(self.request, fast_transition_nonce=self.nonce)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            HYBRID.main(request, object())


class OpenRouterHybridRootTests(unittest.TestCase):
    ROUTE_POLICY = {
        "routes": {
            "claude-opus-5": "anthropic",
            "gpt-5.6-sol": "openai",
            ORP_ROOT_MODEL: "openrouter",
            "vendor/extra-model": "openrouter",
        },
        "model_ids": [
            "claude-opus-5", "gpt-5.6-sol", ORP_ROOT_MODEL, "vendor/extra-model",
        ],
        "agent_names": [
            "airlock-opus", "airlock-or-extra", "airlock-or-root", "airlock-sol",
        ],
        "extra_model_ids": ["vendor/extra-model"],
        "extra_agent_names": ["airlock-or-extra"],
        "discovery_model": "gpt-5.6-sol",
        "picker_models": {
            "fable": "gpt-5.6-sol",
            "opus": "claude-opus-5",
            "sonnet": "gpt-5.6-sol",
            "haiku": "gpt-5.6-sol",
        },
    }

    def build(self, root_name: str = "OpenRouter root", **environment: str):
        with patch.dict(os.environ, environment, clear=True):
            return build_child_environment(
                "hybrid-openrouter-root",
                "off",
                proxy_url="http://127.0.0.1:18765",
                root_model=ORP_ROOT_MODEL,
                root_name=root_name,
                context_window="272000",
                route_policy=self.ROUTE_POLICY,
                router_url="http://127.0.0.1:28471",
            )

    def test_environment_keeps_the_router_and_pops_every_credential(self) -> None:
        inherited = {
            "ANTHROPIC_AUTH_TOKEN": "unused",
            "CLAUDE_CODE_OAUTH_TOKEN": "synthetic-oauth",
            "OPENAI_API_KEY": "synthetic-openai",
            "CODEX_API_KEY": "synthetic-codex",
            "XAI_API_KEY": "synthetic-xai",
            "GROK_API_KEY": "synthetic-grok",
            "OPENROUTER_API_KEY": "synthetic-openrouter",
            "AIRLOCK_HYBRID": "1",
            "AIRLOCK_GPT_HYBRID": "1",
        }
        environment = self.build("OpenRouter root", **inherited)
        for variable in HYBRID.ORP_CREDENTIAL_VARIABLES | {"ANTHROPIC_API_KEY"}:
            self.assertNotIn(variable, environment)
        self.assertEqual(environment["ANTHROPIC_BASE_URL"], "http://127.0.0.1:28471")
        self.assertEqual(environment["AIRLOCK_OPENROUTER_HYBRID"], "1")
        for marker in ("AIRLOCK_HYBRID", "AIRLOCK_GPT_HYBRID", "AIRLOCK_GROK_HYBRID"):
            self.assertNotIn(marker, environment)
        # Hybrid sessions keep routing by argv model ID; only the custom option
        # carries the OpenRouter root.
        self.assertNotIn("ANTHROPIC_MODEL", environment)
        self.assertEqual(environment["ANTHROPIC_CUSTOM_MODEL_OPTION"], ORP_ROOT_MODEL)
        self.assertEqual(
            environment["ANTHROPIC_CUSTOM_MODEL_OPTION_NAME"],
            "OpenRouter root (OpenRouter hybrid root)",
        )
        self.assertEqual(
            environment["ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION"],
            f"Selected OpenRouter hybrid root ({ORP_ROOT_MODEL})",
        )
        # A vendor ID matches no Anthropic pattern, so effort must be declared.
        self.assertEqual(
            environment["ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES"],
            "effort,xhigh_effort,max_effort",
        )
        # Family slots stay wrapper backed and never resolve to OpenRouter.
        self.assertEqual(environment["ANTHROPIC_DEFAULT_FABLE_MODEL"], "gpt-5.6-sol")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_OPUS_MODEL"], "claude-opus-5")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_SONNET_MODEL"], "gpt-5.6-sol")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "gpt-5.6-sol")

    def test_forwarded_model_must_restate_the_selected_root(self) -> None:
        HYBRID.reject_openrouter_model_overrides(["--model", ORP_ROOT_MODEL], ORP_ROOT_MODEL)
        HYBRID.reject_openrouter_model_overrides(["-m", ORP_ROOT_MODEL], ORP_ROOT_MODEL)
        HYBRID.reject_openrouter_model_overrides([f"--model={ORP_ROOT_MODEL}"], ORP_ROOT_MODEL)
        for arguments in (
            ["--model", "vendor/other-model"],
            [f"--model={ORP_ROOT_MODEL}x"],
            ["--model"],
        ):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                HYBRID.reject_openrouter_model_overrides(arguments, ORP_ROOT_MODEL)

    def test_request_shape_requires_route_and_proxy_but_no_root_fields(self) -> None:
        base_request = {
            "profile": "hybrid-openrouter-root",
            "plugin_dir": str(ROOT / "plugins" / "airlock"),
            "max_agents": "off",
            "context_window": "272000",
            "fast_mode": "off",
            "claude": sys.executable,
            "catalog_files": {},
            "router_helper": str(ROOT / "bin" / "airlock-router.py"),
            "args": ["-r"],
        }
        cases = (
            (
                dict(base_request, proxy_url="http://127.0.0.1:18765"),
                "requires an exact OpenRouter root route",
            ),
            (
                dict(
                    base_request,
                    proxy_url="http://127.0.0.1:18765",
                    openrouter_root_route="root",
                    root_model=ORP_ROOT_MODEL,
                ),
                "must not include root_model",
            ),
            (
                dict(base_request, openrouter_root_route="root"),
                "OpenAI proxy URL is invalid",
            ),
        )
        with patch.dict(os.environ, {"AIRLOCK_OPENROUTER_HYBRID": "1"}, clear=True):
            for request, expected in cases:
                stderr = io.StringIO()
                with self.subTest(expected=expected), contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
                    HYBRID.main(dict(request), _allow_transition=False)
                self.assertIn(expected, stderr.getvalue())


class GrokProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        # Same hermetic default as the launcher tests: no inherited Fast
        # transition credentials.
        patcher = patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(HYBRID.FAST_TRANSITION_CHANNEL_ENV, None)
        os.environ.pop(HYBRID.FAST_TRANSITION_NONCE_ENV, None)

    ROUTE_POLICY = {
        "routes": {
            "claude-opus-5": "anthropic",
            "gpt-5.6-sol": "openai",
            "grok-4.6": "grok",
            "grok-composer-2.5-fast": "grok",
        },
        "model_ids": [
            "claude-opus-5", "gpt-5.6-sol", "grok-4.6", "grok-composer-2.5-fast",
        ],
        "agent_names": [
            "airlock-composer", "airlock-grok", "airlock-opus", "airlock-sol",
        ],
        "extra_model_ids": [],
        "extra_agent_names": [],
        "picker_models": {
            "fable": "grok-4.6",
            "opus": "grok-4.6",
            "sonnet": "grok-composer-2.5-fast",
            "haiku": "grok-composer-2.5-fast",
        },
    }

    def build(self, profile: str, root_model: str, router_url: str | None, **environment: str):
        with patch.dict(os.environ, environment, clear=True):
            return build_child_environment(
                profile,
                "off",
                proxy_url="http://127.0.0.1:18765",
                root_model=root_model,
                root_name="Grok 4.6",
                context_window="272000",
                route_policy=self.ROUTE_POLICY,
                router_url=router_url,
            )

    def test_grok_pure_talks_to_the_proxy_without_a_router(self) -> None:
        environment = self.build(
            "grok-pure", "grok-4.6", None,
            ANTHROPIC_BASE_URL="http://127.0.0.1:18765",
            ANTHROPIC_MODEL="grok-4.6",
            ANTHROPIC_DEFAULT_FABLE_MODEL="claude-fable-5",
            ANTHROPIC_SMALL_FAST_MODEL="gpt-5.6-sol",
        )
        self.assertEqual(environment["ANTHROPIC_BASE_URL"], "http://127.0.0.1:18765")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_FABLE_MODEL"], "grok-4.6")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_OPUS_MODEL"], "grok-4.6")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_SONNET_MODEL"], "grok-composer-2.5-fast")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "grok-composer-2.5-fast")
        self.assertEqual(environment["ANTHROPIC_SMALL_FAST_MODEL"], "grok-composer-2.5-fast")
        self.assertEqual(environment["AIRLOCK_ACTIVE_PROFILE"], "grok-pure")
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", environment)
        self.assertEqual(environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "272000")
        for marker in ("AIRLOCK_HYBRID", "AIRLOCK_GPT_HYBRID", "AIRLOCK_GROK_HYBRID"):
            self.assertNotIn(marker, environment)

    def test_grok_pure_rejects_a_router(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.build("grok-pure", "grok-4.6", "http://127.0.0.1:28471")

    def test_grok_root_declares_effort_capabilities(self) -> None:
        # Claude Code matches Anthropic ID patterns to decide whether a model
        # supports effort. A Grok ID matches nothing, so /effort would vanish
        # without an explicit declaration.
        environment = self.build("hybrid-grok-root", "grok-4.6", "http://127.0.0.1:28471")
        self.assertEqual(
            environment["ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES"],
            "effort,xhigh_effort,max_effort",
        )

    def test_grok_classifier_rides_the_composer_seat(self) -> None:
        environment = self.build("hybrid-grok-root", "grok-4.6", "http://127.0.0.1:28471")
        self.assertEqual(
            environment["CLAUDE_CODE_AUTO_MODE_MODEL"],
            "grok-composer-2.5-fast",
        )

    def test_hybrid_grok_root_sets_only_its_own_marker(self) -> None:
        environment = self.build(
            "hybrid-grok-root", "grok-4.6", "http://127.0.0.1:28471",
            AIRLOCK_HYBRID="1", AIRLOCK_GPT_HYBRID="1",
        )
        self.assertEqual(environment["AIRLOCK_GROK_HYBRID"], "1")
        self.assertNotIn("AIRLOCK_HYBRID", environment)
        self.assertNotIn("AIRLOCK_GPT_HYBRID", environment)
        self.assertEqual(environment["ANTHROPIC_BASE_URL"], "http://127.0.0.1:28471")

    def test_grok_workers_reach_the_allowlists(self) -> None:
        environment = self.build("hybrid-grok-root", "grok-4.6", "http://127.0.0.1:28471")
        self.assertEqual(
            environment["AIRLOCK_ALLOWED_AGENT_NAMES"],
            "airlock-composer,airlock-grok,airlock-opus,airlock-sol",
        )
        self.assertIn("grok-4.6", environment["AIRLOCK_ALLOWED_AGENT_MODELS"])


if __name__ == "__main__":
    unittest.main()
