"""Tests for versioned, UUID-preserving config-entry migrations."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronicus import async_migrate_entry
from custom_components.hydronicus.config_flow import HydronicClimateConfigFlow
from custom_components.hydronicus.const import (
    CONFIG_ENTRY_MINOR_VERSION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from custom_components.hydronicus.migration import (
    CURRENT_CONFIG_ENTRY_VERSION,
    MIGRATION_DISPATCH,
    ConfigEntryMigrationError,
    migrate_entry_data,
)

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "migrations" / "v1-current-topology.json"
MATRIX_PATH = Path(__file__).parents[1] / "fixtures" / "migrations" / "public-beta-matrix.json"


def _public_beta_upgrade_cases() -> list[tuple[str, str, int, int]]:
    with MATRIX_PATH.open(encoding="utf-8") as matrix_file:
        matrix = json.load(matrix_file)
    return [
        (
            predecessor["fixture"],
            predecessor["release"],
            version[0],
            version[1],
        )
        for predecessor in matrix["supported_predecessors"]
        for version in predecessor["config_entry_versions"]
    ]


def _load_fixture() -> dict[str, Any]:
    with FIXTURE_PATH.open(encoding="utf-8") as fixture_file:
        fixture = json.load(fixture_file)
    assert fixture["format"] == "hydronicus-config-entry"
    assert fixture["format_version"] == 1
    return fixture


def _fixture_entry() -> dict[str, Any]:
    return _load_fixture()["entry"]


def test_migration_dispatch_is_versioned_and_deterministic() -> None:
    """The registered migration edge can be replayed without changing topology data."""
    fixture_entry = _fixture_entry()
    source_data = deepcopy(fixture_entry["data"])

    first_by_source = {
        minor_version: migrate_entry_data(
            source_data,
            version=fixture_entry["version"],
            minor_version=minor_version,
        )
        for minor_version in (0, 1)
    }
    second_by_source = {
        minor_version: migrate_entry_data(
            source_data,
            version=fixture_entry["version"],
            minor_version=minor_version,
        )
        for minor_version in (0, 1)
    }

    assert CURRENT_CONFIG_ENTRY_VERSION == (CONFIG_ENTRY_VERSION, CONFIG_ENTRY_MINOR_VERSION)
    assert (HydronicClimateConfigFlow.VERSION, HydronicClimateConfigFlow.MINOR_VERSION) == (
        CONFIG_ENTRY_VERSION,
        CONFIG_ENTRY_MINOR_VERSION,
    )
    assert {
        (1, 0),
        (1, 1),
    }.issubset(MIGRATION_DISPATCH)
    assert MIGRATION_DISPATCH[(1, 0)].target == CURRENT_CONFIG_ENTRY_VERSION
    assert MIGRATION_DISPATCH[(1, 1)].target == CURRENT_CONFIG_ENTRY_VERSION
    assert first_by_source == second_by_source
    assert first_by_source[0] == first_by_source[1] == source_data


def test_invalid_migration_does_not_mutate_source_data() -> None:
    """Validation failure must leave the caller-owned nested payload untouched."""
    invalid_data = deepcopy(_fixture_entry()["data"])
    invalid_data["topology"]["routes"][0]["zone_id"] = "00000000-0000-4000-8000-000000000099"
    original_data = deepcopy(invalid_data)

    with pytest.raises(ConfigEntryMigrationError):
        migrate_entry_data(invalid_data, version=1, minor_version=0)

    assert invalid_data == original_data


async def test_historical_topology_migrates_through_setup_and_reload(hass) -> None:
    """Setup and reload reconstruct the same UUID-linked synthetic plant."""
    fixture_entry = _fixture_entry()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=fixture_entry["title"],
        data=fixture_entry["data"],
        version=fixture_entry["version"],
        minor_version=fixture_entry["minor_version"],
    )
    hass.states.async_set("sensor.synthetic_zone_temperature", "19.0")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert (entry.version, entry.minor_version) == CURRENT_CONFIG_ENTRY_VERSION

    runtime = entry.runtime_data
    assert runtime.plant.id == "00000000-0000-4000-8000-000000000001"
    assert set(runtime.plant.zones) == {"00000000-0000-4000-8000-000000000002"}
    assert set(runtime.plant.circuits) == {"00000000-0000-4000-8000-000000000005"}
    assert set(runtime.plant.valves) == {"00000000-0000-4000-8000-000000000003"}
    assert set(runtime.plant.pumps) == {"00000000-0000-4000-8000-000000000004"}
    assert [route.id for route in runtime.plant.routes] == ["00000000-0000-4000-8000-000000000006"]
    route = runtime.plant.routes[0]
    assert route.zone_id == "00000000-0000-4000-8000-000000000002"
    assert route.circuit_id == "00000000-0000-4000-8000-000000000005"

    assert await hass.config_entries.async_reload(entry.entry_id)
    reloaded_runtime = entry.runtime_data
    assert reloaded_runtime.plant.id == runtime.plant.id
    assert set(reloaded_runtime.plant.zones) == set(runtime.plant.zones)
    assert set(reloaded_runtime.plant.circuits) == set(runtime.plant.circuits)
    assert set(reloaded_runtime.plant.valves) == set(runtime.plant.valves)
    assert set(reloaded_runtime.plant.pumps) == set(runtime.plant.pumps)
    assert reloaded_runtime.plant.routes == runtime.plant.routes


async def test_invalid_historical_topology_does_not_update_entry(hass) -> None:
    """The Home Assistant migration hook updates only after validation succeeds."""
    fixture_entry = _fixture_entry()
    invalid_data = deepcopy(fixture_entry["data"])
    invalid_data["topology"]["routes"][0]["zone_id"] = "00000000-0000-4000-8000-000000000099"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=fixture_entry["title"],
        data=invalid_data,
        version=fixture_entry["version"],
        minor_version=fixture_entry["minor_version"],
    )
    original_data = deepcopy(dict(entry.data))
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is False
    assert dict(entry.data) == original_data
    assert (entry.version, entry.minor_version) == (1, 0)


@pytest.mark.parametrize(
    ("fixture_name", "release", "version", "minor_version"),
    _public_beta_upgrade_cases(),
    ids=lambda value: str(value),
)
async def test_every_supported_public_beta_predecessor_preserves_topology_and_entity_ids(
    hass, fixture_name: str, release: str, version: int, minor_version: int
) -> None:
    """Every distributed predecessor reloads with the same UUID graph and unique IDs."""
    fixture_path = Path(__file__).parents[1] / "fixtures" / "migrations" / fixture_name
    with fixture_path.open(encoding="utf-8") as fixture_file:
        fixture = json.load(fixture_file)
    assert fixture["release"] == release
    fixture_entry = fixture["entry"]
    original_topology = deepcopy(fixture_entry["data"]["topology"])
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=fixture_entry["title"],
        data=fixture_entry["data"],
        version=version,
        minor_version=minor_version,
    )
    hass.states.async_set("sensor.synthetic_zone_temperature", "19.0")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert (entry.version, entry.minor_version) == CURRENT_CONFIG_ENTRY_VERSION
    assert entry.data["topology"] == original_topology

    runtime = entry.runtime_data
    assert runtime.plant.id == fixture_entry["data"]["plant_id"]
    assert runtime.plant.routes[0].zone_id == original_topology["zones"][0]["id"]
    assert runtime.plant.routes[0].circuit_id == original_topology["circuits"][0]["id"]
    circuit_id = original_topology["circuits"][0]["id"]
    circuit = runtime.plant.circuits[circuit_id]
    assert circuit.valve_ids == (original_topology["valves"][0]["id"],)
    assert circuit.pump_id == original_topology["pumps"][0]["id"]

    registry = er.async_get(hass)
    unique_ids_before = {
        entity.unique_id
        for entity in registry.entities.values()
        if entity.config_entry_id == entry.entry_id
    }
    assert unique_ids_before

    assert await hass.config_entries.async_reload(entry.entry_id)
    assert entry.data["topology"] == original_topology
    unique_ids_after = {
        entity.unique_id
        for entity in registry.entities.values()
        if entity.config_entry_id == entry.entry_id
    }
    assert unique_ids_after == unique_ids_before


def test_historical_fixture_uses_only_synthetic_entity_ids() -> None:
    """Migration fixtures must not couple tests to a household entity registry."""
    topology = _fixture_entry()["data"]["topology"]
    entity_ids = [
        topology["zones"][0]["temperature_sensor_metadata"][0]["entity_id"],
        topology["valves"][0]["entity_id"],
        topology["pumps"][0]["entity_id"],
    ]

    assert all(
        entity_id.startswith(("sensor.synthetic_", "switch.synthetic_")) for entity_id in entity_ids
    )
