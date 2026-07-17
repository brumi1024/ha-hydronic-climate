"""Executable named scenarios from the implementation plan."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hydronicus_core.model import (
    Circuit,
    DeliveryRoute,
    PlantConfiguration,
    Pump,
    PumpState,
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
            valves=(Valve("valve", "Scenario valve", "switch.scenario_valve", 0),),
            pumps=(Pump("pump", "Scenario pump", "switch.scenario_pump", 0),),
            circuits=(Circuit("circuit", "Scenario circuit", ("valve",), "pump"),),
            routes=(DeliveryRoute("route", "zone", "circuit"),),
        )
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
