"""
Euler-Maruyama simulator for continuous-discrete SDAE models (Ph.D. Ch. 6).

Extends ``SDESimulator`` to handle differential-algebraic systems:

    dx = f(x, y, u, d, p, t) dt + sigma(x, y, u, d, p, t) dw(t),
    dw(t) ~ N(0, I dt),
    0 = h(x, y, u, d, p, t)

At each Euler-Maruyama sub-step:
  1. The Euler drift update is applied to x.
  2. The algebraic constraint ``h(x, y, u, d, p, t) = 0`` is solved for y
     via :func:`_newton_solve`.
  3. Diffusion noise is added.

Reference:  Ph.D. thesis, Ch. 6.
"""

from __future__ import annotations

import numpy as np

from ..models import ContinuousDiscreteDAEModel
from .._utils import _newton_solve


class SDAESimulator:
    """
    Euler-Maruyama simulator for continuous-discrete SDAE models (Ph.D. Ch. 6).

    Parameters
    ----------
    model : ContinuousDiscreteDAEModel
        The nonlinear SDAE system to simulate.
    dt : float
        Measurement sampling interval (seconds).
    n_steps : int, optional
        Number of Euler-Maruyama sub-steps per interval.  Default: 10.
    scheme : {"EE", "IE"}, optional
        Integration scheme.  Default: ``"EE"``.
    newton_tol : float, optional
        Convergence tolerance for the Newton solver on ``h = 0``.
        Default: 1e-10.
    newton_max_iter : int, optional
        Maximum Newton iterations per sub-step.  Default: 50.
    seed : int or None, optional
        Random seed for reproducibility.
    """

    def __init__(
        self,
        model: ContinuousDiscreteDAEModel,
        dt: float,
        n_steps: int = 10,
        scheme: str = "EE",
        newton_tol: float = 1e-10,
        newton_max_iter: int = 50,
        seed: int | None = None,
    ) -> None:
        if scheme not in ("EE", "IE"):
            raise ValueError(f"Unknown scheme '{scheme}'; choose 'EE' or 'IE'.")
        self._model = model
        self._dt = dt
        self._n_steps = n_steps
        self._scheme = scheme
        self._newton_tol = newton_tol
        self._newton_max_iter = newton_max_iter
        self._rng = np.random.default_rng(seed)

    def _solve_constraint(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Solve h(x, y, u, d, p, t) = 0 for y via :func:`_newton_solve`.

        Uses ``y`` as the initial guess and iterates until
        ``‖h‖ < newton_tol`` or ``newton_max_iter`` steps have been taken.
        """
        return _newton_solve(
            residual=lambda y_: self._model.h(x, y_, u, d, p, t),
            jacobian=lambda y_: self._model.dhdy(x, y_, u, d, p, t),
            x0=y,
            tol=self._newton_tol,
            max_iter=self._newton_max_iter,
        )

    def step(
        self,
        x: np.ndarray,
        y: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Simulate one measurement interval from t to t + dt.

        Parameters
        ----------
        x : (nx,) ndarray  — differential state at time t.
        y : (ny,) ndarray  — algebraic state at time t (consistent).
        u : (nu,) ndarray  — control input over [t, t+dt].
        d : (nd,) ndarray  — disturbance over [t, t+dt].
        p : (nparams,) ndarray  — parameter vector.
        t : float          — current time.

        Returns
        -------
        x_next : (nx,) differential state at t + dt.
        y_next : (ny,) algebraic state at t + dt (consistent).
        """
        h = self._dt / self._n_steps
        sqrt_h = np.sqrt(h)
        nx = x.shape[0]

        x_cur = x.copy()
        y_cur = y.copy()
        t_cur = t

        for _ in range(self._n_steps):
            # Diffusion: sigma encodes continuous-time noise magnitude; dw ~ N(0, I dt)
            sigma_val = self._model.sigma(x_cur, y_cur, u, d, p, t_cur)  # (nx, nw)
            nw = sigma_val.shape[1]
            xi = self._rng.standard_normal(nw)
            dW = sigma_val @ xi * sqrt_h

            if self._scheme == "EE":
                # 1. Euler drift step on x
                f_val = self._model.f(x_cur, y_cur, u, d, p, t_cur)
                x_next = x_cur + f_val * h
                # 2. Solve algebraic constraint at the new x
                y_next = self._solve_constraint(x_next, y_cur, u, d, p, t_cur + h)
                # 3. Add diffusion noise
                x_next = x_next + dW
                # Re-solve constraint after noise perturbation
                y_next = self._solve_constraint(x_next, y_next, u, d, p, t_cur + h)
            else:  # IE — implicit drift, explicit diffusion
                # Noise term first (explicit)
                noise = dW
                # Solve coupled system:
                #   F(x_next) = x_next − (x_cur + noise) − f(x_next, y_next,…) h = 0
                #   h(x_next, y_next, …) = 0
                # via alternating Newton: y given x (inner), then x (outer).
                rhs = x_cur + noise
                t_next = t_cur + h
                y_ref = [y_cur.copy()]

                def residual(xn: np.ndarray) -> np.ndarray:
                    y_ref[0] = self._solve_constraint(xn, y_ref[0], u, d, p, t_next)
                    return xn - rhs - self._model.f(xn, y_ref[0], u, d, p, t_next) * h

                def jacobian(xn: np.ndarray) -> np.ndarray:
                    return (np.eye(nx)
                            - h * self._model.dfdx(xn, y_ref[0], u, d, p, t_next))

                x_next = _newton_solve(
                    residual, jacobian, x_cur.copy(),
                    tol=self._newton_tol, max_iter=self._newton_max_iter,
                )
                y_next = self._solve_constraint(x_next, y_ref[0], u, d, p, t_next)

            x_cur = x_next
            y_cur = y_next
            t_cur += h

        return x_cur, y_cur

    def simulate(
        self,
        x0: np.ndarray,
        y0: np.ndarray,
        U: np.ndarray,
        D: np.ndarray,
        P: np.ndarray,
        t0: float = 0.0,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Simulate over a full horizon of T measurement intervals.

        Parameters
        ----------
        x0 : (nx,) ndarray
            Initial differential state at t0.
        y0 : (ny,) ndarray
            Initial algebraic state (consistent with h(x0, y0, ...) = 0).
        U : (T, nu) ndarray
            Input trajectory.
        D : (T, nd) ndarray
            Disturbance trajectory.
        P : (T, nparams) ndarray
            Parameter trajectory.
        t0 : float, optional
            Start time.  Default: 0.

        Returns
        -------
        X : (T+1, nx) ndarray  — differential state trajectory.
        Y : (T+1, ny) ndarray  — algebraic state trajectory.
        """
        T = U.shape[0]
        nx = x0.shape[0]
        ny = y0.shape[0]
        X = np.empty((T + 1, nx))
        Y = np.empty((T + 1, ny))
        X[0] = x0.copy()
        Y[0] = y0.copy()
        t_cur = t0
        for k in range(T):
            X[k + 1], Y[k + 1] = self.step(X[k], Y[k], U[k], D[k], P[k], t_cur)
            t_cur += self._dt
        return X, Y
