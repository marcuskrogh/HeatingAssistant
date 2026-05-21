"""
1R1C thermal model — basic behavioural tests.

These tests verify the single-temperature-node (1R1C) model behaviour:

  * State dimension is n (one float per room).
  * Heat input raises the single temperature.
  * Outdoor loss cools the room.
  * Inter-room connections route through the single temperature.
  * Steady-state: T_inf = T_out + Q * R_ext.
  * ``predict()`` saves and restores state.
  * Single eigenvalue (stable, negative).
  * Backward compat ``Room(temperature=X)`` still works.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.thermal_model import (
    HouseModel,
    Room,
    RoomConnection,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_single_room(
    *,
    thermal_mass: float = 5_000_000.0,
    r_external: float = 0.05,
    temperature: float = 20.0,
    infiltration_fraction: float = 0.0,
) -> HouseModel:
    """Build a single-room HouseModel with 1R1C parameters."""
    room = Room(
        name="a",
        thermal_mass=thermal_mass,
        r_external=r_external,
        temperature=temperature,
        infiltration_fraction=infiltration_fraction,
    )
    return HouseModel([room])


# ---------------------------------------------------------------------------
# Dataclass / construction
# ---------------------------------------------------------------------------


def test_room_legacy_temperature_kwarg() -> None:
    """``Room(..., temperature=X)`` is the primary cold-start API.
    The ``.temperature`` attribute should equal X."""
    room = Room(name="a", thermal_mass=5e6, r_external=0.05, temperature=18.5)
    assert room.temperature == pytest.approx(18.5)


def test_room_temperature_attribute_is_plain_float() -> None:
    """``room.temperature`` is a plain float (not a property backed by
    air_temperature in 1R1C)."""
    room = Room(name="a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    room.temperature = 21.0
    assert room.temperature == pytest.approx(21.0)


# ---------------------------------------------------------------------------
# Matrix structure
# ---------------------------------------------------------------------------


def test_house_model_state_dimension_is_n() -> None:
    """1R1C: state vector has length n (one per room)."""
    rooms = [
        Room(name="a", thermal_mass=5e6, r_external=0.05),
        Room(name="b", thermal_mass=3e6, r_external=0.08),
    ]
    model = HouseModel(rooms)
    assert model._C.shape == (2,)
    assert model._A.shape == (2, 2)
    assert model._B_ext.shape == (2,)


def test_eigenvalue_is_stable_and_negative() -> None:
    """The 1R1C system has a single eigenvalue that is stable (negative).
    For a single room F = -1/(C * R_ext)."""
    model = _make_single_room(thermal_mass=5e6, r_external=0.05)
    inv_C = 1.0 / model._C
    F = model._A * inv_C[:, np.newaxis]
    eigs = np.linalg.eigvals(F).real
    # Only one eigenvalue for a single room.
    assert len(eigs) == 1
    assert eigs[0] < 0.0


def test_heat_input_raises_temperature() -> None:
    """Applying heater power raises the single temperature node."""
    model = _make_single_room(temperature=20.0)
    temps = model.step(
        dt=60.0,
        heat_inputs={"a": 2000.0},
        outdoor_temp=20.0,   # no outdoor loss
        solar_gains={},
    )
    assert temps["a"] > 20.01


def test_outdoor_loss_cools_room() -> None:
    """Without any heat input, a warm room cools toward outdoor."""
    model = _make_single_room(temperature=20.0)
    model.step(
        dt=900.0,
        heat_inputs={},
        outdoor_temp=0.0,
        solar_gains={},
    )
    assert model.temperatures["a"] < 20.0


# ---------------------------------------------------------------------------
# Inter-room connections
# ---------------------------------------------------------------------------


def test_inter_room_connection_on_single_temperature() -> None:
    """In 1R1C inter-room couplings appear on the temperature diagonal.
    The A matrix should have non-zero off-diagonal entries for connected rooms."""
    rooms = [
        Room(
            name="a", thermal_mass=5e6, r_external=0.05,
            connections=[RoomConnection(connected_room="b", r_value=0.2)],
            temperature=20.0,
        ),
        Room(
            name="b", thermal_mass=3e6, r_external=0.08,
            connections=[RoomConnection(connected_room="a", r_value=0.2)],
            temperature=20.0,
        ),
    ]
    model = HouseModel(rooms)
    A = model._A
    # Off-diagonal entries carry the inter-room conductance.
    assert A[0, 1] > 0.0
    assert A[1, 0] > 0.0


def test_heat_flows_through_inter_room_connection() -> None:
    """A hot room transfers heat to a cold connected room."""
    rooms = [
        Room(
            name="hot", thermal_mass=5e6, r_external=10.0,  # large R to minimize outdoor loss
            connections=[RoomConnection(connected_room="cold", r_value=0.2)],
            temperature=25.0,
        ),
        Room(
            name="cold", thermal_mass=5e6, r_external=10.0,
            connections=[RoomConnection(connected_room="hot", r_value=0.2)],
            temperature=15.0,
        ),
    ]
    model = HouseModel(rooms)
    model.step(dt=900.0, heat_inputs={}, outdoor_temp=20.0, solar_gains={})
    # Hot should cool; cold should warm (toward each other).
    assert model.temperatures["hot"] < 25.0
    assert model.temperatures["cold"] > 15.0


# ---------------------------------------------------------------------------
# Steady-state calibration
# ---------------------------------------------------------------------------


def test_steady_state_T_inf_equals_T_out_plus_Q_times_R() -> None:
    """At steady state with constant heating power Q and outdoor temperature
    T_out:  T_inf = T_out + Q * R_ext."""
    thermal_mass = 5e6
    r_external = 0.05
    Q = 1000.0   # W
    T_out = 0.0  # °C
    expected_T_inf = T_out + Q * r_external  # = 50 °C

    model = _make_single_room(thermal_mass=thermal_mass, r_external=r_external)
    # Run many steps until settled (time constant = C * R = 5e6 * 0.05 = 250000s;
    # 5000 steps × 900s = 4500000s ≈ 18 time constants → well settled).
    for _ in range(5000):
        model.step(dt=900.0, heat_inputs={"a": Q}, outdoor_temp=T_out, solar_gains={})
    assert model.temperatures["a"] == pytest.approx(expected_T_inf, rel=0.01)


# ---------------------------------------------------------------------------
# Predict / state save-restore
# ---------------------------------------------------------------------------


def test_predict_saves_and_restores_state() -> None:
    """``HouseModel.predict`` runs the model forward without mutating the
    cached state — the temperature before and after should be equal."""
    model = _make_single_room(temperature=20.0)
    before = model.temperatures["a"]

    _ = model.predict(
        horizon=10, dt=900.0,
        heat_schedule=[{"a": 2000.0}] * 10,
        outdoor_temps=[0.0] * 10,
        solar_gain_schedule=[{}] * 10,
    )

    assert model.temperatures["a"] == pytest.approx(before)
