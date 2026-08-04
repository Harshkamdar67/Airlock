#!/usr/bin/env python3
"""Release metadata checks that do not publish anything."""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_version_matches_plugin(self) -> None:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
        plugin = json.loads(
            (ROOT / "plugins" / "airlock" / ".claude-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(plugin["version"], version)
        access_helper = (ROOT / "bin" / "airlock-access.py").read_text(encoding="utf-8")
        self.assertIn(f'"version": "{version}"', access_helper)

    def test_release_workflow_is_tag_only_and_builds_checksums(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("tags:", workflow)
        self.assertIn("git archive", workflow)
        self.assertIn("sha256sum", workflow)
        self.assertIn("attest-build-provenance", workflow)
        self.assertNotRegex(workflow, re.compile(r"\b(npm publish|twine upload|brew tap-new)\b"))

    def test_github_contribution_security_baseline(self) -> None:
        github = ROOT / ".github"
        required = (
            github / "CODEOWNERS",
            github / "pull_request_template.md",
            github / "dependabot.yml",
            github / "ISSUE_TEMPLATE" / "bug_report.yml",
            github / "ISSUE_TEMPLATE" / "feature_request.yml",
            github / "ISSUE_TEMPLATE" / "config.yml",
        )
        for path in required:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertTrue(path.is_file())

        self.assertIn("* @Harshkamdar67", required[0].read_text(encoding="utf-8"))
        pull_request = required[1].read_text(encoding="utf-8")
        self.assertIn("Security and compatibility", pull_request)
        self.assertIn("Developer Certificate of Origin sign-off", pull_request)
        dependabot = required[2].read_text(encoding="utf-8")
        self.assertIn("package-ecosystem: github-actions", dependabot)
        issue_config = required[-1].read_text(encoding="utf-8")
        self.assertIn("blank_issues_enabled: false", issue_config)
        self.assertIn("security/policy", issue_config)

        for path in (github / "workflows").glob("*.yml"):
            workflow = path.read_text(encoding="utf-8")
            actions = re.findall(r"^\s*(?:-\s+)?uses:\s+([^\s#]+)", workflow, re.MULTILINE)
            self.assertTrue(actions, f"workflow has no Actions: {path.name}")
            for action in actions:
                with self.subTest(workflow=path.name, action=action):
                    self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
