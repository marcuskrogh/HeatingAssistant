"""Lightweight live UI refresh between scheduled MPC ticks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .core import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)


def refresh_live_state(coordinator: HeatingAssistantCoordinator) -> Optional[float]:
    """Re-read live inputs and recompute cheap visualisation state.

    This is the lightweight counterpart to ``_async_update_data`` used by
    the fast UI refresh and the window-override push.  It updates everything
    the dashboard needs to stay live between scheduled MPC ticks WITHOUT
    running the MPC controller or advancing the EKF.

    Returns the effective outdoor temperature (the fresh reading, or the last
    valid value when the entity is transiently unavailable), or ``None`` when
    no valid reading has ever been obtained.
    """
    from datetime import datetime, timezone

    coordinator.now_utc = datetime.now(tz=timezone.utc)

    coordinator.measured_temperatures = {}
    for room_name, entity_ids in coordinator._temp_sensors.items():
        readings: List[float] = []
        for entity_id in entity_ids:
            state = coordinator.hass.states.get(entity_id)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    readings.append(float(state.state))
                except (ValueError, TypeError):
                    pass
        if readings:
            avg = sum(readings) / len(readings)
            coordinator.model.rooms[room_name].temperature = avg
            coordinator.measured_temperatures[room_name] = avg
    coordinator._rooms_ever_measured.update(coordinator.measured_temperatures.keys())

    try:
        coordinator._apply_schedule(coordinator.now_utc.astimezone())
    except Exception:
        _LOGGER.debug("UI refresh: schedule apply failed", exc_info=True)

    coordinator._update_window_state_machine(coordinator.now_utc)

    _outdoor = coordinator._read_outdoor_temp()
    coordinator.outdoor_temp = _outdoor
    if _outdoor is not None:
        coordinator._last_valid_outdoor_temp = _outdoor
    outdoor_temp = (
        _outdoor if _outdoor is not None else coordinator._last_valid_outdoor_temp
    )

    coordinator._publish_current_solar_gains()

    if outdoor_temp is not None:
        try:
            coordinator.heat_flows = coordinator.model.compute_heat_flows(outdoor_temp)
        except Exception:
            _LOGGER.debug("UI refresh: heat-flow recompute failed", exc_info=True)

    return outdoor_temp


async def async_refresh_ui(coordinator: HeatingAssistantCoordinator) -> None:
    """Refresh live UI state without running the MPC."""
    await coordinator._ensure_runtime_state_loaded()
    try:
        outdoor_temp = refresh_live_state(coordinator)
    except Exception:
        _LOGGER.warning("Fast UI refresh failed", exc_info=True)
        return
    if outdoor_temp is not None:
        try:
            await coordinator._reapply_climate_setpoints(outdoor_temp)
        except Exception:
            _LOGGER.debug(
                "Fast UI refresh: setpoint re-apply failed", exc_info=True,
            )
        try:
            await coordinator._async_reconcile_actuation(outdoor_temp)
        except Exception:
            _LOGGER.debug(
                "Fast UI refresh: actuation watchdog failed", exc_info=True,
            )
    coordinator.async_update_listeners()
