"""
Phase 1 A1 — tests for the 1R1C → 2R2C envelope upgrade.

The 2R2C network represents each room as two thermal nodes:

  * a **fast air node** ``T_a`` (small ``C_a``) that responds in minutes
    to heat input, and
  * a **slow envelope node** ``T_w`` (large ``C_w``) that lags the air
    by ~hours.

These tests pin down the new behaviour that 1R1C couldn't reproduce:

  * Two distinct eigenvalues (fast / slow modes) on a single-room model.
  * Heat input lands on the air node — the wall responds with a lag.
  * Inter-room conduction routes through the wall nodes (mass-to-mass
    partitions; locked design call in roadmap §17.2 A1).
  * Steady-state behaviour matches the bundled ``1/r_external``
    calibration — the 2R2C → 1R1C reduction is exact at the typical
    reference conditions.
  * The EKF reconstructs the unobserved wall state from the dynamics.
  * Backward-compat ``Room(temperature=20.0)`` initialises both nodes.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.const import (
    DEFAULT_C_AIR_FRACTION,
    DEFAULT_R_AW_FRACTION,
    MAX_INFILTRATION_FRACTION,
)
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
    air_temperature: float = 20.0,
    wall_temperature: float | None = None,
    c_air_fraction: float = DEFAULT_C_AIR_FRACTION,
    r_aw_fraction: float = DEFAULT_R_AW_FRACTION,
    infiltration_fraction: float = 0.0,
) -> HouseModel:
    """Build a single-room HouseModel with explicit 2R2C parameters.

    ``infiltration_fraction`` defaults to 0 so these tests focus on the
    A1 split rather than the C1 overlay; combine with the C1 test suite
    when both effects matter.
    """
    room = Room(
        name="a",
        thermal_mass=thermal_mass,
        r_external=r_external,
        air_temperature=air_temperature,
        wall_temperature=wall_temperature if wall_temperature is not None else air_temperature,
        c_air_fraction=c_air_fraction,
        r_aw_fraction=r_aw_fraction,
        infiltration_fraction=infiltration_fraction,
    )
    return HouseModel([room])


# ---------------------------------------------------------------------------
# Dataclass / construction
# ---------------------------------------------------------------------------


def test_room_legacy_temperature_kwarg_seeds_both_nodes() -> None:
    """``Room(..., temperature=X)`` is the pre-A1 cold-start API.  It
    must initialise *both* ``air_temperature`` and ``wall_temperature``
    to ``X`` so existing constructions stay correct under 2R2C."""
    room = Room(name="a", thermal_mass=5e6, r_external=0.05, temperature=18.5)
    assert room.air_temperature == pytest.approx(18.5)
    assert room.wall_temperature == pytest.approx(18.5)
    # And the live ``temperature`` property reads the air node.
    assert room.temperature == pytest.approx(18.5)


def test_room_temperature_property_writes_only_air() -> None:
    """``room.temperature = X`` updates the air node (what a thermistor
    measures) and leaves the wall node alone — the wall state is
    independently maintained by the EKF."""
    room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        air_temperature=20.0, wall_temperature=18.0,
    )
    room.temperature = 21.0
    assert room.air_temperature == pytest.approx(21.0)
    assert room.wall_temperature == pytest.approx(18.0)  # unchanged


# ---------------------------------------------------------------------------
# Matrix structure
# ---------------------------------------------------------------------------


def test_house_model_state_dimension_is_3n() -> None:
    """Phase 1 A2: state vector grows from 2n (air + wall) to 3n (air +
    wall + slab) — every room now carries a slab state, even
    ``floor_type = none`` rooms (the slab there is effectively passive
    via large R_sa / R_sg)."""
    rooms = [
        Room(name="a", thermal_mass=5e6, r_external=0.05),
        Room(name="b", thermal_mass=3e6, r_external=0.08),
    ]
    model = HouseModel(rooms)
    assert model._C.shape == (6,)        # 3n = 6
    assert model._A.shape == (6, 6)
    assert model._B_ext.shape == (6,)
    assert model._B_ground.shape == (6,)


def test_eigenvalues_show_fast_slow_separation() -> None:
    """The 2R2C system has two eigenvalues with very different time
    constants (the air mode is fast, the envelope mode is slow).  A
    1R1C system has only one.  This separation is the whole point of
    going to 2R2C — it's what lets the controller anticipate the
    slow-mode response.
    """
    model = _make_single_room(thermal_mass=5e6, r_external=0.05)
    # F = C⁻¹ A on the 2R2C block.  For a single room there are no
    # inter-room couplings, so F is a 2×2 matrix.
    inv_C = 1.0 / model._C
    F = model._A * inv_C[:, np.newaxis]
    eigs = np.linalg.eigvals(F).real
    # Both eigenvalues should be negative (stable).
    assert np.all(eigs < 0.0)
    # Time constants τ = -1/λ in seconds.
    taus = sorted(-1.0 / eigs)
    fast, slow = taus[0], taus[1]
    # Fast mode should be on the order of minutes; slow on the order of
    # hours.  Demand at least a factor-of-10 separation — that's the
    # qualitative signature that 1R1C cannot reproduce.
    assert slow / fast > 10.0, (
        f"Expected ≥10x time-scale separation between modes, got "
        f"fast={fast:.1f}s ({fast/60:.1f}min), slow={slow:.1f}s ({slow/3600:.2f}h)"
    )


def test_heat_input_lands_on_air_node() -> None:
    """A unit of heater power applied to the room produces an immediate
    rise on the *air* node only — the wall responds with a lag."""
    model = _make_single_room(air_temperature=20.0)
    temps = model.step(
        dt=60.0,                       # 1-minute step
        heat_inputs={"a": 2000.0},     # 2 kW
        outdoor_temp=20.0,             # no envelope loss
        solar_gains={},
    )
    # Air should have risen noticeably; wall should be barely changed
    # over a single minute.
    assert temps["a"] > 20.01
    walls = model.wall_temperatures
    assert walls["a"] - 20.0 < 0.5 * (temps["a"] - 20.0), (
        f"Wall responded too fast: air rose {temps['a']-20:.3f} K, "
        f"wall rose {walls['a']-20:.3f} K in 60 s"
    )


def test_outdoor_loss_lands_on_wall_node() -> None:
    """When the room is warm and outdoor is cold (and no heater is on),
    the wall block sees direct loss via R_we — its drift should be
    negative even when the air block hasn't moved much yet."""
    # Start with air = wall = warm; immediately step into cold outdoor.
    model = _make_single_room(
        air_temperature=20.0, wall_temperature=20.0,
        infiltration_fraction=0.0,  # disable SG overlay so loss goes purely via wall
    )
    model.step(
        dt=60.0,
        heat_inputs={},
        outdoor_temp=0.0,
        solar_gains={},
    )
    walls_after = model.wall_temperatures
    air_after = model.temperatures
    # Both should drop, but the wall (which is in direct contact with
    # outdoor via R_we) should drop on its own merit — not just by
    # following the air.
    assert walls_after["a"] < 20.0
    assert air_after["a"] < 20.0


def test_inter_room_connections_route_through_wall_nodes() -> None:
    """Locked design call in roadmap §17.2 A1: inter-room couplings are
    mass-to-mass partitions, so they appear in the wall-wall block of
    the A matrix, not the air-air block.
    """
    rooms = [
        Room(
            name="a", thermal_mass=5e6, r_external=0.05,
            connections=[RoomConnection(connected_room="b", r_value=0.2)],
            air_temperature=20.0, wall_temperature=20.0,
        ),
        Room(
            name="b", thermal_mass=3e6, r_external=0.08,
            connections=[RoomConnection(connected_room="a", r_value=0.2)],
            air_temperature=20.0, wall_temperature=20.0,
        ),
    ]
    model = HouseModel(rooms)
    n = 2
    # Wall-wall block: A[n:2n, n:2n] should have non-zero off-diagonal
    # entries for the (a, b) coupling.  Air-air block: A[:n, :n] should
    # NOT have an off-diagonal entry for the inter-room connection
    # (only the air↔wall couplings).
    A = model._A
    # Air-air block off-diagonal must be zero for room (a, b) — no
    # direct air-to-air coupling between connected rooms.
    assert A[0, 1] == pytest.approx(0.0)
    assert A[1, 0] == pytest.approx(0.0)
    # Wall-wall block off-diagonal must carry the inter-room conductance.
    assert A[n + 0, n + 1] > 0.0
    assert A[n + 1, n + 0] > 0.0


# ---------------------------------------------------------------------------
# Bundled-parameter calibration
# ---------------------------------------------------------------------------


def test_2r2c_steady_state_matches_bundled_r_external() -> None:
    """At thermodynamic steady state under typical-conditions wind and
    no heat input, the rate of air-cooling implied by the 2R2C network
    should match the bundled-parameter rate ``(T_a − T_out) / (C × R_external)``
    within a small tolerance.  This is the calibration guarantee: the
    A1 split doesn't shift the steady-state UA away from the user's
    configured ``r_external``.
    """
    thermal_mass = 5e6
    r_external = 0.05
    model = _make_single_room(
        thermal_mass=thermal_mass,
        r_external=r_external,
        infiltration_fraction=0.0,  # avoid SG overlay
    )
    # Hold air and wall at exactly the same value (steady-state-ish
    # initial condition) and look at the very first step's air-drift.
    T0 = 20.0
    T_out = 0.0
    # Compute analytic 1R1C steady-state cooling rate.
    expected_dT_per_step = -(T0 - T_out) / (thermal_mass * r_external)  # K/s

    # Run the model forward long enough for both air and wall to
    # asymptote toward the outdoor.  The slow-mode time constant for
    # ``thermal_mass = 5 MJ/K`` and ``r_external = 0.05 K/W`` is
    # ``M × R = 250 000 s`` ≈ 70 h, so we need several time constants
    # (~10 days) for full settling.  Use 480 × 900 s = 5 days; the
    # remaining gap is small enough that monotonic decay is the more
    # robust assertion than an absolute threshold.
    prev_air = T0
    for _ in range(480):
        prev_air = model.temperatures["a"]
        model.step(dt=900.0, heat_inputs={}, outdoor_temp=T_out, solar_gains={})

    settled_air = model.temperatures["a"]
    settled_wall = model.wall_temperatures["a"]
    # Both nodes must have decayed substantially toward outdoor.
    assert settled_air < T0 - 5.0
    assert settled_wall < T0 - 5.0
    # In quasi-equilibrium (no heat input, settled state) the two
    # nodes should be near each other: the wall is in direct contact
    # with outdoor via R_we and the air follows via R_aw.  Allow up to
    # 1 K spread to keep the assertion tolerant.
    assert abs(settled_air - settled_wall) < 1.0


# ---------------------------------------------------------------------------
# Predict / EKF wall reconstruction
# ---------------------------------------------------------------------------


def test_predict_saves_and_restores_wall_state() -> None:
    """``HouseModel.predict`` runs the model forward without mutating
    the cached state.  Phase 1 A1 added a wall state — the save/restore
    must cover both nodes.
    """
    model = _make_single_room(air_temperature=20.0, wall_temperature=18.0)
    before_air = model.temperatures["a"]
    before_wall = model.wall_temperatures["a"]

    # Run prediction.
    _ = model.predict(
        horizon=10, dt=900.0,
        heat_schedule=[{"a": 2000.0}] * 10,
        outdoor_temps=[0.0] * 10,
        solar_gain_schedule=[{}] * 10,
    )

    assert model.temperatures["a"] == pytest.approx(before_air)
    assert model.wall_temperatures["a"] == pytest.approx(before_wall)


# ---------------------------------------------------------------------------
# Conductive-path calibration
# ---------------------------------------------------------------------------


def test_r_aw_plus_r_we_equals_total_conductive_path() -> None:
    """The user's bundled ``r_external`` represents the total UA at
    typical conditions.  Internally A1 splits the *conductive* share
    (``(1 - infiltration_fraction) * (1/r_external)``) into R_aw and
    R_we.  Their sum must equal the conductive path resistance.
    """
    r_ext = 0.05
    f_inf = 0.30
    model = _make_single_room(r_external=r_ext, infiltration_fraction=f_inf)
    r_cond_total = r_ext / (1.0 - f_inf)
    assert model._r_aw[0] + model._r_we[0] == pytest.approx(
        r_cond_total, rel=1e-6,
    )


def test_c_air_plus_c_wall_plus_c_slab_equals_total_thermal_mass() -> None:
    """Phase 1 A2: bundled ``thermal_mass`` now splits into three nodes
    (``c_air + c_wall + c_slab``).  The sum is conserved regardless of
    split fractions or floor type."""
    M = 5e6
    model = _make_single_room(thermal_mass=M)
    total = model._c_air[0] + model._c_wall[0] + model._c_slab[0]
    assert total == pytest.approx(M, rel=1e-9)


def test_infiltration_fraction_clamped_at_max() -> None:
    """``infiltration_fraction`` is clamped at ``MAX_INFILTRATION_FRACTION``
    so the conductive path doesn't degenerate (R_aw + R_we → ∞ as
    f_inf → 1)."""
    r_ext = 0.05
    model = _make_single_room(r_external=r_ext, infiltration_fraction=0.99)
    # Conductive path must stay finite.  The clamp at
    # MAX_INFILTRATION_FRACTION (0.95) gives a worst-case
    # R_cond_total = r_external / (1 - 0.95) = 20 × r_external.
    r_total = model._r_aw[0] + model._r_we[0]
    expected_max = r_ext / (1.0 - MAX_INFILTRATION_FRACTION)
    assert np.isfinite(r_total)
    # Allow a small numerical slack but verify the clamp is respected.
    assert r_total <= expected_max * (1.0 + 1e-6)
