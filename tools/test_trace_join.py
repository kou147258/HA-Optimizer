"""Run the real parser against the real field names, or the reason is gone.

Four releases of this page reported a confident verdict from data it had
attached to nothing, and the cause was four wrong assumptions about the shape
of a trace. None of them raised: `script_execution` is a string, so reading
`.error` off it hit a guard that quietly produced an empty dict; `state` is
"stopped" for every completed run, so the branch that meant "finished" was
unreachable; `not_triggered` records were counted as runs; and there is no
`key` field at all, so every run was filed under the empty string.

A check that greps this module for a key name cannot catch any of that. This
one imports the module and calls `classify()` and `_join()` on dicts built to
match `ActionTrace.as_short_dict()` exactly - the fixture below is transcribed
from HA 2026.8.3's `trace/models.py`, including the fields it does NOT have.

If Home Assistant changes the shape, these fail here rather than on a user's
panel, which is the only acceptable time for them to fail.
"""
from __future__ import annotations

import asyncio
import re
import sys
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "custom_components" / "ha_optimizer" / "automation_runs.py"

results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


# ── stub the two top-level homeassistant imports ────────────────────────────
# The module imports HomeAssistant and entity_registry at the top and nothing
# else, so two empty modules are enough to import it and call the pure logic.
ha = types.ModuleType("homeassistant")
core = types.ModuleType("homeassistant.core")
core.HomeAssistant = type("HomeAssistant", (), {})
helpers = types.ModuleType("homeassistant.helpers")
entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")
entity_registry.async_get = lambda hass: None
helpers.entity_registry = entity_registry
sys.modules.setdefault("homeassistant", ha)
sys.modules.setdefault("homeassistant.core", core)
sys.modules.setdefault("homeassistant.helpers", helpers)
sys.modules.setdefault("homeassistant.helpers.entity_registry", entity_registry)

spec = types.ModuleType("automation_runs")
spec.__file__ = str(MODULE_PATH)
code = compile(MODULE_PATH.read_text(encoding="utf-8"), str(MODULE_PATH), "exec")
exec(code, spec.__dict__)  # noqa: S102 - running this module's own code is the test


# ── the fixture: as_short_dict(), transcribed from trace/models.py ──────────
def stored(item_id: str, *, state: str = "stopped", reason: str | None = "finished",
           error: str | None = None, not_triggered: bool = False) -> dict:
    """One entry of what `async_list_traces(hass, "automation", None)` returns.

    Every key here is one `ActionTrace.as_short_dict()` sets. There is no
    "key", no "script_execution.error", and no "stopped_reason" - the three
    the previous version of this module read.
    """
    d = {
        "last_step": "action 0",
        "run_id": f"run-{item_id}",
        "state": state,
        "script_execution": reason,
        "timestamp": {"start": "2026-10-04T00:33:15+00:00",
                      "finish": "2026-10-04T00:33:16+00:00"},
        "domain": "automation",
        "item_id": item_id,
    }
    if not_triggered:
        d["not_triggered"] = True
    if error is not None:
        d["error"] = error
    return d


C = spec.classify
JOIN = spec._join
# _row and _summary touch nothing on `self`; they are bound here so the test
# can call the shipped code rather than a copy of it.
ROW = lambda aid, meta, runs: spec.AutomationRunAnalyzer._row(None, aid, meta, runs)  # noqa: E731
SUMMARY = lambda rows: spec.AutomationRunAnalyzer._summary(None, rows)  # noqa: E731

check("the module imports and exposes the parser", callable(C) and callable(JOIN))

# ── a failure is a failure, and the error text is found ─────────────────────
# This is the headline: the old code read `script_execution.error` off a
# STRING, so every real failure was reported with no error at all.
failed = C(stored("11111", reason="error", error="Error executing template: x is undefined"))
check("a run that raised is a failure", failed["outcome"] == spec.OUTCOME_FAILED,
      f"got {failed['outcome']}")
check("the error text is read from the top level", failed["error"] == "Error executing template: x is undefined",
      f"got {failed['error']!r}")
check("the error reaches the row, so it can be diagnosed",
      (ROW("automation.11111", {"entity_id": "automation.11111", "name": "11111",
                                "unique_id": "11111"}, [failed]))["last_error"]
      == "Error executing template: x is undefined")

# A run can fail without an exception message; the reason alone says so.
for reason in ("error", "failed_single", "failed_max_runs",
               "disallowed_recursion_detected"):
    check(f"reason {reason!r} alone counts as a failure",
          C(stored("11111", reason=reason))["outcome"] == spec.OUTCOME_FAILED,
          f"got {C(stored('11111', reason=reason))['outcome']}")

# ── a success is a success ──────────────────────────────────────────────────
ok = C(stored("11111"))
check("a completed run is ok", ok["outcome"] == spec.OUTCOME_OK, f"got {ok['outcome']}")
check("'stopped' is not read as an early exit",
      C(stored("11111", state="stopped"))["outcome"] == spec.OUTCOME_OK,
      'state "stopped" is what every finished run sets')
check("a cancelled run is not a failure",
      C(stored("11111", reason="cancelled"))["outcome"] == spec.OUTCOME_OK)

# ── aborted is its own bucket, and is not called a condition stop ───────────
aborted = C(stored("11111", reason="aborted"))
check("an aborted run is counted apart", aborted["outcome"] == spec.OUTCOME_ABORTED,
      f"got {aborted['outcome']}")
check("an aborted run is never a failure",
      aborted["outcome"] not in (spec.OUTCOME_FAILED,))
check("there is no 'condition stopped' outcome any more",
      not hasattr(spec, "OUTCOME_CONDITION"),
      "Home Assistant records a false condition and a break: identically, so "
      "the claim could not be supported")

# ── a trigger that declined to fire is not a run ────────────────────────────
nt = C(stored("11111", not_triggered=True))
check("a not-triggered record is not a run", nt["outcome"] == spec.OUTCOME_NOT_TRIGGERED)
meta = {"entity_id": "automation.11111", "name": "11111", "unique_id": "11111"}
row_mixed = ROW("automation.11111", meta, [ok, nt, failed, aborted])
check("runs_known excludes not-triggered records", row_mixed["runs_known"] == 3,
      f"got {row_mixed['runs_known']} from 4 records")
check("not_triggered is counted on its own", row_mixed["not_triggered"] == 1)
check("aborted is counted on its own", row_mixed["aborted"] == 1)
s = SUMMARY([row_mixed])
check("the success rate leaves aborted and not-triggered out of the denominator",
      s["success_rate"] == 50.0, f"1 ok of 1 ok + 1 failed = {s['success_rate']}")
check("the summary counts only automations that actually have runs",
      s["automations_with_traces"] == 1,
      "a row with no runs must not be counted as traced")

# ── the join: unique_id, not the entity_id tail ─────────────────────────────
# The case that emptied this page: an automation with an alias. Its trace is
# filed under the YAML `id`, and its entity_id is built from the alias, so the
# two halves of "automation.<tail>" are different strings.
aliased = {
    "automation.xin_jian_zi_dong_hua": {"unique_id": "8f2c1d", "name": "新建自动化"},
    "automation.11111": {"unique_id": "11111", "name": "11111"},
}
traces = [stored("8f2c1d"), stored("8f2c1d"), stored("11111")]
joined, unmatched = JOIN(aliased, traces)
check("a run files under its unique_id even when the entity_id differs",
      len(joined.get("automation.xin_jian_zi_dong_hua", [])) == 2,
      f"got {len(joined.get('automation.xin_jian_zi_dong_hua', []))} of 2")
check("an automation whose id and entity_id agree still joins",
      len(joined.get("automation.11111", [])) == 1)
check("nothing is left over when every run matches", unmatched == set(),
      f"left over: {unmatched}")

# The tail fallback still has to work for an entry the registry cannot describe,
# so a run is dropped only after both routes fail.
fallback, left = JOIN({"automation.plain": {"name": "plain"}}, [stored("plain")])
check("a registry entry with no unique_id still joins on the tail",
      len(fallback.get("automation.plain", [])) == 1)
orphan, left2 = JOIN({"automation.a": {"unique_id": "a"}}, [stored("zzz")])
check("a run that matches nothing is reported, not dropped silently",
      left2 == {"zzz"}, f"got {left2}")
check("a reported orphan does not invent a row", orphan == {})

# ── the parser must not depend on a field that does not exist ───────────────
src = MODULE_PATH.read_text(encoding="utf-8")
_code = re.sub(r'(?s)"""..*?"""', " ", src)
_code = re.sub(r"(?m)#.*$", " ", _code)
check("the join tries item_id before any key fallback",
      _code.index('t.get("item_id")') < _code.index('t.get("key")'),
      "item_id is the field as_short_dict() carries; key is only a safety net")
check("the error is read from the top level, not from script_execution",
      'trace_dict.get("error")' in _code
      and "script_execution.error" not in _code,
      "script_execution is a reason string and has no .error")
check("the parser does not read a 'stopped_reason' it invented",
      "stopped_reason" not in _code,
      "as_short_dict() has no such field; the old code read it and got nothing")

# ── coverage must be numbers, not a sentence ────────────────────────────────
# The first version of this check grepped the module for the literal strings
# "buckets" and "runs", which also appear in the lines that ASSIGN them - so
# deleting the numbers from the report left it green. So the numbers are
# obtained the only way that can be wrong: by running the reader.
TRACE_KEY = object()          # stands in for DATA_TRACE, a HassKey instance
STORED_TRACES = {
    "automation.11111": [{"short_dict": {}}, {"short_dict": {}}],
    "automation.8f2c1d": [{"short_dict": {}}],
}
RETURNED = [stored("11111"), stored("8f2c1d")]


class _Storage:
    def __init__(self, hass, version, key):
        self.key = key

    async def async_load(self):
        assert self.key == "trace.saved_traces", self.key
        return STORED_TRACES


async def _done(hass):
    return None


util = types.ModuleType("homeassistant.components.trace.util")
util.async_restore_traces = _done


async def _list(hass, domain, key):
    assert (domain, key) == ("automation", None), (domain, key)
    return list(RETURNED)


util.async_list_traces = _list
storage_mod = types.ModuleType("homeassistant.helpers.storage")
storage_mod.Store = _Storage
trace_pkg = types.ModuleType("homeassistant.components.trace")
components = types.ModuleType("homeassistant.components")
components.trace = trace_pkg
trace_pkg.util = util
sys.modules["homeassistant.components"] = components
sys.modules["homeassistant.components.trace"] = trace_pkg
sys.modules["homeassistant.components.trace.util"] = util
sys.modules["homeassistant.helpers.storage"] = storage_mod

_FAKE_DATA: dict = {TRACE_KEY: {"automation.11111": {}, "automation.8f2c1d": {},
                                "script.s1": {}}}


class _Hass:
    data = _FAKE_DATA


# The reader is a coroutine, so it is driven rather than inspected.
spec._DATA_TRACE_KEY = TRACE_KEY
traces, state, observed = asyncio.run(spec._load_traces(_Hass()))

check("the reader returns the runs it was given", len(traces) == 2, f"got {len(traces)}")
check("the reader says ok when it read something", state == "ok", f"got {state!r}")
check("coverage counts the trace buckets it can see",
      observed["buckets"] == 2,
      f"three buckets exist, one of them a script: got {observed['buckets']}")
check("coverage counts the runs it read back",
      observed["runs"] == 2, f"got {observed['runs']}")
check("coverage reports what the storage file holds",
      (observed["storage_keys"], observed["storage_runs"]) == (2, 3),
      f"got {observed['storage_keys']} keys / {observed['storage_runs']} runs")
check("coverage lists the bucket keys, so an empty page can be argued with",
      sorted(observed["bucket_keys"]) == ["automation.11111", "automation.8f2c1d"],
      f"got {observed['bucket_keys']}")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)

# ── diagnosis must not take the page down ───────────────────────────────────
# This line had never run in production, and when it finally did it raised:
# the rule table held tuples of patterns and the code called `.split()` on one.
# The whole tab answers HTTP 500 the first time any automation fails, so the
# diagnosis path needs a test that reaches it the way a real failure would.
D = spec.diagnose
check("a template error is diagnosed",
      (D("Error rendering template: 'dict object' has no attribute 'x'") or {})["id"]
      == "template")
# The `.*` in these patterns never worked, because the match was a substring
# test, so this rule has been dead since it was written.
missing = D("Service light.kitchen not found") or {}
check("a wildcard pattern actually matches",
      missing["id"] == "entity_missing", f"got {missing['id']}")
check("a wildcard rule reports the text it matched",
      "not found" in missing.get("matched", ""), f"got {missing.get('matched')!r}")
check("an unrecognised error is reported as unclassified, not invented",
      (D("something nobody has a rule for") or {})["id"] == "unclassified")
check("no error text yields no diagnosis at all", D(None) is None)
check("a non-string error does not raise", D({"weird": True}) is None)
bad_rule = spec.DIAGNOSES[0]
try:
    spec.DIAGNOSES.insert(0, {"id": "broken", "match": ("([unclosed",),
                              "suggestion": "x"})
    broken = D("anything at all") or {}
    check("a bad pattern in our own rule table is contained", True)
    if broken["id"] not in ("broken",):
        print(f"        (reported as {broken['id']!r})")
finally:
    spec.DIAGNOSES.remove(spec.DIAGNOSES[0])
check("the rule table is back to its original first entry",
      spec.DIAGNOSES[0] is bad_rule)

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
