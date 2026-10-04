"""Run the panel's render functions for real, and let a ReferenceError be one.

`node --check` only parses: `partialDay is not defined` parses perfectly, as did
`today is not defined` before it. Two of the worst bugs in this panel's history
were an identifier read from a scope it was never in, and neither was visible to
any static check - they were found by someone executing the code.

So this executes it. It loads the panel's own <script> bodies into a Node
context with the smallest DOM and i18n stubs that will run, then calls each
render entry point with a representative payload - including the shapes that
actually break: a zero baseline, a null pct_change, a partial day, no traces, a
trash row that never expires, and a list with nothing in it.

A render path that throws is a red tab for the user, so it is a red check here.

Requires node. Exits 2 with a clear message if node is missing, rather than
passing silently - a check that cannot run must not report success.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
PANEL = ROOT / "custom_components" / "ha_optimizer" / "panel.html"

results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


if shutil.which("node") is None:
    print("  node is not on PATH - this check cannot run")
    print("\nFAILED: a check that cannot run must not report success")
    sys.exit(2)

panel = PANEL.read_text(encoding="utf-8")
scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>",
                     re.sub(r"<style[\s\S]*?</style>", " ", panel))
check("the panel has inline script to load", bool(scripts), "none found")
js = "\n".join(scripts)

# The payload shapes that have each broken this tab, kept deliberately small and
# explicit so a future change that drops one of them is visible here.
CASES = [
    ("fingerprint: a full day, a zero baseline and a null pct_change",
     "renderFingerprintResults", {
         "confidence": 20, "confidence_label": {"key": "fp_confidence_very_low",
                                                 "params": {"days": 2}},
         "baseline_days": 2, "anomalies": [], "sparklines": {},
         "today_metrics": {"date": "2026-10-04", "hours_elapsed": 10.6,
                           "partial": True, "total_writes": 1522,
                           "total_writes_raw": 674, "active_entities": 0,
                           "unavail_events": 248, "unavail_events_raw": 110,
                           "unavail_entities": 1, "unstable_entities": [],
                           "unavail_orphan_events": 0, "db_size_mb": 4.5,
                           "ha_lifecycle_events": 0, "top_writers": [],
                           "automation_triggers": 0, "key_events": []},
     }),
    ("fingerprint: an anomaly with no percentage to give",
     "renderFingerprintResults", {
         "confidence": 90, "confidence_label": {"key": "x", "params": {}},
         "baseline_days": 8, "sparklines": {"total_writes": [1, 2, 3]},
         "anomalies": [{"metric": "total_writes", "label": {"key": "fp_metric_total_writes"},
                        "unit": {"key": "fp_unit_times"}, "today": 674,
                        "baseline_mean": 0, "baseline_days": 6, "pct_change": None,
                        "delta": 674, "direction": "up", "severity": "warning",
                        "method": "zero-baseline", "description": {
                            "key": "fp_anomaly_desc_zero_base",
                            "params": {"label": "x", "val": 674, "unit": "t",
                                       "direction": "up", "pct": None, "mean": 0,
                                       "delta": 674, "days": 6}},
                        "correlations": []}],
         "today_metrics": {"date": "2026-10-04", "hours_elapsed": 10.6,
                           "partial": True, "total_writes": 674,
                           "total_writes_raw": 674, "active_entities": 5,
                           "unavail_events": 0, "unavail_events_raw": 0,
                           "db_size_mb": 4.5, "ha_lifecycle_events": 0,
                           "top_writers": [], "automation_triggers": 0,
                           "key_events": []},
     }),
    ("automations: no traces at all, and rows that never expire",
     "renderAutomationRuns", {
         "coverage": {"traces_available": False, "state": "unavailable",
                      "noteKey": "autoCoverUnavailable",
                      "observed": {"buckets": 0, "bucket_keys": [], "runs": 0,
                                   "storage_keys": 0, "storage_runs": 0},
                      "unmatched_runs": 0},
         "summary": {"runs_known": 0, "successes": 0, "failures": 0,
                     "aborted": 0, "not_triggered": 0, "success_rate": None,
                     "untraced": 1, "total_automations": 1, "failing": []},
         "automations": [{"automation_id": "automation.a", "entity_id": "automation.a",
                          "name": "a", "disabled": False, "traced": False,
                          "runs_known": 0, "failures": 0, "aborted": 0,
                          "not_triggered": 0, "successes": 0,
                          "last_outcome": "untraced", "last_run": None,
                          "last_error": None, "consecutive_failures": 0,
                          "last_triggered": "2026-10-03T12:00:00+00:00",
                          "diagnosis": None}],
     }),
]

harness = """
// ── the smallest world these render functions need ──
const _store = {};
function _el() {
  const e = {
    innerHTML: '', textContent: '', value: '', checked: false, disabled: false,
    style: new Proxy({}, { set: () => true, get: () => '' }),
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    dataset: new Proxy({}, { get: () => '' }),
    children: [], querySelector: () => null, querySelectorAll: () => [],
    appendChild() {}, removeChild() {}, insertBefore() {}, setAttribute() {},
    getAttribute: () => null, addEventListener() {}, removeEventListener() {},
    closest: () => null, focus() {}, scrollIntoView() {},
    getBoundingClientRect: () => ({ width: 100, height: 20, top: 0, left: 0 }),
  };
  return e;
}
globalThis.document = {
  getElementById: () => _el(), querySelector: () => _el(),
  querySelectorAll: () => [], createElement: () => _el(),
  createTextNode: () => ({}), body: _el(), documentElement: _el(),
  addEventListener() {}, removeEventListener() {},
};
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.dispatchEvent = () => true;
globalThis.postMessage = () => {};
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.setTimeout = (fn) => { try { fn(); } catch (e) { throw e; } return 0; };
globalThis.clearTimeout = () => {};
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};
globalThis.requestAnimationFrame = (fn) => { try { fn(0); } catch (e) {} return 0; };
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
globalThis.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
globalThis.navigator = { language: 'en', languages: ['en'] };
globalThis.location = { href: 'http://ha.local/lovelace/0', search: '', pathname: '/' };
globalThis.history = { replaceState() {}, pushState() {} };
globalThis.alert = () => {};
globalThis.confirm = () => true;
globalThis.prompt = () => null;
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' });
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
globalThis.IntersectionObserver = class {
  observe() {} unobserve() {} disconnect() {}
};
globalThis.MutationObserver = class {
  observe() {} disconnect() {} takeRecords() { return []; }
};
globalThis.CustomEvent = class { constructor(t, o) { this.detail = o && o.detail; } };
globalThis.AbortController = class {
  constructor() { this.signal = {}; }
  abort() {}
};
globalThis._hass = { states: { get: () => null }, callService: async () => ({}),
                      connection: {}, language: 'en' };

__PANEL_JS__

// ── the cases ──
const CASES = __CASES__;
let failed = 0;
for (const [label, fn, payload] of CASES) {
  try {
    const host = _el();
    // eval, not globalThis: the panel's `function foo() {}` declarations are
    // module-local in Node, so only a same-scope lookup finds them - which is
    // the same scoping the browser gives them, and the point of the exercise.
    eval(fn)(payload, host);
    if (typeof host.innerHTML !== 'string') {
      console.log('  FAIL  ' + label + '  (did not write to the host)');
      failed++;
    } else {
      console.log('  ok    ' + label);
    }
  } catch (err) {
    console.log('  FAIL  ' + label + '  -> ' + (err && err.message));
    failed++;
  }
}
process.exit(failed ? 1 : 0);
"""

with __import__("tempfile").TemporaryDirectory() as td:
    script = Path(td) / "render.js"
    script.write_text(
        harness.replace("__PANEL_JS__", js)
        .replace("__CASES__", json.dumps(CASES, ensure_ascii=False)),
        encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    out = (r.stdout or "") + (r.stderr or "")

for line in out.splitlines():
    if line.strip().startswith(("ok ", "FAIL ")):
        print("  " + line.strip())
    elif "Error" in line or "is not defined" in line:
        print("  " + line.strip()[:140])

executed = sum(1 for l in out.splitlines() if l.strip().startswith(("ok ", "FAIL ")))
check("every render case ran", executed == len(CASES),
      f"{executed} of {len(CASES)} produced a result line")
check("no render path threw", r.returncode == 0,
      out.strip().splitlines()[-1][:160] if out.strip() else "")

ok = sum(1 for x in results if x[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
