"""
Successive-linearisation MPC for nonlinear continuous-discrete models.

This module provides a linear-QP MPC controller that, at each control interval,
linearises a nonlinear continuous-discrete model around an operating point,
ZOH-discretises the local model, and solves the existing linear
:class:`~mbc.control.OptimalControlProblem` in deviation coordinates.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .._utils import _fd_jacobian, _zoh_full, _van_loan
from ..models import LinearDiscreteModel, ContinuousDiscreteModel
from .ocp import OptimalControlProblem


class _DeviationLinearDiscreteModel(LinearDiscreteModel):
    """Mutable linear-discrete model in deviation coordinates."""

    def __init__(
        self,
        nx: int,
        nu: int,
        nd: int,
        nym: int,
        nz: int,
        u_min_abs: np.ndarray,
        u_max_abs: np.ndarray,
    ) -> None:
        self._nx = nx
        self._nu = nu
        self._nd = nd
        self._nym = nym
        self._nz = nz

        self._u_min_abs = np.asarray(u_min_abs, dtype=float).reshape(nu)
        self._u_max_abs = np.asarray(u_max_abs, dtype=float).reshape(nu)

        self._Ad = np.eye(nx)
        self._Bd = np.zeros((nx, nu))
        self._Ed = np.zeros((nx, nd))
        self._Cm = np.zeros((nym, nx))
        self._Cz = np.zeros((nz, nx))
        self._Qd = np.eye(nx) * 1e-8
        self._Rm = np.eye(nym)

        self._x = np.zeros(nx)
        self._x_ref = np.zeros(nx)

        self.x_ss = np.zeros(nx)
        self.u_ss = np.zeros(nu)
        self.d_ss = np.zeros(nd)

    def update(
        self,
        *,
        Ad: np.ndarray,
        Bd: np.ndarray,
        Ed: np.ndarray,
        Cm: np.ndarray,
        Cz: np.ndarray,
        Qd: np.ndarray,
        Rm: np.ndarray,
        x_ss: np.ndarray,
        u_ss: np.ndarray,
        d_ss: np.ndarray,
        x_ref: np.ndarray,
    ) -> None:
        self._Ad = np.asarray(Ad, dtype=float)
        self._Bd = np.asarray(Bd, dtype=float)
        self._Ed = np.asarray(Ed, dtype=float)
        self._Cm = np.asarray(Cm, dtype=float)
        self._Cz = np.asarray(Cz, dtype=float)
        self._Qd = np.asarray(Qd, dtype=float)
        self._Rm = np.asarray(Rm, dtype=float)

        self.x_ss = np.asarray(x_ss, dtype=float).reshape(self._nx)
        self.u_ss = np.asarray(u_ss, dtype=float).reshape(self._nu)
        self.d_ss = np.asarray(d_ss, dtype=float).reshape(self._nd)

        self._x = np.zeros(self._nx)
        self._x_ref = np.asarray(x_ref, dtype=float).reshape(self._nx)

    @property
    def nx(self) -> int:
        return self._nx

    @property
    def nu(self) -> int:
        return self._nu

    @property
    def nd(self) -> int:
        return self._nd

    @property
    def Ad(self) -> np.ndarray:
        return self._Ad

    @property
    def Bd(self) -> np.ndarray:
        return self._Bd

    @property
    def Ed(self) -> np.ndarray:
        return self._Ed

    @property
    def Cm(self) -> np.ndarray:
        return self._Cm

    @property
    def Cz(self) -> np.ndarray:
        return self._Cz

    @property
    def Qd(self) -> np.ndarray:
        return self._Qd

    @property
    def Rm(self) -> np.ndarray:
        return self._Rm

    @property
    def x(self) -> list[float]:
        return self._x.tolist()

    @x.setter
    def x(self, val: list[float]) -> None:
        self._x = np.asarray(val, dtype=float).reshape(self._nx)

    @property
    def x_ref(self) -> np.ndarray:
        return self._x_ref.copy()

    @property
    def u_bounds(self) -> tuple[np.ndarray, np.ndarray]:
        # Bounds are in deviation coordinates: du = u - u_ss.
        return self._u_min_abs - self.u_ss, self._u_max_abs - self.u_ss

    @property
    def abs_u_bounds(self) -> tuple[np.ndarray, np.ndarray]:
        """Absolute input bounds (u_min_abs, u_max_abs)."""
        return self._u_min_abs.copy(), self._u_max_abs.copy()


def linearize_cd_model(
    model: ContinuousDiscreteModel,
    x_ss: np.ndarray,
    u_ss: np.ndarray,
    d_ss: np.ndarray,
    p: np.ndarray,
    t: float,
) -> dict[str, np.ndarray]:
    """Linearise model around operating point (x_ss, u_ss, d_ss, p, t)."""
    x_ss = np.asarray(x_ss, dtype=float)
    u_ss = np.asarray(u_ss, dtype=float)
    d_ss = np.asarray(d_ss, dtype=float)
    p = np.asarray(p, dtype=float)

    A = model.dfdx(x_ss, u_ss, d_ss, p, t)
    B = model.dfdu(x_ss, u_ss, d_ss, p, t)
    E = model.dfdd(x_ss, u_ss, d_ss, p, t)

    Cm = model.dhmdx(x_ss, u_ss, d_ss, p, t)
    Cz = model.dgmdx(x_ss, u_ss, d_ss, p, t)

    Dm = model.dhmdu(x_ss, u_ss, d_ss, p, t)
    Fm = model.dhmdd(x_ss, u_ss, d_ss, p, t)
    Dz = model.dgmdu(x_ss, u_ss, d_ss, p, t)

    if model.nd > 0:
        Fz = _fd_jacobian(lambda d_: model.gm(x_ss, u_ss, d_, p, t), d_ss)
    else:
        Fz = np.zeros((model.nz, 0))

    G = model.sigma(x_ss, u_ss, d_ss, p, t)

    return {
        "A": A,
        "B": B,
        "E": E,
        "Cm": Cm,
        "Cz": Cz,
        "Dm": Dm,
        "Fm": Fm,
        "Dz": Dz,
        "Fz": Fz,
        "G": G,
    }


def discretize_cd_linearization(
    lin: dict[str, np.ndarray],
    dt: float,
) -> dict[str, np.ndarray]:
    """Discretise local linearised model using ZOH and Van Loan."""
    A = lin["A"]
    B = lin["B"]
    E = lin["E"]
    G = lin["G"]

    Ad, Bd, Ed = _zoh_full(A, B, E, dt)

    if G.size == 0:
        Qd = np.zeros((A.shape[0], A.shape[0]))
    else:
        Qd = _van_loan(A, G, np.eye(G.shape[1]), dt)

    return {
        "Ad": Ad,
        "Bd": Bd,
        "Ed": Ed,
        "Qd": Qd,
        "Cm": lin["Cm"],
        "Cz": lin["Cz"],
        "Dm": lin["Dm"],
        "Fm": lin["Fm"],
        "Dz": lin["Dz"],
        "Fz": lin["Fz"],
    }


class CDLinearizedMPCController:
    """
    Successive-linearisation MPC for nonlinear continuous-discrete models.

    The controller keeps existing nonlinear estimators unchanged and reuses
    :class:`OptimalControlProblem` by updating a mutable deviation linear model
    at each sampling instant.
    """

    def __init__(
        self,
        model: ContinuousDiscreteModel,
        estimator: Any,
        N: int,
        Q: Any,
        R: Any,
        dt: float,
        u_min: np.ndarray,
        u_max: np.ndarray,
        x_ref: np.ndarray | None = None,
        P: Any | None = None,
        S: Any | None = None,
        rho: float = 1e4,
        y_offset: float = 2.0,
    ) -> None:
        self._model = model
        self._estimator = estimator
        self._N = int(N)
        self._dt = float(dt)

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

        self._ocp = OptimalControlProblem(
            model=self._lin_model,
            N=self._N,
            Q=Q,
            R=R,
            P=P,
            S=S,
            rho=rho,
            y_offset=y_offset,
        )

        self._u_prev = np.zeros(model.nu, dtype=float)
        self._d_prev = np.zeros(model.nd, dtype=float)
        self._last_D_dev = np.zeros((self._N, model.nd), dtype=float)

    @property
    def x_ref(self) -> np.ndarray:
        """Absolute state reference used for tracking."""
        return self._x_ref_abs.copy()

    @x_ref.setter
    def x_ref(self, val: np.ndarray) -> None:
        self._x_ref_abs = np.asarray(val, dtype=float).reshape(self._model.nx)

    @property
    def last_disturbance_deviation_trajectory(self) -> np.ndarray:
        """Most recent disturbance trajectory in deviation coordinates."""
        return self._last_D_dev.copy()

    def step(
        self,
        y: np.ndarray,
        d: np.ndarray,
        p: np.ndarray | None = None,
        t: float = 0.0,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Execute one closed-loop step.

        Disturbance-hold assumption: the measured disturbance at the current
        interval start is used as operating disturbance (`d_ss`) and held
        constant over the horizon in the local linear MPC problem.
        """
        y = np.asarray(y, dtype=float).reshape(self._model.nym)
        d_now = np.asarray(d, dtype=float).reshape(self._model.nd)
        p_ = np.array([], dtype=float) if p is None else np.asarray(p, dtype=float)

        est_out = self._estimator.step(y, self._u_prev, self._d_prev, p_, t)
        x_hat = np.asarray(est_out[0], dtype=float).reshape(self._model.nx)

        x_ss = x_hat.copy()
        u_ss = self._u_prev.copy()
        d_ss = d_now.copy()

        lin = linearize_cd_model(self._model, x_ss, u_ss, d_ss, p_, t)
        disc = discretize_cd_linearization(lin, self._dt)

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

        # Deviation MPC uses constant disturbance over the horizon: dd[k] = 0.
        D_dev_np = np.zeros((self._N, self._model.nd), dtype=float)
        self._last_D_dev = D_dev_np.copy()
        D_dev = D_dev_np.reshape(-1)

        x0_dev = np.zeros(self._model.nx, dtype=float)
        u_prev_dev = np.zeros(self._model.nu, dtype=float)

        U_dev, X_dev = self._ocp.solve(
            x0=x0_dev,
            D=D_dev,
            x_ref=x_ref_dev,
            u_prev=u_prev_dev,
        )

        U_dev_np = np.asarray(U_dev, dtype=float).reshape(self._N, self._model.nu)
        X_dev_np = np.asarray(X_dev, dtype=float).reshape(self._N, self._model.nx)

        U_abs = U_dev_np + u_ss.reshape(1, -1)
        X_abs = X_dev_np + x_ss.reshape(1, -1)
        u_abs = U_abs[0].copy()

        # Numerical safety against tiny solver tolerances beyond box constraints.
        u_min_abs, u_max_abs = self._lin_model.abs_u_bounds
        u_abs = np.minimum(np.maximum(u_abs, u_min_abs), u_max_abs)
        U_abs[0] = u_abs

        self._u_prev = u_abs.copy()
        self._d_prev = d_now.copy()

        return u_abs, U_abs, X_abs
