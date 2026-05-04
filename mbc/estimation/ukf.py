"""
Continuous-Discrete Unscented Kalman Filter (Ph.D. Ch. 7.2).

The CD-UKF replaces the linearisation used by the CD-EKF with a
deterministic sigma-point approximation (unscented transform).

Prediction:
    2 nx + 1 sigma points are generated from the current estimate and
    covariance.  Each point is propagated individually through the
    nonlinear drift ODE (Euler, n_steps sub-steps).  The predicted mean
    and covariance are recovered from the weighted point cloud plus the
    integrated process-noise term G Q_c Gᵀ dt.

Measurement update:
    A new sigma-point set is generated from (x̂⁻, P⁻), mapped through h,
    and the standard cross-covariance / innovation expressions give K and
    the posterior.

Reference:  Ph.D. thesis, Ch. 7.2.
"""

from __future__ import annotations

import numpy as np

from ..models import ContinuousDiscreteModel


class ContinuousDiscreteUKF:
    """
    Continuous-Discrete Unscented Kalman Filter (Ph.D. Ch. 7.2).

    Parameters
    ----------
    model : ContinuousDiscreteModel
        Nonlinear continuous-discrete system.
    x0 : (nx,) ndarray
        Initial state estimate.
    P0 : (nx, nx) ndarray
        Initial state covariance.
    dt : float
        Measurement sampling interval (seconds).
    n_steps : int, optional
        Number of Euler integration sub-steps per interval.  Default: 10.
    alpha : float, optional
        Sigma-point spread parameter.  Default: 1.0.  For ``nx`` ≤ 5 a
        value of 0.5–1.0 gives good numerical conditioning; the commonly
        quoted default of 1e-3 causes catastrophic weight cancellation for
        small state dimensions.
    beta : float, optional
        Distribution parameter (2 is optimal for Gaussian).  Default: 2.
    kappa : float, optional
        Secondary spread parameter.  Default: 0.
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

        nx = self._x.shape[0]
        lam = alpha ** 2 * (nx + kappa) - nx
        self._lam = lam
        self._nx = nx

        # Mean weights
        Wm = np.full(2 * nx + 1, 1.0 / (2.0 * (nx + lam)))
        Wm[0] = lam / (nx + lam)
        self._Wm = Wm

        # Covariance weights
        Wc = Wm.copy()
        Wc[0] = lam / (nx + lam) + (1.0 - alpha ** 2 + beta)
        self._Wc = Wc

    # ── Internal helpers ──────────────────────────────────────────────────

    def _sigma_points(self, x: np.ndarray, P: np.ndarray) -> np.ndarray:
        """
        Compute the 2nx+1 scaled sigma points from mean *x* and covariance *P*.

        Returns
        -------
        sigma : (2*nx+1, nx) ndarray
        """
        nx = self._nx
        scale = np.sqrt(nx + self._lam)
        # Cholesky of (nx+λ)P  → columns give the ±offset vectors
        try:
            L = np.linalg.cholesky((nx + self._lam) * P)
        except np.linalg.LinAlgError:
            # Regularise if P is numerically non-PD
            eps = 1e-8 * np.eye(nx)
            L = np.linalg.cholesky((nx + self._lam) * P + eps)

        sigma = np.empty((2 * nx + 1, nx))
        sigma[0] = x
        for i in range(nx):
            sigma[i + 1]      = x + L[:, i]
            sigma[i + 1 + nx] = x - L[:, i]
        return sigma

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
        Propagate sigma points through the nonlinear drift from t to t + dt.

        Parameters
        ----------
        u : (nu,) ndarray      — control input.
        d : (nd,) ndarray      — disturbance.
        p : (nparams,) ndarray — parameter vector.
        t : float              — current time.

        Returns
        -------
        x_pred : (nx,) predicted mean.
        P_pred : (nx, nx) predicted covariance.
        """
        model = self._model
        h = self._h_sub
        nx = self._nx

        # Generate sigma points
        sigma = self._sigma_points(self._x, self._P)

        # Propagate each sigma point through Euler integration of dx/dt = f
        sigma_prop = sigma.copy()
        t_j = t
        for _ in range(self._n_steps):
            F = np.row_stack([
                model.f(sigma_prop[i], u, d, p, t_j)
                for i in range(2 * nx + 1)
            ])   # (2nx+1, nx)
            sigma_prop = sigma_prop + h * F
            t_j += h

        # Predicted mean
        x_pred = np.einsum("i,ij->j", self._Wm, sigma_prop)

        # Predicted covariance (from sigma points)
        P_pred = np.zeros((nx, nx))
        for i in range(2 * nx + 1):
            diff = sigma_prop[i] - x_pred
            P_pred += self._Wc[i] * np.outer(diff, diff)

        # Add integrated process noise: sigma @ sigma^T * dt
        # Evaluated at x_pred (end of interval).
        sigma_val = model.sigma(x_pred, u, d, p, t)
        P_pred += sigma_val @ sigma_val.T * self._dt

        # Symmetrise to prevent numerical drift
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
        Apply unscented measurement update.

        Parameters
        ----------
        y    : (ny,) ndarray      — observation.
        u    : (nu,) ndarray      — input at measurement time.
        d    : (nd,) ndarray      — disturbance at measurement time.
        p    : (nparams,) ndarray — parameter vector.
        mask : (ny,) bool ndarray, optional — active output mask.

        Returns
        -------
        x_hat : (nx,) corrected state estimate.
        P     : (nx, nx) corrected covariance.
        """
        model = self._model
        nx = self._nx
        R = model.Rm

        # Regenerate sigma points from predicted (x̂⁻, P⁻)
        sigma = self._sigma_points(self._x, self._P)

        # Map sigma points through observation function
        Upsilon = np.array([
            model.hm(sigma[i], u, d, p, 0.0) for i in range(2 * nx + 1)
        ])   # (2nx+1, ny)

        # Apply mask
        if mask is not None:
            y = y[mask]
            Upsilon = Upsilon[:, mask]
            R = R[np.ix_(mask, mask)]

        # Predicted measurement mean
        y_pred = np.einsum("i,ij->j", self._Wm, Upsilon)

        # Innovation covariance S and cross-covariance Pxy
        ny_act = y_pred.shape[0]
        S = np.zeros((ny_act, ny_act))
        Pxy = np.zeros((nx, ny_act))
        for i in range(2 * nx + 1):
            dy = Upsilon[i] - y_pred
            dx = sigma[i] - self._x
            S   += self._Wc[i] * np.outer(dy, dy)
            Pxy += self._Wc[i] * np.outer(dx, dy)
        S += R

        # Kalman gain  K = Pxy S⁻¹
        K = Pxy @ np.linalg.solve(S.T, np.eye(ny_act)).T
        innov = y - y_pred
        x_new = self._x + K @ innov
        # Joseph-form posterior: P = (I−KH)P(I−KH)ᵀ + KRKᵀ  where H is implicit
        # via Pxy = P H^T  ⟹  K H P = K Pxy^T
        P_new = self._P - K @ Pxy.T - Pxy @ K.T + K @ S @ K.T
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
        """Combined predict + update step."""
        self.predict(u, d, p, t)
        return self.update(y, u, d, p, mask=mask)
