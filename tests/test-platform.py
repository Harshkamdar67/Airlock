#!/usr/bin/env python3
"""Cross-platform checks for repository entry points."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHELL_FILES = [ROOT / "bin" / "airlock"]
SHELL_FILES.extend(sorted((ROOT / "scripts").glob("*.sh")))
SHELL_FILES.extend(sorted((ROOT / "tests").glob("*.sh")))
SHELL_FILES.extend(sorted((ROOT / "plugins" / "airlock" / "scripts").glob("*.sh")))


class PlatformTests(unittest.TestCase):
    def test_bash_entry_points_use_lf(self) -> None:
        for path in SHELL_FILES:
            with self.subTest(path=path.relative_to(ROOT)):
                payload = path.read_bytes()
                self.assertNotIn(b"\r\n", payload)
                self.assertTrue(payload.startswith(b"#!/"))

    def test_git_attributes_force_bash_entry_points_to_lf(self) -> None:
        for path in SHELL_FILES:
            relative = path.relative_to(ROOT).as_posix()
            completed = subprocess.run(
                ["git", "-C", str(ROOT), "check-attr", "eol", "--", relative],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            )
            with self.subTest(path=relative):
                self.assertTrue(completed.stdout.rstrip().endswith(": eol: lf"))

    def test_bash_entry_points_parse(self) -> None:
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is unavailable")
        completed = subprocess.run(
            [bash, "-n", *(str(path) for path in SHELL_FILES)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
