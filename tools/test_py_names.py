"""Every name the component calls must exist. This is the Python half of a
check that already exists for the panel's JavaScript.

1.7.22 shipped a tab that answered HTTP 500 on every load because
`async_analyze` called `_ensure_trace_component`, which nothing in the
repository ever defined. Twenty-five checks passed, and all of them would pass
again: `py_compile` only checks syntax, a grep for the name finds the call, and
the module docstring explains the surrounding code fluently enough that the
missing definition is not what you notice.

What makes it invisible is that the name is not absent from the file - it is
absent from the module while the code reads as though it were there. So the
check is scope-aware rather than a text search: Python's own `symtable` already
knows which scope a reference belongs to and whether that scope binds it, and
that is exactly the question being asked.

Deliberately zero false positives, because a noisy check gets ignored. A name is
reported only when the enclosing scope does not bind it, no enclosing scope
binds it as a closure, and no module-level binding or builtin provides it.

Line numbers come from `ast`, not from `symtable`: `symtable.Symbol` has no
line-number method, and a report whose line numbers are invented sends the next
reader to the wrong place - which is worse than reporting no line at all.
"""
from __future__ import annotations

import ast
import builtins
import symtable
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ha_optimizer"

# Provided by the interpreter for every module, not by the source.
MODULE_DUNDERS = {
    "__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__",
    "__builtins__", "__debug__", "__annotations__", "__path__", "__all__",
    "__class__",
}

# Bound by the class machinery, never by a def/class statement.
IMPLICIT_NAMES = {"__qualname__", "__module__", "__dict__", "__weakref__",
                  "__doc__", "__firstlineno__", "__static_attributes__"}

BUILTINS = set(dir(builtins))


def module_bindings(table: symtable.SymbolTable) -> set[str]:
    """Names the module itself provides to every scope inside it."""
    out = set()
    for sym in table.get_symbols():
        if sym.is_assigned() or sym.is_imported() or sym.is_namespace():
            out.add(sym.get_name())
    return out


def unresolved(table: symtable.SymbolTable, available: set[str],
               stack: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """(scope, name) for every reference this scope cannot resolve.

    `available` is what the scope may fall back on: the module's own bindings
    plus the builtins. Nested scopes add nothing to it, because a function body
    cannot see a sibling's locals - a reference that is neither local nor free
    resolves at module level or not at all.
    """
    found: list[tuple[str, str]] = []
    for sym in table.get_symbols():
        name = sym.get_name()
        if not sym.is_referenced():
            continue
        if sym.is_assigned() or sym.is_parameter() or sym.is_imported():
            continue           # local, parameter, or a local import
        if sym.is_free():
            continue           # bound as a closure by an enclosing scope
        if name in available or name in BUILTINS:
            continue
        if table.get_type() == "class" and name in IMPLICIT_NAMES:
            continue
        scope = " > ".join(("module", *stack, table.get_name()))
        found.append((scope, name))
    for child in table.get_children():
        if child.get_type() in ("function", "class", "comprehension"):
            # The module scope's own name is "top"; it is not part of a path.
            label = table.get_name() if table.get_type() != "module" else ""
            found.extend(unresolved(child, available, stack + ((label,) if label else ())))
    return found


def find_scope(tree: ast.Module, scope: str) -> ast.AST | None:
    """The ast node for a scope path like "module > Klass > method".

    A comprehension is a scope to symtable but not a node in the ast, so its
    name resolves to nothing and the search continues in the enclosing
    function. That yields a line number that is in the right function rather
    than no line number at all.
    """
    node: ast.AST = tree
    for part in scope.split(" > ")[1:]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)) and child.name == part:
                node = child
                break
    return node


def lines_for(tree: ast.Module, scope: str, name: str) -> list[int]:
    node = find_scope(tree, scope)
    if node is None:
        return []
    return sorted({n.lineno for n in ast.walk(node)
                   if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                   and n.id == name})


results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


files = sorted(p for p in COMPONENT.rglob("*.py") if "__pycache__" not in p.parts)
check("the component has python sources to check", bool(files), f"none under {COMPONENT}")

star_imports: list[str] = []
broken: list[str] = []
total = 0

for path in files:
    rel = path.relative_to(ROOT)
    src = path.read_text(encoding="utf-8")
    if "import *" in src:
        star_imports.append(str(rel))
    try:
        tree = ast.parse(src, filename=str(path))
        top = symtable.symtable(src, str(path), "exec")
    except SyntaxError as exc:
        broken.append(f"{rel}: {exc}")
        continue
    available = module_bindings(top) | MODULE_DUNDERS
    # A crash in the checker must not look like a pass. Reporting the exception
    # is the only honest option: this file once raised AttributeError on the
    # reporting path, which meant a genuinely broken tree produced a non-zero
    # exit and no report at all - indistinguishable, to a counter-proof that
    # only watches the exit code, from having caught the defect.
    try:
        hits = unresolved(top, available)
    except Exception as exc:  # noqa: BLE001
        broken.append(f"{rel}: the check itself failed ({type(exc).__name__}: {exc})")
        continue
    for scope, name in hits:
        total += 1
        where = lines_for(tree, scope, name)
        at = f"{rel}:{where[0]}" if where else f"{rel} (in {scope})"
        print(f"  FAIL  {at}  {name!r} is not defined anywhere  [in {scope}]")

# A star import makes "is this name defined" unanswerable, so a module that
# uses one is reported rather than silently trusted.
check("no module hides its names behind a star import", not star_imports,
      ", ".join(star_imports))
check("every module parses and the check survives it", not broken, "; ".join(broken))
check("no reference resolves to a name nothing defines", total == 0,
      f"{total} undefined reference(s) above")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
