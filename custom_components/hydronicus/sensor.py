"""Read-only explanations for shadow controller decisions."""

from __future__ import annotations

from typing import Any, cast

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HydronicConfigEntry
from .const import (
    DOMAIN,
    MAX_RECONCILIATION_INTERVAL_SECONDS,
    MIN_RECONCILIATION_INTERVAL_SECONDS,
    RECONCILIATION_INTERVAL_SECONDS,
)
from .runtime import HydronicRuntime

_MAX_STATE_LENGTH = 255


class ControllerStatusSensor(SensorEntity):
    """Expose one low-cardinality plant status for Recorder and dashboards."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_icon = "mdi:state-machine"
    _attr_should_poll = False

    def __init__(self, entry: HydronicConfigEntry) -> None:
        """Bind the status to one plant runtime."""
        self._entry = entry
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_controller_status"
        self._attr_name = "Controller status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        """Resolve the current runtime after a config-entry reload."""
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        """Subscribe to atomic controller evaluations."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        """Return a bounded, low-cardinality controller status."""
        return self._runtime.operational_status()

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the latest operation decisions beside the bounded status."""
        return {
            "dry_run": self._runtime.dry_run,
            "safe_shutdown_phase": self._runtime.runtime_state.safe_shutdown_phase.value,
            "operations": self._runtime.execution_summary(),
        }


class ReconciliationStatusSensor(SensorEntity):
    """Expose bounded reconciliation status without high-cardinality attributes."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_icon = "mdi:sync-circle"
    _attr_should_poll = False

    def __init__(self, entry: HydronicConfigEntry) -> None:
        """Bind reconciliation telemetry to one plant runtime."""
        self._entry = entry
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_reconciliation_status"
        self._attr_name = "Reconciliation status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        """Resolve the current runtime after a config-entry reload."""
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        """Subscribe to bounded reconciliation updates."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        """Return the latest bounded reconciliation outcome."""
        return self._runtime.last_reconciliation_status

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose only static scheduling policy, keeping Recorder cardinality low."""
        interval = min(
            max(float(RECONCILIATION_INTERVAL_SECONDS), MIN_RECONCILIATION_INTERVAL_SECONDS),
            MAX_RECONCILIATION_INTERVAL_SECONDS,
        )
        return {"interval_seconds": interval, "bounded": True}


class TopologyPreviewSensor(SensorEntity):
    """Expose the compiled plant graph in a persistent diagnostic entity."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_icon = "mdi:graph-outline"
    _attr_should_poll = False

    def __init__(self, entry: HydronicConfigEntry) -> None:
        """Bind the preview to one compiled plant runtime."""
        self._entry = entry
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_topology_preview"
        self._attr_name = "Topology preview"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        """Resolve the current runtime after a config-entry reload."""
        return cast(HydronicRuntime, self._entry.runtime_data)

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
            "warnings": [
                {
                    "code": warning.code,
                    "message": warning.message,
                    "valve_id": warning.valve_id,
                    "circuit_ids": list(warning.circuit_ids),
                    "zone_ids": list(warning.zone_ids),
                }
                for warning in self._runtime.plant.warnings
            ],
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
        self._entry = entry
        self._zone_id = zone_id
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_{zone_id}_explanation"
        self._attr_name = f"{name} explanation"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        """Resolve the current runtime after a config-entry reload."""
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        """Subscribe after Home Assistant has registered the entity."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str | None:
        """Return the cached human-readable controller explanation."""
        if self._runtime.evaluation is None:
            return None
        return cast(
            str | None,
            self._runtime.evaluation.diagnostics.zone_reasons.get(self._zone_id),
        )

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose structured controller status alongside the display explanation."""
        return _zone_diagnostic_attributes(self._runtime, self._zone_id)


class ZoneAggregateTemperatureSensor(SensorEntity):
    """Expose the temperature aggregate used by the controller."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: HydronicConfigEntry, zone_id: str, name: str) -> None:
        """Bind the aggregate to one comfort zone."""
        self._entry = entry
        self._zone_id = zone_id
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_{zone_id}_aggregate_temperature"
        self._attr_name = f"{name} aggregate temperature"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        """Resolve the current runtime after a config-entry reload."""
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        """Subscribe after Home Assistant has registered the entity."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> float | None:
        """Return the aggregate from the last atomic evaluation."""
        return self._runtime.zone_current_temperature(self._zone_id)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose sensor-health details without requiring prose parsing."""
        return _zone_diagnostic_attributes(self._runtime, self._zone_id)


class ZoneBlockedReasonSensor(SensorEntity):
    """Expose the structured sensor-health reason for one zone."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, entry: HydronicConfigEntry, zone_id: str, name: str) -> None:
        """Bind the blocked reason to one comfort zone."""
        self._entry = entry
        self._zone_id = zone_id
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_{zone_id}_blocked_reason"
        self._attr_name = f"{name} blocked reason"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        """Resolve the current runtime after a config-entry reload."""
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        """Subscribe after Home Assistant has registered the entity."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        """Return a stable sentinel when the zone is not blocked."""
        reason = self._runtime.zone_blocked_reason(self._zone_id) or "none"
        return reason[:_MAX_STATE_LENGTH]

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the structured block details."""
        return _zone_diagnostic_attributes(self._runtime, self._zone_id)


class RecommendedSourceSensor(SensorEntity):
    """Expose the current deterministic shadow source recommendation."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:fire-circle"

    def __init__(self, entry: HydronicConfigEntry) -> None:
        """Bind the plant-level recommendation to the current runtime."""
        self._entry = entry
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_recommended_source"
        self._attr_name = "Recommended source"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        """Resolve the current runtime after reload."""
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        """Subscribe to atomic runtime evaluations."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        """Return the stable source ID or an explicit no-source sentinel."""
        recommendation = self._runtime.source_recommendation()
        return recommendation.source_id if recommendation and recommendation.source_id else "none"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose source names, eligibility, and the human-readable explanation."""
        recommendation = self._runtime.source_recommendation()
        if recommendation is None:
            return {"eligible_source_ids": [], "explanation": "No source configured."}
        source = self._runtime.plant.sources.get(recommendation.source_id or "")
        return {
            "source_name": source.name if source is not None else None,
            "eligible_source_ids": list(recommendation.eligible_source_ids),
            "explanation": recommendation.explanation,
        }


class SourceRecommendationExplanationSensor(SensorEntity):
    """Expose the explanation for the source recommendation."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:text-box-check-outline"

    def __init__(self, entry: HydronicConfigEntry) -> None:
        """Bind the explanation to the current runtime."""
        self._entry = entry
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_source_recommendation"
        self._attr_name = "Source recommendation"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        """Resolve the current runtime after reload."""
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        """Subscribe to atomic runtime evaluations."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        """Return the current explanation, bounded for Home Assistant state storage."""
        recommendation = self._runtime.source_recommendation()
        if recommendation is None:
            return "No source configured."
        return recommendation.explanation[:_MAX_STATE_LENGTH]

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the stable recommendation ID and eligible IDs."""
        recommendation = self._runtime.source_recommendation()
        if recommendation is None:
            return {"source_id": None, "eligible_source_ids": []}
        return {
            "source_id": recommendation.source_id,
            "eligible_source_ids": list(recommendation.eligible_source_ids),
        }


class ActiveSourceSensor(SensorEntity):
    """Expose the source owning the latest guarded heating demand."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:heat-pump"

    def __init__(self, entry: HydronicConfigEntry) -> None:
        self._entry = entry
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_active_source"
        self._attr_name = "Active source"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        return self._runtime.runtime_state.selected_source_id or "none"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        selection = self._runtime.source_selection_diagnostic()
        recommendation = self._runtime.source_recommendation()
        return {
            "recommended_source_id": recommendation.source_id if recommendation else None,
            "target_source_id": getattr(selection, "target_source_id", None),
            "phase": getattr(getattr(selection, "phase", None), "value", None),
            "dwell_remaining_seconds": getattr(selection, "dwell_remaining_seconds", 0.0),
            "hydraulically_safe": getattr(selection, "hydraulically_safe", False),
            "explanation": getattr(selection, "explanation", None),
        }


class SourceChangeoverSensor(SensorEntity):
    """Expose the deterministic source selection phase and guard."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:swap-horizontal-circle"

    def __init__(self, entry: HydronicConfigEntry) -> None:
        self._entry = entry
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_source_changeover"
        self._attr_name = "Source changeover"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        selection = self._runtime.source_selection_diagnostic()
        return getattr(getattr(selection, "phase", None), "value", "idle")

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        selection = self._runtime.source_selection_diagnostic()
        recommendation = self._runtime.source_recommendation()
        return {
            "active_source_id": getattr(selection, "active_source_id", None),
            "target_source_id": getattr(selection, "target_source_id", None),
            "recommended_source_id": recommendation.source_id if recommendation else None,
            "dwell_remaining_seconds": getattr(selection, "dwell_remaining_seconds", 0.0),
            "hydraulically_safe": getattr(selection, "hydraulically_safe", False),
            "dry_run": self._runtime.dry_run,
            "explanation": getattr(selection, "explanation", None),
        }


class SourceDwellSensor(SensorEntity):
    """Expose remaining source minimum dwell time from the atomic evaluation."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:timer-lock-outline"
    _attr_native_unit_of_measurement = "s"

    def __init__(self, entry: HydronicConfigEntry) -> None:
        self._entry = entry
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_source_dwell"
        self._attr_name = "Source dwell"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> float:
        selection = self._runtime.source_selection_diagnostic()
        return float(getattr(selection, "dwell_remaining_seconds", 0.0))


class SourceBlockedReasonSensor(SensorEntity):
    """Expose a bounded source-specific block explanation."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:source-branch-alert"

    def __init__(self, entry: HydronicConfigEntry, source_id: str, name: str) -> None:
        self._entry = entry
        self._source_id = source_id
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_{source_id}_blocked_reason"
        self._attr_name = f"{name} blocked reason"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        diagnostic = self._runtime.source_diagnostic(self._source_id)
        return str(getattr(diagnostic, "reason", "No source diagnostic."))[:_MAX_STATE_LENGTH]

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        diagnostic = self._runtime.source_diagnostic(self._source_id)
        return {
            "available": getattr(diagnostic, "available", None),
            "eligible": getattr(diagnostic, "eligible", False),
            "recommended": getattr(diagnostic, "recommended", False),
            "active": getattr(diagnostic, "active", False),
            "demand_requested": getattr(diagnostic, "demand_requested", False),
            "demand_permitted": getattr(diagnostic, "demand_permitted", False),
            "blocked": getattr(diagnostic, "blocked", False),
        }


class ZoneCoolingBlockedReasonSensor(SensorEntity):
    """Expose the cooling interlock explanation for one comfort zone."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:water-alert-outline"

    def __init__(self, entry: HydronicConfigEntry, zone_id: str, name: str) -> None:
        """Bind the cooling explanation to one comfort zone."""
        self._entry = entry
        self._zone_id = zone_id
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_{zone_id}_cooling_blocked_reason"
        self._attr_name = f"{name} cooling blocked reason"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        """Resolve the current runtime after a config-entry reload."""
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        """Subscribe after Home Assistant has registered the entity."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        """Return a stable sentinel when cooling is not blocked."""
        reason = self._runtime.cooling_zone_blocked_reason(self._zone_id) or "none"
        return reason[:_MAX_STATE_LENGTH]

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose structured cooling safety details."""
        return _cooling_diagnostic_attributes(self._runtime, self._zone_id)


class ZoneDewPointSensor(SensorEntity):
    """Expose the calculated zone dew point used by cooling safety."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: HydronicConfigEntry, zone_id: str, name: str) -> None:
        """Bind the dew-point diagnostic to one zone."""
        self._entry = entry
        self._zone_id = zone_id
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_{zone_id}_dew_point"
        self._attr_name = f"{name} cooling dew point"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        """Resolve the current runtime after a config-entry reload."""
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        """Subscribe after Home Assistant has registered the entity."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> float | None:
        """Return the latest calculated dew point."""
        return self._runtime.zone_dew_point(self._zone_id)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose cooling interlock diagnostics alongside the dew point."""
        return _cooling_diagnostic_attributes(self._runtime, self._zone_id)


class ZoneCondensationMarginSensor(SensorEntity):
    """Expose the lowest configured reference margin for a zone."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: HydronicConfigEntry, zone_id: str, name: str) -> None:
        """Bind the condensation margin diagnostic to one zone."""
        self._entry = entry
        self._zone_id = zone_id
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_{zone_id}_condensation_margin"
        self._attr_name = f"{name} cooling condensation margin"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        """Resolve the current runtime after a config-entry reload."""
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        """Subscribe after Home Assistant has registered the entity."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> float | None:
        """Return the lowest usable reference margin."""
        return self._runtime.zone_condensation_margin(self._zone_id)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the configured and calculated safety state."""
        return _cooling_diagnostic_attributes(self._runtime, self._zone_id)


class ActuatorFeedbackReasonSensor(SensorEntity):
    """Expose the structured feedback or manual-intervention explanation."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:information-outline"

    def __init__(self, entry: HydronicConfigEntry, actuator_id: str, name: str) -> None:
        """Bind one diagnostic state to an actuator."""
        self._entry = entry
        self._actuator_id = actuator_id
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_{actuator_id}_feedback_reason"
        self._attr_name = f"{name} feedback reason"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        """Resolve the current runtime after a config-entry reload."""
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        """Subscribe to atomic evaluations."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        """Return a stable bounded diagnostic explanation."""
        failure = self._runtime.actuator_execution_failure(self._actuator_id)
        if failure is not None:
            return f"{failure.kind.value}: {failure.explanation}"[:_MAX_STATE_LENGTH]
        diagnostic = self._runtime.actuator_diagnostic(self._actuator_id)
        reason = str(getattr(diagnostic, "reason", "No actuator feedback diagnostic."))
        return reason[:_MAX_STATE_LENGTH]

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose structured mismatch and fail-closed details."""
        diagnostic = self._runtime.actuator_diagnostic(self._actuator_id)
        failure = self._runtime.actuator_execution_failure(self._actuator_id)
        return {
            "status": getattr(getattr(diagnostic, "status", None), "value", None),
            "mismatch": getattr(diagnostic, "mismatch", False),
            "blocked": getattr(diagnostic, "blocked", False),
            "expected": getattr(diagnostic, "expected", None),
            "observed": getattr(diagnostic, "observed", None),
            "stale_feedback": list(getattr(diagnostic, "stale_feedback", ())),
            "execution_failure_kind": getattr(getattr(failure, "kind", None), "value", None),
            "execution_failure": getattr(failure, "explanation", None),
        }


def _zone_diagnostic_attributes(runtime: Any, zone_id: str) -> dict[str, object]:
    """Build common structured attributes for all zone explanation entities."""
    aggregation = runtime.zone_aggregation(zone_id)
    decision = runtime.zone_decision(zone_id)
    attributes: dict[str, object] = {
        "blocked": runtime.zone_is_blocked(zone_id),
    }
    if aggregation is not None:
        attributes.update(
            {
                "usable_sensor_ids": list(aggregation.usable_sensor_ids),
                "excluded_optional_sensor_ids": list(aggregation.excluded_optional_sensor_ids),
                "blocking_required_sensor_ids": list(aggregation.blocking_required_sensor_ids),
                "aggregation_explanation": aggregation.explanation,
            }
        )
    if decision is not None:
        status = getattr(decision.status, "value", decision.status)
        attributes.update(
            {
                "decision_status": status,
                "demand": decision.demand,
                "deadline": (
                    decision.deadline.isoformat() if decision.deadline is not None else None
                ),
            }
        )
    return attributes


class PlantModeSensor(SensorEntity):
    """Expose the mode currently permitted to use shared equipment."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:state-machine"

    def __init__(self, entry: HydronicConfigEntry) -> None:
        """Bind the active mode to the plant runtime."""
        self._entry = entry
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_operating_mode"
        self._attr_name = "Operating mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        """Resolve the current runtime after a config-entry reload."""
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        """Subscribe to atomic controller evaluations."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        """Return the active, safety-permitted operating mode."""
        return self._runtime.active_mode().value

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose requested mode and transition state without prose parsing."""
        state = self._runtime.runtime_state
        return {
            "requested_mode": state.requested_mode.value,
            "changeover_phase": state.changeover_phase.value,
            "changeover_target_mode": (
                state.changeover_target_mode.value
                if state.changeover_target_mode is not None
                else None
            ),
            "changeover_deadline": (
                state.changeover_deadline.isoformat()
                if state.changeover_deadline is not None
                else None
            ),
            "explanation": self._runtime.mode_explanation(),
        }


class ModeChangeoverExplanationSensor(SensorEntity):
    """Explain why a requested mode is active, idle, or locked."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:text-box-outline"

    def __init__(self, entry: HydronicConfigEntry) -> None:
        """Bind the explanation to the plant runtime."""
        self._entry = entry
        runtime = entry.runtime_data
        self._attr_unique_id = f"{runtime.plant_id}_mode_changeover_explanation"
        self._attr_name = "Mode changeover explanation"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.plant_id)}, name=runtime.name
        )

    @property
    def _runtime(self) -> HydronicRuntime:
        """Resolve the current runtime after a config-entry reload."""
        return cast(HydronicRuntime, self._entry.runtime_data)

    async def async_added_to_hass(self) -> None:
        """Subscribe to atomic controller evaluations."""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        """Return the bounded changeover explanation."""
        return self._runtime.mode_explanation()[:_MAX_STATE_LENGTH]

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the complete structured mode lockout."""
        state = self._runtime.runtime_state
        return {
            "requested_mode": state.requested_mode.value,
            "active_mode": state.plant_mode.value,
            "changeover_phase": state.changeover_phase.value,
            "changeover_target_mode": (
                state.changeover_target_mode.value
                if state.changeover_target_mode is not None
                else None
            ),
            "deadline": (
                state.changeover_deadline.isoformat()
                if state.changeover_deadline is not None
                else None
            ),
        }


async def async_setup_entry(
    hass: HomeAssistant, entry: HydronicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add read-only explanations for all configured zones."""
    runtime = entry.runtime_data
    parent_entities: list[SensorEntity] = [
        ControllerStatusSensor(entry),
        ReconciliationStatusSensor(entry),
        TopologyPreviewSensor(entry),
        PlantModeSensor(entry),
        ModeChangeoverExplanationSensor(entry),
        ActiveSourceSensor(entry),
        SourceChangeoverSensor(entry),
        SourceDwellSensor(entry),
    ]
    parent_entities.extend(
        [RecommendedSourceSensor(entry), SourceRecommendationExplanationSensor(entry)]
    )
    source_subentry_entities: dict[str, list[SensorEntity]] = {}
    for source in runtime.plant.sources.values():
        entity = SourceBlockedReasonSensor(entry, source.id, source.name)
        if subentry_id := runtime.source_subentry_ids.get(source.id):
            source_subentry_entities.setdefault(subentry_id, []).append(entity)
        else:
            parent_entities.append(entity)
    subentry_entities: dict[str, list[SensorEntity]] = {}
    for zone in runtime.plant.zones.values():
        entities = [
            ZoneExplanationSensor(entry, zone.id, zone.name),
            ZoneAggregateTemperatureSensor(entry, zone.id, zone.name),
            ZoneBlockedReasonSensor(entry, zone.id, zone.name),
            ZoneCoolingBlockedReasonSensor(entry, zone.id, zone.name),
            ZoneDewPointSensor(entry, zone.id, zone.name),
            ZoneCondensationMarginSensor(entry, zone.id, zone.name),
        ]
        if subentry_id := runtime.zone_subentry_ids.get(zone.id):
            subentry_entities.setdefault(subentry_id, []).extend(entities)
        else:
            parent_entities.extend(entities)
    for actuator_id, actuator in (*runtime.plant.valves.items(), *runtime.plant.pumps.items()):
        if not runtime.diagnostics_include_actuator_details:
            continue
        entity = ActuatorFeedbackReasonSensor(entry, actuator_id, actuator.name)
        if subentry_id := runtime.actuator_subentry_ids.get(actuator_id):
            subentry_entities.setdefault(subentry_id, []).append(entity)
        else:
            parent_entities.append(entity)
    async_add_entities(parent_entities)
    for subentry_id, entities in source_subentry_entities.items():
        subentry_entities.setdefault(subentry_id, []).extend(entities)
    for subentry_id, entities in subentry_entities.items():
        async_add_entities(entities, config_subentry_id=subentry_id)


def _cooling_diagnostic_attributes(runtime: HydronicRuntime, zone_id: str) -> dict[str, object]:
    """Build structured cooling diagnostics without parsing explanation text."""
    decision = runtime.cooling_zone_decision(zone_id)
    if decision is None:
        return {"cooling_blocked": False}
    return {
        "cooling_blocked": runtime.cooling_zone_is_blocked(zone_id),
        "cooling_demand": decision.demand,
        "cooling_decision_status": getattr(decision.status, "value", decision.status),
        "dew_point": decision.dew_point,
        "condensation_margin": decision.condensation_margin,
        "humidity_usable_sensor_ids": (
            list(decision.humidity_aggregation.usable_sensor_ids)
            if decision.humidity_aggregation is not None
            else []
        ),
        "humidity_blocking_required_sensor_ids": (
            list(decision.humidity_aggregation.blocking_required_sensor_ids)
            if decision.humidity_aggregation is not None
            else []
        ),
        "interlocks": [
            {
                "id": interlock.interlock_id,
                "status": interlock.status.value,
                "reason": interlock.reason,
            }
            for interlock in decision.interlocks
        ],
    }
