#!/usr/bin/env python3
"""Non-model tests for sensitive env key-only projection hooks."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "plugins" / "airlock" / "scripts" / "secret-guard.py"


class SecretGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".env").write_text(
            "DATABASE_URL=postgres://private\nexport STRIPE_SECRET_KEY='sk_private'\nDATABASE_URL=duplicate\n",
            encoding="utf-8",
        )
        (self.root / ".env.example").write_text("SAFE_EXAMPLE=placeholder\n", encoding="utf-8")
        (self.root / "credentials.json").write_text(
            '{"access_token":"credential-private"}\n', encoding="utf-8"
        )
        (self.root / "application.json").write_text(
            '{"client_secret":"structured-private"}\n', encoding="utf-8"
        )
        (self.root / "settings.json").write_text('{"mode":"test"}\n', encoding="utf-8")
        proxy_auth = self.root / ".airlock" / "claude-code-proxy" / "codex" / "auth.json"
        proxy_auth.parent.mkdir(parents=True)
        proxy_auth.write_text('{"mode":"synthetic"}\n', encoding="utf-8")
        (self.root / "app.py").write_text("print('safe')\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def invoke(self, event: object, *, environment: dict[str, str] | None = None) -> dict | None:
        env = os.environ.copy()
        if environment:
            env.update(environment)
        completed = subprocess.run(
            [sys.executable, str(GUARD)], cwd=self.root, env=env,
            input=json.dumps(event), text=True, encoding="utf-8",
            capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout) if completed.stdout.strip() else None

    def reason(self, payload: dict) -> str:
        return payload["hookSpecificOutput"]["permissionDecisionReason"]

    def test_read_returns_keys_without_values_or_value_metadata(self) -> None:
        payload = self.invoke({
            "tool_name": "Read",
            "tool_input": {"file_path": str(self.root / ".env")},
            "cwd": str(self.root),
        })
        self.assertIsNotNone(payload)
        reason = self.reason(payload)
        self.assertIn('"keys":["DATABASE_URL","STRIPE_SECRET_KEY"]', reason)
        self.assertIn('"values_exposed":false', reason)
        self.assertNotIn("postgres://private", reason)
        self.assertNotIn("sk_private", reason)
        self.assertNotIn("duplicate", reason)

    def test_broad_grep_and_shell_access_are_denied_but_safe_glob_is_allowed(self) -> None:
        broad = self.invoke({
            "tool_name": "Grep", "tool_input": {"pattern": "private", "path": str(self.root)},
            "cwd": str(self.root),
        })
        self.assertIn("could return raw", self.reason(broad))
        scoped = self.invoke({
            "tool_name": "Grep",
            "tool_input": {"pattern": "safe", "path": str(self.root), "glob": "*.py"},
            "cwd": str(self.root),
        })
        self.assertIsNone(scoped)
        shell = self.invoke({
            "tool_name": "Bash", "tool_input": {"command": "cat .env; printf done"},
            "cwd": str(self.root),
        })
        self.assertIn("blocked shell access", self.reason(shell))

    def test_safe_schema_and_configured_sensitive_file_behavior(self) -> None:
        safe = self.invoke({
            "tool_name": "Read", "tool_input": {"file_path": str(self.root / ".env.example")},
            "cwd": str(self.root),
        })
        self.assertIsNone(safe)
        configured = self.root / "config" / "private.settings"
        configured.parent.mkdir()
        configured.write_text("CUSTOM_TOKEN=value\n", encoding="utf-8")
        payload = self.invoke({
            "tool_name": "Read", "tool_input": {"file_path": str(configured)},
            "cwd": str(self.root),
        }, environment={"AIRLOCK_SENSITIVE_ENV_FILES": "config/private.settings"})
        reason = self.reason(payload)
        self.assertIn("CUSTOM_TOKEN", reason)
        self.assertNotIn("CUSTOM_TOKEN=value", reason)

    def test_credential_paths_and_structured_tokens_are_denied(self) -> None:
        named = self.invoke({
            "tool_name": "Read",
            "tool_input": {"file_path": str(self.root / "credentials.json")},
            "cwd": str(self.root),
        })
        named_reason = self.reason(named)
        self.assertIn("credential-bearing", named_reason)
        self.assertNotIn("credentials.json", named_reason)
        self.assertNotIn("credential-private", named_reason)

        structured = self.invoke({
            "tool_name": "Read",
            "tool_input": {"file_path": str(self.root / "application.json")},
            "cwd": str(self.root),
        })
        self.assertIn("credential-bearing", self.reason(structured))
        self.assertNotIn("structured-private", self.reason(structured))

        proxy_auth = self.invoke({
            "tool_name": "Read",
            "tool_input": {
                "file_path": str(
                    self.root / ".airlock" / "claude-code-proxy" / "codex" / "auth.json"
                )
            },
            "cwd": str(self.root),
        })
        self.assertIn("credential-bearing", self.reason(proxy_auth))
        self.assertNotIn("auth.json", self.reason(proxy_auth))

        safe = self.invoke({
            "tool_name": "Read",
            "tool_input": {"file_path": str(self.root / "settings.json")},
            "cwd": str(self.root),
        })
        self.assertIsNone(safe)

        globbed = self.invoke({
            "tool_name": "Glob",
            "tool_input": {"pattern": "**/*.json", "path": str(self.root)},
            "cwd": str(self.root),
        })
        self.assertIn("could return raw", self.reason(globbed))

        shell = self.invoke({
            "tool_name": "Bash",
            "tool_input": {"command": "git show HEAD:credentials.json"},
            "cwd": str(self.root),
        })
        self.assertIn("credential-bearing", self.reason(shell))

    def test_malformed_event_fails_closed(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(GUARD)], cwd=self.root, input="not-json",
            text=True, encoding="utf-8", capture_output=True, check=False,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
