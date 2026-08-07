"""Build patched thermal models for sysid / open-loop simulation."""

from __future__ import annotations

import copy
from typing import Any, Dict, List

# Per-room thermal-model attributes that a sysid / open-loop run may override
# from the System Identification panel.  Each maps a service-data / room_params
# key to the corresponding ``Room`` attribute so the simulation reflects exactly
# the values the user has entered, without applying them to the live model.
ROOM_PARAM_ATTRS = (
    "thermal_mass",
    "r_external",
    "internal_gain",
    "solar_scale",
    "c_air_fraction",
    "r_aw_fraction",
)


def build_sim_model(
    live_model: Any,
    room_params: Dict[str, Dict[str, float]],
    room_names: List[str],
) -> Any:
    """Return a deep copy of *live_model* with *room_params* overrides applied.

    Every per-room thermal-model parameter the identification panel exposes
    (``thermal_mass``, ``r_external``, ``internal_gain``, ``solar_scale`` and
    the 2R2C envelope split ``c_air_fraction`` / ``r_aw_fraction``) is applied
    when present so the reconstruction / open-loop simulation uses the full set
    of values currently shown in the UI, not just C and R_ext.
    """
    sim_model = copy.deepcopy(live_model)
    for name in room_names:
        overrides = room_params.get(name, {})
        if not overrides:
            continue
        room = sim_model.rooms.get(name)
        if room is None:
            continue
        for attr in ROOM_PARAM_ATTRS:
            if attr in overrides:
                setattr(room, attr, float(overrides[attr]))

    # Rebuild cached matrices so HouseThermalSDE picks up the new parameters.
    sim_model.rebuild_derived_parameters()
    C, A, B = sim_model._build_matrices()
    sim_model._C     = C
    sim_model._A     = A
    sim_model._B_ext = B

    return sim_model
