"""Build the deterministic large synthetic Plant benchmark topology."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from custom_components.hydronicus.core.model import PlantSnapshot, TemperatureObservation

BENCHMARK_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "benchmarks" / "large-synthetic-plant.json"
)


def _uuid(offset: int, index: int) -> str:
    """Return a deterministic UUID in a namespace reserved for one object kind."""
    return f"00000000-0000-4000-8000-{offset + index:012d}"


def load_benchmark_profile() -> dict[str, Any]:
    """Load and validate the checked-in benchmark profile descriptor."""
    fixture = json.loads(BENCHMARK_FIXTURE.read_text(encoding="utf-8"))
    assert fixture["format"] == "hydronicus-synthetic-plant-benchmark"
    assert fixture["format_version"] == 1
    profile = fixture["profile"]
    assert profile["dry_run"] is True
    return profile


def _entity(domain: str, kind: str, index: int, suffix: str = "") -> str:
    """Return one household-agnostic synthetic entity binding."""
    suffix_part = f"_{suffix}" if suffix else ""
    return f"{domain}.synthetic_{kind}_{index + 1:03d}{suffix_part}"


def build_large_synthetic_entry() -> dict[str, Any]:
    """Expand the profile into persisted config-entry data."""
    profile = load_benchmark_profile()
    zone_count = int(profile["zone_count"])
    circuit_count = int(profile["circuit_count"])
    valve_count = int(profile["valve_count"])
    pump_count = int(profile["pump_count"])
    source_count = int(profile["source_count"])
    temperature_sensors_per_zone = int(profile["temperature_sensors_per_zone"])
    humidity_sensors_per_zone = int(profile["humidity_sensors_per_zone"])
    routes_per_zone = int(profile["routes_per_zone"])
    valves_per_circuit = int(profile["valves_per_circuit"])

    zones = []
    for zone_index in range(zone_count):
        temperature_sensor_metadata = [
            {
                "entity_id": _entity(
                    "sensor", "zone_temperature", zone_index, f"{sensor_index + 1}"
                ),
                "required": True,
                "weight": float(sensor_index + 1),
                "calibration_offset": 0.0,
                "max_age_seconds": 1800.0,
                "designated_reference": False,
            }
            for sensor_index in range(temperature_sensors_per_zone)
        ]
        humidity_sensor_metadata = [
            {
                "entity_id": _entity("sensor", "zone_humidity", zone_index, f"{sensor_index + 1}"),
                "required": True,
                "weight": 1.0,
                "calibration_offset": 0.0,
                "max_age_seconds": 1800.0,
                "designated_reference": False,
            }
            for sensor_index in range(humidity_sensors_per_zone)
        ]
        zones.append(
            {
                "id": _uuid(1000, zone_index),
                "name": f"Synthetic zone {zone_index + 1:03d}",
                "target_temperature": 21.0,
                "temperature_sensor_metadata": temperature_sensor_metadata,
                "humidity_sensor_metadata": humidity_sensor_metadata,
                "temperature_aggregation": "weighted_mean",
                "preset_targets": {"comfort": 21.5, "eco": 19.0, "away": 16.0},
            }
        )

    valves = [
        {
            "id": _uuid(2000, valve_index),
            "name": f"Synthetic valve {valve_index + 1:03d}",
            "entity_id": _entity("switch", "valve", valve_index),
            "opening_time_seconds": 30.0 + valve_index % 3 * 5.0,
            "readiness_entity_id": _entity("binary_sensor", "valve_ready", valve_index),
            "position_feedback_entity": _entity("sensor", "valve_position", valve_index),
        }
        for valve_index in range(valve_count)
    ]
    pumps = [
        {
            "id": _uuid(3000, pump_index),
            "name": f"Synthetic pump {pump_index + 1:03d}",
            "entity_id": _entity("switch", "pump", pump_index),
            "overrun_seconds": 120.0 + pump_index % 3 * 30.0,
            "power_feedback_entity": _entity("sensor", "pump_power", pump_index),
            "flow_feedback_entity": _entity("sensor", "pump_flow", pump_index),
            "fault_feedback_entity": _entity("binary_sensor", "pump_fault", pump_index),
        }
        for pump_index in range(pump_count)
    ]

    circuits = []
    for circuit_index in range(circuit_count):
        valve_ids = [
            _uuid(2000, (circuit_index * valves_per_circuit + valve_index) % valve_count)
            for valve_index in range(valves_per_circuit)
        ]
        circuits.append(
            {
                "id": _uuid(4000, circuit_index),
                "name": f"Synthetic circuit {circuit_index + 1:03d}",
                "valve_ids": valve_ids,
                "pump_id": _uuid(3000, circuit_index % pump_count),
                "cooling_enabled": True,
                "supply_temperature_sensor": _entity("sensor", "circuit_supply", circuit_index),
                "surface_temperature_sensor": _entity("sensor", "circuit_surface", circuit_index),
                "condensation_margin": 2.0,
            }
        )

    routes = []
    for zone_index in range(zone_count):
        for route_index in range(routes_per_zone):
            circuit_index = (zone_index + route_index * (circuit_count // routes_per_zone)) % (
                circuit_count
            )
            route_number = zone_index * routes_per_zone + route_index
            routes.append(
                {
                    "id": _uuid(5000, route_number),
                    "zone_id": _uuid(1000, zone_index),
                    "circuit_id": _uuid(4000, circuit_index),
                    "enabled": True,
                }
            )

    sources = []
    for source_index in range(source_count):
        source = {
            "id": _uuid(6000, source_index),
            "name": f"Synthetic source {source_index + 1:03d}",
            "source_type": "temperature_qualified_buffer" if source_index == 1 else "external",
            "priority": source_index,
            "availability_entity": _entity("binary_sensor", "source_available", source_index),
            "demand_entity": _entity("switch", "source_demand", source_index),
        }
        if source_index == 1:
            source.update(
                {
                    "temperature_entity": _entity("sensor", "source_temperature", source_index),
                    "minimum_temperature": 28.0,
                }
            )
        sources.append(source)

    topology: dict[str, Any] = {
        "zones": zones,
        "valves": valves,
        "pumps": pumps,
        "circuits": circuits,
        "routes": routes,
        "sources": sources,
    }
    if profile["source_selector"]:
        topology["source_selector"] = {
            "id": _uuid(7000, 0),
            "name": "Synthetic source selector",
            "entity_id": "select.synthetic_source_selector",
            "break_interval_seconds": 30.0,
            "minimum_dwell_seconds": 300.0,
            "release_option": "none",
            "shadow_only": True,
        }
    return {
        "name": str(profile["name"]),
        "plant_id": str(profile["plant_id"]),
        "dry_run": True,
        "topology": topology,
    }


def synthetic_state(entity_id: str) -> str:
    """Return a deterministic safe state for one benchmark binding."""
    domain, _, name = entity_id.partition(".")
    if domain == "sensor":
        if "humidity" in name:
            return "45.0"
        if "surface" in name:
            return "21.0"
        if "supply" in name:
            return "18.0"
        if "position" in name:
            return "100.0"
        if "power" in name or "flow" in name:
            return "1.0"
        if "source_temperature" in name:
            return "32.0"
        return "23.0"
    if domain == "binary_sensor":
        return "off" if "fault" in name else "on"
    if domain == "select":
        return "none"
    return "off"


def synthetic_entity_ids(value: object) -> set[str]:
    """Collect every generated Home Assistant binding from nested entry data."""
    if isinstance(value, dict):
        return {item for child in value.values() for item in synthetic_entity_ids(child)}
    if isinstance(value, list):
        return {item for child in value for item in synthetic_entity_ids(child)}
    if isinstance(value, str) and "." in value:
        domain = value.partition(".")[0]
        if domain in {"binary_sensor", "select", "sensor", "switch", "valve"}:
            return {value}
    return set()


def build_synthetic_snapshot(entry_data: dict[str, Any], now: datetime) -> PlantSnapshot:
    """Build complete synthetic observations for a pure controller evaluation."""
    topology = entry_data["topology"]
    temperatures: dict[str, TemperatureObservation] = {}
    humidities: dict[str, TemperatureObservation] = {}
    supply_temperatures: dict[str, TemperatureObservation] = {}
    surface_temperatures: dict[str, TemperatureObservation] = {}
    for zone in topology["zones"]:
        for sensor in zone["temperature_sensor_metadata"]:
            temperatures[sensor["entity_id"]] = TemperatureObservation(23.0, now)
        for sensor in zone["humidity_sensor_metadata"]:
            humidities[sensor["entity_id"]] = TemperatureObservation(45.0, now)
    for circuit in topology["circuits"]:
        supply_temperatures[circuit["supply_temperature_sensor"]] = TemperatureObservation(
            18.0, now
        )
        surface_temperatures[circuit["surface_temperature_sensor"]] = TemperatureObservation(
            21.0, now
        )
    source_temperatures = {
        source["id"]: TemperatureObservation(32.0, now)
        for source in topology["sources"]
        if "temperature_entity" in source
    }
    source_availability = {source["id"]: True for source in topology["sources"]}
    source_demand_states = {source["id"]: False for source in topology["sources"]}
    selector = topology.get("source_selector")
    source_selector_states = {selector["id"]: "none"} if selector else {}
    return PlantSnapshot(
        temperatures=temperatures,
        humidities=humidities,
        supply_temperatures=supply_temperatures,
        surface_temperatures=surface_temperatures,
        source_temperatures=source_temperatures,
        source_availability=source_availability,
        source_selector_states=source_selector_states,
        source_demand_states=source_demand_states,
    )
