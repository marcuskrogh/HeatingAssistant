"""
Heat-source models for the Heating Assistant integration.

Supported types
---------------
* ElectricHeater      – resistive/infrared electric heater; linear, COP = efficiency ≈ 1
* HeatPump            – air-source heat pump with outdoor-temperature-dependent COP
* GenericThermostat   – catch-all for any heat-only device with a temperature setpoint
* GasHeater           – gas-fired furnace or boiler; linear, draws no electricity
* HydronicRadiator     – district heating / hot-water radiator; linear, draws no electricity
* OilRadiator and ElectricFloorHeating are not separate classes — they instantiate
  ElectricHeater with typology-appropriate emitter time constants set in const.py.
* HydronicFloorHeating is not a separate class — it instantiates HydronicRadiator
  with a longer emitter time constant (3600 s) to reflect concrete-slab thermal inertia.
"""

from __future__ import annotations

from .base import HeatSource, _SOFT_CEIL_K, _T_SUPPLY_K, _soft_ceiling
from .electric import ElectricHeater
from .gas import GasHeater
from .gshp import GroundSourceHeatPump
from .heat_pump import HeatPump, _cop_at_temp
from .hydronic import HydronicRadiator
from .pellet import PelletStove
from .storage import ElectricStorageHeater
from .thermostat import GenericThermostat

__all__ = [
    "ElectricHeater",
    "ElectricStorageHeater",
    "GasHeater",
    "GenericThermostat",
    "GroundSourceHeatPump",
    "HeatPump",
    "HeatSource",
    "HydronicRadiator",
    "PelletStove",
    "_SOFT_CEIL_K",
    "_T_SUPPLY_K",
    "_cop_at_temp",
    "_soft_ceiling",
]
