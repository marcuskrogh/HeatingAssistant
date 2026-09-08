"""SWD-516: unique device+entity labels on config sensor chips."""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[1]
_TREES = (
    _ROOT / "heatingassistant" / "app" / "static",
    _ROOT / "heating_assistant" / "heatingassistant" / "app" / "static",
)


def _load_bridge_manager(
    *,
    extra_modules: dict[str, Any] | None = None,
):
    ha_mqtt = MagicMock()
    ha_mqtt.async_publish = AsyncMock()
    fake_components = MagicMock()
    fake_components.mqtt = ha_mqtt
    fake_ha = MagicMock()
    fake_ha.components = fake_components
    fake_ha.config_entries = MagicMock()
    fake_ha.const = MagicMock(
        SERVICE_TURN_OFF="turn_off",
        SERVICE_TURN_ON="turn_on",
        STATE_UNAVAILABLE="unavailable",
        STATE_UNKNOWN="unknown",
    )
    fake_ha.core = MagicMock()
    fake_ha.helpers = MagicMock()
    fake_ha.helpers.event = MagicMock(async_track_state_change_event=MagicMock())
    modules = {
        "homeassistant": fake_ha,
        "homeassistant.components": fake_components,
        "homeassistant.config_entries": fake_ha.config_entries,
        "homeassistant.const": fake_ha.const,
        "homeassistant.core": fake_ha.core,
        "homeassistant.helpers": fake_ha.helpers,
        "homeassistant.helpers.event": fake_ha.helpers.event,
    }
    if extra_modules:
        modules.update(extra_modules)
    patcher = patch.dict("sys.modules", modules)
    patcher.start()
    for name in list(sys.modules):
        if name.startswith("custom_components.heating_assistant"):
            del sys.modules[name]
    bm = importlib.import_module("custom_components.heating_assistant.bridge_manager")
    return bm, patcher


def test_combine_device_entity_name_is_unique_enough() -> None:
    bm, patcher = _load_bridge_manager()
    try:
        combine = bm.combine_device_entity_name
        assert (
            combine("Living Room Window", "TempPV", "sensor.a")
            == "Living Room Window TempPV"
        )
        assert (
            combine(
                "Living Room Window",
                "Living Room Window TempPV",
                "sensor.a",
            )
            == "Living Room Window TempPV"
        )
        assert combine("", "TempPV", "sensor.a") == "TempPV"
        assert combine("", "", "sensor.a") == "sensor.a"
    finally:
        patcher.stop()


def test_catalog_display_name_combines_device_and_entity() -> None:
    living = SimpleNamespace(entity_id="sensor.living_temppv", name="TempPV")
    kitchen = SimpleNamespace(entity_id="sensor.kitchen_temppv", name="TempPV")
    entries = {
        "sensor.living_temppv": SimpleNamespace(
            name="TempPV",
            original_name="TempPV",
            device_id="dev-living",
        ),
        "sensor.kitchen_temppv": SimpleNamespace(
            name="TempPV",
            original_name="TempPV",
            device_id="dev-kitchen",
        ),
    }
    devices = {
        "dev-living": SimpleNamespace(name_by_user=None, name="Living Room Window"),
        "dev-kitchen": SimpleNamespace(name_by_user=None, name="Kitchen Window"),
    }

    class _EntReg:
        def async_get(self, entity_id: str) -> Any:
            return entries.get(entity_id)

    class _DevReg:
        def async_get(self, device_id: str) -> Any:
            return devices.get(device_id)

    fake_er = MagicMock()
    fake_er.async_get.return_value = _EntReg()
    fake_dr = MagicMock()
    fake_dr.async_get.return_value = _DevReg()

    bm, patcher = _load_bridge_manager(
        extra_modules={
            "homeassistant.helpers.entity_registry": fake_er,
            "homeassistant.helpers.device_registry": fake_dr,
        }
    )
    try:
        living_name = bm.entity_catalog_display_name(MagicMock(), living)
        kitchen_name = bm.entity_catalog_display_name(MagicMock(), kitchen)
    finally:
        patcher.stop()

    assert living_name == "Living Room Window TempPV"
    assert kitchen_name == "Kitchen Window TempPV"
    assert living_name != kitchen_name


def test_catalog_falls_back_to_state_name_without_device() -> None:
    bm, patcher = _load_bridge_manager()
    try:
        state = SimpleNamespace(entity_id="sensor.outdoor", name="Outdoor temperature")
        assert bm.entity_catalog_display_name(MagicMock(), state) == "Outdoor temperature"
    finally:
        patcher.stop()


def test_config_chips_store_entity_ids_not_labels() -> None:
    source = (
        _ROOT / "heatingassistant" / "app" / "static" / "js" / "config" / "config-ui.js"
    ).read_text(encoding="utf-8")
    assert "obj[key].push(id)" in source
    assert "obj[key] = id" in source
    assert 'title="${escapeAttr(id)}"' in source
    assert "entityFriendlyName(hass, id)" in source
    assert "name: entityFriendlyName(hass, id)" in source


def test_panel_entity_display_name_harness() -> None:
    harness = _ROOT / "tests" / "panel_entity_display_name.harness.mjs"
    node = shutil.which("node")
    if not node:
        for candidate in (
            Path(r"C:\Program Files\nodejs\node.exe"),
            Path("/usr/bin/node"),
            Path("/usr/local/bin/node"),
        ):
            if candidate.is_file():
                node = str(candidate)
                break
    if not node:
        pytest.skip("node is not on PATH")
    result = subprocess.run(
        [node, str(harness)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_synced_app_package_has_unique_label_helper() -> None:
    for static in _TREES:
        js = static.joinpath("js", "config", "config-ui.js").read_text(encoding="utf-8")
        assert "function combineDeviceEntityName" in js
        assert "function entityFriendlyName" in js
        assert "hass?.devices" in js
