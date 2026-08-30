"""Gas-fired heat source."""

from __future__ import annotations

from typing import Optional

from .base import HeatSource

class GasHeater(HeatSource):
    """
    Gas-fired heater — furnace, gas wall heater, or gas boiler with direct
    modulating control (e.g. a 0–10 V or OpenTherm interface).

    Physics: linear, Q = max_power × efficiency × power_scale × u, where
    ``efficiency`` is the combustion efficiency (AFUE-like fraction of the
    gas lower heating value delivered as room heat).  Condensing units reach
    ≈ 0.95; older conventional units ≈ 0.80–0.85.

    ``elec_per_unit_heat`` is zero: gas heaters draw no electrical power for
    their heating output (fan, ignition, and controls draw negligible watts
    that are not modelled here).
    """

    def __init__(
        self,
        name: str,
        room: str,
        max_power: float,
        efficiency: float = 0.90,
        max_temp_offset: float = 5.0,
        heater_entity: Optional[str] = None,
        power_scale: float = 1.0,
        emitter_time_constant: float = 0.0,
    ) -> None:
        super().__init__(name, room, max_power, heater_entity, power_scale, emitter_time_constant)
        if not 0.0 < efficiency <= 1.0:
            raise ValueError(f"efficiency must be in (0, 1]; got {efficiency}")
        if max_temp_offset < 0.0:
            raise ValueError(f"max_temp_offset must be >= 0; got {max_temp_offset}")
        self.efficiency = efficiency
        self.max_temp_offset = max_temp_offset
        self._gain: float = max_power * efficiency * self._power_scale

    def _recompute_power_scaled_gains(self) -> None:
        self._gain = self.max_power * self.efficiency * self._power_scale

    def thermal_power(self, setpoint_fraction: float, outdoor_temp: float = 0.0) -> float:
        """Thermal power = gas input × combustion efficiency × power_scale."""
        return self._gain * setpoint_fraction

    def rated_heating_capacity(self, outdoor_temp: float = 0.0) -> float:
        return self.max_power * self.efficiency

    @property
    def elec_per_unit_heat(self) -> float:
        return 0.0

    def target_temperature(self, setpoint_fraction: float, internal_temp: float) -> float:
        setpoint_fraction = max(0.0, min(1.0, setpoint_fraction))
        return internal_temp + setpoint_fraction * self.max_temp_offset


# ---------------------------------------------------------------------------
# Hydronic radiator  (district heating / hot-water radiator)
# ---------------------------------------------------------------------------
