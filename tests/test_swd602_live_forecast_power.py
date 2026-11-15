"""SWD-602: live-shaped forecasts and scaled power through the real tick.

2026.11.5 kept a list-shaped forecast on the direct builder, then the tick
could still hand the solver a flat outdoor and price series. A map-shaped
forecast was ignored, and a price timestamp without a timezone aborted the
whole disturbance build. A fresh controller also logged 0 W while the
restored actuator command was 1, and a fitted scale stored under another
source name was left at 1 so the plan showed rated watts.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.controller.facade import HeatingMPCController
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload

_ZONE = timezone(timedelta(hours=1))
_NOW_LOCAL = datetime(2026, 11, 15, 8, 33, tzinfo=_ZONE)
_NOW = _NOW_LOCAL.astimezone(timezone.utc)
_STAMP = _NOW.timestamp()


def _weather_rows() -> list[dict]:
    start = datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc)
    return [
        {
            "datetime": (start + timedelta(hours=hour)).isoformat(),
            "temperature": 5.2 + 0.3 * hour,
            "cloud_coverage": 40 + (hour % 5),
            "condition": "cloudy",
        }
        for hour in range(48)
    ]


def _price_rows(*, naive: bool) -> list[dict]:
    day = datetime(2026, 11, 15, 0, 0, tzinfo=None if naive else _ZONE)
    rows = []
    for index in range(96):
        start = day + timedelta(minutes=15 * index)
        rows.append(
            {
                "start": start.isoformat(),
                "value": 0.8 + (index % 17) * 0.07,
            }
        )
    return rows


def _options() -> dict:
    return {
        "instance_id": "haos",
        "system_enabled": True,
        "mpc_mode": "nmpc",
        "nmpc_period": 900,
        "nmpc_fast_substeps": 1,
        "nmpc_horizon_h": 2,
        "outdoor_temp_entity": "sensor.outdoor_temperature",
        "weather_entity": "weather.home",
        "price_entity": "sensor.nordpool",
        "rooms": [
            {
                "name": "Living Room",
                "setpoint": 21.0,
                "temp_tags": ["living_temp"],
            }
        ],
        "heat_sources": [
            {
                "name": "Vaillant",
                "type": "heat_pump",
                "room": "Living Room",
                "max_power": 7000.0,
                "hvac_mode": "heat",
                "output_tag": "hp_heat",
                "power_scale": 1.0,
            }
        ],
        "estimated_params": {
            "active": {
                "rooms": {"Living Room": {"internal_gain": 1550.0}},
                "sources": {"Heat pump": {"power_scale": 0.15}},
            }
        },
    }


def _runtime(tmp_path: Path) -> HeatingRuntime:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    runtime._schedule_now_local = lambda: _NOW_LOCAL  # type: ignore[method-assign]
    return runtime


def _watch(seen: list[dict]):
    real = HeatingMPCController.solve_nmpc

    def _solve(self, *args, **kwargs):
        seen.append(dict(kwargs))
        return real(self, *args, **kwargs)

    HeatingMPCController.solve_nmpc = _solve  # type: ignore[method-assign]
    return real


def _restore_solve(real) -> None:
    HeatingMPCController.solve_nmpc = real  # type: ignore[method-assign]


def _seed(runtime: HeatingRuntime, *, weather_forecast, price_rows: list[dict]) -> None:
    runtime.update_tag("living_temp", MqttTagPayload(16.0, ts=_STAMP))
    runtime.update_tag("outdoor_temp", MqttTagPayload(4.4, ts=_STAMP))
    runtime.update_tag(
        "weather_forecast",
        MqttTagPayload(
            4.4,
            ts=_STAMP,
            attributes={"temperature": 4.4, "forecast": weather_forecast},
        ),
    )
    runtime.update_tag(
        "energy_price",
        MqttTagPayload(
            1.564,
            ts=_STAMP,
            attributes={
                "raw_today": price_rows,
                "today": [row["value"] for row in price_rows],
                "forecast": price_rows,
            },
        ),
    )
    runtime.actuator_outputs["hp_heat"] = 1.0


async def _tick(runtime: HeatingRuntime) -> None:
    await runtime.run_control_tick(now=_STAMP)
    thread = getattr(runtime, "_nmpc_thread", None)
    if thread is not None:
        thread.join(timeout=60)
        assert not thread.is_alive()


def _uniq(values) -> int:
    return len({round(float(value), 3) for value in values})


def _assert_varying_kwargs(kwargs: dict, horizon: int) -> None:
    outdoor = list(kwargs["outdoor_forecast"])
    price = list(kwargs["price_forecast"])
    assert len(outdoor) == horizon
    assert len(price) == horizon
    assert _uniq(outdoor) > 1
    assert any(abs(float(value) - 4.4) > 0.2 for value in outdoor)
    assert _uniq(price) > 1
    assert any(abs(float(value) - 1.564) > 0.05 for value in price)


def _tick_rows(runtime: HeatingRuntime) -> list[dict]:
    rows = []
    root = runtime.data_dir / "control_log"
    for path in sorted(root.glob("**/*.tick.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _assert_scaled_tick(runtime: HeatingRuntime, row: dict, *, full_command: bool) -> None:
    room = row["rooms"]["living_room"]
    source = runtime.control_engine.heat_sources[0]
    applied = float(row["sources"]["vaillant"]["u_applied"])
    if full_command:
        assert applied == pytest.approx(1.0)
    model = source.smooth_thermal_power(applied, 4.4)
    display = source.display_smooth_thermal_power(applied, 4.4)
    assert room["q_model_W"] == pytest.approx(model, rel=0.05)
    assert room["q_model_display_W"] == pytest.approx(display, rel=0.05)
    if applied > 0.2:
        assert display > 1000
        assert room["q_model_W"] < room["q_model_display_W"] * 0.5


def _assert_scaled_api(runtime: HeatingRuntime) -> None:
    payload = runtime.forecasts()
    outdoor = [entry["outdoor_temp"] for entry in payload["outdoor_forecast"]]
    price = [entry["price"] for entry in payload["price_forecast"]]
    assert _uniq(outdoor) > 1
    assert _uniq(price) > 1
    powers = [
        entry.get("heating_power")
        for entry in payload["rooms"]["living_room"]["forecast"]
        if entry.get("heating_power") is not None
    ]
    assert powers
    source = runtime.control_engine.heat_sources[0]
    display = source.display_smooth_thermal_power(1.0, 4.4)
    assert max(powers) < display * 0.5
    assert max(powers) > 0.0


def test_live_shaped_tick_keeps_forecasts_and_scaled_power(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    seen: list[dict] = []
    real = _watch(seen)
    try:
        _seed(runtime, weather_forecast=_weather_rows(), price_rows=_price_rows(naive=False))

        async def _run() -> None:
            await _tick(runtime)
            await runtime.update_config({"comfort_offset": 1.0}, now=_STAMP + 900)
            thread = getattr(runtime, "_nmpc_thread", None)
            if thread is not None:
                thread.join(timeout=60)

        asyncio.run(_run())
    finally:
        _restore_solve(real)

    horizon = runtime.control_engine._controller.horizon
    assert len(seen) >= 2
    for kwargs in seen:
        _assert_varying_kwargs(kwargs, horizon)
    rows = _tick_rows(runtime)
    assert len(rows) >= 2
    _assert_scaled_tick(runtime, rows[0], full_command=True)
    _assert_scaled_tick(runtime, rows[-1], full_command=False)
    _assert_scaled_api(runtime)


def test_map_forecast_and_naive_price_timestamps_reach_the_solver(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    seen: list[dict] = []
    real = _watch(seen)
    weather = {
        row["datetime"]: {
            "temperature": row["temperature"],
            "cloud_coverage": row["cloud_coverage"],
        }
        for row in _weather_rows()
    }
    try:
        _seed(runtime, weather_forecast=weather, price_rows=_price_rows(naive=True))

        async def _run() -> None:
            await _tick(runtime)

        asyncio.run(_run())
    finally:
        _restore_solve(real)

    horizon = runtime.control_engine._controller.horizon
    assert len(seen) == 1
    _assert_varying_kwargs(seen[0], horizon)
    _assert_scaled_api(runtime)
    rows = _tick_rows(runtime)
    assert len(rows) == 1
    _assert_scaled_tick(runtime, rows[0], full_command=True)
