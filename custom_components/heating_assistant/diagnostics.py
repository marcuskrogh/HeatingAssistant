"""
Diagnostics support for the Heating Assistant integration.

Provides a full system state dump accessible via the HA diagnostics
panel (Settings → Devices & Services → Heating Assistant → Diagnostics).
This includes configuration, model state, heat flow breakdown, prediction
trajectory, and controller parameters — useful for troubleshooting and
verifying setup.
"""

from __future__ import annotations

from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import HeatingAssistantCoordinator
from .heat_sources import HeatPump


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> Dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: HeatingAssistantCoordinator = hass.data[DOMAIN][entry.entry_id]
    model = coordinator.model

    # --- Room details ---
    rooms_diag = {}
    for name, room in model.rooms.items():
        rooms_diag[name] = {
            "temperature": round(room.temperature, 2),
            "setpoint": room.setpoint,
            "thermal_mass": room.thermal_mass,
            "r_external": room.r_external,
            "time_constant_hours": round(
                model.time_constant(name) / 3600, 2
            ),
            "connections": [
                {"room": c.connected_room, "r_value": c.r_value}
                for c in room.connections
            ],
            "windows": [
                {
                    "area": w.area,
                    "orientation": w.orientation,
                    "tilt": w.tilt,
                }
                for w in room.windows
            ],
        }

    # --- Heat source details ---
    sources_diag = {}
    for src in coordinator.heat_sources:
        info: Dict[str, Any] = {
            "type": type(src).__name__,
            "room": src.room,
            "max_power": src.max_power,
            "current_power": round(src.current_power, 1),
            "control_action": coordinator.actions.get(src.name, 0.0),
        }
        if isinstance(src, HeatPump):
            info.update({
                "cop_rated": src.cop_rated,
                "cop_temp_ref": src.cop_temp_ref,
                "cop_current": round(
                    src.cop(coordinator.outdoor_temp), 2
                ),
                "min_power": src.min_power,
            })
        sources_diag[src.name] = info

    # --- Heat flows ---
    heat_flows = coordinator.heat_flows

    # --- Predictions ---
    predictions_diag = []
    for i, pred in enumerate(coordinator.predictions):
        step_data: Dict[str, Any] = {
            "step": i + 1,
            "temperatures": {
                k: round(v, 2) for k, v in pred.items()
            },
        }
        if i < len(coordinator.heating_schedule):
            step_data["heating_power"] = {
                k: round(v, 1)
                for k, v in coordinator.heating_schedule[i].items()
            }
        if i < len(coordinator.solar_forecast):
            step_data["solar_gains"] = {
                k: round(v, 1)
                for k, v in coordinator.solar_forecast[i].items()
            }
        if i < len(coordinator.outdoor_forecast):
            step_data["outdoor_temp"] = round(
                coordinator.outdoor_forecast[i], 2
            )
        predictions_diag.append(step_data)

    # --- Solar gains ---
    solar_gains = {
        k: round(v, 1) for k, v in coordinator.solar_gains.items()
    }

    # --- Steady-state analysis ---
    steady_state = {}
    for name in model.room_names:
        sources_in_room = [
            s for s in coordinator.heat_sources if s.room == name
        ]
        total_max_power = sum(s.max_power for s in sources_in_room)
        if total_max_power > 0:
            steady_state[name] = {
                "max_heating_power": total_max_power,
                "steady_state_at_minus_10": round(
                    model.steady_state_temperature(name, total_max_power, -10.0), 1
                ),
                "steady_state_at_0": round(
                    model.steady_state_temperature(name, total_max_power, 0.0), 1
                ),
                "steady_state_at_5": round(
                    model.steady_state_temperature(name, total_max_power, 5.0), 1
                ),
            }

    return {
        "outdoor_temperature": coordinator.outdoor_temp,
        "rooms": rooms_diag,
        "heat_sources": sources_diag,
        "heat_flows": heat_flows,
        "solar_gains": solar_gains,
        "predictions": predictions_diag,
        "steady_state_analysis": steady_state,
        "controller": {
            "horizon": coordinator._horizon,
            "dt": coordinator._dt,
            "latitude": coordinator._latitude,
            "longitude": coordinator._longitude,
        },
        "model_fit_diagnostics": _get_model_fit_diagnostics(coordinator),
    }


def _get_model_fit_diagnostics(coordinator: HeatingAssistantCoordinator) -> Dict[str, Any]:
    """
    Extract model fit diagnostics from the coordinator.

    Returns goodness-of-fit metrics, parameter validation, and controller
    performance for all rooms based on the accumulated history buffer.
    """
    from .model_diagnostics import (
        compute_model_fit_metrics,
        validate_parameters,
        compute_controller_performance,
    )

    if len(coordinator.history_buffer) < 2:
        return {
            "error": "Insufficient history data",
            "n_steps": len(coordinator.history_buffer),
        }

    diagnostics: Dict[str, Any] = {
        "n_steps": len(coordinator.history_buffer),
        "rooms": {},
    }

    for i, room_name in enumerate(coordinator.model.room_names):
        room = coordinator.model.rooms[room_name]
        room_diag: Dict[str, Any] = {}

        # Extract predictions and measurements
        predictions = []
        measurements = []
        for record in coordinator.history_buffer:
            y = record.get("y", [])
            y_pred = record.get("y_pred", [])
            if i < len(y) and i < len(y_pred):
                predictions.append(y_pred[i])
                measurements.append(y[i])

        # Compute model fit metrics
        if len(predictions) >= 2:
            try:
                fit_metrics = compute_model_fit_metrics(
                    predictions, measurements, room_name
                )
                room_diag["fit_metrics"] = {
                    "rmse": round(fit_metrics.rmse, 3),
                    "mae": round(fit_metrics.mae, 3),
                    "r_squared": round(fit_metrics.r_squared, 4),
                    "bias": round(fit_metrics.bias, 3),
                    "max_error": round(fit_metrics.max_error, 2),
                    "residual_std": round(fit_metrics.residual_std, 3),
                    "residual_autocorr_lag1": (
                        round(fit_metrics.residual_autocorr_lag1, 3)
                        if fit_metrics.residual_autocorr_lag1 is not None
                        else None
                    ),
                    "n_samples": fit_metrics.n_samples,
                }
            except Exception as exc:
                room_diag["fit_metrics"] = {"error": str(exc)}

        # Validate parameters
        try:
            param_validation = validate_parameters(
                room_name, room.thermal_mass, room.r_external
            )
            room_diag["parameter_validation"] = {
                "thermal_mass": param_validation.thermal_mass,
                "r_external": param_validation.r_external,
                "time_constant_hours": round(param_validation.time_constant_hours, 2),
                "mass_valid": param_validation.mass_valid,
                "r_external_valid": param_validation.r_external_valid,
                "time_constant_valid": param_validation.time_constant_valid,
                "warnings": param_validation.warnings,
            }
        except Exception as exc:
            room_diag["parameter_validation"] = {"error": str(exc)}

        # Compute controller performance
        if len(measurements) >= 2:
            try:
                controller_perf = compute_controller_performance(
                    measurements, room.setpoint, room_name
                )
                room_diag["controller_performance"] = {
                    "mean_tracking_error": round(controller_perf.mean_tracking_error, 3),
                    "tracking_error_std": round(controller_perf.tracking_error_std, 3),
                    "time_above_setpoint": round(controller_perf.time_above_setpoint, 3),
                    "time_below_setpoint": round(controller_perf.time_below_setpoint, 3),
                    "time_in_deadband": round(controller_perf.time_in_deadband, 3),
                    "max_overshoot": round(controller_perf.max_overshoot, 2),
                    "max_undershoot": round(controller_perf.max_undershoot, 2),
                    "n_samples": controller_perf.n_samples,
                }
            except Exception as exc:
                room_diag["controller_performance"] = {"error": str(exc)}

        diagnostics["rooms"][room_name] = room_diag

    return diagnostics
