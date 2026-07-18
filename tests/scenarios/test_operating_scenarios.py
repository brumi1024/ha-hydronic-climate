"""Executable named scenarios from the implementation plan."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hydronicus_core.controller import evaluate
from hydronicus_core.executor import ActuatorExecutor, ActuatorOperation
from hydronicus_core.model import (
    Circuit,
    DeliveryRoute,
    PlantConfiguration,
    PlantSnapshot,
    Pump,
    PumpState,
    RuntimeState,
    Source,
    SourceKind,
    TemperatureObservation,
    TemperatureSensorMetadata,
    Valve,
    ValveState,
    Zone,
    ZoneDecisionStatus,
)
from hydronicus_core.topology import compile_topology

from .harness import ScenarioStep, run_scenario

NOW = datetime(2026, 7, 17, tzinfo=UTC)


def test_two_zones_release_shared_pump_independently() -> None:
    """One released route must not stop a pump still serving another route."""
    plant = compile_topology(
        PlantConfiguration(
            id="shared-pump-plant",
            zones=(
                Zone("living", "Living", 21.0, ("sensor.living_temperature",)),
                Zone("office", "Office", 21.0, ("sensor.office_temperature",)),
            ),
            valves=(
                Valve("living-valve", "Living valve", "switch.living_valve", 1),
                Valve("office-valve", "Office valve", "switch.office_valve", 1),
            ),
            pumps=(Pump("pump", "Shared pump", "switch.shared_pump", 10),),
            circuits=(
                Circuit("living-circuit", "Living circuit", ("living-valve",), "pump"),
                Circuit("office-circuit", "Office circuit", ("office-valve",), "pump"),
            ),
            routes=(
                DeliveryRoute("living-route", "living", "living-circuit"),
                DeliveryRoute("office-route", "office", "office-circuit"),
            ),
        )
    )
    both_request = {"sensor.living_temperature": 20.0, "sensor.office_temperature": 20.0}

    run_scenario(
        plant,
        started_at=NOW,
        steps=(
            ScenarioStep(
                timedelta(),
                both_request,
                valves={"living-valve": ValveState.OPENING, "office-valve": ValveState.OPENING},
                pumps={"pump": PumpState.OFF},
                commands=frozenset({("living-valve", "open"), ("office-valve", "open")}),
            ),
            ScenarioStep(
                timedelta(seconds=1),
                both_request,
                valves={"living-valve": ValveState.OPEN, "office-valve": ValveState.OPEN},
                pumps={"pump": PumpState.RUNNING},
                commands=frozenset({("pump", "turn_on")}),
            ),
            ScenarioStep(
                timedelta(seconds=1),
                {"sensor.living_temperature": 22.0, "sensor.office_temperature": 20.0},
                valves={"living-valve": ValveState.CLOSED, "office-valve": ValveState.OPEN},
                pumps={"pump": PumpState.RUNNING},
                commands=frozenset({("living-valve", "close")}),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_active_executor_tracer_is_idempotent_at_unchanged_fake_clock() -> None:
    """One demand transition dispatches once and an unchanged reevaluation is quiet."""
    plant = _single_zone_scenario_plant(
        sensor_metadata=(TemperatureSensorMetadata("sensor.zone"),),
        valve_opening=30,
    )
    snapshot = PlantSnapshot({"sensor.zone": TemperatureObservation(20.0, NOW)})
    executor = ActuatorExecutor.from_plant(plant, shadow_mode=False)
    dispatched: list[ActuatorOperation] = []

    async def dispatch(operation: ActuatorOperation) -> None:
        dispatched.append(operation)

    first = evaluate(plant, snapshot, RuntimeState(), NOW)
    first_report = await executor.async_execute(first.control_plan, dispatch)
    unchanged = evaluate(plant, snapshot, first.next_runtime, NOW)
    second_report = await executor.async_execute(unchanged.control_plan, dispatch)

    assert first_report.executed[0].service == "turn_on"
    assert first_report.executed[0].entity_id == "switch.scenario_valve"
    assert len(dispatched) == 1
    assert second_report.executed == ()
    assert second_report.suppressed == ()


def test_coupled_zones_share_one_valve() -> None:
    """A shared valve remains open until its final zone releases demand and overrun ends."""
    plant = compile_topology(
        PlantConfiguration(
            id="shared-valve-plant",
            zones=(
                Zone("living", "Living", 21.0, ("sensor.living_temperature",)),
                Zone("office", "Office", 21.0, ("sensor.office_temperature",)),
            ),
            valves=(Valve("valve", "Shared valve", "switch.shared_valve", 1),),
            pumps=(Pump("pump", "Shared pump", "switch.shared_pump", 10),),
            circuits=(
                Circuit("floor", "Floor circuit", ("valve",), "pump"),
                Circuit("ceiling", "Ceiling circuit", ("valve",), "pump"),
            ),
            routes=(
                DeliveryRoute("living-floor", "living", "floor"),
                DeliveryRoute("office-ceiling", "office", "ceiling"),
            ),
        )
    )
    both_request = {"sensor.living_temperature": 20.0, "sensor.office_temperature": 20.0}
    one_requests = {"sensor.living_temperature": 22.0, "sensor.office_temperature": 20.0}
    neither_requests = {"sensor.living_temperature": 22.0, "sensor.office_temperature": 22.0}

    run_scenario(
        plant,
        started_at=NOW,
        steps=(
            ScenarioStep(
                timedelta(),
                both_request,
                valves={"valve": ValveState.OPENING},
                pumps={"pump": PumpState.OFF},
                commands=frozenset({("valve", "open")}),
            ),
            ScenarioStep(
                timedelta(seconds=1),
                both_request,
                valves={"valve": ValveState.OPEN},
                pumps={"pump": PumpState.RUNNING},
                commands=frozenset({("pump", "turn_on")}),
            ),
            ScenarioStep(
                timedelta(seconds=1),
                one_requests,
                valves={"valve": ValveState.OPEN},
                pumps={"pump": PumpState.RUNNING},
            ),
            ScenarioStep(
                timedelta(seconds=1),
                neither_requests,
                valves={"valve": ValveState.OPEN},
                pumps={"pump": PumpState.OVERRUN},
            ),
            ScenarioStep(
                timedelta(seconds=10),
                neither_requests,
                valves={"valve": ValveState.CLOSED},
                pumps={"pump": PumpState.OFF},
                commands=frozenset({("pump", "turn_off"), ("valve", "close")}),
            ),
        ),
    )


def _single_zone_scenario_plant(
    *,
    sensor_metadata: tuple[TemperatureSensorMetadata, ...],
    minimum_active: float = 0,
    minimum_idle: float = 0,
    valve_opening: float = 0,
    sources: tuple[Source, ...] = (),
) -> object:
    """Build a small synthetic plant for named fake-clock scenarios."""
    return compile_topology(
        PlantConfiguration(
            id="scenario-plant",
            zones=(
                Zone(
                    "zone",
                    "Scenario zone",
                    21.0,
                    temperature_sensor_metadata=sensor_metadata,
                    minimum_active_duration_seconds=minimum_active,
                    minimum_idle_duration_seconds=minimum_idle,
                ),
            ),
            valves=(Valve("valve", "Scenario valve", "switch.scenario_valve", valve_opening),),
            pumps=(Pump("pump", "Scenario pump", "switch.scenario_pump", 0),),
            circuits=(Circuit("circuit", "Scenario circuit", ("valve",), "pump"),),
            routes=(DeliveryRoute("route", "zone", "circuit"),),
            sources=sources,
        )
    )


def test_buffer_becomes_ineligible_during_active_heating() -> None:
    """A stale buffer falls back while hydraulic demand remains active."""
    plant = _single_zone_scenario_plant(
        sensor_metadata=(TemperatureSensorMetadata("sensor.zone"),),
        sources=(
            Source(
                "buffer",
                "Buffer",
                priority=1,
                kind=SourceKind.TEMPERATURE_QUALIFIED_BUFFER,
                temperature_entity_id="sensor.buffer_temperature",
                minimum_temperature=40,
                maximum_age_seconds=30,
                hysteresis=0.5,
            ),
            Source("boiler", "Boiler", priority=2),
        ),
    )

    run_scenario(
        plant,
        started_at=NOW,
        steps=(
            ScenarioStep(
                timedelta(),
                {"sensor.zone": 19.0},
                source_temperatures={"buffer": TemperatureObservation(45.0, NOW)},
                source_availability={"buffer": True},
                valves={"valve": ValveState.OPENING},
                pumps={"pump": PumpState.OFF},
                commands=frozenset({("valve", "open")}),
                check_source=True,
                source_id="buffer",
            ),
            ScenarioStep(
                timedelta(seconds=1),
                {"sensor.zone": 19.0},
                source_temperatures={"buffer": TemperatureObservation(45.0, NOW)},
                source_availability={"buffer": True},
                valves={"valve": ValveState.OPEN},
                pumps={"pump": PumpState.RUNNING},
                commands=frozenset({("pump", "turn_on")}),
                check_source=True,
                source_id="buffer",
            ),
            ScenarioStep(
                timedelta(seconds=30),
                {"sensor.zone": 19.0},
                source_temperatures={"buffer": TemperatureObservation(45.0, NOW)},
                source_availability={"buffer": True},
                valves={"valve": ValveState.OPEN},
                pumps={"pump": PumpState.RUNNING},
                check_source=True,
                source_id="boiler",
                source_explanation="stale",
            ),
        ),
    )


def test_zone_sensor_becomes_stale() -> None:
    """A fake-clock tick blocks stale input even without a new sensor event."""
    plant = _single_zone_scenario_plant(
        sensor_metadata=(TemperatureSensorMetadata("sensor.zone", max_age_seconds=30),)
    )

    run_scenario(
        plant,
        started_at=NOW,
        steps=(
            ScenarioStep(
                timedelta(),
                {"sensor.zone": 19.0},
                valves={"valve": ValveState.OPENING},
                pumps={"pump": PumpState.OFF},
                commands=frozenset({("valve", "open")}),
                zone_demands={"zone": True},
                zone_statuses={"zone": ZoneDecisionStatus.REQUESTED},
            ),
            ScenarioStep(
                timedelta(seconds=31),
                {},
                observations={
                    "sensor.zone": TemperatureObservation(19.0, NOW),
                },
                valves={"valve": ValveState.CLOSED},
                pumps={"pump": PumpState.OFF},
                commands=frozenset({("valve", "close")}),
                zone_demands={"zone": False},
                zone_statuses={"zone": ZoneDecisionStatus.SENSOR_BLOCKED},
            ),
        ),
    )


def test_optional_sensor_degradation_preserves_demand() -> None:
    """Losing an optional reading leaves the required heating demand intact."""
    plant = _single_zone_scenario_plant(
        sensor_metadata=(
            TemperatureSensorMetadata("sensor.required"),
            TemperatureSensorMetadata("sensor.optional", required=False),
        )
    )

    run_scenario(
        plant,
        started_at=NOW,
        steps=(
            ScenarioStep(
                timedelta(),
                {"sensor.required": 19.0, "sensor.optional": 19.0},
                valves={"valve": ValveState.OPENING},
                pumps={"pump": PumpState.OFF},
                commands=frozenset({("valve", "open")}),
            ),
            ScenarioStep(
                timedelta(seconds=1),
                {"sensor.required": 19.0},
                valves={"valve": ValveState.OPEN},
                pumps={"pump": PumpState.RUNNING},
                commands=frozenset({("pump", "turn_on")}),
                zone_demands={"zone": True},
                zone_statuses={"zone": ZoneDecisionStatus.REQUESTED},
            ),
        ),
    )


def test_minimum_active_hold_then_release() -> None:
    """Heating stays active until its minimum-active deadline, then releases."""
    plant = _single_zone_scenario_plant(
        sensor_metadata=(TemperatureSensorMetadata("sensor.zone"),),
        minimum_active=10,
    )

    run_scenario(
        plant,
        started_at=NOW,
        steps=(
            ScenarioStep(
                timedelta(),
                {"sensor.zone": 19.0},
                valves={"valve": ValveState.OPENING},
                pumps={"pump": PumpState.OFF},
                commands=frozenset({("valve", "open")}),
            ),
            ScenarioStep(
                timedelta(seconds=1),
                {"sensor.zone": 19.0},
                valves={"valve": ValveState.OPEN},
                pumps={"pump": PumpState.RUNNING},
                commands=frozenset({("pump", "turn_on")}),
            ),
            ScenarioStep(
                timedelta(seconds=5),
                {"sensor.zone": 22.0},
                valves={"valve": ValveState.OPEN},
                pumps={"pump": PumpState.RUNNING},
                zone_demands={"zone": True},
                zone_statuses={"zone": ZoneDecisionStatus.DURATION_HELD},
            ),
            ScenarioStep(
                timedelta(seconds=4),
                {"sensor.zone": 22.0},
                valves={"valve": ValveState.OPEN},
                pumps={"pump": PumpState.OVERRUN},
                zone_demands={"zone": False},
                zone_statuses={"zone": ZoneDecisionStatus.SATISFIED},
            ),
        ),
    )


def test_minimum_idle_lockout_then_demand() -> None:
    """A new demand waits for the minimum-idle deadline before requesting heat."""
    plant = _single_zone_scenario_plant(
        sensor_metadata=(TemperatureSensorMetadata("sensor.zone"),),
        minimum_idle=10,
    )

    run_scenario(
        plant,
        started_at=NOW,
        steps=(
            ScenarioStep(
                timedelta(),
                {"sensor.zone": 22.0},
                zone_demands={"zone": False},
                zone_statuses={"zone": ZoneDecisionStatus.SATISFIED},
            ),
            ScenarioStep(
                timedelta(seconds=5),
                {"sensor.zone": 19.0},
                zone_demands={"zone": False},
                zone_statuses={"zone": ZoneDecisionStatus.DURATION_LOCKED},
            ),
            ScenarioStep(
                timedelta(seconds=5),
                {"sensor.zone": 19.0},
                valves={"valve": ValveState.OPENING},
                pumps={"pump": PumpState.OFF},
                commands=frozenset({("valve", "open")}),
                zone_demands={"zone": True},
                zone_statuses={"zone": ZoneDecisionStatus.REQUESTED},
            ),
        ),
    )


def test_cooling_stops_before_condensation_margin_is_crossed() -> None:
    """Cooling releases its virtual path as soon as the safety margin is unsafe."""
    plant = compile_topology(
        PlantConfiguration(
            id="cooling-safety-plant",
            zones=(
                Zone(
                    "zone",
                    "Cooling zone",
                    24.0,
                    temperature_sensor_metadata=(TemperatureSensorMetadata("sensor.zone"),),
                    humidity_sensor_metadata=(TemperatureSensorMetadata("sensor.humidity"),),
                    cooling_start_delta=0.5,
                    cooling_stop_delta=0.2,
                ),
            ),
            valves=(Valve("valve", "Cooling valve", "switch.cooling_valve", 0),),
            pumps=(Pump("pump", "Cooling pump", "switch.cooling_pump", 10),),
            circuits=(
                Circuit(
                    "cooling-circuit",
                    "Cooling circuit",
                    ("valve",),
                    "pump",
                    cooling_enabled=True,
                    supply_temperature_sensor="sensor.supply",
                    condensation_margin=2.0,
                ),
            ),
            routes=(DeliveryRoute("route", "zone", "cooling-circuit"),),
        )
    )

    run_scenario(
        plant,
        started_at=NOW,
        steps=(
            ScenarioStep(
                timedelta(),
                {"sensor.zone": 25.0},
                humidities={"sensor.humidity": 50.0},
                supply_temperatures={"sensor.supply": 18.0},
                valves={"valve": ValveState.OPENING},
                pumps={"pump": PumpState.OFF},
                commands=frozenset({("valve", "open")}),
                cooling_zone_demands={"zone": True},
                cooling_zone_statuses={"zone": ZoneDecisionStatus.REQUESTED},
            ),
            ScenarioStep(
                timedelta(seconds=1),
                {"sensor.zone": 25.0},
                humidities={"sensor.humidity": 50.0},
                supply_temperatures={"sensor.supply": 18.0},
                valves={"valve": ValveState.OPEN},
                pumps={"pump": PumpState.RUNNING},
                commands=frozenset({("pump", "turn_on")}),
                cooling_zone_demands={"zone": True},
            ),
            ScenarioStep(
                timedelta(seconds=1),
                {"sensor.zone": 25.0},
                humidities={"sensor.humidity": 50.0},
                supply_temperatures={"sensor.supply": 15.8},
                valves={"valve": ValveState.OPEN},
                pumps={"pump": PumpState.OVERRUN},
                commands=frozenset(),
                cooling_zone_demands={"zone": False},
                cooling_zone_statuses={"zone": ZoneDecisionStatus.SENSOR_BLOCKED},
            ),
            ScenarioStep(
                timedelta(seconds=10),
                {"sensor.zone": 25.0},
                humidities={"sensor.humidity": 50.0},
                supply_temperatures={"sensor.supply": 15.8},
                valves={"valve": ValveState.CLOSED},
                pumps={"pump": PumpState.OFF},
                commands=frozenset({("pump", "turn_off"), ("valve", "close")}),
                cooling_zone_demands={"zone": False},
            ),
        ),
    )


def test_shared_mode_conflict_keeps_cooling_out_of_heating_path() -> None:
    """A fake-clock arbitration pass never shares a valve or pump across modes."""
    plant = compile_topology(
        PlantConfiguration(
            id="shared-mode-scenario",
            zones=(
                Zone("heating", "Heating zone", 21.0, ("sensor.heating",)),
                Zone(
                    "cooling",
                    "Cooling zone",
                    24.0,
                    temperature_sensor_metadata=(TemperatureSensorMetadata("sensor.cooling"),),
                    humidity_sensor_metadata=(TemperatureSensorMetadata("sensor.humidity"),),
                ),
            ),
            valves=(Valve("shared-valve", "Shared valve", "switch.shared_valve", 0),),
            pumps=(Pump("shared-pump", "Shared pump", "switch.shared_pump", 0),),
            circuits=(
                Circuit("heating-circuit", "Heating circuit", ("shared-valve",), "shared-pump"),
                Circuit(
                    "cooling-circuit",
                    "Cooling circuit",
                    ("shared-valve",),
                    "shared-pump",
                    cooling_enabled=True,
                    supply_temperature_sensor="sensor.supply",
                ),
            ),
            routes=(
                DeliveryRoute("heating-route", "heating", "heating-circuit"),
                DeliveryRoute("cooling-route", "cooling", "cooling-circuit"),
            ),
        )
    )

    run_scenario(
        plant,
        started_at=NOW,
        steps=(
            ScenarioStep(
                timedelta(),
                {"sensor.heating": 19.0, "sensor.cooling": 25.0},
                humidities={"sensor.humidity": 50.0},
                supply_temperatures={"sensor.supply": 18.0},
                valves={"shared-valve": ValveState.OPENING},
                pumps={"shared-pump": PumpState.OFF},
                commands=frozenset({("shared-valve", "open")}),
                cooling_zone_demands={"cooling": False},
                cooling_zone_statuses={"cooling": ZoneDecisionStatus.SENSOR_BLOCKED},
                mode_conflict_codes=(
                    "shared_valve_heating_cooling_conflict",
                    "shared_pump_heating_cooling_conflict",
                ),
            ),
            ScenarioStep(
                timedelta(seconds=1),
                {"sensor.heating": 19.0, "sensor.cooling": 25.0},
                humidities={"sensor.humidity": 50.0},
                supply_temperatures={"sensor.supply": 18.0},
                valves={"shared-valve": ValveState.OPEN},
                pumps={"shared-pump": PumpState.RUNNING},
                commands=frozenset({("shared-pump", "turn_on")}),
                cooling_zone_demands={"cooling": False},
                mode_conflict_codes=(
                    "shared_valve_heating_cooling_conflict",
                    "shared_pump_heating_cooling_conflict",
                ),
            ),
        ),
    )
