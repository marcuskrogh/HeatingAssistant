"""Unit tests for KPI calculation functions (WP-2)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.kpi import (
    RoomSnapshot,
    comfort_deviation_c,
    room_comfort_deviation_c,
    comfort_index_pct,
    mean_tracking_error_c,
    aggregate_model_fit_r2,
    model_fit_label,
    effective_cop_display,
    horizon_comfort_risk_steps,
    heat_loss_gauge_max,
    solar_gain_gauge_max,
    house_heating_power_gauge_max,
    total_heating_power_kw,
    room_temperature,
    MODEL_FIT_LABEL_GOOD,
    MODEL_FIT_LABEL_ACCEPTABLE,
    MODEL_FIT_LABEL_POOR,
    MODEL_FIT_LABEL_EMPTY,
    HEAT_LOSS_GAUGE_FLOOR_W,
    SOLAR_GAIN_GAUGE_DEFAULT_MAX_W,
)


def _room(
    slug: str,
    *,
    active: bool = True,
    temp_filtered=None,
    temp_measured=None,
    lower=20.0,
    upper=22.0,
    setpoint=21.0,
    r2=None,
) -> RoomSnapshot:
    return RoomSnapshot(
        slug=slug,
        room_active=active,
        temperature_filtered=temp_filtered,
        temperature_measured=temp_measured,
        constraint_lower=lower,
        constraint_upper=upper,
        setpoint=setpoint,
        model_fit_r2=r2,
    )


# ── Comfort deviation ─────────────────────────────────────────────────────


def test_comfort_deviation_in_band():
    assert comfort_deviation_c(21.0, 20.0, 22.0) == pytest.approx(0.0)


def test_comfort_deviation_below_band():
    assert comfort_deviation_c(19.2, 20.0, 22.0) == pytest.approx(0.8)


def test_comfort_deviation_above_band():
    assert comfort_deviation_c(22.5, 20.0, 22.0) == pytest.approx(0.5)


def test_comfort_deviation_on_boundary():
    assert comfort_deviation_c(20.0, 20.0, 22.0) == pytest.approx(0.0)
    assert comfort_deviation_c(22.0, 20.0, 22.0) == pytest.approx(0.0)


def test_room_comfort_deviation_inactive_returns_none():
    room = _room("bed", active=False, temp_filtered=18.0)
    assert room_comfort_deviation_c(room) is None


def test_room_comfort_deviation_missing_bounds_returns_none():
    room = _room("bed", temp_filtered=21.0, lower=None, upper=22.0)
    assert room_comfort_deviation_c(room) is None


def test_room_comfort_deviation_prefers_filtered():
    room = _room("bed", temp_filtered=21.0, temp_measured=19.0)
    assert room_comfort_deviation_c(room) == pytest.approx(0.0)


# ── Comfort index ─────────────────────────────────────────────────────────


def test_comfort_index_all_in_band():
    rooms = [
        _room("living", temp_filtered=21.0),
        _room("bed", temp_filtered=21.5),
    ]
    assert comfort_index_pct(rooms) == pytest.approx(100.0)


def test_comfort_index_partial_in_band():
    rooms = [
        _room("living", temp_filtered=21.0),
        _room("bed", temp_filtered=23.0),
    ]
    assert comfort_index_pct(rooms) == pytest.approx(50.0)


def test_comfort_index_none_in_band():
    rooms = [
        _room("living", temp_filtered=19.0),
        _room("bed", temp_filtered=23.5),
    ]
    assert comfort_index_pct(rooms) == pytest.approx(0.0)


def test_comfort_index_excludes_inactive_rooms():
    rooms = [
        _room("living", temp_filtered=21.0),
        _room("bed", active=False, temp_filtered=15.0),
    ]
    assert comfort_index_pct(rooms) == pytest.approx(100.0)


def test_comfort_index_excludes_missing_temperature():
    rooms = [
        _room("living", temp_filtered=21.0),
        _room("bed", temp_filtered=None, temp_measured=None),
    ]
    assert comfort_index_pct(rooms) == pytest.approx(100.0)


def test_comfort_index_all_rooms_off_returns_none():
    rooms = [
        _room("living", active=False),
        _room("bed", active=False),
    ]
    assert comfort_index_pct(rooms) is None


def test_comfort_index_all_active_missing_data_returns_none():
    rooms = [
        _room("living", temp_filtered=None),
        _room("bed", temp_measured=None),
    ]
    assert comfort_index_pct(rooms) is None


def test_comfort_index_single_room():
    assert comfort_index_pct([_room("studio", temp_filtered=21.0)]) == pytest.approx(100.0)
    assert comfort_index_pct([_room("studio", temp_filtered=25.0)]) == pytest.approx(0.0)


def test_comfort_index_falls_back_to_measured():
    rooms = [_room("living", temp_measured=21.0)]
    assert comfort_index_pct(rooms) == pytest.approx(100.0)


# ── Mean tracking error ───────────────────────────────────────────────────


def test_mean_tracking_error_perfect():
    rooms = [
        _room("living", temp_filtered=21.0, setpoint=21.0),
        _room("bed", temp_filtered=20.0, setpoint=20.0),
    ]
    assert mean_tracking_error_c(rooms) == pytest.approx(0.0)


def test_mean_tracking_error_with_deviation():
    rooms = [
        _room("living", temp_filtered=21.5, setpoint=21.0),
        _room("bed", temp_filtered=19.0, setpoint=20.0),
    ]
    assert mean_tracking_error_c(rooms) == pytest.approx(0.75)


def test_mean_tracking_error_excludes_inactive():
    rooms = [
        _room("living", temp_filtered=21.0, setpoint=21.0),
        _room("bed", active=False, temp_filtered=15.0, setpoint=20.0),
    ]
    assert mean_tracking_error_c(rooms) == pytest.approx(0.0)


def test_mean_tracking_error_excludes_missing_temperature():
    rooms = [
        _room("living", temp_filtered=22.0, setpoint=21.0),
        _room("bed", temp_filtered=None, setpoint=20.0),
    ]
    assert mean_tracking_error_c(rooms) == pytest.approx(1.0)


def test_mean_tracking_error_no_active_rooms_returns_none():
    assert mean_tracking_error_c([_room("bed", active=False)]) is None


def test_mean_tracking_error_all_missing_returns_none():
    rooms = [_room("living", temp_filtered=None, setpoint=21.0)]
    assert mean_tracking_error_c(rooms) is None


# ── Model fit aggregation ─────────────────────────────────────────────────


def test_model_fit_label_thresholds():
    assert model_fit_label(0.9) == MODEL_FIT_LABEL_GOOD
    assert model_fit_label(0.81) == MODEL_FIT_LABEL_GOOD
    assert model_fit_label(0.8) == MODEL_FIT_LABEL_ACCEPTABLE
    assert model_fit_label(0.6) == MODEL_FIT_LABEL_ACCEPTABLE
    assert model_fit_label(0.5) == MODEL_FIT_LABEL_POOR
    assert model_fit_label(0.1) == MODEL_FIT_LABEL_POOR


def test_aggregate_model_fit_good():
    mean_r2, label = aggregate_model_fit_r2([0.85, 0.9, 0.88])
    assert mean_r2 == pytest.approx(0.876666, abs=1e-4)
    assert label == MODEL_FIT_LABEL_GOOD


def test_aggregate_model_fit_acceptable():
    mean_r2, label = aggregate_model_fit_r2([0.6, 0.7])
    assert mean_r2 == pytest.approx(0.65)
    assert label == MODEL_FIT_LABEL_ACCEPTABLE


def test_aggregate_model_fit_poor():
    mean_r2, label = aggregate_model_fit_r2([0.3, 0.4])
    assert mean_r2 == pytest.approx(0.35)
    assert label == MODEL_FIT_LABEL_POOR


def test_aggregate_model_fit_empty():
    mean_r2, label = aggregate_model_fit_r2([])
    assert mean_r2 == pytest.approx(0.0)
    assert label == MODEL_FIT_LABEL_EMPTY


def test_aggregate_model_fit_skips_none():
    mean_r2, label = aggregate_model_fit_r2([None, 0.9, None])
    assert mean_r2 == pytest.approx(0.9)
    assert label == MODEL_FIT_LABEL_GOOD


def test_aggregate_model_fit_includes_negative_r2():
    mean_r2, label = aggregate_model_fit_r2([-0.2, 0.9])
    assert mean_r2 == pytest.approx(0.35)
    assert label == MODEL_FIT_LABEL_POOR


# ── Effective COP display ─────────────────────────────────────────────────


def test_effective_cop_display_with_heat_pump():
    assert effective_cop_display(3.42, has_heat_pump=True) == pytest.approx(3.42)


def test_effective_cop_display_resistive_only_hidden():
    assert effective_cop_display(1.0, has_heat_pump=False) is None


def test_effective_cop_display_zero_hidden():
    assert effective_cop_display(0.0, has_heat_pump=True) is None


def test_effective_cop_display_none_hidden():
    assert effective_cop_display(None, has_heat_pump=True) is None


# ── Horizon comfort risk ──────────────────────────────────────────────────


def test_horizon_comfort_risk_all_in_band():
    assert horizon_comfort_risk_steps([21.0, 21.5, 20.5], 20.0, 22.0) == 0


def test_horizon_comfort_risk_some_out_of_band():
    temps = [21.0, 19.0, 23.0, 21.0]
    assert horizon_comfort_risk_steps(temps, 20.0, 22.0) == 2


def test_horizon_comfort_risk_skips_non_finite():
    assert horizon_comfort_risk_steps([21.0, float("nan"), 25.0], 20.0, 22.0) == 1


def test_horizon_comfort_risk_invalid_bounds():
    assert horizon_comfort_risk_steps([25.0], float("nan"), 22.0) == 0


# ── Gauge bounds and formatting helpers ───────────────────────────────────


def test_heat_loss_gauge_max_floor():
    assert heat_loss_gauge_max(500.0) == pytest.approx(HEAT_LOSS_GAUGE_FLOOR_W)


def test_heat_loss_gauge_max_scales_up():
    assert heat_loss_gauge_max(2500.0) == pytest.approx(3000.0)
    assert heat_loss_gauge_max(3000.0) == pytest.approx(3600.0)


def test_solar_gain_gauge_max_default():
    assert solar_gain_gauge_max(200.0) == pytest.approx(SOLAR_GAIN_GAUGE_DEFAULT_MAX_W)


def test_solar_gain_gauge_max_expands():
    assert solar_gain_gauge_max(1500.0) == pytest.approx(1500.0)


def test_house_power_gauge_max_with_rated_capacity():
    assert house_heating_power_gauge_max(2000.0, rated_capacity_w=8000.0) == pytest.approx(8000.0)


def test_house_power_gauge_max_rated_floor():
    assert house_heating_power_gauge_max(2000.0, rated_capacity_w=2000.0) == pytest.approx(3000.0)


def test_house_power_gauge_max_live_fallback():
    assert house_heating_power_gauge_max(5000.0) == pytest.approx(10000.0)
    assert house_heating_power_gauge_max(9000.0) == pytest.approx(10800.0)


def test_total_heating_power_kw():
    assert total_heating_power_kw(3456.0) == pytest.approx(3.5)


def test_room_temperature_prefers_filtered():
    room = _room("x", temp_filtered=21.1, temp_measured=19.0)
    assert room_temperature(room) == pytest.approx(21.1)
