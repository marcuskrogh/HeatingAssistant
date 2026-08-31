"""SWD-421: room-view plots stay on the 2-hour NMPC plan after 15-minute ticks."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from heatingassistant.app.forecast_payload import build_app_forecast_payload
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources import HeatPump
from heatingassistant.engine.thermal_model import HouseModel, Room

_NOW = datetime(2026, 8, 20, 22, 0, tzinfo=timezone.utc)
_ROOM = "living_room"
_ENGINE_ROOM = "Living Room"


def _ctrl() -> HeatingMPCController:
    room = Room(
        _ROOM, 5e6, 0.05, temperature=22.0, setpoint=23.5, comfort_offset=2.0
    )
    hp = HeatPump("hp", _ROOM, max_power=7000.0, hvac_mode="heat_cool")
    return HeatingMPCController(
        HouseModel([room]),
        [hp],
        nmpc_period=7200.0,
        nmpc_fast_substeps=4,
        nmpc_horizon_h=4.0,
    )


def _varying_outdoor(n: int) -> list[float]:
    # Large within-block swings so COP would invent short watt steps if
    # display power used outdoor_seq[k] instead of the slow-interval sample.
    pattern = [0.0, 25.0, -8.0, 18.0]
    return [pattern[i % len(pattern)] for i in range(n)]


def _watts(schedule: list[dict[str, float]], room: str) -> list[float]:
    return [float(step[room]) for step in schedule]


def _temps(predictions: list[dict[str, float]], room: str) -> list[float]:
    return [float(step[room]) for step in predictions]


def _assert_slow_holds(values: list[float], m: int, *, atol: float) -> None:
    assert len(values) >= 2 * m
    for start in range(0, len(values), m):
        block = np.asarray(values[start : start + m], dtype=float)
        assert float(np.max(block) - np.min(block)) <= atol
    assert abs(values[0] - values[m]) > atol * 4.0


def test_timing_uses_fast_grid_samples() -> None:
    ctrl = _ctrl()
    assert ctrl._dt == pytest.approx(1800.0)
    assert ctrl._timing.m == 4
    assert ctrl._timing.n_slow == 2
    assert ctrl.horizon == 8


def test_rebuild_and_compute_keep_slow_power_holds() -> None:
    ctrl = _ctrl()
    m = int(ctrl._timing.m)
    n_fast = int(ctrl.horizon)
    outdoor = _varying_outdoor(n_fast)
    u_star = np.array([[0.8], [0.25]], dtype=float)
    t_ref = np.linspace(21.0, 24.5, n_fast).reshape(-1, 1)

    ctrl._outdoor_forecast = list(outdoor)
    ctrl.set_accepted_path(u_star, t_ref)
    assert ctrl.rebuild_forecast_from_plan() is True

    plan_watts = _watts(ctrl.heating_schedule, _ROOM)
    plan_temps = _temps(ctrl.predictions, _ROOM)
    assert plan_temps == pytest.approx(t_ref.ravel().tolist(), abs=1e-12)
    # T_ref is the fast-grid OCP path, not a 2 h constant like U*.
    assert float(np.max(plan_temps[:m]) - np.min(plan_temps[:m])) > 0.2
    _assert_slow_holds(plan_watts, m, atol=1.0)

    U_fast = np.repeat(u_star, m, axis=0)
    naive = [
        float(
            ctrl._system.display_heating_powers(U_fast[k], outdoor[k])[_ROOM]
        )
        for k in range(m)
    ]
    assert max(naive) - min(naive) > 50.0

    ctrl.compute(
        outdoor[0],
        solar_gains={_ROOM: 0.0},
        now=_NOW,
        outdoor_forecast=outdoor,
    )
    after_watts = _watts(ctrl.heating_schedule, _ROOM)
    after_temps = _temps(ctrl.predictions, _ROOM)
    _assert_slow_holds(after_watts, m, atol=1.0)
    assert after_watts == pytest.approx(plan_watts, abs=1.0)
    assert after_temps == pytest.approx(t_ref.ravel().tolist(), abs=1e-12)
    assert ctrl.rebuild_forecast_from_plan() is True
    remaining_t = ctrl._forecast_T(n_fast)
    assert remaining_t is not None
    assert _temps(ctrl.predictions, _ROOM) == pytest.approx(
        remaining_t[:, 0].tolist(), abs=1e-12
    )
    assert float(np.max(remaining_t[:m, 0]) - np.min(remaining_t[:m, 0])) > 0.2


def test_room_snapshot_after_compute_keeps_slow_power_holds() -> None:
    engine = ControlEngine(
        {
            "nmpc_period": 7200.0,
            "nmpc_fast_substeps": 4,
            "nmpc_horizon_h": 4.0,
            "rooms": [
                {
                    "name": _ENGINE_ROOM,
                    "setpoint": 23.5,
                    "comfort_offset": 2.0,
                    "temperature": 22.0,
                }
            ],
            "heat_sources": [
                {
                    "name": "hp",
                    "type": "heat_pump",
                    "room": _ENGINE_ROOM,
                    "max_power": 7000.0,
                    "hvac_mode": "heat_cool",
                }
            ],
        }
    )
    ctrl = engine._controller
    assert ctrl is not None
    m = int(ctrl._timing.m)
    n_fast = int(ctrl.horizon)
    outdoor = _varying_outdoor(n_fast)
    t_ref = np.linspace(22.0, 24.0, n_fast).reshape(-1, 1)
    ctrl._outdoor_forecast = list(outdoor)
    ctrl.set_accepted_path(np.array([[0.7], [0.15]], dtype=float), t_ref)
    assert ctrl.rebuild_forecast_from_plan() is True
    preview_watts = _watts(ctrl.heating_schedule, _ENGINE_ROOM)

    engine.compute_actions(
        {_ENGINE_ROOM: 22.0},
        outdoor[0],
        {_ENGINE_ROOM: 23.5},
        now=_NOW,
        outdoor_forecast=outdoor,
    )
    snap = engine.forecast_snapshot()
    watts = _watts(snap["heating_schedule"], _ENGINE_ROOM)
    temps = _temps(snap["predictions"], _ENGINE_ROOM)
    assert watts == pytest.approx(preview_watts, abs=1.0)
    _assert_slow_holds(watts, m, atol=1.0)
    assert temps == pytest.approx(t_ref.ravel().tolist(), abs=1e-12)
    assert snap["dt"] == pytest.approx(ctrl._dt)

    payload = build_app_forecast_payload(
        rooms=[{"name": _ENGINE_ROOM, "setpoint": 23.5, "comfort_offset": 2.0}],
        room_temperatures={_ENGINE_ROOM: 22.0},
        outdoor_temp=outdoor[0],
        energy_price=None,
        snapshot=snap,
        now=_NOW,
    )
    assert payload["step_seconds"] == pytest.approx(ctrl._dt)
    room = payload["rooms"]["living_room"]
    plotted = [float(step["heating_power"]) for step in room["forecast"][1:]]
    _assert_slow_holds(plotted[: n_fast], m, atol=1.0)
