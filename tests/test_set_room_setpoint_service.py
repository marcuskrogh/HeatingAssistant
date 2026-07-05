"""Tests for the ``set_room_setpoint`` service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import custom_components.heating_assistant.services.control as control_mod
from custom_components.heating_assistant.services.control import (
    handle_set_room_setpoint,
)


def _make_coordinator(room_names):
    coordinator = MagicMock()
    coordinator.model = SimpleNamespace(room_names=list(room_names))
    return coordinator


@pytest.mark.asyncio
async def test_setpoint_applied_resolving_slug(monkeypatch):
    coordinator = _make_coordinator(["Living Room"])
    hass = SimpleNamespace()
    monkeypatch.setattr(control_mod, "get_coordinator", lambda _h: coordinator)

    await handle_set_room_setpoint(
        hass,
        SimpleNamespace(data={"room_name": "living_room", "setpoint": 22.5}),
    )

    coordinator.set_room_setpoint.assert_called_once_with("Living Room", 22.5)
    coordinator.async_update_listeners.assert_called_once()


@pytest.mark.asyncio
async def test_unknown_room_raises(monkeypatch):
    coordinator = _make_coordinator(["Kitchen"])
    hass = SimpleNamespace()
    monkeypatch.setattr(control_mod, "get_coordinator", lambda _h: coordinator)

    with pytest.raises(ValueError):
        await handle_set_room_setpoint(
            hass,
            SimpleNamespace(data={"room_name": "missing", "setpoint": 21.0}),
        )
    coordinator.set_room_setpoint.assert_not_called()
