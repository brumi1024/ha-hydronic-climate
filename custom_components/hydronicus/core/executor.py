"""Deterministic actuator command execution without Home Assistant imports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .controller import safe_shutdown as build_safe_shutdown
from .model import (
    ActuatorAction,
    ActuatorCommand,
    CompiledPlant,
    ControlPlan,
    RuntimeState,
    SafeShutdownPlan,
)


class ActuatorObservedState(StrEnum):
    """Last trustworthy state observed or confirmed for one actuator."""

    UNKNOWN = "unknown"
    OPEN = "open"
    CLOSED = "closed"
    ON = "on"
    OFF = "off"


class ActuatorFailureKind(StrEnum):
    """Stable categories for an unsuccessful actuator service call."""

    TIMEOUT = "timeout"
    REJECTED = "rejected"


class ReconciliationStatus(StrEnum):
    """Outcome of comparing desired, observed, and retained command state."""

    OBSERVED = "observed"
    RETAINED = "retained"
    REQUIRED = "required"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ActuatorBinding:
    """The Home Assistant entity bound to one controller actuator ID."""

    actuator_id: str
    entity_id: str


@dataclass(frozen=True, slots=True)
class ActuatorOperation:
    """One explicit Home Assistant operation ready for adapter dispatch."""

    actuator_id: str
    entity_id: str
    domain: str
    service: str
    target_state: ActuatorObservedState


@dataclass(frozen=True, slots=True)
class ActuatorExecutionFailure:
    """A deterministic explanation for one rejected or timed-out command."""

    operation: ActuatorOperation
    kind: ActuatorFailureKind
    explanation: str

    @property
    def actuator_id(self) -> str:
        """Return the failed actuator identifier."""
        return self.operation.actuator_id


ExecutionFailure = ActuatorExecutionFailure


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """The state comparison used before an actuator operation is dispatched."""

    actuator_id: str
    desired: ActuatorObservedState
    observed: ActuatorObservedState
    retained: ActuatorObservedState | None
    status: ReconciliationStatus
    explanation: str


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """The result of executing one immutable control plan."""

    executed: tuple[ActuatorOperation, ...] = ()
    suppressed: tuple[ActuatorOperation, ...] = ()
    shadowed: tuple[ActuatorOperation, ...] = ()
    failures: tuple[ActuatorExecutionFailure, ...] = ()

    @property
    def failed(self) -> tuple[ActuatorExecutionFailure, ...]:
        """Return failed operations using the concise public spelling."""
        return self.failures


@dataclass(frozen=True, slots=True)
class SafeShutdownReport:
    """Synthetic or shadow execution result for one shutdown phase."""

    plan: SafeShutdownPlan
    next_runtime: RuntimeState
    execution: ExecutionReport


type DispatchOperation = Callable[[ActuatorOperation], Awaitable[None]]


def _entity_domain(entity_id: str) -> str:
    """Return the Home Assistant domain from an entity ID."""
    domain, separator, _object_id = entity_id.partition(".")
    if not separator or domain not in {"switch", "valve"}:
        raise ValueError(
            f"Actuator entity {entity_id!r} must belong to the switch or valve domain."
        )
    return domain


def _observation_domain(entity_id: str) -> str:
    """Return the domain accepted for actuator or readiness observations."""
    domain, separator, _object_id = entity_id.partition(".")
    if not separator or domain not in {"switch", "valve", "binary_sensor"}:
        raise ValueError(
            f"Actuator feedback entity {entity_id!r} must belong to a switch, valve, "
            "or binary_sensor domain."
        )
    return domain


def operation_for(command: ActuatorCommand, binding: ActuatorBinding) -> ActuatorOperation:
    """Translate one explicit domain command into a non-toggle service operation."""
    domain = _entity_domain(binding.entity_id)
    if domain == "switch":
        if command.action is ActuatorAction.OPEN or command.action is ActuatorAction.TURN_ON:
            service = "turn_on"
            target_state = ActuatorObservedState.ON
        else:
            service = "turn_off"
            target_state = ActuatorObservedState.OFF
    elif command.action is ActuatorAction.OPEN:
        service = "open_valve"
        target_state = ActuatorObservedState.OPEN
    elif command.action is ActuatorAction.CLOSE:
        service = "close_valve"
        target_state = ActuatorObservedState.CLOSED
    else:
        raise ValueError(
            f"Native valve actuator {binding.actuator_id!r} requires open or close, "
            f"not {command.action.value}."
        )

    return ActuatorOperation(
        actuator_id=binding.actuator_id,
        entity_id=binding.entity_id,
        domain=domain,
        service=service,
        target_state=target_state,
    )


def observed_state_for(entity_id: str, state: str | None) -> ActuatorObservedState:
    """Convert a Home Assistant entity state into a conservative actuator state."""
    if state is None:
        return ActuatorObservedState.UNKNOWN
    normalized = str(state).lower()
    domain = _observation_domain(entity_id)
    if domain in {"switch", "binary_sensor"}:
        return {
            "on": ActuatorObservedState.ON,
            "off": ActuatorObservedState.OFF,
        }.get(normalized, ActuatorObservedState.UNKNOWN)
    return {
        "open": ActuatorObservedState.OPEN,
        "closed": ActuatorObservedState.CLOSED,
    }.get(normalized, ActuatorObservedState.UNKNOWN)


@dataclass(slots=True)
class ActuatorExecutor:
    """Execute explicit, idempotent commands against generic HA entities."""

    bindings: Mapping[str, ActuatorBinding]
    shadow_mode: bool = True
    actuator_shadow_modes: Mapping[str, bool] = field(default_factory=dict)
    readiness_bindings: Mapping[str, str] = field(default_factory=dict)
    observed_states: dict[str, ActuatorObservedState] = field(default_factory=dict)
    feedback_states: dict[str, ActuatorObservedState] = field(default_factory=dict)
    requested_states: dict[str, ActuatorObservedState] = field(default_factory=dict)
    failure_states: dict[str, ActuatorExecutionFailure] = field(default_factory=dict)
    reconciliations: dict[str, ReconciliationResult] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Copy configuration and initialize every actuator conservatively."""
        self.bindings = dict(self.bindings)
        self.actuator_shadow_modes = {
            str(actuator_id): bool(shadow)
            for actuator_id, shadow in self.actuator_shadow_modes.items()
        }
        self.readiness_bindings = {
            str(actuator_id): str(entity_id)
            for actuator_id, entity_id in self.readiness_bindings.items()
            if str(actuator_id) in self.bindings
        }
        self.observed_states = {
            actuator_id: self.observed_states.get(actuator_id, ActuatorObservedState.UNKNOWN)
            for actuator_id in self.bindings
        }
        self.feedback_states = {
            actuator_id: self.feedback_states.get(actuator_id, ActuatorObservedState.UNKNOWN)
            for actuator_id in self.readiness_bindings
        }
        self.requested_states = {
            actuator_id: self.requested_states[actuator_id]
            for actuator_id in self.bindings
            if actuator_id in self.requested_states
        }
        self.failure_states = {
            actuator_id: failure
            for actuator_id, failure in self.failure_states.items()
            if actuator_id in self.bindings
        }
        self.reconciliations = {
            actuator_id: reconciliation
            for actuator_id, reconciliation in self.reconciliations.items()
            if actuator_id in self.bindings
        }

    @classmethod
    def from_plant(
        cls,
        plant: CompiledPlant,
        *,
        shadow_mode: bool = True,
        actuator_shadow_modes: Mapping[str, bool] | None = None,
    ) -> ActuatorExecutor:
        """Build generic bindings from the compiled plant topology."""
        bindings: dict[str, ActuatorBinding] = {}
        for actuator_id, valve in plant.valves.items():
            bindings[actuator_id] = ActuatorBinding(actuator_id, valve.entity_id)
        readiness_bindings = {
            actuator_id: valve.readiness_entity_id
            for actuator_id, valve in plant.valves.items()
            if valve.readiness_entity_id is not None
        }
        for actuator_id, pump in plant.pumps.items():
            if actuator_id in bindings:
                raise ValueError(f"Actuator ID {actuator_id!r} is used by more than one actuator.")
            bindings[actuator_id] = ActuatorBinding(actuator_id, pump.entity_id)
        for source_id, source in plant.sources.items():
            if source.demand_entity_id is not None:
                binding_id = f"source:{source_id}"
                if binding_id in bindings:
                    raise ValueError(
                        f"Actuator ID {binding_id!r} is used by more than one actuator."
                    )
                bindings[binding_id] = ActuatorBinding(binding_id, source.demand_entity_id)
        return cls(
            bindings=bindings,
            shadow_mode=shadow_mode,
            actuator_shadow_modes=actuator_shadow_modes or {},
            readiness_bindings=readiness_bindings,
        )

    def actuator_state(self, actuator_id: str) -> ActuatorObservedState:
        """Return the last trustworthy state, defaulting safely to unknown."""
        if actuator_id not in self.bindings:
            raise KeyError(f"Unknown actuator {actuator_id!r}.")
        return self.observed_states.get(actuator_id, ActuatorObservedState.UNKNOWN)

    def requested_state(self, actuator_id: str) -> ActuatorObservedState | None:
        """Return a dispatched desired state without treating it as feedback."""
        if actuator_id not in self.bindings:
            raise KeyError(f"Unknown actuator {actuator_id!r}.")
        return self.requested_states.get(actuator_id)

    def failure_for(self, actuator_id: str) -> ActuatorExecutionFailure | None:
        """Return the last command failure until trustworthy feedback repairs it."""
        if actuator_id not in self.bindings:
            raise KeyError(f"Unknown actuator {actuator_id!r}.")
        return self.failure_states.get(actuator_id)

    def reconcile(self, operation: ActuatorOperation) -> ReconciliationResult:
        """Compare desired, observed, and retained state before dispatch."""
        observed = self.actuator_state(operation.actuator_id)
        retained = self.requested_state(operation.actuator_id)
        failure = self.failure_for(operation.actuator_id)
        if observed is operation.target_state:
            status = ReconciliationStatus.OBSERVED
            explanation = "Observed feedback already satisfies the desired state."
        elif failure is not None and retained is None:
            status = ReconciliationStatus.FAILED
            explanation = failure.explanation
        elif retained is operation.target_state:
            status = ReconciliationStatus.RETAINED
            explanation = "The desired state is already retained as an in-flight request."
        else:
            status = ReconciliationStatus.REQUIRED
            explanation = "Observed and retained state do not satisfy the desired state."
        result = ReconciliationResult(
            actuator_id=operation.actuator_id,
            desired=operation.target_state,
            observed=observed,
            retained=retained,
            status=status,
            explanation=explanation,
        )
        self.reconciliations[operation.actuator_id] = result
        return result

    def readiness_state(self, actuator_id: str) -> bool | None:
        """Return explicit readiness feedback, or None when only a timer is available."""
        if actuator_id not in self.bindings:
            raise KeyError(f"Unknown actuator {actuator_id!r}.")
        feedback = self.feedback_states.get(actuator_id, ActuatorObservedState.UNKNOWN)
        if feedback in {ActuatorObservedState.OPEN, ActuatorObservedState.ON}:
            return True
        if feedback in {ActuatorObservedState.CLOSED, ActuatorObservedState.OFF}:
            return False
        domain = _entity_domain(self.bindings[actuator_id].entity_id)
        if domain == "valve":
            observed = self.actuator_state(actuator_id)
            if observed is ActuatorObservedState.OPEN:
                return True
            if observed is ActuatorObservedState.CLOSED:
                return False
        return None

    def observe_entity_state(self, entity_id: str, state: str | None) -> None:
        """Record a state event for every binding using the entity."""
        is_actuator = any(binding.entity_id == entity_id for binding in self.bindings.values())
        is_feedback = entity_id in self.readiness_bindings.values()
        if not is_actuator and not is_feedback:
            return
        observed = observed_state_for(entity_id, state)
        for actuator_id, binding in self.bindings.items():
            if binding.entity_id == entity_id:
                self.observed_states[actuator_id] = observed
                if self.requested_states.get(actuator_id) is observed:
                    self.requested_states.pop(actuator_id, None)
                    self.failure_states.pop(actuator_id, None)
                failure = self.failure_states.get(actuator_id)
                if failure is not None and failure.operation.target_state is observed:
                    self.failure_states.pop(actuator_id, None)
        for actuator_id, feedback_entity_id in self.readiness_bindings.items():
            if feedback_entity_id == entity_id:
                self.feedback_states[actuator_id] = observed

    def observe_entities(self, states: Mapping[str, str | None]) -> None:
        """Reconcile configured entity states without assuming desired state."""
        for binding in self.bindings.values():
            observed = observed_state_for(binding.entity_id, states.get(binding.entity_id))
            self.observed_states[binding.actuator_id] = observed
            if self.requested_states.get(binding.actuator_id) is observed:
                self.requested_states.pop(binding.actuator_id, None)
                self.failure_states.pop(binding.actuator_id, None)
            failure = self.failure_states.get(binding.actuator_id)
            if failure is not None and failure.operation.target_state is observed:
                self.failure_states.pop(binding.actuator_id, None)
        for actuator_id, entity_id in self.readiness_bindings.items():
            self.feedback_states[actuator_id] = observed_state_for(entity_id, states.get(entity_id))

    async def async_execute(
        self,
        plan: ControlPlan,
        dispatch: DispatchOperation,
        *,
        force_shadow: bool = False,
        force_shadow_actuator_ids: frozenset[str] = frozenset(),
        force_dispatch: bool = False,
        unavailable_actuator_ids: frozenset[str] = frozenset(),
    ) -> ExecutionReport:
        """Dispatch only unsatisfied, non-shadowed explicit operations."""
        executed: list[ActuatorOperation] = []
        suppressed: list[ActuatorOperation] = []
        shadowed: list[ActuatorOperation] = []
        failures: list[ActuatorExecutionFailure] = []
        for command in plan.commands:
            try:
                binding = self.bindings[command.actuator_id]
            except KeyError as error:
                raise ValueError(
                    f"Control plan references unknown actuator {command.actuator_id!r}."
                ) from error
            if command.actuator_id in unavailable_actuator_ids:
                continue
            operation = operation_for(command, binding)
            reconciliation = self.reconcile(operation)
            if not force_dispatch and reconciliation.status in {
                ReconciliationStatus.OBSERVED,
                ReconciliationStatus.RETAINED,
                ReconciliationStatus.FAILED,
            }:
                suppressed.append(operation)
                continue
            if (
                force_shadow
                or command.actuator_id in force_shadow_actuator_ids
                or self.shadow_mode
                or self.actuator_shadow_modes.get(command.actuator_id, False)
            ):
                shadowed.append(operation)
                continue
            try:
                await dispatch(operation)
            except TimeoutError as error:
                failure = ActuatorExecutionFailure(
                    operation,
                    ActuatorFailureKind.TIMEOUT,
                    f"Command {command.action.value} for actuator {command.actuator_id} "
                    f"timed out: {error or 'no response'}",
                )
                self.requested_states.pop(command.actuator_id, None)
                self.failure_states[command.actuator_id] = failure
                failures.append(failure)
                continue
            except Exception as error:
                failure = ActuatorExecutionFailure(
                    operation,
                    ActuatorFailureKind.REJECTED,
                    f"Command {command.action.value} for actuator {command.actuator_id} "
                    f"was rejected: {error or type(error).__name__}",
                )
                self.requested_states.pop(command.actuator_id, None)
                self.failure_states[command.actuator_id] = failure
                failures.append(failure)
                continue
            self.requested_states[command.actuator_id] = operation.target_state
            if (
                command.actuator_id in self.failure_states
                and self.failure_states[command.actuator_id].operation.target_state
                is not operation.target_state
            ):
                self.failure_states.pop(command.actuator_id, None)
            executed.append(operation)
        return ExecutionReport(tuple(executed), tuple(suppressed), tuple(shadowed), tuple(failures))

    async def async_safe_shutdown(
        self,
        plant: CompiledPlant,
        runtime: RuntimeState,
        now: datetime,
        dispatch: DispatchOperation,
        *,
        force_shadow: bool = False,
        unavailable_actuator_ids: frozenset[str] = frozenset(),
    ) -> SafeShutdownReport:
        """Execute one explicit source-release, overrun, pump, or valve phase."""
        plan, next_runtime = build_safe_shutdown(plant, runtime, now)
        control_plan = ControlPlan(
            commands=plan.commands,
            valve_consumers={},
            pump_consumers={},
        )
        execution = await self.async_execute(
            control_plan,
            dispatch,
            force_shadow=force_shadow,
            force_dispatch=True,
            unavailable_actuator_ids=unavailable_actuator_ids,
        )
        return SafeShutdownReport(plan, next_runtime, execution)
