"""
Continuous-Discrete Particle Filter (CD-PF) for SDE systems
(ControlToolbox §State Estimation for Nonlinear SDE Systems —
*Continuous-Discrete Particle Filter*).

The CD-PF approximates the state distribution by a randomly sampled
particle set of size N_p.  The time update is identical to the CD-EnKF —
each particle is propagated independently through the full SDE via
Euler-Maruyama.  The measurement update replaces the Kalman correction
with **likelihood-weighted resampling**: each particle is scored by how
well its predicted measurement matches the observed one, and the
particle set is resampled proportionally to these scores using
**systematic resampling** (single uniform draw, equally-spaced
resampling points — minimal-variance, O(N_p)).

Initialisation
--------------
The particle set {x̂^{(i)}_{0|0}}_{i=1}^{N_p} is sampled from the prior;
in practice draw from N(x̂_{0|0}, P_{0|0}) when no analytic prior is
available.

Time update
-----------
For each particle i = 1, …, N_p, integrate the SDE

    dx̂_k^{(i)}(t) = f(x̂_k^{(i)}, u, d, p, t) dt
                  + sigma(x̂_k^{(i)}, u, d, p, t) dω_k^{(i)}(t)

with independent Wiener increments dω_k^{(i)} per particle.

Measurement update
------------------
Per-particle innovations e^{(i)} = y^m_k − hm(x̂_{k|k-1}^{(i)}, p) and
Gaussian likelihood weights

    w̃^{(i)} = (2π)^{-nym/2} |R|^{-1/2} exp(−½ (e^{(i)})ᵀ R⁻¹ e^{(i)}),
    w^{(i)} = w̃^{(i)} / Σ_j w̃^{(j)}.

Systematic resampling: draw q_1 ~ U[0, 1), construct N_p equally-spaced
resampling points q^{(i)} = ((i−1) + q_1) / N_p and select particles
according to the weight CDF.

The filtered statistics are sample mean and Bessel-corrected sample
covariance over the resampled particle set (uniform weights after
resampling).
"""

from __future__ import annotations

import numpy as np

from ..models import ContinuousDiscreteModel
from .._utils import _cholesky_psd
from ._ensemble import _ensemble_measurements, _propagate_em_ensemble


class ContinuousDiscreteParticleFilter:
    """
    Continuous-Discrete Particle Filter for SDE systems
    (ControlToolbox §SDE State Estimation — *CD-PF*).

    Parameters
    ----------
    model : ContinuousDiscreteModel
        Nonlinear continuous-discrete SDE system.
    x0 : (nx,) ndarray
        Initial state estimate (particle mean).
    P0 : (nx, nx) ndarray
        Initial covariance (used to draw the initial particle cloud).
    dt : float
        Measurement sampling interval (seconds).
    N : int, optional
        Number of particles N_p.  Default: 500.
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
        N: int = 500,
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

        # Initialise particles by sampling from N(x0, P0)
        L = _cholesky_psd(P0)
        Z = self._rng.standard_normal((nx, N))
        self._X = np.array(x0, dtype=float)[:, None] + L @ Z   # (nx, N)

    # ── Internal helpers ──────────────────────────────────────────────────

    @staticmethod
    def _systematic_resample(
        w: np.ndarray, rng: np.random.Generator
    ) -> np.ndarray:
        """
        Systematic resampling (ControlToolbox §SDE-CD-PF).  Single uniform
        draw q_1 ~ U[0, 1/N), equally-spaced resampling points
        q^{(i)} = (i-1)/N + q_1, weight CDF lookup.

        Parameters
        ----------
        w   : (N,) normalised weight array.
        rng : random Generator.

        Returns
        -------
        indices : (N,) int ndarray of resampled particle indices.
        """
        N = len(w)
        cumsum = np.cumsum(w)
        q1 = rng.uniform(0.0, 1.0 / N)
        positions = q1 + np.arange(N) / N
        indices = np.searchsorted(cumsum, positions)
        return np.clip(indices, 0, N - 1)

    # ── Public properties ─────────────────────────────────────────────────

    @property
    def x_hat(self) -> np.ndarray:
        """Particle sample mean x̂ ∈ ℝⁿˣ (copy)."""
        return self._X.mean(axis=1).copy()

    @property
    def P(self) -> np.ndarray:
        """Bessel-corrected sample covariance P ∈ ℝⁿˣˣⁿˣ (copy)."""
        N = self._N
        diff = self._X - self._X.mean(axis=1, keepdims=True)
        return (diff @ diff.T) / (N - 1)

    @property
    def particles(self) -> np.ndarray:
        """Particle matrix X ∈ ℝⁿˣˣᴺ (copy)."""
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
        Time update: propagate each particle independently through the
        full SDE via Euler-Maruyama with state-dependent diffusion.

        Parameters
        ----------
        u : (nu,) ndarray  — control input over [t, t+dt].
        d : (nd,) ndarray  — disturbance over [t, t+dt].
        p : (nparams,) ndarray  — parameter vector.
        t : float          — current time.

        Returns
        -------
        x_pred : (nx,) sample mean after propagation.
        P_pred : (nx, nx) Bessel-corrected sample covariance.
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
        Measurement update by likelihood-weighted systematic resampling.

        Parameters
        ----------
        y    : (nym,) ndarray  — observation.
        u    : (nu,) ndarray   — input at measurement time.
        d    : (nd,) ndarray   — disturbance at measurement time.
        p    : (nparams,) ndarray — parameter vector.
        mask : (nym,) bool ndarray, optional — active output mask.

        Returns
        -------
        x_hat : (nx,) updated sample mean.
        P     : (nx, nx) Bessel-corrected sample covariance.
        """
        model = self._model
        N = self._N
        R = model.Rm

        # Predicted measurements for each particle  (nym, N)
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

        # Per-particle log-likelihood (Gaussian with covariance R)
        sign, log_det = np.linalg.slogdet(R_sub)
        if sign <= 0:
            raise ValueError("Measurement covariance R is not positive definite.")
        log_norm = -0.5 * (y_sub.shape[0] * np.log(2.0 * np.pi) + log_det)
        # Pre-factor exp(log_norm) is the same for every particle and cancels
        # after normalisation; we keep it for log-likelihood interpretability.
        R_inv = np.linalg.inv(R_sub)
        log_w = np.empty(N)
        for i in range(N):
            innov = y_sub - Z[:, i]
            log_w[i] = log_norm - 0.5 * innov @ R_inv @ innov

        # Normalise via log-sum-exp for numerical stability
        log_w_max = log_w.max()
        w = np.exp(log_w - log_w_max)
        s = w.sum()
        if not np.isfinite(s) or s <= 0.0:
            # Degenerate likelihoods — keep the cloud untouched
            return self.x_hat, self.P
        w /= s

        # Systematic resampling (every step, per CD-PF spec)
        idx = self._systematic_resample(w, self._rng)
        self._X = self._X[:, idx]

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
