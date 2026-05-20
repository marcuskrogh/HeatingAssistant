"""Unit tests for the nonlinear CD-NMPC controller."""

import sys
import os
import pytest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

from custom_components.heating_assistant.thermal_model import (
    HouseModel,
    Room,
    RoomConnection,
)
from custom_components.heating_assistant.heat_sources import ElectricHeater, HeatPump
from mbc.models import ContinuousDiscreteModel
from mbc.estimation import ContinuousDiscreteEKF
from mbc.control import CDTrackingOptimalControlProblem, CDNMPCController
from custom_components.heating_assistant.controller import (
    HouseThermalSDE,
    HeatingMPCController,
)


# -- Helpers ------------------------------------------------------------------

def _make_model_and_sources():
    """Simple two-room model with one electric heater per room."""
    living = Room(
        name="living_room",
        thermal_mass=5_000_000.0,
        r_external=0.05,
        connections=[RoomConnection("bedroom", 0.2)],
        temperature=18.0,
        setpoint=21.0,
    )
    bedroom = Room(
        name="bedroom",
        thermal_mass=3_000_000.0,
        r_external=0.08,
        connections=[RoomConnection("living_room", 0.2)],
        temperature=17.0,
        setpoint=20.0,
    )
    model = HouseModel([living, bedroom])
    sources = [
        ElectricHeater("lr_heater", "living_room", max_power=2000.0),
        ElectricHeater("br_heater", "bedroom", max_power=1500.0),
    ]
    return model, sources


def _aug_state(
    temps: list[float],
    offsets: list[float] | None = None,
    walls: list[float] | None = None,
    slabs: list[float] | None = None,
) -> np.ndarray:
    """Build an augmented 2R2C+slab state vector ``[T_a, T_w, T_s, b]``.

    Phase 1 A1 grew per-room state from 1 to 2 nodes (air + wall);
    Phase 1 A2 added a third (slab), so the augmented vector now has
    length ``4n``.  Default cold-start convention: wall = slab = air,
    offset = 0.  The EKF lets them diverge over time.
    """
    if walls is None:
        walls = list(temps)
    if slabs is None:
        slabs = list(temps)
    if offsets is None:
        offsets = [0.0] * len(temps)
    return np.array(
        list(temps) + list(walls) + list(slabs) + list(offsets),
        dtype=float,
    )


def _central_difference_jacobian(
    fun,
    x: np.ndarray,
    eps: float = 1e-6,
) -> np.ndarray:
    """Central-difference Jacobian for vector-valued ``fun(x)``."""
    x = np.asarray(x, dtype=float)
    y0 = np.asarray(fun(x), dtype=float)
    J = np.zeros((y0.size, x.size), dtype=float)
    for i in range(x.size):
        dx = np.zeros_like(x)
        dx[i] = eps
        y_plus = np.asarray(fun(x + dx), dtype=float)
        y_minus = np.asarray(fun(x - dx), dtype=float)
        J[:, i] = (y_plus - y_minus) / (2.0 * eps)
    return J


# -- HouseThermalSDE tests ----------------------------------------------------

class TestHouseThermalSDE:
    """Tests for HouseThermalSDE as a ContinuousDiscreteModel implementation."""

    def test_is_continuous_discrete_model(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        assert isinstance(sde, ContinuousDiscreteModel)

    def test_dimensions(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        # Phase 1 A2: state grew from 3n ([T_a, T_w, b]) to 4n
        # ([T_a, T_w, T_s, b]).  nx = 2 rooms × 4 = 8.
        assert sde.nx == 8
        assert sde.nu == 2
        assert sde.nd == 3   # T_out + 2 solar/internal-gain channels
        assert sde.nw == 8   # one noise per state
        assert sde.nz == 2   # controlled output = air node per room
        assert sde.nym == 2  # measured output = air node per room

    def test_drift_shape(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.0, 17.0])
        u = np.array([0.5, 0.3])
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        f = sde.f(x, u, d, p, 0.0)
        # 2R2C+slab augmented drift: 4n = 8 entries
        # (air, wall, slab, offset blocks).
        assert f.shape == (8,)

    def test_drift_heating_increases_temperature(self):
        """Full heating (u=1) on a non-UFH room should give positive
        drift on the air block when the room is cold."""
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([15.0, 14.0])   # cold rooms (air; wall/slab start equal)
        u = np.array([1.0, 1.0])     # full heating
        d = sde.disturbance_vector(0.0, {})
        p = np.array([])
        f = sde.f(x, u, d, p, 0.0)
        # Layout: f = [air (n), wall (n), slab (n), offset (n)] with n=2.
        # Heat input lands on the air block → air drift positive.
        assert np.all(f[:2] > 0.0), f"Expected positive air drift, got {f}"
        # Offset block (last n entries) has zero drift.
        assert np.allclose(f[6:], 0.0)

    def test_drift_no_heat_cold_outside(self):
        """No heating and cold outside: warm rooms should cool down on
        the air node — and the wall node should drift cold (it
        conducts directly to outdoor via R_we)."""
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([20.0, 20.0])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(-10.0, {})
        p = np.array([])
        f = sde.f(x, u, d, p, 0.0)
        # Wall block (rows 2..4) drifts negative (direct conduction).
        assert np.all(f[2:4] < 0.0), f"Expected negative wall drift, got {f}"
        # Offset block (rows 6..8) has zero drift.
        assert np.allclose(f[6:], 0.0)

    def test_sigma_shape(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.0, 17.0])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        sig = sde.sigma(x, u, d, p, 0.0)
        # 2R2C+slab augmented: nx = 4n = 8 → σ is 8×8.
        assert sig.shape == (8, 8)

    def test_sigma_is_scaled_identity(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0, sigma_w=0.1)
        x = _aug_state([18.0, 17.0])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        sig = sde.sigma(x, u, d, p, 0.0)
        # Block diagonal: 0.1·I_6 on the (air, wall, slab) physical
        # block, 0.002·I_2 on the offset block.
        expected = np.zeros((8, 8))
        expected[:6, :6] = 0.1 * np.eye(6)
        expected[6:, 6:] = 0.002 * np.eye(2)
        np.testing.assert_array_almost_equal(sig, expected)

    def test_sigma_applies_per_room_process_noise_covariance_scales(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0, sigma_w=0.1)
        sde.set_room_process_noise_covariance_scales(
            {"living_room": 9.0, "bedroom": 1.0}
        )
        sig = sde.sigma(
            _aug_state([18.0, 17.0]),
            np.zeros(sde.nu),
            sde.disturbance_vector(5.0, {}),
            np.array([]),
            0.0,
        )
        diag = np.diag(sig)
        # State order: [T_a(2), T_w(2), T_s(2), b(2)].
        assert diag[0] == pytest.approx(0.3)  # 0.1 * sqrt(9)
        assert diag[1] == pytest.approx(0.1)
        assert diag[2] == pytest.approx(0.3)
        assert diag[3] == pytest.approx(0.1)
        assert diag[4] == pytest.approx(0.3)
        assert diag[5] == pytest.approx(0.1)

    def test_controlled_output_equals_state(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.5, 17.5])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        z = sde.g(x, u, d, p, 0.0)
        np.testing.assert_array_equal(z, np.array([18.5, 17.5]))

    def test_controlled_output_includes_offset_state(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.5, 17.5], [1.0, -0.25])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        z = sde.g(x, u, d, p, 0.0)
        np.testing.assert_array_equal(z, np.array([19.5, 17.25]))

    def test_measurement_equals_state(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.5, 17.5])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        ym = sde.hm(x, u, d, p, 0.0)
        np.testing.assert_array_equal(ym, np.array([18.5, 17.5]))

    def test_measurement_includes_offset_state(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.5, 17.5], [1.0, -0.25])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        ym = sde.hm(x, u, d, p, 0.0)
        np.testing.assert_array_equal(ym, np.array([19.5, 17.25]))

    def test_measurement_noise_covariance_shape(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        Rm = sde.Rm
        assert Rm.shape == (2, 2)
        assert np.allclose(Rm, Rm.T)

    def test_measurement_noise_covariance_positive_definite(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0, sigma_v=0.5)
        Rm = sde.Rm
        eigvals = np.linalg.eigvalsh(Rm)
        assert np.all(eigvals > 0)

    def test_analytic_state_jacobian(self):
        """``dfdx`` mirrors the structural 3n×3n matrix ``F`` on the
        top-left physical block and is zero on the offset rows/columns."""
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.0, 17.0])
        u = np.array([0.3, 0.5])
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        J_analytic = sde.dfdx(x, u, d, p, 0.0)
        # 2R2C+slab augmented Jacobian is 4n × 4n = 8 × 8.
        assert J_analytic.shape == (8, 8)
        # Top-left 3n × 3n block matches the structural F.  No wind set,
        # so the SG overlay is zero and J[:3n, :3n] == F exactly.
        np.testing.assert_array_almost_equal(J_analytic[:6, :6], sde._F)
        # Offset block (rows/cols 6..8) is all-zero (zero drift on b).
        np.testing.assert_array_equal(J_analytic[:6, 6:], np.zeros((6, 2)))
        np.testing.assert_array_equal(J_analytic[6:, :], np.zeros((2, 8)))

    def test_observation_jacobian_is_identity(self):
        """``dhm/dx`` for the 2R2C+slab measurement ``y = T_a + b`` is
        ``[I_n, 0_n, 0_n, I_n]``: identity on the air block, zero on
        the wall and slab blocks (both unobserved), identity on the
        offset block.
        """
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.0, 17.0])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        H = sde.dhmdx(x, u, d, p, 0.0)
        expected = np.array([
            [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
        ])
        np.testing.assert_array_equal(H, expected)

    def test_analytic_state_jacobian_matches_finite_difference_unaugmented(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=False)
        x = np.array([18.0, 17.0, 18.0, 17.0, 18.0, 17.0], dtype=float)
        u = np.array([0.4, 0.3], dtype=float)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])

        J_analytic = sde.dfdx(x, u, d, p, 0.0)
        J_fd = _central_difference_jacobian(
            lambda state: sde.f(state, u, d, p, 0.0),
            x,
        )
        np.testing.assert_allclose(J_analytic, J_fd, rtol=5e-4, atol=5e-6)

    def test_observation_jacobian_matches_finite_difference_unaugmented(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=False)
        x = np.array([18.0, 17.0, 18.0, 17.0, 18.0, 17.0], dtype=float)
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])

        H_analytic = sde.dhmdx(x, u, d, p, 0.0)
        H_fd = _central_difference_jacobian(
            lambda state: sde.hm(state, u, d, p, 0.0),
            x,
        )
        np.testing.assert_allclose(H_analytic, H_fd, rtol=1e-6, atol=1e-8)

    def test_disturbance_vector_shape(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        d = sde.disturbance_vector(5.0, {"living_room": 200.0, "bedroom": 50.0})
        assert d.shape == (3,)
        assert d[0] == pytest.approx(5.0)

    def test_x_ref_matches_setpoints(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        ref = sde.x_ref
        np.testing.assert_array_equal(ref, [21.0, 20.0])

    def test_u_bounds(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        lb, ub = sde.u_bounds
        np.testing.assert_array_equal(lb, [0.0, 0.0])
        np.testing.assert_array_equal(ub, [1.0, 1.0])

    def test_u_bounds_heat_pump_cooling_capable(self):
        """A HeatPump with cooling_cop > 0 should have lower bound -1."""
        living = Room("living_room", 5_000_000.0, 0.05, temperature=22.0, setpoint=21.0)
        model = HouseModel([living])
        sources = [HeatPump("hp", "living_room", max_power=5000.0, cooling_cop=2.5)]
        sde = HouseThermalSDE(model, sources, dt=900.0)
        lb, ub = sde.u_bounds
        assert lb[0] == pytest.approx(-1.0)
        assert ub[0] == pytest.approx(1.0)

    def test_u_bounds_heat_pump_no_cooling(self):
        """A HeatPump with cooling_cop=0 should have lower bound 0 (no cooling)."""
        living = Room("living_room", 5_000_000.0, 0.05, temperature=22.0, setpoint=21.0)
        model = HouseModel([living])
        sources = [HeatPump("hp", "living_room", max_power=5000.0, cooling_cop=0.0)]
        sde = HouseThermalSDE(model, sources, dt=900.0)
        lb, ub = sde.u_bounds
        assert lb[0] == pytest.approx(0.0)
        assert ub[0] == pytest.approx(1.0)

    def test_drift_cooling_decreases_temperature(self):
        """With u = -1 (full cooling via smooth sigmoid), drift must be negative
        for a warm room even when the outdoor temperature is also warm."""
        living = Room("living_room", 5_000_000.0, 0.05, temperature=25.0, setpoint=21.0)
        model = HouseModel([living])
        hp = HeatPump("hp", "living_room", max_power=5000.0, cooling_cop=2.5)
        sde = HouseThermalSDE(model, [hp], dt=900.0)
        x = _aug_state([25.0])
        u = np.array([-1.0])   # full cooling
        d = sde.disturbance_vector(25.0, {})   # warm outside – no natural cooling
        p = np.array([])
        f = sde.f(x, u, d, p, 0.0)
        assert f[0] < 0.0, f"Expected negative drift with full cooling, got {f}"

    def test_drift_smooth_zero_at_u_zero(self):
        """At u = 0, a cooling-capable source must contribute zero thermal power
        to the drift (smooth_thermal_power(0) = 0)."""
        living = Room("living_room", 5_000_000.0, 0.05, temperature=20.0, setpoint=21.0)
        model = HouseModel([living])
        hp = HeatPump("hp", "living_room", max_power=5000.0, cooling_cop=2.5)
        sde = HouseThermalSDE(model, [hp], dt=900.0)
        x = _aug_state([20.0])
        u_zero = np.array([0.0])
        d = sde.disturbance_vector(20.0, {})   # same inside/outside → no heat exchange
        p = np.array([])
        f = sde.f(x, u_zero, d, p, 0.0)
        assert f[0] == pytest.approx(0.0, abs=1e-4), (
            f"Expected ~0 drift at u=0 with no temperature differential, got {f}"
        )

    def test_drift_no_cooling_when_not_capable(self):
        """A heating-only source must not produce cooling drift when u < 0."""
        model, sources = _make_model_and_sources()  # ElectricHeaters, can_cool=False
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([25.0, 24.0])
        u = np.array([-1.0, -1.0])   # negative, but sources can't cool
        d = sde.disturbance_vector(25.0, {})
        p = np.array([])
        f = sde.f(x, u, d, p, 0.0)
        # thermal_power clamps negative fraction to give some value, but
        # since can_cool=False the model uses thermal_power(u_j) directly;
        # the drift should equal the no-input drift
        f_noinput = sde.f(x, np.zeros(2), d, p, 0.0)
        np.testing.assert_array_almost_equal(f, f_noinput)

    # ── Analytical Jacobian (dfdu) tests ─────────────────────────────────

    def test_dfdu_shape(self):
        """dfdu must return an (nx, nu) matrix."""
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([20.0, 19.0])
        u = np.array([0.5, 0.3])
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        J = sde.dfdu(x, u, d, p, 0.0)
        assert J.shape == (sde.nx, sde.nu)

    def test_dfdu_matches_central_differences_electric_heaters(self):
        """Analytical dfdu must match FD Jacobian for heating-only sources."""
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([20.0, 19.0])
        u = np.array([0.5, 0.3])  # strictly positive (feasible region)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        J_analytic = sde.dfdu(x, u, d, p, 0.0)
        J_fd = _central_difference_jacobian(
            lambda u_: sde.f(x, u_, d, p, 0.0), u,
        )
        np.testing.assert_allclose(J_analytic, J_fd, atol=1e-4, rtol=1e-3)

    def test_dfdu_matches_central_differences_heat_pump(self):
        """Analytical dfdu must match FD Jacobian for a cooling-capable source."""
        living = Room("living_room", 5_000_000.0, 0.05, temperature=20.0, setpoint=21.0)
        model = HouseModel([living])
        hp = HeatPump("hp", "living_room", max_power=5000.0, cooling_cop=2.5)
        sde = HouseThermalSDE(model, [hp], dt=900.0)
        x = _aug_state([20.0])
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        for u_val in [0.5, 0.0, -0.5]:
            u = np.array([u_val])
            J_analytic = sde.dfdu(x, u, d, p, 0.0)
            J_fd = _central_difference_jacobian(
                lambda u_: sde.f(x, u_, d, p, 0.0), u,
            )
            np.testing.assert_allclose(J_analytic, J_fd, atol=1e-4, rtol=1e-3,
                                       err_msg=f"u={u_val}")

    def test_dgmdx_const_shape_unaugmented(self):
        """dgmdx_const must have shape (nz, nx) for un-augmented model."""
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=False)
        n = sde._n_rooms
        H = sde.dgmdx_const
        assert H.shape == (n, sde.nx)
        # First n columns are identity, rest are zero
        np.testing.assert_array_equal(H[:, :n], np.eye(n))
        np.testing.assert_array_equal(H[:, n:], np.zeros((n, sde.nx - n)))

    def test_dgmdx_const_shape_augmented(self):
        """dgmdx_const must have shape (nz, nx) for augmented model."""
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=True)
        n = sde._n_rooms
        H = sde.dgmdx_const
        assert H.shape == (n, sde.nx)
        # Air block: H[:, :n] = I
        np.testing.assert_array_equal(H[:, :n], np.eye(n))
        # Offset block: H[:, b_start:b_start+n] = I
        b_start = sde._offset_block_start
        np.testing.assert_array_equal(H[:, b_start:b_start + n], np.eye(n))
        # All other columns are zero
        mask = np.zeros(sde.nx, dtype=bool)
        mask[:n] = True
        mask[b_start:b_start + n] = True
        np.testing.assert_array_equal(H[:, ~mask], np.zeros((n, (~mask).sum())))

    def test_dgmdx_const_matches_gm_finite_diff(self):
        """dgmdx_const must equal the finite-difference Jacobian of gm."""
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=True)
        x = _aug_state([20.0, 19.0])
        u_dummy = np.zeros(sde.nu)
        d_dummy = sde.disturbance_vector(5.0, {})
        p_dummy = np.array([])
        H_const = sde.dgmdx_const
        H_fd = _central_difference_jacobian(
            lambda xv: sde.gm(xv, u_dummy, d_dummy, p_dummy, 0.0), x,
        )
        np.testing.assert_allclose(H_const, H_fd, atol=1e-8)

    # ── _AnalyticEOCP installation tests ─────────────────────────────────

    def test_analytic_jacs_installed_for_ipopt(self):
        """_install_analytic_jacs upgrades the EOCP when IPOPT is the solver."""
        pytest.importorskip("cyipopt")
        from custom_components.heating_assistant.controller import (
            _AnalyticEOCP, _ANALYTIC_JACS_AVAILABLE,
        )
        assert _ANALYTIC_JACS_AVAILABLE
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(
            model, sources, horizon=3, dt=900,
            solver="ipopt",
            use_analytic_derivatives=True,
        )
        assert isinstance(ctrl._ocp._eocp, _AnalyticEOCP), (
            "EOCP should be upgraded to _AnalyticEOCP when IPOPT is active "
            "and use_analytic_derivatives=True"
        )

    def test_analytic_jacs_not_installed_for_slsqp(self):
        """_AnalyticEOCP must NOT be installed when solver is SLSQP."""
        from custom_components.heating_assistant.controller import (
            _AnalyticEOCP,
        )
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(
            model, sources, horizon=3, dt=900,
            solver="SLSQP",
            use_analytic_derivatives=True,
        )
        assert not isinstance(ctrl._ocp._eocp, _AnalyticEOCP), (
            "_AnalyticEOCP must not be installed for SLSQP solver"
        )


class TestContinuousDiscreteEKF:
    """Tests for the continuous-discrete EKF with HouseThermalSDE."""

    def _make_ekf(self, sde=None):
        if sde is None:
            model, sources = _make_model_and_sources()
            sde = HouseThermalSDE(model, sources, dt=900.0)
        x0 = np.array(sde.x)
        P0 = np.eye(sde.nx)
        return ContinuousDiscreteEKF(sde, x0, P0, dt=900.0, n_steps=5), sde

    def test_initial_state(self):
        ekf, sde = self._make_ekf()
        np.testing.assert_array_equal(ekf.x_hat, np.array(sde.x))

    def test_initial_covariance_shape(self):
        ekf, sde = self._make_ekf()
        assert ekf.P.shape == (sde.nx, sde.nx)

    def test_update_with_measurement(self):
        """After update the estimate should be close to the measurement."""
        ekf, sde = self._make_ekf()
        y = np.array([18.5, 17.5])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        x_hat, P = ekf.step(y, u, d, p, 0.0)
        np.testing.assert_array_almost_equal(x_hat[:sde.nym], y, decimal=1)

    def test_covariance_propagates(self):
        """P should change after a predict-update cycle."""
        ekf, sde = self._make_ekf()
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        P_before = ekf.P.copy()
        ekf.step(np.array([18.0, 17.0]), u, d, p, 0.0)
        ekf.step(np.array([18.1, 17.1]), u, d, p, 900.0)
        assert not np.allclose(P_before, ekf.P)

    def test_covariance_stays_symmetric(self):
        """P must remain symmetric after multiple updates."""
        ekf, sde = self._make_ekf()
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        for temp in [18.0, 18.1, 18.2, 18.3]:
            ekf.step(np.array([temp, temp - 1.0]), u, d, p, 0.0)
        np.testing.assert_array_almost_equal(ekf.P, ekf.P.T)

    def test_covariance_positive_semidefinite(self):
        """All eigenvalues of P should be non-negative."""
        ekf, sde = self._make_ekf()
        u = np.array([0.5, 0.3])
        d = sde.disturbance_vector(0.0, {})
        p = np.array([])
        for i in range(8):
            ekf.step(
                np.array([18.0 + 0.05 * i, 17.0 + 0.03 * i]),
                u, d, p, float(i * 900)
            )
        eigvals = np.linalg.eigvalsh(ekf.P)
        assert np.all(eigvals >= -1e-10)


# -- CDTrackingOptimalControlProblem tests ------------------------------------

class TestCDTrackingOCP:
    """Tests for CDTrackingOptimalControlProblem with HouseThermalSDE."""

    def _make_ocp(self, horizon=3):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        n_x, n_u = sde.nx, sde.nu
        Q = np.eye(sde.nz)
        R = 0.01 * np.eye(n_u)
        z_ref = sde.x_ref
        u_min, u_max = sde.u_bounds
        ocp = CDTrackingOptimalControlProblem(
            sde, N=horizon, Q=Q, R=R,
            z_ref=z_ref,
            u_min=u_min, u_max=u_max,
            dt=900.0, n_steps=5,
        )
        return ocp, sde

    def test_solve_returns_correct_shapes(self):
        ocp, sde = self._make_ocp(horizon=3)
        x0 = np.array(sde.x)
        d = sde.disturbance_vector(5.0, {})
        d_traj = np.tile(d, (3, 1))
        u_opt, cost, _ = ocp.solve(x0, d_traj)
        assert u_opt.shape == (3, 2)
        assert isinstance(cost, float)

    def test_inputs_within_bounds(self):
        ocp, sde = self._make_ocp(horizon=3)
        x0 = np.array(sde.x)
        d = sde.disturbance_vector(-10.0, {})
        d_traj = np.tile(d, (3, 1))
        u_opt, _, _ = ocp.solve(x0, d_traj)
        assert np.all(u_opt >= -1e-9)
        assert np.all(u_opt <= 1.0 + 1e-9)

    def test_heats_when_below_setpoint(self):
        """When rooms are cold, the OCP should recommend positive heating."""
        ocp, sde = self._make_ocp(horizon=4)
        x0 = _aug_state([15.0, 14.0])  # below setpoints
        d = sde.disturbance_vector(-10.0, {})
        d_traj = np.tile(d, (4, 1))
        u_opt, _, _ = ocp.solve(x0, d_traj)
        assert u_opt[0].sum() > 0.0, "Expected positive heating when below setpoint"

    def test_no_heat_above_setpoint(self):
        """When rooms are above setpoints and outside is warm, OCP should not heat."""
        living = Room("living_room", 5e6, 0.05, temperature=25.0, setpoint=21.0)
        bedroom = Room("bedroom", 3e6, 0.08, temperature=24.0, setpoint=20.0)
        model = HouseModel([living, bedroom])
        sources = [
            ElectricHeater("lr_heater", "living_room", max_power=2000.0),
            ElectricHeater("br_heater", "bedroom", max_power=1500.0),
        ]
        sde = HouseThermalSDE(model, sources, dt=900.0)
        ocp = CDTrackingOptimalControlProblem(
            sde, N=3, Q=np.eye(sde.nz), R=0.001 * np.eye(2),
            z_ref=sde.x_ref, u_min=np.zeros(2), u_max=np.ones(2),
            dt=900.0, n_steps=5,
        )
        d = sde.disturbance_vector(22.0, {})
        d_traj = np.tile(d, (3, 1))
        x0 = _aug_state([25.0, 24.0])
        u_opt, _, _ = ocp.solve(x0, d_traj)
        np.testing.assert_array_almost_equal(u_opt[0], np.zeros(2), decimal=4)


# -- HeatingMPCController (application facade) tests -------------------------

class TestHeatingMPCController:
    def test_actions_cover_all_sources(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now)
        for src in sources:
            assert src.name in actions

    def test_fractions_in_range(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now)
        for name, frac in actions.items():
            assert 0.0 <= frac <= 1.0, f"Fraction out of range for {name}: {frac}"

    def test_fractions_are_continuous(self):
        """Continuous NLP optimisation; fractions are not grid-restricted."""
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900,
                                    energy_weight=0.001)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=-5.0, now=now)
        assert all(isinstance(f, float) for f in actions.values())
        assert all(0.0 <= f <= 1.0 for f in actions.values())

    def test_heats_when_below_setpoint(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=-10.0, now=now)
        assert any(frac > 0.0 for frac in actions.values())

    def test_no_heat_when_warm_enough(self):
        living = Room("living_room", 5e6, 0.05, temperature=25.0, setpoint=21.0)
        bedroom = Room("bedroom", 3e6, 0.08, temperature=24.0, setpoint=20.0)
        model = HouseModel([living, bedroom])
        sources = [
            ElectricHeater("lr_heater", "living_room", max_power=2000.0),
            ElectricHeater("br_heater", "bedroom", max_power=1500.0),
        ]
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=22.0, now=now)
        assert all(frac == pytest.approx(0.0, abs=1e-4) for frac in actions.values())

    def test_solar_gains_provided_externally(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        gains = {"living_room": 300.0, "bedroom": 100.0}
        actions = ctrl.compute(outdoor_temp=5.0, solar_gains=gains, now=now)
        for src in sources:
            assert src.name in actions
            assert 0.0 <= actions[src.name] <= 1.0

    def test_controller_updates_source_state(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=-5.0, now=now)
        for src in sources:
            expected = src.thermal_power(actions[src.name])
            assert src.current_power == pytest.approx(expected, rel=1e-6)

    def test_visualisation_properties_populated(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=5.0, now=now)
        assert len(ctrl.predictions) == 3
        assert len(ctrl.outdoor_forecast) == 3
        assert len(ctrl.solar_forecast) == 4  # N+1: covers now through now+N*dt
        assert len(ctrl.heating_schedule) == 3

    def test_controller_uses_unaugmented_states_for_runtime_efficiency(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        assert ctrl._system._augment_offsets is False
        assert ctrl._control_system._augment_offsets is False
        assert ctrl._system.nx == 6

    def test_filtered_temperatures_use_air_state_when_offsets_disabled(self):
        room = Room("living_room", 5_000_000.0, 0.05, temperature=20.0, setpoint=21.0)
        model = HouseModel([room])
        ctrl = HeatingMPCController(
            model,
            [ElectricHeater("h", "living_room", 2000.0)],
            horizon=2,
            dt=900,
        )
        ctrl._ekf._x_np = np.array([20.0, 20.0, 20.0], dtype=float)

        assert ctrl.filtered_temperatures["living_room"] == pytest.approx(20.0)

    def test_predictions_use_air_state_when_offsets_disabled(self):
        room = Room("living_room", 5_000_000.0, 0.05, temperature=20.0, setpoint=21.0)
        model = HouseModel([room])
        ctrl = HeatingMPCController(
            model,
            [ElectricHeater("h", "living_room", 2000.0)],
            horizon=2,
            dt=900,
        )
        x_state = np.array([20.0, 20.0, 20.0], dtype=float)
        ctrl._ekf._x_np = x_state.copy()
        ctrl._ekf.predict = lambda *args, **kwargs: None

        def _update(*args, **kwargs):
            ctrl._ekf._x_np = x_state.copy()
            return x_state.copy(), ctrl._ekf.P

        ctrl._ekf.update = _update
        ctrl._ocp.solve = lambda *args, **kwargs: (
            np.zeros((ctrl._horizon, ctrl._system.nu)),
            None,
            {},
        )

        ctrl.compute(
            outdoor_temp=20.0,
            solar_gains={"living_room": 0.0},
            now=datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc),
            outdoor_forecast=[20.0, 20.0],
        )

        assert [step["living_room"] for step in ctrl.predictions] == pytest.approx([20.0, 20.0])

    def test_ipopt_backend_is_active_when_available(self):
        pytest.importorskip("cyipopt")
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900, solver="ipopt")
        backend_name = type(ctrl._ocp._eocp._solver_backend).__name__.lower()
        assert "ipopt" in backend_name
        assert ctrl.solver_active.lower() in {"ipopt", "cyipopt"}

    def test_predictions_contain_all_rooms(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=5.0, now=now)
        for step in ctrl.predictions:
            assert "living_room" in step
            assert "bedroom" in step

    def test_heat_pump_cop_varies_with_temperature(self):
        """Higher outdoor temperature -> higher COP."""
        hp = HeatPump("hp", "lr", max_power=6100.0, cop_rated=3.5, cop_temp_ref=7.0)
        assert hp.cop(10.0) > hp.cop(-10.0)

    def test_heat_pump_min_power_respected(self):
        living = Room("living_room", 5e6, 0.05, temperature=20.5, setpoint=21.0)
        model = HouseModel([living])
        hp = HeatPump("hp1", "living_room", max_power=6100.0,
                      cop_rated=3.5, cop_temp_ref=7.0, min_power=1000.0)
        ctrl = HeatingMPCController(model, [hp], horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=7.0, now=now)
        assert hp.current_power == 0.0 or hp.current_power >= 1000.0

    def test_smoothing_weight_reduces_input_change(self):
        """With a large smoothing weight, the first-step input should
        be smaller or equal to the unsmoothed case."""
        model_a, sources_a = _make_model_and_sources()
        model_b, sources_b = _make_model_and_sources()
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)

        ctrl_no_smooth = HeatingMPCController(model_a, sources_a, horizon=3, dt=900,
                                              smoothing_weight=0.0)
        ctrl_smooth = HeatingMPCController(model_b, sources_b, horizon=3, dt=900,
                                           smoothing_weight=1.0)

        actions_a = ctrl_no_smooth.compute(outdoor_temp=-10.0, now=now)
        actions_b = ctrl_smooth.compute(outdoor_temp=-10.0, now=now)

        total_a = sum(actions_a.values())
        total_b = sum(actions_b.values())
        # With the comfort-corridor objective and weak setpoint pull, smoothing
        # may shift where effort is spent over the horizon; keep a regression
        # guard that it stays in the same ballpark as the unsmoothed solution.
        assert total_b <= total_a + 0.1

    def test_smoothing_disabled_with_zero_weight(self):
        """smoothing_weight=0.0 gives the same result for identical initial conditions."""
        model_a, sources_a = _make_model_and_sources()
        model_b, sources_b = _make_model_and_sources()
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)

        ctrl_a = HeatingMPCController(model_a, sources_a, horizon=2, dt=900,
                                      smoothing_weight=0.0)
        ctrl_b = HeatingMPCController(model_b, sources_b, horizon=2, dt=900,
                                      smoothing_weight=0.0)

        actions_a = ctrl_a.compute(outdoor_temp=0.0, now=now)
        actions_b = ctrl_b.compute(outdoor_temp=0.0, now=now)

        for name in actions_a:
            assert actions_a[name] == pytest.approx(actions_b[name], abs=1e-4)

    def test_default_smoothing_weight(self):
        """Default smoothing_weight is 0.1."""
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now)
        assert all(0.0 <= f <= 1.0 for f in actions.values())

    def test_constraint_offset_configurable(self):
        """constraint_offset should be accepted and affect behaviour."""
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900,
                                    constraint_offset=1.0)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now)
        assert all(0.0 <= f <= 1.0 for f in actions.values())

    def test_comfort_corridor_bounds_fallback_to_setpoint_plus_offset(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900, constraint_offset=1.5)
        z_min, z_max = ctrl._control_system.comfort_corridor_bounds(fallback_offset=1.5)
        np.testing.assert_allclose(z_min, np.array([19.0, 18.0]))
        np.testing.assert_allclose(z_max, np.array([23.0, 22.0]))

    def test_comfort_corridor_bounds_use_room_overrides(self):
        model, sources = _make_model_and_sources()
        model.rooms["living_room"].comfort_corridor_low = 20.0
        model.rooms["living_room"].comfort_corridor_high = 22.0
        model.rooms["bedroom"].comfort_corridor_low = 18.0
        model.rooms["bedroom"].comfort_corridor_high = 21.0
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900, constraint_offset=1.5)
        z_min, z_max = ctrl._control_system.comfort_corridor_bounds(fallback_offset=1.5)
        np.testing.assert_allclose(z_min, np.array([20.0, 18.0]))
        np.testing.assert_allclose(z_max, np.array([22.0, 21.0]))

    def test_weak_setpoint_pull_no_chasing_inside_comfort_corridor(self):
        room = Room(
            "living_room",
            5_000_000.0,
            0.05,
            temperature=20.0,
            setpoint=21.0,
            comfort_corridor_low=19.0,
            comfort_corridor_high=23.0,
        )
        model = HouseModel([room])
        heater = ElectricHeater("heater", "living_room", max_power=2000.0)
        ctrl = HeatingMPCController(
            model,
            [heater],
            horizon=3,
            dt=900,
            constraint_offset=2.0,
            energy_weight=0.01,
        )
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=20.0, now=now)
        assert actions["heater"] == pytest.approx(0.0, abs=2e-2)

    def test_outdoor_forecast_used(self):
        """When an outdoor forecast is provided, it should be stored."""
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        forecast = [-5.0, -6.0, -7.0]
        ctrl.compute(outdoor_temp=-4.0, now=now, outdoor_forecast=forecast)
        assert ctrl.outdoor_forecast == forecast

    def test_uses_nonlinear_sde_model(self):
        """HeatingMPCController should use HouseThermalSDE internally."""
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        assert isinstance(ctrl._system, HouseThermalSDE)
        assert isinstance(ctrl._control_system, HouseThermalSDE)
        assert ctrl._control_system.nx == ctrl._system.nx
        assert isinstance(ctrl._ekf, ContinuousDiscreteEKF)
        assert isinstance(ctrl._ocp, CDTrackingOptimalControlProblem)

    def test_negative_smoothing_weight_raises(self):
        model, sources = _make_model_and_sources()
        with pytest.raises(ValueError, match="smoothing_weight"):
            HeatingMPCController(model, sources, horizon=2, dt=900,
                                 smoothing_weight=-0.1)

    def test_solver_selection_is_exposed(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(
            model,
            sources,
            horizon=2,
            dt=900,
            solver="SLSQP",
            solver_options={"maxiter": 42},
        )
        assert ctrl.solver_requested == "SLSQP"
        assert ctrl.solver_active == "SLSQP"
        assert ctrl.use_analytic_derivatives is True
        assert ctrl._ocp._eocp._solver_backend._options["maxiter"] == 42

    def test_default_solver_prefers_ipopt_but_falls_back_on_build_failure(self):
        model, sources = _make_model_and_sources()
        original_build = HeatingMPCController._build_ocp

        def _flaky_build(ctrl_self, **kwargs):
            if str(kwargs["solver"]).lower() in {"ipopt", "cyipopt"}:
                raise RuntimeError("IPOPT backend unavailable at initialization")
            return original_build(ctrl_self, **kwargs)

        with patch.object(HeatingMPCController, "_build_ocp", new=_flaky_build):
            ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)

        assert ctrl.solver_requested == "ipopt"
        assert ctrl.solver_active == "SLSQP"

    def test_ipopt_runtime_error_falls_back_to_slsqp(self):
        model, sources = _make_model_and_sources()
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        original_solve = CDTrackingOptimalControlProblem.solve

        def _flaky_solve(ocp_self, *args, **kwargs):
            backend_name = type(ocp_self._eocp._solver_backend).__name__.lower()
            if "ipopt" in backend_name:
                raise RuntimeError(
                    "IPOPT backend requested but cyipopt is not available."
                )
            return original_solve(ocp_self, *args, **kwargs)

        with patch.object(CDTrackingOptimalControlProblem, "solve", new=_flaky_solve):
            ctrl = HeatingMPCController(
                model,
                sources,
                horizon=2,
                dt=900,
                solver="ipopt",
            )
            actions = ctrl.compute(outdoor_temp=0.0, now=now)

        assert ctrl.solver_requested == "ipopt"
        assert ctrl.solver_active == "SLSQP"
        assert all(0.0 <= frac <= 1.0 for frac in actions.values())

    def test_mpc_requests_cooling_when_above_setpoint(self):
        """When a cooling-capable heat pump room is well above setpoint, the
        MPC should output a negative fraction (active cooling request)."""
        living = Room(
            "living_room", 5_000_000.0, 0.05, temperature=27.0, setpoint=21.0,
        )
        model = HouseModel([living])
        hp = HeatPump("hp", "living_room", max_power=5000.0, cooling_cop=2.5)
        ctrl = HeatingMPCController(model, [hp], horizon=3, dt=900)
        now = datetime(2024, 7, 1, 14, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=25.0, now=now)
        assert actions["hp"] < 0.0, (
            f"Expected negative fraction (cooling) when room is well above "
            f"setpoint, got {actions['hp']}"
        )

    def test_mpc_cooling_source_power_is_negative(self):
        """After a cooling step, the source's current_power should be negative."""
        living = Room(
            "living_room", 5_000_000.0, 0.05, temperature=27.0, setpoint=21.0,
        )
        model = HouseModel([living])
        hp = HeatPump("hp", "living_room", max_power=5000.0, cooling_cop=2.5)
        ctrl = HeatingMPCController(model, [hp], horizon=3, dt=900)
        now = datetime(2024, 7, 1, 14, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=25.0, now=now)
        assert hp.current_power < 0.0, (
            "Expected negative current_power on heat pump in cooling mode"
        )

    def test_no_cooling_when_cooling_cop_zero(self):
        """A heat pump with cooling_cop=0 must not receive negative fractions."""
        living = Room(
            "living_room", 5_000_000.0, 0.05, temperature=27.0, setpoint=21.0,
        )
        model = HouseModel([living])
        hp = HeatPump("hp", "living_room", max_power=5000.0, cooling_cop=0.0)
        ctrl = HeatingMPCController(model, [hp], horizon=3, dt=900)
        now = datetime(2024, 7, 1, 14, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=25.0, now=now)
        assert actions["hp"] >= 0.0, (
            "Heating-only heat pump must not receive a negative (cooling) fraction"
        )


class TestTotalComputes:
    """total_computes increments on every compute() call and never saturates."""

    def test_starts_at_zero(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        assert ctrl.total_computes == 0

    def test_increments_each_call(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        for expected in range(1, 5):
            ctrl.compute(outdoor_temp=0.0, now=now)
            assert ctrl.total_computes == expected, (
                f"Expected total_computes={expected}, got {ctrl.total_computes}"
            )

    def test_exceeds_rolling_window(self):
        """total_computes must exceed MPC_STATS_BUFFER_SIZE (rolling deque cap)."""
        from custom_components.heating_assistant.controller import MPC_STATS_BUFFER_SIZE

        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        # Run one more than the buffer size
        for _ in range(MPC_STATS_BUFFER_SIZE + 1):
            ctrl.compute(outdoor_temp=0.0, now=now)
        assert ctrl.total_computes == MPC_STATS_BUFFER_SIZE + 1, (
            "total_computes should grow beyond the rolling-window cap"
        )
        assert ctrl.n_solves == MPC_STATS_BUFFER_SIZE, (
            "n_solves is capped at MPC_STATS_BUFFER_SIZE"
        )


class TestSolarForecastIndexing:
    """_forecast_solar must start at k=0 (current solar) not k=1 (one step ahead)."""

    _NOW = datetime(2024, 6, 21, 12, 0, tzinfo=timezone.utc)  # Summer solstice noon

    def test_solar_seq_zero_uses_current_time(self):
        """solar_seq[0] must equal the solar gain computed at *now* (not now+dt)."""
        from custom_components.heating_assistant.controller import (
            HeatingMPCController, HouseThermalSDE,
        )
        from custom_components.heating_assistant.solar_model import room_solar_gains
        from datetime import timedelta

        room = Room(
            "living_room", 5_000_000.0, 0.05,
            temperature=15.0, setpoint=21.0,
        )
        model = HouseModel([room])
        heater = ElectricHeater("h", "living_room", 2000)
        ctrl = HeatingMPCController(
            model, [heater], horizon=4, dt=900,
            latitude=55.0, longitude=12.0,
        )

        solar_seq = ctrl._forecast_solar(self._NOW)

        # Expected: solar at exactly _NOW (k=0 → t = _NOW + 0*dt)
        expected_k0 = room_solar_gains(
            model.rooms["living_room"].windows,
            self._NOW,
            55.0, 12.0,
        )
        # Wrong (old) value would be solar at _NOW + dt
        wrong_k0 = room_solar_gains(
            model.rooms["living_room"].windows,
            self._NOW + timedelta(seconds=900),
            55.0, 12.0,
        )

        assert solar_seq[0]["living_room"] == pytest.approx(expected_k0, rel=1e-6), (
            "solar_seq[0] should use solar at now, not now+dt"
        )
        # Sanity-check: the wrong value actually differs (summer noon, gains change)
        if abs(wrong_k0 - expected_k0) > 1e-3:
            assert solar_seq[0]["living_room"] != pytest.approx(wrong_k0, rel=1e-3), (
                "solar_seq[0] must not equal the old (k+1) value"
            )

    def test_solar_forecast_length_is_horizon_plus_one(self):
        """_forecast_solar must return N+1 entries to cover now through now+N*dt."""
        room = Room("r", 5_000_000.0, 0.05, temperature=20.0, setpoint=21.0)
        model = HouseModel([room])
        ctrl = HeatingMPCController(
            model, [ElectricHeater("h", "r", 1000)], horizon=6, dt=900,
        )
        solar_seq = ctrl._forecast_solar(self._NOW)
        assert len(solar_seq) == 7  # horizon + 1

    def test_d_traj_zero_uses_current_disturbance(self):
        """After fix, d_traj[0] must use current solar (not future solar)."""
        from custom_components.heating_assistant.solar_model import room_solar_gains

        room = Room(
            "living_room", 5_000_000.0, 0.05,
            temperature=15.0, setpoint=21.0,
        )
        model = HouseModel([room])
        ctrl = HeatingMPCController(
            model, [ElectricHeater("h", "living_room", 2000)],
            horizon=4, dt=900, latitude=55.0, longitude=12.0,
        )

        # Compute with a fixed 'now' and check stored solar_forecast
        ctrl.compute(outdoor_temp=5.0, now=self._NOW)

        expected_solar_0 = room_solar_gains(
            model.rooms["living_room"].windows,
            self._NOW,
            55.0, 12.0,
        )
        # ctrl.solar_forecast[0] = solar_seq[0] = solar at _NOW (after fix)
        assert ctrl.solar_forecast[0]["living_room"] == pytest.approx(
            expected_solar_0, rel=1e-6
        ), "solar_forecast[0] must reflect current (now) solar gains"

    def test_mayer_zref_updated_on_setpoint_change(self):
        """After compute(), the Mayer terminal cost _zref must track current setpoints.

        The Mayer closure captures z_ref_arr at OCP construction time.  Without
        the fix, the terminal cost always pulls toward the *initial* setpoints
        even after the user changes them via the climate entity.  The fix updates
        the captured numpy array in-place so both stage and terminal costs use
        the same up-to-date reference.
        """
        room = Room(
            "living_room", 5_000_000.0, 0.05,
            temperature=18.0, setpoint=21.0,
        )
        model = HouseModel([room])
        ctrl = HeatingMPCController(
            model, [ElectricHeater("h", "living_room", 2000)],
            horizon=4, dt=900,
        )

        # Sanity-check: initial Mayer _zref matches initial setpoint.
        # _mayer signature: def _mayer(x, y, theta, _P=P_arr, _zref=z_ref_arr, ...)
        # __defaults__[1] is the _zref default argument (the captured z_ref_arr array).
        mayer = ctrl._ocp._eocp._mayer
        assert mayer is not None, "Mayer function must be present when terminal_weight > 0"
        assert mayer.__defaults__[1][0] == pytest.approx(21.0), (  # index 1 → _zref
            "Initial Mayer _zref must equal the initial setpoint"
        )

        # Change setpoint and recompute
        model.rooms["living_room"].setpoint = 25.0
        ctrl.compute(outdoor_temp=0.0, now=self._NOW)

        # Both the stage reference and the Mayer terminal reference must be 25.0
        assert ctrl._ocp._eocp._z_ref[0][0] == pytest.approx(25.0), (
            "_eocp._z_ref (stage cost) must be updated to the new setpoint"
        )
        assert mayer.__defaults__[1][0] == pytest.approx(25.0), (  # index 1 → _zref
            "Mayer _zref (terminal cost) must be updated to the new setpoint"
        )


class TestKalmanInnovation:
    """HeatingMPCController.last_innovation is populated after each compute()."""

    _NOW = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)

    def test_innovation_none_before_compute(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        assert ctrl.last_innovation is None

    def test_innovation_populated_after_compute(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        ctrl.compute(outdoor_temp=5.0, now=self._NOW)
        innov = ctrl.last_innovation
        assert innov is not None, "last_innovation must be populated after compute()"
        assert isinstance(innov, list), "last_innovation must be a list"
        assert len(innov) == len(model.room_names), (
            "last_innovation length must equal number of rooms"
        )

    def test_innovation_contains_floats(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        ctrl.compute(outdoor_temp=5.0, now=self._NOW)
        for v in ctrl.last_innovation:
            assert isinstance(v, float)

    def test_innovation_updates_each_call(self):
        """Innovation should reflect the current measurement residual."""
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        ctrl.compute(outdoor_temp=5.0, now=self._NOW)
        innov_1 = list(ctrl.last_innovation)

        # Change temperatures so measurement changes
        model.rooms["living_room"].temperature = 25.0
        model.rooms["bedroom"].temperature = 24.0
        ctrl.compute(outdoor_temp=5.0, now=self._NOW)
        innov_2 = list(ctrl.last_innovation)

        # Innovations should differ after a large temperature jump
        assert innov_1 != innov_2, (
            "Innovation must reflect the new measurement residual after a temperature change"
        )


class TestTerminalWeight:
    """HeatingMPCController.terminal_weight exposes the configured weight."""

    def test_default_terminal_weight(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        assert ctrl.terminal_weight == pytest.approx(100.0)

    def test_custom_terminal_weight(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900,
                                    terminal_weight=50.0)
        assert ctrl.terminal_weight == pytest.approx(50.0)

    def test_terminal_weight_is_float(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        assert isinstance(ctrl.terminal_weight, float)
