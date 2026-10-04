"""Counter-proof for test_panel_renders.py.

The bug it exists for: `partialDay is not defined`, which shipped in 1.7.30 and
blanked the fingerprint tab. `node --check` parses it perfectly, and so does
every other check in tools/ - the previous version of the same mistake, `today is
not defined`, had already shipped and was found by a person running the code.

So the cases put those exact defects back and require this check to go red AND
name them. Naming matters: a check that goes red without saying which identifier
is out of scope is one step from being ignored.

Three more holes in the same check, each closed here the same way - by proving
the check goes red and names the entry point it broke:

  * COVERAGE. It called two render entry points out of the fourteen panel.html
    declares, so a whole render body replaced with `return;` was invisible.
  * THE ASSERTION COULD NOT FAIL. The DOM stub preset `innerHTML` to '' and the
    check asserted the result was a string - true of '' too. A render that wrote
    nothing passed.
  * SWALLOWED EXCEPTIONS. The requestAnimationFrame stub caught and discarded
    the error, and the gauge animation is the one place the panel wraps render
    work in rAF.

The last case is the control, and the second-to-last is the one that matters
most: the same rAF defect with the old swallow put back is SILENT again, which
is what proves the swallow - not something else - was what hid it.

Every patch asserts that it applied, exactly once. A counter-proof whose
injection did not apply must say so loudly rather than pass.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

PANEL = "custom_components/ha_optimizer/panel.html"
TEST = "tools/test_panel_renders.py"

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

# (a) a whole render body neutered. The health tab renders into `healthContent`
# and the very next line is the "nothing to show" early return, so this is where
# a `return;` hides with nothing left to notice it.
NEUTER = ("function renderHealthResults(d, ts) {\n"
          "  const c = document.getElementById('healthContent');\n"
          "  if (!c) return;",
          "function renderHealthResults(d, ts) {\n"
          "  return;\n"
          "  const c = document.getElementById('healthContent');\n"
          "  if (!c) return;")

# (b) a render body that still RUNS and still returns normally, and writes
# nothing: the last assignment moved onto a style property. Nothing throws, so
# the old "did not throw" assertion had nothing to say, and the old `typeof
# host.innerHTML === 'string'` assertion was satisfied by the '' the stub was
# born with.
SILENT = ("  }).join('') + '</div>';\n"
          "  c.innerHTML = html;\n"
          "}\n"
          "\n"
          "// ================================================================\n"
          "// INTEGRATION HEALTH",
          "  }).join('') + '</div>';\n"
          "  c.style.opacity = '0';\n"
          "}\n"
          "\n"
          "// ================================================================\n"
          "// INTEGRATION HEALTH")

# (c) a ReferenceError inside the panel's only requestAnimationFrame render.
RAF = ("      requestAnimationFrame(() => {\n"
       "        clipRect.setAttribute('width', (hs / 100 * 160).toFixed(1));",
       "      requestAnimationFrame(() => {\n"
       "        clipRect.setAttribute('width', (hs / 100 * partialDay * 160).toFixed(1));")

# The swallow itself, as it was: catch and discard whatever the frame threw.
OLD_RAF_STUB = ("globalThis.requestAnimationFrame = (fn) => { fn(0); return 0; };",
                "globalThis.requestAnimationFrame = (fn) => { try { fn(0); } catch (e) {} return 0; };")

CASES = [
    ("1.7.30 as shipped: partialDay is never declared",
     [(PANEL, DECL, "")],
     "partialDay is not defined", True),
    ("the earlier one: _fpMetricRow reading `today` from its own scope",
     [(PANEL, SIGNATURE,
       "function _fpMetricRow(label, extrapolated, raw, unit, hoursElapsed) {\n"
       "  const partialDay = today.partial;")],
     "today is not defined", True),
    ("(a) a whole render body replaced with `return;`",
     [(PANEL, *NEUTER)],
     "renderHealthResults", True),
    ("(b) a render that runs to the end and writes nothing",
     [(PANEL, *SILENT)],
     "renderDeadCodeResults", True),
    ("(c) a ReferenceError inside the gauge's requestAnimationFrame",
     [(PANEL, *RAF)],
     "_renderOverview", True),
    # (d) The exclusion reasons are backend strings, and the assertion that
    # they arrive escaped is only meaningful if the escaping is what makes it
    # true. An earlier version of that case put the probe string in
    # `not_contains` and never in the data - so the data could not contain it,
    # and the assertion was satisfied by nothing at all.
    ("(d) the exclusion reasons reach the markup unescaped",
     [(PANEL,
       "        `<li>${escapeHtml(k)}: ${Number(baselineExcluded[k]) || 0}</li>`)"
       ".join('')}</ul>\n",
       "        `<li>${k}: ${Number(baselineExcluded[k]) || 0}</li>`)"
       ".join('')}</ul>\n")],
     "present but must not be", True),
    # The control for (c): the identical defect with the old swallow put back is
    # silent again. If this ever goes red, (c) is no longer proving what it
    # claims to prove - the throw would be reaching the harness by another route.
    ("(c) control: the same defect with the old rAF swallow reinstated is silent",
     [(TEST, *OLD_RAF_STUB), (PANEL, *RAF)],
     "_renderOverview", False),
    # The control for everything: a tree with nothing wrong in it still passes,
    # so "red" above means something.
    ("control: an unmutated tree is still reported as passing",
     [],
     "", False),
]


def build(t: Path, patches: list[tuple]) -> None:
    shutil.copytree(ROOT / "custom_components", t / "custom_components")
    shutil.copytree(ROOT / "tools", t / "tools")
    for sub in ("custom_components", "tools"):
        for pc in (t / sub).rglob("__pycache__"):
            shutil.rmtree(pc, ignore_errors=True)
    for rel, old, new in patches:
        p = t / rel
        raw = p.read_bytes().decode("utf-8")
        # panel.html is CRLF and the anchors above use \n, so the comparison runs
        # on normalised text and the write restores what the file had. Reading it
        # the other way round reports "anchor appears 0 times" and tests nothing.
        crlf = "\r\n" in raw
        text = raw.replace("\r\n", "\n")
        n = text.count(old)
        if n != 1:
            raise AssertionError(
                f"the patch for {rel} matched {n} times, not once:\n"
                f"  anchor: {old[:90]!r}\n"
                f"  -> a patch that does not apply is a test that cannot fail")
        out = text.replace(old, new, 1)
        p.write_bytes((out.replace("\n", "\r\n") if crlf else out).encode("utf-8"))


missed = 0
for label, patches, signal, expect_red in CASES:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        try:
            build(t, patches)
        except AssertionError as exc:
            print(f"FAIL  {label} - the patch did not apply")
            print("      " + str(exc).replace("\n", "\n      ")[:200])
            missed += 1
            continue
        r = subprocess.run([sys.executable, str(t / "tools" / "test_panel_renders.py"), str(t)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        crashed = "Traceback" in out
        red = r.returncode != 0
        named = bool(signal) and any(signal in ln and "FAIL" in ln for ln in out.splitlines())
        print(f"{'FAIL' if (red != expect_red or crashed or (signal and expect_red and not named)) else 'ok  '}  {label}")
        print(f"      exit={r.returncode}  expect_red={expect_red}  named={signal!r}->{named}")
        for ln in out.splitlines():
            if ln.strip().startswith(("FAIL", "-- ")) or (signal and signal in ln):
                print("      | " + ln.strip()[:130])
        if crashed:
            print("      | the check itself crashed: " + out.strip().splitlines()[-1][:110])
            missed += 1
        elif red != expect_red:
            print("      | expected "
                  + ("red" if expect_red else "green") + " and got the other")
            missed += 1
        elif expect_red and not named:
            print("      | went red but never named " + signal)
            missed += 1

print()
print("FAILED" if missed else
      "PASSED: every hole is closed, and every one of them is named")
sys.exit(1 if missed else 0)
