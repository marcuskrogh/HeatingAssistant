"""Replay 13 Nov 05:00 through 14 Nov 05:00 on the planner's own coefficients.

Inputs are the fixture (measured room and outdoor temperature, the app's
heating-power series, and unscaled solar). Coefficients come from the
planner model after the persisted parameters are restored, including the
constant air-heat channel the model uses for the pump's minimum output.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.engine.parameter_lifecycle import (
    estimated_params_snapshot,
    restore_estimated_parameters,
)

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = (
    Path(__file__).resolve().parents[1]
    / "heatingassistant"
    / "engine"
    / "data"
    / "living_room_estimated_params.json"
)
DAY = FIXTURES / "fixture_living_room_13nov_hourly.csv"
COPENHAGEN = ZoneInfo("Europe/Copenhagen")


def _load_day() -> dict[str, np.ndarray]:
    with DAY.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    index_key = list(rows[0].keys())[0]

    def column(name: str) -> np.ndarray:
        return np.array([float(row[name] or 0.0) for row in rows], dtype=float)

    hours = np.array(
        [
            datetime.fromisoformat(row[index_key]).astimezone(COPENHAGEN).hour
            for row in rows
        ],
        dtype=int,
    )
    return {
        "t_room": column("T_room"),
        "t_out": column("T_out"),
        "app_heat_w": column("app_heat_W"),
        "solar_w": column("app_solar_unscaled_W"),
        "hours": hours,
    }


def _planner_coefficients() -> tuple[float, float, float, float, float]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    engine = ControlEngine(config)
    restore_estimated_parameters(
        engine.model,
        engine.heat_sources,
        estimated_params_snapshot(config),
    )
    sde = engine._controller._control_system
    room = sde._room_list[0]
    n_rooms = int(sde._n_rooms)
    a_out = float(sde._G_d[0, 0])
    b_sol = float(sde._G_d[0, 1])
    inv_c = float(sde._inv_C_cap[0])
    air_heat = float(sde._G_d[0, 1 + n_rooms])
    internal_gain = float(sde._model.rooms[room].internal_gain)
    scale = float(engine.heat_sources[0]._power_scale)
    return a_out, b_sol, inv_c, scale, air_heat * internal_gain


def _replay(
    day: dict[str, np.ndarray],
    a_out: float,
    b_sol: float,
    inv_c: float,
    scale: float,
    q_internal: float,
    dt: float = 3600.0,
) -> np.ndarray:
    temperatures = day["t_room"]
    predicted = [float(temperatures[0])]
    for k in range(len(temperatures) - 1):
        heat_rate = (
            scale * float(day["app_heat_w"][k]) * inv_c
            + b_sol * float(day["solar_w"][k])
            + q_internal
        )
        equilibrium = float(day["t_out"][k]) + heat_rate / a_out
        predicted.append(equilibrium + (predicted[-1] - equilibrium) * np.exp(-a_out * dt))
    return np.asarray(predicted, dtype=float)


def test_planner_tracks_logged_day() -> None:
    day = _load_day()
    a_out, b_sol, inv_c, scale, q_internal = _planner_coefficients()
    predicted = _replay(day, a_out, b_sol, inv_c, scale, q_internal)
    measured = day["t_room"]
    rmse = float(np.sqrt(np.mean((predicted - measured) ** 2)))
    night = (day["hours"] >= 23) | (day["hours"] <= 2)
    measured_rate = float(np.diff(measured)[night[:-1]].mean())
    modelled_rate = float(np.diff(predicted)[night[:-1]].mean())
    assert rmse <= 1.0, f"24 h open-loop error {rmse:.2f} K > 1.0 K"
    assert abs(modelled_rate - measured_rate) <= 0.1, (
        f"night cooling rate off by {modelled_rate - measured_rate:.2f} K/h"
    )
