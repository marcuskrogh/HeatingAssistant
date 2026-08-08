"""SWD-274: App must declare hassio_api so SUPERVISOR_TOKEN is injected."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
APP_CONFIG = ROOT / "heating_assistant" / "config.yaml"


def test_app_config_enables_supervisor_token_for_mqtt_discovery() -> None:
    config = yaml.safe_load(APP_CONFIG.read_text(encoding="utf-8"))
    assert config["hassio_api"] is True
    assert config["hassio_role"] == "default"
    assert config["homeassistant_api"] is True
    assert config["services"] == ["mqtt:need"]
    assert config["version"] == "2.0.13"
