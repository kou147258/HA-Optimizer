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


def exit_calls(tree: ast.Module) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if is_exit(n)]


def bare_exit_at_top_level(tree: ast.Module) -> int | None:
    """The index of an unconditional module-level exit, if there is one.

    An exit inside `if FAILURES:` is not one: the statements after it are
    reached whenever there were no failures, which is the point of that idiom
    and is how most of these tools report success. Only a bare
    `sys.exit(...)` in the module's own flow ends the run no matter what, and
    anything after it is dead.
    """
    for i, stmt in enumerate(tree.body):
        if isinstance(stmt, ast.Expr) and is_exit(stmt.value):
            return i
    return None


def _unguarded_exits(tree: ast.Module) -> list[ast.Call]:
    """The exits that end the run whatever happens.

    A bare `sys.exit(...)` as a top-level statement, and any exit inside a
    top-level compound statement, is a verdict: exactly one is wanted, because a
    second one is unreachable. An exit inside a `def` is not one of these - it
    belongs to a function, usually an `if __name__` guard, and is not a verdict
    for the module.

    The first version of this asked "is this exit inside a branch?" by walking
    the whole tree, and Module.body matched itself, so every exit counted as
    guarded and every tool reported zero.
    """
    out: list[ast.Call] = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Expr) and is_exit(stmt.value):
            out.append(stmt.value)
        elif isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try,
                               ast.With, ast.AsyncWith)):
            out.extend(node for node in ast.walk(stmt) if is_exit(node))
    return out


tools = sorted(p for p in TOOLS.glob("*.py")
               if p.name.startswith(("test_", "counterproof_", "check_"))
               and p.name != Path(__file__).name)
check("there are tools to check", bool(tools), f"none under {TOOLS}")

without_exit: list[str] = []

for path in tools:
    rel = path.relative_to(ROOT)
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        check(f"{rel} parses", False, str(exc))
        continue

    calls = exit_calls(tree)
    idx = bare_exit_at_top_level(tree)

    if idx is None:
        # A tool that ends through a function, or falls off the end, is fine.
        # A conditional early exit - `if the anchor is not unique: exit` - is
        # also fine, and refusing to run the cases at all is the right thing
        # to do. Neither is a defect, so neither is asserted. This check once
        # counted EVERY sys.exit and called a tool with two guard clauses plus
        # its verdict "exits exactly once", which is not what it says.
        without_exit.append(str(rel))
    else:
        # Only the tail matters. There used to be a second rule here, "reaches
        # its verdict through exactly one exit", and it was wrong twice: it
        # counted a guard clause as a second verdict, and fixing that made it
        # count zero. A module that aborts early and then has one final exit is
        # correct. The defect worth catching is one shape only - module-level
        # work after a top-level exit, which can never run.
        tail = tree.body[idx + 1:]
        check(f"{rel} has no unreachable tail", not tail,
              f"{len(tail)} module-level statement(s) after a top-level exit, "
              f"starting with {type(tail[0]).__name__ if tail else ''} - they "
              f"never run and the file still reports green")

print(f"  ({len(without_exit)} tool(s) end through a function or fall off the end: "
      + ", ".join(without_exit) + ")")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
