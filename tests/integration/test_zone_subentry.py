"""Integration tests for dynamic comfort-zone config subentries."""

from __future__ import annotations

import math
from copy import deepcopy
from types import MappingProxyType
from uuid import UUID

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronicus.const import (
    CONF_CIRCUIT_IDS,
    CONF_NAME,
    CONF_TARGET_TEMPERATURE,
    CONF_TEMPERATURE_SENSOR,
    CONF_TEMPERATURE_SENSOR_METADATA,
    CONF_TEMPERATURE_SENSORS,
    DOMAIN,
    SUBENTRY_TYPE_ZONE,
)

PLANT_ID = "00000000-0000-4000-8000-000000000001"
BASE_ZONE_ID = "00000000-0000-4000-8000-000000000002"
VALVE_ID = "00000000-0000-4000-8000-000000000003"
PUMP_ID = "00000000-0000-4000-8000-000000000004"
CIRCUIT_ID = "00000000-0000-4000-8000-000000000005"
BASE_ROUTE_ID = "00000000-0000-4000-8000-000000000006"
SECOND_CIRCUIT_ID = "00000000-0000-4000-8000-000000000007"
SECOND_ROUTE_ID = "00000000-0000-4000-8000-000000000008"
LEGACY_ZONE_ID = "00000000-0000-4000-8000-000000000009"
LEGACY_ROUTE_ID = "00000000-0000-4000-8000-000000000010"


async def _confirm_warning_review(hass, result):
    """Acknowledge non-fatal topology warnings when a flow presents them."""
    if result.get("type") == FlowResultType.FORM and result.get("step_id") == "review":
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={"confirm": True}
        )
        await hass.async_block_till_done()
    return result


def _plant_entry(
    *, with_second_circuit: bool = False, preset_targets: dict[str, float] | None = None
) -> MockConfigEntry:
    """Return one valid parent-owned zone and hydraulic path."""
    circuits = [
        {
            "id": CIRCUIT_ID,
            "name": "Floor loop",
            "valve_ids": [VALVE_ID],
            "pump_id": PUMP_ID,
        }
    ]
    routes = [
        {
            "id": BASE_ROUTE_ID,
            "zone_id": BASE_ZONE_ID,
            "circuit_id": CIRCUIT_ID,
        }
    ]
    if with_second_circuit:
        circuits.append(
            {
                "id": SECOND_CIRCUIT_ID,
                "name": "Ceiling loop",
                "valve_ids": [VALVE_ID],
                "pump_id": PUMP_ID,
            }
        )
        routes.append(
            {
                "id": SECOND_ROUTE_ID,
                "zone_id": BASE_ZONE_ID,
                "circuit_id": SECOND_CIRCUIT_ID,
            }
        )
    zone_data = {
        "id": BASE_ZONE_ID,
        "name": "Living room",
        "target_temperature": 21.0,
        "temperature_sensor": "sensor.living_temperature",
    }
    if preset_targets is not None:
        zone_data["preset_targets"] = preset_targets
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hydronic plant",
        data={
            "name": "Hydronic plant",
            "plant_id": PLANT_ID,
            "dry_run": True,
            "topology": {
                "zones": [zone_data],
                "valves": [
                    {
                        "id": VALVE_ID,
                        "name": "Floor valve",
                        "entity_id": "switch.floor_valve",
                        "opening_time_seconds": 30.0,
                    }
                ],
                "pumps": [
                    {
                        "id": PUMP_ID,
                        "name": "Floor pump",
                        "entity_id": "switch.floor_pump",
                        "overrun_seconds": 120.0,
                    }
                ],
                "circuits": circuits,
                "routes": routes,
            },
        },
    )


async def _add_zone(hass, entry, *, circuit_ids: list[str]):
    """Add one zone through the public subentry flow."""
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ZONE),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Office",
            CONF_TARGET_TEMPERATURE: 20.0,
            CONF_TEMPERATURE_SENSORS: ["sensor.office_temperature"],
            CONF_CIRCUIT_IDS: circuit_ids,
        },
    )
    return await _confirm_warning_review(hass, result)


async def test_add_zone_subentry_composes_route_and_owned_entities(hass) -> None:
    """A UI-created zone should evaluate and own its shadow entities."""
    hass.states.async_set("sensor.living_temperature", "21.5")
    hass.states.async_set("sensor.office_temperature", "18.0")
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    initial_runtime = entry.runtime_data

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ZONE),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Office",
            CONF_TARGET_TEMPERATURE: 20.0,
            CONF_TEMPERATURE_SENSORS: ["sensor.office_temperature"],
            CONF_CIRCUIT_IDS: [CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.runtime_data is not initial_runtime
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_ZONE
    )
    zone_id = subentry.data["id"]
    assert str(UUID(zone_id)) == zone_id
    zone = entry.runtime_data.plant.zones[zone_id]
    assert zone.name == "Office"
    assert zone.target_temperature == 20.0
    assert zone.temperature_sensors == ("sensor.office_temperature",)
    routes = [route for route in entry.runtime_data.plant.routes if route.zone_id == zone_id]
    assert len(routes) == 1
    assert routes[0].circuit_id == CIRCUIT_ID
    assert str(UUID(routes[0].id)) == routes[0].id

    demand_entity_id = "binary_sensor.hydronic_plant_office_demand"
    explanation_entity_id = "sensor.hydronic_plant_office_explanation"
    assert hass.states.get(demand_entity_id).state == "on"
    assert hass.states.get(explanation_entity_id).state.startswith("Heating requested:")
    assert hass.states.get("sensor.hydronic_plant_office_aggregate_temperature").state == "18.0"
    assert hass.states.get("binary_sensor.hydronic_plant_office_blocked").state == "off"
    assert hass.states.get("sensor.hydronic_plant_office_blocked_reason").state == "none"
    registry = er.async_get(hass)
    assert registry.async_get(demand_entity_id).config_subentry_id == subentry.subentry_id
    assert registry.async_get(explanation_entity_id).config_subentry_id == subentry.subentry_id

    climate_state = hass.states.get("climate.hydronic_plant_office")
    assert climate_state is not None
    assert climate_state.state == "heat"
    assert climate_state.attributes["hvac_action"] == "heating"
    assert climate_state.attributes["current_temperature"] == 18.0
    assert climate_state.attributes["temperature"] == 20.0

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.hydronic_plant_office", "temperature": 17.0},
        blocking=True,
    )
    await hass.async_block_till_done()

    climate_state = hass.states.get("climate.hydronic_plant_office")
    assert climate_state is not None
    assert climate_state.attributes["hvac_action"] == "idle"
    assert climate_state.attributes["temperature"] == 17.0
    assert subentry.data[CONF_TARGET_TEMPERATURE] == 17.0


async def test_zone_presets_recalculate_and_manual_target_clears_mode(hass) -> None:
    """Configured presets persist their target and manual control returns to none."""
    hass.states.async_set("sensor.living_temperature", "18.0")
    entry = _plant_entry(preset_targets={"comfort": 22.0, "eco": 19.0})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    climate_entity_id = "climate.hydronic_plant_living_room"
    climate_state = hass.states.get(climate_entity_id)
    assert climate_state is not None
    assert climate_state.attributes["preset_modes"] == ["comfort", "eco"]
    assert climate_state.attributes["preset_mode"] == "none"

    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": climate_entity_id, "preset_mode": "comfort"},
        blocking=True,
    )
    await hass.async_block_till_done()

    climate_state = hass.states.get(climate_entity_id)
    assert climate_state is not None
    assert climate_state.attributes["preset_mode"] == "comfort"
    assert climate_state.attributes["temperature"] == 22.0

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": climate_entity_id, "temperature": 18.5},
        blocking=True,
    )
    await hass.async_block_till_done()

    climate_state = hass.states.get(climate_entity_id)
    assert climate_state is not None
    assert climate_state.attributes["preset_mode"] == "none"
    assert climate_state.attributes["temperature"] == 18.5


async def test_add_zone_subentry_aggregates_multiple_battery_sensors(hass) -> None:
    """A UI-created zone should average all selected battery sensor states."""
    hass.states.async_set("sensor.living_temperature", "21.5")
    hass.states.async_set("sensor.office_wall_temperature", "18.0")
    hass.states.async_set("sensor.office_window_temperature", "20.0")
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ZONE),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Office",
            CONF_TARGET_TEMPERATURE: 20.0,
            CONF_TEMPERATURE_SENSORS: [
                "sensor.office_wall_temperature",
                "sensor.office_window_temperature",
            ],
            CONF_CIRCUIT_IDS: [CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_ZONE
    )
    zone_id = subentry.data["id"]
    assert entry.runtime_data.plant.zones[zone_id].temperature_sensors == (
        "sensor.office_wall_temperature",
        "sensor.office_window_temperature",
    )
    assert hass.states.get("binary_sensor.hydronic_plant_office_demand").state == "on"
    assert hass.states.get("sensor.hydronic_plant_office_explanation").state.startswith(
        "Heating requested: 19.0"
    )

    hass.states.async_set("sensor.office_window_temperature", "24.0")
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.hydronic_plant_office_demand").state == "off"
    assert hass.states.get("sensor.hydronic_plant_office_explanation").state.startswith(
        "Satisfied: 21.0"
    )

    hass.states.async_set("sensor.office_wall_temperature", "14.0")
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.hydronic_plant_office_demand").state == "on"
    assert hass.states.get("sensor.hydronic_plant_office_explanation").state.startswith(
        "Heating requested: 19.0"
    )

    hass.states.async_set("sensor.office_window_temperature", "unavailable")
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.hydronic_plant_office_demand").state == "off"
    assert hass.states.get("sensor.hydronic_plant_office_explanation").state.startswith("Blocked:")


async def test_reconfigure_migrates_legacy_single_sensor_subentry(hass) -> None:
    """A milestone 2 zone should load and migrate without changing its UUID."""
    hass.states.async_set("sensor.legacy_office_temperature", "18.0")
    hass.states.async_set("sensor.office_backup_temperature", "20.0")
    entry = _plant_entry()
    entry.add_to_hass(hass)
    legacy_subentry = config_entries.ConfigSubentry(
        data=MappingProxyType(
            {
                "id": LEGACY_ZONE_ID,
                CONF_NAME: "Legacy office",
                CONF_TARGET_TEMPERATURE: 20.0,
                CONF_TEMPERATURE_SENSOR: "sensor.legacy_office_temperature",
                CONF_CIRCUIT_IDS: [CIRCUIT_ID],
                "routes": [
                    {
                        "id": LEGACY_ROUTE_ID,
                        "circuit_id": CIRCUIT_ID,
                    }
                ],
            }
        ),
        subentry_type=SUBENTRY_TYPE_ZONE,
        title="Legacy office",
        unique_id=LEGACY_ZONE_ID,
    )
    assert hass.config_entries.async_add_subentry(entry, legacy_subentry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.runtime_data.plant.zones[LEGACY_ZONE_ID].temperature_sensors == (
        "sensor.legacy_office_temperature",
    )

    result = await entry.start_subentry_reconfigure_flow(hass, legacy_subentry.subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Legacy office",
            CONF_TARGET_TEMPERATURE: 20.0,
            CONF_TEMPERATURE_SENSORS: [
                "sensor.legacy_office_temperature",
                "sensor.office_backup_temperature",
            ],
            CONF_CIRCUIT_IDS: [CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()
    result = await _confirm_warning_review(hass, result)

    assert result["type"] == FlowResultType.ABORT
    assert legacy_subentry.data["id"] == LEGACY_ZONE_ID
    assert CONF_TEMPERATURE_SENSOR not in legacy_subentry.data
    assert legacy_subentry.data[CONF_TEMPERATURE_SENSORS] == [
        "sensor.legacy_office_temperature",
        "sensor.office_backup_temperature",
    ]
    assert entry.runtime_data.plant.zones[LEGACY_ZONE_ID].temperature_sensors == (
        "sensor.legacy_office_temperature",
        "sensor.office_backup_temperature",
    )


async def test_reconfigure_zone_preserves_zone_and_retained_route_uuids(hass) -> None:
    """Updating a zone should retain its identity and surviving route identity."""
    hass.states.async_set("sensor.living_temperature", "21.5")
    hass.states.async_set("sensor.office_temperature", "18.0")
    hass.states.async_set("sensor.study_temperature", "19.0")
    entry = _plant_entry(with_second_circuit=True)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await _add_zone(hass, entry, circuit_ids=[CIRCUIT_ID, SECOND_CIRCUIT_ID])
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_ZONE
    )
    zone_id = subentry.data["id"]
    route_ids = {route["circuit_id"]: route["id"] for route in subentry.data["routes"]}
    runtime_after_add = entry.runtime_data

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Study",
            CONF_TARGET_TEMPERATURE: 21.5,
            CONF_TEMPERATURE_SENSORS: ["sensor.study_temperature"],
            CONF_CIRCUIT_IDS: [SECOND_CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()
    result = await _confirm_warning_review(hass, result)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.runtime_data is not runtime_after_add
    assert subentry.data["id"] == zone_id
    assert subentry.title == "Study"
    assert subentry.data["routes"] == [
        {
            "id": route_ids[SECOND_CIRCUIT_ID],
            "circuit_id": SECOND_CIRCUIT_ID,
        }
    ]
    zone = entry.runtime_data.plant.zones[zone_id]
    assert zone.name == "Study"
    assert zone.target_temperature == 21.5
    assert zone.temperature_sensors == ("sensor.study_temperature",)
    routes = [route for route in entry.runtime_data.plant.routes if route.zone_id == zone_id]
    assert [(route.id, route.circuit_id) for route in routes] == [
        (route_ids[SECOND_CIRCUIT_ID], SECOND_CIRCUIT_ID)
    ]
    registry = er.async_get(hass)
    assert (
        registry.async_get("binary_sensor.hydronic_plant_office_demand").unique_id
        == f"{PLANT_ID}_{zone_id}_demand"
    )
    assert (
        registry.async_get("sensor.hydronic_plant_office_explanation").unique_id
        == f"{PLANT_ID}_{zone_id}_explanation"
    )


async def test_reconfigure_zone_preserves_sensor_metadata(hass) -> None:
    """The basic reconfigure form must retain detailed metadata for selected sensors."""
    hass.states.async_set("sensor.living_temperature", "21.5")
    hass.states.async_set("sensor.office_temperature", "18.0")
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await _add_zone(hass, entry, circuit_ids=[CIRCUIT_ID])
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_ZONE
    )
    metadata = [
        {
            "entity_id": "sensor.office_temperature",
            "required": False,
            "weight": 2.5,
            "calibration_offset": -0.4,
            "max_age_seconds": 900.0,
            "designated_reference": True,
        }
    ]
    hass.config_entries.async_update_subentry(
        entry,
        subentry,
        data={**subentry.data, CONF_TEMPERATURE_SENSOR_METADATA: metadata},
    )
    await hass.async_block_till_done()

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Office renamed",
            CONF_TARGET_TEMPERATURE: 20.5,
            CONF_TEMPERATURE_SENSORS: ["sensor.office_temperature"],
            CONF_CIRCUIT_IDS: [CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()
    result = await _confirm_warning_review(hass, result)

    assert result["reason"] == "reconfigure_successful"
    assert subentry.data[CONF_TEMPERATURE_SENSOR_METADATA] == metadata


async def test_reconfigure_zone_preserves_retained_route_enablement(hass) -> None:
    """Reconfiguration must not silently re-enable a retained route."""
    entry = _plant_entry(with_second_circuit=True)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await _add_zone(hass, entry, circuit_ids=[CIRCUIT_ID, SECOND_CIRCUIT_ID])
    await hass.async_block_till_done()
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_ZONE
    )
    disabled_route_id = next(
        route["id"] for route in subentry.data["routes"] if route["circuit_id"] == CIRCUIT_ID
    )
    updated_data = {
        **subentry.data,
        "routes": [dict(route) for route in subentry.data["routes"]],
    }
    updated_data["routes"][0]["enabled"] = False
    hass.config_entries.async_update_subentry(entry, subentry, data=updated_data)
    await hass.async_block_till_done()

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: subentry.data[CONF_NAME],
            CONF_TARGET_TEMPERATURE: subentry.data[CONF_TARGET_TEMPERATURE],
            CONF_TEMPERATURE_SENSORS: subentry.data[CONF_TEMPERATURE_SENSORS],
            CONF_CIRCUIT_IDS: [CIRCUIT_ID, SECOND_CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()
    result = await _confirm_warning_review(hass, result)

    assert result["reason"] == "reconfigure_successful"
    assert subentry.data["routes"][0] == {
        "id": disabled_route_id,
        "circuit_id": CIRCUIT_ID,
        "enabled": False,
    }


async def test_add_rejects_stale_circuit_without_mutating_entry(hass) -> None:
    """A stale circuit selection should fail before persistence or reload."""
    entry = _plant_entry(with_second_circuit=True)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ZONE),
        context={"source": config_entries.SOURCE_USER},
    )

    updated_data = deepcopy(dict(entry.data))
    updated_data["topology"]["circuits"] = [updated_data["topology"]["circuits"][0]]
    updated_data["topology"]["routes"] = [updated_data["topology"]["routes"][0]]
    hass.config_entries.async_update_entry(entry, data=updated_data)
    await hass.async_block_till_done()
    initial_runtime = entry.runtime_data

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Office",
            CONF_TARGET_TEMPERATURE: 20.0,
            CONF_TEMPERATURE_SENSORS: ["sensor.office_temperature"],
            CONF_CIRCUIT_IDS: [SECOND_CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_zone"}
    assert not entry.subentries
    assert entry.runtime_data is initial_runtime


async def test_add_and_reconfigure_reject_non_finite_target_atomically(hass) -> None:
    """Invalid demand thresholds should never persist or replace a valid runtime."""
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    initial_runtime = entry.runtime_data

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ZONE),
        context={"source": config_entries.SOURCE_USER},
    )
    with pytest.raises(InvalidData):
        await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={
                CONF_NAME: "Invalid office",
                CONF_TARGET_TEMPERATURE: math.inf,
                CONF_TEMPERATURE_SENSORS: ["sensor.office_temperature"],
                CONF_CIRCUIT_IDS: [CIRCUIT_ID],
            },
        )
    await hass.async_block_till_done()

    assert not entry.subentries
    assert entry.runtime_data is initial_runtime

    result = await _add_zone(hass, entry, circuit_ids=[CIRCUIT_ID])
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_ZONE
    )
    original_data = dict(subentry.data)
    valid_runtime = entry.runtime_data
    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={
                CONF_NAME: "Invalid update",
                CONF_TARGET_TEMPERATURE: math.nan,
                CONF_TEMPERATURE_SENSORS: ["sensor.office_temperature"],
                CONF_CIRCUIT_IDS: [CIRCUIT_ID],
            },
        )
    await hass.async_block_till_done()

    assert dict(subentry.data) == original_data
    assert entry.runtime_data is valid_runtime


async def test_reconfigure_rejects_stale_circuit_atomically(hass) -> None:
    """An invalid zone update should preserve stored data and runtime."""
    entry = _plant_entry(with_second_circuit=True)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    result = await _add_zone(hass, entry, circuit_ids=[CIRCUIT_ID])
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_ZONE
    )

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["type"] == FlowResultType.FORM
    updated_data = deepcopy(dict(entry.data))
    updated_data["topology"]["circuits"] = [updated_data["topology"]["circuits"][0]]
    updated_data["topology"]["routes"] = [updated_data["topology"]["routes"][0]]
    hass.config_entries.async_update_entry(entry, data=updated_data)
    await hass.async_block_till_done()
    original_data = dict(subentry.data)
    valid_runtime = entry.runtime_data

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Invalid update",
            CONF_TARGET_TEMPERATURE: 22.0,
            CONF_TEMPERATURE_SENSORS: ["sensor.office_temperature"],
            CONF_CIRCUIT_IDS: [SECOND_CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "invalid_zone"}
    assert dict(subentry.data) == original_data
    assert entry.runtime_data is valid_runtime


async def test_delete_zone_subentry_removes_routes_and_owned_entities(hass) -> None:
    """Deleting a zone should restore the base graph without stale entities."""
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    result = await _add_zone(hass, entry, circuit_ids=[CIRCUIT_ID])
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_ZONE
    )
    zone_id = subentry.data["id"]
    demand_entity_id = "binary_sensor.hydronic_plant_office_demand"
    explanation_entity_id = "sensor.hydronic_plant_office_explanation"
    registry = er.async_get(hass)
    assert registry.async_get(demand_entity_id).config_subentry_id == subentry.subentry_id
    assert registry.async_get(explanation_entity_id).config_subentry_id == subentry.subentry_id

    assert hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
    await hass.async_block_till_done()

    assert zone_id not in entry.runtime_data.plant.zones
    assert all(route.zone_id != zone_id for route in entry.runtime_data.plant.routes)
    assert set(entry.runtime_data.plant.zones) == {BASE_ZONE_ID}
    assert hass.states.get(demand_entity_id) is None
    assert hass.states.get(explanation_entity_id) is None
    assert registry.async_get(demand_entity_id) is None
    assert registry.async_get(explanation_entity_id) is None


async def test_reload_reconstructs_persisted_zone_subentry(hass) -> None:
    """A later plant reload should reconstruct the zone and route ownership."""
    hass.states.async_set("sensor.office_temperature", "18.0")
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    result = await _add_zone(hass, entry, circuit_ids=[CIRCUIT_ID])
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_ZONE
    )
    zone_id = subentry.data["id"]
    route_ids = {route["id"] for route in subentry.data["routes"]}
    runtime_after_add = entry.runtime_data

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data is not runtime_after_add
    assert entry.runtime_data.plant.zones[zone_id].name == "Office"
    assert {
        route.id for route in entry.runtime_data.plant.routes if route.zone_id == zone_id
    } == route_ids
    registry = er.async_get(hass)
    assert (
        registry.async_get("binary_sensor.hydronic_plant_office_demand").config_subentry_id
        == subentry.subentry_id
    )
    assert (
        registry.async_get("sensor.hydronic_plant_office_explanation").config_subentry_id
        == subentry.subentry_id
    )


async def test_dynamic_zone_can_share_parent_circuit_demand(hass) -> None:
    """Two zones may share one circuit without duplicating actuator consumers."""
    hass.states.async_set("sensor.living_temperature", "22.0")
    hass.states.async_set("sensor.office_temperature", "18.0")
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    result = await _add_zone(hass, entry, circuit_ids=[CIRCUIT_ID])
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_ZONE
    )
    zone_id = subentry.data["id"]

    assert entry.runtime_data.runtime_state.zone_demands == {
        BASE_ZONE_ID: False,
        zone_id: True,
    }
    assert entry.runtime_data.evaluation.control_plan.valve_consumers == {
        VALVE_ID: frozenset({CIRCUIT_ID})
    }
