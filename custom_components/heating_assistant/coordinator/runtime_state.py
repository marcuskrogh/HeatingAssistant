"""Cloud-cover EMA smoothing and EKF runtime-state persistence."""

from __future__ import annotations

import logging
import time as _time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

import numpy as np

from .. import weather as _weather
from ..const import (
    CLOUD_SMOOTHING_TAU_S,
    DEFAULT_COMFORT_OFFSET,
    DEFAULT_SETPOINT,
    RUNTIME_STATE_SAVE_DELAY_S,
)
from ..experiments import excitation_fraction
from ..schedule import control_params_at

if TYPE_CHECKING:
    from .core import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)

# Maximum number of EKF prediction steps to propagate when filling a stop/start
# gap.  Beyond this limit the persisted state is still restored (better than a
# cold start) but no forward propagation is attempted.  At a 15-minute update
# interval this caps the gap at 4 days.
_MAX_EKF_GAP_STEPS = 384


async def ensure_runtime_state_loaded(coordinator: HeatingAssistantCoordinator) -> None:
    """Load persisted smoothed runtime weather state once, on first use.

    Seeds the cloud-cover EMA from the value saved by the previous session
    so the first cycle after a restart attenuates the solar model instead of
    emitting an unattenuated clear-sky spike.
    """
    if coordinator._runtime_state_loaded:
        return
    coordinator._runtime_state_loaded = True
    try:
        data = await coordinator._runtime_store.async_load()
    except Exception:  # pragma: no cover - defensive; never block a cycle
        data = None
    if isinstance(data, dict):
        if coordinator._cloud_cover_filtered is None:
            cc = data.get("cloud_cover_filtered")
            if cc is not None:
                try:
                    coordinator._cloud_cover_filtered = max(0.0, min(1.0, float(cc)))
                except (TypeError, ValueError):
                    pass
        ekf_x_hat = data.get("ekf_x_hat")
        ekf_P = data.get("ekf_P")
        ekf_save_ts = data.get("ekf_save_ts")
        ekf_u_prev = data.get("ekf_u_prev")
        ekf_d_prev = data.get("ekf_d_prev")
        if ekf_x_hat is not None and ekf_P is not None:
            try:
                x_hat = np.array(ekf_x_hat, dtype=float)
                P_mat = np.array(ekf_P, dtype=float)
                save_ts = float(ekf_save_ts) if ekf_save_ts is not None else 0.0
                u_prev = (
                    np.array(ekf_u_prev, dtype=float)
                    if ekf_u_prev is not None else None
                )
                d_prev = (
                    np.array(ekf_d_prev, dtype=float)
                    if ekf_d_prev is not None else None
                )
                coordinator._pending_ekf_state = (x_hat, P_mat, save_ts, u_prev, d_prev)
            except (TypeError, ValueError):
                pass


def smooth_cloud_cover(
    coordinator: HeatingAssistantCoordinator, cc_obs: Optional[float]
) -> Optional[float]:
    """Exponentially smooth the live cloud-cover observation."""
    coordinator._cloud_cover_filtered = _weather.smooth_cloud_cover_step(
        coordinator._cloud_cover_filtered,
        cc_obs,
        coordinator.dt,
        CLOUD_SMOOTHING_TAU_S,
    )
    return coordinator._cloud_cover_filtered


def save_runtime_state(coordinator: HeatingAssistantCoordinator) -> None:
    """Persist cloud cover and EKF state (throttled) so they survive restarts."""
    try:
        def _snapshot() -> Dict[str, Any]:
            payload: Dict[str, Any] = {
                "cloud_cover_filtered": coordinator._cloud_cover_filtered,
            }
            try:
                x_hat, P = coordinator.controller.ekf_state
                u_prev, d_prev = coordinator.controller.ekf_inputs
                payload["ekf_x_hat"] = x_hat.tolist()
                payload["ekf_P"] = P.tolist()
                payload["ekf_save_ts"] = _time.time()
                payload["ekf_u_prev"] = u_prev.tolist()
                payload["ekf_d_prev"] = d_prev.tolist()
            except Exception:  # pragma: no cover - defensive
                pass
            return payload

        coordinator._runtime_store.async_delay_save(
            _snapshot, RUNTIME_STATE_SAVE_DELAY_S
        )
    except Exception:  # pragma: no cover - defensive; never block a cycle
        pass


def restore_pending_ekf_state(coordinator: HeatingAssistantCoordinator) -> None:
    """Restore persisted EKF state and propagate through any stop/start gap."""
    if coordinator._pending_ekf_state is None:
        return
    x_hat, P, save_ts, u_prev, d_prev = coordinator._pending_ekf_state
    coordinator._pending_ekf_state = None
    if coordinator.controller.restore_ekf_state(x_hat, P):
        propagate_ekf_gap(coordinator, save_ts, u_prev, d_prev)
    else:
        _LOGGER.debug(
            "Persisted EKF state has incompatible dimensions — "
            "starting from cold initial conditions"
        )


def propagate_ekf_gap(
    coordinator: HeatingAssistantCoordinator,
    save_ts: float,
    u_prev: Optional[np.ndarray],
    d_prev: Optional[np.ndarray],
) -> None:
    """Propagate the EKF forward from save_ts to now without measurement updates."""
    if save_ts <= 0.0:
        return
    now_ts = _time.time()
    gap_s = now_ts - save_ts
    if gap_s <= 0.0:
        return
    dt = float(coordinator.dt)
    n_gap = min(round(gap_s / dt), _MAX_EKF_GAP_STEPS)
    if n_gap <= 0:
        return

    u_default = (
        np.asarray(u_prev, dtype=float)
        if u_prev is not None
        else np.zeros(len(coordinator.heat_sources), dtype=float)
    )
    d_arr = (
        np.asarray(d_prev, dtype=float)
        if d_prev is not None
        else np.zeros(system_nd(coordinator), dtype=float)
    )

    try:
        u_seq = build_gap_u_sequence(coordinator, save_ts, n_gap, dt, u_default)
        coordinator.controller.propagate_ekf(u_seq, d_arr)
        _LOGGER.debug(
            "EKF gap propagation: %.0f s gap → %d steps (max %d)",
            gap_s, n_gap, _MAX_EKF_GAP_STEPS,
        )
    except Exception:  # pragma: no cover - defensive; never block startup
        _LOGGER.debug(
            "EKF gap propagation failed; state restored without propagation",
            exc_info=True,
        )


def system_nd(coordinator: HeatingAssistantCoordinator) -> int:
    """Return the disturbance dimension of the current controller model."""
    try:
        return coordinator.controller._mpc._model.nd
    except Exception:
        return 1 + 2 * len(coordinator.model.room_names)


def build_gap_u_sequence(
    coordinator: HeatingAssistantCoordinator,
    start_ts: float,
    n_steps: int,
    dt: float,
    u_default: np.ndarray,
) -> np.ndarray:
    """Build per-step actuator commands for EKF gap propagation."""
    from ..dashboard import slugify as _slugify

    n_sources = len(coordinator.heat_sources)
    u_seq = np.empty((n_steps, n_sources), dtype=float)
    u_seq[:] = u_default

    manager = getattr(coordinator, "experiment_manager", None)

    for k in range(n_steps):
        t_k = start_ts + k * dt

        for i, src in enumerate(coordinator.heat_sources):
            room_slug = _slugify(src.room)
            can_cool = bool(getattr(src, "can_cool", False))

            if manager is not None:
                exp = manager.active_for_room(room_slug, t_k)
                if exp is not None:
                    u_seq[k, i] = excitation_fraction(exp, t_k, can_cool=can_cool)
                    continue

            room_name = src.room
            schedule = coordinator._room_schedule.get(room_name)
            if schedule is not None and coordinator._schedule_enabled.get(room_name, True):
                base_sp = coordinator._base_setpoint.get(room_name, DEFAULT_SETPOINT)
                default_off = coordinator._room_comfort_offset.get(
                    room_name, DEFAULT_COMFORT_OFFSET
                )
                t_local = datetime.fromtimestamp(t_k)
                params = control_params_at(schedule, base_sp, t_local, default_off)
                if params is None:
                    u_seq[k, i] = 0.0

    return u_seq
