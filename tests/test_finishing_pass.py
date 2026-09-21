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


def test_sky_ua_adds_to_air_node_conductance() -> None:
    """When ``sky_radiative_ua > 0`` outdoor UA on the air node grows
    by that amount, and the A diagonal becomes more negative."""
    sky_ua = 2.0
    room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        sky_radiative_ua=sky_ua,
    )
    model = HouseModel([room])
    g_inf, g_rest, _dup = room.conductances()
    expected_b = g_inf + g_rest
    assert model._B_ext[0] == pytest.approx(expected_b)
    assert model._A[0, 0] == pytest.approx(-expected_b, rel=1e-9)


def test_sky_offset_pulls_air_node_down_on_clear_night() -> None:
    """The constant sky cooling-drift bias ``−sky_ua · ΔT_sky / C``
    appears on the air node of ``_B_sky_offset``."""
    sky_ua = 3.0
    room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        sky_radiative_ua=sky_ua,
    )
    model = HouseModel([room])
    expected = -sky_ua * DEFAULT_DELTA_T_SKY / room.c_air
    assert model._B_sky_offset[0] == pytest.approx(expected, rel=1e-9)


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


def test_thermal_bridge_increases_air_node_outdoor_conductance() -> None:
    """``thermal_bridge_psi_l > 0`` adds to outdoor UA on the air node."""
    psi_l = 4.0
    room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        thermal_bridge_psi_l=psi_l,
    )
    model = HouseModel([room])
    g_inf, g_rest, _dup = room.conductances()
    assert model._B_ext[0] == pytest.approx(g_inf + g_rest)


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

    # 1R1C state: [T_a (1), b (1)] augmented.
    x = np.array([20.0, 0.0])
    u = np.array([0.0])
    d = sde_no.disturbance_vector(20.0, {})
    p = np.array([])

    f_no = sde_no.f(x, u, d, p, 0.0)
    f_sky = sde_sky.f(x, u, d, p, 0.0)
    # The sky offset acts on the air node.
    assert f_sky[0] < f_no[0] - 1e-9


def test_controller_sees_facade_solar_on_air_node() -> None:
    """A facade-solar-equipped room has a larger solar channel gain on the
    air row than a room with facade_solar_share = 0.
    """
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

    assert sde_facade._G_d[0, 1] > sde_base._G_d[0, 1] + 1e-9


def test_controller_no_facade_solar_when_share_zero() -> None:
    """Default ``facade_solar_share = 0`` puts all solar on the air node."""
    room = Room(name="a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    src = ElectricHeater("h", "a", max_power=1000.0)
    sde = HouseThermalSDE(HouseModel([room]), [src], dt=900.0)
    assert sde._G_d[0, 1] == pytest.approx(1.0 / sde._C_cap[0], rel=1e-9)
    assert sde._G_d.shape[0] == 1
