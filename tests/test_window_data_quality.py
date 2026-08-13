"""SWD-322: open-window samples are flagged and excluded from offline PE / ID fits.

Per-room ``window_open`` masks (set when heater override is active) must:
* carry through history → standardised PE records
* leave the open-loop objective unchanged when only flagged samples are corrupted
* gap EKF / open-loop diagnostic charts and keep RMSE free of those samples
"""

from __future__ import annotations

import numpy as np
import pytest

from heatingassistant.engine.controller import HouseThermalSDE
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.model_diagnostics import compute_open_loop_predictions
from heatingassistant.engine.parameter_estimator import KalmanMLEstimator, _ThetaLayout
from heatingassistant.engine.sysid import run_sysid_ekf
from heatingassistant.engine.thermal_model import HouseModel, Room


pytestmark = pytest.mark.unit


def _room(name: str = "studio"):
    return Room(name, 4e6, 0.05, temperature=20.0, setpoint=21.0)


def _heater(room: str = "studio", scale: float = 1.0):
    return ElectricHeater("h", room, 3000.0, power_scale=scale)


def _history(
    n: int = 80,
    dt: float = 900.0,
    u: float = 0.6,
    open_range=None,
    corrupt_open: bool = False,
    room: str = "studio",
):
    """Single-room history; optionally flag/corrupt an open-window block.

    ``open_range`` is a ``(start, stop)`` half-open step interval that is flagged
    ``window_open: {room: True}``.  When ``corrupt_open`` is set those same
    samples get an absurd temperature so a test can prove they are excluded.
    """
    t0 = 1_000_000.0
    history = []
    for i in range(n):
        is_open = open_range is not None and open_range[0] <= i < open_range[1]
        temp = 20.0 + 0.02 * (i % 10)
        if is_open and corrupt_open:
            temp = -50.0  # physically impossible — must not influence the fit
        history.append(
            {
                "y": [temp],
                "u": [u],
                "d_outdoor": 2.0,
                "d_solar": {room: 0.0},
                "timestamp": t0 + dt * i,
                "window_open": {room: bool(is_open)},
            }
        )
    return history


def test_convert_history_std_carries_window_open_mask():
    est = KalmanMLEstimator([_room()], [_heater()], dt=900.0)
    history = _history(n=10, open_range=(3, 6))
    std = est._convert_history_std(history, use_ym=True)

    flags = [bool(rec["window_open"][0]) for rec in std]
    assert flags == [False, False, False, True, True, True, False, False, False, False]
    # Records without the key (legacy / seeded history) default to all-closed.
    legacy = est._convert_history_std(
        [
            {
                "y": [20.0],
                "u": [0.0],
                "d_outdoor": 2.0,
                "d_solar": {},
                "timestamp": 0.0,
            }
        ],
        use_ym=True,
    )
    assert legacy[0]["window_open"].dtype == bool
    assert not legacy[0]["window_open"].any()


def _eval_objective(est, history):
    layout = _ThetaLayout(
        n_rooms=1,
        identifiable_sources=[],
        identifiable_pairs=[],
    )
    theta = np.concatenate(
        [
            est._log_mass_prior,
            est._log_r_prior,
            est._q_int_prior,
            np.array([0.0]),  # t_wall_init
        ]
    )
    std = est._convert_history_std(history, use_ym=True)
    return est._simulation_mse_and_grad(
        theta,
        layout,
        std,
        nominal_dt=est._dt,
        max_window_steps=est._max_window_steps,
        min_segment_steps=est._min_segment_steps,
    )


def test_open_loop_objective_excludes_trailing_open_window():
    """Corrupted trailing open-window samples leave MSE/grad unchanged."""
    est = KalmanMLEstimator([_room()], [_heater()], dt=900.0)
    clean = _history(n=80, open_range=(60, 80), corrupt_open=False)
    corrupt = _history(n=80, open_range=(60, 80), corrupt_open=True)

    mse_clean, grad_clean = _eval_objective(est, clean)
    mse_corrupt, grad_corrupt = _eval_objective(est, corrupt)

    assert np.isclose(mse_clean, mse_corrupt, rtol=0, atol=1e-9)
    assert np.allclose(grad_clean, grad_corrupt, rtol=0, atol=1e-9)


def test_open_loop_objective_uses_unflagged_corruption():
    """Control: same corruption without the flag *does* move the objective."""
    est = KalmanMLEstimator([_room()], [_heater()], dt=900.0)
    clean = _history(n=80, open_range=(60, 80), corrupt_open=False)

    unflagged = _history(n=80, open_range=(60, 80), corrupt_open=True)
    for rec in unflagged:
        rec["window_open"] = {"studio": False}

    mse_clean, _ = _eval_objective(est, clean)
    mse_unflagged, _ = _eval_objective(est, unflagged)
    assert not np.isclose(mse_clean, mse_unflagged, atol=1e-6)


def test_open_loop_objective_excludes_only_flagged_room():
    """Two rooms: corrupt open-window on room A must not change room B's fit."""
    rooms = [_room("a"), _room("b")]
    heaters = [_heater("a"), ElectricHeater("hb", "b", 3000.0, power_scale=1.0)]
    est = KalmanMLEstimator(rooms, heaters, dt=900.0)

    t0 = 1_000_000.0
    dt = 900.0
    n = 60

    def _two_room(corrupt_a: bool):
        history = []
        for i in range(n):
            a_open = 40 <= i < 60
            ta = -50.0 if (a_open and corrupt_a) else (20.0 + 0.01 * i)
            tb = 21.0 + 0.02 * (i % 7)
            history.append(
                {
                    "y": [ta, tb],
                    "u": [0.5, 0.5],
                    "d_outdoor": 2.0,
                    "d_solar": {"a": 0.0, "b": 0.0},
                    "timestamp": t0 + dt * i,
                    "window_open": {"a": a_open, "b": False},
                }
            )
        return history

    layout = _ThetaLayout(
        n_rooms=2,
        identifiable_sources=[],
        identifiable_pairs=[],
    )
    theta = np.concatenate(
        [
            est._log_mass_prior,
            est._log_r_prior,
            est._q_int_prior,
            np.zeros(2),
        ]
    )

    def _mse(history):
        std = est._convert_history_std(history, use_ym=True)
        mse, _ = est._simulation_mse_and_grad(
            theta,
            layout,
            std,
            nominal_dt=est._dt,
            max_window_steps=est._max_window_steps,
            min_segment_steps=est._min_segment_steps,
        )
        return mse

    assert np.isclose(
        _mse(_two_room(False)),
        _mse(_two_room(True)),
        rtol=0,
        atol=1e-9,
    )


def _ekf(history, dt=900.0):
    return run_sysid_ekf(
        history,
        HouseModel([_room()]),
        [_heater()],
        ["studio"],
        dt,
        horizon_steps=len(history),
        room_params={},
        sigma_w=0.1,
        sigma_v=0.5,
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
    """Trailing corrupt open-window block leaves RMSE/MAE identical."""
    clean = _history(n=40, open_range=(30, 40), corrupt_open=False)
    corrupt = _history(n=40, open_range=(30, 40), corrupt_open=True)
    rc, rk = _ekf(clean), _ekf(corrupt)
    assert rc["rmse"] is not None and rk["rmse"] is not None
    assert np.isclose(rc["rmse"], rk["rmse"], rtol=0, atol=1e-9)
    assert np.isclose(rc["mae"], rk["mae"], rtol=0, atol=1e-9)


def test_ekf_reconstruction_reanchors_after_window():
    """After the window closes, prediction restarts from the real reading."""
    dt = 900.0
    t0 = 1_000_000.0
    history = []
    for i in range(40):
        is_open = 20 <= i < 30
        temp = 15.0 if 20 <= i < 32 else 20.0
        history.append(
            {
                "y": [temp],
                "u": [0.0],
                "d_outdoor": 2.0,
                "d_solar": {"studio": 0.0},
                "timestamp": t0 + dt * i,
                "window_open": {"studio": bool(is_open)},
            }
        )
    sim = _ekf(history)["simulation"]
    step30 = next(e for e in sim if round((e["time"] - t0) / dt) == 30)
    assert step30["predicted"] is not None
    assert abs(step30["predicted"] - 15.0) < abs(step30["predicted"] - 20.0)


def _open_loop(history, dt=900.0):
    system = HouseThermalSDE(HouseModel([_room()]), [_heater()], dt)
    return compute_open_loop_predictions(
        history=history,
        system=system,
        room_names=["studio"],
        n_rooms=1,
        dt=dt,
        segment_length=None,
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
