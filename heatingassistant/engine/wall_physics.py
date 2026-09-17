"""2R2C wall-node physics used by the CD-EKF and grey-box PE.

The wall/mass temperature is unmeasured.  Algebraic steady state of the
wall energy balance (no inter-room flow) is

    T_w^ss = ρ T_a + (1 − ρ) T_out + Q_wall / (g_aw + g_wout)

with ρ = g_aw / (g_aw + g_wout).  That mix moves with air, outdoor,
solar, and θ (splits / UA).  The estimator fuses it as a Kalman
*measurement* of the wall block (not a clip).  PE uses the same mix as
the MAP mean for T_w(t_0) and as an open-loop path residual.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

#: Wall diffusion as a fraction of the air-node σ_w (same per-room q-scale).
WALL_PROCESS_NOISE_FRACTION = 0.1

#: Prior std [K] of wall lag around algebraic SS (capacitance, not a box).
WALL_LAG_SIGMA_K = 2.5

#: Weight on ||T_w − T_w^ss||² in the N-step PE objective (K², pre-scale).
WALL_SS_PENALTY = 1.0


def wall_steady_state(
    t_air: np.ndarray,
    t_out: float,
    g_aw: np.ndarray,
    g_wout: np.ndarray,
    q_wall: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Algebraic wall SS from the 2R2C wall row (dT_w/dt = 0, no R_ij)."""
    ta = np.asarray(t_air, dtype=float).ravel()
    g_aw = np.asarray(g_aw, dtype=float).ravel()
    g_wout = np.asarray(g_wout, dtype=float).ravel()
    g_sum = np.maximum(g_aw + g_wout, 1e-12)
    rho = g_aw / g_sum
    tw = rho * ta + (1.0 - rho) * float(t_out)
    if q_wall is not None:
        tw = tw + np.asarray(q_wall, dtype=float).ravel()[: tw.size] / g_sum[: tw.size]
    return tw


def _q_wall_from_quants(
    quants: Dict[str, np.ndarray],
    q_solar: Optional[Sequence[float]],
    n: int,
) -> np.ndarray:
    if q_solar is None:
        return np.zeros(n, dtype=float)
    s = np.asarray(quants["s"], dtype=float)
    frac = float(quants.get("wall_frac", 0.5))
    facade = np.asarray(quants.get("facade", np.zeros(n)), dtype=float)
    q_sol = np.asarray(q_solar, dtype=float).ravel()
    q_wall = np.zeros(n, dtype=float)
    m = min(n, s.size, q_sol.size)
    q_wall[:m] = (frac + facade[:m]) * s[:m] * q_sol[:m]
    return q_wall


def wall_ss_from_quants(
    quants: Dict[str, np.ndarray],
    t_air: Sequence[float],
    t_out: float,
    q_solar: Optional[Sequence[float]] = None,
) -> np.ndarray:
    """T_w^ss from PE θ-quantities (g_we stands in for g_wout)."""
    mu, _drf, _dlogr, _dlogs = wall_ss_partials(quants, t_air, t_out, q_solar)
    return mu


def wall_ss_partials(
    quants: Dict[str, np.ndarray],
    t_air: Sequence[float],
    t_out: float,
    q_solar: Optional[Sequence[float]] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """``T_w^ss`` and ∂μ/∂(r_aw, log r_ext, log s) for PE MAP / path residuals.

    PE uses ``g_we`` as ``g_wout`` (no sky/bridge in θ).  Then
    ``ρ = 1 − r_aw_fraction`` and the solar offset is ``Q_wall / g_sum``.
    """
    g_aw = np.asarray(quants["g_aw"], dtype=float).ravel()
    g_wout = np.asarray(quants["g_we"], dtype=float).ravel()
    n = int(g_aw.size)
    rf = np.asarray(quants.get("rf", np.full(n, 0.05)), dtype=float).ravel()
    if rf.size < n:
        rf = np.pad(rf, (0, n - rf.size), constant_values=0.05)
    ta = np.asarray(t_air, dtype=float).ravel()
    if ta.size < n:
        ta = np.pad(ta, (0, n - ta.size), constant_values=20.0)
    else:
        ta = ta[:n]
    q_wall = _q_wall_from_quants(quants, q_solar, n)
    mu = wall_steady_state(ta, t_out, g_aw, g_wout, q_wall)
    g_sum = np.maximum(g_aw[:n] + g_wout[:n], 1e-12)
    g_cond = np.maximum(g_aw[:n] * np.maximum(rf[:n], 1e-12), 1e-12)
    dmu_drf = (float(t_out) - ta) + q_wall * (1.0 - 2.0 * rf[:n]) / g_cond
    dmu_dlogr = q_wall / g_sum
    dmu_dlogs = q_wall / g_sum
    return mu, dmu_drf, dmu_dlogr, dmu_dlogs


def wall_ss_from_room(
    room: Any,
    t_air: float,
    t_out: Optional[float],
    q_solar: Optional[float] = None,
) -> float:
    """Algebraic wall SS for a configured :class:`~.thermal_model.Room`."""
    ta = float(t_air)
    if t_out is None or not np.isfinite(t_out):
        to = ta
    else:
        to = float(t_out)
    _g_inf, g_aw, g_we = room.conductances()
    g_wout = (
        float(g_we)
        + float(getattr(room, "sky_radiative_ua", 0.0) or 0.0)
        + float(getattr(room, "thermal_bridge_psi_l", 0.0) or 0.0)
    )
    q_wall = None
    if q_solar is not None and np.isfinite(q_solar):
        from .const import SOLAR_WALL_FRACTION
        facade = (
            float(getattr(room, "facade_solar_share", 0.0) or 0.0)
            * float(getattr(room, "facade_absorptance", 0.0) or 0.0)
        )
        scale = float(getattr(room, "solar_scale", 1.0) or 1.0)
        q_wall = np.array(
            [(SOLAR_WALL_FRACTION + facade) * scale * float(q_solar)],
            dtype=float,
        )
    return float(wall_steady_state([ta], to, [g_aw], [g_wout], q_wall)[0])


def wall_measurement_variance(
    g_aw: np.ndarray,
    g_wout: np.ndarray,
    q_wall: Optional[np.ndarray] = None,
    sigma_lag: float = WALL_LAG_SIGMA_K,
) -> np.ndarray:
    """R for the wall SS measurement: lag variance plus solar-offset variance."""
    g_sum = np.maximum(
        np.asarray(g_aw, dtype=float) + np.asarray(g_wout, dtype=float),
        1e-12,
    )
    r = np.full(g_sum.shape, float(sigma_lag) ** 2, dtype=float)
    if q_wall is not None:
        r = r + (np.asarray(q_wall, dtype=float).ravel()[: r.size] / g_sum[: r.size]) ** 2
    return np.maximum(r, 1e-6)


def fuse_wall_equilibrium(
    x: np.ndarray,
    P: np.ndarray,
    y_wall: np.ndarray,
    r_phys: np.ndarray,
    n_rooms: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """Joseph-form Kalman update of the wall block toward T_w^ss.

    Observation: ``y_wall ≈ x[n:2n]`` with diagonal ``R = diag(r_phys)``.
    Air, filter, and offset states are unchanged except through ``P``.
    """
    x = np.asarray(x, dtype=float).copy()
    P = np.asarray(P, dtype=float).copy()
    n = int(n_rooms)
    nx = int(x.size)
    if n <= 0 or x.size < 2 * n or P.shape != (nx, nx):
        return x, P
    y_w = np.asarray(y_wall, dtype=float).ravel()[:n]
    r = np.asarray(r_phys, dtype=float).ravel()[:n]
    if y_w.size < n:
        return x, P
    R = np.diag(np.maximum(r, 1e-6))
    H = np.zeros((n, nx))
    for i in range(n):
        H[i, n + i] = 1.0
    S = H @ P @ H.T + R
    try:
        S_inv = np.linalg.inv(S)
    except np.linalg.LinAlgError:
        S_inv = np.linalg.pinv(S)
    K = P @ H.T @ S_inv
    innov = y_w - x[n: 2 * n]
    x = x + K @ innov
    eye = np.eye(nx)
    kh = K @ H
    P = (eye - kh) @ P @ (eye - kh).T + K @ R @ K.T
    P = 0.5 * (P + P.T)
    return x, P


def isolate_air_wall_covariance(P: np.ndarray, n_rooms: int) -> np.ndarray:
    """Zero air-wall cross-covariance so air innovations cannot move Tw.

    The wall energy balance already implies: if Q_wall >= 0 (no sky sink)
    and Tw < min(Ta, Tout), then dTw/dt > 0.  The Kalman gain
    K_w = P_wa S^{-1} ignores that and dumps air-model mismatch into the
    unmeasured wall.  Decorrelating before the air update leaves Tw to
    the ODE plus the SS measurement.
    """
    P = np.asarray(P, dtype=float).copy()
    n = int(n_rooms)
    if n <= 0 or P.shape[0] < 2 * n or P.shape[1] < 2 * n:
        return P
    P[n: 2 * n, :n] = 0.0
    P[:n, n: 2 * n] = 0.0
    P = 0.5 * (P + P.T)
    return P


def block_air_wall_kalman_gain(estimator: Any) -> None:
    """Apply :func:`isolate_air_wall_covariance` on a CD-EKF-like object."""
    model = getattr(estimator, "_model", None)
    n = int(getattr(model, "_n_rooms", 0) or 0) if model is not None else 0
    if n <= 0 or not hasattr(estimator, "_P"):
        return
    estimator._P = isolate_air_wall_covariance(
        np.asarray(estimator._P, dtype=float), n,
    )


def apply_wall_ss_fusion(estimator: Any, y_air: Sequence[float], d: Sequence[float]) -> None:
    """Fuse RC wall equilibrium into a CD-EKF-like object (``_x``, ``_P``)."""
    model = getattr(estimator, "_model", None)
    if model is None or not hasattr(model, "wall_observation"):
        return
    n = int(getattr(model, "_n_rooms", 0) or 0)
    y_w, r_phys = model.wall_observation(y_air, d)
    x_new, p_new = fuse_wall_equilibrium(
        np.asarray(estimator._x, dtype=float),
        np.asarray(estimator._P, dtype=float),
        y_w, r_phys, n,
    )
    estimator._x = x_new
    estimator._P = p_new


def accumulate_wall_ss_penalty(
    total_sse: float,
    total_grad: np.ndarray,
    x: np.ndarray,
    sx: np.ndarray,
    t_air: Sequence[float],
    t_out: float,
    quants: Dict[str, np.ndarray],
    q_solar: Optional[Sequence[float]] = None,
    n_rooms: int = 0,
    weight: float = WALL_SS_PENALTY,
    layout: Any = None,
) -> Tuple[float, np.ndarray]:
    """Add ``w · ( |T_w−μ| − σ_lag )_+²`` so ordinary wall lag is free."""
    n = int(n_rooms)
    if n <= 0 or weight <= 0.0:
        return float(total_sse), total_grad
    mu, dmu_drf, dmu_dlogr, dmu_dlogs = wall_ss_partials(
        quants, t_air, t_out, q_solar,
    )
    sse = float(total_sse)
    grad = np.asarray(total_grad, dtype=float).copy()
    w = float(weight)
    for i in range(n):
        wi = n + i
        if wi >= x.size or i >= mu.size:
            break
        viol = float(x[wi] - mu[i])
        excess = abs(viol) - float(WALL_LAG_SIGMA_K)
        if excess <= 0.0:
            continue
        sse += w * excess * excess
        signed = excess if viol >= 0.0 else -excess
        if wi < sx.shape[1]:
            grad = grad + (2.0 * w * signed) * sx[:, wi]
        if layout is not None:
            apply_wall_ss_param_grad(
                grad, 2.0 * w * signed, i, dmu_drf, dmu_dlogr, dmu_dlogs, layout,
            )
    return sse, grad


def apply_wall_ss_param_grad(
    grad: np.ndarray,
    scale: float,
    room_i: int,
    dmu_drf: np.ndarray,
    dmu_dlogr: np.ndarray,
    dmu_dlogs: np.ndarray,
    layout: Any,
) -> None:
    """Add ``scale · (−∂μ_i/∂θ)`` into *grad* (in-place)."""
    i = int(room_i)
    a_r, b_r = layout.idx_log_r
    if a_r + i < b_r:
        grad[a_r + i] += float(scale) * (-float(dmu_dlogr[i]))
    splits = list(getattr(layout, "identifiable_splits", []) or [])
    if i in splits:
        k = splits.index(i)
        a_rf, b_rf = layout.idx_r_aw
        if a_rf + k < b_rf:
            grad[a_rf + k] += float(scale) * (-float(dmu_drf[i]))
    solar = list(getattr(layout, "identifiable_solar", []) or [])
    if i in solar:
        k = solar.index(i)
        a_s, b_s = layout.idx_log_solar
        if a_s + k < b_s:
            grad[a_s + k] += float(scale) * (-float(dmu_dlogs[i]))


def _record_timestamp(record: Dict[str, Any]) -> Optional[float]:
    t_val = record.get("timestamp", record.get("t"))
    if t_val is None:
        return None
    try:
        return float(t_val)
    except (TypeError, ValueError):
        return None


def capture_tw0_anchors_from_std(
    std_history: Sequence[Dict[str, Any]],
    n_rooms: int,
    n_wall_segs: int,
    dataset_start_timestamps: Optional[Iterable[float]] = None,
    meas_key: str = "ym",
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Anchors from standardised history (``d[0]=T_out``, ``d[1+i]=solar``)."""
    n = int(n_rooms)
    n_segs = max(1, int(n_wall_segs))
    ta = np.full((n_segs, n), 20.0)
    tout = np.full(n_segs, 20.0)
    qsol = np.zeros((n_segs, n))
    if not std_history:
        return ta, tout, qsol
    starts = list(dataset_start_timestamps) if dataset_start_timestamps else []
    records: List[Dict[str, Any]] = []
    if n_segs <= 1 or not starts:
        records = [std_history[0]] * n_segs
    else:
        times = [_record_timestamp(rec) for rec in std_history]
        for ts in starts[:n_segs]:
            try:
                target = float(ts)
            except (TypeError, ValueError):
                records.append(std_history[0])
                continue
            best = std_history[0]
            best_d = float("inf")
            for rec, t_val in zip(std_history, times):
                if t_val is None:
                    continue
                dist = abs(float(t_val) - target)
                if dist < best_d:
                    best_d = dist
                    best = rec
            records.append(best)
        while len(records) < n_segs:
            records.append(records[-1] if records else std_history[0])
    for s, rec in enumerate(records):
        y = rec.get(meas_key, rec.get("y"))
        if y is None:
            y = []
        y = np.asarray(y, dtype=float).ravel()
        for i in range(min(n, y.size)):
            if np.isfinite(y[i]):
                ta[s, i] = float(y[i])
        d_arr = rec.get("d")
        if d_arr is None:
            continue
        d_arr = np.asarray(d_arr, dtype=float).ravel()
        if d_arr.size:
            tout[s] = float(d_arr[0])
        for i in range(n):
            slot = 1 + i
            if slot < d_arr.size:
                qsol[s, i] = float(d_arr[slot])
    return ta, tout, qsol
