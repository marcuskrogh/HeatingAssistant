"""Generic thermostat heat source."""

from __future__ import annotations

from typing import Optional

from .base import HeatSource

class GenericThermostat(HeatSource):
    """
    Catch-all integration for any heat-only device that accepts a temperature
    setpoint but doesn't fit a more specific type (e.g. unknown brand panel
    heater, fan heater with climate entity, underfloor thermostat without a
    known power rating breakdown).

    Physics: linear, Q = max_power × power_scale × u (no explicit efficiency
    parameter — the identified power_scale absorbs any deviation from the
    nominal rating).  Set ``max_power`` to the best available estimate of the
    device's rated thermal output; the parameter estimator will refine it.

    ``elec_per_unit_heat`` is set equal to ``max_power × power_scale``,
    matching a resistive-electric assumption.  If the device is gas-fired,
    use ``gas_heater`` instead.
    """

    def __init__(
        self,
        name: str,
        room: str,
        max_power: float,
        max_temp_offset: float = 5.0,
        heater_entity: Optional[str] = None,
        power_scale: float = 1.0,
        emitter_time_constant: float = 0.0,
    ) -> None:
        super().__init__(name, room, max_power, heater_entity, power_scale, emitter_time_constant)
        if max_temp_offset < 0.0:
            raise ValueError(f"max_temp_offset must be >= 0; got {max_temp_offset}")
        self.max_temp_offset = max_temp_offset
        self._gain: float = max_power * self._power_scale

    def _recompute_power_scaled_gains(self) -> None:
        self._gain = self.max_power * self._power_scale

    def thermal_power(self, setpoint_fraction: float, outdoor_temp: float = 0.0) -> float:
        return self._gain * setpoint_fraction

    def rated_heating_capacity(self, outdoor_temp: float = 0.0) -> float:
        return self.max_power

    @property
    def elec_per_unit_heat(self) -> float:
        return self.max_power

    def target_temperature(self, setpoint_fraction: float, internal_temp: float) -> float:
        setpoint_fraction = max(0.0, min(1.0, setpoint_fraction))
        return internal_temp + setpoint_fraction * self.max_temp_offset


# ---------------------------------------------------------------------------
# Gas heater
# ---------------------------------------------------------------------------
