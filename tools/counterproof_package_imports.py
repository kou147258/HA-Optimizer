"""Counter-proof for test_package_imports.py: break the package's own relative
import and it must go red.

The point of the check is that a module can be broken for every other check -
each of those imports or execs one file in isolation - and still be fine here,
because this one loads the package the way Home Assistant does.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

CASES = [
    ("a relative import names a module that does not exist",
     "purge_engine.py", "from .const import", "from .const_gone import"),
    # The first draft's second case was "drop a relative import" - and it kept
    # passing, because the constants that import provides are only read inside
    # function bodies, so the module still loads. That is a bad test rather than
    # a bad check: it expected a break that is not one. This case is a real one,
    # and it is the class that actually bites on install.
    ("a module raises at import time",
     "write_measure.py", "", "raise RuntimeError('top-level failure')\n"),
    ("a data file the panel needs is missing from the package",
     None, None, None),          # handled specially below
]

missed = 0
for label, filename, old, new in CASES:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        shutil.copytree(ROOT / "custom_components", t / "custom_components")
        shutil.copytree(ROOT / "tools", t / "tools")
        for pc in (t / "custom_components").rglob("__pycache__"):
            shutil.rmtree(pc, ignore_errors=True)

        if filename is None:
            # translations/ is what made 1.7.1 uninstallable, so it is the
            # data file worth removing.
            shutil.rmtree(t / "custom_components" / "ha_optimizer" / "translations",
                          ignore_errors=True)
            signal = "translations/ is present"
        else:
            p = t / "custom_components" / "ha_optimizer" / filename
            text = p.read_bytes().decode("utf-8").replace("\r\n", "\n")
            if old and text.count(old) != 1:
                print(f"FAIL  {label} — the anchor appears {text.count(old)} times")
                missed += 1
                continue
            p.write_bytes((text.replace(old, new, 1) if old else text + new)
                          .replace("\n", "\r\n").encode("utf-8"))
            signal = "every module in the package imports"

        r = subprocess.run([sys.executable, str(t / "tools" / "test_package_imports.py"), str(t)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            print(f"FAIL  {label} — the check still passed")
            missed += 1
        elif signal not in out:
            print(f"FAIL  {label} — went red but never said why; expected {signal!r}")
            missed += 1
        else:
            print(f"ok    {label}")
            for ln in out.splitlines():
                if signal in ln:
                    print("      " + ln.strip()[:112])

print()
print("FAILED" if missed else "PASSED: all three package defects were caught")
sys.exit(1 if missed else 0)
