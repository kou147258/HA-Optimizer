"""Counter-proof for test_trace_join.py: put each of the four wrong field
assumptions back, and the check must go red.

These are not invented. Each one is a defect this feature actually shipped, and
the last one is the one that would have made the next release a 500: the
diagnosis table was a tuple of patterns, the code called `.split("|")` on it,
and nothing reached that line until the joining bug above it was fixed.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
TARGET = "automation_runs.py"

CASES = [
    # 1.7.22's shape: the error read off a reason string, guarded by an
    # isinstance check that turned every real failure into "no error".
    ("the error is read from script_execution again (a string, not a dict)",
     '    error = trace_dict.get("error")\n    reason = trace_dict.get("script_execution")',
     '    reason = trace_dict.get("script_execution")\n'
     '    execution = reason if isinstance(reason, dict) else {}\n'
     '    error = execution.get("error")',
     "the error text is read from the top level"),

    # The defect that emptied the page for four releases: joining on the
    # entity_id tail, which is the alias and not the automation's id.
    ("the join goes back to splitting the key instead of using unique_id",
     "        entity_id = by_unique.get(str(item)) or by_tail.get(str(item))",
     "        entity_id = by_tail.get(str(item))",
     "a run files under its unique_id even when the entity_id differs"),

    # "stopped" is what every finished run sets, so treating it as an early
    # exit turns every success into something else.
    ('"stopped" is read as an early exit again',
     "        outcome = OUTCOME_ABORTED if reason == EXEC_ABORTED else OUTCOME_OK",
     "        outcome = OUTCOME_ABORTED",
     "a completed run is ok"),

    # A trigger that declined to fire is not a run.
    ("not-triggered records are counted as runs again",
     "    if not_triggered:\n        outcome = OUTCOME_NOT_TRIGGERED",
     "    if False:\n        outcome = OUTCOME_NOT_TRIGGERED",
     "a not-triggered record is not a run"),

    # The rule table shape that turned a failing automation into a 500. It is
    # injected as the code, not as the data: an earlier attempt changed one
    # rule's table and the test still passed, because another rule matched
    # first and the broken one was never reached. A counter-proof that tests
    # nothing is worse than none - it looks like coverage.
    ("the diagnosis loop is the old .split() on a tuple again",
     "            for pattern in rule[\"match\"]:\n"
     "                m = re.search(pattern, low)\n"
     "                if m:\n"
     "                    return {\"id\": rule[\"id\"], \"suggestion\": rule[\"suggestion\"],\n"
     "                            \"matched\": m.group(0), \"pattern\": pattern,\n"
     "                            \"error\": error_text}",
     "            for needle in rule[\"match\"].split(\"|\"):\n"
     "                if needle in low:\n"
     "                    return {\"id\": rule[\"id\"], \"suggestion\": rule[\"suggestion\"],\n"
     "                            \"matched\": needle, \"error\": error_text}",
     "automation_runs.py"),

    # Coverage that reports a verdict without saying what it saw. The first
    # version of this case deleted the numbers from the report and the check
    # stayed green, because it grepped for the strings "buckets" and "runs",
    # which also appear on the lines that assign them. The check now runs the
    # reader, so falsifying the number is what turns it red.
    ("the read under-reports how many runs it saw",
     '    observed["runs"] = len(traces or [])',
     '    observed["runs"] = 0',
     "coverage counts the runs it read back"),

    ("the read stops counting the trace buckets",
     '            observed["buckets"] = len(keys)',
     '            observed["buckets"] = 0',
     "coverage counts the trace buckets it can see"),
]

missed = 0
for label, old, new, signal in CASES:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        shutil.copytree(ROOT / "custom_components", t / "custom_components")
        shutil.copytree(ROOT / "tools", t / "tools")
        for pc in (t / "custom_components").rglob("__pycache__"):
            shutil.rmtree(pc, ignore_errors=True)

        p = t / "custom_components" / "ha_optimizer" / TARGET
        src = p.read_text(encoding="utf-8")
        if src.count(old) != 1:
            print(f"FAIL  {label} — anchor appears {src.count(old)} times, "
                  f"so the case tested nothing")
            missed += 1
            continue
        p.write_text(src.replace(old, new, 1), encoding="utf-8", newline="")

        r = subprocess.run([sys.executable, str(t / "tools" / "test_trace_join.py"), str(t)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = r.stdout + r.stderr
        if "Traceback" in out:
            # Whose stack it is decides whether this is a catch or a failure.
            # A traceback whose DEEPEST frame is the module under test is the
            # defect being reported loudly, which is the whole point - the
            # 1.7.23 fix for the 500 was this same AttributeError, and the only
            # way to see it was to run the code. A traceback whose deepest
            # frame is the CHECK is the check being broken, which reports
            # nothing and counts for nothing.
            frames = re.findall(r'File "([^"]+)"', out)
            deepest = frames[-1] if frames else ""
            if deepest.endswith("automation_runs.py"):
                print(f"ok    {label} -> the module raised, loudly, and the test saw it")
            else:
                print(f"FAIL  {label} — the test itself crashed, deepest frame {deepest}")
                print("      " + out.strip().splitlines()[-1])
                missed += 1
        elif r.returncode == 0:
            print(f"FAIL  {label} — the check still passed")
            missed += 1
        elif signal not in out:
            print(f"FAIL  {label} — went red but never said why; expected {signal!r}")
            missed += 1
        else:
            print(f"ok    {label}")
            for ln in out.splitlines():
                if signal in ln and "FAIL" in ln:
                    print("      " + ln.strip())

print()
print("FAILED" if missed else "PASSED: every injected defect was caught, and named")
sys.exit(1 if missed else 0)
