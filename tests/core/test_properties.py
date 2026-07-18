"""Generated safety checks for the deterministic controller."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hydronicus_core.controller import evaluate, safe_shutdown
from hydronicus_core.model import (
    ActuatorAction,
    Circuit,
    DeliveryRoute,
    PlantConfiguration,
    PlantSnapshot,
    Pump,
    PumpRuntime,
    PumpState,
    RuntimeState,
    SafeShutdownPhase,
    Source,
    TemperatureAggregation,
    TemperatureObservation,
    TemperatureSensorMetadata,
    Valve,
    ValveRuntime,
    ValveState,
    Zone,
    ZoneDecisionStatus,
    ZoneRuntime,
)
from hydronicus_core.topology import compile_topology
from hypothesis import given
from hypothesis import strategies as st

NOW = datetime(2026, 7, 17, tzinfo=UTC)


def _shared_pump_plant(
    zone_count: int,
    *,
    opening_seconds: float = 0,
    pump_overrun_seconds: float = 10,
) -> PlantConfiguration:
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
        pumps=(Pump("pump", "Shared pump", "switch.shared_pump", pump_overrun_seconds),),
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


@given(overrun_seconds=st.integers(min_value=1, max_value=300))
def test_generated_safe_shutdown_never_closes_before_pump_overrun_finishes(
    overrun_seconds: int,
) -> None:
    """Every generated overrun keeps valve closure behind source and pump release."""
    plant = compile_topology(_shared_pump_plant(1, pump_overrun_seconds=overrun_seconds))
    running = RuntimeState(
        valves={"valve-0": ValveRuntime(ValveState.OPEN, NOW)},
        pumps={"pump": PumpRuntime(PumpState.RUNNING, NOW)},
    )

    observing, after_observing = safe_shutdown(plant, running, NOW)
    assert observing.phase is SafeShutdownPhase.PUMP_OVERRUN
    assert all(command.action is not ActuatorAction.CLOSE for command in observing.commands)

    stopping, after_stopping = safe_shutdown(
        plant,
        after_observing,
        NOW + timedelta(seconds=overrun_seconds),
    )
    assert stopping.phase is SafeShutdownPhase.PUMPS_STOPPED
    assert all(command.action is not ActuatorAction.CLOSE for command in stopping.commands)

    closed, _after_closed = safe_shutdown(plant, after_stopping, NOW)
    assert closed.phase is SafeShutdownPhase.VALVES_CLOSED


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


@st.composite
def _weighted_sensor_case(
    draw: st.DrawFn,
) -> tuple[tuple[TemperatureSensorMetadata, ...], tuple[float, ...]]:
    count = draw(st.integers(min_value=1, max_value=6))
    values = tuple(
        draw(
            st.lists(
                st.floats(min_value=15, max_value=25, allow_nan=False, allow_infinity=False),
                min_size=count,
                max_size=count,
            )
        )
    )
    weights = tuple(
        draw(
            st.lists(
                st.floats(min_value=0.1, max_value=10, allow_nan=False, allow_infinity=False),
                min_size=count,
                max_size=count,
            )
        )
    )
    offsets = tuple(
        draw(
            st.lists(
                st.floats(min_value=-2, max_value=2, allow_nan=False, allow_infinity=False),
                min_size=count,
                max_size=count,
            )
        )
    )
    metadata = tuple(
        TemperatureSensorMetadata(
            f"sensor.generated_{index}",
            weight=weights[index],
            calibration_offset=offsets[index],
        )
        for index in range(count)
    )
    return metadata, values


@given(_weighted_sensor_case())
def test_generated_sensor_metadata_order_produces_identical_evaluation(
    sensor_case: tuple[tuple[TemperatureSensorMetadata, ...], tuple[float, ...]],
) -> None:
    """Calibration and weighted aggregation are invariant to configuration order."""
    metadata, values = sensor_case
    base = dict(
        id="generated-order-plant",
        valves=(Valve("valve", "Valve", "switch.valve"),),
        pumps=(Pump("pump", "Pump", "switch.pump"),),
        circuits=(Circuit("circuit", "Circuit", ("valve",), "pump"),),
        routes=(DeliveryRoute("route", "zone", "circuit"),),
    )
    plant = compile_topology(
        PlantConfiguration(
            **base,
            zones=(
                Zone(
                    "zone",
                    "Zone",
                    21.0,
                    temperature_sensor_metadata=metadata,
                    aggregation=TemperatureAggregation.WEIGHTED_MEAN,
                ),
            ),
        )
    )
    reverse_plant = compile_topology(
        PlantConfiguration(
            **base,
            zones=(
                Zone(
                    "zone",
                    "Zone",
                    21.0,
                    temperature_sensor_metadata=tuple(reversed(metadata)),
                    aggregation=TemperatureAggregation.WEIGHTED_MEAN,
                ),
            ),
        )
    )
    snapshot = PlantSnapshot(
        {
            sensor.entity_id: TemperatureObservation(values[index], NOW)
            for index, sensor in enumerate(metadata)
        }
    )

    assert evaluate(plant, snapshot, RuntimeState(), NOW) == evaluate(
        reverse_plant, snapshot, RuntimeState(), NOW
    )


@given(
    zone_count=st.integers(min_value=1, max_value=6),
    bad_index=st.integers(min_value=0, max_value=5),
    active_seconds=st.integers(min_value=0, max_value=300),
    idle_seconds=st.integers(min_value=0, max_value=300),
)
def test_blocked_required_sensor_never_produces_zone_demand(
    zone_count: int,
    bad_index: int,
    active_seconds: int,
    idle_seconds: int,
) -> None:
    """No generated duration state can turn a required-sensor failure into demand."""
    bad_index %= zone_count
    sensor_ids = tuple(f"sensor.generated_{index}" for index in range(zone_count))
    metadata = tuple(TemperatureSensorMetadata(sensor_id) for sensor_id in sensor_ids)
    plant = compile_topology(
        PlantConfiguration(
            id="blocked-generated-plant",
            zones=(
                Zone(
                    "zone",
                    "Zone",
                    21.0,
                    temperature_sensor_metadata=metadata,
                    minimum_active_duration_seconds=active_seconds,
                    minimum_idle_duration_seconds=idle_seconds,
                ),
            ),
            valves=(Valve("valve", "Valve", "switch.valve"),),
            pumps=(Pump("pump", "Pump", "switch.pump"),),
            circuits=(Circuit("circuit", "Circuit", ("valve",), "pump"),),
            routes=(DeliveryRoute("route", "zone", "circuit"),),
        )
    )
    snapshot = PlantSnapshot(
        {
            sensor_id: TemperatureObservation(None if index == bad_index else 19.0, NOW)
            for index, sensor_id in enumerate(sensor_ids)
        }
    )
    runtime = RuntimeState(
        zone_demands={"zone": True},
        zone_runtime={"zone": ZoneRuntime(True, NOW - timedelta(seconds=1))},
    )

    result = evaluate(plant, snapshot, runtime, NOW)

    assert result.next_runtime.zone_demands["zone"] is False
    assert result.diagnostics.zone_decisions["zone"].status is ZoneDecisionStatus.SENSOR_BLOCKED


@given(shared_equipment=st.sampled_from(("valve", "pump", "source")))
def test_generated_shared_equipment_conflicts_never_share_mode_consumers(
    shared_equipment: str,
) -> None:
    """Every generated shared-equipment conflict fails closed for cooling."""
    shared_valve = shared_equipment == "valve"
    shared_pump = shared_equipment == "pump"
    valves = (
        (Valve("shared-valve", "Shared valve", "switch.shared_valve"),)
        if shared_valve
        else (
            Valve("heating-valve", "Heating valve", "switch.heating_valve"),
            Valve("cooling-valve", "Cooling valve", "switch.cooling_valve"),
        )
    )
    pumps = (
        (Pump("shared-pump", "Shared pump", "switch.shared_pump"),)
        if shared_pump
        else (
            Pump("heating-pump", "Heating pump", "switch.heating_pump"),
            Pump("cooling-pump", "Cooling pump", "switch.cooling_pump"),
        )
    )
    plant = compile_topology(
        PlantConfiguration(
            id="generated-mode-conflict",
            zones=(
                Zone("heating", "Heating", 21.0, ("sensor.heating",)),
                Zone(
                    "cooling",
                    "Cooling",
                    24.0,
                    temperature_sensor_metadata=(TemperatureSensorMetadata("sensor.cooling"),),
                    humidity_sensor_metadata=(TemperatureSensorMetadata("sensor.humidity"),),
                ),
            ),
            valves=valves,
            pumps=pumps,
            circuits=(
                Circuit(
                    "heating-circuit",
                    "Heating circuit",
                    ("shared-valve" if shared_valve else "heating-valve",),
                    "shared-pump" if shared_pump else "heating-pump",
                ),
                Circuit(
                    "cooling-circuit",
                    "Cooling circuit",
                    ("shared-valve" if shared_valve else "cooling-valve",),
                    "shared-pump" if shared_pump else "cooling-pump",
                    cooling_enabled=True,
                    supply_temperature_sensor="sensor.supply",
                ),
            ),
            routes=(
                DeliveryRoute("heating-route", "heating", "heating-circuit"),
                DeliveryRoute("cooling-route", "cooling", "cooling-circuit"),
            ),
            sources=(Source("source", "Shared source"),) if shared_equipment == "source" else (),
        )
    )
    snapshot = PlantSnapshot(
        temperatures={
            "sensor.heating": TemperatureObservation(19.0, NOW),
            "sensor.cooling": TemperatureObservation(25.0, NOW),
        },
        humidities={"sensor.humidity": TemperatureObservation(50.0, NOW)},
        supply_temperatures={"sensor.supply": TemperatureObservation(18.0, NOW)},
    )

    result = evaluate(plant, snapshot, RuntimeState(), NOW)

    assert result.diagnostics.mode_conflicts
    assert result.next_runtime.cooling_zone_demands["cooling"] is False
    assert result.control_plan.cooling_valve_consumers == {}
    assert result.control_plan.cooling_pump_consumers == {}
    assert result.control_plan.cooling_actuator_ids == frozenset()
