"""Counter-proof for test_i18n_param_shapes.py.

Two directions, and they need two different injections:

- a key defined in BOTH languages, changed in one, makes the two languages
  disagree about its shape. The check must say so - a single dict keyed by
  name keeps only the second definition, which is how the first version of this
  check was blind to the very defect it was written for.
- a key whose shape is wrong in BOTH languages still renders wrong, and only
  comparing the call site against the definition catches it.

The second case edits two lines, because editing one would only ever produce the
first finding.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

EN = "    writesAmp: (p) => `${p.n} writes per distinct value`,\n"
EN_SCALAR = "    writesAmp: (p) => `${p} writes per distinct value`,\n"
ZH_MARK = "writesAmp: (p) => `每个不同取值写了 ${p.n}"

# (label, [(old, new, expected_count)], signal)
CASES = [
    ("one language changes shape, so the two disagree",
     [(EN, "    writesAmp: (h) => `${h} writes per distinct value`,\n", 1)],
     "writesAmp: the two languages define it with different shapes"),

    ("both languages take a scalar while the call passes an object",
     [(EN, EN_SCALAR, 1), (ZH_MARK, ZH_MARK.replace("${p.n}", "${p}"), 1)],
     "writesAmp: the body needs scalar, but the call passes object"),
]

missed = 0
with tempfile.TemporaryDirectory() as td:
    t = Path(td)
    shutil.copytree(ROOT / "custom_components", t / "custom_components")
    shutil.copytree(ROOT / "tools", t / "tools")
    panel = t / "custom_components" / "ha_optimizer" / "panel.html"
    # Normalised to LF: panel.html is CRLF, and an anchor written with a bare
    # \n matches nothing in it. The first draft of this counter-proof reported
    # "anchor appears 0 times" twice for exactly that reason, which is what an
    # anchor assertion is for.
    pristine = panel.read_bytes().decode("utf-8").replace("\r\n", "\n")

    for label, edits, signal in CASES:
        text = pristine
        bad = False
        for old, new, count in edits:
            if text.count(old) != count:
                print(f"FAIL  {label} — anchor appears {text.count(old)} times, "
                      f"expected {count}, so the case tested nothing")
                bad = True
                break
            text = text.replace(old, new, count)
        if bad:
            missed += 1
            continue
        panel.write_bytes(text.encode("utf-8"))
        r = subprocess.run(
            [sys.executable, str(t / "tools" / "test_i18n_param_shapes.py"), str(t)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = r.stdout + r.stderr
        if "Traceback" in out:
            print(f"FAIL  {label} — the check crashed instead of reporting")
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
                if ln.strip().startswith("FAIL") and "writesAmp" in ln:
                    print("      " + ln.strip()[:112])
        panel.write_bytes(pristine.encode("utf-8"))

print()
print("FAILED" if missed else "PASSED: both shape errors were caught and named")
sys.exit(1 if missed else 0)
