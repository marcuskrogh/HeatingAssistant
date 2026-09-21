"""SWD-572: leftover 2R2C UI/plots/ICs after the 1R1C plant swap."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from heatingassistant.engine.estimation.nstep_pem import _seed_state
from heatingassistant.engine.estimation.theta_layout import _ThetaLayout


STATIC = Path("heatingassistant/app/static/js")
SRC = Path("heatingassistant")


class _FakeEst:
    _n = 1


class _FakeModel:
    nx = 2

    def initial_state_from_measurement(self, ym, u, d, wall_seed="steady_state"):
        return np.array([float(ym[0]), 0.42], dtype=float)


def test_pe_markup_is_1r1c_model_parameters() -> None:
    markup = (STATIC / "identification" / "sysid-detail-markup.js").read_text(
        encoding="utf-8"
    )
    assert "Thermal Mass (C)" in markup
    assert "Thermal Resistance" in markup
    assert "Internal Gain" in markup
    assert "Solar Scale" in markup
    assert "Open-contact UA" in markup
    assert "Envelope Split" not in markup
    assert "param-t-wall-initial" not in markup
    assert "param-c-air-fraction" not in markup
    assert "param-r-aw-fraction" not in markup


def test_identification_and_room_plots_are_air_only() -> None:
    datasets = (STATIC / "identification" / "sysid-datasets.js").read_text(
        encoding="utf-8"
    )
    charts = (STATIC / "charts" / "room-charts.js").read_text(encoding="utf-8")
    history = (STATIC / "pages" / "room-detail-history.js").read_text(encoding="utf-8")
    detail = (STATIC / "pages" / "room-detail.js").read_text(encoding="utf-8")
    assert "Predicted (wall)" not in datasets
    assert "makeDataset('Wall'" not in charts
    assert "Wall Forecast" not in charts
    assert "temperature_wall" not in history
    assert "temperature_wall" not in detail


def test_config_room_editor_hides_2r2c_splits() -> None:
    editor = (STATIC / "config" / "config-room-editor.js").read_text(encoding="utf-8")
    assert "c_air_fraction" not in editor
    assert "r_aw_fraction" not in editor
    assert "wall node" not in editor.lower()


def test_pe_theta_layout_identifies_1r1c_blocks_only() -> None:
    layout = _ThetaLayout(
        n_rooms=1,
        identifiable_sources=[0],
        identifiable_pairs=[],
        identifiable_solar=[0],
        identifiable_splits=[0],
        identifiable_ua=[0],
        n_wall_segs=3,
    )
    assert layout.identifiable_splits == []
    assert layout.n_wall_segs == 0
    assert layout.idx_t_wall_init == (3, 3)
    assert layout.idx_c_air[0] == layout.idx_c_air[1]
    assert layout.idx_r_aw[0] == layout.idx_r_aw[1]
    # C, R, q_int, α, solar, ua_open
    assert layout.size == 6


def test_identified_wall_ic_does_not_overwrite_emitter_lag() -> None:
    layout = _ThetaLayout(n_rooms=1, identifiable_sources=[0], identifiable_pairs=[])
    theta = np.array([15.0, -3.0, 40.0, 0.5], dtype=float)
    rec0 = {"ym": [21.0], "u": [0.3], "d": [0.0]}
    x, sx = _seed_state(
        _FakeEst(),
        _FakeModel(),
        layout,
        theta,
        rec0,
        inject_wall=True,
        wall_seg_idx=0,
        ntheta=len(theta),
        nx=2,
    )
    assert x[0] == pytest.approx(21.0)
    assert x[1] == pytest.approx(0.42)
    assert np.all(sx == 0.0)

    x2 = np.array([21.0, 0.42], dtype=float)
    sx2 = np.zeros((len(theta), 2))
    layout.apply_identified_wall_ic(
        x2, sx2, theta, n=1, nx=2, wall_seg_idx=0, t_lo=-40.0, t_hi=80.0,
    )
    assert x2[1] == pytest.approx(0.42)
    assert np.all(sx2 == 0.0)


def test_sysid_and_open_loop_guard_wall_block_on_nx_phys() -> None:
    sysid = (SRC / "engine" / "sysid.py").read_text(encoding="utf-8")
    open_loop = (SRC / "engine" / "open_loop_predictions.py").read_text(
        encoding="utf-8"
    )
    assert "def _has_wall_block" in sysid
    assert "nx_phys > n" in sysid
    assert "_nx_phys" in open_loop
    assert "No-op on 1R1C" in open_loop
