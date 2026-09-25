"""SWD-576: archive finished PE results for later apply."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from heatingassistant.app import sysid_services
from heatingassistant.engine.model_diagnostics import (
    air_temperatures_from_history,
    r_squared_from_rmse,
)
from heatingassistant.engine.parameter_lifecycle import (
    PE_FIT_RESULTS_KEY,
    archive_pe_fit_result,
    delete_pe_fit_result,
    pe_fit_should_archive,
)
from tests.test_swd453_pe_background_job import _ok_result, _runtime, wait_pe_job


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
DETAIL = (
    ROOT / "heatingassistant" / "app" / "static" / "js" / "identification"
    / "sysid-detail.js"
)


def _storeable_result(**overrides) -> dict:
    payload = _ok_result()
    payload.update(
        {
            "exit_label": "Converged (cost reduction)",
            "rmse_c_best": 0.42,
            "cancelled": False,
            "timed_out": False,
        }
    )
    payload.update(overrides)
    return payload


def test_r_squared_from_rmse_matches_ss_res_over_ss_tot() -> None:
    meas = [20.0, 21.0, 22.0, 21.5]
    rmse = 0.5
    mean = sum(meas) / len(meas)
    ss_tot = sum((value - mean) ** 2 for value in meas)
    expected = 1.0 - (len(meas) * rmse * rmse) / ss_tot
    assert r_squared_from_rmse(meas, rmse) == pytest.approx(expected)
    assert air_temperatures_from_history([{"y": [20.0, 21.0]}, {"y": [22.0]}]) == [
        20.0,
        21.0,
        22.0,
    ]


def test_archive_skips_cancelled_and_crash_exits() -> None:
    options: dict = {}
    cancelled = _storeable_result(cancelled=True, success=False, exit_label="Stopped by the user")
    assert pe_fit_should_archive(cancelled) is False
    assert archive_pe_fit_result(options, cancelled) == {}
    crashed = {"success": False, "message": "optimizer exploded"}
    assert pe_fit_should_archive(crashed) is False
    assert archive_pe_fit_result(options, crashed) == {}
    assert options.get(PE_FIT_RESULTS_KEY) in (None, [])


@pytest.mark.parametrize(
    "exit_label",
    ["Did not converge", "Optimiser failed", "ABNORMAL_TERMINATION_IN_LNSRCH"],
)
def test_archive_keeps_finished_fit_with_unlisted_exit(exit_label: str) -> None:
    options: dict = {}
    result = _storeable_result(exit_label=exit_label, success=True)
    entry = archive_pe_fit_result(options, result, result_id="fit-unlisted")
    assert entry["id"] == "fit-unlisted"
    assert entry["exit_label"] == exit_label
    assert options[PE_FIT_RESULTS_KEY][0]["rooms"]["Living Room"]["thermal_mass"] == pytest.approx(
        1_250_000.0
    )


@pytest.mark.parametrize(
    "exit_label,timed_out",
    [
        ("Converged (cost reduction)", False),
        ("Converged (gradient small enough)", False),
        ("Maximum iterations reached", False),
        ("Time limit reached", True),
        ("Fit stopped improving", False),
    ],
)
def test_archive_storeable_exits_without_applying(exit_label: str, timed_out: bool) -> None:
    options: dict = {}
    history = [{"y": [20.0, 21.0]}, {"y": [20.5, 21.5]}]
    result = _storeable_result(
        exit_label=exit_label,
        timed_out=timed_out,
        success=not timed_out,
        rmse_c_best=0.25,
    )
    entry = archive_pe_fit_result(
        options,
        result,
        history=history,
        now_iso="2026-09-22T00:00:00+00:00",
        result_id="fit-1",
    )
    assert entry["id"] == "fit-1"
    assert entry["exit_label"] == exit_label
    assert entry["rmse"] == pytest.approx(0.25)
    assert entry["r_squared"] == pytest.approx(
        r_squared_from_rmse([20.0, 21.0, 20.5, 21.5], 0.25)
    )
    assert entry["rooms"]["Living Room"]["thermal_mass"] == pytest.approx(1_250_000.0)
    assert options[PE_FIT_RESULTS_KEY][0]["id"] == "fit-1"


def test_archive_caps_at_25_newest_first() -> None:
    options: dict = {}
    for index in range(26):
        archive_pe_fit_result(
            options,
            _storeable_result(),
            result_id=f"fit-{index}",
            now_iso=f"2026-09-22T00:00:{index:02d}+00:00",
        )
    catalog = options[PE_FIT_RESULTS_KEY]
    assert len(catalog) == 25
    assert catalog[0]["id"] == "fit-25"
    assert catalog[-1]["id"] == "fit-1"


def test_delete_pe_fit_result_is_catalog_only() -> None:
    options = {"thermal_mass": 1.0}
    archive_pe_fit_result(options, _storeable_result(), result_id="keep")
    archive_pe_fit_result(options, _storeable_result(), result_id="drop")
    assert delete_pe_fit_result(options, "drop") is True
    ids = [row["id"] for row in options[PE_FIT_RESULTS_KEY]]
    assert ids == ["keep"]
    assert options["thermal_mass"] == 1.0
    assert delete_pe_fit_result(options, "missing") is False


@pytest.mark.asyncio
async def test_handle_estimate_archives_without_live_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    room = runtime.control_engine.model.rooms["Living Room"]
    before = float(room.thermal_mass)

    async def fake_history(*args, **kwargs):
        return [{"y": [20.0, 21.0]}, {"y": [20.2, 21.1]}]

    async def fake_estimate(*args, **kwargs):
        return _storeable_result()

    monkeypatch.setattr(sysid_services, "resolve_history", fake_history)
    monkeypatch.setattr(sysid_services, "async_estimate_parameters_ml", fake_estimate)
    payload = await sysid_services.handle_estimate_parameters_ml(
        runtime, {"apply_parameters": False}
    )
    assert payload["success"] is True
    catalog = runtime.options[PE_FIT_RESULTS_KEY]
    assert len(catalog) == 1
    assert catalog[0]["exit_label"] == "Converged (cost reduction)"
    assert catalog[0]["rmse"] == pytest.approx(0.42)
    assert room.thermal_mass == pytest.approx(before)


@pytest.mark.asyncio
async def test_handle_estimate_skips_archive_when_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)

    async def fake_history(*args, **kwargs):
        return [{"y": [20.0]}]

    async def fake_estimate(*args, **kwargs):
        return {
            "success": False,
            "cancelled": True,
            "exit_label": "Stopped by the user",
            "estimated_params": {"Living Room": {"thermal_mass": 9.0, "r_external": 0.1}},
            "rmse_c_best": 0.1,
        }

    monkeypatch.setattr(sysid_services, "resolve_history", fake_history)
    monkeypatch.setattr(sysid_services, "async_estimate_parameters_ml", fake_estimate)
    await sysid_services.handle_estimate_parameters_ml(runtime, {"apply_parameters": False})
    assert runtime.options.get(PE_FIT_RESULTS_KEY) in (None, [])


def test_worker_error_does_not_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)

    async def fake_estimate(*args, **kwargs):
        raise RuntimeError("optimizer exploded")

    monkeypatch.setattr(sysid_services, "handle_estimate_parameters_ml", fake_estimate)
    sysid_services.start_estimate_parameters_ml(runtime, {"apply_parameters": False})
    job = wait_pe_job(runtime)
    assert job["status"] == "error"
    assert runtime.options.get(PE_FIT_RESULTS_KEY) in (None, [])


@pytest.mark.asyncio
async def test_delete_pe_fit_result_handler_persists_catalog(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    archive_pe_fit_result(runtime.options, _storeable_result(), result_id="gone")
    out = await sysid_services.handle_delete_pe_fit_result(
        runtime, {"result_id": "gone"}
    )
    assert out["deleted"] is True
    assert runtime.options.get(PE_FIT_RESULTS_KEY) == []


def test_controller_config_exposes_pe_fit_results(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    archive_pe_fit_result(runtime.options, _storeable_result(), result_id="ui-1")
    config = runtime.controller_config()
    rows = config["pe_fit_results"]
    assert rows[0]["id"] == "ui-1"
    assert "living_room" in rows[0]["rooms"]


def test_identification_results_ui_locks() -> None:
    source = DETAIL.read_text(encoding="utf-8")
    assert "Identification Results" in source
    assert "pe_fit_results" in source
    assert "deletePeFitResult" in source
    assert "Finished — load a result below when you want to apply it." in source
    assert "populateModelFromSysid" not in source
    assert "Loaded — review the fields below, then click Apply Parameters." not in source
    datasets = (
        ROOT / "heatingassistant" / "app" / "static" / "js" / "identification"
        / "sysid-datasets.js"
    ).read_text(encoding="utf-8")
    assert "Stored Datasets" in datasets
    assert "container.appendChild(resultsSection);" in source
    assert source.index("container.appendChild(resultsSection);") < source.index(
        "container.appendChild(historySection);"
    )
