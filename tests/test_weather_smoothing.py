import math
from heatingassistant.engine.weather import smooth_cloud_cover_step as step

def test_seeds_on_first_observation():
    assert step(None, 0.5, 900.0, 1800.0) == 0.5

def test_holds_previous_when_observation_missing():
    assert step(0.4, None, 900.0, 1800.0) == 0.4
    assert step(None, None, 900.0, 1800.0) is None

def test_clamps_observation_to_unit_interval():
    assert step(None, 1.5, 900.0, 1800.0) == 1.0
    assert step(None, -0.2, 900.0, 1800.0) == 0.0

def test_ema_moves_fraction_toward_observation():
    # dt == tau -> alpha = 1 - e^-1 ~= 0.632
    out = step(0.0, 1.0, 1800.0, 1800.0)
    assert math.isclose(out, 1.0 - math.exp(-1.0), rel_tol=1e-9)

def test_ema_alpha_value():
    out = step(0.0, 1.0, 900.0, 1800.0)  # alpha = 1 - e^-0.5
    assert math.isclose(out, 1.0 - math.exp(-0.5), rel_tol=1e-9)

def test_repeated_steps_converge():
    v = None
    for _ in range(50):
        v = step(v, 0.8, 900.0, 1800.0)
    assert math.isclose(v, 0.8, abs_tol=1e-3)

def test_zero_tau_disables_smoothing():
    assert step(0.2, 0.9, 900.0, 0.0) == 0.9

def test_smoothing_lags_a_sudden_jump():
    # A jump 0.3 -> 0.9 should not be reached in one step (it is smoothed).
    out = step(0.3, 0.9, 900.0, 1800.0)
    assert 0.3 < out < 0.9
