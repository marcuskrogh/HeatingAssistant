"""Tests for the fast UI refresh path and live-value sensor availability.

The MPC runs strictly at the scheduled update interval.  Between those ticks a
lightweight ``_refresh_live_state`` / ``async_refresh_ui`` keeps the dashboard's
measurements, setpoints, and KPIs current WITHOUT running the controller, and the
live-value sensors stay ``available`` so a single failed update cycle never blanks
the overview.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator


def _make_hass(temp_states: dict[str, str]) -> SimpleNamespace:
    hass = SimpleNamespace()
    hass.services = SimpleNamespace(async_call=AsyncMock())

    def _get_state(entity_id: str):
        if entity_id not in temp_states:
            return None
        return SimpleNamespace(state=temp_states[entity_id], attributes={})

    hass.states = SimpleNamespace(get=_get_state)
    return hass


def _make_live_coord(
    *,
    temp_states: dict[str, str],
    temp_sensors: dict[str, list[str]],
    outdoor: float | None = 5.0,
) -> HeatingAssistantCoordinator:
    """Build a coordinator skeleton wired for the fast-refresh path only."""
    coord = object.__new__(HeatingAssistantCoordinator)
    coord.hass = _make_hass(temp_states)

    rooms = {
        name: SimpleNamespace(temperature=0.0, setpoint=21.0)
        for name in temp_sensors
    }
    coord.model = SimpleNamespace(
        room_names=list(temp_sensors),
        rooms=rooms,
        compute_heat_flows=MagicMock(return_value={"total": 1.0}),
    )

    coord._temp_sensors = temp_sensors
    coord.measured_temperatures = {}
    coord._rooms_ever_measured = set()
    coord.solar_gains = {}
    coord.heat_flows = {}
    coord.cloud_cover = None
    coord.ghi_now = None
    coord.outdoor_temp = None
    coord._last_valid_outdoor_temp = None

    # Stub the heavier collaborators so the refresh stays MPC-free.
    coord._apply_schedule = MagicMock()
    coord._update_window_state_machine = MagicMock()
    coord._read_outdoor_temp = MagicMock(return_value=outdoor)
    coord._room_solar_gain = MagicMock(return_value=123.0)

    # The controller must NOT be touched by the fast path; make any access fail.
    def _boom(*_a, **_k):  # pragma: no cover - only hit on regression
        raise AssertionError("fast UI refresh must not run the MPC controller")

    coord.controller = SimpleNamespace(compute=_boom)
    return coord


def test_refresh_live_state_reads_measurements_without_mpc():
    coord = _make_live_coord(
        temp_states={"sensor.lr": "19.5", "sensor.kt": "20.5"},
        temp_sensors={"living_room": ["sensor.lr"], "kitchen": ["sensor.kt"]},
        outdoor=4.0,
    )

    outdoor = coord._refresh_live_state()

    assert outdoor == pytest.approx(4.0)
    assert coord.measured_temperatures["living_room"] == pytest.approx(19.5)
    assert coord.measured_temperatures["kitchen"] == pytest.approx(20.5)
    assert coord.model.rooms["living_room"].temperature == pytest.approx(19.5)
    assert coord._rooms_ever_measured == {"living_room", "kitchen"}
    # Cheap visualisation state is refreshed, but the controller is never run.
    assert coord.solar_gains["living_room"] == pytest.approx(123.0)
    coord._apply_schedule.assert_called_once()
    coord._update_window_state_machine.assert_called_once()
    coord.model.compute_heat_flows.assert_called_once_with(4.0)
    assert isinstance(coord.now_utc, datetime)
    assert coord.now_utc.tzinfo == timezone.utc


def test_refresh_live_state_persists_last_outdoor_when_unavailable():
    coord = _make_live_coord(
        temp_states={"sensor.lr": "18.0"},
        temp_sensors={"living_room": ["sensor.lr"]},
        outdoor=None,
    )
    coord._last_valid_outdoor_temp = 7.5

    outdoor = coord._refresh_live_state()

    # Raw reading is None (sensor reports "unknown") but the model evaluations
    # use the persisted last-valid value.
    assert coord.outdoor_temp is None
    assert outdoor == pytest.approx(7.5)
    coord.model.compute_heat_flows.assert_called_once_with(7.5)


@pytest.mark.asyncio
async def test_async_refresh_ui_notifies_listeners():
    coord = _make_live_coord(
        temp_states={"sensor.lr": "21.0"},
        temp_sensors={"living_room": ["sensor.lr"]},
        outdoor=5.0,
    )
    coord.async_update_listeners = MagicMock()

    await coord.async_refresh_ui()

    coord.async_update_listeners.assert_called_once()
    assert coord.measured_temperatures["living_room"] == pytest.approx(21.0)


def test_live_value_sensor_mixin_stays_available():
    from custom_components.heating_assistant.sensor import _LiveValueSensorMixin

    class _Dummy(_LiveValueSensorMixin):
        pass

    assert _Dummy().available is True


def test_controller_config_update_interval_is_json_serialisable():
    """Regression: a single non-serialisable attribute breaks the whole payload.

    ``DataUpdateCoordinator`` backs its ``update_interval`` property with
    ``self._update_interval``; assigning a ``timedelta`` (which the runtime
    reconfiguration does) used to clobber the coordinator's own interval field
    with a ``timedelta``.  ``ControllerConfigSensor`` then exposed that timedelta
    in its attributes, and Home Assistant's WebSocket API failed to serialise the
    *entire* state payload — starving the dashboard of every entity.

    The sensor now publishes the coerced numeric seconds (``coordinator.dt``), so
    the attributes must always be JSON-serialisable.
    """
    import json
    from datetime import timedelta

    from custom_components.heating_assistant.coordinator import _coerce_interval_seconds
    from custom_components.heating_assistant.sensor import ControllerConfigSensor

    class _FakeCoord:
        _room_comfort_offset = {"living_room": 2.0}
        _tracking_weight = 1.0
        _energy_weight = 1.0
        _energy_price_weight = 0.0
        _smoothing_weight = 1.0
        _soft_constraint_weight = 1.0
        _soft_constraint_linear_weight = 1.0
        _terminal_weight = 1.0
        _horizon = 100
        _sigma_w = 1.0
        _sigma_v = 1.0
        _sigma_b = 1.0
        _window_open_debounce = 60.0
        _window_open_close_settle = 30.0
        _window_open_q_inflation = 1.0

        @property
        def dt(self):
            # Mirror the real coerced property: even when the underlying interval
            # is a timedelta, dt returns plain float seconds.
            return _coerce_interval_seconds(timedelta(seconds=300))

    sensor = object.__new__(ControllerConfigSensor)
    sensor._coordinator = _FakeCoord()

    attrs = sensor.extra_state_attributes

    assert attrs["update_interval"] == pytest.approx(300.0)
    assert not isinstance(attrs["update_interval"], timedelta)
    # Must not raise — this is exactly what the HA WebSocket serialiser does.
    json.dumps(attrs)
