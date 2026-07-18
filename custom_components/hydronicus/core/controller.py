"""Deterministic, heating-only shadow controller for Hydronicus."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta
from math import fsum, isfinite
from statistics import median

from .model import (
    ActuatorAction,
    ActuatorCommand,
    AggregationResult,
    CompiledPlant,
    ControllerDiagnostics,
    ControlPlan,
    DeliveryRoute,
    Evaluation,
    PlantMode,
    PlantSnapshot,
    PumpRuntime,
    PumpState,
    RuntimeState,
    Source,
    SourceKind,
    SourceRecommendation,
    TemperatureAggregation,
    TemperatureObservation,
    ValveRuntime,
    ValveState,
    Zone,
    ZoneDecision,
    ZoneDecisionStatus,
    ZoneRuntime,
)


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


def mean_zone_temperature(sensor_ids: tuple[str, ...], snapshot: PlantSnapshot) -> float | None:
    """Average readings for callers using the legacy helper signature."""
    zone = Zone("legacy", "Legacy", 0.0, sensor_ids)
    result = aggregate_zone_temperature_result(zone, snapshot)
    return result.value


def _zone_runtime(runtime: RuntimeState, zone_id: str) -> ZoneRuntime:
    """Read new timing state while accepting the old demand-only state."""
    if zone_id in runtime.zone_runtime:
        return runtime.zone_runtime[zone_id]
    return ZoneRuntime(demand=runtime.zone_demands.get(zone_id, False))


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


def evaluate(
    plant: CompiledPlant,
    snapshot: PlantSnapshot,
    runtime: RuntimeState,
    now: datetime,
) -> Evaluation:
    """Return the next deterministic shadow runtime and virtual control plan."""
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

    eligible_routes = resolve_delivery_routes(plant, zone_demands)
    requested_circuits = {route.circuit_id for route in eligible_routes}
    route_ids_by_circuit: dict[str, list[str]] = defaultdict(list)
    for route in eligible_routes:
        route_ids_by_circuit[route.circuit_id].append(route.id)

    valve_consumers: dict[str, set[str]] = defaultdict(set)
    for circuit_id in sorted(requested_circuits):
        circuit = plant.circuits[circuit_id]
        for valve_id in circuit.valve_ids:
            valve_consumers[valve_id].add(circuit_id)

    valves: dict[str, ValveRuntime] = {}
    valve_ready: set[str] = set()
    commands: list[ActuatorCommand] = []
    actuator_reasons: dict[str, str] = {}
    for valve_id in sorted(plant.valves):
        valve = plant.valves[valve_id]
        consumers = valve_consumers.get(valve.id, set())
        previous = runtime.valves.get(valve.id, ValveRuntime())
        if consumers:
            if previous.state is ValveState.CLOSED:
                current = ValveRuntime(ValveState.OPENING, now)
                commands.append(
                    ActuatorCommand(
                        valve.id,
                        ActuatorAction.OPEN,
                        "A requesting circuit needs this valve.",
                    )
                )
                actuator_reasons[valve.id] = "Opening for active circuit consumers."
            elif previous.state is ValveState.OPENING and _elapsed(
                now, previous.changed_at
            ) >= timedelta(seconds=valve.opening_time_seconds):
                current = ValveRuntime(ValveState.OPEN, previous.changed_at)
                actuator_reasons[valve.id] = "Open-delay elapsed; valve is virtually ready."
            else:
                current = previous
                actuator_reasons[valve.id] = "Held open for active circuit consumers."
        else:
            current = previous
            actuator_reasons[valve.id] = "Idle because its consumer set is empty."
        valves[valve.id] = current
        if current.state is ValveState.OPEN:
            valve_ready.add(valve.id)

    ready_circuits = {
        circuit_id
        for circuit_id in sorted(requested_circuits)
        if all(valve_id in valve_ready for valve_id in plant.circuits[circuit_id].valve_ids)
    }
    pump_consumers: dict[str, set[str]] = defaultdict(set)
    for circuit_id in sorted(ready_circuits):
        pump_consumers[plant.circuits[circuit_id].pump_id].add(circuit_id)

    pumps: dict[str, PumpRuntime] = {}
    for pump_id in sorted(plant.pumps):
        pump = plant.pumps[pump_id]
        consumers = pump_consumers.get(pump.id, set())
        previous = runtime.pumps.get(pump.id, PumpRuntime())
        if consumers:
            if previous.state is not PumpState.RUNNING:
                current = PumpRuntime(PumpState.RUNNING, now)
                commands.append(
                    ActuatorCommand(
                        pump.id,
                        ActuatorAction.TURN_ON,
                        "A ready circuit needs this pump.",
                    )
                )
            else:
                current = previous
            actuator_reasons[pump.id] = "Running for ready circuit consumers."
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
        if valve_consumers.get(valve_id) or protected_by_overrun:
            continue
        if valve.state is not ValveState.CLOSED:
            valves[valve_id] = ValveRuntime(ValveState.CLOSED, now)
            commands.append(
                ActuatorCommand(
                    valve_id,
                    ActuatorAction.CLOSE,
                    "No active consumer remains after pump overrun.",
                )
            )
            actuator_reasons[valve_id] = "Closing because its consumer set is empty."

    circuit_reasons = {
        circuit_id: (
            "Ready: eligible delivery route "
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
        active_heating=any(zone_demands.values()),
    )
    selected_source_id = (
        source_recommendation.source_id if source_recommendation is not None else None
    )
    return Evaluation(
        next_runtime=RuntimeState(
            zone_demands=zone_demands,
            zone_runtime=zone_runtime,
            valves=valves,
            pumps=pumps,
            plant_mode=PlantMode.HEATING if any(zone_demands.values()) else PlantMode.IDLE,
            selected_source_id=selected_source_id,
        ),
        control_plan=ControlPlan(
            commands=tuple(commands),
            valve_consumers={
                key: frozenset(value) for key, value in sorted(valve_consumers.items())
            },
            pump_consumers={key: frozenset(value) for key, value in sorted(pump_consumers.items())},
            plant_mode=PlantMode.HEATING if any(zone_demands.values()) else PlantMode.IDLE,
            source_recommendation=source_recommendation,
        ),
        diagnostics=ControllerDiagnostics(
            zone_reasons=zone_reasons,
            circuit_reasons=circuit_reasons,
            actuator_reasons=actuator_reasons,
            zone_decisions=zone_decisions,
            source_recommendation=source_recommendation,
        ),
    )
