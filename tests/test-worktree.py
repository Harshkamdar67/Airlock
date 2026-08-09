#!/usr/bin/env python3
"""Offline tests for filtered native worktree hooks."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "plugins" / "airlock" / "scripts" / "worktree.py"
HOOKS = ROOT / "plugins" / "airlock" / "hooks" / "hooks.json"


class WorktreeHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init")
        self.git("config", "user.name", "Airlock Test")
        self.git("config", "user.email", "test@localhost")
        (self.repo / ".gitignore").write_text(
            ".env\nignored.txt\n.claude/worktrees/\n",
            encoding="utf-8",
        )
        (self.repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        (self.repo / ".env").write_text("BASELINE_KEY=private-baseline\n", encoding="utf-8")
        (self.repo / "credentials.json").write_text(
            '{"access_token":"tracked-private"}\n', encoding="utf-8"
        )
        self.git("add", ".gitignore", "tracked.txt", "credentials.json")
        self.git("add", "--force", ".env")
        self.git("commit", "-m", "baseline")
        self.before_head = self.git("rev-parse", "HEAD").stdout.strip()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=check,
        )

    def invoke(
        self,
        operation: str,
        event: dict[str, object],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [sys.executable, str(MANAGER), operation],
            cwd=self.repo,
            input=json.dumps(event),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        if check:
            self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed

    def create(self, name: str = "agent-test") -> tuple[Path, subprocess.CompletedProcess[str]]:
        completed = self.invoke("create", {
            "session_id": "session-test",
            "cwd": str(self.repo.resolve()),
            "hook_event_name": "WorktreeCreate",
            "name": name,
            "agent_id": "agent-1",
            "agent_type": "airlock-luna",
        })
        worktree = Path(completed.stdout.strip())
        self.assertTrue(worktree.is_absolute())
        self.assertTrue(worktree.is_dir())
        return worktree, completed

    def remove(self, worktree: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.invoke("remove", {
            "session_id": "session-test",
            "cwd": str(self.repo.resolve()),
            "hook_event_name": "WorktreeRemove",
            "worktree_path": str(worktree.resolve()),
        }, check=check)

    def test_session_plugin_registers_notice_and_unmatched_worktree_hooks(self) -> None:
        hooks = json.loads(HOOKS.read_text(encoding="utf-8"))["hooks"]
        self.assertEqual(hooks["SessionStart"][0]["matcher"], "startup|resume|clear")
        notice_hook = hooks["SessionStart"][0]["hooks"][0]
        self.assertEqual(notice_hook["type"], "command")
        self.assertEqual(notice_hook["timeout"], 5)
        self.assertIn("update-notice.sh", notice_hook["command"])
        for event, script in (
            ("WorktreeCreate", "worktree-create.sh"),
            ("WorktreeRemove", "worktree-remove.sh"),
        ):
            self.assertIn(event, hooks)
            self.assertNotIn("matcher", hooks[event][0])
            command = hooks[event][0]["hooks"][0]["command"]
            self.assertIn(script, command)

    def test_create_projects_dirty_and_safe_untracked_without_mutating_checkout(self) -> None:
        (self.repo / "tracked.txt").write_text("working tree\n", encoding="utf-8")
        (self.repo / "staged.txt").write_text("staged\n", encoding="utf-8")
        self.git("add", "staged.txt")
        (self.repo / "safe-untracked.txt").write_text("safe untracked\n", encoding="utf-8")
        (self.repo / ".env").write_text(
            "DATABASE_URL=private-value\nAPI_KEY=another-private-value\n",
            encoding="utf-8",
        )
        (self.repo / "secrets.json").write_text(
            '{"client_secret":"untracked-private"}\n', encoding="utf-8"
        )
        (self.repo / "ignored.txt").write_text("ignored-private\n", encoding="utf-8")
        before_status = self.git("status", "--porcelain=v1", "-z").stdout

        worktree, completed = self.create()

        self.assertEqual((worktree / "tracked.txt").read_text(encoding="utf-8"), "working tree\n")
        self.assertEqual((worktree / "staged.txt").read_text(encoding="utf-8"), "staged\n")
        self.assertEqual(
            (worktree / "safe-untracked.txt").read_text(encoding="utf-8"),
            "safe untracked\n",
        )
        projection = json.loads((worktree / ".env").read_text(encoding="utf-8"))
        self.assertEqual(projection["keys"], ["DATABASE_URL", "API_KEY"])
        self.assertFalse(projection["values_exposed"])
        self.assertNotIn("private-value", (worktree / ".env").read_text(encoding="utf-8"))
        self.assertFalse((worktree / "credentials.json").exists())
        self.assertFalse((worktree / "secrets.json").exists())
        self.assertFalse((worktree / "ignored.txt").exists())
        self.assertIn("filtered 2 credential-bearing", completed.stderr)
        self.assertIn("key-only projections", completed.stderr)
        self.assertEqual(self.git("status", "--porcelain=v1", "-z").stdout, before_status)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), self.before_head)
        self.assertEqual(self.git("status", "--porcelain=v1", cwd=worktree).stdout, "")

        branch = self.git("branch", "--show-current", cwd=worktree).stdout.strip()
        self.assertTrue(branch.startswith("airlock-worktree-"))
        self.remove(worktree)
        self.assertFalse(worktree.exists())
        self.assertNotIn(branch, self.git("branch", "--format=%(refname:short)").stdout.splitlines())

    def test_remove_preserves_changed_or_committed_worktrees(self) -> None:
        worktree, _ = self.create("changed-agent")
        (worktree / "tracked.txt").write_text("agent edit\n", encoding="utf-8")
        changed = self.remove(worktree, check=False)
        self.assertNotEqual(changed.returncode, 0)
        self.assertIn("contains changes", changed.stderr)
        self.assertTrue(worktree.is_dir())
        self.git("checkout", "--", "tracked.txt", cwd=worktree)
        self.git("commit", "--allow-empty", "-m", "agent commit", cwd=worktree)
        committed = self.remove(worktree, check=False)
        self.assertNotEqual(committed.returncode, 0)
        self.assertIn("contains commits", committed.stderr)
        self.assertTrue(worktree.is_dir())
        self.git("worktree", "remove", "--force", str(worktree))
        branches = self.git("branch", "--format=%(refname:short)").stdout.splitlines()
        for branch in branches:
            if branch.startswith("airlock-worktree-"):
                self.git("branch", "-D", branch)

    def test_unsafe_names_and_escaping_symlinks_fail_closed(self) -> None:
        unsafe = self.invoke("create", {
            "session_id": "session-test",
            "cwd": str(self.repo.resolve()),
            "hook_event_name": "WorktreeCreate",
            "name": "../outside",
        }, check=False)
        self.assertNotEqual(unsafe.returncode, 0)
        self.assertIn("unsafe path segment", unsafe.stderr)

        if os.name != "nt":
            outside = Path(self.temp.name) / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")

            # An absolute target is rejected before the escape check, so both
            # link guards need their own case.
            os.symlink(outside, self.repo / "escape-link")
            self.git("add", "escape-link")
            absolute = self.invoke("create", {
                "session_id": "session-test",
                "cwd": str(self.repo.resolve()),
                "hook_event_name": "WorktreeCreate",
                "name": "symlink-absolute",
            }, check=False)
            self.assertNotEqual(absolute.returncode, 0)
            self.assertIn("absolute target", absolute.stderr)
            self.git("rm", "-f", "--quiet", "escape-link")

            os.symlink("../outside.txt", self.repo / "escape-link")
            self.git("add", "escape-link")
            escaping = self.invoke("create", {
                "session_id": "session-test",
                "cwd": str(self.repo.resolve()),
                "hook_event_name": "WorktreeCreate",
                "name": "symlink-relative",
            }, check=False)
            self.assertNotEqual(escaping.returncode, 0)
            self.assertIn("escapes the repository", escaping.stderr)


if __name__ == "__main__":
    unittest.main()
