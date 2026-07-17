"""Domain types that contain no Home Assistant dependencies."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

MIN_ZONE_TARGET_TEMPERATURE = 5.0
MAX_ZONE_TARGET_TEMPERATURE = 35.0


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

    DESIGNATED_REFERENCE = "designated_reference"
    MEAN = "mean"
    MEDIAN = "median"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    WEIGHTED_MEAN = "weighted_mean"


class ZoneDecisionStatus(StrEnum):
    """Structured result category for one zone evaluation."""

    REQUESTED = "requested"
    SATISFIED = "satisfied"
    DURATION_HELD = "duration_held"
    DURATION_LOCKED = "duration_locked"
    SENSOR_BLOCKED = "sensor_blocked"


class PresetMode(StrEnum):
    """Supported standard Home Assistant heating preset names."""

    NONE = "none"
    COMFORT = "comfort"
    ECO = "eco"
    AWAY = "away"


@dataclass(frozen=True, slots=True)
class TemperatureSensorMetadata:
    """Immutable configuration for one temperature observation."""

    entity_id: str
    required: bool = True
    weight: float = 1.0
    calibration_offset: float = 0.0
    max_age_seconds: float = 1800.0
    designated_reference: bool = False

    @property
    def maximum_age_seconds(self) -> float:
        """Return the configured freshness limit using the long-form name."""
        return self.max_age_seconds

    @property
    def is_designated_reference(self) -> bool:
        """Return whether this sensor is the designated reference."""
        return self.designated_reference


# These aliases keep the contract discoverable under the names used by callers
# that describe the value as a sensor configuration or sensor record.
TemperatureSensorConfig = TemperatureSensorMetadata
TemperatureSensor = TemperatureSensorMetadata


@dataclass(frozen=True, slots=True, init=False)
class Zone:
    """A comfort target whose required sensors contribute to one demand value."""

    id: str
    name: str
    target_temperature: float
    temperature_sensor_metadata: tuple[TemperatureSensorMetadata, ...]
    aggregation: TemperatureAggregation = TemperatureAggregation.MEAN
    heating_start_delta: float = 0.3
    heating_stop_delta: float = 0.1
    minimum_active_duration_seconds: float = 0.0
    minimum_idle_duration_seconds: float = 0.0
    preset_targets: Mapping[str, float] = field(default_factory=dict)

    def __init__(
        self,
        id: str,
        name: str,
        target_temperature: float,
        temperature_sensors: Iterable[str | TemperatureSensorMetadata] = (),
        aggregation: TemperatureAggregation = TemperatureAggregation.MEAN,
        temperature_sensor_weights: Mapping[str, float] | None = None,
        heating_start_delta: float = 0.3,
        heating_stop_delta: float = 0.1,
        minimum_active_duration_seconds: float = 0.0,
        minimum_idle_duration_seconds: float = 0.0,
        preset_targets: Mapping[str, float] | None = None,
        *,
        sensor_metadata: Iterable[str | TemperatureSensorMetadata] | None = None,
        temperature_sensor_metadata: Iterable[str | TemperatureSensorMetadata] | None = None,
    ) -> None:
        """Accept the legacy ID tuple while storing one canonical metadata tuple."""
        if (
            sensor_metadata is not None
            and temperature_sensor_metadata is not None
            and tuple(sensor_metadata) != tuple(temperature_sensor_metadata)
        ):
            raise ValueError("Zone sensor metadata was provided more than once.")
        raw_metadata = (
            temperature_sensor_metadata
            if temperature_sensor_metadata is not None
            else sensor_metadata
        )
        raw_items = tuple(temperature_sensors if raw_metadata is None else raw_metadata)
        legacy_weights = temperature_sensor_weights or {}
        metadata: list[TemperatureSensorMetadata] = []
        for item in raw_items:
            if isinstance(item, TemperatureSensorMetadata):
                sensor = item
            else:
                sensor = TemperatureSensorMetadata(
                    entity_id=str(item),
                    weight=float(legacy_weights.get(str(item), 1.0)),
                )
            if sensor.entity_id in legacy_weights and sensor.weight == 1.0:
                sensor = TemperatureSensorMetadata(
                    entity_id=sensor.entity_id,
                    required=sensor.required,
                    weight=float(legacy_weights[sensor.entity_id]),
                    calibration_offset=sensor.calibration_offset,
                    max_age_seconds=sensor.max_age_seconds,
                    designated_reference=sensor.designated_reference,
                )
            metadata.append(sensor)

        object.__setattr__(self, "id", id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "target_temperature", target_temperature)
        object.__setattr__(self, "temperature_sensor_metadata", tuple(metadata))
        object.__setattr__(self, "aggregation", aggregation)
        object.__setattr__(self, "heating_start_delta", heating_start_delta)
        object.__setattr__(self, "heating_stop_delta", heating_stop_delta)
        object.__setattr__(self, "minimum_active_duration_seconds", minimum_active_duration_seconds)
        object.__setattr__(self, "minimum_idle_duration_seconds", minimum_idle_duration_seconds)
        object.__setattr__(
            self,
            "preset_targets",
            MappingProxyType(dict(preset_targets or {})),
        )

    @property
    def temperature_sensors(self) -> tuple[str, ...]:
        """Return legacy entity IDs for existing adapter and migration callers."""
        return tuple(sensor.entity_id for sensor in self.temperature_sensor_metadata)

    @property
    def sensor_metadata(self) -> tuple[TemperatureSensorMetadata, ...]:
        """Return the canonical per-sensor configuration records."""
        return self.temperature_sensor_metadata

    @property
    def temperature_sensor_weights(self) -> Mapping[str, float]:
        """Return a read-only compatibility view of configured weights."""
        return {sensor.entity_id: sensor.weight for sensor in self.temperature_sensor_metadata}


@dataclass(frozen=True, slots=True)
class ZoneRuntime:
    """Persistable demand state and its last state-transition timestamp."""

    demand: bool = False
    last_demand_transition_at: datetime | None = None

    @property
    def demand_active(self) -> bool:
        """Return the current demand state using an explicit name."""
        return self.demand


ZoneRuntimeState = ZoneRuntime


@dataclass(frozen=True, slots=True)
class AggregationResult:
    """Structured aggregate and sensor-health result for one zone."""

    value: float | None
    usable_sensor_ids: tuple[str, ...] = ()
    excluded_optional_sensor_ids: tuple[str, ...] = ()
    blocking_required_sensor_ids: tuple[str, ...] = ()
    explanation: str = ""

    @property
    def aggregate_temperature(self) -> float | None:
        """Return the aggregate under its domain-facing name."""
        return self.value

    @property
    def is_blocked(self) -> bool:
        """Return whether sensor health prevents a usable aggregate."""
        return bool(self.blocking_required_sensor_ids) or not self.usable_sensor_ids


@dataclass(frozen=True, slots=True)
class ZoneDecision:
    """Structured safety and timing decision for one comfort zone."""

    status: ZoneDecisionStatus
    demand: bool
    aggregation: AggregationResult | None = None
    explanation: str = ""
    deadline: datetime | None = None

    @property
    def decision(self) -> ZoneDecisionStatus:
        """Return the status using the alternate contract terminology."""
        return self.status


@dataclass(frozen=True, slots=True)
class TopologyWarning:
    """Non-fatal warning produced while compiling a valid topology."""

    code: str
    message: str
    valve_id: str
    circuit_ids: tuple[str, ...] = ()
    zone_ids: tuple[str, ...] = ()

    @property
    def affected_valve_id(self) -> str:
        """Return the valve involved in this warning."""
        return self.valve_id


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
    warnings: tuple[TopologyWarning, ...] = ()


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
    zone_runtime: Mapping[str, ZoneRuntime] = field(default_factory=dict)
    valves: Mapping[str, ValveRuntime] = field(default_factory=dict)
    pumps: Mapping[str, PumpRuntime] = field(default_factory=dict)

    @property
    def zone_states(self) -> Mapping[str, ZoneRuntime]:
        """Return zone timing state using the public state-oriented name."""
        return self.zone_runtime


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
    zone_decisions: Mapping[str, ZoneDecision] = field(default_factory=dict)

    @property
    def zone_diagnostics(self) -> Mapping[str, ZoneDecision]:
        """Return structured decisions without requiring prose parsing."""
        return self.zone_decisions


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Atomic result of a deterministic controller evaluation."""

    next_runtime: RuntimeState
    control_plan: ControlPlan
    diagnostics: ControllerDiagnostics
