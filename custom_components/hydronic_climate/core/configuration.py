"""Conversion between persisted Home Assistant entry data and the pure domain model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .model import Circuit, DeliveryRoute, PlantConfiguration, Zone


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


def plant_configuration_from_entry_data(data: Mapping[str, Any]) -> PlantConfiguration:
    """Build a generic plant configuration from one config entry's persisted data."""
    plant_id = str(_required(data, "plant_id"))
    raw_topology = data.get("topology", {})
    if not isinstance(raw_topology, Mapping):
        raise StoredTopologyError("Stored topology must be an object.")

    zones = tuple(
        Zone(
            id=str(_required(item, "id")),
            name=str(_required(item, "name")),
            target_temperature=float(_required(item, "target_temperature")),
            temperature_sensor=str(_required(item, "temperature_sensor")),
        )
        for item in _objects(raw_topology, "zones")
    )
    circuits = tuple(
        Circuit(
            id=str(_required(item, "id")),
            name=str(_required(item, "name")),
            valve_id=str(_required(item, "valve_id")),
            pump_id=str(_required(item, "pump_id")),
            valve_opening_time_seconds=float(item.get("valve_opening_time_seconds", 30.0)),
            pump_overrun_seconds=float(item.get("pump_overrun_seconds", 120.0)),
        )
        for item in _objects(raw_topology, "circuits")
    )
    routes = tuple(
        DeliveryRoute(
            id=str(_required(item, "id")),
            zone_id=str(_required(item, "zone_id")),
            circuit_id=str(_required(item, "circuit_id")),
            enabled=bool(item.get("enabled", True)),
        )
        for item in _objects(raw_topology, "routes")
    )
    return PlantConfiguration(id=plant_id, zones=zones, circuits=circuits, routes=routes)
