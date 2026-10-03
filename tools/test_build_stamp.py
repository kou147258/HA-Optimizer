"""The panel must be able to say which build it is, and an upgrade must
actually reach the browser.

Run:  python3 tools/test_build_stamp.py

Reported by a user who could not tell whether a fix had landed. Two causes,
one fix:

  * Home Assistant serves `/local/` with `cache-control: public,
    max-age=2678400` - 31 days. A panel upgrade therefore does not reach an
    open browser tab, and nothing on screen says so.
  * The panel carried no version anywhere, so "is this the new one?" could
    only be answered by guessing, or by hard-refreshing and hoping.

The version now goes into the iframe's query string. That is a new URL and
therefore a new cache entry, and the panel reads the same value from its own
`location.search` to display it - one source, no injected copy of the file, no
extra request, and nothing that can drift from the manifest.

The 1.7.5 lesson applies directly: the stamp's element AND its style must sit
outside every media query, or it is simply absent on a wide panel - which is
the exact failure this file exists to prevent.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMP = ROOT / "custom_components" / "ha_optimizer"
PANEL_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else COMP / "panel.html"
INIT_PATH = Path(sys.argv[2]) if len(sys.argv) > 2 else COMP / "__init__.py"
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


panel = PANEL_PATH.read_text(encoding="utf-8")
init = INIT_PATH.read_text(encoding="utf-8")


def media_extents(src: str):
    out = []
    for m in re.finditer(r"@media[^{]*\{", src):
        open_i = src.index("{", m.start())
        depth, q, comment, k, end = 0, None, False, open_i, -1
        while k < len(src):
            c, n = src[k], src[k + 1] if k + 1 < len(src) else ""
            if comment:
                if c == "*" and n == "/":
                    comment = False
                    k += 1
            elif q:
                if c == "\\":
                    k += 1
                elif c == q:
                    q = None
            elif c == "/" and n == "*":
                comment = True
                k += 1
            elif c in ('"', "'"):
                q = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = k
                    break
            k += 1
        out.append((m.start(), end))
    return out


MEDIAS = media_extents(panel)


def nested_in(needle: str) -> bool:
    i = panel.find(needle)
    return i >= 0 and any(a < i < b for a, b in MEDIAS)


print("\nthe iframe URL busts the 31-day cache on /local/")
check("the panel URL carries a version query",
      re.search(r'panel\.html\?v=\{VERSION\}', init) is not None,
      "without it a browser can serve the previous panel for a month")
check("the version comes from the single constant, not a literal",
      "VERSION" in init and init.count("?v={VERSION}") == 1,
      "a hardcoded version here would need editing in two places and drift")
check("VERSION is imported where the panel is registered",
      re.search(r"from \.const import \([^)]*VERSION", init, re.S) is not None
      or re.search(r"^\s*VERSION,\s*$", init, re.M) is not None)
check("the bare URL is not registered anywhere any more",
      '"url": "/local/ha_optimizer/panel.html",' not in init,
      "that is the cacheable one")

print("\nthe panel says which build it is")
check("a build stamp element exists", 'id="buildVersion"' in panel)
check("it reads the version from its own URL rather than a baked-in copy",
      "URLSearchParams(location.search).get('v')" in panel,
      "a value written into the file at copy time is a second source to keep in "
      "step, and it is exactly what goes stale")
check("it degrades visibly when the query is absent",
      "(unversioned copy)" in panel,
      "silently showing nothing is the same as not having the stamp")

print("\nnothing here is scoped to a viewport")
check("the media queries were all located",
      len(MEDIAS) >= 3 and all(b > a for a, b in MEDIAS),
      "a checker that cannot find the media queries passes everything under them")
check("the stamp element is outside every media query",
      not nested_in('class="build-stamp"'),
      "inside one it simply does not exist on a wide panel")
check("the stamp's style is outside every media query",
      not nested_in(".build-stamp {"),
      "this is the 1.7.5 failure again: the rule existed, the assertions were "
      "green, and a 1900px panel never saw it")
check("the stamp sits inside the panel body, not in the chrome",
      panel.index('id="buildVersion"') > panel.index("<body"))

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("all build-stamp checks passed")
