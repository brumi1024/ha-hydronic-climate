"""Tests for the pure topology compiler."""

from __future__ import annotations

import math

import pytest
from hydronic_climate_core.model import (
    Circuit,
    DeliveryRoute,
    PlantConfiguration,
    Pump,
    Valve,
    Zone,
)
from hydronic_climate_core.topology import TopologyValidationError, compile_topology


def test_compile_topology_produces_summary() -> None:
    """A valid topology should compile into a deterministic summary."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(
            Zone(
                id="zone-1",
                name="Living room",
                target_temperature=21.5,
                temperature_sensors=("sensor.living_room_temperature",),
            ),
        ),
        valves=(Valve("valve-1", "Floor valve", "switch.floor_valve"),),
        pumps=(Pump("pump-1", "Floor pump", "switch.floor_pump"),),
        circuits=(
            Circuit(
                id="circuit-1",
                name="Floor loop",
                valve_ids=("valve-1",),
                pump_id="pump-1",
            ),
        ),
        routes=(DeliveryRoute(id="route-1", zone_id="zone-1", circuit_id="circuit-1"),),
    )

    compiled = compile_topology(plant)

    assert compiled.id == "plant-1"
    assert compiled.logic_summary == (
        "Circuit Floor loop opens valves Floor valve before requesting pump Floor pump.",
        "Zone Living room can request circuit Floor loop.",
    )


def test_compile_topology_explains_multi_route_and_shared_equipment() -> None:
    """The preview should make shared hydraulic ownership understandable."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(
            Zone("living", "Living room", 21.0, ("sensor.living_temperature",)),
            Zone("office", "Office", 20.0, ("sensor.office_temperature",)),
        ),
        valves=(Valve("shared", "Shared valve", "switch.shared_valve"),),
        pumps=(Pump("pump", "Shared pump", "switch.shared_pump"),),
        circuits=(
            Circuit("floor", "Floor loop", ("shared",), "pump"),
            Circuit("ceiling", "Ceiling loop", ("shared",), "pump"),
        ),
        routes=(
            DeliveryRoute("living-floor", "living", "floor"),
            DeliveryRoute("living-ceiling", "living", "ceiling"),
            DeliveryRoute("office-floor", "office", "floor"),
        ),
    )

    compiled = compile_topology(plant)

    assert compiled.logic_summary == (
        "Circuit Floor loop opens valves Shared valve before requesting pump Shared pump.",
        "Circuit Ceiling loop opens valves Shared valve before requesting pump Shared pump.",
        "Zone Living room can request circuits Floor loop, Ceiling loop.",
        "Zone Office can request circuit Floor loop.",
        "Valve Shared valve is shared by circuits Floor loop, Ceiling loop.",
        "Pump Shared pump is shared by circuits Floor loop, Ceiling loop.",
    )


def test_compile_topology_rejects_duplicate_delivery_route_relationships() -> None:
    """A zone-to-circuit relationship must have one stable route identity."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(Zone("zone-1", "Living room", 21.5, ("sensor.living_temperature",)),),
        valves=(Valve("valve-1", "Floor valve", "switch.floor_valve"),),
        pumps=(Pump("pump-1", "Floor pump", "switch.floor_pump"),),
        circuits=(Circuit("circuit-1", "Floor loop", ("valve-1",), "pump-1"),),
        routes=(
            DeliveryRoute("route-1", "zone-1", "circuit-1"),
            DeliveryRoute("route-2", "zone-1", "circuit-1", enabled=False),
        ),
    )

    with pytest.raises(
        TopologyValidationError,
        match="Duplicate delivery routes: zone-1 -> circuit-1",
    ):
        compile_topology(plant)


def test_compile_topology_rejects_orphans() -> None:
    """A topology with unreferenced objects should fail closed."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(
            Zone(
                id="zone-1",
                name="Living room",
                target_temperature=21.5,
                temperature_sensors=("sensor.living_room_temperature",),
            ),
        ),
        valves=(Valve("valve-1", "Floor valve", "switch.floor_valve"),),
        pumps=(Pump("pump-1", "Floor pump", "switch.floor_pump"),),
        circuits=(
            Circuit(
                id="circuit-1",
                name="Floor loop",
                valve_ids=("valve-1",),
                pump_id="pump-1",
            ),
        ),
        routes=tuple(),
    )

    with pytest.raises(TopologyValidationError, match="orphaned zones"):
        compile_topology(plant)


def test_compile_topology_rejects_unknown_actuator_relationships() -> None:
    """Circuit relationships must resolve to topology-owned actuator ids."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(Zone("zone-1", "Living room", 21.5, ("sensor.living_temperature",)),),
        valves=(Valve("valve-1", "Floor valve", "switch.floor_valve"),),
        pumps=(Pump("pump-1", "Floor pump", "switch.floor_pump"),),
        circuits=(Circuit("circuit-1", "Floor loop", ("missing-valve",), "pump-1"),),
        routes=(DeliveryRoute("route-1", "zone-1", "circuit-1"),),
    )

    with pytest.raises(TopologyValidationError, match="unknown valves: missing-valve"):
        compile_topology(plant)


def test_compile_topology_rejects_unknown_pump_relationship() -> None:
    """A circuit pump relationship must resolve to a topology-owned pump id."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(Zone("zone-1", "Living room", 21.5, ("sensor.living_temperature",)),),
        valves=(Valve("valve-1", "Floor valve", "switch.floor_valve"),),
        pumps=(Pump("pump-1", "Floor pump", "switch.floor_pump"),),
        circuits=(Circuit("circuit-1", "Floor loop", ("valve-1",), "missing-pump"),),
        routes=(DeliveryRoute("route-1", "zone-1", "circuit-1"),),
    )

    with pytest.raises(TopologyValidationError, match="unknown pump missing-pump"):
        compile_topology(plant)


def test_compile_topology_rejects_orphaned_actuators() -> None:
    """Unused actuator nodes should fail closed instead of silently drifting."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(Zone("zone-1", "Living room", 21.5, ("sensor.living_temperature",)),),
        valves=(
            Valve("valve-1", "Floor valve", "switch.floor_valve"),
            Valve("valve-2", "Unused valve", "switch.unused_valve"),
        ),
        pumps=(
            Pump("pump-1", "Floor pump", "switch.floor_pump"),
            Pump("pump-2", "Unused pump", "switch.unused_pump"),
        ),
        circuits=(Circuit("circuit-1", "Floor loop", ("valve-1",), "pump-1"),),
        routes=(DeliveryRoute("route-1", "zone-1", "circuit-1"),),
    )

    with pytest.raises(
        TopologyValidationError, match="orphaned valves: valve-2; orphaned pumps: pump-2"
    ):
        compile_topology(plant)


def test_compile_topology_rejects_duplicate_actuator_ids() -> None:
    """Actuator relationship ids must be globally unambiguous within their type."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(Zone("zone-1", "Living room", 21.5, ("sensor.living_temperature",)),),
        valves=(
            Valve("valve-1", "First valve", "switch.first_valve"),
            Valve("valve-1", "Second valve", "switch.second_valve"),
        ),
        pumps=(Pump("pump-1", "Floor pump", "switch.floor_pump"),),
        circuits=(Circuit("circuit-1", "Floor loop", ("valve-1",), "pump-1"),),
        routes=(DeliveryRoute("route-1", "zone-1", "circuit-1"),),
    )

    with pytest.raises(TopologyValidationError, match="Duplicate valve ids: valve-1"):
        compile_topology(plant)


def test_compile_topology_rejects_duplicate_pump_ids() -> None:
    """Pump relationship ids must be globally unambiguous."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(Zone("zone-1", "Living room", 21.5, ("sensor.living_temperature",)),),
        valves=(Valve("valve-1", "Floor valve", "switch.floor_valve"),),
        pumps=(
            Pump("pump-1", "First pump", "switch.first_pump"),
            Pump("pump-1", "Second pump", "switch.second_pump"),
        ),
        circuits=(Circuit("circuit-1", "Floor loop", ("valve-1",), "pump-1"),),
        routes=(DeliveryRoute("route-1", "zone-1", "circuit-1"),),
    )

    with pytest.raises(TopologyValidationError, match="Duplicate pump ids: pump-1"):
        compile_topology(plant)


def test_compile_topology_rejects_circuit_without_valves() -> None:
    """A circuit without a valve cannot be sequenced safely."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(Zone("zone-1", "Living room", 21.5, ("sensor.living_temperature",)),),
        valves=(),
        pumps=(Pump("pump-1", "Floor pump", "switch.floor_pump"),),
        circuits=(Circuit("circuit-1", "Floor loop", (), "pump-1"),),
        routes=(DeliveryRoute("route-1", "zone-1", "circuit-1"),),
    )

    with pytest.raises(
        TopologyValidationError, match="Circuit circuit-1 requires at least one valve"
    ):
        compile_topology(plant)


@pytest.mark.parametrize("target_temperature", [math.inf, -math.inf, math.nan])
def test_compile_topology_rejects_non_finite_zone_target(
    target_temperature: float,
) -> None:
    """Zone demand thresholds must remain finite and deterministic."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(
            Zone(
                "zone-1",
                "Living room",
                target_temperature,
                ("sensor.living_temperature",),
            ),
        ),
        valves=(Valve("valve-1", "Floor valve", "switch.floor_valve"),),
        pumps=(Pump("pump-1", "Floor pump", "switch.floor_pump"),),
        circuits=(Circuit("circuit-1", "Floor loop", ("valve-1",), "pump-1"),),
        routes=(DeliveryRoute("route-1", "zone-1", "circuit-1"),),
    )

    with pytest.raises(
        TopologyValidationError,
        match="Zone zone-1 target temperature must be finite",
    ):
        compile_topology(plant)


def test_compile_topology_rejects_duplicate_zone_temperature_sensors() -> None:
    """One physical reading must not receive accidental double aggregation weight."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(
            Zone(
                "zone-1",
                "Living room",
                21.5,
                ("sensor.living_temperature", "sensor.living_temperature"),
            ),
        ),
        valves=(Valve("valve-1", "Floor valve", "switch.floor_valve"),),
        pumps=(Pump("pump-1", "Floor pump", "switch.floor_pump"),),
        circuits=(Circuit("circuit-1", "Floor loop", ("valve-1",), "pump-1"),),
        routes=(DeliveryRoute("route-1", "zone-1", "circuit-1"),),
    )

    with pytest.raises(
        TopologyValidationError,
        match="Zone zone-1 temperature sensors must not contain duplicates",
    ):
        compile_topology(plant)


def test_compile_topology_rejects_zone_without_temperature_sensors() -> None:
    """Every zone needs at least one observation before demand can be evaluated."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(Zone("zone-1", "Living room", 21.5, ()),),
        valves=(Valve("valve-1", "Floor valve", "switch.floor_valve"),),
        pumps=(Pump("pump-1", "Floor pump", "switch.floor_pump"),),
        circuits=(Circuit("circuit-1", "Floor loop", ("valve-1",), "pump-1"),),
        routes=(DeliveryRoute("route-1", "zone-1", "circuit-1"),),
    )

    with pytest.raises(
        TopologyValidationError,
        match="Zone zone-1 requires at least one temperature sensor",
    ):
        compile_topology(plant)


def test_compile_topology_rejects_blank_temperature_sensor_id() -> None:
    """Every configured observation must identify a real Home Assistant entity."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(Zone("zone-1", "Living room", 21.5, ("",)),),
        valves=(Valve("valve-1", "Floor valve", "switch.floor_valve"),),
        pumps=(Pump("pump-1", "Floor pump", "switch.floor_pump"),),
        circuits=(Circuit("circuit-1", "Floor loop", ("valve-1",), "pump-1"),),
        routes=(DeliveryRoute("route-1", "zone-1", "circuit-1"),),
    )

    with pytest.raises(
        TopologyValidationError,
        match="Zone zone-1 temperature sensors must be non-empty entity ids",
    ):
        compile_topology(plant)


@pytest.mark.parametrize("opening_time", [-1.0, math.inf, math.nan])
def test_compile_topology_rejects_unsafe_valve_timing(opening_time: float) -> None:
    """Valve readiness timing must be finite and non-negative."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(Zone("zone-1", "Living room", 21.5, ("sensor.living_temperature",)),),
        valves=(
            Valve(
                "valve-1",
                "Floor valve",
                "switch.floor_valve",
                opening_time_seconds=opening_time,
            ),
        ),
        pumps=(Pump("pump-1", "Floor pump", "switch.floor_pump"),),
        circuits=(Circuit("circuit-1", "Floor loop", ("valve-1",), "pump-1"),),
        routes=(DeliveryRoute("route-1", "zone-1", "circuit-1"),),
    )

    with pytest.raises(
        TopologyValidationError, match="Valve valve-1 opening time must be finite and non-negative"
    ):
        compile_topology(plant)


@pytest.mark.parametrize("overrun", [-1.0, math.inf, math.nan])
def test_compile_topology_rejects_unsafe_pump_timing(overrun: float) -> None:
    """Pump overrun timing must be finite and non-negative."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(Zone("zone-1", "Living room", 21.5, ("sensor.living_temperature",)),),
        valves=(Valve("valve-1", "Floor valve", "switch.floor_valve"),),
        pumps=(
            Pump(
                "pump-1",
                "Floor pump",
                "switch.floor_pump",
                overrun_seconds=overrun,
            ),
        ),
        circuits=(Circuit("circuit-1", "Floor loop", ("valve-1",), "pump-1"),),
        routes=(DeliveryRoute("route-1", "zone-1", "circuit-1"),),
    )

    with pytest.raises(
        TopologyValidationError, match="Pump pump-1 overrun must be finite and non-negative"
    ):
        compile_topology(plant)


def test_compile_topology_rejects_duplicate_physical_actuator_bindings() -> None:
    """One physical HA entity must compile to one actuator state machine."""
    plant = PlantConfiguration(
        id="plant-1",
        zones=(Zone("zone-1", "Living room", 21.5, ("sensor.living_temperature",)),),
        valves=(
            Valve("valve-1", "First valve", "switch.shared_valve"),
            Valve("valve-2", "Second valve", "switch.shared_valve"),
        ),
        pumps=(Pump("pump-1", "Floor pump", "switch.floor_pump"),),
        circuits=(Circuit("circuit-1", "Floor loop", ("valve-1",), "pump-1"),),
        routes=(DeliveryRoute("route-1", "zone-1", "circuit-1"),),
    )

    with pytest.raises(
        TopologyValidationError,
        match="Duplicate actuator entity bindings: switch.shared_valve",
    ):
        compile_topology(plant)
