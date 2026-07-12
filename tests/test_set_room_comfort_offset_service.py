"""Tests for the ``set_room_comfort_offset`` service.

The dashboard climate cards call this service so users can widen or narrow a
room's comfort band without editing a schedule.  These tests guard that the
service resolves the room slug to the canonical configured name and forwards
the change to the coordinator.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import custom_components.heating_assistant.__init__ as init_mod
import custom_components.heating_assistant.services.control as svc_mod


def _capture_handler(hass):
    """Run ``_register_services`` and return the comfort-offset handler."""
    handlers: dict = {}

    def _register(domain, name, handler, **kwargs):
        handlers[name] = handler

    hass.services = SimpleNamespace(async_register=_register)
    init_mod._register_services(hass)
    return handlers["set_room_comfort_offset"]


def _make_coordinator(room_names):
    coordinator = MagicMock()
    coordinator.model = SimpleNamespace(room_names=list(room_names))
    return coordinator


def _make_hass(coordinator, monkeypatch):
    hass = SimpleNamespace()
    hass.data = {}
    monkeypatch.setattr(svc_mod, "get_coordinator", lambda _h: coordinator)
    return hass


# Slug resolution and the unknown-room error are covered for this handler by
# the shared parametrized tests in tests/test_set_room_setpoint_service.py.


async def test_offset_applied_with_exact_name(monkeypatch):
    """An exact configured room name is accepted as well as the slug, and the
    handler registered by ``_register_services`` is wired to the real
    implementation."""
    coordinator = _make_coordinator(["Bedroom"])
    hass = _make_hass(coordinator, monkeypatch)

    handler = _capture_handler(hass)
    await handler(
        SimpleNamespace(data={"room_name": "Bedroom", "comfort_offset": 2.5})
    )

    coordinator.set_room_comfort_offset.assert_called_once_with("Bedroom", 2.5)
