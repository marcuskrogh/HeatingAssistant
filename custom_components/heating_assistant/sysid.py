"""
System identification simulation for the Heating Assistant integration.

Provides ``run_sysid_simulation``: an open-loop forward simulation of the
1R1C thermal model over a recorded history window with configurable
parameters, returning the simulated temperatures *and* the propagated
state-covariance for dashboard visualisation.

The covariance propagation uses the linearised (Euler-discretised) state
matrix so the shaded confidence band on the dashboard shows how fast
uncertainty grows as the model runs free — wider band means the model is
sensitive to process noise at the current parameters.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_sysid_simulation(
    history: List[Dict[str, Any]],
    model: Any,                          # HouseModel
    heat_sources: List[Any],             # list of HeatSource objects
    room_names: List[str],
    dt: float,                           # sampling interval [s]
    horizon_steps: int,
    room_params: Dict[str, Dict[str, float]],  # {room: {thermal_mass, r_external}}
    sigma_w: float,                      # process noise std [°C / sqrt(s)]
    sigma_v: float,                      # measurement noise std [°C]
) -> Dict[str, Any]:
    """
    Run an open-loop simulation over ``horizon_steps`` of the history buffer.

    The model is initialised from the first measurement in the window and
    propagated purely with the recorded control inputs and disturbances.  A
    per-room state covariance is propagated in parallel so the caller can
    render ±2σ confidence bands on the dashboard.

    Parameters
    ----------
    history
        Ordered history-buffer entries (oldest first), each a dict with keys
        ``y`` (list of room temps), ``u`` (list of source fractions),
        ``d_outdoor`` (float), ``d_solar`` (dict room→W), ``timestamp``.
    model
        The live ``HouseModel`` instance (read-only; a copy is made).
    heat_sources
        The live heat-source objects (read-only; used for max_power).
    room_names
        Ordered room names matching the ``y`` index.
    dt
        Sampling interval in seconds.
    horizon_steps
        Number of history steps to simulate (capped to len(history)−1).
    room_params
        Overrideable per-room parameters.  Keys that are present override the
        corresponding room attribute; missing keys use the live model values.
        Supported keys per room: ``thermal_mass`` [J/K], ``r_external`` [K/W].
    sigma_w
        Process noise standard deviation [°C / step¹ᐟ²] applied to each
        temperature state.  Controls how quickly open-loop covariance grows.
    sigma_v
        Measurement noise standard deviation [°C].  Added in quadrature to
        the diagonal of the propagated state covariance to give the total
        output variance.

    Returns
    -------
    dict with keys:
        ``per_room``  – {room_name: {simulation, rmse, mae, thermal_mass,
                                     r_external, sigma_w, sigma_v}}
          ``simulation``  – list of {time, measured, simulated,
                                     cov_upper, cov_lower}
        ``horizon_steps`` – actual steps simulated
        ``error``         – only present on failure
    """
    n = len(room_names)
    if n == 0:
        return {"error": "No rooms configured.", "per_room": {}}

    if len(history) < 2:
        return {
            "error": (
                f"Insufficient history: need ≥ 2 steps, have {len(history)}."
            ),
            "per_room": {},
            "horizon_steps": 0,
        }

    actual_steps = min(horizon_steps, len(history) - 1)
    # Take the most recent records so we always start from a consistent model
    # state and avoid stale entries that may have been recorded with a different
    # room count (e.g. before a room was added to the configuration).
    window = list(history)[-(actual_steps + 1):]

    # ------------------------------------------------------------------
    # Build a temporary model with overridden parameters
    # ------------------------------------------------------------------
    try:
        sim_model = _build_sim_model(model, room_params, room_names)
    except Exception as exc:
        _LOGGER.error("Failed to build simulation model: %s", exc, exc_info=True)
        return {"error": f"Model construction failed: {exc}", "per_room": {}}

    # ------------------------------------------------------------------
    # Extract the linearised drift matrix F_c = (1/C) A for covariance
    # propagation.  Shape (n, n).
    # ------------------------------------------------------------------
    C_vec, A_mat, _ = sim_model._build_matrices()
    inv_C = 1.0 / np.maximum(C_vec, 1e-6)
    F_c = A_mat * inv_C[:, None]           # (n, n)

    # Euler-discretised state-transition matrix: F_d ≈ I + F_c·dt
    F_d = np.eye(n) + F_c * dt            # (n, n)

    # Discrete process-noise covariance: Q_d = sigma_w² · dt · I
    Q_d = (sigma_w ** 2) * dt * np.eye(n)

    # ------------------------------------------------------------------
    # Find the first record in the window that has a full-length y-vector.
    # Older entries may have been recorded with a different room count (e.g.
    # before a room was added), so we skip them rather than hard-failing.
    # ------------------------------------------------------------------
    start_idx = 0
    for start_idx, rec in enumerate(window):
        y_check = rec.get("y", [])
        if len(y_check) >= n:
            break
    else:
        return {
            "error": (
                f"No history record with ≥ {n} room temperatures found "
                f"(all {len(window)} records in the window have a shorter y-vector). "
                "This can happen if rooms were recently added to the configuration."
            ),
            "per_room": {},
        }

    window = window[start_idx:]
    if len(window) < 2:
        return {
            "error": "Fewer than 2 usable records after skipping short y-vectors.",
            "per_room": {},
        }

    y0 = window[0].get("y", [])

    T_sim = np.array(y0[:n], dtype=float)
    # Initial covariance: uncertainty = measurement noise on each axis
    P = (sigma_v ** 2) * np.eye(n)

    # Heat-source max powers (needed to convert fraction u → watts)
    source_max_power: List[float] = []
    source_rooms: List[str] = []
    for src in heat_sources:
        source_max_power.append(float(getattr(src, "max_power", 0.0)))
        source_rooms.append(getattr(src, "room", ""))

    # ------------------------------------------------------------------
    # Forward simulation
    # ------------------------------------------------------------------
    per_room_sim: Dict[str, List[Dict[str, Any]]] = {name: [] for name in room_names}

    # Record initial point
    ts0 = float(window[0].get("timestamp", 0.0))
    for i, name in enumerate(room_names):
        std_i = float(np.sqrt(max(0.0, P[i, i]) + sigma_v ** 2))
        per_room_sim[name].append({
            "time": ts0,
            "measured": float(y0[i]),
            "simulated": float(T_sim[i]),
            "cov_upper": float(T_sim[i] + 2.0 * std_i),
            "cov_lower": float(T_sim[i] - 2.0 * std_i),
        })

    # Set initial temperatures on the simulation model
    for i, name in enumerate(room_names):
        sim_model.rooms[name].temperature = float(T_sim[i])

    for record in window[1:]:
        u_raw = record.get("u", [])
        d_outdoor = float(record.get("d_outdoor", 10.0))
        d_solar: Dict[str, float] = record.get("d_solar", {})
        timestamp = float(record.get("timestamp", 0.0))
        y_raw = record.get("y", [])
        # If this record has fewer entries than expected, use None for missing rooms.
        y_meas: List[Optional[float]] = [
            (float(y_raw[i]) if i < len(y_raw) else None) for i in range(n)
        ]

        # Convert control fractions to heat inputs [W]
        heat_inputs: Dict[str, float] = {}
        for k, src_room in enumerate(source_rooms):
            if src_room in heat_inputs:
                heat_inputs[src_room] = 0.0
        for k, (src_room, max_p) in enumerate(zip(source_rooms, source_max_power)):
            frac = float(u_raw[k]) if k < len(u_raw) else 0.0
            heat_inputs[src_room] = heat_inputs.get(src_room, 0.0) + frac * max_p

        # Advance model state (updates sim_model temperatures in-place)
        try:
            sim_model.step(
                dt=dt,
                heat_inputs=heat_inputs,
                outdoor_temp=d_outdoor,
                solar_gains=d_solar,
            )
        except Exception as exc:
            _LOGGER.warning("SysID simulation step failed: %s", exc)
            break

        T_sim = np.array(
            [sim_model.rooms[name].temperature for name in room_names],
            dtype=float,
        )

        # Propagate covariance: P_{k+1} = F_d P F_d^T + Q_d
        P = F_d @ P @ F_d.T + Q_d

        for i, name in enumerate(room_names):
            std_i = float(np.sqrt(max(0.0, P[i, i]) + sigma_v ** 2))
            measured_i = (
                float(y_meas[i]) if i < len(y_meas) and y_meas[i] is not None
                else None
            )
            per_room_sim[name].append({
                "time": timestamp,
                "measured": measured_i,
                "simulated": float(T_sim[i]),
                "cov_upper": float(T_sim[i] + 2.0 * std_i),
                "cov_lower": float(T_sim[i] - 2.0 * std_i),
            })

    # ------------------------------------------------------------------
    # Compute RMSE / MAE per room
    # ------------------------------------------------------------------
    per_room_out: Dict[str, Any] = {}
    for i, name in enumerate(room_names):
        sim_data = per_room_sim[name]
        errors = [
            e["simulated"] - e["measured"]
            for e in sim_data
            if e["measured"] is not None
        ]
        if errors:
            err_arr = np.array(errors)
            rmse = float(np.sqrt(np.mean(err_arr ** 2)))
            mae = float(np.mean(np.abs(err_arr)))
        else:
            rmse = None
            mae = None

        room = sim_model.rooms.get(name)
        per_room_out[name] = {
            "simulation": sim_data,
            "rmse": rmse,
            "mae": mae,
            "thermal_mass": float(room.thermal_mass) if room else None,
            "r_external": float(room.r_external) if room else None,
            "sigma_w": sigma_w,
            "sigma_v": sigma_v,
        }

    return {
        "per_room": per_room_out,
        "horizon_steps": actual_steps,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_sim_model(
    live_model: Any,
    room_params: Dict[str, Dict[str, float]],
    room_names: List[str],
) -> Any:
    """Return a deep copy of *live_model* with room_params overridden."""
    import copy

    sim_model = copy.deepcopy(live_model)
    for name in room_names:
        overrides = room_params.get(name, {})
        if not overrides:
            continue
        room = sim_model.rooms.get(name)
        if room is None:
            continue
        if "thermal_mass" in overrides:
            room.thermal_mass = float(overrides["thermal_mass"])
        if "r_external" in overrides:
            room.r_external = float(overrides["r_external"])

    # Rebuild cached matrices so the new parameters are reflected in F_c / F_d.
    sim_model.rebuild_derived_parameters()
    C, A, B = sim_model._build_matrices()
    sim_model._C = C
    sim_model._A = A
    sim_model._B_ext = B

    return sim_model
