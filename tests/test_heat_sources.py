"""Unit tests for heat-source models."""

import math
import sys
import os
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.heat_sources import (
    ElectricHeater,
    GasHeater,
    GenericThermostat,
    HeatPump,
    HydronicRadiator,
    _cop_at_temp,
    _soft_ceiling,
    _SOFT_CEIL_K,
    GroundSourceHeatPump,
    PelletStove,
    ElectricStorageHeater,
)
from custom_components.heating_assistant.const import (
    SOURCE_TYPE_OIL_BOILER,
    SOURCE_TYPE_GROUND_SOURCE_HP,
    SOURCE_TYPE_PELLET_STOVE,
    SOURCE_TYPE_ELECTRIC_STORAGE,
    SOURCE_TYPE_HYDRONIC_FLOOR,
    DEFAULT_OIL_BOILER_EFFICIENCY,
    DEFAULT_GROUND_SOURCE_COP,
    DEFAULT_PELLET_EFFICIENCY,
    DEFAULT_PELLET_MIN_POWER_FRACTION,
    DEFAULT_STORAGE_CHARGE_POWER,
    DEFAULT_STORAGE_CAPACITY_KWH,
    DEFAULT_STORAGE_DISCHARGE_RATE,
    CONF_SOURCE_MIN_POWER_FRACTION,
    CONF_SOURCE_CHARGE_POWER,
    CONF_SOURCE_STORAGE_CAPACITY_KWH,
    CONF_SOURCE_PASSIVE_DISCHARGE_RATE,
    SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU,
    CONF_SOURCE_NAME,
    CONF_SOURCE_ROOM,
    CONF_SOURCE_MAX_POWER,
    CONF_SOURCE_EFFICIENCY,
    CONF_SOURCE_TYPE,
    CONF_SOURCE_COP_RATED,
    CONF_SOURCE_HVAC_MODE,
)
from custom_components.heating_assistant.coordinator import build_heat_sources


class TestElectricHeater:
    def test_full_power(self):
        heater = ElectricHeater("h1", "living_room", max_power=2000.0)
        assert heater.thermal_power(1.0) == pytest.approx(2000.0)

    def test_off(self):
        heater = ElectricHeater("h1", "living_room", max_power=2000.0)
        assert heater.thermal_power(0.0) == pytest.approx(0.0)

    def test_partial_power(self):
        heater = ElectricHeater("h1", "living_room", max_power=2000.0)
        assert heater.thermal_power(0.5) == pytest.approx(1000.0)

    def test_efficiency_scaling(self):
        heater = ElectricHeater("h1", "living_room", max_power=2000.0, efficiency=0.9)
        assert heater.thermal_power(1.0) == pytest.approx(1800.0)

    def test_invalid_efficiency(self):
        with pytest.raises(ValueError):
            ElectricHeater("h1", "living_room", max_power=2000.0, efficiency=1.5)

    def test_set_power_clamps_fraction(self):
        heater = ElectricHeater("h1", "living_room", max_power=2000.0)
        heater.set_power(1.5)  # above 1.0 – should clamp
        assert heater.current_power == pytest.approx(2000.0)
        heater.set_power(-0.5)  # below 0.0 – should clamp
        assert heater.current_power == pytest.approx(0.0)

    def test_outdoor_temp_ignored(self):
        heater = ElectricHeater("h1", "living_room", max_power=2000.0)
        p1 = heater.thermal_power(1.0, outdoor_temp=-20.0)
        p2 = heater.thermal_power(1.0, outdoor_temp=20.0)
        assert p1 == pytest.approx(p2)

    def test_target_temperature_uses_internal_temp_plus_offset(self):
        heater = ElectricHeater(
            "h1", "living_room", max_power=2000.0, max_temp_offset=4.0
        )
        assert heater.target_temperature(0.5, 20.0) == pytest.approx(22.0)

    def test_invalid_max_temp_offset(self):
        with pytest.raises(ValueError):
            ElectricHeater("h1", "living_room", max_power=2000.0, max_temp_offset=-1.0)

    def test_zero_max_temp_offset_gives_no_setpoint_offset(self):
        heater = ElectricHeater(
            "h1", "living_room", max_power=2000.0, max_temp_offset=0.0
        )
        assert heater.target_temperature(1.0, 20.0) == pytest.approx(20.0)


class TestHeatPump:
    def test_full_power_at_rated_conditions(self):
        hp = HeatPump("hp1", "living_room", max_power=5000.0, cop_rated=3.5, cop_temp_ref=7.0)
        # At rated outdoor temp the COP should be close to 3.5
        # thermal = (max_power / cop_rated) * 1.0 * cop(T_ref) ≈ max_power
        power = hp.thermal_power(1.0, outdoor_temp=7.0)
        # Allow reasonable tolerance
        assert power == pytest.approx(5000.0, rel=0.05)

    def test_off_is_zero(self):
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        assert hp.thermal_power(0.0, outdoor_temp=7.0) == pytest.approx(0.0)

    def test_cop_decreases_with_cold_outdoor(self):
        hp = HeatPump("hp1", "living_room", max_power=5000.0, cop_rated=3.5, cop_temp_ref=7.0)
        cop_warm = hp.cop(15.0)
        cop_cold = hp.cop(-10.0)
        assert cop_warm > cop_cold

    def test_cop_below_min_temp_is_zero(self):
        hp = HeatPump("hp1", "living_room", max_power=5000.0, min_outdoor_temp=-20.0)
        assert hp.cop(-25.0) == pytest.approx(0.0)

    def test_cop_never_below_one(self):
        hp = HeatPump("hp1", "living_room", max_power=5000.0, min_outdoor_temp=-50.0)
        assert hp.cop(-40.0) >= 1.0

    def test_cop_at_temp_helper(self):
        cop = _cop_at_temp(cop_rated=3.5, cop_temp_ref=7.0, outdoor_temp=7.0)
        assert cop == pytest.approx(3.5, rel=1e-3)

    def test_power_scales_with_fraction(self):
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        p_half = hp.thermal_power(0.5, outdoor_temp=7.0)
        p_full = hp.thermal_power(1.0, outdoor_temp=7.0)
        assert p_full == pytest.approx(2.0 * p_half, rel=1e-6)

    def test_min_power_no_longer_clamps_output(self):
        """The min-power output deadband was removed: sub-threshold outputs are
        returned as-is rather than forced to zero."""
        hp = HeatPump(
            "hp1", "living_room", max_power=6100.0,
            cop_rated=3.5, cop_temp_ref=7.0, min_power=1000.0,
        )
        # fraction 0.1 → ~610 W, previously < min_power 1000 W → 0; now passes through.
        power = hp.thermal_power(0.1, outdoor_temp=7.0)
        assert power > 0.0

    def test_zero_fraction_is_zero(self):
        """Fraction 0 returns 0 (zero control → zero power)."""
        hp = HeatPump(
            "hp1", "living_room", max_power=6100.0,
            cop_rated=3.5, cop_temp_ref=7.0,
        )
        assert hp.thermal_power(0.0, outdoor_temp=7.0) == pytest.approx(0.0)

    # -- target_temperature (offset-based heat pump control) ---------------

    def test_target_temperature_full_power(self):
        """At fraction=1.0 the offset saturates at delta_sat (logit → ∞, capped)."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, delta_sat=3.0,
                      hvac_mode="heat")
        assert hp.target_temperature(1.0, 23.0) == pytest.approx(26.0)

    def test_target_temperature_zero(self):
        """At fraction=0.0 the target equals the internal temperature (no gap → idle)."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        assert hp.target_temperature(0.0, 23.0) == pytest.approx(23.0)

    def test_target_temperature_half_power(self):
        """At fraction=0.5 the offset is delta_sat/2 (logit midpoint)."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, delta_sat=3.0,
                      hvac_mode="heat")
        assert hp.target_temperature(0.5, 20.0) == pytest.approx(21.5)

    def test_target_temperature_clamps_fraction(self):
        """Fractions outside [u_min, u_max] are clamped before computing setpoint."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, max_temp_offset=5.0,
                      hvac_mode="heat_cool")
        # Verify clamping: out-of-range fractions give the same result as the limit.
        assert hp.target_temperature(1.5, 20.0) == pytest.approx(
            hp.target_temperature(1.0, 20.0)
        )
        assert hp.target_temperature(-1.5, 20.0) == pytest.approx(
            hp.target_temperature(-1.0, 20.0)
        )

    def test_max_temp_offset_default(self):
        """Default max_temp_offset should be 5.0 °C."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        assert hp.max_temp_offset == 5.0

    def test_custom_delta_sat(self):
        """Custom delta_sat controls the saturation offset of the logit mapping."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, delta_sat=4.0,
                      hvac_mode="heat")
        assert hp.delta_sat == 4.0
        # At fraction=1.0 offset saturates at delta_sat=4.0
        assert hp.target_temperature(1.0, 20.0) == pytest.approx(24.0)
        # At fraction=0.5 offset is delta_sat/2 = 2.0
        assert hp.target_temperature(0.5, 20.0) == pytest.approx(22.0)

    def test_fraction_from_setpoint_offset_inverts_target_temperature(self):
        """Readback inverse must recover the commanded fraction."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, delta_sat=3.0,
                      hvac_mode="heat_cool")
        base = 22.0
        for fraction in (-1.0, -0.5, 0.0, 0.5, 1.0):
            target = hp.target_temperature(fraction, base)
            recovered = hp.fraction_from_setpoint_offset(target - base)
            assert recovered == pytest.approx(fraction, abs=0.02)

    def test_hvac_mode_default(self):
        """Default hvac_mode should be 'heat_cool'."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        assert hp.hvac_mode == "heat_cool"

    def test_hvac_mode_heat(self):
        hp = HeatPump("hp1", "living_room", max_power=5000.0, hvac_mode="heat")
        assert hp.hvac_mode == "heat"
        assert hp.u_min == 0.0
        assert hp.u_max == 1.0

    def test_hvac_mode_cool(self):
        hp = HeatPump("hp1", "living_room", max_power=5000.0, hvac_mode="cool")
        assert hp.hvac_mode == "cool"
        assert hp.u_min == -1.0
        assert hp.u_max == 0.0

    def test_hvac_mode_heat_cool(self):
        hp = HeatPump("hp1", "living_room", max_power=5000.0, hvac_mode="heat_cool")
        assert hp.u_min == -1.0
        assert hp.u_max == 1.0

    def test_target_temperature_negative_fraction(self):
        """Negative fraction drives setpoint below base_temp: logit midpoint at f=0.5."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, hvac_mode="heat_cool",
                      delta_sat=3.0)
        # fraction=-0.5, heat_cool (u_range=1): f=0.5, logit=0, offset=delta_sat/2=1.5
        result = hp.target_temperature(-0.5, 25.0)
        assert result == pytest.approx(23.5)

    def test_target_temperature_clamped_to_u_max(self):
        """Fraction above u_max is clamped (heat mode: u_max=1)."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, hvac_mode="heat",
                      delta_sat=3.0)
        # clamped to 1.0 → offset saturates at delta_sat=3
        assert hp.target_temperature(2.0, 20.0) == pytest.approx(23.0)

    def test_target_temperature_clamped_to_u_min_cool(self):
        """In cool mode positive fractions are clamped to u_max=0 → idle setpoint."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, hvac_mode="cool",
                      max_temp_offset=5.0)
        assert hp.target_temperature(0.5, 25.0) == pytest.approx(25.0)

    def test_cooling_power_default(self):
        """Cooling power = -(max_power / cop_rated) × cooling_cop × cooling_efficiency.

        With defaults cop_rated=3.5, cooling_cop=2.5, cooling_efficiency=1.0:
            cooling = -(5000 / 3.5) × 2.5 ≈ -3571.4 W
        """
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        cooling = hp.cooling_power(outdoor_temp=20.0)
        assert cooling == pytest.approx(-(5000.0 / 3.5) * 2.5, rel=1e-3)

    def test_cooling_power_custom_efficiency(self):
        """``cooling_efficiency`` modulates the cooling output (0–1)."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000.0, cooling_efficiency=0.8,
        )
        cooling = hp.cooling_power(outdoor_temp=20.0)
        # -(5000/3.5) × 2.5 × 0.8
        assert cooling == pytest.approx(-(5000.0 / 3.5) * 2.5 * 0.8, rel=1e-3)

    def test_cooling_power_custom_cop(self):
        """``cooling_cop`` (EER) sets the rated cooling capacity."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000.0,
            cop_rated=3.5, cooling_cop=3.0,
        )
        cooling = hp.cooling_power(outdoor_temp=20.0)
        # -(5000/3.5) × 3.0
        assert cooling == pytest.approx(-(5000.0 / 3.5) * 3.0, rel=1e-3)

    def test_cooling_capacity_lower_than_heating_max(self):
        """Cooling capacity must NOT inherit heating ``max_power`` — it should
        be derived from electrical input × cooling COP, which gives a
        smaller magnitude than the heating thermal max for typical EERs.
        """
        hp = HeatPump(
            "hp1", "living_room", max_power=6600.0,
            cop_rated=3.5, cooling_cop=2.5,
        )
        cooling = hp.cooling_power(outdoor_temp=20.0)
        # |cooling| should be strictly less than heating max_power
        assert abs(cooling) < hp.max_power
        # And should equal -(electric_max × cooling_cop)
        assert cooling == pytest.approx(-(6600.0 / 3.5) * 2.5, rel=1e-3)

    def test_cooling_power_respects_power_scale(self):
        """``power_scale`` applies to the MPC plant model, not display power."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000.0,
            cooling_cop=2.5, power_scale=0.5,
        )
        cooling = hp.cooling_power(outdoor_temp=20.0)
        assert cooling == pytest.approx(-(5000.0 / 3.5) * 2.5 * 0.5, rel=1e-3)
        assert hp.display_smooth_thermal_power(-1.0, 20.0) == pytest.approx(
            -hp.rated_cooling_power, rel=1e-3,
        )

    # -- delta_sat and dead-zone helpers -----------------------------------

    def test_delta_sat_default(self):
        """Default delta_sat should be 3.0 °C."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        assert hp.delta_sat == 3.0

    def test_logit_midpoint_at_half_fraction(self):
        """At fraction=0.5 the offset is always delta_sat/2 (logit midpoint)."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, delta_sat=4.0,
                      hvac_mode="heat")
        assert hp.target_temperature(0.5, 20.0) == pytest.approx(22.0)

    # -- can_cool property -------------------------------------------------

    def test_can_cool_true_by_default(self):
        """Default hvac_mode='heat_cool' and cooling_cop=2.5 → can_cool=True."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        assert hp.can_cool is True

    def test_can_cool_false_when_heat_only_mode(self):
        """hvac_mode='heat' → can_cool=False even with cooling_cop > 0."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, hvac_mode="heat")
        assert hp.can_cool is False

    def test_can_cool_true_when_cool_only_mode(self):
        """hvac_mode='cool' → can_cool=True."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, hvac_mode="cool")
        assert hp.can_cool is True

    def test_can_cool_false_when_cooling_cop_zero(self):
        """Setting cooling_cop=0 disables active cooling."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, cooling_cop=0.0)
        assert hp.can_cool is False

    def test_can_cool_electric_heater_false(self):
        """Electric heaters never support cooling."""
        heater = ElectricHeater("h1", "living_room", max_power=2000.0)
        assert heater.can_cool is False

    # -- target_temperature unified (heat_cool mode) -----------------------

    def test_target_temperature_full_cooling(self):
        """fraction=-1.0: offset saturates at delta_sat → setpoint = base − delta_sat."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, delta_sat=3.0,
                      hvac_mode="heat_cool")
        # offset capped at delta_sat=3 → 23 − 3 = 20
        assert hp.target_temperature(-1.0, 23.0) == pytest.approx(20.0)

    def test_target_temperature_zero_is_base(self):
        """fraction=0 → setpoint equals base_temp (HP idles, no offset)."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0,
                      hvac_mode="heat_cool")
        assert hp.target_temperature(0.0, 23.0) == pytest.approx(23.0)

    def test_target_temperature_half_cooling(self):
        """fraction=-0.5 (heat_cool): logit midpoint → offset = delta_sat/2."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, delta_sat=3.0,
                      hvac_mode="heat_cool")
        # f=0.5, logit=0, offset=1.5 → 24 − 1.5 = 22.5
        assert hp.target_temperature(-0.5, 24.0) == pytest.approx(22.5)

    # -- smooth_thermal_power ----------------------------------------------

    def test_smooth_thermal_power_zero_at_u_zero(self):
        """φ(0) must be exactly 0."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        assert hp.smooth_thermal_power(0.0, outdoor_temp=7.0) == pytest.approx(0.0, abs=1e-6)

    def test_smooth_thermal_power_positive_at_u_one(self):
        """φ(+1) should be close to the max heating capacity (≥ 98 %)."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        q_heat = hp.thermal_power(1.0, outdoor_temp=7.0)
        phi = hp.smooth_thermal_power(1.0, outdoor_temp=7.0)
        # Should be ≥ 98 % of max heating capacity
        assert phi >= 0.98 * q_heat

    def test_smooth_thermal_power_negative_at_u_minus_one(self):
        """φ(−1) should be close to the negative max cooling capacity (≥ 98 %)."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        q_cool = abs(hp.cooling_power(outdoor_temp=7.0))
        phi = hp.smooth_thermal_power(-1.0, outdoor_temp=7.0)
        # Should be ≥ 98 % of max cooling capacity (in magnitude)
        assert phi <= -0.98 * q_cool

    def test_smooth_thermal_power_monotone(self):
        """φ must be strictly increasing with u."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        us = np.linspace(-1.0, 1.0, 21)
        powers = [hp.smooth_thermal_power(u, outdoor_temp=7.0) for u in us]
        diffs = [powers[i + 1] - powers[i] for i in range(len(powers) - 1)]
        assert all(d > 0 for d in diffs), "smooth_thermal_power must be monotone increasing"

    def test_smooth_thermal_power_asymmetric(self):
        """Heating capacity (φ at +1) should exceed cooling capacity (|φ at -1|)
        when typical COP values make heating output larger than cooling output."""
        hp = HeatPump(
            "hp1", "living_room", max_power=6600.0,
            cop_rated=3.5, cooling_cop=2.5,
        )
        phi_heat = hp.smooth_thermal_power(1.0, outdoor_temp=7.0)
        phi_cool = abs(hp.smooth_thermal_power(-1.0, outdoor_temp=7.0))
        assert phi_heat > phi_cool, "Heating capacity should exceed cooling capacity"

    def test_smooth_thermal_power_fallback_when_no_cooling(self):
        """When cooling_cop=0 (no cooling), smooth_thermal_power falls back to
        the linear heating model and returns 0 for u ≤ 0."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, cooling_cop=0.0)
        assert hp.smooth_thermal_power(0.0, outdoor_temp=7.0) == pytest.approx(0.0, abs=1e-6)
        assert hp.smooth_thermal_power(-0.5, outdoor_temp=7.0) == pytest.approx(0.0)
        phi = hp.smooth_thermal_power(0.5, outdoor_temp=7.0)
        assert phi == pytest.approx(hp.thermal_power(0.5, outdoor_temp=7.0), rel=1e-6)

    # -- control_for_power_fraction (linear power for experiments) ----------

    def test_control_for_power_fraction_inverts_sigmoid(self):
        """A commanded power fraction maps to the control input that delivers
        exactly that fraction of capacity — so a step is linear in power."""
        hp = HeatPump("hp1", "living_room", max_power=6600.0,
                      cop_rated=3.5, cooling_cop=2.5)
        outdoor = 5.0
        k = 5.0
        q_heat = hp.thermal_power(1.0, outdoor)
        q_cool = abs(hp.cooling_power(outdoor))
        for pf, q in [(0.75, q_heat), (0.5, q_heat), (0.25, q_heat),
                      (-0.5, q_cool), (-0.25, q_cool)]:
            u = hp.control_for_power_fraction(pf, outdoor, k)
            delivered = hp.smooth_thermal_power(u, outdoor, k)
            assert delivered == pytest.approx(pf * q, rel=1e-6)

    def test_control_for_power_fraction_zero_maps_to_zero(self):
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        assert hp.control_for_power_fraction(0.0, 5.0, 5.0) == 0.0

    def test_control_for_power_fraction_linear_for_electric_heater(self):
        # No sigmoid → the power fraction is the control input directly.
        h = ElectricHeater("h1", "living_room", max_power=2000.0)
        assert h.control_for_power_fraction(0.75, 5.0) == pytest.approx(0.75)
        assert h.control_for_power_fraction(0.25, 5.0) == pytest.approx(0.25)

    # -- heating_efficiency -----------------------------------------------

    def test_heating_efficiency_default_is_one(self):
        """Default heating_efficiency should be 1.0 (no scaling)."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        assert hp.heating_efficiency == pytest.approx(1.0)

    def test_heating_efficiency_scales_thermal_power(self):
        """heating_efficiency=0.8 should reduce thermal_power by 20 %."""
        hp_base = HeatPump("hp1", "living_room", max_power=5000.0)
        hp_eff = HeatPump("hp1", "living_room", max_power=5000.0, heating_efficiency=0.8)
        p_base = hp_base.thermal_power(1.0, outdoor_temp=7.0)
        p_eff = hp_eff.thermal_power(1.0, outdoor_temp=7.0)
        assert p_eff == pytest.approx(0.8 * p_base, rel=1e-6)

    def test_heating_efficiency_does_not_affect_cooling_power(self):
        """heating_efficiency must NOT scale the cooling capacity."""
        hp_base = HeatPump("hp1", "living_room", max_power=5000.0)
        hp_eff = HeatPump("hp1", "living_room", max_power=5000.0, heating_efficiency=0.5)
        assert hp_base.cooling_power() == pytest.approx(hp_eff.cooling_power(), rel=1e-6)

    def test_heating_efficiency_propagates_to_smooth_thermal_power(self):
        """smooth_thermal_power at u=+1 should reflect heating_efficiency."""
        hp_base = HeatPump("hp1", "living_room", max_power=5000.0)
        hp_eff = HeatPump("hp1", "living_room", max_power=5000.0, heating_efficiency=0.7)
        phi_base = hp_base.smooth_thermal_power(1.0, outdoor_temp=7.0)
        phi_eff = hp_eff.smooth_thermal_power(1.0, outdoor_temp=7.0)
        # With reduced heating capacity the sigmoid at +1 saturates to a lower value
        assert phi_eff < phi_base

    def test_heating_efficiency_partial_fraction(self):
        """heating_efficiency scales linearly with setpoint_fraction."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, heating_efficiency=0.6)
        p_half = hp.thermal_power(0.5, outdoor_temp=7.0)
        p_full = hp.thermal_power(1.0, outdoor_temp=7.0)
        assert p_full == pytest.approx(2.0 * p_half, rel=1e-6)

    # -- max_power ceiling (regression: power must never exceed max_power) ---

    def test_thermal_power_never_exceeds_max_power_at_warm_outdoor_temp(self):
        """Regression: warm outdoor temps raise COP above rated value, which used
        to push thermal_power beyond max_power. The output must be capped."""
        hp = HeatPump(
            "hp1", "living_room", max_power=6000.0,
            cop_rated=3.5, cop_temp_ref=7.0,
        )
        # At 15 °C the COP exceeds 3.5, which previously caused ~8 400 W output.
        power = hp.thermal_power(1.0, outdoor_temp=15.0)
        assert power <= 6000.0, f"thermal_power exceeded max_power: {power} W"

    def test_thermal_power_never_exceeds_max_power_across_temp_range(self):
        """thermal_power must stay ≤ max_power for all outdoor temperatures."""
        max_power = 6000.0
        hp = HeatPump(
            "hp1", "living_room", max_power=max_power,
            cop_rated=3.5, cop_temp_ref=7.0,
        )
        for t_out in range(-10, 25):
            power = hp.thermal_power(1.0, outdoor_temp=float(t_out))
            assert power <= max_power + 1e-9, (
                f"thermal_power exceeded max_power at {t_out} °C: {power} W"
            )

    def test_thermal_power_never_exceeds_max_power_with_power_scale(self):
        """power_scale shifts both the base and the ceiling proportionally."""
        max_power = 6000.0
        scale = 1.3
        hp = HeatPump(
            "hp1", "living_room", max_power=max_power,
            cop_rated=3.5, cop_temp_ref=7.0, power_scale=scale,
        )
        ceiling = max_power * scale
        for t_out in range(-10, 25):
            power = hp.thermal_power(1.0, outdoor_temp=float(t_out))
            assert power <= ceiling + 1e-9, (
                f"thermal_power exceeded ceiling at {t_out} °C: {power} W"
            )

    def test_smooth_thermal_power_never_exceeds_max_power_at_warm_outdoor_temp(self):
        """smooth_thermal_power must also respect the max_power ceiling."""
        hp = HeatPump(
            "hp1", "living_room", max_power=6000.0,
            cop_rated=3.5, cop_temp_ref=7.0,
        )
        phi = hp.smooth_thermal_power(1.0, outdoor_temp=15.0)
        assert phi <= 6000.0 + 1e-9, (
            f"smooth_thermal_power exceeded max_power at 15 °C: {phi} W"
        )

    def test_smooth_thermal_power_never_exceeds_max_power_across_temp_range(self):
        """smooth_thermal_power must stay ≤ max_power for all outdoor temperatures."""
        max_power = 6000.0
        hp = HeatPump(
            "hp1", "living_room", max_power=max_power,
            cop_rated=3.5, cop_temp_ref=7.0,
        )
        for t_out in range(-10, 25):
            phi = hp.smooth_thermal_power(1.0, outdoor_temp=float(t_out))
            assert phi <= max_power + 1e-9, (
                f"smooth_thermal_power exceeded max_power at {t_out} °C: {phi} W"
            )

    def test_thermal_power_still_reaches_max_power_at_rated_conditions(self):
        """Full power at rated outdoor temp must be within 2 % of max_power.

        The soft ceiling has a known bias of ln(2)/k ≈ 1.4 % at the rated
        point (where q_cop == q_max), so we allow 2 % tolerance here.
        """
        hp = HeatPump(
            "hp1", "living_room", max_power=6000.0,
            cop_rated=3.5, cop_temp_ref=7.0,
        )
        power = hp.thermal_power(1.0, outdoor_temp=7.0)
        assert power == pytest.approx(6000.0, rel=0.02)

    def test_rated_heating_capacity_ignores_power_scale(self):
        """Plot bounds must reflect datasheet COP limits, not sysid power_scale."""
        hp = HeatPump(
            "hp1", "living_room", max_power=6000.0,
            cop_rated=3.5, cop_temp_ref=7.0, power_scale=0.6,
        )
        rated = hp.rated_heating_capacity(outdoor_temp=7.0)
        scaled = hp.thermal_power(1.0, outdoor_temp=7.0)
        displayed = hp.display_thermal_power(1.0, outdoor_temp=7.0)
        assert rated == pytest.approx(6000.0, rel=0.02)
        assert scaled == pytest.approx(0.6 * rated, rel=0.02)
        assert displayed == pytest.approx(rated, rel=0.02)

    def test_display_smooth_thermal_power_ignores_power_scale(self):
        hp = HeatPump(
            "hp1", "living_room", max_power=6000.0,
            cop_rated=3.5, cop_temp_ref=7.0, cooling_cop=2.5,
            hvac_mode="heat_cool", power_scale=0.5,
        )
        outdoor = 7.0
        assert hp.display_smooth_thermal_power(1.0, outdoor) == pytest.approx(
            hp.rated_heating_capacity(outdoor), rel=0.02,
        )
        assert hp.smooth_thermal_power(1.0, outdoor) == pytest.approx(
            0.5 * hp.rated_heating_capacity(outdoor), rel=0.02,
        )
        assert hp.display_smooth_thermal_power(-1.0, outdoor) == pytest.approx(
            -hp.rated_cooling_power, rel=0.02,
        )

    def test_rated_heating_capacity_follows_outdoor_cop(self):
        """Rated capacity must rise with outdoor temperature via COP."""
        hp = HeatPump(
            "hp1", "living_room", max_power=6000.0,
            cop_rated=3.5, cop_temp_ref=7.0,
        )
        cold = hp.rated_heating_capacity(outdoor_temp=-10.0)
        mild = hp.rated_heating_capacity(outdoor_temp=7.0)
        warm = hp.rated_heating_capacity(outdoor_temp=15.0)
        assert cold < mild <= warm
        assert mild == pytest.approx(6000.0, rel=0.02)

    def test_rated_heating_capacity_matches_thermal_power_at_unit_scale(self):
        hp = HeatPump(
            "hp1", "living_room", max_power=5000.0,
            cop_rated=3.5, cop_temp_ref=7.0, power_scale=1.0,
        )
        for t_out in (-5.0, 0.0, 7.0, 12.0):
            assert hp.rated_heating_capacity(t_out) == pytest.approx(
                hp.thermal_power(1.0, t_out), rel=1e-6,
            )

    # -- _soft_ceiling unit tests ------------------------------------------

    def test_soft_ceiling_below_cap_is_identity(self):
        """Well below the cap the output tracks the input almost exactly."""
        cap = 6000.0
        x = cap * 0.1
        assert _soft_ceiling(x, cap) == pytest.approx(x, rel=1e-4)

    def test_soft_ceiling_above_cap_saturates(self):
        """Well above the cap the output is essentially equal to cap."""
        cap = 6000.0
        x = cap * 3.0
        assert _soft_ceiling(x, cap) == pytest.approx(cap, rel=1e-4)

    def test_soft_ceiling_never_exceeds_cap(self):
        """Output must be ≤ cap for all inputs."""
        cap = 6000.0
        for x in [0.0, 1000.0, 5000.0, 6000.0, 8000.0, 20000.0]:
            assert _soft_ceiling(x, cap) <= cap + 1e-9

    def test_soft_ceiling_derivative_is_sigmoid(self):
        """Derivative at any point must equal sigmoid(k·(x/cap − 1))."""
        cap = 6000.0
        for x in [1000.0, 4000.0, 6000.0, 8000.0, 12000.0]:
            dx = 0.01
            numerical = (_soft_ceiling(x + dx, cap) - _soft_ceiling(x - dx, cap)) / (2 * dx)
            # df/dx = sigmoid(k·(1 − x/cap));  sign opposite to the soft-max convention
            analytical = 1.0 / (1.0 + math.exp(_SOFT_CEIL_K * (x / cap - 1.0)))
            assert numerical == pytest.approx(analytical, rel=1e-3)

    def test_soft_ceiling_zero_cap_returns_zero(self):
        """A zero cap should return 0 without division errors."""
        assert _soft_ceiling(5000.0, 0.0) == pytest.approx(0.0)


class TestGenericThermostat:
    def test_full_power(self):
        ht = GenericThermostat("ht1", "hall", max_power=1500.0)
        assert ht.thermal_power(1.0) == pytest.approx(1500.0)

    def test_off(self):
        ht = GenericThermostat("ht1", "hall", max_power=1500.0)
        assert ht.thermal_power(0.0) == pytest.approx(0.0)

    def test_partial_power(self):
        ht = GenericThermostat("ht1", "hall", max_power=1500.0)
        assert ht.thermal_power(0.5) == pytest.approx(750.0)

    def test_outdoor_temp_ignored(self):
        ht = GenericThermostat("ht1", "hall", max_power=1500.0)
        assert ht.thermal_power(1.0, outdoor_temp=-20.0) == pytest.approx(
            ht.thermal_power(1.0, outdoor_temp=20.0)
        )

    def test_elec_per_unit_heat_equals_max_power(self):
        ht = GenericThermostat("ht1", "hall", max_power=1500.0)
        assert ht.elec_per_unit_heat == pytest.approx(1500.0)

    def test_elec_per_unit_heat_ignores_power_scale(self):
        """Electrical draw is based on configured capacity, not sysid scale."""
        ht = GenericThermostat("ht1", "hall", max_power=1500.0, power_scale=0.8)
        assert ht.elec_per_unit_heat == pytest.approx(1500.0)

    def test_power_scale_update_recomputes_gain(self):
        ht = GenericThermostat("ht1", "hall", max_power=1500.0)
        ht.power_scale = 0.75
        assert ht.thermal_power(1.0) == pytest.approx(1125.0)

    def test_set_power_clamps_fraction(self):
        ht = GenericThermostat("ht1", "hall", max_power=1500.0)
        ht.set_power(2.0)
        assert ht.current_power == pytest.approx(1500.0)
        ht.set_power(-1.0)
        assert ht.current_power == pytest.approx(0.0)

    def test_target_temperature(self):
        ht = GenericThermostat("ht1", "hall", max_power=1500.0, max_temp_offset=4.0)
        assert ht.target_temperature(0.5, 20.0) == pytest.approx(22.0)

    def test_invalid_max_temp_offset(self):
        with pytest.raises(ValueError):
            GenericThermostat("ht1", "hall", max_power=1500.0, max_temp_offset=-1.0)

    def test_cannot_cool(self):
        ht = GenericThermostat("ht1", "hall", max_power=1500.0)
        assert ht.can_cool is False

    def test_control_bounds(self):
        ht = GenericThermostat("ht1", "hall", max_power=1500.0)
        assert ht.u_min == pytest.approx(0.0)
        assert ht.u_max == pytest.approx(1.0)


class TestGasHeater:
    def test_full_power_default_efficiency(self):
        gh = GasHeater("gh1", "kitchen", max_power=3000.0)
        assert gh.thermal_power(1.0) == pytest.approx(3000.0 * 0.90)

    def test_off(self):
        gh = GasHeater("gh1", "kitchen", max_power=3000.0)
        assert gh.thermal_power(0.0) == pytest.approx(0.0)

    def test_partial_power(self):
        gh = GasHeater("gh1", "kitchen", max_power=3000.0, efficiency=0.85)
        assert gh.thermal_power(0.5) == pytest.approx(3000.0 * 0.85 * 0.5)

    def test_condensing_efficiency(self):
        gh = GasHeater("gh1", "kitchen", max_power=3000.0, efficiency=0.95)
        assert gh.thermal_power(1.0) == pytest.approx(2850.0)

    def test_elec_per_unit_heat_is_zero(self):
        gh = GasHeater("gh1", "kitchen", max_power=3000.0)
        assert gh.elec_per_unit_heat == pytest.approx(0.0)

    def test_outdoor_temp_ignored(self):
        gh = GasHeater("gh1", "kitchen", max_power=3000.0)
        assert gh.thermal_power(1.0, outdoor_temp=-15.0) == pytest.approx(
            gh.thermal_power(1.0, outdoor_temp=20.0)
        )

    def test_power_scale_update_recomputes_gain(self):
        gh = GasHeater("gh1", "kitchen", max_power=3000.0, efficiency=0.90)
        gh.power_scale = 0.80
        assert gh.thermal_power(1.0) == pytest.approx(3000.0 * 0.90 * 0.80)

    def test_set_power_clamps_fraction(self):
        gh = GasHeater("gh1", "kitchen", max_power=3000.0)
        gh.set_power(1.5)
        assert gh.current_power == pytest.approx(3000.0 * 0.90)

    def test_target_temperature(self):
        gh = GasHeater("gh1", "kitchen", max_power=3000.0, max_temp_offset=6.0)
        assert gh.target_temperature(1.0, 18.0) == pytest.approx(24.0)

    def test_invalid_efficiency_too_high(self):
        with pytest.raises(ValueError):
            GasHeater("gh1", "kitchen", max_power=3000.0, efficiency=1.1)

    def test_invalid_efficiency_zero(self):
        with pytest.raises(ValueError):
            GasHeater("gh1", "kitchen", max_power=3000.0, efficiency=0.0)

    def test_invalid_max_temp_offset(self):
        with pytest.raises(ValueError):
            GasHeater("gh1", "kitchen", max_power=3000.0, max_temp_offset=-2.0)

    def test_cannot_cool(self):
        gh = GasHeater("gh1", "kitchen", max_power=3000.0)
        assert gh.can_cool is False

    def test_control_bounds(self):
        gh = GasHeater("gh1", "kitchen", max_power=3000.0)
        assert gh.u_min == pytest.approx(0.0)
        assert gh.u_max == pytest.approx(1.0)


class TestHydronicRadiator:
    def test_full_power(self):
        hr = HydronicRadiator("hr1", "living_room", max_power=2000.0)
        assert hr.thermal_power(1.0) == pytest.approx(2000.0)

    def test_off(self):
        hr = HydronicRadiator("hr1", "living_room", max_power=2000.0)
        assert hr.thermal_power(0.0) == pytest.approx(0.0)

    def test_partial_power(self):
        hr = HydronicRadiator("hr1", "living_room", max_power=2000.0)
        assert hr.thermal_power(0.5) == pytest.approx(1000.0)

    def test_outdoor_temp_ignored(self):
        # District heating supply temp is controlled by the network, not outdoor temp
        hr = HydronicRadiator("hr1", "living_room", max_power=2000.0)
        assert hr.thermal_power(1.0, outdoor_temp=-10.0) == pytest.approx(
            hr.thermal_power(1.0, outdoor_temp=20.0)
        )

    def test_elec_per_unit_heat_is_zero(self):
        hr = HydronicRadiator("hr1", "living_room", max_power=2000.0)
        assert hr.elec_per_unit_heat == pytest.approx(0.0)

    def test_power_scale_update_recomputes_gain(self):
        hr = HydronicRadiator("hr1", "living_room", max_power=2000.0)
        hr.power_scale = 0.85
        assert hr.thermal_power(1.0) == pytest.approx(1700.0)

    def test_set_power_clamps_fraction(self):
        hr = HydronicRadiator("hr1", "living_room", max_power=2000.0)
        hr.set_power(1.5)
        assert hr.current_power == pytest.approx(2000.0)
        hr.set_power(-0.5)
        assert hr.current_power == pytest.approx(0.0)

    def test_target_temperature(self):
        hr = HydronicRadiator("hr1", "living_room", max_power=2000.0, max_temp_offset=4.0)
        assert hr.target_temperature(0.5, 20.0) == pytest.approx(22.0)

    def test_default_emitter_time_constant(self):
        hr = HydronicRadiator("hr1", "living_room", max_power=2000.0)
        assert hr.emitter_time_constant == pytest.approx(600.0)

    def test_custom_emitter_time_constant(self):
        # Hydronic UFH users should set a longer tau
        hr = HydronicRadiator("hr1", "living_room", max_power=2000.0, emitter_time_constant=3600.0)
        assert hr.emitter_time_constant == pytest.approx(3600.0)

    def test_invalid_max_temp_offset(self):
        with pytest.raises(ValueError):
            HydronicRadiator("hr1", "living_room", max_power=2000.0, max_temp_offset=-1.0)

    def test_cannot_cool(self):
        hr = HydronicRadiator("hr1", "living_room", max_power=2000.0)
        assert hr.can_cool is False

    def test_control_bounds(self):
        hr = HydronicRadiator("hr1", "living_room", max_power=2000.0)
        assert hr.u_min == pytest.approx(0.0)
        assert hr.u_max == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# WP2 new heat source types
# ---------------------------------------------------------------------------

class TestGroundSourceHeatPump:
    def test_full_power_default_cop(self):
        """Full setpoint delivers close to max_power (soft ceiling ≤ 2 % below)."""
        gshp = GroundSourceHeatPump("g", "r", max_power=8000.0)
        assert gshp.thermal_power(1.0) == pytest.approx(8000.0, rel=0.02)

    def test_off(self):
        gshp = GroundSourceHeatPump("g", "r", max_power=8000.0)
        assert gshp.thermal_power(0.0) == pytest.approx(0.0)

    def test_partial_power_linear(self):
        """At fraction=0.5 the output is the soft-ceiling of (max_power * 0.5)."""
        gshp = GroundSourceHeatPump("g", "r", max_power=8000.0)
        # At 0.5 fraction: raw = 4000, cap = 8000 → soft_ceiling(4000, 8000) ≈ 4000
        # The key assertion is that output at 0.5 is well below output at 1.0
        # (not exactly half, because soft_ceiling(raw, cap) ≠ 0.5 * soft_ceiling(2*raw, cap)).
        # We verify it is in the correct ballpark and positive.
        p05 = gshp.thermal_power(0.5)
        p10 = gshp.thermal_power(1.0)
        assert p05 > 0.0
        assert p05 < p10
        # At 0.5 fraction the raw value is half max_power; since 4000 ≪ 8000
        # the soft ceiling barely bites, so the output should be ≈ 4000 W.
        assert p05 == pytest.approx(4000.0, rel=0.02)

    def test_cop_does_not_vary_with_outdoor_temp(self):
        """Key differentiator: flat COP — no Carnot outdoor-temperature correction."""
        gshp = GroundSourceHeatPump("g", "r", max_power=8000.0)
        p_cold = gshp.thermal_power(1.0, outdoor_temp=-15.0)
        p_warm = gshp.thermal_power(1.0, outdoor_temp=15.0)
        assert p_cold == pytest.approx(p_warm)

    def test_rated_heating_capacity_flat(self):
        """Rated capacity must not change with outdoor temperature (unlike ASHP)."""
        gshp = GroundSourceHeatPump("g", "r", max_power=8000.0)
        assert gshp.rated_heating_capacity(-15.0) == pytest.approx(
            gshp.rated_heating_capacity(15.0)
        )

    def test_can_cool_default(self):
        """Default hvac_mode='heat_cool' → can_cool is True."""
        gshp = GroundSourceHeatPump("g", "r", max_power=8000.0)
        assert gshp.can_cool is True

    def test_heat_only_mode(self):
        """hvac_mode='heat' → can_cool False, u bounds [0, 1]."""
        gshp = GroundSourceHeatPump("g", "r", max_power=8000.0, hvac_mode="heat")
        assert gshp.can_cool is False
        assert gshp.u_min == pytest.approx(0.0)
        assert gshp.u_max == pytest.approx(1.0)

    def test_cooling_power(self):
        """Cooling power = -(max_power / cop_rated) * cooling_cop * cooling_efficiency."""
        gshp = GroundSourceHeatPump(
            "g", "r", max_power=8000.0, cop_rated=4.5,
            cooling_cop=2.5, cooling_efficiency=1.0,
        )
        expected = -(8000.0 / 4.5) * 2.5
        assert gshp.cooling_power() == pytest.approx(expected, rel=1e-3)

    def test_elec_per_unit_heat(self):
        """Electrical draw = (max_power / cop_rated) * heating_efficiency."""
        gshp = GroundSourceHeatPump(
            "g", "r", max_power=8000.0, cop_rated=4.5, heating_efficiency=1.0,
        )
        assert gshp.elec_per_unit_heat == pytest.approx(8000.0 / 4.5 * 1.0, rel=1e-3)

    def test_target_temperature_zero(self):
        """At fraction=0.0 the setpoint equals the base temperature."""
        gshp = GroundSourceHeatPump("g", "r", max_power=8000.0)
        assert gshp.target_temperature(0.0, 22.0) == pytest.approx(22.0)

    def test_target_temperature_full_heat(self):
        """At fraction=1.0 the offset saturates at delta_sat (default 3.0 °C)."""
        gshp = GroundSourceHeatPump("g", "r", max_power=8000.0, delta_sat=3.0)
        result = gshp.target_temperature(1.0, 20.0)
        assert result == pytest.approx(20.0 + 3.0)

    def test_default_emitter_tau(self):
        """Class default emitter_time_constant is 0.0 (coordinator applies typology default)."""
        gshp = GroundSourceHeatPump("g", "r", max_power=8000.0)
        assert gshp.emitter_time_constant == pytest.approx(0.0)

    def test_power_scale_update(self):
        """Assigning power_scale re-derives gain; output at fraction=0.5 changes proportionally."""
        gshp = GroundSourceHeatPump("g", "r", max_power=8000.0)
        gshp.power_scale = 0.8
        # At fraction=0.5 with scale=0.8: raw = 8000*1.0*0.8*0.5 = 3200, cap = 8000*1.0*0.8 = 6400
        # soft_ceiling(3200, 6400) ≈ 3200 (3200 ≪ 6400, minimal soft-ceiling bias)
        assert gshp.thermal_power(0.5) == pytest.approx(3200.0, rel=0.02)


class TestPelletStove:
    def test_full_power(self):
        ps = PelletStove("p", "r", max_power=10000.0)
        assert ps.thermal_power(1.0) == pytest.approx(10000.0 * 0.88)

    def test_off(self):
        ps = PelletStove("p", "r", max_power=10000.0)
        assert ps.thermal_power(0.0) == pytest.approx(0.0)

    def test_below_min_fraction_is_zero(self):
        """Below the 30 % floor the stove is off."""
        ps = PelletStove("p", "r", max_power=10000.0)
        assert ps.thermal_power(0.29) == pytest.approx(0.0)

    def test_at_min_fraction(self):
        """At exactly the floor fraction the output is linear (not zero)."""
        ps = PelletStove("p", "r", max_power=10000.0)
        assert ps.thermal_power(0.30) == pytest.approx(10000.0 * 0.88 * 0.30)

    def test_above_min_fraction_linear(self):
        ps = PelletStove("p", "r", max_power=10000.0)
        assert ps.thermal_power(0.5) == pytest.approx(10000.0 * 0.88 * 0.5)

    def test_custom_min_fraction(self):
        ps = PelletStove("p", "r", max_power=10000.0, min_power_fraction=0.20)
        assert ps.thermal_power(0.19) == pytest.approx(0.0)
        assert ps.thermal_power(0.20) == pytest.approx(10000.0 * 0.88 * 0.20)

    def test_efficiency_scales_output(self):
        ps = PelletStove("p", "r", max_power=10000.0, efficiency=0.92)
        assert ps.thermal_power(1.0) == pytest.approx(10000.0 * 0.92)

    def test_invalid_efficiency(self):
        with pytest.raises(ValueError):
            PelletStove("p", "r", max_power=10000.0, efficiency=1.1)

    def test_invalid_min_fraction_too_high(self):
        with pytest.raises(ValueError):
            PelletStove("p", "r", max_power=10000.0, min_power_fraction=1.0)

    def test_elec_per_unit_heat_is_zero(self):
        """Pellet stove burns biomass — zero electrical draw."""
        ps = PelletStove("p", "r", max_power=10000.0)
        assert ps.elec_per_unit_heat == pytest.approx(0.0)

    def test_cannot_cool(self):
        ps = PelletStove("p", "r", max_power=10000.0)
        assert ps.can_cool is False

    def test_default_emitter_tau(self):
        """Class default τ_em is 2400.0 s (40 min burn-bed lag)."""
        ps = PelletStove("p", "r", max_power=10000.0)
        assert ps.emitter_time_constant == pytest.approx(2400.0)

    def test_rated_heating_capacity(self):
        ps = PelletStove("p", "r", max_power=10000.0, efficiency=0.88)
        assert ps.rated_heating_capacity() == pytest.approx(10000.0 * 0.88)

    def test_outdoor_temp_ignored(self):
        ps = PelletStove("p", "r", max_power=10000.0)
        assert ps.thermal_power(1.0, -20.0) == pytest.approx(ps.thermal_power(1.0, 20.0))

    def test_power_scale_update(self):
        ps = PelletStove("p", "r", max_power=10000.0, efficiency=0.88)
        ps.power_scale = 0.9
        assert ps.thermal_power(1.0) == pytest.approx(10000.0 * 0.88 * 0.9)


class TestElectricStorageHeater:
    def test_boost_power_thermal(self):
        esh = ElectricStorageHeater("e", "r", max_power=500.0)
        assert esh.thermal_power(1.0) == pytest.approx(500.0)

    def test_boost_power_off(self):
        esh = ElectricStorageHeater("e", "r", max_power=500.0)
        assert esh.thermal_power(0.0) == pytest.approx(0.0)

    def test_boost_partial(self):
        esh = ElectricStorageHeater("e", "r", max_power=500.0)
        assert esh.thermal_power(0.5) == pytest.approx(250.0)

    def test_passive_thermal_output_full_charge(self):
        """At full charge: stored_kwh * 3_600_000 * discharge_rate."""
        esh = ElectricStorageHeater(
            "e", "r", max_power=0.0,
            storage_capacity_kwh=8.0,
            passive_discharge_rate=DEFAULT_STORAGE_DISCHARGE_RATE,
        )
        expected = 8.0 * 3_600_000.0 * DEFAULT_STORAGE_DISCHARGE_RATE
        assert esh.passive_thermal_output(8.0) == pytest.approx(expected)

    def test_passive_thermal_output_zero_stored(self):
        esh = ElectricStorageHeater("e", "r", max_power=0.0)
        assert esh.passive_thermal_output(0.0) == pytest.approx(0.0)

    def test_passive_thermal_output_proportional(self):
        """Doubling stored energy doubles passive output."""
        esh = ElectricStorageHeater("e", "r", max_power=0.0)
        p1 = esh.passive_thermal_output(4.0)
        p2 = esh.passive_thermal_output(8.0)
        assert p2 == pytest.approx(2.0 * p1)

    def test_elec_per_unit_heat_is_charge_power(self):
        esh = ElectricStorageHeater("e", "r", max_power=500.0, charge_power=1500.0)
        assert esh.elec_per_unit_heat == pytest.approx(1500.0)

    def test_cannot_cool(self):
        esh = ElectricStorageHeater("e", "r", max_power=500.0)
        assert esh.can_cool is False

    def test_default_charge_power(self):
        esh = ElectricStorageHeater("e", "r", max_power=500.0)
        assert esh.charge_power == pytest.approx(DEFAULT_STORAGE_CHARGE_POWER)

    def test_default_storage_capacity(self):
        esh = ElectricStorageHeater("e", "r", max_power=500.0)
        assert esh.storage_capacity_kwh == pytest.approx(DEFAULT_STORAGE_CAPACITY_KWH)

    def test_emitter_tau_default(self):
        """Boost coil is instant — class default τ_em is 0.0."""
        esh = ElectricStorageHeater("e", "r", max_power=500.0)
        assert esh.emitter_time_constant == pytest.approx(0.0)

    def test_power_scale_affects_boost(self):
        esh = ElectricStorageHeater("e", "r", max_power=500.0)
        esh.power_scale = 0.5
        assert esh.thermal_power(1.0) == pytest.approx(500.0 * 0.5)


class TestOilBoilerAlias:
    def _make_cfg(self, extra=None):
        cfg = {
            CONF_SOURCE_TYPE: SOURCE_TYPE_OIL_BOILER,
            CONF_SOURCE_NAME: "ob",
            CONF_SOURCE_ROOM: "r",
            CONF_SOURCE_MAX_POWER: 20000.0,
        }
        if extra:
            cfg.update(extra)
        return [cfg]

    def test_factory_returns_gas_heater(self):
        sources = build_heat_sources(self._make_cfg())
        assert isinstance(sources[0], GasHeater)

    def test_default_efficiency(self):
        sources = build_heat_sources(self._make_cfg())
        assert sources[0].efficiency == pytest.approx(DEFAULT_OIL_BOILER_EFFICIENCY)

    def test_thermal_power(self):
        sources = build_heat_sources(self._make_cfg())
        assert sources[0].thermal_power(1.0) == pytest.approx(20000.0 * DEFAULT_OIL_BOILER_EFFICIENCY)

    def test_elec_per_unit_heat_zero(self):
        """Oil combustion draws no electricity."""
        sources = build_heat_sources(self._make_cfg())
        assert sources[0].elec_per_unit_heat == pytest.approx(0.0)

    def test_custom_efficiency_override(self):
        sources = build_heat_sources(self._make_cfg(extra={CONF_SOURCE_EFFICIENCY: 0.93}))
        assert sources[0].efficiency == pytest.approx(0.93)


class TestCoordinatorFactoryNewTypes:
    def _cfg(self, src_type, extra=None):
        cfg = {
            CONF_SOURCE_TYPE: src_type,
            CONF_SOURCE_NAME: "s",
            CONF_SOURCE_ROOM: "r",
            CONF_SOURCE_MAX_POWER: 5000.0,
        }
        if extra:
            cfg.update(extra)
        return [cfg]

    def test_ground_source_hp_factory(self):
        sources = build_heat_sources(self._cfg(SOURCE_TYPE_GROUND_SOURCE_HP))
        assert isinstance(sources[0], GroundSourceHeatPump)

    def test_ground_source_hp_tau(self):
        sources = build_heat_sources(self._cfg(SOURCE_TYPE_GROUND_SOURCE_HP))
        assert sources[0].emitter_time_constant == pytest.approx(
            SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU[SOURCE_TYPE_GROUND_SOURCE_HP]
        )

    def test_pellet_stove_factory(self):
        sources = build_heat_sources(self._cfg(SOURCE_TYPE_PELLET_STOVE))
        assert isinstance(sources[0], PelletStove)

    def test_pellet_stove_tau(self):
        sources = build_heat_sources(self._cfg(SOURCE_TYPE_PELLET_STOVE))
        assert sources[0].emitter_time_constant == pytest.approx(
            SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU[SOURCE_TYPE_PELLET_STOVE]
        )

    def test_electric_storage_factory(self):
        sources = build_heat_sources(self._cfg(SOURCE_TYPE_ELECTRIC_STORAGE))
        assert isinstance(sources[0], ElectricStorageHeater)

    def test_electric_storage_tau(self):
        sources = build_heat_sources(self._cfg(SOURCE_TYPE_ELECTRIC_STORAGE))
        assert sources[0].emitter_time_constant == pytest.approx(
            SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU[SOURCE_TYPE_ELECTRIC_STORAGE]
        )

    def test_hydronic_floor_tau_fixed(self):
        """Regression: WP2 fixes hydronic_floor_heating τ_em from 3600 → 7200 s."""
        sources = build_heat_sources(self._cfg(SOURCE_TYPE_HYDRONIC_FLOOR))
        assert sources[0].emitter_time_constant == pytest.approx(7200.0)

    def test_ground_source_hp_custom_cop(self):
        sources = build_heat_sources(
            self._cfg(SOURCE_TYPE_GROUND_SOURCE_HP, extra={CONF_SOURCE_COP_RATED: 5.0})
        )
        assert sources[0].cop_rated == pytest.approx(5.0)

    def test_pellet_stove_custom_min_fraction(self):
        sources = build_heat_sources(
            self._cfg(SOURCE_TYPE_PELLET_STOVE, extra={CONF_SOURCE_MIN_POWER_FRACTION: 0.25})
        )
        assert sources[0].min_power_fraction == pytest.approx(0.25)

    def test_electric_storage_custom_charge_power(self):
        sources = build_heat_sources(
            self._cfg(SOURCE_TYPE_ELECTRIC_STORAGE, extra={CONF_SOURCE_CHARGE_POWER: 2000.0})
        )
        assert sources[0].charge_power == pytest.approx(2000.0)


class TestHydronicFloorHeatingTauRegression:
    def test_tau_default_is_7200(self):
        """Regression guard: WP2 fixed τ_em from 3600 s to 7200 s."""
        assert SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU[SOURCE_TYPE_HYDRONIC_FLOOR] == pytest.approx(7200.0)

    def test_factory_applies_7200_default(self):
        """Factory with no explicit emitter_time_constant yields 7200 s."""
        cfg = [
            {
                CONF_SOURCE_TYPE: SOURCE_TYPE_HYDRONIC_FLOOR,
                CONF_SOURCE_NAME: "ufh",
                CONF_SOURCE_ROOM: "r",
                CONF_SOURCE_MAX_POWER: 3000.0,
            }
        ]
        sources = build_heat_sources(cfg)
        assert sources[0].emitter_time_constant == pytest.approx(7200.0)

    def test_factory_respects_override(self):
        """An explicit emitter_time_constant overrides the typology default."""
        from custom_components.heating_assistant.const import CONF_SOURCE_EMITTER_TIME_CONSTANT
        cfg = [
            {
                CONF_SOURCE_TYPE: SOURCE_TYPE_HYDRONIC_FLOOR,
                CONF_SOURCE_NAME: "ufh",
                CONF_SOURCE_ROOM: "r",
                CONF_SOURCE_MAX_POWER: 3000.0,
                CONF_SOURCE_EMITTER_TIME_CONSTANT: 10800.0,
            }
        ]
        sources = build_heat_sources(cfg)
        assert sources[0].emitter_time_constant == pytest.approx(10800.0)
