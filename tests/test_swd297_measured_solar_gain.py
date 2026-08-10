"""SWD-297: publish applied solar_gain_measured (not hardcoded 0)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.mqtt.bridge import InMemoryMqttBus


pytestmark = pytest.mark.unit

_SOLAR_ENTITY = "sensor.heating_assistant_living_room_solar_gain_measured"


def _options(**overrides) -> dict:
    base = {
        "instance_id": "haos",
        "plot_history_hours": 12.0,
        "update_interval": 900,
        "latitude": 55.7,
        "longitude": 12.6,
        "rooms": [
            {
                "name": "Living Room",
                "setpoint": 22.0,
                "temp_tags": ["living_temp"],
                "enabled": True,
                "solar_exposure": "high",
                "solar_facing": 180,
                "windows": [{"area": 2.5, "azimuth": 180, "tilt": 90, "shgc": 0.5}],
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
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_hass_states_publishes_cached_solar_gain(tmp_path: Path) -> None:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    await runtime.start()
    runtime.control_engine._last_solar_forecast = [{"Living Room": 1234.5}]

    states = runtime.hass_states()
    assert float(states[_SOLAR_ENTITY]["state"]) == pytest.approx(1234.5)
    assert states[_SOLAR_ENTITY]["attributes"]["window_count"] == 1
    assert states[_SOLAR_ENTITY]["attributes"]["total_window_area"] == pytest.approx(2.5)
    await runtime.stop()


@pytest.mark.asyncio
async def test_history_records_solar_gain_samples(tmp_path: Path) -> None:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    await runtime.start()
    runtime.control_engine._last_solar_forecast = [{"Living Room": 800.0}]
    runtime._record_history_samples(force=True)

    samples = runtime.history(entity_ids=[_SOLAR_ENTITY]).get(_SOLAR_ENTITY, [])
    assert samples
    assert float(samples[-1]["s"]) == pytest.approx(800.0)
    await runtime.stop()


def test_applied_solar_gains_prefers_forecast_cache() -> None:
    engine = ControlEngine(
        {
            "update_interval": 900,
            "horizon": 4,
            "latitude": 55.7,
            "longitude": 12.6,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 22.0,
                    "solar_exposure": "high",
                    "solar_facing": 180,
                    "windows": [],
                }
            ],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 2000.0,
                }
            ],
        }
    )
    engine._last_solar_forecast = [{"Living Room": 42.0}]
    assert engine.applied_solar_gains()["Living Room"] == pytest.approx(42.0)


def test_applied_solar_gains_live_fallback_daytime() -> None:
    """With an empty forecast cache, geometric compute must still return daytime gain."""
    engine = ControlEngine(
        {
            "update_interval": 900,
            "horizon": 4,
            "latitude": 55.7,
            "longitude": 12.6,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 22.0,
                    "solar_exposure": "high",
                    "solar_facing": 180,
                    "windows": [],
                }
            ],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 2000.0,
                }
            ],
        }
    )
    if engine._controller is None:
        pytest.skip("MPC controller unavailable in this environment")

    noon = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    gains = engine.applied_solar_gains(now=noon, cloud_cover_now=0.2)
    assert gains["Living Room"] > 1.0


@pytest.mark.asyncio
async def test_hass_states_solar_after_mpc_compute(tmp_path: Path) -> None:
    """End-to-end: daytime MPC compute → non-zero solar_gain_measured."""
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    await runtime.start()
    engine = runtime.control_engine
    if engine._controller is None:
        await runtime.stop()
        pytest.skip("MPC controller unavailable in this environment")

    noon = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    engine._controller.compute(
        outdoor_temp=18.0,
        solar_gains=None,
        now=noon,
        cloud_cover_now=0.2,
    )
    engine._cache_controller_forecast(engine._controller)

    states = runtime.hass_states()
    assert float(states[_SOLAR_ENTITY]["state"]) > 1.0
    await runtime.stop()
