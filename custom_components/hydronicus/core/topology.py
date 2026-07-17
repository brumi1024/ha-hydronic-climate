"""Validation and compilation of an explicitly configured plant topology."""

from __future__ import annotations

from collections.abc import Iterable
from math import isfinite

from .model import CompiledPlant, PlantConfiguration, TemperatureAggregation


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
        if not all(ids):
            raise TopologyValidationError(f"Every {object_name} requires a non-empty id.")
        if duplicates := _duplicates(ids):
            raise TopologyValidationError(
                f"Duplicate {object_name} ids: {', '.join(sorted(duplicates))}."
            )
    if not all(route_ids):
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
        if not isinstance(zone.aggregation, TemperatureAggregation):
            raise TopologyValidationError(
                f"Zone {zone.id} temperature aggregation must be a supported policy."
            )
        if not isfinite(zone.target_temperature):
            raise TopologyValidationError(
                f"Zone {zone.id} target temperature must be finite."
            )
        if not zone.temperature_sensors:
            raise TopologyValidationError(
                f"Zone {zone.id} requires at least one temperature sensor."
            )
        if not all(
            isinstance(sensor_id, str) and sensor_id.strip()
            for sensor_id in zone.temperature_sensors
        ):
            raise TopologyValidationError(
                f"Zone {zone.id} temperature sensors must be non-empty entity ids."
            )
        if duplicates := _duplicates(zone.temperature_sensors):
            raise TopologyValidationError(
                f"Zone {zone.id} temperature sensors must not contain duplicates: "
                + ", ".join(sorted(duplicates))
                + "."
            )
        unknown_weight_sensors = sorted(
            set(zone.temperature_sensor_weights) - set(zone.temperature_sensors)
        )
        if unknown_weight_sensors:
            raise TopologyValidationError(
                f"Zone {zone.id} temperature sensor weights reference unknown sensors: "
                + ", ".join(unknown_weight_sensors)
                + "."
            )
        if any(
            not isinstance(weight, (int, float))
            or not isfinite(float(weight))
            or float(weight) <= 0
            for weight in zone.temperature_sensor_weights.values()
        ):
            raise TopologyValidationError(
                f"Zone {zone.id} temperature sensor weights must be positive and finite."
            )
    for valve in configuration.valves:
        if not isfinite(valve.opening_time_seconds) or valve.opening_time_seconds < 0:
            raise TopologyValidationError(
                f"Valve {valve.id} opening time must be finite and non-negative."
            )
    for pump in configuration.pumps:
        if not isfinite(pump.overrun_seconds) or pump.overrun_seconds < 0:
            raise TopologyValidationError(
                f"Pump {pump.id} overrun must be finite and non-negative."
            )

    actuator_entity_ids = [
        *(valve.entity_id for valve in configuration.valves),
        *(pump.entity_id for pump in configuration.pumps),
    ]
    if not all(actuator_entity_ids):
        raise TopologyValidationError("Every actuator requires a non-empty entity binding.")
    if duplicates := _duplicates(actuator_entity_ids):
        raise TopologyValidationError(
            f"Duplicate actuator entity bindings: {', '.join(sorted(duplicates))}."
        )

    zones = {zone.id: zone for zone in configuration.zones}
    valves = {valve.id: valve for valve in configuration.valves}
    pumps = {pump.id: pump for pump in configuration.pumps}
    circuits = {circuit.id: circuit for circuit in configuration.circuits}
    referenced_valves: set[str] = set()
    referenced_pumps: set[str] = set()
    for circuit in configuration.circuits:
        if not circuit.valve_ids:
            raise TopologyValidationError(
                f"Circuit {circuit.id} requires at least one valve."
            )
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
            circuits[route.circuit_id].name
            for route in configuration.routes
            if route.enabled and route.zone_id == zone.id
        ]
        noun = "circuit" if len(route_circuits) == 1 else "circuits"
        summary.append(
            f"Zone {zone.name} can request {noun} {', '.join(route_circuits)}."
        )
    for valve in configuration.valves:
        shared_circuits = [
            circuit.name
            for circuit in configuration.circuits
            if valve.id in circuit.valve_ids
        ]
        if len(shared_circuits) > 1:
            summary.append(
                f"Valve {valve.name} is shared by circuits "
                f"{', '.join(shared_circuits)}."
            )
    for pump in configuration.pumps:
        shared_circuits = [
            circuit.name
            for circuit in configuration.circuits
            if pump.id == circuit.pump_id
        ]
        if len(shared_circuits) > 1:
            summary.append(
                f"Pump {pump.name} is shared by circuits "
                f"{', '.join(shared_circuits)}."
            )
    enabled_routes = tuple(
        sorted(
            (route for route in configuration.routes if route.enabled),
            key=lambda route: (route.zone_id, route.circuit_id, route.id),
        )
    )
    return CompiledPlant(
        id=configuration.id,
        zones=zones,
        valves=valves,
        pumps=pumps,
        circuits=circuits,
        routes=enabled_routes,
        logic_summary=tuple(summary),
    )
