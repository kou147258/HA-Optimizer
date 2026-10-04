"""A diagnosis has to be translatable, and it has to be translated.

The seven common-cause explanations were English prose in the backend, and the
panel printed `diag.suggestion` verbatim. A Chinese user got a Chinese label
followed by an English paragraph, on every failing automation, every time.

The backend now sends a key and the panel renders it through `t()`. That is only
better if every key is actually there in every language - and `t('...')` with a
DYNAMIC argument is precisely the call site no static shape check can read, so
this file is the thing that has to cover it.

It also pins the part that went wrong on the way: when the rules were changed
from a prose field to a key, `diagnose()` went on reading `rule["suggestion"]`.
That is a KeyError on the first classified failure - the automation tab would
have died with the same bare HTTP 500, on the page the user was already reading.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
COMP = ROOT / "custom_components" / "ha_optimizer"
PANEL = (COMP / "panel.html").read_text(encoding="utf-8")

results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


# ── the real module, with its two Home Assistant imports stubbed ───────────
def load() -> dict:
    src = (COMP / "automation_runs.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    src = src.replace("from homeassistant.core import HomeAssistant", "HomeAssistant = object")
    src = src.replace("from homeassistant.helpers import entity_registry as er", "er = None")
    ns: dict = {}
    exec(compile(src, "automation_runs.py", "exec"), ns)   # noqa: S102
    return ns


ns = load()
diagnose = ns["diagnose"]
RULES = ns["DIAGNOSES"]

# One error per rule, each written to match THAT rule and not an earlier one.
# The rules are tried in order, so an error that also matches rule 0 would come
# back labelled template and the test would quietly pass on the wrong key.
ERRORS = {
    "template": "Template variable error: 'x' is undefined",
    "entity_missing": "Action light.turn_on not found",
    "unavailable": "Entity sensor.x is unavailable",
    "auth": "Unauthorized",
    "missing_key": "required key not provided @ data['item']",
    "timeout": "Timeout waiting for the step",
    "no_response": "no response from the device",
}

check("there are diagnosis rules to check", bool(RULES), "none found")
check("every rule has a key", all("key" in r for r in RULES),
      f"missing on: {[r.get('id') for r in RULES if 'key' not in r]}")
check("no rule carries user-facing prose any more",
      not any("suggestion" in r for r in RULES),
      f"still present on: {[r.get('id') for r in RULES if 'suggestion' in r]}")

# ── every rule fires, and returns a key the panel can translate ────────────
for rule in RULES:
    rid = rule.get("id", "<no id>")
    err = ERRORS.get(rid)
    check(f"a known error exists for rule `{rid}`", err is not None)
    if not err or "key" not in rule:
        # No key means there is nothing to translate or to compare against, and
        # the check above has already said so. Carrying on and reading
        # `rule["key"]` anyway would turn a reportable defect into a traceback -
        # and a check that crashes is a check that reports nothing.
        continue
    d = diagnose(err)
    check(f"`{rid}` is the rule that matches its own error",
          d is not None and d.get("id") == rid,
          f"got {d.get('id') if d else None!r} instead")
    if not d:
        continue
    check(f"`{rid}` returns a key and not a suggestion",
          d.get("key") == rule["key"] and "suggestion" not in d,
          f"got key={d.get('key')!r}, suggestion={d.get('suggestion')!r}")
    n = PANEL.count(f"    {rule['key']}: ")
    check(f"`{rule['key']}` is defined in both languages", n == 2,
          f"found {n} definition(s) in panel.html; the panel needs one per language")
    # A definition with no body is a key that renders as nothing at all.
    body = re.search(rf"^    {re.escape(rule['key'])}: '(.*)',$", PANEL, re.M)
    check(f"`{rule['key']}` has text, not an empty string",
          bool(body) and len(body.group(1)) > 20, body.group(1)[:40] if body else "absent")

# ── the unclassified branch says nothing, and says it safely ───────────────
for label, arg in (("an empty error", ""), ("None", None), ("a number", 7)):
    d = diagnose(arg)
    check(f"{label} classifies as nothing at all", d is None, repr(d))

d = diagnose("some error nobody wrote a rule for")
check("an unknown error is unclassified, not given a generic suggestion",
      d is not None and d["id"] == "unclassified", repr(d))
check("and carries no key, so the panel shows its own unclassified sentence",
      d is not None and d.get("key") == "" and "suggestion" not in d, repr(d))
check("and still reports the raw error, so it can be searched for",
      d is not None and d.get("error") == "some error nobody wrote a rule for", repr(d))

# ── the trace-behind string, which the panel calls with two arguments ─────
check("`autoTraceBehind` is defined in both languages", PANEL.count("    autoTraceBehind: ") == 2,
      f"found {PANEL.count('    autoTraceBehind: ')} definition(s)")
for param in ("{triggered}", "{stored}"):
    check(f"`autoTraceBehind` uses {param}", param in PANEL)
called = re.search(r"t\('autoTraceBehind',\s*\{([^}]*)\}", PANEL)
check("the panel passes both of the arguments it needs",
      called is not None and "triggered:" in called.group(1) and "stored:" in called.group(1),
      called.group(1) if called else "the call was not found")

# The panel must not still be reading the field that no longer exists.
check("the panel renders a key and not the old field",
      "diag.key" in PANEL and "diag.suggestion" not in PANEL,
      "the panel still reads diag.suggestion, which the backend no longer sends")

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
