"""Tests for decoding persisted plant topology."""

from __future__ import annotations

import pytest
from hydronicus_core.configuration import (
    StoredTopologyError,
    plant_configuration_from_entry_data,
)
from hydronicus_core.model import TemperatureAggregation

PLANT_ID = "00000000-0000-4000-8000-000000000001"
ZONE_ID = "00000000-0000-4000-8000-000000000002"
VALVE_ID = "00000000-0000-4000-8000-000000000003"
PUMP_ID = "00000000-0000-4000-8000-000000000004"
CIRCUIT_ID = "00000000-0000-4000-8000-000000000005"
ROUTE_ID = "00000000-0000-4000-8000-000000000006"


def test_circuits_default_to_heating_only() -> None:
    """Omitted cooling compatibility remains disabled for canonical circuits."""
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": PLANT_ID,
            "topology": {
                "circuits": [
                    {
                        "id": CIRCUIT_ID,
                        "name": "Floor loop",
                        "valve_ids": [VALVE_ID],
                        "pump_id": PUMP_ID,
                    }
                ],
            },
        }
    )

    assert plant.circuits[0].cooling_enabled is False


def test_cooling_compatibility_requires_a_persisted_boolean() -> None:
    """Malformed stored mode flags cannot silently enable cooling."""
    with pytest.raises(StoredTopologyError, match="cooling_enabled.*boolean"):
        plant_configuration_from_entry_data(
            {
                "plant_id": PLANT_ID,
                "topology": {
                    "circuits": [
                        {
                            "id": CIRCUIT_ID,
                            "name": "Floor loop",
                            "valve_ids": [VALVE_ID],
                            "pump_id": PUMP_ID,
                            "cooling_enabled": "false",
                        }
                    ],
                },
            }
        )


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
                        "temperature_sensor_metadata": [{"entity_id": "sensor.zone"}],
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


@pytest.mark.parametrize(
    ("collection", "record", "message"),
    [
        (
            "valves",
            {
                "id": "00000000-0000-4000-8000-000000000002",
                "name": "Valve",
                "entity_id": "switch.valve",
                "feedback_entity_id": "binary_sensor.valve_ready",
            },
            "Stored valve uses unsupported fields: feedback_entity_id",
        ),
        (
            "valves",
            {
                "id": "00000000-0000-4000-8000-000000000002",
                "name": "Valve",
                "entity_id": "switch.valve",
                "valve_opening_time_seconds": 30,
            },
            "Stored valve uses unsupported fields: valve_opening_time_seconds",
        ),
        (
            "pumps",
            {
                "id": "00000000-0000-4000-8000-000000000002",
                "name": "Pump",
                "entity_id": "switch.pump",
                "power_feedback_entity_id": "sensor.pump_power",
            },
            "Stored pump uses unsupported fields: power_feedback_entity_id",
        ),
        (
            "pumps",
            {
                "id": "00000000-0000-4000-8000-000000000002",
                "name": "Pump",
                "entity_id": "switch.pump",
                "pump_overrun_seconds": 120,
            },
            "Stored pump uses unsupported fields: pump_overrun_seconds",
        ),
    ],
)
def test_rejects_alternate_valve_and_pump_fields(collection, record, message) -> None:
    """Stored actuators accept only field names emitted by current configuration flows."""
    with pytest.raises(StoredTopologyError, match=message):
        plant_configuration_from_entry_data(
            {
                "plant_id": "00000000-0000-4000-8000-000000000001",
                "topology": {collection: [record]},
            }
        )


def test_rejects_nonboolean_route_enablement() -> None:
    """A malformed flag must not silently enable a delivery route."""
    with pytest.raises(StoredTopologyError, match="route enabled must be a boolean"):
        plant_configuration_from_entry_data(
            {
                "plant_id": PLANT_ID,
                "topology": {
                    "routes": [
                        {
                            "id": ROUTE_ID,
                            "zone_id": ZONE_ID,
                            "circuit_id": CIRCUIT_ID,
                            "enabled": "false",
                        }
                    ]
                },
            }
        )


def test_decodes_zone_temperature_aggregation_from_config_entry_data() -> None:
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": PLANT_ID,
            "topology": {
                "zones": [
                    {
                        "id": ZONE_ID,
                        "name": "Living room",
                        "target_temperature": 21.5,
                        "temperature_sensor_metadata": [
                            {"entity_id": "sensor.living_temperature", "weight": 1},
                            {
                                "entity_id": "sensor.living_temperature_backup",
                                "weight": 3,
                            },
                        ],
                        "temperature_aggregation": "weighted_mean",
                    }
                ],
                "circuits": [],
                "routes": [],
            },
        }
    )

    assert plant.zones[0].aggregation is TemperatureAggregation.WEIGHTED_MEAN
    assert {
        sensor.entity_id: sensor.weight for sensor in plant.zones[0].temperature_sensor_metadata
    } == {
        "sensor.living_temperature": 1.0,
        "sensor.living_temperature_backup": 3.0,
    }


def test_zone_temperature_aggregation_defaults_to_mean() -> None:
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": PLANT_ID,
            "topology": {
                "zones": [
                    {
                        "id": ZONE_ID,
                        "name": "Living room",
                        "target_temperature": 21.5,
                        "temperature_sensor_metadata": [{"entity_id": "sensor.living_temperature"}],
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
                "plant_id": PLANT_ID,
                "topology": {
                    "zones": [
                        {
                            "id": ZONE_ID,
                            "name": "Living room",
                            "target_temperature": 21.5,
                            "temperature_sensor_metadata": [
                                {"entity_id": "sensor.living_temperature"}
                            ],
                            "temperature_aggregation": "trimmed_mean",
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
                "plant_id": PLANT_ID,
                "topology": {
                    "zones": [
                        {
                            "id": ZONE_ID,
                            "name": "Living room",
                            "target_temperature": 21.5,
                            "temperature_sensor_metadata": [
                                {
                                    "entity_id": "sensor.living_temperature",
                                    "weight": weight,
                                }
                            ],
                        }
                    ],
                    "circuits": [],
                    "routes": [],
                },
            }
        )


def test_rejects_missing_required_persisted_topology_field() -> None:
    with pytest.raises(StoredTopologyError, match="temperature_sensor_metadata"):
        plant_configuration_from_entry_data(
            {
                "plant_id": PLANT_ID,
                "topology": {
                    "zones": [{"id": ZONE_ID, "name": "Living room", "target_temperature": 21}],
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
                        "temperature_sensor_metadata": [{"entity_id": "sensor.living_temperature"}],
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
    plant = plant_configuration_from_entry_data({"plant_id": PLANT_ID})

    assert plant.id == PLANT_ID
    assert plant.zones == ()


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
                            "temperature_sensor_metadata": [
                                {"entity_id": "sensor.living_temperature"}
                            ],
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


def test_canonical_sensor_metadata_gets_safe_defaults() -> None:
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": PLANT_ID,
            "topology": {
                "zones": [
                    {
                        "id": ZONE_ID,
                        "name": "Living room",
                        "target_temperature": 21.0,
                        "temperature_sensor_metadata": [{"entity_id": "sensor.living_temperature"}],
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
    assert sensor.max_age_seconds == 1800.0
    assert sensor.designated_reference is False


def test_decodes_sensor_metadata_and_zone_policy_fields() -> None:
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": PLANT_ID,
            "topology": {
                "zones": [
                    {
                        "id": ZONE_ID,
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
    assert zone.sensor_metadata[0].max_age_seconds == 300
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
                "plant_id": PLANT_ID,
                "topology": {
                    "zones": [
                        {
                            "id": ZONE_ID,
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
                "plant_id": PLANT_ID,
                "topology": {
                    "zones": [
                        {
                            "id": ZONE_ID,
                            "name": "Living room",
                            "target_temperature": 21.0,
                            "temperature_sensor_metadata": [{"entity_id": "sensor.one"}],
                            "temperature_aggregation": "designated_reference",
                        }
                    ],
                    "circuits": [],
                    "routes": [],
                },
            }
        )


def test_route_enablement_is_preserved() -> None:
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": PLANT_ID,
            "topology": {
                "zones": [],
                "circuits": [],
                "routes": [
                    {
                        "id": ROUTE_ID,
                        "zone_id": ZONE_ID,
                        "circuit_id": CIRCUIT_ID,
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
                        "temperature_sensor_metadata": [{"entity_id": "sensor.living_temperature"}],
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


def test_decodes_canonical_humidity_sensor_metadata() -> None:
    """Humidity uses the same canonical immutable metadata collection."""
    zone_data = {
        "id": ZONE_ID,
        "name": "Living room",
        "target_temperature": 24.0,
        "temperature_sensor_metadata": [{"entity_id": "sensor.temperature"}],
        "humidity_sensor_metadata": [
            {
                "entity_id": "sensor.humidity",
                "required": False,
                "weight": 2.0,
                "max_age_seconds": 60,
            }
        ],
    }
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": PLANT_ID,
            "topology": {"zones": [zone_data], "circuits": [], "routes": []},
        }
    )

    assert plant.zones[0].humidity_sensors == ("sensor.humidity",)
    assert plant.zones[0].humidity_sensor_metadata[0].weight == 2.0
    assert plant.zones[0].humidity_sensor_metadata[0].max_age_seconds == 60


@pytest.mark.parametrize(
    "unsupported_fields",
    [
        {"temperature_sensor": "sensor.temperature"},
        {"temperature_sensors": ["sensor.temperature"]},
        {"temperature_sensor_weights": {"sensor.temperature": 2.0}},
        {"designated_reference_sensor": "sensor.temperature"},
        {"humidity_sensor": "sensor.humidity"},
        {"humidity_sensors": ["sensor.humidity"]},
        {"humidity_sensor_weights": {"sensor.humidity": 2.0}},
    ],
)
def test_rejects_unsupported_sensor_representations(unsupported_fields) -> None:
    """Only canonical metadata collections are accepted before the first release."""
    with pytest.raises(StoredTopologyError, match="unsupported sensor fields"):
        plant_configuration_from_entry_data(
            {
                "plant_id": PLANT_ID,
                "topology": {
                    "zones": [
                        {
                            "id": ZONE_ID,
                            "name": "Living room",
                            "target_temperature": 24.0,
                            "temperature_sensor_metadata": [{"entity_id": "sensor.temperature"}],
                            **unsupported_fields,
                        }
                    ],
                    "circuits": [],
                    "routes": [],
                },
            }
        )
