"""
Micro-benchmark for HouseThermalSDE and HeatPump hot-path functions.

Times the functions called at every ODE sub-step and NLP gradient evaluation:
    f, dfdx, dfdu, g, gm, hm, dhmdx, dgmdx_const
    HeatPump.smooth_thermal_power / thermal_power / cooling_power

Usage:
    python benchmarks/bench_model.py
    python benchmarks/bench_model.py --reps 500 --warmup 50
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
import types

import numpy as np

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_PKG  = os.path.join(_ROOT, "custom_components", "heating_assistant")


def _load(mod_name: str, rel_path: str):
    """Load a submodule by file path, bypassing __init__.py."""
    path = os.path.join(_PKG, rel_path)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod  = importlib.util.module_from_spec(spec)
    # Register so intra-package relative imports resolve.
    sys.modules[mod_name] = mod
    # Stub the HA package as a parent so relative imports don't explode.
    if "heating_assistant" not in sys.modules:
        pkg = types.ModuleType("heating_assistant")
        pkg.__path__ = [_PKG]  # type: ignore[attr-defined]
        sys.modules["heating_assistant"] = pkg
    spec.loader.exec_module(mod)
    return mod

# Load dependency chain in order (relative imports chain through sys.modules)
_const_mod  = _load("heating_assistant.const",        "const.py")
_solar_mod  = _load("heating_assistant.solar_model",  "solar_model.py")
_therm_mod  = _load("heating_assistant.thermal_model","thermal_model.py")
_src_mod    = _load("heating_assistant.heat_sources", "heat_sources.py")

# controller.py imports from mbc — stub it if not installed
try:
    import mbc.models, mbc.estimation, mbc.control  # noqa: F401
except ImportError:
    def _stub(name):
        parts = name.split(".")
        for i in range(1, len(parts) + 1):
            pkg = ".".join(parts[:i])
            if pkg not in sys.modules:
                m = types.ModuleType(pkg)
                m.__path__ = []  # type: ignore
                sys.modules[pkg] = m
    for _p in ["mbc","mbc.models","mbc.estimation","mbc.control","mbc.identification"]:
        _stub(_p)
    sys.modules["mbc.models"].ContinuousDiscreteModel = object
    _me = sys.modules["mbc.estimation"]
    _me.ContinuousDiscreteEKF = object; _me.KalmanFilter = object
    _mc = sys.modules["mbc.control"]
    _mc.StandardContinuousOCP = object
    _mc.CDNMPCController = object; _mc.NLPScalingPolicy = object

_integ_mod  = _load("heating_assistant.integrator",  "integrator.py")
_ctrl_mod   = _load("heating_assistant.controller",  "controller.py")

HouseModel      = _therm_mod.HouseModel
Room            = _therm_mod.Room
RoomConnection  = _therm_mod.RoomConnection
ElectricHeater  = _src_mod.ElectricHeater
HeatPump        = _src_mod.HeatPump
HouseThermalSDE = _ctrl_mod.HouseThermalSDE


# ── Benchmark helpers ──────────────────────────────────────────────────────

def _timeit(fn, warmup: int, reps: int) -> tuple[float, float, float]:
    for _ in range(warmup):
        fn()
    t = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        t.append((time.perf_counter() - t0) * 1e6)  # µs
    t.sort()
    mean   = float(np.mean(t))
    median = float(np.median(t))
    p95    = t[min(int(0.95 * len(t)), len(t) - 1)]
    return mean, median, p95


def _row(label: str, mean: float, median: float, p95: float) -> str:
    return (f"  {label:<30}  mean={mean:7.1f}µs  "
            f"median={median:7.1f}µs  p95={p95:7.1f}µs")


# ── Model builders ─────────────────────────────────────────────────────────

def _build_2room():
    living  = Room("living",  r_external=0.05, thermal_mass=5e6, temperature=20.0, setpoint=21.0)
    bedroom = Room("bedroom", r_external=0.08, thermal_mass=3e6, temperature=19.0, setpoint=20.0,
                   connections=[RoomConnection("living", 0.3)])
    model = HouseModel([living, bedroom])
    hp = HeatPump("hp1", "living", max_power=5000, cop_rated=3.5,
                  cooling_cop=2.5, emitter_time_constant=60.0)
    eh = ElectricHeater("eh1", "bedroom", max_power=2000)
    theta = np.array([np.log(5e6), np.log(3e6), np.log(0.05), np.log(0.08),
                      50.0, 30.0, 0.0])
    sde = HouseThermalSDE(model, [hp, eh], dt=300,
                          sigma_w=0.1, sigma_v=0.5, sigma_b=0.002,
                          augment_offsets=True, identifiable_sources=[0], theta=theta)
    x = np.array([20.0, 19.0, 0.5, 0.1, 0.05])
    u = np.array([0.6, 0.4])
    d = np.array([-2.0, 300.0, 100.0])
    return sde, hp, x, u, d, theta


def _build_5room():
    rooms = [
        Room("living",   r_external=0.04, thermal_mass=8e6, temperature=20.0, setpoint=21.0,
             connections=[RoomConnection("kitchen",0.2), RoomConnection("hallway",0.15)]),
        Room("kitchen",  r_external=0.06, thermal_mass=3.5e6, temperature=20.0, setpoint=20.0,
             connections=[RoomConnection("living",0.2)]),
        Room("bedroom1", r_external=0.07, thermal_mass=4e6, temperature=19.0, setpoint=19.0,
             connections=[RoomConnection("hallway",0.25), RoomConnection("bedroom2",0.4)]),
        Room("bedroom2", r_external=0.09, thermal_mass=3e6, temperature=18.5, setpoint=19.0,
             connections=[RoomConnection("bedroom1",0.4)]),
        Room("hallway",  r_external=0.10, thermal_mass=2e6, temperature=18.0, setpoint=18.0,
             connections=[RoomConnection("living",0.15), RoomConnection("bedroom1",0.25)]),
    ]
    model = HouseModel(rooms)
    sources = [
        HeatPump("hp", "living", max_power=6000, cop_rated=3.5,
                 cooling_cop=2.5, emitter_time_constant=60.0),
        ElectricHeater("kt",  "kitchen",  max_power=1500),
        ElectricHeater("br1", "bedroom1", max_power=2000),
        ElectricHeater("br2", "bedroom2", max_power=1500),
        ElectricHeater("hll", "hallway",  max_power=1000),
    ]
    theta = np.concatenate([
        np.log([8e6, 3.5e6, 4e6, 3e6, 2e6]),
        np.log([0.04, 0.06, 0.07, 0.09, 0.10]),
        [50., 20., 30., 25., 15.], [0.0],
    ])
    sde = HouseThermalSDE(model, sources, dt=300,
                          sigma_w=0.1, sigma_v=0.5, sigma_b=0.002,
                          augment_offsets=True, identifiable_sources=[0], theta=theta)
    hp = sources[0]
    x = np.concatenate([[20., 20., 19., 18.5, 18.], [0.5], [0.1]*5])
    u = np.array([0.6, 0.4, 0.5, 0.3, 0.2])
    d = np.array([-2., 300., 100., 80., 60., 40.])
    return sde, hp, x, u, d, theta


# ── Main ───────────────────────────────────────────────────────────────────

def run(reps: int = 300, warmup: int = 30) -> None:
    print(f"\n{'='*68}")
    print(f"  HouseThermalSDE micro-benchmark  (warmup={warmup}, reps={reps})")
    print(f"{'='*68}")

    for label, build_fn in [
        ("2-room (1 filtered HP + 1 EH, nx=5)", _build_2room),
        ("5-room (1 filtered HP + 4 EH, nx=11)", _build_5room),
    ]:
        print(f"\n--- {label} ---")
        sde, hp, x, u, d, theta = build_fn()
        p = theta

        for name, fn in [
            ("f",                 lambda: sde.f(x, u, d, p, 0.0)),
            ("dfdx",              lambda: sde.dfdx(x, u, d, p, 0.0)),
            ("dfdu",              lambda: sde.dfdu(x, u, d, p, 0.0)),
            ("g",                 lambda: sde.g(x, u, d, p, 0.0)),
            ("gm",                lambda: sde.gm(x, u, d, p, 0.0)),
            ("hm",                lambda: sde.hm(x, u, d, p, 0.0)),
            ("dhmdx",             lambda: sde.dhmdx(x, u, d, p, 0.0)),
            ("dgmdx_const",       lambda: sde.dgmdx_const),
            ("smooth_tp(hp)",     lambda: hp.smooth_thermal_power(0.6, -2.0)),
            ("thermal_power(hp)", lambda: hp.thermal_power(0.7, -2.0)),
            ("cooling_power(hp)", lambda: hp.cooling_power(-2.0)),
        ]:
            mean, median, p95 = _timeit(fn, warmup, reps)
            print(_row(name, mean, median, p95))

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reps",   type=int, default=300)
    parser.add_argument("--warmup", type=int, default=30)
    args = parser.parse_args()
    run(args.reps, args.warmup)
