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

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SERVICE_RELOAD
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
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
from .services.context import get_coordinator
from .services.history_access import (
    records_for_dataset,
    records_for_datasets,
)
from .services.register import register_all_services
from .services.configuration import (
    DEFAULT_DASHBOARD_FILENAME,
    DEFAULT_INDUSTRIAL_DASHBOARD_FILENAME,
    SERVICE_REGENERATE_DASHBOARD,
)
from .services.diagnostics import SERVICE_COMPUTE_LOGLIK_SLICE
from .services.identification import (
    SERVICE_APPLY_HEATER_SCALES,
    SERVICE_APPLY_MANUAL_PARAMETERS,
    SERVICE_ESTIMATE_PARAMETERS,
    SERVICE_RESET_ESTIMATED_PARAMETERS,
    SERVICE_RUN_SYSID_SIMULATION,
    SERVICE_SIMULATE_THERMAL_RESPONSE,
)
from .websocket_api import register_websocket_api
from .services.simulation import (
    compute_open_loop_rmse_by_horizon,
    merge_per_room_into_sysid_results,
    open_loop_t_wall_initial_dict,
)
from .yaml_merge import MergedEntry as _MergedEntry, merge_yaml_into_entry_data as _merge_yaml_into_entry_data

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["climate", "sensor", "button", "datetime"]

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
                    "js_url": "/ha-industrial-panel/industrial-dashboard.js?v=90",
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

    from .lovelace_dashboard import build_dashboard_from_coordinator, dashboard_to_yaml

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

    from .lovelace_dashboard import (
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


def _register_services(hass: HomeAssistant) -> None:
    """Register domain services for setup assistance."""
    register_all_services(hass)


from .room_migration import (
    _ROOM_KEYED_STATE_KEYS,
    _apply_renames_to_connections,
    _migrate_room_entities,
    _migrate_room_name_data,
    _remap_keys,
)

# Backward-compatible private aliases for tests and legacy imports.
_records_for_dataset = records_for_dataset
_records_for_datasets = records_for_datasets
_compute_open_loop_rmse_by_horizon = compute_open_loop_rmse_by_horizon
_merge_per_room_into_sysid_results = merge_per_room_into_sysid_results
_open_loop_t_wall_initial_dict = open_loop_t_wall_initial_dict
