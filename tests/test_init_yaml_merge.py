"""Tests for YAML/config-entry merge behaviour in integration setup."""

from custom_components.heating_assistant.__init__ import _merge_yaml_into_entry_data
from custom_components.heating_assistant.const import (
    CONF_HEAT_SOURCES,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_ROOMS,
    CONF_WEATHER_ENTITY,
)


def test_merge_uses_yaml_when_entry_has_empty_room_and_source_lists():
    entry_data = {
        CONF_ROOMS: [],
        CONF_HEAT_SOURCES: [],
        CONF_OUTDOOR_TEMP_ENTITY: "",
        CONF_WEATHER_ENTITY: "",
    }
    yaml_cfg = {
        CONF_ROOMS: [{"name": "living_room"}],
        CONF_HEAT_SOURCES: [{"name": "heater_1"}],
        CONF_OUTDOOR_TEMP_ENTITY: "sensor.outdoor",
        CONF_WEATHER_ENTITY: "weather.home",
    }

    merged = _merge_yaml_into_entry_data(entry_data, yaml_cfg)

    assert merged[CONF_ROOMS] == yaml_cfg[CONF_ROOMS]
    assert merged[CONF_HEAT_SOURCES] == yaml_cfg[CONF_HEAT_SOURCES]
    assert merged[CONF_OUTDOOR_TEMP_ENTITY] == "sensor.outdoor"
    assert merged[CONF_WEATHER_ENTITY] == "weather.home"


def test_merge_preserves_non_empty_entry_room_and_source_lists():
    entry_data = {
        CONF_ROOMS: [{"name": "entry_room"}],
        CONF_HEAT_SOURCES: [{"name": "entry_source"}],
    }
    yaml_cfg = {
        CONF_ROOMS: [{"name": "yaml_room"}],
        CONF_HEAT_SOURCES: [{"name": "yaml_source"}],
    }

    merged = _merge_yaml_into_entry_data(entry_data, yaml_cfg)

    assert merged[CONF_ROOMS] == entry_data[CONF_ROOMS]
    assert merged[CONF_HEAT_SOURCES] == entry_data[CONF_HEAT_SOURCES]
