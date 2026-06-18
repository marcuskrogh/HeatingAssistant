"""
One-shot successive-linearisation OCP for nonlinear continuous-discrete models.

:class:`CDLinearizedOptimalControlProblem` linearises a nonlinear
:class:`~mbc.models.ContinuousDiscreteModel` around the current operating
point, ZOH-discretises the local LTI model, and solves the standard
:class:`~mbc.control.OptimalControlProblem` QP in deviation coordinates.

This is the open-loop OCP counterpart of
:class:`~mbc.control.CDLinearizedMPCController` (which closes the loop with
an estimator).  It exposes the same tracking / constraint vocabulary as the
other OCP classes:

* setpoint tracking via ``Q``, ``P``, and ``z_ref``
* hard input box ``u_min`` / ``u_max``
* soft output box via ``y_offset`` and ``rho``
* input ROM penalty ``S`` and hard ROM box ``du_min`` / ``du_max``
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

import numpy as np

from .cd_linearized_mpc import (
    _DeviationLinearDiscreteModel,
    discretize_cd_linearization,
    linearize_cd_model,
)
from .ocp import OptimalControlProblem

if TYPE_CHECKING:
    from ..models import ContinuousDiscreteModel


class CDLinearizedOptimalControlProblem:
    """
    Discrete linearised tracking OCP for a nonlinear CD plant.

  Parameters
  ----------
  model : ContinuousDiscreteModel
      Nonlinear plant to linearise at each solve.
  N : int
      Prediction horizon.
  Q, R, P, S : array-like
      Quadratic stage / terminal / ROM costs (deviation coordinates).
  z_ref : (nz,) array-like, optional
      Output setpoint.  ``None`` → zeros.
  u_min, u_max : (nu,) array-like
      Hard absolute input bounds.
  du_min, du_max : (nu,) array-like, optional
      Hard input ROM bounds on ``Δu``.
  rho, y_offset : float
      Soft output constraint penalty and half-width.
  dt : float, optional
      Sampling interval.  ``None`` → ``model.dt`` if present else ``1.0``.
  """

    def __init__(
        self,
        model: "ContinuousDiscreteModel",
        N: int,
        Q: Any,
        R: Any,
        P: Any | None = None,
        S: Any | None = None,
        du_min: Any | None = None,
        du_max: Any | None = None,
        rho: float = 1e4,
        rho_lin: float = 0.0,
        y_offset: float = 2.0,
        z_ref: Any | None = None,
        u_min: Any | None = None,
        u_max: Any | None = None,
        dt: float | None = None,
        solver: str = "highs",
        solver_options: dict[str, Any] | None = None,
        formulation: str = "auto",
    ) -> None:
        self._model = model
        self._N = int(N)
        self._dt = float(dt if dt is not None else getattr(model, "dt", 1.0))
        self._z_ref = (
            np.zeros(model.nz, dtype=float)
            if z_ref is None
            else np.asarray(z_ref, dtype=float).reshape(model.nz)
        )

        if u_min is None or u_max is None:
            if hasattr(model, "u_bounds"):
                u_min_def, u_max_def = model.u_bounds
                u_min = u_min_def if u_min is None else u_min
                u_max = u_max_def if u_max is None else u_max
            else:
                raise ValueError("u_min and u_max are required when model has no u_bounds.")

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
            du_min=du_min,
            du_max=du_max,
            rho=rho,
            rho_lin=rho_lin,
            y_offset=y_offset,
            solver=solver,
            solver_options=solver_options,
            formulation=formulation,
        )

    @property
    def N(self) -> int:
        return self._N

    @property
    def nu(self) -> int:
        return self._model.nu

    def solve(
        self,
        x0: Any,
        D: Any,
        u_prev: Any | None = None,
        p: Any | None = None,
        t: float = 0.0,
        z_ref: Any | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Linearise at ``x0``, solve the deviation QP, return absolute trajectories.

        Parameters
        ----------
        x0 : (nx,) array-like
            Current state (linearisation point).
        D : (N·nd,) array-like
            Stacked disturbance forecast.
        u_prev : (nu,) array-like, optional
            Previously applied input (absolute).
        p : parameter vector, optional
        t : float
            Time stamp for Jacobians.
        z_ref : (nz,) array-like, optional
            Override output setpoint for this solve.

        Returns
        -------
        U : (N·nu,) ndarray — absolute optimal input sequence.
        X : (N·nx,) ndarray — absolute predicted state trajectory.
        """
        x0 = np.asarray(x0, dtype=float).reshape(self._model.nx)
        p_ = np.array([], dtype=float) if p is None else np.asarray(p, dtype=float)
        u_prev_abs = (
            np.zeros(self._model.nu, dtype=float)
            if u_prev is None
            else np.asarray(u_prev, dtype=float).reshape(self._model.nu)
        )

        if z_ref is not None:
            z_ref_use = np.asarray(z_ref, dtype=float).reshape(self._model.nz)
        else:
            z_ref_use = self._z_ref

        x_ss = x0.copy()
        u_ss = u_prev_abs.copy()
        d_ss = (
            np.asarray(D, dtype=float).reshape(self._N, self._model.nd)[0]
            if self._model.nd > 0
            else np.zeros(0, dtype=float)
        )

        lin = linearize_cd_model(self._model, x_ss, u_ss, d_ss, p_, t)
        disc = discretize_cd_linearization(lin, self._dt)
        Cz = disc["Cz"]

        x_ref_dev = self._x_ref_dev_from_z(z_ref_use, x_ss, Cz)

        self._lin_model.update(
            Ad=disc["Ad"],
            Bd=disc["Bd"],
            Ed=disc["Ed"],
            Cm=disc["Cm"],
            Cz=Cz,
            Qd=disc["Qd"],
            Rm=np.asarray(self._model.Rm, dtype=float),
            x_ss=x_ss,
            u_ss=u_ss,
            d_ss=d_ss,
            x_ref=x_ref_dev,
        )

        D_np = np.asarray(D, dtype=float).reshape(-1)
        d_dev = D_np.reshape(self._N, self._model.nd) - d_ss.reshape(1, -1)
        D_dev = d_dev.reshape(-1)

        U_dev, X_dev = self._ocp.solve(
            x0=np.zeros(self._model.nx, dtype=float),
            D=D_dev,
            x_ref=x_ref_dev,
            u_prev=np.zeros(self._model.nu, dtype=float),
        )

        U_dev_np = U_dev.reshape(self._N, self._model.nu)
        X_dev_np = X_dev.reshape(self._N, self._model.nx)
        U_abs = (U_dev_np + u_ss.reshape(1, -1)).reshape(-1)
        X_abs = (X_dev_np + x_ss.reshape(1, -1)).reshape(-1)

        u_min_abs, u_max_abs = self._lin_model.abs_u_bounds
        U_abs_2d = U_abs.reshape(self._N, self._model.nu)
        U_abs_2d = np.clip(U_abs_2d, u_min_abs, u_max_abs)
        return U_abs_2d.reshape(-1), X_abs

    def _x_ref_dev_from_z(
        self, z_ref: np.ndarray, x_ss: np.ndarray, Cz: np.ndarray
    ) -> np.ndarray:
        z_err = z_ref - Cz @ x_ss
        if Cz.shape[0] == Cz.shape[1]:
            try:
                return np.linalg.solve(Cz, z_err)
            except np.linalg.LinAlgError:
                pass
        return np.linalg.lstsq(Cz, z_err, rcond=None)[0]
