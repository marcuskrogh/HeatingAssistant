"""SWD-597: display series use the estimate and the model, not mislabelled measurements."""

from __future__ import annotations

from pathlib import Path

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus

pytestmark = pytest.mark.unit


def _runtime(tmp_path: Path) -> HeatingRuntime:
    return HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "house",
            "system_enabled": False,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 21.0,
                    "comfort_offset": 1.0,
                    "temp_tags": ["living_temp"],
                }
            ],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 2000.0,
                    "output_tag": "heater",
                }
            ],
        },
    )


def test_temperature_filtered_is_x_hat_when_sigma_v_nonzero(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    controller = runtime.control_engine._controller
    assert float(controller._system._sigma_v) != 0.0
    names = list(controller._system._room_list)
    index = names.index("Living Room")
    controller._ekf._x[index] = 18.2
    assert float(controller.filtered_temperatures["Living Room"]) == pytest.approx(18.2)
    runtime.room_temperatures["Living Room"] = 21.0
    runtime.tag_values["living_temp"] = 21.0

    state = runtime.hass_states()["sensor.heating_assistant_living_room_temperature_filtered"]
    assert float(state["state"]) == pytest.approx(18.2)
    assert float(state["state"]) != pytest.approx(21.0)
    assert state["attributes"]["quantity"] == "ekf_x_hat"


def test_heat_loss_matches_ua_times_temperature_difference(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    room = runtime.control_engine.model.rooms["Living Room"]
    room.r_external = 0.05
    room.ua_open = 4.0
    room.sky_radiative_ua = 0.0
    room.thermal_bridge_psi_l = 0.0
    controller = runtime.control_engine._controller
    index = list(controller._system._room_list).index("Living Room")
    controller._ekf._x[index] = 22.0
    runtime.options["outdoor_temp_tag"] = "outdoor"
    runtime.tag_values["outdoor"] = 2.0

    g_inf, g_out, _duplicate = room.conductances()
    closed = (float(g_inf) + float(g_out)) * (22.0 - 2.0)
    state = runtime.hass_states()["sensor.heating_assistant_living_room_heat_loss"]
    assert float(state["state"]) == pytest.approx(closed, abs=1.0)

    runtime._window_state["Living Room"] = "open"
    opened = closed + 4.0 * (22.0 - 2.0)
    state_open = runtime.hass_states()["sensor.heating_assistant_living_room_heat_loss"]
    assert float(state_open["state"]) == pytest.approx(opened, abs=1.0)


def test_power_series_is_labelled_as_model_not_measurement(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.actuator_outputs["heater"] = 0.4
    states = runtime.hass_states()
    measured = states["sensor.heating_assistant_living_room_heating_power_measured"]
    model = states["sensor.heating_assistant_living_room_heating_power_model"]
    assert measured["state"] == model["state"]
    assert measured["attributes"]["measured"] is False
    assert measured["attributes"]["quantity"] == "commanded_model_power"
    assert "measured" not in model["entity_id"]
    assert model["attributes"]["legacy_entity_id"] == measured["entity_id"]


def test_parameter_confidence_exposes_components_and_tau(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    state = runtime.hass_states()["sensor.heating_assistant_living_room_parameter_confidence"]
    attrs = state["attributes"]
    assert "confidence_mass" in attrs
    assert "confidence_r_external" in attrs
    assert "confidence_tau" in attrs
    assert "tau_h" in attrs
    total = attrs["confidence_mass"] + attrs["confidence_r_external"] + attrs["confidence_tau"]
    assert total == pytest.approx(float(state["state"]), abs=0.05)


def test_mpc_performance_state_is_the_control_cycle_duration(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime._last_control_duration_s = 0.42
    state = runtime.hass_states()["sensor.heating_assistant_mpc_performance"]
    assert state["attributes"]["value_kind"] == "control_cycle_duration"
    assert float(state["state"]) == pytest.approx(0.42)
    assert state["attributes"]["control_cycle_duration_s"] == pytest.approx(0.42)
    assert state["attributes"]["friendly_name"] == "Control cycle duration"


def test_control_doc_has_no_integrating_disturbance_state() -> None:
    text = Path("docs/agents/CONTROL.md").read_text(encoding="utf-8")
    assert "augment_offsets=False" in text
    assert "no integrating" in text
    assert "[Ta, phi, b]" not in text
