"""
Open-window data-quality handling across system identification.

A window that is open during data collection injects unmodelled air-exchange
loss into a room's temperature trace.  The coordinator now labels every history
record with a per-room ``window_open`` flag (the same predicate that drives the
real-time EKF process-noise inflation), and the identification stack consumes it
*per room*:

* the open-loop MSE objective (``_simulation_mse_and_grad``) drops the affected
  room's residual and pins its air node to the measurement so the coupled
  neighbours still see the true boundary temperature;
* the EKF reconstruction (``run_sysid_ekf``) and the open-loop diagnostic
  (``compute_open_loop_predictions``) render the excluded samples as gaps
  (``measured``/``predicted`` = ``None``) and exclude them from the RMSE/MAE,
  re-anchoring the room from its real reading so the prediction restarts cleanly.

These tests pin that behaviour down.
"""

import numpy as np

from custom_components.heating_assistant.controller import HouseThermalSDE
from custom_components.heating_assistant.heat_sources import ElectricHeater
from custom_components.heating_assistant.thermal_model import HouseModel, Room
from custom_components.heating_assistant.sysid import run_sysid_ekf
from custom_components.heating_assistant.model_diagnostics import (
    compute_open_loop_predictions,
)
from custom_components.heating_assistant.parameter_estimator import (
    KalmanMLEstimator,
    _ThetaLayout,
)


def _room():
    return Room("studio", 4e6, 0.05, temperature=20.0, setpoint=21.0)


def _heater(scale=1.0):
    return ElectricHeater("h", "studio", 3000.0, power_scale=scale)


def _history(n=80, dt=900.0, u=0.6, open_range=None, corrupt_open=False):
    """Single-room history; optionally flag/corrupt an open-window block.

    ``open_range`` is a ``(start, stop)`` half-open step interval that is flagged
    ``window_open: {"studio": True}``.  When ``corrupt_open`` is set those same
    samples get an absurd temperature so a test can prove they are excluded.
    """
    t0 = 1_000_000.0
    history = []
    for i in range(n):
        is_open = open_range is not None and open_range[0] <= i < open_range[1]
        temp = 20.0 + 0.02 * (i % 10)
        if is_open and corrupt_open:
            temp = -50.0  # physically impossible — must not influence the fit
        history.append({
            "y": [temp],
            "u": [u],
            "d_outdoor": 2.0,
            "d_solar": {"studio": 0.0},
            "timestamp": t0 + dt * i,
            "window_open": {"studio": bool(is_open)},
        })
    return history


# ---------------------------------------------------------------------------
# _convert_history_std carries the per-room mask in room order
# ---------------------------------------------------------------------------

def test_convert_history_std_carries_window_open_mask():
    est = KalmanMLEstimator([_room()], [_heater()], dt=900.0)
    history = _history(n=10, open_range=(3, 6))
    std = est._convert_history_std(history, use_ym=True)

    flags = [bool(rec["window_open"][0]) for rec in std]
    assert flags == [False, False, False, True, True, True, False, False, False, False]
    # Records without the key (legacy / seeded history) default to all-closed.
    legacy = est._convert_history_std([
        {"y": [20.0], "u": [0.0], "d_outdoor": 2.0, "d_solar": {}, "timestamp": 0.0}
    ], use_ym=True)
    assert legacy[0]["window_open"].dtype == bool
    assert not legacy[0]["window_open"].any()


# ---------------------------------------------------------------------------
# Open-loop objective ignores corrupted samples flagged window-open
# ---------------------------------------------------------------------------

def _eval_objective(est, history):
    layout = _ThetaLayout(
        n_rooms=1, identifiable_sources=[], identifiable_pairs=[],
    )
    theta = np.concatenate([
        est._log_mass_prior, est._log_r_prior, est._q_int_prior,
        np.array([0.0]),  # t_wall_init
    ])
    std = est._convert_history_std(history, use_ym=True)
    return est._simulation_mse_and_grad(
        theta, layout, std,
        nominal_dt=est._dt,
        max_window_steps=est._max_window_steps,
        min_segment_steps=est._min_segment_steps,
    )


def test_open_loop_objective_excludes_trailing_open_window():
    """A corrupted open-window block at the END of the data (no scored step
    follows it, so the pinned state cannot leak forward) leaves the open-loop
    objective and its gradient bit-for-bit unchanged — the samples are excluded.
    """
    est = KalmanMLEstimator([_room()], [_heater()], dt=900.0)
    clean = _history(n=80, open_range=(60, 80), corrupt_open=False)
    corrupt = _history(n=80, open_range=(60, 80), corrupt_open=True)

    mse_clean, grad_clean = _eval_objective(est, clean)
    mse_corrupt, grad_corrupt = _eval_objective(est, corrupt)

    assert np.isclose(mse_clean, mse_corrupt, rtol=0, atol=1e-9)
    assert np.allclose(grad_clean, grad_corrupt, rtol=0, atol=1e-9)


def test_open_loop_objective_uses_unflagged_corruption():
    """Control: the very same corruption WITHOUT the window-open flag does move
    the objective — proving the flag (not some other masking) is what excludes.
    """
    est = KalmanMLEstimator([_room()], [_heater()], dt=900.0)
    clean = _history(n=80, open_range=(60, 80), corrupt_open=False)

    # Corrupt the trailing block but label it closed (window_open False).
    unflagged = _history(n=80, open_range=(60, 80), corrupt_open=True)
    for rec in unflagged:
        rec["window_open"] = {"studio": False}

    mse_clean, _ = _eval_objective(est, clean)
    mse_unflagged, _ = _eval_objective(est, unflagged)
    assert not np.isclose(mse_clean, mse_unflagged, atol=1e-6)


# ---------------------------------------------------------------------------
# EKF reconstruction renders gaps and excludes them from the RMSE
# ---------------------------------------------------------------------------

def _ekf(history, dt=900.0):
    return run_sysid_ekf(
        history, HouseModel([_room()]), [_heater()], ["studio"], dt,
        horizon_steps=len(history), room_params={}, sigma_w=0.1, sigma_v=0.5,
    )["per_room"]["studio"]


def test_ekf_reconstruction_gaps_open_window():
    """Every open-window sample becomes a null gap in both series."""
    dt = 900.0
    history = _history(n=40, open_range=(20, 30), corrupt_open=True)
    sim = _ekf(history)["simulation"]
    t0 = history[0]["timestamp"]

    for entry in sim:
        step = round((entry["time"] - t0) / dt)
        if 20 <= step < 30:
            assert entry["measured"] is None, f"step {step} measured not gapped"
            assert entry["predicted"] is None, f"step {step} predicted not gapped"
        else:
            assert entry["measured"] is not None
            assert entry["predicted"] is not None


def test_ekf_reconstruction_excludes_open_window_from_rmse():
    """A corrupted *trailing* open-window block (nothing scored after it) leaves
    the RMSE/MAE identical to the clean run — the samples never enter the error.
    """
    clean = _history(n=40, open_range=(30, 40), corrupt_open=False)
    corrupt = _history(n=40, open_range=(30, 40), corrupt_open=True)
    rc, rk = _ekf(clean), _ekf(corrupt)
    assert rc["rmse"] is not None and rk["rmse"] is not None
    assert np.isclose(rc["rmse"], rk["rmse"], rtol=0, atol=1e-9)
    assert np.isclose(rc["mae"], rk["mae"], rtol=0, atol=1e-9)


def test_ekf_reconstruction_reanchors_after_window():
    """After the window closes the prediction restarts from the room's *real*
    reading (re-anchored during the gap), not from a stale pre-window free-run.
    """
    dt = 900.0
    t0 = 1_000_000.0
    history = []
    for i in range(40):
        is_open = 20 <= i < 30
        # Real trace: a genuine 5 °C cooling dip while the window is open that
        # persists just past it, so a model that ignored the dip would mispredict.
        temp = 15.0 if 20 <= i < 32 else 20.0
        history.append({
            "y": [temp], "u": [0.0], "d_outdoor": 2.0, "d_solar": {"studio": 0.0},
            "timestamp": t0 + dt * i, "window_open": {"studio": bool(is_open)},
        })
    sim = _ekf(history)["simulation"]
    # First scored step after the window (step 30) must track the real ~15 °C,
    # which is only possible if the state was re-anchored through the gap.
    step30 = next(e for e in sim if round((e["time"] - t0) / dt) == 30)
    assert step30["predicted"] is not None
    # Re-anchored → closer to the real 15 °C dip than to the pre-window 20 °C a
    # stale (non-anchored) free-run would have held.  (The wall node's lag keeps
    # it a little above 15 °C, which is physically correct.)
    assert abs(step30["predicted"] - 15.0) < abs(step30["predicted"] - 20.0)


# ---------------------------------------------------------------------------
# Open-loop diagnostic renders gaps and excludes them from the RMSE
# ---------------------------------------------------------------------------

def _open_loop(history, dt=900.0):
    system = HouseThermalSDE(HouseModel([_room()]), [_heater()], dt)
    return compute_open_loop_predictions(
        history=history, system=system, room_names=["studio"],
        n_rooms=1, dt=dt, segment_length=None,
    )["per_room"]["studio"]


def test_open_loop_diagnostic_gaps_open_window():
    dt = 900.0
    history = _history(n=40, open_range=(20, 30), corrupt_open=True)
    sim = _open_loop(history)["simulation"]
    t0 = history[0]["timestamp"]

    gapped = [e for e in sim if e["measured"] is None]
    assert gapped, "expected gap entries for the open-window block"
    for entry in gapped:
        step = round((entry["time"] - t0) / dt)
        assert 20 <= step < 30
        assert entry["predicted"] is None


def test_open_loop_diagnostic_excludes_open_window_from_rmse():
    """Trailing corrupt open-window block leaves the open-loop RMSE unchanged."""
    clean = _history(n=40, open_range=(30, 40), corrupt_open=False)
    corrupt = _history(n=40, open_range=(30, 40), corrupt_open=True)
    rc, rk = _open_loop(clean), _open_loop(corrupt)
    assert rc["rmse"] is not None and rk["rmse"] is not None
    assert np.isclose(rc["rmse"], rk["rmse"], rtol=0, atol=1e-9)
