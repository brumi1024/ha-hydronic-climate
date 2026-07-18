"""Tests for the deterministic shadow-mode heating sequence."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from hydronicus_core.controller import (
    aggregate_temperature,
    aggregate_zone_humidity_result,
    aggregate_zone_temperature,
    aggregate_zone_temperature_result,
    condensation_margin,
    dew_point_celsius,
    evaluate,
    mean_zone_temperature,
    resolve_cooling_delivery_routes,
)
from hydronicus_core.model import (
    Circuit,
    DeliveryRoute,
    InterlockStatus,
    ModeConflict,
    PlantConfiguration,
    PlantSnapshot,
    Pump,
    PumpRuntime,
    PumpState,
    RuntimeState,
    Source,
    TemperatureAggregation,
    TemperatureObservation,
    TemperatureSensorMetadata,
    Valve,
    ValveRuntime,
    ValveState,
    Zone,
    ZoneDecisionStatus,
    ZoneRuntime,
)
from hydronicus_core.topology import compile_topology

NOW = datetime(2026, 7, 17, tzinfo=UTC)


def _plant() -> PlantConfiguration:
    return PlantConfiguration(
        id="plant",
        zones=(Zone("living", "Living", 21.0, ("temperature.living",)),),
        valves=(Valve("valve.floor", "Floor valve", "switch.floor_valve", 30),),
        pumps=(Pump("pump.floor", "Floor pump", "switch.floor_pump", 120),),
        circuits=(
            Circuit(
                "floor",
                "Floor",
                ("valve.floor",),
                "pump.floor",
            ),
        ),
        routes=(DeliveryRoute("living-floor", "living", "floor"),),
    )


def _snapshot(temperature: float) -> PlantSnapshot:
    return PlantSnapshot({"temperature.living": TemperatureObservation(temperature, NOW)})


@pytest.mark.parametrize(
    ("aggregation", "expected"),
    [
        (TemperatureAggregation.MEAN, 20.0),
        (TemperatureAggregation.MEDIAN, 20.0),
        (TemperatureAggregation.MINIMUM, 18.0),
        (TemperatureAggregation.MAXIMUM, 22.0),
        (TemperatureAggregation.WEIGHTED_MEAN, 20.5),
    ],
)
def test_zone_temperature_aggregation_is_deterministic(aggregation, expected) -> None:
    """Every supported policy produces the documented aggregate."""
    zone = Zone(
        "living",
        "Living",
        21.0,
        ("temperature.living", "temperature.living_backup", "temperature.living_window"),
        aggregation=aggregation,
        temperature_sensor_weights={
            "temperature.living": 1.0,
            "temperature.living_backup": 2.0,
            "temperature.living_window": 1.0,
        },
    )
    snapshot = PlantSnapshot(
        {
            "temperature.living": TemperatureObservation(18.0, NOW),
            "temperature.living_backup": TemperatureObservation(22.0, NOW),
            "temperature.living_window": TemperatureObservation(20.0, NOW),
        }
    )

    assert aggregate_zone_temperature(zone, snapshot) == expected


def test_zone_temperature_aggregation_defaults_legacy_zones_to_mean() -> None:
    """Zones created before aggregation support retain mean semantics."""
    zone = Zone("living", "Living", 21.0, ("temperature.living", "temperature.backup"))
    snapshot = PlantSnapshot(
        {
            "temperature.living": TemperatureObservation(18.0, NOW),
            "temperature.backup": TemperatureObservation(22.0, NOW),
        }
    )

    assert aggregate_zone_temperature(zone, snapshot) == 20.0


def test_designated_reference_aggregation_is_calibrated_and_order_independent() -> None:
    """The designated sensor wins regardless of configuration order and is calibrated."""
    sensors = (
        TemperatureSensorMetadata("temperature.window", calibration_offset=4.0),
        TemperatureSensorMetadata(
            "temperature.reference",
            calibration_offset=-1.0,
            designated_reference=True,
        ),
    )
    reverse = Zone(
        "living",
        "Living",
        21.0,
        temperature_sensor_metadata=tuple(reversed(sensors)),
        aggregation=TemperatureAggregation.DESIGNATED_REFERENCE,
    )
    snapshot = PlantSnapshot(
        {
            "temperature.reference": TemperatureObservation(20.0, NOW),
            "temperature.window": TemperatureObservation(12.0, NOW),
        }
    )

    result = aggregate_zone_temperature_result(reverse, snapshot, now=NOW)

    assert result.value == 19.0
    assert result.usable_sensor_ids == (
        "temperature.reference",
        "temperature.window",
    )
    assert result.excluded_optional_sensor_ids == ()
    assert result.blocking_required_sensor_ids == ()


@pytest.mark.parametrize(
    ("aggregation", "expected"),
    [
        (TemperatureAggregation.MEAN, 20.0),
        (TemperatureAggregation.MEDIAN, 20.0),
        (TemperatureAggregation.MINIMUM, 18.0),
        (TemperatureAggregation.MAXIMUM, 22.0),
        (TemperatureAggregation.WEIGHTED_MEAN, 20.5),
    ],
)
def test_calibrated_aggregation_applies_offsets_before_every_policy(
    aggregation: TemperatureAggregation, expected: float
) -> None:
    """All non-reference policies consume calibrated readings, not raw values."""
    zone = Zone(
        "living",
        "Living",
        21.0,
        temperature_sensor_metadata=(
            TemperatureSensorMetadata("temperature.living", calibration_offset=1.0),
            TemperatureSensorMetadata("temperature.backup", calibration_offset=-1.0, weight=2.0),
            TemperatureSensorMetadata("temperature.window", weight=1.0),
        ),
        aggregation=aggregation,
    )
    snapshot = PlantSnapshot(
        {
            "temperature.living": TemperatureObservation(17.0, NOW),
            "temperature.backup": TemperatureObservation(23.0, NOW),
            "temperature.window": TemperatureObservation(20.0, NOW),
        }
    )

    result = aggregate_zone_temperature_result(zone, snapshot, now=NOW)

    assert result.value == expected


def test_optional_sensor_is_excluded_and_required_sensor_remains_usable() -> None:
    """An unavailable optional observation cannot distort or block aggregation."""
    zone = Zone(
        "living",
        "Living",
        21.0,
        temperature_sensor_metadata=(
            TemperatureSensorMetadata("temperature.required"),
            TemperatureSensorMetadata("temperature.optional", required=False),
        ),
    )
    result = aggregate_zone_temperature_result(
        zone,
        PlantSnapshot(
            {
                "temperature.required": TemperatureObservation(19.0, NOW),
                "temperature.optional": TemperatureObservation(None, NOW),
            }
        ),
        now=NOW,
    )

    assert result.value == 19.0
    assert result.usable_sensor_ids == ("temperature.required",)
    assert result.excluded_optional_sensor_ids == ("temperature.optional",)
    assert result.blocking_required_sensor_ids == ()
    assert "Excluded optional sensors" in result.explanation


def test_stale_required_sensor_blocks_immediately() -> None:
    """Age-based freshness is enforced at evaluation time, not only on events."""
    zone = Zone(
        "living",
        "Living",
        21.0,
        temperature_sensor_metadata=(
            TemperatureSensorMetadata("temperature.required", max_age_seconds=30),
        ),
    )
    result = aggregate_zone_temperature_result(
        zone,
        PlantSnapshot(
            {"temperature.required": TemperatureObservation(19.0, NOW - timedelta(seconds=31))}
        ),
        now=NOW,
    )

    assert result.value is None
    assert result.blocking_required_sensor_ids == ("temperature.required",)
    assert "stale" in result.explanation


def test_aggregation_reports_missing_and_invalid_timestamp_health() -> None:
    """Missing observations and unusable timestamps are explicit sensor failures."""
    optional_zone = Zone(
        "living",
        "Living",
        21.0,
        temperature_sensor_metadata=(
            TemperatureSensorMetadata("temperature.optional", required=False),
        ),
    )
    missing = aggregate_zone_temperature_result(optional_zone, PlantSnapshot({}), now=NOW)
    assert missing.value is None
    assert missing.excluded_optional_sensor_ids == ("temperature.optional",)
    assert "no usable" in missing.explanation

    required_zone = Zone(
        "living",
        "Living",
        21.0,
        temperature_sensor_metadata=(TemperatureSensorMetadata("temperature.required"),),
    )
    no_timestamp = aggregate_zone_temperature_result(
        required_zone,
        PlantSnapshot({"temperature.required": TemperatureObservation(19.0, None)}),
        now=NOW,
    )
    invalid_timestamp = aggregate_zone_temperature_result(
        required_zone,
        PlantSnapshot(
            {"temperature.required": TemperatureObservation(19.0, NOW.replace(tzinfo=None))}
        ),
        now=NOW,
    )
    assert no_timestamp.blocking_required_sensor_ids == ("temperature.required",)
    assert "missing timestamp" in no_timestamp.explanation
    assert invalid_timestamp.blocking_required_sensor_ids == ("temperature.required",)
    assert "invalid timestamp" in invalid_timestamp.explanation


def test_aggregation_rejects_non_finite_calibration_result_and_bad_weight() -> None:
    """Runtime arithmetic remains fail-closed even when topology was bypassed."""
    overflow_zone = Zone(
        "living",
        "Living",
        21.0,
        temperature_sensor_metadata=(
            TemperatureSensorMetadata("temperature.required", calibration_offset=1e308),
        ),
    )
    overflow = aggregate_zone_temperature_result(
        overflow_zone,
        PlantSnapshot({"temperature.required": TemperatureObservation(1e308, NOW)}),
        now=NOW,
    )
    weighted_zone = Zone(
        "living",
        "Living",
        21.0,
        temperature_sensor_metadata=(TemperatureSensorMetadata("temperature.weight", weight=0),),
        aggregation=TemperatureAggregation.WEIGHTED_MEAN,
    )
    weighted = aggregate_zone_temperature_result(
        weighted_zone,
        PlantSnapshot({"temperature.weight": TemperatureObservation(19.0, NOW)}),
        now=NOW,
    )

    assert overflow.blocking_required_sensor_ids == ("temperature.required",)
    assert "non-finite after calibration" in overflow.explanation
    assert weighted.value is None
    assert "designated reference" not in weighted.explanation


def test_optional_designated_reference_does_not_fallback_to_another_policy() -> None:
    """A selected reference policy cannot silently substitute a different sensor."""
    zone = Zone(
        "living",
        "Living",
        21.0,
        temperature_sensor_metadata=(
            TemperatureSensorMetadata(
                "temperature.reference", required=False, designated_reference=True
            ),
            TemperatureSensorMetadata("temperature.backup"),
        ),
        aggregation=TemperatureAggregation.DESIGNATED_REFERENCE,
    )
    result = aggregate_temperature(
        zone,
        PlantSnapshot({"temperature.backup": TemperatureObservation(19.0, NOW)}),
        now=NOW,
    )

    assert result.value is None
    assert result.usable_sensor_ids == ("temperature.backup",)
    assert result.excluded_optional_sensor_ids == ("temperature.reference",)
    assert "designated reference" in result.explanation


def test_legacy_aggregation_helpers_preserve_value_only_contract() -> None:
    """The adapter compatibility helpers expose values while structured callers use results."""
    snapshot = PlantSnapshot(
        {
            "temperature.a": TemperatureObservation(19.0, NOW),
            "temperature.b": TemperatureObservation(21.0, NOW),
        }
    )
    zone = Zone("living", "Living", 21.0, ("temperature.a", "temperature.b"))

    assert aggregate_zone_temperature(zone, snapshot) == 20.0
    assert mean_zone_temperature(("temperature.a", "temperature.b"), snapshot) == 20.0


def _timed_plant(
    *, active: float = 0, idle: float = 0, max_age: float = 1800
) -> PlantConfiguration:
    return PlantConfiguration(
        id="timed-plant",
        zones=(
            Zone(
                "living",
                "Living",
                21.0,
                temperature_sensor_metadata=(
                    TemperatureSensorMetadata("temperature.living", max_age_seconds=max_age),
                ),
                minimum_active_duration_seconds=active,
                minimum_idle_duration_seconds=idle,
            ),
        ),
        valves=(Valve("valve", "Valve", "switch.valve", 0),),
        pumps=(Pump("pump", "Pump", "switch.pump", 0),),
        circuits=(Circuit("circuit", "Circuit", ("valve",), "pump"),),
        routes=(DeliveryRoute("route", "living", "circuit"),),
    )


def _timed_snapshot(value: float, observed_at: datetime) -> PlantSnapshot:
    return PlantSnapshot({"temperature.living": TemperatureObservation(value, observed_at)})


def test_required_sensor_block_overrides_minimum_active_duration() -> None:
    """Fail-closed sensor safety releases active demand without waiting."""
    plant = compile_topology(_timed_plant(active=60, max_age=30))
    active = evaluate(plant, _timed_snapshot(19.0, NOW), RuntimeState(), NOW)
    blocked = evaluate(
        plant,
        _timed_snapshot(22.0, NOW - timedelta(seconds=31)),
        active.next_runtime,
        NOW + timedelta(seconds=1),
    )

    decision = blocked.diagnostics.zone_decisions["living"]
    assert active.next_runtime.zone_demands["living"] is True
    assert blocked.next_runtime.zone_demands["living"] is False
    assert decision.status is ZoneDecisionStatus.SENSOR_BLOCKED
    assert blocked.control_plan.valve_consumers == {}


def test_minimum_active_and_idle_deadlines_are_structured_and_deterministic() -> None:
    """Demand transitions honor both deadlines after hysteresis is applied."""
    plant = compile_topology(_timed_plant(active=60, idle=30))
    runtime = RuntimeState(
        zone_demands={"living": False},
        zone_runtime={
            "living": ZoneRuntime(False, NOW - timedelta(seconds=30)),
        },
    )
    active = evaluate(plant, _timed_snapshot(19.0, NOW), runtime, NOW)
    held = evaluate(
        plant,
        _timed_snapshot(22.0, NOW + timedelta(seconds=10)),
        active.next_runtime,
        NOW + timedelta(seconds=10),
    )
    released = evaluate(
        plant,
        _timed_snapshot(22.0, NOW + timedelta(seconds=60)),
        held.next_runtime,
        NOW + timedelta(seconds=60),
    )
    locked = evaluate(
        plant,
        _timed_snapshot(19.0, NOW + timedelta(seconds=70)),
        released.next_runtime,
        NOW + timedelta(seconds=70),
    )
    requested = evaluate(
        plant,
        _timed_snapshot(19.0, NOW + timedelta(seconds=90)),
        locked.next_runtime,
        NOW + timedelta(seconds=90),
    )

    assert active.diagnostics.zone_decisions["living"].status is ZoneDecisionStatus.REQUESTED
    assert held.next_runtime.zone_demands["living"] is True
    assert held.diagnostics.zone_decisions["living"].status is ZoneDecisionStatus.DURATION_HELD
    assert held.diagnostics.zone_decisions["living"].deadline == NOW + timedelta(seconds=60)
    assert released.next_runtime.zone_demands["living"] is False
    assert released.diagnostics.zone_decisions["living"].status is ZoneDecisionStatus.SATISFIED
    assert locked.next_runtime.zone_demands["living"] is False
    assert locked.diagnostics.zone_decisions["living"].status is ZoneDecisionStatus.DURATION_LOCKED
    assert locked.diagnostics.zone_decisions["living"].deadline == NOW + timedelta(seconds=90)
    assert requested.next_runtime.zone_demands["living"] is True
    assert requested.diagnostics.zone_decisions["living"].status is ZoneDecisionStatus.REQUESTED


def test_restored_runtime_without_timestamp_uses_conservative_timing() -> None:
    """Unknown restored age cannot bypass a configured minimum active duration."""
    plant = compile_topology(_timed_plant(active=60))
    runtime = RuntimeState(
        zone_demands={"living": True},
        zone_runtime={"living": ZoneRuntime(True, None)},
    )

    held = evaluate(plant, _timed_snapshot(22.0, NOW), runtime, NOW)
    released = evaluate(
        plant,
        _timed_snapshot(22.0, NOW + timedelta(seconds=60)),
        held.next_runtime,
        NOW + timedelta(seconds=60),
    )

    assert held.next_runtime.zone_demands["living"] is True
    assert held.diagnostics.zone_decisions["living"].status is ZoneDecisionStatus.DURATION_HELD
    assert released.next_runtime.zone_demands["living"] is False


def test_controller_uses_zone_aggregation_policy_for_demand() -> None:
    """Demand evaluation must use the same aggregate exposed to climate entities."""
    plant = compile_topology(
        PlantConfiguration(
            id="plant",
            zones=(
                Zone(
                    "living",
                    "Living",
                    20.0,
                    ("temperature.living", "temperature.window"),
                    aggregation=TemperatureAggregation.MAXIMUM,
                ),
            ),
            valves=(Valve("valve.floor", "Floor valve", "switch.floor_valve"),),
            pumps=(Pump("pump.floor", "Floor pump", "switch.floor_pump"),),
            circuits=(Circuit("floor", "Floor", ("valve.floor",), "pump.floor"),),
            routes=(DeliveryRoute("living-floor", "living", "floor"),),
        )
    )
    result = evaluate(
        plant,
        PlantSnapshot(
            {
                "temperature.living": TemperatureObservation(18.0, NOW),
                "temperature.window": TemperatureObservation(20.0, NOW),
            }
        ),
        RuntimeState(),
        NOW,
    )

    assert result.next_runtime.zone_demands["living"] is False
    assert result.diagnostics.zone_reasons["living"] == (
        "Heating remains idle inside the hysteresis band."
    )


def test_shadow_sequence_waits_for_valve_before_requesting_pump() -> None:
    plant = compile_topology(_plant())

    opening = evaluate(plant, _snapshot(20.0), RuntimeState(), NOW)
    ready = evaluate(plant, _snapshot(20.0), opening.next_runtime, NOW + timedelta(seconds=30))

    assert opening.next_runtime.valves["valve.floor"].state is ValveState.OPENING
    assert opening.control_plan.commands[0].action == "open"
    assert "pump.floor" not in {command.actuator_id for command in opening.control_plan.commands}
    assert ready.next_runtime.valves["valve.floor"].state is ValveState.OPEN
    assert ready.next_runtime.pumps["pump.floor"].state is PumpState.RUNNING
    assert [(command.actuator_id, command.action) for command in ready.control_plan.commands] == [
        ("pump.floor", "turn_on")
    ]


def _cooling_plant(
    *,
    supply: str | None = "temperature.supply",
    surface: str | None = None,
    margin: float = 2.0,
) -> object:
    """Build one fully synthetic cooling-enabled circuit."""
    return compile_topology(
        PlantConfiguration(
            id="cooling-plant",
            zones=(
                Zone(
                    "living",
                    "Living",
                    24.0,
                    temperature_sensor_metadata=(TemperatureSensorMetadata("temperature.living"),),
                    humidity_sensor_metadata=(TemperatureSensorMetadata("humidity.living"),),
                    cooling_start_delta=0.5,
                    cooling_stop_delta=0.2,
                ),
            ),
            valves=(Valve("valve", "Cooling valve", "switch.cooling_valve", 0),),
            pumps=(Pump("pump", "Cooling pump", "switch.cooling_pump", 0),),
            circuits=(
                Circuit(
                    "cooling",
                    "Cooling",
                    ("valve",),
                    "pump",
                    cooling_enabled=True,
                    supply_temperature_sensor=supply,
                    surface_temperature_sensor=surface,
                    condensation_margin=margin,
                ),
            ),
            routes=(DeliveryRoute("route", "living", "cooling"),),
        )
    )


def _cooling_snapshot(
    *,
    temperature: float = 25.0,
    humidity: float = 50.0,
    supply: float = 18.0,
    observed_at: datetime = NOW,
) -> PlantSnapshot:
    """Build a fresh synthetic cooling snapshot."""
    return PlantSnapshot(
        temperatures={"temperature.living": TemperatureObservation(temperature, observed_at)},
        humidities={"humidity.living": TemperatureObservation(humidity, observed_at)},
        supply_temperatures={"temperature.supply": TemperatureObservation(supply, observed_at)},
    )


def _mixed_mode_plant(*, shared_valve: bool, shared_pump: bool, source: bool = False):
    """Build heating and cooling routes with selected shared equipment."""
    return compile_topology(
        PlantConfiguration(
            id="mixed-mode-plant",
            zones=(
                Zone("heating-zone", "Heating zone", 21.0, ("temperature.heating",)),
                Zone(
                    "cooling-zone",
                    "Cooling zone",
                    24.0,
                    temperature_sensor_metadata=(
                        TemperatureSensorMetadata("temperature.cooling"),
                    ),
                    humidity_sensor_metadata=(TemperatureSensorMetadata("humidity.cooling"),),
                ),
            ),
            valves=(
                Valve("heating-valve", "Heating valve", "switch.heating_valve", 0),
                Valve("cooling-valve", "Cooling valve", "switch.cooling_valve", 0),
            )
            if not shared_valve
            else (Valve("shared-valve", "Shared valve", "switch.shared_valve", 0),),
            pumps=(
                Pump("heating-pump", "Heating pump", "switch.heating_pump", 0),
                Pump("cooling-pump", "Cooling pump", "switch.cooling_pump", 0),
            )
            if not shared_pump
            else (Pump("shared-pump", "Shared pump", "switch.shared_pump", 0),),
            circuits=(
                Circuit(
                    "heating-circuit",
                    "Heating circuit",
                    ("shared-valve" if shared_valve else "heating-valve",),
                    "shared-pump" if shared_pump else "heating-pump",
                ),
                Circuit(
                    "cooling-circuit",
                    "Cooling circuit",
                    ("shared-valve" if shared_valve else "cooling-valve",),
                    "shared-pump" if shared_pump else "cooling-pump",
                    cooling_enabled=True,
                    supply_temperature_sensor="temperature.cooling_supply",
                ),
            ),
            routes=(
                DeliveryRoute("heating-route", "heating-zone", "heating-circuit"),
                DeliveryRoute("cooling-route", "cooling-zone", "cooling-circuit"),
            ),
            sources=(Source("source", "Shared source"),) if source else (),
        )
    )


def _mixed_mode_snapshot() -> PlantSnapshot:
    """Return safe observations that request both heating and cooling."""
    return PlantSnapshot(
        temperatures={
            "temperature.heating": TemperatureObservation(19.0, NOW),
            "temperature.cooling": TemperatureObservation(25.0, NOW),
        },
        humidities={"humidity.cooling": TemperatureObservation(50.0, NOW)},
        supply_temperatures={
            "temperature.cooling_supply": TemperatureObservation(18.0, NOW)
        },
    )


def test_cooling_route_arbitration_rejects_heating_only_circuits() -> None:
    """Cooling demand cannot turn an existing heating-only route into a cooling path."""
    plant = compile_topology(_plant())

    assert resolve_cooling_delivery_routes(plant, {"living": True}) == ()


@pytest.mark.parametrize(
    ("shared_valve", "shared_pump"),
    [(True, False), (False, True), (True, True)],
)
def test_shared_equipment_conflict_blocks_cooling_with_structured_explanations(
    shared_valve: bool, shared_pump: bool
) -> None:
    """Heating priority removes only cooling routes coupled to shared equipment."""
    plant = _mixed_mode_plant(shared_valve=shared_valve, shared_pump=shared_pump)

    result = evaluate(plant, _mixed_mode_snapshot(), RuntimeState(), NOW)

    conflicts = result.diagnostics.mode_conflicts
    assert conflicts
    assert all(isinstance(conflict, ModeConflict) for conflict in conflicts)
    assert result.next_runtime.cooling_zone_demands["cooling-zone"] is False
    assert result.control_plan.cooling_valve_consumers == {}
    assert result.control_plan.cooling_pump_consumers == {}
    assert result.control_plan.cooling_actuator_ids == frozenset()
    assert result.control_plan.mode_conflicts == conflicts
    assert result.diagnostics.cooling_zone_decisions["cooling-zone"].status is (
        ZoneDecisionStatus.SENSOR_BLOCKED
    )
    assert "Cooling blocked by shared" in result.diagnostics.cooling_zone_reasons[
        "cooling-zone"
    ]
    assert result == evaluate(plant, _mixed_mode_snapshot(), RuntimeState(), NOW)


def test_shared_source_conflict_blocks_cooling_even_on_independent_hydraulics() -> None:
    """Plant-owned sources are shared until source paths become explicitly scoped."""
    plant = _mixed_mode_plant(shared_valve=False, shared_pump=False, source=True)

    result = evaluate(plant, _mixed_mode_snapshot(), RuntimeState(), NOW)

    assert [conflict.equipment_kind for conflict in result.diagnostics.mode_conflicts] == ["source"]
    assert result.diagnostics.mode_conflicts[0].equipment_id == "source"
    assert "shared source" in result.diagnostics.cooling_zone_reasons["cooling-zone"]


def test_independent_hydraulic_paths_keep_cooling_route_eligible() -> None:
    """Without shared equipment or sources, route arbitration keeps both modes visible."""
    plant = _mixed_mode_plant(shared_valve=False, shared_pump=False)

    result = evaluate(plant, _mixed_mode_snapshot(), RuntimeState(), NOW)

    assert result.diagnostics.mode_conflicts == ()
    assert result.next_runtime.cooling_zone_demands["cooling-zone"] is True
    assert result.control_plan.cooling_valve_consumers == {
        "cooling-valve": frozenset({"cooling-circuit"})
    }
    assert result.control_plan.cooling_pump_consumers == {}
    assert result.control_plan.cooling_actuator_ids == frozenset({"cooling-valve"})


def test_dew_point_and_condensation_margin_are_deterministic() -> None:
    """The Magnus calculation and reference margin remain stable and unit-testable."""
    dew_point = dew_point_celsius(25.0, 50.0)

    assert dew_point == pytest.approx(13.8516, abs=0.001)
    assert condensation_margin(18.0, dew_point) == pytest.approx(4.1484, abs=0.001)
    assert dew_point_celsius(25.0, 0.0) is None
    assert condensation_margin(18.0, float("nan")) is None


def test_cooling_start_and_stop_thresholds_are_explicit() -> None:
    """Cooling uses independent start and stop thresholds around the target."""
    plant = _cooling_plant()
    requested = evaluate(plant, _cooling_snapshot(temperature=24.6), RuntimeState(), NOW)
    held = evaluate(
        plant,
        _cooling_snapshot(temperature=24.2, observed_at=NOW + timedelta(seconds=1)),
        requested.next_runtime,
        NOW + timedelta(seconds=1),
    )
    stopped = evaluate(
        plant,
        _cooling_snapshot(temperature=23.7, supply=18.0, observed_at=NOW + timedelta(seconds=2)),
        held.next_runtime,
        NOW + timedelta(seconds=2),
    )

    assert requested.next_runtime.cooling_zone_demands["living"] is True
    assert (
        requested.diagnostics.cooling_zone_decisions["living"].status
        is ZoneDecisionStatus.REQUESTED
    )
    assert held.next_runtime.cooling_zone_demands["living"] is True
    assert stopped.next_runtime.cooling_zone_demands["living"] is False
    assert (
        stopped.diagnostics.cooling_zone_decisions["living"].status
        is ZoneDecisionStatus.SATISFIED
    )


def test_required_cooling_observations_block_when_missing_or_stale() -> None:
    """Temperature, humidity, and supply freshness all fail closed."""
    plant = _cooling_plant()
    missing_humidity = _cooling_snapshot()
    missing_humidity = PlantSnapshot(
        temperatures=missing_humidity.temperatures,
        supply_temperatures=missing_humidity.supply_temperatures,
    )
    humidity_blocked = evaluate(plant, missing_humidity, RuntimeState(), NOW)
    stale_supply = evaluate(
        plant,
        _cooling_snapshot(observed_at=NOW - timedelta(seconds=1801)),
        RuntimeState(),
        NOW,
    )

    assert humidity_blocked.next_runtime.cooling_zone_demands["living"] is False
    assert humidity_blocked.diagnostics.cooling_zone_decisions["living"].status is (
        ZoneDecisionStatus.SENSOR_BLOCKED
    )
    assert "humidity" in humidity_blocked.diagnostics.cooling_zone_reasons["living"].lower()
    assert stale_supply.next_runtime.cooling_zone_demands["living"] is False
    assert stale_supply.diagnostics.cooling_zone_decisions["living"].status is (
        ZoneDecisionStatus.SENSOR_BLOCKED
    )
    assert "stale" in stale_supply.diagnostics.cooling_zone_reasons["living"]


def test_cooling_blocks_before_condensation_margin_is_crossed() -> None:
    """A reference at the configured margin blocks before a risky request."""
    plant = _cooling_plant(margin=2.0)
    result = evaluate(
        plant,
        _cooling_snapshot(supply=15.0),
        RuntimeState(),
        NOW,
    )
    decision = result.diagnostics.cooling_zone_decisions["living"]

    assert result.next_runtime.cooling_zone_demands["living"] is False
    assert decision.status is ZoneDecisionStatus.SENSOR_BLOCKED
    assert decision.dew_point == pytest.approx(13.8516, abs=0.001)
    assert decision.condensation_margin == pytest.approx(1.1484, abs=0.001)
    assert any(
        interlock.status is InterlockStatus.BLOCKED
        for interlock in decision.interlocks
    )


def test_humidity_aggregation_excludes_optional_and_applies_calibration() -> None:
    """Humidity health follows required/optional and calibrated metadata semantics."""
    zone = Zone(
        "living",
        "Living",
        24.0,
        temperature_sensor_metadata=(TemperatureSensorMetadata("temperature.living"),),
        humidity_sensor_metadata=(
            TemperatureSensorMetadata("humidity.primary", calibration_offset=1.0),
            TemperatureSensorMetadata("humidity.backup", required=False),
        ),
    )
    result = aggregate_zone_humidity_result(
        zone,
        PlantSnapshot(
            temperatures={"temperature.living": TemperatureObservation(25.0, NOW)},
            humidities={
                "humidity.primary": TemperatureObservation(49.0, NOW),
                "humidity.backup": TemperatureObservation(float("inf"), NOW),
            },
        ),
        now=NOW,
    )

    assert result.value == 50.0
    assert result.excluded_optional_sensor_ids == ("humidity.backup",)
    assert result.blocking_required_sensor_ids == ()


def test_invalid_humidity_and_surface_reference_are_fail_closed() -> None:
    """Invalid humidity and stale surface references never permit cooling."""
    plant = _cooling_plant(supply=None, surface="temperature.surface")
    invalid_humidity = _cooling_snapshot(humidity=101.0)
    invalid_humidity = PlantSnapshot(
        temperatures=invalid_humidity.temperatures,
        humidities=invalid_humidity.humidities,
        surface_temperatures={"temperature.surface": TemperatureObservation(18.0, NOW)},
    )
    invalid = evaluate(plant, invalid_humidity, RuntimeState(), NOW)
    stale_surface = evaluate(
        plant,
        PlantSnapshot(
            temperatures={"temperature.living": TemperatureObservation(25.0, NOW)},
            humidities={"humidity.living": TemperatureObservation(50.0, NOW)},
            surface_temperatures={
                "temperature.surface": TemperatureObservation(18.0, NOW - timedelta(seconds=1801))
            },
        ),
        RuntimeState(),
        NOW,
    )

    assert "outside" in invalid.diagnostics.cooling_zone_reasons["living"]
    assert "surface" in stale_surface.diagnostics.cooling_zone_reasons["living"]
    assert stale_surface.next_runtime.cooling_zone_demands["living"] is False


def test_cooling_virtual_pump_sequence_is_reported_without_execution() -> None:
    """A ready cooling circuit requests the virtual pump through the pure plan only."""
    plant = _cooling_plant()
    opening = evaluate(plant, _cooling_snapshot(), RuntimeState(), NOW)
    ready = evaluate(
        plant,
        _cooling_snapshot(observed_at=NOW + timedelta(seconds=1)),
        opening.next_runtime,
        NOW + timedelta(seconds=1),
    )

    assert ready.next_runtime.plant_mode.value == "cooling"
    assert ready.next_runtime.pumps["pump"].state is PumpState.RUNNING
    assert ready.control_plan.cooling_pump_consumers == {"pump": frozenset({"cooling"})}
    assert [(command.actuator_id, command.action) for command in ready.control_plan.commands] == [
        ("pump", "turn_on")
    ]


def test_removing_demand_overruns_pump_before_closing_valve() -> None:
    plant = compile_topology(_plant())
    opening = evaluate(plant, _snapshot(20.0), RuntimeState(), NOW)
    running = evaluate(plant, _snapshot(20.0), opening.next_runtime, NOW + timedelta(seconds=30))

    overrun = evaluate(plant, _snapshot(22.0), running.next_runtime, NOW + timedelta(seconds=31))
    stopped = evaluate(plant, _snapshot(22.0), overrun.next_runtime, NOW + timedelta(seconds=151))

    assert overrun.next_runtime.pumps["pump.floor"].state is PumpState.OVERRUN
    assert overrun.next_runtime.valves["valve.floor"].state is ValveState.OPEN
    assert [command.action for command in overrun.control_plan.commands] == []
    assert stopped.next_runtime.pumps["pump.floor"].state is PumpState.OFF
    assert stopped.next_runtime.valves["valve.floor"].state is ValveState.CLOSED
    assert {(command.actuator_id, command.action) for command in stopped.control_plan.commands} == {
        ("pump.floor", "turn_off"),
        ("valve.floor", "close"),
    }


def test_restored_actuator_without_timestamp_is_conservative() -> None:
    """Restored opening and overrun states do not assume their durations elapsed."""
    plant = compile_topology(_plant())
    snapshot = _snapshot(20.0)
    runtime = RuntimeState(
        valves={"valve.floor": ValveRuntime(ValveState.OPENING, None)},
        pumps={"pump.floor": PumpRuntime(PumpState.OVERRUN, None)},
    )

    result = evaluate(plant, snapshot, runtime, NOW)

    assert result.next_runtime.valves["valve.floor"].state is ValveState.OPENING
    assert result.next_runtime.pumps["pump.floor"].state is PumpState.OVERRUN


def test_shared_pump_remains_running_when_one_consumer_releases_demand() -> None:
    plant = compile_topology(
        PlantConfiguration(
            id="plant",
            zones=(
                Zone("living", "Living", 21.0, ("temperature.living",)),
                Zone("office", "Office", 21.0, ("temperature.office",)),
            ),
            valves=(
                Valve("valve.floor", "Floor valve", "switch.floor_valve", 1),
                Valve("valve.office", "Office valve", "switch.office_valve", 1),
            ),
            pumps=(Pump("pump.shared", "Shared pump", "switch.shared_pump"),),
            circuits=(
                Circuit(
                    "floor",
                    "Floor",
                    ("valve.floor",),
                    "pump.shared",
                ),
                Circuit(
                    "office",
                    "Office",
                    ("valve.office",),
                    "pump.shared",
                ),
            ),
            routes=(
                DeliveryRoute("living-floor", "living", "floor"),
                DeliveryRoute("office-circuit", "office", "office"),
            ),
        )
    )
    cool_both = PlantSnapshot(
        {
            "temperature.living": TemperatureObservation(20.0, NOW),
            "temperature.office": TemperatureObservation(20.0, NOW),
        }
    )
    only_office = PlantSnapshot(
        {
            "temperature.living": TemperatureObservation(22.0, NOW),
            "temperature.office": TemperatureObservation(20.0, NOW),
        }
    )
    opening = evaluate(plant, cool_both, RuntimeState(), NOW)
    running = evaluate(plant, cool_both, opening.next_runtime, NOW + timedelta(seconds=1))
    released = evaluate(plant, only_office, running.next_runtime, NOW + timedelta(seconds=2))

    assert released.next_runtime.pumps["pump.shared"].state is PumpState.RUNNING
    assert released.control_plan.pump_consumers["pump.shared"] == frozenset({"office"})
    assert all(command.actuator_id != "pump.shared" for command in released.control_plan.commands)


def test_shared_valve_and_pump_remain_active_until_last_consumer_releases() -> None:
    """One zone releasing must not stop equipment still owned by another circuit."""
    plant = compile_topology(
        PlantConfiguration(
            id="plant",
            zones=(
                Zone("living", "Living", 21.0, ("temperature.living",)),
                Zone("office", "Office", 21.0, ("temperature.office",)),
            ),
            valves=(Valve("shared", "Shared valve", "switch.shared_valve", 1),),
            pumps=(Pump("pump", "Shared pump", "switch.shared_pump", 10),),
            circuits=(
                Circuit("floor", "Floor", ("shared",), "pump"),
                Circuit("ceiling", "Ceiling", ("shared",), "pump"),
            ),
            routes=(
                DeliveryRoute("living-floor", "living", "floor"),
                DeliveryRoute("office-ceiling", "office", "ceiling"),
            ),
        )
    )
    both_request = PlantSnapshot(
        {
            "temperature.living": TemperatureObservation(20.0, NOW),
            "temperature.office": TemperatureObservation(20.0, NOW),
        }
    )
    only_office = PlantSnapshot(
        {
            "temperature.living": TemperatureObservation(22.0, NOW),
            "temperature.office": TemperatureObservation(20.0, NOW),
        }
    )
    neither_requests = PlantSnapshot(
        {
            "temperature.living": TemperatureObservation(22.0, NOW),
            "temperature.office": TemperatureObservation(22.0, NOW),
        }
    )

    opening = evaluate(plant, both_request, RuntimeState(), NOW)
    running = evaluate(plant, both_request, opening.next_runtime, NOW + timedelta(seconds=1))
    one_released = evaluate(plant, only_office, running.next_runtime, NOW + timedelta(seconds=2))
    overrun = evaluate(
        plant, neither_requests, one_released.next_runtime, NOW + timedelta(seconds=3)
    )
    stopped = evaluate(plant, neither_requests, overrun.next_runtime, NOW + timedelta(seconds=13))

    assert one_released.control_plan.valve_consumers == {"shared": frozenset({"ceiling"})}
    assert one_released.control_plan.pump_consumers == {"pump": frozenset({"ceiling"})}
    assert one_released.next_runtime.valves["shared"].state is ValveState.OPEN
    assert one_released.next_runtime.pumps["pump"].state is PumpState.RUNNING
    assert one_released.control_plan.commands == ()
    assert overrun.next_runtime.valves["shared"].state is ValveState.OPEN
    assert overrun.next_runtime.pumps["pump"].state is PumpState.OVERRUN
    assert stopped.next_runtime.valves["shared"].state is ValveState.CLOSED
    assert stopped.next_runtime.pumps["pump"].state is PumpState.OFF


def test_one_zone_requests_every_enabled_delivery_route() -> None:
    """Heating-only arbitration retains the legacy any-demand behavior."""
    plant = compile_topology(
        PlantConfiguration(
            id="plant",
            zones=(Zone("living", "Living", 21.0, ("temperature.living",)),),
            valves=(
                Valve("floor-valve", "Floor valve", "switch.floor_valve", 1),
                Valve("ceiling-valve", "Ceiling valve", "switch.ceiling_valve", 1),
            ),
            pumps=(
                Pump("floor-pump", "Floor pump", "switch.floor_pump"),
                Pump("ceiling-pump", "Ceiling pump", "switch.ceiling_pump"),
            ),
            circuits=(
                Circuit("floor", "Floor", ("floor-valve",), "floor-pump"),
                Circuit("ceiling", "Ceiling", ("ceiling-valve",), "ceiling-pump"),
            ),
            routes=(
                DeliveryRoute("living-floor", "living", "floor"),
                DeliveryRoute("living-ceiling", "living", "ceiling"),
            ),
        )
    )

    opening = evaluate(plant, _snapshot(20.0), RuntimeState(), NOW)
    ready = evaluate(plant, _snapshot(20.0), opening.next_runtime, NOW + timedelta(seconds=1))

    assert opening.control_plan.valve_consumers == {
        "floor-valve": frozenset({"floor"}),
        "ceiling-valve": frozenset({"ceiling"}),
    }
    assert ready.control_plan.pump_consumers == {
        "floor-pump": frozenset({"floor"}),
        "ceiling-pump": frozenset({"ceiling"}),
    }
    assert opening.diagnostics.circuit_reasons["floor"] == (
        "Waiting for valve readiness after eligible delivery route living-floor "
        "requested this circuit."
    )


def test_unchanged_running_snapshot_produces_no_new_commands() -> None:
    plant = compile_topology(_plant())
    opening = evaluate(plant, _snapshot(20.0), RuntimeState(), NOW)
    running = evaluate(plant, _snapshot(20.0), opening.next_runtime, NOW + timedelta(seconds=30))

    unchanged = evaluate(plant, _snapshot(20.0), running.next_runtime, NOW + timedelta(seconds=31))

    assert unchanged.control_plan.commands == ()


def test_zone_demand_blocks_when_a_legacy_sensor_exceeds_default_maximum_age() -> None:
    """Legacy sensors use the frozen 1800-second freshness default."""
    plant = compile_topology(
        PlantConfiguration(
            id="plant",
            zones=(
                Zone(
                    "living",
                    "Living",
                    21.0,
                    temperature_sensors=(
                        "temperature.living_wall",
                        "temperature.living_window",
                    ),
                ),
            ),
            valves=(Valve("valve.floor", "Floor valve", "switch.floor_valve", 30),),
            pumps=(Pump("pump.floor", "Floor pump", "switch.floor_pump", 120),),
            circuits=(Circuit("floor", "Floor", ("valve.floor",), "pump.floor"),),
            routes=(DeliveryRoute("living-floor", "living", "floor"),),
        )
    )
    snapshot = PlantSnapshot(
        {
            "temperature.living_wall": TemperatureObservation(19.0, NOW - timedelta(hours=12)),
            "temperature.living_window": TemperatureObservation(21.0, NOW),
        }
    )

    result = evaluate(plant, snapshot, RuntimeState(), NOW)

    assert result.next_runtime.zone_demands == {"living": False}
    assert result.diagnostics.zone_decisions["living"].status is ZoneDecisionStatus.SENSOR_BLOCKED
    assert "stale" in result.diagnostics.zone_reasons["living"]


@pytest.mark.parametrize("unusable_value", [None, math.nan, math.inf, -math.inf])
def test_zone_blocks_when_any_required_temperature_sensor_is_unusable(
    unusable_value: float | None,
) -> None:
    """A missing required reading must not be silently dropped from the mean."""
    plant = compile_topology(
        PlantConfiguration(
            id="plant",
            zones=(
                Zone(
                    "living",
                    "Living",
                    21.0,
                    ("temperature.living_wall", "temperature.living_window"),
                ),
            ),
            valves=(Valve("valve.floor", "Floor valve", "switch.floor_valve", 30),),
            pumps=(Pump("pump.floor", "Floor pump", "switch.floor_pump", 120),),
            circuits=(Circuit("floor", "Floor", ("valve.floor",), "pump.floor"),),
            routes=(DeliveryRoute("living-floor", "living", "floor"),),
        )
    )
    snapshot = PlantSnapshot(
        {
            "temperature.living_wall": TemperatureObservation(19.0, NOW),
            "temperature.living_window": TemperatureObservation(unusable_value, NOW),
        }
    )

    result = evaluate(plant, snapshot, RuntimeState(), NOW)

    assert result.next_runtime.zone_demands == {"living": False}
    assert result.diagnostics.zone_reasons["living"].startswith("Blocked:")


def test_circuit_waits_for_every_series_valve_before_pump_request() -> None:
    """A circuit is ready only after all of its required valves are open."""
    plant = compile_topology(
        PlantConfiguration(
            id="plant",
            zones=(Zone("living", "Living", 21.0, ("temperature.living",)),),
            valves=(
                Valve("supply", "Supply valve", "switch.supply_valve", 10),
                Valve("return", "Return valve", "switch.return_valve", 30),
            ),
            pumps=(Pump("pump", "Circulation pump", "switch.circulation_pump", 120),),
            circuits=(Circuit("floor", "Floor", ("supply", "return"), "pump"),),
            routes=(DeliveryRoute("living-floor", "living", "floor"),),
        )
    )

    opening = evaluate(plant, _snapshot(20.0), RuntimeState(), NOW)
    one_ready = evaluate(plant, _snapshot(20.0), opening.next_runtime, NOW + timedelta(seconds=10))
    all_ready = evaluate(
        plant, _snapshot(20.0), one_ready.next_runtime, NOW + timedelta(seconds=30)
    )

    assert one_ready.next_runtime.valves["supply"].state is ValveState.OPEN
    assert one_ready.next_runtime.valves["return"].state is ValveState.OPENING
    assert one_ready.next_runtime.pumps["pump"].state is PumpState.OFF
    assert all_ready.next_runtime.pumps["pump"].state is PumpState.RUNNING
    commands = [
        (command.actuator_id, command.action) for command in all_ready.control_plan.commands
    ]
    assert commands == [("pump", "turn_on")]
