"""Benchmark the large synthetic Plant through compile, evaluation, and shadow runtime paths."""

from __future__ import annotations

import json
import time
import tracemalloc
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydronicus.const import (
    CONFIG_ENTRY_MINOR_VERSION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from custom_components.hydronicus.core.configuration import plant_configuration_from_entry_data
from custom_components.hydronicus.core.controller import evaluate
from custom_components.hydronicus.core.model import RuntimeState
from custom_components.hydronicus.core.topology import compile_topology

from .plant_factory import (
    build_large_synthetic_entry,
    build_synthetic_snapshot,
    load_benchmark_profile,
    synthetic_entity_ids,
    synthetic_state,
)


def _compile_and_evaluate(entry_data: dict[str, Any]) -> tuple[dict[str, float], Any]:
    """Measure the pure deterministic compile/evaluation seam with bounded allocations."""
    configuration = plant_configuration_from_entry_data(entry_data)
    now = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
    snapshot = build_synthetic_snapshot(entry_data, now)
    tracemalloc.start()
    compile_started = time.perf_counter_ns()
    plant = compile_topology(configuration)
    compile_ms = (time.perf_counter_ns() - compile_started) / 1_000_000
    evaluation_started = time.perf_counter_ns()
    result = evaluate(plant, snapshot, RuntimeState(), now)
    evaluation_ms = (time.perf_counter_ns() - evaluation_started) / 1_000_000
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "compile_ms": round(compile_ms, 3),
        "evaluation_ms": round(evaluation_ms, 3),
        "peak_memory_mib": round(peak_bytes / 1024 / 1024, 3),
    }, result


def _topology_counts(entry_data: dict[str, Any]) -> dict[str, int]:
    """Count the generated topology objects and shared relationship references."""
    topology = entry_data["topology"]
    valve_references = [
        valve_id for circuit in topology["circuits"] for valve_id in circuit["valve_ids"]
    ]
    pump_references = [circuit["pump_id"] for circuit in topology["circuits"]]
    temperature_sensor_count = sum(
        len(zone["temperature_sensor_metadata"]) for zone in topology["zones"]
    )
    humidity_sensor_count = sum(len(zone["humidity_sensor_metadata"]) for zone in topology["zones"])
    interlock_reference_count = sum(
        int(circuit["supply_temperature_sensor"] is not None)
        + int(circuit["surface_temperature_sensor"] is not None)
        for circuit in topology["circuits"]
        if circuit["cooling_enabled"]
    )
    return {
        "zones": len(topology["zones"]),
        "circuits": len(topology["circuits"]),
        "valves": len(topology["valves"]),
        "pumps": len(topology["pumps"]),
        "sources": len(topology["sources"]),
        "routes": len(topology["routes"]),
        "temperature_sensors": temperature_sensor_count,
        "humidity_sensors": humidity_sensor_count,
        "interlock_references": interlock_reference_count,
        "shared_valve_references": sum(count > 1 for count in _counts(valve_references).values()),
        "shared_pump_references": sum(count > 1 for count in _counts(pump_references).values()),
    }


def _counts(values: list[str]) -> dict[str, int]:
    """Count repeated relationship references without relying on collection order."""
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


async def test_large_synthetic_plant_benchmark_is_bounded_and_shadow_only(hass) -> None:
    """Record topology, performance, reconciliation, and publication evidence."""
    profile = load_benchmark_profile()
    entry_data = build_large_synthetic_entry()
    counts = _topology_counts(entry_data)
    metrics, evaluation = _compile_and_evaluate(entry_data)
    metrics["interlock_count"] = len(evaluation.diagnostics.interlocks)

    assert counts["zones"] >= 48
    assert counts["circuits"] >= 24
    assert counts["valves"] >= 12
    assert counts["pumps"] >= 6
    assert counts["sources"] >= 3
    assert counts["temperature_sensors"] >= 96
    assert counts["humidity_sensors"] >= 48
    assert counts["interlock_references"] >= 48
    assert counts["shared_valve_references"] == counts["valves"]
    assert counts["shared_pump_references"] == counts["pumps"]
    assert len(evaluation.diagnostics.interlocks) >= counts["zones"]

    entry = MockConfigEntry(
        domain=DOMAIN,
        title=entry_data["name"],
        data=entry_data,
        version=CONFIG_ENTRY_VERSION,
        minor_version=CONFIG_ENTRY_MINOR_VERSION,
    )
    for entity_id in synthetic_entity_ids(entry_data):
        hass.states.async_set(entity_id, synthetic_state(entity_id))
    entry.add_to_hass(hass)
    service_call = AsyncMock()
    entity_update_count = 0

    def count_entity_update() -> None:
        nonlocal entity_update_count
        entity_update_count += 1

    with patch.object(type(hass.services), "async_call", new=service_call):
        assert await hass.config_entries.async_setup(entry.entry_id)
        runtime = entry.runtime_data
        remove_listener = runtime.async_add_listener(count_entity_update)
        hass.states.async_set("sensor.synthetic_zone_temperature_001_1", "24.0")
        await hass.async_block_till_done()
        await runtime.async_refresh(hass)
        await runtime._async_periodic_reconciliation(hass)
        await runtime._async_periodic_reconciliation(hass)
        await hass.async_block_till_done()

        metrics.update(
            {
                "reconciliation_count": runtime.reconciliation_count,
                "reconciliation_changed_count": runtime.reconciliation_changed_count,
                "reconciliation_unchanged_count": runtime.reconciliation_unchanged_count,
                "refresh_count": runtime.refresh_count,
                "evaluation_count": runtime.evaluation_count,
                "entity_update_count": entity_update_count,
                "service_call_count": service_call.await_count,
            }
        )
        remove_listener()
        assert await hass.config_entries.async_unload(entry.entry_id)

    thresholds = profile["thresholds"]
    assert metrics["compile_ms"] <= thresholds["compile_ms_max"]
    assert metrics["evaluation_ms"] <= thresholds["evaluation_ms_max"]
    assert metrics["peak_memory_mib"] <= thresholds["peak_memory_mib_max"]
    assert metrics["reconciliation_count"] >= thresholds["minimum_reconciliation_count"]
    assert metrics["entity_update_count"] >= thresholds["minimum_entity_update_count"]
    assert metrics["service_call_count"] <= thresholds["maximum_service_call_count"]

    print(
        "LARGE_PLANT_BENCHMARK "
        + json.dumps({"topology": counts, "metrics": metrics}, sort_keys=True)
    )
