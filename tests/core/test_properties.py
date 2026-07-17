"""Generated safety checks for the deterministic controller."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
from hypothesis import given
from hypothesis import strategies as st

NOW = datetime(2026, 7, 17, tzinfo=UTC)


def _shared_pump_plant(zone_count: int, *, opening_seconds: float = 0) -> PlantConfiguration:
    """Build a valid generated topology with independent valves and one shared pump."""
    zones = tuple(
        Zone(f"zone-{index}", f"Zone {index}", 21.0, (f"sensor.zone_{index}",))
        for index in range(zone_count)
    )
    valves = tuple(
        Valve(
            f"valve-{index}",
            f"Valve {index}",
            f"switch.valve_{index}",
            opening_seconds,
        )
        for index in range(zone_count)
    )
    circuits = tuple(
        Circuit(f"circuit-{index}", f"Circuit {index}", (f"valve-{index}",), "pump")
        for index in range(zone_count)
    )
    routes = tuple(
        DeliveryRoute(f"route-{index}", f"zone-{index}", f"circuit-{index}")
        for index in range(zone_count)
    )
    return PlantConfiguration(
        id="generated-plant",
        zones=zones,
        valves=valves,
        pumps=(Pump("pump", "Shared pump", "switch.shared_pump", 10),),
        circuits=circuits,
        routes=routes,
    )


def _snapshot(temperatures: tuple[float, ...]) -> PlantSnapshot:
    return PlantSnapshot(
        {
            f"sensor.zone_{index}": TemperatureObservation(temperature, NOW)
            for index, temperature in enumerate(temperatures)
        }
    )


@st.composite
def _partial_release(draw: st.DrawFn) -> tuple[int, frozenset[int]]:
    zone_count = draw(st.integers(min_value=2, max_value=6))
    remaining = draw(
        st.frozensets(
            st.integers(min_value=0, max_value=zone_count - 1),
            min_size=1,
            max_size=zone_count - 1,
        )
    )
    return zone_count, remaining


@given(_partial_release())
def test_shared_pump_never_stops_while_a_consumer_remains(
    release_case: tuple[int, frozenset[int]],
) -> None:
    """Releasing any proper subset must preserve the complete remaining consumer set."""
    zone_count, remaining = release_case
    plant = compile_topology(_shared_pump_plant(zone_count))
    all_request = _snapshot((20.0,) * zone_count)
    after_release = _snapshot(
        tuple(20.0 if index in remaining else 22.0 for index in range(zone_count))
    )

    opening = evaluate(plant, all_request, RuntimeState(), NOW)
    running = evaluate(plant, all_request, opening.next_runtime, NOW)
    released = evaluate(plant, after_release, running.next_runtime, NOW + timedelta(seconds=1))

    assert released.next_runtime.pumps["pump"].state is PumpState.RUNNING
    assert released.control_plan.pump_consumers["pump"] == frozenset(
        f"circuit-{index}" for index in remaining
    )
    assert all(
        not (command.actuator_id == "pump" and command.action == "turn_off")
        for command in released.control_plan.commands
    )


@given(
    zone_count=st.integers(min_value=1, max_value=6),
    opening_seconds=st.integers(min_value=1, max_value=300),
    elapsed_seconds=st.integers(min_value=0, max_value=299),
)
def test_pump_cannot_run_before_every_requested_valve_is_ready(
    zone_count: int,
    opening_seconds: int,
    elapsed_seconds: int,
) -> None:
    """Generated timing combinations must fail closed before the opening delay elapses."""
    elapsed_seconds %= opening_seconds
    plant = compile_topology(_shared_pump_plant(zone_count, opening_seconds=opening_seconds))
    snapshot = _snapshot((20.0,) * zone_count)

    opening = evaluate(plant, snapshot, RuntimeState(), NOW)
    waiting = evaluate(
        plant,
        snapshot,
        opening.next_runtime,
        NOW + timedelta(seconds=elapsed_seconds),
    )

    assert all(valve.state is ValveState.OPENING for valve in waiting.next_runtime.valves.values())
    assert waiting.next_runtime.pumps["pump"].state is PumpState.OFF
    assert all(command.actuator_id != "pump" for command in waiting.control_plan.commands)


@given(
    temperatures=st.lists(
        st.floats(min_value=15, max_value=25, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=6,
    )
)
def test_evaluation_is_deterministic_for_generated_topologies(
    temperatures: list[float],
) -> None:
    """Identical topology, observations, state, and time must produce an identical result."""
    plant = compile_topology(_shared_pump_plant(len(temperatures)))
    snapshot = _snapshot(tuple(temperatures))

    first = evaluate(plant, snapshot, RuntimeState(), NOW)
    second = evaluate(plant, snapshot, RuntimeState(), NOW)

    assert first == second


@given(
    temperatures=st.lists(
        st.floats(min_value=15, max_value=25, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=6,
    )
)
def test_unchanged_generated_snapshot_produces_no_new_commands(
    temperatures: list[float],
) -> None:
    """A settled generated plant must not emit another command for unchanged inputs."""
    plant = compile_topology(_shared_pump_plant(len(temperatures)))
    snapshot = _snapshot(tuple(temperatures))

    initial = evaluate(plant, snapshot, RuntimeState(), NOW)
    settled = evaluate(plant, snapshot, initial.next_runtime, NOW)
    unchanged = evaluate(
        plant,
        snapshot,
        settled.next_runtime,
        NOW + timedelta(seconds=1),
    )

    assert unchanged.control_plan.commands == ()
