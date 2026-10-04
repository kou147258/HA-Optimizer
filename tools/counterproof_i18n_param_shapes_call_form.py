"""Counter-proof for test_i18n_param_shapes.py: the two forms the check missed.

The check's definition rule was anchored on `=> `, so a translation defined as a
plain string holding `{name}` placeholders was invisible - and an invisible
definition meant its call sites were skipped silently, because the loop moved on
when it found no shape. Its call rule was anchored on `'`, so a backtick-quoted
key was invisible too. A wrong-arity call in either form was not a finding. It
was nothing at all.

Every case below is a call the OLD rule could not see. The ones in the form the
old rule already caught would prove nothing new:

  1  a scalar where a `{name}` string definition needs an object
  2  the same wrong shape behind a backtick-quoted key
  3  a `{name}` string definition rewritten as a scalar function in BOTH
     languages, so the shape comparison has to see both forms as one namespace

Case 1 is then re-run against the pre-fix check and must stay GREEN, which is
what makes this a measurement of the rule rather than of the file: same tree,
same injection, only the rule differs. It comes from
`git show HEAD:tools/test_i18n_param_shapes.py`; pass `--old <path>` if that is
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
CHECK = "test_i18n_param_shapes.py"
PANEL_REL = "custom_components/ha_optimizer/panel.html"

# (label, [(old, new, expected_count)], signal the check must print)
#
# The argument injected is a LITERAL. A member read like `dev.reconnects_7d` is
# deliberately not one: a property read could be the object the body wants just
# as easily as a scalar, so it is the same "either shape is possible" case as a
# variable and the check skips it on purpose. Guessing there would be a false
# positive, and this counter-proof is here to prove a rule, not to grade it.
CASES = [
    ("a scalar where a {name} string definition needs an object",
     [("t('healthReconnects7d', {n: dev.reconnects_7d})",
       "t('healthReconnects7d', 7)", 1)],
     "healthReconnects7d: the body needs object, but the call passes scalar"),

    ("the same wrong shape behind a backtick-quoted key",
     [("${t('healthBdPenalty', {n: 35})}", "${t(`healthBdPenalty`, 35)}", 1)],
     "healthBdPenalty: the body needs object, but the call passes scalar"),

    ("a {name} string definition rewritten as a scalar function in both languages",
     [("    healthBdPenalty: '-{n} pts',",
       "    healthBdPenalty: (n) => `-${n} pts`,", 1),
      ("    healthBdPenalty: '-{n} 分',",
       "    healthBdPenalty: (n) => `-${n} 分`,", 1)],
     "healthBdPenalty: the body needs scalar, but the call passes object"),
]

PRISTINE_EDITS = CASES[0][1]      # the injection the pre-fix rule is measured on


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


def apply(panel: Path, pristine: str, edits, eol: str) -> str | None:
    """Patch the panel, or return the reason the patch did not happen."""
    text = pristine
    for old, new, count in edits:
        if text.count(old) != count:
            return (f"anchor appears {text.count(old)} times, expected {count}: "
                    f"{old[:60]!r}")
        text = text.replace(old, new, count)
    panel.write_bytes(text.replace("\n", eol).encode("utf-8"))
    return None


def run(tree: Path, rule: str) -> subprocess.CompletedProcess:
    (tree / "tools" / CHECK).write_text(rule, encoding="utf-8", newline="")
    return subprocess.run([sys.executable, str(tree / "tools" / CHECK), str(tree)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


missed = 0
with tempfile.TemporaryDirectory() as td:
    tree = Path(td) / "a"
    tree.mkdir()
    shutil.copytree(ROOT / "custom_components", tree / "custom_components")
    shutil.copytree(ROOT / "tools", tree / "tools")
    panel = tree / PANEL_REL
    raw = panel.read_bytes()
    eol = "\r\n" if b"\r\n" in raw else "\n"
    pristine = raw.decode("utf-8").replace("\r\n", "\n")
    (tree / "tools" / CHECK).write_text(CURRENT, encoding="utf-8", newline="")

    for label, edits, signal in CASES:
        problem = apply(panel, pristine, edits, eol)
        if problem:
            print(f"FAIL  {label} - {problem}, so the case tested nothing")
            missed += 1
            continue
        r = run(tree, CURRENT)
        out = r.stdout + r.stderr
        if "Traceback" in out:
            print(f"FAIL  {label} - the check crashed instead of reporting")
            missed += 1
        elif r.returncode == 0:
            print(f"FAIL  {label} - the check still passed")
            missed += 1
        elif signal not in out:
            print(f"FAIL  {label} - went red but never said why; expected {signal!r}")
            missed += 1
        else:
            print(f"ok    {label}")
            for ln in out.splitlines():
                if ln.strip().startswith("FAIL") and "health" in ln:
                    print("      " + ln.strip()[:150])

    # ── the pre-fix rule, on the first injection, must not catch it ──
    problem = apply(panel, pristine, PRISTINE_EDITS, eol)
    if problem:
        print(f"FAIL  {problem} (pre-fix comparison)")
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
        r = run(tree, PRE_FIX)
        out = r.stdout + r.stderr
        if "Traceback" in out:
            print("FAIL  the pre-fix check crashed; that is not a crossable result")
            missed += 1
        elif r.returncode != 0:
            print("FAIL  the pre-fix check also caught it, so this counter-proof is not "
                  "measuring the rule that was added - it is measuring something else")
            missed += 1
        else:
            print("ok    the same injection against the pre-fix rule stays green: a "
                  "`{name}` string definition was not a definition to it at all")

print()
print("FAILED" if missed else
      "PASSED: all three missed forms are caught and named; the pre-fix rule was fooled")
sys.exit(1 if missed else 0)
