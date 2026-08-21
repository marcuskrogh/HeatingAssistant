#!/usr/bin/env python3
"""SWD-417 measure sandbox: Forecast jitter vs integrator substeps / ROM.

Isolation tree only. Wraps production ControlEngine / MeanOcp / EKF;
does not edit production source. `--fixed-u` solves once then re-rolls T.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from heatingassistant.engine.const import (  # noqa: E402
    DEFAULT_NMPC_FAST_SUBSTEPS,
    DEFAULT_NMPC_HORIZON_H,
    DEFAULT_NMPC_PERIOD,
    DEFAULT_SMOOTHING_WEIGHT,
)
from heatingassistant.app.forecast_payload import build_app_forecast_payload  # noqa: E402
from heatingassistant.engine.control_loop import ControlEngine  # noqa: E402
from heatingassistant.engine.integrator import implicit_euler_step  # noqa: E402
from heatingassistant.engine.naming import room_slug  # noqa: E402

HERE = Path(__file__).resolve().parent
INSPECT = HERE / "inspect"
NOW = datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc)
ROOM = "Living Room"
BASELINE_N_INT = 10
CANDIDATE_N_INT = (1, 10, 40)
FIXED_U_N_INT = (1, 10, 40, 100)
APP_SMOOTHING = 0.05  # runtime.py fallback when the option is unset
ENGINE_SMOOTHING = DEFAULT_SMOOTHING_WEIGHT  # 0.1; Tuning UI default
REFERENCE_N_INT = 100


def _config(*, smoothing_weight: float | None = None) -> dict:
    cfg: dict = {
        "nmpc_period": DEFAULT_NMPC_PERIOD,
        "nmpc_fast_substeps": DEFAULT_NMPC_FAST_SUBSTEPS,
        "nmpc_horizon_h": DEFAULT_NMPC_HORIZON_H,
        "latitude": 55.67,
        "longitude": 12.57,
        "energy_price_weight": 1.0,
        "rooms": [
            {
                "name": ROOM,
                "setpoint": 23.5,
                "comfort_offset": 2.0,
                "temperature": 25.2,
                "solar_exposure": "high",
                "solar_facing": 180.0,
            }
        ],
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
    if smoothing_weight is not None:
        cfg["smoothing_weight"] = float(smoothing_weight)
    return cfg


def _set_n_int(engine: ControlEngine, n_int: int) -> None:
    ctrl = engine._controller
    n = int(n_int)
    ctrl._n_int_steps = n
    ctrl._system._n_int_steps = n
    ctrl._control_system._n_int_steps = n
    ekf = getattr(ctrl, "_ekf", None)
    params = getattr(ekf, "params", None)
    if params is not None and hasattr(params, "n_steps"):
        try:
            params.n_steps = n
        except Exception:
            object.__setattr__(params, "n_steps", n)


def _series(snap: dict, key: str) -> np.ndarray:
    rows = snap[key]
    return np.array([float(step[ROOM]) for step in rows], dtype=float)


def _jitter(temps: np.ndarray, dt_h: float) -> dict[str, float]:
    if temps.size < 2:
        return {
            "n": float(temps.size),
            "max_abs_dT_K": 0.0,
            "rms_dT_K": 0.0,
            "sign_flips": 0.0,
            "p95_abs_dT_K": 0.0,
            "max_abs_dT_per_h": 0.0,
        }
    dT = np.diff(temps)
    signs = np.sign(dT)
    flips = int(np.sum((signs[1:] * signs[:-1]) < 0.0))
    abs_dT = np.abs(dT)
    return {
        "n": float(temps.size),
        "max_abs_dT_K": float(np.max(abs_dT)),
        "rms_dT_K": float(np.sqrt(np.mean(dT * dT))),
        "sign_flips": float(flips),
        "p95_abs_dT_K": float(np.percentile(abs_dT, 95)),
        "max_abs_dT_per_h": float(np.max(abs_dT) / dt_h),
    }


def _u_hold_hours(watts: np.ndarray, dt_h: float) -> float:
    if watts.size == 0:
        return 0.0
    runs = []
    run = 1
    for i in range(1, watts.size):
        if abs(watts[i] - watts[i - 1]) < 1.0:
            run += 1
        else:
            runs.append(run)
            run = 1
    runs.append(run)
    return float(np.median(runs) * dt_h)


def peaked_prices(n_fast: int, dt_s: float) -> list[float]:
    """Two forecast peaks (~1.2→2.6), matching the room-view price shape."""

    hours = (np.arange(n_fast, dtype=float) + 0.5) * (dt_s / 3600.0)
    base = 1.15
    p1 = 1.25 * np.exp(-0.5 * ((hours - 8.0) / 2.0) ** 2)
    p2 = 1.45 * np.exp(-0.5 * ((hours - 20.0) / 2.5) ** 2)
    return (base + p1 + p2).tolist()


def _price_series(kind: str, n_fast: int, dt_s: float) -> list[float]:
    if kind == "peaked":
        return peaked_prices(n_fast, dt_s)
    return [2.0] * n_fast


def _max_slow_step_kw(watts: np.ndarray, m: int) -> float:
    if watts.size < 2 * m:
        return 0.0
    slow = watts[::m]
    if slow.size < 2:
        return 0.0
    return float(np.max(np.abs(np.diff(slow))) / 1000.0)


def run_one(
    n_int: int,
    *,
    smoothing_weight: float | None = None,
    price_kind: str = "flat",
) -> dict:
    engine = ControlEngine(_config(smoothing_weight=smoothing_weight))
    _set_n_int(engine, n_int)
    n_fast = engine._controller.horizon
    dt_s = float(engine._controller._dt)
    m = int(engine._controller._timing.m)
    outdoor = [22.0] * n_fast
    prices = _price_series(price_kind, n_fast, dt_s)
    used_s = float(engine._controller._smoothing_weight)
    engine.compute_actions(
        {ROOM: 25.2},
        22.0,
        {ROOM: 23.5},
        now=NOW,
        outdoor_forecast=outdoor,
        price_forecast=prices,
    )
    idle = engine.forecast_snapshot()
    engine.mark_nmpc_busy()
    plan = engine.solve_nmpc_blocking()
    accepted = bool(plan.get("accepted"))
    applied = bool(engine.apply_nmpc_result(plan)) if accepted else False
    snap = engine.forecast_snapshot()
    temps = _series(snap, "predictions")
    watts = _series(snap, "heating_schedule")
    idle_temps = _series(idle, "predictions")
    hours = (np.arange(temps.size, dtype=float) + 1.0) * (dt_s / 3600.0)
    metrics = _jitter(temps, dt_s / 3600.0)
    s_label = f"s_rom={used_s:g}"
    n_label = f"n_int={n_int}"
    label = s_label if smoothing_weight is not None else n_label
    metrics.update(
        {
            "label": label,
            "n_int": n_int,
            "smoothing_weight": used_s,
            "price_kind": price_kind,
            "accepted": accepted,
            "applied": applied,
            "fun": float(plan.get("fun", float("nan"))),
            "cost_zero": float(plan.get("cost_zero", float("nan"))),
            "min_W": float(np.min(watts)) if watts.size else 0.0,
            "max_W": float(np.max(watts)) if watts.size else 0.0,
            "max_slow_step_kW": _max_slow_step_kw(watts, m),
            "u_hold_h": _u_hold_hours(watts, dt_s / 3600.0),
            "idle_max_T": float(np.max(idle_temps)) if idle_temps.size else float("nan"),
            "plan_max_T": float(np.max(temps)) if temps.size else float("nan"),
            "dt_s": dt_s,
        }
    )
    return {
        "metrics": metrics,
        "hours": hours,
        "temps": temps,
        "watts": watts,
        "prices": np.asarray(prices, dtype=float),
        "idle_temps": idle_temps,
        "label": label,
    }


def _freeze_plan(engine: ControlEngine) -> dict:
    """Copy U*, x0, and disturbance sequences after an accepted solve."""

    ctrl = engine._controller
    U_fast = np.asarray(ctrl._nmpc_U_fast, dtype=float).copy()
    T_ocp = np.asarray(ctrl._nmpc_T_ref, dtype=float).copy()
    solar = [dict(s) for s in (ctrl._solar_forecast or [])]
    outdoor = [float(v) for v in (ctrl._outdoor_forecast or [])]
    return {
        "U_fast": U_fast,
        "T_ocp": T_ocp,
        "x0": np.asarray(ctrl._ekf.x_hat, dtype=float).copy(),
        "outdoor": outdoor,
        "solar": solar,
        "room_list": list(ctrl._system._room_list),
        "n_rooms": int(ctrl._system._n_rooms),
        "dt_s": float(ctrl._dt),
        "nu": int(ctrl._system.nu),
    }


def _roll_frozen(engine: ControlEngine, frozen: dict, n_int: int) -> np.ndarray:
    """Open-loop nonlinear roll of a frozen U* at a chosen integrator density."""

    _set_n_int(engine, n_int)
    ctrl = engine._controller
    x_hat = ctrl._ekf.x_hat
    saved = np.asarray(x_hat, dtype=float).copy()
    x_hat[:] = frozen["x0"]
    try:
        preds = ctrl._compute_nonlinear_predictions(
            frozen["U_fast"],
            frozen["outdoor"],
            frozen["solar"],
            frozen["room_list"],
            frozen["n_rooms"],
        )
    finally:
        x_hat[:] = saved
    return np.array([float(step[ROOM]) for step in preds], dtype=float)


def _dense_roll(engine: ControlEngine, frozen: dict, n_int: int) -> tuple[np.ndarray, np.ndarray]:
    """Record air temperature after every implicit-Euler substep (same U*, d)."""

    _set_n_int(engine, n_int)
    ctrl = engine._controller
    sde = ctrl._system
    x = frozen["x0"].copy()
    p = np.array([], dtype=float)
    dt = float(frozen["dt_s"])
    h = dt / float(n_int)
    U_fast = frozen["U_fast"]
    outdoor = frozen["outdoor"]
    solar = frozen["solar"]
    n_fast = int(U_fast.shape[0])
    hours = np.empty(n_fast * n_int, dtype=float)
    temps = np.empty(n_fast * n_int, dtype=float)
    t = 0.0
    idx = 0
    for k in range(n_fast):
        u_k = U_fast[k]
        outdoor_k = outdoor[k] if k < len(outdoor) else outdoor[-1]
        solar_k = solar[k] if k < len(solar) else solar[-1]
        d_k = ctrl._control_system.disturbance_vector(outdoor_k, solar_k)
        rhs = lambda xx, u=u_k, d=d_k: sde.f(xx, u, d, p, 0.0)
        jac = lambda xx, u=u_k, d=d_k: sde.dfdx(xx, u, d, p, 0.0)
        for _ in range(n_int):
            x = implicit_euler_step(rhs, jac, x, h)
            t += h
            hours[idx] = t / 3600.0
            temps[idx] = float(x[0])
            idx += 1
    return hours, temps


def run_fixed_u(
    *,
    solve_n_int: int,
    roll_n_int: list[int],
    price_kind: str = "flat",
) -> dict:
    """Solve once, then re-simulate T under that U* at several n_int."""

    engine = ControlEngine(_config())
    _set_n_int(engine, solve_n_int)
    n_fast = engine._controller.horizon
    dt_s = float(engine._controller._dt)
    m = int(engine._controller._timing.m)
    outdoor = [22.0] * n_fast
    prices = _price_series(price_kind, n_fast, dt_s)
    engine.compute_actions(
        {ROOM: 25.2},
        22.0,
        {ROOM: 23.5},
        now=NOW,
        outdoor_forecast=outdoor,
        price_forecast=prices,
    )
    engine.mark_nmpc_busy()
    plan = engine.solve_nmpc_blocking()
    if not plan.get("accepted") or not engine.apply_nmpc_result(plan):
        raise RuntimeError("fixed-U sandbox: production NMPC did not accept a plan")
    snap = engine.forecast_snapshot()
    watts = _series(snap, "heating_schedule")
    frozen = _freeze_plan(engine)
    hours = (np.arange(frozen["U_fast"].shape[0], dtype=float) + 1.0) * (
        frozen["dt_s"] / 3600.0
    )
    solar = np.array(
        [float(step.get(ROOM, 0.0)) for step in frozen["solar"][: hours.size]],
        dtype=float,
    )
    t_ocp = np.asarray(frozen["T_ocp"][: hours.size, 0], dtype=float)
    runs = []
    for n_int in roll_n_int:
        temps = _roll_frozen(engine, frozen, n_int)
        n = min(temps.size, hours.size)
        temps = temps[:n]
        err_ocp = temps - t_ocp[:n]
        metrics = _jitter(temps, frozen["dt_s"] / 3600.0)
        metrics.update(
            {
                "label": f"n_int={n_int}",
                "n_int": n_int,
                "max_abs_err_vs_ocp_K": float(np.max(np.abs(err_ocp))),
                "rms_err_vs_ocp_K": float(np.sqrt(np.mean(err_ocp * err_ocp))),
            }
        )
        runs.append(
            {
                "metrics": metrics,
                "hours": hours[:n],
                "temps": temps,
                "watts": watts[:n],
                "label": f"n_int={n_int}",
                "n_int": n_int,
            }
        )
    ref_n = max(roll_n_int)
    ref = next(run for run in runs if run["n_int"] == ref_n)
    for run in runs:
        n = min(run["temps"].size, ref["temps"].size)
        err = run["temps"][:n] - ref["temps"][:n]
        run["metrics"]["max_abs_err_vs_ref_K"] = float(np.max(np.abs(err)))
        run["metrics"]["rms_err_vs_ref_K"] = float(np.sqrt(np.mean(err * err)))
        run["metrics"]["reference_n_int"] = ref_n
    dense_h, dense_t = _dense_roll(engine, frozen, ref_n)
    return {
        "runs": runs,
        "hours": hours,
        "t_ocp": t_ocp,
        "watts": watts,
        "solar": solar,
        "prices": np.asarray(prices, dtype=float),
        "dense_hours": dense_h,
        "dense_temps": dense_t,
        "solve_n_int": solve_n_int,
        "reference_n_int": ref_n,
        "accepted": True,
        "fun": float(plan.get("fun", float("nan"))),
        "min_W": float(np.min(watts)) if watts.size else 0.0,
        "max_W": float(np.max(watts)) if watts.size else 0.0,
        "u_hold_h": _u_hold_hours(watts, dt_s / 3600.0),
        "max_slow_step_kW": _max_slow_step_kw(watts, m),
    }


def plot_runs(runs: list[dict], tag: str, title: str) -> Path:
    INSPECT.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    ax_t, ax_u, ax_p = axes
    for run in runs:
        ax_t.plot(run["hours"], run["temps"], label=run["label"], linewidth=1.6)
        ax_u.plot(run["hours"], run["watts"] / 1000.0, label=run["label"], linewidth=1.6)
    ax_t.axhline(23.5, color="0.4", linestyle="--", linewidth=0.8, label="setpoint")
    ax_t.axhline(25.5, color="0.7", linestyle=":", linewidth=0.8)
    ax_t.axhline(21.5, color="0.7", linestyle=":", linewidth=0.8)
    ax_t.set_ylabel("Forecast T [°C]")
    ax_t.set_title(title)
    ax_t.legend(loc="best")
    ax_t.grid(True, alpha=0.3)
    ax_u.set_ylabel("Planned power [kW]")
    ax_u.legend(loc="best")
    ax_u.grid(True, alpha=0.3)
    ax_p.plot(runs[0]["hours"], runs[0]["prices"], color="#81c784", linewidth=1.6)
    ax_p.set_ylabel("Price")
    ax_p.set_xlabel("Hours from now")
    ax_p.grid(True, alpha=0.3)
    fig.tight_layout()
    path = INSPECT / f"{tag}_forecast.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_dt(runs: list[dict], tag: str) -> Path:
    fig, ax = plt.subplots(figsize=(11, 4))
    for run in runs:
        dT = np.diff(run["temps"])
        hours = run["hours"][1:]
        ax.plot(hours, dT, label=run["label"], linewidth=1.2)
    ax.axhline(0.0, color="0.3", linewidth=0.8)
    ax.set_ylabel("ΔT per 15 min [K]")
    ax.set_xlabel("Hours from now")
    ax.set_title("Consecutive forecast steps (jitter)")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    path = INSPECT / f"{tag}_dT.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def plot_fixed_u(pack: dict, tag: str) -> list[Path]:
    INSPECT.mkdir(parents=True, exist_ok=True)
    runs = pack["runs"]
    hours = pack["hours"]
    ref_n = int(pack["reference_n_int"])
    solve_n = int(pack["solve_n_int"])

    fig, axes = plt.subplots(3, 1, figsize=(11, 10), sharex=True)
    ax_t, ax_err, ax_u = axes
    ax_t.plot(hours, pack["t_ocp"], color="0.2", linestyle="--", linewidth=1.4, label="OCP T_ref")
    for run in runs:
        ax_t.plot(run["hours"], run["temps"], label=run["label"], linewidth=1.4)
    ax_t.axhline(23.5, color="0.4", linestyle="--", linewidth=0.8)
    ax_t.axhline(25.5, color="0.7", linestyle=":", linewidth=0.8)
    ax_t.set_ylabel("T [°C]")
    ax_t.set_title(
        f"Frozen U* (solved at n_int={solve_n}) — open-loop T vs integrator density"
    )
    ax_t.legend(loc="best")
    ax_t.grid(True, alpha=0.3)
    ref = next(run for run in runs if run["n_int"] == ref_n)
    for run in runs:
        n = min(run["temps"].size, ref["temps"].size)
        ax_err.plot(
            run["hours"][:n],
            run["temps"][:n] - ref["temps"][:n],
            label=run["label"],
            linewidth=1.2,
        )
    ax_err.axhline(0.0, color="0.3", linewidth=0.8)
    ax_err.set_ylabel(f"T − T(n_int={ref_n}) [K]")
    ax_err.legend(loc="best")
    ax_err.grid(True, alpha=0.3)
    ax_u.step(hours, pack["watts"] / 1000.0, where="post", color="#ef5350", linewidth=1.4)
    ax_u.set_ylabel("Frozen U* [kW]")
    ax_u.set_xlabel("Hours from now")
    ax_u.grid(True, alpha=0.3)
    fig.tight_layout()
    forecast_path = INSPECT / f"{tag}_forecast.png"
    fig.savefig(forecast_path, dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 4))
    for run in runs:
        ax.plot(run["hours"][1:], np.diff(run["temps"]), label=run["label"], linewidth=1.2)
    ax.axhline(0.0, color="0.3", linewidth=0.8)
    ax.set_ylabel("ΔT per 15 min [K]")
    ax.set_xlabel("Hours from now")
    ax.set_title("Frozen U* — consecutive 15 min samples")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    dt_path = INSPECT / f"{tag}_dT.png"
    fig.savefig(dt_path, dpi=120)
    plt.close(fig)

    mask = pack["dense_hours"] <= 12.0
    fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    ax_d, ax_s = axes
    ax_d.plot(
        pack["dense_hours"][mask],
        pack["dense_temps"][mask],
        color="#90caf9",
        linewidth=0.8,
        label=f"substeps n_int={ref_n}",
    )
    prod = next((run for run in runs if run["n_int"] == solve_n), runs[0])
    m12 = prod["hours"] <= 12.0
    ax_d.plot(
        prod["hours"][m12],
        prod["temps"][m12],
        "o",
        color="#1565c0",
        markersize=4,
        label=f"15 min knots n_int={solve_n}",
    )
    ax_d.plot(
        ref["hours"][ref["hours"] <= 12.0],
        ref["temps"][ref["hours"] <= 12.0],
        "x",
        color="#c62828",
        markersize=5,
        label=f"15 min knots n_int={ref_n}",
    )
    ax_d.set_ylabel("T [°C]")
    ax_d.set_title("First 12 h: high-fidelity substeps vs 15 min Forecast knots")
    ax_d.legend(loc="best")
    ax_d.grid(True, alpha=0.3)
    ax_s.plot(hours[hours <= 12.0], pack["solar"][: int(np.sum(hours <= 12.0))], color="#ffa726")
    ax_s.set_ylabel("Solar [W]")
    ax_s.set_xlabel("Hours from now")
    ax_s.grid(True, alpha=0.3)
    fig.tight_layout()
    dense_path = INSPECT / f"{tag}_dense.png"
    fig.savefig(dense_path, dpi=120)
    plt.close(fig)
    return [forecast_path, dt_path, dense_path]


def _payload_temps(snap: dict, t_now: float) -> np.ndarray:
    """Room-view / Tuning series including the NOW bridge sample."""

    payload = build_app_forecast_payload(
        rooms=[{"name": ROOM, "setpoint": 23.5, "comfort_offset": 2.0}],
        room_temperatures={ROOM: t_now},
        outdoor_temp=22.0,
        energy_price=2.0,
        snapshot=snap,
        now=NOW,
    )
    slug = room_slug(ROOM)
    rows = payload["rooms"][slug]["forecast"]
    return np.array(
        [float(step["temperature"]) for step in rows if step.get("temperature") is not None],
        dtype=float,
    )


def _payload_watts(snap: dict) -> np.ndarray:
    payload = build_app_forecast_payload(
        rooms=[{"name": ROOM, "setpoint": 23.5, "comfort_offset": 2.0}],
        room_temperatures={ROOM: 25.2},
        outdoor_temp=22.0,
        energy_price=2.0,
        snapshot=snap,
        now=NOW,
    )
    slug = room_slug(ROOM)
    rows = payload["rooms"][slug]["forecast"]
    vals = []
    for step in rows:
        p = step.get("heating_power")
        vals.append(float(p) if p is not None else 0.0)
    return np.array(vals, dtype=float)


def run_surfaces(*, n_fast_ticks: int = 8, price_kind: str = "peaked") -> dict:
    """Compare Tuning preview (fresh NLP) vs room-view (live compute cache)."""

    engine = ControlEngine(_config())
    n_fast = engine._controller.horizon
    dt_s = float(engine._controller._dt)
    outdoor = [22.0] * n_fast
    prices = _price_series(price_kind, n_fast, dt_s)
    t_meas = 25.2
    engine.compute_actions(
        {ROOM: t_meas},
        22.0,
        {ROOM: 23.5},
        now=NOW,
        outdoor_forecast=outdoor,
        price_forecast=prices,
    )
    engine.mark_nmpc_busy()
    plan = engine.solve_nmpc_blocking()
    if not plan.get("accepted") or not engine.apply_nmpc_result(plan):
        raise RuntimeError("surfaces sandbox: live NMPC did not accept")
    snap_apply = engine.forecast_snapshot()
    t_apply = _series(snap_apply, "predictions")
    w_apply = _series(snap_apply, "heating_schedule")

    # Same extra compute() the Tuning preview runs after apply.
    engine.compute_actions(
        {ROOM: t_meas},
        22.0,
        {ROOM: 23.5},
        now=NOW,
        outdoor_forecast=outdoor,
        price_forecast=prices,
    )
    snap_preview_compute = engine.forecast_snapshot()
    t_preview_compute = _series(snap_preview_compute, "predictions")

    # Room view after one slow period of 15 min ticks. Measured T held (sensor);
    # EKF + P run; Forecast is rebuilt from the unshifted U* each tick.
    for k in range(1, n_fast_ticks + 1):
        now_k = NOW + timedelta(seconds=dt_s * k)
        engine.compute_actions(
            {ROOM: t_meas},
            22.0,
            {ROOM: 23.5},
            now=now_k,
            outdoor_forecast=outdoor,
            price_forecast=prices,
        )
    snap_room = engine.forecast_snapshot()
    t_room = _series(snap_room, "predictions")
    w_room = _series(snap_room, "heating_schedule")

    preview = engine.preview_tuning_forecast(
        {},
        {ROOM: t_meas},
        22.0,
        {ROOM: 23.5},
        outdoor_forecast=outdoor,
        price_forecast=prices,
        now=NOW + timedelta(seconds=dt_s * n_fast_ticks),
    )
    if preview.get("error"):
        raise RuntimeError(f"surfaces sandbox: preview failed {preview.get('error')}")
    t_preview = _series(preview, "predictions")
    w_preview = _series(preview, "heating_schedule")

    n = min(t_apply.size, t_room.size, t_preview.size, t_preview_compute.size)
    hours = (np.arange(n, dtype=float) + 1.0) * (dt_s / 3600.0)
    series = {
        "apply T_ref": t_apply[:n],
        "preview after compute()": t_preview_compute[:n],
        "room view after 2 h ticks": t_room[:n],
        "tuning preview re-solve": t_preview[:n],
    }
    watts = {
        "apply T_ref": w_apply[:n],
        "room view after 2 h ticks": w_room[:n],
        "tuning preview re-solve": w_preview[:n],
    }
    metrics = []
    for label, temps in series.items():
        row = _jitter(temps, dt_s / 3600.0)
        err = temps[:n] - t_preview[:n]
        row.update(
            {
                "label": label,
                "max_abs_err_vs_preview_K": float(np.max(np.abs(err))),
                "rms_err_vs_preview_K": float(np.sqrt(np.mean(err * err))),
            }
        )
        metrics.append(row)
    ui_room = _payload_temps(snap_room, t_meas)
    ui_preview = _payload_temps(preview, t_meas)
    ui_apply = _payload_temps(snap_apply, 25.2)
    return {
        "hours": hours,
        "series": series,
        "watts": watts,
        "metrics": metrics,
        "ui_hours": np.arange(ui_room.size, dtype=float) * (dt_s / 3600.0),
        "ui_room": ui_room,
        "ui_preview": ui_preview,
        "ui_apply": ui_apply,
        "n_fast_ticks": n_fast_ticks,
        "t_meas_after": t_meas,
        "fun_live": float(plan.get("fun", float("nan"))),
    }


def plot_surfaces(pack: dict, tag: str) -> list[Path]:
    INSPECT.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(3, 1, figsize=(11, 10), sharex=True)
    ax_t, ax_u, ax_ui = axes
    for label, temps in pack["series"].items():
        ax_t.plot(pack["hours"], temps, label=label, linewidth=1.4)
    ax_t.axhline(23.5, color="0.4", linestyle="--", linewidth=0.8)
    ax_t.axhline(25.5, color="0.7", linestyle=":", linewidth=0.8)
    ax_t.set_ylabel("Engine T [°C]")
    ax_t.set_title("Room-view live cache vs Tuning preview (same plant, peaked price)")
    ax_t.legend(loc="best", fontsize=8)
    ax_t.grid(True, alpha=0.3)
    for label, watts in pack["watts"].items():
        ax_u.step(
            pack["hours"],
            np.asarray(watts)[: pack["hours"].size] / 1000.0,
            where="post",
            label=label,
            linewidth=1.4,
        )
    ax_u.set_ylabel("Planned power [kW]")
    ax_u.legend(loc="best", fontsize=8)
    ax_u.grid(True, alpha=0.3)
    n_ui = min(pack["ui_room"].size, pack["ui_preview"].size, pack["ui_apply"].size)
    hours_ui = pack["ui_hours"][:n_ui]
    ax_ui.plot(hours_ui, pack["ui_apply"][:n_ui], label="payload after apply", linewidth=1.4)
    ax_ui.plot(hours_ui, pack["ui_room"][:n_ui], label="payload room view", linewidth=1.4)
    ax_ui.plot(hours_ui, pack["ui_preview"][:n_ui], label="payload tuning preview", linewidth=1.4)
    ax_ui.set_ylabel("UI payload T [°C]")
    ax_ui.set_xlabel("Hours from the plot NOW")
    ax_ui.legend(loc="best", fontsize=8)
    ax_ui.grid(True, alpha=0.3)
    fig.tight_layout()
    forecast_path = INSPECT / f"{tag}_forecast.png"
    fig.savefig(forecast_path, dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 4))
    for label, temps in pack["series"].items():
        ax.plot(pack["hours"][1:], np.diff(temps), label=label, linewidth=1.1)
    ax.axhline(0.0, color="0.3", linewidth=0.8)
    ax.set_ylabel("ΔT per 15 min [K]")
    ax.set_xlabel("Hours from now")
    ax.set_title("Consecutive engine Forecast steps")
    ax.legend(loc="best", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    dt_path = INSPECT / f"{tag}_dT.png"
    fig.savefig(dt_path, dpi=120)
    plt.close(fig)
    return [forecast_path, dt_path]


def _floats(raw: str) -> list[float]:
    return [float(part) for part in raw.split(",") if part.strip()]


def _ints(raw: str) -> list[int]:
    return [int(part) for part in raw.split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="01")
    parser.add_argument(
        "--n-int",
        default="",
        help="comma-separated n_int_steps; default 1,10,40 unless --smoothing or --fixed-u",
    )
    parser.add_argument(
        "--smoothing",
        default="",
        help="comma-separated smoothing_weight (s_rom) values; production n_int=10",
    )
    parser.add_argument(
        "--price",
        choices=("flat", "peaked"),
        default="flat",
        help="fast-rate price forecast (OCP sees 2 h means)",
    )
    parser.add_argument(
        "--fixed-u",
        action="store_true",
        help="solve once at --solve-n-int, then re-roll T at --n-int (no re-solve)",
    )
    parser.add_argument(
        "--solve-n-int",
        type=int,
        default=BASELINE_N_INT,
        help="n_int used only for the NLP when --fixed-u is set (default 10)",
    )
    parser.add_argument(
        "--surfaces",
        action="store_true",
        help="compare room-view live forecast cache vs Tuning preview re-solve",
    )
    parser.add_argument(
        "--fast-ticks",
        type=int,
        default=8,
        help="15 min ticks to run on the live controller before comparing (default 8 = 2 h)",
    )
    args = parser.parse_args()
    INSPECT.mkdir(parents=True, exist_ok=True)
    if args.surfaces:
        pack = run_surfaces(n_fast_ticks=args.fast_ticks, price_kind=args.price)
        plots = plot_surfaces(pack, args.tag)
        report = {
            "tag": args.tag,
            "mode": "surfaces",
            "scenario": {
                "now": NOW.isoformat(),
                "room": ROOM,
                "T0": 25.2,
                "setpoint": 23.5,
                "timing": "2 h / 8 fast / 36 h",
                "price": args.price,
                "n_fast_ticks": pack["n_fast_ticks"],
                "t_meas_after": pack["t_meas_after"],
                "fun_live": pack["fun_live"],
            },
            "metrics": pack["metrics"],
            "plots": [str(p.relative_to(ROOT)) for p in plots],
        }
        report_path = INSPECT / f"{args.tag}_report.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0
    if args.fixed_u:
        n_vals = _ints(args.n_int) if args.n_int.strip() else list(FIXED_U_N_INT)
        pack = run_fixed_u(
            solve_n_int=args.solve_n_int,
            roll_n_int=n_vals,
            price_kind=args.price,
        )
        plots = plot_fixed_u(pack, args.tag)
        table = [run["metrics"] for run in pack["runs"]]
        report = {
            "tag": args.tag,
            "mode": "fixed_u",
            "scenario": {
                "now": NOW.isoformat(),
                "room": ROOM,
                "T0": 25.2,
                "setpoint": 23.5,
                "comfort_offset": 2.0,
                "timing": "2 h / 8 fast / 36 h",
                "solar_exposure": "high",
                "price": args.price,
                "solve_n_int": pack["solve_n_int"],
                "reference_n_int": pack["reference_n_int"],
                "u_hold_h": pack["u_hold_h"],
                "min_W": pack["min_W"],
                "max_W": pack["max_W"],
                "fun": pack["fun"],
            },
            "baseline_n_int": BASELINE_N_INT,
            "metrics": table,
            "plots": [str(p.relative_to(ROOT)) for p in plots],
        }
        report_path = INSPECT / f"{args.tag}_report.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0
    if args.smoothing.strip():
        n_int = 10
        runs = [
            run_one(n_int, smoothing_weight=s, price_kind=args.price)
            for s in _floats(args.smoothing)
        ]
        title = f"Production NMPC — ROM vs {args.price} price (n_int=10)"
    else:
        n_vals = _ints(args.n_int) if args.n_int.strip() else list(CANDIDATE_N_INT)
        runs = [run_one(n, price_kind=args.price) for n in n_vals]
        title = f"Production NMPC — integrator substeps ({args.price} price)"
    forecast_png = plot_runs(runs, args.tag, title)
    dt_png = plot_dt(runs, args.tag)
    table = [run["metrics"] for run in runs]
    report = {
        "tag": args.tag,
        "scenario": {
            "now": NOW.isoformat(),
            "room": ROOM,
            "T0": 25.2,
            "setpoint": 23.5,
            "comfort_offset": 2.0,
            "timing": "2 h / 8 fast / 36 h",
            "solar_exposure": "high",
            "price": args.price,
            "app_smoothing_fallback": APP_SMOOTHING,
            "engine_smoothing_default": ENGINE_SMOOTHING,
        },
        "baseline_n_int": BASELINE_N_INT,
        "metrics": table,
        "plots": [str(forecast_png.relative_to(ROOT)), str(dt_png.relative_to(ROOT))],
    }
    report_path = INSPECT / f"{args.tag}_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
