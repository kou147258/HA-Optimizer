#!/usr/bin/env python3
"""One entry point for HA Optimizer's checks and its release.

Why this file exists
--------------------
The checks grew one file at a time, each with its own runner, its own output
format and a hardcoded path, and the release was a hand-rolled script per
version. That is the part of the work that was genuinely repetitive: every new
defect class cost a new pair of files, a new CI step, and a fresh round of
debugging the same tools. So this runs all of them and normalises the output.

    python3 tools/audit.py                     every check
    python3 tools/audit.py list                names only
    python3 tools/audit.py check i18n restore  only the named checks
    python3 tools/audit.py check --json r.json machine-comparable output
    python3 tools/audit.py check --verbose     show each tool's own output
    python3 tools/audit.py build               build + verify the package
    python3 tools/audit.py release 1.7.11      verify, tag, publish, read back
    python3 tools/audit.py release 1.7.11 --dry-run
    python3 tools/audit.py verify 1.7.10       what GitHub serves, checked

Output is one line per check in a fixed order, so two runs can be diffed:

    ok    pass  version.sources     all 4 sources agree on 1.7.10
    FAIL  FAIL  package.eol         store.py: mixed line endings (CRLF 12, LF 3)

Release:

    python3 tools/audit.py release 1.7.11
    python3 tools/audit.py release 1.7.11 --dry-run
    python3 tools/audit.py build            just build + verify the package
    python3 tools/audit.py verify 1.7.11    download what GitHub serves and
                                            check the CONTENT, not the hash

The build reads the git object store rather than the working tree: with
core.autocrlf=true the worktree is CRLF and the objects are LF, so packaging
the worktree ships a panel.html that differs byte-for-byte from the one on
GitHub. It selects by excluding, never by listing what to include - an earlier
version enumerated 11 filenames and silently dropped translations/ from every
release up to 1.7.1.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
COMPONENT = Path("custom_components") / "ha_optimizer"
REPO = "kou147258/HA-Optimizer"
BRANCH = "fix/ha-2026.8-frontend-compat"
PREFIX = COMPONENT.as_posix() + "/"

# Deterministic archive: a fixed timestamp so the same tree always produces the
# same bytes, which is what makes "the download equals the build" meaningful.
ZIP_DATE = (2020, 1, 1, 0, 0, 0)

EXCLUDE = (
    re.compile(r"\.pyc$"),
    re.compile(r"(^|/)__pycache__/"),
    re.compile(r"(^|/)\.DS_Store$"),
    re.compile(r"(^|/)1\.txt$"),
    re.compile(r"\.zip$"),
    re.compile(r"(^|/)\.DS_Store$"),
)

REQUIRED = (
    "manifest.json", "panel.html", "__init__.py", "config_flow.py",
    "services.yaml", "strings.json", "const.py", "store.py",
    "scanner.py", "purge_engine.py", "fingerprint.py",
    "translations/zh-Hans.json",
)


# ── reporting ──────────────────────────────────────────────────────────────
@dataclass
class Result:
    cid: str
    name: str
    ok: bool
    details: list[str] = field(default_factory=list)
    output: str = ""

    @property
    def status(self) -> str:
        return "pass" if self.ok else "FAIL"


class Audit:
    def __init__(self) -> None:
        self.results: list[Result] = []

    def add(self, cid: str, name: str, ok: bool, details: list[str] | None = None,
            output: str = "") -> Result:
        r = Result(cid, name, bool(ok), details or [], output)
        self.results.append(r)
        return r

    # ── the canonical line ──
    def render(self, verbose: bool = False) -> str:
        w = max((len(r.cid) for r in self.results), default=10)
        lines = []
        for r in self.results:
            head = f"{'ok   ' if r.ok else 'FAIL '} {r.status:4} {r.cid.ljust(w)}  {r.name}"
            if not r.ok and r.details:
                head += "  |  " + r.details[0]
                for extra in r.details[1:]:
                    head += "\n" + " " * (w + 18) + "|  " + extra
            lines.append(head)
        if verbose:
            for r in self.results:
                if r.output.strip():
                    lines.append(f"--- {r.cid} ---")
                    lines.append(r.output.rstrip())
        failed = [r for r in self.results if not r.ok]
        lines.append("")
        lines.append(f"total {len(self.results)}  pass {len(self.results) - len(failed)}  "
                     f"fail {len(failed)}")
        for r in failed:
            lines.append(f"  FAIL {r.cid}: {r.name}")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps({
            "repo": REPO,
            "branch": BRANCH,
            "head": git("rev-parse", "HEAD"),
            "tree": git("rev-parse", "HEAD^{tree}"),
            "checks": [
                {"id": r.cid, "name": r.name, "status": r.status, "details": r.details}
                for r in self.results
            ],
            "total": len(self.results),
            "failed": sum(1 for r in self.results if not r.ok),
        }, ensure_ascii=False, indent=2)


# ── process helpers ────────────────────────────────────────────────────────
class Fail(Exception):
    pass


def git(*args: str, binary: bool = False):
    r = subprocess.run(["git", "-C", str(ROOT), *args],
                       capture_output=True,
                       **({} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}))
    if r.returncode != 0:
        err = r.stderr if binary else r.stderr
        raise Fail(f"git {' '.join(args)}: {err.strip()[:200]}")
    return r.stdout if binary else r.stdout


def gh_json(path: str):
    return json.loads(gh(["api", path]))


def gh_post_json(path: str, body: dict):
    """POST a JSON body. Never pass non-ASCII on the command line: the shell
    and gh mangle it, and a mangled release title is worse than a failed one."""
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return json.loads(gh(["api", path, "-X", "POST", "--input", "-"], stdin=raw))


def gh(args: list[str], stdin: bytes | None = None, timeout: int = 300) -> str:
    """Run gh with bounded retries on transient network failure.

    Reads are retried freely. Writes are NOT: `gh` reports a flaky 404 on a
    first attempt as a hard error, and retrying a write can duplicate a release
    or upload an asset twice. Callers that write must therefore be idempotent
    and check the state first.
    """
    is_read = not any(a in ("-X", "--method") for a in args)
    last: Exception | None = None
    for attempt in range(1, 6):
        try:
            r = subprocess.run(["gh", *args], input=stdin, capture_output=True,
                               timeout=timeout)
            if r.returncode == 0:
                return r.stdout.decode("utf-8", "replace")
            out = (r.stdout + r.stderr).decode("utf-8", "replace")
            transient = re.search(
                r"dial tcp|connectex|reset|Recv failure|timeout|TLS|EOF|"
                r"no such host|temporarily|502|503|504", out, re.I)
            if not (is_read and transient):
                raise Fail(f"gh {' '.join(args[:3])}: {out.strip()[:300]}")
            last = Fail(out.strip()[:300])
        except subprocess.TimeoutExpired as exc:
            last = exc
        if attempt < 5:
            time.sleep(attempt * 2.0)
    raise Fail(str(last))


# ── checks: existing tools, run as-is ──────────────────────────────────────
# Each is invoked as a subprocess and reduced to pass/fail plus its output, so
# the checks keep their own text and the report stays one format.
TOOL_CHECKS: list[tuple[str, str, list[str]]] = [
    ("i18n.dictionary", "translation dictionaries agree", ["check_i18n.py"]),
    ("i18n.param_shapes", "a translated string is called with the shape it reads",
     ["test_i18n_param_shapes.py"]),
    ("i18n.param_shapes.cp", "the param-shape check can still fail",
     ["counterproof_i18n_param_shapes.py"]),
    ("version.sources", "all version sources agree", ["check_version.py"]),
    ("purge.safety", "purge safety regressions", ["test_purge_safety.py"]),
    ("purge.safety.cp", "a commented-out purge guard is not readable as code",
     ["counterproof_purge_safety.py"]),
    ("purge.tracking", "a disabled entity is recorded before the next is disabled",
     ["test_purge_tracking.py"]),
    ("panel.sync", "panel and service registration", ["test_panel_sync.py"]),
    ("panel.scan_contract", "the panel reads only keys the scan emits",
     ["test_scan_contract.py"]),
    ("panel.scan_contract.cp", "the scan-contract check can still fail",
     ["counterproof_scan_contract.py"]),
    ("panel.build_stamp", "build stamp and cache busting", ["test_build_stamp.py"]),
    ("panel.filters", "filter behaviour", ["test_filters.js"]),
    ("trash.bulk", "trash bulk operations", ["test_trash_bulk.py"]),
    ("panel.ui_labels", "UI label and control naming", ["test_ui_labels.py"]),
    ("panel.renders", "every render entry point runs, and no DOM writer is undeclared",
     ["test_panel_renders.py"]),
    ("panel.renders.cp",
     "the render check catches a neutered renderer, a blank render and a swallowed throw",
     ["counterproof_panel_renders.py"]),
    ("panel.ui_labels.cp", "UI label checks can still fail", ["counterproof_ui_labels.js"]),
    ("restore.truth", "restore truth regressions", ["test_restore_truth.py"]),
    ("restore.truth.cp", "restore truth checks can still fail", ["counterproof_restore.py"]),
    ("restore.engine", "restore engine behaviour", ["test_restore_engine.py"]),
    ("safety.gate", "the gate on the irreversible job", ["test_safety_gate.py"]),
    ("safety.gate.cp", "safety gate checks can still fail", ["counterproof_safety_gate.py"]),
    ("store.concurrency", "store locking and merging", ["test_store_concurrency.py"]),
    ("store.records", "trash records are copied, and 0 days expires nothing",
     ["test_store_records.py"]),
    ("fail.loud", "no silent failures", ["test_fail_loud.py"]),
    ("compat.claims", "compatibility claims", ["test_compat_claims.py"]),
    ("live_log", "live-log regressions", ["test_live_log_findings.py"]),
    ("source.dead_guard", "flattened lines and dead guards", ["test_dead_guard.py"]),
    ("source.dead_guard.cp", "dead-guard checks can still fail", ["counterproof_dead_guard.py"]),
    ("source.dead_guard.tpl", "the dead-guard stripper sees code, not text",
     ["counterproof_dead_guard_templates.py"]),
    ("identity.rename", "registry identity behaviour", ["test_rename_identity.py"]),
    ("identity.rename.cp", "identity checks can still fail", ["counterproof_rename_identity.py"]),
    ("source.trace_join", "traces are read the way HA stores them", ["test_trace_join.py"]),
    # Every other check imports or execs ONE file. This one loads the whole
    # component the way Home Assistant does, as a package, so a module broken
    # for all of them and fine here is still caught.
    ("package.imports", "the whole package loads as Home Assistant loads it",
     ["test_package_imports.py"]),
    ("package.imports.cp", "the package-load check can still fail",
     ["counterproof_package_imports.py"]),    ("source.trace_join.cp", "trace-join checks can still fail", ["counterproof_trace_join.py"]),
    # It was on disk for releases and never listed here, so it had been failing
    # silently - which is how a stub went stale against the code it was checking.
    ("source.untraced_lists", "automations are listed even with no traces",
     ["test_untraced_lists.py"]),
    ("source.py_names", "every name the component calls is defined", ["test_py_names.py"]),
    ("source.py_names.cp", "py-name checks can still fail", ["counterproof_py_names.py"]),
    ("source.noop_ternary", "no conditional does nothing while looking like it does",
     ["test_no_noop_ternaries.py"]),
    ("source.recorder_sql", "SQL columns exist in the recorder schema", ["test_recorder_sql.py"]),
    ("source.recorder_sql.cp", "recorder-SQL checks can still fail",
     ["counterproof_recorder_sql.py"]),
    # A stored baseline day and this morning were being averaged as if they
    # were the same measurement, and the top-writer tag compared a 06:00 figure
    # against a whole-day peak - so it could not fire before twenty hours in.
    ("fingerprint.windows", "a baseline day and this morning are one measurement",
     ["test_fingerprint_windows.py"]),
    ("fingerprint.windows.cp", "the window checks can still fail",
     ["counterproof_fingerprint_windows.py"]),
    ("tools.reach_verdict", "no tool ends its run before its own assertions",
     ["test_check_tools.py"]),
    ("tools.reach_verdict.cp", "the unreachable-tail check can still fail",
     ["counterproof_check_tools.py"]),
    # Every service handler closes over the ConfigEntry parameter `entry`. A
    # local of the same name in one of them made Python treat EVERY `entry` in
    # that function as local, including the two reads above it, so every soft
    # purge died with a bare HTTP 500 and nothing in the panel said why.
    ("source.no_shadowing", "no function reads a name before it is assigned",
     ["test_python_shadowing.py"]),
    ("source.no_shadowing.cp", "the shadowing check can still fail",
     ["counterproof_python_shadowing.py"]),
    # The seven common-cause explanations were English prose in the backend and
    # the panel printed them verbatim. `t()` with a DYNAMIC key is the one call
    # shape no param-shape check can read, so it needs its own.
    ("auto.diagnosis_i18n", "every diagnosis is a key, and every key is translated",
     ["test_automation_i18n.py"]),
    ("auto.diagnosis_i18n.cp", "prose, or a key in one language only, is caught",
     ["counterproof_automation_i18n.py"]),
    # A hard delete of a UI automation did nothing and said it was YAML. The
    # registry entry for a UI automation carries no config_entry_id on 2026.8,
    # and "no config entry" was read as "therefore YAML".
    ("purge.hard_delete", "a hard delete removes it, or says plainly it did not",
     ["test_hard_delete_honesty.py"]),
    # The harness's own trust boundary: an injected defect plus a lying exit
    # code must not read as a pass. It also asserts a clean tree still passes,
    # so it cannot be satisfied by a harness that calls everything red.
    ("tools.audit_trust", "the harness rejects a check that lies about itself",
     ["test_audit_trust.py"]),
    ("release.bom", "the release survives a title file with a BOM",
     ["test_release_text.py"]),
    # Each of these measures its own check twice: the current rule must catch
    # the injected defect and the pre-fix rule must not, so "the check went red"
    # cannot be satisfied by a broken file. The pre-fix rules are frozen under
    # tools/prefix_rules/ - reading them from git works until the fix lands, and
    # then there is nothing left to compare against.
    ("panel.scan_contract.cp.wrong_dict", "the key cannot be parked in an unrelated dict",
     ["counterproof_scan_contract_wrong_dict.py"]),
    ("source.noop_ternary.cp.branches", "the no-op check sees the if/else form",
     ["counterproof_noop_ternaries_branches.py"]),
    ("i18n.param_shapes.cp.call_form", "the param-shape check sees the call form",
     ["counterproof_i18n_param_shapes_call_form.py"]),
]


# A tool has to SAY it passed, and its arithmetic has to add up.
#
# The exit code alone was the entire trust boundary of this file, and an audit
# of the suite proved it could be crossed in one line: a check that printed
# "7/10 passed" plus three FAIL lines and then `sys.exit(0)` was reported here
# as `ok  pass`. Every check's own verdict line was decorative.
#
# The rule is read off what the tools actually print, not guessed. The first
# version guessed and turned nine genuinely-green checks red, because a
# counter-proof legitimately PRINTS `FAIL` lines: that is the injected defect's
# report, indented, and its own last line says the defect was caught.
#
# Which means the meaning of an indented `FAIL` is not a property of the output
# at all - it is a property of WHAT KIND OF TOOL this is, and only the caller
# knows that. Every check in the directory reports its failed assertions as
# `  FAIL  <name>`, so the "indented is data" exemption covered precisely what
# real checks emit, and a check that printed `10/10 passed` + `PASSED` next to
# three indented FAILs was read as a pass. The harness knows the kind from the
# file it is running, so it says so:
#
#   * a CHECK: any `FAIL` line, indented or not, is a failed assertion;
#   * a COUNTER-PROOF: a `FAIL` at column 0 is its own verdict, and an
#     indented one is the defect it was asked to catch;
#   * either way the verdict is the last line that LOOKS like one and is not a
#     failure, because log output can follow it (an engine's own warnings do);
#   * `N/M passed` has to satisfy N == M.
VERDICT_SHAPE = re.compile(
    r"^(?:PASSED\b|OK\b|\d+\s*/\s*\d+\s*passed\b|.*\ball\b.*\bpassed\b)", re.I)
COUNTED = re.compile(r"(\d+)\s*/\s*(\d+)\s*passed\b", re.I)
A_FAILURE = re.compile(r"^\s*(?:FAILED|FAIL)\b")


def is_failure_line(ln: str, counterproof: bool) -> bool:
    """Is this line the tool saying something did not pass?

    The one asymmetry is indentation, and it is only an exemption for a
    counter-proof: there the indented FAIL is the defect under test, echoed on
    purpose. A check has no such thing - its indented FAILs are its results.
    """
    return bool(A_FAILURE.match(ln)) and not (counterproof and ln[:1].isspace())


def tool_verdict(out: str, counterproof: bool = False) -> tuple[bool, str]:
    """Did this tool say it passed? Returns (believed_pass, evidence)."""
    lines = [ln.rstrip() for ln in out.splitlines() if ln.strip()]
    for ln in lines:
        if is_failure_line(ln, counterproof):
            return False, f"reported a failure: {ln.strip()[:60]!r}"
    # A count that does not add up is the tool admitting that something failed,
    # and it is an admission wherever it appears - not only in the line that
    # wins the verdict search. An honest `7/10 passed` followed by an
    # overriding `PASSED` is still an admission. A counter-proof echoes the
    # mutated check's whole output, including its count, so its indented copy
    # is data there; its own lines are its own.
    for ln in lines:
        if counterproof and ln[:1].isspace():
            continue
        m = COUNTED.search(ln)
        if m and m.group(1) != m.group(2):
            return False, f"its own count does not add up: {ln.strip()[:60]!r}"
    # A line that is itself a failure is never the verdict, however much it
    # looks like one: `  FAIL  missing the all checks passed line` matches the
    # "all ... passed" shape, and quoting it as the passing verdict is the one
    # reading of this rule that reports a failure as a pass.
    verdict = next((ln.strip() for ln in reversed(lines)
                    if VERDICT_SHAPE.match(ln.strip())
                    and not A_FAILURE.match(ln)), None)
    if verdict is None:
        return False, "printed no verdict line at all"
    return True, verdict


def check_tool(audit: Audit, cid: str, name: str, argv: list[str]) -> None:
    exe = ["node"] if argv[0].endswith(".js") else [sys.executable]
    r = subprocess.run([*exe, str(TOOLS / argv[0])], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    out = (r.stdout or "") + (r.stderr or "")
    claimed_pass, evidence = tool_verdict(
        out, counterproof=Path(argv[0]).name.startswith("counterproof_"))
    details = []
    if r.returncode != 0:
        details = [ln.strip(" -│") for ln in out.splitlines()
                   if ln.strip().startswith("FAIL") or "FAILED" in ln][:6]
        details = [d for d in details if d] or [f"exit {r.returncode}"]
    elif not claimed_pass:
        details = [f"exited 0 but {evidence}"]
    audit.add(cid, name, r.returncode == 0 and claimed_pass, details, out)


# ── checks: run in-process, no child process ───────────────────────────────
def check_package_eol(audit: Audit) -> None:
    """One line ending per file, no BOM, no mixed endings.

    The worktree is CRLF and the objects are LF, so this checks the OBJECTS:
    what ships is what the git database holds, not what the editor left behind.
    A file that is the only CRLF blob in the repo makes every diff to it show
    as a whole-file rewrite, which hides the real change.
    """
    details = []
    for name in sorted(p.name for p in (ROOT / COMPONENT).iterdir() if p.is_file()):
        try:
            raw = git_bytes("show", f"HEAD:{PREFIX}{name}")
        except Exception as exc:  # noqa: BLE001
            # A brand-new, uncommitted file. Reporting that is useful; raising
            # would abort the whole audit, which is how a check becomes worse
            # than no check - the first run after adding a file died here.
            details.append(f"{name}: not in HEAD yet ({str(exc)[:60]})")
            continue
        crlf = raw.count(b"\r\n")
        lf = raw.count(b"\n") - crlf
        if crlf and lf:
            details.append(f"{name}: mixed (CRLF {crlf}, bare LF {lf})")
        elif crlf:
            details.append(f"{name}: CRLF in the object store "
                           f"(every other file is LF - a whole-file diff follows)")
        if raw.startswith(b"\xef\xbb\xbf"):
            details.append(f"{name}: has a BOM")
    audit.add("package.eol", "objects are LF, no BOM, no mixed endings",
              not details, details)


def check_version_tagged(audit: Audit) -> None:
    """The version on disk is the version the panel will report."""
    manifest = json.loads(git("show", f"HEAD:{PREFIX}manifest.json"))
    const = git("show", f"HEAD:{PREFIX}const.py")
    m = re.search(r'VERSION\s*=\s*"([^"]+)"', const)
    panel = git("show", f"HEAD:{PREFIX}panel.html")
    stamped = "?v=" in panel or "buildVersion" in panel
    details = []
    if not m or m.group(1) != manifest["version"]:
        details.append(f"const.py says {m and m.group(1)}, manifest says {manifest['version']}")
    if not stamped:
        details.append("panel.html has no build stamp")
    audit.add("release.version", "disk version is consistent and stampable",
              not details, details)


def check_commit_contains(audit: Audit) -> None:
    """Tracked files are committed.

    Untracked files are ignored on purpose. The package is built from the git
    object store, so an untracked file cannot reach an install, and including
    them makes this check non-idempotent: the audit's own JSON report is
    untracked, so the second run of the same tree would report a different
    result than the first.
    """
    dirty = [ln for ln in git("status", "--porcelain").splitlines()
             if ln.strip() and not ln.startswith("??")]
    audit.add("repo.clean", "nothing uncommitted among tracked files",
              not dirty, dirty[:8])


# ── packaging ──────────────────────────────────────────────────────────────
def package_files(rev: str = "HEAD") -> list[str]:
    allf = git("ls-tree", "-r", "--name-only", rev).split("\n")
    return [f for f in allf if f.startswith(PREFIX) and not any(e.search(f) for e in EXCLUDE)]


def check_package_contents(audit: Audit, files: list[str]) -> None:
    names = {f[len(PREFIX):] for f in files}
    details = [f"missing {w}" for w in REQUIRED if w not in names]
    # Parse the JSON we are about to ship. A translations file that does not
    # parse unzips fine and only fails much later, on the user's instance.
    for jf in ("strings.json", "translations/zh-Hans.json", "manifest.json"):
        if jf in names:
            try:
                json.loads(git("show", f"HEAD:{PREFIX}{jf}"))
            except Exception as exc:  # noqa: BLE001
                details.append(f"{jf} does not parse: {exc}")
    if "strings.json" in names and "translations/zh-Hans.json" in names:
        s = set(json.loads(git("show", f"HEAD:{PREFIX}strings.json")))
        z = set(json.loads(git("show", f"HEAD:{PREFIX}translations/zh-Hans.json")))
        if s - z:
            details.append(f"untranslated HA UI strings would ship: {sorted(s - z)}")
    audit.add("package.contents", "required files present, JSON parses, "
              "translations complete", not details, details)


def git_bytes(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, check=True).stdout


def build_package(rev: str = "HEAD", out_dir: Path | None = None) -> tuple[Path, str, int]:
    """Build the asset from the git object store, then read it back.

    The read-back is not ceremony: the build is the component that silently
    dropped translations/ from every release up to 1.7.1, and nothing about
    that failure is visible from the builder's own return value. So the archive
    is reopened and every entry is compared to the blob it came from, before
    the file is written anywhere.
    """
    files = package_files(rev)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files:
            data = git_bytes("show", f"{rev}:{f}")
            info = zipfile.ZipInfo(f, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    raw = buf.getvalue()

    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = sorted(z.namelist())
        if names != sorted(files):
            raise Fail(f"package read-back: {sorted(set(names) ^ set(files))}")
        for f in files:
            if z.read(f) != git_bytes("show", f"{rev}:{f}"):
                raise Fail(f"package read-back: {f} does not match the git object")
        for f in files:
            if f.startswith(PREFIX + "translations/"):
                json.loads(z.read(f).decode("utf-8"))

    # Build the ref string before the assignment: inside an f-string, `tree`
    # on the right-hand side is the local being assigned on this very line.
    tree_sha = git("rev-parse", "%s^{tree}" % rev).strip()
    name = f"ha_optimizer-zh-cn-{tree_sha[:7]}.zip"
    # Built outside the checkout: a release artefact in the working tree makes
    # `repo.clean` fail and tempts someone into committing a binary.
    out = (out_dir or ROOT.parent) / name
    out.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    return out, sha, len(raw)


# ── verification of what GitHub actually serves ────────────────────────────
def remote_tree(rev: str) -> tuple[str, str, dict[str, str]]:
    """Resolve a ref to (commit sha, {path: blob sha}) through the GitHub API.

    The tags in this repo are created through the API, so a local fetch is the
    only way to get one, and git is not reliable on this machine. Reading the
    objects from the API is also the more correct invariant: what is being
    verified is that the download matches the tagged commit, not that it
    matches whatever happens to be checked out now.
    """
    commit = gh_json(f"repos/{REPO}/commits/{rev}")["sha"]
    tree_sha = gh_json(f"repos/{REPO}/commits/{rev}")["commit"]["tree"]["sha"]
    tree = gh_json(f"repos/{REPO}/git/trees/{tree_sha}?recursive=1")
    blobs = {e["path"]: e["sha"] for e in tree.get("tree", [])
             if e.get("type") == "blob"}
    return commit, tree_sha, blobs


def remote_blob(sha: str) -> bytes:
    data = gh_json(f"repos/{REPO}/git/blobs/{sha}")
    if data.get("encoding") == "base64":
        return base64.b64decode(data["content"])
    return data["content"].encode("utf-8")


def verify_served(tag: str, tree7: str, audit: Audit) -> None:
    """Download, unpack, and compare CONTENT against the tag's own objects.

    A matching hash only proves the download equals the build. The build is
    what dropped translations/ from every release up to 1.7.1, so hash
    equality stayed green while a whole directory was missing.
    """
    tmp = ROOT / ".audit-dl"
    if tmp.exists():
        import shutil
        shutil.rmtree(tmp)
    tmp.mkdir()
    try:
        gh(["release", "download", tag, "--repo", REPO, "--pattern", "*.zip",
            "--clobber", "--dir", str(tmp)])
        zips = list(tmp.glob("*.zip"))
        details = []
        if not zips:
            audit.add("release.asset", "the release has an asset", False,
                      ["no .zip asset on the release"])
            return
        asset = zips[0]
        if tree7 and tree7 not in asset.name:
            details.append(f"asset is {asset.name}, expected the {tree7} build")
        commit, _tree_sha, blobs = remote_tree(tag)
        want = sorted(p for p in blobs
                      if p.startswith(PREFIX) and not any(e.search(p) for e in EXCLUDE))
        with zipfile.ZipFile(asset) as z:
            got = sorted(z.namelist())
            if got != want:
                details.append(
                    f"file list differs: only in the package "
                    f"{sorted(set(got) - set(want))}, "
                    f"missing {sorted(set(want) - set(got))}")
            for rel in want:
                if rel in got and z.read(rel) != remote_blob(blobs[rel]):
                    details.append(f"content differs from the tagged blob: {rel}")
            # The directory 1.7.2 had to fix, asserted by name so the
            # regression cannot come back quietly.
            if not any(p.startswith(PREFIX + "translations/") for p in got):
                details.append("translations/ is missing from the package")
            for jf in (PREFIX + "strings.json", PREFIX + "translations/zh-Hans.json"):
                if jf in got:
                    try:
                        json.loads(z.read(jf).decode("utf-8"))
                    except Exception as exc:  # noqa: BLE001
                        details.append(f"{jf} in the package does not parse: {exc}")
        audit.add("release.asset",
                  f"what GitHub serves is what {tag} ({commit[:7]}) holds",
                  not details, details)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ── release ────────────────────────────────────────────────────────────────
def do_release(version: str, dry_run: bool, notes: Path, title: Path,
               notes_only: bool = False) -> int:
    tag = f"v{version}"
    # A BOM is stripped rather than obeyed. PowerShell's `Set-Content -Encoding
    # UTF8` writes one, and reading a title that carries it put U+FEFF in
    # front of the release name - which then could not even be printed on a
    # gbk console, so the release died after packaging and before tagging.
    # `.strip()` does not remove it, and it is invisible in most editors, which
    # is why it survived two attempts to write the same file.
    text = title.read_text(encoding="utf-8-sig").strip()
    body = notes.read_text(encoding="utf-8-sig")
    if "\ufffd" in text + body:
        print("ABORT: replacement character in the release text")
        return 1
    if not text or not body:
        print("ABORT: empty title or body")
        return 1

    if notes_only:
        # Correcting the description of a release that is already out. Nothing
        # is rebuilt and the tag is not touched: the bytes users install are
        # already published, and the only thing wrong is the text describing
        # them. Verified by reading it back, because a silent no-op here would
        # look exactly like success.
        if not release_exists(tag):
            print(f"ABORT: {tag} does not exist")
            return 2
        gh(["release", "edit", tag, "--repo", REPO, "--title", text,
            "--notes-file", str(notes)])
        rel = gh_json(f"repos/{REPO}/releases/tags/{tag}")
        same_title = rel.get("name") == text
        same_body = rel.get("body", "") == body
        print(f"title matches: {same_title}")
        print(f"body  matches: {same_body}  ({len(rel.get('body', ''))} chars)")
        if not (same_title and same_body):
            print("the edit did not take")
            return 1
        print(f"{tag} description updated")
        return 0

    # The version on disk must be the version being released. This gate exists
    # because two releases shipped as 1.7.17 and 1.7.18 while const.py still
    # said 1.7.16 - the panel URL is built from VERSION, so `?v=` never changed,
    # /local/ kept serving 31-day-cached bytes, and the delivered files could not
    # reach the browser at all. check_version only proves the four sources agree
    # with each other, never with the release.
    on_disk = json.loads(git("show", f"HEAD:{PREFIX}manifest.json"))["version"]
    const_v = re.search(r'VERSION\s*=\s*"([^"]+)"',
                        git("show", f"HEAD:{PREFIX}const.py")).group(1)
    file_v = re.search(r"PANEL_FILE_BUILD\s*=\s*'([^']+)'",
                       git("show", f"HEAD:{PREFIX}panel.html"))
    file_v = file_v.group(1) if file_v else None
    if on_disk != version:
        print(f"ABORT: the tree says {on_disk} (const.py {const_v}, "
              f"panel.html {file_v}) but you asked to release {version}.")
        print("       Bump the version first - the panel URL is built from it, "
              "so a mismatch pins every browser to a cached file.")
        return 2
    if const_v != version or (file_v and file_v != version):
        print(f"ABORT: const.py={const_v} panel.html={file_v} do not match "
              f"manifest.json={on_disk}.")
        return 2

    audit = Audit()
    files = package_files("HEAD")
    check_package_contents(audit, files)
    check_package_eol(audit)
    if [r for r in audit.results if not r.ok]:
        print(audit.render())
        print("\nrefusing to release: the package does not verify")
        return 1
    print("package verifies:", len(files), "files")

    # No second read of the title here. There was one, with encoding="utf-8",
    # and it silently overwrote the BOM-stripped read above - so a title file
    # written by PowerShell's default `Set-Content -Encoding UTF8` still
    # arrived with a U+FEFF in front of it, and the release died here with a
    # UnicodeEncodeError on a gbk console, after packaging and before tagging.
    # The same duplicate-block shape that made thirteen assertions in
    # test_trace_join.py unreachable.
    print(f"title   : {text}")
    print(f"body    : {len(body)} chars")
    if dry_run:
        print("\ndry run: stopping before tag/upload")
        return 0

    asset, sha, size = build_package()
    print(f"asset   : {asset.name}  {size} bytes  sha256 {sha[:16]}...")

    head = gh_json(f"repos/{REPO}/branches/{quote(BRANCH)}")["commit"]["sha"]
    tree = git("rev-parse", "HEAD^{tree}").strip()
    # The tag must point at what is being released. Creating it at the branch
    # head while the package is built from the local HEAD tags the PREVIOUS
    # commit and ships an asset that does not match its own tag - which is what
    # happened once, and the loop-back caught it only by comparing the download
    # against the tag.
    #
    # Trees, not commit SHAs: a commit recreated through the API has a different
    # SHA from its local twin (different committer and timestamp) while its tree
    # is byte-identical, so comparing SHAs would refuse every push-based release.
    local_tree = tree
    remote_tree = gh_json(f"repos/{REPO}/commits/{head}")["commit"]["tree"]["sha"]
    if local_tree != remote_tree:
        print(f"ABORT: the local tree {local_tree[:7]} is not the branch head's "
              f"tree {remote_tree[:7]}. Push first - a tag created now would "
              f"point at the previous commit while the package is built from "
              f"this one.")
        return 2
    if tree[:7] not in asset.name:
        print(f"ABORT: asset built from tree {tree[:7]}, branch head is {head[:7]}")
        return 1

    exists = ref_exists(f"repos/{REPO}/git/ref/tags/{tag}")
    if exists:
        print(f"tag {tag} already exists - not moving it")
    else:
        gh_post_json(f"repos/{REPO}/git/refs", {"ref": f"refs/tags/{tag}", "sha": head})
        print(f"tag {tag} -> {head[:7]}")

    # Idempotent: a release with no asset is a half-finished release, and a
    # blind retry of the upload is how you get two of them.
    if not release_has_asset(tag, asset.name):
        if release_exists(tag):
            gh(["release", "edit", tag, "--repo", REPO, "--title", text,
                "--notes-file", str(notes)])
            print("release notes updated")
        else:
            gh(["release", "create", tag, "--repo", REPO, "--title", text,
                "--notes-file", str(notes), "--target", head])
            print("release created")
        gh(["release", "upload", tag, str(asset), "--repo", REPO, "--clobber"])
        print(f"uploaded {asset.name}")
    else:
        print(f"asset already present on {tag}")

    verify_served(tag, tree[:7], audit)
    print()
    print(audit.render())
    return 1 if any(not r.ok for r in audit.results) else 0


def quote(s: str) -> str:
    from urllib.parse import quote as q
    return q(s, safe="")


def ref_exists(path: str) -> bool:
    try:
        gh(["api", path])
        return True
    except Fail:
        return False


def release_exists(tag: str) -> bool:
    return ref_exists(f"repos/{REPO}/releases/tags/{tag}")


def release_has_asset(tag: str, name: str) -> bool:
    try:
        rel = gh_json(f"repos/{REPO}/releases/tags/{tag}")
    except Fail:
        return False
    return any(a.get("name") == name for a in rel.get("assets", []))


# ── main ───────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(
        prog="audit.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    p_check = sub.add_parser("check", help="run the checks (the default)")
    p_check.add_argument("names", nargs="*", help="only these check ids")
    p_check.add_argument("--json", metavar="PATH")
    p_check.add_argument("--verbose", action="store_true")
    sub.add_parser("list", help="list the check ids")

    sub.add_parser("build", help="build and verify the package, no network")

    p_rel = sub.add_parser("release", help="verify, tag, publish, then read back")
    p_rel.add_argument("version")
    p_rel.add_argument("--dry-run", action="store_true")
    p_rel.add_argument("--notes-only", action="store_true",
                       help="update title and body only: no tag, no rebuild, "
                            "no upload - for correcting the text of a release "
                            "that was described badly")
    p_rel.add_argument("--notes")
    p_rel.add_argument("--title")

    p_ver = sub.add_parser("verify", help="check what GitHub serves for a tag")
    p_ver.add_argument("tag")

    args = ap.parse_args()
    cmd = args.cmd or "check"

    if cmd == "list":
        for cid, name, _ in TOOL_CHECKS:
            print(f"{cid:24} {name}")
        for cid, name in (("package.eol", "objects are LF, no BOM, no mixed endings"),
                          ("release.version", "disk version is consistent and stampable"),
                          ("repo.clean", "nothing uncommitted among tracked files")):
            print(f"{cid:24} {name}")
        return 0

    if cmd == "build":
        a = Audit()
        files = package_files("HEAD")
        check_package_contents(a, files)
        check_package_eol(a)
        print(a.render())
        if any(not r.ok for r in a.results):
            return 1
        asset, sha, size = build_package()
        print(f"built {asset.name}  {size} bytes  sha256 {sha}")
        return 0

    if cmd == "release":
        v = args.version.replace(".", "")
        notes = Path(args.notes) if args.notes else TOOLS / f"notes-{v}.md"
        title = Path(args.title) if args.title else TOOLS / f"title-{v}.txt"
        for p in (notes, title):
            if not p.is_file():
                print(f"missing {p}")
                return 2
        return do_release(args.version, args.dry_run, notes, title, args.notes_only)

    if cmd == "verify":
        tag = args.tag if args.tag.startswith("v") else f"v{args.tag}"
        a = Audit()
        tree7 = ""
        try:
            _, tree_sha, _ = remote_tree(tag)
            tree7 = tree_sha[:7]
        except Fail as exc:
            print(f"could not resolve {tag}: {exc}")
        verify_served(tag, tree7, a)
        print(a.render())
        return 1 if any(not r.ok for r in a.results) else 0

    # check
    audit = Audit()
    wanted = set(getattr(args, "names", []) or [])
    for cid, name, argv in TOOL_CHECKS:
        if not wanted or cid in wanted:
            check_tool(audit, cid, name, argv)
    if not wanted or "package.eol" in wanted:
        check_package_eol(audit)
    if not wanted or "release.version" in wanted:
        check_version_tagged(audit)
    if not wanted or "repo.clean" in wanted:
        check_commit_contains(audit)
    print(audit.render(getattr(args, "verbose", False)))
    if getattr(args, "json", None):
        Path(args.json).write_text(audit.to_json(), encoding="utf-8")
        print(f"wrote {args.json}")
    return 1 if any(not r.ok for r in audit.results) else 0


if __name__ == "__main__":
    sys.exit(main())
