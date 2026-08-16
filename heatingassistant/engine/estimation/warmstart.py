"""Warm-start helpers for grey-box parameter estimation."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..controller import HouseThermalSDE as HouseThermalSystem
from .constants import (
    _C_AIR_HI,
    _C_AIR_LO,
    _LOG_ALPHA_HI,
    _LOG_ALPHA_LO,
    _LOG_MASS_HI,
    _LOG_MASS_LO,
    _LOG_R_HI,
    _LOG_R_LO,
    _LOG_SOLAR_HI,
    _LOG_SOLAR_LO,
    _Q_INT_HI,
    _Q_INT_LO,
    _R_AW_HI,
    _R_AW_LO,
    _log_mass_bounds,
    _T_WALL_HI,
    _T_WALL_LO,
)
from .theta_layout import _ThetaLayout


def _update_wall_init_prior_from_history(
    est: Any,
    history: List[Dict[str, Any]],
) -> None:
    """Set the wall-init MAP prior from the first record's air / outdoor temps.

    Uses a (T_a, T_out) midpoint blend — a better default than T_wall = T_air
    after long envelope transients — and falls back to measured air when
    outdoor temperature is unavailable.
    """
    if not history:
        return
    first = history[0]
    first_y = first.get("y", [])
    t_out: Optional[float] = None
    try:
        t_out = float(first.get("d_outdoor"))
    except (TypeError, ValueError):
        t_out = None
    for i in range(est._n):
        t_air: Optional[float] = None
        if i < len(first_y):
            try:
                t_air = float(first_y[i])
            except (ValueError, TypeError):
                t_air = None
        if t_air is None:
            continue
        if t_out is not None and np.isfinite(t_out):
            # Midpoint between air and outdoor is a robust cold-start for
            # the hidden envelope when no filter history is available.
            est._t_wall_init_prior[i] = 0.5 * (t_air + t_out)
        else:
            est._t_wall_init_prior[i] = t_air


def _pin_locked_params(
    est: Any,
    bounds: List[Tuple[float, float]],
    theta_prior: np.ndarray,
    locked_params: Dict[str, Any],
    layout: _ThetaLayout,
    identifiable_sources: List[int],
    identifiable_solar: List[int],
    identifiable_splits: List[int],
) -> None:
    """Pin locked parameters to their fixed values by setting lb = ub.

    Modifies *bounds* and *theta_prior* in-place so the optimiser treats
    each locked parameter as a constant rather than a decision variable.
    Values outside the physical bounds are silently clamped.
    """
    for param_key, room_or_src_dict in locked_params.items():
        if not isinstance(room_or_src_dict, dict):
            continue

        if param_key == "heater_scales":
            for src_name, value in room_or_src_dict.items():
                src_idx = next(
                    (j for j, s in enumerate(est._sources)
                     if s.name == src_name),
                    None,
                )
                if src_idx is None or src_idx not in identifiable_sources:
                    continue
                k = identifiable_sources.index(src_idx)
                log_val = math.log(max(float(value), 1e-3))
                log_val = max(_LOG_ALPHA_LO, min(_LOG_ALPHA_HI, log_val))
                idx = layout.idx_log_alpha[0] + k
                bounds[idx] = (log_val, log_val)
                theta_prior[idx] = log_val
            continue

        for room_name, value in room_or_src_dict.items():
            if room_name not in est._room_names:
                continue
            i = est._room_names.index(room_name)

            if param_key == "thermal_mass":
                log_val = math.log(max(float(value), 1.0))
                log_val = max(_LOG_MASS_LO, min(_LOG_MASS_HI, log_val))
                idx = layout.idx_log_mass[0] + i
                bounds[idx] = (log_val, log_val)
                theta_prior[idx] = log_val

            elif param_key == "r_external":
                log_val = math.log(max(float(value), 1e-9))
                log_val = max(_LOG_R_LO, min(_LOG_R_HI, log_val))
                idx = layout.idx_log_r[0] + i
                bounds[idx] = (log_val, log_val)
                theta_prior[idx] = log_val

            elif param_key == "internal_gain":
                val = max(_Q_INT_LO, min(_Q_INT_HI, float(value)))
                idx = layout.idx_q_int[0] + i
                bounds[idx] = (val, val)
                theta_prior[idx] = val

            elif param_key == "solar_scale":
                if i not in identifiable_solar:
                    continue
                k = identifiable_solar.index(i)
                log_val = math.log(max(float(value), 1e-3))
                log_val = max(_LOG_SOLAR_LO, min(_LOG_SOLAR_HI, log_val))
                idx = layout.idx_log_solar[0] + k
                bounds[idx] = (log_val, log_val)
                theta_prior[idx] = log_val

            elif param_key == "c_air_fraction":
                if i not in identifiable_splits:
                    continue
                k = identifiable_splits.index(i)
                val = max(_C_AIR_LO, min(_C_AIR_HI, float(value)))
                idx = layout.idx_c_air[0] + k
                bounds[idx] = (val, val)
                theta_prior[idx] = val

            elif param_key == "r_aw_fraction":
                if i not in identifiable_splits:
                    continue
                k = identifiable_splits.index(i)
                val = max(_R_AW_LO, min(_R_AW_HI, float(value)))
                idx = layout.idx_r_aw[0] + k
                bounds[idx] = (val, val)
                theta_prior[idx] = val

            elif param_key == "t_wall_initial":
                val = max(_T_WALL_LO, min(_T_WALL_HI, float(value)))
                # Lock ALL dataset-segment wall temps to the same value.
                for seg in range(layout.n_wall_segs):
                    idx = layout.idx_t_wall_init[0] + seg * layout.n_rooms + i
                    bounds[idx] = (val, val)
                    theta_prior[idx] = val


def _physics_informed_theta(
    est: Any,
    std_history: List[Dict[str, np.ndarray]],
    layout: _ThetaLayout,
    theta_prior: np.ndarray,
    lb: np.ndarray,
    ub: np.ndarray,
    nominal_dt: Optional[float] = None,
) -> Optional[np.ndarray]:
    """Coarse, data-driven starting point for the multi-start optimiser.

    Performs an independent ordinary-least-squares fit of the lumped
    1R1C energy balance for each room

        C_i (dT_i/dt) = g_ext_i (T_out − T_i) + Q_i + q_int_i

    where ``Q_i`` is the known heat injection (heaters at the prior
    power-scale + solar) for room *i*.  Treating the inter-room coupling
    as part of the residual, each room yields a linear system in the
    unknowns ``[C_i, g_ext_i, q_int_i]`` solved by least squares over all
    consecutive sample pairs with a near-nominal time step.

    Only rooms with a well-conditioned fit and physically sensible
    (positive, in-bounds) ``C`` and ``g_ext`` adopt the data-driven
    values; every other room — and the heater-scale / inter-room-R
    blocks — keeps its prior.  Returns ``None`` when no room could be
    fit, so the caller simply falls back to the prior start.

    The result is a *starting point* only; the full nonlinear multi-step
    objective still refines it, so the coarseness of this fit (no
    inter-room term, explicit-Euler derivative) is acceptable.
    """
    n = est._n
    if len(std_history) < est._min_history_steps:
        return None
    dt_nom = float(nominal_dt) if nominal_dt else float(est._dt)

    # Per-room accumulators for the design matrix rows.
    rows: List[List[List[float]]] = [[] for _ in range(n)]
    targets: List[List[float]] = [[] for _ in range(n)]

    # Map each source to its room index and a thermal-power callable.
    src_room = [est._room_names.index(s.room) if s.room in est._room_names
                else -1 for s in est._sources]

    for k in range(len(std_history) - 1):
        rec = std_history[k]
        rec_next = std_history[k + 1]
        try:
            T_k = np.asarray(rec["ym"], dtype=float)
            T_next = np.asarray(rec_next["ym"], dtype=float)
            u_k = np.asarray(rec["u"], dtype=float)
            d_k = np.asarray(rec["d"], dtype=float)
        except (KeyError, TypeError, ValueError):
            continue
        if T_k.size < n or T_next.size < n:
            continue

        t_k = rec.get("t")
        t_next = rec_next.get("t")
        if t_k is not None and t_next is not None:
            dt = float(t_next) - float(t_k)
            # Skip pairs straddling a gap (controller restart / pause).
            if not (0.5 * dt_nom <= dt <= 1.5 * dt_nom):
                continue
        else:
            dt = dt_nom

        T_out = float(d_k[0]) if d_k.size > 0 else 0.0

        # Known heat injection per room: heaters (prior scale) + solar.
        Q = np.zeros(n)
        for j, src in enumerate(est._sources):
            i = src_room[j]
            if i < 0:
                continue
            u_s = float(u_k[j]) if j < u_k.size else 0.0
            try:
                Q[i] += float(src.thermal_power(max(0.0, u_s), T_out))
            except Exception:
                pass
        for i in range(n):
            if 1 + i < d_k.size:
                Q[i] += float(d_k[1 + i])  # solar gain slot

        for i in range(n):
            dTdt = (float(T_next[i]) - float(T_k[i])) / dt
            # C·dTdt − g·(T_out − T) − q_int = Q   →  unknown [C, g, q_int]
            rows[i].append([dTdt, -(T_out - float(T_k[i])), -1.0])
            targets[i].append(float(Q[i]))

    theta = theta_prior.copy()
    any_fit = False
    for i in range(n):
        A = np.asarray(rows[i], dtype=float)
        b = np.asarray(targets[i], dtype=float)
        if A.shape[0] < max(est._min_history_steps // 2, 5):
            continue
        # Require the regressors to actually vary, else the fit is
        # ill-posed (e.g. constant temperature / no excitation).
        if np.all(np.std(A, axis=0) < 1e-9):
            continue
        try:
            sol, _res, rank, _sv = np.linalg.lstsq(A, b, rcond=None)
        except np.linalg.LinAlgError:
            continue
        if rank < 3 or not np.all(np.isfinite(sol)):
            continue
        C_i, g_i, q_i = float(sol[0]), float(sol[1]), float(sol[2])
        if not (C_i > 0.0 and g_i > 0.0):
            continue  # unphysical — keep the prior for this room
        log_mass = float(np.clip(
            math.log(C_i), *_log_mass_bounds(float(est._log_mass_prior[i]))
        ))
        log_r = float(np.clip(math.log(1.0 / g_i), _LOG_R_LO, _LOG_R_HI))
        q_int = float(np.clip(q_i, _Q_INT_LO, _Q_INT_HI))
        theta[i] = log_mass
        theta[n + i] = log_r
        theta[2 * n + i] = q_int
        any_fit = True

    # Coarse heater-scale warm start from the same energy balance when
    # the layout includes α and the source duty cycle varies.
    if any_fit and layout.identifiable_sources:
        a0, b0 = layout.idx_log_alpha
        for k_la, s_idx in enumerate(layout.identifiable_sources):
            src = est._sources[s_idx]
            room_i = (
                est._room_names.index(src.room)
                if src.room in est._room_names else -1
            )
            if room_i < 0:
                continue
            C_i = math.exp(theta[room_i])
            g_i = math.exp(-theta[n + room_i])
            q_i = float(theta[2 * n + room_i])
            alpha_samples: List[float] = []
            for k in range(len(std_history) - 1):
                rec = std_history[k]
                rec_next = std_history[k + 1]
                try:
                    T_k = float(rec["ym"][room_i])
                    T_next = float(rec_next["ym"][room_i])
                    u_k = float(rec["u"][s_idx])
                    d_k = np.asarray(rec["d"], dtype=float)
                except (KeyError, TypeError, ValueError, IndexError):
                    continue
                if u_k < 0.08:
                    continue
                t_k = rec.get("t")
                t_next = rec_next.get("t")
                dt_step = (
                    float(t_next) - float(t_k)
                    if t_k is not None and t_next is not None
                    else float(nominal_dt) if nominal_dt else est._dt
                )
                if dt_step <= 0:
                    continue
                T_out = float(d_k[0]) if d_k.size > 0 else 0.0
                try:
                    Q_rated = float(src.thermal_power(max(0.0, u_k), T_out))
                except Exception:
                    continue
                if Q_rated < 1.0:
                    continue
                dTdt = (T_next - T_k) / dt_step
                residual_q = C_i * dTdt - g_i * (T_out - T_k) - q_i
                alpha_samples.append(residual_q / Q_rated)
            if len(alpha_samples) >= 5:
                alpha_med = float(np.median(alpha_samples))
                if np.isfinite(alpha_med) and alpha_med > 0:
                    log_a = float(
                        np.clip(math.log(alpha_med), _LOG_ALPHA_LO, _LOG_ALPHA_HI)
                    )
                    theta[a0 + k_la] = log_a

    if not any_fit:
        return None
    return np.clip(theta, lb, ub)


def _initial_state_and_covariance(
    est: Any,
    system: HouseThermalSystem,
    first_measurement: np.ndarray,
    u: Optional[np.ndarray] = None,
    d: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Build x0/P0 matching system dimensions (supports augmented states).

    When the first record's control ``u`` / disturbance ``d`` are supplied
    the state is seeded via the system's ``initial_state_from_measurement``
    helper — the single source of truth shared with the open-loop
    diagnostic and the simulation objective — so the wall nodes start at
    the (T_a, T_out) steady state and the emitter-lag states are
    warm-started to the commanded fraction.  Without ``u``/``d`` (or for
    legacy system objects) it falls back to seeding the wall block at the
    air temperature; the latent states never start at 0.
    """
    nx = int(system.nx)
    nym = int(system.nym)
    n = est._n

    x0: Optional[np.ndarray] = None
    init_fn = getattr(system, "initial_state_from_measurement", None)
    if callable(init_fn) and u is not None:
        try:
            x0 = np.asarray(
                init_fn(
                    np.asarray(first_measurement, dtype=float),
                    np.asarray(u, dtype=float),
                    np.asarray(d, dtype=float) if d is not None else None,
                ),
                dtype=float,
            )
            if x0.shape[0] != nx or not np.all(np.isfinite(x0)):
                x0 = None
        except Exception:
            x0 = None

    if x0 is None:
        x0 = np.zeros(nx, dtype=float)
        n_copy = min(nym, len(first_measurement), nx)
        x0[:n_copy] = np.array(first_measurement[:n_copy], dtype=float)
        # Wall block warm-starts at the air temperature; the emitter-lag
        # block (and any further augmentation) stays at zero.
        if nx >= 2 * n and n_copy >= n:
            x0[n: 2 * n] = x0[:n]

    P0 = np.eye(nx, dtype=float) * est._R_var * 10.0
    if nx > nym:
        # Latent states (wall nodes, emitter lags) are not directly
        # observed at startup, so start them with higher uncertainty
        # than the measured temperature components.
        P0[nym:, nym:] *= 4.0
    return x0, P0
