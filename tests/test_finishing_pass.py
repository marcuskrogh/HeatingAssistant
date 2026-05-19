"""
Phase 1 Step 6 (C3 + C4 + C5) — tests for the finishing-pass envelope
correction terms.

All three corrections default to **off** (zero) per room, so existing
installs see no behaviour change.  Each is opt-in via per-room YAML
fields:

  * **C3 long-wave to sky** — ``sky_radiative_ua`` (W/K).  Adds a
    radiative conductance in parallel with the wall→outdoor
    conductance, plus a constant cooling drift
    ``−sky_radiative_ua · ΔT_sky`` on the wall node (the clear-night
    cooling effect).  ΔT_sky is a model constant (default 6 K);
    Phase 5 will promote it to a cloud-cover-driven term.

  * **C4 sol-air on opaque** — ``facade_colour`` / ``facade_absorptance``
    + ``facade_solar_share``.  A fraction
    ``facade_absorptance × facade_solar_share`` of the room's
    window-derived solar gain lands on the wall block (the opaque
    facade's sol-air heat input).  The full per-surface geometry
    model is a Phase 5 / 6 follow-up.

  * **C5 thermal-bridge correction** — ``thermal_bridge_psi_l``
    (W/K).  Adds to the wall→outdoor conductance.  Identified from
    data with a strong prior centred on zero — Phase 4 will surface
    this in the identifiability diagnostics.

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

from custom_components.heating_assistant.const import (
    DEFAULT_DELTA_T_SKY,
    FACADE_COLOUR_DARK,
    FACADE_COLOUR_LIGHT,
    FACADE_COLOUR_MEDIUM,
    FACADE_COLOUR_TO_ABSORPTANCE,
)
from custom_components.heating_assistant.controller import HouseThermalSDE
from custom_components.heating_assistant.heat_sources import ElectricHeater
from custom_components.heating_assistant.thermal_model import HouseModel, Room


# ---------------------------------------------------------------------------
# C3 — long-wave to sky
# ---------------------------------------------------------------------------


def test_default_sky_radiative_ua_is_zero() -> None:
    """A bare ``Room(...)`` has ``sky_radiative_ua = 0`` so the wall
    block sees no long-wave-to-sky contribution.  Preserves pre-C3
    behaviour for existing installs."""
    room = Room(name="a", thermal_mass=5e6, r_external=0.05)
    assert room.sky_radiative_ua == pytest.approx(0.0)


def test_sky_ua_adds_to_wall_conductance() -> None:
    """When ``sky_radiative_ua > 0`` the wall's B_ext entry grows by
    that amount, and the corresponding A diagonal becomes more negative."""
    sky_ua = 2.0
    room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        sky_radiative_ua=sky_ua,
    )
    model = HouseModel([room])
    n = 1
    j_w = n + 0
    # B_ext on the wall row: 1/R_we + sky_ua + thermal_bridge.
    expected_b = 1.0 / model._r_we[0] + sky_ua + 0.0
    assert model._B_ext[j_w] == pytest.approx(expected_b)
    # A diagonal carries -1/R_aw - (1/R_we + sky_ua + bridge) - no inter-room.
    expected_diag = -(
        1.0 / model._r_aw[0]
        + 1.0 / model._r_we[0]
        + sky_ua
    )
    assert model._A[j_w, j_w] == pytest.approx(expected_diag, rel=1e-9)


def test_sky_offset_pulls_wall_down_on_clear_night() -> None:
    """The constant sky cooling-drift bias ``−sky_ua · ΔT_sky / C_w``
    appears on the wall block of ``_B_sky_offset``.  Other blocks
    (air, slab) remain zero."""
    sky_ua = 3.0
    room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        sky_radiative_ua=sky_ua,
    )
    model = HouseModel([room])
    n = 1
    # Air block: zero
    assert model._B_sky_offset[0] == pytest.approx(0.0)
    # Wall block: -sky_ua · ΔT_sky / C_wall
    expected = -sky_ua * DEFAULT_DELTA_T_SKY / model._c_wall[0]
    assert model._B_sky_offset[n + 0] == pytest.approx(expected, rel=1e-9)
    # Slab block: zero
    assert model._B_sky_offset[2 * n + 0] == pytest.approx(0.0)


def test_sky_offset_is_zero_when_sky_ua_is_zero() -> None:
    """With ``sky_radiative_ua = 0`` (the default) ``_B_sky_offset``
    is the zero vector — the no-regression invariant."""
    room = Room(name="a", thermal_mass=5e6, r_external=0.05)
    model = HouseModel([room])
    assert np.allclose(model._B_sky_offset, 0.0)


def test_clear_night_cooling_drives_wall_below_outdoor() -> None:
    """End-to-end: a clear-night room (sky_ua > 0, no heat input,
    T_a = T_w = T_out) sees a *negative* wall drift — colder than
    plain conduction would give."""
    # Baseline: no sky coupling — wall sits in steady state.
    base_room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        air_temperature=10.0, wall_temperature=10.0, slab_temperature=10.0,
    )
    sky_room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        air_temperature=10.0, wall_temperature=10.0, slab_temperature=10.0,
        sky_radiative_ua=3.0,
    )
    base_model = HouseModel([base_room])
    sky_model = HouseModel([sky_room])
    # Step long enough for the sky-offset drift to dominate the
    # initial transient (the thermal time-constant is C_w / g_total
    # ≈ 2.5e6 / O(20) ≈ ~30 h, so we run for several hours).
    args = dict(dt=900.0, heat_inputs={}, outdoor_temp=10.0, solar_gains={})
    for _ in range(40):  # 10 hours
        base_model.step(**args)
        sky_model.step(**args)
    # Sky-coupled wall should have dropped below outdoor; baseline
    # wall stays at outdoor (no driving gradient).
    assert base_model.wall_temperatures["a"] == pytest.approx(10.0, abs=1e-3)
    assert sky_model.wall_temperatures["a"] < 9.9


# ---------------------------------------------------------------------------
# C4 — sol-air on opaque surfaces
# ---------------------------------------------------------------------------


def test_default_facade_solar_share_is_zero() -> None:
    """A bare ``Room(...)`` has ``facade_solar_share = 0`` so no
    sol-air heat input lands on the wall — pre-C4 behaviour."""
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


def test_facade_solar_lands_on_wall_block() -> None:
    """A room with non-zero ``facade_solar_share`` sees the window-
    derived solar gain *also* contribute to the wall block of Q,
    proportional to ``facade_absorptance × facade_solar_share``."""
    room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        air_temperature=20.0, wall_temperature=20.0, slab_temperature=20.0,
        facade_absorptance=0.8, facade_solar_share=0.5,
    )
    model = HouseModel([room])
    # 1 kW solar gain into the room.
    new = model.step(
        dt=900.0, heat_inputs={}, outdoor_temp=20.0,
        solar_gains={"a": 1000.0},
    )
    wall = model.wall_temperatures["a"]
    # Wall should have risen above its starting value due to the
    # sol-air contribution.  Baseline (share=0) keeps the wall at
    # outdoor.
    base = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        air_temperature=20.0, wall_temperature=20.0, slab_temperature=20.0,
    )
    base_model = HouseModel([base])
    base_model.step(
        dt=900.0, heat_inputs={}, outdoor_temp=20.0,
        solar_gains={"a": 1000.0},
    )
    base_wall = base_model.wall_temperatures["a"]
    assert wall > base_wall + 1e-3, (
        f"sol-air wall ({wall:.4f}) should rise above baseline wall "
        f"({base_wall:.4f}) with α=0.8, share=0.5, Q_sol=1000W"
    )


def test_sol_air_share_scales_linearly() -> None:
    """Doubling ``facade_solar_share`` should approximately double the
    facade-induced wall-temperature rise *above the share=0 baseline*.

    The baseline rise comes from air→wall conduction driven by the
    solar gain warming the air node; the C4 effect is the *additional*
    direct-on-wall sol-air contribution, which scales linearly in
    ``facade_solar_share`` over a short step."""
    def _wall_rise(share: float) -> float:
        room = Room(
            name="a", thermal_mass=5e6, r_external=0.05,
            air_temperature=20.0, wall_temperature=20.0, slab_temperature=20.0,
            facade_absorptance=0.6, facade_solar_share=share,
        )
        model = HouseModel([room])
        model.step(
            dt=60.0,  # short step → linear regime
            heat_inputs={}, outdoor_temp=20.0,
            solar_gains={"a": 1000.0},
        )
        return model.wall_temperatures["a"] - 20.0

    rise_base = _wall_rise(0.0)
    rise_low = _wall_rise(0.2) - rise_base
    rise_high = _wall_rise(0.4) - rise_base
    # The C4 contribution scales linearly in share.
    ratio = rise_high / max(rise_low, 1e-12)
    assert ratio == pytest.approx(2.0, rel=0.05)


# ---------------------------------------------------------------------------
# C5 — thermal-bridge correction
# ---------------------------------------------------------------------------


def test_default_thermal_bridge_psi_l_is_zero() -> None:
    """Bare ``Room`` has ``thermal_bridge_psi_l = 0`` — pre-C5 behaviour."""
    room = Room(name="a", thermal_mass=5e6, r_external=0.05)
    assert room.thermal_bridge_psi_l == pytest.approx(0.0)


def test_thermal_bridge_increases_wall_outdoor_conductance() -> None:
    """``thermal_bridge_psi_l > 0`` adds to the wall→outdoor
    conductance (B_ext) and tightens the wall A-diagonal."""
    psi_l = 4.0
    room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        thermal_bridge_psi_l=psi_l,
    )
    model = HouseModel([room])
    n = 1
    j_w = n + 0
    expected_b = 1.0 / model._r_we[0] + 0.0 + psi_l
    assert model._B_ext[j_w] == pytest.approx(expected_b)


def test_thermal_bridge_cools_room_faster() -> None:
    """A room with a thermal bridge cools faster than an otherwise
    identical room without one (no heat input, cold outdoor)."""
    base_room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        air_temperature=20.0, wall_temperature=20.0, slab_temperature=20.0,
    )
    bridged_room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        air_temperature=20.0, wall_temperature=20.0, slab_temperature=20.0,
        thermal_bridge_psi_l=4.0,
    )
    base_model = HouseModel([base_room])
    bridged_model = HouseModel([bridged_room])

    # Run for 24 × 15 min = 6 h — long enough for the bridge-driven
    # extra heat loss to produce a clearly visible air-temperature
    # difference against the slow envelope cooling.
    for _ in range(24):
        base_model.step(
            dt=900.0, heat_inputs={}, outdoor_temp=0.0, solar_gains={},
        )
        bridged_model.step(
            dt=900.0, heat_inputs={}, outdoor_temp=0.0, solar_gains={},
        )

    assert bridged_model.temperatures["a"] < base_model.temperatures["a"] - 0.1


# ---------------------------------------------------------------------------
# Controller-side plumbing
# ---------------------------------------------------------------------------


def test_controller_sees_sky_offset_in_drift() -> None:
    """The SDE's ``f`` includes the sky cooling-drift bias — the
    wall-block drift is more negative for a sky-coupled room than
    for an identical room without sky coupling, evaluated at the
    same state."""
    no_sky = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        air_temperature=20.0, wall_temperature=20.0, slab_temperature=20.0,
    )
    sky = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        air_temperature=20.0, wall_temperature=20.0, slab_temperature=20.0,
        sky_radiative_ua=3.0,
    )
    src = ElectricHeater("h", "a", max_power=1000.0)
    sde_no = HouseThermalSDE(HouseModel([no_sky]), [src], dt=900.0)
    sde_sky = HouseThermalSDE(HouseModel([sky]), [src], dt=900.0)

    # State: T_a = T_w = T_s = 20, b = 0.  4 augmented (n=1) + 0 filtered.
    x = np.array([20.0, 20.0, 20.0, 0.0])
    u = np.array([0.0])
    d = sde_no.disturbance_vector(20.0, {})
    p = np.array([])

    f_no = sde_no.f(x, u, d, p, 0.0)
    f_sky = sde_sky.f(x, u, d, p, 0.0)
    # Sky-coupled wall-block drift (index n+0 = 1) should be more
    # negative (clear-night cooling).
    assert f_sky[1] < f_no[1] - 1e-9


def test_controller_sees_facade_solar_on_wall_block() -> None:
    """A facade-solar-equipped room receives part of its window-
    derived solar gain on the wall block — the SDE's ``f`` reflects
    this via ``G_d`` carrying the share on the wall row of the solar
    channel."""
    facade_room = Room(
        name="a", thermal_mass=5e6, r_external=0.05,
        air_temperature=20.0, wall_temperature=20.0, slab_temperature=20.0,
        facade_absorptance=0.8, facade_solar_share=0.5,
    )
    src = ElectricHeater("h", "a", max_power=1000.0)
    sde = HouseThermalSDE(HouseModel([facade_room]), [src], dt=900.0)
    # G_d wall-block row (index n+0 = 1) on the solar column (index 1+0 = 1)
    # should be positive (0.5 · 0.8 / C_wall).
    n = 1
    expected = 0.5 * 0.8 / sde._C_cap[n + 0]
    assert sde._G_d[n + 0, 1 + 0] == pytest.approx(expected, rel=1e-9)


def test_controller_no_facade_solar_when_share_zero() -> None:
    """Default ``facade_solar_share = 0`` keeps the wall-row solar
    entry of G_d at zero — pre-C4 behaviour."""
    room = Room(name="a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    src = ElectricHeater("h", "a", max_power=1000.0)
    sde = HouseThermalSDE(HouseModel([room]), [src], dt=900.0)
    assert sde._G_d[1, 1] == pytest.approx(0.0)
