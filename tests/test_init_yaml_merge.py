"""Tests for YAML/config-entry merge behaviour in integration setup."""

from custom_components.heating_assistant.__init__ import _merge_yaml_into_entry_data
from custom_components.heating_assistant.const import (
    CONF_HEAT_SOURCES,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_ROOMS,
    CONF_WEATHER_ENTITY,
)


def test_merge_uses_yaml_when_entry_has_empty_room_and_source_lists():
    """YAML rooms replace empty-list placeholders in entry.data."""
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


def test_merge_yaml_always_wins_when_yaml_defines_rooms():
    """YAML rooms always replace entry.data rooms so YAML edits take effect immediately."""
    entry_data = {
        CONF_ROOMS: [{"name": "stale_room"}],
        CONF_HEAT_SOURCES: [{"name": "stale_source"}],
    }
    yaml_cfg = {
        CONF_ROOMS: [{"name": "living_room"}, {"name": "bedroom"}],
        CONF_HEAT_SOURCES: [{"name": "heat_pump"}],
    }

    merged = _merge_yaml_into_entry_data(entry_data, yaml_cfg)

    # YAML must win – entry.data rooms/sources are replaced by the YAML ones.
    assert merged[CONF_ROOMS] == yaml_cfg[CONF_ROOMS]
    assert merged[CONF_HEAT_SOURCES] == yaml_cfg[CONF_HEAT_SOURCES]


def test_merge_keeps_entry_rooms_when_yaml_has_no_rooms():
    """When YAML has no rooms, entry.data rooms are preserved as a fallback."""
    entry_data = {
        CONF_ROOMS: [{"name": "persisted_room"}],
        CONF_HEAT_SOURCES: [{"name": "persisted_source"}],
    }
    # YAML block exists but defines no rooms (e.g. user only put outdoor entity)
    yaml_cfg = {
        CONF_ROOMS: [],
        CONF_HEAT_SOURCES: [],
        CONF_OUTDOOR_TEMP_ENTITY: "sensor.outdoor",
    }

    merged = _merge_yaml_into_entry_data(entry_data, yaml_cfg)

    # Entry.data rooms must be kept – YAML has none so we fall back.
    assert merged[CONF_ROOMS] == entry_data[CONF_ROOMS]
    assert merged[CONF_HEAT_SOURCES] == entry_data[CONF_HEAT_SOURCES]


def test_merge_returns_empty_when_both_have_no_rooms():
    """When both YAML and entry.data have no rooms, merged result is empty."""
    entry_data = {CONF_ROOMS: [], CONF_HEAT_SOURCES: []}
    yaml_cfg = {CONF_ROOMS: [], CONF_HEAT_SOURCES: []}

    merged = _merge_yaml_into_entry_data(entry_data, yaml_cfg)

    assert merged[CONF_ROOMS] == []
    assert merged[CONF_HEAT_SOURCES] == []
