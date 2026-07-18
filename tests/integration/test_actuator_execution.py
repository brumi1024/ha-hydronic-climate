"""Integration tests for generic actuator service execution."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronicus.const import (
    CONF_ACTUATOR_SHADOW_MODES,
    CONF_NAME,
    CONF_PLANT_ID,
    CONF_SHADOW_MODE,
    DOMAIN,
)
from custom_components.hydronicus.core.executor import ActuatorObservedState
from custom_components.hydronicus.core.model import ActuatorAction
from custom_components.hydronicus.runtime import HydronicRuntime

PLANT_ID = "00000000-0000-4000-8000-000000000001"
ZONE_ID = "00000000-0000-4000-8000-000000000002"
VALVE_ID = "00000000-0000-4000-8000-000000000003"
PUMP_ID = "00000000-0000-4000-8000-000000000004"
CIRCUIT_ID = "00000000-0000-4000-8000-000000000005"
ROUTE_ID = "00000000-0000-4000-8000-000000000006"


def _entry(
    *,
    shadow_mode: bool,
    valve_entity_id: str = "switch.synthetic_valve",
    actuator_shadow: bool = False,
    pump_overrun_seconds: float = 300.0,
) -> MockConfigEntry:
    """Build a completely synthetic plant with one generic valve actuator."""
    data = {
        CONF_NAME: "Synthetic plant",
        CONF_PLANT_ID: PLANT_ID,
        CONF_SHADOW_MODE: shadow_mode,
        "topology": {
            "zones": [
                {
                    "id": ZONE_ID,
                    "name": "Synthetic zone",
                    "target_temperature": 21.0,
                    "temperature_sensor": "sensor.synthetic_temperature",
                }
            ],
            "valves": [
                {
                    "id": VALVE_ID,
                    "name": "Synthetic valve",
                    "entity_id": valve_entity_id,
                    "opening_time_seconds": 300.0,
                }
            ],
            "pumps": [
                {
                    "id": PUMP_ID,
                    "name": "Synthetic pump",
                    "entity_id": "switch.synthetic_pump",
                    "overrun_seconds": pump_overrun_seconds,
                }
            ],
            "circuits": [
                {
                    "id": CIRCUIT_ID,
                    "name": "Synthetic circuit",
                    "valve_ids": [VALVE_ID],
                    "pump_id": PUMP_ID,
                }
            ],
            "routes": [
                {
                    "id": ROUTE_ID,
                    "zone_id": ZONE_ID,
                    "circuit_id": CIRCUIT_ID,
                }
            ],
        },
    }
    if actuator_shadow:
        data[CONF_ACTUATOR_SHADOW_MODES] = {VALVE_ID: True}
    return MockConfigEntry(domain=DOMAIN, title="Synthetic plant", data=data)


def _register_recorder(hass, calls: list[tuple[str, str, str]]) -> None:
    """Register synthetic service endpoints without creating physical entities."""

    async def record(call) -> None:
        calls.append((call.domain, call.service, call.data["entity_id"]))

    for domain, service in (
        ("switch", "turn_on"),
        ("switch", "turn_off"),
        ("valve", "open_valve"),
        ("valve", "close_valve"),
    ):
        hass.services.async_register(domain, service, record)


@pytest.mark.parametrize(
    ("entity_id", "initial_state", "expected_domain", "expected_service"),
    [
        ("switch.synthetic_valve", "off", "switch", "turn_on"),
        ("valve.synthetic_valve", "closed", "valve", "open_valve"),
    ],
)
async def test_demand_reaches_the_expected_generic_service_call(
    hass,
    entity_id: str,
    initial_state: str,
    expected_domain: str,
    expected_service: str,
) -> None:
    """A synthetic demand traverses evaluation, runtime, adapter, and service dispatch."""
    calls: list[tuple[str, str, str]] = []
    _register_recorder(hass, calls)
    hass.states.async_set("sensor.synthetic_temperature", "18.0")
    hass.states.async_set(entity_id, initial_state)
    entry = _entry(shadow_mode=False, valve_entity_id=entity_id)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    runtime = entry.runtime_data
    assert runtime.evaluation is not None
    assert runtime.evaluation.control_plan.commands[0].action is ActuatorAction.OPEN
    assert calls == [(expected_domain, expected_service, entity_id)]
    assert all(service != "toggle" for _domain, service, _entity in calls)

    await runtime.async_refresh(hass)
    await hass.async_block_till_done()
    assert calls == [(expected_domain, expected_service, entity_id)]


async def test_global_shadow_keeps_the_desired_plan_without_service_calls(hass) -> None:
    """Global shadow mode preserves the command and explanation while issuing no call."""
    hass.states.async_set("sensor.synthetic_temperature", "18.0")
    hass.states.async_set("switch.synthetic_valve", "off")
    entry = _entry(shadow_mode=True)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    runtime = entry.runtime_data
    assert runtime.evaluation is not None
    assert runtime.evaluation.control_plan.commands[0].action is ActuatorAction.OPEN
    assert runtime.evaluation.diagnostics.actuator_reasons[VALVE_ID].startswith("Opening")
    assert runtime.last_execution is not None
    assert [operation.actuator_id for operation in runtime.last_execution.shadowed] == [VALVE_ID]


async def test_per_actuator_shadow_suppresses_only_the_selected_command(hass) -> None:
    """A per-actuator shadow flag does not alter the desired control plan."""
    calls: list[tuple[str, str, str]] = []
    _register_recorder(hass, calls)
    hass.states.async_set("sensor.synthetic_temperature", "18.0")
    hass.states.async_set("switch.synthetic_valve", "off")
    entry = _entry(shadow_mode=False, actuator_shadow=True)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    runtime = entry.runtime_data
    assert runtime.evaluation is not None
    assert runtime.evaluation.control_plan.commands[0].actuator_id == VALVE_ID
    assert runtime.last_execution is not None
    assert [operation.actuator_id for operation in runtime.last_execution.shadowed] == [VALVE_ID]
    assert calls == []


async def test_reload_reconstructs_unknown_state_when_feedback_is_not_trustworthy(hass) -> None:
    """Reload does not restore a prior command as an observed physical state."""
    calls: list[tuple[str, str, str]] = []
    _register_recorder(hass, calls)
    hass.states.async_set("sensor.synthetic_temperature", "18.0")
    hass.states.async_set("switch.synthetic_valve", "off")
    entry = _entry(shadow_mode=False)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.executor.actuator_state(VALVE_ID) is ActuatorObservedState.ON

    hass.states.async_set("sensor.synthetic_temperature", "22.0")
    hass.states.async_set("switch.synthetic_valve", "unknown")
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert isinstance(entry.runtime_data, HydronicRuntime)
    assert entry.runtime_data.executor.actuator_state(VALVE_ID) is ActuatorObservedState.UNKNOWN


async def test_reload_reconciles_observed_active_actuators_before_idle_shutdown(hass) -> None:
    """Observed active equipment is reconciled into the virtual state before shutdown."""
    calls: list[tuple[str, str, str]] = []
    _register_recorder(hass, calls)
    hass.states.async_set("sensor.synthetic_temperature", "22.0")
    hass.states.async_set("switch.synthetic_valve", "on")
    hass.states.async_set("switch.synthetic_pump", "on")
    entry = _entry(shadow_mode=False, pump_overrun_seconds=0.0)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert ("switch", "turn_off", "switch.synthetic_pump") in calls
    assert ("switch", "turn_off", "switch.synthetic_valve") in calls
