"""Pure unit tests for history record parsers."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from heatingassistant.engine.history.records import (
    record_d,
    record_u,
    record_window_open,
)


def test_record_u_pads_short_vector():
    record = {"u": [0.25]}
    u = record_u(record, n_u=3)
    assert u.shape == (3,)
    assert np.allclose(u, [0.25, 0.0, 0.0])


def test_record_d_uses_disturbance_vector():
    record = {"d_outdoor": 7.5, "d_solar": {"room_a": 100.0, "room_b": 50.0}}
    expected = np.array([7.5, 100.0, 50.0, 1.0])
    system = SimpleNamespace(
        disturbance_vector=lambda outdoor, solar: [
            outdoor,
            solar.get("room_a", 0.0),
            solar.get("room_b", 0.0),
            1.0,
        ]
    )
    d = record_d(record, system, ["room_a", "room_b"], n_d=4)
    assert np.allclose(d, expected)


def test_record_window_open_defaults_false():
    record = {"window_open": {"room_a": True}}
    flags = record_window_open(record, ["room_a", "room_b"])
    assert flags == [True, False]

    flags_missing = record_window_open({}, ["room_a"])
    assert flags_missing == [False]
