"""Counter-proof for test_scan_contract.py: drop the key again and it must go red.

This is the real defect, verbatim: the panel read `write_rollup` from 1.7.11 and
the backend computed the numbers and threw them away.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

DROP = b'            "write_rollup": write_rollup["by_area"],\r\n'
SIGNAL = "every key the panel reads is emitted by the scan"

missed = 0
with tempfile.TemporaryDirectory() as td:
    t = Path(td)
    shutil.copytree(ROOT / "custom_components", t / "custom_components")
    shutil.copytree(ROOT / "tools", t / "tools")
    scanner = t / "custom_components" / "ha_optimizer" / "scanner.py"
    raw = scanner.read_bytes()
    if raw.count(DROP) != 1:
        print(f"FAIL  anchor appears {raw.count(DROP)} times, so the case tested nothing")
        missed += 1
    else:
        scanner.write_bytes(raw.replace(DROP, b"", 1))
        r = subprocess.run([sys.executable, str(t / "tools" / "test_scan_contract.py"), str(t)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = r.stdout + r.stderr
        if "Traceback" in out:
            print("FAIL  the check crashed instead of reporting")
            missed += 1
        elif r.returncode == 0:
            print("FAIL  the check still passed with the key removed")
            missed += 1
        elif SIGNAL not in out:
            print(f"FAIL  went red but never said why; expected {SIGNAL!r}")
            missed += 1
        else:
            print("ok    the dropped key is caught")
            for ln in out.splitlines():
                if "write_rollup" in ln:
                    print("      " + ln.strip()[:108])

print()
print("FAILED" if missed else "PASSED: the missing key was caught")
sys.exit(1 if missed else 0)
