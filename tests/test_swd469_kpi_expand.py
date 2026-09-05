"""SWD-469: expandable KPI cards and last NMPC duration."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.mqtt.bridge import InMemoryMqttBus

_ROOT = Path(__file__).resolve().parents[1]
_TREES = (
    _ROOT / "heatingassistant" / "app" / "static",
    _ROOT / "heating_assistant" / "heatingassistant" / "app" / "static",
)


def _read(static: Path, *parts: str) -> str:
    return static.joinpath(*parts).read_text(encoding="utf-8")


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
            "rooms": [{"name": "Living Room", "setpoint": 21.0, "temp_tags": ["living_temp"]}],
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


def test_expand_state_after_click_restores_original_order() -> None:
    expand = _read(_TREES[0], "js", "components", "kpi-expand.js")
    assert "export function expandStateAfterClick" in expand
    assert "export function bindKpiExpandSection" in expand
    assert "kpi-expand__detail-inner" in expand
    assert "kpi-expand__lead" not in expand


def test_overview_and_room_register_expand_host() -> None:
    for static in _TREES:
        overview = _read(static, "js", "pages", "overview.js")
        room = _read(static, "js", "pages", "room-detail.js")
        css = _read(static, "css", "industrial.css")
        assert "bindKpiExpandSection" in overview
        assert "bindKpiExpandSection" in room
        assert "nmpcLoadDetail" in overview
        assert "timeInRangeDetail" in room
        assert "regulatorLoadDetail" in room
        assert "kpi-expand--open" in css
        assert "kpi-expand__detail" in css
        assert "kpi-expand__lead" not in css
        assert "description:" in _read(static, "js", "kpi-detail-catalog.js")


def test_panel_entry_cache_bust() -> None:
    for static in _TREES:
        index = _read(static, "index.html")
        dashboard = _read(static, "industrial-dashboard.js")
        assert "industrial-dashboard.js?v=150" in index
        assert "return '150'" in dashboard


def test_nmpc_worker_publishes_last_nmpc_duration(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def _fake_solve():
        started.set()
        release.wait(timeout=2.0)
        time.sleep(0.05)
        return {
            "accepted": False,
            "u_star": np.zeros((1, 1)),
            "t_ref": np.zeros((2, 1)),
            "fun": 1.0,
        }

    def _slow_apply(*_args, **_kwargs):
        time.sleep(0.2)
        return False

    runtime.control_engine.solve_nmpc_blocking = _fake_solve  # type: ignore[method-assign]
    runtime.control_engine.mark_nmpc_busy = lambda: None  # type: ignore[method-assign]
    runtime.control_engine.apply_nmpc_result = _slow_apply  # type: ignore[method-assign]
    runtime._schedule_nmpc_worker()
    assert started.wait(timeout=2.0)
    release.set()
    thread = runtime._nmpc_thread
    assert thread is not None
    thread.join(timeout=2.0)
    duration = runtime._last_nmpc_duration_s
    assert duration is not None
    assert duration >= 0.04
    assert duration < 0.2
    attrs = runtime.hass_states()["sensor.heating_assistant_mpc_performance"]["attributes"]
    assert attrs["last_nmpc_duration_s"] == duration
    native = runtime.hass_states()["sensor.heating_assistant_mpc_performance"]["state"]
    assert float(native) == float(runtime._last_control_duration_s)


def test_panel_expand_order_harness() -> None:
    result = subprocess.run(
        ["node", str(_ROOT / "tests" / "panel_kpi_expand.harness.mjs")],
        check=False,
        capture_output=True,
        text=True,
        cwd=_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
