"""Read-only shadow demand entities."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HydronicConfigEntry
from .const import DOMAIN
from .core.model import PumpState, ValveState


class HydronicShadowEntity(BinarySensorEntity):
    """Shared lifecycle for entities driven by the in-memory shadow runtime."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: HydronicConfigEntry) -> None:
        """Bind the entity to one plant runtime."""
        self._runtime = entry.runtime_data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._runtime.plant_id)}, name=self._runtime.name
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe after Home Assistant has registered the entity."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))


class ZoneDemandBinarySensor(HydronicShadowEntity):
    """Whether a zone currently requests heat in shadow mode."""

    def __init__(self, entry: HydronicConfigEntry, zone_id: str, name: str) -> None:
        super().__init__(entry)
        self._zone_id = zone_id
        self._attr_unique_id = f"{self._runtime.plant_id}_{zone_id}_demand"
        self._attr_name = f"{name} demand"

    @property
    def is_on(self) -> bool:
        """Return the cached calculated demand."""
        return self._runtime.runtime_state.zone_demands.get(self._zone_id, False)


class ActuatorRequestedBinarySensor(HydronicShadowEntity):
    """Whether a valve or pump is virtually requested by the controller."""

    def __init__(
        self, entry: HydronicConfigEntry, actuator_id: str, actuator_name: str, kind: str
    ) -> None:
        super().__init__(entry)
        self._actuator_id = actuator_id
        self._kind = kind
        self._attr_unique_id = f"{self._runtime.plant_id}_{kind}_{actuator_id}_requested"
        self._attr_name = f"{actuator_name} requested"

    @property
    def is_on(self) -> bool:
        """Return the cached virtual request state."""
        if self._kind == "valve":
            return self._runtime.runtime_state.valves.get(self._actuator_id, None) is not None and (
                self._runtime.runtime_state.valves[self._actuator_id].state is not ValveState.CLOSED
            )
        return self._runtime.runtime_state.pumps.get(self._actuator_id, None) is not None and (
            self._runtime.runtime_state.pumps[self._actuator_id].state
            in (PumpState.RUNNING, PumpState.OVERRUN)
        )


async def async_setup_entry(
    hass: HomeAssistant, entry: HydronicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add read-only shadow demand and actuator-request entities."""
    runtime = entry.runtime_data
    parent_entities: list[BinarySensorEntity] = []
    subentry_entities: dict[str, list[BinarySensorEntity]] = {}
    for zone in runtime.plant.zones.values():
        entity = ZoneDemandBinarySensor(entry, zone.id, zone.name)
        if subentry_id := runtime.zone_subentry_ids.get(zone.id):
            subentry_entities.setdefault(subentry_id, []).append(entity)
        else:
            parent_entities.append(entity)
    for valve in runtime.plant.valves.values():
        entity = ActuatorRequestedBinarySensor(entry, valve.id, valve.name, "valve")
        if subentry_id := runtime.actuator_subentry_ids.get(valve.id):
            subentry_entities.setdefault(subentry_id, []).append(entity)
        else:
            parent_entities.append(entity)
    parent_entities.extend(
        ActuatorRequestedBinarySensor(entry, pump.id, pump.name, "pump")
        for pump in runtime.plant.pumps.values()
    )
    async_add_entities(parent_entities)
    for subentry_id, entities in subentry_entities.items():
        async_add_entities(entities, config_subentry_id=subentry_id)
