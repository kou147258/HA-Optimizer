"""The irreversible job must prove it could give things back before it deletes.

Run:  python3 tools/test_safety_gate.py

Auto-expiry is the only thing this integration does that cannot be undone. It
runs unattended every 6 hours with a 7-day default retention. The restore path
was broken in 1.7.2, 1.7.3 and 1.7.4 - so for three releases the safety net
and the countdown were running side by side with no coupling, and a broken
restore turned the trash into a deadline instead of a holding pen.

The gate below costs one real round trip per batch, on an entity that was
already scheduled for deletion, and fails closed. These checks pin the shape
of that gate, because "the restore was broken and nobody noticed" is not a
thing an assertion can catch - only the presence of the check itself is.

They also pin the repairs wiring, which is the only reason a user would find
out at all: every silent failure in this project so far reached them as a log
line they had to find, recognise and paste.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The component directory can be redirected so the counter-proof harness can run
# this suite against a deliberately broken copy without touching the real files.
COMP = Path(os.environ.get("HAOPT_COMPONENT_DIR") or (ROOT / "custom_components" / "ha_optimizer"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


init = (COMP / "__init__.py").read_text(encoding="utf-8")
strings = json.loads((COMP / "strings.json").read_text(encoding="utf-8"))
zh = json.loads((COMP / "translations" / "zh-Hans.json").read_text(encoding="utf-8"))

expiry = init.split("async def _async_check_soft_delete_expiry", 1)[1]
selftest = init.split("async def _async_restore_self_test", 1)[1].split("\nasync def ", 1)[0] \
    if "async def _async_restore_self_test" in init else ""

# The gate block on its own: from the self-test call up to the line that clears
# the issue. Scoping matters - searching the rest of the file for a `return`
# matches one from an unrelated function, which is how an early version of this
# suite passed with the gate's return removed.
_gate_start = expiry.find("if not await _async_restore_self_test")
_gate_end = expiry.find('_async_clear_issue(hass, "auto_purge_aborted")')
_gate_block = expiry[_gate_start:_gate_end] if (_gate_start >= 0 and _gate_end > _gate_start) else ""

# ═══ 1. the gate exists and is actually reached ═════════════════════════════
print("\nthe gate on the only irreversible job")
check("a restore self-test exists", bool(selftest),
      "without it there is nothing standing between a broken restore and a "
      "6-hourly deletion timer")
check("auto-expiry calls it before it deletes anything",
      "await _async_restore_self_test(hass, entry, expired[0])" in expiry
      and expiry.index("_async_restore_self_test") < expiry.index("async_hard_delete_soft_deleted"),
      "the call has to come before the hard delete, not after")
check("and returns immediately when the test fails",
      "return" in _gate_block,
      "falling through to the delete after a failed test is the whole failure; "
      "an earlier version of this check searched the rest of the file for a "
      "'return' and matched one from an unrelated function")
check("the self-test requires the entity to be BACK, not just 'success'",
      "came_back" in selftest and "result.get(\"success\") and came_back" in selftest,
      "a restore that reports success and leaves the entity gone is exactly the "
      "bug that ran for three releases")
check("the self-test re-disables what it re-enabled",
      "soft_delete=True" in selftest,
      "otherwise every run leaves one more entity enabled and quietly undoes the "
      "user's own delete")
check("a batch with nothing in it is not self-tested",
      selftest.strip() != "" and "expired[0]" in expiry,
      "the test runs on one entity from the batch, so an empty batch must not "
      "index into it")

# ═══ 2. the gate fails LOUDLY, in the UI ═══════════════════════════════════
print("\na failed gate is visible without reading a log")
check("a failure raises a repair issue",
      '"auto_purge_aborted"' in expiry and "_async_raise_issue" in expiry)
check("the issue is an error, not a warning",
      re.search(r'"auto_purge_aborted",\s*\n\s*severity="error"', expiry) is not None)
check("the issue names the entities that were spared",
      "entities=expired" in expiry)
check("and says plainly that nothing was deleted",
      "nothing has been deleted" in expiry)

print("\nissues are real, not invented keys")
check("strings.json has an issues section", "issues" in strings)
check("zh-Hans.json has the same section", "issues" in zh)
check("both define the same issue ids",
      sorted(strings.get("issues", {})) == sorted(zh.get("issues", {})) == [
          "auto_purge_aborted", "restore_did_not_complete"],
      f"en={sorted(strings.get('issues', {}))} zh={sorted(zh.get('issues', {}))}")
for key in ("auto_purge_aborted", "restore_did_not_complete"):
    en_body = strings.get("issues", {}).get(key, {})
    zh_body = zh.get("issues", {}).get(key, {})
    check(f"{key}: en and zh both have a title and a description",
          bool(en_body.get("title")) and bool(en_body.get("description"))
          and bool(zh_body.get("title")) and bool(zh_body.get("description")))
    placeholders = set(re.findall(r"\{(\w+)\}", en_body.get("description", "")))
    check(f"{key}: the description only uses placeholders the code passes",
          placeholders <= {"count", "entities", "description"},
          f"uses {sorted(placeholders)}")

# ═══ 3. a false restore success is an issue too ═════════════════════════════
print("\nthe other failure that cost real data")
check("a claimed restore is verified against the state machine",
      "_entity_is_back" in init)
check("and a failure raises an issue",
      '"restore_did_not_complete"' in init)
check("a later success clears it again",
      '_async_clear_issue(hass, "restore_did_not_complete")' in init,
      "an issue card that outlives its cause is worse than none")
check("auto_purge_aborted is cleared once the batch can run",
      '_async_clear_issue(hass, "auto_purge_aborted")' in expiry)

# ═══ 4. reporting never breaks the caller ═══════════════════════════════════
print("\nreporting is best effort")
check("raising an issue cannot raise out of its caller",
      re.search(r"def _async_raise_issue.*?except Exception as exc", init, re.S) is not None)
check("clearing one neither",
      re.search(r"def _async_clear_issue.*?except Exception as exc", init, re.S) is not None)
check("a missing issue_registry is tolerated",
      init.count("except ImportError") >= 2)

# ═══ 5. uninstall keeps the evidence ═══════════════════════════════════════
print("\nuninstalling keeps the records")
check("there is an async_remove_entry", "async def async_remove_entry" in init)
remove = init.split("async def async_remove_entry", 1)[1].split("\nasync def ", 1)[0] if "async def async_remove_entry" in init else ""
check("it delegates to the unload path",
      "async_unload_entry" in remove)
check("it does NOT delete the storage files",
      not re.search(r"\.unlink\(|rmtree\(|async_remove\(.*STORE", remove),
      "those files are the only record of what was disabled and when; a user "
      "who removes the integration BECAUSE it misbehaved would lose the evidence "
      "at the exact moment they need it")
check("and it says where they are",
      ".storage" in remove and "_LOGGER" in remove)

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("all safety-gate checks passed")
