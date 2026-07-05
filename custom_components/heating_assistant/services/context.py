"""Shared service-layer helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..coordinator import HeatingAssistantCoordinator


def get_coordinator(hass: HomeAssistant) -> "HeatingAssistantCoordinator":
    """Return the active Heating Assistant coordinator."""
    from ..const import DOMAIN  # noqa: PLC0415
    from ..coordinator import HeatingAssistantCoordinator  # noqa: PLC0415

    for entry_id, obj in hass.data.get(DOMAIN, {}).items():
        if isinstance(obj, HeatingAssistantCoordinator):
            return obj
    raise ValueError("No Heating Assistant coordinator found")
