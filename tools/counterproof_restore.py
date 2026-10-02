"""Counter-proofs for tools/test_restore_truth.py.

Run:  python3 tools/counterproof_restore.py

Each defect the live instance exposed is injected into a copy of the source and
the suite has to fail. A guard that has never been seen red is
indistinguishable from one that checks nothing - and the first version of this
suite proved the point the other way round: its checks were written against
strings that no longer existed, so they passed while verifying nothing.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMP = ROOT / "custom_components" / "ha_optimizer"
SUITE = ROOT / "tools" / "test_restore_truth.py"
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# (label, file, old, new) - the literal defect as it existed before the fix.
MUTATIONS = [
    (
        "the row that comes back is the snapshot, not the current state",
        "store.py",
        "            entry = dict(snapshot)\n",
        "            entry = snapshot\n",
    ),
    (
        "the restored row never re-reads the registry",
        "store.py",
        '                entry["disabled"] = bool(current.disabled)\n',
        "",
    ),
    (
        "the misleading '-> empty trash' toast is back",
        "panel.html",
        "t('restoreAlreadyEnabled')",
        "t('emptyTrash')",
    ),
    (
        "the post-restore refresh is gone",
        "panel.html",
        """    try {
      const fresh = extractData(await callService('ha_optimizer', 'get_results', {}));
      if (fresh && Array.isArray(fresh.results)) {
        displayResults(fresh.results);
        updateStats(fresh.statistics || {});
        localStorage.setItem('ha_optimizer_results', JSON.stringify(fresh));
      }
    } catch (e) {
      console.warn('[PurgeEngine] refresh after restore failed:', e);
    }""",
        "",
    ),
    (
        "the trash record is dropped regardless of the outcome",
        "__init__.py",
        '        if result.get("success"):\n            # Put the entity back in the scan list BEFORE dropping the trash',
        '        if True:\n            # Put the entity back in the scan list BEFORE dropping the trash',
    ),
]


def run_suite(paths: list[Path]) -> tuple[bool, str]:
    r = subprocess.run([sys.executable, str(SUITE), *[str(p) for p in paths]],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0, r.stdout + r.stderr


def main() -> int:
    originals = {n: (COMP / n).read_text(encoding="utf-8") for n in ("store.py", "panel.html", "__init__.py")}

    ok, out = run_suite([COMP / n for n in ("store.py", "panel.html", "__init__.py")])
    print("unmutated sources must PASS:")
    print(("  ok   " if ok else "  FAIL ") + "suite is green on the real sources")
    if not ok:
        print(out)
        return 1

    bad = 0
    print("\neach injected defect must FAIL:")
    for i, (label, filename, old, new) in enumerate(MUTATIONS, 1):
        if old not in originals[filename]:
            print(f"  SKIP  #{i} {label} - the source moved, the mutation no longer applies")
            bad += 1
            continue
        tmpdir = Path(tempfile.mkdtemp(prefix="haopt-restore-"))
        paths = []
        for name in ("store.py", "panel.html", "__init__.py"):
            text = originals[name]
            if name == filename:
                text = text.replace(old, new, 1)
            p = tmpdir / name
            p.write_text(text, encoding="utf-8")
            paths.append(p)
        green, out = run_suite(paths)
        names = [ln.strip()[2:] for ln in out.splitlines() if ln.strip().startswith("- ")]
        # A counter-proof holds when the suite goes RED on the broken copy.
        if green:
            print(f"  FAIL  #{i} {label}")
            print("           the suite stayed green - the guard is not looking at this")
            bad += 1
        else:
            print(f"  ok    #{i} {label}")
            for nm in names[:2]:
                print("           caught by: " + nm)
        for p in paths:
            p.unlink(missing_ok=True)
        tmpdir.rmdir()

    print()
    if bad:
        print(f"{bad} counter-proof(s) did not hold")
        return 1
    print(f"all {len(MUTATIONS)} counter-proofs held: every injected defect was caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
