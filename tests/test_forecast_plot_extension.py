"""Tests for the plot-only forecast horizon in build_forecast_payload.

The dashboard can request a display horizon that differs from the controller
horizon.  When it is longer, the final actuation is held flat and the
temperature is simulated forward; when shorter, the forecast is truncated.
Neither changes the controller.
"""

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator


def _make_coordinator():
    room = SimpleNamespace(temperature=20.5, setpoint=21.0, windows=[], comfort_offset=2.0)
    sources = [SimpleNamespace(room="living_room", current_power=900.0, max_power=2000.0)]

    def _predict(horizon, dt, heat_schedule, outdoor_temps, solar_gain_schedule, initial_temps=None):
        base = float(initial_temps["living_room"]) if initial_temps else 20.0
        return [{"living_room": round(base + 0.1 * (k + 1), 3)} for k in range(horizon)]

    coord = SimpleNamespace(
        predictions=[{"living_room": 20.6}, {"living_room": 20.7}],
        linearised_predictions=[],
        heating_schedule=[{"living_room": 1234.0}, {"living_room": 1100.0}],
        solar_forecast=[{"living_room": 50.0}, {"living_room": 60.0}],
        outdoor_forecast=[5.0, 4.5],
        price_forecast=[0.10, 0.11, 0.12],
        filtered_temperatures={"living_room": 20.63},
        solar_gains={"living_room": 50.0},
        heat_sources=sources,
        model=SimpleNamespace(
            rooms={"living_room": room},
            room_names=["living_room"],
            predict=_predict,
        ),
        outdoor_temp=5.0,
        dt=900,
        _control_trajectory=None,
        _sources_by_room={"living_room": sources},
        is_room_enabled=lambda r: True,
        now_utc=datetime(2026, 1, 5, 7, 30, tzinfo=timezone.utc),
    )
    coord.sources_for_room = lambda r: [s for s in coord.heat_sources if s.room == r]
    return coord


def test_default_horizon_unchanged():
    coord = _make_coordinator()
    payload = HeatingAssistantCoordinator.build_forecast_payload(coord, room_names=["living_room"])
    fc = payload["rooms"]["living_room"]["forecast"]
    # now entry + 2 controller steps
    assert len(fc) == 3
    assert payload["rooms"]["living_room"]["plot_horizon_steps"] == 2
    assert not any(e.get("extended") for e in fc)


def test_extended_horizon_holds_final_actuation():
    coord = _make_coordinator()
    payload = HeatingAssistantCoordinator.build_forecast_payload(
        coord, room_names=["living_room"], plot_forecast_steps=4
    )
    room_fc = payload["rooms"]["living_room"]
    fc = room_fc["forecast"]
    # now entry + 4 steps
    assert len(fc) == 5
    assert room_fc["plot_horizon_steps"] == 4
    # the controller horizon metadata still reports the real horizon
    assert room_fc["horizon_steps"] == 2

    extended = [e for e in fc if e.get("extended")]
    assert len(extended) == 2
    # final actuation (last heating_schedule value) held flat over the extension
    for e in extended:
        assert e["heating_power"] == 1100.0
        assert "temperature" in e
    # outdoor forecast in the top-level array also spans the full window
    assert len(payload["outdoor_forecast"]) == 5


def test_price_forecast_tracks_display_horizon():
    coord = _make_coordinator()
    # Extended: price held flat past the available forecast (now + 4 steps = 5 points).
    extended = HeatingAssistantCoordinator.build_forecast_payload(
        coord, room_names=["living_room"], plot_forecast_steps=4
    )
    prices = extended["price_forecast"]
    assert len(prices) == 5
    assert prices[-1]["price"] == 0.12  # last known price held flat
    # Truncated: fewer price points than the controller horizon.
    truncated = HeatingAssistantCoordinator.build_forecast_payload(
        coord, room_names=["living_room"], plot_forecast_steps=1
    )
    assert len(truncated["price_forecast"]) == 2  # now + 1 step
    # Default (no plot horizon) keeps the full price forecast.
    default = HeatingAssistantCoordinator.build_forecast_payload(
        coord, room_names=["living_room"]
    )
    assert len(default["price_forecast"]) == 3


def test_truncated_horizon():
    coord = _make_coordinator()
    payload = HeatingAssistantCoordinator.build_forecast_payload(
        coord, room_names=["living_room"], plot_forecast_steps=1
    )
    fc = payload["rooms"]["living_room"]["forecast"]
    # now entry + 1 step only
    assert len(fc) == 2
    assert not any(e.get("extended") for e in fc)


def test_extension_failure_falls_back_gracefully():
    coord = _make_coordinator()

    def _boom(*a, **k):
        raise RuntimeError("predict failed")

    coord.model.predict = _boom
    payload = HeatingAssistantCoordinator.build_forecast_payload(
        coord, room_names=["living_room"], plot_forecast_steps=6
    )
    fc = payload["rooms"]["living_room"]["forecast"]
    # Extension simulation failed → only the controller horizon is returned.
    assert len(fc) == 3
    assert not any(e.get("extended") for e in fc)
