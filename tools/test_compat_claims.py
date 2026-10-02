"""Guards for the three findings from the post-1.7.0 review.

Run:  python3 tools/test_compat_claims.py

These are not "does the code do the right thing" tests - the other suites
cover that. They pin three things that were true and are easy to regress,
each of which had already caused a real problem once:

  1. The README promised Home Assistant versions the code cannot actually
     serve. The options flow relied on `self.config_entry` being injected,
     which only happens from 2024.11, and the manifest declared no minimum at
     all - so HACS would have installed it anywhere and the settings dialog
     would have raised on older instances.
  2. The health score had a made-up denominator.
  3. A soft-deleted entity's snapshot must survive a re-scan, because a
     disabled entity can never be re-scanned. This one is a trap rather than a
     bug: "cleaning up" stale snapshots looks reasonable and silently undoes
     the restore-returns-to-list fix.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ha_optimizer"
README = ROOT / "README.md"

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
readme = README.read_text(encoding="utf-8")
panel = (COMPONENT / "panel.html").read_text(encoding="utf-8")
config_flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
store = (COMPONENT / "store.py").read_text(encoding="utf-8")

# ═══ 1. the declared minimum version and the claim in the README agree ═══════
print("\ncompatibility claim")
declared = manifest.get("homeassistant")
check("manifest declares a minimum Home Assistant version", bool(declared),
      "without it HACS installs the integration on any version, including "
      "ones whose APIs the code does not use")

if declared:
    parts = [int(x) for x in declared.split(".")]
    check("the minimum is at least the version the options flow needs (2024.11)",
          tuple(parts[:3]) >= (2024, 11, 0),
          f"declared {declared} but the flow needs 2024.11+")

    # the README must not advertise a floor lower than the manifest enforces
    floor_rows = re.findall(r"^\|\s*Home Assistant\s*\|\s*([^|]+)\|", readme, re.M)
    check("the README has a compatibility row", bool(floor_rows))
    claimed = floor_rows[0] if floor_rows else ""
    # "2023.7+" is not a number - parse it as a (year, minor) pair
    versions = [tuple(int(x) for x in v.split(".")[:2])
                for v in re.findall(r"20\d\d\.\d+", claimed)]
    check("the README does not promise a lower version than the manifest",
          all(v >= (parts[0], parts[1]) for v in versions),
          f"README says {claimed!r}, manifest enforces {declared}")

    badges = re.findall(r"Home%20Assistant-([0-9.]+)\+", readme)
    badge_versions = [tuple(int(x) for x in b.split(".")[:2]) for b in badges]
    check("the README badge agrees with the manifest too",
          all(v >= (parts[0], parts[1]) for v in badge_versions),
          f"badges: {badges}")

# and the code must not depend on the version it just disclaimed
check("the options flow accepts a config entry from either side of the 2024.11 split",
      "def __init__(self, config_entry=None)" in config_flow
      and "_legacy_entry" in config_flow,
      "it used to read self.config_entry unconditionally, which only exists "
      "from 2024.11")
check("the options flow never reads self.config_entry directly",
      not re.search(r"self\.config_entry\.(options|data)", config_flow))
check("the fallback is explained where it lives",
      "2024.11" in config_flow)

# ═══ 2. no invented denominator in the health score ═════════════════════════
print("\nhealth score denominator")
recalc = panel[panel.index("function recalcHealthFromResults()"):]
recalc = recalc[:recalc.index("\nfunction ")]
# Strip comment-only lines first. The fix documents the old expression in a
# comment, and a guard that trips on its own explanation is a guard nobody
# will keep - it would be "fixed" by deleting the explanation.
recalc_code = "\n".join(l for l in recalc.splitlines() if not l.strip().startswith("//"))
check("no magic multiplier is used as a stand-in for the entity total",
      not re.search(r"allResults\.length\s*\*\s*\d", recalc_code),
      "`allResults.length * 5` made the score fiction whenever it fired")
check("there is an explicit guard for a zero denominator", "total <= 0" in recalc_code)
check("the fallback is documented where it lives",
      "made-up multiplier" in recalc or "well-defined denominator" in recalc)

# ═══ 3. a trash snapshot must outlive the scan that produced it ══════════════
print("\ntrash snapshots survive a re-scan")
save = store[store.index("async def async_save_scan_results"):]
save = save[:save.index("\n    async def ")]
check("saving new scan results does not touch the soft-delete records",
      "_soft_data" not in save.replace("# _soft_data", "").split('"""')[0] + save.split('"""')[-1],
      "a disabled entity is never re-scanned, so pruning its snapshot here "
      "would silently break restore")
check("the reason is written down at the call site",
      "disabled" in save and "snapshot" in save)

scanner = (COMPONENT / "scanner.py").read_text(encoding="utf-8")
check("the scanner really does skip disabled entities (the premise above)",
      "entry.disabled_by is None" in scanner,
      "if this ever changes, a re-scan could refresh snapshots and the "
      "guard should be revisited")

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("all compatibility-claim checks passed")
