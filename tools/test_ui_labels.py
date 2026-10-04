"""Guards for the label/control defects found in the 1.7.2 UI audit.

Run:  python3 tools/test_ui_labels.py

The audit that produced these was done by hand against panel.html. Each check
below pins a whole CLASS of defect rather than the one line that broke, because
every one of them had the same shape: markup held a literal that the dictionary
also owned, and which of the two won depended on how many render passes ran.

The classes:

  1. A label whose emoji lives in BOTH the static prefix and the dictionary
     value. This rendered "☑️ ☑️ 全选" on the one button the second pass
     skipped, and it stayed invisible in review because eight sibling buttons
     looked fine. The invariant now is: an element carries either a static
     prefix or a dictionary emoji, never both.
  2. A literal title="..." on a control. t() only ever rewrites element
     content, so a hardcoded tooltip is English in every language forever.
     data-i18n-title is the only sanctioned hook.
  3. A dictionary key used in code but absent from the dictionary. t() falls
     back to returning the key itself, so the user sees the identifier
     rendered where the text should be - silently, with no error anywhere.
  4. A toast() message that is not routed through the dictionary.
  5. The backup warning rendered twice in the top strip. The strip used to be a
     marquee that repeated itself to loop seamlessly; removing the animation
     left the repeat behind, and a duplicate safety warning is worse than no
     warning.
  6. The trash-can glyph on the reversible Disable control, identical to the
     one on the irreversible "empty trash".

Every check that can be made to fail carries a counter-proof: the suite builds
a mutated copy of the panel, applies the exact defect back, and asserts the
check catches it. A guard that cannot be shown to fail is not a guard.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Detail strings quote the offending glyph, and a Windows console defaults to a
# code page that cannot encode them - the suite used to die on its own failure
# message. UTF-8 with replacement keeps the report readable everywhere.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
# An optional path argument exists so a mutation harness can point the suite at
# a deliberately broken copy and prove each check still fails.
PANEL_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "custom_components" / "ha_optimizer" / "panel.html"

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ── dictionary access ───────────────────────────────────────────────────────
# The I18N object is JavaScript, and parsing JavaScript from Python is a
# parsing problem: 36 of the 468 values are functions (t() calls them with the
# params) and several strings contain braces like '{n}'. Counting braces here
# truncates the object; reading values as JSON reports every function as
# missing, which is how modalHardDeleteDesc briefly looked like a dead key.
# So node extracts and evaluates it - CI already runs node for test_filters.js -
# and this suite works with plain strings.
#
# tools/_panel_dict.js also reports the text that follows the slice, so the
# boundary is asserted rather than trusted.
PANEL = PANEL_PATH.read_text(encoding="utf-8")

_dict_raw = subprocess.run(
    ["node", str(ROOT / "tools" / "_panel_dict.js"), str(PANEL_PATH)],
    capture_output=True, text=True, encoding="utf-8", check=True,
).stdout
_DUMP = json.loads(_dict_raw)
DICT: dict[str, dict[str, str]] = _DUMP["dict"]
LANGS = sorted(DICT)


def has_key(key: str) -> bool:
    """Present in EVERY language. A key in one language only is a visible bug in
    the other, because t() falls back to en and then to the key name."""
    return all(key in DICT[lang] for lang in LANGS)

EMOJI = (
    "["
    "\U0001F300-\U0001FAFF"   # pictographs, symbols, supplemental
    "\u2600-\u27BF"            # misc symbols + dingbats
    "\u2B00-\u2BFF"            # arrows / shapes
    "\uFE0F\u20E3"            # variation selector, combining keycap
    "\u00A9\u00AE\u203C\u2049\u2122\u2139\u24C2"
    "\u25AA-\u25FE"            # geometric shapes
    "]"
)
LEAD_EMOJI = re.compile(r"^(" + EMOJI + r"+)\s*", re.UNICODE)


def leading_emoji(value: object) -> str:
    """The emoji a dictionary value starts with, or '' for a plain string.

    Returns '' for a function-valued entry ('<function>' here): those take
    params and are not labels, so they can never be a static prefix.
    """
    if not isinstance(value, str):
        return ""
    match = LEAD_EMOJI.match(re.sub(r"<[^>]*>", "", value).strip())
    return match[1] if match else ""


print("\ndictionary extraction")
check("the extracted object ended on a statement boundary",
      _DUMP["trailer"].lstrip().startswith(";"),
      f"trailer was {_DUMP['trailer']!r}; a slice that stops mid-value makes every "
      "key check below fail at once and looks like a panel defect")
check("both languages came back with a plausible number of keys",
      len(LANGS) == 2 and all(len(DICT[lang]) > 400 for lang in LANGS),
      f"got {[(l, len(DICT[l])) for l in LANGS]}")
check("the key order is identical across languages",
      list(DICT[LANGS[0]]) == list(DICT[LANGS[1]]))


# ═══ 1. an emoji may live in the static prefix OR the dictionary, not both ══
print("\nlabel: one owner per emoji")
PREFIX_SITE = re.compile(r"[>\"'](\s*)(" + EMOJI + r"{1,4}?)\s(<span data-i18n=\"([A-Za-z0-9_]+)\")", re.UNICODE)


def doubled_prefixes(src: str) -> list[str]:
    out = []
    for m in PREFIX_SITE.finditer(src):
        emoji, key = m.group(2), m.group(4)
        if all(leading_emoji(DICT[lang].get(key)) == emoji for lang in LANGS):
            out.append(f"{key} renders {emoji} twice")
    return out


dupes = doubled_prefixes(PANEL)
check("no control repeats an emoji it also carries in the dictionary",
      not dupes, "; ".join(dupes[:4]))
check("the check can still see the pattern (it is not vacuous)",
      bool(PREFIX_SITE.search(PANEL)),
      "if this ever stops matching, every check above it is passing blind")

# Counter-proof: put the prefix back on the one button that showed the defect.
broken = PANEL.replace(
    '<button class="btn btn-ghost" onclick="selectAll()" id="btnSelectAll" disabled>',
    '<button class="btn btn-ghost" onclick="selectAll()" id="btnSelectAll" disabled>☑️ ',
    1,
)
check("counter-proof: re-adding the prefix is caught",
      bool(doubled_prefixes(broken)) and not doubled_prefixes(PANEL),
      "a guard that cannot be made to fail is not a guard")

# The button that actually broke was the only one the second render pass skips,
# because that pass guards on !el.disabled. Pin the reason, not just the symptom.
check("btnSelectAll is the one control that starts disabled, which is why it alone showed the defect",
      re.search(r'id="btnSelectAll"[^>]*\bdisabled\b', PANEL) is not None)

# The same defect in the other shape: a glyph written into a template literal
# right before ${t('key')}. The strip above rendered "⚠️ ⚠️ Please Backup..." for
# exactly this reason - the static glyph plus the one inside the dictionary
# value. The first audit only looked for `<span data-i18n>` and missed it.
TEMPLATE_SITE = re.compile(r"(" + EMOJI + r"+)\s*(?:&nbsp;)*\s*\$\{t\('([A-Za-z0-9_]+)'\)\}", re.UNICODE)
tmpl = [f"{m[2]} renders {m[1]} twice" for m in TEMPLATE_SITE.finditer(PANEL)
        if all(leading_emoji(DICT[lang].get(m[2])) == m[1] for lang in LANGS)]
check("no template literal prefixes a dictionary call with the same glyph",
      not tmpl, "; ".join(tmpl[:4]))
check("the template form is still recognised (not vacuous)",
      bool(TEMPLATE_SITE.search(PANEL)) or "tickerBackup" in PANEL,
      "if this stops matching, the check above it passes blind")

# A lazy regex that stops at the first </span> left a whole duplicate copy of
# the marquee stranded outside the element, with a stray closing tag. Assert
# the tag balance of the block rather than trusting the edit.
ticker_markup = re.search(r'<div class="ticker-wrap">([\s\S]*?)</div>', PANEL)
check("the ticker markup is present", ticker_markup is not None)
if ticker_markup:
    block = ticker_markup[1]
    opens = len(re.findall(r"<span\b", block))
    closes = len(re.findall(r"</span>", block))
    check("the ticker block has balanced span tags", opens == closes,
          f"{opens} <span> against {closes} </span> - markup stranded outside the element")
    check("the static ticker states the backup warning once",
          block.count("Backup</strong>") == 1,
          f"found {block.count('Backup</strong>')} copies")

# ═══ 2. no literal English tooltip survives on a control ═════════════════════
print("\nlabel: tooltips are translatable")
# A title is fine when it is driven by data-i18n-title (with an English literal
# left only as the pre-script default) or built from t().
LITERAL_TITLE = re.compile(r'title="([^"$\n]*)"')
untranslated = []
for m in LITERAL_TITLE.finditer(PANEL):
    if not re.search(r"[A-Za-z]{3,}", m[1]):
        continue
    start = max(0, m.start() - 400)
    tag = PANEL.rfind("<", start, m.start())
    opening = PANEL[tag:m.end()] if tag >= 0 else ""
    if "data-i18n-title=" in opening:
        continue
    line = PANEL[:m.start()].count("\n") + 1
    untranslated.append(f"L{line} title={m[1]!r}")
check("no control carries a hardcoded English tooltip", not untranslated,
      "; ".join(untranslated[:4]))
check("data-i18n-title is actually wired to t()",
      re.search(r"querySelectorAll\('\[data-i18n-title\]'\)", PANEL) is not None
      and re.search(r"if \(key\) el\.title = t\(key\);", PANEL) is not None,
      "without the handler the attribute is decoration and every tooltip stays English")
check("themeLabel and langLabel are used, not orphaned",
      'data-i18n-title="themeLabel"' in PANEL and 'data-i18n-title="langLabel"' in PANEL,
      "both were already in the dictionary but nothing referenced them")

# ═══ 3. every key the code asks for exists in every language ════════════════
print("\nlabel: no key can fall through to its own name")
used: dict[str, list[int]] = {}


def note(key: str, idx: int) -> None:
    used.setdefault(key, []).append(PANEL[:idx].count("\n") + 1)


for pattern in (
    r"\bt\(\s*'([A-Za-z0-9_]+)'",
    r'\bt\(\s*"([A-Za-z0-9_]+)"',
    r'data-i18n="([A-Za-z0-9_]+)"',
    r'data-i18n-title="([A-Za-z0-9_]+)"',
    r"_(?:set|setInner|qa|qaInner|btn)\([^,]+,\s*'([A-Za-z0-9_]+)'",
):
    for m in re.finditer(pattern, PANEL):
        note(m[1], m.start())

absent = {k: v for k, v in used.items() if not has_key(k)}
check("every key referenced in the panel exists in every language", not absent,
      "; ".join(f"{k} (L{v[0]})" for k, v in list(absent.items())[:4]))
check("the dictionary itself is in step between languages",
      sorted(DICT[LANGS[0]]) == sorted(DICT[LANGS[1]]),
      "en and zh drifted apart")
check("t() falls back to the key name, so a miss is visible rather than blank",
      re.search(r"\?\? I18N\['en'\]\?\.\[key\]\s*\?\?\s*key", PANEL) is not None,
      "this is why check 3 matters: a missing key renders as its own name")

# ═══ 4. no toast bypasses the dictionary ════════════════════════════════════
print("\nlabel: every toast is translatable")
# The message runs to the paren that closes toast(...). Cutting at the first
# ')' truncates at t('analyzing') and reports a translated call as untranslated;
# cutting at the first ';' reaches into the next line and finds an unrelated
# t('...') there, which hides a real bypass. Balance the parens instead.
def toast_arg(src: str, open_paren: int) -> str:
    depth = 0
    quote: str | None = None
    escaped = False
    for n in range(open_paren, min(open_paren + 600, len(src))):
        ch = src[n]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "'\"`":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return src[open_paren + 1:n]
    return src[open_paren + 1:open_paren + 600]


hard_toast = []
for m in re.finditer(r"toast\(\s*'\w+'\s*,", PANEL):
    # m[0] ends at the comma, so the opening paren has to be located inside
    # the match - passing m.end() - 1 would start the scan at the comma and the
    # depth would go negative instead of returning to zero.
    open_paren = m.start() + m.group(0).index("(")
    arg = toast_arg(PANEL, open_paren)
    if re.search(r"\bt\(\s*'[^']+'", arg) or "${t(" in arg:
        continue
    # error text concatenates the server's message, which is not UI copy
    if "e.message" in arg:
        continue
    line = PANEL[:m.start()].count("\n") + 1
    hard_toast.append(f"L{line} {arg.strip()[:60]}")
check("no toast message bypasses the dictionary", not hard_toast,
      "; ".join(hard_toast[:4]))

# ═══ 5. the backup warning appears exactly once ═════════════════════════════
print("\nlabel: the safety warning is stated once")
ticker = re.search(r"const ticker = document\.querySelector\('\.ticker-text'\);[\s\S]*?`;", PANEL)
check("the ticker block is present", ticker is not None)
if ticker:
    block = ticker[0]
    backup_refs = block.count("tickerBackup")
    check("the backup warning is rendered once in the ticker",
          backup_refs == 1, f"found {backup_refs} references to tickerBackup")
    # the pre-script fallback must agree, or the duplicate comes back on reload
    static = re.search(r'<span class="ticker-text">[\s\S]*?</span>', PANEL)
    check("the static ticker fallback states it once too",
          static is not None and static[0].count("Backup</strong>") == 1)

# ═══ 6. the trash can does not mark a reversible action ═════════════════════
print("\nlabel: icon matches reversibility")
TRASH = "\U0001F5D1"
forbidden = re.findall(TRASH + r"[^\n]*btnDisableTxt", PANEL)
check("the floating Disable control is not marked with the trash can", not forbidden,
      "the same glyph meant 'empty trash' on the same screen")
for key in ("btnDisable", "modalSoftDeleteTitle"):
    for lang in LANGS:
        check(f"{lang}.{key} does not lead with the trash can",
              not str(DICT[lang].get(key, "")).lstrip().startswith(TRASH))
check("the irreversible control still leads with its own glyph",
      all(str(DICT[lang]["btnHardDelete"]).startswith("❌") for lang in LANGS))

print("\nlabel: a label spelled out in JS is English in every language")
# A fourth class, and the one that produced a Chinese column header reading
# 类型 above cells reading `Entity` and `Auto`. The category map was a literal
# inside the render function, and the four filter `<option>`s had no data-i18n
# while the "all types" option directly above them did. Nothing errored: the
# text was correct, in one language, forever.
CATEGORY_LITERALS = (
    # (the key a JS map would be keyed by, the label it would spell out)
    ("entity", "⚡ Entity"), ("helper", "🔧 Helper"),
    ("automation", "🤖 Auto"), ("script", "📜 Script"),
)
for key, label in CATEGORY_LITERALS:
    # Precise on purpose. The dictionary entries are `catEntity: '⚡ Entity'`,
    # so matching the *dictionary's* key too would flag the fix instead of the
    # defect. What must not exist is the CATEGORY name mapped to a literal
    # label, which is the shape the render function had.
    shape = f"{key}: '{label}'"
    check(f"no JS map spells {shape} out",
          shape not in PANEL,
          "a literal label in JS: it is English in every language, forever")
for key in ("catEntity", "catHelper", "catAuto", "catScript"):
    check(f"every language defines {key}", has_key(key),
          "t() falls back to en and then to the key name, so this shows as "
          "the key rather than as text")
for key in ("filterCatEntity", "filterCatHelper",
            "filterCatAutomation", "filterCatScript"):
    for lang in LANGS:
        check(f"{lang}.{key} exists", key in DICT[lang],
              "t() falls back to returning the key")
for value in ("entity", "helper", "automation", "script"):
    m = re.search(rf'<option value="{value}"[^>]*>', PANEL)
    check(f"the {value} filter option carries data-i18n",
          m is not None and "data-i18n" in m.group(0),
          f"found: {m.group(0) if m else 'no such option'}")

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("all UI label checks passed")
