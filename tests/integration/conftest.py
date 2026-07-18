"""Enable the custom integration for adapter integration tests."""

import pytest


@pytest.fixture(autouse=True)
def enable_hydronic_custom_integration(enable_custom_integrations, hass) -> None:
    """Allow Home Assistant's loader to discover this custom integration."""
    for entity_id, state in {
        "switch.test_valve": "off",
        "switch.test_pump": "off",
        "switch.synthetic_valve": "off",
        "switch.synthetic_pump": "off",
        "valve.synthetic_valve": "closed",
        "binary_sensor.synthetic_valve_ready": "off",
        "switch.synthetic_source": "off",
        "switch.floor_valve": "off",
        "switch.floor_pump": "off",
        "switch.return_valve": "off",
        "switch.shared_valve": "off",
        "switch.updated_return_valve": "off",
        "switch.shared_pump": "off",
        "switch.shared_return_valve": "off",
        "switch.shared_supply_valve": "off",
        "switch.cooling_valve": "off",
        "switch.cooling_pump": "off",
        "switch.shared_mode_valve": "off",
        "switch.shared_mode_pump": "off",
        "switch.shared_equipment": "off",
        "binary_sensor.buffer_available": "off",
    }.items():
        hass.states.async_set(entity_id, state)
