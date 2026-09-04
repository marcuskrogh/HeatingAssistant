"""Receding N-step path PEM with CD-EKF state and full θ Jacobians."""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .constants import _EMPTY_IDX, _T_WALL_HI, _T_WALL_LO
from .model_build import _build_parametric_system, _theta_model_quantities
from .sensitivity import _dFdtheta_const, _dfdtheta_step
from .theta_layout import _ThetaLayout

_SENTINEL = 1e10


class PeComputeTimeout(Exception):
    """Wall-clock cap hit during a PE NLP evaluation."""

    def __init__(self, cap_s: float, elapsed_s: float) -> None:
        self.cap_s = float(cap_s)
        self.elapsed_s = float(elapsed_s)
        super().__init__(timeout_user_message(self.cap_s))


def timeout_user_message(cap_s: float) -> str:
    cap = float(cap_s)
    if cap >= 60.0 and abs(cap / 60.0 - round(cap / 60.0)) < 1e-6:
        mins = int(round(cap / 60.0))
        t = f"{mins} minute" if mins == 1 else f"{mins} minutes"
    else:
        t = f"{int(round(cap))} seconds"
    return (
        f"Parameter estimation stopped after {t} (the configured maximum "
        "compute time). The selected dataset is too large for that limit. "
        "Parameters were not applied. Use a shorter time window or fewer "
        "datasets, or raise PE max compute time under Configuration → Advanced."
    )


def _check_deadline(deadline_mono: Optional[float], cap_s: float, t0: float) -> None:
    if deadline_mono is None:
        return
    if time.monotonic() > deadline_mono:
        raise PeComputeTimeout(cap_s, time.monotonic() - t0)


def _open_masks(n: int, rec: Dict[str, Any], rec_next: Dict[str, Any], layout: _ThetaLayout):
    open_k = rec.get("window_open")
    open_next = rec_next.get("window_open")
    modelled_open = np.zeros(n, dtype=bool)
    for i_ua in layout.identifiable_ua:
        modelled_open[i_ua] = True
    pin_src = open_k
    if pin_src is not None and modelled_open.any():
        pin_src = pin_src & ~modelled_open
    pin_idx = (
        np.where(pin_src)[0]
        if pin_src is not None and np.any(pin_src)
        else _EMPTY_IDX
    )
    if open_k is None and open_next is None:
        drop_idx = _EMPTY_IDX
    else:
        drop_mask = np.zeros(n, dtype=bool)
        if open_k is not None:
            drop_mask |= open_k
        if open_next is not None:
            drop_mask |= open_next
        drop_mask &= ~modelled_open
        drop_idx = np.where(drop_mask)[0]
    return pin_idx, drop_idx, open_k


def _ua_disturbance(
    n: int,
    layout: _ThetaLayout,
    theta: np.ndarray,
    d_k: np.ndarray,
    ym_k: np.ndarray,
    open_k,
) -> Tuple[np.ndarray, np.ndarray]:
    d_step = np.asarray(d_k, dtype=float).copy()
    ua_coeff = np.zeros(n, dtype=float)
    if layout.identifiable_ua:
        T_out_k = float(d_step[0]) if len(d_step) else 0.0
        ua_vals = layout.get_ua_open(theta)
        for k_ua, i_ua in enumerate(layout.identifiable_ua):
            c_open = bool(open_k[i_ua]) if open_k is not None else False
            coeff = (T_out_k - float(ym_k[i_ua])) if c_open else 0.0
            ua_coeff[i_ua] = coeff
            slot = 1 + n + i_ua
            if slot < len(d_step):
                d_step[slot] += float(ua_vals[k_ua]) * coeff
    return d_step, ua_coeff


def _implicit_mean(
    est: Any,
    model,
    quants,
    layout: _ThetaLayout,
    theta: np.ndarray,
    x: np.ndarray,
    sx: np.ndarray,
    u_k: np.ndarray,
    d_step: np.ndarray,
    ua_coeff: np.ndarray,
    ym_k: np.ndarray,
    pin_idx: np.ndarray,
    h_sub: float,
    n_sub: int,
    ntheta: int,
    nx: int,
    *,
    with_p: bool,
    P: Optional[np.ndarray] = None,
    sP: Optional[np.ndarray] = None,
    dFdtheta: Optional[np.ndarray] = None,
) -> Optional[Tuple[np.ndarray, np.ndarray, Optional[np.ndarray], Optional[np.ndarray]]]:
    _I = np.eye(nx)
    _p0 = model.params
    for _ in range(n_sub):
        try:
            f_val = model.f(x, u_k, d_step, _p0, 0.0)
            F_full = model.dfdx(x, u_k, d_step, _p0, 0.0)
        except Exception:
            return None
        M = _I - h_sub * F_full
        try:
            c_aff = f_val - F_full @ x
            x_new = np.linalg.solve(M, x + h_sub * c_aff)
            f_new = model.f(x_new, u_k, d_step, _p0, 0.0)
            dfdtheta_val = _dfdtheta_step(
                est, quants, layout, model, f_new, x_new, u_k, d_step,
                ntheta, nx, ua_coeff=ua_coeff,
            )
            sx = np.linalg.solve(M, (sx + h_sub * dfdtheta_val).T).T
            if with_p and P is not None and sP is not None and dFdtheta is not None:
                try:
                    G_sig = model.sigma(x_new, u_k, d_step, _p0, 0.0)
                except Exception:
                    G_sig = np.zeros((nx, nx))
                Phi = np.linalg.inv(M)
                Qd = h_sub * (G_sig @ G_sig.T)
                P = Phi @ P @ Phi.T + Qd
                P = 0.5 * (P + P.T)
                dPhi = h_sub * np.einsum("ab,ibc,cd->iad", Phi, dFdtheta, Phi)
                Phi_sP = np.einsum("ab,ibc->iac", Phi, sP)
                t1 = np.einsum("iab,cb->iac", Phi_sP, Phi)
                t2 = np.einsum("iab,bc->iac", dPhi, P)
                sP = t1 + t2 + t2.transpose(0, 2, 1)
                sP = 0.5 * (sP + sP.transpose(0, 2, 1))
            x = x_new
            if pin_idx.size:
                x[pin_idx] = ym_k[pin_idx]
                sx[:, pin_idx] = 0.0
                if with_p and P is not None:
                    P[pin_idx, :] = 0.0
                    P[:, pin_idx] = 0.0
                    if sP is not None:
                        sP[:, pin_idx, :] = 0.0
                        sP[:, :, pin_idx] = 0.0
        except np.linalg.LinAlgError:
            return None
        if not (np.all(np.isfinite(x)) and np.all(np.isfinite(sx))):
            return None
    return x, sx, P, sP


def _kalman_update(
    model,
    x: np.ndarray,
    P: np.ndarray,
    sx: np.ndarray,
    sP: np.ndarray,
    u_k: np.ndarray,
    d_step: np.ndarray,
    ym_next: np.ndarray,
    drop_idx: np.ndarray,
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    _p0 = model.params
    nx = x.shape[0]
    try:
        y_hat = model.hm(x, u_k, d_step, _p0, 0.0)
        H = model.dhmdx(x, u_k, d_step, _p0, 0.0)
        Rm_mat = np.array(model.Rm, dtype=float)
    except Exception:
        return None
    nu = np.asarray(ym_next, dtype=float) - y_hat
    if drop_idx.size:
        nu[drop_idx] = 0.0
        H[drop_idx, :] = 0.0
    S = H @ P @ H.T + Rm_mat
    try:
        sign, _logdet = np.linalg.slogdet(S)
        if sign <= 0:
            return None
        S_inv = np.linalg.inv(S)
    except np.linalg.LinAlgError:
        return None
    S_inv_nu = S_inv @ nu
    q = H.T @ S_inv_nu
    K = P @ H.T @ S_inv
    Phi = np.eye(nx) - K @ H
    P_minus = P.copy()
    x = x + K @ nu
    P = Phi @ P_minus @ Phi.T + K @ Rm_mat @ K.T
    P = 0.5 * (P + P.T)
    sx_aug = sx + np.einsum("iab,b->ia", sP, q)
    sx = np.einsum("ab,ib->ia", Phi, sx_aug)
    Phi_sP = np.einsum("ab,ibc->iac", Phi, sP)
    Phi_sP_Ht = np.einsum("iac,dc->iad", Phi_sP, H)
    J = np.einsum("iad,de->iae", Phi_sP_Ht, S_inv)
    HP_m = H @ P_minus
    JHP = np.einsum("iad,db->iab", J, HP_m)
    term2 = np.einsum("iab,cb->iac", JHP, Phi)
    JRm = np.einsum("iad,de->iae", J, Rm_mat)
    term4 = np.einsum("iad,bd->iab", JRm, K)
    sP = (
        np.einsum("iab,cb->iac", Phi_sP, Phi)
        - term2
        - term2.transpose(0, 2, 1)
        + term4
        + term4.transpose(0, 2, 1)
    )
    sP = 0.5 * (sP + sP.transpose(0, 2, 1))
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(P))):
        return None
    return x, P, sx, sP


def _seed_state(
    est: Any,
    model,
    layout: _ThetaLayout,
    theta: np.ndarray,
    rec0: Dict[str, Any],
    inject_wall: bool,
    wall_seg_idx: Optional[int],
    ntheta: int,
    nx: int,
) -> Tuple[np.ndarray, np.ndarray]:
    n = est._n
    ym0 = np.asarray(rec0["ym"], dtype=float)
    u0 = np.asarray(rec0.get("u", []), dtype=float)
    d0 = np.asarray(rec0.get("d", []), dtype=float)
    wall_seed = "air" if inject_wall else "steady_state"
    try:
        x = np.asarray(
            model.initial_state_from_measurement(ym0, u0, d0, wall_seed=wall_seed),
            dtype=float,
        )
    except TypeError:
        x = np.asarray(model.initial_state_from_measurement(ym0, u0, d0), dtype=float)
    sx = np.zeros((ntheta, nx))
    tw0, _ = layout.idx_t_wall_init
    if inject_wall and wall_seg_idx is not None:
        tw_base = tw0 + wall_seg_idx * n
        for i in range(n):
            if n + i < nx:
                x[n + i] = float(np.clip(theta[tw_base + i], _T_WALL_LO, _T_WALL_HI))
                sx[tw_base + i, n + i] = 1.0
    return x, sx


def nstep_pem_and_grad(
    est: Any,
    theta: np.ndarray,
    layout: _ThetaLayout,
    std_history: List[Dict[str, Any]],
    nominal_dt: float,
    *,
    n_horizon: int,
    origin_stride: int,
    dataset_start_ts: Optional[List[float]] = None,
    max_gap_factor: float = 1.5,
    deadline_mono: Optional[float] = None,
    cap_s: float = 60.0,
    t0_mono: Optional[float] = None,
) -> Tuple[float, np.ndarray]:
    """N-step path misfit (OE scale) + gradient through EKF and open-loop."""
    t0 = time.monotonic() if t0_mono is None else t0_mono
    _zero = np.zeros(len(theta))
    if not np.all(np.isfinite(theta)):
        return _SENTINEL, _zero.copy()
    model = _build_parametric_system(est, layout, theta)
    if model is None:
        return _SENTINEL, _zero.copy()

    n = est._n
    nx = int(model.nx)
    ntheta = len(theta)
    n_sub = max(1, int(math.ceil(est._dt / 300.0)))
    quants = _theta_model_quantities(est, layout, theta)
    dFdtheta = _dFdtheta_const(est, quants, layout, model, ntheta, nx)
    n_horizon = max(1, int(n_horizon))
    origin_stride = max(1, int(origin_stride))

    Nhist = len(std_history)
    seg_starts: List[int] = [0]
    for idx in range(Nhist - 1):
        t_a = std_history[idx].get("t")
        t_b = std_history[idx + 1].get("t")
        if t_a is not None and t_b is not None:
            if (float(t_b) - float(t_a)) > max_gap_factor * nominal_dt:
                seg_starts.append(idx + 1)
    seg_starts.append(Nhist)

    _ts_tol = 0.5 * nominal_dt
    _ds_ts_arr = (
        np.array(dataset_start_ts, dtype=float)
        if dataset_start_ts
        else None
    )

    total_sse = 0.0
    total_grad = np.zeros(ntheta)
    n_steps_used = 0

    for seg_i in range(len(seg_starts) - 1):
        _check_deadline(deadline_mono, cap_s, t0)
        seg_begin = seg_starts[seg_i]
        seg_end = seg_starts[seg_i + 1]
        if (seg_end - seg_begin) < 2:
            continue
        seg = std_history[seg_begin:seg_end]
        if _ds_ts_arr is not None:
            t_seg_start = seg[0].get("t")
            if t_seg_start is not None:
                diffs = np.abs(_ds_ts_arr - float(t_seg_start))
                best_k = int(np.argmin(diffs))
                _wall_seg_idx: Optional[int] = (
                    best_k if diffs[best_k] <= _ts_tol else None
                )
            else:
                _wall_seg_idx = None
        else:
            _wall_seg_idx = 0 if seg_i == 0 else None

        x, sx = _seed_state(
            est, model, layout, theta, seg[0],
            inject_wall=_wall_seg_idx is not None,
            wall_seg_idx=_wall_seg_idx,
            ntheta=ntheta, nx=nx,
        )
        P = np.eye(nx) * float(est._Q_var)
        sP = np.zeros((ntheta, nx, nx))

        for k in range(len(seg) - 1):
            _check_deadline(deadline_mono, cap_s, t0)
            rec_k = seg[k]
            rec_next = seg[k + 1]
            try:
                u_k = np.asarray(rec_k["u"], dtype=float)
                d_k = np.asarray(rec_k["d"], dtype=float)
                ym_k = np.asarray(rec_k["ym"], dtype=float)
                ym_next = np.asarray(rec_next["ym"], dtype=float)
            except (KeyError, TypeError, ValueError):
                break
            pin_idx, drop_idx, open_k = _open_masks(n, rec_k, rec_next, layout)
            d_step, ua_coeff = _ua_disturbance(n, layout, theta, d_k, ym_k, open_k)
            t_k = rec_k.get("t")
            t_n = rec_next.get("t")
            if t_k is not None and t_n is not None:
                actual_dt = float(t_n) - float(t_k)
                if actual_dt <= 0.0:
                    actual_dt = nominal_dt
            else:
                actual_dt = nominal_dt
            h_sub = actual_dt / n_sub

            remaining = len(seg) - 1 - k
            horizon = min(n_horizon, remaining)
            if k % origin_stride == 0 and horizon >= 1:
                x_ol = x.copy()
                sx_ol = sx.copy()
                for j in range(horizon):
                    rec_j = seg[k + j]
                    rec_jn = seg[k + j + 1]
                    try:
                        u_j = np.asarray(rec_j["u"], dtype=float)
                        d_j = np.asarray(rec_j["d"], dtype=float)
                        ym_j = np.asarray(rec_j["ym"], dtype=float)
                        ym_jn = np.asarray(rec_jn["ym"], dtype=float)
                    except (KeyError, TypeError, ValueError):
                        break
                    pin_j, drop_j, open_j = _open_masks(n, rec_j, rec_jn, layout)
                    d_j_step, ua_j = _ua_disturbance(n, layout, theta, d_j, ym_j, open_j)
                    tj = rec_j.get("t")
                    tn = rec_jn.get("t")
                    if tj is not None and tn is not None:
                        dt_j = float(tn) - float(tj)
                        if dt_j <= 0.0:
                            dt_j = nominal_dt
                    else:
                        dt_j = nominal_dt
                    adv = _implicit_mean(
                        est, model, quants, layout, theta, x_ol, sx_ol,
                        u_j, d_j_step, ua_j, ym_j, pin_j, dt_j / n_sub, n_sub,
                        ntheta, nx, with_p=False,
                    )
                    if adv is None:
                        return _SENTINEL, _zero.copy()
                    x_ol, sx_ol, _, _ = adv
                    residual = ym_jn - x_ol[:n]
                    if drop_j.size:
                        residual[drop_j] = 0.0
                    total_sse += float(np.dot(residual, residual))
                    total_grad -= 2.0 * (sx_ol[:, :n] @ residual)
                    n_steps_used += 1

            adv = _implicit_mean(
                est, model, quants, layout, theta, x, sx,
                u_k, d_step, ua_coeff, ym_k, pin_idx, h_sub, n_sub,
                ntheta, nx, with_p=True, P=P, sP=sP, dFdtheta=dFdtheta,
            )
            if adv is None:
                return _SENTINEL, _zero.copy()
            x, sx, P, sP = adv
            if P is None or sP is None:
                return _SENTINEL, _zero.copy()
            upd = _kalman_update(
                model, x, P, sx, sP, u_k, d_step, ym_next, drop_idx,
            )
            if upd is None:
                return _SENTINEL, _zero.copy()
            x, P, sx, sP = upd

    if n_steps_used == 0:
        return _SENTINEL, _zero.copy()
    scale = float(n * est._R_var)
    mse = total_sse / scale
    grad = total_grad / scale
    if not (np.isfinite(mse) and np.all(np.isfinite(grad))):
        return _SENTINEL, _zero.copy()
    return mse, grad


def nstep_path_rmse(
    est: Any,
    theta: np.ndarray,
    layout: _ThetaLayout,
    std_history: List[Dict[str, Any]],
    nominal_dt: float,
    *,
    n_horizon: int,
    origin_stride: int,
    dataset_start_ts: Optional[List[float]] = None,
) -> float:
    """RMSE of receding N-step air paths (same origins as the fit)."""
    mse, _ = nstep_pem_and_grad(
        est, theta, layout, std_history, nominal_dt,
        n_horizon=n_horizon, origin_stride=origin_stride,
        dataset_start_ts=dataset_start_ts, deadline_mono=None,
    )
    if mse >= _SENTINEL / 10.0:
        return float("nan")
    return float(math.sqrt(max(0.0, mse * float(est._R_var))))
