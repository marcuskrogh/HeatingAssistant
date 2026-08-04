"""Tests for the tuning-preview MPC solve (preview_tuning_forecast)."""

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from custom_components.heating_assistant.const import (
    CONF_HORIZON,
    CONF_MPC_MODE,
    CONF_TRACKING_WEIGHT,
    MPC_MODE_NONLINEAR,
)
from custom_components.heating_assistant.mpc_mode_validation import (
    MpcModeUnavailableError,
)


def _make_preview_coordinator():
    room = SimpleNamespace(
        temperature=20.5,
        setpoint=21.0,
        windows=[],
        comfort_offset=2.0,
    )
    source = SimpleNamespace(
        name="lr_heater",
        room="living_room",
        current_power=900.0,
        max_power=2000.0,
    )

    coord = SimpleNamespace(
        _horizon=4,
        _update_interval_s=900,
        _tracking_weight=0.0,
        _energy_weight=0.01,
        _smoothing_weight=0.1,
        _soft_constraint_weight=10.0,
        _soft_constraint_linear_weight=0.0,
        _terminal_weight=100.0,
        _energy_price_weight=1.0,
        _sigma_w=0.1,
        _sigma_v=0.5,
        _sigma_b=0.002,
        _latitude=55.0,
        _longitude=12.0,
        _ground_albedo=0.2,
        _room_comfort_offset={"living_room": 2.0},
        _system_enabled=True,
        outdoor_temp=5.0,
        _last_valid_outdoor_temp=5.0,
        solar_gains={"living_room": 50.0},
        filtered_temperatures={"living_room": 20.63},
        outdoor_forecast=[5.0, 4.5, 4.0, 3.5],
        price_forecast=[0.10, 0.11, 0.12, 0.13],
        heat_sources=[source],
        model=SimpleNamespace(
            rooms={"living_room": room},
            room_names=["living_room"],
        ),
        now_utc=datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc),
        dt=900,
        predictions=[],
        linearised_predictions=[],
        heating_schedule=[],
        solar_forecast=[],
        price_forecast_attr=[],
    )
    coord.is_room_enabled = lambda _r: True
    coord.is_window_override_active = lambda _r: False
    coord._read_wind_speed_now = lambda: None
    coord._build_experiment_clamps = lambda _now: {}
    coord._compute_control_trajectory = lambda _nl, n, _dt: SimpleNamespace(
        setpoints={"living_room": [21.0] * n},
        comfort_offsets={"living_room": [2.0] * n},
        q_scales={"living_room": [1.0] * n},
        r_scales={"living_room": [1.0] * n},
        enabled_steps={"living_room": [True] * n},
    )
    coord.sources_for_room = lambda r: [s for s in coord.heat_sources if s.room == r]
    coord.build_forecast_payload = HeatingAssistantCoordinator.build_forecast_payload.__get__(
        coord, HeatingAssistantCoordinator
    )
    coord.controller = SimpleNamespace(
        ekf_state=([20.5], [[1.0]]),
    )
    return coord


def test_preview_returns_error_when_outdoor_unavailable():
    coord = _make_preview_coordinator()
    coord.outdoor_temp = None
    coord._last_valid_outdoor_temp = None

    result = HeatingAssistantCoordinator.preview_tuning_forecast(
        coord, {}, None, {}
    )
    assert result == {"error": "outdoor_temperature_unavailable"}


@patch("custom_components.heating_assistant.controller.factory.HeatingMPCController")
def test_preview_runs_mpc_and_builds_payload(mock_controller_cls):
    coord = _make_preview_coordinator()
    preview_ctrl = MagicMock()
    preview_ctrl.predictions = [{"living_room": 20.6}, {"living_room": 20.7}]
    preview_ctrl.linearised_predictions = []
    preview_ctrl.heating_schedule = [{"living_room": 1200.0}, {"living_room": 1100.0}]
    preview_ctrl.outdoor_forecast = [5.0, 4.5]
    preview_ctrl.solar_forecast = [{"living_room": 50.0}, {"living_room": 60.0}]
    preview_ctrl.price_forecast = [0.10, 0.11]
    mock_controller_cls.return_value = preview_ctrl

    payload = HeatingAssistantCoordinator.preview_tuning_forecast(
        coord,
        {CONF_TRACKING_WEIGHT: 1.5, CONF_HORIZON: 2},
        None,
        {"cloud_cover_now": 0.5},
    )

    mock_controller_cls.assert_called_once()
    kwargs = mock_controller_cls.call_args.kwargs
    assert kwargs["horizon"] == 2
    assert kwargs["tracking_weight"] == 1.5
    preview_ctrl.compute.assert_called_once()
    assert preview_ctrl.compute.call_args.kwargs["run_optimization"] is True
    assert "rooms" in payload
    assert "living_room" in payload["rooms"]


def test_preview_rejects_nonlinear_when_no_nlp_backend():
    coord = _make_preview_coordinator()
    coord._ipopt_available = False
    coord._nonlinear_available = False
    coord._ipopt_unavailable_reason = "ipopt library missing"

    with pytest.raises(MpcModeUnavailableError, match="NLP solver backend"):
        HeatingAssistantCoordinator.preview_tuning_forecast(
            coord,
            {CONF_MPC_MODE: MPC_MODE_NONLINEAR},
            None,
            {},
        )


def test_factory_allows_nonlinear_with_scipy_backend():
    coord = _make_preview_coordinator()
    coord._ipopt_available = False
    coord._nonlinear_available = True
    coord._nonlinear_backend = "scipy"
    from custom_components.heating_assistant.controller.factory import (
        ControllerBuildConfig,
    )

    config = ControllerBuildConfig.from_coordinator(
        coord, overrides={CONF_MPC_MODE: MPC_MODE_NONLINEAR}
    )
    assert config.mpc_mode == MPC_MODE_NONLINEAR
    assert config.nonlinear_backend == "scipy"
