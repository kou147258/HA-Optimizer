"""Behaviour tests for the restore path, against a stubbed Home Assistant.

Run:  python3 tools/test_restore_engine.py

This is the defect a live instance actually had, and the one that matters:

    restore -> {"success": true, "re_enabled": true}
    ... and the automation was still absent from the state machine ...

Nothing was deleted. Four other automations had been sitting in the same
unloaded state for days and the user had read every one of them as gone. They
all came back at once, the moment `automation.reload` was issued by hand.

`async_update_entity(entity_id, disabled_by=None)` edits a registry row. It
does not re-instantiate anything, and Home Assistant only rebuilds an entity
when the config entry that owns it is set up again. So the engine was editing a
dictionary, announcing success, and the trash record was dropped on the word of
a function that had only verified the edit.

The panel was telling the truth throughout. The backend was not.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ha_optimizer"
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ── a Home Assistant small enough to see into ───────────────────────────────
class Disabler:
    USER = "user"
    INTEGRATION = "integration"
    SYSTEM = "system"
    CONFIG_ENTRY = "config_entry"


class RegEntry:
    def __init__(self, entity_id, disabled=False, disabled_by=None, config_entry_id="ce1"):
        self.entity_id = entity_id
        self.disabled = disabled
        self.disabled_by = disabled_by
        self.config_entry_id = config_entry_id


class EntReg:
    def __init__(self):
        self.entries: dict[str, RegEntry] = {}
        self.updates: list[tuple] = []

    def async_get(self, entity_id):
        return self.entries.get(entity_id)

    def async_update_entity(self, entity_id, **kwargs):
        self.updates.append((entity_id, kwargs))
        e = self.entries.get(entity_id)
        if e is None:
            raise KeyError(entity_id)
        for k, v in kwargs.items():
            setattr(e, k, v)
        # Home Assistant derives `disabled` from `disabled_by`: clearing the
        # disabler clears both. A stub that only moved the one field made
        # "did the restore work?" fail for a reason that does not exist in HA.
        if kwargs.get("disabled_by", "sentinel") is None:
            e.disabled = False
        return e


class ConfigEntries:
    def __init__(self):
        self.reloaded: list[str] = []
        self.fail_on: set[str] = set()

    async def async_reload(self, entry_id):
        if entry_id in self.fail_on:
            raise RuntimeError(f"reload of {entry_id} failed")
        self.reloaded.append(entry_id)
        return True


class States:
    def __init__(self):
        self.present: set[str] = set()

    def get(self, entity_id):
        return object() if entity_id in self.present else None


class Hass:
    def __init__(self):
        self.ent_reg = EntReg()
        self.config_entries = ConfigEntries()
        self.states = States()


def make_engine():
    er_mod = types.ModuleType("homeassistant.helpers.entity_registry")
    er_mod.RegistryEntryDisabler = Disabler
    er_mod._hass = None
    er_mod.async_get = lambda hass: hass.ent_reg

    ha = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = Hass
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    helpers.entity_registry = er_mod
    util = types.ModuleType("homeassistant.util")
    util.__path__ = []
    dt = types.ModuleType("homeassistant.util.dt")
    dt.utcnow = lambda: None
    util.dt = dt
    storage = types.ModuleType("homeassistant.helpers.storage")

    class _Store:
        def __init__(self, *a, **k):
            self.data = {}

        async def async_load(self):
            return self.data

        async def async_save(self, d):
            self.data = d

    storage.Store = _Store
    for n, m in [("homeassistant", ha), ("homeassistant.core", core),
                 ("homeassistant.helpers", helpers),
                 ("homeassistant.helpers.entity_registry", er_mod),
                 ("homeassistant.helpers.storage", storage),
                 ("homeassistant.util", util), ("homeassistant.util.dt", dt)]:
        sys.modules[n] = m

    pkg = types.ModuleType("ha_optimizer")
    pkg.__path__ = [str(COMPONENT)]
    sys.modules["ha_optimizer"] = pkg
    from ha_optimizer import purge_engine as pe
    return pe


pe = make_engine()
Engine = pe.PurgeEngine if hasattr(pe, "PurgeEngine") else None
if Engine is None:  # the class may be named differently
    cands = [v for k, v in vars(pe).items() if isinstance(v, type) and "hass" in (getattr(v, "__init__", None).__code__.co_varnames if getattr(v, "__init__", None) else ())]
    Engine = cands[0] if cands else None
assert Engine is not None, "could not find the engine class in purge_engine"

# ═══ 1. re-enabling must actually bring the entity back ═════════════════════
print("\nrestore: the entity has to come back, not just the registry row")
h = Hass()
h.ent_reg.entries["automation.3333"] = RegEntry(
    "automation.3333", disabled=True, disabled_by=Disabler.USER, config_entry_id="ce-auto")
eng = Engine(h)
r = asyncio.run(eng.async_restore_entity("automation.3333"))
check("the disabled_by flag is cleared", not h.ent_reg.entries["automation.3333"].disabled_by)
check("the owning config entry is reloaded",
      "ce-auto" in h.config_entries.reloaded,
      "this is the whole fix: editing the registry row does not re-instantiate "
      "the entity, and without a reload it never appears in the state machine")
check("the result says what actually happened",
      r["success"] is True and r["re_enabled"] is True and r.get("reloaded") is True)

# ═══ 2. an entity that is already enabled can still be unloaded ════════════
# The state the live instance was left in: registry says enabled, entity absent.
h2 = Hass()
h2.ent_reg.entries["sensor.x"] = RegEntry("sensor.x", disabled=False, disabled_by=None, config_entry_id="ce-x")
eng2 = Engine(h2)
r2 = asyncio.run(eng2.async_restore_entity("sensor.x"))
check("an already-enabled entity still triggers a reload of its owner",
      "ce-x" in h2.config_entries.reloaded,
      "this is exactly the state a previous restore left behind; returning "
      "'nothing to do' here is how entities got stranded")
check("and reports that a reload happened rather than claiming nothing to do",
      r2["success"] is True and r2.get("reloaded") is True)

# ═══ 3. an entity with no owning config entry is not an error ══════════════
h3 = Hass()
h3.ent_reg.entries["sensor.yaml"] = RegEntry("sensor.yaml", disabled=True,
                                             disabled_by=Disabler.USER, config_entry_id=None)
eng3 = Engine(h3)
r3 = asyncio.run(eng3.async_restore_entity("sensor.yaml"))
check("a YAML entity restores without a reload and is not called a failure",
      r3["success"] is True and r3["re_enabled"] is True and r3.get("reloaded") is False,
      "there is nothing to reload; inventing a failure would be worse")
check("and it still clears the disabled flag",
      not h3.ent_reg.entries["sensor.yaml"].disabled)

# ═══ 4. failures are still failures ════════════════════════════════════════
h4 = Hass()
eng4 = Engine(h4)
r4 = asyncio.run(eng4.async_restore_entity("sensor.gone"))
check("an entity that is not in the registry fails", r4["success"] is False)

h5 = Hass()
h5.ent_reg.entries["sensor.keep"] = RegEntry("sensor.keep", disabled=True,
                                             disabled_by=Disabler.SYSTEM, config_entry_id="ce-k")
eng5 = Engine(h5)
r5 = asyncio.run(eng5.async_restore_entity("sensor.keep"))
check("a SYSTEM-disabled entity is refused, not silently cleared",
      r5["success"] is False and h5.ent_reg.entries["sensor.keep"].disabled)

h6 = Hass()
h6.ent_reg.entries["sensor.reload"] = RegEntry("sensor.reload", disabled=True,
                                               disabled_by=Disabler.USER, config_entry_id="ce-r")
h6.config_entries.fail_on.add("ce-r")
eng6 = Engine(h6)
r6 = asyncio.run(eng6.async_restore_entity("sensor.reload"))
check("a failed reload does not lose the restore",
      r6["success"] is True and not h6.ent_reg.entries["sensor.reload"].disabled,
      "the registry edit did happen; a reload failure must not discard it")
check("and it is reported rather than claimed as a clean success",
      r6.get("reloaded") is False)

# ═══ 5. the source keeps the reload in the path ════════════════════════════
print("\nrestore: the invariant is written down")
src = (COMPONENT / "purge_engine.py").read_text(encoding="utf-8")
fn = src.split("async def async_restore_entity", 1)[1].split("async def async_hard_delete", 1)[0]
check("the restore path itself reloads, not only the already-enabled branch",
      fn.count("async_reload") >= 1)
check("a missing entity still reports a plain failure",
      "not found in registry" in fn)
init = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
check("a claimed restore is confirmed against the state machine",
      "_entity_is_back" in init and "did not come back" in init,
      "the trash record is dropped on the word of this function, so a false "
      "'success' turns a failed restore into a real deletion")
check("that check runs only when something claimed to be re-enabled",
      'if result.get("re_enabled")' in init)
check("the wait is bounded rather than a bare instant check",
      "asyncio.sleep" in init and "timeout" in init,
      "a restore that worked on a loaded instance must not be failed for lag")

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("all restore-engine checks passed")
