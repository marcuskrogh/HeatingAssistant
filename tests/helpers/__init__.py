"""Shared test utilities for the Heating Assistant test suite."""

from .coordinator_stubs import (
    make_hass_stub,
    make_minimal_coordinator,
    make_live_refresh_coordinator,
    wire_room_enablement,
)
from .setup_patches import patch_setup_stores

__all__ = [
    "make_hass_stub",
    "make_minimal_coordinator",
    "make_live_refresh_coordinator",
    "wire_room_enablement",
    "patch_setup_stores",
]
