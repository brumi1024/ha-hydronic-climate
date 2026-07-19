"""Versioned, redacted presentation data for the Hydronicus Plant card."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from homeassistant.helpers import entity_registry as entity_registry_helper

from .const import DOMAIN
from .core.executor import (
    ActuatorExecutionFailure,
    ActuatorOperation,
)
from .core.model import (
    ModeChangeoverPhase,
    PumpState,
    SafeShutdownPhase,
    ZoneDecision,
    ZoneDecisionStatus,
)

PRESENTATION_SCHEMA_VERSION = 1

_SEVERITY_ORDER = {"critical": 0, "error": 1, "warning": 2, "info": 3}


def presentation_entity_ids(
    hass: Any,
    entry_id: str,
    plant_id: str,
    zone_ids: Mapping[str, str] | tuple[str, ...] = (),
) -> dict[str, str]:
    """Return only Hydronicus-owned entity IDs needed by the presentation."""
    registry = entity_registry_helper.async_get(hass)
    result: dict[str, str] = {}

    def find(domain: str, unique_id: str, key: str) -> None:
        entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
        if entity_id is not None:
            result[key] = entity_id

    find("select", f"{plant_id}_requested_mode", "requested_mode")
    find("button", f"{plant_id}_safe_shutdown", "safe_shutdown")
    for zone_id in zone_ids:
        find("climate", f"{plant_id}_{zone_id}_climate", f"climate:{zone_id}")
    # The config entry argument makes the ownership boundary explicit even
    # though the unique IDs above are already Plant-scoped.
    del entry_id
    return result


def build_plant_presentation(
    runtime: Any,
    *,
    control_entities: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Build one JSON-compatible Plant snapshot from structured runtime state.

    This function deliberately whitelists public fields instead of serializing
    runtime or diagnostics objects wholesale.  Physical entity bindings never
    cross this boundary.
    """
    evaluation = runtime.evaluation
    diagnostics = evaluation.diagnostics if evaluation is not None else None
    control_plan = evaluation.control_plan if evaluation is not None else None
    control_entities = dict(control_entities or {})
    zone_entity_ids = {
        key.removeprefix("climate:"): value
        for key, value in control_entities.items()
        if key.startswith("climate:")
    }

    snapshot: dict[str, object] = {
        "schema_version": PRESENTATION_SCHEMA_VERSION,
        "plant": _plant_snapshot(runtime, diagnostics),
        "controls": {
            "requested_mode": control_entities.get("requested_mode"),
            "safe_shutdown": control_entities.get("safe_shutdown"),
        },
        "zones": _zone_snapshots(runtime, diagnostics, zone_entity_ids),
        "topology": _topology_snapshot(runtime, control_plan),
        "delivery_paths": _delivery_paths(runtime, evaluation),
        "actuators": _actuator_snapshots(runtime, evaluation),
        "sources": _source_snapshots(runtime, diagnostics),
        "alerts": _alerts(runtime, evaluation),
        "explanations": _explanation_steps(runtime, evaluation),
        "execution": _execution_snapshot(runtime, evaluation),
        "safe_shutdown": _safe_shutdown_snapshot(runtime),
    }
    return snapshot


def serialize_presentation(snapshot: Mapping[str, object]) -> str:
    """Serialize a presentation snapshot deterministically for transport/tests."""
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _plant_snapshot(runtime: Any, diagnostics: Any) -> dict[str, object]:
    status = runtime.operational_status()
    if runtime.unresolved_bindings:
        health = "unavailable"
    elif status == "blocked":
        health = "blocked"
    elif status in {"initializing", "stopped"}:
        health = status
    else:
        health = "healthy"

    changeover = runtime.runtime_state.changeover_phase
    target_mode = runtime.runtime_state.changeover_target_mode
    active_mode = runtime.active_mode()
    requested_mode = runtime.requested_mode()
    source_selection = diagnostics.source_selection if diagnostics else None
    active_source_id = runtime.runtime_state.selected_source_id or (
        source_selection.active_source_id if source_selection else None
    )
    recommended_source_id = (
        diagnostics.source_recommendation.source_id
        if diagnostics and diagnostics.source_recommendation
        else None
    )
    active_source = runtime.plant.sources.get(active_source_id) if active_source_id else None
    recommended_source = (
        runtime.plant.sources.get(recommended_source_id) if recommended_source_id else None
    )
    forced_shadow = ["cooling", "source_selection"]
    if runtime.plant.source_selector is None:
        forced_shadow.remove("source_selection")
    return {
        "id": runtime.plant_id,
        "name": runtime.name,
        "status": status,
        "health": health,
        "requested_mode": _value(requested_mode),
        "active_mode": _value(active_mode),
        "changeover": {
            "phase": _value(changeover),
            "target_mode": _value(target_mode),
            "reason": runtime.runtime_state.changeover_reason,
        },
        "controller": {
            "evaluated": diagnostics is not None,
            "mode_explanation": diagnostics.mode_explanation if diagnostics else None,
        },
        "source": {
            "active_id": active_source.id if active_source else None,
            "active_name": active_source.name if active_source else None,
            "recommended_id": recommended_source.id if recommended_source else None,
            "recommended_name": recommended_source.name if recommended_source else None,
        },
        "execution_boundary": {
            "mode": "dry_run" if runtime.dry_run else "mixed",
            "dry_run": runtime.dry_run,
            "forced_shadow": forced_shadow,
            "message": _boundary_message(runtime, forced_shadow),
        },
    }


def _boundary_message(runtime: Any, forced_shadow: list[str]) -> str:
    if runtime.dry_run:
        return "Dry run - operations are proposed and no actuator calls are sent."
    if forced_shadow:
        return (
            "Mixed control - heating may execute; "
            + ", ".join(forced_shadow)
            + " remain shadow-only."
        )
    return "Heating control enabled - review each operation boundary before use."


def _zone_snapshots(
    runtime: Any,
    diagnostics: Any,
    zone_entity_ids: Mapping[str, str],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for zone_id, zone in sorted(runtime.plant.zones.items()):
        heating = diagnostics.zone_decisions.get(zone_id) if diagnostics else None
        cooling = diagnostics.cooling_zone_decisions.get(zone_id) if diagnostics else None
        aggregation = heating.aggregation if heating else None
        demand = runtime.runtime_state.zone_runtime.get(zone_id)
        cooling_demand = runtime.runtime_state.cooling_zone_demands.get(zone_id, False)
        result.append(
            {
                "id": zone_id,
                "name": zone.name,
                "climate_entity_id": zone_entity_ids.get(zone_id),
                "current_temperature": aggregation.value if aggregation else None,
                "target_temperature": runtime.zone_target_temperatures.get(
                    zone_id, zone.target_temperature
                ),
                "preset": runtime.zone_preset_modes.get(zone_id, "none"),
                "preset_modes": sorted(zone.preset_targets),
                "demand": bool(demand.demand) if demand else False,
                "phase": _zone_phase(
                    heating, cooling, bool(demand and demand.demand), cooling_demand
                ),
                "blocked": runtime.zone_is_blocked(zone_id)
                or runtime.cooling_zone_is_blocked(zone_id),
                "blocked_reason": runtime.zone_blocked_reason(zone_id)
                or runtime.cooling_zone_blocked_reason(zone_id),
                "sensor_status": {
                    "usable": len(aggregation.usable_sensor_ids) if aggregation else 0,
                    "optional_excluded": len(aggregation.excluded_optional_sensor_ids)
                    if aggregation
                    else 0,
                    "required_blocking": len(aggregation.blocking_required_sensor_ids)
                    if aggregation
                    else 0,
                },
                "cooling": {
                    "demand": cooling_demand,
                    "status": _value(cooling.status) if cooling else None,
                    "dew_point": cooling.dew_point if cooling else None,
                    "condensation_margin": cooling.condensation_margin if cooling else None,
                    "blocked": runtime.cooling_zone_is_blocked(zone_id),
                    "reason": runtime.cooling_zone_blocked_reason(zone_id),
                    "interlocks": [
                        {
                            "id": interlock.interlock_id,
                            "status": _value(interlock.status),
                            "reason": interlock.reason,
                        }
                        for interlock in (cooling.interlocks if cooling else ())
                    ],
                },
                "route_ids": sorted(
                    route.id for route in runtime.plant.routes if route.zone_id == zone_id
                ),
                "coupling_group_ids": _zone_coupling_groups(runtime, zone_id),
            }
        )
    return result


def _zone_phase(
    heating: ZoneDecision | None,
    cooling: ZoneDecision | None,
    demand: bool,
    cooling_demand: bool,
) -> str:
    if (
        heating
        and heating.status in {ZoneDecisionStatus.SENSOR_BLOCKED, ZoneDecisionStatus.MODE_BLOCKED}
    ) or (
        cooling
        and cooling.status in {ZoneDecisionStatus.SENSOR_BLOCKED, ZoneDecisionStatus.MODE_BLOCKED}
    ):
        return "blocked"
    if cooling_demand:
        return "cooling"
    if demand:
        return "heating"
    if heating and heating.status in {
        ZoneDecisionStatus.DURATION_HELD,
        ZoneDecisionStatus.DURATION_LOCKED,
    }:
        return "waiting"
    return "idle"


def _topology_snapshot(runtime: Any, control_plan: Any) -> dict[str, object]:
    circuits = {
        circuit_id: {
            "id": circuit_id,
            "name": circuit.name,
            "valve_ids": list(circuit.valve_ids),
            "pump_id": circuit.pump_id,
            "cooling_enabled": circuit.cooling_enabled,
            "route_ids": sorted(
                route.id for route in runtime.plant.routes if route.circuit_id == circuit_id
            ),
        }
        for circuit_id, circuit in sorted(runtime.plant.circuits.items())
    }
    routes = [
        {
            "id": route.id,
            "zone_id": route.zone_id,
            "circuit_id": route.circuit_id,
            "enabled": route.enabled,
        }
        for route in sorted(runtime.plant.routes, key=lambda item: item.id)
    ]
    coupling_groups = _coupling_groups(runtime)
    return {
        "routes": routes,
        "circuits": list(circuits.values()),
        "coupling_groups": coupling_groups,
        "summary": {
            "zones": len(runtime.plant.zones),
            "circuits": len(runtime.plant.circuits),
            "routes": len(runtime.plant.routes),
            "valves": len(runtime.plant.valves),
            "pumps": len(runtime.plant.pumps),
            "sources": len(runtime.plant.sources),
            "warnings": len(runtime.plant.warnings),
        },
        "warnings": [
            {"code": warning.code, "message": warning.message}
            for warning in sorted(runtime.plant.warnings, key=lambda item: item.code)
        ],
        "active_consumer_sets": {
            "valves": _consumer_sets(runtime, control_plan, "valve"),
            "pumps": _consumer_sets(runtime, control_plan, "pump"),
        },
    }


def _consumer_sets(runtime: Any, control_plan: Any, kind: str) -> list[dict[str, object]]:
    consumers = getattr(control_plan, f"{kind}_consumers", {}) if control_plan else {}
    return [
        {
            "actuator_id": actuator_id,
            "consumers": _named_circuits(runtime, consumer_ids),
        }
        for actuator_id, consumer_ids in sorted(consumers.items())
    ]


def _actuator_snapshots(runtime: Any, evaluation: Any) -> list[dict[str, object]]:
    control_plan = evaluation.control_plan if evaluation else None
    diagnostics = evaluation.diagnostics if evaluation else None
    result: list[dict[str, object]] = []
    for actuator_id, actuator in sorted(
        (*runtime.plant.valves.items(), *runtime.plant.pumps.items())
    ):
        kind = "valve" if actuator_id in runtime.plant.valves else "pump"
        diagnostic = diagnostics.actuator_diagnostics.get(actuator_id) if diagnostics else None
        requested = runtime.executor.requested_state(actuator_id)
        observed = runtime.executor.actuator_state(actuator_id)
        consumers = _active_consumers(runtime, control_plan, actuator_id, kind)
        runtime_state = (
            runtime.runtime_state.valves.get(actuator_id)
            if kind == "valve"
            else runtime.runtime_state.pumps.get(actuator_id)
        )
        state = _actuator_state(kind, runtime_state, diagnostic, consumers)
        result.append(
            {
                "id": actuator_id,
                "name": actuator.name,
                "kind": kind,
                "state": state,
                "requested": _value(requested),
                "observed": _value(observed),
                "ready": bool(runtime_state.is_ready)
                if kind == "valve" and runtime_state is not None
                else kind == "pump"
                and runtime_state is not None
                and runtime_state.state is PumpState.RUNNING,
                "blocked": bool(diagnostic and diagnostic.blocked),
                "mismatch": bool(diagnostic and diagnostic.mismatch),
                "reason": _actuator_reason(runtime, evaluation, actuator_id, diagnostic),
                "active_consumers": consumers,
            }
        )
    return result


def _active_consumers(
    runtime: Any, control_plan: Any, actuator_id: str, kind: str
) -> list[dict[str, object]]:
    if control_plan is None:
        return []
    consumer_ids = set(getattr(control_plan, f"{kind}_consumers", {}).get(actuator_id, ()))
    consumer_ids.update(getattr(control_plan, f"cooling_{kind}_consumers", {}).get(actuator_id, ()))
    return _named_circuits(runtime, consumer_ids)


def _actuator_state(
    kind: str, runtime_state: Any, diagnostic: Any, consumers: list[dict[str, object]]
) -> str:
    if diagnostic and diagnostic.blocked:
        return "blocked"
    if diagnostic and diagnostic.mismatch:
        return "mismatch"
    if kind == "valve":
        return _value(runtime_state.state) if runtime_state is not None else "unavailable"
    if runtime_state is None:
        return "unavailable"
    if runtime_state.state is PumpState.OVERRUN:
        return "overrun"
    if runtime_state.state is PumpState.WAITING_FOR_VALVES:
        return "waiting"
    if runtime_state.state is PumpState.STARTING:
        return "starting"
    if runtime_state.state is PumpState.RUNNING:
        return "active"
    return "idle" if not consumers else "requested"


def _actuator_reason(
    runtime: Any, evaluation: Any, actuator_id: str, diagnostic: Any
) -> str | None:
    if diagnostic and diagnostic.reason:
        return diagnostic.reason
    return (
        evaluation.diagnostics.actuator_reasons.get(actuator_id) if evaluation is not None else None
    )


def _source_snapshots(runtime: Any, diagnostics: Any) -> list[dict[str, object]]:
    source_diagnostics = diagnostics.source_diagnostics if diagnostics else {}
    result = []
    for source_id, source in sorted(runtime.plant.sources.items()):
        diagnostic = source_diagnostics.get(source_id)
        result.append(
            {
                "id": source_id,
                "name": source.name,
                "available": diagnostic.available if diagnostic else None,
                "eligible": diagnostic.eligible if diagnostic else False,
                "recommended": bool(diagnostic and diagnostic.recommended),
                "active": bool(diagnostic and diagnostic.active),
                "demand_requested": bool(diagnostic and diagnostic.demand_requested),
                "demand_permitted": bool(diagnostic and diagnostic.demand_permitted),
                "blocked": bool(diagnostic and diagnostic.blocked),
                "reason": diagnostic.reason if diagnostic else None,
            }
        )
    selection = diagnostics.source_selection if diagnostics else None
    return result + (
        [
            {
                "selection": {
                    "phase": _value(selection.phase),
                    "active_source_id": selection.active_source_id,
                    "target_source_id": selection.target_source_id,
                    "recommended_source_id": selection.recommended_source_id,
                    "hydraulically_safe": selection.hydraulically_safe,
                    "dwell_remaining_seconds": selection.dwell_remaining_seconds,
                    "explanation": selection.explanation,
                }
            }
        ]
        if selection is not None
        else []
    )


def _delivery_paths(runtime: Any, evaluation: Any) -> list[dict[str, object]]:
    control_plan = evaluation.control_plan if evaluation else None
    diagnostics = evaluation.diagnostics if evaluation else None
    paths: list[dict[str, object]] = []
    for route in sorted(runtime.plant.routes, key=lambda item: item.id):
        zone = runtime.plant.zones[route.zone_id]
        circuit = runtime.plant.circuits[route.circuit_id]
        zone_runtime = runtime.runtime_state.zone_runtime.get(zone.id)
        active = bool(zone_runtime and zone_runtime.demand)
        pump_consumers = (
            control_plan.pump_consumers.get(circuit.pump_id, ()) if control_plan else ()
        )
        valve_consumers = control_plan.valve_consumers if control_plan else {}
        circuit_requested = circuit.id in pump_consumers or any(
            circuit.id in consumers for consumers in valve_consumers.values()
        )
        status = (
            "active"
            if circuit.id in pump_consumers
            else "waiting"
            if circuit_requested
            else "requested"
            if active
            else "idle"
        )
        circuit_reason = diagnostics.circuit_reasons.get(circuit.id) if diagnostics else None
        if circuit_reason and circuit_reason.startswith("Blocked"):
            status = "blocked"
        pump_state = runtime.runtime_state.pumps.get(circuit.pump_id)
        if pump_state and pump_state.state is PumpState.OVERRUN and not active:
            status = "overrun"
        nodes: list[dict[str, object]] = [
            {
                "kind": "zone",
                "id": zone.id,
                "name": zone.name,
                "state": _zone_path_state(zone.id, runtime, evaluation),
            },
            {"kind": "circuit", "id": circuit.id, "name": circuit.name, "state": status},
        ]
        nodes.extend(
            {
                "kind": "valve",
                "id": valve_id,
                "name": runtime.plant.valves[valve_id].name,
                "state": _value(runtime.runtime_state.valves.get(valve_id).state)
                if runtime.runtime_state.valves.get(valve_id)
                else "unavailable",
            }
            for valve_id in circuit.valve_ids
        )
        nodes.append(
            {
                "kind": "pump",
                "id": circuit.pump_id,
                "name": runtime.plant.pumps[circuit.pump_id].name,
                "state": _value(pump_state.state) if pump_state else "unavailable",
            }
        )
        if runtime.plant.sources:
            source_id = runtime.runtime_state.selected_source_id
            source = runtime.plant.sources.get(source_id) if source_id else None
            if source is None and diagnostics and diagnostics.source_recommendation:
                source = runtime.plant.sources.get(diagnostics.source_recommendation.source_id)
            if source is not None:
                nodes.append(
                    {"kind": "source", "id": source.id, "name": source.name, "state": "selected"}
                )
        paths.append(
            {
                "id": route.id,
                "zone_id": zone.id,
                "circuit_id": circuit.id,
                "status": status,
                "problem": circuit_reason if status == "blocked" else None,
                "coupled": bool(_zone_coupling_groups(runtime, zone.id)),
                "nodes": nodes,
            }
        )
    return paths


def _zone_path_state(zone_id: str, runtime: Any, evaluation: Any) -> str:
    if evaluation is None:
        return "initializing"
    decision = evaluation.diagnostics.zone_decisions.get(zone_id)
    cooling = evaluation.diagnostics.cooling_zone_decisions.get(zone_id)
    return _zone_phase(
        decision,
        cooling,
        runtime.runtime_state.zone_runtime.get(zone_id, object()).demand
        if zone_id in runtime.runtime_state.zone_runtime
        else False,
        runtime.runtime_state.cooling_zone_demands.get(zone_id, False),
    )


def _alerts(runtime: Any, evaluation: Any) -> list[dict[str, object]]:
    alerts: dict[tuple[str, str], dict[str, object]] = {}

    def add(code: str, severity: str, scope: str, message: str) -> None:
        alerts[(code, scope)] = {
            "code": code,
            "severity": severity,
            "priority": _SEVERITY_ORDER[severity],
            "scope": scope,
            "message": message,
        }

    if runtime.operational_status() == "initializing":
        add("plant_initializing", "info", "plant", "Hydronicus is evaluating the Plant.")
    if runtime.operational_status() == "stopped":
        add("plant_unavailable", "error", "plant", "The Hydronicus Plant is unavailable.")
    if runtime.unresolved_bindings:
        add(
            "binding_unavailable",
            "error",
            "plant",
            "One or more configured bindings are unavailable; control is blocked.",
        )
    if evaluation is None:
        return _sorted_alerts(alerts.values())
    for zone_id, decision in sorted(evaluation.diagnostics.zone_decisions.items()):
        if decision.status is ZoneDecisionStatus.SENSOR_BLOCKED:
            add("zone_sensor_blocked", "error", zone_id, decision.explanation)
        elif decision.status is ZoneDecisionStatus.MODE_BLOCKED:
            add("zone_mode_blocked", "warning", zone_id, decision.explanation)
    for zone_id, decision in sorted(evaluation.diagnostics.cooling_zone_decisions.items()):
        if decision.status in {ZoneDecisionStatus.SENSOR_BLOCKED, ZoneDecisionStatus.MODE_BLOCKED}:
            add("cooling_blocked", "warning", zone_id, decision.explanation)
    for actuator_id, diagnostic in sorted(evaluation.diagnostics.actuator_diagnostics.items()):
        if diagnostic.mismatch:
            add("actuator_mismatch", "error", actuator_id, diagnostic.reason)
        elif diagnostic.blocked:
            add("actuator_blocked", "error", actuator_id, diagnostic.reason)
    if runtime.runtime_state.changeover_phase is not ModeChangeoverPhase.IDLE:
        add("mode_changeover", "warning", "plant", runtime.runtime_state.changeover_reason)
    if runtime.runtime_state.safe_shutdown_phase is not SafeShutdownPhase.IDLE:
        add(
            "safe_shutdown",
            "warning",
            "plant",
            "Safe shutdown is in progress; sequencing remains in Hydronicus.",
        )
    if runtime.last_execution:
        for failure in runtime.last_execution.failures:
            add(
                "operation_timed_out" if failure.kind.value == "timeout" else "operation_failed",
                "error",
                failure.operation.actuator_id,
                failure.explanation,
            )
        if runtime.last_execution.proposed:
            add(
                "dry_run_operations" if runtime.dry_run else "shadow_operations",
                "warning",
                "plant",
                f"{len(runtime.last_execution.proposed)} operation(s) are proposed "
                "and not executed.",
            )
    return _sorted_alerts(alerts.values())


def _sorted_alerts(alerts: Any) -> list[dict[str, object]]:
    return sorted(
        alerts, key=lambda item: (int(item["priority"]), str(item["code"]), str(item["scope"]))
    )


def _explanation_steps(runtime: Any, evaluation: Any) -> list[dict[str, object]]:
    if evaluation is None:
        return [
            {
                "order": 0,
                "scope": "plant",
                "code": "initializing",
                "message": "The controller has not evaluated the Plant yet.",
            }
        ]
    diagnostics = evaluation.diagnostics
    steps: list[dict[str, object]] = []
    order = 0

    def add(scope: str, code: str, message: str) -> None:
        nonlocal order
        if message:
            steps.append({"order": order, "scope": scope, "code": code, "message": message})
            order += 1

    add("plant", "mode", diagnostics.mode_explanation)
    for zone_id, reason in sorted(diagnostics.zone_reasons.items()):
        add(zone_id, "zone_decision", reason)
    for circuit_id, reason in sorted(diagnostics.circuit_reasons.items()):
        add(circuit_id, "circuit_decision", reason)
    for actuator_id, reason in sorted(diagnostics.actuator_reasons.items()):
        add(actuator_id, "actuator_decision", reason)
    if diagnostics.source_recommendation:
        add("plant", "source_recommendation", diagnostics.source_recommendation.explanation)
    if diagnostics.source_selection:
        add("plant", "source_selection", diagnostics.source_selection.explanation)
    return steps


def _execution_snapshot(runtime: Any, evaluation: Any) -> dict[str, object]:
    report = runtime.last_execution
    if report is None:
        return {
            "boundary": _execution_boundary(runtime, evaluation),
            "operations": {
                "proposed": [],
                "executed": [],
                "suppressed": [],
                "failed": [],
                "timed_out": [],
            },
        }
    operations = {
        "proposed": [_operation(runtime, item, "proposed") for item in report.proposed],
        "executed": [_operation(runtime, item, "executed") for item in report.executed],
        "suppressed": [_operation(runtime, item, "suppressed") for item in report.suppressed],
        "failed": [
            _failure_operation(runtime, failure)
            for failure in report.failures
            if failure.kind.value != "timeout"
        ],
        "timed_out": [
            _failure_operation(runtime, failure)
            for failure in report.failures
            if failure.kind.value == "timeout"
        ],
    }
    return {"boundary": _execution_boundary(runtime, evaluation), "operations": operations}


def _execution_boundary(runtime: Any, evaluation: Any) -> dict[str, object]:
    return {
        "dry_run": runtime.dry_run,
        "cooling_shadow": True,
        "source_selection_shadow": bool(runtime.plant.source_selector),
        "forced_shadow_actuators": sorted(
            evaluation.control_plan.cooling_actuator_ids if evaluation else ()
        ),
    }


def _operation(runtime: Any, operation: ActuatorOperation, result: str) -> dict[str, object]:
    return {
        "result": result,
        "actuator_id": operation.actuator_id,
        "actuator_name": _actuator_name(runtime, operation.actuator_id),
        "action": operation.service,
        "target_state": _value(operation.target_state),
        "target_value": operation.target_value,
        "reason": operation.reason,
    }


def _failure_operation(runtime: Any, failure: ActuatorExecutionFailure) -> dict[str, object]:
    return {
        **_operation(
            runtime, failure.operation, "timed_out" if failure.kind.value == "timeout" else "failed"
        ),
        "failure_kind": failure.kind.value,
        "explanation": failure.explanation,
    }


def _safe_shutdown_snapshot(runtime: Any) -> dict[str, object]:
    phase = runtime.runtime_state.safe_shutdown_phase
    return {
        "active": phase is not SafeShutdownPhase.IDLE,
        "phase": _value(phase),
        "message": "Safe shutdown delegates ordered release and stop sequencing to Hydronicus.",
    }


def _actuator_name(runtime: Any, actuator_id: str) -> str | None:
    if actuator_id in runtime.plant.valves:
        return runtime.plant.valves[actuator_id].name
    if actuator_id in runtime.plant.pumps:
        return runtime.plant.pumps[actuator_id].name
    if actuator_id.startswith("source:"):
        source = runtime.plant.sources.get(actuator_id.removeprefix("source:"))
        return source.name if source else None
    if runtime.plant.source_selector and runtime.plant.source_selector.id == actuator_id:
        return runtime.plant.source_selector.name
    return None


def _named_circuits(runtime: Any, circuit_ids: Any) -> list[dict[str, object]]:
    return [
        {"id": circuit_id, "name": runtime.plant.circuits[circuit_id].name}
        for circuit_id in sorted(circuit_ids)
        if circuit_id in runtime.plant.circuits
    ]


def _coupling_groups(runtime: Any) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    for kind, actuator_ids in (("valve", runtime.plant.valves), ("pump", runtime.plant.pumps)):
        for actuator_id, _actuator in sorted(actuator_ids.items()):
            if kind == "valve":
                circuit_ids = sorted(
                    circuit.id
                    for circuit in runtime.plant.circuits.values()
                    if actuator_id in circuit.valve_ids
                )
            else:
                circuit_ids = sorted(
                    circuit.id
                    for circuit in runtime.plant.circuits.values()
                    if circuit.pump_id == actuator_id
                )
            if len(circuit_ids) < 2:
                continue
            zone_ids = sorted(
                {route.zone_id for route in runtime.plant.routes if route.circuit_id in circuit_ids}
            )
            groups.append(
                {
                    "id": f"{kind}:{actuator_id}",
                    "kind": kind,
                    "actuator_id": actuator_id,
                    "circuit_ids": circuit_ids,
                    "zone_ids": zone_ids,
                    "message": (
                        f"Shared {kind} couples these delivery paths; they are not "
                        "independently controllable."
                    ),
                }
            )
    return groups


def _zone_coupling_groups(runtime: Any, zone_id: str) -> list[str]:
    return [group["id"] for group in _coupling_groups(runtime) if zone_id in group["zone_ids"]]


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value
