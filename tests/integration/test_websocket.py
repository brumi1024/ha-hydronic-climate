"""Lifecycle and permission tests for Hydronicus Plant WebSocket commands."""

from __future__ import annotations

from dataclasses import dataclass

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronicus.const import CONF_DRY_RUN, CONF_NAME, CONF_PLANT_ID, DOMAIN
from custom_components.hydronicus.websocket import (
    WS_LIST_PLANTS,
    WS_SUBSCRIBE_PLANT,
    ws_list_plants,
    ws_subscribe_plant,
)

PLANT_ID = "00000000-0000-4000-8000-000000000101"
ZONE_A = "00000000-0000-4000-8000-000000000102"
ZONE_B = "00000000-0000-4000-8000-000000000103"
VALVE_ID = "00000000-0000-4000-8000-000000000104"
PUMP_ID = "00000000-0000-4000-8000-000000000105"
CIRCUIT_ID = "00000000-0000-4000-8000-000000000106"
ROUTE_A = "00000000-0000-4000-8000-000000000107"
ROUTE_B = "00000000-0000-4000-8000-000000000108"


@dataclass
class _User:
    permissions: object | None = None


class _Connection:
    """Small ActiveConnection seam for command and cleanup tests."""

    def __init__(self, user: object) -> None:
        self.user = user
        self.results: list[tuple[int, object]] = []
        self.events: list[tuple[int, object]] = []
        self.errors: list[tuple[int, str, str]] = []
        self.subscriptions: dict[int, object] = {}

    def send_result(self, msg_id: int, result: object) -> None:
        self.results.append((msg_id, result))

    def send_event(self, msg_id: int, event: object) -> None:
        self.events.append((msg_id, event))

    def send_error(self, msg_id: int, code: str, message: str) -> None:
        self.errors.append((msg_id, code, message))


def _entry() -> MockConfigEntry:
    """Create one two-zone Plant with synthetic bindings."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="WebSocket Plant",
        data={
            CONF_NAME: "WebSocket Plant",
            CONF_PLANT_ID: PLANT_ID,
            CONF_DRY_RUN: True,
            "topology": {
                "zones": [
                    {
                        "id": ZONE_A,
                        "name": "Zone A",
                        "target_temperature": 21.0,
                        "temperature_sensor_metadata": [{"entity_id": "sensor.ws_zone_a"}],
                    },
                    {
                        "id": ZONE_B,
                        "name": "Zone B",
                        "target_temperature": 21.0,
                        "temperature_sensor_metadata": [{"entity_id": "sensor.ws_zone_b"}],
                    },
                ],
                "valves": [{"id": VALVE_ID, "name": "Valve", "entity_id": "switch.ws_valve"}],
                "pumps": [{"id": PUMP_ID, "name": "Pump", "entity_id": "switch.ws_pump"}],
                "circuits": [
                    {
                        "id": CIRCUIT_ID,
                        "name": "Circuit",
                        "valve_ids": [VALVE_ID],
                        "pump_id": PUMP_ID,
                    }
                ],
                "routes": [
                    {"id": ROUTE_A, "zone_id": ZONE_A, "circuit_id": CIRCUIT_ID},
                    {"id": ROUTE_B, "zone_id": ZONE_B, "circuit_id": CIRCUIT_ID},
                ],
            },
        },
    )


async def test_list_and_subscribe_are_permission_filtered_and_reconnect_on_reload(hass) -> None:
    """Plant discovery and snapshots follow visible Hydronicus-owned entities."""
    hass.states.async_set("sensor.ws_zone_a", "18")
    hass.states.async_set("sensor.ws_zone_b", "19")
    hass.states.async_set("switch.ws_valve", "off")
    hass.states.async_set("switch.ws_pump", "off")
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    all_connection = _Connection(_User())
    await ws_list_plants.__wrapped__(  # type: ignore[attr-defined]
        hass, all_connection, {"id": 1, "type": WS_LIST_PLANTS}
    )
    assert all_connection.results[0][1]["plants"][0]["id"] == PLANT_ID

    class _Permissions:
        def check_entity(self, entity_id: str, _permission: str) -> bool:
            return entity_id.endswith("zone_a") or entity_id.endswith("safe_shutdown")

    filtered_connection = _Connection(_User(_Permissions()))
    await ws_subscribe_plant.__wrapped__(  # type: ignore[attr-defined]
        hass,
        filtered_connection,
        {"id": 2, "type": WS_SUBSCRIBE_PLANT, "plant_id": PLANT_ID},
    )
    initial = filtered_connection.results[0][1]["snapshot"]
    assert [zone["id"] for zone in initial["zones"]] == [ZONE_A]
    assert initial["controls"]["requested_mode"] is None
    assert filtered_connection.subscriptions

    old_runtime = entry.runtime_data
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert entry.runtime_data is not old_runtime
    assert any("snapshot" in event for _msg_id, event in filtered_connection.events)

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert filtered_connection.events[-1][1]["status"] == "unavailable"
    filtered_connection.subscriptions[2]()
    assert not filtered_connection.subscriptions


async def test_subscribe_missing_plant_returns_defined_error(hass) -> None:
    """An unloaded or unknown Plant never leaks topology or raises a 500."""
    connection = _Connection(_User())
    await ws_subscribe_plant.__wrapped__(  # type: ignore[attr-defined]
        hass,
        connection,
        {"id": 1, "type": WS_SUBSCRIBE_PLANT, "plant_id": "missing"},
    )
    assert connection.errors[0][1] == "plant_not_found"
