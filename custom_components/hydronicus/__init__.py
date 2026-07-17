"""Hydronicus integration setup."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .runtime import HydronicRuntime

type HydronicConfigEntry = ConfigEntry[HydronicRuntime]


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
