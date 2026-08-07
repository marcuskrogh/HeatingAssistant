"""Build grey-box thermal models from the estimation parameter vector θ."""

from __future__ import annotations

import copy
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..controller import HouseThermalSDE as HouseThermalSystem
from ..thermal_model import HouseModel, Room
from .constants import (
    _C_AIR_HI,
    _C_AIR_LO,
    _LOG_R_IJ_HI,
    _LOG_R_IJ_LO,
    _LOG_SOLAR_HI,
    _LOG_SOLAR_LO,
    _R_AW_HI,
    _R_AW_LO,
)
from .theta_layout import _ThetaLayout

_LOGGER = logging.getLogger(__name__)


def _theta_model_quantities(
    est: Any,
    layout: _ThetaLayout,
    theta: np.ndarray,
) -> Dict[str, np.ndarray]:
    """Per-room physical quantities implied by θ, for the sensitivity pass.

    Mirrors the parametrisation in :meth:`Room.conductances` /
    :func:`_build_rooms_from_theta`: total mass and UA from the
    log-space θ entries, split fractions and solar scales from the
    gated θ entries (configured values for ungated rooms).
    """
    from ..const import (  # noqa: PLC0415
        MAX_INFILTRATION_FRACTION,
        SOLAR_WALL_FRACTION,
    )
    (log_mass, log_r, _q_int, _t_wall_init, log_alpha, log_r_ij,
     log_solar, c_air, r_aw) = layout.unpack(theta)
    n = est._n

    C_tot = np.exp(log_mass)
    ua = np.exp(-log_r)

    fc = est._c_air_prior_full.copy()
    rf = est._r_aw_prior_full.copy()
    for k, i in enumerate(layout.identifiable_splits):
        fc[i] = float(np.clip(c_air[k], _C_AIR_LO, _C_AIR_HI))
        rf[i] = float(np.clip(r_aw[k], _R_AW_LO, _R_AW_HI))
    s = np.exp(est._log_solar_prior_full).copy()
    for k, i in enumerate(layout.identifiable_solar):
        s[i] = math.exp(float(np.clip(log_solar[k], _LOG_SOLAR_LO, _LOG_SOLAR_HI)))

    f_inf = np.array([
        float(np.clip(getattr(r, "infiltration_fraction", 0.0),
                      0.0, MAX_INFILTRATION_FRACTION))
        for r in est._rooms
    ])
    facade = np.array([
        float(getattr(r, "facade_solar_share", 0.0))
        * float(getattr(r, "facade_absorptance", 0.0))
        for r in est._rooms
    ])

    C_a = fc * C_tot
    C_w = (1.0 - fc) * C_tot
    g_inf = f_inf * ua
    g_cond = (1.0 - f_inf) * ua
    g_aw = g_cond / rf
    g_we = g_cond / (1.0 - rf)

    heater_scales = np.ones(est._n_u)
    for k_la, s_idx in enumerate(layout.identifiable_sources):
        heater_scales[s_idx] = float(np.exp(log_alpha[k_la]))
    g_ij_vec = np.exp(-log_r_ij) if len(log_r_ij) else np.array([])

    return {
        "C_a": C_a, "C_w": C_w, "fc": fc, "rf": rf, "s": s,
        "g_inf": g_inf, "g_aw": g_aw, "g_we": g_we,
        "facade": facade, "wall_frac": float(SOLAR_WALL_FRACTION),
        "heater_scales": heater_scales, "g_ij": g_ij_vec,
    }


def _build_rooms_from_theta(
    est: Any,
    layout: _ThetaLayout,
    log_mass: np.ndarray,
    log_r: np.ndarray,
    log_r_ij_map: Optional[Dict[Tuple[int, int], float]],
    log_solar: Optional[np.ndarray] = None,
    c_air: Optional[np.ndarray] = None,
    r_aw: Optional[np.ndarray] = None,
) -> List[Room]:
    """Construct Room objects with θ-dependent parameters baked in.

    Gated parameters (inter-room R, solar scale, envelope splits) take
    their θ values for the identified rooms/pairs and the configured
    values elsewhere.  ``internal_gain`` stays 0 — q_int is applied
    through the θ parameter vector in ``HouseThermalSDE.f``.
    """
    room_idx = {r.name: k for k, r in enumerate(est._rooms)}

    solar_scales = np.exp(est._log_solar_prior_full).copy()
    if log_solar is not None and len(log_solar):
        for k, i in enumerate(layout.identifiable_solar):
            solar_scales[i] = math.exp(float(np.clip(
                log_solar[k], _LOG_SOLAR_LO, _LOG_SOLAR_HI,
            )))
    c_air_full = est._c_air_prior_full.copy()
    r_aw_full = est._r_aw_prior_full.copy()
    if c_air is not None and len(c_air):
        for k, i in enumerate(layout.identifiable_splits):
            c_air_full[i] = float(np.clip(c_air[k], _C_AIR_LO, _C_AIR_HI))
            r_aw_full[i] = float(np.clip(r_aw[k], _R_AW_LO, _R_AW_HI))

    new_rooms = []
    for i, r in enumerate(est._rooms):
        new_conns = copy.deepcopy(r.connections)
        if log_r_ij_map:
            for conn in new_conns:
                j = room_idx.get(conn.connected_room)
                if j is not None:
                    pair = (min(i, j), max(i, j))
                    if pair in log_r_ij_map:
                        conn.r_value = float(math.exp(
                            float(np.clip(
                                log_r_ij_map[pair],
                                _LOG_R_IJ_LO, _LOG_R_IJ_HI,
                            ))
                        ))
        new_rooms.append(Room(
            name=r.name,
            thermal_mass=float(math.exp(log_mass[i])),
            r_external=float(math.exp(log_r[i])),
            connections=new_conns,
            windows=r.windows,
            temperature=r.temperature,
            setpoint=r.setpoint,
            # Internal gain is applied via the θ parameter vector during
            # the forward pass; keep the rebuilt model neutral.
            internal_gain=0.0,
            solar_scale=float(solar_scales[i]),
            c_air_fraction=float(c_air_full[i]),
            r_aw_fraction=float(r_aw_full[i]),
            # Configured typology presets — not identified.
            infiltration_fraction=r.infiltration_fraction,
            sky_radiative_ua=r.sky_radiative_ua,
            facade_absorptance=r.facade_absorptance,
            facade_solar_share=r.facade_solar_share,
            thermal_bridge_psi_l=r.thermal_bridge_psi_l,
        ))
    return new_rooms


def _build_system(
    est: Any,
    log_masses: np.ndarray,
    log_rs: np.ndarray,
    log_r_ij: Optional[Dict[Tuple[int, int], float]] = None,
) -> Optional[HouseThermalSystem]:
    """Build a :class:`HouseThermalSystem` for the supplied parameters.

    Internal-gain and heater-scale parameters are *not* baked into the
    rebuilt system; they are applied externally during the Kalman
    forward pass.  Solar scales and envelope splits stay at their
    configured values (this entry point predates those parameters and
    is kept for callers that only vary C / R / R_ij).
    """
    try:
        layout = _ThetaLayout(est._n, [], [])
        new_rooms = _build_rooms_from_theta(
            est, layout, log_masses, log_rs, log_r_ij,
        )
        model = HouseModel(new_rooms)
        return HouseThermalSystem(model, est._sources, est._dt)
    except Exception as exc:
        _LOGGER.debug("Failed to build thermal system: %s", exc, exc_info=True)
        return None


def _build_parametric_system(
    est: Any,
    layout: _ThetaLayout,
    theta: np.ndarray,
) -> Optional[HouseThermalSystem]:
    """
    Build a parametric HouseThermalSDE from theta for estimation.

    All structural parameters (C, R_ext, R_ij, solar scale, envelope
    splits) are baked into the model matrices; q_int and the heater
    scales remain in θ and are applied inside ``HouseThermalSDE.f``.
    """
    try:
        (log_mass, log_r, _q_int, _t_wall_init, _log_alpha, log_r_ij,
         log_solar, c_air, r_aw) = layout.unpack(theta)

        log_r_ij_map: Optional[Dict[Tuple[int, int], float]] = None
        if len(log_r_ij):
            log_r_ij_map = {
                pair: float(log_r_ij[k])
                for k, pair in enumerate(layout.identifiable_pairs)
            }

        new_rooms = _build_rooms_from_theta(
            est, layout, log_mass, log_r, log_r_ij_map,
            log_solar=log_solar, c_air=c_air, r_aw=r_aw,
        )
        model = HouseModel(new_rooms)
        # HouseThermalSystem._get_heater_scales expects the OLD layout:
        #   [log_mass(n), log_r(n), q_int(n), log_alpha(*), ...]
        # Our _ThetaLayout inserts t_wall_init at [3n, 4n), so we strip
        # that block before handing theta to the model, ensuring the
        # hardcoded offset 3*n lands on log_alpha as expected.
        a_tw, b_tw = layout.idx_t_wall_init
        theta_for_model = np.concatenate([theta[:a_tw], theta[b_tw:]])
        return HouseThermalSystem(
            model, est._sources, est._dt,
            sigma_w=math.sqrt(est._Q_var),
            sigma_v=math.sqrt(est._R_var),
            augment_offsets=False,
            n_int_steps=10,
            identifiable_sources=layout.identifiable_sources,
            theta=theta_for_model,
        )
    except Exception as exc:
        _LOGGER.debug("Failed to build parametric system: %s", exc, exc_info=True)
        return None
