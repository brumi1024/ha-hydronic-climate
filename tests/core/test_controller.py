"""Tests for the deterministic shadow-mode heating sequence."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from hydronic_climate_core.controller import evaluate
from hydronic_climate_core.model import (
    Circuit,
    DeliveryRoute,
    PlantConfiguration,
    PlantSnapshot,
    Pump,
    PumpState,
    RuntimeState,
    TemperatureObservation,
    Valve,
    ValveState,
    Zone,
)
from hydronic_climate_core.topology import compile_topology

NOW = datetime(2026, 7, 17, tzinfo=UTC)


def _plant() -> PlantConfiguration:
    return PlantConfiguration(
        id="plant",
        zones=(Zone("living", "Living", 21.0, ("temperature.living",)),),
        valves=(Valve("valve.floor", "Floor valve", "switch.floor_valve", 30),),
        pumps=(Pump("pump.floor", "Floor pump", "switch.floor_pump", 120),),
        circuits=(
            Circuit(
                "floor",
                "Floor",
                ("valve.floor",),
                "pump.floor",
            ),
        ),
        routes=(DeliveryRoute("living-floor", "living", "floor"),),
    )


def _snapshot(temperature: float) -> PlantSnapshot:
    return PlantSnapshot({"temperature.living": TemperatureObservation(temperature, NOW)})


def test_shadow_sequence_waits_for_valve_before_requesting_pump() -> None:
    plant = compile_topology(_plant())

    opening = evaluate(plant, _snapshot(20.0), RuntimeState(), NOW)
    ready = evaluate(plant, _snapshot(20.0), opening.next_runtime, NOW + timedelta(seconds=30))

    assert opening.next_runtime.valves["valve.floor"].state is ValveState.OPENING
    assert opening.control_plan.commands[0].action == "open"
    assert "pump.floor" not in {command.actuator_id for command in opening.control_plan.commands}
    assert ready.next_runtime.valves["valve.floor"].state is ValveState.OPEN
    assert ready.next_runtime.pumps["pump.floor"].state is PumpState.RUNNING
    assert [(command.actuator_id, command.action) for command in ready.control_plan.commands] == [
        ("pump.floor", "turn_on")
    ]


def test_removing_demand_overruns_pump_before_closing_valve() -> None:
    plant = compile_topology(_plant())
    opening = evaluate(plant, _snapshot(20.0), RuntimeState(), NOW)
    running = evaluate(plant, _snapshot(20.0), opening.next_runtime, NOW + timedelta(seconds=30))

    overrun = evaluate(plant, _snapshot(22.0), running.next_runtime, NOW + timedelta(seconds=31))
    stopped = evaluate(plant, _snapshot(22.0), overrun.next_runtime, NOW + timedelta(seconds=151))

    assert overrun.next_runtime.pumps["pump.floor"].state is PumpState.OVERRUN
    assert overrun.next_runtime.valves["valve.floor"].state is ValveState.OPEN
    assert [command.action for command in overrun.control_plan.commands] == []
    assert stopped.next_runtime.pumps["pump.floor"].state is PumpState.OFF
    assert stopped.next_runtime.valves["valve.floor"].state is ValveState.CLOSED
    assert {(command.actuator_id, command.action) for command in stopped.control_plan.commands} == {
        ("pump.floor", "turn_off"),
        ("valve.floor", "close"),
    }


def test_shared_pump_remains_running_when_one_consumer_releases_demand() -> None:
    plant = compile_topology(
        PlantConfiguration(
            id="plant",
            zones=(
                Zone("living", "Living", 21.0, ("temperature.living",)),
                Zone("office", "Office", 21.0, ("temperature.office",)),
            ),
            valves=(
                Valve("valve.floor", "Floor valve", "switch.floor_valve", 1),
                Valve("valve.office", "Office valve", "switch.office_valve", 1),
            ),
            pumps=(Pump("pump.shared", "Shared pump", "switch.shared_pump"),),
            circuits=(
                Circuit(
                    "floor",
                    "Floor",
                    ("valve.floor",),
                    "pump.shared",
                ),
                Circuit(
                    "office",
                    "Office",
                    ("valve.office",),
                    "pump.shared",
                ),
            ),
            routes=(
                DeliveryRoute("living-floor", "living", "floor"),
                DeliveryRoute("office-circuit", "office", "office"),
            ),
        )
    )
    cool_both = PlantSnapshot(
        {
            "temperature.living": TemperatureObservation(20.0, NOW),
            "temperature.office": TemperatureObservation(20.0, NOW),
        }
    )
    only_office = PlantSnapshot(
        {
            "temperature.living": TemperatureObservation(22.0, NOW),
            "temperature.office": TemperatureObservation(20.0, NOW),
        }
    )
    opening = evaluate(plant, cool_both, RuntimeState(), NOW)
    running = evaluate(plant, cool_both, opening.next_runtime, NOW + timedelta(seconds=1))
    released = evaluate(plant, only_office, running.next_runtime, NOW + timedelta(seconds=2))

    assert released.next_runtime.pumps["pump.shared"].state is PumpState.RUNNING
    assert released.control_plan.pump_consumers["pump.shared"] == frozenset({"office"})
    assert all(command.actuator_id != "pump.shared" for command in released.control_plan.commands)


def test_shared_valve_and_pump_remain_active_until_last_consumer_releases() -> None:
    """One zone releasing must not stop equipment still owned by another circuit."""
    plant = compile_topology(
        PlantConfiguration(
            id="plant",
            zones=(
                Zone("living", "Living", 21.0, ("temperature.living",)),
                Zone("office", "Office", 21.0, ("temperature.office",)),
            ),
            valves=(Valve("shared", "Shared valve", "switch.shared_valve", 1),),
            pumps=(Pump("pump", "Shared pump", "switch.shared_pump", 10),),
            circuits=(
                Circuit("floor", "Floor", ("shared",), "pump"),
                Circuit("ceiling", "Ceiling", ("shared",), "pump"),
            ),
            routes=(
                DeliveryRoute("living-floor", "living", "floor"),
                DeliveryRoute("office-ceiling", "office", "ceiling"),
            ),
        )
    )
    both_request = PlantSnapshot(
        {
            "temperature.living": TemperatureObservation(20.0, NOW),
            "temperature.office": TemperatureObservation(20.0, NOW),
        }
    )
    only_office = PlantSnapshot(
        {
            "temperature.living": TemperatureObservation(22.0, NOW),
            "temperature.office": TemperatureObservation(20.0, NOW),
        }
    )
    neither_requests = PlantSnapshot(
        {
            "temperature.living": TemperatureObservation(22.0, NOW),
            "temperature.office": TemperatureObservation(22.0, NOW),
        }
    )

    opening = evaluate(plant, both_request, RuntimeState(), NOW)
    running = evaluate(
        plant, both_request, opening.next_runtime, NOW + timedelta(seconds=1)
    )
    one_released = evaluate(
        plant, only_office, running.next_runtime, NOW + timedelta(seconds=2)
    )
    overrun = evaluate(
        plant, neither_requests, one_released.next_runtime, NOW + timedelta(seconds=3)
    )
    stopped = evaluate(
        plant, neither_requests, overrun.next_runtime, NOW + timedelta(seconds=13)
    )

    assert one_released.control_plan.valve_consumers == {
        "shared": frozenset({"ceiling"})
    }
    assert one_released.control_plan.pump_consumers == {
        "pump": frozenset({"ceiling"})
    }
    assert one_released.next_runtime.valves["shared"].state is ValveState.OPEN
    assert one_released.next_runtime.pumps["pump"].state is PumpState.RUNNING
    assert one_released.control_plan.commands == ()
    assert overrun.next_runtime.valves["shared"].state is ValveState.OPEN
    assert overrun.next_runtime.pumps["pump"].state is PumpState.OVERRUN
    assert stopped.next_runtime.valves["shared"].state is ValveState.CLOSED
    assert stopped.next_runtime.pumps["pump"].state is PumpState.OFF


def test_one_zone_requests_every_enabled_delivery_route() -> None:
    """Heating-only arbitration retains the legacy any-demand behavior."""
    plant = compile_topology(
        PlantConfiguration(
            id="plant",
            zones=(Zone("living", "Living", 21.0, ("temperature.living",)),),
            valves=(
                Valve("floor-valve", "Floor valve", "switch.floor_valve", 1),
                Valve("ceiling-valve", "Ceiling valve", "switch.ceiling_valve", 1),
            ),
            pumps=(
                Pump("floor-pump", "Floor pump", "switch.floor_pump"),
                Pump("ceiling-pump", "Ceiling pump", "switch.ceiling_pump"),
            ),
            circuits=(
                Circuit("floor", "Floor", ("floor-valve",), "floor-pump"),
                Circuit("ceiling", "Ceiling", ("ceiling-valve",), "ceiling-pump"),
            ),
            routes=(
                DeliveryRoute("living-floor", "living", "floor"),
                DeliveryRoute("living-ceiling", "living", "ceiling"),
            ),
        )
    )

    opening = evaluate(plant, _snapshot(20.0), RuntimeState(), NOW)
    ready = evaluate(plant, _snapshot(20.0), opening.next_runtime, NOW + timedelta(seconds=1))

    assert opening.control_plan.valve_consumers == {
        "floor-valve": frozenset({"floor"}),
        "ceiling-valve": frozenset({"ceiling"}),
    }
    assert ready.control_plan.pump_consumers == {
        "floor-pump": frozenset({"floor"}),
        "ceiling-pump": frozenset({"ceiling"}),
    }
    assert opening.diagnostics.circuit_reasons["floor"] == (
        "Waiting for valve readiness after eligible delivery route living-floor "
        "requested this circuit."
    )


def test_unchanged_running_snapshot_produces_no_new_commands() -> None:
    plant = compile_topology(_plant())
    opening = evaluate(plant, _snapshot(20.0), RuntimeState(), NOW)
    running = evaluate(plant, _snapshot(20.0), opening.next_runtime, NOW + timedelta(seconds=30))

    unchanged = evaluate(plant, _snapshot(20.0), running.next_runtime, NOW + timedelta(seconds=31))

    assert unchanged.control_plan.commands == ()


def test_zone_demand_uses_mean_of_multiple_battery_sensor_readings() -> None:
    """Old but usable battery readings should contribute without an implicit timeout."""
    plant = compile_topology(
        PlantConfiguration(
            id="plant",
            zones=(
                Zone(
                    "living",
                    "Living",
                    21.0,
                    temperature_sensors=(
                        "temperature.living_wall",
                        "temperature.living_window",
                    ),
                ),
            ),
            valves=(Valve("valve.floor", "Floor valve", "switch.floor_valve", 30),),
            pumps=(Pump("pump.floor", "Floor pump", "switch.floor_pump", 120),),
            circuits=(Circuit("floor", "Floor", ("valve.floor",), "pump.floor"),),
            routes=(DeliveryRoute("living-floor", "living", "floor"),),
        )
    )
    snapshot = PlantSnapshot(
        {
            "temperature.living_wall": TemperatureObservation(
                19.0, NOW - timedelta(hours=12)
            ),
            "temperature.living_window": TemperatureObservation(21.0, NOW),
        }
    )

    result = evaluate(plant, snapshot, RuntimeState(), NOW)

    assert result.next_runtime.zone_demands == {"living": True}
    assert result.diagnostics.zone_reasons["living"].startswith(
        "Heating requested: 20.0"
    )


@pytest.mark.parametrize("unusable_value", [None, math.nan, math.inf, -math.inf])
def test_zone_blocks_when_any_required_temperature_sensor_is_unusable(
    unusable_value: float | None,
) -> None:
    """A missing required reading must not be silently dropped from the mean."""
    plant = compile_topology(
        PlantConfiguration(
            id="plant",
            zones=(
                Zone(
                    "living",
                    "Living",
                    21.0,
                    ("temperature.living_wall", "temperature.living_window"),
                ),
            ),
            valves=(Valve("valve.floor", "Floor valve", "switch.floor_valve", 30),),
            pumps=(Pump("pump.floor", "Floor pump", "switch.floor_pump", 120),),
            circuits=(Circuit("floor", "Floor", ("valve.floor",), "pump.floor"),),
            routes=(DeliveryRoute("living-floor", "living", "floor"),),
        )
    )
    snapshot = PlantSnapshot(
        {
            "temperature.living_wall": TemperatureObservation(19.0, NOW),
            "temperature.living_window": TemperatureObservation(unusable_value, NOW),
        }
    )

    result = evaluate(plant, snapshot, RuntimeState(), NOW)

    assert result.next_runtime.zone_demands == {"living": False}
    assert result.diagnostics.zone_reasons["living"].startswith("Blocked:")


def test_circuit_waits_for_every_series_valve_before_pump_request() -> None:
    """A circuit is ready only after all of its required valves are open."""
    plant = compile_topology(
        PlantConfiguration(
            id="plant",
            zones=(Zone("living", "Living", 21.0, ("temperature.living",)),),
            valves=(
                Valve("supply", "Supply valve", "switch.supply_valve", 10),
                Valve("return", "Return valve", "switch.return_valve", 30),
            ),
            pumps=(Pump("pump", "Circulation pump", "switch.circulation_pump", 120),),
            circuits=(Circuit("floor", "Floor", ("supply", "return"), "pump"),),
            routes=(DeliveryRoute("living-floor", "living", "floor"),),
        )
    )

    opening = evaluate(plant, _snapshot(20.0), RuntimeState(), NOW)
    one_ready = evaluate(
        plant, _snapshot(20.0), opening.next_runtime, NOW + timedelta(seconds=10)
    )
    all_ready = evaluate(
        plant, _snapshot(20.0), one_ready.next_runtime, NOW + timedelta(seconds=30)
    )

    assert one_ready.next_runtime.valves["supply"].state is ValveState.OPEN
    assert one_ready.next_runtime.valves["return"].state is ValveState.OPENING
    assert one_ready.next_runtime.pumps["pump"].state is PumpState.OFF
    assert all_ready.next_runtime.pumps["pump"].state is PumpState.RUNNING
    commands = [
        (command.actuator_id, command.action)
        for command in all_ready.control_plan.commands
    ]
    assert commands == [("pump", "turn_on")]
