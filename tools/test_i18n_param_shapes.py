"""A function-valued translation must be called with the shape its body expects.

`t(key, params)` passes `params` straight into the function: `val(params)`. So a
definition written `(h) => \`${h}...\`` takes a scalar, and one written
`(p) => \`${p.why}\`` takes an object - and nothing checks which. Getting it
wrong renders `[object Object]h` or `undefined` in the middle of a translated
page, and both have shipped here: `fpPartialDay` was defined for a scalar and
called with an object.

Literal call sites are checked. A call whose argument is a variable is skipped,
because a variable can legitimately be either shape and guessing would be a
false positive - and a check that reports problems it does not have is worse
than no check.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
PANEL = ROOT / "custom_components" / "ha_optimizer" / "panel.html"

src = PANEL.read_text(encoding="utf-8")
code = re.sub(r"<style[\s\S]*?</style>", " ", src)
scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", code)
js = "\n".join(scripts)

results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


# ── the definitions: key -> does its single parameter get a property read? ──
DEF = re.compile(
    r"^\s*([A-Za-z_$][\w$]*)\s*:\s*\(\s*([A-Za-z_$][\w$]*)\s*\)\s*=>\s*`([^`]*)`",
    re.M)
# Every key is defined TWICE, once per language, and a single dict keyed by name
# silently kept only the second. That made the check blind to a change applied
# to one language: the other definition agreed with the call site and won. So
# the shapes are collected as a SET, and a key whose two definitions disagree is
# itself a finding - the two languages would render the same key differently.
shapes: dict[str, set[str]] = {}
for key, param, body in DEF.findall(js):
    shape = "object" if re.search(rf"\b{re.escape(param)}\s*\.", body) else "scalar"
    shapes.setdefault(key, set()).add(shape)

check("the panel has function-valued translations to check", bool(shapes),
      "none matched, so the check would pass without testing anything")

# ── the call sites: a literal argument has a knowable shape ────────────────
CALL = re.compile(r"\bt\(\s*'([A-Za-z_$][\w$]*)'\s*,")
OBJ_ARG = re.compile(r"^\s*\{")
SCALAR_ARG = re.compile(r"^\s*(?:'[^']*'|\"[^\"]*\"|-?\d|true\b|false\b|null\b)")

mismatches: list[str] = []
seen_calls = 0
for key, variants in sorted(shapes.items()):
    if len(variants) > 1:
        mismatches.append(
            f"{key}: the two languages define it with different shapes "
            f"({', '.join(sorted(variants))}) - the same key would render "
            f"differently depending on the language")

for m in CALL.finditer(js):
    key = m.group(1)
    variants = shapes.get(key)
    if not variants or len(variants) != 1:
        continue
    shape = next(iter(variants))
    tail = js[m.end():]
    depth, i = 0, 0
    while i < len(tail):                 # the argument may be an object literal
        c = tail[i]
        if c == "{":
            depth += 1
        elif c == "}":
            if depth == 0:
                break
            depth -= 1
        elif c in "'\"":
            q, i = c, i + 1
            while i < len(tail) and tail[i] != q:
                i += 2 if tail[i] == "\\" else 1
        elif depth == 0 and c in ";\n":
            break
        i += 1
    arg = tail[:i]
    if OBJ_ARG.match(arg):
        given = "object"
    elif SCALAR_ARG.match(arg):
        given = "scalar"
    else:
        continue                          # a variable: either shape is possible
    seen_calls += 1
    if given != shape:
        mismatches.append(
            f"{key}: the body needs {shape}, but the call passes {given}")

check("the check examined at least one literal call site", seen_calls > 0,
      "no call site could be resolved, so nothing was tested")
for line in mismatches:
    print(f"  FAIL  {line}")
check("every literal call matches the shape its definition reads",
      not mismatches, f"{len(mismatches)} mismatch(es) above")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed, {seen_calls} literal call site(s) checked")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
