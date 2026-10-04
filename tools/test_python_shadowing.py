"""A local that shadows a name its function reads earlier is a runtime 500.

`_register_services(hass, entry)` takes the ConfigEntry as `entry` and every
service handler closes over it. A check added to the purge path in 1.7.32 named
its loop variable `entry` too. Python decides local-versus-closure at COMPILE
time, so the two reads that came earlier - which want the ConfigEntry - became
reads of an unbound local:

    UnboundLocalError: cannot access local variable 'entry' where it is not
    associated with a value

Home Assistant reports that to the panel as a bare `HTTP 500: Server got itself
in trouble`, on every soft purge, with no indication of where. Nothing about the
message points at a name, so nothing about the message would have led anyone to
look.

No static name check sees this. `entry` IS defined - in the enclosing scope - so
`test_py_names.py` is right that the name resolves, and it is wrong for the
purge. The rule that does see it is Python's own scoping: inside one function
body, a name is local for the whole body the moment it is assigned anywhere, and
a load that precedes every store is a load of an unbound local.

Scoped precisely, without descending into nested functions - descending is what
makes a naive version of this report `_register_services` for a handler's own
local and look like a second bug where there is none.
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


def own_names(fn: ast.AST) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """Loads and stores belonging to THIS function body only.

    A nested def/lambda/async def is a different scope with its own rules, so
    its body is not walked. `ast.walk` descends into all of them, which is why a
    version of this that used it reported the enclosing factory for a handler's
    own local - a second finding that does not exist, and a false one is how a
    guard gets turned off.
    """
    loads: dict[str, list[int]] = {}
    stores: dict[str, list[int]] = {}

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                # A different scope with its own rules. Its NAME is a store in
                # THIS scope, its body is not ours to walk.
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    stores.setdefault(child.name, []).append(child.lineno)
                continue
            if isinstance(child, ast.arg):
                # A PARAMETER is a binding of the enclosing scope, and it is
                # not an `ast.Name`. Leaving these out made the check blind to
                # exactly the case it was written for - the shadowed name was
                # `_register_services`' parameter - so it passed on the real
                # defect. A name-resolution check that cannot see parameters is
                # not a name-resolution check.
                stores.setdefault(child.arg, []).append(getattr(child, "lineno", 0))
                visit(child)
                continue
            if isinstance(child, ast.Name):
                (loads if isinstance(child.ctx, ast.Load) else stores).setdefault(
                    child.id, []).append(child.lineno)
            visit(child)

    visit(fn)
    return loads, stores


def enclosing_scopes(tree: ast.Module) -> dict[int, set[str]]:
    """For each function node id, the names its enclosing functions bind."""
    bound: dict[int, set[str]] = {}
    stack: list[set[str]] = []

    def names_bound_here(fn: ast.AST) -> set[str]:
        _l, s = own_names(fn)
        return set(s)

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            here = set.union(*stack) if stack else set()
            bound[id(node)] = here
            stack.append(names_bound_here(node))
            for child in ast.iter_child_nodes(node):
                visit(child)
            stack.pop()
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return bound


problems: list[str] = []
files = sorted(p for p in COMPONENT.rglob("*.py") if "__pycache__" not in p.parts)
check("the component has python sources to check", bool(files), f"none under {COMPONENT}")

for path in files:
    rel = path.relative_to(ROOT)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bound = enclosing_scopes(tree)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        loads, stores = own_names(node)
        outer = bound.get(id(node), set())
        for name, at in stores.items():
            if name not in outer:
                continue
            if name not in loads:
                continue
            first_load = min(loads[name])
            if first_load < min(at):
                problems.append(
                    f"{rel}:{node.name}() reads `{name}` at line {first_load} but "
                    f"only assigns it at line {min(at)}; the enclosing scope "
                    f"binds it too, so the load is of an unbound local")

for p in problems:
    print(f"  FAIL  {p}")
check("no function reads a name before every assignment to it", not problems,
      f"{len(problems)} occurrence(s)")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
