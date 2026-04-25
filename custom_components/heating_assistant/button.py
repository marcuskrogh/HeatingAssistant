"""
Heating Assistant – Button platform.

Provides a single button entity that triggers maximum-likelihood thermal
parameter estimation (via the accumulated history buffer) with one press.
The estimated parameters are applied to the running model automatically and
a persistent notification reports the results.
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
    async_add_entities([EstimateParametersButton(coordinator)])


class EstimateParametersButton(ButtonEntity):
    """
    Button that triggers maximum-likelihood thermal parameter estimation.

    Pressing this button runs the Kalman-filter PED log-likelihood
    optimisation over the rolling observation history.  Estimated values for
    ``thermal_mass`` and ``r_external`` are applied to the live model and
    reported via a persistent notification.
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
        await self._coordinator.async_estimate_parameters_ml(apply_params=True)
