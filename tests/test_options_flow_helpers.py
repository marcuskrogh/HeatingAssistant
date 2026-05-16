"""Unit tests for the pure-Python options-flow helpers (U4).

``RoomFlowHelper`` and ``WindowFlowHelper`` carry the room and window CRUD
logic that ``HeatingAssistantOptionsFlow`` used to inline.  Pulling the logic
out is what made these tests possible — they don't need HA's flow framework.
"""

from __future__ import annotations

import pytest

from custom_components.heating_assistant._options_flow import (
    BUILDING_AGE_TO_R_EXTERNAL,
    COMPASS_TO_DEGREES,
    ROOM_SIZE_TO_THERMAL_MASS,
    RoomFlowHelper,
    WindowFlowHelper,
    degrees_to_compass,
    format_entity_ids,
    nearest_choice,
    parse_entity_ids,
    window_display,
)
from custom_components.heating_assistant.const import (
    CONF_ROOM_NAME,
    CONF_SETPOINT,
    CONF_R_EXTERNAL,
    CONF_TEMP_SENSOR,
    CONF_TEMP_SENSORS,
    CONF_THERMAL_MASS,
    CONF_WINDOWS,
    CONF_WINDOW_AREA,
    CONF_WINDOW_ORIENTATION,
    CONF_WINDOW_TILT,
)


# ── Pure helpers ──────────────────────────────────────────────────────────


def test_parse_entity_ids_splits_and_trims():
    assert parse_entity_ids("sensor.a, sensor.b ,sensor.c") == [
        "sensor.a",
        "sensor.b",
        "sensor.c",
    ]


def test_parse_entity_ids_empty_returns_empty_list():
    assert parse_entity_ids("") == []
    assert parse_entity_ids("   ") == []


def test_format_entity_ids_joins_with_comma_space():
    assert format_entity_ids(["sensor.a", "sensor.b"]) == "sensor.a, sensor.b"


def test_nearest_choice_picks_closest_value():
    assert nearest_choice(5e6, ROOM_SIZE_TO_THERMAL_MASS, "medium") == "medium"
    assert nearest_choice(8e6, ROOM_SIZE_TO_THERMAL_MASS, "medium") == "large"
    assert nearest_choice(3.5e6, ROOM_SIZE_TO_THERMAL_MASS, "medium") == "small"


def test_degrees_to_compass_round_trip():
    for label, deg in COMPASS_TO_DEGREES.items():
        assert degrees_to_compass(deg) == label


def test_degrees_to_compass_handles_wrap():
    assert degrees_to_compass(359.0) == "N"
    assert degrees_to_compass(361.0) == "N"


def test_window_display_renders_human_label():
    label = window_display(
        "kitchen",
        0,
        {CONF_WINDOW_AREA: 1.2, CONF_WINDOW_ORIENTATION: 180.0, CONF_WINDOW_TILT: 90.0},
    )
    assert "kitchen" in label
    assert "Window 1" in label
    assert "1.2 m²" in label
    assert "facing S" in label
    assert "tilt 90" in label


# ── RoomFlowHelper ────────────────────────────────────────────────────────


def _add_kitchen(helper: RoomFlowHelper) -> None:
    err = helper.add(
        name="kitchen",
        sensors=["sensor.kitchen"],
        thermal_mass=ROOM_SIZE_TO_THERMAL_MASS["medium"],
        r_external=BUILDING_AGE_TO_R_EXTERNAL["1980_1999"],
        setpoint=22.0,
    )
    assert err is None


def test_room_helper_starts_empty():
    helper = RoomFlowHelper()
    assert not helper
    assert helper.names() == []
    assert helper.current_room() is None


def test_room_helper_seeds_from_existing_rooms_by_copying():
    seed = [{CONF_ROOM_NAME: "bedroom", "x": 1}]
    helper = RoomFlowHelper(seed)
    assert helper.names() == ["bedroom"]
    # Mutating the seed must not leak into the helper.
    seed[0]["x"] = 99
    assert helper.rooms[0]["x"] == 1


def test_room_helper_add_appends_full_dict():
    helper = RoomFlowHelper()
    _add_kitchen(helper)

    assert len(helper) == 1
    room = helper.rooms[0]
    assert room[CONF_ROOM_NAME] == "kitchen"
    assert room[CONF_TEMP_SENSORS] == ["sensor.kitchen"]
    # The legacy singular key is populated from the first sensor for backwards
    # compatibility with code paths that haven't been migrated to the plural.
    assert room[CONF_TEMP_SENSOR] == "sensor.kitchen"
    assert room[CONF_THERMAL_MASS] == ROOM_SIZE_TO_THERMAL_MASS["medium"]
    assert room[CONF_R_EXTERNAL] == BUILDING_AGE_TO_R_EXTERNAL["1980_1999"]
    assert room[CONF_SETPOINT] == 22.0
    assert room[CONF_WINDOWS] == []


def test_room_helper_add_without_sensors_omits_singular_key():
    helper = RoomFlowHelper()
    err = helper.add(
        name="bedroom",
        sensors=[],
        thermal_mass=ROOM_SIZE_TO_THERMAL_MASS["small"],
        r_external=BUILDING_AGE_TO_R_EXTERNAL["pre_1940"],
        setpoint=21.0,
    )
    assert err is None
    assert CONF_TEMP_SENSOR not in helper.rooms[0]


def test_room_helper_add_rejects_case_insensitive_duplicates():
    helper = RoomFlowHelper()
    _add_kitchen(helper)
    err = helper.add(
        name="KITCHEN",
        sensors=[],
        thermal_mass=1.0,
        r_external=1.0,
        setpoint=20.0,
    )
    assert err == "duplicate_room"
    assert len(helper) == 1


def test_room_helper_select_binds_current_room():
    helper = RoomFlowHelper(
        [{CONF_ROOM_NAME: "a"}, {CONF_ROOM_NAME: "b"}, {CONF_ROOM_NAME: "c"}]
    )
    assert helper.select("b") is True
    assert helper.current_room()[CONF_ROOM_NAME] == "b"


def test_room_helper_select_missing_clears_current():
    helper = RoomFlowHelper([{CONF_ROOM_NAME: "a"}])
    helper.select("a")
    assert helper.select("unknown") is False
    assert helper.current_room() is None


def test_room_helper_update_current_modifies_in_place():
    helper = RoomFlowHelper()
    _add_kitchen(helper)
    helper.select("kitchen")

    err = helper.update_current(
        name="kitchen renamed",
        sensors=["sensor.k1", "sensor.k2"],
        thermal_mass=ROOM_SIZE_TO_THERMAL_MASS["large"],
        r_external=BUILDING_AGE_TO_R_EXTERNAL["pre_1940"],
        setpoint=23.0,
    )
    assert err is None
    room = helper.rooms[0]
    assert room[CONF_ROOM_NAME] == "kitchen renamed"
    assert room[CONF_TEMP_SENSORS] == ["sensor.k1", "sensor.k2"]
    assert room[CONF_TEMP_SENSOR] == "sensor.k1"
    assert room[CONF_THERMAL_MASS] == ROOM_SIZE_TO_THERMAL_MASS["large"]
    assert room[CONF_SETPOINT] == 23.0


def test_room_helper_update_clearing_sensors_drops_singular_key():
    helper = RoomFlowHelper()
    _add_kitchen(helper)
    helper.select("kitchen")
    helper.update_current(
        name="kitchen",
        sensors=[],
        thermal_mass=1.0,
        r_external=1.0,
        setpoint=22.0,
    )
    assert CONF_TEMP_SENSOR not in helper.rooms[0]


def test_room_helper_update_rejects_collision_with_other_room():
    helper = RoomFlowHelper(
        [{CONF_ROOM_NAME: "kitchen"}, {CONF_ROOM_NAME: "bedroom"}]
    )
    helper.select("bedroom")
    err = helper.update_current(
        name="KITCHEN",
        sensors=[],
        thermal_mass=1.0,
        r_external=1.0,
        setpoint=22.0,
    )
    assert err == "duplicate_room"
    # bedroom is untouched
    assert helper.rooms[1][CONF_ROOM_NAME] == "bedroom"


def test_room_helper_update_allows_same_name():
    helper = RoomFlowHelper(
        [{CONF_ROOM_NAME: "kitchen"}, {CONF_ROOM_NAME: "bedroom"}]
    )
    helper.select("kitchen")
    err = helper.update_current(
        name="kitchen",
        sensors=[],
        thermal_mass=1.0,
        r_external=1.0,
        setpoint=22.0,
    )
    assert err is None


def test_room_helper_update_current_without_selection_errors():
    helper = RoomFlowHelper([{CONF_ROOM_NAME: "kitchen"}])
    err = helper.update_current(
        name="x", sensors=[], thermal_mass=1.0, r_external=1.0, setpoint=22.0
    )
    assert err == "no_room_selected"


def test_room_helper_remove_by_name_drops_room_and_clears_current():
    helper = RoomFlowHelper(
        [{CONF_ROOM_NAME: "a"}, {CONF_ROOM_NAME: "b"}]
    )
    helper.select("a")
    assert helper.remove_by_name("a") is True
    assert helper.names() == ["b"]
    assert helper.current_room() is None


def test_room_helper_remove_unknown_is_noop():
    helper = RoomFlowHelper([{CONF_ROOM_NAME: "a"}])
    assert helper.remove_by_name("missing") is False
    assert helper.names() == ["a"]


# ── WindowFlowHelper ──────────────────────────────────────────────────────


def test_window_helper_initialises_empty_window_list_on_room_without_one():
    room = {CONF_ROOM_NAME: "kitchen"}
    helper = WindowFlowHelper(room)
    assert len(helper) == 0
    assert room[CONF_WINDOWS] == []  # initialised by the helper


def test_window_helper_preserves_existing_windows():
    existing = [{CONF_WINDOW_AREA: 1.0, CONF_WINDOW_ORIENTATION: 0.0, CONF_WINDOW_TILT: 90.0}]
    room = {CONF_ROOM_NAME: "kitchen", CONF_WINDOWS: existing}
    helper = WindowFlowHelper(room)
    assert len(helper) == 1
    # The helper holds the same list by reference, not a copy.
    helper.windows.append({"_": "marker"})
    assert room[CONF_WINDOWS][-1] == {"_": "marker"}


def test_window_helper_add_from_compass_converts_to_degrees():
    room = {CONF_ROOM_NAME: "kitchen"}
    helper = WindowFlowHelper(room)
    helper.add_from_compass(area=2.5, compass="SW", tilt=80.0)
    assert helper.windows == [
        {
            CONF_WINDOW_AREA: 2.5,
            CONF_WINDOW_ORIENTATION: COMPASS_TO_DEGREES["SW"],
            CONF_WINDOW_TILT: 80.0,
        }
    ]


def test_window_helper_add_unknown_compass_raises():
    helper = WindowFlowHelper({CONF_ROOM_NAME: "kitchen"})
    with pytest.raises(KeyError):
        helper.add_from_compass(area=1.0, compass="XYZ", tilt=90.0)


def test_window_helper_remove_in_range_pops_and_out_of_range_is_noop():
    room = {
        CONF_ROOM_NAME: "kitchen",
        CONF_WINDOWS: [
            {CONF_WINDOW_AREA: 1.0, CONF_WINDOW_ORIENTATION: 0.0, CONF_WINDOW_TILT: 90.0},
            {CONF_WINDOW_AREA: 2.0, CONF_WINDOW_ORIENTATION: 90.0, CONF_WINDOW_TILT: 90.0},
        ],
    }
    helper = WindowFlowHelper(room)
    assert helper.remove(0) is True
    assert len(helper) == 1
    assert helper.remove(99) is False
    assert helper.remove(-1) is False


def test_window_helper_display_options_uses_room_name_and_indexes():
    helper = WindowFlowHelper(
        {
            CONF_ROOM_NAME: "kitchen",
            CONF_WINDOWS: [
                {CONF_WINDOW_AREA: 1.0, CONF_WINDOW_ORIENTATION: 0.0, CONF_WINDOW_TILT: 90.0},
                {CONF_WINDOW_AREA: 2.0, CONF_WINDOW_ORIENTATION: 180.0, CONF_WINDOW_TILT: 90.0},
            ],
        }
    )
    options = helper.display_options("kitchen")
    assert list(options) == ["0", "1"]
    assert "Window 1" in options["0"]
    assert "Window 2" in options["1"]
    assert "facing N" in options["0"]
    assert "facing S" in options["1"]
