"""Integration coverage for unresolved binding Repairs and degraded operation."""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronicus.const import CONF_DRY_RUN, CONF_NAME, CONF_PLANT_ID, DOMAIN

PLANT_ID = "00000000-0000-4000-8000-000000000001"
ZONE_A = "00000000-0000-4000-8000-000000000002"
ZONE_B = "00000000-0000-4000-8000-000000000003"
VALVE_A = "00000000-0000-4000-8000-000000000004"
VALVE_B = "00000000-0000-4000-8000-000000000005"
PUMP_A = "00000000-0000-4000-8000-000000000006"
PUMP_B = "00000000-0000-4000-8000-000000000007"
CIRCUIT_A = "00000000-0000-4000-8000-000000000008"
CIRCUIT_B = "00000000-0000-4000-8000-000000000009"
ROUTE_A = "00000000-0000-4000-8000-000000000010"
ROUTE_B = "00000000-0000-4000-8000-000000000011"

MISSING_SENSOR = "sensor.zone_a_temperature"
MISSING_VALVE = "switch.zone_a_valve"
MISSING_READINESS = "binary_sensor.zone_a_valve_ready"


def _entry() -> MockConfigEntry:
    """Build two independent synthetic paths, one intentionally unresolved."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Synthetic plant",
        data={
            CONF_NAME: "Synthetic plant",
            CONF_PLANT_ID: PLANT_ID,
            CONF_DRY_RUN: True,
            "topology": {
                "zones": [
                    {
                        "id": ZONE_A,
                        "name": "Zone A",
                        "target_temperature": 21.0,
                        "temperature_sensor": MISSING_SENSOR,
                    },
                    {
                        "id": ZONE_B,
                        "name": "Zone B",
                        "target_temperature": 21.0,
                        "temperature_sensor": "sensor.zone_b_temperature",
                    },
                ],
                "valves": [
                    {
                        "id": VALVE_A,
                        "name": "Zone A valve",
                        "entity_id": MISSING_VALVE,
                        "readiness_entity_id": MISSING_READINESS,
                    },
                    {
                        "id": VALVE_B,
                        "name": "Zone B valve",
                        "entity_id": "switch.zone_b_valve",
                    },
                ],
                "pumps": [
                    {
                        "id": PUMP_A,
                        "name": "Zone A pump",
                        "entity_id": "switch.zone_a_pump",
                    },
                    {
                        "id": PUMP_B,
                        "name": "Zone B pump",
                        "entity_id": "switch.zone_b_pump",
                    },
                ],
                "circuits": [
                    {
                        "id": CIRCUIT_A,
                        "name": "Zone A circuit",
                        "valve_ids": [VALVE_A],
                        "pump_id": PUMP_A,
                    },
                    {
                        "id": CIRCUIT_B,
                        "name": "Zone B circuit",
                        "valve_ids": [VALVE_B],
                        "pump_id": PUMP_B,
                    },
                ],
                "routes": [
                    {"id": ROUTE_A, "zone_id": ZONE_A, "circuit_id": CIRCUIT_A},
                    {"id": ROUTE_B, "zone_id": ZONE_B, "circuit_id": CIRCUIT_B},
                ],
            },
        },
    )


def _issues(hass):
    """Return active Hydronicus Repairs entries."""
    return {
        issue_id: issue
        for (domain, issue_id), issue in issue_registry.async_get(hass).issues.items()
        if domain == DOMAIN and issue.active
    }


def _register_synthetic_reference(hass, entity_id: str) -> None:
    """Keep a missing synthetic reference in the registry for rename coverage."""
    domain, object_id = entity_id.split(".", 1)
    er.async_get(hass).async_get_or_create(
        domain,
        "synthetic",
        f"repairs:{object_id}",
        suggested_object_id=object_id,
    )


async def test_setup_reload_and_restoration_create_and_remove_repairs(hass) -> None:
    """Missing bindings alert distinctly while the healthy path keeps evaluating."""
    for entity_id in (MISSING_SENSOR, MISSING_VALVE, MISSING_READINESS):
        _register_synthetic_reference(hass, entity_id)
    hass.states.async_set("sensor.zone_b_temperature", "18.0")
    hass.states.async_set("switch.zone_b_valve", "off")
    hass.states.async_set("switch.zone_b_pump", "off")
    hass.states.async_set("switch.zone_a_pump", "off")
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    repairs = _issues(hass)
    translation_keys = {issue.translation_key for issue in repairs.values()}
    assert translation_keys == {
        "missing_sensor_binding",
        "missing_feedback_binding",
        "missing_actuator_binding",
    }
    for issue in repairs.values():
        assert MISSING_SENSOR not in str(issue.translation_placeholders)
        assert MISSING_VALVE not in str(issue.translation_placeholders)
        assert MISSING_READINESS not in str(issue.translation_placeholders)
        assert issue.data is not None
        assert "entity_id" not in issue.data

    runtime = entry.runtime_data
    assert runtime.runtime_state.zone_demands == {ZONE_A: False, ZONE_B: True}
    assert all(
        command.actuator_id not in {VALVE_A, PUMP_A}
        for command in runtime.evaluation.control_plan.commands
    )

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert {issue.translation_key for issue in _issues(hass).values()} == translation_keys

    hass.states.async_set(MISSING_SENSOR, "18.0")
    hass.states.async_set(MISSING_VALVE, "off")
    hass.states.async_set(MISSING_READINESS, "off")
    await hass.async_block_till_done()

    assert _issues(hass) == {}
    assert entry.runtime_data.unresolved_bindings == ()


async def test_unload_removes_repairs_for_removed_plant(hass) -> None:
    """Removing a configured plant does not leave its Repairs behind."""
    for entity_id in (MISSING_SENSOR, MISSING_VALVE, MISSING_READINESS):
        _register_synthetic_reference(hass, entity_id)
    hass.states.async_set("sensor.zone_b_temperature", "18.0")
    hass.states.async_set("switch.zone_b_valve", "off")
    hass.states.async_set("switch.zone_b_pump", "off")
    hass.states.async_set("switch.zone_a_pump", "off")
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _issues(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert _issues(hass) == {}


async def test_reconfigure_replaces_an_unresolved_binding_and_removes_its_repair(hass) -> None:
    """A valid reconfiguration removes the old binding repair after reload."""
    hass.states.async_set("sensor.zone_b_temperature", "18.0")
    hass.states.async_set("switch.zone_b_valve", "off")
    hass.states.async_set("switch.zone_b_pump", "off")
    hass.states.async_set("switch.zone_a_pump", "off")
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert any(
        issue.translation_key == "missing_actuator_binding" for issue in _issues(hass).values()
    )

    updated_data = dict(entry.data)
    updated_data["topology"] = dict(entry.data["topology"])
    updated_data["topology"]["valves"] = [dict(valve) for valve in entry.data["topology"]["valves"]]
    updated_data["topology"]["valves"][0]["entity_id"] = "switch.reconfigured_zone_a_valve"
    hass.states.async_set("switch.reconfigured_zone_a_valve", "off")
    hass.config_entries.async_update_entry(entry, data=updated_data)
    await hass.async_block_till_done()

    assert entry.runtime_data.plant.valves[VALVE_A].entity_id == "switch.reconfigured_zone_a_valve"
    assert all(
        issue.translation_key != "missing_actuator_binding" for issue in _issues(hass).values()
    )
    assert any(
        issue.translation_key == "missing_sensor_binding" for issue in _issues(hass).values()
    )
