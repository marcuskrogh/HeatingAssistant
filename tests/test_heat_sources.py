"""Unit tests for heat-source models."""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.heat_sources import (
    ElectricHeater,
    HeatPump,
    _cop_at_temp,
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
