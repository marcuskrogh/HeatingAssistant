"""Tests for configuration UI text and options-flow wiring."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from custom_components.heating_assistant.config_flow import (
    _build_period_dict,
    _flatten_sections,
    _initial_entry_data,
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
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_SKY_RADIATIVE_UA,
    CONF_THERMAL_BRIDGE_PSI_L,
    CONF_UPDATE_INTERVAL,
    DEFAULT_SETPOINT,
    DEFAULT_UPDATE_INTERVAL,
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


def test_initial_entry_data_sets_location_and_default_update_interval() -> None:
    """Location-only setup should persist coords and default control interval."""
    data = _initial_entry_data(
        {CONF_LATITUDE: 55.6761, CONF_LONGITUDE: 12.5683},
    )
    assert data == {
        CONF_LATITUDE: 55.6761,
        CONF_LONGITUDE: 12.5683,
        CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL,
    }


def test_default_setpoint_is_22() -> None:
    """Internal default setpoint should be fixed at 22°C."""
    assert DEFAULT_SETPOINT == 22.0


def test_config_flow_has_no_options_flow() -> None:
    """HA integration config must not expose the legacy options flow.

    All configuration beyond the site location (rooms, sensors, heat sources,
    schedules) is managed from the Heating Assistant panel, not from the native
    HA integration configure/reconfigure dialogs.
    """
    config_flow_source = CONFIG_FLOW_PATH.read_text(encoding="utf-8")
    helpers_source = OPTIONS_FLOW_HELPERS_PATH.read_text(encoding="utf-8")

    # No options flow class in config_flow.py.
    assert "HeatingAssistantOptionsFlow" not in config_flow_source
    assert "async_get_options_flow" not in config_flow_source
    # Room/window/heater management stays in the helper module only.
    assert "manage_room_windows" not in config_flow_source
    # Solver backend is always QP; the dropdown must not appear.
    assert 'vol.In(["slsqp", "ipopt"])' not in config_flow_source
    # Room CRUD helpers remain in _options_flow.py for the panel to use.
    assert "CONF_SETPOINT: setpoint" in helpers_source
    assert "ROOM_SIZE_TO_THERMAL_MASS" in helpers_source
    assert "BUILDING_AGE_TO_R_EXTERNAL" in helpers_source


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
    ):
        assert key in global_data
        assert key in global_desc
        assert global_data[key]  # non-empty label
        assert global_desc[key]  # non-empty description

    # Estimation noise (sigma_w/v/b) is configured from the industrial panel, not
    # the HA options-flow global step.
    for key in ("sigma_w", "sigma_v", "sigma_b"):
        assert key not in global_data

    # Labels stay friendly — no greek / "SDE" jargon in global settings.
    for key in ("outdoor_temp_entity", "weather_entity"):
        assert "σ" not in global_data[key]

    assert "optional" in global_desc["outdoor_temp_entity"].lower()
    assert "outdoor temperature sensor" in global_desc["weather_entity"].lower()

    # ── MPC tuning is also flat. ──
    mpc_step = strings["options"]["step"]["mpc_tuning"]
    assert "sections" not in mpc_step
    mpc_data = mpc_step["data"]
    for key in (
        "tracking_weight",
        "energy_weight",
        "mpc_mode",
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

    # Windows and heat sources are configured from the industrial panel, not the
    # manage-rooms submenu.
    assert "manage_room_windows" not in manage_rooms_menu
    assert "manage_room_heaters" not in manage_rooms_menu

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

    # ── Reconfigure step is present, flat, and location-only. ──
    reconfigure_step = strings["config"]["step"]["reconfigure"]
    assert "sections" not in reconfigure_step
    for key in ("latitude", "longitude"):
        assert key in reconfigure_step["data"]
        assert key in reconfigure_step["data_description"]
    # Everything else is managed from the panel / options flow.
    for key in ("outdoor_temp_entity", "weather_entity", "update_interval"):
        assert key not in reconfigure_step["data"]

    # The original user-step (initial setup) is location-only.
    user_step = strings["config"]["step"]["user"]
    assert "sections" not in user_step
    for key in ("latitude", "longitude"):
        assert key in user_step["data"]
        assert key in user_step["data_description"]
    assert "outdoor_temp_entity" not in user_step["data"]
    assert "weather_entity" not in user_step["data"]
    assert "update_interval" not in user_step["data"]


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


def test_tuning_page_splits_live_and_restart_parameters() -> None:
    """Controller tuning UI must group live vs restart-required MPC parameters."""
    source = (
        REPO_ROOT / "custom_components" / "heating_assistant" / "www" / "js" / "pages"
        / "tuning-controller.js"
    ).read_text(encoding="utf-8")
    assert "LIVE_PARAM_DEFS" in source
    assert "RESTART_PARAM_DEFS" in source
    assert "MODE_OPTIONS" in source
    assert "mpc_mode" in source
    assert "ipopt_available" in source
    assert "nonlinearOption.disabled = !available" in source
    assert "higher compute cost" in source
    assert "Live tuning" in source
    assert "Restart required" in source
    assert "hasPendingRestartChanges" in source
    assert "tuning-pending-banner--restart" in source
    assert "'horizon'" in source
    assert "'update_interval'" in source
    assert "'tracking_weight'" in source


def test_sysid_pending_banner_hides_when_empty() -> None:
    """Sysid pending banner must respect [hidden] so empty orange box is not shown."""
    css = (
        REPO_ROOT / "custom_components" / "heating_assistant" / "www" / "css" / "pages"
        / "tuning.css"
    ).read_text(encoding="utf-8")
    assert ".tuning-pending-banner[hidden]" in css
    assert "display: none !important" in css

    source = (
        REPO_ROOT / "custom_components" / "heating_assistant" / "www" / "js"
        / "identification" / "sysid-detail.js"
    ).read_text(encoding="utf-8")
    assert "pendingBanner.hidden = !pending" in source
    assert "tuning-pending-banner--actions" in source


def test_panel_js_files_use_ascii_quotes_only() -> None:
    """Curly/smart quotes break ES module parsing in the browser (SyntaxError)."""
    js_root = REPO_ROOT / "custom_components" / "heating_assistant" / "www" / "js"
    bad_chars = {"\u2018", "\u2019", "\u201c", "\u201d"}
    offenders: list[str] = []
    for path in sorted(js_root.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for i, ch in enumerate(text):
            if ch in bad_chars:
                line = text.count("\n", 0, i) + 1
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line}")
                break
    assert not offenders, f"Non-ASCII quotes in panel JS: {offenders}"


def _panel_cache_bust_token() -> str:
    panel_setup = (
        REPO_ROOT / "custom_components" / "heating_assistant" / "panel_setup.py"
    ).read_text(encoding="utf-8")
    match = re.search(r"industrial-dashboard\.js\?v=(\d+)", panel_setup)
    assert match, "Panel js_url cache-bust token not found in panel_setup.py"
    return match.group(1)


def test_panel_cache_bust_token_in_sync() -> None:
    """js_url ?v=, dashboard fallback, and panel-cache-bust.js must match."""
    token = _panel_cache_bust_token()
    cache_bust = (
        REPO_ROOT / "custom_components" / "heating_assistant" / "www" / "js"
        / "panel-cache-bust.js"
    ).read_text(encoding="utf-8")
    dashboard = (
        REPO_ROOT / "custom_components" / "heating_assistant" / "www"
        / "industrial-dashboard.js"
    ).read_text(encoding="utf-8")
    assert f"PANEL_CACHE_BUST = '{token}'" in cache_bust
    assert f"return '{token}'" in dashboard


def test_panel_lifecycle_handles_sidebar_navigation() -> None:
    """Panel must boot on connect and fully tear down on disconnect."""
    dashboard = (
        REPO_ROOT / "custom_components" / "heating_assistant" / "www"
        / "industrial-dashboard.js"
    ).read_text(encoding="utf-8")
    required = [
        "setProperties(props)",
        "_bootGeneration",
        "_booting",
        "connectedCallback()",
        "disconnectedCallback()",
        "generation !== this._bootGeneration",
        "!this.isConnected) return",
        "this._router = null",
        "_applyHassState(hass)",
        # Self-healing watchdog: a stalled boot must never leave the panel stuck
        # on "INITIALIZING…" requiring a manual force reload.
        "_startBootWatchdog",
        "_clearBootWatchdog",
        "BOOT_WATCHDOG_MS",
        "_navigatePanel",
        "navigateTo",
        "_ensureBooted",
        "_resetStaleBoot",
        "_scheduleHashCleanup",
        "_stripLeakedPanelHash",
        "_installPanelHashGuard",
        "location-changed",
        "(() => {",
        "customElements.get('ha-industrial-panel')",
    ]
    forbidden = [
        "_initialized",
        "_resumePanel()",
    ]
    missing = [snippet for snippet in required if snippet not in dashboard]
    present = [snippet for snippet in forbidden if snippet in dashboard]
    assert not missing, f"Panel lifecycle fix incomplete, missing: {missing}"
    assert not present, f"Panel lifecycle regressions, found: {present}"


def test_panel_watchdog_recovers_stalled_boot() -> None:
    """A boot that stalls before a router exists must self-recover (no reload)."""
    harness = REPO_ROOT / "tests" / "panel_watchdog.harness.mjs"
    result = subprocess.run(
        ["node", str(harness)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_panel_hash_guard_strips_leaked_hashes() -> None:
    """Panel hashes must not survive HA sidebar pushState navigation."""
    harness = REPO_ROOT / "tests" / "panel_hash.harness.mjs"
    result = subprocess.run(
        ["node", str(harness)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_panel_lifecycle_survives_ha_sidebar_remount() -> None:
    """HA setProperties-before-connect and sidebar remount must boot and route pages."""
    harness = REPO_ROOT / "tests" / "panel_lifecycle.harness.mjs"
    result = subprocess.run(
        ["node", str(harness)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_panel_global_scope_survives_cross_panel_navigation() -> None:
    """Entry script must not throw when another panel already declared BASE_PATH globally."""
    harness = REPO_ROOT / "tests" / "panel_global_scope.harness.mjs"
    result = subprocess.run(
        ["node", str(harness)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_panel_schedules_modules_load_without_shadowing() -> None:
    """Schedule page modules must not import and re-export the same binding."""
    harness = REPO_ROOT / "tests" / "panel_schedules_module.harness.mjs"
    result = subprocess.run(
        ["node", str(harness)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_panel_schedules_preview_and_inactive() -> None:
    """Period preview, NOW/NEXT across types, and inactive bucketing (SWD-45)."""
    harness = REPO_ROOT / "tests" / "panel_schedules_preview.harness.mjs"
    result = subprocess.run(
        ["node", str(harness)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_panel_schedule_resolver_falls_back_to_config_state() -> None:
    """Shared schedule resolver must use config-entity state when WS is empty."""
    harness = REPO_ROOT / "tests" / "panel_schedule_resolver.harness.mjs"
    result = subprocess.run(
        ["node", str(harness)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_panel_stylesheets_linked_explicitly() -> None:
    """Page CSS must be linked in the shadow root; @import is unreliable on mobile."""
    harness = REPO_ROOT / "tests" / "panel_stylesheets.harness.mjs"
    result = subprocess.run(
        ["node", str(harness)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_panel_climate_power_toggle_regression() -> None:
    """Climate-card power toggles must reconcile optimistic state; backend must push listeners."""
    harness = REPO_ROOT / "tests" / "panel_climate_power.harness.mjs"
    result = subprocess.run(
        ["node", str(harness)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_panel_state_sync_detects_controller_config_attrs() -> None:
    """Panel hass sync must notice room_active/room_enabled attribute changes."""
    harness = REPO_ROOT / "tests" / "panel_state_sync.harness.mjs"
    result = subprocess.run(
        ["node", str(harness)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_panel_relative_imports_use_cache_bust_suffix() -> None:
    """Transitive ES module imports must carry ?v= or browsers serve stale modules."""
    token = _panel_cache_bust_token()
    js_root = REPO_ROOT / "custom_components" / "heating_assistant" / "www" / "js"
    offenders: list[str] = []
    for path in sorted(js_root.rglob("*.js")):
        if "vendor" in path.parts or path.name in {"panel-cache-bust.js"}:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"from '(\.\.?/[^']+\.js)(?:\?v=\d+)?'", text):
            full = match.group(0)
            if f"?v={token}" not in full:
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line}:{full}")
    assert not offenders, f"Unversioned panel imports: {offenders[:5]}"


def test_kpi_engine_exports_room_time_in_range() -> None:
    """room-detail imports roomTimeInRangePct — kpi-engine must export it."""
    kpi_engine = (
        REPO_ROOT / "custom_components" / "heating_assistant" / "www" / "js"
        / "kpi-engine.js"
    ).read_text(encoding="utf-8")
    room_detail = (
        REPO_ROOT / "custom_components" / "heating_assistant" / "www" / "js" / "pages"
        / "room-detail.js"
    ).read_text(encoding="utf-8")
    token = _panel_cache_bust_token()
    assert "export function roomTimeInRangePct" in kpi_engine
    assert f"from '../kpi-engine.js?v={token}'" in room_detail
    assert "roomTimeInRangePct" in room_detail


def test_sysid_index_cards_show_core_kpis_and_dismissible_warnings() -> None:
    """System identification index cards must show R²/RMSE/Estimated and dismissible warnings."""
    token = _panel_cache_bust_token()
    source = (
        REPO_ROOT / "custom_components" / "heating_assistant" / "www" / "js"
        / "identification" / "sysid-index.js"
    ).read_text(encoding="utf-8")
    assert "identificationStat('R²'" in source
    assert "identificationStat('RMSE'" in source
    assert "identificationStat('Estimated'" in source
    assert "card_warnings" in source
    assert "data-dismiss-warning" in source
    # Missing these imports throws ReferenceError while building the first room
    # card, which leaves the identification index empty.
    assert f"from '../utils.js?v={token}'" in source
    assert "formatNumber" in source
    assert "modelFitLabel" in source
    shared = (
        REPO_ROOT / "custom_components" / "heating_assistant" / "www" / "js"
        / "identification" / "sysid-shared.js"
    ).read_text(encoding="utf-8")
    assert "DISMISSED_WARNINGS_KEY" in shared
    assert "loadDismissedWarnings" in source
    assert "identificationStat('MAE'" not in source
    assert "identificationStat('Confidence'" not in source
