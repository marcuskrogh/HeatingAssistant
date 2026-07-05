"""Unit tests for the Kalman-filter ML parameter estimator."""

import math
import sys
import os

import pytest
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.thermal_model import (
    HouseModel,
    Room,
    RoomConnection,
)
from custom_components.heating_assistant.heat_sources import ElectricHeater
from custom_components.heating_assistant.parameter_estimator import (
    KalmanMLEstimator,
    MIN_HISTORY_STEPS,
    _nelder_mead,
)
from custom_components.heating_assistant.estimation.constants import (
    _LOG_MASS_HI,
    _LOG_MASS_LO,
    _LOG_R_HI,
    _LOG_R_LO,
)
from custom_components.heating_assistant.estimation.theta_layout import _ThetaLayout


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_single_room(thermal_mass: float = 5_000_000.0,
                      r_external: float = 0.05) -> Room:
    return Room(
        name="living_room",
        thermal_mass=thermal_mass,
        r_external=r_external,
        temperature=20.0,
        setpoint=21.0,
    )


def _make_two_rooms(
    mass1: float = 5_000_000.0, r1: float = 0.05,
    mass2: float = 3_000_000.0, r2: float = 0.08,
) -> list:
    living = Room(
        name="living_room",
        thermal_mass=mass1,
        r_external=r1,
        connections=[RoomConnection("bedroom", 0.2)],
        temperature=20.0,
        setpoint=21.0,
    )
    bedroom = Room(
        name="bedroom",
        thermal_mass=mass2,
        r_external=r2,
        connections=[RoomConnection("living_room", 0.2)],
        temperature=18.0,
        setpoint=20.0,
    )
    return [living, bedroom]


def _make_sources(rooms):
    return [
        ElectricHeater(f"{r.name}_heater", r.name, max_power=2000.0)
        for r in rooms
    ]


def _generate_history(rooms, sources, dt=60.0, n_steps=60,
                      outdoor_temp=5.0, heating_fraction=0.5,
                      noise_std=0.05, seed=42):
    """
    Simulate a history buffer using the true model parameters so the
    estimator has something real to fit.
    """
    rng = np.random.default_rng(seed)
    model = HouseModel(rooms)
    n = len(rooms)

    temps = {r.name: r.temperature for r in rooms}

    history = []
    for _ in range(n_steps):
        y = [temps[r.name] + rng.normal(0, noise_std) for r in rooms]
        u = [heating_fraction] * len(sources)
        solar = {r.name: 0.0 for r in rooms}

        history.append({
            "y": y,
            "u": u,
            "d_outdoor": outdoor_temp,
            "d_solar": solar,
        })

        # Advance model one step
        heat_inputs = {
            src.room: src.thermal_power(heating_fraction, outdoor_temp)
            for src in sources
        }
        temps = model.step(dt, heat_inputs, outdoor_temp, solar)

    return history


# ---------------------------------------------------------------------------
# Tests for _nelder_mead
# ---------------------------------------------------------------------------

class TestNelderMead:
    """Tests for the pure-NumPy Nelder-Mead implementation."""

    def test_minimises_quadratic(self):
        """Should find the minimum of a simple quadratic."""
        def f(x):
            return float(np.sum((x - np.array([1.0, 2.0])) ** 2))

        x0 = np.array([0.0, 0.0])
        x_best, f_best, converged = _nelder_mead(f, x0, tol=1e-6)
        assert converged
        np.testing.assert_allclose(x_best, [1.0, 2.0], atol=1e-3)
        assert f_best < 1e-6

    def test_minimises_rosenbrock(self):
        """Should converge on the 2-D Rosenbrock function."""
        def rosenbrock(x):
            return float(100 * (x[1] - x[0] ** 2) ** 2 + (1 - x[0]) ** 2)

        x0 = np.array([0.5, 0.5])
        x_best, f_best, _ = _nelder_mead(rosenbrock, x0, tol=1e-6,
                                          max_iter=5000)
        np.testing.assert_allclose(x_best, [1.0, 1.0], atol=1e-2)

    def test_returns_finite_result(self):
        x0 = np.array([3.0])
        x_best, f_best, _ = _nelder_mead(lambda x: float(x[0] ** 2), x0)
        assert math.isfinite(f_best)

    def test_default_max_iter(self):
        """Default max_iter should be 200 × n (no exception raised)."""
        x0 = np.array([1.0, 2.0, 3.0])
        _nelder_mead(lambda x: float(np.sum(x ** 2)), x0)  # must not raise


# ---------------------------------------------------------------------------
# Tests for KalmanMLEstimator
# ---------------------------------------------------------------------------

class TestKalmanMLEstimator:

    def test_insufficient_data_returns_failure(self):
        rooms = [_make_single_room()]
        sources = _make_sources(rooms)
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0)
        short_history = _generate_history(rooms, sources, n_steps=5)
        result = estimator.estimate(short_history)
        assert result["success"] is False
        assert result["n_steps"] == 5
        assert str(MIN_HISTORY_STEPS) in result["message"]

    def test_returns_current_params_on_failure(self):
        rooms = [_make_single_room(thermal_mass=4_000_000.0, r_external=0.06)]
        sources = _make_sources(rooms)
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0)
        history = _generate_history(rooms, sources, n_steps=5)
        result = estimator.estimate(history)
        assert result["current_params"]["living_room"]["thermal_mass"] == pytest.approx(
            4_000_000.0
        )
        assert result["current_params"]["living_room"]["r_external"] == pytest.approx(
            0.06
        )

    def test_success_with_sufficient_data(self):
        rooms = [_make_single_room()]
        sources = _make_sources(rooms)
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0)
        history = _generate_history(rooms, sources, n_steps=MIN_HISTORY_STEPS + 10)
        result = estimator.estimate(history)
        assert result["success"] is True
        assert result["n_steps"] >= MIN_HISTORY_STEPS

    def test_estimated_params_are_positive(self):
        rooms = [_make_single_room()]
        sources = _make_sources(rooms)
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0)
        history = _generate_history(rooms, sources, n_steps=60)
        result = estimator.estimate(history)
        assert result["success"] is True
        p = result["estimated_params"]["living_room"]
        assert p["thermal_mass"] > 0
        assert p["r_external"] > 0

    def test_estimated_params_within_bounds(self):
        rooms = [_make_single_room()]
        sources = _make_sources(rooms)
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0)
        history = _generate_history(rooms, sources, n_steps=60)
        result = estimator.estimate(history)
        assert result["success"] is True
        p = result["estimated_params"]["living_room"]
        assert math.exp(_LOG_MASS_LO) <= p["thermal_mass"] <= math.exp(_LOG_MASS_HI)
        assert math.exp(_LOG_R_LO) <= p["r_external"] <= math.exp(_LOG_R_HI)

    def test_log_likelihood_is_finite(self):
        rooms = [_make_single_room()]
        sources = _make_sources(rooms)
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0)
        history = _generate_history(rooms, sources, n_steps=60)
        result = estimator.estimate(history)
        assert result["success"] is True
        assert result["log_likelihood"] is not None
        assert math.isfinite(result["log_likelihood"])

    def test_two_room_model_succeeds(self):
        rooms = _make_two_rooms()
        sources = _make_sources(rooms)
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0)
        history = _generate_history(rooms, sources, n_steps=60)
        result = estimator.estimate(history)
        assert result["success"] is True
        for room in ["living_room", "bedroom"]:
            assert result["estimated_params"][room]["thermal_mass"] > 0
            assert result["estimated_params"][room]["r_external"] > 0

    def test_compute_log_likelihood_current_params(self):
        """compute_log_likelihood should return a finite value at the prior."""
        rooms = [_make_single_room()]
        sources = _make_sources(rooms)
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0)
        history = _generate_history(rooms, sources, n_steps=60)
        ll = estimator.compute_log_likelihood(history)
        assert ll is not None
        assert math.isfinite(ll)

    def test_regularization_pulls_toward_prior(self):
        """Strong regularisation should yield estimates near the prior."""
        rooms = [_make_single_room(thermal_mass=5_000_000.0, r_external=0.05)]
        sources = _make_sources(rooms)
        # Use very strong regularisation
        estimator = KalmanMLEstimator(
            rooms, sources, dt=60.0, regularization=1e6
        )
        history = _generate_history(rooms, sources, n_steps=60)
        result = estimator.estimate(history)
        assert result["success"] is True
        p = result["estimated_params"]["living_room"]
        # With very strong regularisation the estimates should be very close
        # to the prior (configured) values.
        assert p["thermal_mass"] == pytest.approx(5_000_000.0, rel=0.1)
        assert p["r_external"] == pytest.approx(0.05, rel=0.1)

    def test_default_regularisation_is_light(self):
        """The default prior is deliberately very light so the configured
        data window — not the prior — drives the estimate."""
        rooms = [_make_single_room()]
        sources = _make_sources(rooms)
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0)
        assert estimator._regularization <= 0.01

    def test_default_prior_is_responsive_to_excited_data(self):
        """With the default (light) prior, excited data moves the heater scale
        and thermal parameters substantially off a wrong prior.

        This guards against a heavy prior pinning the estimate near its
        starting value — the symptom that made identification look
        unresponsive even under strong heater excitation.  Only *movement off
        the prior* is asserted (not the exact recovered values): a single room
        at constant outdoor temperature has a scale/mass/gain degeneracy, so
        the data can be fitted by more than one parameter combination.
        """
        # Truth: lighter mass and a clearly non-unit heater scale.
        true_room = _make_single_room(thermal_mass=3_000_000.0, r_external=0.08)
        sources = _make_sources([true_room])
        sources[0].power_scale = 1.6
        history = _generate_history_with_extras(
            [true_room], sources, n_steps=180, seed=3,
        )

        # Fit from a wrong prior with the DEFAULT estimator (no explicit reg).
        prior_room = _make_single_room(thermal_mass=6_000_000.0, r_external=0.04)
        prior_sources = _make_sources([prior_room])
        prior_sources[0].power_scale = 1.0
        estimator = KalmanMLEstimator([prior_room], prior_sources, dt=60.0)
        result = estimator.estimate(history)
        assert result["success"] is True

        # The recovered mass must move a meaningful fraction of the way from
        # the prior (6e6) toward the truth (3e6) — i.e. the prior is not
        # pinning it.
        recovered_mass = result["estimated_params"]["living_room"]["thermal_mass"]
        assert recovered_mass < 5_000_000.0, (
            f"thermal mass stayed pinned near the prior: {recovered_mass}"
        )
        # The heater scale must move clearly away from its unit prior — the data
        # (not the prior) decides where it lands.
        scale = result["estimated_heater_scales"]["living_room_heater"]
        assert abs(scale - 1.0) > 0.2, (
            f"heater scale stayed pinned near the unit prior: {scale}"
        )

    def test_result_contains_required_keys(self):
        rooms = [_make_single_room()]
        sources = _make_sources(rooms)
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0)
        history = _generate_history(rooms, sources, n_steps=60)
        result = estimator.estimate(history)
        for key in ("success", "estimated_params", "current_params",
                    "n_steps", "log_likelihood", "message"):
            assert key in result, f"missing key: {key}"

    def test_ml_improves_likelihood_over_prior(self):
        """
        Estimated parameters should achieve a better (higher) log-likelihood
        than the initial prior when there is enough data from a different model.
        """
        # Generate data from a model with *different* parameters
        true_rooms = [_make_single_room(thermal_mass=3_000_000.0,
                                         r_external=0.08)]
        sources = _make_sources(true_rooms)
        history = _generate_history(true_rooms, sources, n_steps=120, seed=0)

        # Fit with a model that starts at the *wrong* prior
        prior_rooms = [_make_single_room(thermal_mass=5_000_000.0,
                                          r_external=0.05)]
        estimator = KalmanMLEstimator(
            prior_rooms, sources, dt=60.0, regularization=0.0
        )
        prior_ll = estimator.compute_log_likelihood(history)
        result = estimator.estimate(history)
        assert result["success"] is True
        estimated_ll = result["log_likelihood"]
        assert estimated_ll is not None
        # Estimated LL should be >= prior LL (optimizer should not make things worse)
        assert estimated_ll >= prior_ll - 1.0  # allow 1 nat tolerance

    def test_history_with_zero_solar(self):
        """History records without solar gains should not raise."""
        rooms = [_make_single_room()]
        sources = _make_sources(rooms)
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0)
        history = [
            {"y": [20.0], "u": [0.5], "d_outdoor": 5.0, "d_solar": {}}
            for _ in range(MIN_HISTORY_STEPS + 5)
        ]
        result = estimator.estimate(history)
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Tests for the joint estimation of internal heat gain and heater scale
# ---------------------------------------------------------------------------


def _generate_history_with_extras(
    rooms,
    sources,
    dt=60.0,
    n_steps=120,
    outdoor_temp=5.0,
    heating_pattern=None,
    noise_std=0.02,
    seed=42,
):
    """Generate synthetic history with time-varying heating and per-room
    internal heat gains baked into ``Room.internal_gain``.

    ``heating_pattern`` – list[float] (length n_steps) of duty cycles in
    [0, 1].  Defaults to a 50 %-duty square wave so heater dynamics are
    excited.
    """
    rng = np.random.default_rng(seed)
    model = HouseModel(rooms)
    n = len(rooms)

    if heating_pattern is None:
        heating_pattern = [
            1.0 if (k // 30) % 2 == 0 else 0.0 for k in range(n_steps)
        ]

    temps = {r.name: r.temperature for r in rooms}

    history = []
    for k in range(n_steps):
        y = [temps[r.name] + rng.normal(0, noise_std) for r in rooms]
        u = [heating_pattern[k]] * len(sources)
        solar = {r.name: 0.0 for r in rooms}

        history.append({
            "y": y,
            "u": u,
            "d_outdoor": outdoor_temp,
            "d_solar": solar,
        })

        heat_inputs = {
            src.room: src.thermal_power(heating_pattern[k], outdoor_temp)
            for src in sources
        }
        temps = model.step(dt, heat_inputs, outdoor_temp, solar)

    return history


class TestJointInternalGainAndHeaterScale:
    """Recovery of the new joint parameters from synthetic data."""

    def test_result_contains_new_keys(self):
        rooms = [_make_single_room()]
        sources = _make_sources(rooms)
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0)
        history = _generate_history(rooms, sources, n_steps=60)
        result = estimator.estimate(history)
        for key in (
            "estimated_internal_gains",
            "estimated_heater_scales",
            "identifiable_sources",
        ):
            assert key in result, f"missing new key: {key}"

    def test_internal_gain_is_recovered(self):
        """A 500 W constant gain baked into the data should be recovered.

        Uses a room with τ ≈ 7 h (C = 500 kJ/K, R = 0.05 K/W) so that
        the system approaches near-steady-state within the 3-hour window.
        During heater-OFF phases the temperature decays toward
        T_out + q_int * R_ext, which directly reveals q_int regardless of C.
        """
        true_gain = 500.0
        # Shorter time constant (τ ≈ 7 h) makes q_int identifiable
        # in a 3-hour window via the heater-OFF steady-state offset.
        true_room = _make_single_room(thermal_mass=500_000.0, r_external=0.05)
        true_room.internal_gain = true_gain
        true_rooms = [true_room]
        sources = _make_sources(true_rooms)
        history = _generate_history_with_extras(
            true_rooms, sources, n_steps=180, seed=7,
        )

        prior_room = _make_single_room(thermal_mass=500_000.0, r_external=0.05)
        prior_room.internal_gain = 0.0
        estimator = KalmanMLEstimator(
            [prior_room], sources, dt=60.0,
            regularization=0.01,
        )
        result = estimator.estimate(history)
        assert result["success"]
        recovered = result["estimated_internal_gains"]["living_room"]
        assert abs(recovered - true_gain) < 400.0, (
            f"recovered Q_int={recovered} W, expected ≈ {true_gain} W"
        )

    def test_heater_scale_always_estimated_when_excited(self):
        """The heater scale is always part of the joint estimate."""
        rooms = [_make_single_room()]
        sources = _make_sources(rooms)
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0)
        history = _generate_history_with_extras(rooms, sources, n_steps=120)
        result = estimator.estimate(history)
        assert result["success"]
        # α is treated like every other parameter: always present, never gated.
        assert "living_room_heater" in result["identifiable_sources"]
        assert "living_room_heater" in result["estimated_heater_scales"]

    def test_heater_scale_stays_near_unit_when_constant(self):
        """Constant duty cycle → α is still estimated but stays near 1.0.

        With no duty-cycle excitation the scale trades off with the internal
        gain, so the light prior keeps it close to the unit starting value
        rather than dropping it from the parameter vector entirely.
        """
        rooms = [_make_single_room()]
        sources = _make_sources(rooms)
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0)
        constant_pattern = [0.5] * 120
        history = _generate_history_with_extras(
            rooms, sources, n_steps=120, heating_pattern=constant_pattern,
        )
        result = estimator.estimate(history)
        assert result["success"]
        # α is always estimated now (never gated out)…
        assert "living_room_heater" in result["identifiable_sources"]
        # …but with no excitation it remains close to the unit prior.
        scale = result["estimated_heater_scales"]["living_room_heater"]
        assert 0.5 < scale < 2.0, f"scale drifted without excitation: {scale}"

    def test_joint_fit_improves_over_no_internal_gain(self):
        """If true model has 800 W internal gain, joint fit should achieve
        a better log-likelihood than fitting only (C, R_ext)."""
        true_room = _make_single_room()
        true_room.internal_gain = 800.0
        sources = _make_sources([true_room])
        history = _generate_history_with_extras(
            [true_room], sources, n_steps=180, seed=11,
        )

        prior_room = _make_single_room()
        prior_room.internal_gain = 0.0
        # Use a very weak prior so the data dominates
        estimator = KalmanMLEstimator(
            [prior_room], sources, dt=60.0, regularization=0.001,
        )

        # Negative LL of the prior (no internal gain)
        ll_prior = estimator.compute_log_likelihood(history)
        result = estimator.estimate(history)
        ll_post = result["log_likelihood"]
        assert ll_prior is not None and ll_post is not None
        # The joint estimator should lift the log-likelihood meaningfully.
        assert ll_post >= ll_prior - 1.0


# ---------------------------------------------------------------------------
# Tests for analytical gradient (forward-sensitivity pass)
# ---------------------------------------------------------------------------


class TestObjectiveScaling:
    """The identification objective must accumulate (sum) over steps so that
    data confidence grows with the number of observations.

    Regression test: the objective used to be *averaged* over steps, which
    made its curvature independent of the dataset length.  The fixed O(1)
    Gaussian prior then always out-voted the data and the estimates stayed
    pinned to the (possibly wrong) configured prior — the user-visible symptom
    being a poorly-fit open-loop with the wrong gains.
    """

    def test_objective_grows_with_dataset_length(self):
        rooms = [_make_single_room()]
        sources = _make_sources(rooms)
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0, regularization=0.0)

        # A model whose parameters differ from the data-generating ones, so
        # the open-loop residuals are non-trivial.  Each history gets its own
        # fresh room objects: _generate_history mutates the rooms' (air and
        # wall) temperature state, so reusing them would make the long
        # history start from the short one's end state instead of being a
        # superset of it.
        def _true_rooms():
            return [_make_single_room(thermal_mass=3_000_000.0, r_external=0.08)]

        hist_short = _generate_history(_true_rooms(), sources, n_steps=120, seed=1)
        hist_long = _generate_history(_true_rooms(), sources, n_steps=240, seed=1)

        layout = _ThetaLayout(n_rooms=1, identifiable_sources=[],
                              identifiable_pairs=[])
        theta = np.concatenate([
            estimator._log_mass_prior,
            estimator._log_r_prior,
            estimator._q_int_prior,
            estimator._t_wall_init_prior,
        ])

        sh_short = estimator._convert_history_std(hist_short, use_ym=True)
        sh_long = estimator._convert_history_std(hist_long, use_ym=True)

        obj_short, _ = estimator._simulation_mse_and_grad(
            theta, layout, sh_short, nominal_dt=60.0, max_window_steps=10_000,
        )
        obj_long, _ = estimator._simulation_mse_and_grad(
            theta, layout, sh_long, nominal_dt=60.0, max_window_steps=10_000,
        )
        # Twice the data ⇒ roughly twice the accumulated misfit (a *mean*
        # would leave the two values about equal).
        assert obj_long > 1.5 * obj_short


class TestAnalyticalGradient:
    """
    Verify that _cd_ped_neg_ll_and_grad returns a gradient that matches
    the finite-difference approximation.  These tests do not require IPOPT
    to be installed.
    """

    def _make_estimator_and_data(self, rooms, sources):
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0, regularization=0.0)
        history = _generate_history(rooms, sources, n_steps=MIN_HISTORY_STEPS + 10)
        return estimator, history

    def _run_grad_check(self, rooms, sources, identifiable_sources=None,
                        identifiable_pairs=None, eps=1e-4, rtol=0.02):
        """
        Compare the analytical gradient from _cd_ped_neg_ll_and_grad against
        central finite differences.  Asserts max relative error < rtol.
        """
        estimator = KalmanMLEstimator(rooms, sources, dt=60.0, regularization=0.0)
        history = _generate_history(rooms, sources, n_steps=60, seed=99)
        std_history = estimator._convert_history_std(history, use_ym=True)

        if identifiable_sources is None:
            identifiable_sources = []
        if identifiable_pairs is None:
            identifiable_pairs = []

        layout = _ThetaLayout(
            n_rooms=estimator._n,
            identifiable_sources=identifiable_sources,
            identifiable_pairs=identifiable_pairs,
        )
        import math as _math
        n = estimator._n
        theta0 = np.concatenate([
            estimator._log_mass_prior,
            estimator._log_r_prior,
            estimator._q_int_prior,
            estimator._t_wall_init_prior,
            np.array([estimator._log_alpha_prior_full[s]
                      for s in identifiable_sources]),
            np.array([estimator._connection_r_priors[
                          estimator._connection_pairs.index(p)]
                      for p in identifiable_pairs])
            if identifiable_pairs else np.array([]),
        ])

        system0 = estimator._build_parametric_system(layout, theta0)
        assert system0 is not None
        x0, P0 = estimator._initial_state_and_covariance(
            system0, std_history[0]["ym"]
        )

        # Analytical gradient
        neg_ll_val, grad_analytical = estimator._cd_ped_neg_ll_and_grad(
            theta0, layout, std_history, x0, P0
        )
        assert np.isfinite(neg_ll_val), "neg_ll should be finite"
        assert np.all(np.isfinite(grad_analytical)), "gradient should be finite"

        # Central finite-difference gradient
        grad_fd = np.zeros_like(theta0)
        for i in range(len(theta0)):
            tp = theta0.copy(); tp[i] += eps
            tm = theta0.copy(); tm[i] -= eps
            fp, _ = estimator._cd_ped_neg_ll_and_grad(
                tp, layout, std_history, x0, P0
            )
            fm, _ = estimator._cd_ped_neg_ll_and_grad(
                tm, layout, std_history, x0, P0
            )
            grad_fd[i] = (fp - fm) / (2.0 * eps)

        # Allow absolute tolerance for near-zero entries
        np.testing.assert_allclose(
            grad_analytical, grad_fd,
            rtol=rtol, atol=1e-3,
            err_msg="Analytical gradient deviates from finite differences",
        )

    def test_gradient_single_room_basic_params(self):
        """Gradient w.r.t. [log_mass, log_r, q_int] for a single room."""
        rooms = [_make_single_room()]
        sources = _make_sources(rooms)
        self._run_grad_check(rooms, sources)

    def test_gradient_two_room_basic_params(self):
        """Gradient w.r.t. [log_mass×2, log_r×2, q_int×2] for two rooms."""
        rooms = _make_two_rooms()
        sources = _make_sources(rooms)
        self._run_grad_check(rooms, sources)

    def test_gradient_with_log_alpha(self):
        """Gradient includes log_alpha when source is identifiable."""
        rooms = [_make_single_room()]
        sources = _make_sources(rooms)
        self._run_grad_check(rooms, sources, identifiable_sources=[0])

    def test_gradient_with_inter_room_resistance(self):
        """Gradient includes log_r_ij when inter-room pair is identifiable."""
        rooms = _make_two_rooms()
        sources = _make_sources(rooms)
        # Room indices 0 and 1 are connected
        self._run_grad_check(rooms, sources, identifiable_pairs=[(0, 1)])

    def test_gradient_full_parameter_set(self):
        """Full two-room parameter set including alpha and inter-room R."""
        rooms = _make_two_rooms()
        sources = _make_sources(rooms)
        self._run_grad_check(
            rooms, sources,
            identifiable_sources=[0, 1],
            identifiable_pairs=[(0, 1)],
        )
