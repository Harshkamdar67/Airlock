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


if __name__ == "__main__":
    unittest.main()
