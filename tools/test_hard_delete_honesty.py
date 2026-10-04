"""A hard delete must either remove the thing or say plainly that it did not.

Measured on a live 2026.8.3: an automation created through the REST API the
automation editor itself uses - so unambiguously not YAML - came back from a hard
delete as

    {"yaml_manual": [{"note": "This automation is defined in YAML and must be
                                 removed manually"}]}

and was still there afterwards, enabled. Nothing had been removed, and the
reason offered was one the registry actively contradicts: the same entity's
`is_yaml_entity` was False.

The cause was a guess about the registry dressed as a fact. On 2026.8.3 a UI
automation is not a config entry at all - the editor writes it to
`automations.yaml` - so its registry entry carries no `config_entry_id`, and
"no config entry" was read as "therefore YAML".

These cases are runtime, not text: a UI-shaped registry entry is driven through
the real engine with a fake `automations.yaml` on disk, and what has to come out
is the file edited AND the registry row gone. The stub is the endpoint's own
mechanism - read the file, drop the entry, write it back, then remove the
registry row - because a stub that is simpler than the thing it stands in for
tests the stub.

An earlier version of this used `homeassistant.helpers.automation_config`, which
does not exist; it was replaced after the contents API returned 404 for it, and
that fix is why this file fakes a YAML file rather than a collection.
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


class Entry:
    def __init__(self, unique_id, config_entry_id=None, platform="automation",
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
        self.disabled_by_this: list[str] = []
        self.removed: list[str] = []

    def async_get(self, entity_id):
        return self.entry if (self.entry and entity_id == self.entry.entity_id) else None

    def async_update_entity(self, entity_id, disabled_by=None, **_kw):
        self.entry.disabled = True
        self.disabled_by_this.append(entity_id)

    def async_remove(self, entity_id):
        self.removed.append(entity_id)
        self.entities.pop(entity_id, None)
        if self.entry and self.entry.entity_id == entity_id:
            self.entry = None


class ConfigEntries:
    def __init__(self):
        self.removed: list[str] = []

    async def async_remove(self, entry_id):
        self.removed.append(entry_id)


class FileSystem:
    """The fake automations.yaml, and the loader/writer pair HA uses."""

    def __init__(self, automations_yaml: list | None, scripts_yaml=None):
        self.files = {"automations.yaml": automations_yaml,
                      "scripts.yaml": scripts_yaml}
        self.writes: list[str] = []

    def path(self, p):
        # Returns the name unchanged: `files` is keyed by filename, and a
        # prefix here would silently miss every lookup - the engine would read
        # an empty file, find nothing to remove, and report "could not be
        # removed", which is a real outcome wearing the wrong cause.
        return p

    def load_yaml(self, p):
        return list(self.files.get(p) or [])

    def save_yaml(self, p, data):
        self.files[p] = list(data)
        self.writes.append(p)

    # PLAIN, not async. `Hass.async_add_executor_job` returns whatever this
    # returns, so an `async def` here hands back a coroutine nobody awaits -
    # which is a RuntimeWarning in the noise and a silent no-op that made every
    # removal look like a failure while the test blamed the engine.
    def async_add_executor_job(self, fn, *args):
        return fn(*args)


class Hass:
    def __init__(self, fs, config_entries):
        self.config = fs
        self._fs = fs
        self.config_entries = config_entries

    async def async_add_executor_job(self, fn, *args):
        return self._fs.async_add_executor_job(fn, *args)


def run(entry, yaml_items, *, config_entry_id=None, platform="automation"):
    reg = Registry(entry)
    ce = ConfigEntries()
    fs = FileSystem(yaml_items)
    mod = load(reg, fs)
    engine = mod.PurgeEngine(Hass(fs, ce))
    result = asyncio.run(engine.async_purge_entities(
        [entry.entity_id], soft_delete=False))
    return result, reg, fs, ce


def load(registry, fs):
    """exec purge_engine.py with Home Assistant stubbed.

    Assigned, NOT `setdefault`: each case loads the engine with a DIFFERENT
    registry and file, and `setdefault` would hand back the first case's stubs,
    so the later cases would silently re-test the first one.
    """
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
    cfg_mod = types.ModuleType("homeassistant.config")
    cfg_mod.AUTOMATION_CONFIG_PATH = "automations.yaml"
    cfg_mod.SCRIPT_CONFIG_PATH = "scripts.yaml"
    yaml_mod = types.ModuleType("homeassistant.util.yaml")
    yaml_mod.load_yaml = fs.load_yaml
    yaml_mod.save_yaml = fs.save_yaml

    for name, mod in (("homeassistant", ha), ("homeassistant.helpers", helpers),
                      ("homeassistant.core", core),
                      ("homeassistant.helpers.entity_registry", er_mod),
                      ("homeassistant.util", util),
                      ("homeassistant.util.dt", dt_mod),
                      ("homeassistant.config", cfg_mod),
                      ("homeassistant.util.yaml", yaml_mod)):
        sys.modules[name] = mod

    pkg = types.ModuleType("haopt"); pkg.__path__ = [str(COMP)]
    sys.modules["haopt"] = pkg
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


AID = "ui_made_1234"
CFG_ITEM = {"id": AID, "alias": "ui-made", "triggers": [], "actions": []}

# ── 1. a UI automation IS in automations.yaml: it must actually be removed ──
E = Entry(unique_id=AID, config_entry_id=None, platform="automation")
res, reg, fs, ce = run(E, [dict(CFG_ITEM), {"id": "other", "alias": "keep me"}])
check("a UI automation is REMOVED, not reported as YAML",
      E.entity_id in (res.get("success") or []),
      f"success={res.get('success')} yaml_manual={res.get('yaml_manual')}")
check("its entry is gone from automations.yaml",
      [x.get("id") for x in (fs.files["automations.yaml"] or [])] == ["other"],
      f"file now holds {[x.get('id') for x in (fs.files['automations.yaml'] or [])]}")
check("and the file was actually written, not just edited in memory",
      fs.writes == ["automations.yaml"], f"writes={fs.writes}")
check("the entity registry row is removed too",
      reg.removed == [E.entity_id], f"async_remove called with {reg.removed}")
check("and NOT through a config entry that does not exist",
      ce.removed == [], f"config_entries.async_remove called with {ce.removed}")
check("no YAML is claimed anywhere in the result",
      not (res.get("yaml_manual") or []), f"yaml_manual={res.get('yaml_manual')}")

# ── 2. not in the file at all: disabled and recorded, never claimed as YAML ─
E2 = Entry(unique_id="not_in_file", config_entry_id=None, platform="automation")
res2, reg2, _fs2, _ce2 = run(E2, [{"id": "someone_else", "alias": "x"}])
check("an entity in no editor file is NOT reported as a removal",
      E2.entity_id not in (res2.get("success") or []), f"success={res2.get('success')}")
check("and the file is left untouched",
      [x.get("id") for x in (_fs2.files["automations.yaml"] or [])] == ["someone_else"])
check("it is disabled rather than left running after a hard delete",
      reg2.disabled_by_this == [E2.entity_id], f"disabled={reg2.disabled_by_this}")
check("and nothing is claimed as YAML",
      not any("YAML" in str(x) for x in (res2.get("yaml_manual") or [])),
      f"yaml_manual={res2.get('yaml_manual')}")

# ── 3. platform == 'yaml' is still called YAML, same handling ──────────────
E3 = Entry(unique_id="from_yaml", config_entry_id=None, platform="yaml")
res3, r3, _f3, _c3 = run(E3, [])
rows = res3.get("yaml_manual") or []
check("an entity with platform == 'yaml' IS reported as YAML",
      bool(rows) and "YAML" in str(rows[0].get("note", "")), f"rows={rows}")
check("but the handling is the same for any unremovable entity: disabled",
      E3.entity_id in (res3.get("disabled_only") or []),
      f"disabled_only={res3.get('disabled_only')}")

# ── 4. the file is never written when nothing matched ──────────────────────
E4 = Entry(unique_id="absent", config_entry_id=None, platform="automation")
_fs4 = None
res4, _r4, fs4, _c4 = run(E4, [{"id": "x", "alias": "y"}])
check("a no-match removal does not write the file at all",
      fs4.writes == [], f"writes={fs4.writes}")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
