#!/usr/bin/env python3
"""SWD-417 measure sandbox: Forecast jitter vs integrator substeps / ROM.

Isolation tree only. Wraps production ControlEngine / MeanOcp / EKF;
does not edit production source.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
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
from heatingassistant.engine.control_loop import ControlEngine  # noqa: E402

HERE = Path(__file__).resolve().parent
INSPECT = HERE / "inspect"
NOW = datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc)
ROOM = "Living Room"
BASELINE_N_INT = 10
CANDIDATE_N_INT = (1, 10, 40)
APP_SMOOTHING = 0.05  # runtime.py fallback when the option is unset
ENGINE_SMOOTHING = DEFAULT_SMOOTHING_WEIGHT  # 0.1; Tuning UI default


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


def _floats(raw: str) -> list[float]:
    return [float(part) for part in raw.split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="01")
    parser.add_argument(
        "--n-int",
        default="",
        help="comma-separated n_int_steps; default 1,10,40 unless --smoothing is set",
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
    args = parser.parse_args()
    INSPECT.mkdir(parents=True, exist_ok=True)
    if args.smoothing.strip():
        n_int = 10
        runs = [
            run_one(n_int, smoothing_weight=s, price_kind=args.price)
            for s in _floats(args.smoothing)
        ]
        title = f"Production NMPC — ROM vs {args.price} price (n_int=10)"
    else:
        n_vals = (
            [int(part) for part in args.n_int.split(",") if part.strip()]
            if args.n_int.strip()
            else list(CANDIDATE_N_INT)
        )
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
