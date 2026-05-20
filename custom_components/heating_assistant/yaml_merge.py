"""YAML ↔ ConfigEntry merging for the Heating Assistant integration.

When a user defines part of the integration in ``configuration.yaml`` and the
rest via the UI config flow, the two need to be reconciled before the
coordinator can be constructed.  The rules — YAML wins for rooms / heat
sources when present, else the ConfigEntry data wins — used to live inline in
``__init__.py``.  Pulling them out keeps ``__init__.py`` focused on HA-side
wiring (setup, services, options listener) and lets the merge logic be
unit-tested without standing up the full integration.
"""

from __future__ import annotations

from typing import Any, Dict

from .const import (
    CONF_CONSTRAINT_OFFSET,
    CONF_ENERGY_WEIGHT,
    CONF_HEAT_SOURCES,
    CONF_HORIZON,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_MPC_ANALYTIC_DERIVATIVES,
    CONF_MPC_SOLVER,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_ROOMS,
    CONF_SIGMA_B,
    CONF_SIGMA_V,
    CONF_SIGMA_W,
    CONF_SMOOTHING_WEIGHT,
    CONF_TERMINAL_WEIGHT,
    CONF_UPDATE_INTERVAL,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_OPEN_CLOSE_SETTLE,
    CONF_WINDOW_OPEN_DEBOUNCE,
    CONF_WINDOW_OPEN_Q_INFLATION,
    DEFAULT_CONSTRAINT_OFFSET,
    DEFAULT_ENERGY_WEIGHT,
    DEFAULT_HORIZON,
    DEFAULT_MPC_ANALYTIC_DERIVATIVES,
    DEFAULT_MPC_SOLVER,
    DEFAULT_SIGMA_B,
    DEFAULT_SIGMA_V,
    DEFAULT_SIGMA_W,
    DEFAULT_SMOOTHING_WEIGHT,
    DEFAULT_TERMINAL_WEIGHT,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WINDOW_OPEN_CLOSE_SETTLE,
    DEFAULT_WINDOW_OPEN_DEBOUNCE,
    DEFAULT_WINDOW_OPEN_Q_INFLATION,
)


def merge_yaml_into_entry_data(
    entry_data: Dict[str, Any],
    yaml_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge YAML-backed definitions into config-entry data.

    Rules:
    * YAML is authoritative for ``rooms`` and ``heat_sources`` when it defines
      at least one entry; otherwise the config-entry value (potentially set
      by the UI options flow) is kept.
    * For every other scalar, the config-entry value wins when present; YAML
      fills in only what's missing.  This matches the user expectation that
      UI changes survive even when YAML is later edited.
    """
    merged = dict(entry_data)

    # ── Lists: YAML wins when non-empty ──────────────────────────────────
    yaml_rooms = yaml_cfg.get(CONF_ROOMS, None)
    if yaml_rooms not in (None, []):
        merged[CONF_ROOMS] = yaml_rooms
    elif not merged.get(CONF_ROOMS):
        merged[CONF_ROOMS] = []

    yaml_sources = yaml_cfg.get(CONF_HEAT_SOURCES, None)
    if yaml_sources not in (None, []):
        merged[CONF_HEAT_SOURCES] = yaml_sources
    elif not merged.get(CONF_HEAT_SOURCES):
        merged[CONF_HEAT_SOURCES] = []

    # ── Entity references: YAML fills in only when the entry's value is
    # missing or the empty-string default that the config flow stores.
    if not merged.get(CONF_OUTDOOR_TEMP_ENTITY):
        merged[CONF_OUTDOOR_TEMP_ENTITY] = yaml_cfg.get(CONF_OUTDOOR_TEMP_ENTITY, "")
    if not merged.get(CONF_WEATHER_ENTITY):
        merged[CONF_WEATHER_ENTITY] = yaml_cfg.get(CONF_WEATHER_ENTITY, "")

    # ── Scalars with defaults: YAML fills in only when the key is absent.
    _fill_default(merged, yaml_cfg, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    _fill_default(merged, yaml_cfg, CONF_HORIZON, DEFAULT_HORIZON)
    _fill_default(merged, yaml_cfg, CONF_ENERGY_WEIGHT, DEFAULT_ENERGY_WEIGHT)
    _fill_default(merged, yaml_cfg, CONF_SMOOTHING_WEIGHT, DEFAULT_SMOOTHING_WEIGHT)
    _fill_default(merged, yaml_cfg, CONF_CONSTRAINT_OFFSET, DEFAULT_CONSTRAINT_OFFSET)
    _fill_default(merged, yaml_cfg, CONF_TERMINAL_WEIGHT, DEFAULT_TERMINAL_WEIGHT)
    _fill_default(merged, yaml_cfg, CONF_MPC_SOLVER, DEFAULT_MPC_SOLVER)
    _fill_default(
        merged, yaml_cfg, CONF_MPC_ANALYTIC_DERIVATIVES, DEFAULT_MPC_ANALYTIC_DERIVATIVES
    )
    _fill_default(merged, yaml_cfg, CONF_SIGMA_W, DEFAULT_SIGMA_W)
    _fill_default(merged, yaml_cfg, CONF_SIGMA_V, DEFAULT_SIGMA_V)
    _fill_default(merged, yaml_cfg, CONF_SIGMA_B, DEFAULT_SIGMA_B)
    _fill_default(
        merged,
        yaml_cfg,
        CONF_WINDOW_OPEN_DEBOUNCE,
        DEFAULT_WINDOW_OPEN_DEBOUNCE,
    )
    _fill_default(
        merged,
        yaml_cfg,
        CONF_WINDOW_OPEN_CLOSE_SETTLE,
        DEFAULT_WINDOW_OPEN_CLOSE_SETTLE,
    )
    _fill_default(
        merged,
        yaml_cfg,
        CONF_WINDOW_OPEN_Q_INFLATION,
        DEFAULT_WINDOW_OPEN_Q_INFLATION,
    )

    # ── Site location: YAML fills in only when absent and YAML provides it.
    if CONF_LATITUDE not in merged and CONF_LATITUDE in yaml_cfg:
        merged[CONF_LATITUDE] = yaml_cfg[CONF_LATITUDE]
    if CONF_LONGITUDE not in merged and CONF_LONGITUDE in yaml_cfg:
        merged[CONF_LONGITUDE] = yaml_cfg[CONF_LONGITUDE]

    return merged


def _fill_default(
    merged: Dict[str, Any],
    yaml_cfg: Dict[str, Any],
    key: str,
    default: Any,
) -> None:
    """Insert ``key`` into ``merged`` when missing, picking YAML value if
    present, else ``default``.  Existing values in ``merged`` are not touched.
    """
    if key not in merged:
        merged[key] = yaml_cfg.get(key, default)


class MergedEntry:
    """Thin wrapper that presents merged entry data to the coordinator.

    The HA :class:`ConfigEntry` is immutable from the integration's point of
    view (the framework owns it).  When YAML adds fields, we still want the
    coordinator to see them via ``entry.data``, so this proxy reads
    ``options`` / ``entry_id`` / ``title`` from the real entry but overlays
    the merged ``data`` dict.
    """

    def __init__(self, entry: Any, data: Dict[str, Any]) -> None:
        self._entry = entry
        self.data = data
        self.options = entry.options
        self.entry_id = entry.entry_id
        self.title = entry.title
