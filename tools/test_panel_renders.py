"""Run the panel's render functions for real, and let a ReferenceError be one.

`node --check` only parses: `partialDay is not defined` parses perfectly, as did
`today is not defined` before it. Two of the worst bugs in this panel's history
were an identifier read from a scope it was never in, and neither was visible to
any static check - they were found by someone executing the code.

So this executes it. It loads the panel's own <script> bodies into a Node
context with the smallest DOM and i18n stubs that will run, then calls EVERY
render entry point with a representative payload - including the shapes that
actually break: a zero baseline, a null pct_change, a partial day, no traces at
all, a trash row that never expires, and a list with nothing in it.

A render path that throws is a red tab for the user, so it is a red check here.
Three things this file used to get wrong, each of which made it weaker than it
looked - and each of which is the difference between a check and a decoration:

  1. COVERAGE. It called two entry points out of the fourteen panel.html
     declares, so replacing an entire render body with `return;` left it green,
     including for the two ReferenceErrors it was written for had either landed
     in one of the other twelve. The entry points are now ENUMERATED from
     panel.html - from the code, with comments stripped, so a name that exists
     only in prose (`renderSoftRow`, in a comment) is not counted as a function
     - and the number called is checked against the number found. A new render
     function therefore cannot join the panel uncovered: it fails this check
     until someone gives it a payload.

  2. AN ASSERTION THAT COULD NOT FAIL. The stub preset `innerHTML` to '' and the
     test then asserted that what a render wrote was a string - true of every
     value the stub can hold, including the empty one it started with, so it
     held whatever the code did. The stub now RECORDS every write, and a render
     that produces no output fails, because a render that produces no output is
     a blank tab.

  3. SWALLOWED EXCEPTIONS. The requestAnimationFrame stub caught and discarded
     the error, and the gauge animation is the one place the panel wraps render
     work in rAF - so a ReferenceError there was thrown, caught and forgotten by
     the very check that exists to find it. Nothing catches it now: the error
     propagates, the case fails, and the failure names the entry point.

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


def strip_js_comments(src: str) -> str:
    """Drop // and /* */ comments, keeping string and template contents intact.

    A render entry point is counted from CODE, not from text. panel.html names
    `renderSoftRow` in a comment on a line that explains where soft-delete
    buttons are handled - a function that does not exist - and a scan that did
    not strip comments would try to call it and fail the check for a name the
    panel never declared.

    A bare `//` outside a string is always a comment in JS (a `/` inside a regex
    literal has to be escaped), so tracking quotes and `${}` is enough here; no
    regex-vs-division disambiguation is needed.
    """
    out: list[str] = []
    i, n, tpl = 0, len(src), 0          # tpl > 0 while inside a ${ } of a template
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c in "\"'":
            q = c
            out.append(c)
            i += 1
            while i < n:
                if src[i] == "\\":
                    out.append(src[i:i + 2])
                    i += 2
                    continue
                out.append(src[i])
                i += 1
                if src[i - 1] == q:
                    break
            continue
        if c == "`":
            out.append(c)
            i += 1
            while i < n:
                if src[i] == "\\":
                    out.append(src[i:i + 2])
                    i += 2
                    continue
                if src[i] == "`" and tpl == 0:
                    out.append(src[i])
                    i += 1
                    break
                if src[i] == "$" and src[i + 1:i + 2] == "{":
                    tpl += 1
                    out.append("${")
                    i += 2
                    continue
                if src[i] == "}" and tpl:
                    tpl -= 1
                    out.append("}")
                    i += 1
                    continue
                out.append(src[i])
                i += 1
            continue
        if c == "/" and nxt == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue
        out.append(c)
        i += 1
    return "".join(out)


code = strip_js_comments(js)
# An entry point is a top-level `function renderX(` / `function _renderX(`, or a
# const-bound function expression. Both forms, so a rewrite to an arrow function
# is still found - and a name found here that is not a function in the loaded
# panel fails loudly in node below rather than quietly counting.
ENTRY_DECL = re.compile(
    r"^[ \t]*(?:async[ \t]+)?function[ \t]+(_?render[A-Z]\w*)[ \t]*\(", re.M)
ENTRY_ASSIGN = re.compile(
    r"^[ \t]*(?:const|let|var)[ \t]+(_?render[A-Z]\w*)[ \t]*=[ \t]*"
    r"(?:async[ \t]+)?(?:function\b|\()", re.M)
FOUND: list[str] = []
for m in list(ENTRY_DECL.finditer(code)) + list(ENTRY_ASSIGN.finditer(code)):
    if m.group(1) not in FOUND:
        FOUND.append(m.group(1))
check("the panel declares render entry points to drive", bool(FOUND), "none found")
print(f"        {len(FOUND)} render entry points found: {', '.join(FOUND)}")

# ── every top-level function that writes to the document, named or not ─────
# `render*` is a naming convention, not a coverage guarantee. The trash table
# is drawn by `loadSoftDeleted`, an async service loader that would not call
# itself a render even if it rendered - so no case drove it, and the count
# above read as full coverage while a whole table went unexecuted.
#
# So the coverage question is asked of the CODE rather than of the names: every
# top-level function that assigns to a DOM property is found here, and has to
# be driven by a case below or declared here with a reason. A new DOM writer
# that nobody has thought about fails the check rather than joining a blind
# spot.
TOP_FN = re.compile(r"^(?:async[ \t]+)?function[ \t]+(\w+)[ \t]*\(", re.M)
# A line at column 0 begins the next top-level statement. The panel indents
# function bodies, so this bounds each body without parsing JavaScript.
TOP_STMT = re.compile(
    r"^(?:async[ \t]+)?function[ \t]+\w+[ \t]*\(|^(?:const|let|var|class)[ \t]", re.M)
DOM_WRITE = re.compile(
    r"\.(?:innerHTML|outerHTML|textContent|innerText)\s*=(?!=)"
    r"|\.(?:insertAdjacentHTML|appendChild|replaceChildren|prepend|append|after|before)\s*\(")

_starts = [(m.start(), m.group(1)) for m in TOP_FN.finditer(code)]
_bounds = [m.start() for m in TOP_STMT.finditer(code)]
writers: dict[str, str] = {}
for pos, name in _starts:
    nxt = [b for b in _bounds if b > pos]
    writers[name] = code[pos:nxt[0] if nxt else len(code)]
writers = {n: b for n, b in writers.items() if DOM_WRITE.search(b)}

# Real gaps, each one a decision rather than a shape - a rename makes the
# "still a real gap" check below fail, which is the point. A new DOM writer is
# not absorbed into a convenient pattern; someone has to say what it is.
#
#   the trash table and the other async service loaders - driving them means
#     stubbing a service response, and a wrong stub is worse than no stub
#     because it looks like coverage;
#   the destructive and interactive actions - reached from a click, and several
#     of them delete things. A smoke test that calls them can do damage;
#   chrome - toasts, connection status, the language and theme menus, the
#     empty-state placeholders. One line of status text, no view to get wrong.
DECLARED_GAPS = {
    # async service loaders
    "loadSoftDeleted", "loadAddonTab", "loadAutomationRuns", "loadSavedLang",
    "triggerCollectBaseline", "triggerDashboardAnalysis", "triggerDeadCodeAnalysis",
    "triggerFingerprintAnalysis", "triggerHealthAnalysis", "triggerRecorderAnalysis",
    "triggerScan", "triggerStormAnalysis", "recalcHealthFromResults",
    "_fetchSysBarOnly", "_addonRealtimeTick", "_refreshEmptyStates",
    "updateStats", "updateSoftDeleteCount", "updateSelectedBar",
    # destructive and interactive actions
    "hardDeleteEntity", "emptyTrashFlow", "cancelEmptyTrash", "performEmptyTrash",
    "restoreAllTrash", "restoreEntity", "showPurgeModal", "addonStart", "addonStop",
    "addonUpdate", "setLang", "setTheme", "_setSysbarUnavailable",
    # chrome
    "toast", "buildLangMenu", "buildThemeMenu", "_applyTranslations",
    "_updateConnStatus", "_patchAddonStats",
}

_driven = sorted(n for n in writers if n in FOUND)
_gaps = sorted(n for n in writers if n in DECLARED_GAPS)
undeclared = sorted(n for n in writers if n not in FOUND and n not in DECLARED_GAPS)
stale = sorted(n for n in DECLARED_GAPS if n not in writers)

check("every function that writes to the document is driven or declared",
      not undeclared,
      f"writes to the document but is neither driven nor declared: "
      f"{', '.join(undeclared)} - add a case, or declare it with a reason")
check("each declared gap is still a real gap",
      not stale,
      f"declared but no longer writes to the document: {', '.join(stale)} - "
      f"the gap closed, so the entry is now a lie")
print(f"        {len(writers)} function(s) write to the document: "
      f"{len(_driven)} driven, {len(_gaps)} declared gap(s)"
      + (f" ({', '.join(_gaps)})" if _gaps else ""))

# The payload shapes that have each broken this tab, kept deliberately explicit
# so a future change that drops one of them is visible here. `expect` is what the
# case asserts about the OUTPUT, not just that nothing threw:
#   dom    - the render must write text or markup into the stubbed document
#   return - the render must return a non-empty string (a string helper)
#   blank  - the render must write nothing, which is the correct result for the
#            empty branch it is being driven into
CASES = [
    # ── fingerprint: the two shapes the tab actually shipped broken on ──────
    {"fn": "renderFingerprintResults",
     "label": "a full day, a zero baseline and a null pct_change",
     "expect": "dom",
     "args": [{
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
     }]},
    # The baseline-short notice. `baseline_excluded` is what the service sends
    # when stored days were left out of the average, and it is the only place
    # the user is told why - so it has to render, and the reasons it carries
    # have to arrive escaped rather than as markup.
    {"fn": "renderFingerprintResults",
     "label": "stored days left out of the average are explained, and escaped",
     "expect": "contains",
     "contains": ["a 23.0h day (DST)",
                  "measured before windows were recorded"],
     "not_contains": ["<img src=x onerror=alert(1)>"],
     "args": [{
        "confidence": 20, "confidence_label": {"key": "fp_confidence_very_low",
                                               "params": {"days": 0}},
        "baseline_days": 0, "baseline_stored_days": 31,
        "baseline_excluded": {"a 23.0h day (DST)": 1,
                              "measured before windows were recorded": 30,
                              "<img src=x onerror=alert(1)>": 2},
        "anomalies": [], "sparklines": {},
        "today_metrics": {"date": "2026-10-04", "hours_elapsed": 10.6,
                          "partial": True, "total_writes": 1522,
                          "total_writes_raw": 674, "active_entities": 0,
                          "unavail_events": 248, "unavail_events_raw": 110},
        "error": None, "generated_at": "2026-10-04T02:36:00+00:00",
    }]},

    # The same tab when nothing was excluded. The notice must NOT appear here -
    # a permanent warning about a condition that does not apply trains people to
    # ignore warnings, which is how a real one stops being read.
    {"fn": "renderFingerprintResults",
     "label": "a complete baseline renders, and explains nothing it did not exclude",
     "expect": "contains",
     "not_contains": ["a 23.0h day (DST)",
                      "measured before windows were recorded"],
     "args": [{
        "confidence": 90, "confidence_label": {"key": "x", "params": {}},
        "baseline_days": 31, "baseline_stored_days": 31, "baseline_excluded": {},
        "sparklines": {"total_writes": [1, 2, 3]},
        "anomalies": [], "today_metrics": {"date": "2026-10-04",
                                           "hours_elapsed": 24, "partial": False},
        "error": None, "generated_at": "2026-10-04T02:36:00+00:00",
    }]},

    {"fn": "renderFingerprintResults",
     "label": "an anomaly with no percentage to give",
     "expect": "dom",
     "args": [{
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
     }]},

    # ── automations: no traces at all, and rows that never expire ───────────
    {"fn": "renderAutomationRuns",
     "label": "no traces at all, and rows that never expire",
     "expect": "dom",
     "args": [{"__el__": "automationRunsHost"}, {
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
     }]},

    # Triggered after the last stored run. The trace bucket only reaches disk
    # when HA stops, so this is the ordinary state for most of the day - and
    # it is the one that was reported: run something, watch the timestamp not
    # move.
    {"fn": "renderAutomationRuns",
     "label": "a run more recent than the stored trace says so",
     "expect": "contains",
     "contains": ["the stored run only reaches", "missing a key the service requires"],
     "args": [{"__el__": "automationRunsHost"}, {
        "coverage": {"traces_available": True, "noteKey": "autoCoverPartial",
                     "observed": {"buckets": 6, "runs": 13},
                     "unmatched_runs": 0},
        "summary": {"runs_known": 13, "successes": 5, "failures": 8,
                    "aborted": 0, "not_triggered": 0, "success_rate": 38.5,
                    "untraced": 0, "total_automations": 1, "failing": []},
        "automations": [{"automation_id": "automation.22222",
                          "entity_id": "automation.22222", "name": "22222",
                          "disabled": True, "traced": True,
                          "runs_known": 2, "failures": 2, "aborted": 0,
                          "not_triggered": 0, "successes": 0,
                          "last_outcome": "failed",
                          "last_run": "2026-10-04T03:11:04+00:00",
                          "last_triggered": "2026-10-04T08:26:00+00:00",
                          "consecutive_failures": 2,
                          "last_error": "required key not provided",
                          "diagnosis": {"id": "missing_key",
                                        "key": "autoDiag_missing_key",
                                        "matched": "required key not provided"}}],
     }]},

    # The other direction: the stored run is the newer fact, so there is
    # nothing to explain and the notice must stay away.
    {"fn": "renderAutomationRuns",
     "label": "the stored run is the newer fact, so nothing is explained",
     "expect": "contains",
     "not_contains": ["the stored run only reaches"],
     "args": [{"__el__": "automationRunsHost"}, {
        "coverage": {"traces_available": True, "noteKey": "autoCoverPartial",
                     "observed": {"buckets": 6, "runs": 13},
                     "unmatched_runs": 0},
        "summary": {"runs_known": 13, "successes": 5, "failures": 8,
                    "aborted": 0, "not_triggered": 0, "success_rate": 38.5,
                    "untraced": 0, "total_automations": 1, "failing": []},
        "automations": [{"automation_id": "automation.22222",
                          "entity_id": "automation.22222", "name": "22222",
                          "disabled": True, "traced": True,
                          "runs_known": 2, "failures": 2, "aborted": 0,
                          "not_triggered": 0, "successes": 0,
                          "last_outcome": "failed",
                          "last_run": "2026-10-04T08:26:00+00:00",
                          "last_triggered": "2026-10-04T03:11:04+00:00",
                          "consecutive_failures": 2,
                          "last_error": "required key not provided",
                          "diagnosis": {"id": "missing_key",
                                        "key": "autoDiag_missing_key",
                                        "matched": "required key not provided"}}],
     }]},

    # ── the scan table and the group strip: state-driven, not payload-driven ─
    {"fn": "renderTable",
     "label": "rows, pagination and the selection bar",
     "expect": "dom",
     "setup": """
        allResults = [
          {entity_id: 'sensor.living_room_temp', name: 'Living room temp',
           category: 'entity', risk_level: 'low', last_changed: '2026-10-04T09:00:00+00:00',
           reason: [{key: 'noData'}], used_in: ['automation:automation.a'], unique_id: 'abc',
           disabled: false, is_yaml_entity: false, yaml_location: null,
           writes_30d: 120, write_amplification: 1.2, write_alert: false},
          {entity_id: 'automation.nightly', name: 'Nightly', category: 'automation',
           risk_level: 'high', last_changed: null, reason: [], used_in: [],
           unique_id: '1774864588622', disabled: true, is_yaml_entity: true,
           yaml_location: {file: 'automations.yaml', line: 12},
           writes_30d: null, write_amplification: 0, write_alert: false}];
        filteredResults = allResults.slice();
        currentPage = 1;
        selectedIds = new Set(['automation.nightly']);
     """,
     "args": []},
    {"fn": "renderTable",
     "label": "nothing scanned at all",
     "expect": "dom",
     "setup": "allResults = []; filteredResults = []; currentPage = 1; selectedIds = new Set();",
     "args": []},
    {"fn": "renderGroupStrip",
     "label": "two areas, one of them unnamed",
     "expect": "dom",
     "setup": """
        groupFilter = null;
        resultGroups = [
          {area_id: null, area_name: null, count: 3,
           by_risk: {high: 1, medium: 1, low: 1}, platforms: {light: 2}, platform_count: 3},
          {area_id: 'kitchen', area_name: 'Kitchen', count: 12,
           by_risk: {high: 0, medium: 2, low: 0}, platforms: {light: 9, sensor: 3},
           platform_count: 9}];
     """,
     "args": []},
    {"fn": "renderGroupStrip",
     "label": "fewer than two groups: the strip hides itself",
     "expect": "blank",
     "setup": "groupFilter = null; resultGroups = [];",
     "args": []},

    # ── the overview gauges: the panel's only requestAnimationFrame render ────
    {"fn": "_renderOverview",
     "label": "a health score of zero, with the gauge animated",
     "expect": "dom",
     "args": [{"health_score": 0, "total_entities": 0,
               "by_risk": {"high": 0, "medium": 0, "low": 0},
               "by_category": {}}]},
    {"fn": "_renderOverview",
     "label": "a populated score, with by-category and by-risk counts",
     "expect": "dom",
     "args": [{"health_score": 62, "total_entities": 480,
               "by_risk": {"high": 7, "medium": 31, "low": 442},
               "by_category": {"entity": 400, "automation": 40, "helper": 40}}]},

    # ── recorder ───────────────────────────────────────────────────────────
    {"fn": "renderRecorderResults",
     "label": "db size, writers, wasteful entities, domains and a yaml suggestion",
     "expect": "dom",
     "args": [{"db_size_mb": 4.5, "total_states_count": 1234567,
               "commit_interval_suggestion": 5,
               "top_writers": [{"entity_id": "sensor.a", "count": 9000}],
               "wasteful_entities": [{"entity_id": "sensor.b", "total_records": 4000,
                                      "distinct_states": 2}],
               "domain_stats": [{"domain": "sensor", "count": 900},
                                {"domain": "automation", "count": 12}],
               "yaml_exclude_suggestion": "homeassistant:\n  customize:"}, 1757000000000]},
    {"fn": "renderRecorderResults",
     "label": "an analysis that came back with an error",
     "expect": "dom",
     "args": [{"error": "database is locked"}, 1757000000000]},

    # ── dashboards ─────────────────────────────────────────────────────────
    {"fn": "renderDashboardResults",
     "label": "one dashboard with views, cards and cross-references",
     "expect": "dom",
     "args": [{"dashboards": [{
                   "name": "Lovelace", "views": [{"view": "view-kitchen", "cards": 12,
                                                 "entities": 30, "entity_count": 30,
                                                 "db_queries": 0, "max_depth": 2,
                                                 "score": 80, "severity": "info",
                                                 "suggestion": None}],
                   "entity_count": 30, "heavy_cards": 1}],
               "summary": {"dashboard_score": 72, "total_critical": 1,
                           "total_warning": 4,
                           "issues": [{"severity": "critical", "count": 1,
                                       "label": {"key": "x"}}]},
               "ws_pressure": [{"entity_id": "sensor.a", "name": "a",
                                "severity": "critical", "distinct_states": 30,
                                "in_views": ["view-kitchen"], "writes_per_day": 900,
                                "waste_ratio": 3, "yaml_snippet": "x",
                                "suggestion": {"key": "x"}}],
               "recorder_crossref": [{"entity_id": "sensor.b", "name": "b",
                                      "severity": "warning", "distinct_states": 20,
                                      "in_views": ["view-kitchen"], "writes_per_day": 400,
                                      "waste_ratio": 2, "yaml_snippet": "y",
                                      "suggestion": {"key": "x"}}],
               "missing_entities": [{"entity_id": "sensor.c", "name": "c",
                                      "severity": "warning", "title": "t",
                                      "reason": "r", "type": "entity",
                                      "view": "view-kitchen", "count": 1}],
               "duplicate_entities": [{"entity_id": "sensor.d", "name": "d",
                                       "severity": "info", "title": "t",
                                       "reason": "r", "type": "entity",
                                       "view": "view-kitchen", "count": 2}],
               "unavailable_entities": [{"entity_id": "sensor.e", "name": "e",
                                         "severity": "warning", "title": "t",
                                         "reason": "r", "type": "entity",
                                         "view": "view-kitchen", "count": 1}],
               "overloaded_views": [{"view": "view-kitchen", "label": "l",
                                     "severity": "critical", "title": "t",
                                     "reason": "r", "count": 3}],
               "view_complexity": [{"view": "view-kitchen", "label": "l",
                                    "severity": "warning", "max_depth": 4,
                                    "card_count": 12, "custom_count": 2,
                                    "template_count": 3, "entities": 30,
                                    "entity_count": 30, "db_queries": 1,
                                    "score": 55, "suggestion": {"key": "x"}}],
               "heavy_cards": [{"type": "entities", "title": "t", "reason": "r",
                                "severity": "warning", "count": 1, "entity_count": 30}],
               "heavy_graphs": [{"title": "t", "reason": "r", "severity": "info",
                                 "count": 1, "entities": 30}],
               "template_heavy_cards": [{"type": "custom:button-card", "title": "t",
                                          "reason": "r", "severity": "info",
                                          "count": 1}],
               "unconfigured_custom_cards": [{"type": "custom:x", "title": "t",
                                              "reason": "r", "severity": "info",
                                              "count": 1}],
               "custom_cards_unchecked": "the Lovelace resource list was unreadable",
               "total_entity_refs": 30}, 1757000000000]},
    {"fn": "renderDashboardResults",
     "label": "no dashboard file at all",
     "expect": "dom",
     "args": [{"dashboards": []}, 1757000000000]},

    # ── storms ─────────────────────────────────────────────────────────────
    {"fn": "renderStormResults",
     "label": "one critical storm against a baseline",
     "expect": "dom",
     "args": [{"summary": {"critical": 1, "warning": 0},
               "storms": [{"name": "Living room temp", "entity_id": "sensor.temp",
                           "changes_24h": 4800, "changes_1h": 900,
                           "baseline_24h": 12, "ratio": 400, "severity": "critical",
                           "current_state": "4800", "distinct_states": 40,
                           "suggestions": [{"key": "x", "params": {}}]}]}, 1757000000000]},
    {"fn": "renderStormResults",
     "label": "no storms",
     "expect": "dom",
     "args": [{"summary": {"critical": 0, "warning": 0}, "storms": []}, 1757000000000]},

    # ── automation dead code ───────────────────────────────────────────────
    {"fn": "renderDeadCodeResults",
     "label": "two dead automations, one critical",
     "expect": "dom",
     "args": [{"total_analyzed": 42,
               "dead_automations": [
                   {"automation_id": "1", "entity_id": "automation.a", "alias": "A",
                    "severity": "critical",
                    "issues": [{"type": "dead_trigger",
                                "description": {"key": "x", "params": {}}}]},
                   {"automation_id": "2", "entity_id": "automation.b", "alias": "B",
                    "severity": "warning",
                    "issues": [{"type": "always_false_condition",
                                "description": {"key": "x", "params": {}}}]}]}, 1757000000000]},
    {"fn": "renderDeadCodeResults",
     "label": "nothing dead",
     "expect": "dom",
     "args": [{"total_analyzed": 42, "dead_automations": []}, 1757000000000]},

    # ── integration health ─────────────────────────────────────────────────
    {"fn": "renderHealthResults",
     "label": "one offline integration with a down device",
     "expect": "dom",
     "args": [{"summary": {"total_integrations": 3, "good_count": 2,
                           "warning_count": 0, "critical_count": 1, "avg_score": 61},
               "integrations": [{
                   "name": "Zigbee", "health_score": 40, "total_entities": 210,
                   "currently_unavailable": 3, "total_reconnects_7d": 18,
                   "config_failed": 1, "config_retrying": 0,
                   "config_failed_titles": ["Coordinator"],
                   "score_breakdown": {"connectivity": -10, "currently_down": -25,
                                       "config_entry": -15, "error_spike": 0},
                   "problem_entities": [{
                       "device_name": "Sensor", "entity_id": "sensor.s",
                       "is_down_now": True, "current_state": "unavailable",
                       "reconnects_7d": 4, "reconnects_today": 1,
                       "diagnosis": [{"key": "health_offline", "params": {}}]}]}]}, 1757000000000]},
    {"fn": "renderHealthResults",
     "label": "every integration healthy",
     "expect": "dom",
     "args": [{"summary": {"total_integrations": 3, "good_count": 3,
                           "warning_count": 0, "critical_count": 0, "avg_score": 100},
               "integrations": []}, 1757000000000]},

    # ── the system bar and the add-on list ─────────────────────────────────
    {"fn": "_renderSysBar",
     "label": "every metric missing",
     "expect": "dom",
     "args": [{"cpu_percent": None, "cpus": None, "memory_used_mb": None,
               "memory_total_mb": None, "disk_used_mb": None, "disk_total_mb": None,
               "operating_system": None, "hostname": None, "ha_version": None,
               "kernel": None, "_debug": {}}]},
    {"fn": "_renderSysBar",
     "label": "a full set of metrics",
     "expect": "dom",
     "args": [{"cpu_percent": 12.34, "cpus": 4, "memory_used_mb": 3072,
               "memory_total_mb": 8192, "disk_used_mb": 40960,
               "disk_total_mb": 102400, "operating_system": "Debian 12",
               "hostname": "ha", "ha_version": "2026.7.0", "kernel": "6.12",
               "_debug": {"cpu_source": "host"}}]},
    {"fn": "_renderSysCards",
     "label": "the old name, which the realtime tick still calls",
     "expect": "dom",
     "args": [{"cpu_percent": 5.0, "memory_used_mb": 1, "memory_total_mb": 2,
               "disk_used_mb": 3, "disk_total_mb": 4}]},
    {"fn": "_renderAddonList",
     "label": "one running add-on and one with an update",
     "expect": "dom",
     "args": [[{"slug": "core_mosquitto", "name": "Mosquitto broker",
                "version": "6.4.1", "version_latest": "6.4.1", "state": "started",
                "update_available": False, "cpu_percent": 0.5, "ram_percent": 2.1},
               {"slug": "a0d7b954_appdaemon", "name": "AppDaemon",
                "version": "4.5.0", "version_latest": "4.6.0", "state": "stopped",
                "update_available": True, "cpu_percent": None, "ram_percent": None}]]},
    {"fn": "_renderAddonList",
     "label": "no add-ons installed",
     "expect": "dom",
     "args": [[]]},

    # ── the sparkline helper: it returns markup instead of writing it ──────
    {"fn": "renderSparklineSVG",
     "label": "a series with three points",
     "expect": "return",
     "args": [[0, 4, 2, 8, 3]]},
    {"fn": "renderSparklineSVG",
     "label": "too few points to draw",
     "expect": "blank",
     "args": [[7]]},
]

# ── coverage, asserted here and not only in the node output ─────────────────
called = {c["fn"] for c in CASES}
missing = [n for n in FOUND if n not in called]
check("every render entry point the panel declares is driven by a case",
      not missing,
      f"never called: {', '.join(missing) or '-'} (a new render function must be "
      f"given a payload here, or it ships unexecuted)")
unknown = sorted(called - set(FOUND))
check("no case drives a name the panel does not declare",
      not unknown, f"unknown: {', '.join(unknown)}")
asserted = {c["fn"] for c in CASES if c["expect"] in ("dom", "return")}
unasserted = [n for n in FOUND if n not in asserted]
check("every render entry point is driven by a case that asserts its output",
      not unasserted,
      f"only driven by a blank-output case: {', '.join(unasserted)}")

harness = r"""
// ── the smallest world these render functions need ──
// Writes are RECORDED, not preset. The old stub handed out `innerHTML: ''` and
// the test then asserted the result was a string - which is true of '' too, so
// it held whatever the code did. A render that writes nothing is a blank tab,
// and it has to be able to fail here.
const _WRITES = [];
const _ELEMENTS = new Map();
function _el(id) {
  const e = {
    _id: id === undefined || id === null ? '?' : String(id),
    value: '', checked: false, disabled: false,
    style: new Proxy({}, { set: () => true, get: () => '' }),
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    dataset: new Proxy({}, { get: () => '' }),
    children: [], querySelector: () => null, querySelectorAll: () => [],
    appendChild() {}, removeChild() {}, insertBefore() {}, setAttribute() {},
    getAttribute: () => null, addEventListener() {}, removeEventListener() {},
    closest: () => null, focus() {}, scrollIntoView() {},
    getBoundingClientRect: () => ({ width: 100, height: 20, top: 0, left: 0 }),
  };
  for (const prop of ['innerHTML', 'outerHTML', 'textContent']) {
    let v = '';
    Object.defineProperty(e, prop, {
      get() { return v; },
      set(val) {
        v = val === null || val === undefined ? '' : String(val);
        _WRITES.push({ id: e._id, prop: prop, value: v });
      },
      enumerable: true, configurable: true,
    });
  }
  return e;
}
function _byId(id) {
  const key = String(id);
  if (!_ELEMENTS.has(key)) _ELEMENTS.set(key, _el(key));
  return _ELEMENTS.get(key);
}
globalThis.document = {
  getElementById: _byId, querySelector: () => _el('qs'),
  querySelectorAll: () => [], createElement: () => _el('created'),
  createTextNode: () => ({}), body: _el('body'), documentElement: _el('html'),
  addEventListener() {}, removeEventListener() {},
};
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.dispatchEvent = () => true;
globalThis.postMessage = () => {};
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.setTimeout = (fn) => { fn(); return 0; };
globalThis.clearTimeout = () => {};
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};
// No try/catch here. The gauge animation is the only place the panel wraps
// render work in requestAnimationFrame, and the old version of this line
// discarded whatever it threw - so an exception in a render path was thrown,
// caught and forgotten by the one check that exists to find it.
globalThis.requestAnimationFrame = (fn) => { fn(0); return 0; };
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
const FOUND = __FOUND__;
let failed = 0;

// Every name the scan found has to be a real function in the loaded panel, or
// the scan is counting something that is not there and the coverage number
// below means nothing.
for (const name of FOUND) {
  let kind;
  try { kind = eval('typeof ' + name); } catch (e) { kind = 'throws'; }
  if (kind !== 'function') {
    console.log('  FAIL  ' + name + ' is declared by the scan but is not a function in the panel (typeof ' + kind + ')');
    failed++;
  }
}

function _arg(a) {
  return (a && typeof a === 'object' && a.__el__) ? _byId(a.__el__) : a;
}

const called = [];
for (const c of CASES) {
  if (!called.includes(c.fn)) called.push(c.fn);
  _WRITES.length = 0;
  _ELEMENTS.clear();
  let ret;
  let err = null;
  try {
    if (c.setup) eval(c.setup);
    // eval, not globalThis: the panel's `function foo() {}` declarations are
    // module-local in Node, so only a same-scope lookup finds them - which is
    // the same scoping the browser gives them, and the point of the exercise.
    ret = eval(c.fn).apply(null, (c.args || []).map(_arg));
  } catch (e) {
    err = e;
  }
  if (err) {
    console.log('  FAIL  ' + c.fn + ': ' + c.label + '  -> ' + (err && err.message));
    failed++;
    continue;
  }
  const wrote = _WRITES.filter(w => w.value.trim() !== '');
  const returned = typeof ret === 'string' ? ret : '';
  if (c.expect === 'return') {
    if (returned.length) console.log('  ok    ' + c.fn + ': ' + c.label);
    else {
      console.log('  FAIL  ' + c.fn + ': ' + c.label + '  -> returned '
                  + JSON.stringify(ret) + ' instead of markup');
      failed++;
    }
  } else if (c.expect === 'blank') {
    if (wrote.length === 0 && returned.length === 0) {
      console.log('  ok    ' + c.fn + ': ' + c.label);
    } else {
      console.log('  FAIL  ' + c.fn + ': ' + c.label + '  -> wrote '
                  + wrote.map(w => w.id + '.' + w.prop).join(', ')
                  + ' where nothing should be written');
      failed++;
    }
  } else if (c.expect === 'contains') {
    // "Wrote something" cannot tell the notice that explains a short baseline
    // from any other markup on the tab. These cases name the text they need.
    const all = wrote.map(w => w.value).join('') + returned;
    const missing = (c.contains || []).filter(s => !all.includes(s));
    const leaked = (c.not_contains || []).filter(s => all.includes(s));
    if (!wrote.length) {
      console.log('  FAIL  ' + c.fn + ': ' + c.label
                  + '  -> wrote nothing; this tab would render blank');
      failed++;
    } else if (missing.length || leaked.length) {
      const why = [];
      if (missing.length) why.push('missing: ' + JSON.stringify(missing));
      if (leaked.length) why.push('present but must not be: ' + JSON.stringify(leaked));
      console.log('  FAIL  ' + c.fn + ': ' + c.label + '  -> ' + why.join('; '));
      failed++;
    } else {
      console.log('  ok    ' + c.fn + ': ' + c.label
                  + '  [' + (c.contains || []).length + ' marker(s) found]');
    }
  } else if (wrote.length) {
    console.log('  ok    ' + c.fn + ': ' + c.label + '  [' + wrote.length + ' writes]');
  } else {
    console.log('  FAIL  ' + c.fn + ': ' + c.label
                + '  -> wrote nothing; this tab would render blank');
    failed++;
  }
}

const never = FOUND.filter(n => !called.includes(n));
const extra = called.filter(n => !FOUND.includes(n));
if (never.length) {
  console.log('  FAIL  render entry points declared but never called: ' + never.join(', '));
  failed++;
}
if (extra.length) {
  console.log('  FAIL  cases call names the panel does not declare: ' + extra.join(', '));
  failed++;
}
console.log('  --    ' + called.length + ' of ' + FOUND.length + ' render entry points called');
process.exit(failed ? 1 : 0);
"""

with __import__("tempfile").TemporaryDirectory() as td:
    script = Path(td) / "render.js"
    script.write_text(
        harness.replace("__PANEL_JS__", js)
        .replace("__CASES__", json.dumps(CASES, ensure_ascii=False))
        .replace("__FOUND__", json.dumps(FOUND)),
        encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    out = (r.stdout or "") + (r.stderr or "")

for line in out.splitlines():
    if line.strip().startswith(("ok ", "FAIL ", "-- ")):
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
