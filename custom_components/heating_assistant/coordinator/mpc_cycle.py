"""MPC update-cycle helpers: disturbances, controller compute, history recording."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from homeassistant.helpers.update_coordinator import UpdateFailed

from ..const import DOMAIN, MPC_MODE_NONLINEAR
from ..ground_temp import ground_temperature
from ..solar_model import horizontal_irradiance
from . import runtime_state
from .types import ControlTrajectory

if TYPE_CHECKING:
    from .core import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)

# Number of consecutive coordinator cycles with no valid outdoor temperature
# before an UpdateFailed is raised (only applies when no prior reading was ever
# obtained — i.e., the entity was never reachable since startup).
_OUTDOOR_TEMP_MAX_STARTUP_FAILURES = 3


def _notify_nmpc_failure(coordinator: HeatingAssistantCoordinator, error: BaseException) -> None:
    """Raise a visible HA notification when non-linear MPC fails mid-cycle."""
    if getattr(coordinator, "_mpc_mode", None) != MPC_MODE_NONLINEAR:
        return
    message = (
        "Heating Assistant's non-linear MPC failed to compute this cycle. "
        "No automatic switch to linear mode was made. Open Heating Assistant "
        "controller tuning and switch MPC mode to linear, then check the logs "
        f"for details. Last error: {error}"
    )
    try:
        result = coordinator.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant non-linear MPC failed",
                "message": message,
                "notification_id": f"{DOMAIN}_nmpc_failure",
            },
            blocking=False,
        )
        if asyncio.iscoroutine(result):
            coordinator.hass.async_create_task(result)
    except Exception:  # pragma: no cover - notification must never mask cycle state
        _LOGGER.debug(
            "Heating Assistant: failed to create non-linear MPC failure notification",
            exc_info=True,
        )


@dataclass
class DisturbanceContext:
    """Weather, solar, and price inputs gathered for one MPC cycle."""

    outdoor_forecast: Optional[List[float]]
    cloud_cover_now: Optional[float]
    cloud_forecast: Optional[List[float]]
    ghi_now: Optional[float]
    ghi_forecast: Optional[List[float]]
    wind_forecast: Optional[List[float]]
    now: datetime


def read_measurements(coordinator: HeatingAssistantCoordinator) -> Set[str]:
    """Read room temperatures from HA sensors; return rooms awaiting first reading."""
    coordinator.measured_temperatures = {}
    for room_name, entity_ids in coordinator._temp_sensors.items():
        readings: List[float] = []
        for entity_id in entity_ids:
            state = coordinator.hass.states.get(entity_id)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    readings.append(float(state.state))
                except (ValueError, TypeError):
                    _LOGGER.warning(
                        "Cannot parse temperature from entity %s: %r",
                        entity_id,
                        state.state,
                    )
        if readings:
            averaged = sum(readings) / len(readings)
            coordinator.model.rooms[room_name].temperature = averaged
            coordinator.measured_temperatures[room_name] = averaged

    coordinator._rooms_ever_measured.update(coordinator.measured_temperatures.keys())

    rooms_not_ready = {
        name for name in coordinator._temp_sensors
        if name not in coordinator._rooms_ever_measured
    }
    if rooms_not_ready:
        _LOGGER.debug(
            "Waiting for first valid temperature reading for: %s",
            ", ".join(sorted(rooms_not_ready)),
        )
    return rooms_not_ready


async def resolve_outdoor_temperature(
    coordinator: HeatingAssistantCoordinator,
) -> tuple[Optional[float], Optional[Dict[str, Any]]]:
    """Resolve outdoor temperature for MPC, or return an early-cycle payload.

    Returns ``(outdoor_temp, None)`` when MPC can proceed, or
    ``(None, early_return_dict)`` when the cycle should exit before MPC.
    Raises :class:`UpdateFailed` when startup grace is exhausted.
    """
    outdoor_temp = coordinator._read_outdoor_temp()
    coordinator.outdoor_temp = outdoor_temp

    if outdoor_temp is not None:
        coordinator._last_valid_outdoor_temp = outdoor_temp
        coordinator._outdoor_temp_startup_failures = 0
        return outdoor_temp, None

    if coordinator._last_valid_outdoor_temp is not None:
        _LOGGER.debug(
            "Outdoor temperature entity unavailable; "
            "persisting last known value %.1f °C for MPC",
            coordinator._last_valid_outdoor_temp,
        )
        return coordinator._last_valid_outdoor_temp, None

    coordinator._outdoor_temp_startup_failures += 1
    if coordinator._outdoor_temp_startup_failures >= _OUTDOOR_TEMP_MAX_STARTUP_FAILURES:
        raise UpdateFailed(
            "Outdoor temperature is unavailable: neither "
            f"outdoor_temp_entity {coordinator._outdoor_entity!r} nor "
            f"weather_entity {coordinator._weather_entity!r} has produced "
            f"a valid reading after "
            f"{coordinator._outdoor_temp_startup_failures} coordinator cycles. "
            "Check that the configured entities exist and are "
            "reporting a numeric state."
        )

    _LOGGER.debug(
        "Outdoor temperature unavailable — startup cycle %d/%d; "
        "skipping MPC until entity becomes ready",
        coordinator._outdoor_temp_startup_failures,
        _OUTDOOR_TEMP_MAX_STARTUP_FAILURES,
    )
    if not coordinator.actions:
        coordinator.actions = {src.name: 0.0 for src in coordinator.heat_sources}
    await coordinator._ensure_runtime_state_loaded()
    coordinator._publish_current_solar_gains()
    return None, {
        "temperatures": dict(coordinator.model.temperatures),
        "outdoor_temp": None,
        "actions": dict(coordinator.actions),
        "solar_gains": dict(coordinator.solar_gains),
        "predictions": [],
        "heat_flows": {},
        "outdoor_forecast": [],
        "solar_forecast": [],
        "heating_schedule": [],
    }


async def gather_disturbances(
    coordinator: HeatingAssistantCoordinator,
) -> DisturbanceContext:
    """Read price/weather forecasts and configure controller disturbance inputs."""
    coordinator.price_forecast = await coordinator._async_read_price_forecast(
        coordinator.now_utc
    )
    outdoor_forecast = await coordinator._async_read_weather_forecast()
    await coordinator._ensure_runtime_state_loaded()
    if coordinator._pending_ekf_state is not None:
        runtime_state.restore_pending_ekf_state(coordinator)

    cloud_cover_raw = coordinator._read_cloud_cover_now()
    cloud_cover_now = coordinator._smooth_cloud_cover(cloud_cover_raw)
    cloud_forecast = await coordinator._async_read_cloud_forecast(
        cloud_cover_now=cloud_cover_now
    )
    if cloud_cover_now is None and cloud_forecast:
        cloud_cover_now = max(0.0, min(1.0, float(cloud_forecast[0])))
        coordinator._cloud_cover_filtered = cloud_cover_now

    ghi_now, ghi_forecast = coordinator._read_ghi(coordinator.now_utc)

    wind_speed_now = coordinator._read_wind_speed_now()
    coordinator.wind_speed = wind_speed_now
    if hasattr(coordinator.controller, "set_wind_speed"):
        coordinator.controller.set_wind_speed(wind_speed_now)
    wind_forecast = await coordinator._async_read_wind_forecast(wind_speed_now)

    if hasattr(coordinator.controller, "set_cloud_cover"):
        coordinator.controller.set_cloud_cover(cloud_cover_now)
    coordinator.model.set_cloud_cover(cloud_cover_now)
    if hasattr(coordinator.controller, "set_room_process_noise_covariance_scales"):
        q_scale = {
            room_name: (
                coordinator._window_open_q_inflation
                if coordinator.is_window_override_active(room_name)
                else 1.0
            )
            for room_name in coordinator.model.room_names
        }
        coordinator.controller.set_room_process_noise_covariance_scales(q_scale)

    ground_temp_now = ground_temperature(coordinator.now_utc)
    coordinator.ground_temp = ground_temp_now
    if hasattr(coordinator.controller, "set_ground_temp"):
        coordinator.controller.set_ground_temp(ground_temp_now)

    return DisturbanceContext(
        outdoor_forecast=outdoor_forecast,
        cloud_cover_now=cloud_cover_now,
        cloud_forecast=cloud_forecast,
        ghi_now=ghi_now,
        ghi_forecast=ghi_forecast,
        wind_forecast=wind_forecast,
        now=coordinator.now_utc,
    )


def publish_cycle_solar_gains(
    coordinator: HeatingAssistantCoordinator,
    ctx: DisturbanceContext,
) -> None:
    """Compute current solar gains and persist smoothed cloud cover."""
    now = ctx.now
    cloud_cover_now = ctx.cloud_cover_now
    ghi_now = ctx.ghi_now
    ghi_forecast = ctx.ghi_forecast

    coordinator.cloud_cover = cloud_cover_now
    coordinator.ghi_now = ghi_now
    coordinator.ghi_forecast = list(ghi_forecast or [])
    coordinator.solar_source = (
        "forecast" if ghi_forecast or ghi_now is not None else "analytical"
    )
    try:
        coordinator.ghi_now_effective = horizontal_irradiance(
            now,
            coordinator._latitude,
            coordinator._longitude,
            cloud_cover=cloud_cover_now,
            ghi=ghi_now,
        )
    except Exception:  # pragma: no cover - defensive; never block a cycle
        coordinator.ghi_now_effective = ghi_now
    coordinator.solar_gains = {
        name: coordinator._room_solar_gain(name, now, cloud_cover_now, ghi_now)
        for name in coordinator.model.room_names
    }
    coordinator._save_runtime_state()


def _clear_forecasts_after_compute_failure(
    coordinator: HeatingAssistantCoordinator,
    outdoor_temp: float,
    ctx: DisturbanceContext,
) -> None:
    """Clear prediction products after an MPC compute failure/timeout."""
    if not coordinator.actions:
        coordinator.actions = {
            src.name: 0.0 for src in coordinator.heat_sources
        }
    if ctx.outdoor_forecast:
        coordinator.outdoor_forecast = list(
            ctx.outdoor_forecast[: coordinator._horizon]
        )
        if len(coordinator.outdoor_forecast) < coordinator._horizon:
            coordinator.outdoor_forecast.extend(
                [outdoor_temp]
                * (coordinator._horizon - len(coordinator.outdoor_forecast))
            )
    else:
        coordinator.outdoor_forecast = [outdoor_temp] * coordinator._horizon
    coordinator.predictions = []
    coordinator.linearised_predictions = []
    coordinator.heating_schedule = []
    coordinator.solar_forecast = []
    coordinator.filtered_temperatures = {}
    coordinator.estimated_internal_gains = {}


def handle_controller_compute_timeout(
    coordinator: HeatingAssistantCoordinator,
    outdoor_temp: float,
    ctx: DisturbanceContext,
    *,
    timeout_s: float,
) -> None:
    """Surface an NMPC/MPC wall-clock timeout without blocking the event loop forever."""
    error = TimeoutError(
        f"MPC compute exceeded {timeout_s:.0f}s wall-clock budget "
        f"(mode={getattr(coordinator, '_mpc_mode', 'unknown')}, "
        f"backend={getattr(coordinator, '_nonlinear_backend', None)})"
    )
    _LOGGER.warning(
        "Heating Assistant: %s; keeping last controls and clearing forecasts",
        error,
    )
    _notify_nmpc_failure(coordinator, error)
    _clear_forecasts_after_compute_failure(coordinator, outdoor_temp, ctx)


def run_controller_compute(
    coordinator: HeatingAssistantCoordinator,
    outdoor_temp: float,
    control_traj: Optional[ControlTrajectory],
    rooms_not_ready: Set[str],
    ctx: DisturbanceContext,
) -> Optional[List[float]]:
    """Run the MPC controller and mirror outputs onto the coordinator."""
    disabled_src_names = {
        src.name
        for src in coordinator.heat_sources
        if not coordinator.is_room_enabled(src.room)
        or coordinator.is_window_override_active(src.room)
        or src.room in rooms_not_ready
    }
    if coordinator._system_enabled:
        experiment_clamps = coordinator._build_experiment_clamps(coordinator.now_utc)
        coordinator._relax_experiment_comfort(control_traj)
    else:
        experiment_clamps = {}
        coordinator._experiment_active_rooms = set()
        coordinator._experiment_horizon_steps = {}

    now = ctx.now
    kalman_innovation: Optional[List[float]]
    try:
        coordinator.actions = coordinator.controller.compute(
            outdoor_temp=outdoor_temp,
            solar_gains=coordinator.solar_gains,
            now=now,
            outdoor_forecast=ctx.outdoor_forecast,
            cloud_forecast=ctx.cloud_forecast,
            cloud_cover_now=ctx.cloud_cover_now,
            ghi_forecast=ctx.ghi_forecast,
            ghi_now=ctx.ghi_now,
            wind_forecast=ctx.wind_forecast,
            disabled_sources=disabled_src_names or None,
            control_trajectory=control_traj,
            price_forecast=coordinator.price_forecast,
            input_clamps=experiment_clamps or None,
            run_optimization=coordinator._system_enabled,
        )
        coordinator._mpc_shadow_actions = dict(coordinator.controller.mpc_actions)
        coordinator.predictions = coordinator.controller.predictions
        coordinator.linearised_predictions = coordinator.controller.linearised_predictions
        coordinator.outdoor_forecast = coordinator.controller.outdoor_forecast
        coordinator.solar_forecast = coordinator.controller.solar_forecast
        coordinator.heating_schedule = coordinator.controller.heating_schedule
        coordinator.filtered_temperatures = dict(
            coordinator.controller.filtered_temperatures
        )
        coordinator.wall_temperatures = dict(
            getattr(coordinator.controller, "wall_temperatures", {}) or {}
        )
        coordinator.wall_temperature_stds = dict(
            getattr(coordinator.controller, "wall_temperature_stds", {}) or {}
        )
        coordinator.estimated_internal_gains = dict(
            coordinator.controller.estimated_internal_gains
        )
        for _r in rooms_not_ready:
            coordinator.filtered_temperatures.pop(_r, None)
        coordinator.price_forecast = (
            coordinator.controller.price_forecast or coordinator.price_forecast
        )
        kalman_innovation = coordinator.controller.last_innovation
        if coordinator._system_enabled:
            coordinator._last_mpc_run_ts = now.timestamp()
    except Exception as exc:
        _LOGGER.warning(
            "Failed to compute MPC actions; clearing forecast data so "
            "dashboards show a visible gap at the failure point",
            exc_info=True,
        )
        _notify_nmpc_failure(coordinator, exc)
        _clear_forecasts_after_compute_failure(coordinator, outdoor_temp, ctx)
        kalman_innovation = None
    return kalman_innovation


def finalize_actions(
    coordinator: HeatingAssistantCoordinator, outdoor_temp: float
) -> None:
    """Apply window overrides and read back delivered power when stopped."""
    for src in coordinator.heat_sources:
        if coordinator.is_window_override_active(src.room):
            coordinator.actions[src.name] = 0.0
    if not coordinator._system_enabled:
        coordinator.actions = coordinator._read_delivered_actions(outdoor_temp)
    coordinator.heat_flows = coordinator.model.compute_heat_flows(outdoor_temp)


async def record_observation_history(
    coordinator: HeatingAssistantCoordinator,
    outdoor_temp: float,
    ctx: DisturbanceContext,
    kalman_innovation: Optional[List[float]],
) -> None:
    """Append a rate-limited observation to the rolling history buffer."""
    now = ctx.now
    _last_history_ts = (
        float(coordinator._history_buffer[-1].get("timestamp", 0.0))
        if coordinator._history_buffer else 0.0
    )
    if now.timestamp() - _last_history_ts < 0.5 * coordinator._update_interval_s:
        return

    y_pred_aligned = None
    if len(coordinator._history_buffer) > 0:
        y_pred_aligned = coordinator._history_buffer[-1].get("y_pred_for_next")

    if coordinator.predictions and len(coordinator.predictions) > 0:
        first_pred = coordinator.predictions[0]
        y_pred_for_next = [
            first_pred.get(name, coordinator.model.rooms[name].temperature)
            for name in coordinator.model.room_names
        ]
    else:
        y_pred_for_next = [
            coordinator.model.rooms[name].temperature
            for name in coordinator.model.room_names
        ]

    coordinator._history_buffer.append({
        "y": [
            coordinator.measured_temperatures.get(
                name, coordinator.model.rooms[name].temperature
            )
            for name in coordinator.model.room_names
        ],
        "y_pred": y_pred_aligned,
        "y_pred_for_next": y_pred_for_next,
        "u": [
            coordinator.actions.get(src.name, 0.0)
            for src in coordinator.heat_sources
        ],
        "d_outdoor": outdoor_temp,
        "d_solar": dict(coordinator.solar_gains),
        "cloud_cover": ctx.cloud_cover_now,
        "ghi_now": ctx.ghi_now,
        "solar_source": coordinator.solar_source,
        "timestamp": now.timestamp(),
        "kalman_innovation": kalman_innovation,
        "window_open": {
            name: coordinator.is_window_override_active(name)
            for name in coordinator.model.room_names
        },
    })

    if coordinator.id_history_store is not None:
        await coordinator.id_history_store.async_append(
            coordinator._history_buffer[-1]
        )
        await coordinator.id_history_store.async_purge_old()


def build_cycle_result(
    coordinator: HeatingAssistantCoordinator, outdoor_temp: float
) -> Dict[str, Any]:
    """Return the coordinator data snapshot for this cycle."""
    return {
        "temperatures": dict(coordinator.model.temperatures),
        "outdoor_temp": outdoor_temp,
        "actions": dict(coordinator.actions),
        "solar_gains": dict(coordinator.solar_gains),
        "predictions": list(coordinator.predictions),
        "heat_flows": dict(coordinator.heat_flows),
        "outdoor_forecast": list(coordinator.outdoor_forecast),
        "solar_forecast": list(coordinator.solar_forecast),
        "heating_schedule": list(coordinator.heating_schedule),
    }
