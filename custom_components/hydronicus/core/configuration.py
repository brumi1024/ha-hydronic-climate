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
    TemperatureSensorMetadata,
    Valve,
    Zone,
)


class StoredTopologyError(ValueError):
    """Raised when stored topology data cannot safely be reconstructed."""


_SENSOR_METADATA_KEY = "temperature_sensor_metadata"
_SENSOR_KEYS = frozenset(
    {
        "entity_id",
        "required",
        "weight",
        "calibration_offset",
        "max_age_seconds",
        "designated_reference",
    }
)
_PRESET_NAMES = frozenset({"comfort", "eco", "away"})
_LEGACY_MAX_AGE_SECONDS = 1800.0


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
    value = _required(mapping, key)
    if not isinstance(value, str) or not value:
        raise StoredTopologyError(f"Stored topology field {key!r} must be a non-empty string.")
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
    if len(value) != len(set(value)):
        raise StoredTopologyError(f"Stored topology field {key!r} must not contain duplicates.")
    if not require_uuid:
        return tuple(value)
    try:
        return tuple(str(UUID(item)) for item in value)
    except ValueError as error:
        raise StoredTopologyError(
            f"Stored topology field {key!r} must contain only UUIDs."
        ) from error


def _number(
    mapping: Mapping[str, Any],
    key: str,
    default: float,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> float:
    """Decode a finite numeric field with domain-safe bounds."""
    value = mapping.get(key, default)
    if isinstance(value, bool):
        raise StoredTopologyError(f"Stored topology field {key!r} must be numeric.")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise StoredTopologyError(f"Stored topology field {key!r} must be numeric.") from error
    if not isfinite(number):
        raise StoredTopologyError(f"Stored topology field {key!r} must be finite.")
    if positive and number <= 0:
        raise StoredTopologyError(f"Stored topology field {key!r} must be positive.")
    if non_negative and number < 0:
        raise StoredTopologyError(f"Stored topology field {key!r} must be non-negative.")
    return number


def _required_number(mapping: Mapping[str, Any], key: str, *, non_negative: bool = False) -> float:
    """Decode a required finite numeric field."""
    _required(mapping, key)
    return _number(mapping, key, 0.0, non_negative=non_negative)


def _temperature_sensor_weights(
    mapping: Mapping[str, Any], sensor_ids: tuple[str, ...]
) -> Mapping[str, float]:
    """Read optional positive per-sensor weights from the legacy map."""
    raw_weights = mapping.get("temperature_sensor_weights", {})
    if not isinstance(raw_weights, Mapping):
        raise StoredTopologyError(
            "Stored topology field 'temperature_sensor_weights' must be an object."
        )
    unknown = sorted(str(sensor_id) for sensor_id in raw_weights if sensor_id not in sensor_ids)
    if unknown:
        raise StoredTopologyError(
            "Stored topology temperature sensor weights reference unknown sensors: "
            + ", ".join(unknown)
            + "."
        )
    return {
        str(sensor_id): _number(
            {"weight": raw_weight},
            "weight",
            1.0,
            positive=True,
        )
        for sensor_id, raw_weight in raw_weights.items()
    }


def _raw_sensor_records(mapping: Mapping[str, Any]) -> tuple[Mapping[str, Any] | str, ...]:
    """Read canonical metadata or one of the legacy sensor representations."""
    if _SENSOR_METADATA_KEY in mapping:
        raw_metadata = mapping[_SENSOR_METADATA_KEY]
        if isinstance(raw_metadata, Mapping):
            records: list[Mapping[str, Any]] = []
            for entity_id, metadata in raw_metadata.items():
                if not isinstance(metadata, Mapping):
                    raise StoredTopologyError(
                        "Stored temperature sensor metadata values must be objects."
                    )
                records.append({"entity_id": entity_id, **metadata})
            return tuple(records)
        if not isinstance(raw_metadata, list) or not all(
            isinstance(item, Mapping) for item in raw_metadata
        ):
            raise StoredTopologyError(
                "Stored topology field 'temperature_sensor_metadata' must be a list of objects."
            )
        return tuple(raw_metadata)

    raw_sensors = mapping.get("temperature_sensors")
    if raw_sensors is not None:
        if not isinstance(raw_sensors, list) or not raw_sensors:
            raise StoredTopologyError(
                "Stored topology field 'temperature_sensors' must be a non-empty list."
            )
        if all(isinstance(item, Mapping) for item in raw_sensors):
            return tuple(raw_sensors)
        if not all(isinstance(item, str) and item for item in raw_sensors):
            raise StoredTopologyError(
                "Stored topology field 'temperature_sensors' must contain entity ids."
            )
        return tuple(raw_sensors)

    sensor = mapping.get("temperature_sensor")
    if not isinstance(sensor, str) or not sensor:
        raise StoredTopologyError(
            "Stored topology field 'temperature_sensor' must be a non-empty entity id."
        )
    return (sensor,)


def temperature_sensor_metadata_from_mapping(
    mapping: Mapping[str, Any],
) -> tuple[TemperatureSensorMetadata, ...]:
    """Decode canonical and legacy sensor configuration into immutable records."""
    raw_records = _raw_sensor_records(mapping)
    if _SENSOR_METADATA_KEY in mapping and "temperature_sensors" in mapping:
        raw_sensor_ids = mapping["temperature_sensors"]
        if not isinstance(raw_sensor_ids, list) or not all(
            isinstance(sensor_id, str) and sensor_id for sensor_id in raw_sensor_ids
        ):
            raise StoredTopologyError(
                "Stored topology field 'temperature_sensors' must contain entity ids."
            )
        metadata_ids = tuple(
            record.get("entity_id") for record in raw_records if isinstance(record, Mapping)
        )
        if tuple(raw_sensor_ids) != metadata_ids:
            raise StoredTopologyError(
                "Stored temperature sensor metadata must match temperature_sensors."
            )
    legacy_sensor_ids = tuple(item for item in raw_records if isinstance(item, str))
    if len(legacy_sensor_ids) != len(raw_records):
        legacy_sensor_ids = tuple(
            str(item["entity_id"])
            for item in raw_records
            if isinstance(item, Mapping) and isinstance(item.get("entity_id"), str)
        )
    weights = _temperature_sensor_weights(mapping, legacy_sensor_ids)
    records: list[TemperatureSensorMetadata] = []
    seen: set[str] = set()
    for index, raw_record in enumerate(raw_records):
        if isinstance(raw_record, str):
            entity_id = raw_record
            record: Mapping[str, Any] = {}
        else:
            unknown = set(raw_record) - _SENSOR_KEYS
            if unknown:
                raise StoredTopologyError(
                    "Stored temperature sensor metadata has unknown fields: "
                    + ", ".join(sorted(str(key) for key in unknown))
                    + "."
                )
            entity_id = raw_record.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id:
                raise StoredTopologyError(
                    f"Stored temperature sensor metadata entry {index} requires entity_id."
                )
            record = raw_record
        if entity_id in seen:
            raise StoredTopologyError(
                f"Stored temperature sensors must not contain duplicate entity id {entity_id!r}."
            )
        seen.add(entity_id)
        required = record.get("required", True)
        if not isinstance(required, bool):
            raise StoredTopologyError(
                f"Stored temperature sensor {entity_id!r} required must be a boolean."
            )
        designated_reference = record.get("designated_reference", False)
        if not isinstance(designated_reference, bool):
            raise StoredTopologyError(
                f"Stored temperature sensor {entity_id!r} designated_reference must be a boolean."
            )
        weight = (
            _number(record, "weight", weights.get(entity_id, 1.0), positive=True)
            if "weight" in record or entity_id not in weights
            else weights[entity_id]
        )
        records.append(
            TemperatureSensorMetadata(
                entity_id=entity_id,
                required=required,
                weight=weight,
                calibration_offset=_number(record, "calibration_offset", 0.0),
                max_age_seconds=_number(
                    record,
                    "max_age_seconds",
                    _LEGACY_MAX_AGE_SECONDS,
                    positive=True,
                ),
                designated_reference=designated_reference,
            )
        )
    return tuple(records)


def _temperature_sensors(mapping: Mapping[str, Any]) -> tuple[str, ...]:
    """Read sensor IDs while preserving milestone 1 entries."""
    return tuple(sensor.entity_id for sensor in temperature_sensor_metadata_from_mapping(mapping))


def _temperature_aggregation(mapping: Mapping[str, Any]) -> TemperatureAggregation:
    """Read a zone aggregation policy while defaulting legacy data to mean."""
    value = mapping.get("temperature_aggregation", TemperatureAggregation.MEAN.value)
    try:
        return TemperatureAggregation(str(value))
    except ValueError as error:
        raise StoredTopologyError(
            "Stored topology field 'temperature_aggregation' must be a supported policy."
        ) from error


def _preset_targets(mapping: Mapping[str, Any]) -> Mapping[str, float]:
    """Read finite comfort, eco, and away targets from persisted zone data."""
    raw_targets = mapping.get("preset_targets", {})
    if not isinstance(raw_targets, Mapping):
        raise StoredTopologyError("Stored topology field 'preset_targets' must be an object.")
    targets: dict[str, float] = {}
    for name, raw_target in raw_targets.items():
        if name not in _PRESET_NAMES:
            raise StoredTopologyError(f"Stored topology preset target {name!r} is not supported.")
        targets[str(name)] = _number({"target": raw_target}, "target", 0.0)
    for name in _PRESET_NAMES:
        if name in mapping:
            targets[name] = _number(mapping, name, 0.0)
    return targets


def _zone_timing(mapping: Mapping[str, Any]) -> tuple[float, float, float, float]:
    """Read heating hysteresis and minimum duration fields."""
    return (
        _number(mapping, "heating_start_delta", 0.3, non_negative=True),
        _number(mapping, "heating_stop_delta", 0.1, non_negative=True),
        _number(mapping, "minimum_active_duration_seconds", 0.0, non_negative=True),
        _number(mapping, "minimum_idle_duration_seconds", 0.0, non_negative=True),
    )


def _validate_reference_policy(
    mapping: Mapping[str, Any],
    aggregation: TemperatureAggregation,
    metadata: tuple[TemperatureSensorMetadata, ...],
) -> tuple[TemperatureSensorMetadata, ...]:
    """Validate designated-reference metadata and a legacy zone-level reference."""
    reference = mapping.get("designated_reference_sensor")
    if reference is None and isinstance(mapping.get("designated_reference"), str):
        reference = mapping["designated_reference"]
    if reference is not None:
        if not isinstance(reference, str) or reference not in {
            sensor.entity_id for sensor in metadata
        }:
            raise StoredTopologyError(
                "Stored designated reference must identify one configured temperature sensor."
            )
        metadata = tuple(
            TemperatureSensorMetadata(
                entity_id=sensor.entity_id,
                required=sensor.required,
                weight=sensor.weight,
                calibration_offset=sensor.calibration_offset,
                max_age_seconds=sensor.max_age_seconds,
                designated_reference=sensor.entity_id == reference,
            )
            for sensor in metadata
        )
    designated = [sensor for sensor in metadata if sensor.designated_reference]
    if len(designated) > 1:
        raise StoredTopologyError("Stored temperature sensors have multiple designated references.")
    if aggregation is TemperatureAggregation.DESIGNATED_REFERENCE and len(designated) != 1:
        raise StoredTopologyError(
            "Designated-reference aggregation requires exactly one designated sensor."
        )
    return metadata


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
        metadata = temperature_sensor_metadata_from_mapping(item)
        aggregation = _temperature_aggregation(item)
        metadata = _validate_reference_policy(item, aggregation, metadata)
        start_delta, stop_delta, active_duration, idle_duration = _zone_timing(item)
        zones.append(
            Zone(
                id=_id(item, "id", require_uuid=require_uuid),
                name=str(_required(item, "name")),
                target_temperature=_required_number(item, "target_temperature"),
                temperature_sensor_metadata=metadata,
                aggregation=aggregation,
                heating_start_delta=start_delta,
                heating_stop_delta=stop_delta,
                minimum_active_duration_seconds=active_duration,
                minimum_idle_duration_seconds=idle_duration,
                preset_targets=_preset_targets(item),
            )
        )
    if require_uuid:
        valves = tuple(
            Valve(
                id=_id(item, "id", require_uuid=True),
                name=str(_required(item, "name")),
                entity_id=str(_required(item, "entity_id")),
                opening_time_seconds=_number(item, "opening_time_seconds", 30.0, non_negative=True),
            )
            for item in raw_valves
        )
        pumps = tuple(
            Pump(
                id=_id(item, "id", require_uuid=True),
                name=str(_required(item, "name")),
                entity_id=str(_required(item, "entity_id")),
                overrun_seconds=_number(item, "overrun_seconds", 120.0, non_negative=True),
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
            valve_opening_time = _number(
                item, "valve_opening_time_seconds", 30.0, non_negative=True
            )
            pump_overrun = _number(item, "pump_overrun_seconds", 120.0, non_negative=True)
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
