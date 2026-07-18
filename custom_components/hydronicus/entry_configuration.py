"""Compose config-entry and subentry data into one effective plant topology."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from .const import (
    ACTUATOR_KIND_VALVE,
    CONF_ACTUATOR_KIND,
    CONF_CIRCUIT_IDS,
    CONF_ENTITY_ID,
    CONF_NAME,
    CONF_OPENING_TIME,
    CONF_PUMP_ID,
    CONF_ROUTES,
    CONF_SOURCES,
    CONF_TARGET_TEMPERATURE,
    CONF_TOPOLOGY,
    CONF_VALVE_IDS,
    CONF_VALVE_READINESS_ENTITY,
    CONF_ZONE_IDS,
    CONF_ZONES,
    SUBENTRY_TYPE_ACTUATOR,
    SUBENTRY_TYPE_CIRCUIT,
    SUBENTRY_TYPE_SOURCE,
    SUBENTRY_TYPE_ZONE,
)
from .core.configuration import (
    StoredTopologyError,
    plant_configuration_from_entry_data,
)
from .core.model import Circuit, DeliveryRoute, PlantConfiguration, Valve


@dataclass(frozen=True, slots=True)
class EffectivePlantConfiguration:
    """A fully composed plant and its entity-owning subentries."""

    configuration: PlantConfiguration
    actuator_subentry_ids: Mapping[str, str]
    zone_subentry_ids: Mapping[str, str]
    source_subentry_ids: Mapping[str, str]


def zone_target_temperature_update(
    entry: Any, zone_id: str, temperature: float
) -> tuple[str | None, Mapping[str, Any]]:
    """Build a persisted target-temperature update for a configured zone."""
    effective = effective_plant_configuration(entry)
    if subentry_id := effective.zone_subentry_ids.get(zone_id):
        subentry = entry.subentries[subentry_id]
        return subentry_id, {
            **subentry.data,
            CONF_TARGET_TEMPERATURE: temperature,
        }

    topology = entry.data.get(CONF_TOPOLOGY, {})
    if not isinstance(topology, Mapping):
        raise StoredTopologyError("Stored topology must be an object.")
    raw_zones = topology.get(CONF_ZONES, [])
    if not isinstance(raw_zones, list):
        raise StoredTopologyError("Stored topology zones must be a list.")
    zones: list[Mapping[str, Any]] = []
    found = False
    for raw_zone in raw_zones:
        if not isinstance(raw_zone, Mapping):
            raise StoredTopologyError("Stored topology zones must be objects.")
        if str(raw_zone.get("id")) == zone_id:
            zones.append({**raw_zone, CONF_TARGET_TEMPERATURE: temperature})
            found = True
        else:
            zones.append(raw_zone)
    if not found:
        raise StoredTopologyError(f"Unknown zone {zone_id}.")
    return None, {
        **entry.data,
        CONF_TOPOLOGY: {**topology, CONF_ZONES: zones},
    }


def _required(data: Mapping[str, Any], key: str) -> Any:
    try:
        return data[key]
    except KeyError as error:
        raise StoredTopologyError(f"Subentry is missing required field {key!r}.") from error


def _uuid(data: Mapping[str, Any], key: str) -> str:
    value = str(_required(data, key))
    try:
        return str(UUID(value))
    except ValueError as error:
        raise StoredTopologyError(f"Subentry field {key!r} must be a UUID.") from error


def _circuit_ids(data: Mapping[str, Any]) -> tuple[str, ...]:
    value = _required(data, CONF_CIRCUIT_IDS)
    if not isinstance(value, list) or not value:
        raise StoredTopologyError("Actuator subentry requires at least one circuit id.")
    try:
        return tuple(str(UUID(str(item))) for item in value)
    except ValueError as error:
        raise StoredTopologyError("Actuator subentry circuit ids must be UUIDs.") from error


def _uuid_list(data: Mapping[str, Any], key: str, owner: str) -> tuple[str, ...]:
    value = _required(data, key)
    if not isinstance(value, list) or not value:
        raise StoredTopologyError(f"{owner} requires at least one {key} value.")
    try:
        result = tuple(str(UUID(str(item))) for item in value)
    except ValueError as error:
        raise StoredTopologyError(f"{owner} field {key!r} must contain UUIDs.") from error
    if len(result) != len(set(result)):
        raise StoredTopologyError(f"{owner} field {key!r} must not contain duplicates.")
    return result


def _route_enabled(data: Mapping[str, Any]) -> bool:
    """Read an optional route flag without coercing malformed persisted values."""
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise StoredTopologyError("Route enabled must be a boolean.")
    return enabled


def _cooling_enabled(data: Mapping[str, Any]) -> bool:
    """Read explicit circuit mode compatibility without truthy coercion."""
    enabled = data.get("cooling_enabled", False)
    if not isinstance(enabled, bool):
        raise StoredTopologyError("Circuit cooling_enabled must be a boolean.")
    return enabled


def _circuit_routes(
    data: Mapping[str, Any], circuit_id: str, zone_ids: tuple[str, ...]
) -> tuple[DeliveryRoute, ...]:
    raw_routes = _required(data, CONF_ROUTES)
    if not isinstance(raw_routes, list):
        raise StoredTopologyError("Circuit subentry routes must be a list.")
    routes: list[DeliveryRoute] = []
    for raw_route in raw_routes:
        if not isinstance(raw_route, Mapping):
            raise StoredTopologyError("Circuit subentry routes must be objects.")
        routes.append(
            DeliveryRoute(
                id=_uuid(raw_route, "id"),
                zone_id=_uuid(raw_route, "zone_id"),
                circuit_id=circuit_id,
                enabled=_route_enabled(raw_route),
            )
        )
    if len(routes) != len(zone_ids) or {route.zone_id for route in routes} != set(zone_ids):
        raise StoredTopologyError("Circuit subentry routes must match its selected zone ids.")
    return tuple(routes)


def _zone_routes(
    data: Mapping[str, Any], zone_id: str, circuit_ids: tuple[str, ...]
) -> tuple[DeliveryRoute, ...]:
    raw_routes = _required(data, CONF_ROUTES)
    if not isinstance(raw_routes, list):
        raise StoredTopologyError("Zone subentry routes must be a list.")
    routes: list[DeliveryRoute] = []
    for raw_route in raw_routes:
        if not isinstance(raw_route, Mapping):
            raise StoredTopologyError("Zone subentry routes must be objects.")
        routes.append(
            DeliveryRoute(
                id=_uuid(raw_route, "id"),
                zone_id=zone_id,
                circuit_id=_uuid(raw_route, "circuit_id"),
                enabled=_route_enabled(raw_route),
            )
        )
    if len(routes) != len(circuit_ids) or {route.circuit_id for route in routes} != set(
        circuit_ids
    ):
        raise StoredTopologyError("Zone subentry routes must match its selected circuit ids.")
    return tuple(routes)


def effective_plant_configuration(
    entry: Any,
    *,
    proposed_actuators: Sequence[Mapping[str, Any]] = (),
    proposed_circuits: Sequence[Mapping[str, Any]] = (),
    proposed_zones: Sequence[Mapping[str, Any]] = (),
    proposed_sources: Sequence[Mapping[str, Any]] = (),
    excluded_subentry_id: str | None = None,
) -> EffectivePlantConfiguration:
    """Build one atomic topology from embedded data and dynamic subentries."""
    base = plant_configuration_from_entry_data(entry.data)
    actuator_records: list[tuple[str | None, Mapping[str, Any]]] = []
    circuit_records: list[tuple[str | None, Mapping[str, Any]]] = []
    zone_records: list[tuple[str | None, Mapping[str, Any]]] = []
    source_records: list[tuple[str | None, Mapping[str, Any]]] = []
    for subentry in getattr(entry, "subentries", {}).values():
        if (
            subentry.subentry_type == SUBENTRY_TYPE_ACTUATOR
            and subentry.subentry_id != excluded_subentry_id
        ):
            actuator_records.append((subentry.subentry_id, subentry.data))
        elif (
            subentry.subentry_type == SUBENTRY_TYPE_CIRCUIT
            and subentry.subentry_id != excluded_subentry_id
        ):
            circuit_records.append((subentry.subentry_id, subentry.data))
        elif (
            subentry.subentry_type == SUBENTRY_TYPE_ZONE
            and subentry.subentry_id != excluded_subentry_id
        ):
            zone_records.append((subentry.subentry_id, subentry.data))
        elif (
            subentry.subentry_type == SUBENTRY_TYPE_SOURCE
            and subentry.subentry_id != excluded_subentry_id
        ):
            source_records.append((subentry.subentry_id, subentry.data))
    actuator_records.extend((None, data) for data in proposed_actuators)
    circuit_records.extend((None, data) for data in proposed_circuits)
    zone_records.extend((None, data) for data in proposed_zones)
    source_records.extend((None, data) for data in proposed_sources)

    zones = list(base.zones)
    valves = list(base.valves)
    circuits = list(base.circuits)
    routes = list(base.routes)
    sources = list(base.sources)
    parent_circuit_ids = {circuit.id for circuit in circuits}
    parent_zone_ids = {zone.id for zone in base.zones}
    parent_valve_ids = {valve.id for valve in base.valves}
    parent_pump_ids = {pump.id for pump in base.pumps}
    # Keep this tracer deletion-safe by referencing only parent-owned objects.
    # Cross-subentry dependencies need an explicit cascade or repair policy first.
    for _subentry_id, data in circuit_records:
        circuit_id = _uuid(data, "id")
        zone_ids = _uuid_list(data, CONF_ZONE_IDS, "Circuit subentry")
        valve_ids = _uuid_list(data, CONF_VALVE_IDS, "Circuit subentry")
        pump_id = _uuid(data, CONF_PUMP_ID)
        unknown_zone_ids = sorted(set(zone_ids) - parent_zone_ids)
        unknown_valve_ids = sorted(set(valve_ids) - parent_valve_ids)
        if unknown_zone_ids:
            raise StoredTopologyError(
                "Circuit subentry references unknown zones: " + ", ".join(unknown_zone_ids) + "."
            )
        if unknown_valve_ids:
            raise StoredTopologyError(
                "Circuit subentry references unknown valves: " + ", ".join(unknown_valve_ids) + "."
            )
        if pump_id not in parent_pump_ids:
            raise StoredTopologyError(f"Circuit subentry references unknown pump {pump_id}.")
        circuits.append(
            Circuit(
                id=circuit_id,
                name=str(_required(data, CONF_NAME)),
                valve_ids=valve_ids,
                pump_id=pump_id,
                cooling_enabled=_cooling_enabled(data),
                supply_temperature_sensor=data.get("supply_temperature_sensor"),
                surface_temperature_sensor=data.get("surface_temperature_sensor"),
                condensation_margin=float(data.get("condensation_margin", 2.0)),
                supply_temperature_max_age_seconds=float(
                    data.get("supply_temperature_max_age_seconds", 1800.0)
                ),
                surface_temperature_max_age_seconds=float(
                    data.get("surface_temperature_max_age_seconds", 1800.0)
                ),
            )
        )
        routes.extend(_circuit_routes(data, circuit_id, zone_ids))

    zone_subentry_ids: dict[str, str] = {}
    for subentry_id, data in zone_records:
        zone_id = _uuid(data, "id")
        selected_circuit_ids = _uuid_list(data, CONF_CIRCUIT_IDS, "Zone subentry")
        unknown_circuit_ids = sorted(set(selected_circuit_ids) - parent_circuit_ids)
        if unknown_circuit_ids:
            raise StoredTopologyError(
                "Zone subentry references unknown circuits: " + ", ".join(unknown_circuit_ids) + "."
            )
        try:
            target_temperature = float(_required(data, CONF_TARGET_TEMPERATURE))
        except (TypeError, ValueError) as error:
            raise StoredTopologyError(
                "Zone subentry target temperature must be numeric."
            ) from error
        zone = plant_configuration_from_entry_data(
            {
                "plant_id": "00000000-0000-4000-8000-000000000000",
                "topology": {
                    "zones": [
                        {
                            **dict(data),
                            "id": zone_id,
                            CONF_NAME: str(_required(data, CONF_NAME)),
                            CONF_TARGET_TEMPERATURE: target_temperature,
                        }
                    ],
                    "circuits": [],
                    "routes": [],
                },
            }
        ).zones[0]
        zones.append(zone)
        routes.extend(_zone_routes(data, zone_id, selected_circuit_ids))
        if subentry_id is not None:
            zone_subentry_ids[zone_id] = subentry_id

    actuator_subentry_ids: dict[str, str] = {}
    for subentry_id, data in actuator_records:
        kind = str(_required(data, CONF_ACTUATOR_KIND))
        if kind != ACTUATOR_KIND_VALVE:
            raise StoredTopologyError(f"Unsupported actuator subentry kind {kind!r}.")
        actuator_id = _uuid(data, "id")
        selected_circuit_ids = _circuit_ids(data)
        unknown_circuit_ids = sorted(set(selected_circuit_ids) - parent_circuit_ids)
        if unknown_circuit_ids:
            raise StoredTopologyError(
                "Actuator subentry references unknown circuits: "
                + ", ".join(unknown_circuit_ids)
                + "."
            )
        try:
            opening_time_seconds = float(_required(data, CONF_OPENING_TIME))
        except (TypeError, ValueError) as error:
            raise StoredTopologyError("Actuator subentry opening time must be numeric.") from error
        valves.append(
            Valve(
                id=actuator_id,
                name=str(_required(data, CONF_NAME)),
                entity_id=str(_required(data, CONF_ENTITY_ID)),
                opening_time_seconds=opening_time_seconds,
                readiness_entity_id=data.get(CONF_VALVE_READINESS_ENTITY),
            )
        )
        selected = set(selected_circuit_ids)
        circuits = [
            replace(circuit, valve_ids=(*circuit.valve_ids, actuator_id))
            if circuit.id in selected
            else circuit
            for circuit in circuits
        ]
        if subentry_id is not None:
            actuator_subentry_ids[actuator_id] = subentry_id

    source_subentry_ids: dict[str, str] = {}
    for subentry_id, data in source_records:
        source = plant_configuration_from_entry_data(
            {
                "plant_id": "00000000-0000-4000-8000-000000000000",
                "topology": {CONF_SOURCES: [dict(data)]},
            }
        ).sources[0]
        sources.append(source)
        if subentry_id is not None:
            source_subentry_ids[source.id] = subentry_id

    return EffectivePlantConfiguration(
        configuration=replace(
            base,
            zones=tuple(zones),
            valves=tuple(valves),
            circuits=tuple(circuits),
            routes=tuple(routes),
            sources=tuple(sources),
        ),
        actuator_subentry_ids=actuator_subentry_ids,
        zone_subentry_ids=zone_subentry_ids,
        source_subentry_ids=source_subentry_ids,
    )
