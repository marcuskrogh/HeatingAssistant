"""Tests for the actuation watchdog (commanded vs hardware reconciliation)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.heating_assistant.coordinator import actuation
from custom_components.heating_assistant.coordinator.core import HeatingAssistantCoordinator
from custom_components.heating_assistant.heat_sources import ElectricHeater


def _make_coord(
    *,
    entity_state: str,
    commanded_fraction: float,
    system_enabled: bool = True,
    window_state: str = "closed",
) -> HeatingAssistantCoordinator:
    source = ElectricHeater(
        "heater_1",
        "living_room",
        max_power=2000.0,
        heater_entity="switch.lr_heater",
    )
    coord = object.__new__(HeatingAssistantCoordinator)
    coord.heat_sources = [source]
    coord.actions = {"heater_1": commanded_fraction}
    coord._system_enabled = system_enabled
    coord._room_enabled = {"living_room": True}
    coord._schedule_disabled = {"living_room": False}
    coord._window_state = {"living_room": window_state}
    coord._actuation_watchdog_last_ts = 0.0
    coord.now_utc = datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)
    coord.is_experiment_active = lambda _room: False  # type: ignore[method-assign]
    coord.is_room_enabled = lambda room: coord._room_enabled.get(room, True)  # type: ignore[method-assign]
    coord.is_window_override_active = lambda room: coord._window_state.get(  # type: ignore[method-assign]
        room, "closed"
    ) in ("open", "pending_closed")
    coord.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: SimpleNamespace(
                state=entity_state, attributes={}
            )
            if entity_id == "switch.lr_heater"
            else None
        ),
        services=SimpleNamespace(async_call=AsyncMock()),
    )
    return coord


def test_switch_mismatch_when_commanded_on_but_hardware_off():
    coord = _make_coord(entity_state="off", commanded_fraction=1.0)
    assert actuation.has_actuation_mismatch(coord, coord.heat_sources[0]) is True
    assert actuation.find_actuation_mismatches(coord) == ["heater_1"]


def test_switch_mismatch_when_commanded_off_but_hardware_on():
    coord = _make_coord(entity_state="on", commanded_fraction=0.0)
    assert actuation.has_actuation_mismatch(coord, coord.heat_sources[0]) is True


def test_no_mismatch_when_switch_matches_command():
    coord = _make_coord(entity_state="on", commanded_fraction=0.8)
    assert actuation.has_actuation_mismatch(coord, coord.heat_sources[0]) is False
    assert actuation.find_actuation_mismatches(coord) == []


def test_no_mismatch_under_window_override_even_if_command_nonzero():
    """Window override clamps dispatch to zero; an off switch is expected."""
    coord = _make_coord(
        entity_state="off",
        commanded_fraction=1.0,
        window_state="open",
    )
    assert actuation.has_actuation_mismatch(coord, coord.heat_sources[0]) is False


@pytest.mark.asyncio
async def test_reconcile_reapplies_actions_on_mismatch(monkeypatch):
    coord = _make_coord(entity_state="off", commanded_fraction=1.0)
    apply_mock = AsyncMock()
    monkeypatch.setattr(actuation, "apply_actions", apply_mock)

    applied = await actuation.async_reconcile_actuation(coord, outdoor_temp=5.0)

    assert applied is True
    apply_mock.assert_awaited_once_with(coord, 5.0)


@pytest.mark.asyncio
async def test_reconcile_skips_when_states_match(monkeypatch):
    coord = _make_coord(entity_state="on", commanded_fraction=1.0)
    apply_mock = AsyncMock()
    monkeypatch.setattr(actuation, "apply_actions", apply_mock)

    applied = await actuation.async_reconcile_actuation(coord, outdoor_temp=5.0)

    assert applied is False
    apply_mock.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_rate_limited(monkeypatch):
    coord = _make_coord(entity_state="off", commanded_fraction=1.0)
    coord._actuation_watchdog_last_ts = coord.now_utc.timestamp()
    apply_mock = AsyncMock()
    monkeypatch.setattr(actuation, "apply_actions", apply_mock)

    applied = await actuation.async_reconcile_actuation(coord, outdoor_temp=5.0)

    assert applied is False
    apply_mock.assert_not_called()


@pytest.mark.asyncio
async def test_live_refresh_invokes_watchdog(monkeypatch):
    from custom_components.heating_assistant.coordinator import live_refresh

    coord = _make_coord(entity_state="off", commanded_fraction=1.0)
    coord._ensure_runtime_state_loaded = AsyncMock()
    coord._reapply_climate_setpoints = AsyncMock()
    coord._async_reconcile_actuation = AsyncMock(return_value=True)
    coord.async_update_listeners = MagicMock()
    monkeypatch.setattr(live_refresh, "refresh_live_state", lambda _c: 5.0)

    await live_refresh.async_refresh_ui(coord)

    coord._async_reconcile_actuation.assert_awaited_once_with(5.0)
