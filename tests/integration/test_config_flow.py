"""Tests for config flow setup of Hydronicus."""

from __future__ import annotations

import voluptuous_serialize
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv

from custom_components.hydronicus.const import (
    CONF_CALIBRATION_OFFSET,
    CONF_CONFIGURE_SENSOR_METADATA,
    CONF_DESIGNATED_REFERENCE,
    CONF_MAX_AGE,
    CONF_PUMP_ENTITY,
    CONF_PUMP_OVERRUN,
    CONF_REQUIRED,
    CONF_SENSOR_ENTITY,
    CONF_TARGET_TEMPERATURE,
    CONF_TEMPERATURE_AGGREGATION,
    CONF_TEMPERATURE_SENSORS,
    CONF_VALVE_ENTITY,
    CONF_VALVE_OPENING_TIME,
    CONF_WEIGHT,
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
    assert result["description_placeholders"]["zone"] == "Living room"
    assert result["description_placeholders"]["circuit"] == "Floor loop"
    assert result["description_placeholders"]["logic"] == (
        "- Circuit Floor loop opens valves Floor loop valve before requesting pump "
        "Floor loop pump.\n"
        "- Zone Living room can request circuit Floor loop."
    )
    assert result["description_placeholders"]["warnings"] == "- None"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hydronic plant"
    assert result["data"]["dry_run"] is True
    topology = result["data"]["topology"]
    assert topology["zones"][0]["name"] == "Living room"
    assert topology["zones"][0]["temperature_sensors"] == ["sensor.living_temperature"]
    assert topology["zones"][0][CONF_TEMPERATURE_AGGREGATION] == "median"
    assert topology["valves"][0]["entity_id"] == "switch.floor_valve"
    assert topology["pumps"][0]["entity_id"] == "switch.floor_pump"
    assert topology["circuits"][0]["valve_ids"] == [topology["valves"][0]["id"]]
    assert topology["circuits"][0]["pump_id"] == topology["pumps"][0]["id"]


async def test_initial_zone_schema_serializes_for_home_assistant_ui(hass) -> None:
    """The initial zone form must be serializable by Home Assistant's HTTP view."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"name": "Hydronic plant"}
    )

    assert result["step_id"] == "zone"
    voluptuous_serialize.convert(result["data_schema"], custom_serializer=cv.custom_serializer)


async def test_invalid_initial_topology_keeps_review_placeholders(hass) -> None:
    """A validation error should not break the translated review description."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"name": "Hydronic plant"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Living room",
            CONF_TARGET_TEMPERATURE: 21.5,
            CONF_TEMPERATURE_SENSORS: ["sensor.living_temperature"],
            CONF_TEMPERATURE_AGGREGATION: "median",
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Floor loop",
            CONF_VALVE_ENTITY: "switch.shared_equipment",
            CONF_PUMP_ENTITY: "switch.shared_equipment",
            CONF_VALVE_OPENING_TIME: 30,
            CONF_PUMP_OVERRUN: 120,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "review"
    assert result["errors"] == {"base": "invalid_topology"}
    assert result["description_placeholders"] == {
        "zone": "Living room",
        "circuit": "Floor loop",
        "logic": "- Topology could not be compiled.",
        "warnings": "- None",
    }


async def test_advanced_sensor_editor_persists_metadata_and_weighted_policy(hass) -> None:
    """The weighted policy is reachable only through complete typed metadata forms."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"name": "Hydronic plant"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Living room",
            CONF_TARGET_TEMPERATURE: 21.5,
            CONF_TEMPERATURE_SENSORS: [
                "sensor.living_temperature",
                "sensor.living_temperature_backup",
            ],
            CONF_TEMPERATURE_AGGREGATION: "mean",
            CONF_CONFIGURE_SENSOR_METADATA: True,
        },
    )
    assert result["step_id"] == "sensor_metadata"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_SENSOR_ENTITY: "sensor.living_temperature",
            CONF_REQUIRED: True,
            CONF_WEIGHT: 2.0,
            CONF_CALIBRATION_OFFSET: -0.25,
            CONF_MAX_AGE: 300,
            CONF_DESIGNATED_REFERENCE: True,
        },
    )
    assert result["step_id"] == "sensor_metadata"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_SENSOR_ENTITY: "sensor.living_temperature_backup",
            CONF_REQUIRED: False,
            CONF_WEIGHT: 1.0,
            CONF_CALIBRATION_OFFSET: 0.5,
            CONF_MAX_AGE: 900,
            CONF_DESIGNATED_REFERENCE: False,
        },
    )
    assert result["step_id"] == "sensor_policy"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_TEMPERATURE_AGGREGATION: "weighted_mean"},
    )
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
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})

    metadata = result["data"]["topology"]["zones"][0]["temperature_sensor_metadata"]
    assert metadata == [
        {
            "entity_id": "sensor.living_temperature",
            CONF_REQUIRED: True,
            CONF_WEIGHT: 2.0,
            CONF_CALIBRATION_OFFSET: -0.25,
            CONF_MAX_AGE: 300.0,
            CONF_DESIGNATED_REFERENCE: True,
        },
        {
            "entity_id": "sensor.living_temperature_backup",
            CONF_REQUIRED: False,
            CONF_WEIGHT: 1.0,
            CONF_CALIBRATION_OFFSET: 0.5,
            CONF_MAX_AGE: 900.0,
            CONF_DESIGNATED_REFERENCE: False,
        },
    ]
    assert result["data"]["topology"]["zones"][0][CONF_TEMPERATURE_AGGREGATION] == ("weighted_mean")
