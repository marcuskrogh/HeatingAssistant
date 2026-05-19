"""
Phase 1 Step 5 (B2) — tests for the per-source first-order emitter filter.

The B2 spec: each heat source's commanded fraction ``u`` is passed
through a first-order filter with an identified time constant
``τ_em`` before reaching the air (or slab, for UFH) node:

    dφ_j/dt = (u_j - φ_j) / τ_em_j
    Q_j = thermal_power(φ_j, T_out)   (instead of thermal_power(u_j, ...))

Sources with ``τ_em = 0`` (the default for electric resistive heaters,
and the pre-B2 behaviour) bypass the filter — ``u`` flows directly to
``thermal_power``.  Sources with ``τ_em > 0`` add one filter state to
the augmented state vector at indices ``3n..3n+m`` (where ``m`` is the
number of filtered sources).

These tests pin down:

  * Default τ_em values per source type (electric=0, heat-pump=0 at the
    class level; coordinator applies typology defaults).
  * State vector growth: nx = 4n + m augmented; m = 0 when all sources
    have τ_em = 0.
  * Filter dynamics: dφ/dt = (u - φ) / τ approaches u with time
    constant τ.
  * Heat-source power in ``f`` uses φ (not u) for filtered sources.
  * dfdx carries the filter→air/slab cross-coupling and the filter-
    block diagonal -1/τ.
  * Coordinator typology defaults: heat-pump configs get τ_em = 60 s
    by default; user override wins.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.const import (
    CONF_SOURCE_EMITTER_TIME_CONSTANT,
    CONF_SOURCE_MAX_POWER,
    CONF_SOURCE_NAME,
    CONF_SOURCE_ROOM,
    CONF_SOURCE_TYPE,
    SOURCE_TYPE_ELECTRIC,
    SOURCE_TYPE_HEAT_PUMP,
    SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU,
)
from custom_components.heating_assistant.controller import HouseThermalSDE
from custom_components.heating_assistant.coordinator import build_heat_sources
from custom_components.heating_assistant.heat_sources import (
    ElectricHeater,
    HeatPump,
)
from custom_components.heating_assistant.thermal_model import HouseModel, Room


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_default_emitter_time_constant_is_zero_at_class_level() -> None:
    """Direct construction of ``ElectricHeater`` and ``HeatPump`` keeps
    ``emitter_time_constant = 0`` so pre-B2 tests don't accidentally
    inherit a filter state."""
    eh = ElectricHeater(name="h", room="a", max_power=1000.0)
    assert eh.emitter_time_constant == pytest.approx(0.0)
    hp = HeatPump(name="p", room="a", max_power=3000.0)
    assert hp.emitter_time_constant == pytest.approx(0.0)


def test_coordinator_applies_typology_default_for_heat_pump() -> None:
    """When the YAML config doesn't specify
    ``emitter_time_constant``, ``coordinator.build_heat_sources``
    picks the typology default from
    ``SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU``."""
    cfg = [
        {
            CONF_SOURCE_NAME: "p",
            CONF_SOURCE_TYPE: SOURCE_TYPE_HEAT_PUMP,
            CONF_SOURCE_ROOM: "a",
            CONF_SOURCE_MAX_POWER: 3000.0,
        }
    ]
    sources = build_heat_sources(cfg)
    assert sources[0].emitter_time_constant == pytest.approx(
        SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU[SOURCE_TYPE_HEAT_PUMP]
    )


def test_coordinator_user_override_wins_over_typology() -> None:
    """Per-source ``emitter_time_constant`` overrides the typology default."""
    cfg = [
        {
            CONF_SOURCE_NAME: "p",
            CONF_SOURCE_TYPE: SOURCE_TYPE_HEAT_PUMP,
            CONF_SOURCE_ROOM: "a",
            CONF_SOURCE_MAX_POWER: 3000.0,
            CONF_SOURCE_EMITTER_TIME_CONSTANT: 600.0,
        }
    ]
    sources = build_heat_sources(cfg)
    assert sources[0].emitter_time_constant == pytest.approx(600.0)


def test_coordinator_electric_typology_default_is_zero() -> None:
    """Electric resistive heaters default to τ_em = 0 (no filter)."""
    cfg = [
        {
            CONF_SOURCE_NAME: "h",
            CONF_SOURCE_TYPE: SOURCE_TYPE_ELECTRIC,
            CONF_SOURCE_ROOM: "a",
            CONF_SOURCE_MAX_POWER: 1000.0,
        }
    ]
    sources = build_heat_sources(cfg)
    assert sources[0].emitter_time_constant == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# State-vector growth
# ---------------------------------------------------------------------------


def test_nx_unchanged_when_all_sources_unfiltered() -> None:
    """With every source at τ_em = 0, the state vector size is the
    same as pre-B2: 4n augmented, 3n un-augmented."""
    room = Room("a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    model = HouseModel([room])
    src = ElectricHeater("h", "a", max_power=1000.0)  # τ_em = 0
    sde = HouseThermalSDE(model, [src], dt=900.0)
    assert sde.nx == 4  # 4n with n=1, m=0


def test_nx_grows_by_m_when_sources_have_tau_em() -> None:
    """One filtered source → nx grows by 1."""
    room = Room("a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    model = HouseModel([room])
    src = HeatPump("p", "a", max_power=3000.0, emitter_time_constant=60.0)
    sde = HouseThermalSDE(model, [src], dt=900.0)
    assert sde.nx == 5  # 4n + m = 4 + 1


def test_nx_mixed_filtered_unfiltered() -> None:
    """Mixed roster: 2 filtered + 1 unfiltered source → m = 2."""
    room = Room("a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    model = HouseModel([room])
    sources = [
        HeatPump("p1", "a", max_power=3000.0, emitter_time_constant=60.0),
        ElectricHeater("h", "a", max_power=1000.0),  # no filter
        HeatPump("p2", "a", max_power=2000.0, emitter_time_constant=600.0),
    ]
    sde = HouseThermalSDE(model, sources, dt=900.0)
    assert sde._n_filtered == 2
    assert sde.nx == 4 + 2  # 4n + m


def test_filter_idx_for_source_mapping() -> None:
    """The source→filter index map matches the filtered-source order."""
    room = Room("a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    model = HouseModel([room])
    sources = [
        ElectricHeater("h0", "a", max_power=1000.0),                # unfiltered → -1
        HeatPump("p1", "a", max_power=3000.0, emitter_time_constant=60.0),  # filtered → 0
        ElectricHeater("h2", "a", max_power=1000.0),                # unfiltered → -1
        HeatPump("p3", "a", max_power=2000.0, emitter_time_constant=600.0), # filtered → 1
    ]
    sde = HouseThermalSDE(model, sources, dt=900.0)
    assert list(sde._filter_idx_for_source) == [-1, 0, -1, 1]
    assert sde._filtered_source_indices == [1, 3]


# ---------------------------------------------------------------------------
# Filter dynamics in f()
# ---------------------------------------------------------------------------


def _aug(temps, phis=None, offsets=None):
    """Build an augmented state vector ``[T_a, T_w, T_s, φ, b]``."""
    n = len(temps)
    walls = list(temps)
    slabs = list(temps)
    phis = list(phis) if phis is not None else []
    offsets = list(offsets) if offsets is not None else [0.0] * n
    return np.array(list(temps) + walls + slabs + phis + offsets, dtype=float)


def test_filter_dynamics_drift_toward_commanded_fraction() -> None:
    """dφ/dt = (u - φ)/τ — when u > φ the drift is positive."""
    room = Room("a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    model = HouseModel([room])
    src = HeatPump("p", "a", max_power=3000.0, emitter_time_constant=60.0)
    sde = HouseThermalSDE(model, [src], dt=900.0)
    # Augmented state [T_a, T_w, T_s, φ, b] with φ = 0.2.
    x = _aug([20.0], phis=[0.2])
    u = np.array([0.8])  # commanded > current filter
    d = sde.disturbance_vector(5.0, {})
    p = np.array([])
    f = sde.f(x, u, d, p, 0.0)
    # Filter drift sits at index 3 (3n+0 with n=1).
    phi_drift = f[3]
    expected = (0.8 - 0.2) / 60.0
    assert phi_drift == pytest.approx(expected, rel=1e-9)


def test_filter_dynamics_zero_at_steady_state() -> None:
    """When φ = u the filter drift is zero."""
    room = Room("a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    model = HouseModel([room])
    src = HeatPump("p", "a", max_power=3000.0, emitter_time_constant=60.0)
    sde = HouseThermalSDE(model, [src], dt=900.0)
    x = _aug([20.0], phis=[0.5])
    u = np.array([0.5])
    f = sde.f(x, u, sde.disturbance_vector(5.0, {}), np.array([]), 0.0)
    assert f[3] == pytest.approx(0.0, abs=1e-12)


# ---------------------------------------------------------------------------
# Heat input is gated by the filter state
# ---------------------------------------------------------------------------


def test_heat_input_uses_filter_state_not_commanded_u() -> None:
    """For a filtered source, the heat applied to the air node depends
    on φ — not on the raw u.  Commanding u = 1 with φ = 0 should
    produce *no* heat input on this cycle (the filter is at zero)."""
    room = Room("a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    model = HouseModel([room])
    src = ElectricHeater(
        "h", "a", max_power=1000.0,
        efficiency=1.0,
        emitter_time_constant=60.0,  # opt-in filter on this electric heater
    )
    sde = HouseThermalSDE(model, [src], dt=900.0)
    # φ = 0 ⇒ thermal_power(0) = 0 ⇒ no air-block heating term.
    x = _aug([20.0], phis=[0.0])
    u = np.array([1.0])  # commanded full power, but the filter is at 0
    d = sde.disturbance_vector(20.0, {})  # outdoor = room → no conduction loss
    f = sde.f(x, u, d, np.array([]), 0.0)
    # Air drift comes only from the wall and slab couplings.  At
    # T_a = T_w = T_s and no heat input, those couplings are zero.
    assert f[0] == pytest.approx(0.0, abs=1e-9)
    # Filter drift is positive, pointing toward u = 1.
    assert f[3] == pytest.approx(1.0 / 60.0, rel=1e-9)


def test_filtered_source_warms_room_only_after_filter_ramps() -> None:
    """When the filter is at φ = 0.8, the heat applied to the air
    matches ``thermal_power(0.8)`` regardless of the commanded u."""
    room = Room("a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    model = HouseModel([room])
    src = ElectricHeater(
        "h", "a", max_power=1000.0,
        efficiency=1.0,
        emitter_time_constant=60.0,
    )
    sde = HouseThermalSDE(model, [src], dt=900.0)

    # φ = 0.8, commanded u = 0.0 (just ramped down).  Heat input is
    # still 0.8 × 1000 = 800 W, decreasing on subsequent steps as φ
    # follows u down.
    x = _aug([20.0], phis=[0.8])
    u_low = np.array([0.0])
    d = sde.disturbance_vector(20.0, {})
    f_low = sde.f(x, u_low, d, np.array([]), 0.0)
    # Air drift is 800 W / C_air (positive).
    C_air = sde._C_cap[0]
    assert f_low[0] == pytest.approx(800.0 / C_air, rel=1e-6)
    # Filter drift is negative (φ ramping down toward u = 0).
    assert f_low[3] < 0.0


# ---------------------------------------------------------------------------
# UFH + filter combination
# ---------------------------------------------------------------------------


def test_ufh_filtered_heat_lands_on_slab_block() -> None:
    """When a UFH room has a filtered heater, ``thermal_power(φ)``
    lands on the slab block (Phase 1 B1) — not on the air block."""
    room = Room(
        "a", thermal_mass=5e6, r_external=0.05,
        floor_type="ufh", temperature=20.0,
    )
    model = HouseModel([room])
    src = ElectricHeater(
        "ufh", "a", max_power=2000.0,
        efficiency=1.0,
        emitter_time_constant=600.0,  # hydronic-loop lag
    )
    sde = HouseThermalSDE(model, [src], dt=900.0)
    # Push T_g = 20 °C so the slab's ground-coupling drift is zero at
    # the test's initial condition (T_s = 20).  This isolates the
    # heat-input contribution we want to measure.
    sde.set_ground_temp(20.0)
    # φ = 0.6 → 1200 W → onto slab.
    x = _aug([20.0], phis=[0.6])
    u = np.array([0.6])  # at steady state, φ = u
    d = sde.disturbance_vector(20.0, {})
    f = sde.f(x, u, d, np.array([]), 0.0)
    n = 1
    # Slab block (index 2n+0 = 2) drifts positive from 1200 W.
    C_slab = sde._C_cap[2 * n + 0]
    assert f[2 * n + 0] == pytest.approx(1200.0 / C_slab, rel=1e-6)
    # Air block (index 0) drifts only via slab coupling — much smaller.
    assert f[0] < f[2 * n + 0]


# ---------------------------------------------------------------------------
# dfdx with the filter
# ---------------------------------------------------------------------------


def test_dfdx_filter_diagonal_is_minus_inv_tau() -> None:
    """The filter block's diagonal of ∂f/∂x is -1/τ_em per filtered
    source."""
    room = Room("a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    model = HouseModel([room])
    src = HeatPump("p", "a", max_power=3000.0, emitter_time_constant=60.0)
    sde = HouseThermalSDE(model, [src], dt=900.0)
    x = _aug([20.0], phis=[0.5])
    u = np.array([0.5])
    J = sde.dfdx(x, u, sde.disturbance_vector(5.0, {}), np.array([]), 0.0)
    # Filter block diagonal at index 3 (3n with n=1) is -1/60.
    assert J[3, 3] == pytest.approx(-1.0 / 60.0, rel=1e-9)


def test_dfdx_filter_to_air_cross_coupling_for_non_ufh_source() -> None:
    """Heat from a filtered (non-UFH) source contributes
    ∂(f_T_a)/∂φ = (∂Q/∂φ) / C_air > 0 to the Jacobian.  Verified
    against the analytic slope for an electric heater."""
    room = Room("a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    model = HouseModel([room])
    src = ElectricHeater(
        "h", "a", max_power=1000.0,
        efficiency=1.0,
        emitter_time_constant=60.0,
    )
    sde = HouseThermalSDE(model, [src], dt=900.0)
    x = _aug([20.0], phis=[0.5])
    u = np.array([0.5])
    J = sde.dfdx(x, u, sde.disturbance_vector(5.0, {}), np.array([]), 0.0)
    # ∂Q/∂φ for an electric heater is P_max × efficiency = 1000 W.
    expected = 1000.0 / sde._C_cap[0]
    assert J[0, 3] == pytest.approx(expected, rel=1e-3)
    # The slab block stays untouched for a non-UFH room.
    n = 1
    assert J[2 * n + 0, 3] == pytest.approx(0.0, abs=1e-9)


def test_dfdx_filter_to_slab_cross_coupling_for_ufh_source() -> None:
    """In a UFH room, ∂(f_T_s)/∂φ > 0 (filter→slab cross-coupling)
    and ∂(f_T_a)/∂φ ≈ 0."""
    room = Room(
        "a", thermal_mass=5e6, r_external=0.05,
        floor_type="ufh", temperature=20.0,
    )
    model = HouseModel([room])
    src = ElectricHeater(
        "ufh", "a", max_power=2000.0,
        efficiency=1.0,
        emitter_time_constant=600.0,
    )
    sde = HouseThermalSDE(model, [src], dt=900.0)
    x = _aug([20.0], phis=[0.5])
    u = np.array([0.5])
    J = sde.dfdx(x, u, sde.disturbance_vector(5.0, {}), np.array([]), 0.0)
    n = 1
    # ∂(f_T_s)/∂φ = 2000 / C_slab.
    expected = 2000.0 / sde._C_cap[2 * n + 0]
    assert J[2 * n + 0, 3] == pytest.approx(expected, rel=1e-3)
    # ∂(f_T_a)/∂φ for the UFH source is zero (no direct air coupling).
    assert J[0, 3] == pytest.approx(0.0, abs=1e-9)


def test_dfdx_unfiltered_source_does_not_change_jacobian_block_size() -> None:
    """When ``m = 0`` the Jacobian shape is unchanged from the pre-B2
    layout (4n × 4n augmented)."""
    room = Room("a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    model = HouseModel([room])
    src = ElectricHeater("h", "a", max_power=1000.0)  # τ_em = 0
    sde = HouseThermalSDE(model, [src], dt=900.0)
    x = _aug([20.0])
    J = sde.dfdx(x, np.array([0.5]), sde.disturbance_vector(5.0, {}), np.array([]), 0.0)
    assert J.shape == (4, 4)


# ---------------------------------------------------------------------------
# x getter / setter round-trip
# ---------------------------------------------------------------------------


def test_x_setter_extracts_filter_block() -> None:
    """The ``x`` setter writes the filter block into the SDE's
    internal cache, and the getter returns it intact."""
    room = Room("a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    model = HouseModel([room])
    src = HeatPump("p", "a", max_power=3000.0, emitter_time_constant=60.0)
    sde = HouseThermalSDE(model, [src], dt=900.0)
    # Augmented: [T_a (1), T_w (1), T_s (1), φ (1), b (1)] = length 5.
    sde.x = [20.0, 19.0, 18.0, 0.42, 0.1]
    out = sde.x
    assert len(out) == 5
    assert out[3] == pytest.approx(0.42)


def test_x_setter_cold_start_initialises_filter_to_zero() -> None:
    """A length-n input (cold start) initialises the filter to zero."""
    room = Room("a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    model = HouseModel([room])
    src = HeatPump("p", "a", max_power=3000.0, emitter_time_constant=60.0)
    sde = HouseThermalSDE(model, [src], dt=900.0)
    sde.x = [20.0]  # only air
    out = sde.x
    assert out[3] == pytest.approx(0.0)
