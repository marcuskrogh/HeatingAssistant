"""SWD-262: fat HA integration removed.

This test module exercised the removed in-process Home Assistant integration layer.
"""
from __future__ import annotations

import pytest

pytest.skip("SWD-262: fat HA integration removed", allow_module_level=True)

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import custom_components.heating_assistant.__init__ as init_mod
from heatingassistant.engine.const import DOMAIN


class _FakeStore:
    """In-memory replacement for homeassistant.helpers.storage.Store."""

    instances: list = []

    def __init__(self, _hass, version, key):  # noqa: D401, ARG002
        self.version = version
        self.key = key
        self.value = None
        _FakeStore.instances.append(self)

    async def async_load(self):
        return self.value

    async def async_save(self, value):
        self.value = value


def _make_hass(tmp_path) -> SimpleNamespace:
    hass = SimpleNamespace()
    hass.data = {DOMAIN: {}}
    hass.config = SimpleNamespace(path=lambda *parts: str(tmp_path.joinpath(*parts)))
    hass.services = SimpleNamespace(async_call=AsyncMock())

    async def _async_add_executor_job(func, *args):
        return func(*args)

    hass.async_add_executor_job = _async_add_executor_job
    return hass


def _make_coordinator() -> SimpleNamespace:
    coordinator = SimpleNamespace()
    coordinator.model = SimpleNamespace(room_names=["living_room", "bedroom"])
    coordinator.heat_sources = []
    coordinator._room_schedule = {}
    coordinator._horizon = 6
    coordinator.dt = 60
    coordinator._price_entity = None
    return coordinator


def _entry(entry_id="e1"):
    return SimpleNamespace(entry_id=entry_id)


@pytest.fixture(autouse=True)
def _stub_store(monkeypatch):
    _FakeStore.instances.clear()
    monkeypatch.setattr(init_mod, "Store", _FakeStore)
    yield


@pytest.mark.asyncio
async def test_auto_write_creates_file_and_notification_on_first_setup(tmp_path):
    hass = _make_hass(tmp_path)
    coordinator = _make_coordinator()

    await init_mod._async_auto_write_default_dashboard(hass, _entry(), coordinator)

    target = tmp_path / "dashboards" / init_mod.DEFAULT_DASHBOARD_FILENAME
    assert target.exists()
    assert "Overview" in target.read_text(encoding="utf-8")
    hass.services.async_call.assert_awaited_once()
    args, _ = hass.services.async_call.call_args
    assert args[0] == "persistent_notification"
    assert args[1] == "create"
    assert args[2]["notification_id"] == f"{DOMAIN}_dashboard_first_install"


@pytest.mark.asyncio
async def test_auto_write_skips_when_file_already_exists(tmp_path):
    hass = _make_hass(tmp_path)
    coordinator = _make_coordinator()

    # Pre-create the target so the auto-writer must back off.
    base = tmp_path / "dashboards"
    base.mkdir()
    target = base / init_mod.DEFAULT_DASHBOARD_FILENAME
    target.write_text("user content", encoding="utf-8")

    await init_mod._async_auto_write_default_dashboard(hass, _entry(), coordinator)

    # File is left untouched and no notification fires.
    assert target.read_text(encoding="utf-8") == "user content"
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_auto_write_marker_blocks_recreation_after_delete(tmp_path):
    hass = _make_hass(tmp_path)
    coordinator = _make_coordinator()

    # First setup writes the file and records the marker.
    await init_mod._async_auto_write_default_dashboard(hass, _entry(), coordinator)
    target = tmp_path / "dashboards" / init_mod.DEFAULT_DASHBOARD_FILENAME
    assert target.exists()
    hass.services.async_call.reset_mock()

    # User deletes the file, then HA restarts and setup runs again.
    target.unlink()
    # Carry the marker across the simulated restart by reusing the same store.
    saved_marker = _FakeStore.instances[-1].value
    _FakeStore.instances.clear()

    def _store_with_carry(_hass, version, key):  # noqa: ARG001
        store = _FakeStore(_hass, version, key)
        store.value = saved_marker
        return store

    init_mod.Store = _store_with_carry  # type: ignore[attr-defined]

    await init_mod._async_auto_write_default_dashboard(hass, _entry(), coordinator)

    # File stays absent and the notification doesn't fire again.
    assert not target.exists()
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_auto_write_swallows_errors_when_hass_config_missing(tmp_path):
    hass = _make_hass(tmp_path)
    # Drop hass.config to simulate a malformed environment.
    del hass.config
    coordinator = _make_coordinator()

    # Must not raise.
    await init_mod._async_auto_write_default_dashboard(hass, _entry(), coordinator)
    hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_register_lovelace_dashboard_silent_when_lovelace_missing(tmp_path):
    hass = _make_hass(tmp_path)
    # No ``hass.data["lovelace"]`` populated: the helper must return cleanly.
    await init_mod._async_try_register_lovelace_dashboard(
        hass, str(tmp_path / "dashboards" / "heating_assistant.yaml")
    )


@pytest.mark.asyncio
async def test_auto_write_returns_path_used_for_lovelace_registration(tmp_path):
    hass = _make_hass(tmp_path)
    coordinator = _make_coordinator()
    path = await init_mod._async_auto_write_default_dashboard(hass, _entry(), coordinator)
    assert path is not None
    assert path.endswith(init_mod.DEFAULT_DASHBOARD_FILENAME)


@pytest.mark.asyncio
async def test_auto_write_also_supports_industrial_dashboard(tmp_path):
    hass = _make_hass(tmp_path)
    coordinator = _make_coordinator()
    path = await init_mod._async_auto_write_industrial_dashboard(
        hass, _entry(), coordinator
    )
    assert path is not None
    target = tmp_path / "dashboards" / init_mod.DEFAULT_INDUSTRIAL_DASHBOARD_FILENAME
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "Heating Assistant – Industrial" in content
