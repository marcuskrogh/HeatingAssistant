"""Tests for select_leading_window."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.heating_assistant.history_window import select_leading_window


def test_select_leading_window_empty_when_no_data():
    assert select_leading_window([], 1000.0, 3600.0) == []


def test_select_leading_window_respects_bounds():
    hist = [
        {"timestamp": 1000.0, "y": [20.0]},
        {"timestamp": 4600.0, "y": [20.5]},
        {"timestamp": 8200.0, "y": [21.0]},
    ]
    leading = select_leading_window(hist, 8200.0, 3600.0)
    assert len(leading) == 1
    assert leading[0]["timestamp"] == 4600.0
