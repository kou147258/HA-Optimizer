"""Counter-proof for test_recorder_sql.py: put a column back that the table does
not have, and the check must go red and name it.

Two of these are the exact 1.7.22 defect. The third is the shape that matters
more than either: a plausible typo of a column that really exists, which is what
a schema check is actually for. A guard that only catches `old_state` proves it
remembers one string.

The coverage assertion is not re-tested here because it has already been seen to
fail on a real tree: the first draft expected `states_meta`, the check went red,
and it was right - nothing in the component reads that table.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
TARGET = "write_measure.py"

CASES = [
    ("old_state goes back into the query (1.7.22's defect)",
     "COUNT(DISTINCT state) AS distinct_states",
     "COUNT(DISTINCT old_state) AS distinct_states",
     "'old_state' not a column of ['states']"),

    ("new_state goes back into the query (1.7.22's other defect)",
     "COUNT(DISTINCT state) AS distinct_states",
     "COUNT(DISTINCT state || new_state) AS distinct_states",
     "'new_state' not a column of ['states']"),

    ("a real column gets a plausible typo",
     "WHERE last_updated_ts >= {ts_expr}",
     "WHERE last_updated_timestamp >= {ts_expr}",
     "'last_updated_timestamp' not a column of ['states']"),
]

missed = 0
for label, old, new, signal in CASES:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        shutil.copytree(ROOT / "custom_components", t / "custom_components")
        shutil.copytree(ROOT / "tools", t / "tools")
        for pc in (t / "custom_components").rglob("__pycache__"):
            shutil.rmtree(pc, ignore_errors=True)

        target = t / "custom_components" / "ha_optimizer" / TARGET
        src = target.read_text(encoding="utf-8")
        if src.count(old) != 1:
            print(f"FAIL  {label} — anchor appears {src.count(old)} times, "
                  f"so the case tested nothing")
            missed += 1
            continue
        target.write_text(src.replace(old, new, 1), encoding="utf-8", newline="")

        r = subprocess.run([sys.executable, str(t / "tools" / "test_recorder_sql.py"), str(t)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = r.stdout + r.stderr
        if "Traceback" in out:
            print(f"FAIL  {label} — the check crashed instead of reporting")
            print("      " + out.strip().splitlines()[-1])
            missed += 1
        elif r.returncode == 0:
            print(f"FAIL  {label} — the check still passed")
            missed += 1
        elif signal not in out:
            print(f"FAIL  {label} — went red but never said why; expected {signal!r}")
            missed += 1
        else:
            print(f"ok    {label}")
            for ln in out.splitlines():
                if signal.split(" not a column")[0] in ln and "FAIL" in ln:
                    print("      " + ln.strip())

print()
print("FAILED" if missed else "PASSED: every injected defect was caught, and named")
sys.exit(1 if missed else 0)
