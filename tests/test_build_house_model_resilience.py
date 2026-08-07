"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import custom_components.heating_assistant.coordinator as coord_mod
from heatingassistant.engine.const import (
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


# ---------------------------------------------------------------------------
# Heat sources pointing at a room that no longer exists
# ---------------------------------------------------------------------------

def _fake_coordinator_with_rooms(*room_names):
    """A minimal stand-in exposing only what _drop_orphaned_sources reads."""
    model = SimpleNamespace(rooms={name: object() for name in room_names})
    return SimpleNamespace(model=model)


def test_orphaned_heat_source_is_dropped_not_raised():
    """A source whose room was deleted/renamed is dropped, not fed to the model.

    Otherwise the controller's room-index lookup raises KeyError during setup
    and the whole integration fails to start.
    """
    coord = _fake_coordinator_with_rooms("kitchen")
    sources = [
        SimpleNamespace(name="kitchen_heater", room="kitchen"),
        SimpleNamespace(name="ghost_heater", room="living_room"),  # orphaned
    ]

    kept = coord_mod.HeatingAssistantCoordinator._drop_orphaned_sources(
        coord, sources
    )

    assert [s.name for s in kept] == ["kitchen_heater"]


def test_all_valid_sources_are_kept():
    coord = _fake_coordinator_with_rooms("kitchen", "living_room")
    sources = [
        SimpleNamespace(name="a", room="kitchen"),
        SimpleNamespace(name="b", room="living_room"),
    ]

    kept = coord_mod.HeatingAssistantCoordinator._drop_orphaned_sources(
        coord, sources
    )

    assert kept == sources


def test_slug_room_name_is_corrected_to_canonical():
    """A source whose room is stored as a slug (e.g. 'living_room') but the
    model uses a canonical name (e.g. 'Living Room') must be kept, not dropped,
    with src.room corrected to the canonical name in-memory.

    This is the regression guard for the bug where _drop_orphaned_sources
    dropped heat sources that were configured with slug-style room names while
    the room model used Title Case names — silencing all heaters and making the
    MPC return zero power across every horizon step.
    """
    coord = _fake_coordinator_with_rooms("Living Room", "Office")
    sources = [
        SimpleNamespace(name="living_room_heater", room="living_room"),
        SimpleNamespace(name="office_heater", room="office"),
    ]

    kept = coord_mod.HeatingAssistantCoordinator._drop_orphaned_sources(
        coord, sources
    )

    assert len(kept) == 2, "Both slug-named sources should be kept after correction"
    assert kept[0].room == "Living Room", "Slug 'living_room' must be corrected to 'Living Room'"
    assert kept[1].room == "Office", "Slug 'office' must be corrected to 'Office'"


def test_truly_orphaned_source_still_dropped():
    """A source referencing a room that has no slug match in the model is dropped."""
    coord = _fake_coordinator_with_rooms("Living Room")
    sources = [
        SimpleNamespace(name="ghost_heater", room="basement"),  # no match at all
        SimpleNamespace(name="lr_heater", room="Living Room"),  # exact match
    ]

    kept = coord_mod.HeatingAssistantCoordinator._drop_orphaned_sources(
        coord, sources
    )

    assert len(kept) == 1
    assert kept[0].name == "lr_heater"
