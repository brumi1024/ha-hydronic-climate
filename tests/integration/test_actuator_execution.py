"""Integration tests for generic actuator service execution."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronicus.const import (
    CONF_ACTUATOR_SHADOW_MODES,
    CONF_NAME,
    CONF_PLANT_ID,
    CONF_SHADOW_MODE,
    DOMAIN,
)
from custom_components.hydronicus.core.executor import ActuatorFailureKind, ActuatorObservedState
from custom_components.hydronicus.core.model import ActuatorAction
from custom_components.hydronicus.runtime import HydronicRuntime

PLANT_ID = "00000000-0000-4000-8000-000000000001"
ZONE_ID = "00000000-0000-4000-8000-000000000002"
VALVE_ID = "00000000-0000-4000-8000-000000000003"
PUMP_ID = "00000000-0000-4000-8000-000000000004"
CIRCUIT_ID = "00000000-0000-4000-8000-000000000005"
ROUTE_ID = "00000000-0000-4000-8000-000000000006"
SOURCE_ID = "00000000-0000-4000-8000-000000000007"


def _entry(
    *,
    shadow_mode: bool,
    valve_entity_id: str = "switch.synthetic_valve",
    actuator_shadow: bool = False,
    pump_overrun_seconds: float = 300.0,
    valve_opening_seconds: float = 300.0,
    readiness_entity_id: str | None = None,
    source_demand: bool = False,
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
                    "opening_time_seconds": valve_opening_seconds,
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
    if source_demand:
        data["topology"]["sources"] = [
            {
                "id": SOURCE_ID,
                "name": "Synthetic source",
                "source_demand_entity": "switch.synthetic_source",
            }
        ]
    if actuator_shadow:
        data[CONF_ACTUATOR_SHADOW_MODES] = {VALVE_ID: True}
    if readiness_entity_id is not None:
        data["topology"]["valves"][0]["readiness_entity_id"] = readiness_entity_id
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
    assert all(entity != "switch.synthetic_pump" for _domain, _service, entity in calls)
    assert all(service != "toggle" for _domain, service, _entity in calls)

    await runtime.async_refresh(hass)
    await hass.async_block_till_done()
    assert calls == [(expected_domain, expected_service, entity_id)]


async def test_rejected_service_call_is_explained_without_failing_setup(hass) -> None:
    """A service rejection becomes a stable runtime failure report."""

    async def reject(_call) -> None:
        raise HomeAssistantError("synthetic rejection")

    hass.services.async_register("switch", "turn_on", reject)
    hass.states.async_set("sensor.synthetic_temperature", "18.0")
    hass.states.async_set("switch.synthetic_valve", "off")
    entry = _entry(shadow_mode=False)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    report = entry.runtime_data.last_execution
    assert report is not None
    assert len(report.failures) == 1
    assert report.failures[0].kind is ActuatorFailureKind.REJECTED
    assert "synthetic rejection" in report.failures[0].explanation
    assert entry.runtime_data.executor.failure_for(VALVE_ID) == report.failures[0]
    assert entry.runtime_data.runtime_state.valves[VALVE_ID].state.value == "closed"


async def test_delayed_service_success_is_reconciled_without_a_duplicate_command(hass) -> None:
    """A command that outlives its timeout recovers from synthetic feedback."""
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def delayed_open(call) -> None:
        calls.append(call.data["entity_id"])
        started.set()
        await release.wait()
        hass.states.async_set("switch.synthetic_valve", "on")

    hass.services.async_register("switch", "turn_on", delayed_open)
    hass.states.async_set("sensor.synthetic_temperature", "18.0")
    hass.states.async_set("switch.synthetic_valve", "off")
    entry = _entry(shadow_mode=False)
    entry.add_to_hass(hass)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("custom_components.hydronicus.runtime.ACTUATOR_COMMAND_TIMEOUT_SECONDS", 0.01)
        setup_task = asyncio.create_task(hass.config_entries.async_setup(entry.entry_id))
        await started.wait()
        assert await setup_task

    report = entry.runtime_data.last_execution
    assert report is not None
    assert report.failures[0].kind is ActuatorFailureKind.TIMEOUT
    assert calls == ["switch.synthetic_valve"]

    release.set()
    await hass.async_block_till_done()

    assert entry.runtime_data.executor.failure_for(VALVE_ID) is None
    assert entry.runtime_data.executor.actuator_state(VALVE_ID) is ActuatorObservedState.ON
    assert calls == ["switch.synthetic_valve"]


async def test_periodic_reconciliation_repairs_a_missed_feedback_event_without_churn(hass) -> None:
    """A periodic read advances synthetic feedback even when its event was missed."""
    calls: list[tuple[str, str, str]] = []
    _register_recorder(hass, calls)
    hass.states.async_set("sensor.synthetic_temperature", "18.0")
    hass.states.async_set("switch.synthetic_valve", "off")
    hass.states.async_set("switch.synthetic_pump", "off")
    entry = _entry(shadow_mode=False, valve_opening_seconds=0.0)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert calls == [
        ("switch", "turn_on", "switch.synthetic_valve"),
        ("switch", "turn_on", "switch.synthetic_pump"),
    ]

    # Simulate a missed state event by removing the listener before synthetic feedback.
    remove_state_listener = entry.runtime_data._remove_state_listener
    assert remove_state_listener is not None
    remove_state_listener()
    entry.runtime_data._remove_state_listener = None
    hass.states.async_set("switch.synthetic_valve", "on")
    hass.states.async_set("switch.synthetic_pump", "on")
    calls.clear()
    remove_reconciliation_timer = entry.runtime_data._remove_reconciliation_timer
    assert remove_reconciliation_timer is not None
    remove_reconciliation_timer()
    entry.runtime_data._remove_reconciliation_timer = None
    entry.runtime_data._async_handle_reconciliation_timer(datetime.now(UTC))
    await hass.async_block_till_done()

    assert calls == []
    assert entry.runtime_data.runtime_state.valves[VALVE_ID].is_ready is True
    assert entry.runtime_data.runtime_state.pumps[PUMP_ID].state.value == "running"
    remove_reconciliation_timer = entry.runtime_data._remove_reconciliation_timer
    assert remove_reconciliation_timer is not None
    remove_reconciliation_timer()
    entry.runtime_data._remove_reconciliation_timer = None
    entry.runtime_data._async_handle_reconciliation_timer(datetime.now(UTC))
    await hass.async_block_till_done()
    assert calls == []
    await entry.runtime_data.async_stop()


async def test_readiness_feedback_allows_pump_only_after_the_valve_is_ready(hass) -> None:
    """A readiness feedback event advances the synthetic valve-to-pump sequence."""
    calls: list[tuple[str, str, str]] = []
    _register_recorder(hass, calls)
    hass.states.async_set("sensor.synthetic_temperature", "18.0")
    hass.states.async_set("valve.synthetic_valve", "closed")
    hass.states.async_set("binary_sensor.synthetic_valve_ready", "off")
    hass.states.async_set("switch.synthetic_pump", "off")
    entry = _entry(
        shadow_mode=False,
        valve_entity_id="valve.synthetic_valve",
        valve_opening_seconds=300.0,
        readiness_entity_id="binary_sensor.synthetic_valve_ready",
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert calls == [("valve", "open_valve", "valve.synthetic_valve")]
    assert all(entity != "switch.synthetic_pump" for _domain, _service, entity in calls)

    hass.states.async_set("binary_sensor.synthetic_valve_ready", "on")
    await hass.async_block_till_done()

    assert calls == [
        ("valve", "open_valve", "valve.synthetic_valve"),
        ("switch", "turn_on", "switch.synthetic_pump"),
    ]
    assert entry.runtime_data.runtime_state.valves[VALVE_ID].is_ready is True


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
    assert entry.runtime_data.executor.actuator_state(VALVE_ID) is ActuatorObservedState.OFF
    assert entry.runtime_data.executor.requested_state(VALVE_ID) is ActuatorObservedState.ON

    hass.states.async_set("sensor.synthetic_temperature", "22.0")
    hass.states.async_set("switch.synthetic_valve", "unknown")
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert isinstance(entry.runtime_data, HydronicRuntime)
    assert entry.runtime_data.executor.actuator_state(VALVE_ID) is ActuatorObservedState.UNKNOWN


async def test_reload_during_valve_opening_does_not_start_pump_early(hass) -> None:
    """A switch that is on after restart remains timer-gated because it has no position feedback."""
    calls: list[tuple[str, str, str]] = []
    _register_recorder(hass, calls)
    hass.states.async_set("sensor.synthetic_temperature", "18.0")
    hass.states.async_set("switch.synthetic_valve", "off")
    hass.states.async_set("switch.synthetic_pump", "off")
    entry = _entry(shadow_mode=False, valve_opening_seconds=300.0)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert calls == [("switch", "turn_on", "switch.synthetic_valve")]

    hass.states.async_set("switch.synthetic_valve", "on")
    await hass.async_block_till_done()
    calls.clear()

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    runtime = entry.runtime_data
    assert runtime.runtime_state.valves[VALVE_ID].state.value == "opening"
    assert runtime.runtime_state.valves[VALVE_ID].is_ready is False
    assert runtime.runtime_state.pumps[PUMP_ID].state.value == "off"
    assert all(entity != "switch.synthetic_pump" for _domain, _service, entity in calls)


async def test_reload_during_pump_starting_does_not_assume_running_feedback(hass) -> None:
    """A pending start is reasserted only because synthetic feedback still says off."""
    calls: list[tuple[str, str, str]] = []
    _register_recorder(hass, calls)
    hass.states.async_set("sensor.synthetic_temperature", "18.0")
    hass.states.async_set("switch.synthetic_valve", "off")
    hass.states.async_set("switch.synthetic_pump", "off")
    entry = _entry(shadow_mode=False, valve_opening_seconds=0.0)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("switch.synthetic_valve", "on")
    await hass.async_block_till_done()
    calls.clear()

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert calls == [("switch", "turn_on", "switch.synthetic_pump")]
    assert entry.runtime_data.runtime_state.pumps[PUMP_ID].state.value == "starting"
    assert entry.runtime_data.executor.actuator_state(PUMP_ID) is ActuatorObservedState.OFF


async def test_reload_during_pump_running_keeps_observed_running_state_without_churn(hass) -> None:
    """Observed synthetic pump feedback is enough to reconstruct running state."""
    calls: list[tuple[str, str, str]] = []
    _register_recorder(hass, calls)
    hass.states.async_set("sensor.synthetic_temperature", "18.0")
    hass.states.async_set("switch.synthetic_valve", "on")
    hass.states.async_set("switch.synthetic_pump", "on")
    entry = _entry(shadow_mode=False, valve_opening_seconds=0.0)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    calls.clear()

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert calls == []
    assert entry.runtime_data.runtime_state.pumps[PUMP_ID].state.value == "running"


async def test_reload_during_shutdown_preserves_pump_overrun_before_valve_close(hass) -> None:
    """Restarting during shutdown never closes a valve while observed pump is on."""
    calls: list[tuple[str, str, str]] = []
    _register_recorder(hass, calls)
    hass.states.async_set("sensor.synthetic_temperature", "22.0")
    hass.states.async_set("switch.synthetic_valve", "on")
    hass.states.async_set("switch.synthetic_pump", "on")
    entry = _entry(shadow_mode=False, pump_overrun_seconds=60.0)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = entry.runtime_data
    started_at = runtime.runtime_state.pumps[PUMP_ID].changed_at
    assert started_at is not None
    shutdown = await runtime.async_safe_shutdown(hass, now=started_at)
    assert shutdown.plan.phase.value == "pump_overrun"
    calls.clear()

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert calls == []
    assert entry.runtime_data.runtime_state.pumps[PUMP_ID].state.value == "overrun"
    assert all(service != "close_valve" for _domain, service, _entity in calls)


async def test_reload_during_pump_overrun_keeps_valve_protected(hass) -> None:
    """Observed open and running equipment reconstructs overrun before valve closure."""
    calls: list[tuple[str, str, str]] = []
    _register_recorder(hass, calls)
    hass.states.async_set("sensor.synthetic_temperature", "22.0")
    hass.states.async_set("valve.synthetic_valve", "open")
    hass.states.async_set("switch.synthetic_pump", "on")
    entry = _entry(
        shadow_mode=False,
        valve_entity_id="valve.synthetic_valve",
        valve_opening_seconds=300.0,
        pump_overrun_seconds=60.0,
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert calls == []
    assert entry.runtime_data.runtime_state.valves[VALVE_ID].is_ready is True
    assert entry.runtime_data.runtime_state.pumps[PUMP_ID].state.value == "overrun"

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    runtime = entry.runtime_data
    assert runtime.runtime_state.pumps[PUMP_ID].state.value == "overrun"
    assert runtime.runtime_state.valves[VALVE_ID].is_ready is True
    assert all(service != "close_valve" for _domain, service, _entity in calls)


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


async def test_safe_shutdown_is_ordered_and_idempotent_with_intercepted_services(hass) -> None:
    """Synthetic shutdown releases source, waits overrun, then stops pumps and valves."""
    calls: list[tuple[str, str, str]] = []
    _register_recorder(hass, calls)
    hass.states.async_set("sensor.synthetic_temperature", "18.0")
    hass.states.async_set("switch.synthetic_valve", "off")
    hass.states.async_set("switch.synthetic_pump", "off")
    hass.states.async_set("switch.synthetic_source", "on")
    entry = _entry(
        shadow_mode=False,
        pump_overrun_seconds=10.0,
        valve_opening_seconds=0.0,
        source_demand=True,
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = entry.runtime_data
    hass.states.async_set("switch.synthetic_pump", "on")
    await runtime.async_refresh(hass)
    await hass.async_block_till_done()
    started_at = runtime.runtime_state.pumps[PUMP_ID].changed_at
    assert started_at is not None
    calls.clear()

    first = await runtime.async_safe_shutdown(hass, now=started_at)
    assert first.plan.phase.value == "pump_overrun"
    assert calls[-1] == ("switch", "turn_off", "switch.synthetic_source")
    second = await runtime.async_safe_shutdown(hass, now=started_at + timedelta(seconds=5))
    assert second.plan.phase.value == "pump_overrun"
    assert calls == [("switch", "turn_off", "switch.synthetic_source")]
    third = await runtime.async_safe_shutdown(hass, now=started_at + timedelta(seconds=10))
    assert third.plan.phase.value == "pumps_stopped"
    assert calls[-1] == ("switch", "turn_off", "switch.synthetic_pump")
    fourth = await runtime.async_safe_shutdown(hass, now=started_at + timedelta(seconds=11))
    assert fourth.plan.phase.value == "valves_closed"
    assert calls[-1] == ("switch", "turn_off", "switch.synthetic_valve")
    fifth = await runtime.async_safe_shutdown(hass, now=started_at + timedelta(seconds=12))
    assert fifth.execution.executed == ()
