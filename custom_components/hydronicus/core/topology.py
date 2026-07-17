"""Validation and deterministic compilation of an explicitly configured topology."""

from __future__ import annotations

from collections.abc import Iterable
from math import isfinite

from .model import (
    MAX_ZONE_TARGET_TEMPERATURE,
    MIN_ZONE_TARGET_TEMPERATURE,
    CompiledPlant,
    PlantConfiguration,
    TemperatureAggregation,
    TopologyWarning,
    Zone,
)


class TopologyValidationError(ValueError):
    """Raised when a topology cannot safely be evaluated."""


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _finite_non_negative(value: object) -> bool:
    return isinstance(value, (int, float)) and isfinite(float(value)) and float(value) >= 0


def _finite_positive(value: object) -> bool:
    return isinstance(value, (int, float)) and isfinite(float(value)) and float(value) > 0


def _validate_zone(zone: Zone) -> None:
    """Validate all pure controller inputs owned by one comfort zone."""
    # The object is a Zone at the call site.  Keeping validation here based on
    # its public contract makes malformed restored/configured values fail closed.
    zone_id = zone.id
    if not isinstance(zone.aggregation, TemperatureAggregation):
        raise TopologyValidationError(
            f"Zone {zone_id} temperature aggregation must be a supported policy."
        )
    if not isinstance(zone.target_temperature, (int, float)) or not isfinite(
        float(zone.target_temperature)
    ):
        raise TopologyValidationError(f"Zone {zone_id} target temperature must be finite.")
    if not zone.temperature_sensors:
        raise TopologyValidationError(f"Zone {zone_id} requires at least one temperature sensor.")
    if not _finite_non_negative(zone.heating_start_delta) or not _finite_non_negative(
        zone.heating_stop_delta
    ):
        raise TopologyValidationError(
            f"Zone {zone_id} heating hysteresis deltas must be finite and non-negative."
        )
    if not _finite_non_negative(zone.minimum_active_duration_seconds):
        raise TopologyValidationError(
            f"Zone {zone_id} minimum active duration must be finite and non-negative."
        )
    if not _finite_non_negative(zone.minimum_idle_duration_seconds):
        raise TopologyValidationError(
            f"Zone {zone_id} minimum idle duration must be finite and non-negative."
        )

    metadata = tuple(zone.sensor_metadata)
    if not all(
        isinstance(sensor.entity_id, str) and sensor.entity_id.strip() for sensor in metadata
    ):
        raise TopologyValidationError(
            f"Zone {zone_id} temperature sensors must be non-empty entity ids."
        )
    sensor_ids = tuple(sensor.entity_id for sensor in metadata)
    if duplicates := _duplicates(sensor_ids):
        raise TopologyValidationError(
            f"Zone {zone_id} temperature sensors must not contain duplicates: "
            + ", ".join(sorted(duplicates))
            + "."
        )
    for sensor in metadata:
        if not isinstance(sensor.required, bool):
            raise TopologyValidationError(
                f"Zone {zone_id} sensor {sensor.entity_id} required status must be boolean."
            )
        if not _finite_positive(sensor.weight):
            raise TopologyValidationError(
                f"Zone {zone_id} temperature sensor weights must be positive and finite."
            )
        if not isinstance(sensor.calibration_offset, (int, float)) or not isfinite(
            float(sensor.calibration_offset)
        ):
            raise TopologyValidationError(
                f"Zone {zone_id} sensor {sensor.entity_id} calibration offset must be finite."
            )
        if not _finite_positive(sensor.max_age_seconds):
            raise TopologyValidationError(
                f"Zone {zone_id} sensor {sensor.entity_id} maximum age must be positive and finite."
            )
        if not isinstance(sensor.designated_reference, bool):
            raise TopologyValidationError(
                f"Zone {zone_id} sensor {sensor.entity_id} reference status must be boolean."
            )

    reference_ids = tuple(
        sorted(sensor.entity_id for sensor in metadata if sensor.designated_reference)
    )
    if len(reference_ids) > 1:
        raise TopologyValidationError(
            f"Zone {zone_id} has multiple designated reference sensors: "
            + ", ".join(reference_ids)
            + "."
        )
    if zone.aggregation is TemperatureAggregation.DESIGNATED_REFERENCE and len(reference_ids) != 1:
        raise TopologyValidationError(
            f"Zone {zone_id} designated reference aggregation requires exactly one "
            "designated reference sensor."
        )

    allowed_presets = {"comfort", "eco", "away"}
    for preset, target in zone.preset_targets.items():
        if preset not in allowed_presets:
            raise TopologyValidationError(f"Zone {zone_id} has unsupported preset target {preset}.")
        if not isinstance(target, (int, float)) or not isfinite(float(target)):
            raise TopologyValidationError(f"Zone {zone_id} preset target {preset} must be finite.")
        if not MIN_ZONE_TARGET_TEMPERATURE <= float(target) <= MAX_ZONE_TARGET_TEMPERATURE:
            raise TopologyValidationError(
                f"Zone {zone_id} preset target {preset} must be between "
                f"{MIN_ZONE_TARGET_TEMPERATURE:g} and {MAX_ZONE_TARGET_TEMPERATURE:g} °C."
            )


def compile_topology(configuration: PlantConfiguration) -> CompiledPlant:
    """Validate and compile a plant configuration without reading runtime state."""
    if not configuration.id:
        raise TopologyValidationError("Plant id must not be empty.")

    zone_ids = [zone.id for zone in configuration.zones]
    valve_ids = [valve.id for valve in configuration.valves]
    pump_ids = [pump.id for pump in configuration.pumps]
    circuit_ids = [circuit.id for circuit in configuration.circuits]
    route_ids = [route.id for route in configuration.routes]
    for object_name, ids in (
        ("zone", zone_ids),
        ("valve", valve_ids),
        ("pump", pump_ids),
        ("circuit", circuit_ids),
    ):
        if not ids:
            continue
        if not all(isinstance(value, str) and value for value in ids):
            raise TopologyValidationError(f"Every {object_name} requires a non-empty id.")
        if duplicates := _duplicates(ids):
            raise TopologyValidationError(
                f"Duplicate {object_name} ids: {', '.join(sorted(duplicates))}."
            )
    if not all(isinstance(value, str) and value for value in route_ids):
        raise TopologyValidationError("Every route requires a non-empty id.")
    if duplicates := _duplicates(route_ids):
        raise TopologyValidationError(f"Duplicate route ids: {', '.join(sorted(duplicates))}.")
    route_relationships = [
        f"{route.zone_id} -> {route.circuit_id}" for route in configuration.routes
    ]
    if duplicates := _duplicates(route_relationships):
        raise TopologyValidationError(
            "Duplicate delivery routes: " + ", ".join(sorted(duplicates)) + "."
        )

    for zone in configuration.zones:
        _validate_zone(zone)
    for valve in configuration.valves:
        if not _finite_non_negative(valve.opening_time_seconds):
            raise TopologyValidationError(
                f"Valve {valve.id} opening time must be finite and non-negative."
            )
    for pump in configuration.pumps:
        if not _finite_non_negative(pump.overrun_seconds):
            raise TopologyValidationError(
                f"Pump {pump.id} overrun must be finite and non-negative."
            )

    actuator_entity_ids = [
        *(valve.entity_id for valve in configuration.valves),
        *(pump.entity_id for pump in configuration.pumps),
    ]
    if not all(
        isinstance(entity_id, str) and entity_id.strip() for entity_id in actuator_entity_ids
    ):
        raise TopologyValidationError("Every actuator requires a non-empty entity binding.")
    if duplicates := _duplicates(actuator_entity_ids):
        raise TopologyValidationError(
            f"Duplicate actuator entity bindings: {', '.join(sorted(duplicates))}."
        )

    zones = {zone.id: zone for zone in sorted(configuration.zones, key=lambda zone: zone.id)}
    valves = {valve.id: valve for valve in sorted(configuration.valves, key=lambda valve: valve.id)}
    pumps = {pump.id: pump for pump in sorted(configuration.pumps, key=lambda pump: pump.id)}
    circuits = {
        circuit.id: circuit
        for circuit in sorted(configuration.circuits, key=lambda circuit: circuit.id)
    }
    referenced_valves: set[str] = set()
    referenced_pumps: set[str] = set()
    for circuit in configuration.circuits:
        if not circuit.valve_ids:
            raise TopologyValidationError(f"Circuit {circuit.id} requires at least one valve.")
        unknown_valves = sorted(set(circuit.valve_ids) - set(valves))
        if unknown_valves:
            raise TopologyValidationError(
                f"Circuit {circuit.id} references unknown valves: {', '.join(unknown_valves)}."
            )
        if circuit.pump_id not in pumps:
            raise TopologyValidationError(
                f"Circuit {circuit.id} references unknown pump {circuit.pump_id}."
            )
        referenced_valves.update(circuit.valve_ids)
        referenced_pumps.add(circuit.pump_id)

    referenced_zones: set[str] = set()
    referenced_circuits: set[str] = set()
    for route in configuration.routes:
        if route.zone_id not in zones:
            raise TopologyValidationError(
                f"Route {route.id} references unknown zone {route.zone_id}."
            )
        if route.circuit_id not in circuits:
            raise TopologyValidationError(
                f"Route {route.id} references unknown circuit {route.circuit_id}."
            )
        if not route.enabled:
            continue
        referenced_zones.add(route.zone_id)
        referenced_circuits.add(route.circuit_id)

    orphaned_zones = sorted(set(zones) - referenced_zones)
    orphaned_valves = sorted(set(valves) - referenced_valves)
    orphaned_pumps = sorted(set(pumps) - referenced_pumps)
    orphaned_circuits = sorted(set(circuits) - referenced_circuits)
    if orphaned_zones or orphaned_valves or orphaned_pumps or orphaned_circuits:
        details: list[str] = []
        if orphaned_zones:
            details.append(f"orphaned zones: {', '.join(orphaned_zones)}")
        if orphaned_valves:
            details.append(f"orphaned valves: {', '.join(orphaned_valves)}")
        if orphaned_pumps:
            details.append(f"orphaned pumps: {', '.join(orphaned_pumps)}")
        if orphaned_circuits:
            details.append(f"orphaned circuits: {', '.join(orphaned_circuits)}")
        raise TopologyValidationError("; ".join(details) + ".")

    enabled_routes = tuple(
        sorted(
            (route for route in configuration.routes if route.enabled),
            key=lambda route: (route.zone_id, route.circuit_id, route.id),
        )
    )
    summary_routes = tuple(route for route in configuration.routes if route.enabled)
    summary = [
        (
            f"Circuit {circuit.name} opens valves "
            f"{', '.join(valves[valve_id].name for valve_id in circuit.valve_ids)} "
            f"before requesting pump {pumps[circuit.pump_id].name}."
        )
        for circuit in configuration.circuits
    ]
    for zone in configuration.zones:
        route_circuits = [
            circuits[route.circuit_id].name for route in summary_routes if route.zone_id == zone.id
        ]
        noun = "circuit" if len(route_circuits) == 1 else "circuits"
        summary.append(f"Zone {zone.name} can request {noun} {', '.join(route_circuits)}.")

    warnings: list[TopologyWarning] = []
    for valve_id, valve in valves.items():
        shared_circuits = tuple(
            sorted(circuit.id for circuit in circuits.values() if valve_id in circuit.valve_ids)
        )
        if len(shared_circuits) <= 1:
            continue
        summary_circuits = tuple(
            circuit.id for circuit in configuration.circuits if valve_id in circuit.valve_ids
        )
        summary_circuit_names = ", ".join(
            circuits[circuit_id].name for circuit_id in summary_circuits
        )
        warning_circuit_names = ", ".join(
            circuits[circuit_id].name for circuit_id in shared_circuits
        )
        summary.append(f"Valve {valve.name} is shared by circuits {summary_circuit_names}.")
        affected_zones = tuple(
            sorted(
                {route.zone_id for route in enabled_routes if route.circuit_id in shared_circuits}
            )
        )
        warnings.append(
            TopologyWarning(
                code="shared_valve_limits_independent_control",
                message=(
                    f"Valve {valve.name} is shared by circuits {warning_circuit_names}; "
                    "separate climate entities cannot independently control circuits "
                    "coupled by the same physical valve."
                ),
                valve_id=valve_id,
                circuit_ids=shared_circuits,
                zone_ids=affected_zones,
            )
        )

    for pump_id, pump in pumps.items():
        shared_circuits = tuple(
            sorted(circuit.id for circuit in circuits.values() if pump_id == circuit.pump_id)
        )
        if len(shared_circuits) > 1:
            summary_circuits = tuple(
                circuit.id for circuit in configuration.circuits if pump_id == circuit.pump_id
            )
            summary.append(
                f"Pump {pump.name} is shared by circuits "
                + ", ".join(circuits[circuit_id].name for circuit_id in summary_circuits)
                + "."
            )

    return CompiledPlant(
        id=configuration.id,
        zones=zones,
        valves=valves,
        pumps=pumps,
        circuits=circuits,
        routes=enabled_routes,
        logic_summary=tuple(summary),
        warnings=tuple(warnings),
    )
