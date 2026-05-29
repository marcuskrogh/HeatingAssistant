"""
Continuous-Discrete Unscented Kalman Filter (CD-UKF) for SDE systems
(ControlToolbox §State Estimation for Nonlinear SDE Systems —
*Continuous-Discrete Unscented Kalman Filter*).

The CD-UKF represents the state distribution by deterministically sampled
sigma points (the unscented transform).  Sigma points are propagated
through the true nonlinear dynamics; predicted mean and covariance are
recovered as weighted statistics over the propagated set.  No Jacobian is
required.

Time update — augmented dimension n̄ = nx + nω
----------------------------------------------
Tuning parameters α ∈ ]0, 1], κ ≥ 0, β ≥ 0 (β = 2 optimal for Gaussian):

    c̄ = α² (n̄ + κ),    λ̄ = α² (n̄ + κ) − n̄

Weights for i ∈ {1, …, 2 n̄}:

    W̄_m^{(0)} = λ̄ / (n̄ + λ̄),
    W̄_c^{(0)} = λ̄ / (n̄ + λ̄) + 1 − α² + β,
    W̄_m^{(i)} = W̄_c^{(i)} = 1 / (2 (n̄ + λ̄)).

Two sets of sigma points are constructed.  Deterministic state set
(2 nx + 1 points capturing the state covariance):

    χ^{(0)} = x̂_{k|k},
    χ^{(i)} = x̂_{k|k} + √c̄ (√P_{k|k})_i,            i = 1, …, nx,
    χ^{(nx+i)} = x̂_{k|k} − √c̄ (√P_{k|k})_i,        i = 1, …, nx.

Stochastic noise sigma set (2 nω points all placed at the mean) with
structured deterministic noise increments

    Δω^{(2nx+i)}      = +√(c̄ Δt) e_i,    i = 1, …, nω
    Δω^{(2nx+nω+i)}   = −√(c̄ Δt) e_i,    i = 1, …, nω.

Propagation
^^^^^^^^^^^
The deterministic set is propagated through the drift ODE via explicit
Euler with sub-step h = dt/n_steps:

    χ^{(i)} ← χ^{(i)} + h f(χ^{(i)}, u, d, p, t),    i = 0, …, 2 nx.

The stochastic set (held fixed at the mean) is propagated through the
full SDE with its structured noise:

    χ^{(2nx+i)} ← χ^{(2nx+i)} + h f(χ^{(2nx+i)}, …)
                              + sigma(χ^{(2nx+i)}, …) Δω^{(2nx+i)} / √n_steps.

Predicted mean and covariance are weighted statistics over all 2 n̄ + 1
sigma points.

Measurement update — state dimension nx
---------------------------------------
A new sigma-point set of size 2 nx + 1 is generated from (x̂_{k|k-1},
P_{k|k-1}) using parameters c, λ, weights based only on nx:

    z^{m,(i)} = hm(χ^{(i)}, p),   ŷ^m_{k|k-1} = Σ_i W_m^{(i)} z^{m,(i)},
    R_zz   = Σ_i W_c^{(i)} (z^{m,(i)} − ŷ^m_{k|k-1})(…)ᵀ,
    R_e    = R_zz + R,
    R_xy   = Σ_i W_c^{(i)} (χ^{(i)} − x̂_{k|k-1})(z^{m,(i)} − ŷ^m_{k|k-1})ᵀ,
    K      = R_xy R_e⁻¹,
    x̂_{k|k} = x̂_{k|k-1} + K (y^m_k − ŷ^m_{k|k-1}),
    P_{k|k} = P_{k|k-1} − K R_e Kᵀ.
"""

from __future__ import annotations

import numpy as np

from ..models import ContinuousDiscreteModel
from .._utils import _cholesky_psd


def _sigma_weights(n: int, alpha: float, beta: float, kappa: float):
    """Compute scaled unscented sigma-point weights for dimension n."""
    c = alpha ** 2 * (n + kappa)
    lam = c - n
    Wm = np.full(2 * n + 1, 1.0 / (2.0 * (n + lam)))
    Wm[0] = lam / (n + lam)
    Wc = Wm.copy()
    Wc[0] = lam / (n + lam) + (1.0 - alpha ** 2 + beta)
    return c, lam, Wm, Wc


class ContinuousDiscreteUKF:
    """
    Continuous-Discrete Unscented Kalman Filter for SDE systems
    (ControlToolbox §SDE State Estimation — *CD-UKF*).

    Parameters
    ----------
    model : ContinuousDiscreteModel
        Nonlinear continuous-discrete SDE system.
    x0 : (nx,) ndarray
        Initial state estimate x̂_{0|0}.
    P0 : (nx, nx) ndarray
        Initial state covariance P_{0|0}.
    dt : float
        Measurement sampling interval (seconds).
    n_steps : int, optional
        Number of explicit-Euler integration sub-steps per measurement
        interval.  Default: 10.
    alpha : float, optional
        Sigma-point spread parameter α ∈ ]0, 1].  Default: 1.0.
    beta : float, optional
        Distribution parameter (β = 2 is optimal for a Gaussian).
        Default: 2.0.
    kappa : float, optional
        Secondary spread parameter κ ≥ 0.  Default: 0.
    """

    def __init__(
        self,
        model: ContinuousDiscreteModel,
        x0: np.ndarray,
        P0: np.ndarray,
        dt: float,
        n_steps: int = 10,
        alpha: float = 1.0,
        beta: float = 2.0,
        kappa: float = 0.0,
    ) -> None:
        self._model = model
        self._x = np.array(x0, dtype=float)
        self._P = np.array(P0, dtype=float)
        self._dt = dt
        self._n_steps = n_steps
        self._h_sub = dt / n_steps
        self._alpha = float(alpha)
        self._beta = float(beta)
        self._kappa = float(kappa)

        self._nx = self._x.shape[0]
        self._nw = model.nw

        # Augmented (time-update) weights: dimension nx + nw
        n_bar = self._nx + self._nw
        self._n_bar = n_bar
        c_bar, lam_bar, Wm_bar, Wc_bar = _sigma_weights(
            n_bar, alpha, beta, kappa
        )
        self._c_bar = c_bar
        self._lam_bar = lam_bar
        self._Wm_bar = Wm_bar
        self._Wc_bar = Wc_bar

        # State-only (measurement-update) weights: dimension nx
        c_x, lam_x, Wm_x, Wc_x = _sigma_weights(self._nx, alpha, beta, kappa)
        self._c_x = c_x
        self._lam_x = lam_x
        self._Wm_x = Wm_x
        self._Wc_x = Wc_x

    # ── Public properties ─────────────────────────────────────────────────

    @property
    def x_hat(self) -> np.ndarray:
        """Current state estimate x̂ ∈ ℝⁿˣ (copy)."""
        return self._x.copy()

    @property
    def P(self) -> np.ndarray:
        """Current state covariance P ∈ ℝⁿˣˣⁿˣ (copy)."""
        return self._P.copy()

    # ── Filter steps ──────────────────────────────────────────────────────

    def predict(
        self,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Time update via the augmented sigma-point scheme.

        Builds 2 nx + 1 deterministic state sigma points and 2 nω
        stochastic noise sigma points placed at the mean with structured
        deterministic Wiener increments, propagates each set, and
        reconstructs the predicted mean and covariance.

        Parameters
        ----------
        u : (nu,) ndarray  — control input applied over [t, t+dt].
        d : (nd,) ndarray  — disturbance over [t, t+dt].
        p : (nparams,) ndarray  — parameter vector.
        t : float          — current time.

        Returns
        -------
        x_pred : (nx,) predicted state estimate x̂_{k+1|k}.
        P_pred : (nx, nx) predicted covariance P_{k+1|k}.
        """
        model = self._model
        nx = self._nx
        nw = self._nw
        n_bar = self._n_bar
        h = self._h_sub

        # ── Build deterministic state sigma points (2 nx + 1) ──
        L = _cholesky_psd(self._P)              # P = L L^T
        sqrt_c = np.sqrt(self._c_bar)
        det_sigma = np.empty((2 * nx + 1, nx))
        det_sigma[0] = self._x
        for i in range(nx):
            det_sigma[i + 1]      = self._x + sqrt_c * L[:, i]
            det_sigma[nx + i + 1] = self._x - sqrt_c * L[:, i]

        # ── Build stochastic sigma set (all at mean) ──
        # 2 nw points; the structured noise increments are stored separately
        # and applied per-sub-step (the structured Wiener increment over
        # [t_k, t_{k+1}] is split equally across n_steps so that the
        # accumulated Brownian increment matches √(c̄ dt) e_i).
        stoch_sigma = np.tile(self._x, (2 * nw, 1))
        I_nw = np.eye(nw)
        # Per-sub-step Wiener increment: ±√(c̄ h) e_i  (so √(c̄ dt) e_i
        # accumulates linearly when added each sub-step? No — Brownian
        # increments add as Σ Δω_n with E[(Δω)^2] = h, so to get a single
        # Δω = √(c̄ dt) e_i we apply it once.  Here we follow the spec
        # which propagates the stochastic sigma points through the SDE
        # with deterministic noise increments d ω^(.) (t).  We integrate
        # a deterministic Wiener path that has total increment √(c̄ dt) e_i
        # by setting the per-sub-step increment to √(c̄ dt) e_i / n_steps,
        # which makes the cumulative Wiener increment √(c̄ dt) e_i.)
        per_step_pos = np.sqrt(self._c_bar * self._dt) / self._n_steps * I_nw
        per_step_neg = -per_step_pos
        # Stack: rows i = 1..nw are positive, rows nw+1..2nw are negative
        # Each row gives the per-sub-step Wiener increment vector for that
        # stochastic sigma point.
        stoch_d_omega = np.concatenate([per_step_pos, per_step_neg], axis=0)  # (2nw, nw)

        # ── Propagate ──
        t_j = t
        for _ in range(self._n_steps):
            # Deterministic set: drift only
            for i in range(2 * nx + 1):
                f_i = model.f(det_sigma[i], u, d, p, t_j)
                det_sigma[i] = det_sigma[i] + h * f_i

            # Stochastic set: drift + structured noise
            for i in range(2 * nw):
                xi = stoch_sigma[i]
                f_i = model.f(xi, u, d, p, t_j)
                sigma_i = model.sigma(xi, u, d, p, t_j)
                stoch_sigma[i] = xi + h * f_i + sigma_i @ stoch_d_omega[i]

            t_j += h

        # ── Reconstruct mean and covariance over all 2 n̄ + 1 sigma points ──
        # Order: deterministic[0] (mean), deterministic[1..2nx], stochastic[0..2nw-1]
        all_sigma = np.concatenate([det_sigma, stoch_sigma], axis=0)  # (2n_bar+1, nx)

        x_pred = np.einsum("i,ij->j", self._Wm_bar, all_sigma)
        P_pred = np.zeros((nx, nx))
        for i in range(2 * n_bar + 1):
            diff = all_sigma[i] - x_pred
            P_pred += self._Wc_bar[i] * np.outer(diff, diff)
        P_pred = 0.5 * (P_pred + P_pred.T)

        self._x = x_pred
        self._P = P_pred
        return x_pred.copy(), P_pred.copy()

    def update(
        self,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        mask: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Measurement update via the standard (state-only) unscented
        transform.  No augmentation with noise — measurement noise enters
        only through the additive Rm term in the innovation covariance.

        Parameters
        ----------
        y    : (nym,) ndarray  — observation.
        u    : (nu,) ndarray   — input at measurement time.
        d    : (nd,) ndarray   — disturbance at measurement time.
        p    : (nparams,) ndarray — parameter vector.
        mask : (nym,) bool ndarray, optional — active output mask.

        Returns
        -------
        x_hat : (nx,) corrected state estimate x̂_{k|k}.
        P     : (nx, nx) corrected covariance P_{k|k}.
        """
        model = self._model
        nx = self._nx
        R = model.Rm

        # Generate state-only sigma points from (x̂_{k|k-1}, P_{k|k-1})
        L = _cholesky_psd(self._P)
        sqrt_c = np.sqrt(self._c_x)
        sigma = np.empty((2 * nx + 1, nx))
        sigma[0] = self._x
        for i in range(nx):
            sigma[i + 1]      = self._x + sqrt_c * L[:, i]
            sigma[nx + i + 1] = self._x - sqrt_c * L[:, i]

        # Map sigma points through measurement function
        Z = np.array([
            model.hm(sigma[i], u, d, p, 0.0) for i in range(2 * nx + 1)
        ])  # (2nx+1, nym)

        if mask is not None:
            active = np.where(mask)[0]
            if len(active) == 0:
                return self._x.copy(), self._P.copy()
            Z = Z[:, active]
            y_sub = y[active]
            R_sub = R[np.ix_(active, active)]
        else:
            y_sub = y
            R_sub = R

        # Predicted measurement mean
        y_pred = np.einsum("i,ij->j", self._Wm_x, Z)

        ny_act = y_pred.shape[0]
        R_zz = np.zeros((ny_act, ny_act))
        R_xy = np.zeros((nx, ny_act))
        for i in range(2 * nx + 1):
            dy = Z[i] - y_pred
            dx = sigma[i] - self._x
            R_zz += self._Wc_x[i] * np.outer(dy, dy)
            R_xy += self._Wc_x[i] * np.outer(dx, dy)
        R_e = R_zz + R_sub

        # Kalman gain  K = R_xy R_e⁻¹
        K = np.linalg.solve(R_e.T, R_xy.T).T

        # State + covariance updates (CD-UKF spec form: P = P − K R_e Kᵀ)
        innov = y_sub - y_pred
        x_new = self._x + K @ innov
        P_new = self._P - K @ R_e @ K.T
        P_new = 0.5 * (P_new + P_new.T)

        self._x = x_new
        self._P = P_new
        return x_new.copy(), P_new.copy()

    def step(
        self,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
        mask: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Combined time + measurement update."""
        self.predict(u, d, p, t)
        return self.update(y, u, d, p, mask=mask)
