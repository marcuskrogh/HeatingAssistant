"""SWD-318: ID samples on ticker + update_tag; durable-first append."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload


pytestmark = pytest.mark.unit


def _options(*, update_interval: float = 900) -> dict:
    return {
        "instance_id": "haos",
        "update_interval": update_interval,
        "rooms": [
            {
                "name": "Living Room",
                "setpoint": 22.0,
                "temp_tags": ["living_temp"],
                "enabled": True,
            }
        ],
        "heat_sources": [
            {
                "name": "Living Heater",
                "room": "Living Room",
                "type": "electric",
                "output_tag": "living_heater",
                "max_power": 1000.0,
            }
        ],
        "system_enabled": True,
    }


def _runtime(tmp_path: Path, **opts) -> HeatingRuntime:
    return HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options=_options(**opts),
    )


def test_update_tag_records_identification_sample(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, update_interval=900)
    assert len(runtime.history_buffer) == 0

    runtime.update_tag("living_temp", MqttTagPayload(value=21.25, status="GOOD"))

    assert len(runtime.history_buffer) == 1
    assert runtime.history_buffer[-1]["y"][0] == pytest.approx(21.25)
    assert (tmp_path / "id_history" / "haos").exists()


def test_update_tag_respects_interval_gate(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, update_interval=900)
    runtime.update_tag("living_temp", MqttTagPayload(value=21.0, status="GOOD"))
    assert len(runtime.history_buffer) == 1

    runtime.update_tag("living_temp", MqttTagPayload(value=21.5, status="GOOD"))
    assert len(runtime.history_buffer) == 1


@pytest.mark.asyncio
async def test_ticker_records_id_sample_without_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, update_interval=30)
    runtime.update_tag("living_temp", MqttTagPayload(value=20.5, status="GOOD"))

    monkeypatch.setattr(runtime, "_history_tick_interval_s", lambda: 0.05)
    # Keep control quiet so ID write is from the history tick, not MPC.
    monkeypatch.setattr(runtime, "_control_tick_interval_s", lambda: 60.0)

    await runtime.start()
    try:
        # Mark control as recently run and clear ID state so only the ticker writes.
        runtime._last_control_ts = time.time()
        runtime._history_buffer.clear()
        runtime._id_history_last_ts = 0.0
        deadline = time.time() + 2.0
        while time.time() < deadline and len(runtime.history_buffer) == 0:
            time.sleep(0.05)
        assert len(runtime.history_buffer) >= 1
        assert runtime.history_buffer[-1]["y"][0] == pytest.approx(20.5)
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_control_cycle_still_records_when_gate_allows(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, update_interval=900)
    runtime.update_tag("living_temp", MqttTagPayload(value=19.5, status="GOOD"))
    # Exhaust the gate from update_tag, then force a later control sample.
    runtime._id_history_last_ts = 0.0
    before = len(runtime.history_buffer)

    await runtime.run_control_cycle()

    assert len(runtime.history_buffer) >= before
    assert runtime.history_buffer[-1]["y"][0] == pytest.approx(19.5)


def test_durable_first_skips_buffer_on_append_failure(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, update_interval=900)
    runtime.update_tag("living_temp", MqttTagPayload(value=22.0, status="GOOD"))
    # Reset gate after the successful update_tag sample.
    runtime._history_buffer.clear()
    runtime._id_history_last_ts = 0.0

    failing = MagicMock()
    failing.append.side_effect = OSError("disk full")
    failing.purge_old = MagicMock()
    runtime.id_history_store = failing

    runtime._record_identification_sample(time.time(), force=True)

    assert len(runtime.history_buffer) == 0
    assert runtime._id_history_last_ts == 0.0
    failing.append.assert_called_once()
    failing.purge_old.assert_not_called()


def test_purge_failure_still_commits_after_append(tmp_path: Path) -> None:
    """Append success commits buffer even when retention purge fails."""

    runtime = _runtime(tmp_path, update_interval=900)
    runtime.update_tag("living_temp", MqttTagPayload(value=22.0, status="GOOD"))
    runtime._history_buffer.clear()
    runtime._id_history_last_ts = 0.0

    store = MagicMock()
    store.append = MagicMock()
    store.purge_old.side_effect = OSError("purge failed")
    runtime.id_history_store = store

    stamp = time.time()
    runtime._record_identification_sample(stamp, force=True)

    assert len(runtime.history_buffer) == 1
    assert runtime._id_history_last_ts == pytest.approx(stamp)
    store.append.assert_called_once()
    store.purge_old.assert_called_once()


@pytest.mark.asyncio
async def test_control_durable_first_skips_buffer_on_async_failure(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path, update_interval=900)
    runtime.update_tag("living_temp", MqttTagPayload(value=18.0, status="GOOD"))
    runtime._id_history_last_ts = 0.0
    runtime._history_buffer.clear()

    failing = MagicMock()
    failing.async_append = AsyncMock(side_effect=OSError("disk full"))
    failing.async_purge_old = AsyncMock()
    runtime.id_history_store = failing

    await runtime.run_control_cycle()

    assert len(runtime.history_buffer) == 0
    assert runtime._id_history_last_ts == 0.0
    failing.async_append.assert_awaited_once()
