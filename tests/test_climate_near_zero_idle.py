"""Characterise the climate command for a near-zero electric-heater fraction.

Today, on 2026.11.12, ``climate_thermostat_command`` publishes a pure offset.
``u = 3e-6`` with the panel sensor at 22 °C is 22.000015 °C, which a
whole-degree Tuya panel rounds to its own reading. ``u = 0`` is the idle
command: sensor − 1 °C − overshoot.

Real ``ElectricHeater`` and ``climate_write_payload``. No stand-in solver.
"""

from __future__ import annotations

import pytest

from heatingassistant.app.actuation import climate_thermostat_command, climate_write_payload
from heatingassistant.engine.heat_sources.electric import ElectricHeater

pytestmark = pytest.mark.unit


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


def test_near_zero_fraction_publishes_the_panel_reading() -> None:
    command = _command(3e-6, room=22.0, setpoint=23.0, internal=22.0)
    assert command["hvac_mode"] == "heat"
    assert command["temperature"] == pytest.approx(22.0 + 3e-6 * 5.0)
    assert round(command["temperature"]) == 22


def test_zero_fraction_idles_below_the_reading() -> None:
    at_or_below = _command(0.0, room=22.0, setpoint=23.0, internal=22.0)
    assert at_or_below == {"hvac_mode": "heat", "temperature": 21.0}
    above = _command(0.0, room=23.5, setpoint=23.0, internal=22.0)
    # overshoot 0.5 °C: 22 − (1 + 0.5)
    assert above == {"hvac_mode": "heat", "temperature": 20.5}
