"""SWD-299: App publishes model_fit_quality + parameter_confidence diagnostics."""

from __future__ import annotations

from pathlib import Path

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine import const
from heatingassistant.engine.model_diagnostics import (
    compute_model_fit_metrics,
    validate_parameters,
)
from heatingassistant.mqtt.bridge import InMemoryMqttBus


pytestmark = pytest.mark.unit


def _runtime(tmp_path: Path, **room_overrides) -> HeatingRuntime:
    room = {
        "name": "Living Room",
        "setpoint": 21.0,
        "comfort_offset": 1.5,
        "thermal_mass": 1_000_000.0,
        "r_external": 0.05,
        "temp_tags": ["living_temp"],
        "enabled": True,
    }
    room.update(room_overrides)
    return HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "update_interval": 900,
            "rooms": [room],
            "heat_sources": [
                {
                    "name": "Living Heater",
                    "room": "Living Room",
                    "type": const.SOURCE_TYPE_ELECTRIC,
                    "max_power": 1200.0,
                    "heater_entity": "switch.living_heater",
                }
            ],
            "system_enabled": True,
        },
    )


def _seed_aligned_history(runtime: HeatingRuntime, *, perfect: bool = True) -> None:
    dt = float(runtime.options["update_interval"])
    start = 1_800_000_000.0
    records = []
    for i in range(8):
        meas = 21.0 + 0.1 * i
        pred = meas if perfect else meas + 0.5
        records.append(
            {
                "y": [meas],
                "y_pred": [pred] if i > 0 else None,
                "u": [0.0],
                "d_outdoor": 5.0,
                "d_solar": {"Living Room": 0.0},
                "timestamp": start + i * dt,
            }
        )
    runtime._history_buffer.extend(records)


def test_compute_model_fit_metrics_perfect_fit() -> None:
    metrics = compute_model_fit_metrics(
        [20.0, 21.0, 22.0],
        [20.0, 21.0, 22.0],
        "Living Room",
    )
    assert metrics.r_squared == pytest.approx(1.0)
    assert metrics.rmse == pytest.approx(0.0)


def test_validate_parameters_scores_valid_range() -> None:
    validation = validate_parameters("Living Room", 1_000_000.0, 0.05)
    assert validation.mass_valid is True
    assert validation.r_external_valid is True
    assert validation.time_constant_valid is True


def test_hass_states_model_fit_unknown_without_aligned_history(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    states = runtime.hass_states()
    fit = states["sensor.heating_assistant_living_room_model_fit_quality"]
    conf = states["sensor.heating_assistant_living_room_parameter_confidence"]
    assert fit["state"] == "unknown"
    assert fit["attributes"]["error"] == "Insufficient data"
    assert conf["state"] != "unknown"
    assert conf["attributes"]["is_estimated"] is False


def test_hass_states_publishes_model_fit_and_confidence(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _seed_aligned_history(runtime, perfect=True)
    states = runtime.hass_states()

    fit = states["sensor.heating_assistant_living_room_model_fit_quality"]
    assert float(fit["state"]) == pytest.approx(1.0)
    assert fit["attributes"]["rmse"] == pytest.approx(0.0)
    assert fit["attributes"]["n_samples"] >= 2

    conf = states["sensor.heating_assistant_living_room_parameter_confidence"]
    assert float(conf["state"]) == pytest.approx(100.0)
    assert conf["attributes"]["is_estimated"] is False
    assert isinstance(conf["attributes"]["card_warnings"], list)


def test_hass_states_parameter_confidence_is_estimated_from_snapshot(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.options["estimated_params"] = {
        "rooms": {
            "Living Room": {
                "thermal_mass": 1_000_000.0,
                "r_external": 0.05,
                "is_estimated": True,
                "estimated_at": "2026-08-10T12:00:00+00:00",
            }
        }
    }
    _seed_aligned_history(runtime)
    conf = runtime.hass_states()["sensor.heating_assistant_living_room_parameter_confidence"]
    assert conf["attributes"]["is_estimated"] is True
    assert conf["attributes"]["estimated_at"] == "2026-08-10T12:00:00+00:00"
