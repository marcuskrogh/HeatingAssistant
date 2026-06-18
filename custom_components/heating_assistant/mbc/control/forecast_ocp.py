"""Time-varying horizon-profile condensed QP solve for StandardLinearDiscreteOCP."""

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
    input_equilibrium=None,
    output_reference_deviation_profile=None,
    soft_output_band_half_width_profile=None,
    output_tracking_weight_scale_profile=None,
    input_regularisation_weight_scale_profile=None,
    input_lower_bound_profile=None,
    input_upper_bound_profile=None,
    input_linear_cost_coefficient_profile=None,
    input_linear_cost_slack_input_indices=None,
    input_linear_cost_positive_slack_coefficient_profile=None,
    input_linear_cost_negative_slack_coefficient_profile=None,
) -> tuple[np.ndarray, np.ndarray]:
    has_input_linear_cost = (
        input_linear_cost_coefficient_profile is not None
        or input_linear_cost_positive_slack_coefficient_profile is not None
        or input_linear_cost_negative_slack_coefficient_profile is not None
    )

    N = ocp._N
    nx = ocp._model.nx
    nu = ocp._model.nu
    nd = ocp._model.nd
    Cz = np.asarray(ocp._model.Cz, dtype=float)
    nz = Cz.shape[0]

    x0 = np.asarray(x0, dtype=float).reshape(-1)
    x_ref = np.asarray(x_ref, dtype=float).reshape(-1)
    D = np.asarray(D, dtype=float).reshape(-1) if D is not None else np.zeros(N * nd)
    if u_prev is not None:
        u_prev = np.asarray(u_prev, dtype=float).reshape(-1)

    Ad = np.asarray(ocp._model.Ad, dtype=float)
    Bd = np.asarray(ocp._model.Bd, dtype=float)
    Ed = np.asarray(ocp._model.Ed, dtype=float)

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
    CG = Cz_bar @ Gamma
    CP = Cz_bar @ Psi
    CL = Cz_bar @ Lambda

    z_ref_np = Cz @ x_ref

    has_varying = (
        output_reference_deviation_profile is not None
        or soft_output_band_half_width_profile is not None
        or output_tracking_weight_scale_profile is not None
        or input_regularisation_weight_scale_profile is not None
    )

    if has_varying:
        Q_bar = np.zeros((N * nz, N * nz))
        for k in range(N):
            base_diag = ocp._P if k == N - 1 else ocp._Q
            for i in range(nz):
                val = base_diag[i, i]
                if output_tracking_weight_scale_profile is not None:
                    val *= float(output_tracking_weight_scale_profile[k, i])
                Q_bar[k * nz + i, k * nz + i] = val

        R_bar = np.zeros((N * nu, N * nu))
        for k in range(N):
            for i in range(nu):
                val = ocp._R[i, i]
                if input_regularisation_weight_scale_profile is not None:
                    val *= float(input_regularisation_weight_scale_profile[k, i])
                R_bar[k * nu + i, k * nu + i] = val

        if output_reference_deviation_profile is not None:
            z_ref_bar = np.asarray(
                output_reference_deviation_profile, dtype=float,
            ).reshape(-1)
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
        np.asarray(input_equilibrium, dtype=float).reshape(-1)
        if input_equilibrium is not None
        else np.zeros(nu, dtype=float)
    )
    u_ss_bar = np.tile(u_ss_vec, N)

    use_absolute_inputs = has_input_linear_cost

    if use_absolute_inputs:
        cg_shift = CG @ u_ss_bar
        e_free_abs = e_free - cg_shift
        f_u = CG.T @ Q_bar @ e_free_abs
    elif input_equilibrium is not None and not np.allclose(u_ss_vec, 0.0):
        for k in range(N):
            for i in range(nu):
                r_val = ocp._R[i, i]
                if input_regularisation_weight_scale_profile is not None:
                    r_val *= float(input_regularisation_weight_scale_profile[k, i])
                f_u[k * nu + i] += r_val * u_ss_vec[i]

    if ocp._S is not None:
        if u_prev is None:
            u_prev_eff = u_ss_vec if use_absolute_inputs else np.zeros(nu)
        else:
            u_prev_arr = np.asarray(u_prev, dtype=float).reshape(-1)
            u_prev_eff = (
                u_prev_arr + u_ss_vec if use_absolute_inputs else u_prev_arr
            )
        d0_shift = np.zeros(N * nu)
        d0_shift[:nu] = -u_prev_eff
        H_uu = H_uu + ocp._D_diff.T @ ocp._S_bar @ ocp._D_diff
        f_u = f_u + ocp._D_diff.T @ ocp._S_bar @ d0_shift

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

    u_min_np, u_max_np = ocp._model.u_bounds
    if input_lower_bound_profile is not None or input_upper_bound_profile is not None:
        if use_absolute_inputs:
            if input_lower_bound_profile is not None:
                u_min_tiled = np.asarray(input_lower_bound_profile, dtype=float).reshape(-1)
            else:
                u_min_tiled = np.tile((u_min_np + u_ss_vec).reshape(-1), N)
            if input_upper_bound_profile is not None:
                u_max_tiled = np.asarray(input_upper_bound_profile, dtype=float).reshape(-1)
            else:
                u_max_tiled = np.tile((u_max_np + u_ss_vec).reshape(-1), N)
        else:
            u_ss_row = np.asarray(ocp._model.u_ss, dtype=float).reshape(1, -1)
            if input_lower_bound_profile is not None:
                u_min_tiled = (
                    np.asarray(input_lower_bound_profile, dtype=float) - u_ss_row
                ).reshape(-1)
            else:
                u_min_tiled = np.tile(u_min_np.reshape(-1), N)
            if input_upper_bound_profile is not None:
                u_max_tiled = (
                    np.asarray(input_upper_bound_profile, dtype=float) - u_ss_row
                ).reshape(-1)
            else:
                u_max_tiled = np.tile(u_max_np.reshape(-1), N)
    else:
        if use_absolute_inputs:
            u_min_tiled = np.tile((u_min_np + u_ss_vec).reshape(-1), N)
            u_max_tiled = np.tile((u_max_np + u_ss_vec).reshape(-1), N)
        else:
            u_min_tiled = np.tile(u_min_np.reshape(-1), N)
            u_max_tiled = np.tile(u_max_np.reshape(-1), N)

    if has_varying:
        z_min_parts: list[np.ndarray] = []
        z_max_parts: list[np.ndarray] = []
        for k in range(N):
            z_ref_k = (
                np.asarray(output_reference_deviation_profile[k], dtype=float)
                if output_reference_deviation_profile is not None
                else z_ref_np
            )
            off_k = (
                np.asarray(soft_output_band_half_width_profile[k], dtype=float)
                if soft_output_band_half_width_profile is not None
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
    if use_absolute_inputs:
        cg_shift = CG @ u_ss_bar
        h_out = np.concatenate([
            -z_min_tiled + Z_free - cg_shift,
            z_max_tiled - Z_free + cg_shift,
        ])
    else:
        h_out = np.concatenate([-z_min_tiled + Z_free, z_max_tiled - Z_free])

    lb = np.concatenate([u_min_tiled, np.zeros(n_eps)])
    ub = np.concatenate([u_max_tiled, np.full(n_eps, np.inf)])

    if input_linear_cost_coefficient_profile is not None:
        coeff = np.asarray(input_linear_cost_coefficient_profile, dtype=float).reshape(N, nu)
        for k in range(N):
            for i in range(nu):
                f[k * nu + i] += coeff[k, i]

    slack_list: list[int] = []
    if input_linear_cost_slack_input_indices is not None:
        slack_list = [
            int(i) for i in np.asarray(input_linear_cost_slack_input_indices, dtype=int).reshape(-1)
        ]
    n_slack = len(slack_list)
    slack_set = set(slack_list)

    if has_input_linear_cost and n_slack > 0:
        pos_profile = (
            None if input_linear_cost_positive_slack_coefficient_profile is None
            else np.asarray(input_linear_cost_positive_slack_coefficient_profile, dtype=float)
            .reshape(N, n_slack)
        )
        neg_profile = (
            None if input_linear_cost_negative_slack_coefficient_profile is None
            else np.asarray(input_linear_cost_negative_slack_coefficient_profile, dtype=float)
            .reshape(N, n_slack)
        )

        n_S = N * n_slack
        n_Z_aug = n_U + n_eps + 2 * n_S

        H_aug = np.zeros((n_Z_aug, n_Z_aug))
        H_aug[:n_Z, :n_Z] = H

        f_aug = np.zeros(n_Z_aug)
        f_aug[:n_Z] = f
        for k in range(N):
            for jj, src_i in enumerate(slack_list):
                if pos_profile is not None:
                    sp = n_U + n_eps + k * n_slack + jj
                    f_aug[sp] += float(pos_profile[k, jj])
                if neg_profile is not None:
                    sm = n_U + n_eps + n_S + k * n_slack + jj
                    f_aug[sm] += float(neg_profile[k, jj])

        G_aug = np.zeros((2 * n_eps, n_Z_aug))
        G_aug[:, :n_Z] = G_out

        s_plus_ub = np.zeros(n_S)
        s_minus_ub = np.zeros(n_S)
        for k in range(N):
            for jj, src_i in enumerate(slack_list):
                u_max_i = float(u_max_np[src_i] + u_ss_vec[src_i])
                u_min_i = float(u_min_np[src_i] + u_ss_vec[src_i])
                s_plus_ub[k * n_slack + jj] = u_max_i
                s_minus_ub[k * n_slack + jj] = abs(u_min_i)
        lb_aug = np.concatenate([lb, np.zeros(2 * n_S)])
        ub_aug = np.concatenate([ub, s_plus_ub, s_minus_ub])

        n_eq = N * n_slack
        A_eq = np.zeros((n_eq, n_Z_aug))
        b_eq = np.zeros(n_eq)
        for k in range(N):
            for jj, src_i in enumerate(slack_list):
                row_eq = k * n_slack + jj
                A_eq[row_eq, k * nu + src_i] = 1.0
                A_eq[row_eq, n_U + n_eps + k * n_slack + jj] = -1.0
                A_eq[row_eq, n_U + n_eps + n_S + k * n_slack + jj] = 1.0

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
        if use_absolute_inputs:
            U_flat = U_abs_flat - u_ss_bar
        else:
            U_flat = U_abs_flat

    U_dev_flat = U_flat
    X_flat = Psi @ x0 + Gamma @ U_dev_flat + Lambda @ D
    return U_flat, X_flat
