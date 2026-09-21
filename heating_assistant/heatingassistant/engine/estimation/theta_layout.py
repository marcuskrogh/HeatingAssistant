"""Packed parameter vector layout for grey-box estimation."""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np


class _ThetaLayout:
    """
    Index layout of the packed parameter vector ``θ`` (1R1C):

        [ log_mass_1..n
          log_r_ext_1..n
          q_int_1..n
          log_alpha_{s_k} for s_k in identifiable_sources
          log_r_ij_{p_k} for p_k in identifiable_pairs
          log_solar_{i_k} for i_k in identifiable_solar
          ua_open_{i_k}  for i_k in identifiable_ua       (linear space, ≥ 0) ]

    The first three blocks always exist (one entry per room); the gated
    blocks are present only for the rooms / sources / pairs that passed
    their identifiability gates, so an old 3n-element θ remains a valid
    layout with all gates closed.  ``ua_open`` is appended last so
    HouseThermalSDE.f offsets for q_int / α stay at ``3n``.

    Split fractions and ``t_wall_init`` are not identified (live plant is
    1R1C).  ``identifiable_splits`` / ``n_wall_segs`` are accepted and
    ignored so older call sites keep compiling.
    """

    def __init__(
        self,
        n_rooms: int,
        identifiable_sources: List[int],
        identifiable_pairs: List[Tuple[int, int]],
        identifiable_solar: Optional[List[int]] = None,
        identifiable_splits: Optional[List[int]] = None,
        identifiable_ua: Optional[List[int]] = None,
        n_wall_segs: int = 1,
    ) -> None:
        self.n_rooms = n_rooms
        self.identifiable_sources = list(identifiable_sources)
        self.identifiable_pairs = list(identifiable_pairs)
        self.identifiable_solar = list(identifiable_solar or [])
        # 1R1C: envelope splits and wall ICs are not in θ.
        self.identifiable_splits: List[int] = []
        self.identifiable_ua = list(identifiable_ua or [])
        self.n_wall_segs = 0
        _ = identifiable_splits
        _ = n_wall_segs

        n = n_rooms
        self.idx_log_mass = (0, n)
        self.idx_log_r = (n, 2 * n)
        self.idx_q_int = (2 * n, 3 * n)
        self.idx_t_wall_init = (3 * n, 3 * n)

        off = 3 * n
        self.idx_log_alpha = (off, off + len(identifiable_sources))
        off = self.idx_log_alpha[1]
        self.idx_log_r_ij = (off, off + len(identifiable_pairs))
        off = self.idx_log_r_ij[1]
        self.idx_log_solar = (off, off + len(self.identifiable_solar))
        off = self.idx_log_solar[1]
        self.idx_c_air = (off, off)
        self.idx_r_aw = (off, off)
        self.idx_ua_open = (off, off + len(self.identifiable_ua))

        self.size = self.idx_ua_open[1]

    def get_t_wall_seg(self, theta: np.ndarray, seg: int) -> np.ndarray:
        """Empty: wall ICs are not in θ."""
        return np.zeros(self.n_rooms, dtype=float)

    def unpack(self, theta: np.ndarray):
        a, b = self.idx_log_mass
        log_mass = theta[a:b]
        a, b = self.idx_log_r
        log_r = theta[a:b]
        a, b = self.idx_q_int
        q_int = theta[a:b]
        t_wall_init = np.zeros(0, dtype=float)
        a, b = self.idx_log_alpha
        log_alpha = theta[a:b]
        a, b = self.idx_log_r_ij
        log_r_ij = theta[a:b]
        a, b = self.idx_log_solar
        log_solar = theta[a:b]
        c_air = np.zeros(0, dtype=float)
        r_aw = np.zeros(0, dtype=float)
        return (
            log_mass, log_r, q_int, t_wall_init, log_alpha, log_r_ij,
            log_solar, c_air, r_aw,
        )

    def get_ua_open(self, theta: np.ndarray) -> np.ndarray:
        """Return the gated UA_open block (empty when no room is identifiable)."""
        a, b = self.idx_ua_open
        return theta[a:b]
