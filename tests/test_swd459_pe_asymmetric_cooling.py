"""SWD-459: PE history must use cooling capacity, not −heating max."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from heatingassistant.engine.heat_sources import GroundSourceHeatPump, HeatPump
from heatingassistant.engine.heat_sources.electric import ElectricHeater
from heatingassistant.engine.history.plot_series import identification_aux_series


pytestmark = pytest.mark.unit


def _record(u: float, outdoor: float = 20.0, ts: float = 1_800_000_000.0) -> dict:
    return {
        "y": [22.0],
        "u": [u],
        "d_outdoor": outdoor,
        "d_solar": {"living_room": 0.0},
        "timestamp": ts,
    }


def test_pe_series_heat_pump_full_cool_uses_cooling_capacity():
    hp = HeatPump(
        "hp",
        "living_room",
        max_power=7000.0,
        cop_rated=3.5,
        cooling_cop=2.5,
    )
    series = identification_aux_series([_record(-1.0)], [hp], "living_room")
    value = series["heating_power"][0]["value"]
    expected = hp.cooling_power(outdoor_temp=20.0)
    assert value == pytest.approx(expected)
    assert abs(value) < hp.max_power
    assert value != pytest.approx(-hp.max_power)


def test_pe_series_heat_pump_full_heat_still_uses_heating_capacity():
    hp = HeatPump("hp", "living_room", max_power=7000.0, cop_rated=3.5)
    series = identification_aux_series([_record(1.0, outdoor=7.0)], [hp], "living_room")
    assert series["heating_power"][0]["value"] == pytest.approx(
        hp.thermal_power(1.0, 7.0)
    )


def test_pe_series_heat_pump_cooling_respects_power_scale():
    hp = HeatPump(
        "hp",
        "living_room",
        max_power=7000.0,
        cop_rated=3.5,
        cooling_cop=2.5,
        power_scale=0.5,
    )
    series = identification_aux_series([_record(-1.0)], [hp], "living_room")
    assert series["heating_power"][0]["value"] == pytest.approx(
        hp.cooling_power(outdoor_temp=20.0)
    )


def test_pe_series_heat_pump_partial_cool_scales_cooling_capacity():
    hp = HeatPump(
        "hp",
        "living_room",
        max_power=7000.0,
        cop_rated=3.5,
        cooling_cop=2.5,
    )
    series = identification_aux_series([_record(-0.5)], [hp], "living_room")
    assert series["heating_power"][0]["value"] == pytest.approx(
        0.5 * hp.cooling_power(outdoor_temp=20.0)
    )


def test_pe_series_heat_only_heat_pump_ignores_negative_u():
    hp = HeatPump(
        "hp",
        "living_room",
        max_power=7000.0,
        hvac_mode="heat",
    )
    series = identification_aux_series([_record(-1.0)], [hp], "living_room")
    assert series["heating_power"][0]["value"] == pytest.approx(0.0)


def test_pe_series_electric_heater_unchanged():
    heater = ElectricHeater("h", "living_room", 2000.0)
    series = identification_aux_series([_record(0.4)], [heater], "living_room")
    assert series["heating_power"][0]["value"] == pytest.approx(800.0)


def test_pe_series_cooling_capable_does_not_fall_back_to_heating_gain():
    source = SimpleNamespace(
        room="living_room",
        can_cool=True,
        max_power=7000.0,
        power_scale=1.0,
        smooth_thermal_power=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
        thermal_power=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    series = identification_aux_series([_record(-1.0)], [source], "living_room")
    assert series["heating_power"][0]["value"] == pytest.approx(0.0)


def test_heat_pump_thermal_power_negative_is_cooling_capacity():
    hp = HeatPump("hp", "living_room", max_power=5000.0)
    assert hp.thermal_power(-1.0, 20.0) == pytest.approx(hp.cooling_power(20.0))
    assert hp.thermal_power(-1.0, 20.0) == pytest.approx(
        hp.smooth_thermal_power(-1.0, 20.0)
    )
    assert abs(hp.thermal_power(-1.0, 20.0)) < hp.max_power


def test_gshp_thermal_power_negative_is_cooling_capacity():
    gshp = GroundSourceHeatPump(
        "g", "living_room", max_power=8000.0, cop_rated=4.5, cooling_cop=2.5,
    )
    expected = gshp.cooling_power()
    assert gshp.thermal_power(-1.0) == pytest.approx(expected)
    assert gshp.smooth_thermal_power(-1.0) == pytest.approx(expected)
    assert abs(expected) < gshp.max_power
