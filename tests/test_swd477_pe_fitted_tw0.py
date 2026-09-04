"""SWD-477: reuse fitted Tw0 for the current parameter set on PE simulate."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from heatingassistant.app.sysid_services import (
    _resolve_simulation_t_wall,
    handle_get_pe_inputs,
)
from heatingassistant.engine.heat_sources import ElectricHeater
from heatingassistant.engine.parameter_lifecycle import (
    apply_estimated_parameters,
    lookup_fitted_t_wall_initial,
    pe_fit_record,
)
from heatingassistant.engine.thermal_model import HouseModel, Room


pytestmark = pytest.mark.unit

STATIC_JS = Path(__file__).resolve().parents[1] / "heatingassistant" / "app" / "static" / "js"


def _heater(room: str = "living_room") -> ElectricHeater:
    return ElectricHeater("h", room, 2000.0)


def _history(n: int = 12) -> list[dict]:
    t0 = 1_800_000_000.0
    return [
        {
            "y": [21.0 + 0.1 * i],
            "u": [0.2],
            "d_outdoor": 8.0,
            "d_solar": {"living_room": 40.0},
            "timestamp": t0 + 900.0 * i,
        }
        for i in range(n)
    ]


def _fit(tw0: float = 16.4, dataset_id: str = "ds-1") -> dict:
    return pe_fit_record(
        {"living_room": {"thermal_mass": 3.0e5, "r_external": 0.05}},
        estimated_internal_gains={"living_room": 40.0},
        estimated_solar_scales={"living_room": 1.0},
        estimated_envelope_splits={
            "living_room": {"c_air_fraction": 0.05, "r_aw_fraction": 0.05}
        },
        dataset_ids=[dataset_id],
        estimated_t_wall_initial={"living_room": tw0},
    )


def test_pe_fit_record_maps_per_dataset_tw0():
    record = pe_fit_record(
        {"living_room": {"thermal_mass": 1.0, "r_external": 0.1}},
        dataset_ids=["a", "b"],
        estimated_t_wall_initial={"living_room": 18.0},
        estimated_t_wall_per_dataset=[
            {"living_room": 15.0},
            {"living_room": 17.5},
        ],
    )
    assert record["t_wall_initial_by_dataset"]["a"]["living_room"] == pytest.approx(15.0)
    assert record["t_wall_initial_by_dataset"]["b"]["living_room"] == pytest.approx(17.5)


def test_lookup_fitted_t_wall_requires_same_dataset_and_params():
    fit = _fit(16.4, "ds-1")
    found = lookup_fitted_t_wall_initial(fit, dataset_ids=["ds-1"])
    assert found is not None
    assert found["living_room"] == pytest.approx(16.4)
    assert lookup_fitted_t_wall_initial(fit, dataset_ids=["other"]) is None
    assert lookup_fitted_t_wall_initial(
        fit,
        dataset_ids=["ds-1"],
        room_params={"living_room": {"thermal_mass": 9.0e5}},
    ) is None


def test_apply_estimated_parameters_persists_tw0_and_dataset_ids():
    room = Room("living_room", 2.0e5, 0.04)
    model = HouseModel([room])
    options: dict = {}
    apply_estimated_parameters(
        model,
        [_heater()],
        options,
        {"living_room": {"thermal_mass": 3.1e5, "r_external": 0.06}},
        dataset_ids=["ds-heat"],
        estimated_t_wall_initial={"living_room": 15.2},
    )
    active = options["estimated_params"]["active"]
    assert active["dataset_ids"] == ["ds-heat"]
    assert active["t_wall_initial"]["living_room"] == pytest.approx(15.2)
    assert active["t_wall_initial_by_dataset"]["ds-heat"]["living_room"] == pytest.approx(15.2)


def test_resolve_simulation_t_wall_reuses_last_pe_fit(monkeypatch):
    room = Room("living_room", 3.0e5, 0.05)
    runtime = SimpleNamespace(
        options={},
        _last_pe_fit=_fit(16.4, "ds-1"),
        control_engine=SimpleNamespace(model=HouseModel([room])),
    )

    def _boom(*_a, **_k):
        raise AssertionError("window fit must not run when the PE IC matches")

    monkeypatch.setattr(
        "heatingassistant.app.sysid_services.optimal_t_wall_for_window",
        _boom,
    )
    val, source = _resolve_simulation_t_wall(
        _history(),
        runtime,
        [_heater()],
        ["living_room"],
        900.0,
        {"living_room": {"thermal_mass": 3.0e5, "r_external": 0.05}},
        {"dataset_id": "ds-1"},
    )
    assert source == "parameter_set"
    assert val["living_room"] == pytest.approx(16.4)


def test_resolve_simulation_t_wall_window_fits_unrelated_dataset(monkeypatch):
    room = Room("living_room", 3.0e5, 0.05)
    runtime = SimpleNamespace(
        options={},
        _last_pe_fit=_fit(16.4, "ds-1"),
        control_engine=SimpleNamespace(model=HouseModel([room])),
    )
    monkeypatch.setattr(
        "heatingassistant.app.sysid_services.optimal_t_wall_for_window",
        lambda *_a, **_k: {"living_room": 12.0},
    )
    val, source = _resolve_simulation_t_wall(
        _history(),
        runtime,
        [_heater()],
        ["living_room"],
        900.0,
        {},
        {"dataset_id": "ds-other"},
    )
    assert source == "window_fit"
    assert val["living_room"] == pytest.approx(12.0)


def test_get_pe_inputs_returns_fitted_tw0_for_matching_dataset(monkeypatch):
    hist = _history()
    runtime = SimpleNamespace(
        options={"rooms": [{"name": "living_room"}]},
        _history_buffer=list(hist),
        id_history_store=None,
        _last_pe_fit=_fit(16.4, "ds-1"),
        _last_identified_heater_scales={},
        control_engine=SimpleNamespace(
            model=SimpleNamespace(room_names=["living_room"], rooms={"living_room": Room("living_room", 3.0e5, 0.05)}),
            heat_sources=[_heater()],
        ),
    )

    async def _fake_resolve(_runtime, **kwargs):
        assert kwargs.get("dataset_id") == "ds-1"
        return list(hist)

    monkeypatch.setattr("heatingassistant.app.sysid_services.resolve_history", _fake_resolve)
    result = asyncio.run(
        handle_get_pe_inputs(runtime, {"room_name": "living_room", "dataset_id": "ds-1"})
    )
    assert result["t_wall_initial"] == pytest.approx(16.4)
    assert result["t_wall_initial_source"] == "parameter_set"


def test_pe_ui_shows_tw0_source():
    detail = (STATIC_JS / "identification" / "sysid-detail.js").read_text(encoding="utf-8")
    markup = (STATIC_JS / "identification" / "sysid-detail-markup.js").read_text(encoding="utf-8")
    assert "t_wall_initial_source" in detail
    assert "fittedTw0FromActiveHistory" in detail
    assert "formMatchesParamFingerprint" in detail
    assert "param_fingerprint" in detail
    extras = detail.split("function renderIdentifiedExtras", 1)[1].split("function populateModelFromSysid", 1)[0]
    assert "applySimulatedTw0" not in extras
    assert "applyTw0" in detail
    assert "param-t-wall-initial-hint" in markup
    assert "current parameter set" in markup


def test_restore_from_applied_snapshot_without_last_pe_fit(monkeypatch):
    """Loading a stored dataset used to estimate the current θ restores snapshot Tw0."""
    room = Room("living_room", 2.0e5, 0.04)
    model = HouseModel([room])
    options: dict = {}
    apply_estimated_parameters(
        model,
        [_heater()],
        options,
        {"living_room": {"thermal_mass": 3.1e5, "r_external": 0.06}},
        dataset_ids=["ds-heat"],
        estimated_t_wall_initial={"living_room": 15.2},
    )
    runtime = SimpleNamespace(
        options=options,
        _last_pe_fit=None,
        control_engine=SimpleNamespace(model=HouseModel([Room("living_room", 3.1e5, 0.06)])),
    )

    def _boom(*_a, **_k):
        raise AssertionError("window fit must not run when the applied snapshot matches")

    monkeypatch.setattr(
        "heatingassistant.app.sysid_services.optimal_t_wall_for_window",
        _boom,
    )
    val, source = _resolve_simulation_t_wall(
        _history(),
        runtime,
        [_heater()],
        ["living_room"],
        900.0,
        {"living_room": {"thermal_mass": 3.1e5, "r_external": 0.06}},
        {"dataset_id": "ds-heat"},
    )
    assert source == "parameter_set"
    assert val["living_room"] == pytest.approx(15.2)


def test_window_pe_fit_reused_when_timestamps_match(monkeypatch):
    room = Room("living_room", 3.0e5, 0.05)
    fit = pe_fit_record(
        {"living_room": {"thermal_mass": 3.0e5, "r_external": 0.05}},
        estimated_t_wall_initial={"living_room": 11.0},
        window_start=100.0,
        window_end=200.0,
    )
    runtime = SimpleNamespace(
        options={},
        _last_pe_fit=fit,
        control_engine=SimpleNamespace(model=HouseModel([room])),
    )
    monkeypatch.setattr(
        "heatingassistant.app.sysid_services.optimal_t_wall_for_window",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("window opt must not run for matching PE window")
        ),
    )
    val, source = _resolve_simulation_t_wall(
        _history(),
        runtime,
        [_heater()],
        ["living_room"],
        900.0,
        {"living_room": {"thermal_mass": 3.0e5, "r_external": 0.05}},
        {"window_start": 100.0, "window_end": 200.0},
    )
    assert source == "parameter_set"
    assert val["living_room"] == pytest.approx(11.0)


def test_loading_one_dataset_from_joint_pe_uses_that_block(monkeypatch):
    """Joint PE stores one Tw0 per dataset; loading one of those datasets restores it."""
    room = Room("living_room", 3.0e5, 0.05)
    runtime = SimpleNamespace(
        options={},
        _last_pe_fit=pe_fit_record(
            {"living_room": {"thermal_mass": 3.0e5, "r_external": 0.05}},
            dataset_ids=["ds1", "ds2"],
            estimated_t_wall_initial={"living_room": 9.0},
            estimated_t_wall_per_dataset=[
                {"living_room": 15.0},
                {"living_room": 17.5},
            ],
        ),
        control_engine=SimpleNamespace(model=HouseModel([room])),
    )

    def _boom(*_a, **_k):
        raise AssertionError("window fit must not run for a dataset used in the PE")

    monkeypatch.setattr(
        "heatingassistant.app.sysid_services.optimal_t_wall_for_window",
        _boom,
    )
    val, source = _resolve_simulation_t_wall(
        _history(),
        runtime,
        [_heater()],
        ["living_room"],
        900.0,
        {},
        {"dataset_id": "ds1"},
    )
    assert source == "parameter_set"
    assert val["living_room"] == pytest.approx(15.0)


def test_empty_window_opt_falls_back_to_midpoint_not_air(monkeypatch):
    room = Room("living_room", 3.0e5, 0.05)
    runtime = SimpleNamespace(
        options={},
        _last_pe_fit=None,
        control_engine=SimpleNamespace(model=HouseModel([room])),
    )
    monkeypatch.setattr(
        "heatingassistant.app.sysid_services.optimal_t_wall_for_window",
        lambda *_a, **_k: {},
    )
    val, source = _resolve_simulation_t_wall(
        _history(),
        runtime,
        [_heater()],
        ["living_room"],
        900.0,
        {},
        {"dataset_id": "unrelated"},
    )
    assert source == "window_fit"
    assert val["living_room"] == pytest.approx(14.5)
    assert val["living_room"] != pytest.approx(21.0)
