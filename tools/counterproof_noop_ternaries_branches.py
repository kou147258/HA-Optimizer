"""Counter-proof for test_no_noop_ternaries.py: the statement form, at the site.

The hole was shape, not logic. The check only ever looked at `ast.IfExp`, so

    if measured:
        r["writes_30d"] = None
    else:
        r["writes_30d"] = None

was the shipped defect, written as an `if`/`else`, and this check could not see it.

The injection replaces the fixed line in `write_measure.annotate()` - the exact
statement the ternary used to occupy, and the one whose two arms have to
distinguish "measured zero" from "never measured" - with the statement form of
the same no-op.

Two trees, on purpose:
  A  the current check - must go RED, and must name the file
  B  the pre-fix check - must stay GREEN
B is what makes this a measurement of the rule and not of the file: same tree,
same injection, only the rule differs. If B went red too, the injection would be
proving a syntax error or a crash. B comes from
`git show HEAD:tools/test_no_noop_ternaries.py`; pass `--old <path>` if that is
not the pre-fix rule any more.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
CHECK = "test_no_noop_ternaries.py"
TARGET_REL = "custom_components/ha_optimizer/write_measure.py"

OLD = '            r["writes_30d"] = 0 if measured else None\n'
NEW = ('            if measured:\n'
       '                r["writes_30d"] = None\n'
       '            else:\n'
       '                r["writes_30d"] = None\n')
SIGNAL = "both arms of the if/else are"
NAMES_FILE = "write_measure.py"


def old_check() -> str:
    # The pre-fix rule lives in `tools/prefix_rules/`, not in git history.
    # Reading it from `git show HEAD:tools/...` works right up until the fix is
    # committed, and after that HEAD holds the rule this file exists to prove is
    # insufficient - so the comparison below would have nothing left to compare
    # and the check would go red on every run from then on. A frozen copy cannot
    # rot that way. It is still compared against the current rule below, so
    # "updating" it to match is caught rather than quietly accepted.
    fixture = ROOT / "tools" / "prefix_rules" / CHECK
    if fixture.is_file():
        return fixture.read_text(encoding="utf-8")
    if len(sys.argv) > 2 and sys.argv[2] == "--old" and len(sys.argv) > 3:
        return Path(sys.argv[3]).read_text(encoding="utf-8")
    got = subprocess.run(["git", "show", f"HEAD:tools/{CHECK}"],
                         cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if got.returncode != 0:
        print(f"FAIL  could not read the pre-fix check from git: {got.stderr.strip()[:120]}")
        print("      pass --old <path-to-the-pre-fix-check> instead")
        sys.exit(1)
    return got.stdout


PRE_FIX = old_check()
CURRENT = (ROOT / "tools" / CHECK).read_text(encoding="utf-8")


def inject(tree: Path) -> str | None:
    """Put the statement form in. Returns an error string, or None on success."""
    target = tree / TARGET_REL
    raw = target.read_bytes()
    eol = "\r\n" if b"\r\n" in raw else "\n"
    lf = raw.decode("utf-8").replace("\r\n", "\n")
    if lf.count(OLD) != 1:
        return (f"the line to replace appears {lf.count(OLD)} times, expected 1, "
                f"so the counter-proof injected nothing")
    if lf.count(NEW) != 0:
        return "the statement form is already present, so this proves nothing new"
    target.write_bytes(lf.replace(OLD, NEW, 1).replace("\n", eol).encode("utf-8"))
    return None


def run(tree: Path, rule: str) -> subprocess.CompletedProcess:
    (tree / "tools" / CHECK).write_text(rule, encoding="utf-8", newline="")
    return subprocess.run([sys.executable, str(tree / "tools" / CHECK), str(tree)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def stage(base: Path, name: str) -> Path:
    tree = base / name
    tree.mkdir()
    shutil.copytree(ROOT / "custom_components", tree / "custom_components")
    shutil.copytree(ROOT / "tools", tree / "tools")
    return tree


missed = 0
with tempfile.TemporaryDirectory() as td:
    base = Path(td)

    # ── tree A: the current rule must catch the statement form ──
    a = stage(base, "a")
    problem = inject(a)
    if problem:
        print(f"FAIL  {problem}")
        missed += 1
    else:
        r = run(a, CURRENT)
        out = r.stdout + r.stderr
        if "Traceback" in out:
            print("FAIL  the check crashed instead of reporting")
            missed += 1
        elif r.returncode == 0:
            print("FAIL  the if/else writes None on both sides and the check passed")
            missed += 1
        elif SIGNAL not in out:
            print(f"FAIL  went red but never said why; expected {SIGNAL!r}")
            missed += 1
        elif NAMES_FILE not in out:
            print(f"FAIL  went red but did not name the file ({NAMES_FILE})")
            missed += 1
        else:
            print("ok    if/else with one value on both sides -> the check goes red")
            for ln in out.splitlines():
                if ln.strip().startswith("FAIL") and "writes_30d" in ln:
                    print("      " + ln.strip()[:150])

    # ── tree B: the pre-fix rule must NOT catch it, or this proves nothing ──
    b = stage(base, "b")
    problem = inject(b)
    if problem:
        print(f"FAIL  {problem} (second tree)")
        missed += 1
    elif PRE_FIX == CURRENT:
        _from = ("tools/prefix_rules/" + CHECK
                 if (ROOT / "tools" / "prefix_rules" / CHECK).is_file()
                 else "git HEAD (no tools/prefix_rules/ fixture exists)")
        print(f"FAIL  the pre-fix rule it read - from {_from} - is the current rule, so "
              f"there is nothing to compare against. Either the fixture was updated to "
              f"match (it must not be) or the fix is committed and --old is needed.")
        missed += 1
    else:
        r = run(b, PRE_FIX)
        out = r.stdout + r.stderr
        if "Traceback" in out:
            print("FAIL  the pre-fix check crashed; that is not a crossable result")
            missed += 1
        elif r.returncode != 0:
            print("FAIL  the pre-fix check also caught it, so this counter-proof is not "
                  "measuring the rule that was added - it is measuring something else")
            missed += 1
        else:
            print("ok    the same injection against the pre-fix rule stays green: it only "
                  "knew about the conditional-expression form")

print()
print("FAILED" if missed else
      "PASSED: the if/else form is caught and named; the pre-fix rule was fooled")
sys.exit(1 if missed else 0)
