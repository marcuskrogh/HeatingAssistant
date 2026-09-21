"""SWD-570: 1R1C live plant spec locks (pass criteria)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.controller import HouseThermalSDE
from heatingassistant.engine.controller.facade import HeatingMPCController
from heatingassistant.engine.estimation.kalman_ml import KalmanMLEstimator
from heatingassistant.engine.estimation.theta_layout import _ThetaLayout
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.thermal_model import HouseModel, Room
from heatingassistant.mqtt.bridge import InMemoryMqttBus


def _one_room(*, temperature: float = 20.0, r_external: float = 0.05) -> tuple:
    room = Room(
        name="studio",
        thermal_mass=5_000_000.0,
        r_external=r_external,
        temperature=temperature,
        setpoint=21.0,
        comfort_offset=1.0,
        infiltration_fraction=0.0,
        sky_radiative_ua=0.0,
        thermal_bridge_psi_l=0.0,
    )
    heater = ElectricHeater("h", "studio", max_power=4000.0)
    model = HouseModel([room])
    return model, heater


def test_physical_state_length_is_n_rooms() -> None:
    model, heater = _one_room()
    n = 1
    assert model._C.shape == (n,)
    assert model._A.shape == (n, n)
    sde = HouseThermalSDE(model, [heater], dt=900.0, augment_offsets=False)
    assert sde._nx_phys == n
    assert sde.nx == n


def test_steady_state_air_is_tout_plus_q_r() -> None:
    r_ext = 0.05
    Q = 1000.0
    tout = 0.0
    model, _heater = _one_room(temperature=tout, r_external=r_ext)
    for _ in range(8000):
        model.step(dt=900.0, heat_inputs={"studio": Q}, outdoor_temp=tout, solar_gains={})
    expected = tout + Q * r_ext
    assert model.temperatures["studio"] == pytest.approx(expected, abs=0.05)


def test_cd_kalman_hm_observes_only_air() -> None:
    model, heater = _one_room(temperature=19.0)
    sde = HouseThermalSDE(model, [heater], dt=900.0, augment_offsets=True)
    x = np.array([19.0, 0.4], dtype=float)
    ym = sde.hm(x, np.zeros(sde.nu), sde.disturbance_vector(5.0, {}), np.array([]), 0.0)
    assert ym.shape == (1,)
    assert ym[0] == pytest.approx(19.4)


def test_pe_theta_has_no_wall_or_split_blocks() -> None:
    layout = _ThetaLayout(n_rooms=2, identifiable_sources=[], identifiable_pairs=[])
    assert layout.idx_t_wall_init[0] == layout.idx_t_wall_init[1]
    assert layout.idx_c_air[0] == layout.idx_c_air[1]
    assert layout.idx_r_aw[0] == layout.idx_r_aw[1]
    _log_m, _log_r, _q, t_wall, _a, _rij, _s, c_air, r_aw = layout.unpack(
        np.zeros(layout.size)
    )
    assert t_wall.size == 0
    assert c_air.size == 0
    assert r_aw.size == 0


def test_pe_estimate_omits_wall_and_split_keys() -> None:
    model, heater = _one_room()
    est = KalmanMLEstimator(list(model.rooms.values()), [heater], dt=900.0)
    result = est.estimate([])
    assert "estimated_t_wall_initial" not in result
    assert "estimated_envelope_splits" not in result
    assert "t_wall_initial" not in result
    assert "c_air_fraction" not in result
    assert "r_aw_fraction" not in result


def test_nmpc_apply_is_ustar_k_not_kp() -> None:
    model, heater = _one_room(temperature=16.0)
    ctrl = HeatingMPCController(
        model,
        [heater],
        horizon=4,
        dt=900.0,
        mpc_mode="nmpc",
        nmpc_fast_substeps=1,
        nmpc_horizon_h=1.0,
    )
    U = np.zeros((ctrl.timing.n_slow, 1), dtype=float)
    U[0, 0] = 0.25
    U[1, 0] = 0.55
    ctrl._nmpc_U = U
    ctrl._nmpc_k = 1
    u = ctrl._p_command_vector(None, None, None)
    assert u.shape == (1,)
    assert u[0] == pytest.approx(0.55)


def test_nmpc_replans_heat_when_air_is_below_comfort() -> None:
    model, heater = _one_room(temperature=16.0)
    ctrl = HeatingMPCController(
        model,
        [heater],
        horizon=4,
        dt=900.0,
        mpc_mode="nmpc",
        nmpc_fast_substeps=1,
        nmpc_horizon_h=1.0,
    )
    plan = ctrl.solve_nmpc(
        outdoor_temp=-5.0,
        now=datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc),
        timeout_s=8.0,
        maxiter=30,
    )
    u_star = np.asarray(plan["u_star"], dtype=float)
    assert plan["accepted"] is True
    assert float(np.max(u_star)) > 0.1


def inspect_source() -> str:
    from heatingassistant.engine.controller import facade as facade_mod

    return Path(facade_mod.__file__).read_text(encoding="utf-8")


def test_p_command_source_is_ustar_hold() -> None:
    text = inspect_source()
    assert "u_ref + Kp" not in text
    assert "Hold the remaining planned" in text


def test_runtime_does_not_publish_wall_entity(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "rooms": [{"name": "Living Room", "setpoint": 21.0}],
        },
    )
    states = runtime.hass_states()
    assert "sensor.heating_assistant_living_room_temperature_wall" not in states


def test_control_docs_are_nmpc_only() -> None:
    control = Path("docs/agents/CONTROL.md").read_text(encoding="utf-8")
    assert "u = U*[k]" in control
    assert "There is no inner P/PID loop" in control
    roadmap = Path("docs/ROADMAP.md").read_text(encoding="utf-8")
    assert "Superseded as the live control loop" in roadmap


def test_room_chart_source_drops_wall_series() -> None:
    charts = Path("heatingassistant/app/static/js/charts/room-charts.js").read_text(
        encoding="utf-8"
    )
    detail = Path("heatingassistant/app/static/js/pages/room-detail.js").read_text(
        encoding="utf-8"
    )
    assert "replaceChartDataset(ds, 'Wall Forecast'" not in detail
    assert "makeDataset('Wall'" not in charts or "wallHistory.length > 0" in charts
    assert "const wallHistory = [];" in charts
    assert "const wallForecast = [];" in charts
