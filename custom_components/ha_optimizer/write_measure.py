"""Attach recorder write volume to the scan candidates.

Why
---
The scanner says an entity is a candidate because nothing references it. It
does not say what that entity costs. Those are different questions, and for the
entities people actually argue about they have opposite answers:

* a sensor nobody reads, reporting 3 times a month, is free to leave alone;
* a state machine writing 2000 times a day with two distinct values is the one
  actually filling the database and pushing the browser.

`RecorderAnalyzer` already counts both (`top_writers`, `wasteful_entities`),
but it reports them as a separate analysis. Joining them onto the candidate list
is what turns "2371 unused entities" into a list where acting on the right 50
is obvious.

Design constraints, learned on this instance
--------------------------------------------
* `recorder.get_statistics` returns 400 here, so the aggregate tables are not
  usable; the raw `states` table is, and that is what this queries.
* Around 2000 candidate ids would make a single `IN (...)` clause enormous and
  slow on a large database, so the ids are chunked and the results merged.
* A missing measurement is NOT a zero. A query that could not run reports
  `measured: false` with a reason, because "0 writes" and "we could not look"
  are different facts and only one of them is a recommendation.
"""
from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

# SQLite's default limit on host parameters is 999, and a very long literal list
# is slow to parse long before it is wrong. 200 keeps each statement small
# enough for a large database while still needing only ten round trips for
# 2000 candidates.
CHUNK = 200

# Above this, an entity is writing so often that the row is worth acting on
# even though nothing references it. Tuned to be generous: the point is to
# surface the obviously expensive, not to rank candidates.
WRITE_ALERT_DAILY = 100

# Writes per distinct state: the shape of a sensor that flapped all day. A
# value above this with a low absolute count is still noise, so both conditions
# have to hold.
FLAP_RATIO = 50


async def _run_in_db_executor(hass, func, *args):
    """Run a blocking SQL function on the recorder's executor."""
    from homeassistant.components.recorder import get_instance  # noqa: PLC0415
    from homeassistant.util import dt as dt_util  # noqa: PLC0415

    instance = get_instance(hass)
    return await instance.async_add_executor_job(lambda: func(instance, *args))


def _quote_list(ids: list[str]) -> str:
    """An IN (...) literal.

    Entity ids come from the registry, never from a template or a service
    call, so they are quoted and escaped rather than bound; this keeps the
    statement portable across SQLite and MySQL, which spell their placeholders
    differently.
    """
    out = []
    for eid in ids:
        out.append("'" + eid.replace("\\", "\\\\").replace("'", "''") + "'")
    return ", ".join(out)


def _query_chunk(session, text, is_mysql: bool, chunk: list[str]) -> dict[str, dict]:
    if is_mysql:
        ts_expr = "UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 30 DAY))"
    else:
        ts_expr = "strftime('%s', 'now', '-30 days')"
    rows = session.execute(text(f"""
        SELECT entity_id,
               COUNT(*)          AS writes,
               COUNT(DISTINCT old_state || '|' || new_state) AS distinct_states
        FROM states
        WHERE last_updated_ts >= {ts_expr}
          AND entity_id IN ({_quote_list(chunk)})
        GROUP BY entity_id
    """)).fetchall()
    return {
        r[0]: {"writes": int(r[1] or 0), "distinct_states": int(r[2] or 0)}
        for r in rows if r[0]
    }


async def measure_candidates(hass, entity_ids: list[str]) -> dict[str, Any]:
    """Write volume for the given entity ids over the last 30 days.

    Returns {"measured": bool, "reason": str | None, "by_entity": {...},
             "window_days": 30}. `by_entity` holds only the ids that had rows:
    an entity absent from the result had no writes in the window, which is a
    real measurement, so the caller can tell it apart from one that was never
    queried.
    """
    out: dict[str, Any] = {"measured": False, "reason": None, "by_entity": {},
                           "window_days": 30}
    if not entity_ids:
        out["measured"] = True
        return out

    from sqlalchemy import text  # noqa: PLC0415

    def _work(instance):
        is_mysql = "mysql" in str(instance.engine.url) or \
                   "mariadb" in str(instance.engine.url)
        merged: dict[str, dict] = {}
        with instance.get_session() as session:
            for i in range(0, len(entity_ids), CHUNK):
                merged.update(
                    _query_chunk(session, text, is_mysql, entity_ids[i:i + CHUNK]))
        return merged

    try:
        out["by_entity"] = await _run_in_db_executor(hass, _work)
        out["measured"] = True
    except Exception as exc:  # noqa: BLE001
        # A diagnostic that cannot query says so. It does not report zeros,
        # and it does not raise: the scan itself succeeded, and failing the
        # whole scan over a missing measurement would lose the result the user
        # actually came for.
        out["reason"] = f"recorder query failed: {exc}"
        _LOGGER.warning("Candidate write measurement unavailable: %s", exc)
    return out


def annotate(results: list[dict], measurement: dict[str, Any]) -> dict[str, Any]:
    """Add the write numbers to each candidate dict, in place.

    Also returns a per-group rollup, because a hundred free entities and a
    hundred expensive ones are not the same thing to be told about.
    """
    by_entity = measurement.get("by_entity") or {}
    measured = bool(measurement.get("measured"))
    for r in results:
        stats = by_entity.get(r.get("entity_id"))
        if stats is None:
            r["writes_30d"] = None if measured else None
            r["distinct_states_30d"] = None
            r["measured"] = measured
            continue
        writes = stats["writes"]
        distinct = max(stats["distinct_states"], 1)
        r["writes_30d"] = writes
        r["distinct_states_30d"] = stats["distinct_states"]
        r["measured"] = measured
        r["writes_per_day"] = round(writes / 30, 1)
        r["write_amplification"] = round(writes / distinct, 1)
        # Two independent reasons to care, and they are different:
        #   - a lot of rows (the database is filling up)
        #   - a lot of rows per distinct value (a sensor is flapping)
        r["write_alert"] = bool(
            writes / 30 >= WRITE_ALERT_DAILY
            or (writes / distinct >= FLAP_RATIO and writes / 30 >= 10)
        )

    rollup: dict[str, dict] = {}
    for r in results:
        key = r.get("area_name") or r.get("area_id") or ""
        b = rollup.setdefault(key, {"count": 0, "writes_30d": 0, "alerts": 0,
                                    "measured": 0})
        b["count"] += 1
        if r.get("measured") and r.get("writes_30d") is not None:
            b["measured"] += 1
            b["writes_30d"] += r["writes_30d"]
            if r.get("write_alert"):
                b["alerts"] += 1
    return {"measured": measured, "reason": measurement.get("reason"),
            "by_area": rollup}
