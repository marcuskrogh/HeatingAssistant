"""Next-fit PE data-coverage categories for the Parameter Estimation page."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from .constants import MIN_HISTORY_STEPS, _MIN_HISTORY_TIME_S
from .identifiability import (
    _check_identifiable_solar,
    _check_identifiable_sources,
    _identifiable_split_rooms,
)

#: Recommended closed-window duration for envelope C,R [s].
CLOSED_RECOMMEND_S = 12 * 3600
#: Hint: a full day of closed data is better.
CLOSED_HINT_S = 24 * 3600
#: Recommended open-contact duration for extra UA [s].
OPEN_RECOMMEND_S = 30 * 60

STATUS_CHECKED = "checked"
STATUS_UNCHECKED = "unchecked"
STATUS_NA = "na"

SHORT_LABELS = {
    "closed_window_envelope": "Envelope",
    "heater_excitation": "Heater",
    "solar_variation": "Solar",
    "open_contact": "Open UA",
}

_STATUS_RANK = {
    STATUS_NA: 0,
    STATUS_UNCHECKED: 1,
    STATUS_CHECKED: 2,
}


def _step_duration(history: Sequence[Mapping[str, Any]], index: int, dt: float) -> float:
    if index + 1 < len(history):
        t0 = history[index].get("timestamp")
        t1 = history[index + 1].get("timestamp")
        try:
            step = float(t1) - float(t0)
        except (TypeError, ValueError):
            step = dt
        if step > 0.0:
            return step
    return float(dt) if dt > 0.0 else 0.0


def _open_closed_durations(
    history: Sequence[Mapping[str, Any]],
    room_name: str,
    dt: float,
) -> tuple[float, float]:
    closed_s = 0.0
    open_s = 0.0
    for i, rec in enumerate(history):
        step = _step_duration(history, i, dt)
        wo = rec.get("window_open") or {}
        if wo.get(room_name, False):
            open_s += step
        else:
            closed_s += step
    return closed_s, open_s


def _total_duration(history: Sequence[Mapping[str, Any]], dt: float) -> float:
    return sum(_step_duration(history, i, dt) for i in range(len(history)))


def _category(
    *,
    cat_id: str,
    label: str,
    status: str,
    have_s: Optional[float],
    recommend_s: Optional[float],
    hint: str = "",
) -> Dict[str, Any]:
    return {
        "id": cat_id,
        "label": label,
        "short_label": SHORT_LABELS.get(cat_id, label),
        "status": status,
        "have_s": None if have_s is None else round(float(have_s), 1),
        "recommend_s": None if recommend_s is None else round(float(recommend_s), 1),
        "hint": hint,
    }


def categorise_pe_coverage(
    history: Sequence[Mapping[str, Any]],
    *,
    room_name: str,
    room_names: Sequence[str],
    sources: Sequence[Any],
    dt: float,
    has_contact_entity: bool,
    min_history_steps: Optional[int] = None,
) -> Dict[str, Any]:
    """Classify the records that would enter the next PE fit for ``room_name``.

    Returns a dict with ``n_steps``, ``duration_s``, and ``categories``.
    """
    records: List[Mapping[str, Any]] = list(history or [])
    names = list(room_names)
    min_steps = (
        int(min_history_steps)
        if min_history_steps is not None
        else max(10, int((_MIN_HISTORY_TIME_S / dt) + 0.999)) if dt > 0
        else MIN_HISTORY_STEPS
    )

    closed_s, open_s = _open_closed_durations(records, room_name, dt)
    total_s = _total_duration(records, dt)

    excited = _check_identifiable_sources(
        list(records), len(sources), min_history_steps=min_steps,
    )
    split_rooms = _identifiable_split_rooms(excited, list(sources), names)
    heater_ok = False
    if room_name in names:
        heater_ok = names.index(room_name) in split_rooms

    solar_rooms = _check_identifiable_solar(
        list(records), names, min_history_steps=min_steps,
    )
    solar_ok = room_name in names and names.index(room_name) in solar_rooms

    closed_status = (
        STATUS_CHECKED if closed_s >= CLOSED_RECOMMEND_S else STATUS_UNCHECKED
    )
    heater_status = STATUS_CHECKED if heater_ok else STATUS_UNCHECKED
    solar_status = STATUS_CHECKED if solar_ok else STATUS_UNCHECKED
    if not has_contact_entity:
        open_status = STATUS_NA
    else:
        open_status = STATUS_CHECKED if open_s >= OPEN_RECOMMEND_S else STATUS_UNCHECKED

    categories = [
        _category(
            cat_id="closed_window_envelope",
            label="Closed-window envelope",
            status=closed_status,
            have_s=closed_s,
            recommend_s=CLOSED_RECOMMEND_S,
            hint="24 h of closed data is better",
        ),
        _category(
            cat_id="heater_excitation",
            label="Heater excitation",
            status=heater_status,
            have_s=total_s,
            recommend_s=float(min_steps) * float(dt) if dt > 0 else _MIN_HISTORY_TIME_S,
            hint="Dataset must include this room's heater command both off (0) and on",
        ),
        _category(
            cat_id="solar_variation",
            label="Solar variation",
            status=solar_status,
            have_s=total_s,
            recommend_s=float(min_steps) * float(dt) if dt > 0 else _MIN_HISTORY_TIME_S,
            hint="Daytime solar change so the solar scale is identifiable",
        ),
        _category(
            cat_id="open_contact",
            label="Open-contact (extra UA)",
            status=open_status,
            have_s=None if not has_contact_entity else open_s,
            recommend_s=None if not has_contact_entity else OPEN_RECOMMEND_S,
            hint=(
                "No window or door contact configured"
                if not has_contact_entity
                else "Open window or door samples to identify extra outdoor exchange"
            ),
        ),
    ]
    return {
        "room": room_name,
        "n_steps": len(records),
        "duration_s": round(total_s, 1),
        "categories": categories,
    }


def coverage_tags(result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Slim category tags for a stored-dataset summary."""
    tags: List[Dict[str, Any]] = []
    for cat in result.get("categories") or []:
        tags.append(
            {
                "id": cat.get("id"),
                "label": cat.get("label"),
                "short_label": cat.get("short_label") or SHORT_LABELS.get(str(cat.get("id") or ""), ""),
                "status": cat.get("status"),
            }
        )
    return tags


def union_pe_coverage(
    coverages: Sequence[Mapping[str, Any]],
    *,
    room_name: str = "",
) -> Dict[str, Any]:
    """Union per-dataset coverage: a category is checked if any set covers it."""
    by_id: Dict[str, Dict[str, Any]] = {}
    n_steps = 0
    duration_s = 0.0
    for cov in coverages:
        n_steps += int(cov.get("n_steps") or 0)
        try:
            duration_s += float(cov.get("duration_s") or 0.0)
        except (TypeError, ValueError):
            pass
        for cat in cov.get("categories") or []:
            cid = str(cat.get("id") or "")
            if not cid:
                continue
            prev = by_id.get(cid)
            if prev is None:
                by_id[cid] = dict(cat)
                continue
            prev_rank = _STATUS_RANK.get(str(prev.get("status")), 0)
            new_rank = _STATUS_RANK.get(str(cat.get("status")), 0)
            if new_rank > prev_rank:
                prev["status"] = cat.get("status")
            for key in ("have_s",):
                left = prev.get(key)
                right = cat.get(key)
                if left is None:
                    prev[key] = right
                elif right is not None:
                    try:
                        prev[key] = round(float(left) + float(right), 1)
                    except (TypeError, ValueError):
                        pass
    return {
        "room": room_name or (coverages[0].get("room") if coverages else ""),
        "n_steps": n_steps,
        "duration_s": round(duration_s, 1),
        "categories": list(by_id.values()),
    }


__all__ = [
    "CLOSED_HINT_S",
    "CLOSED_RECOMMEND_S",
    "OPEN_RECOMMEND_S",
    "SHORT_LABELS",
    "STATUS_CHECKED",
    "STATUS_NA",
    "STATUS_UNCHECKED",
    "categorise_pe_coverage",
    "coverage_tags",
    "union_pe_coverage",
]
