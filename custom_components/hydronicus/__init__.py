"""Hydronicus integration setup."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .migration import ConfigEntryMigrationError, migrate_entry_data
from .runtime import HydronicRuntime

type HydronicConfigEntry = ConfigEntry[HydronicRuntime]

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: HydronicConfigEntry) -> bool:
    """Migrate a persisted plant before setup, updating only validated data."""
    from .migration import CURRENT_CONFIG_ENTRY_VERSION

    source_version = (entry.version, entry.minor_version)
    if source_version == CURRENT_CONFIG_ENTRY_VERSION:
        return True
    try:
        migrated_data = migrate_entry_data(
            entry.data,
            version=entry.version,
            minor_version=entry.minor_version,
        )
    except ConfigEntryMigrationError as error:
        _LOGGER.error(
            "Hydronicus config-entry migration from %s failed: %s; "
            "the entry was left unchanged. Inspect the referenced topology field "
            "and restore a compatible backup if the data is not repairable.",
            source_version,
            error,
        )
        return False
    hass.config_entries.async_update_entry(
        entry,
        data=migrated_data,
        version=CURRENT_CONFIG_ENTRY_VERSION[0],
        minor_version=CURRENT_CONFIG_ENTRY_VERSION[1],
    )
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: HydronicConfigEntry) -> None:
    """Reload the complete plant after config-entry or subentry changes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: HydronicConfigEntry) -> bool:
    """Set up a hydronic plant from a config entry."""
    runtime = HydronicRuntime.from_entry(entry)
    entry.runtime_data = runtime
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await runtime.async_start(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HydronicConfigEntry) -> bool:
    """Unload a hydronic plant without issuing equipment commands."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
        entry.runtime_data = None
    return unloaded
