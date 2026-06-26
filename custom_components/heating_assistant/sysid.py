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
_ContinuousDiscreteEKFParams = None
_IntegrationScheme = None


def _ensure_imports() -> None:
    global _HouseThermalSDE, _ContinuousDiscreteEKF
    global _ContinuousDiscreteEKFParams, _IntegrationScheme
    if _HouseThermalSDE is None:
        from .controller import HouseThermalSDE  # noqa: PLC0415
        from mbc.estimation import (  # noqa: PLC0415
            ContinuousDiscreteEKF,
            ContinuousDiscreteEKFParams,
            IntegrationScheme,
        )
        _HouseThermalSDE = HouseThermalSDE
        _ContinuousDiscreteEKF = ContinuousDiscreteEKF
        _ContinuousDiscreteEKFParams = ContinuousDiscreteEKFParams
        _IntegrationScheme = IntegrationScheme


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
    window_spec: Optional[Any] = None,        # (start_ts, end_ts) explicit window, or None
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
        Number of most-recent history steps to reconstruct.  Ignored when
        ``window_spec`` is provided.
    room_params
        Per-room overrides applied to the copy.  Keys: ``thermal_mass``
        [J/K], ``r_external`` [K/W].
    sigma_w
        Continuous-time process noise intensity [K/√s]  (σ(x) = σ_w · I).
        Matches the units used by the production CD-EKF controller.
    sigma_v
        Measurement noise std [K].  Used by ``HouseThermalSDE.Rm``.
    window_spec
        Optional explicit window selection as ``(start_ts, end_ts)`` where
        both values are UNIX timestamps [s].  When provided, ``horizon_steps``
        is ignored and the specified slice of *history* is used instead of
        the most-recent trailing window.  This enables identification over
        a specific past experiment (e.g. an overnight step-response test)
        without that experiment needing to be the most recent data.

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

    # Window selection: explicit range takes priority over the trailing window.
    if window_spec is not None:
        from .history_window import select_window_by_timestamps  # noqa: PLC0415
        try:
            start_ts, end_ts = float(window_spec[0]), float(window_spec[1])
        except (TypeError, IndexError, ValueError) as exc:
            return {
                "error": f"Invalid window_spec {window_spec!r}: {exc}",
                "per_room": {},
                "horizon_steps": 0,
            }
        window = select_window_by_timestamps(history, start_ts, end_ts)
        if len(window) < 2:
            return {
                "error": (
                    f"No data (or < 2 records) in specified window "
                    f"[{start_ts:.0f}, {end_ts:.0f}]."
                ),
                "per_room": {},
                "horizon_steps": 0,
            }
    else:
        # Default: select the most-recent wall-clock horizon.  The history
        # buffer may contain gaps (standby, restarts).  Wall-clock selection
        # ensures the window never exceeds the configured horizon regardless
        # of sample density; the CD-EKF bridges each gap using the true dt.
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
            ts=dt,
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

    # If the caller supplied identified wall initial temperatures (via room_params
    # key "t_wall_initial"), use them instead of the default T_wall = T_air seed.
    _has_t_wall = any(
        "t_wall_initial" in room_params.get(name, {}) for name in room_list
    )
    t_wall_init_arr: Optional[np.ndarray] = None
    if _has_t_wall:
        t_wall_init_arr = np.array([
            float(room_params.get(name, {}).get("t_wall_initial", float("nan")))
            for name in room_list
        ])

    # Seed the full state from the first measurement so the wall and emitter-lag
    # nodes start at physically sensible values — see _init_state_from_measurement.
    x_curr = _init_state_from_measurement(
        sde, y0, n, n_x, u_prev, d_prev, t_wall_init=t_wall_init_arr,
    )
    P_curr = (sigma_v ** 2) * np.eye(n_x)

    # Record the initial anchor point.  Rooms whose window was open at the first
    # sample are rendered as a gap (null) — the open-window measurement is
    # excluded data, so neither the point nor the prediction is shown.
    _has_wall = n_x > n  # 2R2C: wall states at x[n:2n]
    ts0 = float(window[0].get("timestamp", 0.0))
    open0 = _record_window_open(window[0], room_names)
    for i, name in enumerate(room_names):
        if open0[i]:
            per_room_sim[name].append({
                "time":      ts0,
                "measured":  None,
                "predicted": None,
                "cov_upper": None,
                "cov_lower": None,
            })
            continue
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
        raw_meas: List[Optional[float]] = [
            float(y_raw[i]) if i < len(y_raw) else None for i in range(n)
        ]
        # Per-room open-window exclusion: the sensor still reads while a window
        # is open, but that measurement carries unmodelled air-exchange loss.  We
        # keep it OUT of the EKF update (so it neither corrects that room toward
        # an unexplainable value nor — via the joint cross-covariance — drags the
        # other rooms), render it as a gap (null) in the plot, and re-anchor the
        # room's air state to the true reading afterwards so the state stays
        # physical and the prediction reinitialises cleanly once the window
        # closes.
        excluded = _record_window_open(record, room_names)
        # ``y_meas`` is the display/score copy (None where excluded → chart gap,
        # skipped by the RMSE/MAE reducer).
        y_meas: List[Optional[float]] = [
            None if excluded[i] else raw_meas[i] for i in range(n)
        ]
        u_curr = _record_u(record, n_u)
        d_curr = _record_d(record, sde, room_list, n_d)

        # Build an EKF for this specific interval length.
        # n_steps scales with dt_step so the sub-step size stays ≤ dt/10
        # (same accuracy as the nominal filter).
        n_steps_step = max(1, min(200, round(dt_step * 10.0 / dt)))
        prev_ts = sde._ts
        sde._ts = dt_step
        try:
            ekf_step = _ContinuousDiscreteEKF(
                sde, x_curr, P_curr,
                params=_ContinuousDiscreteEKFParams(
                    n_steps=n_steps_step,
                    scheme=_IntegrationScheme.IMPLICIT_EULER,
                ),
            )
        finally:
            sde._ts = prev_ts

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
            if excluded[i]:
                # Open window → render a gap in both the measured and the
                # predicted series (the prediction would be an unanchored
                # free-run against data we are deliberately excluding).
                per_room_sim[name].append({
                    "time":      timestamp,
                    "measured":  None,
                    "predicted": None,
                    "cov_upper": None,
                    "cov_lower": None,
                })
                continue
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
        # A room is updated only when it has a real measurement AND its window
        # is closed.  Open-window rooms are masked out of the joint update.
        valid = np.array(
            [raw_meas[i] is not None and not excluded[i] for i in range(n)],
            dtype=bool,
        )
        y_k   = np.array([
            raw_meas[i] if raw_meas[i] is not None else float(T_pred[i])
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
        # Re-anchor each open-window room's air node to its true sensor reading.
        # The reading is excluded from the *fit* (it was masked above) but is a
        # perfectly good temperature, so using it to hold the state keeps the
        # coupled-neighbour propagation physical and guarantees the room's
        # prediction restarts from reality the moment the window closes.
        for i in range(n):
            if excluded[i] and raw_meas[i] is not None:
                x_curr[i] = float(raw_meas[i])
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
    t_wall_init: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Build a full EKF state vector consistent with a measurement.

    Delegates to the SDE's ``initial_state_from_measurement`` (the single
    source of truth shared with the open-loop diagnostic and the estimator):
    air temperatures from the measurement, the wall node equal to the air node
    (``T_air = T_envelope`` — the unbiased seed for the unobserved envelope),
    and emitter-lag states warm-started to the commanded fraction.  Without this
    the reconstruction cold-started every latent state at 0 — the wall node
    began at 0 °C and only recovered after a long EKF transient, badly
    distorting the start of the reconstruction.  Falls back to the
    air-temperature-only initialisation for monkeypatched/legacy SDE objects
    that do not provide the helper (the wall then starts at the air temperature
    rather than zero).

    When ``t_wall_init`` is provided (identified wall initial temperatures per
    room), those values override the default wall seed so the reconstruction
    starts from the identified envelope state rather than assuming T_wall = T_air.

    Wall-seed contract: first anchor uses air → ``t_wall_init`` override;
    see :meth:`HouseThermalSDE.initial_state_from_measurement` for the shared
    policy with the estimator objective and open-loop diagnostics.
    """
    air = np.array(
        [float(y_vals[i]) if i < len(y_vals) else 0.0 for i in range(n)],
        dtype=float,
    )
    init_fn = getattr(sde, "initial_state_from_measurement", None)
    x: Optional[np.ndarray] = None
    if callable(init_fn):
        # Request the diagnostics wall seed (envelope = air node) so the
        # reconstruction starts at T_air = T_envelope.  Older / monkeypatched
        # SDEs without the ``wall_seed`` kwarg fall back to the plain call and
        # the explicit air overwrite below.
        try:
            x = np.asarray(init_fn(air, u_vec, d_vec, wall_seed="air"), dtype=float)
        except TypeError:
            try:
                x = np.asarray(init_fn(air, u_vec, d_vec), dtype=float)
            except TypeError:
                try:
                    x = np.asarray(init_fn(air, u_vec), dtype=float)
                except Exception:
                    x = None
            except Exception:
                x = None
        except Exception:
            x = None
    if x is not None:
        if x.shape[0] >= 2 * n:
            if t_wall_init is not None:
                # Use identified wall initial temperatures.
                for i in range(n):
                    if i < len(t_wall_init) and np.isfinite(float(t_wall_init[i])):
                        x[n + i] = float(t_wall_init[i])
            else:
                # Default: wall starts at the measured air temperature.
                x[n:2 * n] = air[:n]
        return x
    # Fallback (no helper): air temperatures measured, wall block seeded.
    x = np.zeros(n_x, dtype=float)
    x[:n] = air
    if n_x >= 2 * n:
        if t_wall_init is not None:
            for i in range(n):
                if i < len(t_wall_init) and np.isfinite(float(t_wall_init[i])):
                    x[n + i] = float(t_wall_init[i])
        else:
            x[n:2 * n] = air
    return x


def _record_window_open(
    record: Dict[str, Any],
    room_names: List[str],
) -> List[bool]:
    """Per-room open-window flags for a history record, ordered by ``room_names``.

    The coordinator stores ``{"window_open": {room_name: bool}}`` (True while a
    window override is active).  Records that pre-date the field — seeded or
    legacy history — return all-``False`` (treated as good data).
    """
    wo = record.get("window_open") or {}
    return [bool(wo.get(name, False)) for name in room_names]


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
