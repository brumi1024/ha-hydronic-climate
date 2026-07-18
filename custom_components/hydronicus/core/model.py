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


class PlantMode(StrEnum):
    """Operating mode shared by heating, cooling, and idle evaluations."""

    IDLE = "idle"
    HEATING = "heating"
    COOLING = "cooling"


class ActuatorAction(StrEnum):
    """Explicit actuator operations accepted by the executor boundary."""

    OPEN = "open"
    CLOSE = "close"
    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"


class InterlockStatus(StrEnum):
    """Result of one safety interlock evaluation."""

    PERMITTED = "permitted"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class EquipmentKind(StrEnum):
    """Kind of shared equipment that can couple heating and cooling."""

    VALVE = "valve"
    PUMP = "pump"
    SOURCE = "source"


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


class SourceKind(StrEnum):
    """Supported source qualification strategies."""

    EXTERNAL = "external"
    TEMPERATURE_QUALIFIED_BUFFER = "temperature_qualified_buffer"
    BUFFER = "temperature_qualified_buffer"


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
HumiditySensorMetadata = TemperatureSensorMetadata
ObservationMetadata = TemperatureSensorMetadata


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
    humidity_sensor_metadata: tuple[TemperatureSensorMetadata, ...] = ()
    cooling_start_delta: float = 0.3
    cooling_stop_delta: float = 0.1

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
        humidity_sensors: Iterable[str | TemperatureSensorMetadata] = (),
        humidity_sensor_weights: Mapping[str, float] | None = None,
        cooling_start_delta: float = 0.3,
        cooling_stop_delta: float = 0.1,
        *,
        sensor_metadata: Iterable[str | TemperatureSensorMetadata] | None = None,
        temperature_sensor_metadata: Iterable[str | TemperatureSensorMetadata] | None = None,
        humidity_sensor_metadata: Iterable[str | TemperatureSensorMetadata] | None = None,
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
        metadata = _sensor_metadata_records(raw_items, legacy_weights)
        raw_humidity = tuple(
            humidity_sensors if humidity_sensor_metadata is None else humidity_sensor_metadata
        )
        humidity_metadata = _sensor_metadata_records(raw_humidity, humidity_sensor_weights or {})

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
        object.__setattr__(self, "humidity_sensor_metadata", humidity_metadata)
        object.__setattr__(self, "cooling_start_delta", cooling_start_delta)
        object.__setattr__(self, "cooling_stop_delta", cooling_stop_delta)

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

    @property
    def humidity_sensors(self) -> tuple[str, ...]:
        """Return configured humidity entity IDs."""
        return tuple(sensor.entity_id for sensor in self.humidity_sensor_metadata)

    @property
    def humidity_sensor_weights(self) -> Mapping[str, float]:
        """Return a read-only view of configured humidity weights."""
        return {sensor.entity_id: sensor.weight for sensor in self.humidity_sensor_metadata}


def _sensor_metadata_records(
    raw_items: Iterable[str | TemperatureSensorMetadata],
    weights: Mapping[str, float],
) -> tuple[TemperatureSensorMetadata, ...]:
    """Normalize legacy entity IDs and immutable observation metadata."""
    metadata: list[TemperatureSensorMetadata] = []
    for item in raw_items:
        if isinstance(item, TemperatureSensorMetadata):
            sensor = item
        else:
            sensor = TemperatureSensorMetadata(
                entity_id=str(item),
                weight=float(weights.get(str(item), 1.0)),
            )
        if sensor.entity_id in weights and sensor.weight == 1.0:
            sensor = TemperatureSensorMetadata(
                entity_id=sensor.entity_id,
                required=sensor.required,
                weight=float(weights[sensor.entity_id]),
                calibration_offset=sensor.calibration_offset,
                max_age_seconds=sensor.max_age_seconds,
                designated_reference=sensor.designated_reference,
            )
        metadata.append(sensor)
    return tuple(metadata)


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
    humidity_aggregation: AggregationResult | None = None
    dew_point: float | None = None
    condensation_margin: float | None = None
    interlocks: tuple[SafetyInterlockResult, ...] = ()

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
    equipment_kind: str = EquipmentKind.VALVE
    equipment_id: str | None = None

    @property
    def affected_valve_id(self) -> str:
        """Return the valve involved in this warning."""
        return self.valve_id

    @property
    def affected_equipment_id(self) -> str:
        """Return the stable id of the equipment involved in this warning."""
        return self.equipment_id or self.valve_id


@dataclass(frozen=True, slots=True)
class ModeConflict:
    """Deterministic explanation for a heating/cooling shared-equipment conflict."""

    code: str
    equipment_kind: str
    equipment_id: str
    heating_circuit_ids: tuple[str, ...]
    cooling_circuit_ids: tuple[str, ...]
    heating_zone_ids: tuple[str, ...]
    cooling_zone_ids: tuple[str, ...]
    message: str

    @property
    def interlock_id(self) -> str:
        """Return a stable interlock id suitable for adapter publication."""
        return f"cooling:mode-conflict:{self.equipment_kind}:{self.equipment_id}"


@dataclass(frozen=True, slots=True)
class SafetyInterlockResult:
    """Structured result for a safety permit used by future control modes."""

    interlock_id: str
    status: InterlockStatus
    reason: str

    @property
    def permits(self) -> bool:
        """Return whether this result permits the guarded operation."""
        return self.status is InterlockStatus.PERMITTED


@dataclass(frozen=True, slots=True)
class SourceRecommendation:
    """Shadow source choice and explanation without issuing an actuator call."""

    source_id: str | None
    explanation: str
    eligible_source_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, init=False)
class Source:
    """A configured heat source used only by the shadow recommender."""

    id: str
    name: str
    priority: int
    kind: SourceKind
    availability_entity_id: str | None
    temperature_entity_id: str | None
    minimum_temperature: float | None
    maximum_age_seconds: float
    hysteresis: float

    def __init__(
        self,
        id: str,
        name: str,
        priority: int = 0,
        kind: SourceKind | str = SourceKind.EXTERNAL,
        availability_entity_id: str | None = None,
        temperature_entity_id: str | None = None,
        minimum_temperature: float | None = None,
        maximum_age_seconds: float = 1800.0,
        hysteresis: float = 0.5,
        *,
        source_type: SourceKind | str | None = None,
        availability_entity: str | None = None,
        temperature_entity: str | None = None,
        buffer_minimum_temperature: float | None = None,
        buffer_maximum_age_seconds: float | None = None,
        buffer_hysteresis: float | None = None,
    ) -> None:
        """Accept descriptive aliases while storing one stable source contract."""
        if source_type is not None:
            kind = source_type
        if availability_entity is not None:
            availability_entity_id = availability_entity
        if temperature_entity is not None:
            temperature_entity_id = temperature_entity
        if buffer_minimum_temperature is not None:
            minimum_temperature = buffer_minimum_temperature
        if buffer_maximum_age_seconds is not None:
            maximum_age_seconds = buffer_maximum_age_seconds
        if buffer_hysteresis is not None:
            hysteresis = buffer_hysteresis
        normalized_kind_value = str(kind)
        if normalized_kind_value in {"buffer", "temperature_buffer"}:
            normalized_kind_value = SourceKind.TEMPERATURE_QUALIFIED_BUFFER.value
        normalized_kind = SourceKind(normalized_kind_value)
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "priority", priority)
        object.__setattr__(self, "kind", normalized_kind)
        object.__setattr__(self, "availability_entity_id", availability_entity_id)
        object.__setattr__(self, "temperature_entity_id", temperature_entity_id)
        object.__setattr__(self, "minimum_temperature", minimum_temperature)
        object.__setattr__(self, "maximum_age_seconds", float(maximum_age_seconds))
        object.__setattr__(self, "hysteresis", float(hysteresis))

    @property
    def source_type(self) -> SourceKind:
        """Return the qualification kind using its configuration-facing name."""
        return self.kind

    @property
    def temperature_threshold(self) -> float | None:
        """Return the buffer qualification threshold."""
        return self.minimum_temperature


HeatSource = Source
SourceConfig = Source
HeatSourceKind = SourceKind


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
    cooling_enabled: bool = False
    supply_temperature_sensor: str | None = None
    surface_temperature_sensor: str | None = None
    condensation_margin: float = 2.0
    supply_temperature_max_age_seconds: float = 1800.0
    surface_temperature_max_age_seconds: float = 1800.0

    @property
    def supply_temperature_entity_id(self) -> str | None:
        """Return the configured supply reference using an entity-oriented name."""
        return self.supply_temperature_sensor

    @property
    def surface_temperature_entity_id(self) -> str | None:
        """Return the configured surface reference using an entity-oriented name."""
        return self.surface_temperature_sensor

    @property
    def cooling_condensation_margin(self) -> float:
        """Return the configured cooling safety margin in degrees Celsius."""
        return self.condensation_margin


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
    sources: tuple[Source, ...] = ()


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
    sources: Mapping[str, Source] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TemperatureObservation:
    """A temperature reading supplied by the runtime adapter."""

    value: float | None
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class PlantSnapshot:
    """All observations required by the pure controller.

    The optional mappings are extension points for cooling safety and source
    recommendation.  Heating-only callers can continue to provide only
    ``temperatures``.
    """

    temperatures: Mapping[str, TemperatureObservation]
    humidities: Mapping[str, TemperatureObservation] = field(default_factory=dict)
    supply_temperatures: Mapping[str, TemperatureObservation] = field(default_factory=dict)
    surface_temperatures: Mapping[str, TemperatureObservation] = field(default_factory=dict)
    source_temperatures: Mapping[str, TemperatureObservation] = field(default_factory=dict)
    source_availability: Mapping[str, bool] = field(default_factory=dict)


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
    cooling_zone_demands: Mapping[str, bool] = field(default_factory=dict)
    zone_runtime: Mapping[str, ZoneRuntime] = field(default_factory=dict)
    valves: Mapping[str, ValveRuntime] = field(default_factory=dict)
    pumps: Mapping[str, PumpRuntime] = field(default_factory=dict)
    plant_mode: PlantMode = PlantMode.IDLE
    selected_source_id: str | None = None

    @property
    def zone_states(self) -> Mapping[str, ZoneRuntime]:
        """Return zone timing state using the public state-oriented name."""
        return self.zone_runtime


@dataclass(frozen=True, slots=True)
class ActuatorCommand:
    """An idempotent desired actuator command, never a toggle."""

    actuator_id: str
    action: ActuatorAction
    reason: str

    def __post_init__(self) -> None:
        """Normalize legacy string construction while rejecting unknown actions."""
        object.__setattr__(self, "action", ActuatorAction(self.action))


@dataclass(frozen=True, slots=True)
class ControlPlan:
    """Desired shadow or active actions produced during one evaluation."""

    commands: tuple[ActuatorCommand, ...]
    valve_consumers: Mapping[str, frozenset[str]]
    pump_consumers: Mapping[str, frozenset[str]]
    plant_mode: PlantMode = PlantMode.IDLE
    cooling_zone_demands: Mapping[str, bool] = field(default_factory=dict)
    source_recommendation: SourceRecommendation | None = None
    interlocks: Mapping[str, SafetyInterlockResult] = field(default_factory=dict)
    cooling_valve_consumers: Mapping[str, frozenset[str]] = field(default_factory=dict)
    cooling_pump_consumers: Mapping[str, frozenset[str]] = field(default_factory=dict)
    cooling_actuator_ids: frozenset[str] = frozenset()
    mode_conflicts: tuple[ModeConflict, ...] = ()

    @property
    def conflicts(self) -> tuple[ModeConflict, ...]:
        """Return structured heating/cooling conflicts."""
        return self.mode_conflicts


@dataclass(frozen=True, slots=True)
class ControllerDiagnostics:
    """Human-readable reasons for every significant controller decision."""

    zone_reasons: Mapping[str, str]
    circuit_reasons: Mapping[str, str]
    actuator_reasons: Mapping[str, str]
    zone_decisions: Mapping[str, ZoneDecision] = field(default_factory=dict)
    cooling_zone_decisions: Mapping[str, ZoneDecision] = field(default_factory=dict)
    interlocks: Mapping[str, SafetyInterlockResult] = field(default_factory=dict)
    source_recommendation: SourceRecommendation | None = None
    cooling_circuit_reasons: Mapping[str, str] = field(default_factory=dict)
    cooling_zone_reasons: Mapping[str, str] = field(default_factory=dict)
    mode_conflicts: tuple[ModeConflict, ...] = ()

    @property
    def zone_diagnostics(self) -> Mapping[str, ZoneDecision]:
        """Return structured decisions without requiring prose parsing."""
        return self.zone_decisions

    @property
    def conflicts(self) -> tuple[ModeConflict, ...]:
        """Return structured heating/cooling conflicts."""
        return self.mode_conflicts


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Atomic result of a deterministic controller evaluation."""

    next_runtime: RuntimeState
    control_plan: ControlPlan
    diagnostics: ControllerDiagnostics
