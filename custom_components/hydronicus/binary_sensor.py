"""Read-only shadow demand entities."""

from __future__ import annotations

from typing import cast

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HydronicConfigEntry
from .const import DOMAIN
from .core.model import PumpState, ValveState
from .runtime import HydronicRuntime


class HydronicShadowEntity(BinarySensorEntity):
    """Shared lifecycle for entities driven by the in-memory shadow runtime."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: HydronicConfigEntry) -> None:
        """Bind the entity to one plant runtime."""
        self._runtime: HydronicRuntime = cast(HydronicRuntime, entry.runtime_data)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._runtime.plant_id)}, name=self._runtime.name
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe after Home Assistant has registered the entity."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))


class ModeChangeoverLockoutBinarySensor(HydronicShadowEntity):
    """Whether a requested mode is waiting for safe shared-plant idle."""

    _attr_icon = "mdi:lock-clock"

    def __init__(self, entry: HydronicConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{self._runtime.plant_id}_mode_changeover_lockout"
        self._attr_name = "Mode changeover lockout"

    @property
    def is_on(self) -> bool:
        """Return the structured transition state."""
        return self._runtime.mode_is_locked()

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose mode, phase, and deadline as machine-readable attributes."""
        state = self._runtime.runtime_state
        return {
            "requested_mode": state.requested_mode.value,
            "active_mode": state.plant_mode.value,
            "phase": state.changeover_phase.value,
            "target_mode": (
                state.changeover_target_mode.value
                if state.changeover_target_mode is not None
                else None
            ),
            "deadline": (
                state.changeover_deadline.isoformat()
                if state.changeover_deadline is not None
                else None
            ),
            "reason": self._runtime.mode_explanation(),
        }


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
        return bool(self._runtime.runtime_state.zone_demands.get(self._zone_id, False))


class ZoneBlockedBinarySensor(HydronicShadowEntity):
    """Whether sensor health currently blocks a zone from requesting heat."""

    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, entry: HydronicConfigEntry, zone_id: str, name: str) -> None:
        super().__init__(entry)
        self._zone_id = zone_id
        self._attr_unique_id = f"{self._runtime.plant_id}_{zone_id}_blocked"
        self._attr_name = f"{name} blocked"

    @property
    def is_on(self) -> bool:
        """Return structured blocked state from the latest evaluation."""
        return self._runtime.zone_is_blocked(self._zone_id)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose structured block diagnostics without parsing display prose."""
        aggregation = self._runtime.zone_aggregation(self._zone_id)
        return {
            "reason": self._runtime.zone_blocked_reason(self._zone_id),
            "blocking_required_sensor_ids": (
                list(aggregation.blocking_required_sensor_ids) if aggregation is not None else []
            ),
            "excluded_optional_sensor_ids": (
                list(aggregation.excluded_optional_sensor_ids) if aggregation is not None else []
            ),
        }


class ZoneCoolingDemandBinarySensor(HydronicShadowEntity):
    """Whether a zone currently requests cooling in shadow mode."""

    def __init__(self, entry: HydronicConfigEntry, zone_id: str, name: str) -> None:
        super().__init__(entry)
        self._zone_id = zone_id
        self._attr_unique_id = f"{self._runtime.plant_id}_{zone_id}_cooling_demand"
        self._attr_name = f"{name} cooling demand"

    @property
    def is_on(self) -> bool:
        """Return the latest pure-controller cooling demand."""
        return bool(self._runtime.runtime_state.cooling_zone_demands.get(self._zone_id, False))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose cooling decision status and explanation."""
        decision = self._runtime.cooling_zone_decision(self._zone_id)
        return {
            "blocked": self._runtime.cooling_zone_is_blocked(self._zone_id),
            "reason": self._runtime.cooling_zone_blocked_reason(self._zone_id),
            "decision_status": (
                getattr(decision.status, "value", decision.status) if decision is not None else None
            ),
        }


class ZoneCoolingBlockedBinarySensor(HydronicShadowEntity):
    """Whether cooling safety currently blocks a zone."""

    _attr_icon = "mdi:water-alert-outline"

    def __init__(self, entry: HydronicConfigEntry, zone_id: str, name: str) -> None:
        super().__init__(entry)
        self._zone_id = zone_id
        self._attr_unique_id = f"{self._runtime.plant_id}_{zone_id}_cooling_blocked"
        self._attr_name = f"{name} cooling blocked"

    @property
    def is_on(self) -> bool:
        """Return structured cooling safety state."""
        return self._runtime.cooling_zone_is_blocked(self._zone_id)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the interlock explanation without prose parsing."""
        decision = self._runtime.cooling_zone_decision(self._zone_id)
        return {
            "reason": self._runtime.cooling_zone_blocked_reason(self._zone_id),
            "dew_point": decision.dew_point if decision is not None else None,
            "condensation_margin": decision.condensation_margin if decision is not None else None,
        }


class SourceDemandBinarySensor(HydronicShadowEntity):
    """Expose one guarded source-demand recommendation as a synthetic output."""

    def __init__(self, entry: HydronicConfigEntry, source_id: str, name: str) -> None:
        super().__init__(entry)
        self._source_id = source_id
        self._attr_unique_id = f"{self._runtime.plant_id}_{source_id}_demand"
        self._attr_name = f"{name} demand"

    @property
    def is_on(self) -> bool:
        """Return the demand requested by the latest evaluation, including shadow mode."""
        diagnostic = self._runtime.source_diagnostic(self._source_id)
        return bool(getattr(diagnostic, "demand_requested", False))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the guarded permit and execution boundary atomically."""
        diagnostic = self._runtime.source_diagnostic(self._source_id)
        source = self._runtime.plant.sources[self._source_id]
        return {
            "available": getattr(diagnostic, "available", None),
            "eligible": getattr(diagnostic, "eligible", False),
            "recommended": getattr(diagnostic, "recommended", False),
            "active": getattr(diagnostic, "active", False),
            "demand_permitted": getattr(diagnostic, "demand_permitted", False),
            "source_shadow_mode": source.shadow_mode,
            "global_shadow_mode": self._runtime.shadow_mode,
            "blocked": getattr(diagnostic, "blocked", False),
            "reason": getattr(diagnostic, "reason", None),
        }


class SourceAvailableBinarySensor(HydronicShadowEntity):
    """Expose the source availability input used by qualification."""

    def __init__(self, entry: HydronicConfigEntry, source_id: str, name: str) -> None:
        super().__init__(entry)
        self._source_id = source_id
        self._attr_unique_id = f"{self._runtime.plant_id}_{source_id}_available"
        self._attr_name = f"{name} available"

    @property
    def is_on(self) -> bool:
        """Return only a positively known available state."""
        diagnostic = self._runtime.source_diagnostic(self._source_id)
        return getattr(diagnostic, "available", None) is True

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose eligibility and the qualification reason."""
        diagnostic = self._runtime.source_diagnostic(self._source_id)
        return {
            "available": getattr(diagnostic, "available", None),
            "eligible": getattr(diagnostic, "eligible", False),
            "reason": getattr(diagnostic, "reason", None),
        }


class SourceActiveBinarySensor(HydronicShadowEntity):
    """Expose which source owns the current guarded heating request."""

    def __init__(self, entry: HydronicConfigEntry, source_id: str, name: str) -> None:
        super().__init__(entry)
        self._source_id = source_id
        self._attr_unique_id = f"{self._runtime.plant_id}_{source_id}_active"
        self._attr_name = f"{name} active"

    @property
    def is_on(self) -> bool:
        """Return the active-source result from the same evaluation as demand."""
        diagnostic = self._runtime.source_diagnostic(self._source_id)
        return bool(getattr(diagnostic, "active", False))


class SourceBlockedBinarySensor(HydronicShadowEntity):
    """Expose source-specific blocking without blocking unrelated source demand."""

    _attr_icon = "mdi:source-branch-off"

    def __init__(self, entry: HydronicConfigEntry, source_id: str, name: str) -> None:
        super().__init__(entry)
        self._source_id = source_id
        self._attr_unique_id = f"{self._runtime.plant_id}_{source_id}_blocked"
        self._attr_name = f"{name} blocked"

    @property
    def is_on(self) -> bool:
        """Return whether this source's dependent demand is blocked."""
        diagnostic = self._runtime.source_diagnostic(self._source_id)
        return bool(getattr(diagnostic, "blocked", False))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the bounded source block explanation."""
        diagnostic = self._runtime.source_diagnostic(self._source_id)
        return {"reason": getattr(diagnostic, "reason", None)}


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
            in (PumpState.STARTING, PumpState.RUNNING, PumpState.OVERRUN)
        )


class ActuatorMismatchBinarySensor(HydronicShadowEntity):
    """Expose a manual actuator-state mismatch without toggle behavior."""

    _attr_icon = "mdi:alert-outline"

    def __init__(self, entry: HydronicConfigEntry, actuator_id: str, name: str) -> None:
        super().__init__(entry)
        self._actuator_id = actuator_id
        self._attr_unique_id = f"{self._runtime.plant_id}_{actuator_id}_mismatch"
        self._attr_name = f"{name} mismatch"

    @property
    def is_on(self) -> bool:
        """Return the structured mismatch state."""
        if self._runtime.actuator_execution_failure(self._actuator_id) is not None:
            return True
        diagnostic = self._runtime.actuator_diagnostic(self._actuator_id)
        return bool(getattr(diagnostic, "mismatch", False))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose expected and observed values for operator diagnosis."""
        diagnostic = self._runtime.actuator_diagnostic(self._actuator_id)
        failure = self._runtime.actuator_execution_failure(self._actuator_id)
        return {
            "expected": getattr(diagnostic, "expected", None),
            "observed": getattr(diagnostic, "observed", None),
            "reason": getattr(diagnostic, "reason", None),
            "feedback_kind": getattr(diagnostic, "feedback_kind", None),
            "execution_failure": getattr(failure, "explanation", None),
        }


class ActuatorBlockedBinarySensor(HydronicShadowEntity):
    """Expose whether unsafe actuator feedback blocks dependent circuits."""

    _attr_icon = "mdi:shield-alert-outline"

    def __init__(self, entry: HydronicConfigEntry, actuator_id: str, name: str) -> None:
        super().__init__(entry)
        self._actuator_id = actuator_id
        self._attr_unique_id = f"{self._runtime.plant_id}_{actuator_id}_blocked"
        self._attr_name = f"{name} blocked"

    @property
    def is_on(self) -> bool:
        """Return whether dependent circuits fail closed."""
        if self._runtime.actuator_execution_failure(self._actuator_id) is not None:
            return True
        diagnostic = self._runtime.actuator_diagnostic(self._actuator_id)
        return bool(getattr(diagnostic, "blocked", False))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose stale feedback and the dependent block reason."""
        diagnostic = self._runtime.actuator_diagnostic(self._actuator_id)
        failure = self._runtime.actuator_execution_failure(self._actuator_id)
        return {
            "reason": getattr(diagnostic, "reason", None),
            "stale_feedback": list(getattr(diagnostic, "stale_feedback", ())),
            "dependent_blocked": getattr(diagnostic, "blocked", False),
            "execution_failure": getattr(failure, "explanation", None),
        }


async def async_setup_entry(
    hass: HomeAssistant, entry: HydronicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add read-only shadow demand and actuator-request entities."""
    runtime = entry.runtime_data
    parent_entities: list[BinarySensorEntity] = [ModeChangeoverLockoutBinarySensor(entry)]
    subentry_entities: dict[str, list[BinarySensorEntity]] = {}
    for zone in runtime.plant.zones.values():
        entities = [
            ZoneDemandBinarySensor(entry, zone.id, zone.name),
            ZoneBlockedBinarySensor(entry, zone.id, zone.name),
            ZoneCoolingDemandBinarySensor(entry, zone.id, zone.name),
            ZoneCoolingBlockedBinarySensor(entry, zone.id, zone.name),
        ]
        if subentry_id := runtime.zone_subentry_ids.get(zone.id):
            subentry_entities.setdefault(subentry_id, []).extend(entities)
        else:
            parent_entities.extend(entities)
    for source in runtime.plant.sources.values():
        entities = [
            SourceDemandBinarySensor(entry, source.id, source.name),
            SourceAvailableBinarySensor(entry, source.id, source.name),
            SourceActiveBinarySensor(entry, source.id, source.name),
            SourceBlockedBinarySensor(entry, source.id, source.name),
        ]
        if subentry_id := runtime.source_subentry_ids.get(source.id):
            subentry_entities.setdefault(subentry_id, []).extend(entities)
        else:
            parent_entities.extend(entities)
    for valve in runtime.plant.valves.values():
        entities = [
            ActuatorRequestedBinarySensor(entry, valve.id, valve.name, "valve"),
            ActuatorMismatchBinarySensor(entry, valve.id, valve.name),
            ActuatorBlockedBinarySensor(entry, valve.id, valve.name),
        ]
        if subentry_id := runtime.actuator_subentry_ids.get(valve.id):
            subentry_entities.setdefault(subentry_id, []).extend(entities)
        else:
            parent_entities.extend(entities)
    for pump in runtime.plant.pumps.values():
        parent_entities.extend(
            (
                ActuatorRequestedBinarySensor(entry, pump.id, pump.name, "pump"),
                ActuatorMismatchBinarySensor(entry, pump.id, pump.name),
                ActuatorBlockedBinarySensor(entry, pump.id, pump.name),
            )
        )
    async_add_entities(parent_entities)
    for subentry_id, entities in subentry_entities.items():
        async_add_entities(entities, config_subentry_id=subentry_id)
