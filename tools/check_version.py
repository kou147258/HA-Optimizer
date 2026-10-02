#!/usr/bin/env python3
"""Check that every place the integration states its version agrees.

`manifest.json` is what Home Assistant and HACS actually read, but this repo
also mirrors the number in `const.py` and in both READMEs. They had drifted
apart (manifest 1.2.2 vs 1.0.0 everywhere else) with nothing catching it, so
the sidebar and the docs could not be reconciled with what gets installed.

    python3 tools/check_version.py

Exit codes: 0 = all agree, 1 = mismatch, 2 = could not read the files.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ha_optimizer"

# where each file actually lives, and how to pull the number out of it
SOURCES = {
    "custom_components/ha_optimizer/manifest.json": None,   # parsed as JSON
    "custom_components/ha_optimizer/const.py": re.compile(r'^VERSION\s*=\s*"([^"]+)"', re.M),
    "README.md": re.compile(r"badge/version-([0-9][^-]*)-"),
    "README_vi.md": re.compile(r"badge/version-([0-9][^-]*)-"),
}


def read_version(rel: str):
    path = ROOT / rel
    if not path.is_file():
        return None, f"file not found: {rel}"
    text = path.read_text(encoding="utf-8")
    if SOURCES[rel] is None:
        try:
            return json.loads(text).get("version"), None
        except json.JSONDecodeError as exc:
            return None, f"{rel} is not valid JSON: {exc}"
    m = SOURCES[rel].search(text)
    if not m:
        return None, f"no version pattern matched in {rel}"
    return m.group(1).strip(), None


def main() -> int:
    found: dict[str, str] = {}
    problems: list[str] = []

    for rel in SOURCES:
        v, err = read_version(rel)
        if err:
            problems.append(err)
            continue
        found[rel] = v
        print(f"  {rel:<44} {v}")

    values = set(found.values())
    if len(values) > 1:
        groups: dict[str, list[str]] = {}
        for rel, v in sorted(found.items()):
            groups.setdefault(v, []).append(rel)
        detail = "  ".join(f"{v!r} in {', '.join(rs)}" for v, rs in sorted(groups.items()))
        problems.append(f"version numbers disagree -> {detail}")

    if problems:
        print(f"\n{len(problems)} problem(s):", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    print(f"\nOK - all {len(found)} sources agree on {values.pop()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
