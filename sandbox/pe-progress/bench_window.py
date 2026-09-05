#!/usr/bin/env python3
"""Approximate N-step PE J+grad wall time vs identification window length.

Uses production ``nstep_pem_and_grad`` on the live NMPC grid (not a toy
loop). One-room synthetic excited history. Not HAOS hardware.
"""

from __future__ import annotations

import csv
import json
import math
import platform
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from heatingassistant.engine.estimation.identifiability import (  # noqa: E402
    _check_identifiable_connections,
    _check_identifiable_open_ua,
    _check_identifiable_solar,
    _check_identifiable_sources,
    _identifiable_split_rooms,
)
from heatingassistant.engine.estimation.kalman_ml import (  # noqa: E402
    KalmanMLEstimator,
)
from heatingassistant.engine.estimation.theta_layout import _ThetaLayout  # noqa: E402
from heatingassistant.engine.nmpc_timing import timing_from_options  # noqa: E402
from heatingassistant.engine.thermal_model import HouseModel  # noqa: E402
from tests.helpers.estimation_fixtures import (  # noqa: E402
    make_electric_heaters,
    make_single_room,
)

SANDBOX = Path(__file__).resolve().parent
INSPECT = SANDBOX / "inspect"

WINDOW_H = (6, 12, 24, 48, 72, 120)
CAP_S = (60.0, 300.0)


def excited_history(*, n_steps: int, dt: float, seed: int = 3):
    rng = np.random.default_rng(seed)
    room = make_single_room(thermal_mass=8.0e6, r_external=0.025, temperature=20.0)
    sources = make_electric_heaters([room], max_power=2000.0)
    model = HouseModel([room])
    t0 = 1_700_000_000.0
    history = []
    for k in range(n_steps):
        duty = 0.85 if (k // 4) % 2 == 0 else 0.05
        tout = 2.0 + 6.0 * math.sin(2.0 * math.pi * k / 96.0)
        y = float(model.rooms[room.name].temperature) + float(rng.normal(0.0, 0.04))
        history.append(
            {
                "y": [y],
                "u": [duty],
                "d_outdoor": tout,
                "d_solar": {room.name: 0.0},
                "timestamp": t0 + k * dt,
            }
        )
        heat_inputs = {src.room: src.thermal_power(duty, tout) for src in sources}
        model.step(dt, heat_inputs, tout, {room.name: 0.0})
    return [room], sources, history


def layout_and_theta(est: KalmanMLEstimator, history):
    identifiable_pairs = _check_identifiable_connections(
        history, est._room_names, est._connection_pairs,
        min_history_steps=est._min_history_steps,
    )
    identifiable_sources = list(range(est._n_u))
    excited_sources = _check_identifiable_sources(
        history, est._n_u, min_history_steps=est._min_history_steps,
    )
    identifiable_solar = _check_identifiable_solar(
        history, est._room_names, min_history_steps=est._min_history_steps,
    )
    identifiable_splits = _identifiable_split_rooms(
        excited_sources, est._sources, est._room_names,
    )
    identifiable_ua = _check_identifiable_open_ua(
        history, est._room_names, est._min_segment_steps,
    )
    layout = _ThetaLayout(
        n_rooms=est._n,
        identifiable_sources=identifiable_sources,
        identifiable_pairs=identifiable_pairs,
        identifiable_solar=identifiable_solar,
        identifiable_splits=identifiable_splits,
        identifiable_ua=identifiable_ua,
        n_wall_segs=1,
    )
    theta = np.concatenate(
        [
            est._log_mass_prior,
            est._log_r_prior,
            est._q_int_prior,
            np.tile(est._t_wall_init_prior, 1),
            np.array([est._log_alpha_prior_full[s] for s in identifiable_sources]),
            np.array(
                [
                    est._connection_r_priors[est._connection_pairs.index(p)]
                    for p in identifiable_pairs
                ]
            ),
            np.array([est._log_solar_prior_full[i] for i in identifiable_solar]),
            np.array([est._c_air_prior_full[i] for i in identifiable_splits]),
            np.array([est._r_aw_prior_full[i] for i in identifiable_splits]),
            np.array([est._ua_open_prior_full[i] for i in identifiable_ua]),
        ]
    )
    return layout, theta


def n_origins(n_hist: int, n_horizon: int, stride: int) -> int:
    if n_hist < 2:
        return 0
    count = 0
    for k in range(n_hist - 1):
        remaining = n_hist - 1 - k
        horizon = min(n_horizon, remaining)
        if k % stride == 0 and horizon >= 1:
            count += 1
    return count


def time_call(fn, repeats: int) -> float:
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return float(np.median(times))


def main() -> None:
    timing = timing_from_options(
        {},
        default_period=7200.0,
        default_substeps=8,
        default_horizon_h=36.0,
    )
    dt = timing.dt_s
    n_horizon = timing.n_fast
    stride = timing.fast_substeps
    repeats = 2
    rows = []
    for hours in WINDOW_H:
        n_steps = max(12, int(round(hours * 3600.0 / dt)))
        rooms, sources, history = excited_history(n_steps=n_steps, dt=dt)
        est = KalmanMLEstimator(
            rooms,
            sources,
            dt=dt,
            n_horizon_steps=n_horizon,
            origin_stride=stride,
            max_compute_s=0.0,
            use_nstep_pem=True,
        )
        layout, theta = layout_and_theta(est, history)
        std = est._convert_history_std(history, use_ym=True)
        est._pe_deadline_mono = None

        def nstep():
            est._nstep_pem_and_grad(theta, layout, std, dt)

        def oe():
            est._simulation_mse_and_grad(
                theta, layout, std, nominal_dt=dt,
                max_window_steps=est._max_window_steps,
                min_segment_steps=est._min_segment_steps,
            )

        nstep()  # warmup / import
        nstep_s = time_call(nstep, repeats)
        oe()
        oe_s = time_call(oe, repeats)
        origins = n_origins(len(std), n_horizon, stride)
        row = {
            "window_h": hours,
            "n_steps": n_steps,
            "n_origins": origins,
            "ntheta": int(len(theta)),
            "nstep_s": round(nstep_s, 4),
            "oe_s": round(oe_s, 4),
            "nfev_in_60s": round(60.0 / nstep_s, 1) if nstep_s > 0 else None,
            "nfev_in_300s": round(300.0 / nstep_s, 1) if nstep_s > 0 else None,
        }
        rows.append(row)
        print(json.dumps(row), flush=True)

    meta = {
        "host": platform.node(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "dt_s": dt,
        "n_horizon": n_horizon,
        "origin_stride": stride,
        "rooms": 1,
        "repeats": repeats,
        "caps_s": list(CAP_S),
        "note": (
            "Seconds per one J+grad evaluation at the prior. "
            "A production job also runs tiled-OE L-BFGS first on the same cap, "
            "then one or more N-step L-BFGS starts (maxiter 500). "
            "This host is not the HAOS box."
        ),
        "rows": rows,
    }
    INSPECT.mkdir(parents=True, exist_ok=True)
    json_path = INSPECT / "03_window_runtime.json"
    csv_path = INSPECT / "03_window_runtime.csv"
    json_path.write_text(json.dumps(meta, indent=2) + "\n")
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    hours = [r["window_h"] for r in rows]
    nstep = [r["nstep_s"] for r in rows]
    oe = [r["oe_s"] for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(10.2, 4.2))
    ax = axes[0]
    ax.plot(hours, nstep, "o-", color="#4fc3f7", label="N-step J+grad")
    ax.plot(hours, oe, "s--", color="#9aa3b2", label="tiled OE J+grad")
    ax.set_xlabel("window (hours)")
    ax.set_ylabel("seconds per evaluation")
    ax.set_title("One PE evaluation vs window")
    ax.grid(True, alpha=0.3)
    ax.legend()
    ax = axes[1]
    nfev5 = [r["nfev_in_300s"] for r in rows]
    nfev1 = [r["nfev_in_60s"] for r in rows]
    ax.plot(hours, nfev5, "o-", color="#4fc3f7", label="evals in 5 min cap")
    ax.plot(hours, nfev1, "s--", color="#f5a623", label="evals in 1 min cap")
    ax.set_xlabel("window (hours)")
    ax.set_ylabel("N-step evaluations that fit the cap")
    ax.set_title("How far L-BFGS can get")
    ax.set_yscale("log")
    ax.grid(True, alpha=0.3, which="both")
    ax.legend()
    fig.tight_layout()
    png = INSPECT / "03_window_runtime.png"
    fig.savefig(png, dpi=140)
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")
    print(f"wrote {png}")


if __name__ == "__main__":
    main()
