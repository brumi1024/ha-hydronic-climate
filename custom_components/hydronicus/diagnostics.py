"""Redacted, deterministic diagnostics for one Hydronicus config entry."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING

from .const import (
    MAX_RECONCILIATION_INTERVAL_SECONDS,
    MIN_RECONCILIATION_INTERVAL_SECONDS,
    RECONCILIATION_INTERVAL_SECONDS,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .runtime import HydronicRuntime


_REDACTED = "<redacted>"
_REDACTED_NAME = "<redacted name>"
_REDACTED_ENTITY = "<redacted entity>"
_REDACTED_VALUE = "<redacted value>"
_ENTITY_ID_PATTERN = re.compile(r"\b[a-z][a-z0-9_]*\.[a-z0-9_]+\b", re.IGNORECASE)
_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s,;]+", re.IGNORECASE)
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(?:api[_ -]?key|authorization|bearer|credential|password|private[_ -]?key|secret|token)"
    r"\s*[:=]\s*[^\s,;]+"
)
_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_])[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:\s*°?[A-Za-z%]+)?")
_SECRET_KEY_PATTERN = re.compile(
    r"(?i)(?:api[_ -]?key|authorization|bearer|credential|password|"
    r"private[_ -]?key|secret|token|url)"
)


class _References:
    """Stable opaque references for topology objects and bound entities."""

    def __init__(self, runtime: HydronicRuntime) -> None:
        self.plant = {runtime.plant.id: "plant-1"}
        self.zones = _references("zone", runtime.plant.zones)
        self.circuits = _references("circuit", runtime.plant.circuits)
        self.valves = _references("valve", runtime.plant.valves)
        self.pumps = _references("pump", runtime.plant.pumps)
        self.sources = _references("source", runtime.plant.sources)
        self.routes = _references("route", {route.id: route for route in runtime.plant.routes})
        self.actuators = _references(
            "actuator",
            {
                **runtime.plant.valves,
                **runtime.plant.pumps,
                **{
                    f"source:{source_id}": source
                    for source_id, source in runtime.plant.sources.items()
                },
            },
        )
        self.entities = _references("entity", _configured_entity_ids(runtime))
        self.sensitive_values = _sensitive_values(runtime)

    def ref(self, kind: str, value: str | None) -> str | None:
        """Return an opaque reference for a known value."""
        if value is None:
            return None
        table = self.plant if kind == "plant" else getattr(self, f"{kind}s", {})
        return table.get(value, _REDACTED)

    def entity(self, value: str | None) -> str | None:
        """Return an opaque entity reference without leaking the entity ID."""
        if value is None:
            return None
        return self.entities.get(value, _REDACTED_ENTITY)

    def text(self, value: object) -> str:
        """Redact names, bindings, URLs, credentials, UUIDs, and site values."""
        return _redact_text(str(value), self.sensitive_values)


def _references(prefix: str, values: Mapping[str, object] | Iterable[str]) -> dict[str, str]:
    """Create deterministic references from sorted stable IDs."""
    keys = values.keys() if isinstance(values, Mapping) else values
    return {key: f"{prefix}-{index}" for index, key in enumerate(sorted(map(str, keys)), 1)}


def _configured_entity_ids(runtime: HydronicRuntime) -> set[str]:
    """Collect all configured bindings so none can appear in diagnostics."""
    entity_ids: set[str] = set()
    for zone in runtime.plant.zones.values():
        entity_ids.update(sensor.entity_id for sensor in zone.sensor_metadata)
        entity_ids.update(sensor.entity_id for sensor in zone.humidity_sensor_metadata)
    for circuit in runtime.plant.circuits.values():
        entity_ids.update(
            entity_id
            for entity_id in (
                circuit.supply_temperature_sensor,
                circuit.surface_temperature_sensor,
            )
            if entity_id is not None
        )
    for valve in runtime.plant.valves.values():
        entity_ids.update(
            entity_id
            for entity_id in (
                valve.entity_id,
                valve.readiness_entity_id,
                valve.position_entity_id,
            )
            if entity_id is not None
        )
    for pump in runtime.plant.pumps.values():
        entity_ids.update(
            entity_id
            for entity_id in (
                pump.entity_id,
                pump.power_entity_id,
                pump.flow_entity_id,
                pump.fault_entity_id,
            )
            if entity_id is not None
        )
    for source in runtime.plant.sources.values():
        entity_ids.update(
            entity_id
            for entity_id in (
                source.availability_entity_id,
                source.temperature_entity_id,
                source.demand_entity_id,
            )
            if entity_id is not None
        )
    return entity_ids


def _sensitive_values(runtime: HydronicRuntime) -> tuple[str, ...]:
    """Return configured strings that must never be copied into a snapshot."""
    values = {runtime.name, runtime.plant.id}
    values.update(runtime.plant.zones)
    values.update(zone.name for zone in runtime.plant.zones.values())
    values.update(runtime.plant.circuits)
    values.update(circuit.name for circuit in runtime.plant.circuits.values())
    values.update(runtime.plant.valves)
    values.update(valve.name for valve in runtime.plant.valves.values())
    values.update(runtime.plant.pumps)
    values.update(pump.name for pump in runtime.plant.pumps.values())
    values.update(runtime.plant.sources)
    values.update(source.name for source in runtime.plant.sources.values())
    values.update(_configured_entity_ids(runtime))
    return tuple(sorted((value for value in values if value), key=len, reverse=True))


def _redact_text(value: str, sensitive_values: Sequence[str]) -> str:
    """Remove sensitive content from human-readable controller text."""
    redacted = _URL_PATTERN.sub(_REDACTED, value)
    redacted = _CREDENTIAL_PATTERN.sub(_REDACTED, redacted)
    for sensitive in sensitive_values:
        redacted = redacted.replace(sensitive, _REDACTED)
    redacted = _UUID_PATTERN.sub(_REDACTED, redacted)
    redacted = _ENTITY_ID_PATTERN.sub(_REDACTED_ENTITY, redacted)
    return _NUMBER_PATTERN.sub(_REDACTED_VALUE, redacted)


def _safe_reason(references: _References, value: object | None) -> str | None:
    """Return a bounded redacted explanation, or no explanation when absent."""
    if value is None:
        return None
    return references.text(value)


def _safe_mapping(
    mapping: Mapping[str, object], references: Mapping[str, str], redactions: _References
) -> dict[str, str]:
    """Redact mapping keys that represent stable object IDs."""
    return {
        references.get(str(key), _REDACTED): redactions.text(value)
        for key, value in sorted(mapping.items(), key=lambda item: str(item[0]))
    }


def _configuration_shape(runtime: HydronicRuntime, references: _References) -> dict[str, object]:
    """Describe configuration shape without copying user configuration values."""
    entry = runtime._entry
    entry_data = getattr(entry, "data", {})
    topology_sections: list[str] = []
    if isinstance(entry_data, Mapping):
        raw_topology = entry_data.get("topology")
        if isinstance(raw_topology, Mapping):
            topology_sections = sorted(
                str(key) for key in raw_topology if not _SECRET_KEY_PATTERN.search(str(key))
            )
    return {
        "stored_field_names": sorted(
            str(key) for key in entry_data if not _SECRET_KEY_PATTERN.search(str(key))
        )
        if isinstance(entry_data, Mapping)
        else [],
        "topology_sections": topology_sections,
        "plant": {
            "reference": references.ref("plant", runtime.plant.id),
            "name": _REDACTED_NAME,
            "shadow_mode": runtime.shadow_mode,
            "diagnostics_include_actuator_details": runtime.diagnostics_include_actuator_details,
        },
        "counts": {
            "zones": len(runtime.plant.zones),
            "circuits": len(runtime.plant.circuits),
            "routes": len(runtime.plant.routes),
            "valves": len(runtime.plant.valves),
            "pumps": len(runtime.plant.pumps),
            "sources": len(runtime.plant.sources),
        },
    }


def _configuration_objects(runtime: HydronicRuntime, references: _References) -> dict[str, object]:
    """Return the non-secret configuration needed to understand the topology."""
    zones = []
    for zone_id, zone in sorted(runtime.plant.zones.items()):
        zones.append(
            {
                "reference": references.ref("zone", zone_id),
                "name": _REDACTED_NAME,
                "aggregation": zone.aggregation.value,
                "temperature_sensor_count": len(zone.sensor_metadata),
                "required_temperature_sensor_count": sum(
                    sensor.required for sensor in zone.sensor_metadata
                ),
                "optional_temperature_sensor_count": sum(
                    not sensor.required for sensor in zone.sensor_metadata
                ),
                "humidity_sensor_count": len(zone.humidity_sensor_metadata),
                "configured_presets": sorted(zone.preset_targets),
                "minimum_active_duration_configured": zone.minimum_active_duration_seconds > 0,
                "minimum_idle_duration_configured": zone.minimum_idle_duration_seconds > 0,
            }
        )
    circuits = []
    for circuit_id, circuit in sorted(runtime.plant.circuits.items()):
        circuits.append(
            {
                "reference": references.ref("circuit", circuit_id),
                "name": _REDACTED_NAME,
                "valve_references": [
                    references.ref("valve", valve_id) for valve_id in circuit.valve_ids
                ],
                "pump_reference": references.ref("pump", circuit.pump_id),
                "cooling_enabled": circuit.cooling_enabled,
                "supply_temperature_reference_configured": circuit.supply_temperature_sensor
                is not None,
                "surface_temperature_reference_configured": circuit.surface_temperature_sensor
                is not None,
            }
        )
    routes = [
        {
            "reference": references.ref("route", route.id),
            "zone_reference": references.ref("zone", route.zone_id),
            "circuit_reference": references.ref("circuit", route.circuit_id),
            "enabled": route.enabled,
        }
        for route in sorted(runtime.plant.routes, key=lambda item: item.id)
    ]
    return {
        "zones": zones,
        "circuits": circuits,
        "routes": routes,
        "actuators": {
            "valves": [
                {
                    "reference": references.ref("valve", actuator_id),
                    "kind": "valve",
                    "readiness_feedback_configured": valve.readiness_entity_id is not None,
                    "position_feedback_configured": valve.position_entity_id is not None,
                }
                for actuator_id, valve in sorted(runtime.plant.valves.items())
            ],
            "pumps": [
                {
                    "reference": references.ref("pump", actuator_id),
                    "kind": "pump",
                    "power_feedback_configured": pump.power_entity_id is not None,
                    "flow_feedback_configured": pump.flow_entity_id is not None,
                    "fault_feedback_configured": pump.fault_entity_id is not None,
                }
                for actuator_id, pump in sorted(runtime.plant.pumps.items())
            ],
        },
        "sources": [
            {
                "reference": references.ref("source", source_id),
                "kind": source.kind.value,
                "availability_configured": source.availability_entity_id is not None,
                "temperature_reference_configured": source.temperature_entity_id is not None,
                "demand_actuator_configured": source.demand_entity_id is not None,
                "source_shadow_mode": source.shadow_mode,
            }
            for source_id, source in sorted(runtime.plant.sources.items())
        ],
    }


def _topology(runtime: HydronicRuntime, references: _References) -> dict[str, object]:
    """Return compiled relationships and redacted compiler explanations."""
    warnings = [
        {
            "code": warning.code,
            "message": references.text(warning.message),
            "equipment_reference": references.ref(
                "valve" if warning.equipment_kind == "valve" else warning.equipment_kind,
                warning.affected_equipment_id,
            ),
            "circuit_references": [
                references.ref("circuit", circuit_id) for circuit_id in warning.circuit_ids
            ],
            "zone_references": [references.ref("zone", zone_id) for zone_id in warning.zone_ids],
        }
        for warning in sorted(
            runtime.plant.warnings,
            key=lambda item: (item.code, item.affected_equipment_id, item.circuit_ids),
        )
    ]
    return {
        "valid": True,
        "logic_summary": sorted(references.text(item) for item in runtime.plant.logic_summary),
        "warnings": warnings,
        "relationships": _configuration_objects(runtime, references),
    }


def _runtime_state(runtime: HydronicRuntime, references: _References) -> dict[str, object]:
    """Return current controller-owned state without exact site timestamps."""
    zones = [
        {
            "reference": references.ref("zone", zone_id),
            "demand": bool(runtime.runtime_state.zone_demands.get(zone_id, False)),
            "cooling_demand": bool(runtime.runtime_state.cooling_zone_demands.get(zone_id, False)),
            "transition_timestamp_present": (
                runtime.runtime_state.zone_runtime.get(zone_id) is not None
                and runtime.runtime_state.zone_runtime[zone_id].last_demand_transition_at
                is not None
            ),
        }
        for zone_id in sorted(runtime.plant.zones)
    ]
    valves = [
        {
            "reference": references.ref("valve", actuator_id),
            "state": value.state.value,
            "ready": value.is_ready,
            "transition_timestamp_present": value.changed_at is not None,
        }
        for actuator_id, value in sorted(runtime.runtime_state.valves.items())
    ]
    pumps = [
        {
            "reference": references.ref("pump", actuator_id),
            "state": value.state.value,
            "transition_timestamp_present": value.changed_at is not None,
        }
        for actuator_id, value in sorted(runtime.runtime_state.pumps.items())
    ]
    return {
        "plant_mode": runtime.runtime_state.plant_mode.value,
        "selected_source_reference": references.ref(
            "source", runtime.runtime_state.selected_source_id
        ),
        "safe_shutdown_phase": runtime.runtime_state.safe_shutdown_phase.value,
        "safe_shutdown_started": runtime.runtime_state.safe_shutdown_started_at is not None,
        "zones": zones,
        "valves": valves,
        "pumps": pumps,
    }


def _controller(runtime: HydronicRuntime, references: _References) -> dict[str, object]:
    """Return structured controller decisions with redacted explanations."""
    evaluation = runtime.evaluation
    if evaluation is None:
        return {
            "evaluated": False,
            "plant_mode": None,
            "zones": [],
            "circuits": {},
            "actuators": {},
        }

    zones = []
    for zone_id in sorted(runtime.plant.zones):
        decision = evaluation.diagnostics.zone_decisions.get(zone_id)
        cooling = evaluation.diagnostics.cooling_zone_decisions.get(zone_id)
        aggregation = decision.aggregation if decision is not None else None
        zones.append(
            {
                "reference": references.ref("zone", zone_id),
                "status": decision.status.value if decision is not None else None,
                "demand": decision.demand if decision is not None else False,
                "cooling_status": cooling.status.value if cooling is not None else None,
                "cooling_demand": cooling.demand if cooling is not None else False,
                "aggregate_value_present": (
                    aggregation is not None and aggregation.value is not None
                ),
                "usable_sensor_count": len(aggregation.usable_sensor_ids) if aggregation else 0,
                "excluded_optional_sensor_count": (
                    len(aggregation.excluded_optional_sensor_ids) if aggregation else 0
                ),
                "blocking_required_sensor_count": (
                    len(aggregation.blocking_required_sensor_ids) if aggregation else 0
                ),
                "reason": _safe_reason(
                    references, decision.explanation if decision is not None else None
                ),
                "deadline_present": decision is not None and decision.deadline is not None,
            }
        )

    source = evaluation.diagnostics.source_recommendation
    return {
        "evaluated": True,
        "plant_mode": evaluation.control_plan.plant_mode.value,
        "command_count": len(evaluation.control_plan.commands),
        "zone_reason_count": len(evaluation.diagnostics.zone_reasons),
        "zones": zones,
        "circuits": _safe_mapping(
            {
                circuit_id: reason
                for circuit_id, reason in evaluation.diagnostics.circuit_reasons.items()
            },
            references.circuits,
            references,
        ),
        "actuators": _safe_mapping(
            {
                actuator_id: reason
                for actuator_id, reason in evaluation.diagnostics.actuator_reasons.items()
            },
            references.actuators,
            references,
        ),
        "interlocks": [
            {
                "reference": references.text(interlock_id),
                "status": interlock.status.value,
                "reason": references.text(interlock.reason),
            }
            for interlock_id, interlock in sorted(evaluation.diagnostics.interlocks.items())
        ],
        "mode_conflict_codes": sorted(
            conflict.code for conflict in evaluation.diagnostics.mode_conflicts
        ),
        "source_recommendation": {
            "source_reference": references.ref("source", source.source_id) if source else None,
            "eligible_source_count": len(source.eligible_source_ids) if source else 0,
            "reason": _safe_reason(references, source.explanation if source else None),
        },
        "source_diagnostics": [
            {
                "reference": references.ref("source", source_id),
                "available": diagnostic.available,
                "eligible": diagnostic.eligible,
                "recommended": diagnostic.recommended,
                "active": diagnostic.active,
                "demand_requested": diagnostic.demand_requested,
                "demand_permitted": diagnostic.demand_permitted,
                "source_shadow_mode": diagnostic.shadow_mode,
                "blocked": diagnostic.blocked,
                "reason": _safe_reason(references, diagnostic.reason),
            }
            for source_id, diagnostic in sorted(evaluation.diagnostics.source_diagnostics.items())
        ],
        "source_selection": (
            {
                "phase": evaluation.diagnostics.source_selection.phase.value,
                "active_source_reference": references.ref(
                    "source", evaluation.diagnostics.source_selection.active_source_id
                ),
                "target_source_reference": references.ref(
                    "source", evaluation.diagnostics.source_selection.target_source_id
                ),
                "recommended_source_reference": references.ref(
                    "source", evaluation.diagnostics.source_selection.recommended_source_id
                ),
                "hydraulically_safe": evaluation.diagnostics.source_selection.hydraulically_safe,
                "dwell_remaining_seconds": (
                    evaluation.diagnostics.source_selection.dwell_remaining_seconds
                ),
                "reason": _safe_reason(
                    references, evaluation.diagnostics.source_selection.explanation
                ),
            }
            if evaluation.diagnostics.source_selection is not None
            else None
        ),
    }


def _execution(runtime: HydronicRuntime, references: _References) -> dict[str, object]:
    """Return bounded execution counters and failure categories."""
    report = runtime.last_execution
    if report is None:
        return {"present": False, "executed": 0, "suppressed": 0, "shadowed": 0, "failures": []}
    return {
        "present": True,
        "executed": len(report.executed),
        "suppressed": len(report.suppressed),
        "shadowed": len(report.shadowed),
        "failures": sorted(failure.kind.value for failure in report.failures),
        "failure_messages": [references.text(failure.explanation) for failure in report.failures],
    }


def _actuator_summary(runtime: HydronicRuntime, references: _References) -> dict[str, object]:
    """Return low-cardinality actuator telemetry and optional bounded details."""
    executor = runtime.executor
    summary: dict[str, object] = {
        "detail_enabled": runtime.diagnostics_include_actuator_details,
        "configured_count": len(executor.bindings),
        "observed_state_counts": {
            state.value: sum(value is state for value in executor.observed_states.values())
            for state in sorted(set(executor.observed_states.values()), key=lambda item: item.value)
        },
        "retained_request_count": len(executor.requested_states),
        "failure_count": len(executor.failure_states),
        "mismatch_count": len(runtime.evaluation.diagnostics.mismatches)
        if runtime.evaluation is not None
        else 0,
    }
    if not runtime.diagnostics_include_actuator_details:
        summary["details"] = []
        return summary
    summary["details"] = [
        {
            "reference": references.ref("actuator", actuator_id),
            "reconciliation_status": reconciliation.status.value,
            "desired_state": reconciliation.desired.value,
            "observed_state": reconciliation.observed.value,
            "retained_request": (
                reconciliation.retained.value if reconciliation.retained is not None else None
            ),
        }
        for actuator_id, reconciliation in sorted(executor.reconciliations.items())
    ]
    return summary


def _reconciliation(runtime: HydronicRuntime) -> dict[str, object]:
    """Return bounded operational telemetry suitable for Recorder review."""
    interval = min(
        max(float(RECONCILIATION_INTERVAL_SECONDS), MIN_RECONCILIATION_INTERVAL_SECONDS),
        MAX_RECONCILIATION_INTERVAL_SECONDS,
    )
    return {
        "interval_seconds": interval,
        "bounded": True,
        "last_status": runtime.last_reconciliation_status,
        "reconciliation_count": runtime.reconciliation_count,
        "changed_count": runtime.reconciliation_changed_count,
        "unchanged_count": runtime.reconciliation_unchanged_count,
        "last_changed_actuator_count": runtime.last_reconciliation_changed_actuator_count,
        "refresh_count": runtime.refresh_count,
        "evaluation_count": runtime.evaluation_count,
        "coalesced_refresh_count": runtime.coalesced_refresh_count,
    }


def _warnings(runtime: HydronicRuntime, references: _References) -> dict[str, object]:
    """Return topology and runtime warnings without copying failure text blindly."""
    topology = _topology(runtime, references)["warnings"]
    runtime_warnings = [
        {
            "code": "actuator_command_failure",
            "kind": failure.kind.value,
            "message": "An actuator command failed; inspect the redacted execution state.",
        }
        for failure in sorted(
            (
                failure
                for failure in runtime.executor.failure_states.values()
                if failure is not None
            ),
            key=lambda item: (item.kind.value, item.operation.actuator_id),
        )
    ]
    return {
        "topology_count": len(topology),
        "runtime_count": len(runtime_warnings),
        "topology": topology,
        "runtime": runtime_warnings,
    }


def build_diagnostics(runtime: HydronicRuntime) -> dict[str, object]:
    """Build one deterministic redacted snapshot for download or tests."""
    references = _References(runtime)
    return {
        "schema_version": 1,
        "integration": {"domain": "hydronicus", "diagnostics_redacted": True},
        "configuration_shape": _configuration_shape(runtime, references),
        "compiled_topology": _topology(runtime, references),
        "runtime_state": _runtime_state(runtime, references),
        "controller_decisions": _controller(runtime, references),
        "actuator_state": _actuator_summary(runtime, references),
        "execution": _execution(runtime, references),
        "warnings": _warnings(runtime, references),
        "reconciliation": _reconciliation(runtime),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry[HydronicRuntime]
) -> dict[str, object]:
    """Return diagnostics through Home Assistant's downloadable diagnostics hook."""
    del hass
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None:
        return {
            "schema_version": 1,
            "integration": {"domain": "hydronicus", "diagnostics_redacted": True},
            "runtime_state": {"available": False},
        }
    return build_diagnostics(runtime)


__all__ = ["async_get_config_entry_diagnostics", "build_diagnostics"]
