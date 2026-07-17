"""Tests for integration setup and unload."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronicus.const import (
    CONF_NAME,
    CONF_PLANT_ID,
    CONF_SHADOW_MODE,
    DOMAIN,
)


async def test_setup_unload_and_reload_entry(hass) -> None:
    """The integration should load, unload, and reload an empty plant cleanly."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hydronic plant",
        data={
            CONF_NAME: "Hydronic plant",
            CONF_PLANT_ID: "plant-1",
            CONF_SHADOW_MODE: True,
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.runtime_data is not None
    assert entry.runtime_data.shadow_mode is True

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not hasattr(entry, "runtime_data")

    assert await hass.config_entries.async_reload(entry.entry_id)


async def test_configured_zone_climate_unloads_with_entry(hass) -> None:
    """Configured climate entities must disappear with their parent entry."""
    hass.states.async_set("sensor.test_zone_temperature", "18.0")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hydronic plant",
        data={
            CONF_NAME: "Hydronic plant",
            CONF_PLANT_ID: "00000000-0000-4000-8000-000000000001",
            CONF_SHADOW_MODE: True,
            "topology": {
                "zones": [
                    {
                        "id": "00000000-0000-4000-8000-000000000002",
                        "name": "Test zone",
                        "target_temperature": 21.0,
                        "temperature_sensor": "sensor.test_zone_temperature",
                        "preset_targets": {"comfort": 22.0, "eco": 19.0},
                    }
                ],
                "valves": [
                    {
                        "id": "00000000-0000-4000-8000-000000000003",
                        "name": "Test valve",
                        "entity_id": "switch.test_valve",
                    }
                ],
                "pumps": [
                    {
                        "id": "00000000-0000-4000-8000-000000000004",
                        "name": "Test pump",
                        "entity_id": "switch.test_pump",
                    }
                ],
                "circuits": [
                    {
                        "id": "00000000-0000-4000-8000-000000000005",
                        "name": "Test circuit",
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
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    climate_entity_id = "climate.hydronic_plant_test_zone"
    assert hass.states.get(climate_entity_id) is not None
    assert hass.states.get("sensor.hydronic_plant_test_zone_aggregate_temperature").state == "18.0"
    assert hass.states.get("binary_sensor.hydronic_plant_test_zone_blocked").state == "off"
    assert hass.states.get("sensor.hydronic_plant_test_zone_blocked_reason").state == "none"
    assert hass.states.get(climate_entity_id).attributes["preset_modes"] == [
        "comfort",
        "eco",
    ]

    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": climate_entity_id, "preset_mode": "comfort"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(climate_entity_id).attributes["preset_mode"] == "comfort"
    assert hass.states.get(climate_entity_id).attributes["temperature"] == 22.0

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": climate_entity_id, "temperature": 18.5},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(climate_entity_id).attributes["preset_mode"] == "none"
    assert hass.states.get(climate_entity_id).attributes["temperature"] == 18.5

    hass.states.async_set("sensor.test_zone_temperature", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.hydronic_plant_test_zone_blocked").state == "on"
    assert hass.states.get("binary_sensor.hydronic_plant_test_zone_demand").state == "off"
    assert hass.states.get("sensor.hydronic_plant_test_zone_blocked_reason").state.startswith(
        "Blocked:"
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert hass.states.get(climate_entity_id).state == "unavailable"
