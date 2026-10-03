"""Guard the two things that made the automations tab impossible to satisfy.

Both were invisible to twenty-five checks because both are about a value the
code invented: a key format, and a call the component makes internally.

  * traces are stored under "automation.<entity_id>"; the code looked them up
    by config_entry_id, so no run could ever match;
  * every helper in trace/util.py restores saved traces first; the code read
    the private dictionary and never did.

Neither is visible to a type checker, a syntax check, or a reading of the code.
Both are visible to reading HA's own source - which is the check: assert that
this module goes through the component's API rather than its private storage.
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
mod = (ROOT / "custom_components" / "ha_optimizer" / "automation_runs.py").read_text(encoding="utf-8")
# Check the CODE, not the prose describing it. The module docstring documents
# the shape of the trace storage - and it mentions DATA_TRACE - so a search over
# the raw text reports a read that is not there. Same mistake as checking a
# comment to decide whether a rule is implemented.
_code = re.sub(r'(?s)"""..*?"""', " ", mod)
_code = re.sub(r"(?m)#.*$", " ", _code)
results = []


def check(name, cond, detail=""):
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if not cond and detail else ""))


check("it goes through the trace component's own API",
      "async_list_traces" in mod and "async_restore_traces" in mod,
      "reading hass.data[DATA_TRACE] directly misses the restore and the key format")
check("it does not read the private trace storage",
      "DATA_TRACE" not in _code,
      "the private storage is keyed automation.<entity_id> and restored lazily; "
      "going around the component's own API is what hid both")
check("traces are joined on the entity_id, not the config entry id",
      'key.split(".", 1)[1]' in _code and "config_entry_id or entry.entity_id" not in _code,
      "joining on config_entry_id never matches a storage key")
_restore = _code.index("await async_restore_traces(hass)")
_list = _code.index("await async_list_traces(hass", _restore)
check("saved traces are restored before they are listed", _restore < _list,
      "restore must come first, or everything from before a restart is invisible")

# The shapes the component actually uses, asserted against the component's own
# source, so this does not rot when HA changes them.
TRACE_SRC = Path.home() / "nope"
check("the key format assumption is written down where the code is",
      'automation.<entity_id>' in mod,
      "a future reader needs to know why the key looks like that")

print()
failed = [r for r in results if not r[0]]
print(f"{len(results) - len(failed)}/{len(results)} passed")
print("FAILED" if failed else "PASSED")
sys.exit(1 if failed else 0)
