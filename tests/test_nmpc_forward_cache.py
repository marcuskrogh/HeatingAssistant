"""The forward-roll cache must return the same cost and gradient as a fresh roll."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.engine.controller import facade
from heatingassistant.engine.nmpc_ocp import MeanOcp

ROOT = Path(__file__).resolve().parents[1]
APP_NMPC = ROOT / "heating_assistant" / "heatingassistant" / "engine" / "nmpc_ocp.py"
ROOT_NMPC = ROOT / "heatingassistant" / "engine" / "nmpc_ocp.py"


def _engine() -> ControlEngine:
    engine = ControlEngine(
        {
            "mpc_mode": "nmpc",
            "system_enabled": True,
            "nmpc_period": 900,
            "nmpc_fast_substeps": 1,
            "nmpc_horizon_h": 1.0,
            "nmpc_max_compute_s": 30.0,
            "energy_price_weight": 1.0,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 21.0,
                    "comfort_offset": 1.5,
                    "thermal_mass": 9.0e6,
                    "r_external": 0.05,
                    "temperature": 19.0,
                },
                {
                    "name": "Office",
                    "setpoint": 21.0,
                    "comfort_offset": 1.5,
                    "thermal_mass": 2.5e6,
                    "r_external": 0.05,
                    "temperature": 17.0,
                    "windows": [{"area": 2.0, "orientation": 270.0, "tilt": 90.0}],
                },
            ],
            "heat_sources": [
                {
                    "name": "Heat pump",
                    "type": "heat_pump",
                    "room": "Living Room",
                    "max_power": 9000.0,
                    "hvac_mode": "heat",
                },
                {
                    "name": "Office panel",
                    "type": "electric_heater",
                    "room": "Office",
                    "max_power": 1300.0,
                },
            ],
        }
    )
    assert engine._controller is not None
    return engine


def _ocp(engine: ControlEngine) -> MeanOcp:
    held: dict[str, MeanOcp] = {}
    cls = facade.MeanOcp
    real_init = cls.__init__

    def init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        held["ocp"] = self

    cls.__init__ = init  # type: ignore[method-assign]
    try:
        from scipy.optimize import OptimizeResult

        def skip(**kwargs):
            return OptimizeResult(
                x=np.asarray(kwargs["x0"], dtype=float),
                nit=0,
                success=True,
                fun=0.0,
                message="skipped",
            )

        engine._controller.solve_nmpc(
            outdoor_temp=-2.0,
            outdoor_forecast=[-2.0] * 4,
            price_forecast=[0.35] * 4,
            timeout_s=5.0,
            maxiter=1,
            minimize_fn=skip,
        )
    finally:
        cls.__init__ = real_init  # type: ignore[method-assign]
    return held["ocp"]


def _assert_cache_matches(ocp: MeanOcp) -> None:
    u = np.linspace(0.1, 0.8, ocp.n * ocp.nu)
    ocp._forward_key = None
    gradient_fresh = np.asarray(ocp.jac(u), dtype=float).copy()
    gradient_cached = np.asarray(ocp.jac(u), dtype=float).copy()
    assert np.array_equal(gradient_cached, gradient_fresh)
    cost_cached = float(ocp.cost(u))
    ocp._forward_key = None
    cost_fresh = float(ocp.cost(u))
    assert cost_cached == cost_fresh


def test_cached_roll_matches_a_fresh_roll() -> None:
    _assert_cache_matches(_ocp(_engine()))


def _load_app_nmpc():
    """Load the file the App image copies into the container."""

    import sys

    spec = importlib.util.spec_from_file_location(
        "heatingassistant.engine.app_copy_nmpc_ocp",
        APP_NMPC,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_app_package_copy_runs_the_same_cache() -> None:
    assert APP_NMPC.read_bytes() == ROOT_NMPC.read_bytes()
    app_mod = _load_app_nmpc()
    original = facade.MeanOcp
    facade.MeanOcp = app_mod.MeanOcp
    try:
        ocp = _ocp(_engine())
    finally:
        facade.MeanOcp = original
    assert type(ocp) is app_mod.MeanOcp
    _assert_cache_matches(ocp)
