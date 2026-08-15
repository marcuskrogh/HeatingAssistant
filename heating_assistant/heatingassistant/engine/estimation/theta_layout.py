"""Packed parameter vector layout for grey-box estimation."""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np


class _ThetaLayout:
    """
    Index layout of the packed parameter vector ``θ``:

        [ log_mass_1..n
          log_r_ext_1..n
          q_int_1..n
          t_wall_init_seg0_1..n          (first dataset / single dataset)
          [t_wall_init_seg1_1..n]        (second dataset, when n_wall_segs>1)
          ...
          log_alpha_{s_k} for s_k in identifiable_sources
          log_r_ij_{p_k} for p_k in identifiable_pairs
          log_solar_{i_k} for i_k in identifiable_solar
          c_air_{i_k}    for i_k in identifiable_splits   (linear space)
          r_aw_{i_k}     for i_k in identifiable_splits   (linear space)
          ua_open_{i_k}  for i_k in identifiable_ua       (linear space, ≥ 0) ]

    The first three blocks always exist (one entry per room); the gated
    blocks are present only for the rooms / sources / pairs that passed
    their identifiability gates, so an old 3n-element θ remains a valid
    layout with all gates closed.  ``n_wall_segs`` controls how many
    dataset-level initial-wall-temperature blocks are included; each block
    covers all rooms.  ``ua_open`` is appended last so HouseThermalSDE.f
    offsets for q_int / α are unchanged.
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
        self.identifiable_splits = list(identifiable_splits or [])
        self.identifiable_ua = list(identifiable_ua or [])
        self.n_wall_segs = max(1, int(n_wall_segs))

        n = n_rooms
        self.idx_log_mass = (0, n)
        self.idx_log_r = (n, 2 * n)
        self.idx_q_int = (2 * n, 3 * n)
        # t_wall_init block: n_wall_segs blocks of n rooms each.
        self.idx_t_wall_init = (3 * n, 3 * n + self.n_wall_segs * n)

        off = 3 * n + self.n_wall_segs * n
        self.idx_log_alpha = (off, off + len(identifiable_sources))
        off = self.idx_log_alpha[1]
        self.idx_log_r_ij = (off, off + len(identifiable_pairs))
        off = self.idx_log_r_ij[1]
        self.idx_log_solar = (off, off + len(self.identifiable_solar))
        off = self.idx_log_solar[1]
        self.idx_c_air = (off, off + len(self.identifiable_splits))
        off = self.idx_c_air[1]
        self.idx_r_aw = (off, off + len(self.identifiable_splits))
        off = self.idx_r_aw[1]
        self.idx_ua_open = (off, off + len(self.identifiable_ua))

        self.size = self.idx_ua_open[1]

    def get_t_wall_seg(self, theta: np.ndarray, seg: int) -> np.ndarray:
        """Return the t_wall_init block for dataset segment ``seg``."""
        n = self.n_rooms
        a = self.idx_t_wall_init[0] + seg * n
        return theta[a: a + n]

    def unpack(self, theta: np.ndarray):
        a, b = self.idx_log_mass
        log_mass = theta[a:b]
        a, b = self.idx_log_r
        log_r = theta[a:b]
        a, b = self.idx_q_int
        q_int = theta[a:b]
        # Return FIRST segment's wall temps for backward compatibility.
        a = self.idx_t_wall_init[0]
        t_wall_init = theta[a: a + self.n_rooms]
        a, b = self.idx_log_alpha
        log_alpha = theta[a:b]
        a, b = self.idx_log_r_ij
        log_r_ij = theta[a:b]
        a, b = self.idx_log_solar
        log_solar = theta[a:b]
        a, b = self.idx_c_air
        c_air = theta[a:b]
        a, b = self.idx_r_aw
        r_aw = theta[a:b]
        return (
            log_mass, log_r, q_int, t_wall_init, log_alpha, log_r_ij,
            log_solar, c_air, r_aw,
        )

    def get_ua_open(self, theta: np.ndarray) -> np.ndarray:
        """Return the gated UA_open block (empty when no room is identifiable)."""
        a, b = self.idx_ua_open
        return theta[a:b]
