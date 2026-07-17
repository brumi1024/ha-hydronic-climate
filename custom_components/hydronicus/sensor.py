"""Read-only explanations for shadow controller decisions."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HydronicConfigEntry
from .const import DOMAIN


class TopologyPreviewSensor(SensorEntity):
    """Expose the compiled plant graph in a persistent diagnostic entity."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_icon = "mdi:graph-outline"
    _attr_should_poll = False

    def __init__(self, entry: HydronicConfigEntry) -> None:
        """Bind the preview to one compiled plant runtime."""
        self._runtime = entry.runtime_data
        self._attr_unique_id = f"{self._runtime.plant_id}_topology_preview"
        self._attr_name = "Topology preview"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._runtime.plant_id)}, name=self._runtime.name
        )

    @property
    def native_value(self) -> str:
        """Summarize the graph size without overflowing Home Assistant state length."""
        zone_count = len(self._runtime.plant.zones)
        circuit_count = len(self._runtime.plant.circuits)
        zone_noun = "zone" if zone_count == 1 else "zones"
        circuit_noun = "circuit" if circuit_count == 1 else "circuits"
        return f"{zone_count} {zone_noun}, {circuit_count} {circuit_noun}"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return every human-readable compiler decision as structured data."""
        return {
            "logic_summary": list(self._runtime.plant.logic_summary),
            "routes": len(self._runtime.plant.routes),
            "valves": len(self._runtime.plant.valves),
            "pumps": len(self._runtime.plant.pumps),
        }


class ZoneExplanationSensor(SensorEntity):
    """Expose the last controller explanation for a comfort zone."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: HydronicConfigEntry, zone_id: str, name: str) -> None:
        """Bind a diagnostic entity to one zone."""
        self._runtime = entry.runtime_data
        self._zone_id = zone_id
        self._attr_unique_id = f"{self._runtime.plant_id}_{zone_id}_explanation"
        self._attr_name = f"{name} explanation"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._runtime.plant_id)}, name=self._runtime.name
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe after Home Assistant has registered the entity."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str | None:
        """Return the cached human-readable controller explanation."""
        if self._runtime.evaluation is None:
            return None
        return self._runtime.evaluation.diagnostics.zone_reasons.get(self._zone_id)


async def async_setup_entry(
    hass: HomeAssistant, entry: HydronicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add read-only explanations for all configured zones."""
    runtime = entry.runtime_data
    parent_entities: list[SensorEntity] = [TopologyPreviewSensor(entry)]
    subentry_entities: dict[str, list[SensorEntity]] = {}
    for zone in runtime.plant.zones.values():
        entity = ZoneExplanationSensor(entry, zone.id, zone.name)
        if subentry_id := runtime.zone_subentry_ids.get(zone.id):
            subentry_entities.setdefault(subentry_id, []).append(entity)
        else:
            parent_entities.append(entity)
    async_add_entities(parent_entities)
    for subentry_id, entities in subentry_entities.items():
        async_add_entities(entities, config_subentry_id=subentry_id)
