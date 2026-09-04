"""SWD-481: receding N-step PE MLE, timeout, Advanced config."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from heatingassistant.engine.const import (
    CONF_PE_MAX_COMPUTE_S,
    DEFAULT_PE_MAX_COMPUTE_S,
)
from heatingassistant.engine.estimation.nstep_pem import timeout_user_message
from heatingassistant.engine.nmpc_timing import timing_from_options
from heatingassistant.engine.thermal_model import HouseModel
from tests.helpers.estimation_fixtures import (
    generate_history,
    make_electric_heaters,
    make_kalman_ml_estimator,
    make_single_room,
)


pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[1]


def _excited_history(*, n_steps: int, dt: float, seed: int = 3):
    """Duty-cycled heat and outdoor sine so tiled OE and N-step PEM differ."""
    rng = np.random.default_rng(seed)
    room = make_single_room(thermal_mass=8.0e6, r_external=0.025, temperature=20.0)
    sources = make_electric_heaters([room], max_power=2000.0)
    model = HouseModel([room])
    t0 = 1_700_000_000.0
    history = []
    for k in range(n_steps):
        duty = 0.85 if (k // 4) % 2 == 0 else 0.05
        tout = 2.0 + 6.0 * math.sin(2.0 * math.pi * k / 96.0)
        y = float(model.rooms[room.name].temperature) + float(rng.normal(0.0, 0.04))
        history.append(
            {
                "y": [y],
                "u": [duty],
                "d_outdoor": tout,
                "d_solar": {room.name: 0.0},
                "timestamp": t0 + k * dt,
            }
        )
        heat_inputs = {
            src.room: src.thermal_power(duty, tout) for src in sources
        }
        model.step(dt, heat_inputs, tout, {room.name: 0.0})
    return [room], sources, history


def test_timeout_message_names_advanced_and_shorter_data():
    msg = timeout_user_message(60.0)
    assert "1 minute" in msg
    assert "not applied" in msg
    assert "Advanced" in msg
    assert "shorter" in msg.lower() or "fewer" in msg.lower()


def test_pe_timeout_aborts_without_success():
    room = make_single_room()
    sources = make_electric_heaters([room])
    history = generate_history([room], sources, n_steps=80, dt=60.0)
    est = make_kalman_ml_estimator(
        [room], sources, dt=60.0,
        n_horizon_steps=24,
        origin_stride=4,
        max_compute_s=1e-6,
        use_nstep_pem=True,
    )
    result = est.estimate(history)
    assert result["success"] is False
    assert result.get("timed_out") is True
    assert "Advanced" in (result.get("message") or "")
    assert result.get("log_likelihood") is None


def test_nstep_pem_beats_tiled_oe_on_nstep_rmse():
    dt = 900.0
    rooms, sources, train = _excited_history(n_steps=96, dt=dt)
    common = dict(
        dt=dt,
        n_horizon_steps=16,
        origin_stride=8,
        max_window_steps=40,
        max_compute_s=120.0,
        regularization=0.01,
    )
    oe = make_kalman_ml_estimator(rooms, sources, use_nstep_pem=False, **common)
    pem = make_kalman_ml_estimator(rooms, sources, use_nstep_pem=True, **common)
    r_oe = oe.estimate(train)
    r_pem = pem.estimate(train)
    assert r_oe["success"]
    assert r_pem["success"]
    rmse_oe = oe.score_nstep_rmse()
    rmse_pem = pem.score_nstep_rmse()
    assert np.isfinite(rmse_oe) and np.isfinite(rmse_pem)
    assert rmse_pem < rmse_oe


def test_advanced_landing_card_and_page_exist():
    landing = (_ROOT / "heatingassistant/app/static/js/config/config-landing.js").read_text()
    assert "#config/advanced" in landing
    assert "Advanced" in landing
    adv = (_ROOT / "heatingassistant/app/static/js/config/config-advanced.js").read_text()
    assert "pe_max_compute" in adv
    cfg = (_ROOT / "heatingassistant/app/static/js/pages/configuration.js").read_text()
    assert "renderAdvanced" in cfg
    assert "page === 'advanced'" in cfg


def test_default_pe_max_compute_is_one_minute():
    assert DEFAULT_PE_MAX_COMPUTE_S == 60.0
    assert CONF_PE_MAX_COMPUTE_S == "pe_max_compute_s"


def test_nstep_objective_finite_at_prior():
    room = make_single_room()
    sources = make_electric_heaters([room])
    history = generate_history([room], sources, n_steps=80, dt=60.0)
    est = make_kalman_ml_estimator(
        [room], sources, dt=60.0,
        n_horizon_steps=8, origin_stride=4,
        max_compute_s=30.0, use_nstep_pem=True,
    )
    result = est.estimate(history)
    assert result["success"] is True
    assert result.get("log_likelihood") is not None
    assert np.isfinite(est.score_nstep_rmse())


def test_production_pe_grid_follows_nmpc_timing():
    timing = timing_from_options(
        {"nmpc_period": 7200.0, "nmpc_fast_substeps": 8, "nmpc_horizon_h": 36.0},
        default_period=7200.0,
        default_substeps=8,
        default_horizon_h=36.0,
    )
    room = make_single_room()
    sources = make_electric_heaters([room])
    est = make_kalman_ml_estimator(
        [room], sources, dt=timing.dt_s,
        n_horizon_steps=timing.n_fast,
        origin_stride=timing.fast_substeps,
        use_nstep_pem=True,
    )
    assert est._n_horizon_steps == timing.n_fast
    assert est._origin_stride == timing.fast_substeps
    assert timing.n_fast == 144
    assert timing.fast_substeps == 8
