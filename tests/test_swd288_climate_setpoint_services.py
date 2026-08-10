"""SWD-288: climate card setpoint / enablement services must persist config."""

from __future__ import annotations

from pathlib import Path

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus


pytestmark = pytest.mark.unit


def _runtime(tmp_path: Path) -> HeatingRuntime:
    return HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "comfort_offset": 2.0,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 22.0,
                    "comfort_offset": 1.0,
                    "temp_tags": ["living_temp"],
                    "enabled": True,
                }
            ],
            "system_enabled": True,
        },
    )


@pytest.mark.asyncio
async def test_climate_set_temperature_persists_room_setpoint(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    await runtime.start()

    result = await runtime.apply_service(
        "climate",
        "set_temperature",
        {
            "entity_id": "climate.heating_assistant_living_room",
            "temperature": 23.5,
        },
    )

    rooms = result["config"]["rooms"]
    living = next(room for room in rooms if room["name"] == "Living Room")
    assert living["setpoint"] == pytest.approx(23.5)
    assert runtime.config()["rooms"][0]["setpoint"] == pytest.approx(23.5)

    states = runtime.hass_states()
    assert float(states["sensor.heating_assistant_living_room_setpoint"]["state"]) == pytest.approx(
        23.5
    )
    assert float(
        states["climate.heating_assistant_living_room"]["attributes"]["temperature"]
    ) == pytest.approx(23.5)


@pytest.mark.asyncio
async def test_set_room_setpoint_service_persists(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    await runtime.start()

    await runtime.apply_service(
        "heating_assistant",
        "set_room_setpoint",
        {"room_name": "living_room", "setpoint": 21.0},
    )

    assert runtime.config()["rooms"][0]["setpoint"] == pytest.approx(21.0)


@pytest.mark.asyncio
async def test_set_room_comfort_offset_service_persists(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    await runtime.start()

    await runtime.apply_service(
        "heating_assistant",
        "set_room_comfort_offset",
        {"room_name": "living_room", "comfort_offset": 1.5},
    )

    assert runtime.config()["rooms"][0]["comfort_offset"] == pytest.approx(1.5)
    offsets = runtime.hass_states()["sensor.heating_assistant_controller_config"][
        "attributes"
    ]["room_comfort_offsets"]
    assert offsets["living_room"] == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_climate_turn_off_disables_room(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    await runtime.start()

    await runtime.apply_service(
        "climate",
        "turn_off",
        {"entity_id": "climate.heating_assistant_living_room"},
    )

    assert runtime.config()["rooms"][0]["enabled"] is False
    assert runtime.hass_states()["climate.heating_assistant_living_room"]["state"] == "off"


@pytest.mark.asyncio
async def test_climate_turn_on_enables_room(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    await runtime.start()
    await runtime.apply_service(
        "heating_assistant",
        "set_room_enabled",
        {"room_name": "living_room", "enabled": False},
    )

    await runtime.apply_service(
        "climate",
        "turn_on",
        {"entity_id": ["climate.heating_assistant_living_room"]},
    )

    assert runtime.config()["rooms"][0]["enabled"] is True
    assert runtime.hass_states()["climate.heating_assistant_living_room"]["state"] == "heat"


@pytest.mark.asyncio
async def test_climate_set_temperature_rejects_unknown_entity(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    await runtime.start()

    with pytest.raises(ValueError, match="unsupported climate entity"):
        await runtime.apply_service(
            "climate",
            "set_temperature",
            {"entity_id": "climate.other_thermostat", "temperature": 22.0},
        )
