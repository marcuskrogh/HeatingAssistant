"""Tests for YAML/config-entry merge behaviour in integration setup."""

from custom_components.heating_assistant.__init__ import _merge_yaml_into_entry_data
from custom_components.heating_assistant.const import (
    CONF_HEAT_SOURCES,
    CONF_MPC_ANALYTIC_DERIVATIVES,
    CONF_MPC_MODE,
    CONF_MPC_SOLVER,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_ROOM_NAME,
    CONF_ROOMS,
    CONF_SCHEDULE,
    CONF_SIGMA_B,
    CONF_SIGMA_V,
    CONF_SIGMA_W,
    CONF_WEATHER_ENTITY,
    DEFAULT_MPC_ANALYTIC_DERIVATIVES,
    DEFAULT_MPC_MODE,
    DEFAULT_MPC_SOLVER,
    DEFAULT_SIGMA_B,
    DEFAULT_SIGMA_V,
    DEFAULT_SIGMA_W,
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

    assert merged[CONF_SIGMA_W] == DEFAULT_SIGMA_W
    assert merged[CONF_SIGMA_V] == DEFAULT_SIGMA_V
    assert merged[CONF_SIGMA_B] == DEFAULT_SIGMA_B
    assert merged[CONF_MPC_MODE] == DEFAULT_MPC_MODE


def test_merge_migrates_legacy_solver_to_mpc_mode():
    entry_data = {CONF_ROOMS: [], CONF_HEAT_SOURCES: []}

    assert (
        _merge_yaml_into_entry_data(entry_data, {CONF_MPC_SOLVER: "ipopt"})[
            CONF_MPC_MODE
        ]
        == "non-linear"
    )
    assert (
        _merge_yaml_into_entry_data(entry_data, {CONF_MPC_SOLVER: "slsqp"})[
            CONF_MPC_MODE
        ]
        == "non-linear"
    )
    assert (
        _merge_yaml_into_entry_data(entry_data, {CONF_MPC_SOLVER: "qp"})[
            CONF_MPC_MODE
        ]
        == "linear"
    )


def test_merge_yaml_rooms_preserve_stored_schedules():
    """YAML rooms must not discard dashboard-configured schedules.

    When rooms are defined in YAML (normal setup), merging YAML into entry data
    previously replaced the room list completely, stripping any CONF_SCHEDULE key
    that the update_room_schedule service had written to the config entry.  The
    coordinator then loaded no schedule, the config sensor exposed empty
    room_schedules, and the frontend showed nothing after a reload.
    """
    periods = [
        {
            "name": "Morning",
            "mode": "comfort",
            "start": "06:00",
            "end": "09:00",
            "days": [0, 1, 2, 3, 4],
            "setpoint": 21.0,
        }
    ]
    entry_data = {
        CONF_ROOMS: [
            {CONF_ROOM_NAME: "Living Room", "thermal_mass": 5_000_000.0, CONF_SCHEDULE: periods},
            {CONF_ROOM_NAME: "Bedroom", "thermal_mass": 3_000_000.0},
        ],
        CONF_HEAT_SOURCES: [],
    }
    # YAML defines the same rooms but without any schedule key — typical real setup
    yaml_cfg = {
        CONF_ROOMS: [
            {CONF_ROOM_NAME: "Living Room", "thermal_mass": 5_000_000.0},
            {CONF_ROOM_NAME: "Bedroom", "thermal_mass": 3_000_000.0},
        ],
        CONF_HEAT_SOURCES: [],
    }

    merged = _merge_yaml_into_entry_data(entry_data, yaml_cfg)

    # Living Room had a schedule in entry_data — it must survive the YAML merge.
    living = next(r for r in merged[CONF_ROOMS] if r[CONF_ROOM_NAME] == "Living Room")
    assert living.get(CONF_SCHEDULE) == periods, (
        "Schedule was discarded by YAML merge — schedules will disappear on reload"
    )

    # Bedroom had no schedule — no CONF_SCHEDULE key must be injected.
    bedroom = next(r for r in merged[CONF_ROOMS] if r[CONF_ROOM_NAME] == "Bedroom")
    assert CONF_SCHEDULE not in bedroom


def test_merge_yaml_rooms_without_stored_schedules_unchanged():
    """When stored entry rooms have no schedule the YAML rooms are used as-is."""
    entry_data = {
        CONF_ROOMS: [
            {CONF_ROOM_NAME: "Living Room"},
        ],
        CONF_HEAT_SOURCES: [],
    }
    yaml_cfg = {
        CONF_ROOMS: [
            {CONF_ROOM_NAME: "Living Room", "thermal_mass": 5_000_000.0},
            {CONF_ROOM_NAME: "Bedroom", "thermal_mass": 3_000_000.0},
        ],
        CONF_HEAT_SOURCES: [],
    }

    merged = _merge_yaml_into_entry_data(entry_data, yaml_cfg)

    assert merged[CONF_ROOMS] == yaml_cfg[CONF_ROOMS]


def test_merge_preserves_entry_sigma_values():
    entry_data = {
        CONF_ROOMS: [],
        CONF_HEAT_SOURCES: [],
        CONF_SIGMA_W: 0.25,
        CONF_SIGMA_V: 0.75,
        CONF_SIGMA_B: 0.01,
    }
    yaml_cfg = {
        CONF_SIGMA_W: 0.5,
        CONF_SIGMA_V: 1.5,
        CONF_SIGMA_B: 0.02,
    }

    merged = _merge_yaml_into_entry_data(entry_data, yaml_cfg)

    assert merged[CONF_SIGMA_W] == 0.25
    assert merged[CONF_SIGMA_V] == 0.75
    assert merged[CONF_SIGMA_B] == 0.01
