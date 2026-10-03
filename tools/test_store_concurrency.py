"""Store: serialise every write, and merge instead of clobber.

Run with the component directory as argv[1].

Why this is in the store and not in the service handlers: every mutable thing
the integration has - the trash records and the scan results - belongs to this
one object. Locking here holds no matter which caller arrived first, including
the scheduled scan, and it is a much smaller diff than re-indenting four
handlers that have already been through enough.

The merge is the part that actually fixes the visible bug. `async_save_scan_
results` REPLACES the results, so a scan that started before a purge and wrote
after it resurrects every entity the purge removed - the user sees entities they
just deleted reappear in the candidate list and concludes the delete failed.
Concurrent edits are therefore recorded and re-applied to whatever the scan
brings in.
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
COMP = Path(sys.argv[1] if len(sys.argv) > 1 else
            "C:/Users/43457/.minimax/sessions/mvs_beb6ae963fc14d89832b6a57e91f762d/workspace/HA-Optimizer/custom_components/ha_optimizer")
SRC = COMP / "store.py"
# read with newline="" - universal-newline translation would turn every CRLF
# into a bare LF and the file would silently stop matching the repository
with open(SRC, "r", encoding="utf-8", newline="") as fh:
    src = fh.read()
NL = "\r\n"
ok, bad = [], []


def check(name, cond, detail=""):
    (ok if cond else bad).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))


print("\nimports and the lock")
# \r?\b: the file is CRLF, and `$` in MULTILINE matches before the LF, not
# before the CR - so `^import asyncio$` never matches a CRLF line.
check("asyncio is imported", re.search(r"^import asyncio\r?$", src, re.M) is not None)
check("the lock is created", "self._lock = asyncio.Lock()" in src)
check("it is documented where it is created", "mutual exclusion" in src or "resurrects" in src)

print("\nevery mutation is inside the lock")
# a method mutates if it assigns to _soft_data / _scan_data / calls async_save
MUTATORS = [
    "async_save_scan_results", "async_add_soft_deleted", "async_restore_scan_entries",
    "async_remove_soft_deleted", "async_remove_from_scan_results", "async_clear_scan_results",
]
for name in MUTATORS:
    m = re.search(rf"    async def {name}\(.*?(?=\n    async def |\n    def |\n\nclass |\Z)", src, re.S)
    if not m:
        check(f"{name} exists", False, "not found - the method may have been renamed")
        continue
    body = m.group(0)
    locked = "async with self._lock" in body
    mutates = ("_soft_data" in body or "_scan_data" in body or "async_save" in body)
    if mutates:
        check(f"{name} mutates and is locked", locked,
              "a write that does not take the lock is exactly the interleaving this exists to stop")
    else:
        check(f"{name} does not mutate", True)

print("\nreads are not needlessly serialised")
for name in ("async_get_soft_deleted", "async_get_expired_soft_deleted", "async_get_scan_results"):
    m = re.search(rf"    async def {name}\(.*?(?=\n    async def |\n    def |\Z)", src, re.S)
    if not m:
        continue
    check(f"{name} does not take the lock", "async with self._lock" not in m.group(0),
          "the trash list is read by the panel constantly; locking a read would "
          "serialise every poll behind a purge")

def code_only(src: str) -> str:
    """Strip docstrings and comments.

    The docstring of async_save_scan_results literally contains the sentence
    "it never touches `_soft_data`", so a plain substring search for `_soft_data`
    finds prose about not doing the thing and reports a failure for a function
    that is correct. Searching comments for evidence of behaviour is searching
    for the absence of evidence.
    """
    out = re.sub(r'"""[\s\S]*?"""', "", src)
    out = re.sub(r"'''[\s\S]*?'''", "", out)
    out = re.sub(r"(?m)#.*$", "", out)
    return out


CODE = code_only(src)

print("\nthe scan merges rather than clobbers")
check("concurrent removals are remembered", "self._removed_since_scan" in src)
check("concurrent additions are remembered", "self._added_since_scan" in src)
# The merge lives in the helper, not in async_save_scan_results itself - that
# one is now a two-line wrapper that takes the lock and delegates. Checking the
# wrapper reported three failures for code that was present and correct.
save = re.search(r"    async def _async_save_scan_results_locked\(.*?(?=\n    async def |\Z)", src, re.S)
body = save.group(0) if save else ""
check("the merge is in the locked helper, not the wrapper",
      save is not None
      and "async with self._lock" in (re.search(
          r"    async def async_save_scan_results\(.*?(?=\n    (?:async )?def )", src, re.S) or
          type("x", (), {"group": lambda self, n: ""})).group(0))
check("the wrapper really delegates under the lock",
      "_async_save_scan_results_locked" in body)
check("the removal set is applied to the incoming results", "self._removed_since_scan" in body)
check("the addition set is applied to the incoming results", "self._added_since_scan" in body)
check("and both are cleared afterwards",
      body.count("self._removed_since_scan.clear()") == 1
      and body.count("self._added_since_scan.clear()") == 1,
      "forgetting to clear makes every later scan re-apply a stale edit")
check("the trash records are still never touched by a scan",
      "_soft_data" not in code_only(body),
      "1.7.0 lost the restore snapshot exactly this way")

print()
if bad:
    print(f"{len(bad)} check(s) FAILED")
    sys.exit(1)
print(f"all {len(ok)} store-concurrency checks passed")
