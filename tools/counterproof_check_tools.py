"""Counter-proofs for the two guards added after a guard turned out to be dead.

`test_check_tools.py` exists because `test_trace_join.py` carried two
summary-and-exit blocks: the first sat in the middle of the file, so thirteen
assertions after it never ran while it printed 35/35 and green. A skipped
assertion is indistinguishable from a passing one to every reader of the output,
so nothing about the checks themselves would ever have said so.

The third case is the same shape one level up, in the release path: a second read
of the title with an encoding that undoes the first read's BOM stripping. It is
not a tool-shape problem and `test_check_tools` cannot see it, so it is checked
by running the release - which is why this file picks which checker to ask
rather than always asking the same one.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

# (label, file, anchor, replacement, the signal that must appear)
CASES = [
    ("a tool ends the run before its own assertions",
     "tools/test_trace_join.py",
     'check("coverage lists the bucket keys, so an empty page can be argued with",',
     'sys.exit(0)\ncheck("coverage lists the bucket keys, so an empty page can be argued with",',
     "test_trace_join.py has no unreachable tail"),

    ("a tool exits twice",
     "tools/test_py_names.py",
     "sys.exit(0 if ok == len(results) else 1)\n",
     "sys.exit(0 if ok == len(results) else 1)\n"
     "sys.exit(0 if ok == len(results) else 1)\n",
     "test_py_names.py exits exactly once"),

    ("the release reads the title again without stripping the BOM",
     "tools/audit.py",
     '    text = title.read_text(encoding="utf-8-sig").strip()\n',
     '    text = title.read_text(encoding="utf-8-sig").strip()\n'
     '    text = title.read_text(encoding="utf-8").strip()\n',
     "no BOM reaches anything the release printed"),
]

missed = 0
for label, target, old, new, signal in CASES:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        shutil.copytree(ROOT / "custom_components", t / "custom_components")
        shutil.copytree(ROOT / "tools", t / "tools")
        for pc in (t / "custom_components").rglob("__pycache__"):
            shutil.rmtree(pc, ignore_errors=True)

        p = t / target
        src = p.read_text(encoding="utf-8")
        if src.count(old) != 1:
            print(f"FAIL  {label} — anchor appears {src.count(old)} times, "
                  f"so the case tested nothing")
            missed += 1
            continue
        p.write_text(src.replace(old, new, 1), encoding="utf-8", newline="")

        # The release case is not about tool shape; it dies inside audit.py's
        # own dry run, which the shape checker cannot observe.
        checker = "test_release_text.py" if target == "tools/audit.py" \
            else "test_check_tools.py"
        r = subprocess.run([sys.executable, str(t / "tools" / checker), str(t)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        out = r.stdout + r.stderr
        if "Traceback" in out:
            # The counter-proof itself broke - most likely the injected defect
            # stopped the checker from starting. Say so rather than counting it.
            print(f"FAIL  {label} — the checker crashed instead of reporting")
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
print("FAILED" if missed else "PASSED: every injected defect was caught, and named")
sys.exit(1 if missed else 0)
