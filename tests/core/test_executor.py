"""Tests for the explicit, generic actuator executor seam."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hydronicus_core.executor import (
    ActuatorBinding,
    ActuatorExecutor,
    ActuatorFailureKind,
    ActuatorObservedState,
    ActuatorOperation,
    observed_state_for,
    operation_for,
)
from hydronicus_core.model import (
    ActuatorAction,
    ActuatorCommand,
    CompiledPlant,
    ControlPlan,
    Pump,
    RuntimeState,
    Source,
    SourceSelectionActuator,
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


@pytest.mark.asyncio
async def test_source_selector_translates_select_option_and_is_idempotent() -> None:
    """A selector receives one explicit option command and no toggle operation."""
    command = ActuatorCommand("selector", "select", "synthetic source selection", "buffer")
    binding = ActuatorBinding("selector", "select.synthetic_source")
    assert operation_for(command, binding) == ActuatorOperation(
        "selector",
        "select.synthetic_source",
        "select",
        "select_option",
        ActuatorObservedState.SELECTED,
        "buffer",
    )

    executor = ActuatorExecutor({"selector": binding}, shadow_mode=False)
    dispatched: list[ActuatorOperation] = []

    async def dispatch(operation: ActuatorOperation) -> None:
        dispatched.append(operation)

    await executor.async_execute(_plan(command), dispatch)
    second = await executor.async_execute(_plan(command), dispatch)

    assert len(dispatched) == 1
    assert second.suppressed == tuple(dispatched)
    assert all(operation.service != "toggle" for operation in dispatched)


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
async def test_per_source_shadow_preserves_guarded_demand_without_dispatch() -> None:
    """A source may remain synthetic while its guarded demand stays observable."""
    plant = CompiledPlant(
        id="plant",
        zones={},
        valves={},
        pumps={},
        circuits={},
        routes=(),
        logic_summary=(),
        sources={
            "heat-pump": Source(
                "heat-pump",
                "Heat pump",
                demand_entity_id="switch.synthetic_heat_pump",
                shadow_mode=True,
            )
        },
    )
    executor = ActuatorExecutor.from_plant(plant, shadow_mode=False)
    dispatched: list[ActuatorOperation] = []

    async def dispatch(operation: ActuatorOperation) -> None:
        dispatched.append(operation)

    report = await executor.async_execute(
        ControlPlan(
            commands=(
                ActuatorCommand(
                    "source:heat-pump",
                    ActuatorAction.TURN_ON,
                    "Synthetic guarded demand",
                ),
            ),
            valve_consumers={},
            pump_consumers={},
        ),
        dispatch,
    )

    assert dispatched == []
    assert [operation.entity_id for operation in report.shadowed] == [
        "switch.synthetic_heat_pump"
    ]


@pytest.mark.asyncio
async def test_executor_safe_shutdown_dispatches_only_intercepted_source_release() -> None:
    """The executor exposes the pure shutdown plan through the normal dispatch seam."""
    plant = CompiledPlant(
        id="plant",
        zones={},
        valves={},
        pumps={},
        circuits={},
        routes=(),
        logic_summary=(),
        sources={
            "source": Source(
                "source",
                "Source",
                demand_entity_id="switch.source_demand",
            )
        },
    )
    executor = ActuatorExecutor.from_plant(plant, shadow_mode=False)
    dispatched: list[ActuatorOperation] = []

    async def dispatch(operation: ActuatorOperation) -> None:
        dispatched.append(operation)

    report = await executor.async_safe_shutdown(
        plant,
        RuntimeState(),
        datetime(2026, 7, 18, tzinfo=UTC),
        dispatch,
    )

    assert report.plan.phase.value == "valves_closed"
    assert [operation.actuator_id for operation in dispatched] == ["source:source"]
    assert report.execution.executed == tuple(dispatched)


@pytest.mark.asyncio
async def test_executor_safe_shutdown_releases_bound_source_selector() -> None:
    """Safe shutdown releases a bound selector through the explicit select option seam."""
    plant = CompiledPlant(
        id="plant",
        zones={},
        valves={},
        pumps={},
        circuits={},
        routes=(),
        logic_summary=(),
        source_selector=SourceSelectionActuator(
            "selector",
            "Synthetic selector",
            entity_id="select.synthetic_source",
            release_option="none",
            shadow_only=False,
        ),
    )
    executor = ActuatorExecutor.from_plant(plant, shadow_mode=False)
    dispatched: list[ActuatorOperation] = []

    async def dispatch(operation: ActuatorOperation) -> None:
        dispatched.append(operation)

    report = await executor.async_safe_shutdown(
        plant,
        RuntimeState(),
        datetime(2026, 7, 18, tzinfo=UTC),
        dispatch,
    )

    assert [operation.actuator_id for operation in dispatched] == ["selector"]
    assert dispatched[0].service == "select_option"
    assert dispatched[0].target_value == "none"
    assert report.execution.executed == tuple(dispatched)


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
async def test_reconciliation_records_desired_observed_and_retained_state() -> None:
    """Repeated evaluation retains an in-flight request until feedback arrives."""
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

    reconciliation = executor.reconciliations["valve"]
    assert reconciliation.desired is ActuatorObservedState.ON
    assert reconciliation.observed is ActuatorObservedState.UNKNOWN
    assert reconciliation.retained is ActuatorObservedState.ON
    assert reconciliation.status.value == "retained"
    assert first.executed == tuple(dispatched)
    assert second.suppressed == tuple(dispatched)

    executor.observe_entity_state("switch.floor_valve", "on")
    await executor.async_execute(
        _plan(ActuatorCommand("valve", "open", "synthetic demand")),
        dispatch,
    )
    assert executor.reconciliations["valve"].status.value == "observed"


@pytest.mark.asyncio
async def test_rejected_service_call_is_a_deterministic_failure_without_retaining_request() -> None:
    """A rejected call is reported and cannot masquerade as actuator feedback."""
    executor = ActuatorExecutor(
        {"valve": ActuatorBinding("valve", "switch.floor_valve")},
        shadow_mode=False,
    )

    async def reject(_operation: ActuatorOperation) -> None:
        raise RuntimeError("synthetic service rejected")

    report = await executor.async_execute(
        _plan(ActuatorCommand("valve", "open", "synthetic demand")),
        reject,
    )

    assert len(report.failures) == 1
    failure = report.failures[0]
    assert failure.kind is ActuatorFailureKind.REJECTED
    assert "synthetic service rejected" in failure.explanation
    assert executor.requested_state("valve") is None
    assert executor.failure_for("valve") == failure


@pytest.mark.asyncio
async def test_timeout_is_distinguished_from_a_rejected_service_call() -> None:
    """Timeouts retain a stable failure explanation for the adapter."""
    executor = ActuatorExecutor(
        {"valve": ActuatorBinding("valve", "switch.floor_valve")},
        shadow_mode=False,
    )

    async def timeout(_operation: ActuatorOperation) -> None:
        raise TimeoutError("synthetic command timeout")

    report = await executor.async_execute(
        _plan(ActuatorCommand("valve", "open", "synthetic demand")),
        timeout,
    )

    assert report.failures[0].kind is ActuatorFailureKind.TIMEOUT
    assert report.failures[0].explanation == (
        "Command open for actuator valve timed out: synthetic command timeout"
    )


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
async def test_cooling_shadow_suppresses_starts_but_allows_safe_shutdowns() -> None:
    """Cooling starts stay synthetic while shutdown commands can make paths safe."""
    executor = ActuatorExecutor(
        {"pump": ActuatorBinding("pump", "switch.floor_pump")},
        shadow_mode=False,
    )
    dispatched: list[ActuatorOperation] = []

    async def dispatch(operation: ActuatorOperation) -> None:
        dispatched.append(operation)

    report = await executor.async_execute(
        _plan(
            ActuatorCommand("pump", ActuatorAction.TURN_OFF, "safe shutdown"),
            ActuatorCommand("pump", ActuatorAction.TURN_ON, "cooling demand"),
        ),
        dispatch,
        force_shadow_start_actuator_ids=frozenset({"pump"}),
    )

    assert [operation.service for operation in dispatched] == ["turn_off"]
    assert [operation.service for operation in report.shadowed] == ["turn_on"]


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
    assert executor.actuator_state("active") is ActuatorObservedState.UNKNOWN
    assert executor.requested_state("active") is ActuatorObservedState.ON


def test_configured_readiness_feedback_can_satisfy_a_valve_without_waiting_for_timer() -> None:
    """A synthetic end-switch is observed separately from the actuator command state."""
    plant = CompiledPlant(
        id="plant",
        zones={},
        valves={
            "valve": Valve(
                "valve",
                "Valve",
                "switch.floor_valve",
                opening_time_seconds=300,
                readiness_entity_id="binary_sensor.floor_valve_ready",
            )
        },
        pumps={},
        circuits={},
        routes=(),
        logic_summary=(),
    )
    executor = ActuatorExecutor.from_plant(plant, shadow_mode=False)

    executor.observe_entities(
        {
            "switch.floor_valve": "on",
            "binary_sensor.floor_valve_ready": "on",
        }
    )

    assert executor.actuator_state("valve") is ActuatorObservedState.ON
    assert executor.readiness_state("valve") is True

    executor.observe_entity_state("binary_sensor.floor_valve_ready", "off")
    assert executor.readiness_state("valve") is False


def test_native_valve_feedback_and_invalid_executor_references_fail_closed() -> None:
    """Native valve feedback is usable while unknown executor references remain errors."""
    executor = ActuatorExecutor(
        {"valve": ActuatorBinding("valve", "valve.floor_valve")},
        shadow_mode=False,
    )
    executor.observe_entity_state("valve.floor_valve", "open")
    assert executor.readiness_state("valve") is True
    executor.observe_entity_state("valve.floor_valve", "closed")
    assert executor.readiness_state("valve") is False

    switch_executor = ActuatorExecutor(
        {"switch": ActuatorBinding("switch", "switch.floor_valve")},
        shadow_mode=False,
    )
    assert switch_executor.readiness_state("switch") is None

    with pytest.raises(ValueError, match="feedback entity"):
        observed_state_for("sensor.invalid", "on")
    with pytest.raises(KeyError, match="Unknown actuator"):
        executor.requested_state("missing")
    with pytest.raises(KeyError, match="Unknown actuator"):
        executor.readiness_state("missing")


def test_from_plant_rejects_a_valve_and_pump_id_collision() -> None:
    """One actuator UUID cannot ambiguously bind both a valve and a pump."""
    plant = CompiledPlant(
        id="plant",
        zones={},
        valves={"shared": Valve("shared", "Valve", "switch.valve")},
        pumps={"shared": Pump("shared", "Pump", "switch.pump")},
        circuits={},
        routes=(),
        logic_summary=(),
    )

    with pytest.raises(ValueError, match="more than one actuator"):
        ActuatorExecutor.from_plant(plant)


def test_reload_starts_unknown_and_reconcile_uses_only_observed_state() -> None:
    """A new executor never restores a previous optimistic command as observed state."""
    bindings = {"valve": ActuatorBinding("valve", "switch.floor_valve")}
    executor = ActuatorExecutor(bindings, shadow_mode=False)

    assert executor.actuator_state("valve") is ActuatorObservedState.UNKNOWN

    executor.observe_entities({"switch.floor_valve": "unavailable"})
    assert executor.actuator_state("valve") is ActuatorObservedState.UNKNOWN

    executor.observe_entities({"switch.floor_valve": "on"})
    assert executor.actuator_state("valve") is ActuatorObservedState.ON
