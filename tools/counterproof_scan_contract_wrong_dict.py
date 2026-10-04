"""Counter-proof for test_scan_contract.py: park the key in the wrong dict.

The existing counter-proof DELETES the key. That is not the hole this one is for.
The hole was that the check collected keys from every `return {...}` in the module
with `ast.walk`, so the key only had to be in *a* dict in the file. So this one
moves it: `"write_rollup"` is taken out of the dict `DataScanner.async_scan()`
returns - the only response the panel is ever handed - and dropped into
`ScanResult.to_dict()`, which the panel never sees. The response is now missing a
key the panel reads, and the file still contains the key in a returned dict.

Two trees, on purpose:
  A  the current check    - must go RED, and must name the file
  B  the pre-fix check    - must stay GREEN
B is what makes this a measurement of the rule rather than of the file: the same
injection, the same tree, only the rule differs. If B also went red, the injection
would be proving something else (a syntax error, a crash) and this file would be
lying to you. B comes from `git show HEAD:tools/test_scan_contract.py`; pass
`--old <path>` if that is not the pre-fix rule any more.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
CHECK = "test_scan_contract.py"

# Anchors are asserted to match exactly once each. An anchor that matched nothing
# would leave the tree unpatched, the check green, and this file reporting success
# for a defect that was never injected - which is the bug class this repo exists
# to stop. Scanner is CRLF, so the text is normalised to LF, patched, and written
# back as CRLF to leave the file's own line endings alone.
FROM = '            "write_rollup": write_rollup["by_area"],\n'
INTO = '            "measured": self.measured,\n'
SIGNAL = "every key the panel reads is emitted by the scan"
NAMES_FILE = "custom_components/ha_optimizer/scanner.py"


def old_check() -> str:
    # The pre-fix rule lives in `tools/prefix_rules/`, not in git history.
    # Reading it from `git show HEAD:tools/...` works right up until the fix is
    # committed, and after that HEAD holds the rule this file exists to prove is
    # insufficient - so the comparison below would have nothing left to compare
    # and the check would go red on every run from then on. A frozen copy cannot
    # rot that way. It is still compared against the current rule below, so
    # "updating" it to match is caught rather than quietly accepted.
    fixture = ROOT / "tools" / "prefix_rules" / CHECK
    if fixture.is_file():
        return fixture.read_text(encoding="utf-8")
    if len(sys.argv) > 2 and sys.argv[2] == "--old" and len(sys.argv) > 3:
        return Path(sys.argv[3]).read_text(encoding="utf-8")
    got = subprocess.run(["git", "show", f"HEAD:tools/{CHECK}"],
                         cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if got.returncode != 0:
        print(f"FAIL  could not read the pre-fix check from git: {got.stderr.strip()[:120]}")
        print("      pass --old <path-to-the-pre-fix-check> instead")
        sys.exit(1)
    return got.stdout


PRE_FIX = old_check()


def run(tree: Path, rule: str) -> subprocess.CompletedProcess:
    (tree / "tools" / CHECK).write_text(rule, encoding="utf-8", newline="")
    return subprocess.run([sys.executable, str(tree / "tools" / CHECK), str(tree)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


missed = 0
with tempfile.TemporaryDirectory() as td:
    base = Path(td)

    # ── tree A: the current rule must catch the key in the wrong dict ──
    a = base / "a"
    a.mkdir()
    shutil.copytree(ROOT / "custom_components", a / "custom_components")
    shutil.copytree(ROOT / "tools", a / "tools")
    scanner = a / "custom_components" / "ha_optimizer" / "scanner.py"
    lf = scanner.read_bytes().decode("utf-8").replace("\r\n", "\n")
    if lf.count(FROM) != 1:
        print(f"FAIL  the key to move appears {lf.count(FROM)} times, expected 1, "
              f"so the counter-proof tested nothing")
        missed += 1
    elif lf.count(INTO) != 1:
        print(f"FAIL  the destination appears {lf.count(INTO)} times, expected 1, "
              f"so the counter-proof tested nothing")
        missed += 1
    else:
        moved = lf.replace(FROM, "", 1).replace(INTO, INTO + FROM, 1)
        scanner.write_bytes(moved.replace("\n", "\r\n").encode("utf-8"))
        r = run(a, (ROOT / "tools" / CHECK).read_text(encoding="utf-8"))
        out = r.stdout + r.stderr
        if "Traceback" in out:
            print("FAIL  the check crashed instead of reporting")
            missed += 1
        elif r.returncode == 0:
            print("FAIL  the key is in a dict the panel never sees and the check passed")
            missed += 1
        elif SIGNAL not in out:
            print(f"FAIL  went red but never said why; expected {SIGNAL!r}")
            missed += 1
        elif NAMES_FILE not in out:
            print(f"FAIL  went red but did not name the file ({NAMES_FILE})")
            missed += 1
        else:
            print("ok    key parked in an unrelated dict -> the response check goes red")
            for ln in out.splitlines():
                if ln.strip().startswith("FAIL") and ("write_rollup" in ln or "emits" in ln):
                    print("      " + ln.strip()[:150])

    # ── tree B: the pre-fix rule must NOT catch it, or this proves nothing ──
    b = base / "b"
    b.mkdir()
    shutil.copytree(ROOT / "custom_components", b / "custom_components")
    shutil.copytree(ROOT / "tools", b / "tools")
    scanner = b / "custom_components" / "ha_optimizer" / "scanner.py"
    lf = scanner.read_bytes().decode("utf-8").replace("\r\n", "\n")
    if lf.count(FROM) != 1 or lf.count(INTO) != 1:
        print("FAIL  the anchors moved between the two trees, so A and B are not comparable")
        missed += 1
    elif PRE_FIX == (ROOT / "tools" / CHECK).read_text(encoding="utf-8"):
        _from = ("tools/prefix_rules/" + CHECK
                 if (ROOT / "tools" / "prefix_rules" / CHECK).is_file()
                 else "git HEAD (no tools/prefix_rules/ fixture exists)")
        print(f"FAIL  the pre-fix rule it read - from {_from} - is the current rule, so "
              f"there is nothing to compare against. Either the fixture was updated to "
              f"match (it must not be) or the fix is committed and --old is needed.")
        missed += 1
    else:
        scanner.write_bytes(lf.replace(FROM, "", 1).replace(INTO, INTO + FROM, 1)
                            .replace("\n", "\r\n").encode("utf-8"))
        r = run(b, PRE_FIX)
        out = r.stdout + r.stderr
        if "Traceback" in out:
            print("FAIL  the pre-fix check crashed; that is not a crossable result")
            missed += 1
        elif r.returncode != 0:
            print("FAIL  the pre-fix check also caught it, so this counter-proof is not "
                  "measuring the rule that was added - it is measuring something else")
            missed += 1
        else:
            print("ok    the same injection against the pre-fix rule stays green, so the "
                  "injection measures the new rule")
            print("      (that is the hole: any returned dict in the file satisfied it)")

print()
print("FAILED" if missed else
      "PASSED: a key in the wrong dict is caught and named; the pre-fix rule was fooled")
sys.exit(1 if missed else 0)
