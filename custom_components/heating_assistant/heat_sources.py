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

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


_T_SUPPLY_K: float = 308.15  # Assumed supply temperature 35 °C in Kelvin

# Dimensionless sharpness for the smooth power ceiling (higher → sharper).
# At x = cap the output is cap · (1 − ln 2 / k); with k = 50 the bias is ≈ 1.4 %.
# The derivative is sigmoid(k · (x/cap − 1)), which the L-BFGS-B solver
# can exploit without any non-differentiable kink.
_SOFT_CEIL_K: float = 50.0


def _soft_ceiling(x: float, cap: float) -> float:
    """Smooth C∞ ceiling: differentiable approximation to min(x, cap).

    Uses a normalised softplus so the sharpness is dimensionless:

        f(x) = cap − (cap / k) · log(1 + exp(k · (1 − x / cap)))
        f′(x) = sigmoid(k · (x / cap − 1))

    The function is always ≤ cap, approaches x for x ≪ cap, and approaches
    cap for x ≫ cap.  Numerically stable for all finite x.
    """
    if cap <= 0.0:
        return 0.0
    t = _SOFT_CEIL_K * (1.0 - x / cap)
    # Numerically stable softplus: avoids exp overflow for large positive t.
    sp = t + math.log1p(math.exp(-t)) if t > 0.0 else math.log1p(math.exp(t))
    return cap - (cap / _SOFT_CEIL_K) * sp


class HeatSource(ABC):
    """Abstract base class for a controllable heat source."""

    def __init__(
        self,
        name: str,
        room: str,
        max_power: float,
        heater_entity: Optional[str] = None,
        power_scale: float = 1.0,
        emitter_time_constant: float = 0.0,
    ) -> None:
        self.name = name
        self.room = room                     # name of the room this source heats
        self.max_power = max_power           # W (maximum *thermal* output)
        self.heater_entity = heater_entity   # optional HA entity_id
        # Multiplicative correction on actual delivered thermal power.
        # Identified jointly with thermal-mass and resistance parameters.
        # Stored behind a property so assigning a new value re-derives the
        # cached power-dependent gains (``_gain`` / ``_q_heat_base`` …) that the
        # SDE and MPC read; a plain attribute left those caches stale, so
        # identified or manually-entered heater scales had no effect on the
        # model.  The raw private attribute is set here (not via the property)
        # because subclass attributes the recompute hook needs are not assigned
        # until after ``super().__init__`` returns.
        self._power_scale = float(power_scale)
        # Phase 1 B2 (pragmatic emitter filter): first-order time
        # constant [s] for the commanded-fraction → delivered-fraction
        # filter.  Captures TRV / valve / water-loop / metal-mass lag
        # without requiring supply-temperature telemetry.
        #
        # * 0 (default for electric resistive heaters) → no filter,
        #   commanded fraction reaches the thermal node instantly.
        # * 60 s (typical heat pump / fan-coil indoor unit) → fast
        #   filter capturing fan-spin-up and refrigerant-loop response.
        # * 600 s (~10 min, typical hydronic radiator) → slow filter
        #   capturing water-loop and metal-mass dynamics.
        #
        # Values > 0 add one state variable per source to the EKF /
        # OCP, so the controller can anticipate the lag rather than
        # treating commanded power as instantaneous.
        self.emitter_time_constant: float = float(emitter_time_constant)
        self._current_power: float = 0.0    # W

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def current_power(self) -> float:
        """Current thermal output power [W]."""
        return self._current_power

    @property
    def power_scale(self) -> float:
        """Multiplicative correction on delivered thermal power (1.0 = rated)."""
        return self._power_scale

    @power_scale.setter
    def power_scale(self, value: float) -> None:
        self._power_scale = float(value)
        # Re-derive any cached power-dependent gains so a runtime scale change
        # (Apply Parameters, identified scales, or a simulation preview) takes
        # effect.  Subclasses override ``_recompute_power_scaled_gains``.
        self._recompute_power_scaled_gains()

    def _recompute_power_scaled_gains(self) -> None:
        """Recompute cached gains that depend on ``power_scale``.

        No-op in the base class; subclasses that precompute thermal-output
        constants from ``power_scale`` at construction override this so those
        caches stay consistent when ``power_scale`` is reassigned.
        """

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

    def control_for_power_fraction(
        self, power_fraction: float, outdoor_temp: float = 0.0, k_sigmoid: float = 5.0,
    ) -> float:
        """Return the control input ``u`` that delivers ``power_fraction`` of the
        source's max heat (``>= 0``) / cool (``< 0``) capacity.

        For sources whose input→power map is linear (electric heaters, heat-only
        units) the control input *is* the power fraction, so this is the identity
        (clamped to the actuation range).  Sources with a nonlinear map (a
        cooling-capable heat pump's smooth sigmoid) override this to invert that
        map, so a commanded power fraction lands linearly on delivered power —
        used by identification experiments so a step of ``step_pct`` really
        delivers ``step_pct`` of capacity.
        """
        return max(self.u_min, min(self.u_max, float(power_fraction)))

    @property
    def can_cool(self) -> bool:
        """Returns True if this source can actively remove heat from the room."""
        return False

    @property
    def u_min(self) -> float:
        """Lower bound on the control input."""
        return 0.0

    @property
    def u_max(self) -> float:
        """Upper bound on the control input."""
        return 1.0

    @property
    def elec_per_unit_heat(self) -> float:
        """Electrical power [W] drawn per unit of positive control input (heating).

        For heat pumps the COP cancels out: P_elec = thermal / COP is independent
        of outdoor temperature and equals the rated electrical draw at full load.
        """
        return 0.0

    @property
    def elec_per_unit_cool(self) -> float:
        """Electrical power [W] drawn per unit of |negative| control input (cooling).

        Zero for sources that cannot cool.
        """
        return 0.0

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

    @property
    def elec_per_unit_heat(self) -> float:
        return self.max_power * self.efficiency * self.power_scale

    def target_temperature(self, setpoint_fraction: float, internal_temp: float) -> float:
        """Target setpoint = internal_temp + fraction × max_temp_offset."""
        setpoint_fraction = max(0.0, min(1.0, setpoint_fraction))
        return internal_temp + setpoint_fraction * self.max_temp_offset


# ---------------------------------------------------------------------------
# Generic thermostat
# ---------------------------------------------------------------------------

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

    @property
    def elec_per_unit_heat(self) -> float:
        return self.max_power * self.power_scale

    def target_temperature(self, setpoint_fraction: float, internal_temp: float) -> float:
        setpoint_fraction = max(0.0, min(1.0, setpoint_fraction))
        return internal_temp + setpoint_fraction * self.max_temp_offset


# ---------------------------------------------------------------------------
# Gas heater
# ---------------------------------------------------------------------------

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

    @property
    def elec_per_unit_heat(self) -> float:
        return 0.0

    def target_temperature(self, setpoint_fraction: float, internal_temp: float) -> float:
        setpoint_fraction = max(0.0, min(1.0, setpoint_fraction))
        return internal_temp + setpoint_fraction * self.max_temp_offset


# ---------------------------------------------------------------------------
# Hydronic radiator  (district heating / hot-water radiator)
# ---------------------------------------------------------------------------

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

    @property
    def elec_per_unit_heat(self) -> float:
        return 0.0

    def target_temperature(self, setpoint_fraction: float, internal_temp: float) -> float:
        setpoint_fraction = max(0.0, min(1.0, setpoint_fraction))
        return internal_temp + setpoint_fraction * self.max_temp_offset


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

    @property
    def can_cool(self) -> bool:
        """Returns True when the configured hvac_mode includes cooling."""
        return self.hvac_mode in ("cool", "heat_cool") and self.cooling_cop > 0

    @property
    def elec_per_unit_heat(self) -> float:
        # COP cancels: P_elec = thermal(u) / COP = _q_heat_base * u / COP * COP = _q_heat_base * u
        # Recompute from current power_scale to stay consistent after power_scale updates.
        electric_max = self.max_power / self.cop_rated if self.cop_rated > 0 else 0.0
        return electric_max * self.heating_efficiency * self.power_scale

    @property
    def elec_per_unit_cool(self) -> float:
        if not self.can_cool:
            return 0.0
        # Q_cool = electric_max * cooling_cop * cooling_efficiency * power_scale
        # P_elec_cool = Q_cool / cooling_cop = electric_max * cooling_efficiency * power_scale
        electric_max = self.max_power / self.cop_rated if self.cop_rated > 0 else 0.0
        return electric_max * self.cooling_efficiency * self.power_scale

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
        Thermal power output [W].

        The heat pump's *rated* electrical input power is ``max_power / cop_rated``.
        The actual thermal output depends on the actual COP at the current
        outdoor temperature.
        """
        if outdoor_temp < self.min_outdoor_temp:
            return 0.0
        cop_now = max(1.0, self._cop_scale * _T_SUPPLY_K / max(_T_SUPPLY_K - outdoor_temp - 273.15, 1.0))
        power = _soft_ceiling(self._q_heat_base * cop_now, self._q_heat_max) * setpoint_fraction
        return power

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
        the runtime-scaled ``_q_cool_const``.  Use this for stable plot bounds;
        use ``-cooling_power()`` for the gauge where the identified scale matters.
        """
        if not self.can_cool or self.cop_rated <= 0:
            return 0.0
        return self._electric_max * self.cooling_cop * self.cooling_efficiency

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

    def smooth_thermal_power(
        self, u: float, outdoor_temp: float, k_base: float = 5.0,
    ) -> float:
        """
        Smooth, asymmetric sigmoid mapping of the MPC control input
        *u* ∈ [−1, 1] to instantaneous thermal power [W].

        The function is the unique shifted logistic that satisfies:

        * φ(0) = 0  (zero control → zero power)
        * φ(u) → +Q_heat  as u → +1  (positive control → heating)
        * φ(u) → −Q_cool  as u → −1  (negative control → cooling)

        Concretely::

            Q_heat = thermal_power(1, T_out)       # max heating capacity [W]
            Q_cool = |cooling_power(T_out)|        # max cooling capacity [W]
            offset = ln(Q_cool / Q_heat)           # ensures φ(0) = 0
            k      = k_base + max(0, ln(Q_heat / Q_cool))
                    # adaptive sharpness: guarantees ≥ σ(k_base) saturation
                    # (≈ 99 % for k_base = 5) at u = ±1
            φ(u)   = (Q_heat + Q_cool) · σ(k · u + offset) − Q_cool

        where σ is the standard logistic sigmoid.

        The function is smooth (C∞) everywhere and has continuous gradients
        that the L-BFGS-B optimiser can exploit without needing to handle
        a non-differentiable kink at u = 0.

        Parameters
        ----------
        u : float
            MPC control input in [−1, 1]; positive → heating, negative → cooling.
        outdoor_temp : float
            Current outdoor temperature [°C] for COP calculation.
        k_base : float, optional
            Base sharpness parameter.  The effective sharpness is automatically
            increased to compensate for asymmetric heating/cooling capacities so
            that both extremes saturate to ≥ σ(k_base) of their capacity within
            u ∈ [−1, 1].  Default is 5.0.

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
