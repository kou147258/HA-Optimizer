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
    # Nothing is broken here. The harness must say so. Without this case a
    # harness that reported everything as failed would pass the other three.
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
