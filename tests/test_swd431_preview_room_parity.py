"""SWD-431: Tuning preview with live params plots the room-view remaining plan."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from heatingassistant.app.forecast_payload import build_app_forecast_payload
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.engine.naming import room_slug

_NOW = datetime(2026, 8, 21, 13, 0, tzinfo=timezone.utc)
_ROOM = "Living Room"
_SLUG = room_slug(_ROOM)


def _engine() -> ControlEngine:
    return ControlEngine(
        {
            "nmpc_period": 1800.0,
            "nmpc_fast_substeps": 2,
            "nmpc_horizon_h": 1.0,
            "tracking_weight": 0.0,
            "energy_weight": 0.01,
            "smoothing_weight": 0.1,
            "comfort_offset": 2.0,
            "rooms": [
                {
                    "name": _ROOM,
                    "setpoint": 23.5,
                    "comfort_offset": 2.0,
                    "temperature": 24.0,
                }
            ],
            "heat_sources": [
                {
                    "name": "hp",
                    "type": "heat_pump",
                    "room": _ROOM,
                    "max_power": 7000.0,
                    "hvac_mode": "heat_cool",
                }
            ],
        }
    )


def _install_plan(engine: ControlEngine) -> None:
    ctrl = engine._controller
    assert ctrl is not None
    n_fast = ctrl.horizon
    n_slow = ctrl.timing.n_slow
    ctrl.set_accepted_path(
        np.full((n_slow, 1), -0.2),
        np.linspace(24.0, 23.0, n_fast).reshape(-1, 1),
        plan_epoch=_NOW.timestamp(),
        now=_NOW.timestamp(),
    )
    ctrl._outdoor_forecast = [20.0] * n_fast
    ctrl._solar_forecast = [{_ROOM: 0.0} for _ in range(n_fast)]
    ctrl._price_forecast = [1.0] * n_fast
    assert ctrl.rebuild_forecast_from_plan() is True
    engine._cache_controller_forecast(ctrl)


def test_matching_preview_reuses_live_room_snapshot() -> None:
    engine = _engine()
    if engine._controller is None:
        pytest.skip("MPC controller unavailable in this environment")
    _install_plan(engine)
    live = engine.forecast_snapshot()
    built = {"n": 0}
    orig = engine._build_controller_from_config

    def counting(config, **kwargs):
        built["n"] += 1
        return orig(config, **kwargs)

    engine._build_controller_from_config = counting  # type: ignore[method-assign]
    preview = engine.preview_tuning_forecast(
        {
            "tracking_weight": 0.0,
            "energy_weight": 0.01,
            "smoothing_weight": 0.1,
            "comfort_offset": 2.0,
            "nmpc_period": 1800.0,
            "nmpc_fast_substeps": 2,
            "nmpc_horizon_h": 1.0,
        },
        {_ROOM: 24.0},
        20.0,
        {_ROOM: 23.5},
        outdoor_forecast=[20.0] * engine._controller.horizon,
        price_forecast=[1.0] * engine._controller.horizon,
        now=_NOW,
    )
    assert built["n"] == 0
    assert preview.get("predictions") == live["predictions"]
    assert preview.get("heating_schedule") == live["heating_schedule"]
    live_payload = build_app_forecast_payload(
        rooms=[{"name": _ROOM, "setpoint": 23.5, "comfort_offset": 2.0}],
        room_temperatures={_ROOM: 24.0},
        outdoor_temp=20.0,
        energy_price=1.0,
        snapshot=live,
        now=_NOW,
    )
    preview_payload = build_app_forecast_payload(
        rooms=[{"name": _ROOM, "setpoint": 23.5, "comfort_offset": 2.0}],
        room_temperatures={_ROOM: 24.0},
        outdoor_temp=20.0,
        energy_price=1.0,
        snapshot=preview,
        now=_NOW,
    )
    live_fc = live_payload["rooms"][_SLUG]["forecast"]
    prev_fc = preview_payload["rooms"][_SLUG]["forecast"]
    assert [step.get("temperature") for step in live_fc] == [
        step.get("temperature") for step in prev_fc
    ]
    assert [step.get("heating_power") for step in live_fc] == [
        step.get("heating_power") for step in prev_fc
    ]


def test_changed_weight_still_rebuilds_preview_controller() -> None:
    engine = _engine()
    if engine._controller is None:
        pytest.skip("MPC controller unavailable in this environment")
    _install_plan(engine)
    live = engine.forecast_snapshot()
    built = {"n": 0}
    orig = engine._build_controller_from_config

    def counting(config, **kwargs):
        built["n"] += 1
        return orig(config, **kwargs)

    engine._build_controller_from_config = counting  # type: ignore[method-assign]
    preview = engine.preview_tuning_forecast(
        {"tracking_weight": 4.0},
        {_ROOM: 24.0},
        20.0,
        {_ROOM: 23.5},
        outdoor_forecast=[20.0] * engine._controller.horizon,
        now=_NOW,
    )
    assert built["n"] == 1
    assert "error" not in preview
    assert live["predictions"] == engine.forecast_snapshot()["predictions"]
