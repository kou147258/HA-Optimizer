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
from datetime import datetime, timedelta
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
        cutoff = (dt_util.utcnow() - timedelta(days=BASELINE_DAYS_WINDOW)).date()
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

            now = dt_util.utcnow()
            yesterday = (now - timedelta(days=1)).date()
            date_str = yesterday.isoformat()

            if is_mysql:
                ts_start = f"UNIX_TIMESTAMP('{yesterday} 00:00:00')"
                ts_end   = f"UNIX_TIMESTAMP('{yesterday} 23:59:59')"
                ts_7d    = "UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 7 DAY))"
                dom_expr = "SUBSTRING_INDEX(entity_id, '.', 1)"
            else:
                ts_start = f"strftime('%s', '{yesterday} 00:00:00')"
                ts_end   = f"strftime('%s', '{yesterday} 23:59:59')"
                ts_7d    = "strftime('%s', 'now', '-7 days')"
                dom_expr = "substr(entity_id, 1, instr(entity_id, '.') - 1)"

            metrics: dict[str, Any] = {"date": date_str}

            with instance.get_session() as session:
                # 1. Total state writes yesterday
                row = session.execute(text(f"""
                    SELECT COUNT(*) FROM states
                    WHERE last_updated_ts BETWEEN {ts_start} AND {ts_end}
                """)).scalar()
                metrics["total_writes"] = int(row or 0)

                # 2. Top 10 entities by write count yesterday
                rows = session.execute(text(f"""
                    SELECT entity_id, COUNT(*) as cnt
                    FROM states
                    WHERE last_updated_ts BETWEEN {ts_start} AND {ts_end}
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
                        WHERE time_fired_ts BETWEEN {ts_start} AND {ts_end}
                          AND event_type = 'automation_triggered'
                    """)).scalar()
                    metrics["automation_triggers"] = int(auto_row or 0)
                except Exception:
                    metrics["automation_triggers"] = 0

                # 4. Integration restarts: count states flipping to unavailable/unknown
                #    per platform — the best proxy that needs no log file access
                rows_restart = session.execute(text(f"""
                    SELECT entity_id, COUNT(*) as cnt
                    FROM states
                    WHERE last_updated_ts BETWEEN {ts_start} AND {ts_end}
                      AND state IN ('unavailable', 'unknown')
                    GROUP BY entity_id
                    HAVING COUNT(*) >= 3
                    ORDER BY cnt DESC
                    LIMIT 20
                """)).fetchall()
                metrics["unavail_events"] = int(sum(r[1] for r in rows_restart))
                metrics["unstable_entities"] = [
                    {"entity_id": r[0], "count": int(r[1])} for r in rows_restart[:5]
                ]

                # 5. Unique entities active yesterday
                uniq_row = session.execute(text(f"""
                    SELECT COUNT(DISTINCT entity_id) FROM states
                    WHERE last_updated_ts BETWEEN {ts_start} AND {ts_end}
                """)).scalar()
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
                        WHERE time_fired_ts BETWEEN {ts_start} AND {ts_end}
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
                try:
                    event_rows = session.execute(text(f"""
                        SELECT event_type, time_fired_ts
                        FROM events
                        WHERE time_fired_ts BETWEEN {ts_start} AND {ts_end}
                          AND event_type IN (
                            'homeassistant_start', 'homeassistant_stop',
                            'component_loaded'
                          )
                        ORDER BY time_fired_ts
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

            # Pick the algorithm
            if n >= MIN_DAYS_FOR_SIGMA:
                method = "σ"
                try:
                    stdev = statistics.stdev(hist_vals)
                except statistics.StatisticsError:
                    stdev = 0.0

                if stdev < 0.001:
                    # No variance to compare against - only report when today differs from the mean by more than 50%
                    if mean_val > 0 and abs(today_val_f - mean_val) / mean_val > 0.5:
                        severity = "warning"
                        pct_change = round((today_val_f - mean_val) / mean_val * 100)
                    else:
                        continue
                else:
                    z = (today_val_f - mean_val) / stdev
                    if abs(z) < SIGMA_THRESHOLD:
                        continue
                    severity = "critical" if abs(z) >= SIGMA_THRESHOLD * 1.5 else "warning"
                    pct_change = round((today_val_f - mean_val) / max(mean_val, 1) * 100)

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
                pct_change = round((today_val_f - mean_val) / max(mean_val, 1) * 100)

            direction = "higher" if pct_change > 0 else "lower"
            anomalies.append({
                "metric": metric_key,
                "label": label,
                "unit": unit,
                "today": today_val,
                "baseline_mean": round(mean_val, 1),
                "baseline_days": n,
                "pct_change": pct_change,
                "direction": direction,
                "severity": severity,
                "method": method,
                "description": {
                    "key": "fp_anomaly_desc",
                    "params": {
                        "label": label,
                        "val": today_val,
                        "unit": unit,
                        # direction_key is resolved by the panel's tVal() before
                        # it is interpolated into {direction}
                        "direction_key": f"fp_direction_{direction}",
                        "pct": abs(pct_change),
                        "mean": round(mean_val, 1),
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
                # HA restart/reload events close to today
                for ev in key_events:
                    ev_type = ev.get("type", "")
                    ts = ev.get("ts", 0)
                    if ev_type == "homeassistant_start":
                        ts_dt = datetime.utcfromtimestamp(ts)
                        correlations.append({
                            "key": "fp_corr_restart",
                            "params": {"time": ts_dt.strftime("%H:%M")},
                        })
                    elif ev_type == "component_loaded":
                        ts_dt = datetime.utcfromtimestamp(ts)
                        correlations.append({
                            "key": "fp_corr_reload",
                            "params": {"time": ts_dt.strftime("%H:%M")},
                        })

            # Check whether one top writer stands out
            if anomaly["metric"] == "total_writes":
                top = today_metrics.get("top_writers", [])
                if top:
                    top1 = top[0]
                    total = today_metrics.get("total_writes", 1)
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
        today_str = dt_util.utcnow().date().isoformat()

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

            now = dt_util.utcnow()
            today = now.date()
            today_str = today.isoformat()

            if is_mysql:
                ts_start = f"UNIX_TIMESTAMP('{today} 00:00:00')"
                ts_end   = f"UNIX_TIMESTAMP(NOW())"
            else:
                ts_start = f"strftime('%s', '{today} 00:00:00')"
                ts_end   = f"strftime('%s', 'now')"

            metrics: dict[str, Any] = {"date": today_str, "partial": True}

            # Hours elapsed today - so the comparison can be normalised
            hours_elapsed = now.hour + now.minute / 60.0
            metrics["hours_elapsed"] = round(hours_elapsed, 1)

            with instance.get_session() as session:
                row = session.execute(text(f"""
                    SELECT COUNT(*) FROM states
                    WHERE last_updated_ts BETWEEN {ts_start} AND {ts_end}
                """)).scalar()
                # Extrapolate out to 24h so a partial today compares fairly with a full baseline day
                raw_writes = int(row or 0)
                if hours_elapsed > 0:
                    metrics["total_writes"] = round(raw_writes * 24 / hours_elapsed)
                else:
                    metrics["total_writes"] = raw_writes
                metrics["total_writes_raw"] = raw_writes

                rows = session.execute(text(f"""
                    SELECT entity_id, COUNT(*) as cnt
                    FROM states
                    WHERE last_updated_ts BETWEEN {ts_start} AND {ts_end}
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
                        WHERE time_fired_ts BETWEEN {ts_start} AND {ts_end}
                          AND event_type = 'automation_triggered'
                    """)).scalar()
                    raw_auto = int(auto_row or 0)
                    metrics["automation_triggers"] = round(raw_auto * 24 / max(hours_elapsed, 1))
                    metrics["automation_triggers_raw"] = raw_auto
                except Exception:
                    metrics["automation_triggers"] = 0
                    metrics["automation_triggers_raw"] = 0

                rows_restart = session.execute(text(f"""
                    SELECT entity_id, COUNT(*) as cnt
                    FROM states
                    WHERE last_updated_ts BETWEEN {ts_start} AND {ts_end}
                      AND state IN ('unavailable', 'unknown')
                    GROUP BY entity_id
                    HAVING COUNT(*) >= 2
                    ORDER BY cnt DESC
                    LIMIT 20
                """)).fetchall()
                raw_unavail = int(sum(r[1] for r in rows_restart))
                metrics["unavail_events"] = round(raw_unavail * 24 / max(hours_elapsed, 1))
                metrics["unavail_events_raw"] = raw_unavail
                metrics["unstable_entities"] = [
                    {"entity_id": r[0], "count": int(r[1])} for r in rows_restart[:5]
                ]

                uniq_row = session.execute(text(f"""
                    SELECT COUNT(DISTINCT entity_id) FROM states
                    WHERE last_updated_ts BETWEEN {ts_start} AND {ts_end}
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
                        WHERE time_fired_ts BETWEEN {ts_start} AND {ts_end}
                          AND event_type IN (
                            'homeassistant_start','homeassistant_stop',
                            'component_loaded','service_registered',
                            'homeassistant_final_write'
                          )
                    """)).scalar()
                    metrics["ha_lifecycle_events"] = int(ha_ev_row or 0)
                except Exception:
                    metrics["ha_lifecycle_events"] = 0

                try:
                    ev_rows = session.execute(text(f"""
                        SELECT event_type, time_fired_ts
                        FROM events
                        WHERE time_fired_ts BETWEEN {ts_start} AND {ts_end}
                          AND event_type IN (
                            'homeassistant_start','homeassistant_stop',
                            'component_loaded'
                          )
                        ORDER BY time_fired_ts
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
