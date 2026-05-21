"""
Ground-temperature model tests.

The slab/UFH features are not present in 1R1C.  This file retains the
ground-temperature model tests that are independent of the thermal model.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.const import (
    DEFAULT_GROUND_TEMP_AMPLITUDE,
    DEFAULT_GROUND_TEMP_MEAN,
)
from custom_components.heating_assistant.ground_temp import ground_temperature


# ---------------------------------------------------------------------------
# Ground-temperature model
# ---------------------------------------------------------------------------


def test_ground_temperature_peak_matches_peak_day() -> None:
    """The cosine peaks at ``mean + amplitude`` on the configured
    ``peak_day``."""
    # 2024 is a leap year — day 220 is 7 Aug.
    peak_dt = datetime(2024, 8, 7, 12, 0, tzinfo=timezone.utc)
    T_peak = ground_temperature(peak_dt)
    assert T_peak == pytest.approx(
        DEFAULT_GROUND_TEMP_MEAN + DEFAULT_GROUND_TEMP_AMPLITUDE, abs=0.5,
    )


def test_ground_temperature_trough_six_months_off_peak() -> None:
    """The cosine reaches its minimum half a year away from the peak —
    around day 37 (peak_day − 365/2)."""
    trough_dt = datetime(2024, 2, 6, 12, 0, tzinfo=timezone.utc)  # day 37
    T_trough = ground_temperature(trough_dt)
    assert T_trough == pytest.approx(
        DEFAULT_GROUND_TEMP_MEAN - DEFAULT_GROUND_TEMP_AMPLITUDE, abs=0.5,
    )
