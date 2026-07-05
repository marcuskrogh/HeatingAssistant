"""
Initial-state estimation for open-loop replay and EKF reconstruction.

When the user selects a simulation or identification window, the air
temperature at the window start is measured directly.  The wall / envelope
temperature is hidden and must be inferred.  Fitting ``t_wall_initial`` on the
*same* window that is being scored is circular and tends to underestimate
heater influence (the optimiser can explain early transients with a wrong wall
state instead of correcting structural parameters).

This module estimates initial conditions from a **leading calibration window**
(preceding the simulation window) using:

1. **CD-EKF propagation** (primary) — run the production filter forward over
   the leading data; the posterior wall states at the window boundary are the
   best linear-Gaussian estimate of the hidden envelope.
2. **Wall-only open-loop optimisation** (refinement / fallback) — minimise
   one-step simulation error on the leading window with structural parameters
   fixed, exactly as :meth:`KalmanMLEstimator.estimate_wall_initial_only`
   does but restricted to the calibration slice.

When no leading data is available, a short **prefix** of the simulation window
(at most 25 % of its length, capped at six hours) is used for calibration so
the scored portion is never used to tune its own initial state.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

_LOGGER = logging.getLogger(__name__)

#: Default wall-clock span [h] of leading data used to seed hidden states.
DEFAULT_LEADING_HOURS: float = 6.0

#: Maximum fraction of a simulation window used for prefix calibration when no
#: leading history exists.
_MAX_PREFIX_FRACTION: float = 0.25


from .history.records import record_d, record_u, record_window_open


def ekf_state_at_end_of_history(
    history: List[Dict[str, Any]],
    system: Any,
    room_names: List[str],
    dt: float,
    sigma_w: float = 0.1,
    sigma_v: float = 0.5,
    x0: Optional[np.ndarray] = None,
) -> Optional[np.ndarray]:
    """Propagate the production CD-EKF over ``history`` and return the final state.

  Returns ``None`` when propagation fails or ``history`` is too short.
    """
    if len(history) < 2:
        return None

    try:
        from mbc.estimation import (  # noqa: PLC0415
            ContinuousDiscreteEKF,
            ContinuousDiscreteEKFParams,
            IntegrationScheme,
        )
    except ImportError:
        _LOGGER.debug("mbc CD-EKF unavailable for initial-state estimation")
        return None

    n = len(room_names)
    n_u = int(getattr(system, "nu", 0))
    n_d = int(getattr(system, "nd", 1 + 2 * n))
    room_list = list(getattr(system, "_room_list", room_names))
    n_x = int(system.nx)
    p = np.array([])

    y0 = history[0].get("y", [])
    u_prev = record_u(history[0], n_u)
    d_prev = record_d(history[0], system, room_list, n_d)

    if x0 is None:
        init_fn = getattr(system, "initial_state_from_measurement", None)
        if callable(init_fn):
            try:
                x_curr = np.asarray(
                    init_fn(
                        np.asarray(y0[:n], dtype=float),
                        u_prev,
                        d_prev,
                        wall_seed="steady_state",
                    ),
                    dtype=float,
                )
            except TypeError:
                x_curr = np.asarray(
                    init_fn(np.asarray(y0[:n], dtype=float), u_prev, d_prev),
                    dtype=float,
                )
        else:
            x_curr = np.zeros(n_x, dtype=float)
            x_curr[: min(n, len(y0))] = np.asarray(y0[:n], dtype=float)
    else:
        x_curr = np.asarray(x0, dtype=float).copy()

    P_curr = (sigma_v ** 2) * np.eye(n_x)
    t_prev = float(history[0].get("timestamp", 0.0))

    for record in history[1:]:
        timestamp = float(record.get("timestamp", 0.0))
        dt_step = timestamp - t_prev
        if dt_step <= 0:
            continue

        y_raw = record.get("y", [])
        excluded = record_window_open(record, room_names)
        y_meas = [
            float(y_raw[i]) if i < len(y_raw) and not excluded[i] else None
            for i in range(n)
        ]
        u_curr = record_u(record, n_u)
        d_curr = record_d(record, system, room_list, n_d)

        n_steps_step = max(1, min(200, round(dt_step * 10.0 / dt)))
        prev_ts = getattr(system, "_ts", dt)
        system._ts = dt_step
        try:
            ekf_step = ContinuousDiscreteEKF(
                system,
                x_curr,
                P_curr,
                params=ContinuousDiscreteEKFParams(
                    n_steps=n_steps_step,
                    scheme=IntegrationScheme.IMPLICIT_EULER,
                ),
            )
            x_pred, P_pred = ekf_step.predict(u_prev, d_prev, p, t_prev)
        except Exception as exc:
            _LOGGER.debug("EKF predict failed during initial-state seed: %s", exc)
            system._ts = prev_ts
            return None
        finally:
            system._ts = prev_ts

        y_update = np.array(
            [
                y_meas[i] if y_meas[i] is not None else float(x_pred[i])
                for i in range(n)
            ],
            dtype=float,
        )
        mask = np.array(
            [y_meas[i] is not None and not excluded[i] for i in range(n)],
            dtype=bool,
        )
        try:
            x_curr, P_curr = ekf_step.update(y_update, u_curr, d_curr, p, mask=mask)
        except Exception as exc:
            _LOGGER.debug("EKF update failed during initial-state seed: %s", exc)
            return None

        t_prev = timestamp
        u_prev = u_curr
        d_prev = d_curr

    return x_curr


def _prefix_calibration_slice(
    simulation_history: List[Dict[str, Any]],
    dt: float,
    max_prefix_hours: float = DEFAULT_LEADING_HOURS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split simulation history into (calibration_prefix, scored_suffix)."""
    n = len(simulation_history)
    if n < 4:
        return [], list(simulation_history)

    max_prefix_steps = max(
        2,
        min(
            int(math.ceil(max_prefix_hours * 3600.0 / dt)),
            max(2, int(n * _MAX_PREFIX_FRACTION)),
            n - 2,
        ),
    )
    return (
        list(simulation_history[: max_prefix_steps + 1]),
        list(simulation_history[max_prefix_steps:]),
    )


def estimate_simulation_initial_state(
    simulation_history: List[Dict[str, Any]],
    system: Any,
    room_names: List[str],
    dt: float,
    *,
    leading_history: Optional[List[Dict[str, Any]]] = None,
    sigma_w: float = 0.1,
    sigma_v: float = 0.5,
    room_params: Optional[Dict[str, Dict[str, Any]]] = None,
    wall_optimizer: Optional[Any] = None,
    leading_hours: float = DEFAULT_LEADING_HOURS,
) -> Dict[str, Any]:
    """Estimate air and wall temperatures at the start of ``simulation_history``.

    Returns a dict with keys:

    ``t_air`` : {room_name: float}
        Measured air temperatures at the simulation-window anchor (exact).
    ``t_wall`` : {room_name: float}
        Estimated envelope temperatures at the anchor.
    ``method`` : str
        ``"ekf_leading"``, ``"opt_leading"``, ``"ekf_prefix"``, ``"opt_prefix"``,
        or ``"steady_state"`` when only a physics-based fallback is available.
    ``calibration_steps`` : int
        Number of records used to infer the wall state.
    """
    n = len(room_names)
    result_t_air: Dict[str, float] = {}
    result_t_wall: Dict[str, float] = {}
    method = "steady_state"
    calibration_steps = 0

    if not simulation_history:
        return {
            "t_air": result_t_air,
            "t_wall": result_t_wall,
            "method": method,
            "calibration_steps": 0,
        }

    y0 = simulation_history[0].get("y", [])
    for i, name in enumerate(room_names):
        if i < len(y0):
            result_t_air[name] = round(float(y0[i]), 2)

    calibration_history = list(leading_history) if leading_history else []
    scored_history = list(simulation_history)
    if not calibration_history:
        calibration_history, scored_history = _prefix_calibration_slice(
            simulation_history, dt, max_prefix_hours=leading_hours,
        )

    # ── EKF on calibration window ────────────────────────────────────────
    x_ekf: Optional[np.ndarray] = None
    if len(calibration_history) >= 2:
        calibration_steps = len(calibration_history)
        x_ekf = ekf_state_at_end_of_history(
            calibration_history,
            system,
            room_names,
            dt,
            sigma_w=sigma_w,
            sigma_v=sigma_v,
        )
        if x_ekf is not None and x_ekf.shape[0] >= 2 * n:
            for i, name in enumerate(room_names):
                result_t_wall[name] = round(float(x_ekf[n + i]), 2)
            method = "ekf_leading" if leading_history else "ekf_prefix"

    # ── Wall-only optimisation on calibration window (refine / fallback) ─
    t_wall_opt: Dict[str, float] = {}
    if wall_optimizer is not None and len(calibration_history) >= 2:
        try:
            t_wall_opt = wall_optimizer.estimate_wall_initial_only(
                calibration_history,
                room_params=room_params,
            )
        except Exception as exc:
            _LOGGER.debug("Wall-only optimisation for initial state failed: %s", exc)

    if t_wall_opt:
        if method.startswith("ekf"):
            # Blend EKF and optimisation: EKF is smoother; optimisation
            # corrects structural-parameter mismatch on the calibration slice.
            for name in room_names:
                if name in t_wall_opt and name in result_t_wall:
                    result_t_wall[name] = round(
                        0.6 * result_t_wall[name] + 0.4 * float(t_wall_opt[name]),
                        2,
                    )
                elif name in t_wall_opt:
                    result_t_wall[name] = round(float(t_wall_opt[name]), 2)
            method = method.replace("ekf", "ekf+opt", 1)
        else:
            for name, val in t_wall_opt.items():
                result_t_wall[name] = round(float(val), 2)
            method = "opt_leading" if leading_history else "opt_prefix"
            calibration_steps = len(calibration_history)

    # ── Steady-state fallback for any missing room ───────────────────────
    init_fn = getattr(system, "initial_state_from_measurement", None)
    u0 = record_u(simulation_history[0], int(getattr(system, "nu", 0)))
    d0 = record_d(
        simulation_history[0],
        system,
        list(getattr(system, "_room_list", room_names)),
        int(getattr(system, "nd", 1 + 2 * n)),
    )
    for i, name in enumerate(room_names):
        if name in result_t_wall:
            continue
        t_air = result_t_air.get(name, 20.0)
        if callable(init_fn):
            try:
                x_ss = np.asarray(
                    init_fn(
                        np.array([t_air]),
                        u0,
                        d0,
                        wall_seed="steady_state",
                    ),
                    dtype=float,
                )
                if x_ss.shape[0] > n + i:
                    result_t_wall[name] = round(float(x_ss[n + i]), 2)
                    continue
            except Exception:
                pass
        t_out = float(d0[0]) if d0.size > 0 else t_air
        result_t_wall[name] = round(0.5 * (t_air + t_out), 2)

    return {
        "t_air": result_t_air,
        "t_wall": result_t_wall,
        "method": method,
        "calibration_steps": calibration_steps,
    }
