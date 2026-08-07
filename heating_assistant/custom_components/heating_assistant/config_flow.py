"""Config flow for the staged thin Heating Assistant MQTT bridge."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries

from .const import CONF_INSTANCE_ID, DEFAULT_INSTANCE_ID, DOMAIN, NAME


class HeatingAssistantMqttThinConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Collect only the MQTT App instance id."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            instance_id = (user_input.get(CONF_INSTANCE_ID) or DEFAULT_INSTANCE_ID).strip()
            await self.async_set_unique_id(instance_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"{NAME} ({instance_id})",
                data={CONF_INSTANCE_ID: instance_id},
            )

        schema = vol.Schema({vol.Required(CONF_INSTANCE_ID, default=DEFAULT_INSTANCE_ID): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors={})
