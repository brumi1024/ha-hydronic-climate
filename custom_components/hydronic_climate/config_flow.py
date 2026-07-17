"""Config flow for Hydronic Climate."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries, selector

from .const import (
    CONF_CIRCUITS,
    CONF_NAME,
    CONF_PLANT_ID,
    CONF_PUMP_ENTITY,
    CONF_PUMP_OVERRUN,
    CONF_ROUTES,
    CONF_SHADOW_MODE,
    CONF_TARGET_TEMPERATURE,
    CONF_TEMPERATURE_SENSOR,
    CONF_TOPOLOGY,
    CONF_VALVE_ENTITY,
    CONF_VALVE_OPENING_TIME,
    CONF_ZONES,
    DEFAULT_PLANT_NAME,
    DEFAULT_PUMP_OVERRUN,
    DEFAULT_TARGET_TEMPERATURE,
    DEFAULT_VALVE_OPENING_TIME,
    DOMAIN,
)
from .core.configuration import StoredTopologyError, plant_configuration_from_entry_data
from .core.topology import TopologyValidationError, compile_topology


class HydronicClimateConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle creation of a hydronic plant config entry."""

    VERSION = 1

    _draft: dict[str, Any]

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
                self._draft[CONF_TOPOLOGY] = {
                    CONF_ZONES: [
                        {
                            "id": str(uuid4()),
                            CONF_NAME: name,
                            CONF_TARGET_TEMPERATURE: user_input[CONF_TARGET_TEMPERATURE],
                            CONF_TEMPERATURE_SENSOR: user_input[CONF_TEMPERATURE_SENSOR],
                        }
                    ]
                }
                return await self.async_step_circuit()
            errors["base"] = "name_required"

        return self.async_show_form(
            step_id="zone",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Required(
                        CONF_TARGET_TEMPERATURE, default=DEFAULT_TARGET_TEMPERATURE
                    ): vol.Coerce(float),
                    vol.Required(CONF_TEMPERATURE_SENSOR): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                }
            ),
            errors=errors,
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
                zone_id = self._draft[CONF_TOPOLOGY][CONF_ZONES][0]["id"]
                self._draft[CONF_TOPOLOGY][CONF_CIRCUITS] = [
                    {
                        "id": circuit_id,
                        CONF_NAME: name,
                        "valve_id": user_input[CONF_VALVE_ENTITY],
                        "pump_id": user_input[CONF_PUMP_ENTITY],
                        CONF_VALVE_OPENING_TIME: user_input[CONF_VALVE_OPENING_TIME],
                        CONF_PUMP_OVERRUN: user_input[CONF_PUMP_OVERRUN],
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
        if user_input is not None:
            try:
                compile_topology(plant_configuration_from_entry_data(self._draft))
            except (StoredTopologyError, TopologyValidationError):
                return self.async_show_form(step_id="review", errors={"base": "invalid_topology"})

            await self.async_set_unique_id(self._draft[CONF_PLANT_ID])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=self._draft[CONF_NAME], data=self._draft)

        topology = self._draft[CONF_TOPOLOGY]
        return self.async_show_form(
            step_id="review",
            description_placeholders={
                "zone": topology[CONF_ZONES][0][CONF_NAME],
                "circuit": topology[CONF_CIRCUITS][0][CONF_NAME],
            },
        )
