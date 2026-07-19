"""Deterministic source eligibility and recommendation tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hydronicus_core.configuration import (
    StoredTopologyError,
    plant_configuration_from_entry_data,
)
from hydronicus_core.controller import evaluate, recommend_source
from hydronicus_core.model import (
    Circuit,
    DeliveryRoute,
    PlantConfiguration,
    PlantSnapshot,
    Pump,
    RuntimeState,
    Source,
    SourceKind,
    TemperatureObservation,
    TemperatureSensorMetadata,
    Valve,
    Zone,
)
from hydronicus_core.topology import TopologyValidationError, compile_topology

NOW = datetime(2026, 7, 18, tzinfo=UTC)
STORED_PLANT_ID = "00000000-0000-4000-8000-000000000001"
STORED_SOURCE_ID = "00000000-0000-4000-8000-000000000002"
STORED_BUFFER_ID = "00000000-0000-4000-8000-000000000003"


def _metadata(*entity_ids: str) -> tuple[TemperatureSensorMetadata, ...]:
    return tuple(TemperatureSensorMetadata(entity_id) for entity_id in entity_ids)


def _plant(*sources: Source):
    return compile_topology(
        PlantConfiguration(
            id="source-plant",
            zones=(
                Zone(
                    "zone",
                    "Zone",
                    21.0,
                    _metadata(
                        "sensor.zone",
                    ),
                ),
            ),
            valves=(Valve("valve", "Valve", "switch.valve", 0),),
            pumps=(Pump("pump", "Pump", "switch.pump", 0),),
            circuits=(Circuit("circuit", "Circuit", ("valve",), "pump"),),
            routes=(DeliveryRoute("route", "zone", "circuit"),),
            sources=tuple(sources),
        )
    )


def _snapshot(
    *,
    source_availability: dict[str, bool] | None = None,
    source_temperatures: dict[str, TemperatureObservation] | None = None,
) -> PlantSnapshot:
    return PlantSnapshot(
        temperatures={"sensor.zone": TemperatureObservation(19.0, NOW)},
        source_availability=source_availability or {},
        source_temperatures=source_temperatures or {},
    )


def test_decodes_canonical_source_configuration() -> None:
    """Persisted generic and temperature-qualified fields become typed sources."""
    plant = plant_configuration_from_entry_data(
        {
            "plant_id": STORED_PLANT_ID,
            "topology": {
                "sources": [
                    {
                        "id": STORED_SOURCE_ID,
                        "name": "Boiler",
                        "priority": 20,
                        "source_type": "external",
                        "availability_entity": "binary_sensor.boiler_available",
                    },
                    {
                        "id": STORED_BUFFER_ID,
                        "name": "Buffer",
                        "priority": 10,
                        "source_type": "temperature_qualified_buffer",
                        "temperature_entity": "sensor.buffer_temperature",
                        "minimum_temperature": 40,
                        "maximum_age_seconds": 300,
                        "hysteresis": 1.5,
                    },
                ]
            },
        }
    )

    assert plant.sources[0].kind is SourceKind.EXTERNAL
    assert plant.sources[0].availability_entity_id == "binary_sensor.boiler_available"
    assert plant.sources[1].kind is SourceKind.TEMPERATURE_QUALIFIED_BUFFER
    assert plant.sources[1].minimum_temperature == 40.0
    assert plant.sources[1].maximum_age_seconds == 300.0
    assert plant.sources[1].hysteresis == 1.5


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (Source("bad", "Bad", priority=-1), "priority"),
        (Source("bad", "Bad", priority=1.5), "priority"),
        (
            Source("bad", "Bad", kind=SourceKind.TEMPERATURE_QUALIFIED_BUFFER),
            "temperature entity",
        ),
        (
            Source(
                "bad",
                "Bad",
                kind=SourceKind.TEMPERATURE_QUALIFIED_BUFFER,
                temperature_entity_id="sensor.buffer",
                maximum_age_seconds=0,
            ),
            "maximum temperature age",
        ),
    ],
)
def test_rejects_invalid_source_configuration(source: Source, message: str) -> None:
    with pytest.raises(TopologyValidationError, match=message):
        compile_topology(PlantConfiguration("plant", (), (), (), (), (), (source,)))


def test_rejects_invalid_stored_source_configuration() -> None:
    with pytest.raises(StoredTopologyError, match="source type"):
        plant_configuration_from_entry_data(
            {
                "plant_id": STORED_PLANT_ID,
                "topology": {
                    "sources": [
                        {
                            "id": STORED_SOURCE_ID,
                            "name": "Source",
                            "source_type": "unknown",
                        }
                    ]
                },
            }
        )

    with pytest.raises(StoredTopologyError, match="priority"):
        plant_configuration_from_entry_data(
            {
                "plant_id": STORED_PLANT_ID,
                "topology": {
                    "sources": [{"id": STORED_SOURCE_ID, "name": "Source", "priority": 1.5}]
                },
            }
        )


@pytest.mark.parametrize(
    "source_fields",
    [
        {"source_type": "buffer"},
        {"source_type": "temperature_buffer"},
    ],
)
def test_rejects_short_source_kind_names(source_fields) -> None:
    """Source kinds use the exact names emitted by the current UI writer."""
    with pytest.raises(StoredTopologyError, match="source type"):
        plant_configuration_from_entry_data(
            {
                "plant_id": STORED_PLANT_ID,
                "topology": {
                    "sources": [{"id": STORED_SOURCE_ID, "name": "Source", **source_fields}]
                },
            }
        )


@pytest.mark.parametrize(
    "source_fields",
    [
        {"availability_sensor": "binary_sensor.available"},
        {"availability_entity_id": "binary_sensor.available"},
        {"temperature_sensor": "sensor.temperature"},
        {"temperature_entity_id": "sensor.temperature"},
        {"demand_entity": "switch.demand"},
        {"demand_entity_id": "switch.demand"},
        {"readiness_entity": "binary_sensor.ready"},
    ],
)
def test_rejects_alternate_source_fields(source_fields) -> None:
    """Persisted sources accept only current availability, temperature, and demand names."""
    with pytest.raises(StoredTopologyError, match="Stored source uses unsupported fields"):
        plant_configuration_from_entry_data(
            {
                "plant_id": STORED_PLANT_ID,
                "topology": {
                    "sources": [{"id": STORED_SOURCE_ID, "name": "Source", **source_fields}]
                },
            }
        )


def test_priority_selection_is_deterministic_and_shadow_only() -> None:
    """Lower priority wins and equal priorities use the stable source ID tie-breaker."""
    plant = _plant(
        Source("zeta", "Zeta", priority=10),
        Source("alpha", "Alpha", priority=10),
        Source("fallback", "Fallback", priority=20),
    )

    first = evaluate(plant, _snapshot(), RuntimeState(), NOW)
    second = evaluate(plant, _snapshot(), RuntimeState(), NOW)

    assert first.control_plan.source_recommendation is not None
    assert first.control_plan.source_recommendation.source_id == "alpha"
    assert first.control_plan.source_recommendation.eligible_source_ids == (
        "alpha",
        "zeta",
        "fallback",
    )
    assert first == second
    assert all(command.actuator_id not in plant.sources for command in first.control_plan.commands)


def test_buffer_stale_or_unavailable_falls_back_to_external_source() -> None:
    """A buffer must pass both availability and freshness checks before selection."""
    plant = _plant(
        Source(
            "buffer",
            "Buffer",
            priority=1,
            kind=SourceKind.TEMPERATURE_QUALIFIED_BUFFER,
            availability_entity_id="binary_sensor.buffer_available",
            temperature_entity_id="sensor.buffer_temperature",
            minimum_temperature=40,
            maximum_age_seconds=30,
        ),
        Source("boiler", "Boiler", priority=2),
    )
    fresh = TemperatureObservation(45.0, NOW)
    selected = evaluate(
        plant,
        _snapshot(
            source_availability={"buffer": True},
            source_temperatures={"buffer": fresh},
        ),
        RuntimeState(),
        NOW,
    )
    stale = evaluate(
        plant,
        _snapshot(
            source_availability={"buffer": True},
            source_temperatures={
                "buffer": TemperatureObservation(45.0, NOW - timedelta(seconds=31))
            },
        ),
        selected.next_runtime,
        NOW,
    )
    unavailable = evaluate(
        plant,
        _snapshot(
            source_availability={"buffer": False},
            source_temperatures={"buffer": fresh},
        ),
        selected.next_runtime,
        NOW,
    )

    assert selected.next_runtime.selected_source_id == "buffer"
    assert stale.next_runtime.selected_source_id == "boiler"
    assert "stale" in stale.diagnostics.source_recommendation.explanation
    assert unavailable.next_runtime.selected_source_id == "boiler"
    assert "availability" in unavailable.diagnostics.source_recommendation.explanation


def test_buffer_hysteresis_prevents_recommendation_chatter() -> None:
    """A selected buffer holds through the lower threshold before re-entry."""
    plant = _plant(
        Source(
            "buffer",
            "Buffer",
            priority=1,
            kind=SourceKind.TEMPERATURE_QUALIFIED_BUFFER,
            temperature_entity_id="sensor.buffer_temperature",
            minimum_temperature=40,
            hysteresis=0.5,
        ),
        Source("boiler", "Boiler", priority=2),
    )
    runtime = RuntimeState()
    readings = (
        (40.0, "buffer"),
        (39.6, "buffer"),
        (39.4, "boiler"),
        (39.9, "boiler"),
        (40.0, "buffer"),
    )
    for temperature, expected in readings:
        result = evaluate(
            plant,
            _snapshot(source_temperatures={"buffer": TemperatureObservation(temperature, NOW)}),
            runtime,
            NOW,
        )
        assert result.next_runtime.selected_source_id == expected
        runtime = result.next_runtime


def test_recommendation_without_demand_is_explicit_and_no_sources_is_optional() -> None:
    source_plant = _plant(Source("boiler", "Boiler"))
    no_demand = evaluate(
        source_plant,
        PlantSnapshot(
            temperatures={"sensor.zone": TemperatureObservation(22.0, NOW)},
        ),
        RuntimeState(),
        NOW,
    )
    empty_plant = compile_topology(PlantConfiguration("empty", (), (), (), (), ()))

    assert no_demand.diagnostics.source_recommendation is not None
    assert no_demand.diagnostics.source_recommendation.source_id is None
    assert "No active heating demand" in no_demand.diagnostics.source_recommendation.explanation
    assert recommend_source(empty_plant, PlantSnapshot({}), RuntimeState(), NOW) is None
