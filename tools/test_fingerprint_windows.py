"""A baseline day and this morning are not the same measurement.

Two defects, both in `fingerprint.py`, both about comparing numbers that were
measured over different windows.

F1. A stored baseline day carried no record of the window it was measured over.
The profiler used to file a "day" as 16:00-to-16:00 local - SQLite read the
naive day-boundary string as UTC - and the code was later fixed to use the local
midnight, but the days already in the store were written by the old code and
nothing said so. The panel averaged them against a local-day measurement. That
is not a rounding error: a busy evening lands in one bucket and the morning in
the other, and the two were measured 8% apart. Averaging an unknown window with
a known one is worse than dropping it, so days are now stamped as they are
written and the unstamped ones are excluded - and the count of what was dropped,
and why, is reported rather than swallowed.

F2. The top-writer correlation compared today's writes since midnight against a
stored full-day peak, so an entity had to exceed twice a whole day's traffic
before the tag appeared. That is impossible before about twenty hours in, which
is every hour anyone is awake to read it. Today's figure is projected to a day
first, by the same helper the totals use.

Loads the real module with a stubbed Home Assistant; no file is written.
"""
from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ha_optimizer"

results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


# ── the smallest homeassistant that fingerprint.py imports ──────────────────
def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules.setdefault(name, m)
    return m


ha = _mod("homeassistant")
ha.__path__ = []                       # type: ignore[attr-defined]
helpers = _mod("homeassistant.helpers")
helpers.__path__ = []                  # type: ignore[attr-defined]
_mod("homeassistant.core", HomeAssistant=type("HomeAssistant", (), {}))
_mod("homeassistant.helpers.entity_registry",
     async_get=lambda hass: None, RegistryEntryDisabler=type("D", (), {"USER": "u"}))
_mod("homeassistant.helpers.storage", Store=type("Store", (), {}))

TZ = timezone(timedelta(hours=8))       # GMT+8, the machine this was measured on
dt_util = types.ModuleType("homeassistant.util.dt")
dt_util.UTC = timezone.utc
dt_util.now = lambda: datetime.now(timezone.utc)
dt_util.utcnow = lambda: datetime.now(timezone.utc)
dt_util.as_local = lambda d: d.astimezone(TZ)
dt_util.get_default_time_zone = lambda: TZ
_mod("homeassistant.util").dt = dt_util
sys.modules["homeassistant.util.dt"] = dt_util

fp = types.ModuleType("haopt.fingerprint")
fp.__file__ = str(COMPONENT / "fingerprint.py")
exec(compile((COMPONENT / "fingerprint.py").read_text(encoding="utf-8"),
             str(COMPONENT / "fingerprint.py"), "exec"), fp.__dict__)

WINDOW = {"version": fp.MEASUREMENT_WINDOW_VERSION, "hours": 24.0, "tz": "UTC+08:00"}


def day(total: float, *, window=True, version=None, hours=24.0) -> dict:
    d: dict = {
        "date": "2026-09-01",
        "total_writes": total,
        "top_writers": [{"entity_id": "sensor.busy", "writes": int(total)}],
    }
    if window:
        d["window"] = {"version": version or fp.MEASUREMENT_WINDOW_VERSION,
                       "hours": hours, "tz": "UTC+08:00"}
    return d


# ── 1. the window is stamped on the way in ─────────────────────────────────
src = (COMPONENT / "fingerprint.py").read_text(encoding="utf-8")
check("a stored day records the window rule that measured it",
      "MEASUREMENT_WINDOW_VERSION" in src.split("async def _run_in_db_executor")[0],
      "the constant is never referenced outside its own definition")
check("the stamped day carries the real span, not a constant 24",
      '"hours": round((ts_end - ts_start) / 3600.0, 2)' in src,
      "a 23h DST day would be stamped 24h and pass the DST test below")

# ── 2. comparability ───────────────────────────────────────────────────────
W = fp._window_is_comparable
ok, why = W(day(100))
check("a day stamped by the current rule is comparable", ok, why)

ok, why = W(day(100, window=False))
check("a day with no window at all is excluded", not ok, f"it was accepted: {why!r}")
check("and the reason names the cause, not just 'no'",
      "before windows were recorded" in why, why)

ok, why = W(day(100, version=1))
check("a day measured by an older window rule is excluded", not ok, why)
check("and the reason says which rule produced it",
      "v1" in why and f"v{fp.MEASUREMENT_WINDOW_VERSION}" in why, why)

ok, why = W(day(100, hours=23.0))
check("a 23h DST day is excluded from a 24h average", not ok, why)
ok, why = W(day(100, hours=25.0))
check("a 25h DST day is excluded from a 24h average", not ok, why)
ok, why = W(day(100, hours=23.6))
check("a day within tolerance is kept", ok, why)
check("a missing window length is excluded rather than assumed",
      not W({"window": {"version": fp.MEASUREMENT_WINDOW_VERSION}})[0])
check("a window that is not a dict is excluded rather than assumed",
      not W({"window": "yesterday"})[0])


# ── 3. the baseline is built from comparable days, and says what it dropped ─
class _Store:
    def __init__(self, days):
        self._days = dict(days)

    async def async_load(self):
        return None

    def get_all_days(self):
        return dict(self._days)

    def count_days(self):
        return len(self._days)


TODAY = {"total_writes": 1000, "top_writers": [], "hours_elapsed": 6.0,
         "partial": True, "active_entities": 5, "automation_triggers": 1}


async def run(days):
    a = fp.FingerprintAnalyzer.__new__(fp.FingerprintAnalyzer)
    a.store = _Store(days)
    a.hass = None
    a._profiler = None
    a._detector = fp.SigmaDetector()
    a._linker = fp.CorrelationLinker()

    async def today():
        return dict(TODAY)
    a._profile_today = today
    return await a.async_analyze()


today_str = dt_util.as_local(dt_util.utcnow()).date().isoformat()
good = {f"2026-09-{d:02d}": day(100 + d) for d in range(1, 6)}
# Keyed by the real local date so it cannot collide with - and silently
# overwrite - one of the days above. A collision here would show up as one
# unexplained exclusion, which is exactly the kind of thing this file exists
# to notice rather than accept.
good[today_str] = day(999, window=False)

rep = asyncio.run(run(good))
# `baseline_stored_days` already excludes today - it is the length of the
# candidate list, which is built with `k != today_str`. Subtracting again here
# is how a test ends up asserting the arithmetic it is meant to be checking.
kept = rep["baseline_stored_days"]
check("a clean store keeps all of its days",
      rep["baseline_days"] == kept, f"{rep['baseline_days']} kept, {kept} stored")
check("nothing is excluded when every day is stamped",
      rep["baseline_excluded"] == {}, rep["baseline_excluded"])

mixed = dict(good)
mixed["2026-08-01"] = day(200, window=False)
mixed["2026-08-02"] = day(210, version=1)
mixed["2026-08-03"] = day(220, hours=23.0)
rep2 = asyncio.run(run(mixed))
check("unstamped, old-rule and DST days are all left out of the average",
      rep2["baseline_days"] == kept,
      f"baseline_days={rep2['baseline_days']}, stored={rep2['baseline_stored_days']}")
check("each dropped day is reported with its own reason",
      sorted(rep2["baseline_excluded"].values()) == [1, 1, 1]
      and len(rep2["baseline_excluded"]) == 3, rep2["baseline_excluded"])
check("the stored count still reports what the store holds",
      rep2["baseline_stored_days"] == rep["baseline_stored_days"] + 3,
      f"{rep2['baseline_stored_days']} vs {rep['baseline_stored_days']}")
check("an entirely unstamped store reports zero, not a fabricated average",
      asyncio.run(run({f"2026-07-{d:02d}": day(10, window=False)
                       for d in range(1, 29)}))["baseline_days"] == 0)


# ── 4. the top-writer comparison is made in the same window as the baseline ─
B = fp._baseline_top_writer_corr
hist = [day(600)]                     # this entity's own recorded peak: 600

# 06:00. 400 writes so far projects to 1600, over the 1200 gate, so the tag
# fires. Against the unprojected 400 it never would have - and that is the
# twenty hours the check used to be blind for.
check("an entity under its own gate after projection stays quiet",
      B("sensor.busy", 200, hist, 6.0) is None,
      "200 at 06:00 is 800 projected, still under 600x2")
tag = B("sensor.busy", 400, hist, 6.0)
check("an entity on a day's pace past its own peak IS reported", tag is not None,
      "the first twenty hours could never raise this tag")
if tag:
    check("the tag quotes the observed count, not the projection",
          tag["params"]["n"] == 400, tag["params"])
    check("and carries the projection it actually compared",
          tag["params"]["projected"] == 1600, tag["params"])
    check("the baseline peak it exceeded is still reported",
          tag["params"]["base"] == 600, tag["params"])

check("a genuinely ordinary entity stays quiet even projected",
      B("sensor.calm", 20, [day(600)], 6.0) is None)
check("a peak too small for a multiple to mean anything stays quiet",
      B("sensor.busy", 9000, [day(3)], 6.0) is None)
check("an entity below its own peak stays quiet at any hour",
      B("sensor.busy", 100, hist, 24.0) is None)
check("an entity never seen in the baseline is not given a peak of zero",
      B("sensor.new", 9000, hist, 6.0) is None)
check("the default argument keeps a whole-day caller unchanged",
      B("sensor.busy", 100, hist) is None and B("sensor.busy", 1300, hist) is not None)

# The projection is capped exactly like the totals', or a 00:05 sample claims a
# month of traffic.
check("the projection is capped at the same ceiling the totals use",
      fp._extrapolate_to_day(10, 0.08) == 10 * fp.MAX_EXTRAPOLATION_FACTOR,
      f"got {fp._extrapolate_to_day(10, 0.08)}")

# ── 5. the panel must not print a projection as though it were counted ─────
panel = (COMPONENT / "panel.html").read_text(encoding="utf-8")
check("the tag text asks for a projection and shows the observed count",
      "{projected}" in panel and "{n}" in panel)
check("the tag no longer claims a projected number was written today",
      "wrote {n} times today" not in panel,
      "it prints the projection as an observed count")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
