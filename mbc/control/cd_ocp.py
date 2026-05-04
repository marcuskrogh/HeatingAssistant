"""
Optimal Control Problems for continuous-discrete systems.

``CDOptimalControlProblem``
    A thin, typed wrapper around ``OptimalControlProblem`` for
    ``LinearContinuousDiscreteModel``.  Solves the receding-horizon QP via the
    lifted (batch) formulation using ``cvxopt.solvers.qp``.

``CDTrackingOptimalControlProblem``
    Nonlinear tracking OCP for any ``ContinuousDiscreteModel``.  Solves the
    finite-horizon NLP with output tracking, input cost, rate-of-movement (ROM)
    penalty and hard constraint, a linear input penalty, and soft state/output
    constraints.  The NLP is solved by ``scipy.optimize.minimize`` (default:
    SLSQP).

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

The lifted (batch) QP is solved using ``cvxopt.solvers.qp``.

Nonlinear problem formulation (Ph.D. Ch. 9 — tracking variant)
---------------------------------------------------------------
For a general nonlinear continuous-discrete model with controlled output
z = g(x, u, d, p, t), the tracking OCP is:

    min_{u_0,...,u_{N-1}}  J = Σₖ₌₀ᴺ⁻¹ [
                                ‖z[k+1] − z_ref‖²_Q
                              + ‖u[k]‖²_R
                              + ‖Δu[k]‖²_S
                              + c_uᵀ u[k]
                              + ρ_x (‖max(0, x[k+1] − x_max)‖²
                                   + ‖max(0, x_min − x[k+1])‖²)
                              + ρ_z (‖max(0, z[k+1] − z_max)‖²
                                   + ‖max(0, z_min − z[k+1])‖²)
                            ]
                          + ‖z[N] − z_ref‖²_P

    s.t.  x[k+1] = f̄(x[k], u[k], d[k])   (Euler integration of mean drift)
          u_min  ≤ u[k] ≤ u_max            (hard input box)
          Δu_min ≤ u[k] − u[k−1] ≤ Δu_max (hard input ROM box)

The NLP is solved by ``scipy.optimize.minimize`` (SLSQP by default).

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

from collections.abc import Callable
from typing import Tuple, TYPE_CHECKING

import numpy as np
from cvxopt import matrix

from .ocp import OptimalControlProblem
from .._utils import _np_to_cvx

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
    Q : cvxopt.matrix (ny, ny)
        Stage output tracking cost  ‖y − r‖²_Q.
    R : cvxopt.matrix (nu, nu)
        Stage input cost  ‖u‖²_R.
    P : cvxopt.matrix (ny, ny), optional
        Terminal output tracking cost.  Default: Q.
    S : cvxopt.matrix (nu, nu), optional
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
        Q: matrix,
        R: matrix,
        P: matrix | None = None,
        S: matrix | None = None,
        rho: float = 1e4,
        y_offset: float = 2.0,
    ) -> None:
        # Store the original CD model for direct access (e.g. x_ref_cvx)
        self._cd_model = model

        # Pass an adapter to the parent so that OptimalControlProblem.solve
        # receives the cvxopt-compatible interface it expects.
        super().__init__(
            model=_CDModelAdapter(model),  # type: ignore[arg-type]
            N=N,
            Q=Q,
            R=R,
            P=P,
            S=S,
            rho=rho,
            y_offset=y_offset,
        )


# ── Nonlinear Tracking OCP ────────────────────────────────────────────────────


class CDTrackingOptimalControlProblem:
    """
    Nonlinear tracking OCP for continuous-discrete systems (NLP formulation).

    Solves the finite-horizon NLP from a given initial state.  The predicted
    trajectory is computed by explicit Euler integration of the mean dynamics
    (no stochastic noise) over each sampling interval.

    Cost function over prediction horizon N:

        J = Σ_{k=0}^{N-1} [
                ‖z[k+1] − z_ref‖²_Q
              + ‖u[k]‖²_R
              + ‖Δu[k]‖²_S
              + c_uᵀ u[k]
              + ρ_x (‖max(0, x[k+1] − x_max)‖²
                   + ‖max(0, x_min − x[k+1])‖²)
              + ρ_z (‖max(0, z[k+1] − z_max)‖²
                   + ‖max(0, z_min − z[k+1])‖²)
            ]
          + ‖z[N] − z_ref‖²_P

    where  z[k] = g(x[k], u[k], d[k], p, t_k)  is the controlled output.

    Constraints:

        x[k+1] = f̄(x[k], u[k], d[k])   (mean dynamics, Euler integration)
        u_min  ≤ u[k] ≤ u_max            (hard input box)
        Δu_min ≤ u[k] − u[k−1] ≤ Δu_max (hard input ROM box)

    Soft state and output constraints are encoded as quadratic penalty terms
    with weights ``rho_x`` and ``rho_z`` respectively.

    Parameters
    ----------
    model : ContinuousDiscreteModel
        Nonlinear continuous-discrete model.  ``model.g(x, u, d, p, t)``
        provides the controlled output ``z``.
    N : int
        Prediction horizon (number of sampling intervals).
    Q : (nz, nz) ndarray
        Stage output tracking cost  ‖z − z_ref‖²_Q.
    R : (nu, nu) ndarray
        Stage input cost  ‖u‖²_R.
    P : (nz, nz) ndarray or None, optional
        Terminal output tracking cost  ‖z[N] − z_ref‖²_P.  Default: Q.
    S : (nu, nu) ndarray or None, optional
        Input rate-of-movement cost  ‖Δu‖²_S.  ``None`` disables the ROM
        penalty term (ROM hard constraints can still be active via
        ``du_min`` / ``du_max``).
    c_u : (nu,) ndarray or None, optional
        Linear input penalty vector  c_uᵀ u.  ``None`` disables the term.
    z_ref : (nz,) ndarray or None, optional
        Constant output reference / setpoint.  ``None`` uses zeros.
    u_min : (nu,) ndarray or None, optional
        Hard lower bound on inputs.  ``None`` = unconstrained.
    u_max : (nu,) ndarray or None, optional
        Hard upper bound on inputs.  ``None`` = unconstrained.
    du_min : (nu,) ndarray or None, optional
        Hard lower bound on input rate of movement  Δu[k] = u[k] − u[k−1].
        ``None`` = unconstrained.
    du_max : (nu,) ndarray or None, optional
        Hard upper bound on input ROM.  ``None`` = unconstrained.
    x_min : (nx,) ndarray or None, optional
        Soft lower bound on state (penalised by ``rho_x``).  ``None`` disables.
    x_max : (nx,) ndarray or None, optional
        Soft upper bound on state.  ``None`` disables.
    rho_x : float, optional
        Quadratic penalty weight on soft state constraint violation.
        Default: 1e4.
    z_min : (nz,) ndarray or None, optional
        Soft lower bound on controlled output (penalised by ``rho_z``).
        ``None`` disables.
    z_max : (nz,) ndarray or None, optional
        Soft upper bound on controlled output.  ``None`` disables.
    rho_z : float, optional
        Quadratic penalty weight on soft output constraint violation.
        Default: 1e4.
    n_steps : int, optional
        Explicit-Euler integration sub-steps per sampling interval.
        Default: 10.
    solver : str, optional
        NLP solver passed to ``scipy.optimize.minimize``.  Default: ``"SLSQP"``.
    solver_options : dict or None, optional
        Options forwarded to the solver.  ``None`` uses solver defaults.
    dt : float or None, optional
        Sampling interval.  If ``None``, taken from ``model.dt`` if available,
        else ``1.0``.
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
        solver: str = "SLSQP",
        solver_options: dict | None = None,
        dt: float | None = None,
    ) -> None:
        self._model = model
        self._N = N
        self._Q = np.asarray(Q, dtype=float)
        self._R = np.asarray(R, dtype=float)
        self._P = np.asarray(P, dtype=float) if P is not None else self._Q.copy()
        self._S = np.asarray(S, dtype=float) if S is not None else None
        self._c_u = np.asarray(c_u, dtype=float) if c_u is not None else None
        self._z_ref = (
            np.asarray(z_ref, dtype=float) if z_ref is not None
            else np.zeros(model.nz)
        )
        self._u_min = np.asarray(u_min, dtype=float) if u_min is not None else None
        self._u_max = np.asarray(u_max, dtype=float) if u_max is not None else None
        self._du_min = np.asarray(du_min, dtype=float) if du_min is not None else None
        self._du_max = np.asarray(du_max, dtype=float) if du_max is not None else None
        self._x_min = np.asarray(x_min, dtype=float) if x_min is not None else None
        self._x_max = np.asarray(x_max, dtype=float) if x_max is not None else None
        self._rho_x = float(rho_x)
        self._z_min = np.asarray(z_min, dtype=float) if z_min is not None else None
        self._z_max = np.asarray(z_max, dtype=float) if z_max is not None else None
        self._rho_z = float(rho_z)
        self._n_steps = n_steps
        self._solver = solver
        self._solver_options = solver_options
        self._dt: float = (
            float(dt) if dt is not None else float(getattr(model, "dt", 1.0))
        )

    @property
    def N(self) -> int:
        """Prediction horizon (number of sampling intervals)."""
        return self._N

    @property
    def nu(self) -> int:
        """Input dimension."""
        return self._model.nu

    def _predict_mean(
        self,
        x: np.ndarray,
        u: np.ndarray,
        d: np.ndarray,
        p: np.ndarray,
        t: float,
    ) -> np.ndarray:
        """Integrate mean dynamics over one sampling interval (no noise)."""
        h = self._dt / self._n_steps
        x_cur = x.copy()
        t_cur = t
        for _ in range(self._n_steps):
            x_cur = x_cur + self._model.f(x_cur, u, d, p, t_cur) * h
            t_cur += h
        return x_cur

    def solve(
        self,
        x0: np.ndarray,
        d_trajectory: np.ndarray,
        u_prev: np.ndarray | None = None,
        p: np.ndarray | None = None,
        t0: float = 0.0,
    ) -> tuple[np.ndarray, float]:
        """
        Solve the tracking OCP from initial state x0.

        Parameters
        ----------
        x0 : (nx,) ndarray
            Current state estimate (initial condition for the NLP).
        d_trajectory : (N, nd) ndarray
            Predicted disturbance trajectory over the horizon.
        u_prev : (N, nu) ndarray or None, optional
            Previous optimal input sequence used as a warm start.
            Shifted by one step (last element repeated).  ``None`` initialises
            from zeros.
        p : (nparams,) ndarray or None, optional
            Parameter vector.  ``None`` uses ``model.params``.
        t0 : float, optional
            Start time for the prediction horizon.  Default: 0.

        Returns
        -------
        u_opt : (N, nu) ndarray
            Optimal input sequence.
        cost : float
            Optimal tracking cost.
        """
        from scipy.optimize import minimize, Bounds

        N = self._N
        nu = self._model.nu
        p_ = self._model.params if p is None else p

        # ── Previous input for ROM (first step uses u_prev[-1] if supplied) ──
        u_prev_0 = u_prev[-1] if u_prev is not None else np.zeros(nu)

        # ── Warm start ────────────────────────────────────────────────────────
        if u_prev is not None:
            u0 = np.empty_like(u_prev)
            u0[:-1] = u_prev[1:]
            u0[-1] = u_prev[-1]
        else:
            u0 = np.zeros((N, nu))
        u0_flat = u0.ravel()

        # ── Objective function ────────────────────────────────────────────────
        def objective(u_flat: np.ndarray) -> float:
            U = u_flat.reshape(N, nu)
            x = x0.copy()
            t = t0
            total = 0.0
            for k in range(N):
                u_k = U[k]
                u_km1 = U[k - 1] if k > 0 else u_prev_0

                # Quadratic input cost
                total += 0.5 * float(u_k @ self._R @ u_k)

                # ROM penalty
                if self._S is not None:
                    du_k = u_k - u_km1
                    total += 0.5 * float(du_k @ self._S @ du_k)

                # Linear input penalty
                if self._c_u is not None:
                    total += float(self._c_u @ u_k)

                # Propagate state
                x = self._predict_mean(x, u_k, d_trajectory[k], p_, t)
                t += self._dt

                # Controlled output
                z_k = self._model.g(x, u_k, d_trajectory[k], p_, t)
                ez = z_k - self._z_ref

                # Stage tracking cost (use terminal matrix P on last step)
                W = self._P if k == N - 1 else self._Q
                total += 0.5 * float(ez @ W @ ez)

                # Soft state constraints
                if self._x_min is not None:
                    viol = np.maximum(0.0, self._x_min - x)
                    total += self._rho_x * float(viol @ viol)
                if self._x_max is not None:
                    viol = np.maximum(0.0, x - self._x_max)
                    total += self._rho_x * float(viol @ viol)

                # Soft output constraints
                if self._z_min is not None:
                    viol = np.maximum(0.0, self._z_min - z_k)
                    total += self._rho_z * float(viol @ viol)
                if self._z_max is not None:
                    viol = np.maximum(0.0, z_k - self._z_max)
                    total += self._rho_z * float(viol @ viol)

            return total

        # ── Build scipy Bounds for hard input box ─────────────────────────────
        if self._u_min is not None or self._u_max is not None:
            lb = (
                np.tile(self._u_min, N)
                if self._u_min is not None
                else np.full(N * nu, -np.inf)
            )
            ub = (
                np.tile(self._u_max, N)
                if self._u_max is not None
                else np.full(N * nu, np.inf)
            )
            bounds = Bounds(lb, ub)
        else:
            bounds = None

        # ── Build scipy constraints for hard ROM bounds ───────────────────────
        scipy_constraints: list = []
        if self._du_min is not None or self._du_max is not None:
            du_lo = self._du_min
            du_hi = self._du_max

            def _make_rom_con(k_: int, lo: bool) -> Callable:
                def _con(u_flat: np.ndarray) -> np.ndarray:
                    u_k = u_flat[k_ * nu:(k_ + 1) * nu]
                    u_km1 = u_flat[(k_ - 1) * nu:k_ * nu] if k_ > 0 else u_prev_0
                    du = u_k - u_km1
                    return du - du_lo if lo else du_hi - du
                return _con

            for k in range(N):
                if du_lo is not None:
                    scipy_constraints.append(
                        {"type": "ineq", "fun": _make_rom_con(k, True)}
                    )
                if du_hi is not None:
                    scipy_constraints.append(
                        {"type": "ineq", "fun": _make_rom_con(k, False)}
                    )

        # ── Solve NLP ─────────────────────────────────────────────────────────
        result = minimize(
            objective,
            u0_flat,
            method=self._solver,
            bounds=bounds,
            constraints=scipy_constraints if scipy_constraints else (),
            options=self._solver_options,
        )

        u_opt = result.x.reshape(N, nu)
        cost = float(result.fun)
        return u_opt, cost

    def step(
        self,
        x0: np.ndarray,
        d_trajectory: np.ndarray,
        u_prev: np.ndarray | None = None,
        p: np.ndarray | None = None,
        t0: float = 0.0,
    ) -> np.ndarray:
        """
        Solve and return only the first optimal control action.

        Parameters
        ----------
        x0 : (nx,) ndarray
            Current state estimate.
        d_trajectory : (N, nd) ndarray
            Predicted disturbance trajectory.
        u_prev : (N, nu) ndarray or None, optional
            Previous optimal sequence for warm-starting.
        p : (nparams,) ndarray or None, optional
            Parameter vector.  ``None`` uses ``model.params``.
        t0 : float, optional
            Start time.  Default: 0.

        Returns
        -------
        u0 : (nu,) ndarray
            First element of the optimal input sequence.
        """
        u_opt, _ = self.solve(x0, d_trajectory, u_prev, p=p, t0=t0)
        return u_opt[0]
