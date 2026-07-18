"""Deterministic synthetic source-selection state-machine tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hydronicus_core.configuration import StoredTopologyError, plant_configuration_from_entry_data
from hydronicus_core.controller import evaluate
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
    Source,
    SourceKind,
    SourceSelectionActuator,
    SourceSelectionPhase,
    SourceSelectionRuntime,
    TemperatureObservation,
    Valve,
    ValveRuntime,
    ValveState,
    Zone,
)
from hydronicus_core.topology import TopologyValidationError, compile_topology

NOW = datetime(2026, 7, 18, tzinfo=UTC)


def _configuration(
    *,
    selector_entity: str | None = None,
    source_selector: SourceSelectionActuator | None = None,
) -> PlantConfiguration:
    return PlantConfiguration(
        id="source-selection-plant",
        zones=(Zone("zone", "Zone", 21.0, ("sensor.zone",)),),
        valves=(Valve("valve", "Valve", "switch.synthetic_valve", 0),),
        pumps=(Pump("pump", "Pump", "switch.synthetic_pump", 0),),
        circuits=(Circuit("circuit", "Circuit", ("valve",), "pump"),),
        routes=(DeliveryRoute("route", "zone", "circuit"),),
        sources=(
            Source(
                "buffer",
                "Buffer",
                priority=1,
                kind=SourceKind.TEMPERATURE_QUALIFIED_BUFFER,
                temperature_entity_id="sensor.buffer_temperature",
                minimum_temperature=40.0,
                hysteresis=0.5,
                demand_entity_id="switch.synthetic_buffer",
            ),
            Source(
                "boiler",
                "Boiler",
                priority=2,
                demand_entity_id="switch.synthetic_boiler",
            ),
        ),
        source_selector=source_selector
        or SourceSelectionActuator(
            "selector",
            "Synthetic source selector",
            entity_id=selector_entity,
            break_interval_seconds=10,
            minimum_dwell_seconds=30,
        ),
    )


def _plant(*, selector_entity: str | None = None) -> object:
    return compile_topology(_configuration(selector_entity=selector_entity))


def _snapshot(
    now: datetime,
    *,
    buffer_temperature: float = 45.0,
    buffer_available: bool = True,
    selector: str | None = "none",
    source_demand_states: dict[str, bool] | None = None,
) -> PlantSnapshot:
    return PlantSnapshot(
        temperatures={"sensor.zone": TemperatureObservation(19.0, now)},
        source_temperatures={
            "buffer": TemperatureObservation(buffer_temperature, now),
        },
        source_availability={"buffer": buffer_available},
        source_selector_states={"selector": selector},
        source_demand_states=source_demand_states or {},
    )


def _commands(result) -> tuple[tuple[str, ActuatorAction, str | None], ...]:
    return tuple(
        (command.actuator_id, command.action, command.target)
        for command in result.control_plan.commands
    )


def test_source_selection_waits_for_a_stable_hydraulic_transition() -> None:
    """Valve opening and pump startup block source selection commands."""
    plant = _plant()

    opening = evaluate(plant, _snapshot(NOW), RuntimeState(), NOW)
    assert _commands(opening) == (("valve", ActuatorAction.OPEN, None),)
    assert not any(
        command.actuator_id.startswith("source:") for command in opening.control_plan.commands
    )
    assert opening.diagnostics.source_diagnostics["buffer"].demand_requested is False
    assert opening.diagnostics.source_diagnostics["buffer"].demand_permitted is False
    assert (
        opening.next_runtime.source_selection.phase is SourceSelectionPhase.WAITING_FOR_HYDRAULICS
    )

    pump_start = evaluate(
        plant,
        _snapshot(NOW + timedelta(seconds=1)),
        opening.next_runtime,
        NOW + timedelta(seconds=1),
    )
    assert _commands(pump_start) == (("pump", ActuatorAction.TURN_ON, None),)
    assert (
        pump_start.next_runtime.source_selection.phase
        is SourceSelectionPhase.WAITING_FOR_HYDRAULICS
    )

    stable = evaluate(
        plant,
        _snapshot(NOW + timedelta(seconds=2)),
        pump_start.next_runtime,
        NOW + timedelta(seconds=2),
    )
    assert _commands(stable) == (("source:buffer", ActuatorAction.TURN_ON, None),)
    assert stable.next_runtime.source_selection.phase is SourceSelectionPhase.SELECTING
    assert stable.diagnostics.source_diagnostics["buffer"].demand_requested is True
    assert stable.diagnostics.source_diagnostics["buffer"].demand_permitted is True


def test_source_change_is_break_before_make_and_never_selects_both_sources() -> None:
    """Fallback releases the old source, waits, then selects only the new one."""
    plant = _plant()
    runtime = SourceSelectionRuntime(
        phase=SourceSelectionPhase.ACTIVE,
        active_source_id="buffer",
        target_source_id="buffer",
        last_selected_at=NOW - timedelta(seconds=30),
    )
    starting = RuntimeState(
        selected_source_id="buffer",
        source_selection=runtime,
        valves={"valve": ValveRuntime(ValveState.OPEN, NOW, True)},
        pumps={"pump": PumpRuntime(PumpState.RUNNING, NOW)},
    )

    release = evaluate(
        plant,
        _snapshot(NOW, buffer_available=False),
        starting,
        NOW,
    )
    assert _commands(release) == (("source:buffer", ActuatorAction.TURN_OFF, None),)
    assert release.next_runtime.source_selection.phase is SourceSelectionPhase.BREAKING

    during_break = evaluate(
        plant,
        _snapshot(NOW + timedelta(seconds=9), buffer_available=False, selector="none"),
        release.next_runtime,
        NOW + timedelta(seconds=9),
    )
    assert _commands(during_break) == ()

    select = evaluate(
        plant,
        _snapshot(NOW + timedelta(seconds=10), buffer_available=False, selector="none"),
        during_break.next_runtime,
        NOW + timedelta(seconds=10),
    )
    assert _commands(select) == (("source:boiler", ActuatorAction.TURN_ON, None),)
    assert all(
        not (command.actuator_id == "source:buffer" and command.action is ActuatorAction.TURN_ON)
        for command in select.control_plan.commands
    )


def test_source_selection_honors_dwell_but_allows_unavailable_fallback() -> None:
    """A healthy source stays selected through priority churn, while unavailable data falls back."""
    plant = _plant()
    active = RuntimeState(
        selected_source_id="buffer",
        source_selection=SourceSelectionRuntime(
            phase=SourceSelectionPhase.MINIMUM_DWELL,
            active_source_id="buffer",
            target_source_id="buffer",
            last_selected_at=NOW,
        ),
        valves={"valve": ValveRuntime(ValveState.OPEN, NOW, True)},
        pumps={"pump": PumpRuntime(PumpState.RUNNING, NOW)},
    )

    healthy = evaluate(
        plant,
        _snapshot(NOW + timedelta(seconds=1)),
        active,
        NOW + timedelta(seconds=1),
    )
    assert _commands(healthy) == ()
    assert healthy.next_runtime.source_selection.phase is SourceSelectionPhase.MINIMUM_DWELL

    unavailable = evaluate(
        plant,
        _snapshot(NOW + timedelta(seconds=2), buffer_available=False),
        healthy.next_runtime,
        NOW + timedelta(seconds=2),
    )
    assert _commands(unavailable) == (("source:buffer", ActuatorAction.TURN_OFF, None),)
    assert unavailable.next_runtime.source_selection.phase is SourceSelectionPhase.BREAKING
    assert unavailable.next_runtime.zone_demands["zone"] is True
    assert unavailable.diagnostics.source_diagnostics["buffer"].blocked is True
    assert unavailable.diagnostics.source_diagnostics["boiler"].recommended is True
    assert unavailable.diagnostics.source_diagnostics["boiler"].demand_requested is False


def test_source_selection_transition_is_restart_safe() -> None:
    """A restored break phase does not reselect until its recorded interval expires."""
    plant = _plant()
    restored = RuntimeState(
        selected_source_id="buffer",
        source_selection=SourceSelectionRuntime(
            phase=SourceSelectionPhase.BREAKING,
            active_source_id="buffer",
            target_source_id="boiler",
            transition_started_at=NOW,
        ),
        valves={"valve": ValveRuntime(ValveState.OPEN, NOW, True)},
        pumps={"pump": PumpRuntime(PumpState.RUNNING, NOW)},
    )

    held = evaluate(
        plant,
        _snapshot(NOW + timedelta(seconds=9), buffer_available=False, selector="none"),
        restored,
        NOW + timedelta(seconds=9),
    )
    assert _commands(held) == ()
    assert held.next_runtime.source_selection.phase is SourceSelectionPhase.BREAKING

    resumed = evaluate(
        plant,
        _snapshot(NOW + timedelta(seconds=10), buffer_available=False, selector="none"),
        held.next_runtime,
        NOW + timedelta(seconds=10),
    )
    assert _commands(resumed) == (("source:boiler", ActuatorAction.TURN_ON, None),)


def test_bound_selector_waits_for_release_feedback_before_selecting_new_source() -> None:
    """A stale old selector state cannot collapse break-before-make into a toggle."""
    plant = _plant(selector_entity="select.synthetic_source")
    starting = RuntimeState(
        selected_source_id="buffer",
        source_selection=SourceSelectionRuntime(
            phase=SourceSelectionPhase.ACTIVE,
            active_source_id="buffer",
            target_source_id="buffer",
            last_selected_at=NOW - timedelta(seconds=30),
        ),
        valves={"valve": ValveRuntime(ValveState.OPEN, NOW, True)},
        pumps={"pump": PumpRuntime(PumpState.RUNNING, NOW)},
    )

    release = evaluate(
        plant,
        _snapshot(NOW, buffer_available=False, selector="buffer"),
        starting,
        NOW,
    )
    assert _commands(release) == (("selector", ActuatorAction.SELECT, "none"),)
    assert release.next_runtime.source_selection.phase is SourceSelectionPhase.BREAKING

    stale_feedback = evaluate(
        plant,
        _snapshot(NOW + timedelta(seconds=9), buffer_available=False, selector="buffer"),
        release.next_runtime,
        NOW + timedelta(seconds=9),
    )
    assert _commands(stale_feedback) == ()
    assert stale_feedback.next_runtime.source_selection.phase is SourceSelectionPhase.BREAKING

    select = evaluate(
        plant,
        _snapshot(NOW + timedelta(seconds=10), buffer_available=False, selector="none"),
        stale_feedback.next_runtime,
        NOW + timedelta(seconds=10),
    )
    assert _commands(select) == (("selector", ActuatorAction.SELECT, "boiler"),)
    assert select.next_runtime.source_selection.phase is SourceSelectionPhase.SELECTING

    repeated = evaluate(
        plant,
        _snapshot(NOW + timedelta(seconds=11), buffer_available=False, selector="none"),
        select.next_runtime,
        NOW + timedelta(seconds=11),
    )
    assert _commands(repeated) == ()

    target_feedback = evaluate(
        plant,
        _snapshot(NOW + timedelta(seconds=12), buffer_available=False, selector="boiler"),
        select.next_runtime,
        NOW + timedelta(seconds=12),
    )
    assert _commands(target_feedback) == ()
    assert target_feedback.next_runtime.source_selection.phase is SourceSelectionPhase.MINIMUM_DWELL


@pytest.mark.parametrize("selector", [None, "unconfigured-source"])
def test_bound_selector_unknown_feedback_blocks_execution(selector: str | None) -> None:
    """Unknown or invalid selector feedback fails closed before any selection command."""
    plant = _plant(selector_entity="select.synthetic_source")
    result = evaluate(plant, _snapshot(NOW, selector=selector), RuntimeState(), NOW)

    assert not any(command.actuator_id == "selector" for command in result.control_plan.commands)
    assert result.control_plan.source_selection is not None
    assert result.control_plan.source_selection.hydraulically_safe is False
    assert result.next_runtime.source_selection.phase is SourceSelectionPhase.IDLE


def test_bound_selector_restart_waits_for_break_interval_before_accepting_target_feedback() -> None:
    """A persisted break phase cannot accept target feedback before its safety interval."""
    plant = _plant(selector_entity="select.synthetic_source")
    restored = RuntimeState(
        source_selection=SourceSelectionRuntime(
            phase=SourceSelectionPhase.BREAKING,
            target_source_id="boiler",
            transition_started_at=NOW,
        ),
        valves={"valve": ValveRuntime(ValveState.OPEN, NOW, True)},
        pumps={"pump": PumpRuntime(PumpState.RUNNING, NOW)},
    )

    held = evaluate(
        plant,
        _snapshot(NOW + timedelta(seconds=9), buffer_available=False, selector="boiler"),
        restored,
        NOW + timedelta(seconds=9),
    )
    assert _commands(held) == ()
    assert held.next_runtime.source_selection.phase is SourceSelectionPhase.BREAKING

    accepted = evaluate(
        plant,
        _snapshot(NOW + timedelta(seconds=10), buffer_available=False, selector="boiler"),
        held.next_runtime,
        NOW + timedelta(seconds=10),
    )
    assert _commands(accepted) == ()
    assert accepted.next_runtime.source_selection.phase is SourceSelectionPhase.MINIMUM_DWELL


def test_source_demand_feedback_blocks_multiple_sources_and_confirms_selection() -> None:
    """Demand-output feedback is one-hot and confirms the explicit target."""
    plant = compile_topology(replace(_configuration(), source_selector=None))
    multiple = evaluate(
        plant,
        _snapshot(NOW, source_demand_states={"buffer": True, "boiler": True}),
        RuntimeState(),
        NOW,
    )
    assert multiple.control_plan.source_selection is not None
    assert "multiple source demands" in multiple.control_plan.source_selection.explanation
    assert multiple.next_runtime.source_selection.phase is SourceSelectionPhase.IDLE

    selecting = RuntimeState(
        source_selection=SourceSelectionRuntime(
            phase=SourceSelectionPhase.SELECTING,
            target_source_id="boiler",
            transition_started_at=NOW,
        ),
        valves={"valve": ValveRuntime(ValveState.OPEN, NOW, True)},
        pumps={"pump": PumpRuntime(PumpState.RUNNING, NOW)},
    )
    confirmed = evaluate(
        plant,
        _snapshot(
            NOW + timedelta(seconds=1),
            buffer_available=False,
            source_demand_states={"buffer": False, "boiler": True},
        ),
        selecting,
        NOW + timedelta(seconds=1),
    )
    assert confirmed.control_plan.commands == ()
    assert confirmed.next_runtime.source_selection.phase is SourceSelectionPhase.ACTIVE


def test_source_demand_release_feedback_and_hydraulic_overrun_hold_break_phase() -> None:
    """Break phase waits for both old-demand release and pump overrun completion."""
    plant = compile_topology(replace(_configuration(), source_selector=None))
    breaking = RuntimeState(
        source_selection=SourceSelectionRuntime(
            phase=SourceSelectionPhase.BREAKING,
            target_source_id="boiler",
            transition_started_at=NOW - timedelta(seconds=20),
            released_source_id="buffer",
        ),
        valves={"valve": ValveRuntime(ValveState.OPEN, NOW, True)},
        pumps={"pump": PumpRuntime(PumpState.RUNNING, NOW)},
    )
    release_pending = evaluate(
        plant,
        _snapshot(
            NOW,
            buffer_available=False,
            source_demand_states={"buffer": True, "boiler": False},
        ),
        breaking,
        NOW,
    )
    assert release_pending.control_plan.commands == ()
    assert "old-source release" in release_pending.control_plan.source_selection.explanation

    overrun = replace(
        breaking,
        pumps={"pump": PumpRuntime(PumpState.OVERRUN, NOW)},
    )
    overrun_hold = evaluate(
        plant,
        _snapshot(
            NOW,
            buffer_available=False,
            source_demand_states={"buffer": False, "boiler": False},
        ),
        overrun,
        NOW,
    )
    assert not any(
        command.actuator_id.startswith("source:") for command in overrun_hold.control_plan.commands
    )
    assert overrun_hold.next_runtime.source_selection.phase is SourceSelectionPhase.BREAKING


def test_source_selection_handles_no_eligible_source_and_restored_unknown_source() -> None:
    """A stale transition or removed source resets without issuing a made-up command."""
    plant = _plant()
    no_sources = replace(plant, sources={})
    no_target = RuntimeState(
        source_selection=SourceSelectionRuntime(
            phase=SourceSelectionPhase.BREAKING,
            target_source_id="removed",
            transition_started_at=NOW - timedelta(seconds=20),
        ),
        valves={"valve": ValveRuntime(ValveState.OPEN, NOW, True)},
        pumps={"pump": PumpRuntime(PumpState.RUNNING, NOW)},
    )
    idle = evaluate(no_sources, _snapshot(NOW), no_target, NOW)
    assert idle.control_plan.commands == ()
    assert idle.next_runtime.source_selection.phase is SourceSelectionPhase.IDLE

    unknown = RuntimeState(
        source_selection=SourceSelectionRuntime(
            phase=SourceSelectionPhase.ACTIVE,
            active_source_id="removed",
            target_source_id="removed",
        ),
        valves={"valve": ValveRuntime(ValveState.OPEN, NOW, True)},
        pumps={"pump": PumpRuntime(PumpState.RUNNING, NOW)},
    )
    reset = evaluate(plant, _snapshot(NOW), unknown, NOW)
    assert reset.control_plan.commands == ()
    assert reset.next_runtime.source_selection.phase is SourceSelectionPhase.IDLE


def test_healthy_source_change_honors_minimum_dwell_before_release() -> None:
    """A still-eligible lower-priority source cannot chatter during minimum dwell."""
    plant = _plant()
    runtime = RuntimeState(
        source_selection=SourceSelectionRuntime(
            phase=SourceSelectionPhase.ACTIVE,
            active_source_id="boiler",
            target_source_id="boiler",
            last_selected_at=NOW,
        ),
        valves={"valve": ValveRuntime(ValveState.OPEN, NOW, True)},
        pumps={"pump": PumpRuntime(PumpState.RUNNING, NOW)},
    )
    held = evaluate(
        plant,
        _snapshot(NOW + timedelta(seconds=1)),
        runtime,
        NOW + timedelta(seconds=1),
    )

    assert held.control_plan.commands == ()
    assert held.next_runtime.source_selection.phase is SourceSelectionPhase.MINIMUM_DWELL


@pytest.mark.parametrize(
    ("topology", "message"),
    [
        ({"source_selector": []}, "source selector must be an object"),
        (
            {
                "source_selector": {
                    "id": "selector",
                    "name": "Selector",
                    "entity_id": "select.one",
                    "selector_entity_id": "select.two",
                }
            },
            "provided more than once",
        ),
        (
            {"source_selector": {"id": "selector", "name": "Selector", "release_option": ""}},
            "release option",
        ),
        (
            {"source_selector": {"id": "selector", "name": "Selector", "shadow_only": "yes"}},
            "shadow_only",
        ),
    ],
)
def test_source_selector_configuration_rejects_unsafe_stored_values(
    topology: dict[str, object], message: str
) -> None:
    with pytest.raises(StoredTopologyError, match=message):
        plant_configuration_from_entry_data({"plant_id": "plant", "topology": topology})


@pytest.mark.parametrize(
    ("selector", "message"),
    [
        (SourceSelectionActuator("", "Selector"), "non-empty id"),
        (SourceSelectionActuator("selector", ""), "non-empty name"),
        (SourceSelectionActuator("selector", "Selector", entity_id=""), "entity"),
        (
            SourceSelectionActuator("selector", "Selector", entity_id="switch.selector"),
            "select domain",
        ),
        (
            SourceSelectionActuator("selector", "Selector", break_interval_seconds=-1),
            "break interval",
        ),
        (
            SourceSelectionActuator("selector", "Selector", minimum_dwell_seconds=-1),
            "minimum dwell",
        ),
        (SourceSelectionActuator("selector", "Selector", release_option=""), "release option"),
    ],
)
def test_source_selector_topology_validation_rejects_unsafe_values(
    selector: SourceSelectionActuator, message: str
) -> None:
    with pytest.raises(TopologyValidationError, match=message):
        compile_topology(replace(_configuration(), source_selector=selector))


@pytest.mark.parametrize(
    ("selector", "message"),
    [
        (SourceSelectionActuator("zone", "Selector"), "already used by another object"),
        (
            SourceSelectionActuator("source:buffer", "Selector"),
            "already used by another object",
        ),
    ],
)
def test_source_selector_topology_rejects_binding_collisions(
    selector: SourceSelectionActuator, message: str
) -> None:
    with pytest.raises(TopologyValidationError, match=message):
        compile_topology(replace(_configuration(), source_selector=selector))


def test_source_selector_topology_rejects_source_demand_collision() -> None:
    """A selector cannot share an entity with a source demand output."""
    configuration = _configuration()
    source = replace(configuration.sources[0], demand_entity_id="select.synthetic_source")
    selector = SourceSelectionActuator(
        "selector",
        "Selector",
        entity_id="select.synthetic_source",
    )
    with pytest.raises(TopologyValidationError, match="already used by another actuator"):
        compile_topology(
            replace(
                configuration,
                sources=(source, *configuration.sources[1:]),
                source_selector=selector,
            )
        )


def test_source_selector_configuration_defaults_to_synthetic_execution() -> None:
    """Persisted selector timing is typed without enabling physical control."""
    configuration = plant_configuration_from_entry_data(
        {
            "plant_id": "plant",
            "topology": {
                "source_selector": {
                    "id": "synthetic-selector",
                    "name": "Synthetic selector",
                    "break_seconds": 12,
                    "minimum_source_dwell_seconds": 45,
                }
            },
        }
    )

    assert configuration.source_selector is not None
    assert configuration.source_selector.break_interval_seconds == 12.0
    assert configuration.source_selector.minimum_dwell_seconds == 45.0
    assert configuration.source_selector.shadow_only is True
