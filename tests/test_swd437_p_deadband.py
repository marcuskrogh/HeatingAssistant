"""Gated P deadband when NMPC feedforward is near zero."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.const import DEFAULT_P_DEADBAND, DEFAULT_U_REF_GATE
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources import ElectricHeater, HeatPump
from heatingassistant.engine.nmpc_p import comfort_fallback_command, p_command
from heatingassistant.engine.thermal_model import HouseModel, Room
from heatingassistant.mqtt.bridge import InMemoryMqttBus


def test_p_command_ungated_default_unchanged() -> None:
    assert p_command(0.0, 21.0, 20.5, 0.1, 0.0, 1.0) == pytest.approx(0.05)


def test_p_deadband_holds_off_near_zero_u_ref() -> None:
    assert (
        p_command(
            0.0,
            21.0,
            20.5,
            0.1,
            0.0,
            1.0,
            u_ref_gate=0.02,
            p_deadband=1.0,
        )
        == 0.0
    )
    assert (
        p_command(
            0.01,
            21.0,
            20.2,
            0.1,
            0.0,
            1.0,
            u_ref_gate=0.02,
            p_deadband=1.0,
        )
        == 0.0
    )


def test_p_deadband_inclusive_edge() -> None:
    assert (
        p_command(
            0.0,
            21.0,
            20.0,
            0.1,
            0.0,
            1.0,
            u_ref_gate=0.02,
            p_deadband=1.0,
        )
        == 0.0
    )


def test_p_deadband_tracks_when_error_exceeds_band() -> None:
    assert p_command(
        0.0,
        21.0,
        18.0,
        0.1,
        0.0,
        1.0,
        u_ref_gate=0.02,
        p_deadband=1.0,
    ) == pytest.approx(0.3)


def test_p_deadband_does_not_swallow_preheat() -> None:
    assert p_command(
        0.05,
        21.0,
        20.5,
        0.1,
        0.0,
        1.0,
        u_ref_gate=0.02,
        p_deadband=1.0,
    ) == pytest.approx(0.1)


def test_u_ref_at_gate_is_not_deadbanded() -> None:
    assert p_command(
        0.02,
        21.0,
        20.5,
        0.1,
        0.0,
        1.0,
        u_ref_gate=0.02,
        p_deadband=1.0,
    ) == pytest.approx(0.07)


def test_p_deadband_cools_only_outside_band_when_nmpc_off() -> None:
    assert (
        p_command(
            -0.01,
            21.0,
            21.4,
            0.1,
            -1.0,
            1.0,
            u_ref_gate=0.02,
            p_deadband=1.0,
        )
        == 0.0
    )
    assert p_command(
        -0.01,
        21.0,
        23.0,
        0.1,
        -1.0,
        1.0,
        u_ref_gate=0.02,
        p_deadband=1.0,
    ) == pytest.approx(-0.21)


def test_comfort_fallback_still_ungated() -> None:
    assert comfort_fallback_command(20.5, 21.0, 2.0, 0.1, 0.0, 1.0) == 0.0
    assert comfort_fallback_command(18.0, 21.0, 2.0, 0.1, 0.0, 1.0) == pytest.approx(0.3)


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


def test_controller_zero_when_nmpc_off_inside_deadband() -> None:
    ctrl = _ctrl(temperature=20.5, u_ref=0.0, t_ref=21.0)
    assert ctrl._p_deadband == pytest.approx(DEFAULT_P_DEADBAND)
    assert ctrl._u_ref_gate == pytest.approx(DEFAULT_U_REF_GATE)
    u = ctrl._p_command_vector(None, None, None)
    assert u[0] == pytest.approx(0.0)


def test_controller_tracks_when_nmpc_preheats() -> None:
    ctrl = _ctrl(temperature=20.5, u_ref=0.05, t_ref=21.0)
    u = ctrl._p_command_vector(None, None, None)
    assert u[0] == pytest.approx(0.1)


def test_controller_tracks_when_error_exceeds_deadband() -> None:
    ctrl = _ctrl(temperature=18.0, u_ref=0.0, t_ref=21.0)
    u = ctrl._p_command_vector(None, None, None)
    assert u[0] == pytest.approx(0.3)


def test_heat_pump_off_when_nmpc_off_inside_deadband() -> None:
    room = Room(
        "living_room",
        5e6,
        0.05,
        temperature=21.4,
        setpoint=21.0,
        comfort_offset=3.0,
    )
    hp = HeatPump("hp", "living_room", max_power=4000.0, hvac_mode="heat_cool")
    ctrl = HeatingMPCController(HouseModel([room]), [hp], horizon=2, dt=900.0)
    ctrl.set_accepted_path(
        np.zeros((ctrl.timing.n_slow, 1)),
        np.full((ctrl.horizon, 1), 21.0),
    )
    ctrl._ekf.x_hat[0] = 21.4
    assert ctrl._p_command_vector(None, None, None)[0] == pytest.approx(0.0)


def test_control_engine_reads_deadband_keys() -> None:
    engine = ControlEngine(
        {
            "update_interval": 900,
            "horizon": 2,
            "p_deadband": 1.5,
            "u_ref_gate": 0.05,
            "rooms": [{"name": "Living Room", "setpoint": 21.0}],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 1500.0,
                }
            ],
        }
    )
    assert engine._controller is not None
    assert engine._controller._p_deadband == pytest.approx(1.5)
    assert engine._controller._u_ref_gate == pytest.approx(0.05)


def test_negative_deadband_and_gate_rejected() -> None:
    room = Room("living_room", 5e6, 0.05, temperature=21.0, setpoint=21.0)
    heater = ElectricHeater("h", "living_room", max_power=2000.0)
    with pytest.raises(ValueError, match="p_deadband"):
        HeatingMPCController(
            HouseModel([room]), [heater], horizon=2, dt=900.0, p_deadband=-0.1
        )
    with pytest.raises(ValueError, match="u_ref_gate"):
        HeatingMPCController(
            HouseModel([room]), [heater], horizon=2, dt=900.0, u_ref_gate=-0.01
        )


def test_controller_config_exposes_deadband_defaults(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path, bus=InMemoryMqttBus(), options={"instance_id": "haos"}
    )
    cfg = runtime.controller_config()
    assert cfg["p_deadband"] == pytest.approx(DEFAULT_P_DEADBAND)
    assert cfg["u_ref_gate"] == pytest.approx(DEFAULT_U_REF_GATE)


def test_tuning_pane_exposes_deadband_knobs() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "heatingassistant"
        / "app"
        / "static"
        / "js"
        / "pages"
        / "tuning-controller.js"
    ).read_text(encoding="utf-8")
    assert "p_deadband" in source
    assert "u_ref_gate" in source
    assert "P deadband (NMPC off)" in source
    assert "NMPC-off gate" in source
