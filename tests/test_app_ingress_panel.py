from __future__ import annotations

from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
from threading import Thread
from typing import Iterator
from urllib.request import Request, urlopen

from heatingassistant.app.__main__ import _Handler
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.persistence import load_config


@contextmanager
def app_server(runtime: HeatingRuntime) -> Iterator[str]:
    handler = type("TestHeatingAssistantIngressHandler", (_Handler,), {"runtime": runtime})
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


def post_json(base_url: str, path: str, payload: dict) -> dict:
    request = Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


def test_ingress_serves_industrial_panel_assets_and_bootstrap(tmp_path) -> None:
    runtime = HeatingRuntime(tmp_path, options={"instance_id": "haos"})

    with app_server(runtime) as base_url:
        with urlopen(
            f"{base_url}/ha-industrial-panel/industrial-dashboard.js", timeout=5
        ) as response:
            assert response.status == 200
            assert "javascript" in response.headers["Content-Type"]
            assert b"customElements.define('ha-industrial-panel'" in response.read()

        with urlopen(f"{base_url}/", timeout=5) as response:
            body = response.read().decode("utf-8")

    assert "static/js/app-hass-shim.js" in body
    assert "ha-industrial-panel/industrial-dashboard.js?v=124" in body
    assert "ha-industrial-panel" in body
    assert "Home Assistant custom-panel entry point" not in body


def test_industrial_dashboard_base_path_is_importable_module_url() -> None:
    """SWD-266: bare 'ha-industrial-panel/...' fails dynamic import(); must be a URL."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "heatingassistant"
        / "app"
        / "static"
        / "industrial-dashboard.js"
    ).read_text(encoding="utf-8")

    # Must not assign a bare package-style path (no ./, ../, /, or http).
    assert "const BASE_PATH = 'ha-industrial-panel'" not in source
    assert 'const BASE_PATH = "ha-industrial-panel"' not in source
    # Prefer resolving from the classic script URL, with ./ fallback.
    assert "new URL('.', src)" in source
    assert "return './ha-industrial-panel'" in source


def test_ingress_index_injects_base_href_for_ha_proxy(tmp_path) -> None:
    runtime = HeatingRuntime(tmp_path, options={"instance_id": "haos"})

    with app_server(runtime) as base_url:
        request = Request(
            f"{base_url}/",
            headers={"X-Ingress-Path": "/api/hassio_ingress/test-key"},
        )
        with urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8")

    assert '<base href="/api/hassio_ingress/test-key/">' in body
    assert 'window.__HA_INGRESS_BASE="/api/hassio_ingress/test-key"' in body
    assert "static/js/app-hass-shim.js" in body


def test_ingress_panel_json_endpoints_and_schedule_persistence(tmp_path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        options={
            "instance_id": "haos",
            "rooms": [
                {
                    "name": "Living Room",
                    "temp_tags": ["living_temp"],
                    "setpoint": 21.0,
                    "comfort_offset": 1.5,
                }
            ],
            "schedules": {
                "living_room": {
                    "enabled": True,
                    "periods": [{"start": "08:00", "end": "22:00", "setpoint": 21.0}],
                }
            },
        },
    )

    with app_server(runtime) as base_url:
        schedules = get_json(base_url, "/api/schedules")
        controller = get_json(base_url, "/api/controller_config")
        ui_settings = get_json(base_url, "/api/ui_settings")
        model_config = get_json(base_url, "/api/model_config")
        forecasts = get_json(base_url, "/api/forecasts")
        datasets = get_json(base_url, "/api/datasets")
        pe_coverage = get_json(base_url, "/api/pe_coverage?room_slug=living_room")
        experiments = get_json(base_url, "/api/experiments")
        state = get_json(base_url, "/api/state")

        updated = post_json(
            base_url,
            "/api/schedules",
            {
                "room_name": "Living Room",
                "enabled": False,
                "periods": [{"start": "07:00", "end": "09:00", "setpoint": 20.0}],
            },
        )

    assert schedules["room_schedules"]["living_room"]["periods"][0]["start"] == "08:00"
    assert controller["config"]["room_schedules"]["living_room"]["enabled"] is True
    assert controller["config"]["room_comfort_offsets"]["living_room"] == 1.5
    assert set(ui_settings) == {"ui_settings"}
    assert model_config["rooms"][0]["name"] == "Living Room"
    assert "rooms" in forecasts
    assert "price_forecast" in forecasts
    assert forecasts["plot_forecast_hours"] is None
    assert datasets == {"datasets": []}
    assert pe_coverage["room"] == "Living Room"
    assert [cat["id"] for cat in pe_coverage["categories"]] == [
        "closed_window_envelope",
        "heater_excitation",
        "solar_variation",
        "open_contact",
    ]
    assert pe_coverage["categories"][3]["status"] == "na"
    assert experiments == {"experiments": []}
    assert "sensor.heating_assistant_controller_config" in state["hass_states"]

    saved = updated["room_schedules"]["living_room"]
    assert saved["enabled"] is False
    assert saved["periods"][0]["start"] == "07:00"
    assert load_config(tmp_path)["schedules"]["living_room"] == saved
