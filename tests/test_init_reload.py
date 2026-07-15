"""Tests for the reload-without-restart feature."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import custom_components.heating_assistant.__init__ as init_mod
from custom_components.heating_assistant.const import (
    CONF_HEAT_SOURCES,
    CONF_ROOMS,
    DOMAIN,
)
from tests.helpers.setup_patches import ensure_hass_config, patch_setup_stores


class _SetupListenerCoordinatorMixin:
    def setup_startup_listeners(self):
        return None

    def setup_window_listeners(self):
        return None

    def async_update_listeners(self):
        return None

    async def async_request_refresh(self):
        return None

    @property
    def system_enabled(self):
        return True

    @property
    def update_interval_seconds(self):
        return 900


def _make_entry(entry_id: str = "entry-1") -> SimpleNamespace:
    return SimpleNamespace(
        data={CONF_ROOMS: [], CONF_HEAT_SOURCES: []},
        options={},
        entry_id=entry_id,
        title="Heating Assistant",
        add_update_listener=MagicMock(return_value=lambda: None),
        async_on_unload=MagicMock(),
    )


def _make_hass() -> SimpleNamespace:
    hass = SimpleNamespace()
    ensure_hass_config(hass)
    hass.data = {}
    hass.services = SimpleNamespace(has_service=MagicMock(return_value=True))
    hass.config_entries = SimpleNamespace(
        async_update_entry=MagicMock(),
        async_forward_entry_setups=AsyncMock(),
        async_unload_platforms=AsyncMock(return_value=True),
        async_reload=AsyncMock(),
        async_entries=MagicMock(return_value=[]),
    )
    return hass


@pytest.mark.asyncio
async def test_unload_stashes_runtime_state_for_reload():
    """Unload should snapshot history + toggles before discarding coordinator."""
    hass = _make_hass()
    entry = _make_entry()

    coordinator = MagicMock(spec=init_mod.HeatingAssistantCoordinator)
    coordinator._history_buffer = [{"step": 1}, {"step": 2}]
    coordinator._room_enabled = {"living_room": False, "kitchen": True}
    coordinator._schedule_enabled = {"living_room": False}
    coordinator._base_setpoint = {"living_room": 24.0, "kitchen": 20.0}
    coordinator._system_enabled = True

    hass.data[DOMAIN] = {entry.entry_id: coordinator}

    assert await init_mod.async_unload_entry(hass, entry) is True

    # Coordinator was removed
    assert entry.entry_id not in hass.data[DOMAIN]

    # Runtime state was stashed
    stash = hass.data[DOMAIN]["_reload_state"][entry.entry_id]
    assert stash["history_buffer"] == [{"step": 1}, {"step": 2}]
    assert stash["room_enabled"] == {"living_room": False, "kitchen": True}
    assert stash["schedule_enabled"] == {"living_room": False}
    assert stash["base_setpoint"] == {"living_room": 24.0, "kitchen": 20.0}
    assert stash["system_enabled"] is True


@pytest.mark.asyncio
async def test_setup_entry_restores_stashed_state(monkeypatch):
    """Setup should restore stashed runtime state from a prior unload."""
    hass = _make_hass()
    entry = _make_entry()

    hass.data[DOMAIN] = {
        "yaml_config": {CONF_ROOMS: [], CONF_HEAT_SOURCES: []},
        "_reload_state": {
            entry.entry_id: {
                "history_buffer": [{"step": 7}],
                "system_enabled": False,
                "room_enabled": {"living_room": False, "removed_room": False},
                "schedule_enabled": {"living_room": False},
                "base_setpoint": {"living_room": 24.0, "removed_room": 21.0},
            }
        },
    }

    class _FakeRoom:
        def __init__(self):
            self.setpoint = 22.0

    class _Coordinator(_SetupListenerCoordinatorMixin):
        def __init__(self, *_args, **_kwargs):
            self._history_buffer = []
            self._system_enabled = True
            self._room_enabled = {"living_room": True, "kitchen": True}
            self._schedule_enabled = {"living_room": True, "kitchen": True}
            self._base_setpoint = {"living_room": 22.0, "kitchen": 20.0}
            self.model = SimpleNamespace(rooms={"living_room": _FakeRoom(), "kitchen": _FakeRoom()})

        async def async_config_entry_first_refresh(self):
            return None

    monkeypatch.setattr(init_mod, "HeatingAssistantCoordinator", _Coordinator)
    patch_setup_stores(monkeypatch)

    assert await init_mod.async_setup_entry(hass, entry) is True

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator._history_buffer == [{"step": 7}]
    assert coordinator._system_enabled is False
    # Only rooms still present are restored; "removed_room" is dropped.
    assert coordinator._room_enabled == {"living_room": False, "kitchen": True}
    assert coordinator._schedule_enabled == {"living_room": False, "kitchen": True}
    assert coordinator._base_setpoint == {"living_room": 24.0, "kitchen": 20.0}
    assert coordinator.model.rooms["living_room"].setpoint == 24.0
    # Stash is consumed.
    assert entry.entry_id not in hass.data[DOMAIN]["_reload_state"]


@pytest.mark.asyncio
async def test_setup_entry_attaches_update_listener(monkeypatch):
    """Setup should register the options-flow update listener via async_on_unload."""
    hass = _make_hass()
    entry = _make_entry()
    hass.data[DOMAIN] = {"yaml_config": {CONF_ROOMS: [], CONF_HEAT_SOURCES: []}}

    class _Coordinator(_SetupListenerCoordinatorMixin):
        def __init__(self, *_args, **_kwargs):
            self._history_buffer = []
            self._room_enabled = {}
            self._schedule_enabled = {}
            self._base_setpoint = {}
            self.model = SimpleNamespace(rooms={})

        async def async_config_entry_first_refresh(self):
            return None

    monkeypatch.setattr(init_mod, "HeatingAssistantCoordinator", _Coordinator)
    patch_setup_stores(monkeypatch)

    assert await init_mod.async_setup_entry(hass, entry) is True

    entry.add_update_listener.assert_called_once_with(init_mod._async_update_listener)
    assert entry.async_on_unload.call_count >= 1


@pytest.mark.asyncio
async def test_update_listener_schedules_reload_in_background():
    """Options listener should schedule reload without waiting for completion."""
    hass = _make_hass()
    entry = _make_entry(entry_id="entry-bg")
    scheduled = []

    def _capture_task(coro):
        scheduled.append(coro)

    hass.async_create_task = _capture_task

    await init_mod._async_update_listener(hass, entry)

    assert len(scheduled) == 1
    hass.config_entries.async_reload.assert_not_awaited()

    await scheduled[0]
    hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)


@pytest.mark.asyncio
async def test_update_listener_applies_non_structural_changes_without_reload():
    """Options listener should skip reload when coordinator can apply in-place."""
    hass = _make_hass()
    entry = _make_entry(entry_id="entry-runtime")
    entry.data = {"existing": "data"}
    entry.options = {"existing": "options"}
    apply_runtime_reconfiguration = MagicMock(return_value=True)

    class _Coordinator:
        def apply_runtime_reconfiguration(self, _config):
            return apply_runtime_reconfiguration(_config)

        def async_update_listeners(self):
            return None

    original = init_mod.HeatingAssistantCoordinator
    init_mod.HeatingAssistantCoordinator = _Coordinator
    hass.data = {
        DOMAIN: {
            entry.entry_id: _Coordinator()
        }
    }
    hass.async_create_task = MagicMock()

    try:
        await init_mod._async_update_listener(hass, entry)
    finally:
        init_mod.HeatingAssistantCoordinator = original

    apply_runtime_reconfiguration.assert_called_once_with({"existing": "options"})
    hass.async_create_task.assert_not_called()
    hass.config_entries.async_reload.assert_not_awaited()


@pytest.mark.asyncio
async def test_reload_service_refreshes_yaml_and_reloads_entries(monkeypatch):
    """The reload service should re-read YAML and reload every domain entry."""
    hass = _make_hass()
    hass.data = {DOMAIN: {"yaml_config": {CONF_ROOMS: [{"name": "old"}]}}}

    new_yaml = {DOMAIN: {CONF_ROOMS: [{"name": "new"}], CONF_HEAT_SOURCES: []}}

    async def _fake_yaml(_hass, _domain):
        return new_yaml

    entry_a = SimpleNamespace(entry_id="a")
    entry_b = SimpleNamespace(entry_id="b")
    hass.config_entries.async_entries = MagicMock(return_value=[entry_a, entry_b])

    monkeypatch.setattr(init_mod, "async_integration_yaml_config", _fake_yaml)

    captured = {}

    def _capture_admin_service(_hass, _domain, service, handler, schema=None):
        captured["service"] = service
        captured["handler"] = handler

    monkeypatch.setattr(init_mod, "async_register_admin_service", _capture_admin_service)

    assert await init_mod.async_setup(hass, {}) is True

    assert captured["service"] == init_mod.SERVICE_RELOAD
    await captured["handler"](SimpleNamespace(data={}))

    # YAML was refreshed to the new config.
    assert hass.data[DOMAIN]["yaml_config"] == new_yaml[DOMAIN]
    # Both entries were reloaded.
    assert hass.config_entries.async_reload.await_count == 2


@pytest.mark.asyncio
async def test_reload_service_keeps_old_config_on_invalid_yaml(monkeypatch):
    """If YAML fails validation, reload should be a no-op (no entry reload, no wipe)."""
    hass = _make_hass()
    old_yaml = {CONF_ROOMS: [{"name": "old"}]}
    hass.data = {DOMAIN: {"yaml_config": old_yaml}}

    async def _fake_yaml(_hass, _domain):
        return None  # validation failure

    hass.config_entries.async_entries = MagicMock(
        return_value=[SimpleNamespace(entry_id="a")]
    )

    monkeypatch.setattr(init_mod, "async_integration_yaml_config", _fake_yaml)

    captured = {}

    def _capture_admin_service(_hass, _domain, service, handler, schema=None):
        captured["handler"] = handler

    monkeypatch.setattr(init_mod, "async_register_admin_service", _capture_admin_service)

    assert await init_mod.async_setup(hass, {}) is True
    await captured["handler"](SimpleNamespace(data={}))

    # Old YAML untouched, no reload triggered.
    assert hass.data[DOMAIN]["yaml_config"] is old_yaml
    hass.config_entries.async_reload.assert_not_called()
