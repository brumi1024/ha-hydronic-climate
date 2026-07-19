"""Validation and deterministic compilation of an explicitly configured topology."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import isfinite

from .model import (
    MAX_ZONE_TARGET_TEMPERATURE,
    MIN_ZONE_TARGET_TEMPERATURE,
    Circuit,
    CompiledPlant,
    EquipmentKind,
    PlantConfiguration,
    Pump,
    Source,
    SourceKind,
    SourceSelectionActuator,
    TemperatureAggregation,
    TopologyWarning,
    Valve,
    Zone,
)


class TopologyValidationError(ValueError):
    """Raised when a topology cannot safely be evaluated."""


@dataclass(frozen=True, slots=True)
class _TopologyIndex:
    """Deterministically indexed topology objects after uniqueness validation."""

    zones: dict[str, Zone]
    valves: dict[str, Valve]
    pumps: dict[str, Pump]
    circuits: dict[str, Circuit]
    sources: dict[str, Source]


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


def _validate_feedback_binding(
    owner: str,
    field_name: str,
    entity_id: str | None,
    max_age_seconds: float,
) -> None:
    """Validate one optional feedback binding without making it mandatory."""
    if entity_id is not None and (not isinstance(entity_id, str) or not entity_id.strip()):
        raise TopologyValidationError(f"{owner} {field_name} entity must be a non-empty entity id.")
    if not _finite_positive(max_age_seconds):
        raise TopologyValidationError(
            f"{owner} {field_name} maximum age must be positive and finite."
        )


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
    if (
        not MIN_ZONE_TARGET_TEMPERATURE
        <= float(zone.target_temperature)
        <= (MAX_ZONE_TARGET_TEMPERATURE)
    ):
        raise TopologyValidationError(
            f"Zone {zone_id} target temperature must be between "
            f"{MIN_ZONE_TARGET_TEMPERATURE:g} and {MAX_ZONE_TARGET_TEMPERATURE:g} °C."
        )
    if not zone.temperature_sensors:
        raise TopologyValidationError(f"Zone {zone_id} requires at least one temperature sensor.")
    if not _finite_non_negative(zone.heating_start_delta) or not _finite_non_negative(
        zone.heating_stop_delta
    ):
        raise TopologyValidationError(
            f"Zone {zone_id} heating hysteresis deltas must be finite and non-negative."
        )
    if not _finite_non_negative(zone.cooling_start_delta) or not _finite_non_negative(
        zone.cooling_stop_delta
    ):
        raise TopologyValidationError(
            f"Zone {zone_id} cooling hysteresis deltas must be finite and non-negative."
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

    humidity_metadata = tuple(zone.humidity_sensor_metadata)
    humidity_ids = tuple(sensor.entity_id for sensor in humidity_metadata)
    if duplicates := _duplicates(humidity_ids):
        raise TopologyValidationError(
            f"Zone {zone_id} humidity sensors must not contain duplicates: "
            + ", ".join(sorted(duplicates))
            + "."
        )
    for sensor in humidity_metadata:
        if not isinstance(sensor.entity_id, str) or not sensor.entity_id.strip():
            raise TopologyValidationError(
                f"Zone {zone_id} humidity sensors must be non-empty entity ids."
            )
        if not isinstance(sensor.required, bool):
            raise TopologyValidationError(
                f"Zone {zone_id} humidity sensor {sensor.entity_id} required status "
                "must be boolean."
            )
        if not _finite_positive(sensor.weight):
            raise TopologyValidationError(
                f"Zone {zone_id} humidity sensor weights must be positive and finite."
            )
        if not isfinite(float(sensor.calibration_offset)):
            raise TopologyValidationError(
                f"Zone {zone_id} humidity sensor {sensor.entity_id} calibration must be finite."
            )
        if not _finite_positive(sensor.max_age_seconds):
            raise TopologyValidationError(
                f"Zone {zone_id} humidity sensor {sensor.entity_id} maximum age must be positive."
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


def _validate_source(source: Source) -> None:
    """Validate source configuration without observing runtime state."""
    if not isinstance(source.name, str) or not source.name.strip():
        raise TopologyValidationError(f"Source {source.id} requires a non-empty name.")
    if (
        not isinstance(source.priority, int)
        or isinstance(source.priority, bool)
        or source.priority < 0
    ):
        raise TopologyValidationError(
            f"Source {source.id} priority must be a non-negative integer."
        )
    if not isinstance(source.kind, SourceKind):
        raise TopologyValidationError(f"Source {source.id} type must be supported.")
    for field_name, entity_id in (
        ("availability", source.availability_entity_id),
        ("temperature", source.temperature_entity_id),
        ("demand", source.demand_entity_id),
    ):
        if entity_id is not None and (not isinstance(entity_id, str) or not entity_id.strip()):
            raise TopologyValidationError(
                f"Source {source.id} {field_name} entity must be a non-empty entity id."
            )
    if not _finite_positive(source.maximum_age_seconds):
        raise TopologyValidationError(
            f"Source {source.id} maximum temperature age must be positive and finite."
        )
    if not _finite_non_negative(source.hysteresis):
        raise TopologyValidationError(
            f"Source {source.id} hysteresis must be finite and non-negative."
        )
    if source.kind is SourceKind.TEMPERATURE_QUALIFIED_BUFFER:
        if source.temperature_entity_id is None:
            raise TopologyValidationError(
                f"Source {source.id} buffer requires a temperature entity."
            )
        if source.minimum_temperature is None or not isfinite(float(source.minimum_temperature)):
            raise TopologyValidationError(
                f"Source {source.id} buffer minimum temperature must be finite."
            )


def _validate_source_selector(selector: SourceSelectionActuator) -> None:
    """Validate generic selector timing and optional entity binding."""
    if not isinstance(selector.id, str) or not selector.id.strip():
        raise TopologyValidationError("Source selector requires a non-empty id.")
    if not isinstance(selector.name, str) or not selector.name.strip():
        raise TopologyValidationError(f"Source selector {selector.id} requires a non-empty name.")
    if selector.entity_id is not None and (
        not isinstance(selector.entity_id, str) or not selector.entity_id.strip()
    ):
        raise TopologyValidationError(
            f"Source selector {selector.id} entity must be a non-empty entity id."
        )
    if selector.entity_id is not None and selector.entity_id.partition(".")[0] != "select":
        raise TopologyValidationError(
            f"Source selector {selector.id} entity must belong to the select domain."
        )
    if not _finite_non_negative(selector.break_interval_seconds):
        raise TopologyValidationError(
            f"Source selector {selector.id} break interval must be finite and non-negative."
        )
    if not _finite_non_negative(selector.minimum_dwell_seconds):
        raise TopologyValidationError(
            f"Source selector {selector.id} minimum dwell must be finite and non-negative."
        )
    if not isinstance(selector.release_option, str) or not selector.release_option.strip():
        raise TopologyValidationError(
            f"Source selector {selector.id} release option must be non-empty."
        )
    if not isinstance(selector.shadow_only, bool):
        raise TopologyValidationError(f"Source selector {selector.id} shadow_only must be boolean.")


def _index_topology(configuration: PlantConfiguration) -> _TopologyIndex:
    """Validate stable object identities and return deterministic lookup maps."""
    collections = (
        ("zone", configuration.zones),
        ("valve", configuration.valves),
        ("pump", configuration.pumps),
        ("circuit", configuration.circuits),
        ("source", configuration.sources),
    )
    for object_name, objects in collections:
        ids = [item.id for item in objects]
        if not ids:
            continue
        if not all(isinstance(value, str) and value for value in ids):
            raise TopologyValidationError(f"Every {object_name} requires a non-empty id.")
        if duplicates := _duplicates(ids):
            raise TopologyValidationError(
                f"Duplicate {object_name} ids: {', '.join(sorted(duplicates))}."
            )

    route_ids = [route.id for route in configuration.routes]
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

    return _TopologyIndex(
        zones={zone.id: zone for zone in sorted(configuration.zones, key=lambda item: item.id)},
        valves={
            valve.id: valve for valve in sorted(configuration.valves, key=lambda item: item.id)
        },
        pumps={pump.id: pump for pump in sorted(configuration.pumps, key=lambda item: item.id)},
        circuits={
            circuit.id: circuit
            for circuit in sorted(configuration.circuits, key=lambda item: item.id)
        },
        sources={
            source.id: source
            for source in sorted(configuration.sources, key=lambda item: item.id)
        },
    )


def compile_topology(configuration: PlantConfiguration) -> CompiledPlant:
    """Validate and compile a plant configuration without reading runtime state."""
    if not configuration.id:
        raise TopologyValidationError("Plant id must not be empty.")

    index = _index_topology(configuration)
    zone_ids = list(index.zones)
    valve_ids = list(index.valves)
    pump_ids = list(index.pumps)
    circuit_ids = list(index.circuits)
    source_ids = list(index.sources)

    for zone in configuration.zones:
        _validate_zone(zone)
    for valve in configuration.valves:
        if not _finite_non_negative(valve.opening_time_seconds):
            raise TopologyValidationError(
                f"Valve {valve.id} opening time must be finite and non-negative."
            )
        if valve.readiness_entity_id is not None and (
            not isinstance(valve.readiness_entity_id, str)
            or not valve.readiness_entity_id.strip()
        ):
            raise TopologyValidationError(
                f"Valve {valve.id} readiness feedback entity must be non-empty."
            )
        _validate_feedback_binding(
            f"Valve {valve.id}",
            "position feedback",
            valve.position_entity_id,
            valve.position_max_age_seconds,
        )
    for pump in configuration.pumps:
        if not _finite_non_negative(pump.overrun_seconds):
            raise TopologyValidationError(
                f"Pump {pump.id} overrun must be finite and non-negative."
            )
        for field_name, entity_id, max_age in (
            ("power feedback", pump.power_entity_id, pump.power_max_age_seconds),
            ("flow feedback", pump.flow_entity_id, pump.flow_max_age_seconds),
            ("fault feedback", pump.fault_entity_id, pump.fault_max_age_seconds),
        ):
            _validate_feedback_binding(f"Pump {pump.id}", field_name, entity_id, max_age)
    for source in configuration.sources:
        _validate_source(source)
    source_selector = configuration.source_selector
    if source_selector is not None:
        _validate_source_selector(source_selector)

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
    source_demand_entity_ids = [
        source.demand_entity_id
        for source in configuration.sources
        if source.demand_entity_id is not None
    ]
    if source_selector is not None and source_selector.entity_id in source_demand_entity_ids:
        raise TopologyValidationError(
            f"Source selector entity binding is already used by another actuator: "
            f"{source_selector.entity_id}."
        )
    object_ids = {*zone_ids, *valve_ids, *pump_ids, *circuit_ids, *source_ids}
    source_actuator_ids = {f"source:{source_id}" for source_id in source_ids}
    if source_selector is not None and source_selector.id in object_ids | source_actuator_ids:
        raise TopologyValidationError(
            f"Source selector id {source_selector.id!r} is already used by another object."
        )

    zones = index.zones
    valves = index.valves
    pumps = index.pumps
    circuits = index.circuits
    sources = index.sources
    referenced_valves: set[str] = set()
    referenced_pumps: set[str] = set()
    for circuit in configuration.circuits:
        if not circuit.valve_ids:
            raise TopologyValidationError(f"Circuit {circuit.id} requires at least one valve.")
        if not isinstance(circuit.cooling_enabled, bool):
            raise TopologyValidationError(f"Circuit {circuit.id} cooling enabled must be boolean.")
        if circuit.cooling_enabled and not (
            circuit.supply_temperature_sensor or circuit.surface_temperature_sensor
        ):
            raise TopologyValidationError(
                f"Cooling circuit {circuit.id} requires a supply or surface temperature reference."
            )
        for reference_name, reference in (
            ("supply", circuit.supply_temperature_sensor),
            ("surface", circuit.surface_temperature_sensor),
        ):
            if reference is not None and (
                not isinstance(reference, str) or not reference.strip()
            ):
                raise TopologyValidationError(
                    f"Circuit {circuit.id} {reference_name} temperature reference must be "
                    "a non-empty entity id."
                )
        if not _finite_non_negative(circuit.condensation_margin):
            raise TopologyValidationError(
                f"Circuit {circuit.id} condensation margin must be finite and non-negative."
            )
        if not _finite_positive(circuit.supply_temperature_max_age_seconds):
            raise TopologyValidationError(
                f"Circuit {circuit.id} supply reference maximum age must be positive and finite."
            )
        if not _finite_positive(circuit.surface_temperature_max_age_seconds):
            raise TopologyValidationError(
                f"Circuit {circuit.id} surface reference maximum age must be positive and finite."
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
        if not isinstance(route.enabled, bool):
            raise TopologyValidationError(f"Route {route.id} enabled must be boolean.")
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

    for circuit in configuration.circuits:
        if not circuit.cooling_enabled:
            continue
        served_zone_ids = {
            route.zone_id
            for route in configuration.routes
            if route.enabled and route.circuit_id == circuit.id
        }
        for zone_id in sorted(served_zone_ids):
            if not zones[zone_id].humidity_sensor_metadata:
                raise TopologyValidationError(
                    f"Cooling circuit {circuit.id} requires humidity observations for "
                    f"zone {zone_id}."
                )

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

    for source in configuration.sources:
        if source.kind is SourceKind.TEMPERATURE_QUALIFIED_BUFFER:
            summary.append(
                f"Source {source.name} is eligible when its buffer reaches "
                f"{source.minimum_temperature:g} °C."
            )
        else:
            summary.append(f"Source {source.name} is eligible when available.")

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
                equipment_kind=EquipmentKind.VALVE,
                equipment_id=valve_id,
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
            affected_zones = tuple(
                sorted(
                    {
                        route.zone_id
                        for route in enabled_routes
                        if route.circuit_id in shared_circuits
                    }
                )
            )
            warnings.append(
                TopologyWarning(
                    code="shared_pump_limits_independent_control",
                    message=(
                        f"Pump {pump.name} is shared by circuits "
                        f"{', '.join(circuits[circuit_id].name for circuit_id in shared_circuits)}"
                        "; "
                        "separate climate entities cannot independently control heating and "
                        "cooling "
                        "through the same pump."
                    ),
                    valve_id=pump_id,
                    circuit_ids=shared_circuits,
                    zone_ids=affected_zones,
                    equipment_kind=EquipmentKind.PUMP,
                    equipment_id=pump_id,
                )
            )

    cooling_circuit_ids = tuple(
        sorted(circuit_id for circuit_id, circuit in circuits.items() if circuit.cooling_enabled)
    )
    if cooling_circuit_ids and sources:
        affected_circuit_ids = tuple(sorted(circuits))
        affected_zone_ids = tuple(
            sorted(
                {
                    route.zone_id
                    for route in enabled_routes
                    if route.circuit_id in affected_circuit_ids
                }
            )
        )
        for source_id, source in sources.items():
            warnings.append(
                TopologyWarning(
                    code="shared_source_limits_independent_control",
                    message=(
                        f"Source {source.name} is shared by the plant; separate climate entities "
                        "cannot independently change heating and cooling source mode."
                    ),
                    valve_id=source_id,
                    circuit_ids=affected_circuit_ids,
                    zone_ids=affected_zone_ids,
                    equipment_kind=EquipmentKind.SOURCE,
                    equipment_id=source_id,
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
        sources=sources,
        warnings=tuple(warnings),
        source_selector=source_selector,
    )
