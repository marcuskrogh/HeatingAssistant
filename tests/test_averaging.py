from __future__ import annotations

import pytest

from heatingassistant.fusion.averaging import average_numeric_tags


pytestmark = pytest.mark.unit


def test_average_numeric_tags_uses_good_numeric_values_only() -> None:
    result = average_numeric_tags(
        {
            "living_1": 20.0,
            "living_2": None,
            "living_3": 22.0,
            "living_4": 99.0,
            "living_5": "21.0",  # type: ignore[dict-item]
        },
        {
            "living_1": "GOOD",
            "living_2": "GOOD",
            "living_3": "GOOD",
            "living_4": "BAD",
            "living_5": "GOOD",
        },
    )

    assert result == pytest.approx(21.0)


def test_average_numeric_tags_three_sensors_mean() -> None:
    result = average_numeric_tags(
        {"living_1": 19.5, "living_2": 20.0, "living_3": 20.5},
        {"living_1": "GOOD", "living_2": "GOOD", "living_3": "GOOD"},
    )

    assert result == pytest.approx(20.0)


def test_average_numeric_tags_returns_none_when_no_good_values() -> None:
    result = average_numeric_tags(
        {"living_1": None, "living_2": 22.0, "living_3": 20.0},
        {"living_1": "GOOD", "living_2": "BAD", "living_3": "UNCERTAIN"},
    )

    assert result is None
