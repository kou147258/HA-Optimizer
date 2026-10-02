"""Regression tests for the two defects found while auditing against HA 2026.8.

Both run WITHOUT Home Assistant: `__init__.py` is loaded with stub
`homeassistant.*` modules injected into sys.modules, the same way
`test_purge_safety.py` does it, so the panel-sync helper and the service
registrations can be exercised in isolation.

Run:  python3 tools/test_panel_sync.py

What is locked in here:

  1. `_copy_panel_to_www` decides on CONTENT. The old version compared mtimes
     and skipped whenever the served copy was not older than the source, so an
     upgrade could install a new panel and keep serving the old one - silently.
     The mtime-only case (identical bytes, destination stamped later) is the
     exact scenario that shipped broken, so it is the one that matters most.

  2. No service is registered with `SupportsResponse.ONLY`. ONLY is enforced by
     Home Assistant, and only the panel can satisfy it, so it quietly made
     seven services unreachable from automations, scripts and the Developer
     Tools action picker.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import re
import sys
import tempfile
import time
import types
from pathlib import Path

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "ha_optimizer"
sys.path.insert(0, str(COMPONENT.parent))

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ── stub the parts of Home Assistant __init__.py touches ───────────────────
def _stub() -> None:
    ha = types.ModuleType("homeassistant")
    ha.__path__ = []
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.ServiceCall = object
    # ONLY is kept in the stub precisely so the guard can find it: if the code
    # ever registers ONLY again, the test sees the same value HA would.
    core.SupportsResponse = types.SimpleNamespace(OPTIONAL="optional", ONLY="only", NONE="none")
    core.callback = lambda f: f
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    cv = types.ModuleType("homeassistant.helpers.config_validation")
    cv.entity_id = str
    er = types.ModuleType("homeassistant.helpers.entity_registry")
    er.RegistryEntryDisabler = types.SimpleNamespace(USER="user", INTEGRATION="integration",
                                                    CONFIG_ENTRY="config_entry", SYSTEM="system")
    er.async_get = lambda hass: hass.ent_reg
    dr = types.ModuleType("homeassistant.helpers.device_registry")
    dr.async_get = lambda hass: None
    event = types.ModuleType("homeassistant.helpers.event")
    event.async_track_time_interval = lambda *a, **k: None
    event.async_track_time_change = lambda *a, **k: None
    util = types.ModuleType("homeassistant.util")
    util.__path__ = []
    dt = types.ModuleType("homeassistant.util.dt")
    dt.UTC = None
    storage = types.ModuleType("homeassistant.helpers.storage")
    storage.Store = object
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    recorder = types.ModuleType("homeassistant.components.recorder")
    recorder.get_instance = lambda hass: None
    frontend = types.ModuleType("homeassistant.components.frontend")
    frontend.async_register_built_in_panel = lambda *a, **k: None
    frontend.async_remove_panel = lambda *a, **k: None
    ingress = types.ModuleType("homeassistant.components.ingress")
    ingress.async_create_ingress_url = lambda *a, **k: "/api/ingress/x"
    pn = types.ModuleType("homeassistant.components.persistent_notification")
    pn.async_create = lambda *a, **k: None

    for name, mod in [
        ("homeassistant", ha), ("homeassistant.core", core),
        ("homeassistant.helpers", helpers),
        ("homeassistant.helpers.config_validation", cv),
        ("homeassistant.helpers.entity_registry", er),
        ("homeassistant.helpers.device_registry", dr),
        ("homeassistant.helpers.event", event),
        ("homeassistant.util", util), ("homeassistant.util.dt", dt),
        ("homeassistant.helpers.storage", storage),
        ("homeassistant.config_entries", config_entries),
        ("homeassistant.components", components),
        ("homeassistant.components.recorder", recorder),
        ("homeassistant.components.frontend", frontend),
        ("homeassistant.components.ingress", ingress),
        ("homeassistant.components.persistent_notification", pn),
    ]:
        sys.modules[name] = mod
    helpers.config_validation = cv
    helpers.entity_registry = er
    helpers.device_registry = dr
    helpers.event = event
    helpers.storage = storage
    util.dt = dt
    components.recorder = recorder
    components.frontend = frontend
    components.ingress = ingress
    components.persistent_notification = pn


_stub()

import logging  # noqa: E402

for _name in ("custom_components.ha_optimizer", "ha_optimizer"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)

# __init__.py uses relative imports, so it has to be loaded AS the package -
# `import ha_optimizer` against a pre-seeded sys.modules entry would hand back
# the stub and never execute a line of the real module.
_spec = importlib.util.spec_from_file_location(
    "ha_optimizer", COMPONENT / "__init__.py", submodule_search_locations=[str(COMPONENT)]
)
init = importlib.util.module_from_spec(_spec)
sys.modules["ha_optimizer"] = init
_spec.loader.exec_module(init)


class _FakeConfig:
    def __init__(self, config_dir: str) -> None:
        self.config_dir = config_dir


class _FakeHass:
    def __init__(self, config_dir: str) -> None:
        self.config = _FakeConfig(config_dir)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run_copy(config_dir: str):
    return init._copy_panel_to_www(_FakeHass(config_dir))


# ═══ 1. the panel served from www must match the one in the package ═════════
print("\n_copy_panel_to_www")
src_real = COMPONENT / "panel.html"
src_bytes = src_real.read_bytes() if src_real.exists() else b"<html>shipped</html>"

with tempfile.TemporaryDirectory() as td:
    # a) nothing there yet -> installed
    ok = _run_copy(td)
    dst = Path(td) / "www" / "ha_optimizer" / "panel.html"
    check("installs the panel when www/ is empty", ok and dst.exists())
    check("the installed bytes are the shipped bytes",
          dst.exists() and dst.read_bytes() == src_bytes)

    # b) identical content, destination stamped NEWER - the exact case the
    #    mtime comparison used to get wrong in the other direction
    future = time.time() + 86400
    os.utime(dst, (future, future))
    ok = _run_copy(td)
    check("a newer mtime on an identical copy is not a problem",
          ok and dst.read_bytes() == src_bytes)

    # c) destination genuinely stale, and stamped newer - the case that
    #    shipped: the upgrade lands but the old panel keeps being served
    _write(dst, "<html>the previous release</html>")
    os.utime(dst, (future, future))
    ok = _run_copy(td)
    check("replaces a stale panel even when the stale one is newer",
          ok and dst.read_bytes() == src_bytes,
          f"still serving: {dst.read_bytes()[:60]!r}")

    # d) destination older and stale - the case the mtime check did handle
    _write(dst, "<html>the previous release</html>")
    os.utime(dst, (time.time() - 86400, time.time() - 86400))
    _run_copy(td)
    check("replaces a stale panel that is older", dst.read_bytes() == src_bytes)

    # e) an unrelated file in the target directory is left alone, not deleted
    stranger = dst.parent / "notes.txt"
    _write(stranger, "user file")
    _run_copy(td)
    check("does not delete unrelated files from www/ha_optimizer", stranger.exists())

    # f) no half-written file is left behind next to the target
    leftovers = [p.name for p in dst.parent.iterdir() if p.name != dst.name]
    check("leaves no .new scratch file behind", leftovers == ["notes.txt"],
          f"unexpected: {leftovers}")

    # g) a missing source is reported, not silently ignored
    with tempfile.TemporaryDirectory() as empty:
        saved = src_real.read_bytes() if src_real.exists() else None
        try:
            if src_real.exists():
                src_real.unlink()
            ok = _run_copy(empty)
            check("reports failure when panel.html is missing from the package",
                  ok is False)
        finally:
            if saved is not None:
                src_real.write_bytes(saved)


# ═══ 2. no service may be ONLY ═════════════════════════════════════════════
print("\nservice registration")
source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
check("no SupportsResponse.ONLY anywhere in __init__.py",
      "SupportsResponse.ONLY" not in source,
      "found: " + str(source.count("SupportsResponse.ONLY")))
check("the analysis services are still registered at all",
      all(f"DOMAIN, SERVICE_ANALYZE_{n}" in source
          for n in ("RECORDER", "DASHBOARD", "STORMS", "DEAD_CODE", "HEALTH",
                    "ADDONS", "FINGERPRINT")))

registrations = source.count("supports_response=SupportsResponse.OPTIONAL")
# Not a magic number: the two bulk trash services added later also declare
# OPTIONAL, so the count grows with the service surface. What has to hold is
# that every service the panel asks for a response from is registered that
# way - a hardcoded "10" went stale the moment restore_all and empty_trash
# landed.
panel_src = (COMPONENT / "panel.html").read_text(encoding="utf-8")
m = re.search(r"SERVICES_WITH_RESPONSE\s*=\s*new Set\(\[([^\]]*)\]\)", panel_src)
with_response = set(re.findall(r"'([a-z_]+)'", m.group(1))) if m else set()
check("the panel asks for responses", bool(with_response))
check("every service the panel asks a response from is registered OPTIONAL",
      with_response and registrations >= len(with_response),
      f"{registrations} OPTIONAL registrations vs {len(with_response)} in the panel")

# Count alone would not have caught the real defect: `purge` and `restore` were
# registered with NO supports_response, so Home Assistant treated them as NONE
# and threw the return value away. The panel then read an empty object and
# skipped every outcome toast - pressing "delete permanently" closed the
# dialog and said nothing at all, which reads as "the delete did not work".
# Matching by identity is the only thing that catches that.
# Match each registration BLOCK rather than scanning the whole file, and take
# the most capable mode per service rather than the last seen. Both matter:
# a regex over the source consumes text, so the `except ImportError` fallback
# that re-declares a service without a response flag would overwrite the real
# answer, and several services are registered twice that way.
by_const: dict[str, str] = {}
for block in source.split("async_register(")[1:]:
    name = re.search(r"(SERVICE_[A-Z_]+)\s*,\s*handle_\w+", block)
    if not name:
        continue
    key = name.group(1).replace("SERVICE_", "").lower()
    mode = re.search(r"supports_response=SupportsResponse\.(\w+)", block)
    found = mode.group(1).lower() if mode else "none"
    if by_const.get(key) in (None, "none"):
        by_const[key] = found
missing = sorted(s for s in with_response if by_const.get(s) != "optional")
check("every service the panel asks a response from declares OPTIONAL by name",
      not missing, f"not OPTIONAL: {missing} (parsed: {by_const})")
check("purge declares a response mode", by_const.get("purge") == "optional",
      "with NONE the result dict is discarded and the panel goes silent")
check("restore declares a response mode", by_const.get("restore") == "optional",
      "restore reported the same outcome whether it worked or not")
check("no service the panel uses is left on the implicit default",
      not [s for s in with_response if by_const.get(s) == "none"],
      f"implicit NONE: {[s for s in with_response if by_const.get(s) == 'none']}")

# The reason ONLY was wrong has to stay written down, or somebody will
# "tighten" it back.
check("the ONLY/OPTIONAL decision is explained in a comment",
      "ONLY means" in source and "return_response" in source)


# ═══ 3. the panel asks for exactly the services that can answer ═════════════
print("\npanel consistency")
panel = (COMPONENT / "panel.html").read_text(encoding="utf-8")
m = re.search(r"SERVICES_WITH_RESPONSE\s*=\s*new Set\(\[([^\]]*)\]\)", panel)
with_response = set(re.findall(r"'([a-z_]+)'", m.group(1))) if m else set()
# The set grows as services gain a response mode, so it is described by
# membership, never by a count. `len(...) == 10` went stale twice today.
check("the panel asks for responses at all", bool(with_response))
check("no read-only service was dropped from the panel's response set",
      {"scan", "get_results", "analyze_health", "analyze_addons"} <= with_response)
check("the delete path is in the panel's response set",
      {"purge", "restore"} <= with_response,
      "without these the panel reads an empty result and shows nothing")
check("the bulk trash services are in the panel's response set",
      {"restore_all", "empty_trash"} <= with_response)


# ═══ 4. filter <option> labels must be matched by value, not by index ══════
# The labels used to be applied positionally, from a key list written
# separately from the <option> markup. That silently mislabelled the second
# option onwards: filterCat's keys were ordered entity/helper/automation/
# script-to-automation/script-to-script/helper, so picking "自动化" filtered
# `helper`, and filterYaml's two keys were swapped so "YAML" displayed the
# Registry label. Both are invisible in review and obvious the moment you
# click the dropdown.
print("\nfilter option labels")

# There are exactly two dictionaries, so "declared in both" is simply
# "declared twice". Slicing the blocks apart textually is not worth it: the
# dictionaries close mid-line (`addonsNone: '…',}};`), so a `\n  },` anchor
# finds nothing and silently yields an empty string.
i18n_src = panel
shell = panel


def declared_how_often(key: str) -> int:
    return len(re.findall(rf"^\s+{re.escape(key)}\s*:", i18n_src, re.M))

for select_id in ("filterRisk", "filterCat", "filterYaml", "filterState"):
    block = re.search(rf'id="{select_id}"[^>]*>(.*?)</select>', shell, re.S)
    check(f"#{select_id} exists in the markup", block is not None)
    if not block:
        continue
    values = re.findall(r'<option value="([^"]*)"', block.group(1))
    call = re.search(rf"_labelOptions\('{select_id}', \{{(.*?)\}}\)", shell, re.S)
    check(f"#{select_id} is labelled by value, not by index", call is not None)
    if not call:
        continue
    # the value→key map is a JS object literal whose keys are mostly UNQUOTED
    # (`low: 'filterLow'`), and the empty option value is the quoted `''`
    keys = dict(re.findall(r"'?([A-Za-z0-9_]*)'?\s*:\s*'([^']*)'", call.group(1)))
    unlabelled = [v for v in values if v not in keys]
    check(f"#{select_id}: every option value has a key", not unlabelled,
          f"unlabelled: {unlabelled} (parsed keys: {sorted(keys)})")
    stale = [k for k in keys if k not in values]
    check(f"#{select_id}: no key without a matching option", not stale,
          f"orphaned: {stale}")
    for key in keys.values():
        check(f"#{select_id}: key '{key}' is declared in both dictionaries",
              declared_how_often(key) == 2, f"declared {declared_how_often(key)}x")

check("no positional option labelling is left anywhere",
      "options].forEach((opt, i)" not in shell)

# ═══ 4b. every form control is addressable ═════════════════════════════════
# Chrome's Issues panel flagged "A form field element should have an id or name
# attribute" on the per-row checkbox. Not a functional bug, but it is a hint
# that something in the panel is anonymous, and the count in DevTools (4) is
# the number of rendered rows - so it scales with the table, not with the
# markup, and nobody would notice it in review.
print("\nform controls")
import re as _re  # noqa: E402
controls = []
for _tag in ("input", "select", "textarea"):
    controls += list(_re.finditer(rf"<{_tag}\b[^>]*>", shell, _re.I))
anonymous = [m for m in controls
             if not _re.search(r"\sid\s*=", m.group(0))
             and not _re.search(r"\sname\s*=", m.group(0))]
check(f"every form control has an id or a name ({len(controls)} found)",
      not anonymous,
      f"{len(anonymous)} without either: "
      + "; ".join(m.group(0)[:70] for m in anonymous[:3]))
check("the row checkbox is explicitly excluded from autofill",
      'autocomplete="off"' in shell)

# ═══ 4c. nothing on the chrome loops forever ═══════════════════════════════
# The user reported "the screen keeps flickering" when the delete confirmation
# was open. Measured on the instance: the event loop is not blocked (probe
# p50 3ms / max 4ms, identical idle and under load), and the whole scan ->
# select -> modal flow runs in a real browser with zero console errors. What
# was left was two permanent animations in the top strip: a 60s marquee and a
# 2s blink, both `infinite`, neither gated on prefers-reduced-motion, on screen
# for as long as anyone is looking at the panel.
print("\nno permanent motion on the chrome")
infinite = [m.group(0).replace("\n", " ") for m in _re.finditer(r"animation:[^;}]*infinite", shell)]
allowed = [a for a in infinite if "spin" in a]   # the spinner only runs while loading
stray = [a for a in infinite if a not in allowed]
check("no infinite animation on anything that is always visible",
      not stray, f"stray: {stray}")
check("the only infinite animation left is the loading spinner",
      len(allowed) == 1, f"found {len(allowed)}")
check("the backup warning no longer scrolls",
      "tickerScroll" not in shell,
      "a warning that moves is a warning you cannot read")
check("motion is opt-out for anyone who asked for reduced motion",
      "prefers-reduced-motion" in shell and ".live-dot" in shell)

# ═══ 5. no hardcoded upstream locale in printed timestamps ═════════════════
# Two timestamps were formatted with toLocaleString('vi-VN'), upstream's source
# language, so a Chinese or English user was shown upstream's date conventions
# no matter what they picked. print-time formatting must follow the panel.
vn = re.findall(r"toLocale(?:Time|Date)?String\('vi-VN'\)", shell)
check("no timestamp is formatted with a hardcoded vi-VN locale", not vn,
      f"{len(vn)} occurrence(s)")
check("the locale helper exists and is used",
      shell.count("_panelLocale()") >= 3,
      f"{shell.count('_panelLocale()')} reference(s)")

# risk levels are still only the three defined values, and a disabled
# automation is never signalled by changing one
check("risk levels are still only the three defined values",
      all(f"risk-{r}" in shell for r in ("low", "medium", "high")))


print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:", ", ".join(FAILURES))
    sys.exit(1)
print("all panel-sync and service-registration checks passed")
