"""Engine persistence facade backed by the App JSON store.

The legacy HA integration persistence module writes ConfigEntry data and
imports Home Assistant.  The App engine uses the HA-independent JSON
helpers from :mod:`heatingassistant.persistence` instead.
"""

from __future__ import annotations

from heatingassistant.persistence import (
    load_config,
    load_json,
    load_state,
    save_config,
    save_json,
    save_state,
)

__all__ = [
    "load_config",
    "load_json",
    "load_state",
    "save_config",
    "save_json",
    "save_state",
]
