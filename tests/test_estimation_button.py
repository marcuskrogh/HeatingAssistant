"""Regression test: async_estimate_parameters_ml must pass dt as float.

Prior to the fix, coordinator.py passed dt=self._update_interval (int) to
KalmanMLEstimator, which expects dt: float.  This test ensures the float
conversion is in place and that pressing the button does not raise a TypeError.
"""

from __future__ import annotations

import sys
import os
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.mark.asyncio
async def test_estimate_parameters_ml_passes_float_dt():
    """KalmanMLEstimator must receive dt as float, not int."""
    captured: dict = {}

    class _FakeEstimator:
        def __init__(self, *, rooms, sources, dt):
            captured["dt"] = dt
            captured["dt_type"] = type(dt)

        def estimate(self, history):
            return {"success": False}

    # Minimal coordinator stand-in – only the attributes used by the method.
    coordinator = SimpleNamespace()
    coordinator._update_interval = 900  # int, as stored by the real coordinator
    coordinator.model = SimpleNamespace(rooms={})
    coordinator.heat_sources = []
    coordinator._history_buffer = deque()
    coordinator.hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(
            side_effect=lambda fn, *args: fn(*args)
        )
    )

    import custom_components.heating_assistant.coordinator as coord_mod

    bound = coord_mod.HeatingAssistantCoordinator.async_estimate_parameters_ml

    # The method does `from .parameter_estimator import KalmanMLEstimator`, so
    # we patch at the source module level.
    with patch(
        "custom_components.heating_assistant.parameter_estimator.KalmanMLEstimator",
        _FakeEstimator,
    ):
        await bound(coordinator, apply_params=False)

    assert "dt" in captured, "KalmanMLEstimator was never instantiated"
    assert isinstance(captured["dt"], float), (
        f"dt must be float, got {captured['dt_type'].__name__!r} ({captured['dt']!r})"
    )
    assert captured["dt"] == 900.0
