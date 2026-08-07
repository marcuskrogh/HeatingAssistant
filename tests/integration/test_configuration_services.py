"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
from __future__ import annotations

import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from heatingassistant.engine.const import CONF_TRACKING_WEIGHT, DOMAIN
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
