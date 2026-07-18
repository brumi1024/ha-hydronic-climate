"""End-to-end public-beta setup and shadow simulation checks."""

from __future__ import annotations

from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

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


async def test_public_documentation_path_creates_and_exercises_shadow_plant(hass) -> None:
    """The README and configuration guide path works with disposable entities only."""
    temperature_entity = "sensor.hydronicus_simulated_zone_temperature"
    valve_entity = "switch.hydronicus_simulated_zone_valve"
    pump_entity = "switch.hydronicus_simulated_zone_pump"
    hass.states.async_set(temperature_entity, "18.0")
    hass.states.async_set(valve_entity, "off")
    hass.states.async_set(pump_entity, "off")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"name": "Public beta simulated plant"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Simulated zone",
            CONF_TARGET_TEMPERATURE: 21.0,
            CONF_TEMPERATURE_SENSORS: [temperature_entity],
            CONF_TEMPERATURE_AGGREGATION: "mean",
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Simulated circuit",
            CONF_VALVE_ENTITY: valve_entity,
            CONF_PUMP_ENTITY: pump_entity,
            CONF_VALVE_OPENING_TIME: 0.0,
            CONF_PUMP_OVERRUN: 0.0,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "review"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entry = next(
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.title == "Public beta simulated plant"
    )
    assert entry.data["shadow_mode"] is True
    await hass.async_block_till_done()

    assert entry.runtime_data.shadow_mode is True
    demand_state = hass.states.get(
        "binary_sensor.public_beta_simulated_plant_simulated_zone_demand"
    )
    topology_state = hass.states.get("sensor.public_beta_simulated_plant_topology_preview")
    assert demand_state is not None and demand_state.state == "on"
    assert topology_state is not None and topology_state.state == "1 zone, 1 circuit"
    assert hass.states.get(valve_entity).state == "off"
    assert hass.states.get(pump_entity).state == "off"

    hass.states.async_set(temperature_entity, "22.0")
    await hass.async_block_till_done()
    demand_state = hass.states.get(
        "binary_sensor.public_beta_simulated_plant_simulated_zone_demand"
    )
    assert demand_state is not None and demand_state.state == "off"
    assert hass.states.get(valve_entity).state == "off"
    assert hass.states.get(pump_entity).state == "off"


async def test_public_beta_fresh_entry_can_reload_without_changing_domain(hass) -> None:
    """A fresh package entry remains a Hydronicus entry across a reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Fresh package plant",
        data={"name": "Fresh package plant", "plant_id": "fresh-plant", "shadow_mode": True},
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.domain == DOMAIN
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert entry.domain == DOMAIN
