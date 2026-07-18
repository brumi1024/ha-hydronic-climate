"""Home Assistant runtime boundary for a hydronic plant."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any, cast

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    CONF_ACTUATOR_SHADOW_MODES,
    CONF_NAME,
    CONF_PLANT_ID,
    CONF_SHADOW_MODE,
)
from .core.controller import evaluate
from .core.executor import (
    ActuatorExecutor,
    ActuatorObservedState,
    ActuatorOperation,
    ExecutionReport,
    SafeShutdownReport,
)
from .core.model import (
    MAX_ZONE_TARGET_TEMPERATURE,
    MIN_ZONE_TARGET_TEMPERATURE,
    ActuatorFeedback,
    AggregationResult,
    CompiledPlant,
    Evaluation,
    FeedbackObservation,
    PlantMode,
    PlantSnapshot,
    PumpRuntime,
    PumpState,
    RuntimeState,
    SourceRecommendation,
    TemperatureObservation,
    ValveRuntime,
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
    actuator_shadow_modes: Mapping[str, bool] = field(default_factory=dict)
    source_subentry_ids: Mapping[str, str] = field(default_factory=dict)
    runtime_state: RuntimeState = field(default_factory=RuntimeState)
    zone_target_temperatures: dict[str, float] = field(default_factory=dict)
    zone_preset_modes: dict[str, str] = field(default_factory=dict)
    evaluation: Evaluation | None = None
    snapshot: PlantSnapshot | None = None
    last_execution: ExecutionReport | None = None
    _hass: HomeAssistant | None = None
    _entry: Any | None = None
    _remove_state_listener: Callable[[], None] | None = None
    _remove_transition_timer: Callable[[], None] | None = None
    _listeners: set[Callable[[], None]] = field(default_factory=set)
    executor: ActuatorExecutor = field(init=False)

    def __post_init__(self) -> None:
        """Create an executor whose state starts unknown until observed."""
        self.executor = ActuatorExecutor.from_plant(
            self.plant,
            shadow_mode=self.shadow_mode,
            actuator_shadow_modes=self.actuator_shadow_modes,
        )

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
            actuator_shadow_modes=_stored_actuator_shadow_modes(entry),
            source_subentry_ids=effective.source_subentry_ids,
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
        """Reconcile observations, evaluate the plan, and execute safe commands."""
        if self._remove_state_listener is not None:
            self._remove_state_listener()
            self._remove_state_listener = None
        self._hass = hass
        self.executor.observe_entities(self._actuator_states(hass))
        self._reconcile_actuator_runtime()
        await self.async_refresh(hass)
        self._remove_state_listener = async_track_state_change_event(
            hass, self._observed_entity_ids(), self._async_handle_state_change
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

    def cooling_zone_decision(self, zone_id: str) -> ZoneDecision | None:
        """Return the structured cooling decision for one zone."""
        if self.evaluation is None:
            return None
        return self.evaluation.diagnostics.cooling_zone_decisions.get(zone_id)

    def cooling_zone_is_blocked(self, zone_id: str) -> bool:
        """Return whether cooling safety currently blocks one zone."""
        decision = self.cooling_zone_decision(zone_id)
        return decision is not None and decision.status is ZoneDecisionStatus.SENSOR_BLOCKED

    def cooling_zone_blocked_reason(self, zone_id: str) -> str | None:
        """Return the structured cooling interlock explanation for one zone."""
        decision = self.cooling_zone_decision(zone_id)
        if decision is None or decision.status is not ZoneDecisionStatus.SENSOR_BLOCKED:
            return None
        return decision.explanation

    def actuator_diagnostic(self, actuator_id: str) -> object | None:
        """Return structured feedback and manual-mismatch diagnostics."""
        if self.evaluation is None:
            return None
        return self.evaluation.diagnostics.actuator_diagnostics.get(actuator_id)

    async def async_safe_shutdown(
        self,
        hass: HomeAssistant | None = None,
        *,
        now: datetime | None = None,
    ) -> SafeShutdownReport:
        """Release source demand, observe overrun, then stop pumps and valves."""
        active_hass = hass or self._hass
        if active_hass is None:
            raise RuntimeError("Hydronic runtime is not started.")
        report = await self.executor.async_safe_shutdown(
            self.plant,
            self.runtime_state,
            now or self._now(),
            lambda operation: self._async_dispatch_actuator(active_hass, operation),
            force_shadow=self.shadow_mode,
        )
        self.runtime_state = report.next_runtime
        self.last_execution = report.execution
        if report.plan.next_deadline is not None:
            self._schedule_next_transition(active_hass, now or self._now())
        for listener in self._listeners:
            listener()
        return report

    def zone_dew_point(self, zone_id: str) -> float | None:
        """Return the last calculated dew point for one zone."""
        decision = self.cooling_zone_decision(zone_id)
        return decision.dew_point if decision is not None else None

    def zone_condensation_margin(self, zone_id: str) -> float | None:
        """Return the lowest configured reference margin for one zone."""
        decision = self.cooling_zone_decision(zone_id)
        return decision.condensation_margin if decision is not None else None

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

    def source_recommendation(self) -> SourceRecommendation | None:
        """Return the structured shadow source recommendation, when configured."""
        if self.evaluation is None:
            return None
        return self.evaluation.diagnostics.source_recommendation

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register an entity update callback."""
        self._listeners.add(listener)

        def remove_listener() -> None:
            self._listeners.discard(listener)

        return remove_listener

    @callback
    def _async_handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Observe actuator feedback and re-evaluate after a configured state changes."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        if entity_id is not None:
            self.executor.observe_entity_state(
                entity_id,
                getattr(new_state, "state", None),
            )
            actuator_ids = self._actuator_ids_for_entity(entity_id)
            if actuator_ids:
                self._reconcile_actuator_runtime(actuator_ids)
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
        """Return every configured temperature sensor once."""
        return tuple(
            dict.fromkeys(
                sensor_id
                for zone in self.plant.zones.values()
                for sensor_id in zone.temperature_sensors
            )
        )

    def _actuator_states(self, hass: HomeAssistant) -> dict[str, str | None]:
        """Read configured actuator states without deriving desired state."""
        states = {
            binding.entity_id: getattr(hass.states.get(binding.entity_id), "state", None)
            for binding in self.executor.bindings.values()
        }
        states.update(
            {
                entity_id: getattr(hass.states.get(entity_id), "state", None)
                for entity_id in self.executor.readiness_bindings.values()
            }
        )
        return states

    def _actuator_ids_for_entity(self, entity_id: str) -> set[str]:
        """Return actuators whose command or readiness feedback uses one entity."""
        return {
            *{
                actuator_id
                for actuator_id, binding in self.executor.bindings.items()
                if binding.entity_id == entity_id
            },
            *{
                actuator_id
                for actuator_id, feedback_entity_id in self.executor.readiness_bindings.items()
                if feedback_entity_id == entity_id
            },
        }

    def _reconcile_actuator_runtime(self, actuator_ids: set[str] | None = None) -> None:
        """Seed virtual actuator state from feedback without trusting commands as feedback."""
        now = self._now()
        valves = dict(self.runtime_state.valves)
        selected = actuator_ids if actuator_ids is not None else set(self.plant.valves) | set(
            self.plant.pumps
        )
        for actuator_id in sorted(set(self.plant.valves) & selected):
            current = valves.get(actuator_id)
            observed = self.executor.actuator_state(actuator_id)
            readiness = self.executor.readiness_state(actuator_id)
            if readiness is True:
                valves[actuator_id] = ValveRuntime(ValveState.OPEN, now, True)
            elif readiness is False or observed is ActuatorObservedState.OFF:
                valves[actuator_id] = ValveRuntime(ValveState.CLOSED, now, False)
            elif observed is ActuatorObservedState.ON:
                if current is None or current.state is ValveState.CLOSED:
                    valves[actuator_id] = ValveRuntime(ValveState.OPENING, now, False)
                elif current.state is ValveState.OPENING:
                    valves[actuator_id] = ValveRuntime(
                        ValveState.OPENING,
                        current.changed_at or now,
                        False,
                    )
            elif current is not None:
                # An unknown transition invalidates a previous ready assumption.
                valves[actuator_id] = ValveRuntime(ValveState.OPENING, now, False)

        pumps = dict(self.runtime_state.pumps)
        for actuator_id in sorted(set(self.plant.pumps) & selected):
            current = pumps.get(actuator_id)
            observed = self.executor.actuator_state(actuator_id)
            if observed is ActuatorObservedState.ON:
                pumps[actuator_id] = PumpRuntime(PumpState.RUNNING, now)
            elif observed is ActuatorObservedState.OFF:
                pumps[actuator_id] = PumpRuntime(PumpState.OFF, now)
            elif current is not None and current.state is PumpState.RUNNING:
                pumps[actuator_id] = PumpRuntime(
                    PumpState.OFF
                    if any(self.runtime_state.zone_demands.values())
                    else PumpState.OVERRUN,
                    now,
                )

        self.runtime_state = replace(self.runtime_state, valves=valves, pumps=pumps)

    def _humidity_sensor_ids(self) -> tuple[str, ...]:
        """Return every configured humidity sensor once."""
        return tuple(
            dict.fromkeys(
                sensor_id
                for zone in self.plant.zones.values()
                for sensor_id in zone.humidity_sensors
            )
        )

    def _reference_sensor_ids(self) -> tuple[str, ...]:
        """Return every configured supply and surface reference once."""
        return tuple(
            dict.fromkeys(
                sensor_id
                for circuit in self.plant.circuits.values()
                for sensor_id in (
                    circuit.supply_temperature_sensor,
                    circuit.surface_temperature_sensor,
                )
                if sensor_id is not None
            )
        )

    def _observation_sensor_ids(self) -> tuple[str, ...]:
        """Return all configured observation entities for listeners and refresh."""
        return tuple(
            dict.fromkeys(
                (
                    *self._temperature_sensor_ids(),
                    *self._humidity_sensor_ids(),
                    *self._reference_sensor_ids(),
                )
            )
        )

    def _feedback_sensor_ids(self) -> tuple[str, ...]:
        """Return every configured actuator feedback entity once."""
        valve_feedback = tuple(
            dict.fromkeys(
                entity_id
                for valve in self.plant.valves.values()
                for entity_id in (valve.position_entity_id,)
                if entity_id is not None
            )
        )
        pump_feedback = tuple(
            dict.fromkeys(
                entity_id
                for pump in self.plant.pumps.values()
                for entity_id in (
                    pump.power_entity_id,
                    pump.flow_entity_id,
                    pump.fault_entity_id,
                )
                if entity_id is not None
            )
        )
        return valve_feedback + pump_feedback

    def _observed_entity_ids(self) -> tuple[str, ...]:
        """Return observations, source inputs, and actuators for one listener."""
        return tuple(
            dict.fromkeys(
                (
                    *self._observation_sensor_ids(),
                    *self._feedback_sensor_ids(),
                    *(
                        entity_id
                        for source in self.plant.sources.values()
                        for entity_id in (
                            source.availability_entity_id,
                            source.temperature_entity_id,
                        )
                        if entity_id is not None
                    ),
                    *self.executor.readiness_bindings.values(),
                    *(binding.entity_id for binding in self.executor.bindings.values()),
                )
            )
        )

    async def _async_dispatch_actuator(
        self, hass: HomeAssistant, operation: ActuatorOperation
    ) -> None:
        """Translate a generic operation into one explicit Home Assistant service call."""
        await hass.services.async_call(
            operation.domain,
            operation.service,
            {"entity_id": operation.entity_id},
            blocking=True,
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
            for source in self.plant.sources.values():
                if source.temperature_entity_id is None:
                    continue
                observation = self.snapshot.source_temperatures.get(source.id)
                if observation is None or observation.observed_at is None:
                    continue
                deadline = observation.observed_at + timedelta(seconds=source.maximum_age_seconds)
                if deadline > now:
                    delays.append((deadline - now).total_seconds())
            for sensor_id in self._humidity_sensor_ids():
                observation = self.snapshot.humidities.get(sensor_id)
                if observation is None or observation.observed_at is None:
                    continue
                max_age = min(
                    sensor.max_age_seconds
                    for zone in self.plant.zones.values()
                    for sensor in zone.humidity_sensor_metadata
                    if sensor.entity_id == sensor_id
                )
                deadline = observation.observed_at + timedelta(seconds=max_age)
                if deadline > now:
                    delays.append((deadline - now).total_seconds())
            for circuit in self.plant.circuits.values():
                for observation, max_age in (
                    (circuit.supply_temperature_sensor, circuit.supply_temperature_max_age_seconds),
                    (
                        circuit.surface_temperature_sensor,
                        circuit.surface_temperature_max_age_seconds,
                    ),
                ):
                    if observation is None:
                        continue
                    reading = (
                        self.snapshot.supply_temperatures.get(observation)
                        if observation == circuit.supply_temperature_sensor
                        else self.snapshot.surface_temperatures.get(observation)
                    )
                    if reading is None or reading.observed_at is None:
                        continue
                    deadline = reading.observed_at + timedelta(seconds=max_age)
                    if deadline > now:
                        delays.append((deadline - now).total_seconds())

            for valve in self.plant.valves.values():
                if valve.position_entity_id is None:
                    continue
                observation = self.snapshot.actuator_feedback.get(valve.id)
                reading = observation.position if observation is not None else None
                if reading is not None and reading.observed_at is not None:
                    deadline = reading.observed_at + timedelta(
                        seconds=valve.position_max_age_seconds
                    )
                    if deadline > now:
                        delays.append((deadline - now).total_seconds())
            for pump in self.plant.pumps.values():
                feedback = self.snapshot.actuator_feedback.get(pump.id)
                for kind, entity_id, max_age in (
                    ("power", pump.power_entity_id, pump.power_max_age_seconds),
                    ("flow", pump.flow_entity_id, pump.flow_max_age_seconds),
                    ("fault", pump.fault_entity_id, pump.fault_max_age_seconds),
                ):
                    if entity_id is None:
                        continue
                    reading = getattr(feedback, kind, None) if feedback is not None else None
                    if reading is not None and reading.observed_at is not None:
                        deadline = reading.observed_at + timedelta(seconds=max_age)
                        if deadline > now:
                            delays.append((deadline - now).total_seconds())

        return min(delays) if delays else None

    def _schedule_next_transition(self, hass: HomeAssistant, now: datetime) -> None:
        """Replace the pending timer with the earliest controller deadline."""
        self._cancel_transition_timer()
        delay = self._next_transition_delay(now)
        if delay is None:
            return
        if delay == 0:
            hass.async_create_task(self.async_refresh(hass))
            return
        self._remove_transition_timer = async_call_later(
            hass, delay, self._async_handle_transition_timer
        )

    async def async_refresh(self, hass: HomeAssistant) -> None:
        """Read sensor states, evaluate the controller, and notify shadow entities."""
        observations: dict[str, TemperatureObservation] = {}
        for sensor_id in self._observation_sensor_ids():
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
        source_temperatures: dict[str, TemperatureObservation] = {}
        source_availability: dict[str, bool] = {}
        for source in self.plant.sources.values():
            if source.temperature_entity_id is not None:
                state = hass.states.get(source.temperature_entity_id)
                try:
                    value = float(state.state) if state is not None else None
                except TypeError, ValueError:
                    value = None
                observed_at = None
                if state is not None:
                    observed_at = getattr(state, "last_reported", None)
                    if observed_at is None:
                        observed_at = getattr(state, "last_updated", None)
                source_temperatures[source.id] = TemperatureObservation(value, observed_at)
            if source.availability_entity_id is not None:
                state = hass.states.get(source.availability_entity_id)
                source_availability[source.id] = _state_is_available(state)
        actuator_feedback: dict[str, ActuatorFeedback] = {}

        def feedback_reading(entity_id: str | None) -> FeedbackObservation | None:
            """Read one configured feedback entity without coercing its meaning."""
            if entity_id is None:
                return None
            state = hass.states.get(entity_id)
            if state is None:
                return FeedbackObservation(None, None)
            raw_state = getattr(state, "state", None)
            value: float | bool | str | None = raw_state
            with suppress(TypeError, ValueError):
                value = float(raw_state)
            observed_at = getattr(state, "last_reported", None)
            if observed_at is None:
                observed_at = getattr(state, "last_updated", None)
            return FeedbackObservation(value, observed_at)

        for valve in self.plant.valves.values():
            if valve.position_entity_id is not None:
                actuator_feedback[valve.id] = ActuatorFeedback(
                    position=feedback_reading(valve.position_entity_id)
                )
        for pump in self.plant.pumps.values():
            if any(
                entity_id is not None
                for entity_id in (pump.power_entity_id, pump.flow_entity_id, pump.fault_entity_id)
            ):
                actuator_feedback[pump.id] = ActuatorFeedback(
                    power=feedback_reading(pump.power_entity_id),
                    flow=feedback_reading(pump.flow_entity_id),
                    fault=feedback_reading(pump.fault_entity_id),
                )
        temperature_ids = set(self._temperature_sensor_ids())
        humidity_ids = set(self._humidity_sensor_ids())
        supply_ids = {
            circuit.supply_temperature_sensor
            for circuit in self.plant.circuits.values()
            if circuit.supply_temperature_sensor is not None
        }
        surface_ids = {
            circuit.surface_temperature_sensor
            for circuit in self.plant.circuits.values()
            if circuit.surface_temperature_sensor is not None
        }
        self.snapshot = PlantSnapshot(
            temperatures={sensor_id: observations[sensor_id] for sensor_id in temperature_ids},
            humidities={sensor_id: observations[sensor_id] for sensor_id in humidity_ids},
            supply_temperatures={sensor_id: observations[sensor_id] for sensor_id in supply_ids},
            surface_temperatures={sensor_id: observations[sensor_id] for sensor_id in surface_ids},
            source_temperatures=source_temperatures,
            source_availability=source_availability,
            actuator_feedback=actuator_feedback,
        )
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
        cooling_shadow_only = result.control_plan.plant_mode is PlantMode.COOLING or (
            self.runtime_state.plant_mode is PlantMode.COOLING
            and not any(result.next_runtime.zone_demands.values())
        )
        self.runtime_state = result.next_runtime
        self.evaluation = result
        self.last_execution = await self.executor.async_execute(
            result.control_plan,
            lambda operation: self._async_dispatch_actuator(hass, operation),
            force_shadow=cooling_shadow_only,
            force_shadow_actuator_ids=result.control_plan.cooling_actuator_ids,
        )
        if self._entry is not None or self._hass is not None:
            self._schedule_next_transition(hass, now)
        for listener in self._listeners:
            listener()

    def _now(self) -> datetime:
        """Read the UTC clock in one place so timer tests can control it."""
        return datetime.now(UTC)


_PRESET_MODES = {"comfort", "eco", "away"}


def _state_is_available(state: Any) -> bool:
    """Interpret common Home Assistant availability helper states."""
    if state is None:
        return False
    normalized = str(state.state).strip().lower()
    if normalized in {"on", "true", "1", "yes", "available", "ready", "home"}:
        return True
    if normalized in {"off", "false", "0", "no", "unavailable", "unknown", "away"}:
        return False
    return False


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


def _stored_actuator_shadow_modes(entry: Any) -> Mapping[str, bool]:
    """Read optional per-actuator shadow flags without coercing malformed values."""
    raw = entry.data.get(CONF_ACTUATOR_SHADOW_MODES, {})
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(actuator_id): value for actuator_id, value in raw.items() if isinstance(value, bool)
    }


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
