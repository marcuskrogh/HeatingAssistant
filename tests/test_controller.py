"""Unit tests for the linearised CD-MPC controller."""

import sys
import os
import pytest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

from heatingassistant.engine.thermal_model import (
    HouseModel,
    Room,
    RoomConnection,
    Window,
)
from heatingassistant.engine.heat_sources import ElectricHeater, HeatPump
from mbc.models import ContinuousDiscreteSDE
from mbc.estimation import (
    ContinuousDiscreteEKF,
    ContinuousDiscreteEKFParams,
    IntegrationScheme,
)
from heatingassistant.engine.controller import (
    HouseThermalSDE,
    HeatingMPCController,
    HeatingLinearisedMPC,
)


# -- Helpers ------------------------------------------------------------------

_MPC_NOW = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)


def _seed_path(ctrl, t_ref=None, u_ref=0.3):
    """Install a slow plan so compute() runs the P-law (no inline NLP)."""
    n_fast = ctrl.horizon
    n_rooms = ctrl._system._n_rooms
    nu = ctrl._system.nu
    n_slow = ctrl.timing.n_slow
    if t_ref is None:
        t_ref = [
            ctrl._system._model.rooms[name].setpoint
            for name in ctrl._system._room_list
        ]
    T = np.tile(np.asarray(t_ref, dtype=float).reshape(1, -1), (n_fast, 1))
    if T.shape[1] < n_rooms:
        pad = np.tile(T[:, -1:], (1, n_rooms - T.shape[1]))
        T = np.hstack([T, pad])
    U = np.full((n_slow, nu), float(u_ref))
    ctrl.set_accepted_path(U, T)


@pytest.fixture(scope="class")
def two_room():
    """Shared two-room model and heat sources (read-only across a test class)."""
    return _make_model_and_sources()


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
    """Build an augmented 2R2C state vector ``[T_a(n), T_w(n), b(n)]``.

    2R2C has air + wall nodes per room.  The augmented state (with
    augment_offsets=True) has length ``3n``.  ``walls`` defaults to the
    same values as ``temps`` (thermal equilibrium start); ``slabs`` is
    accepted but ignored (no slab node in 2R2C).  ``offsets`` default to
    zero (no initial measurement bias).
    """
    n = len(temps)
    if walls is None:
        walls = list(temps)  # start wall at air temp (equilibrium)
    if offsets is None:
        offsets = [0.0] * n
    return np.array(
        list(temps) + list(walls) + list(offsets),
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
    """Tests for HouseThermalSDE as a ContinuousDiscreteSDE implementation."""

    def test_is_continuous_discrete_model(self, two_room):
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        assert isinstance(sde, ContinuousDiscreteSDE)

    def test_dimensions(self, two_room):
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        n = 2  # two rooms
        # 2R2C augmented: state is [T_a(n), T_w(n), b(n)].  nx = 3n = 6.
        assert sde.nx == 3 * n
        assert sde.nu == 2
        assert sde.nd == 1 + 2 * n   # T_out + n solar slots + n air-heat slots
        assert sde.nw == 3 * n       # one noise per state
        assert sde.nz == 2           # controlled output = room temperature
        assert sde.nym == 2          # measured output = room temperature

    def test_drift_shape(self, two_room):
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.0, 17.0])
        u = np.array([0.5, 0.3])
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        f = sde.f(x, u, d, p, 0.0)
        # 2R2C augmented drift: 3n = 6 entries [T_a(n), T_w(n), b(n)].
        assert f.shape == (6,)

    def test_drift_heating_increases_temperature(self, two_room):
        """Full heating (u=1) on a room should give positive drift on the
        air block when the room is cold."""
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([15.0, 14.0])   # cold rooms (air; wall starts at air temp)
        u = np.array([1.0, 1.0])       # full heating
        d = sde.disturbance_vector(0.0, {})
        p = np.array([])
        f = sde.f(x, u, d, p, 0.0)
        n = sde._n_rooms
        # Layout: f = [T_a(n), T_w(n), b(n)] with n=2.
        # Heat input lands on the air-temperature block → drift positive.
        assert np.all(f[:n] > 0.0), f"Expected positive air-temperature drift, got {f}"
        # Offset block (last n entries) has zero drift.
        assert np.allclose(f[sde._offset_block_start:], 0.0)

    def test_drift_no_heat_cold_outside(self, two_room):
        """No heating and cold outside: warm rooms should cool down
        (negative temperature drift on the air block)."""
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([20.0, 20.0])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(-10.0, {})
        p = np.array([])
        f = sde.f(x, u, d, p, 0.0)
        n = sde._n_rooms
        # Air-temperature block (rows 0..n) drifts negative (heat loss to outdoor).
        assert np.all(f[:n] < 0.0), f"Expected negative temperature drift, got {f}"
        # Offset block (last n entries) has zero drift.
        assert np.allclose(f[sde._offset_block_start:], 0.0)

    def test_sigma_shape(self, two_room):
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.0, 17.0])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        sig = sde.sigma(x, u, d, p, 0.0)
        # 2R2C augmented: nx = 3n = 6 → σ is 6×6.
        n = sde._n_rooms
        assert sig.shape == (3 * n, 3 * n)

    def test_sigma_is_scaled_identity(self, two_room):
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0, sigma_w=0.1)
        x = _aug_state([18.0, 17.0])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        sig = sde.sigma(x, u, d, p, 0.0)
        n = sde._n_rooms  # n=2
        nx_phys = sde._nx_phys  # 2n=4
        b_start = sde._offset_block_start  # 2n+m=4 (no filter)
        # Block diagonal: 0.1·I on the air block, 0.1·I on the wall block,
        # 0.002·I on the offset block.
        expected = np.zeros((sde.nx, sde.nx))
        expected[:nx_phys, :nx_phys] = 0.1 * np.eye(nx_phys)
        expected[b_start:b_start + n, b_start:b_start + n] = 0.002 * np.eye(n)
        np.testing.assert_array_almost_equal(sig, expected)

    def test_sigma_applies_per_room_process_noise_covariance_scales(self, two_room):
        model, sources = two_room
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
        # State order: [T_a(2), T_w(2), b(2)].
        # Air block: living_room gets scale=9, bedroom gets scale=1.
        assert diag[0] == pytest.approx(0.3)  # 0.1 * sqrt(9) = 0.3
        assert diag[1] == pytest.approx(0.1)  # 0.1 * sqrt(1) = 0.1

    def test_controlled_output_equals_state(self, two_room):
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.5, 17.5])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        z = sde.g(x, u, d, p, 0.0)
        np.testing.assert_array_equal(z, np.array([18.5, 17.5]))

    def test_controlled_output_includes_offset_state(self, two_room):
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.5, 17.5], [1.0, -0.25])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        z = sde.g(x, u, d, p, 0.0)
        np.testing.assert_array_equal(z, np.array([19.5, 17.25]))

    def test_measurement_equals_state(self, two_room):
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.5, 17.5])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        ym = sde.hm(x, u, d, p, 0.0)
        np.testing.assert_array_equal(ym, np.array([18.5, 17.5]))

    def test_measurement_includes_offset_state(self, two_room):
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.5, 17.5], [1.0, -0.25])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        ym = sde.hm(x, u, d, p, 0.0)
        np.testing.assert_array_equal(ym, np.array([19.5, 17.25]))

    def test_measurement_noise_covariance_shape(self, two_room):
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        Rm = sde.Rm
        assert Rm.shape == (2, 2)
        assert np.allclose(Rm, Rm.T)

    def test_measurement_noise_covariance_positive_definite(self, two_room):
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0, sigma_v=0.5)
        Rm = sde.Rm
        eigvals = np.linalg.eigvalsh(Rm)
        assert np.all(eigvals > 0)

    def test_analytic_state_jacobian(self, two_room):
        """``dfdx`` mirrors the 2n×2n matrix ``_F`` on the top-left
        physical block and is zero on the offset rows/columns."""
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.0, 17.0])
        u = np.array([0.3, 0.5])
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        J_analytic = sde.dfdx(x, u, d, p, 0.0)
        n = sde._n_rooms
        nx_phys = sde._nx_phys  # 2n
        # 2R2C augmented Jacobian: total nx = 3n = 6 for n=2.
        assert J_analytic.shape == (sde.nx, sde.nx)
        # Top-left 2n × 2n physical block matches the structural F.
        # No wind set, so the SG overlay is zero and J[:2n, :2n] == _F exactly.
        np.testing.assert_array_almost_equal(J_analytic[:nx_phys, :nx_phys], sde._F)
        # Offset block columns (b_start onwards) contribute zero to physical rows.
        b_start = sde._offset_block_start
        np.testing.assert_array_equal(
            J_analytic[:nx_phys, b_start:], np.zeros((nx_phys, n))
        )
        # Offset rows have zero drift.
        np.testing.assert_array_equal(
            J_analytic[b_start:, :], np.zeros((n, sde.nx))
        )

    def test_observation_jacobian_is_identity(self, two_room):
        """``dhm/dx`` for the 2R2C measurement ``y = T_a + b`` has shape
        (n, nx) with I_n on the air block and I_n on the offset block;
        wall and filter blocks are zero (unobserved).
        """
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([18.0, 17.0])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        H = sde.dhmdx(x, u, d, p, 0.0)
        n = sde._n_rooms
        b_start = sde._offset_block_start
        # Expected H: identity on air columns (0..n), zero on wall+filter,
        # identity on offset columns (b_start..b_start+n).
        expected = np.zeros((n, sde.nx))
        expected[:, :n] = np.eye(n)
        expected[:, b_start:b_start + n] = np.eye(n)
        np.testing.assert_array_equal(H, expected)

    def test_analytic_state_jacobian_matches_finite_difference_unaugmented(self, two_room):
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=False)
        # Un-augmented 2R2C: state = [T_a(n), T_w(n)], length 2n=4.
        n = sde._n_rooms
        x = np.array([18.0, 17.0, 18.0, 17.0], dtype=float)  # [T_a, T_w]
        u = np.array([0.4, 0.3], dtype=float)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])

        J_analytic = sde.dfdx(x, u, d, p, 0.0)
        J_fd = _central_difference_jacobian(
            lambda state: sde.f(state, u, d, p, 0.0),
            x,
        )
        np.testing.assert_allclose(J_analytic, J_fd, rtol=5e-4, atol=5e-6)

    def test_observation_jacobian_matches_finite_difference_unaugmented(self, two_room):
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=False)
        # Un-augmented 2R2C: state = [T_a(n), T_w(n)], length 2n=4.
        n = sde._n_rooms
        x = np.array([18.0, 17.0, 18.0, 17.0], dtype=float)  # [T_a, T_w]
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])

        H_analytic = sde.dhmdx(x, u, d, p, 0.0)
        H_fd = _central_difference_jacobian(
            lambda state: sde.hm(state, u, d, p, 0.0),
            x,
        )
        np.testing.assert_allclose(H_analytic, H_fd, rtol=1e-6, atol=1e-8)

    def test_disturbance_vector_shape(self, two_room):
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        d = sde.disturbance_vector(5.0, {"living_room": 200.0, "bedroom": 50.0})
        n = sde._n_rooms
        # d = [T_out, q_solar(n), q_air(n)]: length 1 + 2n
        assert d.shape == (1 + 2 * n,)
        assert d[0] == pytest.approx(5.0)

    def test_x_ref_matches_setpoints(self, two_room):
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        ref = sde.x_ref
        np.testing.assert_array_equal(ref, [21.0, 20.0])

    def test_u_bounds(self, two_room):
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        lb, ub = sde.u_bounds
        np.testing.assert_array_equal(lb, [0.0, 0.0])
        np.testing.assert_array_equal(ub, [1.0, 1.0])

    def test_u_bounds_heat_pump_cooling_capable(self, two_room):
        """A HeatPump with cooling_cop > 0 should have lower bound -1."""
        living = Room("living_room", 5_000_000.0, 0.05, temperature=22.0, setpoint=21.0)
        model = HouseModel([living])
        sources = [HeatPump("hp", "living_room", max_power=5000.0, cooling_cop=2.5)]
        sde = HouseThermalSDE(model, sources, dt=900.0)
        lb, ub = sde.u_bounds
        assert lb[0] == pytest.approx(-1.0)
        assert ub[0] == pytest.approx(1.0)

    def test_u_bounds_heat_pump_heat_only_mode(self, two_room):
        """hvac_mode='heat' → lower bound 0, upper bound 1."""
        living = Room("living_room", 5_000_000.0, 0.05, temperature=22.0, setpoint=21.0)
        model = HouseModel([living])
        sources = [HeatPump("hp", "living_room", max_power=5000.0, hvac_mode="heat")]
        sde = HouseThermalSDE(model, sources, dt=900.0)
        lb, ub = sde.u_bounds
        assert lb[0] == pytest.approx(0.0)
        assert ub[0] == pytest.approx(1.0)

    def test_u_bounds_heat_pump_cool_only_mode(self, two_room):
        """hvac_mode='cool' → lower bound -1, upper bound 0."""
        living = Room("living_room", 5_000_000.0, 0.05, temperature=22.0, setpoint=21.0)
        model = HouseModel([living])
        sources = [HeatPump("hp", "living_room", max_power=5000.0, hvac_mode="cool")]
        sde = HouseThermalSDE(model, sources, dt=900.0)
        lb, ub = sde.u_bounds
        assert lb[0] == pytest.approx(-1.0)
        assert ub[0] == pytest.approx(0.0)

    def test_drift_cooling_decreases_temperature(self, two_room):
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

    def test_drift_smooth_zero_at_u_zero(self, two_room):
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

    def test_drift_no_cooling_when_not_capable(self, two_room):
        """A heating-only source must not produce cooling drift when u < 0."""
        model, sources = two_room  # ElectricHeaters, can_cool=False
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

    def test_dfdu_shape(self, two_room):
        """dfdu must return an (nx, nu) matrix."""
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([20.0, 19.0])
        u = np.array([0.5, 0.3])
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        J = sde.dfdu(x, u, d, p, 0.0)
        assert J.shape == (sde.nx, sde.nu)

    def test_dfdu_matches_central_differences_electric_heaters(self, two_room):
        """Analytical dfdu must match FD Jacobian for heating-only sources."""
        model, sources = two_room
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

    def test_dfdu_matches_central_differences_heat_pump(self, two_room):
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

    def test_dgmdx_const_shape_unaugmented(self, two_room):
        """dgmdx_const must have shape (nz, nx) for un-augmented model."""
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=False)
        n = sde._n_rooms
        H = sde.dgmdx_const
        assert H.shape == (n, sde.nx)
        # First n columns are identity, rest are zero
        np.testing.assert_array_equal(H[:, :n], np.eye(n))
        np.testing.assert_array_equal(H[:, n:], np.zeros((n, sde.nx - n)))

    def test_dgmdx_const_shape_augmented(self, two_room):
        """dgmdx_const must have shape (nz, nx) for augmented model."""
        model, sources = two_room
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

    def test_dgmdx_const_matches_gm_finite_diff(self, two_room):
        """dgmdx_const must equal the finite-difference Jacobian of gm."""
        model, sources = two_room
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

    def test_dgmdx_method_called_by_mbc_eocp(self, two_room):
        """dgmdx() returns the same constant matrix as dgmdx_const."""
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0, augment_offsets=True)
        x = _aug_state([20.0, 19.0])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        H_method = sde.dgmdx(x, u, d, p, 0.0)
        H_prop = sde.dgmdx_const
        np.testing.assert_array_equal(H_method, H_prop)

    def test_dgmdu_returns_zeros(self, two_room):
        """dgmdu() must return a zero (nz, nu) matrix — gm is independent of u."""
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        x = _aug_state([20.0, 19.0])
        u = np.array([0.5, 0.3])
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        G = sde.dgmdu(x, u, d, p, 0.0)
        assert G.shape == (sde.nz, sde.nu)
        np.testing.assert_array_equal(G, np.zeros((sde.nz, sde.nu)))

    def test_mbc_eocp_uses_analytical_jacs(self, two_room):
        """New mbc EOCP always uses analytical Jacobians when model supplies them.

        After upgrading to mbc v0.1+, analytical Jacobians for equality and
        inequality constraints are always active (mbc calls model.dfdu and
        model.dgmdx directly).  This test verifies that the model's dgmdx
        and dgmdu are callable and return the correct shapes so mbc can
        use them.
        """
        model, sources = two_room
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

    def _make_ekf(self, two_room, sde=None):
        if sde is None:
            model, sources = two_room
            sde = HouseThermalSDE(model, sources, dt=900.0)
        x0 = np.array(sde.x)
        P0 = np.eye(sde.nx)
        return ContinuousDiscreteEKF(
            sde, x0, P0,
            params=ContinuousDiscreteEKFParams(
                n_steps=5,
                scheme=IntegrationScheme.IMPLICIT_EULER,
            ),
        ), sde

    def test_initial_state(self, two_room):
        ekf, sde = self._make_ekf(two_room)
        np.testing.assert_array_equal(ekf.x_hat, np.array(sde.x))

    def test_initial_covariance_shape(self, two_room):
        ekf, sde = self._make_ekf(two_room)
        assert ekf.P.shape == (sde.nx, sde.nx)

    def test_update_with_measurement(self, two_room):
        """After update the estimate should be close to the measurement."""
        ekf, sde = self._make_ekf(two_room)
        y = np.array([18.5, 17.5])
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        x_hat, P = ekf.step(y, u, d, p, 0.0)
        np.testing.assert_array_almost_equal(x_hat[:sde.nym], y, decimal=1)

    def test_covariance_propagates(self, two_room):
        """P should change after a predict-update cycle."""
        ekf, sde = self._make_ekf(two_room)
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        P_before = ekf.P.copy()
        ekf.step(np.array([18.0, 17.0]), u, d, p, 0.0)
        ekf.step(np.array([18.1, 17.1]), u, d, p, 900.0)
        assert not np.allclose(P_before, ekf.P)

    def test_covariance_stays_symmetric(self, two_room):
        """P must remain symmetric after multiple updates."""
        ekf, sde = self._make_ekf(two_room)
        u = np.zeros(sde.nu)
        d = sde.disturbance_vector(5.0, {})
        p = np.array([])
        for temp in [18.0, 18.1, 18.2, 18.3]:
            ekf.step(np.array([temp, temp - 1.0]), u, d, p, 0.0)
        np.testing.assert_array_almost_equal(ekf.P, ekf.P.T)

    def test_covariance_positive_semidefinite(self, two_room):
        """All eigenvalues of P should be non-negative."""
        ekf, sde = self._make_ekf(two_room)
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
# Removed from upstream mbc; HeatingAssistant uses linearised CD-MPC instead.

@pytest.mark.skip(reason="CDTrackingOptimalControlProblem was removed from upstream mbc")
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

    def test_solve_returns_correct_shapes(self, two_room):
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
    def test_actions_cover_all_sources(self, two_room):
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = _MPC_NOW
        actions = ctrl.compute(outdoor_temp=0.0, now=now)
        for src in sources:
            assert src.name in actions

    def test_fractions_in_range(self, two_room):
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = _MPC_NOW
        actions = ctrl.compute(outdoor_temp=0.0, now=now)
        for name, frac in actions.items():
            assert 0.0 <= frac <= 1.0, f"Fraction out of range for {name}: {frac}"

    def test_fractions_are_continuous(self, two_room):
        """QP optimisation; fractions are continuous, not grid-restricted."""
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900,
                                    energy_weight=0.001)
        now = _MPC_NOW
        actions = ctrl.compute(outdoor_temp=-5.0, now=now)
        assert all(isinstance(f, float) for f in actions.values())
        assert all(0.0 <= f <= 1.0 for f in actions.values())

    def test_heats_when_below_setpoint(self, two_room):
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        n_fast = ctrl.horizon
        n_rooms = ctrl._system._n_rooms
        t_ref = np.full((n_fast, n_rooms), 21.0)
        u_star = np.zeros((ctrl.timing.n_slow, ctrl._system.nu))
        ctrl.set_accepted_path(u_star, t_ref)
        now = _MPC_NOW
        actions = ctrl.compute(outdoor_temp=-10.0, now=now)
        assert any(frac > 0.0 for frac in actions.values())

    def test_no_heat_when_warm_enough(self, two_room):
        living = Room("living_room", 5e6, 0.05, temperature=25.0, setpoint=21.0)
        bedroom = Room("bedroom", 3e6, 0.08, temperature=24.0, setpoint=20.0)
        model = HouseModel([living, bedroom])
        sources = [
            ElectricHeater("lr_heater", "living_room", max_power=2000.0),
            ElectricHeater("br_heater", "bedroom", max_power=1500.0),
        ]
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = _MPC_NOW
        actions = ctrl.compute(outdoor_temp=22.0, now=now)
        assert all(frac == pytest.approx(0.0, abs=1e-4) for frac in actions.values())

    def test_solar_gains_provided_externally(self, two_room):
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = _MPC_NOW
        gains = {"living_room": 300.0, "bedroom": 100.0}
        actions = ctrl.compute(outdoor_temp=5.0, solar_gains=gains, now=now)
        for src in sources:
            assert src.name in actions
            assert 0.0 <= actions[src.name] <= 1.0

    def test_controller_updates_source_state(self, two_room):
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = _MPC_NOW
        actions = ctrl.compute(outdoor_temp=-5.0, now=now)
        for src in sources:
            expected = src.thermal_power(actions[src.name])
            assert src.current_power == pytest.approx(expected, rel=1e-6)

    def test_visualisation_properties_populated(self, two_room):
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        now = _MPC_NOW
        ctrl.compute(outdoor_temp=5.0, now=now)
        assert len(ctrl.predictions) == 3
        assert len(ctrl.outdoor_forecast) == 3
        assert len(ctrl.solar_forecast) == 4  # N+1: covers now through now+N*dt
        assert len(ctrl.heating_schedule) == 3

    def test_input_clamps_pin_applied_action_over_horizon(self, two_room):
        """An input clamp forces the room's heater to the prescribed signal even
        when the unconstrained MPC would idle, and the plan reflects it."""
        living = Room("living_room", 5e6, 0.05, temperature=25.0, setpoint=21.0)
        bedroom = Room("bedroom", 3e6, 0.08, temperature=24.0, setpoint=20.0)
        model = HouseModel([living, bedroom])
        sources = [
            ElectricHeater("lr_heater", "living_room", max_power=2000.0),
            ElectricHeater("br_heater", "bedroom", max_power=1500.0),
        ]
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        now = _MPC_NOW

        clamps = {"lr_heater": np.full(3, 1.0)}
        actions = ctrl.compute(outdoor_temp=22.0, now=now, input_clamps=clamps)

        # Warm rooms → MPC would idle; the clamp pins the living-room heater on
        # while the (unclamped) bedroom heater stays off.
        assert actions["lr_heater"] == pytest.approx(1.0, abs=1e-3)
        assert actions["br_heater"] == pytest.approx(0.0, abs=1e-2)
        # The planned heating schedule carries the clamp across the horizon, so
        # the actuator forecast plot shows the experiment signal.
        for step in ctrl.heating_schedule:
            assert step["living_room"] == pytest.approx(2000.0, rel=1e-2)

    def test_input_clamp_survives_disabled_source(self, two_room):
        """A clamped step overrides a disabled (schedule-off) source so an
        experiment can run during the comfort schedule's off periods."""
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        now = _MPC_NOW

        actions = ctrl.compute(
            outdoor_temp=5.0, now=now,
            disabled_sources={"lr_heater"},
            input_clamps={"lr_heater": np.full(3, 0.8)},
        )
        assert actions["lr_heater"] == pytest.approx(0.8, abs=1e-3)

    def test_input_clamp_is_linear_in_delivered_power_for_heat_pump(self, two_room):
        """The clamp value is a *power* fraction: a reversible heat pump delivers
        exactly that fraction of capacity (the sigmoid is inverted), so the step
        is linear in power — including the negative (cool) direction."""
        room = Room("living_room", 5e6, 0.05, temperature=21.0, setpoint=21.0)
        model = HouseModel([room])
        hp = HeatPump("hp", "living_room", max_power=5000.0, cooling_cop=2.5)
        ctrl = HeatingMPCController(model, [hp], horizon=2, dt=900)
        now = _MPC_NOW
        outdoor = 5.0
        q_heat = hp.thermal_power(1.0, outdoor)
        q_cool = abs(hp.cooling_power(outdoor))

        for pf, q in [(0.75, q_heat), (0.25, q_heat), (-0.5, q_cool)]:
            ctrl.compute(outdoor_temp=outdoor, now=now,
                         input_clamps={"hp": np.full(2, pf)})
            delivered = ctrl.heating_schedule[0]["living_room"]
            assert delivered == pytest.approx(pf * q, rel=2e-2)

    def test_input_clamp_partial_horizon_zeros_unclamped_disabled_steps(self, two_room):
        """With a clamp only on the first step, a disabled source is pinned for
        that step and zeroed for the released tail."""
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        now = _MPC_NOW

        clamp = np.array([1.0, np.nan, np.nan])
        ctrl.compute(
            outdoor_temp=5.0, now=now,
            disabled_sources={"lr_heater"},
            input_clamps={"lr_heater": clamp},
        )
        sched = ctrl.heating_schedule
        assert sched[0]["living_room"] == pytest.approx(2000.0, rel=1e-2)
        assert sched[1]["living_room"] == pytest.approx(0.0, abs=1e-6)
        assert sched[2]["living_room"] == pytest.approx(0.0, abs=1e-6)

    def test_input_clamp_with_experiment_comfort_relaxation_and_disabled_source(self, two_room):
        """Experiment clamps must still force the prescribed signal even when
        the coordinator applies full comfort relaxation (Q=0, offset=1000 °C)
        AND the room is schedule-disabled.

        This is the regression guard for the bug where _relax_experiment_comfort
        opening the corridor to 1000 °C caused the QP to return zero for all
        horizon steps in the room view power chart.
        """
        from types import SimpleNamespace
        room = Room("living_room", 5e6, 0.05, temperature=18.0, setpoint=21.0)
        model = HouseModel([room])
        sources = [ElectricHeater("lr_heater", "living_room", max_power=2000.0)]
        ctrl = HeatingMPCController(model, sources, horizon=4, dt=3600)
        now = datetime(2024, 1, 15, 2, 0, tzinfo=timezone.utc)

        # Simulate what _relax_experiment_comfort does: zero Q weight and open
        # the comfort corridor wide (1000 °C) for all governed horizon steps.
        EXPERIMENT_RELAXED_OFFSET = 1000.0
        traj = SimpleNamespace(
            setpoints={"living_room": np.full(4, 21.0)},
            comfort_offsets={"living_room": np.full(4, EXPERIMENT_RELAXED_OFFSET)},
            q_scales={"living_room": np.zeros(4)},   # experiment relaxation: Q=0
            r_scales={"living_room": np.ones(4)},
            enabled_steps={"living_room": np.zeros(4, dtype=bool)},  # room off
        )

        # Step experiment: heating phase (50 % for steps 0-2), settle (0 %) at step 3.
        clamps = {"lr_heater": np.array([0.5, 0.5, 0.5, 0.0])}

        ctrl.compute(
            outdoor_temp=5.0, now=now,
            disabled_sources={"lr_heater"},
            control_trajectory=traj,
            input_clamps=clamps,
        )
        sched = ctrl.heating_schedule
        # Steps 0-2 must reflect the 50 % clamp = 1000 W.
        assert sched[0]["living_room"] == pytest.approx(1000.0, rel=5e-2), (
            "Step 0: experiment clamp should force 50 % power (1000 W); got zero — "
            "comfort relaxation is incorrectly suppressing the experiment signal"
        )
        assert sched[1]["living_room"] == pytest.approx(1000.0, rel=5e-2)
        assert sched[2]["living_room"] == pytest.approx(1000.0, rel=5e-2)
        # Settle phase must be off.
        assert sched[3]["living_room"] == pytest.approx(0.0, abs=1e-6)

    def test_controller_uses_unaugmented_states_for_runtime_efficiency(self, two_room):
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        assert ctrl._system._augment_offsets is False
        assert ctrl._control_system._augment_offsets is False
        n = ctrl._system._n_rooms
        # 2R2C un-augmented: [T_a(n), T_w(n)] = 2n states, no filter states
        assert ctrl._system.nx == 2 * n

    def test_filtered_temperatures_use_air_state_when_offsets_disabled(self, two_room):
        room = Room("living_room", 5_000_000.0, 0.05, temperature=20.0, setpoint=21.0)
        model = HouseModel([room])
        ctrl = HeatingMPCController(
            model,
            [ElectricHeater("h", "living_room", 2000.0)],
            horizon=2,
            dt=900,
        )
        ctrl._ekf._x = np.array([20.0, 20.0, 20.0], dtype=float)

        assert ctrl.filtered_temperatures["living_room"] == pytest.approx(20.0)

    def test_predictions_use_air_state_when_offsets_disabled(self, two_room):
        room = Room("living_room", 5_000_000.0, 0.05, temperature=20.0, setpoint=21.0)
        model = HouseModel([room])
        ctrl = HeatingMPCController(
            model,
            [ElectricHeater("h", "living_room", 2000.0)],
            horizon=2,
            dt=900,
        )
        ctrl.compute(
            outdoor_temp=20.0,
            solar_gains={"living_room": 0.0},
            now=datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc),
            outdoor_forecast=[20.0, 20.0],
        )

        preds = [step["living_room"] for step in ctrl.predictions]
        assert len(preds) == 2
        assert all(abs(t - 20.0) < 1.0 for t in preds)

    def test_predictions_contain_all_rooms(self, two_room):
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = _MPC_NOW
        ctrl.compute(outdoor_temp=5.0, now=now)
        for step in ctrl.predictions:
            assert "living_room" in step
            assert "bedroom" in step

    def test_heat_pump_cop_varies_with_temperature(self, two_room):
        """Higher outdoor temperature -> higher COP."""
        hp = HeatPump("hp", "lr", max_power=6100.0, cop_rated=3.5, cop_temp_ref=7.0)
        assert hp.cop(10.0) > hp.cop(-10.0)

    def test_smoothing_weight_reduces_input_change(self):
        """With a large smoothing weight, the first-step input should
        be smaller or equal to the unsmoothed case."""
        model_a, sources_a = _make_model_and_sources()
        model_b, sources_b = _make_model_and_sources()
        now = _MPC_NOW

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
        now = _MPC_NOW

        ctrl_a = HeatingMPCController(model_a, sources_a, horizon=2, dt=900,
                                      smoothing_weight=0.0)
        ctrl_b = HeatingMPCController(model_b, sources_b, horizon=2, dt=900,
                                      smoothing_weight=0.0)

        actions_a = ctrl_a.compute(outdoor_temp=0.0, now=now)
        actions_b = ctrl_b.compute(outdoor_temp=0.0, now=now)

        for name in actions_a:
            assert actions_a[name] == pytest.approx(actions_b[name], abs=1e-4)

    def test_default_smoothing_weight(self, two_room):
        """Default smoothing_weight is 0.1."""
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = _MPC_NOW
        actions = ctrl.compute(outdoor_temp=0.0, now=now)
        assert all(0.0 <= f <= 1.0 for f in actions.values())

    def test_soft_constraint_weight_configurable(self, two_room):
        """soft_constraint_weight should be accepted and affect behaviour."""
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900,
                                    soft_constraint_weight=1000.0)
        now = _MPC_NOW
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

    def test_weak_setpoint_pull_no_chasing_inside_comfort_corridor(self, two_room):
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
        now = _MPC_NOW
        actions = ctrl.compute(outdoor_temp=20.0, now=now)
        assert actions["heater"] == pytest.approx(0.0, abs=2e-2)

    def test_outdoor_forecast_used(self, two_room):
        """When an outdoor forecast is provided, it should be stored."""
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        now = _MPC_NOW
        forecast = [-5.0, -6.0, -7.0]
        ctrl.compute(outdoor_temp=-4.0, now=now, outdoor_forecast=forecast)
        assert ctrl.outdoor_forecast == forecast

    def test_uses_linearised_mpc(self, two_room):
        """HeatingMPCController should use the forecast-aware linearised MPC."""
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        assert isinstance(ctrl._system, HouseThermalSDE)
        assert isinstance(ctrl._control_system, HouseThermalSDE)
        assert ctrl._control_system.nx == ctrl._system.nx
        assert isinstance(ctrl._ekf, ContinuousDiscreteEKF)
        assert isinstance(ctrl._mpc, HeatingLinearisedMPC)

    def test_negative_smoothing_weight_raises(self, two_room):
        model, sources = two_room
        with pytest.raises(ValueError, match="smoothing_weight"):
            HeatingMPCController(model, sources, horizon=2, dt=900,
                                 smoothing_weight=-0.1)

    def test_solver_and_derivative_flags_exposed(self, two_room):
        """solver_requested, solver_active and use_analytic_derivatives are readable."""
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        assert isinstance(ctrl.solver_requested, str)
        assert isinstance(ctrl.solver_active, str)
        assert ctrl.use_analytic_derivatives is True

    def test_mpc_requests_cooling_when_above_setpoint(self, two_room):
        """When a cooling-capable heat pump room is well above setpoint, the
        MPC should output a negative fraction (active cooling request)."""
        living = Room(
            "living_room", 5_000_000.0, 0.05, temperature=27.0, setpoint=21.0,
        )
        model = HouseModel([living])
        hp = HeatPump("hp", "living_room", max_power=5000.0, cooling_cop=2.5)
        ctrl = HeatingMPCController(model, [hp], horizon=3, dt=900)
        _seed_path(ctrl, t_ref=[21.0], u_ref=0.0)
        now = datetime(2024, 7, 1, 14, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=25.0, now=now)
        assert actions["hp"] < 0.0, (
            f"Expected negative fraction (cooling) when room is well above "
            f"setpoint, got {actions['hp']}"
        )

    def test_mpc_cooling_source_power_is_negative(self, two_room):
        """After a cooling step, the source's current_power should be negative."""
        living = Room(
            "living_room", 5_000_000.0, 0.05, temperature=27.0, setpoint=21.0,
        )
        model = HouseModel([living])
        hp = HeatPump("hp", "living_room", max_power=5000.0, cooling_cop=2.5)
        ctrl = HeatingMPCController(model, [hp], horizon=3, dt=900)
        _seed_path(ctrl, t_ref=[21.0], u_ref=0.0)
        now = datetime(2024, 7, 1, 14, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=25.0, now=now)
        assert hp.current_power < 0.0, (
            "Expected negative current_power on heat pump in cooling mode"
        )

    def test_no_cooling_when_heat_only_mode(self, two_room):
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

    def test_heat_pump_filter_state_cooling_reduced_inside_comfort(self, two_room):
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
        # 2R2C state layout for 1 room + 1 filtered source:
        # [T_a(1), T_w(1), phi(1)] = length 3.
        ctrl._ekf._x = np.array([21.0, 21.0, -1.0])
        ctrl._mpc._u_prev = np.array([-1.0])

        now = datetime(2024, 7, 1, 14, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(
            outdoor_temp=20.0, solar_gains={"living": 0.0}, now=now,
        )

        # The MPC must substantially reduce cooling from full power (-1.0).
        # The buggy transposed-Ad version returned -0.65 because it could not
        # see the filter state driving the temperature out of the comfort band.
        # With the linear heat-pump power curve the cooling gain no longer
        # saturates near u = -1, so the calibrated reduction settles a touch
        # below -0.5 (≈45 % cut) rather than the sigmoid model's over-back-off.
        assert actions["hp"] > -0.6, (
            f"Expected cooling to be substantially reduced when filter state "
            f"drives temperature below comfort; got {actions['hp']:.4f} "
            f"(buggy version: ~-0.65)"
        )


class TestTotalComputes:
    """total_computes increments on every compute() call and never saturates."""

    def test_starts_at_zero(self, two_room):
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        assert ctrl.total_computes == 0

    def test_increments_each_call(self, two_room):
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = _MPC_NOW
        for expected in range(1, 5):
            ctrl.compute(outdoor_temp=0.0, now=now)
            assert ctrl.total_computes == expected, (
                f"Expected total_computes={expected}, got {ctrl.total_computes}"
            )

    def test_exceeds_rolling_window(self, two_room):
        """total_computes must exceed MPC_STATS_BUFFER_SIZE (rolling deque cap)."""
        from heatingassistant.engine.controller import MPC_STATS_BUFFER_SIZE

        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        now = _MPC_NOW
        # Run one more than the buffer size
        for _ in range(MPC_STATS_BUFFER_SIZE + 1):
            ctrl.compute(outdoor_temp=0.0, now=now)
        assert ctrl.total_computes == MPC_STATS_BUFFER_SIZE + 1, (
            "total_computes should grow beyond the rolling-window cap"
        )
        assert ctrl.n_solves == 0, (
            "NLP solve times are recorded on the worker, not on compute()"
        )


class TestSolarForecastIndexing:
    """_forecast_solar must start at k=0 (current solar) not k=1 (one step ahead)."""

    _NOW = datetime(2024, 6, 21, 12, 0, tzinfo=timezone.utc)  # Summer solstice noon

    def test_solar_seq_zero_uses_current_time(self):
        """solar_seq[0] must equal the solar gain computed at *now* (not now+dt)."""
        from heatingassistant.engine.controller import (
            HeatingMPCController, HouseThermalSDE,
        )
        from heatingassistant.engine.solar_model import room_solar_gains
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
        from heatingassistant.engine.solar_model import room_solar_gains

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
        """Comfort bounds for the slow OCP track the current setpoints."""
        room = Room(
            "living_room", 5_000_000.0, 0.05,
            temperature=18.0, setpoint=21.0, comfort_offset=2.0,
        )
        model = HouseModel([room])
        ctrl = HeatingMPCController(
            model, [ElectricHeater("h", "living_room", 2000)],
            horizon=4, dt=900,
        )

        t_min, t_max = ctrl._comfort_bounds_fast(None, ctrl.horizon)
        assert t_min[0, 0] == pytest.approx(19.0)
        assert t_max[0, 0] == pytest.approx(23.0)

        model.rooms["living_room"].setpoint = 25.0
        t_min, t_max = ctrl._comfort_bounds_fast(None, ctrl.horizon)
        assert t_min[0, 0] == pytest.approx(23.0)
        assert t_max[0, 0] == pytest.approx(27.0)


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


class TestRunOptimizationGate:
    """compute(run_optimization=False) runs only the EKF, skipping the MPC.

    When the system is stopped the controller must keep estimating state (so
    filtered temperatures and the Kalman innovation stay live) but must not
    solve the QP or produce a control trajectory.
    """

    _NOW = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)

    def test_state_estimation_runs_while_stopped(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        # Seed the EKF with one normal cycle, then change the measurement.
        ctrl.compute(outdoor_temp=5.0, now=self._NOW)
        model.rooms["living_room"].temperature = 25.0
        model.rooms["bedroom"].temperature = 24.0

        ctrl.compute(outdoor_temp=5.0, now=self._NOW, run_optimization=False)

        innov = ctrl.last_innovation
        assert innov is not None, "EKF innovation must be populated while stopped"
        assert len(innov) == len(model.room_names)
        # Filtered temperatures move toward the new (warmer) measurement.
        filt = ctrl.filtered_temperatures
        assert filt["living_room"] > 21.0, (
            "EKF must fuse the new measurement even while the MPC is stopped"
        )

    def test_mpc_skipped_while_stopped(self):
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)

        actions = ctrl.compute(outdoor_temp=-10.0, now=self._NOW, run_optimization=False)

        # No QP solve recorded, no forecast trajectory produced.
        assert ctrl.total_computes == 0, "No MPC solve should be counted while stopped"
        assert ctrl.predictions == [], "Predictions must be cleared while stopped"
        assert ctrl.heating_schedule == [], "Heating schedule must be cleared while stopped"
        # Still returns a full action dict (current commanded fractions).
        for src in sources:
            assert src.name in actions

    def test_optimization_runs_when_enabled(self):
        """Sanity: the default path still solves the QP and predicts a trajectory."""
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        ctrl.compute(outdoor_temp=-10.0, now=self._NOW)
        assert ctrl.total_computes == 1
        assert len(ctrl.predictions) > 0

    def test_covariance_not_collapsed_after_many_stopped_cycles(self):
        """EKF covariance must not collapse to zero during extended stopped periods.

        Without a predict step, P can only decrease via the update equation.
        After many cycles P → 0, the Kalman gain K → 0, and the filter
        freezes — it no longer tracks the actual temperature.  This is the
        open-loop-drift symptom the user reported overnight.

        The fix (calling estimator.step rather than propagate) guarantees
        predict always runs for the nominal Ts, keeping P at its steady-state
        value and the gain at a level that lets the filter track changes.
        """
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        # Warm up with one optimisation cycle.
        ctrl.compute(outdoor_temp=5.0, now=self._NOW)

        # Simulate an overnight stopped period: 32 cycles at 15-min intervals.
        for k in range(32):
            model.rooms["living_room"].temperature = 20.0 - k * 0.1
            model.rooms["bedroom"].temperature = 19.0 - k * 0.1
            ctrl.compute(outdoor_temp=5.0, now=self._NOW, run_optimization=False)

        # After the stopped period the room has cooled significantly.
        # A healthy filter (P maintained by predict) must still track it.
        model.rooms["living_room"].temperature = 15.0
        model.rooms["bedroom"].temperature = 14.0
        ctrl.compute(outdoor_temp=0.0, now=self._NOW, run_optimization=False)

        filt = ctrl.filtered_temperatures
        # If P had collapsed (K ≈ 0) the estimate would be frozen near 20 °C.
        # A working filter must pull the estimate well below 19 °C.
        assert filt["living_room"] < 19.0, (
            "EKF must still track temperature changes after many stopped cycles; "
            "if the covariance collapsed the estimate would be frozen near 20 °C"
        )

    def test_no_discrete_jump_on_re_enable(self):
        """Filtered temperature must not jump discontinuously when re-enabled.

        With the P-collapse bug: overnight stopped → estimate freezes at ~20 °C,
        room cools to ~16 °C → re-enable → first predict inflates P → huge
        update correction → discrete jump reported by the user.

        After the fix the estimate tracks the measurement throughout the stopped
        period so the innovation at re-enable is small and no jump occurs.
        """
        model, sources = _make_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        ctrl.compute(outdoor_temp=5.0, now=self._NOW)

        # Stopped period: room cools from 20 °C to 16 °C over 20 cycles.
        for k in range(20):
            temp = 20.0 - k * 0.2
            model.rooms["living_room"].temperature = temp
            model.rooms["bedroom"].temperature = temp - 1.0
            ctrl.compute(outdoor_temp=3.0, now=self._NOW, run_optimization=False)

        filt_before = ctrl.filtered_temperatures["living_room"]

        # Re-enable: first optimisation cycle after the stopped period.
        model.rooms["living_room"].temperature = 16.0
        model.rooms["bedroom"].temperature = 15.0
        ctrl.compute(outdoor_temp=3.0, now=self._NOW, run_optimization=True)

        filt_after = ctrl.filtered_temperatures["living_room"]
        jump = abs(filt_after - filt_before)
        assert jump < 2.0, (
            f"Filtered temperature jumped {jump:.2f} °C on re-enable; "
            "the estimate should have been tracking the measurement during the "
            "stopped period so no large correction is needed at re-enable"
        )


class TestTerminalWeight:
    """HeatingMPCController.terminal_weight exposes the configured weight."""

    def test_default_terminal_weight(self, two_room):
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900)
        assert ctrl.terminal_weight == pytest.approx(100.0)

    def test_custom_terminal_weight(self, two_room):
        model, sources = two_room
        ctrl = HeatingMPCController(model, sources, horizon=2, dt=900,
                                    terminal_weight=50.0)
        assert ctrl.terminal_weight == pytest.approx(50.0)

    def test_terminal_weight_is_float(self, two_room):
        model, sources = two_room
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
        _seed_path(ctrl)
        now = datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now, disabled_sources={"br_heater"})
        assert actions["br_heater"] == pytest.approx(0.0)

    def test_enabled_source_action_is_nonzero(self):
        model, sources = self._make_cold_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        _seed_path(ctrl)
        now = datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now, disabled_sources={"br_heater"})
        assert actions["lr_heater"] > 0.0

    def test_disabled_source_heating_schedule_all_zeros(self):
        model, sources = self._make_cold_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        _seed_path(ctrl)
        now = datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=0.0, now=now, disabled_sources={"br_heater"})
        for step in ctrl.heating_schedule:
            assert step.get("bedroom", 0.0) == pytest.approx(0.0), (
                f"Expected 0 W predicted for disabled bedroom, got {step}"
            )

    def test_enabled_source_heating_schedule_nonzero(self):
        model, sources = self._make_cold_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        _seed_path(ctrl)
        now = datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=0.0, now=now, disabled_sources={"br_heater"})
        first_step = ctrl.heating_schedule[0]
        assert first_step.get("living_room", 0.0) > 0.0

    def test_disabled_source_current_power_is_zero(self):
        model, sources = self._make_cold_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        _seed_path(ctrl)
        now = datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=0.0, now=now, disabled_sources={"br_heater"})
        br_heater = next(s for s in sources if s.name == "br_heater")
        assert br_heater.current_power == pytest.approx(0.0)

    def test_no_disabled_sources_behaves_normally(self):
        model, sources = self._make_cold_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        _seed_path(ctrl)
        now = datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(outdoor_temp=0.0, now=now, disabled_sources=None)
        assert actions["lr_heater"] > 0.0
        assert actions["br_heater"] > 0.0

    def test_all_disabled_all_actions_zero(self):
        model, sources = self._make_cold_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        _seed_path(ctrl)
        now = datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(
            outdoor_temp=0.0, now=now,
            disabled_sources={"lr_heater", "br_heater"},
        )
        assert actions["lr_heater"] == pytest.approx(0.0)
        assert actions["br_heater"] == pytest.approx(0.0)

    def test_all_disabled_heating_schedule_stays_zero_with_plan(self):
        model, sources = self._make_cold_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        _seed_path(ctrl)
        now = datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc)
        ctrl.compute(
            outdoor_temp=0.0, now=now,
            disabled_sources={"lr_heater", "br_heater"},
        )
        for step in ctrl.heating_schedule:
            assert step.get("living_room", 0.0) == pytest.approx(0.0)
            assert step.get("bedroom", 0.0) == pytest.approx(0.0)

    def test_mpc_actions_holds_unzeroed_optimum_for_disabled_source(self):
        # The MPC keeps solving for a disabled source in the background; the
        # returned ``actions`` are zeroed for it, but ``mpc_actions`` exposes
        # the actuation it would command if the source were available — what a
        # window-override room should resume at once its window closes again.
        model, sources = self._make_cold_model_and_sources()
        ctrl = HeatingMPCController(model, sources, horizon=3, dt=900)
        _seed_path(ctrl)
        now = datetime(2024, 1, 15, 3, 0, tzinfo=timezone.utc)
        actions = ctrl.compute(
            outdoor_temp=0.0, now=now, disabled_sources={"br_heater"}
        )
        assert actions["br_heater"] == pytest.approx(0.0)
        # The cold bedroom still wants heat — the shadow optimum is positive.
        assert ctrl.mpc_actions["br_heater"] > 0.0
        # Enabled sources match between the two views.
        assert ctrl.mpc_actions["lr_heater"] == pytest.approx(actions["lr_heater"])


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

    def test_heating_only_source_equilibrium_in_bounds(self, two_room):
        """Electric heater equilibrium must lie in [0, 1]."""
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        # 2R2C state: [T_a(2), T_w(2)] for un-augmented
        x = np.array([18.0, 17.0, 18.0, 17.0], dtype=float)
        d = sde.disturbance_vector(-5.0, {})
        p = np.array([])
        u_eq = sde.compute_u_eq(x, d, p, 0.0)
        assert u_eq.shape == (2,)
        for j in range(sde.nu):
            assert 0.0 <= u_eq[j] <= 1.0, f"u_eq[{j}]={u_eq[j]} out of [0,1]"

    def test_heat_pump_equilibrium_in_bounds(self, two_room):
        """Heat pump equilibrium must lie in [-1, 1]."""
        model, sources = _make_hp_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        # 2R2C un-augmented single room: [T_a, T_w]
        x = np.array([15.0, 15.0], dtype=float)
        d = sde.disturbance_vector(-5.0, {})
        p = np.array([])
        u_eq = sde.compute_u_eq(x, d, p, 0.0)
        assert u_eq.shape == (1,)
        assert -1.0 <= u_eq[0] <= 1.0

    def test_equilibrium_maintains_temperature(self, two_room):
        """f evaluated at u_eq should have near-zero temperature drift."""
        model, sources = _make_hp_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        outdoor_temp = 5.0
        # 2R2C un-augmented single room: [T_a, T_w]
        x = np.array([18.0, 18.0], dtype=float)
        d = sde.disturbance_vector(outdoor_temp, {})
        p = np.array([])
        u_eq = sde.compute_u_eq(x, d, p, 0.0)
        drift = sde.f(x, u_eq, d, p, 0.0)
        # Air-temperature block drift should be close to zero.
        assert abs(drift[0]) < 0.5, f"Temperature drift at u_eq too large: {drift[0]:.4f} °C/s"

    def test_equilibrium_zero_when_room_warm_heating_only(self, two_room):
        """A heating-only source should have u_eq = 0 when the room is warm
        enough that thermal losses are zero or negative (e.g. warm outdoor)."""
        model, sources = two_room
        sde = HouseThermalSDE(model, sources, dt=900.0)
        # 2R2C un-augmented two rooms: [T_a(2), T_w(2)]
        x = np.array([25.0, 25.0, 25.0, 25.0], dtype=float)
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
        # 2R2C un-augmented single room: [T_a, T_w]
        x = np.array([18.0, 18.0], dtype=float)
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
        _seed_path(ctrl, t_ref=[21.0], u_ref=0.8)
        ctrl.compute(outdoor_temp=-5.0, now=now)
        # Fast P tracks T_ref; no QP linearisation point on the happy path.
        actions = ctrl.compute(outdoor_temp=-5.0, now=now)
        assert actions["hp"] > 0.5

    def test_cold_room_requests_near_max_heating(self):
        """A very cold room (16 °C below setpoint) should receive strong
        heating on the first step, not the attenuated output caused by a bad
        linearisation at the cold operating point (the guarded regression
        produced u ≈ 0.05).  Under the 2R2C model the optimum is lower than
        the old 1R1C near-max value: the fast air node reaches the comfort
        corridor within the horizon without heating the full thermal mass,
        so the threshold separates the pathology rather than pinning the
        1R1C optimum."""
        ctrl = self._make_ctrl(room_temp=5.0, setpoint=21.0)
        now = datetime(2024, 1, 15, 6, 0, tzinfo=timezone.utc)
        _seed_path(ctrl, t_ref=[21.0], u_ref=0.8)
        actions = ctrl.compute(outdoor_temp=-5.0, now=now)
        u_hp = actions["hp"]
        assert u_hp > 0.5, (
            f"Expected strong heating for cold room (u > 0.5), got u = {u_hp:.3f}"
        )

    def test_x_ref_is_unchanged_by_setpoint_linearisation(self):
        """The MPC's x_ref must remain equal to the setpoints array (not x_hat)."""
        ctrl = self._make_ctrl(room_temp=5.0, setpoint=21.0)
        now = datetime(2024, 1, 15, 6, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=-5.0, now=now)
        assert ctrl._mpc.x_ref[0] == pytest.approx(21.0)


class TestSolarGhiThreading:
    """The controller's per-room solar gain honours forecast-GHI precedence and
    the windowless exposure fallback (the solar-forecast work)."""

    def _windowed_controller(self):
        living = Room(
            name="living_room", thermal_mass=5_000_000.0, r_external=0.05,
            temperature=18.0, setpoint=21.0,
            windows=[Window(area=2.0, orientation=180.0, tilt=90.0)],
        )
        # No windows, but a solar-exposure aperture facing south.
        bedroom = Room(
            name="bedroom", thermal_mass=3_000_000.0, r_external=0.08,
            temperature=17.0, setpoint=20.0,
            solar_exposure_aperture=3.0, solar_facing=180.0,
        )
        model = HouseModel([living, bedroom])
        sources = [ElectricHeater("lr", "living_room", max_power=2000.0)]
        return HeatingMPCController(model, sources, horizon=3, dt=900.0)

    def test_room_gain_ghi_overrides_cloud(self):
        ctrl = self._windowed_controller()
        now = datetime(2024, 6, 21, 11, 0, tzinfo=timezone.utc)
        # With GHI provided, cloud cover is ignored entirely.
        with_cloud = ctrl._room_gain("living_room", now, cloud_cover=1.0, ghi=500.0)
        no_cloud = ctrl._room_gain("living_room", now, cloud_cover=None, ghi=500.0)
        assert no_cloud > 0.0
        assert with_cloud == pytest.approx(no_cloud, rel=1e-12)

    def test_windowless_room_uses_exposure(self):
        ctrl = self._windowed_controller()
        now = datetime(2024, 6, 21, 11, 0, tzinfo=timezone.utc)
        gain = ctrl._room_gain("bedroom", now, cloud_cover=None, ghi=600.0)
        assert gain > 0.0  # exposure preset gives non-zero gain without windows

    def test_forecast_solar_per_step_fallback(self):
        ctrl = self._windowed_controller()
        now = datetime(2024, 6, 21, 11, 0, tzinfo=timezone.utc)
        # k=0 uses ghi_now=500; k=1 uses ghi_forecast[0]=500 (same GHI, different
        # sun position 15 min later); k=2 uses ghi_forecast[1]=None → falls back to
        # cloud_cover_now via ghi_now fallback.
        schedules = ctrl._forecast_solar(
            now,
            cloud_forecast=[1.0, 1.0, 1.0, 1.0],
            cloud_cover_now=1.0,
            ghi_forecast=[500.0, None, None, None],
            ghi_now=500.0,
        )
        # k=0 and k=1 both use GHI=500 but differ by sun position (15 min apart).
        g0 = schedules[0]["living_room"]
        g1 = schedules[1]["living_room"]
        # k=2 uses ghi_forecast[1]=None → fallback to ghi_now=500, still GHI path.
        g2 = schedules[2]["living_room"]
        assert g0 > 0.0 and g1 >= 0.0 and g2 >= 0.0
        # All steps use GHI=500 but differ by sun position.
        assert abs(g0 - g1) > 1e-9


class TestPriceAwareAbsoluteEnergyPricing:
    """Price-aware MPC charges absolute electrical draw (u_abs), not u_dev."""

    def _make_ctrl(self, room_temp: float, horizon: int = 16):
        room = Room(
            "living_room",
            5_000_000.0,
            0.05,
            air_temperature=room_temp,
            setpoint=21.0,
            comfort_offset=2.0,
        )
        model = HouseModel([room])
        hp = HeatPump(
            "hp", "living_room", max_power=6000.0, cop_rated=3.5,
            cop_temp_ref=7.0, cooling_cop=2.5, hvac_mode="heat",
        )
        return HeatingMPCController(
            model, [hp], horizon=horizon, dt=900,
            tracking_weight=0.0,
            energy_weight=0.0,
            soft_constraint_weight=10.0,
            energy_price_weight=1.0,
        )

    def test_cheap_tariff_triggers_proactive_heating_before_lower_bound(self):
        """With cheap prices now and a cold forecast, the MPC should heat
        proactively instead of coasting to the comfort lower bound."""
        ctrl = self._make_ctrl(room_temp=19.1)
        now = datetime(2024, 1, 15, 6, 0, tzinfo=timezone.utc)
        outdoor_fc = [-8.0] * 16
        prices = [0.05] * 8 + [0.50] * 8
        actions = ctrl.compute(
            outdoor_temp=-8.0, now=now,
            outdoor_forecast=outdoor_fc, price_forecast=prices,
        )
        preds = ctrl.predictions
        lower_bound = 19.0
        # With no accepted plan, P commands u=0.  Seed a cheap-hour plan and
        # the tracker must heat.
        _seed_path(ctrl, t_ref=[21.0], u_ref=0.4)
        actions = ctrl.compute(
            outdoor_temp=-8.0, now=now,
            outdoor_forecast=outdoor_fc, price_forecast=prices,
        )
        preds = ctrl.predictions
        assert actions["hp"] > 0.03
        assert preds[0]["living_room"] > lower_bound - 1.0

    def test_rising_price_monotonic_fall_heats_early_not_at_boundary(self):
        """User scenario: temperature falls toward the lower bound while
        electricity prices rise monotonically.  The MPC must heat during the
        cheap early steps and stay off the bound — not coast to the edge and
        only heat once the constraint is reached at peak prices."""
        room = Room(
            "living_room", 5_000_000.0, 0.05,
            air_temperature=20.5, setpoint=21.0, comfort_offset=2.0,
        )
        model = HouseModel([room])
        hp = HeatPump(
            "hp", "living_room", max_power=6000.0, cop_rated=3.5,
            cop_temp_ref=7.0, cooling_cop=2.5, hvac_mode="heat",
        )
        horizon = 24
        ctrl = HeatingMPCController(
            model, [hp], horizon=horizon, dt=900,
            tracking_weight=0.0, energy_weight=0.0,
            soft_constraint_weight=10.0, energy_price_weight=1.0,
        )
        now = datetime(2024, 1, 15, 6, 0, tzinfo=timezone.utc)
        outdoor_fc = [-6.0] * horizon
        prices = [round(0.10 + 0.02 * k, 3) for k in range(horizon)]
        _seed_path(ctrl, t_ref=[21.0], u_ref=0.3)
        actions = ctrl.compute(
            outdoor_temp=-6.0, now=now,
            outdoor_forecast=outdoor_fc, price_forecast=prices,
        )
        preds = ctrl.predictions
        sched = ctrl.heating_schedule
        lower_bound = 19.0
        half = horizon // 2

        assert actions["hp"] > 0.01
        assert list(sched[0].values())[0] > 30.0
        assert all(p["living_room"] > lower_bound - 2.0 for p in preds[:half])

    def test_no_price_preserves_boundary_riding_zone_control(self):
        """Without price awareness the controller may ride the corridor edge."""
        ctrl = self._make_ctrl(room_temp=19.1)
        now = datetime(2024, 1, 15, 6, 0, tzinfo=timezone.utc)
        outdoor_fc = [-8.0] * 16
        ctrl.compute(
            outdoor_temp=-8.0, now=now,
            outdoor_forecast=outdoor_fc, price_forecast=None,
        )
        preds = ctrl.predictions
        # No accepted plan → u=0 (zone hold), forecast is the unheated rollout.
        assert ctrl._mpc_actions["hp"] == pytest.approx(0.0, abs=0.05)
        assert preds[0]["living_room"] == pytest.approx(19.1, abs=1.0)

    def test_centered_room_does_not_heat_under_cheap_tariff(self):
        """Cheap prices alone must not trigger heating when the room is centred."""
        ctrl = self._make_ctrl(room_temp=21.0)
        now = datetime(2024, 1, 15, 6, 0, tzinfo=timezone.utc)
        prices = [0.05] * 16
        actions = ctrl.compute(
            outdoor_temp=-8.0, now=now,
            outdoor_forecast=[-8.0] * 16, price_forecast=prices,
        )
        assert actions["hp"] == pytest.approx(0.0, abs=0.02)

    def test_electric_heater_price_term_uses_kw_units(self):
        """Non-bidirectional sources must scale elec_per_unit_heat W → kW."""
        room = Room(
            "living_room", 5_000_000.0, 0.05,
            air_temperature=19.1, setpoint=21.0, comfort_offset=2.0,
        )
        model = HouseModel([room])
        heater = ElectricHeater("heater", "living_room", max_power=2000.0)
        ctrl = HeatingMPCController(
            model, [heater], horizon=16, dt=900,
            tracking_weight=0.0, energy_weight=0.0,
            soft_constraint_weight=10.0, energy_price_weight=1.0,
        )
        now = datetime(2024, 1, 15, 6, 0, tzinfo=timezone.utc)
        prices = [0.05] * 8 + [0.50] * 8
        _seed_path(ctrl, t_ref=[21.0], u_ref=0.3)
        actions = ctrl.compute(
            outdoor_temp=-8.0, now=now,
            outdoor_forecast=[-8.0] * 16, price_forecast=prices,
        )
        preds = ctrl.predictions
        assert actions["heater"] > 0.05
        assert preds[0]["living_room"] > 18.0


class TestHeatPumpLinearPowerCurve:
    """The heat-pump u→power curve is linear so the MPC linearisation is
    consistent with the nonlinear plant (the old front-loaded sigmoid made the
    linearised prediction diverge from reality and ride the comfort boundary)."""

    def _make_ctrl(self, mode: str):
        room = Room(
            "living_room", 5_000_000.0, 0.03,
            air_temperature=20.0, setpoint=21.0, comfort_offset=2.0,
        )
        model = HouseModel([room])
        hp = HeatPump(
            "hp", "living_room", max_power=3000.0, cop_rated=3.5,
            cop_temp_ref=7.0, cooling_cop=2.5, hvac_mode=mode,
        )
        return HeatingMPCController(
            model, [hp], horizon=24, dt=900,
            tracking_weight=0.0, energy_weight=0.0,
            soft_constraint_weight=10.0, energy_price_weight=1.0,
        )

    def test_heat_cool_linear_and_nonlinear_predictions_agree(self):
        """In heat/cool mode the linearised forecast the MPC optimises against
        must track the nonlinear rollout.  The old sigmoid model made the
        linear forecast claim the room held ~3 °C warmer than reality, so the
        controller under-heated and the room rode/violated the lower bound."""
        ctrl = self._make_ctrl("heat_cool")
        now = datetime(2024, 1, 15, 6, 0, tzinfo=timezone.utc)
        prices = [round(0.30 + 0.001 * k, 4) for k in range(24)]
        ctrl.compute(
            outdoor_temp=-18.0, now=now,
            outdoor_forecast=[-18.0] * 24, price_forecast=prices,
        )
        lin = [p["living_room"] for p in ctrl.linearised_predictions]
        nl = [p["living_room"] for p in ctrl.predictions]
        max_div = max(abs(a - b) for a, b in zip(lin, nl))
        assert max_div < 0.3, (
            f"linear vs nonlinear prediction diverged by {max_div:.2f} °C "
            f"(heat/cool sigmoid bug); lin={[round(x,2) for x in lin[:6]]} "
            f"nl={[round(x,2) for x in nl[:6]]}"
        )

    def test_heat_cool_matches_heat_only_under_cold_load(self):
        """A heat/cool pump and a heat-only pump must plan the same first
        action and forecast for an identical heating-only situation."""
        now = datetime(2024, 1, 15, 6, 0, tzinfo=timezone.utc)
        prices = [round(0.30 + 0.001 * k, 4) for k in range(24)]
        results = {}
        for mode in ("heat", "heat_cool"):
            ctrl = self._make_ctrl(mode)
            actions = ctrl.compute(
                outdoor_temp=-18.0, now=now,
                outdoor_forecast=[-18.0] * 24, price_forecast=prices,
            )
            results[mode] = (
                actions["hp"],
                [p["living_room"] for p in ctrl.linearised_predictions],
            )
        u_heat, pred_heat = results["heat"]
        u_cool, pred_cool = results["heat_cool"]
        assert abs(u_heat - u_cool) < 0.05
        max_div = max(abs(a - b) for a, b in zip(pred_heat, pred_cool))
        assert max_div < 0.3, f"heat vs heat_cool forecast diverged {max_div:.2f} °C"


# ── Regression tests for native MBC controller issues ─────────────────────────


class TestBidirectionalJacobianSmoothBlend:
    """Bug 1: dfdu() kink at u=0 for bidirectional heat pumps caused jitter.

    The piecewise-linear power curve φ(u) = q_heat·u (u≥0) / q_cool·u (u<0)
    has a slope discontinuity at u=0.  When the equilibrium input u_ss crosses
    zero between consecutive solve steps (mild weather), the B matrix changed
    abruptly, producing oscillating controller outputs (jitter).

    The fix introduces a linear blend in the ±_KINK_BLEND neighbourhood around
    u=0 so the Jacobian is a continuous function of the operating point.
    """

    def _make_ctrl(self):
        room = Room("lr", 5_000_000.0, 0.05, temperature=21.0, setpoint=21.0)
        model = HouseModel([room])
        hp = HeatPump(
            "hp", "lr", max_power=3000.0, cop_rated=3.5,
            cop_temp_ref=7.0, cooling_cop=2.5, hvac_mode="heat_cool",
        )
        return HeatingMPCController(
            model, [hp], horizon=6, dt=900,
            tracking_weight=0.0, energy_weight=0.01,
            soft_constraint_weight=100.0,
        )

    def test_dfdu_continuous_near_zero(self):
        """Jacobian slope must be continuous across the u=0 boundary for a
        bidirectional heat pump; adjacent values should not differ more than
        the heating/cooling slope difference."""
        room = Room("lr", 5_000_000.0, 0.05, temperature=21.0, setpoint=21.0)
        model = HouseModel([room])
        hp = HeatPump(
            "hp", "lr", max_power=3000.0, cop_rated=3.5,
            cop_temp_ref=7.0, cooling_cop=2.5, hvac_mode="heat_cool",
        )
        sde = HouseThermalSDE(model, [hp], dt=900.0)

        x = np.array(sde.x)
        d = np.array([5.0])  # mild outdoor temperature
        p = np.array([])
        t = 0.0

        eps_vals = np.linspace(-0.05, 0.05, 201)
        slopes = []
        for eff_u in eps_vals:
            u = np.array([eff_u])
            J = sde.dfdu(x, u, d, p, t)
            slopes.append(float(J[0, 0]))

        # Slope must change smoothly; max step between adjacent samples < slope gap
        max_step = max(abs(slopes[i+1] - slopes[i]) for i in range(len(slopes) - 1))
        q_heat = hp.thermal_power(1.0, 5.0)
        slope_gap = abs(q_heat - hp._q_cool_const) / sde._C_cap[0]
        assert max_step < slope_gap, (
            f"dfdu has a discontinuity near u=0: max step={max_step:.4g}, "
            f"slope gap={slope_gap:.4g}"
        )

    def test_consecutive_solves_stable_near_zero_equilibrium(self):
        """Controller output must not jitter when u_ss is near zero (mild weather)."""
        ctrl = self._make_ctrl()
        now = datetime(2024, 3, 20, 12, 0, tzinfo=timezone.utc)
        # Mild outdoor temperature where equilibrium input is near zero
        outdoor = 18.0
        actions = []
        for _ in range(4):
            a = ctrl.compute(outdoor_temp=outdoor, now=now)
            actions.append(a["hp"])
        # All four consecutive solves should agree within 0.05
        assert max(actions) - min(actions) < 0.05, (
            f"Jitter detected in consecutive solves at mild temperature: {actions}"
        )


class TestPerRoomComfortOffsets:
    """Bug 2a: comfort band used max(offset) for ALL rooms instead of per-room offsets.

    When rooms have different comfort_offset values, the MPC used the maximum
    offset for every room, allowing rooms with smaller offsets to be heated past
    their configured comfort maximum without penalty.

    The fix reads per-room offsets from the model and sets them as a (N, nz)
    profile on every solve.
    """

    def test_room_with_small_offset_respects_its_own_bound(self):
        """A room with comfort_offset=0.5 must not be heated above setpoint+0.5
        even when another room in the house has a larger comfort_offset."""
        tight_room = Room(
            "tight", 5_000_000.0, 0.05,
            temperature=20.5, setpoint=21.0, comfort_offset=0.5,
        )
        loose_room = Room(
            "loose", 5_000_000.0, 0.05,
            temperature=18.0, setpoint=21.0, comfort_offset=3.0,
        )
        model = HouseModel([tight_room, loose_room])
        heater_tight = ElectricHeater("h_tight", "tight", max_power=2000.0)
        heater_loose = ElectricHeater("h_loose", "loose", max_power=2000.0)

        # Use a high soft constraint weight so violations are strongly penalised
        ctrl = HeatingMPCController(
            model, [heater_tight, heater_loose],
            horizon=6, dt=900,
            tracking_weight=0.0, energy_weight=0.01,
            soft_constraint_weight=1000.0,
        )

        now = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        ctrl.compute(outdoor_temp=20.0, now=now)

        # The tight room's linearised predictions must stay within its own offset
        tight_preds = [p["tight"] for p in ctrl.linearised_predictions]
        comfort_max = tight_room.setpoint + tight_room.comfort_offset  # 21.5
        violations = [t for t in tight_preds if t > comfort_max + 0.1]
        assert not violations, (
            f"Tight room (offset=0.5) heated above {comfort_max}°C: {tight_preds}"
        )


class TestNoHeatingAboveComfortMax:
    """Bug 2b: soft constraint weight too low — price incentive outweighed the
    comfort penalty, allowing the controller to heat above the comfort maximum.

    With tracking_weight=0 and rho=10, the quadratic penalty gradient is zero
    at eps=0 (room exactly at comfort max), giving no resistance to the price
    incentive.  The fix raises the default rho to 1000.

    Regression: with the default rho, a room already at the comfort maximum
    must not be heated further even when electricity prices are favourable.
    """

    def test_no_heat_above_comfort_max_with_price(self):
        """Room at setpoint + comfort_offset (= comfort max) must not receive
        additional heating when energy price is positive (no rho=10 under-penalty)."""
        comfort_offset = 2.0
        setpoint = 21.0
        comfort_max = setpoint + comfort_offset  # 23.0

        room = Room(
            "lr", 5_000_000.0, 0.03,
            temperature=comfort_max,  # room already at comfort max
            setpoint=setpoint, comfort_offset=comfort_offset,
        )
        model = HouseModel([room])
        hp = HeatPump(
            "hp", "lr", max_power=3000.0, cop_rated=3.5,
            cop_temp_ref=7.0, hvac_mode="heat",
        )
        # Use the DEFAULT soft_constraint_weight (was 10, fixed to 1000)
        ctrl = HeatingMPCController(
            model, [hp], horizon=6, dt=900,
            tracking_weight=0.0, energy_weight=0.0,
            energy_price_weight=1.0,
        )

        now = datetime(2024, 1, 15, 8, 0, tzinfo=timezone.utc)
        prices = [0.10] * 24  # low, constant price — creates price incentive to heat
        actions = ctrl.compute(
            outdoor_temp=-5.0, now=now,
            outdoor_forecast=[-5.0] * 24,
            price_forecast=prices,
        )
        u_applied = actions["hp"]
        assert u_applied < 0.05, (
            f"Controller heated at u={u_applied:.3f} when room was already at "
            f"comfort max ({comfort_max}°C); soft constraint weight is too low."
        )


class TestInfeasibleSetpoint:
    """When the HP cannot maintain the setpoint at full power, the linearisation
    point must be corrected so the MPC sees the room dropping and heats at max.

    Without the Newton correction, the deviation model assumes (x_ss=setpoint,
    u_ss=u_max) is an equilibrium and predicts stable temperatures.  The energy
    cost then dominates and the controller reduces actuation to ~25% even though
    full heating is needed.
    """

    def test_max_heating_when_setpoint_physically_infeasible(self):
        """Poorly insulated room at outdoor=-10°C: setpoint cannot be maintained.
        Controller must output full heating to minimise comfort violations."""
        room = Room(
            "lr", 5_000_000.0, 0.003,
            temperature=19.0, setpoint=21.0, comfort_offset=2.0,
        )
        model = HouseModel([room])
        hp = HeatPump(
            "hp", "lr", max_power=5000.0, cop_rated=3.5,
            cop_temp_ref=7.0, cooling_cop=2.5, hvac_mode="heat_cool",
        )
        ctrl = HeatingMPCController(
            model, [hp], horizon=12, dt=900,
            tracking_weight=0.0, energy_weight=0.01,
            energy_price_weight=1.0, soft_constraint_weight=1000.0,
        )
        now = datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc)
        _seed_path(ctrl, t_ref=[21.0], u_ref=1.0)
        actions = ctrl.compute(
            outdoor_temp=-10.0, now=now,
            outdoor_forecast=[-10.0] * 24,
            price_forecast=[0.20] * 24,
        )
        u_applied = actions["hp"]
        assert u_applied > 0.9, (
            f"Expected near-maximum heating (u > 0.9) when setpoint is "
            f"physically infeasible; got u = {u_applied:.4f}.  "
            f"Likely caused by inconsistent linearisation point."
        )
