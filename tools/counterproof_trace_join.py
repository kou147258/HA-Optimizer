"""Counter-proof for test_trace_join.py: put the wrong key format back and the
check must go red. A guard nobody has seen fail is not known to work."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"C:\Users\43457\.minimax\sessions\mvs_beb6ae963fc14d89832b6a57e91f762d\workspace\HA-Optimizer")

CASES = [
    # the join uses the domain instead of the item id
    ("the join is put back on the wrong half of the key",
     "key.split(\".\", 1)[1] if \".\" in key else key",
     "key.split(\".\", 1)[0] if \".\" in key else key"),
    # the restore call is dropped
    ("the restore of saved traces is dropped",
     "        await async_restore_traces(hass)\n", ""),
    # and the code goes back to the private storage
    ("the private storage is read directly again",
     "from homeassistant.components.trace.util import (  # noqa: PLC0415\n"
     "            async_list_traces,\n            async_restore_traces,\n        )",
     "from homeassistant.components.trace.const import DATA_TRACE  # noqa: PLC0415"),
]

missed = 0
for label, old, new in CASES:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        shutil.copytree(ROOT / "custom_components", t / "custom_components")
        shutil.copytree(ROOT / "tools", t / "tools")
        for pc in (t / "custom_components").rglob("__pycache__"):
            shutil.rmtree(pc, ignore_errors=True)
        p = t / "custom_components" / "ha_optimizer" / "automation_runs.py"
        src = p.read_text(encoding="utf-8")
        if src.count(old) != 1:
            print(f"SKIP  {label}: anchor appears {src.count(old)} times")
            missed += 1
            continue
        p.write_text(src.replace(old, new, 1), encoding="utf-8", newline="")
        r = subprocess.run([sys.executable, str(t / "tools" / "test_trace_join.py"), str(t)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            missed += 1
            print(f"FAIL  {label} — the check still passed")
        else:
            reds = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
            print(f"ok    {label} -> {len(reds)} assertion(s) went red")

print()
print("FAILED" if missed else "PASSED: every injected defect was caught")
sys.exit(1 if missed else 0)
