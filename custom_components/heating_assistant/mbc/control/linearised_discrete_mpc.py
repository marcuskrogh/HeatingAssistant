"""
Successive-linearisation MPC for nonlinear discrete-time plants.

:class:`LinearisedDiscreteMPC` composes a discrete-time state estimator with
:class:`~mbc.control.StandardLinearDiscreteOCP`, updating a local linear
discrete-time model at each sampling instant.
"""

from __future__ import annotations

from typing import Any, Protocol

import numpy as np

from .cd_linearized_mpc import _DeviationLinearDiscreteModel
from .ocp import StandardLinearDiscreteOCP


class DiscreteNonlinearModel(Protocol):
    """Nonlinear discrete-time plant exposing one-step-map Jacobians."""

    @property
    def nx(self) -> int: ...

    @property
    def nu(self) -> int: ...

    @property
    def nd(self) -> int: ...

    @property
    def nym(self) -> int: ...

    @property
    def nz(self) -> int: ...

    @property
    def Rm(self) -> np.ndarray: ...

    @property
    def Qd(self) -> np.ndarray: ...

    def dfdx(self, x, u, d, p, t) -> np.ndarray: ...

    def dfdu(self, x, u, d, p, t) -> np.ndarray: ...

    def dfdd(self, x, u, d, p, t) -> np.ndarray: ...

    def dhmdx(self, x, u, d, p, t=0.0) -> np.ndarray: ...

    def dgmdx(self, x, u, d, p, t) -> np.ndarray: ...


def linearize_discrete_model(
    model: DiscreteNonlinearModel,
    x_ss: np.ndarray,
    u_ss: np.ndarray,
    d_ss: np.ndarray,
    p: np.ndarray,
    t: float = 0.0,
) -> dict[str, np.ndarray]:
    """Linearise the discrete map ``x[k+1] = f(x[k], u[k], d[k])`` at ``(x_ss, u_ss, d_ss)``."""
    return {
        "Ad": model.dfdx(x_ss, u_ss, d_ss, p, t),
        "Bd": model.dfdu(x_ss, u_ss, d_ss, p, t),
        "Ed": model.dfdd(x_ss, u_ss, d_ss, p, t),
        "Cm": model.dhmdx(x_ss, u_ss, d_ss, p, t),
        "Cz": model.dgmdx(x_ss, u_ss, d_ss, p, t),
        "Qd": np.asarray(model.Qd, dtype=float),
    }


class LinearisedDiscreteMPC:
    """
    MPC for a linearised discrete-time plant, discrete estimator, and discrete-time OCP.

    Successive linearisation of a nonlinear discrete-time model at each step,
    solved via :class:`StandardLinearDiscreteOCP` in deviation coordinates.
    """

    def __init__(
        self,
        model: DiscreteNonlinearModel,
        estimator: Any,
        N: int,
        Q: Any,
        R: Any,
        u_min: np.ndarray,
        u_max: np.ndarray,
        x_ref: np.ndarray | None = None,
        P: Any | None = None,
        S: Any | None = None,
        du_min: Any | None = None,
        du_max: Any | None = None,
        rho: float = 1e4,
        rho_lin: float = 0.0,
        y_offset: float = 2.0,
    ) -> None:
        self._model = model
        self._estimator = estimator
        self._N = int(N)

        self._x_ref_abs = (
            np.zeros(model.nx, dtype=float)
            if x_ref is None
            else np.asarray(x_ref, dtype=float).reshape(model.nx)
        )

        self._lin_model = _DeviationLinearDiscreteModel(
            nx=model.nx,
            nu=model.nu,
            nd=model.nd,
            nym=model.nym,
            nz=model.nz,
            u_min_abs=np.asarray(u_min, dtype=float),
            u_max_abs=np.asarray(u_max, dtype=float),
        )

        self._ocp = StandardLinearDiscreteOCP(
            model=self._lin_model,
            N=self._N,
            Q=Q,
            R=R,
            P=P,
            S=S,
            du_min=du_min,
            du_max=du_max,
            rho=rho,
            rho_lin=rho_lin,
            y_offset=y_offset,
        )

        self._u_prev = np.zeros(model.nu, dtype=float)
        self._d_prev = np.zeros(model.nd, dtype=float)
        self._last_D_dev = np.zeros((self._N, model.nd), dtype=float)

    @property
    def x_ref(self) -> np.ndarray:
        return self._x_ref_abs.copy()

    @x_ref.setter
    def x_ref(self, val: np.ndarray) -> None:
        self._x_ref_abs = np.asarray(val, dtype=float).reshape(self._model.nx)

    @property
    def last_disturbance_deviation_trajectory(self) -> np.ndarray:
        return self._last_D_dev.copy()

    def step(
        self,
        y: np.ndarray,
        d: np.ndarray,
        p: np.ndarray | None = None,
        t: float = 0.0,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        y = np.asarray(y, dtype=float).reshape(self._model.nym)
        d_now = np.asarray(d, dtype=float).reshape(self._model.nd)
        p_ = np.array([], dtype=float) if p is None else np.asarray(p, dtype=float)

        est_out = self._estimator.step(y, self._u_prev, self._d_prev, p_, t)
        x_hat = np.asarray(est_out[0], dtype=float).reshape(self._model.nx)

        x_ss = x_hat.copy()
        u_ss = self._u_prev.copy()
        d_ss = d_now.copy()

        disc = linearize_discrete_model(self._model, x_ss, u_ss, d_ss, p_, t)
        x_ref_dev = self._x_ref_abs - x_ss
        self._lin_model.update(
            Ad=disc["Ad"],
            Bd=disc["Bd"],
            Ed=disc["Ed"],
            Cm=disc["Cm"],
            Cz=disc["Cz"],
            Qd=disc["Qd"],
            Rm=np.asarray(self._model.Rm, dtype=float),
            x_ss=x_ss,
            u_ss=u_ss,
            d_ss=d_ss,
            x_ref=x_ref_dev,
        )

        D_dev_np = np.zeros((self._N, self._model.nd), dtype=float)
        self._last_D_dev = D_dev_np.copy()
        D_dev = D_dev_np.reshape(-1)

        U_dev, X_dev = self._ocp.solve(
            x0=np.zeros(self._model.nx, dtype=float),
            D=D_dev,
            x_ref=x_ref_dev,
            u_prev=np.zeros(self._model.nu, dtype=float),
        )

        U_dev_np = np.asarray(U_dev, dtype=float).reshape(self._N, self._model.nu)
        X_dev_np = np.asarray(X_dev, dtype=float).reshape(self._N, self._model.nx)

        U_abs = U_dev_np + u_ss.reshape(1, -1)
        X_abs = X_dev_np + x_ss.reshape(1, -1)
        u_abs = U_abs[0].copy()

        u_min_abs, u_max_abs = self._lin_model.abs_u_bounds
        u_abs = np.minimum(np.maximum(u_abs, u_min_abs), u_max_abs)
        U_abs[0] = u_abs

        self._u_prev = u_abs.copy()
        self._d_prev = d_now.copy()

        return u_abs, U_abs, X_abs
