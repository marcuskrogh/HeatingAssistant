"""Two-layer NMPC tracker is gone: commands hold planned U* (ZOH)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.thermal_model import HouseModel, Room
from heatingassistant.mqtt.bridge import InMemoryMqttBus

_ROOT = Path(__file__).resolve().parents[1]
_APP_TREES = (
    _ROOT / "heatingassistant" / "app" / "static",
    _ROOT / "heating_assistant" / "heatingassistant" / "app" / "static",
)


def _ctrl(*, temperature: float, u_ref: float, t_ref: float) -> HeatingMPCController:
    room = Room(
        "living_room",
        5e6,
        0.05,
        temperature=temperature,
        setpoint=21.0,
        comfort_offset=2.0,
    )
    heater = ElectricHeater("h", "living_room", max_power=2000.0)
    ctrl = HeatingMPCController(HouseModel([room]), [heater], horizon=2, dt=900.0)
    n_fast = ctrl.horizon
    ctrl.set_accepted_path(
        np.full((ctrl.timing.n_slow, 1), u_ref),
        np.full((n_fast, 1), t_ref),
    )
    ctrl._ekf.x_hat[0] = temperature
    return ctrl


def test_command_holds_u_ref_when_air_is_off_plan() -> None:
    ctrl = _ctrl(temperature=18.0, u_ref=0.2, t_ref=21.0)
    u = ctrl._p_command_vector(None, None, None)
    assert u[0] == pytest.approx(0.2)


def test_command_holds_zero_u_ref_inside_old_deadband() -> None:
    ctrl = _ctrl(temperature=20.5, u_ref=0.0, t_ref=21.0)
    u = ctrl._p_command_vector(None, None, None)
    assert u[0] == pytest.approx(0.0)


def test_no_plan_holds_zero_instead_of_comfort_p() -> None:
    room = Room(
        "living_room",
        5e6,
        0.05,
        temperature=18.0,
        setpoint=21.0,
        comfort_offset=2.0,
    )
    heater = ElectricHeater("h", "living_room", max_power=2000.0)
    ctrl = HeatingMPCController(HouseModel([room]), [heater], horizon=2, dt=900.0)
    ctrl._ekf.x_hat[0] = 18.0
    u = ctrl._p_command_vector(None, None, None)
    assert u[0] == pytest.approx(0.0)


def test_tuning_ui_has_no_two_layer_tracker_knobs() -> None:
    for static in _APP_TREES:
        source = (static / "js" / "pages" / "tuning-controller.js").read_text(
            encoding="utf-8"
        )
        assert "p_deadband" not in source
        assert "u_ref_gate" not in source
        assert "P deadband (NMPC off)" not in source
        assert "NMPC-off gate" not in source
        assert "Nonlinear tracker" not in source


def test_heater_p_gain_editor_removed() -> None:
    source = (
        _ROOT
        / "heatingassistant"
        / "app"
        / "static"
        / "js"
        / "config"
        / "config-source-editor.js"
    ).read_text(encoding="utf-8")
    assert "numberField(src, 'p_gain'" not in source
    assert "P gain" not in source
    assert "max_temp_offset" in source


def test_controller_config_omits_tracker_knobs(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "rooms": [{"name": "Living Room", "setpoint": 21.0}],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 1500.0,
                }
            ],
        },
    )
    cfg = runtime.controller_config()
    assert "p_deadband" not in cfg
    assert "u_ref_gate" not in cfg


def test_engine_still_rejects_negative_legacy_tracker_keys() -> None:
    cfg = {
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
        "p_deadband": -0.1,
    }
    with pytest.raises(ValueError, match="p_deadband"):
        ControlEngine(cfg)
