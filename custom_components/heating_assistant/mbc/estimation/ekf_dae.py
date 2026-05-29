"""
Continuous-Discrete Extended Kalman Filter (CD-EKF) for SDAE systems
(ControlToolbox §State Estimation for Nonlinear SDAE Systems —
*Continuous-Discrete Extended Kalman Filter for SDAEs*).

Model
-----
    dx(t) = f(x, y, u, d, p, t) dt + sigma(x, y, u, d, p, t) dw(t),  dw ~ N(0, I dt)
    0     = g(x, y, p)
    ym(tk) = hm(x, y, p) + v(tk),   v ~ N(0, R)

The algebraic variables y are *never* added to the state vector.  The
implicit function theorem expresses y as an implicit function of x and
propagates the resulting sensitivities through the time and measurement
updates; the state covariance P remains nx × nx.

Initialisation
--------------
The initial mean ŷ_{0|0} satisfies g(x̂_{0|0}, ŷ_{0|0}, p) = 0.  The
initial algebraic covariance is

    P_{y,0|0} = Φ_{yx} P_{0|0} Φ_{yx}ᵀ,
    (∂g/∂y) Φ_{yx} = −∂g/∂x,

evaluated at (x̂_{0|0}, ŷ_{0|0}).

Time update — implicit Euler with sensitivity propagation
----------------------------------------------------------
The interval [t_k, t_{k+1}] is divided into N_int sub-steps of size h.
At each sub-step n we:

1. Solve the implicit Euler residual

       R(x_{n+1}, y_{n+1}) = [
           x_{n+1} − x_n − f(x_{n+1}, y_{n+1}, u, d, p, t_{n+1}) h;
           g(x_{n+1}, y_{n+1}, p)
       ] = 0

   by Newton iteration, giving the mean (x̂_{k,n+1}, ŷ_{k,n+1}).

2. Compute the one-step sensitivities Φ_{xx}, Φ_{yx} by solving the
   linear system

       [ I − (∂f/∂x) h    −(∂f/∂y) h ] [ Φ_{xx} ]   [ I ]
       [   ∂g/∂x              ∂g/∂y  ] [ Φ_{yx} ] = [ 0 ].

   The coefficient matrix is the residual Jacobian from step 1 — already
   factored as part of the Newton solve.

3. Advance the covariance via the left-rectangular rule for the
   stochastic integral:

       τ_{k,n}     = P_{k,n} + sigma sigma^T h    (sigma evaluated at (x̂_{k,n}, ŷ_{k,n}))
       P_{k,n+1}   = Φ_{xx} τ_{k,n} Φ_{xx}^T

4. Propagate the algebraic covariance via the implicit function theorem:

       P_{y,k,n+1} = Φ_{yx,k,n+1} P_{k,n+1} Φ_{yx,k,n+1}^T,
       (∂g/∂y) Φ_{yx,k,n+1} = −∂g/∂x,

   evaluated at (x̂_{k,n+1}, ŷ_{k,n+1}).

Measurement update — total-derivative C
---------------------------------------
    e_k = y^m_k − hm(x̂_{k|k-1}, ŷ_{k|k-1}, p),
    C_k = ∂hm/∂x + (∂hm/∂y)(∂y/∂x)
        = ∂hm/∂x − (∂hm/∂y) (∂g/∂y)⁻¹ (∂g/∂x),
    R_e = C_k P_{k|k-1} C_k^T + R,
    K_k = P_{k|k-1} C_k^T R_e⁻¹,

    x̂_{k|k} = x̂_{k|k-1} + K_k e_k,
    P_{k|k} = (I − K_k C_k) P_{k|k-1} (I − K_k C_k)^T + K_k R K_k^T   (Joseph)

The filtered ŷ_{k|k} satisfies g(x̂_{k|k}, ŷ_{k|k}, p) = 0 (Newton solve)
and P_{y,k|k} is recomputed via the implicit function theorem at the
filtered state.
"""

from __future__ import annotations

import numpy as np

from ..models import ContinuousDiscreteDAEModel
from .._utils import _newton_solve


class ContinuousDiscreteDAEEKF:
    """
    Continuous-Discrete EKF for SDAE systems
    (ControlToolbox §SDAE State Estimation — *CD-EKF for SDAEs*).

    Parameters
    ----------
    model : ContinuousDiscreteDAEModel
        Nonlinear SDAE system.  Must implement ``f``, ``sigma``, ``g``,
        ``hm``, ``Rm`` plus Jacobians ``dfdx``, ``dfdy``, ``dgdx``, ``dgdy``,
        ``dhmdx`` and (when ``hm`` depends on y) ``dhmdy``.
    x0 : (nx,) ndarray
        Initial differential state estimate x̂_{0|0}.
    y0 : (ny,) ndarray
        Initial algebraic state guess.  Will be projected onto the
        constraint g(x0, y0, ...) = 0 by Newton iteration.
    P0 : (nx, nx) ndarray
        Initial state covariance P_{0|0}.
    dt : float
        Measurement sampling interval (seconds).
    n_steps : int, optional
        Implicit-Euler integration sub-steps per measurement interval.
        Default: 10.
    newton_tol : float, optional
        Newton convergence tolerance for the implicit sub-step and the
        algebraic projections.  Default: 1e-10.
    newton_max_iter : int, optional
        Maximum Newton iterations per solve.  Default: 50.
    """

    def __init__(
        self,
        model: ContinuousDiscreteDAEModel,
        x0: np.ndarray,
        y0: np.ndarray,
        P0: np.ndarray,
        dt: float,
        n_steps: int = 10,
        newton_tol: float = 1e-10,
        newton_max_iter: int = 50,
    ) -> None:
        self._model = model
        self._dt = dt
        self._n_steps = n_steps
        self._h_sub = dt / n_steps
        self._newton_tol = newton_tol
        self._newton_max_iter = newton_max_iter

        self._x = np.array(x0, dtype=float)
        self._P = np.array(P0, dtype=float)
        self._nx = self._x.shape[0]
        self._ny = model.ny

        # Cached u/d/p for the current sub-step closure
        self._u_last: np.ndarray | None = None
        self._d_last: np.ndarray | None = None
        self._p_last: np.ndarray | None = None

        # Project initial y onto the constraint manifold
        self._y = self._consistent_y(self._x, np.array(y0, dtype=float),
                                      np.zeros(model.nu) if y0 is not None else None,
                                      None, None, 0.0,
                                      use_dummies=True)
        # Initial algebraic covariance via implicit function theorem
        self._Py = self._compute_Py(self._x, self._y,
                                     np.zeros(model.nu),
                                     np.zeros(model.nd),
                                     np.array([], dtype=float),
                                     0.0, P=self._P)

    # ── Public properties ─────────────────────────────────────────────────

    @property
    def x_hat(self) -> np.ndarray:
        """Current differential state estimate x̂ ∈ ℝⁿˣ (copy)."""
        return self._x.copy()

    @property
    def y_hat(self) -> np.ndarray:
        """Current algebraic state estimate ŷ ∈ ℝⁿʸ (copy)."""
        return self._y.copy()

    @property
    def P(self) -> np.ndarray:
        """Current state covariance P ∈ ℝⁿˣˣⁿˣ (copy)."""
        return self._P.copy()

    @property
    def Py(self) -> np.ndarray:
        """Current algebraic-variable covariance P_y ∈ ℝⁿʸˣⁿʸ (copy)."""
        return self._Py.copy()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _consistent_y(
        self,
        x: np.ndarray,
        y_init: np.ndarray,
        u: np.ndarray | None,
        d: np.ndarray | None,
        p: np.ndarray | None,
        t: float,
        use_dummies: bool = False,
    ) -> np.ndarray:
        """Solve g(x, y, u, d, p, t) = 0 for y by Newton iteration."""
        if use_dummies:
            u = np.zeros(self._model.nu)
            d = np.zeros(self._model.nd)
            p = np.array([], dtype=float)
        return _newton_solve(
            residual=lambda y_: self._model.g(x, y_, u, d, p, t),
            jacobian=lambda y_: self._model.dgdy(x, y_, u, d, p, t),
            x0=y_init,
            tol=self._newton_tol,
            max_iter=self._newton_max_iter,
        )

    def _compute_Py(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
        P: np.ndarray,
    ) -> np.ndarray:
        """
        Algebraic-variable covariance P_y = Φ_{yx} P Φ_{yx}^T via the
        implicit function theorem (∂g/∂y) Φ_{yx} = −(∂g/∂x).
        """
        dgdx = self._model.dgdx(x, y, u, d, p, t)   # (ny, nx)
        dgdy = self._model.dgdy(x, y, u, d, p, t)   # (ny, ny)
        Phi_yx = np.linalg.solve(dgdy, -dgdx)        # (ny, nx)
        Py = Phi_yx @ P @ Phi_yx.T
        return 0.5 * (Py + Py.T)

    # ── Filter steps ──────────────────────────────────────────────────────

    def predict(
        self,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Time update via the implicit-Euler / sensitivity scheme.

        Parameters
        ----------
        u : (nu,) ndarray  — control input over [t, t+dt].
        d : (nd,) ndarray  — disturbance over [t, t+dt].
        p : (nparams,) ndarray — parameter vector.
        t : float          — current time.

        Returns
        -------
        x_pred : (nx,) predicted differential state x̂_{k+1|k}.
        y_pred : (ny,) consistent algebraic state ŷ_{k+1|k}.
        P_pred : (nx, nx) predicted covariance P_{k+1|k}.
        """
        model = self._model
        nx = self._nx
        ny = self._ny
        h = self._h_sub

        x_n = self._x.copy()
        y_n = self._y.copy()
        P_n = self._P.copy()
        t_n = t

        I_xx = np.eye(nx)
        I_yy_full_zero_top = np.concatenate([I_xx, np.zeros((ny, nx))], axis=0)  # (nx+ny, nx)

        for _ in range(self._n_steps):
            t_next = t_n + h
            rhs_x = x_n.copy()

            def residual(z: np.ndarray) -> np.ndarray:
                xn = z[:nx]
                yn = z[nx:]
                f_val = model.f(xn, yn, u, d, p, t_next)
                g_val = model.g(xn, yn, u, d, p, t_next)
                return np.concatenate([
                    xn - rhs_x - f_val * h,
                    g_val,
                ])

            def jacobian(z: np.ndarray) -> np.ndarray:
                xn = z[:nx]
                yn = z[nx:]
                dfdx = model.dfdx(xn, yn, u, d, p, t_next)
                dfdy = model.dfdy(xn, yn, u, d, p, t_next)
                dgdx = model.dgdx(xn, yn, u, d, p, t_next)
                dgdy = model.dgdy(xn, yn, u, d, p, t_next)
                top = np.concatenate([I_xx - dfdx * h, -dfdy * h], axis=1)
                bot = np.concatenate([dgdx, dgdy], axis=1)
                return np.concatenate([top, bot], axis=0)

            z0 = np.concatenate([x_n, y_n])
            z_next = _newton_solve(
                residual, jacobian, z0,
                tol=self._newton_tol, max_iter=self._newton_max_iter,
            )
            x_next = z_next[:nx]
            y_next = z_next[nx:]

            # ── Sensitivity sub-step ──
            J = jacobian(z_next)   # (nx+ny, nx+ny) — same as Newton's last Jacobian
            # Solve J · [Φ_xx; Φ_yx] = [I; 0]  (block columns of size nx)
            Phi_block = np.linalg.solve(J, I_yy_full_zero_top)   # (nx+ny, nx)
            Phi_xx = Phi_block[:nx, :]
            # Phi_yx = Phi_block[nx:, :]   (computed below for P_y)

            # ── Covariance sub-step (left rectangular rule) ──
            sigma_n = model.sigma(x_n, y_n, u, d, p, t_n)   # (nx, nw)
            tau = P_n + sigma_n @ sigma_n.T * h
            P_next = Phi_xx @ tau @ Phi_xx.T
            P_next = 0.5 * (P_next + P_next.T)

            x_n = x_next
            y_n = y_next
            P_n = P_next
            t_n = t_next

        self._x = x_n
        self._y = y_n
        self._P = P_n
        self._Py = self._compute_Py(x_n, y_n, u, d, p, t_n, P=P_n)
        return x_n.copy(), y_n.copy(), P_n.copy()

    def update(
        self,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        mask: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Measurement update with the total-derivative C and Joseph form.

        Parameters
        ----------
        y    : (nym,) ndarray  — observation y^m_k.
        u    : (nu,) ndarray   — input at measurement time.
        d    : (nd,) ndarray   — disturbance at measurement time.
        p    : (nparams,) ndarray — parameter vector.
        mask : (nym,) bool ndarray, optional — active output mask.

        Returns
        -------
        x_hat : (nx,) corrected differential state estimate x̂_{k|k}.
        y_hat : (ny,) consistent algebraic state ŷ_{k|k}.
        P     : (nx, nx) corrected covariance P_{k|k}.
        """
        model = self._model
        nx = self._nx
        ny = self._ny
        x = self._x
        y_alg = self._y
        P = self._P
        R = model.Rm

        # Total derivative C = ∂hm/∂x + (∂hm/∂y)(∂y/∂x)
        # with (∂g/∂y)(∂y/∂x) = −(∂g/∂x).
        dhmdx = model.dhmdx(x, y_alg, u, d, p, 0.0)         # (nym, nx)
        dhmdy = model.dhmdy(x, y_alg, u, d, p, 0.0)         # (nym, ny)
        dgdx = model.dgdx(x, y_alg, u, d, p, 0.0)           # (ny, nx)
        dgdy = model.dgdy(x, y_alg, u, d, p, 0.0)           # (ny, ny)
        Phi_yx = np.linalg.solve(dgdy, -dgdx)                # (ny, nx)
        C = dhmdx + dhmdy @ Phi_yx                           # (nym, nx)

        y_hat = model.hm(x, y_alg, u, d, p, 0.0)            # (nym,)

        if mask is not None:
            active = np.where(mask)[0]
            if len(active) == 0:
                return x.copy(), y_alg.copy(), P.copy()
            C_sub = C[active, :]
            y_hat_sub = y_hat[active]
            y_obs_sub = y[active]
            R_sub = R[np.ix_(active, active)]
        else:
            C_sub = C
            y_hat_sub = y_hat
            y_obs_sub = y
            R_sub = R

        R_e = C_sub @ P @ C_sub.T + R_sub
        Kt = np.linalg.solve(R_e, C_sub @ P)
        K = Kt.T

        e = y_obs_sub - y_hat_sub
        x_new = x + K @ e

        IKC = np.eye(nx) - K @ C_sub
        P_new = IKC @ P @ IKC.T + K @ R_sub @ K.T
        P_new = 0.5 * (P_new + P_new.T)

        # Project the algebraic state onto g = 0 at the updated x
        y_new = self._consistent_y(x_new, y_alg, u, d, p, 0.0)

        self._x = x_new
        self._y = y_new
        self._P = P_new
        self._Py = self._compute_Py(x_new, y_new, u, d, p, 0.0, P=P_new)
        return x_new.copy(), y_new.copy(), P_new.copy()

    def step(
        self,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
        mask: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Combined time + measurement update."""
        self.predict(u, d, p, t)
        return self.update(y, u, d, p, mask=mask)
