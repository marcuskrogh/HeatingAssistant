"""SWD-522: exclusive linear vs nonlinear MPC mode."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from heatingassistant.engine.const import (
    CONF_MPC_MODE,
    DEFAULT_NMPC_FAST_SUBSTEPS,
    DEFAULT_NMPC_HORIZON_H,
    DEFAULT_NMPC_PERIOD,
    MPC_MODE_LINEAR,
    MPC_MODE_NMPC,
    coerce_mpc_mode,
)
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.nmpc_timing import timing_from_options
from heatingassistant.engine.thermal_model import HouseModel, Room

_NOW = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _tiny_ctrl(**kwargs) -> HeatingMPCController:
    room = Room(
        "living_room",
        5e6,
        0.05,
        temperature=18.0,
        setpoint=21.0,
        comfort_offset=2.0,
    )
    model = HouseModel([room])
    heater = ElectricHeater("h", "living_room", max_power=2000.0)
    return HeatingMPCController(
        model, [heater], horizon=4, dt=900.0, **kwargs
    )


def test_coerce_mpc_mode_defaults_to_nmpc():
    assert coerce_mpc_mode(None) == MPC_MODE_NMPC
    assert coerce_mpc_mode("") == MPC_MODE_NMPC
    assert coerce_mpc_mode("qp") == MPC_MODE_LINEAR
    assert coerce_mpc_mode("LINEAR") == MPC_MODE_LINEAR


def test_linear_timing_ignores_nmpc_triple():
    timing = timing_from_options(
        {
            CONF_MPC_MODE: MPC_MODE_LINEAR,
            "update_interval": 600,
            "horizon": 8,
            "nmpc_period": 7200,
            "nmpc_fast_substeps": 8,
            "nmpc_horizon_h": 36,
        },
        default_period=DEFAULT_NMPC_PERIOD,
        default_substeps=DEFAULT_NMPC_FAST_SUBSTEPS,
        default_horizon_h=DEFAULT_NMPC_HORIZON_H,
    )
    assert timing.dt_s == pytest.approx(600.0)
    assert timing.n_fast == 8


def test_nmpc_timing_keeps_triple_when_linear_knobs_present():
    timing = timing_from_options(
        {
            CONF_MPC_MODE: MPC_MODE_NMPC,
            "update_interval": 600,
            "horizon": 8,
            "nmpc_period": 1800,
            "nmpc_fast_substeps": 2,
            "nmpc_horizon_h": 1.0,
        },
        default_period=DEFAULT_NMPC_PERIOD,
        default_substeps=DEFAULT_NMPC_FAST_SUBSTEPS,
        default_horizon_h=DEFAULT_NMPC_HORIZON_H,
    )
    assert timing.period_s == pytest.approx(1800.0)
    assert timing.fast_substeps == 2
    assert timing.n_fast == 4


def test_linear_compute_solves_qp_not_p():
    ctrl = _tiny_ctrl(mpc_mode=MPC_MODE_LINEAR)
    called = {"n": 0}
    real_step = ctrl._mpc.step

    def _step(*args, **kwargs):
        called["n"] += 1
        return real_step(*args, **kwargs)

    ctrl._mpc.step = _step  # type: ignore[method-assign]
    actions = ctrl.compute(outdoor_temp=0.0, now=_NOW)
    assert called["n"] == 1
    assert "h" in actions
    assert ctrl.nmpc_due is False
    assert ctrl.predictions


def test_nmpc_compute_does_not_call_qp_step():
    ctrl = _tiny_ctrl(mpc_mode=MPC_MODE_NMPC)
    ctrl._mpc.step = MagicMock(side_effect=AssertionError("QP must not run"))
    ctrl.set_accepted_path(
        np.full((ctrl.timing.n_slow, 1), 0.3),
        np.full((ctrl.horizon, 1), 21.0),
    )
    actions = ctrl.compute(outdoor_temp=0.0, now=_NOW)
    assert actions["h"] == pytest.approx(0.3)


def test_engine_preserves_linear_knobs_in_nmpc_mode():
    engine = ControlEngine(
        {
            "rooms": [
                {
                    "name": "living_room",
                    "thermal_mass": 5e6,
                    "r_external": 0.05,
                    "setpoint": 21.0,
                }
            ],
            "heat_sources": [
                {
                    "name": "h",
                    "type": "electric_heater",
                    "room": "living_room",
                    "max_power": 2000,
                }
            ],
            CONF_MPC_MODE: MPC_MODE_NMPC,
            "update_interval": 600,
            "horizon": 12,
            "nmpc_period": 1800,
            "nmpc_fast_substeps": 2,
            "nmpc_horizon_h": 1.0,
        }
    )
    assert engine.config["update_interval"] == 600
    assert engine.config["horizon"] == 12
    assert engine.config["nmpc_period"] == pytest.approx(1800.0)
    assert engine._controller is not None
    assert engine._controller.mpc_mode == MPC_MODE_NMPC


def test_tuning_page_has_exclusive_mode_cards():
    source = (
        ROOT / "heatingassistant" / "app" / "static" / "js" / "pages" / "tuning-controller.js"
    ).read_text(encoding="utf-8")
    assert "Linear model predictive control" in source
    assert "Nonlinear model predictive control" in source
    assert "linearisation error" in source
    assert "mpc_mode: selectedMode" in source
    assert "tuning-mode-card" in source
    assert "p_deadband" not in source
    assert "u_ref_gate" not in source
