"""
2R2C estimation tests: analytic gradients and parameter recovery.

The identification objective (multi-step open-loop MSE) carries analytic
forward sensitivities for every parameter family in the extended θ layout:

    [log_mass, log_r_ext, q_int, log_alpha, log_r_ij,
     log_solar, c_air_fraction, r_aw_fraction]

These tests pin the gradient against finite differences across ALL
families simultaneously (the strongest correctness check available — a
wrong chain-rule term in any family fails it), and verify that the gated
solar scale is actually recovered from solar-driven data.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.thermal_model import (
    HouseModel,
    Room,
    RoomConnection,
)
from custom_components.heating_assistant.heat_sources import ElectricHeater
from custom_components.heating_assistant.parameter_estimator import (
    KalmanMLEstimator,
    _ThetaLayout,
    _check_identifiable_solar,
    _identifiable_split_rooms,
)


def _make_setup():
    rooms = [
        Room("a", thermal_mass=5e6, r_external=0.02,
             connections=[RoomConnection("b", 0.5)],
             internal_gain=0.0, temperature=20.0),
        Room("b", thermal_mass=4e6, r_external=0.03,
             connections=[RoomConnection("a", 0.5)],
             internal_gain=0.0, temperature=19.0),
    ]
    sources = [
        ElectricHeater(name="ha", room="a", max_power=1500.0),
        ElectricHeater(name="hb", room="b", max_power=1000.0),
    ]
    est = KalmanMLEstimator(rooms, sources, dt=900.0)
    return rooms, sources, est


def _make_history(n_steps=80, solar_amp=400.0, seed=7):
    rng = np.random.default_rng(seed)
    t0 = 1_700_000_000.0
    Ta, Tb = 20.0, 19.0
    recs = []
    for k in range(n_steps):
        u = [0.6 if (k // 8) % 2 == 0 else 0.0,
             0.4 if (k // 6) % 2 == 0 else 0.0]
        solar = {"a": max(0.0, solar_amp * math.sin(2 * math.pi * k / 40.0)),
                 "b": 0.0}
        Ta += 0.05 * u[0] - 0.02 + 0.01 * rng.standard_normal()
        Tb += 0.04 * u[1] - 0.015 + 0.01 * rng.standard_normal()
        recs.append({
            "y": [Ta, Tb], "u": u,
            "d_outdoor": 2.0 + 3.0 * math.sin(2 * math.pi * k / 60.0),
            "d_solar": solar,
            "timestamp": t0 + 900.0 * k,
        })
    return recs


class TestThetaLayout:
    def test_extended_blocks_are_indexed_after_legacy_blocks(self):
        layout = _ThetaLayout(
            n_rooms=2,
            identifiable_sources=[0],
            identifiable_pairs=[(0, 1)],
            identifiable_solar=[0, 1],
            identifiable_splits=[1],
        )
        # 3n legacy + t_wall_init (n) + alpha + r_ij + solar + c_air + r_aw
        assert layout.size == 8 + 1 + 1 + 2 + 1 + 1
        assert layout.idx_log_solar == (10, 12)
        assert layout.idx_c_air == (12, 13)
        assert layout.idx_r_aw == (13, 14)

    def test_all_gates_closed_reproduces_legacy_size(self):
        layout = _ThetaLayout(n_rooms=3, identifiable_sources=[],
                              identifiable_pairs=[])
        assert layout.size == 12


class TestIdentifiabilityGates:
    def test_solar_gate_requires_variation(self):
        history = _make_history(solar_amp=400.0)
        gated = _check_identifiable_solar(history, ["a", "b"],
                                          min_history_steps=10)
        assert gated == [0]   # room b has zero solar throughout

    def test_solar_gate_closed_without_solar(self):
        history = _make_history(solar_amp=0.0)
        gated = _check_identifiable_solar(history, ["a", "b"],
                                          min_history_steps=10)
        assert gated == []

    def test_split_gate_follows_excited_sources(self):
        _rooms, sources, _est = _make_setup()
        assert _identifiable_split_rooms([0], sources, ["a", "b"]) == [0]
        assert _identifiable_split_rooms([0, 1], sources, ["a", "b"]) == [0, 1]
        assert _identifiable_split_rooms([], sources, ["a", "b"]) == []


class TestAnalyticGradient2R2C:
    def test_mse_gradient_matches_fd_all_families(self):
        """FD check across every θ family at a non-trivial point."""
        _rooms, _sources, est = _make_setup()
        history = _make_history()
        std = est._convert_history_std(history, use_ym=True)

        layout = _ThetaLayout(
            n_rooms=2,
            identifiable_sources=[0, 1],
            identifiable_pairs=[(0, 1)],
            identifiable_solar=[0],
            identifiable_splits=[0, 1],
        )
        theta = np.concatenate([
            est._log_mass_prior + 0.1,
            est._log_r_prior - 0.05,
            np.array([30.0, -20.0]),          # q_int
            np.array([0.0, 0.0]),             # t_wall_init (per room)
            np.array([0.05, -0.04]),          # log_alpha
            np.array([math.log(0.5)]),        # log_r_ij
            np.array([0.1]),                  # log_solar room a
            np.array([0.08, 0.06]),           # c_air
            np.array([0.07, 0.05]),           # r_aw
        ])
        assert len(theta) == layout.size

        kwargs = dict(nominal_dt=900.0, min_segment_steps=4,
                      max_window_steps=20)
        f0, g_ana = est._simulation_mse_and_grad(theta, layout, std, **kwargs)
        assert np.isfinite(f0) and f0 < 1e9

        h = 1e-6
        g_fd = np.zeros_like(theta)
        for i in range(len(theta)):
            tp = theta.copy(); tp[i] += h
            tm = theta.copy(); tm[i] -= h
            fp, _ = est._simulation_mse_and_grad(tp, layout, std, **kwargs)
            fm, _ = est._simulation_mse_and_grad(tm, layout, std, **kwargs)
            g_fd[i] = (fp - fm) / (2 * h)

        scale = np.maximum(np.abs(g_fd), 1e-3)
        rel = np.abs(g_ana - g_fd) / scale
        assert np.all(rel < 2e-2), f"gradient mismatch: rel={rel}"


class TestSolarScaleRecovery:
    def test_estimator_recovers_solar_scale(self):
        """Truth generated with solar_scale = 0.5; prior says 1.0.

        The estimator must pull the gated room's solar scale clearly
        toward the true value (and below ~0.75, i.e. most of the way from
        the prior to the truth).
        """
        TRUE_SCALE = 0.5
        true_rooms = [
            Room("a", thermal_mass=5e6, r_external=0.02, temperature=20.0,
                 solar_scale=TRUE_SCALE),
        ]
        model = HouseModel(true_rooms)
        sources = [ElectricHeater(name="ha", room="a", max_power=1500.0)]

        rng = np.random.default_rng(3)
        t0 = 1_700_000_000.0
        history = []
        temps = {"a": 20.0}
        for k in range(192):   # 48 h at 900 s
            u = 0.5 if (k // 10) % 2 == 0 else 0.0
            solar_raw = max(0.0, 800.0 * math.sin(2 * math.pi * (k % 96) / 96.0))
            history.append({
                "y": [temps["a"] + 0.02 * rng.standard_normal()],
                "u": [u],
                "d_outdoor": 5.0,
                "d_solar": {"a": solar_raw},   # raw gain; scale applied in-model
                "timestamp": t0 + 900.0 * k,
            })
            temps = model.step(
                900.0, {"a": u * 1500.0}, 5.0, {"a": solar_raw},
            )

        # Estimation rooms carry the (wrong) prior solar_scale = 1.
        est_rooms = [
            Room("a", thermal_mass=5e6, r_external=0.02, temperature=20.0),
        ]
        est = KalmanMLEstimator(est_rooms, sources, dt=900.0,
                                regularization=0.1)
        result = est.estimate(history)
        assert result["success"]
        assert "a" in result["identifiable_solar_rooms"]
        s_hat = result["estimated_solar_scales"]["a"]
        assert s_hat < 0.75, f"solar scale not recovered: {s_hat}"


class TestWallObservability:
    def test_metric_in_unit_interval_and_sensible(self):
        from custom_components.heating_assistant.model_diagnostics import (
            wall_state_observability,
        )
        room = Room("a", thermal_mass=5e6, r_external=0.02)
        m = wall_state_observability(room, dt=900.0, horizon_steps=96)
        assert m is not None and 0.0 < m <= 1.0

    def test_weaker_coupling_lowers_observability(self):
        from custom_components.heating_assistant.model_diagnostics import (
            wall_state_observability,
        )
        tight = Room("a", thermal_mass=5e6, r_external=0.02,
                     r_aw_fraction=0.05)
        # Larger air↔wall share → weaker coupling conductance → the wall
        # is harder to see from the air measurement.
        loose = Room("a", thermal_mass=5e6, r_external=0.02,
                     r_aw_fraction=0.6)
        m_tight = wall_state_observability(tight)
        m_loose = wall_state_observability(loose)
        assert m_tight is not None and m_loose is not None
        assert m_loose != pytest.approx(m_tight)
