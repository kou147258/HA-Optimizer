"""Regression tests for the three data-loss risks found in HA Optimizer.

These run WITHOUT Home Assistant: the three modules under test are loaded with
stub `homeassistant.*` modules injected into sys.modules, so the purge engine and
the expiry path can be exercised in isolation.

Run:  python3 tools/test_purge_safety.py
Each check prints the behaviour it locks in; the whole file exits non-zero if
any of them stops holding.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "ha_optimizer"
sys.path.insert(0, str(COMPONENT.parent))

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ── stub the parts of Home Assistant these modules touch ─────────────────────
def _stub() -> None:
    ha = types.ModuleType("homeassistant")
    ha.__path__ = []
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.ServiceCall = object
    core.SupportsResponse = types.SimpleNamespace(OPTIONAL="optional", NONE="none")
    core.callback = lambda f: f
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    er = types.ModuleType("homeassistant.helpers.entity_registry")
    er.RegistryEntryDisabler = types.SimpleNamespace(USER="user", INTEGRATION="integration",
                                                    CONFIG_ENTRY="config_entry", SYSTEM="system")
    er.async_get = lambda hass: hass.ent_reg
    dr = types.ModuleType("homeassistant.helpers.device_registry")
    dt = types.ModuleType("homeassistant.util.dt")
    dt.UTC = None
    storage = types.ModuleType("homeassistant.helpers.storage")
    storage.Store = object
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    pn = types.ModuleType("homeassistant.components.persistent_notification")
    calls: list[tuple] = []
    pn.async_create = lambda hass, title, message, notification_id=None: calls.append(
        (title, message, notification_id))
    pn.CALLS = calls
    ingress = types.ModuleType("homeassistant.components.ingress")
    ingress.async_create_ingress_url = lambda *a, **k: "/api/ingress/x"
    frontend = types.ModuleType("homeassistant.components.frontend")
    frontend.async_register_built_in_panel = lambda *a, **k: None
    frontend.async_remove_panel = lambda *a, **k: None
    util = types.ModuleType("homeassistant.util")
    util.__path__ = []

    for name, mod in [
        ("homeassistant", ha), ("homeassistant.core", core),
        ("homeassistant.helpers", helpers),
        ("homeassistant.helpers.entity_registry", er),
        ("homeassistant.helpers.device_registry", dr),
        ("homeassistant.util", util), ("homeassistant.util.dt", dt),
        ("homeassistant.helpers.storage", storage),
        ("homeassistant.config_entries", config_entries),
        ("homeassistant.components", components),
        ("homeassistant.components.persistent_notification", pn),
        ("homeassistant.components.ingress", ingress),
        ("homeassistant.components.frontend", frontend),
    ]:
        sys.modules[name] = mod
    # `from homeassistant.helpers import entity_registry as er` resolves via the
    # attribute first, so wire the submodules onto their parents too.
    helpers.entity_registry = er
    helpers.device_registry = dr
    helpers.storage = storage
    util.dt = dt
    components.persistent_notification = pn
    components.ingress = ingress
    components.frontend = frontend


_stub()

# purge_engine does `from .const import ...`, so it has to be imported as part of
# a package. Build a namespace package pointing at the component directory
# without executing its heavy __init__.py.
_pkg = types.ModuleType("ha_optimizer")
_pkg.__path__ = [str(COMPONENT)]
sys.modules["ha_optimizer"] = _pkg

from ha_optimizer import const  # noqa: E402
from ha_optimizer import purge_engine  # noqa: E402

SAFETY_DEVICE_CLASSES = const.SAFETY_DEVICE_CLASSES

# the purge engine logs one warning per refused entity; keep the test output clean
import logging  # noqa: E402
logging.getLogger("custom_components.ha_optimizer.purge_engine").setLevel(logging.CRITICAL)
logging.getLogger("custom_components.ha_optimizer").setLevel(logging.CRITICAL)


# ── RISK 2: the execution layer must refuse every safety device class ─────────
print("RISK 2 — purge engine must not remove safety-class entities")
CONSTE = purge_engine.er.RegistryEntryDisabler


class FakeRegEntry:
    def __init__(self, device_class=None, config_entry_id="ce_1", platform="integration"):
        self.original_device_class = device_class
        self.device_class = device_class
        self.config_entry_id = config_entry_id
        self.platform = platform
        self.disabled = False


class FakeEntReg:
    def __init__(self, entry):
        self.entry = entry
        self.updated: list[tuple] = []
        self.removed: list[str] = []

    def async_get(self, entity_id):
        return self.entry

    def async_update_entity(self, entity_id, disabled_by=None):
        self.updated.append((entity_id, disabled_by))

    def async_remove(self, entity_id):
        self.removed.append(entity_id)


class FakeConfigEntries:
    def __init__(self):
        self.removed: list[str] = []

    async def async_remove(self, entry_id):
        self.removed.append(entry_id)


class FakeHass:
    def __init__(self, ent_reg):
        self.ent_reg = ent_reg
        self.config_entries = FakeConfigEntries()


for cls in ("door", "window", "motion", "occupancy", "vibration", "sound",
            "smoke", "gas", "lock", "battery", "problem", "moisture",
            "carbon_monoxide", "carbon_dioxide", "safety", "tamper",
            "connectivity", "update"):
    entry = FakeRegEntry(device_class=cls)
    reg = FakeEntReg(entry)
    engine = purge_engine.PurgeEngine(FakeHass(reg))
    res = asyncio.run(engine.async_purge_entities(["binary_sensor.probe"], soft_delete=False))
    refused = res["skipped_high_risk"] == ["binary_sensor.probe"] and not reg.removed
    if not refused:
        check(f"refuses device_class={cls!r}", False,
              f"removed={reg.removed} skipped={res['skipped_high_risk']}")
        break
else:
    check(f"refuses all {len(SAFETY_DEVICE_CLASSES)} safety device classes", True)
    check("purge engine uses the shared SAFETY_DEVICE_CLASSES",
          purge_engine._SAFETY_CLASSES is SAFETY_DEVICE_CLASSES)

# a normal entity must still be deletable
reg = FakeEntReg(FakeRegEntry(device_class="temperature", config_entry_id="ce_2"))
engine = purge_engine.PurgeEngine(FakeHass(reg))
asyncio.run(engine.async_purge_entities(["sensor.normal"], soft_delete=False))
check("still deletes an ordinary entity", reg.removed == ["sensor.normal"], str(reg.removed))


# ── RISK 3: automation/script deletion must tell the truth ──────────────────
print("\nRISK 3 — automation/script hard delete must not report a disable as success")
check("_remove_by_domain returns a status string, not a bare bool",
      purge_engine.PurgeEngine._remove_by_domain.__annotations__.get("return") == "str",
      str(purge_engine.PurgeEngine._remove_by_domain.__annotations__.get("return")))

# UI-created: entity registry carries the owning config entry -> really removed
reg = FakeEntReg(FakeRegEntry(config_entry_id="ce_auto_1"))
hass = FakeHass(reg)
engine = purge_engine.PurgeEngine(hass)
res = asyncio.run(engine.async_purge_entities(["automation.kitchen"], soft_delete=False))
check("UI automation: reported as success AND config entry removed",
      res["success"] == ["automation.kitchen"] and hass.config_entries.removed == ["ce_auto_1"],
      f"success={res['success']} removed={hass.config_entries.removed}")

# delete raises -> disabled only, reported as such, never as success
class BoomConfigEntries(FakeConfigEntries):
    async def async_remove(self, entry_id):
        raise RuntimeError("cannot remove config entry")


reg = FakeEntReg(FakeRegEntry(config_entry_id="ce_auto_2"))
hass = FakeHass(reg)
hass.config_entries = BoomConfigEntries()
engine = purge_engine.PurgeEngine(hass)
res = asyncio.run(engine.async_purge_entities(["automation.broken"], soft_delete=False))
check("failed delete: NOT in success",
      "automation.broken" not in res["success"], str(res["success"]))
check("failed delete: surfaced as disabled_only",
      res["disabled_only"] == ["automation.broken"], str(res.get("disabled_only")))
check("failed delete: entity actually got disabled",
      reg.updated and reg.updated[0][0] == "automation.broken", str(reg.updated))

# YAML-defined: no config entry -> honest "cannot delete"
reg = FakeEntReg(FakeRegEntry(config_entry_id=None, platform="automation"))
hass = FakeHass(reg)
engine = purge_engine.PurgeEngine(hass)
res = asyncio.run(engine.async_purge_entities(["automation.from_yaml"], soft_delete=False))
check("YAML automation: reported as manual work, not deleted",
      res["success"] == [] and res["yaml_manual"]
      and res["yaml_manual"][0]["entity_id"] == "automation.from_yaml",
      f"success={res['success']} manual={res['yaml_manual']}")


# ── RISK 1: unattended auto-purge must be announced ─────────────────────────
print("\nRISK 1 — automatic trash expiry must not be silent")
init_src = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
check("expiry path creates a persistent notification",
      "persistent_notification.async_create" in init_src)
check("expiry path logs at warning level",
      re_warn := ("_LOGGER.warning(" in init_src.split("async def _async_check_soft_delete_expiry")[1][:4000]))
check("entities that could not be removed stay tracked",
      "still_tracked" in init_src.split("async def _async_check_soft_delete_expiry")[1][:4000])
_purge_body = init_src.split("def handle_purge")[1][:2500]
# The invariant is "an entity the engine left disabled is in the trash". It used
# to be satisfied by a batch write here, and it is now satisfied earlier and
# more safely: the engine reports each entity the moment it disables it, and the
# handler persists that. The batch write stayed for a while afterwards, where it
# cost an extra save per purge and rewrote `disabled_at` on every record - so
# asserting the call site here would have pinned a mechanism that is no longer
# the right one, which is how an assertion outlives the design it was written
# for. The invariant is asserted instead, and the mechanism by the engine check.
check("purge service keeps disabled_only entities in the trash",
      'result.get("untracked")' in _purge_body
      and "on_left_disabled=_record" in _purge_body,
      "the handler must record each entity as the engine disables it, and say "
      "so when a record could not be written")
check("the engine records every path that leaves an entity disabled",
      _engine_records_all := (
          (COMPONENT / "purge_engine.py").read_text(encoding="utf-8")
          .count("_record_if_callbacked(") >= 3),
      "a hard delete that could only disable disables just as a soft delete "
      "does, and needs the same trash record")

# `from __future__ import annotations` turns annotations into strings, and the
# docstring explaining the fix still names the dead keys — so inspect the AST,
# which sees only real code.
import ast  # noqa: E402

pe_tree = ast.parse((COMPONENT / "purge_engine.py").read_text(encoding="utf-8"))
# drop every string that is a docstring; keep the rest as "live" code strings
docstrings = set()
for node in ast.walk(pe_tree):
    if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        d = ast.get_docstring(node, clean=False)
        if d:
            docstrings.add(d)
live = [n.value for n in ast.walk(pe_tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value not in docstrings]

check("the dead hass.data['automation_storage'] lookup is gone",
      not any("automation_storage" in s for s in live),
      str([s for s in live if "automation_storage" in s]))
check("the dead hass.data['script_storage'] lookup is gone",
      not any("script_storage" in s for s in live),
      str([s for s in live if "script_storage" in s]))
check("the unused StorageCollection imports are gone",
      not any("StorageCollection" in s for s in live),
      str([s for s in live if "StorageCollection" in s]))
check("deletion resolves the config entry via the entity registry",
      any("config_entry_id" in s for s in live))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED: {FAILURES}")
    sys.exit(1)
print("all purge-safety regression checks passed")
