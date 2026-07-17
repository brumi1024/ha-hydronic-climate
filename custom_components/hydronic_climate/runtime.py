"""Home Assistant runtime boundary for a hydronic plant.

Hardware observation and service execution will be added here in later milestones.
The initial vertical slice is intentionally shadow-only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import CONF_NAME, CONF_PLANT_ID, CONF_SHADOW_MODE
from .core.configuration import plant_configuration_from_entry_data
from .core.controller import evaluate
from .core.model import (
    CompiledPlant,
    Evaluation,
    PlantSnapshot,
    PumpState,
    RuntimeState,
    TemperatureObservation,
    ValveState,
)
from .core.topology import compile_topology


@dataclass(slots=True)
class HydronicRuntime:
    """Runtime data retained for one configured plant."""

    plant_id: str
    name: str
    shadow_mode: bool
    plant: CompiledPlant
    runtime_state: RuntimeState = field(default_factory=RuntimeState)
    evaluation: Evaluation | None = None
    snapshot: PlantSnapshot | None = None
    _hass: HomeAssistant | None = None
    _remove_state_listener: Callable[[], None] | None = None
    _remove_transition_timer: Callable[[], None] | None = None
    _listeners: set[Callable[[], None]] = field(default_factory=set)

    @classmethod
    def from_entry(cls, entry: Any) -> HydronicRuntime:
        """Construct safe runtime data from a config entry."""
        plant = compile_topology(plant_configuration_from_entry_data(entry.data))
        return cls(
            plant_id=str(entry.data.get(CONF_PLANT_ID, getattr(entry, "entry_id", "plant"))),
            name=str(entry.data.get(CONF_NAME, getattr(entry, "title", "Hydronic plant"))),
            shadow_mode=bool(entry.data.get(CONF_SHADOW_MODE, True)),
            plant=plant,
        )

    async def async_start(self, hass: HomeAssistant) -> None:
        """Observe configured sensors and evaluate the shadow plan without service calls."""
        self._hass = hass
        await self.async_refresh(hass)
        sensor_ids = [zone.temperature_sensor for zone in self.plant.zones.values()]
        self._remove_state_listener = async_track_state_change_event(
            hass, sensor_ids, self._async_handle_state_change
        )

    async def async_stop(self) -> None:
        """Remove runtime listeners without changing any physical equipment."""
        if self._remove_state_listener is not None:
            self._remove_state_listener()
            self._remove_state_listener = None
        self._cancel_transition_timer()
        self._hass = None

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register an entity update callback."""
        self._listeners.add(listener)

        def remove_listener() -> None:
            self._listeners.discard(listener)

        return remove_listener

    @callback
    def _async_handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Re-evaluate after a configured temperature sensor changes."""
        if self._hass is not None:
            self._hass.async_create_task(self.async_refresh(self._hass))

    @callback
    def _async_handle_transition_timer(self, _now: datetime) -> None:
        """Re-evaluate when the next virtual actuator transition becomes due."""
        self._remove_transition_timer = None
        if self._hass is not None:
            self._hass.async_create_task(self.async_refresh(self._hass))

    def _cancel_transition_timer(self) -> None:
        """Cancel the pending one-shot transition timer, if any."""
        if self._remove_transition_timer is not None:
            self._remove_transition_timer()
            self._remove_transition_timer = None

    def _next_transition_delay(self, now: datetime) -> float | None:
        """Return seconds until the earliest pending virtual actuator transition."""
        delays: list[float] = []
        for circuit in self.plant.circuits.values():
            valve = self.runtime_state.valves.get(circuit.valve_id)
            if (
                valve is not None
                and valve.state is ValveState.OPENING
                and valve.changed_at is not None
            ):
                deadline = valve.changed_at + timedelta(
                    seconds=circuit.valve_opening_time_seconds
                )
                delays.append(max(0.0, (deadline - now).total_seconds()))

            pump = self.runtime_state.pumps.get(circuit.pump_id)
            if pump is not None and pump.state is PumpState.OVERRUN and pump.changed_at is not None:
                deadline = pump.changed_at + timedelta(seconds=circuit.pump_overrun_seconds)
                delays.append(max(0.0, (deadline - now).total_seconds()))

        return min(delays) if delays else None

    def _schedule_next_transition(self, hass: HomeAssistant, now: datetime) -> None:
        """Replace the pending timer with the earliest controller deadline."""
        self._cancel_transition_timer()
        delay = self._next_transition_delay(now)
        if delay is not None:
            self._remove_transition_timer = async_call_later(
                hass, delay, self._async_handle_transition_timer
            )

    async def async_refresh(self, hass: HomeAssistant) -> None:
        """Read sensor states, evaluate the controller, and notify shadow entities."""
        observations: dict[str, TemperatureObservation] = {}
        for zone in self.plant.zones.values():
            state = hass.states.get(zone.temperature_sensor)
            value: float | None
            try:
                value = float(state.state) if state is not None else None
            except ValueError:
                value = None
            observations[zone.temperature_sensor] = TemperatureObservation(
                value=value,
                observed_at=state.last_updated if state is not None else None,
            )
        self.snapshot = PlantSnapshot(observations)
        now = datetime.now(UTC)
        result = evaluate(self.plant, self.snapshot, self.runtime_state, now)
        self.runtime_state = result.next_runtime
        self.evaluation = result
        self._schedule_next_transition(hass, now)
        for listener in self._listeners:
            listener()
