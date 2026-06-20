"""Tests for heat-pump power bound fields in build_forecast_payload."""

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from custom_components.heating_assistant.heat_sources import HeatPump


def _make_hp_coord(outdoor_temp: float = -10.0):
    room = SimpleNamespace(
        temperature=20.5, setpoint=21.0, windows=[], comfort_offset=2.0,
    )
    hp = HeatPump(
        "hp1", "living_room", max_power=6000.0,
        cop_rated=3.5, cop_temp_ref=7.0, cooling_cop=2.5, hvac_mode="heat_cool",
    )

    coord = SimpleNamespace(
        predictions=[{"living_room": 20.6}],
        linearised_predictions=[],
        heating_schedule=[{"living_room": 3000.0}],
        solar_forecast=[{"living_room": 50.0}],
        outdoor_forecast=[outdoor_temp],
        price_forecast=[0.10],
        filtered_temperatures={"living_room": 20.63},
        solar_gains={"living_room": 50.0},
        heat_sources=[hp],
        model=SimpleNamespace(
            rooms={"living_room": room},
            room_names=["living_room"],
            predict=lambda *a, **k: [{"living_room": 20.7}],
        ),
        outdoor_temp=outdoor_temp,
        dt=900,
        _control_trajectory=None,
        _sources_by_room={"living_room": [hp]},
        is_room_enabled=lambda r: True,
        now_utc=datetime(2026, 1, 5, 7, 30, tzinfo=timezone.utc),
    )
    coord.sources_for_room = lambda r: [s for s in coord.heat_sources if s.room == r]
    return coord, hp


def test_cold_outdoor_rated_capacity_below_configured_max_power():
    """At cold outdoor temps COP limits delivery below configured max_power."""
    coord, hp = _make_hp_coord(outdoor_temp=-10.0)
    payload = HeatingAssistantCoordinator.build_forecast_payload(
        coord, room_names=["living_room"],
    )
    room = payload["rooms"]["living_room"]
    rated_now = hp.rated_heating_capacity(-10.0)
    assert room["max_power"] == pytest.approx(6000.0, rel=0.01)
    assert room["current_rated_max_power"] == pytest.approx(rated_now, rel=0.01)
    assert room["current_rated_max_power"] < room["max_power"] * 0.75


def test_forecast_heating_capacity_matches_rated_at_each_step():
    """Per-step heating_capacity must follow the outdoor-temperature COP curve."""
    coord, hp = _make_hp_coord(outdoor_temp=-5.0)
    payload = HeatingAssistantCoordinator.build_forecast_payload(
        coord, room_names=["living_room"],
    )
    room = payload["rooms"]["living_room"]
    assert room["current_max_power"] == pytest.approx(
        room["current_rated_max_power"], rel=0.01,
    )
    assert room["forecast"][0]["heating_capacity"] == pytest.approx(
        hp.rated_heating_capacity(-5.0), rel=0.01,
    )
    assert room["forecast"][1]["heating_capacity"] == pytest.approx(
        hp.rated_heating_capacity(-5.0), rel=0.01,
    )
