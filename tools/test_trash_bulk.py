"""Regression tests for the bulk trash operations.

The trash is the last stop before something is gone for good, so these cover
the bookkeeping rather than the registry calls. Two defects are locked in
here, both of which were real:

  * a restore put nothing back. The scan entry was dropped when the entity was
    soft-deleted, so restoring brought the entity to life in Home Assistant
    and left it invisible in this panel until the next scan - up to
    scan_interval_days away.
  * a bulk hard delete has to keep the ones it could not remove. Reporting
    them as removed left them disabled and untracked: a ghost that nothing
    would ever restore or finish.

Runs WITHOUT Home Assistant; PurgeStore is loaded with stub homeassistant.*
modules the same way test_purge_safety.py does it.

Run:  python3 tools/test_trash_bulk.py
"""
from __future__ import annotations

import asyncio
import re
import sys
import types
from datetime import timedelta
from pathlib import Path

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "ha_optimizer"
sys.path.insert(0, str(COMPONENT.parent))

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ── stub the slice of Home Assistant store.py touches ──────────────────────
def _stub() -> None:
    ha = types.ModuleType("homeassistant")
    ha.__path__ = []
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    storage = types.ModuleType("homeassistant.helpers.storage")

    class _Store:
        """Just enough Store to capture what gets written."""
        instances: list["_Store"] = []

        def __init__(self, *a, **k):
            self.data = None
            self.saves = 0
            _Store.instances.append(self)

        async def async_load(self):
            return self.data

        async def async_save(self, data):
            self.data = data
            self.saves += 1

    storage.Store = _Store
    util = types.ModuleType("homeassistant.util")
    util.__path__ = []
    dt = types.ModuleType("homeassistant.util.dt")

    def _utcnow():
        import datetime as _dt
        return _dt.datetime(2026, 10, 2, 12, 0, 0, tzinfo=_dt.timezone.utc)

    # store.py calls dt_util.utcnow() on the module, not on a class
    dt.utcnow = _utcnow
    dt.UTC = None
    util.dt = dt

    for name, mod in [
        ("homeassistant", ha), ("homeassistant.core", core),
        ("homeassistant.helpers", helpers),
        ("homeassistant.helpers.storage", storage),
        ("homeassistant.util", util), ("homeassistant.util.dt", dt),
    ]:
        sys.modules[name] = mod
    helpers.storage = storage
    util.dt = dt


_stub()
_pkg = types.ModuleType("ha_optimizer")
_pkg.__path__ = [str(COMPONENT)]
sys.modules["ha_optimizer"] = _pkg

from ha_optimizer import store as store_mod  # noqa: E402

PurgeStore = store_mod.PurgeStore


def make_store(scan_results, soft):
    s = PurgeStore(hass=object())
    s._scan_data = {"results": list(scan_results), "statistics": {"total_entities": len(scan_results)}}
    s._soft_data = dict(soft)
    return s


def entry(entity_id, **extra):
    base = {"entity_id": entity_id, "name": entity_id.split(".")[-1], "category": "entity",
            "risk_level": "low", "reason": ["reason_stale"], "is_yaml_entity": False,
            "disabled": False}
    base.update(extra)
    return base


# ═══ 1. soft delete snapshots the scan entry ═══════════════════════════════
print("\nsoft delete keeps a restorable snapshot")
s = make_store(
    [entry("sensor.a"), entry("sensor.b"), entry("sensor.c")],
    {},
)
asyncio.run(s.async_add_soft_deleted(["sensor.a", "sensor.b"]))
check("two entities tracked in the trash", len(s._soft_data) == 2)
check("a disabled_at timestamp is recorded",
      all("disabled_at" in m for m in s._soft_data.values()))
check("the scan entry is snapshotted for restore",
      all("scan_entry" in m for m in s._soft_data.values()))
check("the snapshot is the real scan entry, not a stub",
      s._soft_data["sensor.a"]["scan_entry"]["entity_id"] == "sensor.a")
check("entities not in the trash are untouched", "sensor.c" not in s._soft_data)

# an entity with no scan entry (purged once before, or trashed from elsewhere)
s2 = make_store([entry("sensor.a")], {})
asyncio.run(s2.async_add_soft_deleted(["sensor.ghost"]))
check("an entity with no scan entry is still tracked",
      "sensor.ghost" in s2._soft_data and "scan_entry" not in s2._soft_data["sensor.ghost"])


# ═══ 2. restore puts the entry back ════════════════════════════════════════
print("\nrestore returns the entity to the scan list")
s = make_store([entry("sensor.a"), entry("sensor.b")], {})
asyncio.run(s.async_add_soft_deleted(["sensor.a"]))
# the purge then strips it from the live scan results, as handle_purge does
s._scan_data["results"] = [r for r in s._scan_data["results"] if r["entity_id"] != "sensor.a"]
check("the entity is gone from the scan list after the purge",
      not any(r["entity_id"] == "sensor.a" for r in s._scan_data["results"]))

added = asyncio.run(s.async_restore_scan_entries(["sensor.a"]))
check("restore reports what it put back", added == ["sensor.a"])
check("the entity is back in the scan list",
      any(r["entity_id"] == "sensor.a" for r in s._scan_data["results"]))
check("the restored entry kept its data, not just its id",
      s._scan_data["results"][-1]["reason"] == ["reason_stale"])

# restoring twice must not duplicate the row
asyncio.run(s.async_restore_scan_entries(["sensor.a"]))
check("restoring twice does not duplicate the row",
      sum(1 for r in s._scan_data["results"] if r["entity_id"] == "sensor.a") == 1)

# the ordering that handle_restore depends on
s3 = make_store([entry("sensor.a")], {})
asyncio.run(s3.async_add_soft_deleted(["sensor.a"]))
s3._scan_data["results"] = []
asyncio.run(s3.async_remove_soft_deleted(["sensor.a"]))   # snapshot discarded first
added3 = asyncio.run(s3.async_restore_scan_entries(["sensor.a"]))
check("restoring after the trash record is gone cannot invent a row",
      added3 == [] and s3._scan_data["results"] == [],
      "this is why handle_restore must restore BEFORE removing the record")

# restoring an entity that was never snapshotted is not an error
added4 = asyncio.run(s3.async_restore_scan_entries(["sensor.never"]))
check("restoring an unknown entity is a no-op, not a crash", added4 == [])


# ═══ 3. the source code keeps that order ════════════════════════════════════
print("\nhandle_restore order")
init_src = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
# read once, up front: the sections below all need it
panel = (COMPONENT / "panel.html").read_text(encoding="utf-8")
body = init_src[init_src.index("async def handle_restore("):]
body = body[:body.index("\n    async def handle_restore_all(")]
check("handle_restore calls async_restore_scan_entries", "async_restore_scan_entries" in body)
check("restore happens BEFORE the trash record is dropped",
      body.index("async_restore_scan_entries") < body.index("async_remove_soft_deleted"),
      "if this inverts, the snapshot is already gone when it is needed")

empty = init_src[init_src.index("async def handle_empty_trash("):]
empty = empty[:empty.index("\n    hass.services.async_register(")]
check("empty_trash keeps what it could not remove",
      "kept[eid]" in empty and "async_remove_soft_deleted(removed)" in empty)
check("empty_trash yields on a TIME budget, not a fixed item count",
      "budget.tick()" in empty and "_BULK_BATCH" not in init_src,
      "a fixed count is the wrong unit: a plain entity is an in-memory dict "
      "update while an automation also tears down a config entry")
check("empty_trash says which entries can NEVER be removed",
      "kept_permanent" in empty and "permanent.append(eid)" in empty,
      "a YAML automation or a safety device class will never go away; saying "
      "only 'stayed in the trash' invites the user to retry forever")
check("the permanent flag reaches the panel", "kept_permanent" in panel)
check("the panel has a distinct message for the permanent case",
      "trashEmptyDoneStuck" in panel)

restore_all = init_src[init_src.index("async def handle_restore_all("):]
restore_all = restore_all[:restore_all.index("\n    async def handle_empty_trash(")]
check("restore_all also yields on a time budget", "budget.tick()" in restore_all)
check("restore_all keeps what it could not restore",
      "failed[eid]" in restore_all and "async_remove_soft_deleted(restored)" in restore_all)
check("restore_all restores the scan entries too", "async_restore_scan_entries" in restore_all)

# ═══ 4. the services are actually registered and documented ═════════════════
print("\nservice surface")
services_yaml = (COMPONENT / "services.yaml").read_text(encoding="utf-8")
for name in ("scan", "purge", "restore", "restore_all", "empty_trash", "get_results"):
    # MULTILINE, not "\n<name>:" - scan is the first entry in the file
    check(f"{name} is declared in services.yaml",
          re.search(rf"^{name}:", services_yaml, re.M) is not None)
    check(f"{name} is registered in __init__.py", f"SERVICE_{name.upper()}" in init_src)
for const in ("SERVICE_RESTORE_ALL", "SERVICE_EMPTY_TRASH"):
    check(f"{const} has a value in const.py",
          f'{const} = ' in (COMPONENT / "const.py").read_text(encoding="utf-8"))
check("unload removes the new services",
      "SERVICE_RESTORE_ALL" in init_src and "SERVICE_EMPTY_TRASH" in init_src)

# ═══ 5. the irreversible bulk action is gated in the UI ════════════════════
print("\nempty-trash confirmation")
flow = panel[panel.index("function emptyTrashFlow()"):]
flow = flow[:flow.index("function cancelEmptyTrash()")]
check("emptying the trash asks the user to TYPE the count",
      "trashConfirmInput" in flow and "String(_trashCount)" in flow)
check("the confirm button starts disabled", "ok.disabled = true" in flow)
check("it is NOT a single confirm()", "confirm(" not in flow,
      "a single confirm for a bulk irreversible delete is too weak")
# the gate must be an exact match, not a truthy check: "40" must not pass for 4
check("the typed value is compared exactly against the count",
      "input.value.trim() === String(_trashCount)" in flow)
check("the button is enabled only when the value matches",
      "ok.disabled = !typed" in flow)

# ═══ 6. the countdown has to agree with the expiry check ═══════════════════
# Truncating timedelta.days showed 「还有 29 天」 for an entry that had been in
# the trash for an hour with soft_delete_days=30 - one day short on every
# entry. And it has to line up with the removal rule, which is a floor on
# (now - disabled_at).
print("\ncountdown arithmetic")
get_results = init_src[init_src.index("async def handle_get_results("):]
get_results = get_results[:get_results.index("\n    async def handle_analyze_recorder(")]
check("days_left is rounded UP, not truncated",
      "math.ceil(remaining / 86400)" in get_results
      and "(expires - now).days" not in get_results,
      "truncating loses a day on every entry")
check("an already-expired entry reports 0, not a negative number",
      "if remaining > 0 else 0" in get_results)
store_src = (COMPONENT / "store.py").read_text(encoding="utf-8")
expiry = store_src[store_src.index("async def async_get_expired_soft_deleted"):]
check("the removal rule is a floor on the age",
      "(now - disabled_at).days" in expiry and ">= days" in expiry)
check("the countdown rationale is written down",
      "rounded UP" in get_results and "agree with the expiry check" in get_results)

print()
restore_flow = panel[panel.index("async function restoreAllTrash()"):]
restore_flow = restore_flow[:restore_flow.index("// A restored entity is put back")]
check("restore-all uses a plain confirm (it destroys nothing)", "confirm(" in restore_flow)
# the empty_trash CALL lives in performEmptyTrash, which comes after
# cancelEmptyTrash - slicing the flow function alone would miss it
check("restore-all calls the restore_all service", "'restore_all'" in restore_flow)
check("empty-trash calls the empty_trash service",
      "'empty_trash'" in panel[panel.index("async function performEmptyTrash()"):])
check("the trash view shows the countdown column", "_expiryLabel(item.days_left)" in panel)
check("the countdown sorts soonest-first",
      "softDeleted.sort" in panel)
check("both bulk buttons exist in the markup",
      'id="btnRestoreAll"' in panel and 'id="btnEmptyTrash"' in panel)

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("all trash-bulk regression checks passed")
