"""
Regression tests for the EKF-reconstruction initial state.

The system-identification EKF reconstruction used to cold-start every latent
state at 0: it built ``x = np.zeros(nx)`` and overwrote only the air-temperature
block, so the 2R2C wall node began at **0 °C**.  The reconstruction then spent a
long EKF transient climbing the wall back to a physical value, distorting the
start of the plotted trajectory and the early one-step residuals.

The reconstruction now seeds the full state from the first measurement via the
shared ``initial_state_from_measurement`` helper (the same one the open-loop
diagnostic and the estimator objective use): air temps from the measurement, the
wall node equal to the air node (``T_air = T_envelope`` — the unbiased seed for
the unobserved envelope), emitter lags warm-started.
"""

import numpy as np

from heatingassistant.engine.controller import HouseThermalSDE
from heatingassistant.engine.thermal_model import HouseModel, Room
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.sysid import (
    run_sysid_ekf,
    _init_state_from_measurement,
)


def _system(dt=900.0):
    room = Room("studio", 4e6, 0.05, temperature=20.0, setpoint=21.0)
    model = HouseModel([room])
    heater = ElectricHeater("h", "studio", 3000.0)
    sde = HouseThermalSDE(model, [heater], dt, augment_offsets=False)
    return model, heater, sde


def _history(dt=900.0, n=40, air=20.5, outdoor=5.0):
    t0 = 1_000_000.0
    return [
        {"y": [air], "u": [0.3], "d_outdoor": outdoor,
         "d_solar": {"studio": 0.0}, "timestamp": t0 + dt * i}
        for i in range(n)
    ]


def test_reconstruction_wall_initialised_at_air_not_zero():
    """The anchor wall temperature must equal the air temperature (Tair=Tenv)."""
    dt = 900.0
    model, heater, _ = _system(dt)
    air, outdoor = 20.5, 5.0
    hist = _history(dt, air=air, outdoor=outdoor)

    res = run_sysid_ekf(hist, model, [heater], ["studio"], dt,
                        horizon_steps=len(hist), room_params={},
                        sigma_w=0.1, sigma_v=0.5)
    anchor = res["per_room"]["studio"]["simulation"][0]

    assert anchor["predicted"] == air            # air seeded from measurement
    wall = anchor["predicted_wall"]
    assert wall is not None
    # The unobserved envelope starts equal to the air node (unbiased seed),
    # not at the old 0 °C cold start nor the parameter-dependent steady state.
    assert wall == air


def test_init_helper_seeds_wall_at_air():
    """The helper delegates to the SDE; the wall starts equal to the air node."""
    dt = 900.0
    _, _, sde = _system(dt)
    n, nx = 1, int(sde.nx)
    u = np.array([0.3])
    d = np.array([5.0, 0.0])  # [T_out, q_solar]
    x = _init_state_from_measurement(sde, [20.5], n, nx, u, d)

    assert x.shape[0] == nx
    assert x[0] == 20.5                # air from measurement
    if nx >= 2 * n:
        assert x[1] == 20.5            # wall equals air, not 0 nor steady state


def test_init_helper_fallback_without_sde_helper():
    """Legacy/monkeypatched SDEs (no helper) still get wall = air, never 0."""
    class _Bare:
        nx = 2

    x = _init_state_from_measurement(_Bare(), [18.0], n=1, n_x=2,
                                     u_vec=np.array([0.0]),
                                     d_vec=np.array([0.0]))
    assert x[0] == 18.0
    assert x[1] == 18.0   # wall falls back to air temperature, not 0
