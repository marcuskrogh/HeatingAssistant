"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import custom_components.heating_assistant.__init__ as init_mod
from heatingassistant.engine.const import (
    CONF_CONNECTIONS,
    CONF_CONNECTED_ROOM,
    CONF_ESTIMATED_PARAMS,
    CONF_HEAT_SOURCES,
    CONF_PERSISTED_COMFORT_OFFSETS,
    CONF_PERSISTED_SCHEDULES,
    CONF_PERSISTED_SETPOINTS,
    CONF_ROOMS,
    CONF_SOURCE_ROOM,
    CONF_R_VALUE,
)


def test_migrate_room_name_data_moves_all_keyed_stores():
    data = {
        CONF_PERSISTED_SETPOINTS: {"living_room": 21.0, "kitchen": 20.0},
        CONF_PERSISTED_COMFORT_OFFSETS: {"living_room": 1.5},
        CONF_PERSISTED_SCHEDULES: {"living_room": [{"start": "08:00", "end": "22:00"}]},
        CONF_ESTIMATED_PARAMS: {
            "rooms": {"living_room": {"thermal_mass": 5e6}, "kitchen": {"thermal_mass": 3e6}},
            "sources": {"heater": {"power_scale": 1.0}},
        },
        CONF_HEAT_SOURCES: [
            {"name": "heater", CONF_SOURCE_ROOM: "living_room", "max_power": 2000},
            {"name": "kitchen_heater", CONF_SOURCE_ROOM: "kitchen", "max_power": 1000},
        ],
    }
    renames = {"living_room": "lounge"}

    out = init_mod._migrate_room_name_data(data, renames)

    assert out[CONF_PERSISTED_SETPOINTS] == {"lounge": 21.0, "kitchen": 20.0}
    assert out[CONF_PERSISTED_COMFORT_OFFSETS] == {"lounge": 1.5}
    assert "lounge" in out[CONF_PERSISTED_SCHEDULES]
    assert "living_room" not in out[CONF_PERSISTED_SCHEDULES]
    assert set(out[CONF_ESTIMATED_PARAMS]["rooms"]) == {"lounge", "kitchen"}
    # The sources sub-dict (keyed by source name) is untouched.
    assert out[CONF_ESTIMATED_PARAMS]["sources"] == {"heater": {"power_scale": 1.0}}
    # The heater that pointed at the renamed room follows it; the other doesn't.
    rooms_by_source = {s["name"]: s[CONF_SOURCE_ROOM] for s in out[CONF_HEAT_SOURCES]}
    assert rooms_by_source == {"heater": "lounge", "kitchen_heater": "kitchen"}


def test_migrate_room_name_data_noop_without_renames():
    data = {CONF_PERSISTED_SETPOINTS: {"living_room": 21.0}}
    out = init_mod._migrate_room_name_data(data, {})
    assert out == data
    assert out is not data  # returns a copy


def test_apply_renames_to_connections():
    rooms = [
        {"name": "lounge", CONF_CONNECTIONS: [{CONF_CONNECTED_ROOM: "kitchen", CONF_R_VALUE: 0.2}]},
        {"name": "kitchen", CONF_CONNECTIONS: [{CONF_CONNECTED_ROOM: "living_room", CONF_R_VALUE: 0.2}]},
    ]
    out = init_mod._apply_renames_to_connections(rooms, {"living_room": "lounge"})
    # kitchen's connection to the old name is re-pointed; the other is untouched.
    assert out[1][CONF_CONNECTIONS][0][CONF_CONNECTED_ROOM] == "lounge"
    assert out[0][CONF_CONNECTIONS][0][CONF_CONNECTED_ROOM] == "kitchen"


def test_remap_keys_handles_non_dict():
    assert init_mod._remap_keys(None, {"a": "b"}) is None
    assert init_mod._remap_keys([1, 2], {"a": "b"}) == [1, 2]
