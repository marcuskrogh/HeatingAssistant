"""Gaussian MAP regularisation for grey-box parameter estimation."""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

import numpy as np

from ..wall_physics import apply_wall_ss_param_grad, wall_ss_partials
from .constants import (
    _MASS_PRIOR_WEIGHT,
    _SPLIT_PRIOR_STD,
    _T_WALL_MIN_LAM,
    _T_WALL_PRIOR_STD,
    _UA_OPEN_PRIOR_STD,
)
from .model_build import _theta_model_quantities
from .theta_layout import _ThetaLayout


def tw0_ss_means(est: Any, layout: _ThetaLayout, theta: np.ndarray) -> np.ndarray:
    """MAP mean for each ``t_wall_init`` entry: algebraic ``T_w^ss(θ)`` at anchors."""
    n = layout.n_rooms
    n_segs = layout.n_wall_segs
    fallback = np.tile(np.asarray(est._t_wall_init_prior, dtype=float), n_segs)
    ta = getattr(est, "_tw0_ta", None)
    if ta is None:
        return fallback
    tout = np.asarray(getattr(est, "_tw0_tout", np.array([])), dtype=float).ravel()
    qsol = getattr(est, "_tw0_qsol", None)
    quants = _theta_model_quantities(est, layout, theta)
    means = np.empty(n * n_segs, dtype=float)
    ta_arr = np.asarray(ta, dtype=float)
    for s in range(n_segs):
        ta_s = ta_arr[s] if ta_arr.ndim == 2 else ta_arr
        to_s = float(tout[s]) if s < tout.size else (
            float(tout[-1]) if tout.size else 20.0
        )
        qs = None
        if qsol is not None:
            q_arr = np.asarray(qsol, dtype=float)
            qs = q_arr[s] if q_arr.ndim == 2 else q_arr
        mu, _drf, _dlogr, _dlogs = wall_ss_partials(quants, ta_s, to_s, qs)
        means[s * n:(s + 1) * n] = mu[:n]
    return means


def _tw0_map_value_and_grad(
    est: Any,
    theta: np.ndarray,
    layout: _ThetaLayout,
    lam_tw: float,
    grad: Optional[np.ndarray] = None,
) -> Tuple[float, Optional[np.ndarray]]:
    """Gaussian MAP of Tw0 toward ``T_w^ss(θ)``; optional in-place *grad*."""
    a, b = layout.idx_t_wall_init
    tw = np.asarray(theta[a:b], dtype=float)
    mu = tw0_ss_means(est, layout, theta)
    if mu.size != tw.size:
        mu = np.resize(mu, tw.size)
    err = tw - mu
    sigma2 = float(_T_WALL_PRIOR_STD) ** 2
    reg = float(lam_tw) * float(np.sum(err ** 2)) / sigma2
    if grad is None:
        return reg, None
    scale_vec = (2.0 * float(lam_tw) / sigma2) * err
    grad[a:b] = grad[a:b] + scale_vec
    ta = getattr(est, "_tw0_ta", None)
    if ta is None:
        return reg, grad
    n = layout.n_rooms
    n_segs = layout.n_wall_segs
    quants = _theta_model_quantities(est, layout, theta)
    tout = np.asarray(getattr(est, "_tw0_tout", np.array([])), dtype=float).ravel()
    qsol = getattr(est, "_tw0_qsol", None)
    ta_arr = np.asarray(ta, dtype=float)
    for s in range(n_segs):
        ta_s = ta_arr[s] if ta_arr.ndim == 2 else ta_arr
        to_s = float(tout[s]) if s < tout.size else (
            float(tout[-1]) if tout.size else 20.0
        )
        qs = None
        if qsol is not None:
            q_arr = np.asarray(qsol, dtype=float)
            qs = q_arr[s] if q_arr.ndim == 2 else q_arr
        _mu, dmu_drf, dmu_dlogr, dmu_dlogs = wall_ss_partials(
            quants, ta_s, to_s, qs,
        )
        for i in range(n):
            idx = s * n + i
            if idx >= scale_vec.size:
                break
            apply_wall_ss_param_grad(
                grad, float(scale_vec[idx]), i,
                dmu_drf, dmu_dlogr, dmu_dlogs, layout,
            )
    return reg, grad


def _compute_regularization_gradient(
    est: Any,
    theta: np.ndarray,
    layout: _ThetaLayout,
    identifiable_pairs: List[Tuple[int, int]],
) -> np.ndarray:
    """
    Return ∂reg/∂θ where reg(θ) is the Gaussian regularisation term
    from :func:`_compute_regularization_theta`.
    """
    (log_mass, log_r, q_int, t_wall_init, log_alpha, log_r_ij,
     log_solar, c_air, r_aw) = layout.unpack(theta)
    lam = est._regularization
    grad = np.zeros_like(theta)

    a, b = layout.idx_log_mass
    w_mass = float(getattr(est, "_mass_prior_weight", _MASS_PRIOR_WEIGHT))
    grad[a:b] = 2.0 * lam * w_mass * (log_mass - est._log_mass_prior)

    a, b = layout.idx_log_r
    grad[a:b] = 2.0 * lam * (log_r - est._log_r_prior)

    a, b = layout.idx_q_int
    grad[a:b] = 2.0 * lam * (q_int - est._q_int_prior) / (100.0 ** 2)

    lam_tw = max(lam, _T_WALL_MIN_LAM)
    _tw0_map_value_and_grad(est, theta, layout, lam_tw, grad=grad)

    a, b = layout.idx_log_alpha
    if a < b:
        la_prior = np.array(
            [est._log_alpha_prior_full[s] for s in layout.identifiable_sources]
        )
        grad[a:b] = 2.0 * lam * est._alpha_prior_weight * (log_alpha - la_prior)

    a, b = layout.idx_log_r_ij
    if a < b:
        r_priors = np.array([
            est._connection_r_priors[est._connection_pairs.index(p)]
            for p in identifiable_pairs
        ])
        grad[a:b] = 2.0 * lam * (log_r_ij - r_priors)

    a, b = layout.idx_log_solar
    if a < b:
        s_prior = np.array([
            est._log_solar_prior_full[i] for i in layout.identifiable_solar
        ])
        grad[a:b] = 2.0 * lam * (log_solar - s_prior)

    a, b = layout.idx_c_air
    if a < b:
        ca_prior = np.array([
            est._c_air_prior_full[i] for i in layout.identifiable_splits
        ])
        grad[a:b] = 2.0 * lam * (c_air - ca_prior) / (_SPLIT_PRIOR_STD ** 2)

    a, b = layout.idx_r_aw
    if a < b:
        ra_prior = np.array([
            est._r_aw_prior_full[i] for i in layout.identifiable_splits
        ])
        grad[a:b] = 2.0 * lam * (r_aw - ra_prior) / (_SPLIT_PRIOR_STD ** 2)

    a, b = layout.idx_ua_open
    if a < b:
        ua = layout.get_ua_open(theta)
        ua_prior = np.array(
            [est._ua_open_prior_full[i] for i in layout.identifiable_ua]
        )
        grad[a:b] = 2.0 * lam * (ua - ua_prior) / (_UA_OPEN_PRIOR_STD ** 2)

    return grad


def _compute_regularization(
    est: Any,
    log_mass: np.ndarray,
    log_r: np.ndarray,
    q_int: np.ndarray,
    log_alpha: np.ndarray,
    log_r_ij: np.ndarray,
    identifiable_pairs: List[Tuple[int, int]],
    identifiable_sources: Optional[List[int]] = None,
) -> float:
    """Gaussian regularisation toward priors for all parameters.

    Internal-gain and α priors use unit-scale weights; the linear-space
    q_int penalty is divided by 100² so the prior std corresponds to
    ~100 W rather than 1 W.
    """
    if identifiable_sources is not None and len(log_alpha):
        log_alpha_prior = np.array(
            [est._log_alpha_prior_full[s] for s in identifiable_sources]
        )
    else:
        log_alpha_prior = np.array([]) if not len(log_alpha) else np.array(
            [est._log_alpha_prior_full[s] for s in range(len(log_alpha))]
        )

    r_ij_priors = np.array([
        est._connection_r_priors[est._connection_pairs.index(p)]
        for p in identifiable_pairs
    ]) if identifiable_pairs else np.array([])

    w_mass = float(getattr(est, "_mass_prior_weight", _MASS_PRIOR_WEIGHT))
    reg = est._regularization * (
        w_mass * float(np.sum((log_mass - est._log_mass_prior) ** 2))
        + float(np.sum((log_r - est._log_r_prior) ** 2))
        + float(np.sum((q_int - est._q_int_prior) ** 2)) / (100.0 ** 2)
    )
    if len(log_alpha):
        reg += est._regularization * est._alpha_prior_weight * float(
            np.sum((log_alpha - log_alpha_prior) ** 2)
        )
    if len(log_r_ij):
        reg += est._regularization * float(
            np.sum((log_r_ij - r_ij_priors) ** 2)
        )
    return reg


def _compute_regularization_theta(
    est: Any,
    theta: np.ndarray,
    layout: _ThetaLayout,
) -> float:
    """Gaussian regularisation toward priors for the full θ vector.

    Wraps :func:`_compute_regularization` (the always-present blocks)
    and adds the gated blocks: unit-scale priors for the log solar
    scale, and tight ``_SPLIT_PRIOR_STD`` priors for the linear-space
    envelope split fractions.
    """
    (log_mass, log_r, q_int, t_wall_init, log_alpha, log_r_ij,
     log_solar, c_air, r_aw) = layout.unpack(theta)
    reg = _compute_regularization(
        est,
        log_mass, log_r, q_int, log_alpha, log_r_ij,
        layout.identifiable_pairs, layout.identifiable_sources,
    )
    lam = est._regularization
    lam_tw = max(lam, _T_WALL_MIN_LAM)
    # Wall initial temperatures: Gaussian MAP toward algebraic T_w^ss(θ)
    # at the dataset-start air/outdoor/solar anchors (not a clip box).
    tw_reg, _ = _tw0_map_value_and_grad(est, theta, layout, lam_tw)
    reg += tw_reg
    if len(log_solar):
        prior = np.array([
            est._log_solar_prior_full[i] for i in layout.identifiable_solar
        ])
        reg += lam * float(np.sum((log_solar - prior) ** 2))
    if len(c_air):
        ca_prior = np.array([
            est._c_air_prior_full[i] for i in layout.identifiable_splits
        ])
        ra_prior = np.array([
            est._r_aw_prior_full[i] for i in layout.identifiable_splits
        ])
        reg += lam * float(
            np.sum((c_air - ca_prior) ** 2)
            + np.sum((r_aw - ra_prior) ** 2)
        ) / (_SPLIT_PRIOR_STD ** 2)
    ua = layout.get_ua_open(theta)
    if len(ua):
        ua_prior = np.array(
            [est._ua_open_prior_full[i] for i in layout.identifiable_ua]
        )
        reg += lam * float(np.sum((ua - ua_prior) ** 2)) / (
            _UA_OPEN_PRIOR_STD ** 2
        )
    return reg
