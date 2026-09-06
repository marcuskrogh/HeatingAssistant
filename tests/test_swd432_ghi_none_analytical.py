"""Missing forecast GHI must take the cloud/clear path, not ghi_now."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from heatingassistant.engine.const import SOLAR_GAIN_SMOOTHING_TAU_S
from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.solar_forecast import select_ghi_for_step
from heatingassistant.engine.solar_model import smooth_solar_gain_step
from heatingassistant.engine.thermal_model import HouseModel, Room, Window


pytestmark = pytest.mark.unit

_NOW = datetime(2024, 6, 21, 11, 0, tzinfo=timezone.utc)


def _windowed_controller() -> HeatingMPCController:
    living = Room(
        name="living_room",
        thermal_mass=5_000_000.0,
        r_external=0.05,
        temperature=18.0,
        setpoint=21.0,
        windows=[Window(area=2.0, orientation=180.0, tilt=90.0)],
    )
    model = HouseModel([living])
    sources = [ElectricHeater("lr", "living_room", max_power=2000.0)]
    return HeatingMPCController(model, sources, horizon=3, dt=900.0)


class TestSelectGhiForStepAnalytical:
    def test_numeric_slot_is_used(self):
        assert select_ghi_for_step([100.0, 200.0, 300.0], 1) == 200.0

    def test_none_slot_ignores_numeric_fallback(self):
        assert select_ghi_for_step([100.0, None, 300.0], 1, fallback=50.0) is None

    def test_out_of_range_ignores_numeric_fallback(self):
        assert select_ghi_for_step([100.0, 200.0], 5, fallback=500.0) is None

    def test_empty_forecast_ignores_numeric_fallback(self):
        assert select_ghi_for_step(None, 0, fallback=42.0) is None
        assert select_ghi_for_step([], 0, fallback=42.0) is None


class TestForecastSolarNoneIsAnalytical:
    def test_k0_uses_measured_ghi_now(self):
        ctrl = _windowed_controller()
        schedules = ctrl._forecast_solar(
            _NOW,
            cloud_forecast=[1.0, 1.0, 1.0],
            cloud_cover_now=1.0,
            ghi_forecast=[None, None, None],
            ghi_now=500.0,
        )
        measured = ctrl._room_gain(
            "living_room", _NOW, cloud_cover=1.0, ghi=500.0,
        )
        analytical = ctrl._room_gain(
            "living_room", _NOW, cloud_cover=1.0, ghi=None,
        )
        assert schedules[0]["living_room"] == pytest.approx(measured, rel=1e-12)
        assert schedules[0]["living_room"] != pytest.approx(analytical, rel=1e-9)

    def test_none_future_step_matches_cloud_clear_gain(self):
        ctrl = _windowed_controller()
        schedules = ctrl._forecast_solar(
            _NOW,
            cloud_forecast=[1.0, 1.0, 1.0],
            cloud_cover_now=1.0,
            ghi_forecast=[500.0, None, None],
            ghi_now=500.0,
        )
        t2 = _NOW + timedelta(seconds=900.0 * 2)
        analytical = ctrl._room_gain(
            "living_room", t2, cloud_cover=1.0, ghi=None,
        )
        leaked = ctrl._room_gain(
            "living_room", t2, cloud_cover=1.0, ghi=500.0,
        )
        g2 = schedules[2]["living_room"]
        v = smooth_solar_gain_step(
            None,
            ctrl._room_gain("living_room", _NOW, cloud_cover=1.0, ghi=500.0),
            900.0,
            SOLAR_GAIN_SMOOTHING_TAU_S,
        )
        v = smooth_solar_gain_step(
            v,
            ctrl._room_gain(
                "living_room",
                _NOW + timedelta(seconds=900.0),
                cloud_cover=1.0,
                ghi=500.0,
            ),
            900.0,
            SOLAR_GAIN_SMOOTHING_TAU_S,
        )
        v = smooth_solar_gain_step(v, analytical, 900.0, SOLAR_GAIN_SMOOTHING_TAU_S)
        assert g2 == pytest.approx(v)
        assert analytical != pytest.approx(leaked, rel=1e-9)

    def test_past_coverage_tail_is_analytical(self):
        ctrl = _windowed_controller()
        schedules = ctrl._forecast_solar(
            _NOW,
            cloud_forecast=[1.0],
            cloud_cover_now=1.0,
            ghi_forecast=[500.0],
            ghi_now=500.0,
        )
        t3 = _NOW + timedelta(seconds=900.0 * 3)
        analytical = ctrl._room_gain(
            "living_room", t3, cloud_cover=1.0, ghi=None,
        )
        leaked = ctrl._room_gain(
            "living_room", t3, cloud_cover=1.0, ghi=500.0,
        )
        g3 = schedules[3]["living_room"]
        v = None
        inst = [
            ctrl._room_gain("living_room", _NOW, cloud_cover=1.0, ghi=500.0),
            ctrl._room_gain(
                "living_room",
                _NOW + timedelta(seconds=900.0),
                cloud_cover=1.0,
                ghi=500.0,
            ),
            ctrl._room_gain(
                "living_room",
                _NOW + timedelta(seconds=1800.0),
                cloud_cover=1.0,
                ghi=None,
            ),
            analytical,
        ]
        for sample in inst:
            v = smooth_solar_gain_step(v, sample, 900.0, SOLAR_GAIN_SMOOTHING_TAU_S)
        assert g3 == pytest.approx(v)
        assert analytical != pytest.approx(leaked, rel=1e-9)
