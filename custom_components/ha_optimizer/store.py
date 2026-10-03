"""Store manager for HA Optimizer - handles persistent scan results and soft-delete tracking."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
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
        # Every mutation of _soft_data and _scan_data goes through this lock.
        # Until it existed there was no mutual exclusion anywhere in the
        # integration, and the scheduled scan REPLACES the scan results while a
        # purge filters them and a restore adds to them. Interleaved, the scan
        # write resurrects entities the user just deleted - which reads as "the
        # delete did not work", the family of bug this project has spent a day
        # on. Holding it here rather than in the service handlers keeps the diff
        # small and, more to the point, holds no matter which caller arrived
        # first - including the scheduled one.
        self._lock = asyncio.Lock()
        # Edits made while a scan was in flight, replayed onto its result. A scan
        # over a large instance takes seconds, and blocking a user action for
        # that long would be a worse trade than merging.
        self._removed_since_scan: set[str] = set()
        self._added_since_scan: dict[str, dict] = {}

    async def async_load(self):
        """Load data from persistent storage."""
        self._scan_data = await self._scan_store.async_load() or {}
        self._soft_data = await self._soft_store.async_load() or {}
        _LOGGER.debug("Loaded scan data: %d results, soft-delete: %d entries",
                      len(self._scan_data.get("results", [])),
                      len(self._soft_data))

    async def async_save_scan_results(self, data: dict):
        async with self._lock:
            return await self._async_save_scan_results_locked(data)

    async def _async_save_scan_results_locked(self, data: dict) -> None:
        """Save scan results.

        Note what this does NOT do: it never touches `_soft_data`. A soft deleted
        entity is disabled, and the scanner skips disabled entities
        (`entry.disabled_by is None`), so a trash snapshot can never be refreshed by
        a later scan. If saving results also pruned snapshots whose entity is absent
        from the new set - which is every trash entry, by definition - then
        "restore" would silently stop returning entities to the list again, and the
        fix from 1.7.0 would quietly undo itself. The snapshot is the only record of
        why an entity was a candidate, so it outlives any number of scans.

        This is also where a scan's result meets whatever a purge or a restore did
        while the scan was running, which is why the wrapper takes the lock and
        hands off here.
        """

        # Replay anything a purge or a restore changed while this scan was
        # running, so a late write cannot undo it.
        if self._removed_since_scan or self._added_since_scan:
            rows = [
                r for r in (data.get("results") or [])
                if isinstance(r, dict) and r.get("entity_id") not in self._removed_since_scan
            ]
            have = {r.get("entity_id") for r in rows}
            for eid, row in self._added_since_scan.items():
                if eid not in have:
                    rows.append(row)
            data = {**data, "results": rows}
            _LOGGER.debug(
                "Replayed %d concurrent removal(s) and %d addition(s) onto the scan",
                len(self._removed_since_scan), len(self._added_since_scan),
            )
        self._removed_since_scan.clear()
        self._added_since_scan.clear()

        """Store a completed scan. Takes the lock, then merges - see the helper."""
        self._scan_data = data
        await self._scan_store.async_save(data)

    async def async_get_scan_results(self) -> dict:
        """Get last scan results."""
        return self._scan_data

    async def async_add_soft_deleted(self, entity_ids: list[str]):

        async with self._lock:
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

            The registry identity is stored too, and that is not belt-and-braces:
            since HA 2026.8 the user can rename an entity_id and choose how its
            parts are laid out. These records are keyed by entity_id, so a rename
            between "disabled" and "restore" orphans the record - the entity comes
            back under its new id, the trash row can never match it again, and
            nothing anywhere reports it. unique_id + platform + domain is the
            triple the registry itself uses to recognise an entity, so it
            survives exactly the change that breaks the key.
            """
            now_iso = dt_util.utcnow().isoformat()
            index = {
                r.get("entity_id"): r
                for r in (self._scan_data.get("results") or [])
                if isinstance(r, dict)
            }
            ent_reg = er.async_get(self.hass) if self.hass is not None else None
            for eid in entity_ids:
                entry: dict[str, Any] = {"disabled_at": now_iso}
                snapshot = index.get(eid)
                if snapshot is not None:
                    entry["scan_entry"] = snapshot
                reg = ent_reg.async_get(eid) if ent_reg is not None else None
                if reg is not None and reg.unique_id:
                    entry["unique_id"] = reg.unique_id
                    entry["platform"] = reg.platform
                    entry["domain"] = reg.domain
                self._soft_data[eid] = entry
            await self._soft_store.async_save(self._soft_data)

    async def async_resolve_soft_deleted(self, entity_id: str) -> dict[str, Any]:
        """Map a trash key onto the entity that exists now.

        Returns {"entity_id", "status", "renamed_from", "identity_known"}, where
        status is:

        * "ok"      - the recorded entity_id still resolves;
        * "renamed" - it does not, but the stored registry identity does, and
                      "entity_id" is where that entity lives now;
        * "gone"    - neither resolves. It was hard-deleted, or renamed in a way
                      that changed its unique_id too, or its integration is gone.

        `identity_known` says whether a registry identity was stored at all, so
        a caller can tell "deleted" from "we have no way of knowing" instead of
        reporting both with the same words. It is False for every record written
        before this existed.

        Renamed and gone are the same "not found in registry" answer to every
        caller that only looks at the id, which is precisely how a rename comes
        to read as a delete.
        """
        ent_reg = er.async_get(self.hass) if self.hass is not None else None
        if ent_reg is None:
            return {"entity_id": entity_id, "status": "gone", "renamed_from": None,
                    "identity_known": False}
        if ent_reg.async_get(entity_id) is not None:
            return {"entity_id": entity_id, "status": "ok", "renamed_from": None,
                    "identity_known": True}
        meta = self._soft_data.get(entity_id) or {}
        uid = meta.get("unique_id")
        platform = meta.get("platform")
        domain = meta.get("domain")
        identity_known = bool(uid and platform and domain)
        if identity_known:
            current = ent_reg.async_get_entity_id(domain, platform, uid)
            if current:
                return {
                    "entity_id": current,
                    "status": "renamed",
                    "renamed_from": entity_id,
                    "identity_known": True,
                }
        return {"entity_id": entity_id, "status": "gone", "renamed_from": None,
                "identity_known": identity_known}
    
    
    async def async_restore_scan_entries(self, entity_ids: list[str],
                                          rename_map: dict[str, str] | None = None):

        async with self._lock:
            """Put snapshotted scan entries back after a restore.
    
            Must be called BEFORE `async_remove_soft_deleted`, which is what
            discards the snapshots.
    
            The snapshot is the analysis, not the state. Replaying it verbatim put
            the entity back in the list still marked `disabled: true` - and for the
            main use of this feature that is guaranteed: the reason a disabled
            automation shows up as a candidate at all is `reason_auto_disabled`,
            so the snapshot says disabled, the restore un-disables it, and the row
            comes back claiming it is still disabled. A restore that worked then
            looks exactly like one that did not, which is what a live instance
            reported. The current state is therefore re-read from the registry
            before the row is written back.
            """
            rename_map = rename_map or {}
            results = self._scan_data.setdefault("results", [])
            present = {r.get("entity_id") for r in results if isinstance(r, dict)}
            added: list[str] = []
            ent_reg = er.async_get(self.hass) if self.hass is not None else None
            for eid in entity_ids:
                meta = self._soft_data.get(eid) or {}
                snapshot = meta.get("scan_entry")
                # The snapshot was taken under the id the entity had when it was
                # disabled. Restoring it verbatim puts a row back for an
                # entity_id that no longer exists, so the entity is invisible in
                # the panel again under a name nothing can find. Write the row
                # under the id the entity has today, and ask the registry about
                # that id.
                target = rename_map.get(eid, eid)
                if not isinstance(snapshot, dict) or target in present:
                    continue
                entry = dict(snapshot)
                entry["entity_id"] = target
                current = ent_reg.async_get(target) if ent_reg is not None else None
                if current is not None:
                    entry["disabled"] = bool(current.disabled)
                    if not current.disabled:
                        # The reason no longer holds. Leaving it would keep the row
                        # flagged as a problem on the strength of something that
                        # was fixed a moment ago.
                        reasons = entry.get("reason")
                        if isinstance(reasons, list) and "reason_auto_disabled" in reasons:
                            reasons = [r for r in reasons if r != "reason_auto_disabled"]
                            entry["reason"] = reasons
                            if not reasons:
                                entry["risk_level"] = "low"
                results.append(entry)
                present.add(target)
                added.append(eid)
                self._added_since_scan[target] = entry
            if added:
                _LOGGER.debug("Restored %d scan result entries from the trash", len(added))
                await self._scan_store.async_save(self._scan_data)
            return added
    
    
    async def async_remove_soft_deleted(self, entity_ids: list[str]):

        async with self._lock:
            """Remove entities from soft-delete tracking (restored or hard-deleted)."""
            for eid in entity_ids:
                self._soft_data.pop(eid, None)
            await self._soft_store.async_save(self._soft_data)
    
    
    async def async_get_soft_deleted(self) -> dict[str, dict]:
        """Get all soft-deleted entities."""
        return dict(self._soft_data)

    async def async_get_expired_soft_deleted(self, days: int) -> list[str]:
        """Return entity_ids that have been soft-deleted longer than `days`.

        `days` of 0 or less means NOTHING expires, and that is what the
        documentation promises: "set soft_delete_days: 0 if you would rather
        empty it yourself". The rule below was a bare `age >= days`, so with 0
        every entry qualified the instant it was written - a documented
        off-switch that mass hard-deletes the whole trash. The options flow
        also refuses 0 (`vol.Range(min=1)`), so the switch was unreachable by
        the documented route and destructive by every other one.
        """
        if days <= 0:
            return []
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

        async with self._lock:
            """Remove specific entity_ids from stored scan results (post-purge cleanup)."""
            if not self._scan_data or "results" not in self._scan_data:
                return
            before = len(self._scan_data["results"])
            self._scan_data["results"] = [
                r for r in self._scan_data["results"]
                if r.get("entity_id") not in entity_ids
            ]
            # A scan already in flight will write its own list; without this it
            # would put these entities straight back into the candidate list,
            # which is exactly what "the delete did not work" looks like.
            self._removed_since_scan.update(entity_ids)
            after = len(self._scan_data["results"])
            if before != after:
                _LOGGER.debug("Removed %d entities from scan results store", before - after)
                await self._scan_store.async_save(self._scan_data)
    
    
    async def async_clear_scan_results(self):

        async with self._lock:
            """Clear scan results."""
            self._scan_data = {}
            await self._scan_store.async_save({})
    
