"""Counter-proofs for tools/test_safety_gate.py.

Run:  python3 tools/counterproof_safety_gate.py

The gate is the only thing between a broken restore and a 6-hourly deletion
timer, so "the suite is green" has to mean something. Each of these puts the
defect back and requires the suite to go red.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMP = ROOT / "custom_components" / "ha_optimizer"
SUITE = ROOT / "tools" / "test_safety_gate.py"
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# (label, file, old, new) - the defect, exactly as it would be reintroduced
MUTATIONS = [
    (
        "auto-expiry deletes without testing the restore first",
        "__init__.py",
        "    if not await _async_restore_self_test(hass, entry, expired[0]):",
        "    if False:",
    ),
    (
        "the self-test trusts the service's own success flag",
        "__init__.py",
        "    if not (result.get(\"success\") and came_back):",
        "    if not result.get(\"success\"):",
    ),
    (
        "the self-test leaves the entity enabled",
        "__init__.py",
        "        await engine.async_purge_entities([entity_id], soft_delete=True)",
        "        pass",
    ),
    (
        "a failed gate only logs, and the delete goes on",
        "__init__.py",
        "        )\n        return\n    _async_clear_issue(hass, \"auto_purge_aborted\")",
        "        )\n    _async_clear_issue(hass, \"auto_purge_aborted\")",
    ),
    (
        "uninstall deletes the records",
        "__init__.py",
        "    if kept:\n        _LOGGER.warning(",
        "    for k in (STORE_KEY, SOFT_DELETE_STORE_KEY):\n        try:\n            (Path(hass.config.config_dir) / \".storage\" / k).unlink()\n        except Exception:\n            pass\n    if kept:\n        _LOGGER.warning(",
    ),
]


def run(mutated_init: str) -> tuple[bool, str]:
    """Run the suite against a copy of the component with __init__.py replaced.

    Only the file under mutation is copied in; the rest is read from the real
    tree, because the suite also looks at strings.json and the translation and
    copying only half the tree produces a FileNotFoundError rather than a
    meaningful result.
    """
    import os
    with tempfile.TemporaryDirectory(prefix="haopt-safety-") as d:
        (Path(d) / "__init__.py").write_text(mutated_init, encoding="utf-8")
        (Path(d) / "strings.json").write_text(
            (COMP / "strings.json").read_text(encoding="utf-8"), encoding="utf-8")
        td = Path(d) / "translations"
        td.mkdir()
        (td / "zh-Hans.json").write_text(
            (COMP / "translations" / "zh-Hans.json").read_text(encoding="utf-8"), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(SUITE)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "HAOPT_COMPONENT_DIR": d},
        )
        return r.returncode == 0, r.stdout + r.stderr


def main() -> int:
    original = (COMP / "__init__.py").read_text(encoding="utf-8")
    ok, out = run(original)
    print("unmutated source must PASS:")
    print(("  ok   " if ok else "  FAIL ") + "suite is green on the real source")
    if not ok:
        print(out)
        return 1

    bad = 0
    print("\neach injected defect must FAIL:")
    for i, (label, filename, old, new) in enumerate(MUTATIONS, 1):
        src = original
        if src.count(old) != 1:
            print(f"  SKIP  #{i} {label} - the anchor no longer appears exactly once")
            bad += 1
            continue
        mutated = src.replace(old, new, 1)
        green, detail = run(mutated)
        names = [l.strip()[2:] for l in detail.splitlines() if l.strip().startswith("- ")]
        if green:
            print(f"  FAIL  #{i} {label}")
            print("           the suite stayed green - the guard is not looking at this")
            bad += 1
        else:
            print(f"  ok    #{i} {label}")
            for n in names[:2]:
                print("           caught by: " + n)
    print()
    if bad:
        print(f"{bad} counter-proof(s) did not hold")
        return 1
    print(f"all {len(MUTATIONS)} counter-proofs held: every injected defect was caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
