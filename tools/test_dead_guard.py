#!/usr/bin/env python3
"""Guards against code that has been flattened into a comment.

The defect this exists for
--------------------------
`scanner.py` shipped in v1.7.9 with a whole `if / elif` block collapsed onto a
single 903-character physical line:

    if not custom_cards_known:   # ...marker assignment...   )   elif card_type not in ...

Everything after the `#` is a comment, so the file still compiled, every gate
still passed, and the release notes were true about the *intent* — but the
`custom_cards_unchecked` assignment never executed and the `elif` was gone.
The published behaviour was the exact opposite of the fix: when the Lovelace
resource list could not be read, it accused the user of unconfigured cards.

Three reasons the obvious checks missed it:
  * `py_compile` passes — it is syntactically valid.
  * grepping for `custom_cards_unchecked` finds it — it is in the file, inside a
    comment. Presence is not execution.
  * Counting `ast.Assign` nodes with a `Constant` target reads 0, but that is
    an artefact of `result["key"] = ...` having a `Subscript` target. Only a
    branch-structure check answers the real question.

So the checks here are structural:

  1  No physical line longer than MAX_LINE (box-drawing separators exempt).
  2  The `if not custom_cards_known:` branch assigns the marker, and its
     `elif card_type not in registered_custom_cards:` exists.
  3  The marker is actually surfaced: panel.html reads it and the label exists
     in every language block.
  4  No Vietnamese left in comments/docstrings (the upstream author's language)
     -- diacritics alone are not enough, many Vietnamese words have none.

Known limitation of check 4
---------------------------
It is a diacritic-plus-stopword heuristic, not a language detector. A
diacritic-free Vietnamese phrase that happens to avoid every listed stopword
("so sanh gia tri hom nay") would pass. It reliably catches the upstream
residue (which is accented) and an unaccented regression built from common
words; it is not proof the tree is English-only. The counter-proof therefore
injects the real upstream line rather than an invented one.

Usage:  python3 tools/test_dead_guard.py [repo root]
Exit:   0 all checks pass, 1 a check failed, 2 could not run
"""
from __future__ import annotations

import ast
import io
import sys
import tokenize
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MAX_LINE = 300
BOX = set(chr(c) for c in range(0x2500, 0x2580))

VIET_DIACRITIC = set(
    "àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩ"
    "òóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ"
)
# Diacritic-free Vietnamese only: every word here must be checked against
# English first, or it fires on ordinary comments and the check turns to noise.
VIET_STOPWORDS = {
    "cua", "chuc", "chay", "goi", "chinh", "khac", "khong", "duoc", "tien",
    "moi", "theo", "giua", "toan", "dieu", "kiem", "phat", "hien", "nguyen",
    "nhieu", "xuat", "ghi", "luu", "xem", "dung", "sai", "loi", "truoc",
    "sau", "ngoai", "vao", "tuong", "nhe", "nhat", "hinh", "thu", "san",
    "tiep", "bien", "noi", "chung", "rieng", "mot", "hai", "bon", "muoi",
}
ALLOWED_NON_ASCII = set(
    "\u2014\u2013\u2018\u2019\u201c\u201d\u2026\u00b7\u00b1\u00d7"
    "\u2264\u2265\u00b0\u03c3\u03bc\u00b2\u00b3\u00d2\u0394"
    "\u2713\u2714\u2717\u2718\u26a0\u2192\u2261\u00a9\u00ae\u2022\u2032\u2033"
) | BOX


def _is_separator(line: str) -> bool:
    s = line.strip()
    return s.startswith("#") and set(s[1:].strip()) <= BOX


def check_no_flattened_lines(root: Path) -> list[str]:
    """Python only.

    The length heuristic fits Python and not panel.html: the panel legitimately
    carries 300-500 char lines (fixture arrays, long translation values), so
    applying the same threshold there produced five false positives on day one
    and would have trained everyone to ignore the check. panel.html gets its
    own targeted signature instead -- see check_panel_no_commented_code.
    """
    problems = []
    for path in sorted((root / "custom_components" / "ha_optimizer").glob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if len(line) > MAX_LINE and not _is_separator(line):
                head = line.strip()[:70]
                problems.append(
                    f"{path.name}:{i} line is {len(line)} chars (>{MAX_LINE}) "
                    f"-- likely code folded into a comment: {head!r}"
                )
    return problems


def check_panel_no_commented_code(root: Path) -> list[str]:
    """Flag a `//` comment in panel.html that swallowed live JS.

    The signature is a `${` after `//`: template interpolation only appears in
    executable code, so a comment containing one means a template literal got
    commented out -- the panel.html twin of the scanner.py defect.
    """
    path = root / "custom_components" / "ha_optimizer" / "panel.html"
    problems = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        idx = line.find("//")
        if idx < 0:
            continue
        before = line[:idx]
        if before.count('"') % 2 or before.count("'") % 2 or before.count("`") % 2:
            continue  # the // is inside a string, not a comment
        if "${" in line[idx:]:
            problems.append(
                f"panel.html:{i} comment contains ${{...}} -- template literal "
                f"swallowed by a comment: {line.strip()[:70]!r}"
            )
    return problems


def check_custom_card_branch(root: Path) -> list[str]:
    """The `custom_cards_known` guard must be an if/elif, not a bare if."""
    path = root / "custom_components" / "ha_optimizer" / "scanner.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as err:
        # A check that crashes is worse than no check: the suite dies with a
        # traceback instead of reporting. Report the parse failure instead.
        return [f"scanner.py does not parse: {err}"]
    problems = []
    branches = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.If) and "custom_cards_known" in ast.dump(n.test)
    ]
    if not branches:
        return ["scanner.py: no `custom_cards_known` branch found at all"]
    for node in branches:
        body_src = "\n".join(ast.unparse(s) for s in node.body)
        if "custom_cards_unchecked" not in body_src:
            problems.append(
                "scanner.py: `if not custom_cards_known:` does not assign "
                "custom_cards_unchecked -- the 'could not check' marker is dead "
                f"(body starts: {ast.unparse(node.body[0]).splitlines()[0][:70]!r})"
            )
        orelse = node.orelse
        has_elif = bool(orelse) and "not in registered_custom_cards" in "\n".join(
            ast.unparse(s) for s in orelse
        )
        if not has_elif:
            problems.append(
                "scanner.py: `if not custom_cards_known:` has no "
                "`elif card_type not in registered_custom_cards:` -- unknown "
                "state falls through to the accusation branch"
            )
    return problems


def check_marker_is_surfaced(root: Path) -> list[str]:
    panel_path = root / "custom_components" / "ha_optimizer" / "panel.html"
    panel = panel_path.read_text(encoding="utf-8")
    problems = []
    if "d.custom_cards_unchecked" not in panel:
        problems.append(
            "panel.html never reads d.custom_cards_unchecked -- the backend "
            "marker would be invisible, so a skipped check still reads as "
            "'checked, all fine'"
        )
    key = "dashCustomCardsUnchecked"
    if f"t('{key}')" not in panel and f't("{key}")' not in panel:
        problems.append(f"panel.html never renders t('{key}')")
    n = panel.count(f"{key}:")
    if n < 2:
        problems.append(
            f"'{key}' defined {n} time(s); it needs a definition in every "
            "language block (en + zh)"
        )
    return problems


def _comment_and_doc_lines(path: Path):
    """Yield (lineno, text) for comments and docstrings, using tokenize so a
    '#' inside a string is not mistaken for a comment."""
    src = path.read_text(encoding="utf-8")
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenError:
        return
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            yield tok.start[0], tok.string
        elif tok.type == tokenize.STRING and (
            tok.line.lstrip().startswith(('"""', "'''"))
            or tok.line[: tok.start[1]].strip() == ""
        ):
            body = tok.string
            for q in ('"""', "'''"):
                if body.startswith(q):
                    body = body[len(q):]
                    if body.endswith(q):
                        body = body[: -len(q)]
                    break
            for i, ln in enumerate(body.splitlines() or [""]):
                yield tok.start[0] + i, ln


def check_no_foreign_comments(root: Path) -> list[str]:
    problems = []
    comp = root / "custom_components" / "ha_optimizer"
    for path in sorted(comp.glob("*.py")):
        for lineno, text in _comment_and_doc_lines(path):
            if any(ch in VIET_DIACRITIC for ch in text):
                problems.append(f"{path.name}:{lineno} Vietnamese diacritics: {text.strip()[:70]!r}")
                continue
            words = {w.strip(".,:;()[]{}\"'").lower() for w in text.split()}
            hit = words & VIET_STOPWORDS
            if hit:
                problems.append(f"{path.name}:{lineno} Vietnamese stopword(s) {sorted(hit)}: {text.strip()[:70]!r}")
    panel = (comp / "panel.html")
    if panel.is_file():
        for i, line in enumerate(panel.read_text(encoding="utf-8").splitlines(), 1):
            if any(ch in VIET_DIACRITIC for ch in line):
                problems.append(f"panel.html:{i} Vietnamese diacritics: {line.strip()[:70]!r}")
    return problems


# ── a name is called that does not exist ─────────────────────────────────────
# Neither py_compile nor `node --check` can see this: both validate syntax, not
# names. The Dashboard tab called self._build_summary() on every click for
# several releases and nothing here noticed, and a scan threw
# "escapeHtml is not defined" because the panel has never had an escaping
# helper and the new code assumed one.

PY_BUILTIN = {
    "print", "len", "str", "int", "float", "bool", "list", "dict", "set", "tuple",
    "sorted", "sum", "min", "max", "round", "abs", "any", "all", "getattr",
    "setattr", "hasattr", "isinstance", "enumerate", "zip", "map", "filter",
    "range", "reversed", "type", "repr", "format", "super", "next", "iter",
    "frozenset", "vars", "id", "open", "input", "exec", "eval",
}


def check_undefined_python_names(root: Path) -> list[str]:
    import ast

    problems = []
    for f in sorted((root / "custom_components" / "ha_optimizer").glob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            problems.append(f"{f.name} does not parse: {exc}")
            continue
        file_classes = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            # A class that inherits from anything defined elsewhere may well get
            # the name from that base. Both `OptionsFlow` (a Name) and
            # `config_entries.ConfigFlow` (an Attribute) are external, so any
            # base that is not a class defined in this file disqualifies the
            # class from being checked - config_flow's subclasses would
            # otherwise be flagged for inheriting Home Assistant's own methods.
            external = [ast.unparse(b) for b in node.bases
                        if not (isinstance(b, ast.Name) and b.id in file_classes)]
            if external:
                continue
            defined = {n.name for n in node.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and isinstance(inner.func.value, ast.Name)
                        and inner.func.value.id == "self"
                        and inner.func.attr not in defined
                        and inner.func.attr not in PY_BUILTIN
                        and not inner.func.attr.startswith("__")):
                    problems.append(
                        f"{f.name}:{inner.lineno} {node.name}.{inner.func.attr}() "
                        f"is called but not defined in the class")
    return problems


JS_BUILTIN = {
    "t", "if", "for", "while", "switch", "catch", "return", "typeof", "function",
    "String", "Number", "Boolean", "Array", "Object", "JSON", "Math", "Date",
    "parseInt", "parseFloat", "isNaN", "isFinite", "setTimeout", "clearTimeout",
    "setInterval", "clearInterval", "requestAnimationFrame", "cancelAnimationFrame",
    "console", "fetch", "encodeURIComponent", "decodeURIComponent", "structuredClone",
    "Promise", "Error", "RegExp", "Map", "Set", "Symbol", "Intl", "FormData",
    "Blob", "URL", "TextDecoder", "TextEncoder", "escape", "unescape", "async",
    "await", "new", "typeof", "void", "delete", "in", "of", "this", "super", "yield",
    "class", "extends", "do", "else", "try", "throw", "case", "default", "var",
    "let", "const", "null", "true", "false", "undefined", "NaN", "Infinity",
    "callService", "extractData", "haShowMoreInfo",  # panel-local, defined there
    # Browser globals the panel calls. `confirm` guards the two irreversible
    # buttons, so it is the one that must not read as undefined.
    "confirm", "alert", "prompt", "open", "close", "focus", "blur", "scrollTo",
    # CSS functions that survive the style-attribute strip because the panel
    # also embeds CSS in template strings.
    "rgba", "linear-gradient", "radial-gradient", "translateX", "translateY",
    "gradient", "conic-gradient", "repeating-linear-gradient",
    "translate", "scale", "rotate", "calc", "clamp", "var", "cubic-bezier",
    "steps", "minmax", "repeat", "cubicBezier", "blur", "url", "counter",
    "attr", "format", "min", "max", "abs", "pow", "sqrt", "round_", "wrap",
}


def _skip_template(js: str, i: int) -> int:
    """Index just past the template literal whose opening backtick is at i."""
    n = len(js)
    i += 1
    while i < n:
        c = js[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            return i + 1
        if c == "$" and i + 1 < n and js[i + 1] == "{":
            i = _skip_expr(js, i + 2)
            continue
        i += 1
    return n


def _skip_expr(js: str, i: int) -> int:
    """Index of the '}' closing a `${` whose body starts at i."""
    n = len(js)
    depth = 1
    while i < n:
        c = js[i]
        if c == "\\":
            i += 2
            continue
        if c in "'\"":
            q = c
            i += 1
            while i < n and js[i] != q:
                i += 2 if js[i] == "\\" else 1
            i += 1
            continue
        if c == "`":
            i = _skip_template(js, i)
            continue
        if c == "/" and i + 1 < n and js[i + 1] == "/":
            j = js.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "/" and i + 1 < n and js[i + 1] == "*":
            j = js.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def _strip_literals(js: str) -> str:
    """Blank out comments and every kind of literal, honouring nesting.

    This replaces a set of regexes that stripped template literals as
    `` `...` ``. That cannot work here: a template literal may hold `${ ... }`
    containing another template literal, and this file does that everywhere, so
    pairing backticks sequentially mislabels every literal after the first
    nest. The mislabel boundary happened to sit somewhere harmless, so the
    check was green - and an unrelated edit elsewhere in the file moved it, and
    the check then reported three names that are defined in the file and one
    that is a browser builtin. A check whose verdict depends on where an
    unrelated edit landed is not a check.
    """
    out = []
    i, n = 0, len(js)
    while i < n:
        c = js[i]
        if c == "/" and i + 1 < n and js[i + 1] == "/":
            j = js.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "/" and i + 1 < n and js[i + 1] == "*":
            j = js.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c in "'\"":
            q = c
            i += 1
            while i < n and js[i] != q:
                if js[i] == "\\":
                    i += 1
                if i < n and js[i] == "\n":
                    break        # unterminated: do not swallow the rest of the file
                i += 1
            i += 1
            out.append(" ")
            continue
        if c == "`":
            i = _skip_template(js, i)
            out.append(" ")
            continue
        out.append(c)
        i += 1
    return "".join(out)


def check_undefined_js_names(root: Path) -> list[str]:
    import re as _re

    panel = (root / "custom_components" / "ha_optimizer" / "panel.html").read_text(encoding="utf-8")
    declared = set(_re.findall(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", panel))
    declared |= set(_re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\s*)?\(", panel))
    declared |= set(_re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*\[[^\]]*\]\.map", panel))
    # `const NAME = IDENT => ...` declares exactly what `= (...) =>` declares,
    # and the panel uses both forms. Only the parenthesised one was collected,
    # so a one-parameter arrow helper read as an undefined call.
    declared |= set(_re.findall(
        r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?[A-Za-z_$][\w$]*\s*=>",
        panel))
    # ANY `const`/`let`/`var` binding counts, whatever it is initialised to. A
    # name bound to a non-function and then called is a real defect, but a name
    # bound to a function and shadowed by a later `let val = dict[key]` in
    # another scope is not - and the narrower pattern reported the latter.
    declared |= set(_re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", panel))
    # Formal parameters bind too: `restorers.forEach(fn => fn())` is not a call
    # to an undefined `fn`.
    declared |= set(_re.findall(r"function[^(]*\(([^)]*)\)", panel))
    for params in _re.findall(r"function[^(]*\(([^)]*)\)", panel):
        for part in params.split(","):
            token = part.strip().split("=")[0].strip().lstrip(".")
            if _re.fullmatch(r"[A-Za-z_$][\w$]*", token or ""):
                declared.add(token)
    for params in _re.findall(r"\(([^()]*)\)\s*=>", panel):
        for part in params.split(","):
            token = part.strip().split("=")[0].strip().lstrip(".")
            if _re.fullmatch(r"[A-Za-z_$][\w$]*", token or ""):
                declared.add(token)
    # A single parameter needs no parentheses: `forEach(fn => fn())` binds `fn`
    # just as firmly as `(fn) =>` does.
    declared |= set(_re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*=>", panel))
    # A name reached only through `typeof X === 'function'` is a deliberate
    # optional hook: `typeof` on an undeclared identifier is safe, and the
    # branch simply does not run when nothing implements it.
    declared |= set(_re.findall(r"typeof\s+([A-Za-z_$][\w$]*)\s*===?\s*['\"]function['\"]", panel))
    # Object-literal members that hold functions, e.g. I18N entries.
    declared |= set(_re.findall(r"^\s*([A-Za-z_$][\w$]*)\s*:\s*(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>", panel, _re.M))
    declared |= set(_re.findall(r"^\s*([A-Za-z_$][\w$]*)\s*:\s*function", panel, _re.M))
    declared |= set(_re.findall(r"^\s*([A-Za-z_$][\w$]*)\s*:\s*\(p\)\s*=>", panel, _re.M))
    declared |= set(_re.findall(r"^\s*([A-Za-z_$][\w$]*)\s*:\s*(?:async\s*)?function", panel, _re.M))

    # The <style> blocks are CSS, not JavaScript: linear-gradient(), rgba() and
    # translateX() all live there and none of them is a function call.
    panel = _re.sub(r"<style[\s\S]*?</style>", " ", panel)
    scripts = _re.findall(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", panel)
    js = "\n".join(scripts)
    # Inline style attributes live in the markup, not in the script, but the
    # script builds plenty of them. rgba(), translateX() and friends would
    # otherwise read as calls to functions that do not exist.
    js = _re.sub(r'style\s*=\s*"[^"]*"', " ", js)
    js = _re.sub(r"style\s*=\s*'[^']*'", " ", js)
    # Drop comments, then string and template literals: a word that only appears
    # inside a sentence or a stylesheet is not a call. Nesting-aware, because a
    # regex cannot pair backticks across a ${ } that holds another literal.
    js = _strip_literals(js)
    # Object method shorthand / class members are not free calls.
    js = _re.sub(r"(?<![\w$])([A-Za-z_$][\w$]*)\s*:\s*function", " obj:", js)
    js = _re.sub(r"(?<![\w$])([A-Za-z_$][\w$]*)\s*\(", r"\1(", js)

    called = set(_re.findall(r"(?<![.\w$])([a-z_$][\w$]*)\s*\(", js))
    missing = sorted(called - declared - JS_BUILTIN)
    return [f"panel.html calls {m}() but nothing in the file defines it" for m in missing]


CHECKS = [
    ("no flattened lines (python)", check_no_flattened_lines),
    ("no commented-out code (panel)", check_panel_no_commented_code),
    ("custom-card guard is an if/elif", check_custom_card_branch),
    ("marker is surfaced in the panel", check_marker_is_surfaced),
    ("no foreign-language comments", check_no_foreign_comments),
    ("every self.<name>() is defined", check_undefined_python_names),
    ("every bare JS call is defined", check_undefined_js_names),
]


def run_all(root: Path) -> dict:
    return {name: fn(root) for name, fn in CHECKS}


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    if not (root / "custom_components" / "ha_optimizer").is_dir():
        print(f"not a HA Optimizer checkout: {root}")
        return 2
    failed = 0
    for name, _ in CHECKS:
        problems = run_all(root)[name]
        if problems:
            failed += 1
            print(f"FAIL  {name}")
            for p in problems:
                print(f"        {p}")
        else:
            print(f"ok    {name}")
    print("FAILED" if failed else "PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
