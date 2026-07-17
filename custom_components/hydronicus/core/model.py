"""Domain types that contain no Home Assistant dependencies."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class ValveState(StrEnum):
    """Virtual or observed lifecycle state of a valve."""

    CLOSED = "closed"
    OPENING = "opening"
    OPEN = "open"


class PumpState(StrEnum):
    """Virtual or observed lifecycle state of a pump."""

    OFF = "off"
    WAITING_FOR_VALVES = "waiting_for_valves"
    RUNNING = "running"
    OVERRUN = "overrun"


class TemperatureAggregation(StrEnum):
    """Policy used to combine a zone's configured temperature readings."""

    MEAN = "mean"
    MEDIAN = "median"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    WEIGHTED_MEAN = "weighted_mean"


@dataclass(frozen=True, slots=True)
class Zone:
    """A comfort target whose required sensors contribute to one demand value."""

    id: str
    name: str
    target_temperature: float
    temperature_sensors: tuple[str, ...]
    aggregation: TemperatureAggregation = TemperatureAggregation.MEAN
    temperature_sensor_weights: Mapping[str, float] = field(default_factory=dict)
    heating_start_delta: float = 0.3
    heating_stop_delta: float = 0.1


@dataclass(frozen=True, slots=True)
class Valve:
    """A topology-owned valve with one Home Assistant entity binding."""

    id: str
    name: str
    entity_id: str
    opening_time_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class Pump:
    """A topology-owned pump with one Home Assistant entity binding."""

    id: str
    name: str
    entity_id: str
    overrun_seconds: float = 120.0


@dataclass(frozen=True, slots=True)
class Circuit:
    """A water path whose required valves must be ready before its pump may run."""

    id: str
    name: str
    valve_ids: tuple[str, ...]
    pump_id: str


@dataclass(frozen=True, slots=True)
class DeliveryRoute:
    """An eligible connection from a zone to a circuit."""

    id: str
    zone_id: str
    circuit_id: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class PlantConfiguration:
    """User topology before validation and compilation."""

    id: str
    zones: tuple[Zone, ...]
    valves: tuple[Valve, ...]
    pumps: tuple[Pump, ...]
    circuits: tuple[Circuit, ...]
    routes: tuple[DeliveryRoute, ...]


@dataclass(frozen=True, slots=True)
class CompiledPlant:
    """Validated topology optimized for deterministic evaluation."""

    id: str
    zones: Mapping[str, Zone]
    valves: Mapping[str, Valve]
    pumps: Mapping[str, Pump]
    circuits: Mapping[str, Circuit]
    routes: tuple[DeliveryRoute, ...]
    logic_summary: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TemperatureObservation:
    """A temperature reading supplied by the runtime adapter."""

    value: float | None
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class PlantSnapshot:
    """All Home Assistant observations required by the pure controller."""

    temperatures: Mapping[str, TemperatureObservation]


@dataclass(frozen=True, slots=True)
class ValveRuntime:
    """Controller-owned lifecycle data for one shared valve."""

    state: ValveState = ValveState.CLOSED
    changed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PumpRuntime:
    """Controller-owned lifecycle data for one shared pump."""

    state: PumpState = PumpState.OFF
    changed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """Persistable controller state, separate from observed Home Assistant state."""

    zone_demands: Mapping[str, bool] = field(default_factory=dict)
    valves: Mapping[str, ValveRuntime] = field(default_factory=dict)
    pumps: Mapping[str, PumpRuntime] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActuatorCommand:
    """An idempotent desired actuator command, never a toggle."""

    actuator_id: str
    action: str
    reason: str


@dataclass(frozen=True, slots=True)
class ControlPlan:
    """Desired shadow or active actions produced during one evaluation."""

    commands: tuple[ActuatorCommand, ...]
    valve_consumers: Mapping[str, frozenset[str]]
    pump_consumers: Mapping[str, frozenset[str]]


@dataclass(frozen=True, slots=True)
class ControllerDiagnostics:
    """Human-readable reasons for every significant controller decision."""

    zone_reasons: Mapping[str, str]
    circuit_reasons: Mapping[str, str]
    actuator_reasons: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Atomic result of a deterministic controller evaluation."""

    next_runtime: RuntimeState
    control_plan: ControlPlan
    diagnostics: ControllerDiagnostics
