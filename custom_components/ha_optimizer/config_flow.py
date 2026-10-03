"""Config flow for HA Optimizer."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    DOMAIN,
    CONF_SCAN_INTERVAL_DAYS,
    CONF_STALE_DAYS_THRESHOLD,
    CONF_ENABLE_SOFT_DELETE,
    CONF_SOFT_DELETE_DAYS,
    CONF_EXCLUDE_DEVICE_CLASSES,
    DEFAULT_SCAN_INTERVAL_DAYS,
    DEFAULT_STALE_DAYS_THRESHOLD,
    DEFAULT_ENABLE_SOFT_DELETE,
    DEFAULT_SOFT_DELETE_DAYS,
    DEFAULT_EXCLUDE_DEVICE_CLASSES,
)

STEP_SCHEMA = vol.Schema({
    vol.Optional(CONF_SCAN_INTERVAL_DAYS, default=DEFAULT_SCAN_INTERVAL_DAYS):
        vol.All(int, vol.Range(min=0, max=365)),
    vol.Optional(CONF_STALE_DAYS_THRESHOLD, default=DEFAULT_STALE_DAYS_THRESHOLD):
        vol.All(int, vol.Range(min=1, max=365)),
    vol.Optional(CONF_ENABLE_SOFT_DELETE, default=DEFAULT_ENABLE_SOFT_DELETE):
        bool,
    # 0 is allowed and means "never expire". The README documents it as
    # the way to keep the trash indefinitely, and the store now honours it;
    # the floor of 1 made the documented switch unreachable, and the only
    # way to reach 0 was to set it by hand - where it used to mean the
    # opposite, expiring everything at once.
    vol.Optional(CONF_SOFT_DELETE_DAYS, default=DEFAULT_SOFT_DELETE_DAYS):
        vol.All(int, vol.Range(min=0, max=90)),
    vol.Optional(CONF_EXCLUDE_DEVICE_CLASSES, default=DEFAULT_EXCLUDE_DEVICE_CLASSES):
        str,
})


class PurgeEngineConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HA Optimizer."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        if user_input is not None:
            # Store settings in options, keep data empty
            return self.async_create_entry(title="HA Optimizer", data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Return the options flow handler."""
        return PurgeEngineOptionsFlow()


class PurgeEngineOptionsFlow(config_entries.OptionsFlow):
    """Handle the options flow for HA Optimizer.

    Home Assistant split this in two: up to 2024.10 the config entry was
    handed to the flow's constructor, and from 2024.11 it is injected on the
    instance instead - and assigning it in `__init__` became a deprecation
    error. The original code relied on the new behaviour while the README
    promised 2023.7, which meant the settings dialog would have raised
    `AttributeError` on every older instance.

    Accepting both is cheap and harmless, so the flow does that and
    `manifest.json` states the version that is actually exercised in CI.
    """

    def __init__(self, config_entry=None):
        # Only the pre-2024.11 path ever has anything to store.
        if config_entry is not None:
            self._legacy_entry = config_entry

    def _entry(self):
        """The config entry, whichever way this version of HA provides it."""
        try:
            entry = self.config_entry      # HA >= 2024.11 sets this for us
        except (AttributeError, TypeError):
            entry = None
        return entry or getattr(self, "_legacy_entry", None)

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        entry = self._entry()
        # Read current values from either options or data (first-time migration)
        current = dict(entry.options) if entry else {}
        if not current and entry:
            current = dict(entry.data)

        schema = vol.Schema({
            vol.Optional(
                CONF_SCAN_INTERVAL_DAYS,
                default=current.get(CONF_SCAN_INTERVAL_DAYS, DEFAULT_SCAN_INTERVAL_DAYS),
            ): vol.All(int, vol.Range(min=0, max=365)),
            vol.Optional(
                CONF_STALE_DAYS_THRESHOLD,
                default=current.get(CONF_STALE_DAYS_THRESHOLD, DEFAULT_STALE_DAYS_THRESHOLD),
            ): vol.All(int, vol.Range(min=1, max=365)),
            vol.Optional(
                CONF_ENABLE_SOFT_DELETE,
                default=current.get(CONF_ENABLE_SOFT_DELETE, DEFAULT_ENABLE_SOFT_DELETE),
            ): bool,
            vol.Optional(
                CONF_SOFT_DELETE_DAYS,
                default=current.get(CONF_SOFT_DELETE_DAYS, DEFAULT_SOFT_DELETE_DAYS),
            ): vol.All(int, vol.Range(min=0, max=90)),
            vol.Optional(
                CONF_EXCLUDE_DEVICE_CLASSES,
                default=current.get(CONF_EXCLUDE_DEVICE_CLASSES, DEFAULT_EXCLUDE_DEVICE_CLASSES),
            ): str,
        })

        return self.async_show_form(step_id="init", data_schema=schema)
