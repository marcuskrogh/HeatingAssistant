#!/usr/bin/env python3
"""
Local model-fit visualisation script for the Heating Assistant.

Two modes
---------
Synthetic (default):
    Simulates a history buffer using the true thermal model, then evaluates
    fit quality using a *misspecified* model (2× thermal mass) so that
    residuals are non-trivial and the plots are informative.

Snapshot (--snapshot history.json):
    Loads a JSON-serialised history buffer exported from Home Assistant.
    Figures 1–3 and 5 are drawn from the raw history.  Figures 4 and 6
    require a model and are skipped unless --snapshot also provides
    model parameters (currently not supported — re-run in synthetic mode
    to see those figures).

Window selection
----------------
By default the script shows all available history.  Three ways to restrict
the identification window:

1. **Recent trailing window** (``--horizon HOURS``): use the last N hours of data
   (like the HA dashboard horizon slider).

2. **Explicit box selection** (``--start`` / ``--end``): pick an exact date/time
   range, e.g. to target an overnight step-response experiment.

      --start "2024-01-15 22:00" --end "2024-01-16 06:00"
      --start 1705363200           --end 1705392000   # UNIX timestamps
      --start 1705363200           # use from this point to end of history

3. **Interactive Grafana-like selection** (``--interactive``): shows a full-history
   overview with a draggable span selector.  Drag to highlight a region, then
   close the window to use it as the identification window.  Requires an
   interactive matplotlib backend (TkAgg / Qt5Agg).

Usage
-----
    python tests/plot_model_fit.py                                 # synthetic, all data
    python tests/plot_model_fit.py --snapshot history.json         # snapshot mode
    python tests/plot_model_fit.py --horizon 6                     # last 6 h only
    python tests/plot_model_fit.py --start "2024-01-15 22:00" --end "2024-01-16 06:00"
    python tests/plot_model_fit.py --interactive                   # Grafana-like selector
    python tests/plot_model_fit.py --room bedroom                  # single room
    python tests/plot_model_fit.py --save-dir /tmp/plots           # save PNGs

Seven figures (Fig 0 is the window-context overview, 1–6 are analysis):

    0. Window overview     – full history with identified window highlighted
    1. Temperature history – measured / predicted / setpoint / constraint
    2. Prediction error    – residual time-series + autocorrelation bars
    3. Heating power       – per-room heating fraction × max_power
    4. Open-loop sim.      – multi-step free-run vs measurement
    5. Heat flow breakdown – stacked-area: heating / solar / loss / inter-room
    6. Log-likelihood surf – 2D contour of neg-LL vs (log C, log R_ext)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ── Backend selection BEFORE pyplot import ──────────────────────────────────
# Check for --interactive flag in sys.argv before any pyplot import so we can
# choose the right backend.  The Agg backend is headless-safe for CI/saving;
# TkAgg / Qt5Agg are needed for the interactive SpanSelector.
import matplotlib
_INTERACTIVE_MODE = "--interactive" in sys.argv
if not _INTERACTIVE_MODE:
    matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.ticker import FuncFormatter

# ── path setup ───────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

# ── HA dependency stubs ───────────────────────────────────────────────────────
# Inject lightweight stubs for Home Assistant packages so that the integration
# modules (thermal_model, model_diagnostics, etc.) can be imported without a
# full HA install.  This mirrors what tests/conftest.py does for pytest.

import types as _types

# ── Robust auto-stubbing for HA and voluptuous ────────────────────────────────
# The integration package's __init__.py imports many HA symbols at module level.
# Rather than enumerating every one, we use stub modules whose __getattr__
# returns a suitable sentinel for *any* attribute lookup.
#
# Special-cased values match what the integration code actually relies on for
# the modules imported here (thermal_model, heat_sources, model_diagnostics,
# history_window).

class _AutoClass:
    """Returned for any attribute that should act like a class."""
    def __init__(self, *a, **kw): pass
    def __init_subclass__(cls, **kw): pass
    def __call__(self, *a, **kw): return self
    def __getattr__(self, name): return _SENTINEL
    def __bool__(self): return False

class _Sentinel:
    """Returned for scalar constants that just need to exist."""
    def __init__(self, name="<stub>"): self._n = name
    def __repr__(self): return self._n
    def __str__(self): return self._n
    def __eq__(self, o): return isinstance(o, _Sentinel) and self._n == o._n
    def __hash__(self): return hash(self._n)
    def __call__(self, *a, **kw): return self
    def __getattr__(self, name): return _Sentinel(name)
    def __iter__(self): return iter([])
    def __bool__(self): return False

_SENTINEL = _Sentinel()


class _AutoStubModule(_types.ModuleType):
    """A stub module that returns a _Sentinel for any attribute access."""

    def __init__(self, name: str):
        super().__init__(name)
        self.__path__: list = []

    def __getattr__(self, name: str):  # type: ignore[override]
        # Return an exception-derived class for names ending in Error/Exception/Ready
        if any(name.endswith(s) for s in ("Error", "Exception", "NotReady", "Invalid")):
            exc_cls = type(name, (Exception,), {})
            setattr(self, name, exc_cls)
            return exc_cls
        # Return a callable class for PascalCase names (likely a class)
        if name and name[0].isupper():
            stub_cls = type(name, (_AutoClass,), {"__module__": self.__name__})
            setattr(self, name, stub_cls)
            return stub_cls
        # Everything else is a sentinel constant
        val = _Sentinel(name)
        setattr(self, name, val)
        return val


def _stub_module(name: str) -> _AutoStubModule:
    parts = name.split(".")
    for i in range(1, len(parts) + 1):
        pkg = ".".join(parts[:i])
        if pkg not in sys.modules:
            mod = _AutoStubModule(pkg)
            sys.modules[pkg] = mod
        elif not isinstance(sys.modules[pkg], _AutoStubModule):
            pass  # already a real module (e.g., numpy)
    m = sys.modules[name]
    if not isinstance(m, _AutoStubModule):
        return _AutoStubModule(name)  # return a fresh one without registering
    return m  # type: ignore[return-value]


import importlib.abc as _iabc
import importlib.machinery as _imach

class _HAStubFinder(_iabc.MetaPathFinder, _iabc.Loader):
    """Intercept imports of homeassistant.* and voluptuous and return auto-stubs."""

    _PREFIXES = ("homeassistant", "voluptuous")

    def find_module(self, name, path=None):  # legacy interface, keep for compat
        return self if any(name == p or name.startswith(p + ".") for p in self._PREFIXES) else None

    def find_spec(self, name, path, target=None):
        if any(name == p or name.startswith(p + ".") for p in self._PREFIXES):
            return _imach.ModuleSpec(name, self, is_package=True)
        return None

    def create_module(self, spec):
        if spec.name in sys.modules:
            return sys.modules[spec.name]
        mod = _AutoStubModule(spec.name)
        sys.modules[spec.name] = mod
        return mod

    def exec_module(self, module):
        pass  # nothing to execute; the auto-stub __getattr__ handles the rest

# Install the finder BEFORE any HA imports happen.
sys.meta_path.insert(0, _HAStubFinder())

# Voluptuous needs special callable stubs (schema/validator factories).
import voluptuous as _vol  # noqa: E402  — the stub finder intercepts this
_vol.Schema   = lambda schema=None, **kw: (lambda x: x)  # type: ignore
_vol.Required = lambda key, **kw: key                     # type: ignore
_vol.Optional = lambda key, **kw: key                     # type: ignore
_vol.All      = lambda *a: a[0] if a else None            # type: ignore
_vol.Coerce   = lambda t: t                               # type: ignore
_vol.Range    = lambda **kw: None                         # type: ignore
_vol.In       = lambda v: None                            # type: ignore
_vol.REMOVE_EXTRA = "REMOVE_EXTRA"                        # type: ignore
_vol.ALLOW_EXTRA  = "ALLOW_EXTRA"                         # type: ignore
_vol.PREVENT_EXTRA = "PREVENT_EXTRA"                      # type: ignore

# homeassistant.core.callback must be the identity decorator
import homeassistant.core as _ha_core  # noqa: E402
_ha_core.callback = lambda f: f        # type: ignore

# config_validation helpers used in YAML config schemas
import homeassistant.helpers.config_validation as _cv  # noqa: E402
_cv.string = str; _cv.positive_float = float; _cv.positive_int = int  # type: ignore
_cv.boolean = bool; _cv.entity_id = str; _cv.entity_ids = list        # type: ignore
_cv.ensure_list = list                                                 # type: ignore
_cv.make_entity_service_schema = lambda s: s                           # type: ignore

import homeassistant.helpers.update_coordinator as _coord  # noqa: E402
_coord.UpdateFailed = Exception                            # type: ignore

from heatingassistant.engine.thermal_model import (
    HouseModel, Room, RoomConnection,
)
from heatingassistant.engine.heat_sources import ElectricHeater
from custom_components.heating_assistant.model_diagnostics import (
    compute_autocorrelation_function,
)
from heatingassistant.engine.history_window import (
    select_recent_window,
    select_window_by_timestamps,
    history_time_range,
)

# ── Apex Charts colour palette (panel chart colours) ─────────────────────────
C_MEASURED    = "#2196F3"   # blue
C_PREDICTED   = "#FF9800"   # orange
C_SETPOINT    = "#4CAF50"   # green
C_CONSTRAINT  = "#F44336"   # red
C_RESIDUAL    = "#9C27B0"   # purple
C_HEATING     = "#4CAF50"   # green
C_SOLAR       = "#FF9800"   # orange
C_EXT_LOSS    = "#F44336"   # red
C_INTER_ROOM  = "#2196F3"   # blue
C_GRID        = "#E0E0E0"   # light grey
C_WINDOW_FILL = "#2196F3"   # blue for window highlight
C_OVERVIEW_BG = "#F5F5F5"   # light grey for out-of-window data

# Colour rotation for overview room lines
_ROOM_COLORS = ["#2196F3", "#FF9800", "#4CAF50", "#F44336", "#9C27B0", "#00BCD4"]

FIGSIZE = (12, 6)
DPI     = 100


# ── Datetime helpers ─────────────────────────────────────────────────────────

def _is_real_ts(ts: float) -> bool:
    """Return True if ``ts`` looks like a real UNIX timestamp (after year 2001)."""
    return ts > 1_000_000_000.0


def _ts_to_label(ts: float) -> str:
    """Format a UNIX timestamp as a human-readable datetime string."""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return f"{ts:.0f}"


def _parse_datetime_arg(s: str) -> float:
    """Parse a ``--start`` / ``--end`` argument to a UNIX timestamp [s].

    Accepts ISO-format datetime strings (``YYYY-MM-DD HH:MM:SS``,
    ``YYYY-MM-DD HH:MM``, ``YYYY-MM-DD``) and plain float timestamps.
    """
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            pass
    return float(s)


# ── X-axis helpers ───────────────────────────────────────────────────────────

def _x_axis_data(
    history: List[Dict[str, Any]],
    dt: float = 60.0,
) -> Tuple[np.ndarray, str, bool, float]:
    """Return ``(x_vals, xlabel, is_real_ts, base_ts)`` for plotting.

    For synthetic histories (small timestamps) ``x_vals`` is in minutes from 0.
    For real HA histories (UNIX epoch timestamps) ``x_vals`` is in hours from
    the window start so axes are compact; tick labels show human-readable times.

    Returns
    -------
    x_vals : ndarray of shape (len(history),)
    xlabel : axis label string
    is_real_ts : True when timestamps are real UNIX epoch values
    base_ts : UNIX timestamp of the first record (0.0 for synthetic)
    """
    if not history:
        return np.array([]), "Time [min]", False, 0.0

    ts_all = np.array([float(r.get("timestamp", 0.0)) for r in history])
    base_ts = float(ts_all[0])

    if _is_real_ts(base_ts):
        x_vals = (ts_all - base_ts) / 3600.0  # hours from window start
        return x_vals, f"Time  [h from {_ts_to_label(base_ts)}]", True, base_ts
    else:
        return ts_all / 60.0, "Time [min]", False, 0.0


def _apply_datetime_ticks(
    ax: plt.Axes,
    base_ts: float,
    span_hours: float,
    axis: str = "x",
) -> None:
    """Apply human-readable datetime tick labels to a real-timestamp axis.

    The axis values are hours from ``base_ts``; ticks are formatted as
    ``MM-DD\\nHH:MM`` (or ``HH:MM`` for short spans).
    """
    if span_hours <= 2:
        fmt_str = "%H:%M"
    elif span_hours <= 48:
        fmt_str = "%m-%d\n%H:%M"
    else:
        fmt_str = "%m-%d"

    def _fmt(x_hours: float, _pos: Any) -> str:
        try:
            ts = base_ts + x_hours * 3600.0
            return datetime.fromtimestamp(ts).strftime(fmt_str)
        except (OSError, OverflowError):
            return ""

    formatter = FuncFormatter(_fmt)
    if axis == "x":
        ax.xaxis.set_major_formatter(formatter)
    else:
        ax.yaxis.set_major_formatter(formatter)


# ── Style helper ─────────────────────────────────────────────────────────────

def _style_ax(ax: plt.Axes, title: str, xlabel: str, ylabel: str) -> None:
    ax.set_facecolor("white")
    ax.grid(color=C_GRID, linewidth=0.8)
    ax.set_title(title, fontsize=10, pad=4)
    ax.set_xlabel(xlabel, fontsize=8)
    ax.set_ylabel(ylabel, fontsize=8)
    ax.tick_params(labelsize=7)
    for spine in ax.spines.values():
        spine.set_color(C_GRID)


# Legacy helper kept for internal compatibility
def _x_minutes(history: List[Dict[str, Any]], dt: float = 60.0) -> np.ndarray:
    """Return x-axis values in minutes, using timestamps if present."""
    ts = [r.get("timestamp") for r in history]
    if ts[0] is not None:
        try:
            return np.array([float(t) / 60.0 for t in ts])
        except (TypeError, ValueError):
            pass
    return np.arange(len(history)) * dt / 60.0


# ── Model factories ───────────────────────────────────────────────────────────

def _make_rooms_true(
    mass1: float = 5_000_000.0, r1: float = 0.05,
    mass2: float = 3_000_000.0, r2: float = 0.08,
    r12: float = 0.2,
) -> List[Room]:
    living = Room(
        name="living_room",
        thermal_mass=mass1,
        r_external=r1,
        connections=[RoomConnection("bedroom", r12)],
        temperature=20.0,
        setpoint=21.0,
    )
    bedroom = Room(
        name="bedroom",
        thermal_mass=mass2,
        r_external=r2,
        connections=[RoomConnection("living_room", r12)],
        temperature=18.0,
        setpoint=19.0,
    )
    return [living, bedroom]


def _make_rooms_misspec(rooms_true: List[Room], mass_factor: float = 2.0) -> List[Room]:
    """Return a copy of rooms_true with thermal_mass scaled by mass_factor."""
    misspec = deepcopy(rooms_true)
    for r in misspec:
        r.thermal_mass *= mass_factor
    return misspec


def _make_sources(rooms: List[Room]) -> List[ElectricHeater]:
    return [
        ElectricHeater(f"{r.name}_heater", r.name, max_power=2000.0)
        for r in rooms
    ]


# ── Synthetic history generation ─────────────────────────────────────────────

def generate_synthetic_history(
    rooms: List[Room],
    sources: List[ElectricHeater],
    dt: float = 60.0,
    n_steps: int = 240,
    noise_std: float = 0.05,
    seed: int = 42,
) -> List[Dict[str, Any]]:
    """
    Simulate a history buffer using the true model with a simple bang-bang
    controller, a diurnal outdoor temperature cycle, and small solar gains.

    Returns
    -------
    list of dicts with keys: y, u, d_outdoor, d_solar, timestamp, setpoint
    """
    rng = np.random.default_rng(seed)
    model = HouseModel(deepcopy(rooms))
    room_names = [r.name for r in rooms]
    setpoints = {r.name: r.setpoint for r in rooms}

    history: List[Dict[str, Any]] = []

    for k in range(n_steps):
        t_hours = k * dt / 3600.0

        # Diurnal outdoor temperature: 5 ± 4 °C with trough at 4 am
        outdoor_temp = 5.0 + 4.0 * math.cos(2 * math.pi * (t_hours - 4.0) / 24.0)

        # Solar gain: zero at night, peak at midday
        solar_scale = max(0.0, math.sin(math.pi * (t_hours % 24.0) / 12.0))
        solar = {
            "living_room": 300.0 * solar_scale,
            "bedroom":      100.0 * solar_scale,
        }

        # Bang-bang heating with small noise
        temps = model.temperatures
        u: List[float] = []
        for i, src in enumerate(sources):
            sp = setpoints[src.room]
            frac = 0.85 if temps[src.room] < sp - 0.2 else 0.05
            frac += 0.05 * float(rng.standard_normal())
            u.append(float(np.clip(frac, 0.0, 1.0)))

        # Record noisy measurement at step k (before advancing)
        y = [temps[rn] + float(rng.normal(0.0, noise_std)) for rn in room_names]

        history.append({
            "y":          y,
            "u":          u,
            "d_outdoor":  outdoor_temp,
            "d_solar":    {k_: round(v, 2) for k_, v in solar.items()},
            "timestamp":  k * dt,
            "setpoint":   dict(setpoints),
        })

        # Advance model
        heat_inputs = {
            src.room: src.thermal_power(u[i], outdoor_temp)
            for i, src in enumerate(sources)
        }
        model.step(dt, heat_inputs, outdoor_temp, solar)

    return history


# ── One-step prediction using a given model ───────────────────────────────────

def compute_one_step_preds(
    history: List[Dict[str, Any]],
    rooms_model: List[Room],
    sources: List[ElectricHeater],
    dt: float,
) -> List[List[float]]:
    """
    Compute y_pred[k+1] for k = 0..N-2 using rooms_model.

    Resets the model to y[k] before each step so errors do not accumulate.
    Index alignment: y_preds[k] is the prediction for history[k+1]["y"].

    Returns
    -------
    y_preds : list of length N-1, each entry is a list of n_rooms floats.
    """
    model = HouseModel(deepcopy(rooms_model))
    room_names = [r.name for r in rooms_model]
    y_preds: List[List[float]] = []

    for k in range(len(history) - 1):
        rec = history[k]
        # Reset to measured state at step k
        model.set_temperatures({
            room_names[i]: float(rec["y"][i])
            for i in range(len(room_names))
            if i < len(rec.get("y", []))
        })

        outdoor = float(rec.get("d_outdoor", 0.0))
        solar   = rec.get("d_solar", {n: 0.0 for n in room_names})
        u_rec   = rec.get("u", [0.0] * len(sources))

        heat_inputs = {
            src.room: src.thermal_power(
                u_rec[i] if i < len(u_rec) else 0.0, outdoor
            )
            for i, src in enumerate(sources)
        }
        new_temps = model.step(dt, heat_inputs, outdoor, solar)
        y_preds.append([new_temps[n] for n in room_names])

    return y_preds


# ── Open-loop multi-step simulation ──────────────────────────────────────────

def compute_open_loop_segments(
    history: List[Dict[str, Any]],
    rooms_model: List[Room],
    sources: List[ElectricHeater],
    dt: float,
    segment_length: int = 30,
) -> Dict[str, Any]:
    """
    Non-overlapping open-loop simulations of length ``segment_length``.

    At each window start the model is initialised from y[start]; then it
    is propagated forward using recorded u and d without any measurement
    correction.

    Returns
    -------
    dict with:
      per_room : {room_name: {predictions, measurements, rmse, mae}}
      segment_starts : list of start indices (in the original history)
      segment_length : int
    """
    model = HouseModel(deepcopy(rooms_model))
    room_names = [r.name for r in rooms_model]
    n = len(history)

    per_room_preds: Dict[str, List[float]] = {nm: [] for nm in room_names}
    per_room_meas:  Dict[str, List[float]] = {nm: [] for nm in room_names}
    segment_starts: List[int] = []

    for start in range(0, n - segment_length, segment_length):
        seg = history[start: start + segment_length]
        y0  = seg[0].get("y", [])
        model.set_temperatures({
            room_names[i]: float(y0[i])
            for i in range(len(room_names))
            if i < len(y0)
        })
        segment_starts.append(start)

        for rec in seg[1:]:
            outdoor = float(rec.get("d_outdoor", 0.0))
            solar   = rec.get("d_solar", {n: 0.0 for n in room_names})
            u_rec   = rec.get("u", [0.0] * len(sources))

            heat_inputs = {
                src.room: src.thermal_power(
                    u_rec[i] if i < len(u_rec) else 0.0, outdoor
                )
                for i, src in enumerate(sources)
            }
            new_temps = model.step(dt, heat_inputs, outdoor, solar)

            y_meas = rec.get("y", [])
            for ridx, nm in enumerate(room_names):
                per_room_preds[nm].append(new_temps[nm])
                per_room_meas[nm].append(
                    float(y_meas[ridx]) if ridx < len(y_meas) else float("nan")
                )

    results: Dict[str, Any] = {}
    for nm in room_names:
        preds = np.array(per_room_preds[nm])
        meas  = np.array(per_room_meas[nm])
        valid = ~(np.isnan(preds) | np.isnan(meas))
        rmse  = float(np.sqrt(np.mean((preds[valid] - meas[valid]) ** 2))) if valid.any() else float("nan")
        mae   = float(np.mean(np.abs(preds[valid] - meas[valid])))         if valid.any() else float("nan")
        results[nm] = {
            "predictions":  preds.tolist(),
            "measurements": meas.tolist(),
            "rmse": rmse,
            "mae":  mae,
        }

    return {
        "per_room":       results,
        "segment_starts": segment_starts,
        "segment_length": segment_length,
    }


# ── Heat flow decomposition ───────────────────────────────────────────────────

def compute_heat_flows(
    history: List[Dict[str, Any]],
    rooms: List[Room],
    sources: List[ElectricHeater],
) -> Dict[str, Dict[str, List[float]]]:
    """
    Per-room heat flow components [W] from the recorded history.

    Returns
    -------
    {room_name: {"heating", "solar", "ext_loss", "inter_room"}}
    """
    room_names  = [r.name for r in rooms]
    room_by_name = {r.name: r for r in rooms}
    src_by_room  = {src.room: src for src in sources}

    flows: Dict[str, Dict[str, List[float]]] = {
        nm: {"heating": [], "solar": [], "ext_loss": [], "inter_room": []}
        for nm in room_names
    }

    for rec in history:
        y    = rec.get("y", [])
        u    = rec.get("u", [])
        dout = float(rec.get("d_outdoor", 0.0))
        dsol = rec.get("d_solar", {})

        room_u = {room_names[i]: (u[i] if i < len(u) else 0.0) for i in range(len(room_names))}

        for ridx, nm in enumerate(room_names):
            room = room_by_name[nm]
            T    = float(y[ridx]) if ridx < len(y) else 0.0

            src = src_by_room.get(nm)
            Q_heat  = src.thermal_power(room_u.get(nm, 0.0), dout) if src else 0.0
            Q_solar = float(dsol.get(nm, 0.0))
            Q_ext   = (dout - T) / room.r_external   # negative = net loss to outside

            Q_inter = 0.0
            for conn in room.connections:
                j_idx = room_names.index(conn.connected_room) if conn.connected_room in room_names else -1
                if 0 <= j_idx < len(y):
                    Q_inter += (float(y[j_idx]) - T) / conn.r_value

            flows[nm]["heating"].append(Q_heat)
            flows[nm]["solar"].append(Q_solar)
            flows[nm]["ext_loss"].append(Q_ext)
            flows[nm]["inter_room"].append(Q_inter)

    return flows


# ── Log-likelihood surface (single room, inline Kalman) ───────────────────────

def compute_ll_surface(
    history: List[Dict[str, Any]],
    rooms: List[Room],
    sources: List[ElectricHeater],
    room_name: str,
    dt: float,
    grid_n: int = 30,
) -> Optional[Dict[str, Any]]:
    """
    2D neg-LL surface over (log C, log R_ext) for *room_name*.

    Uses a scalar Kalman filter that models the room in isolation (outdoor
    coupling only), which is an approximation but sufficient for a
    diagnostic surface plot.

    Returns None if the room or source cannot be found.
    """
    room_idx = next((i for i, r in enumerate(rooms) if r.name == room_name), None)
    src_idx  = next((i for i, s in enumerate(sources) if s.room == room_name), None)
    if room_idx is None or src_idx is None:
        return None

    true_room = rooms[room_idx]
    log_C_true = math.log(true_room.thermal_mass)
    log_R_true = math.log(true_room.r_external)

    # Extract room-specific sequences
    y_seq    = np.array([float(r["y"][room_idx]) for r in history if len(r.get("y", [])) > room_idx])
    u_raw    = np.array([r.get("u", [0.0]*len(sources))[src_idx] for r in history])
    Q_heat   = np.array([sources[src_idx].thermal_power(float(u_raw[k]), float(history[k].get("d_outdoor", 0.0)))
                         for k in range(len(history))])
    dout     = np.array([float(r.get("d_outdoor", 0.0)) for r in history])
    n = len(y_seq)

    log_C_range = np.linspace(log_C_true - 1.5, log_C_true + 1.5, grid_n)
    log_R_range = np.linspace(log_R_true - 1.5, log_R_true + 1.5, grid_n)

    ll_surface = np.zeros((grid_n, grid_n))

    for ci, lc in enumerate(log_C_range):
        for ri, lr in enumerate(log_R_range):
            C_val = math.exp(lc)
            R_val = math.exp(lr)
            g_ext = 1.0 / R_val

            # Scalar Euler-discretised state: T[k+1] = A*T[k] + B*Q[k] + E*Tout[k]
            A_d = 1.0 - (g_ext / C_val) * dt
            B_d = dt / C_val
            E_d = (g_ext / C_val) * dt

            Q_kf = 1e-4   # process noise variance
            R_kf = 0.01   # measurement noise variance

            x = float(y_seq[0])
            P = 1.0
            nll = 0.0

            for k in range(1, n):
                x_pred = A_d * x + B_d * float(Q_heat[k - 1]) + E_d * float(dout[k - 1])
                P_pred = A_d * A_d * P + Q_kf
                S      = P_pred + R_kf
                nu     = float(y_seq[k]) - x_pred
                nll   += 0.5 * (math.log(max(S, 1e-12) * 2 * math.pi) + nu * nu / S)
                K  = P_pred / S
                x  = x_pred + K * nu
                P  = (1.0 - K) * P_pred

            ll_surface[ri, ci] = nll   # row=R, col=C

    return {
        "log_C_range": log_C_range,
        "log_R_range": log_R_range,
        "ll_surface":  ll_surface,
        "log_C_true":  log_C_true,
        "log_R_true":  log_R_true,
    }


# ── Figure 0: Window overview ─────────────────────────────────────────────────

def fig_window_overview(
    full_history: List[Dict[str, Any]],
    window_history: List[Dict[str, Any]],
    room_names: List[str],
    dt: float = 60.0,
) -> plt.Figure:
    """
    Figure 0 — Identification window overview.

    Shows all room temperatures across the **full** history buffer.  The
    selected identification window is highlighted with a shaded band and
    boundary markers.  When the window covers the entire history the
    highlight spans the full plot.

    This figure provides context for *where* in the data the current window
    sits — particularly useful when the window is a specific past experiment
    (e.g. an overnight step-response test that is not the latest data).
    """
    if not full_history:
        fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
        fig.suptitle("Figure 0 — Window Overview (no data)", fontsize=11)
        return fig

    # Full-history x-axis in hours from the very first record
    ts_full = np.array([float(r.get("timestamp", 0.0)) for r in full_history])
    base_ts_full = float(ts_full[0])
    is_real = _is_real_ts(base_ts_full)

    if is_real:
        x_full = (ts_full - base_ts_full) / 3600.0
        xlabel = f"Time  [h from {_ts_to_label(base_ts_full)}]"
    else:
        x_full = ts_full / 60.0
        xlabel = "Time [min]"

    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.set_facecolor("white")
    ax.grid(color=C_GRID, linewidth=0.8, zorder=0)

    # Dimmed full-history lines (all rooms in grey)
    for ridx, nm in enumerate(room_names):
        y_full = np.array([
            float(r["y"][ridx]) if ridx < len(r.get("y", [])) else np.nan
            for r in full_history
        ])
        ax.plot(x_full[:len(y_full)], y_full,
                color="#BDBDBD", lw=0.8, alpha=0.7, zorder=1)

    # Window bounds in x-axis units
    win_ts = np.array([float(r.get("timestamp", 0.0)) for r in window_history]) if window_history else np.array([])
    if len(win_ts) >= 2:
        if is_real:
            x_win_start = (float(win_ts[0])  - base_ts_full) / 3600.0
            x_win_end   = (float(win_ts[-1]) - base_ts_full) / 3600.0
        else:
            x_win_start = float(win_ts[0])  / 60.0
            x_win_end   = float(win_ts[-1]) / 60.0

        # Highlight the selected window
        ax.axvspan(x_win_start, x_win_end,
                   color=C_WINDOW_FILL, alpha=0.10, zorder=2, label="_highlight")
        ax.axvline(x_win_start, color=C_WINDOW_FILL, lw=1.5, ls="--", zorder=3, alpha=0.8)
        ax.axvline(x_win_end,   color=C_WINDOW_FILL, lw=1.5, ls="--", zorder=3, alpha=0.8)

        # Coloured room lines inside the window
        x_win = win_ts.copy()
        if is_real:
            x_win = (x_win - base_ts_full) / 3600.0
        else:
            x_win = x_win / 60.0

        for ridx, nm in enumerate(room_names):
            y_win = np.array([
                float(r["y"][ridx]) if ridx < len(r.get("y", [])) else np.nan
                for r in window_history
            ])
            color = _ROOM_COLORS[ridx % len(_ROOM_COLORS)]
            ax.plot(x_win[:len(y_win)], y_win,
                    color=color, lw=1.4, zorder=4,
                    label=nm.replace("_", " ").title())

        # Annotate window boundaries with actual date/time
        y_top = ax.get_ylim()[1]
        if is_real:
            lbl_start = _ts_to_label(float(win_ts[0]))
            lbl_end   = _ts_to_label(float(win_ts[-1]))
        else:
            lbl_start = f"{x_win_start:.0f} min"
            lbl_end   = f"{x_win_end:.0f} min"

        win_hours = (float(win_ts[-1]) - float(win_ts[0])) / 3600.0
        win_steps = len(window_history)
        ax.text(x_win_start, ax.get_ylim()[1], f" {lbl_start}",
                fontsize=7, color=C_WINDOW_FILL, va="top", ha="left",
                transform=ax.get_xaxis_transform(), clip_on=True)
        ax.text(x_win_end, ax.get_ylim()[1], f"{lbl_end} ",
                fontsize=7, color=C_WINDOW_FILL, va="top", ha="right",
                transform=ax.get_xaxis_transform(), clip_on=True)

        full_hours = (float(ts_full[-1]) - float(ts_full[0])) / 3600.0
        title = (
            f"Figure 0 — Identification Window Overview\n"
            f"Window: {lbl_start}  →  {lbl_end}  "
            f"({win_hours:.1f} h · {win_steps} steps)  "
            f"|  Full history: {full_hours:.1f} h · {len(full_history)} steps"
        )
    else:
        title = "Figure 0 — Identification Window Overview  (no window selected)"

    _style_ax(ax, title, xlabel, "Temperature [°C]")
    if is_real:
        full_span_h = (float(ts_full[-1]) - float(ts_full[0])) / 3600.0
        _apply_datetime_ticks(ax, base_ts_full, full_span_h)
    ax.legend(fontsize=7, loc="upper right")

    for spine in ax.spines.values():
        spine.set_color(C_GRID)

    fig.tight_layout()
    return fig


# ── Interactive window selector ───────────────────────────────────────────────

def interactive_window_selector(
    history: List[Dict[str, Any]],
    room_names: List[str],
    dt: float = 60.0,
    initial_start_ts: Optional[float] = None,
    initial_end_ts: Optional[float] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Launch a Grafana-like interactive window selector.

    Displays all room temperatures over the full history.  The user can:

    * **Drag** on the plot to define a time window (like Grafana's time-range
      brush).  A semi-transparent blue highlight shows the selection in
      real time.
    * **Resize** the selection by dragging its edges (interactive SpanSelector).
    * **Close** the figure (or press Enter) to confirm the current selection.

    If no selection is made before the window is closed, ``(None, None)`` is
    returned and the caller falls back to the full history.

    Requires an interactive matplotlib backend (TkAgg, Qt5Agg, etc.).  If the
    backend cannot be switched, a console prompt is used as fallback.

    Parameters
    ----------
    history
        Full history buffer (all available data).
    room_names
        Room names to display.
    dt
        Nominal sampling interval [s] (used for formatting only).
    initial_start_ts, initial_end_ts
        Optional pre-selection shown when the figure opens, e.g. from a
        previous ``--start`` / ``--end`` argument.

    Returns
    -------
    (start_ts, end_ts) as UNIX timestamps [s], or (None, None) if not selected.
    """
    from matplotlib.widgets import SpanSelector

    if not history:
        return None, None

    ts_all = np.array([float(r.get("timestamp", 0.0)) for r in history])
    base_ts = float(ts_all[0])
    is_real = _is_real_ts(base_ts)
    full_span_h = (float(ts_all[-1]) - base_ts) / 3600.0

    if is_real:
        x_all = (ts_all - base_ts) / 3600.0
        xlabel = f"Time  [h from {_ts_to_label(base_ts)}]"
    else:
        x_all = ts_all / 60.0
        xlabel = "Time [min]"

    n_rooms = len(room_names)
    fig_height = max(4.0, 2.0 + n_rooms * 0.8)
    fig, ax = plt.subplots(figsize=(14, fig_height))
    fig.patch.set_facecolor("#1C1C2E")
    ax.set_facecolor("#252540")

    # Grid
    ax.grid(color="#3A3A5C", linewidth=0.7, zorder=0)
    for spine in ax.spines.values():
        spine.set_color("#3A3A5C")
    ax.tick_params(colors="#CCCCCC", labelsize=8)
    ax.xaxis.label.set_color("#CCCCCC")
    ax.yaxis.label.set_color("#CCCCCC")

    # Room temperature lines
    for ridx, nm in enumerate(room_names):
        y_arr = np.array([
            float(r["y"][ridx]) if ridx < len(r.get("y", [])) else np.nan
            for r in history
        ])
        color = _ROOM_COLORS[ridx % len(_ROOM_COLORS)]
        ax.plot(x_all[:len(y_arr)], y_arr,
                color=color, lw=1.3, alpha=0.9, label=nm.replace("_", " ").title(),
                zorder=2)

    # Pre-existing selection (from --start/--end args)
    if initial_start_ts is not None and initial_end_ts is not None:
        if is_real:
            x_s = (initial_start_ts - base_ts) / 3600.0
            x_e = (initial_end_ts   - base_ts) / 3600.0
        else:
            x_s = initial_start_ts / 60.0
            x_e = initial_end_ts   / 60.0
        ax.axvspan(x_s, x_e, color="#2196F3", alpha=0.20, zorder=1)

    ax.set_xlabel(xlabel, color="#CCCCCC", fontsize=9)
    ax.set_ylabel("Temperature [°C]", color="#CCCCCC", fontsize=9)
    if is_real:
        _apply_datetime_ticks(ax, base_ts, full_span_h)

    title = (
        "Identification Window Selector — Grafana-style\n"
        "Drag to select · Drag edges to resize · Close window to confirm · "
        "Close without selecting for full history"
    )
    ax.set_title(title, color="#E0E0E0", fontsize=9, pad=8)
    ax.legend(fontsize=8, facecolor="#1C1C2E", labelcolor="#CCCCCC",
              edgecolor="#3A3A5C", loc="upper right")

    fig.tight_layout()

    # SpanSelector: drag to define the window
    selected: List[Optional[Tuple[float, float]]] = [None]

    def on_select(xmin: float, xmax: float) -> None:
        if abs(xmax - xmin) < 1e-6:
            return
        selected[0] = (xmin, xmax)
        if is_real:
            ts_s = base_ts + xmin * 3600.0
            ts_e = base_ts + xmax * 3600.0
            lbl_s = _ts_to_label(ts_s)
            lbl_e = _ts_to_label(ts_e)
            span_h = (ts_e - ts_s) / 3600.0
            status = f"Selected: {lbl_s}  →  {lbl_e}  ({span_h:.2f} h)"
        else:
            span_m = xmax - xmin
            status = f"Selected: {xmin:.1f}  →  {xmax:.1f} min  ({span_m:.1f} min)"
        print(f"\r  {status}   ", end="", flush=True)
        try:
            fig.canvas.manager.set_window_title(f"Window: {status}")  # type: ignore[attr-defined]
        except AttributeError:
            pass

    span_sel = SpanSelector(  # noqa: F841 — must stay alive to keep callbacks connected
        ax, on_select, "horizontal",
        useblit=True,
        props=dict(alpha=0.30, facecolor="#2196F3", edgecolor="#64B5F6", linewidth=1.5),
        interactive=True,
        drag_from_anywhere=True,
    )

    print("\n  Interactive window selector — dark Grafana-style view.")
    print("  Drag on the chart to select an identification window.")
    print("  Close the window (or press Enter in the terminal) to confirm.")
    plt.show()

    # plt.show() blocks until the figure is closed
    print()

    if selected[0] is None:
        print("  No selection → using full history.")
        return None, None

    xmin, xmax = selected[0]
    if is_real:
        return base_ts + xmin * 3600.0, base_ts + xmax * 3600.0
    else:
        return xmin * 60.0, xmax * 60.0


# ── Figure 1: Temperature history ────────────────────────────────────────────

def fig_temperature_history(
    history: List[Dict[str, Any]],
    room_names: List[str],
    y_preds: Optional[List[List[float]]],
    rooms: List[Room],
    dt: float = 60.0,
    constraint_band: float = 1.0,
) -> plt.Figure:
    """
    Figure 1 — Temperature history.

    One subplot per room showing measured temperature (blue), one-step
    predicted temperature (orange), setpoint (green), and a ±constraint
    shaded band (red, α=0.12).
    """
    n_rooms = len(room_names)
    fig, axes = plt.subplots(1, n_rooms, figsize=FIGSIZE, dpi=DPI, sharey=False)
    if n_rooms == 1:
        axes = [axes]

    x_all, xlabel, is_real, base_ts = _x_axis_data(history, dt)
    span_h = float(x_all[-1] - x_all[0]) if len(x_all) >= 2 else 0.0

    for ridx, (nm, ax) in enumerate(zip(room_names, axes)):
        y_meas = np.array([r["y"][ridx] for r in history if ridx < len(r.get("y", []))])
        x_meas = x_all[:len(y_meas)]

        # Setpoint (constant or from history)
        sp = next((r.setpoint for r in rooms if r.name == nm), 21.0)
        sp_arr = np.array([
            r.get("setpoint", {}).get(nm, sp)
            for r in history[:len(y_meas)]
        ], dtype=float)

        ax.plot(x_meas, y_meas, color=C_MEASURED,   lw=1.4, label="Measured")
        ax.plot(x_meas, sp_arr, color=C_SETPOINT,   lw=1.0, ls="--", label="Setpoint")
        ax.fill_between(x_meas, sp_arr - constraint_band, sp_arr + constraint_band,
                        color=C_CONSTRAINT, alpha=0.10, label=f"±{constraint_band:.1f}°C band")

        # One-step prediction (aligned: y_preds[k] → y[k+1])
        if y_preds is not None:
            x_pred = x_all[1: len(y_preds) + 1]
            y_pred_arr = np.array([yp[ridx] for yp in y_preds if ridx < len(yp)])
            ax.plot(x_pred[:len(y_pred_arr)], y_pred_arr,
                    color=C_PREDICTED, lw=1.2, alpha=0.85, label="1-step predicted")

        _style_ax(ax, f"{nm.replace('_', ' ').title()}", xlabel, "Temperature [°C]")
        if is_real:
            _apply_datetime_ticks(ax, base_ts, span_h)
        ax.legend(fontsize=7, loc="upper right")

    fig.suptitle("Figure 1 — Temperature History", fontsize=11, y=1.01)
    fig.tight_layout()
    return fig


# ── Figure 2: Prediction error + ACF ─────────────────────────────────────────

def fig_prediction_error_acf(
    history: List[Dict[str, Any]],
    room_names: List[str],
    y_preds: List[List[float]],
    dt: float = 60.0,
    max_lag: int = 20,
) -> plt.Figure:
    """
    Figure 2 — Prediction error time-series (top) and ACF (bottom).

    One column per room.
    """
    n_rooms = len(room_names)
    fig = plt.figure(figsize=FIGSIZE, dpi=DPI)
    gs  = gridspec.GridSpec(2, n_rooms, figure=fig, hspace=0.45, wspace=0.35)

    x_all, xlabel, is_real, base_ts = _x_axis_data(history, dt)
    span_h = float(x_all[-1] - x_all[0]) if len(x_all) >= 2 else 0.0

    for ridx, nm in enumerate(room_names):
        # Build aligned residuals: residual[k] = y_pred[k] - y_meas[k+1]
        residuals = []
        x_res     = []
        for k, yp in enumerate(y_preds):
            if k + 1 < len(history) and ridx < len(yp) and ridx < len(history[k + 1].get("y", [])):
                residuals.append(yp[ridx] - history[k + 1]["y"][ridx])
                x_res.append(x_all[k + 1])

        residuals = np.array(residuals)
        x_res     = np.array(x_res)

        # Residual time series
        ax_top = fig.add_subplot(gs[0, ridx])
        ax_top.plot(x_res, residuals, color=C_RESIDUAL, lw=0.9)
        ax_top.axhline(0.0, color="grey", lw=0.7, ls="--")
        rmse = float(np.sqrt(np.mean(residuals ** 2))) if len(residuals) else float("nan")
        _style_ax(ax_top,
                  f"{nm.replace('_', ' ').title()}\nRMSE={rmse:.3f}°C",
                  xlabel, "Error [°C]")
        if is_real:
            _apply_datetime_ticks(ax_top, base_ts, span_h)

        # ACF
        ax_bot = fig.add_subplot(gs[1, ridx])
        if len(residuals) >= 4:
            acf_res = compute_autocorrelation_function(residuals.tolist(), max_lag=max_lag)
            lags    = acf_res["lags"]
            acf     = acf_res["acf"]
            ci      = acf_res["confidence_bound"]
            colors_bar = [
                C_CONSTRAINT if (k > 0 and abs(acf[k]) > ci) else C_RESIDUAL
                for k in range(len(lags))
            ]
            ax_bot.bar(lags, acf, color=colors_bar, width=0.6, alpha=0.8)
            ax_bot.axhline(+ci, color=C_CONSTRAINT, lw=0.8, ls="--", label=f"±{ci:.2f} CI")
            ax_bot.axhline(-ci, color=C_CONSTRAINT, lw=0.8, ls="--")
            ax_bot.legend(fontsize=6)
        _style_ax(ax_bot, "Residual ACF", "Lag", "Autocorrelation")
        ax_bot.set_ylim(-1.1, 1.1)

    fig.suptitle("Figure 2 — Prediction Error & Residual ACF", fontsize=11, y=1.01)
    return fig


# ── Figure 3: Heating power ───────────────────────────────────────────────────

def fig_heating_power(
    history: List[Dict[str, Any]],
    room_names: List[str],
    sources: List[ElectricHeater],
    dt: float = 60.0,
) -> plt.Figure:
    """
    Figure 3 — Heating power [W] per room over time.
    """
    n_rooms = len(room_names)
    fig, axes = plt.subplots(1, n_rooms, figsize=FIGSIZE, dpi=DPI, sharey=False)
    if n_rooms == 1:
        axes = [axes]

    x_all, xlabel, is_real, base_ts = _x_axis_data(history, dt)
    span_h = float(x_all[-1] - x_all[0]) if len(x_all) >= 2 else 0.0
    src_by_room = {src.room: src for src in sources}

    for ridx, (nm, ax) in enumerate(zip(room_names, axes)):
        src     = src_by_room.get(nm)
        u_vals  = [r.get("u", [0.0]*len(sources))[ridx] if ridx < len(r.get("u", [])) else 0.0
                   for r in history]
        d_out   = [float(r.get("d_outdoor", 0.0)) for r in history]
        q_vals  = [src.thermal_power(u_vals[k], d_out[k]) if src else 0.0
                   for k in range(len(history))]

        ax.fill_between(x_all[:len(q_vals)], 0, q_vals, color=C_HEATING, alpha=0.70, step="post")
        ax.step(x_all[:len(q_vals)], q_vals, color=C_HEATING, lw=1.0, where="post")
        _style_ax(ax, f"{nm.replace('_', ' ').title()}", xlabel, "Power [W]")
        if is_real:
            _apply_datetime_ticks(ax, base_ts, span_h)
        if src:
            ax.set_ylim(0, src.max_power * 1.15)

    fig.suptitle("Figure 3 — Heating Power", fontsize=11, y=1.01)
    fig.tight_layout()
    return fig


# ── Figure 4: Open-loop simulation ───────────────────────────────────────────

def fig_open_loop_simulation(
    history: List[Dict[str, Any]],
    room_names: List[str],
    rooms_model: List[Room],
    sources: List[ElectricHeater],
    dt: float = 60.0,
    segment_length: int = 30,
) -> plt.Figure:
    """
    Figure 4 — Open-loop (free-run) simulation vs measurement.

    Vertical dashed lines mark the start of each new segment.
    """
    ol = compute_open_loop_segments(history, rooms_model, sources, dt, segment_length)
    seg_starts = ol["segment_starts"]
    seg_len    = ol["segment_length"]

    n_rooms = len(room_names)
    fig, axes = plt.subplots(1, n_rooms, figsize=FIGSIZE, dpi=DPI, sharey=False)
    if n_rooms == 1:
        axes = [axes]

    x_all, xlabel, is_real, base_ts = _x_axis_data(history, dt)
    span_h = float(x_all[-1] - x_all[0]) if len(x_all) >= 2 else 0.0

    for ridx, (nm, ax) in enumerate(zip(room_names, axes)):
        data  = ol["per_room"].get(nm, {})
        preds = np.array(data.get("predictions", []))
        meas  = np.array(data.get("measurements", []))

        # x-axis: reconstruct step indices from segment layout
        x_idx: List[int] = []
        for s_start in seg_starts:
            x_idx.extend(range(s_start + 1, s_start + seg_len))
        x_idx = x_idx[:len(preds)]
        x_plot = x_all[np.array(x_idx, dtype=int)] if len(x_idx) else np.array([])

        if len(x_plot):
            ax.plot(x_plot, meas[:len(x_plot)], color=C_MEASURED,  lw=1.3, label="Measured")
            ax.plot(x_plot, preds[:len(x_plot)], color=C_PREDICTED, lw=1.2, alpha=0.85,
                    label="Open-loop pred.")

        # Segment boundary lines
        for s_start in seg_starts:
            if s_start < len(x_all):
                ax.axvline(x_all[s_start], color="grey", lw=0.6, ls=":", alpha=0.6)

        rmse = data.get("rmse", float("nan"))
        rmse_str = f"{rmse:.3f}" if rmse == rmse else "N/A"
        _style_ax(ax, f"{nm.replace('_', ' ').title()}\nOpen-loop RMSE={rmse_str}°C",
                  xlabel, "Temperature [°C]")
        if is_real:
            _apply_datetime_ticks(ax, base_ts, span_h)
        ax.legend(fontsize=7)

    fig.suptitle(
        f"Figure 4 — Open-Loop Simulation  (segment={segment_length} steps = "
        f"{segment_length * dt / 60:.0f} min)",
        fontsize=11, y=1.01,
    )
    fig.tight_layout()
    return fig


# ── Figure 5: Heat flow breakdown ────────────────────────────────────────────

def fig_heat_flow_breakdown(
    history: List[Dict[str, Any]],
    room_names: List[str],
    rooms: List[Room],
    sources: List[ElectricHeater],
    dt: float = 60.0,
    smooth_window: int = 5,
) -> plt.Figure:
    """
    Figure 5 — Stacked-area heat flow breakdown per room [W].

    A positive ext_loss means outdoor is warmer than the room; negative
    means heat is lost to the outside.  The chart shows all four components.
    """
    flows   = compute_heat_flows(history, rooms, sources)
    n_rooms = len(room_names)
    fig, axes = plt.subplots(1, n_rooms, figsize=FIGSIZE, dpi=DPI, sharey=False)
    if n_rooms == 1:
        axes = [axes]

    x_all, xlabel, is_real, base_ts = _x_axis_data(history, dt)
    span_h = float(x_all[-1] - x_all[0]) if len(x_all) >= 2 else 0.0

    def _smooth(arr: List[float], w: int) -> np.ndarray:
        a = np.array(arr, dtype=float)
        if w > 1 and len(a) >= w:
            kernel = np.ones(w) / w
            return np.convolve(a, kernel, mode="same")
        return a

    for ridx, (nm, ax) in enumerate(zip(room_names, axes)):
        f     = flows[nm]
        x     = x_all[:len(f["heating"])]
        heat  = _smooth(f["heating"],   smooth_window)
        solar = _smooth(f["solar"],     smooth_window)
        ext   = _smooth(f["ext_loss"],  smooth_window)
        inter = _smooth(f["inter_room"], smooth_window)

        ax.fill_between(x, 0, heat,  color=C_HEATING,   alpha=0.65, label="Heating")
        ax.fill_between(x, 0, solar, color=C_SOLAR,     alpha=0.55, label="Solar gain")
        ax.fill_between(x, 0, ext,   color=C_EXT_LOSS,  alpha=0.45, label="Ext. exchange")
        ax.fill_between(x, 0, inter, color=C_INTER_ROOM, alpha=0.45, label="Inter-room")
        ax.axhline(0.0, color="grey", lw=0.7, ls="--")

        _style_ax(ax, f"{nm.replace('_', ' ').title()}", xlabel, "Power [W]")
        if is_real:
            _apply_datetime_ticks(ax, base_ts, span_h)
        ax.legend(fontsize=6, loc="upper right")

    fig.suptitle(f"Figure 5 — Heat Flow Breakdown  (smoothed {smooth_window}-step MA)",
                 fontsize=11, y=1.01)
    fig.tight_layout()
    return fig


# ── Figure 6: Log-likelihood surface ─────────────────────────────────────────

def fig_ll_surface(
    history: List[Dict[str, Any]],
    rooms: List[Room],
    sources: List[ElectricHeater],
    room_name: str,
    dt: float = 60.0,
    grid_n: int = 30,
) -> Optional[plt.Figure]:
    """
    Figure 6 — 2D neg-LL contour over (log C, log R_ext) for *room_name*.

    A cross marks the configured (prior) parameter values; a star marks
    the grid optimum.
    """
    surf = compute_ll_surface(history, rooms, sources, room_name, dt, grid_n)
    if surf is None:
        print(f"  [Fig 6] Skipped: room '{room_name}' or source not found.")
        return None

    log_C_range = np.array(surf["log_C_range"])
    log_R_range = np.array(surf["log_R_range"])
    ll_surface  = np.array(surf["ll_surface"])
    log_C_true  = surf["log_C_true"]
    log_R_true  = surf["log_R_true"]

    # Clip outliers for better colour resolution
    vmin = np.percentile(ll_surface, 2)
    vmax = np.percentile(ll_surface, 98)
    ll_plot = np.clip(ll_surface, vmin, vmax)

    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    C_grid, R_grid = np.meshgrid(log_C_range, log_R_range)
    cs = ax.contourf(C_grid, R_grid, ll_plot, levels=20, cmap="viridis_r")
    plt.colorbar(cs, ax=ax, label="Neg. log-likelihood")
    ax.contour(C_grid, R_grid, ll_plot, levels=20, colors="white", linewidths=0.4, alpha=0.4)

    # True / configured value
    ax.plot(log_C_true, log_R_true, "w+", ms=14, mew=2.0, label="True / configured")

    # Grid optimum
    min_idx = np.unravel_index(np.argmin(ll_surface), ll_surface.shape)
    log_C_opt = float(log_C_range[min_idx[1]])
    log_R_opt = float(log_R_range[min_idx[0]])
    ax.plot(log_C_opt, log_R_opt, "r*", ms=12, label=f"Grid opt  (C={math.exp(log_C_opt):.2e}, R={math.exp(log_R_opt):.4f})")

    _style_ax(ax,
              f"Figure 6 — Neg-LL surface: {room_name.replace('_', ' ').title()}",
              "log(Thermal mass C)  [log J/K]",
              "log(Ext. resistance R_ext)  [log K/W]")
    ax.legend(fontsize=8)
    fig.tight_layout()
    return fig


# ── Snapshot mode helpers ─────────────────────────────────────────────────────

def load_snapshot(path: str) -> List[Dict[str, Any]]:
    """Load a JSON history buffer exported from Home Assistant."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return data
    # Support {"history": [...]} envelope
    if isinstance(data, dict) and "history" in data:
        return data["history"]
    raise ValueError(f"Cannot parse snapshot at {path!r}: expected a list or {{history: [...]}} dict.")


# ── Window selection helper ───────────────────────────────────────────────────

def apply_window_selection(
    full_history: List[Dict[str, Any]],
    start_ts: Optional[float],
    end_ts: Optional[float],
    horizon_hours: Optional[float],
    dt: float = 60.0,
) -> List[Dict[str, Any]]:
    """Return the slice of *full_history* matching the requested window.

    Priority order:
    1. Explicit ``[start_ts, end_ts]`` range when both are given.
    2. Trailing ``horizon_hours`` window from the end of history.
    3. Full history when neither is specified.

    Parameters
    ----------
    full_history
        All available history records.
    start_ts, end_ts
        Optional explicit UNIX timestamps [s] for box-style selection.
        ``start_ts`` alone is also accepted (selects from that point to end).
    horizon_hours
        Optional trailing-window width [h].  Ignored when ``start_ts`` is set.
    dt
        Nominal sampling interval [s].
    """
    if not full_history:
        return []

    min_ts, max_ts = history_time_range(full_history)
    if min_ts is None or max_ts is None:
        return list(full_history)

    if start_ts is not None:
        eff_end = end_ts if end_ts is not None else max_ts
        window = select_window_by_timestamps(full_history, start_ts, eff_end)
        return window if window else list(full_history)

    if horizon_hours is not None and horizon_hours > 0:
        return select_recent_window(full_history, horizon_hours * 3600.0)

    return list(full_history)


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Heating Assistant — local model-fit visualisation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--snapshot",   metavar="PATH",
                   help="JSON history buffer exported from HA (snapshot mode)")
    p.add_argument("--room",       metavar="NAME", default=None,
                   help="Show only this room (default: all rooms)")
    p.add_argument("--save-dir",   metavar="DIR",  default=None,
                   help="Save PNGs to this directory instead of displaying interactively")
    p.add_argument("--no-display", action="store_true",
                   help="Skip plt.show() even when --save-dir is not set")
    p.add_argument("--dt",         type=float, default=60.0,
                   help="Sampling interval in seconds (default 60)")
    p.add_argument("--n-steps",    type=int,   default=240,
                   help="Steps to simulate in synthetic mode (default 240)")
    p.add_argument("--seg-len",    type=int,   default=30,
                   help="Open-loop segment length in steps (default 30)")
    p.add_argument("--mass-factor", type=float, default=2.0,
                   help="Thermal-mass misspecification factor (default 2.0)")
    p.add_argument("--grid-n",     type=int,   default=30,
                   help="Grid resolution for LL surface (default 30)")
    p.add_argument("--no-fig4",    action="store_true", help="Skip open-loop figure")
    p.add_argument("--no-fig6",    action="store_true", help="Skip LL surface figure")

    # Window selection arguments
    win = p.add_argument_group(
        "Window selection",
        "Control which slice of the history is used for identification.\n"
        "Priority: --start/--end > --horizon > full history.\n"
        "Use --interactive for the Grafana-like visual selector.",
    )
    win.add_argument(
        "--start", metavar="DATETIME_OR_TS", default=None,
        help=(
            "Start of identification window.  "
            "Accepts 'YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DD HH:MM', 'YYYY-MM-DD', "
            "or a UNIX timestamp float.  "
            "Use with --end to define a box selection."
        ),
    )
    win.add_argument(
        "--end", metavar="DATETIME_OR_TS", default=None,
        help=(
            "End of identification window (same format as --start).  "
            "If --start is given but --end is omitted, the window runs "
            "from --start to the last record in the history."
        ),
    )
    win.add_argument(
        "--horizon", metavar="HOURS", type=float, default=None,
        help=(
            "Trailing window width in hours: use the most recent N hours of "
            "data (equivalent to the HA dashboard horizon slider).  "
            "Ignored when --start is given."
        ),
    )
    win.add_argument(
        "--interactive", action="store_true",
        help=(
            "Launch a Grafana-style interactive window selector.  "
            "Shows a full-history overview with a draggable span brush; "
            "close the window to confirm the selection.  "
            "Requires an interactive matplotlib backend (TkAgg / Qt5Agg)."
        ),
    )

    return p.parse_args()


def _save_or_show(figs: List[Tuple[str, Optional[plt.Figure]]], save_dir: Optional[str],
                  no_display: bool) -> None:
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
    for name, fig in figs:
        if fig is None:
            continue
        if save_dir:
            out = os.path.join(save_dir, f"{name}.png")
            fig.savefig(out, bbox_inches="tight", dpi=DPI)
            print(f"  Saved {out}")
        if not no_display and not save_dir:
            plt.figure(fig.number)
    if not no_display and not save_dir:
        plt.show()


def main() -> None:
    args = parse_args()

    print("=" * 60)
    print("  Heating Assistant — model-fit visualisation")
    print("=" * 60)

    # Parse explicit window arguments
    start_ts: Optional[float] = None
    end_ts:   Optional[float] = None

    if args.start is not None:
        try:
            start_ts = _parse_datetime_arg(args.start)
            print(f"  Window start : {_ts_to_label(start_ts) if _is_real_ts(start_ts) else f'{start_ts:.0f} s'}")
        except (ValueError, OSError) as exc:
            print(f"  Error parsing --start {args.start!r}: {exc}")
            sys.exit(1)

    if args.end is not None:
        try:
            end_ts = _parse_datetime_arg(args.end)
            print(f"  Window end   : {_ts_to_label(end_ts) if _is_real_ts(end_ts) else f'{end_ts:.0f} s'}")
        except (ValueError, OSError) as exc:
            print(f"  Error parsing --end {args.end!r}: {exc}")
            sys.exit(1)

    if args.horizon is not None:
        print(f"  Horizon      : {args.horizon:.1f} h (trailing window from end of history)")

    if args.snapshot:
        # ── Snapshot mode ────────────────────────────────────────
        print(f"  Mode   : snapshot ({args.snapshot!r})")
        full_history = load_snapshot(args.snapshot)
        dt           = args.dt

        # Infer room names from the first record with a non-empty "y"
        n_rooms = max(len(r.get("y", [])) for r in full_history)
        room_names_inferred = [f"room_{i}" for i in range(n_rooms)]

        # Try to read room names from "setpoint" dict in history
        for rec in full_history:
            sp = rec.get("setpoint", {})
            if isinstance(sp, dict) and sp:
                room_names_inferred = list(sp.keys())
                break

        if args.room:
            room_names_inferred = [nm for nm in room_names_inferred if nm == args.room]

        print(f"  Rooms  : {room_names_inferred}")
        print(f"  Steps  : {len(full_history)} total")

        # ── Interactive window selection ─────────────────────────
        if args.interactive:
            start_ts, end_ts = interactive_window_selector(
                full_history, room_names_inferred, dt,
                initial_start_ts=start_ts, initial_end_ts=end_ts,
            )

        # Apply window selection
        history = apply_window_selection(full_history, start_ts, end_ts, args.horizon, dt)
        print(f"  Window : {len(history)} steps")

        # Dummy Room objects for heat flow calculation (no real params available)
        rooms_dummy = [
            Room(name=nm, thermal_mass=5e6, r_external=0.05, temperature=20.0)
            for nm in room_names_inferred
        ]
        sources_dummy = _make_sources(rooms_dummy)

        figs: List[Tuple[str, Optional[plt.Figure]]] = []
        figs.append(("fig0_window_overview",
                     fig_window_overview(full_history, history, room_names_inferred, dt)))
        figs.append(("fig1_temperature_history",
                     fig_temperature_history(history, room_names_inferred, None, rooms_dummy, dt)))
        figs.append(("fig3_heating_power",
                     fig_heating_power(history, room_names_inferred, sources_dummy, dt)))
        figs.append(("fig5_heat_flow_breakdown",
                     fig_heat_flow_breakdown(history, room_names_inferred, rooms_dummy, sources_dummy, dt)))

        print("  Note: Figs 2, 4, 6 require model predictions — not available in snapshot mode")
        print("        (run in synthetic mode to see all figures)")

    else:
        # ── Synthetic mode ────────────────────────────────────────
        print(f"  Mode        : synthetic (mass_factor={args.mass_factor}×)")
        print(f"  Steps       : {args.n_steps}  dt={args.dt}s")
        print(f"  Seg length  : {args.seg_len} steps = {args.seg_len * args.dt / 60:.0f} min")

        rooms_true   = _make_rooms_true()
        rooms_missp  = _make_rooms_misspec(rooms_true, args.mass_factor)
        sources      = _make_sources(rooms_true)
        dt           = args.dt

        all_room_names = [r.name for r in rooms_true]
        room_names = [nm for nm in all_room_names if not args.room or nm == args.room]

        print(f"  Rooms       : {room_names}")

        # Generate history with the TRUE model
        print("  Generating history... ", end="", flush=True)
        full_history = generate_synthetic_history(rooms_true, sources, dt, args.n_steps)
        print("done")

        # ── Interactive window selection ─────────────────────────
        if args.interactive:
            start_ts, end_ts = interactive_window_selector(
                full_history, room_names, dt,
                initial_start_ts=start_ts, initial_end_ts=end_ts,
            )

        # Apply window selection
        history = apply_window_selection(full_history, start_ts, end_ts, args.horizon, dt)
        print(f"  Window      : {len(history)} of {len(full_history)} steps")

        if len(history) < 2:
            print("  Error: selected window contains fewer than 2 records — aborting.")
            sys.exit(1)

        # One-step predictions using the MISSPECIFIED model (on the window)
        print("  Computing one-step predictions (misspec)... ", end="", flush=True)
        y_preds = compute_one_step_preds(history, rooms_missp, sources, dt)
        print("done")

        figs: List[Tuple[str, Optional[plt.Figure]]] = []

        print("  Rendering Fig 0 (window overview)... ", end="", flush=True)
        figs.append(("fig0_window_overview",
                     fig_window_overview(full_history, history, room_names, dt)))
        print("done")

        print("  Rendering Fig 1 (temperature history)... ", end="", flush=True)
        figs.append(("fig1_temperature_history",
                     fig_temperature_history(history, room_names, y_preds, rooms_true, dt)))
        print("done")

        print("  Rendering Fig 2 (prediction error + ACF)... ", end="", flush=True)
        figs.append(("fig2_prediction_error_acf",
                     fig_prediction_error_acf(history, room_names, y_preds, dt)))
        print("done")

        print("  Rendering Fig 3 (heating power)... ", end="", flush=True)
        figs.append(("fig3_heating_power",
                     fig_heating_power(history, room_names, sources, dt)))
        print("done")

        if not args.no_fig4:
            print("  Rendering Fig 4 (open-loop simulation, misspec model)... ", end="", flush=True)
            figs.append(("fig4_open_loop_simulation",
                         fig_open_loop_simulation(history, room_names, rooms_missp, sources, dt,
                                                  args.seg_len)))
            print("done")
        else:
            print("  Skipped Fig 4 (--no-fig4)")

        print("  Rendering Fig 5 (heat flow breakdown)... ", end="", flush=True)
        figs.append(("fig5_heat_flow_breakdown",
                     fig_heat_flow_breakdown(history, room_names, rooms_true, sources, dt)))
        print("done")

        if not args.no_fig6:
            focus_room = room_names[0]
            print(f"  Rendering Fig 6 (LL surface, room={focus_room!r}, grid={args.grid_n}×{args.grid_n})... ",
                  end="", flush=True)
            figs.append(("fig6_ll_surface",
                         fig_ll_surface(history, rooms_true, sources, focus_room, dt, args.grid_n)))
            print("done")
        else:
            print("  Skipped Fig 6 (--no-fig6)")

        # Summary statistics
        print()
        print("  One-step prediction RMSE (misspecified model):")
        for ridx, nm in enumerate(all_room_names):
            if nm not in room_names:
                continue
            residuals = [
                y_preds[k][ridx] - history[k + 1]["y"][ridx]
                for k in range(len(y_preds))
                if ridx < len(y_preds[k]) and ridx < len(history[k + 1].get("y", []))
            ]
            rmse = float(np.sqrt(np.mean(np.array(residuals) ** 2))) if residuals else float("nan")
            print(f"    {nm:20s}  {rmse:.4f}°C")

        # Print window summary
        if start_ts is not None or end_ts is not None or args.horizon is not None:
            ts_win = [float(r.get("timestamp", 0.0)) for r in history]
            if ts_win:
                win_min_ts, win_max_ts = ts_win[0], ts_win[-1]
                if _is_real_ts(win_min_ts):
                    print(f"\n  Identification window: {_ts_to_label(win_min_ts)}  →  {_ts_to_label(win_max_ts)}")
                else:
                    print(f"\n  Identification window: {win_min_ts/60:.0f}–{win_max_ts/60:.0f} min "
                          f"({(win_max_ts - win_min_ts)/3600:.2f} h, {len(history)} steps)")

    print()
    _save_or_show(figs, args.save_dir, args.no_display)
    print("  Done.")


if __name__ == "__main__":
    main()
