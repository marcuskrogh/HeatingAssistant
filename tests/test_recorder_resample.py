"""Unit tests for recorder resample helpers."""

from heatingassistant.engine.recorder_resample import (
    build_uniform_grid, zoh_resample,
)

def test_build_grid():
    g = build_uniform_grid(0.0, 900.0*3, 900.0)
    assert g == [0.0, 900.0, 1800.0, 2700.0]
    assert build_uniform_grid(100.0, 50.0, 10.0) == []
    assert build_uniform_grid(0.0, 100.0, 0.0) == []

def test_zoh_basic():
    samples = [(0.0, 10.0), (1000.0, 20.0), (2500.0, 30.0)]
    grid = [0.0, 900.0, 1800.0, 2700.0]
    # 0->10 (sample at 0), 900->10 (last<=900 is t=0), 1800->20 (t=1000),
    # 2700->30 (t=2500)
    assert zoh_resample(samples, grid) == [10.0, 10.0, 20.0, 30.0]

def test_zoh_none_before_first_sample():
    samples = [(1000.0, 5.0)]
    grid = [0.0, 500.0, 1000.0, 1500.0]
    assert zoh_resample(samples, grid) == [None, None, 5.0, 5.0]

def test_zoh_unsorted_and_invalid_dropped():
    samples = [(2500.0, 30.0), (0.0, 10.0), (1000.0, None), (500.0, float("nan"))]
    grid = [0.0, 1000.0, 3000.0]
    assert zoh_resample(samples, grid) == [10.0, 10.0, 30.0]

def test_zoh_empty():
    assert zoh_resample([], [0.0, 1.0]) == [None, None]
