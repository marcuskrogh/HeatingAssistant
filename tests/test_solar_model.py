"""Unit tests for the solar model."""

import sys
import os
import math
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.solar_model import (
    solar_angles,
    clear_sky_dni,
    clear_sky_dhi,
    angle_of_incidence,
    window_solar_gain,
    room_solar_gains,
    cloud_attenuation_factor,
)
from custom_components.heating_assistant.thermal_model import Window


LATITUDE = 55.0    # Copenhagen-ish
LONGITUDE = 12.0


class TestSolarAngles:
    def test_no_sun_at_night(self):
        # 2 AM UTC in summer should still be "night" at high latitude
        dt = datetime(2024, 6, 21, 1, 0, 0, tzinfo=timezone.utc)
        altitude, _ = solar_angles(dt, LATITUDE, LONGITUDE)
        assert altitude < math.radians(10)  # very low or below horizon

    def test_sun_at_solar_noon(self):
        # Solar noon on summer solstice – altitude should be positive
        dt = datetime(2024, 6, 21, 10, 0, 0, tzinfo=timezone.utc)  # approx solar noon
        altitude, _ = solar_angles(dt, LATITUDE, LONGITUDE)
        assert altitude > 0.0

    def test_altitude_range(self):
        dt = datetime(2024, 3, 21, 12, 0, 0, tzinfo=timezone.utc)
        altitude, azimuth = solar_angles(dt, LATITUDE, LONGITUDE)
        assert -math.pi / 2 <= altitude <= math.pi / 2
        assert 0.0 <= azimuth <= 360.0


class TestClearSkyDNI:
    def test_zero_at_night(self):
        assert clear_sky_dni(altitude=-0.1, n=172) == pytest.approx(0.0)

    def test_positive_at_midday(self):
        altitude = math.radians(50.0)
        dni = clear_sky_dni(altitude=altitude, n=172)
        assert dni > 0.0
        assert dni < 1361.0  # less than solar constant

    def test_increases_with_altitude(self):
        n = 172
        dni_low = clear_sky_dni(altitude=math.radians(20.0), n=n)
        dni_high = clear_sky_dni(altitude=math.radians(60.0), n=n)
        assert dni_high > dni_low


class TestAngleOfIncidence:
    def test_normal_incidence(self):
        # Sun directly in front of a south-facing vertical surface at solar noon
        # solar altitude 45°, azimuth 180° (south), surface tilt 90°, orientation 180° (south)
        theta = angle_of_incidence(
            altitude=math.radians(45.0),
            azimuth_deg=180.0,
            surface_tilt=90.0,
            surface_azimuth=180.0,
        )
        # cos(theta) = cos(45°)*cos(0°)*sin(90°) + sin(45°)*cos(90°)
        #            = cos(45°) = 0.707
        assert math.cos(theta) == pytest.approx(math.cos(math.radians(45.0)), abs=1e-6)

    def test_grazing_incidence(self):
        # Sun behind the surface (azimuth 0°, north), south-facing surface
        theta = angle_of_incidence(
            altitude=math.radians(45.0),
            azimuth_deg=0.0,
            surface_tilt=90.0,
            surface_azimuth=180.0,
        )
        assert math.cos(theta) == pytest.approx(0.0, abs=1e-6)


class TestWindowSolarGain:
    def test_zero_gain_at_night(self):
        window = Window(area=2.0, orientation=180.0, tilt=90.0)
        dt = datetime(2024, 1, 15, 1, 0, 0, tzinfo=timezone.utc)  # midnight
        gain = window_solar_gain(window, dt, LATITUDE, LONGITUDE)
        assert gain == pytest.approx(0.0)

    def test_positive_gain_at_daytime(self):
        window = Window(area=2.0, orientation=180.0, tilt=90.0)
        # Midday in summer
        dt = datetime(2024, 6, 21, 10, 0, 0, tzinfo=timezone.utc)
        gain = window_solar_gain(window, dt, LATITUDE, LONGITUDE)
        assert gain >= 0.0

    def test_larger_window_more_gain(self):
        dt = datetime(2024, 6, 21, 10, 0, 0, tzinfo=timezone.utc)
        small = Window(area=1.0, orientation=180.0, tilt=90.0)
        large = Window(area=4.0, orientation=180.0, tilt=90.0)
        g_small = window_solar_gain(small, dt, LATITUDE, LONGITUDE)
        g_large = window_solar_gain(large, dt, LATITUDE, LONGITUDE)
        assert g_large == pytest.approx(g_small * 4.0, rel=1e-6)

    def test_room_solar_gains_sums_windows(self):
        dt = datetime(2024, 6, 21, 10, 0, 0, tzinfo=timezone.utc)
        w1 = Window(area=1.0, orientation=180.0, tilt=90.0)
        w2 = Window(area=2.0, orientation=180.0, tilt=90.0)
        total = room_solar_gains([w1, w2], dt, LATITUDE, LONGITUDE)
        expected = (
            window_solar_gain(w1, dt, LATITUDE, LONGITUDE)
            + window_solar_gain(w2, dt, LATITUDE, LONGITUDE)
        )
        assert total == pytest.approx(expected)

    def test_empty_room_no_gain(self):
        dt = datetime(2024, 6, 21, 10, 0, 0, tzinfo=timezone.utc)
        assert room_solar_gains([], dt, LATITUDE, LONGITUDE) == pytest.approx(0.0)


class TestCloudAttenuation:
    def test_factor_clear_sky(self):
        assert cloud_attenuation_factor(0.0) == pytest.approx(1.0)

    def test_factor_overcast(self):
        # Kasten–Czeplak at c=1: 1 − 0.75 · 1^3.4 = 0.25
        assert cloud_attenuation_factor(1.0) == pytest.approx(0.25)

    def test_factor_half_cover(self):
        # Around 0.5 the factor should sit comfortably between clear and overcast
        f = cloud_attenuation_factor(0.5)
        assert 0.6 < f < 0.95

    def test_factor_clamped_below_zero(self):
        assert cloud_attenuation_factor(-0.2) == pytest.approx(1.0)

    def test_factor_clamped_above_one(self):
        assert cloud_attenuation_factor(2.0) == pytest.approx(0.25)

    def test_factor_monotonically_decreasing(self):
        cs = [0.0, 0.25, 0.5, 0.75, 1.0]
        factors = [cloud_attenuation_factor(c) for c in cs]
        assert factors == sorted(factors, reverse=True)

    def test_window_gain_unchanged_without_cloud(self):
        dt = datetime(2024, 6, 21, 10, 0, 0, tzinfo=timezone.utc)
        w = Window(area=2.0, orientation=180.0, tilt=90.0)
        baseline = window_solar_gain(w, dt, LATITUDE, LONGITUDE)
        explicit_clear = window_solar_gain(w, dt, LATITUDE, LONGITUDE, cloud_cover=0.0)
        none_cloud = window_solar_gain(w, dt, LATITUDE, LONGITUDE, cloud_cover=None)
        assert explicit_clear == pytest.approx(baseline)
        assert none_cloud == pytest.approx(baseline)

    def test_window_gain_attenuated_when_overcast(self):
        dt = datetime(2024, 6, 21, 10, 0, 0, tzinfo=timezone.utc)
        w = Window(area=2.0, orientation=180.0, tilt=90.0)
        clear = window_solar_gain(w, dt, LATITUDE, LONGITUDE)
        overcast = window_solar_gain(w, dt, LATITUDE, LONGITUDE, cloud_cover=1.0)
        assert overcast == pytest.approx(0.25 * clear, rel=1e-9)

    def test_window_gain_zero_at_night_regardless_of_cloud(self):
        dt = datetime(2024, 6, 21, 1, 0, 0, tzinfo=timezone.utc)
        w = Window(area=2.0, orientation=180.0, tilt=90.0)
        assert window_solar_gain(w, dt, LATITUDE, LONGITUDE, cloud_cover=0.5) == 0.0

    def test_room_gain_applies_cloud_factor(self):
        dt = datetime(2024, 6, 21, 10, 0, 0, tzinfo=timezone.utc)
        windows = [
            Window(area=1.0, orientation=180.0, tilt=90.0),
            Window(area=2.0, orientation=180.0, tilt=90.0),
        ]
        clear = room_solar_gains(windows, dt, LATITUDE, LONGITUDE)
        overcast = room_solar_gains(windows, dt, LATITUDE, LONGITUDE, cloud_cover=1.0)
        assert overcast == pytest.approx(0.25 * clear, rel=1e-9)
