"""SWD-281: durable plot + identification history across App restart."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import time

import pytest

from heatingassistant.app.plot_history import PlotHistoryStore
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.history.store import IdentificationHistoryStore
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.app.runtime import publish_tag_in


pytestmark = pytest.mark.unit


def _options() -> dict:
    return {
        "instance_id": "haos",
        "plot_history_hours": 12.0,
        "parameter_estimation_history_days": 30,
        "update_interval": 900,
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


@pytest.mark.asyncio
async def test_plot_history_survives_runtime_restart(tmp_path: Path) -> None:
    bus = InMemoryMqttBus()
    runtime = HeatingRuntime(tmp_path, bus=bus, options=_options())
    await runtime.start()
    await publish_tag_in(runtime, "living_temp", 21.5)
    runtime._record_history_samples(force=True)

    entity_id = "sensor.heating_assistant_living_room_temperature_measured"
    before = runtime.history(entity_ids=[entity_id])[entity_id]
    assert before
    assert float(before[-1]["s"]) == pytest.approx(21.5)
    assert (tmp_path / "plot_history").exists()

    await runtime.stop()

    # Simulate App update/restart: new process, same /data volume.
    restarted = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    restored = restarted.history(entity_ids=[entity_id]).get(entity_id, [])
    assert restored
    assert any(float(sample["s"]) == pytest.approx(21.5) for sample in restored)
    await restarted.stop()


@pytest.mark.asyncio
async def test_identification_history_survives_runtime_restart(tmp_path: Path) -> None:
    bus = InMemoryMqttBus()
    runtime = HeatingRuntime(tmp_path, bus=bus, options=_options())
    await runtime.start()
    await publish_tag_in(runtime, "living_temp", 20.75)
    # Control cycle records identification history (original coordinator tick).
    await runtime.run_control_cycle()
    # Force u after compute so the stored record is deterministic for the test.
    runtime.actuator_outputs["living_heater"] = 0.4
    stamp = time.time()
    runtime._record_identification_sample(stamp, force=True)
    runtime._save_runtime_state()

    assert len(runtime.history_buffer) >= 1
    last = runtime.history_buffer[-1]
    assert last["y"][0] == pytest.approx(20.75)
    assert last["u"][0] == pytest.approx(0.4)
    assert "d_outdoor" in last
    assert "d_solar" in last
    assert "timestamp" in last
    assert (tmp_path / "id_history" / "haos").exists()

    await runtime.stop()

    restarted = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    assert len(restarted.history_buffer) >= 1
    matching = [
        rec
        for rec in restarted.history_buffer
        if abs(float(rec.get("timestamp", 0.0)) - stamp) < 1e-6
    ]
    assert matching
    restored = matching[-1]
    assert restored["y"][0] == pytest.approx(20.75)
    assert restored["u"][0] == pytest.approx(0.4)
    await restarted.stop()


def test_plot_history_store_round_trip(tmp_path: Path) -> None:
    store = PlotHistoryStore(tmp_path, retention_days=3)
    store.setup()
    now = 1_700_000_000.0
    store.append_samples(
        [
            {"entity_id": "sensor.heating_assistant_a", "s": "21.0", "lu": now - 100},
            {"entity_id": "sensor.heating_assistant_a", "s": "21.5", "lu": now},
            {"entity_id": "sensor.heating_assistant_b", "s": "18.0", "lu": now},
        ]
    )
    loaded = store.load_recent(hours_back=1.0, max_per_entity=100, now_ts=now + 1)
    assert list(loaded["sensor.heating_assistant_a"])[-1]["s"] == "21.5"
    assert list(loaded["sensor.heating_assistant_b"])[-1]["s"] == "18.0"


def test_identification_history_store_accepts_data_dir(tmp_path: Path) -> None:
    store = IdentificationHistoryStore(
        entry_id="default",
        retention_days=7,
        data_dir=tmp_path,
    )
    store.setup()
    store.append(
        {
            "y": [20.0],
            "u": [0.2],
            "d_outdoor": 5.0,
            "d_solar": {"Living Room": 0.0},
            "timestamp": time.time(),
        }
    )
    recent = store.query_recent(10)
    assert len(recent) == 1
    assert recent[0]["y"] == [20.0]
    assert (tmp_path / "id_history" / "default").is_dir()


def test_plot_and_id_retention_purge_old_day_files(tmp_path: Path) -> None:
    plot = PlotHistoryStore(tmp_path, retention_days=2)
    plot.setup()
    today = date.today()
    old_day = today - timedelta(days=10)
    keep_day = today - timedelta(days=1)
    (tmp_path / "plot_history" / f"{old_day.isoformat()}.jsonl").write_text(
        '{"e":"sensor.heating_assistant_a","s":"1","lu":1}\n', encoding="utf-8"
    )
    (tmp_path / "plot_history" / f"{keep_day.isoformat()}.jsonl").write_text(
        '{"e":"sensor.heating_assistant_a","s":"2","lu":2}\n', encoding="utf-8"
    )
    plot._last_purge_date = None
    plot.purge_old()
    assert not (tmp_path / "plot_history" / f"{old_day.isoformat()}.jsonl").exists()
    assert (tmp_path / "plot_history" / f"{keep_day.isoformat()}.jsonl").exists()

    store = IdentificationHistoryStore(
        entry_id="haos", retention_days=3, data_dir=tmp_path
    )
    store.setup()
    old_id = tmp_path / "id_history" / "haos" / f"{old_day.isoformat()}.jsonl"
    keep_id = tmp_path / "id_history" / "haos" / f"{keep_day.isoformat()}.jsonl"
    old_id.write_text('{"y":[1],"timestamp":1}\n', encoding="utf-8")
    keep_id.write_text('{"y":[2],"timestamp":2}\n', encoding="utf-8")
    store._last_purge_date = None
    store.purge_old()
    assert not old_id.exists()
    assert keep_id.exists()
