"""
Continuous-discrete plant specialisations of discrete- and continuous-time OCPs.

``StandardLinearContinuousDiscreteOCP``
    Standard discrete-time tracking OCP for linear continuous-discrete plants.
    ZOH-discretises the plant and inherits :class:`~mbc.control.StandardLinearDiscreteOCP`.

``StandardContinuousOCP``
    Standard continuous-time tracking OCP for nonlinear continuous-discrete
    plants.  Thin wrapper around :class:`~mbc.control.GeneralContinuousOCP`.
"""

from __future__ import annotations

from typing import Any, Tuple, TYPE_CHECKING

import numpy as np

from .ocp import StandardLinearDiscreteOCP
from .qp_solver import QPSolverBackend
from .nlp_solver import NLPScalingPolicy, NLPSolverBackend

if TYPE_CHECKING:
    from ..models import ContinuousDiscreteModel, LinearContinuousDiscreteModel


class _CDModelAdapter:
    """Adapter exposing ZOH-discretised matrices for ``StandardLinearDiscreteOCP``."""

    def __init__(self, model: "LinearContinuousDiscreteModel") -> None:
        self._m = model
        from .._utils import _zoh_full
        self._Ad_np, self._Bd_np, self._Ed_np = _zoh_full(
            model.A, model.B, model.E, model.dt
        )

    @property
    def nx(self) -> int:
        return self._m.nx

    @property
    def nu(self) -> int:
        return self._m.nu

    @property
    def nd(self) -> int:
        return self._m.nd

    @property
    def Cm(self) -> np.ndarray:
        return self._m.Cm

    @property
    def Cz(self) -> np.ndarray:
        return self._m.Cz

    @property
    def Ad(self) -> np.ndarray:
        return self._Ad_np

    @property
    def Bd(self) -> np.ndarray:
        return self._Bd_np

    @property
    def Ed(self) -> np.ndarray:
        return self._Ed_np

    @property
    def u_bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        return self._m.u_bounds


class StandardLinearContinuousDiscreteOCP(StandardLinearDiscreteOCP):
    """
    Standard discrete-time tracking OCP for linear continuous-discrete plants.

    The plant is ZOH-discretised once at construction; the resulting QP is
    solved by :class:`~mbc.control.StandardLinearDiscreteOCP`.
    """

    def __init__(
        self,
        model: "LinearContinuousDiscreteModel",
        N: int,
        Q: Any,
        R: Any,
        P: Any | None = None,
        S: Any | None = None,
        du_min: Any | None = None,
        du_max: Any | None = None,
        rho: float = 1e4,
        y_offset: float = 2.0,
        solver: str | QPSolverBackend = "highs",
        solver_options: dict[str, Any] | None = None,
        formulation: str = "auto",
    ) -> None:
        self._cd_model = model
        super().__init__(
            model=_CDModelAdapter(model),  # type: ignore[arg-type]
            N=N,
            Q=Q,
            R=R,
            P=P,
            S=S,
            du_min=du_min,
            du_max=du_max,
            rho=rho,
            y_offset=y_offset,
            solver=solver,
            solver_options=solver_options,
            formulation=formulation,
        )


class StandardContinuousOCP:
    """
    Standard continuous-time tracking OCP for nonlinear continuous-discrete plants.

    Thin wrapper around :class:`~mbc.control.GeneralContinuousOCP` with a
    quadratic-tracking-friendly constructor (``Q``, ``R``, ``P``, ``S``, ``c_u``).
    """

    def __init__(
        self,
        model: "ContinuousDiscreteModel",
        N: int,
        Q: np.ndarray,
        R: np.ndarray,
        P: np.ndarray | None = None,
        S: np.ndarray | None = None,
        c_u: np.ndarray | None = None,
        z_ref: np.ndarray | None = None,
        u_min: np.ndarray | None = None,
        u_max: np.ndarray | None = None,
        du_min: np.ndarray | None = None,
        du_max: np.ndarray | None = None,
        x_min: np.ndarray | None = None,
        x_max: np.ndarray | None = None,
        rho_x: float = 1e4,
        z_min: np.ndarray | None = None,
        z_max: np.ndarray | None = None,
        rho_z: float = 1e4,
        n_steps: int = 10,
        solver: str | NLPSolverBackend = "SLSQP",
        solver_options: dict | None = None,
        solver_scaling: NLPScalingPolicy | dict | None = None,
        dt: float | None = None,
    ) -> None:
        from .enmpc import GeneralContinuousOCP

        Q_arr = np.asarray(Q, dtype=float)
        R_arr = np.asarray(R, dtype=float)
        z_ref_arr = (
            np.asarray(z_ref, dtype=float)
            if z_ref is not None
            else np.zeros(model.nz)
        )

        def _lagrange(t, x, y, u, theta, _R=R_arr):
            return float(u @ _R @ u)

        def _lagrange_jac(t, x, y, u, theta, _R=R_arr):
            return (np.zeros_like(x), np.zeros_like(y), 2.0 * _R @ u)

        if P is not None:
            P_arr = np.asarray(P, dtype=float)
            is_dae = hasattr(model, "ny") and hasattr(model, "g")
            zeros_u = np.zeros(model.nu)
            zeros_d = np.zeros(model.nd)

            def _mayer(
                x, y, theta,
                _P=P_arr, _zref=z_ref_arr, _model=model,
                _is_dae=is_dae, _zu=zeros_u, _zd=zeros_d,
            ):
                if _is_dae:
                    z = _model.gm(x, y, _zu, _zd, theta, 0.0)
                else:
                    z = _model.gm(x, _zu, _zd, theta, 0.0)
                e = z - _zref
                return float(e @ _P @ e)

            def _mayer_jac(
                x, y, theta,
                _P=P_arr, _zref=z_ref_arr, _model=model,
                _is_dae=is_dae, _zu=zeros_u, _zd=zeros_d,
            ):
                if _is_dae:
                    z = _model.gm(x, y, _zu, _zd, theta, 0.0)
                    Pgrad = 2.0 * _P @ (z - _zref)
                    dgmdx_val = _model.dgmdx(x, y, _zu, _zd, theta, 0.0)
                    dgmdy_val = _model.dgmdy(x, y, _zu, _zd, theta, 0.0)
                    return (dgmdx_val.T @ Pgrad, dgmdy_val.T @ Pgrad)
                else:
                    z = _model.gm(x, _zu, _zd, theta, 0.0)
                    Pgrad = 2.0 * _P @ (z - _zref)
                    dgmdx_val = _model.dgmdx(x, _zu, _zd, theta, 0.0)
                    return (dgmdx_val.T @ Pgrad, np.zeros(0))
        else:
            _mayer = None
            _mayer_jac = None

        self._ocp = GeneralContinuousOCP(
            model,
            N,
            lagrange=_lagrange,
            lagrange_jac=_lagrange_jac,
            mayer=_mayer,
            mayer_jac=_mayer_jac,
            Q_z=Q_arr,
            z_ref=z_ref_arr,
            Q_du=np.asarray(S, dtype=float) if S is not None else None,
            p_u_eco=np.asarray(c_u, dtype=float) if c_u is not None else None,
            u_min=u_min,
            u_max=u_max,
            du_min=du_min,
            du_max=du_max,
            x_min=x_min,
            x_max=x_max,
            rho_x_2=rho_x,
            z_min=z_min,
            z_max=z_max,
            rho_z_2=rho_z,
            n_steps=n_steps,
            solver=solver,
            solver_options=solver_options,
            solver_scaling=solver_scaling,
            dt=dt,
        )

    @property
    def N(self) -> int:
        return self._ocp.N

    @property
    def nu(self) -> int:
        return self._ocp.nu

    def solve(
        self,
        x0: np.ndarray,
        d_trajectory: np.ndarray,
        u_prev: np.ndarray | None = None,
        x_prev: np.ndarray | None = None,
        y_prev: np.ndarray | None = None,
        p: np.ndarray | None = None,
        t0: float = 0.0,
    ) -> tuple[np.ndarray, float, dict]:
        return self._ocp.solve(
            x0, d_trajectory,
            u_prev=u_prev, x_prev=x_prev, y_prev=y_prev,
            p=p, t0=t0,
        )

    def step(
        self,
        x0: np.ndarray,
        d_trajectory: np.ndarray,
        u_prev: np.ndarray | None = None,
        x_prev: np.ndarray | None = None,
        y_prev: np.ndarray | None = None,
        p: np.ndarray | None = None,
        t0: float = 0.0,
    ) -> np.ndarray:
        return self._ocp.step(
            x0, d_trajectory,
            u_prev=u_prev, x_prev=x_prev, y_prev=y_prev,
            p=p, t0=t0,
        )


# Backward-compatible aliases (deprecated).
CDOptimalControlProblem = StandardLinearContinuousDiscreteOCP
LinearContinuousOCP = StandardLinearContinuousDiscreteOCP
CDTrackingOptimalControlProblem = StandardContinuousOCP
