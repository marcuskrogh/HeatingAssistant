"""SWD-276: wall-clock history/control ticker when MQTT tags are quiet."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload


pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_background_ticker_records_history_without_tag_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "update_interval": 30,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 22.0,
                    "temp_tags": ["living_temp"],
                }
            ],
        },
    )
    runtime.update_tag("living_temp", MqttTagPayload(value=21.5, status="GOOD"))
    entity = "sensor.heating_assistant_living_room_temperature_measured"
    before = len(runtime.history(entity_ids=[entity]).get(entity, []))

    # Speed up the ticker for the test without waiting a full minute.
    monkeypatch.setattr(runtime, "_history_tick_interval_s", lambda: 0.05)
    monkeypatch.setattr(runtime, "_control_tick_interval_s", lambda: 10.0)

    await runtime.start()
    try:
        deadline = time.time() + 2.0
        after = before
        while time.time() < deadline:
            after = len(runtime.history(entity_ids=[entity]).get(entity, []))
            if after > before:
                break
            time.sleep(0.05)
        assert after > before
        samples = runtime.history(entity_ids=[entity])[entity]
        assert samples[-1]["lu"] >= samples[0]["lu"]
    finally:
        await runtime.stop()
    assert runtime._ticker_thread is None or not runtime._ticker_thread.is_alive()


@pytest.mark.asyncio
async def test_background_ticker_runs_control_when_tags_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "update_interval": 30,
            "rooms": [{"name": "Living Room", "setpoint": 22.0, "temp_tags": ["living_temp"]}],
        },
    )
    runtime.update_tag("living_temp", MqttTagPayload(value=21.0, status="GOOD"))
    monkeypatch.setattr(runtime, "_history_tick_interval_s", lambda: 10.0)
    monkeypatch.setattr(runtime, "_control_tick_interval_s", lambda: 0.05)

    await runtime.start()
    try:
        # Clear the start-up control timestamp so the ticker must run.
        runtime._last_control_ran_ts = None
        deadline = time.time() + 2.0
        while time.time() < deadline and runtime._last_control_ran_ts is None:
            time.sleep(0.05)
        assert runtime._last_control_ran_ts is not None
    finally:
        await runtime.stop()
