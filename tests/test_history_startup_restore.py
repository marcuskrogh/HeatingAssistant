"""Integration tests for history buffer restore across reloads and restarts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.heating_assistant.const import DOMAIN
from custom_components.heating_assistant.history.startup_restore import (
    async_restore_history_buffer,
    async_stash_reload_state,
)
from custom_components.heating_assistant.history.store import IdentificationHistoryStore
from tests.helpers.coordinator_stubs import make_minimal_coordinator
from tests.helpers.setup_patches import ensure_hass_config

# Builds real coordinator objects (tests/helpers stubs) — integration tier.
pytestmark = pytest.mark.integration


def _make_entry(entry_id: str = "entry-1"):
    return SimpleNamespace(entry_id=entry_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_async_restore_prefers_reload_state():
    hass = SimpleNamespace()
    ensure_hass_config(hass)
    entry = _make_entry()
    coord = make_minimal_coordinator()
    coord._history_buffer = []

    reload_records = [{"timestamp": 1.0, "y": [20.0]}]
    hass.data = {
        DOMAIN: {
            "_reload_state": {entry.entry_id: {"history_buffer": reload_records}},
        }
    }

    coord.id_history_store = MagicMock()
    coord.id_history_store.async_query_recent = AsyncMock()

    await async_restore_history_buffer(hass, entry, coord)

    assert coord._history_buffer == reload_records
    coord.id_history_store.async_query_recent.assert_not_awaited()
    assert entry.entry_id not in hass.data[DOMAIN].get("_reload_state", {})


@pytest.mark.integration
@pytest.mark.asyncio
async def test_async_restore_jsonl_before_recorder(tmp_path):
    hass = SimpleNamespace()
    ensure_hass_config(hass, str(tmp_path))
    entry = _make_entry()
    coord = make_minimal_coordinator()
    coord._history_buffer = []
    coord.hass = hass

    store = IdentificationHistoryStore(hass, entry.entry_id)
    await store.async_setup()

    ts = 1_700_000_000.0
    for i in range(3):
        await store.async_append(
            {
                "y": [20.0 + i],
                "u": [0.5],
                "d_outdoor": 5.0,
                "d_solar": {},
                "timestamp": ts + i * 900,
            }
        )

    coord.id_history_store = store
    hass.data = {DOMAIN: {}}

    with patch(
        "custom_components.heating_assistant.history.seed.async_rebuild_history_from_recorder",
        new_callable=AsyncMock,
    ) as mock_recorder:
        await async_restore_history_buffer(hass, entry, coord)
        mock_recorder.assert_not_awaited()

    assert len(coord._history_buffer) == 3
    assert coord._history_buffer[0]["y"] == [20.0]
    assert coord._history_buffer[-1]["y"] == [22.0]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_async_stash_reload_state(tmp_path):
    hass = SimpleNamespace()
    ensure_hass_config(hass, str(tmp_path))
    entry = _make_entry()
    coord = make_minimal_coordinator()
    coord._history_buffer = [{"timestamp": 42.0, "y": [21.0]}]
    coord._system_enabled = True
    coord._room_enabled = {"living_room": True}

    saved_data: dict = {}

    class _CapturingStore:
        def __init__(self, hass, version=1, key=""):
            self.key = key

        async def async_save(self, data):
            saved_data["history"] = data

    with patch(
        "custom_components.heating_assistant.history.startup_restore.Store",
        _CapturingStore,
    ):
        hass.data = {DOMAIN: {}}
        await async_stash_reload_state(hass, entry, coord)

    reload_state = hass.data[DOMAIN]["_reload_state"][entry.entry_id]
    assert reload_state["history_buffer"] == coord._history_buffer
    assert reload_state["system_enabled"] is True
    assert reload_state["room_enabled"] == {"living_room": True}
    assert saved_data["history"] == coord._history_buffer
