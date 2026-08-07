"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

import importlib
import sys

import pytest

# conftest stubs voluptuous as a passthrough; reload the real package so
# CONFIG_SCHEMA performs actual validation.
if "voluptuous" in sys.modules:
    del sys.modules["voluptuous"]
import voluptuous as vol  # noqa: E402

sys.modules["voluptuous"] = vol

import custom_components.heating_assistant.config_schema as _config_schema_mod  # noqa: E402

importlib.reload(_config_schema_mod)
from custom_components.heating_assistant.config_schema import CONFIG_SCHEMA  # noqa: E402
from heatingassistant.engine.const import (
    CONF_HEAT_SOURCES,
    CONF_INFILTRATION_FRACTION,
    CONF_ROOM_NAME,
    CONF_ROOMS,
    CONF_SOURCE_MAX_POWER,
    CONF_SOURCE_NAME,
    CONF_SOURCE_ROOM,
    CONF_SOURCE_TYPE,
    CONF_TERMINAL_WEIGHT,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    SOURCE_TYPE_ELECTRIC,
)


def _minimal_config(**overrides):
    """Build a minimal valid config dict, applying optional overrides."""
    base = {DOMAIN: {}}
    if overrides:
        base[DOMAIN].update(overrides)
    return base


def test_minimal_empty_config_valid():
    """An empty integration block is accepted."""
    result = CONFIG_SCHEMA(_minimal_config())
    assert DOMAIN in result
    assert CONF_ROOMS not in result[DOMAIN] or result[DOMAIN][CONF_ROOMS] == []
    assert CONF_HEAT_SOURCES not in result[DOMAIN] or result[DOMAIN][CONF_HEAT_SOURCES] == []


def test_minimal_room_and_source_valid():
    """A single room and heat source with required fields validates."""
    config = _minimal_config(
        rooms=[{CONF_ROOM_NAME: "living_room"}],
        heat_sources=[
            {
                CONF_SOURCE_NAME: "heater",
                CONF_SOURCE_TYPE: SOURCE_TYPE_ELECTRIC,
                CONF_SOURCE_ROOM: "living_room",
                CONF_SOURCE_MAX_POWER: 2000,
            }
        ],
    )
    result = CONFIG_SCHEMA(config)
    assert result[DOMAIN][CONF_ROOMS][0][CONF_ROOM_NAME] == "living_room"
    assert result[DOMAIN][CONF_HEAT_SOURCES][0][CONF_SOURCE_MAX_POWER] == 2000.0


def test_room_with_nested_window_and_connection():
    """Nested room schemas (windows, connections, schedule) validate."""
    config = _minimal_config(
        rooms=[
            {
                CONF_ROOM_NAME: "bedroom",
                "windows": [{"area": 2.5, "orientation": 180.0}],
                "connections": [{"room": "hall", "r_value": 0.1}],
                "schedule": [
                    {
                        "start": "06:00",
                        "end": "22:00",
                        "setpoint": 21.0,
                    }
                ],
            }
        ],
    )
    result = CONFIG_SCHEMA(config)
    room = result[DOMAIN][CONF_ROOMS][0]
    assert room["windows"][0]["area"] == 2.5
    assert room["connections"][0]["room"] == "hall"


@pytest.mark.parametrize(
    "config,match",
    [
        (
            _minimal_config(update_interval=30),
            "update_interval",
        ),
        (
            _minimal_config(
                rooms=[{CONF_ROOM_NAME: "x", CONF_INFILTRATION_FRACTION: 1.5}]
            ),
            "infiltration_fraction",
        ),
        (
            _minimal_config(terminal_weight=0.5),
            "terminal_weight",
        ),
        (
            _minimal_config(identification_history_days=3),
            "identification_history_days",
        ),
        (
            _minimal_config(
                heat_sources=[
                    {
                        CONF_SOURCE_NAME: "bad",
                        CONF_SOURCE_TYPE: "unknown_type",
                        CONF_SOURCE_ROOM: "x",
                        CONF_SOURCE_MAX_POWER: 1000,
                    }
                ]
            ),
            "type",
        ),
        (
            _minimal_config(rooms=[{"setpoint": 21.0}]),
            "name",
        ),
        (
            _minimal_config(
                heat_sources=[
                    {
                        CONF_SOURCE_NAME: "incomplete",
                        CONF_SOURCE_TYPE: SOURCE_TYPE_ELECTRIC,
                        CONF_SOURCE_MAX_POWER: 1000,
                    }
                ]
            ),
            "room",
        ),
    ],
)
def test_invalid_values_rejected(config, match):
    """Out-of-range or missing required fields raise voluptuous.Invalid."""
    with pytest.raises(vol.Invalid, match=match):
        CONFIG_SCHEMA(config)
