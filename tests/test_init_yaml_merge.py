"""Tests for YAML/config-entry merge behaviour in integration setup."""

from custom_components.heating_assistant.__init__ import _merge_yaml_into_entry_data
from custom_components.heating_assistant.const import (
    CONF_HEAT_SOURCES,
    CONF_MPC_ANALYTIC_DERIVATIVES,
    CONF_MPC_SOLVER,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_ROOMS,
    CONF_WEATHER_ENTITY,
    DEFAULT_MPC_ANALYTIC_DERIVATIVES,
    DEFAULT_MPC_SOLVER,
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


def test_merge_yaml_takes_precedence_when_yaml_has_rooms_and_sources():
    entry_data = {
        CONF_ROOMS: [{"name": "entry_room"}],
        CONF_HEAT_SOURCES: [{"name": "entry_source"}],
    }
    yaml_cfg = {
        CONF_ROOMS: [{"name": "living_room"}, {"name": "bedroom"}],
        CONF_HEAT_SOURCES: [{"name": "heat_pump"}],
    }

    merged = _merge_yaml_into_entry_data(entry_data, yaml_cfg)

    assert merged[CONF_ROOMS] == yaml_cfg[CONF_ROOMS]
    assert merged[CONF_HEAT_SOURCES] == yaml_cfg[CONF_HEAT_SOURCES]


def test_merge_keeps_entry_rooms_and_sources_when_yaml_has_none():
    entry_data = {
        CONF_ROOMS: [{"name": "persisted_room"}],
        CONF_HEAT_SOURCES: [{"name": "persisted_source"}],
    }
    yaml_cfg = {
        CONF_ROOMS: [],
        CONF_HEAT_SOURCES: [],
        CONF_OUTDOOR_TEMP_ENTITY: "sensor.outdoor",
    }

    merged = _merge_yaml_into_entry_data(entry_data, yaml_cfg)

    assert merged[CONF_ROOMS] == entry_data[CONF_ROOMS]
    assert merged[CONF_HEAT_SOURCES] == entry_data[CONF_HEAT_SOURCES]


def test_merge_returns_empty_when_both_have_no_rooms_or_sources():
    entry_data = {CONF_ROOMS: [], CONF_HEAT_SOURCES: []}
    yaml_cfg = {CONF_ROOMS: [], CONF_HEAT_SOURCES: []}

    merged = _merge_yaml_into_entry_data(entry_data, yaml_cfg)

    assert merged[CONF_ROOMS] == []
    assert merged[CONF_HEAT_SOURCES] == []


def test_merge_populates_solver_defaults():
    entry_data = {CONF_ROOMS: [], CONF_HEAT_SOURCES: []}
    yaml_cfg = {}

    merged = _merge_yaml_into_entry_data(entry_data, yaml_cfg)

    assert merged[CONF_MPC_SOLVER] == DEFAULT_MPC_SOLVER
    assert (
        merged[CONF_MPC_ANALYTIC_DERIVATIVES]
        == DEFAULT_MPC_ANALYTIC_DERIVATIVES
    )
