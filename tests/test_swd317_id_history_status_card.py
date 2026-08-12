"""SWD-317: ID history health card metrics (System Status only)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from heatingassistant.app.id_history_health import (
    ID_HISTORY_APPEND_ERROR_STREAK,
    evaluate_id_history_health,
)
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.app.system_health import SystemQuality
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload


pytestmark = pytest.mark.unit


def test_age_warns_after_two_intervals() -> None:
    snap = evaluate_id_history_health(
        now_ts=10_000.0,
        update_interval_s=900.0,
        buffer_last_ts=10_000.0 - 1801.0,
        disk_last_ts=10_000.0 - 1801.0,
        append_failure_streak=0,
        last_append_ok=True,
    )
    assert snap["last_sample_quality"] == "warning"
    assert snap["append_quality"] == "healthy"
    assert snap["lag_quality"] == "healthy"


def test_append_error_only_after_three_failures() -> None:
    two = evaluate_id_history_health(
        now_ts=1_000.0,
        update_interval_s=900.0,
        buffer_last_ts=999.0,
        disk_last_ts=999.0,
        append_failure_streak=2,
        last_append_ok=False,
    )
    assert two["append_quality"] == "healthy"
    assert "failed" in two["append_detail"]

    three = evaluate_id_history_health(
        now_ts=1_000.0,
        update_interval_s=900.0,
        buffer_last_ts=999.0,
        disk_last_ts=999.0,
        append_failure_streak=ID_HISTORY_APPEND_ERROR_STREAK,
        last_append_ok=False,
    )
    assert three["append_quality"] == "error"


def test_lag_warns_when_buffer_ahead_of_disk() -> None:
    snap = evaluate_id_history_health(
        now_ts=10_000.0,
        update_interval_s=900.0,
        buffer_last_ts=10_000.0,
        disk_last_ts=10_000.0 - 2000.0,
        append_failure_streak=0,
        last_append_ok=True,
    )
    assert snap["buffer_disk_lag_s"] == pytest.approx(2000.0)
    assert snap["lag_quality"] == "warning"


@pytest.mark.asyncio
async def test_runtime_exposes_id_history_without_changing_overall(
    tmp_path: Path,
) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "update_interval": 900,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 22.0,
                    "temp_tags": ["living_temp"],
                }
            ],
        },
    )
    await runtime.start()
    try:
        before = runtime.system_health()["quality"]
        runtime.update_tag("living_temp", MqttTagPayload(value=21.0, status="GOOD"))
        stamp = time.time() - 2000.0
        runtime._id_history_last_ts = stamp
        runtime._id_history_disk_last_ts = stamp
        runtime._id_history_append_failure_streak = 3
        runtime._id_history_last_append_ok = False

        idh = runtime.id_history_health()
        assert idh["last_sample_quality"] == "warning"
        assert idh["append_quality"] == "error"

        after = runtime.system_health()["quality"]
        assert after == before
        assert after in {
            SystemQuality.HEALTHY.value,
            SystemQuality.WARNING.value,
            SystemQuality.ERROR.value,
        }

        summary = runtime.hass_states()["sensor.heating_assistant_system_summary"]
        assert "id_history" in summary["attributes"]
        assert summary["attributes"]["id_history"]["append_quality"] == "error"
        assert summary["attributes"]["system_quality"] == after
    finally:
        await runtime.stop()


def test_append_failure_streak_resets_on_success(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "update_interval": 900,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 22.0,
                    "temp_tags": ["living_temp"],
                    "enabled": True,
                }
            ],
        },
    )
    runtime.update_tag("living_temp", MqttTagPayload(value=22.0, status="GOOD"))
    runtime._id_history_last_ts = 0.0
    runtime._history_buffer.clear()

    failing = MagicMock()
    failing.append.side_effect = OSError("disk full")
    failing.purge_old = MagicMock()
    runtime.id_history_store = failing

    for _ in range(3):
        runtime._record_identification_sample(time.time(), force=True)
    assert runtime._id_history_append_failure_streak == 3
    assert runtime.id_history_health()["append_quality"] == "error"

    runtime.id_history_store = MagicMock()
    runtime.id_history_store.append = MagicMock()
    runtime.id_history_store.purge_old = MagicMock()
    runtime._record_identification_sample(time.time(), force=True)
    assert runtime._id_history_append_failure_streak == 0
    assert runtime._id_history_last_append_ok is True
    assert runtime.id_history_health()["append_quality"] == "healthy"
