"""Near-zero electric-heater fractions use the idle climate command.

On 2026.11.12 a fraction of 3e-6 published 22.000015 °C, which a whole-degree
Tuya panel rounds to its own reading. The Office room rose while that target
equaled the reading and fell when the target was 21.9 °C, so a fraction below
``CLIMATE_THERMOSTAT_IDLE_BELOW_U`` now returns the u = 0 idle command
(SWD-628). A fraction at or above the threshold stays a pure offset.

Real heater classes and ``climate_write_payload``. No stand-in solver.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from heatingassistant.app.actuation import (
    CLIMATE_THERMOSTAT_IDLE_BELOW_U,
    climate_thermostat_command,
    climate_write_payload,
    number_write_payload,
    switch_write_payload,
)
from heatingassistant.engine.heat_sources.electric import ElectricHeater
from heatingassistant.engine.heat_sources.heat_pump import HeatPump

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[1]
_APP_ACTUATION = (
    _ROOT / "heating_assistant" / "heatingassistant" / "app" / "actuation.py"
)


def _heater() -> ElectricHeater:
    return ElectricHeater("Office panel", "Office", max_power=1300.0)


def _command(fraction: float, *, room: float, setpoint: float, internal: float) -> dict:
    heater = _heater()
    direct = climate_thermostat_command(
        heater, fraction, internal, room, setpoint
    )
    payload = climate_write_payload(
        heater,
        fraction,
        enabled=True,
        internal_temp=internal,
        outdoor_temp=0.0,
        room_temp=room,
        room_setpoint=setpoint,
    )
    assert payload == direct
    return direct


def test_near_zero_fraction_uses_the_idle_command() -> None:
    # Previously 22.000015 °C, which rounded to the panel reading.
    command = _command(3e-6, room=22.0, setpoint=23.0, internal=22.0)
    idle = _command(0.0, room=22.0, setpoint=23.0, internal=22.0)
    assert command == idle
    assert command == {"hvac_mode": "heat", "temperature": 21.0}
    above = _command(3e-6, room=23.5, setpoint=23.0, internal=22.0)
    assert above == _command(0.0, room=23.5, setpoint=23.0, internal=22.0)
    assert above["temperature"] == pytest.approx(20.5)


def test_zero_fraction_idles_below_the_reading() -> None:
    at_or_below = _command(0.0, room=22.0, setpoint=23.0, internal=22.0)
    assert at_or_below == {"hvac_mode": "heat", "temperature": 21.0}
    above = _command(0.0, room=23.5, setpoint=23.0, internal=22.0)
    # overshoot 0.5 °C: 22 − (1 + 0.5)
    assert above == {"hvac_mode": "heat", "temperature": 20.5}


def test_threshold_boundary_keeps_the_pure_offset() -> None:
    assert CLIMATE_THERMOSTAT_IDLE_BELOW_U == pytest.approx(0.1)
    below = _command(
        CLIMATE_THERMOSTAT_IDLE_BELOW_U - 1e-9,
        room=22.0,
        setpoint=23.0,
        internal=22.0,
    )
    assert below == {"hvac_mode": "heat", "temperature": 21.0}
    at = _command(
        CLIMATE_THERMOSTAT_IDLE_BELOW_U,
        room=22.0,
        setpoint=23.0,
        internal=22.0,
    )
    assert at["temperature"] == pytest.approx(22.5)
    above = _command(0.5, room=22.0, setpoint=23.0, internal=22.0)
    assert above["temperature"] == pytest.approx(24.5)


def test_heat_pump_switch_and_number_are_unchanged() -> None:
    hp = HeatPump("Heat pump", "Living Room", max_power=9000.0, hvac_mode="heat")
    for fraction in (0.0, 3e-6, 0.05, 0.1, 0.88):
        command = climate_write_payload(
            hp,
            fraction,
            enabled=True,
            internal_temp=21.0,
            outdoor_temp=-2.0,
            room_temp=20.0,
            room_setpoint=21.0,
            supported_modes=["heat", "off"],
        )
        assert command["temperature"] == pytest.approx(
            hp.target_temperature(fraction, 21.0, -2.0)
        )
    assert switch_write_payload(3e-6, enabled=True) is False
    assert switch_write_payload(0.6, enabled=True) is True
    assert switch_write_payload(0.5, enabled=True) is False
    assert number_write_payload(0.1, enabled=True) == 10.0
    assert number_write_payload(3e-6, enabled=True) == 0.0


def test_app_copy_idles_a_near_zero_fraction() -> None:
    spec = importlib.util.spec_from_file_location(
        "heatingassistant.app.app_copy_near_zero_actuation",
        _APP_ACTUATION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module.CLIMATE_THERMOSTAT_IDLE_BELOW_U == CLIMATE_THERMOSTAT_IDLE_BELOW_U
    heater = _heater()
    command = module.climate_thermostat_command(heater, 3e-6, 22.0, 22.0, 23.0)
    assert command == {"hvac_mode": "heat", "temperature": 21.0}
    kept = module.climate_thermostat_command(heater, 0.1, 22.0, 22.0, 23.0)
    assert kept["temperature"] == pytest.approx(22.5)
