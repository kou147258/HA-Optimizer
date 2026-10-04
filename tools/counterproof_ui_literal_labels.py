"""Does the new "label spelled out in JS" class of check actually bite?

Reverts the panel fix in a scratch tree and requires the check to go red. A new
rule nobody has tried to break is a rule nobody has learned whether it holds.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"C:\Users\43457\.minimax\sessions\mvs_beb6ae963fc14d89832b6a57e91f762d\workspace\HA-Optimizer")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PANEL = "custom_components/ha_optimizer/panel.html"
CASES = [
    ("the category map is spelled out in JS again",
     "    const catLabel = { entity: t('catEntity'), helper: t('catHelper'),\n"
     "                       automation: t('catAuto'), script: t('catScript') }[r.category] || r.category;\n",
     "    const catLabel = { entity: '⚡ Entity', helper: '🔧 Helper', automation: '🤖 Auto', script: '📜 Script' }[r.category] || r.category;\n",
     "no JS map spells entity: '⚡ Entity' out"),
    ("a translation is removed from one language only",
     "    catAuto: '🤖 自动化', catScript: '📜 脚本',\n",
     "",
     "every language defines catAuto"),
    ("a filter option loses its data-i18n",
     '          <option value="automation" data-i18n="filterCatAutomation">Automation</option>\n',
     '          <option value="automation">Automation</option>\n',
     "the automation filter option carries data-i18n"),
    ("control: the unmutated tree", None, None, ""),
]

missed = 0
for label, old, new, signal in CASES:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        shutil.copytree(ROOT / "custom_components", t / "custom_components")
        shutil.copytree(ROOT / "tools", t / "tools")
        for sub in ("custom_components", "tools"):
            for pc in (t / sub).rglob("__pycache__"):
                shutil.rmtree(pc, ignore_errors=True)
        if old is not None:
            p = t / PANEL
            text = p.read_bytes().decode("utf-8").replace("\r\n", "\n")
            n = text.count(old)
            if n != 1:
                print(f"FAIL  {label} — the anchor appears {n} times, so this case "
                      f"proves nothing")
                missed += 1
                continue
            p.write_bytes(text.replace(old, new, 1).replace("\n", "\r\n").encode("utf-8"))

        r = subprocess.run([sys.executable, str(t / "tools" / "test_ui_labels.py")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        out = r.stdout + r.stderr
        expect_red = old is not None
        red = r.returncode != 0

        if "Traceback" in out:
            print(f"FAIL  {label} — the check crashed instead of reporting")
            missed += 1
        elif red != expect_red:
            print(f"FAIL  {label} — expected {'red' if expect_red else 'green'}, "
                  f"got the other (exit {r.returncode})")
            missed += 1
        elif expect_red and signal not in out:
            print(f"FAIL  {label} — went red but not because of this case; "
                  f"{signal!r} never appeared")
            missed += 1
        else:
            print(f"ok    {label}")
            for ln in out.splitlines():
                if ln.strip().startswith("  - ") and signal.split(":")[0][:22] in ln:
                    print("      " + ln.strip()[:150])

print()
print("FAILED" if missed else
      "PASSED: a literal label, a one-sided translation and a bare option are all caught")
sys.exit(1 if missed else 0)
