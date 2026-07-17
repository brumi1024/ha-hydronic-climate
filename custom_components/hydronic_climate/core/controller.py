"""Deterministic shadow-mode controller for the initial hydronic vertical slice."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from math import isfinite
from statistics import median

from .model import (
    ActuatorCommand,
    CompiledPlant,
    ControllerDiagnostics,
    ControlPlan,
    DeliveryRoute,
    Evaluation,
    PlantSnapshot,
    PumpRuntime,
    PumpState,
    RuntimeState,
    TemperatureAggregation,
    ValveRuntime,
    ValveState,
    Zone,
)


def _zone_demand(
    *,
    previous: bool,
    temperature: float | None,
    target: float,
    start_delta: float,
    stop_delta: float,
) -> tuple[bool, str]:
    """Apply heating hysteresis to one zone without side effects."""
    if temperature is None:
        return False, "Blocked: the required temperature sensor has no usable reading."
    if temperature <= target - start_delta:
        return True, f"Heating requested: {temperature:.1f} is below {target - start_delta:.1f}."
    if temperature >= target + stop_delta:
        return False, f"Satisfied: {temperature:.1f} is at or above {target + stop_delta:.1f}."
    if previous:
        return True, "Heating remains requested inside the hysteresis band."
    return False, "Heating remains idle inside the hysteresis band."


def _elapsed(now: datetime, changed_at: datetime | None) -> timedelta:
    """Return a conservative zero duration for state restored without a timestamp."""
    return now - changed_at if changed_at is not None else timedelta(0)


def aggregate_zone_temperature(zone: Zone, snapshot: PlantSnapshot) -> float | None:
    """Combine all required readings according to the zone's policy."""
    readings: list[float] = []
    for sensor_id in zone.temperature_sensors:
        observation = snapshot.temperatures.get(sensor_id)
        if observation is None or observation.value is None or not isfinite(observation.value):
            return None
        readings.append(observation.value)
    if not readings:
        return None
    if zone.aggregation is TemperatureAggregation.MEAN:
        return sum(readings) / len(readings)
    if zone.aggregation is TemperatureAggregation.MEDIAN:
        return float(median(readings))
    if zone.aggregation is TemperatureAggregation.MINIMUM:
        return min(readings)
    if zone.aggregation is TemperatureAggregation.MAXIMUM:
        return max(readings)
    if zone.aggregation is TemperatureAggregation.WEIGHTED_MEAN:
        weights = [
            zone.temperature_sensor_weights.get(sensor_id, 1.0)
            for sensor_id in zone.temperature_sensors
        ]
        if any(not isfinite(weight) or weight <= 0 for weight in weights):
            return None
        return sum(
            reading * weight for reading, weight in zip(readings, weights, strict=True)
        ) / sum(weights)


def mean_zone_temperature(
    sensor_ids: tuple[str, ...], snapshot: PlantSnapshot
) -> float | None:
    """Average readings for callers using the legacy helper signature."""
    return aggregate_zone_temperature(Zone("legacy", "Legacy", 0.0, sensor_ids), snapshot)


def resolve_delivery_routes(
    plant: CompiledPlant, zone_demands: dict[str, bool]
) -> tuple[DeliveryRoute, ...]:
    """Return eligible routes under the deterministic heating-only any-demand policy."""
    return tuple(
        route
        for route in plant.routes
        if zone_demands.get(route.zone_id, False)
    )


def evaluate(
    plant: CompiledPlant,
    snapshot: PlantSnapshot,
    runtime: RuntimeState,
    now: datetime,
) -> Evaluation:
    """Return the next shadow runtime state and idempotent desired control plan."""
    zone_demands: dict[str, bool] = {}
    zone_reasons: dict[str, str] = {}
    for zone in plant.zones.values():
        temperature = aggregate_zone_temperature(zone, snapshot)
        demand, reason = _zone_demand(
            previous=runtime.zone_demands.get(zone.id, False),
            temperature=temperature,
            target=zone.target_temperature,
            start_delta=zone.heating_start_delta,
            stop_delta=zone.heating_stop_delta,
        )
        zone_demands[zone.id] = demand
        zone_reasons[zone.id] = reason

    eligible_routes = resolve_delivery_routes(plant, zone_demands)
    requested_circuits = {route.circuit_id for route in eligible_routes}
    route_ids_by_circuit: dict[str, list[str]] = defaultdict(list)
    for route in eligible_routes:
        route_ids_by_circuit[route.circuit_id].append(route.id)
    valve_consumers: dict[str, set[str]] = defaultdict(set)
    for circuit_id in requested_circuits:
        circuit = plant.circuits[circuit_id]
        for valve_id in circuit.valve_ids:
            valve_consumers[valve_id].add(circuit_id)

    valves: dict[str, ValveRuntime] = dict(runtime.valves)
    valve_ready: set[str] = set()
    commands: list[ActuatorCommand] = []
    actuator_reasons: dict[str, str] = {}
    for valve in plant.valves.values():
        consumers = valve_consumers.get(valve.id, set())
        previous = valves.get(valve.id, ValveRuntime())
        if consumers:
            if previous.state is ValveState.CLOSED:
                valves[valve.id] = ValveRuntime(ValveState.OPENING, now)
                commands.append(
                    ActuatorCommand(
                        valve.id,
                        "open",
                        "A requesting circuit needs this valve.",
                    )
                )
                actuator_reasons[valve.id] = "Opening for active circuit consumers."
            elif previous.state is ValveState.OPENING and _elapsed(
                now, previous.changed_at
            ) >= timedelta(seconds=valve.opening_time_seconds):
                valves[valve.id] = ValveRuntime(ValveState.OPEN, previous.changed_at)
                actuator_reasons[valve.id] = "Open-delay elapsed; valve is virtually ready."
            else:
                valves[valve.id] = previous
                actuator_reasons[valve.id] = "Held open for active circuit consumers."
        else:
            valves[valve.id] = previous
        if valves[valve.id].state is ValveState.OPEN:
            valve_ready.add(valve.id)

    ready_circuits = {
        circuit_id
        for circuit_id in requested_circuits
        if all(valve_id in valve_ready for valve_id in plant.circuits[circuit_id].valve_ids)
    }
    pump_consumers: dict[str, set[str]] = defaultdict(set)
    for circuit_id in ready_circuits:
        pump_consumers[plant.circuits[circuit_id].pump_id].add(circuit_id)

    pumps: dict[str, PumpRuntime] = dict(runtime.pumps)
    for pump in plant.pumps.values():
        consumers = pump_consumers.get(pump.id, set())
        previous = pumps.get(pump.id, PumpRuntime())
        if consumers:
            if previous.state is not PumpState.RUNNING:
                pumps[pump.id] = PumpRuntime(PumpState.RUNNING, now)
                commands.append(
                    ActuatorCommand(pump.id, "turn_on", "A ready circuit needs this pump.")
                )
            else:
                pumps[pump.id] = previous
            actuator_reasons[pump.id] = "Running for ready circuit consumers."
            continue
        if previous.state is PumpState.RUNNING:
            pumps[pump.id] = PumpRuntime(PumpState.OVERRUN, now)
            actuator_reasons[pump.id] = "Overrunning after the final ready circuit released demand."
            continue
        if previous.state is PumpState.OVERRUN and _elapsed(now, previous.changed_at) < timedelta(
            seconds=pump.overrun_seconds
        ):
            pumps[pump.id] = previous
            actuator_reasons[pump.id] = "Overrun is still protecting the hydraulic circuit."
            continue
        if previous.state is PumpState.OVERRUN:
            pumps[pump.id] = PumpRuntime(PumpState.OFF, now)
            commands.append(ActuatorCommand(pump.id, "turn_off", "Pump overrun has completed."))
        else:
            pumps[pump.id] = previous
        actuator_reasons[pump.id] = "Idle because no ready circuit requires this pump."

    overrun_pumps = {pump_id for pump_id, pump in pumps.items() if pump.state is PumpState.OVERRUN}
    for valve_id in plant.valves:
        valve = valves.get(valve_id, ValveRuntime())
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
                    "close",
                    "No active consumer remains after pump overrun.",
                )
            )
            actuator_reasons[valve_id] = "Closing because its consumer set is empty."

    circuit_reasons = {
        circuit.id: (
            "Ready: eligible delivery route "
            + ", ".join(route_ids_by_circuit[circuit.id])
            + " has valve-ready demand."
            if circuit.id in ready_circuits
            else "Waiting for valve readiness after eligible delivery route "
            + ", ".join(route_ids_by_circuit[circuit.id])
            + " requested this circuit."
            if circuit.id in requested_circuits
            else "Idle: no eligible delivery route currently requests this circuit."
        )
        for circuit in plant.circuits.values()
    }
    return Evaluation(
        next_runtime=RuntimeState(zone_demands=zone_demands, valves=valves, pumps=pumps),
        control_plan=ControlPlan(
            commands=tuple(commands),
            valve_consumers={key: frozenset(value) for key, value in valve_consumers.items()},
            pump_consumers={key: frozenset(value) for key, value in pump_consumers.items()},
        ),
        diagnostics=ControllerDiagnostics(zone_reasons, circuit_reasons, actuator_reasons),
    )
