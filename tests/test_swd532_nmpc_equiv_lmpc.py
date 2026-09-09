"""SWD-532: Nonlinear MPC on the same sample grid as Linear."""

from __future__ import annotations

from pathlib import Path

import pytest

from heatingassistant.engine.const import (
    DEFAULT_NMPC_FAST_SUBSTEPS,
    DEFAULT_NMPC_HORIZON_H,
    DEFAULT_NMPC_PERIOD,
    MPC_MODE_LINEAR,
    MPC_MODE_NMPC,
)
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.engine.nmpc_timing import (
    coerce_to_sample_grid,
    timing_from_options,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
TUNING_JS = ROOT / "heatingassistant" / "app" / "static" / "js" / "pages" / "tuning-controller.js"


def test_legacy_two_rate_coerces_to_one_decision_per_sample() -> None:
    timing = coerce_to_sample_grid(7200.0, 8, 36.0)
    assert timing.dt_s == pytest.approx(900.0)
    assert timing.fast_substeps == 1
    assert timing.period_s == pytest.approx(900.0)
    assert timing.n_slow == 144
    assert timing.n_fast == 144
    assert timing.horizon_h == pytest.approx(36.0)


def test_options_without_interval_coerce_stored_triple() -> None:
    timing = timing_from_options(
        {
            "nmpc_period": 1800,
            "nmpc_fast_substeps": 2,
            "nmpc_horizon_h": 1.0,
        },
        default_period=DEFAULT_NMPC_PERIOD,
        default_substeps=DEFAULT_NMPC_FAST_SUBSTEPS,
        default_horizon_h=DEFAULT_NMPC_HORIZON_H,
    )
    assert timing.dt_s == pytest.approx(900.0)
    assert timing.fast_substeps == 1
    assert timing.n_fast == 4
    assert timing.n_slow == 4


def test_linear_and_nmpc_share_interval_horizon_grid() -> None:
    kwargs = dict(
        default_period=DEFAULT_NMPC_PERIOD,
        default_substeps=DEFAULT_NMPC_FAST_SUBSTEPS,
        default_horizon_h=DEFAULT_NMPC_HORIZON_H,
    )
    payload = {"update_interval": 900, "horizon": 8}
    linear = timing_from_options({**payload, "mpc_mode": MPC_MODE_LINEAR}, **kwargs)
    nmpc = timing_from_options({**payload, "mpc_mode": MPC_MODE_NMPC}, **kwargs)
    assert linear.dt_s == pytest.approx(nmpc.dt_s)
    assert linear.n_fast == nmpc.n_fast
    assert linear.fast_substeps == 1
    assert nmpc.fast_substeps == 1
    assert linear.period_s == pytest.approx(linear.dt_s)


def test_engine_persists_synced_timing_aliases() -> None:
    engine = ControlEngine(
        {
            "rooms": [{"name": "Living Room", "setpoint": 21.0}],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 1500.0,
                }
            ],
            "nmpc_period": 7200,
            "nmpc_fast_substeps": 8,
            "nmpc_horizon_h": 36,
        }
    )
    assert engine.config["update_interval"] == pytest.approx(900.0)
    assert engine.config["horizon"] == 144
    assert engine.config["nmpc_period"] == pytest.approx(900.0)
    assert engine.config["nmpc_fast_substeps"] == 1
    assert engine.config["nmpc_horizon_h"] == pytest.approx(36.0)
    assert engine._controller is not None
    assert engine._controller.timing.fast_substeps == 1
    assert engine._controller.timing.n_slow == 144


def test_tuning_page_has_shared_timing_no_plan_period() -> None:
    source = TUNING_JS.read_text(encoding="utf-8")
    assert "Plan period" not in source
    assert "ctrl-nmpc_sample_interval" not in source
    assert 'id="ctrl-look_ahead_h"' in source
    assert "label: 'Sample interval'" in source
    assert "the same receding-horizon problem as Linear" in source
    assert "nmpc_fast_substeps: 1" in source
    assert "nmpc_period: 900" in source
    assert "SHARED_RESTART_PARAM_DEFS" in source
    assert "HIDDEN_TIMING_PARAM_DEFS" in source
    labelled, hidden = source.split("HIDDEN_TIMING_PARAM_DEFS", 1)
    assert "Plan period" not in labelled
    assert "nmpc_period" in hidden
    countdown = (
        ROOT / "heatingassistant" / "app" / "static" / "js" / "components" / "countdown.js"
    ).read_text(encoding="utf-8")
    compute_block = countdown.split("COUNTDOWN_COMPUTE", 1)[1].split("export const COUNTDOWN_CONTROL", 1)[0]
    assert "defaultDt: 900" in compute_block
    assert "defaultDt: 7200" not in compute_block
