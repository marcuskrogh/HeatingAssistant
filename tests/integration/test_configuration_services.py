"""Integration tests for services.configuration handlers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.heating_assistant.const import (
    CONF_PLOT_FORECAST_HOURS,
    CONF_PLOT_HISTORY_HOURS,
    CONF_TRACKING_WEIGHT,
    DOMAIN,
)
from custom_components.heating_assistant.services import configuration as config_svc


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_controller_tuning_applies_and_persists(monkeypatch):
    coordinator = MagicMock()
    coordinator._entry = SimpleNamespace(entry_id="entry-1")
    hass = SimpleNamespace(data={DOMAIN: {"entry-1": coordinator}})
    persist_calls: list = []

    monkeypatch.setattr(
        config_svc,
        "get_coordinator",
        lambda _h: coordinator,
    )
    monkeypatch.setattr(
        config_svc,
        "persist_tuning_updates",
        lambda h, c, updates: persist_calls.append((h, c, updates)),
    )

    call = SimpleNamespace(
        data={CONF_TRACKING_WEIGHT: 3.5, "unknown_key": 99},
    )
    await config_svc.handle_update_controller_tuning(hass, call)

    coordinator.apply_tuning_updates.assert_called_once_with({CONF_TRACKING_WEIGHT: 3.5})
    coordinator.async_update_listeners.assert_called_once()
    assert persist_calls[0][2] == {CONF_TRACKING_WEIGHT: 3.5}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_ui_settings_persists_plot_horizons(monkeypatch):
    coordinator = MagicMock()
    hass = SimpleNamespace(data={DOMAIN: {}})

    monkeypatch.setattr(config_svc, "get_coordinator", lambda _h: coordinator)
    monkeypatch.setattr(config_svc, "persist_tuning_updates", MagicMock())

    call = SimpleNamespace(
        data={
            CONF_PLOT_HISTORY_HOURS: 24,
            CONF_PLOT_FORECAST_HOURS: 48,
        },
    )
    await config_svc.handle_update_ui_settings(hass, call)

    coordinator.apply_tuning_updates.assert_called_once_with(
        {CONF_PLOT_HISTORY_HOURS: 24, CONF_PLOT_FORECAST_HOURS: 48}
    )
