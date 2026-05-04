"""
Continuous-Discrete Particle Filter (Ph.D. Ch. 7.4).

The CD-PF is a sequential Monte Carlo estimator.  N weighted particles
approximate the posterior distribution of the state.

Prediction:
    Each particle x_i is propagated through the nonlinear SDE via
    Euler-Maruyama integration (same scheme as ContinuousDiscreteEnKF).

Measurement update:
    Weights w_i are multiplied by the likelihood p(y | x_i):
        w_i ← w_i · p(y | x_i)   where   p(y | x_i) ∝ exp(-½ ‖y - h(x_i,d)‖²_R⁻¹)
    Weights are normalised.  Systematic resampling is applied when the
    effective sample size N_eff = (Σ w_i²)⁻¹ falls below threshold * N.

Reference:  Ph.D. thesis, Ch. 7.4.
"""

from __future__ import annotations

import numpy as np

from ..models import ContinuousDiscreteModel


class ContinuousDiscreteParticleFilter:
    """
    Continuous-Discrete Particle Filter (Ph.D. Ch. 7.4).

    Parameters
    ----------
    model : ContinuousDiscreteModel
        Nonlinear continuous-discrete system.
    x0 : (nx,) ndarray
        Initial state estimate (particle mean).
    P0 : (nx, nx) ndarray
        Initial covariance (used to draw the initial particle cloud).
    dt : float
        Measurement sampling interval (seconds).
    N : int, optional
        Number of particles.  Default: 500.
    n_steps : int, optional
        Euler-Maruyama sub-steps per measurement interval.  Default: 10.
    resample_threshold : float, optional
        Effective sample size fraction below which systematic resampling
        is triggered.  Default: 0.5 (i.e. N_eff < N/2).
    seed : int or None, optional
        Random seed for reproducibility.
    """

    def __init__(
        self,
        model: ContinuousDiscreteModel,
        x0: np.ndarray,
        P0: np.ndarray,
        dt: float,
        N: int = 500,
        n_steps: int = 10,
        resample_threshold: float = 0.5,
        seed: int | None = None,
    ) -> None:
        self._model = model
        self._dt = dt
        self._N = N
        self._n_steps = n_steps
        self._h_sub = dt / n_steps
        self._resample_threshold = resample_threshold
        self._rng = np.random.default_rng(seed)

        nx = len(x0)
        self._nx = nx

        # Initialise particles by sampling from N(x0, P0)
        L = np.linalg.cholesky(P0)
        Z = self._rng.standard_normal((nx, N))
        self._X = np.array(x0, dtype=float)[:, None] + L @ Z   # (nx, N)
        self._w = np.full(N, 1.0 / N)   # uniform weights

    # ── Internal helpers ──────────────────────────────────────────────────

    @staticmethod
    def _systematic_resample(
        w: np.ndarray, rng: np.random.Generator
    ) -> np.ndarray:
        """
        Systematic resampling.  Returns an index array of length N.

        Parameters
        ----------
        w   : (N,) normalised weight array.
        rng : random Generator.

        Returns
        -------
        indices : (N,) int ndarray.
        """
        N = len(w)
        cumsum = np.cumsum(w)
        u0 = rng.uniform(0.0, 1.0 / N)
        positions = u0 + np.arange(N) / N
        indices = np.searchsorted(cumsum, positions)
        return np.clip(indices, 0, N - 1)

    # ── Public properties ─────────────────────────────────────────────────

    @property
    def x_hat(self) -> np.ndarray:
        """Weighted particle mean x̂ ∈ ℝⁿˣ (copy)."""
        return (self._X * self._w[None, :]).sum(axis=1).copy()

    @property
    def P(self) -> np.ndarray:
        """Weighted particle covariance P ∈ ℝⁿˣˣⁿˣ (copy)."""
        mu = self.x_hat
        diff = self._X - mu[:, None]   # (nx, N)
        return (diff * self._w[None, :]) @ diff.T

    @property
    def particles(self) -> np.ndarray:
        """Particle matrix X ∈ ℝⁿˣˣᴺ (copy)."""
        return self._X.copy()

    @property
    def weights(self) -> np.ndarray:
        """Normalised particle weights w ∈ ℝᴺ (copy)."""
        return self._w.copy()

    # ── Filter steps ──────────────────────────────────────────────────────

    def predict(
        self,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Propagate all particles from t to t + dt via Euler-Maruyama.

        Parameters
        ----------
        u : (nu,) ndarray      — control input.
        d : (nd,) ndarray      — disturbance.
        p : (nparams,) ndarray — parameter vector.
        t : float              — current time.

        Returns
        -------
        x_pred : (nx,) weighted mean after propagation.
        P_pred : (nx, nx) weighted covariance after propagation.
        """
        model = self._model
        h = self._h_sub
        sqrt_h = np.sqrt(h)
        N = self._N

        # Diffusion: sigma encodes full noise magnitude, sigma @ sigma^T = Q
        x_mean0 = self._X.mean(axis=1)
        sigma_val = model.sigma(x_mean0, u, d, p, t)  # (nx, nw)
        nw = sigma_val.shape[1]

        t_j = t
        for _ in range(self._n_steps):
            W = self._rng.standard_normal((nw, N))
            # Stack f evaluations: (nx, N)
            F = np.column_stack([
                model.f(self._X[:, i], u, d, p, t_j) for i in range(N)
            ])
            self._X = self._X + h * F + sigma_val @ (sqrt_h * W)
            t_j += h

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
        Update particle weights by the observation likelihood, then resample
        if the effective sample size is below the threshold.

        Parameters
        ----------
        y    : (ny,) ndarray      — observation.
        u    : (nu,) ndarray      — input at measurement time.
        d    : (nd,) ndarray      — disturbance at measurement time.
        p    : (nparams,) ndarray — parameter vector.
        mask : (ny,) bool ndarray, optional — active output mask.

        Returns
        -------
        x_hat : (nx,) updated weighted mean.
        P     : (nx, nx) updated weighted covariance.
        """
        model = self._model
        N = self._N
        R = model.Rm

        # Predicted observations for each particle
        HX = np.column_stack([
            model.hm(self._X[:, i], u, d, p, 0.0) for i in range(N)
        ])   # (ny, N)

        # Apply mask
        if mask is not None:
            y = y[mask]
            HX = HX[mask, :]
            R = R[np.ix_(mask, mask)]

        # Log-likelihood for each particle
        R_inv = np.linalg.inv(R)
        log_w = np.empty(N)
        for i in range(N):
            innov_i = y - HX[:, i]
            log_w[i] = -0.5 * innov_i @ R_inv @ innov_i

        # Update and normalise weights in log-space for numerical stability
        log_w_new = np.log(self._w + 1e-300) + log_w
        log_w_new -= log_w_new.max()   # shift for stability
        w_new = np.exp(log_w_new)
        w_new /= w_new.sum()
        self._w = w_new

        # Systematic resampling when N_eff < threshold * N
        N_eff = 1.0 / np.sum(self._w ** 2)
        if N_eff < self._resample_threshold * N:
            idx = self._systematic_resample(self._w, self._rng)
            self._X = self._X[:, idx]
            self._w = np.full(N, 1.0 / N)

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
        """Combined predict + update step."""
        self.predict(u, d, p, t)
        return self.update(y, u, d, p, mask=mask)
