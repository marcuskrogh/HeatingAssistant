"""Shared parsers for identification-history records."""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np


def record_u(record: Dict[str, Any], n_u: int) -> np.ndarray:
    """Extract control fractions from a history record as a float64 array."""
    u_raw = record.get("u", [])
    return np.array(
        [float(u_raw[k]) if k < len(u_raw) else 0.0 for k in range(n_u)],
        dtype=float,
    )


def record_d(
    record: Dict[str, Any],
    system: Any,
    room_list: List[str],
    n_d: int,
) -> np.ndarray:
    """Build the disturbance vector for a history record.

    Layout matches ``HouseThermalSDE.nd``: ``d = [T_out, q_solar(n), q_air(n)]``.
    Uses the SDE's ``disturbance_vector`` when available so internal gains match
    the live CD-EKF / MPC path.
    """
    outdoor = float(record.get("d_outdoor", 10.0))
    solar = record.get("d_solar", {}) or {}
    builder = getattr(system, "disturbance_vector", None)
    if builder is not None:
        return np.asarray(builder(outdoor, solar), dtype=float)

    d = np.zeros(n_d, dtype=float)
    d[0] = outdoor
    for i, name in enumerate(room_list):
        if 1 + i < n_d:
            d[1 + i] = float(solar.get(name, 0.0))
    return d


def record_window_open(record: Dict[str, Any], room_names: List[str]) -> List[bool]:
    """Return per-room window-open flags from a history record."""
    wo = record.get("window_open") or {}
    return [bool(wo.get(name, False)) for name in room_names]
