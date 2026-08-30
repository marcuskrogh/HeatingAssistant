"""Hydronic radiator heat source."""

from __future__ import annotations

from typing import Optional

from .base import HeatSource

class HydronicRadiator(HeatSource):
    """
    Hot-water radiator fed by district heating (fjernvarme) or a central boiler.

    The room receives heat via a thermostatic radiator valve (TRV) or zone
    valve that modulates hot-water flow.  From the MPC's perspective the
    physics is a linear emitter:

        Q_thermal = max_power × power_scale × u

    ``max_power`` should be the radiator's EN 442 rated output [W] at the
    standard conditions (supply 75 °C / return 65 °C / room 20 °C, ΔT = 50 K).
    The parameter estimator will identify a ``power_scale`` correction if the
    actual supply temperature or room conditions differ.

    District heating and boiler-fed systems draw negligible electricity for
    their thermal output (only circulation-pump power, which is not modelled),
    so ``elec_per_unit_heat`` is zero.

    The default emitter time constant of 600 s (≈ 10 min) captures the
    water-mass and metal-body lag between a TRV command and the resulting
    change in room-air heat delivery.  Hydronic underfloor heating has a much
    longer lag (hours); use ``type: hydronic_floor_heating`` or set
    ``emitter_time_constant: 3600`` explicitly.
    """

    def __init__(
        self,
        name: str,
        room: str,
        max_power: float,
        max_temp_offset: float = 5.0,
        heater_entity: Optional[str] = None,
        power_scale: float = 1.0,
        emitter_time_constant: float = 600.0,
    ) -> None:
        super().__init__(name, room, max_power, heater_entity, power_scale, emitter_time_constant)
        if max_temp_offset < 0.0:
            raise ValueError(f"max_temp_offset must be >= 0; got {max_temp_offset}")
        self.max_temp_offset = max_temp_offset
        self._gain: float = max_power * self._power_scale

    def _recompute_power_scaled_gains(self) -> None:
        self._gain = self.max_power * self._power_scale

    def thermal_power(self, setpoint_fraction: float, outdoor_temp: float = 0.0) -> float:
        """Thermal power = EN 442 rated output × power_scale × u."""
        return self._gain * setpoint_fraction

    def rated_heating_capacity(self, outdoor_temp: float = 0.0) -> float:
        return self.max_power

    @property
    def elec_per_unit_heat(self) -> float:
        return 0.0

    def target_temperature(self, setpoint_fraction: float, internal_temp: float) -> float:
        setpoint_fraction = max(0.0, min(1.0, setpoint_fraction))
        return internal_temp + setpoint_fraction * self.max_temp_offset


