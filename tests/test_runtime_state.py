"""Unit tests for coordinator runtime-state persistence and cloud-cover smoothing."""

from __future__ import annotations

import math
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from custom_components.heating_assistant.coordinator import runtime_state as rs
from custom_components.heating_assistant.const import CLOUD_SMOOTHING_TAU_S
from tests.helpers.coordinator_stubs import make_minimal_coordinator

# Builds real coordinator objects (tests/helpers stubs) — integration tier.
pytestmark = pytest.mark.integration


def _runtime_coord(**kwargs):
    coord = make_minimal_coordinator(**kwargs)
    coord._runtime_state_loaded = False
    coord._runtime_store = SimpleNamespace(
        async_load=AsyncMock(return_value=None),
        async_delay_save=MagicMock(),
    )
    coord._pending_ekf_state = None
    return coord


@pytest.mark.asyncio
async def test_ensure_runtime_state_loaded_skips_when_already_loaded():
    coord = _runtime_coord()
    coord._runtime_state_loaded = True

    await rs.ensure_runtime_state_loaded(coord)

    coord._runtime_store.async_load.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_runtime_state_loaded_seeds_cloud_cover_and_ekf_state():
    coord = _runtime_coord()
    x_hat = [20.0, 19.5]
    p_mat = [[1.0, 0.0], [0.0, 1.0]]
    coord._runtime_store.async_load = AsyncMock(
        return_value={
            "cloud_cover_filtered": 0.42,
            "ekf_x_hat": x_hat,
            "ekf_P": p_mat,
            "ekf_save_ts": 1_700_000_000.0,
            "ekf_u_prev": [0.5],
            "ekf_d_prev": [5.0, 0.0],
        }
    )

    await rs.ensure_runtime_state_loaded(coord)

    assert coord._cloud_cover_filtered == pytest.approx(0.42)
    assert coord._pending_ekf_state is not None
    restored_x, restored_p, save_ts, u_prev, d_prev = coord._pending_ekf_state
    np.testing.assert_allclose(restored_x, x_hat)
    np.testing.assert_allclose(restored_p, p_mat)
    assert save_ts == pytest.approx(1_700_000_000.0)
    np.testing.assert_allclose(u_prev, [0.5])
    np.testing.assert_allclose(d_prev, [5.0, 0.0])
    assert coord._runtime_state_loaded is True


@pytest.mark.asyncio
async def test_ensure_runtime_state_loaded_clamps_cloud_cover():
    coord = _runtime_coord()
    coord._runtime_store.async_load = AsyncMock(
        return_value={"cloud_cover_filtered": 1.5}
    )

    await rs.ensure_runtime_state_loaded(coord)

    assert coord._cloud_cover_filtered == 1.0


@pytest.mark.asyncio
async def test_ensure_runtime_state_loaded_tolerates_load_failure():
    coord = _runtime_coord()
    coord._runtime_store.async_load = AsyncMock(side_effect=OSError("disk"))

    await rs.ensure_runtime_state_loaded(coord)

    assert coord._runtime_state_loaded is True
    assert coord._cloud_cover_filtered is None
    assert coord._pending_ekf_state is None


@pytest.mark.asyncio
async def test_ensure_runtime_state_loaded_preserves_existing_cloud_cover():
    coord = _runtime_coord()
    coord._cloud_cover_filtered = 0.3
    coord._runtime_store.async_load = AsyncMock(
        return_value={"cloud_cover_filtered": 0.9}
    )

    await rs.ensure_runtime_state_loaded(coord)

    assert coord._cloud_cover_filtered == pytest.approx(0.3)


def test_smooth_cloud_cover_wrapper_persists_filtered_state_on_coordinator():
    """The EMA math itself is covered by test_weather_smoothing.py; this only
    checks the coordinator wrapper feeds ``coord.dt`` / CLOUD_SMOOTHING_TAU_S
    into the pure step and persists the result on ``_cloud_cover_filtered``."""
    coord = _runtime_coord(dt=900)

    first = rs.smooth_cloud_cover(coord, 0.0)
    assert first == pytest.approx(0.0)
    assert coord._cloud_cover_filtered == pytest.approx(0.0)

    result = rs.smooth_cloud_cover(coord, 1.0)

    alpha = 1.0 - math.exp(-coord.dt / CLOUD_SMOOTHING_TAU_S)
    assert result == pytest.approx(alpha)
    assert coord._cloud_cover_filtered == pytest.approx(alpha)


def test_save_runtime_state_persists_cloud_cover_and_ekf_snapshot():
    coord = _runtime_coord()
    coord._cloud_cover_filtered = 0.65
    x_hat = np.array([21.0])
    p_mat = np.array([[0.5]])
    u_prev = np.array([1.0])
    d_prev = np.array([5.0, 0.0])
    coord.controller = SimpleNamespace(
        ekf_state=(x_hat, p_mat),
        ekf_inputs=(u_prev, d_prev),
    )
    captured: dict = {}

    def _capture_snapshot(data_func, delay):
        captured["delay"] = delay
        captured["payload"] = data_func()

    coord._runtime_store.async_delay_save = MagicMock(side_effect=_capture_snapshot)

    rs.save_runtime_state(coord)

    coord._runtime_store.async_delay_save.assert_called_once()
    payload = captured["payload"]
    assert payload["cloud_cover_filtered"] == pytest.approx(0.65)
    assert payload["ekf_x_hat"] == [21.0]
    assert payload["ekf_P"] == [[0.5]]
    assert payload["ekf_u_prev"] == [1.0]
    assert payload["ekf_d_prev"] == [5.0, 0.0]
    assert "ekf_save_ts" in payload


def test_save_runtime_state_still_persists_cloud_cover_without_ekf():
    coord = _runtime_coord()
    coord._cloud_cover_filtered = 0.2
    coord.controller = SimpleNamespace()
    captured: dict = {}

    def _capture_snapshot(data_func, _delay):
        captured["payload"] = data_func()

    coord._runtime_store.async_delay_save = MagicMock(side_effect=_capture_snapshot)

    rs.save_runtime_state(coord)

    assert captured["payload"] == {"cloud_cover_filtered": 0.2}


def test_restore_pending_ekf_state_noop_when_empty():
    coord = _runtime_coord()
    coord.controller = MagicMock()

    rs.restore_pending_ekf_state(coord)

    coord.controller.restore_ekf_state.assert_not_called()


def test_restore_pending_ekf_state_restores_and_propagates_gap():
    coord = _runtime_coord()
    x_hat = np.array([20.0])
    p_mat = np.array([[1.0]])
    u_prev = np.array([0.5])
    d_prev = np.array([5.0])
    coord._pending_ekf_state = (x_hat, p_mat, 1_700_000_000.0, u_prev, d_prev)
    coord.controller = MagicMock(restore_ekf_state=MagicMock(return_value=True))

    with patch.object(rs, "propagate_ekf_gap") as mock_propagate:
        rs.restore_pending_ekf_state(coord)

    coord.controller.restore_ekf_state.assert_called_once_with(x_hat, p_mat)
    mock_propagate.assert_called_once_with(
        coord, 1_700_000_000.0, u_prev, d_prev
    )
    assert coord._pending_ekf_state is None


def test_restore_pending_ekf_state_clears_incompatible_state():
    coord = _runtime_coord()
    coord._pending_ekf_state = (
        np.array([20.0]),
        np.array([[1.0]]),
        1_700_000_000.0,
        None,
        None,
    )
    coord.controller = MagicMock(restore_ekf_state=MagicMock(return_value=False))

    with patch.object(rs, "propagate_ekf_gap") as mock_propagate:
        rs.restore_pending_ekf_state(coord)

    mock_propagate.assert_not_called()
    assert coord._pending_ekf_state is None


def test_propagate_ekf_gap_skips_when_save_ts_non_positive():
    coord = _runtime_coord()
    coord.controller = MagicMock()

    rs.propagate_ekf_gap(coord, 0.0, None, None)

    coord.controller.propagate_ekf.assert_not_called()


def test_propagate_ekf_gap_propagates_recent_gap():
    coord = _runtime_coord(dt=900)
    coord.heat_sources = [SimpleNamespace(room="living_room", can_cool=False)]
    coord.controller = MagicMock()
    save_ts = time.time() - 1800.0

    rs.propagate_ekf_gap(
        coord,
        save_ts,
        np.array([0.25]),
        np.array([5.0, 0.0]),
    )

    coord.controller.propagate_ekf.assert_called_once()
    u_seq, d_arr = coord.controller.propagate_ekf.call_args[0]
    assert u_seq.shape == (2, 1)
    np.testing.assert_allclose(u_seq[0], [0.25])
    np.testing.assert_allclose(d_arr, [5.0, 0.0])


def test_system_nd_falls_back_to_room_count():
    coord = _runtime_coord(room_names=["living_room", "bedroom"])
    coord.controller = SimpleNamespace(_mpc=SimpleNamespace(_model=SimpleNamespace(nd=99)))

    assert rs.system_nd(coord) == 99

    broken = _runtime_coord(room_names=["living_room", "bedroom"])
    broken.controller = object()
    assert rs.system_nd(broken) == 5
