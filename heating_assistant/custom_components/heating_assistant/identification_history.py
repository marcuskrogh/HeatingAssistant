"""Backward-compatible re-export; prefer :mod:`history.store`."""

from __future__ import annotations

from .history.store import IdentificationHistoryStore

__all__ = ["IdentificationHistoryStore"]
