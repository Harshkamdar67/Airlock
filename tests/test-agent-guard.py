#!/usr/bin/env python3
"""Synthetic PreToolUse tests for the snapshot-backed Agent guard."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ACCESS_PATH = ROOT / "bin" / "airlock-access.py"
POLICY_PATH = ROOT / "bin" / "airlock_policy.py"
GUARD = ROOT / "plugins" / "airlock" / "scripts" / "agent-guard.py"
GUARD_SH = GUARD.with_suffix(".sh")
SECRET_GUARD_SH = GUARD.parent / "secret-guard.sh"
MARKER = "Extra usage authorized: yes"
ROOT_MODELS = {
    "openrouter-pure": "anthropic/claude-sonnet-4.5",
    "openai-pure": "gpt-5.6-sol",
    "grok-pure": "grok-4.6",
    "hybrid-openai-root": "gpt-5.6-sol",
    "hybrid-anthropic-root": "claude-sonnet-5[1m]",
    "hybrid-grok-root": "grok-4.6",
}
FAMILY_VARIABLES = {
    "fable": "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ACCESS = load_module("airlock_access_guard_test", ACCESS_PATH)
POLICY = load_module("airlock_policy_guard_test", POLICY_PATH)
GUARD_MODULE = load_module("airlock_agent_guard_test", GUARD)


class AgentGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.registry = self.root / "openrouter-registry.json"
        self.environment = {
            "AIRLOCK_CONFIG_FILE": str(self.root / "config"),
            "AIRLOCK_ACCESS_FILE": str(self.root / "access.json"),
            "AIRLOCK_OPENROUTER_REGISTRY_FILE": str(self.registry),
            "AIRLOCK_SESSION_RUNTIME_DIR": str(self.root / "runtime"),
            "AIRLOCK_ACCESS_GROK_AUTH": "1",
            "AIRLOCK_GROK_MODELS": "grok,composer",
        }
        self.patch = patch.dict(os.environ, self.environment, clear=False)
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.temporary.cleanup()

    def write_registry(self, *, include_other: bool = False) -> None:
        entries = [{
            "route": "declared-sonnet",
            "model": "anthropic/claude-sonnet-4.5",
            "endpoint_provider": "deepinfra/turbo",
            "provider_name": "DeepInfra",
            "provider_slug": "deepinfra",
            "quantization": "fp8",
            "canonical_slug": "anthropic/claude-sonnet-4.5-20250929",
            "alias_target": None,
            "supported_parameters": ["tool_choice", "tools"],
            "expiration_date": None,
            "checked_at": int(time.time()),
            "enabled": True,
        }]
        if include_other:
            entries.append({
                "route": "other",
                "model": "deepseek/deepseek-chat-v3-0324",
                "endpoint_provider": "deepinfra/turbo",
                "provider_name": "DeepInfra",
                "provider_slug": "deepinfra",
                "quantization": "fp8",
                "canonical_slug": "deepseek/deepseek-chat-v3-0324",
                "alias_target": None,
                "supported_parameters": ["tool_choice", "tools"],
                "expiration_date": None,
                "checked_at": int(time.time()),
                "enabled": True,
            })
        self.registry.write_text(json.dumps({
            "schema_version": 1,
            "models": entries,
        }), encoding="utf-8")

    def session(
        self,
        profile: str,
        *,
        openrouter: bool = False,
        openrouter_root_route: str | None = None,
        include_other_openrouter: bool = False,
    ) -> dict[str, object]:
        if openrouter:
            self.write_registry(include_other=include_other_openrouter)
        elif self.registry.exists():
            self.registry.unlink()
        policy = ACCESS.load_policy()
        root_model = ROOT_MODELS[profile]
        path, digest = ACCESS.write_session_snapshot(
            policy,
            profile,
            root_model,
            self.root / f"runtime-{profile}-{openrouter}",
            openrouter_root_route=openrouter_root_route,
        )
        snapshot = POLICY.load_session_snapshot(path, digest)
        return {
            "policy": policy,
            "snapshot": snapshot,
            "path": path,
            "digest": digest,
            "openrouter_root_route": openrouter_root_route,
        }

    def rewrite_snapshot(self, session: dict[str, object], **changes: object) -> None:
        value = session["snapshot"].to_dict()
        value.update(changes)
        raw = POLICY.canonical_json_bytes(value)
        path = session["path"]
        path.write_bytes(raw)
        session["digest"] = POLICY.sha256_bytes(raw)

    def invoke(
        self,
        session: dict[str, object] | None,
        tool_input: object,
        *,
        tool_name: str = "Agent",
        environment_changes: dict[str, str | None] | None = None,
        raw_event: str | None = None,
        digest: str | None = None,
    ) -> dict | None:
        environment = os.environ.copy()
        managed_variables = {
            "AIRLOCK_ACTIVE_PROFILE",
            "AIRLOCK_ALLOWED_AGENT_NAMES",
            "AIRLOCK_ALLOWED_AGENT_MODELS",
            "AIRLOCK_EXTRA_USAGE_AGENT_NAMES",
            "AIRLOCK_EXTRA_USAGE_AGENT_MODELS",
            "AIRLOCK_DISCOVERY_MODEL",
            "AIRLOCK_ROOT_MODEL",
            "ANTHROPIC_SMALL_FAST_MODEL",
            "AIRLOCK_POLICY_HELPER",
            "AIRLOCK_SESSION_SNAPSHOT",
            "AIRLOCK_SESSION_SNAPSHOT_SHA256",
            *FAMILY_VARIABLES.values(),
        }
        for variable in managed_variables:
            environment.pop(variable, None)
        if session is not None:
            snapshot = session["snapshot"]
            policy = session["policy"]
            agents = snapshot.agents
            environment.update({
                "AIRLOCK_ACTIVE_PROFILE": snapshot.profile,
                "AIRLOCK_ALLOWED_AGENT_NAMES": ",".join(sorted(agents)),
                "AIRLOCK_ALLOWED_AGENT_MODELS": ",".join(sorted({
                    agent.model for agent in agents.values()
                })),
                "AIRLOCK_EXTRA_USAGE_AGENT_NAMES": ",".join(sorted(
                    name for name, agent in agents.items() if agent.extra_usage
                )),
                "AIRLOCK_EXTRA_USAGE_AGENT_MODELS": ",".join(sorted({
                    agent.model for agent in agents.values() if agent.extra_usage
                })),
                "AIRLOCK_ROOT_MODEL": snapshot.root_model,
                "AIRLOCK_POLICY_HELPER": str(POLICY_PATH),
                "AIRLOCK_SESSION_SNAPSHOT": str(session["path"]),
                "AIRLOCK_SESSION_SNAPSHOT_SHA256": digest or str(session["digest"]),
            })
            family_models = ACCESS.proxy_picker_models(
                policy,
                snapshot.profile,
                openrouter_root_route=session.get("openrouter_root_route"),
            )
            for family, model in family_models.items():
                environment[FAMILY_VARIABLES[family]] = model
            discovery = ACCESS.discovery_model(
                policy,
                snapshot.profile,
                openrouter_root_route=session.get("openrouter_root_route"),
            )
            if discovery:
                environment["AIRLOCK_DISCOVERY_MODEL"] = discovery
            if snapshot.profile == "openrouter-pure":
                environment["ANTHROPIC_SMALL_FAST_MODEL"] = snapshot.root_model
        for variable, value in (environment_changes or {}).items():
            if value is None:
                environment.pop(variable, None)
            else:
                environment[variable] = value
        event = raw_event
        if event is None:
            event = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
        completed = subprocess.run(
            [sys.executable, str(GUARD)],
            input=event,
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=environment,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout) if completed.stdout.strip() else None

    def assert_denied(self, result: dict | None, contains: str | None = None) -> None:
        self.assertIsNotNone(result)
        decision = result["hookSpecificOutput"]
        self.assertEqual(decision["hookEventName"], "PreToolUse")
        self.assertEqual(decision["permissionDecision"], "deny")
        if contains:
            self.assertIn(contains, decision["permissionDecisionReason"])

    def test_protocol_version_matches_snapshot_producer(self) -> None:
        self.assertEqual(
            GUARD_MODULE.MANAGED_PROTOCOL_VERSION,
            ACCESS.MANAGED_PROTOCOL_VERSION,
        )

    def test_all_profiles_accept_their_exact_workers_and_builtins(self) -> None:
        for profile in ROOT_MODELS:
            if profile == "openrouter-pure":
                continue
            session = self.session(profile)
            for name, agent in session["snapshot"].agents.items():
                prompt = MARKER if agent.extra_usage else "work"
                with self.subTest(profile=profile, agent=name):
                    self.assertIsNone(self.invoke(
                        session, {"subagent_type": name, "prompt": prompt}
                    ))
            for name in ("Explore", "Plan", "general-purpose"):
                with self.subTest(profile=profile, builtin=name):
                    self.assertIsNone(self.invoke(
                        session, {"subagent_type": name, "model": "haiku"}
                    ))

    def test_declared_openrouter_worker_is_exact_named_and_always_extra(self) -> None:
        session = self.session("hybrid-anthropic-root", openrouter=True)
        name = "airlock-or-declared-sonnet"
        self.assert_denied(self.invoke(
            session, {"subagent_type": name, "prompt": "work"}
        ), "extra-usage")
        self.assertIsNone(self.invoke(
            session, {"subagent_type": name, "prompt": MARKER}
        ))
        self.assert_denied(self.invoke(
            session,
            {
                "subagent_type": name,
                "model": "anthropic/claude-sonnet-4.5",
                "prompt": MARKER,
            },
        ), "caller model overrides")

    def test_openrouter_pure_selected_root_maps_all_aliases_discovery_and_small_fast(self) -> None:
        session = self.session(
            "openrouter-pure",
            openrouter=True,
            openrouter_root_route="declared-sonnet",
        )
        root = session["snapshot"].root_model
        self.assertEqual(root, "anthropic/claude-sonnet-4.5")
        self.assertEqual(
            ACCESS.proxy_picker_models(
                session["policy"],
                "openrouter-pure",
                openrouter_root_route="declared-sonnet",
            ),
            {family: root for family in FAMILY_VARIABLES},
        )
        self.assertEqual(
            ACCESS.discovery_model(
                session["policy"],
                "openrouter-pure",
                openrouter_root_route="declared-sonnet",
            ),
            root,
        )
        for model in (*FAMILY_VARIABLES, root):
            with self.subTest(model=model):
                self.assertIsNone(self.invoke(
                    session, {"subagent_type": "Plan", "model": model}
                ))
        self.assertIsNone(self.invoke(session, {"subagent_type": "Explore"}))

    def test_openrouter_pure_rejects_every_other_openrouter_family_model(self) -> None:
        session = self.session(
            "openrouter-pure",
            openrouter=True,
            openrouter_root_route="declared-sonnet",
            include_other_openrouter=True,
        )
        other = "deepseek/deepseek-chat-v3-0324"
        for family, variable in FAMILY_VARIABLES.items():
            with self.subTest(family=family):
                self.assert_denied(self.invoke(
                    session,
                    {"subagent_type": "Plan", "model": family},
                    environment_changes={variable: other},
                ), "family model map")
        self.assert_denied(self.invoke(
            session, {"subagent_type": "Plan", "model": other}
        ), "configured family alias")

    def test_openrouter_pure_root_agent_is_free_and_extra_agent_requires_marker(self) -> None:
        session = self.session(
            "openrouter-pure",
            openrouter=True,
            openrouter_root_route="declared-sonnet",
            include_other_openrouter=True,
        )
        self.assertIsNone(self.invoke(
            session,
            {"subagent_type": "airlock-or-declared-sonnet", "prompt": "work"},
        ))
        self.assert_denied(self.invoke(
            session,
            {"subagent_type": "airlock-or-other", "prompt": "work"},
        ), "extra-usage")
        self.assertIsNone(self.invoke(
            session,
            {
                "subagent_type": "airlock-or-other",
                "prompt": MARKER,
            },
        ))

    def test_openrouter_pure_snapshot_and_environment_mismatches_fail_closed(self) -> None:
        session = self.session(
            "openrouter-pure",
            openrouter=True,
            openrouter_root_route="declared-sonnet",
        )
        cases = [
            ({"digest": "0" * 64}, "permission set"),
            (
                {"environment_changes": {"AIRLOCK_ACTIVE_PROFILE": "openai-pure"}},
                "permission set",
            ),
            (
                {"environment_changes": {"AIRLOCK_ROOT_MODEL": "other/model"}},
                "permission set",
            ),
            (
                {"environment_changes": {"AIRLOCK_DISCOVERY_MODEL": "other/model"}},
                "root model map",
            ),
            (
                {"environment_changes": {"ANTHROPIC_SMALL_FAST_MODEL": "other/model"}},
                "root model map",
            ),
        ]
        for options, reason in cases:
            with self.subTest(options=options):
                self.assert_denied(self.invoke(
                    session,
                    {"subagent_type": "Plan", "model": "sonnet"},
                    **options,
                ), reason)

    def test_hybrid_profiles_still_block_openrouter_family_aliases(self) -> None:
        session = self.session("hybrid-anthropic-root", openrouter=True)
        other = "anthropic/claude-sonnet-4.5"
        for family, variable in FAMILY_VARIABLES.items():
            with self.subTest(family=family):
                self.assert_denied(self.invoke(
                    session,
                    {"subagent_type": "Plan", "model": family},
                    environment_changes={variable: other},
                ), "family model map")

        session = self.session("hybrid-anthropic-root", openrouter=True)
        result = self.invoke(session, {
            "subagent_type": "general-purpose",
            "model": "anthropic/claude-sonnet-4.5",
            "prompt": MARKER,
        })
        self.assert_denied(result, "configured family alias")

    def test_undeclared_and_cross_profile_workers_are_denied(self) -> None:
        session = self.session("openai-pure")
        for name in ("airlock-or-not-declared", "airlock-opus", "claude", "unknown"):
            with self.subTest(name=name):
                self.assert_denied(self.invoke(
                    session, {"subagent_type": name, "prompt": MARKER}
                ))

    def test_named_workers_reject_every_model_override(self) -> None:
        session = self.session("openai-pure")
        for model in ("gpt-5.6-sol", None):
            with self.subTest(model=model):
                self.assert_denied(self.invoke(session, {
                    "subagent_type": "airlock-sol", "model": model,
                }), "caller model overrides")

    def test_builtin_aliases_and_exact_non_openrouter_models_are_accepted(self) -> None:
        session = self.session("hybrid-anthropic-root")
        family = ACCESS.proxy_picker_models(
            session["policy"], "hybrid-anthropic-root"
        )
        for model in (*FAMILY_VARIABLES, family["sonnet"]):
            with self.subTest(model=model):
                self.assertIsNone(self.invoke(session, {
                    "subagent_type": "Plan", "model": model,
                }))
        self.assert_denied(self.invoke(session, {
            "subagent_type": "Plan", "model": "not-a-route",
        }), "configured family alias")

    def test_unpinned_explore_uses_snapshot_bound_discovery_policy(self) -> None:
        session = self.session("hybrid-anthropic-root")
        result = self.invoke(session, {"subagent_type": "Explore"})
        discovery = ACCESS.discovery_model(
            session["policy"], session["snapshot"].profile
        )
        if discovery == session["snapshot"].root_model:
            self.assertIsNone(result)
        else:
            self.assert_denied(result, 'model: "haiku"')
        self.assertIsNone(self.invoke(session, {
            "subagent_type": "Explore", "model": "haiku",
        }))
        self.assertIsNone(self.invoke(session, {"subagent_type": "Plan"}))

    def test_missing_invalid_or_mismatched_snapshot_fails_closed(self) -> None:
        session = self.session("openai-pure")
        self.assert_denied(self.invoke(None, {"subagent_type": "airlock-sol"}))
        self.assert_denied(self.invoke(
            session,
            {"subagent_type": "airlock-sol"},
            digest="0" * 64,
        ), "permission set")
        self.assert_denied(self.invoke(
            session,
            {"subagent_type": "airlock-sol"},
            environment_changes={"AIRLOCK_ACTIVE_PROFILE": "grok-pure"},
        ), "permission set")
        self.assert_denied(self.invoke(
            session,
            {"subagent_type": "airlock-sol"},
            environment_changes={"AIRLOCK_ROOT_MODEL": "gpt-5.6-luna"},
        ), "permission set")
        self.assert_denied(self.invoke(
            session,
            {"subagent_type": "airlock-sol"},
            environment_changes={"AIRLOCK_POLICY_HELPER": None},
        ), "permission set")

    def test_a_snapshot_from_an_older_airlock_asks_for_a_restart(self) -> None:
        # Installing Airlock while a session is running leaves that session
        # with a snapshot the new schema serializes differently, so the digest
        # stops matching and every Agent call is denied for the rest of it.
        # The denial has to name the cause, because the fix is a restart and
        # nothing in the session hints at that.
        session = self.session("openai-pure")
        value = session["snapshot"].to_dict()
        for field in ("compactors", "context_windows", "overflow_shrink"):
            value.pop(field, None)
        raw = POLICY.canonical_json_bytes(value)
        session["path"].write_bytes(raw)
        session["digest"] = POLICY.sha256_bytes(raw)
        self.assert_denied(
            self.invoke(session, {"subagent_type": "airlock-sol"}),
            "Restart the session",
        )

    def test_an_edited_snapshot_is_never_reported_as_an_upgrade(self) -> None:
        # The restart wording is only safe because it cannot be reached by
        # editing the file: the recorded digest still describes the authentic
        # bytes, so an edit fails that comparison and stays generic.
        session = self.session("openai-pure")
        value = session["snapshot"].to_dict()
        value["agents"] = dict(value["agents"])
        value["agents"]["airlock-intruder"] = {
            "model": "gpt-5.6-sol",
            "provider": "openai",
            "extra_usage": False,
        }
        session["path"].write_bytes(POLICY.canonical_json_bytes(value))
        result = self.invoke(session, {"subagent_type": "airlock-sol"})
        self.assert_denied(result, "permission set is invalid")
        self.assertNotIn(
            "Restart", result["hookSpecificOutput"]["permissionDecisionReason"]
        )

    def test_protocol_mismatch_fails_closed(self) -> None:
        session = self.session("openai-pure")
        self.rewrite_snapshot(
            session,
            protocol_version=GUARD_MODULE.MANAGED_PROTOCOL_VERSION + 1,
        )
        self.assert_denied(self.invoke(
            session, {"subagent_type": "airlock-sol"}
        ), "permission set")

    def test_every_environment_allow_list_must_match_snapshot_exactly(self) -> None:
        session = self.session("hybrid-anthropic-root", openrouter=True)
        variables = (
            "AIRLOCK_ALLOWED_AGENT_NAMES",
            "AIRLOCK_ALLOWED_AGENT_MODELS",
            "AIRLOCK_EXTRA_USAGE_AGENT_NAMES",
            "AIRLOCK_EXTRA_USAGE_AGENT_MODELS",
        )
        for variable in variables:
            for value in (None, "", "wrong", "wrong,wrong"):
                with self.subTest(variable=variable, value=value):
                    self.assert_denied(self.invoke(
                        session,
                        {"subagent_type": "airlock-sonnet"},
                        environment_changes={variable: value},
                    ), "permission set")

    def test_family_map_must_resolve_to_non_openrouter_snapshot_models(self) -> None:
        session = self.session("hybrid-anthropic-root", openrouter=True)
        for value in (None, "unknown", "anthropic/claude-sonnet-4.5"):
            with self.subTest(value=value):
                self.assert_denied(self.invoke(
                    session,
                    {"subagent_type": "Plan", "model": "haiku"},
                    environment_changes={"ANTHROPIC_DEFAULT_HAIKU_MODEL": value},
                ), "family model map")

    def test_duplicate_key_malformed_and_unexpected_events_fail_closed(self) -> None:
        session = self.session("openai-pure")
        duplicate = (
            '{"tool_name":"Agent","tool_name":"Read",'
            '"tool_input":{"subagent_type":"airlock-sol"}}'
        )
        self.assert_denied(self.invoke(
            session, {}, raw_event=duplicate
        ), "malformed")
        self.assert_denied(self.invoke(
            session, None
        ), "malformed Agent tool input")
        self.assert_denied(self.invoke(
            session, {"subagent_type": "airlock-sol"}, tool_name="Read"
        ), "unexpected tool event")

    def test_oversized_event_fails_closed(self) -> None:
        session = self.session("openai-pure")
        event = json.dumps({
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "airlock-sol",
                "prompt": "x" * (GUARD_MODULE.MAX_EVENT_BYTES + 1),
            },
        })
        self.assert_denied(self.invoke(
            session, {}, raw_event=event
        ), "oversized")

    def test_shell_guard_uses_explicit_python_and_snapshot(self) -> None:
        bash = shutil.which("bash")
        self.assertIsNotNone(bash)
        session = self.session("openai-pure")
        environment = os.environ.copy()
        environment["AIRLOCK_PYTHON"] = sys.executable
        snapshot = session["snapshot"]
        environment.update({
            "AIRLOCK_ACTIVE_PROFILE": snapshot.profile,
            "AIRLOCK_ALLOWED_AGENT_NAMES": ",".join(sorted(snapshot.agents)),
            "AIRLOCK_ALLOWED_AGENT_MODELS": ",".join(sorted({
                agent.model for agent in snapshot.agents.values()
            })),
            "AIRLOCK_EXTRA_USAGE_AGENT_NAMES": "",
            "AIRLOCK_EXTRA_USAGE_AGENT_MODELS": "",
            "AIRLOCK_ROOT_MODEL": snapshot.root_model,
            "AIRLOCK_POLICY_HELPER": str(POLICY_PATH),
            "AIRLOCK_SESSION_SNAPSHOT": str(session["path"]),
            "AIRLOCK_SESSION_SNAPSHOT_SHA256": str(session["digest"]),
        })
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
                        result["hookSpecificOutput"]["permissionDecision"], "deny"
                    )


class NestedAgentGuardTests(AgentGuardTests):
    """A worker's own Agent calls stay pinned to the worker's own model."""

    def nested(self, session, caller: str, tool_input: dict) -> dict | None:
        # Claude Code adds agent_id and agent_type when the caller is a worker.
        return self.invoke(session, None, raw_event=json.dumps({
            "tool_name": "Agent",
            "tool_input": tool_input,
            "agent_id": "a1234567890abcdef",
            "agent_type": caller,
        }))

    def first_worker(self, session) -> tuple[str, object]:
        for name, agent in session["snapshot"].agents.items():
            return name, agent
        raise AssertionError("session has no workers")

    def test_worker_may_spawn_its_own_type(self) -> None:
        session = self.session("hybrid-anthropic-root")
        name, agent = self.first_worker(session)
        prompt = MARKER if agent.extra_usage else "work"
        self.assertIsNone(
            self.nested(session, name, {"subagent_type": name, "prompt": prompt})
        )

    def test_worker_cannot_spawn_a_different_type(self) -> None:
        session = self.session("hybrid-anthropic-root")
        names = list(session["snapshot"].agents)
        self.assertGreater(len(names), 1)
        result = self.nested(
            session, names[0], {"subagent_type": names[1], "prompt": MARKER}
        )
        self.assertIsNotNone(result)
        self.assertEqual(
            result["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_worker_cannot_spawn_a_builtin_agent(self) -> None:
        session = self.session("hybrid-anthropic-root")
        name, _agent = self.first_worker(session)
        result = self.nested(session, name, {"subagent_type": "Explore"})
        self.assertIsNotNone(result)
        self.assertEqual(
            result["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_worker_cannot_override_the_model(self) -> None:
        session = self.session("hybrid-anthropic-root")
        name, _agent = self.first_worker(session)
        result = self.nested(
            session, name,
            {"subagent_type": name, "model": "haiku", "prompt": MARKER},
        )
        self.assertIsNotNone(result)
        self.assertEqual(
            result["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_blank_caller_identity_is_rejected(self) -> None:
        session = self.session("hybrid-anthropic-root")
        name, _agent = self.first_worker(session)
        result = self.nested(session, "", {"subagent_type": name, "prompt": MARKER})
        self.assertIsNotNone(result)
        self.assertEqual(
            result["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_root_calls_are_unaffected(self) -> None:
        # No agent_type means the root is calling, which may pick any worker.
        session = self.session("hybrid-anthropic-root")
        for name, agent in session["snapshot"].agents.items():
            prompt = MARKER if agent.extra_usage else "work"
            with self.subTest(agent=name):
                self.assertIsNone(self.invoke(
                    session, {"subagent_type": name, "prompt": prompt}
                ))


if __name__ == "__main__":
    unittest.main()
