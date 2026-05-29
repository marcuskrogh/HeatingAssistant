"""
Optimal Control Problems for continuous-discrete systems.

``CDOptimalControlProblem``
    A thin, typed wrapper around :class:`OptimalControlProblem` for
    :class:`~mbc.models.LinearContinuousDiscreteModel`.  Solves the
    receding-horizon QP via the lifted (batch) formulation using
    a convex-QP backend (OSQP by default; M.Sc. thesis Ch. 5).

``CDTrackingOptimalControlProblem``
    Convenience wrapper around
    :class:`~mbc.control.EconomicOptimalControlProblem` that exposes a
    tracking-OCP-friendly constructor (``Q``, ``R``, ``P``, ``S``, …).
    The underlying NLP follows the ControlToolbox §EMPC direct-simultaneous
    discretisation (implicit Euler + right-rectangular Lagrange) for both
    SDE and SDAE plant models.

Linear problem formulation (M.Sc. thesis, Ch. 5)
-------------------------------------------------
Given the continuous-discrete model

    dx = (A_c x + B_c u + E_c d) dt + G dw
    y[k] = C x[k] + v[k]

the controller operates on the ZOH-discretised prediction model

    x[k+1] = A_d x[k] + B_d u[k] + E_d d[k]

where (A_d, B_d, E_d) = ``model.discretize(d)``.

The receding-horizon quadratic program over horizon N is:

    min_{U}  J(U) = Σₖ₌₀ᴺ⁻¹ [ ‖y[k+1] − r‖²_Q  +  ‖u[k]‖²_R
                               + ‖Δu[k]‖²_S ]
                   + ‖y[N] − r‖²_P
                   + ρ Σₖ₌₀ᴺ⁻¹ ‖ε[k+1]‖²

    s.t.  x[k+1] = A_d x[k] + B_d u[k] + E_d d[k]
          u_min ≤ u[k] ≤ u_max                        (hard input box)
          y_min − ε[k+1] ≤ y[k+1] ≤ y_max + ε[k+1]   (soft output box)
          ε[k+1] ≥ 0

The lifted (batch) QP is solved through a pluggable convex-QP backend
(OSQP by default; HiGHS available).

Notation
---------
    nx  – state dimension          x ∈ ℝⁿˣ
    nu  – input dimension          u ∈ ℝⁿᵘ
    nd  – disturbance dimension    d ∈ ℝⁿᵈ
    nz  – output dimension         z ∈ ℝⁿᶻ
    N   – prediction horizon
    Ψ   – free-response matrix     Ψ ∈ ℝᴺⁿˣˣⁿˣ
    Γ   – forced-response matrix   Γ ∈ ℝᴺⁿˣˣᴺⁿᵘ
    Λ   – disturbance matrix       Λ ∈ ℝᴺⁿˣˣᴺⁿᵈ
"""

from __future__ import annotations

from typing import Any, Tuple, TYPE_CHECKING

import numpy as np

from .ocp import OptimalControlProblem
from .qp_solver import QPSolverBackend
from .nlp_solver import NLPScalingPolicy, NLPSolverBackend

if TYPE_CHECKING:
    from ..models import ContinuousDiscreteModel, LinearContinuousDiscreteModel


class _CDModelAdapter:
    """
    Thin adapter that wraps a ``LinearContinuousDiscreteModel`` and exposes
    the numpy interface expected by ``OptimalControlProblem``.

    ``OptimalControlProblem.solve`` accesses:
      - ``model.nx``, ``model.nu``, ``model.nd``  (int)
      - ``model.Cm``        (numpy ndarray, for output prediction)
      - ``model.Ad``        (numpy ndarray, ZOH-discretised state matrix)
      - ``model.Bd``        (numpy ndarray, ZOH-discretised input matrix)
      - ``model.Ed``        (numpy ndarray, ZOH-discretised disturbance matrix)
      - ``model.u_bounds``  (tuple of numpy (nu,) arrays)

    The ZOH-discretised matrices are computed once at construction time.
    """

    def __init__(self, model: "LinearContinuousDiscreteModel") -> None:
        self._m = model
        # Compute ZOH-discretised matrices once at construction time to avoid
        # repeated computation and any thread-safety concerns with lazy init.
        from .._utils import _zoh_full
        self._Ad_np, self._Bd_np, self._Ed_np = _zoh_full(
            model.A, model.B, model.E, model.dt
        )

    def _ensure_discretized(self) -> None:
        """No-op: matrices are computed in __init__."""

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
        """Measurement output matrix Cm (numpy ndarray)."""
        return self._m.Cm

    @property
    def Cz(self) -> np.ndarray:
        """Output matrix Cz (numpy ndarray) — used by ``OptimalControlProblem``."""
        return self._m.Cz

    @property
    def Ad(self) -> np.ndarray:
        """ZOH-discretised state-transition matrix Ad (numpy ndarray)."""
        self._ensure_discretized()
        return self._Ad_np

    @property
    def Bd(self) -> np.ndarray:
        """ZOH-discretised input matrix Bd (numpy ndarray)."""
        self._ensure_discretized()
        return self._Bd_np

    @property
    def Ed(self) -> np.ndarray:
        """ZOH-discretised disturbance matrix Ed (numpy ndarray)."""
        self._ensure_discretized()
        return self._Ed_np

    @property
    def u_bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        """Input box constraints (u_min, u_max), each a (nu,) ndarray."""
        return self._m.u_bounds


class CDOptimalControlProblem(OptimalControlProblem):
    """
    Receding-horizon QP for a linear continuous-discrete system.

    Inherits the full QP formulation and solver from
    ``OptimalControlProblem``.  The only difference is the model type:
    a ``_CDModelAdapter`` wraps a ``LinearContinuousDiscreteModel``,
    computing ZOH-discretised matrices ``Ad``, ``Bd``, ``Ed`` at construction
    time and exposing them as numpy arrays for the inherited ``solve`` method.
    The original CD model is also stored as ``self._cd_model`` for direct access.

    Parameters
    ----------
    model : LinearContinuousDiscreteModel
        Plant model.  Must implement ``nx``, ``nu``, ``nd``, ``A``, ``B``,
        ``E``, ``Cm``, ``dt``, and ``u_bounds``.
    N : int
        Prediction horizon (number of sampling intervals).
    Q : (ny, ny) array-like
        Stage output tracking cost  ‖y − r‖²_Q.
    R : (nu, nu) array-like
        Stage input cost  ‖u‖²_R.
    P : (ny, ny) array-like, optional
        Terminal output tracking cost.  Default: Q.
    S : (nu, nu) array-like, optional
        Input rate-of-movement cost  ‖Δu‖²_S.  ``None`` → disabled.
    rho : float, optional
        Penalty weight on soft output constraint violation.  Default: 1e4.
    y_offset : float, optional
        Symmetric half-width δ of the soft output constraint band
        ``[r − δ,  r + δ]``.  Default: 2.0.

    Notes
    -----
    ``CDOptimalControlProblem.solve`` has the same signature as
    ``OptimalControlProblem.solve`` — see that class for full documentation.
    """

    def __init__(
        self,
        model: "LinearContinuousDiscreteModel",
        N: int,
        Q: Any,
        R: Any,
        P: Any | None = None,
        S: Any | None = None,
        rho: float = 1e4,
        y_offset: float = 2.0,
        solver: str | QPSolverBackend = "highs",
        solver_options: dict[str, Any] | None = None,
        formulation: str = "auto",
    ) -> None:
        # Store the original CD model for direct access.
        self._cd_model = model

        # Pass an adapter to the parent so that OptimalControlProblem.solve
        # receives the numpy interface it expects (ZOH-discretised matrices).
        super().__init__(
            model=_CDModelAdapter(model),  # type: ignore[arg-type]
            N=N,
            Q=Q,
            R=R,
            P=P,
            S=S,
            rho=rho,
            y_offset=y_offset,
            solver=solver,
            solver_options=solver_options,
            formulation=formulation,
        )


# ── Nonlinear Tracking OCP (thin wrapper around the EOCP) ────────────────────


class CDTrackingOptimalControlProblem:
    """
    Tracking OCP for continuous-discrete nonlinear systems — thin wrapper
    around :class:`~mbc.control.EconomicOptimalControlProblem` that exposes a
    quadratic-tracking-friendly constructor (``Q``, ``R``, ``P``, ``S``, ``c_u``).

    The underlying NLP is the ControlToolbox §EMPC direct-simultaneous
    formulation (implicit Euler dynamics + right-rectangular Lagrange) —
    correct for both SDE and SDAE plant models.

    Cost function over prediction horizon N (with sub-step Δt = T_s / n_steps,
    output ``z = gm(x, y, u, d, p, t)``):

        J = Σ_{n=0}^{M−1} [ ‖z_{n+1} − z_ref‖²_Q + u_{k(n)}^T R u_{k(n)} ] · Δt
            + Σ_{k=0}^{N−1} [ ‖Δu_k‖²_S + c_u^T u_k ] · T_s
            + (z_M − z_ref)^T P (z_M − z_ref)
            + (soft state / output exact penalties)

    Hard constraints:

        u_min ≤ u_k ≤ u_max
        Δu_min ≤ u_k − u_{k−1} ≤ Δu_max

    Parameters
    ----------
    model : ContinuousDiscreteModel or ContinuousDiscreteDAEModel
        Nonlinear continuous-discrete plant.  ``model.gm`` provides the
        output ``z`` used in tracking and the soft-z constraints.
    N : int
        Prediction horizon (number of control intervals).
    Q : (nz, nz) ndarray
        Stage output tracking cost  ‖z − z_ref‖²_Q.
    R : (nu, nu) ndarray
        Stage input cost  ‖u‖²_R, applied per sub-step (right-rectangular).
    P : (nz, nz) ndarray, optional
        Terminal output tracking cost  ‖z_M − z_ref‖²_P.  Encoded as the
        Mayer term of the underlying EOCP.
    S : (nu, nu) ndarray, optional
        Quadratic ROM penalty  ‖Δu_k‖²_S T_s  on Δu_k = u_k − u_{k−1}.
    c_u : (nu,) ndarray, optional
        Linear input penalty  c_u^T u_k T_s.
    z_ref : (nz,) ndarray, optional
        Constant tracking reference.  ``None`` → zeros.
    u_min, u_max : (nu,) ndarray, optional
        Hard input box.
    du_min, du_max : (nu,) ndarray, optional
        Hard input ROM box on Δu_k.
    x_min, x_max : (nx,) ndarray, optional
        Soft state box (slacked, exact-penalty form per spec).
    rho_x : float, optional
        Quadratic penalty weight on state-slack variables.  Default: 1e4.
    z_min, z_max : (nz,) ndarray, optional
        Soft output box.
    rho_z : float, optional
        Quadratic penalty weight on output-slack variables.  Default: 1e4.
    n_steps : int, optional
        Implicit-Euler sub-steps per control interval.  Default: 10.
    solver : str or NLPSolverBackend, optional
        NLP backend selector. Reserved strings are ``"ipopt"``/``"cyipopt"``
        and ``"scipy"``/``"scipy-minimize"``.  Known scipy method names (e.g.
        ``"SLSQP"``, ``"trust-constr"``) are also accepted directly.  Any
        other string raises ``ValueError``.  Default: ``"SLSQP"``.
    solver_options : dict or None, optional
        Forwarded to the NLP solver.
    solver_scaling : NLPScalingPolicy or dict or None, optional
        Backend-agnostic scaling controls:
        ``objective_scale``, ``variable_scale``, ``constraint_scale``.
    dt : float or None, optional
        Sampling interval ``T_s``.  ``None`` → ``model.dt`` (if any) else 1.0.
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
        from .enmpc import EconomicOptimalControlProblem

        Q_arr = np.asarray(Q, dtype=float)
        R_arr = np.asarray(R, dtype=float)
        z_ref_arr = (
            np.asarray(z_ref, dtype=float)
            if z_ref is not None
            else np.zeros(model.nz)
        )

        # Quadratic input cost ‖u‖²_R encoded as a Lagrange callable.
        def _lagrange(t, x, y, u, theta, _R=R_arr):
            return float(u @ _R @ u)

        def _lagrange_jac(t, x, y, u, theta, _R=R_arr):
            # ∂L/∂x = 0,  ∂L/∂y = 0,  ∂L/∂u = 2 R u
            return (np.zeros_like(x), np.zeros_like(y), 2.0 * _R @ u)

        # Terminal tracking ‖z_M − z_ref‖²_P encoded as a Mayer callable.
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
                # Pgrad = 2 P (gm − z_ref);  ∂J/∂x = dgmdx^T Pgrad
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

        self._eocp = EconomicOptimalControlProblem(
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
        """Prediction horizon (number of control intervals)."""
        return self._eocp.N

    @property
    def nu(self) -> int:
        """Input dimension."""
        return self._eocp.nu

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
        """
        Solve the tracking OCP from initial state ``x0``.

        Returns
        -------
        u_opt : (N, nu) ndarray  — optimal input sequence.
        cost  : float            — optimal NLP objective value.
        info  : dict             — ``{"X", "Y", "result"}`` for warm-starting.
        """
        return self._eocp.solve(
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
        """Solve and return only the first optimal control action."""
        return self._eocp.step(
            x0, d_trajectory,
            u_prev=u_prev, x_prev=x_prev, y_prev=y_prev,
            p=p, t0=t0,
        )
