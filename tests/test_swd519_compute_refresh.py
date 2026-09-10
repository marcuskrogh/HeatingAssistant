"""SWD-519: publish compute flags when solvers start and finish."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import numpy as np

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus

_ROOT = Path(__file__).resolve().parents[1]
_TREES = (
    _ROOT / "heatingassistant" / "app" / "static",
    _ROOT / "heating_assistant" / "heatingassistant" / "app" / "static",
)


def _runtime(tmp_path: Path) -> HeatingRuntime:
    return HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={
            "instance_id": "haos",
            "system_enabled": False,
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


def _record_status(runtime: HeatingRuntime) -> list[dict[str, object]]:
    snaps: list[dict[str, object]] = []
    original = runtime.publish_status

    async def _capture() -> None:
        snaps.append(
            {
                "nmpc_computing": bool(runtime._nmpc_computing),
                "control_computing": bool(runtime._control_computing),
                "nmpc_result_ts": runtime._nmpc_result_ts,
                "last_nmpc_duration_s": runtime._last_nmpc_duration_s,
            }
        )
        await original()

    runtime.publish_status = _capture  # type: ignore[method-assign]
    return snaps


def test_nmpc_reject_publishes_computing_start_and_idle_result(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    snaps = _record_status(runtime)
    started = threading.Event()
    release = threading.Event()

    def _fake_solve():
        started.set()
        release.wait(timeout=2.0)
        return {
            "accepted": False,
            "u_star": np.zeros((1, 1)),
            "t_ref": np.zeros((2, 1)),
            "fun": 1.0,
        }

    runtime.control_engine.solve_nmpc_blocking = _fake_solve  # type: ignore[method-assign]
    runtime.control_engine.mark_nmpc_busy = lambda: None  # type: ignore[method-assign]
    runtime.control_engine.apply_nmpc_result = (  # type: ignore[method-assign]
        lambda _result, **_kwargs: False
    )
    runtime._schedule_nmpc_worker()
    assert started.wait(timeout=2.0)
    assert any(row["nmpc_computing"] is True for row in snaps)
    release.set()
    thread = runtime._nmpc_thread
    assert thread is not None
    thread.join(timeout=2.0)
    idle = [row for row in snaps if row["nmpc_computing"] is False]
    assert idle
    assert idle[-1]["nmpc_result_ts"] is not None
    assert float(idle[-1]["last_nmpc_duration_s"] or 0) >= 0
    assert runtime._nmpc_computing is False


def test_nmpc_accept_clears_computing_before_finish_publish(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    snaps = _record_status(runtime)
    runtime.control_engine.solve_nmpc_blocking = lambda: {  # type: ignore[method-assign]
        "accepted": True,
        "u_star": np.array([[0.4]]),
        "t_ref": np.array([[21.0]]),
        "fun": 1.0,
    }
    runtime.control_engine.apply_nmpc_result = (  # type: ignore[method-assign]
        lambda _result, **_kwargs: True
    )
    runtime.control_engine.consume_watchdog_notification = lambda: None  # type: ignore[method-assign]
    runtime._nmpc_computing = True
    runtime._nmpc_worker_thread()
    finish = [row for row in snaps if row["nmpc_computing"] is False]
    assert finish, "accepted solve must publish idle computing flags"
    assert finish[0]["nmpc_result_ts"] is not None
    assert float(finish[0]["last_nmpc_duration_s"] or 0) >= 0


def test_nmpc_error_publishes_idle_result(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    snaps = _record_status(runtime)

    def _boom():
        raise RuntimeError("nmpc failed")

    runtime.control_engine.solve_nmpc_blocking = _boom  # type: ignore[method-assign]
    runtime.control_engine.mark_nmpc_busy = lambda: None  # type: ignore[method-assign]
    runtime._nmpc_computing = True
    runtime._nmpc_worker_thread()
    idle = [row for row in snaps if row["nmpc_computing"] is False]
    assert idle, "failed solve must still publish idle computing flags"
    assert idle[-1]["nmpc_result_ts"] is not None
    assert float(idle[-1]["last_nmpc_duration_s"] or 0) >= 0
    assert runtime._nmpc_computing is False


def test_control_cycle_publishes_computing_start(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    snaps = _record_status(runtime)
    asyncio.run(runtime.run_control_cycle())
    assert any(row["control_computing"] is True for row in snaps)
    assert snaps[-1]["control_computing"] is False
    assert runtime._control_computing is False


def test_ingress_poll_tightens_while_solver_busy() -> None:
    shim = (_ROOT / "heatingassistant" / "app" / "static" / "js" / "app-hass-shim.js").read_text(
        encoding="utf-8"
    )
    assert "_syncPollInterval" in shim
    assert "_solverBusy" in shim
    assert "nmpc_computing" in shim
    assert "? 1000 :" in shim or "1000 : this._idlePollIntervalMs" in shim


def test_panel_detects_in_place_mpc_attribute_changes() -> None:
    for static in _TREES:
        dashboard = (static / "industrial-dashboard.js").read_text(encoding="utf-8")
        assert "JSON.stringify(prev.attributes" in dashboard


def test_calver_and_cache_bust_for_compute_refresh() -> None:
    init = (_ROOT / "heatingassistant" / "__init__.py").read_text(encoding="utf-8")
    assert '__version__ = "2026.09.22"' in init
    changelog = (_ROOT / "heating_assistant" / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "# 2026.09.17" in changelog
    for static in _TREES:
        index = (static / "index.html").read_text(encoding="utf-8")
        dashboard = (static / "industrial-dashboard.js").read_text(encoding="utf-8")
        assert "industrial-dashboard.js?v=169" in index
        assert "app-hass-shim.js?v=169" in index
        assert "return '169'" in dashboard
