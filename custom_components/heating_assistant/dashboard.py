"""Backward-compatible shim for dashboard entry points.

Lovelace card/view builders live in :mod:`lovelace_dashboard`.
"""

from __future__ import annotations

from .lovelace_dashboard import (
    build_dashboard_from_coordinator,
    build_dashboard_variant_from_coordinator,
)
from .naming import slugify

__all__ = [
    "slugify",
    "build_dashboard_from_coordinator",
    "build_dashboard_variant_from_coordinator",
]
