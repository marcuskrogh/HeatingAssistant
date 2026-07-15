"""Runtime (non-structural) configuration updates for the coordinator."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, Dict, Set

from ..const import (
    CONF_COMFORT_OFFSET,
    CONF_ENERGY_PRICE_WEIGHT,
    CONF_ENERGY_WEIGHT,
    CONF_ESTIMATED_PARAMS,
    CONF_HEAT_SOURCES,
    CONF_HORIZON,
    CONF_IDENTIFICATION_HISTORY_DAYS,
    CONF_IDENTIFICATION_HORIZON_HOURS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_PERSISTED_COMFORT_OFFSETS,
    CONF_PERSISTED_ROOM_ENABLED,
    CONF_PERSISTED_SCHEDULES,
    CONF_PERSISTED_SETPOINTS,
    CONF_PERSISTED_SYSTEM_ENABLED,
    CONF_PLOT_FORECAST_HOURS,
    CONF_PLOT_HISTORY_HOURS,
    CONF_PRICE_ENTITY,
    CONF_ROOMS,
    CONF_SIGMA_B,
    CONF_SIGMA_V,
    CONF_SIGMA_W,
    CONF_SMOOTHING_WEIGHT,
    CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT,
    CONF_SOFT_CONSTRAINT_WEIGHT,
    CONF_TERMINAL_WEIGHT,
    CONF_TRACKING_WEIGHT,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_OPEN_CLOSE_SETTLE,
    CONF_WINDOW_OPEN_DEBOUNCE,
    CONF_WINDOW_OPEN_Q_INFLATION,
    CONF_SOLAR_RADIATION_ENTITY,
)

if TYPE_CHECKING:
    from .core import HeatingAssistantCoordinator

RELOAD_REQUIRED_CONFIG_KEYS: Set[str] = {
    CONF_ROOMS,
    CONF_HEAT_SOURCES,
    CONF_HORIZON,
    CONF_UPDATE_INTERVAL,
}

PERSISTED_STATE_KEYS: Set[str] = {
    CONF_PERSISTED_SETPOINTS,
    CONF_PERSISTED_SCHEDULES,
    CONF_ESTIMATED_PARAMS,
    CONF_PERSISTED_COMFORT_OFFSETS,
    CONF_PERSISTED_ROOM_ENABLED,
    CONF_PERSISTED_SYSTEM_ENABLED,
}

RUNTIME_RECONFIG_KEYS: Set[str] = {
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_SOLAR_RADIATION_ENTITY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_TRACKING_WEIGHT,
    CONF_ENERGY_WEIGHT,
    CONF_SMOOTHING_WEIGHT,
    CONF_SOFT_CONSTRAINT_WEIGHT,
    CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT,
    CONF_TERMINAL_WEIGHT,
    CONF_SIGMA_W,
    CONF_SIGMA_V,
    CONF_SIGMA_B,
    CONF_WINDOW_OPEN_DEBOUNCE,
    CONF_WINDOW_OPEN_CLOSE_SETTLE,
    CONF_WINDOW_OPEN_Q_INFLATION,
    CONF_PRICE_ENTITY,
    CONF_PLOT_HISTORY_HOURS,
    CONF_PLOT_FORECAST_HOURS,
    CONF_IDENTIFICATION_HISTORY_DAYS,
}


def apply_runtime_reconfiguration(
    coordinator: HeatingAssistantCoordinator, config: Dict[str, Any]
) -> bool:
    """Queue non-structural config changes for the next regular tick.

    Returns ``True`` when the change can be applied in-place, ``False``
    when a full integration reload is required.
    """
    if not hasattr(coordinator, "_last_runtime_config"):
        coordinator._last_runtime_config = {}
    if not hasattr(coordinator, "_pending_runtime_reconfiguration"):
        coordinator._pending_runtime_reconfiguration = {}

    new_config = dict(config)
    old_persisted = coordinator._last_runtime_config.get(CONF_PERSISTED_SCHEDULES)
    new_persisted = new_config.get(CONF_PERSISTED_SCHEDULES)
    changed_keys = {
        key
        for key in set(coordinator._last_runtime_config) | set(new_config)
        if coordinator._last_runtime_config.get(key) != new_config.get(key)
    }
    changed_keys -= PERSISTED_STATE_KEYS
    coordinator._last_runtime_config = new_config

    if old_persisted != new_persisted:
        from . import schedule_control

        schedule_control.apply_persisted_schedules(
            coordinator, dict(new_persisted or {})
        )

    if not changed_keys:
        return True

    if changed_keys & RELOAD_REQUIRED_CONFIG_KEYS:
        return False

    for key in changed_keys & RUNTIME_RECONFIG_KEYS:
        coordinator._pending_runtime_reconfiguration[key] = new_config.get(key)
    return True


def apply_pending_runtime_reconfiguration(coordinator: HeatingAssistantCoordinator) -> None:
    """Apply queued runtime config updates at the start of a normal cycle."""
    pending_state = getattr(coordinator, "_pending_runtime_reconfiguration", None)
    if not pending_state:
        return

    pending = dict(pending_state)
    coordinator._pending_runtime_reconfiguration.clear()
    rebuild_controller = False

    if CONF_PRICE_ENTITY in pending:
        coordinator._price_entity = (
            str(pending.get(CONF_PRICE_ENTITY) or "") or None
        )
    if CONF_PLOT_HISTORY_HOURS in pending:
        coordinator._plot_history_hours = float(
            pending.get(CONF_PLOT_HISTORY_HOURS, coordinator._plot_history_hours)
        )
    if CONF_PLOT_FORECAST_HOURS in pending:
        coordinator._plot_forecast_hours = float(
            pending.get(CONF_PLOT_FORECAST_HOURS, coordinator._plot_forecast_hours)
        )
    if CONF_OUTDOOR_TEMP_ENTITY in pending:
        coordinator._outdoor_entity = str(
            pending.get(CONF_OUTDOOR_TEMP_ENTITY, "")
        )
    if CONF_WEATHER_ENTITY in pending:
        coordinator._weather_entity = str(pending.get(CONF_WEATHER_ENTITY, ""))
    if CONF_SOLAR_RADIATION_ENTITY in pending:
        coordinator._solar_radiation_entity = (
            str(pending.get(CONF_SOLAR_RADIATION_ENTITY, "")) or None
        )
    if CONF_LATITUDE in pending:
        coordinator._latitude = float(
            pending.get(CONF_LATITUDE, coordinator._latitude)
        )
    if CONF_LONGITUDE in pending:
        coordinator._longitude = float(
            pending.get(CONF_LONGITUDE, coordinator._longitude)
        )
    if CONF_TRACKING_WEIGHT in pending:
        coordinator._tracking_weight = float(
            pending.get(CONF_TRACKING_WEIGHT, coordinator._tracking_weight)
        )
        rebuild_controller = True
    if CONF_SOFT_CONSTRAINT_WEIGHT in pending:
        coordinator._soft_constraint_weight = float(
            pending.get(
                CONF_SOFT_CONSTRAINT_WEIGHT, coordinator._soft_constraint_weight
            )
        )
        rebuild_controller = True
    if CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT in pending:
        coordinator._soft_constraint_linear_weight = float(
            pending.get(
                CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT,
                coordinator._soft_constraint_linear_weight,
            )
        )
        rebuild_controller = True
    if CONF_ENERGY_WEIGHT in pending:
        coordinator._energy_weight = float(
            pending.get(CONF_ENERGY_WEIGHT, coordinator._energy_weight)
        )
        rebuild_controller = True
    if CONF_SMOOTHING_WEIGHT in pending:
        coordinator._smoothing_weight = float(
            pending.get(CONF_SMOOTHING_WEIGHT, coordinator._smoothing_weight)
        )
        rebuild_controller = True
    if CONF_TERMINAL_WEIGHT in pending:
        coordinator._terminal_weight = float(
            pending.get(CONF_TERMINAL_WEIGHT, coordinator._terminal_weight)
        )
        rebuild_controller = True
    if CONF_SIGMA_W in pending:
        coordinator._sigma_w = float(
            pending.get(CONF_SIGMA_W, coordinator._sigma_w)
        )
        rebuild_controller = True
    if CONF_SIGMA_V in pending:
        coordinator._sigma_v = float(
            pending.get(CONF_SIGMA_V, coordinator._sigma_v)
        )
        rebuild_controller = True
    if CONF_SIGMA_B in pending:
        coordinator._sigma_b = float(
            pending.get(CONF_SIGMA_B, coordinator._sigma_b)
        )
        rebuild_controller = True
    if CONF_IDENTIFICATION_HORIZON_HOURS in pending:
        coordinator._identification_horizon_hours = float(
            pending.get(
                CONF_IDENTIFICATION_HORIZON_HOURS,
                coordinator._identification_horizon_hours,
            )
        )
    if CONF_IDENTIFICATION_HISTORY_DAYS in pending:
        coordinator._identification_history_days = int(
            pending.get(
                CONF_IDENTIFICATION_HISTORY_DAYS,
                coordinator._identification_history_days,
            )
        )
        if coordinator.id_history_store is not None:
            coordinator.id_history_store.update_retention_days(
                coordinator._identification_history_days
            )
    if CONF_WINDOW_OPEN_DEBOUNCE in pending:
        coordinator._window_open_debounce = float(
            pending.get(
                CONF_WINDOW_OPEN_DEBOUNCE, coordinator._window_open_debounce
            )
        )
    if CONF_WINDOW_OPEN_CLOSE_SETTLE in pending:
        coordinator._window_open_close_settle = float(
            pending.get(
                CONF_WINDOW_OPEN_CLOSE_SETTLE,
                coordinator._window_open_close_settle,
            )
        )
    if CONF_WINDOW_OPEN_Q_INFLATION in pending:
        coordinator._window_open_q_inflation = float(
            pending.get(
                CONF_WINDOW_OPEN_Q_INFLATION,
                coordinator._window_open_q_inflation,
            )
        )
    if CONF_HORIZON in pending:
        coordinator._horizon = int(pending.get(CONF_HORIZON, coordinator._horizon))
        rebuild_controller = True
    if CONF_ENERGY_PRICE_WEIGHT in pending:
        coordinator._energy_price_weight = float(
            pending.get(
                CONF_ENERGY_PRICE_WEIGHT, coordinator._energy_price_weight
            )
        )
        rebuild_controller = True
    if CONF_UPDATE_INTERVAL in pending:
        coordinator._update_interval_s = int(
            pending.get(CONF_UPDATE_INTERVAL, coordinator._update_interval_s)
        )
        coordinator.update_interval = timedelta(seconds=coordinator._update_interval_s)
    if CONF_COMFORT_OFFSET in pending:
        new_offset = float(pending.get(CONF_COMFORT_OFFSET, 2.0))
        for room_name in coordinator._room_comfort_offset:
            coordinator._room_comfort_offset[room_name] = new_offset
        rebuild_controller = True

    if rebuild_controller:
        coordinator._build_controller()


def apply_tuning_updates(
    coordinator: HeatingAssistantCoordinator, updates: Dict[str, Any]
) -> None:
    """Apply tuning parameter changes immediately (called from services)."""
    for key, value in updates.items():
        coordinator._pending_runtime_reconfiguration[key] = value
    apply_pending_runtime_reconfiguration(coordinator)
