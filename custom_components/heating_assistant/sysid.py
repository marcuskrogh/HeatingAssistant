"""
System identification via CD-EKF reconstruction for the Heating Assistant.

``run_sysid_ekf`` drives the production ``ContinuousDiscreteEKF`` from the
``mbc`` package (the same filter the MPC controller uses) over a replay of
the recorded history window.  The SDE model used is a ``HouseThermalSDE``
copy built from the live (or overridden) room parameters so the sysid filter
is bit-for-bit identical to the live controller — same implicit-Euler mean
integration, same sensitivity-matrix covariance propagation:

    Predict over [t_{k-1}, t_k]  (n_steps sub-steps each):
        mean:  Newton solve  x_{j+1} − x_j − h f(x_{j+1}, u, d, p, t) = 0
        cov:   Φ_j = (I − h · ∂f/∂x(x_{j+1}))⁻¹
               τ_j = P_j + h · σ(x_j) σ(x_j)^T
               P_{j+1} = Φ_j τ_j Φ_j^T

Before each measurement update the predicted state x̂⁻(k) = x_pred[:n_rooms]
and the room-temperature covariance P⁻(k)[:n,:n] are recorded so the
dashboard can overlay:

  * the one-step EKF prediction (blue line) against the measurement (red), and
  * a ±2σ band  √(P⁻[i,i] + σ_v²)  from the predicted output variance.

Update (Joseph form, built into mbc):
    update(y_k, u_k, d_k, p, mask=valid)  →  x̂⁺(k), P⁺(k)
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional

import numpy as np

_LOGGER = logging.getLogger(__name__)

# Deferred imports so the module loads even if mbc / controller is absent
# during unit tests that monkey-patch run_sysid_ekf.
_HouseThermalSDE = None
_ContinuousDiscreteEKF = None


def _ensure_imports() -> None:
    global _HouseThermalSDE, _ContinuousDiscreteEKF
    if _HouseThermalSDE is None:
        from .controller import HouseThermalSDE  # noqa: PLC0415
        from .mbc.estimation import ContinuousDiscreteEKF  # noqa: PLC0415
        _HouseThermalSDE = HouseThermalSDE
        _ContinuousDiscreteEKF = ContinuousDiscreteEKF


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_sysid_ekf(
    history: List[Dict[str, Any]],
    model: Any,                               # HouseModel (read-only; deep-copied)
    heat_sources: List[Any],                  # HeatSource objects (read-only)
    room_names: List[str],
    dt: float,                                # sampling interval [s]
    horizon_steps: int,
    room_params: Dict[str, Dict[str, float]], # {room: {thermal_mass, r_external}}
    sigma_w: float,                           # continuous process noise intensity [K/√s]
    sigma_v: float,                           # measurement noise std [K]
) -> Dict[str, Any]:
    """
    Reconstruct the state trajectory with the production CD-EKF and return
    per-step one-step-ahead predictions with their output covariance.

    Parameters
    ----------
    history
        History-buffer entries (oldest first), each a dict with keys
        ``y`` (list[float]), ``u`` (list[float]), ``d_outdoor`` (float),
        ``d_solar`` (dict[str,float]), ``timestamp`` (float).
    model
        Live ``HouseModel``; a deep copy is made before overriding params.
    heat_sources
        Live heat-source objects.
    room_names
        Ordered room names matching the ``y`` index.
    dt
        Sampling interval in seconds (= coordinator update interval).
    horizon_steps
        Number of most-recent history steps to reconstruct.
    room_params
        Per-room overrides applied to the copy.  Keys: ``thermal_mass``
        [J/K], ``r_external`` [K/W].
    sigma_w
        Continuous-time process noise intensity [K/√s]  (σ(x) = σ_w · I).
        Matches the units used by the production CD-EKF controller.
    sigma_v
        Measurement noise std [K].  Used by ``HouseThermalSDE.Rm``.

    Returns
    -------
    dict with keys:
        ``per_room``     – {room_name: {simulation, rmse, mae, thermal_mass,
                                        r_external, sigma_w, sigma_v}}
          ``simulation`` – list of {time (UNIX float), measured,
                                    predicted, cov_upper, cov_lower}
        ``horizon_steps`` – actual steps reconstructed
        ``error``         – present only on failure
    """
    _ensure_imports()

    n = len(room_names)
    if n == 0:
        return {"error": "No rooms configured.", "per_room": {}}

    if len(history) < 2:
        return {
            "error": f"Insufficient history: need ≥ 2 steps, have {len(history)}.",
            "per_room": {},
            "horizon_steps": 0,
        }

    # Select the window by *active sampled time* rather than raw wall-clock or
    # step count.  The history buffer may contain gaps (standby, restarts).
    # Pure step-count selection can span far more wall-clock time than intended,
    # while a pure wall-clock cutoff lets a single restart gap inside the window
    # consume the whole horizon — leaving only post-restart samples.  Counting
    # active time (capping each interval so a gap costs at most one nominal step)
    # spans ~horizon of real operation and bridges restarts naturally; the
    # continuous-time CD-EKF then propagates across each gap using the true dt.
    from .history_window import select_recent_window  # noqa: PLC0415

    window = select_recent_window(history, horizon_steps * dt, dt)
    if len(window) < 2:
        window = list(history)[-(min(horizon_steps, len(history) - 1) + 1):]

    # Skip leading records that pre-date a room-count change (e.g. config
    # edits that added rooms while the buffer was already populated).
    win_start = 0
    for win_start, rec in enumerate(window):
        if len(rec.get("y", [])) >= n:
            break
    window = window[win_start:]

    if len(window) < 2:
        return {
            "error": "Insufficient valid history (room-count mismatch).",
            "per_room": {},
            "horizon_steps": 0,
        }

    actual_steps = len(window) - 1

    # ------------------------------------------------------------------
    # Build parametrised HouseModel copy
    # ------------------------------------------------------------------
    try:
        sim_model = _build_sim_model(model, room_params, room_names)
    except Exception as exc:
        _LOGGER.error("SysID: model construction failed: %s", exc, exc_info=True)
        return {"error": f"Model construction failed: {exc}", "per_room": {}}

    # ------------------------------------------------------------------
    # Build HouseThermalSDE  (augment_offsets=False: state = [T(n), φ(m)])
    # ------------------------------------------------------------------
    try:
        sde = _HouseThermalSDE(
            sim_model,
            heat_sources,
            dt,
            sigma_w=sigma_w,
            sigma_v=sigma_v,
            augment_offsets=False,
        )
    except Exception as exc:
        _LOGGER.error("SysID: SDE construction failed: %s", exc, exc_info=True)
        return {"error": f"SDE construction failed: {exc}", "per_room": {}}

    n_x = sde.nx                              # n_rooms + n_emitter_lags
    room_list = sde._room_list                # authoritative room order from SDE

    p = np.array([])    # use model-default parameters

    # ------------------------------------------------------------------
    # ZOH bookkeeping
    # ------------------------------------------------------------------
    n_u = len(heat_sources)
    n_d = sde.nd   # 1 + 2·n_rooms  ([T_out, q_solar(n), q_air(n)])

    # ------------------------------------------------------------------
    # Output containers
    # ------------------------------------------------------------------
    per_room_sim: Dict[str, List[Dict[str, Any]]] = {name: [] for name in room_names}

    # ------------------------------------------------------------------
    # Variable-dt CD-EKF over the entire window.
    #
    # The model is continuous-time, so the EKF can propagate between any
    # two observations regardless of the interval between them.  We create
    # a fresh EKF object per step using the *actual* elapsed time derived
    # from the timestamps.  This correctly handles:
    #   • normal operation with a fixed update interval,
    #   • brief restarts (small gap — EKF bridges the gap naturally), and
    #   • longer down-times (larger gap — EKF propagates uncertainty over
    #     the actual outage duration, which is then corrected by the update).
    # ------------------------------------------------------------------
    y0 = window[0].get("y", [])
    u_prev = _record_u(window[0], n_u)
    d_prev = _record_d(window[0], sde, room_list, n_d)
    # Seed the full state from the first measurement so the wall and emitter-lag
    # nodes start at physically sensible values (steady-state wall, warm emitter)
    # rather than 0 — see _init_state_from_measurement.
    x_curr = _init_state_from_measurement(sde, y0, n, n_x, u_prev, d_prev)
    P_curr = (sigma_v ** 2) * np.eye(n_x)

    # Record the initial anchor point.
    _has_wall = n_x > n  # 2R2C: wall states at x[n:2n]
    ts0 = float(window[0].get("timestamp", 0.0))
    for i, name in enumerate(room_names):
        entry0: Dict[str, Any] = {
            "time":      ts0,
            "measured":  float(y0[i]) if i < len(y0) else None,
            "predicted": float(x_curr[i]),
            "cov_upper": float(x_curr[i] + 2.0 * sigma_v),
            "cov_lower": float(x_curr[i] - 2.0 * sigma_v),
        }
        if _has_wall and n + i < n_x:
            entry0["predicted_wall"] = float(x_curr[n + i])
        per_room_sim[name].append(entry0)

    # u_prev / d_prev were computed above for the initial-state seeding.
    t_prev = ts0

    for record in window[1:]:
        timestamp = float(record.get("timestamp", 0.0))
        dt_step   = timestamp - t_prev

        if dt_step <= 0:
            # Skip duplicate or out-of-order timestamps.
            continue

        y_raw  = record.get("y", [])
        y_meas: List[Optional[float]] = [
            float(y_raw[i]) if i < len(y_raw) else None for i in range(n)
        ]
        u_curr = _record_u(record, n_u)
        d_curr = _record_d(record, sde, room_list, n_d)

        # Build an EKF for this specific interval length.
        # n_steps scales with dt_step so the sub-step size stays ≤ dt/10
        # (same accuracy as the nominal filter).
        n_steps_step = max(1, min(200, round(dt_step * 10.0 / dt)))
        ekf_step = _ContinuousDiscreteEKF(
            sde, x_curr, P_curr, dt_step,
            n_steps=n_steps_step,
            scheme="implicit-euler",
        )

        # ---- Prediction step ----------------------------------------
        try:
            x_pred, P_pred = ekf_step.predict(u_prev, d_prev, p, t_prev)
        except Exception as exc:
            _LOGGER.warning(
                "SysID CD-EKF predict failed at ts=%.0f (dt_step=%.1f s): %s",
                timestamp, dt_step, exc,
            )
            # Reinitialise from the raw measurement and continue.  Seed the
            # latent states (wall, emitter lag) from the measurement too so the
            # wall does not restart at 0 °C after a transient EKF failure.
            y_reinit = [
                y_meas[i] if y_meas[i] is not None else float(x_curr[i])
                for i in range(n)
            ]
            x_curr = _init_state_from_measurement(
                sde, y_reinit, n, n_x, u_curr, d_curr
            )
            P_curr = (sigma_v ** 2) * np.eye(n_x)
            t_prev = timestamp
            u_prev = u_curr
            d_prev = d_curr
            continue

        T_pred    = x_pred[:n]
        P_pred_rr = P_pred[:n, :n]

        # ---- Record one-step-ahead prediction -----------------------
        for i, name in enumerate(room_names):
            std_i = float(np.sqrt(max(0.0, P_pred_rr[i, i] + sigma_v ** 2)))
            entry: Dict[str, Any] = {
                "time":      timestamp,
                "measured":  y_meas[i],
                "predicted": float(T_pred[i]),
                "cov_upper": float(T_pred[i] + 2.0 * std_i),
                "cov_lower": float(T_pred[i] - 2.0 * std_i),
            }
            if _has_wall and n + i < n_x and P_pred.shape[0] > n + i:
                std_w = float(np.sqrt(max(0.0, P_pred[n + i, n + i])))
                entry["predicted_wall"] = float(x_pred[n + i])
                entry["wall_cov_upper"] = float(x_pred[n + i] + 2.0 * std_w)
                entry["wall_cov_lower"] = float(x_pred[n + i] - 2.0 * std_w)
            per_room_sim[name].append(entry)

        # ---- Update step --------------------------------------------
        valid = np.array([m is not None for m in y_meas], dtype=bool)
        y_k   = np.array([
            y_meas[i] if y_meas[i] is not None else float(T_pred[i])
            for i in range(n)
        ], dtype=float)
        mask  = valid if not valid.all() else None

        try:
            ekf_step.update(y_k, u_curr, d_curr, p, mask=mask)
        except Exception as exc:
            _LOGGER.warning(
                "SysID CD-EKF update failed at ts=%.0f: %s", timestamp, exc,
            )
            x_curr[:n] = [float(y_k[i]) for i in range(n)]
            P_curr = (sigma_v ** 2) * np.eye(n_x)
            t_prev = timestamp
            u_prev = u_curr
            d_prev = d_curr
            continue

        x_curr = ekf_step.x_hat
        P_curr = ekf_step.P
        u_prev = u_curr
        d_prev = d_curr
        t_prev = timestamp

    # ------------------------------------------------------------------
    # One-step prediction RMSE / MAE per room
    # ------------------------------------------------------------------
    per_room_out: Dict[str, Any] = {}
    for i, name in enumerate(room_names):
        entries = per_room_sim[name]
        errors  = [
            e["predicted"] - e["measured"]
            for e in entries
            if e.get("measured") is not None
        ]
        if errors:
            arr  = np.array(errors, dtype=float)
            rmse = float(np.sqrt(np.mean(arr ** 2)))
            mae  = float(np.mean(np.abs(arr)))
        else:
            rmse = mae = None

        room = sim_model.rooms.get(name)
        per_room_out[name] = {
            "simulation":   entries,
            "rmse":         rmse,
            "mae":          mae,
            "thermal_mass": float(room.thermal_mass) if room else None,
            "r_external":   float(room.r_external)   if room else None,
            "sigma_w":      sigma_w,
            "sigma_v":      sigma_v,
        }

    return {
        "per_room":      per_room_out,
        "horizon_steps": actual_steps,
    }


# Alias kept for callers that import the old name.
run_sysid_simulation = run_sysid_ekf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_state_from_measurement(
    sde: Any,
    y_vals: List[Any],
    n: int,
    n_x: int,
    u_vec: np.ndarray,
    d_vec: np.ndarray,
) -> np.ndarray:
    """Build a full EKF state vector consistent with a measurement.

    Delegates to the SDE's ``initial_state_from_measurement`` (the single
    source of truth shared with the open-loop diagnostic and the estimator):
    air temperatures from the measurement, wall nodes at the (T_a, T_out)
    steady state, and emitter-lag states warm-started to the commanded
    fraction.  Without this the reconstruction cold-started every latent state
    at 0 — the wall node began at 0 °C and only recovered after a long EKF
    transient, badly distorting the start of the reconstruction.  Falls back to
    the air-temperature-only initialisation for monkeypatched/legacy SDE
    objects that do not provide the helper (the wall then starts at the air
    temperature rather than zero).
    """
    air = np.array(
        [float(y_vals[i]) if i < len(y_vals) else 0.0 for i in range(n)],
        dtype=float,
    )
    init_fn = getattr(sde, "initial_state_from_measurement", None)
    if callable(init_fn):
        try:
            return np.asarray(init_fn(air, u_vec, d_vec), dtype=float)
        except TypeError:
            try:
                return np.asarray(init_fn(air, u_vec), dtype=float)
            except Exception:
                pass
        except Exception:
            pass
    # Fallback: air temperatures measured, wall block warm-started to the air
    # temperature (never 0), remaining latent states left at 0.
    x = np.zeros(n_x, dtype=float)
    x[:n] = air
    if n_x >= 2 * n:
        x[n:2 * n] = air
    return x


def _record_u(record: Dict[str, Any], n_u: int) -> np.ndarray:
    """Extract control fractions from a history record as a float64 array."""
    u_raw = record.get("u", [])
    return np.array(
        [float(u_raw[k]) if k < len(u_raw) else 0.0 for k in range(n_u)],
        dtype=float,
    )


def _record_d(
    record: Dict[str, Any],
    sde: Any,
    room_list: List[str],
    n_d: int,
) -> np.ndarray:
    """Build the disturbance vector for a history record.

    Layout matches ``HouseThermalSDE.nd``: ``d = [T_out, q_solar(n), q_air(n)]``
    — outdoor temperature, the per-room *unscaled* modelled solar gain, and the
    per-room air-node heat (internal gain).  The SDE's own
    ``disturbance_vector`` is used so the reconstruction folds in each room's
    identified ``internal_gain`` exactly as the live CD-EKF / MPC does; without
    it the internal-gain field had no effect on the reconstruction.
    """
    outdoor = float(record.get("d_outdoor", 10.0))
    solar = record.get("d_solar", {}) or {}
    builder = getattr(sde, "disturbance_vector", None)
    if builder is not None:
        return np.asarray(builder(outdoor, solar), dtype=float)

    # Fallback for SDEs without a disturbance builder: solar only.
    d = np.zeros(n_d, dtype=float)
    d[0] = outdoor
    for i, name in enumerate(room_list):
        if 1 + i < n_d:
            d[1 + i] = float(solar.get(name, 0.0))
    return d


# Per-room thermal-model attributes that a sysid / open-loop run may override
# from the System Identification panel.  Each maps a service-data / room_params
# key to the corresponding ``Room`` attribute so the simulation reflects exactly
# the values the user has entered, without applying them to the live model.
_ROOM_PARAM_ATTRS = (
    "thermal_mass",
    "r_external",
    "internal_gain",
    "solar_scale",
    "c_air_fraction",
    "r_aw_fraction",
)


def _build_sim_model(
    live_model: Any,
    room_params: Dict[str, Dict[str, float]],
    room_names: List[str],
) -> Any:
    """Return a deep copy of *live_model* with *room_params* overrides applied.

    Every per-room thermal-model parameter the identification panel exposes
    (``thermal_mass``, ``r_external``, ``internal_gain``, ``solar_scale`` and
    the 2R2C envelope split ``c_air_fraction`` / ``r_aw_fraction``) is applied
    when present so the reconstruction / open-loop simulation uses the full set
    of values currently shown in the UI, not just C and R_ext.
    """
    sim_model = copy.deepcopy(live_model)
    for name in room_names:
        overrides = room_params.get(name, {})
        if not overrides:
            continue
        room = sim_model.rooms.get(name)
        if room is None:
            continue
        for attr in _ROOM_PARAM_ATTRS:
            if attr in overrides:
                setattr(room, attr, float(overrides[attr]))

    # Rebuild cached matrices so HouseThermalSDE picks up the new parameters.
    sim_model.rebuild_derived_parameters()
    C, A, B = sim_model._build_matrices()
    sim_model._C     = C
    sim_model._A     = A
    sim_model._B_ext = B

    return sim_model
