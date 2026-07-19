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
    STARTING = "starting"
    RUNNING = "running"
    OVERRUN = "overrun"


class SafeShutdownPhase(StrEnum):
    """Ordered phases of an explicit safe-shutdown request."""

    IDLE = "idle"
    SOURCE_RELEASED = "source_released"
    PUMP_OVERRUN = "pump_overrun"
    PUMPS_STOPPED = "pumps_stopped"
    VALVES_CLOSED = "valves_closed"


class ActuatorFeedbackStatus(StrEnum):
    """Conservative status of configured actuator feedback."""

    NOT_CONFIGURED = "not_configured"
    HEALTHY = "healthy"
    MISMATCH = "mismatch"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class PlantMode(StrEnum):
    """Operating mode shared by heating, cooling, and idle evaluations."""

    AUTO = "auto"
    IDLE = "idle"
    HEATING = "heating"
    COOLING = "cooling"


class ModeChangeoverPhase(StrEnum):
    """Ordered phases used while moving the shared plant between modes."""

    IDLE = "idle"
    SOURCE_RELEASE = "source_release"
    PUMP_OVERRUN = "pump_overrun"
    PUMPS_STOPPING = "pumps_stopping"
    VALVES_CLOSING = "valves_closing"


class ActuatorAction(StrEnum):
    """Explicit actuator operations accepted by the executor boundary."""

    OPEN = "open"
    CLOSE = "close"
    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"
    SELECT = "select"


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
    MODE_BLOCKED = "mode_blocked"


class SourceKind(StrEnum):
    """Supported source qualification strategies."""

    EXTERNAL = "external"
    TEMPERATURE_QUALIFIED_BUFFER = "temperature_qualified_buffer"


class SourceSelectionPhase(StrEnum):
    """Ordered phases of a deterministic source changeover."""

    IDLE = "idle"
    WAITING_FOR_HYDRAULICS = "waiting_for_hydraulics"
    MINIMUM_DWELL = "minimum_dwell"
    RELEASING = "releasing"
    BREAKING = "breaking"
    SELECTING = "selecting"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class TemperatureSensorMetadata:
    """Immutable configuration for one temperature observation."""

    entity_id: str
    required: bool = True
    weight: float = 1.0
    calibration_offset: float = 0.0
    max_age_seconds: float = 1800.0
    designated_reference: bool = False


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
        """Return entity IDs for adapter callers that do not need metadata."""
        return tuple(sensor.entity_id for sensor in self.temperature_sensor_metadata)

    @property
    def sensor_metadata(self) -> tuple[TemperatureSensorMetadata, ...]:
        """Return the canonical per-sensor configuration records."""
        return self.temperature_sensor_metadata

    @property
    def humidity_sensors(self) -> tuple[str, ...]:
        """Return configured humidity entity IDs."""
        return tuple(sensor.entity_id for sensor in self.humidity_sensor_metadata)


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


@dataclass(frozen=True, slots=True)
class AggregationResult:
    """Structured aggregate and sensor-health result for one zone."""

    value: float | None
    usable_sensor_ids: tuple[str, ...] = ()
    excluded_optional_sensor_ids: tuple[str, ...] = ()
    blocking_required_sensor_ids: tuple[str, ...] = ()
    explanation: str = ""

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


@dataclass(frozen=True, slots=True)
class SourceDiagnostic:
    """One atomic source qualification and guarded-demand result."""

    source_id: str
    available: bool | None
    eligible: bool
    recommended: bool
    active: bool
    demand_requested: bool
    demand_permitted: bool
    blocked: bool
    reason: str


@dataclass(frozen=True, slots=True, init=False)
class Source:
    """A configured heat source used by qualification and guarded demand."""

    id: str
    name: str
    priority: int
    kind: SourceKind
    availability_entity_id: str | None
    temperature_entity_id: str | None
    minimum_temperature: float | None
    maximum_age_seconds: float
    hysteresis: float
    demand_entity_id: str | None

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
        demand_entity_id: str | None = None,
        source_demand_entity_id: str | None = None,
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
        if source_demand_entity_id is not None:
            demand_entity_id = source_demand_entity_id
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
        object.__setattr__(self, "demand_entity_id", demand_entity_id)


@dataclass(frozen=True, slots=True, init=False)
class SourceSelectionActuator:
    """Generic source selector configuration with a synthetic-safe default."""

    id: str
    name: str
    entity_id: str | None
    break_interval_seconds: float
    minimum_dwell_seconds: float
    release_option: str
    shadow_only: bool

    def __init__(
        self,
        id: str,
        name: str,
        entity_id: str | None = None,
        break_interval_seconds: float = 30.0,
        minimum_dwell_seconds: float = 300.0,
        *,
        selector_entity_id: str | None = None,
        break_seconds: float | None = None,
        minimum_source_dwell_seconds: float | None = None,
        release_option: str = "none",
        shadow_only: bool = True,
    ) -> None:
        """Accept descriptive aliases while keeping selector execution synthetic by default."""
        if (
            entity_id is not None
            and selector_entity_id is not None
            and entity_id != selector_entity_id
        ):
            raise ValueError("Source selector entity was provided more than once.")
        if selector_entity_id is not None:
            entity_id = selector_entity_id
        if break_seconds is not None:
            break_interval_seconds = break_seconds
        if minimum_source_dwell_seconds is not None:
            minimum_dwell_seconds = minimum_source_dwell_seconds
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "entity_id", entity_id)
        object.__setattr__(self, "break_interval_seconds", float(break_interval_seconds))
        object.__setattr__(self, "minimum_dwell_seconds", float(minimum_dwell_seconds))
        object.__setattr__(self, "release_option", release_option)
        object.__setattr__(self, "shadow_only", bool(shadow_only))


@dataclass(frozen=True, slots=True)
class SourceSelectionRuntime:
    """Persistable state for one break-before-make source transition."""

    phase: SourceSelectionPhase = SourceSelectionPhase.IDLE
    active_source_id: str | None = None
    target_source_id: str | None = None
    transition_started_at: datetime | None = None
    last_selected_at: datetime | None = None
    released_source_id: str | None = None


@dataclass(frozen=True, slots=True)
class SourceSelectionDiagnostic:
    """Structured explanation for source selection and its safety gate."""

    phase: SourceSelectionPhase
    active_source_id: str | None
    target_source_id: str | None
    recommended_source_id: str | None
    hydraulically_safe: bool
    explanation: str
    dwell_remaining_seconds: float = 0.0


@dataclass(frozen=True, slots=True, init=False)
class Valve:
    """A topology-owned valve with one Home Assistant entity binding."""

    id: str
    name: str
    entity_id: str
    opening_time_seconds: float = 30.0
    readiness_entity_id: str | None = None
    position_entity_id: str | None = None
    position_max_age_seconds: float = 1800.0

    def __init__(
        self,
        id: str,
        name: str,
        entity_id: str,
        opening_time_seconds: float = 30.0,
        readiness_entity_id: str | None = None,
        *,
        feedback_entity_id: str | None = None,
        position_entity_id: str | None = None,
        position_feedback_entity_id: str | None = None,
        position_max_age_seconds: float = 1800.0,
        feedback_max_age_seconds: float | None = None,
    ) -> None:
        """Accept readiness and position feedback terminology at the model boundary."""
        if (
            readiness_entity_id is not None
            and feedback_entity_id is not None
            and readiness_entity_id != feedback_entity_id
        ):
            raise ValueError("Valve readiness feedback was provided more than once.")
        if position_feedback_entity_id is not None:
            position_entity_id = position_feedback_entity_id
        if feedback_max_age_seconds is not None:
            position_max_age_seconds = feedback_max_age_seconds
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "entity_id", entity_id)
        object.__setattr__(self, "opening_time_seconds", opening_time_seconds)
        object.__setattr__(
            self,
            "readiness_entity_id",
            readiness_entity_id if readiness_entity_id is not None else feedback_entity_id,
        )
        object.__setattr__(self, "position_entity_id", position_entity_id)
        object.__setattr__(self, "position_max_age_seconds", float(position_max_age_seconds))


@dataclass(frozen=True, slots=True, init=False)
class Pump:
    """A topology-owned pump with one Home Assistant entity binding."""

    id: str
    name: str
    entity_id: str
    overrun_seconds: float = 120.0
    power_entity_id: str | None = None
    flow_entity_id: str | None = None
    fault_entity_id: str | None = None
    power_max_age_seconds: float = 1800.0
    flow_max_age_seconds: float = 1800.0
    fault_max_age_seconds: float = 1800.0

    def __init__(
        self,
        id: str,
        name: str,
        entity_id: str,
        overrun_seconds: float = 120.0,
        *,
        power_entity_id: str | None = None,
        power_feedback_entity_id: str | None = None,
        flow_entity_id: str | None = None,
        flow_feedback_entity_id: str | None = None,
        fault_entity_id: str | None = None,
        fault_feedback_entity_id: str | None = None,
        power_max_age_seconds: float = 1800.0,
        flow_max_age_seconds: float = 1800.0,
        fault_max_age_seconds: float = 1800.0,
        feedback_max_age_seconds: float | None = None,
    ) -> None:
        """Store independently optional pump feedback without changing legacy arguments."""
        if power_feedback_entity_id is not None:
            power_entity_id = power_feedback_entity_id
        if flow_feedback_entity_id is not None:
            flow_entity_id = flow_feedback_entity_id
        if fault_feedback_entity_id is not None:
            fault_entity_id = fault_feedback_entity_id
        if feedback_max_age_seconds is not None:
            power_max_age_seconds = feedback_max_age_seconds
            flow_max_age_seconds = feedback_max_age_seconds
            fault_max_age_seconds = feedback_max_age_seconds
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "entity_id", entity_id)
        object.__setattr__(self, "overrun_seconds", overrun_seconds)
        object.__setattr__(self, "power_entity_id", power_entity_id)
        object.__setattr__(self, "flow_entity_id", flow_entity_id)
        object.__setattr__(self, "fault_entity_id", fault_entity_id)
        object.__setattr__(self, "power_max_age_seconds", float(power_max_age_seconds))
        object.__setattr__(self, "flow_max_age_seconds", float(flow_max_age_seconds))
        object.__setattr__(self, "fault_max_age_seconds", float(fault_max_age_seconds))


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
    source_selector: SourceSelectionActuator | None = None


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
    source_selector: SourceSelectionActuator | None = None


@dataclass(frozen=True, slots=True)
class TemperatureObservation:
    """A temperature reading supplied by the runtime adapter."""

    value: float | None
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class FeedbackObservation:
    """One typed actuator observation with an explicit freshness timestamp."""

    value: float | bool | str | None
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ActuatorFeedback:
    """Optional, independently configured observations for one actuator."""

    position: FeedbackObservation | None = None
    power: FeedbackObservation | None = None
    flow: FeedbackObservation | None = None
    fault: FeedbackObservation | None = None


@dataclass(frozen=True, slots=True)
class ActuatorDiagnostic:
    """Structured observed-state, mismatch, and dependent-block explanation."""

    actuator_id: str
    status: ActuatorFeedbackStatus
    mismatch: bool = False
    blocked: bool = False
    expected: str | None = None
    observed: str | float | bool | None = None
    feedback_kind: str | None = None
    stale_feedback: tuple[str, ...] = ()
    reason: str = ""

    @property
    def is_mismatch(self) -> bool:
        """Return whether observed feedback disagrees with requested state."""
        return self.mismatch

    @property
    def dependent_blocked(self) -> bool:
        """Return whether dependent hydraulic paths must fail closed."""
        return self.blocked


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
    source_selector_states: Mapping[str, str | None] = field(default_factory=dict)
    source_demand_states: Mapping[str, bool] = field(default_factory=dict)
    actuator_feedback: Mapping[str, ActuatorFeedback] = field(default_factory=dict)
    unavailable_entity_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ValveRuntime:
    """Controller-owned lifecycle data for one shared valve."""

    state: ValveState = ValveState.CLOSED
    changed_at: datetime | None = None
    ready: bool | None = None

    def __post_init__(self) -> None:
        """Preserve the legacy open-state constructor as a ready state."""
        if self.ready is None:
            object.__setattr__(self, "ready", self.state is ValveState.OPEN)

    @property
    def is_ready(self) -> bool:
        """Return whether this virtual valve satisfies circuit readiness."""
        return self.state is ValveState.OPEN and bool(self.ready)


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
    requested_mode: PlantMode = PlantMode.AUTO
    selected_source_id: str | None = None
    source_selection: SourceSelectionRuntime = field(default_factory=SourceSelectionRuntime)
    changeover_phase: ModeChangeoverPhase = ModeChangeoverPhase.IDLE
    changeover_target_mode: PlantMode | None = None
    changeover_started_at: datetime | None = None
    changeover_deadline: datetime | None = None
    changeover_reason: str = ""
    safe_shutdown_phase: SafeShutdownPhase = SafeShutdownPhase.IDLE
    safe_shutdown_started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ActuatorCommand:
    """An idempotent desired actuator command, never a toggle."""

    actuator_id: str
    action: ActuatorAction
    reason: str
    target: str | None = None

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
    source_selection: SourceSelectionDiagnostic | None = None
    source_selection_actuator_ids: frozenset[str] = frozenset()
    requested_mode: PlantMode = PlantMode.AUTO
    changeover_phase: ModeChangeoverPhase = ModeChangeoverPhase.IDLE
    changeover_target_mode: PlantMode | None = None
    changeover_deadline: datetime | None = None
    mode_explanation: str = ""


@dataclass(frozen=True, slots=True)
class SafeShutdownPlan:
    """One idempotent step in the source-release and hydraulic shutdown order."""

    phase: SafeShutdownPhase
    commands: tuple[ActuatorCommand, ...] = ()
    next_deadline: datetime | None = None
    explanation: str = ""


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
    source_diagnostics: Mapping[str, SourceDiagnostic] = field(default_factory=dict)
    cooling_circuit_reasons: Mapping[str, str] = field(default_factory=dict)
    cooling_zone_reasons: Mapping[str, str] = field(default_factory=dict)
    mode_conflicts: tuple[ModeConflict, ...] = ()
    actuator_diagnostics: Mapping[str, ActuatorDiagnostic] = field(default_factory=dict)
    source_selection: SourceSelectionDiagnostic | None = None
    requested_mode: PlantMode = PlantMode.AUTO
    active_mode: PlantMode = PlantMode.IDLE
    changeover_phase: ModeChangeoverPhase = ModeChangeoverPhase.IDLE
    changeover_target_mode: PlantMode | None = None
    changeover_deadline: datetime | None = None
    mode_explanation: str = ""

    @property
    def actuator_feedback(self) -> Mapping[str, ActuatorDiagnostic]:
        """Return structured actuator feedback diagnostics."""
        return self.actuator_diagnostics

    @property
    def mismatches(self) -> Mapping[str, ActuatorDiagnostic]:
        """Return diagnostics for callers interested in manual intervention."""
        return {
            actuator_id: diagnostic
            for actuator_id, diagnostic in self.actuator_diagnostics.items()
            if diagnostic.mismatch
        }


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Atomic result of a deterministic controller evaluation."""

    next_runtime: RuntimeState
    control_plan: ControlPlan
    diagnostics: ControllerDiagnostics
