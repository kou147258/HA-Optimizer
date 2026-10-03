"""Every column the component's SQL names must exist in the table it reads.

1.7.22 logged this on the user's machine and nothing else noticed:

    sqlite3.OperationalError: no such column: old_state
    ... COUNT(DISTINCT old_state || '|' || new_state) ...

Nothing is wrong with that SQL's shape, its syntax, or its types. It is wrong
because `states` has no `old_state` column - and the reason is worth writing
down, because it is exactly why a type checker, a syntax check and a careful
read all pass:

Home Assistant 2026.8 still declares the name `old_state` on its `States` ORM
class, as

    old_state: Mapped[States | None] = relationship("States", remote_side=[state_id])

a self-join to the previous row, not a column. The ORM "has" `old_state`; the
table does not have the column. `new_state` does not exist anywhere, on the ORM
or in the database. So a schema check written by reading the ORM declarations
would have passed the query that broke the panel, and a schema check written
from memory would have done the same.

The columns below are therefore the class-level `mapped_column(...)` attributes
of HA 2026.8.3's `homeassistant/components/recorder/db_schema.py`, with
`relationship()` excluded - extracted with ast, which is what distinguishes
`old_state: Mapped[...] = relationship(...)` from a real column. The runtime
error and this list agree: `old_state` and `new_state` are in neither.

What this does NOT cover, stated plainly: SQL assembled at runtime by string
concatenation rather than by f-string interpolation is invisible here, and so is
anything reached through a helper in another module. Known limits, not
promises.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ha_optimizer"

# HA 2026.8.3 recorder tables: class-level mapped_column() attributes only.
SCHEMA: dict[str, set[str]] = {
    "states": {
        "state_id", "entity_id", "state", "attributes", "event_id", "last_changed",
        "last_changed_ts", "last_reported_ts", "last_updated", "last_updated_ts",
        "old_state_id", "attributes_id", "context_id", "context_user_id",
        "context_parent_id", "origin_idx", "context_id_bin", "context_user_id_bin",
        "context_parent_id_bin", "metadata_id",
    },
    "states_meta": {"metadata_id", "entity_id"},
    "events": {
        "event_id", "event_type", "event_data", "origin", "origin_idx", "time_fired",
        "time_fired_ts", "context_id", "context_user_id", "context_parent_id",
        "data_id", "context_id_bin", "context_user_id_bin", "context_parent_id_bin",
        "event_type_id",
    },
    "state_attributes": {"attributes_id", "hash", "shared_attrs"},
    "event_data": {"data_id", "hash", "shared_data"},
    "event_types": {"event_type_id", "event_type"},
    "migration_changes": {"migration_id", "version"},
    "recorder_runs": {"run_id", "start", "end", "closed_incorrect", "created"},
    "schema_changes": {"change_id", "schema_version", "changed"},
    "statistics_runs": {"run_id", "start"},
    # Not a recorder table: the MySQL-only database-size query reads it.
    "information_schema.tables": {"data_length", "index_length", "table_schema",
                                  "table_name", "table_rows"},
}

ALL_COLUMNS: set[str] = set().union(*SCHEMA.values())

KEYWORDS = {
    "SELECT", "FROM", "WHERE", "GROUP", "BY", "ORDER", "HAVING", "LIMIT", "JOIN",
    "LEFT", "RIGHT", "INNER", "OUTER", "CROSS", "ON", "AS", "AND", "OR", "NOT",
    "IN", "IS", "NULL", "DISTINCT", "CASE", "WHEN", "THEN", "ELSE", "END", "CAST",
    "INTERVAL", "DAY", "DAYS", "HOUR", "MINUTE", "SECOND", "MONTH", "YEAR", "ALL",
    "UNION", "DESC", "ASC", "EXISTS", "BETWEEN", "LIKE", "PRAGMA", "EXPLAIN",
}

# What makes a string a statement THIS check is about: it starts with a verb
# and it reads a table this check knows. Both halves are needed, and each one
# was learned from a false positive.
#
#   - start with a verb: matching the verbs anywhere, case-insensitively, read
#     English prose - "different questions" contains "from" - and turned every
#     docstring in the component into two thousand reported columns.
#   - read a known table: the verb alone still matched `"update"` used as a
#     domain name and "delete failed, entity only disabled - still tracked" used
#     as a message. Neither reads a table, and neither is SQL.
#
# The cost is stated rather than hidden: a statement with no FROM - a bare
# `SELECT COUNT(*) / 1440.0` fragment - is not analysed. `table_coverage`
# below is what stops that from quietly becoming a hole.
SQL_START = re.compile(r"^\s*(SELECT|WITH|INSERT|UPDATE|DELETE|REPLACE)\b", re.I)

STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*")
TABLE_REF = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([A-Za-z_][\w.]*)", re.I)
ALIAS_AFTER_AS = re.compile(r"\bAS\s+([A-Za-z_]\w*)", re.I)
ALIAS_AFTER_TABLE = re.compile(
    r"\b(?:FROM|JOIN)\s+[A-Za-z_][\w.]*\s+(?:AS\s+)?([A-Za-z_]\w*)", re.I)


def resolve(node: ast.AST | None, consts: dict[str, ast.AST], depth: int = 0) -> str:
    """Reconstruct SQL from an f-string, following module-level constants.

    A plain name is replaced by whatever it was assigned, so `ORDER BY
    {waste_order_expr}` is analysed as the expression it really is. Anything
    that cannot be resolved statically becomes a quoted placeholder, which the
    tokenizer then ignores - an unknown stays unknown rather than becoming a
    false alarm.
    """
    if depth > 3 or node is None:
        return "' '"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(resolve(v, consts, depth + 1) for v in node.values)
    if isinstance(node, ast.Name) and node.id in consts:
        return resolve(consts[node.id], consts, depth + 1)
    return "' '"


def docstring_nodes(tree: ast.Module) -> set[int]:
    """Identity of every docstring, so prose is never read as SQL."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and \
                isinstance(body[0].value, ast.Constant) and \
                isinstance(body[0].value.value, str):
            out.add(id(body[0].value))
    return out


def module_constants(tree: ast.Module) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.value is not None:
            out[node.target.id] = node.value
    return out


def tables_in(sql: str) -> set[str]:
    """The known tables a statement actually reads."""
    body = STRING_LITERAL.sub(" ", sql)
    return {m.group(1).lower() for m in TABLE_REF.finditer(body)} & set(SCHEMA)


def suspects(sql: str) -> list[tuple[str, str]]:
    """(identifier, why) for every column-like name the table does not have.

    Only ever called for a statement that reads a known table, so there is no
    "validate against everything" fallback: that fallback was the other half of
    the false positives, because it accepted prose and then complained about it.
    """
    body = STRING_LITERAL.sub(" ", sql)
    tables = {m.group(1).lower() for m in TABLE_REF.finditer(body)}
    known_tables = {t for t in tables if t in SCHEMA}
    if not known_tables:
        return []
    aliases = {m.group(1).lower() for m in ALIAS_AFTER_AS.finditer(body)}
    aliases |= {m.group(1).lower() for m in ALIAS_AFTER_TABLE.finditer(body)}
    aliases |= {t.rsplit(".", 1)[-1] for t in tables}

    known: set[str] = set()
    for t in known_tables:
        known |= SCHEMA[t]

    out: list[tuple[str, str]] = []
    for m in IDENT.finditer(body):
        token = m.group(0)
        tail = body[m.end():m.end() + 1]
        head = body[:m.start()].rstrip()
        if tail == "(":                       # a function call
            continue
        if head.endswith("."):                # qualifier, e.g. a table alias
            continue
        if token.upper() in KEYWORDS:
            continue
        low = token.lower()
        if low in aliases or low in tables:
            continue
        if low in known or low in ALL_COLUMNS:
            continue
        out.append((token, f"not a column of {sorted(known_tables)}"))
    return out


results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((bool(cond), name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   [{detail}]" if not cond and detail else ""))


files = sorted(p for p in COMPONENT.rglob("*.py") if "__pycache__" not in p.parts)
check("the component has python sources to check", bool(files), f"none under {COMPONENT}")

statements = 0
bad: list[str] = []
broken: list[str] = []
covered: set[str] = set()

for path in files:
    rel = path.relative_to(ROOT)
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        broken.append(f"{rel}: {exc}")
        continue
    consts = module_constants(tree)
    prose = docstring_nodes(tree)
    seen_here: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if id(node) in prose:
            continue
        if isinstance(node, ast.JoinedStr):
            sql, line = resolve(node, consts), node.lineno
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            sql, line = node.value, node.lineno
        else:
            continue
        if not SQL_START.match(sql):
            continue
        try:
            found = suspects(sql)
        except Exception as exc:  # noqa: BLE001
            broken.append(f"{rel}:{line}: the check itself failed "
                          f"({type(exc).__name__}: {exc})")
            continue
        if not found and not tables_in(sql):
            continue          # not a statement about a table this check knows
        statements += 1
        covered |= tables_in(sql)
        for ident, why in found:
            if (line, ident) in seen_here:
                continue
            seen_here.add((line, ident))
            bad.append(f"{rel}:{line}  {ident!r} {why}")

check("every module parses and the check survives it", not broken, "; ".join(broken))
check("there is SQL to check", statements > 0, "no statement matched the SQL test")
for line in bad:
    print(f"  FAIL  {line}")
check("every column the SQL names exists in its table", not bad,
      f"{len(bad)} unknown column(s) above")

# The tables the component actually reads, verified rather than assumed - the
# first draft of this list said `states_meta`, and the check was right to
# object: `SELECT metadata_id ... FROM states` reads `states`, so no statement
# in the component reads `states_meta` and pretending otherwise would have made
# the assertion permanently red and therefore permanently ignored. It is
# deliberately absent; do not add it back without a query that reads it.
EXPECTED_TABLES = {"states", "events", "information_schema.tables"}
check("every table the component reads was actually examined",
      EXPECTED_TABLES <= covered,
      "no analysed statement reads: " + ", ".join(sorted(EXPECTED_TABLES - covered)))

# The two names this whole file exists for, asserted by name as well: a check
# that cannot name the defect it was written for will eventually stop catching
# it without anybody noticing.
for gone in ("old_state", "new_state"):
    check(f"no SQL refers to {gone!r}, which is not a column of states",
          not any(f"'{gone}'" in b for b in bad))

ok = sum(1 for r in results if r[0])
print()
print(f"{ok}/{len(results)} passed, {statements} statement(s) analysed")
print("FAILED" if ok != len(results) else "PASSED")
sys.exit(0 if ok == len(results) else 1)
