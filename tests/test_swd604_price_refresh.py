"""SWD-604: price series after a forecast refresh, and one startup tick.

A forecast that starts tomorrow has no stamp at the current quarter. The
lookup used to fill that gap with the last forecast hour, so the tick and the
solver both started at that unrelated price. A refresh that republished the
forecast and left raw_today out of the payload also dropped today's quarters.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from heatingassistant.app import runtime as runtime_mod
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.controller.facade import HeatingMPCController
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload

_ZONE = timezone(timedelta(hours=1))
_NOW_LOCAL = datetime(2026, 11, 15, 12, 29, 40, tzinfo=_ZONE)
_NOW = _NOW_LOCAL.astimezone(timezone.utc)
_STAMP = _NOW.timestamp()
_QUARTER = 1.399
_AT_1700 = 2.99
_FORECAST_BEFORE = 1.218
_FORECAST_AFTER = 1.08
_AT_2000 = 3.55
_LAST_FORECAST = 0.854
_SENSOR = 1.399


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return _NOW.replace(tzinfo=None)
        return _NOW.astimezone(tz)


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


def _raw_today() -> list[dict]:
    day = datetime(2026, 11, 15, 0, 0, tzinfo=_ZONE)
    rows = []
    for index in range(96):
        stamp = day + timedelta(minutes=15 * index)
        price = 1.1
        if stamp.hour == 12 and stamp.minute == 0:
            price = 1.38
        elif stamp.hour == 12 and stamp.minute == 15:
            price = _QUARTER
        elif stamp.hour == 13 and stamp.minute == 0:
            price = 1.494
        elif stamp.hour == 14 and stamp.minute == 0:
            price = 1.75
        elif stamp.hour == 15 and stamp.minute == 0:
            price = 2.087
        elif stamp.hour == 16 and stamp.minute == 0:
            price = 2.169
        elif stamp.hour == 17 and stamp.minute == 0:
            price = _AT_1700
        rows.append({"hour": stamp.isoformat(), "price": price})
    return rows


def _forecast(midnight: float) -> list[dict]:
    start = datetime(2026, 11, 16, 0, 0, tzinfo=_ZONE)
    rows = []
    for hour in range(152):
        price = 1.2 + (hour % 9) * 0.04
        if hour == 0:
            price = midnight
        elif hour == 20:
            price = _AT_2000
        elif hour == 151:
            price = _LAST_FORECAST
        rows.append(
            {"hour": (start + timedelta(hours=hour)).isoformat(), "price": round(price, 3)}
        )
    return rows


def _weather() -> list[dict]:
    start = datetime(2026, 11, 15, 11, 0, tzinfo=timezone.utc)
    return [
        {
            "datetime": (start + timedelta(hours=hour)).isoformat(),
            "temperature": 6.0 + 0.1 * hour,
            "cloud_coverage": 40.0,
        }
        for hour in range(48)
    ]


def _options(*, horizon_h: float) -> dict:
    return {
        "instance_id": "haos",
        "system_enabled": True,
        "mpc_mode": "nmpc",
        "nmpc_period": 900,
        "nmpc_fast_substeps": 1,
        "nmpc_horizon_h": horizon_h,
        "outdoor_temp_entity": "sensor.outdoor_temperature",
        "weather_entity": "weather.home",
        "price_entity": "sensor.nordpool",
        "rooms": [{"name": "Living Room", "setpoint": 21.0, "temp_tags": ["living_temp"]}],
        "heat_sources": [
            {
                "name": "Living Room Heater",
                "type": "heat_pump",
                "room": "Living Room",
                "max_power": 7000.0,
                "hvac_mode": "heat",
                "output_tag": "hp_heat",
            }
        ],
    }


def _runtime(tmp_path: Path, *, horizon_h: float) -> HeatingRuntime:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options(horizon_h=horizon_h))
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


async def _publish(runtime: HeatingRuntime, tag: str, payload: MqttTagPayload) -> None:
    await runtime._handle_tag_message(
        f"heatingassistant/haos/tag/{tag}/in",
        payload.encode(),
        0,
        False,
        now=_STAMP,
    )


async def _seed(runtime: HeatingRuntime, attrs: dict) -> None:
    await _publish(runtime, "living_temp", MqttTagPayload(20.0, ts=_STAMP))
    await _publish(runtime, "outdoor_temp", MqttTagPayload(4.4, ts=_STAMP))
    await _publish(
        runtime,
        "weather_forecast",
        MqttTagPayload(4.4, ts=_STAMP, attributes={"temperature": 4.4, "forecast": _weather()}),
    )
    await _publish(
        runtime,
        "energy_price",
        MqttTagPayload(_SENSOR, ts=_STAMP, attributes=attrs),
    )
    runtime.actuator_outputs["hp_heat"] = 1.0


async def _tick(runtime: HeatingRuntime, now: float | None = None) -> None:
    await runtime.run_control_tick(now=_STAMP if now is None else now)
    thread = getattr(runtime, "_nmpc_thread", None)
    if thread is not None:
        thread.join(timeout=60)
        assert not thread.is_alive()


def _rows(runtime: HeatingRuntime) -> list[dict]:
    rows = []
    root = runtime.data_dir / "control_log"
    for path in sorted(root.glob("**/*.tick.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _step_at(series: list[float], hour: int, minute: int, *, day: int) -> float:
    for index, price in enumerate(series):
        step = _NOW_LOCAL + timedelta(seconds=900 * index)
        if step.day == day and step.hour == hour and step.minute >= minute:
            return float(price)
    raise AssertionError(f"horizon does not reach {day} {hour:02d}:{minute:02d}")


def _first_tomorrow(series: list[float]) -> float:
    for index, price in enumerate(series):
        step = _NOW_LOCAL + timedelta(seconds=900 * index)
        if step.date() > _NOW_LOCAL.date():
            return float(price)
    raise AssertionError("horizon does not reach 16 Nov")


def test_refresh_keeps_todays_quarters_then_tomorrows_forecast(tmp_path: Path) -> None:
    previous_tz = _use_copenhagen()
    previous_datetime = runtime_mod.datetime
    runtime_mod.datetime = _FrozenDateTime
    runtime = _runtime(tmp_path, horizon_h=12)
    seen: list[dict] = []
    real = _watch(seen)
    raw = _raw_today()
    before = {
        "raw_today": raw,
        "today": [row["price"] for row in raw],
        "forecast": _forecast(_FORECAST_BEFORE),
    }
    try:
        runtime._anchor_schedule_epoch(_STAMP)
        asyncio.run(_seed(runtime, before))
        asyncio.run(_tick(runtime))
        asyncio.run(
            _publish(
                runtime,
                "energy_price",
                MqttTagPayload(
                    _SENSOR,
                    ts=_STAMP + 900,
                    attributes={
                        "raw_today": raw,
                        "today": [row["price"] for row in raw],
                        "forecast": _forecast(_FORECAST_AFTER),
                    },
                ),
            )
        )
        asyncio.run(_tick(runtime, _STAMP + 900))
    finally:
        _restore_solve(real)
        runtime_mod.datetime = previous_datetime
        _restore_tz(previous_tz)

    assert len(seen) >= 2
    for kwargs in seen[-2:]:
        series = list(kwargs["price_forecast"])
        assert series[0] == pytest.approx(_QUARTER)
        assert _step_at(series, 17, 0, day=15) == pytest.approx(_AT_1700)
        assert max(series) >= _AT_1700
        assert _LAST_FORECAST not in {round(value, 3) for value in series[:4]}
    assert _first_tomorrow(list(seen[-2]["price_forecast"])) == pytest.approx(_FORECAST_BEFORE)
    assert _first_tomorrow(list(seen[-1]["price_forecast"])) == pytest.approx(_FORECAST_AFTER)
    rows = _rows(runtime)
    assert rows[-1]["price"] == pytest.approx(_QUARTER)
    assert rows[-2]["price"] == pytest.approx(_QUARTER)


def test_forecast_only_does_not_use_the_last_hour_as_now(tmp_path: Path) -> None:
    previous_tz = _use_copenhagen()
    previous_datetime = runtime_mod.datetime
    runtime_mod.datetime = _FrozenDateTime
    runtime = _runtime(tmp_path, horizon_h=12)
    seen: list[dict] = []
    real = _watch(seen)
    try:
        asyncio.run(
            _seed(
                runtime,
                {"forecast": _forecast(_FORECAST_AFTER)},
            )
        )
        asyncio.run(_tick(runtime))
    finally:
        _restore_solve(real)
        runtime_mod.datetime = previous_datetime
        _restore_tz(previous_tz)

    series = list(seen[-1]["price_forecast"])
    assert series[0] == pytest.approx(_SENSOR)
    assert series[0] != pytest.approx(_LAST_FORECAST)
    assert _first_tomorrow(series) == pytest.approx(_FORECAST_AFTER)
    assert _rows(runtime)[-1]["price"] == pytest.approx(_SENSOR)


def test_refresh_that_omits_raw_today_keeps_the_cached_quarters(tmp_path: Path) -> None:
    previous_tz = _use_copenhagen()
    previous_datetime = runtime_mod.datetime
    runtime_mod.datetime = _FrozenDateTime
    runtime = _runtime(tmp_path, horizon_h=2)
    seen: list[dict] = []
    real = _watch(seen)
    raw = _raw_today()
    try:
        asyncio.run(
            _seed(
                runtime,
                {
                    "raw_today": raw,
                    "today": [row["price"] for row in raw],
                    "forecast": _forecast(_FORECAST_BEFORE),
                },
            )
        )
        asyncio.run(
            _publish(
                runtime,
                "energy_price",
                MqttTagPayload(
                    _SENSOR,
                    ts=_STAMP + 1,
                    attributes={"forecast": _forecast(_FORECAST_AFTER)},
                ),
            )
        )
        asyncio.run(_tick(runtime))
    finally:
        _restore_solve(real)
        runtime_mod.datetime = previous_datetime
        _restore_tz(previous_tz)

    series = list(seen[-1]["price_forecast"])
    assert series[0] == pytest.approx(_QUARTER)
    assert _rows(runtime)[-1]["price"] == pytest.approx(_QUARTER)


class _ConnectOnRegister(InMemoryMqttBus):
    """Fire the connect handler as soon as it is registered, like Paho."""

    def add_connect_handler(self, handler) -> None:
        asyncio.get_running_loop().create_task(handler())


def test_startup_writes_one_tick_row(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=_ConnectOnRegister(),
        options={
            "instance_id": "haos",
            "system_enabled": True,
            "mpc_mode": "linear",
        },
    )
    asyncio.run(runtime.start())
    try:
        rows = _rows(runtime)
    finally:
        asyncio.run(runtime.stop())
    assert len(rows) == 1
