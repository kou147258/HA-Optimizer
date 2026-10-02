#!/usr/bin/env python3
"""i18n consistency checker for the HA Optimizer panel.

Guards the translation dictionary in panel.html against the drift that is easy
to introduce and hard to notice: a key added to `en` but not to the other 12
languages, a `{placeholder}` that only some languages carry, an HTML tag dropped
in translation, a key repeated inside a block (JS silently keeps the last one, so
a copy-pasted foreign block can override good translations), two non-English
languages sharing most of a key family (one was copy-pasted from the other), a
backend-emitted key that no dictionary defines, or a `t('key')` call site
pointing at a key that does not exist.

Dependency-free on purpose: this runs in CI before HACS/hassfest, which have
their own jobs. Usage:

    python3 tools/check_i18n.py            # report + exit 1 on any problem
    python3 tools/check_i18n.py --quiet    # only print problems

Exit codes: 0 = clean, 1 = at least one problem found, 2 = could not parse.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ha_optimizer"
PANEL = COMPONENT / "panel.html"

BASE = "en"  # reference language for parity

# Keys deliberately hidden from the per-device tag row in the Health tab.
# Kept in sync with _HIDDEN_DIAG_KEYS in panel.html.
HIDDEN_DIAG_KEYS = {"health_normal", "health_battery_critical", "health_battery_low"}


class ParseError(Exception):
    pass


# ── minimal JS-object-literal reader ──────────────────────────────────────────
# The I18N literal is hand-written and regular, but its values contain commas,
# braces ({placeholders}) and nested quotes, so brace counting alone is not
# enough — string/template state has to be tracked.

def _match_brace(text: str, start: int) -> int:
    """Index of the '}' matching the '{' at `start`, or -1."""
    depth = 0
    i = start
    quote = None
    n = len(text)
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        else:
            if c in "'\"`":
                quote = c
            elif c == "/" and i + 1 < n and text[i + 1] == "/":
                while i < n and text[i] != "\n":
                    i += 1
            elif c == "/" and i + 1 < n and text[i + 1] == "*":
                end = text.find("*/", i)
                if end == -1:
                    return -1
                i = end + 2
                continue
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        raw = raw[1:-1]
    return (
        raw.replace("\\'", "'")
        .replace('\\"', '"')
        .replace("\\n", "\n")
        .replace("\\\\", "\\")
    )


def parse_i18n(text: str, dupes: dict | None = None) -> dict[str, dict[str, str]]:
    """Return {lang: {key: value}} from the `const I18N = {...};` literal.

    If `dupes` is given, it is filled with {lang: [duplicated keys]}.
    """
    marker = "const I18N = {"
    idx = text.find(marker)
    if idx == -1:
        raise ParseError("const I18N = { not found in panel.html")
    obj_start = idx + len(marker) - 1
    obj_end = _match_brace(text, obj_start)
    if obj_end == -1:
        raise ParseError("unterminated I18N object")
    body = text[obj_start + 1 : obj_end]

    result: dict[str, dict[str, str]] = {}
    i = 0
    n = len(body)
    while i < n:
        m = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*").match(body, i)
        if not m:
            i += 1
            continue
        lang = m.group(1)
        j = m.end()
        # language value is an object literal
        while j < n and body[j] in " \t\r\n":
            j += 1
        if j >= n or body[j] != "{":
            i = m.end()
            continue
        close = _match_brace(body, j)
        if close == -1:
            raise ParseError(f"unterminated language block: {lang}")
        lang_dupes: list[str] = []
        result[lang] = _parse_entries(body[j + 1 : close], lang_dupes)
        if lang_dupes and dupes is not None:
            dupes[lang] = lang_dupes
        i = close + 1
    return result


def _parse_entries(body: str, dupes: list | None = None) -> dict[str, str]:
    """Parse one language block. A key repeated inside the block is recorded in
    `dupes` if provided — in a JS object literal the LAST definition silently
    wins, so a stray copy-pasted block is invisible to a parity check."""
    entries: dict[str, str] = {}
    i = 0
    n = len(body)
    at_depth_zero = True
    key = None
    buf: list[str] = []
    quote = None
    depth = 0
    while i < n:
        c = body[i]
        if quote:
            buf.append(c)
            if c == "\\" and i + 1 < n:
                buf.append(body[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
            buf.append(c)
            i += 1
            continue
        if c in "{(":
            depth += 1
            buf.append(c)
            i += 1
            continue
        if c in "})":
            depth -= 1
            buf.append(c)
            i += 1
            continue
        if c == "," and depth == 0:
            if key is not None:
                if key in entries and dupes is not None:
                    dupes.append(key)
                entries[key] = "".join(buf)
            key, buf, at_depth_zero = None, [], True
            i += 1
            continue
        if key is None and at_depth_zero:
            km = re.compile(r"\s*(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_]*))\s*:\s*").match(body, i)
            if km:
                key = km.group(1) if km.group(1) is not None else km.group(2)
                i = km.end()
                at_depth_zero = False
                continue
        if key is not None:
            buf.append(c)
        i += 1
    if key is not None:
        if key in entries and dupes is not None:
            dupes.append(key)
        entries[key] = "".join(buf)
    return {k: _normalise(v) for k, v in entries.items()}


def _normalise(raw: str) -> str:
    v = raw.strip()
    # function entry: (n) => `text`  ->  just the text
    fm = re.match(r"^\([^)]*\)\s*=>\s*`(.*)`$", v, re.S)
    if fm:
        return fm.group(1)
    fm = re.match(r"^[A-Za-z_$][\w$]*\s*=>\s*`(.*)`$", v, re.S)
    if fm:
        return fm.group(1)
    return _unquote(v)


# ── checks ───────────────────────────────────────────────────────────────────

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
TAG = re.compile(r"</?[A-Za-z][^>]*>")
CALL = re.compile(r"\bt\(\s*'([A-Za-z_][A-Za-z0-9_]*)'")


def backend_keys(texts: dict[str, str]) -> set[str]:
    """Every i18n key the Python backend can put on the wire."""
    found: set[str] = set()
    pats = [
        r'["\']key["\']\s*:\s*["\']([A-Za-z_][A-Za-z0-9_]*)["\']',          # {"key": "..."}
        r'\(\s*["\'](?:critical|warning|info)["\']\s*,\s*["\'](card_[a-z_]+)["\']',
        r'\(\s*["\'](fp_metric_[a-z_]+)["\']\s*,',                          # METRIC_LABELS
        r'(?:reasons|suggestions|diag|issues)\.append\(\s*["\']([a-z][a-z0-9_]{3,})["\']',
        r'_add\(\s*"[a-z]+"\s*,\s*"[a-z_]+"\s*,\s*[^,]+,\s*["\']([a-z][a-z0-9_]{3,})["\']',
        r'return\s*["\'](fp_confidence_[a-z_]+)["\']',
    ]
    for p in pats:
        for m in re.finditer(p, "\n".join(texts.values())):
            found.add(m.group(1))
    if "fp_direction_" in "\n".join(texts.values()):
        found |= {"fp_direction_higher", "fp_direction_lower"}
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true", help="only print problems")
    args = ap.parse_args()

    if not PANEL.is_file():
        print(f"FATAL: {PANEL} not found", file=sys.stderr)
        return 2
    try:
        panel = PANEL.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        print(f"FATAL: panel.html is not valid UTF-8: {exc}", file=sys.stderr)
        return 2

    dupes: dict[str, list[str]] = {}
    try:
        i18n = parse_i18n(panel, dupes)
    except ParseError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2
    if BASE not in i18n:
        print(f"FATAL: reference language '{BASE}' not found", file=sys.stderr)
        return 2

    problems: list[str] = []
    base = i18n[BASE]
    base_keys = set(base)
    langs = [l for l in i18n if l != BASE]

    def ph(v: str) -> set[str]:
        return set(PLACEHOLDER.findall(v))

    def tags(v: str) -> list[str]:
        return sorted(TAG.findall(v))

    # every value that legitimately appears in a dictionary, used to tell a real
    # translation apart from prose hardcoded into the page
    dict_values: set = set()
    for _d in i18n.values():
        for _v in _d.values():
            if isinstance(_v, str):
                dict_values.add(_v)
            elif callable(_v):
                dict_values.add(_v.__code__.co_consts[1] if len(_v.__code__.co_consts) > 1 else "")

    for lang in sorted(dupes):
        d = sorted(set(dupes[lang]))
        problems.append(
            f"[{lang}] {len(d)} DUPLICATED key(s) — the last definition silently wins: "
            + ", ".join(d[:8]) + (" ..." if len(d) > 8 else "")
        )

    for lang in sorted(langs):
        d = i18n[lang]
        missing = sorted(base_keys - set(d))
        extra = sorted(set(d) - base_keys)
        if missing:
            problems.append(f"[{lang}] missing {len(missing)} key(s): {', '.join(missing[:8])}"
                            + (" ..." if len(missing) > 8 else ""))
        if extra:
            problems.append(f"[{lang}] has {len(extra)} key(s) absent from {BASE}: {', '.join(extra[:8])}")
        for k in sorted(base_keys & set(d)):
            bp, lp = ph(base[k]), ph(d[k])
            if bp != lp:
                problems.append(f"[{lang}.{k}] placeholders differ: {BASE}={sorted(bp)} {lang}={sorted(lp)}")
            bt, lt = tags(base[k]), tags(d[k])
            if bt != lt:
                problems.append(f"[{lang}.{k}] HTML tags differ: {BASE}={bt} {lang}={lt}")

    # every key the backend may emit must resolve in EVERY language
    py = {}
    for name in ("scanner.py", "fingerprint.py", "__init__.py", "purge_engine.py"):
        p = COMPONENT / name
        if p.is_file():
            try:
                py[name] = p.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                print(f"FATAL: {name} is not valid UTF-8: {exc}", file=sys.stderr)
                return 2
    emitted = backend_keys(py)
    unknown = sorted(emitted - base_keys)
    if unknown:
        problems.append(f"backend emits {len(unknown)} key(s) absent from {BASE}: {', '.join(unknown[:10])}")
    for lang in sorted(i18n):
        gap = sorted(emitted - set(i18n[lang]))
        if gap:
            problems.append(f"[{lang}] missing {len(gap)} backend-emitted key(s): {', '.join(gap[:8])}")

    # every literal t('key') call site must exist
    called = set(CALL.findall(panel))
    bad_calls = sorted(called - base_keys)
    if bad_calls:
        problems.append(f"t() call site(s) with unknown key: {', '.join(bad_calls[:10])}")

    # string-based filtering on translated text cannot work across languages
    for probe in ("dg.startsWith(", "dg.toLowerCase()", "dg.includes("):
        if probe in panel:
            problems.append(f"panel filters translated text with `{probe}` — use _i18nKeyOf() instead")

    # ── cross-language contamination ──────────────────────────────────────────
    # Comparing a translation against ENGLISH cannot catch a block copied from
    # another language: the wrong text still differs from English, so key parity
    # and placeholder checks all pass. What does catch it is byte-identity
    # between two non-English dictionaries. Upstream shipped eight languages
    # whose dashLbl* family was another language's text, and nothing noticed.
    FAMILIES = {
        "dashLbl*": lambda k: k.startswith("dashLbl"),
        "reason_*": lambda k: k.startswith("reason_"),
        "card_*": lambda k: k.startswith("card_"),
        "storm_*": lambda k: k.startswith("storm_"),
        "dead_*": lambda k: k.startswith("dead_"),
        "health_*": lambda k: k.startswith("health_"),
        "fp_*": lambda k: k.startswith("fp_"),
        "dash_*": lambda k: k.startswith("dash_") and not k.startswith("dashLbl"),
    }
    others = [l for l in sorted(i18n) if l != BASE]
    for fam, pred in FAMILIES.items():
        fkeys = [k for k in base_keys if pred(k)]
        if len(fkeys) < 8:
            continue
        threshold = max(5, len(fkeys) * 0.5)
        for a_i, A in enumerate(others):
            for B in others[a_i + 1:]:
                same = [k for k in fkeys
                        if i18n[A].get(k) is not None and i18n[A].get(k) == i18n[B].get(k)
                        and i18n[A].get(k) != base.get(k)]
                if len(same) >= threshold:
                    problems.append(
                        f"[{A} / {B}] {len(same)}/{len(fkeys)} {fam} values are byte-identical "
                        f"— one of these blocks was probably copied from the other language"
                    )

    # ── vocabulary drift: a key family written in a different language ────────
    # Byte-identity between two dictionaries only catches a block copied from a
    # language that is *also* present. Upstream shipped eight languages whose
    # dashLbl* family was a THIRD language's text (fr held German, nl held
    # French, pl held Dutch, sv held Polish, hu held Swedish, cs held
    # Hungarian), so no two of them matched each other and the check above
    # stayed quiet. The reliable signal is self-consistency: a language's
    # dashLbl* words should share vocabulary with that same language's other
    # keys, not with another dictionary's.
    CLEAN_PREFIXES = ("reason_", "card_", "storm_", "dead_", "health_", "fp_", "dash_")
    WORD = re.compile(r"[\wÀ-ɏͰ-ϿЀ-ӿ一-鿿]{3,}", re.UNICODE)

    def own_vocab(lang: str) -> set:
        out = set()
        for k, v in i18n[lang].items():
            if k in base_keys and any(k.startswith(p) for p in CLEAN_PREFIXES):
                out |= {w.lower() for w in WORD.findall(v)}
        return out

    def probe_tokens(lang: str) -> set:
        out = set()
        for k, v in i18n[lang].items():
            if k.startswith("dashLbl"):
                out |= {w.lower() for w in WORD.findall(v)}
        return out

    if all("dashLbl" in i18n[l] for l in i18n):
        vocabs = {l: own_vocab(l) for l in i18n}
        for lang in sorted(i18n):
            toks = probe_tokens(lang)
            if len(toks) < 8:
                continue
            own = len(toks & vocabs[lang])
            best_other, best_score = None, 0
            for other in i18n:
                if other == lang:
                    continue
                score = len(toks & vocabs[other])
                if score > best_score:
                    best_other, best_score = other, score
            if best_other and best_score >= 8 and best_score > own * 1.5:
                problems.append(
                    f"[{lang}] dashLbl* vocabulary matches {best_other} far better than its own "
                    f"({best_score} shared words vs {own}) - this family looks like {best_other} text"
                )

    # ── hardcoded prose that bypasses the dictionary ─────────────────────────
    # Key parity cannot see this class at all: the strings are not keys, so
    # every check above passes while the Health tab renders Vietnamese for
    # everybody. Vietnamese is the upstream source language, so a line that
    # carries Vietnamese diacritics AND is not marked `data-i18n=` AND does
    # not already go through t() is prose that was never routed for translation.
    vn = ("ăâđêôơưĂÂĐÊÔƠƯàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩị"
          "òóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ")
    dict_start = panel.find("const I18N = {")
    if dict_start != -1:
        dict_end = _match_brace(panel, dict_start + len("const I18N = ") - 1)
        body = panel[:dict_start] + panel[dict_end:]
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        for n, line in enumerate(body.split("\n"), 1):
            if not any(ch in vn for ch in line):
                continue
            if "data-i18n" in line or re.search(r"\bt\(", line):
                continue
            # elements whose text is assigned at runtime by id, and the
            # LANGUAGES table (each entry is that language's own name)
            if re.search(r'id="(?:langCurrentName|connStatusTxt)"', line):
                continue
            if re.search(r"\{\s*code:\s*'[a-z]{2}'", line):
                continue
            stripped = line.strip()
            if stripped.startswith(("//", "*", "<!--", "/*")):
                continue
            snippet = stripped[:80]
            problems.append(
                f"hardcoded Vietnamese outside i18n at panel.html~{n} "
                f"(no data-i18n, no t()): {snippet!r}"
            )

    # ── theme names: translated, and refreshed when the language changes ────
    # The theme table carried a hardcoded English `name` beside a translated
    # `descKey`, so a Chinese user saw "深色 + 蓝" under a button reading
    # "Deep Space". Two separate things have to hold, and key parity sees
    # neither: every visible theme string must go through t(), and the button
    # label must be re-written inside _applyTranslations(). Rebuilding only the
    # dropdown leaves the button frozen on the language the page booted in.
    themes = re.search(r"const THEMES = \[(.*?)\n\];", panel, re.S)
    if themes:
        entries = re.findall(r"\{\s*id:\s*'([^']+)'(.*?)\}", themes.group(1), re.S)
        for theme_id, rest in entries:
            if "nameKey" not in rest:
                problems.append(
                    f"theme '{theme_id}' has no nameKey - its name would stay "
                    f"English in every language"
                )
    for pattern, what in (
        (r"\$\{th\.name\}", "buildThemeMenu renders ${th.name} instead of t(th.nameKey)"),
        (r"themeCurrentName'\)\.textContent\s*=\s*thObj\.name",
         "the theme button is written from thObj.name instead of t(thObj.nameKey)"),
    ):
        if re.search(pattern, panel):
            problems.append(what)

    apply_start = panel.find("function _applyTranslations()")
    if apply_start != -1:
        apply_end = panel.find("\nfunction ", apply_start + 10)
        apply_body = panel[apply_start:apply_end if apply_end != -1 else len(panel)]
        if "themeCurrentName" not in apply_body:
            problems.append(
                "_applyTranslations() never touches #themeCurrentName, so the "
                "theme button keeps the language the page booted in"
            )
        if "buildThemeMenu" not in apply_body:
            problems.append(
                "_applyTranslations() never rebuilds the theme menu, so its "
                "items keep the language the page booted in"
            )

    # ── Home Assistant's own translation surface ────────────────────────────
    # The panel dictionary is not the only thing a user reads. The config flow
    # and the options dialog are rendered by Home Assistant from
    # `strings.json` (English) plus `translations/<lang>.json`, and they are a
    # completely separate namespace from `I18N` in panel.html. Key parity above
    # cannot see it at all: shipping a panel with a flawless dictionary and no
    # translations directory leaves the setup dialog showing raw field keys
    # (`scan_interval_days`, `enable_soft_delete`) in a Chinese UI.
    import json

    strings_file = COMPONENT / "strings.json"
    zh_file = COMPONENT / "translations" / "zh-Hans.json"

    def leaves(node, prefix=""):
        out = set()
        if isinstance(node, dict):
            for k, v in node.items():
                p = f"{prefix}.{k}" if prefix else k
                out |= leaves(v, p) if isinstance(v, dict) else {p}
        return out

    en_doc = zh_doc = None
    if not strings_file.is_file():
        problems.append("strings.json is missing - the config flow would show raw field keys")
    else:
        try:
            en_doc = json.loads(strings_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"strings.json is not valid JSON: {exc}")
    if not zh_file.is_file():
        problems.append(
            "translations/zh-Hans.json is missing - the config flow and the options "
            "dialog will not be translated into Chinese"
        )
    else:
        try:
            zh_doc = json.loads(zh_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"translations/zh-Hans.json is not valid JSON: {exc}")

    if en_doc is not None and zh_doc is not None:
        en_keys = leaves(en_doc)
        zh_keys = leaves(zh_doc)
        scope = lambda s: {k for k in s if k.split(".")[0] in ("config", "options", "services")}  # noqa: E731
        missing = scope(en_keys) - zh_keys
        extra = scope(zh_keys) - en_keys
        for k in sorted(missing)[:10]:
            problems.append(f"[zh-Hans] missing string for the HA UI: {k}")
        if len(missing) > 10:
            problems.append(f"[zh-Hans] ... and {len(missing) - 10} more missing HA UI strings")
        # A config-flow field with no label is what the user actually sees; say
        # so specifically instead of leaving it in a generic key diff.
        for k in sorted(missing):
            if ".data." in k:
                problems.append(
                    f"config-flow field '{k.split('.')[-1]}' would render as a raw key in the dialog"
                )
        for k in sorted(extra)[:5]:
            problems.append(f"[zh-Hans] has a string that strings.json does not: {k}")
        if not missing and not extra:
            print(f"HA UI strings: {len(scope(en_keys))} in strings.json, all present in zh-Hans")

    if not args.quiet:
        print(f"languages : {len(i18n)} ({', '.join(sorted(i18n))})")
        print(f"keys      : {len(base_keys)} (reference: {BASE})")
        print(f"t() sites : {len(called)}")
        print(f"backend   : {len(emitted)} i18n keys emitted by scanner/fingerprint")

    if problems:
        print(f"\n{len(problems)} problem(s) found:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    if not args.quiet:
        print("OK - i18n dictionaries are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
