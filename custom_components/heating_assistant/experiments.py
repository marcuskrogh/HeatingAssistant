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
* :func:`excitation_fraction` is a pure function returning the heater power
  fraction (0–1) the signal requests at a given instant.  The coordinator then
  applies safety clamps (frost floor / overheat ceiling) on top of it.
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
    DEFAULT_EXCITATION_HIGH,
    DEFAULT_EXCITATION_LOW,
    DEFAULT_EXCITATION_PERIOD_S,
    DEFAULT_EXCITATION_TYPE,
    DEFAULT_EXPERIMENT_MAX_TEMP,
    DEFAULT_EXPERIMENT_MIN_TEMP,
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
    amplitude_high: float = DEFAULT_EXCITATION_HIGH
    amplitude_low: float = DEFAULT_EXCITATION_LOW
    period_s: float = DEFAULT_EXCITATION_PERIOD_S
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

    def is_active_at(self, now_ts: float) -> bool:
        """Whether the experiment should be driving heaters at ``now_ts``."""
        if self.status in (STATUS_COMPLETED, STATUS_CANCELLED):
            return False
        return self.start_ts <= now_ts < self.end_ts


# ---------------------------------------------------------------------------
# Excitation signal
# ---------------------------------------------------------------------------

def excitation_fraction(exp: Experiment, now_ts: float) -> float:
    """Return the raw heater power fraction (0–1) the signal requests now.

    This is the *unclamped* signal value; the coordinator applies the safety
    temperature bounds on top of it.  Before the window starts (or after it
    ends) the low amplitude is returned.
    """
    high = _clamp01(exp.amplitude_high)
    low = _clamp01(exp.amplitude_low)
    elapsed = now_ts - exp.start_ts
    if elapsed < 0.0 or now_ts >= exp.end_ts:
        return low

    period = exp.period_s if exp.period_s and exp.period_s > 0 else DEFAULT_EXCITATION_PERIOD_S
    step_index = int(elapsed // period)

    if exp.signal_type == EXCITATION_STEP:
        return high
    if exp.signal_type == EXCITATION_PULSE:
        return high if (step_index % 2 == 0) else low
    # Default / PRBS.
    return high if prbs_bit(exp.seed, step_index) else low


def apply_safety_bounds(
    fraction: float,
    measured_temp: Optional[float],
    min_temp: float,
    max_temp: float,
    high: float,
) -> float:
    """Clamp an excitation ``fraction`` against the room's safety temperature band.

    * Below ``min_temp`` the heater is forced to ``high`` (frost protection).
    * At/above ``max_temp`` the heater is forced off (prevents overheating).
    * Otherwise the requested ``fraction`` passes through unchanged.

    A missing measurement (``None``) leaves the requested fraction untouched so a
    transient sensor drop-out does not abort the excitation.
    """
    if measured_temp is None:
        return fraction
    if measured_temp <= min_temp:
        return _clamp01(high)
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
    amplitude_high: float,
    amplitude_low: float,
    period_s: float,
) -> None:
    """Raise ``ValueError`` if the excitation parameters are not usable."""
    if signal_type not in EXCITATION_TYPES:
        raise ValueError(
            f"signal_type must be one of {EXCITATION_TYPES}, got {signal_type!r}"
        )
    hi = _clamp01(amplitude_high)
    lo = _clamp01(amplitude_low)
    if hi <= lo:
        raise ValueError("amplitude_high must be greater than amplitude_low")
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
