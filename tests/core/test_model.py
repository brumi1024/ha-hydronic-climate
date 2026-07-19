"""Tests for the frozen cross-milestone controller contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hydronicus_core.model import (
    ActuatorAction,
    ActuatorCommand,
    InterlockStatus,
    PlantMode,
    PlantSnapshot,
    RuntimeState,
    SafetyInterlockResult,
    SourceRecommendation,
    Valve,
    ValveRuntime,
    ValveState,
)


def test_actuator_commands_normalize_explicit_actions() -> None:
    """Legacy string callers still enter the executor contract as an enum."""
    command = ActuatorCommand("valve", "open", "needs heat")

    assert command.action is ActuatorAction.OPEN

    with pytest.raises(ValueError):
        ActuatorCommand("valve", "toggle", "unsafe operation")


def test_wave_one_extensions_are_optional_for_heating_callers() -> None:
    """Existing heating evaluations can omit cooling and source observations."""
    snapshot = PlantSnapshot(temperatures={})
    runtime = RuntimeState()
    interlock = SafetyInterlockResult("dew-point", InterlockStatus.PERMITTED, "safe")
    recommendation = SourceRecommendation("buffer", "Buffer is eligible", ("buffer",))

    assert snapshot.humidities == {}
    assert runtime.plant_mode is PlantMode.IDLE
    assert interlock.permits is True
    assert recommendation.source_id == "buffer"


def test_valve_readiness_aliases_and_legacy_open_state_are_safe() -> None:
    """Feedback naming remains compatible while explicit readiness stays immutable."""
    valve = Valve(
        "valve",
        "Valve",
        "switch.valve",
        feedback_entity_id="binary_sensor.valve_ready",
    )
    assert valve.readiness_entity_id == "binary_sensor.valve_ready"
    assert valve.readiness_entity_id == "binary_sensor.valve_ready"
    assert ValveRuntime(ValveState.OPEN, datetime(2026, 7, 17, tzinfo=UTC)).is_ready is True
    assert ValveRuntime(ValveState.OPEN, None, False).is_ready is False

    with pytest.raises(ValueError, match="provided more than once"):
        Valve(
            "valve",
            "Valve",
            "switch.valve",
            readiness_entity_id="binary_sensor.one",
            feedback_entity_id="binary_sensor.two",
        )
