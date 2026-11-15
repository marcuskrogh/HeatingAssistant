"""SWD-601: horizon forecasts, one tick row, and scaled planned power.

The SWD-598 cadence tests replaced the solver, so they never checked that
the series ``compute_actions`` used to pass still arrives. A scalar weather
update used to be followed at once by that call. The 15-minute tick now
runs later, after the live attribute dict has been cleared.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources import HeatPump
from heatingassistant.engine.thermal_model import HouseModel, Room
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload

_NOW = datetime(2026, 11, 15, 5, 30, tzinfo=timezone.utc)


def _forecast() -> list[dict]:
    return [
        {
            "datetime": (_NOW + timedelta(hours=hour)).isoformat(),
            "temperature": 1.0 + 2.0 * hour,
            "cloud_coverage": 10 + 15 * hour,
        }
        for hour in range(8)
    ]


def _options() -> dict:
    return {
        "instance_id": "haos",
        "system_enabled": False,
        "mpc_mode": "nmpc",
        "update_interval": 3600,
        "nmpc_period": 3600,
        "nmpc_horizon_h": 6.0,
        "nmpc_fast_substeps": 1,
        "outdoor_temp_entity": "sensor.outdoor_temperature",
        "weather_entity": "weather.home",
        "price_entity": "sensor.energy_price",
        "solar_radiation_entity": "sensor.solar",
        "rooms": [
            {
                "name": "Living Room",
                "setpoint": 21.0,
                "temp_tags": ["living_temp"],
            }
        ],
        "schedules": {
            "living_room": {
                "enabled": True,
                "periods": [
                    {
                        "name": "night_off",
                        "schedule_type": "weekly_recurring",
                        "time_mode": "window",
                        "start": "22:00",
                        "end": "06:00",
                        "days": [0, 1, 2, 3, 4, 5, 6],
                        "mode": "off",
                        "frost_protection": 10.0,
                        "enabled": True,
                    },
                    {
                        "name": "morning",
                        "schedule_type": "weekly_recurring",
                        "time_mode": "window",
                        "start": "06:00",
                        "end": "12:00",
                        "days": [0, 1, 2, 3, 4, 5, 6],
                        "mode": "comfort",
                        "setpoint": 21.0,
                        "comfort_offset": 0.5,
                        "enabled": True,
                    },
                ],
            }
        },
        "heat_sources": [
            {
                "name": "heater",
                "type": "electric_heater",
                "room": "Living Room",
                "max_power": 1500.0,
                "output_tag": "heater_heat",
            }
        ],
    }


def _runtime(tmp_path: Path) -> HeatingRuntime:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    runtime._schedule_now_local = lambda: _NOW.astimezone()  # type: ignore[method-assign]
    return runtime


def _watch_solve(runtime: HeatingRuntime) -> dict[str, list]:
    controller = runtime.control_engine._controller
    assert controller is not None
    seen: dict[str, list] = {"calls": []}
    real = controller.solve_nmpc

    def _solve(*args, **kwargs):
        seen["calls"].append(dict(kwargs))
        return real(*args, **kwargs)

    controller.solve_nmpc = _solve  # type: ignore[method-assign]
    return seen


async def _tag(
    runtime: HeatingRuntime,
    tag: str,
    value: float,
    now: float,
    attributes: dict | None = None,
) -> None:
    await runtime._handle_tag_message(
        f"heatingassistant/haos/tag/{tag}/in",
        MqttTagPayload(value=value, ts=now, attributes=attributes).encode(),
        qos=0,
        retain=False,
        now=now,
    )


async def _seed(runtime: HeatingRuntime, now: float = 1_000.0) -> None:
    await _tag(runtime, "living_temp", 16.0, now)
    await _tag(runtime, "outdoor_temp", 4.2, now)
    await _tag(
        runtime,
        "weather_forecast",
        4.2,
        now,
        {
            "temperature": 4.2,
            "forecast": _forecast(),
        },
    )
    await _tag(
        runtime,
        "energy_price",
        0.4,
        now,
        {
            "raw_today": [
                {"start": (_NOW + timedelta(hours=hour)).isoformat(), "value": 0.1 * (hour + 1)}
                for hour in range(8)
            ]
        },
    )
    await _tag(
        runtime,
        "solar_radiation",
        50.0,
        now,
        {
            "forecast": {
                (_NOW + timedelta(hours=hour)).isoformat(): 40.0 + 30.0 * hour
                for hour in range(8)
            }
        },
    )


async def _tick(runtime: HeatingRuntime, now: float) -> None:
    await runtime.run_control_tick(now=now)
    thread = getattr(runtime, "_nmpc_thread", None)
    if thread is not None:
        thread.join(timeout=30)
        assert not thread.is_alive()


def _assert_varying_series(values: list, *, not_equal_to: float | None = None) -> None:
    assert len(values) >= 2
    assert len(set(round(float(value), 3) for value in values)) > 1
    if not_equal_to is not None:
        assert any(abs(float(value) - not_equal_to) > 0.2 for value in values)


def test_tick_solves_with_the_forecast_series_not_the_current_value(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    seen = _watch_solve(runtime)

    async def _run() -> None:
        await _seed(runtime)
        await _tag(runtime, "weather_forecast", 4.2, 1_100.0)
        await _tag(runtime, "energy_price", 0.4, 1_100.0)
        await _tag(runtime, "solar_radiation", 50.0, 1_100.0)
        assert "forecast" not in (runtime.tag_attributes.get("weather_forecast") or {})
        await _tick(runtime, 1_000.0)

    asyncio.run(_run())
    assert len(seen["calls"]) == 1
    kwargs = seen["calls"][0]
    horizon = runtime.control_engine._controller.horizon
    assert len(kwargs["outdoor_forecast"]) == horizon
    _assert_varying_series(kwargs["outdoor_forecast"], not_equal_to=4.2)
    assert len(kwargs["price_forecast"]) == horizon
    _assert_varying_series(kwargs["price_forecast"], not_equal_to=0.4)
    assert len(kwargs["cloud_forecast"]) == horizon
    _assert_varying_series(kwargs["cloud_forecast"])
    assert len(kwargs["ghi_forecast"]) == horizon
    _assert_varying_series([value for value in kwargs["ghi_forecast"] if value is not None])
    trajectory = kwargs["control_trajectory"]
    enabled = list(trajectory.enabled_steps["Living Room"])
    assert len(enabled) == horizon
    assert any(enabled) and not all(enabled)
    stored = runtime.control_engine._controller.outdoor_forecast
    assert list(stored) == pytest.approx(kwargs["outdoor_forecast"])


def test_planner_tick_matches_compute_actions_inputs(tmp_path: Path) -> None:
    stamped = _runtime(tmp_path / "compute")
    ticked = _runtime(tmp_path / "tick")
    seen = _watch_solve(ticked)

    async def _run() -> None:
        await _seed(stamped)
        await _seed(ticked)
        outdoor = stamped._outdoor_temperature()
        ctx = stamped._schedule_control_context()
        disturbances = stamped._mpc_disturbance_inputs(outdoor, now=ctx["now_utc"])
        stamped.control_engine.compute_actions(
            stamped.room_temperatures,
            outdoor,
            ctx["setpoints"],
            comfort_offsets=ctx["comfort_offsets"],
            control_trajectory=ctx["trajectory"],
            disabled_sources=ctx["disabled_sources"],
            now=ctx["now_utc"],
            **disturbances,
        )
        await _tick(ticked, 2_000.0)

    asyncio.run(_run())
    stored = stamped.control_engine._nmpc_last_kwargs
    solved = seen["calls"][0]
    for key in (
        "outdoor_forecast",
        "cloud_forecast",
        "price_forecast",
        "ghi_forecast",
    ):
        assert list(solved[key]) == pytest.approx(list(stored[key]))
    assert list(solved["control_trajectory"].setpoints["Living Room"]) == pytest.approx(
        list(stored["control_trajectory"].setpoints["Living Room"])
    )
    assert stamped.control_engine.model.rooms["Living Room"].comfort_offset == pytest.approx(
        ticked.control_engine.model.rooms["Living Room"].comfort_offset
    )


def test_one_sample_writes_one_tick_row_with_estimator_values(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    async def _run() -> None:
        await _seed(runtime)
        await _tick(runtime, 5_000.0)

    asyncio.run(_run())
    rows = []
    for path in (tmp_path / "control_log").rglob("*.tick.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "tick"
    assert row["origin"] == "control_log"
    assert "dt_actual" in row
    room = row["rooms"]["living_room"]
    assert room["x_hat"] is not None
    assert room["y_fused"] == pytest.approx(16.0)


def test_planned_power_uses_scaled_heat_pump_model() -> None:
    room = Room("Living Room", 5.0e6, 0.05, temperature=20.0, setpoint=21.0)
    pump = HeatPump(
        "hp",
        "Living Room",
        max_power=4000.0,
        hvac_mode="heat",
        power_scale=0.15,
    )
    ctrl = HeatingMPCController(
        HouseModel([room]),
        [pump],
        horizon=4,
        dt=900.0,
        mpc_mode="nmpc",
        nmpc_fast_substeps=1,
        nmpc_horizon_h=1.0,
    )
    outdoor = 4.2
    unscaled = ctrl._system.display_heating_powers([1.0], outdoor)["Living Room"]
    scaled = ctrl._system.heating_powers([1.0], outdoor)["Living Room"]
    assert unscaled > 1000.0
    assert scaled == pytest.approx(unscaled * 0.15, rel=0.05)
    ctrl._outdoor_forecast = [outdoor] * ctrl.horizon
    ctrl.set_accepted_path(
        np.ones((ctrl.timing.n_slow, 1)),
        np.full((ctrl.horizon, 1), 21.0),
    )
    assert ctrl.rebuild_forecast_from_plan() is True
    published = float(ctrl.heating_schedule[0]["Living Room"])
    assert published == pytest.approx(scaled, rel=1e-6)
    assert published < unscaled * 0.5
