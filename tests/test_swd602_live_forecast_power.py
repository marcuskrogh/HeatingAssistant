"""SWD-602: live forecast attributes through the real tick and the real solver.

The weather and price attributes confirmed on /api/state already parse. The
flat series was produced later: solve_nmpc stores the series, then blocks in
the NLP, and a room-temperature reading calls compute(run_optimization=False)
without that series. That call replaced the outdoor forecast with the current
sensor and cleared the price forecast. The accepted snapshot copied the
replacement, so /api/forecasts was flat while solar, rebuilt geometrically,
still varied.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
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
_SENSOR_OUTDOOR = 4.4
_SENSOR_PRICE = 1.564
_FIRST_FORECAST_C = 6.1
_RAW_TODAY_PRICE = 1.604  # 08:30 local quarter of the raw_today series below
_TOMORROW_PRICE = 1.218


def _use_copenhagen() -> str | None:
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Copenhagen"
    time.tzset()
    return previous


def _restore_tz(previous: str | None) -> None:
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time.tzset()


def _weather_rows() -> list[dict]:
    """48 hourly rows. The first hour is 08:00 UTC, which is 09:00 local."""

    start = datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc)
    return [
        {
            "cloud_coverage": 79.0 if hour == 0 else 40.0 + (hour % 7),
            "condition": "partlycloudy",
            "datetime": (start + timedelta(hours=hour)).isoformat(),
            "temperature": _FIRST_FORECAST_C + 0.1 * hour,
            "precipitation": 0.0,
            "wind_speed": 3.0,
        }
        for hour in range(48)
    ]


def _raw_today() -> list[dict]:
    day = datetime(2026, 11, 15, 0, 0, tzinfo=_ZONE)
    return [
        {
            "start": (day + timedelta(minutes=15 * index)).isoformat(),
            "value": round(1.844 - (index % 13) * 0.03, 3),
        }
        for index in range(96)
    ]


def _tomorrow_forecast() -> list[dict]:
    """152 hourly prices that start at local midnight tomorrow. No raw_tomorrow."""

    start = datetime(2026, 11, 16, 0, 0, tzinfo=_ZONE)
    return [
        {
            "start": (start + timedelta(hours=index)).isoformat(),
            "value": round(_TOMORROW_PRICE + (index % 11) * 0.05, 3),
        }
        for index in range(152)
    ]


def _price_rows(*, naive: bool) -> list[dict]:
    day = datetime(2026, 11, 15, 0, 0, tzinfo=None if naive else _ZONE)
    return [
        {
            "start": (day + timedelta(minutes=15 * index)).isoformat(),
            "value": round(0.8 + (index % 17) * 0.07, 3),
        }
        for index in range(96)
    ]


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
                "name": "Living Room Heater",
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
            },
            "sources": {"Heat pump": {"power_scale": 0.15}},
        },
    }


def _runtime(tmp_path: Path) -> HeatingRuntime:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    runtime._schedule_now_local = lambda: _NOW_LOCAL  # type: ignore[method-assign]
    return runtime


def _watch(seen: list[dict], runtime: HeatingRuntime | None = None):
    """Record solver kwargs. Optionally replay a room-temperature estimate.

    That estimate is what a living_temp message does while the NLP is still
    running: compute with no staged forecast. It used to flatten the series
    the solver had just stored, before the snapshot was copied.
    """

    real = HeatingMPCController.solve_nmpc

    def _solve(self, *args, **kwargs):
        seen.append(dict(kwargs))
        result = real(self, *args, **kwargs)
        if runtime is not None:
            runtime._update_estimate(_STAMP)
        return result

    HeatingMPCController.solve_nmpc = _solve  # type: ignore[method-assign]
    return real


def _restore_solve(real) -> None:
    HeatingMPCController.solve_nmpc = real  # type: ignore[method-assign]


async def _publish(runtime: HeatingRuntime, tag: str, payload: MqttTagPayload) -> None:
    await runtime._handle_tag_message(
        f"heatingassistant/haos/tag/{tag}/in",
        payload.encode(),
        0,
        False,
        now=_STAMP,
    )


async def _seed_live(runtime: HeatingRuntime) -> None:
    raw_today = _raw_today()
    await _publish(runtime, "living_temp", MqttTagPayload(16.0, ts=_STAMP))
    await _publish(runtime, "outdoor_temp", MqttTagPayload(_SENSOR_OUTDOOR, ts=_STAMP))
    await _publish(
        runtime,
        "weather_forecast",
        MqttTagPayload(
            _SENSOR_OUTDOOR,
            ts=_STAMP,
            attributes={
                "condition": "partlycloudy",
                "temperature": _SENSOR_OUTDOOR,
                "forecast": _weather_rows(),
            },
        ),
    )
    await _publish(
        runtime,
        "energy_price",
        MqttTagPayload(
            _SENSOR_PRICE,
            ts=_STAMP,
            attributes={
                "forecast": _tomorrow_forecast(),
                "raw_today": raw_today,
                "today": [row["value"] for row in raw_today],
            },
        ),
    )
    runtime.actuator_outputs["hp_heat"] = 1.0


def _seed_rows(
    runtime: HeatingRuntime, *, weather_forecast, price_rows: list[dict]
) -> None:
    runtime.update_tag("living_temp", MqttTagPayload(16.0, ts=_STAMP))
    runtime.update_tag("outdoor_temp", MqttTagPayload(_SENSOR_OUTDOOR, ts=_STAMP))
    runtime.update_tag(
        "weather_forecast",
        MqttTagPayload(
            _SENSOR_OUTDOOR,
            ts=_STAMP,
            attributes={"temperature": _SENSOR_OUTDOOR, "forecast": weather_forecast},
        ),
    )
    runtime.update_tag(
        "energy_price",
        MqttTagPayload(
            _SENSOR_PRICE,
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
    applied = float(row["sources"]["living_room_heater"]["u_applied"])
    if full_command:
        assert applied == pytest.approx(1.0)
    model = source.smooth_thermal_power(applied, _SENSOR_OUTDOOR)
    display = source.display_smooth_thermal_power(applied, _SENSOR_OUTDOOR)
    assert room["q_model_W"] == pytest.approx(model, rel=0.05)
    assert room["q_model_display_W"] == pytest.approx(display, rel=0.05)
    if applied > 0.2:
        assert display > 1000
        assert room["q_model_W"] == pytest.approx(display * 0.15, rel=0.05)


def _tomorrow_step(series: list[float]) -> float:
    for index, _price in enumerate(series):
        step = _NOW_LOCAL + timedelta(seconds=900 * index)
        if step.date() > _NOW_LOCAL.date():
            return float(series[index])
    raise AssertionError("horizon does not reach tomorrow")


def test_live_shapes_survive_a_room_temperature_update(tmp_path: Path) -> None:
    previous_tz = _use_copenhagen()
    runtime = _runtime(tmp_path)
    seen: list[dict] = []
    real = _watch(seen, runtime)
    try:
        asyncio.run(_seed_live(runtime))
        stored = runtime.tag_attributes["weather_forecast"]["forecast"]
        assert isinstance(stored, list) and len(stored) == 48
        assert stored[0]["datetime"].endswith("+00:00")
        price_attrs = runtime.tag_attributes["energy_price"]
        assert price_attrs["forecast"][0]["start"].startswith("2026-11-16T00:00:00")
        assert "raw_tomorrow" not in price_attrs
        assert runtime.control_engine.heat_sources[0].name == "Living Room Heater"
        assert runtime.control_engine.heat_sources[0].power_scale == pytest.approx(0.15)

        asyncio.run(_tick(runtime))
    finally:
        _restore_solve(real)

    try:
        horizon = runtime.control_engine._controller.horizon
        assert len(seen) == 1
        outdoor = list(seen[0]["outdoor_forecast"])
        price = list(seen[0]["price_forecast"])
        assert len(outdoor) == horizon
        assert outdoor[0] == pytest.approx(_FIRST_FORECAST_C, abs=0.05)
        assert _uniq(outdoor) > 1
        assert all(abs(float(value) - _SENSOR_OUTDOOR) > 0.5 for value in outdoor)
        assert price[0] == pytest.approx(_RAW_TODAY_PRICE, abs=0.02)
        assert _uniq(price) > 1
        assert not all(abs(float(value) - _SENSOR_PRICE) < 0.01 for value in price)

        full = runtime._mpc_disturbance_inputs(
            _SENSOR_OUTDOOR, horizon=145, dt_s=900.0, now=_NOW
        )
        assert len(full["outdoor_forecast"]) == 145
        assert full["outdoor_forecast"][0] == pytest.approx(_FIRST_FORECAST_C, abs=0.05)
        assert _uniq(full["outdoor_forecast"]) > 10
        assert full["price_forecast"][0] == pytest.approx(_RAW_TODAY_PRICE, abs=0.02)
        assert _tomorrow_step(full["price_forecast"]) == pytest.approx(_TOMORROW_PRICE, abs=0.02)

        payload = runtime.forecasts()
        api_outdoor = [entry["outdoor_temp"] for entry in payload["outdoor_forecast"]]
        api_price = [entry["price"] for entry in payload["price_forecast"]]
        assert api_outdoor[0] == pytest.approx(_SENSOR_OUTDOOR, abs=0.05)
        assert api_outdoor[1] == pytest.approx(_FIRST_FORECAST_C, abs=0.05)
        assert _uniq(api_outdoor[1:]) > 1
        assert api_price[0] == pytest.approx(_RAW_TODAY_PRICE, abs=0.02)
        assert _uniq(api_price) > 1
        assert not all(abs(float(value) - _SENSOR_PRICE) < 0.01 for value in api_price)

        source = runtime.control_engine.heat_sources[0]
        display = source.display_smooth_thermal_power(1.0, _SENSOR_OUTDOOR)
        powers = [
            entry.get("heating_power")
            for entry in payload["rooms"]["living_room"]["forecast"]
            if entry.get("heating_power") is not None
        ]
        assert powers
        assert max(powers) == pytest.approx(display * 0.15, rel=0.08)
        rows = _tick_rows(runtime)
        assert len(rows) == 1
        _assert_scaled_tick(runtime, rows[0], full_command=True)
    finally:
        _restore_tz(previous_tz)


def test_map_forecast_and_naive_price_timestamps_reach_the_solver(tmp_path: Path) -> None:
    previous_tz = _use_copenhagen()
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
        _seed_rows(runtime, weather_forecast=weather, price_rows=_price_rows(naive=True))
        asyncio.run(_tick(runtime))
    finally:
        _restore_solve(real)
        _restore_tz(previous_tz)

    horizon = runtime.control_engine._controller.horizon
    assert len(seen) == 1
    outdoor = list(seen[0]["outdoor_forecast"])
    price = list(seen[0]["price_forecast"])
    assert len(outdoor) == horizon
    assert _uniq(outdoor) > 1
    assert all(abs(float(value) - _SENSOR_OUTDOOR) > 0.5 for value in outdoor)
    assert _uniq(price) > 1
    payload = runtime.forecasts()
    api_outdoor = [entry["outdoor_temp"] for entry in payload["outdoor_forecast"]]
    assert _uniq(api_outdoor[1:]) > 1
    rows = _tick_rows(runtime)
    assert len(rows) == 1
    _assert_scaled_tick(runtime, rows[0], full_command=True)
