"""Tests for decoding persisted plant topology."""

from __future__ import annotations

import pytest
from hydronic_climate_core.configuration import (
    StoredTopologyError,
    plant_configuration_from_entry_data,
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
                    }
                ],
                "routes": [{"id": "route-1", "zone_id": "zone-1", "circuit_id": "circuit-1"}],
            },
        }
    )

    assert plant.id == "plant-1"
    assert plant.zones[0].target_temperature == 21.5
    assert plant.circuits[0].valve_opening_time_seconds == 45
    assert plant.routes[0].zone_id == "zone-1"


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


def test_empty_plant_entry_remains_a_valid_milestone_zero_configuration() -> None:
    plant = plant_configuration_from_entry_data({"plant_id": "plant-1"})

    assert plant.id == "plant-1"
    assert plant.zones == ()
