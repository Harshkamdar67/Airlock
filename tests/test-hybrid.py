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


class HybridLauncherTests(unittest.TestCase):
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
            environment = HYBRID.build_child_environment(
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
            return HYBRID.build_child_environment(
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
        self.assertEqual(environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "272000")

    def test_explicit_airlock_window_can_override_a_bare_sol_root(self) -> None:
        environment = self.build(
            "hybrid-openai-root",
            "gpt-5.6-sol",
            context_window="450000",
            force_context_window=True,
        )
        self.assertEqual(environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "450000")

    def test_bare_proxy_root_keeps_the_conservative_auto_compact_window(self) -> None:
        environment = self.build("hybrid-grok-root", "grok-4.5")
        self.assertEqual(environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "272000")

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
            direct = HYBRID.build_child_environment(
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
            gpt_root = HYBRID.build_child_environment(
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
            claude_root = HYBRID.build_child_environment(
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
                HYBRID.build_child_environment(
                    "hybrid-openai-root",
                    "off",
                    proxy_url="http://127.0.0.1:18765",
                    root_model="gpt-5.6-sol",
                    root_name="GPT-5.6 Sol",
                    context_window="272000",
                    route_policy=ROUTE_POLICY,
                    router_url="http://127.0.0.1:28471",
                )

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
            environment = HYBRID.build_child_environment(
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

    def test_router_start_uses_exact_route_table_and_parent(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="http://127.0.0.1:28471\n", stderr=""
        )
        with patch.object(HYBRID.subprocess, "run", return_value=completed) as run:
            address = HYBRID.start_native_router(
                ROOT / "bin" / "airlock-router.py",
                ROUTE_POLICY["routes"],
                "http://127.0.0.1:18765",
            )
        self.assertEqual(address, "http://127.0.0.1:28471")
        command = run.call_args.args[0]
        self.assertIn("--parent-pid", command)
        self.assertIn(str(os.getpid()), command)
        self.assertEqual(
            json.loads(command[command.index("--routes-json") + 1]),
            ROUTE_POLICY["routes"],
        )

    def test_exact_native_agent_permissions_are_sorted_and_narrow(self) -> None:
        access = HYBRID.load_access_module()
        agents_json = '{"airlock-sonnet":{},"airlock-luna":{},"airlock-sol":{}}'
        names, rules = HYBRID.managed_agent_permissions(access, agents_json)
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
            HYBRID.managed_session_settings(access, agents_json, "inherit")
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
            json.loads(HYBRID.managed_session_settings(access, agents_json, "on"))[
                "fastMode"
            ]
        )
        self.assertFalse(
            json.loads(HYBRID.managed_session_settings(access, agents_json, "off"))[
                "fastMode"
            ]
        )
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            HYBRID.managed_agent_permissions(access, '{"Explore":{}}')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            HYBRID.managed_session_settings(access, '{"Explore":{}}', "off")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            HYBRID.managed_session_settings(access, agents_json, "invalid")

    def test_append_routing_guidance_preserves_custom_prompt_and_order(self) -> None:
        self.assertEqual(
            HYBRID.append_routing_guidance(["--model", "root"], "native"),
            ["--model", "root", "--append-system-prompt", "native"],
        )
        separate = HYBRID.append_routing_guidance(
            ["--model", "root", "--append-system-prompt", "user", "task"], "native",
        )
        self.assertEqual(separate, [
            "--model", "root", "task", "--append-system-prompt", "user\n\nnative",
        ])
        equals = HYBRID.append_routing_guidance(
            ["--append-system-prompt=first", "--append-system-prompt", "second"], "native",
        )
        self.assertEqual(equals, [
            "--append-system-prompt", "first\n\nsecond\n\nnative",
        ])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            HYBRID.append_routing_guidance(["--append-system-prompt"], "native")

    def test_protected_options_are_rejected(self) -> None:
        for option in HYBRID.PROTECTED_OPTIONS:
            with self.subTest(option=option), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                HYBRID.validate_child_args([option, "value"])
            with self.subTest(option=option + "="), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                HYBRID.validate_child_args([f"{option}=value"])


class GrokProfileTests(unittest.TestCase):
    ROUTE_POLICY = {
        "routes": {
            "claude-opus-5": "anthropic",
            "gpt-5.6-sol": "openai",
            "grok-4.5": "grok",
            "grok-composer-2.5-fast": "grok",
        },
        "model_ids": [
            "claude-opus-5", "gpt-5.6-sol", "grok-4.5", "grok-composer-2.5-fast",
        ],
        "agent_names": [
            "airlock-composer", "airlock-grok", "airlock-opus", "airlock-sol",
        ],
        "extra_model_ids": [],
        "extra_agent_names": [],
        "picker_models": {
            "fable": "grok-4.5",
            "opus": "grok-4.5",
            "sonnet": "grok-composer-2.5-fast",
            "haiku": "grok-composer-2.5-fast",
        },
    }

    def build(self, profile: str, root_model: str, router_url: str | None, **environment: str):
        with patch.dict(os.environ, environment, clear=True):
            return HYBRID.build_child_environment(
                profile,
                "off",
                proxy_url="http://127.0.0.1:18765",
                root_model=root_model,
                root_name="Grok 4.5",
                context_window="272000",
                route_policy=self.ROUTE_POLICY,
                router_url=router_url,
            )

    def test_grok_pure_talks_to_the_proxy_without_a_router(self) -> None:
        environment = self.build(
            "grok-pure", "grok-4.5", None,
            ANTHROPIC_BASE_URL="http://127.0.0.1:18765",
            ANTHROPIC_MODEL="grok-4.5",
            ANTHROPIC_DEFAULT_FABLE_MODEL="claude-fable-5",
            ANTHROPIC_SMALL_FAST_MODEL="gpt-5.6-sol",
        )
        self.assertEqual(environment["ANTHROPIC_BASE_URL"], "http://127.0.0.1:18765")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_FABLE_MODEL"], "grok-4.5")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_OPUS_MODEL"], "grok-4.5")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_SONNET_MODEL"], "grok-composer-2.5-fast")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "grok-composer-2.5-fast")
        self.assertEqual(environment["ANTHROPIC_SMALL_FAST_MODEL"], "grok-composer-2.5-fast")
        self.assertEqual(environment["AIRLOCK_ACTIVE_PROFILE"], "grok-pure")
        for marker in ("AIRLOCK_HYBRID", "AIRLOCK_GPT_HYBRID", "AIRLOCK_GROK_HYBRID"):
            self.assertNotIn(marker, environment)

    def test_grok_pure_rejects_a_router(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.build("grok-pure", "grok-4.5", "http://127.0.0.1:28471")

    def test_grok_root_declares_effort_capabilities(self) -> None:
        # Claude Code matches Anthropic ID patterns to decide whether a model
        # supports effort. A Grok ID matches nothing, so /effort would vanish
        # without an explicit declaration.
        environment = self.build("hybrid-grok-root", "grok-4.5", "http://127.0.0.1:28471")
        self.assertEqual(
            environment["ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES"],
            "effort,xhigh_effort,max_effort",
        )

    def test_hybrid_grok_root_sets_only_its_own_marker(self) -> None:
        environment = self.build(
            "hybrid-grok-root", "grok-4.5", "http://127.0.0.1:28471",
            AIRLOCK_HYBRID="1", AIRLOCK_GPT_HYBRID="1",
        )
        self.assertEqual(environment["AIRLOCK_GROK_HYBRID"], "1")
        self.assertNotIn("AIRLOCK_HYBRID", environment)
        self.assertNotIn("AIRLOCK_GPT_HYBRID", environment)
        self.assertEqual(environment["ANTHROPIC_BASE_URL"], "http://127.0.0.1:28471")

    def test_grok_workers_reach_the_allowlists(self) -> None:
        environment = self.build("hybrid-grok-root", "grok-4.5", "http://127.0.0.1:28471")
        self.assertEqual(
            environment["AIRLOCK_ALLOWED_AGENT_NAMES"],
            "airlock-composer,airlock-grok,airlock-opus,airlock-sol",
        )
        self.assertIn("grok-4.5", environment["AIRLOCK_ALLOWED_AGENT_MODELS"])


if __name__ == "__main__":
    unittest.main()
