"""Store manager for HA Optimizer - handles persistent scan results and soft-delete tracking."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SOFT_DELETE_STORE_KEY, STORE_KEY

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1


class PurgeStore:
    """Manages persistent storage for scan results and soft-delete tracking."""

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._scan_store = Store(hass, STORAGE_VERSION, STORE_KEY)
        self._soft_store = Store(hass, STORAGE_VERSION, SOFT_DELETE_STORE_KEY)
        self._scan_data: dict = {}
        self._soft_data: dict = {}

    async def async_load(self):
        """Load data from persistent storage."""
        self._scan_data = await self._scan_store.async_load() or {}
        self._soft_data = await self._soft_store.async_load() or {}
        _LOGGER.debug("Loaded scan data: %d results, soft-delete: %d entries",
                      len(self._scan_data.get("results", [])),
                      len(self._soft_data))

    async def async_save_scan_results(self, data: dict):
        """Save scan results."""
        self._scan_data = data
        await self._scan_store.async_save(data)

    async def async_get_scan_results(self) -> dict:
        """Get last scan results."""
        return self._scan_data

    async def async_add_soft_deleted(self, entity_ids: list[str]):
        """Record entities as soft-deleted with timestamp.

        The scan entry is snapshotted alongside the timestamp. It used to be
        dropped the moment an entity was soft-deleted, which meant a restore
        put nothing back: the entity came back to life in Home Assistant and
        then vanished from this panel until the next scan - which is up to
        `scan_interval_days` away, and reads to the user as "I restored it and
        the tool lost track of it".

        An entity that is not in the stored scan results (already purged once,
        or added to the trash from outside a purge) simply gets no snapshot,
        and restoring it is still a success - there is nothing to put back.
        """
        now_iso = dt_util.utcnow().isoformat()
        index = {
            r.get("entity_id"): r
            for r in (self._scan_data.get("results") or [])
            if isinstance(r, dict)
        }
        for eid in entity_ids:
            entry: dict[str, Any] = {"disabled_at": now_iso}
            snapshot = index.get(eid)
            if snapshot is not None:
                entry["scan_entry"] = snapshot
            self._soft_data[eid] = entry
        await self._soft_store.async_save(self._soft_data)

    async def async_restore_scan_entries(self, entity_ids: list[str]):
        """Put snapshotted scan entries back after a restore.

        Must be called BEFORE `async_remove_soft_deleted`, which is what
        discards the snapshots.
        """
        results = self._scan_data.setdefault("results", [])
        present = {r.get("entity_id") for r in results if isinstance(r, dict)}
        added: list[str] = []
        for eid in entity_ids:
            meta = self._soft_data.get(eid) or {}
            snapshot = meta.get("scan_entry")
            if not isinstance(snapshot, dict) or eid in present:
                continue
            results.append(snapshot)
            present.add(eid)
            added.append(eid)
        if added:
            _LOGGER.debug("Restored %d scan result entries from the trash", len(added))
            await self._scan_store.async_save(self._scan_data)
        return added

    async def async_remove_soft_deleted(self, entity_ids: list[str]):
        """Remove entities from soft-delete tracking (restored or hard-deleted)."""
        for eid in entity_ids:
            self._soft_data.pop(eid, None)
        await self._soft_store.async_save(self._soft_data)

    async def async_get_soft_deleted(self) -> dict[str, dict]:
        """Get all soft-deleted entities."""
        return dict(self._soft_data)

    async def async_get_expired_soft_deleted(self, days: int) -> list[str]:
        """Return entity_ids that have been soft-deleted longer than `days`."""
        now = dt_util.utcnow()
        expired = []
        for eid, meta in self._soft_data.items():
            try:
                disabled_at = datetime.fromisoformat(meta["disabled_at"])
                age = (now - disabled_at).days
                if age >= days:
                    expired.append(eid)
            except (KeyError, ValueError, TypeError):
                pass
        return expired

    async def async_remove_from_scan_results(self, entity_ids: list[str]):
        """Remove specific entity_ids from stored scan results (post-purge cleanup)."""
        if not self._scan_data or "results" not in self._scan_data:
            return
        before = len(self._scan_data["results"])
        self._scan_data["results"] = [
            r for r in self._scan_data["results"]
            if r.get("entity_id") not in entity_ids
        ]
        after = len(self._scan_data["results"])
        if before != after:
            _LOGGER.debug("Removed %d entities from scan results store", before - after)
            await self._scan_store.async_save(self._scan_data)

    async def async_clear_scan_results(self):
        """Clear scan results."""
        self._scan_data = {}
        await self._scan_store.async_save({})
