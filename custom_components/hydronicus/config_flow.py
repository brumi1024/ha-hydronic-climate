"""Config flow for Hydronicus."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    ACTUATOR_KIND_VALVE,
    CONF_ACTUATOR_KIND,
    CONF_AWAY_TARGET,
    CONF_CALIBRATION_OFFSET,
    CONF_CIRCUIT_IDS,
    CONF_CIRCUITS,
    CONF_COMFORT_TARGET,
    CONF_CONFIGURE_SENSOR_METADATA,
    CONF_DESIGNATED_REFERENCE,
    CONF_ECO_TARGET,
    CONF_ENTITY_ID,
    CONF_HEATING_START_DELTA,
    CONF_HEATING_STOP_DELTA,
    CONF_MAX_AGE,
    CONF_MINIMUM_ACTIVE_DURATION,
    CONF_MINIMUM_IDLE_DURATION,
    CONF_NAME,
    CONF_OPENING_TIME,
    CONF_OVERRUN,
    CONF_PLANT_ID,
    CONF_PRESET_TARGETS,
    CONF_PUMP_ENTITY,
    CONF_PUMP_ID,
    CONF_PUMP_OVERRUN,
    CONF_PUMPS,
    CONF_REQUIRED,
    CONF_ROUTES,
    CONF_SENSOR_ENTITY,
    CONF_SHADOW_MODE,
    CONF_TARGET_TEMPERATURE,
    CONF_TEMPERATURE_AGGREGATION,
    CONF_TEMPERATURE_SENSOR,
    CONF_TEMPERATURE_SENSOR_METADATA,
    CONF_TEMPERATURE_SENSORS,
    CONF_TOPOLOGY,
    CONF_VALVE_ENTITY,
    CONF_VALVE_IDS,
    CONF_VALVE_OPENING_TIME,
    CONF_VALVES,
    CONF_WEIGHT,
    CONF_ZONE_IDS,
    CONF_ZONES,
    DEFAULT_HEATING_START_DELTA,
    DEFAULT_HEATING_STOP_DELTA,
    DEFAULT_MINIMUM_ACTIVE_DURATION,
    DEFAULT_MINIMUM_IDLE_DURATION,
    DEFAULT_PLANT_NAME,
    DEFAULT_PUMP_OVERRUN,
    DEFAULT_SENSOR_MAX_AGE,
    DEFAULT_SENSOR_WEIGHT,
    DEFAULT_TARGET_TEMPERATURE,
    DEFAULT_TEMPERATURE_AGGREGATION,
    DEFAULT_VALVE_OPENING_TIME,
    DOMAIN,
    SUBENTRY_TYPE_ACTUATOR,
    SUBENTRY_TYPE_CIRCUIT,
    SUBENTRY_TYPE_ZONE,
)
from .core.configuration import (
    StoredTopologyError,
    plant_configuration_from_entry_data,
)
from .core.model import (
    MAX_ZONE_TARGET_TEMPERATURE,
    MIN_ZONE_TARGET_TEMPERATURE,
    CompiledPlant,
    TemperatureAggregation,
)
from .core.topology import TopologyValidationError, compile_topology
from .entry_configuration import effective_plant_configuration


@dataclass(frozen=True, slots=True)
class CircuitOptions:
    """Parent-owned topology choices available to a circuit flow."""

    zones: list[selector.SelectOptionDict]
    valves: list[selector.SelectOptionDict]
    pumps: list[selector.SelectOptionDict]


def _routes_with_retained_fields(
    existing_routes: Sequence[Mapping[str, Any]] | None,
    *,
    relationship_key: str,
    relationship_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """Create relationship records while preserving retained route metadata."""
    routes_by_relationship = {
        str(route[relationship_key]): route for route in existing_routes or []
    }
    return [
        {
            "id": str(routes_by_relationship.get(relationship_id, {}).get("id", uuid4())),
            relationship_key: relationship_id,
            **(
                {"enabled": routes_by_relationship[relationship_id]["enabled"]}
                if relationship_id in routes_by_relationship
                and "enabled" in routes_by_relationship[relationship_id]
                else {}
            ),
        }
        for relationship_id in relationship_ids
    ]


def _circuit_data(
    user_input: Mapping[str, Any],
    circuit_id: str,
    existing_routes: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize one circuit and preserve route UUIDs for retained zones."""
    zone_ids = list(user_input[CONF_ZONE_IDS])
    return {
        "id": circuit_id,
        CONF_NAME: str(user_input[CONF_NAME]).strip(),
        CONF_ZONE_IDS: zone_ids,
        CONF_VALVE_IDS: list(user_input[CONF_VALVE_IDS]),
        CONF_PUMP_ID: user_input[CONF_PUMP_ID],
        CONF_ROUTES: _routes_with_retained_fields(
            existing_routes,
            relationship_key="zone_id",
            relationship_ids=zone_ids,
        ),
    }


def _topology_select(
    options: list[selector.SelectOptionDict],
    *,
    multiple: bool,
) -> selector.SelectSelector:
    """Build a UUID-backed topology object selector."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=options, multiple=multiple)
    )


def _circuit_schema(
    options: CircuitOptions,
    *,
    defaults: Mapping[str, Any] | None = None,
) -> vol.Schema:
    """Build the shared circuit form schema."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, vol.UNDEFINED)): str,
            vol.Required(
                CONF_ZONE_IDS, default=defaults.get(CONF_ZONE_IDS, vol.UNDEFINED)
            ): _topology_select(options.zones, multiple=True),
            vol.Required(
                CONF_VALVE_IDS, default=defaults.get(CONF_VALVE_IDS, vol.UNDEFINED)
            ): _topology_select(options.valves, multiple=True),
            vol.Required(
                CONF_PUMP_ID, default=defaults.get(CONF_PUMP_ID, vol.UNDEFINED)
            ): _topology_select(options.pumps, multiple=False),
        }
    )


def _effective_topology_is_valid(
    entry: config_entries.ConfigEntry,
    *,
    proposed_actuators: Sequence[Mapping[str, Any]] = (),
    proposed_circuits: Sequence[Mapping[str, Any]] = (),
    proposed_zones: Sequence[Mapping[str, Any]] = (),
    excluded_subentry_id: str | None = None,
) -> bool:
    """Compile a complete proposed topology without mutating the config entry."""
    return (
        _effective_topology_compile(
            entry,
            proposed_actuators=proposed_actuators,
            proposed_circuits=proposed_circuits,
            proposed_zones=proposed_zones,
            excluded_subentry_id=excluded_subentry_id,
        )
        is not None
    )


def _effective_topology_compile(
    entry: config_entries.ConfigEntry,
    *,
    proposed_actuators: Sequence[Mapping[str, Any]] = (),
    proposed_circuits: Sequence[Mapping[str, Any]] = (),
    proposed_zones: Sequence[Mapping[str, Any]] = (),
    excluded_subentry_id: str | None = None,
) -> CompiledPlant | None:
    """Compile a complete proposed topology without mutating the config entry."""
    try:
        effective = effective_plant_configuration(
            entry,
            proposed_actuators=proposed_actuators,
            proposed_circuits=proposed_circuits,
            proposed_zones=proposed_zones,
            excluded_subentry_id=excluded_subentry_id,
        )
        return compile_topology(effective.configuration)
    except StoredTopologyError, TopologyValidationError:
        return None


def _warning_text(compiled: Any) -> str:
    """Render structured compiler warnings for a confirmation form."""
    warnings = getattr(compiled, "warnings", ())
    return "\n".join(f"- {warning.message}" for warning in warnings)


def _initial_review_placeholders(
    topology: Mapping[str, Any], compiled: CompiledPlant | None
) -> dict[str, str]:
    """Render complete initial-review context, including validation failures."""
    logic = (
        "\n".join(f"- {line}" for line in compiled.logic_summary)
        if compiled is not None
        else "- Topology could not be compiled."
    )
    return {
        "zone": str(topology[CONF_ZONES][0][CONF_NAME]),
        "circuit": str(topology[CONF_CIRCUITS][0][CONF_NAME]),
        "logic": logic,
        "warnings": _warning_text(compiled) or "- None",
    }


def _warning_review_schema() -> vol.Schema:
    """Require an explicit acknowledgement before persisting warnings."""
    return vol.Schema(
        {
            vol.Required("confirm", default=False): selector.BooleanSelector(),
        }
    )


def _circuit_validation_error(
    entry: config_entries.ConfigEntry,
    data: Mapping[str, Any],
    *,
    excluded_subentry_id: str | None = None,
) -> str | None:
    """Return a flow error after validating a proposed circuit atomically."""
    if not data[CONF_NAME]:
        return "name_required"
    if not _effective_topology_is_valid(
        entry,
        proposed_circuits=(data,),
        excluded_subentry_id=excluded_subentry_id,
    ):
        return "invalid_circuit"
    return None


class CircuitSubentryFlowHandler(config_entries.ConfigSubentryFlow):
    """Add a circuit and its delivery routes to existing plant objects."""

    _draft: dict[str, Any]
    _draft_compiled: CompiledPlant
    _reconfigure: bool

    def _options(self) -> CircuitOptions:
        """Return parent-owned dependencies with deletion-safe lifecycles."""
        configuration = plant_configuration_from_entry_data(self._get_entry().data)
        return CircuitOptions(
            zones=[
                selector.SelectOptionDict(value=zone.id, label=zone.name)
                for zone in configuration.zones
            ],
            valves=[
                selector.SelectOptionDict(value=valve.id, label=valve.name)
                for valve in configuration.valves
            ],
            pumps=[
                selector.SelectOptionDict(value=pump.id, label=pump.name)
                for pump in configuration.pumps
            ],
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Create one circuit serving one or more existing zones."""
        entry = self._get_entry()
        options = self._options()
        if not options.zones or not options.valves or not options.pumps:
            return self.async_abort(reason="incomplete_plant")

        errors: dict[str, str] = {}
        if user_input is not None:
            circuit_id = str(uuid4())
            data = _circuit_data(user_input, circuit_id)
            if error := _circuit_validation_error(entry, data):
                errors["base"] = error
            else:
                self._draft = data
                self._reconfigure = False
                compiled = _effective_topology_compile(
                    entry,
                    proposed_circuits=(data,),
                )
                if compiled is not None and compiled.warnings:
                    self._draft_compiled = compiled
                    return await self.async_step_review()
                return self.async_create_entry(
                    title=data[CONF_NAME], data=data, unique_id=circuit_id
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_circuit_schema(options),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Update a circuit without changing retained circuit or route UUIDs."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        options = self._options()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _circuit_data(
                user_input,
                subentry.data["id"],
                subentry.data[CONF_ROUTES],
            )
            if error := _circuit_validation_error(
                entry,
                data,
                excluded_subentry_id=subentry.subentry_id,
            ):
                errors["base"] = error
            else:
                self._draft = data
                self._reconfigure = True
                compiled = _effective_topology_compile(
                    entry,
                    proposed_circuits=(data,),
                    excluded_subentry_id=subentry.subentry_id,
                )
                if compiled is not None and compiled.warnings:
                    self._draft_compiled = compiled
                    return await self.async_step_review()
                return self.async_update_and_abort(
                    entry, subentry, title=data[CONF_NAME], data=data
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_circuit_schema(
                options,
                defaults=subentry.data,
            ),
            errors=errors,
        )

    async def async_step_review(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Confirm structured warnings before saving the proposed circuit."""
        if user_input is not None:
            if not user_input.get("confirm", False):
                return self.async_show_form(
                    step_id="review",
                    data_schema=_warning_review_schema(),
                    errors={"base": "confirm_required"},
                    description_placeholders={"warnings": _warning_text(self._draft_compiled)},
                )
            if self._reconfigure:
                subentry = self._get_reconfigure_subentry()
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=self._draft[CONF_NAME],
                    data=self._draft,
                )
            return self.async_create_entry(
                title=self._draft[CONF_NAME],
                data=self._draft,
                unique_id=self._draft["id"],
            )
        return self.async_show_form(
            step_id="review",
            data_schema=_warning_review_schema(),
            description_placeholders={"warnings": _warning_text(self._draft_compiled)},
        )


def _zone_data(
    user_input: Mapping[str, Any],
    zone_id: str,
    existing_routes: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize one zone and preserve route UUIDs for retained circuits."""
    circuit_ids = list(user_input[CONF_CIRCUIT_IDS])
    sensor_ids = [str(sensor_id) for sensor_id in user_input[CONF_TEMPERATURE_SENSORS]]
    raw_metadata = user_input.get(CONF_TEMPERATURE_SENSOR_METADATA)
    if raw_metadata is None:
        metadata = [
            {
                "entity_id": sensor_id,
                CONF_REQUIRED: True,
                CONF_WEIGHT: DEFAULT_SENSOR_WEIGHT,
                CONF_CALIBRATION_OFFSET: 0.0,
                CONF_MAX_AGE: DEFAULT_SENSOR_MAX_AGE,
                CONF_DESIGNATED_REFERENCE: False,
            }
            for sensor_id in sensor_ids
        ]
    elif isinstance(raw_metadata, Mapping):
        metadata = [
            {"entity_id": str(sensor_id), **dict(sensor_data)}
            for sensor_id, sensor_data in raw_metadata.items()
            if isinstance(sensor_data, Mapping)
        ]
    elif isinstance(raw_metadata, list):
        metadata = [dict(sensor_data) for sensor_data in raw_metadata]
    else:
        metadata = raw_metadata
    raw_preset_targets = user_input.get(CONF_PRESET_TARGETS, {})
    preset_targets = dict(raw_preset_targets) if isinstance(raw_preset_targets, Mapping) else {}
    for preset_name in (CONF_COMFORT_TARGET, CONF_ECO_TARGET, CONF_AWAY_TARGET):
        if preset_name in user_input and user_input[preset_name] is not None:
            preset_targets[preset_name] = user_input[preset_name]
    return {
        "id": zone_id,
        CONF_NAME: str(user_input[CONF_NAME]).strip(),
        CONF_TARGET_TEMPERATURE: user_input[CONF_TARGET_TEMPERATURE],
        CONF_TEMPERATURE_SENSORS: sensor_ids,
        CONF_TEMPERATURE_SENSOR_METADATA: metadata,
        CONF_TEMPERATURE_AGGREGATION: user_input.get(
            CONF_TEMPERATURE_AGGREGATION, DEFAULT_TEMPERATURE_AGGREGATION
        ),
        CONF_HEATING_START_DELTA: user_input.get(
            CONF_HEATING_START_DELTA, DEFAULT_HEATING_START_DELTA
        ),
        CONF_HEATING_STOP_DELTA: user_input.get(
            CONF_HEATING_STOP_DELTA, DEFAULT_HEATING_STOP_DELTA
        ),
        CONF_MINIMUM_ACTIVE_DURATION: user_input.get(
            CONF_MINIMUM_ACTIVE_DURATION, DEFAULT_MINIMUM_ACTIVE_DURATION
        ),
        CONF_MINIMUM_IDLE_DURATION: user_input.get(
            CONF_MINIMUM_IDLE_DURATION, DEFAULT_MINIMUM_IDLE_DURATION
        ),
        CONF_PRESET_TARGETS: preset_targets,
        CONF_CIRCUIT_IDS: circuit_ids,
        CONF_ROUTES: _routes_with_retained_fields(
            existing_routes,
            relationship_key="circuit_id",
            relationship_ids=circuit_ids,
        ),
    }


def _sensor_metadata_schema(
    sensor_id: str,
    defaults: Mapping[str, Any] | None = None,
) -> vol.Schema:
    """Build one explicit editor for one sensor's immutable metadata."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_SENSOR_ENTITY,
                default=defaults.get("entity_id", sensor_id),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_REQUIRED,
                default=defaults.get(CONF_REQUIRED, True),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_WEIGHT,
                default=defaults.get(CONF_WEIGHT, DEFAULT_SENSOR_WEIGHT),
            ): vol.All(vol.Coerce(float), vol.Range(min=0, min_included=False)),
            vol.Required(
                CONF_CALIBRATION_OFFSET,
                default=defaults.get(CONF_CALIBRATION_OFFSET, 0.0),
            ): vol.Coerce(float),
            vol.Required(
                CONF_MAX_AGE,
                default=defaults.get(CONF_MAX_AGE, DEFAULT_SENSOR_MAX_AGE),
            ): vol.All(vol.Coerce(float), vol.Range(min=0, min_included=False)),
            vol.Required(
                CONF_DESIGNATED_REFERENCE,
                default=defaults.get(CONF_DESIGNATED_REFERENCE, False),
            ): selector.BooleanSelector(),
        }
    )


def _sensor_metadata_record(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one metadata form result for canonical persistence."""
    return {
        "entity_id": str(user_input[CONF_SENSOR_ENTITY]),
        CONF_REQUIRED: bool(user_input[CONF_REQUIRED]),
        CONF_WEIGHT: user_input[CONF_WEIGHT],
        CONF_CALIBRATION_OFFSET: user_input[CONF_CALIBRATION_OFFSET],
        CONF_MAX_AGE: user_input[CONF_MAX_AGE],
        CONF_DESIGNATED_REFERENCE: bool(user_input[CONF_DESIGNATED_REFERENCE]),
    }


def _preset_targets_schema(defaults: Mapping[str, Any] | None = None) -> dict[Any, Any]:
    """Return optional finite target fields for standard heating presets."""
    defaults = defaults or {}
    targets = defaults.get(CONF_PRESET_TARGETS, {})
    if not isinstance(targets, Mapping):
        targets = {}
    fields: dict[Any, Any] = {}
    for name in (CONF_COMFORT_TARGET, CONF_ECO_TARGET, CONF_AWAY_TARGET):
        key: Any = vol.Optional(
            name,
            **({"default": targets[name]} if name in targets else {}),
        )
        fields[key] = vol.All(
            vol.Coerce(float),
            vol.Range(
                min=MIN_ZONE_TARGET_TEMPERATURE,
                max=MAX_ZONE_TARGET_TEMPERATURE,
            ),
        )
    return fields


def _zone_advanced_fields(defaults: Mapping[str, Any] | None = None) -> dict[Any, Any]:
    """Return fields shared by initial and subentry zone forms."""
    defaults = defaults or {}
    return {
        vol.Required(
            CONF_HEATING_START_DELTA,
            default=defaults.get(CONF_HEATING_START_DELTA, DEFAULT_HEATING_START_DELTA),
        ): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Required(
            CONF_HEATING_STOP_DELTA,
            default=defaults.get(CONF_HEATING_STOP_DELTA, DEFAULT_HEATING_STOP_DELTA),
        ): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Required(
            CONF_MINIMUM_ACTIVE_DURATION,
            default=defaults.get(CONF_MINIMUM_ACTIVE_DURATION, DEFAULT_MINIMUM_ACTIVE_DURATION),
        ): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Required(
            CONF_MINIMUM_IDLE_DURATION,
            default=defaults.get(CONF_MINIMUM_IDLE_DURATION, DEFAULT_MINIMUM_IDLE_DURATION),
        ): vol.All(vol.Coerce(float), vol.Range(min=0)),
        **_preset_targets_schema(defaults),
    }


def _zone_temperature_sensor_defaults(defaults: Mapping[str, Any]) -> Any:
    """Return new-list defaults for current and milestone 1 subentries."""
    if CONF_TEMPERATURE_SENSORS in defaults:
        return defaults[CONF_TEMPERATURE_SENSORS]
    if CONF_TEMPERATURE_SENSOR in defaults:
        return [defaults[CONF_TEMPERATURE_SENSOR]]
    return vol.UNDEFINED


def _zone_temperature_aggregation_default(defaults: Mapping[str, Any]) -> str:
    """Return the persisted or legacy-default aggregation policy."""
    return str(defaults.get(CONF_TEMPERATURE_AGGREGATION, DEFAULT_TEMPERATURE_AGGREGATION))


def _zone_has_editable_sensor_metadata(defaults: Mapping[str, Any]) -> bool:
    """Return whether a persisted zone can expose metadata-dependent policies."""
    metadata = defaults.get(CONF_TEMPERATURE_SENSOR_METADATA)
    return isinstance(metadata, list) and bool(metadata)


def _temperature_aggregation_selector(
    *, include_metadata_policies: bool = False
) -> selector.SelectSelector:
    """Build a policy selector with weighted mean gated by metadata editing."""
    labels = {
        TemperatureAggregation.MEAN.value: "Mean",
        TemperatureAggregation.MEDIAN.value: "Median",
        TemperatureAggregation.MINIMUM.value: "Heating-oriented minimum",
        TemperatureAggregation.MAXIMUM.value: "Cooling-oriented maximum",
        TemperatureAggregation.DESIGNATED_REFERENCE.value: "Designated reference",
        TemperatureAggregation.WEIGHTED_MEAN.value: "Weighted mean",
    }
    user_selectable = [
        TemperatureAggregation.MEAN,
        TemperatureAggregation.MEDIAN,
        TemperatureAggregation.MINIMUM,
        TemperatureAggregation.MAXIMUM,
    ]
    if include_metadata_policies:
        user_selectable.extend(
            [TemperatureAggregation.DESIGNATED_REFERENCE, TemperatureAggregation.WEIGHTED_MEAN]
        )
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value=policy.value, label=labels[policy.value])
                for policy in user_selectable
            ]
        )
    )


def _zone_schema(
    circuit_options: list[selector.SelectOptionDict],
    defaults: Mapping[str, Any] | None = None,
) -> vol.Schema:
    """Build the shared zone form schema."""
    defaults = defaults or {}
    schema: dict[Any, Any] = {
        vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, vol.UNDEFINED)): str,
        vol.Required(
            CONF_TARGET_TEMPERATURE,
            default=defaults.get(CONF_TARGET_TEMPERATURE, DEFAULT_TARGET_TEMPERATURE),
        ): vol.Coerce(float),
        vol.Required(
            CONF_TEMPERATURE_SENSORS,
            default=_zone_temperature_sensor_defaults(defaults),
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", multiple=True)),
        vol.Required(
            CONF_TEMPERATURE_AGGREGATION,
            default=_zone_temperature_aggregation_default(defaults),
        ): _temperature_aggregation_selector(
            include_metadata_policies=_zone_has_editable_sensor_metadata(defaults)
        ),
        vol.Optional(
            CONF_CONFIGURE_SENSOR_METADATA,
            default=False,
        ): selector.BooleanSelector(),
        vol.Required(
            CONF_CIRCUIT_IDS,
            default=defaults.get(CONF_CIRCUIT_IDS, vol.UNDEFINED),
        ): _topology_select(circuit_options, multiple=True),
    }
    schema.update(_zone_advanced_fields(defaults))
    return vol.Schema(schema)


def _zone_validation_error(
    entry: config_entries.ConfigEntry,
    data: Mapping[str, Any],
    *,
    excluded_subentry_id: str | None = None,
) -> str | None:
    """Return a flow error after validating a proposed zone atomically."""
    if not data[CONF_NAME]:
        return "name_required"
    if not _effective_topology_is_valid(
        entry,
        proposed_zones=(data,),
        excluded_subentry_id=excluded_subentry_id,
    ):
        return "invalid_zone"
    return None


def _requires_sensor_metadata_path(data: Mapping[str, Any]) -> bool:
    """Return whether the selected policy requires the typed metadata editor."""
    return data.get(CONF_TEMPERATURE_AGGREGATION) in {
        TemperatureAggregation.DESIGNATED_REFERENCE.value,
        TemperatureAggregation.WEIGHTED_MEAN.value,
    }


class ZoneSubentryFlowHandler(config_entries.ConfigSubentryFlow):
    """Add a comfort zone routed through existing parent-owned circuits."""

    _zone_draft: dict[str, Any]
    _metadata_records: list[dict[str, Any]]
    _metadata_index: int
    _zone_reconfigure: bool
    _zone_compiled: CompiledPlant

    def _circuit_options(self) -> list[selector.SelectOptionDict]:
        configuration = plant_configuration_from_entry_data(self._get_entry().data)
        return [
            selector.SelectOptionDict(value=circuit.id, label=circuit.name)
            for circuit in configuration.circuits
        ]

    async def _finish_zone(self) -> config_entries.SubentryFlowResult:
        """Validate and persist the completed zone draft as one atomic operation."""
        entry = self._get_entry()
        excluded_subentry_id = None
        if self._zone_reconfigure:
            excluded_subentry_id = self._get_reconfigure_subentry().subentry_id
        if error := _zone_validation_error(
            entry,
            self._zone_draft,
            excluded_subentry_id=excluded_subentry_id,
        ):
            return self.async_show_form(step_id="sensor_policy", errors={"base": error})
        compiled = _effective_topology_compile(
            entry,
            proposed_zones=(self._zone_draft,),
            excluded_subentry_id=excluded_subentry_id,
        )
        if compiled is None:
            return self.async_show_form(step_id="sensor_policy", errors={"base": "invalid_zone"})
        if compiled.warnings:
            self._zone_compiled = compiled
            return await self.async_step_review()
        if self._zone_reconfigure:
            subentry = self._get_reconfigure_subentry()
            return self.async_update_and_abort(
                entry,
                subentry,
                title=self._zone_draft[CONF_NAME],
                data=self._zone_draft,
            )
        return self.async_create_entry(
            title=self._zone_draft[CONF_NAME],
            data=self._zone_draft,
            unique_id=self._zone_draft["id"],
        )

    async def async_step_sensor_metadata(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Edit one sensor at a time so weights and safety metadata stay typed."""
        sensor_ids = list(self._zone_draft[CONF_TEMPERATURE_SENSORS])
        if user_input is not None:
            self._metadata_records.append(_sensor_metadata_record(user_input))
            self._metadata_index += 1
        if self._metadata_index < len(sensor_ids):
            sensor_id = sensor_ids[self._metadata_index]
            defaults = next(
                (
                    record
                    for record in self._zone_draft[CONF_TEMPERATURE_SENSOR_METADATA]
                    if record.get("entity_id") == sensor_id
                ),
                {},
            )
            return self.async_show_form(
                step_id="sensor_metadata",
                data_schema=_sensor_metadata_schema(sensor_id, defaults),
                description_placeholders={"sensor": sensor_id},
            )
        if self._metadata_records:
            sensor_ids = [record["entity_id"] for record in self._metadata_records]
            self._zone_draft[CONF_TEMPERATURE_SENSORS] = sensor_ids
            self._zone_draft[CONF_TEMPERATURE_SENSOR_METADATA] = self._metadata_records
        return await self.async_step_sensor_policy()

    async def async_step_sensor_policy(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Choose policies that require the completed editable sensor metadata path."""
        if user_input is not None:
            self._zone_draft[CONF_TEMPERATURE_AGGREGATION] = user_input[
                CONF_TEMPERATURE_AGGREGATION
            ]
            return await self._finish_zone()
        return self.async_show_form(
            step_id="sensor_policy",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TEMPERATURE_AGGREGATION,
                        default=self._zone_draft[CONF_TEMPERATURE_AGGREGATION],
                    ): _temperature_aggregation_selector(include_metadata_policies=True),
                }
            ),
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Create one comfort zone attached to one or more circuits."""
        entry = self._get_entry()
        circuit_options = self._circuit_options()
        if not circuit_options:
            return self.async_abort(reason="no_circuits")

        errors: dict[str, str] = {}
        if user_input is not None:
            zone_id = str(uuid4())
            data = _zone_data(user_input, zone_id)
            if user_input.get(CONF_CONFIGURE_SENSOR_METADATA):
                self._zone_draft = data
                self._metadata_records = []
                self._metadata_index = 0
                self._zone_reconfigure = False
                return await self.async_step_sensor_metadata()
            if _requires_sensor_metadata_path(data) and (
                CONF_TEMPERATURE_SENSOR_METADATA not in user_input
            ):
                errors["base"] = "sensor_metadata_required"
            elif error := _zone_validation_error(entry, data):
                errors["base"] = error
            else:
                self._zone_draft = data
                self._zone_reconfigure = False
                return await self._finish_zone()

        return self.async_show_form(
            step_id="user",
            data_schema=_zone_schema(circuit_options),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Update one zone without changing retained zone or route UUIDs."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        circuit_options = self._circuit_options()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _zone_data(
                user_input,
                subentry.data["id"],
                subentry.data[CONF_ROUTES],
            )
            if user_input.get(CONF_CONFIGURE_SENSOR_METADATA):
                self._zone_draft = data
                self._metadata_records = []
                self._metadata_index = 0
                self._zone_reconfigure = True
                return await self.async_step_sensor_metadata()
            if _requires_sensor_metadata_path(data) and (
                CONF_TEMPERATURE_SENSOR_METADATA not in user_input
            ):
                errors["base"] = "sensor_metadata_required"
            elif error := _zone_validation_error(
                entry,
                data,
                excluded_subentry_id=subentry.subentry_id,
            ):
                errors["base"] = error
            else:
                self._zone_draft = data
                self._zone_reconfigure = True
                return await self._finish_zone()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_zone_schema(circuit_options, subentry.data),
            errors=errors,
        )

    async def async_step_review(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Confirm structured warnings before saving the proposed zone."""
        if user_input is not None:
            if not user_input.get("confirm", False):
                return self.async_show_form(
                    step_id="review",
                    data_schema=_warning_review_schema(),
                    errors={"base": "confirm_required"},
                    description_placeholders={"warnings": _warning_text(self._zone_compiled)},
                )
            if self._zone_reconfigure:
                subentry = self._get_reconfigure_subentry()
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=self._zone_draft[CONF_NAME],
                    data=self._zone_draft,
                )
            return self.async_create_entry(
                title=self._zone_draft[CONF_NAME],
                data=self._zone_draft,
                unique_id=self._zone_draft["id"],
            )
        return self.async_show_form(
            step_id="review",
            data_schema=_warning_review_schema(),
            description_placeholders={"warnings": _warning_text(self._zone_compiled)},
        )


def _valve_actuator_data(user_input: Mapping[str, Any], actuator_id: str) -> dict[str, Any]:
    """Normalize one valve actuator payload for persistent subentry storage."""
    return {
        "id": actuator_id,
        CONF_ACTUATOR_KIND: ACTUATOR_KIND_VALVE,
        CONF_NAME: str(user_input[CONF_NAME]).strip(),
        CONF_ENTITY_ID: user_input[CONF_ENTITY_ID],
        CONF_OPENING_TIME: user_input[CONF_OPENING_TIME],
        CONF_CIRCUIT_IDS: user_input[CONF_CIRCUIT_IDS],
    }


def _valve_actuator_schema(
    circuit_options: list[selector.SelectOptionDict],
    defaults: Mapping[str, Any] | None = None,
) -> vol.Schema:
    """Build the shared valve form schema with optional reconfigure defaults."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, vol.UNDEFINED)): str,
            vol.Required(
                CONF_ENTITY_ID, default=defaults.get(CONF_ENTITY_ID, vol.UNDEFINED)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain=["switch", "valve"])),
            vol.Required(
                CONF_OPENING_TIME,
                default=defaults.get(CONF_OPENING_TIME, DEFAULT_VALVE_OPENING_TIME),
            ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Required(
                CONF_CIRCUIT_IDS,
                default=defaults.get(CONF_CIRCUIT_IDS, vol.UNDEFINED),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=circuit_options,
                    multiple=True,
                )
            ),
        }
    )


def _actuator_validation_error(
    entry: config_entries.ConfigEntry,
    data: Mapping[str, Any],
    *,
    excluded_subentry_id: str | None = None,
) -> str | None:
    """Return a flow error after validating the complete proposed topology."""
    if not data[CONF_NAME]:
        return "name_required"
    if not _effective_topology_is_valid(
        entry,
        proposed_actuators=(data,),
        excluded_subentry_id=excluded_subentry_id,
    ):
        return "invalid_actuator"
    return None


class ActuatorSubentryFlowHandler(config_entries.ConfigSubentryFlow):
    """Add an actuator that extends one or more existing hydraulic circuits."""

    _draft: dict[str, Any]
    _draft_compiled: CompiledPlant
    _reconfigure: bool

    def _circuit_options(self) -> list[selector.SelectOptionDict]:
        entry = self._get_entry()
        configuration = plant_configuration_from_entry_data(entry.data)
        return [
            selector.SelectOptionDict(value=circuit.id, label=circuit.name)
            for circuit in configuration.circuits
        ]

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Create one valve actuator attached to selected circuits."""
        entry = self._get_entry()
        circuit_options = self._circuit_options()
        if not circuit_options:
            return self.async_abort(reason="no_circuits")

        errors: dict[str, str] = {}
        if user_input is not None:
            actuator_id = str(uuid4())
            data = _valve_actuator_data(user_input, actuator_id)
            if error := _actuator_validation_error(entry, data):
                errors["base"] = error
            else:
                self._draft = data
                self._reconfigure = False
                compiled = _effective_topology_compile(
                    entry,
                    proposed_actuators=(data,),
                )
                if compiled is not None and compiled.warnings:
                    self._draft_compiled = compiled
                    return await self.async_step_review()
                return self.async_create_entry(
                    title=data[CONF_NAME], data=data, unique_id=actuator_id
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_valve_actuator_schema(circuit_options),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Update one valve actuator without changing its stable UUID."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        circuit_options = self._circuit_options()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _valve_actuator_data(user_input, subentry.data["id"])
            if error := _actuator_validation_error(
                entry,
                data,
                excluded_subentry_id=subentry.subentry_id,
            ):
                errors["base"] = error
            else:
                self._draft = data
                self._reconfigure = True
                compiled = _effective_topology_compile(
                    entry,
                    proposed_actuators=(data,),
                    excluded_subentry_id=subentry.subentry_id,
                )
                if compiled is not None and compiled.warnings:
                    self._draft_compiled = compiled
                    return await self.async_step_review()
                return self.async_update_and_abort(
                    entry, subentry, title=data[CONF_NAME], data=data
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_valve_actuator_schema(circuit_options, subentry.data),
            errors=errors,
        )

    async def async_step_review(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Confirm structured warnings before saving the proposed actuator."""
        if user_input is not None:
            if not user_input.get("confirm", False):
                return self.async_show_form(
                    step_id="review",
                    data_schema=_warning_review_schema(),
                    errors={"base": "confirm_required"},
                    description_placeholders={"warnings": _warning_text(self._draft_compiled)},
                )
            if self._reconfigure:
                subentry = self._get_reconfigure_subentry()
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=self._draft[CONF_NAME],
                    data=self._draft,
                )
            return self.async_create_entry(
                title=self._draft[CONF_NAME],
                data=self._draft,
                unique_id=self._draft["id"],
            )
        return self.async_show_form(
            step_id="review",
            data_schema=_warning_review_schema(),
            description_placeholders={"warnings": _warning_text(self._draft_compiled)},
        )


class HydronicClimateConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle creation of a hydronic plant config entry."""

    VERSION = 1

    _draft: dict[str, Any]
    _zone_draft: dict[str, Any]
    _metadata_records: list[dict[str, Any]]
    _metadata_index: int

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type[config_entries.ConfigSubentryFlow]]:
        """Return dynamic object types supported by this plant."""
        return {
            SUBENTRY_TYPE_ACTUATOR: ActuatorSubentryFlowHandler,
            SUBENTRY_TYPE_CIRCUIT: CircuitSubentryFlowHandler,
            SUBENTRY_TYPE_ZONE: ZoneSubentryFlowHandler,
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            name = str(user_input[CONF_NAME]).strip()
            if not name:
                errors["base"] = "name_required"
            else:
                self._draft = {
                    CONF_NAME: name,
                    CONF_PLANT_ID: str(uuid4()),
                    CONF_SHADOW_MODE: True,
                }
                return await self.async_step_zone()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=DEFAULT_PLANT_NAME): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Collect the first shadow-mode comfort zone."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input[CONF_NAME]).strip()
            if name:
                sensor_ids = [str(sensor_id) for sensor_id in user_input[CONF_TEMPERATURE_SENSORS]]
                self._zone_draft = {
                    "id": str(uuid4()),
                    CONF_NAME: name,
                    CONF_TARGET_TEMPERATURE: user_input[CONF_TARGET_TEMPERATURE],
                    CONF_TEMPERATURE_SENSORS: sensor_ids,
                    CONF_TEMPERATURE_SENSOR_METADATA: [
                        {
                            "entity_id": sensor_id,
                            CONF_REQUIRED: True,
                            CONF_WEIGHT: DEFAULT_SENSOR_WEIGHT,
                            CONF_CALIBRATION_OFFSET: 0.0,
                            CONF_MAX_AGE: DEFAULT_SENSOR_MAX_AGE,
                            CONF_DESIGNATED_REFERENCE: False,
                        }
                        for sensor_id in sensor_ids
                    ],
                    CONF_TEMPERATURE_AGGREGATION: user_input.get(
                        CONF_TEMPERATURE_AGGREGATION, DEFAULT_TEMPERATURE_AGGREGATION
                    ),
                    CONF_HEATING_START_DELTA: user_input.get(
                        CONF_HEATING_START_DELTA, DEFAULT_HEATING_START_DELTA
                    ),
                    CONF_HEATING_STOP_DELTA: user_input.get(
                        CONF_HEATING_STOP_DELTA, DEFAULT_HEATING_STOP_DELTA
                    ),
                    CONF_MINIMUM_ACTIVE_DURATION: user_input.get(
                        CONF_MINIMUM_ACTIVE_DURATION, DEFAULT_MINIMUM_ACTIVE_DURATION
                    ),
                    CONF_MINIMUM_IDLE_DURATION: user_input.get(
                        CONF_MINIMUM_IDLE_DURATION, DEFAULT_MINIMUM_IDLE_DURATION
                    ),
                    CONF_PRESET_TARGETS: {
                        preset_name: user_input[preset_name]
                        for preset_name in (
                            CONF_COMFORT_TARGET,
                            CONF_ECO_TARGET,
                            CONF_AWAY_TARGET,
                        )
                        if user_input.get(preset_name) is not None
                    },
                }
                if user_input.get(CONF_TEMPERATURE_SENSOR_METADATA) is not None:
                    self._zone_draft[CONF_TEMPERATURE_SENSOR_METADATA] = user_input[
                        CONF_TEMPERATURE_SENSOR_METADATA
                    ]
                if user_input.get(CONF_CONFIGURE_SENSOR_METADATA):
                    self._metadata_records = []
                    self._metadata_index = 0
                    return await self.async_step_sensor_metadata()
                if _requires_sensor_metadata_path(self._zone_draft) and (
                    CONF_TEMPERATURE_SENSOR_METADATA not in user_input
                ):
                    errors["base"] = "sensor_metadata_required"
                else:
                    self._draft[CONF_TOPOLOGY] = {CONF_ZONES: [self._zone_draft]}
                    return await self.async_step_circuit()
            else:
                errors["base"] = "name_required"

        return self.async_show_form(
            step_id="zone",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Required(
                        CONF_TARGET_TEMPERATURE, default=DEFAULT_TARGET_TEMPERATURE
                    ): vol.Coerce(float),
                    vol.Required(CONF_TEMPERATURE_SENSORS): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor", multiple=True)
                    ),
                    vol.Required(
                        CONF_TEMPERATURE_AGGREGATION,
                        default=DEFAULT_TEMPERATURE_AGGREGATION,
                    ): _temperature_aggregation_selector(),
                    vol.Optional(
                        CONF_CONFIGURE_SENSOR_METADATA,
                        default=False,
                    ): selector.BooleanSelector(),
                    **_zone_advanced_fields(),
                }
            ),
            errors=errors,
        )

    async def async_step_sensor_metadata(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Edit initial sensor metadata through typed one-sensor forms."""
        sensor_ids = list(self._zone_draft[CONF_TEMPERATURE_SENSORS])
        if user_input is not None:
            self._metadata_records.append(_sensor_metadata_record(user_input))
            self._metadata_index += 1
        if self._metadata_index < len(sensor_ids):
            sensor_id = sensor_ids[self._metadata_index]
            defaults = next(
                (
                    record
                    for record in self._zone_draft[CONF_TEMPERATURE_SENSOR_METADATA]
                    if record.get("entity_id") == sensor_id
                ),
                {},
            )
            return self.async_show_form(
                step_id="sensor_metadata",
                data_schema=_sensor_metadata_schema(sensor_id, defaults),
                description_placeholders={"sensor": sensor_id},
            )
        if self._metadata_records:
            self._zone_draft[CONF_TEMPERATURE_SENSORS] = [
                record["entity_id"] for record in self._metadata_records
            ]
            self._zone_draft[CONF_TEMPERATURE_SENSOR_METADATA] = self._metadata_records
        return await self.async_step_sensor_policy()

    async def async_step_sensor_policy(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Choose designated-reference or weighted aggregation after metadata editing."""
        if user_input is not None:
            self._zone_draft[CONF_TEMPERATURE_AGGREGATION] = user_input[
                CONF_TEMPERATURE_AGGREGATION
            ]
            self._draft[CONF_TOPOLOGY] = {CONF_ZONES: [self._zone_draft]}
            return await self.async_step_circuit()
        return self.async_show_form(
            step_id="sensor_policy",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TEMPERATURE_AGGREGATION,
                        default=self._zone_draft[CONF_TEMPERATURE_AGGREGATION],
                    ): _temperature_aggregation_selector(include_metadata_policies=True),
                }
            ),
        )

    async def async_step_circuit(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Collect the first hydraulic circuit and its shadow-only equipment path."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input[CONF_NAME]).strip()
            if name:
                circuit_id = str(uuid4())
                valve_id = str(uuid4())
                pump_id = str(uuid4())
                zone_id = self._draft[CONF_TOPOLOGY][CONF_ZONES][0]["id"]
                self._draft[CONF_TOPOLOGY][CONF_VALVES] = [
                    {
                        "id": valve_id,
                        CONF_NAME: f"{name} valve",
                        CONF_ENTITY_ID: user_input[CONF_VALVE_ENTITY],
                        CONF_OPENING_TIME: user_input[CONF_VALVE_OPENING_TIME],
                    }
                ]
                self._draft[CONF_TOPOLOGY][CONF_PUMPS] = [
                    {
                        "id": pump_id,
                        CONF_NAME: f"{name} pump",
                        CONF_ENTITY_ID: user_input[CONF_PUMP_ENTITY],
                        CONF_OVERRUN: user_input[CONF_PUMP_OVERRUN],
                    }
                ]
                self._draft[CONF_TOPOLOGY][CONF_CIRCUITS] = [
                    {
                        "id": circuit_id,
                        CONF_NAME: name,
                        CONF_VALVE_IDS: [valve_id],
                        "pump_id": pump_id,
                    }
                ]
                self._draft[CONF_TOPOLOGY][CONF_ROUTES] = [
                    {"id": str(uuid4()), "zone_id": zone_id, "circuit_id": circuit_id}
                ]
                return await self.async_step_review()
            errors["base"] = "name_required"

        return self.async_show_form(
            step_id="circuit",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Required(CONF_VALVE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=["switch", "valve"])
                    ),
                    vol.Required(CONF_PUMP_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="switch")
                    ),
                    vol.Required(
                        CONF_VALVE_OPENING_TIME, default=DEFAULT_VALVE_OPENING_TIME
                    ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                    vol.Required(CONF_PUMP_OVERRUN, default=DEFAULT_PUMP_OVERRUN): vol.All(
                        vol.Coerce(float), vol.Range(min=0)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_review(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Validate the initial topology before storing it in a config entry."""
        topology = self._draft[CONF_TOPOLOGY]
        try:
            plant = compile_topology(plant_configuration_from_entry_data(self._draft))
        except StoredTopologyError, TopologyValidationError:
            return self.async_show_form(
                step_id="review",
                errors={"base": "invalid_topology"},
                description_placeholders=_initial_review_placeholders(topology, None),
            )

        if user_input is not None:
            await self.async_set_unique_id(self._draft[CONF_PLANT_ID])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=self._draft[CONF_NAME], data=self._draft)

        return self.async_show_form(
            step_id="review",
            description_placeholders=_initial_review_placeholders(topology, plant),
        )
