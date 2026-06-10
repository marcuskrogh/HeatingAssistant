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
    clear_sky_plane_poa,
    angle_of_incidence,
    window_solar_gain,
    room_solar_gains,
    room_solar_gains_from_exposure,
    cloud_attenuation_factor,
    erbs_diffuse_fraction,
    extraterrestrial_normal_irradiance,
    ghi_to_dni_dhi,
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
        # Cloud attenuation is component-aware: the total (horizontal) GHI
        # drops to 25 % (Kasten-Czeplak) and the *beam* essentially
        # vanishes, while the diffuse share grows.  A vertical window keeps
        # collecting diffuse from half the sky plus the ground, so its POA
        # does not scale with the GHI factor — assert the real invariants:
        # attenuation and a collapsed beam.
        assert 0.0 < overcast < clear
        from custom_components.heating_assistant.solar_model import (
            _day_of_year, _intensity_dni_dhi, solar_angles,
        )
        alt, _az = solar_angles(dt, LATITUDE, LONGITUDE)
        n_day = _day_of_year(dt)
        dni_clear, dhi_clear = _intensity_dni_dhi(alt, n_day, None, None)
        dni_oc, dhi_oc = _intensity_dni_dhi(alt, n_day, 1.0, None)
        assert dni_oc < 0.05 * dni_clear           # beam collapses
        assert dhi_oc > dhi_clear                  # diffuse share grows
        # Total horizontal GHI follows the Kasten-Czeplak factor exactly.
        import math as _math
        ghi_clear = dni_clear * _math.sin(alt) + dhi_clear
        ghi_oc = dni_oc * _math.sin(alt) + dhi_oc
        assert ghi_oc == pytest.approx(0.25 * ghi_clear, rel=1e-9)

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
        # Attenuated, monotonic in cloud cover, never negative.
        assert 0.0 < overcast < clear
        half = room_solar_gains(windows, dt, LATITUDE, LONGITUDE, cloud_cover=0.5)
        assert overcast < half < clear


# Midday in summer, sun well above the horizon for the tests below.
NOON = datetime(2024, 6, 21, 11, 0, tzinfo=timezone.utc)


class TestClearSkyPlanePOA:
    def test_zero_at_night(self):
        midnight = datetime(2024, 12, 21, 0, 0, tzinfo=timezone.utc)
        assert clear_sky_plane_poa(midnight, 60.0, 12.0, 90.0, 180.0) == 0.0

    def test_positive_south_facing_midday(self):
        poa = clear_sky_plane_poa(NOON, LATITUDE, LONGITUDE, 90.0, 180.0)
        assert poa > 0.0

    def test_sun_facing_beats_away_facing(self):
        # Convention-agnostic: a vertical plane oriented toward the modelled
        # solar azimuth collects more than one facing directly away from it.
        _alt, az = solar_angles(NOON, LATITUDE, LONGITUDE)
        toward = clear_sky_plane_poa(NOON, LATITUDE, LONGITUDE, 90.0, az)
        away = clear_sky_plane_poa(NOON, LATITUDE, LONGITUDE, 90.0, (az + 180.0) % 360.0)
        assert toward > away


class TestErbsDecomposition:
    def test_zero_at_night(self):
        assert ghi_to_dni_dhi(0.0, -0.1, 172) == (0.0, 0.0)
        assert ghi_to_dni_dhi(500.0, -0.1, 172) == (0.0, 0.0)

    def test_components_sum_to_ghi_horizontal(self):
        alt = math.radians(50.0)
        ghi = 600.0
        dni, dhi = ghi_to_dni_dhi(ghi, alt, 172)
        # GHI = DNI·sin(alt) + DHI  (energy balance on the horizontal)
        assert dni * math.sin(alt) + dhi == pytest.approx(ghi, rel=1e-6)

    def test_overcast_is_mostly_diffuse(self):
        alt = math.radians(40.0)
        n = 172
        ghi_extra = extraterrestrial_normal_irradiance(n) * math.sin(alt)
        low_ghi = 0.15 * ghi_extra          # very cloudy → low clearness
        dni, dhi = ghi_to_dni_dhi(low_ghi, alt, n)
        assert dhi > 0.8 * low_ghi          # Erbs: nearly all diffuse

    def test_clear_has_strong_beam(self):
        alt = math.radians(40.0)
        n = 172
        ghi_extra = extraterrestrial_normal_irradiance(n) * math.sin(alt)
        high_ghi = 0.75 * ghi_extra         # clear → high clearness
        dni, dhi = ghi_to_dni_dhi(high_ghi, alt, n)
        assert dhi < 0.4 * high_ghi          # mostly beam


class TestGhiPrecedence:
    def test_ghi_overrides_cloud(self):
        win = Window(area=2.0, orientation=180.0, tilt=90.0)
        # With GHI provided, cloud_cover is ignored entirely.
        with_cloud = window_solar_gain(
            win, NOON, LATITUDE, LONGITUDE, cloud_cover=1.0, ghi=400.0
        )
        without_cloud = window_solar_gain(
            win, NOON, LATITUDE, LONGITUDE, ghi=400.0
        )
        assert with_cloud == pytest.approx(without_cloud, rel=1e-12)

    def test_zero_ghi_zero_gain(self):
        win = Window(area=2.0, orientation=180.0, tilt=90.0)
        assert window_solar_gain(win, NOON, LATITUDE, LONGITUDE, ghi=0.0) == 0.0

    def test_gain_monotonic_in_ghi(self):
        # Horizontal surface: POA ≡ GHI (DNI·sinα + DHI), so gain is exactly
        # proportional to GHI — convention-independent and strictly monotonic.
        win = Window(area=2.0, orientation=180.0, tilt=0.0)
        low = window_solar_gain(win, NOON, LATITUDE, LONGITUDE, ghi=200.0)
        high = window_solar_gain(win, NOON, LATITUDE, LONGITUDE, ghi=800.0)
        assert 0.0 < low < high
        # ~4× the GHI → ~4× the gain on a horizontal surface.  Not exact:
        # the incidence-angle modifier weights the (GHI-dependent) beam
        # share, introducing a small sub-proportionality.
        assert high == pytest.approx(4.0 * low, rel=0.05)

    def test_ghi_none_keeps_cloud_path(self):
        win = Window(area=2.0, orientation=180.0, tilt=90.0)
        with_cloud = window_solar_gain(
            win, NOON, LATITUDE, LONGITUDE, cloud_cover=1.0, ghi=None
        )
        clear = window_solar_gain(win, NOON, LATITUDE, LONGITUDE)
        # ghi=None must fall back to the cloud-attenuated clear-sky path
        # (attenuated; not the unattenuated clear value).
        assert 0.0 < with_cloud < clear


class TestExposureGain:
    def test_zero_aperture_is_zero(self):
        assert room_solar_gains_from_exposure(0.0, 180.0, NOON, LATITUDE, LONGITUDE) == 0.0

    def test_scales_with_aperture(self):
        g1 = room_solar_gains_from_exposure(1.0, 180.0, NOON, LATITUDE, LONGITUDE)
        g3 = room_solar_gains_from_exposure(3.0, 180.0, NOON, LATITUDE, LONGITUDE)
        assert g1 > 0.0
        assert g3 == pytest.approx(3.0 * g1, rel=1e-9)

    def test_ghi_drives_exposure(self):
        zero = room_solar_gains_from_exposure(
            3.0, 180.0, NOON, LATITUDE, LONGITUDE, ghi=0.0
        )
        lit = room_solar_gains_from_exposure(
            3.0, 180.0, NOON, LATITUDE, LONGITUDE, ghi=600.0
        )
        assert zero == 0.0
        assert lit > 0.0
