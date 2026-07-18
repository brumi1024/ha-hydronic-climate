"""Tests for the explicit, generic actuator executor seam."""

from __future__ import annotations

import pytest
from hydronicus_core.executor import (
    ActuatorBinding,
    ActuatorExecutor,
    ActuatorObservedState,
    ActuatorOperation,
    observed_state_for,
    operation_for,
)
from hydronicus_core.model import (
    ActuatorCommand,
    CompiledPlant,
    ControlPlan,
    Pump,
    Valve,
)


def _plan(*commands: ActuatorCommand) -> ControlPlan:
    """Build the smallest immutable control plan accepted by the executor."""
    return ControlPlan(
        commands=commands,
        valve_consumers={},
        pump_consumers={},
    )


@pytest.mark.parametrize(
    ("entity_id", "action", "domain", "service", "target"),
    [
        (
            "switch.floor_valve",
            "open",
            "switch",
            "turn_on",
            ActuatorObservedState.ON,
        ),
        (
            "switch.floor_valve",
            "close",
            "switch",
            "turn_off",
            ActuatorObservedState.OFF,
        ),
        (
            "valve.floor_valve",
            "open",
            "valve",
            "open_valve",
            ActuatorObservedState.OPEN,
        ),
        (
            "valve.floor_valve",
            "close",
            "valve",
            "close_valve",
            ActuatorObservedState.CLOSED,
        ),
    ],
)
def test_generic_adapter_translates_only_explicit_services(
    entity_id: str,
    action: str,
    domain: str,
    service: str,
    target: ActuatorObservedState,
) -> None:
    """Switch and native valve entities receive their domain-specific explicit operation."""
    operation = operation_for(
        ActuatorCommand("valve", action, "synthetic demand"),
        ActuatorBinding("valve", entity_id),
    )

    assert operation == ActuatorOperation(
        "valve",
        entity_id,
        domain,
        service,
        target,
    )
    assert operation.service != "toggle"


def test_native_valve_rejects_switch_only_actions() -> None:
    """A native valve must not be driven through an invented turn-on operation."""
    with pytest.raises(ValueError, match="requires open or close"):
        operation_for(
            ActuatorCommand("valve", "turn_on", "synthetic demand"),
            ActuatorBinding("valve", "valve.floor_valve"),
        )


def test_invalid_entity_and_observation_states_fail_closed() -> None:
    """Unknown entity domains and transitional feedback never become a desired state."""
    with pytest.raises(ValueError, match="switch or valve domain"):
        operation_for(
            ActuatorCommand("valve", "open", "synthetic demand"),
            ActuatorBinding("valve", "light.floor_valve"),
        )

    assert observed_state_for("switch.floor_valve", None) is ActuatorObservedState.UNKNOWN
    assert observed_state_for("switch.floor_valve", "unavailable") is ActuatorObservedState.UNKNOWN
    assert observed_state_for("valve.floor_valve", "opening") is ActuatorObservedState.UNKNOWN
    assert observed_state_for("valve.floor_valve", "open") is ActuatorObservedState.OPEN


def test_from_plant_builds_bindings_for_both_actuator_families() -> None:
    """The adapter sees every compiled valve and pump without household-specific logic."""
    plant = CompiledPlant(
        id="plant",
        zones={},
        valves={"valve": Valve("valve", "Valve", "valve.floor")},
        pumps={"pump": Pump("pump", "Pump", "switch.floor_pump")},
        circuits={},
        routes=(),
        logic_summary=(),
    )

    executor = ActuatorExecutor.from_plant(plant, shadow_mode=False)

    assert executor.bindings == {
        "valve": ActuatorBinding("valve", "valve.floor"),
        "pump": ActuatorBinding("pump", "switch.floor_pump"),
    }
    executor.observe_entity_state("sensor.unrelated", "18.0")
    with pytest.raises(KeyError, match="Unknown actuator"):
        executor.actuator_state("missing")


@pytest.mark.asyncio
async def test_executor_suppresses_an_already_satisfied_command() -> None:
    """Executing the same desired operation twice results in one dispatch."""
    executor = ActuatorExecutor(
        {"valve": ActuatorBinding("valve", "switch.floor_valve")},
        shadow_mode=False,
    )
    dispatched: list[ActuatorOperation] = []

    async def dispatch(operation: ActuatorOperation) -> None:
        dispatched.append(operation)

    first = await executor.async_execute(
        _plan(ActuatorCommand("valve", "open", "synthetic demand")),
        dispatch,
    )
    second = await executor.async_execute(
        _plan(ActuatorCommand("valve", "open", "synthetic demand")),
        dispatch,
    )

    assert len(dispatched) == 1
    assert first.executed == tuple(dispatched)
    assert second.suppressed == tuple(dispatched)
    assert second.shadowed == ()


@pytest.mark.asyncio
async def test_global_shadow_preserves_commands_without_dispatching() -> None:
    """Global shadow mode leaves the plan untouched while suppressing all calls."""
    executor = ActuatorExecutor(
        {"valve": ActuatorBinding("valve", "switch.floor_valve")},
        shadow_mode=True,
    )
    dispatched: list[ActuatorOperation] = []

    async def dispatch(operation: ActuatorOperation) -> None:
        dispatched.append(operation)

    report = await executor.async_execute(
        _plan(ActuatorCommand("valve", "open", "synthetic demand")),
        dispatch,
    )

    assert dispatched == []
    assert len(report.shadowed) == 1
    assert executor.actuator_state("valve") is ActuatorObservedState.UNKNOWN


@pytest.mark.asyncio
async def test_forced_shadow_preserves_commands_without_dispatching() -> None:
    """A cooling-only shadow boundary suppresses calls even when heating is active."""
    executor = ActuatorExecutor(
        {"valve": ActuatorBinding("valve", "switch.floor_valve")},
        shadow_mode=False,
    )
    dispatched: list[ActuatorOperation] = []

    async def dispatch(operation: ActuatorOperation) -> None:
        dispatched.append(operation)

    report = await executor.async_execute(
        _plan(ActuatorCommand("valve", "open", "cooling demand")),
        dispatch,
        force_shadow=True,
    )

    assert dispatched == []
    assert [operation.actuator_id for operation in report.shadowed] == ["valve"]
    assert executor.actuator_state("valve") is ActuatorObservedState.UNKNOWN


@pytest.mark.asyncio
async def test_per_actuator_shadow_only_suppresses_that_actuator() -> None:
    """One shadowed actuator does not block another physical operation."""
    executor = ActuatorExecutor(
        {
            "shadowed": ActuatorBinding("shadowed", "switch.shadowed_valve"),
            "active": ActuatorBinding("active", "switch.active_valve"),
        },
        shadow_mode=False,
        actuator_shadow_modes={"shadowed": True},
    )
    dispatched: list[ActuatorOperation] = []

    async def dispatch(operation: ActuatorOperation) -> None:
        dispatched.append(operation)

    report = await executor.async_execute(
        _plan(
            ActuatorCommand("shadowed", "open", "shadowed demand"),
            ActuatorCommand("active", "open", "active demand"),
        ),
        dispatch,
    )

    assert [operation.actuator_id for operation in report.shadowed] == ["shadowed"]
    assert [operation.actuator_id for operation in dispatched] == ["active"]
    assert executor.actuator_state("shadowed") is ActuatorObservedState.UNKNOWN
    assert executor.actuator_state("active") is ActuatorObservedState.ON


def test_reload_starts_unknown_and_reconcile_uses_only_observed_state() -> None:
    """A new executor never restores a previous optimistic command as observed state."""
    bindings = {"valve": ActuatorBinding("valve", "switch.floor_valve")}
    executor = ActuatorExecutor(bindings, shadow_mode=False)

    assert executor.actuator_state("valve") is ActuatorObservedState.UNKNOWN

    executor.observe_entities({"switch.floor_valve": "unavailable"})
    assert executor.actuator_state("valve") is ActuatorObservedState.UNKNOWN

    executor.observe_entities({"switch.floor_valve": "on"})
    assert executor.actuator_state("valve") is ActuatorObservedState.ON
