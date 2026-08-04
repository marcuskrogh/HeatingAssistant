"""Regression tests for coordinator construction and sensor platform setup."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.heating_assistant.const import (
    CONF_HEAT_SOURCES,
    CONF_PERSISTED_SYSTEM_ENABLED,
    CONF_ROOM_NAME,
    CONF_ROOMS,
    CONF_SETPOINT,
    DOMAIN,
)
from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from custom_components.heating_assistant.sensor import ControllerConfigSensor, _build_entities


def _minimal_entry(*, persisted_enabled: bool | None = None):
    data = {
        CONF_ROOMS: [{CONF_ROOM_NAME: "living_room", CONF_SETPOINT: 21.0}],
        CONF_HEAT_SOURCES: [],
    }
    if persisted_enabled is not None:
        data[CONF_PERSISTED_SYSTEM_ENABLED] = persisted_enabled
    return SimpleNamespace(
        data=data,
        options={},
        entry_id="entry-init-regression",
        title="Heating Assistant",
    )


def _minimal_hass():
    hass = SimpleNamespace()
    hass.config = SimpleNamespace(latitude=55.0, longitude=12.0)
    return hass


@pytest.fixture
def fake_controller(monkeypatch):
    class _FakeController:
        def __init__(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(
        "custom_components.heating_assistant.coordinator.core.HeatingMPCController",
        _FakeController,
    )
    monkeypatch.setattr(
        "custom_components.heating_assistant.controller.ipopt_deps.ensure_cyipopt_installed",
        lambda **_kwargs: SimpleNamespace(available=True, source="test", detail=None),
    )
    monkeypatch.setattr(
        "custom_components.heating_assistant.controller.ipopt_probe.probe_nonlinear_backend",
        lambda: SimpleNamespace(
            available=True,
            backend="scipy",
            ipopt_available=False,
            reason="test stub uses scipy",
        ),
    )
    monkeypatch.setattr(
        "custom_components.heating_assistant.controller.ipopt_probe.probe_ipopt_capability",
        lambda: SimpleNamespace(
            available=True,
            backend="scipy",
            ipopt_available=False,
            reason="test stub uses scipy",
        ),
    )


def test_coordinator_init_initialises_room_state(fake_controller):
    """``__init__`` must call ``_init_room_state`` so setup/update can run."""
    entry = _minimal_entry(persisted_enabled=True)
    coord = HeatingAssistantCoordinator(_minimal_hass(), entry)

    assert hasattr(coord, "_system_enabled")
    assert coord._system_enabled is True
    assert hasattr(coord, "_room_schedule")
    assert "living_room" in coord._room_schedule
    assert coord._base_setpoint["living_room"] == 21.0
    assert coord.system_enabled is True


def test_coordinator_init_defaults_system_enabled_to_false(fake_controller):
    entry = _minimal_entry()
    coord = HeatingAssistantCoordinator(_minimal_hass(), entry)
    assert coord._system_enabled is False
    assert coord.system_enabled is False


def test_apply_schedule_runs_after_coordinator_init(fake_controller):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    entry = _minimal_entry()
    coord = HeatingAssistantCoordinator(_minimal_hass(), entry)
    now_local = datetime.now(tz=ZoneInfo("UTC"))

    # Must not raise AttributeError for missing _room_schedule.
    coord._apply_schedule(now_local)


def test_controller_config_sensor_unique_id_uses_domain(fake_controller):
    entry = _minimal_entry()
    coord = HeatingAssistantCoordinator(_minimal_hass(), entry)

    sensor = ControllerConfigSensor(coord)

    assert sensor._attr_unique_id == f"{DOMAIN}_controller_config"


def test_build_entities_includes_controller_config_sensor(fake_controller):
    entry = _minimal_entry()
    coord = HeatingAssistantCoordinator(_minimal_hass(), entry)

    entities = _build_entities(coord)

    assert any(isinstance(entity, ControllerConfigSensor) for entity in entities)
