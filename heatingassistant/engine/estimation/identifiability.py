"""Identifiability gates for grey-box parameter estimation."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np

from .constants import (
    MIN_HISTORY_STEPS,
    _ALPHA_PRIOR_WEIGHT,
    _ALPHA_PRIOR_WEIGHT_EXCITED,
    _MIN_HEATER_USAGE_STD,
    _MIN_SOLAR_STD,
    _MIN_TEMP_DIFF_STD,
)


def _check_identifiable_connections(
    history: List[Dict[str, Any]],
    room_names: List[str],
    connections: List[Tuple[int, int]],
    min_std: float = _MIN_TEMP_DIFF_STD,
    min_history_steps: int = MIN_HISTORY_STEPS,
) -> List[Tuple[int, int]]:
    """
    Return the subset of room-index pairs (i, j) for which the inter-room
    temperature difference has sufficient variance for R_ij to be identifiable.
    """
    if len(history) < min_history_steps:
        return []

    identifiable = []
    for i, j in connections:
        diffs = []
        for record in history:
            y = record.get("y", [])
            if i < len(y) and j < len(y):
                diffs.append(float(y[i]) - float(y[j]))
        if len(diffs) >= min_history_steps and float(np.std(diffs)) > min_std:
            identifiable.append((i, j))
    return identifiable


def _check_identifiable_sources(
    history: List[Dict[str, Any]],
    n_sources: int,
    min_std: float = _MIN_HEATER_USAGE_STD,
    min_history_steps: int = MIN_HISTORY_STEPS,
) -> List[int]:
    """
    Return the indices of heat sources whose duty-cycle ``u`` shows enough
    variation for the power-scale parameter α_s to be identifiable.
    """
    if len(history) < min_history_steps:
        return []

    identifiable = []
    for s in range(n_sources):
        u_vals = []
        for record in history:
            u = record.get("u", [])
            if s < len(u):
                u_vals.append(float(u[s]))
        if len(u_vals) >= min_history_steps and float(np.std(u_vals)) > min_std:
            identifiable.append(s)
    return identifiable


def _check_identifiable_solar(
    history: List[Dict[str, Any]],
    room_names: List[str],
    min_std: float = _MIN_SOLAR_STD,
    min_history_steps: int = MIN_HISTORY_STEPS,
) -> List[int]:
    """
    Return the room indices whose recorded solar-gain disturbance varies
    enough for the per-room solar scale ``s_i`` to be identifiable.
    """
    if len(history) < min_history_steps:
        return []

    identifiable = []
    for i, name in enumerate(room_names):
        gains = [
            float(record.get("d_solar", {}).get(name, 0.0))
            for record in history
        ]
        if len(gains) >= min_history_steps and float(np.std(gains)) > min_std:
            identifiable.append(i)
    return identifiable


def _check_identifiable_open_ua(
    history: List[Dict[str, Any]],
    room_names: List[str],
    min_open_steps: int,
) -> List[int]:
    """Return room indices with enough open-contact samples to identify UA_open.

    ``min_open_steps`` is the estimator's existing segment minimum
    (``N_min``).  Rooms below that bar keep SWD-322 exclusion and
    ``UA_open = 0``.
    """
    if min_open_steps <= 0 or not history:
        return []
    counts = [0] * len(room_names)
    for record in history:
        wo = record.get("window_open") or {}
        for i, name in enumerate(room_names):
            if wo.get(name, False):
                counts[i] += 1
    return [i for i, count in enumerate(counts) if count >= min_open_steps]


def _identifiable_split_rooms(
    identifiable_sources: List[int],
    sources: List[Any],
    room_names: List[str],
) -> List[int]:
    """
    Return the room indices whose 2R2C split fractions are identifiable.

    The fast/slow split is only visible through heater step responses, so a
    room qualifies when at least one of its own heat sources passed the
    duty-cycle excitation gate.  Passive rooms keep their typology defaults.
    """
    rooms_with_excited_source = {
        getattr(sources[s], "room", None) for s in identifiable_sources
    }
    return [
        i for i, name in enumerate(room_names)
        if name in rooms_with_excited_source
    ]


def _heater_excitation_score(
    history: List[Dict[str, Any]],
    n_u: int,
    min_history_steps: int = MIN_HISTORY_STEPS,
) -> float:
    """Return the maximum std of any source duty cycle in ``history``."""
    if len(history) < min_history_steps or n_u <= 0:
        return 0.0
    u_matrix = []
    for rec in history:
        u = rec.get("u", [])
        if not u:
            continue
        row = [float(u[j]) if j < len(u) else 0.0 for j in range(n_u)]
        u_matrix.append(row)
    if len(u_matrix) < 2:
        return 0.0
    arr = np.asarray(u_matrix, dtype=float)
    return float(np.max(np.std(arr, axis=0)))


def _adaptive_alpha_prior_weight(
    history: List[Dict[str, Any]],
    n_u: int,
    min_history_steps: int = MIN_HISTORY_STEPS,
) -> float:
    """Weaker α prior when heater duty cycle varies enough to identify scale."""
    score = _heater_excitation_score(history, n_u, min_history_steps)
    if score >= _MIN_HEATER_USAGE_STD:
        return _ALPHA_PRIOR_WEIGHT_EXCITED
    return _ALPHA_PRIOR_WEIGHT
