"""SWD-542: configurable NMPC wall-clock compute cap."""

from __future__ import annotations

from pathlib import Path

import pytest

from heatingassistant.engine.const import (
    CONF_NMPC_MAX_COMPUTE_S,
    DEFAULT_NMPC_MAX_COMPUTE_S,
    coerce_nmpc_max_compute_s,
)
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.nmpc_ocp import NMPC_TIMEOUT_S
from heatingassistant.engine.thermal_model import HouseModel, Room

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
TUNING_JS = ROOT / "heatingassistant" / "app" / "static" / "js" / "pages" / "tuning-controller.js"


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
    return HeatingMPCController(model, [heater], horizon=4, dt=900.0, **kwargs)


def _tiny_engine(**config) -> ControlEngine:
    payload = {
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
        "update_interval": 900,
        "horizon": 4,
        **config,
    }
    return ControlEngine(payload)


def test_default_nmpc_timeout_is_one_minute() -> None:
    assert DEFAULT_NMPC_MAX_COMPUTE_S == pytest.approx(60.0)
    assert NMPC_TIMEOUT_S == pytest.approx(60.0)
    assert coerce_nmpc_max_compute_s(None) == pytest.approx(60.0)
    assert coerce_nmpc_max_compute_s(0) == pytest.approx(60.0)
    assert coerce_nmpc_max_compute_s(-5) == pytest.approx(60.0)
    assert coerce_nmpc_max_compute_s("nope") == pytest.approx(60.0)
    ctrl = _tiny_ctrl()
    assert ctrl._nmpc_timeout_s == pytest.approx(60.0)


def test_configured_nmpc_timeout_is_stored_on_controller() -> None:
    ctrl = _tiny_ctrl(nmpc_max_compute_s=12.5)
    assert ctrl._nmpc_timeout_s == pytest.approx(12.5)


def test_engine_passes_nmpc_max_compute_into_controller() -> None:
    engine = _tiny_engine(**{CONF_NMPC_MAX_COMPUTE_S: 90.0})
    assert engine._controller is not None
    assert engine._controller._nmpc_timeout_s == pytest.approx(90.0)


def test_engine_invalid_nmpc_max_compute_falls_back() -> None:
    engine = _tiny_engine(**{CONF_NMPC_MAX_COMPUTE_S: 0})
    assert engine._controller is not None
    assert engine._controller._nmpc_timeout_s == pytest.approx(60.0)


def test_tuning_page_exposes_shared_max_compute() -> None:
    source = TUNING_JS.read_text(encoding="utf-8")
    assert "NMPC_LIVE_PARAM_DEFS" not in source
    assert "nmpcLiveSubsection" not in source
    assert "Nonlinear MPC solver" not in source
    shared, _rest = source.split("LINEAR_LIVE_PARAM_DEFS", 1)
    assert "nmpc_max_compute_s" in shared
    assert "Max compute time" in shared
    assert "one planner solve" in shared
    assert "nmpc_max_compute_s: 60" in source
    assert "linearLiveSubsection.hidden = selectedMode !== MPC_MODE_LINEAR" in source
    assert "nmpcLiveSubsection.hidden" not in source


def test_linear_controller_stores_max_compute() -> None:
    ctrl = _tiny_ctrl(mpc_mode="linear", nmpc_max_compute_s=12.5)
    assert ctrl._nmpc_timeout_s == pytest.approx(12.5)


def test_controller_config_snapshot_includes_nmpc_max_compute(tmp_path) -> None:
    from heatingassistant.app.runtime import HeatingRuntime

    runtime = HeatingRuntime(tmp_path, options={"instance_id": "t"})
    cfg = runtime.controller_config()
    assert cfg[CONF_NMPC_MAX_COMPUTE_S] == pytest.approx(60.0)
    runtime.options[CONF_NMPC_MAX_COMPUTE_S] = 45.0
    cfg = runtime.controller_config()
    assert cfg[CONF_NMPC_MAX_COMPUTE_S] == pytest.approx(45.0)
