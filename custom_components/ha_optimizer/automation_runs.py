"""Automation run health: what ran, what failed, and what the failure was.

Why this module exists
----------------------
An automation that throws leaves almost no trace anywhere a panel can see. It
is not an event, so it does not appear in the logbook; and on an instance where
the error-log endpoint is unavailable there is nothing else to read. What DOES
exist is Home Assistant's own `trace` component, which keeps the last five runs
of every automation and records how each run ended.

That is the source, and the shape below is taken from HA 2026.8.3's
`homeassistant/components/trace/models.py` rather than from memory:

    hass.data[DATA_TRACE]           -> {automation_id: TraceBuckets}
    TraceBuckets.all_traces()       -> the runs (runs + not-triggered)
    BaseTrace.as_extended_dict()    -> {
        "state":        "finished" | "stopped" | "running",
        "timestamp":    {"start": dt, "finish": dt | None},
        "script_execution": {"result": ..., "error": str | None,
                             "stopped": str | None},
        "trace": {step_path: [ {"name", "state", "error", ...}, ... ]},
    }

Everything here is read defensively. This is a diagnostic, and a diagnostic
that crashes while reporting a broken automation is worse than no diagnostic:
it turns "an automation is failing" into "the panel is failing". Anything
unexpected becomes a named, reported gap rather than an exception.

The one distinction that matters most
------------------------------------
A run that stopped because a CONDITION was false is a normal, successful
outcome - that is what conditions are for. Home Assistant's own trace list says
"Stopped because a condition failed". Reporting those as failures would drown
the real ones, so `state == "stopped"` with a condition reason is counted
separately and never as a failure.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

# Run outcomes. Kept as plain strings so the panel can translate them.
OUTCOME_OK = "ok"
OUTCOME_FAILED = "failed"
OUTCOME_CONDITION = "condition_stopped"
OUTCOME_RUNNING = "running"
OUTCOME_UNKNOWN = "unknown"


async def _load_traces(hass) -> tuple[list[dict[str, Any]] | None, str]:
    """Return the automations' traces, using the component's own API.

    Returns (traces, state) where state is "unavailable", "empty" or "ok".

    Two things this replaces, both of which were quietly wrong:

    * it reads the storage under `"automation.<entity_id>"` keys, as
      `trace/util.py` itself does (`domain, item_id = key.split(".", 1)`). The
      previous code looked the same data up by `config_entry_id`, which never
      matches a key - so no run could be found, however much tracing was on;
    * it restores traces saved at shutdown first, which every helper in
      `trace/util.py` does and the previous code did not, so everything
      recorded before a restart was invisible as well.
    """
    try:
        from homeassistant.components.trace.util import (  # noqa: PLC0415
            async_list_traces,
            async_restore_traces,
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("trace helpers unavailable: %s", exc)
        return None, "unavailable"
    try:
        await async_restore_traces(hass)
        traces = await async_list_traces(hass, "automation", None)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Could not read traces: %s", exc)
        return None, "unavailable"
    return (traces or []), ("ok" if traces else "empty")


# ── failure diagnosis ──────────────────────────────────────────────────────
# Each rule is a (name, matcher, suggestion) triple. The suggestion is only
# offered when the matcher actually matched, and the rule name travels with it,
# so a wrong diagnosis is visible as a wrong rule rather than as confident
# prose. `kind` is what the panel groups on.
DIAGNOSES: list[dict[str, str]] = [
    {
        "id": "template",
        "match": ("template variable error", "undefinederror", "has no attribute",
                  "template render", "'dict object' has no attribute", "has no key"),
        "suggestion": "A template evaluated to an error. Test it in the template editor "
                      "with the same variables the trigger supplies - the trace's "
                      "changed_variables tab shows what the trigger actually passed.",
    },
    {
        "id": "entity_missing",
        "match": ("action .* not found", "service .* not found", "has not been loaded",
                  "entity .* not found", "not found in the entity registry",
                  "unknown service", "unknown entity"),
        "suggestion": "The action names a service or entity that does not exist. Either "
                      "the integration providing it is not loaded, or the name changed "
                      "when the entity_id was renamed.",
    },
    {
        "id": "unavailable",
        "match": ("is unavailable", "unavailable", "was aborted because the entity"),
        "suggestion": "The action targeted an entity that was unavailable at the time. "
                      "Usually the device dropped off the network; check it before "
                      "changing the automation.",
    },
    {
        "id": "auth",
        "match": ("unauthorized", "requires admin", "not authorized", "permission denied"),
        "suggestion": "The action needs an administrator context. Scripts and automations "
                      "run as the user that started them, so a service now restricted to "
                      "admins will fail for a non-admin start.",
    },
    {
        "id": "timeout",
        "match": ("timeout", "timed out"),
        "suggestion": "The run exceeded its time limit. Usually a wait_template loop or a "
                      "service that is slow to answer; check the step's duration in the "
                      "trace timeline.",
    },
    {
        "id": "no_response",
        "match": ("no response", "no longer accepting"),
        "suggestion": "The target stopped responding mid-run. This is usually the device, "
                      "not the automation.",
    },
]


def diagnose(error_text: str | None) -> dict[str, Any] | None:
    """Turn an error string into a named diagnosis, or say we do not know."""
    if not error_text:
        return None
    low = error_text.lower()
    for rule in DIAGNOSES:
        for needle in rule["match"].split("|"):
            if needle in low:
                return {"id": rule["id"], "suggestion": rule["suggestion"],
                        "matched": needle, "error": error_text}
    # Unknown is reported as unknown. A generic "check the logs" on an error we
    # could not classify is the advice equivalent of a wrong answer.
    return {"id": "unclassified", "suggestion": "", "matched": "", "error": error_text}


# ── run classification ─────────────────────────────────────────────────────
def classify(trace_dict: dict[str, Any]) -> dict[str, Any]:
    """One stored run -> an outcome plus the error behind it."""
    state = trace_dict.get("state")
    execution = trace_dict.get("script_execution") or {}
    if not isinstance(execution, dict):
        execution = {}
    error = execution.get("error")
    stopped = execution.get("stopped")
    ts = trace_dict.get("timestamp") or {}
    if not isinstance(ts, dict):
        ts = {}

    if state == "running":
        outcome = OUTCOME_RUNNING
    elif error:
        outcome = OUTCOME_FAILED
    elif state == "stopped":
        # "stopped" with no error means the run ended early on purpose - most
        # often a condition that was false. That is a normal outcome.
        reason = (stopped or "").lower()
        outcome = OUTCOME_CONDITION if "condition" in reason or not stopped else OUTCOME_OK
    elif state == "finished":
        outcome = OUTCOME_OK
    else:
        outcome = OUTCOME_UNKNOWN

    return {
        "outcome": outcome,
        "error": error,
        "stopped_reason": stopped,
        "start": ts.get("start"),
        "finish": ts.get("finish"),
        "last_step": trace_dict.get("last_step"),
        "run_id": trace_dict.get("run_id"),
    }


def _trace_dicts(bucket: Any) -> list[dict[str, Any]]:
    """The runs in a bucket, as plain dicts, without ever raising."""
    out: list[dict[str, Any]] = []
    try:
        traces = list(bucket.all_traces())
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Could not read a trace bucket: %s", exc)
        return out
    for t in traces:
        try:
            d = t.as_extended_dict() if hasattr(t, "as_extended_dict") else dict(t)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Could not serialise a trace: %s", exc)
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


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

        data_key, state = await _ensure_trace_component(self.hass)
        if state == "unavailable":
            result["coverage"] = {
                "traces_available": False,
                "state": "unavailable",
                "noteKey": "autoCoverUnavailable",
            }
            return result
        # No early return here. An absent trace store says the OUTCOMES are
        # unknown; it says nothing about which automations exist, and returning
        # an empty list at this point is what made 1.7.20 look exactly like
        # 1.7.18 - the listing code sat below this branch and was never reached
        # in the normal case.
        traces, state = await _load_traces(self.hass)
        if state == "ok":
            result["coverage"] = {
                "traces_available": True,
                "state": "ok",
                "noteKey": "",
            }
        else:
            result["coverage"] = {
                "traces_available": False,
                "state": "empty",
                "noteKey": "autoCoverEmpty",
            }

        names, enumerate_error = self._automation_names()
        if enumerate_error:
            result["coverage"]["enumerate_error"] = enumerate_error
        # Group the returned traces by the automation they belong to, using the
        # same key the component uses: "automation.<entity_id>".
        by_entity: dict[str, list[dict[str, Any]]] = {}
        for t in traces or []:
            key = t.get("key") or ""
            entity_id = key.split(".", 1)[1] if "." in key else key
            by_entity.setdefault(entity_id, []).append(t)
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
            row["traced"] = bool(runs)
            if not runs:
                row["last_outcome"] = "untraced"
                row["unmeasured_reason"] = (
                    "Home Assistant only records a trace for automations somebody "
                    "has traced - open this automation's Traces page once, or set "
                    "`trace: stored_traces:` in its YAML, and it will be measured here."
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
        """Automation id -> name and disabled state, from the entity registry.

        Traces are keyed by the automation's config-entry id, which is not the
        entity_id, so the registry is what connects the two.
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
            # Keyed by entity_id: that is what the trace storage uses
            # ("automation.<entity_id>"), and joining on anything else is how
            # this feature spent three releases finding nothing.
            out[entry.entity_id] = {
                "entity_id": entry.entity_id,
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
        conditions = [r for r in runs if r["outcome"] == OUTCOME_CONDITION]
        ok = [r for r in runs if r["outcome"] == OUTCOME_OK]

        ordered = sorted(runs, key=lambda r: str(r.get("start") or ""), reverse=True)
        last = ordered[0] if ordered else None

        consecutive = 0
        for r in ordered:
            if r["outcome"] == OUTCOME_FAILED:
                consecutive += 1
            elif r["outcome"] in (OUTCOME_OK, OUTCOME_RUNNING):
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
            "runs_known": len(runs),
            "failures": len(failures),
            "condition_stops": len(conditions),
            "successes": len(ok),
            "last_outcome": last["outcome"] if last else OUTCOME_UNKNOWN,
            "last_run": last.get("start") if last else None,
            "last_error": failures[0].get("error") if failures else None,
            "consecutive_failures": consecutive,
            "diagnosis": diagnosis,
        }

    def _summary(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        runs = sum(r["runs_known"] for r in rows)
        failures = sum(r["failures"] for r in rows)
        conditions = sum(r["condition_stops"] for r in rows)
        successes = sum(r["successes"] for r in rows)
        decided = successes + failures
        return {
            "automations_with_traces": len(rows),
            "runs_known": runs,
            "successes": successes,
            "failures": failures,
            "condition_stops": conditions,
            # Only over runs whose outcome is actually known. Counting a
            # condition stop in the denominator would make an automation that
            # correctly did nothing look like a failing one.
            "success_rate": round(successes / decided * 100, 1) if decided else None,
            "failing": sorted(
                (r for r in rows if r["failures"]),
                key=lambda r: (-r["consecutive_failures"], -r["failures"]),
            )[:5],
        }
