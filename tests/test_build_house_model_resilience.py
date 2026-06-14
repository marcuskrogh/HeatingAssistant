"""Regression tests for ``build_house_model`` resilience.

A room's inter-room connections reference the adjacent room by name.  Deleting
or renaming a room can leave other rooms with a connection pointing at a name
that no longer exists.  Such a dangling connection (or a window/connection with
invalid numeric values) must be dropped with a warning rather than raise — a
stale sub-record left behind by a room edit must never crash integration setup
(which would take the whole integration, and therefore every room, offline).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import custom_components.heating_assistant.coordinator as coord_mod
from custom_components.heating_assistant.const import (
    CONF_CONNECTED_ROOM,
    CONF_CONNECTIONS,
    CONF_ROOM_NAME,
    CONF_R_VALUE,
    CONF_WINDOW_AREA,
    CONF_WINDOW_ORIENTATION,
    CONF_WINDOWS,
)


def test_dangling_connection_is_dropped_not_raised():
    """A connection to a room that no longer exists is dropped, model still builds."""
    rooms_cfg = [
        {
            CONF_ROOM_NAME: "living_room",
            # "bedroom" was deleted — this link now dangles.
            CONF_CONNECTIONS: [
                {CONF_CONNECTED_ROOM: "bedroom", CONF_R_VALUE: 0.2},
            ],
        },
        {CONF_ROOM_NAME: "kitchen"},
    ]

    model = coord_mod.build_house_model(rooms_cfg)

    # Both real rooms survive; the dangling connection is gone.
    assert set(model.rooms) == {"living_room", "kitchen"}
    assert model.rooms["living_room"].connections == []


def test_valid_connection_is_kept_and_coerced():
    rooms_cfg = [
        {
            CONF_ROOM_NAME: "living_room",
            # r_value as a string — coerced to float, kept.
            CONF_CONNECTIONS: [{CONF_CONNECTED_ROOM: "bedroom", CONF_R_VALUE: "0.2"}],
        },
        {CONF_ROOM_NAME: "bedroom"},
    ]

    model = coord_mod.build_house_model(rooms_cfg)

    conns = model.rooms["living_room"].connections
    assert len(conns) == 1
    assert conns[0].connected_room == "bedroom"
    assert conns[0].r_value == 0.2


def test_zero_r_value_connection_is_dropped():
    rooms_cfg = [
        {
            CONF_ROOM_NAME: "a",
            CONF_CONNECTIONS: [{CONF_CONNECTED_ROOM: "b", CONF_R_VALUE: 0}],
        },
        {CONF_ROOM_NAME: "b"},
    ]

    model = coord_mod.build_house_model(rooms_cfg)
    assert model.rooms["a"].connections == []


def test_invalid_window_is_dropped_not_raised():
    rooms_cfg = [
        {
            CONF_ROOM_NAME: "living_room",
            CONF_WINDOWS: [
                {CONF_WINDOW_AREA: 2.0, CONF_WINDOW_ORIENTATION: 180},  # valid
                {CONF_WINDOW_ORIENTATION: 180},  # missing area — dropped
            ],
        },
    ]

    model = coord_mod.build_house_model(rooms_cfg)
    assert len(model.rooms["living_room"].windows) == 1
    assert model.rooms["living_room"].windows[0].area == 2.0
