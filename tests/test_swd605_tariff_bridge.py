"""SWD-605: tariffs must survive the bridge and reach the day latch.

The 2026.11.8 tests handed tariffs to the App directly. These tests run a
realistic Energi Data Service attribute set through forecast_publish, then
through a real control tick and the real solver.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_helpers = _load("swd604_price_helpers", _ROOT / "tests" / "test_swd604_price_refresh.py")
_bridge = _load(
    "ha_forecast_publish_swd605",
    _ROOT / "custom_components" / "heating_assistant" / "forecast_publish.py",
)


def _full_attributes() -> dict:
    today = _helpers._quarters(datetime(2026, 11, 15, 0, 0, tzinfo=_helpers._ZONE))
    tomorrow = _helpers._quarters(datetime(2026, 11, 16, 0, 0, tzinfo=_helpers._ZONE))
    assert len(today) == 96
    assert len(tomorrow) == 96
    return _helpers._eds(today, raw_tomorrow=tomorrow, tomorrow_valid=True)


async def _through_bridge(attrs: dict) -> dict:
    state = SimpleNamespace(
        domain="sensor",
        entity_id="sensor.energi_data_service",
        state="0.972",
        attributes=attrs,
    )
    published = await _bridge.forecast_attributes_for_publish(
        SimpleNamespace(services=None),
        state,
    )
    assert isinstance(published, dict)
    return published


async def _publish_bridged(runtime, attrs: dict, *, ts: float) -> dict:
    published = await _through_bridge(attrs)
    await _helpers._publish(
        runtime,
        "energy_price",
        _helpers.MqttTagPayload(0.972, ts=ts, attributes=published),
    )
    return published


def _solve_again(runtime) -> None:
    controller = runtime.control_engine._controller
    if controller is not None:
        controller._nmpc_U = None


def test_bridge_latches_tariffs_and_rejects_the_same_load_without_them(tmp_path, caplog) -> None:
    previous_tz = _helpers._use_copenhagen()
    previous_datetime = _helpers.runtime_mod.datetime
    runtime = _helpers._runtime(tmp_path, horizon_h=2)
    seen: list[dict] = []
    real = _helpers._watch(seen)
    full = _full_attributes()
    expected = _helpers._quarter_price(full["raw_today"], 12, 15)
    try:
        _helpers._freeze(runtime, _helpers._NOW)
        with caplog.at_level(logging.WARNING, logger="heatingassistant.app.runtime"):
            asyncio.run(_helpers._seed(runtime, {}))
            published = asyncio.run(_publish_bridged(runtime, full, ts=_helpers._STAMP))
            assert published["price_source"] == "energidataservice"
            assert published["tomorrow_valid"] is True
            assert set(published["tariffs"]["tariffs"]) == {str(hour) for hour in range(24)}
            additions = published["tariffs"]["additional_tariffs"]
            assert additions["transmissions_nettarif"] + additions["systemtarif"] + additions["elafgift"] == pytest.approx(0.154)
            assert len(published["raw_today"]) == 96
            assert len(published["raw_tomorrow"]) == 96
            asyncio.run(_helpers._tick(runtime))
            without = dict(full)
            without.pop("tariffs")
            asyncio.run(_publish_bridged(runtime, without, ts=_helpers._STAMP + 1))
            _solve_again(runtime)
            asyncio.run(_helpers._tick(runtime, _helpers._STAMP + 1))
    finally:
        _helpers._restore_solve(real)
        _helpers.runtime_mod.datetime = previous_datetime
        _helpers._restore_tz(previous_tz)

    assert [list(item["price_forecast"])[0] for item in seen[-2:]] == pytest.approx([expected, expected])
    assert [row["price"] for row in _helpers._rows(runtime)[-2:]] == pytest.approx([expected, expected])
    assert runtime.status()["control"]["price_latch_ignored"] == 1
    assert "2026-11-15" in runtime._price_latch.days
    assert "2026-11-16" in runtime._price_latch.days
    assert caplog.text.count("failed validation") == 1


def test_bridge_cold_start_without_tariffs_omits_the_price_term(tmp_path, caplog) -> None:
    previous_tz = _helpers._use_copenhagen()
    previous_datetime = _helpers.runtime_mod.datetime
    runtime = _helpers._runtime(tmp_path, horizon_h=2)
    seen: list[dict] = []
    real = _helpers._watch(seen)
    full = _full_attributes()
    tariff_less = dict(full)
    tariff_less["tariffs"] = {"tariffs": {}, "additional_tariffs": {}}
    expected = _helpers._quarter_price(full["raw_today"], 12, 15)
    try:
        _helpers._freeze(runtime, _helpers._NOW)
        with caplog.at_level(logging.WARNING, logger="heatingassistant.app.runtime"):
            asyncio.run(_helpers._seed(runtime, {}))
            published = asyncio.run(_publish_bridged(runtime, tariff_less, ts=_helpers._STAMP))
            assert published["price_source"] == "energidataservice"
            assert published["tariffs"]["tariffs"] == {}
            asyncio.run(_helpers._tick(runtime))
            awaiting = runtime.status()["control"]["price_latch_awaiting_valid"]
            asyncio.run(_publish_bridged(runtime, full, ts=_helpers._STAMP + 1))
            _solve_again(runtime)
            asyncio.run(_helpers._tick(runtime, _helpers._STAMP + 1))
    finally:
        _helpers._restore_solve(real)
        _helpers.runtime_mod.datetime = previous_datetime
        _helpers._restore_tz(previous_tz)

    assert seen[-2]["price_forecast"] is None
    assert _helpers._rows(runtime)[-2]["price"] is None
    assert awaiting is True
    assert "without an electricity price" in caplog.text
    assert list(seen[-1]["price_forecast"])[0] == pytest.approx(expected)
    assert _helpers._rows(runtime)[-1]["price"] == pytest.approx(expected)
    assert runtime.status()["control"]["price_latch_awaiting_valid"] is False
    assert runtime._price_latch.days
