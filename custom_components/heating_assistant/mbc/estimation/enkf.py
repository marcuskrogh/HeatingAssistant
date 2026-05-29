"""
Continuous-Discrete Ensemble Kalman Filter (CD-EnKF) for SDE systems
(ControlToolbox §State Estimation for Nonlinear SDE Systems —
*Continuous-Discrete Ensemble Kalman Filter*).

The CD-EnKF approximates the state distribution by a randomly sampled
ensemble of N_p particles.  Each particle is propagated independently
through the full stochastic dynamics with its own realisation of the
Wiener process.  Statistics (mean, covariance, cross-covariance) are
computed as Bessel-corrected sample averages.  The measurement update
applies the Kalman gain to each particle individually using a perturbed
measurement so that the ensemble spread is consistent with the posterior
covariance.

Initialisation
--------------
The ensemble {x̂^{(i)}_{0|0}}_{i=1}^{N_p} is sampled from the prior; when
no analytic prior is available, draw from N(x̂_{0|0}, P_{0|0}).

Time update — per particle Euler-Maruyama
-----------------------------------------
For each i = 1, …, N_p, integrate

    dx̂_k^{(i)}(t) = f(x̂_k^{(i)}, u, d, p, t) dt
                  + sigma(x̂_k^{(i)}, u, d, p, t) dω_k^{(i)}(t)

with independent standard Wiener increments dω_k^{(i)} per particle.
The discretisation uses ``n_steps`` Euler-Maruyama sub-steps with
state-dependent diffusion evaluated at each particle.

Predicted statistics (Bessel-corrected):

    x̂_{k+1|k} = (1/N_p) Σ_i x̂_{k+1|k}^{(i)}
    P_{k+1|k} = (1/(N_p−1)) Σ_i (x̂_{k+1|k}^{(i)} − x̂_{k+1|k})(…)ᵀ

Measurement update — perturbed observations
-------------------------------------------
Predicted measurement ensemble z^{m,(i)} = hm(x̂_{k|k-1}^{(i)}, p), sample
statistics ŷ^m_{k|k-1}, R_zz, R_xy with Bessel correction, then

    R_e = R_zz + R,
    K   = R_xy R_e⁻¹,
    y^{m,(i)}_k = y^m_k + v_k^{(i)},   v_k^{(i)} ~ N(0, R),
    x̂_{k|k}^{(i)} = x̂_{k|k-1}^{(i)} + K (y^{m,(i)}_k − z^{m,(i)}).

The per-particle perturbation prevents ensemble collapse — without it the
ensemble covariance shrinks by the deterministic factor (I − K C) and
underestimates the posterior covariance.
"""

from __future__ import annotations

import numpy as np

from ..models import ContinuousDiscreteModel
from .._utils import _cholesky_psd
from ._ensemble import _ensemble_measurements, _propagate_em_ensemble


class ContinuousDiscreteEnKF:
    """
    Continuous-Discrete Ensemble Kalman Filter for SDE systems
    (ControlToolbox §SDE State Estimation — *CD-EnKF*).

    Parameters
    ----------
    model : ContinuousDiscreteModel
        Nonlinear continuous-discrete SDE system.
    x0 : (nx,) ndarray
        Initial state estimate (ensemble mean).
    P0 : (nx, nx) ndarray
        Initial covariance (used to draw the initial ensemble).
    dt : float
        Measurement sampling interval (seconds).
    N : int, optional
        Ensemble size N_p.  Default: 100.
    n_steps : int, optional
        Euler-Maruyama sub-steps per measurement interval.  Default: 10.
    seed : int or None, optional
        Random seed for reproducibility.
    """

    def __init__(
        self,
        model: ContinuousDiscreteModel,
        x0: np.ndarray,
        P0: np.ndarray,
        dt: float,
        N: int = 100,
        n_steps: int = 10,
        seed: int | None = None,
    ) -> None:
        self._model = model
        self._dt = dt
        self._N = N
        self._n_steps = n_steps
        self._h_sub = dt / n_steps
        self._rng = np.random.default_rng(seed)

        nx = len(x0)
        self._nx = nx

        # Initialise ensemble by sampling from N(x0, P0)
        L = _cholesky_psd(P0)
        Z = self._rng.standard_normal((nx, N))
        self._X = np.array(x0, dtype=float)[:, None] + L @ Z   # (nx, N)

    # ── Public properties ─────────────────────────────────────────────────

    @property
    def x_hat(self) -> np.ndarray:
        """Ensemble mean x̂ ∈ ℝⁿˣ (copy)."""
        return self._X.mean(axis=1).copy()

    @property
    def P(self) -> np.ndarray:
        """Bessel-corrected ensemble sample covariance P ∈ ℝⁿˣˣⁿˣ (copy)."""
        A = self._X - self._X.mean(axis=1, keepdims=True)
        return (A @ A.T) / (self._N - 1)

    @property
    def ensemble(self) -> np.ndarray:
        """Full ensemble matrix X ∈ ℝⁿˣˣᴺ (copy)."""
        return self._X.copy()

    # ── Filter steps ──────────────────────────────────────────────────────

    def predict(
        self,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Time update: propagate each ensemble member independently through
        the full SDE via Euler-Maruyama with state-dependent diffusion.

        Parameters
        ----------
        u : (nu,) ndarray  — control input over [t, t+dt].
        d : (nd,) ndarray  — disturbance over [t, t+dt].
        p : (nparams,) ndarray  — parameter vector.
        t : float          — current time.

        Returns
        -------
        x_pred : (nx,) ensemble mean after propagation.
        P_pred : (nx, nx) Bessel-corrected ensemble covariance.
        """
        self._X = _propagate_em_ensemble(
            self._model, self._X, u, d, p, t,
            h=self._h_sub, n_steps=self._n_steps, rng=self._rng,
        )
        return self.x_hat, self.P

    def update(
        self,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        mask: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Measurement update via perturbed-observations Kalman correction
        applied per particle.

        Parameters
        ----------
        y    : (nym,) ndarray  — observation.
        u    : (nu,) ndarray   — input at measurement time.
        d    : (nd,) ndarray   — disturbance at measurement time.
        p    : (nparams,) ndarray — parameter vector.
        mask : (nym,) bool ndarray, optional — active output mask.

        Returns
        -------
        x_hat : (nx,) updated ensemble mean.
        P     : (nx, nx) Bessel-corrected ensemble covariance.
        """
        model = self._model
        N = self._N
        R = model.Rm

        # Predicted measurement ensemble  (nym, N)
        Z = _ensemble_measurements(model, self._X, u, d, p)

        if mask is not None:
            active = np.where(mask)[0]
            if len(active) == 0:
                return self.x_hat, self.P
            y_sub = y[active]
            Z = Z[active, :]
            R_sub = R[np.ix_(active, active)]
        else:
            y_sub = y
            R_sub = R

        ny_act = y_sub.shape[0]

        # Sample anomalies (Bessel-corrected via division by √(N − 1))
        x_mean = self._X.mean(axis=1, keepdims=True)        # (nx, 1)
        z_mean = Z.mean(axis=1, keepdims=True)              # (nym, 1)
        A_x = (self._X - x_mean) / np.sqrt(N - 1)           # (nx, N)
        A_z = (Z - z_mean) / np.sqrt(N - 1)                 # (nym, N)

        R_xy = A_x @ A_z.T          # (nx, nym)
        R_zz = A_z @ A_z.T          # (nym, nym)
        R_e = R_zz + R_sub

        # Single Kalman gain shared across all particles
        K = np.linalg.solve(R_e.T, R_xy.T).T                 # (nx, nym)

        # Perturbed measurements per particle
        V = self._rng.multivariate_normal(np.zeros(ny_act), R_sub, size=N).T  # (nym, N)
        Y_pert = y_sub[:, None] + V

        for i in range(N):
            innov_i = Y_pert[:, i] - Z[:, i]
            self._X[:, i] = self._X[:, i] + K @ innov_i

        return self.x_hat, self.P

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
