"""Counter-proof for test_panel_renders.py.

The bug it exists for: `partialDay is not defined`, which shipped in 1.7.30 and
blanked the fingerprint tab. `node --check` parses it perfectly, and so does
every other check in tools/ - the previous version of the same mistake, `today is
not defined`, had already shipped and was found by a person running the code.

So the cases put those exact defects back and require this check to go red AND
name them. Naming matters: a check that goes red without saying which identifier
is out of scope is one step from being ignored.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

# What 1.7.30 shipped: the call sites pass `partialDay` and nothing in that
# scope ever declares it.
DECL = ("  const partialDay = (today.partial !== null && today.partial !== undefined)\n"
        "    ? !!today.partial\n"
        "    : hoursElapsed < 23;\n")

# The guard inside _fpMetricRow, and the signature, so the mutation reproduces
# the original defect rather than a duplicate declaration: the parameter has to
# go too, or the same name is declared twice and it is a SyntaxError instead.
GUARD = "  if (partialDay === undefined || partialDay === null) {"
SIGNATURE = "function _fpMetricRow(label, extrapolated, raw, unit, hoursElapsed, partialDay) {"

CASES = [
    ("1.7.30 as shipped: partialDay is never declared",
     lambda s: s.replace(DECL, ""),
     "partialDay is not defined"),
    ("the earlier one: _fpMetricRow reading `today` from its own scope",
     lambda s: s.replace(
         SIGNATURE,
         "function _fpMetricRow(label, extrapolated, raw, unit, hoursElapsed) {\n"
         "  const partialDay = today.partial;", 1),
     "today is not defined"),
]

missed = 0
for label, mutate, signal in CASES:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        shutil.copytree(ROOT / "custom_components", t / "custom_components")
        shutil.copytree(ROOT / "tools", t / "tools")
        p = t / "custom_components" / "ha_optimizer" / "panel.html"
        # panel.html is CRLF and the anchors above use \n, so the comparison
        # runs on normalised text and the write restores CRLF. Reading it the
        # other way round reports "anchor appears 0 times" and tests nothing.
        text = p.read_bytes().decode("utf-8").replace("\r\n", "\n")
        changed = mutate(text)
        if changed == text:
            print(f"FAIL  {label} — the mutation changed nothing, so it tested nothing")
            missed += 1
            continue
        p.write_bytes(changed.replace("\n", "\r\n").encode("utf-8"))
        r = subprocess.run([sys.executable, str(t / "tools" / "test_panel_renders.py"), str(t)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        if "Traceback" in out and signal not in out:
            print(f"FAIL  {label} — the check itself crashed")
            print("      " + out.strip().splitlines()[-1][:120])
            missed += 1
        elif r.returncode == 0:
            print(f"FAIL  {label} — the check still passed")
            missed += 1
        elif signal not in out:
            print(f"FAIL  {label} — went red but never named it; expected {signal!r}")
            missed += 1
        else:
            print(f"ok    {label}")
            for ln in out.splitlines():
                if signal in ln:
                    print("      " + ln.strip()[:120])

print()
print("FAILED" if missed else "PASSED: both ReferenceErrors are caught and named")
sys.exit(1 if missed else 0)
