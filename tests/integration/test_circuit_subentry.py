"""Integration tests for dynamic circuit config subentries."""

from __future__ import annotations

from copy import deepcopy
from uuid import UUID

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronicus.const import (
    CONF_NAME,
    CONF_PUMP_ID,
    CONF_VALVE_IDS,
    CONF_ZONE_IDS,
    DOMAIN,
    SUBENTRY_TYPE_CIRCUIT,
)

PLANT_ID = "00000000-0000-4000-8000-000000000001"
LIVING_ZONE_ID = "00000000-0000-4000-8000-000000000002"
OFFICE_ZONE_ID = "00000000-0000-4000-8000-000000000003"
VALVE_ID = "00000000-0000-4000-8000-000000000004"
PUMP_ID = "00000000-0000-4000-8000-000000000005"
BASE_CIRCUIT_ID = "00000000-0000-4000-8000-000000000006"
LIVING_ROUTE_ID = "00000000-0000-4000-8000-000000000007"
OFFICE_ROUTE_ID = "00000000-0000-4000-8000-000000000008"
SECOND_VALVE_ID = "00000000-0000-4000-8000-000000000009"


async def _confirm_warning_review(hass, result):
    """Acknowledge non-fatal topology warnings when a flow presents them."""
    if result.get("type") == FlowResultType.FORM and result.get("step_id") == "review":
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input={"confirm": True}
        )
        await hass.async_block_till_done()
    return result


def _plant_entry() -> MockConfigEntry:
    """Return a valid two-zone plant whose equipment can be shared."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hydronic plant",
        data={
            "name": "Hydronic plant",
            "plant_id": PLANT_ID,
            "shadow_mode": True,
            "topology": {
                "zones": [
                    {
                        "id": LIVING_ZONE_ID,
                        "name": "Living room",
                        "target_temperature": 21.0,
                        "temperature_sensor": "sensor.living_temperature",
                    },
                    {
                        "id": OFFICE_ZONE_ID,
                        "name": "Office",
                        "target_temperature": 20.0,
                        "temperature_sensor": "sensor.office_temperature",
                    },
                ],
                "valves": [
                    {
                        "id": VALVE_ID,
                        "name": "Shared supply valve",
                        "entity_id": "switch.shared_supply_valve",
                        "opening_time_seconds": 30.0,
                    },
                    {
                        "id": SECOND_VALVE_ID,
                        "name": "Shared return valve",
                        "entity_id": "switch.shared_return_valve",
                        "opening_time_seconds": 45.0,
                    },
                ],
                "pumps": [
                    {
                        "id": PUMP_ID,
                        "name": "Shared pump",
                        "entity_id": "switch.shared_pump",
                        "overrun_seconds": 120.0,
                    }
                ],
                "circuits": [
                    {
                        "id": BASE_CIRCUIT_ID,
                        "name": "Floor loop",
                        "valve_ids": [VALVE_ID, SECOND_VALVE_ID],
                        "pump_id": PUMP_ID,
                    }
                ],
                "routes": [
                    {
                        "id": LIVING_ROUTE_ID,
                        "zone_id": LIVING_ZONE_ID,
                        "circuit_id": BASE_CIRCUIT_ID,
                    },
                    {
                        "id": OFFICE_ROUTE_ID,
                        "zone_id": OFFICE_ZONE_ID,
                        "circuit_id": BASE_CIRCUIT_ID,
                    },
                ],
            },
        },
    )


async def test_topology_preview_updates_after_adding_shared_circuit(hass) -> None:
    """The plant should expose its compiled routes and shared equipment after reload."""
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    entity_id = "sensor.hydronic_plant_topology_preview"

    preview = hass.states.get(entity_id)
    assert preview is not None
    assert preview.state == "2 zones, 1 circuit"
    assert preview.attributes["logic_summary"] == [
        "Circuit Floor loop opens valves Shared supply valve, Shared return valve "
        "before requesting pump Shared pump.",
        "Zone Living room can request circuit Floor loop.",
        "Zone Office can request circuit Floor loop.",
    ]

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CIRCUIT),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Ceiling loop",
            CONF_ZONE_IDS: [LIVING_ZONE_ID, OFFICE_ZONE_ID],
            CONF_VALVE_IDS: [VALVE_ID, SECOND_VALVE_ID],
            CONF_PUMP_ID: PUMP_ID,
        },
    )
    await hass.async_block_till_done()
    result = await _confirm_warning_review(hass, result)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    preview = hass.states.get(entity_id)
    assert preview is not None
    assert preview.state == "2 zones, 2 circuits"
    assert (
        "Zone Living room can request circuits Floor loop, Ceiling loop."
        in (preview.attributes["logic_summary"])
    )
    assert (
        "Valve Shared supply valve is shared by circuits Floor loop, Ceiling loop."
        in (preview.attributes["logic_summary"])
    )
    assert (
        "Pump Shared pump is shared by circuits Floor loop, Ceiling loop."
        in (preview.attributes["logic_summary"])
    )


async def test_add_circuit_subentry_composes_multi_zone_routes(hass) -> None:
    """One UI-created circuit should serve many zones through stable routes."""
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    initial_runtime = entry.runtime_data

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CIRCUIT),
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Ceiling loop",
            CONF_ZONE_IDS: [LIVING_ZONE_ID, OFFICE_ZONE_ID],
            CONF_VALVE_IDS: [VALVE_ID, SECOND_VALVE_ID],
            CONF_PUMP_ID: PUMP_ID,
        },
    )
    await hass.async_block_till_done()
    result = await _confirm_warning_review(hass, result)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.runtime_data is not initial_runtime
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_CIRCUIT
    )
    circuit_id = subentry.data["id"]
    UUID(circuit_id)
    circuit = entry.runtime_data.plant.circuits[circuit_id]
    assert circuit.name == "Ceiling loop"
    assert circuit.valve_ids == (VALVE_ID, SECOND_VALVE_ID)
    assert circuit.pump_id == PUMP_ID
    routes = [route for route in entry.runtime_data.plant.routes if route.circuit_id == circuit_id]
    assert {route.zone_id for route in routes} == {LIVING_ZONE_ID, OFFICE_ZONE_ID}
    assert len({route.id for route in routes}) == 2
    assert all(str(UUID(route.id)) == route.id for route in routes)


async def test_reconfigure_circuit_preserves_retained_relationship_uuids(hass) -> None:
    """Updating served zones should retain circuit and surviving route identities."""
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CIRCUIT),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Ceiling loop",
            CONF_ZONE_IDS: [LIVING_ZONE_ID, OFFICE_ZONE_ID],
            CONF_VALVE_IDS: [VALVE_ID],
            CONF_PUMP_ID: PUMP_ID,
        },
    )
    await hass.async_block_till_done()
    result = await _confirm_warning_review(hass, result)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_CIRCUIT
    )
    circuit_id = subentry.data["id"]
    route_ids = {route["zone_id"]: route["id"] for route in subentry.data["routes"]}
    runtime_after_add = entry.runtime_data

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Updated ceiling loop",
            CONF_ZONE_IDS: [OFFICE_ZONE_ID],
            CONF_VALVE_IDS: [VALVE_ID],
            CONF_PUMP_ID: PUMP_ID,
        },
    )
    await hass.async_block_till_done()
    result = await _confirm_warning_review(hass, result)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.runtime_data is not runtime_after_add
    assert subentry.data["id"] == circuit_id
    assert subentry.title == "Updated ceiling loop"
    assert subentry.data["routes"] == [{"id": route_ids[OFFICE_ZONE_ID], "zone_id": OFFICE_ZONE_ID}]
    circuit = entry.runtime_data.plant.circuits[circuit_id]
    assert circuit.name == "Updated ceiling loop"
    routes = [route for route in entry.runtime_data.plant.routes if route.circuit_id == circuit_id]
    assert [(route.id, route.zone_id) for route in routes] == [
        (route_ids[OFFICE_ZONE_ID], OFFICE_ZONE_ID)
    ]


async def test_reconfigure_circuit_preserves_retained_route_enablement(hass) -> None:
    """Reconfiguration must not silently re-enable a retained route."""
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CIRCUIT),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Ceiling loop",
            CONF_ZONE_IDS: [LIVING_ZONE_ID, OFFICE_ZONE_ID],
            CONF_VALVE_IDS: [VALVE_ID],
            CONF_PUMP_ID: PUMP_ID,
        },
    )
    await hass.async_block_till_done()
    result = await _confirm_warning_review(hass, result)
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_CIRCUIT
    )
    disabled_route_id = next(
        route["id"] for route in subentry.data["routes"] if route["zone_id"] == LIVING_ZONE_ID
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
            CONF_NAME: "Ceiling loop",
            CONF_ZONE_IDS: [LIVING_ZONE_ID, OFFICE_ZONE_ID],
            CONF_VALVE_IDS: [VALVE_ID],
            CONF_PUMP_ID: PUMP_ID,
        },
    )
    await hass.async_block_till_done()
    result = await _confirm_warning_review(hass, result)

    assert result["reason"] == "reconfigure_successful"
    assert subentry.data["routes"] == [
        {"id": disabled_route_id, "zone_id": LIVING_ZONE_ID, "enabled": False},
        {
            "id": next(
                route["id"]
                for route in updated_data["routes"]
                if route["zone_id"] == OFFICE_ZONE_ID
            ),
            "zone_id": OFFICE_ZONE_ID,
        },
    ]


async def test_add_rejects_unknown_relationship_without_mutating_entry(hass) -> None:
    """A stale topology selection should fail before persistence or reload."""
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CIRCUIT),
        context={"source": config_entries.SOURCE_USER},
    )

    updated_data = deepcopy(dict(entry.data))
    updated_data["topology"]["zones"] = [updated_data["topology"]["zones"][0]]
    updated_data["topology"]["routes"] = [updated_data["topology"]["routes"][0]]
    hass.config_entries.async_update_entry(entry, data=updated_data)
    await hass.async_block_till_done()
    initial_runtime = entry.runtime_data

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Invalid loop",
            CONF_ZONE_IDS: [OFFICE_ZONE_ID],
            CONF_VALVE_IDS: [VALVE_ID],
            CONF_PUMP_ID: PUMP_ID,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_circuit"}
    assert not entry.subentries
    assert entry.runtime_data is initial_runtime


async def test_delete_circuit_subentry_restores_base_topology(hass) -> None:
    """Deleting a circuit should atomically remove its circuit and routes."""
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CIRCUIT),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Ceiling loop",
            CONF_ZONE_IDS: [LIVING_ZONE_ID, OFFICE_ZONE_ID],
            CONF_VALVE_IDS: [VALVE_ID],
            CONF_PUMP_ID: PUMP_ID,
        },
    )
    await hass.async_block_till_done()
    result = await _confirm_warning_review(hass, result)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_CIRCUIT
    )
    circuit_id = subentry.data["id"]
    runtime_after_add = entry.runtime_data

    assert hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data is not runtime_after_add
    assert circuit_id not in entry.runtime_data.plant.circuits
    assert all(route.circuit_id != circuit_id for route in entry.runtime_data.plant.routes)
    assert set(entry.runtime_data.plant.circuits) == {BASE_CIRCUIT_ID}


async def test_reconfigure_rejects_stale_relationship_atomically(hass) -> None:
    """An invalid update should preserve subentry data and the valid runtime."""
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CIRCUIT),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Ceiling loop",
            CONF_ZONE_IDS: [LIVING_ZONE_ID],
            CONF_VALVE_IDS: [VALVE_ID],
            CONF_PUMP_ID: PUMP_ID,
        },
    )
    await hass.async_block_till_done()
    result = await _confirm_warning_review(hass, result)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_CIRCUIT
    )

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["type"] == FlowResultType.FORM

    updated_data = deepcopy(dict(entry.data))
    updated_data["topology"]["zones"] = [updated_data["topology"]["zones"][0]]
    updated_data["topology"]["routes"] = [updated_data["topology"]["routes"][0]]
    hass.config_entries.async_update_entry(entry, data=updated_data)
    await hass.async_block_till_done()
    original_data = dict(subentry.data)
    valid_runtime = entry.runtime_data

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Invalid update",
            CONF_ZONE_IDS: [OFFICE_ZONE_ID],
            CONF_VALVE_IDS: [VALVE_ID],
            CONF_PUMP_ID: PUMP_ID,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "invalid_circuit"}
    assert dict(subentry.data) == original_data
    assert entry.runtime_data is valid_runtime


async def test_reload_reconstructs_persisted_circuit_subentry(hass) -> None:
    """A later plant reload should reconstruct the circuit and its route UUIDs."""
    entry = _plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_CIRCUIT),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Ceiling loop",
            CONF_ZONE_IDS: [LIVING_ZONE_ID, OFFICE_ZONE_ID],
            CONF_VALVE_IDS: [VALVE_ID, SECOND_VALVE_ID],
            CONF_PUMP_ID: PUMP_ID,
        },
    )
    await hass.async_block_till_done()
    result = await _confirm_warning_review(hass, result)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_TYPE_CIRCUIT
    )
    circuit_id = subentry.data["id"]
    route_ids = {route["id"] for route in subentry.data["routes"]}
    runtime_after_add = entry.runtime_data

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data is not runtime_after_add
    assert entry.runtime_data.plant.circuits[circuit_id].valve_ids == (
        VALVE_ID,
        SECOND_VALVE_ID,
    )
    assert {
        route.id for route in entry.runtime_data.plant.routes if route.circuit_id == circuit_id
    } == route_ids
