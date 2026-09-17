"""SWD-564: wall EKF process noise must not dump heat-pulse innovations into Tw."""

from __future__ import annotations

import numpy as np
import pytest

from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources.electric import ElectricHeater
from heatingassistant.engine.thermal_model import HouseModel, Room


pytestmark = pytest.mark.unit


def _controller() -> HeatingMPCController:
    room = Room(
        name="Living Room",
        thermal_mass=5_000_000.0,
        r_external=0.05,
        temperature=22.0,
        wall_temperature=21.0,
        setpoint=22.0,
    )
    heater = ElectricHeater("heater", room="Living Room", max_power=6000.0)
    return HeatingMPCController(
        HouseModel([room]),
        [heater],
        horizon=4,
        dt=900.0,
        measurement_dt=900.0,
        n_int_steps=10,
        mpc_mode="nmpc",
        nmpc_period=900.0,
        nmpc_fast_substeps=1,
        nmpc_horizon_h=1.0,
        sigma_w=0.1,
        sigma_v=0.5,
    )


def test_heat_pulse_does_not_crash_wall_below_outdoor() -> None:
    """Pinned air + 5 kW must not send reconstructed Tw toward 0 °C."""

    ctrl = _controller()
    t_out = 14.0
    t_air = 22.0
    walls = []
    for k in range(16):  # 4 h
        hour = k * 0.25
        u_frac = 5000.0 / 6000.0 if 1.0 <= hour < 3.0 else 0.0
        u = np.array([u_frac], dtype=float)
        d = ctrl._control_system.disturbance_vector(t_out, {"Living Room": 0.0})
        p = np.array([], dtype=float)
        y = np.array([t_air], dtype=float)
        ctrl._ekf.predict(u, d, p, 0.0)
        ctrl._ekf.update(y, u, d, p)
        walls.append(float(ctrl.wall_temperatures["Living Room"]))
    assert min(walls) >= t_out - 2.0
    assert max(walls) <= t_air + 5.0


def test_wall_sigma_scales_with_air_to_wall_capacitance() -> None:
    ctrl = _controller()
    sde = ctrl._system
    n = sde._n_rooms
    sig = np.diag(sde._sigma_matrix)
    ratio = min(float(sde._C_cap[0] / sde._C_cap[n]), 1.0)
    assert sig[0] == pytest.approx(0.1)
    assert sig[n] == pytest.approx(0.1 * ratio)
    assert sig[n] < sig[0]
