"""Tests for coordinator startup entity listeners."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import custom_components.heating_assistant.coordinator as coord_mod


def test_setup_startup_listeners_refreshes_when_entities_already_available(monkeypatch):
    """Entities restored before the listener registers must still trigger MPC."""
    scheduled = []

    class _FakeCoordinator:
        _outdoor_entity = "sensor.outdoor"
        _weather_entity = None
        _temp_sensors = {"living_room": ["sensor.living_temp"]}
        hass = SimpleNamespace(
            states=SimpleNamespace(
                get=lambda eid: SimpleNamespace(state="21.5")
                if eid in ("sensor.outdoor", "sensor.living_temp")
                else None
            ),
            async_create_task=lambda coro: scheduled.append(coro),
        )

        async def async_request_refresh(self):
            return None

    cancel = MagicMock()
    monkeypatch.setattr(
        coord_mod,
        "async_track_state_change_event",
        lambda *_a, **_kw: cancel,
    )

    result = coord_mod.HeatingAssistantCoordinator.setup_startup_listeners(
        _FakeCoordinator()
    )

    assert result is cancel
    assert len(scheduled) == 1


def test_setup_startup_listeners_waits_when_entities_unavailable(monkeypatch):
    """No immediate refresh when watched entities are still unknown."""
    scheduled = []

    class _FakeCoordinator:
        _outdoor_entity = "sensor.outdoor"
        _weather_entity = None
        _temp_sensors = {}
        hass = SimpleNamespace(
            states=SimpleNamespace(
                get=lambda _eid: SimpleNamespace(state="unknown")
            ),
            async_create_task=lambda coro: scheduled.append(coro),
        )

        async def async_request_refresh(self):
            return None

    monkeypatch.setattr(
        coord_mod,
        "async_track_state_change_event",
        lambda *_a, **_kw: MagicMock(),
    )

    coord_mod.HeatingAssistantCoordinator.setup_startup_listeners(_FakeCoordinator())

    assert scheduled == []
