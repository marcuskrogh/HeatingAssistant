"""SWD-285: Controller Tuning preview must use unapplied draft params."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.mqtt.bridge import InMemoryMqttBus
from heatingassistant.mqtt.topics import MqttTagPayload


pytestmark = pytest.mark.unit


def _runtime_with_room(tmp_path: Path, **extra_options) -> HeatingRuntime:
    options = {
        "instance_id": "haos",
        "update_interval": 900,
        "horizon": 4,
        "tracking_weight": 0.0,
        "energy_weight": 0.01,
        "latitude": 55.7,
        "longitude": 12.6,
        "rooms": [
            {
                "name": "Living Room",
                "setpoint": 22.0,
                "comfort_offset": 2.0,
                "temp_tags": ["living_temp"],
            }
        ],
        "heat_sources": [
            {
                "name": "living_heater",
                "type": "electric_heater",
                "room": "Living Room",
                "max_power": 1500.0,
            }
        ],
        **extra_options,
    }
    runtime = HeatingRuntime(tmp_path, bus=InMemoryMqttBus(), options=options)
    runtime.update_tag("living_temp", MqttTagPayload(value=21.0, status="GOOD"))
    runtime.update_tag("outdoor_temp", MqttTagPayload(value=5.0, status="GOOD"))
    runtime.options["outdoor_temp_tag"] = "outdoor_temp"
    return runtime


def test_preview_returns_error_when_outdoor_unavailable(tmp_path: Path) -> None:
    runtime = _runtime_with_room(tmp_path)
    runtime.tag_values.pop("outdoor_temp", None)
    runtime.options.pop("outdoor_temp_tag", None)
    result = runtime.preview_tuning_forecast({"tracking_weight": 2.0}, 1.0)
    assert result == {"error": "outdoor_temperature_unavailable"}


def test_preview_does_not_mutate_live_forecast_cache(tmp_path: Path) -> None:
    runtime = _runtime_with_room(tmp_path)
    engine = runtime.control_engine
    engine._last_predictions = [{"Living Room": 21.0}]
    engine._last_heating_schedule = [{"Living Room": 100.0}]
    engine.mode = "mpc"
    before = engine.forecast_snapshot()

    if engine._controller is None:
        pytest.skip("MPC controller unavailable in this environment")

    payload = runtime.preview_tuning_forecast(
        {"tracking_weight": 5.0, "horizon": 3},
        plot_forecast_hours=0.5,
    )
    assert "error" not in payload
    assert "living_room" in payload["rooms"]
    after = engine.forecast_snapshot()
    assert after["predictions"] == before["predictions"]
    assert after["heating_schedule"] == before["heating_schedule"]
    # Applied options unchanged.
    assert runtime.options["tracking_weight"] == 0.0
    assert runtime.options["horizon"] == 4


def test_preview_uses_draft_horizon_not_applied_config(tmp_path: Path) -> None:
    runtime = _runtime_with_room(tmp_path)
    if runtime.control_engine._controller is None:
        pytest.skip("MPC controller unavailable in this environment")

    payload = runtime.preview_tuning_forecast(
        {"horizon": 2, "update_interval": 900, "tracking_weight": 1.0},
        plot_forecast_hours=None,
    )
    assert "error" not in payload
    room = payload["rooms"]["living_room"]
    # bridge + 2 prediction steps for horizon=2
    assert len(room["forecast"]) == 3
    assert room["step_seconds"] == pytest.approx(900.0)


def test_preview_comfort_offset_affects_bands_and_restores_model(tmp_path: Path) -> None:
    runtime = _runtime_with_room(tmp_path)
    room = runtime.control_engine.model.rooms["Living Room"]
    assert room.comfort_offset == pytest.approx(2.0)

    if runtime.control_engine._controller is None:
        pytest.skip("MPC controller unavailable in this environment")

    payload = runtime.preview_tuning_forecast(
        {"comfort_offset": 0.5, "horizon": 2},
        plot_forecast_hours=0.5,
    )
    assert "error" not in payload
    forecast = payload["rooms"]["living_room"]["forecast"]
    assert forecast[0]["constraint_upper"] == pytest.approx(22.5)
    assert forecast[0]["constraint_lower"] == pytest.approx(21.5)
    # Live model comfort restored after preview.
    assert room.comfort_offset == pytest.approx(2.0)


@patch("heatingassistant.engine.control_loop.build_mpc_controller", create=True)
def test_control_engine_preview_builds_with_overrides(_mock_unused) -> None:
    """Unit-level: preview controller is built from draft weights via factory."""
    engine = ControlEngine(
        {
            "update_interval": 900,
            "horizon": 4,
            "tracking_weight": 0.0,
            "energy_weight": 0.01,
            "latitude": 55.7,
            "longitude": 12.6,
            "rooms": [{"name": "Living Room", "setpoint": 22.0, "comfort_offset": 2.0}],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 1500.0,
                }
            ],
        }
    )
    preview_ctrl = MagicMock()
    preview_ctrl.predictions = [{"Living Room": 21.5}]
    preview_ctrl.linearised_predictions = []
    preview_ctrl.heating_schedule = [{"Living Room": 700.0}]
    preview_ctrl.outdoor_forecast = [5.0]
    preview_ctrl.solar_forecast = [{"Living Room": 0.0}]
    preview_ctrl.price_forecast = [0.1]
    preview_ctrl.filtered_temperatures = {"Living Room": 21.1}

    with patch.object(
        engine, "_build_controller_from_config", return_value=preview_ctrl
    ) as build:
        snapshot = engine.preview_tuning_forecast(
            {"tracking_weight": 3.5, "horizon": 2},
            {"Living Room": 21.0},
            5.0,
            {"Living Room": 22.0},
            outdoor_forecast=[5.0, 4.5],
            price_forecast=[0.1, 0.2],
        )

    build.assert_called_once()
    kwargs = build.call_args.kwargs
    assert kwargs["horizon"] == 2
    cfg = build.call_args.args[0]
    assert cfg["tracking_weight"] == 3.5
    preview_ctrl.solve_nmpc.assert_called_once()
    preview_ctrl.apply_nmpc_result.assert_called_once()
    preview_ctrl.compute.assert_called_once()
    assert preview_ctrl.compute.call_args.kwargs.get("run_optimization") is True
    assert snapshot["predictions"][0]["Living Room"] == pytest.approx(21.5)
    assert snapshot["dt"] == pytest.approx(900.0)
    assert snapshot["horizon"] == 2
    # Live cache still empty / untouched.
    assert engine._last_predictions == []


def test_control_engine_preview_compute_failure_returns_error() -> None:
    engine = ControlEngine(
        {
            "update_interval": 900,
            "horizon": 4,
            "rooms": [{"name": "Living Room", "setpoint": 22.0}],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 1500.0,
                }
            ],
        }
    )
    preview_ctrl = MagicMock()
    preview_ctrl.compute.side_effect = RuntimeError("solver boom")
    with patch.object(engine, "_build_controller_from_config", return_value=preview_ctrl):
        result = engine.preview_tuning_forecast(
            {"tracking_weight": 1.0},
            {"Living Room": 21.0},
            5.0,
            {"Living Room": 22.0},
        )
    assert result == {"error": "preview_compute_failed"}


def test_preview_returns_busy_when_control_lock_held(tmp_path: Path) -> None:
    runtime = _runtime_with_room(tmp_path)
    fake_lock = MagicMock()
    fake_lock.acquire.return_value = False
    runtime._control_lock = fake_lock
    result = runtime.preview_tuning_forecast({"tracking_weight": 1.0}, 0.5)
    assert result == {"error": "controller_busy"}
    fake_lock.acquire.assert_called_once()
    fake_lock.release.assert_not_called()
