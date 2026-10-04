"""Counter-proof for the shadowing check.

Three cases. The first is the defect that actually shipped: a loop variable
named `entry` shadowing the ConfigEntry parameter that the closure reads two
lines earlier, which took down every soft purge with a bare HTTP 500.

The second proves the check is not recognising the word "entry" - it finds a
shadow of an arbitrary name in an arbitrary place. The third is the control: a
function that assigns a name nothing outside it binds is NOT a finding, because
the component is full of ordinary locals and a guard that flags them all is a
guard nobody keeps.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
CHECK = "tools/test_python_shadowing.py"

# (label, file, old, new, the phrase the failure must contain)
CASES = [
    ("the shipped defect: a loop variable named `entry`",
     "custom_components/ha_optimizer/__init__.py",
     "                reg_entry = ent_reg.async_get(eid)\n"
     "                if reg_entry is not None and not reg_entry.disabled:",
     "                entry = ent_reg.async_get(eid)\n"
     "                if entry is not None and not entry.disabled:",
     "unbound local"),

    # Not the word "entry", not that file, not that function. Appended rather
    # than spliced so the case is about the rule and not about a lucky anchor.
    ("a shadow of any name, anywhere in the component",
     "custom_components/ha_optimizer/scanner.py",
     None,   # append
     "\n\ndef _shadow_probe(shared):\n"
     "    def _inner():\n"
     "        before = shared\n"
     "        shared = 1\n"
     "        return before\n"
     "    return _inner\n",
     "unbound local"),

    ("control: an ordinary local is not a finding", None, None, None, ""),
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
            if old is None:                      # append
                text += new
            else:
                n = text.count(old)
                if n != 1:
                    print(f"FAIL  {label} — the anchor appears {n} times, so "
                          f"this case proves nothing")
                    missed += 1
                    continue
                text = text.replace(old, new, 1)
            p.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

        r = subprocess.run([sys.executable, str(t / CHECK), str(t)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        out = r.stdout + r.stderr
        expect_red = target is not None
        red = r.returncode != 0

        if "Traceback" in out or "SyntaxError" in out:
            print(f"FAIL  {label} — the check crashed instead of reporting")
            print("      " + out.strip().splitlines()[-1][:150])
            missed += 1
        elif red != expect_red:
            print(f"FAIL  {label} — expected {'red' if expect_red else 'green'}, "
                  f"got the other (exit {r.returncode})")
            missed += 1
        elif expect_red and signal not in out:
            print(f"FAIL  {label} — went red but never said why; expected "
                  f"{signal!r} to appear among the findings")
            missed += 1
        else:
            print(f"ok    {label}")
            for ln in out.splitlines():
                if signal and signal in ln and ln.strip().startswith("FAIL"):
                    print("      " + ln.strip()[:170])

print()
print("FAILED" if missed
      else "PASSED: the shadow is caught, anywhere; an ordinary local is not")
sys.exit(1 if missed else 0)
