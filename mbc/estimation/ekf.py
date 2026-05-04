"""
Continuous-Discrete Extended Kalman Filter (Ph.D. Ch. 7.1).

The CD-EKF propagates the state estimate and covariance through the
nonlinear continuous SDE dynamics between measurement times, then
applies a standard linearised measurement update.

Prediction (continuous propagation over [t_k, t_{k+1}]):
    dx_hat/dt = f(x_hat, u, d, t)
    dP/dt     = F(t) P + P F(t)ᵀ + G(t) Q_c G(t)ᵀ

where F = ∂f/∂x and G = g evaluated at the current estimate.
Both ODEs are integrated numerically with step size dt / n_steps.

Measurement update (standard EKF correction):
    H   = ∂h/∂x |_{x_hat}
    S   = H P⁻ Hᵀ + R
    K   = P⁻ Hᵀ S⁻¹
    x̂   = x̂⁻ + K (y − h(x̂⁻, d))
    P   = (I − K H) P⁻ (I − K H)ᵀ + K R Kᵀ   (Joseph form)

Reference:  Ph.D. thesis, Ch. 7.1.
"""

from __future__ import annotations

import numpy as np

from ..models import ContinuousDiscreteModel


class ContinuousDiscreteEKF:
    """
    Continuous-Discrete Extended Kalman Filter (Ph.D. Ch. 7.1).

    Parameters
    ----------
    model : ContinuousDiscreteModel
        Nonlinear continuous-discrete system providing ``f``, ``g``, ``h``,
        ``Q_c``, and ``R``.
    x0 : (nx,) ndarray
        Initial state estimate.
    P0 : (nx, nx) ndarray
        Initial state covariance.
    dt : float
        Measurement sampling interval (seconds).
    n_steps : int, optional
        Number of Euler integration sub-steps per measurement interval.
        Default: 10.
    """

    def __init__(
        self,
        model: ContinuousDiscreteModel,
        x0: np.ndarray,
        P0: np.ndarray,
        dt: float,
        n_steps: int = 10,
    ) -> None:
        self._model = model
        self._x_np: np.ndarray = np.array(x0, dtype=float)
        self._P_np: np.ndarray = np.array(P0, dtype=float)
        self._dt = dt
        self._n_steps = n_steps
        self._h = dt / n_steps

    # ── Public properties ─────────────────────────────────────────────────

    @property
    def x_hat(self) -> np.ndarray:
        """Current state estimate x̂ ∈ ℝⁿˣ (copy)."""
        return self._x_np.copy()

    @property
    def P(self) -> np.ndarray:
        """Current state covariance P ∈ ℝⁿˣˣⁿˣ (copy)."""
        return self._P_np.copy()

    # ── Filter steps ──────────────────────────────────────────────────────

    def predict(
        self,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Propagate state estimate and covariance from t to t + dt.

        Integrates the nonlinear drift and continuous Riccati ODE using
        Euler steps of size ``dt / n_steps``.

        Parameters
        ----------
        u : (nu,) ndarray  — control input applied over [t, t+dt].
        d : (nd,) ndarray  — disturbance over [t, t+dt].
        p : (nparams,) ndarray  — parameter vector.
        t : float          — current time.

        Returns
        -------
        x_pred : (nx,) predicted state estimate.
        P_pred : (nx, nx) predicted covariance.
        """
        x = self._x_np.copy()
        P = self._P_np.copy()
        h = self._h
        model = self._model

        t_j = t
        for _ in range(self._n_steps):
            F_j = model.dfdx(x, u, d, p, t_j)
            G_j = model.sigma(x, u, d, p, t_j)
            f_j = model.f(x, u, d, p, t_j)

            P_dot = F_j @ P + P @ F_j.T + G_j @ G_j.T
            x = x + h * f_j
            P = P + h * P_dot
            # Symmetrise at every sub-step to prevent numerical drift to non-PD
            P = (P + P.T) * 0.5
            t_j += h

        self._x_np = x
        self._P_np = P
        return x.copy(), P.copy()

    def update(
        self,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        mask: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Apply measurement update given observation y_k.

        Parameters
        ----------
        y : (ny,) ndarray   — observation vector.
        u : (nu,) ndarray   — input at measurement time.
        d : (nd,) ndarray   — disturbance at measurement time.
        p : (nparams,) ndarray  — parameter vector.
        mask : (ny,) bool ndarray, optional
            When provided, only outputs where ``mask[i]`` is ``True`` are
            used in the update.  ``None`` (default) uses all outputs.

        Returns
        -------
        x_hat : (nx,) corrected state estimate.
        P     : (nx, nx) corrected covariance.
        """
        x = self._x_np
        P = self._P_np
        nx = x.shape[0]
        R = self._model.Rm

        H = self._model.dhmdx(x, u, d, p, 0.0)               # (ny, nx)
        y_hat = self._model.hm(x, u, d, p, 0.0)               # (ny,)

        if mask is not None:
            active = np.where(mask)[0]
            if len(active) == 0:
                return x.copy(), P.copy()
            H = H[active, :]
            y_hat = y_hat[active]
            y_sub = y[active]
            R_sub = R[np.ix_(active, active)]
        else:
            y_sub = y
            R_sub = R

        # Innovation covariance  S = H P Hᵀ + R
        S = H @ P @ H.T + R_sub                   # (na, na)

        # Kalman gain  K = P Hᵀ S⁻¹   via  S Kᵀ = H P
        Kt = np.linalg.solve(S, H @ P)            # (na, nx)  = K^T
        K = Kt.T                                   # (nx, na)

        # State correction
        e = y_sub - y_hat
        x_new = x + K @ e

        # Joseph form:  P = (I − K H) P (I − K H)ᵀ + K R Kᵀ
        IKH = np.eye(nx) - K @ H
        P_new = IKH @ P @ IKH.T + K @ R_sub @ K.T
        P_new = (P_new + P_new.T) * 0.5

        self._x_np = x_new
        self._P_np = P_new
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
        """
        Combined predict + update step.

        Propagates the estimate from the previous time to ``t``, then
        fuses the measurement ``y``.

        Parameters
        ----------
        y    : (ny,) ndarray  — observation at time t.
        u    : (nu,) ndarray  — input applied over the previous interval.
        d    : (nd,) ndarray  — disturbance at time t.
        p    : (nparams,) ndarray  — parameter vector.
        t    : float          — current measurement time.
        mask : (ny,) bool ndarray, optional — see :meth:`update`.

        Returns
        -------
        x_hat : (nx,) corrected state estimate.
        P     : (nx, nx) corrected covariance.
        """
        self.predict(u, d, p, t)
        return self.update(y, u, d, p, mask=mask)
