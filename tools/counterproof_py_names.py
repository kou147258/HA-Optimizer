"""Counter-proof for test_py_names.py: the check must go red on a name nothing
defines. A guard nobody has seen fail is not known to work.

The first case is not invented: it is the exact defect 1.7.22 shipped, which
answered HTTP 500 on every load of the automations tab.

Each case names the signal it expects, and a traceback counts as a failure.
That distinction is the point. An earlier version of this file only watched the
exit code, and the check turned out to raise TypeError on its own reporting
path - so on a genuinely broken tree it exited non-zero and printed no report,
which the counter-proof read as "caught it".
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

# (label, relative file, old, new, signal that must appear in the output)
CASES = [
    ("the call to a helper that was never defined is back (1.7.22's bug)",
     "automation_runs.py",
     "async def _ensure_trace_component(hass) -> tuple[str, str]:",
     "async def _ensure_trace_component_RENAMED(hass) -> tuple[str, str]:",
     "'_ensure_trace_component' is not defined anywhere"),

    ("a method calls a name the module does not define",
     "automation_runs.py",
     "        state, reason = await _ensure_trace_component(self.hass)",
     "        state, reason = await _ensure_trace_componentX(self.hass)",
     "'_ensure_trace_componentX' is not defined anywhere"),

    ("a dict comprehension references a name nothing defines",
     "write_measure.py",
     '    return {\n        r[0]: {"writes"',
     '    return {\n        _undefined_key(r[0]): {"writes"',
     "'_undefined_key' is not defined anywhere"),

    ("a module hides its names behind a star import",
     "write_measure.py",
     "from typing import Any\n",
     "from typing import Any\nfrom os.path import *\n",
     "star import"),
]

missed = 0
for label, filename, old, new, signal in CASES:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        shutil.copytree(ROOT / "custom_components", t / "custom_components")
        shutil.copytree(ROOT / "tools", t / "tools")
        for pc in (t / "custom_components").rglob("__pycache__"):
            shutil.rmtree(pc, ignore_errors=True)

        target = t / "custom_components" / "ha_optimizer" / filename
        src = target.read_text(encoding="utf-8")
        if src.count(old) != 1:
            print(f"FAIL  {label} — anchor appears {src.count(old)} times, "
                  f"so the case tested nothing")
            missed += 1
            continue
        target.write_text(src.replace(old, new, 1), encoding="utf-8", newline="")

        r = subprocess.run([sys.executable, str(t / "tools" / "test_py_names.py"), str(t)],
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
                if signal.split(" is not")[0] in ln and "FAIL" in ln:
                    print("      " + ln.strip())

print()
print("FAILED" if missed else "PASSED: every injected defect was caught, and said so")
sys.exit(1 if missed else 0)
