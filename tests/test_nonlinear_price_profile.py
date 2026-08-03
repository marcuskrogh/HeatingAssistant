"""Tests for nonlinear MPC horizon-varying price costs."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from custom_components.heating_assistant.controller.facade import HeatingNonlinearMPC


def test_nonlinear_mpc_price_cost_preserves_per_step_profile():
    """The nonlinear OCP price term must not collapse the horizon to a mean price."""
    mpc = object.__new__(HeatingNonlinearMPC)
    mpc._N = 2
    mpc._dt = 900.0
    mpc._model = SimpleNamespace(
        nu=1,
        nx=2,
        u_bounds=(np.array([0.0]), np.array([1.0])),
    )
    mpc._ocp = SimpleNamespace(
        _p_u_eco="stale",
        _lagrange="stale",
        _lagrange_jac="stale",
    )

    HeatingNonlinearMPC._configure_price(
        mpc,
        price_seq=np.array([0.10, 0.30]),
        elec_heat=np.array([1.0]),
        elec_cool=None,
        bid_mask=np.array([False]),
        price_weight=1.0,
        dt_h=0.25,
    )

    assert mpc._ocp._p_u_eco is None
    u = np.array([3600.0])
    first_step_cost = mpc._ocp._lagrange(450.0, None, None, u, None)
    second_step_cost = mpc._ocp._lagrange(1350.0, None, None, u, None)
    assert first_step_cost == pytest.approx(0.10 * 1e-3)
    assert second_step_cost == pytest.approx(0.30 * 1e-3)
    assert second_step_cost == pytest.approx(first_step_cost * 3.0)

    _, _, grad_u = mpc._ocp._lagrange_jac(1350.0, None, None, u, None)
    assert grad_u == pytest.approx(np.array([0.30 * 1e-3 / 3600.0]))
