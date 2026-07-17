"""Conversion between persisted Home Assistant entry data and the pure domain model."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any
from uuid import UUID

from .model import (
    Circuit,
    DeliveryRoute,
    PlantConfiguration,
    Pump,
    TemperatureAggregation,
    Valve,
    Zone,
)


class StoredTopologyError(ValueError):
    """Raised when stored topology data cannot safely be reconstructed."""


def _required(mapping: Mapping[str, Any], key: str) -> Any:
    """Read a required stored field with an actionable error message."""
    try:
        return mapping[key]
    except KeyError as error:
        raise StoredTopologyError(f"Stored topology is missing required field {key!r}.") from error


def _objects(topology: Mapping[str, Any], key: str) -> tuple[Mapping[str, Any], ...]:
    """Read a list of stored objects without trusting config-entry data blindly."""
    raw_objects = topology.get(key, [])
    if not isinstance(raw_objects, list) or not all(
        isinstance(item, Mapping) for item in raw_objects
    ):
        raise StoredTopologyError(f"Stored topology field {key!r} must be a list of objects.")
    return tuple(raw_objects)


def _id(mapping: Mapping[str, Any], key: str, *, require_uuid: bool = False) -> str:
    """Read a relationship id and optionally enforce the new UUID format."""
    value = str(_required(mapping, key))
    if not require_uuid:
        return value
    try:
        return str(UUID(value))
    except ValueError as error:
        raise StoredTopologyError(f"Stored topology field {key!r} must be a UUID.") from error


def _string_list(
    mapping: Mapping[str, Any], key: str, *, require_uuid: bool = False
) -> tuple[str, ...]:
    """Read a required list of non-empty relationship ids."""
    value = _required(mapping, key)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise StoredTopologyError(f"Stored topology field {key!r} must be a non-empty list of ids.")
    if not require_uuid:
        return tuple(value)
    try:
        return tuple(str(UUID(item)) for item in value)
    except ValueError as error:
        raise StoredTopologyError(
            f"Stored topology field {key!r} must contain only UUIDs."
        ) from error


def _temperature_sensors(mapping: Mapping[str, Any]) -> tuple[str, ...]:
    """Read new sensor lists while preserving milestone 1 entries."""
    if "temperature_sensors" in mapping:
        return _string_list(mapping, "temperature_sensors")
    sensor = str(_required(mapping, "temperature_sensor"))
    if not sensor:
        raise StoredTopologyError(
            "Stored topology field 'temperature_sensor' must be a non-empty entity id."
        )
    return (sensor,)


def _temperature_aggregation(mapping: Mapping[str, Any]) -> TemperatureAggregation:
    """Read a zone aggregation policy while defaulting legacy data to mean."""
    value = mapping.get("temperature_aggregation", TemperatureAggregation.MEAN.value)
    try:
        return TemperatureAggregation(str(value))
    except ValueError as error:
        raise StoredTopologyError(
            "Stored topology field 'temperature_aggregation' must be a supported policy."
        ) from error


def _temperature_sensor_weights(
    mapping: Mapping[str, Any], sensor_ids: tuple[str, ...]
) -> Mapping[str, float]:
    """Read optional positive per-sensor weights for weighted means."""
    raw_weights = mapping.get("temperature_sensor_weights", {})
    if not isinstance(raw_weights, Mapping):
        raise StoredTopologyError(
            "Stored topology field 'temperature_sensor_weights' must be an object."
        )
    unknown = sorted(set(raw_weights) - set(sensor_ids))
    if unknown:
        raise StoredTopologyError(
            "Stored topology temperature sensor weights reference unknown sensors: "
            + ", ".join(str(sensor_id) for sensor_id in unknown)
            + "."
        )
    weights: dict[str, float] = {}
    for sensor_id, raw_weight in raw_weights.items():
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError) as error:
            raise StoredTopologyError(
                f"Stored temperature sensor weight for {sensor_id!r} must be numeric."
            ) from error
        if not isfinite(weight) or weight <= 0:
            raise StoredTopologyError(
                f"Stored temperature sensor weight for {sensor_id!r} must be positive."
            )
        weights[str(sensor_id)] = weight
    return weights


def _route_enabled(mapping: Mapping[str, Any]) -> bool:
    """Read the optional route flag without coercing malformed stored data."""
    enabled = mapping.get("enabled", True)
    if not isinstance(enabled, bool):
        raise StoredTopologyError("Stored route enabled must be a boolean.")
    return enabled


def plant_configuration_from_entry_data(data: Mapping[str, Any]) -> PlantConfiguration:
    """Build a generic plant configuration from one config entry's persisted data."""
    raw_topology = data.get("topology", {})
    if not isinstance(raw_topology, Mapping):
        raise StoredTopologyError("Stored topology must be an object.")

    raw_circuits = _objects(raw_topology, "circuits")
    raw_valves = _objects(raw_topology, "valves")
    raw_pumps = _objects(raw_topology, "pumps")
    require_uuid = bool(raw_valves or raw_pumps)
    plant_id = _id(data, "plant_id", require_uuid=require_uuid)
    zones = []
    for item in _objects(raw_topology, "zones"):
        sensor_ids = _temperature_sensors(item)
        zones.append(
            Zone(
                id=_id(item, "id", require_uuid=require_uuid),
                name=str(_required(item, "name")),
                target_temperature=float(_required(item, "target_temperature")),
                temperature_sensors=sensor_ids,
                aggregation=_temperature_aggregation(item),
                temperature_sensor_weights=_temperature_sensor_weights(item, sensor_ids),
            )
        )
    if require_uuid:
        valves = tuple(
            Valve(
                id=_id(item, "id", require_uuid=True),
                name=str(_required(item, "name")),
                entity_id=str(_required(item, "entity_id")),
                opening_time_seconds=float(item.get("opening_time_seconds", 30.0)),
            )
            for item in raw_valves
        )
        pumps = tuple(
            Pump(
                id=_id(item, "id", require_uuid=True),
                name=str(_required(item, "name")),
                entity_id=str(_required(item, "entity_id")),
                overrun_seconds=float(item.get("overrun_seconds", 120.0)),
            )
            for item in raw_pumps
        )
        circuits = [
            Circuit(
                id=_id(item, "id", require_uuid=True),
                name=str(_required(item, "name")),
                valve_ids=_string_list(item, "valve_ids", require_uuid=True),
                pump_id=_id(item, "pump_id", require_uuid=True),
            )
            for item in raw_circuits
        ]
    else:
        valve_data: dict[str, Valve] = {}
        pump_data: dict[str, Pump] = {}
        circuits = []
        for item in raw_circuits:
            name = str(_required(item, "name"))
            valve_id = str(_required(item, "valve_id"))
            pump_id = str(_required(item, "pump_id"))
            valve_opening_time = float(item.get("valve_opening_time_seconds", 30.0))
            pump_overrun = float(item.get("pump_overrun_seconds", 120.0))
            prior_valve = valve_data.get(valve_id)
            valve_data[valve_id] = Valve(
                id=valve_id,
                name=prior_valve.name if prior_valve is not None else f"{name} valve",
                entity_id=valve_id,
                opening_time_seconds=(
                    max(prior_valve.opening_time_seconds, valve_opening_time)
                    if prior_valve is not None
                    else valve_opening_time
                ),
            )
            prior_pump = pump_data.get(pump_id)
            pump_data[pump_id] = Pump(
                id=pump_id,
                name=prior_pump.name if prior_pump is not None else f"{name} pump",
                entity_id=pump_id,
                overrun_seconds=(
                    max(prior_pump.overrun_seconds, pump_overrun)
                    if prior_pump is not None
                    else pump_overrun
                ),
            )
            circuits.append(
                Circuit(
                    id=str(_required(item, "id")),
                    name=name,
                    valve_ids=(valve_id,),
                    pump_id=pump_id,
                )
            )
        valves = tuple(valve_data.values())
        pumps = tuple(pump_data.values())
    routes = tuple(
        DeliveryRoute(
            id=_id(item, "id", require_uuid=require_uuid),
            zone_id=_id(item, "zone_id", require_uuid=require_uuid),
            circuit_id=_id(item, "circuit_id", require_uuid=require_uuid),
            enabled=_route_enabled(item),
        )
        for item in _objects(raw_topology, "routes")
    )
    return PlantConfiguration(
        id=plant_id,
        zones=tuple(zones),
        valves=valves,
        pumps=pumps,
        circuits=tuple(circuits),
        routes=routes,
    )
