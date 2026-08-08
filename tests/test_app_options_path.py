"""SWD-263: App CLI must accept Supervisor --options-path and merge options."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heatingassistant.app.__main__ import _parser, merge_supervisor_options
from heatingassistant.persistence import save_config


pytestmark = pytest.mark.unit


def test_parser_accepts_run_sh_argv(tmp_path: Path) -> None:
    options = tmp_path / "options.json"
    options.write_text("{}", encoding="utf-8")
    args = _parser().parse_args(
        [
            "--host",
            "0.0.0.0",
            "--port",
            "8100",
            "--options-path",
            str(options),
            "--data-dir",
            str(tmp_path),
            "--ha-runtime",
        ]
    )
    assert args.options_path == str(options)
    assert args.data_dir == str(tmp_path)
    assert args.ha_runtime is True
    assert args.port == 8100


def test_merge_supervisor_options_overlays_mqtt_without_wiping_rooms(
    tmp_path: Path,
) -> None:
    save_config(
        tmp_path,
        {
            "instance_id": "old",
            "mqtt_broker": "stale",
            "rooms": [{"name": "Studio", "enabled": True}],
            "bindings": [{"tag": "t", "entity_id": "sensor.x", "direction": "in"}],
        },
    )
    options = tmp_path / "options.json"
    options.write_text(
        json.dumps(
            {
                "instance_id": "default",
                "mqtt_broker": "core-mosquitto",
                "mqtt_port": 1883,
                "mqtt_username": "user",
                "mqtt_password": "secret",
            }
        ),
        encoding="utf-8",
    )

    merged = merge_supervisor_options(tmp_path, options)

    assert merged["instance_id"] == "default"
    assert merged["mqtt_broker"] == "core-mosquitto"
    assert merged["mqtt_port"] == 1883
    assert merged["mqtt_username"] == "user"
    assert merged["mqtt_password"] == "secret"
    assert merged["rooms"] == [{"name": "Studio", "enabled": True}]
    assert merged["bindings"] == [
        {"tag": "t", "entity_id": "sensor.x", "direction": "in"}
    ]


def test_merge_supervisor_options_missing_file_keeps_config(tmp_path: Path) -> None:
    save_config(tmp_path, {"instance_id": "kept", "rooms": []})
    merged = merge_supervisor_options(tmp_path, tmp_path / "missing.json")
    assert merged["instance_id"] == "kept"
    assert merged["rooms"] == []
    assert merged["mqtt_source"] == "options"
