"""SWD-442: lock HeatingRuntime public seams before collaborator extract."""

from __future__ import annotations

from pathlib import Path

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus


def _runtime(tmp_path: Path) -> HeatingRuntime:
    return HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 21.0,
                    "temp_tags": ["living_temp"],
                }
            ],
        },
    )


def test_http_composition_root_methods_exist(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    for name in (
        "start",
        "stop",
        "status",
        "config",
        "state_snapshot",
        "schedules",
        "controller_config",
        "ui_settings",
        "model_config",
        "forecasts",
        "datasets",
        "dataset",
        "experiments",
        "history",
        "hass_states",
        "update_config",
        "update_schedule",
        "update_ui_settings",
        "update_bindings",
        "apply_service",
        "preview_tuning_forecast",
        "run_control_cycle",
    ):
        assert callable(getattr(runtime, name)), name


def test_hass_states_exposes_panel_entities(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    states = runtime.hass_states()
    assert "sensor.heating_assistant_controller_config" in states
    assert "sensor.heating_assistant_system_summary" in states
    assert "sensor.heating_assistant_mpc_performance" in states
    assert "sensor.heating_assistant_living_room_temperature_measured" in states
    assert "climate.heating_assistant_living_room" in states
    mpc = states["sensor.heating_assistant_mpc_performance"]["attributes"]
    assert "nmpc_computing" in mpc
    assert "control_computing" in mpc
    assert "nmpc_period_s" in mpc


def test_app_package_exports_heating_runtime() -> None:
    from heatingassistant.app import HeatingRuntime as Exported

    assert Exported is HeatingRuntime
