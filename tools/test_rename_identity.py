#!/usr/bin/env python3
"""Behaviour tests for trash records that outlive their entity_id.

Since HA 2026.8 the user can rename an entity_id and choose how its parts are
laid out. These records are keyed by entity_id, so before this the failure was
silent and total: disable an entity, rename it, press Restore, and the tool
answered "not found in the registry - it was deleted, not disabled" while the
entity sat there, still disabled, under its new name. Nothing was wrong with the
entity and nothing said so.

The tests execute the real store.py against a fake registry, so what is under
test is the shipped code rather than a transcription of it, and the rename is a
fact rather than a string comparison.

Usage:  python3 tools/test_rename_identity.py [repo root]
Exit:   0 all passed, 1 a failure, 2 could not run
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
COMP = ROOT / "custom_components" / "ha_optimizer"

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((bool(ok), name, detail))


# ── the minimum Home Assistant surface the store touches ────────────────────
class Entry:
    def __init__(self, entity_id, unique_id, platform, domain):
        self.entity_id = entity_id
        self.unique_id = unique_id
        self.platform = platform
        self.domain = domain
        self.disabled = False


class FakeRegistry:
    """Only the two calls the resolver makes."""

    def __init__(self):
        self.entries: list[Entry] = []

    def add(self, entity_id, unique_id, platform, domain):
        self.entries.append(Entry(entity_id, unique_id, platform, domain))
        return self.entries[-1]

    def async_get(self, entity_id):
        return next((e for e in self.entries if e.entity_id == entity_id), None)

    def async_get_entity_id(self, domain, platform, unique_id):
        for e in self.entries:
            if (e.domain, e.platform, e.unique_id) == (domain, platform, unique_id):
                return e.entity_id
        return None


class FakeEr:
    registry = FakeRegistry()

    @staticmethod
    def async_get(hass):
        return FakeEr.registry


class FakeStoreHandle:
    async def async_save(self, data):
        return None


class FakeErModule:
    """Stands in for homeassistant.helpers.entity_registry."""

    RegistryEntryDisabler = types.SimpleNamespace(USER="user", INTEGRATION="integration")

    @staticmethod
    def async_get(hass):
        return FakeEr.registry


def load_store_module():
    """Exec the real store.py with Home Assistant replaced by fakes."""
    src = (COMP / "store.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    src = src.replace("from homeassistant.core import HomeAssistant", "HomeAssistant = object")
    src = src.replace("from homeassistant.helpers import entity_registry as er", "er = _ER")
    src = src.replace("from homeassistant.helpers.storage import Store", "Store = _Store")
    src = src.replace("from homeassistant.util import dt as dt_util", "dt_util = _DT")
    src = src.replace("from .const import DOMAIN, SOFT_DELETE_STORE_KEY, STORE_KEY",
                      "DOMAIN = 'ha_optimizer'\nSOFT_DELETE_STORE_KEY = 'sd'\nSTORE_KEY = 'st'")
    ns = {
        "__name__": "ha_optimizer._store_under_test",
        "_ER": FakeErModule,
        "_Store": FakeStoreHandle,
        "_DT": types.SimpleNamespace(utcnow=lambda: dt.datetime(2026, 10, 3, tzinfo=dt.timezone.utc)),
    }
    exec(compile(src, "store.py", "exec"), ns)  # noqa: S102 - the point of the test
    return ns


def make_store(ns):
    """A PurgeStore with its storage handles stubbed.

    `hass` must NOT be None: the store guards every registry call with
    `if self.hass is not None`, so a None here silently skips capturing the
    identity and every rename resolves as gone.
    """
    store = ns["PurgeStore"].__new__(ns["PurgeStore"])
    store.hass = object()
    store._scan_store = FakeStoreHandle()
    store._soft_store = FakeStoreHandle()
    store._scan_data = {"results": []}
    store._soft_data = {}
    store._lock = asyncio.Lock()
    store._removed_since_scan = set()
    store._added_since_scan = {}
    return store


async def main() -> int:
    try:
        ns = load_store_module()
    except Exception as exc:  # noqa: BLE001
        print(f"could not load store.py: {exc}")
        return 2

    FakeEr.registry = FakeRegistry()
    reg = FakeEr.registry
    reg.add("automation.morning", "uid-morning", "automation", "automation")
    store = make_store(ns)

    # ── 1. writing the record captures the registry identity ────────────────
    await store.async_add_soft_deleted(["automation.morning"])
    meta = store._soft_data.get("automation.morning", {})
    check(meta.get("unique_id") == "uid-morning", "the trash record stores unique_id", str(meta))
    check(meta.get("platform") == "automation", "and the platform", str(meta))
    check(meta.get("domain") == "automation", "and the domain", str(meta))
    check("disabled_at" in meta, "and keeps the timestamp it always had", str(meta))

    # ── 2. nothing renamed ──────────────────────────────────────────────────
    got = await store.async_resolve_soft_deleted("automation.morning")
    check(got["status"] == "ok" and got["entity_id"] == "automation.morning",
          "an unchanged record resolves to itself", str(got))

    # ── 3. the case this was written for ───────────────────────────────────
    reg.entries[0].entity_id = "automation.morning_livingroom"
    got = await store.async_resolve_soft_deleted("automation.morning")
    check(got["status"] == "renamed",
          "a renamed entity is reported as renamed, not missing", str(got))
    check(got["entity_id"] == "automation.morning_livingroom",
          "and resolves to where the entity lives now", str(got))
    check(got["renamed_from"] == "automation.morning",
          "keeping the trash key so the right record gets removed", str(got))

    # ── 4. unique_id is not unique across platforms ─────────────────────────
    reg.entries[0].entity_id = "automation.morning"      # the automation is back
    reg.add("sensor.same_uid", "uid-morning", "demo", "sensor")
    del reg.entries[0]                                    # ...and now removed again
    got = await store.async_resolve_soft_deleted("automation.morning")
    check(got["status"] == "gone",
          "a unique_id reused on another platform is a different entity", str(got))

    # ── 5. genuinely gone ───────────────────────────────────────────────────
    reg.entries.clear()
    got = await store.async_resolve_soft_deleted("automation.morning")
    check(got["status"] == "gone", "an entity that really is gone says gone", str(got))
    check(got["identity_known"] is True,
          "and still reports that it had an identity, so the message can differ",
          str(got))

    # ── 6. a record written before identities were stored ───────────────────
    store._soft_data["automation.legacy"] = {"disabled_at": "2026-01-01T00:00:00+00:00"}
    got = await store.async_resolve_soft_deleted("automation.legacy")
    check(got["status"] == "gone" and got["identity_known"] is False,
          "a legacy record admits it has no way to know", str(got))

    # ── 7. the restored scan row follows the rename ─────────────────────────
    reg.add("automation.morning_new", "uid-morning", "automation", "automation")
    store._soft_data["automation.morning"] = {
        "disabled_at": "2026-10-01T00:00:00+00:00",
        "unique_id": "uid-morning",
        "platform": "automation",
        "domain": "automation",
        "scan_entry": {"entity_id": "automation.morning", "risk_level": "low",
                       "reason": ["reason_auto_disabled"], "disabled": True},
    }
    added = await store.async_restore_scan_entries(
        ["automation.morning"], {"automation.morning": "automation.morning_new"})
    rows = [r for r in store._scan_data["results"] if "morning" in r.get("entity_id", "")]
    check(added == ["automation.morning"],
          "the restore reports the trash key, not the new id", str(added))
    check(len(rows) == 1 and rows[0]["entity_id"] == "automation.morning_new",
          "the scan row is written under the id the entity has now", str(rows))
    check("automation.morning\"" not in repr(rows[0]),
          "no stale row is left under the id that no longer exists", str(rows))

    # ── 8. the restore path resolves, and the wording survives ───────────────
    init_src = (COMP / "__init__.py").read_text(encoding="utf-8")
    start = init_src.index("async def _verified_restore")
    tail = init_src[start:]
    fn = tail[: tail.index("\nasync def ")] if "\nasync def " in tail else tail
    check("async_resolve_soft_deleted" in fn,
          "the restore path resolves the record before restoring")
    check("store is not None" in fn,
          "and only when handed the trash, so the self-test path is untouched")
    check("renamed" in fn and "renamed_from" in fn,
          "and reports the rename instead of a plain success")
    check("no longer in the registry - it was deleted, not disabled" in fn,
          "the deleted/not-disabled wording survives for the case it is true of")
    check("async_resolve_soft_deleted" in init_src[init_src.index("async def handle_empty_trash"):],
          "empty-trash resolves too, or a renamed entity is 'kept' for ever")

    for ok, name, detail in results:
        print(f"{'  PASS' if ok else '  FAIL'}  {name}")
        if not ok and detail:
            print(f"        {detail}")
    failed = [r for r in results if not r[0]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    print("FAILED" if failed else "PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
