"""Deterministic heating and cooling shadow controller for Hydronicus."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import fsum, isfinite, log
from statistics import median

from .entity_bindings import degraded_circuit_ids
from .model import (
    ActuatorAction,
    ActuatorCommand,
    ActuatorDiagnostic,
    ActuatorFeedbackStatus,
    AggregationResult,
    CompiledPlant,
    ControllerDiagnostics,
    ControlPlan,
    DeliveryRoute,
    EquipmentKind,
    Evaluation,
    FeedbackObservation,
    InterlockStatus,
    ModeChangeoverPhase,
    ModeConflict,
    PlantMode,
    PlantSnapshot,
    PumpRuntime,
    PumpState,
    RuntimeState,
    SafeShutdownPhase,
    SafeShutdownPlan,
    SafetyInterlockResult,
    Source,
    SourceDiagnostic,
    SourceKind,
    SourceRecommendation,
    SourceSelectionActuator,
    SourceSelectionDiagnostic,
    SourceSelectionPhase,
    SourceSelectionRuntime,
    TemperatureAggregation,
    TemperatureObservation,
    ValveRuntime,
    ValveState,
    Zone,
    ZoneDecision,
    ZoneDecisionStatus,
    ZoneRuntime,
)

_DEW_POINT_A = 17.62
_DEW_POINT_B = 243.12


def _elapsed(now: datetime, changed_at: datetime | None) -> timedelta:
    """Return a conservative zero duration for state without a timestamp."""
    if changed_at is None:
        return timedelta(0)
    try:
        return now - changed_at
    except TypeError, ValueError:
        # A restored timestamp with incompatible timezone information is not
        # trustworthy enough to satisfy a safety timing requirement.
        return timedelta(0)


def _observation_is_usable(
    observation: TemperatureObservation | None,
    *,
    max_age_seconds: float,
    now: datetime | None,
) -> tuple[bool, str]:
    """Return whether one observation is valid at the controller evaluation time."""
    if observation is None:
        return False, "missing"
    if observation.value is None or not isfinite(observation.value):
        return False, "non-finite"
    if observation.observed_at is None:
        return False, "missing timestamp"
    if now is not None:
        try:
            age = now - observation.observed_at
        except TypeError, ValueError:
            return False, "invalid timestamp"
        if age > timedelta(seconds=max_age_seconds):
            return False, "stale"
    return True, "usable"


def aggregate_zone_temperature_result(
    zone: Zone,
    snapshot: PlantSnapshot,
    *,
    now: datetime | None = None,
) -> AggregationResult:
    """Aggregate a zone's observations and report sensor health structurally.

    ``now`` is optional only for compatibility with the pre-Milestone 3
    adapter helper.  Controller evaluations always provide it, so freshness
    is enforced at the safety decision boundary.
    """
    metadata = tuple(sorted(zone.sensor_metadata, key=lambda sensor: sensor.entity_id))
    usable: list[tuple[str, float, float]] = []
    excluded_optional: list[str] = []
    blocking_required: list[str] = []
    failure_reasons: dict[str, str] = {}

    for sensor in metadata:
        observation = snapshot.temperatures.get(sensor.entity_id)
        valid, reason = _observation_is_usable(
            observation,
            max_age_seconds=sensor.max_age_seconds,
            now=now,
        )
        if not valid:
            failure_reasons[sensor.entity_id] = reason
            if sensor.required:
                blocking_required.append(sensor.entity_id)
            else:
                excluded_optional.append(sensor.entity_id)
            continue
        assert observation is not None
        assert observation.value is not None
        calibrated = observation.value + sensor.calibration_offset
        if not isfinite(calibrated):
            failure_reasons[sensor.entity_id] = "non-finite after calibration"
            if sensor.required:
                blocking_required.append(sensor.entity_id)
            else:
                excluded_optional.append(sensor.entity_id)
            continue
        usable.append((sensor.entity_id, calibrated, sensor.weight))

    usable_ids = tuple(sensor_id for sensor_id, _value, _weight in usable)
    excluded_ids = tuple(sorted(excluded_optional))
    blocking_ids = tuple(sorted(blocking_required))
    failure_text = "; ".join(
        f"{sensor_id} ({failure_reasons[sensor_id]})" for sensor_id in sorted(failure_reasons)
    )

    value: float | None = None
    if not blocking_ids and usable:
        values = [reading for _sensor_id, reading, _weight in usable]
        if zone.aggregation is TemperatureAggregation.DESIGNATED_REFERENCE:
            references = [
                reading
                for sensor_id, reading, _weight in usable
                if next(
                    sensor.designated_reference
                    for sensor in metadata
                    if sensor.entity_id == sensor_id
                )
            ]
            # Topology validation guarantees one configured reference.  A
            # missing optional reference remains blocked rather than silently
            # changing the user's selected aggregation policy.
            if references:
                value = references[0]
        elif zone.aggregation is TemperatureAggregation.MEAN:
            value = fsum(values) / len(values)
        elif zone.aggregation is TemperatureAggregation.MEDIAN:
            value = float(median(values))
        elif zone.aggregation is TemperatureAggregation.MINIMUM:
            value = min(values)
        elif zone.aggregation is TemperatureAggregation.MAXIMUM:
            value = max(values)
        elif zone.aggregation is TemperatureAggregation.WEIGHTED_MEAN:
            weights = [weight for _sensor_id, _reading, weight in usable]
            if all(isfinite(weight) and weight > 0 for weight in weights):
                value = fsum(reading * weight for _sensor_id, reading, weight in usable) / fsum(
                    weights
                )

    if blocking_ids:
        explanation = "Blocked: required temperature sensors are unusable: " + ", ".join(
            failure_text
            for failure_text in failure_text.split("; ")
            if failure_text.split(" ", 1)[0] in blocking_ids
        )
    elif not usable:
        explanation = "Blocked: no usable temperature sensors remain."
    elif value is None:
        explanation = (
            "Blocked: the designated reference sensor is not usable."
            if zone.aggregation is TemperatureAggregation.DESIGNATED_REFERENCE
            else "Blocked: the selected aggregation could not produce a finite value."
        )
    else:
        explanation = f"Aggregated {value:.2f} °C from {', '.join(usable_ids)}."

    if excluded_ids:
        explanation += (
            " Excluded optional sensors: "
            + ", ".join(f"{sensor_id} ({failure_reasons[sensor_id]})" for sensor_id in excluded_ids)
            + "."
        )
    return AggregationResult(
        value=value,
        usable_sensor_ids=usable_ids,
        excluded_optional_sensor_ids=excluded_ids,
        blocking_required_sensor_ids=blocking_ids,
        explanation=explanation,
    )


def aggregate_temperature(
    zone: Zone,
    snapshot: PlantSnapshot,
    *,
    now: datetime | None = None,
) -> AggregationResult:
    """Named structured aggregation seam for adapters and diagnostics."""
    return aggregate_zone_temperature_result(zone, snapshot, now=now)


def aggregate_zone_temperature(
    zone: Zone,
    snapshot: PlantSnapshot,
    *,
    now: datetime | None = None,
) -> float | None:
    """Return only the aggregate value for legacy entity callers."""
    return aggregate_zone_temperature_result(zone, snapshot, now=now).value


def aggregate_zone_humidity_result(
    zone: Zone,
    snapshot: PlantSnapshot,
    *,
    now: datetime | None = None,
) -> AggregationResult:
    """Aggregate required and optional relative-humidity observations."""
    metadata = tuple(sorted(zone.humidity_sensor_metadata, key=lambda sensor: sensor.entity_id))
    usable: list[tuple[str, float, float]] = []
    excluded_optional: list[str] = []
    blocking_required: list[str] = []
    failure_reasons: dict[str, str] = {}
    for sensor in metadata:
        observation = snapshot.humidities.get(sensor.entity_id)
        valid, reason = _observation_is_usable(
            observation,
            max_age_seconds=sensor.max_age_seconds,
            now=now,
        )
        if not valid:
            failure_reasons[sensor.entity_id] = reason
            if sensor.required:
                blocking_required.append(sensor.entity_id)
            else:
                excluded_optional.append(sensor.entity_id)
            continue
        assert observation is not None
        assert observation.value is not None
        calibrated = observation.value + sensor.calibration_offset
        if not isfinite(calibrated):
            failure_reasons[sensor.entity_id] = "non-finite after calibration"
            if sensor.required:
                blocking_required.append(sensor.entity_id)
            else:
                excluded_optional.append(sensor.entity_id)
            continue
        usable.append((sensor.entity_id, calibrated, sensor.weight))

    usable_ids = tuple(sensor_id for sensor_id, _value, _weight in usable)
    excluded_ids = tuple(sorted(excluded_optional))
    blocking_ids = tuple(sorted(blocking_required))
    value: float | None = None
    if not blocking_ids and usable:
        weights = [weight for _sensor_id, _value, weight in usable]
        if all(isfinite(weight) and weight > 0 for weight in weights):
            value = fsum(value * weight for _sensor_id, value, weight in usable) / fsum(weights)

    failure_text = "; ".join(
        f"{sensor_id} ({failure_reasons[sensor_id]})" for sensor_id in sorted(failure_reasons)
    )
    if blocking_ids:
        explanation = "Blocked: required humidity sensors are unusable: " + ", ".join(
            item for item in failure_text.split("; ") if item.split(" ", 1)[0] in blocking_ids
        )
    elif not usable:
        explanation = "Blocked: no usable humidity sensors remain."
    elif value is None:
        explanation = "Blocked: humidity aggregation could not produce a finite value."
    else:
        explanation = f"Aggregated {value:.2f} % from {', '.join(usable_ids)}."
    if excluded_ids:
        explanation += (
            " Excluded optional sensors: "
            + ", ".join(f"{sensor_id} ({failure_reasons[sensor_id]})" for sensor_id in excluded_ids)
            + "."
        )
    return AggregationResult(
        value=value,
        usable_sensor_ids=usable_ids,
        excluded_optional_sensor_ids=excluded_ids,
        blocking_required_sensor_ids=blocking_ids,
        explanation=explanation,
    )


def aggregate_zone_humidity(
    zone: Zone,
    snapshot: PlantSnapshot,
    *,
    now: datetime | None = None,
) -> float | None:
    """Return the deterministic relative-humidity aggregate."""
    return aggregate_zone_humidity_result(zone, snapshot, now=now).value


def dew_point_celsius(temperature_celsius: float, relative_humidity: float) -> float | None:
    """Calculate dew point with the deterministic Magnus approximation."""
    if not isfinite(temperature_celsius) or not isfinite(relative_humidity):
        return None
    if relative_humidity <= 0 or relative_humidity > 100:
        return None
    gamma = log(relative_humidity / 100.0) + (
        _DEW_POINT_A * temperature_celsius / (_DEW_POINT_B + temperature_celsius)
    )
    denominator = _DEW_POINT_A - gamma
    if denominator == 0 or not isfinite(denominator):
        return None
    result = _DEW_POINT_B * gamma / denominator
    return result if isfinite(result) else None


def calculate_dew_point(temperature_celsius: float, relative_humidity: float) -> float | None:
    """Compatibility name for the public dew-point calculation seam."""
    return dew_point_celsius(temperature_celsius, relative_humidity)


def condensation_margin(reference_temperature: float, dew_point: float) -> float | None:
    """Return the temperature distance between a reference and dew point."""
    if not isfinite(reference_temperature) or not isfinite(dew_point):
        return None
    result = reference_temperature - dew_point
    return result if isfinite(result) else None


def calculate_condensation_margin(reference_temperature: float, dew_point: float) -> float | None:
    """Compatibility name for the public condensation-margin seam."""
    return condensation_margin(reference_temperature, dew_point)


def _zone_runtime(runtime: RuntimeState, zone_id: str) -> ZoneRuntime:
    """Read the controller-owned timing and demand state for one zone."""
    return runtime.zone_runtime.get(zone_id, ZoneRuntime())


def _zone_demand(
    *,
    previous: bool,
    temperature: float | None,
    target: float,
    start_delta: float,
    stop_delta: float,
) -> tuple[bool, str]:
    """Apply heating hysteresis to one zone without timing side effects."""
    if temperature is None:
        return False, "Blocked: the zone has no usable aggregate temperature."
    if temperature <= target - start_delta:
        return True, f"Heating requested: {temperature:.1f} is below {target - start_delta:.1f}."
    if temperature >= target + stop_delta:
        return False, f"Satisfied: {temperature:.1f} is at or above {target + stop_delta:.1f}."
    if previous:
        return True, "Heating remains requested inside the hysteresis band."
    return False, "Heating remains idle inside the hysteresis band."


def _cooling_zone_demand(
    *,
    previous: bool,
    temperature: float | None,
    target: float,
    start_delta: float,
    stop_delta: float,
) -> tuple[bool, str]:
    """Apply explicit cooling hysteresis to one zone."""
    if temperature is None:
        return False, "Blocked: the zone has no usable aggregate temperature for cooling."
    if temperature >= target + start_delta:
        return True, f"Cooling requested: {temperature:.1f} is above {target + start_delta:.1f}."
    if temperature <= target - stop_delta:
        return False, f"Satisfied: {temperature:.1f} is at or below {target - stop_delta:.1f}."
    if previous:
        return True, "Cooling remains requested inside the hysteresis band."
    return False, "Cooling remains idle inside the hysteresis band."


def resolve_cooling_delivery_routes(
    plant: CompiledPlant,
    cooling_zone_demands: Mapping[str, bool],
    *,
    blocked_circuit_ids: frozenset[str] = frozenset(),
) -> tuple[DeliveryRoute, ...]:
    """Return enabled routes whose circuits explicitly support cooling.

    ``blocked_circuit_ids`` is populated only by deterministic shared-equipment
    arbitration.  Keeping it at the route seam makes heating-only circuits
    impossible to select even when a caller supplies malformed demand maps.
    """
    return tuple(
        sorted(
            (
                route
                for route in plant.routes
                if route.enabled
                and cooling_zone_demands.get(route.zone_id, False)
                and plant.circuits[route.circuit_id].cooling_enabled
                and route.circuit_id not in blocked_circuit_ids
            ),
            key=lambda route: (route.zone_id, route.circuit_id, route.id),
        )
    )


def resolve_mode_conflicts(
    plant: CompiledPlant,
    heating_routes: tuple[DeliveryRoute, ...],
    cooling_routes: tuple[DeliveryRoute, ...],
) -> tuple[ModeConflict, ...]:
    """Find deterministic heating/cooling conflicts on shared equipment.

    Heating wins a conflict because cooling is the safety-sensitive mode.  The
    caller removes the conflicting cooling routes before compiling consumer
    sets, so one physical actuator never receives both mode requests.
    """
    heating_circuit_ids = {route.circuit_id for route in heating_routes}
    cooling_circuit_ids = {route.circuit_id for route in cooling_routes}
    if not heating_circuit_ids or not cooling_circuit_ids:
        return ()

    def _zone_ids(routes: tuple[DeliveryRoute, ...], circuit_ids: set[str]) -> tuple[str, ...]:
        return tuple(sorted({route.zone_id for route in routes if route.circuit_id in circuit_ids}))

    conflicts: list[ModeConflict] = []
    for valve_id in sorted(plant.valves):
        heating = tuple(
            sorted(
                circuit.id
                for circuit in plant.circuits.values()
                if circuit.id in heating_circuit_ids and valve_id in circuit.valve_ids
            )
        )
        cooling = tuple(
            sorted(
                circuit.id
                for circuit in plant.circuits.values()
                if circuit.id in cooling_circuit_ids and valve_id in circuit.valve_ids
            )
        )
        if not heating or not cooling:
            continue
        valve = plant.valves[valve_id]
        conflicts.append(
            ModeConflict(
                code="shared_valve_heating_cooling_conflict",
                equipment_kind=EquipmentKind.VALVE,
                equipment_id=valve_id,
                heating_circuit_ids=heating,
                cooling_circuit_ids=cooling,
                heating_zone_ids=_zone_ids(heating_routes, set(heating)),
                cooling_zone_ids=_zone_ids(cooling_routes, set(cooling)),
                message=(
                    f"Cooling blocked by shared valve {valve.name} ({valve_id}): "
                    f"heating circuits {', '.join(heating)} and cooling circuits "
                    f"{', '.join(cooling)} cannot be requested simultaneously."
                ),
            )
        )

    for pump_id in sorted(plant.pumps):
        heating = tuple(
            sorted(
                circuit.id
                for circuit in plant.circuits.values()
                if circuit.id in heating_circuit_ids and circuit.pump_id == pump_id
            )
        )
        cooling = tuple(
            sorted(
                circuit.id
                for circuit in plant.circuits.values()
                if circuit.id in cooling_circuit_ids and circuit.pump_id == pump_id
            )
        )
        if not heating or not cooling:
            continue
        pump = plant.pumps[pump_id]
        conflicts.append(
            ModeConflict(
                code="shared_pump_heating_cooling_conflict",
                equipment_kind=EquipmentKind.PUMP,
                equipment_id=pump_id,
                heating_circuit_ids=heating,
                cooling_circuit_ids=cooling,
                heating_zone_ids=_zone_ids(heating_routes, set(heating)),
                cooling_zone_ids=_zone_ids(cooling_routes, set(cooling)),
                message=(
                    f"Cooling blocked by shared pump {pump.name} ({pump_id}): "
                    f"heating circuits {', '.join(heating)} and cooling circuits "
                    f"{', '.join(cooling)} cannot be requested simultaneously."
                ),
            )
        )

    # Sources are currently plant-owned rather than circuit-owned.  Therefore
    # every configured source is shared by all active hydraulic mode requests.
    for source_id in sorted(plant.sources):
        source = plant.sources[source_id]
        heating = tuple(sorted(heating_circuit_ids))
        cooling = tuple(sorted(cooling_circuit_ids))
        conflicts.append(
            ModeConflict(
                code="shared_source_heating_cooling_conflict",
                equipment_kind=EquipmentKind.SOURCE,
                equipment_id=source_id,
                heating_circuit_ids=heating,
                cooling_circuit_ids=cooling,
                heating_zone_ids=_zone_ids(heating_routes, heating_circuit_ids),
                cooling_zone_ids=_zone_ids(cooling_routes, cooling_circuit_ids),
                message=(
                    f"Cooling blocked by shared source {source.name} ({source_id}): "
                    "heating and cooling requests cannot be active simultaneously."
                ),
            )
        )
    return tuple(conflicts)


def _cooling_interlocks(
    plant: CompiledPlant,
    zone: Zone,
    temperature: float | None,
    humidity: float | None,
    snapshot: PlantSnapshot,
    now: datetime,
) -> tuple[bool, str, tuple[SafetyInterlockResult, ...], float | None, float | None]:
    """Evaluate every cooling circuit reference for one zone fail-closed."""
    routes = tuple(
        route
        for route in plant.routes
        if route.enabled
        and route.zone_id == zone.id
        and plant.circuits[route.circuit_id].cooling_enabled
    )
    if not routes:
        return (
            False,
            "Cooling idle: no cooling-enabled circuit serves this zone.",
            (),
            None,
            None,
        )
    if temperature is None or humidity is None:
        result = SafetyInterlockResult(
            f"cooling:{zone.id}:observations",
            InterlockStatus.BLOCKED,
            "Cooling is blocked until required temperature and humidity observations are usable.",
        )
        return False, result.reason, (result,), None, None

    dew_point = dew_point_celsius(temperature, humidity)
    if dew_point is None:
        result = SafetyInterlockResult(
            f"cooling:{zone.id}:dew-point",
            InterlockStatus.BLOCKED,
            "Cooling is blocked because relative humidity is outside the usable 0-100% range.",
        )
        return False, result.reason, (result,), None, None

    interlocks: list[SafetyInterlockResult] = []
    margins: list[float] = []
    for route in routes:
        circuit = plant.circuits[route.circuit_id]
        references = (
            (
                "supply",
                circuit.supply_temperature_sensor,
                circuit.supply_temperature_max_age_seconds,
            ),
            (
                "surface",
                circuit.surface_temperature_sensor,
                circuit.surface_temperature_max_age_seconds,
            ),
        )
        circuit_blocked = False
        circuit_margins: list[float] = []
        for reference_name, entity_id, max_age in references:
            if entity_id is None:
                continue
            observation = (
                snapshot.supply_temperatures.get(entity_id)
                if reference_name == "supply"
                else snapshot.surface_temperatures.get(entity_id)
            )
            usable, reason = _observation_is_usable(
                observation,
                max_age_seconds=max_age,
                now=now,
            )
            if not usable:
                circuit_blocked = True
                interlocks.append(
                    SafetyInterlockResult(
                        f"cooling:{route.circuit_id}:{reference_name}",
                        InterlockStatus.BLOCKED,
                        f"Cooling is blocked because the required {reference_name} reference "
                        f"{entity_id} is {reason}.",
                    )
                )
                continue
            assert observation is not None and observation.value is not None
            margin = condensation_margin(observation.value, dew_point)
            assert margin is not None
            circuit_margins.append(margin)
            margins.append(margin)
            if margin <= circuit.condensation_margin:
                circuit_blocked = True
                interlocks.append(
                    SafetyInterlockResult(
                        f"cooling:{route.circuit_id}:{reference_name}",
                        InterlockStatus.BLOCKED,
                        f"Cooling is blocked before the condensation margin is crossed: "
                        f"{margin:.2f} °C is at or below {circuit.condensation_margin:.2f} °C.",
                    )
                )
            else:
                interlocks.append(
                    SafetyInterlockResult(
                        f"cooling:{route.circuit_id}:{reference_name}",
                        InterlockStatus.PERMITTED,
                        f"{reference_name.title()} reference leaves {margin:.2f} °C of "
                        f"condensation margin.",
                    )
                )
        if circuit_blocked:
            return (
                False,
                next(
                    item.reason
                    for item in reversed(interlocks)
                    if item.status is InterlockStatus.BLOCKED
                ),
                tuple(interlocks),
                dew_point,
                min(circuit_margins) if circuit_margins else None,
            )

    return (
        True,
        f"Cooling safety permitted: dew point is {dew_point:.2f} °C and the lowest "
        f"reference margin is {min(margins):.2f} °C.",
        tuple(interlocks),
        dew_point,
        min(margins),
    )


def _apply_zone_timing(
    *,
    previous: ZoneRuntime,
    requested: bool,
    now: datetime,
    minimum_active_seconds: float,
    minimum_idle_seconds: float,
) -> tuple[ZoneRuntime, ZoneDecisionStatus, datetime | None, str]:
    """Apply minimum active and idle durations after hysteresis."""
    transition_at = previous.last_demand_transition_at
    # A missing transition timestamp is restored state without trustworthy age.
    # Seed it at this evaluation and enforce the full configured duration.
    if transition_at is None:
        transition_at = now

    duration = minimum_active_seconds if previous.demand else minimum_idle_seconds
    deadline = transition_at + timedelta(seconds=duration)
    if (
        requested != previous.demand
        and duration > 0
        and _elapsed(now, transition_at) < timedelta(seconds=duration)
    ):
        if previous.demand:
            return (
                ZoneRuntime(demand=True, last_demand_transition_at=transition_at),
                ZoneDecisionStatus.DURATION_HELD,
                deadline,
                f"Heating remains active until minimum active deadline {deadline.isoformat()}.",
            )
        return (
            ZoneRuntime(demand=False, last_demand_transition_at=transition_at),
            ZoneDecisionStatus.DURATION_LOCKED,
            deadline,
            f"Heating remains idle until minimum idle deadline {deadline.isoformat()}.",
        )

    if requested != previous.demand:
        transition_at = now
    return (
        ZoneRuntime(demand=requested, last_demand_transition_at=transition_at),
        ZoneDecisionStatus.REQUESTED if requested else ZoneDecisionStatus.SATISFIED,
        None,
        "",
    )


def _target_mode(
    requested_mode: PlantMode,
    *,
    heating_demand: bool,
    cooling_demand: bool,
) -> PlantMode:
    """Select the next mode without allowing safety-sensitive cooling to win ties."""
    if requested_mode is PlantMode.IDLE:
        return PlantMode.IDLE
    if requested_mode is PlantMode.HEATING:
        return PlantMode.HEATING
    if requested_mode is PlantMode.COOLING:
        return PlantMode.COOLING
    if heating_demand:
        return PlantMode.HEATING
    if cooling_demand:
        return PlantMode.COOLING
    return PlantMode.IDLE


def _equipment_requires_safe_idle(
    plant: CompiledPlant,
    runtime: RuntimeState,
) -> bool:
    """Return whether any shared path or source can still carry the old mode."""
    if runtime.selected_source_id is not None:
        return True
    if any(
        runtime_state.state is not ValveState.CLOSED
        for runtime_state in runtime.valves.values()
    ):
        return True
    return any(runtime_state.state is not PumpState.OFF for runtime_state in runtime.pumps.values())


def _source_release_commands(plant: CompiledPlant) -> list[ActuatorCommand]:
    """Release every configured source demand before moving hydraulic mode."""
    return [
        ActuatorCommand(
            f"source:{source.id}",
            ActuatorAction.TURN_OFF,
            "Release source demand before changing the shared plant mode.",
        )
        for source in sorted(plant.sources.values(), key=lambda item: item.id)
        if source.demand_entity_id is not None
    ]


def _advance_changeover_phase(
    phase: ModeChangeoverPhase,
    *,
    pumps: Mapping[str, PumpRuntime],
    valves: Mapping[str, ValveRuntime],
) -> ModeChangeoverPhase:
    """Advance only after observed virtual state satisfies the prior phase."""
    pumps_idle = all(item.state is PumpState.OFF for item in pumps.values())
    valves_idle = all(item.state is ValveState.CLOSED for item in valves.values())
    if phase is ModeChangeoverPhase.IDLE:
        return phase
    if phase is ModeChangeoverPhase.SOURCE_RELEASE:
        if not pumps_idle:
            return (
                ModeChangeoverPhase.PUMP_OVERRUN
                if any(item.state is PumpState.OVERRUN for item in pumps.values())
                else ModeChangeoverPhase.PUMPS_STOPPING
            )
        return ModeChangeoverPhase.IDLE if valves_idle else ModeChangeoverPhase.VALVES_CLOSING
    if phase in {ModeChangeoverPhase.PUMP_OVERRUN, ModeChangeoverPhase.PUMPS_STOPPING}:
        return ModeChangeoverPhase.VALVES_CLOSING if pumps_idle else phase
    return ModeChangeoverPhase.IDLE if valves_idle else ModeChangeoverPhase.VALVES_CLOSING


def resolve_delivery_routes(
    plant: CompiledPlant, zone_demands: Mapping[str, bool]
) -> tuple[DeliveryRoute, ...]:
    """Return eligible routes under deterministic heating-only any-demand policy."""
    return tuple(
        sorted(
            (
                route
                for route in plant.routes
                if route.enabled and zone_demands.get(route.zone_id, False)
            ),
            key=lambda route: (route.zone_id, route.circuit_id, route.id),
        )
    )


def _source_eligibility(
    source: Source,
    snapshot: PlantSnapshot,
    runtime: RuntimeState,
    now: datetime,
) -> tuple[bool, str]:
    """Return deterministic eligibility and a diagnostic reason for one source."""
    if (
        source.demand_entity_id is not None
        and source.demand_entity_id in snapshot.unavailable_entity_ids
    ):
        return False, "demand output is unavailable"
    availability = snapshot.source_availability.get(source.id)
    if availability is None and source.availability_entity_id is not None:
        availability = snapshot.source_availability.get(source.availability_entity_id)
    if source.availability_entity_id is not None and availability is not True:
        return False, "availability is false or unavailable"
    if availability is False:
        return False, "availability is false"
    if source.kind is not SourceKind.TEMPERATURE_QUALIFIED_BUFFER:
        return True, "available"

    observation = snapshot.source_temperatures.get(source.id)
    if observation is None and source.temperature_entity_id is not None:
        observation = snapshot.source_temperatures.get(source.temperature_entity_id)
    usable, reason = _observation_is_usable(
        observation,
        max_age_seconds=source.maximum_age_seconds,
        now=now,
    )
    if not usable:
        return False, f"buffer temperature is {reason}"
    assert observation is not None
    assert observation.value is not None
    assert source.minimum_temperature is not None
    threshold = source.minimum_temperature - (
        source.hysteresis if runtime.selected_source_id == source.id else 0.0
    )
    if observation.value < threshold:
        return False, f"buffer temperature {observation.value:.1f} °C is below {threshold:.1f} °C"
    return True, f"buffer temperature {observation.value:.1f} °C meets {threshold:.1f} °C"


def recommend_source(
    plant: CompiledPlant,
    snapshot: PlantSnapshot,
    runtime: RuntimeState,
    now: datetime,
    *,
    active_heating: bool = True,
) -> SourceRecommendation | None:
    """Recommend one eligible source without producing a source command.

    Lower numeric priorities win, with source IDs as the stable tie-breaker.
    A selected buffer source uses the lower hysteresis threshold while a new
    buffer recommendation uses its configured qualification threshold.
    """
    if not plant.sources:
        return None
    if not active_heating:
        return SourceRecommendation(None, "No active heating demand.")

    eligible: list[Source] = []
    reasons: list[str] = []
    for source in sorted(plant.sources.values(), key=lambda item: (item.priority, item.id)):
        is_eligible, reason = _source_eligibility(source, snapshot, runtime, now)
        if is_eligible:
            eligible.append(source)
        else:
            reasons.append(f"{source.name} ({source.id}): {reason}.")
    eligible_ids = tuple(source.id for source in eligible)
    if not eligible:
        details = " ".join(reasons) if reasons else "No configured source is eligible."
        return SourceRecommendation(None, f"No eligible heat source. {details}", eligible_ids)

    selected = eligible[0]
    explanation = (
        f"Recommended source: {selected.name} ({selected.id}), priority {selected.priority}. "
        f"Eligible sources: {', '.join(eligible_ids)}."
    )
    if reasons:
        explanation += " Ineligible sources: " + " ".join(reasons)
    return SourceRecommendation(selected.id, explanation, eligible_ids)


def _source_selector_observation(
    selector: SourceSelectionActuator,
    snapshot: PlantSnapshot,
    plant: CompiledPlant,
) -> tuple[str | None, bool]:
    """Return a known synthetic selector value and whether feedback is usable."""
    if selector.entity_id is None:
        return None, True
    if selector.id not in snapshot.source_selector_states:
        return None, False
    value = snapshot.source_selector_states[selector.id]
    if value is None:
        return None, False
    normalized = str(value).strip()
    if normalized in {"", selector.release_option, "none", "off", "unavailable", "unknown"}:
        return None, normalized in {selector.release_option, "none", "off"}
    if normalized not in plant.sources:
        return None, False
    return normalized, True


def _source_hydraulic_safety(
    plant: CompiledPlant,
    runtime: RuntimeState,
    valves: Mapping[str, ValveRuntime],
    pumps: Mapping[str, PumpRuntime],
    commands: tuple[ActuatorCommand, ...],
    plant_mode: PlantMode,
) -> tuple[bool, str]:
    """Return whether source selection may run during the current hydraulic state."""
    hydraulic_actuator_ids = set(plant.valves) | set(plant.pumps)
    if any(command.actuator_id in hydraulic_actuator_ids for command in commands):
        return False, "Waiting for valve and pump commands to settle."
    if any(valve.state is ValveState.OPENING for valve in valves.values()):
        return False, "Waiting for valve opening to complete."
    if any(
        pump.state in {PumpState.WAITING_FOR_VALVES, PumpState.STARTING, PumpState.OVERRUN}
        for pump in pumps.values()
    ):
        return False, "Waiting for the pump transition or overrun to complete."
    if runtime.plant_mode is not PlantMode.IDLE and runtime.plant_mode is not plant_mode:
        return False, "Waiting for the plant operating-mode transition to complete."
    return True, "Hydraulic valves, pumps, and plant mode are stable."


def _source_demand_permit(
    plant: CompiledPlant,
    ready_circuits: set[str],
    pump_consumers: Mapping[str, frozenset[str] | set[str]],
    pumps: Mapping[str, PumpRuntime],
    plant_mode: PlantMode,
) -> tuple[bool, str]:
    """Require one ready, demand-carrying circuit with a running pump."""
    if plant_mode is not PlantMode.HEATING:
        return False, "Heat-pump demand is blocked because the plant is not heating."
    if not ready_circuits:
        return False, "Heat-pump demand is blocked until a delivery circuit is ready."
    running_paths: list[str] = []
    waiting_paths: list[str] = []
    for circuit_id in sorted(ready_circuits):
        circuit = plant.circuits[circuit_id]
        pump = pumps.get(circuit.pump_id)
        if (
            pump is not None
            and pump.state is PumpState.RUNNING
            and circuit_id in pump_consumers.get(circuit.pump_id, frozenset())
        ):
            running_paths.append(circuit_id)
        else:
            waiting_paths.append(circuit_id)
    if running_paths:
        return True, f"Heat-pump demand permitted by running ready circuit {running_paths[0]}."
    return (
        False,
        "Heat-pump demand is blocked until a ready circuit has a running pump path"
        + (f" (waiting for {', '.join(waiting_paths)})." if waiting_paths else "."),
    )


def _source_selection_command(
    selector: SourceSelectionActuator | None,
    source: Source,
    *,
    action: ActuatorAction,
    reason: str,
) -> ActuatorCommand | None:
    """Build one explicit selector or source-demand command."""
    if selector is not None and selector.entity_id is not None:
        target = selector.release_option if action is ActuatorAction.TURN_OFF else source.id
        return ActuatorCommand(selector.id, ActuatorAction.SELECT, reason, target)
    if source.demand_entity_id is None:
        return None
    return ActuatorCommand(f"source:{source.id}", action, reason)


def _source_release_command(
    selector: SourceSelectionActuator | None,
    source: Source,
    *,
    reason: str,
) -> ActuatorCommand | None:
    """Build an explicit old-source release operation."""
    if selector is not None and selector.entity_id is not None:
        return ActuatorCommand(selector.id, ActuatorAction.SELECT, reason, selector.release_option)
    if source.demand_entity_id is None:
        return None
    return ActuatorCommand(f"source:{source.id}", ActuatorAction.TURN_OFF, reason)


@dataclass(frozen=True, slots=True)
class _SourceObservationReconciliation:
    """Canonical source state derived from runtime and observed actuator feedback."""

    selection: SourceSelectionRuntime
    active_source_id: str | None
    observed_source_id: str | None
    blocked_diagnostic: SourceSelectionDiagnostic | None = None


def _reconcile_source_observations(
    plant: CompiledPlant,
    snapshot: PlantSnapshot,
    runtime: RuntimeState,
    now: datetime,
    recommended_source_id: str | None,
) -> _SourceObservationReconciliation:
    """Normalize selector, direct demand, and restored source-selection state."""
    selector = plant.source_selector
    selection = runtime.source_selection
    observed_source: str | None = None
    if selector is not None:
        observed_source, observed_known = _source_selector_observation(selector, snapshot, plant)
        if selector.entity_id is not None and not observed_known:
            return _SourceObservationReconciliation(
                selection,
                selection.active_source_id,
                observed_source,
                SourceSelectionDiagnostic(
                    SourceSelectionPhase.WAITING_FOR_HYDRAULICS,
                    selection.active_source_id,
                    selection.target_source_id,
                    recommended_source_id,
                    False,
                    "Source selection is held because selector feedback is unknown or invalid.",
                ),
            )

    active_source_id = selection.active_source_id
    if active_source_id is None and selector is None:
        active_source_id = runtime.selected_source_id
        observed_demand_sources = tuple(
            sorted(
                source_id
                for source_id in plant.sources
                if snapshot.source_demand_states.get(source_id) is True
            )
        )
        if len(observed_demand_sources) > 1:
            return _SourceObservationReconciliation(
                selection,
                active_source_id,
                observed_source,
                SourceSelectionDiagnostic(
                    SourceSelectionPhase.WAITING_FOR_HYDRAULICS,
                    None,
                    recommended_source_id,
                    recommended_source_id,
                    False,
                    "Source selection is blocked because multiple source demands are observed on.",
                ),
            )
        if len(observed_demand_sources) == 1 and selection.phase not in {
            SourceSelectionPhase.BREAKING,
            SourceSelectionPhase.SELECTING,
        }:
            active_source_id = observed_demand_sources[0]
            selection = SourceSelectionRuntime(
                SourceSelectionPhase.ACTIVE,
                active_source_id,
                active_source_id,
                now,
                selection.last_selected_at or now,
            )
        demand_entity_ids = tuple(
            source_id
            for source_id, source in plant.sources.items()
            if source.demand_entity_id is not None
        )
        if (
            active_source_id is None
            and selection.phase is SourceSelectionPhase.IDLE
            and recommended_source_id is not None
            and demand_entity_ids
            and all(source_id in snapshot.source_demand_states for source_id in demand_entity_ids)
            and not observed_demand_sources
        ):
            selection = SourceSelectionRuntime(
                SourceSelectionPhase.BREAKING,
                None,
                recommended_source_id,
                now,
                None,
            )
    if (
        selector is not None
        and selector.entity_id is not None
        and selection.phase
        not in {SourceSelectionPhase.BREAKING, SourceSelectionPhase.SELECTING}
        and observed_source != active_source_id
    ):
        active_source_id = observed_source
        selection = SourceSelectionRuntime(
            phase=(
                SourceSelectionPhase.ACTIVE
                if observed_source
                else SourceSelectionPhase.BREAKING
                if recommended_source_id is not None
                else SourceSelectionPhase.IDLE
            ),
            active_source_id=observed_source,
            target_source_id=observed_source or recommended_source_id,
            transition_started_at=now,
            last_selected_at=now if observed_source else None,
        )
    return _SourceObservationReconciliation(selection, active_source_id, observed_source)


def _advance_breaking_source_selection(
    plant: CompiledPlant,
    snapshot: PlantSnapshot,
    selection: SourceSelectionRuntime,
    now: datetime,
    recommended_source_id: str | None,
    observed_source: str | None,
    *,
    hydraulic_safe: bool,
    hydraulic_reason: str,
    demand_permitted: bool,
    demand_reason: str,
) -> tuple[SourceSelectionRuntime, tuple[ActuatorCommand, ...], SourceSelectionDiagnostic]:
    """Advance old-source release and the configured break interval."""
    selector = plant.source_selector
    target_source_id = recommended_source_id
    if target_source_id is not None and target_source_id not in plant.sources:
        target_source_id = None
    started_at = selection.transition_started_at or now
    elapsed = _elapsed(now, started_at)
    released_source_id = selection.released_source_id
    if (
        selector is None
        and released_source_id is not None
        and released_source_id in snapshot.source_demand_states
        and snapshot.source_demand_states[released_source_id]
    ):
        diagnostic = SourceSelectionDiagnostic(
            SourceSelectionPhase.BREAKING,
            released_source_id,
            target_source_id,
            recommended_source_id,
            False,
            "Waiting for observed old-source release before selecting another source.",
        )
        return selection, (), diagnostic
    if selector is not None and selector.entity_id is not None and observed_source is not None:
        if observed_source == target_source_id:
            if elapsed < timedelta(seconds=selector.break_interval_seconds):
                diagnostic = SourceSelectionDiagnostic(
                    SourceSelectionPhase.BREAKING,
                    None,
                    target_source_id,
                    recommended_source_id,
                    False,
                    "Waiting for the configured break interval before accepting "
                    "selector feedback.",
                )
                return selection, (), diagnostic
            selected_at = selection.last_selected_at or now
            phase = (
                SourceSelectionPhase.MINIMUM_DWELL
                if selector.minimum_dwell_seconds > 0
                else SourceSelectionPhase.ACTIVE
            )
            next_selection = SourceSelectionRuntime(
                phase,
                target_source_id,
                target_source_id,
                selected_at,
                selected_at,
            )
            return (
                next_selection,
                (),
                SourceSelectionDiagnostic(
                    phase,
                    target_source_id,
                    target_source_id,
                    recommended_source_id,
                    True,
                    "Selector feedback confirms the target after the break interval.",
                ),
            )
        diagnostic = SourceSelectionDiagnostic(
            SourceSelectionPhase.BREAKING,
            None,
            target_source_id,
            recommended_source_id,
            False,
            "Waiting for selector feedback to confirm that the old source is released.",
        )
        return selection, (), diagnostic
    if not hydraulic_safe:
        diagnostic = SourceSelectionDiagnostic(
            SourceSelectionPhase.BREAKING,
            None,
            target_source_id,
            recommended_source_id,
            False,
            f"Source release is complete; {hydraulic_reason}",
        )
        return (
            SourceSelectionRuntime(
                SourceSelectionPhase.BREAKING,
                None,
                target_source_id,
                started_at,
                selection.last_selected_at,
                selection.released_source_id,
            ),
            (),
            diagnostic,
        )
    if target_source_id is not None and not demand_permitted:
        diagnostic = SourceSelectionDiagnostic(
            SourceSelectionPhase.BREAKING,
            None,
            target_source_id,
            recommended_source_id,
            False,
            demand_reason,
        )
        return selection, (), diagnostic
    if elapsed < timedelta(seconds=(selector.break_interval_seconds if selector else 0.0)):
        diagnostic = SourceSelectionDiagnostic(
            SourceSelectionPhase.BREAKING,
            None,
            target_source_id,
            recommended_source_id,
            True,
            "Old source released; observing the configured break interval before selection.",
        )
        return (
            SourceSelectionRuntime(
                SourceSelectionPhase.BREAKING,
                None,
                target_source_id,
                started_at,
                selection.last_selected_at,
                selection.released_source_id,
            ),
            (),
            diagnostic,
        )
    if target_source_id is None:
        next_selection = SourceSelectionRuntime(
            SourceSelectionPhase.IDLE,
            None,
            None,
            now,
            None,
        )
        return (
            next_selection,
            (),
            SourceSelectionDiagnostic(
                SourceSelectionPhase.IDLE,
                None,
                None,
                recommended_source_id,
                True,
                "No eligible source remains after the break interval.",
            ),
        )
    target_source = plant.sources[target_source_id]
    command = _source_selection_command(
        selector,
        target_source,
        action=ActuatorAction.TURN_ON,
        reason="Select the fallback source after the break interval.",
    )
    next_selection = SourceSelectionRuntime(
        SourceSelectionPhase.SELECTING,
        None,
        target_source_id,
        now,
        selection.last_selected_at,
        None,
    )
    return (
        next_selection,
        (command,) if command is not None else (),
        SourceSelectionDiagnostic(
            SourceSelectionPhase.SELECTING,
            None,
            target_source_id,
            recommended_source_id,
            True,
            "Break interval elapsed; selecting the new source.",
        ),
    )


def _advance_selecting_source_selection(
    plant: CompiledPlant,
    snapshot: PlantSnapshot,
    selection: SourceSelectionRuntime,
    now: datetime,
    recommended_source_id: str | None,
    observed_source: str | None,
    *,
    hydraulic_safe: bool,
) -> tuple[SourceSelectionRuntime, tuple[ActuatorCommand, ...], SourceSelectionDiagnostic]:
    """Advance explicit selection until one-hot feedback confirms the target."""
    selector = plant.source_selector
    target_source_id = selection.target_source_id
    if target_source_id is not None and recommended_source_id is None:
        target_source = plant.sources.get(target_source_id)
        release_command = (
            _source_release_command(
                selector,
                target_source,
                reason="Release source demand before heating demand becomes unsafe.",
            )
            if target_source is not None
            else None
        )
        next_selection = SourceSelectionRuntime(
            SourceSelectionPhase.BREAKING,
            target_source_id,
            None,
            now,
            selection.last_selected_at,
            target_source_id,
        )
        return (
            next_selection,
            (release_command,) if release_command is not None else (),
            SourceSelectionDiagnostic(
                SourceSelectionPhase.BREAKING,
                target_source_id,
                None,
                None,
                True,
                "Source demand released because no eligible heating source remains.",
            ),
        )
    if selector is not None and selector.entity_id is not None:
        if observed_source == target_source_id:
            selected_at = selection.transition_started_at or now
            phase = (
                SourceSelectionPhase.MINIMUM_DWELL
                if selector.minimum_dwell_seconds > 0
                else SourceSelectionPhase.ACTIVE
            )
            next_selection = SourceSelectionRuntime(
                phase,
                target_source_id,
                target_source_id,
                selected_at,
                selected_at,
            )
        else:
            next_selection = selection
    else:
        source_demand_observed = (
            target_source_id is not None
            and snapshot.source_demand_states.get(target_source_id) is True
            and all(
                source_id == target_source_id
                or snapshot.source_demand_states.get(source_id) is not True
                for source_id in plant.sources
            )
        )
        if source_demand_observed:
            selected_at = selection.transition_started_at or now
            phase = (
                SourceSelectionPhase.MINIMUM_DWELL
                if selector is not None and selector.minimum_dwell_seconds > 0
                else SourceSelectionPhase.ACTIVE
            )
            next_selection = SourceSelectionRuntime(
                phase,
                target_source_id,
                target_source_id,
                selected_at,
                selected_at,
            )
        else:
            next_selection = selection
    return (
        next_selection,
        (),
        SourceSelectionDiagnostic(
            SourceSelectionPhase.SELECTING,
            next_selection.active_source_id,
            target_source_id,
            recommended_source_id,
            hydraulic_safe,
            "Waiting for selector feedback after the explicit selection command.",
        ),
    )


def _advance_unselected_source(
    plant: CompiledPlant,
    selection: SourceSelectionRuntime,
    now: datetime,
    recommended_source_id: str | None,
    *,
    hydraulic_safe: bool,
    hydraulic_reason: str,
    demand_permitted: bool,
    demand_reason: str,
) -> tuple[SourceSelectionRuntime, tuple[ActuatorCommand, ...], SourceSelectionDiagnostic]:
    """Advance idle and hydraulic-waiting phases toward explicit selection."""
    selector = plant.source_selector
    if recommended_source_id is None:
        next_selection = SourceSelectionRuntime(SourceSelectionPhase.IDLE)
        explanation = "No eligible source is available for the active plant demand."
        phase = SourceSelectionPhase.IDLE
    elif not hydraulic_safe:
        next_selection = SourceSelectionRuntime(
            SourceSelectionPhase.WAITING_FOR_HYDRAULICS,
            None,
            recommended_source_id,
            selection.transition_started_at,
            selection.last_selected_at,
        )
        explanation = hydraulic_reason
        phase = SourceSelectionPhase.WAITING_FOR_HYDRAULICS
    elif not demand_permitted:
        next_selection = SourceSelectionRuntime(
            SourceSelectionPhase.WAITING_FOR_HYDRAULICS,
            None,
            recommended_source_id,
            selection.transition_started_at,
            selection.last_selected_at,
        )
        explanation = demand_reason
        phase = SourceSelectionPhase.WAITING_FOR_HYDRAULICS
    else:
        target_source = plant.sources[recommended_source_id]
        command = _source_selection_command(
            selector,
            target_source,
            action=ActuatorAction.TURN_ON,
            reason="Select the recommended source after hydraulic stabilization.",
        )
        next_selection = SourceSelectionRuntime(
            SourceSelectionPhase.SELECTING,
            None,
            recommended_source_id,
            now,
            selection.last_selected_at,
        )
        explanation = "Hydraulics are stable; selecting the recommended source."
        phase = SourceSelectionPhase.SELECTING
        return (
            next_selection,
            (command,) if command is not None else (),
            SourceSelectionDiagnostic(
                phase,
                None,
                recommended_source_id,
                recommended_source_id,
                True,
                explanation,
            ),
        )
    return (
        next_selection,
        (),
        SourceSelectionDiagnostic(
            phase,
            None,
            recommended_source_id,
            recommended_source_id,
            hydraulic_safe,
            explanation,
        ),
    )


def _advance_active_source(
    plant: CompiledPlant,
    selection: SourceSelectionRuntime,
    now: datetime,
    recommendation: SourceRecommendation | None,
    active_source_id: str,
    *,
    hydraulic_safe: bool,
    hydraulic_reason: str,
) -> tuple[SourceSelectionRuntime, tuple[ActuatorCommand, ...], SourceSelectionDiagnostic]:
    """Advance active, dwell, waiting, and release-first changeover phases."""
    selector = plant.source_selector
    recommended_source_id = recommendation.source_id if recommendation is not None else None
    active_source = plant.sources.get(active_source_id)
    if active_source is None:
        next_selection = SourceSelectionRuntime(SourceSelectionPhase.IDLE)
        return (
            next_selection,
            (),
            SourceSelectionDiagnostic(
                SourceSelectionPhase.IDLE,
                None,
                recommended_source_id,
                recommended_source_id,
                hydraulic_safe,
                "The restored active source is no longer configured; selection is reset safely.",
            ),
        )
    if recommended_source_id == active_source_id:
        selected_at = selection.last_selected_at
        dwell_seconds = selector.minimum_dwell_seconds if selector else 0.0
        if selected_at is not None and _elapsed(now, selected_at) < timedelta(
            seconds=dwell_seconds
        ):
            next_selection = SourceSelectionRuntime(
                SourceSelectionPhase.MINIMUM_DWELL,
                active_source_id,
                active_source_id,
                selection.transition_started_at,
                selected_at,
            )
            return (
                next_selection,
                (),
                SourceSelectionDiagnostic(
                    SourceSelectionPhase.MINIMUM_DWELL,
                    active_source_id,
                    active_source_id,
                    recommended_source_id,
                    hydraulic_safe,
                    "Minimum source dwell is holding the selected source.",
                ),
            )
        next_selection = SourceSelectionRuntime(
            SourceSelectionPhase.ACTIVE,
            active_source_id,
            active_source_id,
            selection.transition_started_at,
            selected_at,
        )
        return (
            next_selection,
            (),
            SourceSelectionDiagnostic(
                SourceSelectionPhase.ACTIVE,
                active_source_id,
                active_source_id,
                recommended_source_id,
                hydraulic_safe,
                "Selected source remains eligible without a changeover command.",
            ),
        )

    fallback = active_source_id not in (
        recommendation.eligible_source_ids if recommendation else ()
    )
    if fallback:
        selected_source = plant.sources[active_source_id]
        release_command = _source_release_command(
            selector,
            selected_source,
            reason=(
                "Release the unavailable source before deterministic fallback."
                if recommended_source_id is not None
                else "Release source demand because no eligible heating source remains."
            ),
        )
        next_selection = SourceSelectionRuntime(
            SourceSelectionPhase.BREAKING,
            None,
            recommended_source_id,
            now,
            selection.last_selected_at,
            active_source_id,
        )
        return (
            next_selection,
            (release_command,) if release_command is not None else (),
            SourceSelectionDiagnostic(
                SourceSelectionPhase.BREAKING,
                active_source_id,
                recommended_source_id,
                recommended_source_id,
                True,
                "Old source demand released before deterministic fallback.",
            ),
        )

    if not hydraulic_safe:
        next_selection = SourceSelectionRuntime(
            SourceSelectionPhase.WAITING_FOR_HYDRAULICS,
            active_source_id,
            recommended_source_id,
            selection.transition_started_at,
            selection.last_selected_at,
        )
        return (
            next_selection,
            (),
            SourceSelectionDiagnostic(
                SourceSelectionPhase.WAITING_FOR_HYDRAULICS,
                active_source_id,
                recommended_source_id,
                recommended_source_id,
                False,
                hydraulic_reason,
            ),
        )

    selected_at = selection.last_selected_at
    dwell_seconds = selector.minimum_dwell_seconds if selector else 0.0
    if (
        not fallback
        and selected_at is not None
        and _elapsed(now, selected_at) < timedelta(seconds=dwell_seconds)
    ):
        next_selection = SourceSelectionRuntime(
            SourceSelectionPhase.MINIMUM_DWELL,
            active_source_id,
            active_source_id,
            selection.transition_started_at,
            selected_at,
        )
        return (
            next_selection,
            (),
            SourceSelectionDiagnostic(
                SourceSelectionPhase.MINIMUM_DWELL,
                active_source_id,
                active_source_id,
                recommended_source_id,
                hydraulic_safe,
                "Minimum source dwell is holding the selected source before changeover.",
            ),
        )
    selected_source = plant.sources[active_source_id]
    release_command = _source_release_command(
        selector,
        selected_source,
        reason=(
            "Release the unavailable source before deterministic fallback."
            if fallback
            else "Release the old source before changeover."
        ),
    )
    next_selection = SourceSelectionRuntime(
        SourceSelectionPhase.BREAKING,
        None,
        recommended_source_id,
        now,
        selection.last_selected_at,
        active_source_id,
    )
    return (
        next_selection,
        (release_command,) if release_command is not None else (),
        SourceSelectionDiagnostic(
            SourceSelectionPhase.BREAKING,
            active_source_id,
            recommended_source_id,
            recommended_source_id,
            True,
            "Old source released; observing break-before-make interval.",
        ),
    )


def _advance_source_selection(
    plant: CompiledPlant,
    snapshot: PlantSnapshot,
    runtime: RuntimeState,
    now: datetime,
    recommendation: SourceRecommendation | None,
    *,
    hydraulic_safe: bool,
    hydraulic_reason: str,
    demand_permitted: bool,
    demand_reason: str,
) -> tuple[SourceSelectionRuntime, tuple[ActuatorCommand, ...], SourceSelectionDiagnostic | None]:
    """Advance source selection through a one-hot, break-before-make sequence."""
    selector = plant.source_selector
    has_demand_actuator = any(
        source.demand_entity_id is not None for source in plant.sources.values()
    )
    if selector is None and not has_demand_actuator:
        return (
            runtime.source_selection,
            (),
            SourceSelectionDiagnostic(
                SourceSelectionPhase.IDLE,
                runtime.selected_source_id,
                recommendation.source_id if recommendation is not None else None,
                recommendation.source_id if recommendation is not None else None,
                hydraulic_safe,
                "Source execution is disabled; the shadow recommendation remains available.",
            )
            if recommendation is not None
            else None,
        )

    recommended_source_id = recommendation.source_id if recommendation is not None else None
    reconciliation = _reconcile_source_observations(
        plant, snapshot, runtime, now, recommended_source_id
    )
    if reconciliation.blocked_diagnostic is not None:
        return reconciliation.selection, (), reconciliation.blocked_diagnostic
    selection = reconciliation.selection
    active_source_id = reconciliation.active_source_id
    observed_source = reconciliation.observed_source_id

    if selection.phase is SourceSelectionPhase.BREAKING:
        return _advance_breaking_source_selection(
            plant,
            snapshot,
            selection,
            now,
            recommended_source_id,
            observed_source,
            hydraulic_safe=hydraulic_safe,
            hydraulic_reason=hydraulic_reason,
            demand_permitted=demand_permitted,
            demand_reason=demand_reason,
        )

    if selection.phase is SourceSelectionPhase.SELECTING:
        return _advance_selecting_source_selection(
            plant,
            snapshot,
            selection,
            now,
            recommended_source_id,
            observed_source,
            hydraulic_safe=hydraulic_safe,
        )

    if active_source_id is None:
        return _advance_unselected_source(
            plant,
            selection,
            now,
            recommended_source_id,
            hydraulic_safe=hydraulic_safe,
            hydraulic_reason=hydraulic_reason,
            demand_permitted=demand_permitted,
            demand_reason=demand_reason,
        )

    return _advance_active_source(
        plant,
        selection,
        now,
        recommendation,
        active_source_id,
        hydraulic_safe=hydraulic_safe,
        hydraulic_reason=hydraulic_reason,
    )


def _feedback_is_fresh(
    observation: FeedbackObservation | None,
    *,
    max_age_seconds: float,
    now: datetime,
) -> tuple[bool, str]:
    """Validate a configured feedback reading at the safety decision boundary."""
    if observation is None or observation.value is None:
        return False, "missing"
    if observation.observed_at is None:
        return False, "missing timestamp"
    try:
        age = now - observation.observed_at
    except (TypeError, ValueError):
        return False, "invalid timestamp"
    if age > timedelta(seconds=max_age_seconds):
        return False, "stale"
    return True, "fresh"


def _feedback_boolean(value: float | bool | str | None) -> bool | None:
    """Decode a boolean feedback signal without guessing unknown values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"on", "true", "yes", "fault", "error", "1", "active"}:
            return True
        if normalized in {"off", "false", "no", "ok", "clear", "0", "inactive"}:
            return False
    return None


def _feedback_active(value: float | bool | str | None) -> bool | None:
    """Decode power or flow feedback as active, inactive, or unknown."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(isfinite(float(value)) and float(value) > 0)
    return _feedback_boolean(value)


def _position_state(value: float | bool | str | None) -> str | None:
    """Normalize a valve position feedback reading to open or closed."""
    if isinstance(value, bool):
        return "open" if value else "closed"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not isfinite(float(value)):
            return None
        if float(value) >= 99.0:
            return "open"
        if float(value) <= 1.0:
            return "closed"
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"open", "opening", "on", "true", "100", "100%"}:
            return "open" if normalized not in {"opening"} else None
        if normalized in {"closed", "closing", "off", "false", "0", "0%"}:
            return "closed" if normalized not in {"closing"} else None
    return None


def _feedback_diagnostics(
    plant: CompiledPlant,
    snapshot: PlantSnapshot,
    now: datetime,
    expected_states: Mapping[str, str],
) -> dict[str, ActuatorDiagnostic]:
    """Return stable feedback diagnostics for every configured actuator."""
    diagnostics: dict[str, ActuatorDiagnostic] = {}
    for actuator_id, valve in plant.valves.items():
        if valve.position_entity_id is None:
            diagnostics[actuator_id] = ActuatorDiagnostic(
                actuator_id,
                ActuatorFeedbackStatus.NOT_CONFIGURED,
                expected=expected_states.get(actuator_id),
                reason="No valve position feedback is configured.",
            )
            continue
        feedback = snapshot.actuator_feedback.get(actuator_id)
        observation = feedback.position if feedback is not None else None
        fresh, reason = _feedback_is_fresh(
            observation,
            max_age_seconds=valve.position_max_age_seconds,
            now=now,
        )
        expected = expected_states.get(actuator_id, "closed")
        if not fresh:
            diagnostics[actuator_id] = ActuatorDiagnostic(
                actuator_id,
                (
                    ActuatorFeedbackStatus.BLOCKED
                    if expected == "open"
                    else ActuatorFeedbackStatus.UNKNOWN
                ),
                blocked=expected == "open",
                expected=expected,
                feedback_kind="position",
                stale_feedback=("position",),
                reason=f"Valve position feedback is {reason}; dependent circuits fail closed.",
            )
            continue
        assert observation is not None
        valve_observed = _position_state(observation.value)
        mismatch = valve_observed is None or valve_observed != expected
        blocked = expected == "open" and valve_observed != "open"
        diagnostics[actuator_id] = ActuatorDiagnostic(
            actuator_id,
            ActuatorFeedbackStatus.BLOCKED if blocked else ActuatorFeedbackStatus.MISMATCH
            if mismatch
            else ActuatorFeedbackStatus.HEALTHY,
            mismatch=mismatch,
            blocked=blocked,
            expected=expected,
            observed=valve_observed,
            feedback_kind="position",
            reason=(
                f"Manual valve mismatch: expected {expected}, "
                f"observed {valve_observed or 'unknown'}."
                if mismatch
                else "Valve position feedback agrees with the requested state."
            ),
        )

    for actuator_id, pump in plant.pumps.items():
        configured = (
            ("power", pump.power_entity_id, pump.power_max_age_seconds),
            ("flow", pump.flow_entity_id, pump.flow_max_age_seconds),
            ("fault", pump.fault_entity_id, pump.fault_max_age_seconds),
        )
        configured = tuple(item for item in configured if item[1] is not None)
        if not configured:
            diagnostics[actuator_id] = ActuatorDiagnostic(
                actuator_id,
                ActuatorFeedbackStatus.NOT_CONFIGURED,
                expected=expected_states.get(actuator_id),
                reason="No pump feedback is configured.",
            )
            continue
        feedback = snapshot.actuator_feedback.get(actuator_id)
        expected = expected_states.get(actuator_id, "off")
        stale: list[str] = []
        mismatch = False
        blocked = False
        pump_observed: str | float | bool | None = None
        reasons: list[str] = []
        for kind, _entity_id, max_age in configured:
            observation = getattr(feedback, kind, None) if feedback is not None else None
            fresh, reason = _feedback_is_fresh(
                observation,
                max_age_seconds=max_age,
                now=now,
            )
            if not fresh:
                stale.append(kind)
                blocked = True
                reasons.append(f"{kind} feedback is {reason}")
                continue
            assert observation is not None
            pump_observed = observation.value
            if kind == "fault":
                fault = _feedback_boolean(observation.value)
                if fault is None:
                    blocked = True
                    reasons.append("fault feedback is unknown")
                elif fault:
                    blocked = True
                    reasons.append("pump fault is asserted")
            else:
                active = _feedback_active(observation.value)
                expected_active = expected == "on"
                if active is None:
                    mismatch = True
                    reasons.append(f"{kind} feedback is unknown")
                elif active is not expected_active:
                    mismatch = True
                    reasons.append(
                        f"manual pump mismatch: expected {kind} "
                        f"{'active' if expected_active else 'inactive'}, "
                        f"observed {'active' if active else 'inactive'}"
                    )
        if blocked:
            status = ActuatorFeedbackStatus.BLOCKED
        elif mismatch:
            status = ActuatorFeedbackStatus.MISMATCH
        else:
            status = ActuatorFeedbackStatus.HEALTHY
        diagnostics[actuator_id] = ActuatorDiagnostic(
            actuator_id,
            status,
            mismatch=mismatch,
            blocked=blocked,
            expected=expected,
            observed=pump_observed,
            feedback_kind=stale[0] if stale else None,
            stale_feedback=tuple(stale),
            reason=(
                "; ".join(reasons)
                if reasons
                else "Configured pump feedback agrees with the requested state."
            ),
        )
    return diagnostics


def safe_shutdown(
    plant: CompiledPlant,
    runtime: RuntimeState,
    now: datetime,
) -> tuple[SafeShutdownPlan, RuntimeState]:
    """Build one idempotent safe-shutdown step in explicit safe order.

    Source demand is released first, pump overrun is observed next, pumps are
    stopped only after their overrun deadline, and valves close only after all
    pumps are off.  The function is pure; an adapter may intercept or shadow
    the returned commands.
    """
    phase = runtime.safe_shutdown_phase
    source_commands: list[ActuatorCommand] = []
    if phase is SafeShutdownPhase.IDLE:
        if plant.source_selector is not None and plant.source_selector.entity_id is not None:
            source_commands.append(
                ActuatorCommand(
                    plant.source_selector.id,
                    ActuatorAction.SELECT,
                    "Release source selector before hydraulic shutdown.",
                    plant.source_selector.release_option,
                )
            )
        source_commands.extend(
            ActuatorCommand(
                f"source:{source.id}",
                ActuatorAction.TURN_OFF,
                "Release source demand before hydraulic shutdown.",
            )
            for source in sorted(plant.sources.values(), key=lambda item: item.id)
            if source.demand_entity_id is not None
        )

    pumps = dict(runtime.pumps)
    valves = dict(runtime.valves)
    commands = list(source_commands)
    next_deadline: datetime | None = None
    pump_stop_commands: list[ActuatorCommand] = []
    active_pumps = False
    for pump_id, pump in sorted(plant.pumps.items()):
        previous = pumps.get(pump_id, PumpRuntime())
        if previous.state is PumpState.STARTING:
            pumps[pump_id] = PumpRuntime(PumpState.OFF, now)
            pump_stop_commands.append(
                ActuatorCommand(
                    pump_id,
                    ActuatorAction.TURN_OFF,
                    "Stop an unconfirmed pump start before closing valves.",
                )
            )
        elif previous.state is PumpState.RUNNING:
            active_pumps = True
            if pump.overrun_seconds > 0:
                pumps[pump_id] = PumpRuntime(PumpState.OVERRUN, now)
                next_deadline = min(
                    (next_deadline or now + timedelta(seconds=pump.overrun_seconds)),
                    now + timedelta(seconds=pump.overrun_seconds),
                )
            else:
                pumps[pump_id] = PumpRuntime(PumpState.OFF, now)
                pump_stop_commands.append(
                    ActuatorCommand(
                        pump_id,
                        ActuatorAction.TURN_OFF,
                        "Stop pump after source release; no overrun is configured.",
                    )
                )
        elif previous.state is PumpState.OVERRUN:
            active_pumps = True
            deadline = (previous.changed_at or now) + timedelta(seconds=pump.overrun_seconds)
            if deadline > now:
                next_deadline = min(next_deadline or deadline, deadline)
            else:
                pumps[pump_id] = PumpRuntime(PumpState.OFF, now)
                pump_stop_commands.append(
                    ActuatorCommand(
                        pump_id,
                        ActuatorAction.TURN_OFF,
                        "Pump overrun completed during safe shutdown.",
                    )
                )

    if active_pumps and next_deadline is not None:
        plan = SafeShutdownPlan(
            SafeShutdownPhase.PUMP_OVERRUN,
            tuple(commands),
            next_deadline,
            "Source demand released; observing pump overrun before stopping pumps.",
        )
        return plan, RuntimeState(
            cooling_zone_demands={},
            zone_runtime=runtime.zone_runtime,
            valves=valves,
            pumps=pumps,
            plant_mode=PlantMode.IDLE,
            selected_source_id=None,
            safe_shutdown_phase=SafeShutdownPhase.PUMP_OVERRUN,
            safe_shutdown_started_at=runtime.safe_shutdown_started_at or now,
        )

    commands.extend(pump_stop_commands)
    if pump_stop_commands:
        plan = SafeShutdownPlan(
            SafeShutdownPhase.PUMPS_STOPPED,
            tuple(commands),
            None,
            "Source released and pumps stopped; valve closure is the next safe step.",
        )
        return plan, RuntimeState(
            cooling_zone_demands={},
            zone_runtime=runtime.zone_runtime,
            valves=valves,
            pumps=pumps,
            plant_mode=PlantMode.IDLE,
            selected_source_id=None,
            safe_shutdown_phase=SafeShutdownPhase.PUMPS_STOPPED,
            safe_shutdown_started_at=runtime.safe_shutdown_started_at or now,
        )

    valve_close_commands: list[ActuatorCommand] = []
    for valve_id, valve_runtime in sorted(valves.items()):
        if valve_runtime.state is not ValveState.CLOSED:
            valves[valve_id] = ValveRuntime(ValveState.CLOSED, now)
            valve_close_commands.append(
                ActuatorCommand(
                    valve_id,
                    ActuatorAction.CLOSE,
                    "Close valve after all pumps have stopped.",
                )
            )
    commands.extend(valve_close_commands)
    phase = SafeShutdownPhase.VALVES_CLOSED
    plan = SafeShutdownPlan(
        phase,
        tuple(commands),
        None,
        "All source demand, pumps, and valves are safely released.",
    )
    return plan, RuntimeState(
        cooling_zone_demands={},
        zone_runtime=runtime.zone_runtime,
        valves=valves,
        pumps=pumps,
        plant_mode=PlantMode.IDLE,
        selected_source_id=None,
        safe_shutdown_phase=phase,
        safe_shutdown_started_at=runtime.safe_shutdown_started_at or now,
    )


@dataclass(frozen=True, slots=True)
class _HeatingEvaluation:
    """Pure heating-demand phase output."""

    zone_demands: dict[str, bool]
    zone_runtime: dict[str, ZoneRuntime]
    zone_reasons: dict[str, str]
    zone_decisions: dict[str, ZoneDecision]


def _evaluate_heating_zones(
    plant: CompiledPlant,
    snapshot: PlantSnapshot,
    runtime: RuntimeState,
    now: datetime,
) -> _HeatingEvaluation:
    """Evaluate heating demand independently for every configured zone."""
    zone_demands: dict[str, bool] = {}
    zone_runtime: dict[str, ZoneRuntime] = {}
    zone_reasons: dict[str, str] = {}
    zone_decisions: dict[str, ZoneDecision] = {}

    for zone_id in sorted(plant.zones):
        zone = plant.zones[zone_id]
        previous = _zone_runtime(runtime, zone.id)
        aggregation = aggregate_zone_temperature_result(zone, snapshot, now=now)
        if aggregation.blocking_required_sensor_ids or aggregation.value is None:
            # Sensor safety takes precedence over any comfort timing hold.
            demand = False
            transition_at = previous.last_demand_transition_at
            if previous.demand or transition_at is None:
                transition_at = now
            next_zone_runtime = ZoneRuntime(False, transition_at)
            status = ZoneDecisionStatus.SENSOR_BLOCKED
            deadline = None
            reason = aggregation.explanation
        else:
            requested, reason = _zone_demand(
                previous=previous.demand,
                temperature=aggregation.value,
                target=zone.target_temperature,
                start_delta=zone.heating_start_delta,
                stop_delta=zone.heating_stop_delta,
            )
            next_zone_runtime, status, deadline, timing_reason = _apply_zone_timing(
                previous=previous,
                requested=requested,
                now=now,
                minimum_active_seconds=zone.minimum_active_duration_seconds,
                minimum_idle_seconds=zone.minimum_idle_duration_seconds,
            )
            demand = next_zone_runtime.demand
            if timing_reason:
                reason = timing_reason
            if aggregation.excluded_optional_sensor_ids:
                reason = f"{reason} {aggregation.explanation}"

        zone_demands[zone.id] = demand
        zone_runtime[zone.id] = next_zone_runtime
        zone_reasons[zone.id] = reason
        zone_decisions[zone.id] = ZoneDecision(
            status=status,
            demand=demand,
            aggregation=aggregation,
            explanation=reason,
            deadline=deadline,
        )

    return _HeatingEvaluation(
        zone_demands=zone_demands,
        zone_runtime=zone_runtime,
        zone_reasons=zone_reasons,
        zone_decisions=zone_decisions,
    )


@dataclass(frozen=True, slots=True)
class _CoolingEvaluation:
    """Pure cooling-demand and safety-interlock phase output."""

    zone_demands: dict[str, bool]
    zone_reasons: dict[str, str]
    zone_decisions: dict[str, ZoneDecision]
    interlocks: dict[str, SafetyInterlockResult]
    circuit_reasons: dict[str, str]


def _evaluate_cooling_zones(
    plant: CompiledPlant,
    snapshot: PlantSnapshot,
    runtime: RuntimeState,
    now: datetime,
    heating_decisions: Mapping[str, ZoneDecision],
) -> _CoolingEvaluation:
    """Evaluate cooling demand and condensation safety for every zone."""
    zone_demands: dict[str, bool] = {}
    zone_reasons: dict[str, str] = {}
    zone_decisions: dict[str, ZoneDecision] = {}
    interlocks_by_id: dict[str, SafetyInterlockResult] = {}

    for zone_id in sorted(plant.zones):
        zone = plant.zones[zone_id]
        previous_cooling = runtime.cooling_zone_demands.get(zone.id, False)
        temperature_aggregation = heating_decisions[zone.id].aggregation
        temperature = temperature_aggregation.value if temperature_aggregation else None
        humidity_aggregation = aggregate_zone_humidity_result(zone, snapshot, now=now)
        humidity = humidity_aggregation.value
        requested, reason = _cooling_zone_demand(
            previous=previous_cooling,
            temperature=temperature,
            target=zone.target_temperature,
            start_delta=zone.cooling_start_delta,
            stop_delta=zone.cooling_stop_delta,
        )
        safety_permitted, safety_reason, interlocks, dew_point, margin = _cooling_interlocks(
            plant,
            zone,
            temperature,
            humidity,
            snapshot,
            now,
        )
        for interlock in interlocks:
            interlocks_by_id[interlock.interlock_id] = interlock
        cooling_enabled_route_exists = any(
            route.enabled
            and route.zone_id == zone.id
            and plant.circuits[route.circuit_id].cooling_enabled
            for route in plant.routes
        )
        observation_blocked = (
            temperature_aggregation is None
            or temperature_aggregation.blocking_required_sensor_ids
            or temperature is None
            or humidity_aggregation.blocking_required_sensor_ids
            or humidity is None
        )
        if cooling_enabled_route_exists and observation_blocked:
            demand = False
            status = ZoneDecisionStatus.SENSOR_BLOCKED
            reason = (
                temperature_aggregation.explanation
                if temperature_aggregation is not None
                and temperature_aggregation.blocking_required_sensor_ids
                else humidity_aggregation.explanation
            )
        elif cooling_enabled_route_exists and not safety_permitted:
            demand = False
            status = ZoneDecisionStatus.SENSOR_BLOCKED
            reason = safety_reason
        elif not cooling_enabled_route_exists:
            demand = False
            status = ZoneDecisionStatus.SATISFIED
            reason = safety_reason
        else:
            demand = requested
            status = ZoneDecisionStatus.REQUESTED if demand else ZoneDecisionStatus.SATISFIED
            if not safety_permitted:
                status = ZoneDecisionStatus.SENSOR_BLOCKED
                reason = safety_reason
            elif humidity_aggregation.excluded_optional_sensor_ids:
                reason = f"{reason} {humidity_aggregation.explanation}"

        zone_demands[zone.id] = demand
        zone_reasons[zone.id] = reason
        zone_decisions[zone.id] = ZoneDecision(
            status=status,
            demand=demand,
            aggregation=temperature_aggregation,
            explanation=reason,
            humidity_aggregation=humidity_aggregation,
            dew_point=dew_point,
            condensation_margin=margin,
            interlocks=interlocks,
        )

    return _CoolingEvaluation(
        zone_demands=zone_demands,
        zone_reasons=zone_reasons,
        zone_decisions=zone_decisions,
        interlocks=interlocks_by_id,
        circuit_reasons={},
    )


def evaluate(
    plant: CompiledPlant,
    snapshot: PlantSnapshot,
    runtime: RuntimeState,
    now: datetime,
) -> Evaluation:
    """Return the next deterministic shadow runtime and virtual control plan."""
    heating = _evaluate_heating_zones(plant, snapshot, runtime, now)
    zone_demands = heating.zone_demands
    zone_runtime = heating.zone_runtime
    zone_reasons = heating.zone_reasons
    zone_decisions = heating.zone_decisions

    cooling = _evaluate_cooling_zones(plant, snapshot, runtime, now, zone_decisions)
    cooling_zone_demands = cooling.zone_demands
    cooling_zone_reasons = cooling.zone_reasons
    cooling_zone_decisions = cooling.zone_decisions
    cooling_interlocks = cooling.interlocks
    cooling_circuit_reasons = cooling.circuit_reasons

    degraded_circuits = degraded_circuit_ids(plant, snapshot.unavailable_entity_ids)
    raw_eligible_routes = resolve_delivery_routes(plant, zone_demands)
    eligible_routes = tuple(
        route for route in raw_eligible_routes if route.circuit_id not in degraded_circuits
    )
    for zone_id, demand in list(zone_demands.items()):
        if not demand:
            continue
        raw_zone_routes = tuple(route for route in raw_eligible_routes if route.zone_id == zone_id)
        healthy_zone_routes = tuple(route for route in eligible_routes if route.zone_id == zone_id)
        if raw_zone_routes and not healthy_zone_routes:
            zone = plant.zones[zone_id]
            reason = (
                f"Blocked: every delivery route for zone {zone.name} uses an unresolved "
                "actuator or feedback binding."
            )
            prior = zone_decisions[zone_id]
            previous = zone_runtime[zone_id]
            zone_demands[zone_id] = False
            zone_runtime[zone_id] = ZoneRuntime(
                False,
                now
                if previous.demand or previous.last_demand_transition_at is None
                else previous.last_demand_transition_at,
            )
            zone_reasons[zone_id] = reason
            zone_decisions[zone_id] = ZoneDecision(
                status=ZoneDecisionStatus.SENSOR_BLOCKED,
                demand=False,
                aggregation=prior.aggregation,
                explanation=reason,
            )

    raw_cooling_routes = resolve_cooling_delivery_routes(plant, cooling_zone_demands)
    requested_cooling_routes = tuple(
        route for route in raw_cooling_routes if route.circuit_id not in degraded_circuits
    )
    for zone_id, demand in list(cooling_zone_demands.items()):
        if not demand:
            continue
        raw_zone_routes = tuple(route for route in raw_cooling_routes if route.zone_id == zone_id)
        healthy_zone_routes = tuple(
            route for route in requested_cooling_routes if route.zone_id == zone_id
        )
        if raw_zone_routes and not healthy_zone_routes:
            zone = plant.zones[zone_id]
            reason = (
                f"Blocked: every cooling delivery route for zone {zone.name} uses an "
                "unresolved actuator or feedback binding."
            )
            prior = cooling_zone_decisions[zone_id]
            cooling_zone_demands[zone_id] = False
            cooling_zone_reasons[zone_id] = reason
            cooling_zone_decisions[zone_id] = ZoneDecision(
                status=ZoneDecisionStatus.SENSOR_BLOCKED,
                demand=False,
                aggregation=prior.aggregation,
                explanation=reason,
                humidity_aggregation=prior.humidity_aggregation,
                dew_point=prior.dew_point,
                condensation_margin=prior.condensation_margin,
                interlocks=prior.interlocks,
            )
    requested_mode = runtime.requested_mode
    mode_conflicts = (
        resolve_mode_conflicts(plant, eligible_routes, requested_cooling_routes)
        if requested_mode is PlantMode.AUTO
        else ()
    )
    blocked_circuit_ids = frozenset(
        circuit_id
        for conflict in mode_conflicts
        for circuit_id in conflict.cooling_circuit_ids
    )
    cooling_routes = resolve_cooling_delivery_routes(
        plant,
        cooling_zone_demands,
        blocked_circuit_ids=blocked_circuit_ids,
    )
    cooling_routes_by_zone: dict[str, tuple[DeliveryRoute, ...]] = defaultdict(tuple)
    for route in cooling_routes:
        cooling_routes_by_zone[route.zone_id] = (*cooling_routes_by_zone[route.zone_id], route)

    conflict_interlocks = {
        conflict.interlock_id: SafetyInterlockResult(
            conflict.interlock_id,
            InterlockStatus.BLOCKED,
            conflict.message,
        )
        for conflict in mode_conflicts
    }
    cooling_interlocks.update(conflict_interlocks)
    for conflict in mode_conflicts:
        for circuit_id in conflict.cooling_circuit_ids:
            prior_reason = cooling_circuit_reasons.get(circuit_id)
            cooling_circuit_reasons[circuit_id] = (
                f"{prior_reason} {conflict.message}" if prior_reason else conflict.message
            )
    for zone_id, prior in list(cooling_zone_decisions.items()):
        if not prior.demand:
            continue
        affected = tuple(
            conflict
            for conflict in mode_conflicts
            if zone_id in conflict.cooling_zone_ids
        )
        if not affected:
            continue
        conflict_reasons = " ".join(conflict.message for conflict in affected)
        if cooling_routes_by_zone.get(zone_id):
            cooling_zone_reasons[zone_id] = (
                f"{prior.explanation} {conflict_reasons} "
                "Cooling remains eligible through an independent delivery route."
            )
            cooling_zone_decisions[zone_id] = ZoneDecision(
                status=ZoneDecisionStatus.REQUESTED,
                demand=True,
                aggregation=prior.aggregation,
                explanation=cooling_zone_reasons[zone_id],
                humidity_aggregation=prior.humidity_aggregation,
                dew_point=prior.dew_point,
                condensation_margin=prior.condensation_margin,
                interlocks=(
                    *prior.interlocks,
                    *(conflict_interlocks[item.interlock_id] for item in affected),
                ),
            )
            continue
        cooling_zone_demands[zone_id] = False
        cooling_zone_reasons[zone_id] = conflict_reasons
        cooling_zone_decisions[zone_id] = ZoneDecision(
            status=ZoneDecisionStatus.SENSOR_BLOCKED,
            demand=False,
            aggregation=prior.aggregation,
            explanation=conflict_reasons,
            humidity_aggregation=prior.humidity_aggregation,
            dew_point=prior.dew_point,
            condensation_margin=prior.condensation_margin,
            interlocks=(
                *prior.interlocks,
                *(conflict_interlocks[item.interlock_id] for item in affected),
            ),
        )

    target_mode = _target_mode(
        requested_mode,
        heating_demand=any(zone_demands.values()),
        cooling_demand=any(cooling_zone_demands.values()),
    )
    changeover_phase = runtime.changeover_phase
    changeover_started_at = runtime.changeover_started_at
    equipment_active = _equipment_requires_safe_idle(plant, runtime)
    mode_change_requested = (
        changeover_phase is not ModeChangeoverPhase.IDLE
        or (
            target_mode in {PlantMode.HEATING, PlantMode.COOLING}
            and target_mode != runtime.plant_mode
            and (
                runtime.plant_mode in {PlantMode.HEATING, PlantMode.COOLING}
                or equipment_active
                and (
                    target_mode is PlantMode.COOLING
                    or requested_mode is PlantMode.HEATING
                )
            )
        )
    )
    if changeover_phase is ModeChangeoverPhase.IDLE and mode_change_requested:
        changeover_phase = ModeChangeoverPhase.SOURCE_RELEASE
        changeover_started_at = now
    elif changeover_phase is not ModeChangeoverPhase.IDLE:
        # A new request may retarget the destination, but never bypasses the
        # already active safe-idle sequence.
        changeover_started_at = changeover_started_at or now

    independent_dual_mode = (
        requested_mode is PlantMode.AUTO
        and any(zone_demands.values())
        and any(cooling_zone_demands.values())
        and not mode_conflicts
    )
    if changeover_phase is not ModeChangeoverPhase.IDLE:
        transition_reason = (
            f"Mode change to {target_mode.value} is locked until the shared plant is safely "
            f"idle ({changeover_phase.value})."
        )
        for zone_id, prior in list(zone_decisions.items()):
            if not prior.demand:
                continue
            zone_demands[zone_id] = False
            previous = zone_runtime[zone_id]
            zone_runtime[zone_id] = ZoneRuntime(
                False,
                now
                if previous.demand or previous.last_demand_transition_at is None
                else previous.last_demand_transition_at,
            )
            zone_reasons[zone_id] = f"Heating blocked: {transition_reason}"
            zone_decisions[zone_id] = ZoneDecision(
                status=ZoneDecisionStatus.MODE_BLOCKED,
                demand=False,
                aggregation=prior.aggregation,
                explanation=zone_reasons[zone_id],
                deadline=prior.deadline,
            )
        for zone_id, prior in list(cooling_zone_decisions.items()):
            if not prior.demand:
                continue
            cooling_zone_demands[zone_id] = False
            cooling_zone_reasons[zone_id] = f"Cooling blocked: {transition_reason}"
            cooling_zone_decisions[zone_id] = ZoneDecision(
                status=ZoneDecisionStatus.MODE_BLOCKED,
                demand=False,
                aggregation=prior.aggregation,
                explanation=cooling_zone_reasons[zone_id],
                humidity_aggregation=prior.humidity_aggregation,
                dew_point=prior.dew_point,
                condensation_margin=prior.condensation_margin,
                interlocks=prior.interlocks,
            )
    elif target_mode is PlantMode.HEATING and not independent_dual_mode:
        for zone_id, prior in list(cooling_zone_decisions.items()):
            if not prior.demand:
                continue
            cooling_zone_demands[zone_id] = False
            cooling_zone_reasons[zone_id] = (
                f"Cooling blocked: the plant is operating in {PlantMode.HEATING.value} mode."
            )
            cooling_zone_decisions[zone_id] = ZoneDecision(
                status=ZoneDecisionStatus.MODE_BLOCKED,
                demand=False,
                aggregation=prior.aggregation,
                explanation=cooling_zone_reasons[zone_id],
                humidity_aggregation=prior.humidity_aggregation,
                dew_point=prior.dew_point,
                condensation_margin=prior.condensation_margin,
                interlocks=prior.interlocks,
            )
    elif target_mode is PlantMode.COOLING:
        for zone_id, prior in list(zone_decisions.items()):
            if not prior.demand:
                continue
            zone_demands[zone_id] = False
            previous = zone_runtime[zone_id]
            zone_runtime[zone_id] = ZoneRuntime(
                False,
                now
                if previous.demand or previous.last_demand_transition_at is None
                else previous.last_demand_transition_at,
            )
            zone_reasons[zone_id] = (
                f"Heating blocked: the plant is operating in {PlantMode.COOLING.value} mode."
            )
            zone_decisions[zone_id] = ZoneDecision(
                status=ZoneDecisionStatus.MODE_BLOCKED,
                demand=False,
                aggregation=prior.aggregation,
                explanation=zone_reasons[zone_id],
                deadline=prior.deadline,
            )

    if changeover_phase is not ModeChangeoverPhase.IDLE:
        eligible_routes = ()
        cooling_routes = ()
    elif independent_dual_mode:
        eligible_routes = resolve_delivery_routes(plant, zone_demands)
        cooling_routes = resolve_cooling_delivery_routes(plant, cooling_zone_demands)
    elif target_mode is PlantMode.HEATING:
        eligible_routes = resolve_delivery_routes(plant, zone_demands)
        cooling_routes = ()
    elif target_mode is PlantMode.COOLING:
        eligible_routes = ()
        cooling_routes = resolve_cooling_delivery_routes(plant, cooling_zone_demands)
    else:
        eligible_routes = ()
        cooling_routes = ()

    cooling_routes_by_zone = defaultdict(tuple)
    for route in cooling_routes:
        cooling_routes_by_zone[route.zone_id] = (*cooling_routes_by_zone[route.zone_id], route)

    # A conflict can remove the last cooling route for a zone.  The public
    # zone demand map is updated above while the filtered route set remains the
    # single source of truth for actuator consumers.
    requested_circuits = {route.circuit_id for route in (*eligible_routes, *cooling_routes)}
    route_ids_by_circuit: dict[str, list[str]] = defaultdict(list)
    for route in eligible_routes:
        route_ids_by_circuit[route.circuit_id].append(route.id)
    for route in cooling_routes:
        route_ids_by_circuit[route.circuit_id].append(route.id)

    valve_consumers: dict[str, set[str]] = defaultdict(set)
    for circuit_id in sorted(requested_circuits):
        circuit = plant.circuits[circuit_id]
        for valve_id in circuit.valve_ids:
            valve_consumers[valve_id].add(circuit_id)

    cooling_valve_consumers: dict[str, set[str]] = defaultdict(set)
    for route in cooling_routes:
        circuit = plant.circuits[route.circuit_id]
        cooling_circuit_reasons[circuit.id] = (
            f"Cooling route {route.id} requested circuit {circuit.name}."
        )
        for valve_id in circuit.valve_ids:
            cooling_valve_consumers[valve_id].add(circuit.id)

    feedback_expected: dict[str, str] = {
        valve_id: "open" if valve_consumers.get(valve_id) else "closed"
        for valve_id in plant.valves
    }
    feedback_diagnostics = _feedback_diagnostics(plant, snapshot, now, feedback_expected)
    blocked_valves = {
        actuator_id
        for actuator_id, diagnostic in feedback_diagnostics.items()
        if actuator_id in plant.valves and diagnostic.blocked
    }

    valves: dict[str, ValveRuntime] = {}
    valve_ready: set[str] = set()
    commands: list[ActuatorCommand] = []
    actuator_reasons: dict[str, str] = {}
    for valve_id in sorted(plant.valves):
        valve = plant.valves[valve_id]
        consumers = valve_consumers.get(valve.id, set())
        previous = runtime.valves.get(valve.id, ValveRuntime())
        if valve.entity_id in snapshot.unavailable_entity_ids:
            current = ValveRuntime(ValveState.CLOSED, now, False)
            actuator_reasons[valve.id] = (
                "Blocked: the configured valve actuator binding is unresolved."
            )
        elif consumers:
            position_feedback = feedback_diagnostics[valve.id]
            observed_open = position_feedback.observed == "open"
            if valve.position_entity_id is not None and observed_open:
                current = ValveRuntime(ValveState.OPEN, now, True)
                actuator_reasons[valve.id] = (
                    "Position feedback confirms the valve is open for active circuit consumers."
                )
            elif previous.state is ValveState.CLOSED:
                current = ValveRuntime(ValveState.OPENING, now, False)
                commands.append(
                    ActuatorCommand(
                        valve.id,
                        ActuatorAction.OPEN,
                        "A requesting circuit needs this valve.",
                    )
                )
                actuator_reasons[valve.id] = "Opening for active circuit consumers."
            elif previous.state is ValveState.OPENING and (
                previous.is_ready
                or _elapsed(now, previous.changed_at)
                >= timedelta(seconds=valve.opening_time_seconds)
            ):
                current = ValveRuntime(ValveState.OPEN, previous.changed_at, True)
                actuator_reasons[valve.id] = "Open-delay elapsed; valve is virtually ready."
            elif previous.state is ValveState.OPEN and not previous.is_ready:
                if _elapsed(now, previous.changed_at) >= timedelta(
                    seconds=valve.opening_time_seconds
                ):
                    current = ValveRuntime(ValveState.OPEN, previous.changed_at, True)
                    actuator_reasons[valve.id] = "Configured valve readiness time elapsed."
                else:
                    current = ValveRuntime(
                        ValveState.OPENING,
                        previous.changed_at or now,
                        False,
                    )
                    actuator_reasons[valve.id] = "Waiting for valve readiness feedback or timer."
            else:
                current = previous
                actuator_reasons[valve.id] = "Held open for active circuit consumers."
        else:
            current = previous
            actuator_reasons[valve.id] = "Idle because its consumer set is empty."
        valves[valve.id] = current
        if current.state is ValveState.OPEN and valve.id not in blocked_valves:
            valve_ready.add(valve.id)

    ready_circuits = {
        circuit_id
        for circuit_id in sorted(requested_circuits)
        if all(valve_id in valve_ready for valve_id in plant.circuits[circuit_id].valve_ids)
    }
    pump_consumers: dict[str, set[str]] = defaultdict(set)
    for circuit_id in sorted(ready_circuits):
        pump_consumers[plant.circuits[circuit_id].pump_id].add(circuit_id)

    feedback_expected.update(
        {
            pump_id: "on" if pump_consumers.get(pump_id) else "off"
            for pump_id in plant.pumps
        }
    )
    feedback_diagnostics = _feedback_diagnostics(plant, snapshot, now, feedback_expected)
    pump_dependent_requested = {
        pump_id: {
            circuit_id
            for circuit_id in requested_circuits
            if plant.circuits[circuit_id].pump_id == pump_id
        }
        for pump_id in plant.pumps
    }
    blocked_pumps = {
        pump_id
        for pump_id, diagnostic in feedback_diagnostics.items()
        if pump_id in plant.pumps and diagnostic.blocked and pump_dependent_requested[pump_id]
    }
    blocked_pump_circuits = {
        circuit_id
        for pump_id in blocked_pumps
        for circuit_id in pump_dependent_requested[pump_id]
    }
    for pump_id in blocked_pumps:
        pump_consumers[pump_id].difference_update(blocked_pump_circuits)
    ready_circuits.difference_update(blocked_pump_circuits)

    cooling_pump_consumers: dict[str, set[str]] = defaultdict(set)
    ready_cooling_circuits = {
        route.circuit_id
        for route in cooling_routes
        if route.circuit_id in ready_circuits
    }
    for circuit_id in sorted(ready_cooling_circuits):
        cooling_pump_consumers[plant.circuits[circuit_id].pump_id].add(circuit_id)

    cooling_actuator_ids = {
        valve_id
        for route in cooling_routes
        for valve_id in plant.circuits[route.circuit_id].valve_ids
    }
    cooling_actuator_ids.update(
        plant.circuits[circuit_id].pump_id for circuit_id in ready_cooling_circuits
    )
    if (
        target_mode is PlantMode.COOLING
        or runtime.plant_mode is PlantMode.COOLING
        or runtime.changeover_target_mode is PlantMode.COOLING
    ):
        cooling_circuit_ids = {
            circuit.id for circuit in plant.circuits.values() if circuit.cooling_enabled
        }
        cooling_actuator_ids.update(
            valve_id
            for circuit_id in cooling_circuit_ids
            for valve_id in plant.circuits[circuit_id].valve_ids
        )
        cooling_actuator_ids.update(
            plant.circuits[circuit_id].pump_id for circuit_id in cooling_circuit_ids
        )

    pumps: dict[str, PumpRuntime] = {}
    for pump_id in sorted(plant.pumps):
        pump = plant.pumps[pump_id]
        consumers = pump_consumers.get(pump.id, set())
        previous = runtime.pumps.get(pump.id, PumpRuntime())
        if pump.entity_id in snapshot.unavailable_entity_ids:
            current = PumpRuntime(PumpState.OFF, now)
            actuator_reasons[pump.id] = (
                "Blocked: the configured pump actuator binding is unresolved."
            )
        elif consumers:
            if previous.state is PumpState.STARTING:
                current = previous
                actuator_reasons[pump.id] = (
                    "Waiting for pump state feedback after the start command."
                )
            elif previous.state is not PumpState.RUNNING:
                current = PumpRuntime(PumpState.RUNNING, now)
                commands.append(
                    ActuatorCommand(
                        pump.id,
                        ActuatorAction.TURN_ON,
                        "A ready circuit needs this pump.",
                    )
                )
                actuator_reasons[pump.id] = "Running for ready circuit consumers."
            else:
                current = previous
                actuator_reasons[pump.id] = "Running for ready circuit consumers."
        elif previous.state is PumpState.STARTING:
            current = PumpRuntime(PumpState.OFF, now)
            commands.append(
                ActuatorCommand(
                    pump.id,
                    ActuatorAction.TURN_OFF,
                    "Stop a pump whose start was not observed before demand released.",
                )
            )
            actuator_reasons[pump.id] = "Stopping an unconfirmed pump start."
        elif previous.state is PumpState.RUNNING:
            current = PumpRuntime(PumpState.OVERRUN, now)
            actuator_reasons[pump.id] = "Overrunning after the final ready circuit released demand."
        elif previous.state is PumpState.OVERRUN and _elapsed(now, previous.changed_at) < timedelta(
            seconds=pump.overrun_seconds
        ):
            current = previous
            actuator_reasons[pump.id] = "Overrun is still protecting the hydraulic circuit."
        elif previous.state is PumpState.OVERRUN:
            current = PumpRuntime(PumpState.OFF, now)
            commands.append(
                ActuatorCommand(
                    pump.id,
                    ActuatorAction.TURN_OFF,
                    "Pump overrun has completed.",
                )
            )
            actuator_reasons[pump.id] = "Idle because no ready circuit requires this pump."
        else:
            current = previous
            actuator_reasons[pump.id] = "Idle because no ready circuit requires this pump."
        pumps[pump.id] = current

    overrun_pumps = {pump_id for pump_id, pump in pumps.items() if pump.state is PumpState.OVERRUN}
    for valve_id in sorted(plant.valves):
        valve = valves[valve_id]
        protected_by_overrun = any(
            circuit.pump_id in overrun_pumps and valve_id in circuit.valve_ids
            for circuit in plant.circuits.values()
        )
        pumps_were_active = any(
            runtime.pumps.get(pump_id, PumpRuntime()).state is not PumpState.OFF
            for pump_id in plant.pumps
        )
        if (
            valve_consumers.get(valve_id)
            or protected_by_overrun
            or (
                changeover_phase is not ModeChangeoverPhase.IDLE
                and pumps_were_active
            )
        ):
            continue
        if valve.state is not ValveState.CLOSED:
            valves[valve_id] = ValveRuntime(ValveState.CLOSED, now, False)
            commands.append(
                ActuatorCommand(
                    valve_id,
                    ActuatorAction.CLOSE,
                    "No active consumer remains after pump overrun.",
                )
            )
            actuator_reasons[valve_id] = "Closing because its consumer set is empty."

    if changeover_phase is ModeChangeoverPhase.SOURCE_RELEASE:
        # Releasing source demand is idempotent and remains visible after a
        # restart that restored this phase.
        commands[:0] = _source_release_commands(plant)
    next_changeover_phase = _advance_changeover_phase(
        changeover_phase,
        pumps=pumps,
        valves=valves,
    )
    changeover_deadline: datetime | None = None
    if next_changeover_phase is ModeChangeoverPhase.PUMP_OVERRUN:
        deadlines = [
            (pump.changed_at or now) + timedelta(seconds=plant.pumps[pump_id].overrun_seconds)
            for pump_id, pump in pumps.items()
            if pump.state is PumpState.OVERRUN
        ]
        if deadlines:
            changeover_deadline = min(deadlines)
    elif next_changeover_phase is ModeChangeoverPhase.PUMPS_STOPPING:
        changeover_deadline = now
    mode_explanation = (
        (
            f"Mode change to {target_mode.value} is locked until the shared plant is safely "
            f"idle ({next_changeover_phase.value})."
        )
        if next_changeover_phase is not ModeChangeoverPhase.IDLE
        else f"Plant mode is {target_mode.value}; the shared hydraulic path is safe to use."
    )
    active_mode = (
        target_mode
        if next_changeover_phase is ModeChangeoverPhase.IDLE
        and (
            (target_mode is PlantMode.HEATING and bool(eligible_routes))
            or (target_mode is PlantMode.COOLING and bool(cooling_routes))
        )
        else PlantMode.IDLE
    )
    changeover_reason = (
        mode_explanation if next_changeover_phase is not ModeChangeoverPhase.IDLE else ""
    )

    circuit_reasons = {
        circuit_id: (
            "Blocked: configured actuator or feedback binding is unresolved."
            if circuit_id in degraded_circuits
            else "Blocked: "
            + feedback_diagnostics[plant.circuits[circuit_id].pump_id].reason
            if circuit_id in blocked_pump_circuits
            else "Blocked: required valve feedback is unavailable or unsafe."
            if circuit_id in requested_circuits
            and any(valve_id in blocked_valves for valve_id in plant.circuits[circuit_id].valve_ids)
            else "Ready: eligible delivery route "
            + ", ".join(route_ids_by_circuit[circuit_id])
            + " has valve-ready demand."
            if circuit_id in ready_circuits
            else "Waiting for valve readiness after eligible delivery route "
            + ", ".join(route_ids_by_circuit[circuit_id])
            + " requested this circuit."
            if circuit_id in requested_circuits
            else "Idle: no eligible delivery route currently requests this circuit."
        )
        for circuit_id in sorted(plant.circuits)
    }
    source_recommendation = recommend_source(
        plant,
        snapshot,
        runtime,
        now,
        active_heating=active_mode is PlantMode.HEATING,
    )
    next_plant_mode = (
        PlantMode.HEATING
        if any(zone_demands.values())
        else PlantMode.COOLING
        if any(cooling_zone_demands.values())
        else PlantMode.IDLE
    )
    hydraulic_safe, hydraulic_reason = _source_hydraulic_safety(
        plant,
        runtime,
        valves,
        pumps,
        tuple(commands),
        next_plant_mode,
    )
    heating_ready_circuits = {
        route.circuit_id for route in eligible_routes if route.circuit_id in ready_circuits
    }
    demand_permitted, demand_reason = _source_demand_permit(
        plant,
        heating_ready_circuits,
        pump_consumers,
        pumps,
        next_plant_mode,
    )
    source_selection_runtime, source_selection_commands, source_selection = (
        _advance_source_selection(
            plant,
            snapshot,
            runtime,
            now,
            source_recommendation,
            hydraulic_safe=hydraulic_safe,
            hydraulic_reason=hydraulic_reason,
            demand_permitted=demand_permitted,
            demand_reason=demand_reason,
        )
    )
    commands.extend(source_selection_commands)
    selector_execution_configured = plant.source_selector is not None or any(
        source.demand_entity_id is not None for source in plant.sources.values()
    )
    selected_source_id = (
        source_selection_runtime.active_source_id
        if selector_execution_configured
        else source_recommendation.source_id
        if source_recommendation is not None
        else None
    )
    direct_source_demand_ids = frozenset(
        command.actuator_id.removeprefix("source:")
        for command in source_selection_commands
        if command.actuator_id.startswith("source:")
        and command.action is ActuatorAction.TURN_ON
    )
    reported_active_source_id = (
        source_selection_runtime.active_source_id
        or next(iter(sorted(direct_source_demand_ids)), None)
    )
    if selector_execution_configured and direct_source_demand_ids:
        selected_source_id = reported_active_source_id
    if source_selection is not None:
        dwell_remaining = 0.0
        if (
            plant.source_selector is not None
            and source_selection.phase is SourceSelectionPhase.MINIMUM_DWELL
            and source_selection_runtime.last_selected_at is not None
        ):
            dwell_remaining = max(
                0.0,
                plant.source_selector.minimum_dwell_seconds
                - _elapsed(now, source_selection_runtime.last_selected_at).total_seconds(),
            )
        source_selection = replace(
            source_selection,
            dwell_remaining_seconds=dwell_remaining,
        )
    source_diagnostics: dict[str, SourceDiagnostic] = {}
    for source_id, source in sorted(plant.sources.items()):
        source_available: bool | None = True
        if source.availability_entity_id is not None:
            source_available = snapshot.source_availability.get(source.id)
            if source_available is None:
                source_available = snapshot.source_availability.get(source.availability_entity_id)
        eligible, eligibility_reason = _source_eligibility(source, snapshot, runtime, now)
        recommended = source_recommendation is not None and (
            source_recommendation.source_id == source_id
        )
        active = reported_active_source_id == source_id
        demand_requested = bool(
            source.demand_entity_id is not None
            and (
                source_id in direct_source_demand_ids
                or (active and recommended and demand_permitted)
            )
        )
        source_permitted = bool(
            source.demand_entity_id is not None
            and recommended
            and eligible
            and demand_permitted
            and (active or source_id in direct_source_demand_ids)
        )
        if source.demand_entity_id is None:
            reason = "No heat-pump demand output is configured for this source."
        elif not eligible:
            reason = f"Blocked: {eligibility_reason}."
        elif not recommended:
            reason = "Blocked: this source is not the deterministic active recommendation."
        elif demand_requested:
            reason = demand_reason
        else:
            reason = demand_reason
        source_diagnostics[source_id] = SourceDiagnostic(
            source_id=source_id,
            available=source_available,
            eligible=eligible,
            recommended=recommended,
            active=active,
            demand_requested=demand_requested,
            demand_permitted=source_permitted,
            blocked=source.demand_entity_id is not None and not demand_requested,
            reason=reason,
        )
    source_selection_actuator_ids = frozenset(
        command.actuator_id for command in source_selection_commands
    )
    return Evaluation(
        next_runtime=RuntimeState(
            cooling_zone_demands=cooling_zone_demands,
            zone_runtime=zone_runtime,
            valves=valves,
            pumps=pumps,
            plant_mode=active_mode,
            requested_mode=requested_mode,
            selected_source_id=selected_source_id if active_mode is PlantMode.HEATING else None,
            source_selection=source_selection_runtime,
            changeover_phase=next_changeover_phase,
            changeover_target_mode=(
                target_mode if next_changeover_phase is not ModeChangeoverPhase.IDLE else None
            ),
            changeover_started_at=(
                changeover_started_at
                if next_changeover_phase is not ModeChangeoverPhase.IDLE
                else None
            ),
            changeover_deadline=changeover_deadline,
            changeover_reason=changeover_reason,
        ),
        control_plan=ControlPlan(
            commands=tuple(commands),
            valve_consumers={
                key: frozenset(value) for key, value in sorted(valve_consumers.items())
            },
            pump_consumers={key: frozenset(value) for key, value in sorted(pump_consumers.items())},
            plant_mode=active_mode,
            requested_mode=requested_mode,
            changeover_phase=next_changeover_phase,
            changeover_target_mode=(
                target_mode if next_changeover_phase is not ModeChangeoverPhase.IDLE else None
            ),
            changeover_deadline=changeover_deadline,
            mode_explanation=mode_explanation,
            source_recommendation=source_recommendation,
            cooling_zone_demands=cooling_zone_demands,
            cooling_valve_consumers={
                key: frozenset(value)
                for key, value in sorted(cooling_valve_consumers.items())
            },
            cooling_pump_consumers={
                key: frozenset(value)
                for key, value in sorted(cooling_pump_consumers.items())
            },
            cooling_actuator_ids=frozenset(sorted(cooling_actuator_ids)),
            mode_conflicts=mode_conflicts,
            interlocks=cooling_interlocks,
            source_selection=source_selection,
            source_selection_actuator_ids=source_selection_actuator_ids,
        ),
        diagnostics=ControllerDiagnostics(
            zone_reasons=zone_reasons,
            circuit_reasons=circuit_reasons,
            actuator_reasons=actuator_reasons,
            zone_decisions=zone_decisions,
            source_recommendation=source_recommendation,
            source_diagnostics=source_diagnostics,
            cooling_zone_decisions=cooling_zone_decisions,
            interlocks=cooling_interlocks,
            cooling_circuit_reasons=cooling_circuit_reasons,
            cooling_zone_reasons=cooling_zone_reasons,
            mode_conflicts=mode_conflicts,
            actuator_diagnostics=feedback_diagnostics,
            source_selection=source_selection,
            requested_mode=requested_mode,
            active_mode=active_mode,
            changeover_phase=next_changeover_phase,
            changeover_target_mode=(
                target_mode if next_changeover_phase is not ModeChangeoverPhase.IDLE else None
            ),
            changeover_deadline=changeover_deadline,
            mode_explanation=mode_explanation,
        ),
    )
