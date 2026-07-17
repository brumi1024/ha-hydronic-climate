"""Tests for the frozen cross-milestone controller contracts."""

from __future__ import annotations

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
