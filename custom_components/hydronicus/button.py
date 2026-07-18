"""Explicit operator actions for Hydronicus."""

from __future__ import annotations

from typing import cast

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HydronicConfigEntry
from .const import DOMAIN
from .runtime import HydronicRuntime


class SafeShutdownButton(ButtonEntity):
    """Release source demand and stop hydraulic equipment in safe order."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:power-cycle"

    def __init__(self, entry: HydronicConfigEntry) -> None:
        """Bind the button to the plant runtime."""
        self._entry = entry
        runtime = cast(HydronicRuntime, entry.runtime_data)
        self._attr_unique_id = f"{runtime.plant_id}_safe_shutdown"
        self._attr_name = "Safe shutdown"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        """Resolve the current runtime after a config-entry reload."""
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_press(self) -> None:
        """Start or advance the explicit shutdown sequence."""
        hass = cast(HomeAssistant, self.hass)
        await self._runtime.async_safe_shutdown(hass)


async def async_setup_entry(
    hass: HomeAssistant, entry: HydronicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Publish one plant-level safe-shutdown action."""
    async_add_entities([SafeShutdownButton(entry)])
