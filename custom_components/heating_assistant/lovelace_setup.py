"""Dormant Lovelace dashboard auto-write and registration helpers."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .services.configuration import (
    DEFAULT_DASHBOARD_FILENAME,
    DEFAULT_INDUSTRIAL_DASHBOARD_FILENAME,
)

if TYPE_CHECKING:
    from .coordinator import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)

DASHBOARD_URL_PATH = "heating-assistant"
DASHBOARD_INDUSTRIAL_URL_PATH = "heating-assistant-industrial"


def _store(hass: HomeAssistant, version: int, key: str):
    """Return a storage helper, respecting ``__init__.Store`` test patches."""
    import custom_components.heating_assistant.__init__ as init_pkg

    return init_pkg.Store(hass, version, key)


async def async_auto_write_default_dashboard(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HeatingAssistantCoordinator,
) -> Optional[str]:
    """Write the default dashboard YAML on first setup (or after a format upgrade)."""
    from .lovelace_dashboard import build_dashboard_from_coordinator, dashboard_to_yaml

    _DASHBOARD_FORMAT_VERSION = 5

    try:
        marker_store = _store(
            hass, version=1, key=f"{DOMAIN}_dashboard_marker_{entry.entry_id}"
        )
        marker = await marker_store.async_load()
        config_horizon = coordinator._horizon
        config_dt = coordinator.dt
        if (
            marker
            and marker.get("written_at")
            and marker.get("format_version", 1) >= _DASHBOARD_FORMAT_VERSION
            and marker.get("horizon") == config_horizon
            and marker.get("dt") == config_dt
        ):
            return marker.get("path") if marker.get("path") else None

        base_dir = hass.config.path("dashboards")
        target = os.path.join(base_dir, DEFAULT_DASHBOARD_FILENAME)
        if not marker and os.path.exists(target):
            await marker_store.async_save(
                {
                    "written_at": datetime.now(tz=timezone.utc).isoformat(),
                    "path": target,
                    "format_version": _DASHBOARD_FORMAT_VERSION,
                    "horizon": config_horizon,
                    "dt": config_dt,
                }
            )
            return target

        dashboard = build_dashboard_from_coordinator(coordinator)
        yaml_text = await hass.async_add_executor_job(dashboard_to_yaml, dashboard)

        def _write() -> None:
            os.makedirs(base_dir, exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(yaml_text)

        await hass.async_add_executor_job(_write)

        await marker_store.async_save(
            {
                "written_at": datetime.now(tz=timezone.utc).isoformat(),
                "path": target,
                "format_version": _DASHBOARD_FORMAT_VERSION,
                "horizon": config_horizon,
                "dt": config_dt,
            }
        )

        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant dashboard available",
                "message": (
                    f"Heating Assistant wrote a starter Lovelace dashboard to "
                    f"`{target}`. To add it to the sidebar, open "
                    "**Settings → Dashboards → Add Dashboard → Show YAML "
                    "editor**, paste the file contents, and save. Re-run "
                    "`heating_assistant.regenerate_dashboard` after editing "
                    "rooms to refresh the file."
                ),
                "notification_id": f"{DOMAIN}_dashboard_first_install",
            },
            blocking=False,
        )
        return target
    except Exception:
        _LOGGER.debug(
            "Heating Assistant: auto-write of starter dashboard skipped",
            exc_info=True,
        )
        return None


async def async_auto_write_industrial_dashboard(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HeatingAssistantCoordinator,
) -> Optional[str]:
    """Write the industrial dashboard YAML as an additional dashboard."""
    from .lovelace_dashboard import (
        DASHBOARD_VARIANT_INDUSTRIAL,
        build_dashboard_variant_from_coordinator,
        dashboard_to_yaml,
    )

    _DASHBOARD_FORMAT_VERSION = 1

    try:
        marker_store = _store(
            hass,
            version=1,
            key=f"{DOMAIN}_industrial_dashboard_marker_{entry.entry_id}",
        )
        marker = await marker_store.async_load()
        config_horizon = coordinator._horizon
        config_dt = coordinator.dt
        if (
            marker
            and marker.get("written_at")
            and marker.get("format_version", 1) >= _DASHBOARD_FORMAT_VERSION
            and marker.get("horizon") == config_horizon
            and marker.get("dt") == config_dt
        ):
            return marker.get("path") if marker.get("path") else None

        base_dir = hass.config.path("dashboards")
        target = os.path.join(base_dir, DEFAULT_INDUSTRIAL_DASHBOARD_FILENAME)
        if not marker and os.path.exists(target):
            await marker_store.async_save(
                {
                    "written_at": datetime.now(tz=timezone.utc).isoformat(),
                    "path": target,
                    "format_version": _DASHBOARD_FORMAT_VERSION,
                    "horizon": config_horizon,
                    "dt": config_dt,
                }
            )
            return target

        dashboard = build_dashboard_variant_from_coordinator(
            coordinator,
            variant=DASHBOARD_VARIANT_INDUSTRIAL,
        )
        yaml_text = await hass.async_add_executor_job(dashboard_to_yaml, dashboard)

        def _write() -> None:
            os.makedirs(base_dir, exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(yaml_text)

        await hass.async_add_executor_job(_write)

        await marker_store.async_save(
            {
                "written_at": datetime.now(tz=timezone.utc).isoformat(),
                "path": target,
                "format_version": _DASHBOARD_FORMAT_VERSION,
                "horizon": config_horizon,
                "dt": config_dt,
            }
        )
        return target
    except Exception:
        _LOGGER.debug(
            "Heating Assistant: auto-write of industrial dashboard skipped",
            exc_info=True,
        )
        return None


async def async_try_register_lovelace_dashboard(
    hass: HomeAssistant,
    yaml_path: str,
    *,
    url_path: str = DASHBOARD_URL_PATH,
    title: str = "Heating Assistant",
    icon: str = "mdi:home-thermometer",
) -> None:
    """Best-effort registration of the YAML file as a Lovelace dashboard."""
    try:
        from homeassistant.components.lovelace.dashboard import LovelaceYAML

        lovelace_data = hass.data.get("lovelace")
        if lovelace_data is None:
            return

        dashboards = getattr(lovelace_data, "dashboards", None)
        if dashboards is None and isinstance(lovelace_data, dict):
            dashboards = lovelace_data.get("dashboards")
        if dashboards is None:
            return
        if url_path in dashboards:
            return

        rel_filename = os.path.relpath(yaml_path, hass.config.path())
        config = {
            "mode": "yaml",
            "icon": icon,
            "title": title,
            "filename": rel_filename,
            "url_path": url_path,
            "show_in_sidebar": True,
            "require_admin": False,
        }
        dashboards[url_path] = LovelaceYAML(hass, url_path, config)

        try:
            from homeassistant.components import frontend

            frontend.async_register_built_in_panel(
                hass,
                component_name="lovelace",
                sidebar_title=config["title"],
                sidebar_icon=config["icon"],
                frontend_url_path=url_path,
                config={"mode": "yaml"},
                require_admin=False,
                update=False,
            )
        except Exception:
            _LOGGER.debug(
                "Heating Assistant: sidebar panel registration skipped",
                exc_info=True,
            )
    except Exception:
        _LOGGER.debug(
            "Heating Assistant: Lovelace dashboard auto-registration skipped",
            exc_info=True,
        )

# Backward-compatible aliases for tests and commented setup_entry hooks.
_async_auto_write_default_dashboard = async_auto_write_default_dashboard
_async_auto_write_industrial_dashboard = async_auto_write_industrial_dashboard
_async_try_register_lovelace_dashboard = async_try_register_lovelace_dashboard
