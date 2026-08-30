"""Ground-source heat pump."""

from __future__ import annotations

import math
from typing import Optional

from .base import HeatSource, _soft_ceiling

class GroundSourceHeatPump(HeatSource):
    """
    Ground-source heat pump (GSHP) with borehole or ground-loop heat exchange.

    Unlike an air-source heat pump, the GSHP draws heat from the ground at a
    nearly constant temperature (``ground_temp``, default 10 °C).  The COP
    does not vary with outdoor air temperature — it depends on the stable
    ground-loop supply temperature.  At any outdoor condition the delivered
    thermal power is therefore flat:

        Q = max_power × heating_efficiency × power_scale × u

    The ``cop_rated`` parameter is used only for the ``elec_per_unit_heat``
    calculation (electrical draw = thermal / cop_rated) and is not applied as
    an outdoor-temperature correction (cf. ``HeatPump``).

    Cooling mode follows the same convention as ``HeatPump``: the cooling
    capacity is derived from the rated electrical input multiplied by
    ``cooling_cop`` (EER).

    The sigmoid logit setpoint mapping from ``HeatPump.target_temperature`` is
    reused unchanged — the control characteristic of the indoor unit is the
    same regardless of the heat source.
    """

    def __init__(
        self,
        name: str,
        room: str,
        max_power: float,
        cop_rated: float = 4.5,
        min_outdoor_temp: float = -50.0,
        max_temp_offset: float = 5.0,
        delta_sat: float = 3.0,
        hvac_mode: str = "heat_cool",
        heater_entity: Optional[str] = None,
        cooling_cop: float = 2.5,
        cooling_efficiency: float = 1.0,
        heating_efficiency: float = 1.0,
        power_scale: float = 1.0,
        emitter_time_constant: float = 0.0,
    ) -> None:
        super().__init__(
            name, room, max_power, heater_entity, power_scale,
            emitter_time_constant=emitter_time_constant,
        )
        self.cop_rated = cop_rated
        self.min_outdoor_temp = min_outdoor_temp
        self.max_temp_offset = max_temp_offset
        self.delta_sat = delta_sat
        self.hvac_mode = hvac_mode
        self.cooling_cop = cooling_cop
        self.cooling_efficiency = cooling_efficiency
        self.heating_efficiency = heating_efficiency
        # Precomputed rated electrical input (used in elec_per_unit_* properties).
        self._electric_max: float = max_power / cop_rated if cop_rated > 0 else 0.0
        self._recompute_power_scaled_gains()

    def _recompute_power_scaled_gains(self) -> None:
        """Re-derive cached thermal-output constants after a scale change."""
        self._q_heat_max: float = (
            self.max_power * self.heating_efficiency * self._power_scale
        )
        self._q_cool_const: float = (
            self._electric_max * self.cooling_cop
            * self.cooling_efficiency * self._power_scale
        )

    @property
    def can_cool(self) -> bool:
        """Returns True when the configured hvac_mode includes cooling."""
        return self.hvac_mode in ("cool", "heat_cool") and self.cooling_cop > 0

    @property
    def elec_per_unit_heat(self) -> float:
        """Electrical draw [W] at full heating load (thermal / cop_rated)."""
        electric_max = self.max_power / self.cop_rated if self.cop_rated > 0 else 0.0
        return electric_max * self.heating_efficiency * self.power_scale

    @property
    def elec_per_unit_cool(self) -> float:
        if not self.can_cool:
            return 0.0
        electric_max = self.max_power / self.cop_rated if self.cop_rated > 0 else 0.0
        return electric_max * self.cooling_efficiency * self.power_scale

    @property
    def u_min(self) -> float:
        return 0.0 if self.hvac_mode == "heat" else -1.0

    @property
    def u_max(self) -> float:
        return 0.0 if self.hvac_mode == "cool" else 1.0

    def rated_heating_capacity(self, outdoor_temp: float = 0.0) -> float:
        """Rated heating capacity [W] — flat, no outdoor-temp correction."""
        return self.max_power * self.heating_efficiency

    @property
    def rated_cooling_power(self) -> float:
        """Rated cooling capacity [W], without ``power_scale``."""
        if not self.can_cool or self.cop_rated <= 0:
            return 0.0
        return self._electric_max * self.cooling_cop * self.cooling_efficiency

    def smooth_thermal_power(
        self, u: float, outdoor_temp: float = 0.0, k_base: float = 5.0,
    ) -> float:
        """Piecewise-linear model power [W]: +Q_heat·u or −Q_cool·|u|."""
        q_heat = self.max_power * self.heating_efficiency * self._power_scale
        q_cool = self._q_cool_const
        if q_heat <= 0.0 or q_cool <= 0.0:
            raw = q_heat * max(0.0, u)
            return _soft_ceiling(raw, self._q_heat_max) if raw > 0.0 else raw
        if u >= 0.0:
            return _soft_ceiling(q_heat * u, self._q_heat_max)
        return q_cool * u

    def display_smooth_thermal_power(
        self, u: float, outdoor_temp: float = 0.0, k_base: float = 5.0,
    ) -> float:
        """Configured thermal output [W] at *u* (ignores ``power_scale``)."""
        q_heat = self.rated_heating_capacity(outdoor_temp)
        q_cool = self.rated_cooling_power
        if q_heat <= 0.0 or q_cool <= 0.0:
            return q_heat * max(0.0, u)
        return q_heat * u if u >= 0.0 else q_cool * u

    def display_thermal_power(
        self, setpoint_fraction: float, outdoor_temp: float = 0.0,
    ) -> float:
        if self.can_cool:
            return self.display_smooth_thermal_power(
                float(setpoint_fraction), outdoor_temp,
            )
        return super().display_thermal_power(setpoint_fraction, outdoor_temp)

    def thermal_power(self, setpoint_fraction: float, outdoor_temp: float = 0.0) -> float:
        """
        Thermal power [W].  Linear in setpoint_fraction; no outdoor-temp COP
        correction (ground temperature is stable year-round).

        Heating uses the rated heating capacity (soft-ceilinged). Cooling
        uses electrical input × cooling COP, not ``−max_power``.
        """
        return self.smooth_thermal_power(float(setpoint_fraction), outdoor_temp)

    def cooling_power(self, outdoor_temp: float = 0.0) -> float:
        """Cooling (heat-removal) power [W] — negative value."""
        if self.cop_rated <= 0:
            return 0.0
        return -self._q_cool_const

    def target_temperature(
        self,
        fraction: float,
        base_temp: float,
        outdoor_temp: float = 0.0,
    ) -> float:
        """
        Compute the climate-entity setpoint from the control fraction, using
        the same sigmoid logit formula as ``HeatPump.target_temperature``.
        """
        fraction = max(self.u_min, min(self.u_max, fraction))
        if abs(fraction) < 1e-9:
            return base_temp
        u_range = self.u_max if fraction > 0.0 else abs(self.u_min)
        f = max(1e-4, min(1.0 - 1e-4, abs(fraction) / max(u_range, 1e-9)))
        logit_f = math.log(f / (1.0 - f))
        half_sat = self.delta_sat / 2.0
        offset = half_sat * (1.0 + logit_f / 5.0)
        offset = max(0.0, min(offset, self.delta_sat))
        return base_temp + math.copysign(offset, fraction)


# ---------------------------------------------------------------------------
# Pellet stove
# ---------------------------------------------------------------------------
