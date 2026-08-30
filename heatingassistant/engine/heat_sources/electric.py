"""Electric resistive heater."""

from __future__ import annotations

from typing import Optional

from .base import HeatSource

# Electric heater
# ---------------------------------------------------------------------------

class ElectricHeater(HeatSource):
    """
    Resistive electric heater (or infrared panel).

    The thermal output equals electrical input power multiplied by
    ``efficiency`` (should be close to 1.0 for resistive heaters).
    """

    def __init__(
        self,
        name: str,
        room: str,
        max_power: float,
        efficiency: float = 1.0,
        max_temp_offset: float = 5.0,
        heater_entity: Optional[str] = None,
        power_scale: float = 1.0,
        emitter_time_constant: float = 0.0,
    ) -> None:
        # Default τ_em = 0: electric resistive heaters have no thermal
        # mass to speak of (a resistor goes from cold to hot in
        # seconds), so the commanded fraction reaches the air node
        # essentially instantly.  Pre-A2 / pre-B2 behaviour is
        # preserved exactly when τ_em stays at 0.
        super().__init__(
            name, room, max_power, heater_entity, power_scale,
            emitter_time_constant=emitter_time_constant,
        )
        if not 0.0 < efficiency <= 1.0:
            raise ValueError(f"efficiency must be in (0, 1]; got {efficiency}")
        if max_temp_offset < 0.0:
            raise ValueError(f"max_temp_offset must be >= 0; got {max_temp_offset}")
        self.efficiency = efficiency
        self.max_temp_offset = max_temp_offset
        self._gain: float = max_power * efficiency * self._power_scale

    def _recompute_power_scaled_gains(self) -> None:
        """Re-derive the cached linear gain after a ``power_scale`` change."""
        self._gain = self.max_power * self.efficiency * self._power_scale

    def thermal_power(self, setpoint_fraction: float, outdoor_temp: float = 0.0) -> float:
        """Thermal power = electrical power × efficiency × power_scale."""
        return self._gain * setpoint_fraction

    def rated_heating_capacity(self, outdoor_temp: float = 0.0) -> float:
        return self.max_power * self.efficiency

    @property
    def elec_per_unit_heat(self) -> float:
        return self.max_power * self.efficiency

    def target_temperature(self, setpoint_fraction: float, internal_temp: float) -> float:
        """Target setpoint = internal_temp + fraction × max_temp_offset."""
        setpoint_fraction = max(0.0, min(1.0, setpoint_fraction))
        return internal_temp + setpoint_fraction * self.max_temp_offset


# ---------------------------------------------------------------------------
# Generic thermostat
# ---------------------------------------------------------------------------
