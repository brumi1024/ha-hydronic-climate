"""Integration coverage for the cooling shadow tracer and its diagnostics."""

from __future__ import annotations

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronicus.const import (
    CONF_CONDENSATION_MARGIN,
    CONF_COOLING_ENABLED,
    CONF_COOLING_START_DELTA,
    CONF_COOLING_STOP_DELTA,
    CONF_DRY_RUN,
    CONF_HUMIDITY_SENSORS,
    CONF_NAME,
    CONF_PLANT_ID,
    CONF_SUPPLY_TEMPERATURE_SENSOR,
    CONF_TEMPERATURE_SENSORS,
    DOMAIN,
)

PLANT_ID = "00000000-0000-4000-8000-000000000001"
ZONE_ID = "00000000-0000-4000-8000-000000000002"
VALVE_ID = "00000000-0000-4000-8000-000000000003"
PUMP_ID = "00000000-0000-4000-8000-000000000004"
CIRCUIT_ID = "00000000-0000-4000-8000-000000000005"
ROUTE_ID = "00000000-0000-4000-8000-000000000006"
HEATING_ZONE_ID = "00000000-0000-4000-8000-000000000010"
COOLING_ZONE_ID = "00000000-0000-4000-8000-000000000011"
HEATING_CIRCUIT_ID = "00000000-0000-4000-8000-000000000012"
COOLING_CIRCUIT_ID = "00000000-0000-4000-8000-000000000013"
HEATING_ROUTE_ID = "00000000-0000-4000-8000-000000000014"
COOLING_ROUTE_ID = "00000000-0000-4000-8000-000000000015"


def _cooling_entry(*, dry_run: bool = True) -> MockConfigEntry:
    """Return one persisted, cooling-enabled synthetic plant."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hydronic plant",
        data={
            CONF_NAME: "Hydronic plant",
            CONF_PLANT_ID: PLANT_ID,
            CONF_DRY_RUN: dry_run,
            "topology": {
                "zones": [
                    {
                        "id": ZONE_ID,
                        "name": "Living",
                        "target_temperature": 24.0,
                        CONF_TEMPERATURE_SENSORS: ["sensor.living_temperature"],
                        "temperature_sensor_metadata": [{"entity_id": "sensor.living_temperature"}],
                        CONF_HUMIDITY_SENSORS: ["sensor.living_humidity"],
                        "humidity_sensor_metadata": [{"entity_id": "sensor.living_humidity"}],
                        CONF_COOLING_START_DELTA: 0.5,
                        CONF_COOLING_STOP_DELTA: 0.2,
                    }
                ],
                "valves": [
                    {
                        "id": VALVE_ID,
                        "name": "Cooling valve",
                        "entity_id": "switch.cooling_valve",
                        "opening_time_seconds": 0.0,
                    }
                ],
                "pumps": [
                    {
                        "id": PUMP_ID,
                        "name": "Cooling pump",
                        "entity_id": "switch.cooling_pump",
                        "overrun_seconds": 120.0,
                    }
                ],
                "circuits": [
                    {
                        "id": CIRCUIT_ID,
                        "name": "Cooling circuit",
                        "valve_ids": [VALVE_ID],
                        "pump_id": PUMP_ID,
                        CONF_COOLING_ENABLED: True,
                        CONF_SUPPLY_TEMPERATURE_SENSOR: "sensor.cooling_supply",
                        CONF_CONDENSATION_MARGIN: 2.0,
                    }
                ],
                "routes": [{"id": ROUTE_ID, "zone_id": ZONE_ID, "circuit_id": CIRCUIT_ID}],
            },
        },
    )


def _shared_mode_entry() -> MockConfigEntry:
    """Return synthetic heating and cooling routes sharing a valve and pump."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Shared mode plant",
        data={
            CONF_NAME: "Shared mode plant",
            CONF_PLANT_ID: PLANT_ID,
            CONF_DRY_RUN: True,
            "topology": {
                "zones": [
                    {
                        "id": HEATING_ZONE_ID,
                        "name": "Heating zone",
                        "target_temperature": 21.0,
                        CONF_TEMPERATURE_SENSORS: ["sensor.heating_temperature"],
                    },
                    {
                        "id": COOLING_ZONE_ID,
                        "name": "Cooling zone",
                        "target_temperature": 24.0,
                        CONF_TEMPERATURE_SENSORS: ["sensor.cooling_temperature"],
                        CONF_HUMIDITY_SENSORS: ["sensor.cooling_humidity"],
                    },
                ],
                "valves": [
                    {
                        "id": VALVE_ID,
                        "name": "Shared valve",
                        "entity_id": "switch.shared_mode_valve",
                        "opening_time_seconds": 0.0,
                    }
                ],
                "pumps": [
                    {
                        "id": PUMP_ID,
                        "name": "Shared pump",
                        "entity_id": "switch.shared_mode_pump",
                        "overrun_seconds": 0.0,
                    }
                ],
                "circuits": [
                    {
                        "id": HEATING_CIRCUIT_ID,
                        "name": "Heating circuit",
                        "valve_ids": [VALVE_ID],
                        "pump_id": PUMP_ID,
                    },
                    {
                        "id": COOLING_CIRCUIT_ID,
                        "name": "Cooling circuit",
                        "valve_ids": [VALVE_ID],
                        "pump_id": PUMP_ID,
                        CONF_COOLING_ENABLED: True,
                        CONF_SUPPLY_TEMPERATURE_SENSOR: "sensor.shared_mode_supply",
                    },
                ],
                "routes": [
                    {
                        "id": HEATING_ROUTE_ID,
                        "zone_id": HEATING_ZONE_ID,
                        "circuit_id": HEATING_CIRCUIT_ID,
                    },
                    {
                        "id": COOLING_ROUTE_ID,
                        "zone_id": COOLING_ZONE_ID,
                        "circuit_id": COOLING_CIRCUIT_ID,
                    },
                ],
            },
        },
    )


async def test_cooling_remains_proposed_when_heating_execution_is_enabled(hass) -> None:
    """Cooling commands never reach Home Assistant while active heating control is enabled."""
    calls: list[tuple[str, str, str]] = []

    async def record(call) -> None:
        calls.append((call.domain, call.service, call.data["entity_id"]))

    hass.services.async_register("switch", "turn_on", record)
    hass.services.async_register("switch", "turn_off", record)
    hass.states.async_set("sensor.living_temperature", "25.0")
    hass.states.async_set("sensor.living_humidity", "50.0")
    hass.states.async_set("sensor.cooling_supply", "18.0")
    hass.states.async_set("switch.cooling_valve", "off")
    hass.states.async_set("switch.cooling_pump", "off")
    entry = _cooling_entry(dry_run=False)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert calls == []
    assert entry.runtime_data.last_execution is not None
    proposed_ids = {
        operation.actuator_id for operation in entry.runtime_data.last_execution.proposed
    }
    assert proposed_ids
    assert proposed_ids <= {VALVE_ID, PUMP_ID}


async def test_cooling_diagnostics_reload_and_shadow_boundary(hass) -> None:
    """Cooling entities follow one evaluation, reload safely, and issue no service calls."""
    hass.states.async_set("sensor.living_temperature", "25.0")
    hass.states.async_set("sensor.living_humidity", "50.0")
    hass.states.async_set("sensor.cooling_supply", "18.0")
    entry = _cooling_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.hydronic_plant_living_cooling_demand").state == "on"
    assert hass.states.get("binary_sensor.hydronic_plant_living_cooling_blocked").state == "off"
    assert float(
        hass.states.get("sensor.hydronic_plant_living_cooling_dew_point").state
    ) == pytest.approx(13.8516, abs=0.001)
    assert float(
        hass.states.get("sensor.hydronic_plant_living_cooling_condensation_margin").state
    ) == pytest.approx(4.1484, abs=0.001)
    assert hass.states.get("sensor.hydronic_plant_living_cooling_blocked_reason").state == "none"
    assert entry.runtime_data.evaluation.control_plan.commands

    hass.states.async_set("sensor.cooling_supply", "15.0")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.hydronic_plant_living_cooling_demand").state == "off"
    assert hass.states.get("binary_sensor.hydronic_plant_living_cooling_blocked").state == "on"
    assert (
        "condensation margin"
        in hass.states.get("sensor.hydronic_plant_living_cooling_blocked_reason").state
    )

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.hydronic_plant_living_cooling_blocked").state == "on"


async def test_shared_mode_arbitration_is_visible_and_shadow_only(hass) -> None:
    """Shared mode conflicts are explained without issuing any physical call."""
    calls: list[tuple[str, str, str]] = []

    async def record(call) -> None:
        calls.append((call.domain, call.service, call.data["entity_id"]))

    hass.services.async_register("switch", "turn_on", record)
    hass.services.async_register("switch", "turn_off", record)
    for entity_id, state in (
        ("sensor.heating_temperature", "19.0"),
        ("sensor.cooling_temperature", "25.0"),
        ("sensor.cooling_humidity", "50.0"),
        ("sensor.shared_mode_supply", "18.0"),
        ("switch.shared_mode_valve", "off"),
        ("switch.shared_mode_pump", "off"),
    ):
        hass.states.async_set(entity_id, state)
    entry = _shared_mode_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert calls == []
    assert entry.runtime_data.evaluation.diagnostics.mode_conflicts
    assert entry.runtime_data.runtime_state.cooling_zone_demands[COOLING_ZONE_ID] is False
    reason = hass.states.get("sensor.shared_mode_plant_cooling_zone_cooling_blocked_reason")
    assert reason is not None
    assert "shared" in reason.state
    preview = hass.states.get("sensor.shared_mode_plant_topology_preview")
    assert preview is not None
    warning_codes = {warning["code"] for warning in preview.attributes["warnings"]}
    assert {
        "shared_valve_limits_independent_control",
        "shared_pump_limits_independent_control",
    } <= warning_codes


async def test_mode_changeover_entities_lock_until_shared_path_is_idle(hass) -> None:
    """The HA mode request exposes the lockout while keeping cooling in Dry run."""
    calls: list[tuple[str, str, str]] = []

    async def record(call) -> None:
        calls.append((call.domain, call.service, call.data["entity_id"]))

    hass.services.async_register("switch", "turn_on", record)
    hass.services.async_register("switch", "turn_off", record)
    for entity_id, state in (
        ("sensor.heating_temperature", "19.0"),
        ("sensor.cooling_temperature", "23.0"),
        ("sensor.cooling_humidity", "50.0"),
        ("sensor.shared_mode_supply", "18.0"),
        ("switch.shared_mode_valve", "off"),
        ("switch.shared_mode_pump", "off"),
    ):
        hass.states.async_set(entity_id, state)
    entry = _shared_mode_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("switch.shared_mode_valve", "on")
    await hass.async_block_till_done()
    hass.states.async_set("switch.shared_mode_pump", "on")
    await hass.async_block_till_done()

    assert hass.states.get("select.shared_mode_plant_requested_mode").state == "auto"
    assert hass.states.get("sensor.shared_mode_plant_operating_mode").state == "heating"

    hass.states.async_set("sensor.cooling_temperature", "25.0")
    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": "select.shared_mode_plant_requested_mode",
            "option": "cooling",
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.shared_mode_plant_mode_changeover_lockout").state == "on"
    assert hass.states.get("sensor.shared_mode_plant_operating_mode").state == "idle"
    assert (
        "safely idle"
        in hass.states.get("sensor.shared_mode_plant_mode_changeover_explanation").state
    )
    assert (
        hass.states.get("binary_sensor.shared_mode_plant_cooling_zone_cooling_demand").state
        == "off"
    )
    assert calls == []


async def test_initial_flow_persists_cooling_fields_and_reloads(hass) -> None:
    """The public setup flow persists cooling topology and sensor relationships."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_NAME: "Hydronic plant"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Living",
            "target_temperature": 24.0,
            CONF_TEMPERATURE_SENSORS: ["sensor.living_temperature"],
            CONF_HUMIDITY_SENSORS: ["sensor.living_humidity"],
            CONF_COOLING_START_DELTA: 0.5,
            CONF_COOLING_STOP_DELTA: 0.2,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Cooling circuit",
            "valve_entity": "switch.cooling_valve",
            "pump_entity": "switch.cooling_pump",
            "valve_opening_time_seconds": 0.0,
            "pump_overrun_seconds": 120.0,
            CONF_COOLING_ENABLED: True,
            CONF_SUPPLY_TEMPERATURE_SENSOR: "sensor.cooling_supply",
            CONF_CONDENSATION_MARGIN: 2.0,
        },
    )
    assert result["type"] == FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    topology = result["data"]["topology"]
    assert topology["zones"][0][CONF_HUMIDITY_SENSORS] == ["sensor.living_humidity"]
    assert topology["zones"][0][CONF_COOLING_START_DELTA] == 0.5
    assert topology["circuits"][0][CONF_COOLING_ENABLED] is True
    assert topology["circuits"][0][CONF_SUPPLY_TEMPERATURE_SENSOR] == "sensor.cooling_supply"

    entry = next(
        item for item in hass.config_entries.async_entries(DOMAIN) if item.data == result["data"]
    )
    circuit = next(iter(entry.runtime_data.plant.circuits.values()))
    assert circuit.cooling_enabled is True
    assert circuit.supply_temperature_sensor == "sensor.cooling_supply"
    assert next(iter(entry.runtime_data.plant.zones.values())).humidity_sensors == (
        "sensor.living_humidity",
    )
    circuit_id = circuit.id
    route_ids = tuple(route.id for route in entry.runtime_data.plant.routes)
    assert await hass.config_entries.async_reload(entry.entry_id)
    reloaded_circuit = entry.runtime_data.plant.circuits[circuit_id]
    assert reloaded_circuit.cooling_enabled is True
    assert tuple(route.id for route in entry.runtime_data.plant.routes) == route_ids
