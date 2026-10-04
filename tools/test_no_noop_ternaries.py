"""A conditional whose two branches are the same expression is a no-op, and it
reads as if it did something.

`r["writes_30d"] = None if measured else None` is the whole story. It looks like
it is distinguishing "no data" from "not measured", which is the distinction
this module is built around, and it does not: both arms are `None`, so every
candidate that had simply never written came back "not measured" while the
measurement it came from reported that it had succeeded.

The same defect is written two ways, and this file used to look for only one of
them. The conditional-expression form above is an `ast.IfExp`. The statement form

    if measured:
        r["writes_30d"] = None
    else:
        r["writes_30d"] = None

is an `ast.If`, it is the identical bug, and it was invisible here. One shipped.
An `ast.If` is the shape people actually reach for when a branch grows a second
statement, so a check that only reads `IfExp` is not a narrower check on the same
rule - it is a check on one spelling of it.

Both forms are compared as ASTs (`ast.dump`), never as source text, so wrapping,
line breaks and spacing cannot hide either one - and comments cannot hide one
either, because a comment is not in the tree.

The other half of the risk is the rule firing on something legitimate. An `if`
whose arms differ is not a no-op, and a check that flags those teaches people to
ignore it. So the arms are compared for equivalence, an `elif` chain is excluded
(its middle arm still decides what happens), and the rule is tested against
known-answer cases below before it is trusted with the tree. A detector that
flags everything is as useless as one that flags nothing.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ha_optimizer"

results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


def block_src(stmts: list[ast.stmt]) -> str:
    return "; ".join(ast.unparse(s) for s in stmts)


def _no_op_branch(node: ast.If) -> bool:
    """True when an `if`/`else` has the same statements on both sides.

    An elif chain is not a no-op: `if a: X / elif b: X / else: X` still sends `a`
    and `b` to different places, so the arms are not interchangeable.
    """
    if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
        return False
    left = ast.dump(ast.Module(body=node.body, type_ignores=[]))
    right = ast.dump(ast.Module(body=node.orelse, type_ignores=[]))
    return left == right


def scan_source(src: str) -> tuple[list[tuple[int, str]], int, int]:
    """(findings, conditional expressions seen, two-armed ifs seen).

    One function for the known-answer cases and for the tree, so the cases cannot
    pass while the rule that runs on the tree is broken.
    """
    findings: list[tuple[int, str]] = []
    ternaries = branches = 0
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.IfExp):
            ternaries += 1
            if ast.dump(node.body) == ast.dump(node.orelse):
                findings.append((node.lineno, "both arms of the conditional are "
                                 f"`{ast.unparse(node.body)}`"))
        elif isinstance(node, ast.If) and node.orelse:
            branches += 1
            if _no_op_branch(node):
                findings.append((node.lineno, "both arms of the if/else are "
                                 f"`{block_src(node.body)}`"))
    return findings, ternaries, branches


# ── the rule, against cases whose answer is known ──────────────────────────
SAME_BRANCH = "def f(r, measured):\n    if measured:\n        r[\"n\"] = None\n    else:\n        r[\"n\"] = None\n"
CASES: list[tuple[str, str, int]] = [
    ("the statement form of the defect this check exists for", SAME_BRANCH, 1),
    ("the same defect wrapped and split over lines",
     'def f(r, m):\n    if m:\n        r["n"] = None\n    else:\n        r["n"] = (\n            None\n        )\n', 1),
    ("branches that differ only by a comment",
     'def f(r, m):\n    if m:\n        r["n"] = 1  # measured\n    else:\n        # never queried\n        r["n"] = 1\n', 1),
    ("the same call in both arms",
     "def f(c, o):\n    if c:\n        o.append(1)\n    else:\n        o.append(1)\n", 1),
    ("the conditional-expression form",
     "def f(m):\n    return None if m else None\n", 1),
    ("two arms with different values", "def f(c):\n    if c:\n        return 1\n    else:\n        return 2\n", 0),
    ("two arms that do distinguish",
     'def f(r, m):\n    if m:\n        r["n"] = 0\n    else:\n        r["n"] = None\n', 0),
    ("one arm and no else", "def f(c):\n    if c:\n        return 1\n    return 1\n", 0),
    ("an elif chain whose first and last arms match",
     "def f(a, b):\n    if a:\n        return 1\n    elif b:\n        return 0\n    else:\n        return 1\n", 0),
    ("arms of different length",
     'def f(r, c):\n    if c:\n        r["n"] = 1\n    else:\n        r["n"] = 1\n        r["m"] = 2\n', 0),
    ("the same method name on two different objects",
     "def f(c, a, b):\n    if c:\n        a.add()\n    else:\n        b.add()\n", 0),
    ("a conditional expression that does distinguish",
     "def f(r, m):\n    r[\"n\"] = 0 if m else None\n", 0),
]
self_fail: list[str] = []
for label, src, want in CASES:
    got, _t, _b = scan_source(src)
    if len(got) != want:
        self_fail.append(f"{label}: expected {want} finding(s), the rule reported {len(got)}")
for problem in self_fail:
    print(f"  FAIL  the rule disagrees with a known case - {problem}")
check(f"the rule matches all {len(CASES)} known cases, including the ones it must not flag",
      not self_fail, f"{len(self_fail)} case(s) wrong")

# ── the component ──────────────────────────────────────────────────────────
files = sorted(p for p in COMPONENT.rglob("*.py") if "__pycache__" not in p.parts)
check("there are modules to check", bool(files), f"none under {COMPONENT}")

found: list[str] = []
seen_ternaries = 0
seen_branches = 0
for path in files:
    try:
        findings, ternaries, branches = scan_source(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        found.append(f"{path.name}: does not parse ({exc})")
        continue
    seen_ternaries += ternaries
    seen_branches += branches
    for line, what in findings:
        found.append(f"{path.relative_to(ROOT)}:{line}  {what} - it does nothing, "
                     f"and reads as though it does")

check("the check found conditional expressions to look at", seen_ternaries > 0,
      "none matched, so nothing was tested")
check("the check found two-armed ifs to look at", seen_branches > 0,
      "no if/else matched, so the statement form was never exercised on this tree")
for f in found:
    print(f"  FAIL  {f}")
check("no conditional has two identical branches", not found,
      f"{len(found)} found")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed, {seen_ternaries} conditional expression(s) and "
      f"{seen_branches} two-armed if(s) examined")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
