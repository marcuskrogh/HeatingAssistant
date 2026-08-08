from __future__ import annotations

from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
from threading import Thread
from typing import Iterator
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from heatingassistant.app.__main__ import _Handler
from heatingassistant.app.runtime import HeatingRuntime


pytestmark = pytest.mark.unit


@contextmanager
def app_server(runtime: HeatingRuntime) -> Iterator[str]:
    handler = type("TestHeatingAssistantHandler", (_Handler,), {"runtime": runtime})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def get_json(base_url: str, path: str) -> dict:
    with urlopen(f"{base_url}{path}", timeout=5) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


def send_json(base_url: str, path: str, method: str, payload: dict) -> dict:
    request = Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urlopen(request, timeout=5) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


def test_app_http_health_static_and_config_roundtrip(tmp_path) -> None:
    runtime = HeatingRuntime(tmp_path, options={"instance_id": "haos"})

    with app_server(runtime) as base_url:
        health = get_json(base_url, "/api/health")
        assert health["status"] == "ok"
        assert health["instance_id"] == "haos"

        with urlopen(f"{base_url}/static/css/industrial.css", timeout=5) as response:
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/css")
            assert b"--bg-primary" in response.read()

        updated = send_json(
            base_url,
            "/api/config",
            "POST",
            {
                "rooms": [{"name": "living", "temp_tags": ["living_temp_1"]}],
                "bindings": [
                    {
                        "tag": "living_temp_1",
                        "entity_id": "sensor.living",
                        "direction": "in",
                    }
                ],
            },
        )
        assert updated["instance_id"] == "haos"
        assert updated["rooms"][0]["name"] == "living"

        config = get_json(base_url, "/api/config")
        assert config["bindings"][0]["entity_id"] == "sensor.living"

        bindings = send_json(
            base_url,
            "/api/bindings",
            "PUT",
            {
                "bindings": [
                    {
                        "tag": "living_heat",
                        "entity_id": "switch.living",
                        "direction": "out",
                    }
                ]
            },
        )
        assert bindings["bindings"][0]["tag"] == "living_heat"


def test_app_http_rejects_bad_static_path(tmp_path) -> None:
    runtime = HeatingRuntime(tmp_path, options={"instance_id": "haos"})

    with app_server(runtime) as base_url:
        with pytest.raises(HTTPError) as exc_info:
            urlopen(f"{base_url}/static/../pyproject.toml", timeout=5)
        assert exc_info.value.code == 400


def test_app_http_exposes_mqtt_diagnostics(tmp_path) -> None:
    class FakeBus:
        connected = False
        last_error = "not authorised (rc=5)"

        def subscribe(self, *args, **kwargs):
            return lambda: None

        def add_connect_handler(self, handler) -> None:
            return None

        async def publish(self, *args, **kwargs) -> None:
            raise RuntimeError("MQTT client is not connected")

        def reconfigure(self, **kwargs) -> None:
            return None

    runtime = HeatingRuntime(
        tmp_path,
        bus=FakeBus(),  # type: ignore[arg-type]
        options={
            "instance_id": "haos",
            "mqtt_broker": "core-mosquitto",
            "mqtt_port": 1883,
            "mqtt_username": "addons",
            "mqtt_password": "should-be-redacted",
            "mqtt_source": "options",
            "mqtt_ssl": False,
        },
    )

    with app_server(runtime) as base_url:
        health = get_json(base_url, "/api/health")
        assert health["mqtt_connected"] is False
        assert health["mqtt_source"] == "options"
        assert health["mqtt_last_error"] == "not authorised (rc=5)"
        assert "mqtt_discovery_error" in health
        assert health["mqtt_username"] == "addons"
        assert "mqtt_password" not in health or health["mqtt_password"] in {"", "***"}

        state = get_json(base_url, "/api/state")
        assert state["mqtt_connected"] is False
        assert state["mqtt_last_error"] == "not authorised (rc=5)"
        assert state["mqtt_username"] == "addons"
        assert "mqtt_password" not in state or state["mqtt_password"] in {"", "***"}

        config = get_json(base_url, "/api/config")
        assert config["mqtt_password"] == "***"
