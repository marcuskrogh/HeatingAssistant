"""
Continuous-Discrete Extended Kalman Filter (CD-EKF) for SDE systems
(ControlToolbox §State Estimation for Nonlinear SDE Systems —
*Continuous-Discrete Extended Kalman Filter*).

Model
-----
    dx(t)  = f(x, u, d, p, t) dt + sigma(x, u, d, p, t) dw(t),  dw ~ N(0, I dt)
    ym(tk) = hm(x(tk), p) + v(tk),                              v ~ N(0, R)

Time update over [t_k, t_{k+1}] (mean trajectory + covariance propagation)
---------------------------------------------------------------------------
The predicted mean evolves as the expectation of the SDE (the SDE is a
martingale, so the diffusion contributes zero mean):

    dx̂_k/dt(t) = f(x̂_k(t), u(t), d(t), p, t),    x̂_k(t_k) = x̂_{k|k}.

The predictions at t_{k+1} are x̂_{k+1|k} = x̂_k(t_{k+1}) and
P_{k+1|k} = P_k(t_{k+1}).

Both the mean and covariance are integrated with ``n_steps`` equidistant
sub-steps of size ``h = dt / n_steps``.  Two propagation schemes are
available via the ``scheme`` constructor argument:

Explicit Euler  (``scheme="euler"``, default)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The mean ODE and the Lyapunov-type covariance ODE are integrated with
explicit Euler:

    dP_k/dt(t) = A_k(t) P_k(t) + P_k(t) A_kᵀ(t) + sigma_k(t) sigma_kᵀ(t),

    A_k(t)     = ∂f/∂x (x̂_k(t), u(t), d(t), p, t),
    sigma_k(t) = sigma (x̂_k(t), u(t), d(t), p, t).

For sub-step n:

    A_n     = ∂f/∂x (x̂_n, u, d, p, t_n)
    σ_n     = sigma (x̂_n, u, d, p, t_n)
    x̂_{n+1} = x̂_n + h · f(x̂_n, u, d, p, t_n)
    P_{n+1} = P_n + h · (A_n P_n + P_n A_nᵀ + σ_n σ_nᵀ)
    P_{n+1} ← ½(P_{n+1} + P_{n+1}ᵀ)                     (symmetrise)

The covariance is symmetrised at every sub-step to guard against numerical
drift.

Implicit Euler  (``scheme="implicit-euler"``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The mean ODE is integrated with implicit Euler (first-order L-stable scheme,
suitable for stiff drift dynamics).  The covariance is propagated via the
one-step sensitivity matrix Φ.

For sub-step n (with t_{n+1} = t_n + h):

  1. Solve for x̂_{n+1} by Newton iteration:

         R(x̂_{n+1}) = x̂_{n+1} − x̂_n − h · f(x̂_{n+1}, u, d, p, t_{n+1}) = 0,
         ∂R/∂x̂     = I − h · ∂f/∂x(x̂_{n+1}, u, d, p, t_{n+1}).

  2. Compute the one-step state-transition sensitivity (= inverse of the
     converged Newton Jacobian):

         Φ = (I − h · A_{n+1})⁻¹,   A_{n+1} = ∂f/∂x(x̂_{n+1}, u, d, p, t_{n+1}).

  3. Propagate covariance (left-rectangular rule for the stochastic integral,
     diffusion evaluated at the start of the sub-step):

         τ_{n}   = P_n + h · σ_n σ_nᵀ,    σ_n = sigma(x̂_n, u, d, p, t_n)
         P_{n+1} = Φ τ_n Φᵀ
         P_{n+1} ← ½(P_{n+1} + P_{n+1}ᵀ)                (symmetrise)

Measurement update at t_k (Joseph form)
---------------------------------------
    e_k   = y^m_k − ŷ^m_{k|k-1},         ŷ^m_{k|k-1} = hm(x̂_{k|k-1}, p)
    R_e,k = C_k P_{k|k-1} C_kᵀ + R,       C_k = ∂hm/∂x(x̂_{k|k-1}, p)
    K_k   = P_{k|k-1} C_kᵀ R_e,k⁻¹

    x̂_{k|k} = x̂_{k|k-1} + K_k e_k
    P_{k|k} = (I − K_k C_k) P_{k|k-1} (I − K_k C_k)ᵀ + K_k R K_kᵀ

The Joseph stabilising form is used as it preserves symmetry and positive
definiteness in finite-precision arithmetic.
"""

from __future__ import annotations

import numpy as np

from ..models import ContinuousDiscreteModel
from .._utils import _newton_solve

_VALID_SCHEMES = ("euler", "implicit-euler")


class ContinuousDiscreteEKF:
    """
    Continuous-Discrete Extended Kalman Filter for SDE systems
    (ControlToolbox §SDE State Estimation — *CD-EKF*).

    Two propagation schemes are available via the ``scheme`` parameter:

    * ``"euler"`` (default) — explicit Euler for both the mean ODE and the
      Lyapunov-type covariance ODE.  Cheap per step; requires small ``h``
      for accuracy and stability on stiff systems.

    * ``"implicit-euler"`` — implicit Euler for the mean (via Newton
      iteration) with covariance propagation through the one-step sensitivity
      Φ = (I − h A_{n+1})⁻¹.  L-stable; suitable for stiff drift dynamics.
      See the module docstring for the full algorithm.

    Parameters
    ----------
    model : ContinuousDiscreteModel
        Nonlinear continuous-discrete SDE system providing ``f``, ``sigma``,
        ``hm``, ``Rm``, and Jacobians ``dfdx`` and ``dhmdx``.
    x0 : (nx,) ndarray
        Initial state estimate x̂_{0|0}.
    P0 : (nx, nx) ndarray
        Initial state covariance P_{0|0}.
    dt : float
        Measurement sampling interval (seconds).
    n_steps : int, optional
        Number of integration sub-steps per measurement interval.
        Must be at least 1.  Default: 10.
    scheme : {"euler", "implicit-euler"}, optional
        Propagation scheme for the time update.  Default: ``"euler"``.
    newton_tol : float, optional
        Newton convergence tolerance used by the implicit-Euler sub-step.
        Ignored when ``scheme="euler"``.  Default: 1e-10.
    newton_max_iter : int, optional
        Maximum Newton iterations per implicit sub-step.
        Ignored when ``scheme="euler"``.  Default: 50.
    """

    def __init__(
        self,
        model: ContinuousDiscreteModel,
        x0: np.ndarray,
        P0: np.ndarray,
        dt: float,
        n_steps: int = 10,
        scheme: str = "euler",
        newton_tol: float = 1e-10,
        newton_max_iter: int = 50,
    ) -> None:
        if n_steps < 1:
            raise ValueError(
                f"n_steps must be a positive integer, got {n_steps!r}."
            )
        if scheme not in _VALID_SCHEMES:
            raise ValueError(
                f"scheme must be one of {_VALID_SCHEMES!r}, got {scheme!r}."
            )
        self._model = model
        self._x_np: np.ndarray = np.array(x0, dtype=float)
        self._P_np: np.ndarray = np.array(P0, dtype=float)
        self._dt = dt
        self._n_steps = n_steps
        self._h = dt / n_steps
        self._scheme = scheme
        self._newton_tol = newton_tol
        self._newton_max_iter = newton_max_iter

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
        Time update: integrate the mean ODE and propagate the covariance
        from ``t`` to ``t + dt`` using the configured ``scheme``.

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
        if self._scheme == "euler":
            return self._predict_euler(u, d, p, t)
        return self._predict_implicit_euler(u, d, p, t)

    def _predict_euler(
        self,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Explicit-Euler time update."""
        x = self._x_np.copy()
        P = self._P_np.copy()
        h = self._h
        model = self._model

        t_j = t
        for _ in range(self._n_steps):
            A_j = model.dfdx(x, u, d, p, t_j)
            sigma_j = model.sigma(x, u, d, p, t_j)
            f_j = model.f(x, u, d, p, t_j)

            P_dot = A_j @ P + P @ A_j.T + sigma_j @ sigma_j.T
            x = x + h * f_j
            P = P + h * P_dot
            P = (P + P.T) * 0.5  # symmetrise to prevent numerical drift
            t_j += h

        self._x_np = x
        self._P_np = P
        return x.copy(), P.copy()

    def _predict_implicit_euler(
        self,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Implicit-Euler time update.

        For each sub-step n → n+1:
          1. Newton solve:  x_{n+1} − x_n − h f(x_{n+1}, u, d, p, t_{n+1}) = 0
          2. Sensitivity:   Φ = (I − h A_{n+1})⁻¹
          3. Covariance:    τ = P_n + h σ_n σ_nᵀ
                            P_{n+1} = Φ τ Φᵀ
        """
        x = self._x_np.copy()
        P = self._P_np.copy()
        h = self._h
        model = self._model
        nx = x.shape[0]
        I_nx = np.eye(nx)

        t_n = t
        for _ in range(self._n_steps):
            t_next = t_n + h
            x_n = x.copy()

            # Noise (left rectangular rule): evaluated at start of sub-step
            sigma_n = model.sigma(x_n, u, d, p, t_n)

            # Newton solve for implicit mean
            x_rhs = x_n  # captured for closure

            def residual(xk: np.ndarray) -> np.ndarray:
                return xk - x_rhs - h * model.f(xk, u, d, p, t_next)

            def jacobian(xk: np.ndarray) -> np.ndarray:
                return I_nx - h * model.dfdx(xk, u, d, p, t_next)

            x = _newton_solve(
                residual, jacobian, x_n,
                tol=self._newton_tol,
                max_iter=self._newton_max_iter,
            )

            # Sensitivity matrix Φ = (I − h A_{n+1})⁻¹
            # The converged Newton Jacobian is already (I − h A_{n+1}),
            # so we solve Φ via the same matrix.
            M = jacobian(x)          # (nx, nx)
            Phi = np.linalg.solve(M, I_nx)   # Φ = M⁻¹

            # Covariance propagation
            tau = P + h * sigma_n @ sigma_n.T
            P = Phi @ tau @ Phi.T
            P = (P + P.T) * 0.5      # symmetrise

            t_n = t_next

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
        Measurement update: fuse observation ``y_k`` with the current
        prior using the Joseph stabilising form.

        Parameters
        ----------
        y : (nym,) ndarray  — observation vector at the measurement time.
        u : (nu,) ndarray   — input at measurement time.
        d : (nd,) ndarray   — disturbance at measurement time.
        p : (nparams,) ndarray  — parameter vector.
        mask : (nym,) bool ndarray, optional
            When provided, only outputs where ``mask[i]`` is ``True`` are
            used in the update.  ``None`` (default) uses all outputs.

        Returns
        -------
        x_hat : (nx,) corrected state estimate x̂_{k|k}.
        P     : (nx, nx) corrected covariance P_{k|k}.
        """
        x = self._x_np
        P = self._P_np
        nx = x.shape[0]
        R = self._model.Rm

        C = self._model.dhmdx(x, u, d, p, 0.0)        # (nym, nx)
        y_hat = self._model.hm(x, u, d, p, 0.0)       # (nym,)

        if mask is not None:
            active = np.where(mask)[0]
            if len(active) == 0:
                return x.copy(), P.copy()
            C = C[active, :]
            y_hat = y_hat[active]
            y_sub = y[active]
            R_sub = R[np.ix_(active, active)]
        else:
            y_sub = y
            R_sub = R

        # Innovation covariance R_e = C P Cᵀ + R
        R_e = C @ P @ C.T + R_sub

        # Kalman gain  K = P Cᵀ R_e⁻¹  via solve (R_e Kᵀ = C P)
        Kt = np.linalg.solve(R_e, C @ P)
        K = Kt.T

        # State correction
        e = y_sub - y_hat
        x_new = x + K @ e

        # Joseph form:  P = (I − K C) P (I − K C)ᵀ + K R Kᵀ
        IKC = np.eye(nx) - K @ C
        P_new = IKC @ P @ IKC.T + K @ R_sub @ K.T
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
        Combined time + measurement update.

        Propagates the estimate from the previous time to ``t``, then
        fuses the measurement ``y``.

        Parameters
        ----------
        y    : (nym,) ndarray  — observation at time t.
        u    : (nu,) ndarray   — input applied over the previous interval.
        d    : (nd,) ndarray   — disturbance at time t.
        p    : (nparams,) ndarray  — parameter vector.
        t    : float           — current measurement time.
        mask : (nym,) bool ndarray, optional — see :meth:`update`.

        Returns
        -------
        x_hat : (nx,) corrected state estimate.
        P     : (nx, nx) corrected covariance.
        """
        self.predict(u, d, p, t)
        return self.update(y, u, d, p, mask=mask)
