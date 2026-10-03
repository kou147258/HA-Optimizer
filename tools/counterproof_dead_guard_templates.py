"""Counter-proof for the template-literal half of test_dead_guard.py.

The stripper once blanked a whole template literal, `${ }` included - and this
panel builds nearly all of its DOM inside `${ }`, so the check could not see the
region it most needed to see. A bare undefined call placed in an interpolation
went unseen. The stripper now keeps the interpolations and blanks their text,
which introduced its own defect: `${a()}${b()}` emitted `a()b()` and the check
reported `sevIconescapeHtml`, two identifiers glued together.

Both are checked here, and so is the case that must NOT be reported: a name
inside a plain string literal is text, not a call.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

# panel.html is CRLF in the worktree, and the anchor has to be unique: the first
# draft of this probe anchored on a line that appears four times, so every case
# was skipped and the probe reported success having tested nothing.
ANCHOR = "  ids.forEach(id => selectedIds.add(id));\r\n"

# (label, injected line, signal that must appear, must_be_seen)
CASES = [
    ("a bare call in plain code",
     "  somethingMissingInCode();\r\n", "somethingMissingInCode", True),
    ("a bare call inside the interpolation of a template literal",
     "  html += `${somethingMissingInTemplate()}`;\r\n", "somethingMissingInTemplate", True),
    ("two adjacent interpolations are not glued into one name",
     "  html += `${alpha()}${beta()}`;\r\n", "alphabeta", False),
    ("a name inside a plain string literal is text, not a call",
     "  const s = 'literallyMissingFn()';\r\n", "literallyMissingFn", False),
]

missed = 0
with tempfile.TemporaryDirectory() as td:
    t = Path(td)
    shutil.copytree(ROOT / "custom_components", t / "custom_components")
    shutil.copytree(ROOT / "tools", t / "tools")
    for pc in (t / "custom_components").rglob("__pycache__"):
        shutil.rmtree(pc, ignore_errors=True)
    panel = t / "custom_components" / "ha_optimizer" / "panel.html"
    pristine = panel.read_bytes().decode("utf-8")

    if pristine.count(ANCHOR) != 1:
        print(f"ABORT: the anchor appears {pristine.count(ANCHOR)} times, "
              f"so every case below would be skipped")
        sys.exit(1)

    for label, injected, signal, want_seen in CASES:
        # Inserted after a known line INSIDE the script block, not appended:
        # the end of panel.html is past </script>, and the check only ever reads
        # what is inside one. Appending there reported "not seen" for every
        # case and the probe would have passed for the wrong reason.
        if pristine.count(ANCHOR) != 1:
            print(f"ABORT: the anchor appears {pristine.count(ANCHOR)} times")
            sys.exit(1)
        panel.write_bytes(pristine.replace(ANCHOR, ANCHOR + injected, 1).encode("utf-8"))
        r = subprocess.run([sys.executable, str(t / "tools" / "test_dead_guard.py"), str(t)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        out = r.stdout + r.stderr
        seen = signal in out
        ok = seen == want_seen
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}"
              f"  (expected {'reported' if want_seen else 'not reported'})")
        if not ok:
            missed += 1
        panel.write_bytes(pristine.encode("utf-8"))

print()
print("FAILED" if missed else "PASSED: the stripper sees code and ignores text")
sys.exit(1 if missed else 0)
