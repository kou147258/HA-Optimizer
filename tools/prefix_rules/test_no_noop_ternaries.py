"""A conditional whose two branches are the same expression is a no-op, and it
reads as if it did something.

`r["writes_30d"] = None if measured else None` is the whole story. It looks like
it is distinguishing "no data" from "not measured", which is the distinction
this module is built around, and it does not: both arms are `None`, so every
candidate that had simply never written came back "not measured" while the
measurement it came from reported that it had succeeded.

On the user's instance that left every per-area rollup at `measured: 0`, so the
per-area write line - the thing `write_rollup` was added for - could not render,
and nothing in the response looked wrong.

The check is deliberately narrow: it looks for a conditional expression whose
two arms are textually identical. That is unambiguous, it cannot fire on
anything legitimate, and it would have caught this on the day it was written.
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


files = sorted(p for p in COMPONENT.rglob("*.py") if "__pycache__" not in p.parts)
check("there are modules to check", bool(files), f"none under {COMPONENT}")

found: list[str] = []
seen_ternaries = 0
for path in files:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        found.append(f"{path.name}: does not parse ({exc})")
        continue
    for node in ast.walk(tree):
        if not isinstance(node, ast.IfExp):
            continue
        seen_ternaries += 1
        if ast.dump(node.body) == ast.dump(node.orelse):
            line = node.lineno
            found.append(
                f"{path.relative_to(ROOT)}:{line}  both branches of the "
                f"conditional are `{ast.unparse(node.body)}` - it does nothing, "
                f"and reads as though it does")

check("the check found conditional expressions to look at", seen_ternaries > 0,
      "none matched, so nothing was tested")
for f in found:
    print(f"  FAIL  {f}")
check("no conditional has two identical branches", not found,
      f"{len(found)} found")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed, {seen_ternaries} conditional(s) examined")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
