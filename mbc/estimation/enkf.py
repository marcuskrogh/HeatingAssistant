"""
Continuous-Discrete Ensemble Kalman Filter (Ph.D. Ch. 7.3).

The CD-EnKF maintains an ensemble of N particles that are each
propagated through the nonlinear SDE via Euler-Maruyama integration.
The ensemble mean and sample covariance replace the analytical
Gaussian approximation used by the EKF and UKF.

Prediction:
    Each particle x_i is integrated independently:
        x_i(t+dt) ≈ x_i(t) + f(x_i, u, d, p, t) dt + g(x_i, u, d, p, t) √dt w_i
    where w_i ~ N(0, Q_c).

Measurement update (perturbed observations):
    y_i = y + v_i,  v_i ~ N(0, R)
    K   = P_xy P_yy⁻¹        (estimated from ensemble cross-covariance)
    x_i ← x_i + K (y_i − h(x_i, u, d, p))

Reference:  Ph.D. thesis, Ch. 7.3.
"""

from __future__ import annotations

import numpy as np

from ..models import ContinuousDiscreteModel


class ContinuousDiscreteEnKF:
    """
    Continuous-Discrete Ensemble Kalman Filter (Ph.D. Ch. 7.3).

    Parameters
    ----------
    model : ContinuousDiscreteModel
        Nonlinear continuous-discrete system.
    x0 : (nx,) ndarray
        Initial state estimate (ensemble mean).
    P0 : (nx, nx) ndarray
        Initial covariance (used to draw the initial ensemble).
    dt : float
        Measurement sampling interval (seconds).
    N : int, optional
        Ensemble size.  Default: 100.
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
        L = np.linalg.cholesky(P0)
        Z = self._rng.standard_normal((nx, N))
        self._X = np.array(x0, dtype=float)[:, None] + L @ Z   # (nx, N)

    # ── Public properties ─────────────────────────────────────────────────

    @property
    def x_hat(self) -> np.ndarray:
        """Ensemble mean x̂ ∈ ℝⁿˣ (copy)."""
        return self._X.mean(axis=1).copy()

    @property
    def P(self) -> np.ndarray:
        """Ensemble sample covariance P ∈ ℝⁿˣˣⁿˣ (copy)."""
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
        Propagate all ensemble members from t to t + dt via Euler-Maruyama.

        Parameters
        ----------
        u : (nu,) ndarray      — control input.
        d : (nd,) ndarray      — disturbance.
        p : (nparams,) ndarray — parameter vector.
        t : float              — current time.

        Returns
        -------
        x_pred : (nx,) ensemble mean after propagation.
        P_pred : (nx, nx) ensemble sample covariance after propagation.
        """
        model = self._model
        h = self._h_sub
        sqrt_h = np.sqrt(h)
        nx = self._nx
        N = self._N

        # Diffusion: sigma encodes continuous-time noise magnitude; dw ~ N(0, I dt)
        x_mean0 = self._X.mean(axis=1)
        sigma_val = model.sigma(x_mean0, u, d, p, t)  # (nx, nw)
        nw = sigma_val.shape[1]

        t_j = t
        for _ in range(self._n_steps):
            # Standard-normal noise: (nw, N)
            W = self._rng.standard_normal((nw, N))
            # Stack f evaluations: (nx, N)
            F = np.column_stack([
                model.f(self._X[:, i], u, d, p, t_j) for i in range(N)
            ])
            # Euler-Maruyama: noise ~ N(0, Q * h) via sigma @ W
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
        Apply perturbed-observations ensemble measurement update.

        Parameters
        ----------
        y    : (ny,) ndarray      — observation.
        u    : (nu,) ndarray      — input at measurement time.
        d    : (nd,) ndarray      — disturbance at measurement time.
        p    : (nparams,) ndarray — parameter vector.
        mask : (ny,) bool ndarray, optional — active output mask.

        Returns
        -------
        x_hat : (nx,) updated ensemble mean.
        P     : (nx, nx) updated ensemble sample covariance.
        """
        model = self._model
        N = self._N
        R = model.Rm

        # Map ensemble through observation function — shape (ny, N)
        HX = np.column_stack([
            model.hm(self._X[:, i], u, d, p, 0.0) for i in range(N)
        ])

        # Apply mask
        if mask is not None:
            y = y[mask]
            HX = HX[mask, :]
            R = R[np.ix_(mask, mask)]

        ny_act = y.shape[0]

        # Ensemble anomalies in state and observation space
        x_mean = self._X.mean(axis=1, keepdims=True)   # (nx, 1)
        y_mean = HX.mean(axis=1, keepdims=True)         # (ny, 1)
        A  = (self._X - x_mean) / np.sqrt(N - 1)       # (nx, N)
        HA = (HX - y_mean)      / np.sqrt(N - 1)       # (ny, N)

        # Cross-covariance and innovation covariance
        Pxy = A @ HA.T          # (nx, ny)
        Pyy = HA @ HA.T + R     # (ny, ny)

        # Kalman gain
        K = Pxy @ np.linalg.solve(Pyy.T, np.eye(ny_act)).T   # (nx, ny)

        # Perturbed observations: shape (ny, N)
        V = self._rng.multivariate_normal(np.zeros(ny_act), R, size=N).T
        Y_pert = y[:, None] + V   # (ny, N)

        # Update each ensemble member
        for i in range(N):
            innov_i = Y_pert[:, i] - HX[:, i]
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
        """Combined predict + update step."""
        self.predict(u, d, p, t)
        return self.update(y, u, d, p, mask=mask)
