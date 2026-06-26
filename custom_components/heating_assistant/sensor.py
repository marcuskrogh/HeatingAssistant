"""
Heating Assistant – Sensor platform.

Per-room sensors use a consistent ``_measured`` / ``_filtered`` / ``_forecast``
suffix so the advanced-visualisation dashboards can be pasted verbatim:

- ``temperature_measured``  – averaged raw measurement from configured sensors [°C]
- ``temperature_filtered``  – Kalman-filtered state estimate x̂⁺ [°C]
- ``temperature_forecast``  – MPC predicted trajectory (state = end-of-horizon) [°C]
- ``heating_power_measured`` – sum of active heater outputs (negative = cooling) [W]
- ``heating_power_forecast`` – planned heating/cooling power over the MPC horizon [W]
- ``solar_gain_measured``    – current solar heat gain through windows [W]
- ``solar_gain_forecast``    – predicted solar gain over the MPC horizon [W]
- ``setpoint``               – current per-room setpoint [°C]
- ``constraint_upper`` / ``constraint_lower`` – setpoint ± soft-constraint band [°C]
- ``heat_loss``              – instantaneous heat-loss breakdown [W]
- ``energy_balance``         – net energy flow in the room [W]

Per-heat-source: ``control_action`` (MPC fraction [%]); heat pumps also expose ``cop``.

System-wide: ``outdoor_temperature_measured`` / ``outdoor_temperature_forecast``,
``system_summary``, and ``mpc_performance``.

The forecast sensors set ``device_class``/``state_class`` to ``None`` (so HA's
strict sensor validator accepts them — predictions are not measurements and
must not feed long-term statistics) and override ``available`` to ``True`` so
dashboards keep rendering the cached trajectory across transient coordinator
update failures.
"""

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
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HeatingAssistantCoordinator
from .dashboard import slugify
from .heat_sources import HeatPump
from .kpi import (
    RoomSnapshot,
    comfort_index_pct,
    mean_tracking_error_c,
    room_comfort_deviation_c,
    room_temperature,
)

_LOGGER = logging.getLogger(__name__)


def _preload_model_diagnostics() -> None:
    from . import model_diagnostics  # noqa: F401 – triggers scipy import in executor thread


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Heating Assistant sensor entities from a config entry."""
    coordinator: HeatingAssistantCoordinator = hass.data[DOMAIN][entry.entry_id]

    await hass.async_add_executor_job(_preload_model_diagnostics)

    entities: List[SensorEntity] = []

    # Per-room sensors
    for room_name in coordinator.model.room_names:
        entities.append(TemperatureMeasuredSensor(coordinator, room_name))
        entities.append(TemperatureFilteredSensor(coordinator, room_name))
        entities.append(WallTemperatureSensor(coordinator, room_name))
        entities.append(TemperatureOffsetSensor(coordinator, room_name))
        entities.append(InternalGainEstimatedSensor(coordinator, room_name))
        entities.append(TemperatureForecastSensor(coordinator, room_name))
        entities.append(SetpointSensor(coordinator, room_name))
        entities.append(WindowStateSensor(coordinator, room_name))
        entities.append(ConstraintUpperSensor(coordinator, room_name))
        entities.append(ConstraintLowerSensor(coordinator, room_name))
        entities.append(HeatingPowerMeasuredSensor(coordinator, room_name))
        entities.append(HeatingPowerForecastSensor(coordinator, room_name))
        entities.append(SolarGainMeasuredSensor(coordinator, room_name))
        entities.append(SolarGainForecastSensor(coordinator, room_name))
        entities.append(HeatLossSensor(coordinator, room_name))
        entities.append(EnergyBalanceSensor(coordinator, room_name))
        # Model fit diagnostics sensors
        entities.append(PredictionErrorSensor(coordinator, room_name))
        entities.append(ModelFitQualitySensor(coordinator, room_name))
        entities.append(ParameterConfidenceSensor(coordinator, room_name))
        # Advanced model quality sensors
        entities.append(OpenLoopRMSESensor(coordinator, room_name))
        entities.append(KalmanInnovationSensor(coordinator, room_name))
        entities.append(ResidualACFSensor(coordinator, room_name))
        entities.append(LoglikSliceSensor(coordinator, room_name))
        entities.append(SysIdSimulationSensor(coordinator, room_name))

    # Per-source sensors
    for src in coordinator.heat_sources:
        entities.append(ControlActionSensor(coordinator, src.name))
        entities.append(HeaterScaleSensor(coordinator, src.name))
        entities.append(HeatingEnergyTotalSensor(coordinator, src.name))
        if isinstance(src, HeatPump):
            entities.append(HeatPumpCOPSensor(coordinator, src.name))

    # System-wide sensors
    entities.append(OutdoorTemperatureMeasuredSensor(coordinator))
    entities.append(OutdoorTemperatureForecastSensor(coordinator))
    entities.append(SystemEfficiencySensor(coordinator))
    entities.append(EstimatedParametersStatusSensor(coordinator))
    entities.append(MPCPerformanceSensor(coordinator))
    entities.append(WeatherForecastStatusSensor(coordinator))
    entities.append(SolarRadiationStatusSensor(coordinator))
    entities.append(ControllerConfigSensor(coordinator))

    # Price sensors — only when a price entity is configured
    if getattr(coordinator, "_price_entity", None):
        entities.append(ElectricityPriceSensor(coordinator))
        entities.append(ElectricityPriceForecastSensor(coordinator))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Mixin: keep live-value sensors visible across transient update failures
# ---------------------------------------------------------------------------

class _LiveValueSensorMixin:
    """Keep a sensor available even when a coordinator update cycle fails.

    The live readout sensors (measured / filtered temperature, setpoint, solar
    gain, power, model-fit KPIs, …) read their value from coordinator *instance
    attributes* that persist between cycles and are refreshed by BOTH the
    scheduled MPC tick and the fast UI refresh.  By default ``CoordinatorEntity``
    ties ``available`` to ``coordinator.last_update_success``, so a single failed
    update (e.g. a transient sensor/weather/solver hiccup) makes every one of
    these entities report ``unavailable`` — the dashboard then shows no KPIs,
    setpoints, or measurements until the next *successful* full update, even
    though the last-known values are perfectly usable.

    Mirroring the forecast sensors (which already pin ``available`` to ``True``),
    this mixin keeps the cached live value on screen.  ``native_value`` still
    returns ``None`` — surfaced as ``unknown`` — when there is genuinely no data
    yet, so nothing fabricated is ever displayed.
    """

    @property
    def available(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Temperature sensors (measured and Kalman-filtered) — per room
# ---------------------------------------------------------------------------

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
            from .model_diagnostics import wall_state_observability
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


class _ConstraintSensorBase(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Shared base for the MPC soft-constraint bound sensors.

    Exposes both the current scalar (used by entities cards) and a
    timestamped ``forecast`` attribute that spans the MPC horizon.  The
    band ``setpoint ± offset`` is constant unless the setpoint changes, so
    the line is flat by construction — but it stays anchored to the
    forecast time window, matching the predicted-temperature trace.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1
    _sign: float = 1.0
    _attr_field: str = "constraint"

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator

    @property
    def native_value(self) -> Optional[float]:
        if not self._coordinator.is_room_enabled(self._room_name):
            return None
        bound = _constraint_bound(
            self._coordinator, self._room_name, self._sign,
        )
        return None if bound is None else round(bound, 2)

    @property
    def extra_state_attributes(self) -> dict:
        sign = self._sign
        field = self._attr_field

        def _value(coord: Any, room_name: str) -> Optional[float]:
            return _constraint_bound(coord, room_name, sign)

        traj = getattr(self._coordinator, "_control_trajectory", None)
        step_bounds = None
        if traj is not None:
            sp_seq = traj.setpoints.get(self._room_name)
            off_seq = traj.comfort_offsets.get(self._room_name)
            if sp_seq is not None and off_seq is not None:
                import numpy as np
                step_bounds = sp_seq + sign * off_seq

        return _build_horizon_forecast(
            self._coordinator,
            self._room_name,
            field=field,
            value=_value,
            step_values=step_bounds,
        )


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


# ---------------------------------------------------------------------------
# Helpers for setpoint / constraint forecast attributes
# ---------------------------------------------------------------------------

def _setpoint_value(
    coordinator: HeatingAssistantCoordinator, room_name: str,
) -> Optional[float]:
    room = coordinator.model.rooms.get(room_name)
    if room is None:
        return None
    return round(float(room.setpoint), 2)


def _constraint_bound(
    coordinator: HeatingAssistantCoordinator,
    room_name: str,
    sign: float,
) -> Optional[float]:
    """Compute effective comfort-region bound for a room, or None if unknown.

    Precedence:
      1. Explicit ``room.comfort_corridor_high`` / ``comfort_corridor_low``
         (set externally or by occupancy/mode overrides).
      2. Derived bound ``room.setpoint ± room.comfort_offset``.
    """
    room = coordinator.model.rooms.get(room_name)
    if room is None:
        return None
    explicit_attr = "comfort_corridor_high" if sign > 0 else "comfort_corridor_low"
    explicit = getattr(room, explicit_attr, None)
    if explicit is not None:
        return float(explicit)
    offset = getattr(room, "comfort_offset", None)
    if offset is None:
        return None
    return float(room.setpoint) + sign * float(offset)


def _kpi_room_snapshot(
    coordinator: HeatingAssistantCoordinator, room_name: str,
) -> RoomSnapshot:
    """Build a :class:`~.kpi.RoomSnapshot` from live coordinator state."""
    room = coordinator.model.rooms.get(room_name)
    setpoint = float(room.setpoint) if room is not None else None
    return RoomSnapshot(
        slug=slugify(room_name),
        room_active=coordinator.is_room_enabled(room_name),
        temperature_filtered=coordinator.filtered_temperatures.get(room_name),
        temperature_measured=coordinator.measured_temperatures.get(room_name),
        constraint_lower=_constraint_bound(coordinator, room_name, -1.0),
        constraint_upper=_constraint_bound(coordinator, room_name, 1.0),
        setpoint=setpoint,
    )


def _kpi_room_snapshots(
    coordinator: HeatingAssistantCoordinator,
) -> list[RoomSnapshot]:
    """Per-room KPI inputs for all model rooms."""
    return [
        _kpi_room_snapshot(coordinator, name)
        for name in coordinator.model.room_names
    ]


def _build_horizon_forecast(
    coordinator: HeatingAssistantCoordinator,
    room_name: str,
    field: str,
    value: Any,
    step_values: "Optional[np.ndarray]" = None,
) -> Dict[str, Any]:
    """Build a per-step ``forecast`` attribute that spans the MPC horizon.

    Produces ``horizon + 1`` entries (bridge at ``now`` + one per step)
    so dashboard ``data_generator`` blocks can plot the value as a line
    in the forecast region.  When ``value`` returns ``None`` we still emit
    the entries so the chart renders a continuous trace — these scalars
    (setpoint, constraint bounds) are not MPC outputs, so they remain
    valid even on solver failure.

    When ``step_values`` is provided (a per-step array of shape ``(N,)``
    from the schedule-projected control trajectory) it is used instead of
    repeating the scalar ``value`` result, so the dashboard shows a
    time-varying comfort corridor that reflects scheduled setpoint /
    comfort-offset changes over the horizon.
    """
    horizon = getattr(coordinator, "_horizon", None)
    dt = getattr(coordinator, "dt", None)
    if horizon is None or dt is None:
        return {
            "forecast": [],
            "horizon_steps": 0,
            "step_seconds": None,
        }

    now = getattr(coordinator, "now_utc", None) or datetime.now(tz=timezone.utc)
    current = value(coordinator, room_name)
    forecast: List[Dict[str, Any]] = []
    # Bridge at "now" (k=0) plus one entry per OCP step (k=1…N), so the
    # line spans the same window as the temperature_forecast trace.
    # step_values[k] corresponds to the OCP step starting at now + k*dt,
    # matching the indexing used by _compute_control_trajectory.
    n_steps = len(step_values) if step_values is not None else 0
    for k in range(int(horizon) + 1):
        step_time = now + timedelta(seconds=float(dt) * k)
        entry: Dict[str, Any] = {"time": step_time.isoformat()}
        if step_values is not None and n_steps > 0:
            idx = min(k, n_steps - 1)
            entry[field] = round(float(step_values[idx]), 2)
        elif current is not None:
            entry[field] = round(current, 2)
        forecast.append(entry)

    return {
        "forecast": forecast,
        "horizon_steps": int(horizon),
        "step_seconds": float(dt),
    }


# ---------------------------------------------------------------------------
# Heating power sensor
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Outdoor temperature forecast sensor (system-wide)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Heat loss sensor (per room)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Heating plan sensor (per room)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Prediction error sensor (per room) - for model fit visualization
# ---------------------------------------------------------------------------

class PredictionErrorSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the current prediction error (residual) for a room.

    The state is the most recent prediction error [°C]:
        error = predicted_temp - measured_temp

    Positive error = model over-predicts (predicts warmer than actual)
    Negative error = model under-predicts (predicts colder than actual)

    Historical errors over the MPC horizon are exposed as attributes for
    visualization of model fit quality.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:chart-bell-curve"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Prediction Error"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_prediction_error"

    @property
    def native_value(self) -> Optional[float]:
        """Return the most recent aligned prediction error [°C].

        Uses the ``y_pred`` field which is the prediction made at the previous
        cycle *for* the current cycle — so y_pred and y refer to the same
        timestep and their difference is a genuine forecast error.
        """
        room_idx = self._coordinator.model.room_names.index(self._room_name)
        # Walk backwards to find the latest record with a valid aligned y_pred
        for record in reversed(list(self._coordinator.history_buffer)):
            y_pred = record.get("y_pred")
            y = record.get("y", [])
            if y_pred is None:
                continue
            if room_idx < len(y) and room_idx < len(y_pred):
                return round(float(y_pred[room_idx]) - float(y[room_idx]), 3)
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose historical prediction errors and fit metrics."""
        errors = []
        room_idx = self._coordinator.model.room_names.index(self._room_name)

        for record in list(self._coordinator.history_buffer)[-50:]:  # Last 50 samples
            y = record.get("y", [])
            y_pred = record.get("y_pred")  # aligned: prediction made at k-1 for k

            # Skip records without an aligned prediction (first record after start)
            if y_pred is None:
                continue
            if room_idx < len(y) and room_idx < len(y_pred):
                error = y_pred[room_idx] - y[room_idx]
                errors.append(round(error, 3))

        # Compute basic statistics
        if errors:
            import numpy as np
            errors_arr = np.array(errors)
            rmse = float(np.sqrt(np.mean(errors_arr ** 2)))
            mae = float(np.mean(np.abs(errors_arr)))
            bias = float(np.mean(errors_arr))
            max_error = float(np.max(np.abs(errors_arr)))
        else:
            rmse = mae = bias = max_error = 0.0

        return {
            "recent_errors": errors,
            "rmse": round(rmse, 3),
            "mae": round(mae, 3),
            "bias": round(bias, 3),
            "max_error": round(max_error, 3),
            "n_samples": len(errors),
        }


# ---------------------------------------------------------------------------
# Model fit quality sensor (per room)
# ---------------------------------------------------------------------------

class ModelFitQualitySensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor reporting overall model fit quality for a room.

    The state is the R² (coefficient of determination) score [0-1]:
        1.0 = perfect fit
        0.0 = no better than mean prediction
        < 0 = worse than mean prediction

    Additional fit metrics (RMSE, MAE, autocorrelation) are exposed as
    attributes for detailed diagnostics.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:poll"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Model Fit Quality"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_model_fit_quality"

    @property
    def native_value(self) -> Optional[float]:
        """Return R² score as the main quality metric."""
        from .model_diagnostics import compute_model_fit_metrics

        # Extract predictions and measurements from history
        room_idx = self._coordinator.model.room_names.index(self._room_name)
        predictions = []
        measurements = []

        for record in self._coordinator.history_buffer:
            y = record.get("y", [])
            y_pred = record.get("y_pred")  # may be None for the first record

            # Skip records where no aligned prediction was stored yet
            if y_pred is None:
                continue
            if room_idx < len(y) and room_idx < len(y_pred):
                predictions.append(y_pred[room_idx])
                measurements.append(y[room_idx])

        if len(predictions) < 2:
            return None

        try:
            metrics = compute_model_fit_metrics(predictions, measurements, self._room_name)
            return round(metrics.r_squared, 4)
        except Exception as exc:
            _LOGGER.warning("Failed to compute model fit quality for %s: %s", self._room_name, exc)
            return None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose detailed fit metrics."""
        from .model_diagnostics import compute_model_fit_metrics

        # Extract predictions and measurements from history
        room_idx = self._coordinator.model.room_names.index(self._room_name)
        predictions = []
        measurements = []

        for record in self._coordinator.history_buffer:
            y = record.get("y", [])
            y_pred = record.get("y_pred")  # may be None for the first record

            # Skip records where no aligned prediction was stored yet
            if y_pred is None:
                continue
            if room_idx < len(y) and room_idx < len(y_pred):
                predictions.append(y_pred[room_idx])
                measurements.append(y[room_idx])

        if len(predictions) < 2:
            return {
                "error": "Insufficient data",
                "n_samples": len(predictions),
            }

        try:
            metrics = compute_model_fit_metrics(predictions, measurements, self._room_name)
            return {
                "r_squared": round(metrics.r_squared, 4),
                "rmse": round(metrics.rmse, 3),
                "mae": round(metrics.mae, 3),
                "bias": round(metrics.bias, 3),
                "max_error": round(metrics.max_error, 2),
                "residual_std": round(metrics.residual_std, 3),
                "residual_autocorr_lag1": (
                    round(metrics.residual_autocorr_lag1, 3)
                    if metrics.residual_autocorr_lag1 is not None
                    else None
                ),
                "n_samples": metrics.n_samples,
            }
        except Exception as exc:
            _LOGGER.warning("Failed to compute fit metrics for %s: %s", self._room_name, exc)
            return {
                "error": str(exc),
                "n_samples": len(predictions),
            }


# ---------------------------------------------------------------------------
# Parameter confidence sensor (per room)
# ---------------------------------------------------------------------------


def _closed_loop_fit_for_room(
    coordinator: HeatingAssistantCoordinator,
    room_name: str,
) -> tuple[Optional[float], Optional[float], Optional[int]]:
    """Return (r_squared, rmse, n_samples) from the history buffer, if available."""
    from .model_diagnostics import compute_model_fit_metrics

    try:
        room_idx = coordinator.model.room_names.index(room_name)
    except ValueError:
        return None, None, None

    predictions: list[float] = []
    measurements: list[float] = []
    history = getattr(coordinator, "history_buffer", None) or []
    for record in history:
        y = record.get("y", [])
        y_pred = record.get("y_pred")
        if y_pred is None:
            continue
        if room_idx < len(y) and room_idx < len(y_pred):
            predictions.append(y_pred[room_idx])
            measurements.append(y[room_idx])

    if len(predictions) < 2:
        return None, None, len(predictions)

    try:
        metrics = compute_model_fit_metrics(predictions, measurements, room_name)
        return metrics.r_squared, metrics.rmse, metrics.n_samples
    except Exception:
        return None, None, len(predictions)


def _room_estimation_provenance(
    snapshot: Optional[dict],
    room_name: str,
) -> tuple[bool, Optional[str]]:
    """Return per-room estimation flags from the persisted snapshot."""
    if not snapshot:
        return False, None
    room_snap = snapshot.get("rooms", {}).get(room_name)
    if not isinstance(room_snap, dict):
        return False, None
    if "is_estimated" in room_snap:
        is_estimated = bool(room_snap.get("is_estimated"))
        estimated_at = room_snap.get("estimated_at") if is_estimated else None
        return is_estimated, estimated_at
    # Legacy snapshots listed every room without a provenance flag.
    return False, None


class ParameterConfidenceSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor reporting confidence/validity of thermal parameters for a room.

    The state is a confidence score [0-100]:
        100 = all parameters in valid range
        0 = parameters outside valid range or no data

    Detailed parameter validation warnings are exposed as attributes.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:shield-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Parameter Confidence"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_parameter_confidence"

    @property
    def native_value(self) -> Optional[float]:
        """Return confidence score [0-100]."""
        from .model_diagnostics import validate_parameters

        room = self._coordinator.model.rooms[self._room_name]

        try:
            fit_r2, fit_rmse, _ = _closed_loop_fit_for_room(
                self._coordinator, self._room_name,
            )
            validation = validate_parameters(
                self._room_name,
                room.thermal_mass,
                room.r_external,
                model_r_squared=fit_r2,
                model_rmse=fit_rmse,
            )

            # Compute confidence score
            score = 0.0
            if validation.mass_valid:
                score += 33.3
            if validation.r_external_valid:
                score += 33.3
            if validation.time_constant_valid:
                score += 33.4

            return round(score, 1)
        except Exception as exc:
            _LOGGER.warning("Failed to validate parameters for %s: %s", self._room_name, exc)
            return None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose detailed parameter validation."""
        from .model_diagnostics import (
            build_identification_warnings,
            validate_parameters,
        )

        room = self._coordinator.model.rooms[self._room_name]

        try:
            fit_r2, fit_rmse, n_samples = _closed_loop_fit_for_room(
                self._coordinator, self._room_name,
            )
            validation = validate_parameters(
                self._room_name,
                room.thermal_mass,
                room.r_external,
                model_r_squared=fit_r2,
                model_rmse=fit_rmse,
            )

            snapshot = None
            try:
                snapshot = self._coordinator.estimated_params_snapshot
            except Exception:
                pass
            is_estimated, estimated_at = _room_estimation_provenance(
                snapshot, self._room_name,
            )

            ol_rmse = getattr(self._coordinator, "open_loop_results", {}).get(
                self._room_name, {},
            ).get("rmse")
            card_warnings = build_identification_warnings(
                self._room_name,
                validation,
                model_r_squared=fit_r2,
                model_rmse=fit_rmse,
                open_loop_rmse=ol_rmse,
                n_samples=n_samples,
            )

            return {
                "thermal_mass": validation.thermal_mass,
                "r_external": validation.r_external,
                "internal_gain": round(
                    float(getattr(room, "internal_gain", 0.0)), 2
                ),
                "time_constant_hours": round(validation.time_constant_hours, 2),
                "mass_valid": validation.mass_valid,
                "r_external_valid": validation.r_external_valid,
                "time_constant_valid": validation.time_constant_valid,
                "warnings": validation.warnings,
                "card_warnings": [
                    {"code": w.code, "message": w.message, "severity": w.severity}
                    for w in card_warnings
                ],
                "is_estimated": is_estimated,
                "estimated_at": estimated_at,
            }
        except Exception as exc:
            _LOGGER.warning("Failed to validate parameters for %s: %s", self._room_name, exc)
            return {
                "error": str(exc),
            }


# ---------------------------------------------------------------------------
# Open-loop RMSE sensor (per room) – direct model quality indicator
# ---------------------------------------------------------------------------

class OpenLoopRMSESensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the open-loop prediction RMSE for a room.

    Values come from a continuous open-loop simulation over the history
    buffer (no Kalman correction, no artificial segment restarts).  The
    ``rmse_by_horizon`` attribute reports segmented RMSE at ~4 h / 12 h /
    24 h lookahead horizons.  Together these show how much the thermal
    model drifts from reality, which is the root cause of MPC overshoot.

    Rule of thumb (continuous RMSE):
        < 0.2 °C: excellent – MPC predictions are reliable
        0.2–0.5 °C: acceptable
        > 0.5 °C: likely contributing to overshoot; re-run parameter estimation
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:chart-timeline-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Open Loop RMSE"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_open_loop_rmse"

    @property
    def native_value(self) -> Optional[float]:
        """Return open-loop RMSE [°C] for this room, from coordinator cache."""
        room_data = self._coordinator.open_loop_results.get(self._room_name, {})
        return room_data.get("rmse")

    @property
    def extra_state_attributes(self) -> dict:
        """Expose open-loop simulation data for Apex Charts, from coordinator cache."""
        room_data = self._coordinator.open_loop_results.get(self._room_name, {})
        sim = room_data.get("simulation", [])
        from datetime import datetime, timezone
        formatted_sim = []
        for entry in sim:
            ts = entry.get("time", 0.0)
            dt_iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
            ol_entry: dict = {
                "time": dt_iso,
                "measured": entry.get("measured"),
                "predicted": entry.get("predicted"),
            }
            if entry.get("predicted_wall") is not None:
                ol_entry["predicted_wall"] = entry["predicted_wall"]
            formatted_sim.append(ol_entry)
        attrs: dict = {
            "open_loop_rmse": room_data.get("rmse"),
            "open_loop_mae": room_data.get("mae"),
            # RMSE at ~4 h / 12 h / 24 h open-loop horizons — how the
            # prediction error grows with lookahead, which is what matters
            # for price-driven anticipatory heating.
            "rmse_by_horizon": room_data.get("rmse_by_horizon"),
            "simulation": formatted_sim,
        }
        if "error" in room_data:
            attrs["error"] = room_data["error"]
        return attrs


# ---------------------------------------------------------------------------
# Kalman innovation sensor (per room)
# ---------------------------------------------------------------------------

class KalmanInnovationSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the most recent Kalman filter innovation ν = y − C x̂⁻.

    A well-tuned model/filter should have innovations that are:
        - Zero-mean (no systematic bias)
        - White noise (no autocorrelation)
        - Consistent with the innovation covariance

    Persistent non-zero mean or high autocorrelation indicates that the
    thermal model is missing dynamics (e.g. inter-room coupling, solar,
    or heat source dynamics).
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:waveform"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Kalman Innovation"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_kalman_innovation"

    @property
    def native_value(self) -> Optional[float]:
        """Return the most recent Kalman innovation [°C] for this room."""
        room_idx = self._coordinator.model.room_names.index(self._room_name)
        for record in reversed(list(self._coordinator.history_buffer)):
            innov = record.get("kalman_innovation")
            if innov is not None and room_idx < len(innov):
                return round(float(innov[room_idx]), 4)
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose innovation time series and statistics."""
        from datetime import datetime, timezone
        import numpy as np

        room_idx = self._coordinator.model.room_names.index(self._room_name)
        innovations = []

        for record in self._coordinator.history_buffer:
            innov = record.get("kalman_innovation")
            ts = record.get("timestamp", 0.0)
            if innov is not None and room_idx < len(innov):
                dt_iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
                innovations.append({
                    "time": dt_iso,
                    "value": round(float(innov[room_idx]), 4),
                })

        if not innovations:
            return {"innovations": [], "n_samples": 0}

        vals = np.array([e["value"] for e in innovations])
        mean_v = float(np.mean(vals))
        std_v = float(np.std(vals))

        # Lag-1 autocorrelation
        autocorr_lag1: Optional[float] = None
        if len(vals) >= 4:
            n_v = len(vals)
            c0 = float(np.dot(vals - mean_v, vals - mean_v))
            if c0 > 0:
                c1 = float(np.dot((vals - mean_v)[1:], (vals - mean_v)[:-1]))
                autocorr_lag1 = round(c1 / c0, 4)

        # Consistency: |mean| < 2 * std/sqrt(n) (approximate 95% test)
        n_samples = len(vals)
        is_consistent = abs(mean_v) < 2.0 * (std_v / max(1.0, float(np.sqrt(n_samples))))

        return {
            "innovations": innovations[-100:],  # keep last 100 for attribute size
            "mean": round(mean_v, 4),
            "std": round(std_v, 4),
            "autocorr_lag1": autocorr_lag1,
            "is_consistent": is_consistent,
            "n_samples": n_samples,
        }


# ---------------------------------------------------------------------------
# Residual ACF sensor (per room)
# ---------------------------------------------------------------------------

class ResidualACFSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the lag-1 autocorrelation of 1-step prediction residuals.

    A lag-1 autocorrelation close to 0 indicates that the residuals are
    white noise (good model).  High autocorrelation (> 0.3) means the model
    is systematically missing dynamics — a signal to re-run parameter
    estimation or check the model configuration.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:chart-bar"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – Residual ACF"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_residual_acf"

    @property
    def native_value(self) -> Optional[float]:
        """Return lag-1 residual autocorrelation."""
        try:
            acf_result = self._compute_acf()
            acf = acf_result.get("acf", [])
            return round(acf[1], 4) if len(acf) > 1 else None
        except Exception:
            return None

    def _compute_acf(self) -> dict:
        from .model_diagnostics import compute_autocorrelation_function

        room_idx = self._coordinator.model.room_names.index(self._room_name)
        residuals = []
        for record in self._coordinator.history_buffer:
            y = record.get("y", [])
            y_pred = record.get("y_pred")
            if y_pred is None:
                continue
            if room_idx < len(y) and room_idx < len(y_pred):
                residuals.append(float(y_pred[room_idx]) - float(y[room_idx]))
        return compute_autocorrelation_function(residuals)

    @property
    def extra_state_attributes(self) -> dict:
        """Expose full ACF, confidence bounds, and Ljung-Box statistic."""
        try:
            return self._compute_acf()
        except Exception as exc:
            _LOGGER.debug("ResidualACFSensor error for %s: %s", self._room_name, exc)
            return {"error": str(exc)}



# ---------------------------------------------------------------------------
# Heater scale sensor (per heat source) — estimated power-scale factor
# ---------------------------------------------------------------------------

class HeaterScaleSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor reporting the current power-scale factor for a heat source.

    The state is the scale as a percentage (100 % = nominal rated power).
    A value above 100 % means the estimator found that the source delivers
    more heat than its nominal rating predicts; below 100 % means it
    delivers less (e.g. due to duct losses or a degraded heat pump).

    The factor is 1.0 (= 100 %) when no estimation has been run yet.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:tune-vertical"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        source_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._source_name = source_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {source_name} – Heater Scale"
        self._attr_unique_id = f"{DOMAIN}_{source_name}_heater_scale"

    def _source(self):
        return next(
            (s for s in self._coordinator.heat_sources if s.name == self._source_name),
            None,
        )

    @property
    def native_value(self) -> Optional[float]:
        """Return the power-scale factor as a percentage."""
        src = self._source()
        if src is None:
            return None
        return round(float(getattr(src, "power_scale", 1.0)) * 100.0, 1)

    @property
    def extra_state_attributes(self) -> dict:
        """Expose raw scale factor and estimation provenance."""
        src = self._source()
        snapshot = None
        try:
            snapshot = self._coordinator.estimated_params_snapshot
        except Exception:
            pass
        is_estimated = (
            self._source_name in snapshot.get("sources", {})
            if snapshot else False
        )
        return {
            "source_name": self._source_name,
            "power_scale": round(float(getattr(src, "power_scale", 1.0)), 4) if src else None,
            "max_power": float(src.max_power) if src else None,
            "is_estimated": is_estimated,
            "estimated_at": snapshot.get("estimated_at") if snapshot else None,
        }


# ---------------------------------------------------------------------------
# Log-likelihood slice sensor (per room)
# ---------------------------------------------------------------------------


class LoglikSliceSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Most recent log-likelihood slice computed for a room.

    Populated by the :meth:`HeatingAssistantCoordinator.async_compute_loglik_slice`
    call (triggered by the ``compute_loglik_slice`` service / dashboard
    button). The state is the ISO timestamp of the last successful run –
    set to ``unknown`` until the user requests a slice for the room. The
    attributes carry the full grid so a Plotly card or template sensor can
    visualise the likelihood landscape without recomputing.
    """

    _attr_icon = "mdi:chart-bell-curve"
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
            f"Heating Assistant – {room_name} – Loglik Slice"
        )
        self._attr_unique_id = f"{DOMAIN}_{room_name}_loglik_slice"

    def _slice(self) -> Optional[Dict[str, Any]]:
        slices = getattr(self._coordinator, "_loglik_slices", {}) or {}
        return slices.get(self._room_name)

    @property
    def native_value(self) -> Optional[str]:
        sl = self._slice()
        return None if sl is None else sl.get("computed_at")

    @property
    def extra_state_attributes(self) -> dict:
        sl = self._slice()
        if sl is None:
            return {
                "room": self._room_name,
                "status": "not_computed",
            }
        # Avoid copying the (potentially large) grid until the user opens
        # the entity – it lives in coordinator state so this is a view.
        return dict(sl)


# ---------------------------------------------------------------------------
# Cumulative energy sensor (per heat source)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Estimated parameters status sensor (system-wide)
# ---------------------------------------------------------------------------

class EstimatedParametersStatusSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    System-wide sensor summarising all currently active thermal parameters.

    State
    -----
    * ``"estimated"`` — at least one parameter was set by the ML estimator
      and the snapshot has been persisted to ``entry.data``.
    * ``"default"`` — no estimation has been run yet (or it was reset).

    Attributes
    ----------
    The attributes expose the full parameter set as a machine-readable dict
    that dashboards and automations can consume directly without querying
    individual per-room sensors:

    * ``rooms``       — per-room ``{thermal_mass, r_external, internal_gain, is_estimated}``
    * ``sources``     — per-source ``{power_scale, is_estimated}``
    * ``connections`` — per-pair ``{r_value, is_estimated}``
    * ``estimated_at``        — ISO-8601 timestamp of the last successful run
    * ``log_likelihood``      — log-likelihood value at the optimal solution
    * ``n_rooms_estimated``   — number of rooms whose parameters were estimated
    * ``n_sources_estimated`` — number of sources whose scale was estimated
    """

    _attr_icon = "mdi:database-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – Estimated Parameters Status"
        self._attr_unique_id = f"{DOMAIN}_estimated_parameters_status"

    @property
    def native_value(self) -> str:
        """Return ``"estimated"`` if a persisted snapshot exists, else ``"default"``."""
        snapshot = None
        try:
            snapshot = self._coordinator.estimated_params_snapshot
        except Exception:
            pass
        return "estimated" if snapshot else "default"

    @property
    def extra_state_attributes(self) -> dict:
        """Expose the full parameter set with estimation provenance flags."""
        snapshot = None
        try:
            snapshot = self._coordinator.estimated_params_snapshot
        except Exception:
            pass
        snap = snapshot or {}
        rooms_snap = snap.get("rooms", {})
        sources_snap = snap.get("sources", {})
        connections_snap = snap.get("connections", {})

        rooms_data: Dict[str, Any] = {}
        for name, room in self._coordinator.model.rooms.items():
            room_snap = rooms_snap.get(name, {}) if isinstance(rooms_snap.get(name), dict) else {}
            is_estimated = bool(room_snap.get("is_estimated", False)) if room_snap else False
            rooms_data[name] = {
                "thermal_mass": round(room.thermal_mass, 0),
                "r_external": round(room.r_external, 6),
                "internal_gain": round(
                    float(getattr(room, "internal_gain", 0.0)), 2
                ),
                "is_estimated": is_estimated,
            }

        sources_data: Dict[str, Any] = {}
        for src in self._coordinator.heat_sources:
            sources_data[src.name] = {
                "power_scale": round(float(getattr(src, "power_scale", 1.0)), 4),
                "is_estimated": src.name in sources_snap,
            }

        connections_data: Dict[str, Any] = {
            key: {"r_value": r_val, "is_estimated": True}
            for key, r_val in connections_snap.items()
        }

        history = list(
            getattr(self._coordinator, "_estimation_history", []) or []
        )
        return {
            "rooms": rooms_data,
            "sources": sources_data,
            "connections": connections_data,
            "estimated_at": snap.get("estimated_at"),
            "log_likelihood": snap.get("log_likelihood"),
            "n_rooms_estimated": len(rooms_snap),
            "n_sources_estimated": len(sources_snap),
            "estimation_history": history,
        }


# ---------------------------------------------------------------------------
# MPC performance sensor (system-wide)
# ---------------------------------------------------------------------------
class MPCPerformanceSensor(CoordinatorEntity, SensorEntity):
    """
    System-wide sensor reporting MPC solver performance statistics.

    The state is the most recent OCP wall-clock solve duration [s].  Detailed
    statistics (solve times, tracking errors) are exposed as state attributes.

    Some callers may provide legacy ``datetime.timedelta`` solve-time values.
    Those are normalized to raw seconds so Home Assistant always receives a
    plain numeric state.
    """

    _attr_state_class = None
    _attr_native_unit_of_measurement = "s"
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – MPC Performance"
        self._attr_unique_id = f"{DOMAIN}_mpc_performance"

    @staticmethod
    def _solve_time_seconds(value: timedelta | float | int | None) -> Optional[float]:
        """Convert a solve-time value in seconds to ``float`` or return ``None``."""
        if value is None:
            return None
        if isinstance(value, timedelta):
            return float(value.total_seconds())
        try:
            return float(value)
        except (TypeError, ValueError):
            _LOGGER.debug("Ignoring non-numeric MPC solve time value: %r", value)
            return None

    @property
    def native_value(self) -> Optional[float]:
        """Return the most recent OCP solve duration in seconds."""
        return self._solve_time_seconds(self._coordinator.controller.last_solve_time)

    @property
    def available(self) -> bool:
        """Keep the latest cached solver stats visible across update failures."""
        return getattr(self._coordinator, "controller", None) is not None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose rolling solve-time statistics and recent history."""
        import numpy as np

        controller = self._coordinator.controller
        solve_times = []
        for sample in controller.solve_times:
            seconds = self._solve_time_seconds(sample)
            if seconds is not None:
                solve_times.append(seconds)

        last_t = self._solve_time_seconds(controller.last_solve_time)
        mean_t = self._solve_time_seconds(controller.mean_solve_time)
        max_t = self._solve_time_seconds(controller.max_solve_time)
        n = controller.n_solves

        attrs: Dict[str, Any] = {
            "total_computes": controller.total_computes,
            "last_solve_time_s": round(last_t, 4) if last_t is not None else None,
            "mean_solve_time_s": round(mean_t, 4) if mean_t is not None else None,
            "max_solve_time_s": round(max_t, 4) if max_t is not None else None,
            "n_solves": n,
            "horizon": self._coordinator.controller.horizon,
            "dt_s": self._coordinator.dt,
        }

        # Explicit MPC schedule timestamps so the dashboard countdown is anchored
        # to the actual internal solve cadence rather than the entity's HA
        # last_updated (which the fast UI refresh also bumps between solves).
        last_run_ts = getattr(self._coordinator, "_last_mpc_run_ts", None)
        attrs["last_run_ts"] = last_run_ts
        attrs["next_run_ts"] = (
            last_run_ts + self._coordinator.dt if last_run_ts else None
        )

        # Tracking error per room (|T − setpoint|; mean excludes inactive rooms)
        room_names = self._coordinator.model.room_names
        room_snapshots = _kpi_room_snapshots(self._coordinator)
        tracking_errors: Dict[str, float] = {}
        for name, snap in zip(room_names, room_snapshots):
            temp = room_temperature(snap)
            setpoint = snap.setpoint
            if temp is not None and setpoint is not None:
                tracking_errors[name] = round(abs(temp - setpoint), 3)
        attrs["current_tracking_errors"] = tracking_errors
        mean_err = mean_tracking_error_c(room_snapshots)
        attrs["mean_tracking_error"] = (
            round(mean_err, 2) if mean_err is not None else None
        )
        active_errors = [
            tracking_errors[name]
            for name, snap in zip(room_names, room_snapshots)
            if snap.room_active and name in tracking_errors
        ]
        attrs["max_tracking_error"] = (
            round(max(active_errors), 2) if active_errors else None
        )

        # Terminal-weight in effect (for reference)
        attrs["terminal_weight"] = (
            controller.terminal_weight
            if hasattr(controller, "terminal_weight")
            else None
        )

        # Rolling solve time history (last 50 samples) for sparkline charts
        attrs["recent_solve_times_s"] = [round(t, 4) for t in solve_times[-50:]]

        return attrs


# ---------------------------------------------------------------------------
# Weather forecast status sensor (system-wide, diagnostic)
# ---------------------------------------------------------------------------

class WeatherForecastStatusSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Diagnostic sensor reporting the health of the weather-forecast fetch.

    The state is one of:

    * ``"ok"``        — the most recent fetch succeeded
    * ``"failing"``   — at least one consecutive fetch has failed
    * ``"disabled"``  — no weather entity is configured

    Attributes expose the last error message, the timestamps of the most
    recent success and failure, and the consecutive-failure counter so users
    can diagnose persistent breakages without having to grep the HA log.
    """

    _attr_icon = "mdi:weather-cloudy-alert"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – Weather Forecast Status"
        self._attr_unique_id = f"{DOMAIN}_weather_forecast_status"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        if not getattr(self._coordinator, "_weather_entity", None):
            return "disabled"
        if getattr(self._coordinator, "weather_consecutive_failures", 0) > 0:
            return "failing"
        return "ok"

    @property
    def extra_state_attributes(self) -> dict:
        last_err_at = getattr(self._coordinator, "weather_last_error_at", None)
        last_ok_at = getattr(self._coordinator, "weather_last_success_at", None)
        return {
            "weather_entity": getattr(self._coordinator, "_weather_entity", None),
            "last_error": getattr(self._coordinator, "weather_last_error", None),
            "last_error_at": last_err_at.isoformat() if last_err_at else None,
            "last_success_at": last_ok_at.isoformat() if last_ok_at else None,
            "consecutive_failures": getattr(
                self._coordinator, "weather_consecutive_failures", 0
            ),
        }


class SolarRadiationStatusSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Diagnostic sensor reporting the health of the solar-radiation forecast.

    The state is one of:

    * ``"ok"``        — the most recent read succeeded and drives the gains
    * ``"failing"``   — at least one consecutive read has failed
    * ``"disabled"``  — no solar-radiation entity is configured

    Attributes expose which solar source is active (the irradiance forecast vs
    the analytical clear-sky model), the current GHI and its horizon series
    [W/m²] (kept here, off the history buffer), and the usual error / timestamp
    diagnostics.
    """

    _attr_icon = "mdi:weather-sunny"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant – Solar Radiation Forecast Status"
        self._attr_unique_id = f"{DOMAIN}_solar_radiation_status"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        if not getattr(self._coordinator, "_solar_radiation_entity", None):
            return "disabled"
        if getattr(self._coordinator, "solar_fc_consecutive_failures", 0) > 0:
            return "failing"
        return "ok"

    @property
    def extra_state_attributes(self) -> dict:
        last_err_at = getattr(self._coordinator, "solar_fc_last_error_at", None)
        last_ok_at = getattr(self._coordinator, "solar_fc_last_success_at", None)
        return {
            "solar_radiation_entity": getattr(
                self._coordinator, "_solar_radiation_entity", None
            ),
            "active_source": getattr(self._coordinator, "solar_source", "analytical"),
            "ghi_now": getattr(self._coordinator, "ghi_now", None),
            # Effective GHI used by the overview's solar-irradiance KPI: the
            # measured/forecast value when present, else the modeled clear-sky
            # value. Always populated when a site location is known.
            "ghi_now_effective": getattr(
                self._coordinator, "ghi_now_effective", None
            ),
            "ghi_forecast": list(
                getattr(self._coordinator, "ghi_forecast", []) or []
            ),
            "last_error": getattr(self._coordinator, "solar_fc_last_error", None),
            "last_error_at": last_err_at.isoformat() if last_err_at else None,
            "last_success_at": last_ok_at.isoformat() if last_ok_at else None,
            "consecutive_failures": getattr(
                self._coordinator, "solar_fc_consecutive_failures", 0
            ),
        }


# ---------------------------------------------------------------------------
# System identification simulation sensor (per room)
# ---------------------------------------------------------------------------


class SysIdSimulationSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """
    Sensor exposing the most recent system-identification EKF reconstruction.

    State value: one-step-ahead RMSE [°C] (``None`` until the first run).

    Attributes:
        ``simulation``   – list of {time (ISO-8601), measured, predicted,
                                     cov_upper, cov_lower} for Apex Charts
        ``thermal_mass`` – J/K used for this run
        ``r_external``   – K/W used for this run
        ``sigma_w``      – process noise std used for this run
        ``sigma_v``       – measurement noise std used for this run
        ``rmse``         – same as state value [°C]
        ``mae``          – mean absolute error [°C]
        ``horizon_hours``– reconstructed horizon in hours
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:chart-scatter-plot"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HeatingAssistantCoordinator,
        room_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._room_name = room_name
        self._coordinator = coordinator
        self._attr_name = f"Heating Assistant – {room_name} – SysID Simulation"
        self._attr_unique_id = f"{DOMAIN}_{room_name}_sysid_simulation"

    @property
    def native_value(self) -> Optional[float]:
        room_data = getattr(self._coordinator, "sysid_results", {}).get(
            self._room_name, {}
        )
        return room_data.get("rmse")

    @property
    def extra_state_attributes(self) -> dict:
        room_data = getattr(self._coordinator, "sysid_results", {}).get(
            self._room_name, {}
        )
        sim_raw = room_data.get("simulation", [])
        formatted: list = []
        for entry in sim_raw:
            ts = entry.get("time", 0.0)
            dt_iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
            sim_entry: dict = {
                "time": dt_iso,
                "measured": entry.get("measured"),
                "predicted": entry.get("predicted"),
                "cov_upper": entry.get("cov_upper"),
                "cov_lower": entry.get("cov_lower"),
            }
            if entry.get("predicted_wall") is not None:
                sim_entry["predicted_wall"] = entry["predicted_wall"]
                sim_entry["wall_cov_upper"] = entry.get("wall_cov_upper")
                sim_entry["wall_cov_lower"] = entry.get("wall_cov_lower")
            formatted.append(sim_entry)
        dt_val = self._coordinator.dt
        horizon_steps = room_data.get("horizon_steps", 0)
        horizon_hours = round(horizon_steps * dt_val / 3600.0, 2) if horizon_steps else None
        return {
            "simulation": formatted,
            "thermal_mass": room_data.get("thermal_mass"),
            "r_external": room_data.get("r_external"),
            # Full identified parameter set surfaced after an ML dry-run so the
            # room-level identification page can review/apply every parameter.
            "internal_gain": room_data.get("internal_gain"),
            "solar_scale": room_data.get("solar_scale"),
            "c_air_fraction": room_data.get("c_air_fraction"),
            "r_aw_fraction": room_data.get("r_aw_fraction"),
            "t_wall_initial": room_data.get("t_wall_initial"),
            "estimated_inter_room_r": room_data.get("estimated_inter_room_r"),
            "heater_scales": room_data.get("heater_scales"),
            "sigma_w": room_data.get("sigma_w"),
            "sigma_v": room_data.get("sigma_v"),
            "rmse": room_data.get("rmse"),
            "mae": room_data.get("mae"),
            "horizon_hours": horizon_hours,
            "window_start": room_data.get("window_start"),
            "window_end": room_data.get("window_end"),
        }


# ---------------------------------------------------------------------------
# Controller / estimation configuration — system-wide
# ---------------------------------------------------------------------------


class ControllerConfigSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Exposes current controller tuning and estimation parameters as attributes.

    The frontend reads this entity reactively via state subscription to populate
    the Controller Tuning and System Identification pages.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:tune-vertical"

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant \u2013 Controller Config"
        self._attr_unique_id = f"{DOMAIN}_controller_config"

    @property
    def available(self) -> bool:
        # Tuning parameters live on the coordinator object from __init__ and
        # are not produced by the periodic update cycle.  Always return True
        # so that hass.states always carries the tuning attributes even when
        # an MPC solve or data-fetch step fails and last_update_success is False.
        return True

    @property
    def native_value(self) -> str:
        return "active"

    @property
    def extra_state_attributes(self) -> dict:
        c = self._coordinator

        # Core tuning parameters — set in coordinator __init__, always accessible.
        attrs: dict = {
            "comfort_offset": next(iter(c._room_comfort_offset.values()), 2.0),
            "tracking_weight": c._tracking_weight,
            "energy_weight": c._energy_weight,
            "energy_price_weight": c._energy_price_weight,
            "smoothing_weight": c._smoothing_weight,
            "soft_constraint_weight": c._soft_constraint_weight,
            "soft_constraint_linear_weight": c._soft_constraint_linear_weight,
            "terminal_weight": c._terminal_weight,
            "horizon": c._horizon,
            # Always expose as a plain number of seconds.  ``_update_interval``
            # can end up as a ``datetime.timedelta`` (e.g. when the config entry
            # stored it that way), and a single non-JSON-serialisable attribute
            # makes Home Assistant's WebSocket API fail to serialise the *whole*
            # state payload — which silently starves the dashboard of every
            # heating_assistant entity.  ``coordinator.dt`` is the coerced value.
            "update_interval": c.dt,
            "sigma_w": c._sigma_w,
            "sigma_v": c._sigma_v,
            "sigma_b": c._sigma_b,
            "identification_horizon_hours": float(
                getattr(c, "_identification_horizon_hours", 6.0)
            ),
            "window_open_debounce": c._window_open_debounce,
            "window_open_close_settle": c._window_open_close_settle,
            "window_open_q_inflation": c._window_open_q_inflation,
            # Heater power-scale factors — identified vs applied
            "identified_heater_scales": dict(
                getattr(c, "_last_identified_heater_scales", {})
            ),
            "current_heater_scales": {
                src.name: {
                    "power_scale": round(float(getattr(src, "power_scale", 1.0)), 4),
                    "room": getattr(src, "room", None),
                    "room_slug": slugify(getattr(src, "room", "") or ""),
                }
                for src in getattr(c, "heat_sources", [])
            },
        }

        # Schedule, setpoint and room data — requires model to be initialised.
        try:
            schedules: dict = {}
            for room_name, room_schedule in c._room_schedule.items():
                if room_schedule and not room_schedule.is_empty:
                    schedules[slugify(room_name)] = {
                        "enabled": c._schedule_enabled.get(room_name, True),
                        "periods": [
                            {
                                "name": p.name,
                                "start": p.start.strftime("%H:%M"),
                                "end": p.end.strftime("%H:%M"),
                                "mode": p.mode,
                                "setpoint": p.setpoint,
                                "frost_protection": p.frost_protection,
                                "days": sorted(p.days),
                                "comfort_offset": p.comfort_offset,
                                "tracking_weight": p.tracking_weight,
                                "energy_weight": p.energy_weight,
                            }
                            for p in room_schedule.periods
                        ],
                    }
            room_setpoints: dict = {}
            room_comfort_offsets: dict = {}
            # ``room_enabled`` reflects the user's manual on/off toggle, while
            # ``room_active`` is the *effective* state (user toggle AND no active
            # off-schedule).  The dashboard uses ``room_active`` to drive the
            # clear "OFF" state on the climate cards and ``room_enabled`` to
            # reflect the user's intent on the power toggle.
            room_enabled: dict = {}
            room_active: dict = {}
            for rn in c.model.room_names:
                room_setpoints[slugify(rn)] = c.model.rooms[rn].setpoint
                room_comfort_offsets[slugify(rn)] = c._room_comfort_offset.get(rn, 2.0)
                room_enabled[slugify(rn)] = bool(c._room_enabled.get(rn, True))
                room_active[slugify(rn)] = bool(c.is_room_enabled(rn))
            attrs["room_schedules"] = schedules
            attrs["room_setpoints"] = room_setpoints
            attrs["room_comfort_offsets"] = room_comfort_offsets
            attrs["room_enabled"] = room_enabled
            attrs["room_active"] = room_active
        except Exception:
            pass

        # Parameter estimation history from the persisted snapshot.
        try:
            snap = c.estimated_params_snapshot
            if snap and isinstance(snap, dict) and "history" in snap:
                attrs["parameter_history"] = snap["history"]
        except Exception:
            pass

        return attrs
