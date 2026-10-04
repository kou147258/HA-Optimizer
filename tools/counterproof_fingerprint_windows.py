"""Counter-proofs for the two fingerprint window defects.

Both of these fixes are about comparing numbers measured over different windows,
and both of them are easy to write a check for that passes anyway. So each case
puts one real defect back and requires this suite to go red AND say which
assertion noticed.

  1. the top-writer comparison against the raw morning figure - the state it
     shipped in, where an entity had to exceed twice a whole day before the tag
     appeared, so the first twenty hours could never raise it;
  2. every stored day treated as comparable, whatever window measured it;
  3. the panel printing the projection as though it had been counted.

Case 4 is the control. A suite whose cases all pass when the defect is present
has proved nothing, so the unmutated tree has to stay green for "red" to mean
anything.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
FP = "custom_components/ha_optimizer/fingerprint.py"
PANEL = "custom_components/ha_optimizer/panel.html"

# (label, file, anchor, replacement, the assertion that must notice)
CASES = [
    ("the top-writer comparison is made on the raw morning figure",
     FP,
     "    if projected < base_max * TOP_WRITER_BASELINE_FACTOR:\n",
     "    if today_writes < base_max * TOP_WRITER_BASELINE_FACTOR:\n",
     "an entity on a day's pace past its own peak IS reported"),

    ("a stored day is compared whatever window measured it",
     FP,
     '    window = day.get("window")\n',
     '    return True, ""\n    window = day.get("window")\n',
     "a day with no window at all is excluded"),

    ("the panel prints the projection as though it were counted",
     PANEL,
     "is on pace for {projected} writes today",
     "wrote {n} times today",
     "the tag no longer claims a projected number was written today"),

    ("control: the unmutated tree", None, None, None, ""),
]

missed = 0
for label, target, old, new, signal in CASES:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        shutil.copytree(ROOT / "custom_components", t / "custom_components")
        shutil.copytree(ROOT / "tools", t / "tools")
        for sub in ("custom_components", "tools"):
            for pc in (t / sub).rglob("__pycache__"):
                shutil.rmtree(pc, ignore_errors=True)

        if target is not None:
            p = t / target
            text = p.read_bytes().decode("utf-8").replace("\r\n", "\n")
            n = text.count(old)
            if n != 1:
                # A patch that does not apply is a case that tests nothing. Say
                # so instead of reporting a pass.
                print(f"FAIL  {label} — the anchor appears {n} times, so this "
                      f"case proves nothing")
                missed += 1
                continue
            p.write_bytes(text.replace(old, new, 1).replace("\n", "\r\n").encode("utf-8"))

        r = subprocess.run([sys.executable, str(t / "tools" / "test_fingerprint_windows.py"),
                            str(t)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        expect_red = target is not None
        red = "FAILED" in out and f"{r.returncode}" != "0"

        if "Traceback" in out:
            print(f"FAIL  {label} — the check crashed instead of reporting")
            print("      " + out.strip().splitlines()[-1][:150])
            missed += 1
        elif red != expect_red:
            print(f"FAIL  {label} — the check was expected to go "
                  f"{'red' if expect_red else 'green'}, and it did not")
            missed += 1
        elif expect_red and signal not in out:
            print(f"FAIL  {label} — went red but never said why; expected "
                  f"{signal!r} to appear among the failures")
            missed += 1
        else:
            print(f"ok    {label}")
            for ln in out.splitlines():
                if signal and signal in ln and ln.strip().startswith("FAIL"):
                    print("      " + ln.strip()[:150])

print()
print("FAILED" if missed else "PASSED: every injected defect was caught, and named")
sys.exit(1 if missed else 0)
