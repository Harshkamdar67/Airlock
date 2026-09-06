#!/usr/bin/env python3
"""Plain-language documentation checks."""

from __future__ import annotations

from pathlib import Path
import re
import unittest
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
# .claude holds local session notes rather than Airlock-owned documentation.
# node_modules and dist hold third-party packages and built output.
LOCAL_DIRECTORIES = {".claude", "node_modules", "dist"}
MARKDOWN_FILES = sorted(
    path for path in ROOT.rglob("*.md")
    if ".git" not in path.parts
    and not LOCAL_DIRECTORIES.intersection(path.parts)
)
LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


class DocumentationTests(unittest.TestCase):
    def test_docs_do_not_use_em_dashes(self) -> None:
        for path in MARKDOWN_FILES:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn("—", path.read_text(encoding="utf-8"))

    def test_local_markdown_links_exist(self) -> None:
        for path in MARKDOWN_FILES:
            text = path.read_text(encoding="utf-8")
            for raw_target in LINK_PATTERN.findall(text):
                target = raw_target.strip().split()[0].strip("<>")
                if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                target = unquote(target.split("#", 1)[0])
                resolved = (path.parent / target).resolve()
                with self.subTest(path=path.relative_to(ROOT), target=target):
                    self.assertTrue(resolved.is_relative_to(ROOT.resolve()))
                    self.assertTrue(resolved.exists(), f"missing link target: {target}")

    def test_airlock_owned_text_has_no_stale_runtime_brand(self) -> None:
        forbidden = "Code" + " Crossroads"
        text_suffixes = {".cmd", ".example", ".json", ".md", ".ps1", ".py", ".sh", ".yaml", ".yml"}
        for path in ROOT.rglob("*"):
            if (
                not path.is_file()
                or path.is_symlink()
                or ".git" in path.parts
                or "__pycache__" in path.parts
                or LOCAL_DIRECTORIES.intersection(path.parts)
                or (path.suffix.lower() not in text_suffixes and path.name not in {"VERSION"})
            ):
                continue
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn(forbidden, path.read_text(encoding="utf-8"))

    def test_openmodel_docs_distinguish_trigger_from_hard_ceiling(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        model_guide = (ROOT / "docs" / "models-and-usage.md").read_text(
            encoding="utf-8"
        )
        architecture = (ROOT / "docs" / "how-it-works.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("declared context window remains its hard ceiling", readme)
        self.assertIn(
            "This ceiling is process-wide, so in `airlock hybrid om:ROUTE` it also caps every non-local worker",
            readme,
        )
        self.assertIn(
            "always exports it as `CLAUDE_CODE_MAX_CONTEXT_TOKENS`", model_guide
        )
        self.assertIn(
            "cannot raise the effective threshold above the route's declared hard ceiling",
            model_guide,
        )
        self.assertIn(
            "selecting a local root with `airlock hybrid om:ROUTE` also caps every non-local worker",
            model_guide,
        )
        self.assertIn(
            "This is a hard ceiling, not merely a default trigger", architecture
        )
        self.assertIn(
            "cannot raise the effective threshold above the declared ceiling",
            architecture,
        )
        self.assertIn(
            "a hybrid local root's declared ceiling also caps every non-local worker",
            architecture,
        )

    def test_openmodel_docs_state_private_input_bounds(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        commands = (ROOT / "docs" / "commands.md").read_text(encoding="utf-8")
        self.assertIn("bounded hidden prompts", readme)
        self.assertIn("uses a bounded hidden prompt", security)
        self.assertIn("at most 1,024 Unicode characters and 4,096 UTF-8 bytes", commands)

    def test_readme_version_badge_matches_the_release_version(self) -> None:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        badge = re.search(
            r"!\[Version\]\(https://img\.shields\.io/badge/version-([^-\s)]+(?:--[^-\s)]+)*)-",
            readme,
        )
        self.assertIsNotNone(badge, "README has no version badge")
        self.assertEqual(badge.group(1).replace("--", "-"), version)

    def test_readme_is_short_and_starts_with_useful_sections(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(readme.splitlines()), 250)
        headings = [
            "# Airlock",
            "## Why use it",
            "## How it works",
            "## Install",
            "## First run",
            "## Common commands",
            "## Security and file access",
            "## Documentation",
            "## Credits",
            "## License",
        ]
        positions = [readme.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(
            "Run OpenAI and Anthropic models together in one Claude Code session",
            readme,
        )
        self.assertIn(
            "does not call OpenAI or Anthropic models",
            (ROOT / "docs" / "testing.md").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
