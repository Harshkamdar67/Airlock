#!/usr/bin/env python3
"""Offline tests for the user-only cached update notice hook."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins" / "airlock" / "scripts" / "update-notice.py"
WRAPPER = ROOT / "plugins" / "airlock" / "scripts" / "update-notice.sh"
BASH_WRAPPER = str(WRAPPER).replace("\\", "/")
MANIFEST = ROOT / "plugins" / "airlock" / ".claude-plugin" / "plugin.json"
SPEC = importlib.util.spec_from_file_location("airlock_update_notice", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
notice_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notice_module)
CURRENT_VERSION = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
AVAILABLE_VERSION = "0.1.0-beta.4"
RELEASE_URL = (
    "https://github.com/Harshkamdar67/Airlock/releases/tag/"
    f"v{AVAILABLE_VERSION}"
)


def valid_notice(now: int | None = None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "airlock-update-notice",
        "checked_at": int(time.time()) if now is None else now,
        "current_version": CURRENT_VERSION,
        "available_version": AVAILABLE_VERSION,
        "release_url": RELEASE_URL,
    }


def run_notice(path: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["AIRLOCK_UPDATE_NOTICE_FILE"] = str(path)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


class UpdateNoticeTests(unittest.TestCase):
    def assert_silent(self, path: Path) -> None:
        completed = run_notice(path)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")

    def test_valid_notice_is_one_user_only_system_message(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "update-notice.json"
            path.write_text(json.dumps(valid_notice()), encoding="utf-8")
            completed = run_notice(path)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, "")
        output = json.loads(completed.stdout)
        self.assertEqual(set(output), {"systemMessage"})
        self.assertNotIn("additionalContext", completed.stdout)
        self.assertIn(f"Airlock {AVAILABLE_VERSION} is available", output["systemMessage"])
        self.assertIn("Exit this session, then run airlock update.", output["systemMessage"])
        self.assertIn(RELEASE_URL, output["systemMessage"])

    def test_missing_nonregular_symlink_and_oversized_inputs_are_silent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            self.assert_silent(temporary / "missing.json")
            directory = temporary / "directory.json"
            directory.mkdir()
            self.assert_silent(directory)
            oversized = temporary / "oversized.json"
            oversized.write_bytes(b"x" * (notice_module.NOTICE_LIMIT + 1))
            self.assert_silent(oversized)
            source = temporary / "source.json"
            source.write_text(json.dumps(valid_notice()), encoding="utf-8")
            link = temporary / "link.json"
            try:
                link.symlink_to(source)
            except OSError:
                pass
            else:
                self.assert_silent(link)

    def test_malformed_or_noncanonical_json_is_silent(self) -> None:
        malformed_values = [
            b"not json",
            b"[]",
            b'{"schema_version":1,"schema_version":1}',
            json.dumps({**valid_notice(), "extra": True}).encode(),
        ]
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "update-notice.json"
            for raw in malformed_values:
                with self.subTest(raw=raw[:40]):
                    path.write_bytes(raw)
                    self.assert_silent(path)

    def test_bad_types_versions_urls_and_times_are_silent(self) -> None:
        now = int(time.time())
        cases = {
            "bool timestamp": {**valid_notice(now), "checked_at": True},
            "string timestamp": {**valid_notice(now), "checked_at": str(now)},
            "bad schema": {**valid_notice(now), "schema_version": 2},
            "bad kind": {**valid_notice(now), "kind": "other"},
            "installed mismatch": {**valid_notice(now), "current_version": "0.1.0-beta.1"},
            "invalid current": {**valid_notice(now), "current_version": "v0.1.0-beta.2"},
            "invalid available": {**valid_notice(now), "available_version": "0.1.0-beta.03"},
            "overlong available": {
                **valid_notice(now),
                "available_version": "9" * 65 + ".0.0",
                "release_url": (
                    "https://github.com/Harshkamdar67/Airlock/releases/tag/v"
                    + "9" * 65
                    + ".0.0"
                ),
            },
            "not newer": {
                **valid_notice(now),
                "available_version": CURRENT_VERSION,
                "release_url": f"https://github.com/Harshkamdar67/Airlock/releases/tag/v{CURRENT_VERSION}",
            },
            "wrong host": {**valid_notice(now), "release_url": f"https://example.com/v{AVAILABLE_VERSION}"},
            "wrong tag": {**valid_notice(now), "release_url": RELEASE_URL + "-other"},
            "stale": {**valid_notice(now), "checked_at": now - notice_module.NOTICE_TTL_SECONDS - 1},
            "future": {**valid_notice(now), "checked_at": now + notice_module.FUTURE_TOLERANCE_SECONDS + 10},
            "negative": {**valid_notice(now), "checked_at": -1},
            "control characters": {**valid_notice(now), "available_version": "0.1.0-beta.3\nwarning"},
        }
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "update-notice.json"
            for name, value in cases.items():
                with self.subTest(name=name):
                    path.write_text(json.dumps(value), encoding="utf-8")
                    self.assert_silent(path)

    def test_stable_install_does_not_offer_a_prerelease(self) -> None:
        now = int(time.time())
        payload = {
            **valid_notice(now),
            "current_version": "1.0.0",
            "available_version": "1.1.0-rc.1",
            "release_url": (
                "https://github.com/Harshkamdar67/Airlock/releases/tag/v1.1.0-rc.1"
            ),
        }
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "update-notice.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.dict(
                os.environ, {"AIRLOCK_UPDATE_NOTICE_FILE": str(path)}, clear=False
            ), mock.patch.object(notice_module, "installed_version", return_value="1.0.0"):
                self.assertIsNone(notice_module.notice_message(now=now))

    def test_semver_precedence_requires_a_real_upgrade(self) -> None:
        chain = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
        ]
        for older, newer in zip(chain, chain[1:]):
            self.assertTrue(notice_module.is_newer(newer, older))
            self.assertFalse(notice_module.is_newer(older, newer))
        self.assertFalse(notice_module.is_newer("1.0.0+two", "1.0.0+one"))

    def test_internal_errors_return_zero_without_output(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(notice_module, "notice_message", side_effect=OSError("failure")):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = notice_module.main()
        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_shell_wrapper_emits_exact_valid_json(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("Bash is not available")
        with tempfile.TemporaryDirectory() as temporary_text:
            path = Path(temporary_text) / "update-notice.json"
            path.write_text(json.dumps(valid_notice()), encoding="utf-8")
            environment = os.environ.copy()
            environment["AIRLOCK_UPDATE_NOTICE_FILE"] = str(path)
            environment["AIRLOCK_PYTHON"] = sys.executable
            completed = subprocess.run(
                [bash, BASH_WRAPPER],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, "")
        output = json.loads(completed.stdout)
        self.assertEqual(set(output), {"systemMessage"})

    def test_shell_wrapper_rejects_a_banner_printing_interpreter(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("Bash is not available")
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            shim = temporary / "python-shim"
            shim.write_text(
                "#!/bin/sh\nprintf 'unexpected banner\\n'\nexit 0\n",
                encoding="utf-8",
            )
            shim.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = temporary_text
            environment["AIRLOCK_PYTHON"] = str(shim)
            completed = subprocess.run(
                [bash, BASH_WRAPPER],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")

    def test_shell_wrapper_is_silent_without_python(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("Bash is not available")
        environment = os.environ.copy()
        environment["PATH"] = ""
        environment["AIRLOCK_PYTHON"] = "missing-python"
        completed = subprocess.run(
            [bash, BASH_WRAPPER],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
