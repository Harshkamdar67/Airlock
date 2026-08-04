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
        "gpt-5.6-luna[1m]": "openai",
        "gpt-5.6-sol[1m]": "openai",
    },
    "model_ids": ["claude-opus-5", "gpt-5.6-luna[1m]", "gpt-5.6-sol[1m]"],
    "agent_names": ["airlock-luna", "airlock-opus", "airlock-sol"],
    "extra_model_ids": [],
    "extra_agent_names": [],
}


class HybridLauncherTests(unittest.TestCase):
    def test_hybrid_environment_uses_router_profile_depth_cap_and_allowlists(self) -> None:
        with patch.dict(os.environ, {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:18765",
            "ANTHROPIC_MODEL": "gpt-5.6-sol[1m]",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-5",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-5",
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
        self.assertNotIn("ANTHROPIC_DEFAULT_OPUS_MODEL", environment)
        self.assertNotIn("ANTHROPIC_DEFAULT_SONNET_MODEL", environment)
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", environment)
        self.assertEqual(environment["AIRLOCK_ACTIVE_PROFILE"], "hybrid-anthropic-root")
        self.assertEqual(environment["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"], "3")
        self.assertEqual(environment["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"], "1")
        self.assertEqual(
            environment["AIRLOCK_ALLOWED_AGENT_MODELS"],
            "claude-opus-5,gpt-5.6-luna[1m],gpt-5.6-sol[1m]",
        )
        self.assertEqual(
            environment["AIRLOCK_ALLOWED_AGENT_NAMES"],
            "airlock-luna,airlock-opus,airlock-sol",
        )

    def test_effort_capabilities_are_declared_for_gpt_roots_only(self) -> None:
        with patch.dict(os.environ, {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:18765",
            "ANTHROPIC_MODEL": "gpt-5.6-sol[1m]",
        }, clear=True):
            direct = HYBRID.build_child_environment(
                "openai-pure",
                "off",
                proxy_url="http://127.0.0.1:18765",
                root_model="gpt-5.6-sol[1m]",
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
                root_model="gpt-5.6-sol[1m]",
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
                    root_model="gpt-5.6-sol[1m]",
                    root_name="GPT-5.6 Sol",
                    context_window="272000",
                    route_policy=ROUTE_POLICY,
                    router_url="http://127.0.0.1:28471",
                )

    def test_plain_openai_stays_direct_and_off_removes_only_cap(self) -> None:
        with patch.dict(os.environ, {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:18765",
            "ANTHROPIC_AUTH_TOKEN": "unused",
            "ANTHROPIC_MODEL": "gpt-5.6-sol[1m]",
            "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "19",
            "CLAUDE_CODE_SUBAGENT_MODEL": "claude-haiku-4-5-20251001",
        }, clear=True):
            environment = HYBRID.build_child_environment(
                "openai-pure",
                "off",
                proxy_url="http://127.0.0.1:18765",
                root_model="gpt-5.6-sol[1m]",
                root_name="GPT-5.6 Sol",
                context_window="272000",
                route_policy=ROUTE_POLICY,
                router_url=None,
            )
        self.assertNotIn("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", environment)
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", environment)
        self.assertEqual(environment["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"], "1")
        self.assertEqual(environment["ANTHROPIC_BASE_URL"], "http://127.0.0.1:18765")
        self.assertEqual(environment["ANTHROPIC_AUTH_TOKEN"], "unused")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_OPUS_MODEL"], "gpt-5.6-sol[1m]")
        self.assertEqual(environment["ANTHROPIC_DEFAULT_SONNET_MODEL"], "gpt-5.6-sol[1m]")

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
        settings = json.loads(HYBRID.managed_session_settings(access, agents_json))
        self.assertEqual(settings["autoMode"]["environment"][0], "$defaults")
        context = settings["autoMode"]["environment"][1]
        self.assertIn("OpenAI models", context)
        self.assertIn("Anthropic Claude", context)
        self.assertIn("eligible non-ignored untracked regular files", context)
        self.assertIn("exact built-in Explore, Plan, and general-purpose", context)
        self.assertIn("Git-ignored or unsafe paths", context)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            HYBRID.managed_agent_permissions(access, '{"Explore":{}}')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            HYBRID.managed_session_settings(access, '{"Explore":{}}')

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


if __name__ == "__main__":
    unittest.main()
