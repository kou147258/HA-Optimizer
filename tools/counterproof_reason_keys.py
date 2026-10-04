"""Counter-proofs for the reason-key rule.

  1. the exact shape that shipped - an f-string spelling the sentence out;
  2. a plain string that is not a key at all;
  3. a dict whose key is not in the table;
  4. a control.

The first is the one that matters: it is an f-string, and the earlier form of
this rule was a grep for reason keys, which an f-string walks straight past
while looking for a literal that is not there.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
CHECK = "tools/test_reason_keys.py"
SCANNER = "custom_components/ha_optimizer/scanner.py"

CASES = [
    ("a reason spelled out as an f-string again",
     '                reasons.append({"key": "reason_suspicious_name",\n'
     '                                "params": {"pattern": pattern}})',
     '                reasons.append(f"Suspicious name: \'{pattern}\'")',
     "appends a computed value"),
    ("a reason that is a string and not a key",
     '        reasons.append("reason_orphaned")',
     '        reasons.append("The config entry no longer exists")',
     "is not a key in every language table"),
    ("a reason whose key the panel has never heard of",
     '                reasons.append({"key": "reason_suspicious_name",\n'
     '                                "params": {"pattern": pattern}})',
     '                reasons.append({"key": "reason_something_invented",\n'
     '                                "params": {"pattern": pattern}})',
     "is not in every language table"),
    ("control: the unmutated tree", None, None, ""),
]

missed = 0
for label, old, new, signal in CASES:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        shutil.copytree(ROOT / "custom_components", t / "custom_components")
        shutil.copytree(ROOT / "tools", t / "tools")
        for sub in ("custom_components", "tools"):
            for pc in (t / sub).rglob("__pycache__"):
                shutil.rmtree(pc, ignore_errors=True)
        if old is not None:
            p = t / SCANNER
            text = p.read_bytes().decode("utf-8").replace("\r\n", "\n")
            n = text.count(old)
            if n != 1:
                print(f"FAIL  {label} — the anchor appears {n} times, so this case "
                      f"proves nothing")
                missed += 1
                continue
            p.write_bytes(text.replace(old, new, 1).replace("\n", "\r\n").encode("utf-8"))

        r = subprocess.run([sys.executable, str(t / CHECK), str(t)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        out = r.stdout + r.stderr
        expect_red = old is not None
        red = r.returncode != 0
        if "Traceback" in out:
            print(f"FAIL  {label} — the check crashed instead of reporting")
            print("      " + out.strip().splitlines()[-1][:140])
            missed += 1
        elif red != expect_red:
            print(f"FAIL  {label} — expected {'red' if expect_red else 'green'}")
            missed += 1
        elif expect_red and signal not in out:
            print(f"FAIL  {label} — went red but not because of this case; "
                  f"{signal!r} never appeared")
            missed += 1
        else:
            print(f"ok    {label}")
            for ln in out.splitlines():
                if signal and signal in ln and ln.strip().startswith("FAIL"):
                    print("      " + ln.strip()[:150])

print()
print("FAILED" if missed else
      "PASSED: a spelled-out reason, a non-key and an invented key are all caught")
sys.exit(1 if missed else 0)
