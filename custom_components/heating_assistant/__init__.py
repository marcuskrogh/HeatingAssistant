"""
Heating Assistant integration – entry-point.

Set-up flow
-----------
1. ``async_setup``        – register YAML-sourced room/source configuration.
2. ``async_setup_entry``  – create the coordinator and set up platforms.
3. ``async_unload_entry`` – clean up on removal.

YAML configuration example
---------------------------
``configuration.yaml``::

    heating_assistant:
      rooms:
        - name: living_room
          thermal_mass: 8000000     # J/K
          r_external: 0.04          # K/W
          setpoint: 21.0
          temp_sensor: sensor.living_room_temperature
          # Multiple sensors can be used for averaging:
          # temp_sensors:
          #   - sensor.living_room_temp_north
          #   - sensor.living_room_temp_south
          connections:
            - room: kitchen
              r_value: 0.2
          windows:
            - area: 3.0             # m²
              orientation: 180      # South
              tilt: 90
        - name: kitchen
          thermal_mass: 4000000
          r_external: 0.06
          setpoint: 20.0
          connections:
            - room: living_room
              r_value: 0.2

      heat_sources:
        - name: living_room_heater
          type: electric_heater
          room: living_room
          max_power: 2000           # W
          heater_entity: switch.living_room_heater
        - name: heat_pump
          type: heat_pump
          room: living_room
          max_power: 5000           # W thermal
          cop_rated: 3.5
          cop_temp_ref: 7.0
          min_power: 1000           # W thermal (minimum operating power)
          heater_entity: climate.heat_pump

      outdoor_temp_entity: sensor.outdoor_temperature
      weather_entity: weather.forecast_home       # optional: weather forecast
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SERVICE_RELOAD
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.reload import async_integration_yaml_config
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.storage import Store

from .const import (
    ALL_SOURCE_TYPES,
    CONF_COMFORT_OFFSET,
    CONF_CONNECTIONS,
    CONF_CONNECTED_ROOM,
    CONF_ENERGY_WEIGHT,
    CONF_ENERGY_PRICE_WEIGHT,
    CONF_HEAT_SOURCES,
    CONF_HORIZON,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_C_SLAB_FRACTION,
    CONF_FACADE_ABSORPTANCE,
    CONF_FACADE_COLOUR,
    CONF_FACADE_SOLAR_SHARE,
    CONF_FLOOR_TYPE,
    CONF_INFILTRATION_FRACTION,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_R_SA,
    CONF_R_SG,
    CONF_SKY_RADIATIVE_UA,
    CONF_SOFT_CONSTRAINT_WEIGHT,
    CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT,
    CONF_THERMAL_BRIDGE_PSI_L,
    CONF_TRACKING_WEIGHT,
    CONF_WEATHER_ENTITY,
    CONF_SOLAR_RADIATION_ENTITY,
    CONF_SOLAR_EXPOSURE,
    CONF_SOLAR_FACING,
    DEFAULT_SOLAR_EXPOSURE,
    DEFAULT_SOLAR_FACING,
    SOLAR_EXPOSURE_TO_APERTURE,
    CONF_R_EXTERNAL,
    CONF_R_VALUE,
    CONF_ROOM_NAME,
    CONF_ROOMS,
    CONF_SCHEDULE,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_END,
    CONF_SCHEDULE_FROST_PROTECTION,
    CONF_SCHEDULE_MODE,
    CONF_SCHEDULE_NAME,
    CONF_SCHEDULE_SETPOINT,
    CONF_SCHEDULE_START,
    CONF_SCHEDULE_COMFORT_OFFSET,
    CONF_SCHEDULE_TRACKING_WEIGHT,
    CONF_SCHEDULE_ENERGY_WEIGHT,
    CONF_PERSISTED_SCHEDULES,
    CONF_SETPOINT,
    CONF_SIGMA_B,
    CONF_SIGMA_V,
    CONF_SIGMA_W,
    CONF_IDENTIFICATION_HORIZON_HOURS,
    CONF_SMOOTHING_WEIGHT,
    CONF_SOURCE_COOLING_COP,
    CONF_SOURCE_COOLING_EFFICIENCY,
    CONF_SOURCE_COP_RATED,
    CONF_SOURCE_COP_TEMP_REF,
    CONF_SOURCE_EFFICIENCY,
    CONF_SOURCE_HEATER_ENTITY,
    CONF_SOURCE_HEATING_EFFICIENCY,
    CONF_SOURCE_MAX_POWER,
    CONF_SOURCE_MAX_TEMP_OFFSET,
    CONF_SOURCE_MIN_POWER,
    CONF_SOURCE_NAME,
    CONF_SOURCE_ROOM,
    CONF_SOURCE_TURN_OFF_DEADBAND,
    CONF_SOURCE_EMITTER_TIME_CONSTANT,
    CONF_SOURCE_TYPE,
    CONF_TEMP_SENSOR,
    CONF_TEMP_SENSORS,
    CONF_TERMINAL_WEIGHT,
    CONF_THERMAL_MASS,
    CONF_UPDATE_INTERVAL,
    CONF_WINDOWS,
    CONF_WINDOW_AREA,
    CONF_WINDOW_OPEN_CLOSE_SETTLE,
    CONF_WINDOW_OPEN_DEBOUNCE,
    CONF_WINDOW_OPEN_Q_INFLATION,
    CONF_WINDOW_ORIENTATION,
    CONF_WINDOW_SENSORS,
    CONF_WINDOW_TILT,
    DEFAULT_COMFORT_OFFSET,
    DEFAULT_COOLING_COP,
    DEFAULT_COOLING_EFFICIENCY,
    DEFAULT_COP_RATED,
    DEFAULT_COP_TEMP_REF,
    DEFAULT_EFFICIENCY,
    DEFAULT_ENERGY_WEIGHT,
    DEFAULT_ENERGY_PRICE_WEIGHT,
    DEFAULT_FROST_PROTECTION,
    DEFAULT_HEATING_EFFICIENCY,
    DEFAULT_HORIZON,
    DEFAULT_FACADE_COLOUR,
    DEFAULT_FACADE_SOLAR_SHARE,
    DEFAULT_FLOOR_TYPE,
    DEFAULT_MAX_TEMP_OFFSET,
    DEFAULT_INFILTRATION_FRACTION,
    DEFAULT_SKY_RADIATIVE_UA,
    DEFAULT_THERMAL_BRIDGE_PSI_L,
    FACADE_COLOUR_TO_ABSORPTANCE,
    FLOOR_TYPE_DEFAULTS,
    DEFAULT_MIN_POWER,
    DEFAULT_R_EXTERNAL,
    DEFAULT_SETPOINT,
    DEFAULT_SIGMA_B,
    DEFAULT_SIGMA_V,
    DEFAULT_SIGMA_W,
    DEFAULT_SMOOTHING_WEIGHT,
    DEFAULT_SOFT_CONSTRAINT_WEIGHT,
    DEFAULT_SOFT_CONSTRAINT_LINEAR_WEIGHT,
    DEFAULT_TERMINAL_WEIGHT,
    DEFAULT_TRACKING_WEIGHT,
    DEFAULT_THERMAL_MASS,
    DEFAULT_TURN_OFF_DEADBAND,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WINDOW_TILT,
    DEFAULT_WINDOW_OPEN_CLOSE_SETTLE,
    DEFAULT_WINDOW_OPEN_DEBOUNCE,
    DEFAULT_WINDOW_OPEN_Q_INFLATION,
    DOMAIN,
    EXCITATION_TYPES,
    HISTORY_BUFFER_SIZE,
    SCHEDULE_MODE_COMFORT,
    SCHEDULE_MODE_OFF,
    SERVICE_CANCEL_EXPERIMENT,
    SERVICE_CREATE_DATASET,
    SERVICE_DELETE_DATASET,
    SERVICE_DELETE_EXPERIMENT,
    SERVICE_SCHEDULE_EXPERIMENT,
    SERVICE_SET_SCHEDULE_ENABLED,
    SOURCE_TYPE_ELECTRIC,
    SOURCE_TYPE_ELECTRIC_FLOOR,
    SOURCE_TYPE_ELECTRIC_STORAGE,
    SOURCE_TYPE_GAS_HEATER,
    SOURCE_TYPE_GENERIC_THERMOSTAT,
    SOURCE_TYPE_GROUND_SOURCE_HP,
    SOURCE_TYPE_HEAT_PUMP,
    SOURCE_TYPE_HYDRONIC_FLOOR,
    SOURCE_TYPE_HYDRONIC_RADIATOR,
    SOURCE_TYPE_OIL_BOILER,
    SOURCE_TYPE_OIL_RADIATOR,
    SOURCE_TYPE_PELLET_STOVE,
    UPDATE_INTERVAL,
    UI_REFRESH_INTERVAL,
)
from .const import (
    CONF_ENVELOPE_TIGHTNESS,
    CONF_ESTIMATED_PARAMS,
    CONF_GROUND_ALBEDO,
    CONF_PERSISTED_COMFORT_OFFSETS,
    CONF_PERSISTED_SETPOINTS,
    CONF_PRICE_ENTITY,
    CONF_PRICE_NET_TARIFF,
    CONF_PRICE_SPOT_SURCHARGE,
    CONF_PLOT_HISTORY_HOURS,
    CONF_PLOT_FORECAST_HOURS,
    CONF_IDENTIFICATION_HISTORY_DAYS,
    DEFAULT_IDENTIFICATION_HISTORY_DAYS,
    CONF_SOURCE_HVAC_MODE,
    DEFAULT_ENVELOPE_TIGHTNESS,
    DEFAULT_PLOT_HISTORY_HOURS,
    DEFAULT_PLOT_FORECAST_HOURS,
    DEFAULT_SOURCE_HVAC_MODE,
    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION,
    SOURCE_HVAC_MODE_COOL,
    SOURCE_HVAC_MODE_HEAT,
    SOURCE_HVAC_MODE_HEAT_COOL,
)
from .config_schema import CONFIG_SCHEMA
from .coordinator import HeatingAssistantCoordinator
from .persistence import persist_tuning_updates, write_entry_config
from .services.context import get_coordinator
from .services.history_access import (
    dataset_boundaries,
    get_history_for_horizon,
    get_history_for_window,
    get_history_with_leading,
    records_for_dataset,
    records_for_datasets,
)
from .services.register import register_all_services
from .websocket_api import register_websocket_api
from .services.simulation import (
    compute_open_loop_rmse_by_horizon,
    effective_heater_scales,
    estimate_simulation_initial_state,
    extract_sim_room_params,
    inject_identified_t_wall_initial,
    merge_per_room_into_sysid_results,
    open_loop_t_wall_initial_dict,
    patched_heat_sources,
)
from .yaml_merge import MergedEntry as _MergedEntry, merge_yaml_into_entry_data as _merge_yaml_into_entry_data

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["climate", "sensor", "button", "datetime"]

SERVICE_SIMULATE_THERMAL_RESPONSE = "simulate_thermal_response"
SERVICE_ESTIMATE_PARAMETERS = "estimate_parameters"
SERVICE_ESTIMATE_PARAMETERS_ML = "estimate_parameters_ml"
SERVICE_REGENERATE_DASHBOARD = "regenerate_dashboard"
SERVICE_COMPUTE_LOGLIK_SLICE = "compute_loglik_slice"
SERVICE_RUN_SYSID_SIMULATION = "run_sysid_simulation"
SERVICE_APPLY_MANUAL_PARAMETERS = "apply_manual_parameters"
SERVICE_RESET_ESTIMATED_PARAMETERS = "reset_estimated_parameters"
SERVICE_APPLY_HEATER_SCALES = "apply_heater_scales"
# SERVICE_SET_SCHEDULE_ENABLED is imported from .const above

DEFAULT_DASHBOARD_FILENAME = "heating_assistant.yaml"
DEFAULT_INDUSTRIAL_DASHBOARD_FILENAME = "heating_assistant_industrial.yaml"

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def async_setup(hass: HomeAssistant, config: Dict[str, Any]) -> bool:
    """Import YAML configuration into the integration's data store."""
    hass.data.setdefault(DOMAIN, {})

    if DOMAIN in config:
        hass.data[DOMAIN]["yaml_config"] = config[DOMAIN]

    async def _async_reload_service(call: ServiceCall) -> None:
        """Re-read configuration.yaml and reload all Heating Assistant entries."""
        new_conf = await async_integration_yaml_config(hass, DOMAIN)
        if new_conf is None:
            _LOGGER.warning(
                "Heating Assistant: configuration.yaml failed validation; "
                "keeping previous configuration"
            )
            return

        hass.data[DOMAIN]["yaml_config"] = new_conf.get(DOMAIN, {})

        entries = hass.config_entries.async_entries(DOMAIN)
        if entries:
            await asyncio.gather(
                *(hass.config_entries.async_reload(e.entry_id) for e in entries)
            )

    async_register_admin_service(hass, DOMAIN, SERVICE_RELOAD, _async_reload_service)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply options in-place when possible; reload only for structural changes."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if isinstance(coordinator, HeatingAssistantCoordinator):
        entry_data = dict(entry.data)
        opts = entry.options
        if opts.get(CONF_ROOMS):
            entry_data[CONF_ROOMS] = opts[CONF_ROOMS]
        if opts.get(CONF_HEAT_SOURCES):
            entry_data[CONF_HEAT_SOURCES] = opts[CONF_HEAT_SOURCES]
        yaml_cfg = hass.data.get(DOMAIN, {}).get("yaml_config", {})
        if yaml_cfg:
            entry_data = _merge_yaml_into_entry_data(entry_data, yaml_cfg)
        merged_config = {**entry_data, **dict(opts)}
        if coordinator.apply_runtime_reconfiguration(merged_config):
            return
    hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Heating Assistant from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Merge YAML config (if present) into the entry data so the coordinator
    # can see room and heat-source definitions regardless of how they were set.
    entry_data = dict(entry.data)

    # Incorporate rooms/sources that were configured via the options flow
    # (stored in entry.options) into entry_data so the coordinator picks them
    # up.  YAML overrides both when it defines rooms/sources (see below).
    opts = entry.options
    if opts.get(CONF_ROOMS):
        entry_data[CONF_ROOMS] = opts[CONF_ROOMS]
    if opts.get(CONF_HEAT_SOURCES):
        entry_data[CONF_HEAT_SOURCES] = opts[CONF_HEAT_SOURCES]

    yaml_cfg = hass.data[DOMAIN].get("yaml_config", {})
    if yaml_cfg:
        entry_data = _merge_yaml_into_entry_data(entry_data, yaml_cfg)

    # Persist merged definitions so entities survive temporary YAML removal.
    new_rooms = entry_data.get(CONF_ROOMS) or []
    new_sources = entry_data.get(CONF_HEAT_SOURCES) or []
    stored_rooms = entry.data.get(CONF_ROOMS) or []
    stored_sources = entry.data.get(CONF_HEAT_SOURCES) or []
    if new_rooms != stored_rooms or new_sources != stored_sources:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **dict(entry.data),
                CONF_ROOMS: new_rooms,
                CONF_HEAT_SOURCES: new_sources,
            },
        )

    # Build a temporary entry-like object with merged data for the coordinator
    merged_entry = _MergedEntry(entry, entry_data)

    coordinator = HeatingAssistantCoordinator(hass, merged_entry)  # type: ignore[arg-type]

    # Set up the integration-managed identification history store so it is
    # available before the first update cycle and before history is restored.
    from .identification_history import IdentificationHistoryStore
    _history_days = int(
        merged_entry.options.get(
            CONF_IDENTIFICATION_HISTORY_DAYS,
            merged_entry.data.get(CONF_IDENTIFICATION_HISTORY_DAYS, DEFAULT_IDENTIFICATION_HISTORY_DAYS),
        )
    )
    coordinator.id_history_store = IdentificationHistoryStore(
        hass, entry.entry_id, _history_days
    )
    await coordinator.id_history_store.async_setup()

    # Set up the named-dataset and experiment stores, and restore any persisted
    # experiments so a scheduled experiment survives a restart of Home Assistant.
    from .datasets import DatasetStore
    from .experiments import ExperimentStore
    coordinator.dataset_store = DatasetStore(hass, entry.entry_id)
    await coordinator.dataset_store.async_load()
    coordinator.experiment_store = ExperimentStore(hass, entry.entry_id)
    coordinator.experiment_manager = await coordinator.experiment_store.async_load()

    # Restore runtime state stashed by a prior unload (in-memory only; survives
    # a reload but not a full HA restart). Only keys still present in the new
    # configuration are restored — rooms removed by the YAML edit drop their
    # state, which is the right outcome.
    reload_state = hass.data[DOMAIN].get("_reload_state", {}).pop(entry.entry_id, None)
    if reload_state is not None:
        # A room rename queued by update_rooms remaps the stashed runtime state
        # (keyed by the old name) onto the new name so the toggle / setpoint
        # state follows the rename instead of resetting to defaults.
        _renames = hass.data[DOMAIN].pop("_pending_room_renames", {}) or {}

        def _remap(state_dict: Dict[str, Any]) -> Dict[str, Any]:
            return {_renames.get(k, k): v for k, v in state_dict.items()}

        coordinator._history_buffer.extend(reload_state.get("history_buffer", []))
        if "system_enabled" in reload_state:
            coordinator._system_enabled = bool(reload_state["system_enabled"])
        for room, value in _remap(reload_state.get("room_enabled", {})).items():
            if room in coordinator._room_enabled:
                coordinator._room_enabled[room] = value
        for room, value in _remap(reload_state.get("schedule_enabled", {})).items():
            if room in coordinator._schedule_enabled:
                coordinator._schedule_enabled[room] = value
        for room, value in _remap(reload_state.get("base_setpoint", {})).items():
            if room in coordinator._base_setpoint:
                coordinator._base_setpoint[room] = float(value)
                coordinator.model.rooms[room].setpoint = float(value)
    else:
        # No in-memory state means this is a full HA restart (not a reload).
        # Priority: JSONL store (integration-managed, long-term) → HA Recorder
        # (covers the period before the JSONL store existed) → legacy JSON store.
        rebuilt = []
        try:
            rebuilt = await coordinator.id_history_store.async_query_recent(
                HISTORY_BUFFER_SIZE
            )
        except Exception:
            _LOGGER.warning(
                "Heating Assistant: JSONL history store query failed on startup",
                exc_info=True,
            )

        if rebuilt:
            coordinator._history_buffer.extend(rebuilt[-HISTORY_BUFFER_SIZE:])
            _LOGGER.debug(
                "Restored %d history steps from JSONL store",
                len(coordinator._history_buffer),
            )
        else:
            # JSONL store empty (first run or files deleted): try HA Recorder.
            try:
                from .history_seed import async_rebuild_history_from_recorder

                rebuilt = await async_rebuild_history_from_recorder(
                    hass, coordinator, HISTORY_BUFFER_SIZE
                )
            except Exception:
                _LOGGER.warning(
                    "Heating Assistant: history rebuild from recorder failed",
                    exc_info=True,
                )

            if rebuilt:
                coordinator._history_buffer.extend(rebuilt[-HISTORY_BUFFER_SIZE:])
                _LOGGER.debug(
                    "Rebuilt %d history steps from the recorder",
                    len(coordinator._history_buffer),
                )
            else:
                # Fall back to the persisted buffer (recorder unavailable or empty).
                try:
                    store = Store(
                        hass,
                        version=1,
                        key=f"{DOMAIN}_history_{entry.entry_id}",
                    )
                    stored_history = await store.async_load()
                    if stored_history and isinstance(stored_history, list):
                        # Drop records older than the buffer's nominal time span so a
                        # previous session's stale (e.g. week-old) data cannot survive
                        # across a restart, linger at the front of the count-bounded
                        # deque, and pollute the identification diagnostics.
                        from .history_window import prune_stale_records

                        _now = getattr(coordinator, "now_utc", None)
                        _now_ts = _now.timestamp() if _now is not None else time.time()
                        coordinator._history_buffer.extend(
                            prune_stale_records(
                                stored_history[-HISTORY_BUFFER_SIZE:],
                                _now_ts,
                                HISTORY_BUFFER_SIZE * coordinator.dt,
                            )
                        )
                        _LOGGER.debug(
                            "Restored %d history steps from persistent storage",
                            len(coordinator._history_buffer),
                        )
                except Exception:
                    _LOGGER.warning(
                        "Heating Assistant: failed to load persisted history buffer",
                        exc_info=True,
                    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        _LOGGER.debug(
            "Heating Assistant: initial data fetch failed. Setup continues and "
            "entities will populate after the next successful update",
            exc_info=True,
        )

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Register services (only once for the domain)
    if not hass.services.has_service(DOMAIN, SERVICE_SIMULATE_THERMAL_RESPONSE):
        _register_services(hass)

    # Register WebSocket API (only once for the domain)
    if not hass.data[DOMAIN].get("_ws_registered"):
        register_websocket_api(hass)
        hass.data[DOMAIN]["_ws_registered"] = True

    # Auto-reload when the user changes options via the integration UI.
    # Attached after the coordinator is stored so the persist-merged
    # async_update_entry call above (which already short-circuits when nothing
    # changed) cannot fire the listener during setup.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Push the current coordinator attribute values into every registered
    # entity's HA state immediately after setup.  Without this call,
    # ControllerConfigSensor (and others) only write state when the coordinator
    # runs its next scheduled tick, which can be up to update_interval seconds
    # away.  The Tuning page reads hass.states on load, so without this call
    # the entity state may be missing until the first coordinator tick fires.
    coordinator.async_update_listeners()

    # Watch only the entities this integration needs (outdoor sensor, weather,
    # room temperature sensors) rather than waiting for EVENT_HOMEASSISTANT_STARTED
    # which blocks on all integrations — including unrelated slow ones.
    # Each listener fires a coordinator refresh as soon as its entity transitions
    # from unknown/unavailable to a valid state.
    cancel_startup = coordinator.setup_startup_listeners()
    if cancel_startup is not None:
        entry.async_on_unload(cancel_startup)

    # Persisted START/STOP only restores the flag and UI state.  Mirror the
    # set_system_enabled service's immediate refresh so MPC, EKF, and actuator
    # commands resume without waiting for the next scheduled interval (or a
    # manual STOP/START cycle).
    if coordinator.system_enabled:
        await coordinator.async_request_refresh()

    # Schedule immediate + deadline-aligned refreshes for window open/close
    # events so debounce and settle timings are honoured independently of the
    # coordinator's update interval.
    cancel_window = coordinator.setup_window_listeners()
    if cancel_window is not None:
        entry.async_on_unload(cancel_window)

    # Fast UI refresh: re-read measurements / setpoints / solar and push them to
    # the dashboard on a short cadence so KPIs, measurement cards, and plots stay
    # live between scheduled MPC ticks.  The MPC and EKF advance strictly at the
    # coordinator's update_interval; this loop never runs the controller.  The
    # cadence is capped at the update interval so it never fires more often than
    # the MPC when a very short interval is configured.
    ui_refresh_seconds = min(UI_REFRESH_INTERVAL, coordinator.update_interval_seconds)
    if ui_refresh_seconds > 0:

        async def _async_ui_refresh(_now) -> None:
            await coordinator.async_refresh_ui()

        entry.async_on_unload(
            async_track_time_interval(
                hass,
                _async_ui_refresh,
                timedelta(seconds=ui_refresh_seconds),
            )
        )

    # NOTE: Native Lovelace dashboards are kept in code but disabled.
    # The custom JS/CSS panel below is the primary dashboard.
    # written = await _async_auto_write_default_dashboard(hass, entry, coordinator)
    # if written:
    #     await _async_try_register_lovelace_dashboard(hass, written)
    # written_industrial = await _async_auto_write_industrial_dashboard(
    #     hass, entry, coordinator
    # )
    # if written_industrial:
    #     await _async_try_register_lovelace_dashboard(
    #         hass,
    #         written_industrial,
    #         url_path=DASHBOARD_INDUSTRIAL_URL_PATH,
    #         title="Heating Assistant Industrial",
    #         icon="mdi:factory",
    #     )

    # Register custom JS/CSS panel
    try:
        import pathlib

        from homeassistant.components.http import StaticPathConfig

        www_path = pathlib.Path(__file__).parent / "www"
        await hass.http.async_register_static_paths(
            [StaticPathConfig("/ha-industrial-panel", str(www_path), cache_headers=False)]
        )

        # Register the custom icon set so it is available on every HA page
        # (including the sidebar) before the frontend renders.  The icon JS
        # lives in www/ and is served under the static path above.
        _sidebar_icon = "mdi:radiator"
        try:
            from homeassistant.components.frontend import async_register_extra_urls

            async_register_extra_urls(
                hass, ["/ha-industrial-panel/heating-assistant-icons.js"]
            )
            _sidebar_icon = "heating-assistant:logo"
        except (ImportError, AttributeError):
            _LOGGER.debug(
                "Heating Assistant: async_register_extra_urls unavailable, "
                "falling back to mdi:radiator sidebar icon",
            )

        from homeassistant.components.frontend import async_register_built_in_panel

        async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title="Heating Assistant",
            sidebar_icon=_sidebar_icon,
            frontend_url_path="ha-industrial",
            config={
                "_panel_custom": {
                    "name": "ha-industrial-panel",
                    # This ?v= token is the SINGLE source of truth for the
                    # frontend cache-bust version.  industrial-dashboard.js reads
                    # this exact token off its own URL (import.meta.url) and reuses
                    # it for every dynamically-imported submodule, so the entry
                    # point and its submodules can never drift out of sync.  Bump
                    # this token (and nothing else) on every frontend change to
                    # force browsers/service-workers to fetch fresh assets.
                    "js_url": "/ha-industrial-panel/industrial-dashboard.js?v=89",
                    "embed_iframe": False,
                }
            },
            require_admin=False,
        )
    except Exception:
        _LOGGER.debug(
            "Heating Assistant: custom panel registration skipped",
            exc_info=True,
        )

    return True


DASHBOARD_URL_PATH = "heating-assistant"
DASHBOARD_INDUSTRIAL_URL_PATH = "heating-assistant-industrial"


async def _async_auto_write_default_dashboard(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HeatingAssistantCoordinator,
) -> Optional[str]:
    """Write the default dashboard YAML on first setup (or after a format upgrade).

    Skipped when the per-entry marker says we've already auto-written at the
    current format version.  Bumping ``_DASHBOARD_FORMAT_VERSION`` below forces
    a one-time regeneration for all existing installs (e.g. after entity-id
    formula changes).  The file is always overwritten when a regeneration is
    triggered; users who have customised the file directly are expected to use
    the ``regenerate_dashboard`` service instead of relying on the auto-write.

    This is a best-effort convenience: any failure is logged at debug
    level and never propagated, so a missing ``hass.config`` (in tests) or
    a read-only config directory cannot break integration setup.
    """
    import os
    from datetime import datetime, timezone

    from .dashboard import build_dashboard_from_coordinator, dashboard_to_yaml

    # Bump this when the generated entity-id formula or card structure changes
    # in a way that makes old files incorrect.  Existing installs will have
    # their dashboard file overwritten once on the next HA restart.
    _DASHBOARD_FORMAT_VERSION = 5

    try:
        marker_store = Store(
            hass, version=1, key=f"{DOMAIN}_dashboard_marker_{entry.entry_id}"
        )
        marker = await marker_store.async_load()
        config_horizon = coordinator._horizon
        config_dt = coordinator.dt
        if (
            marker
            and marker.get("written_at")
            and marker.get("format_version", 1) >= _DASHBOARD_FORMAT_VERSION
            and marker.get("horizon") == config_horizon
            and marker.get("dt") == config_dt
        ):
            return marker.get("path") if marker.get("path") else None

        base_dir = hass.config.path("dashboards")
        target = os.path.join(base_dir, DEFAULT_DASHBOARD_FILENAME)
        if not marker and os.path.exists(target):
            await marker_store.async_save(
                {
                    "written_at": datetime.now(tz=timezone.utc).isoformat(),
                    "path": target,
                    "format_version": _DASHBOARD_FORMAT_VERSION,
                    "horizon": config_horizon,
                    "dt": config_dt,
                }
            )
            return target

        dashboard = build_dashboard_from_coordinator(coordinator)
        yaml_text = await hass.async_add_executor_job(dashboard_to_yaml, dashboard)

        def _write() -> None:
            os.makedirs(base_dir, exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(yaml_text)

        await hass.async_add_executor_job(_write)

        await marker_store.async_save(
            {
                "written_at": datetime.now(tz=timezone.utc).isoformat(),
                "path": target,
                "format_version": _DASHBOARD_FORMAT_VERSION,
                "horizon": config_horizon,
                "dt": config_dt,
            }
        )

        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Heating Assistant dashboard available",
                "message": (
                    f"Heating Assistant wrote a starter Lovelace dashboard to "
                    f"`{target}`. To add it to the sidebar, open "
                    "**Settings → Dashboards → Add Dashboard → Show YAML "
                    "editor**, paste the file contents, and save. Re-run "
                    "`heating_assistant.regenerate_dashboard` after editing "
                    "rooms to refresh the file."
                ),
                "notification_id": f"{DOMAIN}_dashboard_first_install",
            },
            blocking=False,
        )
        return target
    except Exception:
        _LOGGER.debug(
            "Heating Assistant: auto-write of starter dashboard skipped",
            exc_info=True,
        )
        return None


async def _async_auto_write_industrial_dashboard(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HeatingAssistantCoordinator,
) -> Optional[str]:
    """Write the industrial dashboard YAML as an additional dashboard."""
    import os
    from datetime import datetime, timezone

    from .dashboard import (
        DASHBOARD_VARIANT_INDUSTRIAL,
        build_dashboard_variant_from_coordinator,
        dashboard_to_yaml,
    )

    _DASHBOARD_FORMAT_VERSION = 1

    try:
        marker_store = Store(
            hass,
            version=1,
            key=f"{DOMAIN}_industrial_dashboard_marker_{entry.entry_id}",
        )
        marker = await marker_store.async_load()
        config_horizon = coordinator._horizon
        config_dt = coordinator.dt
        if (
            marker
            and marker.get("written_at")
            and marker.get("format_version", 1) >= _DASHBOARD_FORMAT_VERSION
            and marker.get("horizon") == config_horizon
            and marker.get("dt") == config_dt
        ):
            return marker.get("path") if marker.get("path") else None

        base_dir = hass.config.path("dashboards")
        target = os.path.join(base_dir, DEFAULT_INDUSTRIAL_DASHBOARD_FILENAME)
        if not marker and os.path.exists(target):
            await marker_store.async_save(
                {
                    "written_at": datetime.now(tz=timezone.utc).isoformat(),
                    "path": target,
                    "format_version": _DASHBOARD_FORMAT_VERSION,
                    "horizon": config_horizon,
                    "dt": config_dt,
                }
            )
            return target

        dashboard = build_dashboard_variant_from_coordinator(
            coordinator,
            variant=DASHBOARD_VARIANT_INDUSTRIAL,
        )
        yaml_text = await hass.async_add_executor_job(dashboard_to_yaml, dashboard)

        def _write() -> None:
            os.makedirs(base_dir, exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(yaml_text)

        await hass.async_add_executor_job(_write)

        await marker_store.async_save(
            {
                "written_at": datetime.now(tz=timezone.utc).isoformat(),
                "path": target,
                "format_version": _DASHBOARD_FORMAT_VERSION,
                "horizon": config_horizon,
                "dt": config_dt,
            }
        )
        return target
    except Exception:
        _LOGGER.debug(
            "Heating Assistant: auto-write of industrial dashboard skipped",
            exc_info=True,
        )
        return None


async def _async_try_register_lovelace_dashboard(
    hass: HomeAssistant,
    yaml_path: str,
    *,
    url_path: str = DASHBOARD_URL_PATH,
    title: str = "Heating Assistant",
    icon: str = "mdi:home-thermometer",
) -> None:
    """Best-effort registration of the YAML file as a Lovelace dashboard.

    Hooks into ``hass.data["lovelace"]`` to add a YAML-mode dashboard whose
    source is the file we just wrote, so the entry appears in the sidebar
    without the user having to paste anything. The whole block is wrapped
    in ``try``/``except`` because we depend on a semi-private HA surface
    that occasionally moves between releases. On any failure we leave the
    existing persistent notification as the fallback path.
    """
    import os

    try:
        from homeassistant.components.lovelace.dashboard import LovelaceYAML

        lovelace_data = hass.data.get("lovelace")
        if lovelace_data is None:
            return

        # ``LovelaceData`` (modern HA) exposes ``dashboards``; older
        # snapshots stored it as ``hass.data["lovelace"]["dashboards"]``.
        dashboards = getattr(lovelace_data, "dashboards", None)
        if dashboards is None and isinstance(lovelace_data, dict):
            dashboards = lovelace_data.get("dashboards")
        if dashboards is None:
            return
        if url_path in dashboards:
            return  # already registered (e.g. by the user)

        rel_filename = os.path.relpath(yaml_path, hass.config.path())
        config = {
            "mode": "yaml",
            "icon": icon,
            "title": title,
            "filename": rel_filename,
            "url_path": url_path,
            "show_in_sidebar": True,
            "require_admin": False,
        }
        dashboards[url_path] = LovelaceYAML(
            hass, url_path, config
        )

        try:
            from homeassistant.components import frontend

            frontend.async_register_built_in_panel(
                hass,
                component_name="lovelace",
                sidebar_title=config["title"],
                sidebar_icon=config["icon"],
                frontend_url_path=url_path,
                config={"mode": "yaml"},
                require_admin=False,
                update=False,
            )
        except Exception:
            _LOGGER.debug(
                "Heating Assistant: sidebar panel registration skipped",
                exc_info=True,
            )
    except Exception:
        _LOGGER.debug(
            "Heating Assistant: Lovelace dashboard auto-registration skipped",
            exc_info=True,
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = hass.data[DOMAIN].get(entry.entry_id)
        if isinstance(coordinator, HeatingAssistantCoordinator):
            reload_state = hass.data[DOMAIN].setdefault("_reload_state", {})
            reload_state[entry.entry_id] = {
                "history_buffer": list(coordinator._history_buffer),
                "system_enabled": coordinator._system_enabled,
                "room_enabled": dict(coordinator._room_enabled),
                "schedule_enabled": dict(coordinator._schedule_enabled),
                "base_setpoint": dict(coordinator._base_setpoint),
            }
            # Persist the history buffer to HA's persistent storage so that it
            # survives a full Home Assistant restart (not just an in-memory reload).
            try:
                store = Store(
                    hass,
                    version=1,
                    key=f"{DOMAIN}_history_{entry.entry_id}",
                )
                await store.async_save(list(coordinator._history_buffer))
            except Exception:
                _LOGGER.warning(
                    "Heating Assistant: failed to persist history buffer",
                    exc_info=True,
                )
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


# ---------------------------------------------------------------------------
# Service helpers
# ---------------------------------------------------------------------------

def _get_coordinator(hass: HomeAssistant) -> HeatingAssistantCoordinator:
    return get_coordinator(hass)


# ---------------------------------------------------------------------------
# Room-rename migration
# ---------------------------------------------------------------------------
# When a room is renamed from the Configuration page, everything keyed by the
# old room name must follow it to the new name — otherwise the renamed room
# starts blank and the old data is orphaned.  These helpers migrate the
# config-entry data (persisted state, estimated parameters, heat-source room
# links) and inter-room connection references.  The entity registry is migrated
# separately (best-effort) so the room keeps its history and entity ids.

#: ``entry.data`` keys whose value is a ``{room_name: ...}`` dict.
_ROOM_KEYED_STATE_KEYS = (
    CONF_PERSISTED_SETPOINTS,
    CONF_PERSISTED_COMFORT_OFFSETS,
    CONF_PERSISTED_SCHEDULES,
)


def _remap_keys(d: Any, renames: Dict[str, str]) -> Any:
    """Return ``d`` with any top-level keys present in ``renames`` remapped."""
    if not isinstance(d, dict):
        return d
    return {renames.get(k, k): v for k, v in d.items()}


def _migrate_room_name_data(data: Dict[str, Any], renames: Dict[str, str]) -> Dict[str, Any]:
    """Migrate every room-name-keyed structure in a ``data``/``options`` dict.

    Covers persisted per-room state, the nested ``rooms`` map inside the
    estimated-parameters snapshot, and the ``room`` link on each heat source.
    Returns a new dict; the input is not mutated.
    """
    if not renames:
        return dict(data)
    new = dict(data)

    for key in _ROOM_KEYED_STATE_KEYS:
        if isinstance(new.get(key), dict):
            new[key] = _remap_keys(new[key], renames)

    estimated = new.get(CONF_ESTIMATED_PARAMS)
    if isinstance(estimated, dict) and isinstance(estimated.get("rooms"), dict):
        estimated = dict(estimated)
        estimated["rooms"] = _remap_keys(estimated["rooms"], renames)
        new[CONF_ESTIMATED_PARAMS] = estimated

    sources = new.get(CONF_HEAT_SOURCES)
    if isinstance(sources, list):
        new[CONF_HEAT_SOURCES] = [
            {**s, CONF_SOURCE_ROOM: renames[s[CONF_SOURCE_ROOM]]}
            if isinstance(s, dict) and s.get(CONF_SOURCE_ROOM) in renames
            else s
            for s in sources
        ]
    return new


def _apply_renames_to_connections(
    rooms: List[Dict[str, Any]], renames: Dict[str, str]
) -> List[Dict[str, Any]]:
    """Re-point inter-room connection targets to renamed rooms."""
    if not renames:
        return rooms
    out: List[Dict[str, Any]] = []
    for room in rooms:
        if isinstance(room, dict) and isinstance(room.get(CONF_CONNECTIONS), list):
            room = {
                **room,
                CONF_CONNECTIONS: [
                    {**c, CONF_CONNECTED_ROOM: renames[c[CONF_CONNECTED_ROOM]]}
                    if isinstance(c, dict) and c.get(CONF_CONNECTED_ROOM) in renames
                    else c
                    for c in room[CONF_CONNECTIONS]
                ],
            }
        out.append(room)
    return out


def _migrate_room_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    renames: Dict[str, str],
    all_room_names: List[str],
) -> None:
    """Best-effort: move this integration's per-room entities to the new name.

    Updates each affected registry entry's ``unique_id`` (and ``entity_id`` slug)
    in place so the room keeps its recorder history, dashboards and automations
    instead of orphaning the old entities and creating fresh ones.  Any failure
    is logged and swallowed — a rename must never be blocked by registry quirks.
    """
    from homeassistant.helpers import entity_registry as er

    from .naming import slugify as _slugify

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    existing_uids = {e.unique_id for e in entries}
    other_names = [n for n in all_room_names if n]

    for ent in entries:
        for old, new in renames.items():
            prefix = f"{DOMAIN}_{old}_"
            if not ent.unique_id.startswith(prefix):
                continue
            # Skip when a longer room name also prefixes this id (e.g. renaming
            # "living" must not capture "living_room"'s entities).
            if any(
                other != old
                and len(other) > len(old)
                and ent.unique_id.startswith(f"{DOMAIN}_{other}_")
                for other in other_names
            ):
                break
            new_uid = f"{DOMAIN}_{new}_" + ent.unique_id[len(prefix):]
            if new_uid in existing_uids:
                break
            updates: Dict[str, Any] = {"new_unique_id": new_uid}
            old_slug = _slugify(old)
            new_slug = _slugify(new)
            old_eid_part = f"{DOMAIN}_{old_slug}_"
            if old_slug != new_slug and old_eid_part in ent.entity_id:
                updates["new_entity_id"] = ent.entity_id.replace(
                    old_eid_part, f"{DOMAIN}_{new_slug}_", 1
                )
            try:
                registry.async_update_entity(ent.entity_id, **updates)
                existing_uids.add(new_uid)
            except Exception:  # pragma: no cover - defensive
                _LOGGER.warning(
                    "Heating Assistant: failed migrating entity %s on room rename",
                    ent.entity_id,
                    exc_info=True,
                )
            break


def _register_services(hass: HomeAssistant) -> None:
    """Register domain services for setup assistance."""

    async def handle_simulate(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass)
        result = coordinator.simulate_thermal_response(
            room_name=call.data["room_name"],
            initial_temp=call.data["initial_temp"],
            outdoor_temp=call.data["outdoor_temp"],
            heating_power=call.data["heating_power"],
            duration_hours=call.data["duration_hours"],
        )
        # Fire an event so automations can consume the result via a trigger,
        # and return it as service-response data so it shows up inline in
        # Developer Tools → Actions rather than as a persistent notification.
        hass.bus.async_fire(
            f"{DOMAIN}_simulation_result",
            result,
        )
        return result

    async def handle_estimate(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass)
        result = coordinator.estimate_parameters(
            room_name=call.data["room_name"],
            heating_power=call.data["heating_power"],
            outdoor_temp=call.data["outdoor_temp"],
            initial_temp=call.data["initial_temp"],
            final_temp=call.data["final_temp"],
            duration_seconds=call.data["duration_seconds"],
        )
        # Fire an event for automations and return the result as service
        # response data (Developer Tools → Actions); no notification is raised.
        hass.bus.async_fire(
            f"{DOMAIN}_estimation_result",
            result,
        )
        return result

    async def handle_estimate_ml(call: ServiceCall) -> None:
        """Run ML parameter estimation using the Kalman filter log-likelihood."""
        coordinator = _get_coordinator(hass)
        apply_params: bool = call.data.get("apply_parameters", False)
        horizon_hours: Optional[float] = call.data.get("horizon_hours")
        locked_params: Optional[Dict] = call.data.get("locked_params")
        window_start_ml: Optional[float] = (
            float(call.data["window_start"]) if "window_start" in call.data else None
        )
        window_end_ml: Optional[float] = (
            float(call.data["window_end"]) if "window_end" in call.data else None
        )

        # Resolve the data the estimator runs over, in priority order:
        #   1. ``dataset_ids`` — joint identification over several stored
        #      datasets (their records are merged into one history).
        #   2. ``dataset_id`` — a single stored dataset's snapshot.
        #   3. an explicit ``window_start``/``window_end`` (JSONL / Recorder).
        # Each takes precedence over the trailing ``horizon_hours`` window.
        dataset_ids = call.data.get("dataset_ids")
        history_override = records_for_datasets(coordinator, dataset_ids)
        # Collect each dataset's first timestamp so the estimator assigns an
        # independent per-dataset wall-temperature parameter block.
        dataset_start_timestamps: Optional[List[float]] = (
            dataset_boundaries(coordinator, dataset_ids)
            if history_override is not None
            else None
        )
        if history_override is None:
            history_override = records_for_dataset(
                coordinator, call.data.get("dataset_id")
            )
        if history_override is None and window_start_ml is not None and window_end_ml is not None:
            history_override = await get_history_for_window(
                hass, coordinator, window_start_ml, window_end_ml
            )

        if history_override is None and horizon_hours is not None:
            history_override = await get_history_for_horizon(
                hass, coordinator, float(horizon_hours)
            )

        result = await coordinator.async_estimate_parameters_ml(
            apply_params=apply_params,
            horizon_hours=horizon_hours if history_override is None else None,
            locked_params=locked_params,
            history_override=history_override,
            dataset_start_timestamps=dataset_start_timestamps,
        )

        # Update sysid_results so that the dashboard sensor entities reflect
        # the newly identified parameters immediately (without requiring a
        # separate sysid simulation run).
        if result.get("success"):
            dt = coordinator.dt
            horizon_steps = (
                max(1, int(float(horizon_hours) * 3600.0 / dt))
                if horizon_hours is not None
                else None
            )
            internal_gains = result.get("estimated_internal_gains", {})
            solar_scales = result.get("estimated_solar_scales", {})
            envelope_splits = result.get("estimated_envelope_splits", {})
            t_wall_initial = result.get("estimated_t_wall_initial", {})
            heater_scales = result.get("estimated_heater_scales", {})
            inter_room_r = result.get("estimated_inter_room_r", {})

            # Map each room to the identified scales of the heaters in it so the
            # per-room identification page can list them like any other param.
            sources_by_room: Dict[str, Dict[str, float]] = {}
            for src in coordinator.heat_sources:
                room = getattr(src, "room", None)
                if room is None or src.name not in heater_scales:
                    continue
                sources_by_room.setdefault(room, {})[src.name] = float(
                    heater_scales[src.name]
                )

            for room_name, params in result.get("estimated_params", {}).items():
                existing = coordinator.sysid_results.get(room_name, {})
                existing["thermal_mass"] = params.get("thermal_mass")
                existing["r_external"] = params.get("r_external")
                # Surface the full identified parameter set so the room-level
                # identification page can review and apply every parameter
                # (not just C / R_ext) in one place.
                if room_name in internal_gains:
                    existing["internal_gain"] = internal_gains[room_name]
                if room_name in solar_scales:
                    existing["solar_scale"] = solar_scales[room_name]
                if room_name in envelope_splits:
                    splits = envelope_splits[room_name]
                    if "c_air_fraction" in splits:
                        existing["c_air_fraction"] = splits["c_air_fraction"]
                    if "r_aw_fraction" in splits:
                        existing["r_aw_fraction"] = splits["r_aw_fraction"]
                if room_name in t_wall_initial:
                    existing["t_wall_initial"] = t_wall_initial[room_name]
                room_connections = {
                    key: val
                    for key, val in inter_room_r.items()
                    if key.startswith(f"{room_name}:")
                    or key.endswith(f":{room_name}")
                }
                if room_connections:
                    existing["estimated_inter_room_r"] = room_connections
                if room_name in sources_by_room:
                    existing["heater_scales"] = sources_by_room[room_name]
                if horizon_steps is not None:
                    existing["horizon_steps"] = horizon_steps
                coordinator.sysid_results[room_name] = existing

            # Cache identified heater scales on the coordinator so the system-wide
            # config sensor keeps exposing them too.
            coordinator._last_identified_heater_scales = dict(heater_scales)

            coordinator.async_update_listeners()

        # Fire an event so automations can consume the full result. The
        # outcome is surfaced in the UI through the per-room parameter and
        # diagnostic sensors, so no persistent notification is raised.
        hass.bus.async_fire(
            f"{DOMAIN}_ml_estimation_result",
            {
                k: v
                for k, v in result.items()
                if isinstance(v, (str, int, float, bool, type(None)))
            },
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SIMULATE_THERMAL_RESPONSE,
        handle_simulate,
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("initial_temp"): vol.Coerce(float),
                vol.Required("outdoor_temp"): vol.Coerce(float),
                vol.Required("heating_power"): vol.Coerce(float),
                vol.Required("duration_hours"): vol.Coerce(float),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_ESTIMATE_PARAMETERS,
        handle_estimate,
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("heating_power"): vol.Coerce(float),
                vol.Required("outdoor_temp"): vol.Coerce(float),
                vol.Required("initial_temp"): vol.Coerce(float),
                vol.Required("final_temp"): vol.Coerce(float),
                vol.Required("duration_seconds"): vol.Coerce(float),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_ESTIMATE_PARAMETERS_ML,
        handle_estimate_ml,
        schema=vol.Schema(
            {
                vol.Optional("apply_parameters", default=False): cv.boolean,
                vol.Optional("horizon_hours"): vol.All(
                    vol.Coerce(float), vol.Range(min=1.0)
                ),
                vol.Optional("window_start"): vol.Coerce(float),
                vol.Optional("window_end"): vol.Coerce(float),
                vol.Optional("locked_params"): dict,
                # Identify from a stored dataset's snapshotted records, taking
                # precedence over window_start/window_end and horizon_hours.
                vol.Optional("dataset_id"): cv.string,
                # Joint identification over several stored datasets (their
                # records are merged); takes precedence over dataset_id.
                vol.Optional("dataset_ids"): [cv.string],
            }
        ),
    )

    async def handle_run_open_loop_simulation(call: ServiceCall) -> None:
        """Run open-loop simulation diagnostic and report RMSE per room."""
        from .model_diagnostics import compute_open_loop_predictions
        from .simulation.model_patch import build_sim_model

        coordinator = _get_coordinator(hass)
        horizon_hours: Optional[float] = call.data.get("horizon_hours")
        window_start_ol: Optional[float] = (
            float(call.data["window_start"]) if "window_start" in call.data else None
        )
        window_end_ol: Optional[float] = (
            float(call.data["window_end"]) if "window_end" in call.data else None
        )
        sigma_w: float = float(call.data.get(
            "sigma_w", getattr(coordinator, "_sigma_w", 0.1)
        ))
        sigma_v: float = float(call.data.get(
            "sigma_v", getattr(coordinator, "_sigma_v", 0.5)
        ))

        leading_history: Optional[List[Dict[str, Any]]] = None

        # A stored dataset takes precedence over a window / horizon and supplies
        # its snapshotted records directly.
        dataset_records = records_for_dataset(coordinator, call.data.get("dataset_id"))
        if dataset_records is not None:
            history = dataset_records
        # Explicit window: fetch leading calibration data plus the window.
        elif window_start_ol is not None and window_end_ol is not None:
            _full, leading_history, history = await get_history_with_leading(
                hass, coordinator, window_start_ol, window_end_ol,
            )
            from .history_window import select_window_by_timestamps
            history = select_window_by_timestamps(history, window_start_ol, window_end_ol)
        elif horizon_hours is not None:
            history = await get_history_for_horizon(
                hass, coordinator, float(horizon_hours)
            )
            from .history_window import history_time_range, select_leading_window
            min_ts, _max_ts = history_time_range(history)
            if min_ts is not None:
                _full, leading_history, _sim = await get_history_with_leading(
                    hass,
                    coordinator,
                    min_ts,
                    _max_ts if _max_ts is not None else min_ts,
                    leading_hours=6.0,
                )
        else:
            history = list(coordinator.history_buffer)

        # Build per-room parameter overrides from service data (same keys as
        # run_sysid_simulation) so the open-loop diagnostic uses the full
        # parameter set the user configured in the identification panel.
        room_params = extract_sim_room_params(call, coordinator.model.room_names)

        # When room-parameter overrides are given, build a temporary
        # HouseThermalSDE from a patched model copy so the open-loop
        # simulation uses the parameters currently visible in the UI, not
        # the last applied set.  Without overrides the live controller
        # system is used directly (fast path, no copy needed).
        room_names = coordinator.model.room_names
        n_rooms = len(room_names)
        dt = coordinator.dt

        # Patch heat-source copies with the heater power scales currently shown
        # in the UI (falling back to the last auto-identified scales) so the
        # open-loop simulation uses them even when the user hasn't clicked Apply.
        heater_scales = effective_heater_scales(call, coordinator)
        base_heat_sources = patched_heat_sources(coordinator, heater_scales)

        if room_params or heater_scales:
            from .controller import HouseThermalSDE  # noqa: PLC0415
            try:
                sim_model = build_sim_model(
                    coordinator.model, room_params, room_names
                )
                system = HouseThermalSDE(
                    sim_model,
                    base_heat_sources,
                    dt,
                    sigma_w=sigma_w,
                    sigma_v=sigma_v,
                    augment_offsets=False,
                )
            except Exception as exc:
                _LOGGER.error(
                    "Open-loop simulation: failed to build patched system: %s",
                    exc, exc_info=True,
                )
                return
        else:
            system = coordinator.controller._system

        # Estimate optimal initial air / wall temperatures from a leading
        # calibration window (or a short prefix when no leading data exists).
        t_wall_initial_identified: Dict[str, float] = {}
        try:
            init_state = await estimate_simulation_initial_state(
                hass,
                coordinator,
                history,
                system,
                leading_history=leading_history,
                room_params=room_params if room_params else None,
                sigma_w=sigma_w,
                sigma_v=sigma_v,
            )
            t_wall_initial_identified = {
                name: float(v) for name, v in init_state.get("t_wall", {}).items()
            }
            _LOGGER.debug(
                "Open-loop initial state: method=%s calibration_steps=%s",
                init_state.get("method"),
                init_state.get("calibration_steps"),
            )
        except Exception as exc:
            _LOGGER.warning(
                "Open-loop: initial-state estimation failed (%s); "
                "falling back to cached or air-temperature seed.", exc,
            )
            t_wall_initial_identified = open_loop_t_wall_initial_dict(
                room_params, coordinator
            )

        # Results are surfaced in the UI via the per-room OpenLoopRMSESensor
        # entities, so this service writes its output to the coordinator cache
        # rather than raising a persistent notification.
        #
        # Use continuous mode (segment_length=None) for the main simulation so
        # the plot shows no artificial discontinuities: the wall/envelope state
        # is never re-initialised mid-run, it just evolves freely from the
        # starting condition.  The multi-horizon RMSE analysis below uses fixed
        # segment lengths specifically to measure N-step-ahead accuracy.
        try:
            result = await hass.async_add_executor_job(
                compute_open_loop_predictions,
                history,
                system,
                room_names,
                n_rooms,
                dt,
                None,
                t_wall_initial_identified,
            )

            if "error" not in result:
                per_room = result.get("per_room", {})

                # Multi-horizon open-loop RMSE: re-run the simulation at
                # ~4 h / 12 h / 24 h segment lengths so the diagnostic shows
                # how prediction error grows with horizon — the quantity
                # that matters for price-driven anticipatory heating, where
                # the plan spans many hours.  Each run reuses the same
                # history and model; only the segment slicing differs.
                rmse_by_horizon = await compute_open_loop_rmse_by_horizon(
                    hass,
                    history,
                    system,
                    room_names,
                    n_rooms,
                    dt,
                    t_wall_initial_identified,
                )
                for name in room_names:
                    if name in per_room:
                        per_room[name]["rmse_by_horizon"] = rmse_by_horizon[name]

                # Write per-room results to coordinator cache so that
                # OpenLoopRMSESensor entities can read them without any
                # blocking computation on the event loop.
                coordinator.open_loop_results.update(per_room)
                coordinator.async_update_listeners()

        except Exception as exc:
            _LOGGER.error("Open-loop simulation failed: %s", exc, exc_info=True)

    register_all_services(hass)

    hass.services.async_register(
        DOMAIN,
        "run_open_loop_simulation",
        handle_run_open_loop_simulation,
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
                vol.Optional("segment_length", default=30): vol.All(
                    vol.Coerce(int), vol.Range(min=5)
                ),
                vol.Optional("horizon_hours"): vol.Coerce(float),
                # Explicit window overrides horizon when both start and end are
                # provided. Values are UNIX timestamps (seconds since epoch).
                vol.Optional("window_start"): vol.Coerce(float),
                vol.Optional("window_end"): vol.Coerce(float),
                vol.Optional("sigma_w"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
                vol.Optional("sigma_v"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
                # Per-source heater power scales {source_name: scale}.
                vol.Optional("heater_scales"): {cv.string: vol.Coerce(float)},
            },
            # Allow per-room parameter overrides keyed as <param>_<room_slug>
            # (thermal_mass_<room>, r_external_<room>, internal_gain_<room>,
            # solar_scale_<room>, c_air_fraction_<room>, r_aw_fraction_<room>).
            extra=vol.ALLOW_EXTRA,
        ),
    )

    async def handle_run_sysid_simulation(call: ServiceCall) -> None:
        """Run system-identification open-loop simulation with configurable params."""
        from .sysid import run_sysid_simulation

        coordinator = _get_coordinator(hass)
        room_name_filter: Optional[str] = call.data.get("room_name")
        horizon_hours: float = float(call.data.get("horizon_hours", 6.0))
        sigma_w: float = float(call.data.get(
            "sigma_w", getattr(coordinator, "_sigma_w", 0.1)
        ))
        sigma_v: float = float(call.data.get(
            "sigma_v", getattr(coordinator, "_sigma_v", 0.5)
        ))

        # Explicit window: if window_start / window_end are supplied (UNIX
        # timestamps), they override the horizon-based trailing window.
        window_start: Optional[float] = (
            float(call.data["window_start"]) if "window_start" in call.data else None
        )
        window_end: Optional[float] = (
            float(call.data["window_end"]) if "window_end" in call.data else None
        )
        window_spec = (
            (window_start, window_end)
            if window_start is not None and window_end is not None
            else None
        )

        dt = coordinator.dt  # seconds; handles runtime overrides correctly
        horizon_steps = max(1, int(horizon_hours * 3600.0 / dt))

        # Build per-room parameter overrides from service data (full parameter
        # set, keyed by ``<param>_<room_slug>``).
        room_params = extract_sim_room_params(call, coordinator.model.room_names)

        leading_history: Optional[List[Dict[str, Any]]] = None

        # Fetch history: a stored dataset (``dataset_id``) supplies its
        # snapshotted records directly; otherwise use the JSONL store / Recorder
        # for out-of-buffer windows.  With a dataset the whole snapshot is used
        # (window_spec cleared) since it was captured for exactly this purpose.
        dataset_records = records_for_dataset(coordinator, call.data.get("dataset_id"))
        if dataset_records is not None:
            history = dataset_records
            window_spec = None
        else:
            if window_spec is not None:
                _full, leading_history, history = await get_history_with_leading(
                    hass, coordinator, window_start, window_end,
                )
            else:
                history = await get_history_for_horizon(
                    hass, coordinator, horizon_hours
                )
                from .history_window import history_time_range
                min_ts, max_ts = history_time_range(history)
                if min_ts is not None and max_ts is not None:
                    _full, leading_history, _sim = await get_history_with_leading(
                        hass, coordinator, min_ts, max_ts, leading_hours=6.0,
                    )

        # Patch heat-source copies with the heater power scales currently shown
        # in the UI (falling back to the last auto-identified scales) so the EKF
        # reconstruction uses them even when the user hasn't clicked Apply yet.
        heater_scales = effective_heater_scales(call, coordinator)
        sim_heat_sources = patched_heat_sources(coordinator, heater_scales)

        # Build the SDE used for initial-state estimation (matches sysid run).
        from .controller import HouseThermalSDE  # noqa: PLC0415
        from .simulation.model_patch import build_sim_model  # noqa: PLC0415
        try:
            sim_model = build_sim_model(
                coordinator.model, room_params, coordinator.model.room_names
            )
            init_system = HouseThermalSDE(
                sim_model,
                sim_heat_sources,
                dt,
                sigma_w=sigma_w,
                sigma_v=sigma_v,
                augment_offsets=False,
            )
        except Exception as exc:
            _LOGGER.warning(
                "EKF sim: could not build system for initial-state estimation: %s",
                exc,
            )
            init_system = coordinator.controller._system

        # Estimate optimal initial wall temperatures from a leading calibration
        # window (or a short prefix when no leading data exists).
        try:
            init_state = await estimate_simulation_initial_state(
                hass,
                coordinator,
                history,
                init_system,
                leading_history=leading_history,
                room_params=room_params if room_params else None,
                sigma_w=sigma_w,
                sigma_v=sigma_v,
            )
            for _rn, _tw in init_state.get("t_wall", {}).items():
                room_params.setdefault(_rn, {})["t_wall_initial"] = float(_tw)
            _LOGGER.debug(
                "EKF sim initial state: method=%s calibration_steps=%s",
                init_state.get("method"),
                init_state.get("calibration_steps"),
            )
        except Exception as exc:
            _LOGGER.warning(
                "EKF sim: initial-state estimation failed (%s); "
                "falling back to cached or air-temperature seed.", exc,
            )
            inject_identified_t_wall_initial(room_params, coordinator)

        try:
            result = await hass.async_add_executor_job(
                run_sysid_simulation,
                history,
                coordinator.model,
                sim_heat_sources,
                coordinator.model.room_names,
                dt,
                horizon_steps,
                room_params,
                sigma_w,
                sigma_v,
                window_spec,
            )

            if "error" not in result:
                per_room = result.get("per_room", {})

                # Tag each per-room result with the horizon and window so the
                # sensor can surface them without knowing dt or call.data.
                for room_data in per_room.values():
                    room_data["horizon_steps"] = result.get("horizon_steps", horizon_steps)
                    if window_start is not None:
                        room_data["window_start"] = window_start
                    if window_end is not None:
                        room_data["window_end"] = window_end

                # Filter to requested room if specified.  The frontend
                # sends the room slug (e.g. "bedroom") while per_room keys are
                # the canonical room names (e.g. "Bedroom") stored in the
                # model.  Accept both exact and slug matches so rooms with
                # capital letters or spaces are not silently dropped.
                if room_name_filter:
                    from .naming import slugify as _slugify  # noqa: PLC0415
                    per_room = {
                        k: v for k, v in per_room.items()
                        if k == room_name_filter or _slugify(k) == room_name_filter
                    }

                # Results are surfaced via the per-room SysID diagnostic
                # sensors, so this service stores them on the coordinator and
                # refreshes listeners instead of raising a notification.
                merge_per_room_into_sysid_results(coordinator.sysid_results, per_room)
                coordinator.async_update_listeners()

        except Exception as exc:
            _LOGGER.error("SysID simulation failed: %s", exc, exc_info=True)

    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_SYSID_SIMULATION,
        handle_run_sysid_simulation,
        schema=vol.Schema(
            {
                vol.Optional("room_name"): cv.string,
                vol.Optional("horizon_hours", default=6.0): vol.All(
                    vol.Coerce(float), vol.Range(min=0.5)
                ),
                vol.Optional("sigma_w"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
                vol.Optional("sigma_v"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
                # Explicit identification window as UNIX timestamps [s].
                # When both are provided, horizon_hours is ignored and only data
                # in [window_start, window_end] is used for identification.
                vol.Optional("window_start"): vol.Coerce(float),
                vol.Optional("window_end"): vol.Coerce(float),
                # Per-source heater power scales {source_name: scale}.
                vol.Optional("heater_scales"): {cv.string: vol.Coerce(float)},
            },
            # Allow per-room parameter overrides keyed as <param>_<room_slug>
            # (thermal_mass_<room>, r_external_<room>, internal_gain_<room>,
            # solar_scale_<room>, c_air_fraction_<room>, r_aw_fraction_<room>).
            extra=vol.ALLOW_EXTRA,
        ),
    )

    # ------------------------------------------------------------------
    # Runtime configuration services (called from the Heating Assistant UI)
    # ------------------------------------------------------------------

    _CONTROLLER_TUNING_KEYS = {
        CONF_TRACKING_WEIGHT, CONF_ENERGY_WEIGHT, CONF_ENERGY_PRICE_WEIGHT,
        CONF_SMOOTHING_WEIGHT, CONF_SOFT_CONSTRAINT_WEIGHT,
        CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT, CONF_TERMINAL_WEIGHT,
        CONF_HORIZON, CONF_UPDATE_INTERVAL, CONF_COMFORT_OFFSET,
    }

    _ESTIMATION_PARAM_KEYS = {
        CONF_SIGMA_W, CONF_SIGMA_V, CONF_SIGMA_B,
        CONF_IDENTIFICATION_HORIZON_HOURS,
        CONF_WINDOW_OPEN_DEBOUNCE, CONF_WINDOW_OPEN_CLOSE_SETTLE,
        CONF_WINDOW_OPEN_Q_INFLATION,
    }

    async def handle_update_controller_tuning(call: ServiceCall) -> None:
        """Update MPC controller tuning parameters from the dashboard."""
        coordinator = _get_coordinator(hass)
        updates = {k: v for k, v in call.data.items() if k in _CONTROLLER_TUNING_KEYS}
        if not updates:
            return
        persist_tuning_updates(hass, coordinator, updates)
        coordinator.apply_tuning_updates(updates)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "update_controller_tuning",
        handle_update_controller_tuning,
        schema=vol.Schema(
            {
                vol.Optional(CONF_TRACKING_WEIGHT): vol.Coerce(float),
                vol.Optional(CONF_ENERGY_WEIGHT): vol.Coerce(float),
                vol.Optional(CONF_ENERGY_PRICE_WEIGHT): vol.Coerce(float),
                vol.Optional(CONF_SMOOTHING_WEIGHT): vol.Coerce(float),
                vol.Optional(CONF_SOFT_CONSTRAINT_WEIGHT): vol.Coerce(float),
                vol.Optional(CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT): vol.Coerce(float),
                vol.Optional(CONF_TERMINAL_WEIGHT): vol.Coerce(float),
                vol.Optional(CONF_HORIZON): vol.Coerce(int),
                vol.Optional(CONF_UPDATE_INTERVAL): vol.Coerce(int),
                vol.Optional(CONF_COMFORT_OFFSET): vol.Coerce(float),
            }
        ),
    )

    async def handle_apply_manual_parameters(call: ServiceCall) -> None:
        """Apply manually tuned thermal parameters for a single room."""
        coordinator = _get_coordinator(hass)
        room_name: str = call.data["room_name"]
        thermal_mass: float = call.data["thermal_mass"]
        r_external: float = call.data["r_external"]
        coordinator.apply_manual_parameters(room_name, thermal_mass, r_external)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_MANUAL_PARAMETERS,
        handle_apply_manual_parameters,
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("thermal_mass"): vol.All(
                    vol.Coerce(float), vol.Range(min=1000.0)
                ),
                vol.Required("r_external"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0001)
                ),
            }
        ),
    )

    async def handle_apply_heater_scales(call: ServiceCall) -> None:
        """Apply heater power-scale factors identified by ML estimation.

        When called with no ``scales`` argument the last identified scales
        cached on the coordinator (from the most-recent ``estimate_parameters_ml``
        run) are used.  An explicit ``scales`` dict can override this for
        testing or manual correction.
        """
        coordinator = _get_coordinator(hass)
        scales: Optional[Dict[str, float]] = call.data.get("scales")
        if not scales:
            scales = getattr(coordinator, "_last_identified_heater_scales", {})
        if not scales:
            _LOGGER.warning(
                "apply_heater_scales: no scales provided and none identified yet; "
                "run estimate_parameters_ml first."
            )
            return
        coordinator.apply_heater_scales(scales)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_HEATER_SCALES,
        handle_apply_heater_scales,
        schema=vol.Schema(
            {
                vol.Optional("scales"): {cv.string: vol.Coerce(float)},
            }
        ),
    )

    async def handle_update_estimation_params(call: ServiceCall) -> None:
        """Update state estimation parameters from the dashboard."""
        coordinator = _get_coordinator(hass)
        updates = {k: v for k, v in call.data.items() if k in _ESTIMATION_PARAM_KEYS}
        if not updates:
            return
        persist_tuning_updates(hass, coordinator, updates)
        coordinator.apply_tuning_updates(updates)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "update_estimation_params",
        handle_update_estimation_params,
        schema=vol.Schema(
            {
                vol.Optional(CONF_SIGMA_W): vol.Coerce(float),
                vol.Optional(CONF_SIGMA_V): vol.Coerce(float),
                vol.Optional(CONF_SIGMA_B): vol.Coerce(float),
                vol.Optional(CONF_IDENTIFICATION_HORIZON_HOURS): vol.All(
                    vol.Coerce(float), vol.Range(min=0.5)
                ),
                vol.Optional(CONF_WINDOW_OPEN_DEBOUNCE): vol.Coerce(int),
                vol.Optional(CONF_WINDOW_OPEN_CLOSE_SETTLE): vol.Coerce(int),
                vol.Optional(CONF_WINDOW_OPEN_Q_INFLATION): vol.Coerce(float),
            }
        ),
    )

    # ------------------------------------------------------------------
    # Configuration page services (industrial UI "Configuration" menu)
    # ------------------------------------------------------------------

    async def handle_update_ui_settings(call: ServiceCall) -> None:
        """Persist industrial-panel display settings (plot history / horizon)."""
        coordinator = _get_coordinator(hass)
        updates = {
            k: v
            for k, v in call.data.items()
            if k in (CONF_PLOT_HISTORY_HOURS, CONF_PLOT_FORECAST_HOURS)
        }
        if not updates:
            return
        persist_tuning_updates(hass, coordinator, updates)
        coordinator.apply_tuning_updates(updates)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "update_ui_settings",
        handle_update_ui_settings,
        schema=vol.Schema(
            {
                vol.Optional(CONF_PLOT_HISTORY_HOURS): vol.All(
                    vol.Coerce(float), vol.Range(min=1.0)
                ),
                vol.Optional(CONF_PLOT_FORECAST_HOURS): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
            }
        ),
    )

    _SYSTEM_PARAM_KEYS = {CONF_IDENTIFICATION_HISTORY_DAYS}

    async def handle_update_system_params(call: ServiceCall) -> None:
        """Persist system-level parameters (e.g. history retention)."""
        coordinator = _get_coordinator(hass)
        updates = {k: v for k, v in call.data.items() if k in _SYSTEM_PARAM_KEYS}
        if not updates:
            return
        persist_tuning_updates(hass, coordinator, updates)
        coordinator.apply_tuning_updates(updates)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "update_system_params",
        handle_update_system_params,
        schema=vol.Schema(
            {
                vol.Optional(CONF_IDENTIFICATION_HISTORY_DAYS): vol.All(
                    vol.Coerce(int), vol.Range(min=7)
                ),
            }
        ),
    )

    async def handle_update_rooms(call: ServiceCall) -> None:
        """Replace the configured room list (triggers an integration reload).

        When ``renames`` is supplied ({old_name: new_name}) all data keyed by
        the old room name — persisted state, estimated parameters, heat-source
        links, inter-room connections and the room's entities — is migrated to
        the new name so nothing is left orphaned.
        """
        rooms = call.data["rooms"]
        renames = {
            str(k): str(v)
            for k, v in (call.data.get("renames") or {}).items()
            if k and v and str(k) != str(v)
        }
        if not renames:
            coordinator = _get_coordinator(hass)
            write_entry_config(hass, {CONF_ROOMS: rooms}, coordinator=coordinator)
            return

        coordinator = _get_coordinator(hass)
        entry = hass.config_entries.async_get_entry(coordinator._entry.entry_id)
        if entry is None:
            return

        rooms = _apply_renames_to_connections(rooms, renames)
        room_names = [r.get(CONF_ROOM_NAME) for r in rooms if isinstance(r, dict)]
        # Carry the rename map so the post-reload setup can also remap the
        # in-memory runtime state (room/schedule enabled, base setpoints).
        hass.data.setdefault(DOMAIN, {})["_pending_room_renames"] = renames

        try:
            _migrate_room_entities(hass, entry, renames, room_names)
        except Exception:  # pragma: no cover - defensive
            _LOGGER.warning(
                "Heating Assistant: room-rename entity migration failed",
                exc_info=True,
            )

        # Build migration bases.  Heat sources may live exclusively in
        # entry.options (options-first write path), so promote them into the
        # data dict before migration so CONF_HEAT_SOURCES links are always
        # updated regardless of which store holds them.
        data_base = dict(entry.data)
        if CONF_HEAT_SOURCES not in data_base and CONF_HEAT_SOURCES in entry.options:
            data_base[CONF_HEAT_SOURCES] = entry.options[CONF_HEAT_SOURCES]
        data_base[CONF_ROOMS] = rooms

        new_data = _migrate_room_name_data(data_base, renames)
        new_options = _migrate_room_name_data(
            {**dict(entry.options), CONF_ROOMS: rooms}, renames
        )
        new_options[CONF_ROOMS] = rooms
        hass.config_entries.async_update_entry(
            entry, data=new_data, options=new_options
        )

    # The Configuration UI sends back the WHOLE rooms list — the room being
    # edited plus every other room carried verbatim from the backend.  Those
    # carried rooms already passed the canonical room schema when first written,
    # and they hold values the round-trip schema cannot reproduce: stored weekday
    # indices are ints (not the schema's ``[str]``), schedule periods carry
    # editor-only keys, and identification writes out-of-schema fields like
    # ``solar_scale``.  Re-imposing the room schema on this data only rejected or
    # mangled valid rooms, so the service accepts the rooms list as opaque dicts
    # and leaves validation/coercion to the coordinator (``build_house_model``),
    # which already tolerates partial rooms and now drops dangling sub-records.
    hass.services.async_register(
        DOMAIN,
        "update_rooms",
        handle_update_rooms,
        schema=vol.Schema(
            {
                vol.Required(CONF_ROOMS): [dict],
                # Optional {old_name: new_name} map so a rename migrates all
                # data keyed by the old room name.
                vol.Optional("renames"): {cv.string: cv.string},
            }
        ),
    )

    async def handle_update_heat_sources(call: ServiceCall) -> None:
        """Replace the configured heat-source list (triggers a reload)."""
        coordinator = _get_coordinator(hass)
        sources = call.data["heat_sources"]
        write_entry_config(hass, {CONF_HEAT_SOURCES: sources}, coordinator=coordinator)

    hass.services.async_register(
        DOMAIN,
        "update_heat_sources",
        handle_update_heat_sources,
        # Heat sources are likewise carried verbatim from the backend and may
        # hold out-of-schema fields (e.g. an identified ``power_scale``), so the
        # list is accepted as opaque dicts; the coordinator validates on build.
        schema=vol.Schema({vol.Required(CONF_HEAT_SOURCES): [dict]}),
    )

    async def handle_update_system_config(call: ServiceCall) -> None:
        """Update environment entities and site location from the dashboard.

        Entity keys are applied in-place by the coordinator's runtime
        reconfiguration; an empty string clears the entity.
        """
        coordinator = _get_coordinator(hass)
        _entity_keys = (
            CONF_OUTDOOR_TEMP_ENTITY,
            CONF_WEATHER_ENTITY,
            CONF_SOLAR_RADIATION_ENTITY,
            CONF_PRICE_ENTITY,
        )
        updates: Dict[str, Any] = {}
        for key in _entity_keys:
            if key in call.data:
                # Normalise to "" (cleared) rather than None so the coordinator's
                # str() coercion never produces the literal string "None".
                updates[key] = (call.data.get(key) or "").strip()
        for key in (CONF_LATITUDE, CONF_LONGITUDE):
            if key in call.data:
                updates[key] = float(call.data[key])
        if not updates:
            return
        persist_tuning_updates(hass, coordinator, updates)
        coordinator.apply_tuning_updates(updates)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "update_system_config",
        handle_update_system_config,
        schema=vol.Schema(
            {
                vol.Optional(CONF_OUTDOOR_TEMP_ENTITY): cv.string,
                vol.Optional(CONF_WEATHER_ENTITY): cv.string,
                vol.Optional(CONF_SOLAR_RADIATION_ENTITY): cv.string,
                vol.Optional(CONF_PRICE_ENTITY): cv.string,
                vol.Optional(CONF_LATITUDE): vol.All(
                    vol.Coerce(float), vol.Range(min=-90.0, max=90.0)
                ),
                vol.Optional(CONF_LONGITUDE): vol.All(
                    vol.Coerce(float), vol.Range(min=-180.0, max=180.0)
                ),
            }
        ),
    )

    async def handle_reset_estimated_parameters(call: ServiceCall) -> None:
        """Reset the active model back to configured (YAML) default parameters."""
        coordinator = _get_coordinator(hass)
        coordinator.reset_estimated_parameters()
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_ESTIMATED_PARAMETERS,
        handle_reset_estimated_parameters,
        schema=vol.Schema({}),
    )

    # ------------------------------------------------------------------
    # Parameter history services
    # ------------------------------------------------------------------

    async def handle_store_identified_parameters(call: ServiceCall) -> None:
        """Store identified parameters with history tracking."""
        coordinator = _get_coordinator(hass)
        room_name: str = _resolve_room(coordinator, call.data["room_name"])
        thermal_mass: float = call.data["thermal_mass"]
        r_external: float = call.data["r_external"]
        source: str = call.data.get("source", "manual")
        rmse: Optional[float] = call.data.get("rmse")
        heater_scales = call.data.get("heater_scales")
        coordinator.store_identified_parameters(
            room_name, thermal_mass, r_external, source=source, rmse=rmse,
            internal_gain=call.data.get("internal_gain"),
            solar_scale=call.data.get("solar_scale"),
            c_air_fraction=call.data.get("c_air_fraction"),
            r_aw_fraction=call.data.get("r_aw_fraction"),
            heater_scales=dict(heater_scales) if heater_scales else None,
        )
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "store_identified_parameters",
        handle_store_identified_parameters,
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("thermal_mass"): vol.All(
                    vol.Coerce(float), vol.Range(min=1000.0)
                ),
                vol.Required("r_external"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0001)
                ),
                vol.Optional("source", default="manual"): vol.In(["ml", "manual"]),
                vol.Optional("rmse"): vol.Coerce(float),
                vol.Optional("internal_gain"): vol.Coerce(float),
                vol.Optional("solar_scale"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
                vol.Optional("c_air_fraction"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=1.0)
                ),
                vol.Optional("r_aw_fraction"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=1.0)
                ),
                # Per-source heater power scales {source_name: scale}.
                vol.Optional("heater_scales"): {cv.string: vol.Coerce(float)},
            }
        ),
    )

    async def handle_revert_parameters(call: ServiceCall) -> None:
        """Revert parameters to a previous history entry."""
        coordinator = _get_coordinator(hass)
        room_name: str = _resolve_room(coordinator, call.data["room_name"])
        history_index: int = call.data["history_index"]
        coordinator.revert_parameters(room_name, history_index)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "revert_parameters",
        handle_revert_parameters,
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("history_index"): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=9)
                ),
            }
        ),
    )

    async def handle_delete_parameter_history(call: ServiceCall) -> None:
        """Delete a single entry from the parameter history."""
        coordinator = _get_coordinator(hass)
        history_index: int = call.data["history_index"]
        coordinator.delete_parameter_history(history_index)
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        "delete_parameter_history",
        handle_delete_parameter_history,
        schema=vol.Schema(
            {
                vol.Required("history_index"): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=9)
                ),
            }
        ),
    )

    async def handle_regenerate_dashboard(call: ServiceCall) -> ServiceResponse:
        """Regenerate the Heating Assistant Lovelace dashboard.

        When ``dry_run`` is true the generated YAML is returned in the
        service response without touching the filesystem. Otherwise it is
        written to ``<config>/dashboards/<filename>`` (relative paths
        outside the config directory are rejected).
        """
        import os

        from .dashboard import (
            DASHBOARD_VARIANT_CLASSIC,
            DASHBOARD_VARIANT_INDUSTRIAL,
            build_dashboard_variant_from_coordinator,
            dashboard_to_yaml,
        )

        coordinator = _get_coordinator(hass)
        # ``build_dashboard_from_coordinator`` is a pure read over coordinator
        # state; run it inline to avoid racing with the next update cycle.
        variant = str(call.data.get("variant") or DASHBOARD_VARIANT_CLASSIC)
        if variant not in {DASHBOARD_VARIANT_CLASSIC, DASHBOARD_VARIANT_INDUSTRIAL}:
            raise ValueError("variant must be 'classic' or 'industrial'")
        dashboard = build_dashboard_variant_from_coordinator(coordinator, variant=variant)
        yaml_text: str = await hass.async_add_executor_job(
            dashboard_to_yaml, dashboard
        )

        dry_run = bool(call.data.get("dry_run", False))
        default_filename = (
            DEFAULT_INDUSTRIAL_DASHBOARD_FILENAME
            if variant == DASHBOARD_VARIANT_INDUSTRIAL
            else DEFAULT_DASHBOARD_FILENAME
        )
        filename = str(call.data.get("filename") or default_filename)
        write_path: str | None = None

        if not dry_run:
            base_dir = hass.config.path("dashboards")
            safe_filename = os.path.basename(filename)
            if not safe_filename or safe_filename != filename:
                raise ValueError(
                    "filename must be a plain file name, not a path"
                )
            write_path = os.path.join(base_dir, safe_filename)

            def _write() -> None:
                os.makedirs(base_dir, exist_ok=True)
                with open(write_path, "w", encoding="utf-8") as fh:
                    fh.write(yaml_text)

            await hass.async_add_executor_job(_write)

        # The write path is returned in the service response (``written_to``)
        # for programmatic callers; no persistent notification is raised.
        return {
            "yaml": yaml_text,
            "variant": variant,
            "rooms": [v["title"] for v in dashboard["views"] if v.get("subview")],
            "written_to": write_path,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_REGENERATE_DASHBOARD,
        handle_regenerate_dashboard,
        schema=vol.Schema(
            {
                vol.Optional("dry_run", default=False): cv.boolean,
                vol.Optional("filename"): cv.string,
                vol.Optional("variant"): vol.In(["classic", "industrial"]),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    # ------------------------------------------------------------------
    # System-identification experiments and stored datasets
    # ------------------------------------------------------------------

    def _resolve_room(coordinator: "HeatingAssistantCoordinator", name: str) -> str:
        """Return the canonical room name for a name-or-slug, or raise."""
        from .naming import slugify as _slugify

        for rn in coordinator.model.room_names:
            if rn == name or _slugify(rn) == name:
                return rn
        raise ValueError(f"Room '{name}' not found in configuration")

    async def handle_schedule_experiment(call: ServiceCall) -> ServiceResponse:
        """Schedule a system-identification experiment for one room/time window."""
        from .naming import slugify as _slugify
        from .experiments import Experiment, validate_signal_params
        from .const import (
            DEFAULT_EXCITATION_PERIOD_S,
            DEFAULT_EXCITATION_STEP_PCT,
            DEFAULT_EXCITATION_TYPE,
            DEFAULT_EXPERIMENT_MAX_TEMP,
            DEFAULT_EXPERIMENT_MIN_TEMP,
            DEFAULT_EXPERIMENT_SETTLE_S,
            MAX_EXPERIMENT_DURATION_S,
        )

        coordinator = _get_coordinator(hass)
        canonical = _resolve_room(coordinator, call.data["room_name"])

        start_ts = float(call.data["start"])
        end_ts = float(call.data["end"])
        if end_ts <= start_ts:
            raise ValueError("Experiment end must be after start")
        duration = end_ts - start_ts
        if duration > MAX_EXPERIMENT_DURATION_S:
            raise ValueError("Experiment duration exceeds the 7-day maximum")

        signal_type = str(call.data.get("signal_type", DEFAULT_EXCITATION_TYPE))
        step_pct = float(call.data.get("step_pct", DEFAULT_EXCITATION_STEP_PCT))
        period_s = float(call.data.get("period_s", DEFAULT_EXCITATION_PERIOD_S))
        validate_signal_params(signal_type, step_pct, period_s)

        # Settle / response buffer: excitation stops this long before the window
        # ends so the heater's influence is absorbed within the captured data.
        # Default to the configured buffer but never more than half the window so
        # a short experiment still gets meaningful excitation; an explicit value
        # must leave at least some excitation time.
        settle_raw = call.data.get("settle_s")
        if settle_raw is None:
            settle_s = min(DEFAULT_EXPERIMENT_SETTLE_S, duration / 2.0)
        else:
            settle_s = float(settle_raw)
            if settle_s < 0.0:
                raise ValueError("settle_s must be non-negative")
            if settle_s >= duration:
                raise ValueError("settle_s must be shorter than the experiment duration")

        exp = Experiment(
            room_name=canonical,
            room_slug=_slugify(canonical),
            start_ts=start_ts,
            end_ts=end_ts,
            name=str(call.data.get("name", "")),
            signal_type=signal_type,
            step_pct=step_pct,
            period_s=period_s,
            settle_s=settle_s,
            min_temp=float(call.data.get("min_temp", DEFAULT_EXPERIMENT_MIN_TEMP)),
            max_temp=float(call.data.get("max_temp", DEFAULT_EXPERIMENT_MAX_TEMP)),
            auto_save=bool(call.data.get("auto_save", True)),
        )
        coordinator.schedule_experiment(exp)
        coordinator.async_update_listeners()
        return {"experiment_id": exp.id}

    hass.services.async_register(
        DOMAIN,
        SERVICE_SCHEDULE_EXPERIMENT,
        handle_schedule_experiment,
        schema=vol.Schema(
            {
                vol.Required("room_name"): cv.string,
                vol.Required("start"): vol.Coerce(float),
                vol.Required("end"): vol.Coerce(float),
                vol.Optional("name"): cv.string,
                vol.Optional("signal_type"): vol.In(list(EXCITATION_TYPES)),
                vol.Optional("step_pct"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=1.0)
                ),
                vol.Optional("period_s"): vol.All(
                    vol.Coerce(float), vol.Range(min=60.0)
                ),
                vol.Optional("settle_s"): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
                vol.Optional("min_temp"): vol.Coerce(float),
                vol.Optional("max_temp"): vol.Coerce(float),
                vol.Optional("auto_save"): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def handle_cancel_experiment(call: ServiceCall) -> None:
        """Cancel (or remove) a scheduled / running experiment."""
        coordinator = _get_coordinator(hass)
        coordinator.cancel_experiment(call.data["experiment_id"])
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        SERVICE_CANCEL_EXPERIMENT,
        handle_cancel_experiment,
        schema=vol.Schema({vol.Required("experiment_id"): cv.string}),
    )

    async def handle_delete_experiment(call: ServiceCall) -> None:
        """Delete an experiment outright, regardless of its status."""
        coordinator = _get_coordinator(hass)
        coordinator.delete_experiment(call.data["experiment_id"])
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_EXPERIMENT,
        handle_delete_experiment,
        schema=vol.Schema({vol.Required("experiment_id"): cv.string}),
    )

    async def handle_create_dataset(call: ServiceCall) -> ServiceResponse:
        """Snapshot a custom data window into a named, permanent dataset."""
        from .datasets import build_dataset
        from .history_window import select_window_by_timestamps
        from .naming import slugify as _slugify
        from .const import DATASET_SOURCE_MANUAL

        coordinator = _get_coordinator(hass)
        if coordinator.dataset_store is None:
            raise ValueError("Dataset store is not available")

        name = str(call.data["name"]).strip()
        if not name:
            raise ValueError("Dataset name must not be empty")
        window_start = float(call.data["window_start"])
        window_end = float(call.data["window_end"])
        if window_end <= window_start:
            raise ValueError("window_end must be after window_start")

        canonical: Optional[str] = None
        room_name = call.data.get("room_name")
        if room_name:
            canonical = _resolve_room(coordinator, room_name)
        room_slug = _slugify(canonical) if canonical else None

        # Pull the actual records for the window (JSONL store → Recorder
        # fallback), then clip to the exact bounds.
        records = await get_history_for_window(
            hass, coordinator, window_start, window_end
        )
        records = select_window_by_timestamps(records, window_start, window_end)
        if not records:
            raise ValueError("No observation data found in the selected window")

        dataset = build_dataset(
            name,
            records,
            room_name=canonical,
            room_slug=room_slug,
            source=DATASET_SOURCE_MANUAL,
            notes=str(call.data.get("notes", "")),
            window_start=window_start,
            window_end=window_end,
        )
        await coordinator.dataset_store.async_add(dataset)
        coordinator.async_update_listeners()
        return {
            "dataset_id": dataset["id"],
            "record_count": dataset["record_count"],
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_DATASET,
        handle_create_dataset,
        schema=vol.Schema(
            {
                vol.Required("name"): cv.string,
                vol.Required("window_start"): vol.Coerce(float),
                vol.Required("window_end"): vol.Coerce(float),
                vol.Optional("room_name"): cv.string,
                vol.Optional("notes"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def handle_delete_dataset(call: ServiceCall) -> None:
        """Delete a stored dataset by id."""
        coordinator = _get_coordinator(hass)
        if coordinator.dataset_store is None:
            return
        await coordinator.dataset_store.async_delete(call.data["dataset_id"])
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_DATASET,
        handle_delete_dataset,
        schema=vol.Schema({vol.Required("dataset_id"): cv.string}),
    )
