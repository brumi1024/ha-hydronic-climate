"""Deterministic actuator command execution without Home Assistant imports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from .model import ActuatorAction, ActuatorCommand, CompiledPlant, ControlPlan


class ActuatorObservedState(StrEnum):
    """Last trustworthy state observed or confirmed for one actuator."""

    UNKNOWN = "unknown"
    OPEN = "open"
    CLOSED = "closed"
    ON = "on"
    OFF = "off"


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
class ExecutionReport:
    """The result of executing one immutable control plan."""

    executed: tuple[ActuatorOperation, ...] = ()
    suppressed: tuple[ActuatorOperation, ...] = ()
    shadowed: tuple[ActuatorOperation, ...] = ()


type DispatchOperation = Callable[[ActuatorOperation], Awaitable[None]]


def _entity_domain(entity_id: str) -> str:
    """Return the Home Assistant domain from an entity ID."""
    domain, separator, _object_id = entity_id.partition(".")
    if not separator or domain not in {"switch", "valve"}:
        raise ValueError(
            f"Actuator entity {entity_id!r} must belong to the switch or valve domain."
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
    domain = _entity_domain(entity_id)
    if domain == "switch":
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
    observed_states: dict[str, ActuatorObservedState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Copy configuration and initialize every actuator conservatively."""
        self.bindings = dict(self.bindings)
        self.actuator_shadow_modes = {
            str(actuator_id): bool(shadow)
            for actuator_id, shadow in self.actuator_shadow_modes.items()
        }
        self.observed_states = {
            actuator_id: self.observed_states.get(actuator_id, ActuatorObservedState.UNKNOWN)
            for actuator_id in self.bindings
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
        for actuator_id, pump in plant.pumps.items():
            if actuator_id in bindings:
                raise ValueError(f"Actuator ID {actuator_id!r} is used by more than one actuator.")
            bindings[actuator_id] = ActuatorBinding(actuator_id, pump.entity_id)
        return cls(
            bindings=bindings,
            shadow_mode=shadow_mode,
            actuator_shadow_modes=actuator_shadow_modes or {},
        )

    def actuator_state(self, actuator_id: str) -> ActuatorObservedState:
        """Return the last trustworthy state, defaulting safely to unknown."""
        if actuator_id not in self.bindings:
            raise KeyError(f"Unknown actuator {actuator_id!r}.")
        return self.observed_states.get(actuator_id, ActuatorObservedState.UNKNOWN)

    def observe_entity_state(self, entity_id: str, state: str | None) -> None:
        """Record a state event for every binding using the entity."""
        for actuator_id, binding in self.bindings.items():
            if binding.entity_id == entity_id:
                self.observed_states[actuator_id] = observed_state_for(entity_id, state)

    def observe_entities(self, states: Mapping[str, str | None]) -> None:
        """Reconcile configured entity states without assuming desired state."""
        for binding in self.bindings.values():
            self.observed_states[binding.actuator_id] = observed_state_for(
                binding.entity_id, states.get(binding.entity_id)
            )

    async def async_execute(
        self,
        plan: ControlPlan,
        dispatch: DispatchOperation,
        *,
        force_shadow: bool = False,
    ) -> ExecutionReport:
        """Dispatch only unsatisfied, non-shadowed explicit operations."""
        executed: list[ActuatorOperation] = []
        suppressed: list[ActuatorOperation] = []
        shadowed: list[ActuatorOperation] = []
        for command in plan.commands:
            try:
                binding = self.bindings[command.actuator_id]
            except KeyError as error:
                raise ValueError(
                    f"Control plan references unknown actuator {command.actuator_id!r}."
                ) from error
            operation = operation_for(command, binding)
            if self.actuator_state(command.actuator_id) is operation.target_state:
                suppressed.append(operation)
                continue
            if (
                force_shadow
                or self.shadow_mode
                or self.actuator_shadow_modes.get(command.actuator_id, False)
            ):
                shadowed.append(operation)
                continue
            await dispatch(operation)
            self.observed_states[command.actuator_id] = operation.target_state
            executed.append(operation)
        return ExecutionReport(tuple(executed), tuple(suppressed), tuple(shadowed))
