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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator
from tests.helpers.coordinator_stubs import wire_room_enablement

_DISTURBANCES_ROOM_SOLAR = (
    "custom_components.heating_assistant.coordinator.disturbances.room_solar_gain"
)


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
    coord._cloud_cover_filtered = None
    coord.ghi_now = None
    coord.outdoor_temp = None
    coord._last_valid_outdoor_temp = None

    # Stub the heavier collaborators so the refresh stays MPC-free.
    coord._apply_schedule = MagicMock()
    coord._update_window_state_machine = MagicMock()
    coord._read_outdoor_temp = MagicMock(return_value=outdoor)
    coord._outdoor_entity = None
    coord._weather_entity = None
    coord._solar_radiation_entity = None
    coord._price_entity = None
    coord._weather_warn_thresholds = {3, 10}
    coord.weather_consecutive_failures = 0
    coord.weather_last_error = None
    coord.weather_last_error_at = None
    coord.weather_last_success_at = None
    coord.solar_fc_consecutive_failures = 0
    coord.solar_fc_last_error = None
    coord.solar_fc_last_error_at = None
    coord.solar_fc_last_success_at = None
    coord.now_utc = datetime.now(tz=timezone.utc)
    coord._ensure_runtime_state_loaded = AsyncMock()
    coord._room_solar_gain = MagicMock(return_value=123.0)
    coord._latitude = 55.0
    coord._longitude = 12.0

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

    with patch(_DISTURBANCES_ROOM_SOLAR, return_value=123.0):
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


def test_fast_refresh_uses_persisted_cloud_cover_not_unattenuated():
    """Regression: the fast UI refresh must attenuate solar gain with the
    smoothed/persisted cloud cover instead of emitting an unattenuated
    clear-sky spike before the first full MPC cycle sets ``self.cloud_cover``.

    Right after a restart the full cycle has not run yet, so ``self.cloud_cover``
    is ``None``.  The fast UI refresh (every UI_REFRESH_INTERVAL seconds) used to
    pass that ``None`` straight into the clear-sky model, which skips attenuation
    and produces a solar-gain spike well above the real value.  It must instead
    fall back to the EMA seeded from persistence.
    """
    coord = _make_live_coord(
        temp_states={"sensor.lr": "20.0"},
        temp_sensors={"living_room": ["sensor.lr"]},
        outdoor=5.0,
    )
    # Post-restart state: full cycle hasn't published cloud_cover yet, but the
    # EMA was seeded from the value persisted by the previous session.
    coord.cloud_cover = None
    coord._cloud_cover_filtered = 0.9  # heavy cloud cover persisted last session

    captured: dict = {}

    def _capture(_coord, name, now, cloud_cover, ghi):
        captured["cloud_cover"] = cloud_cover
        return 50.0

    with patch(_DISTURBANCES_ROOM_SOLAR, side_effect=_capture):
        coord._refresh_live_state()

    # The persisted cloud cover drove the attenuation — NOT None.
    assert captured["cloud_cover"] == pytest.approx(0.9)
    # And it is published so subsequent refreshes stay consistent.
    assert coord.cloud_cover == pytest.approx(0.9)


def test_fast_refresh_computes_solar_even_when_outdoor_unavailable():
    """Regression: solar gain must not drop to 0 when the outdoor sensor is
    unavailable (the common case right after a restart).

    Solar gain depends only on time, location and cloud cover — never on the
    outdoor temperature — so the fast UI refresh must publish it even with no
    outdoor reading.  Heat flows, which do need the outdoor temperature, are
    still skipped.
    """
    coord = _make_live_coord(
        temp_states={"sensor.lr": "20.0"},
        temp_sensors={"living_room": ["sensor.lr"]},
        outdoor=None,
    )
    coord._last_valid_outdoor_temp = None  # never had a valid outdoor reading
    coord._cloud_cover_filtered = 0.5

    with patch(_DISTURBANCES_ROOM_SOLAR, return_value=123.0):
        outdoor = coord._refresh_live_state()

    assert outdoor is None
    # Solar gain is published rather than left empty (which the sensor reports
    # as 0).
    assert coord.solar_gains["living_room"] == pytest.approx(123.0)
    # Heat flows need the outdoor temperature, so they are skipped.
    coord.model.compute_heat_flows.assert_not_called()


def test_publish_current_solar_gains_uses_resolved_cloud_cover():
    """The shared snapshot helper attenuates with the resolved cloud cover."""
    coord = object.__new__(HeatingAssistantCoordinator)
    coord.now_utc = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
    coord.cloud_cover = None
    coord._cloud_cover_filtered = 0.3  # e.g. EMA seeded from persistence
    coord.ghi_now = None
    coord.solar_gains = {}
    coord.model = SimpleNamespace(room_names=["living_room"])

    seen: dict = {}

    def _gain(_coord, name, now, cloud_cover, ghi):
        seen["cloud_cover"] = cloud_cover
        return 200.0

    with patch(_DISTURBANCES_ROOM_SOLAR, side_effect=_gain):
        coord._publish_current_solar_gains()

    assert seen["cloud_cover"] == pytest.approx(0.3)
    assert coord.solar_gains["living_room"] == pytest.approx(200.0)
    assert coord.cloud_cover == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_async_refresh_ui_seeds_runtime_state_before_refresh():
    """The fast UI refresh must load persisted runtime state before refreshing.

    Otherwise the very first post-restart refresh has no cloud-cover EMA to fall
    back on and emits an unattenuated clear-sky solar-gain spike.
    """
    coord = _make_live_coord(
        temp_states={"sensor.lr": "21.0"},
        temp_sensors={"living_room": ["sensor.lr"]},
        outdoor=5.0,
    )
    coord.async_update_listeners = MagicMock()

    await coord.async_refresh_ui()

    coord._ensure_runtime_state_loaded.assert_awaited_once()


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


def _make_forecast_coord() -> HeatingAssistantCoordinator:
    """Coordinator skeleton with minimal forecast-related state for WS tests."""
    from datetime import timezone

    coord = object.__new__(HeatingAssistantCoordinator)
    coord.predictions = [{"living_room": 20.0 + i * 0.1} for i in range(3)]
    coord.outdoor_forecast = [5.0, 5.1, 5.2]
    coord.solar_forecast = [{"living_room": 100.0 + i * 10} for i in range(3)]
    coord.heating_schedule = [{"living_room": 500.0} for _ in range(3)]
    coord.linearised_predictions = []
    coord.price_forecast = [0.10, 0.11, 0.12]
    coord.outdoor_temp = 4.5
    coord.solar_gains = {"living_room": 90.0}
    coord.filtered_temperatures = {"living_room": 19.8}
    coord._control_trajectory = None
    coord.now_utc = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    coord._update_interval_s = 300

    room = SimpleNamespace(
        temperature=19.5,
        setpoint=21.0,
        comfort_offset=2.0,
        windows=[],
    )
    coord.model = SimpleNamespace(
        room_names=["living_room"],
        rooms={"living_room": room},
    )
    coord._sources_by_room = {}  # used by sources_for_room()
    wire_room_enablement(coord)
    coord.build_forecast_payload = (
        HeatingAssistantCoordinator.build_forecast_payload.__get__(coord)
    )
    return coord


def test_build_forecast_payload_structure():
    coord = _make_forecast_coord()

    payload = coord.build_forecast_payload()

    assert "rooms" in payload
    assert "living_room" in payload["rooms"]
    room_fc = payload["rooms"]["living_room"]

    # trajectory is the scalar list of predicted temps
    assert len(room_fc["trajectory"]) == 3
    assert room_fc["trajectory"][0] == pytest.approx(20.0)

    # forecast array contains bridge entry + N horizon entries
    assert len(room_fc["forecast"]) == 4  # 1 bridge + 3 steps
    assert "temperature" in room_fc["forecast"][1]
    assert "setpoint" in room_fc["forecast"][0]
    assert "heating_power" in room_fc["forecast"][1]
    assert "solar_gain" in room_fc["forecast"][1]

    # outdoor and price forecasts present
    assert len(payload["outdoor_forecast"]) == 4  # 1 bridge + 3 steps
    assert len(payload["price_forecast"]) == 3
    assert payload["price_forecast"][0]["price"] == pytest.approx(0.10)
    assert payload["step_seconds"] == pytest.approx(300.0)


def test_forecast_sensor_attributes_no_large_arrays():
    """Forecast sensors must not store timestamped arrays in attributes.

    Attributes exceeding 16 KB cause HA Recorder warnings and can break the
    database.  The forecast data is now served via the
    heating_assistant/get_forecasts WebSocket command instead.
    """
    import json
    from custom_components.heating_assistant.sensor import (
        TemperatureForecastSensor,
        HeatingPowerForecastSensor,
        SolarGainForecastSensor,
        OutdoorTemperatureForecastSensor,
        ElectricityPriceForecastSensor,
    )

    coord = _make_forecast_coord()
    # SolarGainForecastSensor reads room.windows
    coord.model.rooms["living_room"].windows = []

    for SensorClass in (
        TemperatureForecastSensor,
        HeatingPowerForecastSensor,
        SolarGainForecastSensor,
    ):
        sensor = object.__new__(SensorClass)
        sensor._coordinator = coord
        sensor._room_name = "living_room"
        attrs = sensor.extra_state_attributes
        assert "forecast" not in attrs, f"{SensorClass.__name__} still exposes 'forecast' in attributes"
        # Must stay small enough to be serialisable without 16 KB concerns
        serialised = json.dumps(attrs)
        assert len(serialised) < 1000, f"{SensorClass.__name__} attributes too large: {len(serialised)} bytes"

    for SensorClass in (OutdoorTemperatureForecastSensor, ElectricityPriceForecastSensor):
        sensor = object.__new__(SensorClass)
        sensor._coordinator = coord
        attrs = sensor.extra_state_attributes
        assert "forecast" not in attrs, f"{SensorClass.__name__} still exposes 'forecast' in attributes"
        serialised = json.dumps(attrs)
        assert len(serialised) < 500


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

        def get_controller_config_snapshot(self):
            return {
                "update_interval": self.dt,
                "horizon": self._horizon,
            }

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
