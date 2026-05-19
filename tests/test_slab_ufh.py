"""
Phase 1 Step 4 (A2 + B1) — tests for the slab node + UFH routing.

A2 adds a third thermal node per room — the **slab** ``T_s`` — coupled
to the air via an internal-film resistance ``R_sa`` and to a built-in
ground-temperature driver via ``R_sg``.  Per-room ``floor_type`` selects
typology defaults from ``const.FLOOR_TYPE_DEFAULTS``:

  * ``none`` — passive slab; very large R_sa / R_sg make the slab state
    effectively decoupled (no contribution to air or wall dynamics).
  * ``slab_on_grade`` — concrete slab in direct contact with ground;
    large C_s, moderate R_sg.
  * ``concrete`` — concrete floor over a conditioned space; large C_s,
    larger R_sg (weaker ground coupling).
  * ``ufh`` — underfloor heating; same physical slab as slab_on_grade,
    but **heat sources route into the slab block** instead of the air
    block (the B1 routing change).

The ground-temperature driver is a built-in sinusoidal annual cycle —
no external data dependency.

These tests pin down:

  * Per-room slab parameters resolved from typology defaults.
  * State vector grew from 3n to 4n (augmented).
  * Slab block is effectively decoupled for ``floor_type = none``.
  * Slab block sees the ground-temperature input ``T_g`` via R_sg.
  * UFH heat input lands on the slab block, not the air block.
  * Characteristic 4–8 h "slab → air" lag for UFH step input.
  * Ground-temperature model: bounded swing, peaks at the configured
    day of year.
"""

from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.const import (
    DEFAULT_GROUND_TEMP_AMPLITUDE,
    DEFAULT_GROUND_TEMP_MEAN,
    DEFAULT_GROUND_TEMP_PEAK_DAY,
    FLOOR_TYPE_CONCRETE,
    FLOOR_TYPE_DEFAULTS,
    FLOOR_TYPE_NONE,
    FLOOR_TYPE_SLAB_ON_GRADE,
    FLOOR_TYPE_UFH,
)
from custom_components.heating_assistant.ground_temp import ground_temperature
from custom_components.heating_assistant.thermal_model import (
    HouseModel,
    Room,
)


# ---------------------------------------------------------------------------
# Ground-temperature model
# ---------------------------------------------------------------------------


def test_ground_temperature_peak_matches_peak_day() -> None:
    """The cosine peaks at ``mean + amplitude`` on the configured
    ``peak_day``."""
    # 2024 is a leap year — day 220 is 7 Aug.
    peak_dt = datetime(2024, 8, 7, 12, 0, tzinfo=timezone.utc)
    T_peak = ground_temperature(peak_dt)
    assert T_peak == pytest.approx(
        DEFAULT_GROUND_TEMP_MEAN + DEFAULT_GROUND_TEMP_AMPLITUDE, abs=0.5,
    )


def test_ground_temperature_trough_six_months_off_peak() -> None:
    """The cosine reaches its minimum half a year away from the peak —
    around day 37 (peak_day − 365/2)."""
    trough_dt = datetime(2024, 2, 6, 12, 0, tzinfo=timezone.utc)  # day 37
    T_trough = ground_temperature(trough_dt)
    assert T_trough == pytest.approx(
        DEFAULT_GROUND_TEMP_MEAN - DEFAULT_GROUND_TEMP_AMPLITUDE, abs=0.5,
    )


def test_ground_temperature_stays_in_band() -> None:
    """Across the entire year the ground temperature stays within
    ``mean ± amplitude``."""
    for day in range(1, 366):
        dt = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc).replace()
        dt = dt.replace(month=1, day=1)
        # Move to the desired day of year.
        from datetime import timedelta
        dt = dt + timedelta(days=day - 1)
        T = ground_temperature(dt)
        assert (
            DEFAULT_GROUND_TEMP_MEAN - DEFAULT_GROUND_TEMP_AMPLITUDE - 1e-6
            <= T
            <= DEFAULT_GROUND_TEMP_MEAN + DEFAULT_GROUND_TEMP_AMPLITUDE + 1e-6
        )


def test_ground_temperature_custom_parameters() -> None:
    """Custom mean / amplitude / peak parameters work as expected."""
    dt = datetime(2024, 7, 15, 0, 0, tzinfo=timezone.utc)  # day ~197
    T = ground_temperature(dt, mean=12.0, amplitude=8.0, peak_day=197)
    assert T == pytest.approx(20.0, abs=0.5)


# ---------------------------------------------------------------------------
# Room data structure
# ---------------------------------------------------------------------------


def test_room_default_floor_type_is_none() -> None:
    """Bare ``Room(...)`` defaults to ``floor_type = none`` — a passive
    slab — for backward compatibility with pre-A2 callers."""
    room = Room(name="a", thermal_mass=5e6, r_external=0.05)
    assert room.floor_type == FLOOR_TYPE_NONE
    assert room.is_ufh is False


def test_room_floor_type_resolves_typology_defaults() -> None:
    """Setting ``floor_type`` without explicit slab numerics picks the
    typology defaults from ``const.FLOOR_TYPE_DEFAULTS``."""
    for ft in (FLOOR_TYPE_NONE, FLOOR_TYPE_SLAB_ON_GRADE, FLOOR_TYPE_CONCRETE, FLOOR_TYPE_UFH):
        room = Room(name="a", thermal_mass=5e6, r_external=0.05, floor_type=ft)
        d = FLOOR_TYPE_DEFAULTS[ft]
        assert room.c_slab_fraction == pytest.approx(d["c_slab_fraction"])
        assert room.r_sa == pytest.approx(d["r_sa"])
        assert room.r_sg == pytest.approx(d["r_sg"])


def test_room_explicit_slab_parameters_override_typology() -> None:
    """Per-field overrides win over typology defaults."""
    room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        floor_type=FLOOR_TYPE_UFH,  # typology says c_slab=0.50
        c_slab_fraction=0.7, r_sa=0.01, r_sg=0.10,
    )
    assert room.c_slab_fraction == pytest.approx(0.7)
    assert room.r_sa == pytest.approx(0.01)
    assert room.r_sg == pytest.approx(0.10)


def test_room_legacy_temperature_seeds_all_three_nodes() -> None:
    """``Room(..., temperature=X)`` initialises air, wall AND slab to X."""
    room = Room(name="a", thermal_mass=5e6, r_external=0.05, temperature=18.5)
    assert room.air_temperature == pytest.approx(18.5)
    assert room.wall_temperature == pytest.approx(18.5)
    assert room.slab_temperature == pytest.approx(18.5)


def test_room_is_ufh_only_for_ufh_floor_type() -> None:
    """The ``is_ufh`` flag controls heat-source routing — it must be
    ``True`` only when floor_type is exactly ``ufh``."""
    assert Room("a", 5e6, 0.05, floor_type=FLOOR_TYPE_UFH).is_ufh is True
    for ft in (FLOOR_TYPE_NONE, FLOOR_TYPE_SLAB_ON_GRADE, FLOOR_TYPE_CONCRETE):
        assert Room("a", 5e6, 0.05, floor_type=ft).is_ufh is False


# ---------------------------------------------------------------------------
# HouseModel matrix structure
# ---------------------------------------------------------------------------


def test_state_dimension_is_3n() -> None:
    """Phase 1 A2: physical state grew from 2n to 3n (air + wall + slab)."""
    rooms = [
        Room("a", 5e6, 0.05),
        Room("b", 3e6, 0.08, floor_type=FLOOR_TYPE_UFH),
    ]
    model = HouseModel(rooms)
    assert model._C.shape == (6,)
    assert model._A.shape == (6, 6)
    assert model._B_ext.shape == (6,)
    assert model._B_ground.shape == (6,)


def test_b_ground_only_on_slab_block() -> None:
    """``B_ground`` carries support only on the slab block of the state."""
    rooms = [Room("a", 5e6, 0.05, floor_type=FLOOR_TYPE_SLAB_ON_GRADE)]
    model = HouseModel(rooms)
    n = 1
    # Air and wall blocks: zero.
    assert model._B_ground[0] == 0.0
    assert model._B_ground[n + 0] == 0.0
    # Slab block: 1/R_sg.
    assert model._B_ground[2 * n + 0] > 0.0
    assert model._B_ground[2 * n + 0] == pytest.approx(1.0 / model._r_sg[0])


def test_slab_block_couples_to_air_via_r_sa() -> None:
    """The slab–air coupling appears at A[2n+i, i] = +1/R_sa_i and
    contributes ``-1/R_sa_i`` to A[2n+i, 2n+i] (the slab diagonal)."""
    rooms = [Room("a", 5e6, 0.05, floor_type=FLOOR_TYPE_SLAB_ON_GRADE)]
    model = HouseModel(rooms)
    n = 1
    g_sa = 1.0 / model._r_sa[0]
    g_sg = 1.0 / model._r_sg[0]
    # Slab → air coupling.
    assert model._A[2 * n + 0, 0] == pytest.approx(g_sa)
    # Air → slab coupling (symmetric).
    assert model._A[0, 2 * n + 0] == pytest.approx(g_sa)
    # Slab diagonal: -1/R_sa - 1/R_sg.
    assert model._A[2 * n + 0, 2 * n + 0] == pytest.approx(-(g_sa + g_sg))


def test_none_floor_type_decouples_slab_block() -> None:
    """``floor_type = none`` uses huge R_sa / R_sg defaults so the slab
    is effectively decoupled — the matrix entries that couple slab to
    air / ground are essentially zero."""
    model = HouseModel([Room("a", 5e6, 0.05, floor_type=FLOOR_TYPE_NONE)])
    n = 1
    # 1 / 1e6 = 1e-6 conductance — many orders of magnitude smaller than
    # typical R_aw or R_we conductances (~10–100 W/K).
    assert abs(model._A[2 * n + 0, 0]) < 1e-3
    assert abs(model._A[0, 2 * n + 0]) < 1e-3
    assert abs(model._B_ground[2 * n + 0]) < 1e-3


def test_inter_room_couplings_still_only_on_wall_block() -> None:
    """Locked design call (roadmap §17.2 A1) — inter-room couplings
    route through the *wall* block, not the slab.  Each slab sits on
    its own ground patch."""
    rooms = [
        Room("a", 5e6, 0.05, floor_type=FLOOR_TYPE_SLAB_ON_GRADE,
             connections=[]),
        Room("b", 5e6, 0.05, floor_type=FLOOR_TYPE_SLAB_ON_GRADE),
    ]
    from custom_components.heating_assistant.thermal_model import RoomConnection
    rooms[0].connections = [RoomConnection("b", 0.2)]
    rooms[1].connections = [RoomConnection("a", 0.2)]
    model = HouseModel(rooms)
    n = 2
    # Wall–wall block carries the inter-room conductance.
    assert model._A[n + 0, n + 1] > 0.0
    assert model._A[n + 1, n + 0] > 0.0
    # Slab–slab block has no inter-room coupling.
    assert model._A[2 * n + 0, 2 * n + 1] == 0.0
    assert model._A[2 * n + 1, 2 * n + 0] == 0.0


# ---------------------------------------------------------------------------
# UFH heat routing (Phase 1 B1)
# ---------------------------------------------------------------------------


def test_air_heating_warms_air_first() -> None:
    """In a non-UFH room, heat input arrives at the air node first.
    Over a single short step the air temperature rises noticeably while
    the slab temperature is essentially unchanged."""
    room = Room(
        "a", thermal_mass=5e6, r_external=0.05,
        floor_type=FLOOR_TYPE_NONE,
        air_temperature=20.0, wall_temperature=20.0, slab_temperature=20.0,
    )
    model = HouseModel([room])
    model.step(
        dt=300.0, heat_inputs={"a": 2000.0},
        outdoor_temp=10.0, solar_gains={},
    )
    air_after = model.temperatures["a"]
    slab_after = model.slab_temperatures["a"]
    # Air should be warmer than where it started; slab effectively
    # unchanged (decoupled in floor_type=none).
    assert air_after > 20.0
    assert abs(slab_after - 20.0) < 0.01


def test_ufh_heating_warms_slab_first() -> None:
    """In a UFH room, heat input arrives at the slab node, not the air.
    Over a single short step the slab temperature rises noticeably while
    the air temperature has barely moved yet — that's the characteristic
    slab→air lag."""
    room = Room(
        "a", thermal_mass=5e6, r_external=0.05,
        floor_type=FLOOR_TYPE_UFH,
        air_temperature=20.0, wall_temperature=20.0, slab_temperature=20.0,
    )
    model = HouseModel([room])
    model.step(
        dt=300.0,  # 5-minute step
        heat_inputs={"a": 2000.0},
        outdoor_temp=10.0, solar_gains={},
    )
    air_after = model.temperatures["a"]
    slab_after = model.slab_temperatures["a"]
    # Slab should be substantially warmer; air should have moved much
    # less in the first 5 minutes (the slab heats the air via R_sa
    # which is a small but finite resistance).
    assert slab_after > 20.0
    # The slab-rise should exceed the air-rise by a comfortable margin.
    assert (slab_after - 20.0) > 2.0 * max(air_after - 20.0, 1e-9)


def test_ufh_step_response_air_lags_slab() -> None:
    """Over a longer step (an hour), the air response to UFH heating
    has a clear lag relative to the slab — but the air is on its way
    up.  Confirms the characteristic slab→air heat-transfer pattern
    rather than the all-or-nothing 1R1C behaviour."""
    room = Room(
        "a", thermal_mass=5e6, r_external=0.05,
        floor_type=FLOOR_TYPE_UFH,
        air_temperature=20.0, wall_temperature=20.0, slab_temperature=20.0,
    )
    model = HouseModel([room])
    # Run six 10-minute steps with constant UFH heating.
    for _ in range(6):
        model.step(
            dt=600.0, heat_inputs={"a": 3000.0},
            outdoor_temp=10.0, solar_gains={},
        )
    air = model.temperatures["a"]
    slab = model.slab_temperatures["a"]
    # Both should have risen; slab should be warmer than air.
    assert air > 20.0
    assert slab > air


# ---------------------------------------------------------------------------
# Ground-temperature plumbing
# ---------------------------------------------------------------------------


def test_set_ground_temp_drives_slab_dynamics() -> None:
    """Changing the ground temperature mid-life changes the slab's
    steady-state target."""
    room = Room(
        "a", thermal_mass=5e6, r_external=0.05,
        floor_type=FLOOR_TYPE_SLAB_ON_GRADE,
        air_temperature=20.0, wall_temperature=20.0, slab_temperature=20.0,
    )
    model = HouseModel([room])

    # Cold ground for a long settling period (many hours).
    model.set_ground_temp(0.0)
    for _ in range(48):  # 48 × 15 min = 12 h
        model.step(
            dt=900.0, heat_inputs={}, outdoor_temp=10.0, solar_gains={},
        )
    slab_cold = model.slab_temperatures["a"]

    # Reset to a fresh model and run with warm ground.
    room2 = Room(
        "a", thermal_mass=5e6, r_external=0.05,
        floor_type=FLOOR_TYPE_SLAB_ON_GRADE,
        air_temperature=20.0, wall_temperature=20.0, slab_temperature=20.0,
    )
    model2 = HouseModel([room2])
    model2.set_ground_temp(15.0)
    for _ in range(48):
        model2.step(
            dt=900.0, heat_inputs={}, outdoor_temp=10.0, solar_gains={},
        )
    slab_warm = model2.slab_temperatures["a"]

    # Warm-ground slab must end up warmer than cold-ground slab.
    assert slab_warm > slab_cold + 0.5


# ---------------------------------------------------------------------------
# Controller plumbing
# ---------------------------------------------------------------------------


def test_house_thermal_sde_state_dimension_with_slab() -> None:
    """``HouseThermalSDE.nx`` is now 4n augmented (3n + offset)."""
    from custom_components.heating_assistant.controller import HouseThermalSDE
    from custom_components.heating_assistant.heat_sources import ElectricHeater

    room = Room("a", 5e6, 0.05, floor_type=FLOOR_TYPE_SLAB_ON_GRADE,
                temperature=20.0)
    model = HouseModel([room])
    src = ElectricHeater("h", "a", max_power=1000.0)
    sde = HouseThermalSDE(model, [src], dt=900.0)
    # 1 room × 4 (air + wall + slab + offset).
    assert sde.nx == 4
    # ``nx`` un-augmented would be 3 (air + wall + slab).
    sde_unaug = HouseThermalSDE(model, [src], dt=900.0, augment_offsets=False)
    assert sde_unaug.nx == 3


def test_controller_ufh_routing() -> None:
    """When a room's ``is_ufh`` is True, the SDE's drift function ``f``
    routes the heat-source contribution to the slab block, not the air."""
    from custom_components.heating_assistant.controller import HouseThermalSDE
    from custom_components.heating_assistant.heat_sources import ElectricHeater

    room = Room(
        "a", thermal_mass=5e6, r_external=0.05,
        floor_type=FLOOR_TYPE_UFH,
        temperature=20.0,
    )
    model = HouseModel([room])
    src = ElectricHeater("h", "a", max_power=5000.0)
    sde = HouseThermalSDE(model, [src], dt=900.0, augment_offsets=False)
    n = 1
    # State layout (un-augmented): [T_a (1), T_w (1), T_s (1)].
    x = np.array([20.0, 20.0, 20.0])
    u = np.array([1.0])  # full heating
    d = sde.disturbance_vector(10.0, {})
    p = np.array([])
    f = sde.f(x, u, d, p, 0.0)
    # Air block (index 0) should NOT receive direct heating — it's UFH.
    # The air drift comes only from the air↔wall and air↔slab coupling
    # terms; the heater term lands on the slab block (index 2*n + 0 = 2).
    assert f[2 * n + 0] > 0.0, f"Slab drift should be positive under UFH heating, got {f}"
    # The slab-block drift should dominate the air-block drift initially
    # (air only sees coupling heat, slab sees direct heat).
    assert f[2 * n + 0] > 2.0 * f[0], (
        f"Slab drift {f[2*n+0]:.3e} should dominate air drift {f[0]:.3e} "
        f"under UFH routing"
    )
