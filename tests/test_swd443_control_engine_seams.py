"""SWD-443: lock ControlEngine public seams before splitting the facade.

Keep compute_actions, preview_tuning_forecast, and controller-build helpers
stable while extracting build / preview mixins.
"""
from __future__ import annotations

import pytest

from heatingassistant.engine.control_loop import (
    ControlEngine,
    _PREVIEW_TUNING_KEYS,
    _PREVIEW_WEIGHT_DEFAULTS,
    _build_heat_sources,
    _build_house_model,
    _snapshot_from_controller,
    reject_negative_p_gating_knobs,
)
from heatingassistant.engine.thermal_model import HouseModel


def _rooms_only() -> dict:
    return {
        "rooms": [
            {
                "name": "living",
                "setpoint": 21.0,
                "output_tags": ["living_heat"],
            }
        ]
    }


def test_control_engine_public_methods_exist() -> None:
    for name in (
        "update_config",
        "step",
        "compute_actions",
        "forecast_snapshot",
        "mpc_actions_by_tag",
        "nmpc_due",
        "nmpc_plan_idle",
        "mark_nmpc_busy",
        "solve_nmpc_blocking",
        "apply_nmpc_result",
        "preview_tuning_forecast",
        "room_power_meta",
        "_try_build_controller",
        "_build_controller_from_config",
        "_preview_matches_live",
    ):
        assert callable(getattr(ControlEngine, name)), name


def test_runtime_imports_control_engine_from_control_loop() -> None:
    from heatingassistant.app.runtime import ControlEngine as RuntimeEngine
    from heatingassistant.engine.control_loop import ControlEngine as LoopEngine

    assert RuntimeEngine is LoopEngine


def test_module_helpers_remain_on_control_loop() -> None:
    assert callable(_build_house_model)
    assert callable(_build_heat_sources)
    assert callable(_snapshot_from_controller)
    assert callable(reject_negative_p_gating_knobs)
    assert "tracking_weight" in _PREVIEW_TUNING_KEYS
    assert "comfort_offset" in _PREVIEW_TUNING_KEYS
    assert "p_deadband" not in _PREVIEW_TUNING_KEYS
    assert "tracking_weight" in _PREVIEW_WEIGHT_DEFAULTS


def test_control_engine_builds_house_from_rooms_config() -> None:
    engine = ControlEngine(_rooms_only())
    assert isinstance(engine.model, HouseModel)
    assert engine.mode == "proportional"
    assert list(engine.model.rooms) == ["living"]
    assert engine._controller is None


def test_compute_actions_proportional_fallback_without_controller() -> None:
    engine = ControlEngine(_rooms_only())
    actions = engine.compute_actions(
        room_temps={"living": 19.5},
        outdoor_temp=5.0,
        setpoints={"living": 21.0},
    )
    assert actions == {"living_heat": pytest.approx(0.5)}
    assert engine.mode == "proportional"


def test_step_delegates_to_compute_actions() -> None:
    engine = ControlEngine(_rooms_only())
    actions = engine.step(
        {
            "room_temps": {"living": 19.5},
            "outdoor_temp": 5.0,
            "setpoints": {"living": 21.0},
        }
    )
    assert actions == {"living_heat": pytest.approx(0.5)}


def test_preview_without_heat_sources_is_controller_unavailable() -> None:
    engine = ControlEngine(_rooms_only())
    result = engine.preview_tuning_forecast(
        {"tracking_weight": 2.0},
        room_temps={"living": 20.0},
        outdoor_temp=5.0,
        setpoints={"living": 21.0},
    )
    assert result == {"error": "controller_unavailable"}


def test_preview_matches_live_false_without_controller() -> None:
    engine = ControlEngine(_rooms_only())
    timing = engine._nmpc_timing()
    assert engine._preview_matches_live({}, timing) is False


def test_try_build_controller_returns_none_without_heat_sources() -> None:
    engine = ControlEngine(_rooms_only())
    assert engine._try_build_controller() is None
    assert engine.mode == "proportional"
    assert engine.fallback_reason == "no heat sources or rooms configured"


def test_nmpc_due_false_and_empty_forecast_without_controller() -> None:
    engine = ControlEngine(_rooms_only())
    assert engine.nmpc_due() is False
    snap = engine.forecast_snapshot()
    assert snap["mode"] == "proportional"
    assert snap["predictions"] == []
    assert snap["heating_schedule"] == []


def test_room_power_meta_empty_without_heat_sources() -> None:
    engine = ControlEngine(_rooms_only())
    assert engine.room_power_meta(0.0) == {}


def test_reject_negative_p_gating_knobs_raises() -> None:
    with pytest.raises(ValueError, match="p_deadband"):
        reject_negative_p_gating_knobs({"p_deadband": -1.0})
    with pytest.raises(ValueError, match="u_ref_gate"):
        reject_negative_p_gating_knobs({"u_ref_gate": -0.5})
    reject_negative_p_gating_knobs({"p_deadband": 0.0, "u_ref_gate": 0.0})
