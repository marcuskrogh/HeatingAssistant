"""SWD-490: solar-gain EMA survives App restart and controller rebuild."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.const import SOLAR_GAIN_SMOOTHING_TAU_S
from heatingassistant.engine.solar_model import smooth_solar_gain_step
from heatingassistant.mqtt.bridge import InMemoryMqttBus

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
DT = 900.0
ROOM = "Living Room"
ENTITY = "sensor.heating_assistant_living_room_solar_gain_measured"


def _options() -> dict:
    return {
        "instance_id": "persist",
        "update_interval": 900,
        "latitude": 55.7,
        "longitude": 12.6,
        "rooms": [
            {
                "name": ROOM,
                "setpoint": 22.0,
                "temp_tags": ["living_temp"],
                "enabled": True,
                "windows": [{"area": 8.0, "azimuth": 180, "tilt": 90, "shgc": 0.5}],
            }
        ],
        "heat_sources": [
            {
                "name": "Living Heater",
                "room": ROOM,
                "type": "electric",
                "output_tag": "living_heater",
                "max_power": 4000.0,
            }
        ],
        "system_enabled": True,
    }


def _compute(runtime: HeatingRuntime, when: datetime, cloud: float) -> float:
    ctrl = runtime.control_engine._controller
    assert ctrl is not None
    ctrl.compute(
        outdoor_temp=18.0,
        solar_gains=None,
        now=when,
        cloud_cover_now=cloud,
        cloud_forecast=[cloud] * 8,
    )
    runtime.control_engine._cache_controller_forecast(ctrl)
    return float(ctrl._solar_forecast[0][ROOM])


def test_restart_continues_filtered_k0(tmp_path: Path) -> None:
    first = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    assert first.control_engine._controller is not None
    clear = _compute(first, NOW, 0.0)
    overcast_t = NOW + timedelta(seconds=DT)
    lagged = _compute(first, overcast_t, 1.0)
    inst = first.control_engine._controller._room_gain(
        ROOM, overcast_t, 1.0, None,
    )
    assert inst < lagged < clear
    first._save_runtime_state()

    restarted = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    applied = restarted._applied_solar_gains()[ROOM]
    published = float(restarted.hass_states()[ENTITY]["state"])
    assert applied == pytest.approx(lagged)
    assert published == pytest.approx(round(lagged, 1))
    assert applied != pytest.approx(inst)

    next_t = overcast_t + timedelta(seconds=DT)
    continued = _compute(restarted, next_t, 1.0)
    inst_next = restarted.control_engine._controller._room_gain(
        ROOM, next_t, 1.0, None,
    )
    expected = smooth_solar_gain_step(
        lagged, inst_next, DT, SOLAR_GAIN_SMOOTHING_TAU_S,
    )
    assert continued == pytest.approx(expected)
    seeded = inst_next
    assert continued != pytest.approx(seeded)


def test_update_config_keeps_solar_ema(tmp_path: Path) -> None:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    _compute(runtime, NOW, 0.0)
    lagged = _compute(runtime, NOW + timedelta(seconds=DT), 1.0)
    runtime.control_engine.update_config(dict(runtime.options))
    assert runtime.control_engine.solar_gain_filter_state()[ROOM] == pytest.approx(
        lagged
    )
    assert runtime._applied_solar_gains()[ROOM] == pytest.approx(lagged)
