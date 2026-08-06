"""Custom industrial dashboard panel registration for Home Assistant."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def async_register_industrial_panel(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register static assets and the built-in sidebar panel for the industrial UI."""
    try:
        import pathlib

        from homeassistant.components.http import StaticPathConfig

        www_path = pathlib.Path(__file__).parent / "www"
        await hass.http.async_register_static_paths(
            [StaticPathConfig("/ha-industrial-panel", str(www_path), cache_headers=False)]
        )

        # Register the custom icon set so it is available on every HA page
        # (including the sidebar) before the frontend renders.
        _sidebar_icon = "mdi:radiator"
        try:
            from homeassistant.components.frontend import async_register_extra_urls

            async_register_extra_urls(
                hass, ["/ha-industrial-panel/heating-assistant-icons.js"]
            )
            _sidebar_icon = "heating-assistant:logo"
        except (ImportError, AttributeError):
            _LOGGER.debug(
                "Heating Assistant: async_register_extra_urls unavailable, "
                "falling back to mdi:radiator sidebar icon",
            )

        from homeassistant.components.frontend import async_register_built_in_panel

        async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title="Heating Assistant",
            sidebar_icon=_sidebar_icon,
            frontend_url_path="ha-industrial",
            config={
                "_panel_custom": {
                    "name": "ha-industrial-panel",
                    # This ?v= token is the SINGLE source of truth for the
                    # frontend cache-bust version.  industrial-dashboard.js reads
                    # this exact token off its own URL (import.meta.url) and reuses
                    # it for every dynamically-imported submodule, so the entry
                    # point and its submodules can never drift out of sync.  Bump
                    # this token (and nothing else) on every frontend change to
                    # force browsers/service-workers to fetch fresh assets.
                    "js_url": "/ha-industrial-panel/industrial-dashboard.js?v=114",
                    "embed_iframe": False,
                }
            },
            require_admin=False,
        )
    except Exception:
        _LOGGER.debug(
            "Heating Assistant: custom panel registration skipped",
            exc_info=True,
        )
