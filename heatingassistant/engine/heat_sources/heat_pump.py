"""Air-source heat pump with outdoor-temperature-dependent COP."""

from __future__ import annotations

import math
from typing import Optional

from .base import HeatSource, _T_SUPPLY_K, _soft_ceiling

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

    Cooling mode
    ------------
    When operating in cooling mode (dry / fan-only / cool), the heat pump
    removes heat from the room.  The cooling capacity must NOT be derived
    from the heating thermal output ``max_power`` — that value already
    incorporates the heating COP and would overstate cooling capability.
    Instead the cooling capacity is computed from the rated electrical
    input multiplied by the *cooling* coefficient of performance (EER):

        electric_max = max_power / cop_rated         [rated electrical input]
        cooling_capacity_max = electric_max * cooling_cop

    The optional ``cooling_efficiency`` parameter (default 1.0) lets
    integrators throttle the cooling capacity below the rated maximum
    (e.g. for dehumidify / dry mode which is gentler than full cooling).
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
        delta_sat: float = 3.0,
        hvac_mode: str = "heat_cool",
        heater_entity: Optional[str] = None,
        cooling_cop: float = 2.5,
        cooling_efficiency: float = 1.0,
        heating_efficiency: float = 1.0,
        power_scale: float = 1.0,
        emitter_time_constant: float = 0.0,
    ) -> None:
        # Phase 1 B2 default τ_em = 0 at the class level — the
        # commanded fraction reaches the thermal node instantly.
        # Typology-based defaults (heat-pump-internal-unit ≈ 60 s,
        # hydronic-radiator ≈ 600 s) are applied in
        # ``coordinator.build_heat_sources`` when the user hasn't
        # explicitly set ``emitter_time_constant`` per-source, so
        # direct test constructions stay backward-compatible.
        super().__init__(
            name, room, max_power, heater_entity, power_scale,
            emitter_time_constant=emitter_time_constant,
        )
        self.cop_rated = cop_rated
        self.cop_temp_ref = cop_temp_ref
        self.min_outdoor_temp = min_outdoor_temp
        # Deprecated, inert: the min-power output deadband was removed (it forced
        # sub-threshold outputs to zero, which interfered with the MPC).  The
        # attribute is retained so existing configs / sensors keep loading.
        self.min_power = min_power
        self.max_temp_offset = max_temp_offset
        self.delta_sat = delta_sat
        self.hvac_mode = hvac_mode
        self.cooling_cop = cooling_cop
        self.cooling_efficiency = cooling_efficiency
        self.heating_efficiency = heating_efficiency

        # Precomputed constants (cop_rated is fixed at construction time)
        self._electric_max: float = max_power / cop_rated if cop_rated > 0 else 0.0
        # Precomputed Carnot-ratio scale factor: cop(T_out) = _cop_scale * T_supply / denom
        _T_ref_K = cop_temp_ref + 273.15
        self._cop_scale: float = cop_rated * max(_T_SUPPLY_K - _T_ref_K, 1.0) / _T_SUPPLY_K
        # Power-scale-dependent thermal-output caches (cooling const, base heat
        # output at COP=1, and the rated-output ceiling).  Derived in the hook
        # so they are re-computed whenever ``power_scale`` is reassigned.
        self._recompute_power_scaled_gains()

    def _recompute_power_scaled_gains(self) -> None:
        """Re-derive cached thermal-output constants after a scale change."""
        self._q_cool_const: float = (
            self._electric_max * self.cooling_cop
            * self.cooling_efficiency * self._power_scale
        )
        # Base thermal output at COP=1 (used in thermal_power / smooth_thermal_power)
        self._q_heat_base: float = (
            self._electric_max * self.heating_efficiency * self._power_scale
        )
        # Hard ceiling: rated thermal output must never be exceeded regardless of outdoor COP
        self._q_heat_max: float = (
            self.max_power * self.heating_efficiency * self._power_scale
        )
        # Rated-capacity caches (no power_scale) for plot bounds — mirrors
        # ``rated_cooling_power`` on the heating side.
        self._q_heat_base_rated: float = (
            self._electric_max * self.heating_efficiency
        )
        self._q_heat_max_rated: float = (
            self.max_power * self.heating_efficiency
        )

    @property
    def can_cool(self) -> bool:
        """Returns True when the configured hvac_mode includes cooling."""
        return self.hvac_mode in ("cool", "heat_cool") and self.cooling_cop > 0

    @property
    def elec_per_unit_heat(self) -> float:
        # COP cancels: P_elec = configured thermal / COP at full command.
        electric_max = self.max_power / self.cop_rated if self.cop_rated > 0 else 0.0
        return electric_max * self.heating_efficiency

    @property
    def elec_per_unit_cool(self) -> float:
        if not self.can_cool:
            return 0.0
        electric_max = self.max_power / self.cop_rated if self.cop_rated > 0 else 0.0
        return electric_max * self.cooling_efficiency

    @property
    def u_min(self) -> float:
        return 0.0 if self.hvac_mode == "heat" else -1.0

    @property
    def u_max(self) -> float:
        return 0.0 if self.hvac_mode == "cool" else 1.0

    def cop(self, outdoor_temp: float) -> float:
        """Return the estimated COP at the given outdoor temperature."""
        if outdoor_temp < self.min_outdoor_temp:
            return 0.0
        return max(1.0, self._cop_scale * _T_SUPPLY_K / max(_T_SUPPLY_K - outdoor_temp - 273.15, 1.0))

    def thermal_power(self, setpoint_fraction: float, outdoor_temp: float = 0.0) -> float:
        """
        Model thermal power [W] including the identified ``power_scale``.

        Heating (``u ≥ 0``) is COP-limited heating capacity times the
        fraction. Cooling (``u < 0``) is rated cooling capacity times the
        fraction — not heating capacity with a negative sign, which would
        overstate heat removal by about ``cop_rated / cooling_cop``.

        Used by the MPC/EKF plant model only.  For sensors and plots use
        :meth:`display_thermal_power` instead.
        """
        return self.smooth_thermal_power(float(setpoint_fraction), outdoor_temp)

    def rated_heating_capacity(self, outdoor_temp: float = 0.0) -> float:
        """COP-limited rated heating capacity at ``u = 1`` without ``power_scale``."""
        if outdoor_temp < self.min_outdoor_temp:
            return 0.0
        cop_now = max(
            1.0,
            self._cop_scale * _T_SUPPLY_K
            / max(_T_SUPPLY_K - outdoor_temp - 273.15, 1.0),
        )
        return _soft_ceiling(
            self._q_heat_base_rated * cop_now, self._q_heat_max_rated,
        )

    def cooling_power(self, outdoor_temp: float = 0.0) -> float:
        """
        Compute the cooling (heat removal) power for the current cooling cycle.

        Cooling capacity is derived from the rated electrical input multiplied
        by the cooling coefficient of performance (EER), then scaled by
        ``cooling_efficiency`` and ``power_scale``.  This avoids the common
        bug of inheriting the heating thermal max as the cooling capacity,
        which would overstate the heat-removal rate by a factor of
        ``cop_rated / cooling_cop`` (typically ≈ 1.4×).

        Parameters
        ----------
        outdoor_temp : float
            Outdoor temperature [°C].  Reserved for future temperature-
            dependent cooling COP corrections; unused by the current linear
            model.

        Returns
        -------
        float
            Negative thermal power (heat removal) [W].
        """
        if self.cop_rated <= 0:
            return 0.0
        return -self._q_cool_const

    @property
    def rated_cooling_power(self) -> float:
        """Rated (configured) cooling capacity [W], without power_scale adjustment.

        Analogous to ``max_power`` for heating: reflects the values the user
        configured (electric_max × cooling_cop × cooling_efficiency) rather than
        the runtime-scaled ``_q_cool_const``.  Use for plot bounds and
        :meth:`display_smooth_thermal_power`; use :meth:`cooling_power` for the
        MPC plant model only.
        """
        if not self.can_cool or self.cop_rated <= 0:
            return 0.0
        return self._electric_max * self.cooling_cop * self.cooling_efficiency

    def display_thermal_power(
        self, setpoint_fraction: float, outdoor_temp: float = 0.0,
    ) -> float:
        """Configured thermal output for sensors/plots (ignores ``power_scale``)."""
        if self.can_cool:
            return self.display_smooth_thermal_power(
                float(setpoint_fraction), outdoor_temp,
            )
        return super().display_thermal_power(setpoint_fraction, outdoor_temp)

    def display_smooth_thermal_power(
        self, u: float, outdoor_temp: float, k_base: float = 5.0,
    ) -> float:
        """Piecewise-linear display power [W] at control input *u* (no ``power_scale``).

        Same shape as :meth:`smooth_thermal_power` but uses configured rated
        capacities so room-view plots reflect heater configuration rather than
        the internal sysid scale factor.
        """
        if outdoor_temp < self.min_outdoor_temp:
            return 0.0
        q_heat = self.rated_heating_capacity(outdoor_temp)
        q_cool = self.rated_cooling_power
        if q_heat <= 0.0 or q_cool <= 0.0:
            return q_heat * max(0.0, u)
        return q_heat * u if u >= 0.0 else q_cool * u

    def target_temperature(
        self,
        fraction: float,
        base_temp: float,
        outdoor_temp: float = 0.0,
    ) -> float:
        """
        Compute the climate-entity setpoint from the control fraction.

        The HP's physical power output vs. setpoint offset is sigmoidal, not
        linear: it reaches near-maximum output at a relatively small offset
        (~``delta_sat`` °C) and has a dead band near zero.  A naive linear
        mapping ``fraction × max_temp_offset`` would over-drive the HP at
        intermediate fractions and starve it at low fractions.

        Instead, the required offset is derived by inverting the HP's
        approximate physical sigmoid::

            P(Δ) ≈ P_max · σ(k · (2Δ/δ_sat − 1))

        giving a smooth (C∞), monotone mapping::

            Δ = (δ_sat / 2) · (1 + logit(f) / k)

        where ``f = |fraction| / u_range`` is the normalised fraction,
        ``δ_sat = delta_sat`` is the saturation offset (°C), and ``k = 5``
        matches the sigmoid steepness used in the MPC model.  The result is
        clamped to ``[0, delta_sat]`` to handle numerical edge cases.

        Parameters
        ----------
        fraction : float
            MPC control signal clamped to ``[u_min, u_max]``.
            Positive → heating, negative → cooling, zero → idle.
        base_temp : float
            Current temperature reading from the HP's own sensor [°C].
            The offset is added to this to form the physical setpoint.
        outdoor_temp : float
            Accepted for API compatibility; not used in this formula.
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

    def fraction_from_setpoint_offset(self, offset: float) -> float:
        """Invert :meth:`target_temperature` for climate readback.

        ``offset`` is the signed gap between the commanded setpoint and the
        comfort setpoint base the integration used when writing the command
        (``target − base``).  Returns the control fraction in
        ``[u_min, u_max]``.
        """
        if abs(offset) < 1e-9:
            return 0.0
        sign = 1.0 if offset > 0.0 else -1.0
        u_range = self.u_max if sign > 0.0 else abs(self.u_min)
        if u_range <= 0.0:
            return 0.0
        half_sat = self.delta_sat / 2.0
        if half_sat <= 0.0:
            return 0.0
        offset_mag = min(abs(offset), self.delta_sat)
        # Inverse of offset = half_sat · (1 + logit(f) / 5).
        logit_f = 5.0 * (offset_mag / half_sat - 1.0)
        f = 1.0 / (1.0 + math.exp(-logit_f))
        return sign * f * u_range

    def smooth_thermal_power(
        self, u: float, outdoor_temp: float, k_base: float = 5.0,
    ) -> float:
        """
        Model piecewise-linear power [W] including ``power_scale``.

        Used by the MPC/EKF plant model only.  For sensors and plots use
        :meth:`display_smooth_thermal_power` instead.

        * φ(u) = Q_heat · u  for u ≥ 0  (heating)
        * φ(u) = Q_cool · u  for u < 0  (cooling; Q_cool > 0 ⇒ negative power)

        with φ(0) = 0, φ(+1) = +Q_heat, and φ(−1) = −Q_cool where::

            Q_heat = thermal_power(1, T_out)   # COP-limited heating capacity [W]
            Q_cool = |cooling_power(T_out)|    # rated cooling capacity [W]

        ``k_base`` is retained for API compatibility but no longer shapes the
        curve (the previous logistic sigmoid under-predicted delivery when the
        linearised MPC turned the compressor down).

        Parameters
        ----------
        u : float
            MPC control input in [−1, 1]; positive → heating, negative → cooling.
        outdoor_temp : float
            Current outdoor temperature [°C] for COP calculation.
        k_base : float, optional
            Unused; kept for backward compatibility.  Default is 5.0.

        Returns
        -------
        float
            Thermal power [W].  Positive values represent heat addition;
            negative values represent heat removal (cooling).
        """
        if outdoor_temp < self.min_outdoor_temp:
            return 0.0
        cop_now = max(1.0, self._cop_scale * _T_SUPPLY_K / max(_T_SUPPLY_K - outdoor_temp - 273.15, 1.0))
        q_heat = _soft_ceiling(self._q_heat_base * cop_now, self._q_heat_max)
        q_cool = self._q_cool_const

        if q_heat <= 0.0 or q_cool <= 0.0:
            # Degenerate case: fall back to linear heating-only model
            return q_heat * max(0.0, u)

        # Piecewise-linear power curve: heating delivers +q_heat·u for u ≥ 0 and
        # cooling delivers q_cool·u (negative) for u < 0, with φ(0) = 0.
        #
        # This mirrors the linear heat-only model so that the *local* slope the
        # MPC linearises around equals the *global* slope over the control
        # range.  The previous logistic curve was steep near u = 0 and flat near
        # the operating point, so its local tangent badly under-predicted the
        # heat lost when the compressor was turned down — the linearised MPC
        # then believed it could coast for free and rode the comfort boundary.
        # ``k_base`` is retained for API compatibility but no longer shapes the
        # curve.
        return q_heat * u if u >= 0.0 else q_cool * u

    def control_for_power_fraction(
        self, power_fraction: float, outdoor_temp: float = 0.0, k_sigmoid: float = 5.0,
    ) -> float:
        """Invert :meth:`smooth_thermal_power` so a commanded power fraction lands
        linearly on delivered power.

        ``power_fraction`` is the signed fraction of capacity: ``+pf`` →
        ``pf · Q_heat`` (heating), ``-pf`` → ``pf · Q_cool`` (cooling).  Returns
        the control input ``u`` (clamped to ``[u_min, u_max]``) that produces that
        thermal power under the same power curve the controller uses.

        The power curve is now piecewise-linear (``φ(u) = q_heat·u`` for ``u ≥ 0``
        and ``q_cool·u`` for ``u < 0``), so the control input that delivers a
        signed fraction of capacity is simply that fraction — identical to the
        linear heat-only identity.
        """
        pf = float(power_fraction)
        return max(self.u_min, min(self.u_max, pf))


# ---------------------------------------------------------------------------
# Ground-source heat pump
# ---------------------------------------------------------------------------
