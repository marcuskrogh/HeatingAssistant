"""Heating Assistant sensor platform."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any, List, Type

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import DOMAIN
from ..coordinator import HeatingAssistantCoordinator
from ..heat_sources import HeatPump
from .base import _LiveValueSensorMixin, _kpi_room_snapshot
from .config import ControllerConfigSensor
from .diagnostics import (
    EstimatedParametersStatusSensor,
    HeaterScaleSensor,
    KalmanInnovationSensor,
    LoglikSliceSensor,
    MPCPerformanceSensor,
    ModelFitQualitySensor,
    OpenLoopRMSESensor,
    ParameterConfidenceSensor,
    PredictionErrorSensor,
    ResidualACFSensor,
    SolarRadiationStatusSensor,
    SysIdSimulationSensor,
    WeatherForecastStatusSensor,
)
from .forecasts import (
    ElectricityPriceForecastSensor,
    ElectricityPriceSensor,
    HeatingPowerForecastSensor,
    OutdoorTemperatureForecastSensor,
    SolarGainForecastSensor,
    TemperatureForecastSensor,
)
from .room_live import (
    ConstraintLowerSensor,
    ConstraintUpperSensor,
    ControlActionSensor,
    EnergyBalanceSensor,
    HeatLossSensor,
    HeatPumpCOPSensor,
    HeatingEnergyTotalSensor,
    HeatingPowerMeasuredSensor,
    InternalGainEstimatedSensor,
    OutdoorTemperatureMeasuredSensor,
    SetpointSensor,
    SolarGainMeasuredSensor,
    SystemEfficiencySensor,
    TemperatureFilteredSensor,
    TemperatureMeasuredSensor,
    TemperatureOffsetSensor,
    WallTemperatureSensor,
    WindowStateSensor,
)

_LOGGER = logging.getLogger(__name__)

# Type alias: (coordinator, *args) -> SensorEntity
_EntityFactory = Callable[..., SensorEntity]

# Declarative entity registry — each entry is (factory, arg_builder).
# arg_builder receives (coordinator) and returns positional args after coordinator.
PER_ROOM_SENSORS: Sequence[tuple[Type[SensorEntity], Callable[[str], tuple[Any, ...]]]] = (
    (TemperatureMeasuredSensor, lambda room: (room,)),
    (TemperatureFilteredSensor, lambda room: (room,)),
    (WallTemperatureSensor, lambda room: (room,)),
    (TemperatureOffsetSensor, lambda room: (room,)),
    (InternalGainEstimatedSensor, lambda room: (room,)),
    (TemperatureForecastSensor, lambda room: (room,)),
    (SetpointSensor, lambda room: (room,)),
    (WindowStateSensor, lambda room: (room,)),
    (ConstraintUpperSensor, lambda room: (room,)),
    (ConstraintLowerSensor, lambda room: (room,)),
    (HeatingPowerMeasuredSensor, lambda room: (room,)),
    (HeatingPowerForecastSensor, lambda room: (room,)),
    (SolarGainMeasuredSensor, lambda room: (room,)),
    (SolarGainForecastSensor, lambda room: (room,)),
    (HeatLossSensor, lambda room: (room,)),
    (EnergyBalanceSensor, lambda room: (room,)),
    (PredictionErrorSensor, lambda room: (room,)),
    (ModelFitQualitySensor, lambda room: (room,)),
    (ParameterConfidenceSensor, lambda room: (room,)),
    (OpenLoopRMSESensor, lambda room: (room,)),
    (KalmanInnovationSensor, lambda room: (room,)),
    (ResidualACFSensor, lambda room: (room,)),
    (LoglikSliceSensor, lambda room: (room,)),
    (SysIdSimulationSensor, lambda room: (room,)),
)

PER_SOURCE_SENSORS: Sequence[tuple[Type[SensorEntity], Callable[[str], tuple[Any, ...]]]] = (
    (ControlActionSensor, lambda src: (src,)),
    (HeaterScaleSensor, lambda src: (src,)),
    (HeatingEnergyTotalSensor, lambda src: (src,)),
)

SYSTEM_SENSORS: Sequence[Type[SensorEntity]] = (
    OutdoorTemperatureMeasuredSensor,
    OutdoorTemperatureForecastSensor,
    SystemEfficiencySensor,
    EstimatedParametersStatusSensor,
    MPCPerformanceSensor,
    WeatherForecastStatusSensor,
    SolarRadiationStatusSensor,
    ControllerConfigSensor,
)

CONDITIONAL_SENSORS: Sequence[
    tuple[Callable[[HeatingAssistantCoordinator], bool], Sequence[Type[SensorEntity]]]
] = (
    (
        lambda c: bool(getattr(c, "_price_entity", None)),
        (ElectricityPriceSensor, ElectricityPriceForecastSensor),
    ),
)


def _preload_model_diagnostics() -> None:
    from .. import model_diagnostics  # noqa: F401 – triggers scipy import in executor thread


def _build_entities(coordinator: HeatingAssistantCoordinator) -> List[SensorEntity]:
    """Instantiate all sensor entities from the declarative registry."""
    entities: List[SensorEntity] = []

    for room_name in coordinator.model.room_names:
        for cls, arg_builder in PER_ROOM_SENSORS:
            entities.append(cls(coordinator, *arg_builder(room_name)))

    for src in coordinator.heat_sources:
        for cls, arg_builder in PER_SOURCE_SENSORS:
            entities.append(cls(coordinator, *arg_builder(src.name)))
        if isinstance(src, HeatPump):
            entities.append(HeatPumpCOPSensor(coordinator, src.name))

    for cls in SYSTEM_SENSORS:
        entities.append(cls(coordinator))

    for predicate, sensor_classes in CONDITIONAL_SENSORS:
        if predicate(coordinator):
            for cls in sensor_classes:
                entities.append(cls(coordinator))

    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Heating Assistant sensor entities from a config entry."""
    coordinator: HeatingAssistantCoordinator = hass.data[DOMAIN][entry.entry_id]

    await hass.async_add_executor_job(_preload_model_diagnostics)

    async_add_entities(_build_entities(coordinator))


__all__ = [
    "async_setup_entry",
    "_LiveValueSensorMixin",
    "_kpi_room_snapshot",
    "ConstraintLowerSensor",
    "ConstraintUpperSensor",
    "ControlActionSensor",
    "ControllerConfigSensor",
    "ElectricityPriceForecastSensor",
    "ElectricityPriceSensor",
    "EnergyBalanceSensor",
    "EstimatedParametersStatusSensor",
    "HeatLossSensor",
    "HeatPumpCOPSensor",
    "HeaterScaleSensor",
    "HeatingEnergyTotalSensor",
    "HeatingPowerForecastSensor",
    "HeatingPowerMeasuredSensor",
    "InternalGainEstimatedSensor",
    "KalmanInnovationSensor",
    "LoglikSliceSensor",
    "MPCPerformanceSensor",
    "ModelFitQualitySensor",
    "OpenLoopRMSESensor",
    "OutdoorTemperatureForecastSensor",
    "OutdoorTemperatureMeasuredSensor",
    "ParameterConfidenceSensor",
    "PredictionErrorSensor",
    "ResidualACFSensor",
    "SetpointSensor",
    "SolarGainForecastSensor",
    "SolarGainMeasuredSensor",
    "SolarRadiationStatusSensor",
    "SysIdSimulationSensor",
    "SystemEfficiencySensor",
    "TemperatureFilteredSensor",
    "TemperatureForecastSensor",
    "TemperatureMeasuredSensor",
    "TemperatureOffsetSensor",
    "WallTemperatureSensor",
    "WeatherForecastStatusSensor",
    "WindowStateSensor",
]
