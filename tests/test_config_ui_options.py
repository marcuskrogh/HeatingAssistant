"""Tests for configuration UI text and options-flow wiring."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.heating_assistant.config_flow import (
    _build_period_dict,
    _is_valid_time_string,
)
from custom_components.heating_assistant.const import DEFAULT_SETPOINT


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_FLOW_PATH = REPO_ROOT / "custom_components" / "heating_assistant" / "config_flow.py"
OPTIONS_FLOW_HELPERS_PATH = (
    REPO_ROOT / "custom_components" / "heating_assistant" / "_options_flow.py"
)
STRINGS_PATH = REPO_ROOT / "custom_components" / "heating_assistant" / "strings.json"
EN_TRANSLATION_PATH = (
    REPO_ROOT / "custom_components" / "heating_assistant" / "translations" / "en.json"
)


def test_default_setpoint_is_22() -> None:
    """Internal default setpoint should be fixed at 22°C."""
    assert DEFAULT_SETPOINT == 22.0


def test_options_flow_source_has_expected_room_and_solver_updates() -> None:
    """Options flow should expose simplified room UI (solver backend removed from UI).

    Room/window CRUD lives in ``_options_flow.py`` after the U4 refactor,
    so a few of these checks span both files.
    """
    config_flow_source = CONFIG_FLOW_PATH.read_text(encoding="utf-8")
    helpers_source = OPTIONS_FLOW_HELPERS_PATH.read_text(encoding="utf-8")
    combined = config_flow_source + "\n" + helpers_source

    # OptionsFlow-side wiring still lives in config_flow.py.
    assert "manage_room_windows" in config_flow_source
    # Solver backend is always QP; the dropdown is no longer shown to users.
    assert 'vol.In(["slsqp", "ipopt"])' not in config_flow_source
    assert "DEFAULT_ROOM_SETPOINT = 22.0" in config_flow_source
    # The default setpoint is passed into the helper's add() call.
    assert "setpoint=DEFAULT_ROOM_SETPOINT" in config_flow_source

    # Shared constants/symbols can live in either file.
    assert "CONF_TEMP_SENSORS" in combined
    assert "ROOM_SIZE_TO_THERMAL_MASS" in combined
    assert "BUILDING_AGE_TO_R_EXTERNAL" in combined
    # The room dict template (now built inside RoomFlowHelper.add) wires the
    # setpoint into CONF_SETPOINT.
    assert "CONF_SETPOINT: setpoint" in helpers_source


def test_strings_and_english_translation_are_in_sync_for_new_ui_labels() -> None:
    """strings.json and en.json should both contain the updated labels."""
    strings = json.loads(STRINGS_PATH.read_text(encoding="utf-8"))
    en = json.loads(EN_TRANSLATION_PATH.read_text(encoding="utf-8"))
    assert strings == en

    global_settings = strings["options"]["step"]["global_settings"]["data"]
    assert "optional when weather forecast entity is set" in global_settings["outdoor_temp_entity"]
    assert "if not set, outdoor sensor is required" in global_settings["weather_entity"]
    assert "IPOPT or SLSQP" in global_settings["mpc_solver"]
    assert global_settings["sigma_w"].startswith("SDE ")
    assert global_settings["sigma_b"].startswith("SDE ")
    assert "window_open_debounce" in global_settings
    assert "window_open_close_settle" in global_settings
    assert "window_open_q_inflation" in global_settings

    add_room = strings["options"]["step"]["add_room"]["data"]
    room_detail = strings["options"]["step"]["room_detail"]["data"]
    manage_rooms_menu = strings["options"]["step"]["manage_rooms"]["menu_options"]

    assert "temp_sensors" in add_room
    assert "window_sensors" in add_room
    assert "comfort_corridor_low" in add_room
    assert "comfort_corridor_high" in add_room
    assert "room_size" in add_room
    assert "building_age" in add_room
    assert "setpoint" not in add_room

    assert "temp_sensors" in room_detail
    assert "window_sensors" in room_detail
    assert "comfort_corridor_low" in room_detail
    assert "comfort_corridor_high" in room_detail
    assert "room_size" in room_detail
    assert "building_age" in room_detail
    assert "setpoint" not in room_detail

    assert "manage_room_windows" in manage_rooms_menu


def test_period_time_validation_helper_accepts_hh_mm_and_rejects_invalid() -> None:
    assert _is_valid_time_string("06:30") is True
    assert _is_valid_time_string("23:59") is True
    assert _is_valid_time_string("24:00") is False
    assert _is_valid_time_string("not-a-time") is False


def test_build_period_dict_omits_defaults_and_keeps_overrides() -> None:
    room_setpoint = 22.0
    room_comfort_offset = 2.0
    period = _build_period_dict(
        {
            "name": "morning",
            "mode": "comfort",
            "start": "06:00",
            "end": "08:00",
            "days": [],
            "setpoint": 23.0,
            "comfort_offset": 1.5,
            "tracking_weight": 2.0,
            "energy_weight": 0.7,
            "frost_protection": 12.0,
        },
        room_setpoint=room_setpoint,
        room_comfort_offset=room_comfort_offset,
    )
    assert period["setpoint"] == 23.0
    assert period["comfort_offset"] == 1.5
    assert period["tracking_weight"] == 2.0
    assert period["energy_weight"] == 0.7
    assert "days" not in period

    period_defaults = _build_period_dict(
        {
            "name": "defaultish",
            "mode": "comfort",
            "start": "08:00",
            "end": "09:00",
            "days": [],
            "setpoint": room_setpoint,
            "comfort_offset": room_comfort_offset,
            "tracking_weight": 1.0,
            "energy_weight": 1.0,
            "frost_protection": 12.0,
        },
        room_setpoint=room_setpoint,
        room_comfort_offset=room_comfort_offset,
    )
    assert "setpoint" not in period_defaults
    assert "comfort_offset" not in period_defaults
    assert "tracking_weight" not in period_defaults
    assert "energy_weight" not in period_defaults
