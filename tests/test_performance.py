"""
Performance benchmarks for the active control system and parameter estimation.

Measures wall-clock run-times of:
  • HeatingMPCController.compute()  — EKF predict-update + L-BFGS-B NLP solve
  • KalmanMLEstimator.estimate()    — multi-start Nelder–Mead ML estimation

Three representative house configurations are tested:
  1. Studio apartment  — 1 room,  1 electric heater,  MPC horizon = 6
  2. Two-bedroom flat  — 2 rooms, 2 electric heaters, MPC horizon = 6
  3. Full house        — 5 rooms, 4 heaters + 1 heat pump, MPC horizon = 8

Each benchmark runs the target function multiple times, collects wall-clock
timings and appends a row to a module-level results list.  After all tests in
this module have executed, a session-scoped autouse fixture writes the
collected results to ``BENCHMARKS.md`` in the repository root.

Run with:
    python -m pytest tests/test_performance.py -v -s

The ``-s`` flag lets the per-test timing summaries print to stdout.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Tuple

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Path setup — allow imports without installing the package
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.thermal_model import (
    HouseModel,
    Room,
    RoomConnection,
)
from custom_components.heating_assistant.heat_sources import ElectricHeater, HeatPump
from custom_components.heating_assistant.controller import HeatingMPCController
from custom_components.heating_assistant.parameter_estimator import (
    KalmanMLEstimator,
    MIN_HISTORY_STEPS,
)

# ---------------------------------------------------------------------------
# Module-level results store — populated by each benchmark test
# ---------------------------------------------------------------------------

_RESULTS: List[dict] = []

_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
_BENCHMARKS_FILE = os.path.join(_REPO_ROOT, "BENCHMARKS.md")

# Number of repetitions for each benchmark routine.
_MPC_REPS = 15
# Parameter estimation is slow (multi-start Nelder-Mead with a Kalman filter
# in the objective); a single timed call is sufficient to track regressions.
_ESTIM_REPS = 1

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _time_it(fn, n_repeats: int) -> Tuple[float, float, float]:
    """Run *fn* n_repeats times; return (mean_ms, median_ms, p95_ms)."""
    times_ms: List[float] = []
    for _ in range(n_repeats):
        t0 = time.perf_counter()
        fn()
        times_ms.append((time.perf_counter() - t0) * 1_000.0)
    times_ms.sort()
    mean_ms = float(np.mean(times_ms))
    median_ms = float(np.median(times_ms))
    p95_ms = times_ms[max(0, int(0.95 * len(times_ms)) - 1)]
    return mean_ms, median_ms, p95_ms


def _generate_history(rooms, sources, n_steps: int = 60, seed: int = 42):
    """Simulate synthetic history for the parameter estimator."""
    rng = np.random.default_rng(seed)
    model = HouseModel(rooms)
    outdoor_temp = 5.0
    temps = {r.name: r.temperature for r in rooms}
    history = []
    for k in range(n_steps):
        y = [temps[r.name] + rng.normal(0.0, 0.05) for r in rooms]
        u = [1.0 if (k // 30) % 2 == 0 else 0.0] * len(sources)
        solar = {r.name: 0.0 for r in rooms}
        history.append({"y": y, "u": u, "d_outdoor": outdoor_temp, "d_solar": solar})
        heat_inputs = {
            src.room: src.thermal_power(u[i], outdoor_temp)
            for i, src in enumerate(sources)
        }
        temps = model.step(60.0, heat_inputs, outdoor_temp, solar)
    return history


def _record(scenario: str, routine: str, reps: int, mean_ms: float,
            median_ms: float, p95_ms: float) -> None:
    _RESULTS.append(
        dict(scenario=scenario, routine=routine, reps=reps,
             mean_ms=mean_ms, median_ms=median_ms, p95_ms=p95_ms)
    )
    print(
        f"\n  [{scenario}] {routine}: "
        f"mean={mean_ms:.1f}ms  median={median_ms:.1f}ms  p95={p95_ms:.1f}ms  "
        f"(n={reps})"
    )


# ---------------------------------------------------------------------------
# House builders
# ---------------------------------------------------------------------------


def _studio():
    """Studio apartment — 1 room, 1 electric heater."""
    room = Room(name="living_room", thermal_mass=4_000_000.0, r_external=0.05,
                temperature=18.0, setpoint=21.0)
    model = HouseModel([room])
    sources = [ElectricHeater("lr_heater", "living_room", max_power=2_000.0)]
    return model, sources


def _two_bedroom():
    """Two-bedroom flat — 2 rooms, 2 electric heaters."""
    living = Room(name="living_room", thermal_mass=5_000_000.0, r_external=0.05,
                  connections=[RoomConnection("bedroom", 0.3)],
                  temperature=18.0, setpoint=21.0)
    bedroom = Room(name="bedroom", thermal_mass=3_000_000.0, r_external=0.08,
                   connections=[RoomConnection("living_room", 0.3)],
                   temperature=17.0, setpoint=20.0)
    model = HouseModel([living, bedroom])
    sources = [
        ElectricHeater("lr_heater", "living_room", max_power=2_000.0),
        ElectricHeater("br_heater", "bedroom", max_power=1_500.0),
    ]
    return model, sources


def _full_house():
    """Full house — 5 rooms, 4 electric heaters + 1 heat pump."""
    living = Room(name="living_room", thermal_mass=8_000_000.0, r_external=0.04,
                  connections=[
                      RoomConnection("kitchen", 0.2),
                      RoomConnection("hallway", 0.15),
                  ],
                  temperature=18.0, setpoint=21.0)
    kitchen = Room(name="kitchen", thermal_mass=3_500_000.0, r_external=0.06,
                   connections=[RoomConnection("living_room", 0.2)],
                   temperature=18.5, setpoint=20.0)
    bedroom1 = Room(name="bedroom1", thermal_mass=4_000_000.0, r_external=0.07,
                    connections=[
                        RoomConnection("hallway", 0.25),
                        RoomConnection("bedroom2", 0.4),
                    ],
                    temperature=17.0, setpoint=19.0)
    bedroom2 = Room(name="bedroom2", thermal_mass=3_000_000.0, r_external=0.09,
                    connections=[RoomConnection("bedroom1", 0.4)],
                    temperature=16.5, setpoint=19.0)
    hallway = Room(name="hallway", thermal_mass=2_000_000.0, r_external=0.10,
                   connections=[
                       RoomConnection("living_room", 0.15),
                       RoomConnection("bedroom1", 0.25),
                   ],
                   temperature=17.5, setpoint=18.0)
    model = HouseModel([living, kitchen, bedroom1, bedroom2, hallway])
    sources = [
        HeatPump("hp_living", "living_room", max_power=6_000.0,
                 cop_rated=3.5, cop_temp_ref=7.0),
        ElectricHeater("kt_heater", "kitchen", max_power=1_500.0),
        ElectricHeater("br1_heater", "bedroom1", max_power=2_000.0),
        ElectricHeater("br2_heater", "bedroom2", max_power=1_500.0),
        ElectricHeater("hall_heater", "hallway", max_power=1_000.0),
    ]
    return model, sources


# ---------------------------------------------------------------------------
# MPC performance tests
# ---------------------------------------------------------------------------


class TestMPCPerformance:
    """Benchmark HeatingMPCController.compute() for different house sizes."""

    _NOW = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)

    def test_studio_1room(self):
        """1-room studio: EKF + L-BFGS-B NLP (horizon=6, 1 input)."""
        model, sources = _studio()
        ctrl = HeatingMPCController(model, sources, horizon=6, dt=900.0)

        def run():
            ctrl.compute(outdoor_temp=0.0, solar_gains={"living_room": 0.0},
                         now=self._NOW)

        # Warm-up (first call may initialise scipy internals)
        run()

        mean_ms, median_ms, p95_ms = _time_it(run, _MPC_REPS)
        _record("studio-1room", "MPC.compute", _MPC_REPS, mean_ms, median_ms, p95_ms)

        # Soft performance guard: median must stay under 5 s (5 000 ms)
        assert median_ms < 5_000, (
            f"MPC.compute median {median_ms:.0f}ms exceeds 5 000ms for studio-1room"
        )

    def test_two_bedroom_2room(self):
        """2-room flat: EKF + L-BFGS-B NLP (horizon=6, 2 inputs)."""
        model, sources = _two_bedroom()
        ctrl = HeatingMPCController(model, sources, horizon=6, dt=900.0)

        def run():
            ctrl.compute(
                outdoor_temp=0.0,
                solar_gains={"living_room": 0.0, "bedroom": 0.0},
                now=self._NOW,
            )

        run()  # warm-up

        mean_ms, median_ms, p95_ms = _time_it(run, _MPC_REPS)
        _record("two-bedroom-2room", "MPC.compute", _MPC_REPS,
                mean_ms, median_ms, p95_ms)

        assert median_ms < 5_000, (
            f"MPC.compute median {median_ms:.0f}ms exceeds 5 000ms for "
            "two-bedroom-2room"
        )

    def test_full_house_5room(self):
        """5-room house: EKF + L-BFGS-B NLP (horizon=8, 5 inputs)."""
        model, sources = _full_house()
        ctrl = HeatingMPCController(model, sources, horizon=8, dt=900.0)
        rooms = [r.name for r in model.rooms.values()]

        def run():
            ctrl.compute(
                outdoor_temp=0.0,
                solar_gains={name: 0.0 for name in rooms},
                now=self._NOW,
            )

        run()  # warm-up

        mean_ms, median_ms, p95_ms = _time_it(run, _MPC_REPS)
        _record("full-house-5room", "MPC.compute", _MPC_REPS,
                mean_ms, median_ms, p95_ms)

        assert median_ms < 10_000, (
            f"MPC.compute median {median_ms:.0f}ms exceeds 10 000ms for "
            "full-house-5room"
        )


# ---------------------------------------------------------------------------
# Parameter estimation performance tests
# ---------------------------------------------------------------------------


class TestParameterEstimationPerformance:
    """Benchmark KalmanMLEstimator.estimate() for different house sizes.

    These tests are marked ``slow`` because each run involves multi-start
    Nelder-Mead with a Kalman filter as the objective, which can take tens
    of seconds.  Skip them in quick CI passes with ``-m "not slow"``.
    """

    _N_HISTORY = MIN_HISTORY_STEPS + 30  # 60 steps of synthetic data

    @pytest.mark.slow
    def test_studio_1room(self):
        """1-room studio: Kalman ML estimation (Nelder-Mead, 3 params)."""
        rooms_cfg = [
            Room(name="living_room", thermal_mass=4_000_000.0, r_external=0.05,
                 temperature=18.0, setpoint=21.0)
        ]
        model, sources = _studio()
        history = _generate_history(list(model.rooms.values()), sources,
                                    n_steps=self._N_HISTORY)
        estimator = KalmanMLEstimator(rooms_cfg, sources, dt=60.0)

        def run():
            estimator.estimate(history)

        mean_ms, median_ms, p95_ms = _time_it(run, _ESTIM_REPS)
        _record("studio-1room", "KalmanMLEstimator.estimate", _ESTIM_REPS,
                mean_ms, median_ms, p95_ms)

        # Soft performance guard: median under 60 s (60 000 ms)
        assert median_ms < 60_000, (
            f"KalmanMLEstimator.estimate median {median_ms:.0f}ms exceeds "
            "60 000ms for studio-1room"
        )

    @pytest.mark.slow
    def test_two_bedroom_2room(self):
        rooms_cfg = [
            Room(name="living_room", thermal_mass=5_000_000.0, r_external=0.05,
                 connections=[RoomConnection("bedroom", 0.3)],
                 temperature=18.0, setpoint=21.0),
            Room(name="bedroom", thermal_mass=3_000_000.0, r_external=0.08,
                 connections=[RoomConnection("living_room", 0.3)],
                 temperature=17.0, setpoint=20.0),
        ]
        model, sources = _two_bedroom()
        history = _generate_history(list(model.rooms.values()), sources,
                                    n_steps=self._N_HISTORY)
        estimator = KalmanMLEstimator(rooms_cfg, sources, dt=60.0)

        def run():
            estimator.estimate(history)

        mean_ms, median_ms, p95_ms = _time_it(run, _ESTIM_REPS)
        _record("two-bedroom-2room", "KalmanMLEstimator.estimate", _ESTIM_REPS,
                mean_ms, median_ms, p95_ms)

        assert median_ms < 120_000, (
            f"KalmanMLEstimator.estimate median {median_ms:.0f}ms exceeds "
            "120 000ms for two-bedroom-2room"
        )

    @pytest.mark.slow
    def test_full_house_5room(self):
        model, sources = _full_house()
        rooms_cfg = list(model.rooms.values())
        history = _generate_history(rooms_cfg, sources, n_steps=self._N_HISTORY)
        estimator = KalmanMLEstimator(
            [
                Room(name=r.name, thermal_mass=r.thermal_mass,
                     r_external=r.r_external,
                     connections=[
                         RoomConnection(c.connected_room, c.r_value)
                         for c in r.connections
                     ],
                     temperature=r.temperature, setpoint=r.setpoint)
                for r in rooms_cfg
            ],
            sources,
            dt=60.0,
        )

        def run():
            estimator.estimate(history)

        mean_ms, median_ms, p95_ms = _time_it(run, _ESTIM_REPS)
        _record("full-house-5room", "KalmanMLEstimator.estimate", _ESTIM_REPS,
                mean_ms, median_ms, p95_ms)

        assert median_ms < 900_000, (
            f"KalmanMLEstimator.estimate median {median_ms:.0f}ms exceeds "
            "900 000ms for full-house-5room"
        )


# ---------------------------------------------------------------------------
# Session fixture — write BENCHMARKS.md once all tests complete
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def write_benchmarks_md():
    """Write BENCHMARKS.md to the repo root after all module tests finish."""
    yield  # run all tests first
    _write_benchmarks_md()


def _write_benchmarks_md() -> None:
    """Format _RESULTS as a Markdown table and write BENCHMARKS.md."""
    if not _RESULTS:
        return

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ── MPC rows ──────────────────────────────────────────────────────────
    mpc_rows = [r for r in _RESULTS if r["routine"] == "MPC.compute"]
    # ── Estimation rows ───────────────────────────────────────────────────
    est_rows = [r for r in _RESULTS if r["routine"] == "KalmanMLEstimator.estimate"]

    def _table_row(r: dict) -> str:
        return (
            f"| {r['scenario']:<22} | {r['mean_ms']:>9.1f} "
            f"| {r['median_ms']:>11.1f} | {r['p95_ms']:>8.1f} "
            f"| {r['reps']:>4} |"
        )

    mpc_table = "\n".join(_table_row(r) for r in mpc_rows)
    est_table = "\n".join(_table_row(r) for r in est_rows)

    lines = [
        "# Performance Benchmarks",
        "",
        f"*Generated: {timestamp}*",
        "",
        "All timings are wall-clock milliseconds measured on the CI runner (single",
        "process, single thread).  Each cell shows the result of running the",
        "function the listed number of times (`n`) and computing the statistic",
        "over those samples.  The first call of every MPC scenario is treated as a",
        "warm-up and is **not** included in the timing samples.",
        "",
        "---",
        "",
        "## MPC active control step — `HeatingMPCController.compute()`",
        "",
        "One control step consists of:",
        "1. CD-EKF predict-update (integrate nonlinear drift + Riccati ODE, then Kalman gain)",
        "2. CDTrackingOCP NLP solve via L-BFGS-B (scipy)",
        "",
        "| Scenario               |  mean (ms) | median (ms) | p95 (ms) |   n |",
        "|------------------------|------------|-------------|----------|-----|",
        mpc_table,
        "",
        "**Configurations:**",
        "",
        "| Scenario | Rooms | Heat sources | MPC horizon | Inputs (u) |",
        "|----------|-------|--------------|-------------|------------|",
        "| `studio-1room` | 1 | 1 electric heater | 6 | 1 |",
        "| `two-bedroom-2room` | 2 (connected) | 2 electric heaters | 6 | 2 |",
        "| `full-house-5room` | 5 (interconnected) | 1 heat pump + 4 electric heaters | 8 | 5 |",
        "",
        "---",
        "",
        "## Parameter estimation — `KalmanMLEstimator.estimate()`",
        "",
        "One estimation run consists of:",
        "1. Identifiability analysis over the history buffer",
        "2. Multi-start Nelder–Mead maximisation of the Kalman prediction-error",
        "   decomposition log-likelihood (3 restarts from the prior + random perturbations)",
        "",
        f"History buffer: {MIN_HISTORY_STEPS + 30} steps (1-minute samples) of synthetic data.",
        "",
        "| Scenario               |  mean (ms) | median (ms) | p95 (ms) |   n |",
        "|------------------------|------------|-------------|----------|-----|",
        est_table,
        "",
        "**Configurations:**",
        "",
        "| Scenario | Rooms | Sources | Parameters estimated |",
        "|----------|-------|---------|---------------------|",
        "| `studio-1room` | 1 | 1 | C₁, R₁, Q_int₁ (3) |",
        "| `two-bedroom-2room` | 2 | 2 | C₁₋₂, R₁₋₂, Q_int₁₋₂, R₁₂ (7) |",
        "| `full-house-5room` | 5 | 5 | C₁₋₅, R₁₋₅, Q_int₁₋₅, R_ij (≥15) |",
        "",
        "---",
        "",
        "## Notes",
        "",
        "- Timings include Python overhead (numpy, scipy import) but not module",
        "  import time (the module is already loaded).",
        "- The L-BFGS-B solver convergence time depends on the warm-start; the",
        "  first call (warm-up) is typically the slowest and is excluded.",
        "- Parameter estimation timing depends heavily on the number of identifiable",
        "  parameters (which the estimator detects automatically from the data).",
        "- Parameter estimation tests are marked `slow` and can be skipped in",
        "  quick CI passes with `pytest -m \"not slow\"`.",
        "- Run all benchmarks yourself:",
        "  ```bash",
        "  python -m pytest tests/test_performance.py -v -s",
        "  ```",
    ]

    with open(_BENCHMARKS_FILE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"\n  → BENCHMARKS.md written to {_BENCHMARKS_FILE}")
