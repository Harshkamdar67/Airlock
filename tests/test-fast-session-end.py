#!/usr/bin/env python3
"""Offline tests for Airlock's managed Fast SessionEnd hook."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins" / "airlock" / "scripts" / "fast-session-end.py"
SKILL = ROOT / "plugins" / "airlock" / "skills" / "airlock-fast" / "SKILL.md"
HOOKS = ROOT / "plugins" / "airlock" / "hooks" / "hooks.json"

spec = importlib.util.spec_from_file_location("airlock_fast_session_end_test", SCRIPT)
assert spec and spec.loader
HOOK = importlib.util.module_from_spec(spec)
spec.loader.exec_module(HOOK)


class FastSessionEndTests(unittest.TestCase):
    def event(self, reason: str = "prompt_input_exit") -> bytes:
        return json.dumps({"session_id": "session-1", "cwd": str(ROOT), "reason": reason}).encode()

    def environment(self, helper: Path) -> dict[str, str]:
        return {
            HOOK.HELPER_ENV: str(helper),
            HOOK.CHANNEL_ENV: "fast-transition-" + "a" * 32 + ".json",
            HOOK.NONCE_ENV: "b" * 64,
            HOOK.PYTHON_ENV: "python",
        }

    def run_hook(self, raw: bytes, environment: dict[str, str] | None = None):
        with patch.dict(os.environ, environment or {}, clear=environment is not None), patch.object(HOOK.subprocess, "run") as run:
            with patch.object(HOOK, "_read_event", return_value=json.loads(raw.decode("utf-8")) if len(raw) <= HOOK.MAX_EVENT_BYTES and raw != b"not json" else None):
                HOOK.main()
            return run

    def test_malformed_oversized_and_nonclean_inputs_noop(self) -> None:
        for raw in (b"not json", b"x" * (HOOK.MAX_EVENT_BYTES + 1), self.event("other")):
            with self.subTest(raw=raw[:12]):
                run = self.run_hook(raw)
                run.assert_not_called()

    def test_no_channel_or_helper_noop(self) -> None:
        run = self.run_hook(self.event(), {})
        run.assert_not_called()

    def test_exact_helper_command_payload_and_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "airlock-access.py"
            helper.write_text("# stub\n", encoding="utf-8")
            env = self.environment(helper)
            with patch.dict(os.environ, env, clear=False), patch.object(HOOK, "_read_event", return_value=json.loads(self.event().decode())), patch.object(HOOK.subprocess, "run") as run:
                run.side_effect = [subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0)]
                self.assertEqual(HOOK.main(), 0)
                command = run.call_args_list[1].args[0]
                self.assertEqual(command, ["python", str(helper), "fast-transition-finalize"])
                self.assertEqual(json.loads(run.call_args_list[1].kwargs["input"]), {
                    "session_id": "session-1", "cwd": str(ROOT), "reason": "prompt_input_exit"
                })
                self.assertEqual(run.call_args_list[1].kwargs["env"][HOOK.CHANNEL_ENV], env[HOOK.CHANNEL_ENV])

    def test_skill_and_hook_metadata(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("disable-model-invocation: true", text)
        self.assertIn('allowed-tools: Bash(airlock fast --arm --session-id "${CLAUDE_SESSION_ID}")', text)
        self.assertIn("must exit cleanly", text)
        hooks = json.loads(HOOKS.read_text(encoding="utf-8"))
        command = hooks["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
        self.assertEqual(command, 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/fast-session-end.sh"')


if __name__ == "__main__":
    unittest.main()
