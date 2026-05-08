"""
Heating Assistant integration – entry-point.

Set-up flow
-----------
1. ``async_setup``        – register YAML-sourced room/source configuration.
2. ``async_setup_entry``  – create the coordinator and set up platforms.
3. ``async_unload_entry`` – clean up on removal.

YAML configuration example
---------------------------
``configuration.yaml``::

    heating_assistant:
      rooms:
        - name: living_room
          thermal_mass: 8000000     # J/K
          r_external: 0.04          # K/W
          setpoint: 21.0
          temp_sensor: sensor.living_room_temperature
          # Multiple sensors can be used for averaging:
          # temp_sensors:
          #   - sensor.living_room_temp_north
          #   - sensor.living_room_temp_south
          connections:
            - room: kitchen
              r_value: 0.2
          windows:
            - area: 3.0             # m²
              orientation: 180      # South
              tilt: 90
        - name: kitchen
          thermal_mass: 4000000
          r_external: 0.06
          setpoint: 20.0
          connections:
            - room: living_room
              r_value: 0.2

      heat_sources:
        - name: living_room_heater
          type: electric_heater
          room: living_room
          max_power: 2000           # W
          heater_entity: switch.living_room_heater
        - name: heat_pump
          type: heat_pump
          room: living_room
          max_power: 5000           # W thermal
          cop_rated: 3.5
          cop_temp_ref: 7.0
          min_power: 1000           # W thermal (minimum operating power)
          heater_entity: climate.heat_pump

      outdoor_temp_entity: sensor.outdoor_temperature
      weather_entity: weather.forecast_home       # optional: weather forecast
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_CONNECTIONS,
    CONF_CONNECTED_ROOM,
    CONF_CONSTRAINT_OFFSET,
    CONF_ENERGY_WEIGHT,
    CONF_HEAT_SOURCES,
    CONF_HORIZON,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_R_EXTERNAL,
    CONF_R_VALUE,
    CONF_ROOM_NAME,
    CONF_ROOMS,
    CONF_SETPOINT,
    CONF_SMOOTHING_WEIGHT,
    CONF_SOURCE_COOLING_COP,
    CONF_SOURCE_COOLING_EFFICIENCY,
    CONF_SOURCE_COP_RATED,
    CONF_SOURCE_COP_TEMP_REF,
    CONF_SOURCE_EFFICIENCY,
    CONF_SOURCE_HEATER_ENTITY,
    CONF_SOURCE_HEATING_EFFICIENCY,
    CONF_SOURCE_MAX_POWER,
    CONF_SOURCE_MAX_TEMP_OFFSET,
    CONF_SOURCE_MIN_POWER,
    CONF_SOURCE_NAME,
    CONF_SOURCE_ROOM,
    CONF_SOURCE_TURN_OFF_DEADBAND,
    CONF_SOURCE_TYPE,
    CONF_TEMP_SENSOR,
    CONF_TEMP_SENSORS,
    CONF_TERMINAL_WEIGHT,
    CONF_THERMAL_MASS,
    CONF_UPDATE_INTERVAL,
    CONF_WINDOWS,
    CONF_WINDOW_AREA,
    CONF_WINDOW_ORIENTATION,
    CONF_WINDOW_TILT,
    DEFAULT_COOLING_COP,
    DEFAULT_COOLING_EFFICIENCY,
    DEFAULT_COP_RATED,
    DEFAULT_COP_TEMP_REF,
    DEFAULT_CONSTRAINT_OFFSET,
    DEFAULT_EFFICIENCY,
    DEFAULT_ENERGY_WEIGHT,
    DEFAULT_HEATING_EFFICIENCY,
    DEFAULT_HORIZON,
    DEFAULT_MAX_TEMP_OFFSET,
    DEFAULT_MIN_POWER,
    DEFAULT_R_EXTERNAL,
    DEFAULT_SETPOINT,
    DEFAULT_SMOOTHING_WEIGHT,
    DEFAULT_TERMINAL_WEIGHT,
    DEFAULT_THERMAL_MASS,
    DEFAULT_TURN_OFF_DEADBAND,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WINDOW_TILT,
    DOMAIN,
    SOURCE_TYPE_ELECTRIC,
    SOURCE_TYPE_HEAT_PUMP,
    UPDATE_INTERVAL,
)
from .coordinator import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["climate", "sensor", "button"]

SERVICE_SIMULATE_THERMAL_RESPONSE = "simulate_thermal_response"
SERVICE_ESTIMATE_PARAMETERS = "estimate_parameters"
SERVICE_ESTIMATE_PARAMETERS_ML = "estimate_parameters_ml"

# ---------------------------------------------------------------------------
# YAML schema
# ---------------------------------------------------------------------------

_WINDOW_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_WINDOW_AREA): vol.Coerce(float),
        vol.Required(CONF_WINDOW_ORIENTATION): vol.Coerce(float),
        vol.Optional(CONF_WINDOW_TILT, default=DEFAULT_WINDOW_TILT): vol.Coerce(float),
    }
)

_CONNECTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CONNECTED_ROOM): str,
        vol.Required(CONF_R_VALUE): vol.Coerce(float),
    }
)

_ROOM_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ROOM_NAME): str,
        vol.Optional(CONF_THERMAL_MASS, default=DEFAULT_THERMAL_MASS): vol.Coerce(float),
        vol.Optional(CONF_R_EXTERNAL, default=DEFAULT_R_EXTERNAL): vol.Coerce(float),
        vol.Optional(CONF_SETPOINT, default=DEFAULT_SETPOINT): vol.Coerce(float),
        vol.Optional(CONF_TEMP_SENSOR): str,
        vol.Optional(CONF_TEMP_SENSORS, default=[]): [str],
        vol.Optional(CONF_CONNECTIONS, default=[]): [_CONNECTION_SCHEMA],
        vol.Optional(CONF_WINDOWS, default=[]): [_WINDOW_SCHEMA],
    }
)

_SOURCE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SOURCE_NAME): str,
        vol.Required(CONF_SOURCE_TYPE): vol.In(
            [SOURCE_TYPE_ELECTRIC, SOURCE_TYPE_HEAT_PUMP]
        ),
        vol.Required(CONF_SOURCE_ROOM): str,
        vol.Required(CONF_SOURCE_MAX_POWER): vol.Coerce(float),
        vol.Optional(CONF_SOURCE_EFFICIENCY, default=DEFAULT_EFFICIENCY): vol.Coerce(float),
        vol.Optional(CONF_SOURCE_COP_RATED, default=DEFAULT_COP_RATED): vol.Coerce(float),
        vol.Optional(CONF_SOURCE_COP_TEMP_REF, default=DEFAULT_COP_TEMP_REF): vol.Coerce(float),
        vol.Optional(CONF_SOURCE_MIN_POWER, default=DEFAULT_MIN_POWER): vol.Coerce(float),
        vol.Optional(CONF_SOURCE_MAX_TEMP_OFFSET, default=DEFAULT_MAX_TEMP_OFFSET): vol.Coerce(float),
        vol.Optional(CONF_SOURCE_TURN_OFF_DEADBAND, default=DEFAULT_TURN_OFF_DEADBAND): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_SOURCE_COOLING_COP, default=DEFAULT_COOLING_COP): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_SOURCE_COOLING_EFFICIENCY, default=DEFAULT_COOLING_EFFICIENCY): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=1.0)
        ),
        vol.Optional(CONF_SOURCE_HEATING_EFFICIENCY, default=DEFAULT_HEATING_EFFICIENCY): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=1.0)
        ),
        vol.Optional(CONF_SOURCE_HEATER_ENTITY): str,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_ROOMS, default=[]): [_ROOM_SCHEMA],
                vol.Optional(CONF_HEAT_SOURCES, default=[]): [_SOURCE_SCHEMA],
                vol.Optional(CONF_OUTDOOR_TEMP_ENTITY): str,
                vol.Optional(CONF_WEATHER_ENTITY): str,
                vol.Optional(CONF_LATITUDE): vol.Coerce(float),
                vol.Optional(CONF_LONGITUDE): vol.Coerce(float),
                vol.Optional(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): vol.All(
                    vol.Coerce(int), vol.Range(min=60, max=3600)
                ),
                vol.Optional(CONF_HORIZON, default=DEFAULT_HORIZON): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=24)
                ),
                vol.Optional(
                    CONF_ENERGY_WEIGHT, default=DEFAULT_ENERGY_WEIGHT
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
                vol.Optional(
                    CONF_SMOOTHING_WEIGHT, default=DEFAULT_SMOOTHING_WEIGHT
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
                vol.Optional(
                    CONF_CONSTRAINT_OFFSET, default=DEFAULT_CONSTRAINT_OFFSET
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
                vol.Optional(
                    CONF_TERMINAL_WEIGHT, default=DEFAULT_TERMINAL_WEIGHT
                ): vol.All(vol.Coerce(float), vol.Range(min=1.0)),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def async_setup(hass: HomeAssistant, config: Dict[str, Any]) -> bool:
    """Import YAML configuration into the integration's data store."""
    hass.data.setdefault(DOMAIN, {})

    if DOMAIN in config:
        hass.data[DOMAIN]["yaml_config"] = config[DOMAIN]

    return True


def _merge_yaml_into_entry_data(
    entry_data: Dict[str, Any],
    yaml_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge YAML-backed defaults into config-entry data."""
    merged = dict(entry_data)

    # Prefer YAML room/source definitions when the entry has placeholders
    # (missing or empty lists). This keeps room-based entities available.
    if not merged.get(CONF_ROOMS):
        merged[CONF_ROOMS] = yaml_cfg.get(CONF_ROOMS, [])
    if not merged.get(CONF_HEAT_SOURCES):
        merged[CONF_HEAT_SOURCES] = yaml_cfg.get(CONF_HEAT_SOURCES, [])

    # Use YAML outdoor entity if the config entry value is empty/missing.
    # setdefault would not overwrite the empty-string default from the
    # config-flow, so we need an explicit check here.
    if not merged.get(CONF_OUTDOOR_TEMP_ENTITY):
        merged[CONF_OUTDOOR_TEMP_ENTITY] = yaml_cfg.get(
            CONF_OUTDOOR_TEMP_ENTITY, ""
        )
    if not merged.get(CONF_WEATHER_ENTITY):
        merged[CONF_WEATHER_ENTITY] = yaml_cfg.get(
            CONF_WEATHER_ENTITY, ""
        )
    merged.setdefault(CONF_UPDATE_INTERVAL, yaml_cfg.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
    merged.setdefault(CONF_HORIZON, yaml_cfg.get(CONF_HORIZON, DEFAULT_HORIZON))
    if CONF_LATITUDE not in merged and CONF_LATITUDE in yaml_cfg:
        merged[CONF_LATITUDE] = yaml_cfg[CONF_LATITUDE]
    if CONF_LONGITUDE not in merged and CONF_LONGITUDE in yaml_cfg:
        merged[CONF_LONGITUDE] = yaml_cfg[CONF_LONGITUDE]
    merged.setdefault(
        CONF_ENERGY_WEIGHT,
        yaml_cfg.get(CONF_ENERGY_WEIGHT, DEFAULT_ENERGY_WEIGHT),
    )
    merged.setdefault(
        CONF_SMOOTHING_WEIGHT,
        yaml_cfg.get(CONF_SMOOTHING_WEIGHT, DEFAULT_SMOOTHING_WEIGHT),
    )
    merged.setdefault(
        CONF_CONSTRAINT_OFFSET,
        yaml_cfg.get(CONF_CONSTRAINT_OFFSET, DEFAULT_CONSTRAINT_OFFSET),
    )
    merged.setdefault(
        CONF_TERMINAL_WEIGHT,
        yaml_cfg.get(CONF_TERMINAL_WEIGHT, DEFAULT_TERMINAL_WEIGHT),
    )
    return merged


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Heating Assistant from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Merge YAML config (if present) into the entry data so the coordinator
    # can see room and heat-source definitions regardless of how they were set.
    entry_data = dict(entry.data)
    yaml_cfg = hass.data[DOMAIN].get("yaml_config", {})
    if yaml_cfg:
        entry_data = _merge_yaml_into_entry_data(entry_data, yaml_cfg)

    # Build a temporary entry-like object with merged data for the coordinator
    merged_entry = _MergedEntry(entry, entry_data)

    coordinator = HeatingAssistantCoordinator(hass, merged_entry)  # type: ignore[arg-type]
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Register services (only once for the domain)
    if not hass.services.has_service(DOMAIN, SERVICE_SIMULATE_THERMAL_RESPONSE):
        _register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


class _MergedEntry:
    """Thin wrapper that presents merged entry data to the coordinator."""

    def __init__(self, entry: ConfigEntry, data: Dict[str, Any]) -> None:
        self._entry = entry
        self.data = data
        self.options = entry.options
        self.entry_id = entry.entry_id
        self.title = entry.title


# ---------------------------------------------------------------------------
# Service helpers
# ---------------------------------------------------------------------------

def _get_coordinator(hass: HomeAssistant) -> HeatingAssistantCoordinator:
    """Return the first available coordinator instance."""
    for entry_id, obj in hass.data.get(DOMAIN, {}).items():
        if isinstance(obj, HeatingAssistantCoordinator):
            return obj
    raise ValueError("No Heating Assistant coordinator found")


def _register_services(hass: HomeAssistant) -> None:
    """Register domain services for setup assistance."""

    async def handle_simulate(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass)
        result = coordinator.simulate_thermal_response(
            room_name=call.data["room_name"],
            initial_temp=call.data["initial_temp"],
            outdoor_temp=call.data["outdoor_temp"],
            heating_power=call.data["heating_power"],
            duration_hours=call.data["duration_hours"],
        )
        # Fire an event so automations or the UI can consume the result
        hass.bus.async_fire(
            f"{DOMAIN}_simulation_result",
            result,
        )
        # Also create a persistent notification for easy access
        if "error" in result:
            message = f"**Error:** {result['error']}"
        else:
            traj = result.get("trajectory", [])
            traj_lines = "\n".join(
                f"  {p['time_minutes']} min → {p['temperature']} °C"
                for p in traj[-10:]  # last 10 data points
            )
            message = (
                f"**Room:** {call.data['room_name']}\n"
                f"**Heating power:** {call.data['heating_power']} W\n"
                f"**Outdoor temp:** {call.data['outdoor_temp']} °C\n"
                f"**Start temp:** {call.data['initial_temp']} °C\n\n"
                f"**Final temperature:** {result['final_temperature']} °C\n"
                f"**Steady-state temperature:** {result['steady_state_temperature']} °C\n"
                f"**Time constant:** {result['time_constant_hours']} hours\n\n"
                f"**Trajectory (last points):**\n{traj_lines}"
            )
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant – Thermal Response Simulation",
                "message": message,
                "notification_id": f"{DOMAIN}_simulation",
            },
            blocking=False,
        )

    async def handle_estimate(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass)
        result = coordinator.estimate_parameters(
            room_name=call.data["room_name"],
            heating_power=call.data["heating_power"],
            outdoor_temp=call.data["outdoor_temp"],
            initial_temp=call.data["initial_temp"],
            final_temp=call.data["final_temp"],
            duration_seconds=call.data["duration_seconds"],
        )
        hass.bus.async_fire(
            f"{DOMAIN}_estimation_result",
            result,
        )
        if "error" in result:
            message = f"**Error:** {result['error']}"
        else:
            message = (
                f"**Room:** {call.data['room_name']}\n\n"
                f"**Estimated thermal_mass:** {result['estimated_thermal_mass']:,.0f} J/K\n"
                f"**Estimated r_external:** {result['estimated_r_external']} K/W\n\n"
                f"**Current thermal_mass:** {result['current_thermal_mass']:,.0f} J/K\n"
                f"**Current r_external:** {result['current_r_external']} K/W\n\n"
                f"**Notes:** {result['notes']}"
            )
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant – Parameter Estimation",
                "message": message,
                "notification_id": f"{DOMAIN}_estimation",
            },
            blocking=False,
        )

    async def handle_estimate_ml(call: ServiceCall) -> None:
        """Run ML parameter estimation using the Kalman filter log-likelihood."""
        coordinator = _get_coordinator(hass)
        apply_params: bool = call.data.get("apply_parameters", True)
        result = await coordinator.async_estimate_parameters_ml(
            apply_params=apply_params,
        )
        hass.bus.async_fire(
            f"{DOMAIN}_ml_estimation_result",
            {
                k: v
                for k, v in result.items()
                if isinstance(v, (str, int, float, bool, type(None)))
            },
        )
        if not result["success"]:
            message = (
                f"**Status:** {result['message']}\n\n"
                f"**Steps in buffer:** {result['n_steps']}"
            )
        else:
            lines = []
            for room, params in result["estimated_params"].items():
                curr = result["current_params"][room]
                lines.append(
                    f"**{room}**\n"
                    f"  thermal\\_mass: {params['thermal_mass']:,.0f} J/K "
                    f"(was {curr['thermal_mass']:,.0f})\n"
                    f"  r\\_external: {params['r_external']:.5f} K/W "
                    f"(was {curr['r_external']:.5f})"
                )
            applied_str = (
                "Parameters applied to live model."
                if apply_params
                else "Dry run – parameters NOT applied."
            )
            ll_str = (
                f"{result['log_likelihood']:.1f}"
                if result["log_likelihood"] is not None
                else "n/a"
            )
            message = (
                f"**{applied_str}**\n\n"
                + "\n\n".join(lines)
                + f"\n\n**Data steps used:** {result['n_steps']}\n"
                f"**Log-likelihood:** {ll_str}\n"
                f"**Status:** {result['message']}"
            )
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant – ML Parameter Estimation",
                "message": message,
                "notification_id": f"{DOMAIN}_ml_estimation",
            },
            blocking=False,
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SIMULATE_THERMAL_RESPONSE,
        handle_simulate,
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("initial_temp"): vol.Coerce(float),
                vol.Required("outdoor_temp"): vol.Coerce(float),
                vol.Required("heating_power"): vol.Coerce(float),
                vol.Required("duration_hours"): vol.Coerce(float),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_ESTIMATE_PARAMETERS,
        handle_estimate,
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("heating_power"): vol.Coerce(float),
                vol.Required("outdoor_temp"): vol.Coerce(float),
                vol.Required("initial_temp"): vol.Coerce(float),
                vol.Required("final_temp"): vol.Coerce(float),
                vol.Required("duration_seconds"): vol.Coerce(float),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_ESTIMATE_PARAMETERS_ML,
        handle_estimate_ml,
        schema=vol.Schema(
            {
                vol.Optional("apply_parameters", default=True): cv.boolean,
            }
        ),
    )

    async def handle_run_open_loop_simulation(call: ServiceCall) -> None:
        """Run open-loop simulation diagnostic and report RMSE per room."""
        from .model_diagnostics import compute_open_loop_predictions

        coordinator = _get_coordinator(hass)
        room_name_filter = call.data.get("room_name")
        segment_length = int(call.data.get("segment_length", 30))

        history = list(coordinator.history_buffer)
        system = coordinator.controller._system
        room_names = coordinator.model.room_names
        n_rooms = len(room_names)

        try:
            result = await hass.async_add_executor_job(
                compute_open_loop_predictions,
                history,
                system,
                room_names,
                n_rooms,
                float(UPDATE_INTERVAL),
                segment_length,
            )

            if "error" in result:
                message = f"**Error:** {result['error']}"
            else:
                lines = [
                    f"**Segment length:** {result['segment_length']} steps "
                    f"({result['segment_length']} min)\n"
                    f"**Segments evaluated:** {result['n_segments']}\n"
                ]
                per_room = result.get("per_room", {})
                rooms_to_report = (
                    [room_name_filter]
                    if room_name_filter and room_name_filter in per_room
                    else list(per_room.keys())
                )
                for room in rooms_to_report:
                    data = per_room.get(room, {})
                    rmse = data.get("rmse")
                    mae = data.get("mae")
                    if rmse is None:
                        lines.append(f"**{room}:** no data")
                        continue
                    quality = (
                        "excellent" if rmse < 0.2
                        else "acceptable" if rmse < 0.5
                        else "poor – re-run parameter estimation"
                    )
                    lines.append(
                        f"**{room}**\n"
                        f"  Open-loop RMSE: {rmse:.3f} °C ({quality})\n"
                        f"  Open-loop MAE:  {mae:.3f} °C"
                    )

                message = "\n\n".join(lines)

        except Exception as exc:
            _LOGGER.error("Open-loop simulation failed: %s", exc, exc_info=True)
            message = f"**Error:** {exc}"

        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant – Open-Loop Simulation",
                "message": message,
                "notification_id": f"{DOMAIN}_open_loop_sim",
            },
            blocking=False,
        )

    async def handle_analyze_model_fit(call: ServiceCall) -> None:
        """Analyze model fit quality for all or specific room."""
        from .model_diagnostics import generate_model_fit_report

        coordinator = _get_coordinator(hass)
        room_name_filter = call.data.get("room_name")

        # Build room parameters dict
        room_params = {}
        setpoints = {}
        for name, room in coordinator.model.rooms.items():
            room_params[name] = (room.thermal_mass, room.r_external)
            setpoints[name] = room.setpoint

        try:
            report = generate_model_fit_report(
                coordinator.history_buffer,
                coordinator.model.room_names,
                room_params,
                setpoints,
            )

            # Filter to specific room if requested
            if room_name_filter and room_name_filter in report.get("rooms", {}):
                filtered_rooms = {room_name_filter: report["rooms"][room_name_filter]}
                report["rooms"] = filtered_rooms

            # Format message
            if "error" in report:
                message = f"**Error:** {report['error']}"
            else:
                lines = [f"**Data steps analyzed:** {report['n_steps']}\n"]
                for room, data in report["rooms"].items():
                    if "error" in data:
                        lines.append(f"**{room}:** {data['error']}")
                        continue

                    fit = data.get("fit_metrics", {})
                    lines.append(
                        f"**{room}**\n"
                        f"  R² score: {fit.get('r_squared', 'n/a')}\n"
                        f"  RMSE: {fit.get('rmse', 'n/a')} °C\n"
                        f"  MAE: {fit.get('mae', 'n/a')} °C\n"
                        f"  Bias: {fit.get('bias', 'n/a')} °C\n"
                        f"  Max error: {fit.get('max_error', 'n/a')} °C\n"
                        f"  Autocorr (lag-1): {fit.get('residual_autocorr_lag1', 'n/a')}"
                    )

                message = "\n\n".join(lines)

            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Heating Assistant – Model Fit Analysis",
                    "message": message,
                    "notification_id": f"{DOMAIN}_model_fit",
                },
                blocking=False,
            )
        except Exception as exc:
            _LOGGER.error("Model fit analysis failed: %s", exc, exc_info=True)
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Heating Assistant – Model Fit Analysis",
                    "message": f"**Error:** {exc}",
                    "notification_id": f"{DOMAIN}_model_fit",
                },
                blocking=False,
            )

    async def handle_validate_parameters(call: ServiceCall) -> None:
        """Validate thermal parameters for all or specific room."""
        from .model_diagnostics import validate_parameters

        coordinator = _get_coordinator(hass)
        room_name_filter = call.data.get("room_name")

        rooms_to_check = (
            [room_name_filter]
            if room_name_filter and room_name_filter in coordinator.model.rooms
            else coordinator.model.room_names
        )

        lines = []
        for room_name in rooms_to_check:
            room = coordinator.model.rooms[room_name]
            try:
                validation = validate_parameters(
                    room_name, room.thermal_mass, room.r_external
                )

                status = "✓ Valid" if all([
                    validation.mass_valid,
                    validation.r_external_valid,
                    validation.time_constant_valid,
                ]) else "⚠ Warnings"

                lines.append(
                    f"**{room_name}** – {status}\n"
                    f"  Thermal mass: {validation.thermal_mass:,.0f} J/K "
                    f"({'✓' if validation.mass_valid else '⚠'})\n"
                    f"  R external: {validation.r_external:.5f} K/W "
                    f"({'✓' if validation.r_external_valid else '⚠'})\n"
                    f"  Time constant: {validation.time_constant_hours:.2f} hours "
                    f"({'✓' if validation.time_constant_valid else '⚠'})"
                )

                if validation.warnings:
                    lines.append("  **Warnings:**")
                    for warning in validation.warnings:
                        lines.append(f"    • {warning}")

            except Exception as exc:
                _LOGGER.error("Parameter validation failed for %s: %s", room_name, exc)
                lines.append(f"**{room_name}:** Error – {exc}")

        message = "\n\n".join(lines)

        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant – Parameter Validation",
                "message": message,
                "notification_id": f"{DOMAIN}_param_validation",
            },
            blocking=False,
        )

    async def handle_controller_performance(call: ServiceCall) -> None:
        """Generate controller performance report for all or specific room."""
        from .model_diagnostics import compute_controller_performance

        coordinator = _get_coordinator(hass)
        room_name_filter = call.data.get("room_name")

        rooms_to_check = (
            [room_name_filter]
            if room_name_filter and room_name_filter in coordinator.model.rooms
            else coordinator.model.room_names
        )

        lines = []
        for room_name in rooms_to_check:
            room = coordinator.model.rooms[room_name]

            # Extract temperature history for this room
            room_idx = coordinator.model.room_names.index(room_name)
            temperatures = []
            for record in coordinator.history_buffer:
                y = record.get("y", [])
                if room_idx < len(y):
                    temperatures.append(y[room_idx])

            if len(temperatures) < 2:
                lines.append(f"**{room_name}:** Insufficient data")
                continue

            try:
                perf = compute_controller_performance(
                    temperatures, room.setpoint, room_name
                )

                lines.append(
                    f"**{room_name}** (setpoint: {room.setpoint} °C)\n"
                    f"  Mean tracking error: {perf.mean_tracking_error:+.3f} °C\n"
                    f"  Tracking error std: {perf.tracking_error_std:.3f} °C\n"
                    f"  Time above setpoint: {perf.time_above_setpoint * 100:.1f}%\n"
                    f"  Time below setpoint: {perf.time_below_setpoint * 100:.1f}%\n"
                    f"  Time in deadband (±0.5°C): {perf.time_in_deadband * 100:.1f}%\n"
                    f"  Max overshoot: {perf.max_overshoot:.2f} °C\n"
                    f"  Max undershoot: {perf.max_undershoot:.2f} °C\n"
                    f"  Samples: {perf.n_samples}"
                )

            except Exception as exc:
                _LOGGER.error("Controller performance analysis failed for %s: %s", room_name, exc)
                lines.append(f"**{room_name}:** Error – {exc}")

        message = "\n\n".join(lines)

        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant – Controller Performance",
                "message": message,
                "notification_id": f"{DOMAIN}_controller_perf",
            },
            blocking=False,
        )

    hass.services.async_register(
        DOMAIN,
        "analyze_model_fit",
        handle_analyze_model_fit,
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "validate_parameters",
        handle_validate_parameters,
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "controller_performance_report",
        handle_controller_performance,
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "run_open_loop_simulation",
        handle_run_open_loop_simulation,
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
                vol.Optional("segment_length", default=30): vol.All(
                    vol.Coerce(int), vol.Range(min=5, max=120)
                ),
            }
        ),
    )
