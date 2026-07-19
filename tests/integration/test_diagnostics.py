"""Integration coverage for redacted diagnostics and bounded reconciliation."""

from __future__ import annotations

import json

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronicus.const import (
    CONF_DIAGNOSTICS_INCLUDE_ACTUATOR_DETAILS,
    CONF_DRY_RUN,
    CONF_NAME,
    CONF_PLANT_ID,
    DOMAIN,
)
from custom_components.hydronicus.diagnostics import async_get_config_entry_diagnostics

PLANT_ID = "00000000-0000-4000-8000-000000000001"
ZONE_ID = "00000000-0000-4000-8000-000000000002"
VALVE_ID = "00000000-0000-4000-8000-000000000003"
PUMP_ID = "00000000-0000-4000-8000-000000000004"
CIRCUIT_ONE_ID = "00000000-0000-4000-8000-000000000005"
CIRCUIT_TWO_ID = "00000000-0000-4000-8000-000000000006"
ROUTE_ONE_ID = "00000000-0000-4000-8000-000000000007"
ROUTE_TWO_ID = "00000000-0000-4000-8000-000000000008"


def _entry(*, detailed_actuators: bool = False) -> MockConfigEntry:
    """Build a shared synthetic topology with deliberately sensitive labels."""
    data = {
        CONF_NAME: "Private Solymar Plant",
        CONF_PLANT_ID: PLANT_ID,
        CONF_DRY_RUN: True,
        "private_url": "https://private.example/hydronic?token=do-not-export",
        "token": "synthetic-secret-token",
        "topology": {
            "zones": [
                {
                    "id": ZONE_ID,
                    "name": "Bedroom near the nursery",
                    "target_temperature": 21.0,
                    "temperature_sensor": "sensor.private_bedroom_temperature",
                }
            ],
            "valves": [
                {
                    "id": VALVE_ID,
                    "name": "Private manifold valve",
                    "entity_id": "switch.private_manifold_valve",
                }
            ],
            "pumps": [
                {
                    "id": PUMP_ID,
                    "name": "Private plant pump",
                    "entity_id": "switch.private_plant_pump",
                }
            ],
            "circuits": [
                {
                    "id": CIRCUIT_ONE_ID,
                    "name": "Private floor circuit",
                    "valve_ids": [VALVE_ID],
                    "pump_id": PUMP_ID,
                },
                {
                    "id": CIRCUIT_TWO_ID,
                    "name": "Private ceiling circuit",
                    "valve_ids": [VALVE_ID],
                    "pump_id": PUMP_ID,
                },
            ],
            "routes": [
                {"id": ROUTE_ONE_ID, "zone_id": ZONE_ID, "circuit_id": CIRCUIT_ONE_ID},
                {"id": ROUTE_TWO_ID, "zone_id": ZONE_ID, "circuit_id": CIRCUIT_TWO_ID},
            ],
        },
    }
    if detailed_actuators:
        data[CONF_DIAGNOSTICS_INCLUDE_ACTUATOR_DETAILS] = True
    return MockConfigEntry(domain=DOMAIN, title="Private Solymar Plant", data=data)


async def test_downloadable_diagnostics_are_deterministic_and_redacted(hass) -> None:
    """A synthetic runtime produces useful shape and no household bindings."""
    hass.states.async_set("sensor.private_bedroom_temperature", "18.0")
    hass.states.async_set("switch.private_manifold_valve", "off")
    hass.states.async_set("switch.private_plant_pump", "off")
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    first = await async_get_config_entry_diagnostics(hass, entry)
    second = await async_get_config_entry_diagnostics(hass, entry)

    assert first == second
    assert first["configuration_shape"]["counts"] == {
        "zones": 1,
        "circuits": 2,
        "routes": 2,
        "valves": 1,
        "pumps": 1,
        "sources": 0,
    }
    warning_codes = {warning["code"] for warning in first["compiled_topology"]["warnings"]}
    assert "shared_valve_limits_independent_control" in warning_codes
    assert first["controller_decisions"]["evaluated"] is True
    assert first["actuator_state"]["detail_enabled"] is False
    assert first["actuator_state"]["details"] == []

    serialized = json.dumps(first, sort_keys=True)
    for forbidden in (
        "Private Solymar Plant",
        "Bedroom near the nursery",
        "sensor.private_bedroom_temperature",
        "switch.private_manifold_valve",
        "switch.private_plant_pump",
        "private.example",
        "synthetic-secret-token",
        PLANT_ID,
        ZONE_ID,
    ):
        assert forbidden not in serialized
    assert "<redacted name>" in serialized


async def test_reconciliation_diagnostics_and_telemetry_are_bounded(hass) -> None:
    """Unchanged periodic reads are counted but do not reevaluate or churn states."""
    hass.states.async_set("sensor.private_bedroom_temperature", "18.0")
    hass.states.async_set("switch.private_manifold_valve", "off")
    hass.states.async_set("switch.private_plant_pump", "off")
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime = entry.runtime_data
    initial_evaluations = runtime.evaluation_count

    await runtime._async_periodic_reconciliation(hass)
    await runtime._async_periodic_reconciliation(hass)

    assert runtime.evaluation_count == initial_evaluations
    assert runtime.reconciliation_count == 2
    assert runtime.reconciliation_changed_count == 0
    assert runtime.reconciliation_unchanged_count == 2
    assert runtime.last_reconciliation_status == "unchanged"
    await hass.async_block_till_done()
    assert hass.states.get("sensor.private_solymar_plant_reconciliation_status").state == (
        "unchanged"
    )
    assert hass.states.get("sensor.private_solymar_plant_controller_status").state == "heating"


async def test_repeated_identical_evaluations_do_not_publish_entity_updates(hass) -> None:
    """A direct refresh with an unchanged snapshot is invisible to Recorder."""
    hass.states.async_set("sensor.private_bedroom_temperature", "18.0")
    hass.states.async_set("switch.private_manifold_valve", "off")
    hass.states.async_set("switch.private_plant_pump", "off")
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime = entry.runtime_data
    publications: list[None] = []
    remove_listener = runtime.async_add_listener(lambda: publications.append(None))
    await runtime.async_refresh(hass)
    publications.clear()
    await runtime.async_refresh(hass)
    await runtime.async_refresh(hass)

    assert runtime.evaluation_count == 4
    assert publications == []
    remove_listener()


async def test_detailed_actuator_diagnostics_are_explicitly_opt_in(hass) -> None:
    """Opt-in details remain opaque and do not reintroduce entity IDs."""
    hass.states.async_set("sensor.private_bedroom_temperature", "18.0")
    hass.states.async_set("switch.private_manifold_valve", "off")
    hass.states.async_set("switch.private_plant_pump", "off")
    entry = _entry(detailed_actuators=True)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    details = diagnostics["actuator_state"]["details"]

    assert diagnostics["actuator_state"]["detail_enabled"] is True
    assert details
    assert all(item["reference"].startswith("actuator-") for item in details)
    serialized = json.dumps(details, sort_keys=True)
    assert "switch.private_manifold_valve" not in serialized
    assert "switch.private_plant_pump" not in serialized


async def test_verbose_actuator_entities_are_opt_in(hass) -> None:
    """The default entity set omits high-cardinality feedback explanations."""
    hass.states.async_set("sensor.private_bedroom_temperature", "18.0")
    hass.states.async_set("switch.private_manifold_valve", "off")
    hass.states.async_set("switch.private_plant_pump", "off")
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert (
        hass.states.get("sensor.private_solymar_plant_private_manifold_valve_feedback_reason")
        is None
    )
