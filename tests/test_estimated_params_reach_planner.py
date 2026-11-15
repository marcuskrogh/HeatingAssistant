"""Restored identified parameters must reach the estimator and the planner.

The saved room config still has the raw defaults (9 MJ/K, 0.05 K/W). The
active snapshot is the November fit. After restore, the house model and both
stochastic models (estimator ``_system`` and planner ``_control_system``)
must carry the same capacitance and drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.engine.living_room_fit import apply_living_room_fit
from heatingassistant.engine.parameter_lifecycle import (
    estimated_params_snapshot,
    restore_estimated_parameters,
    revert_parameters,
)

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "heatingassistant"
    / "engine"
    / "data"
    / "living_room_estimated_params.json"
)
FIT_UA_W_PER_K = 110.11959373108495


def _ua(capacity: float, drift: np.ndarray) -> float:
    return float(-np.asarray(drift, dtype=float)[0, 0] * capacity)


def test_planner_uses_restored_parameters() -> None:
    config = json.loads(FIXTURE.read_text(encoding="utf-8"))
    engine = ControlEngine(config)
    restore_estimated_parameters(
        engine.model,
        engine.heat_sources,
        estimated_params_snapshot(config),
    )
    controller = engine._controller
    assert controller is not None

    house_c = np.asarray(engine.model._C, dtype=float)
    house_f = np.asarray(engine.model._A, dtype=float) / house_c[:, None]
    house_ua = _ua(float(house_c[0]), house_f)
    assert house_ua == pytest.approx(FIT_UA_W_PER_K, rel=1e-6)
    assert float(house_c[0]) == pytest.approx(13_293_529.957768452, rel=1e-9)

    for label in ("_system", "_control_system"):
        sde = getattr(controller, label)
        capacity = np.asarray(sde._C_cap, dtype=float)
        drift = np.asarray(sde._F, dtype=float)
        assert np.allclose(capacity, house_c, rtol=1e-9)
        assert np.allclose(drift, house_f, rtol=1e-9)
        assert _ua(float(capacity[0]), drift) == pytest.approx(house_ua, rel=1e-9)


def test_refresh_keeps_estimator_state() -> None:
    config = json.loads(FIXTURE.read_text(encoding="utf-8"))
    engine = ControlEngine(config)
    controller = engine._controller
    assert controller is not None
    controller._ekf._x[0] = 19.25
    restore_estimated_parameters(
        engine.model,
        engine.heat_sources,
        estimated_params_snapshot(config),
    )
    assert float(controller._ekf._x[0]) == pytest.approx(19.25)
    assert float(np.asarray(controller._system._C_cap)[0]) == pytest.approx(
        13_293_529.957768452, rel=1e-9
    )


def test_september_set_is_replaced_and_can_be_restored() -> None:
    packaged = json.loads(FIXTURE.read_text(encoding="utf-8"))
    september = packaged["estimated_params"]["history"][0]
    options = {
        "rooms": packaged["rooms"],
        "heat_sources": packaged["heat_sources"],
        "nmpc_period": 900,
        "nmpc_fast_substeps": 1,
        "nmpc_horizon_h": 0.25,
        "estimated_params": {"active": september, "history": []},
    }
    assert apply_living_room_fit(options) is True
    active = options["estimated_params"]["active"]["rooms"]["Living Room"]
    assert active["thermal_mass"] == pytest.approx(13_293_529.957768452)
    assert options["estimated_params"]["history"][0]["rooms"]["Living Room"][
        "thermal_mass"
    ] == pytest.approx(276_700_000.0)

    engine = ControlEngine(options)
    revert_parameters(engine.model, engine.heat_sources, options, 0)
    assert float(engine.model.rooms["Living Room"].thermal_mass) == pytest.approx(
        276_700_000.0
    )
    assert float(np.asarray(engine._controller._system._C_cap)[0]) == pytest.approx(
        276_700_000.0
    )
