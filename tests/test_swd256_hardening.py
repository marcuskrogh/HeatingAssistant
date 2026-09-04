"""SWD-256 hardening: packaging isolation + version sync helpers."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from heatingassistant.fusion.averaging import average_numeric_tags

ROOT = Path(__file__).resolve().parents[1]


def test_app_process_isolation_contract_documented():
    """App compute lives outside HA Core process (architectural load isolation)."""
    config = yaml.safe_load((ROOT / "heating_assistant" / "config.yaml").read_text())
    assert config["slug"] == "heatingassistant"
    assert config["startup"] == "application"
    assert config["ingress"] is True
    # Home Assistant config is mapped for thin-integration sync only.
    mapped = {item["type"] if isinstance(item, dict) else item for item in config["map"]}
    assert "homeassistant_config" in mapped
    assert "data" in mapped


def test_thin_integration_has_no_heavy_requirements():
    manifest = json.loads(
        (
            ROOT
            / "heating_assistant"
            / "custom_components"
            / "heating_assistant"
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["version"] == "2026.09.4"
    assert "mqtt" in manifest.get("dependencies", [])
    reqs = manifest.get("requirements") or []
    joined = " ".join(reqs).lower()
    assert "numpy" not in joined
    assert "scipy" not in joined
    assert "mbc" not in joined


def test_version_sync_pending_restart_helper():
    import importlib.util
    import sys
    import types

    root = ROOT / "custom_components" / "heating_assistant"
    pkg_name = "heating_assistant"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(root.resolve())]
    sys.modules[pkg_name] = pkg
    for name in ("const", "version_sync"):
        mod_name = f"{pkg_name}.{name}"
        spec = importlib.util.spec_from_file_location(mod_name, root / f"{name}.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)

    version_sync = sys.modules[f"{pkg_name}.version_sync"]
    manifest_version = json.loads((root / "manifest.json").read_text(encoding="utf-8"))[
        "version"
    ]
    assert version_sync.LOADED_VERSION == manifest_version
    assert version_sync.disk_manifest_version(root) == manifest_version
    assert version_sync.restart_required(root) is False


def test_multi_sensor_room_mean_is_stable_under_partial_bad():
    values = {"a": 20.0, "b": 22.0, "c": None}
    statuses = {"a": "GOOD", "b": "GOOD", "c": "BAD"}
    assert average_numeric_tags(values, statuses) == 21.0
