"""
Heat-source models for the Heating Assistant integration.

Supported types
---------------
* ElectricHeater – resistive heater; COP = efficiency (typically 1.0)
* HeatPump – vapour-compression heat pump with outdoor-temperature-dependent COP
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


class HeatSource(ABC):
    """Abstract base class for a controllable heat source."""

    def __init__(
        self,
        name: str,
        room: str,
        max_power: float,
        heater_entity: Optional[str] = None,
    ) -> None:
        self.name = name
        self.room = room                     # name of the room this source heats
        self.max_power = max_power           # W (maximum *thermal* output)
        self.heater_entity = heater_entity   # optional HA entity_id
        self._current_power: float = 0.0    # W

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def current_power(self) -> float:
        """Current thermal output power [W]."""
        return self._current_power

    @abstractmethod
    def thermal_power(self, setpoint_fraction: float, outdoor_temp: float = 0.0) -> float:
        """
        Compute thermal output [W] for a given fractional set-point.

        Parameters
        ----------
        setpoint_fraction : float
            Control signal in [0, 1] (0 = off, 1 = full power).
        outdoor_temp : float
            Outdoor air temperature [°C].  Used by heat pumps to adjust COP.

        Returns
        -------
        float : thermal power output [W].
        """

    def set_power(self, setpoint_fraction: float, outdoor_temp: float = 0.0) -> float:
        """
        Apply a control set-point, update internal state, and return the
        resulting thermal power output [W].
        """
        setpoint_fraction = max(0.0, min(1.0, setpoint_fraction))
        power = self.thermal_power(setpoint_fraction, outdoor_temp)
        self._current_power = power
        return power

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(name={self.name!r}, room={self.room!r}, "
            f"max_power={self.max_power} W, current={self._current_power:.1f} W)"
        )


# ---------------------------------------------------------------------------
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
        heater_entity: Optional[str] = None,
    ) -> None:
        super().__init__(name, room, max_power, heater_entity)
        if not 0.0 < efficiency <= 1.0:
            raise ValueError(f"efficiency must be in (0, 1]; got {efficiency}")
        self.efficiency = efficiency

    def thermal_power(self, setpoint_fraction: float, outdoor_temp: float = 0.0) -> float:
        """Thermal power = electrical power × efficiency (outdoor_temp ignored)."""
        return self.max_power * setpoint_fraction * self.efficiency


# ---------------------------------------------------------------------------
# Heat pump
# ---------------------------------------------------------------------------

def _cop_at_temp(cop_rated: float, cop_temp_ref: float, outdoor_temp: float) -> float:
    """
    Estimate COP at a given outdoor temperature using a linear correction
    derived from the Carnot COP relationship.

    The correction factor is based on the ratio of ideal (Carnot) COPs:
        COP_real(T) ≈ COP_rated * (T_supply - T_outdoor_ref)
                                  / (T_supply - T_outdoor)

    We assume a fixed supply temperature of 35 °C (typical for underfloor or
    low-temperature radiators) and correct relative to the rated point.
    """
    T_supply = 35.0 + 273.15   # K
    T_ref = cop_temp_ref + 273.15
    T_out = outdoor_temp + 273.15

    # Carnot COPs
    cop_carnot_ref = T_supply / max(T_supply - T_ref, 1.0)
    cop_carnot_now = T_supply / max(T_supply - T_out, 1.0)

    if cop_carnot_ref <= 0:
        return cop_rated

    # Scale rated COP by the ratio of Carnot efficiencies
    # (Assume constant second-law efficiency)
    return cop_rated * (cop_carnot_now / cop_carnot_ref)


class HeatPump(HeatSource):
    """
    Air-source heat pump with temperature-dependent COP.

    The COP decreases as outdoor temperature drops (less efficient when it is
    colder outside).  This model uses a Carnot-based linear approximation.

    The heat pump shuts off (COP = 0) below ``min_outdoor_temp`` to prevent
    defrost damage.

    When operating in cooling mode (dry/dehumidify), the heat pump removes
    heat from the room with an assumed efficiency of 1.0 (conservative estimate).
    """

    def __init__(
        self,
        name: str,
        room: str,
        max_power: float,
        cop_rated: float = 3.5,
        cop_temp_ref: float = 7.0,
        min_outdoor_temp: float = -20.0,
        min_power: float = 0.0,
        max_temp_offset: float = 5.0,
        turn_off_deadband: float = 1.0,
        heater_entity: Optional[str] = None,
        cooling_efficiency: float = 1.0,
    ) -> None:
        super().__init__(name, room, max_power, heater_entity)
        self.cop_rated = cop_rated
        self.cop_temp_ref = cop_temp_ref
        self.min_outdoor_temp = min_outdoor_temp
        self.min_power = min_power
        self.max_temp_offset = max_temp_offset
        self.turn_off_deadband = turn_off_deadband
        self.cooling_efficiency = cooling_efficiency

    def cop(self, outdoor_temp: float) -> float:
        """Return the estimated COP at the given outdoor temperature."""
        if outdoor_temp < self.min_outdoor_temp:
            return 0.0
        return max(1.0, _cop_at_temp(self.cop_rated, self.cop_temp_ref, outdoor_temp))

    def thermal_power(self, setpoint_fraction: float, outdoor_temp: float = 0.0) -> float:
        """
        Thermal power output [W].

        The heat pump's *rated* electrical input power is ``max_power / cop_rated``.
        The actual thermal output depends on the actual COP at the current
        outdoor temperature.

        If the computed output is positive but below ``min_power`` the heat
        pump cannot operate and the method returns 0.
        """
        electric_max = self.max_power / self.cop_rated  # rated electrical input [W]
        actual_cop = self.cop(outdoor_temp)
        power = electric_max * setpoint_fraction * actual_cop
        if 0.0 < power < self.min_power:
            return 0.0
        return power

    def cooling_power(self, outdoor_temp: float = 0.0) -> float:
        """
        Compute the cooling (heat removal) power when operating in dry/dehumidify mode.

        When in cooling mode, the heat pump removes heat from the room. We assume
        a conservative efficiency of 1.0 (cooling_efficiency parameter).

        Returns
        -------
        float
            Negative thermal power (heat removal) [W].
        """
        # Return a negative value to indicate heat removal
        # Use a fraction of max_power as the cooling capacity
        return -self.max_power * self.cooling_efficiency

    def target_temperature(
        self, setpoint_fraction: float, internal_temp: float,
    ) -> float:
        """
        Compute the temperature setpoint to send to the heat pump's climate
        entity so that it produces the desired fraction of its maximum
        thermal output.

        The heat pump modulates its output based on the gap between its
        internal setpoint and its own temperature sensor.  This method
        returns::

            T_target = T_hp + fraction × max_temp_offset

        where *T_hp* is the heat pump's own temperature reading (which may
        differ from HeatingAssistant's room sensor) and *max_temp_offset*
        is the configured maximum temperature differential at full power.

        Parameters
        ----------
        setpoint_fraction : float
            Desired power as a fraction of maximum [0, 1].
        internal_temp : float
            The heat pump's own internal temperature reading [°C].

        Returns
        -------
        float
            Target temperature [°C] to set on the heat pump climate entity.
        """
        setpoint_fraction = max(0.0, min(1.0, setpoint_fraction))
        return internal_temp + setpoint_fraction * self.max_temp_offset
