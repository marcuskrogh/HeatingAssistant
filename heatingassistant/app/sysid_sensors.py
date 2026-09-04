"""Panel sensor payloads for system identification."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from heatingassistant.app.sysid_common import _dt, _iso_time
from heatingassistant.engine.model_diagnostics import (
    build_identification_warnings,
    compute_model_fit_metrics,
    validate_parameters,
)
from heatingassistant.engine.parameter_lifecycle import estimated_params_snapshot

_LOGGER = logging.getLogger("heatingassistant.app.sysid_services")


def _iso_series(entries: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in list(entries or []):
        if not isinstance(entry, Mapping):
            continue
        stamp = entry.get("time")
        if stamp is None:
            continue
        if not isinstance(stamp, str):
            stamp = _iso_time(stamp)
        if stamp is None:
            continue
        out.append({"time": stamp, "value": entry.get("value")})
    return out


def _formatted_sysid_simulation(room_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    formatted: list[dict[str, Any]] = []
    for entry in list(room_data.get("simulation", []) or []):
        if not isinstance(entry, Mapping):
            continue
        sim_entry: dict[str, Any] = {
            "time": _iso_time(entry.get("time")),
            "measured": entry.get("measured"),
            "predicted": entry.get("predicted"),
            "cov_upper": entry.get("cov_upper"),
            "cov_lower": entry.get("cov_lower"),
        }
        if entry.get("predicted_wall") is not None:
            sim_entry["predicted_wall"] = entry.get("predicted_wall")
            sim_entry["wall_cov_upper"] = entry.get("wall_cov_upper")
            sim_entry["wall_cov_lower"] = entry.get("wall_cov_lower")
        formatted.append(sim_entry)
    return formatted


def sysid_sensor_attrs(runtime: Any, room_name: str) -> dict[str, Any]:
    room_data = dict(getattr(runtime, "sysid_results", {}).get(room_name, {}) or {})
    horizon_steps = room_data.get("horizon_steps", 0)
    horizon_hours = round(float(horizon_steps) * _dt(runtime) / 3600.0, 2) if horizon_steps else None
    return {
        "simulation": _formatted_sysid_simulation(room_data),
        "thermal_mass": room_data.get("thermal_mass"),
        "r_external": room_data.get("r_external"),
        "internal_gain": room_data.get("internal_gain"),
        "ua_open": room_data.get("ua_open"),
        "solar_scale": room_data.get("solar_scale"),
        "c_air_fraction": room_data.get("c_air_fraction"),
        "r_aw_fraction": room_data.get("r_aw_fraction"),
        "t_wall_initial": room_data.get("t_wall_initial"),
        "t_wall_initial_source": room_data.get("t_wall_initial_source"),
        "estimated_inter_room_r": room_data.get("estimated_inter_room_r"),
        "heater_scales": room_data.get("heater_scales"),
        "sigma_w": room_data.get("sigma_w"),
        "sigma_v": room_data.get("sigma_v"),
        "rmse": room_data.get("rmse"),
        "mae": room_data.get("mae"),
        "horizon_hours": horizon_hours,
        "window_start": room_data.get("window_start"),
        "window_end": room_data.get("window_end"),
        "heating_power": _iso_series(room_data.get("heating_power")),
        "outdoor_temp": _iso_series(room_data.get("outdoor_temp")),
        "solar_gain": _iso_series(room_data.get("solar_gain")),
    }


def open_loop_sensor_attrs(runtime: Any, room_name: str) -> dict[str, Any]:
    room_data = dict(getattr(runtime, "open_loop_results", {}).get(room_name, {}) or {})
    formatted = []
    for entry in list(room_data.get("simulation", []) or []):
        if not isinstance(entry, Mapping):
            continue
        ol_entry: dict[str, Any] = {
            "time": _iso_time(entry.get("time")),
            "measured": entry.get("measured"),
            "predicted": entry.get("predicted"),
        }
        if entry.get("predicted_wall") is not None:
            ol_entry["predicted_wall"] = entry.get("predicted_wall")
        formatted.append(ol_entry)
    attrs = {
        "open_loop_rmse": room_data.get("rmse"),
        "open_loop_mae": room_data.get("mae"),
        "rmse_by_horizon": room_data.get("rmse_by_horizon"),
        "simulation": formatted,
        "t_wall_initial": room_data.get("t_wall_initial"),
        "t_wall_initial_source": room_data.get("t_wall_initial_source"),
        "heating_power": _iso_series(room_data.get("heating_power")),
        "outdoor_temp": _iso_series(room_data.get("outdoor_temp")),
        "solar_gain": _iso_series(room_data.get("solar_gain")),
    }
    if "error" in room_data:
        attrs["error"] = room_data["error"]
    return attrs


def _room_index(runtime: Any, room_name: str) -> int | None:
    room_names = list(getattr(getattr(runtime.control_engine, "model", None), "room_names", []) or [])
    try:
        return room_names.index(room_name)
    except ValueError:
        return None


def closed_loop_fit_for_room(
    runtime: Any, room_name: str
) -> tuple[float | None, float | None, int | None]:
    """Return ``(r_squared, rmse, n_samples)`` from aligned history ``y`` / ``y_pred``."""

    room_idx = _room_index(runtime, room_name)
    if room_idx is None:
        return None, None, None

    predictions: list[float] = []
    measurements: list[float] = []
    for record in list(getattr(runtime, "_history_buffer", []) or []):
        if not isinstance(record, Mapping):
            continue
        y = record.get("y", []) or []
        y_pred = record.get("y_pred")
        if y_pred is None:
            continue
        if room_idx < len(y) and room_idx < len(y_pred):
            predictions.append(float(y_pred[room_idx]))
            measurements.append(float(y[room_idx]))

    if len(predictions) < 2:
        return None, None, len(predictions)

    try:
        metrics = compute_model_fit_metrics(predictions, measurements, room_name)
    except Exception:
        _LOGGER.exception("Failed to compute closed-loop fit for %s", room_name)
        return None, None, len(predictions)
    return float(metrics.r_squared), float(metrics.rmse), int(metrics.n_samples)


def _room_estimation_provenance(
    snapshot: Mapping[str, Any] | None, room_name: str
) -> tuple[bool, str | None]:
    if not snapshot:
        return False, None
    rooms = snapshot.get("rooms") if isinstance(snapshot, Mapping) else None
    room_snap = rooms.get(room_name) if isinstance(rooms, Mapping) else None
    if not isinstance(room_snap, Mapping):
        return False, None
    if "is_estimated" in room_snap:
        is_estimated = bool(room_snap.get("is_estimated"))
        estimated_at = room_snap.get("estimated_at") if is_estimated else None
        return is_estimated, estimated_at if isinstance(estimated_at, str) else None
    return False, None


def model_fit_quality_sensor(runtime: Any, room_name: str) -> tuple[Any, dict[str, Any]]:
    """Return ``(state, attributes)`` for ``*_model_fit_quality``."""

    room_idx = _room_index(runtime, room_name)
    if room_idx is None:
        return "unknown", {"error": "Unknown room", "n_samples": 0}

    predictions: list[float] = []
    measurements: list[float] = []
    for record in list(getattr(runtime, "_history_buffer", []) or []):
        if not isinstance(record, Mapping):
            continue
        y = record.get("y", []) or []
        y_pred = record.get("y_pred")
        if y_pred is None:
            continue
        if room_idx < len(y) and room_idx < len(y_pred):
            predictions.append(float(y_pred[room_idx]))
            measurements.append(float(y[room_idx]))

    if len(predictions) < 2:
        return "unknown", {"error": "Insufficient data", "n_samples": len(predictions)}

    try:
        metrics = compute_model_fit_metrics(predictions, measurements, room_name)
    except Exception as exc:
        _LOGGER.exception("Failed to compute model fit quality for %s", room_name)
        return "unknown", {"error": str(exc), "n_samples": len(predictions)}

    return round(float(metrics.r_squared), 4), {
        "r_squared": round(float(metrics.r_squared), 4),
        "rmse": round(float(metrics.rmse), 3),
        "mae": round(float(metrics.mae), 3),
        "bias": round(float(metrics.bias), 3),
        "max_error": round(float(metrics.max_error), 2),
        "residual_std": round(float(metrics.residual_std), 3),
        "residual_autocorr_lag1": (
            round(float(metrics.residual_autocorr_lag1), 3)
            if metrics.residual_autocorr_lag1 is not None
            else None
        ),
        "n_samples": int(metrics.n_samples),
        "room": room_name,
    }


def parameter_confidence_sensor(runtime: Any, room_name: str) -> tuple[Any, dict[str, Any]]:
    """Return ``(state, attributes)`` for ``*_parameter_confidence``."""

    rooms = getattr(getattr(runtime.control_engine, "model", None), "rooms", {}) or {}
    room = rooms.get(room_name)
    if room is None:
        return "unknown", {"error": "Unknown room"}

    try:
        fit_r2, fit_rmse, n_samples = closed_loop_fit_for_room(runtime, room_name)
        validation = validate_parameters(
            room_name,
            float(getattr(room, "thermal_mass", 0.0) or 0.0),
            float(getattr(room, "r_external", 0.0) or 0.0),
            model_r_squared=fit_r2,
            model_rmse=fit_rmse,
        )
        score = 0.0
        if validation.mass_valid:
            score += 33.3
        if validation.r_external_valid:
            score += 33.3
        if validation.time_constant_valid:
            score += 33.4

        snapshot = estimated_params_snapshot(runtime.options)
        is_estimated, estimated_at = _room_estimation_provenance(snapshot, room_name)
        ol_rmse = (
            getattr(runtime, "open_loop_results", {}).get(room_name, {}) or {}
        ).get("rmse")
        card_warnings = build_identification_warnings(
            room_name,
            validation,
            model_r_squared=fit_r2,
            model_rmse=fit_rmse,
            open_loop_rmse=float(ol_rmse) if ol_rmse is not None else None,
            n_samples=n_samples,
        )
        return round(score, 1), {
            "thermal_mass": validation.thermal_mass,
            "r_external": validation.r_external,
            "internal_gain": round(float(getattr(room, "internal_gain", 0.0) or 0.0), 2),
            "ua_open": round(float(getattr(room, "ua_open", 0.0) or 0.0), 3),
            "time_constant_hours": round(float(validation.time_constant_hours), 2),
            "mass_valid": validation.mass_valid,
            "r_external_valid": validation.r_external_valid,
            "time_constant_valid": validation.time_constant_valid,
            "warnings": list(validation.warnings),
            "card_warnings": [
                {"code": w.code, "message": w.message, "severity": w.severity}
                for w in card_warnings
            ],
            "is_estimated": is_estimated,
            "estimated_at": estimated_at,
            "room": room_name,
        }
    except Exception as exc:
        _LOGGER.exception("Failed to validate parameters for %s", room_name)
        return "unknown", {"error": str(exc)}
