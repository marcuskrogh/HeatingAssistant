"""SWD-275: App entry must use with-contenv so SUPERVISOR_TOKEN is exported."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "heating_assistant"
APP_CONFIG = APP_DIR / "config.yaml"
APP_RUN = APP_DIR / "run.sh"


def test_run_sh_uses_with_contenv_entrypoint() -> None:
    first = APP_RUN.read_text(encoding="utf-8").splitlines()[0].strip()
    assert first == "#!/usr/bin/with-contenv bashio"


def test_app_keeps_hassio_api_and_version_lock() -> None:
    config = yaml.safe_load(APP_CONFIG.read_text(encoding="utf-8"))
    assert config["hassio_api"] is True
    assert config["homeassistant_api"] is True
    assert config["services"] == ["mqtt:need"]
    assert config["version"] == "2026.09.3"
    assert "with-contenv" in APP_RUN.read_text(encoding="utf-8")
