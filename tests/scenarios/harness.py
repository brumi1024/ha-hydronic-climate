"""Small scenario runner over the pure controller seam."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from hydronicus_core.controller import evaluate
from hydronicus_core.model import (
    PlantSnapshot,
    PumpState,
    RuntimeState,
    TemperatureObservation,
    ValveState,
    ZoneDecisionStatus,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from hydronicus_core.model import CompiledPlant


@dataclass(frozen=True, slots=True)
class ScenarioStep:
    """One externally observable transition in a named operating scenario."""

    after: timedelta
    temperatures: Mapping[str, float]
    valves: Mapping[str, ValveState] = field(default_factory=dict)
    pumps: Mapping[str, PumpState] = field(default_factory=dict)
    commands: frozenset[tuple[str, str]] = frozenset()
    observations: Mapping[str, TemperatureObservation] | None = None
    zone_demands: Mapping[str, bool] = field(default_factory=dict)
    zone_statuses: Mapping[str, ZoneDecisionStatus] = field(default_factory=dict)


def run_scenario(
    plant: CompiledPlant,
    *,
    started_at: datetime,
    steps: tuple[ScenarioStep, ...],
) -> None:
    """Evaluate steps with a fake clock and assert only public control results."""
    runtime = RuntimeState()
    now = started_at
    for step in steps:
        now += step.after
        snapshot = PlantSnapshot(
            step.observations
            if step.observations is not None
            else {
                entity_id: TemperatureObservation(value, now)
                for entity_id, value in step.temperatures.items()
            }
        )
        result = evaluate(plant, snapshot, runtime, now)
        assert {
            actuator_id: result.next_runtime.valves[actuator_id].state
            for actuator_id in step.valves
        } == step.valves
        assert {
            actuator_id: result.next_runtime.pumps[actuator_id].state for actuator_id in step.pumps
        } == step.pumps
        assert (
            frozenset(
                (command.actuator_id, command.action) for command in result.control_plan.commands
            )
            == step.commands
        )
        if step.zone_demands:
            assert {
                zone_id: result.next_runtime.zone_demands[zone_id] for zone_id in step.zone_demands
            } == step.zone_demands
        if step.zone_statuses:
            assert {
                zone_id: result.diagnostics.zone_decisions[zone_id].status
                for zone_id in step.zone_statuses
            } == step.zone_statuses
        runtime = result.next_runtime
