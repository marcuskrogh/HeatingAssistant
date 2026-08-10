"""SWD-289: App runtime owns system-identification services and datasets."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from heatingassistant.app import sysid_services
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine import const
from heatingassistant.mqtt.bridge import InMemoryMqttBus


pytestmark = pytest.mark.unit


def _runtime(tmp_path: Path) -> HeatingRuntime:
    return HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "update_interval": 900,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 21.0,
                    "comfort_offset": 1.5,
                    "thermal_mass": 1_000_000.0,
                    "r_external": 0.01,
                    "temp_tags": ["living_temp"],
                    "enabled": True,
                }
            ],
            "heat_sources": [
                {
                    "name": "Living Heater",
                    "room": "Living Room",
                    "type": const.SOURCE_TYPE_ELECTRIC,
                    "max_power": 1200.0,
                    "heater_entity": "switch.living_heater",
                }
            ],
            "system_enabled": True,
        },
    )


def _seed_history(runtime: HeatingRuntime, *, count: int = 48, start: float = 1_800_000_000.0) -> list[dict]:
    dt = float(runtime.options["update_interval"])
    records = []
    for i in range(count):
        records.append(
            {
                "y": [21.0 + 0.01 * i],
                "u": [100.0 if i % 2 else 0.0],
                "d_outdoor": 5.0,
                "d_solar": {"Living Room": 0.0},
                "timestamp": start + i * dt,
            }
        )
    runtime._history_buffer.extend(records)
    return records


@pytest.mark.asyncio
async def test_create_dataset_round_trips_from_history_buffer(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    records = _seed_history(runtime)

    result = await runtime.apply_service(
        "heating_assistant",
        "create_dataset",
        {
            "name": "Evening step",
            "room_name": "living_room",
            "window_start": records[3]["timestamp"],
            "window_end": records[-3]["timestamp"],
            "notes": "seeded",
        },
    )

    assert result["record_count"] == len(records) - 5
    metas = runtime.datasets()
    assert [meta["id"] for meta in metas] == [result["dataset_id"]]
    assert metas[0]["room_slug"] == "living_room"
    dataset = runtime.dataset(result["dataset_id"])
    assert dataset is not None
    assert dataset["records"][0]["timestamp"] == pytest.approx(records[3]["timestamp"])


@pytest.mark.asyncio
async def test_estimate_parameters_ml_dry_run_populates_sysid_sensor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    _seed_history(runtime)

    async def fake_estimate(*args, **kwargs):
        assert kwargs["apply_params"] is False
        assert len(kwargs["history_override"]) >= 40
        return {
            "success": True,
            "estimated_params": {
                "Living Room": {"thermal_mass": 1_250_000.0, "r_external": 0.012}
            },
            "estimated_internal_gains": {"Living Room": 42.0},
            "estimated_solar_scales": {"Living Room": 0.8},
            "estimated_envelope_splits": {
                "Living Room": {"c_air_fraction": 0.08, "r_aw_fraction": 0.12}
            },
            "estimated_t_wall_initial": {"Living Room": 20.5},
            "estimated_heater_scales": {"Living Heater": 1.1},
            "estimated_inter_room_r": {},
            "log_likelihood": -1.0,
        }

    monkeypatch.setattr(sysid_services, "async_estimate_parameters_ml", fake_estimate)

    result = await runtime.apply_service(
        "heating_assistant",
        "estimate_parameters_ml",
        {"apply_parameters": False, "horizon_hours": 12.0},
    )

    assert result["success"] is True
    assert runtime.sysid_results["Living Room"]["thermal_mass"] == pytest.approx(1_250_000.0)
    sensor = runtime.hass_states()["sensor.heating_assistant_living_room_sysid_simulation"]
    assert sensor["attributes"]["thermal_mass"] == pytest.approx(1_250_000.0)
    assert sensor["attributes"]["heater_scales"] == {"Living Heater": 1.1}


@pytest.mark.asyncio
async def test_simulation_services_populate_caches_and_sensors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    records = _seed_history(runtime)

    class FakeSystem:
        pass

    async def fake_initial_state(*args, **kwargs):
        return {"t_wall": {"Living Room": 20.9}, "method": "fake"}

    def fake_sysid(history, model, heat_sources, room_names, dt, horizon_steps, room_params, sigma_w, sigma_v, window_spec):
        assert len(history) >= 40
        return {
            "horizon_steps": horizon_steps,
            "per_room": {
                "Living Room": {
                    "simulation": [
                        {
                            "time": records[-2]["timestamp"],
                            "measured": 21.1,
                            "predicted": 21.0,
                            "cov_upper": 21.2,
                            "cov_lower": 20.8,
                        }
                    ],
                    "rmse": 0.1,
                    "mae": 0.08,
                    "thermal_mass": 1_000_000.0,
                    "r_external": 0.01,
                    "sigma_w": sigma_w,
                    "sigma_v": sigma_v,
                }
            },
        }

    def fake_open_loop(history, system, room_names, n_rooms, dt, segment_length, t_wall_initial):
        assert len(history) >= 40
        return {
            "per_room": {
                "Living Room": {
                    "simulation": [
                        {
                            "time": records[-1]["timestamp"],
                            "measured": 21.2,
                            "predicted": 21.05,
                        }
                    ],
                    "rmse": 0.15,
                    "mae": 0.11,
                }
            }
        }

    async def fake_rmse_by_horizon(*args, **kwargs):
        return {"Living Room": {"4h": 0.2, "12h": 0.3}}

    monkeypatch.setattr(sysid_services, "HouseThermalSDE", lambda *args, **kwargs: FakeSystem())
    monkeypatch.setattr(sysid_services, "estimate_simulation_initial_state", fake_initial_state)
    monkeypatch.setattr(sysid_services, "run_sysid_simulation", fake_sysid)
    monkeypatch.setattr(sysid_services, "compute_open_loop_predictions", fake_open_loop)
    monkeypatch.setattr(sysid_services, "compute_open_loop_rmse_by_horizon", fake_rmse_by_horizon)

    await runtime.apply_service(
        "heating_assistant",
        "run_sysid_simulation",
        {"room_name": "living_room", "horizon_hours": 12.0, "sigma_w": 0.2, "sigma_v": 0.4},
    )
    await runtime.apply_service(
        "heating_assistant",
        "run_open_loop_simulation",
        {"room_name": "living_room", "horizon_hours": 12.0},
    )

    assert runtime.sysid_results["Living Room"]["rmse"] == pytest.approx(0.1)
    assert runtime.open_loop_results["Living Room"]["rmse"] == pytest.approx(0.15)
    states = runtime.hass_states()
    sysid_sensor = states["sensor.heating_assistant_living_room_sysid_simulation"]
    ol_sensor = states["sensor.heating_assistant_living_room_open_loop_rmse"]
    assert sysid_sensor["state"] == "0.1"
    assert sysid_sensor["attributes"]["simulation"][0]["time"].startswith("2027-")
    assert ol_sensor["state"] == "0.15"
    assert ol_sensor["attributes"]["rmse_by_horizon"] == {"4h": 0.2, "12h": 0.3}


@pytest.mark.asyncio
async def test_store_identified_parameters_updates_controller_config_history(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    await runtime.apply_service(
        "heating_assistant",
        "store_identified_parameters",
        {
            "room_name": "living_room",
            "thermal_mass": 1_400_000.0,
            "r_external": 0.02,
            "source": "manual",
            "rmse": 0.12,
            "heater_scales": {"Living Heater": 1.05},
        },
    )

    config = runtime.controller_config()
    assert config["parameter_history"]
    assert config["parameter_history"][0]["rooms"]["Living Room"]["thermal_mass"] == pytest.approx(
        1_400_000.0
    )
    assert config["current_heater_scales"]["Living Heater"]["power_scale"] == pytest.approx(1.05)


@pytest.mark.asyncio
async def test_update_estimation_params_persists_options(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    await runtime.apply_service(
        "heating_assistant",
        "update_estimation_params",
        {"sigma_w": 0.25, "sigma_v": 0.75, "identification_horizon_hours": 18.0},
    )

    assert runtime.options["sigma_w"] == pytest.approx(0.25)
    assert runtime.options["sigma_v"] == pytest.approx(0.75)
    assert runtime.options["identification_horizon_hours"] == pytest.approx(18.0)
    config = runtime.controller_config()
    assert config["sigma_w"] == pytest.approx(0.25)
    assert config["sigma_v"] == pytest.approx(0.75)
    assert config["identification_horizon_hours"] == pytest.approx(18.0)


@pytest.mark.asyncio
async def test_delete_dataset_removes_dataset(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    records = _seed_history(runtime)
    created = await runtime.apply_service(
        "heating_assistant",
        "create_dataset",
        {
            "name": "Delete me",
            "window_start": records[0]["timestamp"],
            "window_end": records[-1]["timestamp"],
        },
    )

    deleted = await runtime.apply_service(
        "heating_assistant",
        "delete_dataset",
        {"dataset_id": created["dataset_id"]},
    )

    assert deleted == {"deleted": True}
    assert runtime.datasets() == []
    assert runtime.dataset(created["dataset_id"]) is None
