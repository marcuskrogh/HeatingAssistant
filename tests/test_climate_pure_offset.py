"""Pure-offset climate commands for non-heat-pump heaters.

Marcus, 20 Nov 2026: the electric-heater climate command is always a pure
offset from the panel sensor. There is no setpoint floor. A zero fraction
stays on the idle branch (sensor − 1°C − overshoot).

These calls go through ``climate_write_payload``, the function
``_climate_write_value`` uses, with the real heater classes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from heatingassistant.app.actuation import (
    climate_write_payload,
    number_write_payload,
    switch_write_payload,
)
from heatingassistant.engine.heat_sources.electric import ElectricHeater
from heatingassistant.engine.heat_sources.gas import GasHeater
from heatingassistant.engine.heat_sources.gshp import GroundSourceHeatPump
from heatingassistant.engine.heat_sources.heat_pump import HeatPump
from heatingassistant.engine.heat_sources.hydronic import HydronicRadiator
from heatingassistant.engine.heat_sources.pellet import PelletStove
from heatingassistant.engine.heat_sources.storage import ElectricStorageHeater
from heatingassistant.engine.heat_sources.thermostat import GenericThermostat

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[1]
_ROOT_ACTUATION = _ROOT / "heatingassistant" / "app" / "actuation.py"
_APP_ACTUATION = (
    _ROOT / "heating_assistant" / "heatingassistant" / "app" / "actuation.py"
)


def _payload(source, fraction, *, room, setpoint, internal, outdoor=0.0):
    return climate_write_payload(
        source,
        fraction,
        enabled=True,
        internal_temp=internal,
        outdoor_temp=outdoor,
        room_temp=room,
        room_setpoint=setpoint,
        supported_modes=["off", "heat", "cool", "heat_cool"],
    )


def _electric() -> ElectricHeater:
    return ElectricHeater("Office panel", "Office", max_power=1300.0)


def _heat_pump() -> HeatPump:
    return HeatPump(
        "Heat pump", "Living Room", max_power=9000.0, hvac_mode="heat"
    )


def test_electric_positive_fraction_is_a_pure_offset() -> None:
    heater = _electric()
    # room 22, setpoint 23, internal 22. The old floor published 23°C for
    # every fraction up to 0.2.
    expected = {
        0.0: 21.0,
        3e-6: 22.0 + 3e-6 * heater.max_temp_offset,
        5.4e-4: 22.0 + 5.4e-4 * heater.max_temp_offset,
        0.1: 22.5,
        0.2: 23.0,
        0.5: 24.5,
        1.0: 27.0,
    }
    for fraction, temperature in expected.items():
        command = _payload(
            heater, fraction, room=22.0, setpoint=23.0, internal=22.0
        )
        assert command["hvac_mode"] == "heat"
        assert command["temperature"] == pytest.approx(temperature)
        assert command["temperature"] == pytest.approx(
            heater.target_temperature(fraction, 22.0)
            if fraction > 0.0
            else 21.0
        )


def test_near_zero_offset_rounds_to_the_panel_reading() -> None:
    """3e-6 publishes internal + 1.5e-5°C, which is the internal reading.

    The Office panel's step is 1°C. Tuya ``IntegerTypeInformation.scale_value_back``
    is ``round(value * 10**scale)`` and does not apply a separate step snap.
    Scale 0 (whole degrees) and scale 1 (tenths, step 10 → 1°C) both land on
    22, the same integer as the sensor. The panel does not heat. No epsilon.
    """

    heater = _electric()
    command = _payload(heater, 3e-6, room=22.0, setpoint=23.0, internal=22.0)
    target = command["temperature"]
    assert target == pytest.approx(22.0 + 1.5e-5)
    assert round(target) == 22
    assert round(target * 10) == 220


def test_zero_fraction_stays_idle_above_and_below_setpoint() -> None:
    heater = _electric()
    below = _payload(heater, 0.0, room=22.0, setpoint=23.0, internal=22.0)
    above = _payload(heater, 0.0, room=23.5, setpoint=23.0, internal=22.0)
    assert below == {"hvac_mode": "heat", "temperature": 21.0}
    # overshoot 0.5°C: 22 − (1 + 0.5) = 20.5
    assert above == {"hvac_mode": "heat", "temperature": 20.5}


def test_positive_fraction_ignores_room_above_setpoint() -> None:
    heater = _electric()
    command = _payload(heater, 0.1, room=23.5, setpoint=23.0, internal=22.0)
    assert command == {"hvac_mode": "heat", "temperature": 22.5}


def test_heat_pump_switch_and_number_payloads_are_unchanged() -> None:
    hp = _heat_pump()
    for fraction, temperature in (
        (0.0, 21.0),
        (3e-6, 21.0),
        (5.4e-4, 21.0),
        (0.88, hp.target_temperature(0.88, 21.0)),
    ):
        command = _payload(
            hp,
            fraction,
            room=20.0,
            setpoint=21.0,
            internal=21.0,
            outdoor=-2.0,
        )
        assert command["hvac_mode"] == "heat"
        assert command["temperature"] == pytest.approx(temperature)
    assert switch_write_payload(3e-6, enabled=True) is False
    assert switch_write_payload(0.6, enabled=True) is True
    assert number_write_payload(0.1, enabled=True) == 10.0


def test_shared_climate_sources_follow_their_own_target_map() -> None:
    """The floor is gone for every source that shares this command.

    Linear sources return internal + fraction × offset. The ground-source
    pump returns its sigmoid, the same shape the air-source pump already
    publishes, including a tiny fraction that stays on the sensor.
    """

    internal = 22.0
    linear = [
        GasHeater("gas", "Office", max_power=5000),
        HydronicRadiator("hydronic", "Office", max_power=3000),
        PelletStove("pellet", "Office", max_power=8000),
        ElectricStorageHeater("storage", "Office", max_power=2000),
        GenericThermostat("stat", "Office", max_power=2000),
    ]
    for source in linear:
        command = _payload(
            source, 0.1, room=22.0, setpoint=23.0, internal=internal
        )
        assert command["temperature"] == pytest.approx(22.5)
        assert command["temperature"] == pytest.approx(
            source.target_temperature(0.1, internal)
        )
    gshp = GroundSourceHeatPump("gshp", "Office", max_power=8000)
    tiny = _payload(gshp, 3e-6, room=22.0, setpoint=23.0, internal=internal)
    partial = _payload(gshp, 0.1, room=22.0, setpoint=23.0, internal=internal)
    assert tiny["temperature"] == pytest.approx(22.0)
    assert partial["temperature"] == pytest.approx(
        gshp.target_temperature(0.1, internal)
    )
    assert partial["temperature"] < 23.0


def _load_app_actuation():
    spec = importlib.util.spec_from_file_location(
        "heatingassistant.app.app_copy_actuation",
        _APP_ACTUATION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_app_package_copy_uses_the_pure_offset() -> None:
    assert _APP_ACTUATION.read_bytes() == _ROOT_ACTUATION.read_bytes()
    app = _load_app_actuation()
    heater = _electric()
    command = app.climate_write_payload(
        heater,
        3e-6,
        enabled=True,
        internal_temp=22.0,
        outdoor_temp=0.0,
        room_temp=22.0,
        room_setpoint=23.0,
    )
    assert command["temperature"] == pytest.approx(22.0 + 1.5e-5)
    hp = _heat_pump()
    hp_command = app.climate_write_payload(
        hp,
        0.88,
        enabled=True,
        internal_temp=21.0,
        outdoor_temp=-2.0,
        room_temp=20.0,
        room_setpoint=21.0,
        supported_modes=["heat", "off"],
    )
    assert hp_command["temperature"] == pytest.approx(
        hp.target_temperature(0.88, 21.0)
    )
