"""Time-varying / forecast-aware condensed QP solve for StandardLinearDiscreteOCP."""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

import numpy as np
from scipy.linalg import block_diag

from .qp_solver import QPProblem

if TYPE_CHECKING:
    from .ocp import StandardLinearDiscreteOCP


def solve_forecast_qp(
    ocp: StandardLinearDiscreteOCP,
    x0,
    D,
    x_ref,
    u_prev=None,
    *,
    u_ss=None,
    x_ref_dev_seq=None,
    offset_seq=None,
    q_scale_seq=None,
    r_scale_seq=None,
    u_min_seq=None,
    u_max_seq=None,
    price_seq=None,
    elec_heat=None,
    elec_cool=None,
    bid_mask=None,
    price_weight: float = 0.0,
    dt_h: float = 0.25,
) -> tuple[np.ndarray, np.ndarray]:
    has_price = (
        price_seq is not None
        and price_weight > 0.0
        and elec_heat is not None
    )

    N = ocp._N
    nx = ocp._model.nx
    nu = ocp._model.nu
    nd = ocp._model.nd
    Cz = np.asarray(ocp._model.Cz, dtype=float)
    nz = Cz.shape[0]

    # Coerce inputs to numpy 1D
    x0 = np.asarray(x0, dtype=float).reshape(-1)
    x_ref = np.asarray(x_ref, dtype=float).reshape(-1)
    D = np.asarray(D, dtype=float).reshape(-1) if D is not None else np.zeros(N * nd)
    if u_prev is not None:
        u_prev = np.asarray(u_prev, dtype=float).reshape(-1)

    Ad = np.asarray(ocp._model.Ad, dtype=float)
    Bd = np.asarray(ocp._model.Bd, dtype=float)
    Ed = np.asarray(ocp._model.Ed, dtype=float)

    # State-prediction matrices  X = Ψ x₀ + Γ U + Λ D
    Ad_pow = [np.eye(nx)]
    for _ in range(N):
        Ad_pow.append(Ad @ Ad_pow[-1])

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
    CG = Cz_bar @ Gamma    # (N·nz) × (N·nu)
    CP = Cz_bar @ Psi      # (N·nz) × nx
    CL = Cz_bar @ Lambda   # (N·nz) × (N·nd)

    z_ref_np = Cz @ x_ref  # (nz,) baseline output reference

    # ── Time-varying cost matrices ────────────────────────────────────
    # Build Q_bar, R_bar, z_ref_bar, z_min_tiled, z_max_tiled for the QP.
    # When no time-varying parameters are provided these collapse to the
    # same result as the original static helpers, preserving correctness
    # and backward compatibility.
    has_varying = (
        x_ref_dev_seq is not None
        or offset_seq is not None
        or q_scale_seq is not None
        or r_scale_seq is not None
    )

    if has_varying:
        # Q_bar: block-diagonal with per-step, per-output Q weights.
        # Terminal step uses P = terminal_weight × Q_base per element.
        Q_bar = np.zeros((N * nz, N * nz))
        for k in range(N):
            base_diag = ocp._P if k == N - 1 else ocp._Q
            for i in range(nz):
                val = base_diag[i, i]
                if q_scale_seq is not None:
                    val *= float(q_scale_seq[k, i])
                Q_bar[k * nz + i, k * nz + i] = val

        # R_bar: block-diagonal with per-step, per-source R weights.
        R_bar = np.zeros((N * nu, N * nu))
        for k in range(N):
            for i in range(nu):
                val = ocp._R[i, i]
                if r_scale_seq is not None:
                    val *= float(r_scale_seq[k, i])
                R_bar[k * nu + i, k * nu + i] = val

        # z_ref_bar: stacked per-step reference in deviation coordinates.
        if x_ref_dev_seq is not None:
            z_ref_bar = np.asarray(x_ref_dev_seq, dtype=float).reshape(-1)
        else:
            z_ref_bar = np.tile(z_ref_np, N)
    else:
        Q_bar = block_diag(*([ocp._Q] * (N - 1) + [ocp._P])) if N > 1 else ocp._P.copy()
        R_bar = block_diag(*([ocp._R] * N))
        z_ref_bar = np.tile(z_ref_np, N)

    Z_free = CP @ x0 + CL @ D
    e_free = Z_free - z_ref_bar

    H_uu = CG.T @ Q_bar @ CG + R_bar
    f_u = CG.T @ Q_bar @ e_free

    u_ss_vec = (
        np.asarray(u_ss, dtype=float).reshape(-1)
        if u_ss is not None
        else np.zeros(nu, dtype=float)
    )
    u_ss_bar = np.tile(u_ss_vec, N)

    # Price-aware solves optimise u_abs and charge real electricity on the
    # absolute draw (u_abs = 0 ⇒ zero electrical cost).  Penalising u_dev
    # instead would treat u_dev = 0 as free even though u_abs = u_ss.
    use_abs_inputs = has_price

    if use_abs_inputs:
        cg_shift = CG @ u_ss_bar
        e_free_abs = e_free - cg_shift
        f_u = CG.T @ Q_bar @ e_free_abs
    elif u_ss is not None and not np.allclose(u_ss, 0.0):
        # Linear correction: penalise ‖u_dev + u_ss‖²_R instead of ‖u_dev‖²_R.
        for k in range(N):
            for i in range(nu):
                r_val = ocp._R[i, i]
                if r_scale_seq is not None:
                    r_val *= float(r_scale_seq[k, i])
                f_u[k * nu + i] += r_val * u_ss_vec[i]

    if ocp._S is not None:
        if u_prev is None:
            u_prev_eff = u_ss_vec if use_abs_inputs else np.zeros(nu)
        else:
            u_prev_arr = np.asarray(u_prev, dtype=float).reshape(-1)
            u_prev_eff = (
                u_prev_arr + u_ss_vec if use_abs_inputs else u_prev_arr
            )
        d0_shift = np.zeros(N * nu)
        d0_shift[:nu] = -u_prev_eff
        H_uu = H_uu + ocp._D_diff.T @ ocp._S_bar @ ocp._D_diff
        f_u = f_u + ocp._D_diff.T @ ocp._S_bar @ d0_shift

    # Full QP decision variable.  Price-aware solves use absolute inputs
    # (u_abs) in the control block; comfort stays on the soft-slack path.
    n_U = N * nu
    n_eps = N * nz
    n_Z = n_U + n_eps

    H = np.zeros((n_Z, n_Z))
    H[:n_U, :n_U] = H_uu
    np.fill_diagonal(H[n_U:, n_U:], ocp._rho)
    H = 0.5 * (H + H.T)

    f = np.zeros(n_Z)
    f[:n_U] = f_u
    if ocp._rho_lin > 0.0:
        f[n_U:] = ocp._rho_lin

    # Input box bounds.  Deviation bounds are the model default; price-aware
    # solves use absolute actuator fractions directly.
    u_min_np, u_max_np = ocp._model.u_bounds
    if u_min_seq is not None or u_max_seq is not None:
        if use_abs_inputs:
            if u_min_seq is not None:
                u_min_tiled = np.asarray(u_min_seq, dtype=float).reshape(-1)
            else:
                u_min_tiled = np.tile((u_min_np + u_ss_vec).reshape(-1), N)
            if u_max_seq is not None:
                u_max_tiled = np.asarray(u_max_seq, dtype=float).reshape(-1)
            else:
                u_max_tiled = np.tile((u_max_np + u_ss_vec).reshape(-1), N)
        else:
            u_ss_row = np.asarray(ocp._model.u_ss, dtype=float).reshape(1, -1)
            if u_min_seq is not None:
                u_min_tiled = (np.asarray(u_min_seq, dtype=float) - u_ss_row).reshape(-1)
            else:
                u_min_tiled = np.tile(u_min_np.reshape(-1), N)
            if u_max_seq is not None:
                u_max_tiled = (np.asarray(u_max_seq, dtype=float) - u_ss_row).reshape(-1)
            else:
                u_max_tiled = np.tile(u_max_np.reshape(-1), N)
    else:
        if use_abs_inputs:
            u_min_tiled = np.tile((u_min_np + u_ss_vec).reshape(-1), N)
            u_max_tiled = np.tile((u_max_np + u_ss_vec).reshape(-1), N)
        else:
            u_min_tiled = np.tile(u_min_np.reshape(-1), N)
            u_max_tiled = np.tile(u_max_np.reshape(-1), N)

    # Soft output corridor bounds: time-varying when offset_seq/x_ref_dev_seq given.
    if has_varying:
        z_min_parts: list[np.ndarray] = []
        z_max_parts: list[np.ndarray] = []
        for k in range(N):
            z_ref_k = (
                np.asarray(x_ref_dev_seq[k], dtype=float)
                if x_ref_dev_seq is not None
                else z_ref_np
            )
            off_k = (
                np.asarray(offset_seq[k], dtype=float)
                if offset_seq is not None
                else np.full(nz, ocp._y_offset, dtype=float)
            )
            z_min_parts.append(z_ref_k - off_k)
            z_max_parts.append(z_ref_k + off_k)
        z_min_tiled = np.concatenate(z_min_parts)
        z_max_tiled = np.concatenate(z_max_parts)
    else:
        z_min_tiled = np.tile(z_ref_np - ocp._y_offset, N)
        z_max_tiled = np.tile(z_ref_np + ocp._y_offset, N)

    neg_I_eps = -np.eye(n_eps)
    G_out = np.hstack([
        np.vstack([-CG, CG]),
        np.vstack([neg_I_eps, neg_I_eps]),
    ])
    if use_abs_inputs:
        # Z = Z_free + CG·(U_abs − u_ss); shift the RHS accordingly.
        cg_shift = CG @ u_ss_bar
        h_out = np.concatenate([
            -z_min_tiled + Z_free - cg_shift,
            z_max_tiled - Z_free + cg_shift,
        ])
    else:
        h_out = np.concatenate([-z_min_tiled + Z_free, z_max_tiled - Z_free])

    lb = np.concatenate([u_min_tiled, np.zeros(n_eps)])
    ub = np.concatenate([u_max_tiled, np.full(n_eps, np.inf)])

    # ── Price-aware linear cost on absolute electrical draw ─────────────
    # Minimise α·πₖ·Pᵉˡᵉᶜ·Δt·u_abs[k] (kW × h × €/kWh).  u_abs = 0 is zero
    # cost; charging u_dev would incorrectly treat u_dev = 0 (u_abs = u_ss)
    # as free electricity.
    if has_price:
        price_arr = np.maximum(np.asarray(price_seq, dtype=float), 0.0)
        n_price = len(price_arr)

        bid_list: list[int] = []
        if bid_mask is not None:
            bid_list = [i for i in range(nu) if bid_mask[i]]
        bid_set = set(bid_list)
        n_bid = len(bid_list)

        # Non-bidirectional sources (pure heating-only or pure cooling-only):
        # price the absolute draw directly on the input variable (with sign change
        # for cooling-only so that more negative u increases the cost).
        # Bidirectional sources must *not* receive a direct price term on their u;
        # their electricity cost is applied exclusively via the slacks below.
        for k in range(N):
            p_k = float(price_arr[min(k, n_price - 1)])
            for i in range(nu):
                if i not in bid_set:
                    # Recover physical limits to decide direction (u bounds here are
                    # deviation; add u_ss to obtain physical actuator range).
                    phys_u_max_i = float(u_max_np[i] + u_ss_vec[i])
                    if phys_u_max_i > 0.0:
                        # Positive commands (heating) consume elec_heat per +u
                        c = float(elec_heat[i]) * 1e-3
                        sign = +1.0
                    else:
                        # Negative commands (cooling) consume elec_cool per |u|
                        # To charge cost when u becomes more negative, use negative gradient.
                        c = (float(elec_cool[i]) if elec_cool is not None
                             else float(elec_heat[i])) * 1e-3
                        sign = -1.0
                    f[k * nu + i] += price_weight * p_k * (sign * c) * dt_h

        if n_bid == 0:
            result = ocp._backend.solve(
                QPProblem(P=H, q=f, lb=lb, ub=ub, G=G_out, h=h_out)
            )
        else:
            # Bidirectional (heat+cool) sources: price is applied *exclusively* to the
            # slack variables s⁺ (heating draw) and s⁻ (cooling draw).  Their u variable
            # in the U block receives *no* direct electricity price term (the loop above
            # skipped them).  The equality u = s⁺ − s⁻ makes the effective cost
            # π · (c_h s⁺ + c_c s⁻) = π · c · |u| for the active direction.
            # Pure uni sources were handled above and must not appear here.
            n_S = N * n_bid
            n_Z_aug = n_U + n_eps + 2 * n_S

            H_aug = np.zeros((n_Z_aug, n_Z_aug))
            H_aug[:n_Z, :n_Z] = H

            f_aug = np.zeros(n_Z_aug)
            f_aug[:n_Z] = f
            for k in range(N):
                p_k = float(price_arr[min(k, n_price - 1)])
                for jj, src_i in enumerate(bid_list):
                    c_h = float(elec_heat[src_i]) * 1e-3
                    c_c = (float(elec_cool[src_i]) if elec_cool is not None
                           else float(elec_heat[src_i])) * 1e-3
                    sp = n_U + n_eps + k * n_bid + jj
                    sm = n_U + n_eps + n_S + k * n_bid + jj
                    f_aug[sp] += price_weight * p_k * c_h * dt_h
                    f_aug[sm] += price_weight * p_k * c_c * dt_h

            G_aug = np.zeros((2 * n_eps, n_Z_aug))
            G_aug[:, :n_Z] = G_out

            # Bounds: U, ε from before; s⁺/s⁻ ∈ [0, u_max/|u_min|]
            s_plus_ub = np.zeros(n_S)
            s_minus_ub = np.zeros(n_S)
            for k in range(N):
                for jj, src_i in enumerate(bid_list):
                    u_max_i = float(u_max_np[src_i] + u_ss_vec[src_i])
                    u_min_i = float(u_min_np[src_i] + u_ss_vec[src_i])
                    s_plus_ub[k * n_bid + jj] = u_max_i
                    s_minus_ub[k * n_bid + jj] = abs(u_min_i)
            lb_aug = np.concatenate([lb, np.zeros(2 * n_S)])
            ub_aug = np.concatenate([ub, s_plus_ub, s_minus_ub])

            # Equality: u_abs[k,i] − s⁺[k,j] + s⁻[k,j] = 0
            n_eq = N * n_bid
            A_eq = np.zeros((n_eq, n_Z_aug))
            b_eq = np.zeros(n_eq)
            for k in range(N):
                for jj, src_i in enumerate(bid_list):
                    row_eq = k * n_bid + jj
                    A_eq[row_eq, k * nu + src_i] = 1.0
                    A_eq[row_eq, n_U + n_eps + k * n_bid + jj] = -1.0
                    A_eq[row_eq, n_U + n_eps + n_S + k * n_bid + jj] = 1.0

            result = ocp._backend.solve(
                QPProblem(P=H_aug, q=f_aug, lb=lb_aug, ub=ub_aug,
                          G=G_aug, h=h_out, A=A_eq, b=b_eq)
            )
    else:
        result = ocp._backend.solve(
            QPProblem(P=H, q=f, lb=lb, ub=ub, G=G_out, h=h_out)
        )

    if not result.success:
        warnings.warn(
            f"StandardLinearDiscreteOCP.solve: QP solver returned status "
            f"'{result.status}'; returning zero inputs as fallback.",
            RuntimeWarning,
            stacklevel=2,
        )
        U_flat = np.zeros(n_U)
    else:
        U_abs_flat = np.asarray(result.x[:n_U], dtype=float)
        if use_abs_inputs:
            U_flat = U_abs_flat - u_ss_bar
        else:
            U_flat = U_abs_flat

    U_dev_flat = U_flat
    X_flat = Psi @ x0 + Gamma @ U_dev_flat + Lambda @ D
    return U_flat, X_flat
