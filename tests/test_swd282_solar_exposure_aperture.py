"""SWD-282: App room build must apply Option A solar exposure aperture."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from heatingassistant.app.forecast_payload import build_app_forecast_payload
from heatingassistant.engine import const
from heatingassistant.engine.control_loop import ControlEngine, _build_house_model


pytestmark = pytest.mark.unit


def test_build_house_model_maps_high_exposure_and_facing() -> None:
    model = _build_house_model(
        [
            {
                "name": "Living Room",
                "solar_exposure": "high",
                "solar_facing": 270,
                "windows": [],
            }
        ]
    )
    room = model.rooms["Living Room"]
    assert room.solar_exposure_aperture == pytest.approx(
        const.SOLAR_EXPOSURE_TO_APERTURE[const.SOLAR_EXPOSURE_HIGH]
    )
    assert room.solar_facing == pytest.approx(270.0)
    assert room.windows == []


def test_build_house_model_none_exposure_stays_zero() -> None:
    model = _build_house_model(
        [{"name": "Studio", "solar_exposure": "none", "windows": []}]
    )
    assert model.rooms["Studio"].solar_exposure_aperture == pytest.approx(0.0)


def test_control_engine_solar_forecast_varies_with_high_exposure() -> None:
    """Regression: High exposure (no windows) must produce non-zero daytime solar."""
    engine = ControlEngine(
        {
            "update_interval": 900,
            "horizon": 8,
            "latitude": 55.7,
            "longitude": 12.6,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 22.0,
                    "solar_exposure": "high",
                    "solar_facing": 270,
                    "windows": [],
                }
            ],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 2000.0,
                }
            ],
        }
    )
    room = engine.model.rooms["Living Room"]
    assert room.solar_exposure_aperture == pytest.approx(6.0)

    # Midday summer UTC — sun above horizon in Denmark.
    noon = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    if engine._controller is None:
        pytest.skip("MPC controller unavailable in this environment")

    engine._controller.compute(
        outdoor_temp=18.0,
        solar_gains=None,
        now=noon,
        cloud_cover_now=0.2,
    )
    engine._cache_controller_forecast(engine._controller)
    snap = engine.forecast_snapshot()
    solar = snap.get("solar_forecast") or []
    assert solar, "expected solar_forecast entries"
    gains = [float(step.get("Living Room", 0.0)) for step in solar if isinstance(step, dict)]
    assert any(g > 1.0 for g in gains), f"expected daytime solar > 1 W, got {gains}"
    assert max(gains) - min(gains) > 1.0, f"expected solar dynamics, got {gains}"

    payload = build_app_forecast_payload(
        rooms=[{"name": "Living Room", "setpoint": 22.0}],
        room_temperatures={"Living Room": 21.5},
        outdoor_temp=18.0,
        energy_price=0.2,
        snapshot=snap,
        plot_forecast_hours=2.0,
        now=noon,
    )
    fc = payload["rooms"]["living_room"]["forecast"]
    solar_vals = [step.get("solar_gain") for step in fc if step.get("solar_gain") is not None]
    assert solar_vals
    assert any(v > 1.0 for v in solar_vals)
    assert max(solar_vals) != min(solar_vals)


def test_build_rooms_from_theta_preserves_solar_exposure() -> None:
    """ID rebuild must not wipe Option A aperture/facing (SWD-282 secondary)."""
    import numpy as np

    from heatingassistant.engine.estimation.model_build import _build_rooms_from_theta
    from heatingassistant.engine.thermal_model import Room

    room = Room(
        name="Living Room",
        thermal_mass=1e6,
        r_external=0.05,
        solar_exposure_aperture=6.0,
        solar_facing=270.0,
    )
    est = SimpleNamespace(
        _rooms=[room],
        _log_solar_prior_full=np.array([0.0]),
        _c_air_prior_full=np.array([0.05]),
        _r_aw_prior_full=np.array([0.05]),
    )
    layout = SimpleNamespace(identifiable_solar=[], identifiable_splits=[])
    rebuilt = _build_rooms_from_theta(
        est,
        layout,
        np.log(np.array([1e6])),
        np.log(np.array([0.05])),
        None,
    )
    assert rebuilt[0].solar_exposure_aperture == pytest.approx(6.0)
    assert rebuilt[0].solar_facing == pytest.approx(270.0)
