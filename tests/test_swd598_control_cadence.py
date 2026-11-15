"""SWD-598: one planner solve and one actuator write per 15-minute sample.

A climate offset hold between samples is a separate write. It is not one of
these sample writes, and these tests still expect one solve and one
``publish_actuator_outputs`` call per tick.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.controller.ekf import _InnovationEKF
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload


def _runtime(tmp_path: Path) -> HeatingRuntime:
    return HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "system_enabled": False,
            "mpc_mode": "nmpc",
            "update_interval": 900,
            "nmpc_period": 900,
            "price_tag": "energy_price",
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 21.0,
                    "temp_tags": ["living_temp"],
                }
            ],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 1500.0,
                    "output_tag": "heater_heat",
                }
            ],
        },
    )


def _watch(runtime: HeatingRuntime) -> dict[str, list]:
    controller = runtime.control_engine._controller
    assert controller is not None
    seen: dict[str, list] = {"dt": [], "solves": [], "writes": []}
    ekf = controller._ekf
    original = ekf.predict_for

    def _predict_for(dt, *args, **kwargs):
        seen["dt"].append(float(dt))
        return original(dt, *args, **kwargs)

    ekf.predict_for = _predict_for  # type: ignore[method-assign]

    def _solve():
        live = runtime.control_engine._controller
        assert live is not None
        seen["solves"].append(runtime._active_tick_now)
        return {
            "accepted": True,
            "u_star": np.full((live.timing.n_slow, 1), 0.55),
            "t_ref": np.full((live.timing.n_fast, 1), 21.0),
            "fun": 0.0,
            "message": "ok",
        }

    runtime.control_engine.solve_nmpc_blocking = _solve  # type: ignore[method-assign]

    async def _publish():
        seen["writes"].append(float(runtime.actuator_outputs.get("heater_heat", 0.0)))

    runtime.publish_actuator_outputs = _publish  # type: ignore[method-assign]
    return seen


async def _tag(runtime: HeatingRuntime, tag: str, value: float, now: float) -> None:
    await runtime._handle_tag_message(
        f"heatingassistant/haos/tag/{tag}/in",
        MqttTagPayload(value=value, ts=now).encode(),
        qos=0,
        retain=False,
        now=now,
    )


async def _tick(runtime: HeatingRuntime, now: float) -> None:
    await runtime.run_control_tick(now=now)
    thread = getattr(runtime, "_nmpc_thread", None)
    if thread is not None:
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_sensor_and_price_events_do_not_solve_or_write(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    seen = _watch(runtime)

    async def _run() -> None:
        await _tag(runtime, "living_temp", 20.0, 1_000.0)
        await _tag(runtime, "living_temp", 20.2, 1_037.0)
        await _tag(runtime, "energy_price", 0.4, 1_100.0)
        await _tag(runtime, "outdoor", 3.0, 1_200.0)
        await _tag(runtime, "living_temp", 20.4, 1_400.0)

    asyncio.run(_run())
    assert seen["solves"] == []
    assert seen["writes"] == []
    assert seen["dt"] == pytest.approx([37.0, 363.0])


def test_one_solve_and_one_write_per_tick_between_sensor_events(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    seen = _watch(runtime)
    controller = runtime.control_engine._controller
    assert controller is not None
    epoch = 10_000.0
    runtime._anchor_schedule_epoch(epoch)
    stale = np.full((controller.timing.n_slow, 1), 0.2)
    stale[1, 0] = 0.9
    controller.set_accepted_path(
        stale,
        np.full((controller.timing.n_fast, 1), 21.0),
        plan_epoch=epoch,
        now=epoch,
    )

    async def _run() -> None:
        await _tag(runtime, "living_temp", 19.0, epoch)
        for stamp in (epoch + 30.0, epoch + 90.0, epoch + 200.0, epoch + 450.0):
            await _tag(runtime, "living_temp", 19.0 + stamp / 1.0e6, stamp)
            await _tag(runtime, "energy_price", 0.2, stamp + 1.0)
        await _tick(runtime, epoch + 900.0)
        await _tick(runtime, epoch + 900.0)
        for stamp in (epoch + 1000.0, epoch + 1400.0):
            await _tag(runtime, "living_temp", 19.5, stamp)
            await _tag(runtime, "energy_price", 0.8, stamp + 5.0)
        await _tick(runtime, epoch + 1800.0)

    asyncio.run(_run())
    assert seen["solves"] == [epoch + 900.0, epoch + 1800.0]
    assert seen["writes"] == pytest.approx([0.55, 0.55])
    assert 900.0 not in seen["dt"]
    assert 0.9 not in seen["writes"]


def test_predict_dt_is_real_elapsed_time(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    seen = _watch(runtime)
    origin = 50_000.0

    async def _case(gap: float) -> None:
        runtime._ekf_last_t = origin
        if gap != 37.0:
            runtime._anchor_schedule_epoch(origin)
        await _tag(runtime, "living_temp", 18.0, origin)
        before = len(seen["dt"])
        if gap == 37.0:
            await _tag(runtime, "living_temp", 18.1, origin + gap)
        else:
            await _tick(runtime, origin + gap)
        assert seen["dt"][before:] == pytest.approx([gap])

    async def _run() -> None:
        await _case(37.0)
        await _case(900.0)
        await _case(1800.0)

    asyncio.run(_run())
    assert seen["dt"] == pytest.approx([37.0, 900.0, 1800.0])
    assert seen["solves"] == [origin + 900.0, origin + 1800.0]
    assert len(seen["writes"]) == 2


def test_config_save_predicts_real_elapsed_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime(tmp_path)
    seen = _watch(runtime)
    origin = 20_000.0
    runtime._ekf_last_t = origin
    runtime._anchor_schedule_epoch(origin)
    original = _InnovationEKF.predict_for

    def _predict_for(self, dt, *args, **kwargs):
        seen["dt"].append(float(dt))
        return original(self, dt, *args, **kwargs)

    monkeypatch.setattr(_InnovationEKF, "predict_for", _predict_for)

    async def _run() -> None:
        await _tag(runtime, "living_temp", 19.0, origin)
        await runtime.update_config({"energy_price_weight": 0.0}, now=origin + 123.0)
        thread = getattr(runtime, "_nmpc_thread", None)
        if thread is not None:
            thread.join(timeout=3)
            assert not thread.is_alive()

    asyncio.run(_run())
    assert seen["dt"] == pytest.approx([123.0])
    assert seen["solves"] == [origin + 123.0]
    assert seen["writes"] == pytest.approx([0.55])
