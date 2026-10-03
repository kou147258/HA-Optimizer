"""Two properties of the trash records that both failed silently.

1. `async_get_soft_deleted` returned `dict(self._soft_data)` - a SHALLOW copy.
   The records themselves were the live ones, so the panel's read-only results
   poll, which annotates each record with `status`, `current_entity_id`,
   `expires_at` and `days_left`, was mutating live store state from outside the
   lock, and the next unrelated save persisted it. Verified by running it: one
   poll left four display-only fields on the record on disk.

2. `async_get_expired_soft_deleted(0)` returned every entry, because the rule
   was `age >= days` and 0 qualifies everything - while the README documents 0
   as "I will empty the trash myself". An off-switch that mass hard-deletes is
   worse than no off-switch, and the options flow refused 0 anyway, so the
   documented route could not reach it and every other route could.

This runs the real store against stubbed HA storage; no file is written.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ha_optimizer"

results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


# ── the smallest homeassistant that store.py imports ────────────────────────
class _Store:
    def __init__(self, hass, version, key, *, private=False, **kw):
        self.key = key
        self._data = kw.get("data")

    async def async_load(self):
        return self._data

    async def async_save(self, data):
        self._data = data


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules.setdefault(name, m)
    return m


ha = _mod("homeassistant")
ha.__path__ = []                       # type: ignore[attr-defined]
helpers = _mod("homeassistant.helpers")
helpers.__path__ = []                  # type: ignore[attr-defined]
core = _mod("homeassistant.core", HomeAssistant=type("HomeAssistant", (), {}))
util = _mod("homeassistant.util")
util.__path__ = []                     # type: ignore[attr-defined]
import datetime as _dt_mod             # noqa: E402

dt_util = types.ModuleType("homeassistant.util.dt")
dt_util.utcnow = lambda: _dt_mod.datetime.now(_dt_mod.timezone.utc)
util.dt = dt_util
sys.modules["homeassistant.util.dt"] = dt_util
_mod("homeassistant.helpers.storage", Store=_Store)
_mod("homeassistant.helpers.entity_registry",
     async_get=lambda hass: None, RegistryEntryDisabler=type("D", (), {"USER": "u"}))
_mod("homeassistant.helpers.event",
     async_track_time_interval=lambda *a, **k: (lambda: None))

pkg = types.ModuleType("haopt")
pkg.__path__ = [str(COMPONENT)]         # type: ignore[attr-defined]
sys.modules.setdefault("haopt", pkg)
spec = types.ModuleType("haopt.store")
spec.__file__ = str(COMPONENT / "store.py")
exec(compile((COMPONENT / "store.py").read_text(encoding="utf-8"),
             str(COMPONENT / "store.py"), "exec"), spec.__dict__)


def make(soft_data, now_iso):
    import datetime as _dt

    class _Hass:
        data: dict = {}
    store = spec.PurgeStore(_Hass())
    store._soft_data = dict(soft_data)
    store._scan_data = {}
    return store


# A record that is eight days old, and one that is not.
OLD = "2026-09-26T00:00:00+00:00"
NEW = "2026-10-04T00:00:00+00:00"
RECORDS = {
    "sensor.old": {"disabled_at": OLD, "unique_id": "old", "platform": "demo"},
    "sensor.new": {"disabled_at": NEW, "unique_id": "new", "platform": "demo"},
}

# ── 1. the copy must not be shallow ─────────────────────────────────────────
store = make(RECORDS, None)
got = asyncio.run(store.async_get_soft_deleted())
got["sensor.old"]["status"] = "ok"
got["sensor.old"]["days_left"] = 0
got["sensor.old"]["current_entity_id"] = "sensor.old"
got["sensor.old"]["expires_at"] = OLD
got["added"] = {"disabled_at": NEW}

check("a read does not expose the live record",
      "status" not in store._soft_data["sensor.old"],
      f"the store now carries {sorted(store._soft_data['sensor.old'])}")
check("a read cannot add a key to the store",
      "added" not in store._soft_data)
check("the number of records is still what the store holds",
      len(store._soft_data) == 2, f"got {len(store._soft_data)}")

# ── 2. zero means never, and so does anything below it ──────────────────────
for days in (0, -1, -30):
    expired = asyncio.run(make(RECORDS, None).async_get_expired_soft_deleted(days))
    check(f"a window of {days} day(s) expires nothing",
          expired == [], f"got {expired}")

check("a normal window still expires the old one",
      asyncio.run(make(RECORDS, None).async_get_expired_soft_deleted(7)) == ["sensor.old"],
      "the guard must not have broken the feature it protects")
check("a window of 1 day expires only the old one",
      asyncio.run(make(RECORDS, None).async_get_expired_soft_deleted(1)) == ["sensor.old"],
      "a record made today is 0 days old and 0 >= 1 is false")

# A record stamped right now, so the assertion does not depend on when this
# test happens to run - the fixture dates above are fixed, "now" is not.
fresh = dict(RECORDS)
fresh["sensor.fresh"] = {
    "disabled_at": _dt_mod.datetime.now(_dt_mod.timezone.utc).isoformat(),
    "unique_id": "fresh", "platform": "demo",
}
check("a record made this second is not expired by a 1-day window",
      "sensor.fresh" not in asyncio.run(make(fresh, None).async_get_expired_soft_deleted(1)))
check("and is still not expired by a 7-day window",
      "sensor.fresh" not in asyncio.run(make(fresh, None).async_get_expired_soft_deleted(7)))

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
