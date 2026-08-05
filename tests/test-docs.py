#!/usr/bin/env python3
"""Plain-language documentation checks."""

from __future__ import annotations

from pathlib import Path
import re
import unittest
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_FILES = sorted(
    path for path in ROOT.rglob("*.md")
    if ".git" not in path.parts
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
                or (path.suffix.lower() not in text_suffixes and path.name not in {"VERSION"})
            ):
                continue
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn(forbidden, path.read_text(encoding="utf-8"))

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
