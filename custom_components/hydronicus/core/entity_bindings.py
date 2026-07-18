"""Pure inventory and degraded-mode handling for configured entity bindings."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum

from .model import CompiledPlant


class BindingCategory(StrEnum):
    """Safety category for one configured Home Assistant entity reference."""

    SENSOR = "sensor"
    FEEDBACK = "feedback"
    ACTUATOR = "actuator"


@dataclass(frozen=True, slots=True)
class EntityBinding:
    """One topology-owned entity reference without any Home Assistant dependency."""

    category: BindingCategory
    object_type: str
    object_id: str
    object_name: str
    binding_key: str
    label: str
    entity_id: str
    circuit_ids: tuple[str, ...] = ()
    zone_ids: tuple[str, ...] = ()
    actuator_id: str | None = None
    required: bool = True


def configured_entity_bindings(plant: CompiledPlant) -> tuple[EntityBinding, ...]:
    """Return every configured entity reference in stable topology order."""
    circuit_zone_ids: dict[str, tuple[str, ...]] = {
        circuit_id: tuple(
            sorted({route.zone_id for route in plant.routes if route.circuit_id == circuit_id})
        )
        for circuit_id in plant.circuits
    }
    bindings: list[EntityBinding] = []

    for zone_id in sorted(plant.zones):
        zone = plant.zones[zone_id]
        zone_circuit_ids = tuple(
            sorted({route.circuit_id for route in plant.routes if route.zone_id == zone_id})
        )
        for index, sensor in enumerate(zone.sensor_metadata):
            bindings.append(
                EntityBinding(
                    BindingCategory.SENSOR,
                    "zone",
                    zone.id,
                    zone.name,
                    f"temperature_sensor_{index}",
                    "temperature sensor",
                    sensor.entity_id,
                    zone_circuit_ids,
                    (zone.id,),
                    required=sensor.required,
                )
            )
        for index, sensor in enumerate(zone.humidity_sensor_metadata):
            bindings.append(
                EntityBinding(
                    BindingCategory.SENSOR,
                    "zone",
                    zone.id,
                    zone.name,
                    f"humidity_sensor_{index}",
                    "humidity sensor",
                    sensor.entity_id,
                    zone_circuit_ids,
                    (zone.id,),
                    required=sensor.required,
                )
            )

    for circuit_id in sorted(plant.circuits):
        circuit = plant.circuits[circuit_id]
        circuit_zones = circuit_zone_ids[circuit.id]
        if circuit.supply_temperature_sensor is not None:
            bindings.append(
                EntityBinding(
                    BindingCategory.SENSOR,
                    "circuit",
                    circuit.id,
                    circuit.name,
                    "supply_temperature_sensor",
                    "supply temperature reference",
                    circuit.supply_temperature_sensor,
                    (circuit.id,),
                    circuit_zones,
                    required=circuit.cooling_enabled,
                )
            )
        if circuit.surface_temperature_sensor is not None:
            bindings.append(
                EntityBinding(
                    BindingCategory.SENSOR,
                    "circuit",
                    circuit.id,
                    circuit.name,
                    "surface_temperature_sensor",
                    "surface temperature reference",
                    circuit.surface_temperature_sensor,
                    (circuit.id,),
                    circuit_zones,
                    required=circuit.cooling_enabled,
                )
            )

    for valve_id in sorted(plant.valves):
        valve = plant.valves[valve_id]
        circuit_ids = tuple(
            sorted(
                circuit.id for circuit in plant.circuits.values() if valve.id in circuit.valve_ids
            )
        )
        zone_ids = tuple(
            sorted(
                {zone_id for circuit_id in circuit_ids for zone_id in circuit_zone_ids[circuit_id]}
            )
        )
        bindings.append(
            EntityBinding(
                BindingCategory.ACTUATOR,
                "valve",
                valve.id,
                valve.name,
                "actuator",
                "valve actuator",
                valve.entity_id,
                circuit_ids,
                zone_ids,
                actuator_id=valve.id,
            )
        )
        if valve.readiness_entity_id is not None:
            bindings.append(
                EntityBinding(
                    BindingCategory.FEEDBACK,
                    "valve",
                    valve.id,
                    valve.name,
                    "readiness_feedback",
                    "valve readiness feedback",
                    valve.readiness_entity_id,
                    circuit_ids,
                    zone_ids,
                    actuator_id=valve.id,
                )
            )
        if valve.position_entity_id is not None:
            bindings.append(
                EntityBinding(
                    BindingCategory.FEEDBACK,
                    "valve",
                    valve.id,
                    valve.name,
                    "position_feedback",
                    "valve position feedback",
                    valve.position_entity_id,
                    circuit_ids,
                    zone_ids,
                    actuator_id=valve.id,
                )
            )

    for pump_id in sorted(plant.pumps):
        pump = plant.pumps[pump_id]
        circuit_ids = tuple(
            sorted(circuit.id for circuit in plant.circuits.values() if circuit.pump_id == pump.id)
        )
        zone_ids = tuple(
            sorted(
                {zone_id for circuit_id in circuit_ids for zone_id in circuit_zone_ids[circuit_id]}
            )
        )
        bindings.append(
            EntityBinding(
                BindingCategory.ACTUATOR,
                "pump",
                pump.id,
                pump.name,
                "actuator",
                "pump actuator",
                pump.entity_id,
                circuit_ids,
                zone_ids,
                actuator_id=pump.id,
            )
        )
        for binding_key, label, entity_id in (
            ("power_feedback", "pump power feedback", pump.power_entity_id),
            ("flow_feedback", "pump flow feedback", pump.flow_entity_id),
            ("fault_feedback", "pump fault feedback", pump.fault_entity_id),
        ):
            if entity_id is None:
                continue
            bindings.append(
                EntityBinding(
                    BindingCategory.FEEDBACK,
                    "pump",
                    pump.id,
                    pump.name,
                    binding_key,
                    label,
                    entity_id,
                    circuit_ids,
                    zone_ids,
                    actuator_id=pump.id,
                )
            )

    for source_id in sorted(plant.sources):
        source = plant.sources[source_id]
        for binding_key, label, entity_id, category, actuator_id in (
            (
                "availability_sensor",
                "source availability sensor",
                source.availability_entity_id,
                BindingCategory.SENSOR,
                None,
            ),
            (
                "temperature_sensor",
                "source temperature sensor",
                source.temperature_entity_id,
                BindingCategory.SENSOR,
                None,
            ),
            (
                "demand_actuator",
                "source demand actuator",
                source.demand_entity_id,
                BindingCategory.ACTUATOR,
                f"source:{source.id}",
            ),
        ):
            if entity_id is None:
                continue
            bindings.append(
                EntityBinding(
                    category,
                    "source",
                    source.id,
                    source.name,
                    binding_key,
                    label,
                    entity_id,
                    actuator_id=actuator_id,
                )
            )

    return tuple(bindings)


def unresolved_entity_bindings(
    plant: CompiledPlant, resolved_entity_ids: Collection[str]
) -> tuple[EntityBinding, ...]:
    """Return configured bindings whose entity IDs do not currently resolve."""
    resolved = frozenset(resolved_entity_ids)
    return tuple(
        binding
        for binding in configured_entity_bindings(plant)
        if binding.entity_id not in resolved
    )


def degraded_circuit_ids(
    plant: CompiledPlant, unresolved_entity_ids: Collection[str]
) -> frozenset[str]:
    """Return circuits that cannot safely run through an unresolved path."""
    unresolved = frozenset(unresolved_entity_ids)
    return frozenset(
        circuit_id
        for binding in configured_entity_bindings(plant)
        if binding.category in {BindingCategory.ACTUATOR, BindingCategory.FEEDBACK}
        and binding.entity_id in unresolved
        for circuit_id in binding.circuit_ids
    )


def degraded_actuator_ids(
    plant: CompiledPlant, unresolved_entity_ids: Collection[str]
) -> frozenset[str]:
    """Return controller actuator IDs whose primary binding is unresolved."""
    unresolved = frozenset(unresolved_entity_ids)
    return frozenset(
        binding.actuator_id
        for binding in configured_entity_bindings(plant)
        if binding.category is BindingCategory.ACTUATOR
        and binding.entity_id in unresolved
        and binding.actuator_id is not None
    )
