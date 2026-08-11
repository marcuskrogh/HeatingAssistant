"""
Controller-level benchmark: HeatingMPCController.compute() with SLSQP
and analytical derivatives.

Mirrors the scenario configurations in tests/test_performance.py but
uses only SLSQP (always available via SciPy) so the benchmark does not
depend on optional NLP backends.

Usage:
    python benchmarks/bench_controller.py
    python benchmarks/bench_controller.py --reps 10 --warmup 3
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import types
from datetime import datetime, timezone

import numpy as np

_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _ROOT)

# ── Stub only the HA layer (not mbc — that must be real) ──────────────────

def _stub(name: str) -> None:
    parts = name.split(".")
    for i in range(1, len(parts) + 1):
        pkg = ".".join(parts[:i])
        if pkg not in sys.modules:
            mod = types.ModuleType(pkg)
            mod.__path__ = []  # type: ignore[attr-defined]
            sys.modules[pkg] = mod

_HA = [
    "voluptuous",
    "homeassistant", "homeassistant.core", "homeassistant.config_entries",
    "homeassistant.const", "homeassistant.exceptions",
    "homeassistant.helpers", "homeassistant.helpers.entity",
    "homeassistant.helpers.update_coordinator", "homeassistant.helpers.event",
    "homeassistant.helpers.storage", "homeassistant.helpers.config_validation",
    "homeassistant.helpers.entity_platform", "homeassistant.helpers.reload",
    "homeassistant.helpers.service",
    "homeassistant.components", "homeassistant.components.sensor",
    "homeassistant.components.climate", "homeassistant.components.button",
    "homeassistant.components.number", "homeassistant.components.select",
    "homeassistant.components.notify",
]
for _p in _HA:
    _stub(_p)

import importlib.util

def _load(mod_name: str, rel_path: str):
    path = os.path.join(_ROOT, "custom_components", "heating_assistant", rel_path)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod  = importlib.util.module_from_spec(spec)
    if "heating_assistant" not in sys.modules:
        pkg = types.ModuleType("heating_assistant")
        pkg.__path__ = [os.path.join(_ROOT, "custom_components", "heating_assistant")]
        sys.modules["heating_assistant"] = pkg
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod

_const_mod   = _load("heating_assistant.const",        "const.py")
_solar_mod   = _load("heating_assistant.solar_model",  "solar_model.py")
_therm_mod   = _load("heating_assistant.thermal_model","thermal_model.py")
_src_mod     = _load("heating_assistant.heat_sources", "heat_sources.py")
_integ_mod   = _load("heating_assistant.integrator",   "integrator.py")
_ctrl_mod    = _load("heating_assistant.controller",   "controller.py")

HouseModel          = _therm_mod.HouseModel
Room                = _therm_mod.Room
RoomConnection      = _therm_mod.RoomConnection
ElectricHeater      = _src_mod.ElectricHeater
HeatPump            = _src_mod.HeatPump
HeatingMPCController = _ctrl_mod.HeatingMPCController


# ── Benchmark helpers ──────────────────────────────────────────────────────

def _timeit(fn, warmup: int, reps: int) -> tuple[float, float, float]:
    for _ in range(warmup):
        fn()
    t = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        t.append((time.perf_counter() - t0) * 1e3)  # ms
    t.sort()
    mean   = float(np.mean(t))
    median = float(np.median(t))
    p95    = t[min(int(0.95 * len(t)), len(t) - 1)]
    return mean, median, p95


def _row(label: str, mean: float, median: float, p95: float) -> str:
    return (f"  {label:<30}  mean={mean:8.1f}ms  "
            f"median={median:8.1f}ms  p95={p95:8.1f}ms")


# ── House builders (same as test_performance.py) ──────────────────────────

def _studio():
    room = Room("living_room", thermal_mass=4_000_000.0, r_external=0.05,
                temperature=18.0, setpoint=21.0)
    model = HouseModel([room])
    sources = [ElectricHeater("lr_heater", "living_room", max_power=2_000.0)]
    return model, sources


def _two_bedroom():
    living  = Room("living_room", thermal_mass=5_000_000.0, r_external=0.05,
                   connections=[RoomConnection("bedroom", 0.3)],
                   temperature=18.0, setpoint=21.0)
    bedroom = Room("bedroom", thermal_mass=3_000_000.0, r_external=0.08,
                   connections=[RoomConnection("living_room", 0.3)],
                   temperature=17.0, setpoint=20.0)
    model = HouseModel([living, bedroom])
    sources = [
        ElectricHeater("lr_heater", "living_room", max_power=2_000.0),
        ElectricHeater("br_heater", "bedroom",     max_power=1_500.0),
    ]
    return model, sources


def _full_house():
    living  = Room("living_room", thermal_mass=8_000_000.0, r_external=0.04,
                   connections=[RoomConnection("kitchen", 0.2),
                                 RoomConnection("hallway", 0.15)],
                   temperature=18.0, setpoint=21.0)
    kitchen = Room("kitchen",  thermal_mass=3_500_000.0, r_external=0.06,
                   connections=[RoomConnection("living_room", 0.2)],
                   temperature=18.5, setpoint=20.0)
    bed1    = Room("bedroom1", thermal_mass=4_000_000.0, r_external=0.07,
                   connections=[RoomConnection("hallway",  0.25),
                                 RoomConnection("bedroom2", 0.4)],
                   temperature=17.0, setpoint=19.0)
    bed2    = Room("bedroom2", thermal_mass=3_000_000.0, r_external=0.09,
                   connections=[RoomConnection("bedroom1", 0.4)],
                   temperature=16.5, setpoint=19.0)
    hallway = Room("hallway",  thermal_mass=2_000_000.0, r_external=0.10,
                   connections=[RoomConnection("living_room", 0.15),
                                 RoomConnection("bedroom1",   0.25)],
                   temperature=17.5, setpoint=18.0)
    model = HouseModel([living, kitchen, bed1, bed2, hallway])
    sources = [
        HeatPump(      "hp_living",  "living_room", max_power=6_000.0,
                       cop_rated=3.5, cop_temp_ref=7.0),
        ElectricHeater("kt_heater",  "kitchen",     max_power=1_500.0),
        ElectricHeater("br1_heater", "bedroom1",    max_power=2_000.0),
        ElectricHeater("br2_heater", "bedroom2",    max_power=1_500.0),
        ElectricHeater("hall_heater","hallway",      max_power=1_000.0),
    ]
    return model, sources


# ── Scenarios ──────────────────────────────────────────────────────────────

_NOW = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)

SCENARIOS = [
    ("studio-1room    (N=6,  nu=1)", _studio,
     lambda m: {"living_room": 0.0},  6),
    ("two-bedroom-2rm (N=6,  nu=2)", _two_bedroom,
     lambda m: {r: 0.0 for r in m.room_names}, 6),
    ("full-house-5rm  (N=8,  nu=5)", _full_house,
     lambda m: {r: 0.0 for r in m.room_names}, 8),
    ("full-house-5rm  (N=16, nu=5)", _full_house,
     lambda m: {r: 0.0 for r in m.room_names}, 16),
]


# ── Main ───────────────────────────────────────────────────────────────────

def run(reps: int = 15, warmup: int = 3) -> None:
    print(f"\n{'='*72}")
    print(f"  HeatingMPCController.compute() — SLSQP + analytic derivatives")
    print(f"  warmup={warmup} calls excluded, then n={reps} timed calls")
    print(f"{'='*72}")

    for label, build_fn, solar_fn, horizon in SCENARIOS:
        model, sources = build_fn()
        ctrl = HeatingMPCController(
            model, sources,
            horizon=horizon, dt=900.0,
            solver="SLSQP",
            use_analytic_derivatives=True,
        )
        solar = solar_fn(model)

        def run_one():
            ctrl.compute(outdoor_temp=0.0, solar_gains=solar, now=_NOW)

        mean, median, p95 = _timeit(run_one, warmup, reps)
        print(_row(label, mean, median, p95))

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reps",   type=int, default=15)
    parser.add_argument("--warmup", type=int, default=3)
    args = parser.parse_args()
    run(args.reps, args.warmup)
