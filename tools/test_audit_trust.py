"""Prove the harness is no longer crossed by the trick that crossed it.

An audit of this suite injected a real defect into a COMPONENT and then made the
check that covers it end with `sys.exit(0)`. It printed `7/10 passed` and three
FAIL lines, and audit.py reported `ok  pass`. That was the whole trust boundary:
47 checks, and one line of `sys.exit(0)` disarmed all of them.

This runs the real harness against a scratch tree and requires the check to be
reported as a FAILURE. It is the one test that is about the tests.

Two things make it more than a copy of the trick it replaces:

  * Every patch asserts that it applied, exactly once. The first version of this
    file anchored its defect in the test file, where there was no defect to
    inject, and every patch silently matched nothing. It ran, printed three
    FAILs, and proved nothing - a counter-proof that cannot fail is the exact
    thing this test exists to prevent, so it is checked here first.
  * The last case is a clean tree. A harness that reported every check as red
    would satisfy the other three, so "red" is only meaningful next to "green
    still means green".
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


# The defect, as the component spells it. `days <= 0` is the guard that keeps
# 0 meaning "I empty the trash myself"; move it out of reach of every window the
# check tries and all three of those checks fail.
DEFECT = ("        if days <= 0:\n            return []",
          "        if days <= -99:\n            return []")
COUNT_LINE = 'print(f"{ok}/{len(results)} passed")'
VERDICT_LINE = 'print("FAILED" if ok != len(results) else "PASSED")'
EXIT_LINE = "sys.exit(0 if ok == len(results) else 1)"

CASES: list[tuple[str, list[tuple[str, str, str]], bool]] = [
    # The trick exactly as it was reported: real defect, optimistic count, lie
    # about the exit code.
    ("the check fails but prints an optimistic count and exits 0",
     [("custom_components/ha_optimizer/store.py", *DEFECT),
      ("tools/test_store_records.py", VERDICT_LINE, 'print("7/10 passed")'),
      ("tools/test_store_records.py", EXIT_LINE, "sys.exit(0)")],
     True),
    # A tool that says nothing at all and exits 0. Silence is not consent, and
    # the count is gone too, so only the missing-verdict rule can catch this.
    ("the check prints no verdict and exits 0",
     [("custom_components/ha_optimizer/store.py", *DEFECT),
      ("tools/test_store_records.py", COUNT_LINE, ""),
      ("tools/test_store_records.py", VERDICT_LINE, ""),
      ("tools/test_store_records.py", EXIT_LINE, "sys.exit(0)")],
     True),
    # The obvious one, which the old harness caught on the exit code alone.
    # Kept as a control: if only these pass, the two above passed for free.
    ("the check simply fails (the control)",
     [("custom_components/ha_optimizer/store.py", *DEFECT)],
     True),
    # The one that crossed the FIRST version of the new rule, and it crossed it
    # because that rule called an indented `FAIL` data. Every check in the
    # directory reports its failed assertions as `  FAIL  <name>`, so the
    # exemption covered exactly what real checks emit. This tool lies
    # consistently - the count agrees with itself, the verdict says PASSED, the
    # exit is 0 - and next to three indented FAILs it was read as a pass.
    ("the check makes its own count agree and still exits 0",
     [("custom_components/ha_optimizer/store.py", *DEFECT),
      ("tools/test_store_records.py", COUNT_LINE,
       'print(f"{len(results)}/{len(results)} passed")'),
      ("tools/test_store_records.py", VERDICT_LINE, 'print("PASSED")'),
      ("tools/test_store_records.py", EXIT_LINE, "sys.exit(0)")],
     True),
    # Nothing is broken here. The harness must say so. Without this case a
    # harness that reported everything as failed would pass the other cases.
    ("an unmutated tree is still reported as passing",
     [],
     False),
]


def build(t: Path, patches: list[tuple[str, str, str]]) -> None:
    shutil.copytree(ROOT / "custom_components", t / "custom_components")
    shutil.copytree(ROOT / "tools", t / "tools")
    for sub in ("custom_components", "tools"):
        for pc in (t / sub).rglob("__pycache__"):
            shutil.rmtree(pc, ignore_errors=True)
    for rel, old, new in patches:
        p = t / rel
        text = p.read_bytes().decode("utf-8").replace("\r\n", "\n")
        n = text.count(old)
        if n != 1:
            raise AssertionError(
                f"the patch for {rel} matched {n} times, not once:\n"
                f"  anchor: {old!r}\n"
                f"  -> a patch that does not apply is a test that cannot fail")
        p.write_bytes(text.replace(old, new, 1).replace("\n", "\r\n").encode("utf-8"))


# ── the rule itself, on outputs no scratch tree can be asked to produce ─────
# The end-to-end cases above need a whole tree per case and can only inject what
# a real tool does. These pin the decisions directly, including the ones that
# depend on WHAT KIND of tool is speaking - which is not in the output at all,
# and is the thing the previous version of the rule got wrong.
sys.path.insert(0, str(ROOT / "tools"))
import audit  # noqa: E402

A_CHECK = (f"  PASS  a read does not expose the live record\n"
           f"  FAIL  a window of 0 day(s) expires nothing\n"
           f"  FAIL  a window of -1 day(s) expires nothing\n")
A_COUNTERPROOF = (f"  FAIL  a window of 0 day(s) expires nothing\n"
                  f"  PASS  the counter-proof caught it\n")

RULES = [
    # The reported bypass: the count agrees with itself, the verdict says
    # PASSED, and three assertions are visibly red.
    ("an indented FAIL is a failed assertion in a CHECK",
     audit.tool_verdict(A_CHECK + "10/10 passed\nPASSED\n"), False),
    # The same output from a counter-proof: those FAILs are the defect it was
    # asked to catch, and its own verdict speaks for it.
    ("the same lines are data in a COUNTER-PROOF",
     audit.tool_verdict(A_COUNTERPROOF + "1/1 passed\n"
                        "PASSED: every injected defect was caught, and named\n",
                        counterproof=True), True),
    # A failure line matches the "all ... passed" shape, so it can be mistaken
    # for the verdict and quoted as the thing that passed.
    ("a failure line is never the verdict",
     audit.tool_verdict(A_COUNTERPROOF + "  FAIL  missing the all checks "
                        "passed line\nPASSED\n", counterproof=True), True),
    # The reason the verdict is the LAST such line: an engine's own output can
    # follow the summary, and it must not overturn it.
    ("log output after the verdict does not overturn it",
     audit.tool_verdict("10/10 passed\nPASSED\nWARNING: recorder is busy\n"), True),
    # And the two ways a check can be a failure without printing one.
    ("a count that does not add up is a failure",
     audit.tool_verdict("7/10 passed\nPASSED\n"), False),
    ("silence is not consent",
     audit.tool_verdict("  PASS  everything is fine\n"), False),
]
for label, (believed, _evidence), expect_pass in RULES:
    check(label, believed == expect_pass,
          f"expected believed_pass={expect_pass}, got {believed}")


for label, patches, expect_red in CASES:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        try:
            build(t, patches)
        except AssertionError as exc:
            check(label, False, str(exc).replace("\n", " ")[:150])
            continue
        report = t / "report.json"
        r = subprocess.run([sys.executable, str(t / "tools" / "audit.py"), "check",
                            "store.records"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=str(t))
        out = (r.stdout or "") + (r.stderr or "")
        # `--json` is not used: `to_json()` calls git, and a scratch copy is not
        # a repository. What is read here is the render summary's one-line-per-
        # failure block, which the renderer emits verbatim for every check that
        # did not pass - so a passing check produces no such line at all. The
        # `total 1` guard is what keeps "no output" from counting as green.
        red = bool(re.search(r"^\s+FAIL\s+store\.records:\s", out, re.M))
        ran = "total 1" in out
        print(f"\n  {label}")
        print(f"    audit.py exit={r.returncode}  ran={ran}  reported_red={red}  "
              f"expect_red={expect_red}")
        for ln in out.splitlines():
            if "store.records" in ln or ln.startswith("total"):
                print(f"      | {ln.strip()[:100]}")
        if not ran:
            check(label, False, "the harness did not report the check at all")
            continue
        check(label, red == expect_red,
              "the harness believed a check that lied about its own result"
              if expect_red else "the harness rejected a tree with nothing wrong")

ok = sum(1 for x in results if x[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
