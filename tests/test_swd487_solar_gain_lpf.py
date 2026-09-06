"""SWD-487: first-order low-pass on modelled solar gain watts."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.const import (
    CONF_SOLAR_GAIN_SMOOTHING_TAU_S,
    SOLAR_GAIN_SMOOTHING_TAU_S,
)
from heatingassistant.engine.controller import HeatingMPCController
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.solar_model import (
    coerce_solar_gain_smoothing_tau_s,
    smooth_solar_gain_schedule,
    smooth_solar_gain_step,
)
from heatingassistant.engine.thermal_model import HouseModel, Room, Window
from heatingassistant.mqtt.bridge import InMemoryMqttBus


pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc)
DT = 900.0
TAU = SOLAR_GAIN_SMOOTHING_TAU_S


def _windowed_controller(
    tau_s: float | None = None,
) -> HeatingMPCController:
    living = Room(
        name="living_room",
        thermal_mass=5_000_000.0,
        r_external=0.05,
        temperature=21.0,
        setpoint=21.0,
        windows=[Window(area=8.0, orientation=180.0, tilt=90.0)],
    )
    model = HouseModel([living])
    sources = [ElectricHeater("lr", "living_room", max_power=4000.0)]
    kwargs = {}
    if tau_s is not None:
        kwargs["solar_gain_smoothing_tau_s"] = tau_s
    return HeatingMPCController(model, sources, horizon=3, dt=DT, **kwargs)


def test_ema_seeds_then_lags_a_jump() -> None:
    assert smooth_solar_gain_step(None, 100.0, DT, TAU) == 100.0
    lagged = smooth_solar_gain_step(100.0, 25.0, DT, TAU)
    assert 25.0 < lagged < 100.0
    assert lagged == pytest.approx(
        (1.0 - math.exp(-DT / TAU)) * 25.0
        + math.exp(-DT / TAU) * 100.0
    )


def test_zero_tau_is_identity() -> None:
    assert smooth_solar_gain_step(40.0, 10.0, DT, 0.0) == 10.0


def test_schedule_persists_k0_not_horizon_tail() -> None:
    rooms = ["living_room"]
    schedules = [
        {"living_room": 100.0},
        {"living_room": 10.0},
        {"living_room": 10.0},
    ]
    filtered, k0 = smooth_solar_gain_schedule(schedules, None, DT, TAU, rooms)
    assert k0["living_room"] == pytest.approx(filtered[0]["living_room"])
    assert filtered[-1]["living_room"] < filtered[0]["living_room"]
    assert k0["living_room"] != pytest.approx(filtered[-1]["living_room"])


def test_first_k0_matches_instantaneous_cloud() -> None:
    ctrl = _windowed_controller()
    cloudy = ctrl._forecast_solar(
        NOW,
        cloud_forecast=[1.0, 1.0, 1.0, 1.0],
        cloud_cover_now=1.0,
    )
    assert cloudy[0]["living_room"] == pytest.approx(
        ctrl._room_gain("living_room", NOW, cloud_cover=1.0, ghi=None),
        rel=1e-12,
    )


def test_persisted_cloud_jump_does_not_reach_instantaneous() -> None:
    ctrl = _windowed_controller()
    clear = ctrl._forecast_solar(NOW, persist=True)
    overcast_inst = ctrl._room_gain("living_room", NOW, cloud_cover=1.0, ghi=None)
    cloudy = ctrl._forecast_solar(
        NOW,
        cloud_forecast=[1.0, 1.0, 1.0, 1.0],
        cloud_cover_now=1.0,
        persist=True,
    )
    k0 = cloudy[0]["living_room"]
    assert overcast_inst < k0 < clear[0]["living_room"]
    expected = smooth_solar_gain_step(
        clear[0]["living_room"], overcast_inst, DT, TAU,
    )
    assert k0 == pytest.approx(expected)


def test_ghi_none_observation_is_cloud_clear_then_ema() -> None:
    ctrl = _windowed_controller()
    schedules = ctrl._forecast_solar(
        NOW,
        cloud_forecast=[1.0, 1.0, 1.0],
        cloud_cover_now=1.0,
        ghi_forecast=[500.0, None, None],
        ghi_now=500.0,
    )
    g0 = ctrl._room_gain("living_room", NOW, cloud_cover=1.0, ghi=500.0)
    t1 = NOW + timedelta(seconds=DT)
    g1 = ctrl._room_gain("living_room", t1, cloud_cover=1.0, ghi=500.0)
    t2 = NOW + timedelta(seconds=DT * 2)
    g2_obs = ctrl._room_gain("living_room", t2, cloud_cover=1.0, ghi=None)
    leaked = ctrl._room_gain("living_room", t2, cloud_cover=1.0, ghi=500.0)
    v = smooth_solar_gain_step(None, g0, DT, TAU)
    v = smooth_solar_gain_step(v, g1, DT, TAU)
    v = smooth_solar_gain_step(v, g2_obs, DT, TAU)
    assert schedules[2]["living_room"] == pytest.approx(v)
    assert g2_obs != pytest.approx(leaked, rel=1e-9)


def test_coerce_solar_gain_smoothing_tau_s() -> None:
    assert coerce_solar_gain_smoothing_tau_s(None) == SOLAR_GAIN_SMOOTHING_TAU_S
    assert coerce_solar_gain_smoothing_tau_s("not-a-number") == SOLAR_GAIN_SMOOTHING_TAU_S
    assert coerce_solar_gain_smoothing_tau_s(float("nan")) == SOLAR_GAIN_SMOOTHING_TAU_S
    assert coerce_solar_gain_smoothing_tau_s(-12.0) == 0.0
    assert coerce_solar_gain_smoothing_tau_s(600) == pytest.approx(600.0)


def test_controller_uses_configured_tau() -> None:
    default = _windowed_controller()
    assert default._solar_gain_tau_s == pytest.approx(SOLAR_GAIN_SMOOTHING_TAU_S)
    custom = _windowed_controller(tau_s=3600.0)
    assert custom._solar_gain_tau_s == pytest.approx(3600.0)

    seeded = custom._forecast_solar(NOW, persist=True)
    overcast_inst = custom._room_gain("living_room", NOW, cloud_cover=1.0, ghi=None)
    lagged = custom._forecast_solar(
        NOW,
        cloud_forecast=[1.0, 1.0, 1.0, 1.0],
        cloud_cover_now=1.0,
        persist=True,
    )
    expected = smooth_solar_gain_step(
        seeded[0]["living_room"], overcast_inst, DT, 3600.0,
    )
    assert lagged[0]["living_room"] == pytest.approx(expected)

    default_seed = default._forecast_solar(NOW, persist=True)
    default_over = default._forecast_solar(
        NOW,
        cloud_forecast=[1.0, 1.0, 1.0, 1.0],
        cloud_cover_now=1.0,
        persist=True,
    )
    # Longer τ stays closer to the previous (clear) watt.
    assert abs(lagged[0]["living_room"] - seeded[0]["living_room"]) < abs(
        default_over[0]["living_room"] - default_seed[0]["living_room"]
    )


def test_model_config_exposes_solar_gain_smoothing_tau(tmp_path) -> None:
    default_rt = HeatingRuntime(
        tmp_path / "default",
        bus=InMemoryMqttBus(),
        options={"instance_id": "t"},
    )
    assert default_rt.model_config()["system"][
        CONF_SOLAR_GAIN_SMOOTHING_TAU_S
    ] == pytest.approx(SOLAR_GAIN_SMOOTHING_TAU_S)
    custom_rt = HeatingRuntime(
        tmp_path / "custom",
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "t",
            CONF_SOLAR_GAIN_SMOOTHING_TAU_S: 600.0,
        },
    )
    assert custom_rt.model_config()["system"][
        CONF_SOLAR_GAIN_SMOOTHING_TAU_S
    ] == pytest.approx(600.0)


def test_tau_zero_matches_instantaneous_horizon() -> None:
    ctrl = _windowed_controller()
    ctrl._solar_gain_tau_s = 0.0
    t2 = NOW + timedelta(seconds=DT * 2)
    schedules = ctrl._forecast_solar(
        NOW,
        cloud_forecast=[1.0, 1.0, 1.0],
        cloud_cover_now=1.0,
        ghi_forecast=[500.0, None, None],
        ghi_now=500.0,
    )
    assert schedules[2]["living_room"] == pytest.approx(
        ctrl._room_gain("living_room", t2, cloud_cover=1.0, ghi=None),
        rel=1e-12,
    )
