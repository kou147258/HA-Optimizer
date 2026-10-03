"""The keys the panel reads from a scan response must be keys the scan emits.

`write_rollup` was computed by `annotate()` and then dropped before the result
left the backend, while the panel has passed `data.write_rollup` to
`displayResults` at six call sites since 1.7.11. Every one of them read
`undefined`, the per-area write line could never render, and nothing said so:
the panel degrades quietly by design, and the backend had no idea what the
panel asked for.

The general shape of the bug is a name in one file with no counterpart in the
other. This checks the one call the scan result goes through, and it checks the
backend by looking for the key in the dict the scan actually returns - not in a
comment, and not in a local variable of the same name, which is how the key
looked present for a release and a half.
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

# ── the dict the scan actually returns ──────────────────────────────────────
# Found by walking the return statement of the function that builds the scan
# result, so a key that only appears in a comment or in a local cannot pass.
tree = ast.parse(scanner_src)
returned: set[str] = set()
for node in ast.walk(tree):
    if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
        continue
    for k in node.value.keys:
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            returned.add(k.value)
    # `**something` contributes keys we cannot see; a result that spreads is
    # not one to assert a fixed key set against.
    if any(k is None for k in node.value.keys):
        returned.add("*spread*")

check("a returned dict literal was found to check against",
      "results" in returned or "*spread*" in returned,
      f"only these literal keys were found: {sorted(returned)[:8]}")

missing = sorted(read_keys - returned)
for key in missing:
    print(f"  FAIL  the panel reads .{key} and no returned dict emits it")
check("every key the panel reads is emitted by the scan",
      not missing, f"missing: {', '.join(missing)}")

# The specific one, and the shape the panel indexes into.
rollup = re.search(r'"write_rollup"\s*:\s*([^,\n]+)', scanner_src)
check("write_rollup carries the per-area numbers, not a scalar",
      rollup is not None and "by_area" in rollup.group(1),
      f"got {rollup.group(1).strip() if rollup else 'nothing'}")
panel_uses_area = re.search(r"writeRollup\s*\|\|\s*{}\s*\)\s*\[", panel)
check("and the panel indexes it by area name",
      panel_uses_area is not None,
      "the panel's lookup shape changed, so this check is pinning the wrong thing")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
