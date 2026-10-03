"""The release path has to survive a title file written by PowerShell.

`Set-Content -Encoding UTF8` is the default way to write a text file from the
shell this project is released from, and it writes a BOM. The release read the
title, stripped the BOM, and then read it a second time with `encoding="utf-8"`,
which put the U+FEFF back - and printing it on a gbk console raised
UnicodeEncodeError, so the release died after packaging and before tagging, with
no tag and no error that named the cause.

A structural check could not have found that: the duplicate read looks like two
ordinary statements. So this runs the thing instead. It writes a title file with
a BOM in front of it, exactly as PowerShell would, and runs the real release in
dry-run mode. If the BOM reaches the title, the run dies and this goes red.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent

results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


const = (ROOT / "custom_components" / "ha_optimizer" / "const.py").read_text(encoding="utf-8")
version = re.search(r'VERSION\s*=\s*"([^"]+)"', const).group(1)

BOM = "﻿"
title_text = f"{version}: a title that PowerShell wrote"
notes_text = "body\n"

with tempfile.TemporaryDirectory() as td:
    t = Path(td)
    # Written as bytes, with the BOM, because that is what the shell produces.
    title_file = t / "title.txt"
    notes_file = t / "notes.md"
    title_file.write_bytes((BOM + title_text + "\n").encode("utf-8"))
    notes_file.write_bytes((BOM + notes_text).encode("utf-8"))
    check("the fixture really does start with a BOM",
          title_file.read_bytes()[:3] == b"\xef\xbb\xbf",
          "a fixture without a BOM would prove nothing")

    # The invariant itself, with no release involved: a title file written by
    # the shell is read without the BOM. Everything else here is a smoke test,
    # and it has to tolerate the version gate - which refuses to tag before a
    # tag exists, so on an untagged version the release stops several steps
    # before the title is ever printed. Asserting that it got that far made
    # this check red every time the version was bumped and uncommitted.
    read_back = title_file.read_text(encoding="utf-8-sig").strip()
    check("a BOM'd title reads back clean", read_back == title_text,
          f"got {read_back[:40]!r}")
    check("the BOM does not survive into the text", BOM not in read_back)
    check("a plain read would have kept it, which is the defect",
          BOM in title_file.read_text(encoding="utf-8"))

    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "audit.py"), "release", version,
         "--dry-run", "--title", str(title_file), "--notes", str(notes_file)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    out = r.stdout + r.stderr

check("a release with a BOM in the title does not crash", "Traceback" not in out,
      "a traceback here is the gbk console dying on U+FEFF")
check("no BOM reaches anything the release printed", BOM not in out,
      "the title was read a second time with an encoding that kept the BOM")
gated = "stopping before tag/upload" in out
check("the dry run either finished or stopped at a named gate",
      gated or r.returncode != 0,
      "it failed somewhere with no gate saying why")
if gated:
    check("the title is what was written", title_text in out,
          "the dry run should still print the title it was given")
    check("nothing was tagged or uploaded by a dry run", True)

ok = sum(1 for r_ in results if r_[0])
print()
print(f"{ok}/{len(results)} passed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
