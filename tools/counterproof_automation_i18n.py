"""Counter-proofs for the diagnosis translation guard.

Two ways the change can look done and not be:

  1. the rules go back to carrying English prose. The panel would then render
     English for a Chinese user again, and the guard has to say so by name
     rather than by counting;
  2. a key is spelled differently in the backend than in the panel. Nothing at
     run time would complain - `t()` falls back to returning the key itself -
     so the text would simply be a bare `autoDiag_missing_key` on the page. The
     guard has to notice that one language is missing a definition.

Plus a control: the real error text for every rule classifies as that rule, so
a check that passes by matching nothing is caught.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
CHECK = "tools/test_automation_i18n.py"
RUNS = "custom_components/ha_optimizer/automation_runs.py"
PANEL = "custom_components/ha_optimizer/panel.html"

KEY_LINE = '        "key": "autoDiag_missing_key",\n'

CASES = [
    ("the rules carry English prose again",
     [(RUNS, KEY_LINE,
       '        "suggestion": "An action called a service with a data payload.",\n')],
     "no rule carries user-facing prose any more"),

    ("a key is defined in one language and not the other",
     [(PANEL,
       "    autoDiag_timeout: 'The run exceeded its time limit. Usually a "
       "wait_template loop or a service that is slow to answer; check the step\\'s "
       "duration in the trace timeline.',\n", "")],
     "is defined in both languages"),

    ("control: every rule still matches its own error", None, ""),
]

missed = 0
for label, patches, signal in CASES:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        shutil.copytree(ROOT / "custom_components", t / "custom_components")
        shutil.copytree(ROOT / "tools", t / "tools")
        for sub in ("custom_components", "tools"):
            for pc in (t / sub).rglob("__pycache__"):
                shutil.rmtree(pc, ignore_errors=True)

        if patches:
            for rel, old, new in patches:
                p = t / rel
                text = p.read_bytes().decode("utf-8").replace("\r\n", "\n")
                n = text.count(old)
                if n != 1:
                    print(f"FAIL  {label} — the anchor appears {n} times, so this "
                          f"case proves nothing")
                    missed += 1
                    break
                p.write_bytes(text.replace(old, new, 1).replace("\n", "\r\n").encode("utf-8"))
            else:
                r = subprocess.run([sys.executable, str(t / CHECK), str(t)],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace")
                out = r.stdout + r.stderr
                if "Traceback" in out:
                    print(f"FAIL  {label} — the check crashed instead of reporting")
                    print("      " + out.strip().splitlines()[-1][:150])
                    missed += 1
                elif r.returncode == 0:
                    print(f"FAIL  {label} — the check stayed green")
                    missed += 1
                elif signal not in out:
                    print(f"FAIL  {label} — went red but never said why; expected "
                          f"{signal!r}")
                    missed += 1
                else:
                    print(f"ok    {label}")
                    for ln in out.splitlines():
                        if signal in ln and ln.strip().startswith("FAIL"):
                            print("      " + ln.strip()[:150])
            continue

        r = subprocess.run([sys.executable, str(t / CHECK), str(t)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if r.returncode != 0:
            print(f"FAIL  {label} — the unmutated tree is already red")
            missed += 1
        else:
            print(f"ok    {label}")

print()
print("FAILED" if missed else "PASSED: prose and a one-sided key are both caught")
sys.exit(1 if missed else 0)
