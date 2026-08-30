"""SWD-456: NMPC apply must compute plan_epoch without NameError."""

from __future__ import annotations

from pathlib import Path

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.nmpc_timing import slow_slot_start_s
from heatingassistant.mqtt.bridge import InMemoryMqttBus


pytestmark = pytest.mark.unit

_EPOCH = 1_700_000_000.0
_NOW = _EPOCH + 100.0


def _runtime(tmp_path: Path) -> HeatingRuntime:
    return HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={"instance_id": "haos", "system_enabled": False},
    )


def test_slow_slot_start_returns_slot_origin(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime._last_nmpc_ts = _EPOCH
    start = runtime._slow_slot_start(_NOW)
    assert start == pytest.approx(slow_slot_start_s(_EPOCH, runtime._nmpc_period_s(), _NOW))


def test_nmpc_worker_passes_slow_slot_plan_epoch(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path)
    runtime._last_nmpc_ts = _EPOCH
    monkeypatch.setattr("heatingassistant.app.runtime_nmpc.time.time", lambda: _NOW)
    captured: dict[str, object] = {}

    def fake_apply(result, *, plan_epoch=None, now=None):
        captured["plan_epoch"] = plan_epoch
        captured["now"] = now
        captured["accepted"] = result.get("accepted")
        return True

    runtime.control_engine.apply_nmpc_result = fake_apply  # type: ignore[method-assign]
    runtime.control_engine.solve_nmpc_blocking = lambda: {  # type: ignore[method-assign]
        "accepted": True,
        "u_star": [[0.4]],
        "t_ref": [[21.0]],
        "fun": 1.0,
    }
    runtime.control_engine.consume_watchdog_notification = lambda: None  # type: ignore[method-assign]
    runtime._install_nmpc_p_command = lambda: None  # type: ignore[method-assign]
    runtime._nmpc_worker_thread()

    assert captured["accepted"] is True
    assert captured["now"] == pytest.approx(_NOW)
    assert captured["plan_epoch"] == pytest.approx(
        slow_slot_start_s(_EPOCH, runtime._nmpc_period_s(), _NOW)
    )
