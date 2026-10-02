"""Optimizer - Execution layer for HA Optimizer."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SAFETY_DEVICE_CLASSES

_LOGGER = logging.getLogger(__name__)


class PurgeEngine:
    """Handles the actual deletion/disabling of entities."""

    def __init__(self, hass: HomeAssistant):
        self.hass = hass

    async def async_purge_entities(
        self, entity_ids: list[str], soft_delete: bool = True
    ) -> dict[str, Any]:
        """Purge a list of entities. Returns results dict."""
        results = {
            "success": [],
            "failed": [],
            "soft_deleted": [],        # newly disabled by this call
            "already_disabled": [],    # was already disabled before — still added to trash
            "yaml_manual": [],
            "disabled_only": [],       # delete FAILED, only disabled — kept tracked
            "skipped_high_risk": [],   # device class forbids removing it
        }

        ent_reg = er.async_get(self.hass)

        for entity_id in entity_ids:
            try:
                entry = ent_reg.async_get(entity_id)
                domain = entity_id.split(".")[0]

                if not entry:
                    # Not in registry — try domain-specific removal
                    ok = await self._remove_by_domain(entity_id)
                    if ok:
                        results["success"].append(entity_id)
                    else:
                        results["failed"].append({"entity_id": entity_id, "error": "Not found in registry"})
                    continue

                # For automations and scripts, always use domain-specific deletion
                # (entity_registry.async_remove alone does NOT remove the config/storage)
                if domain in ("automation", "script"):
                    if soft_delete:
                        was_already_disabled = entry.disabled
                        if not was_already_disabled:
                            ent_reg.async_update_entity(
                                entity_id,
                                disabled_by=er.RegistryEntryDisabler.USER,
                            )
                            _LOGGER.info("Soft-disabled %s: %s", domain, entity_id)
                        else:
                            _LOGGER.info(
                                "%s was already disabled, adding to trash: %s",
                                domain, entity_id,
                            )
                            results["already_disabled"].append(entity_id)
                        # Either way it goes into soft_deleted (= tracked in trash)
                        results["soft_deleted"].append(entity_id)
                    else:
                        outcome = await self._remove_by_domain(entity_id)
                        if outcome == "removed":
                            results["success"].append(entity_id)
                        elif outcome == "disabled":
                            # It is NOT gone. Reporting this as a success made
                            # a failed hard delete look completed, and the
                            # entity stopped being tracked in the trash.
                            results["disabled_only"].append(entity_id)
                        else:
                            results["yaml_manual"].append({
                                "entity_id": entity_id,
                                "platform": entry.platform,
                                "note": f"This {domain} is defined in YAML and must be removed manually",
                            })
                    continue

                # Skip high-risk safety entities
                device_class = entry.original_device_class or entry.device_class
                if device_class and device_class.lower() in _SAFETY_CLASSES:
                    results["skipped_high_risk"].append(entity_id)
                    _LOGGER.warning("Skipping high-risk safety entity: %s", entity_id)
                    continue

                # YAML-defined entities cannot be deleted via API
                if entry.config_entry_id is None and entry.platform not in ("mqtt", None):
                    results["yaml_manual"].append({
                        "entity_id": entity_id,
                        "platform": entry.platform,
                    })
                    continue

                if soft_delete:
                    # Disable the entity — USER disabler works for any entity
                    was_already_disabled = entry.disabled
                    if not was_already_disabled:
                        ent_reg.async_update_entity(
                            entity_id,
                            disabled_by=er.RegistryEntryDisabler.USER,
                        )
                        _LOGGER.info("Soft-deleted (disabled) entity: %s", entity_id)
                    else:
                        _LOGGER.info(
                            "Entity was already disabled, adding to trash: %s", entity_id
                        )
                        results["already_disabled"].append(entity_id)
                    # Always track in soft_deleted (= goes to trash)
                    results["soft_deleted"].append(entity_id)
                else:
                    # Hard delete
                    ent_reg.async_remove(entity_id)
                    results["success"].append(entity_id)
                    _LOGGER.info("Hard-deleted entity: %s", entity_id)

            except Exception as exc:
                _LOGGER.error("Error purging %s: %s", entity_id, exc)
                results["failed"].append({"entity_id": entity_id, "error": str(exc)})

        return results

    async def async_restore_entity(self, entity_id: str) -> dict:
        """Re-enable a soft-deleted (disabled) entity. Returns dict with success + re_enabled."""
        try:
            ent_reg = er.async_get(self.hass)
            entry = ent_reg.async_get(entity_id)
            if not entry:
                _LOGGER.warning("Cannot restore %s — not found in registry", entity_id)
                return {"success": False, "re_enabled": False, "error": "not found in registry"}
            if not entry.disabled:
                _LOGGER.info("Entity %s is not disabled — removing from trash only", entity_id)
                return {"success": True, "re_enabled": False}
            # Only clear USER or INTEGRATION disabler (not SYSTEM/CONFIG_ENTRY)
            if entry.disabled_by in (
                er.RegistryEntryDisabler.USER,
                er.RegistryEntryDisabler.INTEGRATION,
            ):
                ent_reg.async_update_entity(entity_id, disabled_by=None)
                _LOGGER.info("Restored entity: %s", entity_id)
                return {"success": True, "re_enabled": True}
            else:
                _LOGGER.warning(
                    "Cannot restore %s — disabled by %s (not USER/INTEGRATION)",
                    entity_id, entry.disabled_by
                )
                return {
                    "success": False,
                    "re_enabled": False,
                    "error": f"disabled by {entry.disabled_by} (not USER/INTEGRATION)",
                }
        except Exception as exc:
            _LOGGER.error("Failed to restore %s: %s", entity_id, exc)
            return {"success": False, "re_enabled": False, "error": str(exc)}

    async def async_hard_delete_soft_deleted(self, entity_ids: list[str]) -> dict[str, Any]:
        """Permanently remove entities that have been soft-deleted."""
        return await self.async_purge_entities(entity_ids, soft_delete=False)

    async def _remove_by_domain(self, entity_id: str) -> str:
        """Remove an automation/script through its config entry.

        Returns one of:
          "removed"  - really gone
          "disabled" - could not be deleted, so it was only disabled; the
                       caller must NOT report this as a success, and the
                       entity must not be dropped from the trash records
          "not_found" - nothing to act on

        The previous version returned True from the "disabled" branch, so a
        failed hard delete was reported to the user as a completed one — and
        the entity was left disabled but untracked, meaning nothing would
        ever restore or finish it. It also probed hass.data["automation_storage"]
        and hass.data["script_storage"], which do not exist in Home Assistant,
        so that path could never fire.
        """
        domain = entity_id.split(".")[0]
        if domain not in ("automation", "script"):
            return "not_found"

        try:
            # The entity registry entry carries the config entry that owns it.
            # That is the only reliable link: the entity_id slug, the config
            # entry's unique_id and its entry_id are all different things.
            ent_reg = er.async_get(self.hass)
            reg_entry = ent_reg.async_get(entity_id)
            entry_id = getattr(reg_entry, "config_entry_id", None)
            if entry_id:
                await self.hass.config_entries.async_remove(entry_id)
                _LOGGER.info("Deleted %s config entry %s", domain, entry_id)
                return "removed"
            else:
                # YAML-defined: no config entry owns it, so it cannot be
                # deleted from here at all.
                _LOGGER.info(
                    "%s has no config entry (YAML-defined) - cannot delete", entity_id
                )
                return "not_found"

        except Exception as exc:
            _LOGGER.warning(
                "Could not delete %s: %s - disabling instead", entity_id, exc
            )
            ent_reg = er.async_get(self.hass)
            if ent_reg.async_get(entity_id):
                ent_reg.async_update_entity(
                    entity_id, disabled_by=er.RegistryEntryDisabler.USER
                )
            return "disabled"

    async def async_get_dependency_map(self, entity_id: str) -> dict[str, Any]:
        """Get all places where an entity is referenced (for impact analysis)."""
        deps = {
            "automations": [],
            "scripts": [],
            "groups": [],
            "dashboards": "Cannot be scanned at runtime - check manually",
        }

        # Check automations
        for state in self.hass.states.async_all("automation"):
            automation_id = state.entity_id
            # Check the automation's config via service
            try:
                result = await self.hass.services.async_call(
                    "automation",
                    "config",
                    {"entity_id": automation_id},
                    blocking=True,
                    return_response=True,
                )
            except Exception:
                pass

        # Simple check: scan all state attributes
        for state in self.hass.states.async_all():
            attrs_str = str(state.attributes)
            if entity_id in attrs_str:
                domain = state.entity_id.split(".")[0]
                if domain == "automation":
                    deps["automations"].append(state.entity_id)
                elif domain == "script":
                    deps["scripts"].append(state.entity_id)
                elif domain == "group":
                    deps["groups"].append(state.entity_id)

        return deps


# Device classes that must never be removed here. This used to be a second,
# hand-maintained copy of SAFETY_DEVICE_CLASSES that had silently drifted: it
# was missing door, window, motion, occupancy, vibration and sound, so the
# execution layer would happily hard-delete a door sensor or a motion sensor
# that the scanner had been careful never to suggest. One source of truth now.
_SAFETY_CLASSES = SAFETY_DEVICE_CLASSES
