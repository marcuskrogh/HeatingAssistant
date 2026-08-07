"""Convert Home Assistant history buffers to the standardised mbc format."""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np


def convert_history_std(
    history: List[Dict[str, Any]],
    n: int,
    n_u: int,
    room_names: List[str],
    use_ym: bool = False,
) -> List[Dict[str, np.ndarray]]:
    """Convert the HA history buffer to the standardised mbc format.

    Each record is converted to ``{"y": ndarray, "u": ndarray, "d": ndarray}``
    for discrete-time estimator, or ``{"ym": ndarray, "u": ndarray, "d": ndarray}``
    for continuous-discrete estimator.

    where ``d = [T_out, solar_1 … solar_n, q_air_1 … q_air_n]`` with the
    recorded (raw, unscaled) solar gains in slots 1…n and zeros in the
    air-heat slots — the q_int contribution is applied parametrically
    through θ inside ``HouseThermalSDE.f``.

    Parameters
    ----------
    history : list of dicts
        HA history buffer records.
    n : int
        Number of rooms.
    n_u : int
        Number of heat sources.
    room_names : list of str
        Room names in index order.
    use_ym : bool, optional
        If True, use "ym" key for measurements (CD-EKF convention).
        If False, use "y" key (discrete-time convention). Default: False.
    """
    room_idx = {name: i for i, name in enumerate(room_names)}
    std: List[Dict[str, Any]] = []
    meas_key = "ym" if use_ym else "y"
    for record in history:
        y = np.array(record["y"][:n], dtype=float)
        u = np.zeros(n_u)
        for k, val in enumerate(record.get("u", [])):
            if k < n_u:
                u[k] = float(val)
        d = np.zeros(1 + 2 * n)
        d[0] = float(record["d_outdoor"])
        for name, gain in record.get("d_solar", {}).items():
            if name in room_idx:
                d[1 + room_idx[name]] = float(gain)
        # Carry the Unix timestamp so _simulation_mse_and_grad can detect
        # gaps from controller restarts and treat them as segment boundaries.
        t_val = record.get("timestamp")
        # Per-room window-open data-quality label.  When a room's window was
        # open at this step its air-exchange loss is unmodelled, so the
        # open-loop objective must neither score that room's residual nor let
        # its (cooling) air node leak into the coupled neighbours.  Missing
        # key (old records / seeded history) ⇒ all-closed.
        wo_map = record.get("window_open") or {}
        window_open = np.array(
            [bool(wo_map.get(name, False)) for name in room_names],
            dtype=bool,
        )
        std.append({
            meas_key: y,
            "u": u,
            "d": d,
            "t": float(t_val) if t_val is not None else None,
            "window_open": window_open,
        })
    return std
