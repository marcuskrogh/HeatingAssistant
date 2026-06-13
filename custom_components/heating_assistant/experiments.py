"""
Scheduled system-identification experiments.

A system-identification *experiment* tells the controller to deliberately
*excite* a single room's heaters with an informative input signal during a
user-chosen time window (typically overnight, when the room is unoccupied), so
that the resulting temperature response carries enough information to identify
the room's thermal model accurately.

This module is intentionally free of any Home Assistant imports apart from the
optional :class:`Store` used by :class:`ExperimentStore`, so the experiment data
model and the excitation-signal maths can be unit-tested without a HA install.

Design
------
* :class:`Experiment` is a plain, JSON-serialisable record describing one
  scheduled experiment (room, time window, signal type and parameters, safety
  bounds, and lifecycle status).
* :func:`excitation_fraction` is a pure function returning the signed input
  fraction (heat ``> 0`` / cool ``< 0``) the signal requests at a given instant.
  The default *step* signal is a multi-phase step test (heat — settle — cool —
  settle, or heat — settle for heat-only units); PRBS / pulse stop ``settle_s``
  before the window ends so their influence is absorbed within the captured
  window.  The coordinator clamps the value to each source's range and applies
  the safety temperature band (frost floor / overheat ceiling) on top.
* :class:`ExperimentManager` holds the live list of experiments and advances
  their state machine (``scheduled`` → ``running`` → ``completed``) each tick.
* :class:`ExperimentStore` persists the list across restarts.

The excitation signals are switched at a fixed ``period_s`` cadence so the bit
sequence aligns with the controller's discrete update steps.  PRBS sequences are
generated deterministically from ``seed`` so a run can be reproduced exactly and
the recorded input always matches what was applied.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .const import (
    DATASET_SOURCE_EXPERIMENT,
    DEFAULT_EXCITATION_PERIOD_S,
    DEFAULT_EXCITATION_STEP_PCT,
    DEFAULT_EXCITATION_TYPE,
    DEFAULT_EXPERIMENT_MAX_TEMP,
    DEFAULT_EXPERIMENT_MIN_TEMP,
    DEFAULT_EXPERIMENT_SETTLE_S,
    DOMAIN,
    EXCITATION_PRBS,
    EXCITATION_PULSE,
    EXCITATION_STEP,
    EXCITATION_TYPES,
)

# Experiment lifecycle states.
STATUS_SCHEDULED = "scheduled"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Deterministic PRBS bit generation
# ---------------------------------------------------------------------------

def _splitmix64(x: int) -> int:
    """One round of the SplitMix64 PRNG — a fast, well-distributed integer hash.

    Used to derive a reproducible pseudo-random bit for each switching step so
    a PRBS experiment is fully determined by ``(seed, step_index)`` and can be
    regenerated identically for analysis.
    """
    x = (x + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    z = x
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    return z ^ (z >> 31)


def prbs_bit(seed: int, step_index: int) -> int:
    """Return a reproducible pseudo-random bit (0/1) for ``step_index``.

    The bit depends only on ``seed`` and ``step_index`` so the full sequence is
    deterministic and reproducible.
    """
    return _splitmix64((int(seed) & 0xFFFFFFFFFFFFFFFF) ^ (int(step_index) * 0x100000001B3)) & 1


# ---------------------------------------------------------------------------
# Experiment data model
# ---------------------------------------------------------------------------

@dataclass
class Experiment:
    """One scheduled system-identification experiment for a single room."""

    room_name: str
    room_slug: str
    start_ts: float
    end_ts: float
    name: str = ""
    signal_type: str = DEFAULT_EXCITATION_TYPE
    step_pct: float = DEFAULT_EXCITATION_STEP_PCT
    period_s: float = DEFAULT_EXCITATION_PERIOD_S
    settle_s: float = DEFAULT_EXPERIMENT_SETTLE_S
    min_temp: float = DEFAULT_EXPERIMENT_MIN_TEMP
    max_temp: float = DEFAULT_EXPERIMENT_MAX_TEMP
    auto_save: bool = True
    seed: int = field(default_factory=lambda: uuid.uuid4().int & 0xFFFFFFFFFFFFFFFF)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = STATUS_SCHEDULED
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    dataset_id: Optional[str] = None

    # -- serialisation ----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Experiment":
        # Tolerate unknown keys from a newer/older schema by filtering to the
        # known field names; defaults fill anything missing.
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    # -- lifecycle helpers ------------------------------------------------

    def is_terminal(self) -> bool:
        return self.status in (STATUS_COMPLETED, STATUS_CANCELLED)

    @property
    def excitation_end_ts(self) -> float:
        """Instant at which *active* excitation stops, ahead of ``end_ts``.

        A settle / response buffer of ``settle_s`` is reserved at the tail of the
        window so the heater's influence can be absorbed by the room *within* the
        captured window.  The buffer is clamped so it can never swallow the whole
        window (which would leave no excitation at all).
        """
        settle = self.settle_s if self.settle_s and self.settle_s > 0.0 else 0.0
        settle = min(settle, max(0.0, self.end_ts - self.start_ts))
        return self.end_ts - settle

    def is_active_at(self, now_ts: float) -> bool:
        """Whether the experiment owns the room's heaters at ``now_ts``.

        Stays true through the settle buffer: the controller keeps holding the
        heaters at the low level there (rather than handing back to the MPC,
        which would re-heat and contaminate the recorded relaxation).
        """
        if self.status in (STATUS_COMPLETED, STATUS_CANCELLED):
            return False
        return self.start_ts <= now_ts < self.end_ts

    def is_exciting_at(self, now_ts: float) -> bool:
        """Whether *active* excitation (not the settle tail) is driving heaters."""
        if self.is_terminal():
            return False
        return self.start_ts <= now_ts < self.excitation_end_ts

    def is_settling_at(self, now_ts: float) -> bool:
        """Whether ``now_ts`` falls in the post-excitation settle/response tail."""
        if self.is_terminal():
            return False
        return self.excitation_end_ts <= now_ts < self.end_ts


# ---------------------------------------------------------------------------
# Excitation signal
# ---------------------------------------------------------------------------

def excitation_fraction(exp: Experiment, now_ts: float, can_cool: bool = False) -> float:
    """Return the *signed* input fraction the signal requests now.

    Positive values are a fraction of the source's max heat power; negative
    values are a fraction of its max cool power (only emitted for cool-capable
    sources, via ``can_cool``).  Outside the window the signal rests at 0 (no
    input).  This is the *unclamped* value; the coordinator clamps it to each
    source's actuation range and applies the safety temperature band on top.
    """
    if now_ts < exp.start_ts or now_ts >= exp.end_ts:
        return 0.0

    pct = _clamp01(exp.step_pct)

    if exp.signal_type == EXCITATION_STEP:
        return _step_pattern_fraction(exp, now_ts, pct, can_cool)

    # PRBS / pulse switch between 0 and the step magnitude, and rest at 0 during
    # the trailing settle / response buffer so the room relaxes within the window.
    if now_ts >= exp.excitation_end_ts:
        return 0.0
    period = exp.period_s if exp.period_s and exp.period_s > 0 else DEFAULT_EXCITATION_PERIOD_S
    step_index = int((now_ts - exp.start_ts) // period)
    if exp.signal_type == EXCITATION_PULSE:
        return pct if (step_index % 2 == 0) else 0.0
    # Default / PRBS.
    return pct if prbs_bit(exp.seed, step_index) else 0.0


def _step_pattern_fraction(
    exp: Experiment, now_ts: float, step_pct: float, can_cool: bool
) -> float:
    """Signed level of the multi-phase step test at ``now_ts``.

    The window is split into equal phases following ``0 → +1 → 0 → −1 → 0`` for
    cool-capable sources (heat, settle, cool, settle) or ``0 → +1 → 0`` for
    heat-only sources, where ``±1`` is ``±step_pct``.  The interleaved zeros let
    the room settle between steps so each direction's response is clean.
    """
    duration = exp.end_ts - exp.start_ts
    if duration <= 0.0:
        return 0.0
    levels = (
        (0.0, step_pct, 0.0, -step_pct, 0.0)
        if can_cool
        else (0.0, step_pct, 0.0)
    )
    n = len(levels)
    idx = int((now_ts - exp.start_ts) / (duration / n))
    if idx < 0:
        idx = 0
    elif idx >= n:
        idx = n - 1
    return levels[idx]


def apply_safety_bounds(
    fraction: float,
    measured_temp: Optional[float],
    min_temp: float,
    max_temp: float,
    force_level: float,
) -> float:
    """Clamp an excitation ``fraction`` against the room's safety temperature band.

    * Below ``min_temp`` heating is forced on to ``force_level`` (frost protection).
    * At/above ``max_temp`` the input is forced off (prevents overheating).
    * Otherwise the requested ``fraction`` passes through unchanged.

    A missing measurement (``None``) leaves the requested fraction untouched so a
    transient sensor drop-out does not abort the excitation.
    """
    if measured_temp is None:
        return fraction
    if measured_temp <= min_temp:
        return _clamp01(force_level)
    if measured_temp >= max_temp:
        return 0.0
    return fraction


def _clamp01(x: float) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def validate_signal_params(
    signal_type: str,
    step_pct: float,
    period_s: float,
) -> None:
    """Raise ``ValueError`` if the excitation parameters are not usable."""
    if signal_type not in EXCITATION_TYPES:
        raise ValueError(
            f"signal_type must be one of {EXCITATION_TYPES}, got {signal_type!r}"
        )
    if not (0.0 < float(step_pct) <= 1.0):
        raise ValueError("step_pct must be in (0, 1]")
    if period_s <= 0:
        raise ValueError("period_s must be positive")


# ---------------------------------------------------------------------------
# Experiment manager (live state machine)
# ---------------------------------------------------------------------------

class ExperimentManager:
    """Holds the live list of experiments and advances their state machine.

    Pure in-memory logic — persistence is delegated to :class:`ExperimentStore`.
    """

    def __init__(self, experiments: Optional[List[Experiment]] = None) -> None:
        self._experiments: List[Experiment] = list(experiments or [])

    # -- accessors --------------------------------------------------------

    @property
    def experiments(self) -> List[Experiment]:
        return list(self._experiments)

    def get(self, experiment_id: str) -> Optional[Experiment]:
        for exp in self._experiments:
            if exp.id == experiment_id:
                return exp
        return None

    def for_room(self, room_slug: str) -> List[Experiment]:
        return [e for e in self._experiments if e.room_slug == room_slug]

    def active_for_room(self, room_slug: str, now_ts: float) -> Optional[Experiment]:
        """Return the experiment currently exciting ``room_slug`` (or ``None``).

        At most one experiment per room can be active at a time; when several
        overlap (which the scheduler tries to prevent) the earliest-starting one
        wins for determinism.
        """
        candidates = [
            e
            for e in self._experiments
            if e.room_slug == room_slug and e.is_active_at(now_ts)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda e: e.start_ts)

    def has_active(self, now_ts: float) -> bool:
        return any(e.is_active_at(now_ts) for e in self._experiments)

    # -- mutation ---------------------------------------------------------

    def add(self, exp: Experiment) -> Experiment:
        self._experiments.append(exp)
        return exp

    def cancel(self, experiment_id: str) -> Optional[Experiment]:
        exp = self.get(experiment_id)
        if exp is None or exp.is_terminal():
            return None
        exp.status = STATUS_CANCELLED
        exp.completed_at = time.time()
        return exp

    def remove(self, experiment_id: str) -> bool:
        before = len(self._experiments)
        self._experiments = [e for e in self._experiments if e.id != experiment_id]
        return len(self._experiments) != before

    def prune_terminal(self, keep: int = 20) -> None:
        """Drop the oldest terminal experiments beyond ``keep`` so the list stays
        bounded.  Active and scheduled experiments are always kept."""
        terminal = [e for e in self._experiments if e.is_terminal()]
        if len(terminal) <= keep:
            return
        terminal.sort(key=lambda e: e.completed_at or e.created_at)
        drop_ids = {e.id for e in terminal[: len(terminal) - keep]}
        self._experiments = [e for e in self._experiments if e.id not in drop_ids]

    def advance(self, now_ts: float) -> Dict[str, List[Experiment]]:
        """Advance lifecycle states and report transitions.

        Returns a dict with ``"started"`` and ``"completed"`` lists naming the
        experiments that transitioned on this call, so the coordinator can react
        (e.g. snapshot a dataset for a just-completed experiment).
        """
        started: List[Experiment] = []
        completed: List[Experiment] = []
        for exp in self._experiments:
            if exp.status == STATUS_SCHEDULED and exp.start_ts <= now_ts < exp.end_ts:
                exp.status = STATUS_RUNNING
                exp.started_at = now_ts
                started.append(exp)
            if exp.status in (STATUS_SCHEDULED, STATUS_RUNNING) and now_ts >= exp.end_ts:
                exp.status = STATUS_COMPLETED
                exp.completed_at = now_ts
                completed.append(exp)
        return {"started": started, "completed": completed}

    # -- serialisation ----------------------------------------------------

    def to_list(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._experiments]

    @classmethod
    def from_list(cls, data: Any) -> "ExperimentManager":
        experiments: List[Experiment] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    try:
                        experiments.append(Experiment.from_dict(item))
                    except (TypeError, ValueError):
                        continue
        return cls(experiments)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class ExperimentStore:
    """Persists the experiment list across restarts via HA's ``Store`` helper."""

    def __init__(self, hass: Any, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store

        self._store = Store(hass, version=1, key=f"{DOMAIN}_experiments_{entry_id}")

    async def async_load(self) -> ExperimentManager:
        try:
            data = await self._store.async_load()
        except Exception:  # pragma: no cover - defensive
            data = None
        if isinstance(data, dict):
            data = data.get("experiments")
        return ExperimentManager.from_list(data)

    async def async_save(self, manager: ExperimentManager) -> None:
        await self._store.async_save({"experiments": manager.to_list()})
