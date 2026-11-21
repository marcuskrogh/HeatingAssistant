"""Loaded Parameter Estimation windows must be the windows that are replayed.

Live Office dataset 43e738dbb48a (21 Nov 2026) is one contiguous 96 h run at
about 900 s. Reconstruction of that dataset kept the trailing default horizon
(24 records, 5.75 h) while heating_power stayed on the full snapshot.
Open-loop does not do that for a dataset_id request. A 24-point simulation
with a heating_power series of the same length is the recent-horizon request
``horizon_hours=6`` and nothing else, which is the constant
``DEFAULT_PARAMETER_ESTIMATION_HORIZON_HOURS``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from heatingassistant.app import sysid_services
from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.app.sysid_sensors import open_loop_sensor_attrs, sysid_sensor_attrs
from heatingassistant.engine import const
from heatingassistant.mqtt.bridge import InMemoryMqttBus

pytestmark = pytest.mark.unit

# 24 steps of 900.016 s is 0.384 s past 6 h, so the 6 h cutoff drops that
# sample and keeps 24 records spanning 23 steps (5.75 h). That is the live
# open-loop trace: 20 Nov 22:24 → 21 Nov 04:09.
_STEP_S = 900.016
_END = datetime(2026, 11, 21, 4, 9, 30, tzinfo=timezone.utc).timestamp()
_STEPS = int(96 * 3600 / _STEP_S)


def _runtime(tmp_path: Path) -> HeatingRuntime:
    return HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "pe-window",
            "update_interval": 900,
            "rooms": [
                {
                    "name": "Office",
                    "setpoint": 23.0,
                    "comfort_offset": 1.5,
                    "thermal_mass": 5_000_000.0,
                    "r_external": 0.05,
                    "temp_tags": ["office_temp"],
                    "enabled": True,
                }
            ],
            "heat_sources": [
                {
                    "name": "Office Heater",
                    "room": "Office",
                    "type": const.SOURCE_TYPE_ELECTRIC,
                    "max_power": 1300.0,
                    "heater_entity": "climate.office",
                }
            ],
            "system_enabled": True,
        },
    )


def _seed(runtime: HeatingRuntime) -> list[dict]:
    start = _END - _STEPS * _STEP_S
    records = []
    for index in range(_STEPS + 1):
        stamp = start + index * _STEP_S
        hour = datetime.fromtimestamp(stamp, tz=timezone.utc).hour
        records.append(
            {
                "y": [22.5],
                "u": [1.0 if 7 <= hour < 22 else 0.0],
                "d_outdoor": 8.0,
                "d_solar": {"Office": 0.0},
                "window_open": {"Office": False},
                "timestamp": stamp,
            }
        )
    runtime._history_buffer.extend(records)
    return records


def _loaded_payload(dataset_id: str, records: list[dict]) -> dict:
    # collectSimParams() after Load: dataset id and the saved bounds, no horizon.
    return {
        "room_name": "office",
        "dataset_id": dataset_id,
        "window_start": records[0]["timestamp"],
        "window_end": records[-1]["timestamp"],
        "sigma_w": 0.1,
        "sigma_v": 0.5,
        "thermal_mass_office": 5_000_000.0,
        "r_external_office": 0.05,
        "internal_gain_office": 0.0,
        "solar_scale_office": 1.0,
    }


@pytest.mark.asyncio
async def test_loaded_dataset_reconstruction_covers_saved_window(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    records = _seed(runtime)
    created = await sysid_services.handle_create_dataset(
        runtime,
        {
            "name": "4 days",
            "room_name": "Office",
            "window_start": records[0]["timestamp"],
            "window_end": records[-1]["timestamp"],
        },
    )
    payload = _loaded_payload(created["dataset_id"], records)

    result = await sysid_services.handle_run_sysid_simulation(runtime, payload)
    assert "error" not in result
    simulation = result["per_room"]["Office"]["simulation"]
    heating = result["per_room"]["Office"]["heating_power"]
    published = sysid_sensor_attrs(runtime, "Office")

    assert len(heating) == len(records)
    assert len(published["heating_power"]) == len(records)
    assert simulation[0]["time"] == pytest.approx(records[0]["timestamp"])
    assert simulation[-1]["time"] == pytest.approx(records[-1]["timestamp"])
    assert len(simulation) == len(records)
    assert len(published["simulation"]) == len(records)


@pytest.mark.asyncio
async def test_open_loop_six_hour_horizon_is_last_24_records(tmp_path: Path) -> None:
    """The only writer of open_loop_results is the open-loop service.

    With no dataset and no bounds, horizon_hours=6 (the horizon-field default,
    not the configured 48 h) selects the trailing 6 h. On this 900.016 s grid
    that is exactly the last 24 records, and heating_power is built from that
    same history, so the two series have the same length.
    """

    runtime = _runtime(tmp_path)
    records = _seed(runtime)
    created = await sysid_services.handle_create_dataset(
        runtime,
        {
            "name": "4 days",
            "room_name": "Office",
            "window_start": records[0]["timestamp"],
            "window_end": records[-1]["timestamp"],
        },
    )
    tail = records[-24:]

    recent = await sysid_services.handle_run_open_loop_simulation(
        runtime,
        {
            "room_name": "office",
            "horizon_hours": const.DEFAULT_PARAMETER_ESTIMATION_HORIZON_HOURS,
            "thermal_mass_office": 5_000_000.0,
            "r_external_office": 0.05,
            "internal_gain_office": 0.0,
            "solar_scale_office": 1.0,
        },
    )
    assert "error" not in recent
    room = recent["per_room"]["Office"]
    published = open_loop_sensor_attrs(runtime, "Office")
    assert len(room["simulation"]) == 24
    assert len(room["heating_power"]) == 24
    assert len(published["simulation"]) == 24
    assert len(published["heating_power"]) == 24
    assert room["simulation"][0]["time"] == pytest.approx(tail[0]["timestamp"])
    assert room["simulation"][-1]["time"] == pytest.approx(tail[-1]["timestamp"])
    assert datetime.fromtimestamp(tail[0]["timestamp"], tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    ) == "2026-11-20 22:24:29"
    assert datetime.fromtimestamp(tail[-1]["timestamp"], tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    ) == "2026-11-21 04:09:30"

    loaded = await sysid_services.handle_run_open_loop_simulation(
        runtime, _loaded_payload(created["dataset_id"], records)
    )
    assert "error" not in loaded
    loaded_room = loaded["per_room"]["Office"]
    assert len(loaded_room["simulation"]) == len(records)
    assert len(loaded_room["heating_power"]) == len(records)
