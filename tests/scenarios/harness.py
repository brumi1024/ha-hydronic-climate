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
    humidities: Mapping[str, float] = field(default_factory=dict)
    supply_temperatures: Mapping[str, float] = field(default_factory=dict)
    surface_temperatures: Mapping[str, float] = field(default_factory=dict)
    valves: Mapping[str, ValveState] = field(default_factory=dict)
    pumps: Mapping[str, PumpState] = field(default_factory=dict)
    commands: frozenset[tuple[str, str]] = frozenset()
    observations: Mapping[str, TemperatureObservation] | None = None
    zone_demands: Mapping[str, bool] = field(default_factory=dict)
    zone_statuses: Mapping[str, ZoneDecisionStatus] = field(default_factory=dict)
    source_temperatures: Mapping[str, TemperatureObservation] = field(default_factory=dict)
    source_availability: Mapping[str, bool] = field(default_factory=dict)
    check_source: bool = False
    source_id: str | None = None
    source_explanation: str | None = None
    cooling_zone_demands: Mapping[str, bool] = field(default_factory=dict)
    cooling_zone_statuses: Mapping[str, ZoneDecisionStatus] = field(default_factory=dict)
    mode_conflict_codes: tuple[str, ...] = ()


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
            temperatures=(
                step.observations
                if step.observations is not None
                else {
                    entity_id: TemperatureObservation(value, now)
                    for entity_id, value in step.temperatures.items()
                }
            ),
            humidities={
                entity_id: TemperatureObservation(value, now)
                for entity_id, value in step.humidities.items()
            },
            supply_temperatures={
                entity_id: TemperatureObservation(value, now)
                for entity_id, value in step.supply_temperatures.items()
            },
            surface_temperatures={
                entity_id: TemperatureObservation(value, now)
                for entity_id, value in step.surface_temperatures.items()
            },
            source_temperatures=step.source_temperatures,
            source_availability=step.source_availability,
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
        if step.check_source:
            recommendation = result.diagnostics.source_recommendation
            assert recommendation is not None
            assert recommendation.source_id == step.source_id
            if step.source_explanation is not None:
                assert step.source_explanation in recommendation.explanation
        if step.cooling_zone_demands:
            assert {
                zone_id: result.next_runtime.cooling_zone_demands[zone_id]
                for zone_id in step.cooling_zone_demands
            } == step.cooling_zone_demands
        if step.cooling_zone_statuses:
            assert {
                zone_id: result.diagnostics.cooling_zone_decisions[zone_id].status
                for zone_id in step.cooling_zone_statuses
            } == step.cooling_zone_statuses
        if step.mode_conflict_codes:
            assert tuple(conflict.code for conflict in result.diagnostics.mode_conflicts) == (
                step.mode_conflict_codes
            )
        runtime = result.next_runtime
