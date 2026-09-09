"""SWD-536: one Next Compute ring and mode-general MPC Load."""

from __future__ import annotations

from pathlib import Path

import pytest

from heatingassistant.app.runtime import HeatingRuntime
from heatingassistant.engine.const import MPC_MODE_LINEAR, MPC_MODE_NMPC
from heatingassistant.mqtt.bridge import InMemoryMqttBus

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "heatingassistant" / "app" / "static"


def test_hass_states_publish_planner_load_attrs(tmp_path: Path) -> None:
    linear = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={"instance_id": "haos", "mpc_mode": MPC_MODE_LINEAR},
    )
    linear._last_control_duration_s = 1.25
    linear._last_nmpc_duration_s = 40.0
    attrs = linear.hass_states()["sensor.heating_assistant_mpc_performance"]["attributes"]
    assert attrs["mpc_mode"] == MPC_MODE_LINEAR
    assert attrs["last_planner_duration_s"] == pytest.approx(1.25)

    nmpc = HeatingRuntime(
        tmp_path,
        bus=InMemoryMqttBus(),
        options={"instance_id": "haos", "mpc_mode": MPC_MODE_NMPC},
    )
    nmpc._last_control_duration_s = 0.2
    nmpc._last_nmpc_duration_s = 42.0
    nmpc_attrs = nmpc.hass_states()["sensor.heating_assistant_mpc_performance"][
        "attributes"
    ]
    assert nmpc_attrs["mpc_mode"] == MPC_MODE_NMPC
    assert nmpc_attrs["last_planner_duration_s"] == pytest.approx(42.0)


def test_overview_and_room_have_one_next_compute_ring() -> None:
    countdown = (STATIC / "js" / "components" / "countdown.js").read_text(encoding="utf-8")
    overview = (STATIC / "js" / "pages" / "overview.js").read_text(encoding="utf-8")
    room = (STATIC / "js" / "pages" / "room-detail.js").read_text(encoding="utf-8")
    assert "label: 'NEXT COMPUTE'" in countdown
    assert "NEXT CONTROL" not in countdown
    assert "NEXT NMPC" not in countdown
    assert overview.count("createCountdown(") == 1
    assert room.count("createCountdown(") == 1
    assert "label: 'MPC LOAD'" in overview
    assert "NMPC LOAD" not in overview
    assert "nmpcCountdown" not in overview
    assert "nmpcCountdown" not in room
