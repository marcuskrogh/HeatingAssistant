"""Dual-cycle NMPC / control countdown attributes and panel wiring."""

from __future__ import annotations

from pathlib import Path
import threading
import time

import numpy as np
import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.const import DEFAULT_NMPC_PERIOD
from heatingassistant.mqtt.bridge import InMemoryMqttBus

_STATIC = (
    Path(__file__).resolve().parents[1]
    / "heatingassistant"
    / "app"
    / "static"
)


def test_hass_states_publish_nmpc_cycle_attrs(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path, bus=InMemoryMqttBus(), options={"instance_id": "haos"}
    )
    attrs = runtime.hass_states()["sensor.heating_assistant_mpc_performance"][
        "attributes"
    ]
    assert attrs["nmpc_period_s"] == pytest.approx(DEFAULT_NMPC_PERIOD)
    assert attrs["last_nmpc_ts"] is None
    assert "last_run_ts" in attrs
    assert "dt_s" in attrs
    assert runtime._control_status()["last_nmpc_ts"] is None


def test_hass_states_use_configured_nmpc_period(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "nmpc_period": 1800,
            "nmpc_fast_substeps": 2,
            "nmpc_horizon_h": 0.5,
        },
    )
    attrs = runtime.hass_states()["sensor.heating_assistant_mpc_performance"][
        "attributes"
    ]
    assert attrs["nmpc_period_s"] == pytest.approx(1800.0)


def test_last_nmpc_ts_persists_across_restart(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path, bus=InMemoryMqttBus(), options={"instance_id": "haos"}
    )
    runtime._last_nmpc_ts = 1_700_000_000.0
    runtime._save_runtime_state()

    restarted = HeatingRuntime(
        tmp_path, bus=InMemoryMqttBus(), options={"instance_id": "haos"}
    )
    assert restarted._last_nmpc_ts == pytest.approx(1_700_000_000.0)
    attrs = restarted.hass_states()["sensor.heating_assistant_mpc_performance"][
        "attributes"
    ]
    assert attrs["last_nmpc_ts"] == pytest.approx(1_700_000_000.0)


def test_nmpc_worker_stamps_last_nmpc_ts_on_reject(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "nmpc_period": 1800,
            "nmpc_fast_substeps": 2,
            "nmpc_horizon_h": 0.5,
            "rooms": [
                {
                    "name": "Living Room",
                    "setpoint": 21.0,
                    "temp_tags": ["living_temp"],
                }
            ],
            "heat_sources": [
                {
                    "name": "heater",
                    "type": "electric_heater",
                    "room": "Living Room",
                    "max_power": 1500.0,
                }
            ],
        },
    )
    started = threading.Event()

    def _fake_solve():
        started.set()
        return {
            "accepted": False,
            "u_star": np.zeros((1, 1)),
            "t_ref": np.zeros((2, 1)),
            "fun": 1.0,
        }

    runtime.control_engine.solve_nmpc_blocking = _fake_solve  # type: ignore[method-assign]
    runtime.control_engine.mark_nmpc_busy = lambda: None  # type: ignore[method-assign]
    before = time.time()
    runtime._schedule_nmpc_worker()
    assert started.wait(timeout=2.0)
    thread = runtime._nmpc_thread
    assert thread is not None
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert runtime._last_nmpc_ts is not None
    assert runtime._last_nmpc_ts >= before
    attrs = runtime.hass_states()["sensor.heating_assistant_mpc_performance"][
        "attributes"
    ]
    assert attrs["last_nmpc_ts"] == pytest.approx(runtime._last_nmpc_ts)


def test_concurrent_runtime_state_saves_do_not_raise(tmp_path: Path) -> None:
    runtime = HeatingRuntime(
        tmp_path, bus=InMemoryMqttBus(), options={"instance_id": "haos"}
    )
    errors: list[BaseException] = []

    def _save_loop() -> None:
        try:
            for _ in range(40):
                runtime._last_nmpc_ts = time.time()
                runtime._save_runtime_state()
        except BaseException as exc:  # noqa: BLE001 — collect any thread failure
            errors.append(exc)

    threads = [threading.Thread(target=_save_loop) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert errors == []
    assert (tmp_path / "state.json").is_file()


def test_panel_js_wires_dual_countdown_rings() -> None:
    countdown = (_STATIC / "js" / "components" / "countdown.js").read_text(
        encoding="utf-8"
    )
    overview = (_STATIC / "js" / "pages" / "overview.js").read_text(encoding="utf-8")
    room = (_STATIC / "js" / "pages" / "room-detail.js").read_text(encoding="utf-8")
    status = (_STATIC / "js" / "pages" / "system-status.js").read_text(
        encoding="utf-8"
    )
    assert "NEXT CONTROL" in countdown
    assert "NEXT NMPC" in countdown
    assert "last_nmpc_ts" in countdown
    assert "nmpc_period_s" in countdown
    assert "COUNTDOWN_NMPC" in overview
    assert "COUNTDOWN_NMPC" in room
    assert "nmpc_period_s" in status
    assert "last_nmpc_ts" in status
    assert "NMPC interval" in status
