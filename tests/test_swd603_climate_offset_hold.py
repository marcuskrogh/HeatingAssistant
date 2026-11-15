"""SWD-603: climate offset hold between planner samples.

The hold is the real actuation path. The tick test uses the real solver.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from heatingassistant.app.actuation import (
    CLIMATE_TARGET_STEP_C,
    commanded_climate_offset,
    quantize_hold_target,
)
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.controller.facade import HeatingMPCController
from heatingassistant.engine.heat_sources import HeatPump
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload, tag_out


def _options(**overrides) -> dict:
    options = {
        "instance_id": "haos",
        "system_enabled": True,
        "mpc_mode": "nmpc",
        "nmpc_period": 900,
        "nmpc_fast_substeps": 1,
        "nmpc_horizon_h": 2,
        "rooms": [
            {
                "name": "Living Room",
                "setpoint": 21.0,
                "temp_tags": ["living_temp"],
                "enabled": True,
            }
        ],
        "heat_sources": [
            {
                "name": "Vaillant",
                "type": "heat_pump",
                "room": "Living Room",
                "max_power": 7000.0,
                "hvac_mode": "heat",
                "delta_sat": 3.0,
                "output_tag": "hp_heat",
                "heater_entity": "climate.hp",
            }
        ],
    }
    options.update(overrides)
    return options


def _runtime(tmp_path: Path, **overrides) -> HeatingRuntime:
    return HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options(**overrides))


def _arm(runtime: HeatingRuntime, internal: float, fraction: float, now: float) -> None:
    runtime.tag_values["hp_heat_state"] = "heat"
    runtime.tag_statuses["hp_heat_state"] = "GOOD"
    runtime.tag_attributes["hp_heat_state"] = {"current_temperature": internal}
    runtime.actuator_outputs["hp_heat"] = fraction
    runtime._climate_command_now = now
    asyncio.run(runtime.publish_actuator_outputs())
    runtime._climate_command_now = None


async def _state(runtime: HeatingRuntime, value, internal: float | None, now: float, *, status: str = "GOOD") -> None:
    attrs = None if internal is None else {"current_temperature": internal}
    await runtime._handle_tag_message(
        "heatingassistant/haos/tag/hp_heat_state/in",
        MqttTagPayload(value=value, status=status, ts=now, attributes=attrs).encode(),
        qos=0,
        retain=False,
        now=now,
    )


def _targets(runtime: HeatingRuntime) -> list[float]:
    topic = tag_out("haos", "hp_heat")
    found = []
    published = getattr(runtime.bus, "published", [])
    for topic_i, payload, _qos, _retain in published:
        if topic_i != topic:
            continue
        decoded = MqttTagPayload.decode(payload)
        value = decoded.value
        if isinstance(value, dict) and value.get("temperature") is not None and "hvac_mode" not in value:
            found.append(float(value["temperature"]))
    return found


def test_hold_flag_defaults_on(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    assert "climate_offset_hold" not in runtime.options
    assert runtime._climate_offset_hold_enabled() is True


def test_hold_rewrites_target_when_internal_temp_changes(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    runtime = _runtime(tmp_path)
    _arm(runtime, 20.0, 0.8, now=1_000.0)
    stored = runtime._climate_offset_holds["hp_heat"]
    offset = stored["commanded_offset"]
    assert offset > 0.0
    assert offset <= 3.0

    async def _run() -> None:
        await _state(runtime, "heat", 21.3, 1_010.0)

    with caplog.at_level("INFO"):
        asyncio.run(_run())

    targets = _targets(runtime)
    assert len(targets) == 1
    expected = quantize_hold_target(21.3, offset)
    assert targets[0] == pytest.approx(expected)
    assert targets[0] % CLIMATE_TARGET_STEP_C == pytest.approx(0.0, abs=1e-9)
    assert targets[0] - 21.3 <= offset + 1e-9
    assert runtime.status()["control"]["climate_offset_hold_writes"] == 1
    assert any(
        "climate offset hold write" in rec.message
        and "zone=Living Room" in rec.message
        and "reason=internal_changed" in rec.message
        for rec in caplog.records
    )


def test_unchanged_internal_temp_does_not_write(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _arm(runtime, 20.0, 0.8, now=1_000.0)

    async def _run() -> None:
        await _state(runtime, "heat", 20.0, 1_010.0)

    asyncio.run(_run())
    assert _targets(runtime) == []
    assert runtime._climate_offset_hold_writes == 0


def test_hold_is_capped_at_one_write_per_minute(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _arm(runtime, 20.0, 0.8, now=1_000.0)

    async def _run() -> None:
        await _state(runtime, "heat", 21.0, 1_010.0)
        await _state(runtime, "heat", 22.0, 1_020.0)
        await _state(runtime, "heat", 22.4, 1_070.0)

    asyncio.run(_run())
    assert len(_targets(runtime)) == 2
    assert runtime._climate_offset_hold_writes == 2


def test_hold_respects_offset_step_and_saturation(tmp_path: Path) -> None:
    pump = HeatPump("Vaillant", "Living Room", max_power=7000.0, delta_sat=3.0, hvac_mode="heat")
    saturated = commanded_climate_offset(pump.target_temperature(1.0, 20.0), 20.0, pump.delta_sat)
    assert saturated == pytest.approx(pump.delta_sat)

    runtime = _runtime(tmp_path)
    _arm(runtime, 20.0, 1.0, now=5_000.0)
    stored = runtime._climate_offset_holds["hp_heat"]["commanded_offset"]
    assert stored == pytest.approx(saturated)
    snapped = quantize_hold_target(20.6, stored)
    assert snapped % CLIMATE_TARGET_STEP_C == pytest.approx(0.0, abs=1e-9)
    assert snapped - 20.6 <= stored + 1e-9
    assert snapped - 20.6 <= pump.delta_sat + 1e-9
    assert snapped != pytest.approx(runtime._climate_offset_holds["hp_heat"]["last_written_target"])

    async def _run() -> None:
        await _state(runtime, "heat", 20.6, 5_010.0)

    asyncio.run(_run())
    written = _targets(runtime)
    assert written == [pytest.approx(snapped)]
    assert written[0] - 20.6 <= stored + 1e-9


def test_stale_command_is_not_restored(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _arm(runtime, 20.0, 0.8, now=1_000.0)

    async def _run() -> None:
        await _state(runtime, "heat", 22.0, 1_000.0 + 2 * 900.0 + 1.0)

    asyncio.run(_run())
    assert _targets(runtime) == []


@pytest.mark.parametrize(
    ("value", "status"),
    [("unavailable", "BAD"), ("unknown", "GOOD"), ("off", "GOOD"), ("cool", "GOOD")],
)
def test_hold_refuses_bad_climate_state(tmp_path: Path, value: str, status: str) -> None:
    runtime = _runtime(tmp_path)
    _arm(runtime, 20.0, 0.8, now=1_000.0)

    async def _run() -> None:
        await _state(runtime, value, 22.0, 1_030.0, status=status)

    asyncio.run(_run())
    assert _targets(runtime) == []


def test_flag_off_matches_sample_only_writes(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, climate_offset_hold=False)
    _arm(runtime, 20.0, 0.8, now=1_000.0)

    async def _run() -> None:
        await _state(runtime, "heat", 22.0, 1_030.0)

    asyncio.run(_run())
    assert _targets(runtime) == []
    assert runtime._climate_offset_hold_writes == 0


def test_tick_still_writes_once_and_hold_does_not_solve(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.tag_values["living_temp"] = 16.0
    runtime.room_temperatures["Living Room"] = 16.0
    runtime.tag_values["outdoor"] = 4.4
    runtime.tag_values["hp_heat_state"] = "heat"
    runtime.tag_statuses["hp_heat_state"] = "GOOD"
    runtime.tag_attributes["hp_heat_state"] = {"current_temperature": 30.0}
    solves: list[int] = []
    real = HeatingMPCController.solve_nmpc

    def _solve(self, *args, **kwargs):
        solves.append(1)
        return real(self, *args, **kwargs)

    HeatingMPCController.solve_nmpc = _solve  # type: ignore[method-assign]
    writes: list[float] = []
    real_publish = runtime.publish_actuator_outputs

    async def _publish():
        writes.append(1.0)
        await real_publish()

    runtime.publish_actuator_outputs = _publish  # type: ignore[method-assign]
    predicts: list[float] = []
    controller = runtime.control_engine._controller
    assert controller is not None
    ekf = controller._ekf
    real_predict = ekf.predict_for

    def _predict(dt, *args, **kwargs):
        predicts.append(float(dt))
        return real_predict(dt, *args, **kwargs)

    ekf.predict_for = _predict  # type: ignore[method-assign]

    async def _run() -> None:
        await runtime.run_control_tick(now=10_000.0)
        thread = getattr(runtime, "_nmpc_thread", None)
        if thread is not None:
            thread.join(timeout=60)
            assert not thread.is_alive()
        before_predict = len(predicts)
        before_solves = len(solves)
        await _state(runtime, "heat", 31.2, 10_030.0)
        assert len(solves) == before_solves
        assert len(predicts) == before_predict

    try:
        asyncio.run(_run())
    finally:
        HeatingMPCController.solve_nmpc = real  # type: ignore[method-assign]

    assert writes == [1.0]
    assert len(solves) == 1
