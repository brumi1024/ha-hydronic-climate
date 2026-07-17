"""Home Assistant runtime boundary for a hydronic plant.

Hardware observation and service execution will be added here in later milestones.
The initial vertical slice is intentionally shadow-only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any, cast

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    CONF_NAME,
    CONF_PLANT_ID,
    CONF_SHADOW_MODE,
)
from .core.controller import evaluate
from .core.model import (
    MAX_ZONE_TARGET_TEMPERATURE,
    MIN_ZONE_TARGET_TEMPERATURE,
    AggregationResult,
    CompiledPlant,
    Evaluation,
    PlantSnapshot,
    PumpState,
    RuntimeState,
    TemperatureObservation,
    ValveState,
    ZoneDecision,
    ZoneDecisionStatus,
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
    zone_preset_modes: dict[str, str] = field(default_factory=dict)
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
            zone_preset_modes={
                zone.id: _stored_zone_preset_mode(entry, zone.id, zone, effective.zone_subentry_ids)
                for zone in plant.zones.values()
            },
            _entry=entry,
        )

    async def async_start(self, hass: HomeAssistant) -> None:
        """Observe configured sensors and evaluate the shadow plan without service calls."""
        if self._remove_state_listener is not None:
            self._remove_state_listener()
            self._remove_state_listener = None
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
        self._listeners.clear()
        self._hass = None
        self._entry = None

    async def async_set_zone_target_temperature(
        self, zone_id: str, temperature: float, *, hass: HomeAssistant | None = None
    ) -> None:
        """Persist and immediately apply a zone setpoint in shadow mode."""
        temperature = _validate_target_temperature(temperature)
        if zone_id not in self.plant.zones:
            raise ValueError(f"Unknown zone {zone_id}.")
        active_hass = hass or self._hass
        if active_hass is None or self._entry is None:
            raise RuntimeError("Hydronic runtime is not started.")

        subentry_id, data = zone_target_temperature_update(self._entry, zone_id, temperature)
        data = _zone_preset_mode_update(data, zone_id, "none", subentry_id is not None)
        if subentry_id is not None:
            subentry = self._entry.subentries[subentry_id]
            active_hass.config_entries.async_update_subentry(self._entry, subentry, data=data)
        else:
            active_hass.config_entries.async_update_entry(self._entry, data=data)

        self.zone_target_temperatures[zone_id] = temperature
        self.zone_preset_modes[zone_id] = "none"
        await self.async_refresh(active_hass)

    async def async_set_zone_preset_mode(
        self, zone_id: str, preset_mode: str, *, hass: HomeAssistant | None = None
    ) -> None:
        """Persist a configured preset and immediately apply its target in shadow mode."""
        if zone_id not in self.plant.zones:
            raise ValueError(f"Unknown zone {zone_id}.")
        normalized = str(preset_mode).lower()
        zone = self.plant.zones[zone_id]
        if normalized == "none":
            target = self.zone_target_temperatures[zone_id]
        else:
            if normalized not in _PRESET_MODES:
                raise ValueError(f"Unsupported preset mode {preset_mode!r}.")
            try:
                target = _validate_target_temperature(zone.preset_targets[normalized])
            except KeyError as error:
                raise ValueError(
                    f"Preset {normalized!r} is not configured for zone {zone.name}."
                ) from error

        active_hass = hass or self._hass
        if active_hass is None or self._entry is None:
            raise RuntimeError("Hydronic runtime is not started.")
        subentry_id, data = zone_target_temperature_update(self._entry, zone_id, target)
        data = _zone_preset_mode_update(data, zone_id, normalized, subentry_id is not None)
        if subentry_id is not None:
            subentry = self._entry.subentries[subentry_id]
            active_hass.config_entries.async_update_subentry(self._entry, subentry, data=data)
        else:
            active_hass.config_entries.async_update_entry(self._entry, data=data)

        self.zone_target_temperatures[zone_id] = target
        self.zone_preset_modes[zone_id] = normalized
        await self.async_refresh(active_hass)

    def zone_current_temperature(self, zone_id: str) -> float | None:
        """Return the current aggregate temperature for one zone."""
        if self.snapshot is None or zone_id not in self.plant.zones:
            return None
        aggregation = self.zone_aggregation(zone_id)
        return aggregation.value if aggregation is not None else None

    def zone_aggregation(self, zone_id: str) -> AggregationResult | None:
        """Return the structured aggregate for a zone from the last evaluation."""
        if self.evaluation is None or zone_id not in self.plant.zones:
            return None
        decision = self.evaluation.diagnostics.zone_decisions.get(zone_id)
        return decision.aggregation if decision is not None else None

    def zone_decision(self, zone_id: str) -> ZoneDecision | None:
        """Return the structured controller decision for one zone, when available."""
        if self.evaluation is None:
            return None
        return self.evaluation.diagnostics.zone_decisions.get(zone_id)

    def zone_is_blocked(self, zone_id: str) -> bool:
        """Return structured blocked state without parsing a reason string."""
        decision = self.zone_decision(zone_id)
        if decision is not None:
            return decision.status == ZoneDecisionStatus.SENSOR_BLOCKED or (
                decision.aggregation is not None and decision.aggregation.is_blocked
            )
        aggregation = self.zone_aggregation(zone_id)
        return aggregation.is_blocked if aggregation is not None else False

    def zone_blocked_reason(self, zone_id: str) -> str | None:
        """Return the structured blocking explanation for one zone."""
        decision = self.zone_decision(zone_id)
        if decision is not None and decision.status == ZoneDecisionStatus.SENSOR_BLOCKED:
            return decision.explanation or (
                decision.aggregation.explanation if decision.aggregation is not None else None
            )
        aggregation = self.zone_aggregation(zone_id)
        if aggregation is not None and aggregation.is_blocked:
            return cast(str, aggregation.explanation)
        return None

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
        """Re-evaluate when the earliest virtual deadline becomes due."""
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
        """Return seconds until the earliest actuator, duration, or stale deadline."""
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

        if self.evaluation is not None:
            for decision in self.evaluation.diagnostics.zone_decisions.values():
                if decision.deadline is not None:
                    delays.append(max(0.0, (decision.deadline - now).total_seconds()))

        for zone_id, zone in self.plant.zones.items():
            zone_runtime = self.runtime_state.zone_runtime.get(zone_id)
            if zone_runtime is None or zone_runtime.last_demand_transition_at is None:
                continue
            duration = (
                zone.minimum_active_duration_seconds
                if zone_runtime.demand
                else zone.minimum_idle_duration_seconds
            )
            if duration > 0:
                deadline = zone_runtime.last_demand_transition_at + timedelta(seconds=duration)
                if deadline > now:
                    delays.append((deadline - now).total_seconds())

        if self.snapshot is not None:
            for zone in self.plant.zones.values():
                for sensor in zone.sensor_metadata:
                    observation = self.snapshot.temperatures.get(sensor.entity_id)
                    if observation is None or observation.observed_at is None:
                        continue
                    deadline = observation.observed_at + timedelta(seconds=sensor.max_age_seconds)
                    if deadline > now:
                        delays.append((deadline - now).total_seconds())

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
            except TypeError, ValueError:
                value = None
            if state is None:
                observed_at = None
            else:
                observed_at = getattr(state, "last_reported", None)
                if observed_at is None:
                    observed_at = getattr(state, "last_updated", None)
            observations[sensor_id] = TemperatureObservation(
                value=value,
                observed_at=observed_at,
            )
        self.snapshot = PlantSnapshot(observations)
        now = self._now()
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
        if self._entry is not None or self._hass is not None:
            self._schedule_next_transition(hass, now)
        for listener in self._listeners:
            listener()

    def _now(self) -> datetime:
        """Read the UTC clock in one place so timer tests can control it."""
        return datetime.now(UTC)


_PRESET_MODES = {"comfort", "eco", "away"}


def _validate_target_temperature(temperature: float) -> float:
    """Validate the same finite bounded target range advertised by the climate entity."""
    try:
        value = float(temperature)
    except (TypeError, ValueError) as error:
        raise ValueError("Zone target temperature must be numeric.") from error
    if not isfinite(value) or not MIN_ZONE_TARGET_TEMPERATURE <= value <= (
        MAX_ZONE_TARGET_TEMPERATURE
    ):
        raise ValueError("Zone target temperature must be finite and between 5 and 35 °C.")
    return value


def _stored_zone_preset_mode(
    entry: Any, zone_id: str, zone: Any, subentry_ids: Mapping[str, str]
) -> str:
    """Recover an active preset while treating removed presets as manual targets."""
    data: Mapping[str, Any] | None = None
    subentry_id = subentry_ids.get(zone_id)
    if subentry_id is not None:
        subentry = getattr(entry, "subentries", {}).get(subentry_id)
        if subentry is not None:
            data = subentry.data
    else:
        topology = entry.data.get("topology", {})
        if isinstance(topology, Mapping):
            for raw_zone in topology.get("zones", []):
                if isinstance(raw_zone, Mapping) and str(raw_zone.get("id")) == zone_id:
                    data = raw_zone
                    break
    if data is None:
        return "none"
    value = str(data.get("preset_mode", "none")).lower()
    return value if value in _PRESET_MODES and value in zone.preset_targets else "none"


def _zone_preset_mode_update(
    data: Mapping[str, Any], zone_id: str, preset_mode: str, is_subentry: bool
) -> Mapping[str, Any]:
    """Store the active preset in the zone record, including parent-owned zones."""
    if is_subentry:
        return {**data, "preset_mode": preset_mode}
    topology = data.get("topology", {})
    if not isinstance(topology, Mapping):
        return {**data, "preset_mode": preset_mode}
    raw_zones = topology.get("zones", [])
    if not isinstance(raw_zones, list):
        return {**data, "preset_mode": preset_mode}
    zones = [
        {
            **raw_zone,
            "preset_mode": preset_mode,
        }
        if isinstance(raw_zone, Mapping) and str(raw_zone.get("id")) == zone_id
        else raw_zone
        for raw_zone in raw_zones
    ]
    return {**data, "topology": {**topology, "zones": zones}}
