"""Aggregate registration for extracted Heating Assistant services."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .control import register_control_services
from .diagnostics import register_diagnostic_services


def register_all_services(hass: HomeAssistant) -> None:
    """Register domain services extracted from ``__init__.py``."""
    register_diagnostic_services(hass)
    register_control_services(hass)
