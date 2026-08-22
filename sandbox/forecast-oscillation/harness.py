#!/usr/bin/env python3
"""SWD-432 measure sandbox: room-view Forecast oscillation vs smooth U.

Audits remaining-U* resim inputs (U, outdoor, solar, wind), discrete
map (dt, n_int, implicit Euler), and an independent solve_ivp roll.
Does not edit production source.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.integrate import solve_ivp

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from heatingassistant.app.forecast_payload import build_app_forecast_payload  # noqa: E402
from heatingassistant.app.disturbance_forecasts import build_mpc_disturbance_inputs  # noqa: E402
from heatingassistant.engine.const import (  # noqa: E402
    DEFAULT_C_AIR_FRACTION,
    DEFAULT_NMPC_FAST_SUBSTEPS,
    DEFAULT_NMPC_HORIZON_H,
    DEFAULT_NMPC_PERIOD,
    DEFAULT_THERMAL_MASS,
)
from heatingassistant.engine.control_loop import ControlEngine  # noqa: E402
from heatingassistant.engine.nmpc_ocp import roll_fast_air_path, step_hold  # noqa: E402
from heatingassistant.engine.naming import room_slug  # noqa: E402
from heatingassistant.engine.weather import parse_temperature_forecast  # noqa: E402

HERE = Path(__file__).resolve().parent
INSPECT = HERE / "inspect"
NOW = datetime(2026, 8, 22, 5, 54, tzinfo=timezone.utc)
ROOM = "Living Room"
SLUG = room_slug(ROOM)


def _config(*, thermal_mass: float | None = None, c_air: float | None = None) -> dict:
    room = {
        "name": ROOM,
        "setpoint": 23.5,
        "comfort_offset": 2.0,
        "temperature": 23.8,
        "solar_exposure": "high",
        "solar_facing": 180.0,
        "thermal_mass": float(
            DEFAULT_THERMAL_MASS if thermal_mass is None else thermal_mass
        ),
    }
    if c_air is not None:
        room["c_air_fraction"] = float(c_air)
    return {
        "nmpc_period": DEFAULT_NMPC_PERIOD,
        "nmpc_fast_substeps": DEFAULT_NMPC_FAST_SUBSTEPS,
        "nmpc_horizon_h": DEFAULT_NMPC_HORIZON_H,
        "latitude": 55.67,
        "longitude": 12.57,
        "energy_price_weight": 1.0,
        "rooms": [room],
        "heat_sources": [
            {
                "name": "hp",
                "type": "heat_pump",
                "room": ROOM,
                "max_power": 7000.0,
                "hvac_mode": "heat_cool",
            }
        ],
    }


def _series(rows: list, key: str | None = None) -> np.ndarray:
    if not rows:
        return np.zeros(0, dtype=float)
    if key is None:
        return np.array([float(step[ROOM]) for step in rows], dtype=float)
    out = []
    for step in rows:
        if isinstance(step, dict):
            out.append(float(step.get(key, step.get(ROOM, 0.0)) or 0.0))
        else:
            out.append(float(step))
    return np.array(out, dtype=float)


def _jitter(vals: np.ndarray) -> dict[str, float]:
    if vals.size < 2:
        return {
            "n": float(vals.size),
            "max_abs_d": 0.0,
            "rms_d": 0.0,
            "sign_flips": 0.0,
            "p95_abs_d": 0.0,
        }
    d = np.diff(vals)
    signs = np.sign(d)
    flips = int(np.sum((signs[1:] * signs[:-1]) < 0.0))
    abs_d = np.abs(d)
    return {
        "n": float(vals.size),
        "max_abs_d": float(np.max(abs_d)),
        "rms_d": float(np.sqrt(np.mean(d * d))),
        "sign_flips": float(flips),
        "p95_abs_d": float(np.percentile(abs_d, 95)),
    }


def peaked_prices(n_fast: int, dt_s: float) -> list[float]:
    hours = (np.arange(n_fast, dtype=float) + 0.5) * (dt_s / 3600.0)
    base = 0.9
    p1 = 1.1 * np.exp(-0.5 * ((hours - 8.0) / 2.0) ** 2)
    p2 = 1.2 * np.exp(-0.5 * ((hours - 20.0) / 2.5) ** 2)
    return (base + p1 + p2).tolist()


def hourly_weather(now: datetime, hours: int = 48) -> list[dict]:
    """Smooth summer outdoor + cloud, hourly, like HA weather.forecast."""

    rows = []
    for h in range(hours):
        t = now + timedelta(hours=h)
        local_h = (t.hour + 2) % 24  # Copenhagen summer UTC+2
        outdoor = 16.0 + 6.0 * math.sin((local_h - 8) / 24.0 * 2.0 * math.pi)
        cloud = 35.0 + 20.0 * math.sin((local_h - 14) / 24.0 * 2.0 * math.pi)
        rows.append(
            {
                "datetime": t.isoformat(),
                "temperature": round(outdoor, 2),
                "cloud_coverage": max(0.0, min(100.0, cloud)),
                "condition": "partlycloudy",
            }
        )
    return rows


def hourly_ghi(now: datetime, hours: int = 48) -> list[tuple[float, float]]:
    rows = []
    for h in range(hours):
        t = now + timedelta(hours=h)
        local_h = (t.hour + 2) % 24
        # Simple clear-ish GHI envelope; zero at night.
        elev = math.sin((local_h - 6) / 12.0 * math.pi)
        ghi = max(0.0, 780.0 * elev)
        rows.append((t.timestamp(), ghi))
    return rows


def mean_hold_d(d_list: list[np.ndarray], k0: int, m: int) -> list[np.ndarray]:
    """Average d on the same slow holds as remaining U* (MODEL d_n)."""

    n = len(d_list)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault((int(k0) + i) // max(int(m), 1), []).append(i)
    out: list[np.ndarray] = [d_list[0]] * n
    for idxs in groups.values():
        mean = np.mean(np.stack([d_list[i] for i in idxs], axis=0), axis=0)
        for i in idxs:
            out[i] = mean.copy()
    return out


def _roll_d(ctrl, U: np.ndarray, d_list: list[np.ndarray], x0=None):
    plant = ctrl._control_system
    x = ctrl._ekf.x_hat if x0 is None else np.asarray(x0, dtype=float)
    return roll_fast_air_path(
        plant,
        x,
        U,
        d_list,
        dt_s=float(ctrl._timing.dt_s),
        n_int=int(ctrl._n_int_steps),
        n_rooms=int(plant._n_rooms),
    )


def _d_fast(ctrl, outdoor: list[float], solar: list[dict]) -> list[np.ndarray]:
    plant = ctrl._control_system
    last = {}
    out = []
    for k, tout in enumerate(outdoor):
        if k < len(solar):
            last = solar[k]
        out.append(plant.disturbance_vector(float(tout), last))
    return out


def _roll(ctrl, U: np.ndarray, outdoor: list[float], solar: list[dict], x0=None):
    plant = ctrl._control_system
    x = ctrl._ekf.x_hat if x0 is None else np.asarray(x0, dtype=float)
    d = _d_fast(ctrl, outdoor, solar)
    return roll_fast_air_path(
        plant,
        x,
        U,
        d,
        dt_s=float(ctrl._timing.dt_s),
        n_int=int(ctrl._n_int_steps),
        n_rooms=int(plant._n_rooms),
    )


def _ivp_roll(ctrl, U: np.ndarray, outdoor: list[float], solar: list[dict], x0=None):
    """Independent adaptive RK45 roll with the same ZOH U/d as production."""

    plant = ctrl._control_system
    p = np.array([], dtype=float)
    x = (ctrl._ekf.x_hat if x0 is None else np.asarray(x0, dtype=float)).copy()
    d_list = _d_fast(ctrl, outdoor, solar)
    dt = float(ctrl._timing.dt_s)
    n_rooms = int(plant._n_rooms)
    air = np.zeros((len(U), n_rooms), dtype=float)
    last_d = d_list[-1]
    for k in range(len(U)):
        u_k = np.asarray(U[k], dtype=float)
        d_k = d_list[k] if k < len(d_list) else last_d

        def rhs(t, xx, u=u_k, d=d_k):
            return plant.f(xx, u, d, p, 0.0)

        sol = solve_ivp(
            rhs,
            (0.0, dt),
            x,
            method="RK45",
            rtol=1e-7,
            atol=1e-8,
            dense_output=False,
        )
        if not sol.success:
            raise RuntimeError(f"solve_ivp failed at k={k}: {sol.message}")
        x = sol.y[:, -1]
        air[k] = x[:n_rooms]
    return air, x


def _eigenvalues(ctrl) -> dict:
    plant = ctrl._control_system
    F = np.asarray(plant._F, dtype=float)
    eig = np.linalg.eigvals(F)
    real = np.real(eig)
    imag = np.imag(eig)
    return {
        "n": int(eig.size),
        "max_real": float(np.max(real)),
        "min_real": float(np.min(real)),
        "max_abs_imag": float(np.max(np.abs(imag))),
        "any_unstable": bool(np.any(real > 1e-9)),
        "any_complex": bool(np.any(np.abs(imag) > 1e-9)),
        "eigs": [
            {"re": float(r), "im": float(i)}
            for r, i in zip(real, imag)
        ],
        "C_air": float(plant._C_cap[0]),
        "C_wall": float(plant._C_cap[plant._n_rooms]),
        "n_filtered": int(plant._n_filtered),
        "emitter_tau": (
            float(plant._emitter_taus[0]) if plant._n_filtered else 0.0
        ),
        "dt_s": float(ctrl._timing.dt_s),
        "n_int": int(ctrl._n_int_steps),
        "h_sub": float(ctrl._timing.dt_s) / float(ctrl._n_int_steps),
        "horizon": int(ctrl._horizon),
        "c_air_fraction_default": float(DEFAULT_C_AIR_FRACTION),
    }


def _energy_residual(ctrl, U, outdoor, solar, air) -> dict:
    """Mean |C dT/dt − Q_net| over the first 8 h, using endpoint ΔT/dt."""

    plant = ctrl._control_system
    n = plant._n_rooms
    dt = float(ctrl._timing.dt_s)
    x = ctrl._ekf.x_hat.copy()
    p = np.array([], dtype=float)
    d_list = _d_fast(ctrl, outdoor, solar)
    res = []
    q_heat = []
    q_sol = []
    for k in range(min(len(U), len(air))):
        d_k = d_list[k]
        u_k = np.asarray(U[k], dtype=float)
        x_next = step_hold(plant, x, u_k, d_k, p, dt, int(ctrl._n_int_steps))
        dT = (x_next[: 2 * n] - x[: 2 * n]) / dt
        stored = float(np.dot(plant._C_cap[: 2 * n], dT))
        f = plant.f(x_next, u_k, d_k, p, 0.0)
        rhs_power = float(np.dot(plant._C_cap[: 2 * n], f[: 2 * n]))
        res.append(stored - rhs_power)
        src = plant._sources[0]
        q_heat.append(float(src.smooth_thermal_power(float(u_k[0]), float(d_k[0]), plant._k_sigmoid)))
        q_sol.append(float(d_k[1]))
        x = x_next
    arr = np.asarray(res, dtype=float)
    return {
        "max_abs_W": float(np.max(np.abs(arr))) if arr.size else 0.0,
        "rms_W": float(np.sqrt(np.mean(arr * arr))) if arr.size else 0.0,
        "q_heat_min_W": float(np.min(q_heat)) if q_heat else 0.0,
        "q_heat_max_W": float(np.max(q_heat)) if q_heat else 0.0,
        "q_solar_min_W": float(np.min(q_sol)) if q_sol else 0.0,
        "q_solar_max_W": float(np.max(q_sol)) if q_sol else 0.0,
    }


def _payload_times(payload: dict) -> dict:
    fc = payload["rooms"][SLUG]["forecast"]
    xs = [datetime.fromisoformat(e["time"]).timestamp() for e in fc]
    temps = [e.get("temperature") for e in fc]
    powers = [e.get("heating_power") for e in fc]
    dts = np.diff(xs) if len(xs) > 1 else np.array([])
    return {
        "n": len(fc),
        "monotonic": bool(np.all(dts > 0)) if dts.size else True,
        "min_dt_s": float(np.min(dts)) if dts.size else 0.0,
        "max_dt_s": float(np.max(dts)) if dts.size else 0.0,
        "unique_times": int(len(set(xs))),
        "temp_none": int(sum(t is None for t in temps)),
        "power_none": int(sum(p is None for p in powers)),
        "temp_min": float(min(t for t in temps if t is not None)),
        "temp_max": float(max(t for t in temps if t is not None)),
    }


def _install_and_snap(engine: ControlEngine, *, outdoor, prices, ghi=None, cloud=None):
    kwargs = {
        "now": NOW,
        "outdoor_forecast": outdoor,
        "price_forecast": prices,
    }
    if ghi is not None:
        kwargs["ghi_forecast"] = ghi
        kwargs["ghi_now"] = ghi[0]
    if cloud is not None:
        kwargs["cloud_forecast"] = cloud
        kwargs["cloud_cover_now"] = cloud[0]
    engine.compute_actions({ROOM: 23.8}, outdoor[0], {ROOM: 23.5}, **kwargs)
    engine.mark_nmpc_busy()
    plan = engine.solve_nmpc_blocking()
    accepted = bool(plan.get("accepted"))
    applied = bool(engine.apply_nmpc_result(plan)) if accepted else False
    snap = engine.forecast_snapshot()
    return plan, accepted, applied, snap


def _plot(tag: str, hours, series: dict[str, np.ndarray], title: str) -> str:
    n = len(series)
    fig, axes = plt.subplots(n, 1, figsize=(11, 2.2 * n), sharex=True)
    if n == 1:
        axes = [axes]
    for ax, (label, y) in zip(axes, series.items()):
        ax.plot(hours[: len(y)], y, lw=1.4)
        ax.set_ylabel(label)
        ax.grid(True, alpha=0.3)
    axes[-1].set_xlabel("hours from now")
    fig.suptitle(title)
    fig.tight_layout()
    slug = "".join(c if c.isalnum() else "_" for c in title.lower())
    while "__" in slug:
        slug = slug.replace("__", "_")
    path = INSPECT / f"{tag}_{slug.strip('_')[:72]}.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return str(path.relative_to(ROOT))


def screenshot_p_kw(hours: np.ndarray) -> np.ndarray:
    """Smooth cooling bowl like the room-view Planned Power (~0 → −1.5 kW → 0)."""

    return -1.5 * np.exp(-0.5 * ((hours - 14.0) / 8.0) ** 2)


def zoh_slow(u: np.ndarray, m: int) -> np.ndarray:
    """Hold each block of ``m`` fast samples at the block mean (2 h stairs)."""

    out = np.empty_like(u)
    n = int(u.shape[0])
    m = max(int(m), 1)
    for start in range(0, n, m):
        stop = min(start + m, n)
        out[start:stop] = np.mean(u[start:stop], axis=0, keepdims=True)
    return out


def large_step_p_kw(hours: np.ndarray) -> np.ndarray:
    """Few 2 h-scale drops to −1.5 kW (stairs that still look like a dip)."""

    p = np.zeros_like(hours, dtype=float)
    p[(hours >= 4) & (hours < 8)] = -0.4
    p[(hours >= 8) & (hours < 12)] = -1.0
    p[(hours >= 12) & (hours < 24)] = -1.5
    p[(hours >= 24) & (hours < 28)] = -0.8
    p[(hours >= 28) & (hours < 32)] = -0.3
    return p


def cubic_spline_pp(y: np.ndarray, hours: np.ndarray) -> dict[str, float]:
    """Dense cubic spline p-p versus the 15 min knots (Chart.js-like overshoot)."""

    if y.size < 4:
        return {"knot_pp_K": 0.0, "spline_pp_K": 0.0, "extra_K": 0.0}
    from scipy.interpolate import CubicSpline

    knot_pp = float(np.max(y) - np.min(y))
    dense_t = np.linspace(float(hours[0]), float(hours[-1]), max(int(y.size) * 8, 32))
    sp = CubicSpline(hours[: y.size], y, bc_type="natural")
    yd = sp(dense_t)
    spline_pp = float(np.max(yd) - np.min(yd))
    return {
        "knot_pp_K": knot_pp,
        "spline_pp_K": spline_pp,
        "extra_K": float(spline_pp - knot_pp),
    }


def _apply_c_air(ctrl, frac: float) -> dict:
    """Patch the live plant capacitances (ControlEngine ignores room config)."""

    from heatingassistant.engine.const import SOLAR_WALL_FRACTION

    plant = ctrl._control_system
    model = plant._model
    n = plant._n_rooms
    room = model.rooms[ROOM]
    room.c_air_fraction = float(np.clip(frac, 0.01, 0.60))
    model._C, model._A, model._B_ext = model._build_matrices()
    model._B_sky_offset = model._build_sky_offset(model._C)
    plant._C_cap = np.array(model._C, dtype=float)
    plant._inv_C_cap = 1.0 / plant._C_cap
    plant._F = np.array(model._A, dtype=float) / plant._C_cap[:, np.newaxis]
    plant._src_C_cap = plant._C_cap[plant._src_room_idx]
    plant._G_d[:, :] = 0.0
    plant._G_d[:, 0] = model._B_ext * plant._inv_C_cap
    for i in range(n):
        rm = model.rooms[plant._room_list[i]]
        s_i = float(rm.solar_scale)
        wall_frac = SOLAR_WALL_FRACTION
        facade = float(rm.facade_solar_share) * float(rm.facade_absorptance)
        plant._G_d[i, 1 + i] = (1.0 - wall_frac) * s_i * plant._inv_C_cap[i]
        plant._G_d[n + i, 1 + i] = (wall_frac + facade) * s_i * plant._inv_C_cap[n + i]
        plant._G_d[i, 1 + n + i] = plant._inv_C_cap[i]
    plant._sky_offset_phys = np.array(model._B_sky_offset, dtype=float)
    eig = np.real(np.linalg.eigvals(plant._F))
    fast = float(np.min(eig))
    return {
        "c_air_fraction": float(room.c_air_fraction),
        "C_air": float(plant._C_cap[0]),
        "C_wall": float(plant._C_cap[n]),
        "eig_fast": fast,
        "tau_air_min": float((-1.0 / fast) / 60.0) if fast < 0 else None,
    }


def _u_from_kw(ctrl, p_kw: np.ndarray, outdoor_c: float = 18.0):
    src = ctrl._sources[0]
    q_cool = abs(
        float(src.smooth_thermal_power(-1.0, outdoor_c, ctrl._system._k_sigmoid))
    )
    q_heat = abs(
        float(src.smooth_thermal_power(1.0, outdoor_c, ctrl._system._k_sigmoid))
    )
    u = np.zeros((len(p_kw), 1), dtype=float)
    for i, p in enumerate(p_kw):
        w = float(p) * 1000.0
        if w < 0.0 and q_cool > 0.0:
            u[i, 0] = w / q_cool
        elif w > 0.0 and q_heat > 0.0:
            u[i, 0] = w / q_heat
        else:
            u[i, 0] = 0.0
    return np.clip(u, float(src.u_min), float(src.u_max)), q_cool, q_heat


def _day_night_jitter(hours: np.ndarray, t: np.ndarray) -> dict:
    """Split 15 min |ΔT| into local-day vs local-night (Copenhagen UTC+2)."""

    local_h = (NOW.hour + 2 + hours) % 24.0
    day = (local_h >= 6.0) & (local_h < 21.0)
    # diffs align with the *end* of each interval
    day_d = day[1:]
    d = np.diff(t)
    out = {}
    for name, mask in (("day", day_d), ("night", ~day_d)):
        if not np.any(mask):
            out[name] = {"max_abs_d": 0.0, "sign_flips": 0.0, "n": 0.0}
            continue
        sl = d[mask]
        signs = np.sign(sl)
        flips = int(np.sum((signs[1:] * signs[:-1]) < 0.0)) if sl.size > 1 else 0
        out[name] = {
            "max_abs_d": float(np.max(np.abs(sl))),
            "sign_flips": float(flips),
            "n": float(sl.size),
        }
    return out


def _setup_engine():
    engine = ControlEngine(_config())
    ctrl = engine._controller
    n_fast = int(ctrl.horizon)
    dt_s = float(ctrl._dt)
    weather = hourly_weather(NOW)
    dist = build_mpc_disturbance_inputs(
        outdoor_temp=18.0,
        weather_attrs={"forecast": weather, "cloud_coverage": 40.0},
        price_value=1.2,
        price_attrs={},
        solar_value=120.0,
        solar_attrs={},
        horizon=n_fast,
        dt_s=dt_s,
        now=NOW,
    )
    outdoor = dist["outdoor_forecast"]
    cloud = dist.get("cloud_forecast")
    from heatingassistant.engine.solar_forecast import compute_ghi_series

    ghi_fc, ghi_now = compute_ghi_series(hourly_ghi(NOW), 120.0, n_fast, dt_s, NOW)
    engine.compute_actions(
        {ROOM: 23.8},
        outdoor[0],
        {ROOM: 23.5},
        now=NOW,
        outdoor_forecast=outdoor,
        price_forecast=peaked_prices(n_fast, dt_s),
        ghi_forecast=ghi_fc,
        ghi_now=ghi_now,
        cloud_forecast=cloud,
        cloud_cover_now=(cloud[0] if cloud else 0.4),
    )
    ctrl = engine._controller
    snap = engine.forecast_snapshot()
    tout = list(snap["outdoor_forecast"])
    solar = list(snap["solar_forecast"])
    return engine, ctrl, n_fast, dt_s, tout, solar, ghi_fc, ghi_now, cloud, snap


def run_forced(tag: str) -> dict:
    """Force the screenshot U profile through the production remaining-U* path."""

    INSPECT.mkdir(parents=True, exist_ok=True)
    engine, ctrl, n_fast, dt_s, tout, solar, ghi_fc, ghi_now, cloud, snap = (
        _setup_engine()
    )
    hours = (np.arange(n_fast, dtype=float) + 1.0) * (dt_s / 3600.0)
    m = int(ctrl._timing.m)
    x0 = ctrl._ekf.x_hat.copy()

    p_smooth = screenshot_p_kw(hours)
    U_smooth, q_cool, q_heat = _u_from_kw(ctrl, p_smooth)
    U_stairs = zoh_slow(U_smooth, m)
    p_steps = large_step_p_kw(hours)
    U_steps, _, _ = _u_from_kw(ctrl, p_steps)
    U_steps = zoh_slow(U_steps, m)
    U_const = np.full((n_fast, 1), U_smooth.min(), dtype=float)

    def roll(U, x=None):
        return _roll(ctrl, U, tout, solar, x0=x)[:, 0]

    t_smooth = roll(U_smooth)
    t_stairs = roll(U_stairs)
    t_steps = roll(U_steps)
    t_const = roll(U_const)
    t_u0 = roll(np.zeros_like(U_smooth))

    ivp_stairs, _ = _ivp_roll(ctrl, U_stairs, tout, solar, x0=x0)
    ivp_err = float(np.max(np.abs(t_stairs - ivp_stairs[:, 0])))

    # Wall 5 K hotter than air (identified envelope far from filtered T).
    x_wall = x0.copy()
    n_rooms = int(ctrl._control_system._n_rooms)
    x_wall[n_rooms] = x_wall[0] + 5.0
    t_wall = roll(U_stairs, x=x_wall)

    # GHI coverage hole: 24 h then None, fallback ghi_now (docstring forbids this).
    ghi_short = list(ghi_fc or [])
    n_24 = int(round(24 * 3600 / dt_s))
    for i in range(n_24, len(ghi_short)):
        ghi_short[i] = None
    solar_leak = ctrl._forecast_solar(
        NOW,
        cloud_forecast=cloud,
        cloud_cover_now=(cloud[0] if cloud else 0.4),
        ghi_forecast=ghi_short,
        ghi_now=ghi_now,
    )
    t_leak = _roll(ctrl, U_stairs, tout, solar_leak, x0=x0)[:, 0]
    q_prod = _series(solar)
    q_leak = _series(solar_leak)

    # Interleaved None GHI: every other 15 min step falls back to ghi_now.
    ghi_alt = [
        (g if i % 2 == 0 else None) for i, g in enumerate(ghi_fc or [])
    ]
    solar_alt = ctrl._forecast_solar(
        NOW,
        cloud_forecast=cloud,
        cloud_cover_now=(cloud[0] if cloud else 0.4),
        ghi_forecast=ghi_alt,
        ghi_now=ghi_now,
    )
    t_alt = _roll(ctrl, U_smooth, tout, solar_alt, x0=x0)[:, 0]
    t_alt0 = _roll(ctrl, np.zeros_like(U_smooth), tout, solar_alt, x0=x0)[:, 0]
    q_alt = _series(solar_alt)

    # Short −1.5 kW dip (4 h) so T stays nearer the live 22–25 °C band.
    p_dip = -1.5 * np.exp(-0.5 * ((hours - 8.0) / 2.0) ** 2)
    U_dip, _, _ = _u_from_kw(ctrl, p_dip)
    U_dip_stairs = zoh_slow(U_dip, m)
    t_dip = roll(U_dip)
    t_dip_stairs = roll(U_dip_stairs)

    # c_air_fraction the engine never reads from room config.
    c_air_rows = {}
    t_c_air = {}
    for frac in (0.01, 0.05, 0.20):
        info = _apply_c_air(ctrl, frac)
        t_f = roll(U_stairs)
        c_air_rows[str(frac)] = {**info, **_jitter(t_f), "pp_K": float(np.max(t_f) - np.min(t_f))}
        t_c_air[frac] = t_f
    _apply_c_air(ctrl, DEFAULT_C_AIR_FRACTION)

    # Payload series includes the NOW bridge sample (EKF T, then predictions).
    ctrl._publish_plan_rollout(U_stairs, tout, solar)
    snap_f = {
        "dt": dt_s,
        "predictions": ctrl._predictions,
        "linearised_predictions": ctrl._linearised_predictions,
        "heating_schedule": ctrl._heating_schedule,
        "outdoor_forecast": tout,
        "solar_forecast": solar,
        "filtered_temperatures": {ROOM: 23.8},
        "update_interval": dt_s,
    }
    payload = build_app_forecast_payload(
        rooms=[{"name": ROOM, "setpoint": 23.5, "comfort_offset": 2.0, "enabled": True}],
        room_temperatures={ROOM: 23.8},
        outdoor_temp=float(tout[0]) if tout else 18.0,
        energy_price=1.2,
        snapshot=snap_f,
        now=NOW,
    )
    times = _payload_times(payload)
    t_payload = np.array(
        [float(e["temperature"]) for e in payload["rooms"][SLUG]["forecast"] if e.get("temperature") is not None],
        dtype=float,
    )
    p_payload = np.array(
        [
            float(e["heating_power"]) / 1000.0
            for e in payload["rooms"][SLUG]["forecast"]
            if e.get("heating_power") is not None
        ],
        dtype=float,
    )

    # Display P vs plant thermal Q (cooling should match).
    plant_q = []
    for i in range(n_fast):
        u_i = float(U_stairs[i, 0])
        src = ctrl._sources[0]
        plant_q.append(src.smooth_thermal_power(u_i, float(tout[i]), ctrl._system._k_sigmoid) / 1000.0)
    plant_q = np.array(plant_q, dtype=float)
    disp_q = p_payload[1 : 1 + n_fast] if p_payload.size > n_fast else p_payload[1:]

    ua_fast = abs(_eigenvalues(ctrl)["min_real"] * _eigenvalues(ctrl)["C_air"])
    eigs = _eigenvalues(ctrl)

    plots = [
        _plot(
            tag,
            hours,
            {
                "T 15-min U [°C]": t_smooth,
                "T 2h stairs U [°C]": t_stairs,
                "T large 2h steps [°C]": t_steps,
                "T const −1.5 kW [°C]": t_const,
                "T U=0 [°C]": t_u0,
            },
            "03a T under forced U profiles",
        ),
        _plot(
            tag,
            hours,
            {
                "P 15-min [kW]": (U_smooth[:, 0] * q_cool) / 1000.0,
                "P 2h stairs [kW]": (U_stairs[:, 0] * q_cool) / 1000.0,
                "P large steps [kW]": (U_steps[:, 0] * q_cool) / 1000.0,
                "q_solar [kW]": q_prod[:n_fast] / 1000.0,
            },
            "03b power and solar",
        ),
        _plot(
            tag,
            hours,
            {
                "c_air=0.01": t_c_air[0.01],
                "c_air=0.05 (engine)": t_c_air[0.05],
                "c_air=0.20": t_c_air[0.20],
            },
            "03c c_air_fraction sweep on 2h stairs",
        ),
        _plot(
            tag,
            hours,
            {
                "T production GHI [°C]": t_stairs,
                "T GHI-None tail leak [°C]": t_leak,
                "T interleaved GHI None + smooth U [°C]": t_alt,
                "T interleaved GHI None U=0 [°C]": t_alt0,
                "T wall+5K [°C]": t_wall,
            },
            "03d GHI leak interleaved None and wall offset",
        ),
        _plot(
            tag,
            hours,
            {
                "T short dip 15-min U [°C]": t_dip,
                "T short dip 2h stairs [°C]": t_dip_stairs,
                "P short dip [kW]": (U_dip[:, 0] * q_cool) / 1000.0,
            },
            "03e short 4h dip to -1.5 kW",
        ),
        _plot(
            tag,
            np.concatenate([[0.0], hours]),
            {
                "payload T [°C]": t_payload,
                "payload P [kW]": p_payload[: t_payload.size] if p_payload.size else p_payload,
            },
            "03f payload including NOW bridge",
        ),
    ]

    night_smooth = _day_night_jitter(hours, t_smooth)
    night_stairs = _day_night_jitter(hours, t_stairs)
    night_steps = _day_night_jitter(hours, t_steps)
    night_const = _day_night_jitter(hours, t_const)

    # First 6 h of 2 h stairs: show overshoot-then-settle, not 15 min ringing.
    head = []
    for i in range(min(24, n_fast)):
        head.append(
            {
                "h": round(float(hours[i]), 2),
                "T": round(float(t_stairs[i]), 3),
                "dT": None if i == 0 else round(float(t_stairs[i] - t_stairs[i - 1]), 3),
                "P_kW": round(float(U_stairs[i, 0] * q_cool / 1000.0), 3),
                "q_solar_W": round(float(q_prod[i]) if i < q_prod.size else 0.0, 1),
            }
        )

    report = {
        "tag": tag,
        "now": NOW.isoformat(),
        "q_cool_W": q_cool,
        "q_heat_W": q_heat,
        "ua_fast_W_per_K": ua_fast,
        "expected_dT_per_kW": (1000.0 / ua_fast) if ua_fast else None,
        "eigenvalues": eigs,
        "smooth_15min": {
            **_jitter(t_smooth),
            "pp_K": float(np.max(t_smooth) - np.min(t_smooth)),
            "day_night": night_smooth,
            "spline": cubic_spline_pp(t_smooth, hours),
        },
        "stairs_2h": {
            **_jitter(t_stairs),
            "pp_K": float(np.max(t_stairs) - np.min(t_stairs)),
            "day_night": night_stairs,
            "ivp_err_K": ivp_err,
            "spline": cubic_spline_pp(t_stairs, hours),
            "U_unique": int(len(np.unique(np.round(U_stairs.reshape(-1), 6)))),
        },
        "large_2h_steps": {
            **_jitter(t_steps),
            "pp_K": float(np.max(t_steps) - np.min(t_steps)),
            "day_night": night_steps,
            "spline": cubic_spline_pp(t_steps, hours),
        },
        "const_minus_1_5": {
            **_jitter(t_const),
            "pp_K": float(np.max(t_const) - np.min(t_const)),
            "day_night": night_const,
        },
        "u0_disturbances_only": {
            **_jitter(t_u0),
            "pp_K": float(np.max(t_u0) - np.min(t_u0)),
            "day_night": _day_night_jitter(hours, t_u0),
        },
        "wall_plus_5K": {**_jitter(t_wall), "pp_K": float(np.max(t_wall) - np.min(t_wall))},
        "ghi_none_tail": {
            **_jitter(t_leak),
            "max_T_vs_stairs": float(np.max(np.abs(t_leak - t_stairs))),
            "qsolar_max_prod": float(np.max(q_prod)) if q_prod.size else 0.0,
            "qsolar_max_leak": float(np.max(q_leak)) if q_leak.size else 0.0,
        },
        "ghi_interleaved_none": {
            "with_smooth_U": {
                **_jitter(t_alt),
                "pp_K": float(np.max(t_alt) - np.min(t_alt)),
                "day_night": _day_night_jitter(hours, t_alt),
            },
            "U0": {
                **_jitter(t_alt0),
                "pp_K": float(np.max(t_alt0) - np.min(t_alt0)),
                "day_night": _day_night_jitter(hours, t_alt0),
            },
            "qsolar": _jitter(q_alt),
            "qsolar_max_W": float(np.max(q_alt)) if q_alt.size else 0.0,
        },
        "short_dip": {
            "smooth": {
                **_jitter(t_dip),
                "pp_K": float(np.max(t_dip) - np.min(t_dip)),
                "T_min": float(np.min(t_dip)),
                "T_max": float(np.max(t_dip)),
            },
            "stairs": {
                **_jitter(t_dip_stairs),
                "pp_K": float(np.max(t_dip_stairs) - np.min(t_dip_stairs)),
                "T_min": float(np.min(t_dip_stairs)),
                "T_max": float(np.max(t_dip_stairs)),
            },
        },
        "c_air_sweep": c_air_rows,
        "payload": {
            **times,
            "jitter_T": _jitter(t_payload),
            "jitter_P": _jitter(p_payload),
            "bridge_minus_first_pred_K": (
                float(t_payload[1] - t_payload[0]) if t_payload.size > 1 else None
            ),
        },
        "display_vs_plant_kW": {
            "max_abs": float(np.max(np.abs(disp_q[: len(plant_q)] - plant_q[: len(disp_q)])))
            if disp_q.size and plant_q.size
            else None,
        },
        "head_6h_stairs": head,
        "plots": plots,
        "c_air_fraction_note": (
            "Room config c_air_fraction is ignored by ControlEngine "
            f"(_build_house_model); plant uses DEFAULT {DEFAULT_C_AIR_FRACTION}."
        ),
    }
    (INSPECT / f"{tag}_report.json").write_text(json.dumps(report, indent=2) + "\n")
    (INSPECT / f"{tag}_report.md").write_text(_markdown_forced(report))
    return report


def _markdown_forced(r: dict) -> str:
    s = r["smooth_15min"]
    st = r["stairs_2h"]
    lg = r["large_2h_steps"]
    c = r["const_minus_1_5"]
    u0 = r["u0_disturbances_only"]
    e = r["eigenvalues"]
    lines = [
        f"# Forecast oscillation diagnostic ({r['tag']}) — forced screenshot U",
        "",
        f"NOW = `{r['now']}`; q_cool = {r['q_cool_W']:.0f} W; "
        f"UA_fast ≈ {r['ua_fast_W_per_K']:.1f} W/K; "
        f"expected ΔT ≈ {r['expected_dT_per_kW']:.2f} K per kW step",
        "",
        "## Plant",
        f"- C_air = {e['C_air']:.3e} J/K; τ_air ≈ {(-1.0 / e['min_real']) / 60.0:.1f} min; "
        f"n_int = {e['n_int']}; dt = {e['dt_s']:.0f} s",
        "",
        "## Forced U through production roll_fast_air_path",
        f"- 15-min smooth bowl: max |ΔT| = {s['max_abs_d']:.3f} K; flips = {s['sign_flips']:.0f}; "
        f"p-p = {s['pp_K']:.3f} K; night max |ΔT| = {s['day_night']['night']['max_abs_d']:.3f} K",
        f"- 2 h stairs of the same bowl: max |ΔT| = {st['max_abs_d']:.3f} K; flips = {st['sign_flips']:.0f}; "
        f"p-p = {st['pp_K']:.3f} K; vs RK45 {st['ivp_err_K']:.4f} K; "
        f"night max |ΔT| = {st['day_night']['night']['max_abs_d']:.3f} K",
        f"- Large 2 h steps to −1.5 kW: max |ΔT| = {lg['max_abs_d']:.3f} K; flips = {lg['sign_flips']:.0f}; "
        f"p-p = {lg['pp_K']:.3f} K; night max |ΔT| = {lg['day_night']['night']['max_abs_d']:.3f} K",
        f"- Constant min(U) ≈ −1.5 kW: max |ΔT| = {c['max_abs_d']:.3f} K; flips = {c['sign_flips']:.0f}; "
        f"night max |ΔT| = {c['day_night']['night']['max_abs_d']:.3f} K",
        f"- U = 0 (disturbances only): max |ΔT| = {u0['max_abs_d']:.3f} K; flips = {u0['sign_flips']:.0f}; "
        f"p-p = {u0['pp_K']:.3f} K",
        f"- short 4 h dip to −1.5 kW: max |ΔT| = {r['short_dip']['smooth']['max_abs_d']:.3f} K "
        f"(stairs {r['short_dip']['stairs']['max_abs_d']:.3f} K); "
        f"T ∈ [{r['short_dip']['smooth']['T_min']:.2f}, {r['short_dip']['smooth']['T_max']:.2f}] °C",
        "",
        "## Cubic spline (Chart.js-like extra wiggle)",
        f"- smooth knots p-p {s['spline']['knot_pp_K']:.3f} K → spline {s['spline']['spline_pp_K']:.3f} K "
        f"(+{s['spline']['extra_K']:.3f} K)",
        f"- stairs knots p-p {st['spline']['knot_pp_K']:.3f} K → spline {st['spline']['spline_pp_K']:.3f} K "
        f"(+{st['spline']['extra_K']:.3f} K)",
        "",
        "## c_air_fraction (engine default 0.05; config ignored)",
    ]
    for k, v in r["c_air_sweep"].items():
        lines.append(
            f"- {k}: max |ΔT| = {v['max_abs_d']:.3f} K; flips = {v['sign_flips']:.0f}; "
            f"τ_air = {v['tau_air_min']:.1f} min; C_air = {v['C_air']:.3e} J/K"
        )
    lines += [
        "",
        "## GHI None tail (fallback ghi_now), interleaved None, wall offset",
        f"- tail leak vs production stairs max |ΔT| = {r['ghi_none_tail']['max_T_vs_stairs']:.3f} K",
        f"- interleaved None + smooth U: max |ΔT| = {r['ghi_interleaved_none']['with_smooth_U']['max_abs_d']:.3f} K; "
        f"flips = {r['ghi_interleaved_none']['with_smooth_U']['sign_flips']:.0f}; "
        f"q_solar max |Δ| = {r['ghi_interleaved_none']['qsolar']['max_abs_d']:.1f} W",
        f"- interleaved None U=0: max |ΔT| = {r['ghi_interleaved_none']['U0']['max_abs_d']:.3f} K; "
        f"flips = {r['ghi_interleaved_none']['U0']['sign_flips']:.0f}",
        f"- wall T = air+5 K: max |ΔT| = {r['wall_plus_5K']['max_abs_d']:.3f} K; "
        f"p-p = {r['wall_plus_5K']['pp_K']:.3f} K",
        "",
        "## Payload (NOW bridge + 15 min predictions)",
        f"- T max |ΔT| = {r['payload']['jitter_T']['max_abs_d']:.3f} K; "
        f"bridge→first pred = {r['payload']['bridge_minus_first_pred_K']:.3f} K",
        f"- times monotonic = {r['payload']['monotonic']}; "
        f"dt ∈ [{r['payload']['min_dt_s']:.0f}, {r['payload']['max_dt_s']:.0f}] s",
        f"- display P vs plant Q max |Δ| = {r['display_vs_plant_kW']['max_abs']} kW",
        "",
        r["c_air_fraction_note"],
        "",
    ]
    return "\n".join(lines) + "\n"


def run(tag: str) -> dict:
    INSPECT.mkdir(parents=True, exist_ok=True)
    engine = ControlEngine(_config())
    ctrl = engine._controller
    n_fast = int(ctrl.horizon)
    dt_s = float(ctrl._dt)
    hours = (np.arange(n_fast, dtype=float) + 1.0) * (dt_s / 3600.0)

    weather = hourly_weather(NOW)
    dist = build_mpc_disturbance_inputs(
        outdoor_temp=18.0,
        weather_attrs={"forecast": weather, "cloud_coverage": 40.0},
        price_value=1.2,
        price_attrs={},
        solar_value=120.0,
        solar_attrs={},
        horizon=n_fast,
        dt_s=dt_s,
        now=NOW,
    )
    outdoor = dist["outdoor_forecast"]
    cloud = dist.get("cloud_forecast")
    ghi_series = hourly_ghi(NOW)
    from heatingassistant.engine.solar_forecast import compute_ghi_series

    ghi_fc, ghi_now = compute_ghi_series(ghi_series, 120.0, n_fast, dt_s, NOW)
    prices = peaked_prices(n_fast, dt_s)

    eigs = _eigenvalues(ctrl)

    # --- Frozen U, constant d: discrete map must not ring ---
    U0 = np.full((n_fast, 1), -0.22, dtype=float)
    outdoor_flat = [18.0] * n_fast
    solar_zero = [{ROOM: 0.0} for _ in range(n_fast)]
    air_frozen = _roll(ctrl, U0, outdoor_flat, solar_zero)
    t_frozen = air_frozen[:, 0]
    ivp_frozen, _ = _ivp_roll(ctrl, U0, outdoor_flat, solar_zero)
    frozen_vs_ivp = float(np.max(np.abs(t_frozen - ivp_frozen[:, 0])))

    # n_int sweep on the frozen map
    n_int_orig = int(ctrl._n_int_steps)
    n_int_rows = {}
    for n_int in (1, 10, 40, 100):
        ctrl._n_int_steps = n_int
        ctrl._control_system._n_int_steps = n_int
        air_n = _roll(ctrl, U0, outdoor_flat, solar_zero)
        n_int_rows[str(n_int)] = {
            **_jitter(air_n[:, 0]),
            "max_vs_100": None,
        }
    ctrl._n_int_steps = 100
    ctrl._control_system._n_int_steps = 100
    t_ref = _roll(ctrl, U0, outdoor_flat, solar_zero)[:, 0]
    for n_int in (1, 10, 40, 100):
        ctrl._n_int_steps = n_int
        ctrl._control_system._n_int_steps = n_int
        t_n = _roll(ctrl, U0, outdoor_flat, solar_zero)[:, 0]
        n_int_rows[str(n_int)]["max_vs_100"] = float(np.max(np.abs(t_n - t_ref)))
    ctrl._n_int_steps = n_int_orig
    ctrl._control_system._n_int_steps = n_int_orig

    # --- Production remaining-U* with screenshot-like disturbances ---
    plan, accepted, applied, snap = _install_and_snap(
        engine,
        outdoor=outdoor,
        prices=prices,
        ghi=ghi_fc,
        cloud=cloud,
    )
    ctrl = engine._controller
    t_prod = _series(snap["predictions"])
    p_prod = _series(snap["heating_schedule"]) / 1000.0
    tout_prod = np.array(snap["outdoor_forecast"], dtype=float)
    solar_prod = _series(snap["solar_forecast"])
    U_rem = ctrl._forecast_U(n_fast)

    air_re = _roll(ctrl, U_rem, list(tout_prod), list(snap["solar_forecast"]))
    t_re = air_re[:, 0]
    resim_err = float(np.max(np.abs(t_prod - t_re))) if t_prod.size == t_re.size else None

    ivp_prod, _ = _ivp_roll(ctrl, U_rem, list(tout_prod), list(snap["solar_forecast"]))
    ivp_err = float(np.max(np.abs(t_prod - ivp_prod[:, 0]))) if t_prod.size == ivp_prod.shape[0] else None

    energy = _energy_residual(
        ctrl, U_rem, list(tout_prod), list(snap["solar_forecast"]), air_re
    )

    # Ablations of the accepted U*: zero solar / persist outdoor / 2 h outdoor ZOH
    solar_off = [{ROOM: 0.0} for _ in range(n_fast)]
    t_nosolar = _roll(ctrl, U_rem, list(tout_prod), solar_off)[:, 0]
    outdoor_hold = [float(tout_prod[0])] * n_fast
    t_noout = _roll(ctrl, U_rem, outdoor_hold, list(snap["solar_forecast"]))[:, 0]
    m = int(ctrl._timing.m)
    outdoor_zoh = []
    for i in range(n_fast):
        j = i - (i % m)
        outdoor_zoh.append(float(tout_prod[j]))
    t_zoh = _roll(ctrl, U_rem, outdoor_zoh, list(snap["solar_forecast"]))[:, 0]

    d_15 = _d_fast(ctrl, list(tout_prod), list(snap["solar_forecast"]))
    with ctrl._nmpc_lock:
        k0 = int(ctrl._nmpc_k)
    d_slow = mean_hold_d(d_15, k0, m)
    t_slow_d = _roll_d(ctrl, U_rem, d_slow)[:, 0]
    solar_slow = np.array([float(d[1]) for d in d_slow], dtype=float)
    tout_slow = np.array([float(d[0]) for d in d_slow], dtype=float)

    payload = build_app_forecast_payload(
        rooms=[{"name": ROOM, "setpoint": 23.5, "comfort_offset": 2.0, "enabled": True}],
        room_temperatures={ROOM: 23.8},
        outdoor_temp=float(tout_prod[0]) if tout_prod.size else 18.0,
        energy_price=1.2,
        snapshot=snap,
        now=NOW,
    )
    times = _payload_times(payload)

    # Weather interpolation zigzag check
    outdoor_j = _jitter(np.asarray(outdoor, dtype=float))
    ghi_vals = np.array([g if g is not None else math.nan for g in (ghi_fc or [])], dtype=float)

    plots = [
        _plot(
            tag,
            hours,
            {
                "T forecast [°C]": t_prod,
                "Planned P [kW]": p_prod[: t_prod.size],
                "T_out [°C]": tout_prod[: t_prod.size],
                "q_solar [W]": solar_prod[: t_prod.size],
            },
            "01 production: T vs U vs disturbances",
        ),
        _plot(
            tag,
            hours,
            {
                "frozen U,d T [°C]": t_frozen,
                "solve_ivp [°C]": ivp_frozen[:, 0],
                "prod T [°C]": t_prod,
                "prod ivp [°C]": ivp_prod[:, 0],
            },
            "01 integrators: implicit Euler vs RK45",
        ),
        _plot(
            tag,
            hours,
            {
                "production": t_prod,
                "U* no solar": t_nosolar,
                "U* persist T_out": t_noout,
                "U* 2h T_out ZOH": t_zoh,
            },
            f"{tag} ablations: which input moves T",
        ),
        _plot(
            tag,
            hours,
            {
                "T 15-min d [°C]": t_prod,
                "T slow-held d [°C]": t_slow_d,
                "q_solar 15-min [W]": solar_prod[: t_prod.size],
                "q_solar slow-held [W]": solar_slow[: t_prod.size],
            },
            f"{tag} candidate: hold d on the U grid",
        ),
    ]

    report = {
        "tag": tag,
        "now": NOW.isoformat(),
        "accepted": accepted,
        "applied": applied,
        "plan_success": plan.get("success"),
        "eigenvalues": eigs,
        "frozen": {
            "jitter": _jitter(t_frozen),
            "max_vs_ivp_K": frozen_vs_ivp,
            "n_int": n_int_rows,
        },
        "production": {
            "jitter_T": _jitter(t_prod),
            "jitter_P_kW": _jitter(p_prod),
            "jitter_Tout": _jitter(tout_prod),
            "jitter_qsolar": _jitter(solar_prod),
            "T_min": float(np.min(t_prod)) if t_prod.size else None,
            "T_max": float(np.max(t_prod)) if t_prod.size else None,
            "P_min_kW": float(np.min(p_prod)) if p_prod.size else None,
            "P_max_kW": float(np.max(p_prod)) if p_prod.size else None,
            "qsolar_min_W": float(np.min(solar_prod)) if solar_prod.size else None,
            "qsolar_max_W": float(np.max(solar_prod)) if solar_prod.size else None,
            "resim_err_K": resim_err,
            "ivp_err_K": ivp_err,
            "nmpc_k": int(ctrl._nmpc_k),
            "U_min": float(np.min(U_rem)) if U_rem.size else None,
            "U_max": float(np.max(U_rem)) if U_rem.size else None,
            "U_unique_slow": int(len(np.unique(np.round(U_rem.reshape(-1), 6)))),
        },
        "ablations": {
            "no_solar": _jitter(t_nosolar),
            "persist_outdoor": _jitter(t_noout),
            "outdoor_2h_zoh": _jitter(t_zoh),
            "max_T_vs_nosolar": float(np.max(np.abs(t_prod - t_nosolar))),
            "max_T_vs_persist_out": float(np.max(np.abs(t_prod - t_noout))),
            "max_T_vs_2h_zoh": float(np.max(np.abs(t_prod - t_zoh))),
        },
        "candidate_slow_d": {
            "jitter_T": _jitter(t_slow_d),
            "jitter_qsolar": _jitter(solar_slow),
            "jitter_Tout": _jitter(tout_slow),
            "max_T_vs_production": float(np.max(np.abs(t_prod - t_slow_d))),
            "T_head": [round(float(v), 3) for v in t_prod[:16]],
            "T_slow_head": [round(float(v), 3) for v in t_slow_d[:16]],
        },
        "energy_residual": energy,
        "payload_times": times,
        "weather_interp": {
            "outdoor": outdoor_j,
            "ghi_finite": int(np.sum(np.isfinite(ghi_vals))),
            "ghi_jitter": _jitter(ghi_vals[np.isfinite(ghi_vals)]),
        },
        "plots": plots,
        "c_air_fraction_note": (
            "Room config c_air_fraction is ignored by ControlEngine "
            f"(_build_house_model); plant uses DEFAULT {DEFAULT_C_AIR_FRACTION}."
        ),
    }
    (INSPECT / f"{tag}_report.json").write_text(json.dumps(report, indent=2) + "\n")
    (INSPECT / f"{tag}_report.md").write_text(_markdown(report))
    return report


def _markdown(r: dict) -> str:
    p = r["production"]
    f = r["frozen"]
    a = r["ablations"]
    e = r["eigenvalues"]
    lines = [
        f"# Forecast oscillation diagnostic ({r['tag']})",
        "",
        f"NOW = `{r['now']}`; NMPC accepted={r['accepted']} applied={r['applied']}",
        "",
        "## Discrete map (frozen U=−0.22, T_out=18, q_solar=0)",
        f"- max |ΔT| = {f['jitter']['max_abs_d']:.4f} K; sign flips = {f['jitter']['sign_flips']:.0f}",
        f"- implicit Euler vs RK45 max |ΔT| = {f['max_vs_ivp_K']:.4f} K",
        f"- n_int vs 100: " + ", ".join(
            f"{k}→{v['max_vs_100']:.4f} K" for k, v in f["n_int"].items()
        ),
        "",
        "## Plant",
        f"- eig max real = {e['max_real']:.4g}; any unstable = {e['any_unstable']}; "
        f"max |Im| = {e['max_abs_imag']:.4g}",
        f"- C_air = {e['C_air']:.3e} J/K; C_wall = {e['C_wall']:.3e} J/K; "
        f"τ_em = {e['emitter_tau']:.1f} s; n_int = {e['n_int']}; dt = {e['dt_s']:.0f} s",
        "",
        "## Production remaining-U* resim (screenshot-like weather + GHI)",
        f"- T ∈ [{p['T_min']:.2f}, {p['T_max']:.2f}] °C; max |ΔT| = {p['jitter_T']['max_abs_d']:.3f} K; "
        f"sign flips = {p['jitter_T']['sign_flips']:.0f}",
        f"- P ∈ [{p['P_min_kW']:.3f}, {p['P_max_kW']:.3f}] kW; max |ΔP| = {p['jitter_P_kW']['max_abs_d']:.3f} kW; "
        f"sign flips = {p['jitter_P_kW']['sign_flips']:.0f}",
        f"- q_solar ∈ [{p['qsolar_min_W']:.1f}, {p['qsolar_max_W']:.1f}] W; "
        f"max |Δq| = {p['jitter_qsolar']['max_abs_d']:.1f} W; sign flips = {p['jitter_qsolar']['sign_flips']:.0f}",
        f"- T_out max |Δ| = {p['jitter_Tout']['max_abs_d']:.3f} K; sign flips = {p['jitter_Tout']['sign_flips']:.0f}",
        f"- vs independent resim {p['resim_err_K']:.4f} K; vs RK45 {p['ivp_err_K']:.4f} K",
        f"- remaining U ∈ [{p['U_min']:.3f}, {p['U_max']:.3f}]; unique slow values = {p['U_unique_slow']}",
        "",
        "## Ablations (same U*)",
        f"- no solar: max |ΔT| = {a['no_solar']['max_abs_d']:.3f} K, flips = {a['no_solar']['sign_flips']:.0f} "
        f"(moves production by {a['max_T_vs_nosolar']:.3f} K)",
        f"- persist T_out: max |ΔT| = {a['persist_outdoor']['max_abs_d']:.3f} K, flips = {a['persist_outdoor']['sign_flips']:.0f}",
        f"- 2 h T_out ZOH: max |ΔT| = {a['outdoor_2h_zoh']['max_abs_d']:.3f} K "
        f"(vs 15 min outdoor {a['max_T_vs_2h_zoh']:.3f} K)",
        "",
        "## Candidate: hold d on the same slow grid as U*",
        f"- T max |ΔT| = {r['candidate_slow_d']['jitter_T']['max_abs_d']:.3f} K; "
        f"sign flips = {r['candidate_slow_d']['jitter_T']['sign_flips']:.0f} "
        f"(production {p['jitter_T']['max_abs_d']:.3f} K / {p['jitter_T']['sign_flips']:.0f} flips)",
        f"- q_solar max |Δq| = {r['candidate_slow_d']['jitter_qsolar']['max_abs_d']:.1f} W "
        f"(production {p['jitter_qsolar']['max_abs_d']:.1f} W)",
        f"- vs production max |ΔT| = {r['candidate_slow_d']['max_T_vs_production']:.3f} K",
        "",
        "## Energy / timestamps",
        f"- implicit-Euler storage vs f residual RMS {r['energy_residual']['rms_W']:.3f} W, "
        f"max {r['energy_residual']['max_abs_W']:.3f} W",
        f"- payload times monotonic = {r['payload_times']['monotonic']}; "
        f"dt ∈ [{r['payload_times']['min_dt_s']:.0f}, {r['payload_times']['max_dt_s']:.0f}] s",
        "",
        r["c_air_fraction_note"],
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="01")
    args = parser.parse_args()
    report = run_forced(args.tag) if args.tag == "03" else run(args.tag)
    print((INSPECT / f"{args.tag}_report.md").read_text())
    print("plots:", report["plots"])


if __name__ == "__main__":
    main()
