"""Multi-sensor averaging helpers."""

from __future__ import annotations

from collections.abc import Mapping
from numbers import Real
from typing import Any


def _status_for(tag: str, statuses: Mapping[str, Any] | None) -> str | None:
    if statuses is None:
        return None
    status = statuses.get(tag)
    if isinstance(status, str):
        return status
    return getattr(status, "status", None)


def average_numeric_tags(
    values: dict[str, float | None],
    statuses: Mapping[str, Any] | None,
) -> float | None:
    """Return the mean of numeric values whose status is GOOD.

    BAD, UNCERTAIN, missing-status, ``None``, and non-numeric values are skipped.
    ``None`` is returned when no numeric GOOD values remain.
    """

    good_values: list[float] = []
    for tag, value in values.items():
        if _status_for(tag, statuses) != "GOOD":
            continue
        if value is None or isinstance(value, bool) or not isinstance(value, Real):
            continue
        good_values.append(float(value))

    if not good_values:
        return None
    return sum(good_values) / len(good_values)
