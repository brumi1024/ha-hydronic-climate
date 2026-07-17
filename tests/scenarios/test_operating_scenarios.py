"""Executable named scenarios from the implementation plan."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hydronic_climate_core.model import (
    Circuit,
    DeliveryRoute,
    PlantConfiguration,
    Pump,
    PumpState,
    Valve,
    ValveState,
    Zone,
)
from hydronic_climate_core.topology import compile_topology

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
