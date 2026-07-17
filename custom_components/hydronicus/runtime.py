"""Home Assistant runtime boundary for a hydronic plant.

Hardware observation and service execution will be added here in later milestones.
The initial vertical slice is intentionally shadow-only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    CONF_NAME,
    CONF_PLANT_ID,
    CONF_SHADOW_MODE,
)
from .core.controller import aggregate_zone_temperature, evaluate
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
from .entry_configuration import (
    effective_plant_configuration,
    zone_target_temperature_update,
)


@dataclass(slots=True)
class HydronicRuntime:
    """Runtime data retained for one configured plant."""

    plant_id: str
    name: str
    shadow_mode: bool
    plant: CompiledPlant
    actuator_subentry_ids: Mapping[str, str] = field(default_factory=dict)
    zone_subentry_ids: Mapping[str, str] = field(default_factory=dict)
    runtime_state: RuntimeState = field(default_factory=RuntimeState)
    zone_target_temperatures: dict[str, float] = field(default_factory=dict)
    evaluation: Evaluation | None = None
    snapshot: PlantSnapshot | None = None
    _hass: HomeAssistant | None = None
    _entry: Any | None = None
    _remove_state_listener: Callable[[], None] | None = None
    _remove_transition_timer: Callable[[], None] | None = None
    _listeners: set[Callable[[], None]] = field(default_factory=set)

    @classmethod
    def from_entry(cls, entry: Any) -> HydronicRuntime:
        """Construct safe runtime data from a config entry."""
        effective = effective_plant_configuration(entry)
        plant = compile_topology(effective.configuration)
        return cls(
            plant_id=str(entry.data.get(CONF_PLANT_ID, getattr(entry, "entry_id", "plant"))),
            name=str(entry.data.get(CONF_NAME, getattr(entry, "title", "Hydronic plant"))),
            shadow_mode=bool(entry.data.get(CONF_SHADOW_MODE, True)),
            plant=plant,
            actuator_subentry_ids=effective.actuator_subentry_ids,
            zone_subentry_ids=effective.zone_subentry_ids,
            zone_target_temperatures={
                zone.id: zone.target_temperature for zone in plant.zones.values()
            },
            _entry=entry,
        )

    async def async_start(self, hass: HomeAssistant) -> None:
        """Observe configured sensors and evaluate the shadow plan without service calls."""
        self._hass = hass
        await self.async_refresh(hass)
        self._remove_state_listener = async_track_state_change_event(
            hass, self._temperature_sensor_ids(), self._async_handle_state_change
        )

    async def async_stop(self) -> None:
        """Remove runtime listeners without changing any physical equipment."""
        if self._remove_state_listener is not None:
            self._remove_state_listener()
            self._remove_state_listener = None
        self._cancel_transition_timer()
        self._hass = None
        self._entry = None

    async def async_set_zone_target_temperature(
        self, zone_id: str, temperature: float, *, hass: HomeAssistant | None = None
    ) -> None:
        """Persist and immediately apply a zone setpoint in shadow mode."""
        if not isfinite(temperature):
            raise ValueError("Zone target temperature must be finite.")
        if zone_id not in self.plant.zones:
            raise ValueError(f"Unknown zone {zone_id}.")
        active_hass = hass or self._hass
        if active_hass is None or self._entry is None:
            raise RuntimeError("Hydronic runtime is not started.")

        subentry_id, data = zone_target_temperature_update(
            self._entry, zone_id, temperature
        )
        if subentry_id is not None:
            subentry = self._entry.subentries[subentry_id]
            active_hass.config_entries.async_update_subentry(
                self._entry, subentry, data=data
            )
        else:
            active_hass.config_entries.async_update_entry(self._entry, data=data)

        self.zone_target_temperatures[zone_id] = temperature
        await self.async_refresh(active_hass)

    def zone_current_temperature(self, zone_id: str) -> float | None:
        """Return the current aggregate temperature for one zone."""
        if self.snapshot is None or zone_id not in self.plant.zones:
            return None
        return aggregate_zone_temperature(self.plant.zones[zone_id], self.snapshot)

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

    def _temperature_sensor_ids(self) -> tuple[str, ...]:
        """Return every configured sensor once, preserving topology order."""
        return tuple(
            dict.fromkeys(
                sensor_id
                for zone in self.plant.zones.values()
                for sensor_id in zone.temperature_sensors
            )
        )

    def _next_transition_delay(self, now: datetime) -> float | None:
        """Return seconds until the earliest pending virtual actuator transition."""
        delays: list[float] = []
        for valve_node in self.plant.valves.values():
            valve = self.runtime_state.valves.get(valve_node.id)
            if (
                valve is not None
                and valve.state is ValveState.OPENING
                and valve.changed_at is not None
            ):
                deadline = valve.changed_at + timedelta(seconds=valve_node.opening_time_seconds)
                delays.append(max(0.0, (deadline - now).total_seconds()))

        for pump_node in self.plant.pumps.values():
            pump = self.runtime_state.pumps.get(pump_node.id)
            if pump is not None and pump.state is PumpState.OVERRUN and pump.changed_at is not None:
                deadline = pump.changed_at + timedelta(seconds=pump_node.overrun_seconds)
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
        for sensor_id in self._temperature_sensor_ids():
            state = hass.states.get(sensor_id)
            value: float | None
            try:
                value = float(state.state) if state is not None else None
            except ValueError:
                value = None
            observations[sensor_id] = TemperatureObservation(
                value=value,
                observed_at=state.last_reported if state is not None else None,
            )
        self.snapshot = PlantSnapshot(observations)
        now = datetime.now(UTC)
        evaluation_plant = replace(
            self.plant,
            zones={
                zone_id: replace(
                    zone,
                    target_temperature=self.zone_target_temperatures.get(
                        zone_id, zone.target_temperature
                    ),
                )
                for zone_id, zone in self.plant.zones.items()
            },
        )
        result = evaluate(evaluation_plant, self.snapshot, self.runtime_state, now)
        self.runtime_state = result.next_runtime
        self.evaluation = result
        self._schedule_next_transition(hass, now)
        for listener in self._listeners:
            listener()
