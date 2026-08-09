#!/usr/bin/env python3
"""Offline tests for the guarded Sol long-context proof helper."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "scripts" / "test-sol-long-context.py"
SPEC = importlib.util.spec_from_file_location("airlock_sol_context_test", HELPER_PATH)
assert SPEC and SPEC.loader
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


class SolLongContextProofTests(unittest.TestCase):
    def test_prompt_is_synthetic_in_memory_and_places_both_markers(self) -> None:
        prompt = HELPER.build_prompt(4096)
        self.assertIn(HELPER.EARLY_MARKER, prompt[:512])
        self.assertIn(HELPER.LATE_MARKER, prompt[-512:])
        self.assertGreaterEqual(len(prompt), 4096)
        self.assertNotIn(str(ROOT), prompt)

    def test_stream_parser_returns_only_sanitized_usage_and_marker_state(self) -> None:
        events = [
            {
                "type": "system",
                "model": HELPER.MODEL,
                "private_path": "must-not-leak",
            },
            {
                "type": "assistant",
                "message": {
                    "model": HELPER.WIRE_MODEL,
                    "content": [
                        {
                            "type": "text",
                            "text": f"{HELPER.EARLY_MARKER} {HELPER.LATE_MARKER}",
                        }
                    ],
                    "usage": {
                        "input_tokens": 9,
                        "cache_read_input_tokens": 310001,
                        "output_tokens": 2,
                    },
                },
            },
            {
                "type": "result",
                "result": f"{HELPER.EARLY_MARKER} {HELPER.LATE_MARKER}",
                "usage": {"output_tokens": 3},
            },
        ]
        result = HELPER.sanitized_stream_result(
            "\n".join(json.dumps(event) for event in events)
        )
        HELPER.validate_proof(result)
        rendered = json.dumps(result)
        self.assertNotIn("must-not-leak", rendered)
        self.assertEqual(result["observed_input_and_cache_tokens"], 310010)
        self.assertEqual(result["usage"]["output_tokens"], 3)

    def test_validation_rejects_short_usage_missing_markers_and_wrong_model(self) -> None:
        base = {
            "observed_models": [HELPER.MODEL],
            "result_seen": True,
            "early_marker_seen": True,
            "late_marker_seen": True,
            "observed_input_and_cache_tokens": 300001,
        }
        for field, value in (
            ("observed_input_and_cache_tokens", 300000),
            ("early_marker_seen", False),
            ("late_marker_seen", False),
            ("result_seen", False),
            ("observed_models", ["gpt-wrong"]),
        ):
            candidate = dict(base)
            candidate[field] = value
            with self.subTest(field=field), self.assertRaises(HELPER.ProofError):
                HELPER.validate_proof(candidate)

    def test_missing_exact_authorization_fails_before_resolving_a_launcher(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(HELPER_PATH)],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("explicit authorization is missing", completed.stderr)
        self.assertEqual(completed.stdout, "")

    def test_proof_environment_clears_inherited_context_caps_and_failover(self) -> None:
        environment = HELPER.proof_environment({
            "KEEP": "yes",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "272000",
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "272000",
            "CLAUDE_CODE_DISABLE_1M_CONTEXT": "1",
            "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT": "1",
            "AIRLOCK_CONTEXT_WINDOW": "272000",
        })
        self.assertEqual(environment["KEEP"], "yes")
        for variable in (
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
            "CLAUDE_CODE_DISABLE_1M_CONTEXT",
            "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT",
            "AIRLOCK_CONTEXT_WINDOW",
        ):
            self.assertNotIn(variable, environment)
        self.assertEqual(environment["AIRLOCK_OPENAI_FAST"], "off")
        self.assertEqual(environment["AIRLOCK_EXTRA_USAGE_POLICY"], "never")
        self.assertEqual(environment["AIRLOCK_FAILOVER_POLICY"], "never")
        self.assertEqual(environment["AIRLOCK_MAX_CONCURRENT_SUBAGENTS"], "off")

    def test_failure_classification_never_returns_raw_error_text(self) -> None:
        cases = {
            "maximum context window exceeded for secret-value": "context_limit",
            "RATE LIMIT from account private-name": "rate_limited",
            "authentication failed for private-name": "authentication",
            "unsupported model private-name": "model_unavailable",
            "unknown option private-name": "invalid_cli",
            "arbitrary provider body private-name": "request_failed",
        }
        for raw_error, expected in cases.items():
            with self.subTest(expected=expected):
                category = HELPER.sanitized_failure_category("", raw_error)
                self.assertEqual(category, expected)
                self.assertNotIn("private-name", category)
                self.assertNotIn("secret-value", category)

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher selection")
    def test_windows_cmd_path_uses_its_sibling_powershell_launcher(self) -> None:
        with tempfile.TemporaryDirectory(prefix="airlock proof path ") as directory:
            cmd = Path(directory) / "airlock.cmd"
            powershell = Path(directory) / "airlock.ps1"
            cmd.write_text("@exit /b 0\n", encoding="utf-8")
            powershell.write_text("exit 0\n", encoding="utf-8")
            command = HELPER.command_for_launcher(str(cmd))
        self.assertEqual(command[:4], [
            "powershell", "-NoProfile", "-File", str(powershell)
        ])
        self.assertNotIn("cmd.exe", command)

    def test_launcher_command_disables_tools_mcp_persistence_and_extra_turns(self) -> None:
        command = HELPER.command_for_launcher("/safe/airlock")
        rendered = " ".join(command)
        self.assertIn("openai sol", rendered)
        self.assertIn("--max-turns 1", rendered)
        self.assertIn("--no-session-persistence", command)
        self.assertIn("--disable-slash-commands", command)
        self.assertIn("--strict-mcp-config", command)
        self.assertEqual(command[command.index("--tools") + 1], "")


if __name__ == "__main__":
    unittest.main()
