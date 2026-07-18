"""Tests for decoding persisted plant topology."""

from __future__ import annotations

import pytest
from hydronicus_core.configuration import (
    StoredTopologyError,
    plant_configuration_from_entry_data,
)
from hydronicus_core.model import TemperatureAggregation


def test_existing_circuits_default_to_heating_only() -> None:
    """Omitted cooling compatibility remains disabled for legacy persisted circuits."""
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": "plant-1",
            "topology": {
                "zones": [
                    {
                        "id": "zone-1",
                        "name": "Living room",
                        "target_temperature": 21.5,
                        "temperature_sensor": "sensor.living_temperature",
                    }
                ],
                "circuits": [
                    {
                        "id": "circuit-1",
                        "name": "Floor loop",
                        "valve_id": "valve-1",
                        "pump_id": "pump-1",
                    }
                ],
                "routes": [{"id": "route-1", "zone_id": "zone-1", "circuit_id": "circuit-1"}],
            },
        }
    )

    assert plant.circuits[0].cooling_enabled is False


def test_cooling_compatibility_requires_a_persisted_boolean() -> None:
    """Malformed stored mode flags cannot silently enable cooling."""
    with pytest.raises(StoredTopologyError, match="cooling_enabled.*boolean"):
        plant_configuration_from_entry_data(
            {
                "plant_id": "plant-1",
                "topology": {
                    "zones": [
                        {
                            "id": "zone-1",
                            "name": "Living room",
                            "target_temperature": 21.5,
                            "temperature_sensor": "sensor.living_temperature",
                        }
                    ],
                    "circuits": [
                        {
                            "id": "circuit-1",
                            "name": "Floor loop",
                            "valve_id": "valve-1",
                            "pump_id": "pump-1",
                            "cooling_enabled": "false",
                        }
                    ],
                    "routes": [{"id": "route-1", "zone_id": "zone-1", "circuit_id": "circuit-1"}],
                },
            }
        )


def test_decodes_initial_shadow_topology_from_config_entry_data() -> None:
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": "plant-1",
            "topology": {
                "zones": [
                    {
                        "id": "zone-1",
                        "name": "Living room",
                        "target_temperature": 21.5,
                        "temperature_sensor": "sensor.living_temperature",
                    }
                ],
                "circuits": [
                    {
                        "id": "circuit-1",
                        "name": "Floor loop",
                        "valve_id": "valve.floor_loop",
                        "pump_id": "switch.floor_pump",
                        "valve_opening_time_seconds": 45,
                        "pump_overrun_seconds": 180,
                        "position_feedback_entity": "sensor.floor_valve_position",
                        "position_feedback_max_age_seconds": 90,
                        "power_feedback_entity": "sensor.floor_pump_power",
                        "power_feedback_max_age_seconds": 91,
                        "flow_feedback_entity": "sensor.floor_flow",
                        "flow_feedback_max_age_seconds": 92,
                        "fault_feedback_entity": "binary_sensor.floor_pump_fault",
                        "fault_feedback_max_age_seconds": 93,
                    }
                ],
                "routes": [{"id": "route-1", "zone_id": "zone-1", "circuit_id": "circuit-1"}],
            },
        }
    )

    assert plant.id == "plant-1"
    assert plant.zones[0].target_temperature == 21.5
    assert plant.zones[0].temperature_sensors == ("sensor.living_temperature",)
    assert plant.valves[0].opening_time_seconds == 45
    assert plant.pumps[0].overrun_seconds == 180
    assert plant.valves[0].position_entity_id == "sensor.floor_valve_position"
    assert plant.valves[0].position_max_age_seconds == 90
    assert plant.pumps[0].power_entity_id == "sensor.floor_pump_power"
    assert plant.pumps[0].power_max_age_seconds == 91
    assert plant.pumps[0].flow_entity_id == "sensor.floor_flow"
    assert plant.pumps[0].flow_max_age_seconds == 92
    assert plant.pumps[0].fault_entity_id == "binary_sensor.floor_pump_fault"
    assert plant.pumps[0].fault_max_age_seconds == 93
    assert plant.circuits[0].valve_ids == ("valve.floor_loop",)
    assert plant.routes[0].zone_id == "zone-1"


def test_decodes_configured_valve_readiness_feedback() -> None:
    """Persisted readiness feedback is retained on the first-class valve model."""
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": "00000000-0000-4000-8000-000000000001",
            "topology": {
                "zones": [
                    {
                        "id": "00000000-0000-4000-8000-000000000002",
                        "name": "Zone",
                        "target_temperature": 21.0,
                        "temperature_sensor": "sensor.zone",
                    }
                ],
                "valves": [
                    {
                        "id": "00000000-0000-4000-8000-000000000003",
                        "name": "Valve",
                        "entity_id": "switch.valve",
                        "readiness_entity_id": "binary_sensor.valve_ready",
                    }
                ],
                "pumps": [
                    {
                        "id": "00000000-0000-4000-8000-000000000004",
                        "name": "Pump",
                        "entity_id": "switch.pump",
                    }
                ],
                "circuits": [
                    {
                        "id": "00000000-0000-4000-8000-000000000005",
                        "name": "Circuit",
                        "valve_ids": ["00000000-0000-4000-8000-000000000003"],
                        "pump_id": "00000000-0000-4000-8000-000000000004",
                    }
                ],
                "routes": [
                    {
                        "id": "00000000-0000-4000-8000-000000000006",
                        "zone_id": "00000000-0000-4000-8000-000000000002",
                        "circuit_id": "00000000-0000-4000-8000-000000000005",
                    }
                ],
            },
        }
    )

    assert plant.valves[0].readiness_entity_id == "binary_sensor.valve_ready"


def test_rejects_nonboolean_route_enablement() -> None:
    """A malformed flag must not silently enable a delivery route."""
    with pytest.raises(StoredTopologyError, match="route enabled must be a boolean"):
        plant_configuration_from_entry_data(
            {
                "plant_id": "plant-1",
                "topology": {
                    "routes": [
                        {
                            "id": "route-1",
                            "zone_id": "zone-1",
                            "circuit_id": "circuit-1",
                            "enabled": "false",
                        }
                    ]
                },
            }
        )


def test_decodes_temperature_sensor_list_from_config_entry_data() -> None:
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": "plant-1",
            "topology": {
                "zones": [
                    {
                        "id": "zone-1",
                        "name": "Living room",
                        "target_temperature": 21.5,
                        "temperature_sensors": [
                            "sensor.living_temperature",
                            "sensor.living_temperature_backup",
                        ],
                    }
                ],
                "circuits": [],
                "routes": [],
            },
        }
    )

    assert plant.zones[0].temperature_sensors == (
        "sensor.living_temperature",
        "sensor.living_temperature_backup",
    )


def test_decodes_zone_temperature_aggregation_from_config_entry_data() -> None:
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": "plant-1",
            "topology": {
                "zones": [
                    {
                        "id": "zone-1",
                        "name": "Living room",
                        "target_temperature": 21.5,
                        "temperature_sensors": [
                            "sensor.living_temperature",
                            "sensor.living_temperature_backup",
                        ],
                        "temperature_aggregation": "weighted_mean",
                        "temperature_sensor_weights": {
                            "sensor.living_temperature": 1,
                            "sensor.living_temperature_backup": 3,
                        },
                    }
                ],
                "circuits": [],
                "routes": [],
            },
        }
    )

    assert plant.zones[0].aggregation is TemperatureAggregation.WEIGHTED_MEAN
    assert plant.zones[0].temperature_sensor_weights == {
        "sensor.living_temperature": 1.0,
        "sensor.living_temperature_backup": 3.0,
    }


def test_legacy_zone_temperature_aggregation_defaults_to_mean() -> None:
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": "plant-1",
            "topology": {
                "zones": [
                    {
                        "id": "zone-1",
                        "name": "Living room",
                        "target_temperature": 21.5,
                        "temperature_sensor": "sensor.living_temperature",
                    }
                ],
                "circuits": [],
                "routes": [],
            },
        }
    )

    assert plant.zones[0].aggregation is TemperatureAggregation.MEAN


def test_rejects_unknown_zone_temperature_aggregation() -> None:
    with pytest.raises(StoredTopologyError, match="temperature_aggregation"):
        plant_configuration_from_entry_data(
            {
                "plant_id": "plant-1",
                "topology": {
                    "zones": [
                        {
                            "id": "zone-1",
                            "name": "Living room",
                            "target_temperature": 21.5,
                            "temperature_sensor": "sensor.living_temperature",
                            "temperature_aggregation": "trimmed_mean",
                        }
                    ],
                    "circuits": [],
                    "routes": [],
                },
            }
        )


def test_rejects_zone_weight_for_unknown_sensor() -> None:
    with pytest.raises(StoredTopologyError, match="unknown sensors"):
        plant_configuration_from_entry_data(
            {
                "plant_id": "plant-1",
                "topology": {
                    "zones": [
                        {
                            "id": "zone-1",
                            "name": "Living room",
                            "target_temperature": 21.5,
                            "temperature_sensor": "sensor.living_temperature",
                            "temperature_sensor_weights": {
                                "sensor.other": 2,
                            },
                        }
                    ],
                    "circuits": [],
                    "routes": [],
                },
            }
        )


@pytest.mark.parametrize("weight", [0, -1, float("inf"), "invalid"])
def test_rejects_non_positive_or_invalid_zone_weight(weight) -> None:
    with pytest.raises(StoredTopologyError, match="must be"):
        plant_configuration_from_entry_data(
            {
                "plant_id": "plant-1",
                "topology": {
                    "zones": [
                        {
                            "id": "zone-1",
                            "name": "Living room",
                            "target_temperature": 21.5,
                            "temperature_sensor": "sensor.living_temperature",
                            "temperature_sensor_weights": {
                                "sensor.living_temperature": weight,
                            },
                        }
                    ],
                    "circuits": [],
                    "routes": [],
                },
            }
        )


def test_rejects_missing_required_persisted_topology_field() -> None:
    with pytest.raises(StoredTopologyError, match="temperature_sensor"):
        plant_configuration_from_entry_data(
            {
                "plant_id": "plant-1",
                "topology": {
                    "zones": [{"id": "zone-1", "name": "Living room", "target_temperature": 21}],
                    "circuits": [],
                    "routes": [],
                },
            }
        )


def test_decodes_first_class_actuator_nodes_from_entry_data() -> None:
    """Persisted circuits should reference topology actuators by stable ids."""
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": "00000000-0000-4000-8000-000000000001",
            "topology": {
                "zones": [
                    {
                        "id": "00000000-0000-4000-8000-000000000002",
                        "name": "Living room",
                        "target_temperature": 21.5,
                        "temperature_sensor": "sensor.living_temperature",
                    }
                ],
                "valves": [
                    {
                        "id": "00000000-0000-4000-8000-000000000003",
                        "name": "Supply valve",
                        "entity_id": "switch.supply_valve",
                        "opening_time_seconds": 45,
                        "position_feedback_entity": "sensor.supply_position",
                        "position_feedback_max_age_seconds": 95,
                    },
                    {
                        "id": "00000000-0000-4000-8000-000000000004",
                        "name": "Return valve",
                        "entity_id": "switch.return_valve",
                        "opening_time_seconds": 60,
                    },
                ],
                "pumps": [
                    {
                        "id": "00000000-0000-4000-8000-000000000005",
                        "name": "Circulation pump",
                        "entity_id": "switch.circulation_pump",
                        "overrun_seconds": 180,
                        "power_feedback_entity": "sensor.circulation_power",
                        "power_feedback_max_age_seconds": 96,
                        "flow_feedback_entity": "sensor.circulation_flow",
                        "flow_feedback_max_age_seconds": 97,
                        "fault_feedback_entity": "binary_sensor.circulation_fault",
                        "fault_feedback_max_age_seconds": 98,
                    }
                ],
                "circuits": [
                    {
                        "id": "00000000-0000-4000-8000-000000000006",
                        "name": "Floor loop",
                        "valve_ids": [
                            "00000000-0000-4000-8000-000000000003",
                            "00000000-0000-4000-8000-000000000004",
                        ],
                        "pump_id": "00000000-0000-4000-8000-000000000005",
                    }
                ],
                "routes": [
                    {
                        "id": "00000000-0000-4000-8000-000000000007",
                        "zone_id": "00000000-0000-4000-8000-000000000002",
                        "circuit_id": "00000000-0000-4000-8000-000000000006",
                    }
                ],
            },
        }
    )

    assert plant.valves[0].entity_id == "switch.supply_valve"
    assert plant.valves[1].opening_time_seconds == 60
    assert plant.valves[0].position_entity_id == "sensor.supply_position"
    assert plant.valves[0].position_max_age_seconds == 95
    assert plant.pumps[0].entity_id == "switch.circulation_pump"
    assert plant.pumps[0].power_entity_id == "sensor.circulation_power"
    assert plant.pumps[0].power_max_age_seconds == 96
    assert plant.pumps[0].flow_entity_id == "sensor.circulation_flow"
    assert plant.pumps[0].flow_max_age_seconds == 97
    assert plant.pumps[0].fault_entity_id == "binary_sensor.circulation_fault"
    assert plant.pumps[0].fault_max_age_seconds == 98
    assert plant.circuits[0].valve_ids == (
        "00000000-0000-4000-8000-000000000003",
        "00000000-0000-4000-8000-000000000004",
    )


def test_empty_plant_entry_remains_a_valid_milestone_zero_configuration() -> None:
    plant = plant_configuration_from_entry_data({"plant_id": "plant-1"})

    assert plant.id == "plant-1"
    assert plant.zones == ()


def test_legacy_shared_actuators_keep_the_most_conservative_timing() -> None:
    """Legacy circuit-owned timings must not make a shared actuator less safe."""
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": "plant-1",
            "topology": {
                "circuits": [
                    {
                        "id": "circuit-1",
                        "name": "Floor loop",
                        "valve_id": "switch.shared_valve",
                        "pump_id": "switch.shared_pump",
                        "valve_opening_time_seconds": 30,
                        "pump_overrun_seconds": 120,
                    },
                    {
                        "id": "circuit-2",
                        "name": "Ceiling loop",
                        "valve_id": "switch.shared_valve",
                        "pump_id": "switch.shared_pump",
                        "valve_opening_time_seconds": 60,
                        "pump_overrun_seconds": 180,
                    },
                ]
            },
        }
    )

    assert len(plant.valves) == 1
    assert plant.valves[0].opening_time_seconds == 60
    assert len(plant.pumps) == 1
    assert plant.pumps[0].overrun_seconds == 180


def test_rejects_non_uuid_ids_in_first_class_persisted_topology() -> None:
    """New-format topology relationships must use stable UUIDs."""
    with pytest.raises(StoredTopologyError, match="field 'id' must be a UUID"):
        plant_configuration_from_entry_data(
            {
                "plant_id": "00000000-0000-4000-8000-000000000001",
                "topology": {
                    "zones": [
                        {
                            "id": "00000000-0000-4000-8000-000000000002",
                            "name": "Living room",
                            "target_temperature": 21.5,
                            "temperature_sensor": "sensor.living_temperature",
                        }
                    ],
                    "valves": [
                        {
                            "id": "not-a-uuid",
                            "name": "Floor valve",
                            "entity_id": "switch.floor_valve",
                        }
                    ],
                    "pumps": [
                        {
                            "id": "00000000-0000-4000-8000-000000000005",
                            "name": "Floor pump",
                            "entity_id": "switch.floor_pump",
                        }
                    ],
                    "circuits": [
                        {
                            "id": "00000000-0000-4000-8000-000000000006",
                            "name": "Floor loop",
                            "valve_ids": ["not-a-uuid"],
                            "pump_id": "00000000-0000-4000-8000-000000000005",
                        }
                    ],
                    "routes": [
                        {
                            "id": "00000000-0000-4000-8000-000000000007",
                            "zone_id": "00000000-0000-4000-8000-000000000002",
                            "circuit_id": "00000000-0000-4000-8000-000000000006",
                        }
                    ],
                },
            }
        )


def test_legacy_sensor_data_gets_safe_metadata_defaults() -> None:
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": "plant-1",
            "topology": {
                "zones": [
                    {
                        "id": "zone-1",
                        "name": "Living room",
                        "target_temperature": 21.0,
                        "temperature_sensor": "sensor.living_temperature",
                    }
                ],
                "circuits": [],
                "routes": [],
            },
        }
    )

    sensor = plant.zones[0].sensor_metadata[0]
    assert sensor.entity_id == "sensor.living_temperature"
    assert sensor.required is True
    assert sensor.weight == 1.0
    assert sensor.calibration_offset == 0.0
    assert sensor.maximum_age_seconds == 1800.0
    assert sensor.designated_reference is False


def test_decodes_sensor_metadata_and_zone_policy_fields() -> None:
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": "plant-1",
            "topology": {
                "zones": [
                    {
                        "id": "zone-1",
                        "name": "Living room",
                        "target_temperature": 21.0,
                        "temperature_sensor_metadata": [
                            {
                                "entity_id": "sensor.primary",
                                "required": True,
                                "weight": 2.0,
                                "calibration_offset": -0.25,
                                "max_age_seconds": 300,
                                "designated_reference": True,
                            },
                            {
                                "entity_id": "sensor.backup",
                                "required": False,
                                "weight": 1.0,
                                "calibration_offset": 0.5,
                                "max_age_seconds": 900,
                                "designated_reference": False,
                            },
                        ],
                        "temperature_aggregation": "designated_reference",
                        "heating_start_delta": 0.4,
                        "heating_stop_delta": 0.15,
                        "minimum_active_duration_seconds": 120,
                        "minimum_idle_duration_seconds": 60,
                        "preset_targets": {"comfort": 21.5, "eco": 19.0, "away": 16.0},
                    }
                ],
                "circuits": [],
                "routes": [],
            },
        }
    )

    zone = plant.zones[0]
    assert zone.aggregation is TemperatureAggregation.DESIGNATED_REFERENCE
    assert zone.sensor_metadata[0].maximum_age_seconds == 300
    assert zone.sensor_metadata[1].required is False
    assert zone.heating_start_delta == 0.4
    assert zone.heating_stop_delta == 0.15
    assert zone.minimum_active_duration_seconds == 120
    assert zone.minimum_idle_duration_seconds == 60
    assert dict(zone.preset_targets) == {"comfort": 21.5, "eco": 19.0, "away": 16.0}


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        (
            [{"entity_id": "sensor.one", "unknown": True}],
            "unknown fields",
        ),
        (
            [
                {"entity_id": "sensor.one"},
                {"entity_id": "sensor.one"},
            ],
            "duplicate",
        ),
        (
            [{"entity_id": "sensor.one", "max_age_seconds": 0}],
            "positive",
        ),
    ],
)
def test_rejects_invalid_sensor_metadata(metadata, message) -> None:
    with pytest.raises(StoredTopologyError, match=message):
        plant_configuration_from_entry_data(
            {
                "plant_id": "plant-1",
                "topology": {
                    "zones": [
                        {
                            "id": "zone-1",
                            "name": "Living room",
                            "target_temperature": 21.0,
                            "temperature_sensor_metadata": metadata,
                        }
                    ],
                    "circuits": [],
                    "routes": [],
                },
            }
        )


def test_designated_reference_requires_one_metadata_record() -> None:
    with pytest.raises(StoredTopologyError, match="exactly one designated"):
        plant_configuration_from_entry_data(
            {
                "plant_id": "plant-1",
                "topology": {
                    "zones": [
                        {
                            "id": "zone-1",
                            "name": "Living room",
                            "target_temperature": 21.0,
                            "temperature_sensor": "sensor.one",
                            "temperature_aggregation": "designated_reference",
                        }
                    ],
                    "circuits": [],
                    "routes": [],
                },
            }
        )


def test_legacy_route_enablement_is_preserved() -> None:
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": "plant-1",
            "topology": {
                "zones": [],
                "circuits": [],
                "routes": [
                    {
                        "id": "route-1",
                        "zone_id": "zone-1",
                        "circuit_id": "circuit-1",
                        "enabled": False,
                    }
                ],
            },
        }
    )

    assert plant.routes[0].enabled is False


def test_decodes_cooling_zone_and_circuit_safety_fields() -> None:
    """Persisted humidity, hysteresis, references, and margin reload as one model."""
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": "00000000-0000-4000-8000-000000000001",
            "topology": {
                "zones": [
                    {
                        "id": "00000000-0000-4000-8000-000000000002",
                        "name": "Living room",
                        "target_temperature": 24.0,
                        "temperature_sensor": "sensor.living_temperature",
                        "humidity_sensor_metadata": [
                            {
                                "entity_id": "sensor.living_humidity",
                                "required": True,
                                "max_age_seconds": 300,
                            }
                        ],
                        "cooling_start_delta": 0.6,
                        "cooling_stop_delta": 0.2,
                    }
                ],
                "valves": [
                    {
                        "id": "00000000-0000-4000-8000-000000000003",
                        "name": "Cooling valve",
                        "entity_id": "switch.cooling_valve",
                    }
                ],
                "pumps": [
                    {
                        "id": "00000000-0000-4000-8000-000000000004",
                        "name": "Cooling pump",
                        "entity_id": "switch.cooling_pump",
                    }
                ],
                "circuits": [
                    {
                        "id": "00000000-0000-4000-8000-000000000005",
                        "name": "Cooling circuit",
                        "valve_ids": ["00000000-0000-4000-8000-000000000003"],
                        "pump_id": "00000000-0000-4000-8000-000000000004",
                        "cooling_enabled": True,
                        "surface_temperature_sensor": "sensor.cooling_surface",
                        "condensation_margin": 2.5,
                        "surface_temperature_max_age_seconds": 240,
                    }
                ],
                "routes": [
                    {
                        "id": "00000000-0000-4000-8000-000000000006",
                        "zone_id": "00000000-0000-4000-8000-000000000002",
                        "circuit_id": "00000000-0000-4000-8000-000000000005",
                    }
                ],
            },
        }
    )

    zone = plant.zones[0]
    circuit = plant.circuits[0]
    assert zone.humidity_sensors == ("sensor.living_humidity",)
    assert zone.humidity_sensor_metadata[0].max_age_seconds == 300
    assert zone.cooling_start_delta == 0.6
    assert zone.cooling_stop_delta == 0.2
    assert circuit.cooling_enabled is True
    assert circuit.surface_temperature_sensor == "sensor.cooling_surface"
    assert circuit.condensation_margin == 2.5
    assert circuit.surface_temperature_max_age_seconds == 240


@pytest.mark.parametrize(
    "humidity_fields",
    [
        {"humidity_sensor": "sensor.humidity"},
        {
            "humidity_sensors": ["sensor.humidity", "sensor.humidity.backup"],
            "humidity_sensor_weights": {"sensor.humidity": 2.0},
        },
        {
            "humidity_sensor_metadata": {
                "sensor.humidity": {"required": False, "max_age_seconds": 60}
            }
        },
    ],
)
def test_decodes_legacy_and_canonical_humidity_sensor_shapes(humidity_fields) -> None:
    """All supported persistence shapes use the same immutable humidity metadata."""
    zone_data = {
        "id": "zone-1",
        "name": "Living room",
        "target_temperature": 24.0,
        "temperature_sensor": "sensor.temperature",
        **humidity_fields,
    }
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": "plant-1",
            "topology": {"zones": [zone_data], "circuits": [], "routes": []},
        }
    )

    assert plant.zones[0].humidity_sensors
    assert all(sensor.max_age_seconds > 0 for sensor in plant.zones[0].humidity_sensor_metadata)
