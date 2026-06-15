"""Unit tests for heat-source models."""

import math
import sys
import os
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.heat_sources import (
    ElectricHeater,
    HeatPump,
    _cop_at_temp,
    _soft_ceiling,
    _SOFT_CEIL_K,
    _T_SUPPLY_K,
)


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

    def test_min_power_clamps_to_zero(self):
        """When output would be below min_power, thermal_power returns 0."""
        hp = HeatPump(
            "hp1", "living_room", max_power=6100.0,
            cop_rated=3.5, cop_temp_ref=7.0, min_power=1000.0,
        )
        # At rated conditions, fraction 0.1 → 6100*0.1 = 610 W < 1000 W → 0
        power = hp.thermal_power(0.1, outdoor_temp=7.0)
        assert power == pytest.approx(0.0)

    def test_min_power_allows_above_threshold(self):
        """When output is at or above min_power, thermal_power returns normally."""
        hp = HeatPump(
            "hp1", "living_room", max_power=6100.0,
            cop_rated=3.5, cop_temp_ref=7.0, min_power=1000.0,
        )
        # At rated conditions, fraction 0.3 → 6100*0.3 = 1830 W > 1000 W → normal
        power = hp.thermal_power(0.3, outdoor_temp=7.0)
        assert power > 1000.0

    def test_min_power_zero_fraction_is_zero(self):
        """Fraction 0 should always return 0, regardless of min_power."""
        hp = HeatPump(
            "hp1", "living_room", max_power=6100.0,
            cop_rated=3.5, cop_temp_ref=7.0, min_power=1000.0,
        )
        assert hp.thermal_power(0.0, outdoor_temp=7.0) == pytest.approx(0.0)

    def test_min_power_default_is_zero(self):
        """Default min_power should be 0 (backward compatible)."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0)
        assert hp.min_power == 0.0

    # -- target_temperature (offset-based heat pump control) ---------------

    def test_target_temperature_full_power(self):
        """Heating-only HP: at fraction=1.0 the full max_temp_offset is added (linear)."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, max_temp_offset=5.0,
                      hvac_mode="heat")
        assert hp.target_temperature(1.0, 23.0) == pytest.approx(28.0)

    def test_target_temperature_zero(self):
        """At fraction=0.0 the target equals the internal temperature (no gap → idle)."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, max_temp_offset=5.0)
        assert hp.target_temperature(0.0, 23.0) == pytest.approx(23.0)

    def test_target_temperature_half_power(self):
        """Heating-only HP: at fraction=0.5 half the max_temp_offset is added (linear)."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, max_temp_offset=4.0,
                      hvac_mode="heat")
        assert hp.target_temperature(0.5, 20.0) == pytest.approx(22.0)

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

    def test_custom_max_temp_offset(self):
        """Custom max_temp_offset is stored correctly and scales the heating gap."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, max_temp_offset=8.0,
                      hvac_mode="heat")
        assert hp.max_temp_offset == 8.0
        assert hp.target_temperature(1.0, 20.0) == pytest.approx(28.0)

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
        """Negative fraction drives setpoint below internal temp (cooling).

        For can_cool HPs the gap is sigmoid-normalised, so the magnitude of
        the cooling setpoint offset is larger than the naive linear gap
        (fraction × max_temp_offset).  The exact value is derived from
        smooth_thermal_power / q_cool × max_temp_offset.
        """
        hp = HeatPump("hp1", "living_room", max_power=5000.0, hvac_mode="heat_cool",
                      max_temp_offset=5.0)
        outdoor_temp = 0.0
        fraction = -0.5
        result = hp.target_temperature(fraction, 25.0, outdoor_temp=outdoor_temp)
        # Setpoint must be below internal temp (cooling direction).
        assert result < 25.0
        # Sigmoid front-loads cooling: gap should be larger than linear (−2.5 °C).
        linear_gap = fraction * hp.max_temp_offset  # -2.5 °C
        assert (result - 25.0) < linear_gap  # more negative than linear
        # Verify consistency with smooth_thermal_power prediction.
        smooth_p = hp.smooth_thermal_power(fraction, outdoor_temp)
        expected_gap_frac = smooth_p / hp._q_cool_const  # negative
        expected = 25.0 + expected_gap_frac * hp.max_temp_offset
        assert result == pytest.approx(expected, rel=1e-6)

    def test_target_temperature_clamped_to_u_max(self):
        """Fraction above u_max is clamped (heat mode: u_max=1)."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, hvac_mode="heat",
                      max_temp_offset=5.0)
        assert hp.target_temperature(2.0, 20.0) == pytest.approx(25.0)

    def test_target_temperature_clamped_to_u_min_cool(self):
        """In cool mode positive fractions are clamped to u_max=0 → idle setpoint."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, hvac_mode="cool",
                      max_temp_offset=5.0)
        assert hp.target_temperature(0.5, 25.0) == pytest.approx(25.0)

    # -- sigmoid-normalised gap (can_cool HPs) ----------------------------

    def test_target_temperature_can_cool_heating_sigmoid_consistent(self):
        """For can_cool HPs, the heating gap is sigmoid-normalised so a
        linearly-modulating HP delivers exactly what smooth_thermal_power predicts."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000.0, hvac_mode="heat_cool",
            max_temp_offset=5.0, cop_rated=3.5, cop_temp_ref=7.0, cooling_cop=2.5,
        )
        outdoor_temp = -5.0  # below rated: q_heat < q_heat_max (soft-ceiling inactive)
        fraction = 0.5

        target = hp.target_temperature(fraction, 20.0, outdoor_temp=outdoor_temp)
        gap = target - 20.0

        # Expected: gap / max_temp_offset × q_heat == smooth_thermal_power(u)
        smooth_p = hp.smooth_thermal_power(fraction, outdoor_temp)
        cop_now = max(
            1.0,
            hp._cop_scale * _T_SUPPLY_K
            / max(_T_SUPPLY_K - outdoor_temp - 273.15, 1.0),
        )
        q_heat = _soft_ceiling(hp._q_heat_base * cop_now, hp._q_heat_max)
        delivered = (gap / hp.max_temp_offset) * q_heat
        assert delivered == pytest.approx(smooth_p, rel=1e-6)

        # Gap must be positive (heating) and GREATER than the naive linear gap
        # (sigmoid is front-loaded for heating at intermediate fractions).
        assert gap > 0.0
        assert gap > fraction * hp.max_temp_offset

    def test_target_temperature_can_cool_cooling_sigmoid_consistent(self):
        """For can_cool HPs, the cooling gap is sigmoid-normalised so a
        linearly-modulating HP delivers exactly what smooth_thermal_power predicts."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000.0, hvac_mode="heat_cool",
            max_temp_offset=5.0, cop_rated=3.5, cop_temp_ref=7.0, cooling_cop=2.5,
        )
        outdoor_temp = -5.0
        fraction = -0.5

        target = hp.target_temperature(fraction, 20.0, outdoor_temp=outdoor_temp)
        gap = target - 20.0  # negative

        # Expected: gap / max_temp_offset × q_cool == smooth_thermal_power(u) (negative)
        smooth_p = hp.smooth_thermal_power(fraction, outdoor_temp)
        delivered = (gap / hp.max_temp_offset) * hp._q_cool_const  # both negative
        assert delivered == pytest.approx(smooth_p, rel=1e-6)

        # Gap must be negative (cooling) with magnitude larger than naive linear.
        assert gap < 0.0
        assert gap < fraction * hp.max_temp_offset  # more negative than linear

    def test_target_temperature_can_cool_outdoor_below_min(self):
        """Below min_outdoor_temp, target equals internal (HP cannot operate)."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000.0, hvac_mode="heat_cool",
            max_temp_offset=5.0, min_outdoor_temp=-15.0,
        )
        # smooth_thermal_power returns 0.0 below min_outdoor_temp → idle setpoint.
        result = hp.target_temperature(1.0, 20.0, outdoor_temp=-20.0)
        assert result == pytest.approx(20.0)

    def test_target_temperature_heating_only_is_linear(self):
        """Heating-only HPs keep the linear offset regardless of outdoor_temp."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000.0, hvac_mode="heat",
            max_temp_offset=5.0,
        )
        # Linear: same at any outdoor temperature.
        for outdoor in (-10.0, 0.0, 7.0, 15.0):
            assert hp.target_temperature(0.5, 20.0, outdoor_temp=outdoor) == pytest.approx(22.5)

    def test_target_temperature_can_cool_vs_heating_only_at_full_fraction(self):
        """At fraction=1.0 both modes produce a gap close to max_temp_offset
        (sigmoid saturation is >99% at k=5 for the heating side)."""
        hp_heat = HeatPump(
            "hp1", "living_room", max_power=5000.0, hvac_mode="heat",
            max_temp_offset=5.0,
        )
        hp_hc = HeatPump(
            "hp2", "living_room", max_power=5000.0, hvac_mode="heat_cool",
            max_temp_offset=5.0,
        )
        outdoor_temp = -5.0
        linear_target = hp_heat.target_temperature(1.0, 20.0, outdoor_temp=outdoor_temp)
        sigmoid_target = hp_hc.target_temperature(1.0, 20.0, outdoor_temp=outdoor_temp)
        # Sigmoid target is within 2% of the full linear offset at saturation.
        assert sigmoid_target == pytest.approx(linear_target, rel=0.02)

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
        """``power_scale`` applies to cooling capacity as well as heating."""
        hp = HeatPump(
            "hp1", "living_room", max_power=5000.0,
            cooling_cop=2.5, power_scale=0.5,
        )
        cooling = hp.cooling_power(outdoor_temp=20.0)
        assert cooling == pytest.approx(-(5000.0 / 3.5) * 2.5 * 0.5, rel=1e-3)

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
        """fraction=-1.0 gives a setpoint close to internal − max_temp_offset.

        The sigmoid saturates at >98 % of q_cool at u=−1, so the gap is
        within ~2 % of the full max_temp_offset magnitude.
        """
        hp = HeatPump("hp1", "living_room", max_power=5000.0, max_temp_offset=5.0,
                      hvac_mode="heat_cool")
        result = hp.target_temperature(-1.0, 23.0)
        # Direction: must be below internal.
        assert result < 23.0
        # Magnitude: within 2 % of the full offset (sigmoid saturation).
        assert result == pytest.approx(18.0, rel=0.02)
        # Consistency: gap produces sigmoid-predicted cooling.
        gap = result - 23.0
        smooth_p = hp.smooth_thermal_power(-1.0, 0.0)
        expected_gap_frac = smooth_p / hp._q_cool_const
        assert gap == pytest.approx(expected_gap_frac * hp.max_temp_offset, rel=1e-6)

    def test_target_temperature_zero_is_internal(self):
        """fraction=0 → setpoint equals the internal temperature (idle, no gap)."""
        hp = HeatPump("hp1", "living_room", max_power=5000.0, max_temp_offset=5.0,
                      hvac_mode="heat_cool")
        assert hp.target_temperature(0.0, 23.0) == pytest.approx(23.0)

    def test_target_temperature_half_cooling(self):
        """fraction=-0.5 gives a cooling setpoint consistent with smooth_thermal_power.

        The sigmoid front-loads cooling, so the gap magnitude exceeds the
        naive linear value (0.5 × max_temp_offset).
        """
        hp = HeatPump("hp1", "living_room", max_power=5000.0, max_temp_offset=4.0,
                      hvac_mode="heat_cool")
        result = hp.target_temperature(-0.5, 24.0)
        # Must be below internal (cooling) and below the linear result (22.0).
        assert result < 24.0
        assert result < 22.0  # deeper than linear 0.5 × 4 = 2 °C gap
        # Verify sigmoid consistency.
        gap = result - 24.0
        smooth_p = hp.smooth_thermal_power(-0.5, 0.0)
        expected_gap_frac = smooth_p / hp._q_cool_const
        assert gap == pytest.approx(expected_gap_frac * hp.max_temp_offset, rel=1e-6)

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
