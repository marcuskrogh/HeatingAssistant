"""
Numerical integrator for continuous-discrete SDE models
(ControlToolbox §SDE — *Numerical Integration*).

Implements two integration schemes for the Itô SDE

    dx(t) = f(x, u, d, p, t) dt + sigma(x, u, d, p, t) dw(t),
    dw ~ N(0, I dt).

over an interval ``[t_k, t_{k+1}]`` of length ``dt`` partitioned into
``N`` equidistant sub-steps of size ``Δt = dt / N`` with conventions
(ControlToolbox §SDE):

    x_{k,n} ≈ x(t_k + n Δt),
    u_{k,n} = u_k                       (zero-order hold)
    d_{k,n} = d_k                       (zero-order hold)
    Δω_{k,n} = z_{k,n} √Δt,  z_{k,n} ~ N(0, I)

**Explicit-Explicit Euler-Maruyama (EE)** — both drift and diffusion
evaluated at the current sub-step:

    x_{k,n+1} = x_{k,n} + f(x_{k,n}, u_k, d_k, p, t_{k,n}) Δt
                       + sigma(x_{k,n}, u_k, d_k, p, t_{k,n}) Δω_{k,n}

**Implicit-Explicit (IE)** — drift evaluated at the *next* sub-step
(implicit), diffusion evaluated explicitly:

    x_{k,n+1} = x_{k,n} + f(x_{k,n+1}, u_k, d_k, p, t_{k,n+1}) Δt
                       + sigma(x_{k,n}, u_k, d_k, p, t_{k,n}) Δω_{k,n}

The implicit equation is solved by Newton's method on the residual

    R(x_{k,n+1}) = x_{k,n+1} − x_{k,n}
                   − f(x_{k,n+1}, u_k, d_k, p, t_{k,n+1}) Δt
                   − sigma(x_{k,n}, u_k, d_k, p, t_{k,n}) Δω_{k,n} = 0,

with Jacobian

    ∂R/∂x = I − (∂f/∂x)(x_{k,n+1}, …) Δt.

Use the EE scheme when the drift dynamics are non-stiff and the IE
scheme when stiff.
"""

from __future__ import annotations

import numpy as np

from ..models import ContinuousDiscreteModel
from .._utils import _newton_solve


class SDESimulator:
    """
    Euler-Maruyama / implicit-explicit simulator for continuous-discrete
    SDE models (ControlToolbox §SDE).

    Parameters
    ----------
    model : ContinuousDiscreteModel
        The nonlinear SDE system to simulate.
    dt : float
        Measurement sampling interval (seconds).  This defines the
        coarse time step between observations.
    n_steps : int, optional
        Number of integration sub-steps per measurement interval.
        Default: 10.
    scheme : {"EE", "IE"}, optional
        Integration scheme.  ``"EE"`` = explicit-explicit Euler-Maruyama
        (default), ``"IE"`` = implicit-explicit (implicit drift).
    seed : int or None, optional
        Random seed for reproducibility.
    newton_tol : float, optional
        Newton tolerance for the IE drift solve.  Default: 1e-12.
    newton_max_iter : int, optional
        Maximum Newton iterations for the IE drift solve.  Default: 50.
    """

    def __init__(
        self,
        model: ContinuousDiscreteModel,
        dt: float,
        n_steps: int = 10,
        scheme: str = "EE",
        seed: int | None = None,
        newton_tol: float = 1e-12,
        newton_max_iter: int = 50,
    ) -> None:
        if scheme not in ("EE", "IE"):
            raise ValueError(f"Unknown scheme '{scheme}'; choose 'EE' or 'IE'.")
        self._model = model
        self._dt = dt
        self._n_steps = n_steps
        self._scheme = scheme
        self._rng = np.random.default_rng(seed)
        self._newton_tol = newton_tol
        self._newton_max_iter = newton_max_iter

    def step(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """
        Simulate one measurement interval from t to t + dt.

        Applies ``n_steps`` sub-steps with step size ``Δt = dt / n_steps``.
        The input u and disturbance d are held constant over the interval
        (zero-order hold).

        Parameters
        ----------
        x : (nx,) ndarray  — state at time t.
        u : (nu,) ndarray  — control input over [t, t+dt].
        d : (nd,) ndarray  — disturbance over [t, t+dt].
        p : (nparams,) ndarray  — parameter vector.
        t : float          — current time.

        Returns
        -------
        x_next : (nx,) state at t + dt (one realisation).
        """
        h = self._dt / self._n_steps
        sqrt_h = np.sqrt(h)
        nx = x.shape[0]

        x_cur = x.copy()
        t_cur = t

        for _ in range(self._n_steps):
            sigma_val = self._model.sigma(x_cur, u, d, p, t_cur)  # (nx, nw)
            nw = sigma_val.shape[1]
            z = self._rng.standard_normal(nw)
            noise = sigma_val @ z * sqrt_h

            if self._scheme == "EE":
                f_val = self._model.f(x_cur, u, d, p, t_cur)
                x_cur = x_cur + f_val * h + noise
            else:  # IE
                rhs = x_cur + noise
                t_next = t_cur + h

                def residual(xn: np.ndarray) -> np.ndarray:
                    return xn - rhs - self._model.f(xn, u, d, p, t_next) * h

                def jacobian(xn: np.ndarray) -> np.ndarray:
                    return np.eye(nx) - h * self._model.dfdx(xn, u, d, p, t_next)

                x_cur = _newton_solve(
                    residual, jacobian, x_cur.copy(),
                    tol=self._newton_tol, max_iter=self._newton_max_iter,
                )

            t_cur += h

        return x_cur

    def simulate(
        self,
        x0: np.ndarray,
        U: np.ndarray,
        D: np.ndarray,
        P: np.ndarray,
        t0: float = 0.0,
    ) -> np.ndarray:
        """
        Simulate over a full horizon of T measurement intervals.

        Parameters
        ----------
        x0 : (nx,) ndarray
            Initial state at time t0.
        U : (T, nu) ndarray
            Input trajectory; U[k] is applied over [t_k, t_{k+1}].
        D : (T, nd) ndarray
            Disturbance trajectory; D[k] applies over [t_k, t_{k+1}].
        P : (T, nparams) ndarray
            Parameter trajectory; P[k] applies over [t_k, t_{k+1}].
        t0 : float, optional
            Start time.  Default: 0.

        Returns
        -------
        X : (T+1, nx) ndarray
            State trajectory where X[0] = x0 and X[k+1] = step(X[k], ...).
        """
        T = U.shape[0]
        nx = x0.shape[0]
        X = np.empty((T + 1, nx))
        X[0] = x0.copy()
        t_cur = t0
        for k in range(T):
            X[k + 1] = self.step(X[k], U[k], D[k], P[k], t_cur)
            t_cur += self._dt
        return X
