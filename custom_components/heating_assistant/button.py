"""
Heating Assistant – Button platform.

Provides a single button entity that triggers maximum-likelihood thermal
parameter estimation (via the accumulated history buffer) with one press.
The estimated parameters are applied to the running model automatically and
the results are reflected in the per-room diagnostic sensors.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HeatingAssistantCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Heating Assistant button entities from a config entry."""
    coordinator: HeatingAssistantCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        EstimateParametersButton(coordinator),
        ResetParametersButton(coordinator),
        RunSysIdWithWindowButton(coordinator),
    ])


class EstimateParametersButton(ButtonEntity):
    """
    Button that triggers maximum-likelihood thermal parameter estimation.

    Pressing this button runs the Kalman-filter PED log-likelihood
    optimisation over the rolling observation history.  Estimated values for
    ``thermal_mass`` and ``r_external`` are applied to the live model and
    surfaced through the per-room parameter / diagnostic sensors.
    """

    _attr_icon = "mdi:chart-bell-curve-cumulative"
    _attr_has_entity_name = False

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – Estimate Parameters"
        self._attr_unique_id = f"{DOMAIN}_estimate_parameters_button"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose buffer fill level so users know when data is sufficient."""
        buf_size = len(self._coordinator.history_buffer)
        from .parameter_estimator import MIN_HISTORY_STEPS
        return {
            "history_steps": buf_size,
            "min_steps_required": MIN_HISTORY_STEPS,
            "ready": buf_size >= MIN_HISTORY_STEPS,
        }

    async def async_press(self) -> None:
        """Handle button press – run ML parameter estimation."""
        await self.hass.services.async_call(
            DOMAIN,
            "estimate_parameters_ml",
            {"apply_parameters": True},
            blocking=False,
        )


class ResetParametersButton(ButtonEntity):
    """
    Button that resets all estimated parameters back to the configured
    (YAML / config-entry default) values and removes the persisted snapshot.

    Pressing this button is useful when a previously estimated parameter set
    turns out to be wrong or when the user wants to start the identification
    process from scratch.
    """

    _attr_icon = "mdi:restore"
    _attr_has_entity_name = False

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – Reset Parameters"
        self._attr_unique_id = f"{DOMAIN}_reset_parameters_button"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose whether estimated parameters are currently active."""
        snapshot = None
        try:
            snapshot = self._coordinator.estimated_params_snapshot
        except Exception:
            pass
        return {
            "has_estimated_params": snapshot is not None,
            "estimated_at": snapshot.get("estimated_at") if snapshot else None,
        }

    async def async_press(self) -> None:
        """Handle button press – revert to default parameters.

        The reset is reflected immediately in the UI via the entity state and
        the per-room parameter sensors, so no notification is raised.
        """
        self._coordinator.reset_estimated_parameters()
        self._coordinator.async_update_listeners()


class RunSysIdWithWindowButton(ButtonEntity):
    """
    Button that runs the SysID simulation over the explicitly selected time
    window defined by the two SysID datetime pickers.

    The button reads the current ``native_value`` of
    ``datetime.heating_assistant_sysid_window_start`` and
    ``datetime.heating_assistant_sysid_window_end``, converts them to UNIX
    timestamps, then calls ``run_sysid_simulation`` with ``window_start`` /
    ``window_end`` so only data in that range is used.
    """

    _attr_icon = "mdi:calendar-search"
    _attr_has_entity_name = False

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – Run SysID with Window"
        self._attr_unique_id = f"{DOMAIN}_run_sysid_with_window_button"

    async def async_press(self) -> None:
        """Read datetime entities and call run_sysid_simulation with window_spec."""
        from .const import DOMAIN as _DOMAIN

        start_eid = f"datetime.{_DOMAIN}_sysid_window_start"
        end_eid = f"datetime.{_DOMAIN}_sysid_window_end"

        start_state = self.hass.states.get(start_eid)
        end_state = self.hass.states.get(end_eid)

        if start_state is None or end_state is None:
            _LOGGER.warning(
                "SysID window datetime entities not found (%s, %s) – "
                "falling back to default horizon",
                start_eid,
                end_eid,
            )
            await self.hass.services.async_call(
                _DOMAIN,
                "run_sysid_simulation",
                {},
                blocking=False,
            )
            return

        try:
            from datetime import timezone as _tz
            from datetime import datetime as _dt

            def _parse(state_str: str) -> float:
                dt = _dt.fromisoformat(state_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_tz.utc)
                return dt.timestamp()

            window_start = _parse(start_state.state)
            window_end = _parse(end_state.state)
        except (ValueError, TypeError) as exc:
            _LOGGER.error("Could not parse SysID window datetimes: %s", exc)
            return

        await self.hass.services.async_call(
            _DOMAIN,
            "run_sysid_simulation",
            {"window_start": window_start, "window_end": window_end},
            blocking=False,
        )
