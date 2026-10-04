"""A translation that reads a parameter must be called with the shape it reads.

`t(key, params)` does one of two things, and which one it is depends on how the
key is defined:

    if (typeof val === 'function') return val(params);          -> (p) => `...${p.n}`
    if (params && typeof val === 'string') val = val.replace(   -> '...{n}...'
        /\{(\w+)\}/g, (_, k) => params[k] ...)

Both read a PROPERTY off the second argument. A definition written `(h) =>
`${h}`` takes a scalar; one written `(p) => `${p.why}`` or plain `'{n} entities'`
takes an object. Getting it wrong renders `[object Object]h`, or the literal text
`{n}`, in the middle of a translated page, and both have shipped here:
`fpPartialDay` was defined for a scalar and called with an object.

The previous version of this check only knew the first definition form, because
its definition rule was anchored on `=> \``. The second form - a plain string
whose `{name}` placeholders are substituted at call time - was invisible, so
every call to one of those keys was skipped silently: `shapes.get(key)` returned
nothing and the loop moved on. Nine keys were called that way, and a wrong-arity
call on any of them was not a finding, it was nothing at all. A check that skips
what it cannot see is a check that reports whatever it happens to find.

So the definitions are read from the translation table itself, and both forms
count, and a call to a key the table does not define is now reported as unseen
rather than passed over.

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


# ── the translation table, and nothing else ─────────────────────────────────
# A definition is only a definition if it is a row of the table. Anchoring here
# is also what keeps a lookalike `{ n: '{x}' }` elsewhere in the panel from being
# read as a translation.
TABLE = re.compile(r"(?m)^const I18N = \{\s*$")
TABLE_END = re.compile(r"(?m)^(?:const|let|var|function|class)\b")
table = TABLE.search(js)
end = TABLE_END.search(js, table.end()) if table else None
check("the translation table was located", table is not None and end is not None,
      "no `const I18N = {` block followed by a top-level declaration, so there is "
      "nothing to read definitions from")
region = js[table.end():end.start()] if (table and end) else ""

# Every shape below is read off `t()` itself. If t() stops substituting {name}
# from the params object, "this definition takes an object" stops being true and
# the whole check would be asserting something that is no longer the case.
T_IMPL = re.search(r"(?m)^function t\(\s*key\s*,\s*params\s*\)\s*\{[\s\S]*?^\}", js)
t_body = T_IMPL.group(0) if T_IMPL else ""
check("t() still calls a function value with params and fills {name} from params",
      T_IMPL is not None and "val(params)" in t_body and "params[" in t_body,
      "t(key, params) no longer has the shape this check reasons about, so every "
      "verdict below would be about a function that is not the one in the panel")

# ── the definitions ────────────────────────────────────────────────────────
# Form 1: a function value. Its parameter is either read whole (scalar) or read
# as a property (object).
DEF_FN = re.compile(
    r"(?m)^[ \t]*([A-Za-z_$][\w$]*)[ \t]*:[ \t]*\([ \t]*([A-Za-z_$][\w$]*)[ \t]*\)"
    r"[ \t]*=>[ \t]*`([^`]*)`")
# Form 2: a plain string holding {name} placeholders, which t() fills from
# params[name] - so it reads a property, and its shape is an object too.
DEF_STR = re.compile(r"(?m)^[ \t]*([A-Za-z_$][\w$]*)[ \t]*:[ \t]*'([^'\n]*)'[ \t]*,?[ \t]*$")
PLACEHOLDER = re.compile(r"\{(\w+)\}")

# Every key is defined TWICE, once per language, and a single dict keyed by name
# silently kept only the second. That made the check blind to a change applied
# to one language: the other definition agreed with the call site and won. So
# the shapes are collected as a SET, and a key whose two definitions disagree is
# itself a finding - the two languages would render the same key differently.
shapes: dict[str, set[str]] = {}
defined: set[str] = set()
for key, param, body in DEF_FN.findall(region):
    defined.add(key)
    shapes.setdefault(key, set()).add(
        "object" if re.search(rf"\b{re.escape(param)}\s*\.", body) else "scalar")
for key, body in DEF_STR.findall(region):
    defined.add(key)
    if PLACEHOLDER.search(body):
        shapes.setdefault(key, set()).add("object")

check("the table has function-valued translations to check", bool(shapes),
      "none matched, so the check would pass without testing anything")
check("the table has placeholder-string translations too",
      any(key in shapes for key, _ in DEF_STR.findall(region)),
      "no `key: '...{name}...'` row matched, so the call form this check used to "
      "miss is not being exercised at all")

# ── the call sites: a literal argument has a knowable shape ────────────────
# The key may be written in single quotes, double quotes or backticks. Anchoring
# on one of them is the same hole as anchoring the definition on one of them.
CALL = re.compile(r"(?<![\w$.])t\(\s*(?:'([^']*)'|\"([^\"]*)\"|`([^`]*)`)\s*,")
OBJ_ARG = re.compile(r"^\s*\{")
SCALAR_ARG = re.compile(r"^\s*(?:'[^']*'|\"[^\"]*\"|`[^`]*`|-?\d|true\b|false\b|null\b)")

mismatches: list[str] = []
unseen: list[str] = []
seen_calls = 0
for key, variants in sorted(shapes.items()):
    if len(variants) > 1:
        mismatches.append(
            f"{key}: the two languages define it with different shapes "
            f"({', '.join(sorted(variants))}) - the same key would render "
            f"differently depending on the language")

for m in CALL.finditer(js):
    key = m.group(1) or m.group(2) or m.group(3)
    if key not in defined:
        # Not a shape problem: a blindness problem, and the reason this check
        # was quiet about nine keys for a release. A call the table cannot
        # answer for is reported, not skipped.
        unseen.append(f"{key}: called with an argument, but the table does not "
                      f"define it - its shape cannot be checked")
        continue
    variants = shapes.get(key)
    if not variants or len(variants) != 1:
        continue
    shape = next(iter(variants))
    tail = js[m.end():]
    depth, i = 0, 0
    while i < len(tail):                 # the argument may be an object literal
        c = tail[i]
        if c in "{(":
            depth += 1
        elif c in "})":
            if depth == 0:
                break
            depth -= 1
        elif c in "'\"`":
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
for line in unseen:
    print(f"  FAIL  {line}")
check("no call site is invisible to this check", not unseen,
      f"{len(unseen)} call site(s) the table cannot answer for")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed, {seen_calls} literal call site(s) checked")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
