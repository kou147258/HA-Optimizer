"""A hard delete must either remove the thing or say plainly that it did not.

Measured on a live 2026.8.3: an automation created through the REST API the
automation editor itself uses - so unambiguously not YAML - came back from a hard
delete as

    {"yaml_manual": [{"note": "This automation is defined in YAML and must be
                                 removed manually"}]}

and was still there afterwards, enabled. Nothing had been removed, and the
reason offered was one the registry actively contradicts: the same entity's
`is_yaml_entity` was False and its `platform` was None, where a YAML automation
has `platform == "yaml"`.

The cause was a guess about the registry dressed as a fact. The registry entry
for a UI automation carries no `config_entry_id` on this version, and "no config
entry" was read as "therefore YAML". The scanner in this same project asks the
same question a different way, and the two answers disagreed.

So the cases here are runtime, not text: a UI-shaped registry entry is driven
through the real engine, and what has to come out is a removal - or a failure
that says which. A note that says "YAML" is only allowed when the registry
actually says `platform == "yaml"`.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
COMP = ROOT / "custom_components" / "ha_optimizer"
ENGINE = COMP / "purge_engine.py"

results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


# ── the shape a UI automation's registry entry has on 2026.8 ────────────────
class Entry:
    def __init__(self, unique_id, config_entry_id=None, platform=None,
                 disabled=False):
        self.entity_id = "automation.ui_made"
        self.domain = "automation"
        self.unique_id = unique_id
        self.config_entry_id = config_entry_id
        self.platform = platform
        self.disabled = disabled
        self.original_device_class = None
        self.device_class = None
        self.name = "ui-made"


class Registry:
    def __init__(self, entry):
        self.entry = entry
        self.entities = {entry.entity_id: entry} if entry else {}
        self.disabled_by_this = []

    def async_get(self, entity_id):
        return self.entry if (self.entry and entity_id == self.entry.entity_id) else None

    def async_update_entity(self, entity_id, disabled_by=None, **_kw):
        self.entry.disabled = True
        self.disabled_by_this.append(entity_id)


class Collection:
    def __init__(self, items):
        self.items = dict(items)
        self.deleted: list[str] = []

    async def async_get(self, item_id):
        return self.items.get(item_id)

    async def async_delete_item(self, item_id):
        self.deleted.append(item_id)
        self.items.pop(item_id, None)


class ConfigEntries:
    def __init__(self):
        self.removed: list[str] = []

    async def async_remove(self, entry_id):
        self.removed.append(entry_id)


def load(registry, collection, config_entries):
    """exec purge_engine.py with Home Assistant stubbed to the shape above."""
    ha = types.ModuleType("homeassistant"); ha.__path__ = []
    helpers = types.ModuleType("homeassistant.helpers"); helpers.__path__ = []
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    er_mod = types.ModuleType("homeassistant.helpers.entity_registry")
    er_mod.async_get = lambda hass: registry
    er_mod.RegistryEntryDisabler = type("D", (), {"USER": "user", "INTEGRATION": "integration"})
    util = types.ModuleType("homeassistant.util"); util.__path__ = []
    dt_mod = types.ModuleType("homeassistant.util.dt")
    import datetime as _dt
    dt_mod.utcnow = lambda: _dt.datetime.now(_dt.timezone.utc)
    dt_mod.as_local = lambda d: d
    util.dt = dt_mod
    ac_mod = types.ModuleType("homeassistant.helpers.automation_config")
    ac_mod.async_get_collection = lambda hass: collection

    # Assigned, NOT `setdefault`. Each case loads the engine with a DIFFERENT
    # registry and collection, and `setdefault` would hand back the first
    # case's stubs - so cases 2 and 3 would silently be re-testing case 1. That
    # is the whole "a stub that is stale is worse than no stub" trap, reached
    # from the other direction.
    for name, mod in (("homeassistant", ha), ("homeassistant.helpers", helpers),
                      ("homeassistant.core", core),
                      ("homeassistant.helpers.entity_registry", er_mod),
                      ("homeassistant.util", util),
                      ("homeassistant.util.dt", dt_mod),
                      ("homeassistant.helpers.automation_config", ac_mod)):
        sys.modules[name] = mod

    pkg = types.ModuleType("haopt"); pkg.__path__ = [str(COMP)]
    sys.modules["haopt"] = pkg
    # The real const, not a hand-written one: a stub missing a name the engine
    # imports is an ImportError that reads like a defect in the engine.
    const = types.ModuleType("haopt.const")
    const.__file__ = str(COMP / "const.py")
    exec(compile((COMP / "const.py").read_text(encoding="utf-8"),
                 str(COMP / "const.py"), "exec"), const.__dict__)   # noqa: S102
    sys.modules["haopt.const"] = const

    spec = types.ModuleType("haopt.purge_engine")
    spec.__file__ = str(ENGINE)
    exec(compile(ENGINE.read_text(encoding="utf-8"), str(ENGINE), "exec"),
         spec.__dict__)                                   # noqa: S102
    return spec


class Hass:
    def __init__(self, config_entries):
        self.config_entries = config_entries


def run(entry, collection_items, config_entry_id=None, platform=None):
    reg = Registry(entry)
    col = Collection(collection_items)
    ce = ConfigEntries()
    mod = load(reg, col, ce)
    engine = mod.PurgeEngine(Hass(ce))
    result = asyncio.run(engine.async_purge_entities(
        [entry.entity_id], soft_delete=False))
    return result, reg, col, ce


# ── 1. a UI automation IS in the UI config collection: it must be removed ──
E = Entry(unique_id="ui_made_1234", config_entry_id=None, platform=None)
res, reg, col, ce = run(E, {"ui_made_1234": {"alias": "ui-made"}})
check("a UI automation is REMOVED, not reported as YAML",
      E.entity_id in (res.get("success") or []),
      f"success={res.get('success')} yaml_manual={res.get('yaml_manual')}")
check("the removal went through the UI config collection",
      col.deleted == ["ui_made_1234"], f"deleted={col.deleted}")
check("and NOT through a config entry that does not exist",
      ce.removed == [], f"config_entries.async_remove called with {ce.removed}")
check("no YAML is claimed anywhere in the result",
      not (res.get("yaml_manual") or []),
      f"yaml_manual={res.get('yaml_manual')}")
check("the failure list is empty - it succeeded",
      not (res.get("failed") or []), f"failed={res.get('failed')}")

# ── 2. the entity is NOT in the collection: a failure, not a reason ────────
E2 = Entry(unique_id="ui_made_5678", config_entry_id=None, platform=None)
res2, reg2, col2, ce2 = run(E2, {})            # empty collection
check("an entity in no collection at all is a FAILURE, not a removal",
      E2.entity_id not in (res2.get("success") or []),
      f"success={res2.get('success')}")
check("and it is disabled rather than left running after a hard delete",
      reg2.disabled_by_this == [E2.entity_id], f"disabled={reg2.disabled_by_this}")
check("and the failure names what actually happened, not YAML",
      not any("YAML" in str(f) for f in (res2.get("failed") or [])),
      f"failed={res2.get('failed')}")

# ── 3. an entity the registry calls YAML: same handling, honest wording ────
E3 = Entry(unique_id="from_yaml", config_entry_id=None, platform="yaml")
res3, r3, _c3, _ce3 = run(E3, {})
rows = res3.get("yaml_manual") or []
check("an entity with platform == 'yaml' IS reported as YAML",
      bool(rows) and "YAML" in str(rows[0].get("note", "")), f"rows={rows}")
check("but the handling is the same as any other unremovable entity: disabled",
      E3.entity_id in (res3.get("disabled_only") or []),
      f"disabled_only={res3.get('disabled_only')}")
check("one rule, not two - a YAML guess must not change what happens to it",
      E3.entity_id in (r3.disabled_by_this), f"disabled={r3.disabled_by_this}")

# ── 4. the claim is conditional in the source, not unconditional ───────────
src = ENGINE.read_text(encoding="utf-8")
check("the YAML note is gated on what the registry says",
      'is_yaml = getattr(entry, "platform", None) == "yaml"' in src,
      "the note is emitted unconditionally")
check("and 'no owning config entry' is a distinct outcome, not 'not_found'",
      '"no_owner"' in src,
      "not_found is the already-gone case and must stay separate")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
