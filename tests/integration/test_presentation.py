"""Tests for the versioned Hydronicus Plant presentation contract."""

from __future__ import annotations

import json

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronicus.const import CONF_DRY_RUN, CONF_NAME, CONF_PLANT_ID, DOMAIN
from custom_components.hydronicus.presentation import PRESENTATION_SCHEMA_VERSION

PLANT_ID = "00000000-0000-4000-8000-000000000001"
ZONE_A = "00000000-0000-4000-8000-000000000002"
ZONE_B = "00000000-0000-4000-8000-000000000003"
VALVE_ID = "00000000-0000-4000-8000-000000000004"
PUMP_ID = "00000000-0000-4000-8000-000000000005"
CIRCUIT_A = "00000000-0000-4000-8000-000000000006"
CIRCUIT_B = "00000000-0000-4000-8000-000000000007"
ROUTE_A = "00000000-0000-4000-8000-000000000008"
ROUTE_B = "00000000-0000-4000-8000-000000000009"


def _entry() -> MockConfigEntry:
    """Create a generic two-Zone shared-actuator Plant."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Synthetic Plant",
        data={
            CONF_NAME: "Synthetic Plant",
            CONF_PLANT_ID: PLANT_ID,
            CONF_DRY_RUN: True,
            "private_url": "https://not-exported.invalid/secret",
            "topology": {
                "zones": [
                    {
                        "id": ZONE_A,
                        "name": "Zone A",
                        "target_temperature": 21.0,
                        "temperature_sensor_metadata": [{"entity_id": "sensor.synthetic_zone_a"}],
                        "preset_targets": {"comfort": 22.0, "eco": 19.0},
                    },
                    {
                        "id": ZONE_B,
                        "name": "Zone B",
                        "target_temperature": 21.0,
                        "temperature_sensor_metadata": [{"entity_id": "sensor.synthetic_zone_b"}],
                    },
                ],
                "valves": [
                    {
                        "id": VALVE_ID,
                        "name": "Shared synthetic valve",
                        "entity_id": "switch.synthetic_valve",
                    }
                ],
                "pumps": [
                    {
                        "id": PUMP_ID,
                        "name": "Shared synthetic pump",
                        "entity_id": "switch.synthetic_pump",
                    }
                ],
                "circuits": [
                    {
                        "id": CIRCUIT_A,
                        "name": "Circuit A",
                        "valve_ids": [VALVE_ID],
                        "pump_id": PUMP_ID,
                    },
                    {
                        "id": CIRCUIT_B,
                        "name": "Circuit B",
                        "valve_ids": [VALVE_ID],
                        "pump_id": PUMP_ID,
                    },
                ],
                "routes": [
                    {"id": ROUTE_A, "zone_id": ZONE_A, "circuit_id": CIRCUIT_A},
                    {"id": ROUTE_B, "zone_id": ZONE_B, "circuit_id": CIRCUIT_B},
                ],
            },
        },
    )


async def test_presentation_is_deterministic_redacted_and_topology_driven(hass) -> None:
    """Snapshots expose structured shared topology without physical bindings."""
    hass.states.async_set("sensor.synthetic_zone_a", "18")
    hass.states.async_set("sensor.synthetic_zone_b", "18")
    hass.states.async_set("switch.synthetic_valve", "off")
    hass.states.async_set("switch.synthetic_pump", "off")
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime = entry.runtime_data
    first = runtime.presentation_snapshot(hass)
    second = runtime.presentation_snapshot(hass)

    assert first == second
    assert first["schema_version"] == PRESENTATION_SCHEMA_VERSION
    assert first["plant"]["requested_mode"] == "auto"
    assert first["zones"][0]["preset_modes"] == ["comfort", "eco"]
    assert first["topology"]["coupling_groups"]
    assert first["zones"][0]["coupling_group_ids"]
    shared_consumers = next(
        actuator["active_consumers"]
        for actuator in first["actuators"]
        if actuator["id"] == VALVE_ID
    )
    assert {consumer["id"] for consumer in shared_consumers} == {CIRCUIT_A, CIRCUIT_B}
    assert [node["kind"] for node in first["delivery_paths"][0]["nodes"]] == [
        "zone",
        "circuit",
        "valve",
        "pump",
    ]

    serialized = json.dumps(first, sort_keys=True)
    for forbidden in (
        "sensor.synthetic_zone_a",
        "sensor.synthetic_zone_b",
        "switch.synthetic_valve",
        "switch.synthetic_pump",
        "not-exported.invalid",
    ):
        assert forbidden not in serialized


async def test_presentation_updates_include_execution_boundary_and_alert_priority(hass) -> None:
    """Heating demand and unavailable bindings are visible as explicit states."""
    hass.states.async_set("sensor.synthetic_zone_a", "18")
    hass.states.async_set("sensor.synthetic_zone_b", "unavailable")
    hass.states.async_set("switch.synthetic_valve", "off")
    hass.states.async_set("switch.synthetic_pump", "off")
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    snapshot = entry.runtime_data.presentation_snapshot(hass)

    assert snapshot["plant"]["execution_boundary"]["mode"] == "dry_run"
    assert snapshot["plant"]["execution_boundary"]["dry_run"] is True
    assert any(alert["code"] == "zone_sensor_blocked" for alert in snapshot["alerts"])
    assert snapshot["alerts"] == sorted(
        snapshot["alerts"], key=lambda alert: (alert["priority"], alert["code"], alert["scope"])
    )
    assert any(zone["phase"] == "blocked" for zone in snapshot["zones"])
