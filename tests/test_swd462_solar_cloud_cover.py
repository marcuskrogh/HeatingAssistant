"""SWD-462: historical solar gain must use cloud-cover scaled intensity."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from heatingassistant.app.disturbance_forecasts import build_mpc_disturbance_inputs
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.controller.facade import HeatingMPCController
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.thermal_model import HouseModel, Room, Window
from heatingassistant.engine.weather import (
    attach_condition_from_reason,
    resolve_cloud_cover_now,
)
from heatingassistant.mqtt.topics import MqttTagPayload


pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc)
FORECAST = [
    {
        "datetime": "2026-08-31T12:00:00+00:00",
        "temperature": 18.0,
        "cloud_coverage": 95.0,
        "condition": "cloudy",
    },
    {
        "datetime": "2026-08-31T15:00:00+00:00",
        "temperature": 17.0,
        "cloud_coverage": 100.0,
        "condition": "cloudy",
    },
]


def test_resolve_cloud_cover_prefers_percent() -> None:
    assert resolve_cloud_cover_now(
        {"cloud_coverage": 40, "condition": "cloudy"}
    ) == pytest.approx(0.4)


def test_resolve_cloud_cover_falls_back_to_condition() -> None:
    assert resolve_cloud_cover_now({"condition": "cloudy"}) == pytest.approx(0.85)


def test_attach_condition_from_mqtt_reason() -> None:
    attrs = attach_condition_from_reason({"temperature": 12.0}, "cloudy")
    assert attrs["condition"] == "cloudy"


def test_forecast_without_current_percent_sets_cloud_cover_now() -> None:
    """Regression: k=0 used to stay clear-sky when only forecast had clouds."""
    out = build_mpc_disturbance_inputs(
        outdoor_temp=16.0,
        weather_attrs={"forecast": list(FORECAST)},
        price_value=None,
        price_attrs=None,
        solar_value=None,
        solar_attrs=None,
        horizon=4,
        dt_s=900.0,
        now=NOW,
    )
    assert out["cloud_cover_now"] == pytest.approx(0.95)
    assert out["cloud_forecast"]
    assert all(value >= 0.9 for value in out["cloud_forecast"])
    assert "ghi_now" not in out


def test_condition_only_weather_sets_cloud_cover_now() -> None:
    out = build_mpc_disturbance_inputs(
        outdoor_temp=16.0,
        weather_attrs={"condition": "cloudy"},
        price_value=None,
        price_attrs=None,
        solar_value=None,
        solar_attrs=None,
        horizon=4,
        dt_s=900.0,
        now=NOW,
    )
    assert out["cloud_cover_now"] == pytest.approx(0.85)
    assert out["cloud_forecast"] == [pytest.approx(0.85)] * 4


def _windowed_controller() -> HeatingMPCController:
    living = Room(
        name="living_room",
        thermal_mass=5_000_000.0,
        r_external=0.05,
        temperature=21.0,
        setpoint=21.0,
        windows=[Window(area=8.0, orientation=180.0, tilt=90.0)],
    )
    model = HouseModel([living])
    sources = [ElectricHeater("lr", "living_room", max_power=4000.0)]
    return HeatingMPCController(model, sources, horizon=3, dt=900.0)


def test_forecast_solar_k0_uses_cloud_forecast_when_now_missing() -> None:
    ctrl = _windowed_controller()
    clear = ctrl._forecast_solar(NOW, cloud_forecast=None, cloud_cover_now=None)
    cloudy = ctrl._forecast_solar(
        NOW,
        cloud_forecast=[1.0, 1.0, 1.0, 1.0],
        cloud_cover_now=None,
    )
    assert clear[0]["living_room"] > cloudy[0]["living_room"] > 0.0
    # History/NOW and the first horizon step share the overcast path.
    assert cloudy[0]["living_room"] == pytest.approx(
        ctrl._room_gain("living_room", NOW, cloud_cover=1.0, ghi=None),
        rel=1e-12,
    )


def test_forecast_solar_ghi_still_overrides_cloud() -> None:
    ctrl = _windowed_controller()
    with_cloud = ctrl._room_gain("living_room", NOW, cloud_cover=1.0, ghi=400.0)
    no_cloud = ctrl._room_gain("living_room", NOW, cloud_cover=None, ghi=400.0)
    assert with_cloud == pytest.approx(no_cloud, rel=1e-12)


@pytest.mark.asyncio
async def test_runtime_ignores_unconfigured_solar_radiation_tag(
    tmp_path: Path,
) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        options={
            "instance_id": "haos",
            "update_interval": 900,
            "horizon": 4,
            "weather_tag": "weather_forecast",
            "rooms": [
                {"name": "Living Room", "setpoint": 22.0, "temp_tags": ["living_temp"]}
            ],
        },
    )
    runtime.update_tag(
        "weather_forecast",
        MqttTagPayload(
            value=16.0,
            status="GOOD",
            reason="cloudy",
            ts=1.0,
            attributes={"forecast": list(FORECAST)},
        ),
    )
    runtime.update_tag(
        "solar_radiation",
        MqttTagPayload(value=0.0, status="GOOD", ts=1.0),
    )
    inputs = runtime._mpc_disturbance_inputs(16.0)
    assert "ghi_now" not in inputs
    # MQTT reason ``cloudy`` is the current-sky signal (0.85). Forecast
    # cloud_coverage still scales the horizon. Neither may be clear-sky.
    assert inputs["cloud_cover_now"] == pytest.approx(0.85)
    assert inputs["cloud_forecast"]
    assert all(value >= 0.85 for value in inputs["cloud_forecast"])
    assert runtime.tag_attributes["weather_forecast"]["condition"] == "cloudy"


@pytest.mark.asyncio
async def test_forecast_publish_includes_weather_condition() -> None:
    import importlib.util

    path = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "heating_assistant"
        / "forecast_publish.py"
    )
    spec = importlib.util.spec_from_file_location("ha_forecast_publish_swd462", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    hass = MagicMock()
    hass.services.has_service.return_value = False
    state = SimpleNamespace(
        domain="weather",
        entity_id="weather.home",
        state="cloudy",
        attributes={"temperature": 16.0, "cloud_coverage": 90},
    )
    attrs = await module.forecast_attributes_for_publish(hass, state)
    assert attrs is not None
    assert attrs["condition"] == "cloudy"
    assert attrs["cloud_coverage"] == pytest.approx(90)


def test_runtime_scalar_weather_keeps_condition_for_solar(
    tmp_path: Path,
) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        options={
            "instance_id": "haos",
            "update_interval": 900,
            "horizon": 4,
            "weather_tag": "weather_forecast",
            "rooms": [
                {"name": "Living Room", "setpoint": 22.0, "temp_tags": ["living_temp"]}
            ],
        },
    )
    runtime.update_tag(
        "weather_forecast",
        MqttTagPayload(value=16.0, status="GOOD", reason="cloudy", ts=1.0),
    )
    inputs = runtime._mpc_disturbance_inputs(16.0)
    assert inputs["cloud_cover_now"] == pytest.approx(0.85)
    assert inputs["cloud_forecast"] == [pytest.approx(0.85)] * 4
    assert "ghi_now" not in inputs


def test_runtime_configured_ghi_overrides_cloud(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        options={
            "instance_id": "haos",
            "update_interval": 900,
            "horizon": 4,
            "weather_tag": "weather_forecast",
            "solar_radiation_entity": "sensor.ghi",
            "rooms": [
                {"name": "Living Room", "setpoint": 22.0, "temp_tags": ["living_temp"]}
            ],
        },
    )
    runtime.update_tag(
        "weather_forecast",
        MqttTagPayload(
            value=16.0,
            status="GOOD",
            reason="cloudy",
            ts=1.0,
            attributes={"condition": "cloudy"},
        ),
    )
    runtime.update_tag(
        "solar_radiation",
        MqttTagPayload(value=400.0, status="GOOD", ts=1.0),
    )
    inputs = runtime._mpc_disturbance_inputs(16.0)
    assert inputs["ghi_now"] == pytest.approx(400.0)
    assert inputs["cloud_cover_now"] == pytest.approx(0.85)
