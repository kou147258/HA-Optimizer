"""HA Optimizer - Smart cleanup tool for Home Assistant."""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ENABLE_SOFT_DELETE,
    CONF_SCAN_INTERVAL_DAYS,
    CONF_SOFT_DELETE_DAYS,
    DEFAULT_ENABLE_SOFT_DELETE,
    DEFAULT_SCAN_INTERVAL_DAYS,
    DEFAULT_SOFT_DELETE_DAYS,
    DOMAIN,
    EVENT_PURGE_COMPLETE,
    EVENT_SCAN_COMPLETE,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL,
    VERSION,
    SERVICE_GET_RESULTS,
    SERVICE_PURGE,
    SERVICE_RESTORE,
    SERVICE_RESTORE_ALL,
    SERVICE_EMPTY_TRASH,
    SERVICE_SCAN,
    SERVICE_ANALYZE_FINGERPRINT,
    SERVICE_COLLECT_BASELINE,
    SERVICE_ANALYZE_AUTOMATION_RUNS,
    SOFT_DELETE_STORE_KEY,
    STORE_KEY,
)
from .purge_engine import PurgeEngine
from .scanner import DataScanner, RecorderAnalyzer, DashboardAnalyzer, StateStormDetector, AutomationDeadCodeTracer, IntegrationHealthAnalyzer
from .store import PurgeStore
from .fingerprint import FingerprintAnalyzer, FingerprintStore

SERVICE_ANALYZE_RECORDER = "analyze_recorder"
SERVICE_ANALYZE_DASHBOARD = "analyze_dashboard"
SERVICE_ANALYZE_STORMS = "analyze_storms"
SERVICE_ANALYZE_DEAD_CODE = "analyze_dead_code"
SERVICE_ANALYZE_HEALTH = "analyze_health"
SERVICE_ANALYZE_ADDONS = "analyze_addons"

_LOGGER = logging.getLogger(__name__)

PLATFORMS = []


# ── log throttling ─────────────────────────────────────────────────────────
# The panel polls analyze_addons every 10 seconds on every tab, so anything
# that fails there fails 360 times an hour. A warning per failure turned an
# unsupported Supervisor endpoint into a wall of identical log lines, which
# buries the messages that matter.
_WARNED_ONCE: set[str] = set()
_LAST_WARNED: dict[str, float] = {}
_WARN_THROTTLE_SECONDS = 300.0


# ══════════════════════════════════════════════════════════════════════════
# ISSUES (homeassistant.helpers.issue_registry)
#
# Every silent failure in this integration so far reached the user as a line
# in a log they had to find, recognise and paste. That is not a surface. Home
# Assistant has a first-class one: `repairs`, which shows a persistent card in
# the UI until someone deals with it.
#
# The two that matter most here are the two that have already cost real data
# or real time:
#   * a restore that reported success and left the entity gone
#   * an unattended job that was told to delete and had to stand down
#
# Both are written as issues so they cannot be missed again, and both are
# deleted as soon as the condition clears, so the card is never a lie about
# the current state.
# ══════════════════════════════════════════════════════════════════════════


def _async_raise_issue(
    hass: HomeAssistant,
    issue_id: str,
    *,
    severity: str = "warning",
    entities: list[str] | None = None,
    description: str = "",
) -> None:
    """Show a persistent issue in the Home Assistant UI.

    Best effort by design: an integration must not fail because the repairs
    platform is unavailable, and a missing card is much better than an
    exception during an unrelated setup path.
    """
    try:
        from homeassistant.helpers import issue_registry as ir
    except ImportError:  # pragma: no cover - very old Home Assistant
        _LOGGER.warning("issue_registry unavailable; %s: %s", issue_id, description)
        return
    placeholders: dict[str, str] = {"description": description}
    if entities:
        shown = ", ".join(entities[:10])
        if len(entities) > 10:
            shown += f", +{len(entities) - 10} more"
        placeholders["entities"] = shown
        placeholders["count"] = str(len(entities))
    try:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR if severity == "error" else ir.IssueSeverity.WARNING,
            translation_key=issue_id,
            translation_placeholders=placeholders,
        )
    except Exception as exc:  # pragma: no cover - never let reporting break the caller
        _LOGGER.debug("Could not raise issue %s: %s", issue_id, exc)


def _async_clear_issue(hass: HomeAssistant, issue_id: str) -> None:
    """Drop an issue the moment its condition is gone."""
    try:
        from homeassistant.helpers import issue_registry as ir
    except ImportError:  # pragma: no cover
        return
    try:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
    except Exception as exc:  # pragma: no cover
        _LOGGER.debug("Could not clear issue %s: %s", issue_id, exc)


async def _verified_restore(engine, hass: HomeAssistant, entity_id: str,
                             store=None) -> dict:
    """Restore an entity and make sure the answer means something.

    `async_restore_entity` answers `success, re_enabled=False` for "there was
    nothing to restore", which is true both when the entity is present and
    already enabled AND when it has quietly disappeared from the registry - a
    hard delete removes the automation's config entry, and the entity can be
    gone by the time anyone tries to restore it. Trusting that answer made
    `restore_all` drop the trash record for an entity that no longer existed,
    so the one trace of what had happened was erased at the exact moment it
    mattered. Found the hard way, on a real instance, after losing two test
    automations to the very delete path this project is about.

    It also answers `success, re_enabled=True` after only editing the registry
    row, which is the failure this function cannot catch on its own: a live
    instance returned exactly that for an automation that then stayed absent
    from the state machine until `automation.reload` was issued by hand. So a
    restore that claims to have re-enabled something is confirmed against the
    state machine before the trash record is dropped.

    `store` is the trash, and passing it changes what a failure means. Since HA
    2026.8 the user can rename an entity_id, so a trash record may name an id
    that no longer exists while the entity itself is fine under a new one. Left
    alone that is reported as "no longer in the registry - it was deleted, not
    disabled", which is both wrong and unfalsifiable from the panel. So the
    record is first resolved through the registry identity captured when the
    entity was disabled: renamed means restore the new id, gone means say
    precisely what is known instead of guessing.
    """
    resolved: dict = {"entity_id": entity_id, "status": "ok", "renamed_from": None,
                      "identity_known": False}
    if store is not None:
        resolved = await store.async_resolve_soft_deleted(entity_id)
        target = resolved["entity_id"]
        if resolved["status"] == "renamed":
            _LOGGER.info(
                "%s was renamed to %s while it sat in the trash; restoring the "
                "entity under its current id", entity_id, target,
            )
            entity_id = target
        elif resolved["status"] == "gone":
            error = (
                "renamed since it was disabled, and the registry identity stored "
                "with it no longer matches any entity - find it in the entity "
                "registry and restore it from there"
                if resolved.get("identity_known")
                else "no longer in the registry - it was deleted, not disabled"
            )
            _LOGGER.warning("Cannot restore %s: %s", entity_id, error)
            return {
                "success": False,
                "re_enabled": False,
                "entity_id": entity_id,
                "error": error,
                "orphaned": True,
            }

    result = await engine.async_restore_entity(entity_id)
    if result.get("success"):
        from homeassistant.helpers import entity_registry as er

        in_registry = er.async_get(hass).async_get(entity_id) is not None
        # A reload was issued, so give the entity a moment to appear. If none
        # was, do not sit here for seconds per entity on a batch restore -
        # one look is enough, because a successful restore of an entity that
        # had no owner to reload is already in the state machine.
        reloaded = bool(result.get("reloaded"))
        if not await _entity_is_back(hass, entity_id, timeout=3.0 if reloaded else 0.0):
            # Either branch can be wrong here, not just the "re-enabled" one.
            # Checking only when re_enabled was true left a hole a user walked
            # straight into: the second press on Restore took the
            # "already enabled, nothing to do" path, which never looked at the
            # state machine, reported success, and dropped the trash record -
            # so the entity looked deleted for good. A confirmed success must
            # mean the entity is there, whichever branch produced it.
            if not in_registry:
                error = "no longer in the registry - it was deleted, not disabled"
            else:
                error = (
                    "enabled in the registry but the entity did not come back - "
                    "reload it from the entity registry screen, or restart Home "
                    "Assistant"
                )
            _async_raise_issue(
                hass,
                "restore_did_not_complete",
                severity="error",
                entities=[entity_id],
                description=(
                    "The restore of " + entity_id + " reported success but the "
                    "entity is not in the state machine. It stays in the trash - "
                    "nothing was deleted - but it will not come back on its own."
                ),
            )
            _LOGGER.warning(
                "Restore of %s reported success but the entity is not usable; "
                "keeping it in the trash (%s)", entity_id, error,
            )
            return {
                "success": False,
                "re_enabled": False,
                "entity_id": entity_id,
                "error": error,
            }
    result.setdefault("entity_id", entity_id)
    # The trash is keyed by the id the entity had when it was disabled. Hand
    # that back separately so the caller removes the right record, and say so
    # when the entity has moved.
    result["trash_key"] = resolved.get("renamed_from") or entity_id
    if resolved.get("status") == "renamed":
        result["renamed_from"] = resolved["renamed_from"]
    _async_clear_issue(hass, "restore_did_not_complete")
    return result


async def _entity_is_back(hass: HomeAssistant, entity_id: str, timeout: float = 3.0) -> bool:
    """Is the entity actually usable, waiting up to `timeout` for it to appear.

    `config_entries.async_reload` returns once setup has finished, but the state
    machine entry is not guaranteed to be visible on the very next line on a
    loaded instance. Failing a restore that actually worked would put a live
    entity back in the trash, which is the safe direction but still wrong - so
    this gives it a moment when a reload was actually issued, and does not wait
    at all when one was not.
    """
    deadline = time.monotonic() + timeout
    while True:
        if hass.states.get(entity_id) is not None:
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.1)


def _missing_endpoint(path: str) -> None:
    """Announce an endpoint this Supervisor does not have, exactly once."""
    if path in _WARNED_ONCE:
        return
    _WARNED_ONCE.add(path)
    _LOGGER.info(
        "Supervisor has no %s endpoint - resource gauges will stay empty. "
        "This is normal on installs that do not expose host stats.",
        path,
    )


def _log_throttled(key: str, log, msg: str, *args) -> None:
    """Log at most once per key per _WARN_THROTTLE_SECONDS."""
    now = time.monotonic()
    last = _LAST_WARNED.get(key)
    if last is not None and now - last < _WARN_THROTTLE_SECONDS:
        return
    _LAST_WARNED[key] = now
    log(msg, *args)


class _Cooperative:
    """Yield to the event loop on a TIME budget rather than a fixed item count.

    A fixed count is the wrong unit for this work. Removing a plain entity is
    an in-memory dictionary update in the entity registry - microseconds.
    Removing an automation or a script also tears down its config entry, which
    is not. Twenty of the first never needed a yield; twenty of the second
    means the loop was held for a noticeable fraction of a second. What matters
    is how long the loop was held, so that is what gets measured.

    `asyncio.sleep(0)` only hands control to tasks that are already ready; it
    does not wait for I/O. That is the right tool here, because the work being
    interleaved is exactly other tasks queued on the loop, but it only helps if
    it actually happens - hence the wall-clock budget plus a hard item cap so a
    pathological case still yields even if every single call is slow.
    """

    def __init__(self, budget_seconds: float = 0.05, max_items: int = 200):
        self._budget = budget_seconds
        self._max_items = max_items
        self._last = time.monotonic()
        self._count = 0
        self.yields = 0

    async def tick(self) -> None:
        self._count += 1
        now = time.monotonic()
        if now - self._last < self._budget and self._count < self._max_items:
            return
        self._last = now
        self._count = 0
        self.yields += 1
        await asyncio.sleep(0)


# ================================================================
# PANEL AUTO-COPY
# ================================================================

def _copy_panel_to_www(hass: HomeAssistant) -> bool:
    """
    Copy panel.html from custom_components/ha_optimizer/
    to config/www/ha_optimizer/ so HA can serve it via /local/.

    The decision is made on CONTENT, never on mtime. The previous version
    compared timestamps and skipped whenever the served copy was not older than
    the source, which meant a genuine upgrade could install a new panel and
    leave the old one being served - silently, with nothing in the log. Nothing
    in a HACS install sets mtime to anything meaningful, and the copy itself
    used shutil.copy2, which preserves the source mtime, so the check could
    only ever be right by accident. It has already cost this project one
    "the panel 404s and nothing says why" afternoon.

    Unexpected files in the target directory are reported, never deleted: that
    directory belongs to the user as far as we know, and this function's job
    is to make the served panel match, not to tidy up.

    Returns True if the served panel now matches the shipped one.
    """
    src = Path(__file__).parent / "panel.html"
    www_dir = Path(hass.config.config_dir) / "www" / "ha_optimizer"
    dst = www_dir / "panel.html"

    if not src.exists():
        _LOGGER.error("panel.html not found in integration directory: %s", src)
        return False

    try:
        www_dir.mkdir(parents=True, exist_ok=True)

        source = src.read_bytes()
        served = dst.read_bytes() if dst.exists() else None

        if served == source:
            for stray in sorted(www_dir.iterdir()):
                if stray.name != dst.name and stray.is_file():
                    _LOGGER.debug(
                        "Unexpected file in www/ha_optimizer (left alone): %s", stray.name
                    )
            return True

        # Write beside the target and rename over it. A half-written panel.html
        # is a 404 inside the iframe, and copy-then-truncate is exactly that.
        tmp = dst.parent / (dst.name + ".new")
        tmp.write_bytes(source)
        tmp.replace(dst)

        if served is None:
            _LOGGER.info("Installed panel.html → %s", dst)
        else:
            # Said as a warning because the stale copy is worth knowing about:
            # the panel on screen was not the code that was installed. But it
            # says the replacement SUCCEEDED and what to do, because this fires
            # on every upgrade and a message that only reports a problem trains
            # people to dismiss the banner.
            _LOGGER.warning(
                "Panel out of sync after an update: the copy under www/ was from a "
                "previous version (%d → %d bytes) and has been replaced with the "
                "installed one. Nothing is wrong now - reload the panel page to "
                "see the current version.",
                len(served),
                len(source),
            )
        return True

    except OSError as err:
        _LOGGER.error("Failed to copy panel.html to www: %s", err)
        return False


# ================================================================
# SETUP
# ================================================================

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up HA Optimizer from configuration.yaml (not needed - config flow only)."""
    return True


def _entry_options(entry: ConfigEntry) -> dict:
    """Return effective config — options take priority over data."""
    return dict(entry.options) if entry.options else dict(entry.data)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HA Optimizer from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # ── Copy panel.html to www/ so it's served at /local/ha_optimizer/panel.html
    panel_ok = await hass.async_add_executor_job(_copy_panel_to_www, hass)
    if not panel_ok:
        _LOGGER.warning(
            "panel.html could not be copied to www/ha_optimizer/. "
            "The sidebar panel may not load. "
            "Manually copy panel.html to config/www/ha_optimizer/panel.html as a workaround."
        )

    store = PurgeStore(hass)
    await store.async_load()

    fp_store = FingerprintStore(hass)
    await fp_store.async_load()
    fp_analyzer = FingerprintAnalyzer(hass, fp_store)

    hass.data[DOMAIN][entry.entry_id] = {
        "store": store,
        "scanner": None,
        "engine": PurgeEngine(hass),
        "unsub_interval": None,
        "fp_store": fp_store,
        "fp_analyzer": fp_analyzer,
        "unsub_fp_daily": None,
    }

    # Register the custom panel
    try:
        from homeassistant.components import frontend
        frontend.async_register_built_in_panel(
            hass,
            component_name="iframe",
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            frontend_url_path=PANEL_URL,
            config={
                # The version in the query is not decoration. Home Assistant
                # serves /local/ with `cache-control: public, max-age=2678400`
                # - 31 days - so after an upgrade the browser keeps serving the
                # PREVIOUS panel for a month, with nothing on screen saying
                # which build is on it. A different URL is a different cache
                # entry, so the upgrade is always fetched. The panel reads this
                # same value to display its version.
                "url": f"/local/ha_optimizer/panel.html?v={VERSION}",
                "require_admin": True,
            },
            require_admin=True,
        )
    except Exception as panel_err:
        _LOGGER.warning("Could not register sidebar panel: %s", panel_err)

    # Register services
    _register_services(hass, entry)

    # Set up auto-scan if configured
    _setup_auto_scan(hass, entry)

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # Schedule soft-delete cleanup check every 6 hours
    async def _soft_delete_check_cb(now):
        await _async_check_soft_delete_expiry(hass, entry)

    entry.async_on_unload(
        async_track_time_interval(hass, _soft_delete_check_cb, timedelta(hours=6))
    )

    # Schedule daily fingerprint baseline collection at 00:05 each day
    _schedule_daily_baseline(hass, entry)

    _LOGGER.info("HA Optimizer setup complete.")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload config entry."""
    data = hass.data[DOMAIN].pop(entry.entry_id, {})
    if unsub := data.get("unsub_interval"):
        unsub()
    if unsub_fp := data.get("unsub_fp_daily"):
        unsub_fp()

    # Remove panel
    try:
        from homeassistant.components import frontend
        frontend.async_remove_panel(hass, PANEL_URL)
    except Exception:
        pass

    # Remove services
    for svc in [SERVICE_SCAN, SERVICE_PURGE, SERVICE_RESTORE, SERVICE_RESTORE_ALL,
                 SERVICE_EMPTY_TRASH, SERVICE_GET_RESULTS, SERVICE_ANALYZE_AUTOMATION_RUNS,
                SERVICE_ANALYZE_RECORDER, SERVICE_ANALYZE_DASHBOARD,
                SERVICE_ANALYZE_STORMS, SERVICE_ANALYZE_DEAD_CODE, SERVICE_ANALYZE_HEALTH,
                SERVICE_ANALYZE_ADDONS,
                SERVICE_ANALYZE_FINGERPRINT, SERVICE_COLLECT_BASELINE]:
        hass.services.async_remove(DOMAIN, svc)

    return True



# ══════════════════════════════════════════════════════════════════════════
# REMOVAL
#
# Unregister cleanly - but deliberately KEEP the stored scan results and the
# trash records.
#
# The obvious thing to do on uninstall is to delete `.storage/ha_optimizer*`,
# and it is exactly the wrong thing. Those files are the safety net: they are
# the only record of which entities were disabled and when, and the only way
# to find out what a bad decision did. A user who removes this integration
# *because* it misbehaved would lose the evidence at the exact moment they need
# it, and reinstalling would silently resurrect a stale trash listing with
# entries pointing at entities that no longer exist.
#
# So the files stay, and their location is logged. Removing them is a manual
# decision by someone who has decided they do not want the record.
# ══════════════════════════════════════════════════════════════════════════


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Unregister the panel and services; keep the records on disk."""
    await async_unload_entry(hass, entry)

    from homeassistant.helpers import storage as _storage

    kept = []
    for key in (STORE_KEY, SOFT_DELETE_STORE_KEY):
        try:
            path = Path(hass.config.config_dir) / ".storage" / key
            if path.exists():
                kept.append(str(path))
        except Exception:  # pragma: no cover - purely informational
            continue
    if kept:
        _LOGGER.warning(
            "HA Optimizer was removed but its records were left in place, because "
            "they are the only record of what was disabled and when: %s. Delete "
            "them by hand if you really want them gone.", kept,
        )


# ================================================================
# SERVICES
# ================================================================

def _register_services(hass: HomeAssistant, entry: ConfigEntry):
    """Register all integration services."""

    async def handle_scan(call: ServiceCall):
        """Handle scan service call."""
        data = hass.data[DOMAIN][entry.entry_id]
        opts = _entry_options(entry)
        scanner = DataScanner(hass, opts)
        _LOGGER.info("HA Optimizer: Scan triggered via service")
        results = await scanner.async_scan()
        await data["store"].async_save_scan_results(results)
        hass.bus.async_fire(EVENT_SCAN_COMPLETE, {
            "statistics": results.get("statistics", {}),
            "candidates": len(results.get("results", [])),
        })
        return results

    async def handle_purge(call: ServiceCall):
        """Handle purge service call."""
        entity_ids = call.data.get("entity_ids", [])
        soft = call.data.get("soft_delete", _entry_options(entry).get(CONF_ENABLE_SOFT_DELETE, DEFAULT_ENABLE_SOFT_DELETE))
        data = hass.data[DOMAIN][entry.entry_id]
        store = data["store"]

        async def _record(entity_id: str) -> None:
            # Persisted one entity at a time, from inside the engine's loop, so
            # a stop or a disk error between "the registry says disabled" and
            # "the trash knows about it" costs at most that one entity instead of
            # the whole batch. Every path that leaves an entity disabled goes
            # through this, including a hard delete that could only disable.
            await store.async_add_soft_deleted([entity_id])

        result = await data["engine"].async_purge_entities(
            entity_ids, soft_delete=soft, on_left_disabled=_record,
        )

        # No batch write of the soft-deleted ids here any more. It used to be
        # the only record, and it stayed after the per-entity write was added -
        # where it cost one more save per purge and rewrote `disabled_at` on
        # every record to the batch time, resetting the expiry clock the
        # per-entity write had just set correctly.

        if result.get("untracked"):
            # The one state with no way back: disabled in the registry, no
            # trash record. Nothing in this integration can restore it, so the
            # user is told in the response rather than left to find out.
            _LOGGER.error(
                "%d entity/entities are disabled with no trash record and cannot be "
                "restored from here: %s", len(result["untracked"]), result["untracked"],
            )

        if result.get("disabled_only"):
            _LOGGER.warning(
                "Hard delete did not remove %d entity/entities, only disabled them; "
                "they stay in the trash: %s",
                len(result["disabled_only"]), result["disabled_only"],
            )

        # Remove from stored scan results only the entities that really went away
        processed = result.get("success", [])
        if soft:
            processed = processed + result.get("soft_deleted", [])
        if processed:
            await data["store"].async_remove_from_scan_results(processed)

        hass.bus.async_fire(EVENT_PURGE_COMPLETE, result)
        _LOGGER.info("Purge complete: %s", result)
        return result

    async def handle_restore(call: ServiceCall):
        """Handle restore service call."""
        entity_id = call.data.get("entity_id")
        data = hass.data[DOMAIN][entry.entry_id]
        result = await _verified_restore(data["engine"], hass, entity_id,
                                         store=data["store"])
        if result.get("success"):
            # The trash is keyed by the id the entity had when it was disabled,
            # and the scan row has to come back under the id it has now.
            trash_key = result.get("trash_key") or entity_id
            rename_map = {}
            if result.get("renamed_from"):
                rename_map[trash_key] = result["entity_id"]
            # Put the entity back in the scan list BEFORE dropping the trash
            # record - the snapshot it needs lives in that record. Without
            # this the entity came back to HA but stayed invisible here.
            await data["store"].async_restore_scan_entries([trash_key], rename_map)
            await data["store"].async_remove_soft_deleted([trash_key])
        return {
            "success": result.get("success", False),
            "re_enabled": result.get("re_enabled", False),
            "entity_id": result.get("entity_id", entity_id),
            "renamed_from": result.get("renamed_from"),
            "error": result.get("error"),
        }

    async def handle_restore_all(call: ServiceCall):
        """Restore every entity in the trash.

        Nothing is destroyed here, so this is deliberately not a dangerous
        operation - it is the panic button for "I purged the wrong batch".
        Entities that cannot be re-enabled stay in the trash: they are still
        disabled, and a disabled entity nobody tracks is a ghost.
        """
        data = hass.data[DOMAIN][entry.entry_id]
        trash = await data["store"].async_get_soft_deleted()
        restored: list[str] = []
        failed: dict[str, str] = {}
        renames: dict[str, str] = {}
        budget = _Cooperative()

        for eid in list(trash):
            result = await _verified_restore(data["engine"], hass, eid,
                                             store=data["store"])
            if result.get("success"):
                restored.append(eid)
                if result.get("renamed_from"):
                    renames[eid] = result["entity_id"]
            else:
                failed[eid] = result.get("error") or "restore failed"
            await budget.tick()

        if restored:
            await data["store"].async_restore_scan_entries(restored, renames)
            await data["store"].async_remove_soft_deleted(restored)

        _LOGGER.info(
            "Restore-all: %d restored, %d still in the trash", len(restored), len(failed)
        )
        return {
            "success": not failed,
            "restored": restored,
            "failed": failed,
            "total": len(trash),
        }

    async def handle_empty_trash(call: ServiceCall):
        """Permanently remove every entity in the trash. Irreversible.

        Two things make this different from a single purge, and both were
        learned the hard way on the delete path:

        * it yields on a wall-clock budget, because the work is not uniform -
          a plain entity is an in-memory registry update while an automation
          also tears down a config entry;
        * anything the engine could not actually remove stays in the trash. A
          hard delete that merely disabled the entity has not freed anything,
          and dropping it from the records would leave it disabled and
          untracked - nothing would ever restore or finish it.

        It also separates the two kinds of "not removed". A YAML-defined
        automation and a safety device class can NEVER be removed by this
        tool, so telling the user only that they "stayed in the trash" invites
        them to try again forever. Those are reported as permanent.
        """
        data = hass.data[DOMAIN][entry.entry_id]
        trash = await data["store"].async_get_soft_deleted()
        ids = list(trash)
        if not ids:
            return {"success": True, "removed": [], "kept": {}, "kept_permanent": [],
                    "total": 0}

        removed: list[str] = []
        kept: dict[str, str] = {}
        permanent: list[str] = []
        budget = _Cooperative()

        for eid in ids:
            # A record whose entity was renamed still refers to a real entity,
            # just not under the id we stored. Handing the old id to the engine
            # would find nothing, report nothing removed, and leave a live
            # entity in the trash "kept" for ever, with no explanation.
            resolved = await data["store"].async_resolve_soft_deleted(eid)
            target = resolved["entity_id"]
            result = await data["engine"].async_hard_delete_soft_deleted([target])
            if resolved["status"] == "renamed":
                # Report under the id the user sees in the trash listing.
                def _back(key: str) -> list:
                    return [eid if i == target else i for i in result.get(key, [])]

                result = {
                    **result,
                    "success": _back("success"),
                    "soft_deleted": _back("soft_deleted"),
                    "disabled_only": _back("disabled_only"),
                    "renamed": {eid: target},
                }
            if eid in set(result.get("success", [])) | set(result.get("soft_deleted", [])):
                removed.append(eid)
            elif eid in set(result.get("disabled_only", [])):
                kept[eid] = "delete failed, entity only disabled - still tracked"
            elif any(y.get("entity_id") == eid for y in result.get("yaml_manual", [])):
                kept[eid] = "defined in YAML - this tool cannot delete it, remove it by hand"
                permanent.append(eid)
            elif any(s.get("entity_id") == eid for s in result.get("skipped_high_risk", [])):
                kept[eid] = "safety device class - never removed automatically"
                permanent.append(eid)
            else:
                reason = next(
                    (f.get("error") for f in result.get("failed", []) if f.get("entity_id") == eid),
                    None,
                )
                kept[eid] = reason or "not removed"
            await budget.tick()

        if removed:
            await data["store"].async_remove_soft_deleted(removed)
            await data["store"].async_remove_from_scan_results(removed)

        if kept:
            _LOGGER.warning(
                "Empty-trash left %d entr(ies) in the trash: %d permanently "
                "undeletable by this tool, %d may succeed on a retry. Details: %s",
                len(kept), len(permanent), len(kept) - len(permanent), kept,
            )
        else:
            _LOGGER.warning("Empty-trash permanently removed %d entity/entities", len(removed))

        hass.bus.async_fire(EVENT_PURGE_COMPLETE, {
            "total": len(ids),
            "removed": removed,
            "kept": list(kept),
        })
        return {
            "success": not kept,
            "removed": removed,
            "kept": kept,
            "kept_permanent": permanent,
            "total": len(ids),
        }

    async def handle_get_results(call: ServiceCall):
        """Return last scan results plus soft-deleted tracking data.

        Each trash entry also carries when it will be auto-purged, so the
        panel can show the countdown instead of asking the user to remember
        what `soft_delete_days` is set to.

        `days_left` is rounded UP, not truncated. The difference is one whole
        day on every entry: an entity that went in an hour ago with
        `soft_delete_days: 30` has 29.96 days left, and truncating showed
        「还有 29 天」 on an entry that had plainly just been created. Ceiling
        says 30, and only reaches 0 once the entry really is at the end.

        It also has to agree with the expiry check itself, which removes an
        entry once `(now - disabled_at).days >= days` - i.e. after the full
        period has actually elapsed.

        Each entry also gets a `status`, resolved against the live registry:
        `ok`, `renamed` (the entity moved to a new entity_id since it was
        disabled, and `current_entity_id` says where), or `gone` (no entity
        matches any more). Without it a renamed record is indistinguishable from
        a deleted one, and the panel's own Restore button says "not found".
        """
        data = hass.data[DOMAIN][entry.entry_id]
        scan = await data["store"].async_get_scan_results()
        soft = await data["store"].async_get_soft_deleted()
        days = _entry_options(entry).get(CONF_SOFT_DELETE_DAYS, DEFAULT_SOFT_DELETE_DAYS)
        now = dt_util.utcnow()
        for eid, meta in list(soft.items()):
            try:
                resolved = await data["store"].async_resolve_soft_deleted(eid)
            except Exception:  # noqa: BLE001 - the listing must still render
                resolved = {"entity_id": eid, "status": "gone",
                            "renamed_from": None, "identity_known": False}
                _LOGGER.warning(
                    "Could not resolve the registry identity of trash entry %s; "
                    "reported as gone", eid, exc_info=True,
                )
            meta["status"] = resolved["status"]
            meta["current_entity_id"] = resolved["entity_id"]
            try:
                disabled_at = datetime.fromisoformat(meta["disabled_at"])
                if disabled_at.tzinfo is None:
                    disabled_at = disabled_at.replace(tzinfo=now.tzinfo)
                expires = disabled_at + timedelta(days=days)
                meta["expires_at"] = expires.isoformat()
                # ceil, not the timedelta's truncated .days - see the docstring
                remaining = (expires - now).total_seconds()
                meta["days_left"] = math.ceil(remaining / 86400) if remaining > 0 else 0
            except (KeyError, ValueError, TypeError):
                meta["expires_at"] = None
                meta["days_left"] = None
        return {
            **scan,
            "soft_deleted": soft,
            "soft_delete_days": days,
        }

    async def handle_analyze_automation_runs(call: ServiceCall):
        """Report every automation's recent runs, failures and statistics.

        Backed by Home Assistant's own trace component rather than by anything
        we instrument ourselves, because a failing automation emits no event -
        there is nothing to subscribe to.
        """
        from .automation_runs import AutomationRunAnalyzer  # noqa: PLC0415

        return await AutomationRunAnalyzer(hass).async_analyze()

    async def handle_analyze_recorder(call: ServiceCall):
        """Analyze recorder DB and return optimization suggestions."""
        analyzer = RecorderAnalyzer(hass)
        return await analyzer.async_analyze()

    async def handle_analyze_dashboard(call: ServiceCall):
        """Analyze Lovelace dashboards for heavy cards and missing entities."""
        analyzer = DashboardAnalyzer(hass)
        return await analyzer.async_analyze()

    async def handle_analyze_storms(call: ServiceCall):
        """Detect entities with abnormally high state change frequency."""
        analyzer = StateStormDetector(hass)
        return await analyzer.async_analyze()

    async def handle_analyze_dead_code(call: ServiceCall):
        """Find automations with broken triggers, actions or conditions."""
        analyzer = AutomationDeadCodeTracer(hass)
        return await analyzer.async_analyze()

    async def handle_analyze_health(call: ServiceCall):
        """Score integration health based on reconnect patterns."""
        analyzer = IntegrationHealthAnalyzer(hass)
        return await analyzer.async_analyze()

    async def handle_analyze_addons(call: ServiceCall):
        """Fetch addon list + realtime host resource usage via Supervisor API."""
        import aiohttp
        import asyncio

        token = os.environ.get("SUPERVISOR_TOKEN", "")
        if not token:
            return {"host": {}, "addons": [], "error": "SUPERVISOR_TOKEN not found — requires HAOS or Supervised"}

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        base = "http://supervisor"
        T_FAST = aiohttp.ClientTimeout(total=8)
        T_SLOW = aiohttp.ClientTimeout(total=15)

        async def _get(session, path, timeout=T_FAST):
            """GET supervisor path → data dict (or {} on failure).

            A missing endpoint is a property of the installation, not an event:
            `/host/stats` does not exist on every Supervisor, and the panel
            polls this every 10 seconds on every tab. Logging it as a warning
            each time filled the log with 20+ identical lines in four minutes,
            so an unsupported endpoint is now announced once at INFO and then
            stays quiet. Genuine failures (connection refused, timeouts) still
            warn, and are also rate-limited so a Supervisor that is down does
            not turn into a warning every ten seconds either.
            """
            try:
                async with session.get(f"{base}{path}", headers=headers, timeout=timeout) as r:
                    _LOGGER.debug("Supervisor GET %s -> HTTP %s", path, r.status)
                    if r.status == 200:
                        body = await r.json()
                        data = body.get("data", {})
                        _LOGGER.debug("Supervisor %s keys: %s", path, list(data.keys()) if isinstance(data, dict) else type(data))
                        return data
                    else:
                        text = await r.text()
                        # 404 means this Supervisor has no such endpoint -
                        # a fact about the installation, announced once.
                        if r.status == 404:
                            _missing_endpoint(path)
                        else:
                            _log_throttled(
                                f"http{r.status}:{path}",
                                _LOGGER.warning,
                                "Supervisor GET %s -> %s: %s", path, r.status, text[:200],
                            )
            except Exception as exc:
                _log_throttled(
                    f"exc:{path}:{type(exc).__name__}",
                    _LOGGER.warning,
                    "Supervisor GET %s failed: %s", path, exc,
                )
            return {}

        def _mb(val):
            """Convert bytes → MB (rounded int). Returns None if val is falsy."""
            try:
                v = int(val)
                return round(v / 1024 / 1024) if v > 0 else None
            except (TypeError, ValueError):
                return None

        def _first(*vals):
            """Return first non-None value from args."""
            for v in vals:
                if v is not None:
                    return v
            return None

        async def _addon_stats(session, slug):
            """Return (cpu_percent, ram_mb) for a running addon."""
            d = await _get(session, f"/addons/{slug}/stats")
            cpu = d.get("cpu_percent")
            # Supervisor returns bytes for memory
            ram_bytes = _first(d.get("memory_usage"), d.get("memory_used"), d.get("memory"))
            ram_mb = round(int(ram_bytes) / 1024 / 1024, 1) if ram_bytes else None
            return cpu, ram_mb

        async with aiohttp.ClientSession() as session:
            # ── Fetch all needed endpoints in parallel ──
            # Known working HAOS endpoints (verified against hassio-supervisor source):
            #   /host/info      → OS meta: hostname, kernel, operating_system, timezone, cpus
            #                     + disk: disk_life_time, disk_total, disk_used, disk_free (bytes)
            #   /host/stats     → realtime: cpu_percent, memory_usage, memory_limit (bytes)
            #   /supervisor/stats → cpu_percent, memory_usage, memory_limit for supervisor process
            #   /core/info      → version, arch, machine, ...
            #   /addons         → list of addons
            host_info_raw, host_stats_raw, core_info_raw, addons_raw = await asyncio.gather(
                _get(session, "/host/info", T_SLOW),
                _get(session, "/host/stats", T_FAST),
                _get(session, "/core/info", T_FAST),
                _get(session, "/addons", T_SLOW),
            )

            # ── CPU ──
            # Priority: /host/stats → /host/info → /proc/stat fallback
            cpu_pct = _first(
                host_stats_raw.get("cpu_percent"),
                host_info_raw.get("cpu_percent"),
            )
            if cpu_pct is None:
                try:
                    import asyncio as _asyncio
                    async def _read_proc_cpu():
                        """Read two /proc/stat snapshots 200ms apart → overall CPU %."""
                        def _parse_stat():
                            with open("/proc/stat") as f:
                                line = f.readline()  # cpu  user nice sys idle ...
                            parts = line.split()
                            vals = [int(x) for x in parts[1:]]
                            idle = vals[3]
                            total = sum(vals)
                            return idle, total
                        i1, t1 = _parse_stat()
                        await _asyncio.sleep(0.25)
                        i2, t2 = _parse_stat()
                        dt = t2 - t1
                        if dt > 0:
                            return round(100.0 * (1 - (i2 - i1) / dt), 1)
                        return None
                    cpu_pct = await _read_proc_cpu()
                except Exception as _exc:
                    _LOGGER.debug("proc/stat CPU fallback failed: %s", _exc)

            # ── RAM ──
            # Priority: /host/stats → /host/info → /proc/meminfo fallback
            ram_used_mb = _first(
                _mb(host_stats_raw.get("memory_usage")),
                _mb(host_stats_raw.get("memory_used")),
                _mb(host_stats_raw.get("ram_used")),
            )
            ram_total_mb = _first(
                _mb(host_stats_raw.get("memory_limit")),
                _mb(host_stats_raw.get("memory_total")),
                _mb(host_stats_raw.get("ram_total")),
                _mb(host_info_raw.get("memory_total")),
            )
            if ram_total_mb is None or ram_used_mb is None:
                try:
                    meminfo = {}
                    with open("/proc/meminfo") as f:
                        for line in f:
                            key, _, val = line.partition(":")
                            meminfo[key.strip()] = int(val.split()[0]) * 1024  # kB → bytes
                    if ram_total_mb is None and "MemTotal" in meminfo:
                        ram_total_mb = _mb(meminfo["MemTotal"])
                    if ram_used_mb is None and "MemTotal" in meminfo and "MemAvailable" in meminfo:
                        ram_used_mb = _mb(meminfo["MemTotal"] - meminfo["MemAvailable"])
                except Exception as _exc:
                    _LOGGER.debug("proc/meminfo RAM fallback failed: %s", _exc)

            # ── Disk ──
            # /host/info returns disk_used, disk_total, disk_free.
            # HAOS supervisor historically returned bytes but newer versions return GB (float).
            # Log raw values so we can diagnose.
            disk_used_b  = host_info_raw.get("disk_used")
            disk_total_b = host_info_raw.get("disk_total")
            disk_free_b  = host_info_raw.get("disk_free")
            _LOGGER.info(
                "HA Optimizer disk raw: used=%r total=%r free=%r (host_info keys=%s)",
                disk_used_b, disk_total_b, disk_free_b,
                list(host_info_raw.keys()),
            )

            def _disk_to_mb(val):
                """Convert disk value → MB. Handles bytes (>1e8) and GB (<1e5)."""
                if val is None:
                    return None
                try:
                    v = float(val)
                    if v <= 0:
                        return None
                    # HAOS newer: values in GB (e.g. 58.3 or 512)
                    if v < 100_000:
                        return round(v * 1024)      # GB → MB
                    # Classic: values in bytes
                    return round(v / 1024 / 1024)   # bytes → MB
                except (TypeError, ValueError):
                    return None

            disk_used_mb  = _disk_to_mb(disk_used_b)
            disk_total_mb = _disk_to_mb(disk_total_b)

            # Derive used from total-free if needed
            if disk_used_mb is None and disk_total_b is not None and disk_free_b is not None:
                try:
                    disk_used_mb = _disk_to_mb(float(disk_total_b) - float(disk_free_b))
                except (TypeError, ValueError):
                    pass

            # Fallback: statvfs on the data partition (HAOS stores data at /mnt/data)
            if disk_total_mb is None or disk_used_mb is None:
                import os as _os
                for _mount in ("/mnt/data", "/homeassistant", "/data", "/"):
                    try:
                        _sv = _os.statvfs(_mount)
                        _total = _sv.f_frsize * _sv.f_blocks
                        _free  = _sv.f_frsize * _sv.f_bavail
                        if _total > 0:
                            if disk_total_mb is None:
                                disk_total_mb = _mb(_total)
                            if disk_used_mb is None:
                                disk_used_mb = _mb(_total - _free)
                            _LOGGER.info(
                                "HA Optimizer disk statvfs(%s): total=%sMB used=%sMB",
                                _mount, disk_total_mb, disk_used_mb,
                            )
                            break
                    except Exception as _exc:
                        _LOGGER.debug("statvfs(%s) failed: %s", _mount, _exc)

            _LOGGER.info(
                "HA Optimizer Addons: cpu=%.1f%% ram=%s/%s MB disk=%s/%s MB",
                cpu_pct or 0, ram_used_mb, ram_total_mb, disk_used_mb, disk_total_mb,
            )

            host_info = {
                "cpu_percent":    round(float(cpu_pct), 1) if cpu_pct is not None else None,
                "cpus":           host_info_raw.get("cpus"),
                "memory_used_mb":  ram_used_mb,
                "memory_total_mb": ram_total_mb,
                "disk_used_mb":    disk_used_mb,
                "disk_total_mb":   disk_total_mb,
                "operating_system": host_info_raw.get("operating_system"),
                "kernel":    host_info_raw.get("kernel"),
                "hostname":  host_info_raw.get("hostname"),
                "timezone":  host_info_raw.get("timezone"),
                "ha_version": core_info_raw.get("version"),
                # Debug: expose raw keys so frontend can show what was received
                "_debug": {
                    "host_info_keys":  list(host_info_raw.keys()),
                    "host_stats_keys": list(host_stats_raw.keys()),
                    "cpu_source":  "supervisor" if host_stats_raw.get("cpu_percent") is not None else "proc_stat",
                    "ram_source":  "supervisor" if host_stats_raw.get("memory_usage") is not None else "proc_meminfo",
                    "disk_source": "supervisor" if disk_used_b is not None else "statvfs",
                },
            }

            # ── Addon list + per-addon stats (parallel for running addons) ──
            items = (addons_raw or {}).get("addons") or []
            running_slugs = [a.get("slug") for a in items if a.get("state") == "started" and a.get("slug")]

            stats_results = await asyncio.gather(
                *[_addon_stats(session, slug) for slug in running_slugs],
                return_exceptions=True,
            )
            stats_map = {}
            for slug, res in zip(running_slugs, stats_results):
                if isinstance(res, tuple):
                    stats_map[slug] = res

            addons = []
            for a in items:
                slug = a.get("slug", "")
                cpu_a, ram_a = stats_map.get(slug, (None, None))
                addons.append({
                    "slug": slug,
                    "name": a.get("name"),
                    "version": a.get("version"),
                    "version_latest": a.get("version_latest"),
                    "state": a.get("state"),
                    "update_available": a.get("update") is True or bool(
                        a.get("version") and a.get("version_latest")
                        and a.get("version") != a.get("version_latest")
                    ),
                    "icon": bool(a.get("icon")),
                    "cpu_percent": cpu_a,
                    "memory_usage_mb": ram_a,
                })

        return {"host": host_info, "addons": addons}

    async def handle_analyze_fingerprint(call: ServiceCall):
        """Run fingerprint anomaly detection — compare today vs self."""
        data = hass.data[DOMAIN][entry.entry_id]
        return await data["fp_analyzer"].async_analyze()

    async def handle_collect_baseline(call: ServiceCall):
        """Manually trigger baseline collection for yesterday (useful on first install)."""
        data = hass.data[DOMAIN][entry.entry_id]
        await data["fp_analyzer"].async_collect_daily_baseline()
        fp_store: FingerprintStore = data["fp_store"]
        return {
            "success": True,
            "baseline_days": fp_store.count_days(),
        }

    # Every read-only service is registered OPTIONAL, never ONLY. ONLY means
    # "the caller must ask for a response", and only the panel ever asks - it
    # adds ?return_response on all three of its call paths. Automations,
    # scripts, blueprints and the Developer Tools action picker have no way to
    # ask, so under ONLY the seven analyze_* services were callable from the
    # panel and from nowhere else. OPTIONAL changes nothing for the panel and
    # makes "run a weekly health check from an automation" possible. The
    # results are persisted by the handler either way; the return value is a
    # convenience, not the delivery mechanism.
    #
    # supports_response is available since HA 2023.7 — import conditionally
    try:
        from homeassistant.core import SupportsResponse
        hass.services.async_register(
            DOMAIN, SERVICE_SCAN, handle_scan,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.OPTIONAL,
        )
    except ImportError:
        hass.services.async_register(
            DOMAIN, SERVICE_SCAN, handle_scan,
            schema=vol.Schema({}),
        )
    # purge and restore return a result the panel genuinely needs: which
    # entities really went, which were only disabled, which are YAML-defined
    # and must be done by hand. Registered with no supports_response they were
    # SupportsResponse.NONE, so Home Assistant discarded the return value and
    # answered with the usual empty "changed states" list. The panel then read
    # an empty object and skipped every outcome toast: pressing "delete
    # permanently" closed the dialog and went silent, which reads as "the
    # delete did not work". OPTIONAL is what makes the result reachable.
    hass.services.async_register(
        DOMAIN, SERVICE_PURGE, handle_purge,
        schema=vol.Schema({
            vol.Required("entity_ids"): [cv.entity_id],
            vol.Optional("soft_delete"): bool,
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESTORE, handle_restore,
        schema=vol.Schema({
            vol.Required("entity_id"): cv.entity_id,
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )
    # Bulk trash operations. restore_all is the undo button for a purge that
    # went wrong; empty_trash is irreversible and the panel gates it behind a
    # typed confirmation rather than a single click.
    hass.services.async_register(
        DOMAIN, SERVICE_RESTORE_ALL, handle_restore_all,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_EMPTY_TRASH, handle_empty_trash,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    try:
        from homeassistant.core import SupportsResponse
        hass.services.async_register(
            DOMAIN, SERVICE_GET_RESULTS, handle_get_results,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.OPTIONAL,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_RECORDER, handle_analyze_recorder,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.OPTIONAL,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_DASHBOARD, handle_analyze_dashboard,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.OPTIONAL,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_STORMS, handle_analyze_storms,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.OPTIONAL,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_DEAD_CODE, handle_analyze_dead_code,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.OPTIONAL,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_HEALTH, handle_analyze_health,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.OPTIONAL,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_AUTOMATION_RUNS, handle_analyze_automation_runs,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.OPTIONAL,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_ADDONS, handle_analyze_addons,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.OPTIONAL,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_FINGERPRINT, handle_analyze_fingerprint,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.OPTIONAL,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_COLLECT_BASELINE, handle_collect_baseline,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.OPTIONAL,
        )
    except ImportError:
        hass.services.async_register(
            DOMAIN, SERVICE_GET_RESULTS, handle_get_results,
            schema=vol.Schema({}),
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_RECORDER, handle_analyze_recorder,
            schema=vol.Schema({}),
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_DASHBOARD, handle_analyze_dashboard,
            schema=vol.Schema({}),
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_STORMS, handle_analyze_storms,
            schema=vol.Schema({}),
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_DEAD_CODE, handle_analyze_dead_code,
            schema=vol.Schema({}),
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_HEALTH, handle_analyze_health,
            schema=vol.Schema({}),
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_AUTOMATION_RUNS, handle_analyze_automation_runs,
            schema=vol.Schema({}),
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_ADDONS, handle_analyze_addons,
            schema=vol.Schema({}),
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_FINGERPRINT, handle_analyze_fingerprint,
            schema=vol.Schema({}),
        )
        hass.services.async_register(
            DOMAIN, SERVICE_COLLECT_BASELINE, handle_collect_baseline,
            schema=vol.Schema({}),
        )


# ================================================================
# HELPERS
# ================================================================

def _schedule_daily_baseline(hass: HomeAssistant, entry: ConfigEntry):
    """Schedule the daily fingerprint baseline collection for 00:05."""
    from homeassistant.helpers.event import async_track_time_change

    async def _collect_cb(now):
        data = hass.data[DOMAIN].get(entry.entry_id)
        if data:
            await data["fp_analyzer"].async_collect_daily_baseline()

    unsub = async_track_time_change(hass, _collect_cb, hour=0, minute=5, second=0)
    hass.data[DOMAIN][entry.entry_id]["unsub_fp_daily"] = unsub
    entry.async_on_unload(unsub)
    _LOGGER.debug("Fingerprint daily baseline scheduled at 00:05")


def _setup_auto_scan(hass: HomeAssistant, entry: ConfigEntry):
    """Set up periodic auto-scan if configured."""
    interval_days = _entry_options(entry).get(CONF_SCAN_INTERVAL_DAYS, DEFAULT_SCAN_INTERVAL_DAYS)
    if interval_days <= 0:
        return

    async def _do_scan(_now):
        await hass.services.async_call(DOMAIN, SERVICE_SCAN, {}, blocking=False)

    unsub = async_track_time_interval(hass, _do_scan, timedelta(days=interval_days))
    hass.data[DOMAIN][entry.entry_id]["unsub_interval"] = unsub
    _LOGGER.debug("Auto-scan scheduled every %d days", interval_days)


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry):
    """Handle options update - reload entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_restore_self_test(hass: HomeAssistant, entry: ConfigEntry,
                                   entity_id: str) -> bool:
    """Prove the safety net still works before anything irreversible runs.

    The only way to know the restore path works is to restore something, so
    this does a real round trip on ONE entity that was already scheduled for
    permanent deletion - the cheapest possible place to spend a mutation,
    because losing it costs nothing that was not about to be lost anyway.

    A failure leaves that entity as it found it, and the caller abandons the
    whole batch. Failing closed is the entire point: an unattended job that
    cannot prove it can give things back must not delete anything.
    """
    data = hass.data[DOMAIN].get(entry.entry_id)
    if not data:
        return False
    engine = data["engine"]

    # The store is passed so a renamed entity can be resolved to its current
    # id. Without it this call could not find the entity at all, the self-test
    # failed, and the job stood down on every tick from then on - fail-closed,
    # but the trash silently stopped expiring.
    result = await _verified_restore(engine, hass, entity_id, store=data["store"])
    came_back = hass.states.get(entity_id) is not None
    if not (result.get("success") and came_back):
        _LOGGER.error(
            "Restore self-test FAILED for %s (success=%s, back=%s, error=%s). "
            "Auto-expiry is standing down; nothing will be deleted.",
            entity_id, result.get("success"), came_back, result.get("error"),
        )
        return False

    # Put it back the way we found it, so the test leaves no trace.
    try:
        await engine.async_purge_entities([entity_id], soft_delete=True)
    except Exception as exc:  # pragma: no cover - best effort
        _LOGGER.warning("Self-test could not re-disable %s: %s", entity_id, exc)
        return False
    _LOGGER.info(
        "Restore self-test passed on %s; auto-expiry may proceed", entity_id,
    )
    return True


async def _async_check_soft_delete_expiry(hass: HomeAssistant, entry: ConfigEntry):
    """Check for soft-deleted entities that have expired and hard-delete them.

    This runs unattended every 6 hours and is irreversible, so it never does it
    quietly: every batch is logged at warning level, raised as a persistent
    notification, and announced on the event bus. If a purge engine refuses to
    remove something, that entity is reported rather than silently dropped
    from the trash bookkeeping.
    """
    data = hass.data[DOMAIN].get(entry.entry_id)
    if not data:
        return
    soft_days = _entry_options(entry).get(CONF_SOFT_DELETE_DAYS, DEFAULT_SOFT_DELETE_DAYS)
    store = data["store"]
    expired_keys = await store.async_get_expired_soft_deleted(soft_days)
    if not expired_keys:
        return

    # `expired` holds the keys the trash was written under. For an entity that
    # has since been renamed that is not its current id, and this job did not
    # resolve it - unlike the manual empty-trash path, which does. So a single
    # renamed record made the self-test below fail with "not found in
    # registry", the job stood down, and nothing ever expired again. Resolve
    # once, here, for both the self-test and the delete, and keep the original
    # keys so the records are still dropped by the key they are stored under -
    # no assumption about how the store matches them.
    targets: dict[str, str] = {}
    for key in expired_keys:
        resolved = await store.async_resolve_soft_deleted(key)
        target = resolved.get("entity_id") if isinstance(resolved, dict) else None
        targets[key] = target or key
    expired = sorted(set(targets.values()))

    if not await _async_restore_self_test(hass, entry, expired[0]):
        _async_raise_issue(
            hass,
            "auto_purge_aborted",
            severity="error",
            entities=expired,
            description=(
                f"{len(expired)} entity/entities have been in the trash for more than "
                f"{soft_days} day(s) and are due for permanent deletion. That job "
                "was NOT run: the restore path failed its self-test, so the tool "
                "cannot prove it would be able to give these back. They are still "
                "in the trash and nothing has been deleted."
            ),
        )
        return
    _async_clear_issue(hass, "auto_purge_aborted")

    _LOGGER.warning(
        "Auto-expiring %d soft-deleted entity/entities older than %d day(s): %s",
        len(expired), soft_days, expired,
    )
    result = await data["engine"].async_hard_delete_soft_deleted(expired)
    removed = result.get("success", []) + result.get("soft_deleted", [])

    # Anything the engine could not actually remove must stay in the trash
    # records, otherwise it is a ghost: still disabled, but no longer tracked,
    # so nothing will ever restore or finish it.
    still_tracked = [e for e in expired if e not in removed]
    if still_tracked:
        _LOGGER.warning(
            "Auto-expiry could not remove %d entity/entities, leaving them in the "
            "trash: %s", len(still_tracked), still_tracked,
        )
    # Drop the records by the key they are stored under, which for a renamed
    # entity is not the id the engine was given.
    await store.async_remove_soft_deleted(
        [k for k, v in targets.items() if v in removed]
    )

    hass.bus.async_fire(EVENT_PURGE_COMPLETE, {
        "type": "auto_hard_delete",
        "result": result,
    })

    if removed or still_tracked:
        try:
            from homeassistant.components import persistent_notification
        except ImportError:  # pragma: no cover - notification is best effort
            return
        lines = [
            f"HA Optimizer permanently removed {len(removed)} entity/entities that had been "
            f"disabled for more than {soft_days} day(s). This cannot be undone.",
            "",
            *removed[:25],
        ]
        if len(removed) > 25:
            lines.append(f"... and {len(removed) - 25} more")
        if still_tracked:
            lines += [
                "",
                f"{len(still_tracked)} could not be removed and are still tracked:",
                *still_tracked[:10],
            ]
        persistent_notification.async_create(
            hass,
            "HA Optimizer: automatic trash expiry",
            "\n".join(lines),
            notification_id="ha_optimizer_auto_purge",
        )
