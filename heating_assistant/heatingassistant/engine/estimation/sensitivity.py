"""Forward-sensitivity and gradient helpers for grey-box parameter estimation."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..controller import HouseThermalSDE as HouseThermalSystem
from .constants import _EMPTY_IDX, _T_WALL_HI, _T_WALL_LO
from .model_build import _build_parametric_system, _theta_model_quantities
from .theta_layout import _ThetaLayout


def _dfdtheta_step(
    est: Any,
    q: Dict[str, np.ndarray],
    layout: _ThetaLayout,
    model: HouseThermalSystem,
    f_val: np.ndarray,
    x: np.ndarray,
    u_k: np.ndarray,
    d_k: np.ndarray,
    ntheta: int,
    nx: int,
) -> np.ndarray:
    """``∂f/∂θ`` at fixed state, shape (ntheta, nx), for the 2R2C drift.

    Only the physical rows (air 0…n−1, wall n…2n−1) are non-zero; the
    emitter-filter drift carries no θ-dependence.  Two parameter
    families use the exact whole-row scaling shortcut (every term of a
    room's drift row is proportional to 1/C):

    * ``log_mass_i`` scales both of room i's rows by −1, and
    * ``c_air_i`` scales the air row by −1/fc and the wall row by
      +1/(1−fc).

    The remaining families are written out from the conductance
    structure.  The wind overlay is inactive during estimation, and
    sky/bridge conductances (default 0) are treated as
    r-independent.
    """
    n = est._n
    T_a = x[:n]
    T_w = x[n: 2 * n]
    T_out = float(d_k[0])
    d_sol = np.array([
        float(d_k[1 + i]) if 1 + i < len(d_k) else 0.0 for i in range(n)
    ])
    C_a, C_w = q["C_a"], q["C_w"]
    g_inf, g_aw, g_we = q["g_inf"], q["g_aw"], q["g_we"]
    fc, rf, s = q["fc"], q["rf"], q["s"]
    w = q["wall_frac"]
    facade = q["facade"]

    D = np.zeros((ntheta, nx))
    j_idx = np.arange(n)

    # log_mass: rows scale with 1/C_tot → −f per row.
    D[j_idx, j_idx] = -f_val[:n]
    D[j_idx, n + j_idx] = -f_val[n: 2 * n]

    # log_r: all three conductances scale with 1/r_ext.
    D[n + j_idx, j_idx] = -(
        (g_aw / C_a) * (T_w - T_a) + (g_inf / C_a) * (T_out - T_a)
    )
    D[n + j_idx, n + j_idx] = -(
        (g_aw / C_w) * (T_a - T_w) + (g_we / C_w) * (T_out - T_w)
    )

    # q_int: direct heat on the air node.
    D[2 * n + j_idx, j_idx] = 1.0 / C_a

    # Heater scales α (air node).
    for k_la, s_idx in enumerate(layout.identifiable_sources):
        src = est._sources[s_idx]
        i_src = model._room_idx[src.room]
        u_s = float(u_k[s_idx]) if s_idx < len(u_k) else 0.0
        u_scaled_s = q["heater_scales"][s_idx] * u_s
        a0, _ = layout.idx_log_alpha
        D[a0 + k_la, i_src] = (
            src.thermal_power(max(0.0, u_scaled_s), T_out) / C_a[i_src]
        )

    # Inter-room resistances (wall-to-wall).
    r0, _ = layout.idx_log_r_ij
    for k_rij, (pi, pj) in enumerate(layout.identifiable_pairs):
        g_ij = float(q["g_ij"][k_rij])
        D[r0 + k_rij, n + pi] = (g_ij / C_w[pi]) * (T_w[pi] - T_w[pj])
        D[r0 + k_rij, n + pj] = (g_ij / C_w[pj]) * (T_w[pj] - T_w[pi])

    # Solar scales (split between air and wall like G_d).
    s0, _ = layout.idx_log_solar
    for k_s, i in enumerate(layout.identifiable_solar):
        D[s0 + k_s, i] = (1.0 - w) * s[i] * d_sol[i] / C_a[i]
        D[s0 + k_s, n + i] = (w + facade[i]) * s[i] * d_sol[i] / C_w[i]

    # Envelope splits.
    ca0, _ = layout.idx_c_air
    ra0, _ = layout.idx_r_aw
    for k_sp, i in enumerate(layout.identifiable_splits):
        # c_air: air row ∝ 1/fc, wall row ∝ 1/(1−fc).
        D[ca0 + k_sp, i] = -f_val[i] / fc[i]
        D[ca0 + k_sp, n + i] = f_val[n + i] / (1.0 - fc[i])
        # r_aw: ∂g_aw/∂rf = −g_aw/rf, ∂g_we/∂rf = +g_we/(1−rf).
        D[ra0 + k_sp, i] = -(g_aw[i] / (rf[i] * C_a[i])) * (T_w[i] - T_a[i])
        D[ra0 + k_sp, n + i] = (
            -(g_aw[i] / (rf[i] * C_w[i])) * (T_a[i] - T_w[i])
            + (g_we[i] / ((1.0 - rf[i]) * C_w[i])) * (T_out - T_w[i])
        )
    return D


def _dFdtheta_const(
    est: Any,
    q: Dict[str, np.ndarray],
    layout: _ThetaLayout,
    model: HouseThermalSystem,
    ntheta: int,
    nx: int,
) -> np.ndarray:
    """``∂(∂f/∂x)/∂θ``, shape (ntheta, nx, nx), for the 2R2C drift.

    Used by the EKF-likelihood sensitivity pass to propagate ∂P/∂θ.
    Only the physical 2n × 2n block carries θ-dependence (the
    filter-column coupling through the heater scales is neglected,
    as in the previous 1R1C implementation).
    """
    n = est._n
    C_a, C_w = q["C_a"], q["C_w"]
    g_inf, g_aw, g_we = q["g_inf"], q["g_aw"], q["g_we"]
    fc, rf = q["fc"], q["rf"]
    F_phys = model._F  # (2n, 2n), already divided by C

    D = np.zeros((ntheta, nx, nx))
    for j in range(n):
        # log_mass: both of room j's rows scale with 1/C_tot.
        D[j, j, :2 * n] = -F_phys[j, :]
        D[j, n + j, :2 * n] = -F_phys[n + j, :]
        # log_r: g_inf, g_aw, g_we all scale with 1/r_ext.
        D[n + j, j, j] = (g_inf[j] + g_aw[j]) / C_a[j]
        D[n + j, j, n + j] = -g_aw[j] / C_a[j]
        D[n + j, n + j, j] = -g_aw[j] / C_w[j]
        D[n + j, n + j, n + j] = (g_aw[j] + g_we[j]) / C_w[j]

    r0, _ = layout.idx_log_r_ij
    for k_rij, (pi, pj) in enumerate(layout.identifiable_pairs):
        g_ij = float(q["g_ij"][k_rij])
        t = r0 + k_rij
        D[t, n + pi, n + pi] = g_ij / C_w[pi]
        D[t, n + pj, n + pj] = g_ij / C_w[pj]
        D[t, n + pi, n + pj] = -g_ij / C_w[pi]
        D[t, n + pj, n + pi] = -g_ij / C_w[pj]

    ca0, _ = layout.idx_c_air
    ra0, _ = layout.idx_r_aw
    for k_sp, i in enumerate(layout.identifiable_splits):
        t = ca0 + k_sp
        D[t, i, :2 * n] = -F_phys[i, :] / fc[i]
        D[t, n + i, :2 * n] = F_phys[n + i, :] / (1.0 - fc[i])
        t = ra0 + k_sp
        D[t, i, i] = g_aw[i] / (rf[i] * C_a[i])
        D[t, i, n + i] = -g_aw[i] / (rf[i] * C_a[i])
        D[t, n + i, i] = -g_aw[i] / (rf[i] * C_w[i])
        D[t, n + i, n + i] = (
            g_aw[i] / (rf[i] * C_w[i])
            - g_we[i] / ((1.0 - rf[i]) * C_w[i])
        )
    return D


def _simulation_mse_and_grad(
    est: Any,
    theta: np.ndarray,
    layout: _ThetaLayout,
    std_history: List[Dict[str, Any]],
    nominal_dt: float,
    max_gap_factor: float = 1.5,
    min_segment_steps: int = 20,
    max_window_steps: int = 60,
    dataset_start_ts: Optional[List[float]] = None,
) -> Tuple[float, np.ndarray]:
    """
    Multi-step open-loop simulation MSE and its gradient w.r.t. ``theta``.

    The history is split into contiguous segments wherever the timestamp
    gap between consecutive records exceeds ``max_gap_factor * nominal_dt``
    (controller restarts, HA pauses).  Each segment is further divided
    into windows of at most ``max_window_steps`` steps; each window is
    simulated forward from its own first measured temperature.  Short
    windows keep the simulation horizon well-conditioned and prevent
    the gradient for slowly-varying parameters (q_int) from being
    swamped by the accumulated error of very long open-loop runs.

    Gradient is computed via a forward-sensitivity pass propagating
    ``sx = ∂x/∂θ`` alongside the state.  This is strictly simpler than
    the EKF sensitivity pass because there is no Kalman update step —
    the sensitivity resets at every window boundary rather than
    accumulating across the full dataset.

    **Wall-seed contract** (see
    :meth:`HouseThermalSDE.initial_state_from_measurement`): the first
    window of each *dataset* segment seeds the envelope from the
    corresponding per-dataset ``θ[t_wall_init_seg_k]`` block (air seed
    then override); every later window — including the first window of
    within-dataset timestamp-gap segments — uses the ``"steady_state"``
    warm start at the local (T_a, T_out) equilibrium.  When
    ``dataset_start_ts`` is ``None`` (backward-compat / single-dataset
    path) the very first segment receives the single ``θ[t_wall_init]``
    block and all remaining segments use steady-state.

    Returns
    -------
    mse : float
        Sum of squared errors normalised per room per step.  Returns
        ``1e10`` (sentinel) on any numerical failure so the optimiser
        treats the point as infeasible.
    grad : (ntheta,) ndarray
        Gradient of ``mse`` w.r.t. ``theta`` (zero on failure).
    """
    _SENTINEL = 1e10
    ntheta = len(theta)
    _zero_grad = np.zeros(ntheta)

    if not np.all(np.isfinite(theta)):
        return _SENTINEL, _zero_grad.copy()

    model = _build_parametric_system(est, layout, theta)
    if model is None:
        return _SENTINEL, _zero_grad.copy()

    n = est._n
    nx = int(model.nx)
    _I = np.eye(nx)
    # Sub-steps per measurement interval for the implicit-Euler propagation.
    # Backward Euler is L-stable, so this is purely for accuracy: at the
    # 900 s sampling interval the 2R2C air node (~300 s) needs a sub-step
    # below its time constant for the heating/cooling transient that
    # separates heater-scale from thermal-mass to be resolved.  ~300 s
    # sub-steps (3 per 900 s interval) keep the truncation error small while
    # keeping each objective evaluation affordable for the joint multi-room
    # optimisation.
    n_sub = max(1, int(math.ceil(est._dt / 300.0)))

    quants = _theta_model_quantities(est, layout, theta)
    _p0 = model.params

    # ── Segment history at timestamp gaps ────────────────────────────
    N = len(std_history)
    seg_starts: List[int] = [0]
    for idx in range(N - 1):
        t_a = std_history[idx].get("t")
        t_b = std_history[idx + 1].get("t")
        if t_a is not None and t_b is not None:
            if (float(t_b) - float(t_a)) > max_gap_factor * nominal_dt:
                seg_starts.append(idx + 1)
    seg_starts.append(N)

    total_sse = 0.0
    total_grad = np.zeros(ntheta)
    n_steps_used = 0

    ra0, _ = layout.idx_r_aw
    tw0, _ = layout.idx_t_wall_init
    n_wall_segs = layout.n_wall_segs

    # Pre-process dataset start timestamps for O(1) segment-to-dataset
    # lookup.  Each detected contiguous segment whose first record's
    # timestamp falls within half a nominal step of a known dataset start
    # will be assigned the corresponding per-dataset θ[t_wall_init_seg]
    # block.  Segments that arise from *within-dataset* gaps (controller
    # restarts) will not match any dataset start and will use steady-state.
    _ts_tol = 0.5 * nominal_dt
    if dataset_start_ts is not None and len(dataset_start_ts) > 0:
        _ds_ts_arr: Optional[np.ndarray] = np.array(
            dataset_start_ts, dtype=float
        )
    else:
        _ds_ts_arr = None

    for seg_i in range(len(seg_starts) - 1):
        seg_begin = seg_starts[seg_i]
        seg_end = seg_starts[seg_i + 1]
        if (seg_end - seg_begin) < min_segment_steps:
            continue

        seg = std_history[seg_begin:seg_end]

        # Determine which (if any) per-dataset wall-temp block applies to
        # the first window of this segment.
        #   _wall_seg_idx is None  → steady-state seed (no injection)
        #   _wall_seg_idx == k     → inject θ[t_wall_init_seg_k]
        _wall_seg_idx: Optional[int]
        if _ds_ts_arr is not None:
            t_seg_start = seg[0].get("t")
            if t_seg_start is not None:
                diffs = np.abs(_ds_ts_arr - float(t_seg_start))
                best_k = int(np.argmin(diffs))
                _wall_seg_idx = (
                    best_k if diffs[best_k] <= _ts_tol else None
                )
            else:
                _wall_seg_idx = None
        else:
            # Backward-compat / single-dataset: seed only the very first
            # segment with the single θ[t_wall_init] block (seg index 0).
            _wall_seg_idx = 0 if seg_i == 0 else None

        # Split the contiguous segment into windows of at most
        # max_window_steps.  Each window is simulated independently
        # from its own first measurement so that the open-loop horizon
        # never grows so long that the gradient for slow parameters
        # (q_int, large C) is swamped by accumulated simulation error.
        is_first_window = True
        for win_start in range(0, len(seg), max_window_steps):
            win_end = min(win_start + max_window_steps, len(seg))
            if (win_end - win_start) < min_segment_steps:
                is_first_window = False
                continue
            win = seg[win_start:win_end]

            ym0 = np.asarray(win[0]["ym"], dtype=float)
            u0 = np.asarray(win[0].get("u", []), dtype=float)
            d0 = np.asarray(win[0].get("d", []), dtype=float)
            # Match diagnostics: air seed for the first window of a
            # dataset segment (wall override applied next); interior
            # windows use the parameter-dependent steady-state directly.
            _inject_wall = is_first_window and _wall_seg_idx is not None
            _wall_seed = "air" if _inject_wall else "steady_state"
            try:
                x = np.asarray(
                    model.initial_state_from_measurement(
                        ym0, u0, d0, wall_seed=_wall_seed
                    ),
                    dtype=float,
                )
            except TypeError:
                x = np.asarray(
                    model.initial_state_from_measurement(ym0, u0, d0),
                    dtype=float,
                )
            sx = np.zeros((ntheta, nx))  # ∂x/∂θ — reset per window
            is_first_window = False

            if _inject_wall:
                # Identified per-dataset t_wall_init from θ;
                # ∂x₀[n+i]/∂θ[tw_base+i] = 1.
                tw_base = tw0 + _wall_seg_idx * n  # type: ignore[operator]
                for i in range(n):
                    if n + i < nx:
                        x[n + i] = float(
                            np.clip(theta[tw_base + i], _T_WALL_LO, _T_WALL_HI)
                        )
                        sx[tw_base + i, n + i] = 1.0
                # From the second window onward the remaining segment
                # windows use steady-state, so clear the dataset index to
                # avoid accidental re-injection.
                _wall_seg_idx = None
            else:
                # steady_state wall seed already applied; propagate
                # r_aw sensitivity for that warmstart.
                if len(d0) > 0 and len(layout.identifiable_splits):
                    T_out0 = float(d0[0])
                    for k_sp, i in enumerate(layout.identifiable_splits):
                        sx[ra0 + k_sp, n + i] = T_out0 - float(x[i])

            for step in range(len(win) - 1):
                rec_k = win[step]
                rec_next = win[step + 1]

                try:
                    u_k = np.asarray(rec_k["u"], dtype=float)
                    d_k = np.asarray(rec_k["d"], dtype=float)
                    ym_k = np.asarray(rec_k["ym"], dtype=float)
                    ym_next = np.asarray(rec_next["ym"], dtype=float)
                except (KeyError, TypeError, ValueError):
                    break

                # ── Per-room open-window exclusion ───────────────────────
                # A room whose window is open carries unmodelled
                # air-exchange loss.  Rather than dropping the whole step
                # (which would discard every *other* room too), we:
                #   (1) pin the open room's air node to its measurement for
                #       the duration of the interval (so the coupled
                #       neighbours see the *true* boundary temperature, not a
                #       model free-run that cools too slowly), with zero
                #       parameter sensitivity since a pinned value is data,
                #       not a function of θ; and
                #   (2) drop that room's residual from the objective so its
                #       open-window dynamics never bias C / R / α / R_ij.
                # A room is excluded at step k→k+1 if its window was open at
                # either endpoint (the prediction is then built from a pinned
                # state and/or scored against an open-window measurement).
                open_k = rec_k.get("window_open")
                open_next = rec_next.get("window_open")
                pin_idx = (
                    np.where(open_k)[0]
                    if open_k is not None and open_k.any()
                    else _EMPTY_IDX
                )
                if open_k is None and open_next is None:
                    drop_idx = _EMPTY_IDX
                else:
                    drop_mask = np.zeros(n, dtype=bool)
                    if open_k is not None:
                        drop_mask |= open_k
                    if open_next is not None:
                        drop_mask |= open_next
                    drop_idx = np.where(drop_mask)[0]

                t_k = rec_k.get("t")
                t_next = rec_next.get("t")
                if t_k is not None and t_next is not None:
                    actual_dt = float(t_next) - float(t_k)
                    if actual_dt <= 0.0:
                        actual_dt = nominal_dt
                else:
                    actual_dt = nominal_dt
                h_sub = actual_dt / n_sub

                valid = True
                for _ in range(n_sub):
                    try:
                        f_val = model.f(x, u_k, d_k, _p0, 0.0)
                        F_full = model.dfdx(x, u_k, d_k, _p0, 0.0)
                    except Exception:
                        valid = False
                        break

                    # Linearly-implicit (backward) Euler step.  The 2R2C
                    # air node has a ~300 s time constant, so an *explicit*
                    # Euler step at the 900 s sampling interval (h/τ ≈ 3)
                    # sits past the |1+hλ|<1 stability limit and amplifies
                    # the air mode by ~2× per step — the trajectory (and its
                    # sensitivity) diverged, corrupting the objective and
                    # gradient that drive the optimiser.  Backward Euler is
                    # L-stable, matching the live CD-EKF's implicit-Euler
                    # propagation.  The heat input does not depend on the
                    # state, so f is affine in x within the ZOH step
                    # (f(y)=F·y+c, F constant in x) and one linear solve is
                    # exact:
                    #     (I − hF) x⁺ = x + h·c,      c = f(x) − F·x
                    M = _I - h_sub * F_full
                    try:
                        c_aff = f_val - F_full @ x
                        x_new = np.linalg.solve(M, x + h_sub * c_aff)
                        # Forward-sensitivity for backward Euler:
                        #     (I − hF) sx⁺ = sx + h·∂f/∂θ|_{x⁺}
                        # ∂f/∂θ must be evaluated at the *post-step* state so
                        # the analytic sensitivity matches the exact
                        # derivative of the implicit trajectory (it depends
                        # on x through f ∝ 1/C and the matrix reshaping by
                        # the envelope-split fractions).
                        f_new = model.f(x_new, u_k, d_k, _p0, 0.0)
                        dfdtheta_val = _dfdtheta_step(
                            est, quants, layout, model, f_new, x_new, u_k, d_k,
                            ntheta, nx,
                        )
                        sx = np.linalg.solve(
                            M, (sx + h_sub * dfdtheta_val).T
                        ).T
                        x = x_new
                        # Hold each open room's air node at its measurement
                        # across the ZOH interval so the coupled neighbours
                        # integrate against the real (cooling) boundary
                        # temperature.  The pinned value is data ⇒ its
                        # parameter sensitivity is zero.  The wall node is left
                        # to evolve, driven by the pinned air via R_aw.
                        if pin_idx.size:
                            x[pin_idx] = ym_k[pin_idx]
                            sx[:, pin_idx] = 0.0
                    except np.linalg.LinAlgError:
                        valid = False
                        break

                if (not valid or not np.all(np.isfinite(x))
                        or not np.all(np.isfinite(sx))):
                    # Overflow/divergence at an extreme trial point (e.g.
                    # during a line search).  Mark the whole evaluation
                    # infeasible so the optimiser backs off rather than
                    # ingesting a non-finite gradient.
                    return _SENTINEL, _zero_grad.copy()

                residual = ym_next - x[:n]  # shape (n,)
                # Drop open-window rooms: zeroing the residual removes the
                # room from both the SSE and the gradient (which is
                # sx[:, :n] @ residual) at this step.
                if drop_idx.size:
                    residual[drop_idx] = 0.0
                total_sse += float(np.dot(residual, residual))
                # ∂||residual||²/∂θ_i = -2 * sx[i, :n] · residual
                total_grad -= 2.0 * (sx[:, :n] @ residual)
                n_steps_used += 1

    if n_steps_used == 0:
        return _SENTINEL, _zero_grad.copy()

    # Noise-weighted *sum* of squared residuals (a proper Gaussian
    # data-misfit term), divided only by the number of rooms.
    #
    # We deliberately do NOT average over the number of steps.  Averaging
    # made the objective's curvature independent of the dataset length,
    # so the data could never out-vote the O(1) Gaussian prior no matter
    # how many observations were collected — the estimates stayed pinned
    # to the configured prior (manifesting as a poorly-fit open-loop with
    # the wrong gains).  Summing over steps and weighting by the
    # measurement variance ``R_var`` puts the data term on the same
    # footing as the prior in :meth:`_compute_regularization`, so the
    # data correctly dominates once enough informative samples exist
    # while the prior still stabilises directions the data cannot
    # constrain.
    scale = float(n * est._R_var)
    mse = total_sse / scale
    grad = total_grad / scale
    if not (np.isfinite(mse) and np.all(np.isfinite(grad))):
        return _SENTINEL, _zero_grad.copy()
    return mse, grad


def _cd_ped_neg_ll_and_grad(
    est: Any,
    theta: np.ndarray,
    layout: _ThetaLayout,
    std_history: List[Dict[str, np.ndarray]],
    x0: np.ndarray,
    P0: np.ndarray,
) -> Tuple[float, np.ndarray]:
    """
    Compute the CD-EKF PED negative log-likelihood **and** its gradient
    w.r.t. ``theta`` in a single forward-sensitivity pass.

    The likelihood is

        neg_ll(θ) = ½ Σ_k [ log|Sₖ| + νₖᵀ Sₖ⁻¹ νₖ ]

    Sensitivities  sx_i = ∂x̂/∂θ_i  and  sP_i = ∂P/∂θ_i  are propagated
    alongside the state and covariance through the Euler prediction steps.
    At each measurement the innovation contributes to both the objective and
    the gradient via

        ∂(neg_ll_k)/∂θ_i = ½ tr(M sP_i) − q · sx_i − ½ q · sP_i q

    where  M = Hᵀ S⁻¹ H  and  q = Hᵀ S⁻¹ ν.

    Returns
    -------
    neg_ll : float
    grad   : (ntheta,) ndarray – gradient of neg_ll only (regularisation
             is added by the caller).
    """
    _SENTINEL = 1e10
    ntheta = len(theta)
    _zero_grad = np.zeros(ntheta)

    if not np.all(np.isfinite(theta)):
        return _SENTINEL, _zero_grad.copy()

    model = _build_parametric_system(est, layout, theta)
    if model is None:
        return _SENTINEL, _zero_grad.copy()

    n = est._n
    nx = int(model.nx)     # 2n + m for 2R2C (no augmented offset states)
    n_sub = 1
    h_sub = est._dt / n_sub

    quants = _theta_model_quantities(est, layout, theta)

    # ── Precompute dFdtheta (constant – doesn't depend on x/u/d) ───────
    dFdtheta = _dFdtheta_const(est, quants, layout, model, ntheta, nx)

    # ── Measurement Jacobian H (constant for this model) ────────────────
    _u0 = np.zeros(model.nu)
    _d0 = np.zeros(model.nd)
    _p0 = model.params
    H = model.dhmdx(x0, _u0, _d0, _p0, 0.0)   # (nym, nx)
    Rm_mat = model.Rm                            # (nym, nym)

    # ── Initialise state and sensitivity arrays ──────────────────────────
    x = np.asarray(x0, dtype=float).copy()
    P = np.asarray(P0, dtype=float).copy()
    sx = np.zeros((ntheta, nx))      # ∂x̂/∂θ_i
    sP = np.zeros((ntheta, nx, nx))  # ∂P/∂θ_i (symmetric)

    neg_ll = 0.0
    grad = np.zeros(ntheta)

    for k in range(len(std_history) - 1):
        rec_k = std_history[k]
        rec_next = std_history[k + 1]

        try:
            u_k = np.asarray(rec_k["u"], dtype=float)
            d_k = np.asarray(rec_k["d"], dtype=float)
            ym_next = np.asarray(rec_next["ym"], dtype=float)
        except (KeyError, TypeError, ValueError):
            return _SENTINEL, _zero_grad.copy()

        # ── Euler prediction sub-steps ───────────────────────────────────
        for _ in range(n_sub):
            try:
                f_val = model.f(x, u_k, d_k, _p0, 0.0)        # (nx,)
                F_full = model.dfdx(x, u_k, d_k, _p0, 0.0)    # (nx, nx)
                G_sig = model.sigma(x, u_k, d_k, _p0, 0.0)    # (nx, nw)
            except Exception:
                return _SENTINEL, _zero_grad.copy()

            dfdtheta_val = _dfdtheta_step(
                est, quants, layout, model, f_val, x, u_k, d_k, ntheta, nx,
            )

            # ── Propagate state and covariance (Euler) ──────────────────
            P_dot = F_full @ P + P @ F_full.T + G_sig @ G_sig.T

            # ── Propagate sensitivities (Euler) ─────────────────────────
            # sx_dot[i] = F_full @ sx[i] + dfdtheta[i]
            # Vectorised: sx @ F_full.T gives (F_full @ sx[i])^T per row
            sx_dot = sx @ F_full.T + dfdtheta_val  # (ntheta, nx)

            # sP_dot[i] = F sP[i] + sP[i] Fᵀ + dFdtheta[i] P + P dFdtheta[i]ᵀ
            # Since sP[i] and P are symmetric:
            #   sP[i] Fᵀ = (F sP[i])ᵀ   →  FsP + FsP.T(axes)
            #   P dFdtheta[i]ᵀ = (dFdtheta[i] P)ᵀ  →  dFP + dFP.T(axes)
            FsP = np.einsum("ab,ibc->iac", F_full, sP)   # F @ sP[i]
            dFP = np.einsum("iab,bc->iac", dFdtheta, P)  # dFdtheta[i] @ P
            sP_dot = FsP + FsP.transpose(0, 2, 1) + dFP + dFP.transpose(0, 2, 1)

            x = x + h_sub * f_val
            P = P + h_sub * P_dot
            P = (P + P.T) * 0.5
            sx = sx + h_sub * sx_dot
            sP = sP + h_sub * sP_dot
            sP = (sP + sP.transpose(0, 2, 1)) * 0.5

        if not (np.all(np.isfinite(x)) and np.all(np.isfinite(P))):
            return _SENTINEL, _zero_grad.copy()

        # ── Innovation step ──────────────────────────────────────────────
        try:
            y_hat = model.hm(x, u_k, d_k, _p0, 0.0)
        except Exception:
            return _SENTINEL, _zero_grad.copy()

        nu = ym_next - y_hat
        S = H @ P @ H.T + Rm_mat

        try:
            sign, logdet = np.linalg.slogdet(S)
            if sign <= 0:
                return _SENTINEL, _zero_grad.copy()
            S_inv = np.linalg.inv(S)
        except np.linalg.LinAlgError:
            return _SENTINEL, _zero_grad.copy()

        S_inv_nu = S_inv @ nu
        neg_ll += 0.5 * (logdet + float(nu @ S_inv_nu))
        if not np.isfinite(neg_ll):
            return _SENTINEL, _zero_grad.copy()

        # ── Gradient contributions ───────────────────────────────────────
        # q = Hᵀ S⁻¹ ν   (nx,)
        # M = Hᵀ S⁻¹ H   (nx, nx)
        # ∂(neg_ll_k)/∂θ_i = ½ tr(M sP_i) − q·sx_i − ½ q·sP_i·q
        q = H.T @ S_inv_nu   # (nx,)
        M = H.T @ S_inv @ H  # (nx, nx)

        # Vectorised over all parameters:
        #   tr(M sP_i) = sum(M * sP_i)   [element-wise then sum]
        #   q · sx_i = sx @ q
        #   q · sP_i · q = einsum('i,iab,b', q, sP, q) == q @ sP[i] @ q per i
        grad += (
            0.5 * np.einsum("ab,iab->i", M, sP)
            - sx @ q
            - 0.5 * np.einsum("a,iab,b->i", q, sP, q)
        )

        # ── Kalman update ────────────────────────────────────────────────
        K = P @ H.T @ S_inv          # (nx, nym)
        Phi = np.eye(nx) - K @ H     # (nx, nx)
        P_minus = P.copy()

        x = x + K @ nu
        P = Phi @ P_minus @ Phi.T + K @ Rm_mat @ K.T
        P = (P + P.T) * 0.5

        # ── Sensitivity update through Kalman correction ─────────────────
        # sx[i]⁺ = Φ (sx[i] + sP[i] Hᵀ S⁻¹ ν) = Φ (sx[i] + sP[i] q)
        sx_aug = sx + np.einsum("iab,b->ia", sP, q)   # (ntheta, nx)
        sx = np.einsum("ab,ib->ia", Phi, sx_aug)       # Φ @ sx_aug[i]

        # J_i = Φ sP[i] Hᵀ S⁻¹ = ∂K/∂θ_i   (nx, nym) per i
        Phi_sP = np.einsum("ab,ibc->iac", Phi, sP)              # (ntheta, nx, nx)
        Phi_sP_Ht = np.einsum("iac,dc->iad", Phi_sP, H)         # (ntheta, nx, nym)
        J = np.einsum("iad,de->iae", Phi_sP_Ht, S_inv)          # (ntheta, nx, nym)

        # sP[i]⁺ = Φ sP[i] Φᵀ − J_i H P⁻ Φᵀ − Φ P⁻ Hᵀ J_iᵀ + J_i Rm Kᵀ + K Rm J_iᵀ
        HP_m = H @ P_minus                                        # (nym, nx)
        JHP = np.einsum("iad,db->iab", J, HP_m)                  # (ntheta, nx, nx)
        term2 = np.einsum("iab,cb->iac", JHP, Phi)               # J H P⁻ Φᵀ
        JRm = np.einsum("iad,de->iae", J, Rm_mat)                # (ntheta, nx, nym)
        term4 = np.einsum("iad,bd->iab", JRm, K)                 # J Rm Kᵀ
        sP = (
            np.einsum("iab,cb->iac", Phi_sP, Phi)  # Φ sP[i] Φᵀ
            - term2                                  # − J H P⁻ Φᵀ
            - term2.transpose(0, 2, 1)               # − Φ P⁻ Hᵀ Jᵀ
            + term4                                  # + J Rm Kᵀ
            + term4.transpose(0, 2, 1)               # + K Rm Jᵀ
        )
        sP = (sP + sP.transpose(0, 2, 1)) * 0.5

    return neg_ll, grad
