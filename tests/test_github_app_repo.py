"""Packaging guards for the HeatingAssistant HAOS App repository."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "heating_assistant"
APP_CONFIG = APP_DIR / "config.yaml"
APP_MANIFEST = APP_DIR / "custom_components" / "heating_assistant" / "manifest.json"
APP_DOCKERFILE = APP_DIR / "Dockerfile"
HA_APP_DIR = ROOT / "ha_app"


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_repository_yaml_declares_heatingassistant_app_repo():
    repository = _yaml(ROOT / "repository.yaml")

    assert repository == {
        "name": "HeatingAssistant Apps",
        "url": "https://github.com/marcuskrogh/HeatingAssistant",
        "maintainer": "Marcus Krogh <https://github.com/marcuskrogh>",
    }


def test_repo_contains_exactly_one_app_config():
    configs = sorted(
        path.relative_to(ROOT)
        for path in ROOT.rglob("config.yaml")
        if ".git" not in path.parts and ".pytest_cache" not in path.parts
    )

    assert configs == [Path("heating_assistant/config.yaml")]


def test_heatingassistant_app_config_shape():
    config = _yaml(APP_CONFIG)

    assert config["name"] == "HeatingAssistant"
    assert config["slug"] == "heatingassistant"
    assert config["version"] == "2026.08.21"
    assert config["arch"] == ["amd64", "aarch64"]
    assert config["init"] is False
    assert config["startup"] == "application"
    assert config["boot"] == "auto"
    assert config["timeout"] == 120
    assert config["watchdog"] == "http://[HOST]:[PORT:8100]/"
    assert config["ingress"] is True
    assert config["ingress_port"] == 8100
    assert config["panel_icon"] == "mdi:radiator"
    assert config["panel_title"] == "HeatingAssistant"
    # SUPERVISOR_TOKEN + /services/mqtt require hassio_api (SWD-274).
    assert config["hassio_api"] is True
    assert config["hassio_role"] == "default"
    assert config["homeassistant_api"] is True
    assert config["services"] == ["mqtt:need"]
    assert config["ports"] == {"8100/tcp": 8100}
    assert config["map"] == [
        {"type": "data", "read_only": False},
        {"type": "homeassistant_config", "read_only": False},
    ]
    assert config["options"] == {
        "instance_id": "default",
        "mqtt_broker": "core-mosquitto",
        "mqtt_port": 1883,
        "mqtt_username": "",
        "mqtt_password": "",
    }
    assert config["schema"] == {
        "instance_id": "str",
        "mqtt_broker": "str",
        "mqtt_port": "port",
        "mqtt_username": "str",
        "mqtt_password": "password",
    }


def test_app_version_lock_across_app_context_and_package_metadata():
    config_version = str(_yaml(APP_CONFIG)["version"])

    dockerfile = APP_DOCKERFILE.read_text(encoding="utf-8")
    docker_match = re.search(
        r"^ARG BUILD_VERSION=([^\s]+)$",
        dockerfile,
        flags=re.MULTILINE,
    )
    assert docker_match is not None
    docker_version = docker_match.group(1).strip('"')

    app_manifest = json.loads(APP_MANIFEST.read_text(encoding="utf-8"))
    root_project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    app_project = tomllib.loads((APP_DIR / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    assert app_manifest["domain"] == "heating_assistant"
    assert root_project["name"] == "heatingassistant"
    assert app_project["name"] == "heatingassistant"
    assert {
        config_version,
        docker_version,
        app_manifest["version"],
        root_project["version"],
        app_project["version"],
    } == {"2026.08.21"}


def test_app_dockerfile_uses_synced_package_and_bundled_integration():
    dockerfile = APP_DOCKERFILE.read_text(encoding="utf-8")

    assert "ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.20" in dockerfile
    assert "COPY heatingassistant ./heatingassistant" in dockerfile
    assert "RUN pip3 install --no-cache-dir ." in dockerfile
    assert "paho-mqtt" in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert (
        "COPY custom_components /usr/share/heatingassistant/custom_components"
        in dockerfile
    )
    assert (APP_DIR / "heatingassistant" / "__init__.py").is_file()
    assert (APP_DIR / "heatingassistant" / "app" / "__main__.py").is_file()
    # SWD-275: SUPERVISOR_TOKEN is only visible via with-contenv.
    run_sh = (APP_DIR / "run.sh").read_text(encoding="utf-8")
    assert run_sh.splitlines()[0].strip() == "#!/usr/bin/with-contenv bashio"


def test_ha_app_directory_is_docs_only():
    docs = sorted(path.relative_to(HA_APP_DIR) for path in HA_APP_DIR.rglob("*") if path.is_file())

    assert docs == [Path("INSTALL.md")]
    install_doc = (HA_APP_DIR / "INSTALL.md").read_text(encoding="utf-8")
    assert "../README.md#installation" in install_doc
