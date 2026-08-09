"""SWD-269: config writes, history, and MQTT status when broker is down."""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from heatingassistant.app.runtime import HeatingRuntime, publish_tag_in
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload


pytestmark = pytest.mark.unit


class FlakyMqttBus(InMemoryMqttBus):
    def __init__(self) -> None:
        super().__init__()
        self.allow_publish = False
        self.connected = False
        self.connect_handlers: list[Any] = []

    def add_connect_handler(self, handler: Any) -> None:
        self.connect_handlers.append(handler)

    async def publish(
        self,
        topic: str,
        payload: str | bytes,
        *,
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        if not self.allow_publish:
            raise RuntimeError("MQTT client is not connected")
        await super().publish(topic, payload, qos=qos, retain=retain)


@pytest.mark.asyncio
async def test_update_controller_tuning_succeeds_when_mqtt_down(tmp_path) -> None:
    bus = FlakyMqttBus()
    runtime = HeatingRuntime(
        tmp_path,
        bus=bus,
        options={
            "instance_id": "haos",
            "mqtt_broker": "core-mosquitto",
            "comfort_offset": 2.0,
            "tracking_weight": 1.0,
            "rooms": [{"name": "Living Room", "setpoint": 22.0, "temp_tags": ["living_temp"]}],
        },
    )
    await runtime.start()

    result = await runtime.apply_service(
        "heating_assistant",
        "update_controller_tuning",
        {"comfort_offset": 1.5, "tracking_weight": 2.0},
    )

    assert result["config"]["comfort_offset"] == 1.5
    assert result["config"]["tracking_weight"] == 2.0
    assert runtime.config()["comfort_offset"] == 1.5
    assert runtime.status()["mqtt_connected"] is False


@pytest.mark.asyncio
async def test_history_and_kpi_sensors_populate_after_tag_update(tmp_path) -> None:
    bus = InMemoryMqttBus()
    runtime = HeatingRuntime(
        tmp_path,
        bus=bus,
        options={
            "instance_id": "haos",
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
                    "room": "Living Room",
                    "type": "electric",
                    "output_tag": "living_heater",
                }
            ],
            "system_enabled": True,
        },
    )
    await runtime.start()
    await publish_tag_in(runtime, "living_temp", 21.25)
    # History is gated to update_interval (SWD-277); force one sample for the KPI ring.
    runtime._record_history_samples(force=True)

    assert runtime.room_temperatures["Living Room"] == pytest.approx(21.25)
    states = runtime.hass_states()
    assert float(states["sensor.heating_assistant_living_room_temperature_measured"]["state"]) == pytest.approx(
        21.25
    )
    assert "sensor.heating_assistant_mpc_performance" in states
    assert "sensor.heating_assistant_living_room_solar_gain_measured" in states
    assert "sensor.heating_assistant_living_room_energy_total" in states
    assert runtime.state_snapshot()["mqtt_connected"] is True

    history = runtime.history(
        entity_ids=["sensor.heating_assistant_living_room_temperature_measured"]
    )
    samples = history["sensor.heating_assistant_living_room_temperature_measured"]
    assert samples
    assert float(samples[-1]["s"]) == pytest.approx(21.25)


@pytest.mark.asyncio
async def test_energy_accumulation_does_not_integrate_across_restart_gap(tmp_path) -> None:
    bus = InMemoryMqttBus()
    # Simulate persisted totals from a previous App process with a stale clock.
    from heatingassistant.persistence import save_state

    save_state(
        tmp_path,
        {
            "energy_total_wh": {"living_room": 100.0},
            "energy_last_ts": 1_700_000_000.0,  # hours in the past vs now
            "actuator_outputs": {"living_heater": 2000.0},
        },
    )
    runtime = HeatingRuntime(
        tmp_path,
        bus=bus,
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
            "heat_sources": [
                {
                    "room": "Living Room",
                    "type": "electric",
                    "output_tag": "living_heater",
                }
            ],
            "system_enabled": True,
        },
    )
    runtime.actuator_outputs["living_heater"] = 2000.0
    before = float(runtime._energy_total_wh.get("living_room", 0.0))
    assert runtime._energy_last_ts is None

    # First tick only arms the clock (no integration across the restored gap).
    runtime._accumulate_energy(1_700_000_000.0 + 7200.0)
    mid = float(runtime._energy_total_wh.get("living_room", 0.0))
    assert mid == pytest.approx(before)
    assert runtime._energy_last_ts is not None

    # Second tick with a large gap is capped at 2 × update_interval (1800s).
    runtime.actuator_outputs["living_heater"] = 2000.0
    runtime._accumulate_energy(runtime._energy_last_ts + 7200.0)
    after = float(runtime._energy_total_wh.get("living_room", 0.0))
    assert after - mid == pytest.approx(2000.0 * (1800.0 / 3600.0))


@pytest.mark.asyncio
async def test_thin_bridge_publishes_tag_in_with_retain() -> None:
    ha_mqtt = MagicMock()
    published: list[dict[str, Any]] = []

    async def fake_publish(*args: Any, **kwargs: Any) -> None:
        published.append({"args": args, "kwargs": kwargs})

    ha_mqtt.async_publish = AsyncMock(side_effect=fake_publish)

    fake_components = MagicMock()
    fake_components.mqtt = ha_mqtt
    fake_ha = MagicMock()
    fake_ha.components = fake_components
    fake_ha.config_entries = MagicMock()
    fake_ha.const = MagicMock(
        SERVICE_TURN_OFF="turn_off",
        SERVICE_TURN_ON="turn_on",
        STATE_UNAVAILABLE="unavailable",
        STATE_UNKNOWN="unknown",
    )
    fake_ha.core = MagicMock()
    fake_ha.helpers = MagicMock()
    fake_ha.helpers.event = MagicMock(async_track_state_change_event=MagicMock())

    with patch.dict(
        "sys.modules",
        {
            "homeassistant": fake_ha,
            "homeassistant.components": fake_components,
            "homeassistant.config_entries": fake_ha.config_entries,
            "homeassistant.const": fake_ha.const,
            "homeassistant.core": fake_ha.core,
            "homeassistant.helpers": fake_ha.helpers,
            "homeassistant.helpers.event": fake_ha.helpers.event,
        },
    ):
        # Force a clean import under the mocked Home Assistant modules.
        import sys

        for name in list(sys.modules):
            if name.startswith("custom_components.heating_assistant"):
                del sys.modules[name]
        thin_init = importlib.import_module("custom_components.heating_assistant.__init__")
        hass = MagicMock()
        hass.states.get.return_value = MagicMock(state="20.5")
        manager = thin_init._BridgeManager(
            hass, MagicMock(data={"instance_id": "default"})
        )
        await manager._publish_entity_state("living_temp", hass.states.get("sensor.x"))

    assert published
    assert published[0]["kwargs"].get("retain") is True
    payload = MqttTagPayload.decode(published[0]["args"][2])
    assert payload.value == pytest.approx(20.5)
    assert payload.status == "GOOD"
