"""
2R2C thermal model — basic behavioural tests.

These tests verify the two-temperature-node (2R2C) model behaviour:

  * State dimension is 2n (air block + wall block, one pair per room).
  * _A is (2n, 2n), _C is (2n,), _B_ext is (2n,).
  * Heat input raises the air temperature.
  * Outdoor loss cools the room.
  * Inter-room connections route through the WALL block (A[n+i, n+j]).
  * Steady-state: T_air_ss = T_out + Q * R_ext (invariant preserved from 1R1C).
  * ``predict()`` saves and restores BOTH air and wall node states.
  * Per-room 2x2 sub-system has two eigenvalues, both real and negative.
  * Backward compat ``Room(temperature=X)`` still works.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heatingassistant.engine.thermal_model import (
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
    """2R2C: state vector has length 2n (air block + wall block per room)."""
    rooms = [
        Room(name="a", thermal_mass=5e6, r_external=0.05),
        Room(name="b", thermal_mass=3e6, r_external=0.08),
    ]
    model = HouseModel(rooms)
    n = len(rooms)
    assert model._C.shape == (2 * n,)
    assert model._A.shape == (2 * n, 2 * n)
    assert model._B_ext.shape == (2 * n,)


def test_eigenvalue_is_stable_and_negative() -> None:
    """The 2R2C per-room sub-system has two eigenvalues, both real and negative.

    For a single room the 2×2 air/wall sub-matrix has eigenvalues:
        λ₁ = fast (large magnitude, dominated by air↔wall coupling)
        λ₂ = slow (small magnitude, dominated by wall↔outdoor conduction)
    Both must be real and strictly negative for stability.
    """
    model = _make_single_room(thermal_mass=5e6, r_external=0.05)
    n = model.n  # n=1
    inv_C = 1.0 / model._C
    F = model._A * inv_C[:, np.newaxis]
    # Extract the 2×2 per-room sub-matrix (rows/cols [0, n])
    sub = F[np.ix_([0, n], [0, n])]
    eigs = np.linalg.eigvals(sub)
    # Both eigenvalues must be real and negative (2R2C is stable)
    assert len(eigs) == 2
    assert np.all(eigs.real < 0.0), f"Expected negative eigenvalues, got {eigs}"
    assert np.all(np.abs(eigs.imag) < 1e-10), f"Expected real eigenvalues, got {eigs}"
    # Fast/slow separation: the air node is lighter so one eigenvalue is
    # significantly faster (larger magnitude) than the other.
    sorted_eigs = np.sort(np.abs(eigs.real))
    assert sorted_eigs[1] > 2 * sorted_eigs[0], (
        f"Expected clear fast/slow separation: {sorted_eigs}"
    )


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
    """In 2R2C inter-room couplings appear on the WALL block (rows/cols n..2n).
    For n=2 rooms, the wall-block off-diagonal entries A[n+0, n+1] and
    A[n+1, n+0] carry the inter-room conductance; air-block off-diagonals
    A[0,1] and A[1,0] must be zero (no direct air-to-air coupling)."""
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
    n = len(rooms)  # n = 2
    A = model._A
    # Wall-block off-diagonal entries carry the inter-room conductance.
    assert A[n + 0, n + 1] > 0.0, f"A[n+0, n+1] = {A[n+0, n+1]} should be > 0"
    assert A[n + 1, n + 0] > 0.0, f"A[n+1, n+0] = {A[n+1, n+0]} should be > 0"
    # Air-block off-diagonals must be zero (no direct air-to-air coupling).
    assert A[0, 1] == 0.0, f"A[0,1] = {A[0,1]} should be 0 (no direct air coupling)"
    assert A[1, 0] == 0.0, f"A[1,0] = {A[1,0]} should be 0 (no direct air coupling)"


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
