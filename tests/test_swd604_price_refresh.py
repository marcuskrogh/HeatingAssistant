"""SWD-604: price series after a forecast refresh, tariff-less hold, and one startup tick.

A forecast that starts tomorrow has no stamp at the current quarter. The
lookup used to fill that gap with the last forecast hour, so the tick and the
solver both started at that unrelated price. A refresh that republished the
forecast and left raw_today out of the payload also dropped today's quarters.
"""

from __future__ import annotations

import asyncio
import json
import logging
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



def _options(*, horizon_h: float, **extra: object) -> dict:
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
        **extra,
    }


def _runtime(tmp_path: Path, *, horizon_h: float, **extra: object) -> HeatingRuntime:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options=_options(horizon_h=horizon_h, **extra),
    )
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


_ADDITIONS = {
    "transmissions_nettarif": 0.054,
    "systemtarif": 0.09,
    "elafgift": 0.01,
}
_ADDITION_SUM = 0.154


def _hourly(hour: int) -> float:
    if hour <= 5:
        return 0.133
    if 17 <= hour <= 21:
        return 1.194
    return 0.398


def _spot_value(index: int) -> float:
    return round(0.40 + (index % 8) * 0.02, 5)


def _quarters(day: datetime) -> list[dict]:
    rows = []
    for index in range(96):
        stamp = day + timedelta(minutes=15 * index)
        price = round(_spot_value(index) + _hourly(stamp.hour) + _ADDITION_SUM, 5)
        rows.append({"hour": stamp.isoformat(), "price": price})
    return rows


def _tariff_block() -> dict:
    return {
        "tariffs": {str(hour): _hourly(hour) for hour in range(24)},
        "additional_tariffs": dict(_ADDITIONS),
    }


def _eds(rows: list[dict], **extra: object) -> dict:
    attrs = {
        "raw_today": rows,
        "today_mean": round(sum(row["price"] for row in rows) / len(rows), 3),
        "tariffs": _tariff_block(),
    }
    attrs.update(extra)
    return attrs


def _quarter_price(rows: list[dict], hour: int, minute: int) -> float:
    for row in rows:
        stamp = datetime.fromisoformat(row["hour"])
        if stamp.hour == hour and stamp.minute == minute:
            return float(row["price"])
    raise AssertionError(f"missing {hour:02d}:{minute:02d}")


def _stripped_rows(rows: list[dict]) -> list[dict]:
    stripped = []
    for row in rows:
        stamp = datetime.fromisoformat(row["hour"])
        price = round(float(row["price"]) - _hourly(stamp.hour) - _ADDITION_SUM, 5)
        stripped.append({**row, "price": price})
    return stripped


class _MoveableDateTime(datetime):
    current = _NOW

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.current.replace(tzinfo=None)
        return cls.current.astimezone(tz)


def _freeze(runtime: HeatingRuntime, moment: datetime) -> None:
    _MoveableDateTime.current = moment
    runtime_mod.datetime = _MoveableDateTime
    local = moment.astimezone(_ZONE)
    runtime._schedule_now_local = lambda: local  # type: ignore[method-assign]


def test_valid_day_latches_and_a_tariff_less_update_is_ignored(tmp_path: Path, caplog) -> None:
    previous_tz = _use_copenhagen()
    previous_datetime = runtime_mod.datetime
    runtime = _runtime(tmp_path, horizon_h=2)
    seen: list[dict] = []
    real = _watch(seen)
    today = _quarters(datetime(2026, 11, 15, 0, 0, tzinfo=_ZONE))
    expected = _quarter_price(today, 12, 15)
    stripped = _stripped_rows(today)
    try:
        _freeze(runtime, _NOW)
        with caplog.at_level(logging.WARNING, logger="heatingassistant.app.runtime"):
            asyncio.run(_seed(runtime, _eds(today)))
            asyncio.run(_tick(runtime))
            for _ in range(2):
                asyncio.run(
                    _publish(
                        runtime,
                        "energy_price",
                        MqttTagPayload(
                            _quarter_price(stripped, 12, 15),
                            ts=_STAMP + 1,
                            attributes={
                                "raw_today": stripped,
                                "today_mean": round(sum(row["price"] for row in stripped) / len(stripped), 3),
                                "tariffs": {"tariffs": {}, "additional_tariffs": {}},
                            },
                        ),
                    )
                )
            asyncio.run(_tick(runtime, _STAMP + 1))
    finally:
        _restore_solve(real)
        runtime_mod.datetime = previous_datetime
        _restore_tz(previous_tz)

    assert [list(item["price_forecast"])[0] for item in seen[-2:]] == pytest.approx(
        [expected, expected]
    )
    assert [row["price"] for row in _rows(runtime)[-2:]] == pytest.approx([expected, expected])
    assert runtime.status()["control"]["price_latch_ignored"] == 2
    assert caplog.text.count("failed validation") == 1
    assert "2026-11-15" in runtime._price_latch.days


def test_next_valid_day_is_latched_and_an_invalid_one_is_not(tmp_path: Path) -> None:
    previous_tz = _use_copenhagen()
    previous_datetime = runtime_mod.datetime
    runtime = _runtime(tmp_path, horizon_h=2)
    seen: list[dict] = []
    real = _watch(seen)
    today = _quarters(datetime(2026, 11, 15, 0, 0, tzinfo=_ZONE))
    tomorrow = _quarters(datetime(2026, 11, 16, 0, 0, tzinfo=_ZONE))
    bad_tomorrow = _stripped_rows(tomorrow)
    later = datetime(2026, 11, 16, 0, 15, tzinfo=_ZONE)
    try:
        _freeze(runtime, _NOW)
        asyncio.run(_seed(runtime, _eds(today)))
        asyncio.run(
            _publish(
                runtime,
                "energy_price",
                MqttTagPayload(
                    0.2,
                    ts=_STAMP + 1,
                    attributes=_eds(today, raw_tomorrow=bad_tomorrow, tomorrow_valid=True),
                ),
            )
        )
        ignored = runtime.status()["control"]["price_latch_ignored"]
        asyncio.run(
            _publish(
                runtime,
                "energy_price",
                MqttTagPayload(
                    0.2,
                    ts=_STAMP + 2,
                    attributes=_eds(
                        today,
                        raw_tomorrow=tomorrow,
                        tomorrow_valid=True,
                        forecast=[
                            {
                                "hour": datetime(2026, 11, 17, 0, 0, tzinfo=_ZONE).isoformat(),
                                "price": 1.25,
                            }
                        ],
                    ),
                ),
            )
        )
        _freeze(runtime, later.astimezone(timezone.utc))
        asyncio.run(_tick(runtime, later.timestamp()))
    finally:
        _restore_solve(real)
        runtime_mod.datetime = previous_datetime
        _restore_tz(previous_tz)

    assert ignored == 1
    assert "2026-11-16" in runtime._price_latch.days
    assert "2026-11-17" not in runtime._price_latch.days
    assert runtime._price_latch.forecast[0]["price"] == pytest.approx(1.25)
    assert list(seen[-1]["price_forecast"])[0] == pytest.approx(_quarter_price(tomorrow, 0, 15))
    assert _rows(runtime)[-1]["price"] == pytest.approx(_quarter_price(tomorrow, 0, 15))


def test_cold_start_accepts_only_a_valid_load(tmp_path: Path, caplog) -> None:
    previous_tz = _use_copenhagen()
    previous_datetime = runtime_mod.datetime
    runtime = _runtime(tmp_path, horizon_h=2)
    seen: list[dict] = []
    real = _watch(seen)
    today = _quarters(datetime(2026, 11, 15, 0, 0, tzinfo=_ZONE))
    stripped = _stripped_rows(today)
    try:
        _freeze(runtime, _NOW)
        with caplog.at_level(logging.WARNING, logger="heatingassistant.app.runtime"):
            asyncio.run(
                _seed(
                    runtime,
                    {
                        "raw_today": stripped,
                        "tariffs": {"tariffs": {}, "additional_tariffs": {}},
                    },
                )
            )
            asyncio.run(_tick(runtime))
            awaiting = runtime.status()["control"]["price_latch_awaiting_valid"]
            asyncio.run(
                _publish(
                    runtime,
                    "energy_price",
                    MqttTagPayload(0.01, ts=_STAMP + 1, attributes=_eds(today)),
                )
            )
            # The first solve installed a heating plan, so the next tick is not
            # due until that plan is cleared. Drop it so the valid load is solved.
            runtime.control_engine._controller._nmpc_U = None
            asyncio.run(_tick(runtime, _STAMP + 1))
    finally:
        _restore_solve(real)
        runtime_mod.datetime = previous_datetime
        _restore_tz(previous_tz)

    assert seen[-2]["price_forecast"] is None
    assert _rows(runtime)[-2]["price"] is None
    assert awaiting is True
    assert "without an electricity price" in caplog.text
    assert list(seen[-1]["price_forecast"])[0] == pytest.approx(_quarter_price(today, 12, 15))
    assert _rows(runtime)[-1]["price"] == pytest.approx(_quarter_price(today, 12, 15))
    assert runtime.status()["control"]["price_latch_awaiting_valid"] is False


def test_latched_prices_survive_an_app_restart(tmp_path: Path) -> None:
    previous_tz = _use_copenhagen()
    previous_datetime = runtime_mod.datetime
    runtime = _runtime(tmp_path, horizon_h=2)
    real = _watch([])
    today = _quarters(datetime(2026, 11, 15, 0, 0, tzinfo=_ZONE))
    expected = _quarter_price(today, 12, 15)
    try:
        _freeze(runtime, _NOW)
        asyncio.run(_seed(runtime, _eds(today)))
        restarted = _runtime(tmp_path, horizon_h=2)
        seen: list[dict] = []
        HeatingMPCController.solve_nmpc = real  # type: ignore[method-assign]
        real = _watch(seen)
        _freeze(restarted, _NOW)

        async def _measurements() -> None:
            await _publish(restarted, "living_temp", MqttTagPayload(20.0, ts=_STAMP))
            await _publish(restarted, "outdoor_temp", MqttTagPayload(4.4, ts=_STAMP))
            await _publish(
                restarted,
                "weather_forecast",
                MqttTagPayload(4.4, ts=_STAMP, attributes={"temperature": 4.4, "forecast": _weather()}),
            )
            restarted.actuator_outputs["hp_heat"] = 1.0

        asyncio.run(_measurements())
        asyncio.run(_tick(restarted))
    finally:
        _restore_solve(real)
        runtime_mod.datetime = previous_datetime
        _restore_tz(previous_tz)

    assert list(seen[-1]["price_forecast"])[0] == pytest.approx(expected)
    assert _rows(restarted)[-1]["price"] == pytest.approx(expected)


def test_live_price_is_used_when_the_latched_series_does_not_cover_now(tmp_path: Path, caplog) -> None:
    previous_tz = _use_copenhagen()
    previous_datetime = runtime_mod.datetime
    runtime = _runtime(tmp_path, horizon_h=2)
    seen: list[dict] = []
    real = _watch(seen)
    yesterday = _quarters(datetime(2026, 11, 14, 0, 0, tzinfo=_ZONE))
    try:
        _freeze(runtime, _NOW)
        with caplog.at_level(logging.WARNING, logger="heatingassistant.app.runtime"):
            asyncio.run(_seed(runtime, _eds(yesterday)))
            asyncio.run(
                _publish(
                    runtime,
                    "energy_price",
                    MqttTagPayload(2.5, ts=_STAMP + 1, attributes={}),
                )
            )
            asyncio.run(_tick(runtime))
    finally:
        _restore_solve(real)
        runtime_mod.datetime = previous_datetime
        _restore_tz(previous_tz)

    assert list(seen[-1]["price_forecast"])[0] == pytest.approx(2.5)
    assert _rows(runtime)[-1]["price"] == pytest.approx(2.5)
    assert runtime.status()["control"]["price_latch_live_fallback"] is True
    assert "does not cover now" in caplog.text


