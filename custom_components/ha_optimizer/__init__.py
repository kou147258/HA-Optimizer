"""HA Optimizer - Smart cleanup tool for Home Assistant."""
from __future__ import annotations

import logging
import os
import shutil
from datetime import timedelta
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
    SERVICE_GET_RESULTS,
    SERVICE_PURGE,
    SERVICE_RESTORE,
    SERVICE_SCAN,
    SERVICE_ANALYZE_FINGERPRINT,
    SERVICE_COLLECT_BASELINE,
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


# ================================================================
# PANEL AUTO-COPY
# ================================================================

def _copy_panel_to_www(hass: HomeAssistant) -> bool:
    """
    Copy panel.html from custom_components/ha_optimizer/
    to config/www/ha_optimizer/ so HA can serve it via /local/.

    Returns True if copy succeeded (or file was already up to date).
    """
    src = Path(__file__).parent / "panel.html"
    www_dir = Path(hass.config.config_dir) / "www" / "ha_optimizer"
    dst = www_dir / "panel.html"

    if not src.exists():
        _LOGGER.error("panel.html not found in integration directory: %s", src)
        return False

    try:
        www_dir.mkdir(parents=True, exist_ok=True)

        # Only copy if source is newer or destination doesn't exist
        if dst.exists():
            src_mtime = src.stat().st_mtime
            dst_mtime = dst.stat().st_mtime
            if src_mtime <= dst_mtime:
                _LOGGER.debug("panel.html is already up to date, skipping copy.")
                return True

        shutil.copy2(src, dst)
        _LOGGER.info("Copied panel.html → %s", dst)
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
                "url": "/local/ha_optimizer/panel.html",
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
    for svc in [SERVICE_SCAN, SERVICE_PURGE, SERVICE_RESTORE, SERVICE_GET_RESULTS,
                SERVICE_ANALYZE_RECORDER, SERVICE_ANALYZE_DASHBOARD,
                SERVICE_ANALYZE_STORMS, SERVICE_ANALYZE_DEAD_CODE, SERVICE_ANALYZE_HEALTH,
                SERVICE_ANALYZE_ADDONS,
                SERVICE_ANALYZE_FINGERPRINT, SERVICE_COLLECT_BASELINE]:
        hass.services.async_remove(DOMAIN, svc)

    return True


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
        soft = call.data.get("soft_delete", entry.options.get(CONF_ENABLE_SOFT_DELETE, DEFAULT_ENABLE_SOFT_DELETE))
        data = hass.data[DOMAIN][entry.entry_id]
        result = await data["engine"].async_purge_entities(entity_ids, soft_delete=soft)

        # Track soft-deleted entities (includes both newly disabled AND already_disabled)
        if soft and result.get("soft_deleted"):
            await data["store"].async_add_soft_deleted(result["soft_deleted"])

        # A hard delete that only managed to disable the entity must still be
        # tracked in the trash, otherwise the user has no way back: it is
        # disabled, untracked, and nothing will ever restore or finish it.
        if not soft and result.get("disabled_only"):
            await data["store"].async_add_soft_deleted(result["disabled_only"])

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
        result = await data["engine"].async_restore_entity(entity_id)
        if result.get("success"):
            await data["store"].async_remove_soft_deleted([entity_id])
        return {
            "success": result.get("success", False),
            "re_enabled": result.get("re_enabled", False),
            "entity_id": entity_id,
            "error": result.get("error"),
        }

    async def handle_get_results(call: ServiceCall):
        """Return last scan results plus soft-deleted tracking data."""
        data = hass.data[DOMAIN][entry.entry_id]
        scan = await data["store"].async_get_scan_results()
        soft = await data["store"].async_get_soft_deleted()
        return {
            **scan,
            "soft_deleted": soft,
        }

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
            """GET supervisor path → data dict (or {} on failure). Logs raw keys for debugging."""
            try:
                async with session.get(f"{base}{path}", headers=headers, timeout=timeout) as r:
                    _LOGGER.debug("Supervisor GET %s → HTTP %s", path, r.status)
                    if r.status == 200:
                        body = await r.json()
                        data = body.get("data", {})
                        _LOGGER.debug("Supervisor %s keys: %s", path, list(data.keys()) if isinstance(data, dict) else type(data))
                        return data
                    else:
                        text = await r.text()
                        _LOGGER.warning("Supervisor GET %s → %s: %s", path, r.status, text[:200])
            except Exception as exc:
                _LOGGER.warning("Supervisor GET %s failed: %s", path, exc)
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
    hass.services.async_register(
        DOMAIN, SERVICE_PURGE, handle_purge,
        schema=vol.Schema({
            vol.Required("entity_ids"): [cv.entity_id],
            vol.Optional("soft_delete"): bool,
        }),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESTORE, handle_restore,
        schema=vol.Schema({
            vol.Required("entity_id"): cv.entity_id,
        }),
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
            supports_response=SupportsResponse.ONLY,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_DASHBOARD, handle_analyze_dashboard,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.ONLY,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_STORMS, handle_analyze_storms,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.ONLY,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_DEAD_CODE, handle_analyze_dead_code,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.ONLY,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_HEALTH, handle_analyze_health,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.ONLY,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_ADDONS, handle_analyze_addons,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.ONLY,
        )
        hass.services.async_register(
            DOMAIN, SERVICE_ANALYZE_FINGERPRINT, handle_analyze_fingerprint,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.ONLY,
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
    """Lên lịch thu thập baseline fingerprint mỗi ngày lúc 00:05."""
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
    interval_days = entry.options.get(CONF_SCAN_INTERVAL_DAYS, DEFAULT_SCAN_INTERVAL_DAYS)
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
    soft_days = entry.options.get(CONF_SOFT_DELETE_DAYS, DEFAULT_SOFT_DELETE_DAYS)
    expired = await data["store"].async_get_expired_soft_deleted(soft_days)
    if not expired:
        return

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
    await data["store"].async_remove_soft_deleted(removed)

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
