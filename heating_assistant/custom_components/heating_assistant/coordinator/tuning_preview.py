"""One-off MPC preview solve for tuning-parameter what-if forecasts."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional

from ..const import CONF_COMFORT_OFFSET, CONF_HORIZON, CONF_UPDATE_INTERVAL
from ..controller.factory import ControllerBuildConfig, build_mpc_controller
from ..ground_temp import ground_temperature

if TYPE_CHECKING:
    from .core import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)


def preview_tuning_forecast(
    coordinator: HeatingAssistantCoordinator,
    tuning_overrides: Dict[str, Any],
    plot_forecast_steps: Optional[int] = None,
    weather: Optional[Dict[str, Any]] = None,
) -> dict:
    """Run a one-off MPC solve with proposed tuning parameters.

    Does not persist or apply the overrides.  Returns the same forecast
    payload shape as :meth:`build_forecast_payload` so the dashboard can
    render room-view plots for every room from a single solve.
    """
    overrides = dict(tuning_overrides or {})
    weather = dict(weather or {})

    outdoor_temp = coordinator.outdoor_temp
    if outdoor_temp is None:
        outdoor_temp = coordinator._last_valid_outdoor_temp
    if outdoor_temp is None:
        return {"error": "outdoor_temperature_unavailable"}

    preview_horizon = int(overrides.get(CONF_HORIZON, coordinator._horizon))
    preview_dt = float(overrides.get(CONF_UPDATE_INTERVAL, coordinator._update_interval_s))
    comfort_override = overrides.get(CONF_COMFORT_OFFSET)

    preview_ctrl = build_mpc_controller(
        ControllerBuildConfig.from_coordinator(coordinator, overrides=overrides)
    )

    if hasattr(coordinator, "controller"):
        try:
            x_hat, P = coordinator.controller.ekf_state
            preview_ctrl.restore_ekf_state(x_hat, P)
        except Exception:
            _LOGGER.debug(
                "preview_tuning_forecast: could not copy EKF state",
                exc_info=True,
            )

    now = getattr(coordinator, "now_utc", None) or datetime.now(tz=timezone.utc)
    now_local = now.astimezone()

    if comfort_override is not None:
        saved_offsets = dict(coordinator._room_comfort_offset)
        try:
            preview_comfort = float(comfort_override)
            for room_name in coordinator._room_comfort_offset:
                coordinator._room_comfort_offset[room_name] = preview_comfort
            preview_traj = coordinator._compute_control_trajectory(
                now_local, preview_horizon, preview_dt
            )
        finally:
            coordinator._room_comfort_offset = saved_offsets
    else:
        preview_traj = coordinator._compute_control_trajectory(
            now_local, preview_horizon, preview_dt
        )

    disabled_src_names = {
        src.name
        for src in coordinator.heat_sources
        if not coordinator.is_room_enabled(src.room)
        or coordinator.is_window_override_active(src.room)
    }
    experiment_clamps = (
        coordinator._build_experiment_clamps(now) if coordinator._system_enabled else {}
    )

    cloud_cover_now = weather.get("cloud_cover_now")
    cloud_forecast = weather.get("cloud_forecast")
    ghi_now = weather.get("ghi_now")
    ghi_forecast = weather.get("ghi_forecast")
    wind_forecast = weather.get("wind_forecast")

    if hasattr(preview_ctrl, "set_wind_speed"):
        preview_ctrl.set_wind_speed(coordinator._read_wind_speed_now())
    if hasattr(preview_ctrl, "set_cloud_cover"):
        preview_ctrl.set_cloud_cover(cloud_cover_now)
    if hasattr(preview_ctrl, "set_ground_temp"):
        preview_ctrl.set_ground_temp(ground_temperature(now))
    if hasattr(preview_ctrl, "set_room_process_noise_covariance_scales"):
        q_scale = {
            room_name: (
                coordinator._window_open_q_inflation
                if coordinator.is_window_override_active(room_name)
                else 1.0
            )
            for room_name in coordinator.model.room_names
        }
        preview_ctrl.set_room_process_noise_covariance_scales(q_scale)

    outdoor_fc = list(coordinator.outdoor_forecast or [])
    price_fc = list(coordinator.price_forecast or [])

    preview_ctrl.compute(
        outdoor_temp=outdoor_temp,
        solar_gains=coordinator.solar_gains,
        now=now,
        outdoor_forecast=outdoor_fc if outdoor_fc else None,
        cloud_forecast=cloud_forecast,
        cloud_cover_now=cloud_cover_now,
        ghi_forecast=ghi_forecast,
        ghi_now=ghi_now,
        wind_forecast=wind_forecast,
        disabled_sources=disabled_src_names or None,
        control_trajectory=preview_traj,
        price_forecast=price_fc if price_fc else None,
        input_clamps=experiment_clamps or None,
        run_optimization=True,
    )

    return coordinator.build_forecast_payload(
        plot_forecast_steps=plot_forecast_steps,
        predictions=preview_ctrl.predictions,
        linearised_predictions=preview_ctrl.linearised_predictions,
        heating_schedule=preview_ctrl.heating_schedule,
        outdoor_forecast=preview_ctrl.outdoor_forecast,
        solar_forecast=preview_ctrl.solar_forecast,
        price_forecast=preview_ctrl.price_forecast or price_fc,
        control_trajectory=preview_traj,
        step_dt=preview_dt,
    )
