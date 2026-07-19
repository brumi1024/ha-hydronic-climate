"""Integration tests for source configuration and shadow recommendation entities."""

from __future__ import annotations

import voluptuous_serialize
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronicus.const import (
    CONF_SOURCE_AVAILABILITY_ENTITY,
    CONF_SOURCE_DEMAND_ENTITY,
    CONF_SOURCE_HYSTERESIS,
    CONF_SOURCE_MAXIMUM_AGE,
    CONF_SOURCE_MINIMUM_TEMPERATURE,
    CONF_SOURCE_PRIORITY,
    CONF_SOURCE_TEMPERATURE_ENTITY,
    CONF_SOURCE_TYPE,
    DOMAIN,
    SUBENTRY_TYPE_SOURCE,
)

PLANT_ID = "00000000-0000-4000-8000-000000000001"
ZONE_ID = "00000000-0000-4000-8000-000000000002"
VALVE_ID = "00000000-0000-4000-8000-000000000003"
PUMP_ID = "00000000-0000-4000-8000-000000000004"
CIRCUIT_ID = "00000000-0000-4000-8000-000000000005"
ROUTE_ID = "00000000-0000-4000-8000-000000000006"
SELECTOR_ID = "00000000-0000-4000-8000-000000000007"


def _entry() -> MockConfigEntry:
    """Return a synthetic active-heating topology with no configured sources."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hydronic plant",
        data={
            "name": "Hydronic plant",
            "plant_id": PLANT_ID,
            "dry_run": True,
            "topology": {
                "zones": [
                    {
                        "id": ZONE_ID,
                        "name": "Living room",
                        "target_temperature": 21.0,
                        "temperature_sensor": "sensor.living_temperature",
                    }
                ],
                "valves": [
                    {
                        "id": VALVE_ID,
                        "name": "Floor valve",
                        "entity_id": "switch.floor_valve",
                        "opening_time_seconds": 0,
                    }
                ],
                "pumps": [
                    {
                        "id": PUMP_ID,
                        "name": "Floor pump",
                        "entity_id": "switch.floor_pump",
                        "overrun_seconds": 0,
                    }
                ],
                "circuits": [
                    {
                        "id": CIRCUIT_ID,
                        "name": "Floor loop",
                        "valve_ids": [VALVE_ID],
                        "pump_id": PUMP_ID,
                    }
                ],
                "routes": [{"id": ROUTE_ID, "zone_id": ZONE_ID, "circuit_id": CIRCUIT_ID}],
            },
        },
    )


async def _add_buffer_source(hass, entry):
    """Create a buffer source through the public source subentry flow."""
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SOURCE),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == FlowResultType.FORM
    voluptuous_serialize.convert(result["data_schema"], custom_serializer=cv.custom_serializer)
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "name": "Buffer",
            CONF_SOURCE_TYPE: "temperature_qualified_buffer",
            CONF_SOURCE_PRIORITY: 1,
            CONF_SOURCE_AVAILABILITY_ENTITY: "binary_sensor.buffer_available",
            CONF_SOURCE_DEMAND_ENTITY: "switch.synthetic_source",
            CONF_SOURCE_TEMPERATURE_ENTITY: "sensor.buffer_temperature",
            CONF_SOURCE_MINIMUM_TEMPERATURE: 40.0,
            CONF_SOURCE_MAXIMUM_AGE: 60.0,
            CONF_SOURCE_HYSTERESIS: 0.5,
        },
    )


async def test_source_setup_reconfigure_reload_delete_and_entities(hass) -> None:
    """Source lifecycle persists its contract and publishes only shadow diagnostics."""
    hass.states.async_set("sensor.living_temperature", "19.0")
    hass.states.async_set("binary_sensor.buffer_available", "on")
    hass.states.async_set("sensor.buffer_temperature", "45.0")
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await _add_buffer_source(hass, entry)
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_SOURCE
    )
    source_id = subentry.data["id"]
    assert entry.runtime_data.plant.sources[source_id].priority == 1
    assert entry.runtime_data.plant.sources[source_id].demand_entity_id == "switch.synthetic_source"
    assert entry.runtime_data.plant.sources[source_id].minimum_temperature == 40.0
    assert hass.states.get("sensor.hydronic_plant_recommended_source").state == source_id
    assert hass.states.get("sensor.hydronic_plant_source_recommendation").state.startswith(
        "Recommended source:"
    )
    assert entry.runtime_data.evaluation.control_plan.source_recommendation is not None
    assert entry.runtime_data.evaluation.control_plan.commands
    assert all(
        command.actuator_id not in entry.runtime_data.plant.sources
        for command in entry.runtime_data.evaluation.control_plan.commands
    )

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["type"] == FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            "name": "Backup boiler",
            CONF_SOURCE_TYPE: "external",
            CONF_SOURCE_PRIORITY: 5,
            CONF_SOURCE_AVAILABILITY_ENTITY: "binary_sensor.buffer_available",
            CONF_SOURCE_TEMPERATURE_ENTITY: None,
            CONF_SOURCE_MINIMUM_TEMPERATURE: 0.0,
            CONF_SOURCE_MAXIMUM_AGE: 60.0,
            CONF_SOURCE_HYSTERESIS: 0.5,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert subentry.data["id"] == source_id
    assert subentry.data[CONF_SOURCE_TYPE] == "external"
    assert entry.runtime_data.plant.sources[source_id].kind.value == "external"

    runtime_before_reload = entry.runtime_data
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data is not runtime_before_reload
    assert entry.runtime_data.plant.sources[source_id].name == "Backup boiler"
    assert hass.states.get("sensor.hydronic_plant_recommended_source").state == source_id

    assert hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.plant.sources == {}
    assert hass.states.get("sensor.hydronic_plant_recommended_source").state == "none"
    assert hass.states.get("sensor.hydronic_plant_source_recommendation").state == (
        "No source configured."
    )


async def test_source_availability_and_buffer_freshness_update_entities(hass) -> None:
    """Synthetic availability and temperature changes update recommendation diagnostics."""
    hass.states.async_set("sensor.living_temperature", "19.0")
    hass.states.async_set("binary_sensor.buffer_available", "on")
    hass.states.async_set("sensor.buffer_temperature", "45.0")
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    result = await _add_buffer_source(hass, entry)
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    source_id = next(
        item.data["id"]
        for item in entry.subentries.values()
        if item.subentry_type == SUBENTRY_TYPE_SOURCE
    )

    hass.states.async_set("binary_sensor.buffer_available", "off")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.hydronic_plant_recommended_source").state == "none"
    assert "availability" in hass.states.get("sensor.hydronic_plant_source_recommendation").state

    hass.states.async_set("binary_sensor.buffer_available", "on")
    hass.states.async_set("sensor.buffer_temperature", "39.0")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.hydronic_plant_recommended_source").state == "none"
    assert "below" in hass.states.get("sensor.hydronic_plant_source_recommendation").state
    assert entry.runtime_data.plant.sources[source_id].temperature_entity_id == (
        "sensor.buffer_temperature"
    )


async def test_synthetic_selector_stays_shadow_only_while_recommendation_runs(hass) -> None:
    """Selector diagnostics and fallback remain available without a physical select call."""
    hass.states.async_set("sensor.living_temperature", "19.0")
    hass.states.async_set("binary_sensor.buffer_available", "on")
    hass.states.async_set("sensor.buffer_temperature", "45.0")
    hass.states.async_set("select.synthetic_source", "none")
    entry = _entry()
    entry.data["topology"]["source_selector"] = {
        "id": SELECTOR_ID,
        "name": "Synthetic selector",
        "entity_id": "select.synthetic_source",
        "break_interval_seconds": 5,
        "minimum_dwell_seconds": 10,
    }
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await _add_buffer_source(hass, entry)
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    runtime = entry.runtime_data
    assert runtime.source_recommendation() is not None
    assert runtime.source_recommendation().source_id is not None
    assert runtime.evaluation is not None
    assert runtime.evaluation.control_plan.source_selection is not None
    assert runtime.last_execution is not None
    assert all(
        operation.entity_id != "select.synthetic_source"
        for operation in runtime.last_execution.executed
    )
