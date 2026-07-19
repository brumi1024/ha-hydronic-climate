"""Integration tests for dynamic actuator config subentries."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronicus.const import (
    CONF_CIRCUIT_IDS,
    CONF_ENTITY_ID,
    CONF_NAME,
    CONF_OPENING_TIME,
    DOMAIN,
    SUBENTRY_TYPE_ACTUATOR,
)

PLANT_ID = "00000000-0000-4000-8000-000000000001"
ZONE_ID = "00000000-0000-4000-8000-000000000002"
BASE_VALVE_ID = "00000000-0000-4000-8000-000000000003"
PUMP_ID = "00000000-0000-4000-8000-000000000004"
CIRCUIT_ID = "00000000-0000-4000-8000-000000000005"
ROUTE_ID = "00000000-0000-4000-8000-000000000006"
SECOND_CIRCUIT_ID = "00000000-0000-4000-8000-000000000007"
SECOND_ROUTE_ID = "00000000-0000-4000-8000-000000000008"


async def _confirm_warning_review(hass, result):
    """Acknowledge non-fatal topology warnings when a flow presents them."""
    if result.get("type") == FlowResultType.FORM and result.get("step_id") == "review":
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={"confirm": True}
        )
        await hass.async_block_till_done()
    return result


def _plant_entry(*, with_second_circuit: bool = False) -> MockConfigEntry:
    circuits = [
        {
            "id": CIRCUIT_ID,
            "name": "Floor loop",
            "valve_ids": [BASE_VALVE_ID],
            "pump_id": PUMP_ID,
        }
    ]
    routes = [{"id": ROUTE_ID, "zone_id": ZONE_ID, "circuit_id": CIRCUIT_ID}]
    if with_second_circuit:
        circuits.append(
            {
                "id": SECOND_CIRCUIT_ID,
                "name": "Ceiling loop",
                "valve_ids": [BASE_VALVE_ID],
                "pump_id": PUMP_ID,
            }
        )
        routes.append(
            {
                "id": SECOND_ROUTE_ID,
                "zone_id": ZONE_ID,
                "circuit_id": SECOND_CIRCUIT_ID,
            }
        )
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
                        "id": BASE_VALVE_ID,
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


async def test_add_valve_subentry_reloads_effective_topology(hass) -> None:
    """Adding a valve subentry should attach it to a circuit and reload the plant."""
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    initial_runtime = entry.runtime_data

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ACTUATOR),
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Return valve",
            CONF_ENTITY_ID: "switch.return_valve",
            CONF_OPENING_TIME: 45.0,
            CONF_CIRCUIT_IDS: [CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.runtime_data is not initial_runtime
    added_valves = [
        valve
        for valve in entry.runtime_data.plant.valves.values()
        if valve.entity_id == "switch.return_valve"
    ]
    assert len(added_valves) == 1
    assert entry.runtime_data.plant.circuits[CIRCUIT_ID].valve_ids == (
        BASE_VALVE_ID,
        added_valves[0].id,
    )


async def test_reconfigure_valve_subentry_reloads_effective_topology(hass) -> None:
    """Reconfiguring a valve should retain its UUID and reload its binding."""
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ACTUATOR),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Return valve",
            CONF_ENTITY_ID: "switch.return_valve",
            CONF_OPENING_TIME: 45.0,
            CONF_CIRCUIT_IDS: [CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(iter(entry.subentries.values()))
    actuator_id = subentry.data["id"]
    runtime_after_add = entry.runtime_data

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Updated return valve",
            CONF_ENTITY_ID: "switch.updated_return_valve",
            CONF_OPENING_TIME: 60.0,
            CONF_CIRCUIT_IDS: [CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert subentry.data["id"] == actuator_id
    assert entry.runtime_data is not runtime_after_add
    updated_valve = entry.runtime_data.plant.valves[actuator_id]
    assert updated_valve.name == "Updated return valve"
    assert updated_valve.entity_id == "switch.updated_return_valve"
    assert updated_valve.opening_time_seconds == 60.0


async def test_valve_subentry_can_be_shared_and_moved_between_circuits(hass) -> None:
    """One actuator should attach to many circuits and move atomically on update."""
    entry = _plant_entry(with_second_circuit=True)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ACTUATOR),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Shared valve",
            CONF_ENTITY_ID: "switch.shared_valve",
            CONF_OPENING_TIME: 45.0,
            CONF_CIRCUIT_IDS: [CIRCUIT_ID, SECOND_CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()
    result = await _confirm_warning_review(hass, result)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(iter(entry.subentries.values()))
    actuator_id = subentry.data["id"]
    assert actuator_id in entry.runtime_data.plant.circuits[CIRCUIT_ID].valve_ids
    assert actuator_id in entry.runtime_data.plant.circuits[SECOND_CIRCUIT_ID].valve_ids
    runtime_after_add = entry.runtime_data

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Shared valve",
            CONF_ENTITY_ID: "switch.shared_valve",
            CONF_OPENING_TIME: 45.0,
            CONF_CIRCUIT_IDS: [SECOND_CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()
    result = await _confirm_warning_review(hass, result)

    assert result["type"] == FlowResultType.ABORT
    assert entry.runtime_data is not runtime_after_add
    assert actuator_id not in entry.runtime_data.plant.circuits[CIRCUIT_ID].valve_ids
    assert actuator_id in entry.runtime_data.plant.circuits[SECOND_CIRCUIT_ID].valve_ids


async def test_add_rejects_duplicate_binding_without_mutating_entry(hass) -> None:
    """An invalid new binding should leave subentries and runtime untouched."""
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    initial_runtime = entry.runtime_data

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ACTUATOR),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Duplicate valve",
            CONF_ENTITY_ID: "switch.floor_valve",
            CONF_OPENING_TIME: 45.0,
            CONF_CIRCUIT_IDS: [CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_actuator"}
    assert not entry.subentries
    assert entry.runtime_data is initial_runtime


async def test_reconfigure_rejects_duplicate_binding_without_mutating_entry(hass) -> None:
    """An invalid update should preserve stored data, runtime, and owned entity."""
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ACTUATOR),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Return valve",
            CONF_ENTITY_ID: "switch.return_valve",
            CONF_OPENING_TIME: 45.0,
            CONF_CIRCUIT_IDS: [CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(iter(entry.subentries.values()))
    original_data = dict(subentry.data)
    runtime_after_add = entry.runtime_data
    entity_id = "binary_sensor.hydronic_plant_return_valve_requested"

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Invalid update",
            CONF_ENTITY_ID: "switch.floor_valve",
            CONF_OPENING_TIME: 60.0,
            CONF_CIRCUIT_IDS: [CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "invalid_actuator"}
    assert dict(subentry.data) == original_data
    assert entry.runtime_data is runtime_after_add
    assert hass.states.get(entity_id) is not None


async def test_delete_valve_subentry_removes_runtime_and_entity(hass) -> None:
    """Deleting a valve subentry should restore the base graph without stale entities."""
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ACTUATOR),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Return valve",
            CONF_ENTITY_ID: "switch.return_valve",
            CONF_OPENING_TIME: 45.0,
            CONF_CIRCUIT_IDS: [CIRCUIT_ID],
        },
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(iter(entry.subentries.values()))
    actuator_id = subentry.data["id"]
    entity_id = "binary_sensor.hydronic_plant_return_valve_requested"
    registry = er.async_get(hass)
    registry_entry = registry.async_get(entity_id)

    assert hass.states.get(entity_id) is not None
    assert registry_entry is not None
    assert registry_entry.config_subentry_id == subentry.subentry_id

    assert hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
    await hass.async_block_till_done()

    assert actuator_id not in entry.runtime_data.plant.valves
    assert entry.runtime_data.plant.circuits[CIRCUIT_ID].valve_ids == (BASE_VALVE_ID,)
    assert hass.states.get(entity_id) is None
    assert registry.async_get(entity_id) is None
