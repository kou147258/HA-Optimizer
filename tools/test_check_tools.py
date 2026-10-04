"""A check that cannot fail is worse than no check, and the quietest way to
build one is a file that exits before its own assertions.

`test_trace_join.py` carried two summary-and-exit blocks. The first sat in the
middle, so the process ended there and the whole diagnosis section after it -
thirteen assertions, including the one guarding the HTTP 500 this project
shipped - never ran. The file reported 35/35 and green, and the dead section
would have stayed dead through any number of runs.

Nothing about the checks themselves would ever have said so, because a skipped
assertion is indistinguishable from a passing one to every reader. What can say
so is the file's shape, and that is the check: a tool must reach its verdict
exactly once, and the verdict must be the last thing it does.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


def is_exit(node: ast.AST) -> bool:
    """True for `exit(...)`, `quit(...)` and - the one that matters here -
    `sys.exit(...)`.

    The first version matched only a bare Name, so every tool in the directory
    was reported as having no exit at all and 27 of them were skipped. The
    symptom was a check quietly covering nothing, which is the exact failure
    this file was written to prevent, committed in the file written to prevent
    it.
    """
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name):
        return f.id in ("exit", "quit")
    if isinstance(f, ast.Attribute):
        return f.attr in ("exit", "quit") and isinstance(f.value, ast.Name) \
            and f.value.id == "sys"
    return False


def is_system_exit(stmt: ast.AST) -> bool:
    """True for `raise SystemExit` and `raise SystemExit(...)`.

    This is a RAISE, not a call, so `is_exit` cannot see it - and a tool whose
    summary-and-exit is written that way had no terminator as far as this check
    was concerned, which is the same hole from a different spelling.
    """
    if not isinstance(stmt, ast.Raise) or stmt.exc is None:
        return False
    e = stmt.exc.func if isinstance(stmt.exc, ast.Call) else stmt.exc
    if isinstance(e, ast.Name):
        return e.id == "SystemExit"
    if isinstance(e, ast.Attribute):
        return e.attr == "SystemExit"
    return False


def terminates_run(stmt: ast.AST) -> bool:
    """A statement that ends the process whenever it is reached."""
    return (isinstance(stmt, ast.Expr) and is_exit(stmt.value)) or is_system_exit(stmt)


def is_main_guard(stmt: ast.AST) -> bool:
    """True for `if __name__ == "__main__":`, which is always taken here.

    Every one of these tools is run as a script by the harness, so an exit
    inside that guard ends the run exactly like a top-level one does. It is
    the one branch that is not a branch, and treating it as one left a second
    spelling of the original defect uncatchable.
    """
    if not isinstance(stmt, ast.If) or not isinstance(stmt.test, ast.Compare):
        return False
    c = stmt.test
    if len(c.ops) != 1 or not isinstance(c.left, ast.Name) or c.left.id != "__name__":
        return False
    return any(isinstance(v, ast.Constant) and v.value == "__main__"
               for v in c.comparators)


def dead_tail(tree: ast.Module) -> list[ast.stmt]:
    """The module-level statements that can never run, in file order.

    A conditional exit - `if FAILURES: sys.exit(1)` - is deliberately not one of
    these: the statements after it are reached whenever there were no failures,
    which is how most of these tools report success. Only something that ends
    the run no matter what makes the rest of the file dead, and a bare exit, a
    `raise SystemExit`, and an exit inside the `__main__` guard are the three
    spellings of that.

    Statements after the terminator INSIDE the guard are dead too, which is why
    the guard's own body is scanned and not just the module's.
    """
    for i, stmt in enumerate(tree.body):
        if terminates_run(stmt):
            return tree.body[i + 1:]
        if is_main_guard(stmt):
            for j, inner in enumerate(stmt.body):
                if terminates_run(inner):
                    return [*stmt.body[j + 1:], *tree.body[i + 1:]]
    return []


tools = sorted(p for p in TOOLS.glob("*.py")
               if p.name.startswith(("test_", "counterproof_", "check_"))
               and p.name != Path(__file__).name)
check("there are tools to check", bool(tools), f"none under {TOOLS}")

# A tool that ends through a function, or falls off the end, or aborts inside
# `if FAILURES:` is fine - none of those is a terminator. This check used to
# keep a list of them and print it, which read like coverage: the count went
# down as the rule got narrower, and the tools in it were never looked at.
checked = 0
for path in tools:
    rel = path.relative_to(ROOT)
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        check(f"{rel} parses", False, str(exc))
        continue

    tail = dead_tail(tree)
    checked += 1
    check(f"{rel} has no unreachable tail", not tail,
          f"{len(tail)} statement(s) after something that ends the run no matter "
          f"what, starting with {type(tail[0]).__name__ if tail else ''} - they "
          f"never run and the file still reports green")

# The count is asserted, not printed. A summary line that goes quiet when the
# rule narrows is indistinguishable from one that went quiet because it stopped
# looking; making it an assertion means a tool that stops being scanned fails
# instead of quietly disappearing from a log line.
check("every tool was scanned", checked == len(tools),
      f"{checked} of {len(tools)}")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
