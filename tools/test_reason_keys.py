"""A scan reason must be a key, never a sentence.

The panel prints whatever a reason carries, and it can only translate a key.
One site in scanner.py spelled its sentence out - `f"Suspicious name: '{pattern}'"`
- and it reached a Chinese panel as English, sitting next to translated
neighbours, which is the shape that makes it look like the panel is half
translated rather than broken.

So the invariant is structural rather than a search for one string: every value
appended to a `reasons` list is either a key that exists in BOTH panel tables,
or a dict with a `key` that does. A literal sentence fails, and says what it
found.

It reads the AST, so a string built by concatenation or an f-string is caught
the same way as a plain one - the earlier version of this rule was a grep, and
an f-string is the obvious way to slip past a grep.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
COMP = ROOT / "custom_components" / "ha_optimizer"
PANEL = COMP / "panel.html"

results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


_dict = json.loads(subprocess.run(
    ["node", str(ROOT / "tools" / "_panel_dict.js"), str(PANEL)],
    capture_output=True, text=True, encoding="utf-8", check=True).stdout)["dict"]
LANGS = sorted(_dict)


class ReasonsVisitor(ast.NodeVisitor):
    """Finds every `reasons.append(...)` and reports what it appended."""

    def __init__(self) -> None:
        self.found: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "append"
                and isinstance(f.value, ast.Name) and f.value.id == "reasons"
                and node.args):
            self.found.append((node.lineno, ast.unparse(node.args[0])))
        self.generic_visit(node)


bad: list[str] = []
used_keys: set[str] = set()
for path in sorted(COMP.glob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    v = ReasonsVisitor()
    v.visit(tree)
    for lineno, expr in v.found:
        rel = path.relative_to(ROOT)
        node = ast.parse(expr, mode="eval").body
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            key = node.value
            used_keys.add(key)
            if not all(key in _dict[lang] for lang in LANGS):
                bad.append(f"{rel}:{lineno} appends {key!r}, which is not a key "
                           f"in every language table")
        elif isinstance(node, ast.Dict):
            # The VALUE of the "key" entry, not the name of the entry. Reading
            # the key half instead reports every dict as `appends key 'key'`,
            # which is a finding about this file and not about the panel.
            k = next((ast.literal_eval(v)
                      for kk, v in zip(node.keys, node.values)
                      if isinstance(kk, ast.Constant) and kk.value == "key"), None)
            if isinstance(k, str):
                used_keys.add(k)
                if not all(k in _dict[lang] for lang in LANGS):
                    bad.append(f"{rel}:{lineno} appends key {k!r}, which is not "
                               f"in every language table")
            else:
                bad.append(f"{rel}:{lineno} appends a dict with no 'key' - the "
                           f"panel has nothing to translate: {expr[:60]}")
        else:
            # an f-string or a concatenation: a sentence, and therefore English
            bad.append(f"{rel}:{lineno} appends a computed value, not a key: "
                       f"{expr[:70]}")

for b in bad:
    print(f"  FAIL  {b}")
check("every scan reason is a key the panel can translate", not bad,
      f"{len(bad)} occurrence(s)")

# A key nobody uses is a translation that can rot with nobody noticing; a key
# used but undefined is the reason above. Only the first is worth reporting.
panel_keys = set(_dict[LANGS[0]])
check("the reason keys the scanner sends are all real",
      used_keys <= panel_keys,
      f"not in the table: {sorted(used_keys - panel_keys)}")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
