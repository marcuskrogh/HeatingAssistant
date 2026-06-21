"""Tests for configuration UI text and options-flow wiring."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.heating_assistant.config_flow import (
    _build_period_dict,
    _flatten_sections,
    _is_valid_time_string,
    _normalise_time_string,
)
from custom_components.heating_assistant._options_flow import (
    BUILDING_AGE_TO_R_EXTERNAL,
    ROOM_SIZE_TO_THERMAL_MASS,
    RoomFlowHelper,
)
from custom_components.heating_assistant.const import (
    CONF_FACADE_COLOUR,
    CONF_FACADE_SOLAR_SHARE,
    CONF_FLOOR_TYPE,
    CONF_SKY_RADIATIVE_UA,
    CONF_THERMAL_BRIDGE_PSI_L,
    DEFAULT_SETPOINT,
)


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
    """strings.json and en.json should both contain the modernised labels.

    The modernised UI moves jargon-heavy explanations from labels into
    ``data_description`` helper text below each field. All schemas are flat
    (no ``section()`` wrappers) — sections proved fragile across Home
    Assistant versions when mixed with top-level fields, so we keep the
    visual grouping via field ordering + description-prefixed "Advanced — "
    notes.
    """
    strings = json.loads(STRINGS_PATH.read_text(encoding="utf-8"))
    en = json.loads(EN_TRANSLATION_PATH.read_text(encoding="utf-8"))
    assert strings == en

    # ── Global settings: every field has a friendly label and helper text. ──
    global_step = strings["options"]["step"]["global_settings"]
    global_data = global_step["data"]
    global_desc = global_step["data_description"]
    # Step uses a flat schema — sections must NOT be present, otherwise HA
    # would try to render a section wrapper that no longer exists in code.
    assert "sections" not in global_step

    for key in (
        "outdoor_temp_entity",
        "weather_entity",
        "update_interval",
        "sigma_w",
        "sigma_v",
        "sigma_b",
        "window_open_debounce",
        "window_open_close_settle",
        "window_open_q_inflation",
    ):
        assert key in global_data
        assert key in global_desc
        assert global_data[key]  # non-empty label
        assert global_desc[key]  # non-empty description

    # Labels stay friendly — no greek / "SDE" jargon.
    for key in ("sigma_w", "sigma_v", "sigma_b"):
        assert "σ" not in global_data[key]
        assert not global_data[key].startswith("SDE")

    assert "optional" in global_desc["outdoor_temp_entity"].lower()
    assert "outdoor temperature sensor" in global_desc["weather_entity"].lower()

    # ── MPC tuning is also flat. ──
    mpc_step = strings["options"]["step"]["mpc_tuning"]
    assert "sections" not in mpc_step
    mpc_data = mpc_step["data"]
    for key in (
        "tracking_weight",
        "energy_weight",
        "horizon",
        "smoothing_weight",
        "soft_constraint_weight",
        "terminal_weight",
    ):
        assert key in mpc_data
        assert key in mpc_step["data_description"]

    # ── Rooms: capabilities preserved, advanced envelope fields at top level. ──
    add_room = strings["options"]["step"]["add_room"]
    add_room_data = add_room["data"]
    add_room_desc = add_room["data_description"]
    assert "sections" not in add_room
    room_detail = strings["options"]["step"]["room_detail"]
    room_detail_data = room_detail["data"]
    assert "sections" not in room_detail
    manage_rooms_menu = strings["options"]["step"]["manage_rooms"]["menu_options"]

    for key in (
        "temp_sensors",
        "window_sensors",
        "room_size",
        "building_age",
        "envelope_tightness",
        "comfort_offset",
        "name",
        "floor_type",
        "facade_colour",
        "facade_solar_share",
        "thermal_bridge_psi_l",
        "sky_radiative_ua",
    ):
        assert key in add_room_data
        assert key in add_room_desc
        assert key in room_detail_data
    assert "setpoint" not in add_room_data
    assert "setpoint" not in room_detail_data
    # Comfort band replaces the legacy comfort_corridor_{low,high} keys.
    assert "comfort_corridor_low" not in add_room_data
    assert "comfort_corridor_high" not in add_room_data

    assert "manage_room_windows" in manage_rooms_menu

    # ── Schedule periods use the time picker (no HH:MM in labels). ──
    period = strings["options"]["step"]["add_period"]
    assert "sections" not in period
    period_data = period["data"]
    for key in ("start", "end"):
        assert key in period_data
        # The TimeSelector renders a native time picker — no need to spell
        # the format out in the label.
        assert "HH:MM" not in period_data[key]
    for key in ("tracking_weight", "energy_weight", "frost_protection"):
        assert key in period_data

    # ── Selector translation keys exist for dropdowns. ──
    selector = strings["selector"]
    for key in (
        "room_size",
        "building_age",
        "envelope_tightness",
        "orientation",
        "source_type",
        "hvac_mode",
        "schedule_mode",
        "floor_type",
        "facade_colour",
    ):
        assert key in selector
        assert "options" in selector[key]
        assert selector[key]["options"]  # non-empty

    # ── Reconfigure step is present in the config flow and also flat. ──
    reconfigure_step = strings["config"]["step"]["reconfigure"]
    assert "sections" not in reconfigure_step
    for key in (
        "latitude",
        "longitude",
        "outdoor_temp_entity",
        "weather_entity",
        "update_interval",
    ):
        assert key in reconfigure_step["data"]
        assert key in reconfigure_step["data_description"]

    # The original user-step (initial setup) is also flat.
    user_step = strings["config"]["step"]["user"]
    assert "sections" not in user_step
    for key in (
        "latitude",
        "longitude",
        "outdoor_temp_entity",
        "weather_entity",
        "update_interval",
    ):
        assert key in user_step["data"]
        assert key in user_step["data_description"]


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


def test_build_period_dict_normalises_time_selector_output() -> None:
    """``TimeSelector`` returns ``HH:MM:SS``; stored values stay HH:MM."""
    period = _build_period_dict(
        {
            "name": "evening",
            "mode": "comfort",
            "start": "18:30:00",
            "end": "22:00:45",
            "days": [],
            "setpoint": 22.0,
            "comfort_offset": 2.0,
            "tracking_weight": 1.0,
            "energy_weight": 1.0,
            "frost_protection": 12.0,
        },
        room_setpoint=22.0,
        room_comfort_offset=2.0,
    )
    assert period["start"] == "18:30"
    assert period["end"] == "22:00"


def test_normalise_time_string_passthrough_and_trim() -> None:
    assert _normalise_time_string("06:00") == "06:00"
    assert _normalise_time_string("06:00:00") == "06:00"
    assert _normalise_time_string("23:59:59") == "23:59"


def test_flatten_sections_lifts_nested_dicts_top_level_wins() -> None:
    """Sectioned form input is flattened, with top-level keys taking precedence."""
    flat = _flatten_sections(
        {
            "outdoor_temp_entity": "sensor.outside",
            "sensors": {
                "weather_entity": "weather.home",
                # Should not clobber a top-level value with the same name.
                "outdoor_temp_entity": "sensor.IGNORED",
            },
            "timing": {"update_interval": 900},
        },
        ("sensors", "timing"),
    )
    assert flat == {
        "outdoor_temp_entity": "sensor.outside",
        "weather_entity": "weather.home",
        "update_interval": 900,
    }


def test_flatten_sections_handles_none() -> None:
    assert _flatten_sections(None, ("sensors",)) == {}


def test_room_helper_stores_advanced_envelope_only_when_non_default() -> None:
    """Floor/facade/bridge knobs should only appear in the room dict when set."""
    helper = RoomFlowHelper()
    err = helper.add(
        name="bedroom",
        sensors=["sensor.bedroom"],
        thermal_mass=ROOM_SIZE_TO_THERMAL_MASS["medium"],
        r_external=BUILDING_AGE_TO_R_EXTERNAL["2000_plus"],
        setpoint=22.0,
        floor_type="ufh",  # non-default → stored
        facade_colour="medium",  # default → stripped
        facade_solar_share=0.0,  # default → stripped
        thermal_bridge_psi_l=2.5,  # non-default → stored
        sky_radiative_ua=0.0,  # default → stripped
    )
    assert err is None
    room = helper.rooms[0]
    assert room[CONF_FLOOR_TYPE] == "ufh"
    assert room[CONF_THERMAL_BRIDGE_PSI_L] == 2.5
    assert CONF_FACADE_COLOUR not in room
    assert CONF_FACADE_SOLAR_SHARE not in room
    assert CONF_SKY_RADIATIVE_UA not in room


def test_room_helper_update_strips_advanced_when_user_chooses_defaults() -> None:
    """Editing a room back to defaults must remove previously-stored overrides."""
    helper = RoomFlowHelper()
    helper.add(
        name="bedroom",
        sensors=["sensor.bedroom"],
        thermal_mass=ROOM_SIZE_TO_THERMAL_MASS["medium"],
        r_external=BUILDING_AGE_TO_R_EXTERNAL["2000_plus"],
        setpoint=22.0,
        floor_type="ufh",
        thermal_bridge_psi_l=2.5,
    )
    assert helper.select("bedroom") is True

    helper.update_current(
        name="bedroom",
        sensors=["sensor.bedroom"],
        thermal_mass=ROOM_SIZE_TO_THERMAL_MASS["medium"],
        r_external=BUILDING_AGE_TO_R_EXTERNAL["2000_plus"],
        setpoint=22.0,
        floor_type="none",  # the default → strip
        thermal_bridge_psi_l=0.0,  # the default → strip
    )
    room = helper.rooms[0]
    assert CONF_FLOOR_TYPE not in room
    assert CONF_THERMAL_BRIDGE_PSI_L not in room


def test_sysid_index_cards_show_core_kpis_and_dismissible_warnings() -> None:
    """System identification index cards must show R²/RMSE/Estimated and dismissible warnings."""
    source = (
        REPO_ROOT / "custom_components" / "heating_assistant" / "www" / "js" / "pages"
        / "system-identification.js"
    ).read_text(encoding="utf-8")
    assert "identificationStat('R²'" in source
    assert "identificationStat('RMSE'" in source
    assert "identificationStat('Estimated'" in source
    assert "card_warnings" in source
    assert "data-dismiss-warning" in source
    assert "DISMISSED_WARNINGS_KEY" in source
    assert "identificationStat('MAE'" not in source
    assert "identificationStat('Confidence'" not in source
