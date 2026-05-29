"""
Economic Optimal Control Problem (EOCP) and CD-NMPC Controller
(ControlToolbox §Economic Model Predictive Control).

The EOCP solves a finite-horizon nonlinear NLP that may include any convex
combination of the following objective terms (ControlToolbox §EMPC —
*Objectives*):

* Setpoint tracking          phi_z       — ‖z(t) − z̄(t)‖²_{Q_z}
* Input ROM penalty          phi_{Δu}    — ‖Δu_k‖²_{Q_du} T_s
* Input economy              phi_{u,eco} — p_{u,eco}^T u_k T_s
* General Lagrange / Mayer    phi_{P,eco} (and others) — user callable
* Soft-constraint exact penalty  phi_pq

Hard constraints (ControlToolbox §EMPC — *Constraints*):

* Input box                  u_min  ≤ u_k       ≤ u_max
* Input rate-of-movement     du_min ≤ u_k − u_{k−1} ≤ du_max

Soft (slacked) constraints with combined L1 + L2 exact penalty:

    x_min − p ≤ x_n ≤ x_max + q,    p, q ≥ 0
    z_min − s ≤ z_n ≤ z_max + s,    s ≥ 0

penalised by  Σ_n (rho_·_2 ‖p_n‖² + rho_·_1^T p_n + rho_·_2 ‖q_n‖² + rho_·_1^T q_n) Δt
for state slacks and by Σ_n (rho_z_2 ‖s_n‖² + rho_z_1^T s_n) Δt for output slacks.

The L1 (linear) component is an *exact* penalty: with sufficiently large
``rho_·_1``, the soft-constrained optimum coincides with the hard-constrained
solution whenever the latter exists.

Discretisation (ControlToolbox §EMPC — *Direct Simultaneous Approach*)
-----------------------------------------------------------------------
Continuous-time OCP

    min ∫ l(t, x(t), y(t), u(t), θ) dt + l_hat(x(t_f), y(t_f), θ)

is converted to a finite-dimensional NLP by

* **Implicit Euler** for the differential dynamics
* **Right-rectangular rule** for the Lagrange integral

Each control interval ``[t_k, t_{k+1}]`` is split into ``n_steps`` equidistant
sub-steps of size ``Δt = T_s / n_steps``.  Decision variables are

    {{x_{k,n}, y_{k,n}}_{n=0..n_steps},  u_k}_{k=0..N-1}

with continuity ``x_{k+1, 0} = x_{k, n_steps}`` (and likewise for ``y``).
The sub-step dynamics residual is

    D(z_{n+1}, z_n, u_k, d_k, θ) = [
        x_{n+1} − x_n − f(x_{n+1}, y_{n+1}, u_k, d_k, θ) Δt;
        g(x_{n+1}, y_{n+1}, θ)
    ] = 0

— the same form used by :class:`mbc.simulation.SDAESimulator`, allowing
Jacobian reuse between simulation and optimisation.  For SDE plant models
(``ContinuousDiscreteModel``) the algebraic block ``g`` is absent.

Continuous outputs ``z_{k,n} = g^m(x_{k,n}, y_{k,n}, θ)`` are evaluated as a
post-processing step from the optimal trajectory and used for the tracking
term and the soft-z constraints.

ENMPC algorithm at time t_k (ControlToolbox §EMPC — *Algorithm*)
------------------------------------------------------------------
    1. Measure   y^{m,s}_k = h^m(z^s_k, θ^s) + v^s_k(θ^s)
    2. Estimate  z^c_k = κ(z^c_{k-1}, u_{k-1}, d_{k-1}, y^{m,s}_k, θ^c)
    3. Optimise  u_k = λ(z^c_k, θ^c)
    4. Apply     z^s_{k+1} = F(z^s_k, u_k, d_k, ω^s_k, θ^s)
    5. Repeat at t_{k+1}.

The OCP uses **deterministic** dynamics — diffusion ``sigma`` does not appear
in the prediction model.  Uncertainty enters only through the state estimate
at each sampling time.

This module exposes:

* :class:`EconomicOptimalControlProblem` — the EOCP/NLP solver.
* :class:`CDNMPCController` — closed-loop receding-horizon controller
  composing any continuous-discrete state estimator with this OCP.
"""

from __future__ import annotations

import numpy as np

from ..models import ContinuousDiscreteDAEModel, ContinuousDiscreteModel
from .nlp_solver import (
    NLPConstraint,
    NLPProblem,
    NLPScalingPolicy,
    NLPSolverBackend,
    make_nlp_backend,
)


# ── Internal helpers ─────────────────────────────────────────────────────────


class _DecisionLayout:
    """
    Layout of the flat NLP decision vector z.

    Order:
        [u_0, u_1, ..., u_{N-1},                            (control inputs)
         x_0, x_1, ..., x_M,                                (differential state at sub-steps)
         y_0, y_1, ..., y_M,                                (algebraic state, only if SDAE)
         px_lo_0, ..., px_lo_M, px_hi_0, ..., px_hi_M,      (state slacks, only if soft x)
         pz_0, ..., pz_M]                                    (output slacks, only if soft z)

    where M = N * n_steps is the total number of sub-step intervals; states
    are stored at M + 1 grid points.
    """

    def __init__(
        self,
        N: int,
        n_steps: int,
        nx: int,
        nu: int,
        ny: int,
        nz: int,
        soft_x: bool,
        soft_z: bool,
    ) -> None:
        self.N = N
        self.n_steps = n_steps
        self.M = N * n_steps
        self.nx = nx
        self.nu = nu
        self.ny = ny
        self.nz = nz
        self.soft_x = soft_x
        self.soft_z = soft_z

        # Layout offsets
        offset = 0
        self.u_off = offset
        self.u_size = N * nu
        offset += self.u_size

        self.x_off = offset
        self.x_size = (self.M + 1) * nx
        offset += self.x_size

        self.y_off = offset
        self.y_size = (self.M + 1) * ny
        offset += self.y_size

        # Soft-x slacks: lower + upper, each (M+1, nx)
        self.px_lo_off = offset
        self.px_lo_size = (self.M + 1) * nx if soft_x else 0
        offset += self.px_lo_size
        self.px_hi_off = offset
        self.px_hi_size = (self.M + 1) * nx if soft_x else 0
        offset += self.px_hi_size

        # Soft-z slacks: shared for lower + upper, (M+1, nz)
        self.pz_off = offset
        self.pz_size = (self.M + 1) * nz if soft_z else 0
        offset += self.pz_size

        self.total = offset

    def get_U(self, z: np.ndarray) -> np.ndarray:
        """Return U with shape (N, nu) — ZOH inputs per control interval."""
        return z[self.u_off:self.u_off + self.u_size].reshape(self.N, self.nu)

    def get_X(self, z: np.ndarray) -> np.ndarray:
        """Return X with shape (M+1, nx) — differential state at every sub-step."""
        return z[self.x_off:self.x_off + self.x_size].reshape(self.M + 1, self.nx)

    def get_Y(self, z: np.ndarray) -> np.ndarray:
        """Return Y with shape (M+1, ny) — algebraic state (empty (M+1, 0) for SDE)."""
        return z[self.y_off:self.y_off + self.y_size].reshape(self.M + 1, self.ny)

    def get_PX_lo(self, z: np.ndarray) -> np.ndarray:
        return z[self.px_lo_off:self.px_lo_off + self.px_lo_size].reshape(self.M + 1, self.nx)

    def get_PX_hi(self, z: np.ndarray) -> np.ndarray:
        return z[self.px_hi_off:self.px_hi_off + self.px_hi_size].reshape(self.M + 1, self.nx)

    def get_PZ(self, z: np.ndarray) -> np.ndarray:
        return z[self.pz_off:self.pz_off + self.pz_size].reshape(self.M + 1, self.nz)


# ── Economic Optimal Control Problem ─────────────────────────────────────────


class EconomicOptimalControlProblem:
    """
    Economic Optimal Control Problem for continuous-discrete nonlinear
    SDE / SDAE systems (ControlToolbox §EMPC).

    Direct simultaneous formulation: implicit-Euler dynamics, right-
    rectangular Lagrange.  Decision variables are the inputs
    ``{u_k}_{k=0..N-1}`` together with the differential and algebraic states
    ``{x_n, y_n}_{n=0..M}`` at every sub-step (M = N · n_steps).

    Supports a convex combination of objective terms:

    * Setpoint tracking      ``Q_z``, ``z_ref``
    * Input ROM penalty      ``Q_du``
    * Input economy          ``p_u_eco``
    * General Lagrange       ``lagrange(t, x, y, u, theta)``
    * General Mayer          ``mayer(x, y, theta)``

    Hard constraints

    * Input box              ``u_min`` / ``u_max``
    * Input ROM box          ``du_min`` / ``du_max``

    Soft (slacked, exact-penalty) constraints

    * State                  ``x_min`` / ``x_max``  (+ ``rho_x_1``, ``rho_x_2``)
    * Output                 ``z_min`` / ``z_max``  (+ ``rho_z_1``, ``rho_z_2``)

    Plant model
    -----------
    Either a :class:`~mbc.models.ContinuousDiscreteModel` (SDE — no algebraic
    state) or a :class:`~mbc.models.ContinuousDiscreteDAEModel` (SDAE — with
    algebraic constraint ``g(x, y, …) = 0``).  The OCP detects the model type
    and dispatches signatures of ``f``, ``g``, ``g^m``, etc. accordingly.

    Parameters
    ----------
    model : ContinuousDiscreteModel or ContinuousDiscreteDAEModel
        Plant model providing ``f``, ``g`` (SDAE only), ``gm``, ``hm``, and
        their Jacobians (only used by the NLP solver if it needs gradients).
    N : int
        Prediction horizon (number of control intervals).
    lagrange : callable (t, x, y, u, theta) → float, optional
        General Lagrange (stage) cost.  ``y`` is an empty array when the
        plant is an SDE.
    lagrange_jac : callable (t, x, y, u, theta) → (grad_x, grad_y, grad_u), optional
        Gradient of ``lagrange`` w.r.t. ``(x, y, u)``.  Must be provided
        alongside ``lagrange`` to enable a fully analytical objective
        gradient.  Each returned array has the same shape as the
        corresponding argument (``grad_y`` may be ignored for SDE models).
    mayer : callable (x, y, theta) → float, optional
        General Mayer (terminal) cost.
    mayer_jac : callable (x, y, theta) → (grad_x, grad_y), optional
        Gradient of ``mayer`` w.r.t. ``(x, y)``.  Must be provided
        alongside ``mayer`` to enable a fully analytical objective gradient.
        ``grad_y`` may be ignored for SDE models.
    Q_z : (nz, nz) ndarray, optional
        Tracking weight; activates ``phi_z = Σ_n ‖z_n − z̄_n‖²_{Q_z} Δt``.
    z_ref : (nz,) or (M+1, nz) ndarray, optional
        Constant or time-varying tracking reference.  Required when ``Q_z``
        is set.
    Q_du : (nu, nu) ndarray, optional
        Quadratic ROM penalty on Δu_k = u_k − u_{k−1} (with u_{-1} = u_prev).
    p_u_eco : (nu,) ndarray, optional
        Linear input cost ``p_u_eco^T u_k T_s``.
    u_min, u_max : (nu,) ndarray, optional
        Hard input box.  ``None`` = unconstrained.
    du_min, du_max : (nu,) ndarray, optional
        Hard input rate-of-movement box on Δu_k = u_k − u_{k−1}.
    x_min, x_max : (nx,) ndarray, optional
        Soft state box (slacked).  Penalty weights ``rho_x_1`` (L1, exact)
        and ``rho_x_2`` (L2).
    z_min, z_max : (nz,) ndarray, optional
        Soft output box (slacked).  Penalty weights ``rho_z_1``, ``rho_z_2``.
    rho_x_1 : float, optional
        L1 penalty weight on state slacks (default 0).
    rho_x_2 : float, optional
        L2 penalty weight on state slacks (default 1e4).
    rho_z_1 : float, optional
        L1 penalty weight on output slacks (default 0).
    rho_z_2 : float, optional
        L2 penalty weight on output slacks (default 1e4).
    n_steps : int, optional
        Implicit-Euler sub-steps per control interval.  Default: 10.
    solver : str or NLPSolverBackend, optional
        NLP backend selector. Reserved strings are ``"ipopt"``/``"cyipopt"``
        and ``"scipy"``/``"scipy-minimize"``.  Known
        ``scipy.optimize.minimize`` method names (e.g. ``"SLSQP"``,
        ``"trust-constr"``) are also accepted directly.  Any other string
        raises ``ValueError``.  Default: ``"SLSQP"``.
    solver_options : dict or None, optional
        Forwarded to the NLP solver.
    solver_scaling : NLPScalingPolicy or dict or None, optional
        Backend-agnostic scaling controls:
        ``objective_scale``, ``variable_scale``, ``constraint_scale``.
    dt : float or None, optional
        Sampling interval ``T_s``.  ``None`` → ``model.dt`` if available,
        else ``1.0``.
    """

    def __init__(
        self,
        model: ContinuousDiscreteModel,
        N: int,
        *,
        lagrange: Callable[..., float] | None = None,
        lagrange_jac: Callable[..., tuple] | None = None,
        mayer: Callable[..., float] | None = None,
        mayer_jac: Callable[..., tuple] | None = None,
        Q_z: np.ndarray | None = None,
        z_ref: np.ndarray | None = None,
        Q_du: np.ndarray | None = None,
        p_u_eco: np.ndarray | None = None,
        u_min: np.ndarray | None = None,
        u_max: np.ndarray | None = None,
        du_min: np.ndarray | None = None,
        du_max: np.ndarray | None = None,
        x_min: np.ndarray | None = None,
        x_max: np.ndarray | None = None,
        rho_x_1: float = 0.0,
        rho_x_2: float = 1e4,
        z_min: np.ndarray | None = None,
        z_max: np.ndarray | None = None,
        rho_z_1: float = 0.0,
        rho_z_2: float = 1e4,
        n_steps: int = 10,
        solver: str | NLPSolverBackend = "SLSQP",
        solver_options: dict | None = None,
        solver_scaling: NLPScalingPolicy | dict | None = None,
        dt: float | None = None,
    ) -> None:
        self._model = model
        self._N = int(N)
        self._n_steps = int(n_steps)
        self._is_dae = isinstance(model, ContinuousDiscreteDAEModel)
        self._nx = int(model.nx)
        self._nu = int(model.nu)
        self._nd = int(model.nd)
        self._nz = int(model.nz)
        self._ny = int(model.ny) if self._is_dae else 0

        # Sampling interval
        self._dt: float = (
            float(dt) if dt is not None else float(getattr(model, "dt", 1.0))
        )
        self._h = self._dt / self._n_steps  # sub-step Δt

        # Objective term storage
        self._lagrange = lagrange
        self._lagrange_jac = lagrange_jac
        self._mayer = mayer
        self._mayer_jac = mayer_jac
        self._Q_z = (
            np.asarray(Q_z, dtype=float) if Q_z is not None else None
        )
        if self._Q_z is not None and z_ref is None:
            raise ValueError("Q_z requires z_ref to be supplied as well.")
        self._z_ref = self._broadcast_zref(z_ref) if z_ref is not None else None
        self._Q_du = (
            np.asarray(Q_du, dtype=float) if Q_du is not None else None
        )
        self._p_u_eco = (
            np.asarray(p_u_eco, dtype=float) if p_u_eco is not None else None
        )

        # Hard constraints
        self._u_min = self._asfloat(u_min)
        self._u_max = self._asfloat(u_max)
        self._du_min = self._asfloat(du_min)
        self._du_max = self._asfloat(du_max)

        # Soft constraints
        self._x_min = self._asfloat(x_min)
        self._x_max = self._asfloat(x_max)
        self._z_min = self._asfloat(z_min)
        self._z_max = self._asfloat(z_max)
        self._rho_x_1 = float(rho_x_1)
        self._rho_x_2 = float(rho_x_2)
        self._rho_z_1 = float(rho_z_1)
        self._rho_z_2 = float(rho_z_2)
        self._has_soft_x = self._x_min is not None or self._x_max is not None
        self._has_soft_z = self._z_min is not None or self._z_max is not None

        # NLP setup
        self._solver_backend = make_nlp_backend(
            solver,
            solver_options=solver_options,
            scaling=solver_scaling,
        )

        # Decision-variable layout
        self._layout = _DecisionLayout(
            N=self._N,
            n_steps=self._n_steps,
            nx=self._nx,
            nu=self._nu,
            ny=self._ny,
            nz=self._nz,
            soft_x=self._has_soft_x,
            soft_z=self._has_soft_z,
        )

    # ── Public properties ────────────────────────────────────────────────────

    @property
    def N(self) -> int:
        """Prediction horizon (number of control intervals)."""
        return self._N

    @property
    def nu(self) -> int:
        """Input dimension."""
        return self._nu

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _asfloat(arr) -> np.ndarray | None:
        return np.asarray(arr, dtype=float) if arr is not None else None

    def _broadcast_zref(self, z_ref) -> np.ndarray:
        """Broadcast z_ref to shape (M+1, nz)."""
        z_ref = np.asarray(z_ref, dtype=float)
        M = self._N * self._n_steps
        if z_ref.ndim == 1:
            return np.tile(z_ref, (M + 1, 1))
        if z_ref.shape == (M + 1, self._nz):
            return z_ref
        raise ValueError(
            f"z_ref must have shape ({self._nz},) or "
            f"({M + 1}, {self._nz}); got {z_ref.shape}."
        )

    # Plant dispatch — supports both SDE and SDAE signatures.

    def _f(self, x, y, u, d, p, t):
        return (
            self._model.f(x, y, u, d, p, t)
            if self._is_dae
            else self._model.f(x, u, d, p, t)
        )

    def _g(self, x, y, u, d, p, t):
        if self._is_dae:
            return self._model.g(x, y, u, d, p, t)
        return np.empty(0)

    def _gm(self, x, y, u, d, p, t):
        return (
            self._model.gm(x, y, u, d, p, t)
            if self._is_dae
            else self._model.gm(x, u, d, p, t)
        )

    # ── NLP construction ─────────────────────────────────────────────────────

    def _build_initial_guess(
        self,
        x0: np.ndarray,
        u_prev: np.ndarray | None,
        x_prev: np.ndarray | None,
        y_prev: np.ndarray | None,
    ) -> np.ndarray:
        """
        Construct the initial guess for the NLP decision variable.

        The previous solution (if supplied) is shifted by one control
        interval (last value repeated) — standard receding-horizon warm
        start.  Otherwise we use a zero-input guess and a constant-state
        fill from x0.
        """
        L = self._layout
        z0 = np.zeros(L.total)

        # ── Inputs ──
        if u_prev is not None and u_prev.shape == (self._N, self._nu):
            U_init = np.empty_like(u_prev)
            U_init[:-1] = u_prev[1:]
            U_init[-1] = u_prev[-1]
        else:
            U_init = np.zeros((self._N, self._nu))
        z0[L.u_off:L.u_off + L.u_size] = U_init.ravel()

        # ── States ──
        if x_prev is not None and x_prev.shape == (L.M + 1, self._nx):
            # Shift by n_steps sub-steps; tail repeats final state.
            X_init = np.empty_like(x_prev)
            X_init[:L.M + 1 - self._n_steps] = x_prev[self._n_steps:]
            X_init[L.M + 1 - self._n_steps:] = x_prev[-1]
        else:
            X_init = np.tile(x0, (L.M + 1, 1))
        z0[L.x_off:L.x_off + L.x_size] = X_init.ravel()

        # ── Algebraic state (SDAE only) ──
        if self._is_dae:
            if y_prev is not None and y_prev.shape == (L.M + 1, self._ny):
                Y_init = np.empty_like(y_prev)
                Y_init[:L.M + 1 - self._n_steps] = y_prev[self._n_steps:]
                Y_init[L.M + 1 - self._n_steps:] = y_prev[-1]
            else:
                Y_init = np.zeros((L.M + 1, self._ny))
            z0[L.y_off:L.y_off + L.y_size] = Y_init.ravel()

        # Slacks default to 0 — already zeros.
        return z0

    def _objective(
        self,
        z: np.ndarray,
        x_hat: np.ndarray,
        d_traj: np.ndarray,
        u_prev_0: np.ndarray,
        p_theta: np.ndarray,
        t0: float,
    ) -> float:
        """Compute the EOCP objective value."""
        L = self._layout
        U = L.get_U(z)
        X = L.get_X(z)
        Y = L.get_Y(z)
        h = self._h
        Ts = self._dt

        total = 0.0

        # ── Lagrange (right-rectangular over all sub-steps) ──
        for n in range(L.M):
            k = n // self._n_steps
            u_k = U[k]
            d_k = d_traj[k]
            x_np1 = X[n + 1]
            y_np1 = Y[n + 1] if self._is_dae else np.empty(0)
            t_np1 = t0 + (n + 1) * h

            # User-supplied Lagrange
            if self._lagrange is not None:
                total += float(self._lagrange(t_np1, x_np1, y_np1, u_k, p_theta)) * h

            # Tracking
            if self._Q_z is not None:
                z_np1 = self._gm(x_np1, y_np1, u_k, d_k, p_theta, t_np1)
                e = z_np1 - self._z_ref[n + 1]
                total += float(e @ self._Q_z @ e) * h

        # ── Per-control-interval terms (ROM, input economy) ──
        for k in range(self._N):
            u_k = U[k]
            u_km1 = U[k - 1] if k > 0 else u_prev_0

            if self._Q_du is not None:
                du = u_k - u_km1
                total += float(du @ self._Q_du @ du) * Ts

            if self._p_u_eco is not None:
                total += float(self._p_u_eco @ u_k) * Ts

        # ── Mayer (terminal cost) ──
        if self._mayer is not None:
            x_M = X[L.M]
            y_M = Y[L.M] if self._is_dae else np.empty(0)
            total += float(self._mayer(x_M, y_M, p_theta))

        # ── Soft-constraint exact penalty (Σ over all sub-steps) ──
        if self._has_soft_x:
            PX_lo = L.get_PX_lo(z)
            PX_hi = L.get_PX_hi(z)
            for n in range(L.M + 1):
                if self._x_min is not None:
                    p_n = PX_lo[n]
                    total += (self._rho_x_1 * p_n.sum() + self._rho_x_2 * (p_n @ p_n)) * h
                if self._x_max is not None:
                    q_n = PX_hi[n]
                    total += (self._rho_x_1 * q_n.sum() + self._rho_x_2 * (q_n @ q_n)) * h

        if self._has_soft_z:
            PZ = L.get_PZ(z)
            for n in range(L.M + 1):
                s_n = PZ[n]
                total += (self._rho_z_1 * s_n.sum() + self._rho_z_2 * (s_n @ s_n)) * h

        return total

    def _equality_constraints(
        self,
        z: np.ndarray,
        x_hat: np.ndarray,
        d_traj: np.ndarray,
        p_theta: np.ndarray,
        t0: float,
    ) -> np.ndarray:
        """
        Equality constraints (must equal zero):

        * x_0 − x_hat = 0
        * For each n = 0..M-1:  x_{n+1} − x_n − f(x_{n+1}, y_{n+1}, u_k, d_k) Δt = 0
        * For each n = 0..M-1:  g(x_{n+1}, y_{n+1}, p) = 0    (SDAE only)
        * g(x_0, y_0, p) = 0                                  (SDAE only — algebraic consistency at IC)
        """
        L = self._layout
        U = L.get_U(z)
        X = L.get_X(z)
        Y = L.get_Y(z)
        h = self._h

        residuals: list[np.ndarray] = [X[0] - x_hat]

        # Sub-step dynamics residual (implicit Euler)
        for n in range(L.M):
            k = n // self._n_steps
            u_k = U[k]
            d_k = d_traj[k]
            x_n = X[n]
            x_np1 = X[n + 1]
            y_np1 = Y[n + 1] if self._is_dae else np.empty(0)
            t_np1 = t0 + (n + 1) * h
            f_val = self._f(x_np1, y_np1, u_k, d_k, p_theta, t_np1)
            residuals.append(x_np1 - x_n - f_val * h)

            if self._is_dae:
                residuals.append(self._g(x_np1, y_np1, u_k, d_k, p_theta, t_np1))

        # Algebraic consistency at the initial sub-step (SDAE only)
        if self._is_dae:
            residuals.append(self._g(X[0], Y[0], U[0], d_traj[0], p_theta, t0))

        return np.concatenate(residuals)

    def _inequality_constraints(
        self,
        z: np.ndarray,
        u_prev_0: np.ndarray,
        d_traj: np.ndarray,
        p_theta: np.ndarray,
        t0: float,
    ) -> np.ndarray:
        """
        Inequality constraints in scipy form  (returned values must be ≥ 0).

        * Hard input ROM box       du_min ≤ u_k − u_{k−1} ≤ du_max
        * Soft state lower         x_n + p_lo,n − x_min ≥ 0
        * Soft state upper         x_max + q_hi,n − x_n ≥ 0
        * Soft output lower        z_n + s_n − z_min ≥ 0
        * Soft output upper        z_max + s_n − z_n ≥ 0

        Plain input box ``u_min ≤ u_k ≤ u_max`` is enforced via scipy
        ``Bounds`` and is *not* repeated here.  Slack non-negativity is
        likewise expressed as bounds.
        """
        L = self._layout
        U = L.get_U(z)
        X = L.get_X(z)
        Y = L.get_Y(z)
        h = self._h

        out: list[float] = []

        # ── ROM box ──
        if self._du_min is not None or self._du_max is not None:
            for k in range(self._N):
                u_k = U[k]
                u_km1 = U[k - 1] if k > 0 else u_prev_0
                du = u_k - u_km1
                if self._du_min is not None:
                    out.extend(du - self._du_min)        # du − du_min ≥ 0
                if self._du_max is not None:
                    out.extend(self._du_max - du)        # du_max − du ≥ 0

        # ── Soft state slacks ──
        if self._has_soft_x:
            PX_lo = L.get_PX_lo(z)
            PX_hi = L.get_PX_hi(z)
            for n in range(L.M + 1):
                if self._x_min is not None:
                    out.extend(X[n] + PX_lo[n] - self._x_min)
                if self._x_max is not None:
                    out.extend(self._x_max + PX_hi[n] - X[n])

        # ── Soft output slacks (require evaluating gm at every sub-step) ──
        if self._has_soft_z:
            PZ = L.get_PZ(z)
            for n in range(L.M + 1):
                k = min(n // self._n_steps, self._N - 1)
                u_k = U[k]
                d_k = d_traj[k]
                t_n = t0 + n * h
                y_n = Y[n] if self._is_dae else np.empty(0)
                z_n = self._gm(X[n], y_n, u_k, d_k, p_theta, t_n)
                if self._z_min is not None:
                    out.extend(z_n + PZ[n] - self._z_min)
                if self._z_max is not None:
                    out.extend(self._z_max + PZ[n] - z_n)

        return np.asarray(out, dtype=float)

    # ── Analytical Jacobians ─────────────────────────────────────────────────

    def _equality_constraint_jac(
        self,
        z: np.ndarray,
        x_hat: np.ndarray,
        d_traj: np.ndarray,
        p_theta: np.ndarray,
        t0: float,
    ) -> np.ndarray:
        """
        Dense Jacobian of the equality constraints w.r.t. the NLP decision
        vector ``z``.

        Rows correspond to the same ordering used by
        :meth:`_equality_constraints`.  Computed analytically from
        ``model.dfdx``, ``model.dfdu``, ``model.dfdy`` (DAE only),
        ``model.dgdx``, ``model.dgdy`` (DAE only).  The model defaults
        delegate these to forward finite differences, but subclasses may
        override them with analytic Jacobians for improved efficiency.
        """
        L = self._layout
        U = L.get_U(z)
        X = L.get_X(z)
        Y = L.get_Y(z)
        h = self._h
        nx, nu, ny = self._nx, self._nu, self._ny
        M = L.M

        if self._is_dae:
            # x0 block + M dynamics blocks + M algebraic blocks + IC algebraic
            n_eq = nx + M * nx + M * ny + ny
        else:
            n_eq = nx + M * nx

        J = np.zeros((n_eq, L.total))

        # ── Block 0: x_0 − x_hat = 0 ──
        J[0:nx, L.x_off:L.x_off + nx] = np.eye(nx)
        row = nx

        for n in range(M):
            k = n // self._n_steps
            u_k = U[k]
            d_k = d_traj[k]
            x_np1 = X[n + 1]
            y_np1 = Y[n + 1] if self._is_dae else np.empty(0)
            t_np1 = t0 + (n + 1) * h

            x_np1_col = L.x_off + (n + 1) * nx
            x_n_col = L.x_off + n * nx
            u_k_col = L.u_off + k * nu

            if self._is_dae:
                dfdx_val = self._model.dfdx(x_np1, y_np1, u_k, d_k, p_theta, t_np1)
                dfdu_val = self._model.dfdu(x_np1, y_np1, u_k, d_k, p_theta, t_np1)
                dfdy_val = self._model.dfdy(x_np1, y_np1, u_k, d_k, p_theta, t_np1)
            else:
                dfdx_val = self._model.dfdx(x_np1, u_k, d_k, p_theta, t_np1)
                dfdu_val = self._model.dfdu(x_np1, u_k, d_k, p_theta, t_np1)

            # Dynamics: x_{n+1} − x_n − f(x_{n+1}, …)*h = 0
            J[row:row + nx, x_np1_col:x_np1_col + nx] = np.eye(nx) - dfdx_val * h
            J[row:row + nx, x_n_col:x_n_col + nx] = -np.eye(nx)
            J[row:row + nx, u_k_col:u_k_col + nu] = -dfdu_val * h
            if self._is_dae:
                y_np1_col = L.y_off + (n + 1) * ny
                J[row:row + nx, y_np1_col:y_np1_col + ny] = -dfdy_val * h
            row += nx

            if self._is_dae:
                # Algebraic: g(x_{n+1}, y_{n+1}, …) = 0
                dgdx_val = self._model.dgdx(x_np1, y_np1, u_k, d_k, p_theta, t_np1)
                dgdy_val = self._model.dgdy(x_np1, y_np1, u_k, d_k, p_theta, t_np1)
                y_np1_col = L.y_off + (n + 1) * ny
                J[row:row + ny, x_np1_col:x_np1_col + nx] = dgdx_val
                J[row:row + ny, y_np1_col:y_np1_col + ny] = dgdy_val
                row += ny

        if self._is_dae:
            # Algebraic consistency at IC: g(x_0, y_0, u_0, d_0, p, t0) = 0
            x_0, y_0, u_0, d_0 = X[0], Y[0], U[0], d_traj[0]
            dgdx_0 = self._model.dgdx(x_0, y_0, u_0, d_0, p_theta, t0)
            dgdy_0 = self._model.dgdy(x_0, y_0, u_0, d_0, p_theta, t0)
            J[row:row + ny, L.x_off:L.x_off + nx] = dgdx_0
            J[row:row + ny, L.y_off:L.y_off + ny] = dgdy_0

        return J

    def _inequality_constraint_jac(
        self,
        z: np.ndarray,
        u_prev_0: np.ndarray,
        d_traj: np.ndarray,
        p_theta: np.ndarray,
        t0: float,
    ) -> np.ndarray:
        """
        Dense Jacobian of the inequality constraints w.r.t. the NLP decision
        vector ``z``.

        Rows correspond to the same ordering used by
        :meth:`_inequality_constraints`.  Slack/identity blocks are exact;
        the output-constraint rows use ``model.dgmdx``, ``model.dgmdu``, and
        (for SDAE) ``model.dgmdy``.
        """
        L = self._layout
        U = L.get_U(z)
        X = L.get_X(z)
        Y = L.get_Y(z)
        h = self._h
        nx, nu, ny, nz = self._nx, self._nu, self._ny, self._nz

        rows: list[np.ndarray] = []

        # ── ROM box ──
        if self._du_min is not None or self._du_max is not None:
            for k in range(self._N):
                u_k_col = L.u_off + k * nu
                u_km1_col = L.u_off + (k - 1) * nu if k > 0 else None

                if self._du_min is not None:
                    # du_k − du_min ≥ 0  →  d/dU[k] = +I,  d/dU[k-1] = −I
                    jrow = np.zeros((nu, L.total))
                    jrow[:, u_k_col:u_k_col + nu] = np.eye(nu)
                    if u_km1_col is not None:
                        jrow[:, u_km1_col:u_km1_col + nu] = -np.eye(nu)
                    rows.append(jrow)

                if self._du_max is not None:
                    # du_max − du_k ≥ 0  →  d/dU[k] = −I,  d/dU[k-1] = +I
                    jrow = np.zeros((nu, L.total))
                    jrow[:, u_k_col:u_k_col + nu] = -np.eye(nu)
                    if u_km1_col is not None:
                        jrow[:, u_km1_col:u_km1_col + nu] = np.eye(nu)
                    rows.append(jrow)

        # ── Soft state slacks ──
        if self._has_soft_x:
            for n in range(L.M + 1):
                x_n_col = L.x_off + n * nx
                px_lo_n_col = L.px_lo_off + n * nx
                px_hi_n_col = L.px_hi_off + n * nx

                if self._x_min is not None:
                    # X[n] + PX_lo[n] − x_min ≥ 0
                    jrow = np.zeros((nx, L.total))
                    jrow[:, x_n_col:x_n_col + nx] = np.eye(nx)
                    jrow[:, px_lo_n_col:px_lo_n_col + nx] = np.eye(nx)
                    rows.append(jrow)

                if self._x_max is not None:
                    # x_max + PX_hi[n] − X[n] ≥ 0
                    jrow = np.zeros((nx, L.total))
                    jrow[:, x_n_col:x_n_col + nx] = -np.eye(nx)
                    jrow[:, px_hi_n_col:px_hi_n_col + nx] = np.eye(nx)
                    rows.append(jrow)

        # ── Soft output slacks ──
        if self._has_soft_z:
            for n in range(L.M + 1):
                k = min(n // self._n_steps, self._N - 1)
                u_k = U[k]
                d_k = d_traj[k]
                t_n = t0 + n * h
                x_n = X[n]
                y_n = Y[n] if self._is_dae else np.empty(0)

                x_n_col = L.x_off + n * nx
                u_k_col = L.u_off + k * nu
                pz_n_col = L.pz_off + n * nz

                if self._is_dae:
                    dgmdx_val = self._model.dgmdx(x_n, y_n, u_k, d_k, p_theta, t_n)
                    dgmdu_val = self._model.dgmdu(x_n, y_n, u_k, d_k, p_theta, t_n)
                    dgmdy_val = self._model.dgmdy(x_n, y_n, u_k, d_k, p_theta, t_n)
                    y_n_col = L.y_off + n * ny
                else:
                    dgmdx_val = self._model.dgmdx(x_n, u_k, d_k, p_theta, t_n)
                    dgmdu_val = self._model.dgmdu(x_n, u_k, d_k, p_theta, t_n)

                if self._z_min is not None:
                    # z_n + PZ[n] − z_min ≥ 0
                    jrow = np.zeros((nz, L.total))
                    jrow[:, x_n_col:x_n_col + nx] = dgmdx_val
                    jrow[:, u_k_col:u_k_col + nu] += dgmdu_val
                    jrow[:, pz_n_col:pz_n_col + nz] = np.eye(nz)
                    if self._is_dae:
                        jrow[:, y_n_col:y_n_col + ny] = dgmdy_val
                    rows.append(jrow)

                if self._z_max is not None:
                    # z_max + PZ[n] − z_n ≥ 0
                    jrow = np.zeros((nz, L.total))
                    jrow[:, x_n_col:x_n_col + nx] = -dgmdx_val
                    jrow[:, u_k_col:u_k_col + nu] -= dgmdu_val
                    jrow[:, pz_n_col:pz_n_col + nz] = np.eye(nz)
                    if self._is_dae:
                        jrow[:, y_n_col:y_n_col + ny] = -dgmdy_val
                    rows.append(jrow)

        if not rows:
            return np.zeros((0, L.total))
        return np.vstack(rows)

    def _objective_jac(
        self,
        z: np.ndarray,
        d_traj: np.ndarray,
        u_prev_0: np.ndarray,
        p_theta: np.ndarray,
        t0: float,
    ) -> np.ndarray:
        """
        Analytical gradient of the EOCP objective w.r.t. the NLP decision
        vector ``z``.

        Computes the gradient for all objective terms that admit analytical
        derivatives:

        * Tracking ``‖gm − z_ref‖²_{Q_z}``  via ``model.dgmdx`` / ``model.dgmdu``
        * Lagrange user cost via ``lagrange_jac`` (when provided)
        * Mayer user cost via ``mayer_jac`` (when provided)
        * Quadratic ROM penalty ``Q_du``
        * Linear input economy ``p_u_eco``
        * Soft-x / soft-z exact penalty terms

        This method should only be called when
        ``_can_use_analytical_objective_jac()`` returns ``True``.
        """
        L = self._layout
        U = L.get_U(z)
        X = L.get_X(z)
        Y = L.get_Y(z)
        h = self._h
        Ts = self._dt
        nx, nu, ny, nz = self._nx, self._nu, self._ny, self._nz

        grad = np.zeros(L.total)

        # ── Lagrange / tracking (right-rectangular, sub-step n → x_{n+1}) ──
        for n in range(L.M):
            k = n // self._n_steps
            u_k = U[k]
            d_k = d_traj[k]
            x_np1 = X[n + 1]
            y_np1 = Y[n + 1] if self._is_dae else np.empty(0)
            t_np1 = t0 + (n + 1) * h

            x_np1_col = L.x_off + (n + 1) * nx
            u_k_col = L.u_off + k * nu

            # User Lagrange gradient
            if self._lagrange_jac is not None:
                lag_gx, lag_gy, lag_gu = self._lagrange_jac(
                    t_np1, x_np1, y_np1, u_k, p_theta
                )
                grad[x_np1_col:x_np1_col + nx] += np.asarray(lag_gx, dtype=float) * h
                grad[u_k_col:u_k_col + nu] += np.asarray(lag_gu, dtype=float) * h
                if self._is_dae and ny > 0:
                    y_np1_col = L.y_off + (n + 1) * ny
                    grad[y_np1_col:y_np1_col + ny] += (
                        np.asarray(lag_gy, dtype=float) * h
                    )

            # Tracking term
            if self._Q_z is not None:
                z_np1 = self._gm(x_np1, y_np1, u_k, d_k, p_theta, t_np1)
                e = z_np1 - self._z_ref[n + 1]
                Qze = self._Q_z @ e  # (nz,)

                if self._is_dae:
                    dgmdx_val = self._model.dgmdx(
                        x_np1, y_np1, u_k, d_k, p_theta, t_np1
                    )
                    dgmdu_val = self._model.dgmdu(
                        x_np1, y_np1, u_k, d_k, p_theta, t_np1
                    )
                    dgmdy_val = self._model.dgmdy(
                        x_np1, y_np1, u_k, d_k, p_theta, t_np1
                    )
                    y_np1_col = L.y_off + (n + 1) * ny
                    grad[y_np1_col:y_np1_col + ny] += (
                        dgmdy_val.T @ (2.0 * Qze) * h
                    )
                else:
                    dgmdx_val = self._model.dgmdx(x_np1, u_k, d_k, p_theta, t_np1)
                    dgmdu_val = self._model.dgmdu(x_np1, u_k, d_k, p_theta, t_np1)

                grad[x_np1_col:x_np1_col + nx] += dgmdx_val.T @ (2.0 * Qze) * h
                grad[u_k_col:u_k_col + nu] += dgmdu_val.T @ (2.0 * Qze) * h

        # ── Per-control-interval terms (ROM, input economy) ──
        for k in range(self._N):
            u_k = U[k]
            u_km1 = U[k - 1] if k > 0 else u_prev_0
            u_k_col = L.u_off + k * nu

            if self._Q_du is not None:
                du = u_k - u_km1
                Qdu_du = self._Q_du @ du
                grad[u_k_col:u_k_col + nu] += 2.0 * Qdu_du * Ts
                if k > 0:
                    u_km1_col = L.u_off + (k - 1) * nu
                    grad[u_km1_col:u_km1_col + nu] -= 2.0 * Qdu_du * Ts

            if self._p_u_eco is not None:
                grad[u_k_col:u_k_col + nu] += self._p_u_eco * Ts

        # ── Mayer ──
        if self._mayer_jac is not None:
            x_M = X[L.M]
            y_M = Y[L.M] if self._is_dae else np.empty(0)
            x_M_col = L.x_off + L.M * nx
            mayer_gx, mayer_gy = self._mayer_jac(x_M, y_M, p_theta)
            grad[x_M_col:x_M_col + nx] += np.asarray(mayer_gx, dtype=float)
            if self._is_dae and ny > 0:
                y_M_col = L.y_off + L.M * ny
                grad[y_M_col:y_M_col + ny] += np.asarray(mayer_gy, dtype=float)

        # ── Soft-x exact penalty ──
        if self._has_soft_x:
            PX_lo = L.get_PX_lo(z)
            PX_hi = L.get_PX_hi(z)
            for n in range(L.M + 1):
                px_lo_n_col = L.px_lo_off + n * nx
                px_hi_n_col = L.px_hi_off + n * nx
                if self._x_min is not None:
                    p_n = PX_lo[n]
                    grad[px_lo_n_col:px_lo_n_col + nx] += (
                        self._rho_x_1 + 2.0 * self._rho_x_2 * p_n
                    ) * h
                if self._x_max is not None:
                    q_n = PX_hi[n]
                    grad[px_hi_n_col:px_hi_n_col + nx] += (
                        self._rho_x_1 + 2.0 * self._rho_x_2 * q_n
                    ) * h

        # ── Soft-z exact penalty ──
        if self._has_soft_z:
            PZ = L.get_PZ(z)
            for n in range(L.M + 1):
                pz_n_col = L.pz_off + n * nz
                s_n = PZ[n]
                grad[pz_n_col:pz_n_col + nz] += (
                    self._rho_z_1 + 2.0 * self._rho_z_2 * s_n
                ) * h

        return grad

    def _can_use_analytical_objective_jac(self) -> bool:
        """True when all objective terms admit analytical gradients."""
        if self._lagrange is not None and self._lagrange_jac is None:
            return False
        if self._mayer is not None and self._mayer_jac is None:
            return False
        return True

    # ── Public solve / step ──────────────────────────────────────────────────

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
        Solve the EOCP from initial state ``x0``.

        Parameters
        ----------
        x0 : (nx,) ndarray
            Filtered initial state ``x̂_{0|0}`` — ``x_0 = x0`` is enforced
            as an equality constraint.
        d_trajectory : (N, nd) ndarray
            ZOH disturbance per control interval.
        u_prev : (N, nu) ndarray, optional
            Previous optimal input sequence; shifted by one interval to
            warm-start the NLP.
        x_prev : (M+1, nx) ndarray, optional
            Previous optimal differential-state trajectory; shifted by
            ``n_steps`` sub-steps to warm-start the NLP.
        y_prev : (M+1, ny) ndarray, optional
            Previous optimal algebraic-state trajectory (SDAE only).
        p : (nparams,) ndarray, optional
            Parameter vector ``θ``.  ``None`` → ``model.params``.
        t0 : float, optional
            Start time for the prediction horizon.

        Returns
        -------
        u_opt : (N, nu) ndarray
            Optimal input sequence.
        cost : float
            Optimal NLP objective value.
        info : dict
            ``{"X": (M+1, nx) ndarray, "Y": (M+1, ny) ndarray, "result": OptimizeResult}``
            for warm-starting the next call and inspection.
        """
        L = self._layout
        x_hat = np.asarray(x0, dtype=float)
        d_traj = np.asarray(d_trajectory, dtype=float)
        if d_traj.shape != (self._N, self._nd):
            raise ValueError(
                f"d_trajectory must have shape ({self._N}, {self._nd}); got {d_traj.shape}."
            )
        p_theta = (
            np.asarray(p, dtype=float)
            if p is not None
            else np.asarray(self._model.params, dtype=float)
        )
        u_prev_0 = (
            u_prev[-1] if u_prev is not None else np.zeros(self._nu)
        )

        # ── Warm-start initial guess ──
        z0 = self._build_initial_guess(x_hat, u_prev, x_prev, y_prev)

        # ── Bounds (input box, slack non-negativity) ──
        lb = np.full(L.total, -np.inf)
        ub = np.full(L.total, np.inf)
        if self._u_min is not None:
            lb[L.u_off:L.u_off + L.u_size] = np.tile(self._u_min, self._N)
        if self._u_max is not None:
            ub[L.u_off:L.u_off + L.u_size] = np.tile(self._u_max, self._N)
        # Slack variables ≥ 0
        if self._has_soft_x:
            lb[L.px_lo_off:L.px_lo_off + L.px_lo_size] = 0.0
            lb[L.px_hi_off:L.px_hi_off + L.px_hi_size] = 0.0
        if self._has_soft_z:
            lb[L.pz_off:L.pz_off + L.pz_size] = 0.0
        constraints: list[NLPConstraint] = [
            NLPConstraint(
                kind="eq",
                fun=lambda z: self._equality_constraints(z, x_hat, d_traj, p_theta, t0),
                jac=lambda z: self._equality_constraint_jac(z, x_hat, d_traj, p_theta, t0),
            )
        ]
        # Skip the inequality constraint dict if we have nothing to add — scipy
        # complains about empty constraint vectors.
        has_ineq = (
            self._du_min is not None
            or self._du_max is not None
            or self._has_soft_x
            or self._has_soft_z
        )
        if has_ineq:
            constraints.append(
                NLPConstraint(
                    kind="ineq",
                    fun=lambda z: self._inequality_constraints(z, u_prev_0, d_traj, p_theta, t0),
                    jac=lambda z: self._inequality_constraint_jac(z, u_prev_0, d_traj, p_theta, t0),
                )
            )

        # ── Objective gradient (analytical when all terms have gradients) ──
        obj_jac = (
            (lambda z: self._objective_jac(z, d_traj, u_prev_0, p_theta, t0))
            if self._can_use_analytical_objective_jac()
            else None
        )

        # ── Solve ──
        result = self._solver_backend.solve(
            NLPProblem(
                objective=lambda z: self._objective(z, x_hat, d_traj, u_prev_0, p_theta, t0),
                objective_jac=obj_jac,
                x0=z0,
                lb=lb,
                ub=ub,
                constraints=tuple(constraints),
            )
        )

        z_opt = result.x
        U = L.get_U(z_opt).copy()
        X = L.get_X(z_opt).copy()
        Y = L.get_Y(z_opt).copy() if self._is_dae else np.zeros((L.M + 1, 0))
        info = {"X": X, "Y": Y, "result": result}
        return U, float(result.fun), info

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
        """
        Solve and return only the first optimal control action (receding horizon).

        Returns
        -------
        u0 : (nu,) ndarray
        """
        u_opt, _, _ = self.solve(
            x0, d_trajectory,
            u_prev=u_prev, x_prev=x_prev, y_prev=y_prev,
            p=p, t0=t0,
        )
        return u_opt[0]


# ── Generic CD-NMPC Controller ───────────────────────────────────────────────


class CDNMPCController:
    """
    Closed-loop continuous-discrete NMPC controller (ControlToolbox §EMPC —
    *ENMPC Algorithm*).

    Composes any continuous-discrete state estimator with any OCP that
    exposes ``solve(x0, d_trajectory, …) → (u_opt, cost, info)`` (or the
    legacy two-tuple ``(u_opt, cost)``) into a receding-horizon controller.

    At each measurement time t_k:

      1. **Measure**   y^{m,s}_k  (passed in via :meth:`step`)
      2. **Estimate**  z^c_k = κ(z^c_{k−1}, u_{k−1}, d_{k−1}, y^{m,s}_k, θ^c)
                       (delegated to ``estimator.step``)
      3. **Optimise**  u_k = λ(z^c_k, θ^c)  (delegated to ``ocp.solve``)
      4. **Apply**     return ``u_k`` to the caller, who advances the plant.

    Parameters
    ----------
    estimator : object with ``step(ym, u, d, p, t) → (x_hat, P)`` (or ``(x_hat, y_hat, P)`` for SDAEs)
        Continuous-discrete state estimator.
    ocp : object with ``solve``, ``N``, ``nu``
        Optimal control problem (NLP solver) — typically an
        :class:`EconomicOptimalControlProblem`.
    """

    def __init__(self, estimator, ocp) -> None:
        self._estimator = estimator
        self._ocp = ocp
        self._u_seq_prev: np.ndarray | None = None
        self._x_traj_prev: np.ndarray | None = None
        self._y_traj_prev: np.ndarray | None = None
        self._u_prev: np.ndarray = np.zeros(ocp.nu)

    def step(
        self,
        y: np.ndarray,
        d_trajectory: np.ndarray,
        p: np.ndarray | None = None,
        t: float = 0.0,
    ) -> np.ndarray:
        """
        Execute one closed-loop ENMPC step.

        Parameters
        ----------
        y : (nym,) ndarray
            Current measurement ``y^{m,s}_k``.
        d_trajectory : (N, nd) ndarray
            Disturbance forecast over the horizon; ``d_trajectory[0] = d_k``.
        p : (nparams,) ndarray or None, optional
            Parameter vector ``θ^c``.  ``None`` → empty vector.
        t : float, optional
            Current time ``t_k``.

        Returns
        -------
        u_k : (nu,) ndarray
            Optimal input ``u_k`` to apply over ``[t_k, t_{k+1}]``.
        """
        p_ = np.array([], dtype=float) if p is None else np.asarray(p, dtype=float)
        d0 = d_trajectory[0]

        # 2. Estimate
        est_out = self._estimator.step(y, self._u_prev, d0, p_, t)
        # Estimator may return (x_hat, P) or (x_hat, y_hat, P).
        x_hat = est_out[0]

        # 3. Optimise
        u_opt, _, info = self._ocp.solve(
            x_hat,
            d_trajectory,
            u_prev=self._u_seq_prev,
            x_prev=self._x_traj_prev,
            y_prev=self._y_traj_prev,
            p=p_,
            t0=t,
        )
        u_k = u_opt[0]

        # Cache for next warm-start
        self._u_seq_prev = u_opt
        self._x_traj_prev = info.get("X")
        self._y_traj_prev = info.get("Y")
        self._u_prev = u_k

        # 4. Apply (return to caller)
        return u_k
