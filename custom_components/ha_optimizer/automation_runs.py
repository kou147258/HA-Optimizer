"""Automation run health: what ran, what failed, and what the failure was.

Why this module exists
----------------------
An automation that throws leaves almost no trace anywhere a panel can see. It
is not an event, so it does not appear in the logbook; and on an instance where
the error-log endpoint is unavailable there is nothing else to read. What DOES
exist is Home Assistant's own `trace` component, which keeps the last five runs
of every automation - `DEFAULT_STORED_TRACES = 5` in `trace/const.py`, and
`automation/__init__.py` calls `trace_automation` on every run, so a bucket
exists for anything that has run.

The shape below is taken from HA 2026.8.3's `trace/models.py` and
`trace/util.py`, not from memory - and getting that right is the whole reason
this module now works, because the earlier version got four fields wrong and
every one of them failed silently rather than raising:

    async_list_traces(hass, "automation", None)
        -> [ ActionTrace.as_short_dict(), ... ]      one dict per stored run

    as_short_dict() is:
        {domain, item_id, run_id, state, script_execution,
         timestamp: {start, finish}, last_step,
         error?: str, not_triggered?: True}

    * there is NO "key" field. There are `domain` and `item_id`, and item_id
      is the automation's unique_id - its YAML `id`, not its entity_id.
    * `error` is TOP-LEVEL. `script_execution` is a reason string
      ("finished", "aborted", "cancelled", "error", "failed_single",
      "failed_max_runs", "disallowed_recursion_detected"), so reading
      `script_execution.error` cannot work.
    * `state` is "stopped" for every completed run, successful or not, because
      `ActionTrace.finished()` sets it. "finished" is not a state that occurs.
    * `not_triggered` marks a trigger that evaluated a change and declined to
      fire. That is not a run.

One thing Home Assistant does not keep: a false `condition` and a `break:` both
record `aborted`, with nothing to tell them apart. So an aborted run is counted
in its own bucket here and never as either a success or a failure, instead of
being reported as a condition stop - which is what the previous version did, and
which is a claim the underlying data cannot support.

Everything here is read defensively. This is a diagnostic, and a diagnostic
that crashes while reporting a broken automation is worse than no diagnostic:
it turns "an automation is failing" into "the panel is failing". Anything
unexpected becomes a named, reported gap rather than an exception - and the
coverage section reports what was actually seen, because a page that says "no
failures" when it read nothing is the failure mode this project keeps fixing.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

# Run outcomes. Kept as plain strings so the panel can translate them.
OUTCOME_OK = "ok"
OUTCOME_FAILED = "failed"
OUTCOME_ABORTED = "aborted"
OUTCOME_RUNNING = "running"
OUTCOME_NOT_TRIGGERED = "not_triggered"
OUTCOME_UNKNOWN = "unknown"


async def _ensure_trace_component(hass) -> tuple[str, str]:
    """Make sure the `trace` component is loaded. Returns (state, reason).

    `trace` builds its store in `async_setup`, and nothing loads it on an
    instance where nobody has opened a trace page - so reading `hass.data`
    without setting it up reports "no traces" for a store that was never
    created. That is the difference between "this automation has no runs" and
    "we could not look".

    state is "ok" or "unavailable"; reason is filled in for the second so the
    panel can say why instead of showing a coverage gap with no explanation.
    """
    try:
        from homeassistant.setup import async_setup_component  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("async_setup_component unavailable: %s", exc)
        return "unavailable", f"homeassistant.setup is not importable ({exc})"
    try:
        loaded = await async_setup_component(hass, "trace", {})
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Could not set up the trace component: %s", exc)
        return "unavailable", f"the trace component raised while loading ({exc})"
    if not loaded:
        return "unavailable", "the trace component refused to set up"
    return "ok", ""


# `HassKey` instances are not strings, so the key has to be the constant the
# trace component itself uses. A string that looks right would silently never
# match, which is this feature's original bug all over again.
def _trace_data_key():
    """The key trace's buckets live under in `hass.data`, or None."""
    try:
        from homeassistant.components.trace.const import DATA_TRACE  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    return DATA_TRACE


_DATA_TRACE_KEY = _trace_data_key()


async def _load_traces(hass) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return the automations' traces, using the component's own API.

    Returns (traces, state, observed) where state is "unavailable", "empty" or
    "ok", and `observed` is what was actually seen - how many trace buckets
    exist, how many runs came back, and what the storage file holds.

    That last part is the point. "There are no traces" and "the traces are not
    being joined to anything" produce the same empty list, and this feature has
    shipped an empty list for four releases for exactly that reason: every
    layer reported confidently and nothing reported what it had actually seen.
    A number that can be wrong is worth more than a sentence that cannot.

    What replaced two things that were quietly wrong:

    * it goes through the component's API, which restores saved traces first
      and reads the real storage key, rather than the private dictionary;
    * it loads the `trace` component before reading, because the store is built
      in its `async_setup` and nothing loads it on an instance with no `trace:`
      entry in configuration.yaml - so reading without setting it up reports
      "no traces" for a store that was never created.
    """
    observed: dict[str, Any] = {"buckets": 0, "bucket_keys": [], "runs": 0,
                                "storage_keys": 0, "storage_runs": 0}
    try:
        from homeassistant.components.trace.util import (  # noqa: PLC0415
            async_list_traces,
            async_restore_traces,
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("trace helpers unavailable: %s", exc)
        return [], "unavailable", observed
    try:
        await async_restore_traces(hass)
        # The buckets are read as a count, never as the data source: reading
        # the component's own storage is what is correct, and reading its
        # private dictionary is the mistake this module used to make.
        store = hass.data.get(_DATA_TRACE_KEY)
        if isinstance(store, dict):
            keys = [k for k in store if str(k).startswith("automation.")]
            observed["buckets"] = len(keys)
            observed["bucket_keys"] = keys[:20]
        traces = await async_list_traces(hass, "automation", None)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Could not read traces: %s", exc)
        return [], "unavailable", observed
    observed["runs"] = len(traces or [])
    await _observe_storage(hass, observed)
    return (traces or []), ("ok" if traces else "empty"), observed


async def _observe_storage(hass, observed: dict[str, Any]) -> None:
    """Count what the saved-traces file holds, without using it as the source.

    Trace buckets only reach the storage file when Home Assistant stops, so an
    empty file while buckets exist in memory is the normal state of a running
    instance. Reporting it as a second number, beside the in-memory one, is
    what makes that distinguishable from a file that was never written.
    """
    try:
        from homeassistant.helpers.storage import Store  # noqa: PLC0415

        raw = await Store(hass, 1, "trace.saved_traces").async_load() or {}
        if isinstance(raw, dict):
            observed["storage_keys"] = len(raw)
            observed["storage_runs"] = sum(
                len(v) for v in raw.values() if isinstance(v, list))
    except Exception as exc:  # noqa: BLE001
        observed["storage_error"] = str(exc)[:200]


# ── failure diagnosis ──────────────────────────────────────────────────────
# Each rule is a dict of (id, match, kind, suggestion). `match` holds REGULAR
# EXPRESSIONS, and that is the second bug this table had: they were written
# with `.*` in them and then compared with `needle in text`, so every pattern
# containing a wildcard could never match anything. The `entity_missing` rule
# has been dead since it was written.
#
# The suggestion is only offered when a matcher actually matched, and the rule
# name and the matched text travel with it, so a wrong diagnosis is visible as
# a wrong rule rather than as confident prose. `kind` is what the panel groups
# on.
DIAGNOSES: list[dict[str, Any]] = [
    {
        "id": "template",
        "match": (r"template variable error", r"undefinederror",
                  r"has no attribute", r"template render", r"has no key",
                  r"jinja2?\b.*error"),
        "suggestion": "A template evaluated to an error. Test it in the template editor "
                      "with the same variables the trigger supplies - the trace's "
                      "changed_variables tab shows what the trigger actually passed.",
    },
    {
        "id": "entity_missing",
        "match": (r"action .* not found", r"service .* not found",
                  r"has not been loaded", r"entity .* not found",
                  r"not found in the entity registry", r"unknown service",
                  r"unknown entity"),
        "suggestion": "The action names a service or entity that does not exist. Either "
                      "the integration providing it is not loaded, or the name changed "
                      "when the entity_id was renamed.",
    },
    {
        "id": "unavailable",
        "match": (r"is unavailable", r"\bunavailable\b",
                  r"was aborted because the entity"),
        "suggestion": "The action targeted an entity that was unavailable at the time. "
                      "Usually the device dropped off the network; check it before "
                      "changing the automation.",
    },
    {
        "id": "auth",
        "match": (r"unauthorized", r"requires admin", r"not authorized",
                  r"permission denied"),
        "suggestion": "The action needs an administrator context. Scripts and automations "
                      "run as the user that started them, so a service now restricted to "
                      "admins will fail for a non-admin start.",
    },
    {
        "id": "timeout",
        "match": (r"timeout", r"timed out"),
        "suggestion": "The run exceeded its time limit. Usually a wait_template loop or a "
                      "service that is slow to answer; check the step's duration in the "
                      "trace timeline.",
    },
    {
        "id": "no_response",
        "match": (r"no response", r"no longer accepting"),
        "suggestion": "The target stopped responding mid-run. This is usually the device, "
                      "not the automation.",
    },
]


def diagnose(error_text: str | None) -> dict[str, Any] | None:
    """Turn an error string into a named diagnosis, or say we do not know.

    Never raises. A rule table that is wrong, or an error text that is not a
    string, must not take the page down with it: this is called once per failing
    automation, and the first exception here reached the panel as an HTTP 500 -
    which is the one thing a diagnostic must never do. The rule table was a
    tuple of patterns while the code called `.split()` on it, and the failure
    only ever stayed hidden because the joining bug upstream meant no
    automation was ever classified as failing, so this line never ran.
    """
    if not error_text or not isinstance(error_text, str):
        return None
    low = error_text.lower()
    try:
        for rule in DIAGNOSES:
            for pattern in rule["match"]:
                m = re.search(pattern, low)
                if m:
                    return {"id": rule["id"], "suggestion": rule["suggestion"],
                            "matched": m.group(0), "pattern": pattern,
                            "error": error_text}
    except re.error as exc:
        # A bad pattern in our own table. Reported as unclassified rather than
        # raised, so a typo in a diagnosis costs one classification, not the
        # page.
        _LOGGER.warning("Diagnosis pattern is not valid: %s", exc)
        return {"id": "unclassified", "suggestion": "", "matched": "",
                "error": error_text}
    # Unknown is reported as unknown. A generic "check the logs" on an error we
    # could not classify is the advice equivalent of a wrong answer.
    return {"id": "unclassified", "suggestion": "", "matched": "", "error": error_text}


# ── run classification ─────────────────────────────────────────────────────
# The five `script_execution` reasons `homeassistant/helpers/script.py` can
# record, read off that file rather than guessed. A reason that is not in this
# set is reported as itself, so a new one in Home Assistant shows up as a name
# the panel does not recognise instead of being quietly called a success.
EXEC_FINISHED = "finished"
EXEC_ABORTED = "aborted"
EXEC_CANCELLED = "cancelled"
# Reasons that mean the run broke rather than ended. "aborted" is deliberately
# not among them.
EXEC_ERRORS = ("error", "failed_single", "failed_max_runs",
               "disallowed_recursion_detected")


def classify(trace_dict: dict[str, Any]) -> dict[str, Any]:
    """One stored run -> an outcome plus the error behind it.

    The field names here are the ones `ActionTrace.as_short_dict()` actually
    produces, which is what `async_list_traces` returns. Four of the earlier
    assumptions were wrong, and each one failed silently:

    * the error is a TOP-LEVEL `error` key, not `script_execution.error` -
      `script_execution` is the reason string, so reading `.error` off it
      raised and the `isinstance` guard swallowed every real failure;
    * `state` is "stopped" for every finished run, successful or not -
      `ActionTrace.finished()` sets it - so "finished" is not a state that
      occurs, and treating "stopped" as an early exit would mislabel every
      successful run;
    * `not_triggered` marks a trigger that evaluated a change and declined to
      fire. That is not a run, and counting it as one is what made "0 runs"
      and "5 runs" disagree with the automation's own history;
    * there is no "key" field at all. There is `domain` and `item_id`.
    """
    state = trace_dict.get("state")
    error = trace_dict.get("error")
    reason = trace_dict.get("script_execution")
    ts = trace_dict.get("timestamp") or {}
    if not isinstance(ts, dict):
        ts = {}
    not_triggered = bool(trace_dict.get("not_triggered"))

    if not_triggered:
        outcome = OUTCOME_NOT_TRIGGERED
    elif state == "running":
        outcome = OUTCOME_RUNNING
    elif error or reason in EXEC_ERRORS:
        # An error is a failure whatever the reason says; the reason is
        # reported beside it so the diagnosis has something to work with.
        outcome = OUTCOME_FAILED
    elif state == "stopped":
        # "stopped" simply means it ended. Which way it ended is the reason:
        # a false condition and a `break:` both record "aborted", and Home
        # Assistant keeps no way to tell them apart, so neither is claimed
        # here - an "aborted" run is counted apart, never as a failure.
        outcome = OUTCOME_ABORTED if reason == EXEC_ABORTED else OUTCOME_OK
    else:
        outcome = OUTCOME_UNKNOWN

    return {
        "outcome": outcome,
        "error": error,
        "reason": reason,
        "reason_known": reason is None or reason in (
            EXEC_FINISHED, EXEC_ABORTED, EXEC_CANCELLED) or reason in EXEC_ERRORS,
        "start": ts.get("start"),
        "finish": ts.get("finish"),
        "last_step": trace_dict.get("last_step"),
        "run_id": trace_dict.get("run_id"),
        "item_id": trace_dict.get("item_id"),
    }


def _join(names: dict[str, dict[str, Any]],
          traces: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], set]:
    """Attach each run to the automation that produced it.

    Returns (by_entity_id, unmatched_item_ids).

    The matching is built from the registry's own `unique_id` rather than from
    the shape of the key, because the shape is not what it looks like:

        AutomationEntity passes its `unique_id` - the automation's YAML `id` -
        into `ActionTrace`, which builds the key as `automation.<that id>`.
        The entity_id is `automation.<alias>`, and an automation with an alias
        has an entity_id whose tail is not its id. `新建自动化` runs as
        `automation.xin_jian_zi_dong_hua` and is stored under whatever `id:`
        its YAML carries. Splitting the key and treating the tail as an
        entity_id therefore matches the automations that have no alias and
        silently misses the ones that do - which is the same page, half empty,
        for a reason no summary line would ever have named.

    The entity_id tail is kept as a fallback for an automation the registry
    does not describe, so a run is dropped only after both routes have failed,
    and the ones that get that far are returned to be reported.
    """
    by_unique: dict[str, str] = {}
    by_tail: dict[str, str] = {}
    for entity_id, meta in names.items():
        unique = meta.get("unique_id")
        if unique:
            by_unique[str(unique)] = entity_id
        tail = entity_id.split(".", 1)[1] if "." in entity_id else entity_id
        by_tail.setdefault(tail, entity_id)

    by_entity: dict[str, list[dict[str, Any]]] = {}
    unmatched: set = set()
    for t in traces or []:
        # `item_id` is what as_short_dict() actually carries; the key form is
        # accepted too so this keeps working if the component starts emitting
        # it, and neither route is assumed to be the only one that exists.
        item = t.get("item_id")
        if not item:
            key = t.get("key") or ""
            item = key.split(".", 1)[1] if "." in key else key
        entity_id = by_unique.get(str(item)) or by_tail.get(str(item))
        if entity_id is None:
            unmatched.add(str(item))
            continue
        by_entity.setdefault(entity_id, []).append(t)
    return by_entity, unmatched


# ── the analysis ───────────────────────────────────────────────────────────
class AutomationRunAnalyzer:
    """Report on every automation: its recent runs, its failures, its stats."""

    def __init__(self, hass: HomeAssistant):
        self.hass = hass

    async def async_analyze(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "automations": [],
            "summary": {},
            # What this analysis could NOT see. A panel that shows "no
            # failures" when it read nothing is the failure mode this project
            # keeps fixing, so coverage is part of the answer.
            "coverage": {"traces_available": False, "note": ""},
        }

        state, reason = await _ensure_trace_component(self.hass)
        if state == "unavailable":
            result["coverage"] = {
                "traces_available": False,
                "state": "unavailable",
                "noteKey": "autoCoverUnavailable",
                "reason": reason,
            }
            return result
        # No early return here. An absent trace store says the OUTCOMES are
        # unknown; it says nothing about which automations exist, and returning
        # an empty list at this point is what made 1.7.20 look exactly like
        # 1.7.18 - the listing code sat below this branch and was never reached
        # in the normal case.
        traces, state, observed = await _load_traces(self.hass)
        result["coverage"] = {
            "traces_available": state == "ok",
            "state": state,
            "noteKey": {"ok": "", "empty": "autoCoverEmpty"}.get(
                state, "autoCoverUnavailable"),
            "observed": observed,
        }

        names, enumerate_error = self._automation_names()
        if enumerate_error:
            result["coverage"]["enumerate_error"] = enumerate_error
        by_entity, unmatched = _join(names, traces)
        # Runs that arrived with nothing to match them are the difference
        # between "no automation has been traced" and "the traces exist and
        # this cannot attach them to anything". Reporting the count is what
        # turns the second into something fixable instead of a mystery.
        result["coverage"]["unmatched_runs"] = len(unmatched)
        if unmatched:
            result["coverage"]["unmatched_ids"] = sorted(unmatched)[:10]

        rows: list[dict[str, Any]] = []
        for automation_id, meta in names.items():
            runs = [classify(d) for d in by_entity.get(automation_id, [])]
            row = self._row(automation_id, meta, runs)
            # last_triggered is the one thing available for every automation.
            # It says the automation ran, and when - not whether it worked, and
            # saying otherwise would be the exact kind of confident wrong answer
            # this page exists to avoid.
            row["last_triggered"] = meta.get("last_triggered")
            row["enabled"] = meta.get("enabled")
            if not row["traced"]:
                row["last_outcome"] = "untraced"
                row["unmeasured_reason"] = (
                    "No stored run for this automation. Home Assistant keeps the "
                    "last 5 runs of every automation, so this means it has not run "
                    "since Home Assistant last started - not that it is broken. "
                    "The coverage figures say which of the two it is."
                )
            rows.append(row)

        # Failures first, then the ones we cannot judge, then the healthy.
        # The unmeasured ones sit in the middle: not a warning, not a
        # reassurance, and visibly not "fine".
        def _rank(r: dict[str, Any]) -> tuple:
            return (r["failures"] == 0, r["traced"] is False, r["name"].lower())

        rows.sort(key=_rank)
        result["automations"] = rows
        result["summary"] = self._summary(rows)
        result["summary"]["untraced"] = sum(1 for r in rows if not r["traced"])
        result["summary"]["total_automations"] = len(rows)
        result["summary"]["note"] = (
            "Success and failure are only known for automations that have been "
            "traced. The rest are listed as unmeasured - not as healthy."
        )
        return result

    def _automation_names(self) -> tuple[dict[str, dict[str, Any]], str]:
        """Automation entity_id -> name, unique_id and state, from the registry.

        The `unique_id` is the field that matters here. A trace bucket is keyed
        `automation.<unique_id>`, and `unique_id` is the automation's YAML `id`
        - not the entity_id, which is built from its alias. An earlier version
        of this docstring claimed traces are keyed by the config-entry id; a
        later one claimed they are keyed by the entity_id. Both are wrong, and
        both were believed for long enough to ship three releases of an empty
        page. So the unique_id is read from the registry and nothing is
        inferred from the shape of a key.
        """
        out: dict[str, dict[str, Any]] = {}
        try:
            ent_reg = er.async_get(self.hass)
        except Exception as exc:  # noqa: BLE001
            # Reported, not swallowed into an empty page. Returning {} here
            # renders a tab with nothing on it and no reason, which is how
            # 1.7.20 came to look exactly like 1.7.18.
            _LOGGER.warning("No entity registry available: %s", exc)
            return out, f"the entity registry is not readable ({exc})"
        if ent_reg is None:
            return out, "the entity registry is not loaded"
        for entry in ent_reg.entities.values():
            if entry.domain != "automation":
                continue
            # The state object carries last_triggered, which is the only run
            # fact available for an automation nobody has traced.
            state = self.hass.states.get(entry.entity_id)
            attrs = state.attributes if state is not None else {}
            # unique_id, because that is what a trace bucket is keyed by. The
            # entity_id is what the panel shows and they are not the same
            # string whenever the automation has an alias.
            out[entry.entity_id] = {
                "entity_id": entry.entity_id,
                "unique_id": entry.unique_id,
                "name": (attrs.get("friendly_name")
                         or entry.name or entry.original_name or entry.entity_id),
                "disabled": bool(entry.disabled),
                "enabled": (state.state == "on") if state is not None else None,
                "last_triggered": attrs.get("last_triggered"),
            }
        return out, ""

    def _row(self, automation_id: str, meta: dict[str, Any],
             runs: list[dict[str, Any]]) -> dict[str, Any]:
        failures = [r for r in runs if r["outcome"] == OUTCOME_FAILED]
        aborted = [r for r in runs if r["outcome"] == OUTCOME_ABORTED]
        ok = [r for r in runs if r["outcome"] == OUTCOME_OK]
        # A trigger that evaluated a change and declined to fire is not a run.
        # It is counted so the numbers add up to what Home Assistant holds,
        # and it is kept out of every rate.
        not_triggered = [r for r in runs if r["outcome"] == OUTCOME_NOT_TRIGGERED]
        real_runs = [r for r in runs if r["outcome"] != OUTCOME_NOT_TRIGGERED]

        ordered = sorted(real_runs, key=lambda r: str(r.get("start") or ""), reverse=True)
        last = ordered[0] if ordered else None

        consecutive = 0
        for r in ordered:
            if r["outcome"] == OUTCOME_FAILED:
                consecutive += 1
            elif r["outcome"] in (OUTCOME_OK, OUTCOME_RUNNING, OUTCOME_ABORTED):
                break

        diagnosis = None
        if failures:
            latest_error = next((r["error"] for r in failures if r.get("error")), None)
            diagnosis = diagnose(latest_error)

        return {
            "automation_id": automation_id,
            "entity_id": meta.get("entity_id"),
            "name": meta.get("name") or automation_id,
            "disabled": meta.get("disabled", False),
            # runs_known counts runs, so a not-triggered record cannot inflate
            # it and make an automation look exercised when it never was.
            "runs_known": len(real_runs),
            # Set here rather than by the caller: _summary reads it, and having
            # one function depend on a field another adds afterwards is an
            # ordering trap that only shows up as a KeyError - and a KeyError
            # in here is an HTTP 500 for the whole tab.
            "traced": bool(real_runs),
            "failures": len(failures),
            "aborted": len(aborted),
            "not_triggered": len(not_triggered),
            "successes": len(ok),
            "last_outcome": last["outcome"] if last else OUTCOME_UNKNOWN,
            "last_run": last.get("start") if last else None,
            "last_reason": last.get("reason") if last else None,
            "last_error": failures[0].get("error") if failures else None,
            "consecutive_failures": consecutive,
            "diagnosis": diagnosis,
        }

    def _summary(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        runs = sum(r["runs_known"] for r in rows)
        failures = sum(r["failures"] for r in rows)
        aborted = sum(r["aborted"] for r in rows)
        not_triggered = sum(r["not_triggered"] for r in rows)
        successes = sum(r["successes"] for r in rows)
        decided = successes + failures
        return {
            "automations_with_traces": sum(1 for r in rows if r["traced"]),
            "runs_known": runs,
            "successes": successes,
            "failures": failures,
            "aborted": aborted,
            "not_triggered": not_triggered,
            # Over runs whose outcome is known. An aborted run is neither, and
            # a not-triggered record is not a run at all, so both stay out of
            # the denominator rather than quietly inflating the rate.
            "success_rate": round(successes / decided * 100, 1) if decided else None,
            "failing": sorted(
                (r for r in rows if r["failures"]),
                key=lambda r: (-r["consecutive_failures"], -r["failures"]),
            )[:5],
        }
