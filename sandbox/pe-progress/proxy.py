"""Normalised PE proxy: η = RMSE / σ_R = sqrt(J / n_obs)."""

from __future__ import annotations

import math
from typing import Optional

R_VAR = 0.25
SIGMA_C = math.sqrt(R_VAR)
ETA_TOL = 2.0
ETA_NOISE = 1.0


def eta_from_j(j: float, n_obs: int) -> Optional[float]:
    if n_obs <= 0 or not math.isfinite(j) or j < 0:
        return None
    return math.sqrt(j / float(n_obs))


def rmse_c_from_eta(eta: float) -> float:
    return float(eta) * SIGMA_C


def n_obs_nstep(n_hist: int, n_horizon: int, stride: int) -> int:
    """Count residual steps in receding N-step PEM (matches nstep_pem.py)."""
    n_obs = 0
    n_horizon = max(1, int(n_horizon))
    stride = max(1, int(stride))
    for k in range(max(0, n_hist - 1)):
        remaining = n_hist - 1 - k
        horizon = min(n_horizon, remaining)
        if k % stride == 0 and horizon >= 1:
            n_obs += horizon
    return n_obs


def n_obs_tiled_oe(n_hist: int, max_window_steps: int, min_segment_steps: int) -> int:
    """Count residual steps in tiled OE (matches sensitivity.py windowing)."""
    n_obs = 0
    if n_hist < min_segment_steps:
        return 0
    for win_start in range(0, n_hist, max_window_steps):
        win_end = min(win_start + max_window_steps, n_hist)
        if (win_end - win_start) < min_segment_steps:
            continue
        n_obs += (win_end - win_start) - 1
    return n_obs
