"""Tests for panel_setup.async_register_industrial_panel.

Frontend/sidebar registration broke silently before (the function swallows all
exceptions by design), so these tests pin the registration calls themselves.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.heating_assistant.panel_setup import (
    async_register_industrial_panel,
)

pytestmark = pytest.mark.integration


class _StaticPathConfig:
    def __init__(self, url_path, path, cache_headers=True):
        self.url_path = url_path
        self.path = path
        self.cache_headers = cache_headers


def _install_ha_modules(monkeypatch, *, with_extra_urls=True):
    """Inject the lazily-imported HA frontend/http modules panel_setup needs."""
    http_mod = types.ModuleType("homeassistant.components.http")
    http_mod.StaticPathConfig = _StaticPathConfig
    monkeypatch.setitem(sys.modules, "homeassistant.components.http", http_mod)

    frontend_mod = types.ModuleType("homeassistant.components.frontend")
    panel_calls = []
    extra_url_calls = []

    def async_register_built_in_panel(hass, **kwargs):
        panel_calls.append(kwargs)

    frontend_mod.async_register_built_in_panel = async_register_built_in_panel
    if with_extra_urls:
        def async_register_extra_urls(hass, urls):
            extra_url_calls.append(urls)

        frontend_mod.async_register_extra_urls = async_register_extra_urls
    monkeypatch.setitem(sys.modules, "homeassistant.components.frontend", frontend_mod)
    return panel_calls, extra_url_calls


def _make_hass():
    return SimpleNamespace(http=SimpleNamespace(async_register_static_paths=AsyncMock()))


async def test_registers_static_path_and_panel(monkeypatch):
    panel_calls, extra_url_calls = _install_ha_modules(monkeypatch)
    hass = _make_hass()

    await async_register_industrial_panel(hass, SimpleNamespace())

    (static_configs,) = hass.http.async_register_static_paths.await_args.args
    (cfg,) = static_configs
    assert cfg.url_path == "/ha-industrial-panel"
    assert cfg.path.replace("\\", "/").endswith("/www")
    assert cfg.cache_headers is False

    assert extra_url_calls == [["/ha-industrial-panel/heating-assistant-icons.js"]]
    (panel,) = panel_calls
    assert panel["frontend_url_path"] == "ha-industrial"
    assert panel["sidebar_icon"] == "heating-assistant:logo"
    assert panel["require_admin"] is False
    js_url = panel["config"]["_panel_custom"]["js_url"]
    # The ?v= token is the single source of truth for frontend cache busting.
    assert js_url.startswith("/ha-industrial-panel/industrial-dashboard.js?v=")


async def test_falls_back_to_mdi_icon_without_extra_urls(monkeypatch):
    panel_calls, _ = _install_ha_modules(monkeypatch, with_extra_urls=False)
    hass = _make_hass()

    await async_register_industrial_panel(hass, SimpleNamespace())

    (panel,) = panel_calls
    assert panel["sidebar_icon"] == "mdi:radiator"


async def test_static_path_failure_is_swallowed_and_skips_panel(monkeypatch):
    panel_calls, _ = _install_ha_modules(monkeypatch)
    hass = _make_hass()
    hass.http.async_register_static_paths.side_effect = RuntimeError("http not ready")

    await async_register_industrial_panel(hass, SimpleNamespace())

    assert panel_calls == []
