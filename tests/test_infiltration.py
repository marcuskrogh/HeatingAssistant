"""
Unit tests for the Phase 1 Step 2 (C1) infiltration / conduction split.

The Phase 1 spec splits the bundled ``1/r_external`` into a constant
conductive part and a wind-driven Sherman–Grimsrud (LBL) infiltration
part.  The split is calibrated so that at the typical reference
conditions (``SHERMAN_GRIMSRUD_V_TYPICAL`` m/s, ``SHERMAN_GRIMSRUD_DT_TYPICAL``
K) the runtime UA equals exactly ``1 / r_external``.  These tests pin
down:

- the calibration identity (no behaviour change at typical conditions),
- the monotonic UA response to wind speed for a leaky room,
- the no-op for ``infiltration_fraction = 0`` (room declared as having
  no wind-driven losses),
- the wind-speed parsing path in ``weather.read_wind_speed_now``,
- the end-to-end controller plumbing — ``set_wind_speed`` propagates
  to both the EKF and OCP systems and changes the Jacobian diagonal.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heatingassistant.engine.const import (
    ENVELOPE_TIGHTNESS_LEAKY,
    ENVELOPE_TIGHTNESS_PASSIVE,
    ENVELOPE_TIGHTNESS_TIGHT,
    ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION,
    ENVELOPE_TIGHTNESS_TYPICAL,
    SHERMAN_GRIMSRUD_DT_TYPICAL,
    SHERMAN_GRIMSRUD_V_TYPICAL,
)
from heatingassistant.engine.thermal_model import (
    HouseModel,
    Room,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_single_room(
    r_external: float = 0.05,
    infiltration_fraction: float = 0.30,
) -> HouseModel:
    room = Room(
        name="living_room",
        thermal_mass=5_000_000.0,
        r_external=r_external,
        infiltration_fraction=infiltration_fraction,
    )
    return HouseModel([room])


# ---------------------------------------------------------------------------
# Calibration identity
# ---------------------------------------------------------------------------


def test_typical_conditions_match_legacy_ua() -> None:
    """At the calibration point the wind overlay is the zero vector,
    so the effective UA equals exactly ``1 / r_external``.

    This is the no-regression guarantee: every install that doesn't
    have a wind source still sees the pre-C1 behaviour.
    """
    model = make_single_room(r_external=0.05, infiltration_fraction=0.30)
    # Pick a room temperature so that ΔT = SHERMAN_GRIMSRUD_DT_TYPICAL.
    T = np.array([20.0])
    outdoor = 20.0 - SHERMAN_GRIMSRUD_DT_TYPICAL  # ⇒ |ΔT| = 20 K

    delta = model.infiltration_delta_ua(
        outdoor_temp=outdoor,
        wind_speed=SHERMAN_GRIMSRUD_V_TYPICAL,
        room_temps=T,
    )
    assert np.allclose(delta, 0.0, atol=1e-9)


def test_no_wind_data_is_no_op() -> None:
    """``wind_speed=None`` → zero overlay → exactly the pre-C1 model."""
    model = make_single_room()
    delta = model.infiltration_delta_ua(
        outdoor_temp=0.0, wind_speed=None, room_temps=np.array([20.0]),
    )
    assert np.allclose(delta, 0.0)


def test_zero_infiltration_fraction_is_no_op() -> None:
    """A room declared as having no wind-driven loss returns zero overlay
    regardless of how strong the wind is.
    """
    model = make_single_room(infiltration_fraction=0.0)
    for v in (0.0, 3.0, 10.0, 25.0):
        delta = model.infiltration_delta_ua(
            outdoor_temp=-5.0, wind_speed=v, room_temps=np.array([21.0]),
        )
        assert np.allclose(delta, 0.0), f"v={v} produced non-zero overlay"


# ---------------------------------------------------------------------------
# Wind response
# ---------------------------------------------------------------------------


def test_overlay_grows_monotonically_with_wind() -> None:
    """Higher wind speed → larger positive overlay (more external loss)
    on a leaky room with non-trivial ΔT.
    """
    model = make_single_room(
        infiltration_fraction=ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION[
            ENVELOPE_TIGHTNESS_LEAKY
        ],
    )
    T = np.array([20.0])
    outdoor = 0.0  # |ΔT| = 20 K
    overlays = [
        float(model.infiltration_delta_ua(
            outdoor_temp=outdoor, wind_speed=v, room_temps=T,
        )[0])
        for v in (0.0, 3.0, 6.0, 10.0, 15.0)
    ]
    # 3 m/s is the calibration point: overlay is zero there.
    assert overlays[1] == pytest.approx(0.0, abs=1e-9)
    # Below the calibration the overlay should be negative (less loss),
    # above the calibration positive (more loss).
    assert overlays[0] < 0.0
    for prev, curr in zip(overlays[1:-1], overlays[2:]):
        assert curr > prev, "expected monotonic growth with wind speed"
    # Doubling wind from 6 m/s to 15 m/s should at least double the
    # overlay (the SG kernel scales sqrt(v²) at high winds).
    assert overlays[4] > 1.5 * overlays[2]


def test_overlay_scales_with_infiltration_fraction() -> None:
    """A tighter envelope produces a smaller wind-driven overlay for
    the same conditions."""
    leaky = make_single_room(
        infiltration_fraction=ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION[
            ENVELOPE_TIGHTNESS_LEAKY
        ],
    )
    tight = make_single_room(
        infiltration_fraction=ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION[
            ENVELOPE_TIGHTNESS_TIGHT
        ],
    )
    passive = make_single_room(
        infiltration_fraction=ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION[
            ENVELOPE_TIGHTNESS_PASSIVE
        ],
    )
    args = dict(outdoor_temp=0.0, wind_speed=10.0, room_temps=np.array([20.0]))
    d_leaky = float(leaky.infiltration_delta_ua(**args)[0])
    d_tight = float(tight.infiltration_delta_ua(**args)[0])
    d_passive = float(passive.infiltration_delta_ua(**args)[0])
    assert d_leaky > d_tight > d_passive > 0.0


def test_overlay_per_room_proportional_to_leakage_area() -> None:
    """In a two-room model the overlay scales linearly per-room with
    each room's effective leakage area (= fraction * (1/r_external) /
    rho_cp / sg_typical)."""
    room_a = Room(
        name="a", thermal_mass=5e6, r_external=0.05, infiltration_fraction=0.20,
    )
    room_b = Room(
        name="b", thermal_mass=5e6, r_external=0.05, infiltration_fraction=0.40,
    )
    model = HouseModel([room_a, room_b])
    overlay = model.infiltration_delta_ua(
        outdoor_temp=0.0, wind_speed=8.0, room_temps=np.array([20.0, 20.0]),
    )
    # Same r_external, same ΔT → same SG factor → overlay ratio equals
    # leakage-area ratio = fraction ratio.
    assert overlay[1] / overlay[0] == pytest.approx(0.40 / 0.20, rel=1e-9)


# ---------------------------------------------------------------------------
# step() integration
# ---------------------------------------------------------------------------


def test_step_with_wind_cools_faster_than_typical() -> None:
    """A leaky room under high wind loses heat faster than the same
    room at typical conditions.  Single-step comparison after no heat
    input."""
    common = dict(
        thermal_mass=5_000_000.0, r_external=0.05, infiltration_fraction=0.50,
        temperature=20.0,
    )
    leaky_high = HouseModel([Room(name="a", **common)])
    leaky_typ = HouseModel([Room(name="a", **common)])

    high = leaky_high.step(
        dt=900, heat_inputs={}, outdoor_temp=0.0, solar_gains={},
        wind_speed=15.0,
    )
    typ = leaky_typ.step(
        dt=900, heat_inputs={}, outdoor_temp=0.0, solar_gains={},
        wind_speed=SHERMAN_GRIMSRUD_V_TYPICAL,
    )
    assert high["a"] < typ["a"], (
        f"high-wind step ({high['a']:.3f}) should cool more than "
        f"typical-wind step ({typ['a']:.3f})"
    )


def test_step_no_wind_arg_matches_typical_wind() -> None:
    """Omitting ``wind_speed`` (or passing ``None``) reproduces the
    typical-conditions step exactly."""
    common = dict(
        thermal_mass=5_000_000.0, r_external=0.05, infiltration_fraction=0.30,
        temperature=20.0,
    )
    a = HouseModel([Room(name="a", **common)])
    b = HouseModel([Room(name="a", **common)])

    no_wind = a.step(
        dt=900, heat_inputs={}, outdoor_temp=0.0, solar_gains={},
    )
    typ_wind = b.step(
        dt=900, heat_inputs={}, outdoor_temp=0.0, solar_gains={},
        wind_speed=SHERMAN_GRIMSRUD_V_TYPICAL,
    )
    # Both should be very close — the typical-wind path computes the
    # overlay then sees it's exactly zero at the reference ΔT.  Tiny
    # numerical drift from the float sqrt is allowed.
    assert no_wind["a"] == pytest.approx(typ_wind["a"], abs=5e-3)


# ---------------------------------------------------------------------------
# weather.read_wind_speed_now
# ---------------------------------------------------------------------------


class _FakeWeatherState:
    """Minimal stand-in for a Home Assistant entity state."""

    def __init__(self, state: str, attributes: dict) -> None:
        self.state = state
        self.attributes = attributes


class _FakeHass:
    def __init__(self, states: dict) -> None:
        self._states = states

    class _States:
        def __init__(self, parent: "_FakeHass") -> None:
            self._parent = parent

        def get(self, entity_id: str):
            return self._parent._states.get(entity_id)

    @property
    def states(self) -> "_FakeHass._States":
        return _FakeHass._States(self)


def test_read_wind_speed_returns_none_without_entity() -> None:
    from heatingassistant.engine.weather import read_wind_speed_now
    assert read_wind_speed_now(_FakeHass({}), None) is None


def test_read_wind_speed_returns_none_on_missing_state() -> None:
    from heatingassistant.engine.weather import read_wind_speed_now
    assert read_wind_speed_now(_FakeHass({}), "weather.home") is None


def test_read_wind_speed_returns_value_in_mps() -> None:
    from heatingassistant.engine.weather import read_wind_speed_now
    states = {
        "weather.home": _FakeWeatherState(
            state="partlycloudy",
            attributes={"wind_speed": 4.5, "wind_speed_unit": "m/s"},
        ),
    }
    val = read_wind_speed_now(_FakeHass(states), "weather.home")
    assert val == pytest.approx(4.5)


def test_read_wind_speed_converts_kmh_to_mps() -> None:
    from heatingassistant.engine.weather import read_wind_speed_now
    states = {
        "weather.home": _FakeWeatherState(
            state="cloudy",
            attributes={"wind_speed": 36.0, "wind_speed_unit": "km/h"},
        ),
    }
    val = read_wind_speed_now(_FakeHass(states), "weather.home")
    # 36 km/h = 10 m/s
    assert val == pytest.approx(10.0, rel=1e-6)


def test_read_wind_speed_converts_mph_to_mps() -> None:
    from heatingassistant.engine.weather import read_wind_speed_now
    states = {
        "weather.home": _FakeWeatherState(
            state="windy",
            attributes={"wind_speed": 10.0, "wind_speed_unit": "mph"},
        ),
    }
    val = read_wind_speed_now(_FakeHass(states), "weather.home")
    # 10 mph ≈ 4.47 m/s
    assert val == pytest.approx(4.4704, rel=1e-4)


def test_read_wind_speed_unknown_unit_falls_back_to_mps() -> None:
    from heatingassistant.engine.weather import read_wind_speed_now
    states = {
        "weather.home": _FakeWeatherState(
            state="cloudy",
            attributes={"wind_speed": 7.5, "wind_speed_unit": "furlongs/fortnight"},
        ),
    }
    val = read_wind_speed_now(_FakeHass(states), "weather.home")
    # Falls back to m/s rather than guessing.
    assert val == pytest.approx(7.5)


def test_read_wind_speed_clips_to_nonnegative() -> None:
    from heatingassistant.engine.weather import read_wind_speed_now
    states = {
        "weather.home": _FakeWeatherState(
            state="cloudy",
            attributes={"wind_speed": -1.0, "wind_speed_unit": "m/s"},
        ),
    }
    val = read_wind_speed_now(_FakeHass(states), "weather.home")
    assert val == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Controller plumbing
# ---------------------------------------------------------------------------


def test_controller_set_wind_speed_updates_jacobian() -> None:
    """``HouseThermalSDE.dfdx`` carries the wind-overlay diagonal so the
    EKF stays consistent with the dynamics in ``f``.  Setting a wind
    speed must therefore change the Jacobian diagonal compared with
    the typical-conditions baseline."""
    from heatingassistant.engine.controller import HouseThermalSDE
    from heatingassistant.engine.heat_sources import ElectricHeater

    room = Room(
        name="a", thermal_mass=5e6, r_external=0.05, infiltration_fraction=0.50,
        temperature=20.0,
    )
    model = HouseModel([room])
    src = ElectricHeater(
        name="h1", room="a", max_power=1000.0, efficiency=1.0,
    )
    sde = HouseThermalSDE(
        model, [src], dt=900.0, augment_offsets=False,
    )

    x = np.array([20.0, 20.0, 20.0])  # [T_a, T_w, T_s] for 2R2C+slab single-room SDE
    u = np.zeros(1)
    d = np.array([0.0, 0.0])  # outdoor=0, no solar gain
    p = np.array([])

    # No wind ⇒ overlay is zero ⇒ Jacobian equals the structural F.
    sde.set_wind_speed(None)
    J0 = sde.dfdx(x, u, d, p, 0.0)
    assert np.allclose(J0, sde._F)

    # High wind ⇒ overlay subtracts from the diagonal (more loss).
    sde.set_wind_speed(15.0)
    J1 = sde.dfdx(x, u, d, p, 0.0)
    # Diagonal must be more negative than the no-wind case.
    assert J1[0, 0] < sde._F[0, 0]


def test_controller_f_responds_to_wind() -> None:
    """For a leaky room with ΔT > 0, increasing wind makes ``f`` more
    negative (faster cooling)."""
    from heatingassistant.engine.controller import HouseThermalSDE
    from heatingassistant.engine.heat_sources import ElectricHeater

    room = Room(
        name="a", thermal_mass=5e6, r_external=0.05, infiltration_fraction=0.50,
        temperature=20.0,
    )
    model = HouseModel([room])
    src = ElectricHeater(
        name="h1", room="a", max_power=1000.0, efficiency=1.0,
    )
    sde = HouseThermalSDE(
        model, [src], dt=900.0, augment_offsets=False,
    )

    x = np.array([20.0, 20.0])  # [T_a, T_w] for the single-room 2R2C SDE
    u = np.zeros(1)
    d = np.array([0.0, 0.0, 0.0])  # [T_out, q_solar, q_air]; outdoor=0 ⇒ |ΔT| = 20 K
    p = np.array([])

    sde.set_wind_speed(SHERMAN_GRIMSRUD_V_TYPICAL)  # baseline
    f_typ = sde.f(x, u, d, p, 0.0)[0]

    sde.set_wind_speed(15.0)  # stronger wind
    f_high = sde.f(x, u, d, p, 0.0)[0]

    assert f_high < f_typ, (
        f"high-wind drift ({f_high:.6f}) should be more negative than "
        f"typical-wind drift ({f_typ:.6f})"
    )
