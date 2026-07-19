"""Home Assistant runtime boundary for a hydronic plant."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any, cast

try:
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
except ImportError:  # pragma: no cover - lightweight unit-test Home Assistant stub
    EVENT_HOMEASSISTANT_STOP = "homeassistant_stop"
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    ACTUATOR_COMMAND_TIMEOUT_SECONDS,
    CONF_DIAGNOSTICS_INCLUDE_ACTUATOR_DETAILS,
    CONF_DRY_RUN,
    CONF_NAME,
    CONF_PLANT_ID,
    CONF_REQUESTED_MODE,
    MAX_RECONCILIATION_INTERVAL_SECONDS,
    MIN_RECONCILIATION_INTERVAL_SECONDS,
    RECONCILIATION_INTERVAL_SECONDS,
)
from .core.controller import evaluate
from .core.entity_bindings import (
    EntityBinding,
    configured_entity_bindings,
    degraded_actuator_ids,
    unresolved_entity_bindings,
)
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
    ModeChangeoverPhase,
    PlantMode,
    PlantSnapshot,
    PumpRuntime,
    PumpState,
    RuntimeState,
    SafeShutdownPhase,
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
from .repairs import async_sync_repairs


@dataclass(slots=True)
class HydronicRuntime:
    """Runtime data retained for one configured plant."""

    plant_id: str
    name: str
    dry_run: bool
    plant: CompiledPlant
    actuator_subentry_ids: Mapping[str, str] = field(default_factory=dict)
    zone_subentry_ids: Mapping[str, str] = field(default_factory=dict)
    diagnostics_include_actuator_details: bool = False
    source_subentry_ids: Mapping[str, str] = field(default_factory=dict)
    runtime_state: RuntimeState = field(default_factory=RuntimeState)
    zone_target_temperatures: dict[str, float] = field(default_factory=dict)
    zone_preset_modes: dict[str, str] = field(default_factory=dict)
    evaluation: Evaluation | None = None
    snapshot: PlantSnapshot | None = None
    last_execution: ExecutionReport | None = None
    unresolved_bindings: tuple[EntityBinding, ...] = ()
    unavailable_entity_ids: frozenset[str] = frozenset()
    _hass: HomeAssistant | None = None
    _entry: Any | None = None
    _remove_state_listener: Callable[[], None] | None = None
    _remove_transition_timer: Callable[[], None] | None = None
    _remove_reconciliation_timer: Callable[[], None] | None = None
    _remove_stop_listener: Callable[[], None] | None = None
    _listeners: set[Callable[[], None]] = field(default_factory=set)
    _tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    _refresh_task: asyncio.Task[Any] | None = None
    _stopping: bool = False
    refresh_count: int = 0
    evaluation_count: int = 0
    coalesced_refresh_count: int = 0
    reconciliation_count: int = 0
    reconciliation_changed_count: int = 0
    reconciliation_unchanged_count: int = 0
    last_reconciliation_status: str = "not_started"
    last_reconciliation_changed_actuator_count: int = 0
    _last_publication_signature: str | None = None
    executor: ActuatorExecutor = field(init=False)

    def __post_init__(self) -> None:
        """Create an executor whose state starts unknown until observed."""
        self.executor = ActuatorExecutor.from_plant(
            self.plant,
            dry_run=self.dry_run,
        )

    @classmethod
    def from_entry(cls, entry: Any) -> HydronicRuntime:
        """Construct safe runtime data from a config entry."""
        effective = effective_plant_configuration(entry)
        plant = compile_topology(effective.configuration)
        return cls(
            plant_id=str(entry.data.get(CONF_PLANT_ID, getattr(entry, "entry_id", "plant"))),
            name=str(entry.data.get(CONF_NAME, getattr(entry, "title", "Hydronic plant"))),
            dry_run=bool(entry.data.get(CONF_DRY_RUN, True)),
            plant=plant,
            actuator_subentry_ids=effective.actuator_subentry_ids,
            zone_subentry_ids=effective.zone_subentry_ids,
            diagnostics_include_actuator_details=bool(
                entry.data.get(CONF_DIAGNOSTICS_INCLUDE_ACTUATOR_DETAILS, False)
            ),
            source_subentry_ids=effective.source_subentry_ids,
            runtime_state=RuntimeState(
                requested_mode=_stored_requested_mode(entry),
            ),
            zone_target_temperatures={
                zone.id: zone.target_temperature for zone in plant.zones.values()
            },
            zone_preset_modes={
                zone.id: _stored_zone_preset_mode(entry, zone.id, zone, effective.zone_subentry_ids)
                for zone in plant.zones.values()
            },
            _entry=entry,
        )

    async def async_set_dry_run(
        self, dry_run: bool, *, hass: HomeAssistant | None = None
    ) -> bool:
        """Apply Plant Dry run, safely releasing active heating before suppression."""
        requested = bool(dry_run)
        if requested == self.dry_run:
            return True
        active_hass = hass or self._hass
        if active_hass is None or self._entry is None:
            raise RuntimeError("Hydronic runtime is not started.")

        if requested:
            report = await self.async_safe_shutdown(active_hass, force_dry_run=False)
            while (
                not report.execution.failures
                and report.plan.next_deadline is None
                and report.plan.phase is not SafeShutdownPhase.VALVES_CLOSED
            ):
                report = await self.async_safe_shutdown(active_hass, force_dry_run=False)
            if report.execution.failures or (
                report.plan.phase is not SafeShutdownPhase.VALVES_CLOSED
            ):
                return False
            self.runtime_state = replace(
                report.next_runtime,
                safe_shutdown_phase=SafeShutdownPhase.IDLE,
                safe_shutdown_started_at=None,
            )

        data = dict(self._entry.data)
        data[CONF_DRY_RUN] = requested
        active_hass.config_entries.async_update_entry(self._entry, data=data)
        self.dry_run = requested
        self.executor.dry_run = requested
        if requested:
            self._notify_listeners_if_changed()
        else:
            await self.async_refresh(active_hass)
        return True

    async def async_set_requested_mode(
        self,
        mode: PlantMode | str,
        *,
        hass: HomeAssistant | None = None,
    ) -> None:
        """Persist a requested plant mode and immediately reevaluate safely."""
        try:
            requested = PlantMode(mode)
        except ValueError as error:
            raise ValueError(f"Unsupported plant mode {mode!r}.") from error
        active_hass = hass or self._hass
        if active_hass is None or self._entry is None:
            raise RuntimeError("Hydronic runtime is not started.")
        data = dict(self._entry.data)
        data[CONF_REQUESTED_MODE] = requested.value
        active_hass.config_entries.async_update_entry(self._entry, data=data)
        self.runtime_state = replace(self.runtime_state, requested_mode=requested)
        await self.async_refresh(active_hass)

    def requested_mode(self) -> PlantMode:
        """Return the persisted operator request."""
        return self.runtime_state.requested_mode

    def active_mode(self) -> PlantMode:
        """Return the mode currently allowed to use the shared plant."""
        return self.runtime_state.plant_mode

    def mode_is_locked(self) -> bool:
        """Return whether a mode transition is waiting for safe idle."""
        return self.runtime_state.changeover_phase is not ModeChangeoverPhase.IDLE

    def mode_explanation(self) -> str:
        """Return the structured mode or lockout explanation."""
        if self.evaluation is None:
            return "The controller has not evaluated the plant yet."
        return self.evaluation.diagnostics.mode_explanation

    async def async_start(self, hass: HomeAssistant) -> None:
        """Reconcile observations, evaluate the plan, and execute safe commands."""
        self._stopping = False
        self._last_publication_signature = None
        if self._remove_state_listener is not None:
            self._remove_state_listener()
            self._remove_state_listener = None
        self._cancel_reconciliation_timer()
        if self._remove_stop_listener is not None:
            self._remove_stop_listener()
            self._remove_stop_listener = None
        self._hass = hass
        self.executor.observe_entities(self._actuator_states(hass))
        self._reconcile_actuator_runtime()
        self._remove_state_listener = async_track_state_change_event(
            hass, self._observed_entity_ids(), self._async_handle_state_change
        )
        bus = getattr(hass, "bus", None)
        if bus is not None and hasattr(bus, "async_listen_once"):
            self._remove_stop_listener = bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, self._async_handle_homeassistant_stop
            )
        await self.async_refresh(hass)
        self._schedule_periodic_reconciliation(hass)

    async def async_stop(self) -> None:
        """Remove runtime listeners without changing any physical equipment."""
        self._stopping = True
        if self._hass is not None:
            async_sync_repairs(self._hass, self.plant_id, ())
        if self._remove_state_listener is not None:
            with suppress(ValueError):
                self._remove_state_listener()
            self._remove_state_listener = None
        self._cancel_transition_timer()
        self._cancel_reconciliation_timer()
        if self._remove_stop_listener is not None:
            with suppress(ValueError):
                self._remove_stop_listener()
            self._remove_stop_listener = None
        current_task = asyncio.current_task()
        pending = [task for task in self._tasks if task is not current_task and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.difference_update(pending)
        # A cancelled periodic task can finish its finally block while the
        # gather above is unwinding, so make the timer cancellation idempotent
        # once more before detaching the Home Assistant instance.
        self._cancel_transition_timer()
        self._cancel_reconciliation_timer()
        self._refresh_task = None
        self._last_publication_signature = None
        self._listeners.clear()
        self._hass = None
        self._entry = None

    async def _async_handle_homeassistant_stop(self, _event: Event[Any]) -> None:
        """Cancel runtime work when Home Assistant itself is stopping."""
        await self.async_stop()

    async def async_set_zone_target_temperature(
        self, zone_id: str, temperature: float, *, hass: HomeAssistant | None = None
    ) -> None:
        """Persist and immediately apply a zone setpoint in the runtime."""
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
        """Persist a configured preset and immediately apply its target in the runtime."""
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
        return decision is not None and decision.status in {
            ZoneDecisionStatus.SENSOR_BLOCKED,
            ZoneDecisionStatus.MODE_BLOCKED,
        }

    def cooling_zone_blocked_reason(self, zone_id: str) -> str | None:
        """Return the structured cooling interlock explanation for one zone."""
        decision = self.cooling_zone_decision(zone_id)
        if decision is None or decision.status not in {
            ZoneDecisionStatus.SENSOR_BLOCKED,
            ZoneDecisionStatus.MODE_BLOCKED,
        }:
            return None
        return decision.explanation

    def actuator_diagnostic(self, actuator_id: str) -> object | None:
        """Return structured feedback and manual-mismatch diagnostics."""
        if self.evaluation is None:
            return None
        return self.evaluation.diagnostics.actuator_diagnostics.get(actuator_id)

    def actuator_execution_failure(self, actuator_id: str) -> object | None:
        """Return the latest rejected or timed-out command explanation."""
        return self.executor.failure_for(actuator_id)

    def execution_summary(self) -> dict[str, object]:
        """Return the latest bounded proposed, executed, and suppressed operations."""
        report = self.last_execution

        def operations(items: tuple[ActuatorOperation, ...]) -> list[dict[str, object]]:
            return [
                {
                    "actuator_id": operation.actuator_id,
                    "entity_id": operation.entity_id,
                    "service": operation.service,
                    "target_state": operation.target_state.value,
                    "target_value": operation.target_value,
                    "reason": operation.reason,
                }
                for operation in items
            ]

        if report is None:
            return {
                "dry_run": self.dry_run,
                "proposed": [],
                "executed": [],
                "suppressed": [],
                "failures": [],
            }
        return {
            "dry_run": self.dry_run,
            "proposed": operations(report.proposed),
            "executed": operations(report.executed),
            "suppressed": operations(report.suppressed),
            "failures": [failure.explanation for failure in report.failures],
        }

    async def async_safe_shutdown(
        self,
        hass: HomeAssistant | None = None,
        *,
        now: datetime | None = None,
        force_dry_run: bool | None = None,
    ) -> SafeShutdownReport:
        """Release source demand, observe overrun, then stop pumps and valves."""
        active_hass = hass or self._hass
        if active_hass is None:
            raise RuntimeError("Hydronic runtime is not started.")
        effective_now = now or self._now()
        report = await self.executor.async_safe_shutdown(
            self.plant,
            self.runtime_state,
            effective_now,
            lambda operation: self._async_dispatch_actuator(active_hass, operation),
            force_dry_run=self.dry_run if force_dry_run is None else force_dry_run,
            force_dry_run_actuator_ids=(
                frozenset({self.plant.source_selector.id})
                if self.plant.source_selector is not None
                and self.plant.source_selector.entity_id is not None
                else frozenset()
            ),
            unavailable_actuator_ids=self._unavailable_actuator_ids(),
        )
        self.runtime_state = report.next_runtime
        self.last_execution = report.execution
        self._apply_execution_contract(report.execution, effective_now)
        if report.plan.next_deadline is not None:
            self._schedule_next_transition(active_hass, effective_now)
        self._notify_listeners_if_changed()
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
            return decision.status in {
                ZoneDecisionStatus.SENSOR_BLOCKED,
                ZoneDecisionStatus.MODE_BLOCKED,
            } or (decision.aggregation is not None and decision.aggregation.is_blocked)
        aggregation = self.zone_aggregation(zone_id)
        return aggregation.is_blocked if aggregation is not None else False

    def zone_blocked_reason(self, zone_id: str) -> str | None:
        """Return the structured blocking explanation for one zone."""
        decision = self.zone_decision(zone_id)
        if decision is not None and decision.status in {
            ZoneDecisionStatus.SENSOR_BLOCKED,
            ZoneDecisionStatus.MODE_BLOCKED,
        }:
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

    def source_diagnostic(self, source_id: str) -> object | None:
        """Return one source result from the latest atomic evaluation."""
        if self.evaluation is None:
            return None
        return self.evaluation.diagnostics.source_diagnostics.get(source_id)

    def source_selection_diagnostic(self) -> object | None:
        """Return the latest atomic source-selection result."""
        if self.evaluation is None:
            return None
        return self.evaluation.diagnostics.source_selection

    def operational_status(self) -> str:
        """Return a bounded plant-level status suitable for Recorder telemetry."""
        if self._stopping:
            return "stopped"
        if self.runtime_state.safe_shutdown_phase is not SafeShutdownPhase.IDLE:
            return "safe_shutdown"
        if self.evaluation is None:
            return "initializing"
        if any(
            decision.status is ZoneDecisionStatus.SENSOR_BLOCKED
            for decision in (
                *self.evaluation.diagnostics.zone_decisions.values(),
                *self.evaluation.diagnostics.cooling_zone_decisions.values(),
            )
        ):
            return "blocked"
        return self.evaluation.control_plan.plant_mode.value

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register an entity update callback."""
        self._listeners.add(listener)

        def remove_listener() -> None:
            self._listeners.discard(listener)

        return remove_listener

    @callback
    def _async_handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Observe actuator feedback and re-evaluate after a configured state changes."""
        if self._stopping:
            return
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
        if self.runtime_state.safe_shutdown_phase is not SafeShutdownPhase.IDLE:
            self._notify_listeners_if_changed()
            return
        if self._hass is not None:
            self._schedule_refresh(self._hass)

    @callback
    def _async_handle_transition_timer(self, _now: datetime) -> None:
        """Re-evaluate when the earliest virtual deadline becomes due."""
        self._remove_transition_timer = None
        if self._hass is not None and not self._stopping:
            if self.runtime_state.safe_shutdown_phase is not SafeShutdownPhase.IDLE:
                self._schedule_task(self._hass, self.async_safe_shutdown(self._hass))
            else:
                self._schedule_refresh(self._hass)

    @callback
    def _async_handle_reconciliation_timer(self, _now: datetime) -> None:
        """Periodically read all configured actuator entities to repair missed events."""
        self._remove_reconciliation_timer = None
        if self._hass is not None and not self._stopping:
            self._schedule_task(self._hass, self._async_periodic_reconciliation(self._hass))

    async def _async_periodic_reconciliation(self, hass: HomeAssistant) -> None:
        """Re-read actuator state, then schedule the next periodic reconciliation."""
        try:
            if not self._stopping:
                previous_status = self.last_reconciliation_status
                previous_observed = dict(self.executor.observed_states)
                previous_feedback = dict(self.executor.feedback_states)
                self.executor.observe_entities(self._actuator_states(hass))
                changed_actuators = {
                    actuator_id
                    for actuator_id in set(previous_observed) | set(self.executor.observed_states)
                    if previous_observed.get(actuator_id)
                    != self.executor.observed_states.get(actuator_id)
                }
                changed_actuators.update(
                    f"feedback:{actuator_id}"
                    for actuator_id in set(previous_feedback) | set(self.executor.feedback_states)
                    if previous_feedback.get(actuator_id)
                    != self.executor.feedback_states.get(actuator_id)
                )
                self.reconciliation_count += 1
                self.last_reconciliation_changed_actuator_count = len(changed_actuators)
                if changed_actuators:
                    self.reconciliation_changed_count += 1
                    self.last_reconciliation_status = "changed"
                    self._reconcile_actuator_runtime()
                    await self.async_refresh(hass)
                else:
                    self.reconciliation_unchanged_count += 1
                    self.last_reconciliation_status = "unchanged"
                    if self.last_reconciliation_status != previous_status:
                        self._notify_listeners_if_changed()
        finally:
            if self._hass is hass and not self._stopping:
                self._schedule_periodic_reconciliation(hass)

    def _schedule_periodic_reconciliation(self, hass: HomeAssistant) -> None:
        """Schedule the next periodic actuator read."""
        self._cancel_reconciliation_timer()
        interval = min(
            max(float(RECONCILIATION_INTERVAL_SECONDS), MIN_RECONCILIATION_INTERVAL_SECONDS),
            MAX_RECONCILIATION_INTERVAL_SECONDS,
        )
        self._remove_reconciliation_timer = async_call_later(
            hass, interval, self._async_handle_reconciliation_timer
        )

    def _schedule_task(self, hass: HomeAssistant, coroutine: Any) -> asyncio.Task[Any]:
        """Track an asynchronous runtime operation so unload can cancel it."""
        try:
            task = hass.async_create_task(coroutine, eager_start=False)
        except TypeError:
            # The lightweight Home Assistant test seam predates the optional
            # eager_start argument.
            task = hass.async_create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _schedule_refresh(self, hass: HomeAssistant) -> None:
        """Coalesce state-event refreshes into one tracked task."""
        if self._refresh_task is not None and not self._refresh_task.done():
            self.coalesced_refresh_count += 1
            return
        self._refresh_task = self._schedule_task(hass, self.async_refresh(hass))

    def _cancel_transition_timer(self) -> None:
        """Cancel the pending one-shot transition timer, if any."""
        if self._remove_transition_timer is not None:
            self._remove_transition_timer()
            self._remove_transition_timer = None

    def _cancel_reconciliation_timer(self) -> None:
        """Cancel the periodic reconciliation timer, if any."""
        if self._remove_reconciliation_timer is not None:
            self._remove_reconciliation_timer()
            self._remove_reconciliation_timer = None

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

    def _refresh_binding_health(self, hass: HomeAssistant) -> None:
        """Resolve configured references and synchronize the public Repairs state."""
        if not hasattr(hass, "bus"):
            # Preserve the small Home Assistant-free runtime seam used by the
            # deterministic scheduling tests.
            self.unresolved_bindings = ()
            self.unavailable_entity_ids = frozenset()
            return
        bindings = configured_entity_bindings(self.plant)
        resolved_entity_ids = {
            binding.entity_id
            for binding in bindings
            if _entity_reference_is_resolved(hass.states.get(binding.entity_id))
        }
        self.unresolved_bindings = unresolved_entity_bindings(self.plant, resolved_entity_ids)
        self.unavailable_entity_ids = frozenset(
            binding.entity_id for binding in self.unresolved_bindings
        )
        async_sync_repairs(hass, self.plant_id, self.unresolved_bindings)

    def _unavailable_actuator_ids(self) -> frozenset[str]:
        """Return primary actuator IDs that must never receive a command."""
        return degraded_actuator_ids(self.plant, self.unavailable_entity_ids)

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
        selected = (
            actuator_ids
            if actuator_ids is not None
            else set(self.plant.valves) | set(self.plant.pumps)
        )
        for actuator_id in sorted(set(self.plant.valves) & selected):
            current = valves.get(actuator_id)
            observed = self.executor.actuator_state(actuator_id)
            readiness = self.executor.readiness_state(actuator_id)
            retained = self.executor.requested_state(actuator_id)
            failure = self.executor.failure_for(actuator_id)
            if (
                failure is not None
                and failure.operation.target_state is ActuatorObservedState.ON
                and observed is not ActuatorObservedState.ON
            ):
                valves[actuator_id] = ValveRuntime(ValveState.CLOSED, now, False)
                continue
            if readiness is True:
                valves[actuator_id] = ValveRuntime(
                    ValveState.OPEN,
                    current.changed_at if current is not None else now,
                    True,
                )
            elif retained is ActuatorObservedState.ON and current is not None:
                # A command is still pending.  Keep the timer contract instead
                # of reissuing the command when a state event was missed.
                if current.state is ValveState.OPENING:
                    valves[actuator_id] = current
                else:
                    valves[actuator_id] = ValveRuntime(ValveState.OPENING, now, False)
            elif (
                self.dry_run
                and current is not None
                and current.state is ValveState.OPENING
            ):
                # Shadow execution does not change the physical entity.  Keep
                # the virtual opening transition stable across identical reads.
                valves[actuator_id] = current
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
                valves[actuator_id] = (
                    current
                    if self.dry_run or current.state is ValveState.OPENING
                    else ValveRuntime(ValveState.OPENING, now, False)
                )

        pumps = dict(self.runtime_state.pumps)
        for actuator_id in sorted(set(self.plant.pumps) & selected):
            current = pumps.get(actuator_id)
            observed = self.executor.actuator_state(actuator_id)
            retained = self.executor.requested_state(actuator_id)
            failure = self.executor.failure_for(actuator_id)
            if (
                failure is not None
                and failure.operation.target_state is ActuatorObservedState.ON
                and observed is not ActuatorObservedState.ON
            ):
                pumps[actuator_id] = PumpRuntime(PumpState.OFF, now)
                continue
            if observed is ActuatorObservedState.ON:
                if current is not None and current.state is PumpState.OVERRUN:
                    pumps[actuator_id] = current
                else:
                    pumps[actuator_id] = PumpRuntime(
                        PumpState.RUNNING,
                        current.changed_at if current is not None else now,
                    )
            elif retained is ActuatorObservedState.ON and current is not None:
                if current.state is PumpState.STARTING:
                    pumps[actuator_id] = current
                else:
                    pumps[actuator_id] = PumpRuntime(PumpState.STARTING, now)
            elif observed is ActuatorObservedState.OFF:
                pumps[actuator_id] = PumpRuntime(
                    PumpState.OFF,
                    current.changed_at if current is not None else now,
                )
            elif current is not None and current.state is PumpState.RUNNING:
                pumps[actuator_id] = PumpRuntime(
                    PumpState.RUNNING
                    if self.dry_run
                    else PumpState.STARTING
                    if any(self.runtime_state.zone_demands.values())
                    else PumpState.OVERRUN,
                    current.changed_at or now,
                )

        observed_sources = [
            source.id
            for source in sorted(self.plant.sources.values(), key=lambda item: item.id)
            if source.demand_entity_id is not None
            and self.executor.actuator_state(f"source:{source.id}") is not ActuatorObservedState.OFF
        ]
        self.runtime_state = replace(
            self.runtime_state,
            valves=valves,
            pumps=pumps,
            selected_source_id=(
                observed_sources[0] if observed_sources else self.runtime_state.selected_source_id
            ),
        )

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
                            source.demand_entity_id,
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
        service_task = self._schedule_task(
            hass,
            hass.services.async_call(
                operation.domain,
                operation.service,
                {"entity_id": operation.entity_id},
                blocking=True,
            )
        )
        try:
            await asyncio.wait_for(asyncio.shield(service_task), ACTUATOR_COMMAND_TIMEOUT_SECONDS)
        finally:
            if service_task.done():
                self._tasks.discard(service_task)

    def _next_transition_delay(self, now: datetime) -> float | None:
        """Return seconds until the earliest actuator, duration, or stale deadline."""
        delays: list[float] = []
        if self.runtime_state.safe_shutdown_phase is SafeShutdownPhase.PUMPS_STOPPED:
            return 0.0
        if self.runtime_state.changeover_deadline is not None:
            delays.append(max(0.0, (self.runtime_state.changeover_deadline - now).total_seconds()))
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

        selection = self.runtime_state.source_selection
        if self.plant.source_selector is not None:
            if selection.phase.value == "breaking" and selection.transition_started_at is not None:
                deadline = selection.transition_started_at + timedelta(
                    seconds=self.plant.source_selector.break_interval_seconds
                )
                if deadline > now:
                    delays.append((deadline - now).total_seconds())
            if selection.phase.value == "minimum_dwell" and selection.last_selected_at is not None:
                deadline = selection.last_selected_at + timedelta(
                    seconds=self.plant.source_selector.minimum_dwell_seconds
                )
                if deadline > now:
                    delays.append((deadline - now).total_seconds())

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
            if self.runtime_state.safe_shutdown_phase is not SafeShutdownPhase.IDLE:
                self._schedule_task(hass, self.async_safe_shutdown(hass))
            else:
                self._schedule_refresh(hass)
            return
        self._remove_transition_timer = async_call_later(
            hass, delay, self._async_handle_transition_timer
        )

    async def async_refresh(self, hass: HomeAssistant) -> None:
        """Read sensor states, evaluate the controller, and notify shadow entities."""
        if self._stopping:
            return
        self._refresh_binding_health(hass)
        self.refresh_count += 1
        if self.runtime_state.safe_shutdown_phase is not SafeShutdownPhase.IDLE:
            await self.async_safe_shutdown(hass)
            return
        self.executor.observe_entities(self._actuator_states(hass))
        self._reconcile_actuator_runtime()
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
        source_selector_states: dict[str, str | None] = {}
        source_demand_states: dict[str, bool] = {}
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
            if source.demand_entity_id is not None:
                state = hass.states.get(source.demand_entity_id)
                if state is not None:
                    source_demand_states[source.id] = _state_is_on(state)
        if (
            self.plant.source_selector is not None
            and self.plant.source_selector.entity_id is not None
        ):
            state = hass.states.get(self.plant.source_selector.entity_id)
            source_selector_states[self.plant.source_selector.id] = (
                getattr(state, "state", None) if state is not None else None
            )
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
            source_selector_states=source_selector_states,
            source_demand_states=source_demand_states,
            actuator_feedback=actuator_feedback,
            unavailable_entity_ids=self.unavailable_entity_ids,
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
        self.evaluation_count += 1
        self.runtime_state = result.next_runtime
        self.evaluation = result
        self.last_execution = await self.executor.async_execute(
            result.control_plan,
            lambda operation: self._async_dispatch_actuator(hass, operation),
            unavailable_actuator_ids=self._unavailable_actuator_ids(),
            force_dry_run_start_actuator_ids=result.control_plan.cooling_actuator_ids,
            force_dry_run_actuator_ids=(
                frozenset({self.plant.source_selector.id})
                if self.plant.source_selector is not None
                and self.plant.source_selector.entity_id is not None
                else frozenset()
            ),
        )
        self._apply_execution_contract(self.last_execution, now)
        if self._entry is not None or self._hass is not None:
            self._schedule_next_transition(hass, now)
        self._notify_listeners_if_changed()

    def _notify_listeners_if_changed(self) -> None:
        """Publish only when recorder-visible runtime state has changed."""
        signature = repr(
            (
                self.runtime_state,
                tuple(sorted(self.zone_target_temperatures.items())),
                tuple(sorted(self.zone_preset_modes.items())),
                self.operational_status(),
                self.last_reconciliation_status,
                self.last_reconciliation_changed_actuator_count,
                self._evaluation_publication_signature(),
                tuple(sorted(self.executor.failure_states.items())),
                tuple(sorted(self.executor.reconciliations.items()))
                if self.diagnostics_include_actuator_details
                else (),
            )
        )
        if signature == self._last_publication_signature:
            return
        self._last_publication_signature = signature
        for listener in tuple(self._listeners):
            listener()

    def _evaluation_publication_signature(self) -> object:
        """Return evaluation data without volatile deadline timestamps."""
        if self.evaluation is None:
            return None
        diagnostics = self.evaluation.diagnostics

        def decision_signature(decision: ZoneDecision) -> tuple[object, ...]:
            aggregation = decision.aggregation
            return (
                decision.status,
                decision.demand,
                aggregation.value if aggregation is not None else None,
                aggregation.usable_sensor_ids if aggregation is not None else (),
                aggregation.excluded_optional_sensor_ids if aggregation is not None else (),
                aggregation.blocking_required_sensor_ids if aggregation is not None else (),
                decision.explanation,
                decision.deadline is not None,
                decision.humidity_aggregation.value
                if decision.humidity_aggregation is not None
                else None,
                decision.dew_point,
                decision.condensation_margin,
                tuple(
                    (interlock.interlock_id, interlock.status, interlock.reason)
                    for interlock in decision.interlocks
                ),
            )

        return (
            tuple(
                (zone_id, decision_signature(decision))
                for zone_id, decision in sorted(diagnostics.zone_decisions.items())
            ),
            tuple(
                (zone_id, decision_signature(decision))
                for zone_id, decision in sorted(diagnostics.cooling_zone_decisions.items())
            ),
            tuple(sorted(diagnostics.zone_reasons.items())),
            tuple(sorted(diagnostics.interlocks.items())),
            diagnostics.source_recommendation,
            tuple(sorted(diagnostics.source_diagnostics.items())),
            diagnostics.source_selection,
            diagnostics.mode_conflicts,
            tuple(
                (
                    actuator_id,
                    diagnostic.status,
                    diagnostic.mismatch,
                    diagnostic.blocked,
                    diagnostic.expected,
                    diagnostic.observed,
                    diagnostic.stale_feedback,
                )
                for actuator_id, diagnostic in sorted(diagnostics.actuator_diagnostics.items())
            ),
            (
                tuple(sorted(diagnostics.circuit_reasons.items())),
                tuple(sorted(diagnostics.actuator_reasons.items())),
                tuple(sorted(self.executor.reconciliations.items())),
            )
            if self.diagnostics_include_actuator_details
            else (),
        )

    def _now(self) -> datetime:
        """Read the UTC clock in one place so timer tests can control it."""
        return datetime.now(UTC)

    def _apply_execution_contract(self, report: ExecutionReport, now: datetime) -> None:
        """Keep physical pumps in starting state until an observation confirms them."""
        if self.dry_run and not report.failures:
            return
        pumps = dict(self.runtime_state.pumps)
        valves = dict(self.runtime_state.valves)
        changed = False
        for operation in report.executed:
            if operation.actuator_id not in self.plant.pumps:
                continue
            if operation.target_state is ActuatorObservedState.ON:
                pumps[operation.actuator_id] = PumpRuntime(PumpState.STARTING, now)
                changed = True
        for failure in report.failures:
            operation = failure.operation
            if operation.actuator_id in self.plant.valves and operation.target_state in {
                ActuatorObservedState.ON,
                ActuatorObservedState.OPEN,
            }:
                valves[operation.actuator_id] = ValveRuntime(ValveState.CLOSED, now, False)
                changed = True
            elif operation.actuator_id in self.plant.valves and operation.target_state in {
                ActuatorObservedState.OFF,
                ActuatorObservedState.CLOSED,
            }:
                valves[operation.actuator_id] = ValveRuntime(
                    ValveState.CLOSED
                    if self.executor.actuator_state(operation.actuator_id)
                    is operation.target_state
                    else ValveState.OPENING,
                    now,
                    False,
                )
                changed = True
            elif (
                operation.actuator_id in self.plant.pumps
                and operation.target_state is ActuatorObservedState.ON
            ):
                pumps[operation.actuator_id] = PumpRuntime(PumpState.OFF, now)
                changed = True
            elif (
                operation.actuator_id in self.plant.pumps
                and operation.target_state is ActuatorObservedState.OFF
            ):
                pumps[operation.actuator_id] = PumpRuntime(
                    PumpState.OFF
                    if self.executor.actuator_state(operation.actuator_id)
                    is ActuatorObservedState.OFF
                    else PumpState.RUNNING,
                    now,
                )
                changed = True
        if changed:
            self.runtime_state = replace(self.runtime_state, valves=valves, pumps=pumps)


def _stored_requested_mode(entry: Any) -> PlantMode:
    """Decode the operator mode conservatively across old config entries."""
    raw_mode = entry.data.get(CONF_REQUESTED_MODE, PlantMode.AUTO.value)
    try:
        return PlantMode(raw_mode)
    except ValueError:
        return PlantMode.AUTO


_PRESET_MODES = {"comfort", "eco", "away"}


def _entity_reference_is_resolved(state: Any) -> bool:
    """Return whether Home Assistant currently exposes a usable entity state."""
    if state is None:
        return False
    return str(getattr(state, "state", "")).strip().lower() not in {"unknown", "unavailable"}


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


def _state_is_on(state: Any) -> bool:
    """Interpret a synthetic source-demand switch state conservatively."""
    if state is None:
        return False
    return str(state.state).strip().lower() in {"on", "true", "1", "yes"}


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
