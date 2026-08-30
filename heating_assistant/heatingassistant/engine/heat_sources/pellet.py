"""Pellet stove heat source."""

from __future__ import annotations

from typing import Optional

from .base import HeatSource

class PelletStove(HeatSource):
    """
    Pellet stove with a minimum modulation floor.

    A pellet stove cannot be throttled below ``min_power_fraction`` of rated
    output without extinguishing; below that threshold it is fully off.  This
    creates a piecewise map:

        u < min_power_fraction  →  Q = 0
        u ≥ min_power_fraction  →  Q = max_power × efficiency × power_scale × u

    The MPC treats the region below the floor as a deadband; in practice the
    controller will operate either fully off or above the floor.

    ``elec_per_unit_heat`` is zero: the pellet stove draws no meaningful
    electricity for its thermal output (auger motor and controls are negligible).

    The default ``emitter_time_constant`` of 2400 s (40 min) reflects the
    metal firebox and heat exchanger mass that buffers combustion dynamics
    from room-air delivery.
    """

    def __init__(
        self,
        name: str,
        room: str,
        max_power: float,
        efficiency: float = 0.88,
        min_power_fraction: float = 0.30,
        max_temp_offset: float = 5.0,
        heater_entity: Optional[str] = None,
        power_scale: float = 1.0,
        emitter_time_constant: float = 2400.0,
    ) -> None:
        super().__init__(
            name, room, max_power, heater_entity, power_scale,
            emitter_time_constant=emitter_time_constant,
        )
        if not 0.0 < efficiency <= 1.0:
            raise ValueError(f"efficiency must be in (0, 1]; got {efficiency}")
        if not 0.0 <= min_power_fraction < 1.0:
            raise ValueError(
                f"min_power_fraction must be in [0, 1); got {min_power_fraction}"
            )
        if max_temp_offset < 0.0:
            raise ValueError(f"max_temp_offset must be >= 0; got {max_temp_offset}")
        self.efficiency = efficiency
        self.min_power_fraction = min_power_fraction
        self.max_temp_offset = max_temp_offset
        self._gain: float = max_power * efficiency * self._power_scale

    def _recompute_power_scaled_gains(self) -> None:
        """Re-derive the cached linear gain after a ``power_scale`` change."""
        self._gain = self.max_power * self.efficiency * self._power_scale

    def thermal_power(self, setpoint_fraction: float, outdoor_temp: float = 0.0) -> float:
        """
        Thermal power [W].

        Returns 0 when ``setpoint_fraction`` is below the minimum modulation
        floor; otherwise linear above the floor.
        """
        if setpoint_fraction < self.min_power_fraction:
            return 0.0
        return self._gain * setpoint_fraction

    def rated_heating_capacity(self, outdoor_temp: float = 0.0) -> float:
        """Rated heating capacity [W] at full modulation."""
        return self.max_power * self.efficiency

    @property
    def elec_per_unit_heat(self) -> float:
        """Pellet stoves draw no meaningful electricity for heat output."""
        return 0.0

    def target_temperature(self, setpoint_fraction: float, internal_temp: float) -> float:
        """Target setpoint = internal_temp + fraction × max_temp_offset."""
        setpoint_fraction = max(0.0, min(1.0, setpoint_fraction))
        return internal_temp + setpoint_fraction * self.max_temp_offset


# ---------------------------------------------------------------------------
# Electric storage heater
# ---------------------------------------------------------------------------
