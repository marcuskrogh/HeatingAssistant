"""Gaussian MAP regularisation for grey-box parameter estimation."""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

import numpy as np

from .constants import (
    _MASS_PRIOR_WEIGHT,
    _SPLIT_PRIOR_STD,
    _T_WALL_MIN_LAM,
    _T_WALL_PRIOR_STD,
    _UA_OPEN_PRIOR_STD,
)
from .theta_layout import _ThetaLayout


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
    a, b = layout.idx_t_wall_init
    all_t_wall = theta[a:b]
    all_t_wall_prior = np.tile(est._t_wall_init_prior, layout.n_wall_segs)
    grad[a:b] = (
        2.0 * lam_tw * (all_t_wall - all_t_wall_prior)
        / (_T_WALL_PRIOR_STD ** 2)
    )

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
    # Wall initial temperatures (all segments): Gaussian prior toward
    # first air temp.  Uses lam_tw ≥ _T_WALL_MIN_LAM so even under the
    # default weak regularisation the parameters stay in a physically
    # plausible range.
    a, b = layout.idx_t_wall_init
    all_t_wall = theta[a:b]
    all_t_wall_prior = np.tile(est._t_wall_init_prior, layout.n_wall_segs)
    reg += lam_tw * float(
        np.sum((all_t_wall - all_t_wall_prior) ** 2)
    ) / (_T_WALL_PRIOR_STD ** 2)
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
