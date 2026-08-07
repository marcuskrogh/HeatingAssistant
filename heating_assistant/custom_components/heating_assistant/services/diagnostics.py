"""Diagnostic service handlers for Heating Assistant."""

from __future__ import annotations

from functools import partial
import logging
from typing import Any, Dict

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
import homeassistant.helpers.config_validation as cv

from ..const import DOMAIN
from .context import get_coordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_COMPUTE_LOGLIK_SLICE = "compute_loglik_slice"


async def handle_analyze_model_fit(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Analyze model-fit quality for all or a specific room.

    The fit metrics (R², RMSE, MAE, bias, …) are continuously exposed by
    the per-room ``…_model_fit_quality`` sensor. This service refreshes
    those sensors and returns the full report as service-response data so
    Developer Tools → Actions shows it inline; no persistent notification
    is raised.
    """
    from ..model_diagnostics import generate_model_fit_report

    coordinator = get_coordinator(hass)
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
    except Exception as exc:
        _LOGGER.error("Model fit analysis failed: %s", exc, exc_info=True)
        return {"error": str(exc)}

    # Filter to specific room if requested
    if room_name_filter and room_name_filter in report.get("rooms", {}):
        report["rooms"] = {room_name_filter: report["rooms"][room_name_filter]}

    # Refresh the model-fit sensors so the dashboard reflects the latest data.
    coordinator.async_update_listeners()
    return report


async def handle_validate_parameters(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Validate thermal parameters for all or a specific room.

    Returns the validation result as service-response data (visible in
    Developer Tools → Actions); no persistent notification is raised.
    """
    from ..model_diagnostics import validate_parameters

    coordinator = get_coordinator(hass)
    room_name_filter = call.data.get("room_name")

    rooms_to_check = (
        [room_name_filter]
        if room_name_filter and room_name_filter in coordinator.model.rooms
        else coordinator.model.room_names
    )

    rooms: Dict[str, Any] = {}
    for room_name in rooms_to_check:
        room = coordinator.model.rooms[room_name]
        try:
            validation = validate_parameters(
                room_name, room.thermal_mass, room.r_external
            )
            rooms[room_name] = {
                "valid": all([
                    validation.mass_valid,
                    validation.r_external_valid,
                    validation.time_constant_valid,
                ]),
                "thermal_mass": validation.thermal_mass,
                "thermal_mass_valid": validation.mass_valid,
                "r_external": validation.r_external,
                "r_external_valid": validation.r_external_valid,
                "time_constant_hours": validation.time_constant_hours,
                "time_constant_valid": validation.time_constant_valid,
                "warnings": list(validation.warnings),
            }
        except Exception as exc:
            _LOGGER.error("Parameter validation failed for %s: %s", room_name, exc)
            rooms[room_name] = {"error": str(exc)}

    return {"rooms": rooms}


async def handle_controller_performance(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Generate a controller performance report for all or a specific room.

    Returns the report as service-response data (visible in Developer
    Tools → Actions); no persistent notification is raised.
    """
    from ..model_diagnostics import compute_controller_performance

    coordinator = get_coordinator(hass)
    room_name_filter = call.data.get("room_name")

    rooms_to_check = (
        [room_name_filter]
        if room_name_filter and room_name_filter in coordinator.model.rooms
        else coordinator.model.room_names
    )

    rooms: Dict[str, Any] = {}
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
            rooms[room_name] = {"error": "insufficient_data"}
            continue

        try:
            perf = compute_controller_performance(
                temperatures, room.setpoint, room_name
            )
            rooms[room_name] = {
                "setpoint": room.setpoint,
                "mean_tracking_error": perf.mean_tracking_error,
                "tracking_error_std": perf.tracking_error_std,
                "time_above_setpoint": perf.time_above_setpoint,
                "time_below_setpoint": perf.time_below_setpoint,
                "time_in_deadband": perf.time_in_deadband,
                "max_overshoot": perf.max_overshoot,
                "max_undershoot": perf.max_undershoot,
                "n_samples": perf.n_samples,
            }
        except Exception as exc:
            _LOGGER.error("Controller performance analysis failed for %s: %s", room_name, exc)
            rooms[room_name] = {"error": str(exc)}

    return {"rooms": rooms}


async def handle_compute_loglik_slice(hass: HomeAssistant, call: ServiceCall) -> dict:
    """Compute a 2-D log-likelihood slice for a room.

    The slice is stored on the coordinator and exposed via the
    per-room ``…_loglik_slice`` sensor so dashboards can visualise it
    without re-running the computation, giving Lovelace button presses
    immediate feedback through the sensor state.

    The service intentionally has no ``supports_response`` registration
    so Lovelace button cards can fire it without the frontend requiring
    ``return_response=true``.  The full grid is always available via
    ``state_attr('sensor.heating_assistant_<room>_loglik_slice',
    'log_likelihood')``.
    """
    coordinator = get_coordinator(hass)
    room_name = call.data["room_name"]
    n_grid = int(call.data.get("n_grid", 11))
    span_log = float(call.data.get("span_log", 1.0))

    result = await coordinator.async_compute_loglik_slice(
        room_name, n_grid=n_grid, span_log=span_log
    )
    if result is None:
        return {
            "room": room_name,
            "error": "history_too_short_or_unknown_room",
        }

    # The computed grid is stored on the per-room ``…_loglik_slice`` sensor
    # (and returned here for callers using ``return_response: true``), so no
    # persistent notification is raised.
    return result


def register_diagnostic_services(hass: HomeAssistant) -> None:
    """Register diagnostic domain services."""
    hass.services.async_register(
        DOMAIN,
        "analyze_model_fit",
        partial(handle_analyze_model_fit, hass),
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        "validate_parameters",
        partial(handle_validate_parameters, hass),
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        "controller_performance_report",
        partial(handle_controller_performance, hass),
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_COMPUTE_LOGLIK_SLICE,
        partial(handle_compute_loglik_slice, hass),
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Optional("n_grid", default=11): vol.All(
                    vol.Coerce(int), vol.Range(min=3, max=41)
                ),
                vol.Optional("span_log", default=1.0): vol.All(
                    vol.Coerce(float), vol.Range(min=0.1, max=4.0)
                ),
            }
        ),
    )
