"""Regression tests for spurious HiGHS QP failures.

HiGHS's active-set QP solver (observed up to 1.14) can return "Solve error"
or "Unbounded" on well-posed strictly convex QPs.  Both failure modes were
captured from real MPC solves of :class:`~mbc.control.StandardLinearDiscreteOCP` (single room,
single source): the QP minimises input effort plus soft comfort-corridor
slack, with the slack columns unbounded above.

When the backend silently fails, ``StandardLinearDiscreteOCP.solve`` falls back to
``U_dev = 0``, i.e. a constant ``u_ss`` plan over the whole horizon that sits
strictly inside the input bounds — the controller "runs and finishes" but
never actually optimises.  These tests pin the retry ladder in
:class:`HighsQPBackend` that rescues both failure modes.
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.mbc.control.qp_solver import (
    HighsQPBackend,
    QPProblem,
)


def _mpc_qp(imp, r_diag, q_u, lb_u, ub_u, h_vec):
    """Rebuild a captured forecast-aware linear discrete OCP QP from its generating data.

    Decision vector is ``[U (N); ε (N)]``.  ``imp`` is the first column of
    the lower-triangular Toeplitz input-to-output map CG (impulse response),
    ``r_diag`` the diagonal of the input-cost band (off-diagonal −0.1 from
    the move-suppression term), ``q_u`` the gradient on U, and ``h_vec`` the
    soft-corridor right-hand side ``[−z_min + Z_free; z_max − Z_free]``.
    """
    N = len(imp)
    CG = np.zeros((N, N))
    for k in range(N):
        for j in range(k + 1):
            CG[k, j] = imp[k - j]

    H_uu = np.diag(np.asarray(r_diag, dtype=float))
    for k in range(N - 1):
        H_uu[k, k + 1] = -0.1
        H_uu[k + 1, k] = -0.1

    P = np.zeros((2 * N, 2 * N))
    P[:N, :N] = H_uu
    P[N:, N:] = 10.0 * np.eye(N)

    q = np.concatenate([q_u, np.zeros(N)])
    lb = np.concatenate([np.full(N, lb_u), np.zeros(N)])
    ub = np.concatenate([np.full(N, ub_u), np.full(N, np.inf)])

    G = np.vstack([
        np.hstack([-CG, -np.eye(N)]),
        np.hstack([CG, -np.eye(N)]),
    ])
    return QPProblem(P=P, q=q, lb=lb, ub=ub, G=G, h=np.asarray(h_vec))


# Captured QP that HiGHS 1.14 fails with "Solve error" when the ε columns
# are boxed at 1e10 (the pre-fix blanket workaround) — it solves fine with
# true infinities, so the first ladder rung must pass bounds as given.
_SOLVE_ERROR_QP = _mpc_qp(
    imp=[23.624962138287845, 9.199172536107078,
         4.304280738975483, 2.640468019684139],
    r_diag=[0.21000000000000002] * 3 + [0.11],
    q_u=[-0.09457281498870614, 0.00049338045557217,
         0.00049338045557217, 0.00049338045557217],
    lb_u=-1.049338045557217,
    ub_u=0.950661954442783,
    h_vec=[-8.049655171212489, -9.484199282953137,
           -9.940555176445674, -10.06540738241326,
           12.049655171212489, 13.484199282953137,
           13.940555176445674, 14.06540738241326],
)

# Captured QP that HiGHS 1.14 misreports as "Unbounded" although P is
# positive definite (min eig ≈ 0.016) — it stays "Unbounded" even with every
# column boxed, and only solves after the objective is uniformly rescaled.
_UNBOUNDED_QP = _mpc_qp(
    imp=[5.129825958004026, 1.6468494322759057, 0.7443869924656084,
         0.5094612891343226, 0.447221471561638, 0.42965901448668853],
    r_diag=[0.21000000000000002] * 5 + [0.11],
    q_u=[-0.04743894616310035] + [0.0021] * 5,
    lb_u=-0.2100000000000001,
    ub_u=0.7899999999999999,
    h_vec=[-3.060594976972447, -3.5812863887206525, -3.696175405385711,
           -3.7061828702972024, -3.6891508508013686, -3.665215184097888,
           7.060594976972447, 7.5812863887206525, 7.696175405385711,
           7.706182870297202, 7.689150850801369, 7.665215184097888],
)


def _assert_solved(problem: QPProblem, ref_obj: float):
    result = HighsQPBackend().solve(problem)
    assert result.success, f"QP failed with status '{result.status}'"
    x = result.x
    assert np.all(x >= problem.lb - 1e-6)
    assert np.all(x[np.isfinite(problem.ub)]
                  <= problem.ub[np.isfinite(problem.ub)] + 1e-6)
    assert np.all(problem.G @ x <= problem.h + 1e-6)
    assert abs(result.obj - ref_obj) < 1e-4


class TestSpuriousHighsFailures:
    def test_solve_error_case_recovers(self):
        # Reference optimum cross-checked with scipy SLSQP.
        _assert_solved(_SOLVE_ERROR_QP, -0.0307018717)

    def test_unbounded_misreport_recovers(self):
        # Reference optimum cross-checked with scipy SLSQP.
        _assert_solved(_UNBOUNDED_QP, 0.0014126915)
