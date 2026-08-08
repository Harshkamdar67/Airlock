#!/usr/bin/env python3
"""Synthetic PreToolUse tests for the session-scoped Agent guard."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "plugins" / "airlock" / "scripts" / "agent-guard.py"
GUARD_SH = GUARD.with_suffix(".sh")
SECRET_GUARD_SH = GUARD.parent / "secret-guard.sh"


class AgentGuardTests(unittest.TestCase):
    @staticmethod
    def load_module(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def load_access_module(self):
        return self.load_module("airlock_access_guard_test", ROOT / "bin" / "airlock-access.py")

    def guard_constant(self, name: str) -> set[str]:
        return getattr(self.load_module("airlock_agent_guard_test", GUARD), name)

    def invoke(
        self,
        profile: str | None,
        tool_input: object,
        tool_name: str = "Agent",
        allowed_agents: str | None = None,
        allowed_models: str | None = None,
        extra_agents: str | None = None,
        extra_models: str | None = None,
    ) -> dict | None:
        environment = os.environ.copy()
        for variable in (
            "AIRLOCK_ALLOWED_AGENT_NAMES",
            "AIRLOCK_ALLOWED_AGENT_MODELS",
            "AIRLOCK_EXTRA_USAGE_AGENT_NAMES",
            "AIRLOCK_EXTRA_USAGE_AGENT_MODELS",
        ):
            environment.pop(variable, None)
        if allowed_agents is not None:
            environment["AIRLOCK_ALLOWED_AGENT_NAMES"] = allowed_agents
        if allowed_models is not None:
            environment["AIRLOCK_ALLOWED_AGENT_MODELS"] = allowed_models
        if extra_agents is not None:
            environment["AIRLOCK_EXTRA_USAGE_AGENT_NAMES"] = extra_agents
        if extra_models is not None:
            environment["AIRLOCK_EXTRA_USAGE_AGENT_MODELS"] = extra_models
        if profile is None:
            environment.pop("AIRLOCK_ACTIVE_PROFILE", None)
        else:
            environment["AIRLOCK_ACTIVE_PROFILE"] = profile
        completed = subprocess.run(
            [sys.executable, str(GUARD)],
            input=json.dumps({"tool_name": tool_name, "tool_input": tool_input}),
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=environment,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        return json.loads(completed.stdout) if completed.stdout.strip() else None

    def assert_denied(self, result: dict | None) -> None:
        self.assertIsNotNone(result)
        decision = result["hookSpecificOutput"]
        self.assertEqual(decision["hookEventName"], "PreToolUse")
        self.assertEqual(decision["permissionDecision"], "deny")

    def test_guard_baseline_matches_the_models_the_launcher_actually_enables(self) -> None:
        # The guard validates the launcher's model list against its own baseline
        # and treats any unrecognised value as a corrupt permission set, which
        # denies every Agent call in the session rather than just that model. So
        # the two lists drifting apart is not a cosmetic mismatch, and reading
        # both from the same source is the only way to keep them honest.
        access = self.load_access_module()
        for provider, models in (
            ("anthropic", "ANTHROPIC_MODELS"),
            ("openai", "OPENAI_MODELS"),
            ("grok", "GROK_MODELS"),
        ):
            with self.subTest(provider=provider):
                expected = {
                    profile["model"]
                    for profile in access.MODEL_PROFILES[provider].values()
                }
                self.assertEqual(self.guard_constant(models), expected)

    def test_a_full_session_permission_set_is_accepted_end_to_end(self) -> None:
        # The drift above was invisible to every other test here because they all
        # supply their own model lists. This one sends exactly what the launcher
        # sends, including the extra-usage list that made the guard reject the
        # whole permission set.
        access = self.load_access_module()
        anthropic = access.MODEL_PROFILES["anthropic"]
        openai = access.MODEL_PROFILES["openai"]
        allowed = ",".join(sorted({
            anthropic["opus"]["model"], anthropic["sonnet"]["model"],
            openai["sol"]["model"], openai["luna"]["model"],
        }))
        decision = self.invoke(
            "hybrid-anthropic-root",
            {"subagent_type": "airlock-opus", "prompt": "work"},
            allowed_agents="airlock-luna,airlock-opus,airlock-sol,airlock-sonnet",
            allowed_models=allowed,
            extra_models=anthropic["fable"]["model"],
        )
        self.assertIsNone(decision)
        override = self.invoke(
            "hybrid-anthropic-root",
            {
                "subagent_type": "Explore",
                "model": anthropic["opus"]["model"],
                "prompt": "research",
            },
            allowed_agents="airlock-luna,airlock-opus,airlock-sol,airlock-sonnet",
            allowed_models=allowed,
            extra_models=anthropic["fable"]["model"],
        )
        self.assertIsNone(override)

    def test_allows_exact_profile_worker(self) -> None:
        self.assertIsNone(self.invoke("openai-pure", {"subagent_type": "airlock-terra", "prompt": "ignored"}))
        self.assertIsNone(self.invoke("openai-pure", {"subagent_type": "airlock-luna-fast"}))
        self.assertIsNone(self.invoke("hybrid-anthropic-root", {"subagent_type": "airlock-opus"}))
        self.assertIsNone(self.invoke("hybrid-anthropic-root", {"subagent_type": "airlock-fable"}))
        self.assertIsNone(self.invoke("hybrid-anthropic-root", {"subagent_type": "airlock-haiku"}))

    def test_allows_exact_builtins_for_every_profile(self) -> None:
        for profile in ("openai-pure", "hybrid-openai-root", "hybrid-anthropic-root"):
            for name in ("Explore", "Plan", "general-purpose"):
                with self.subTest(profile=profile, name=name):
                    self.assertIsNone(self.invoke(profile, {"subagent_type": name}))

    def test_denies_other_generic_and_cross_profile_workers(self) -> None:
        for name in ("claude", "airlock-opus", "Agent", "unknown-worker"):
            with self.subTest(name=name):
                self.assert_denied(self.invoke("openai-pure", {"subagent_type": name}))

    def test_builtins_accept_exact_enabled_models_and_inherit_by_default(self) -> None:
        openai_models = "gpt-5.6-luna[1m],gpt-5.6-sol[1m]"
        hybrid_models = openai_models + ",claude-opus-5[1m],claude-sonnet-5[1m]"
        for name in ("Explore", "Plan", "general-purpose"):
            with self.subTest(name=name, mode="inherit"):
                self.assertIsNone(self.invoke("openai-pure", {"subagent_type": name}))
            with self.subTest(name=name, mode="openai"):
                self.assertIsNone(self.invoke(
                    "openai-pure",
                    {"subagent_type": name, "model": "gpt-5.6-luna[1m]"},
                    allowed_models=openai_models,
                ))
            with self.subTest(name=name, mode="hybrid-claude"):
                self.assertIsNone(self.invoke(
                    "hybrid-openai-root",
                    {"subagent_type": name, "model": "claude-sonnet-5[1m]"},
                    allowed_models=hybrid_models,
                ))

    def test_builtins_reject_missing_disabled_cross_profile_and_alias_models(self) -> None:
        allowed = "gpt-5.6-luna[1m],gpt-5.6-sol[1m]"
        for model, models in (
            ("gpt-5.6-luna[1m]", None),
            ("gpt-5.6-luna-fast[1m]", allowed),
            ("claude-opus-5[1m]", allowed),
            ("inherit", allowed),
            ("sonnet", allowed),
            (None, allowed),
        ):
            with self.subTest(model=model, models=models):
                self.assert_denied(self.invoke(
                    "openai-pure",
                    {"subagent_type": "Explore", "model": model},
                    allowed_models=models,
                ))
        for invalid in ("", "gpt-5.6-sol[1m],gpt-5.6-sol[1m]", "unknown-model"):
            with self.subTest(invalid=invalid):
                self.assert_denied(self.invoke(
                    "openai-pure",
                    {"subagent_type": "Plan", "model": "gpt-5.6-sol[1m]"},
                    allowed_models=invalid,
                ))

    def test_named_agents_reject_every_caller_model_override(self) -> None:
        self.assert_denied(self.invoke("openai-pure", {
            "subagent_type": "airlock-sol", "model": "gpt-5.6-sol[1m]",
        }))
        self.assert_denied(self.invoke("openai-pure", {
            "subagent_type": "airlock-sol", "model": None,
        }))

    def test_extra_usage_models_and_agents_require_exact_confirmation(self) -> None:
        marker = "Extra usage authorized: yes"
        models = "claude-fable-5[1m],claude-opus-5[1m]"
        self.assert_denied(self.invoke(
            "hybrid-openai-root",
            {"subagent_type": "Explore", "model": "claude-fable-5[1m]", "prompt": "research"},
            allowed_models=models,
            extra_models="claude-fable-5[1m]",
        ))
        self.assertIsNone(self.invoke(
            "hybrid-openai-root",
            {"subagent_type": "Explore", "model": "claude-fable-5[1m]", "prompt": marker},
            allowed_models=models,
            extra_models="claude-fable-5[1m]",
        ))
        self.assert_denied(self.invoke(
            "hybrid-openai-root",
            {"subagent_type": "airlock-fable", "prompt": "research"},
            extra_agents="airlock-fable",
        ))
        self.assertIsNone(self.invoke(
            "hybrid-openai-root",
            {"subagent_type": "airlock-fable", "prompt": marker},
            extra_agents="airlock-fable",
        ))

    def test_dynamic_enabled_worker_set_denies_disabled_workers(self) -> None:
        allowed = "airlock-luna,airlock-sol"
        self.assertIsNone(self.invoke(
            "openai-pure", {"subagent_type": "airlock-luna"}, allowed_agents=allowed,
        ))
        self.assert_denied(self.invoke(
            "openai-pure", {"subagent_type": "airlock-terra"}, allowed_agents=allowed,
        ))
        for invalid in ("", "airlock-sol,airlock-sol", "airlock-sol,Explore"):
            with self.subTest(invalid=invalid):
                self.assert_denied(self.invoke(
                    "openai-pure", {"subagent_type": "airlock-sol"}, allowed_agents=invalid,
                ))

    def test_shell_agent_guard_uses_explicit_python(self) -> None:
        bash = shutil.which("bash")
        self.assertIsNotNone(bash)
        environment = os.environ.copy()
        environment["AIRLOCK_PYTHON"] = sys.executable
        environment["AIRLOCK_ACTIVE_PROFILE"] = "openai-pure"
        environment.pop("AIRLOCK_ALLOWED_AGENT_NAMES", None)
        event = json.dumps({
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "airlock-terra"},
        })
        completed = subprocess.run(
            [bash, GUARD_SH.as_posix()], input=event, text=True,
            encoding="utf-8", capture_output=True,
            env=environment, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "")

    def test_shell_guards_fail_closed_on_broken_python_aliases(self) -> None:
        bash = shutil.which("bash")
        self.assertIsNotNone(bash)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for name in ("python", "python3"):
                alias = directory / name
                alias.write_text("#!/usr/bin/env bash\nexit 49\n", encoding="utf-8")
                alias.chmod(0o755)
            environment = os.environ.copy()
            environment.pop("AIRLOCK_PYTHON", None)
            environment["PATH"] = str(directory)
            event = json.dumps({
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "airlock-sonnet"},
            })
            for script in (GUARD_SH, SECRET_GUARD_SH):
                with self.subTest(script=script.name):
                    completed = subprocess.run(
                        [bash, script.as_posix()], input=event, text=True,
                        encoding="utf-8", capture_output=True,
                        env=environment, check=False,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    result = json.loads(completed.stdout)
                    self.assertEqual(
                        result["hookSpecificOutput"]["permissionDecision"],
                        "deny",
                    )

    def test_fails_closed_on_unknown_profile_or_malformed_event(self) -> None:
        self.assert_denied(self.invoke(None, {"subagent_type": "airlock-sol"}))
        self.assert_denied(self.invoke("not-a-profile", {"subagent_type": "airlock-sol"}))
        self.assert_denied(self.invoke("openai-pure", None))
        self.assert_denied(self.invoke("openai-pure", {"subagent_type": "airlock-sol"}, "Read"))

    def test_grok_pure_allows_only_grok_workers(self) -> None:
        for name in ("airlock-grok", "airlock-composer"):
            with self.subTest(name=name, allowed=True):
                self.assertIsNone(self.invoke("grok-pure", {"subagent_type": name}))
        for name in ("airlock-sol", "airlock-opus", "airlock-luna-fast", "airlock-sonnet"):
            with self.subTest(name=name, allowed=False):
                self.assert_denied(self.invoke("grok-pure", {"subagent_type": name}))

    def test_grok_pure_builtins_take_only_grok_model_overrides(self) -> None:
        grok_models = "grok-4.5,grok-composer-2.5-fast"
        for name in ("Explore", "Plan", "general-purpose"):
            with self.subTest(name=name, mode="inherit"):
                self.assertIsNone(self.invoke("grok-pure", {"subagent_type": name}))
            with self.subTest(name=name, mode="grok"):
                self.assertIsNone(self.invoke(
                    "grok-pure",
                    {"subagent_type": name, "model": "grok-4.5"},
                    allowed_models=grok_models,
                ))
            for model in ("gpt-5.6-sol[1m]", "claude-opus-5", "grok", "grok-4.5-latest"):
                with self.subTest(name=name, model=model):
                    self.assert_denied(self.invoke(
                        "grok-pure",
                        {"subagent_type": name, "model": model},
                        allowed_models=grok_models,
                    ))

    def test_hybrid_grok_root_reaches_every_provider(self) -> None:
        for name in (
            "airlock-grok", "airlock-composer", "airlock-sol", "airlock-opus",
        ):
            with self.subTest(name=name):
                self.assertIsNone(self.invoke("hybrid-grok-root", {"subagent_type": name}))
        every_model = (
            "grok-4.5,grok-composer-2.5-fast,gpt-5.6-sol[1m],claude-opus-5[1m]"
        )
        for model in ("grok-4.5", "gpt-5.6-sol[1m]", "claude-opus-5[1m]"):
            with self.subTest(model=model):
                self.assertIsNone(self.invoke(
                    "hybrid-grok-root",
                    {"subagent_type": "Explore", "model": model},
                    allowed_models=every_model,
                ))

    def test_named_grok_workers_reject_caller_model_overrides(self) -> None:
        # A named worker's identity binds its model, so a caller override would
        # let the card name and the billed model disagree.
        for profile in ("grok-pure", "hybrid-grok-root"):
            with self.subTest(profile=profile):
                self.assert_denied(self.invoke(profile, {
                    "subagent_type": "airlock-grok", "model": "grok-composer-2.5-fast",
                }))

    def test_grok_session_can_narrow_its_own_worker_set(self) -> None:
        # A config that enables only one Grok route must not leave the other one
        # reachable through the guard.
        self.assertIsNone(self.invoke(
            "grok-pure", {"subagent_type": "airlock-grok"},
            allowed_agents="airlock-grok",
        ))
        self.assert_denied(self.invoke(
            "grok-pure", {"subagent_type": "airlock-composer"},
            allowed_agents="airlock-grok",
        ))


if __name__ == "__main__":
    unittest.main()
