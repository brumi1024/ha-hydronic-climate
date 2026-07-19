"""Deterministic actuator feedback, mismatch, and safe-shutdown tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hydronicus_core.controller import (
    _feedback_active,
    _feedback_boolean,
    _position_state,
    evaluate,
    safe_shutdown,
)
from hydronicus_core.model import (
    ActuatorFeedback,
    ActuatorFeedbackStatus,
    Circuit,
    DeliveryRoute,
    FeedbackObservation,
    PlantConfiguration,
    PlantMode,
    PlantSnapshot,
    Pump,
    PumpRuntime,
    PumpState,
    RuntimeState,
    SafeShutdownPhase,
    Source,
    TemperatureObservation,
    Valve,
    ValveRuntime,
    ValveState,
    Zone,
)
from hydronicus_core.topology import compile_topology

NOW = datetime(2026, 7, 18, tzinfo=UTC)


def _plant(
    *,
    valve_position: str | None = None,
    pump_power: str | None = None,
    pump_flow: str | None = None,
    pump_fault: str | None = None,
    pump_overrun: float = 10.0,
) -> object:
    """Build a small synthetic plant with independently optional feedback."""
    return compile_topology(
        PlantConfiguration(
            id="feedback-plant",
            zones=(Zone("zone", "Zone", 21.0, ("sensor.zone",)),),
            valves=(
                Valve(
                    "valve",
                    "Valve",
                    "switch.valve",
                    0,
                    position_entity_id=valve_position,
                    position_max_age_seconds=5,
                ),
            ),
            pumps=(
                Pump(
                    "pump",
                    "Pump",
                    "switch.pump",
                    pump_overrun,
                    power_entity_id=pump_power,
                    power_max_age_seconds=5,
                    flow_entity_id=pump_flow,
                    flow_max_age_seconds=5,
                    fault_entity_id=pump_fault,
                    fault_max_age_seconds=5,
                ),
            ),
            circuits=(Circuit("circuit", "Circuit", ("valve",), "pump"),),
            routes=(DeliveryRoute("route", "zone", "circuit"),),
        )
    )


def _snapshot(
    *,
    actuator_feedback: dict[str, ActuatorFeedback] | None = None,
    observed_at: datetime = NOW,
) -> PlantSnapshot:
    """Build a fresh synthetic snapshot at a controlled time."""
    return PlantSnapshot(
        temperatures={"sensor.zone": TemperatureObservation(18.0, observed_at)},
        actuator_feedback=actuator_feedback or {},
    )


def test_optional_feedback_is_independently_decoded() -> None:
    """Valve position, power, flow, and fault bindings can be selected separately."""
    plant = compile_topology(
        PlantConfiguration(
            id="configured",
            zones=(Zone("zone", "Zone", 21.0, ("sensor.zone",)),),
            valves=(Valve("valve", "Valve", "switch.valve", position_entity_id="sensor.position"),),
            pumps=(
                Pump(
                    "pump",
                    "Pump",
                    "switch.pump",
                    power_entity_id="sensor.power",
                    flow_entity_id="sensor.flow",
                    fault_entity_id="binary_sensor.fault",
                ),
            ),
            circuits=(Circuit("circuit", "Circuit", ("valve",), "pump"),),
            routes=(DeliveryRoute("route", "zone", "circuit"),),
        )
    )
    assert plant.valves["valve"].position_entity_id == "sensor.position"
    assert plant.valves["valve"].position_max_age_seconds == 1800.0
    assert plant.pumps["pump"].power_entity_id == "sensor.power"
    assert plant.pumps["pump"].power_max_age_seconds == 1800.0
    assert plant.pumps["pump"].flow_entity_id == "sensor.flow"
    assert plant.pumps["pump"].flow_max_age_seconds == 1800.0
    assert plant.pumps["pump"].fault_entity_id == "binary_sensor.fault"
    assert plant.pumps["pump"].fault_max_age_seconds == 1800.0


@pytest.mark.parametrize(
    ("value", "boolean", "active"),
    [
        (True, True, True),
        (False, False, False),
        (1.0, True, True),
        (0.0, False, False),
        ("fault", True, True),
        ("clear", False, False),
        ("active", True, True),
        ("inactive", False, False),
        ("indeterminate", None, None),
    ],
)
def test_feedback_boolean_and_activity_decoders_are_conservative(
    value: float | bool | str,
    boolean: bool | None,
    active: bool | None,
) -> None:
    """Known feedback encodings are accepted while ambiguous values stay unknown."""
    assert _feedback_boolean(value) is boolean
    assert _feedback_active(value) is active


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "open"),
        (False, "closed"),
        (100.0, "open"),
        (0.0, "closed"),
        (50.0, None),
        ("open", "open"),
        ("opening", None),
        ("closed", "closed"),
        ("closing", None),
        ("unknown", None),
    ],
)
def test_valve_position_decoder_keeps_transitional_states_unsafe(
    value: float | bool | str,
    expected: str | None,
) -> None:
    """Only settled open or closed feedback can establish a valve state."""
    assert _position_state(value) == expected


def test_position_feedback_confirms_readiness_without_open_loop_delay() -> None:
    """A fresh open position observation makes the dependent circuit ready."""
    plant = _plant(valve_position="sensor.position")
    result = evaluate(
        plant,
        _snapshot(
            actuator_feedback={
                "valve": ActuatorFeedback(
                    position=FeedbackObservation("open", NOW),
                )
            }
        ),
        RuntimeState(),
        NOW,
    )
    assert result.next_runtime.valves["valve"].state is ValveState.OPEN
    assert ("pump", "turn_on") in {
        (command.actuator_id, command.action.value) for command in result.control_plan.commands
    }
    assert result.diagnostics.actuator_diagnostics["valve"].status is ActuatorFeedbackStatus.HEALTHY


def test_missing_required_position_feedback_fails_closed() -> None:
    """A configured but absent valve position prevents the dependent pump path."""
    plant = _plant(valve_position="sensor.position")
    result = evaluate(plant, _snapshot(), RuntimeState(), NOW)
    diagnostic = result.diagnostics.actuator_diagnostics["valve"]
    assert diagnostic.blocked
    assert "missing" in diagnostic.reason
    assert result.control_plan.pump_consumers == {}
    assert "required valve feedback" in result.diagnostics.circuit_reasons["circuit"]


def test_stale_required_feedback_fails_closed() -> None:
    """A stale configured valve position remains unsafe even when its value is open."""
    plant = _plant(valve_position="sensor.position")
    result = evaluate(
        plant,
        _snapshot(
            actuator_feedback={
                "valve": ActuatorFeedback(
                    position=FeedbackObservation("open", NOW - timedelta(seconds=6)),
                )
            }
        ),
        RuntimeState(),
        NOW,
    )
    diagnostic = result.diagnostics.actuator_diagnostics["valve"]
    assert diagnostic.blocked
    assert diagnostic.stale_feedback == ("position",)


def test_missing_timestamp_and_manual_valve_mismatch_are_explained() -> None:
    """Missing timestamps fail closed, while a settled manual mismatch is visible."""
    plant = _plant(valve_position="sensor.position")
    missing_timestamp = evaluate(
        plant,
        _snapshot(
            actuator_feedback={
                "valve": ActuatorFeedback(
                    position=FeedbackObservation("open", None),
                )
            }
        ),
        RuntimeState(),
        NOW,
    )
    assert missing_timestamp.diagnostics.actuator_diagnostics["valve"].blocked
    assert "timestamp" in missing_timestamp.diagnostics.actuator_diagnostics["valve"].reason

    mismatch = evaluate(
        plant,
        PlantSnapshot(
            temperatures={"sensor.zone": TemperatureObservation(22.0, NOW)},
            actuator_feedback={
                "valve": ActuatorFeedback(
                    position=FeedbackObservation("open", NOW),
                )
            },
        ),
        RuntimeState(),
        NOW,
    )
    diagnostic = mismatch.diagnostics.mismatches["valve"]
    assert diagnostic.status is ActuatorFeedbackStatus.MISMATCH
    assert not diagnostic.blocked
    assert diagnostic.is_mismatch
    assert not diagnostic.dependent_blocked


def test_healthy_independent_pump_feedback_is_not_a_block() -> None:
    """Fresh power, flow, and clear-fault observations can independently agree."""
    plant = _plant(
        valve_position="sensor.position",
        pump_power="sensor.power",
        pump_flow="sensor.flow",
        pump_fault="binary_sensor.fault",
    )
    result = evaluate(
        plant,
        PlantSnapshot(
            temperatures={"sensor.zone": TemperatureObservation(22.0, NOW)},
            actuator_feedback={
                "valve": ActuatorFeedback(position=FeedbackObservation("closed", NOW)),
                "pump": ActuatorFeedback(
                    power=FeedbackObservation(0.0, NOW),
                    flow=FeedbackObservation(0.0, NOW),
                    fault=FeedbackObservation("clear", NOW),
                ),
            },
        ),
        RuntimeState(),
        NOW,
    )
    assert result.diagnostics.actuator_diagnostics["valve"].status is ActuatorFeedbackStatus.HEALTHY
    assert result.diagnostics.actuator_diagnostics["pump"].status is ActuatorFeedbackStatus.HEALTHY


def test_unknown_pump_feedback_blocks_and_explains_manual_state() -> None:
    """Unknown required fault feedback blocks while unknown power remains diagnostic."""
    plant = _plant(
        pump_power="sensor.power",
        pump_flow="sensor.flow",
        pump_fault="binary_sensor.fault",
    )
    result = evaluate(
        plant,
        _snapshot(
            actuator_feedback={
                "pump": ActuatorFeedback(
                    power=FeedbackObservation("indeterminate", NOW),
                    flow=FeedbackObservation(1.0, NOW),
                    fault=FeedbackObservation("indeterminate", NOW),
                )
            }
        ),
        RuntimeState(),
        NOW,
    )
    diagnostic = result.diagnostics.actuator_diagnostics["pump"]
    assert diagnostic.status is ActuatorFeedbackStatus.BLOCKED
    assert diagnostic.mismatch
    assert diagnostic.blocked
    assert "unknown" in diagnostic.reason


def test_faulted_pump_blocks_only_its_dependent_circuit() -> None:
    """One faulted pump does not remove an unrelated circuit consumer."""
    plant = compile_topology(
        PlantConfiguration(
            id="two-pump-plant",
            zones=(
                Zone("one", "One", 21.0, ("sensor.one",)),
                Zone("two", "Two", 21.0, ("sensor.two",)),
            ),
            valves=(
                Valve("valve-one", "Valve one", "switch.valve_one", 0),
                Valve("valve-two", "Valve two", "switch.valve_two", 0),
            ),
            pumps=(
                Pump(
                    "pump-one",
                    "Pump one",
                    "switch.pump_one",
                    0,
                    fault_entity_id="binary_sensor.fault_one",
                ),
                Pump("pump-two", "Pump two", "switch.pump_two", 0),
            ),
            circuits=(
                Circuit("circuit-one", "Circuit one", ("valve-one",), "pump-one"),
                Circuit("circuit-two", "Circuit two", ("valve-two",), "pump-two"),
            ),
            routes=(
                DeliveryRoute("route-one", "one", "circuit-one"),
                DeliveryRoute("route-two", "two", "circuit-two"),
            ),
        )
    )
    snapshot = PlantSnapshot(
        temperatures={
            "sensor.one": TemperatureObservation(18.0, NOW),
            "sensor.two": TemperatureObservation(18.0, NOW),
        },
        actuator_feedback={
            "pump-one": ActuatorFeedback(fault=FeedbackObservation(True, NOW)),
        },
    )
    first = evaluate(plant, snapshot, RuntimeState(), NOW)
    second = evaluate(plant, snapshot, first.next_runtime, NOW)
    assert "circuit-one" in second.diagnostics.circuit_reasons
    assert "fault" in second.diagnostics.circuit_reasons["circuit-one"]
    assert second.control_plan.pump_consumers["pump-two"] == frozenset({"circuit-two"})
    assert all(command.actuator_id != "pump-one" for command in second.control_plan.commands)


def test_safe_shutdown_stops_zero_overrun_pump_before_closing_valve() -> None:
    """A zero-overrun pump stops in one phase before the valve closes."""
    plant = _plant(pump_overrun=0)
    runtime = RuntimeState(
        valves={"valve": ValveRuntime(ValveState.OPEN, NOW)},
        pumps={"pump": PumpRuntime(PumpState.RUNNING, NOW)},
    )
    stop, runtime = safe_shutdown(plant, runtime, NOW)
    assert stop.phase is SafeShutdownPhase.PUMPS_STOPPED
    assert [command.actuator_id for command in stop.commands] == ["pump"]
    close, _runtime = safe_shutdown(plant, runtime, NOW)
    assert close.phase is SafeShutdownPhase.VALVES_CLOSED
    assert [command.actuator_id for command in close.commands] == ["valve"]


def test_manual_pump_override_is_detected_without_toggle() -> None:
    """A valid but inactive power signal exposes a mismatch and keeps explicit commands."""
    plant = _plant(pump_power="sensor.pump_power")
    feedback = {"pump": ActuatorFeedback(power=FeedbackObservation(0.0, NOW))}
    first = evaluate(plant, _snapshot(actuator_feedback=feedback), RuntimeState(), NOW)
    second = evaluate(plant, _snapshot(actuator_feedback=feedback), first.next_runtime, NOW)
    diagnostic = second.diagnostics.mismatches["pump"]
    assert diagnostic.mismatch
    assert not diagnostic.blocked
    assert any(
        command.actuator_id == "pump" and command.action.value == "turn_on"
        for command in second.control_plan.commands
    )
    assert all(command.action.value != "toggle" for command in second.control_plan.commands)


def test_safe_shutdown_releases_source_then_overruns_then_closes() -> None:
    """Shutdown commands are ordered, explicit, and idempotent across fake-clock phases."""
    plant = compile_topology(
        PlantConfiguration(
            id="shutdown-plant",
            zones=(Zone("zone", "Zone", 21.0, ("sensor.zone",)),),
            valves=(Valve("valve", "Valve", "switch.valve"),),
            pumps=(Pump("pump", "Pump", "switch.pump", 10),),
            circuits=(Circuit("circuit", "Circuit", ("valve",), "pump"),),
            routes=(DeliveryRoute("route", "zone", "circuit"),),
            sources=(Source("source", "Source", demand_entity_id="switch.source_demand"),),
        )
    )
    runtime = RuntimeState(
        plant_mode=PlantMode.HEATING,
        valves={"valve": ValveRuntime(ValveState.OPEN, NOW)},
        pumps={"pump": PumpRuntime(PumpState.RUNNING, NOW)},
    )
    source_release, runtime = safe_shutdown(plant, runtime, NOW)
    assert source_release.phase is SafeShutdownPhase.PUMP_OVERRUN
    assert [command.actuator_id for command in source_release.commands] == ["source:source"]
    waiting, runtime = safe_shutdown(plant, runtime, NOW + timedelta(seconds=5))
    assert waiting.phase is SafeShutdownPhase.PUMP_OVERRUN
    assert waiting.commands == ()
    stop_pumps, runtime = safe_shutdown(plant, runtime, NOW + timedelta(seconds=10))
    assert stop_pumps.phase is SafeShutdownPhase.PUMPS_STOPPED
    assert [command.actuator_id for command in stop_pumps.commands] == ["pump"]
    close_valves, runtime = safe_shutdown(plant, runtime, NOW + timedelta(seconds=11))
    assert close_valves.phase is SafeShutdownPhase.VALVES_CLOSED
    assert [command.actuator_id for command in close_valves.commands] == ["valve"]
    done, _runtime = safe_shutdown(plant, runtime, NOW + timedelta(seconds=12))
    assert done.commands == ()


def test_safe_shutdown_is_deterministic_for_idle_runtime() -> None:
    """An already-safe plant returns the same empty final plan repeatedly."""
    plant = _plant()
    first, runtime = safe_shutdown(plant, RuntimeState(), NOW)
    second, _runtime = safe_shutdown(plant, runtime, NOW)
    assert first.phase is SafeShutdownPhase.VALVES_CLOSED
    assert second.phase is SafeShutdownPhase.VALVES_CLOSED
    assert first == second
