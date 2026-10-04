"""Counter-proof for test_purge_safety.py.

The hole it exists for: this file guards the permanent, irreversible deletion of
entities, and several of its assertions were satisfied by a COMMENT rather than
by code. `_fn_source` returns `ast.get_source_segment`, which hands back the raw
lines with their comments intact, so `"async_add_soft_deleted(entity_ids)" in
_soft` was true whether the call was live or commented out - and so was
`result.get("untracked")`, `on_left_disabled=_record`, `still_tracked` and the
whole-file `count("_record_if_callbacked(")`.

A commented-out guard and a live one are indistinguishable to a text match, and
the guard is the whole point: an entity disabled with no trash record cannot be
restored by this integration or by anything else.

So each case comments the guarded construct out - or, for the engine, swaps a
live call for a comment that names it so the text count is unchanged - and
requires the check to go red AND name the guard that went missing. Each case also
asserts the pattern the OLD assertion matched on is still present in the mutated
file: if the comment did not textually satisfy the old check, the case would be
proving nothing about the hole.

The last case is the control: a tree with nothing wrong in it still passes, so
"red" above means something. That is also what reverting the fix looks like.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
INIT = "custom_components/ha_optimizer/__init__.py"
ENGINE = "custom_components/ha_optimizer/purge_engine.py"
TEST = "tools/test_purge_safety.py"

# 1. The soft purge must write the trash record BEFORE the engine disables
#    anything. Commenting the call out leaves the comment saying the same words.
SOFT_RECORD = ("            await store.async_add_soft_deleted(entity_ids)\n",
               "            # await store.async_add_soft_deleted(entity_ids)\n")
OLD_TEXT_1 = "async_add_soft_deleted(entity_ids)"

# 2. The hard-delete path must hand the engine a callback that records each
#    entity it leaves disabled.
CALLBACK = ("                entity_ids, soft_delete=False, on_left_disabled=_record,\n",
            "                entity_ids, soft_delete=False,  # on_left_disabled=_record,\n")
OLD_TEXT_2 = "on_left_disabled=_record"

# 3. The `still_tracked` list has to be BRANCHED ON, not merely assigned: the
#    assignment stays, the source still says `still_tracked` seven times, and
#    the warning about what could not be removed stops being logged. The `if` is
#    disabled in place rather than commented out, because commenting its first
#    line leaves the body over-indented and hands the check an IndentationError
#    instead of a failed guard - a crash is not a finding.
BRANCH = ("    if still_tracked:\n"
          "        _LOGGER.warning(\n",
          "    if False and still_tracked:   # the name is still here; nothing runs\n"
          "        _LOGGER.warning(\n")
OLD_TEXT_3 = "still_tracked"

# 4. A live engine call replaced by a comment naming it: the whole-file text
#    count stays at 3 and the old check stays green, while the number of paths
#    that leave an entity disabled drops from three to two.
#
#    The anchor carries the `outcome == "failed"` branch above it because the
#    `disabled_only` + `_record_if_callbacked` pair now appears at more than one
#    site. It refused to run when the anchor matched twice and said so, which is
#    the correct way round - a counter-proof that guessed which occurrence to
#    comment out would be testing a different thing than it claims.
SITE = ('                            if outcome == "failed":\n'
        '                                results.setdefault("not_disabled", []).append(\n'
        '                                    entity_id\n'
        '                                )\n'
        '                            results["disabled_only"].append(entity_id)\n'
        "                            await _record_if_callbacked(\n"
        "                                on_left_disabled, entity_id, results)\n",
        '                            if outcome == "failed":\n'
        '                                results.setdefault("not_disabled", []).append(\n'
        '                                    entity_id\n'
        '                                )\n'
        '                            results["disabled_only"].append(entity_id)\n'
        "                            # await _record_if_callbacked(\n"
        "                            #     on_left_disabled, entity_id, results)\n")
OLD_TEXT_4 = "_record_if_callbacked("

# The text assertions these replaced, verbatim, so a reviewer can see what used
# to be asked. None of them is asked any more.
TEXT_ASSERTS = [
    '"persistent_notification.async_create" in expiry',
    '"_LOGGER.warning(" in expiry',
    '"still_tracked" in expiry',
    '\'result.get("untracked")\' in _purge_body',
    '"on_left_disabled=_record" in _purge_body',
    '.count("_record_if_callbacked(") >= 3',
    '"async_add_soft_deleted(entity_ids)" in _soft',
    '"async_remove_soft_deleted(sorted(refused))" in _soft',
]

CASES = [
    ("the soft-purge batch record commented out in handle_purge",
     [(INIT, *SOFT_RECORD)],
     "a soft purge records the batch before it disables anything", OLD_TEXT_1, INIT),
    ("the per-entity trash-record callback commented out in handle_purge",
     [(INIT, *CALLBACK)],
     "purge service keeps disabled_only entities in the trash", OLD_TEXT_2, INIT),
    ("the `if still_tracked:` logging guard disabled in place, assignment left live",
     [(INIT, *BRANCH)],
     "entities that could not be removed stay tracked", OLD_TEXT_3, INIT),
    ("one live engine call site replaced by a comment naming it",
     [(ENGINE, *SITE)],
     "the engine records every path that leaves an entity disabled", OLD_TEXT_4, ENGINE),
    ("control: an unmutated tree is still reported as passing",
     [],
     "", "", INIT),
]


def build(t: Path, patches: list) -> None:
    shutil.copytree(ROOT / "custom_components", t / "custom_components")
    shutil.copytree(ROOT / "tools", t / "tools")
    for sub in ("custom_components", "tools"):
        for pc in (t / sub).rglob("__pycache__"):
            shutil.rmtree(pc, ignore_errors=True)
    for rel, old, new in patches:
        p = t / rel
        raw = p.read_bytes().decode("utf-8")
        crlf = "\r\n" in raw
        text = raw.replace("\r\n", "\n")
        n = text.count(old)
        if n != 1:
            raise AssertionError(
                f"the patch for {rel} matched {n} times, not once:\n"
                f"  anchor: {old[:90]!r}\n"
                f"  -> a patch that does not apply is a test that cannot fail")
        out = text.replace(old, new, 1)
        p.write_bytes((out.replace("\n", "\r\n") if crlf else out).encode("utf-8"))


missed = 0
for label, patches, guard, old_text, watch_file in CASES:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        try:
            build(t, patches)
        except AssertionError as exc:
            print(f"FAIL  {label} - the patch did not apply")
            print("      " + str(exc).replace("\n", "\n      ")[:220])
            missed += 1
            continue
        expect_red = bool(patches)
        # The demonstration is void unless the comment still satisfies the text
        # assertion the old check made: that is the whole claim being tested.
        text_still_there = True
        if old_text:
            mutated = (t / watch_file).read_bytes().decode("utf-8")
            text_still_there = old_text in mutated
            if not text_still_there:
                print(f"FAIL  {label} - the comment does not even contain "
                      f"{old_text!r}, so the old text check would have caught it "
                      f"anyway; this case proves nothing")
                missed += 1
        r = subprocess.run([sys.executable, str(t / "tools" / "test_purge_safety.py"), str(t)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        crashed = "Traceback" in out
        red = r.returncode != 0
        named = bool(guard) and any(guard in ln and "FAIL" in ln for ln in out.splitlines())
        bad = crashed or red != expect_red or (guard and not named) or not text_still_there
        print(f"{'FAIL' if bad else 'ok  '}  {label}")
        print(f"      exit={r.returncode}  expect_red={expect_red}  "
              f"old_text_still_present={text_still_there}  named={guard!r}->{named}")
        for ln in out.splitlines():
            if ln.strip().startswith("FAIL"):
                print("      | " + ln.strip()[:150])
        if crashed:
            print("      | the check itself crashed: " + out.strip().splitlines()[-1][:110])
        elif red != expect_red:
            print("      | expected " + ("red" if expect_red else "green")
                  + " and got the other")
        elif guard and not named:
            print("      | went red but never named: " + guard)
        if bad:
            missed += 1

print()
print("  the text assertions this file no longer makes, all of which a comment "
      "could satisfy:")
for line in TEXT_ASSERTS:
    print("    " + line)
print()
print("FAILED" if missed else
      "PASSED: a commented-out guard is red, named, and no longer readable as code")
sys.exit(1 if missed else 0)
