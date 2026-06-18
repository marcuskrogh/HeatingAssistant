"""Unit tests for optimal control problems across all formulation types."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.controller import HouseThermalSDE
from custom_components.heating_assistant.heat_sources import ElectricHeater
from custom_components.heating_assistant.mbc.control import (
    CDOptimalControlProblem,
    CDLinearizedOptimalControlProblem,
    CDTrackingOptimalControlProblem,
    OptimalControlProblem,
)
from custom_components.heating_assistant.thermal_model import HouseModel, Room
from tests.ocp_fixtures import FirstOrderCDModel, FirstOrderDiscreteModel
from tests.test_controller import _aug_state, _make_model_and_sources


def _stack_disturbance(d_scalar: float, N: int, nd: int = 1) -> np.ndarray:
    return np.tile(np.array([d_scalar] * nd, dtype=float), N)


def _ocp_common_kwargs():
    return dict(
        N=4,
        Q=np.array([[10.0]]),
        R=np.array([[0.01]]),
        P=np.array([[50.0]]),
        rho=1e4,
        y_offset=1.0,
    )


class TestDiscreteLinearOCP:
    """OptimalControlProblem — discrete linear QP."""

    def _make_ocp(self, **kwargs):
        model = FirstOrderDiscreteModel(x0=18.0, x_ref=22.0)
        kw = _ocp_common_kwargs()
        kw.update(kwargs)
        return OptimalControlProblem(model=model, **kw), model

    def test_setpoint_tracking_heats_when_cold(self):
        ocp, model = self._make_ocp()
        D = _stack_disturbance(5.0, ocp._N)
        U, _ = ocp.solve(x0=model.x, D=D, x_ref=model.x_ref)
        assert U[0] > 0.1

    def test_hard_input_bounds(self):
        ocp, model = self._make_ocp()
        D = _stack_disturbance(-10.0, ocp._N)
        U, _ = ocp.solve(x0=model.x, D=D, x_ref=model.x_ref)
        assert np.all(U >= -1e-9)
        assert np.all(U <= 1.0 + 1e-9)

    def test_soft_output_tighter_band_increases_heating(self):
        """Tighter soft output band (higher rho) drives more heating when below setpoint."""
        ocp_loose, model = self._make_ocp(y_offset=5.0, rho=1e3)
        ocp_tight, _ = self._make_ocp(y_offset=0.5, rho=1e5)
        D = _stack_disturbance(5.0, ocp_loose._N)
        U_loose, _ = ocp_loose.solve(x0=model.x, D=D, x_ref=model.x_ref)
        U_tight, _ = ocp_tight.solve(x0=model.x, D=D, x_ref=model.x_ref)
        assert U_tight[0] >= U_loose[0] - 1e-6

    def test_hard_rate_constraints_limit_step(self):
        ocp, model = self._make_ocp(du_max=np.array([0.05]))
        D = _stack_disturbance(5.0, ocp._N)
        u_prev = np.array([0.0])
        U, _ = ocp.solve(x0=model.x, D=D, x_ref=model.x_ref, u_prev=u_prev)
        assert U[0] <= 0.05 + 1e-6

    def test_rate_penalty_smooths_inputs(self):
        ocp_smooth, model = self._make_ocp(S=np.array([[5.0]]))
        ocp_plain, _ = self._make_ocp()
        D = _stack_disturbance(5.0, ocp_smooth._N)
        u_prev = np.array([0.0])
        U_s, _ = ocp_smooth.solve(
            x0=model.x, D=D, x_ref=model.x_ref, u_prev=u_prev
        )
        U_p, _ = ocp_plain.solve(
            x0=model.x, D=D, x_ref=model.x_ref, u_prev=u_prev
        )
        assert U_s[0] <= U_p[0] + 1e-6

    @pytest.mark.parametrize("formulation", ["condensed", "sparse"])
    def test_formulations_agree_on_rate_box(self, formulation):
        kwargs = dict(du_max=np.array([0.1]), formulation=formulation)
        ocp, model = self._make_ocp(**kwargs)
        D = _stack_disturbance(5.0, ocp._N)
        U, _ = ocp.solve(
            x0=model.x, D=D, x_ref=model.x_ref, u_prev=np.array([0.0])
        )
        assert U[0] <= 0.1 + 1e-5


class TestContinuousLinearOCP:
    """CDOptimalControlProblem — continuous linear ZOH-QP."""

    def _make_ocp(self, **kwargs):
        model = FirstOrderCDModel(x0=18.0, x_ref=22.0, dt=900.0)
        kw = _ocp_common_kwargs()
        kw.update(kwargs)
        return CDOptimalControlProblem(model=model, **kw), model

    def test_setpoint_tracking(self):
        ocp, model = self._make_ocp()
        D = _stack_disturbance(5.0, ocp._N)
        U, _ = ocp.solve(x0=model.x, D=D, x_ref=model.x_ref)
        assert U[0] > 0.05

    def test_hard_input_and_rate_constraints(self):
        ocp, model = self._make_ocp(du_max=np.array([0.08]))
        D = _stack_disturbance(5.0, ocp._N)
        U, _ = ocp.solve(
            x0=model.x, D=D, x_ref=model.x_ref, u_prev=np.array([0.0])
        )
        assert 0.0 <= U[0] <= 0.08 + 1e-5

    def test_rate_penalty_smooths_inputs(self):
        ocp_pen, model = self._make_ocp(S=np.array([[2.0]]))
        ocp_plain, _ = self._make_ocp()
        D = _stack_disturbance(5.0, ocp_pen._N)
        u_prev = np.array([0.0])
        U_pen, _ = ocp_pen.solve(
            x0=model.x, D=D, x_ref=model.x_ref, u_prev=u_prev
        )
        U_plain, _ = ocp_plain.solve(
            x0=model.x, D=D, x_ref=model.x_ref, u_prev=u_prev
        )
        assert U_pen[0] <= U_plain[0] + 1e-6


class TestDiscreteLinearizedOCP:
    """CDLinearizedOptimalControlProblem — one-shot linearised QP."""

    def _make_house_ocp(self, **kwargs):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        kw = dict(
            N=3,
            Q=np.eye(sde.nz),
            R=0.01 * np.eye(sde.nu),
            rho=1e4,
            y_offset=2.0,
            z_ref=sde.x_ref,
            dt=900.0,
        )
        kw.update(kwargs)
        return CDLinearizedOptimalControlProblem(sde, **kw), sde

    def test_solve_shapes(self):
        ocp, sde = self._make_house_ocp()
        d = sde.disturbance_vector(0.0, {})
        D = np.tile(d, ocp.N)
        U, X = ocp.solve(_aug_state([17.0, 16.0]), D, u_prev=np.zeros(sde.nu))
        assert U.shape == (ocp.N * sde.nu,)
        assert X.shape == (ocp.N * sde.nx,)

    def test_heats_when_cold(self):
        ocp, sde = self._make_house_ocp()
        d = sde.disturbance_vector(-5.0, {})
        D = np.tile(d, ocp.N)
        U, _ = ocp.solve(_aug_state([16.0, 15.0]), D, u_prev=np.zeros(sde.nu))
        assert U.reshape(ocp.N, sde.nu)[0].sum() > 0.0

    def test_respects_input_bounds(self):
        ocp, sde = self._make_house_ocp(du_max=0.15 * np.ones(2))
        d = sde.disturbance_vector(-5.0, {})
        D = np.tile(d, ocp.N)
        U, _ = ocp.solve(_aug_state([16.0, 15.0]), D, u_prev=np.zeros(sde.nu))
        U2 = U.reshape(ocp.N, sde.nu)
        assert np.all(U2 >= -1e-9)
        assert np.all(U2 <= 1.0 + 1e-9)
        assert np.all(U2[0] - np.zeros(sde.nu) <= 0.15 + 1e-5)

    def test_rate_penalty_reduces_first_step(self):
        ocp_pen, sde = self._make_house_ocp(S=5.0 * np.eye(2))
        ocp_plain, _ = self._make_house_ocp()
        d = sde.disturbance_vector(-5.0, {})
        D = np.tile(d, ocp_pen.N)
        x0 = _aug_state([16.0, 15.0])
        u0 = np.zeros(sde.nu)
        U_pen, _ = ocp_pen.solve(x0, D, u_prev=u0)
        U_plain, _ = ocp_plain.solve(x0, D, u_prev=u0)
        assert U_pen.reshape(ocp_pen.N, sde.nu)[0].sum() <= (
            U_plain.reshape(ocp_plain.N, sde.nu)[0].sum() + 1e-6
        )


class TestContinuousNonlinearOCP:
    """CDTrackingOptimalControlProblem — nonlinear NLP."""

    def _make_ocp(self, **kwargs):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        kw = dict(
            N=3,
            Q=np.eye(sde.nz),
            R=0.01 * np.eye(sde.nu),
            z_ref=sde.x_ref,
            u_min=np.zeros(sde.nu),
            u_max=np.ones(sde.nu),
            dt=900.0,
            n_steps=5,
        )
        kw.update(kwargs)
        return CDTrackingOptimalControlProblem(sde, **kw), sde

    def test_setpoint_tracking(self):
        ocp, sde = self._make_ocp()
        d = sde.disturbance_vector(-10.0, {})
        d_traj = np.tile(d, (ocp.N, 1))
        u_opt, _, _ = ocp.solve(_aug_state([15.0, 14.0]), d_traj)
        assert u_opt[0].sum() > 0.0

    def test_hard_input_bounds(self):
        ocp, sde = self._make_ocp()
        d = sde.disturbance_vector(-10.0, {})
        d_traj = np.tile(d, (ocp.N, 1))
        u_opt, _, _ = ocp.solve(_aug_state([15.0, 14.0]), d_traj)
        assert np.all(u_opt >= -1e-9)
        assert np.all(u_opt <= 1.0 + 1e-9)

    def test_hard_rate_constraints(self):
        model, sources = _make_model_and_sources()
        sde = HouseThermalSDE(model, sources, dt=900.0)
        ocp = CDTrackingOptimalControlProblem(
            sde,
            N=3,
            Q=np.eye(sde.nz),
            R=0.01 * np.eye(sde.nu),
            z_ref=sde.x_ref,
            u_min=np.zeros(sde.nu),
            u_max=np.ones(sde.nu),
            du_max=0.1 * np.ones(sde.nu),
            dt=900.0,
            n_steps=5,
        )
        d = sde.disturbance_vector(-10.0, {})
        d_traj = np.tile(d, (ocp.N, 1))
        u_opt, _, _ = ocp.solve(
            _aug_state([15.0, 14.0]), d_traj, u_prev=np.zeros(sde.nu)
        )
        assert np.all(u_opt[0] <= 0.1 + 1e-5)

    def test_soft_output_via_z_band(self):
        living = Room("living_room", 5e6, 0.05, temperature=25.0, setpoint=21.0)
        bedroom = Room("bedroom", 3e6, 0.08, temperature=24.0, setpoint=20.0)
        model = HouseModel([living, bedroom])
        sources = [
            ElectricHeater("lr_heater", "living_room", max_power=2000.0),
            ElectricHeater("br_heater", "bedroom", max_power=1500.0),
        ]
        sde = HouseThermalSDE(model, sources, dt=900.0)
        z_ref = sde.x_ref
        half = 1.0
        ocp = CDTrackingOptimalControlProblem(
            sde,
            N=3,
            Q=np.eye(sde.nz),
            R=0.001 * np.eye(sde.nu),
            z_ref=z_ref,
            z_min=z_ref - half,
            z_max=z_ref + half,
            rho_z=1e5,
            u_min=np.zeros(sde.nu),
            u_max=np.ones(sde.nu),
            dt=900.0,
            n_steps=5,
        )
        d = sde.disturbance_vector(22.0, {})
        d_traj = np.tile(d, (ocp.N, 1))
        u_opt, _, _ = ocp.solve(_aug_state([25.0, 24.0]), d_traj)
        assert u_opt[0].sum() < 0.1

    def test_rate_penalty(self):
        ocp_pen, sde = self._make_ocp(S=10.0 * np.eye(2))
        ocp_plain, _ = self._make_ocp()
        d = sde.disturbance_vector(-10.0, {})
        d_traj = np.tile(d, (ocp_pen.N, 1))
        u_pen, _, _ = ocp_pen.solve(
            _aug_state([15.0, 14.0]), d_traj, u_prev=np.zeros(sde.nu)
        )
        u_plain, _, _ = ocp_plain.solve(
            _aug_state([15.0, 14.0]), d_traj, u_prev=np.zeros(sde.nu)
        )
        assert u_pen[0].sum() <= u_plain[0].sum() + 1e-6
