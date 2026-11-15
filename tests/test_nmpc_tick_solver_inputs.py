"""Planner ticks must call the real solver with the current sample inputs.

SWD-598 replaced the per-tick ``compute_actions`` call with an estimate plus
``solve_nmpc``. Its tests replaced ``solve_nmpc_blocking`` with a function
that ignored arguments and returned a canned plan, so they never reached
``HeatingMPCController.solve_nmpc`` and missed the missing ``outdoor_temp``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload


def _runtime(tmp_path: Path, *, options: dict | None = None) -> HeatingRuntime:
    return HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options=options
        if options is not None
        else {
            "instance_id": "haos",
            "system_enabled": False,
            "mpc_mode": "nmpc",
            "update_interval": 900,
            "nmpc_period": 900,
            "nmpc_horizon_h": 1.0,
            "nmpc_fast_substeps": 1,
            "price_entity": "sensor.energy_price",
            "outdoor_temp_entity": "sensor.outdoor_temperature",
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
    seen: dict[str, list] = {"calls": [], "writes": []}
    real_solve = controller.solve_nmpc

    def _solve(*args, **kwargs):
        seen["calls"].append({"args": args, "kwargs": dict(kwargs)})
        return real_solve(*args, **kwargs)

    controller.solve_nmpc = _solve  # type: ignore[method-assign]
    real_publish = runtime.publish_actuator_outputs

    async def _publish():
        seen["writes"].append(float(runtime.actuator_outputs.get("heater_heat", 0.0)))
        return await real_publish()

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


async def _prime(runtime: HeatingRuntime, now: float) -> None:
    await _tag(runtime, "living_temp", 16.0, now)
    await _tag(runtime, "outdoor_temp", -5.0, now)
    await _tag(runtime, "energy_price", 0.4, now)


async def _tick(runtime: HeatingRuntime, now: float) -> None:
    await runtime.run_control_tick(now=now)
    thread = getattr(runtime, "_nmpc_thread", None)
    if thread is not None:
        thread.join(timeout=30)
        assert not thread.is_alive()


def _assert_real_solve(seen: dict[str, list]) -> None:
    assert len(seen["calls"]) == 1
    kwargs = seen["calls"][0]["kwargs"]
    assert kwargs["outdoor_temp"] == pytest.approx(-5.0)
    assert kwargs["outdoor_forecast"]
    assert kwargs["price_forecast"]
    trajectory = kwargs["control_trajectory"]
    assert trajectory is not None
    assert "Living Room" in trajectory.setpoints
    assert len(trajectory.setpoints["Living Room"]) > 0
    assert seen["calls"][0]  # the real solver returned


def test_fresh_tick_solves_with_current_inputs(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    assert runtime.control_engine._nmpc_last_kwargs == {}
    seen = _watch(runtime)

    async def _run() -> None:
        await _prime(runtime, 1_000.0)
        await _tick(runtime, 1_000.0)

    asyncio.run(_run())
    _assert_real_solve(seen)
    result_kwargs = seen["calls"][0]["kwargs"]
    assert "outdoor_temp" in result_kwargs
    assert seen["writes"] and len(seen["writes"]) == 1


def test_one_tick_writes_once(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    seen = _watch(runtime)

    async def _run() -> None:
        await _prime(runtime, 2_000.0)
        await _tick(runtime, 2_000.0)

    asyncio.run(_run())
    _assert_real_solve(seen)
    assert len(seen["writes"]) == 1


def test_restart_tick_solves_with_current_inputs(tmp_path: Path) -> None:
    first = _runtime(tmp_path)
    first_seen = _watch(first)

    async def _first() -> None:
        await _prime(first, 3_000.0)
        await _tick(first, 3_000.0)

    asyncio.run(_first())
    _assert_real_solve(first_seen)

    restarted = HeatingRuntime(tmp_path, bus=InMemoryMqttBus())
    assert restarted.control_engine._nmpc_last_kwargs == {}
    seen = _watch(restarted)

    async def _second() -> None:
        await _tick(restarted, 3_900.0)

    asyncio.run(_second())
    _assert_real_solve(seen)
    assert len(seen["writes"]) == 1
