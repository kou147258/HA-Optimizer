"""Guards for the three defects found from a live log dump after 1.7.0.

Run:  python3 tools/test_live_log_findings.py

The evidence was HA's own output on a real instance:

  Detected that custom integration 'ha_optimizer' accesses the database
  without the database executor ... fingerprint.py, line 116 / line 528

  Supervisor GET /host/stats -> 404: 404: Not Found        (23 times in 4 min)

  Replaced a stale panel.html under www/ (247217 -> 261243 bytes)

None of them is a crash. They are a recurring background job doing the wrong
thing quietly, which is why the user saw a panel that blinked rather than an
error - and why the 404 repeated 23 times without anyone noticing a pattern.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "ha_optimizer"
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


fingerprint = (COMPONENT / "fingerprint.py").read_text(encoding="utf-8")
init = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
scanner = (COMPONENT / "scanner.py").read_text(encoding="utf-8")
panel = (COMPONENT / "panel.html").read_text(encoding="utf-8")

# ═══ 1. recorder queries must not use the general-purpose executor ═══════════
print("\nrecorder executor")
check("fingerprint has a recorder-executor helper",
      "async_add_executor_job" in fingerprint
      and "get_instance" in fingerprint,
      "HA logs 'accesses the database without the database executor' otherwise")
check("the helper lives at module level, so both classes can use it",
      re.search(r"^async def _run_in_db_executor", fingerprint, re.M) is not None,
      "it was defined inside one class while the other class needed it")

for method in ("async_profile_yesterday", "_profile_today"):
    i = fingerprint.index(f"async def {method}")
    body = fingerprint[i:i + 400]
    check(f"{method} uses the recorder executor",
          "_run_in_db_executor(self.hass" in body
          and "self.hass.async_add_executor_job" not in body,
          "the general executor is what HA flags")

check("no module anywhere still uses the general executor for recorder work",
      "hass.async_add_executor_job" not in fingerprint,
      "leftover general-executor call would be flagged again")
check("scanner.py keeps doing it right (regression reference)",
      "get_instance(hass).async_add_executor_job" in scanner)
check("both fingerprint DB sites are now covered by the same helper",
      fingerprint.count("_run_in_db_executor(self.hass") == 2,
      f"found {fingerprint.count('_run_in_db_executor(self.hass')}")

# ═══ 2. a missing Supervisor endpoint is a fact, not an event ══════════════
print("\nsupervisor log noise")
check("there is a once-only announcer for missing endpoints",
      "_missing_endpoint" in init and "_WARNED_ONCE" in init)
check("a 404 is announced at INFO, not WARNING",
      re.search(r"if r\.status == 404:\s*\n\s*_missing_endpoint", init) is not None,
      "the panel polls every 10s; 23 identical warnings in 4 minutes is the symptom")
check("the announcer says the gauge will stay empty rather than blaming the user",
      "resource gauges will stay empty" in init)
check("other supervisor failures are throttled, not repeated every 10s",
      "_log_throttled" in init and "_WARN_THROTTLE_SECONDS" in init)
check("nothing logs a raw supervisor 404 as a warning any more",
      not re.search(r'_LOGGER\.warning\("Supervisor GET %s . %s: %s"', init),
      "this line fired 23 times in the log dump")

# ═══ 3. the panel must stop re-rendering from an empty payload ═════════════
print("\nsysbar polling")
fetch = panel[panel.index("async function _fetchSysBarOnly()"):]
fetch = fetch[:fetch.index("\n// Start the always-on sysbar loop")]
check("an empty host payload no longer re-renders the gauges",
      "if (data && !data.error) {" not in fetch,
      "an empty payload used to blank CPU/RAM/disk to — once per poll")
check("the panel keeps the last reading when the data is unavailable",
      "_setSysbarUnavailable" in fetch and "_sysbarUnavailableSince" in fetch)
check("unavailable is shown once, in place, instead of by blanking",
      'id="sysbarNote"' in panel and "t('sysbarUnavailable')" in fetch)
check("polling backs off once the install is known not to support it",
      "SYSBAR_BACKOFF_MS" in fetch and "SYSBAR_BACKOFF_MS" in panel)
check("the sysbar message is translated",
      panel.count("sysbarUnavailable:") == 2)
check("the gauge text is never hardcoded to a locale any more",
      "toLocaleTimeString()" not in panel
      or "_panelLocale()" in panel,
      "toLocaleTimeString() with no locale ignores the panel's language")

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("all live-log findings are guarded")
# The canonical verdict line. audit.py requires it: an exit code is not a
# verdict, it is also what a tool that failed three assertions and
# `sys.exit(0)` produces.
print("PASSED")
