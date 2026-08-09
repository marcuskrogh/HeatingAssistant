"""SWD-284: room Price plot needs electricity_price synthetic + day-ahead history."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from heatingassistant.app.runtime import HeatingRuntime, _ELECTRICITY_PRICE_ENTITY, publish_tag_in
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload


pytestmark = pytest.mark.unit


def _options(**overrides) -> dict:
    base = {
        "instance_id": "haos",
        "plot_history_hours": 12.0,
        "update_interval": 900,
        "price_tag": "energy_price",
        "price_net_tariff": 0.1,
        "price_spot_surcharge": 0.05,
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
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_hass_states_publishes_electricity_price(tmp_path: Path) -> None:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    await runtime.start()
    await publish_tag_in(runtime, "energy_price", 0.42)

    states = runtime.hass_states()
    assert _ELECTRICITY_PRICE_ENTITY in states
    # Scalar path applies tariff adder (0.1 + 0.05).
    assert float(states[_ELECTRICITY_PRICE_ENTITY]["state"]) == pytest.approx(0.57)
    assert states[_ELECTRICITY_PRICE_ENTITY]["attributes"]["price_tag"] == "energy_price"
    await runtime.stop()


@pytest.mark.asyncio
async def test_history_records_electricity_price_samples(tmp_path: Path) -> None:
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    await runtime.start()
    await publish_tag_in(runtime, "energy_price", 0.33)
    runtime._record_history_samples(force=True)

    samples = runtime.history(entity_ids=[_ELECTRICITY_PRICE_ENTITY]).get(
        _ELECTRICITY_PRICE_ENTITY, []
    )
    assert samples
    assert float(samples[-1]["s"]) == pytest.approx(0.48)  # 0.33 + 0.15 adder
    await runtime.stop()


@pytest.mark.asyncio
async def test_history_synthesizes_price_from_raw_today(tmp_path: Path) -> None:
    """Day-ahead attrs must backfill historical Price even with an empty ring."""
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=_options())
    await runtime.start()

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    raw_today = [
        {"start": (now - timedelta(hours=2)).isoformat(), "value": 0.20},
        {"start": (now - timedelta(hours=1)).isoformat(), "value": 0.30},
        {"start": now.isoformat(), "value": 0.40},
    ]
    runtime.update_tag(
        "energy_price",
        MqttTagPayload(
            value=0.40,
            status="GOOD",
            reason=None,
            ts=now.timestamp(),
            attributes={
                "raw_today": raw_today,
                "unit_of_measurement": "EUR/kWh",
            },
        ),
    )

    start_ts = (now - timedelta(hours=3)).timestamp()
    end_ts = (now + timedelta(minutes=30)).timestamp()
    samples = runtime.history(
        entity_ids=[_ELECTRICITY_PRICE_ENTITY],
        start_ts=start_ts,
        end_ts=end_ts,
    ).get(_ELECTRICITY_PRICE_ENTITY, [])

    assert len(samples) >= 3
    values = [float(s["s"]) for s in samples]
    # Adder 0.15 applied to each day-ahead slot.
    assert any(v == pytest.approx(0.35) for v in values)
    assert any(v == pytest.approx(0.45) for v in values)
    assert any(v == pytest.approx(0.55) for v in values)

    states = runtime.hass_states()
    assert states[_ELECTRICITY_PRICE_ENTITY]["attributes"]["unit_of_measurement"] == (
        "EUR/kWh"
    )
    # Current sensor value comes from day-ahead at now (0.40 + 0.15).
    assert float(states[_ELECTRICITY_PRICE_ENTITY]["state"]) == pytest.approx(0.55)
    await runtime.stop()


@pytest.mark.asyncio
async def test_history_empty_without_price_tag(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options=_options(price_tag="missing_price"),
    )
    await runtime.start()
    samples = runtime.history(entity_ids=[_ELECTRICITY_PRICE_ENTITY]).get(
        _ELECTRICITY_PRICE_ENTITY, []
    )
    assert samples == []
    states = runtime.hass_states()
    assert states[_ELECTRICITY_PRICE_ENTITY]["state"] == "unknown"
    await runtime.stop()
