"""
Optimal Control Problem (OCP) for linear discrete-time MPC.

Linear specialisation of the ControlToolbox §EMPC formulation: when the
plant dynamics are linear and the OCP is restricted to quadratic stage
costs and box / soft-box constraints, the entire NLP reduces to a single
finite-horizon **quadratic program** that is solved directly with a convex-QP
backend (OSQP by default; HiGHS also available) — strictly more efficient than
the implicit-Euler direct-simultaneous formulation used by
:class:`~mbc.control.GeneralContinuousOCP` for nonlinear plants.

Plant model (ControlToolbox notation, discrete-time specialisation)
-------------------------------------------------------------------
    x[k+1] = Ad x[k] + Bd u[k] + Ed d[k] + Gd w[k],   w[k] ~ N(0, Qd)
    z[k]   = Cz x[k] + Dz u[k] + Fz d[k]
    ym[k]  = Cm x[k] + Dm u[k] + Fm d[k] + v[k],       v[k] ~ N(0, Rm)

The OCP optimises the *output* ``z[k] = Cz x[k] + …``.  When the plant has
``Cz = Cm`` (the default of :class:`~mbc.models.LinearDiscreteModel`) the
output and measurement coincide and the OCP tracks the measured channel
directly.

Cost function over horizon N
----------------------------
    Φ(U) = Σ_{k=0}^{N-1} [ ‖z[k+1] − z_ref‖²_Q + ‖u[k]‖²_R + ‖Δu[k]‖²_S ]
         + ‖z[N] − z_ref‖²_P
         + ρ Σ_{k=0}^{N-1} ‖ε[k+1]‖²

with Δu[k] = u[k] − u[k−1] (rate of movement) and ε[k] the soft-output
slack variable.  (The QP objective is scaled by ½ relative to Φ, which does
not change the optimiser.)

Constraints
-----------
    x[k+1] = Ad x[k] + Bd u[k] + Ed d[k]                (deterministic dynamics)
    u_min ≤ u[k] ≤ u_max                                  (hard input box)
    du_min ≤ u[k] − u[k−1] ≤ du_max                       (hard input ROM box, optional)
    z_ref − δ − ε[k+1] ≤ Cz x[k+1] ≤ z_ref + δ + ε[k+1]  (soft output box)
    ε[k+1] ≥ 0                                            (slack non-negativity)

Two equivalent formulations
---------------------------
The same QP is built in one of two ways, selected by ``formulation``:

* **condensed** — eliminate the states via the lifted prediction
  ``X = Ψ x₀ + Γ U + Λ D`` and optimise over ``Z = [U; ε]`` only.  The
  Hessian is dense and the prediction matrices cost O(N²) to build, but the
  problem is small (``N·nu + N·nz`` variables).  Best for short horizons.

* **sparse** (a.k.a. simultaneous / non-condensed) — keep the states as
  decision variables ``Z = [X; U; ε]`` and impose the dynamics as
  block-banded linear *equality* constraints.  The Hessian and constraint
  matrices are sparse with O(N) nonzeros, so this scales far better for long
  horizons; HiGHS exploits the sparsity directly.

``formulation="auto"`` (default) is **backend-aware**: with OSQP (the default
backend) it resolves to ``sparse``, and with HiGHS it resolves to
``condensed``.  This pairs each solver with the formulation it handles best —
OSQP's sparse first-order solver exploits the banded KKT structure and warm
starts, scaling ~linearly in the horizon, whereas HiGHS's active-set QP is
fastest on the small dense condensed problem (and the dense condensed Hessian
is ill-conditioned for first-order methods at long horizons).  Both
formulations yield the same optimiser to solver tolerance.

Empirically (see ``scripts/qp_formulation_benchmark.py``) OSQP+sparse is the
fastest combination and scales best with the horizon, which is why it is the
default; hence ``solver="osqp"`` with ``formulation="auto"``.

Notation
--------
    nx   – state dimension          x ∈ ℝⁿˣ
    nu   – input dimension          u ∈ ℝⁿᵘ
    nd   – disturbance dimension    d ∈ ℝⁿᵈ
    nz   – output dimension         z ∈ ℝⁿᶻ
    N    – prediction horizon
    Ψ    – free-response matrix     Ψ ∈ ℝᴺⁿˣˣⁿˣ
    Γ    – forced-response matrix   Γ ∈ ℝᴺⁿˣˣᴺⁿᵘ
    Λ    – disturbance matrix       Λ ∈ ℝᴺⁿˣˣᴺⁿᵈ
"""

from __future__ import annotations

import warnings
from abc import ABC, abstractmethod
from typing import Any, Callable, TYPE_CHECKING

import numpy as np
from scipy.linalg import block_diag

from .._utils import _any_to_np1d, _any_to_np2d
from .qp_solver import QPProblem, QPSolverBackend, make_qp_backend

if TYPE_CHECKING:
    from ..models import LinearDiscreteModel


# ── First-difference operator ────────────────────────────────────────────


def _build_rate_inequalities(
    nu: int,
    N: int,
    u_prev: np.ndarray,
    du_min: np.ndarray | None,
    du_max: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray] | tuple[None, None]:
    """
    Build linear inequalities ``G_rate @ U ≤ h_rate`` for hard input ROM box.

    For each step ``k`` and input component ``i``:

        du_min[i] ≤ u[k, i] − u[k−1, i] ≤ du_max[i]

    with ``u[−1] = u_prev``.  Either bound may be omitted (``None``).
    """
    if du_min is None and du_max is None:
        return None, None

    u_prev = np.asarray(u_prev, dtype=float).reshape(nu)
    n_rows = N * nu * (int(du_max is not None) + int(du_min is not None))
    G = np.zeros((n_rows, N * nu))
    h = np.zeros(n_rows)
    row = 0
    for k in range(N):
        for i in range(nu):
            col_k = k * nu + i
            u_prev_i = u_prev[i] if k == 0 else None
            if du_max is not None:
                # u[k,i] − u[k−1,i] ≤ du_max
                G[row, col_k] = 1.0
                if k > 0:
                    G[row, (k - 1) * nu + i] = -1.0
                    h[row] = du_max[i]
                else:
                    h[row] = du_max[i] + u_prev_i
                row += 1
            if du_min is not None:
                # u[k,i] − u[k−1,i] ≥ du_min  →  −u[k,i] + u[k−1,i] ≤ −du_min
                G[row, col_k] = -1.0
                if k > 0:
                    G[row, (k - 1) * nu + i] = 1.0
                    h[row] = -du_min[i]
                else:
                    h[row] = -du_min[i] - u_prev_i
                row += 1
    return G, h


def _build_D_diff(nu: int, N: int) -> np.ndarray:
    """
    Block first-difference matrix ``D_diff`` for the rate-of-movement
    penalty ``ΔU = D_diff U + d₀``:

        [ I               ]       Δu[0] = u[0] − u_prev
        [−I   I           ]       Δu[1] = u[1] − u[0]
        [    −I   I       ]       …
        [         ⋱    I  ]       Δu[N−1] = u[N−1] − u[N−2]
    """
    dim = N * nu
    D = np.zeros((dim, dim))
    for k in range(N):
        for i in range(nu):
            D[k * nu + i, k * nu + i] = 1.0
            if k > 0:
                D[k * nu + i, (k - 1) * nu + i] = -1.0
    return D


def _shift_warm_start(
    U_seq: np.ndarray,
    X_seq: np.ndarray,
    nu: int,
    nx: int,
) -> dict[str, np.ndarray]:
    """
    Shift a previous horizon solution forward by one step for warm-starting.

    Given the previous optimal input sequence ``U_seq = [u₀ … u_{N−1}]`` and
    predicted states ``X_seq = [x₁ … x_N]``, the next receding-horizon QP
    starts one step later, so a good initial guess re-uses the tail and
    repeats the final element:

        U_warm = [u₁, …, u_{N−1}, u_{N−1}]
        X_warm = [x₂, …, x_N,     x_N]

    Returns a ``{"U": …, "X": …}`` dict suitable for ``solve(warm_start=…)``.
    """
    U = np.asarray(U_seq, dtype=float).reshape(-1, nu)
    X = np.asarray(X_seq, dtype=float).reshape(-1, nx)
    U_warm = np.vstack([U[1:], U[-1:]]) if U.shape[0] > 1 else U
    X_warm = np.vstack([X[1:], X[-1:]]) if X.shape[0] > 1 else X
    return {"U": U_warm.reshape(-1), "X": X_warm.reshape(-1)}


# ── Discrete OCP base and standard QP implementation ─────────────────────


class DiscreteOptimalControlProblem(ABC):
    """
    Abstract base for discrete-time horizon optimal control problems.

    Concrete subclasses implement :meth:`solve` for a fixed prediction
    horizon ``N`` and expose the plant dimensions via the wrapped model.
    """

    @abstractmethod
    def solve(
        self,
        x0: Any,
        D: Any,
        x_ref: Any,
        u_prev: Any | None = None,
        warm_start: dict[str, np.ndarray] | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return optimal input and state trajectories over the horizon."""


class StandardLinearDiscreteOCP(DiscreteOptimalControlProblem):
    """
    Standard discrete-time tracking OCP for linear discrete-time plants.

    Solved as a convex QP with hard input and soft output box constraints.

    The OCP tracks the **output** ``z[k] = Cz x[k]`` against a constant
    reference ``z_ref``.  When ``Cz = Cm`` (the default of
    :class:`~mbc.models.LinearDiscreteModel`) the output and the
    measurement coincide and the OCP tracks the measured channel
    directly.

    Parameters
    ----------
    model : LinearDiscreteModel
        Plant model providing ``Ad``, ``Bd``, ``Ed``, ``Cz``, ``u_bounds``.
    N : int
        Prediction horizon (number of control intervals).
    Q : (nz, nz) array-like
        Stage tracking cost ``‖z − z_ref‖²_Q``.
    R : (nu, nu) array-like
        Stage input cost ``‖u‖²_R``.
    P : (nz, nz) array-like, optional
        Terminal tracking cost ``‖z[N] − z_ref‖²_P``.  Default: ``Q``.
    S : (nu, nu) array-like, optional
        Input rate-of-movement cost ``‖Δu‖²_S``.  ``None`` disables.
    du_min, du_max : (nu,) array-like, optional
        Hard input rate-of-movement box on ``Δu[k] = u[k] − u[k−1]``.
        ``None`` disables the corresponding bound.
    rho : float, optional
        Quadratic penalty on the soft-output slack variable ``ε``.
        Default: 1e4.
    y_offset : float, optional
        Symmetric half-width δ of the soft-output band ``[z_ref − δ,
        z_ref + δ]``.  Default: 2.0.
    solver : str or QPSolverBackend, optional
        Convex-QP backend selector.  ``"osqp"`` (default) uses the Apache-2.0
        OSQP solver (sparse, warm-startable, fastest here); ``"highs"`` uses
        the MIT-licensed HiGHS solver via ``highspy``.  A
        :class:`~mbc.control.qp_solver.QPSolverBackend` instance may also be
        supplied directly.
    solver_options : dict, optional
        Backend-specific options forwarded to the QP solver.
    formulation : {"auto", "condensed", "sparse"}, optional
        QP construction strategy (see the module docstring).  Default
        ``"auto"`` is backend-aware: ``"sparse"`` for OSQP and ``"condensed"``
        for HiGHS.  Note that OSQP with ``"condensed"`` is *not* recommended
        for long horizons — the dense condensed Hessian is ill-conditioned for
        OSQP's first-order method; use the (default) sparse form with OSQP.
    """

    def __init__(
        self,
        model: "LinearDiscreteModel",
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
        solver: str | QPSolverBackend = "highs",
        solver_options: dict[str, Any] | None = None,
        formulation: str = "auto",
    ) -> None:
        if formulation not in ("auto", "condensed", "sparse"):
            raise ValueError(
                f"formulation must be 'auto', 'condensed', or 'sparse'; "
                f"got {formulation!r}."
            )
        self._model = model
        self._N = N
        self._Q = _any_to_np2d(Q)
        self._R = _any_to_np2d(R)
        self._P = _any_to_np2d(P) if P is not None else self._Q.copy()
        self._S = _any_to_np2d(S) if S is not None else None
        self._du_min = (
            _any_to_np1d(du_min).reshape(-1) if du_min is not None else None
        )
        self._du_max = (
            _any_to_np1d(du_max).reshape(-1) if du_max is not None else None
        )
        if self._du_min is not None and self._du_min.shape[0] != model.nu:
            raise ValueError(
                f"du_min must have length {model.nu}; got {self._du_min.shape[0]}."
            )
        if self._du_max is not None and self._du_max.shape[0] != model.nu:
            raise ValueError(
                f"du_max must have length {model.nu}; got {self._du_max.shape[0]}."
            )
        self._rho = rho
        self._rho_lin = rho_lin
        self._y_offset = y_offset
        self._backend = make_qp_backend(solver, solver_options=solver_options)
        self._formulation = formulation

        nu = model.nu
        # Pre-compute constant structures
        self._D_diff: np.ndarray | None = None
        self._S_bar: np.ndarray | None = None
        if self._S is not None:
            self._D_diff = _build_D_diff(nu, N)
            self._S_bar = block_diag(*([self._S] * N))

    def _resolve_formulation(self) -> str:
        """Return the concrete formulation ('condensed' or 'sparse').

        ``"auto"`` is backend-aware:

        * OSQP (sparse first-order, exploits the banded KKT structure and
          warm starts) → ``"sparse"`` — scales ~linearly in the horizon and
          is the faster, well-conditioned choice.
        * HiGHS (active-set QP, does not exploit banded structure; the dense
          condensed Hessian is ill-conditioned for first-order methods but
          fine for active-set) → ``"condensed"``.
        """
        if self._formulation != "auto":
            return self._formulation
        from .qp_solver import OSQPBackend
        return "sparse" if isinstance(self._backend, OSQPBackend) else "condensed"

    # ── Public solve ─────────────────────────────────────────────────────

    def solve(
        self,
        x0: Any,
        D: Any,
        x_ref: Any,
        u_prev: Any | None = None,
        warm_start: dict[str, np.ndarray] | None = None,
        *,
        u_ss: Any | None = None,
        x_ref_dev_seq: Any | None = None,
        offset_seq: Any | None = None,
        q_scale_seq: Any | None = None,
        r_scale_seq: Any | None = None,
        u_min_seq: Any | None = None,
        u_max_seq: Any | None = None,
        price_seq: Any | None = None,
        elec_heat: Any | None = None,
        elec_cool: Any | None = None,
        bid_mask: Any | None = None,
        price_weight: float = 0.0,
        dt_h: float = 0.25,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Solve the QP starting from state estimate ``x0``.

        Parameters
        ----------
        x0    : (nx,) array-like — current state estimate ``x̂_{k|k}``.
        D     : (N · nd,) array-like — stacked disturbance forecast
                ``[d[0]; d[1]; …; d[N − 1]]``.
        x_ref : (nx,) array-like — state reference; the output reference is
                ``z_ref = Cz x_ref``.
        u_prev : (nu,) array-like, optional
            Previously-applied input — used only when an input rate-of-
            movement penalty ``S`` is active.
        warm_start : dict, optional
            ``{"U": (N·nu,), "X": (N·nx,)}`` primal warm-start trajectory
            (typically the previous solution shifted one step by
            :func:`_shift_warm_start`).  Ignored if the shapes do not match.
        u_ss, x_ref_dev_seq, offset_seq, q_scale_seq, r_scale_seq,
        u_min_seq, u_max_seq, price_seq, elec_heat, elec_cool, bid_mask,
        price_weight, dt_h :
            Optional time-varying horizon parameters.  When any are supplied the
            condensed forecast QP path is used (see :func:`forecast_ocp.solve_forecast_qp`).

        Returns
        -------
        U : (N · nu,) ndarray — optimal input sequence.
        X : (N · nx,) ndarray — predicted state trajectory ``[x[1]; …; x[N]]``.
        """
        has_price = (
            price_seq is not None
            and price_weight > 0.0
            and elec_heat is not None
        )
        has_forecast = (
            (u_ss is not None and not np.allclose(u_ss, 0.0))
            or x_ref_dev_seq is not None
            or offset_seq is not None
            or q_scale_seq is not None
            or r_scale_seq is not None
            or u_min_seq is not None
            or u_max_seq is not None
            or has_price
        )
        if has_forecast:
            from .forecast_ocp import solve_forecast_qp

            return solve_forecast_qp(
                self,
                x0,
                D,
                x_ref,
                u_prev,
                u_ss=u_ss,
                x_ref_dev_seq=x_ref_dev_seq,
                offset_seq=offset_seq,
                q_scale_seq=q_scale_seq,
                r_scale_seq=r_scale_seq,
                u_min_seq=u_min_seq,
                u_max_seq=u_max_seq,
                price_seq=price_seq,
                elec_heat=elec_heat,
                elec_cool=elec_cool,
                bid_mask=bid_mask,
                price_weight=price_weight,
                dt_h=dt_h,
            )

        N = self._N
        nx = self._model.nx
        nu = self._model.nu
        nd = self._model.nd
        Cz = _any_to_np2d(self._model.Cz)
        nz = Cz.shape[0]

        # ── Coerce inputs to numpy 1-D ──────────────────────────────────
        x0 = _any_to_np1d(x0).reshape(-1)
        x_ref = _any_to_np1d(x_ref).reshape(-1)
        D = _any_to_np1d(D).reshape(-1) if D is not None else np.zeros(N * nd)

        Ad = _any_to_np2d(self._model.Ad)
        Bd = _any_to_np2d(self._model.Bd)
        Ed = _any_to_np2d(self._model.Ed)

        # Rate-of-movement reference input (penalty and/or hard ROM box)
        if self._S is not None or self._du_min is not None or self._du_max is not None:
            u_prev_np = (
                np.zeros(nu) if u_prev is None
                else _any_to_np1d(u_prev).reshape(-1)
            )
        else:
            u_prev_np = None

        formulation = self._resolve_formulation()
        if formulation == "sparse":
            qp_kwargs, extract = self._build_sparse(
                x0, D, x_ref, Ad, Bd, Ed, Cz, nx, nu, nd, nz, u_prev_np
            )
        else:
            qp_kwargs, extract = self._build_condensed(
                x0, D, x_ref, Ad, Bd, Ed, Cz, nx, nu, nd, nz, u_prev_np
            )

        z_warm = self._assemble_warm(formulation, warm_start, N, nx, nu, nz)

        result = self._backend.solve(QPProblem(warm_start=z_warm, **qp_kwargs))

        if not result.success:
            warnings.warn(
                f"StandardLinearDiscreteOCP.solve: QP solver returned status "
                f"'{result.status}'; returning zero inputs as fallback.",
                RuntimeWarning,
                stacklevel=2,
            )
            U_flat = np.zeros(N * nu)
            X_flat = self._simulate(x0, U_flat, D, Ad, Bd, Ed, N, nx, nu, nd)
            return U_flat, X_flat

        return extract(np.asarray(result.x, dtype=float))

    # ── Warm-start assembly ────────────────────────────────────────────

    @staticmethod
    def _assemble_warm(
        formulation: str,
        warm_start: dict[str, np.ndarray] | None,
        N: int,
        nx: int,
        nu: int,
        nz: int,
    ) -> np.ndarray | None:
        """Map a physical ``{"U", "X"}`` warm start to the decision vector."""
        if warm_start is None:
            return None
        n_U = N * nu
        n_eps = N * nz
        n_X = N * nx
        U_w = warm_start.get("U")
        if U_w is None:
            return None
        U_w = np.asarray(U_w, dtype=float).reshape(-1)
        if U_w.shape[0] != n_U:
            return None
        if formulation == "condensed":
            return np.concatenate([U_w, np.zeros(n_eps)])
        X_w = warm_start.get("X")
        if X_w is None:
            return None
        X_w = np.asarray(X_w, dtype=float).reshape(-1)
        if X_w.shape[0] != n_X:
            return None
        return np.concatenate([X_w, U_w, np.zeros(n_eps)])

    # ── Forward simulation (fallback X reconstruction) ──────────────────

    @staticmethod
    def _simulate(
        x0: np.ndarray,
        U_flat: np.ndarray,
        D: np.ndarray,
        Ad: np.ndarray,
        Bd: np.ndarray,
        Ed: np.ndarray,
        N: int,
        nx: int,
        nu: int,
        nd: int,
    ) -> np.ndarray:
        """Roll the deterministic dynamics forward to get ``X = [x₁ … x_N]``."""
        X = np.zeros(N * nx)
        xk = x0
        for k in range(N):
            uk = U_flat[k * nu:(k + 1) * nu]
            dk = D[k * nd:(k + 1) * nd]
            xk = Ad @ xk + Bd @ uk + Ed @ dk
            X[k * nx:(k + 1) * nx] = xk
        return X

    # ── Condensed (dense, state-eliminated) builder ─────────────────────

    def _build_condensed(
        self, x0, D, x_ref, Ad, Bd, Ed, Cz, nx, nu, nd, nz, u_prev_np,
    ) -> tuple[dict[str, Any], Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]]]:
        N = self._N

        # Powers of Ad
        Ad_pow = [np.eye(nx)]
        for _ in range(N):
            Ad_pow.append(Ad @ Ad_pow[-1])

        # State prediction matrices  X = Ψ x₀ + Γ U + Λ D
        Psi = np.zeros((N * nx, nx))
        Gamma = np.zeros((N * nx, N * nu))
        Lambda = np.zeros((N * nx, N * nd))
        for k in range(N):
            Psi[k * nx:(k + 1) * nx, :] = Ad_pow[k + 1]
            for j in range(k + 1):
                Ak = Ad_pow[k - j]
                Gamma[k * nx:(k + 1) * nx, j * nu:(j + 1) * nu] = Ak @ Bd
                Lambda[k * nx:(k + 1) * nx, j * nd:(j + 1) * nd] = Ak @ Ed

        Cz_bar = np.kron(np.eye(N), Cz)
        CG = Cz_bar @ Gamma
        CP = Cz_bar @ Psi
        CL = Cz_bar @ Lambda

        Q_bar = block_diag(*([self._Q] * (N - 1) + [self._P])) if N > 1 else self._P
        R_bar = block_diag(*([self._R] * N))

        z_ref = Cz @ x_ref
        z_ref_bar = np.tile(z_ref, N)
        Z_free = CP @ x0 + CL @ D
        e_free = Z_free - z_ref_bar

        H_uu = CG.T @ Q_bar @ CG + R_bar
        f_u = CG.T @ Q_bar @ e_free

        if self._S is not None:
            d0 = np.zeros(N * nu)
            d0[:nu] = -u_prev_np
            H_uu = H_uu + self._D_diff.T @ self._S_bar @ self._D_diff
            f_u = f_u + self._D_diff.T @ self._S_bar @ d0

        n_U = N * nu
        n_eps = N * nz
        n_Z = n_U + n_eps

        H = np.zeros((n_Z, n_Z))
        H[:n_U, :n_U] = H_uu
        H[n_U:, n_U:] = self._rho * np.eye(n_eps)
        H = 0.5 * (H + H.T)

        f = np.zeros(n_Z)
        f[:n_U] = f_u
        if self._rho_lin > 0.0:
            f[n_U:] = self._rho_lin

        u_min, u_max = self._model.u_bounds
        u_min_t = np.tile(_any_to_np1d(u_min).reshape(-1), N)
        u_max_t = np.tile(_any_to_np1d(u_max).reshape(-1), N)
        lb = np.concatenate([u_min_t, np.zeros(n_eps)])
        ub = np.concatenate([u_max_t, np.full(n_eps, np.inf)])

        z_min_t = np.tile(z_ref - self._y_offset, N)
        z_max_t = np.tile(z_ref + self._y_offset, N)
        neg_I = -np.eye(n_eps)
        G = np.vstack([np.hstack([-CG, neg_I]), np.hstack([CG, neg_I])])
        h = np.concatenate([-z_min_t + Z_free, z_max_t - Z_free])

        if u_prev_np is not None and (
            self._du_min is not None or self._du_max is not None
        ):
            G_rate, h_rate = _build_rate_inequalities(
                nu, N, u_prev_np, self._du_min, self._du_max
            )
            if G_rate is not None:
                G = np.vstack([G, np.hstack([G_rate, np.zeros((G_rate.shape[0], n_eps))])])
                h = np.concatenate([h, h_rate])

        def extract(z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            U_flat = z[:n_U]
            X_flat = Psi @ x0 + Gamma @ U_flat + Lambda @ D
            return U_flat, X_flat

        return {"P": H, "q": f, "lb": lb, "ub": ub, "G": G, "h": h}, extract

    # ── Sparse (simultaneous, banded) builder ───────────────────────────

    def _build_sparse(
        self, x0, D, x_ref, Ad, Bd, Ed, Cz, nx, nu, nd, nz, u_prev_np,
    ) -> tuple[dict[str, Any], Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]]]:
        import scipy.sparse as sp

        N = self._N
        n_X = N * nx
        n_U = N * nu
        n_eps = N * nz
        n_Z = n_X + n_U + n_eps
        oU = n_X            # offset of U block
        oE = n_X + n_U      # offset of ε block

        z_ref = Cz @ x_ref
        f = np.zeros(n_Z)

        # ── Objective Hessian ½ ZᵀHZ (block diagonal, assembled sparse) ──
        Czt = Cz.T
        H_state = Czt @ self._Q @ Cz          # repeated stage state-cost block
        H_state_term = Czt @ self._P @ Cz     # terminal state-cost block
        x_blocks = [H_state] * (N - 1) + [H_state_term] if N > 1 else [H_state_term]
        for k in range(N):
            Qk = self._P if k == N - 1 else self._Q
            f[k * nx:(k + 1) * nx] = -Czt @ (Qk @ z_ref)

        R_bar = block_diag(*([self._R] * N))
        if self._S is not None:
            d0 = np.zeros(n_U)
            d0[:nu] = -u_prev_np
            H_uu = R_bar + self._D_diff.T @ self._S_bar @ self._D_diff
            f[oU:oU + n_U] = self._D_diff.T @ self._S_bar @ d0
        else:
            H_uu = R_bar

        H = sp.block_diag(
            x_blocks + [H_uu, self._rho * sp.eye(n_eps)],
            format="csc",
        )
        if self._rho_lin > 0.0:
            f[oE:oE + n_eps] = self._rho_lin

        # ── Dynamics equality constraints  A_eq Z = b_eq (banded) ───────
        rows: list[int] = []
        cols: list[int] = []
        vals: list[float] = []

        def _add_block(r0, c0, M):
            Mr, Mc = M.shape
            for i in range(Mr):
                for j in range(Mc):
                    v = M[i, j]
                    if v != 0.0:
                        rows.append(r0 + i)
                        cols.append(c0 + j)
                        vals.append(v)

        b_eq = np.zeros(n_X)
        eye_nx = np.eye(nx)
        for k in range(N):                     # equation for x[k+1]
            rs = k * nx
            _add_block(rs, k * nx, eye_nx)             # +x[k+1]
            _add_block(rs, oU + k * nu, -Bd)           # −Bd u[k]
            dk = D[k * nd:(k + 1) * nd]
            if k == 0:
                b_eq[rs:rs + nx] = Ad @ x0 + Ed @ dk
            else:
                _add_block(rs, (k - 1) * nx, -Ad)      # −Ad x[k]
                b_eq[rs:rs + nx] = Ed @ dk
        A_eq = sp.csc_matrix((vals, (rows, cols)), shape=(n_X, n_Z))

        # ── Bounds ──────────────────────────────────────────────────────
        u_min, u_max = self._model.u_bounds
        u_min_t = np.tile(_any_to_np1d(u_min).reshape(-1), N)
        u_max_t = np.tile(_any_to_np1d(u_max).reshape(-1), N)
        lb = np.concatenate([np.full(n_X, -np.inf), u_min_t, np.zeros(n_eps)])
        ub = np.concatenate([np.full(n_X, np.inf), u_max_t, np.full(n_eps, np.inf)])

        # ── Soft output box (inequalities on the states), assembled sparse
        #   Cz x[k+1] − ε[k] ≤ z_max     and    −Cz x[k+1] − ε[k] ≤ −z_min
        g_rows: list[int] = []
        g_cols: list[int] = []
        g_vals: list[float] = []

        def _add_g(r0, c0, M):
            Mr, Mc = M.shape
            for i in range(Mr):
                for j in range(Mc):
                    v = M[i, j]
                    if v != 0.0:
                        g_rows.append(r0 + i)
                        g_cols.append(c0 + j)
                        g_vals.append(v)

        neg_eye_nz = -np.eye(nz)
        h = np.zeros(2 * n_eps)
        z_min = z_ref - self._y_offset
        z_max = z_ref + self._y_offset
        for k in range(N):
            xs = k * nx
            es = oE + k * nz
            r_hi = k * nz
            r_lo = n_eps + k * nz
            _add_g(r_hi, xs, Cz)
            _add_g(r_hi, es, neg_eye_nz)
            h[r_hi:r_hi + nz] = z_max
            _add_g(r_lo, xs, -Cz)
            _add_g(r_lo, es, neg_eye_nz)
            h[r_lo:r_lo + nz] = -z_min
        G = sp.csc_matrix((g_vals, (g_rows, g_cols)), shape=(2 * n_eps, n_Z))

        if u_prev_np is not None and (
            self._du_min is not None or self._du_max is not None
        ):
            G_rate, h_rate = _build_rate_inequalities(
                nu, N, u_prev_np, self._du_min, self._du_max
            )
            if G_rate is not None:
                n_rate = G_rate.shape[0]
                rate_rows: list[int] = []
                rate_cols: list[int] = []
                rate_vals: list[float] = []
                for ri in range(n_rate):
                    for ci in range(G_rate.shape[1]):
                        v = G_rate[ri, ci]
                        if v != 0.0:
                            rate_rows.append(ri)
                            rate_cols.append(oU + ci)
                            rate_vals.append(v)
                G_rate_sp = sp.csc_matrix(
                    (rate_vals, (rate_rows, rate_cols)),
                    shape=(n_rate, n_Z),
                )
                G = sp.vstack([G, G_rate_sp], format="csc")
                h = np.concatenate([h, h_rate])

        def extract(z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            X_flat = z[:n_X]
            U_flat = z[oU:oU + n_U]
            return U_flat, X_flat

        return (
            {"P": H, "q": f, "lb": lb, "ub": ub, "G": G, "h": h, "A": A_eq, "b": b_eq},
            extract,
        )


# Backward-compatible aliases (deprecated).
StandardDiscreteOCP = StandardLinearDiscreteOCP
OptimalControlProblem = StandardLinearDiscreteOCP
