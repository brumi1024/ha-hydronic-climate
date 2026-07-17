"""Tests for config flow setup of Hydronicus."""

from __future__ import annotations

from homeassistant.data_entry_flow import FlowResultType

from custom_components.hydronicus.const import (
    CONF_PUMP_ENTITY,
    CONF_PUMP_OVERRUN,
    CONF_TARGET_TEMPERATURE,
    CONF_TEMPERATURE_AGGREGATION,
    CONF_TEMPERATURE_SENSORS,
    CONF_VALVE_ENTITY,
    CONF_VALVE_OPENING_TIME,
    DOMAIN,
)


async def test_user_config_flow_creates_entry(hass) -> None:
    """A user flow should persist one validated shadow topology."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"name": "Hydronic plant"},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "zone"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Living room",
            CONF_TARGET_TEMPERATURE: 21.5,
            CONF_TEMPERATURE_SENSORS: ["sensor.living_temperature"],
            CONF_TEMPERATURE_AGGREGATION: "median",
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "circuit"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Floor loop",
            CONF_VALVE_ENTITY: "switch.floor_valve",
            CONF_PUMP_ENTITY: "switch.floor_pump",
            CONF_VALVE_OPENING_TIME: 30,
            CONF_PUMP_OVERRUN: 120,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "review"
    assert result["description_placeholders"]["logic"] == (
        "- Circuit Floor loop opens valves Floor loop valve before requesting pump "
        "Floor loop pump.\n"
        "- Zone Living room can request circuit Floor loop."
    )

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hydronic plant"
    assert result["data"]["shadow_mode"] is True
    topology = result["data"]["topology"]
    assert topology["zones"][0]["name"] == "Living room"
    assert topology["zones"][0]["temperature_sensors"] == [
        "sensor.living_temperature"
    ]
    assert topology["zones"][0][CONF_TEMPERATURE_AGGREGATION] == "median"
    assert topology["valves"][0]["entity_id"] == "switch.floor_valve"
    assert topology["pumps"][0]["entity_id"] == "switch.floor_pump"
    assert topology["circuits"][0]["valve_ids"] == [topology["valves"][0]["id"]]
    assert topology["circuits"][0]["pump_id"] == topology["pumps"][0]["id"]
