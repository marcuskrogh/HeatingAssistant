"""Repairs platform: Restart required after a thin-bridge sync.

Home Assistant Settings shows this under the Repairs card (HACS uses the same
pattern), not under Updates.
"""

from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
import voluptuous as vol

from .const import NAME
from .restart_issue import ISSUE_ID


class RestartRequiredFixFlow(RepairsFlow):
    """Confirm and restart Home Assistant Core."""

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        return await self.async_step_confirm_restart(user_input)

    async def async_step_confirm_restart(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        if user_input is not None:
            await self.hass.services.async_call("homeassistant", "restart")
            return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="confirm_restart",
            data_schema=vol.Schema({}),
            description_placeholders={"name": NAME},
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None = None,
    *args: Any,
    **kwargs: Any,
) -> RepairsFlow | None:
    """Create flow."""

    del hass, data, args, kwargs
    if issue_id == ISSUE_ID:
        return RestartRequiredFixFlow()
    return None
