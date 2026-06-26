"""
Regression tests for sysid_results cache consistency across ML estimation and
EKF simulation.

``handle_estimate_ml`` stores identified parameters (t_wall_initial,
internal_gain, heater_scales, …) per room.  ``handle_run_sysid_simulation``
must merge simulation diagnostics into those entries instead of replacing the
whole per-room dict (which erased the identified fields).
"""

import asyncio
import math
import warnings
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.heating_assistant import (
    _compute_open_loop_rmse_by_horizon,
    _merge_per_room_into_sysid_results,
    _open_loop_t_wall_initial_dict,
)
from custom_components.heating_assistant.controller import HouseThermalSDE
from custom_components.heating_assistant.heat_sources import ElectricHeater
from custom_components.heating_assistant.model_diagnostics import (
    compute_open_loop_predictions,
)
from custom_components.heating_assistant.parameter_estimator import KalmanMLEstimator
from custom_components.heating_assistant.sysid import _build_sim_model
from custom_components.heating_assistant.thermal_model import HouseModel, Room


def _ml_identified_cache():
    """Simulate sysid_results after handle_estimate_ml has run."""
    return {
        "Living Room": {
            "thermal_mass": 5.5e6,
            "r_external": 0.06,
            "internal_gain": 180.0,
            "t_wall_initial": 19.8,
            "heater_scales": {"lr_radiator": 0.92},
            "horizon_steps": 96,
        },
    }


def _ekf_simulation_per_room():
    """Simulate per_room returned by run_sysid_simulation."""
    return {
        "Living Room": {
            "simulation": [{"predicted": 20.1, "measured": 20.0}],
            "rmse": 0.15,
            "mae": 0.12,
            "horizon_steps": 24,
        },
    }


def test_ekf_simulation_merge_preserves_ml_identified_fields():
    cache = _ml_identified_cache()
    per_room = _ekf_simulation_per_room()

    _merge_per_room_into_sysid_results(cache, per_room)

    room = cache["Living Room"]
    assert room["internal_gain"] == 180.0
    assert room["t_wall_initial"] == 19.8
    assert room["heater_scales"] == {"lr_radiator": 0.92}
    assert room["thermal_mass"] == 5.5e6
    assert room["r_external"] == 0.06
    assert room["simulation"] == [{"predicted": 20.1, "measured": 20.0}]
    assert room["rmse"] == 0.15
    assert room["mae"] == 0.12
    assert room["horizon_steps"] == 24


def test_bulk_update_would_erase_ml_identified_fields():
    """Document the pre-fix failure mode: dict.update replaces whole entries."""
    cache = _ml_identified_cache()
    per_room = _ekf_simulation_per_room()

    cache.update(per_room)

    room = cache["Living Room"]
    assert "internal_gain" not in room
    assert "t_wall_initial" not in room
    assert "heater_scales" not in room
    assert room["rmse"] == 0.15


def _coordinator_with_sysid(sysid_results, room_names):
    return SimpleNamespace(
        model=SimpleNamespace(room_names=room_names),
        sysid_results=sysid_results,
    )


def test_open_loop_uses_cached_t_wall_initial_without_fast_estimate():
    """Cached ML t_wall_initial is used; fast estimate must not overwrite it."""
    coordinator = _coordinator_with_sysid(
        {"Living Room": {"t_wall_initial": 19.8}},
        ["Living Room", "Bedroom"],
    )
    room_params: dict = {}

    identified = _open_loop_t_wall_initial_dict(room_params, coordinator)
    assert identified == {"Living Room": 19.8}
    assert room_params["Living Room"]["t_wall_initial"] == 19.8

    missing = [n for n in coordinator.model.room_names if n not in identified]
    assert missing == ["Bedroom"]

    # Simulate fast estimate returning values for all rooms.
    fast_estimated = {"Living Room": 21.0, "Bedroom": 18.5}
    merged = _open_loop_t_wall_initial_dict(
        room_params, coordinator, fast_estimated
    )
    assert merged["Living Room"] == 19.8
    assert merged["Bedroom"] == 18.5


def test_open_loop_skips_fast_estimate_when_all_rooms_cached():
    coordinator = _coordinator_with_sysid(
        {
            "Living Room": {"t_wall_initial": 19.8},
            "Bedroom": {"t_wall_initial": 18.2},
        },
        ["Living Room", "Bedroom"],
    )
    identified = _open_loop_t_wall_initial_dict({}, coordinator)
    missing = [n for n in coordinator.model.room_names if n not in identified]
    assert identified == {"Living Room": 19.8, "Bedroom": 18.2}
    assert missing == []


def test_open_loop_multi_horizon_passes_t_wall_initial():
    """Segmented horizon RMSE runs must receive the resolved t_wall_initial."""
    captured: list = []

    def _fake_compute(*args):
        captured.append(args)
        return {"per_room": {"Living Room": {"rmse": 0.2}}}

    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(side_effect=lambda fn, *args: fn(*args))
    )
    t_wall = {"Living Room": 19.8}
    history = [{"y": [20.0], "u": [0.0], "timestamp": 0.0}]
    system = object()

    async def _run():
        with patch(
            "custom_components.heating_assistant.model_diagnostics.compute_open_loop_predictions",
            _fake_compute,
        ):
            await _compute_open_loop_rmse_by_horizon(
                hass,
                history,
                system,
                ["Living Room"],
                1,
                900.0,
                t_wall,
            )

    asyncio.run(_run())

    assert len(captured) == 3
    for args in captured:
        assert args[6] == t_wall
        assert args[5] in (16, 48, 96)  # 4h / 12h / 24h at 900 s/step


# ---------------------------------------------------------------------------
# End-to-end: small synthetic dataset → auto-ID → near-zero open-loop RMSE
# ---------------------------------------------------------------------------

def _sde_synthetic_history(
    true_mass,
    true_r,
    true_scale,
    *,
    n=192,
    dt=900.0,
    t_air0=20.0,
    t_wall0=20.0,
    noise_std=0.0,
    seed=1,
):
    """History integrated with the same SDE/open-loop integrator (not HouseModel.step).

    Diurnal outdoor swing plus on/off heating makes C / R_ext / α jointly
    identifiable.  Noise-free by default so a correct model + t_wall seed
    reproduces the data to numerical precision.
    """
    import numpy as np

    from custom_components.heating_assistant.integrator import implicit_euler_substeps

    room = Room("a", thermal_mass=true_mass, r_external=true_r, temperature=t_air0)
    if t_wall0 != t_air0:
        room.wall_temperature = t_wall0
    model = HouseModel([room])
    heater = ElectricHeater("ha", "a", 2000.0, power_scale=true_scale)
    system = HouseThermalSDE(model, [heater], dt, augment_offsets=False)
    n_rooms = 1

    rng = np.random.default_rng(seed)
    x = np.asarray(
        system.initial_state_from_measurement(
            np.array([t_air0]),
            np.array([0.6]),
            system.disturbance_vector(3.0, {"a": 0.0}),
            wall_seed="air",
        ),
        dtype=float,
    )
    if t_wall0 != t_air0 and n_rooms < len(x):
        x[n_rooms] = t_wall0

    history = []
    t0 = 1_700_000_000.0
    u_prev = np.array([0.6])
    for k in range(n):
        outdoor = 3.0 + 8.0 * math.sin(2 * math.pi * (k % 96) / 96.0)
        u_val = 0.6 if (k // 8) % 2 == 0 else 0.0
        u_prev = np.array([u_val])
        y_meas = float(x[0]) + noise_std * rng.standard_normal()
        history.append({
            "y": [y_meas],
            "u": [u_val],
            "d_outdoor": outdoor,
            "d_solar": {"a": 0.0},
            "timestamp": t0 + dt * k,
        })
        if k < n - 1:
            d_prev = system.disturbance_vector(outdoor, {"a": 0.0})
            n_sub = max(1, min(200, round(dt * 10.0 / system._dt)))
            rhs = lambda s, uu=u_prev, dd=d_prev: system.f(
                s, uu, dd, np.array([]), 0.0,
            )
            jac = lambda s, uu=u_prev, dd=d_prev: system.dfdx(
                s, uu, dd, np.array([]), 0.0,
            )
            x = implicit_euler_substeps(rhs, jac, x, dt, n_sub)
    return history


def _room_params_from_estimate(result):
    """Build per-room overrides from a KalmanMLEstimator.estimate() result."""
    room_params = {}
    for room_name, params in result.get("estimated_params", {}).items():
        entry = dict(params)
        if room_name in result.get("estimated_internal_gains", {}):
            entry["internal_gain"] = result["estimated_internal_gains"][room_name]
        splits = result.get("estimated_envelope_splits", {}).get(room_name, {})
        entry.update(splits)
        if room_name in result.get("estimated_solar_scales", {}):
            entry["solar_scale"] = result["estimated_solar_scales"][room_name]
        room_params[room_name] = entry
    return room_params


def _open_loop_rmse(history, room_params, heater_scales, t_wall_initial, dt=900.0):
    """Run continuous open-loop diagnostic; mirrors the production service path."""
    room_names = list(room_params.keys())
    base_model = HouseModel([
        Room(name, 5e6, 0.05, temperature=20.0) for name in room_names
    ])
    sim_model = _build_sim_model(base_model, room_params, room_names)
    sources = [
        ElectricHeater(name, room_names[0], 2000.0, power_scale=float(scale))
        for name, scale in heater_scales.items()
    ]
    system = HouseThermalSDE(sim_model, sources, dt, augment_offsets=False)
    result = compute_open_loop_predictions(
        history,
        system,
        room_names,
        len(room_names),
        dt,
        segment_length=None,
        t_wall_initial=t_wall_initial,
    )
    assert "error" not in result, result.get("error")
    return result["per_room"][room_names[0]]["rmse"]


def test_small_synthetic_auto_id_near_zero_open_loop_rmse():
    """Auto-ID on SDE-consistent synthetic data → near-zero continuous open-loop RMSE.

    Structural parameters (C, R_ext, heater scale) are pinned during estimation
    to their true values — as they would be after a converged ML run cached in
    ``sysid_results``.  The estimator still identifies ``t_wall_initial`` from
    the data; the open-loop diagnostic must consume that value and reproduce
    the trajectory to numerical precision on the shared SDE integrator.
    """
    warnings.filterwarnings("ignore")
    true_mass, true_r, true_scale = 3e6, 0.03, 0.8
    dt = 900.0
    history = _sde_synthetic_history(
        true_mass, true_r, true_scale, noise_std=0.0,
    )

    prior_room = Room("a", thermal_mass=5e6, r_external=0.05, temperature=20.0)
    sources = [ElectricHeater("ha", "a", max_power=2000.0)]
    estimator = KalmanMLEstimator(
        [prior_room], sources, dt=dt, regularization=0.01,
    )
    locked = {
        "thermal_mass": {"a": true_mass},
        "r_external": {"a": true_r},
        "internal_gain": {"a": 0.0},
        "heater_scales": {"ha": true_scale},
    }
    result = estimator.estimate(history, locked_params=locked)
    assert result["success"]

    room_params = _room_params_from_estimate(result)
    t_wall_initial = result["estimated_t_wall_initial"]
    heater_scales = result["estimated_heater_scales"]

    # Sanity: exact structural params + true wall seed → essentially zero error.
    true_params = {
        "a": {
            "thermal_mass": true_mass,
            "r_external": true_r,
            "internal_gain": 0.0,
        },
    }
    assert _open_loop_rmse(
        history, true_params, {"ha": true_scale}, {"a": 20.0}, dt,
    ) < 0.01

    # Identified t_wall_initial from ML must close the loop on the same data.
    rmse = _open_loop_rmse(
        history, room_params, heater_scales, t_wall_initial, dt,
    )
    assert rmse is not None
    assert rmse < 0.05, (
        f"open-loop RMSE {rmse:.4f} °C with identified t_wall_initial "
        f"{t_wall_initial}"
    )


def test_open_loop_t_wall_initial_override_differs_from_air():
    """Identified t_wall_initial (≠ air) must improve the first-segment open-loop fit."""
    warnings.filterwarnings("ignore")
    dt = 900.0
    true_mass, true_r, true_scale = 3e6, 0.03, 0.8
    t_air0, t_wall0 = 19.0, 21.5
    assert abs(t_wall0 - t_air0) > 0.5

    history = _sde_synthetic_history(
        true_mass, true_r, true_scale,
        n=120, dt=dt, t_air0=t_air0, t_wall0=t_wall0, noise_std=0.0,
    )

    truth_params = {
        "a": {
            "thermal_mass": true_mass,
            "r_external": true_r,
            "internal_gain": 0.0,
        },
    }
    rmse_with = _open_loop_rmse(
        history, truth_params, {"ha": true_scale}, {"a": t_wall0}, dt,
    )
    rmse_without = _open_loop_rmse(
        history, truth_params, {"ha": true_scale}, None, dt,
    )
    assert rmse_with < rmse_without, (
        f"t_wall_initial override did not help: with={rmse_with}, "
        f"without={rmse_without}"
    )
    assert rmse_with < 0.05
