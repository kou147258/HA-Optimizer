"""The keys the panel reads from a scan response must be keys the scan emits.

`write_rollup` was computed by the backend and then dropped before the result left
it, while the panel has passed `data.write_rollup` to `displayResults` at six call
sites since 1.7.11. Every one of them read `undefined`, the per-area write line
could never render, and nothing said so.

The general shape of the bug is a name in one file with no counterpart in the
other. This checks the one call the scan result goes through, and it checks the
backend by looking for the key in the dict the scan ACTUALLY RETURNS.

The previous version of this check collected keys from every `return {...}` in
scanner.py with `ast.walk`. That is the difference between "the panel's key is in
the response" and "the string `write_rollup` appears in a dict somewhere in this
file" - a key parked in `ScanResult.to_dict()`, or in a helper's return, satisfied
it. That is the same bug one level down: the panel was handed a dict without the
key while this check read as satisfied by a dict the panel is never handed.

So the contract is pinned to a function, not to a file. `__init__.py` awaits
`DataScanner.async_scan()` and fires its return value at the panel, which makes
that method's return the only response the panel can see. Its keys are the keys.
Nothing outside that function is consulted, and because the value is read from
the AST, a comment cannot satisfy it either.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ha_optimizer"
PANEL = COMPONENT / "panel.html"
SCANNER = COMPONENT / "scanner.py"
SCANNER_REL = "custom_components/ha_optimizer/scanner.py"
# The function whose return value IS the scan response. Named, not guessed: if
# either name changes, this check says so instead of quietly testing some other
# dict - a check that silently retargets itself is the failure mode it exists to
# catch.
TARGET_CLASS = "DataScanner"
TARGET_METHOD = "async_scan"

panel = PANEL.read_text(encoding="utf-8")
scanner_src = SCANNER.read_text(encoding="utf-8")

results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


# ── the keys the panel hands to displayResults ─────────────────────────────
KEYS = ("results", "groups", "write_measurement", "write_rollup", "statistics")
CALL = re.compile(r"displayResults\(([^;]*?)\);", re.S)
read_keys: set[str] = set()
for m in CALL.finditer(panel):
    for key in KEYS:
        if re.search(rf"\.{key}\b|\b{key}\b", m.group(1)):
            read_keys.add(key)

check("the call the scan result goes through was found", read_keys,
      "no displayResults(...) call site matched, so nothing was tested")


# ── the function the contract is about ─────────────────────────────────────
def find_contract_function(tree: ast.Module):
    """The method whose return value is the response the panel receives."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == TARGET_CLASS:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and item.name == TARGET_METHOD:
                    return item
    return None


def local_dicts(func) -> dict[str, ast.Dict]:
    """`name = {...}` inside the function - the `return name` path."""
    out: dict[str, ast.Dict] = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value
    return out


def returned_dicts(func) -> list[tuple[int, ast.Dict]]:
    """The dicts this function actually returns, as (line, dict).

    A `return {...}` is one. A `return name` is followed into the local that was
    assigned, because that is the SAME dict under another name - not a different
    dict, and emphatically not a dict belonging to some other function. A return
    that resolves to neither is not counted, so a rewritten return path fails the
    check below instead of quietly emptying the key set.
    """
    locals_ = local_dicts(func)
    found: list[tuple[int, ast.Dict]] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        value = node.value
        seen: set[str] = set()
        hops = 0
        while isinstance(value, ast.Name) and hops < 5 and value.id not in seen:
            seen.add(value.id)
            value = locals_.get(value.id)
            hops += 1
        if isinstance(value, ast.Dict):
            found.append((node.lineno, value))
    return found


tree = ast.parse(scanner_src)
contract = find_contract_function(tree)
check(f"{TARGET_CLASS}.{TARGET_METHOD} is the function the contract is about",
      contract is not None,
      f"not found in {SCANNER_REL}; every other return in the file is not the "
      f"response, so the keys below would be read from the wrong dict")

returned: set[str] = set()
rollup_value: ast.AST | None = None
spreads = 0
if contract is not None:
    for _line, d in returned_dicts(contract):
        for key_node, val_node in zip(d.keys, d.values):
            if key_node is None:
                # `**something` contributes keys we cannot see; a result that
                # spreads is not one to assert a fixed key set against.
                spreads += 1
            elif isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                returned.add(key_node.value)
                if key_node.value == "write_rollup":
                    rollup_value = val_node
    if spreads:
        returned.add("*spread*")

check("that function returns a dict literal to check against",
      returned and ("results" in returned or "*spread*" in returned),
      f"the keys it returns are: {sorted(returned)[:8] or 'none'}")

where = f"{SCANNER_REL}  {TARGET_CLASS}.{TARGET_METHOD}()"
missing = sorted(read_keys - returned)
for key in missing:
    print(f"  FAIL  the panel reads .{key} and the scan response does not carry it:"
          f" {where} returns {sorted(returned) or 'no keys at all'}")
check("every key the panel reads is emitted by the scan",
      not missing, f"missing: {', '.join(missing)}")

# The specific one, and the shape the panel indexes into. Read from the same
# dict: a file-wide regex for `"write_rollup":` could be satisfied by a comment
# or by the key having been parked in the wrong dict, which is the defect.
rollup_src = ast.unparse(rollup_value) if rollup_value is not None else None
check("write_rollup carries the per-area numbers, not a scalar",
      rollup_src is not None and "by_area" in rollup_src,
      f"{where} returns write_rollup as "
      f"{rollup_src if rollup_src is not None else 'nothing at all'}")
panel_uses_area = re.search(r"writeRollup\s*\|\|\s*{}\s*\)\s*\[", panel)
check("and the panel indexes it by area name",
      panel_uses_area is not None,
      "the panel's lookup shape changed, so this check is pinning the wrong thing")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
