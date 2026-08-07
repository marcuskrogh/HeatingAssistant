"""Heating Assistant sensor platform — forecast and price sensors."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN
from ..coordinator import HeatingAssistantCoordinator
from ..heat_sources import HeatPump
from ..kpi import (
    RoomSnapshot,
    comfort_index_pct,
    mean_tracking_error_c,
    room_comfort_deviation_c,
    room_temperature,
)
from ..naming import slugify
from .base import (
    _LiveValueSensorMixin,
    _ConstraintSensorBase,
    _build_horizon_forecast,
    _closed_loop_fit_for_room,
    _constraint_bound,
    _kpi_room_snapshot,
    _kpi_room_snapshots,
    _room_estimation_provenance,
    _setpoint_value,
)

_LOGGER = logging.getLogger(__name__)

class OutdoorTemperatureForecastSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the outdoor temperature forecast over the MPC horizon.

    The state is the current outdoor temperature [°C].  The full forecast is
    exposed as a timestamped ``forecast`` attribute for dashboard visualisation.
    When a weather entity is configured, the forecast reflects the interpolated
    weather predictions; otherwise a persistence forecast (current value held
    constant) is used.

    ``device_class``/``state_class`` are ``None`` so HA's strict sensor
    validator accepts forecast values (which must not feed long-term
    statistics), and ``available`` stays ``True`` so the cached trajectory
    survives transient coordinator-update failures.
    """

    _attr_device_class = None
    _attr_state_class = None
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:thermometer-lines"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
    ) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – Outdoor Temperature Forecast"
        self._attr_unique_id = f"{DOMAIN}_outdoor_temperature_forecast"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> Optional[float]:
        t = self._coordinator.outdoor_temp
        return None if t is None else round(t, 2)

    @property
    def extra_state_attributes(self) -> dict:
        outdoor_forecast = self._coordinator.outdoor_forecast
        dt = self._coordinator.dt
        return {
            "horizon_steps": len(outdoor_forecast),
            "step_seconds": dt,
            "horizon_minutes": round(len(outdoor_forecast) * dt / 60, 1),
        }


# ---------------------------------------------------------------------------
# Temperature forecast sensor (per room)
# ---------------------------------------------------------------------------

class TemperatureForecastSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the MPC-predicted temperature trajectory for a room.

    The state is the predicted temperature at the end of the prediction
    horizon.  The full trajectory (one value per time step) is exposed as
    state attributes so users can plot it in Lovelace or use it in
    automations.

    ``device_class``/``state_class`` are ``None`` so HA's strict sensor
    validator accepts forecast values (which must not feed long-term
    statistics), and ``available`` stays ``True`` so the cached trajectory
    survives transient coordinator-update failures.
    """

    _attr_device_class = None
    _attr_state_class = None
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:chart-line"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Temperature Forecast"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_temperature_forecast"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> Optional[float]:
        predictions = self._coordinator.predictions
        if not predictions:
            # Surface failure: when the MPC has no trajectory (cold start or
            # solver failure) leave the state "unknown" so the dashboard
            # header and the recorder show a visible gap, not a fake value.
            return None
        last = predictions[-1]
        temp = last.get(self._room_name)
        return round(temp, 2) if temp is not None else None

    @property
    def extra_state_attributes(self) -> dict:
        predictions = self._coordinator.predictions
        room = self._coordinator.model.rooms[self._room_name]
        dt = self._coordinator.dt
        comfort_offset = float(getattr(room, "comfort_offset", 2.0))

        # Scalar trajectory kept in attributes so it fits in HA Recorder.
        # The full timestamped forecast array is served via the
        # heating_assistant/get_forecasts WebSocket command.
        trajectory: List[float] = []
        for pred in predictions:
            temp = pred.get(self._room_name)
            if temp is not None:
                trajectory.append(round(temp, 2))

        filtered_now = self._coordinator.filtered_temperatures.get(self._room_name)
        now_temp = (
            round(float(filtered_now), 2)
            if filtered_now is not None
            else round(room.temperature, 2)
        )

        return {
            "trajectory": trajectory,
            "setpoint": room.setpoint,
            "comfort_offset": comfort_offset,
            "current_temperature": now_temp,
            "horizon_steps": len(predictions),
            "step_seconds": dt,
            "horizon_minutes": round(len(predictions) * dt / 60, 1),
        }

class HeatingPowerForecastSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the planned heating/cooling power over the MPC horizon for a room.

    The state is the current planned heating power [W]. Negative values indicate
    cooling (heat removal) when heat pumps operate in dry/dehumidify mode.
    The full schedule is exposed as a timestamped ``forecast`` attribute so it
    can be plotted in dashboard cards like ``apexcharts-card``.

    ``device_class``/``state_class`` are ``None`` so HA's strict sensor
    validator accepts forecast values (which must not feed long-term
    statistics), and ``available`` stays ``True`` so the cached trajectory
    survives transient coordinator-update failures.
    """

    _attr_device_class = None
    _attr_state_class = None
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:radiator"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = (
            f"Heating Assistant – {room_name} – Heating Power Forecast"
        )
        self._attr_unique_id = f"{DOMAIN}_{room_name}_heating_power_forecast"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> Optional[float]:
        schedule = self._coordinator.heating_schedule
        if not schedule:
            # Surface failure: an empty schedule means the MPC produced no
            # plan this cycle.  Leave the state "unknown" rather than
            # echoing current heating power as if it were a real plan.
            return None
        return round(schedule[0].get(self._room_name, 0.0), 1)

    @property
    def extra_state_attributes(self) -> dict:
        schedule = self._coordinator.heating_schedule
        dt = self._coordinator.dt
        sources = self._coordinator.sources_for_room(self._room_name)
        max_power = sum(s.max_power for s in sources)
        return {
            "horizon_steps": len(schedule),
            "step_seconds": dt,
            "max_power": max_power if max_power > 0 else None,
        }


# ---------------------------------------------------------------------------
# Solar forecast sensor (per room)
# ---------------------------------------------------------------------------

class SolarGainForecastSensor(CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the predicted solar gain over the MPC horizon for a room.

    The state is the current solar gain [W].  The full forecast is exposed as
    a timestamped ``forecast`` attribute for dashboard visualisation.

    ``device_class``/``state_class`` are ``None`` so HA's strict sensor
    validator accepts forecast values (which must not feed long-term
    statistics), and ``available`` stays ``True`` so the cached trajectory
    survives transient coordinator-update failures.
    """

    _attr_device_class = None
    _attr_state_class = None
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:weather-sunny-alert"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = (
            f"Heating Assistant – {room_name} – Solar Gain Forecast"
        )
        self._attr_unique_id = f"{DOMAIN}_{room_name}_solar_gain_forecast"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> Optional[float]:
        solar_forecast = self._coordinator.solar_forecast
        if not solar_forecast:
            # Surface failure: when no forecast is available leave the state
            # "unknown" rather than echoing the current solar gain.
            return None
        return round(solar_forecast[0].get(self._room_name, 0.0), 1)

    @property
    def extra_state_attributes(self) -> dict:
        solar_forecast = self._coordinator.solar_forecast
        dt = self._coordinator.dt
        room = self._coordinator.model.rooms[self._room_name]
        horizon_steps = max(0, len(solar_forecast) - 1)
        return {
            "horizon_steps": horizon_steps,
            "step_seconds": dt,
            "window_count": len(room.windows),
            "total_window_area": round(sum(w.area for w in room.windows), 2),
        }


# ---------------------------------------------------------------------------
# Electricity price sensors (system-wide) — current + forecast
# ---------------------------------------------------------------------------


class ElectricityPriceSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Current electricity spot price from the configured price entity.

    The state mirrors ``coordinator.price_forecast[0]`` (the price for the
    current control interval), so the sensor updates every coordinator cycle
    and stays aligned with the value the MPC actually used.  The unit of
    measurement is read dynamically from the underlying price entity so it
    reflects whatever currency/kWh unit Nord Pool / Tibber reports.
    """

    _attr_device_class = None
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    _attr_icon = "mdi:cash"

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – Electricity Price"
        self._attr_unique_id = f"{DOMAIN}_electricity_price"

    @property
    def native_unit_of_measurement(self) -> Optional[str]:
        price_entity = getattr(self._coordinator, "_price_entity", None)
        if not price_entity:
            return None
        state = self._coordinator.hass.states.get(price_entity)
        if state is None:
            return None
        return state.attributes.get("unit_of_measurement")

    @property
    def native_value(self) -> Optional[float]:
        forecast = self._coordinator.price_forecast
        if not forecast:
            return None
        try:
            return round(float(forecast[0]), 5)
        except (TypeError, ValueError, IndexError):
            return None

    @property
    def extra_state_attributes(self) -> dict:
        forecast = self._coordinator.price_forecast or []
        return {
            "has_forecast": len(forecast) > 0,
            "horizon_steps": len(forecast),
        }


class ElectricityPriceForecastSensor(CoordinatorEntity, SensorEntity):
    """Electricity price forecast over the MPC prediction horizon.

    The state is the current price (identical to :class:`ElectricityPriceSensor`).
    The full timestamped forecast is exposed as a ``forecast`` attribute for
    ``apexcharts-card`` dashboard plots.

    ``device_class``/``state_class`` are ``None`` — forecasted prices are
    predictions, not historical measurements — and ``available`` returns
    ``True`` so the cached forecast survives transient coordinator failures.
    """

    _attr_device_class = None
    _attr_state_class = None
    _attr_suggested_display_precision = 4
    _attr_icon = "mdi:lightning-bolt-circle"

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – Electricity Price Forecast"
        self._attr_unique_id = f"{DOMAIN}_electricity_price_forecast"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_unit_of_measurement(self) -> Optional[str]:
        price_entity = getattr(self._coordinator, "_price_entity", None)
        if not price_entity:
            return None
        state = self._coordinator.hass.states.get(price_entity)
        if state is None:
            return None
        return state.attributes.get("unit_of_measurement")

    @property
    def native_value(self) -> Optional[float]:
        forecast = self._coordinator.price_forecast
        if not forecast:
            return None
        try:
            return round(float(forecast[0]), 5)
        except (TypeError, ValueError, IndexError):
            return None

    @property
    def extra_state_attributes(self) -> dict:
        forecast = self._coordinator.price_forecast or []
        dt = self._coordinator.dt
        return {
            "horizon_steps": len(forecast),
            "step_seconds": dt,
            "horizon_minutes": round(len(forecast) * dt / 60, 1),
        }
