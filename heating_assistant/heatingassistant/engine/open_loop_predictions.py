"""Open-loop free-run predictions for identification diagnostics."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from .history.window import split_contiguous_runs
from .integrator import ImplicitEulerConvergenceError, implicit_euler_substeps


# ── Open-loop predictions ────────────────────────────────────────────────────

def compute_open_loop_predictions(
    history: List[Dict[str, Any]],
    system: Any,
    room_names: List[str],
    n_rooms: int,
    dt: float,
    segment_length: Optional[int] = None,
    t_wall_initial: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Run open-loop simulations over observation history.

    ``segment_length=None`` runs one continuous free-run per contiguous data
    stretch. Passing an integer computes segmented N-step accuracy by
    re-initialising each fixed-length window from its first measurement.
    """

    n = int(n_rooms)
    if segment_length is not None:
        effective_segment_length = min(int(segment_length), max(1, len(history) // 2))
        if len(history) < effective_segment_length + 1:
            return {
                "error": (
                    f"Insufficient history: need >= {effective_segment_length + 1} "
                    f"steps, have {len(history)}."
                ),
                "per_room": {},
                "overall_rmse": {},
                "n_segments": 0,
                "segment_length": effective_segment_length,
            }
        segment_length = effective_segment_length
    elif len(history) < 2:
        return {
            "error": f"Insufficient history: need >= 2 steps, have {len(history)}.",
            "per_room": {},
            "overall_rmse": {},
            "n_segments": 0,
            "segment_length": None,
        }

    has_disturbance_vector = hasattr(system, "disturbance_vector")
    rooms = getattr(getattr(system, "_model", None), "rooms", {}) or {}
    set_window_open = getattr(system, "set_window_open", None)

    def _ua_modelled(room_name: str) -> bool:
        room_obj = rooms.get(room_name)
        return float(getattr(room_obj, "ua_open", 0.0) or 0.0) > 0.0

    def _gap_open(flags: Dict[str, Any], room_name: str) -> bool:
        return bool(flags.get(room_name, False)) and not _ua_modelled(room_name)

    def _make_d(record: Dict[str, Any]) -> np.ndarray:
        outdoor = float(record.get("d_outdoor", 0.0))
        d_solar = record.get("d_solar", {}) or {}
        if has_disturbance_vector:
            return np.asarray(system.disturbance_vector(outdoor, d_solar), dtype=float)

        n_d = int(getattr(system, "nd", 1 + 2 * n))
        d = np.zeros(n_d)
        d[0] = outdoor
        room_idx = getattr(system, "_room_idx", {name: idx for idx, name in enumerate(room_names)})
        n_model_rooms = len(room_idx)
        for name, idx in room_idx.items():
            if 1 + idx < n_d:
                d[1 + idx] = float(d_solar.get(name, 0.0))
            room_obj = rooms.get(name)
            air_gain_idx = 1 + n_model_rooms + idx
            if room_obj is not None and air_gain_idx < n_d:
                d[air_gain_idx] = float(getattr(room_obj, "internal_gain", 0.0))
        return d

    per_room_preds: Dict[str, List[float]] = {name: [] for name in room_names}
    per_room_meas: Dict[str, List[float]] = {name: [] for name in room_names}
    simulation: Dict[str, List[Dict[str, Any]]] = {name: [] for name in room_names}
    n_segments = 0
    is_first_dataset_segment = True

    contiguous_runs = list(split_contiguous_runs(history, float(dt)))
    if segment_length is None:
        segments = contiguous_runs
    else:
        segments = [
            run[start : start + segment_length]
            for run in contiguous_runs
            for start in range(0, max(1, len(run) - 1), segment_length)
        ]

    nominal_dt = float(getattr(system, "_dt", dt) or dt)
    n_u = int(getattr(system, "nu", 0))

    for seg in segments:
        if len(seg) < 2:
            continue
        y0 = seg[0].get("y", [])
        if len(y0) < n:
            continue

        nx = int(getattr(system, "nx", n))
        u_prev = np.zeros(n_u)
        for k, value in enumerate(seg[0].get("u", [])):
            if k < n_u:
                u_prev[k] = float(value)

        d_prev = _make_d(seg[0])
        init_fn = getattr(system, "initial_state_from_measurement", None)
        if callable(init_fn):
            y0_arr = np.asarray(y0[:n], dtype=float)
            wall_seed = "air" if is_first_dataset_segment else "steady_state"
            try:
                x = np.asarray(init_fn(y0_arr, u_prev, d_prev, wall_seed=wall_seed), dtype=float)
            except TypeError:
                try:
                    x = np.asarray(init_fn(y0_arr, u_prev, d_prev), dtype=float)
                except TypeError:
                    x = np.asarray(init_fn(y0_arr, u_prev), dtype=float)
            if is_first_dataset_segment and x.shape[0] >= 2 * n:
                if t_wall_initial:
                    for room_idx, room_name in enumerate(room_names):
                        if room_name in t_wall_initial:
                            x[n + room_idx] = float(t_wall_initial[room_name])
                        else:
                            x[n + room_idx] = float(y0_arr[room_idx])
                else:
                    x[n : 2 * n] = y0_arr[:n]
            is_first_dataset_segment = False
        else:
            x = np.zeros(nx, dtype=float)
            x[:n] = np.asarray(y0[:n], dtype=float)
            is_first_dataset_segment = False

        ts_prev = float(seg[0].get("timestamp", 0.0))
        has_wall_states = len(x) > n
        window_open0 = seg[0].get("window_open") or {}
        if callable(set_window_open):
            set_window_open(window_open0)
        for room_idx, room_name in enumerate(room_names):
            if room_idx >= len(y0):
                continue
            if _gap_open(window_open0, room_name):
                simulation[room_name].append(
                    {"time": ts_prev, "measured": None, "predicted": None}
                )
                continue
            init_value = round(float(y0[room_idx]), 3)
            entry: Dict[str, Any] = {
                "time": ts_prev,
                "measured": init_value,
                "predicted": init_value,
            }
            if has_wall_states and n + room_idx < len(x):
                entry["predicted_wall"] = round(float(x[n + room_idx]), 3)
            simulation[room_name].append(entry)

        valid_segment = True
        prev_window_open = window_open0
        for record in seg[1:]:
            if callable(set_window_open):
                set_window_open(prev_window_open)
            d = _make_d(record)
            u = np.zeros(n_u)
            for k, value in enumerate(record.get("u", [])):
                if k < n_u:
                    u[k] = float(value)

            ts = float(record.get("timestamp", 0.0))
            dt_step = ts - ts_prev
            if dt_step <= 0:
                dt_step = nominal_dt
            n_steps = max(1, min(200, round(dt_step * 10.0 / nominal_dt)))
            params = np.array([])

            def rhs(state: np.ndarray, u_loc: np.ndarray = u_prev, d_loc: np.ndarray = d_prev) -> np.ndarray:
                return system.f(state, u_loc, d_loc, params, 0.0)

            def jac(state: np.ndarray, u_loc: np.ndarray = u_prev, d_loc: np.ndarray = d_prev) -> np.ndarray:
                return system.dfdx(state, u_loc, d_loc, params, 0.0)

            try:
                x = implicit_euler_substeps(rhs, jac, x, dt_step, n_steps)
            except (ImplicitEulerConvergenceError, Exception):
                valid_segment = False
                break

            d_prev = d
            u_prev = u
            ts_prev = ts

            y_meas = record.get("y", [])
            window_open = record.get("window_open") or {}
            has_wall = len(x) > n
            for room_idx, room_name in enumerate(room_names):
                if room_idx >= len(y_meas):
                    continue
                meas_val = float(y_meas[room_idx])
                if _gap_open(window_open, room_name):
                    x[room_idx] = meas_val
                    simulation[room_name].append(
                        {"time": ts, "measured": None, "predicted": None}
                    )
                    continue
                pred_val = float(x[room_idx])
                per_room_preds[room_name].append(pred_val)
                per_room_meas[room_name].append(meas_val)
                sim_entry: Dict[str, Any] = {
                    "time": ts,
                    "measured": round(meas_val, 3),
                    "predicted": round(pred_val, 3),
                }
                if has_wall and n + room_idx < len(x):
                    sim_entry["predicted_wall"] = round(float(x[n + room_idx]), 3)
                simulation[room_name].append(sim_entry)
            prev_window_open = window_open

        if valid_segment:
            n_segments += 1

    if n_segments == 0:
        return {
            "error": "No valid segments found.",
            "per_room": {},
            "overall_rmse": {},
            "n_segments": 0,
            "segment_length": segment_length,
        }

    per_room_results: Dict[str, Any] = {}
    overall_rmse: Dict[str, float] = {}
    for room_name in room_names:
        preds = np.asarray(per_room_preds[room_name], dtype=float)
        meas = np.asarray(per_room_meas[room_name], dtype=float)
        if len(preds) == 0:
            per_room_results[room_name] = {
                "rmse": None,
                "mae": None,
                "simulation": [],
            }
            continue
        residuals = preds - meas
        rmse = float(np.sqrt(np.mean(residuals ** 2)))
        mae = float(np.mean(np.abs(residuals)))
        per_room_results[room_name] = {
            "rmse": round(rmse, 4),
            "mae": round(mae, 4),
            "simulation": simulation[room_name],
        }
        overall_rmse[room_name] = round(rmse, 4)

    return {
        "per_room": per_room_results,
        "overall_rmse": overall_rmse,
        "n_segments": n_segments,
        "segment_length": segment_length,
    }


__all__ = ["compute_open_loop_predictions"]
