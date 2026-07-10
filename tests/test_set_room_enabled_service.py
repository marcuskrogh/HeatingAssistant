"""Tests for the ``set_room_enabled`` service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import custom_components.heating_assistant.services.control as control_mod
from custom_components.heating_assistant.services.control import (
    handle_set_room_enabled,
)


def _make_coordinator(room_names, setpoint=21.0):
    coordinator = MagicMock()
    coordinator.model = SimpleNamespace(room_names=list(room_names))
    coordinator.get_room_setpoint.return_value = setpoint
    coordinator.async_update_listeners = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


@pytest.mark.asyncio
async def test_disable_room(monkeypatch):
    coordinator = _make_coordinator(["Living Room"])
    hass = SimpleNamespace()
    monkeypatch.setattr(control_mod, "get_coordinator", lambda _h: coordinator)

    await handle_set_room_enabled(
        hass,
        SimpleNamespace(data={"room_name": "living_room", "enabled": False}),
    )

    coordinator.set_room_enabled.assert_called_once_with("Living Room", False)
    coordinator.set_room_setpoint.assert_not_called()
    coordinator.async_update_listeners.assert_called_once()
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_enable_restores_default_setpoint_below_floor(monkeypatch):
    coordinator = _make_coordinator(["Bedroom"], setpoint=5.0)
    hass = SimpleNamespace()
    monkeypatch.setattr(control_mod, "get_coordinator", lambda _h: coordinator)

    await handle_set_room_enabled(
        hass,
        SimpleNamespace(
            data={
                "room_name": "bedroom",
                "enabled": True,
                "restore_default_setpoint": True,
            }
        ),
    )

    coordinator.set_room_enabled.assert_called_once_with("Bedroom", True)
    coordinator.set_room_setpoint.assert_called_once()
    coordinator.async_update_listeners.assert_called_once()
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_enable_skips_restore_when_setpoint_above_floor(monkeypatch):
    coordinator = _make_coordinator(["Bedroom"], setpoint=21.0)
    hass = SimpleNamespace()
    monkeypatch.setattr(control_mod, "get_coordinator", lambda _h: coordinator)

    await handle_set_room_enabled(
        hass,
        SimpleNamespace(
            data={
                "room_name": "bedroom",
                "enabled": True,
                "restore_default_setpoint": True,
            }
        ),
    )

    coordinator.set_room_enabled.assert_called_once_with("Bedroom", True)
    coordinator.set_room_setpoint.assert_not_called()
    coordinator.async_update_listeners.assert_called_once()
    coordinator.async_request_refresh.assert_awaited_once()
