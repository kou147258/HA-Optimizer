#!/usr/bin/env python3
"""Counter-proofs for tools/test_rename_identity.py.

Each case injects the pre-1.7.10 behaviour into a throwaway copy and asserts
the behaviour tests go red. A rename guard that has never been seen to fail is
not known to work.

Injected defects:
  1  the trash record stops storing the registry identity (the 1.7.9 shape)
  2  the resolver falls back to "gone" instead of looking the identity up
  3  the restored scan row keeps the old, dead entity_id
  4  the restore path stops resolving the record

Usage:  python3 tools/counterproof_rename_identity.py [repo root]
Exit:   0 every injection was caught, 1 at least one slipped through
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent


def _drop_identity(src: str) -> str:
    old = """                reg = ent_reg.async_get(eid) if ent_reg is not None else None
                if reg is not None and reg.unique_id:
                    entry["unique_id"] = reg.unique_id
                    entry["platform"] = reg.platform
                    entry["domain"] = reg.domain
"""
    assert old in src, "anchor for the identity write moved"
    return src.replace(old, "", 1)


def _resolver_gives_up(src: str) -> str:
    old = """        if identity_known:
            current = ent_reg.async_get_entity_id(domain, platform, uid)
            if current:
                return {
                    "entity_id": current,
                    "status": "renamed",
                    "renamed_from": entity_id,
                    "identity_known": True,
                }
"""
    assert old in src, "anchor for the identity lookup moved"
    return src.replace(old, "", 1)


def _keep_old_id(src: str) -> str:
    old = '                entry["entity_id"] = target\n'
    assert old in src, "anchor for the restored row moved"
    return src.replace(old, "", 1)


def _restore_stops_resolving(src: str) -> str:
    old = """        resolved = await store.async_resolve_soft_deleted(entity_id)"""
    assert old in src, "anchor for the restore-side resolve moved"
    return src.replace(old, "        resolved = {'entity_id': entity_id, 'status': 'ok', "
                             "'renamed_from': None, 'identity_known': False}", 1)


CASES = [
    ("trash record stops storing the registry identity", "store.py", _drop_identity),
    ("resolver reports gone instead of finding the rename", "store.py", _resolver_gives_up),
    ("restored scan row keeps the dead entity_id", "store.py", _keep_old_id),
    ("restore path stops resolving the record", "__init__.py", _restore_stops_resolving),
]


def main() -> int:
    missed = 0
    for label, filename, inject in CASES:
        with tempfile.TemporaryDirectory(prefix="haopt-rename-cp-") as td:
            tmp = Path(td)
            shutil.copytree(ROOT / "custom_components", tmp / "custom_components")
            shutil.copytree(ROOT / "tools", tmp / "tools")
            for pyc in (tmp / "custom_components").rglob("__pycache__"):
                shutil.rmtree(pyc, ignore_errors=True)
            target = tmp / "custom_components" / "ha_optimizer" / filename
            raw = target.read_bytes()
            text = raw.decode("utf-8").replace("\r\n", "\n")
            target.write_bytes(inject(text).replace("\n", "\r\n").encode("utf-8"))

            proc = subprocess.run(
                [sys.executable, str(TOOLS / "test_rename_identity.py"), str(tmp)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode == 0:
                missed += 1
                print(f"FAIL  {label} -- the tests still passed")
            else:
                failed = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip().startswith("FAIL")]
                print(f"ok    {label} -> {len(failed)} assertion(s) went red")

    print("FAILED" if missed else "PASSED")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
