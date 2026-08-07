"""Heating Assistant sensor platform — live measurement and control sensors."""

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

class TemperatureMeasuredSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Sensor reporting the room temperature measurement used by the integration.

    When multiple ``temp_sensors`` are configured for the room their readings
    are averaged each cycle; this sensor exposes that averaged value so
    dashboards don't have to build their own helper.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = (
            f"Heating Assistant – {room_name} – Temperature Measured"
        )
        self._attr_unique_id = f"{DOMAIN}_{room_name}_temperature_measured"

    @property
    def native_value(self) -> Optional[float]:
        temp = self._coordinator.measured_temperatures.get(self._room_name)
        if temp is None:
            return None
        return round(float(temp), 2)


class TemperatureFilteredSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Sensor reporting the Kalman-filtered room temperature estimate x̂⁺.

    The state estimator fuses the raw measurement with the thermal model
    so brief sensor glitches don't propagate into the MPC.  This sensor
    exposes the post-update filtered estimate after each coordinator cycle.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = (
            f"Heating Assistant – {room_name} – Temperature Filtered"
        )
        self._attr_unique_id = f"{DOMAIN}_{room_name}_temperature_filtered"

    @property
    def native_value(self) -> Optional[float]:
        temp = self._coordinator.filtered_temperatures.get(self._room_name)
        if temp is None:
            return None
        return round(float(temp), 2)

    @property
    def extra_state_attributes(self) -> dict:
        room = self._coordinator.model.rooms[self._room_name]
        snapshot = _kpi_room_snapshot(self._coordinator, self._room_name)
        deviation = room_comfort_deviation_c(snapshot)
        cached_pct = self._coordinator._time_in_range_pct_24h.get(self._room_name)
        time_in_range = None
        if (
            snapshot.room_active
            and room_temperature(snapshot) is not None
            and snapshot.constraint_lower is not None
            and snapshot.constraint_upper is not None
            and cached_pct is not None
        ):
            time_in_range = cached_pct
        return {
            "thermal_mass": room.thermal_mass,
            "r_external": room.r_external,
            "internal_gain": round(float(getattr(room, "internal_gain", 0.0)), 2),
            "solar_scale": round(float(getattr(room, "solar_scale", 1.0)), 4),
            "c_air_fraction": round(float(room.c_air_fraction), 4),
            "r_aw_fraction": round(float(room.r_aw_fraction), 4),
            "comfort_deviation": (
                round(deviation, 1) if deviation is not None else None
            ),
            "time_in_range_pct_24h": (
                round(time_in_range) if time_in_range is not None else None
            ),
        }


class WallTemperatureSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Diagnostic sensor for the EKF-reconstructed wall/mass-node temperature.

    The 2R2C wall node is never measured — the filter reconstructs it from
    the air-temperature dynamics.  The state is the wall estimate [°C]; the
    attributes carry the observability health signals:

    * ``posterior_std`` — EKF posterior std of the wall state [°C].  Should
      contract after start-up and stay bounded; a non-contracting value
      means the wall state is drifting unobserved.
    * ``observability`` — conditioning of the wall-state reconstruction
      (``model_diagnostics.wall_state_observability``; 1 = ideal, ~0 =
      practically unobservable).
    * the room's identified envelope split and solar scale, so the model
      parameters relevant to the wall node are visible in one place.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = (
            f"Heating Assistant – {room_name} – Wall Temperature"
        )
        self._attr_unique_id = f"{DOMAIN}_{room_name}_wall_temperature"

    @property
    def native_value(self) -> Optional[float]:
        temp = getattr(self._coordinator, "wall_temperatures", {}).get(
            self._room_name
        )
        if temp is None:
            return None
        return round(float(temp), 2)

    @property
    def extra_state_attributes(self) -> dict:
        room = self._coordinator.model.rooms[self._room_name]
        std = getattr(self._coordinator, "wall_temperature_stds", {}).get(
            self._room_name
        )
        try:
            from ..model_diagnostics import wall_state_observability
            obs = wall_state_observability(room, dt=self._coordinator.dt)
        except Exception:
            obs = None
        return {
            "posterior_std": round(float(std), 3) if std is not None else None,
            "observability": round(float(obs), 4) if obs is not None else None,
            "c_air_fraction": round(float(room.c_air_fraction), 4),
            "r_aw_fraction": round(float(room.r_aw_fraction), 4),
            "solar_scale": round(float(room.solar_scale), 4),
        }


class TemperatureOffsetSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Sensor reporting the EKF-estimated measurement offset state b [°C]."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:thermometer-lines"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = (
            f"Heating Assistant – {room_name} – Temperature Offset"
        )
        self._attr_unique_id = f"{DOMAIN}_{room_name}_temperature_offset"

    @property
    def native_value(self) -> Optional[float]:
        try:
            offset = self._coordinator.controller.temperature_offsets.get(self._room_name)
        except Exception:
            return None
        if offset is None:
            return None
        return round(float(offset), 3)


class InternalGainEstimatedSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Sensor reporting the online-estimated internal heat gain for a room [W].

    The internal gain is promoted to a state in the CD-EKF and estimated online
    via a regularised (Ornstein–Uhlenbeck) augmented-state process.  The state
    is the total estimated gain (configured nominal + estimated deviation Δĝ);
    the deviation and nominal are exposed as attributes.  Recorded in HA history
    so the dashboard can plot how the learned gain evolves over time.
    """

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:home-thermometer"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = (
            f"Heating Assistant – {room_name} – Internal Gain Estimated"
        )
        self._attr_unique_id = f"{DOMAIN}_{room_name}_internal_gain_estimated"

    @property
    def native_value(self) -> Optional[float]:
        gain = self._coordinator.estimated_internal_gains.get(self._room_name)
        if gain is None:
            return None
        return round(float(gain), 1)

    @property
    def extra_state_attributes(self) -> dict:
        room = self._coordinator.model.rooms.get(self._room_name)
        nominal = float(room.internal_gain) if room is not None else 0.0
        total = self._coordinator.estimated_internal_gains.get(self._room_name)
        attrs: Dict[str, Any] = {
            "nominal_gain_w": round(nominal, 1),
            "estimation_enabled": bool(
                getattr(self._coordinator.controller, "gain_estimation_enabled", False)
            ),
        }
        if total is not None:
            attrs["estimated_deviation_w"] = round(float(total) - nominal, 1)
        return attrs


# ---------------------------------------------------------------------------
# Setpoint and soft-constraint sensors (per room)
# ---------------------------------------------------------------------------

class SetpointSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Sensor reporting the active per-room setpoint [°C].

    Exposes both the current scalar (used by entities cards) and a
    timestamped ``forecast`` attribute that spans the MPC horizon, so
    apexcharts dashboards can draw the setpoint reference in the forecast
    region with the same ``data_generator`` pattern used for the predicted
    trajectory.  The setpoint is the same value across the horizon (the
    controller uses the current scalar as its z_ref), so the line is flat
    by construction — but it stays anchored to the forecast time window
    rather than relying on apexcharts's ``extend_to`` behaviour.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:target"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Setpoint"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_setpoint"

    @property
    def native_value(self) -> Optional[float]:
        room = self._coordinator.model.rooms.get(self._room_name)
        if room is None:
            return None
        if not self._coordinator.is_room_enabled(self._room_name):
            return None
        return round(float(room.setpoint), 2)

    @property
    def extra_state_attributes(self) -> dict:
        traj = getattr(self._coordinator, "_control_trajectory", None)
        step_sp = None
        if traj is not None:
            step_sp = traj.setpoints.get(self._room_name)
        return _build_horizon_forecast(
            self._coordinator,
            self._room_name,
            field="setpoint",
            value=_setpoint_value,
            step_values=step_sp,
        )


class WindowStateSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Per-room open-window state-machine state (Phase 3 W1)."""

    _attr_icon = "mdi:window-open-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Window State"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_window_state"

    @property
    def native_value(self) -> str:
        state = getattr(self._coordinator, "get_window_state", None)
        if callable(state):
            return state(self._room_name)
        return "closed"


class ConstraintUpperSensor(_ConstraintSensorBase):
    """Upper bound of the MPC soft output constraint (setpoint + offset)."""

    _attr_icon = "mdi:arrow-up-bold-box-outline"
    _sign = 1.0
    _attr_field = "constraint_upper"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator, room_name)
        self._attr_name = (
            f"Heating Assistant – {room_name} – Constraint Upper"
        )
        self._attr_unique_id = f"{DOMAIN}_{room_name}_constraint_upper"


class ConstraintLowerSensor(_ConstraintSensorBase):
    """Lower bound of the MPC soft output constraint (setpoint − offset)."""

    _attr_icon = "mdi:arrow-down-bold-box-outline"
    _sign = -1.0
    _attr_field = "constraint_lower"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator, room_name)
        self._attr_name = (
            f"Heating Assistant – {room_name} – Constraint Lower"
        )
        self._attr_unique_id = f"{DOMAIN}_{room_name}_constraint_lower"

class HeatingPowerMeasuredSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Sensor reporting the total active heating/cooling power for a room.

    Positive values indicate heating, negative values indicate cooling
    (heat removal when heat pumps operate in dry/dehumidify mode).
    """

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = (
            f"Heating Assistant – {room_name} – Heating Power Measured"
        )
        self._attr_unique_id = f"{DOMAIN}_{room_name}_heating_power_measured"

    @property
    def native_value(self) -> float:
        """Return the sum of current heater powers for the room [W]."""
        sources = self._coordinator.sources_for_room(self._room_name)
        return round(sum(s.current_power for s in sources), 1)

    @property
    def extra_state_attributes(self) -> dict:
        sources = self._coordinator.sources_for_room(self._room_name)
        return {
            src.name: round(src.current_power, 1)
            for src in sources
        }


# ---------------------------------------------------------------------------
# Solar gain sensor
# ---------------------------------------------------------------------------

class SolarGainMeasuredSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Sensor reporting the current solar heat gain for a room [W]."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:white-balance-sunny"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = (
            f"Heating Assistant – {room_name} – Solar Gain Measured"
        )
        self._attr_unique_id = f"{DOMAIN}_{room_name}_solar_gain_measured"

    @property
    def native_value(self) -> float:
        gain = self._coordinator.solar_gains.get(self._room_name, None)
        if gain is None:
            _LOGGER.debug(
                "No solar gain data for room %s; defaulting to 0", self._room_name
            )
            return 0.0
        return round(gain, 1)

    @property
    def extra_state_attributes(self) -> dict:
        room = self._coordinator.model.rooms[self._room_name]
        return {
            "window_count": len(room.windows),
            "total_window_area": round(sum(w.area for w in room.windows), 2),
        }


# ---------------------------------------------------------------------------
# Control action sensor (per heat source)
# ---------------------------------------------------------------------------

class ControlActionSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Sensor reporting the MPC control action for a heat source [%]."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:tune-vertical"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        source_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._source_name = source_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {source_name} – Control Action"
        self._attr_unique_id = f"{DOMAIN}_{source_name}_control_action"

    @property
    def native_value(self) -> float:
        fraction = self._coordinator.actions.get(self._source_name, 0.0)
        return round(fraction * 100.0, 1)

    @property
    def extra_state_attributes(self) -> dict:
        src = next(
            (s for s in self._coordinator.heat_sources if s.name == self._source_name),
            None,
        )
        if src is None:
            return {}
        return {
            "room": src.room,
            "max_power": src.max_power,
            "current_power": round(src.current_power, 1),
        }


# ---------------------------------------------------------------------------
# Heat pump COP sensor
# ---------------------------------------------------------------------------

class HeatPumpCOPSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Sensor reporting the current COP of a heat pump source."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:heat-pump-outline"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        source_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._source_name = source_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {source_name} – COP"
        self._attr_unique_id = f"{DOMAIN}_{source_name}_cop"

    @property
    def native_value(self) -> Optional[float]:
        src = next(
            (s for s in self._coordinator.heat_sources if s.name == self._source_name),
            None,
        )
        if src is None or not isinstance(src, HeatPump):
            return None
        outdoor_temp = self._coordinator.outdoor_temp
        if outdoor_temp is None:
            return None
        return round(src.cop(outdoor_temp), 2)

    @property
    def extra_state_attributes(self) -> dict:
        src = next(
            (s for s in self._coordinator.heat_sources if s.name == self._source_name),
            None,
        )
        if src is None or not isinstance(src, HeatPump):
            return {}
        return {
            "cop_rated": src.cop_rated,
            "cop_temp_ref": src.cop_temp_ref,
            "min_power": src.min_power,
            "outdoor_temp": self._coordinator.outdoor_temp,
        }


# ---------------------------------------------------------------------------
# Outdoor temperature sensor
# ---------------------------------------------------------------------------

class OutdoorTemperatureMeasuredSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Sensor reporting the outdoor temperature as read by the integration."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
    ) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – Outdoor Temperature Measured"
        self._attr_unique_id = f"{DOMAIN}_outdoor_temperature_measured"

    @property
    def native_value(self) -> Optional[float]:
        t = self._coordinator.outdoor_temp
        return None if t is None else round(t, 2)

class HeatLossSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the instantaneous heat-loss breakdown for a room.

    The state is the total heat loss [W] (positive = losing heat).
    Individual components (external loss, flow to/from each connected room)
    are exposed as state attributes.
    """

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:thermometer-minus"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Heat Loss"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_heat_loss"

    @property
    def native_value(self) -> float:
        flows = self._coordinator.heat_flows.get(self._room_name, {})
        return round(flows.get("total_loss", 0.0), 1)

    @property
    def extra_state_attributes(self) -> dict:
        flows = self._coordinator.heat_flows.get(self._room_name, {})
        room = self._coordinator.model.rooms[self._room_name]
        attrs: Dict[str, Any] = dict(flows)
        attrs["outdoor_temp"] = self._coordinator.outdoor_temp
        attrs["room_temp"] = round(room.temperature, 2)
        # Typed per-connection flow list so dashboards (e.g. a Sankey card)
        # can iterate over connections without having to filter the special
        # ``external_loss``/``total_loss`` keys out of the flat dict.
        attrs["connection_flows"] = [
            {"to_room": key, "watts": float(value)}
            for key, value in flows.items()
            if key not in ("external_loss", "total_loss")
        ]
        return attrs


# ---------------------------------------------------------------------------
# Energy balance sensor (per room)
# ---------------------------------------------------------------------------

class EnergyBalanceSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the net energy balance for a room [W].

    Positive = room is gaining energy (heating up).
    Negative = room is losing energy (cooling down).

    The attributes give a detailed breakdown of all energy flows.
    """

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:scale-balance"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Energy Balance"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_energy_balance"

    @property
    def native_value(self) -> float:
        sources = self._coordinator.sources_for_room(self._room_name)
        heating = sum(s.current_power for s in sources)
        solar = self._coordinator.solar_gains.get(self._room_name, 0.0)
        flows = self._coordinator.heat_flows.get(self._room_name, {})
        total_loss = flows.get("total_loss", 0.0)
        net = heating + solar - total_loss
        return round(net, 1)

    @property
    def extra_state_attributes(self) -> dict:
        sources = self._coordinator.sources_for_room(self._room_name)
        heating = sum(s.current_power for s in sources)
        solar = self._coordinator.solar_gains.get(self._room_name, 0.0)
        flows = self._coordinator.heat_flows.get(self._room_name, {})
        total_loss = flows.get("total_loss", 0.0)
        external_loss = flows.get("external_loss", 0.0)
        room = self._coordinator.model.rooms[self._room_name]

        inter_room_exchange = total_loss - external_loss

        return {
            "heating_power": round(heating, 1),
            "solar_gain": round(solar, 1),
            "external_heat_loss": round(external_loss, 1),
            "inter_room_heat_exchange": round(inter_room_exchange, 1),
            "total_heat_loss": round(total_loss, 1),
            "net_energy_flow": round(heating + solar - total_loss, 1),
            "room_temperature": round(room.temperature, 2),
            "setpoint": room.setpoint,
        }


# ---------------------------------------------------------------------------
# System efficiency sensor
# ---------------------------------------------------------------------------

class SystemEfficiencySensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    System-wide sensor reporting aggregate heating metrics.

    The state is the total heating power across all sources [W].
    Attributes include per-room breakdowns, total heat loss, total solar
    gain, and an effective system COP (for heat pump systems).
    """

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:home-thermometer"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
    ) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – System Summary"
        self._attr_unique_id = f"{DOMAIN}_system_summary"

    @property
    def native_value(self) -> float:
        return round(
            sum(s.current_power for s in self._coordinator.heat_sources), 1
        )

    @property
    def extra_state_attributes(self) -> dict:
        sources = self._coordinator.heat_sources
        total_heating = sum(s.current_power for s in sources)
        total_solar = sum(self._coordinator.solar_gains.values())
        total_loss = sum(
            f.get("total_loss", 0.0)
            for f in self._coordinator.heat_flows.values()
        )

        # Per-room heating power (uses cached room → sources index so this is
        # O(N + M) instead of O(N × M) for N rooms and M sources).
        room_heating: Dict[str, float] = {}
        for name in self._coordinator.model.room_names:
            room_heating[name] = round(
                sum(
                    s.current_power
                    for s in self._coordinator.sources_for_room(name)
                ),
                1,
            )

        # Effective system COP (thermal output / electrical input)
        electrical_input = 0.0
        _outdoor_temp = self._coordinator.outdoor_temp
        for src in sources:
            if isinstance(src, HeatPump):
                cop = src.cop(_outdoor_temp) if _outdoor_temp is not None else 0.0
                if cop > 0:
                    electrical_input += src.current_power / cop
                # If COP is 0, heat pump is off, no electrical input
            else:
                electrical_input += src.current_power

        effective_cop = (
            round(total_heating / electrical_input, 2)
            if electrical_input > 0
            else 0.0
        )

        # Count active sources
        active_sources = sum(1 for s in sources if s.current_power > 0)
        has_heat_pump = any(isinstance(src, HeatPump) for src in sources)
        room_snapshots = _kpi_room_snapshots(self._coordinator)
        comfort_index = comfort_index_pct(room_snapshots)

        return {
            "total_heating_power": round(total_heating, 1),
            "total_solar_gain": round(total_solar, 1),
            "total_heat_loss": round(total_loss, 1),
            "net_energy_flow": round(total_heating + total_solar - total_loss, 1),
            "effective_system_cop": effective_cop,
            "electrical_input_estimate": round(electrical_input, 1),
            "active_sources": active_sources,
            "total_sources": len(sources),
            "has_heat_pump": has_heat_pump,
            "comfort_index_pct": (
                round(comfort_index) if comfort_index is not None else None
            ),
            "room_heating_power": room_heating,
            "outdoor_temperature": self._coordinator.outdoor_temp,
            "system_enabled": self._coordinator.system_enabled,
        }

class HeatingEnergyTotalSensor(CoordinatorEntity, RestoreSensor):
    """Cumulative thermal energy delivered by a heat source [kWh].

    The accumulator advances by ``current_power × dt`` on each coordinator
    update and is persisted via :class:`RestoreSensor` so totals survive
    restarts. ``state_class = TOTAL_INCREASING`` lets the value participate
    in Home Assistant's Energy dashboard.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:lightning-bolt"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        source_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._source_name = source_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {source_name} – Energy Total"
        self._attr_unique_id = f"{DOMAIN}_{source_name}_energy_total"
        self._total_kwh: float = 0.0
        # ``_last_update_ts`` tracks wall-clock time so we integrate over
        # the real elapsed interval even when the coordinator runs a
        # missed-cycle catch-up or the user changes ``update_interval``.
        self._last_update_ts: Optional[float] = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        restored = await self.async_get_last_sensor_data()
        if restored is not None and restored.native_value is not None:
            try:
                self._total_kwh = float(restored.native_value)
            except (TypeError, ValueError):
                self._total_kwh = 0.0

    def _source(self):
        return next(
            (s for s in self._coordinator.heat_sources if s.name == self._source_name),
            None,
        )

    @property
    def native_value(self) -> float:
        # Integrate on each access driven by a coordinator update. The
        # CoordinatorEntity base ensures the value is requested whenever
        # the coordinator's ``last_update_success`` fires.
        from datetime import datetime, timezone

        src = self._source()
        if src is None:
            return round(self._total_kwh, 6)

        now_ts = datetime.now(tz=timezone.utc).timestamp()
        power_w = max(0.0, float(getattr(src, "current_power", 0.0) or 0.0))
        if self._last_update_ts is not None:
            dt_s = max(0.0, now_ts - self._last_update_ts)
            if dt_s > 0.0:
                self._total_kwh += power_w * dt_s / 3_600_000.0
        self._last_update_ts = now_ts
        return round(self._total_kwh, 6)

    @property
    def extra_state_attributes(self) -> dict:
        src = self._source()
        return {
            "source_name": self._source_name,
            "current_power": round(float(getattr(src, "current_power", 0.0) or 0.0), 1) if src else None,
            "room": getattr(src, "room", None) if src else None,
        }
