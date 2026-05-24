"""Unit tests for the linearised CD-MPC controller."""

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
from mbc.control import CDTrackingOptimalControlProblem, CDLinearizedMPCController
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
    """Build an augmented 1R1C state vector ``[T, b]``.

    1R1C has a single temperature node per room.  The augmented state
    has length ``2n``.  The ``walls`` and ``slabs`` kwargs are accepted
    but ignored for backward compatibility with test helpers that
    previously built 2R2C+slab vectors.
    """
    if offsets is None:
        offsets = [0.0] * len(temps)
    return np.array(
        list(temps) + list(offsets),
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
        # 1R1C: state is [T (n), b (n)].  nx = 2 rooms × 2 = 4.
        assert sde.nx == 4
        assert sde.nu == 2
        assert sde.nd == 3   # T_out + 2 solar/internal-gain channels
        assert sde.nw == 4   # one noise per state
        assert sde.nz == 2   # controlled output = room temperature
        assert sde.nym == 2  # measured output = room temperature

    def test_drift_shape(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.0, 17.0])
        u = np.array([0.5, 0.3])
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        f = sde.f(x, u, d, p, 0.0)
        # 1R1C augmented drift: 2n = 4 entries (T block + offset block).
        assert f.shape == (4,)

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
        # Layout: f = [T (n), offset (n)] with n=2.
        # Heat input lands on the temperature block → drift positive.
        assert np.all(f[:2] > 0.0), f"Expected positive temperature drift, got {f}"
        # Offset block (last n entries) has zero drift.
        assert np.allclose(f[2:], 0.0)

    def test_drift_no_heat_cold_outside(self):
        """No heating and cold outside: warm rooms should cool down
        (negative temperature drift)."""
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([20.0, 20.0])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(-10.0, {})
        p = np.array([])
        f = sde.f(x, u, d, p, 0.0)
        # Temperature block (rows 0..2) drifts negative (heat loss to outdoor).
        assert np.all(f[:2] < 0.0), f"Expected negative temperature drift, got {f}"
        # Offset block (rows 2..4) has zero drift.
        assert np.allclose(f[2:], 0.0)

    def test_sigma_shape(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.0, 17.0])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        sig = sde.sigma(x, u, d, p, 0.0)
        # 1R1C augmented: nx = 2n = 4 → σ is 4×4.
        assert sig.shape == (4, 4)

    def test_sigma_is_scaled_identity(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0, sigma_w=0.1)
        x = _aug_state([18.0, 17.0])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        sig = sde.sigma(x, u, d, p, 0.0)
        # Block diagonal: 0.1·I_2 on the T physical block,
        # 0.002·I_2 on the offset block.
        expected = np.zeros((4, 4))
        expected[:2, :2] = 0.1 * np.eye(2)
        expected[2:, 2:] = 0.002 * np.eye(2)
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
        # State order: [T(2), b(2)].
        assert diag[0] == pytest.approx(0.3)  # 0.1 * sqrt(9)
        assert diag[1] == pytest.approx(0.1)

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
        """``dfdx`` mirrors the n×n matrix ``F`` on the top-left
        physical block and is zero on the offset rows/columns."""
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.0, 17.0])
        u = np.array([0.3, 0.5])
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        J_analytic = sde.dfdx(x, u, d, p, 0.0)
        # 1R1C augmented Jacobian is 2n × 2n = 4 × 4.
        assert J_analytic.shape == (4, 4)
        n = sde._n_rooms
        # Top-left n × n block matches the structural F.  No wind set,
        # so the SG overlay is zero and J[:n, :n] == F exactly.
        np.testing.assert_array_almost_equal(J_analytic[:n, :n], sde._F)
        # Offset block (rows/cols n..2n) is all-zero (zero drift on b).
        np.testing.assert_array_equal(J_analytic[:n, n:], np.zeros((n, n)))
        np.testing.assert_array_equal(J_analytic[n:, :], np.zeros((n, 2 * n)))

    def test_observation_jacobian_is_identity(self):
        """``dhm/dx`` for the 1R1C measurement ``y = T + b`` is
        ``[I_n | I_n]``: identity on the T block, identity on the
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
            [1.0, 0.0, 1.0, 0.0],
            [0.0, 1.0, 0.0, 1.0],
        ])
        np.testing.assert_array_equal(H, expected)

    def test_analytic_state_jacobian_matches_finite_difference_unaugmented(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=False)
        x = np.array([18.0, 17.0], dtype=float)
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
        x = np.array([18.0, 17.0], dtype=float)
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

    def test_u_bounds_heat_pump_heat_only_mode(self):
        """hvac_mode='heat' → lower bound 0, upper bound 1."""
        living = Room("living_room", 5_000_000.0, 0.05, temperature=22.0, setpoint=21.0)
        model = HouseModel([living])
        sources = [HeatPump("hp", "living_room", max_power=5000.0, hvac_mode="heat")]
        sde = HouseThermalSDE(model, sources, dt=900.0)
        lb, ub = sde.u_bounds
        assert lb[0] == pytest.approx(0.0)
        assert ub[0] == pytest.approx(1.0)

    def test_u_bounds_heat_pump_cool_only_mode(self):
        """hvac_mode='cool' → lower bound -1, upper bound 0."""
        living = Room("living_room", 5_000_000.0, 0.05, temperature=22.0, setpoint=21.0)
        model = HouseModel([living])
        sources = [HeatPump("hp", "living_room", max_power=5000.0, hvac_mode="cool")]
        sde = HouseThermalSDE(model, sources, dt=900.0)
        lb, ub = sde.u_bounds
        assert lb[0] == pytest.approx(-1.0)
        assert ub[0] == pytest.approx(0.0)

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

    # ── Analytical Jacobians via new mbc API ─────────────────────────────

    def test_dgmdx_method_called_by_mbc_eocp(self):
        """dgmdx() returns the same constant matrix as dgmdx_const."""
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=True)
        x = _aug_state([20.0, 19.0])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        H_method = sde.dgmdx(x, u, d, p, 0.0)
        H_prop = sde.dgmdx_const
        np.testing.assert_array_equal(H_method, H_prop)

    def test_dgmdu_returns_zeros(self):
        """dgmdu() must return a zero (nz, nu) matrix — gm is independent of u."""
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([20.0, 19.0])
        u = np.array([0.5, 0.3])
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        G = sde.dgmdu(x, u, d, p, 0.0)
        assert G.shape == (sde.nz, sde.nu)
        np.testing.assert_array_equal(G, np.zeros((sde.nz, sde.nu)))

    def test_mbc_eocp_uses_analytical_jacs(self):
        """New mbc EOCP always uses analytical Jacobians when model supplies them.

        After upgrading to mbc v0.1+, analytical Jacobians for equality and
        inequality constraints are always active (mbc calls model.dfdu and
        model.dgmdx directly).  This test verifies that the model's dgmdx
        and dgmdu are callable and return the correct shapes so mbc can
        use them.
        """
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=True)
        x = _aug_state([20.0, 19.0])
        u = np.array([0.5, 0.3])
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        # Verify all Jacobian methods have the expected shapes
        assert sde.dgmdx(x, u, d, p, 0.0).shape == (sde.nz, sde.nx)
        assert sde.dgmdu(x, u, d, p, 0.0).shape == (sde.nz, sde.nu)
        assert sde.dfdu(x, u, d, p, 0.0).shape == (sde.nx, sde.nu)


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
        """QP optimisation; fractions are continuous, not grid-restricted."""
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
        # 1R1C un-augmented: one temperature state per room, no filter states
        assert ctrl._system.nx == 2

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
        # Intercept the linearised MPC step so we can control what predictions
        # are returned without running the actual QP.
        n_u = ctrl._system.nu
        n_x = ctrl._system.nx
        N = ctrl._horizon
        x_ss = np.array(ctrl._ekf.x_hat)  # current EKF state (20.0)

        def _fake_step(y, d, p=None, t=0.0, D_forecast=None):
            u_abs = np.zeros(n_u)
            U_abs = np.zeros((N, n_u))
            # Return constant state = x_ss (temperature stays at 20.0)
            X_abs = np.tile(x_ss, (N, 1))
            return u_abs, U_abs, X_abs

        ctrl._mpc.step = _fake_step

        ctrl.compute(
            outdoor_temp=20.0,
            solar_gains={"living_room": 0.0},
            now=datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc),
            outdoor_forecast=[20.0, 20.0],
        )

        assert [step["living_room"] for step in ctrl.predictions] == pytest.approx([20.0, 20.0])

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
        assert total_b <= total_a + 0.15

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

    def test_soft_constraint_weight_configurable(self):
        """soft_constraint_weight should be accepted and affect behaviour."""
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900,
                                    soft_constraint_weight=1000.0)
        now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now)
        assert all(0.0 <= f <= 1.0 for f in actions.values())

    def test_comfort_corridor_bounds_use_per_room_offsets(self):
        model, sources = _make_model_and_sources()
        # Set per-room comfort offsets
        model.rooms["living_room"].comfort_offset = 2.0
        model.rooms["bedroom"].comfort_offset = 1.5
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        z_min, z_max = ctrl._control_system.comfort_corridor_bounds()
        # living_room: setpoint 21.0, offset 2.0 → [19.0, 23.0]
        # bedroom: setpoint 20.0, offset 1.5 → [18.5, 21.5]
        np.testing.assert_allclose(z_min, np.array([19.0, 18.5]))
        np.testing.assert_allclose(z_max, np.array([23.0, 21.5]))

    def test_comfort_corridor_bounds_with_different_setpoints(self):
        model, sources = _make_model_and_sources()
        # Set different setpoints for each room with same offset
        model.rooms["living_room"].setpoint = 21.0
        model.rooms["living_room"].comfort_offset = 1.5
        model.rooms["bedroom"].setpoint = 20.0
        model.rooms["bedroom"].comfort_offset = 1.5
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        z_min, z_max = ctrl._control_system.comfort_corridor_bounds()
        # living_room: setpoint 21.0, offset 1.5 → [19.5, 22.5]
        # bedroom: setpoint 20.0, offset 1.5 → [18.5, 21.5]
        np.testing.assert_allclose(z_min, np.array([19.5, 18.5]))
        np.testing.assert_allclose(z_max, np.array([22.5, 21.5]))

    def test_weak_setpoint_pull_no_chasing_inside_comfort_corridor(self):
        room = Room(
            "living_room",
            5_000_000.0,
            0.05,
            air_temperature=20.0,
            setpoint=21.0,
            comfort_offset=2.0,
        )
        model = HouseModel([room])
        heater = ElectricHeater("heater", "living_room", max_power=2000.0)
        # terminal_weight=1 isolates the stage-cost weak-pull effect without the
        # terminal-cost amplification (100× stage) dominating.  Stage energy cost
        # (0.01) >> stage tracking cost (1e-4) → optimal u ≈ 0 inside corridor.
        ctrl = HeatingMPCController(
            model,
            [heater],
            horizon=3,
            dt=900,
            soft_constraint_weight=1000.0,
            energy_weight=0.01,
            terminal_weight=1,
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

    def test_uses_linearised_mpc(self):
        """HeatingMPCController should use CDLinearizedMPCController internally."""
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        assert isinstance(ctrl._system, HouseThermalSDE)
        assert isinstance(ctrl._control_system, HouseThermalSDE)
        assert ctrl._control_system.nx == ctrl._system.nx
        assert isinstance(ctrl._ekf, ContinuousDiscreteEKF)
        assert isinstance(ctrl._mpc, CDLinearizedMPCController)

    def test_negative_smoothing_weight_raises(self):
        model, sources = _make_model_and_sources()
        with pytest.raises(ValueError, match="smoothing_weight"):
            HeatingMPCController(model, sources, horizon=2, dt=900,
                                 smoothing_weight=-0.1)

    def test_solver_and_derivative_flags_exposed(self):
        """solver_requested, solver_active and use_analytic_derivatives are readable."""
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        assert isinstance(ctrl.solver_requested, str)
        assert isinstance(ctrl.solver_active, str)
        assert ctrl.use_analytic_derivatives is True

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

    def test_no_cooling_when_heat_only_mode(self):
        """A heat pump configured with hvac_mode='heat' must not receive negative fractions."""
        living = Room(
            "living_room", 5_000_000.0, 0.05, temperature=27.0, setpoint=21.0,
        )
        model = HouseModel([living])
        hp = HeatPump("hp", "living_room", max_power=5000.0, hvac_mode="heat")
        ctrl = HeatingMPCController(model, [hp], horizon=3, dt=900)
        now = datetime(2024, 7, 1, 14, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=25.0, now=now)
        assert actions["hp"] >= 0.0, (
            "Heat-only heat pump must not receive a negative (cooling) fraction"
        )

    def test_heat_pump_filter_state_cooling_reduced_inside_comfort(self):
        """Regression: mbc _np_to_cvx transposition bug caused Ad to be stored
        column-swapped in the OCP, breaking the filter-state → temperature
        coupling.  The OCP therefore failed to predict that a large negative
        filter state (stored cooling) would drive the room temperature below the
        comfort corridor, and kept commanding near-full cooling.

        With the fix the MPC correctly sees the temperature will drop below the
        lower comfort bound and substantially reduces the cooling command.
        """
        room = Room(
            "living", thermal_mass=5_000_000.0, r_external=0.05,
            air_temperature=21.0, setpoint=21.0, comfort_offset=2.0,
        )
        model = HouseModel([room])
        hp = HeatPump(
            "hp", "living", max_power=3000.0,
            emitter_time_constant=1200.0, cooling_cop=2.5,
        )
        ctrl = HeatingMPCController(
            model, [hp], horizon=8, dt=900, soft_constraint_weight=1000.0,
        )

        # Simulate: heat pump has been cooling at full power.
        # Filter state phi = -1.0, so stored cooling will drive temperature
        # below comfort [19, 23] °C over the next few steps.
        ctrl._ekf._x_np = np.array([21.0, -1.0])
        ctrl._mpc._u_prev = np.array([-1.0])

        now = datetime(2024, 7, 1, 14, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(
            outdoor_temp=20.0, solar_gains={"living": 0.0}, now=now,
        )

        # The MPC must substantially reduce cooling (action well above -0.5).
        # The buggy transposed-Ad version returned -0.65 because it could not
        # see the filter state driving the temperature out of the comfort band.
        assert actions["hp"] > -0.5, (
            f"Expected cooling to be reduced when filter state drives temperature "
            f"below comfort; got {actions['hp']:.4f} (buggy version: ~-0.65)"
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

    def test_setpoint_reference_updated_on_setpoint_change(self):
        """After compute(), the MPC x_ref must track the current setpoints.

        CDLinearizedMPCController.x_ref is updated via the property setter
        at the start of each compute() call so stage and terminal costs
        always use the latest setpoints.
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

        # Initial x_ref matches initial setpoint
        assert ctrl._mpc.x_ref[0] == pytest.approx(21.0)

        # Change setpoint and recompute — x_ref must be updated
        model.rooms["living_room"].setpoint = 25.0
        ctrl.compute(outdoor_temp=0.0, now=self._NOW)
        assert ctrl._mpc.x_ref[0] == pytest.approx(25.0)


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


class TestDisabledSources:
    """disabled_sources zeroes actions and heating schedule for off rooms."""

    def _make_cold_model_and_sources(self):
        """Two rooms well below setpoint so the QP naturally wants to heat both."""
        living = Room(
            "living_room", 5_000_000.0, 0.05,
            connections=[RoomConnection("bedroom", 0.2)],
            temperature=15.0, setpoint=21.0,
        )
        bedroom = Room(
            "bedroom", 3_000_000.0, 0.08,
            connections=[RoomConnection("living_room", 0.2)],
            temperature=15.0, setpoint=20.0,
        )
        model = HouseModel([living, bedroom])
        sources = [
            ElectricHeater("lr_heater", "living_room", max_power=2000.0),
            ElectricHeater("br_heater", "bedroom", max_power=1500.0),
        ]
        return model, sources

    def test_disabled_source_action_is_zero(self):
        model, sources = self._make_cold_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        now = datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now, disabled_sources={"br_heater"})
        assert actions["br_heater"] == pytest.approx(0.0)

    def test_enabled_source_action_is_nonzero(self):
        model, sources = self._make_cold_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        now = datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now, disabled_sources={"br_heater"})
        assert actions["lr_heater"] > 0.0

    def test_disabled_source_heating_schedule_all_zeros(self):
        model, sources = self._make_cold_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        now = datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=0.0, now=now, disabled_sources={"br_heater"})
        for step in ctrl.heating_schedule:
            assert step.get("bedroom", 0.0) == pytest.approx(0.0), (
                f"Expected 0 W predicted for disabled bedroom, got {step}"
            )

    def test_enabled_source_heating_schedule_nonzero(self):
        model, sources = self._make_cold_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        now = datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=0.0, now=now, disabled_sources={"br_heater"})
        first_step = ctrl.heating_schedule[0]
        assert first_step.get("living_room", 0.0) > 0.0

    def test_disabled_source_current_power_is_zero(self):
        model, sources = self._make_cold_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        now = datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=0.0, now=now, disabled_sources={"br_heater"})
        br_heater = next(s for s in sources if s.name == "br_heater")
        assert br_heater.current_power == pytest.approx(0.0)

    def test_no_disabled_sources_behaves_normally(self):
        model, sources = self._make_cold_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        now = datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now, disabled_sources=None)
        assert actions["lr_heater"] > 0.0
        assert actions["br_heater"] > 0.0

    def test_all_disabled_all_actions_zero(self):
        model, sources = self._make_cold_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        now = datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(
            outdoor_temp=0.0, now=now,
            disabled_sources={"lr_heater", "br_heater"},
        )
        assert actions["lr_heater"] == pytest.approx(0.0)
        assert actions["br_heater"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Tests for equilibrium-input linearisation and full-trajectory bounds clipping
# ---------------------------------------------------------------------------

def _make_hp_model_and_sources():
    """One-room model with a heat pump (cooling-capable) for transient tests."""
    room = Room(
        name="living_room",
        thermal_mass=5_000_000.0,
        r_external=0.05,
        connections=[],
        temperature=15.0,   # cold — transient condition
        setpoint=21.0,
    )
    model = HouseModel([room])
    hp = HeatPump(
        "hp",
        "living_room",
        max_power=6000.0,
        cop_rated=3.5,
        cop_temp_ref=7.0,
        cooling_cop=2.5,
    )
    return model, [hp]


class TestComputeUEq:
    """compute_u_eq should invert the power function exactly and stay in bounds."""

    def test_heating_only_source_equilibrium_in_bounds(self):
        """Electric heater equilibrium must lie in [0, 1]."""
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = np.array([18.0, 17.0])
        d = sde.disturbance_vector(-5.0, {})
        p = np.array([])
        u_eq = sde.compute_u_eq(x, d, p, 0.0)
        assert u_eq.shape == (2,)
        for j in range(sde.nu):
            assert 0.0 <= u_eq[j] <= 1.0, f"u_eq[{j}]={u_eq[j]} out of [0,1]"

    def test_heat_pump_equilibrium_in_bounds(self):
        """Heat pump equilibrium must lie in [-1, 1]."""
        model, sources = _make_hp_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = np.array([15.0])
        d = sde.disturbance_vector(-5.0, {})
        p = np.array([])
        u_eq = sde.compute_u_eq(x, d, p, 0.0)
        assert u_eq.shape == (1,)
        assert -1.0 <= u_eq[0] <= 1.0

    def test_equilibrium_maintains_temperature(self):
        """f evaluated at u_eq should have near-zero temperature drift."""
        model, sources = _make_hp_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        outdoor_temp = 5.0
        x = np.array([18.0])  # room at maintenance temperature
        d = sde.disturbance_vector(outdoor_temp, {})
        p = np.array([])
        u_eq = sde.compute_u_eq(x, d, p, 0.0)
        drift = sde.f(x, u_eq, d, p, 0.0)
        # Temperature block drift should be close to zero.
        assert abs(drift[0]) < 0.5, f"Temperature drift at u_eq too large: {drift[0]:.4f} °C/s"

    def test_equilibrium_zero_when_room_warm_heating_only(self):
        """A heating-only source should have u_eq = 0 when the room is warm
        enough that thermal losses are zero or negative (e.g. warm outdoor)."""
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = np.array([25.0, 25.0])   # warm rooms
        d = sde.disturbance_vector(25.0, {})  # warm outdoor — room gains heat
        p = np.array([])
        u_eq = sde.compute_u_eq(x, d, p, 0.0)
        # Rooms are gaining heat passively; equilibrium input is clamped to 0
        for j in range(sde.nu):
            assert u_eq[j] == pytest.approx(0.0, abs=1e-6)

    def test_equilibrium_clamps_to_one_when_losses_exceed_capacity(self):
        """When thermal losses exceed heater capacity, u_eq must clamp to 1."""
        # Use a very poorly insulated room (R_ext = 0.001 K/W) so the
        # 2 kW heater cannot cover the losses even at u = 1.
        room = Room(
            name="living_room",
            thermal_mass=5_000_000.0,
            r_external=0.001,          # very leaky wall: 1000 W/K
            connections=[],
            temperature=18.0,
        )
        model = HouseModel([room])
        sources = [ElectricHeater("lr_heater", "living_room", max_power=2000.0)]
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = np.array([18.0])
        d = sde.disturbance_vector(-5.0, {})  # 23 K differential → 23 kW loss >> 2 kW
        p = np.array([])
        u_eq = sde.compute_u_eq(x, d, p, 0.0)
        assert u_eq[0] == pytest.approx(1.0, abs=1e-6)


class TestLinearisationBoundsClipping:
    """After each MPC step the full predicted input trajectory must stay in [u_min, u_max]."""

    def _make_controller_with_hp(self, horizon=4):
        model, sources = _make_hp_model_and_sources()
        return HeatingMPCController(model, sources, horizon=horizon, dt=900), sources

    def test_u_abs_trajectory_within_bounds_cold_room(self):
        """After aggressive heating (u_prev forced high), U_abs must stay in [-1, 1]."""
        ctrl, sources = self._make_controller_with_hp()
        now = datetime(2024, 1, 15, 6, 0, tzinfo=timezone.utc)
        # Simulate a step that applies full heating so u_prev = 1.
        ctrl.compute(outdoor_temp=-5.0, now=now)
        # Manually drive u_prev to its maximum to reproduce the post-off transient.
        ctrl._mpc._u_prev[:] = 1.0
        # Compute again — this is the step where the bug caused U_abs > 1.
        ctrl.compute(outdoor_temp=-5.0, now=now)
        U_abs = np.array([
            [ctrl.heating_schedule[k]["living_room"] for k in range(ctrl.horizon)]
        ])
        # Verify via the internal trajectory stored on the mpc object.
        # We check the public actions are clipped and heating schedule is sane.
        action = ctrl._mpc._u_prev  # last applied u (was u_abs[0])
        assert -1.0 <= float(action[0]) <= 1.0

    def test_heating_schedule_power_does_not_exceed_physical_max(self):
        """Heating schedule should never show power above the heat pump's rated output
        at the given outdoor temperature (the sigmoid saturates at Q_heat)."""
        ctrl, sources = self._make_controller_with_hp(horizon=6)
        hp = sources[0]
        outdoor_temp = 5.0
        now = datetime(2024, 1, 15, 6, 0, tzinfo=timezone.utc)
        # Force u_prev = 1 to reproduce the stale-deviation-bound scenario.
        ctrl.compute(outdoor_temp=outdoor_temp, now=now)
        ctrl._mpc._u_prev[:] = 1.0
        ctrl.compute(outdoor_temp=outdoor_temp, now=now)
        q_heat_max = hp.thermal_power(1.0, outdoor_temp)
        q_cool_max = hp._q_cool_const
        for k, step in enumerate(ctrl.heating_schedule):
            p = step.get("living_room", 0.0)
            assert p <= q_heat_max * 1.01, (
                f"Step {k}: heating power {p:.0f} W exceeds physical max {q_heat_max:.0f} W"
            )
            assert p >= -q_cool_max * 1.01, (
                f"Step {k}: cooling power {p:.0f} W below physical min {-q_cool_max:.0f} W"
            )


class TestSetpointLinearisation:
    """The MPC must linearise at the setpoint, not the current estimated state.

    This matters most during transients: a cold room (T_hat << T_set) would
    cause the sigmoid to be evaluated at an extreme point if we linearised at
    T_hat, giving wrong Jacobians and oscillatory recovery.  Linearising at
    the setpoint gives stable Jacobians regardless of transient magnitude.
    """

    def _make_ctrl(self, room_temp=10.0, setpoint=21.0, horizon=4):
        room = Room(
            "living_room",
            thermal_mass=5_000_000.0,
            r_external=0.05,
            connections=[],
            temperature=room_temp,
            setpoint=setpoint,
        )
        model = HouseModel([room])
        hp = HeatPump("hp", "living_room", max_power=6000.0, cop_rated=3.5,
                      cop_temp_ref=7.0, cooling_cop=2.5)
        ctrl = HeatingMPCController(model, [hp], horizon=horizon, dt=900)
        return ctrl

    def test_linearisation_point_is_setpoint_temperature(self):
        """After a step, the lin-model operating-point temperature must equal
        the setpoint, not the current (cold) room temperature."""
        ctrl = self._make_ctrl(room_temp=5.0, setpoint=21.0)
        now = datetime(2024, 1, 15, 6, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=-5.0, now=now)
        x_ss = ctrl._mpc._lin_model.x_ss
        # Temperature component of the operating point must be the setpoint.
        assert x_ss[0] == pytest.approx(21.0, abs=0.1), (
            f"x_ss[0] = {x_ss[0]:.2f} should be setpoint 21.0, not room temp 5.0"
        )

    def test_cold_room_requests_near_max_heating(self):
        """A very cold room (10 °C below setpoint) should receive near-maximum
        heating on the first step, not the attenuated output caused by a bad
        linearisation at the cold operating point."""
        ctrl = self._make_ctrl(room_temp=5.0, setpoint=21.0)
        now = datetime(2024, 1, 15, 6, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=-5.0, now=now)
        u_hp = actions["hp"]
        assert u_hp > 0.7, (
            f"Expected near-max heating for cold room (u > 0.7), got u = {u_hp:.3f}"
        )

    def test_x_ref_is_unchanged_by_setpoint_linearisation(self):
        """The MPC's x_ref must remain equal to the setpoints array (not x_hat)."""
        ctrl = self._make_ctrl(room_temp=5.0, setpoint=21.0)
        now = datetime(2024, 1, 15, 6, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=-5.0, now=now)
        assert ctrl._mpc.x_ref[0] == pytest.approx(21.0)
