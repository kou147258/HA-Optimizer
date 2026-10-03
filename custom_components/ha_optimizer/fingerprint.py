"""Fingerprint anomaly detector - compares today against the days before it.

Architecture:
  DailyProfiler   — runs at 00:05, snapshots yesterday's metrics
  FingerprintStore — keeps a 30-day rolling window in .storage/
  SigmaDetector   — flags anomalies: today vs rolling mean ± 2σ (IQR when <7 days)
  CorrelationLinker — matches an anomaly's timestamp to HA events (update, restart, reload)
  FingerprintAnalyzer — main entry point, called from the analyze_fingerprint service
"""
from __future__ import annotations

import json
import logging
import math
import os
import statistics
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

FINGERPRINT_STORE_KEY = "ha_optimizer_fingerprint"
STORAGE_VERSION = 1
BASELINE_DAYS_WINDOW = 30   # keep at most 30 days of history
MIN_DAYS_FOR_SIGMA = 7      # need at least 7 days for σ; otherwise use IQR
SIGMA_THRESHOLD = 2.0       # standard deviations to consider an anomaly
IQR_MULTIPLIER = 1.5        # IQR multiplier when insufficient days
MAX_EXTRAPOLATION_FACTOR = 24.0   # ceiling for a partial-day projection
TOP_WRITER_BASELINE_FACTOR = 2.0  # today vs this entity's own baseline peak
TOP_WRITER_MIN_BASELINE = 5       # below this a baseline peak is not a signal


async def _run_in_db_executor(hass: HomeAssistant, target, *args):
    """Run a recorder query on the recorder's own executor.

    Home Assistant distinguishes the recorder executor from the general
    purpose one, and says so in the log:

      Detected that custom integration 'ha_optimizer' accesses the database
      without the database executor

    It is not cosmetic. Recorder work on the general executor competes with
    the recorder's own writes for the same connection, and holding the
    general executor stalls unrelated work. `scanner.py` has done this
    correctly from the start; this module did not, and both of its queries
    were flagged.

    `get_instance()` raises when recorder is not set up, so catching here
    keeps the graceful degradation the callers already expect.
    """
    try:
        from homeassistant.components.recorder import get_instance
        return await get_instance(hass).async_add_executor_job(target, *args)
    except Exception as exc:  # noqa: BLE001 - degrade, never break the caller
        _LOGGER.warning("Recorder database unavailable, skipping: %s", exc)
        return None


# ================================================================
# FINGERPRINT STORE
# ================================================================

class FingerprintStore:
    """Store and read the per-day baselines in .storage/."""

    def __init__(self, hass: HomeAssistant):
        self._store = Store(hass, STORAGE_VERSION, FINGERPRINT_STORE_KEY)
        self._data: dict[str, dict] = {}  # {"2024-01-15": {metrics...}}

    async def async_load(self):
        raw = await self._store.async_load() or {}
        self._data = raw.get("days", {})
        await self._purge_old_days()

    async def async_save_day(self, date_str: str, metrics: dict):
        """Store the metrics for one specific day."""
        self._data[date_str] = metrics
        await self._purge_old_days()
        await self._store.async_save({"days": self._data})
        _LOGGER.debug("FingerprintStore: saved baseline for %s", date_str)

    def get_all_days(self) -> dict[str, dict]:
        return dict(self._data)

    def get_day(self, date_str: str) -> dict | None:
        return self._data.get(date_str)

    def count_days(self) -> int:
        return len(self._data)

    async def _purge_old_days(self):
        # The keys are LOCAL calendar days (DailyProfiler builds them that
        # way), so the cutoff has to be a local date too - a UTC cutoff is a
        # whole day off for everyone east or west of Greenwich.
        cutoff = (dt_util.as_local(dt_util.utcnow())
                  - timedelta(days=BASELINE_DAYS_WINDOW)).date()
        unparseable = [k for k in self._data if _parse_date(k) is None]
        if unparseable:
            # Without a parseable date these can never match the cutoff, so a
            # corrupt or hand-edited key stayed in the rolling window forever
            # and the store looked clean. Report them; delete nothing - the
            # data is the user's, and it is not this module's to drop.
            _LOGGER.warning(
                "FingerprintStore: %d unparsable key(s) in %s cannot be aged "
                "out and are kept as-is: %s",
                len(unparseable), FINGERPRINT_STORE_KEY,
                ", ".join(sorted(unparseable)[:5]),
            )
        old_keys = [k for k in self._data if _parse_date(k) and _parse_date(k) < cutoff]
        for k in old_keys:
            del self._data[k]


# ================================================================
# DAILY PROFILER
# ================================================================

class DailyProfiler:
    """Snapshot yesterday's metrics out of the recorder DB."""

    def __init__(self, hass: HomeAssistant):
        self.hass = hass

    async def async_profile_yesterday(self) -> dict | None:
        """Query DB for yesterday's metrics, on the recorder's executor."""
        return await _run_in_db_executor(self.hass, self._run)

    def _run(self) -> dict | None:
        try:
            from homeassistant.components.recorder import get_instance
            from sqlalchemy import text

            instance = get_instance(self.hass)
            db_url = str(instance.engine.url)
            is_mysql = "mysql" in db_url or "mariadb" in db_url
            # HA's configured time zone - the one the user sees in the UI.
            tz = dt_util.get_default_time_zone()

            now = dt_util.utcnow()
            # The day boundary is the LOCAL midnight, and the bounds are
            # computed here instead of in SQL. SQLite's
            # strftime('%s', '2026-10-03 00:00:00') and MySQL's
            # UNIX_TIMESTAMP() both read a naive string as UTC, so in GMT+8
            # "yesterday" began at 08:00 local: the first eight hours of every
            # local day were filed under the day before, and the last eight
            # hours were never counted at all.
            yesterday = (dt_util.as_local(now) - timedelta(days=1)).date()
            date_str = yesterday.isoformat()
            ts_start = int(_local_midnight(yesterday, tz).timestamp())
            # Next local midnight rather than "23:59:59", so a 23h DST day is
            # neither truncated nor double counted at the boundary.
            ts_end = int(
                _local_midnight(yesterday + timedelta(days=1), tz).timestamp()
            )

            if is_mysql:
                ts_7d    = "UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 7 DAY))"
                dom_expr = "SUBSTRING_INDEX(entity_id, '.', 1)"
            else:
                ts_7d    = "strftime('%s', 'now', '-7 days')"
                dom_expr = "substr(entity_id, 1, instr(entity_id, '.') - 1)"

            metrics: dict[str, Any] = {"date": date_str}

            with instance.get_session() as session:
                # 1. Total state writes yesterday
                row = session.execute(text(f"""
                    SELECT COUNT(*) FROM states
                    WHERE last_updated_ts >= {ts_start} AND last_updated_ts < {ts_end}
                """)).scalar()
                metrics["total_writes"] = int(row or 0)

                # 2. Top 10 entities by write count yesterday
                rows = session.execute(text(f"""
                    SELECT entity_id, COUNT(*) as cnt
                    FROM states
                    WHERE last_updated_ts >= {ts_start} AND last_updated_ts < {ts_end}
                    GROUP BY entity_id
                    ORDER BY cnt DESC
                    LIMIT 10
                """)).fetchall()
                metrics["top_writers"] = [
                    {"entity_id": r[0], "writes": int(r[1])} for r in rows if r[0]
                ]

                # 3. Automation triggers (event automation_triggered)
                try:
                    auto_row = session.execute(text(f"""
                        SELECT COUNT(*) FROM events
                        WHERE time_fired_ts >= {ts_start} AND time_fired_ts < {ts_end}
                          AND event_type = 'automation_triggered'
                    """)).scalar()
                    metrics["automation_triggers"] = int(auto_row or 0)
                except Exception:
                    metrics["automation_triggers"] = 0

                # 4. Integration restarts: count states flipping to unavailable/unknown
                #    per platform — the best proxy that needs no log file access
                # The LIMIT applies to the LIST, never to the total. It used to
                # apply to both, because the sum was taken over the rows the
                # query returned: twenty-five entities each writing four
                # unavailable states made the day's total 100 and the metric
                # 80, silently, and the same number feeds the sigma z-score,
                # so a busy day was understated exactly when it mattered most.
                rows_restart = session.execute(text(f"""
                    SELECT entity_id, COUNT(*) as cnt
                    FROM states
                    WHERE last_updated_ts >= {ts_start} AND last_updated_ts < {ts_end}
                      AND state IN ('unavailable', 'unknown')
                    GROUP BY entity_id
                    HAVING COUNT(*) >= 3
                    ORDER BY cnt DESC
                """)).fetchall()
                metrics["unavail_events"] = int(sum(r[1] for r in rows_restart))
                metrics["unavail_entities"] = len(rows_restart)
                metrics["unstable_entities"] = [
                    {"entity_id": r[0], "count": int(r[1])} for r in rows_restart[:5]
                ]

                uniq_row = session.execute(text(f"""
                    SELECT COUNT(DISTINCT entity_id) FROM states
                    WHERE last_updated_ts >= {ts_start} AND last_updated_ts < {ts_end}
                """)).scalar()
                # Deliberately NOT projected. This is a distinct-entity count,
                # not a rate: it saturates within the first hours of the day,
                # so scaling it by 24/hours is not a prediction - at 00:05 it
                # reports 24x the instance's entire entity list. Z-scoring the
                # raw partial value against a full-day baseline read as a large
                # drop every morning; projecting it would only have turned that
                # into a large rise. So the raw count is published and the
                # detector declines to judge it while the day is partial - see
                # SigmaDetector.FULL_DAY_ONLY_METRICS. The panel must render it
                # as a partial-day count, never as a /day figure.
                metrics["active_entities"] = int(uniq_row or 0)

                # 6. Current DB size (MB)
                try:
                    if is_mysql:
                        size_row = session.execute(text("""
                            SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 2)
                            FROM information_schema.tables
                            WHERE table_schema = DATABASE()
                        """)).scalar()
                        metrics["db_size_mb"] = float(size_row or 0)
                    else:
                        db_path_row = session.execute(text("PRAGMA database_list")).fetchone()
                        if db_path_row and db_path_row[2]:
                            size = os.path.getsize(db_path_row[2])
                            metrics["db_size_mb"] = round(size / 1024 / 1024, 2)
                        else:
                            metrics["db_size_mb"] = 0.0
                except Exception:
                    metrics["db_size_mb"] = 0.0

                # 7. How many important HA events fired today (homeassistant_start, component_loaded...)
                try:
                    ha_events_row = session.execute(text(f"""
                        SELECT COUNT(*) FROM events
                        WHERE time_fired_ts >= {ts_start} AND time_fired_ts < {ts_end}
                          AND event_type IN (
                            'homeassistant_start', 'homeassistant_stop',
                            'component_loaded', 'service_registered',
                            'homeassistant_final_write'
                          )
                    """)).scalar()
                    metrics["ha_lifecycle_events"] = int(ha_events_row or 0)
                except Exception:
                    metrics["ha_lifecycle_events"] = 0

                # 8. Timestamp anomaly hints from events (for CorrelationLinker)
                # One row per event TYPE, most frequent first, keeping the
                # first time it fired. Ordered by time under LIMIT 20 the
                # quota was spent by the opening burst - component_loaded
                # fires once per integration at startup - and the restart
                # later in the day, which is what explains a write spike, was
                # never returned at all.
                try:
                    event_rows = session.execute(text(f"""
                        SELECT event_type, MIN(time_fired_ts) AS ts, COUNT(*) AS cnt
                        FROM events
                        WHERE time_fired_ts >= {ts_start} AND time_fired_ts < {ts_end}
                          AND event_type IN (
                            'homeassistant_start', 'homeassistant_stop',
                            'component_loaded'
                          )
                        GROUP BY event_type
                        ORDER BY cnt DESC
                        LIMIT 20
                    """)).fetchall()
                    metrics["key_events"] = [
                        {"type": r[0], "ts": float(r[1])} for r in event_rows if r[0]
                    ]
                except Exception:
                    metrics["key_events"] = []

            return metrics

        except Exception as exc:
            _LOGGER.warning("DailyProfiler error: %s", exc)
            return None


# ================================================================
# SIGMA / IQR DETECTOR
# ================================================================

class SigmaDetector:
    """Compare today's value against the rolling baseline; return the anomalies."""

    # Metrics that cannot be projected from a partial day, and so are not
    # judged until 24h have elapsed (today["partial"] is False). The counted
    # metrics are rates and are projected by _extrapolate_to_day; these two
    # are not — see _run_today for why projecting them is meaningless.
    # "Until partial is False" reads like a wait, and it is not one:
    # today runs from local midnight to now, so hours_elapsed is always
    # under 24 and `partial` is always True - by the time a day reaches 24
    # hours it has rolled over. These two metrics are therefore never judged
    # by the anomaly path at all. That is a deliberate trade: a false alarm
    # every morning is worse than no alarm, and projecting them is worse
    # still. It is NOT a gap that fills itself tomorrow, so the panel is told
    # to render them as partial-day counts, never as a /day figure, and to
    # not imply that a verdict is pending.
    FULL_DAY_ONLY_METRICS = frozenset({"active_entities", "ha_lifecycle_events"})

    # (i18n label key, i18n unit key) — resolved by the panel's tVal()
    METRIC_LABELS = {
        "total_writes":        ("fp_metric_total_writes",     "fp_unit_times"),
        "automation_triggers": ("fp_metric_auto_triggers",    "fp_unit_times"),
        "unavail_events":      ("fp_metric_unavail_events",   "fp_unit_times"),
        "active_entities":     ("fp_metric_active_entities",  "fp_unit_entities"),
        "ha_lifecycle_events": ("fp_metric_ha_lifecycle",     "fp_unit_times"),
    }

    def detect(
        self,
        today: dict,
        history: list[dict],
    ) -> list[dict]:
        """
        Return a list of anomaly dicts.
        history: PAST days only (today excluded), one metrics dict per item.
        """
        anomalies = []
        n = len(history)

        for metric_key, (label, unit) in self.METRIC_LABELS.items():
            today_val = today.get(metric_key)
            if today_val is None:
                continue

            hist_vals = [
                d[metric_key] for d in history
                if d.get(metric_key) is not None
            ]
            if len(hist_vals) < 3:
                continue  # too few days to compare

            mean_val = statistics.mean(hist_vals)
            today_val_f = float(today_val)

            if mean_val == 0 and today_val_f == 0:
                continue

            # Not a rate, so no projection and no verdict before the day is
            # over: a distinct-entity count saturates and a lifecycle count is
            # a startup burst, so a partial sample of either says nothing
            # about the day. Their raw values are still published.
            if today.get("partial") and metric_key in self.FULL_DAY_ONLY_METRICS:
                continue

            delta = today_val_f - mean_val

            if mean_val <= 0:
                # A zero baseline leaves the deviation unquantifiable: there is
                # no ratio to report, and both branches got this case wrong in
                # opposite directions. IQR divided by max(mean, 1) and turned
                # 50 against a zero baseline into "+5000%, critical", while
                # sigma's stdev < 0.001 short-circuit required mean > 0, so one
                # more day of the same zero history made the identical
                # situation report nothing at all - the alarm got quieter as
                # the baseline got more convincing. Report the jump as an
                # absolute delta with no percentage, and name the reason.
                # Severity is "warning", not "critical": with a zero mean and
                # zero variance any non-zero value is infinitely many sigma, so
                # "critical" would be an artefact of a degenerate distribution
                # rather than evidence of extremity.
                method = "zero-baseline"
                severity = "warning"
                pct_change = None
                desc_key = "fp_anomaly_desc_zero_base"
            else:
                desc_key = "fp_anomaly_desc"
                # Pick the algorithm
                if n >= MIN_DAYS_FOR_SIGMA:
                    method = "σ"
                    try:
                        stdev = statistics.stdev(hist_vals)
                    except statistics.StatisticsError:
                        stdev = 0.0

                    if stdev < 0.001:
                        # No variance to compare against - only report when today differs from the mean by more than 50%
                        if abs(delta) / mean_val > 0.5:
                            severity = "warning"
                            pct_change = round(delta / mean_val * 100)
                        else:
                            continue
                    else:
                        z = delta / stdev
                        if abs(z) < SIGMA_THRESHOLD:
                            continue
                        severity = "critical" if abs(z) >= SIGMA_THRESHOLD * 1.5 else "warning"
                        pct_change = round(delta / mean_val * 100)

                else:
                    method = "IQR"
                    sorted_vals = sorted(hist_vals)
                    q1 = _percentile(sorted_vals, 25)
                    q3 = _percentile(sorted_vals, 75)
                    iqr = q3 - q1
                    lower = q1 - IQR_MULTIPLIER * iqr
                    upper = q3 + IQR_MULTIPLIER * iqr

                    if lower <= today_val_f <= upper:
                        continue
                    severity = "critical" if today_val_f > upper * 2 else "warning"
                    pct_change = round(delta / mean_val * 100)

            direction = "higher" if delta > 0 else "lower"
            anomalies.append({
                "metric": metric_key,
                "label": label,
                "unit": unit,
                "today": today_val,
                "baseline_mean": round(mean_val, 1),
                "baseline_days": n,
                # None when the baseline mean is zero: there is no percentage
                # to report, and the panel must render the absolute delta then.
                "pct_change": pct_change,
                "delta": round(delta, 1),
                "direction": direction,
                "severity": severity,
                "method": method,
                "description": {
                    "key": desc_key,
                    "params": {
                        "label": label,
                        "val": today_val,
                        "unit": unit,
                        # direction_key is resolved by the panel's tVal() before
                        # it is interpolated into {direction}
                        "direction_key": f"fp_direction_{direction}",
                        "pct": abs(pct_change) if pct_change is not None else None,
                        "mean": round(mean_val, 1),
                        # Used by the zero-baseline description; the percentage
                        # description simply ignores it.
                        "delta": round(delta, 1),
                        "days": len(hist_vals),
                    },
                },
            })

        return anomalies


# ================================================================
# CORRELATION LINKER
# ================================================================

class CorrelationLinker:
    """Match anomalous metrics against HA system events to explain the cause."""

    # Correlation lookaround window: ±2 hours
    WINDOW_SECONDS = 7200

    def link(
        self,
        anomalies: list[dict],
        today_metrics: dict,
        history_days: list[dict],
    ) -> list[dict]:
        """Add a 'correlations' field to each anomaly."""
        key_events = today_metrics.get("key_events", [])
        if not key_events:
            return anomalies

        enriched = []
        for anomaly in anomalies:
            correlations = []

            if anomaly["metric"] in ("total_writes", "automation_triggers", "unavail_events"):
                # HA restart/reload events close to today. The stored ts stays
                # a UTC epoch; it is converted here because the panel renders
                # this as a wall clock, and in GMT+8 a 20:00 restart used to
                # display as 12:00.
                for ev in key_events:
                    ev_type = ev.get("type", "")
                    if ev_type not in ("homeassistant_start", "component_loaded"):
                        continue
                    ts_dt = _ts_to_local(ev.get("ts"))
                    if ts_dt is None:
                        continue  # no usable timestamp - say nothing rather than
                                  # render 00:00 for a value that is not there
                    correlations.append({
                        "key": ("fp_corr_restart" if ev_type == "homeassistant_start"
                                else "fp_corr_reload"),
                        "params": {"time": ts_dt.strftime("%H:%M")},
                    })

            # Check whether one top writer stands out
            if anomaly["metric"] == "total_writes":
                top = today_metrics.get("top_writers", [])
                if top:
                    top1 = top[0]
                    # The RAW total, because it and the numerator have to come
                    # from the same window. `total_writes` is extrapolated to
                    # 24h so that a partial today compares fairly with a full
                    # baseline day; `top1["writes"]` is not. Dividing one by the
                    # other understated the share by exactly that factor: at
                    # 06:00 an entity with 400 of 600 writes - genuinely 67% -
                    # was computed as 17%, fell under the 20% gate below, and
                    # the correlation silently disappeared until the evening.
                    total = today_metrics.get("total_writes_raw") \
                        or today_metrics.get("total_writes", 1)
                    share = round(top1["writes"] / max(total, 1) * 100)
                    if share >= 20:
                        correlations.append({
                            "key": "fp_corr_top_writer",
                            "params": {
                                "entity": top1["entity_id"],
                                "pct": share,
                                "n": top1["writes"],
                            },
                        })

                    # history_days was accepted and never read, so this whole
                    # check compared today with itself. Compare the same entity
                    # against the baseline the anomaly was raised from: a
                    # writer running well past its own recorded peak is a
                    # different claim from "writes a lot", and it is the one
                    # that points at a loop rather than a busy integration.
                    vs_base = _baseline_top_writer_corr(
                        top1["entity_id"], int(top1["writes"]), history_days
                    )
                    if vs_base:
                        correlations.append(vs_base)

            # Compare today's unstable entities against the history
            if anomaly["metric"] == "unavail_events":
                unstable = today_metrics.get("unstable_entities", [])
                for ent in unstable[:3]:
                    correlations.append({
                        "key": "fp_corr_unavail",
                        "params": {"entity": ent["entity_id"], "n": ent["count"]},
                    })

            anomaly = dict(anomaly)
            anomaly["correlations"] = correlations
            enriched.append(anomaly)

        return enriched


# ================================================================
# FINGERPRINT ANALYZER — main entry point
# ================================================================

class FingerprintAnalyzer:
    """
    Main entry point for the Fingerprint feature.
    Called from service handle_analyze_fingerprint.
    """

    def __init__(self, hass: HomeAssistant, store: "FingerprintStore"):
        self.hass = hass
        self.store = store
        self._profiler = DailyProfiler(hass)
        self._detector = SigmaDetector()
        self._linker = CorrelationLinker()

    async def async_analyze(self) -> dict:
        """
        Run the fingerprint analysis for today.
        Returns a dict with anomalies, baseline_info and today_metrics.
        """
        await self.store.async_load()

        # Collect today's metrics (window = today, 00:00 to now)
        today_metrics = await self._profile_today()

        if today_metrics is None:
            return {
                "error": "Cannot read data from recorder DB",
                "anomalies": [],
                "baseline_days": 0,
                "today_metrics": {},
            }

        all_days = self.store.get_all_days()
        # The same LOCAL date the profiler used. In GMT+8 the UTC date is a
        # different day for the first eight hours of the local day, and then
        # today's own snapshot was not excluded from its own baseline.
        today_str = dt_util.as_local(dt_util.utcnow()).date().isoformat()

        # History = every day except today
        history = [
            v for k, v in sorted(all_days.items())
            if k != today_str
        ]

        baseline_days = len(history)
        anomalies = self._detector.detect(today_metrics, history)
        anomalies = self._linker.link(anomalies, today_metrics, history)

        # Build the sparkline series (30 days + today) for the panel
        sparklines = self._build_sparklines(history, today_metrics)

        confidence = _confidence_level(baseline_days)

        return {
            "anomalies": anomalies,
            "baseline_days": baseline_days,
            "confidence": confidence,
            "confidence_label": _confidence_label(baseline_days),
            "today_metrics": today_metrics,
            "sparklines": sparklines,
            "generated_at": dt_util.utcnow().isoformat(),
            "error": None,
        }

    async def async_collect_daily_baseline(self):
        """
        Runs at 00:05 daily — snapshots yesterday and saves it to the store.
        Scheduled from __init__.py via async_track_time_interval.
        """
        await self.store.async_load()
        metrics = await self._profiler.async_profile_yesterday()
        if metrics:
            date_str = metrics.get("date")
            if date_str:
                await self.store.async_save_day(date_str, metrics)
                _LOGGER.info("FingerprintAnalyzer: baseline saved for %s", date_str)
        else:
            _LOGGER.warning("FingerprintAnalyzer: failed to collect baseline for yesterday")

    async def _profile_today(self) -> dict | None:
        """Profile today, from midnight to now. Recorder executor - see
        _run_in_db_executor for why the general one is not good enough."""
        return await _run_in_db_executor(self.hass, self._run_today)

    def _run_today(self) -> dict | None:
        try:
            from homeassistant.components.recorder import get_instance
            from sqlalchemy import text

            instance = get_instance(self.hass)
            db_url = str(instance.engine.url)
            is_mysql = "mysql" in db_url or "mariadb" in db_url
            # HA's configured time zone - the one the user sees in the UI.
            tz = dt_util.get_default_time_zone()

            now = dt_util.utcnow()
            local_now = dt_util.as_local(now)
            # Local day, local bounds - see DailyProfiler._run for why.
            today = local_now.date()
            today_str = today.isoformat()
            ts_start = int(_local_midnight(today, tz).timestamp())
            ts_end = int(now.timestamp())   # up to now, on HA's own clock

            metrics: dict[str, Any] = {"date": today_str}

            # Hours since local midnight - the fraction the counted metrics
            # below are projected by. Measured between two aware datetimes so
            # a 23h or 25h DST day is scaled by what actually elapsed, and
            # local rather than UTC so "today" means the user's today.
            hours_elapsed = (
                local_now - _local_midnight(today, tz)
            ).total_seconds() / 3600.0
            metrics["hours_elapsed"] = round(hours_elapsed, 1)
            # Computed, not hardcoded: a day stays partial until 24h of it have
            # elapsed. SigmaDetector reads this flag to decline a verdict on
            # the metrics that are not rates (see FULL_DAY_ONLY_METRICS).
            # Nothing read the old constant True, so the panel has to start
            # reading this if it wants to label a day as partial.
            metrics["partial"] = hours_elapsed < 24.0

            with instance.get_session() as session:
                row = session.execute(text(f"""
                    SELECT COUNT(*) FROM states
                    WHERE last_updated_ts >= {ts_start} AND last_updated_ts < {ts_end}
                """)).scalar()
                # Extrapolate out to 24h so a partial today compares fairly
                # with a full baseline day. Through the shared helper now: this
                # site divided by the raw elapsed fraction while the two
                # counters below used max(hours_elapsed, 1), so the same
                # situation was projected x288 at 00:05 here and x48 at 00:30
                # further down - and the inflated figure is what the sigma
                # z-score was given.
                raw_writes = int(row or 0)
                metrics["total_writes"] = _extrapolate_to_day(raw_writes, hours_elapsed)
                metrics["total_writes_raw"] = raw_writes

                rows = session.execute(text(f"""
                    SELECT entity_id, COUNT(*) as cnt
                    FROM states
                    WHERE last_updated_ts >= {ts_start} AND last_updated_ts < {ts_end}
                    GROUP BY entity_id
                    ORDER BY cnt DESC
                    LIMIT 10
                """)).fetchall()
                metrics["top_writers"] = [
                    {"entity_id": r[0], "writes": int(r[1])} for r in rows if r[0]
                ]

                try:
                    auto_row = session.execute(text(f"""
                        SELECT COUNT(*) FROM events
                        WHERE time_fired_ts >= {ts_start} AND time_fired_ts < {ts_end}
                          AND event_type = 'automation_triggered'
                    """)).scalar()
                    raw_auto = int(auto_row or 0)
                    metrics["automation_triggers"] = _extrapolate_to_day(raw_auto, hours_elapsed)
                    metrics["automation_triggers_raw"] = raw_auto
                except Exception:
                    metrics["automation_triggers"] = 0
                    metrics["automation_triggers_raw"] = 0

                # No LIMIT: the sum below is the day's total, and a limited row
                # set understates it — then extrapolates what it undercounted.
                # HAVING >= 3, matching DailyProfiler on purpose. That side is
                # the one that is right: a single unavailable transition is an
                # ordinary reconnect rather than flapping, so >= 2 let noise
                # in; and it is also the side that has been writing the store
                # for up to 30 days, so loosening only today's threshold made
                # "today" and the baseline it is z-scored against two
                # different measurements.
                rows_restart = session.execute(text(f"""
                    SELECT entity_id, COUNT(*) as cnt
                    FROM states
                    WHERE last_updated_ts >= {ts_start} AND last_updated_ts < {ts_end}
                      AND state IN ('unavailable', 'unknown')
                    GROUP BY entity_id
                    HAVING COUNT(*) >= 3
                    ORDER BY cnt DESC
                """)).fetchall()
                raw_unavail = int(sum(r[1] for r in rows_restart))
                metrics["unavail_events"] = _extrapolate_to_day(raw_unavail, hours_elapsed)
                metrics["unavail_events_raw"] = raw_unavail
                metrics["unavail_entities"] = len(rows_restart)
                metrics["unstable_entities"] = [
                    {"entity_id": r[0], "count": int(r[1])} for r in rows_restart[:5]
                ]

                uniq_row = session.execute(text(f"""
                    SELECT COUNT(DISTINCT entity_id) FROM states
                    WHERE last_updated_ts >= {ts_start} AND last_updated_ts < {ts_end}
                """)).scalar()
                metrics["active_entities"] = int(uniq_row or 0)

                try:
                    if is_mysql:
                        size_row = session.execute(text("""
                            SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 2)
                            FROM information_schema.tables
                            WHERE table_schema = DATABASE()
                        """)).scalar()
                        metrics["db_size_mb"] = float(size_row or 0)
                    else:
                        db_path_row = session.execute(text("PRAGMA database_list")).fetchone()
                        if db_path_row and db_path_row[2]:
                            size = os.path.getsize(db_path_row[2])
                            metrics["db_size_mb"] = round(size / 1024 / 1024, 2)
                        else:
                            metrics["db_size_mb"] = 0.0
                except Exception:
                    metrics["db_size_mb"] = 0.0

                try:
                    ha_ev_row = session.execute(text(f"""
                        SELECT COUNT(*) FROM events
                        WHERE time_fired_ts >= {ts_start} AND time_fired_ts < {ts_end}
                          AND event_type IN (
                            'homeassistant_start','homeassistant_stop',
                            'component_loaded','service_registered',
                            'homeassistant_final_write'
                          )
                    """)).scalar()
                    # Also not projected: these all fire in the startup
                    # burst, so the count is a step function of the day rather
                    # than a rate, and a projection would inflate it by 24/h for
                    # no reason. Raw count published, no verdict before 24h -
                    # see active_entities above and
                    # SigmaDetector.FULL_DAY_ONLY_METRICS.
                    metrics["ha_lifecycle_events"] = int(ha_ev_row or 0)
                except Exception:
                    metrics["ha_lifecycle_events"] = 0

                # Grouped and frequency ordered - see DailyProfiler step 8.
                try:
                    ev_rows = session.execute(text(f"""
                        SELECT event_type, MIN(time_fired_ts) AS ts, COUNT(*) AS cnt
                        FROM events
                        WHERE time_fired_ts >= {ts_start} AND time_fired_ts < {ts_end}
                          AND event_type IN (
                            'homeassistant_start','homeassistant_stop',
                            'component_loaded'
                          )
                        GROUP BY event_type
                        ORDER BY cnt DESC
                        LIMIT 20
                    """)).fetchall()
                    metrics["key_events"] = [
                        {"type": r[0], "ts": float(r[1])} for r in ev_rows if r[0]
                    ]
                except Exception:
                    metrics["key_events"] = []

            return metrics

        except Exception as exc:
            _LOGGER.warning("FingerprintAnalyzer._run_today error: %s", exc)
            return None

    def _build_sparklines(self, history: list[dict], today: dict) -> dict[str, list]:
        """Build the data array for the panel sparkline (at most 30 points + today)."""
        keys = ["total_writes", "automation_triggers", "unavail_events"]
        result = {}
        for key in keys:
            points = [
                {"date": d.get("date", ""), "value": d.get(key, 0)}
                for d in history[-29:]  # 29 days of history
            ]
            points.append({
                "date": today.get("date", "today"),
                "value": today.get(key, 0),
                "is_today": True,
            })
            result[key] = points
        return result


# ================================================================
# HELPERS
# ================================================================

def _percentile(sorted_vals: list, pct: float) -> float:
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    idx = (pct / 100) * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _local_midnight(day, tz) -> datetime:
    """Aware midnight of a LOCAL calendar day in HA's configured time zone.

    `datetime.combine(day, time.min, tzinfo=tz)` pins the wall clock to local
    midnight, which is what the start of the day means to the user. Adding a
    day to a UTC datetime instead drifts the boundary by an hour on the two
    DST change days a year.
    """
    return datetime.combine(day, time.min, tzinfo=tz)


def _extrapolate_to_day(raw: int, hours_elapsed: float) -> int:
    """Project a partial-day counter up to a full 24h day.

    One helper for every counted metric in _run_today, so the sites cannot
    drift apart again: the write counter used to divide by the raw elapsed
    fraction while its two siblings floored the divisor at one hour, so the
    same input was projected x288 at 00:05 and x24 at 00:30 - the three
    sites disagreed by an order of magnitude about the same day.

    The floor on the divisor is the explicit cap: one hour of elapsed time is
    the most any projection may claim, so the factor can never exceed
    MAX_EXTRAPOLATION_FACTOR - which is also all a projection can honestly
    assert, since a day cannot hold more than a day.
    """
    factor = min(
        MAX_EXTRAPOLATION_FACTOR,
        MAX_EXTRAPOLATION_FACTOR / max(hours_elapsed, 1.0),
    )
    return round(raw * factor)


def _ts_to_local(ts: Any) -> datetime | None:
    """Convert a stored UTC epoch into a local wall-clock datetime.

    `datetime.utcfromtimestamp` has been deprecated since Python 3.12; the
    correct call is `fromtimestamp(ts, tz)`, which needs the UTC tz spelled out
    to mean the same thing. The result is then moved into HA's configured zone,
    because its only consumer is a rendered "%H:%M".

    Returns None instead of raising on a missing or unparsable value, so one bad
    event row costs a single correlation tag and not the whole analysis.
    """
    try:
        return dt_util.as_local(datetime.fromtimestamp(float(ts), dt_util.UTC))
    except (TypeError, ValueError, OSError, OverflowError) as exc:
        _LOGGER.debug("Fingerprint: unusable event timestamp %r (%s), skipped", ts, exc)
        return None


def _baseline_top_writer_corr(
    entity_id: str, today_writes: int, history: list[dict]
) -> dict | None:
    """Compare today's busiest entity against that same entity's baseline.

    Only entities that appear in a stored day's top_writers list are compared.
    That list is truncated to ten, so "not in it" means "below the tenth
    writer", not "new" - reading that as a baseline of zero would manufacture
    an anomaly for every entity that has ever been busy.
    """
    seen = [
        int(w.get("writes") or 0)
        for day in history
        for w in (day.get("top_writers") or [])
        if w.get("entity_id") == entity_id
    ]
    if not seen:
        return None
    base_max = max(seen)
    if base_max < TOP_WRITER_MIN_BASELINE:
        return None  # too small a peak for a multiple of it to mean anything
    if today_writes < base_max * TOP_WRITER_BASELINE_FACTOR:
        return None
    return {
        "key": "fp_corr_top_writer_vs_base",
        "params": {"entity": entity_id, "n": today_writes, "base": base_max},
    }


def _parse_date(date_str: str):
    try:
        return datetime.fromisoformat(date_str).date()
    except Exception:
        return None


def _confidence_level(days: int) -> int:
    """Return a % confidence based on how many baseline days exist."""
    if days == 0:
        return 0
    if days < 3:
        return 20
    if days < 7:
        return 50
    if days < 14:
        return 75
    if days < 21:
        return 90
    return 99


def _confidence_label(days: int) -> dict:
    """Return an i18n {key, params} object resolved by the panel's tVal()."""
    if days == 0:
        return {"key": "fp_confidence_no_data"}
    if days < 3:
        key = "fp_confidence_very_low"
    elif days < 7:
        key = "fp_confidence_low"
    elif days < 14:
        key = "fp_confidence_moderate"
    else:
        key = "fp_confidence_high"
    return {"key": key, "params": {"days": days}}
