"""Deterministic topology binding and degraded-mode tests."""

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from custom_components.hydronicus.core.controller import evaluate
from custom_components.hydronicus.core.entity_bindings import (
    BindingCategory,
    configured_entity_bindings,
    degraded_circuit_ids,
    unresolved_entity_bindings,
)
from custom_components.hydronicus.core.model import (
    Circuit,
    DeliveryRoute,
    PlantConfiguration,
    PlantSnapshot,
    Pump,
    RuntimeState,
    TemperatureObservation,
    Valve,
    Zone,
)
from custom_components.hydronicus.core.topology import compile_topology

NOW = datetime(2026, 7, 18, tzinfo=UTC)
ZONE_A = "zone-a"
ZONE_B = "zone-b"
VALVE_A = "valve-a"
VALVE_B = "valve-b"
PUMP_A = "pump-a"
PUMP_B = "pump-b"
CIRCUIT_A = "circuit-a"
CIRCUIT_B = "circuit-b"


def _plant():
    return compile_topology(
        PlantConfiguration(
            id="plant",
            zones=(
                Zone(ZONE_A, "Zone A", 21.0, ("sensor.zone_a",)),
                Zone(ZONE_B, "Zone B", 21.0, ("sensor.zone_b",)),
            ),
            valves=(
                Valve(VALVE_A, "Valve A", "switch.valve_a"),
                Valve(VALVE_B, "Valve B", "switch.valve_b"),
            ),
            pumps=(
                Pump(PUMP_A, "Pump A", "switch.pump_a"),
                Pump(PUMP_B, "Pump B", "switch.pump_b"),
            ),
            circuits=(
                Circuit(CIRCUIT_A, "Circuit A", (VALVE_A,), PUMP_A),
                Circuit(CIRCUIT_B, "Circuit B", (VALVE_B,), PUMP_B),
            ),
            routes=(
                DeliveryRoute("route-a", ZONE_A, CIRCUIT_A),
                DeliveryRoute("route-b", ZONE_B, CIRCUIT_B),
            ),
        )
    )


def test_binding_inventory_is_stable_and_keeps_categories_distinct() -> None:
    plant = _plant()

    bindings = configured_entity_bindings(plant)

    assert [(binding.category, binding.binding_key) for binding in bindings] == [
        (BindingCategory.SENSOR, "temperature_sensor_0"),
        (BindingCategory.SENSOR, "temperature_sensor_0"),
        (BindingCategory.ACTUATOR, "actuator"),
        (BindingCategory.ACTUATOR, "actuator"),
        (BindingCategory.ACTUATOR, "actuator"),
        (BindingCategory.ACTUATOR, "actuator"),
    ]
    assert bindings[0].zone_ids == (ZONE_A,)
    assert bindings[2].circuit_ids == (CIRCUIT_A,)


def test_unresolved_actuator_blocks_only_its_circuit() -> None:
    plant = _plant()
    resolved = {
        "sensor.zone_a",
        "sensor.zone_b",
        "switch.valve_b",
        "switch.pump_a",
        "switch.pump_b",
    }

    unresolved = unresolved_entity_bindings(plant, resolved)

    assert [(binding.category, binding.object_id) for binding in unresolved] == [
        (BindingCategory.ACTUATOR, VALVE_A)
    ]
    assert degraded_circuit_ids(plant, {"switch.valve_a"}) == frozenset({CIRCUIT_A})

    evaluation = evaluate(
        plant,
        PlantSnapshot(
            temperatures={
                "sensor.zone_a": TemperatureObservation(18.0, NOW),
                "sensor.zone_b": TemperatureObservation(18.0, NOW),
            },
            unavailable_entity_ids=frozenset({"switch.valve_a"}),
        ),
        runtime=RuntimeState(),
        now=NOW,
    )

    assert evaluation.next_runtime.zone_demands == {ZONE_A: False, ZONE_B: True}
    assert evaluation.control_plan.valve_consumers == {VALVE_B: frozenset({CIRCUIT_B})}
    assert all(
        command.actuator_id not in {VALVE_A, PUMP_A} for command in evaluation.control_plan.commands
    )
    assert "unresolved actuator" in evaluation.diagnostics.zone_reasons[ZONE_A]


@given(missing_entity=st.sampled_from(("switch.valve_a", "switch.pump_a")))
def test_any_unresolved_primary_path_fails_closed(missing_entity: str) -> None:
    """The circuit remains blocked regardless of which primary binding disappeared."""
    evaluation = evaluate(
        _plant(),
        PlantSnapshot(
            temperatures={
                "sensor.zone_a": TemperatureObservation(18.0, NOW),
                "sensor.zone_b": TemperatureObservation(18.0, NOW),
            },
            unavailable_entity_ids=frozenset({missing_entity}),
        ),
        RuntimeState(),
        NOW,
    )

    assert evaluation.next_runtime.zone_demands[ZONE_A] is False
    assert all(
        command.actuator_id not in {VALVE_A, PUMP_A} for command in evaluation.control_plan.commands
    )
