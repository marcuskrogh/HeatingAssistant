"""WebSocket API for the Heating Assistant industrial panel."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Optional

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .const import (
    ALL_SOURCE_TYPES,
    CONF_COMFORT_OFFSET,
    CONF_ENERGY_PRICE_WEIGHT,
    CONF_ENERGY_WEIGHT,
    CONF_HEAT_SOURCES,
    CONF_HORIZON,
    CONF_IDENTIFICATION_HISTORY_DAYS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_MPC_MODE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_PLOT_FORECAST_HOURS,
    CONF_PLOT_HISTORY_HOURS,
    CONF_PRICE_ENTITY,
    CONF_ROOMS,
    CONF_SMOOTHING_WEIGHT,
    CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT,
    CONF_SOFT_CONSTRAINT_WEIGHT,
    CONF_SOLAR_RADIATION_ENTITY,
    CONF_TERMINAL_WEIGHT,
    CONF_TRACKING_WEIGHT,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    DEFAULT_IDENTIFICATION_HISTORY_DAYS,
    DEFAULT_PLOT_FORECAST_HOURS,
    DEFAULT_PLOT_HISTORY_HOURS,
    DEFAULT_UPDATE_INTERVAL,
    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION,
    FACADE_COLOUR_TO_ABSORPTANCE,
    FLOOR_TYPE_DEFAULTS,
    MPC_MODES,
    SOLAR_EXPOSURE_TO_APERTURE,
    SOURCE_HVAC_MODE_COOL,
    SOURCE_HVAC_MODE_HEAT,
    SOURCE_HVAC_MODE_HEAT_COOL,
)
from .naming import slugify
from .services.context import get_coordinator

_LOGGER = logging.getLogger(__name__)


def register_websocket_api(hass: HomeAssistant) -> None:
    """Register WebSocket commands for the dashboard frontend."""
    @websocket_api.websocket_command(
        {vol.Required("type"): "heating_assistant/get_schedules"}
    )
    @websocket_api.async_response
    async def ws_get_schedules(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Return current schedule data directly from the coordinator."""
        coordinator = get_coordinator(hass)
        connection.send_result(
            msg["id"],
            {"room_schedules": coordinator.serialize_room_schedules()},
        )

    websocket_api.async_register_command(hass, ws_get_schedules)

    @websocket_api.websocket_command(
        {vol.Required("type"): "heating_assistant/get_controller_config"}
    )
    @websocket_api.async_response
    async def ws_get_controller_config(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Return current controller tuning parameters directly from the coordinator."""
        try:
            c = get_coordinator(hass)
            config = c.get_controller_config_snapshot()
            _LOGGER.debug("Heating Assistant: get_controller_config -> %s", config)
            connection.send_result(msg["id"], {"config": config})
        except Exception as err:
            _LOGGER.error("Heating Assistant: get_controller_config WS failed: %s", err)
            connection.send_error(msg["id"], "config_fetch_failed", str(err))

    websocket_api.async_register_command(hass, ws_get_controller_config)

    @websocket_api.websocket_command(
        {
            vol.Required("type"): "heating_assistant/get_forecasts",
            # Optional display horizon (hours) requested by the dashboard.  When
            # absent or 0 the full controller horizon is used; when larger than
            # the controller horizon the final actuation is held flat and the
            # trajectory is simulated forward (see build_forecast_payload).
            vol.Optional("plot_forecast_hours"): vol.Coerce(float),
        }
    )
    @websocket_api.async_response
    async def ws_get_forecasts(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Return current forecast arrays directly from the coordinator.

        Forecast data is no longer stored in sensor attributes (to avoid the
        HA Recorder 16 KB size limit), so the dashboard frontend fetches it
        via this endpoint instead.
        """
        try:
            coordinator = get_coordinator(hass)
            plot_hours = msg.get("plot_forecast_hours")
            plot_steps: Optional[int] = None
            if plot_hours is not None and float(plot_hours) > 0:
                dt = coordinator.dt or DEFAULT_UPDATE_INTERVAL
                import math
                plot_steps = max(1, math.ceil(float(plot_hours) * 3600.0 / float(dt)))
            payload = coordinator.build_forecast_payload(
                plot_forecast_steps=plot_steps
            )
            connection.send_result(msg["id"], payload)
        except Exception as err:
            _LOGGER.error("Heating Assistant: get_forecasts WS failed: %s", err)
            connection.send_error(msg["id"], "forecasts_fetch_failed", str(err))

    websocket_api.async_register_command(hass, ws_get_forecasts)

    _PREVIEW_TUNING_SCHEMA = {
        vol.Required("type"): "heating_assistant/preview_tuning_forecast",
        vol.Optional("plot_forecast_hours"): vol.Coerce(float),
        vol.Optional(CONF_TRACKING_WEIGHT): vol.Coerce(float),
        vol.Optional(CONF_ENERGY_WEIGHT): vol.Coerce(float),
        vol.Optional(CONF_ENERGY_PRICE_WEIGHT): vol.Coerce(float),
        vol.Optional(CONF_SMOOTHING_WEIGHT): vol.Coerce(float),
        vol.Optional(CONF_SOFT_CONSTRAINT_WEIGHT): vol.Coerce(float),
        vol.Optional(CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT): vol.Coerce(float),
        vol.Optional(CONF_TERMINAL_WEIGHT): vol.Coerce(float),
        vol.Optional(CONF_HORIZON): vol.Coerce(int),
        vol.Optional(CONF_UPDATE_INTERVAL): vol.Coerce(int),
        vol.Optional(CONF_COMFORT_OFFSET): vol.Coerce(float),
        vol.Optional(CONF_MPC_MODE): vol.In(list(MPC_MODES)),
    }
    _PREVIEW_TUNING_KEYS = {
        CONF_TRACKING_WEIGHT, CONF_ENERGY_WEIGHT, CONF_ENERGY_PRICE_WEIGHT,
        CONF_SMOOTHING_WEIGHT, CONF_SOFT_CONSTRAINT_WEIGHT,
        CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT, CONF_TERMINAL_WEIGHT,
        CONF_HORIZON, CONF_UPDATE_INTERVAL, CONF_COMFORT_OFFSET,
        CONF_MPC_MODE,
    }

    @websocket_api.websocket_command(_PREVIEW_TUNING_SCHEMA)
    @websocket_api.async_response
    async def ws_preview_tuning_forecast(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Run a one-off MPC solve with proposed tuning and return forecast plots."""
        import math
        from datetime import datetime, timezone

        try:
            coordinator = get_coordinator(hass)
            tuning = {
                k: msg[k] for k in _PREVIEW_TUNING_KEYS if k in msg
            }
            plot_hours = msg.get("plot_forecast_hours")
            plot_steps: Optional[int] = None
            if plot_hours is not None and float(plot_hours) > 0:
                preview_dt = float(
                    tuning.get(CONF_UPDATE_INTERVAL, coordinator._update_interval_s)
                )
                plot_steps = max(
                    1, math.ceil(float(plot_hours) * 3600.0 / preview_dt)
                )

            now = getattr(coordinator, "now_utc", None) or datetime.now(
                tz=timezone.utc
            )
            cloud_cover_raw = coordinator._read_cloud_cover_now()
            cloud_cover_now = coordinator._smooth_cloud_cover(cloud_cover_raw)
            cloud_forecast = await coordinator._async_read_cloud_forecast(
                cloud_cover_now=cloud_cover_now
            )
            wind_forecast = await coordinator._async_read_wind_forecast(
                coordinator._read_wind_speed_now()
            )
            ghi_now, ghi_forecast = coordinator._read_ghi(now)
            if cloud_cover_now is None and cloud_forecast:
                cloud_cover_now = max(0.0, min(1.0, float(cloud_forecast[0])))

            weather = {
                "cloud_forecast": cloud_forecast,
                "cloud_cover_now": cloud_cover_now,
                "ghi_now": ghi_now,
                "ghi_forecast": ghi_forecast,
                "wind_forecast": wind_forecast,
            }

            payload = await hass.async_add_executor_job(
                coordinator.preview_tuning_forecast,
                tuning,
                plot_steps,
                weather,
            )
            connection.send_result(msg["id"], payload)
        except Exception as err:
            _LOGGER.error(
                "Heating Assistant: preview_tuning_forecast WS failed: %s", err
            )
            connection.send_error(
                msg["id"], "preview_tuning_failed", str(err)
            )

    websocket_api.async_register_command(hass, ws_preview_tuning_forecast)

    @websocket_api.websocket_command(
        {vol.Required("type"): "heating_assistant/get_ui_settings"}
    )
    @websocket_api.async_response
    async def ws_get_ui_settings(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Return the industrial-panel display settings (plot windows)."""
        try:
            c = get_coordinator(hass)
            connection.send_result(
                msg["id"],
                {
                    "ui_settings": {
                        CONF_PLOT_HISTORY_HOURS: float(
                            getattr(c, "_plot_history_hours", DEFAULT_PLOT_HISTORY_HOURS)
                        ),
                        CONF_PLOT_FORECAST_HOURS: float(
                            getattr(c, "_plot_forecast_hours", DEFAULT_PLOT_FORECAST_HOURS)
                        ),
                    }
                },
            )
        except Exception as err:
            _LOGGER.error("Heating Assistant: get_ui_settings WS failed: %s", err)
            connection.send_error(msg["id"], "ui_settings_fetch_failed", str(err))

    websocket_api.async_register_command(hass, ws_get_ui_settings)

    @websocket_api.websocket_command(
        {vol.Required("type"): "heating_assistant/get_model_config"}
    )
    @websocket_api.async_response
    async def ws_get_model_config(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Return the full editable model configuration for the Configuration UI.

        Reads the live config entry (options shadowing data, matching the
        coordinator's precedence) so the dashboard always edits the values that
        are actually in force.  Returns the rooms list, heat-source list, the
        environment/system entities, the display settings, and the enum choices
        used to populate dropdowns.
        """
        try:
            c = get_coordinator(hass)
            entry = hass.config_entries.async_get_entry(c._entry.entry_id)
            data = dict(entry.data) if entry else {}
            options = dict(entry.options) if entry else {}

            def _merged(key, default=None):
                if key in options:
                    return options[key]
                return data.get(key, default)

            rooms = _merged(CONF_ROOMS, []) or []
            sources = _merged(CONF_HEAT_SOURCES, []) or []

            system = {
                CONF_OUTDOOR_TEMP_ENTITY: _merged(CONF_OUTDOOR_TEMP_ENTITY) or "",
                CONF_WEATHER_ENTITY: _merged(CONF_WEATHER_ENTITY) or "",
                CONF_SOLAR_RADIATION_ENTITY: _merged(CONF_SOLAR_RADIATION_ENTITY) or "",
                CONF_PRICE_ENTITY: _merged(CONF_PRICE_ENTITY) or "",
                CONF_LATITUDE: _merged(CONF_LATITUDE, hass.config.latitude),
                CONF_LONGITUDE: _merged(CONF_LONGITUDE, hass.config.longitude),
            }

            ui = {
                CONF_PLOT_HISTORY_HOURS: float(
                    _merged(CONF_PLOT_HISTORY_HOURS, DEFAULT_PLOT_HISTORY_HOURS)
                ),
                CONF_PLOT_FORECAST_HOURS: float(
                    _merged(CONF_PLOT_FORECAST_HOURS, DEFAULT_PLOT_FORECAST_HOURS)
                ),
            }

            system_params = {
                CONF_IDENTIFICATION_HISTORY_DAYS: int(
                    _merged(CONF_IDENTIFICATION_HISTORY_DAYS, DEFAULT_IDENTIFICATION_HISTORY_DAYS)
                ),
            }

            enums = {
                "floor_types": list(FLOOR_TYPE_DEFAULTS.keys()),
                "facade_colours": list(FACADE_COLOUR_TO_ABSORPTANCE.keys()),
                "solar_exposures": list(SOLAR_EXPOSURE_TO_APERTURE.keys()),
                "envelope_tightness": list(
                    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION.keys()
                ),
                "envelope_tightness_map": dict(
                    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION
                ),
                "source_types": list(ALL_SOURCE_TYPES),
                "hvac_modes": [
                    SOURCE_HVAC_MODE_HEAT,
                    SOURCE_HVAC_MODE_COOL,
                    SOURCE_HVAC_MODE_HEAT_COOL,
                ],
            }

            connection.send_result(
                msg["id"],
                {
                    "rooms": rooms,
                    "heat_sources": sources,
                    "system": system,
                    "ui_settings": ui,
                    "system_params": system_params,
                    "enums": enums,
                },
            )
        except Exception as err:
            _LOGGER.error("Heating Assistant: get_model_config WS failed: %s", err)
            connection.send_error(msg["id"], "model_config_fetch_failed", str(err))

    websocket_api.async_register_command(hass, ws_get_model_config)

    @websocket_api.websocket_command(
        {
            vol.Required("type"): "heating_assistant/list_datasets",
            vol.Optional("room_slug"): str,
        }
    )
    @websocket_api.async_response
    async def ws_list_datasets(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Return metadata for stored identification datasets (no records).

        When *room_slug* is present only datasets for that room are returned.
        """
        try:
            coordinator = get_coordinator(hass)
            store = getattr(coordinator, "dataset_store", None)
            room_slug = msg.get("room_slug")
            datasets = store.list_meta(room_slug=room_slug) if store is not None else []
            connection.send_result(msg["id"], {"datasets": datasets})
        except Exception as err:
            _LOGGER.error("Heating Assistant: list_datasets WS failed: %s", err)
            connection.send_error(msg["id"], "datasets_fetch_failed", str(err))

    websocket_api.async_register_command(hass, ws_list_datasets)

    @websocket_api.websocket_command(
        {
            vol.Required("type"): "heating_assistant/get_dataset",
            vol.Required("dataset_id"): str,
        }
    )
    @websocket_api.async_response
    async def ws_get_dataset(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Return a single stored dataset including its snapshotted records."""
        try:
            coordinator = get_coordinator(hass)
            store = getattr(coordinator, "dataset_store", None)
            dataset = store.get(msg["dataset_id"]) if store is not None else None
            connection.send_result(msg["id"], {"dataset": dataset})
        except Exception as err:
            _LOGGER.error("Heating Assistant: get_dataset WS failed: %s", err)
            connection.send_error(msg["id"], "dataset_fetch_failed", str(err))

    websocket_api.async_register_command(hass, ws_get_dataset)

    @websocket_api.websocket_command(
        {vol.Required("type"): "heating_assistant/list_experiments"}
    )
    @websocket_api.async_response
    async def ws_list_experiments(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        """Return all scheduled / running / completed experiments."""
        try:
            coordinator = get_coordinator(hass)
            manager = getattr(coordinator, "experiment_manager", None)
            experiments = manager.to_list() if manager is not None else []
            connection.send_result(msg["id"], {"experiments": experiments})
        except Exception as err:
            _LOGGER.error("Heating Assistant: list_experiments WS failed: %s", err)
            connection.send_error(msg["id"], "experiments_fetch_failed", str(err))

    websocket_api.async_register_command(hass, ws_list_experiments)
