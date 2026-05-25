"""Focused regression tests for schedule-aware MPC horizon behavior."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from custom_components.heating_assistant.controller import HeatingMPCController
from custom_components.heating_assistant.coordinator import (
    ControlTrajectory,
    HeatingAssistantCoordinator,
)
from custom_components.heating_assistant.heat_sources import ElectricHeater
from custom_components.heating_assistant.schedule import EffectiveControlParams, build_schedule
from custom_components.heating_assistant.sensor import (
    ConstraintLowerSensor,
    ConstraintUpperSensor,
    SetpointSensor,
)
from custom_components.heating_assistant.thermal_model import HouseModel, Room


def _make_coordinator_stub_for_trajectory() -> HeatingAssistantCoordinator:
    coord = object.__new__(HeatingAssistantCoordinator)
    room = SimpleNamespace(setpoint=21.0, comfort_offset=2.0, temperature=20.0)
    coord.model = SimpleNamespace(room_names=["living_room"], rooms={"living_room": room})
    coord._room_schedule = {}
    coord._base_setpoint = {"living_room": 21.0}
    coord._room_comfort_offset = {"living_room": 2.0}
    coord._schedule_enabled = {"living_room": True}
    coord._effective_setpoint = {
        "living_room": EffectiveControlParams(
            setpoint=21.0,
            comfort_offset=1.0,
            tracking_weight=2.0,
            energy_weight=0.7,
            enabled=True,
            period_name="morning",
            mode="comfort",
        )
    }
    return coord


def test_control_trajectory_carries_forward_comfort_values_through_off_period() -> None:
    coord = _make_coordinator_stub_for_trajectory()
    coord._room_schedule["living_room"] = build_schedule(
        [
            {
                "name": "morning",
                "start": "06:00",
                "end": "08:00",
                "mode": "comfort",
                "setpoint": 21.0,
                "comfort_offset": 1.0,
                "tracking_weight": 2.0,
                "energy_weight": 0.7,
            },
            {
                "name": "day_off",
                "start": "08:00",
                "end": "10:00",
                "mode": "off",
                "frost_protection": 12.0,
            },
            {
                "name": "late_comfort",
                "start": "10:00",
                "end": "12:00",
                "mode": "comfort",
                "setpoint": 23.0,
                "comfort_offset": 0.5,
                "tracking_weight": 3.0,
                "energy_weight": 1.2,
            },
        ]
    )

    traj = coord._compute_control_trajectory(
        now_local=datetime(2026, 1, 5, 7, 30),
        N=8,
        dt_seconds=1800.0,
    )

    assert traj.setpoints["living_room"].tolist() == pytest.approx(
        [21.0, 21.0, 21.0, 21.0, 21.0, 23.0, 23.0, 23.0]
    )
    assert traj.comfort_offsets["living_room"].tolist() == pytest.approx(
        [1.0, 1.0, 1.0, 1.0, 1.0, 0.5, 0.5, 0.5]
    )
    assert traj.q_scales["living_room"].tolist() == pytest.approx(
        [2.0, 2.0, 2.0, 2.0, 2.0, 3.0, 3.0, 3.0]
    )
    assert traj.r_scales["living_room"].tolist() == pytest.approx(
        [0.7, 0.7, 0.7, 0.7, 0.7, 1.2, 1.2, 1.2]
    )


def test_control_trajectory_uses_current_effective_values_when_schedule_suspended() -> None:
    coord = _make_coordinator_stub_for_trajectory()
    coord._schedule_enabled["living_room"] = False
    coord._room_schedule["living_room"] = build_schedule(
        [{"name": "ignored", "start": "00:00", "end": "23:59", "setpoint": 25.0}]
    )

    traj = coord._compute_control_trajectory(
        now_local=datetime(2026, 1, 5, 7, 30),
        N=4,
        dt_seconds=900.0,
    )

    assert traj.setpoints["living_room"].tolist() == pytest.approx([21.0, 21.0, 21.0, 21.0])
    assert traj.comfort_offsets["living_room"].tolist() == pytest.approx([1.0, 1.0, 1.0, 1.0])
    assert traj.q_scales["living_room"].tolist() == pytest.approx([2.0, 2.0, 2.0, 2.0])
    assert traj.r_scales["living_room"].tolist() == pytest.approx([0.7, 0.7, 0.7, 0.7])


def test_setpoint_and_constraints_follow_schedule_projected_trajectory() -> None:
    room = SimpleNamespace(temperature=20.0, setpoint=21.0, comfort_offset=2.0)
    coord = SimpleNamespace(
        model=SimpleNamespace(rooms={"living_room": room}),
        _horizon=4,
        dt=900.0,
        now_utc=datetime(2026, 1, 5, 7, 30),
        _control_trajectory=ControlTrajectory(
            setpoints={"living_room": np.array([21.0, 21.0, 23.0, 23.0])},
            comfort_offsets={"living_room": np.array([1.0, 1.0, 0.5, 0.5])},
            q_scales={"living_room": np.array([1.0, 1.0, 1.0, 1.0])},
            r_scales={"living_room": np.array([1.0, 1.0, 1.0, 1.0])},
        ),
    )

    setpoint_fc = SetpointSensor(coord, "living_room").extra_state_attributes["forecast"]
    upper_fc = ConstraintUpperSensor(coord, "living_room").extra_state_attributes["forecast"]
    lower_fc = ConstraintLowerSensor(coord, "living_room").extra_state_attributes["forecast"]

    assert [row["setpoint"] for row in setpoint_fc] == pytest.approx(
        [21.0, 21.0, 23.0, 23.0, 23.0]
    )
    assert [row["constraint_upper"] for row in upper_fc] == pytest.approx(
        [22.0, 22.0, 23.5, 23.5, 23.5]
    )
    assert [row["constraint_lower"] for row in lower_fc] == pytest.approx(
        [20.0, 20.0, 22.5, 22.5, 22.5]
    )


def test_controller_compute_passes_schedule_trajectory_to_mpc_step(monkeypatch) -> None:
    model = HouseModel(
        [
            Room(
                name="living_room",
                thermal_mass=5_000_000.0,
                r_external=0.05,
                setpoint=21.0,
                comfort_offset=2.0,
            )
        ]
    )
    ctrl = HeatingMPCController(
        model=model,
        heat_sources=[ElectricHeater("heater", "living_room", max_power=2000.0)],
        horizon=3,
        dt=900.0,
        measurement_dt=900.0,
        latitude=55.0,
        longitude=12.0,
    )

    captured: dict = {}

    def _fake_step(y, d, p, t, **kwargs):
        captured.update(kwargs)
        nu = len(ctrl._sources)
        n_x = ctrl._system.nx
        N = ctrl.horizon
        return np.zeros(nu), np.zeros((N, nu)), np.zeros((N, n_x))

    monkeypatch.setattr(ctrl._mpc, "step", _fake_step)

    traj = ControlTrajectory(
        setpoints={"living_room": np.array([21.0, 22.0, 23.0])},
        comfort_offsets={"living_room": np.array([2.0, 1.5, 1.0])},
        q_scales={"living_room": np.array([1.0, 1.2, 1.4])},
        r_scales={"living_room": np.array([1.0, 0.8, 0.6])},
    )

    ctrl.compute(
        outdoor_temp=5.0,
        solar_gains={"living_room": 0.0},
        now=datetime(2026, 1, 5, 7, 30, tzinfo=timezone.utc),
        control_trajectory=traj,
    )

    assert np.allclose(captured["x_ref_abs_seq"][:, 0], [21.0, 22.0, 23.0])
    assert np.allclose(captured["offset_seq"][:, 0], [2.0, 1.5, 1.0])
    assert np.allclose(captured["q_scale_seq"][:, 0], [1.0, 1.2, 1.4])
    assert np.allclose(captured["r_scale_seq"][:, 0], [1.0, 0.8, 0.6])
