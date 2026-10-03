"""Counter-proof for test_check_tools.py: a second, early summary block in a
tool must go red.

This is the defect it was written for. `test_trace_join.py` had exactly that:
a summary-and-exit pair in the middle of the file, so thirteen assertions after
it never ran while the file printed 35/35 and green.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

# The early block that really was there, inserted above the real one.
EARLY = (
    "ok = sum(1 for r in results if r[0])\n"
    "print()\n"
    'print(f"{ok}/{len(results)} passed")\n'
    'print("FAILED" if ok != len(results) else "PASSED")\n'
    "sys.exit(0 if ok == len(results) else 1)\n"
)

CASES = [
    ("a tool ends the run before its own assertions",
     "tools/test_trace_join.py", EARLY, "tools\\test_trace_join.py has no unreachable tail"),

    ("a tool exits twice",
     "tools/test_py_names.py", "sys.exit(0 if ok == len(results) else 1)\n",
     "tools\\test_py_names.py exits exactly once"),
]

missed = 0
for label, target, inject, signal in CASES:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        shutil.copytree(ROOT / "custom_components", t / "custom_components")
        shutil.copytree(ROOT / "tools", t / "tools")
        for pc in (t / "custom_components").rglob("__pycache__"):
            shutil.rmtree(pc, ignore_errors=True)

        p = t / target
        src = p.read_text(encoding="utf-8")
        if src.count(inject) != 1:
            print(f"FAIL  {label} — anchor appears {src.count(inject)} times, "
                  f"so the case tested nothing")
            missed += 1
            continue
        p.write_text(src.replace(inject, inject + inject, 1), encoding="utf-8", newline="")

        r = subprocess.run([sys.executable, str(t / "tools" / "test_check_tools.py"), str(t)],
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
                if signal in ln and "FAIL" in ln:
                    print("      " + ln.strip())

print()
print("FAILED" if missed else "PASSED: the unreachable tail was caught")
sys.exit(1 if missed else 0)
