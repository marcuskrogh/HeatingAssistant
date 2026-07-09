"""
Reusable estimation / sysid fixtures for pytest.

Import factory functions directly::

    from tests.helpers.estimation_fixtures import (
        make_single_room,
        make_electric_heaters,
        generate_history,
        make_kalman_ml_estimator,
    )

Or request the module-scoped pytest fixtures defined at the bottom of this
module (registered via ``pytest_plugins`` in ``tests/conftest.py``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

import numpy as np
import pytest

from custom_components.heating_assistant.heat_sources import ElectricHeater
from custom_components.heating_assistant.parameter_estimator import KalmanMLEstimator
from custom_components.heating_assistant.thermal_model import HouseModel, Room

# Defaults aligned with tests/test_parameter_estimator.py helpers.
_DEFAULT_DT = 60.0
_DEFAULT_N_STEPS = 60
_DEFAULT_OUTDOOR_TEMP = 5.0
_DEFAULT_HEATING_FRACTION = 0.5
_DEFAULT_NOISE_STD = 0.05
_DEFAULT_SEED = 42


def make_single_room(
    *,
    name: str = "living_room",
    thermal_mass: float = 5_000_000.0,
    r_external: float = 0.05,
    temperature: float = 20.0,
    setpoint: float = 21.0,
) -> Room:
    """Return a single-room ``Room`` with sensible defaults."""
    return Room(
        name=name,
        thermal_mass=thermal_mass,
        r_external=r_external,
        temperature=temperature,
        setpoint=setpoint,
    )


def make_electric_heaters(
    rooms: Sequence[Room],
    *,
    max_power: float = 2_000.0,
) -> List[ElectricHeater]:
    """Build one ``ElectricHeater`` per room (name ``{room}_heater``)."""
    return [
        ElectricHeater(f"{room.name}_heater", room.name, max_power=max_power)
        for room in rooms
    ]


def generate_history(
    rooms: Sequence[Room],
    sources: Sequence[ElectricHeater],
    *,
    dt: float = _DEFAULT_DT,
    n_steps: int = _DEFAULT_N_STEPS,
    outdoor_temp: float = _DEFAULT_OUTDOOR_TEMP,
    heating_fraction: float = _DEFAULT_HEATING_FRACTION,
    noise_std: float = _DEFAULT_NOISE_STD,
    seed: int = _DEFAULT_SEED,
) -> List[Dict[str, Any]]:
    """Simulate a history buffer using the true model parameters."""
    rng = np.random.default_rng(seed)
    model = HouseModel(list(rooms))
    temps = {room.name: room.temperature for room in rooms}

    history: List[Dict[str, Any]] = []
    for _ in range(n_steps):
        y = [temps[room.name] + rng.normal(0.0, noise_std) for room in rooms]
        u = [heating_fraction] * len(sources)
        solar = {room.name: 0.0 for room in rooms}
        history.append(
            {
                "y": y,
                "u": u,
                "d_outdoor": outdoor_temp,
                "d_solar": solar,
            }
        )
        heat_inputs = {
            src.room: src.thermal_power(heating_fraction, outdoor_temp)
            for src in sources
        }
        temps = model.step(dt, heat_inputs, outdoor_temp, solar)
    return history


def make_kalman_ml_estimator(
    rooms: Sequence[Room],
    sources: Sequence[ElectricHeater],
    *,
    dt: float = _DEFAULT_DT,
    **kwargs: Any,
) -> KalmanMLEstimator:
    """Construct a ``KalmanMLEstimator`` for the given rooms and sources."""
    return KalmanMLEstimator(list(rooms), list(sources), dt=dt, **kwargs)


# ---------------------------------------------------------------------------
# Module-scoped pytest fixtures (shared across a test module)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def single_room() -> Room:
    return make_single_room()


@pytest.fixture(scope="module")
def single_room_sources(single_room: Room) -> List[ElectricHeater]:
    return make_electric_heaters([single_room])


@pytest.fixture(scope="module")
def single_room_history(
    single_room: Room,
    single_room_sources: List[ElectricHeater],
) -> List[Dict[str, Any]]:
    return generate_history([single_room], single_room_sources)


@pytest.fixture(scope="module")
def kalman_ml_estimator(
    single_room: Room,
    single_room_sources: List[ElectricHeater],
) -> KalmanMLEstimator:
    return make_kalman_ml_estimator([single_room], single_room_sources)
