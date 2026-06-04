"""Regression tests for dashboard tuning-parameter persistence.

Guards the bug where tuning / estimation parameters set from the dashboard
reverted to the previously-configured set on restart.  The coordinator reads
these parameters with **options-first** precedence, but the dashboard service
handlers wrote only to ``entry.data``.  Once the options flow had snapshotted
the config into ``entry.options``, that stale options value re-won on every
restart and the dashboard change was silently discarded.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import custom_components.heating_assistant.__init__ as init_mod
from custom_components.heating_assistant.const import (
    CONF_TRACKING_WEIGHT,
    CONF_SIGMA_W,
    DOMAIN,
)


def _capture_handlers(hass):
    """Run ``_register_services`` and return the registered handler map."""
    handlers: dict = {}

    def _register(domain, name, handler, **_kwargs):
        handlers[name] = handler

    hass.services = SimpleNamespace(
        async_register=_register,
        has_service=lambda *_a, **_k: False,
    )
    init_mod._register_services(hass)
    return handlers


def _make_coordinator():
    coordinator = MagicMock()
    coordinator._entry = SimpleNamespace(entry_id="entry-1")
    return coordinator


def _make_hass(entry, coordinator, update_calls, monkeypatch):
    hass = SimpleNamespace()
    hass.data = {DOMAIN: {}}
    hass.config_entries = SimpleNamespace(
        async_get_entry=lambda _eid: entry,
        async_update_entry=lambda e, **kwargs: update_calls.append(kwargs),
    )
    monkeypatch.setattr(init_mod, "_get_coordinator", lambda _h: coordinator)
    return hass


@pytest.mark.asyncio
async def test_controller_tuning_written_to_both_data_and_options(monkeypatch):
    """update_controller_tuning must persist to options (read-first) and data."""
    entry = SimpleNamespace(
        data={CONF_TRACKING_WEIGHT: 1.0},
        options={CONF_TRACKING_WEIGHT: 1.0},
        entry_id="entry-1",
    )
    coordinator = _make_coordinator()
    update_calls: list = []
    hass = _make_hass(entry, coordinator, update_calls, monkeypatch)

    handlers = _capture_handlers(hass)
    await handlers["update_controller_tuning"](
        SimpleNamespace(data={CONF_TRACKING_WEIGHT: 5.0})
    )

    assert len(update_calls) == 1
    call = update_calls[0]
    # Options is the store the coordinator reads first — it MUST hold the new value
    # so a restart does not revert to the stale previously-configured value.
    assert call["options"][CONF_TRACKING_WEIGHT] == 5.0
    assert call["data"][CONF_TRACKING_WEIGHT] == 5.0
    coordinator.apply_tuning_updates.assert_called_once_with({CONF_TRACKING_WEIGHT: 5.0})


@pytest.mark.asyncio
async def test_estimation_params_written_to_both_data_and_options(monkeypatch):
    """update_estimation_params must persist to options (read-first) and data."""
    entry = SimpleNamespace(
        data={CONF_SIGMA_W: 0.25},
        options={},
        entry_id="entry-1",
    )
    coordinator = _make_coordinator()
    update_calls: list = []
    hass = _make_hass(entry, coordinator, update_calls, monkeypatch)

    handlers = _capture_handlers(hass)
    await handlers["update_estimation_params"](
        SimpleNamespace(data={CONF_SIGMA_W: 0.5})
    )

    assert len(update_calls) == 1
    call = update_calls[0]
    assert call["options"][CONF_SIGMA_W] == 0.5
    assert call["data"][CONF_SIGMA_W] == 0.5
