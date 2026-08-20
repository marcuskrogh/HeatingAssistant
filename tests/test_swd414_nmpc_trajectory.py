"""SWD-414: accept useful NMPC plans and plot that trajectory."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from heatingassistant.engine.const import (
    DEFAULT_NMPC_FAST_SUBSTEPS,
    DEFAULT_NMPC_HORIZON_H,
    DEFAULT_NMPC_PERIOD,
)
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.engine.nmpc_accept import ACCEPT_J_RATIO, accept_plan

_NOW = datetime(2026, 8, 20, 13, 10, tzinfo=timezone.utc)
_ROOM_JS = (
    Path(__file__).resolve().parents[1]
    / "heatingassistant"
    / "app"
    / "static"
    / "js"
    / "pages"
    / "room-detail.js"
)


def test_accept_plan_keeps_useful_cooling_that_is_not_near_zero_heat():
    lo = np.array([-1.0])
    hi = np.array([1.0])
    j0 = 1e6
    assert accept_plan(np.array([-0.4]), 0.05 * j0, j0, lo, hi)
    assert accept_plan(np.array([-0.4]), 0.5 * j0, j0, lo, hi)
    assert accept_plan(np.array([-0.4]), (1.0 - ACCEPT_J_RATIO) * j0, j0, lo, hi)
    assert not accept_plan(np.array([0.0]), j0, j0, lo, hi)
    assert not accept_plan(np.array([-0.01]), 0.9995 * j0, j0, lo, hi)


def test_room_detail_refetches_forecasts_on_nmpc_stamp():
    source = _ROOM_JS.read_text(encoding="utf-8")
    assert "function mpcForecastStamp" in source
    assert "last_nmpc_ts" in source
    assert "replaceChartDataset(ds, 'Forecast'" in source
    assert "replaceChartDataset(ds, 'Planned Power'" in source
    update_fn = source.split("function updateChartsFromState", 1)[1]
    extend_pos = update_fn.index("extendLiveChartHistory")
    stamp_pos = update_fn.index("mpcForecastStamp(state)")
    assert extend_pos < stamp_pos


def test_nmpc_cools_in_band_summer_solar_and_plots_plan():
    engine = ControlEngine(
        {
            "nmpc_period": DEFAULT_NMPC_PERIOD,
            "nmpc_fast_substeps": DEFAULT_NMPC_FAST_SUBSTEPS,
            "nmpc_horizon_h": DEFAULT_NMPC_HORIZON_H,
            "latitude": 55.67,
            "longitude": 12.57,
            "energy_price_weight": 1.0,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 23.5,
                    "comfort_offset": 2.0,
                    "temperature": 25.0,
                    "solar_exposure": "high",
                    "solar_facing": 180.0,
                }
            ],
            "heat_sources": [
                {
                    "name": "hp",
                    "type": "heat_pump",
                    "room": "Living Room",
                    "max_power": 7000.0,
                    "hvac_mode": "heat_cool",
                }
            ],
        }
    )
    n_fast = engine._controller.horizon
    outdoor = [22.0] * n_fast
    prices = [2.0] * n_fast
    engine.compute_actions(
        {"Living Room": 25.0},
        22.0,
        {"Living Room": 23.5},
        now=_NOW,
        outdoor_forecast=outdoor,
        price_forecast=prices,
    )
    before = engine.forecast_snapshot()["heating_schedule"]
    assert before
    idle_watts = [float(step["Living Room"]) for step in before]
    idle_temps = [
        float(step["Living Room"])
        for step in engine.forecast_snapshot()["predictions"]
    ]
    assert max(abs(w) for w in idle_watts) < 1.0
    assert max(idle_temps) > 27.0

    engine.mark_nmpc_busy()
    plan = engine.solve_nmpc_blocking()
    u_star = np.asarray(plan["u_star"], dtype=float)
    assert plan["accepted"] is True
    assert float(np.min(u_star)) < -0.05
    assert float(plan["fun"]) <= (1.0 - ACCEPT_J_RATIO) * float(plan["cost_zero"])
    assert engine.apply_nmpc_result(plan) is True

    snap = engine.forecast_snapshot()
    watts = [float(step["Living Room"]) for step in snap["heating_schedule"]]
    temps = [float(step["Living Room"]) for step in snap["predictions"]]
    assert min(watts) < -100.0
    assert max(temps) < max(idle_temps) - 1.0
    assert max(temps) < 28.5
