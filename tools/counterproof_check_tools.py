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

    # A second bare exit with nothing after it is harmless - there is no
    # unreachable code - so the rule that matters is the tail. This case has the
    # real defect's shape: an early exit with the assertions still to come.
    ("a tool ends the run before its own assertions (a second file)",
     "tools/test_py_names.py",
     'check("the component has python sources to check", bool(files)',
     'sys.exit(0)\ncheck("the component has python sources to check", bool(files)',
     "test_py_names.py has no unreachable tail"),

    # The same defect, spelled the two ways the shape checker used to miss.
    # `raise SystemExit` is a Raise, not a Call, so the old `is_exit` could not
    # see it; an exit inside `if __name__ == "__main__":` is not a bare
    # top-level statement, so the old index lookup could not see that either.
    # Both leave thirteen-assertions-worth of dead code reported as green.
    ("a tool ends the run with `raise SystemExit`",
     "tools/test_store_records.py",
     'check("a normal window still expires the old one",',
     'raise SystemExit(0)\ncheck("a normal window still expires the old one",',
     "test_store_records.py has no unreachable tail"),

    ("a tool ends the run inside the __main__ guard",
     "tools/test_store_records.py",
     'check("a normal window still expires the old one",',
     'if __name__ == "__main__":\n    sys.exit(0)\n'
     'check("a normal window still expires the old one",',
     "test_store_records.py has no unreachable tail"),

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
