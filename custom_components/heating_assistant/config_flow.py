"""Config flow for the Heating Assistant integration."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_HORIZON,
    CONF_LATITUDE,
    CONF_MPC_ANALYTIC_DERIVATIVES,
    CONF_MPC_SOLVER,
    CONF_LONGITUDE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    CONF_ROOMS,
    CONF_HEAT_SOURCES,
    DEFAULT_HORIZON,
    DEFAULT_MPC_ANALYTIC_DERIVATIVES,
    DEFAULT_MPC_SOLVER,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    NAME,
)

_LOGGER = logging.getLogger(__name__)


class HeatingAssistantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial configuration flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """
        Step 1: Basic site settings (location, outdoor sensor, time step).

        Room and heat-source configuration is provided via YAML in
        ``configuration.yaml`` (see README for the schema).
        """
        errors: Dict[str, str] = {}

        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title=NAME, data=self._data)

        # Pre-fill with HA's configured location
        ha_lat = self.hass.config.latitude
        ha_lon = self.hass.config.longitude

        schema = vol.Schema(
            {
                vol.Required(CONF_LATITUDE, default=ha_lat): vol.Coerce(float),
                vol.Required(CONF_LONGITUDE, default=ha_lon): vol.Coerce(float),
                vol.Optional(CONF_OUTDOOR_TEMP_ENTITY, default=""): str,
                vol.Optional(CONF_WEATHER_ENTITY, default=""): str,
                vol.Optional(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): vol.All(
                    vol.Coerce(int), vol.Range(min=60, max=3600)
                ),
                vol.Optional(CONF_HORIZON, default=DEFAULT_HORIZON): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=24)
                ),
                vol.Optional(CONF_MPC_SOLVER, default=DEFAULT_MPC_SOLVER): vol.All(
                    str, lambda value: value.lower(), vol.In(["slsqp", "ipopt", "cyipopt"])
                ),
                vol.Optional(
                    CONF_MPC_ANALYTIC_DERIVATIVES,
                    default=DEFAULT_MPC_ANALYTIC_DERIVATIVES,
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "HeatingAssistantOptionsFlow":
        return HeatingAssistantOptionsFlow(config_entry)


class HeatingAssistantOptionsFlow(config_entries.OptionsFlow):
    """Handle options (reconfiguration) for an existing entry."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        errors: Dict[str, str] = {}

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._entry.options or self._entry.data

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_OUTDOOR_TEMP_ENTITY,
                    default=current.get(CONF_OUTDOOR_TEMP_ENTITY, ""),
                ): str,
                vol.Optional(
                    CONF_WEATHER_ENTITY,
                    default=current.get(CONF_WEATHER_ENTITY, ""),
                ): str,
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=current.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=60, max=3600)),
                vol.Optional(
                    CONF_HORIZON,
                    default=current.get(CONF_HORIZON, DEFAULT_HORIZON),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=24)),
                vol.Optional(
                    CONF_MPC_SOLVER,
                    default=current.get(CONF_MPC_SOLVER, DEFAULT_MPC_SOLVER),
                ): vol.All(
                    str, lambda value: value.lower(), vol.In(["slsqp", "ipopt", "cyipopt"])
                ),
                vol.Optional(
                    CONF_MPC_ANALYTIC_DERIVATIVES,
                    default=current.get(
                        CONF_MPC_ANALYTIC_DERIVATIVES,
                        DEFAULT_MPC_ANALYTIC_DERIVATIVES,
                    ),
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
