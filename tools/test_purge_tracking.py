"""A disabled entity must be in the trash before the next one is disabled.

The purge loop disabled every entity in the batch, and only when it returned did
the caller write a single trash record. A stop, a crash or a disk error in
between left entities disabled in the registry with no record - unrestorable,
invisible, and nothing to repair from, because the only list of them was the
one that was lost.

The engine now takes an `on_left_disabled` callback and awaits it immediately
after each registry write. This checks the property that matters, which is not
"the callback is called" but "an entity disabled before a later failure is still
recorded": that is exactly the case the old ordering lost.

So this runs the real engine against a stubbed registry, fails one entity part
way through the batch, and asserts the earlier ones were still recorded.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
ENGINE = ROOT / "custom_components" / "ha_optimizer" / "purge_engine.py"

results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


# ── a registry that fails on one chosen entity ─────────────────────────────
FAIL_ON = "sensor.boom"


class _Entry:
    def __init__(self, entity_id):
        self.entity_id = entity_id
        self.domain = entity_id.split(".")[0]
        self.platform = "demo"
        self.disabled = False
        self.disabled_by = None
        self.config_entry_id = "cfg"
        self.device_class = None
        self.original_device_class = None
        self.device_id = None
        self.unique_id = entity_id.split(".", 1)[1]


class _Reg:
    def __init__(self, ids, fail_remove=False):
        self._entries = {i: _Entry(i) for i in ids}
        self.disabled_now: list[str] = []
        self.removed_now: list[str] = []
        # Makes `_remove_by_domain` fall back to disabling, which is the only
        # way to reach the `disabled_only` branches.
        self._fail_remove = fail_remove

    def async_get(self, entity_id):
        return self._entries.get(entity_id)

    def async_update_entity(self, entity_id, **kw):
        if entity_id == FAIL_ON:
            raise RuntimeError("registry write failed")
        self._entries[entity_id].disabled = True
        self.disabled_now.append(entity_id)

    def async_remove(self, entity_id):
        if self._fail_remove:
            raise RuntimeError("removal refused")
        self.removed_now.append(entity_id)
        self._entries.pop(entity_id, None)


class _Disabler:
    USER = "user"
    INTEGRATION = "integration"
    CONFIG_ENTRY = "config_entry"


er = types.ModuleType("homeassistant.helpers.entity_registry")
er.async_get = lambda hass: None
er.RegistryEntryDisabler = _Disabler
# purge_engine imports `homeassistant.core` at the top, so the stub set has to
# be a package, not just a module in sys.modules.
ha = types.ModuleType("homeassistant")
ha.__path__ = []                      # type: ignore[attr-defined]
core = types.ModuleType("homeassistant.core")
core.HomeAssistant = type("HomeAssistant", (), {})
helpers = types.ModuleType("homeassistant.helpers")
helpers.__path__ = []                 # type: ignore[attr-defined]
helpers.entity_registry = er
# `homeassistant.util` and its `dt` submodule, imported at the top of
# purge_engine.py.
util = types.ModuleType("homeassistant.util")
util.__path__ = []                    # type: ignore[attr-defined]
dt_mod = types.ModuleType("homeassistant.util.dt")
dt_mod.utcnow = lambda: __import__("datetime").datetime.now(
    __import__("datetime").timezone.utc)
util.dt = dt_mod
for name, mod in (("homeassistant", ha), ("homeassistant.core", core),
                  ("homeassistant.helpers", helpers),
                  ("homeassistant.helpers.entity_registry", er),
                  ("homeassistant.util", util),
                  ("homeassistant.util.dt", dt_mod)):
    sys.modules.setdefault(name, mod)

# purge_engine.py does `from .const import ...`, so it has to execute as a
# module inside a package. The package is synthetic and its __path__ points at
# the real component directory, so `haopt.const` loads the shipped const.py
# rather than a copy that could drift from it.
pkg = types.ModuleType("haopt")
pkg.__path__ = [str(ENGINE.parent)]   # type: ignore[attr-defined]
sys.modules.setdefault("haopt", pkg)

spec = types.ModuleType("haopt.purge_engine")
spec.__file__ = str(ENGINE)
exec(compile(ENGINE.read_text(encoding="utf-8"), str(ENGINE), "exec"), spec.__dict__)
sys.modules["haopt.purge_engine"] = spec

async def run(ids, **kwargs):
    reg = _Reg(ids)
    er.async_get = lambda hass: reg
    recorded: list[str] = []

    async def _record(entity_id):
        recorded.append(entity_id)

    engine = spec.PurgeEngine(_Hass())
    out = await engine.async_purge_entities(ids, on_left_disabled=_record, **kwargs)
    return reg, recorded, out


class _Hass:
    data: dict = {}
    states = types.SimpleNamespace(async_all=lambda *a: [])


IDS = ["sensor.one", "sensor.two", FAIL_ON, "sensor.four"]

# The decisive property is not "the callback ran" but "the record was written
# next to the registry write, not after the batch". So the callback snapshots
# what the registry had done at the moment it was called: for the second entity
# it must see only the first two disables, never all of them. A batch write
# after the loop would show the full list every time.
reg = _Reg(IDS)
er.async_get = lambda hass: reg
recorded: list[str] = []
snapshots: list[tuple[str, list[str]]] = []


async def _record(entity_id):
    recorded.append(entity_id)
    snapshots.append((entity_id, list(reg.disabled_now)))


out = asyncio.run(spec.PurgeEngine(_Hass()).async_purge_entities(
    IDS, on_left_disabled=_record))

check("the entity whose registry write failed is not disabled and not recorded",
      FAIL_ON not in reg.disabled_now and FAIL_ON not in recorded,
      f"disabled={reg.disabled_now} recorded={recorded}")
check("every entity that was disabled was recorded",
      recorded == reg.disabled_now, f"recorded={recorded} disabled={reg.disabled_now}")
check("the second entity was recorded while only the first two were disabled",
      dict(snapshots)["sensor.two"] == ["sensor.one", "sensor.two"],
      f"saw {dict(snapshots).get('sensor.two')} - a record written after the "
      f"loop would have seen the whole batch")
check("the last entity was recorded when all of them were",
      dict(snapshots)["sensor.four"] == ["sensor.one", "sensor.two", "sensor.four"],
      f"saw {dict(snapshots).get('sensor.four')}")
check("the failure is still reported as a failure",
      [f["entity_id"] for f in out["failed"]] == [FAIL_ON],
      f"got {out['failed']}")
check("the engine reports exactly the entities it disabled",
      out["soft_deleted"] == ["sensor.one", "sensor.two", "sensor.four"],
      f"got {out['soft_deleted']}")

# A clean batch records each one, in order.
reg2, recorded2 = _Reg(["sensor.a", "sensor.b", "sensor.c"]), []
er.async_get = lambda hass: reg2


async def _record2(eid):
    recorded2.append(eid)


out2 = asyncio.run(spec.PurgeEngine(_Hass()).async_purge_entities(
    ["sensor.a", "sensor.b", "sensor.c"], on_left_disabled=_record2))
check("a clean batch records every entity once, in order",
      recorded2 == ["sensor.a", "sensor.b", "sensor.c"], f"got {recorded2}")

# Hard delete is unaffected: nothing is disabled, so nothing is recorded.
reg3, recorded3 = _Reg(["sensor.h"]), []
er.async_get = lambda hass: reg3


async def _record3(eid):
    recorded3.append(eid)


asyncio.run(spec.PurgeEngine(_Hass()).async_purge_entities(
    ["sensor.h"], soft_delete=False, on_left_disabled=_record3))
check("a hard delete records nothing, because nothing was disabled",
      recorded3 == [] and reg3.removed_now == ["sensor.h"], f"got {recorded3}")

# And the engine is still callable without the callback, so its own tests and
# any other caller keep working.
er.async_get = lambda hass: _Reg(["sensor.plain"])
out4 = asyncio.run(spec.PurgeEngine(_Hass()).async_purge_entities(["sensor.plain"]))
check("the engine still works when no callback is passed",
      out4["soft_deleted"] == ["sensor.plain"], f"got {out4['soft_deleted']}")

# A record that cannot be written is the one state with no way back, so it must
# be surfaced rather than swallowed.
er.async_get = lambda hass: _Reg(["sensor.x"])


async def _boom(eid):
    raise OSError("disk full")


out5 = asyncio.run(spec.PurgeEngine(_Hass()).async_purge_entities(
    ["sensor.x"], on_left_disabled=_boom))
check("a trash record that could not be written is reported, not swallowed",
      out5.get("untracked") == ["sensor.x"], f"got {out5.get('untracked')}")

# A hard delete that could only disable leaves the entity in the same state a
# soft delete does, so it needs the same record. This was the gap a review
# found: the callback fired on the two soft paths and not on either
# `disabled_only` path, so the claim "one entity, not the batch" did not hold
# for exactly the paths where a crash is least expected.
# The entity has to be an automation: a plain sensor is removed through
# `ent_reg.async_remove` and never reaches `_remove_by_domain`, so it cannot
# produce a `disabled_only` at all. The stub has no owning config entry, which
# is what makes that removal fail and the engine fall back to disabling.
er.async_get = lambda hass: _Reg(["automation.z"])
recorded_z: list[str] = []


async def _record_z(eid):
    recorded_z.append(eid)


out6 = asyncio.run(spec.PurgeEngine(_Hass()).async_purge_entities(
    ["automation.z"], soft_delete=False, on_left_disabled=_record_z))
check("a hard delete that could only disable is recorded in the trash",
      out6["disabled_only"] == ["automation.z"] and recorded_z == ["automation.z"],
      f"disabled_only={out6['disabled_only']} recorded={recorded_z}")
check("and it is not reported as a successful deletion",
      "automation.z" not in out6["success"],
      f"success={out6['success']}")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
