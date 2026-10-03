#!/usr/bin/env python3
"""Counter-proofs for tools/test_dead_guard.py.

A green guard proves nothing until you watch it go red on the defects it claims
to catch. Each case below injects one real defect into a throwaway copy of the
component and asserts that the intended check reports it.

The injected defects are the ones that actually shipped:
  1  scanner.py's if/elif block flattened onto one physical line (v1.7.9)
  2  panel.html no longer reading the `custom_cards_unchecked` marker
  3  a Vietnamese comment back in a docstring
  4  a template literal swallowed by a `//` comment in panel.html

Usage:  python3 tools/counterproof_dead_guard.py [repo root]
Exit:   0 every injection was caught, 1 at least one slipped through
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_dead_guard import (  # noqa: E402
    check_custom_card_branch,
    check_marker_is_surfaced,
    check_no_flattened_lines,
    check_no_foreign_comments,
    check_panel_no_commented_code,
)

COMPONENT = ("custom_components", "ha_optimizer")


def _make_copy(root: Path, tmp: Path) -> Path:
    dest = tmp / "custom_components" / "ha_optimizer"
    shutil.copytree(root.joinpath(*COMPONENT), dest)
    # Drop caches so the copy has exactly the files we intend to edit.
    for pyc in dest.rglob("__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)
    return dest


def _flatten_custom_card_block(comp: Path) -> None:
    """Rebuild the exact v1.7.9 shape: one 900+ char line where everything
    after the first '#' is a comment."""
    path = comp / "scanner.py"
    src = path.read_text(encoding="utf-8")
    start = src.index("                            if not custom_cards_known:\n")
    # Search for the elif *relative to start*: a global index() can match an
    # earlier elif elsewhere in the file, which makes src[start:end] empty and
    # silently slices a hole instead of flattening the block.
    tail = src.index('                            elif card_type not in registered_custom_cards:\n', start)
    end = tail + len('                            elif card_type not in registered_custom_cards:\n')
    block = src[start:end]
    # Drop newlines and pad with spaces: syntactically valid, semantically dead.
    flat = block.replace("\n", " " * 4).replace("\r", "")
    src = src[:start] + flat + src[end:]
    path.write_text(src, encoding="utf-8")


def _hide_marker(comp: Path) -> None:
    path = comp / "panel.html"
    src = path.read_text(encoding="utf-8")
    src = src.replace("d.custom_cards_unchecked", "d.somethingElse")
    path.write_text(src, encoding="utf-8")


def _add_vietnamese(comp: Path) -> None:
    """Reintroduce the real upstream comment, verbatim.

    The sample is the actual line that was in fingerprint.py, not an invented
    phrase: a made-up sentence can accidentally dodge the stopword list and
    then the counter-proof "fails" for the wrong reason.
    """
    path = comp / "fingerprint.py"
    src = path.read_text(encoding="utf-8")
    marker = "class SigmaDetector:"
    idx = src.index(marker)
    src = src[:idx] + "# Chạy lúc 00:05 mỗi ngày — chụp snapshot ngày hôm qua\n" + src[idx:]
    path.write_text(src, encoding="utf-8")


def _comment_out_template(comp: Path) -> None:
    path = comp / "panel.html"
    src = path.read_text(encoding="utf-8")
    marker = "// ── Custom card not installed ──"
    idx = src.index(marker)
    src = src[:idx] + marker + " const x = `${t('a')}`;" + src[idx + len(marker):]
    path.write_text(src, encoding="utf-8")


CASES = [
    ("scanner if/elif flattened onto one line", _flatten_custom_card_block,
     check_no_flattened_lines, check_custom_card_branch),
    ("panel stops reading the unchecked marker", _hide_marker,
     check_marker_is_surfaced,),
    ("Vietnamese comment reintroduced", _add_vietnamese,
     check_no_foreign_comments,),
    ("template literal swallowed by a // comment", _comment_out_template,
     check_panel_no_commented_code,),
]


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    if not root.joinpath(*COMPONENT).is_dir():
        print(f"not a HA Optimizer checkout: {root}")
        return 2

    missed = 0
    for label, inject, *checks in CASES:
        with tempfile.TemporaryDirectory(prefix="haopt-counterproof-") as td:
            comp = _make_copy(root, Path(td))
            inject(comp)
            caught_by = [c.__name__ for c in checks if c(root_copy := Path(td))]
            if not caught_by:
                missed += 1
                print(f"FAIL  {label} -- no check fired")
            else:
                print(f"ok    {label} -> caught by {', '.join(caught_by)}")

    # And the real thing must still pass, or the counter-proof only proves the
    # checks are broken in the other direction.
    real = [
        c.__name__ for c in (check_no_flattened_lines, check_custom_card_branch,
                             check_marker_is_surfaced, check_no_foreign_comments,
                             check_panel_no_commented_code)
        if c(root)          # non-empty == problems found
    ]
    if real:
        missed += 1
        print(f"FAIL  clean tree should pass, but these reported problems: {real}")
    else:
        print("ok    clean tree passes")

    print("FAILED" if missed else "PASSED")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
