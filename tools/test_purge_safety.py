"""Regression tests for the three data-loss risks found in HA Optimizer.

These run WITHOUT Home Assistant: the three modules under test are loaded with
stub `homeassistant.*` modules injected into sys.modules, so the purge engine and
the expiry path can be exercised in isolation.

Run:  python3 tools/test_purge_safety.py
Each check prints the behaviour it locks in; the whole file exits non-zero if
any of them stops holding.
"""
from __future__ import annotations

import ast
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
    def __init__(self, device_class=None, config_entry_id="ce_1", platform="integration",
                 disabled=False):
        self.original_device_class = device_class
        self.device_class = device_class
        self.config_entry_id = config_entry_id
        self.platform = platform
        self.disabled = disabled


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

# a normal entity must still be deletable — and "normal" now means DISABLED,
# because a hard delete only applies to something in the trash. The fixture did
# not set it, so it was asserting that a hard delete removes an entity that was
# never disabled: which is what the new guard forbids, and what the expiry job
# used to do to entities a user had re-enabled by hand.
reg = FakeEntReg(FakeRegEntry(device_class="temperature", config_entry_id="ce_2",
                             disabled=True))
engine = purge_engine.PurgeEngine(FakeHass(reg))
asyncio.run(engine.async_purge_entities(["sensor.normal"], soft_delete=False))
check("still deletes an ordinary disabled entity",
      reg.removed == ["sensor.normal"], str(reg.removed))

# and an entity that is enabled again must be left alone entirely
reg2 = FakeEntReg(FakeRegEntry(device_class="temperature", config_entry_id="ce_3",
                               disabled=False))
engine2 = purge_engine.PurgeEngine(FakeHass(reg2))
res2 = asyncio.run(engine2.async_purge_entities(["sensor.back"], soft_delete=False))
check("refuses to delete an entity that is enabled again",
      reg2.removed == [] and res2["not_disabled"] == ["sensor.back"],
      f"removed={reg2.removed} not_disabled={res2.get('not_disabled')}")
check("and it is not reported as a successful deletion either",
      "sensor.back" not in res2["success"], str(res2["success"]))


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
engine_src = (COMPONENT / "purge_engine.py").read_text(encoding="utf-8")


def _fn_nodes(src: str, name: str) -> list:
    """Every function with this name, located by AST rather than by a window.

    It was `split(...)[1][:4000]`, and 4000 characters stopped reaching the end of
    `handle_purge` once the function grew - so the check silently started
    reading half a function and failed for a reason that had nothing to do with
    what it was checking. A window is a time bomb; an AST node is not.
    """
    tree = ast.parse(src)
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]


def _fn_source(src: str, name: str) -> str:
    """The body of one function as raw text - for a FAILURE MESSAGE only.

    `get_source_segment` hands back the lines with their COMMENTS INCLUDED, so a
    substring matched in here can be satisfied by a comment, and this file
    guards an irreversible job: a commented-out guard and a live one are
    indistinguishable to a text match, and the guard is the whole point. It was
    exactly that - commenting out the soft-purge batch record left every check
    below green, because the comment still read `async_add_soft_deleted(
    entity_ids)`. So nothing here asserts on this string any more: the
    assertions read node shapes via _fn_nodes, and this is only ever used to
    show what the code actually was.
    """
    segs = [ast.get_source_segment(src, n) or "" for n in _fn_nodes(src, name)]
    return "\n".join(l.rstrip() for s in segs for l in s.splitlines())


def _dotted(node) -> str:
    """`a.b.c` for a Name/Attribute chain, the bare attr when the base is a
    subscript (`data["engine"].async_purge_entities` -> `async_purge_entities`),
    and '' for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _matches(name: str, callee: str) -> bool:
    """A call to `callee`, however the receiver is spelled."""
    return name == callee or name.endswith("." + callee)


def _calls(node) -> list:
    """Every call inside a node, as (callee name, ast.Call)."""
    return [(_dotted(c.func), c) for c in ast.walk(node) if isinstance(c, ast.Call)]


def _has_call(node, callee: str) -> bool:
    return any(_matches(name, callee) for name, _ in _calls(node))


def _empty() -> ast.Module:
    """A node with nothing in it, so a missing function FAILS the checks below
    instead of raising AttributeError and taking the whole file with it."""
    return ast.Module(body=[], type_ignores=[])


for _name in ("_async_check_soft_delete_expiry", "handle_purge"):
    _found = _fn_nodes(init_src, _name)
    check(f"{_name} is declared exactly once", len(_found) == 1, f"found {len(_found)}")

expiry_nodes = _fn_nodes(init_src, "_async_check_soft_delete_expiry")
expiry = expiry_nodes[0] if len(expiry_nodes) == 1 else _empty()
expiry_src = _fn_source(init_src, "_async_check_soft_delete_expiry")

check("expiry path creates a persistent notification",
      _has_call(expiry, "persistent_notification.async_create"),
      "no live call to it in the function body"
      + (f"; the body starts: {expiry_src[:120]!r}" if expiry_src else ""))
check("expiry path logs at warning level",
      re_warn := _has_call(expiry, "_LOGGER.warning"))
# Assigned AND announced. A comment mentioning the name satisfies neither, and
# so does a name that is only assigned: the function builds `still_tracked` and
# then branches on it TWICE - once to log what could not be removed, once to add
# a line to the notification. Only the first is the announcement, so that is the
# branch this asserts on: a check that accepted either one passed when the
# logging branch was dead and the text-only branch was alive.
_tracked = [n for n in ast.walk(expiry)
            if isinstance(n, (ast.Assign, ast.AnnAssign))
            and any(isinstance(t, ast.Name) and t.id == "still_tracked"
                    for t in ([*n.targets] if isinstance(n, ast.Assign) else [n.target]))]
_announced = [n for n in ast.walk(expiry)
              if isinstance(n, ast.If) and _dotted(n.test) == "still_tracked"
              and any(_matches(name, "_LOGGER.warning") for name, _ in _calls(n))]
check("entities that could not be removed stay tracked",
      bool(_tracked) and bool(_announced),
      "no assignment to still_tracked, or no `if still_tracked:` that logs what "
      "could not be removed (a branch that only adds a line to the notification "
      "body is not an announcement)")

_purge_nodes = _fn_nodes(init_src, "handle_purge")
_purge_node = _purge_nodes[0] if len(_purge_nodes) == 1 else _empty()
_purge_body = _fn_source(init_src, "handle_purge")   # AST-located, not a window
# The invariant is "an entity the engine left disabled is in the trash". It used
# to be satisfied by a batch write here, and it is now satisfied earlier and
# more safely: the engine reports each entity the moment it disables it, and the
# handler persists that. The batch write stayed for a while afterwards, where it
# cost an extra save per purge and rewrote `disabled_at` on every record - so
# asserting the call site here would have pinned a mechanism that is no longer
# the right one, which is how an assertion outlives the design it was written
# for. The invariant is asserted instead, and the mechanism by the engine check.
_untracked = [c for name, c in _calls(_purge_node)
              if name == "result.get" and c.args
              and isinstance(c.args[0], ast.Constant) and c.args[0].value == "untracked"]
_recorded = [k.value for c in ast.walk(_purge_node) if isinstance(c, ast.Call)
             for k in c.keywords
             if k.arg == "on_left_disabled" and isinstance(k.value, ast.Name)
             and k.value.id == "_record"]
check("purge service keeps disabled_only entities in the trash",
      bool(_untracked) and bool(_recorded),
      "the handler must record each entity as the engine disables it, and say "
      "so when a record could not be written"
      + (f"; the body starts: {_purge_body[:120]!r}" if _purge_body else ""))

# The engine's own side of that. This was a whole-file count of the literal text
# "_record_if_callbacked(", which a comment naming the helper inflates and a
# removed call site deflates - so it counted text, not trash records. Count the
# CALL NODES instead.
#
# And not a floor on them either. "At least two call sites" was true with three
# paths in the engine, and commenting one out left two - still passing, while
# the branch it belonged to disabled an entity and recorded nothing. That is
# precisely the state the check exists to prevent, reachable by deleting one
# line. So the invariant is structural: EVERY branch that puts an entity into
# `disabled_only` must also record it, one for one.
_engine_defs = _fn_nodes(engine_src, "_record_if_callbacked")
_sites = sorted(c.lineno for c in ast.walk(ast.parse(engine_src))
                if isinstance(c, ast.Call) and _dotted(c.func) == "_record_if_callbacked")


def _stmt_lists(tree: ast.AST):
    for node in ast.walk(tree):
        for f in ("body", "orelse", "finalbody"):
            v = getattr(node, f, None)
            if isinstance(v, list) and v and all(isinstance(s, ast.stmt) for s in v):
                yield v


def _appends_disabled(stmt: ast.AST) -> bool:
    return (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Attribute)
            and stmt.value.func.attr == "append"
            and "disabled_only" in ast.unparse(stmt.value.func.value))


def _records(stmt: ast.AST) -> bool:
    return (isinstance(stmt, ast.AST)
            and any(isinstance(n, ast.Call) and _dotted(n.func) == "_record_if_callbacked"
                    for n in ast.walk(stmt)))


def _blocks_recording(tree: ast.AST) -> tuple[list[int], list[int]]:
    """(branches that disable, of those the ones that also record).

    Per BRANCH, not per block: an append and the record call that belongs to it
    have to be in the same statement list, and the record call has to come after
    the append. Counting a whole enclosing `for` body as one unit would let any
    one of three branches lose its call and still pass, which is the exact
    defect - a disabled entity with no trash record.
    """
    disables: list[int] = []
    records: list[int] = []
    for stmts in _stmt_lists(tree):
        for i, stmt in enumerate(stmts):
            if not _appends_disabled(stmt):
                continue
            disables.append(stmt.lineno)
            if any(_records(s) for s in stmts[i + 1:]):
                records.append(stmt.lineno)
    return disables, records


_disables, _records_lines = _blocks_recording(ast.parse(engine_src))
check("every branch that leaves an entity disabled also records it",
      sorted(_disables) == sorted(_records_lines),
      f"disables at lines {_disables} but only {_records_lines} of them record; "
      f"a disabled entity with no trash record is the one state nothing here can "
      f"undo, and deleting one _record_if_callbacked call reaches it")
check("the engine records every path that leaves an entity disabled",
      len(_engine_defs) == 1 and len(_sites) == len(_disables),
      f"defined {len(_engine_defs)}x, called from {len(_sites)} site(s) at lines "
      f"{_sites}, for {len(_disables)} disabling branch(es); a hard delete that "
      f"could only disable disables just as a soft delete does, and needs the "
      f"same trash record")

# The soft path records the whole batch BEFORE the engine disables anything.
# The order is the invariant, not the mechanism: a crash between the two must
# leave the trash over-describing an entity that is still enabled (visible,
# harmless, ages out) rather than an entity that is disabled with no record
# (invisible, unrestorable). The per-entity version was also correct, and cost
# one awaited disk write per entity, because Store.async_save does not coalesce
# - only async_delay_save does.
_soft_if = next((n for n in _purge_node.body
                 if isinstance(n, ast.If) and _dotted(n.test) == "soft"), None)
_soft_stmts = list(_soft_if.body) if _soft_if is not None else []


def _first_call(stmts: list, callee: str) -> tuple:
    """(index of the first statement calling it, that call), (-1, None)."""
    for i, st in enumerate(stmts):
        for name, c in _calls(st):
            if _matches(name, callee):
                return i, c
    return -1, None


_i_rec, _rec_call = _first_call(_soft_stmts, "async_add_soft_deleted")
_i_purge, _purge_call = _first_call(_soft_stmts, "async_purge_entities")
check("a soft purge records the batch before it disables anything",
      _i_rec >= 0 and _i_purge >= 0 and _i_rec < _i_purge
      and any(isinstance(a, ast.Name) and a.id == "entity_ids"
              for a in (_rec_call.args if _rec_call is not None else [])),
      f"record at statement {_i_rec}, purge at {_i_purge}"
      + (f" (as {_dotted(_purge_call.func)})" if _purge_call is not None else "")
      + "; the record must be written first - see the comment in handle_purge")
_i_out, _out_call = _first_call(_soft_stmts, "async_remove_soft_deleted")
check("and the entities the engine refused are taken back out",
      _i_out >= 0 and any(isinstance(n, ast.Name) and n.id == "refused"
                          for a in (_out_call.args if _out_call is not None else [])
                          for n in ast.walk(a)),
      "a safety device class or a YAML entity was recorded but never disabled, "
      "so its record has to come back out")

# `from __future__ import annotations` turns annotations into strings, and the
# docstring explaining the fix still names the dead keys — so inspect the AST,
# which sees only real code.
pe_tree = ast.parse(engine_src)
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
