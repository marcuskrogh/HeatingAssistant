"""
Optimal Control Problem (OCP) for Model Predictive Control.

Formulates and solves a finite-horizon quadratic program (QP) at each
MPC step.  The controller tracks per-output setpoints with configurable
input and output constraints.

Problem formulation
-------------------
Given a linear discrete-time system

    x[k+1] = A x[k] + B u[k] + E d[k],      y[k] = C x[k]

the OCP over horizon N is:

    min_{U}  J(U) = Σₖ₌₀ᴺ⁻¹ [ ‖y[k+1] − r‖²_Q  +  ‖u[k]‖²_R
                               + ‖Δu[k]‖²_S ]
                   + ‖y[N] − r‖²_P
                   + ρ Σₖ₌₀ᴺ⁻¹ ‖ε[k+1]‖²

    s.t.  x[k+1] = A x[k] + B u[k] + E d[k]       (dynamics)
          u_min ≤ u[k] ≤ u_max                      (hard input box)
          y_min − ε[k+1] ≤ C x[k+1] ≤ y_max + ε[k+1]   (soft output box)
          ε[k+1] ≥ 0                                 (slack non-negativity)

where:
    r      – output setpoint (reference)
    Δu[k]  = u[k] − u[k−1]  (input rate of movement)
    ε[k]   – slack variable for soft output constraint violation
    ρ      – penalty weight on output constraint violation

The soft output constraints keep outputs within
``[r − δ,  r + δ]`` where δ is the configurable constraint offset,
but allow temporary violations at the cost of the quadratic slack
penalty.

Batch (lifted) form
--------------------
Stacking the state predictions X = [x[1]; …; x[N]] gives

    X = Ψ x₀ + Γ U + Λ D

where Ψ, Γ, Λ are the standard prediction matrices built from A, B, E.
Stacking the output predictions Y = C̄ X (block-diagonal C̄) gives

    Y = C̄ Ψ x₀ + C̄ Γ U + C̄ Λ D

The QP decision variable is z = [U; ε] and the problem is cast as:

    min_z   ½ zᵀ H z + fᵀ z
    s.t.    G z ≤ h

and solved using ``cvxopt.solvers.qp``.

Notation
--------
    n   – state dimension          x ∈ ℝⁿ
    m   – input dimension          u ∈ ℝᵐ
    p   – disturbance dimension    d ∈ ℝᵖ
    l   – output dimension         y ∈ ℝˡ
    N   – prediction horizon
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cvxopt import matrix, spmatrix, solvers

from .._utils import _eye, _zeros, _np_to_cvx

if TYPE_CHECKING:
    from ..models import LinearDiscreteModel

# Silence cvxopt solver output
solvers.options["show_progress"] = False


# ── Helpers ──────────────────────────────────────────────────────────────


def _block_diag(M: matrix, N_blocks: int) -> matrix:
    """Build a block-diagonal matrix by repeating M along the diagonal."""
    r, c = M.size
    out = _zeros(N_blocks * r, N_blocks * c)
    for k in range(N_blocks):
        for i in range(r):
            for j in range(c):
                out[k * r + i, k * c + j] = M[i, j]
    return out


def _block_diag_terminal(M: matrix, M_terminal: matrix, N_blocks: int) -> matrix:
    """
    Block-diagonal matrix with M for the first N−1 blocks and
    M_terminal for the last block.
    """
    r, c = M.size
    out = _zeros(N_blocks * r, N_blocks * c)
    for k in range(N_blocks):
        blk = M_terminal if k == N_blocks - 1 else M
        for i in range(r):
            for j in range(c):
                out[k * r + i, k * c + j] = blk[i, j]
    return out


def _tile_column(v: matrix, N_blocks: int) -> matrix:
    """Vertically stack N copies of column vector v."""
    n = v.size[0]
    out = matrix(0.0, (N_blocks * n, 1))
    for k in range(N_blocks):
        out[k * n:(k + 1) * n] = v
    return out


# ── First-difference operator ────────────────────────────────────────────


def _build_D_diff(n_u: int, N: int) -> matrix:
    """
    Build the block first-difference matrix D_diff for the rate-of-
    movement penalty  ΔU = D_diff U + d₀.

    Structure (block rows of size n_u):
        [ I                ]       ΔU[0] = u[0] − u_prev
        [−I   I            ]       ΔU[1] = u[1] − u[0]
        [    −I   I        ]       …
        [         ⋱    I   ]       ΔU[N−1] = u[N−1] − u[N−2]
    """
    dim = N * n_u
    D = _zeros(dim, dim)
    for k in range(N):
        for i in range(n_u):
            D[k * n_u + i, k * n_u + i] = 1.0
            if k > 0:
                D[k * n_u + i, (k - 1) * n_u + i] = -1.0
    return D


# ── Optimal Control Problem ─────────────────────────────────────────────


class OptimalControlProblem:
    """
    Receding-horizon quadratic OCP with hard input and soft output constraints.

    Parameters
    ----------
    model : LinearDiscreteModel
        Plant model providing dimensions, C matrix, bounds, and ``discretize``.
    N : int
        Prediction horizon (number of steps).
    Q : cvxopt.matrix (l, l)
        Stage output tracking cost  ‖y − r‖²_Q.
    R : cvxopt.matrix (m, m)
        Input cost  ‖u‖²_R.
    P : cvxopt.matrix (l, l), optional
        Terminal output tracking cost.  Default: Q.
    S : cvxopt.matrix (m, m), optional
        Input rate-of-movement cost  ‖Δu‖²_S.  None → disabled.
    rho : float
        Penalty weight on soft output constraint violation.  Default: 1e4.
    y_offset : float
        Symmetric offset δ around the setpoint for soft output constraints:
        ``r − δ ≤ y ≤ r + δ``.  Default: 2.0.
    """

    def __init__(
        self,
        model: "LinearDiscreteModel",
        N: int,
        Q: matrix,
        R: matrix,
        P: matrix | None = None,
        S: matrix | None = None,
        rho: float = 1e4,
        y_offset: float = 2.0,
    ) -> None:
        self._model = model
        self._N = N
        self._Q = Q
        self._R = R
        self._P = P if P is not None else matrix(Q)
        self._S = S
        self._rho = rho
        self._y_offset = y_offset

        n_u = model.nu
        # Pre-compute constant structures
        self._D_diff: matrix | None = None
        self._S_bar: matrix | None = None
        if S is not None:
            self._D_diff = _build_D_diff(n_u, N)
            self._S_bar = _block_diag(S, N)

    def solve(
        self,
        x0: matrix,
        D: matrix,
        x_ref: matrix,
        u_prev: matrix | None = None,
    ) -> tuple[matrix, matrix]:
        """
        Solve the OCP starting from x0.

        Parameters
        ----------
        x0    : (n, 1) current state estimate.
        D     : (N·p, 1) stacked disturbance forecast  [d[0]; d[1]; …; d[N−1]].
        x_ref : (n, 1) setpoint / reference (constant over horizon).
        u_prev : (m, 1) previously applied input (for Δu penalty).

        Returns
        -------
        U : (N·m, 1) optimal input sequence.
        X : (N·n, 1) predicted state trajectory x[1], …, x[N].
        """
        N = self._N
        n_x = self._model.nx
        n_u = self._model.nu
        n_d = self._model.nd
        C = _np_to_cvx(self._model.Cm)  # convert numpy Cm to cvxopt
        l = C.size[0]  # output dimension

        # ── Convert numpy inputs to cvxopt if needed ────────────────────
        import numpy as _np
        if isinstance(x0, _np.ndarray):
            x0 = _np_to_cvx(x0.reshape(-1, 1))
        if isinstance(x_ref, _np.ndarray):
            x_ref = _np_to_cvx(x_ref.reshape(-1, 1))

        # ── Constant discrete-time matrices (no LPV) ────────────────────
        A = _np_to_cvx(self._model.Ad)
        B = _np_to_cvx(self._model.Bd)
        E = _np_to_cvx(self._model.Ed)

        # ── Powers of A ──────────────────────────────────────────────────
        A_pow = [_eye(n_x)]
        for _ in range(N):
            A_pow.append(A * A_pow[-1])

        # ── Prediction matrices ──────────────────────────────────────────
        #   X = Ψ x₀ + Γ U + Λ D
        Psi = _zeros(N * n_x, n_x)
        Gamma = _zeros(N * n_x, N * n_u)
        Lambda = _zeros(N * n_x, N * n_d)

        for k in range(N):
            # Ψ block-row k:  A^{k+1}
            for i in range(n_x):
                for j in range(n_x):
                    Psi[k * n_x + i, j] = A_pow[k + 1][i, j]

            # Γ and Λ block-row k
            for j_step in range(k + 1):
                Ak_j = A_pow[k - j_step]
                AB = Ak_j * B
                AE = Ak_j * E
                for i in range(n_x):
                    for jj in range(n_u):
                        Gamma[k * n_x + i, j_step * n_u + jj] = AB[i, jj]
                    for jj in range(n_d):
                        Lambda[k * n_x + i, j_step * n_d + jj] = AE[i, jj]

        # ── Output prediction matrices ───────────────────────────────────
        #   Y = C̄ X  where C̄ = blkdiag(C, …, C)
        C_bar = _block_diag(C, N)
        CG = C_bar * Gamma        # (N·l) × (N·m)
        CP = C_bar * Psi          # (N·l) × n_x
        CL = C_bar * Lambda       # (N·l) × (N·p)

        # ── Cost matrices ────────────────────────────────────────────────
        Q_bar = _block_diag_terminal(self._Q, self._P, N)   # (N·l) × (N·l)
        R_bar = _block_diag(self._R, N)                      # (N·m) × (N·m)

        # Reference and free response
        r_bar = _tile_column(C * x_ref, N)                   # (N·l, 1)
        Y_free = CP * x0 + CL * D                            # (N·l, 1)
        e_free = Y_free - r_bar                              # (N·l, 1)

        # Hessian and gradient for U-part
        H_uu = CG.T * Q_bar * CG + R_bar                    # (N·m) × (N·m)
        f_u = CG.T * Q_bar * e_free                          # (N·m, 1)

        # Rate-of-movement penalty
        if self._S is not None:
            if u_prev is None:
                u_prev = _zeros(n_u, 1)
            d0_shift = _zeros(N * n_u, 1)
            d0_shift[:n_u] = -u_prev
            H_uu += self._D_diff.T * self._S_bar * self._D_diff
            f_u += self._D_diff.T * self._S_bar * d0_shift

        # ── Soft output constraint slack variables ε ─────────────────────
        #   Decision variable z = [U; ε],  ε ∈ ℝ^{N·l}
        n_U = N * n_u
        n_eps = N * l
        n_z = n_U + n_eps

        # Full Hessian  H = [ H_uu   0  ]
        #                    [  0    ρI  ]
        H = _zeros(n_z, n_z)
        for i in range(n_U):
            for j in range(n_U):
                H[i, j] = H_uu[i, j]
        for i in range(n_eps):
            H[n_U + i, n_U + i] = self._rho

        # Full gradient  f = [ f_u; 0 ]
        f = _zeros(n_z, 1)
        for i in range(n_U):
            f[i] = f_u[i]

        # ── Inequality constraints  G z ≤ h ──────────────────────────────
        #
        # 1) Hard input box:      u_min ≤ u[k] ≤ u_max
        #    →  -u[k] ≤ -u_min    and    u[k] ≤ u_max
        #
        # 2) Soft output box:     y[k+1] ≥ y_min − ε[k+1]   →  -C̄ Γ U + ε ≤ -(y_min − C̄(Ψ x₀ + Λ D))
        #                         y[k+1] ≤ y_max + ε[k+1]   →   C̄ Γ U − ε ≤   y_max − C̄(Ψ x₀ + Λ D)
        #
        # 3) Slack non-negativity:  ε ≥ 0   →  -ε ≤ 0

        u_min_np, u_max_np = self._model.u_bounds
        u_min = _np_to_cvx(u_min_np.reshape(-1, 1))
        u_max = _np_to_cvx(u_max_np.reshape(-1, 1))
        u_min_tiled = _tile_column(u_min, N)   # (N·m, 1)
        u_max_tiled = _tile_column(u_max, N)   # (N·m, 1)

        # Output bounds
        y_min = C * x_ref - matrix(self._y_offset, (l, 1))
        y_max = C * x_ref + matrix(self._y_offset, (l, 1))
        y_min_tiled = _tile_column(y_min, N)   # (N·l, 1)
        y_max_tiled = _tile_column(y_max, N)   # (N·l, 1)

        # Number of inequality rows
        n_ineq = 2 * n_U + 2 * n_eps + n_eps

        G = _zeros(n_ineq, n_z)
        h = _zeros(n_ineq, 1)

        row = 0

        # (1a)  -U ≤ -u_min   →   -I_U  U  + 0 ε  ≤  -u_min
        for i in range(n_U):
            G[row + i, i] = -1.0
            h[row + i] = -u_min_tiled[i]
        row += n_U

        # (1b)   U ≤ u_max    →    I_U  U  + 0 ε  ≤   u_max
        for i in range(n_U):
            G[row + i, i] = 1.0
            h[row + i] = u_max_tiled[i]
        row += n_U

        # (2a) -C̄ Γ U − ε ≤ -(y_min − Y_free)   i.e.  lower output bound
        #      -C̄ Γ U − ε ≤ -y_min + Y_free
        for i in range(n_eps):
            for j in range(n_U):
                G[row + i, j] = -CG[i, j]
            G[row + i, n_U + i] = -1.0
            h[row + i] = -y_min_tiled[i] + Y_free[i]
        row += n_eps

        # (2b)  C̄ Γ U − ε ≤ y_max − Y_free       i.e.  upper output bound
        for i in range(n_eps):
            for j in range(n_U):
                G[row + i, j] = CG[i, j]
            G[row + i, n_U + i] = -1.0
            h[row + i] = y_max_tiled[i] - Y_free[i]
        row += n_eps

        # (3) -ε ≤ 0  (slack non-negativity)
        for i in range(n_eps):
            G[row + i, n_U + i] = -1.0
            h[row + i] = 0.0
        row += n_eps

        # ── Solve QP with cvxopt ─────────────────────────────────────────
        sol = solvers.qp(H, f, G, h)

        if sol["status"] != "optimal":
            # Fallback: zeros (no input)
            U_flat = _zeros(n_U, 1)
        else:
            z_opt = sol["x"]
            U_flat = z_opt[:n_U]

        # ── Predicted trajectory ─────────────────────────────────────────
        X_flat = Psi * x0 + Gamma * U_flat + Lambda * D

        return U_flat, X_flat
