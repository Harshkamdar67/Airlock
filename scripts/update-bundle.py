#!/usr/bin/env python3
"""Update or verify hashes in the managed bundle marker."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "config" / "managed-bundle.json"


def component_path(name: str) -> Path:
    candidate = Path(name)
    if (
        not name
        or candidate.is_absolute()
        or "\\" in name
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValueError(f"unsafe managed component path: {name!r}")
    path = ROOT / candidate
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"managed component is missing or unsafe: {name}")
    return path


def current_digests(bundle: dict) -> dict[str, str]:
    components = bundle.get("components")
    if not isinstance(components, dict) or not components:
        raise ValueError("managed bundle has no component inventory")
    return {
        name: hashlib.sha256(component_path(name).read_bytes()).hexdigest()
        for name in components
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
        if not isinstance(bundle, dict):
            raise ValueError("managed bundle must be a JSON object")
        observed = current_digests(bundle)
        if args.check:
            if bundle.get("components") != observed:
                print(
                    "managed bundle hashes are stale; run python scripts/update-bundle.py",
                    file=sys.stderr,
                )
                return 1
            print(f"Managed bundle hashes are current for {len(observed)} components.")
            return 0
        bundle["components"] = observed
        BUNDLE.write_text(
            json.dumps(bundle, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"Updated managed bundle hashes for {len(observed)} components.")
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"update-bundle: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
