"""Operator selections for the shared Hydronicus plant."""

from __future__ import annotations

from typing import cast

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HydronicConfigEntry
from .const import DOMAIN
from .core.model import PlantMode
from .runtime import HydronicRuntime


class PlantModeSelect(SelectEntity):
    """Select the requested operating mode without directly controlling cooling."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_options = [mode.value for mode in PlantMode]

    def __init__(self, entry: HydronicConfigEntry) -> None:
        """Bind the selection to the plant runtime."""
        self._entry = entry
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_requested_mode"
        self._attr_name = "Requested mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        """Resolve the current runtime after an entry reload."""
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        """Subscribe to atomic controller evaluations."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def current_option(self) -> str:
        """Return the requested mode, not the transient active mode."""
        return self._runtime.requested_mode().value

    async def async_select_option(self, option: str) -> None:
        """Persist the request and let the controller perform safe changeover."""
        await self._runtime.async_set_requested_mode(option, hass=self.hass)


async def async_setup_entry(
    hass: HomeAssistant, entry: HydronicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add the one plant-level requested-mode selector."""
    async_add_entities([PlantModeSelect(entry)])
