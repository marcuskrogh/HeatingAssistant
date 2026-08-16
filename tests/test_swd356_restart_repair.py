"""SWD-356: Restart required is a Settings repair, not an MQTT Update card."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from heatingassistant.app.core_restart import discovery_topic, state_topic, write_stamp
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "heating_assistant"


def _load_restart_issue():
    pkg_name = "heating_assistant"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(INTEGRATION.resolve())]
    sys.modules[pkg_name] = pkg
    for name in ("const", "version_sync", "restart_issue"):
        mod_name = f"{pkg_name}.{name}"
        spec = importlib.util.spec_from_file_location(mod_name, INTEGRATION / f"{name}.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{pkg_name}.restart_issue"]


def test_sync_restart_issue_creates_fixable_repair() -> None:
    restart_issue = _load_restart_issue()
    created: dict[str, object] = {}
    fake_ir = MagicMock()
    fake_ir.IssueSeverity.WARNING = "warning"

    def _create(hass, domain, issue_id, **kwargs):  # noqa: ANN001
        created.update(kwargs)
        created["domain"] = domain
        created["issue_id"] = issue_id

    fake_ir.async_create_issue.side_effect = _create
    restart_issue._issue_registry = lambda: fake_ir  # type: ignore[method-assign]

    assert restart_issue.sync_restart_issue(object(), needed=True) is True
    assert created["domain"] == "heating_assistant"
    assert created["issue_id"] == "restart_required"
    assert created["is_fixable"] is True
    assert created["is_persistent"] is False
    assert created["translation_key"] == "restart_required"
    fake_ir.async_delete_issue.assert_not_called()


def test_sync_restart_issue_defaults_to_version_sync() -> None:
    restart_issue = _load_restart_issue()
    fake_ir = MagicMock()
    fake_ir.IssueSeverity.WARNING = "warning"
    restart_issue._issue_registry = lambda: fake_ir  # type: ignore[method-assign]
    restart_issue.restart_required = lambda: True  # type: ignore[method-assign]

    assert restart_issue.sync_restart_issue(object()) is True
    fake_ir.async_create_issue.assert_called_once()
    fake_ir.async_delete_issue.assert_not_called()


def test_sync_restart_issue_defaults_to_delete_when_not_needed() -> None:
    restart_issue = _load_restart_issue()
    fake_ir = MagicMock()
    restart_issue._issue_registry = lambda: fake_ir  # type: ignore[method-assign]
    restart_issue.restart_required = lambda: False  # type: ignore[method-assign]

    assert restart_issue.sync_restart_issue(object()) is False
    fake_ir.async_delete_issue.assert_called_once()
    fake_ir.async_create_issue.assert_not_called()


def test_sync_restart_issue_deletes_when_not_needed() -> None:
    restart_issue = _load_restart_issue()
    fake_ir = MagicMock()
    restart_issue._issue_registry = lambda: fake_ir  # type: ignore[method-assign]

    assert restart_issue.sync_restart_issue(object(), needed=False) is False
    fake_ir.async_delete_issue.assert_called_once()
    fake_ir.async_create_issue.assert_not_called()


def test_strings_and_repairs_module_ship_with_integration() -> None:
    strings = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))
    assert strings["issues"]["restart_required"]["title"] == "Restart required"
    assert (INTEGRATION / "repairs.py").is_file()
    repairs_src = (INTEGRATION / "repairs.py").read_text(encoding="utf-8")
    assert 'async_call("homeassistant", "restart")' in repairs_src
    sync_script = (ROOT / "scripts" / "sync-ha-app-package.sh").read_text(encoding="utf-8")
    assert sync_script.count("repairs.py") >= 2
    assert sync_script.count("restart_issue.py") >= 2
    dest = ROOT / "heating_assistant" / "custom_components" / "heating_assistant"
    assert (dest / "repairs.py").is_file()
    assert (dest / "restart_issue.py").is_file()


@pytest.mark.asyncio
async def test_app_tombstones_mqtt_update_entity(tmp_path: Path) -> None:
    write_stamp(tmp_path, from_version="2026.08.7", to_version="2026.08.8")
    bus = InMemoryMqttBus()
    runtime = HeatingRuntime(tmp_path, bus=bus, options={"instance_id": "default"})
    await runtime.start()
    retained = {topic: payload for topic, payload, _qos, retain in bus.published if retain}
    assert retained.get(discovery_topic()) == ""
    assert retained.get(state_topic("default")) == ""
    await runtime.stop()
