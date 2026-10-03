"""The integration must not answer confidently when it does not know.

Run:  python3 tools/test_fail_loud.py

Two places swallowed an exception and carried on as though nothing had
happened. Both ended up making a statement about the user's system that was
not supported by the data:

  * the impact dialog runs before a permanent delete, and used to show a list
    that had quietly lost whole categories. It also called
    `automation.config`, threw the result away, and swallowed any failure - so
    a broken call looked exactly like a clean one. The map it returned was a
    string search dressed up as an analysis;
  * the dashboard check reads Lovelace's resource list, and treated a failed
    read as an EMPTY list. "Not in the registered set" then means "this custom
    card was never configured" - so an unreadable file produced an accusation
    against every custom card on the dashboard.

An integration that deletes things should be especially careful here: the
difference between "I checked and found nothing" and "I could not check" is
the difference between a clean report and a false accusation.

This file is the mechanical half of that: it also re-runs the whole-file
mechanical sweep (services consistency, blocking calls, swallowed exceptions,
scheduling, log levels) so a new module cannot quietly reintroduce any of them.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMP = ROOT / "custom_components" / "ha_optimizer"
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def read(name: str) -> str:
    # newline="" so CRLF survives; universal-newline translation would make
    # every pattern below quietly fail on this repository.
    with open(COMP / name, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def body(src: str, defline: str) -> str:
    m = re.search(re.escape(defline) + r".*?(?=\n    (?:async )?def |\nclass |\n\S)", src, re.S)
    return m.group(0) if m else ""


def code_only(src: str) -> str:
    out = re.sub(r'"""[\s\S]*?"""', "", src)
    out = re.sub(r"'''[\s\S]*?'''", "", out)
    return re.sub(r"(?m)#.*$", "", out)


engine = read("purge_engine.py")
init = read("__init__.py")
scanner = read("scanner.py")
panel = read("panel.html")

# ═══ 1. the impact list must not look more complete than it is ═════════════
# This used to guard `async_get_dependency_map`, which had zero callers: no
# service, no panel code, nothing. Its "I am a heuristic" fields were invisible
# because nothing ever called the method, and the assertion meant to prove the
# panel rendered them was `"depSection" in panel or "impact" in panel.lower()`
# - satisfied by the unrelated "Impact Analysis" heading. A check whose name
# says one thing and whose condition verifies something else is worse than no
# check, because it reads as coverage.
#
# The path actually in use is `used_in`, produced by the scanner at scan time.
# So that is what gets checked: the dialog states what it looked at, and an
# empty result is not presented as "nothing depends on this".
print("\nthe impact list must not look more complete than it is")
check("the dead runtime dependency map is gone",
      "async_get_dependency_map" not in engine,
      "zero callers, and strictly weaker than the scanner's used_in")
check("the panel's impact dialog says what it scanned",
      "impactScanScope" in panel,
      "used_in is computed by the scanner and has known blind spots")
check("that caveat is a translation, not hardcoded English",
      panel.count("impactScanScope:") >= 2,
      "one language block is not a translation")
check("an entity with no known references says so",
      "impactNoRefs" in panel,
      "an empty impact section reads as 'nothing depends on it', which is a "
      "different and much stronger claim than 'none was found'")

# ═══ 2. an unreadable resource list is not an empty one ════════════════════
print("\nan unreadable file is not evidence of absence")
cards = body(scanner, "    def _get_registered_custom_cards(self, storage_dir: str)")
check("the reader was found", bool(cards))
check("it returns whether it could read the list",
      "tuple[set[str], bool]" in cards and "return known, complete" in cards)
check("a failed read sets complete = False",
      "complete = False" in cards)
check("and says so at warning level, not debug",
      "_LOGGER.warning" in cards and "_LOGGER.debug(\"Cannot read lovelace_resources" not in cards,
      "a message only visible at debug level is the same as no message")
check("the caller unpacks the flag",
      "registered_custom_cards, custom_cards_known = self._get_registered_custom_cards" in scanner)
check("the accusation is gated on the flag",
      "if not custom_cards_known:" in scanner
      and "elif card_type not in registered_custom_cards:" in scanner)
check("and the run says what it could not check",
      'result["custom_cards_unchecked"]' in scanner)
check("the reader is defined once",
      len(re.findall(r"def _get_registered_custom_cards", scanner)) == 1,
      "replacing only the signature leaves the old body behind as loose code "
      "inside the class - still valid Python, so a parse check misses it")

# ═══ 3. the mechanical sweep, so a new module cannot sneak past ════════════
print("\nwhole-file mechanical sweep")
services_yaml = read("services.yaml")
# The service table lives in __init__.py. An earlier version of this file read
# it out of purge_engine.py, found one constant, and reported thirteen ghosts.
registered = {m.group(1).lower() for m in re.finditer(r"SERVICE_([A-Z_]+)", init)}
documented = set(re.findall(r"^([a-z_]+):", services_yaml, re.M))
called = set(re.findall(r"callService\('ha_optimizer',\s*'([a-z_]+)'", panel))
check("every registered service is documented in services.yaml",
      registered <= documented, f"missing {sorted(registered - documented)}")
check("every documented service is registered",
      documented <= registered, f"ghost {sorted(documented - registered)}")
check("every service the panel calls exists",
      called <= registered, f"panel calls {sorted(called - registered)}")

PYFILES = ["__init__.py", "purge_engine.py", "scanner.py", "store.py", "fingerprint.py"]
high_blocking = []
for f in PYFILES:
    s = read(f)
    for m in re.finditer(r"(time\.sleep\(|\.read_bytes\(|\.write_bytes\(|\.write_text\()", s):
        before = s[:m.start()]
        in_async = before.rfind("async def ") > before.rfind("\ndef ")
        if in_async:
            high_blocking.append(f"{f}:{s[:m.start()].count(chr(10)) + 1}")
check("no blocking file or sleep call inside an async function",
      not high_blocking, ", ".join(high_blocking))

# The three remaining swallows are each a genuinely optional lookup: removing
# the sidebar panel on unload, the recorder's database size, and a date parse.
# What is banned is swallowing in a path that decides something.
#
# They are identified by the statement inside their try block, not by line
# number. Keying on a line number meant that inserting anything above one of
# them silently turned a known-benign entry into "a fourth swallow" - the check
# fired on code nobody had touched, which is how a guard gets switched off.
def _swallow_sites(src: str, name: str) -> list[tuple[str, str]]:
    lines = src.split("\n")
    found: list[tuple[str, str]] = []
    for i, line in enumerate(lines):
        if not re.match(r"\s*except Exception[^\n]*:\s*$", line):
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines) or lines[j].strip() not in ("pass", "return None"):
            continue
        opens = [k for k in range(i) if lines[k].strip() == "try:"]
        # The signature is the statement the except protects, not the try's
        # first line: with nested try blocks "the nearest preceding try:" names
        # the wrong one.
        sig = lines[i - 1].strip() if i else "?"
        assert opens, f"{name}:{i + 1} swallows with no try above it"
        found.append((f"{name}:{i + 1}", sig))
    return found


swallowed: list[str] = []
signatures: dict[str, str] = {}
for f in PYFILES:
    for site, sig in _swallow_sites(read(f), f):
        swallowed.append(site)
        signatures[site] = sig
BENIGN_SIGS = {
    "frontend.async_remove_panel(hass, PANEL_URL)",
    'result["db_size_mb"] = round(size / 1024 / 1024, 1)',
    "return datetime.fromisoformat(date_str).date()",
}
unexpected = [x for x in swallowed if signatures[x] not in BENIGN_SIGS]
check("no bare swallow outside the three known-optional lookups",
      not unexpected, f"new: {unexpected}")
check("those three are still the only ones, so a fourth is noticed",
      sorted(signatures.values()) == sorted(BENIGN_SIGS),
      f"now: {sorted(signatures.items())}")

scheduled = re.findall(r"async_track_time_interval\([^,]+,\s*(\w+),\s*timedelta\(([^)]*)\)", init)
check("the scheduled jobs are still the two we know about",
      sorted(n for n, _ in scheduled) == ["_do_scan", "_soft_delete_check_cb"],
      f"found {scheduled}")

check("no token can reach the log",
      not re.search(r"_LOGGER\.\w+\([^)]*token[^)]*\)", init, re.I | re.S))

# ═══ 4. the store still refuses to prune trash snapshots ═══════════════════
print("\nthe trash snapshot still outlives a scan")
store = read("store.py")
save = store[store.index("async def async_save_scan_results"):]
parts = save.split("\n    async def ")
save = parts[0] + ("\n    async def " + parts[1] if len(parts) > 1 else "")
check("the save path never touches the trash records",
      "_soft_data" not in re.sub(r'""".*?"""', "", save, flags=re.S),
      "1.7.0 lost the restore snapshot exactly this way")

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("all fail-loud checks passed")
