"""Tests for the runtime state container."""

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest import mock

homeassistant = types.ModuleType("homeassistant")
homeassistant_config_entries = types.ModuleType("homeassistant.config_entries")
homeassistant_core = types.ModuleType("homeassistant.core")
homeassistant_helpers = types.ModuleType("homeassistant.helpers")
homeassistant_helpers_event = types.ModuleType("homeassistant.helpers.event")


class _ConfigEntry:
    runtime_data: object | None = None


class _HomeAssistant:
    pass


class _Event:
    pass


class _EventStateChangedData:
    pass


def _callback(func):
    return func


def _track_state_change_event(*args, **kwargs):
    return lambda: None


def _call_later(*args, **kwargs):
    return lambda: None


homeassistant_config_entries.ConfigEntry = _ConfigEntry
homeassistant_core.HomeAssistant = _HomeAssistant
homeassistant_core.Event = _Event
homeassistant_core.EventStateChangedData = _EventStateChangedData
homeassistant_core.callback = _callback
homeassistant_helpers_event.async_call_later = _call_later
homeassistant_helpers_event.async_track_state_change_event = _track_state_change_event
sys.modules.setdefault("homeassistant", homeassistant)
sys.modules.setdefault("homeassistant.config_entries", homeassistant_config_entries)
sys.modules.setdefault("homeassistant.core", homeassistant_core)
sys.modules.setdefault("homeassistant.helpers", homeassistant_helpers)
sys.modules.setdefault("homeassistant.helpers.event", homeassistant_helpers_event)

CONF_DRY_RUN = import_module("custom_components.hydronicus.const").CONF_DRY_RUN
CONF_PLANT_ID = import_module("custom_components.hydronicus.const").CONF_PLANT_ID
runtime_module = import_module("custom_components.hydronicus.runtime")
HydronicRuntime = runtime_module.HydronicRuntime
RuntimeState = import_module("hydronicus_core.model").RuntimeState
SafeShutdownPhase = import_module("hydronicus_core.model").SafeShutdownPhase

NOW = datetime(2026, 7, 17, tzinfo=UTC)
PLANT_UUID = "00000000-0000-4000-8000-000000000001"
ZONE_UUID = "00000000-0000-4000-8000-000000000002"
VALVE_UUID = "00000000-0000-4000-8000-000000000003"
PUMP_UUID = "00000000-0000-4000-8000-000000000004"
CIRCUIT_UUID = "00000000-0000-4000-8000-000000000005"
ROUTE_UUID = "00000000-0000-4000-8000-000000000006"


class _State:
    def __init__(
        self,
        state: str,
        *,
        last_updated: datetime = NOW,
        last_reported: datetime = NOW,
    ) -> None:
        self.state = state
        self.last_updated = last_updated
        self.last_reported = last_reported


class _StateStore:
    def __init__(self, state: str) -> None:
        self.current = _State(state)

    def get(self, entity_id: str) -> _State | None:
        return self.current if entity_id == "sensor.test_temperature" else None


class _RuntimeHomeAssistant:
    def __init__(self, state: str) -> None:
        self.states = _StateStore(state)
        self.tasks: list[asyncio.Task[None]] = []

    def async_create_task(self, coroutine) -> asyncio.Task[None]:
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task


def _configured_entry(
    *,
    zone_overrides: dict[str, object] | None = None,
    pump_overrun_seconds: float = 120.0,
) -> SimpleNamespace:
    zone = {
        "id": ZONE_UUID,
        "name": "Test zone",
        "target_temperature": 21.0,
        "temperature_sensor": "sensor.test_temperature",
    }
    if zone_overrides:
        zone.update(zone_overrides)
    return SimpleNamespace(
        data={
            "name": "Hydronic plant",
            "plant_id": PLANT_UUID,
            "dry_run": True,
            "topology": {
                "zones": [zone],
                "circuits": [
                    {
                        "id": CIRCUIT_UUID,
                        "name": "Test circuit",
                        "valve_ids": [VALVE_UUID],
                        "pump_id": PUMP_UUID,
                    }
                ],
                "valves": [
                    {
                        "id": VALVE_UUID,
                        "name": "Test valve",
                        "entity_id": "switch.test_valve",
                        "opening_time_seconds": 30.0,
                    }
                ],
                "pumps": [
                    {
                        "id": PUMP_UUID,
                        "name": "Test pump",
                        "entity_id": "switch.test_pump",
                        "overrun_seconds": pump_overrun_seconds,
                    }
                ],
                "routes": [
                    {
                        "id": ROUTE_UUID,
                        "zone_id": ZONE_UUID,
                        "circuit_id": CIRCUIT_UUID,
                    }
                ],
            },
        }
    )


class RuntimeTests(unittest.TestCase):
    """Verify runtime data construction."""

    def test_defaults_to_dry_run(self) -> None:
        runtime = HydronicRuntime.from_entry(SimpleNamespace(data={CONF_PLANT_ID: "plant"}))

        self.assertEqual(runtime.plant_id, "plant")
        self.assertEqual(runtime.name, "Hydronic plant")
        self.assertTrue(runtime.dry_run)

    def test_reads_explicit_dry_run(self) -> None:
        runtime = HydronicRuntime.from_entry(
            SimpleNamespace(data={CONF_PLANT_ID: "plant", CONF_DRY_RUN: False})
        )

        self.assertFalse(runtime.dry_run)

    def test_zone_aggregation_requires_a_controller_evaluation(self) -> None:
        """The adapter must not reimplement aggregation before the core evaluates."""
        runtime = HydronicRuntime.from_entry(_configured_entry())
        runtime.snapshot = SimpleNamespace(temperatures={})

        self.assertIsNone(runtime.zone_aggregation(ZONE_UUID))


class RuntimeSchedulingTests(unittest.IsolatedAsyncioTestCase):
    """Verify Home Assistant wakes the controller for timed transitions."""

    async def test_shutdown_timer_advances_shutdown_instead_of_evaluating_demand(self) -> None:
        """A shutdown deadline never routes back through normal demand evaluation."""
        runtime = HydronicRuntime.from_entry(_configured_entry())
        hass = _RuntimeHomeAssistant("20.0")
        runtime._hass = hass
        runtime.runtime_state = RuntimeState(
            safe_shutdown_phase=SafeShutdownPhase.PUMP_OVERRUN
        )

        with mock.patch.object(
            runtime_module.HydronicRuntime,
            "async_safe_shutdown",
            new_callable=mock.AsyncMock,
        ) as safe_shutdown:
            runtime._async_handle_transition_timer(NOW)
            await hass.tasks[-1]

        safe_shutdown.assert_awaited_once_with(hass)

    async def test_timers_advance_valve_readiness_and_pump_overrun(self) -> None:
        runtime = HydronicRuntime.from_entry(_configured_entry())
        hass = _RuntimeHomeAssistant("20.0")
        scheduled: list[tuple[float, object, mock.Mock]] = []

        def schedule(_hass, delay: float, action):
            cancel = mock.Mock()
            scheduled.append((delay, action, cancel))
            return cancel

        with (
            mock.patch.object(
                runtime_module,
                "async_track_state_change_event",
                return_value=mock.Mock(),
            ),
            mock.patch.object(runtime_module, "async_call_later", side_effect=schedule),
            mock.patch.object(runtime_module, "datetime") as clock,
        ):
            clock.now.return_value = NOW
            await runtime.async_start(hass)

            self.assertEqual(scheduled[0][0], 30.0)
            self.assertEqual(runtime.runtime_state.valves[VALVE_UUID].state.value, "opening")
            self.assertEqual(
                [
                    (command.actuator_id, command.action)
                    for command in runtime.evaluation.control_plan.commands
                ],
                [(VALVE_UUID, "open")],
            )

            clock.now.return_value = NOW + timedelta(seconds=30)
            scheduled[0][1](clock.now.return_value)
            await hass.tasks[-1]

            self.assertEqual(runtime.runtime_state.valves[VALVE_UUID].state.value, "open")
            self.assertEqual(runtime.runtime_state.pumps[PUMP_UUID].state.value, "running")
            self.assertEqual(
                [
                    (command.actuator_id, command.action)
                    for command in runtime.evaluation.control_plan.commands
                ],
                [(PUMP_UUID, "turn_on")],
            )

            hass.states.current = _State("22.0")
            clock.now.return_value = NOW + timedelta(seconds=31)
            await runtime.async_refresh(hass)

            overrun_timer = next(item for item in scheduled if item[0] == 120.0)
            self.assertEqual(overrun_timer[0], 120.0)
            self.assertEqual(runtime.runtime_state.pumps[PUMP_UUID].state.value, "overrun")
            self.assertEqual(runtime.evaluation.control_plan.commands, ())

            clock.now.return_value = NOW + timedelta(seconds=151)
            overrun_timer[1](clock.now.return_value)
            await hass.tasks[-1]

            self.assertEqual(runtime.runtime_state.pumps[PUMP_UUID].state.value, "off")
            self.assertEqual(runtime.runtime_state.valves[VALVE_UUID].state.value, "closed")
            self.assertEqual(
                [
                    (command.actuator_id, command.action)
                    for command in runtime.evaluation.control_plan.commands
                ],
                [(PUMP_UUID, "turn_off"), (VALVE_UUID, "close")],
            )

    async def test_due_now_transition_runs_as_a_tracked_home_assistant_task(self) -> None:
        """Zero overrun advances without relying on an untracked timer callback."""
        runtime = HydronicRuntime.from_entry(_configured_entry(pump_overrun_seconds=0))
        hass = _RuntimeHomeAssistant("20.0")
        scheduled: list[tuple[float, object]] = []

        def schedule(_hass, delay: float, action):
            scheduled.append((delay, action))
            return mock.Mock()

        with (
            mock.patch.object(
                runtime_module,
                "async_track_state_change_event",
                return_value=mock.Mock(),
            ),
            mock.patch.object(runtime_module, "async_call_later", side_effect=schedule),
            mock.patch.object(runtime_module, "datetime") as clock,
        ):
            clock.now.return_value = NOW
            await runtime.async_start(hass)

            clock.now.return_value = NOW + timedelta(seconds=30)
            scheduled[0][1](clock.now.return_value)
            await hass.tasks[-1]

            hass.states.current = _State("22.0")
            clock.now.return_value = NOW + timedelta(seconds=31)
            prior_task_count = len(hass.tasks)
            await runtime.async_refresh(hass)

            self.assertEqual(len(hass.tasks), prior_task_count + 1)
            await hass.tasks[-1]

        scheduled_delays = [delay for delay, _action in scheduled]
        self.assertEqual(scheduled_delays[0], 30.0)
        self.assertNotIn(0, scheduled_delays)
        self.assertEqual(runtime.runtime_state.pumps[PUMP_UUID].state.value, "off")
        self.assertEqual(runtime.runtime_state.valves[VALVE_UUID].state.value, "closed")

    async def test_battery_observation_uses_last_reported_timestamp(self) -> None:
        """An unchanged report should be newer than its last value update."""
        runtime = HydronicRuntime.from_entry(_configured_entry())
        hass = _RuntimeHomeAssistant("20.0")
        hass.states.current = _State(
            "20.0",
            last_updated=NOW - timedelta(hours=12),
            last_reported=NOW,
        )

        with mock.patch.object(runtime_module, "async_call_later", return_value=mock.Mock()):
            await runtime.async_refresh(hass)

        self.assertEqual(
            runtime.snapshot.temperatures["sensor.test_temperature"].observed_at,
            NOW,
        )

    async def test_sensor_staleness_deadline_is_scheduled_without_state_change(self) -> None:
        """The runtime wakes at freshness expiry even when no sensor event arrives."""
        runtime = HydronicRuntime.from_entry(
            _configured_entry(
                zone_overrides={
                    "temperature_sensor_metadata": [
                        {
                            "entity_id": "sensor.test_temperature",
                            "max_age_seconds": 60.0,
                        }
                    ]
                }
            )
        )
        hass = _RuntimeHomeAssistant("22.0")
        scheduled: list[float] = []

        def schedule(_hass, delay: float, _action):
            scheduled.append(delay)
            return mock.Mock()

        with (
            mock.patch.object(
                runtime_module,
                "async_track_state_change_event",
                return_value=mock.Mock(),
            ),
            mock.patch.object(runtime_module, "async_call_later", side_effect=schedule),
            mock.patch.object(runtime_module, "datetime") as clock,
        ):
            clock.now.return_value = NOW
            await runtime.async_start(hass)

        self.assertIn(60.0, scheduled)
        self.assertIn(30.0, scheduled)

    async def test_stop_cancels_state_and_timer_listeners(self) -> None:
        runtime = HydronicRuntime.from_entry(_configured_entry())
        hass = _RuntimeHomeAssistant("20.0")
        cancel_state = mock.Mock()
        cancel_timer = mock.Mock()

        with (
            mock.patch.object(
                runtime_module, "async_track_state_change_event", return_value=cancel_state
            ),
            mock.patch.object(runtime_module, "async_call_later", return_value=cancel_timer),
            mock.patch.object(runtime_module, "datetime") as clock,
        ):
            clock.now.return_value = NOW
            await runtime.async_start(hass)
            await runtime.async_stop()

        cancel_state.assert_called_once_with()
        self.assertEqual(cancel_timer.call_count, 2)

    async def test_stop_cancels_in_flight_runtime_work(self) -> None:
        """Unload cancels tracked work rather than leaving a delayed command alive."""
        runtime = HydronicRuntime.from_entry(_configured_entry())
        task = asyncio.create_task(asyncio.sleep(60))
        runtime._tasks.add(task)

        await runtime.async_stop()

        self.assertTrue(task.cancelled())

    async def test_homeassistant_stop_handler_cancels_all_runtime_resources(self) -> None:
        """The Home Assistant stop callback cancels listeners, timers, and work."""
        runtime = HydronicRuntime.from_entry(_configured_entry())
        hass = _RuntimeHomeAssistant("20.0")
        cancel_state = mock.Mock()
        cancel_transition = mock.Mock()
        cancel_reconciliation = mock.Mock()
        cancel_stop_listener = mock.Mock()
        task = asyncio.create_task(asyncio.sleep(60))
        runtime._hass = hass
        runtime._remove_state_listener = cancel_state
        runtime._remove_transition_timer = cancel_transition
        runtime._remove_reconciliation_timer = cancel_reconciliation
        runtime._remove_stop_listener = cancel_stop_listener
        runtime._tasks.add(task)

        await runtime._async_handle_homeassistant_stop(_Event())

        cancel_state.assert_called_once_with()
        cancel_transition.assert_called_once_with()
        cancel_reconciliation.assert_called_once_with()
        cancel_stop_listener.assert_called_once_with()
        self.assertTrue(task.cancelled())
        self.assertIsNone(runtime._hass)
        self.assertTrue(runtime._stopping)


if __name__ == "__main__":
    unittest.main()
