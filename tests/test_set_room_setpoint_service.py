"""Tests for the ``set_room_setpoint`` service, plus the shared
slug-resolution behaviour of the per-room control services.

``set_room_setpoint``, ``set_room_enabled`` and ``set_room_comfort_offset``
all resolve the frontend slug to the canonical configured room name via the
same ``_resolve_room_name`` helper, so slug resolution and the unknown-room
error are tested once here, parametrized over the three handlers.  Each
service's specific behaviour stays in its own test file.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import custom_components.heating_assistant.services.control as control_mod
from custom_components.heating_assistant.services.control import (
    handle_set_room_comfort_offset,
    handle_set_room_enabled,
    handle_set_room_setpoint,
)


def _make_coordinator(room_names):
    coordinator = MagicMock()
    coordinator.model = SimpleNamespace(room_names=list(room_names))
    coordinator.get_room_setpoint.return_value = 21.0
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


# (handler, service-specific call data, coordinator mutator, expected call args)
_HANDLER_CASES = [
    pytest.param(
        handle_set_room_setpoint,
        {"setpoint": 22.5},
        "set_room_setpoint",
        ("Living Room", 22.5),
        id="set_room_setpoint",
    ),
    pytest.param(
        handle_set_room_enabled,
        {"enabled": False},
        "set_room_enabled",
        ("Living Room", False),
        id="set_room_enabled",
    ),
    pytest.param(
        handle_set_room_comfort_offset,
        {"comfort_offset": 1.5},
        "set_room_comfort_offset",
        ("Living Room", 1.5),
        id="set_room_comfort_offset",
    ),
]


@pytest.mark.parametrize("handler,extra_data,mutator,expected_call", _HANDLER_CASES)
async def test_room_slug_resolves_to_canonical_name(
    monkeypatch, handler, extra_data, mutator, expected_call
):
    coordinator = _make_coordinator(["Living Room"])
    hass = SimpleNamespace()
    monkeypatch.setattr(control_mod, "get_coordinator", lambda _h: coordinator)

    await handler(
        hass,
        SimpleNamespace(data={"room_name": "living_room", **extra_data}),
    )

    getattr(coordinator, mutator).assert_called_once_with(*expected_call)
    coordinator.async_update_listeners.assert_called_once()


@pytest.mark.parametrize("handler,extra_data,mutator,expected_call", _HANDLER_CASES)
async def test_unknown_room_raises(
    monkeypatch, handler, extra_data, mutator, expected_call
):
    coordinator = _make_coordinator(["Kitchen"])
    hass = SimpleNamespace()
    monkeypatch.setattr(control_mod, "get_coordinator", lambda _h: coordinator)

    with pytest.raises(ValueError):
        await handler(
            hass,
            SimpleNamespace(data={"room_name": "missing", **extra_data}),
        )
    getattr(coordinator, mutator).assert_not_called()
