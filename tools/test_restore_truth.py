"""Guards for the restore path, after a live instance reported that restoring
an automation behaved exactly like deleting it.

Run:  python3 tools/test_restore_truth.py
      python3 tools/test_restore_truth.py [store.py] [panel.html] [__init__.py]

The path overrides exist so tools/counterproof_restore.py can point every
assertion at a deliberately broken copy and prove the suite can still fail.

The restore had worked. The panel said otherwise, and that is the same class of
defect this project keeps meeting: the store is right, the screen is not.

  1. async_restore_scan_entries replayed the trash snapshot verbatim. The
     snapshot's `disabled` is the state at scan time, and the reason a disabled
     automation is a candidate at all is `reason_auto_disabled` - so the
     snapshot says disabled, the restore un-disables it, and the row comes
     back still flagged. For this feature's main use case the wrong answer was
     guaranteed, not occasional.
  2. The `re_enabled: false` branch toasted "<id> -> empty trash", which reads
     as "this got deleted".
  3. The table is rendered from the in-memory list captured at scan time, so
     the restored row never appeared until the next scan - up to
     scan_interval_days away.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMP = ROOT / "custom_components" / "ha_optimizer"
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

STORE = Path(sys.argv[1]) if len(sys.argv) > 1 else COMP / "store.py"
PANEL = Path(sys.argv[2]) if len(sys.argv) > 2 else COMP / "panel.html"
INIT = Path(sys.argv[3]) if len(sys.argv) > 3 else COMP / "__init__.py"

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


store = STORE.read_text(encoding="utf-8")
panel = PANEL.read_text(encoding="utf-8")
init = INIT.read_text(encoding="utf-8")


def between(text: str, start: str, end: str) -> str:
    if start not in text:
        return ""
    tail = text.split(start, 1)[1]
    return tail.split(end, 1)[0] if end in tail else tail


fn = between(store, "async def async_restore_scan_entries", "\n    async def ")
restore_js = between(panel, "async function restoreEntity", "async function hardDeleteEntity")
handle = between(init, "async def handle_restore(", "async def ")

check("the restore function was found", bool(fn) and bool(restore_js) and bool(handle),
      "a rename would make every assertion below pass blind")

# == 1. the row that comes back must be true, not a replay ==================
print("\nrestore: the row that comes back must be true")
check("the snapshot is copied, not appended by reference",
      "entry = dict(snapshot)" in fn,
      "appending the snapshot object itself would let a later scan mutate the "
      "stored trash record")
check("`disabled` is re-read from the entity registry before the row is written",
      "ent_reg.async_get(target)" in fn
      and 'entry["entity_id"] = target' in fn
      and 'entry["disabled"]' in fn,
      "replaying the snapshot's disabled flag is what made a successful "
      "restore look like a failed one; and the registry must be asked about "
      "the same id the row is written under, or the two can diverge again")
check("the stale `reason_auto_disabled` is dropped once the entity is enabled",
      '"reason_auto_disabled" in reasons' in fn and 'r != "reason_auto_disabled"' in fn,
      "otherwise the row keeps flagging a reason that was just fixed")
check("the entity registry is imported at module level",
      "from homeassistant.helpers import entity_registry as er" in store)
check("an entity that is not in the registry leaves the snapshot alone",
      "if current is not None:" in fn,
      "guessing a disabled state for a missing entity would put a second "
      "invention into the same function")

# == 2. the message must match the outcome =================================
print("\nrestore: the message must match the outcome")
not_reenabled = restore_js.split("re_enabled", 1)[-1][:600] if "re_enabled" in restore_js else ""
check("the not-re-enabled branch does not claim the entity was emptied",
      "emptyTrash" not in not_reenabled,
      "'<id> -> empty trash' is the opposite of what happened and reads as a delete")
check("it uses a dedicated key instead", "t('restoreAlreadyEnabled')" in restore_js)
check("that key is defined in the dictionary", "restoreAlreadyEnabled:" in panel,
      "a missing key falls through to t()'s `?? key` and the user sees the "
      "identifier instead of a message")
check("the message is routed through t() rather than written in English",
      "${t('restoreAlreadyEnabled')}" in restore_js)

# == 3. the table must reflect the store ====================================
print("\nrestore: the table must reflect the store")
check("the panel re-reads the scan list after a restore",
      "get_results" in restore_js and "displayResults" in restore_js,
      "the table is rendered from the list captured at scan time, so without a "
      "refresh the restored entity stays invisible until the next scan")
check("that refresh updates the local cache too",
      "localStorage.setItem('ha_optimizer_results'" in restore_js,
      "otherwise a reload shows the old list again and the fix looks cosmetic")
check("a failed refresh cannot break a restore that already happened",
      "console.warn" in restore_js.split("get_results", 1)[-1][:600])

# == 4. nothing is dropped on a failure =====================================
print("\nrestore: nothing is dropped on a failure")
check("the trash record is removed only when the restore succeeded",
      'if result.get("success"):' in handle and "async_remove_soft_deleted" in handle)
check("the scan entry is put back before the snapshot is discarded",
      "async_restore_scan_entries" in handle
      and handle.index("async_restore_scan_entries") < handle.index("async_remove_soft_deleted"),
      "the snapshot lives in the trash record; the other order loses it")
check("a vanished entity is not reported as a successful restore",
      "_verified_restore" in handle and "no longer in the registry" in init,
      "a hard delete removes the config entry and the entity goes with it")

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print(f"all {16} restore-truth checks passed")
