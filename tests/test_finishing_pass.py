"""
Phase 1 Step 6 (C3 + C4 + C5) — tests for the finishing-pass envelope
correction terms in the 1R1C model.

All three corrections default to **off** (zero) per room, so existing
installs see no behaviour change.  Each is opt-in via per-room YAML
fields:

  * **C3 long-wave to sky** — ``sky_radiative_ua`` (W/K).  Adds a
    radiative conductance in parallel with the single-node→outdoor
    conductance, plus a constant cooling drift
    ``−sky_radiative_ua · ΔT_sky`` on the single temperature node
    (the clear-night cooling effect).  ΔT_sky is a model constant
    (default 6 K).

  * **C4 sol-air on opaque** — ``facade_colour`` / ``facade_absorptance``
    + ``facade_solar_share``.  A fraction
    ``facade_absorptance × facade_solar_share`` of the room's
    window-derived solar gain is added to the single temperature node's
    solar channel.

  * **C5 thermal-bridge correction** — ``thermal_bridge_psi_l``
    (W/K).  Adds to the single-node→outdoor conductance.

These tests pin down the matrix-build effects, the runtime drift
contributions, and the no-regression invariant when all three knobs
are zero.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heatingassistant.engine.const import (
    DEFAULT_DELTA_T_SKY,
    FACADE_COLOUR_DARK,
    FACADE_COLOUR_LIGHT,
    FACADE_COLOUR_MEDIUM,
    FACADE_COLOUR_TO_ABSORPTANCE,
)
from heatingassistant.engine.controller import HouseThermalSDE
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.thermal_model import HouseModel, Room


# ---------------------------------------------------------------------------
# C3 — long-wave to sky
# ---------------------------------------------------------------------------


def test_default_sky_radiative_ua_is_zero() -> None:
    """A bare ``Room(...)`` has ``sky_radiative_ua = 0`` so there is no
    long-wave-to-sky contribution.  Preserves pre-C3 behaviour."""
    room = Room(name="a", thermal_mass=5e6, r_external=0.05)
    assert room.sky_radiative_ua == pytest.approx(0.0)


def test_sky_ua_adds_to_wall_node_conductance() -> None:
    """When ``sky_radiative_ua > 0`` the WALL node's B_ext entry grows
    by that amount, and the wall A diagonal becomes more negative.
    (Long-wave radiation leaves through the envelope surfaces, so the
    sky conductance attaches to the wall node in the 2R2C model.)"""
    sky_ua = 2.0
    room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        sky_radiative_ua=sky_ua,
    )
    model = HouseModel([room])
    _g_inf, g_aw, g_we = room.conductances()
    # Wall row (index n + 0 = 1): g_we + sky_ua + thermal_bridge.
    expected_b = g_we + sky_ua + 0.0
    assert model._B_ext[1] == pytest.approx(expected_b)
    # Wall A diagonal: -(g_aw + g_we + sky_ua), no inter-room.
    expected_diag = -(g_aw + g_we + sky_ua)
    assert model._A[1, 1] == pytest.approx(expected_diag, rel=1e-9)


def test_sky_offset_pulls_wall_node_down_on_clear_night() -> None:
    """The constant sky cooling-drift bias ``−sky_ua · ΔT_sky / C_w``
    appears on the WALL node of ``_B_sky_offset`` (the air row stays 0)."""
    sky_ua = 3.0
    room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        sky_radiative_ua=sky_ua,
    )
    model = HouseModel([room])
    # Wall node (index 1): -sky_ua · ΔT_sky / C_wall.
    expected = -sky_ua * DEFAULT_DELTA_T_SKY / room.c_wall
    assert model._B_sky_offset[0] == pytest.approx(0.0, abs=1e-15)
    assert model._B_sky_offset[1] == pytest.approx(expected, rel=1e-9)


def test_sky_offset_is_zero_when_sky_ua_is_zero() -> None:
    """With ``sky_radiative_ua = 0`` (the default) ``_B_sky_offset``
    is the zero vector — the no-regression invariant."""
    room = Room(name="a", thermal_mass=5e6, r_external=0.05)
    model = HouseModel([room])
    assert np.allclose(model._B_sky_offset, 0.0)


def test_clear_night_cooling_drives_single_node_below_outdoor() -> None:
    """End-to-end: a clear-night room (sky_ua > 0, no heat input,
    T = T_out) sees a *negative* temperature drift — colder than plain
    conduction would give."""
    base_room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        temperature=10.0,
    )
    sky_room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        temperature=10.0,
        sky_radiative_ua=3.0,
    )
    base_model = HouseModel([base_room])
    sky_model = HouseModel([sky_room])
    args = dict(dt=900.0, heat_inputs={}, outdoor_temp=10.0, solar_gains={})
    for _ in range(40):  # 10 hours
        base_model.step(**args)
        sky_model.step(**args)
    # Sky-coupled room should have dropped below outdoor; baseline stays at outdoor.
    assert base_model.temperatures["a"] == pytest.approx(10.0, abs=1e-3)
    assert sky_model.temperatures["a"] < 9.9


# ---------------------------------------------------------------------------
# C4 — sol-air on opaque surfaces
# ---------------------------------------------------------------------------


def test_default_facade_solar_share_is_zero() -> None:
    """A bare ``Room(...)`` has ``facade_solar_share = 0`` so no
    sol-air heat input lands on the node — pre-C4 behaviour."""
    room = Room(name="a", thermal_mass=5e6, r_external=0.05)
    assert room.facade_solar_share == pytest.approx(0.0)


def test_facade_absorptance_typology_defaults_via_coordinator() -> None:
    """The coordinator resolves a YAML ``facade_colour`` preset into
    ``facade_absorptance`` via the colour map; spot-check the three
    canonical values."""
    assert FACADE_COLOUR_TO_ABSORPTANCE[FACADE_COLOUR_LIGHT] < (
        FACADE_COLOUR_TO_ABSORPTANCE[FACADE_COLOUR_MEDIUM]
    )
    assert FACADE_COLOUR_TO_ABSORPTANCE[FACADE_COLOUR_MEDIUM] < (
        FACADE_COLOUR_TO_ABSORPTANCE[FACADE_COLOUR_DARK]
    )


def test_facade_solar_lands_on_single_node() -> None:
    """A room with non-zero ``facade_solar_share`` sees the window-
    derived solar gain ALSO contribute to the single temperature node
    via the sol-air fraction.  The room temperature should be HIGHER
    with facade sol-air than without (same direct solar gain)."""
    room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        temperature=20.0,
        facade_absorptance=0.8, facade_solar_share=0.5,
    )
    model = HouseModel([room])
    new = model.step(
        dt=900.0, heat_inputs={}, outdoor_temp=20.0,
        solar_gains={"a": 1000.0},
    )
    # Compare against a room with no facade share.
    base = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        temperature=20.0,
    )
    base_model = HouseModel([base])
    base_new = base_model.step(
        dt=900.0, heat_inputs={}, outdoor_temp=20.0,
        solar_gains={"a": 1000.0},
    )
    assert new["a"] > base_new["a"] + 1e-3, (
        f"sol-air temp ({new['a']:.4f}) should be higher than baseline "
        f"({base_new['a']:.4f}) with α=0.8, share=0.5, Q_sol=1000W"
    )


def test_sol_air_share_scales_linearly() -> None:
    """Doubling ``facade_solar_share`` should approximately double the
    facade-induced temperature rise above the share=0 baseline.

    Over a short step the C4 sol-air contribution scales linearly in
    ``facade_solar_share``."""
    def _temp_rise(share: float) -> float:
        room = Room(
            name="a", thermal_mass=5e6, r_external=0.05,
            temperature=20.0,
            facade_absorptance=0.6, facade_solar_share=share,
        )
        model = HouseModel([room])
        new = model.step(
            dt=60.0,  # short step → linear regime
            heat_inputs={}, outdoor_temp=20.0,
            solar_gains={"a": 1000.0},
        )
        return new["a"] - 20.0

    rise_base = _temp_rise(0.0)
    rise_low = _temp_rise(0.2) - rise_base
    rise_high = _temp_rise(0.4) - rise_base
    ratio = rise_high / max(rise_low, 1e-12)
    assert ratio == pytest.approx(2.0, rel=0.05)


# ---------------------------------------------------------------------------
# C5 — thermal-bridge correction
# ---------------------------------------------------------------------------


def test_default_thermal_bridge_psi_l_is_zero() -> None:
    """Bare ``Room`` has ``thermal_bridge_psi_l = 0`` — pre-C5 behaviour."""
    room = Room(name="a", thermal_mass=5e6, r_external=0.05)
    assert room.thermal_bridge_psi_l == pytest.approx(0.0)


def test_thermal_bridge_increases_wall_node_outdoor_conductance() -> None:
    """``thermal_bridge_psi_l > 0`` adds to the wall→outdoor
    conductance (B_ext[n]) — thermal bridges live in the envelope."""
    psi_l = 4.0
    room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        thermal_bridge_psi_l=psi_l,
    )
    model = HouseModel([room])
    _g_inf, _g_aw, g_we = room.conductances()
    expected_b = g_we + 0.0 + psi_l
    assert model._B_ext[1] == pytest.approx(expected_b)


def test_thermal_bridge_cools_room_faster() -> None:
    """A room with a thermal bridge cools faster than an otherwise
    identical room without one (no heat input, cold outdoor)."""
    base_room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        temperature=20.0,
    )
    bridged_room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        temperature=20.0,
        thermal_bridge_psi_l=4.0,
    )
    base_model = HouseModel([base_room])
    bridged_model = HouseModel([bridged_room])

    for _ in range(24):
        base_model.step(dt=900.0, heat_inputs={}, outdoor_temp=0.0, solar_gains={})
        bridged_model.step(dt=900.0, heat_inputs={}, outdoor_temp=0.0, solar_gains={})

    assert bridged_model.temperatures["a"] < base_model.temperatures["a"] - 0.1


# ---------------------------------------------------------------------------
# Controller-side plumbing
# ---------------------------------------------------------------------------


def test_controller_sees_sky_offset_in_drift() -> None:
    """The SDE's ``f`` includes the sky cooling-drift bias — the
    drift of the sky-coupled room should be more negative than the
    baseline room, evaluated at the same state."""
    no_sky = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        temperature=20.0,
    )
    sky = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        temperature=20.0,
        sky_radiative_ua=3.0,
    )
    src = ElectricHeater("h", "a", max_power=1000.0)
    sde_no = HouseThermalSDE(HouseModel([no_sky]), [src], dt=900.0)
    sde_sky = HouseThermalSDE(HouseModel([sky]), [src], dt=900.0)

    # 2R2C state: [T_a (1), T_w (1), b (1)] augmented.
    x = np.array([20.0, 20.0, 0.0])
    u = np.array([0.0])
    d = sde_no.disturbance_vector(20.0, {})
    p = np.array([])

    f_no = sde_no.f(x, u, d, p, 0.0)
    f_sky = sde_sky.f(x, u, d, p, 0.0)
    # The sky offset acts on the wall node (index n = 1).
    assert f_sky[1] < f_no[1] - 1e-9


def test_controller_sees_facade_solar_on_wall_node() -> None:
    """A facade-solar-equipped room has a larger solar channel gain on the
    WALL row (``G_d[n, 1]``) than a room with facade_solar_share = 0; the
    air row is unchanged (the sol-air heat lands on the envelope)."""
    facade_room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        temperature=20.0,
        facade_absorptance=0.8, facade_solar_share=0.5,
    )
    base_room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        temperature=20.0,
    )
    src = ElectricHeater("h", "a", max_power=1000.0)
    sde_facade = HouseThermalSDE(HouseModel([facade_room]), [src], dt=900.0)
    sde_base = HouseThermalSDE(HouseModel([base_room]), [src], dt=900.0)

    # G_d[1, 1] is the wall row of room 0's solar channel.
    # With facade sol-air it should be larger than the base.
    assert sde_facade._G_d[1, 1] > sde_base._G_d[1, 1] + 1e-9
    # The air-row coupling is the plain (1 − wall_fraction)/C_a share.
    assert sde_facade._G_d[0, 1] == pytest.approx(sde_base._G_d[0, 1], rel=1e-9)


def test_controller_no_facade_solar_when_share_zero() -> None:
    """Default ``facade_solar_share = 0`` keeps the solar channel at the
    plain air/wall split (scaled by solar_scale = 1): the air row couples
    (1 − SOLAR_WALL_FRACTION)/C_a, the wall row SOLAR_WALL_FRACTION/C_w."""
    from heatingassistant.engine.const import SOLAR_WALL_FRACTION

    room = Room(name="a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    src = ElectricHeater("h", "a", max_power=1000.0)
    sde = HouseThermalSDE(HouseModel([room]), [src], dt=900.0)
    assert sde._G_d[0, 1] == pytest.approx(
        (1.0 - SOLAR_WALL_FRACTION) / sde._C_cap[0], rel=1e-9
    )
    assert sde._G_d[1, 1] == pytest.approx(
        SOLAR_WALL_FRACTION / sde._C_cap[1], rel=1e-9
    )
