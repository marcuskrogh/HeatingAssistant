"""Append-only control log (schema 2) and read-only export.

Day files live at ``<data_dir>/control_log/<instance>/<YYYY-MM-DD>.<kind>.jsonl``.
Files at least two UTC days old are gzipped. Tick and parameter files follow
``control_log_retention_days`` (default 400). Plan files default to 60 days.
``id_history`` is not written or deleted here.

Export walks day files as a generator. It does not append, purge, or rewrite.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import re
import threading
from collections.abc import Iterator, Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from heatingassistant.engine import const

SCHEMA = const.CONTROL_LOG_SCHEMA
GZIP_AFTER_DAYS = const.CONTROL_LOG_GZIP_AFTER_DAYS
SINCE = "2026.11.0"
KINDS = ("tick", "plan", "params")
_FILE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\.(tick|plan|params)\.jsonl(\.gz)?$"
)
_ID_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl(\.gz)?$")

_FIXED_COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    ("t_s", "t", "s", "Unix time of the control step"),
    ("t_iso", "t_iso", "", "UTC timestamp of the control step"),
    ("dt_actual_s", "dt_actual", "s", "Wall-clock seconds since the previous control step"),
    ("origin", "origin", "", "control_log, or id_history when the row is back-filled"),
    ("d_outdoor_degC", "d_outdoor", "°C", "Outdoor temperature used by the step"),
    ("price_per_kWh", "price", "currency/kWh", "Electricity price used by the step"),
    ("plan_id", "plan_id", "", "Identifier of the plan installed at this step"),
    ("params_hash", "params_hash", "", "Hash of the parameter set in force"),
    ("app_version", "app_version", "", "App version that wrote the row"),
    ("schema", "schema", "", "Control log schema version"),
)

_ROOM_COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    ("{slug}_y_fused_degC", "y_fused", "°C", "Fused room temperature measurement"),
    ("{slug}_x_hat_degC", "x_hat", "°C", "EKF air-temperature estimate"),
    ("{slug}_innovation_K", "innovation", "K", "EKF innovation"),
    ("{slug}_y_pred_1step_degC", "y_pred_1step", "°C", "One-step-ahead predicted air temperature"),
    ("{slug}_q_model_W", "q_model_W", "W", "Model thermal power"),
    ("{slug}_q_model_display_W", "q_model_display_W", "W", "Configured display thermal power"),
    ("{slug}_heat_loss_model_W", "heat_loss_model_W", "W", "Outdoor heat loss UA(T_a-T_out) plus the open-window term"),
    ("{slug}_setpoint_degC", "setpoint", "°C", "Active setpoint"),
    ("{slug}_comfort_lower_degC", "lo", "°C", "Comfort band lower edge"),
    ("{slug}_comfort_upper_degC", "hi", "°C", "Comfort band upper edge"),
    ("{slug}_window_open", "window_open", "", "Window override active"),
    ("{slug}_d_solar_W", "d_solar", "W", "Solar disturbance"),
    ("{slug}_P_Ta", "P_Ta", "K^2", "EKF variance of the air temperature"),
    ("{slug}_y_sensors_json", "y_sensors", "", "Per-sensor room temperature measurements"),
)

_SOURCE_COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    ("{slug}_u_applied", "u_applied", "", "Control fraction applied on this step"),
    ("{slug}_u_planned0", "u_planned0", "", "First control fraction of the installed plan"),
    ("{slug}_phi", "phi", "", "Emitter filter state"),
    ("{slug}_hp_target_written_degC", "hp_target_written", "°C", "Climate target computed for the write, logged only"),
    ("{slug}_hp_internal_temp_degC", "hp_internal_temp", "°C", "Heat pump internal temperature, logged only"),
    ("{slug}_hp_offset_K", "hp_offset", "K", "Written target minus internal temperature, logged only"),
    ("{slug}_hvac_mode", "hvac_mode", "", "HVAC mode of the computed write payload"),
    ("{slug}_p_el_metered_W", "p_el_metered_W", "W", "Electric power inferred from the energy meter, logged only"),
    ("{slug}_energy_kwh", "energy_kwh", "kWh", "Heat pump energy meter reading, logged only"),
)

_PARAMS_COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    ("t_s", "t", "s", "Unix time of the parameter change"),
    ("t_iso", "t_iso", "", "UTC timestamp of the parameter change"),
    ("params_hash", "params_hash", "", "Hash of the parameter set"),
    ("app_version", "app_version", "", "App version that wrote the row"),
    ("schema", "schema", "", "Control log schema version"),
    ("rooms_json", "rooms", "", "Room parameter set"),
    ("sources_json", "sources", "", "Heat-source parameter set"),
    ("provenance_json", "provenance", "", "Estimation provenance when present"),
)

_ID_COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    ("t_s", "timestamp", "s", "Unix time of the identification sample"),
    ("t_iso", "t_iso", "", "UTC timestamp of the identification sample"),
    ("y_json", "y", "", "Measured room temperatures"),
    ("u_json", "u", "", "Applied control fractions"),
    ("d_outdoor_degC", "d_outdoor", "°C", "Outdoor temperature"),
    ("d_solar_json", "d_solar", "W", "Solar disturbance by room"),
    ("y_pred_json", "y_pred", "°C", "Predicted room temperatures stored with the sample"),
    ("window_open_json", "window_open", "", "Window override flags"),
    ("origin", "origin", "", "Always id_history for this kind"),
)

_LAST_VALUE_FIELDS = {
    "origin",
    "plan_id",
    "params_hash",
    "app_version",
    "schema",
    "window_open",
    "hvac_mode",
    "y_sensors",
    "rooms",
    "sources",
    "provenance",
    "y",
    "u",
    "d_solar",
    "y_pred",
    "t_iso",
}


def retention_from_options(options: Mapping[str, Any] | None) -> dict[str, int]:
    """Resolve tick, plan, and parameter retention in days."""

    base = {
        "tick": int(const.DEFAULT_CONTROL_LOG_TICK_RETENTION_DAYS),
        "plan": int(const.DEFAULT_CONTROL_LOG_PLAN_RETENTION_DAYS),
        "params": int(const.DEFAULT_CONTROL_LOG_PARAMS_RETENTION_DAYS),
    }
    raw = None if options is None else options.get(const.CONF_CONTROL_LOG_RETENTION_DAYS)
    if raw is None or raw == "":
        return base
    if isinstance(raw, Mapping):
        resolved = dict(base)
        for key in base:
            if raw.get(key) in (None, ""):
                continue
            resolved[key] = max(1, int(raw[key]))
        return resolved
    days = max(1, int(raw))
    return {
        "tick": days,
        "params": days,
        "plan": min(base["plan"], days),
    }


def plan_status(
    *,
    result: Mapping[str, Any] | None,
    applied: bool,
    watchdog_before: bool,
    watchdog_after: bool,
) -> str:
    """Map a finished solve onto accepted, rejected, timeout, or watchdog."""

    payload = result or {}
    status = str(payload.get("status") or "")
    if status == "timeout" or bool(payload.get("timed_out")):
        return "timeout"
    if watchdog_after and not watchdog_before:
        return "watchdog"
    if applied or bool(payload.get("accepted")):
        return "accepted"
    if watchdog_after:
        return "watchdog"
    return "rejected"


def json_ready(value: Any) -> Any:
    """Convert a record to JSON-safe objects. Non-finite floats become null."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if hasattr(value, "tolist"):
        try:
            return json_ready(value.tolist())
        except Exception:
            return None
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return json_ready(item())
        except Exception:
            return None
    return str(value)


def _parse_time(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _column_docs(
    rows: tuple[tuple[str, str, str, str], ...],
    *,
    slug: str | None = None,
) -> list[dict[str, str]]:
    docs: list[dict[str, str]] = []
    for header, name, unit, meaning in rows:
        csv_header = header if slug is None else header.format(slug=slug)
        field = name if slug is None else name
        docs.append(
            {
                "name": field,
                "csv_header": csv_header,
                "unit": unit,
                "meaning": meaning,
                "since": SINCE,
            }
        )
    return docs


def tick_columns(
    rooms: list[tuple[str, str]] | None = None,
    sources: list[tuple[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Column dictionary for a tick export. ``rooms`` and ``sources`` are (slug, name)."""

    columns = _column_docs(_FIXED_COLUMNS)
    for slug, _name in rooms or []:
        columns.extend(_column_docs(_ROOM_COLUMNS, slug=slug))
    for slug, _name in sources or []:
        columns.extend(_column_docs(_SOURCE_COLUMNS, slug=slug))
    return columns


def export_schema(
    *,
    rooms: list[tuple[str, str]] | None = None,
    sources: list[tuple[str, str]] | None = None,
    retention_days: Mapping[str, int] | None = None,
    id_retention_days: int = const.DEFAULT_PARAMETER_ESTIMATION_HISTORY_DAYS,
) -> dict[str, Any]:
    """Column dictionary served by ``GET /api/export/schema``."""

    retention = dict(retention_days or retention_from_options(None))
    tick = tick_columns(rooms, sources)
    params = _column_docs(_PARAMS_COLUMNS)
    ident = _column_docs(_ID_COLUMNS)
    return {
        "schema": SCHEMA,
        "since": SINCE,
        "gzip_after_days": GZIP_AFTER_DAYS,
        "retention_days": retention,
        "id_history_retention_days": int(id_retention_days),
        "id_history": "unchanged",
        "path": "control_log/<instance>/<YYYY-MM-DD>.<kind>.jsonl",
        "kinds": {
            "tick": {
                "formats": ["csv", "jsonl"],
                "resolutions": ["raw", "15m", "1h"],
                "default_format": "csv",
                "backfill": "id_history supplies y, u, d and y_pred when the control log has no row",
                "columns": tick,
                "csv_headers": [column["csv_header"] for column in tick],
            },
            "plan": {
                "formats": ["jsonl"],
                "resolutions": ["raw"],
                "default_format": "jsonl",
                "columns": [
                    {
                        "name": name,
                        "unit": unit,
                        "meaning": meaning,
                        "since": SINCE,
                    }
                    for name, unit, meaning in (
                        ("plan_id", "", "Identifier of this solve"),
                        ("t_start", "s", "Solve timestamp"),
                        ("t_end", "s", "End of the planned horizon"),
                        ("epoch", "s", "Plan epoch passed to the installer"),
                        ("status", "", "accepted, rejected, timeout, or watchdog"),
                        ("cost", "", "Accepted-test cost"),
                        ("cost_zero", "", "Cost of the zero-heat plan"),
                        ("nit", "", "Solver iterations"),
                        ("U", "", "Planned control trajectory"),
                        ("T_ref", "", "Planned air-temperature trajectory"),
                        ("x0", "", "Estimator state at the solve"),
                    )
                ],
                "csv_headers": None,
            },
            "params": {
                "formats": ["csv", "jsonl"],
                "resolutions": ["raw"],
                "default_format": "csv",
                "columns": params,
                "csv_headers": [column["csv_header"] for column in params],
            },
            "id": {
                "formats": ["csv", "jsonl"],
                "resolutions": ["raw", "15m", "1h"],
                "default_format": "csv",
                "columns": ident,
                "csv_headers": [column["csv_header"] for column in ident],
            },
        },
        "example_path": (
            "/api/export?start=2026-11-01T00:00:00Z&end=2026-11-08T00:00:00Z"
            "&kind=tick&room=living_room&format=csv&resolution=1h"
        ),
        "ingress": (
            "Served on the existing read-only App web API. "
            "Through Home Assistant Ingress the same paths are under the ingress prefix."
        ),
    }


class ExportQuery:
    """Validated ``GET /api/export`` query."""

    def __init__(
        self,
        *,
        kind: str,
        start: float,
        end: float,
        room: str | None,
        fmt: str,
        resolution: str,
    ) -> None:
        self.kind = kind
        self.start = start
        self.end = end
        self.room = room
        self.fmt = fmt
        self.resolution = resolution


def parse_export_query(
    query: Mapping[str, list[str]] | None,
    *,
    now: float | None = None,
    retention_days: Mapping[str, int] | None = None,
    id_retention_days: int = const.DEFAULT_PARAMETER_ESTIMATION_HISTORY_DAYS,
) -> ExportQuery:
    """Parse and validate an export query. Raises ValueError on a bad request."""

    raw = query or {}

    def first(name: str) -> str | None:
        values = raw.get(name)
        if not values:
            return None
        text = str(values[0]).strip()
        return text or None

    kind = (first("kind") or "tick").lower()
    if kind not in {"tick", "plan", "params", "id"}:
        raise ValueError("kind must be tick, plan, params, or id")
    resolution = (first("resolution") or "raw").lower()
    if kind == "plan":
        resolution = "raw"
    if resolution not in {"raw", "15m", "1h"}:
        raise ValueError("resolution must be raw, 15m, or 1h")
    if kind == "params" and resolution != "raw":
        raise ValueError("params export is raw only")
    fmt = (first("format") or ("jsonl" if kind == "plan" else "csv")).lower()
    if kind == "plan":
        fmt = "jsonl"
    if fmt not in {"csv", "jsonl"}:
        raise ValueError("format must be csv or jsonl")
    room = first("room")
    stamp = float(now if now is not None else datetime.now(timezone.utc).timestamp())
    end = _parse_time(first("end"))
    start = _parse_time(first("start"))
    if end is None:
        end = stamp
    if start is None:
        start = end - 7 * 86400
    if start > end:
        # Empty window. Callers still get a header and no rows.
        pass
    else:
        if kind == "id":
            limit_days = max(1, int(id_retention_days))
        else:
            retention = retention_from_options(None)
            retention.update(dict(retention_days or {}))
            limit_days = int(retention["tick"] if kind == "tick" else retention[kind])
        max_span = limit_days * 86400 + 7200
        if (end - start) > max_span:
            raise ValueError(
                f"range exceeds {limit_days} day retention for kind={kind}"
            )
    return ExportQuery(
        kind=kind,
        start=float(start),
        end=float(end),
        room=room,
        fmt=fmt,
        resolution=resolution,
    )


def _csv_line(values: list[Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["" if value is None else value for value in values])
    return buffer.getvalue()


def _flatten_tick(
    record: Mapping[str, Any],
    rooms: list[tuple[str, str]],
    sources: list[tuple[str, str]],
) -> dict[str, Any]:
    room_map = record.get("rooms") if isinstance(record.get("rooms"), Mapping) else {}
    source_map = record.get("sources") if isinstance(record.get("sources"), Mapping) else {}
    flat: dict[str, Any] = {
        "t_s": record.get("t"),
        "t_iso": record.get("t_iso") or (
            _iso(float(record["t"])) if record.get("t") is not None else None
        ),
        "dt_actual_s": record.get("dt_actual"),
        "origin": record.get("origin") or "control_log",
        "d_outdoor_degC": record.get("d_outdoor"),
        "price_per_kWh": record.get("price"),
        "plan_id": record.get("plan_id"),
        "params_hash": record.get("params_hash"),
        "app_version": record.get("app_version"),
        "schema": record.get("schema"),
    }
    for slug, _name in rooms:
        fields = room_map.get(slug) if isinstance(room_map.get(slug), Mapping) else {}
        sensors = fields.get("y_sensors")
        flat[f"{slug}_y_fused_degC"] = fields.get("y_fused")
        flat[f"{slug}_x_hat_degC"] = fields.get("x_hat")
        flat[f"{slug}_innovation_K"] = fields.get("innovation")
        flat[f"{slug}_y_pred_1step_degC"] = fields.get("y_pred_1step")
        flat[f"{slug}_q_model_W"] = fields.get("q_model_W")
        flat[f"{slug}_q_model_display_W"] = fields.get("q_model_display_W")
        flat[f"{slug}_heat_loss_model_W"] = fields.get("heat_loss_model_W")
        flat[f"{slug}_setpoint_degC"] = fields.get("setpoint")
        flat[f"{slug}_comfort_lower_degC"] = fields.get("lo")
        flat[f"{slug}_comfort_upper_degC"] = fields.get("hi")
        flat[f"{slug}_window_open"] = fields.get("window_open")
        flat[f"{slug}_d_solar_W"] = fields.get("d_solar")
        flat[f"{slug}_P_Ta"] = fields.get("P_Ta")
        flat[f"{slug}_y_sensors_json"] = (
            json.dumps(sensors, sort_keys=True, separators=(",", ":"))
            if isinstance(sensors, Mapping)
            else None
        )
    for slug, _name in sources:
        fields = source_map.get(slug) if isinstance(source_map.get(slug), Mapping) else {}
        flat[f"{slug}_u_applied"] = fields.get("u_applied")
        flat[f"{slug}_u_planned0"] = fields.get("u_planned0")
        flat[f"{slug}_phi"] = fields.get("phi")
        flat[f"{slug}_hp_target_written_degC"] = fields.get("hp_target_written")
        flat[f"{slug}_hp_internal_temp_degC"] = fields.get("hp_internal_temp")
        flat[f"{slug}_hp_offset_K"] = fields.get("hp_offset")
        flat[f"{slug}_hvac_mode"] = fields.get("hvac_mode")
        flat[f"{slug}_p_el_metered_W"] = fields.get("p_el_metered_W")
        flat[f"{slug}_energy_kwh"] = fields.get("energy_kwh")
    return flat


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and (
        not isinstance(value, float) or math.isfinite(value)
    )


def _add_numbers(
    sums: dict[str, float],
    counts: dict[str, int],
    value: Any,
    path: str,
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            _add_numbers(sums, counts, item, child)
        return
    if isinstance(value, list) or path.split(".")[-1] in _LAST_VALUE_FIELDS:
        return
    leaf = path.split(".")[-1]
    if leaf in {"t", "t_s", "timestamp", "schema"}:
        return
    if _is_number(value):
        sums[path] = sums.get(path, 0.0) + float(value)
        counts[path] = counts.get(path, 0) + 1


def _apply_means(
    value: Any,
    sums: dict[str, float],
    counts: dict[str, int],
    path: str,
) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _apply_means(item, sums, counts, f"{path}.{key}" if path else str(key))
            for key, item in value.items()
        }
    if path in sums and path in counts and counts[path] > 0:
        return sums[path] / counts[path]
    return value


def _resample(records: Iterator[dict[str, Any]], resolution: str) -> Iterator[dict[str, Any]]:
    if resolution == "raw":
        yield from records
        return
    width = 900 if resolution == "15m" else 3600
    bucket_t: int | None = None
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    last: dict[str, Any] | None = None

    def emit() -> dict[str, Any]:
        merged = _apply_means(last or {}, sums, counts, "")
        if not isinstance(merged, dict):
            merged = {}
        if bucket_t is not None:
            merged["t"] = float(bucket_t)
            merged["t_s"] = float(bucket_t)
            merged["timestamp"] = float(bucket_t)
            merged["t_iso"] = _iso(float(bucket_t))
        return merged

    for record in records:
        try:
            stamp = float(record.get("t") if record.get("t") is not None else record.get("timestamp"))
        except (TypeError, ValueError):
            continue
        key = int(stamp // width) * width
        if bucket_t is None:
            bucket_t = key
        elif key != bucket_t:
            yield emit()
            bucket_t = key
            sums = {}
            counts = {}
            last = None
        last = dict(record)
        _add_numbers(sums, counts, record, "")
    if bucket_t is not None:
        yield emit()


def _room_kept(record: Mapping[str, Any], room: str | None) -> bool:
    if not room:
        return True
    rooms = record.get("rooms")
    if isinstance(rooms, Mapping):
        return room in rooms
    return True


class ControlLogStore:
    """Per-instance JSONL control log."""

    def __init__(
        self,
        data_dir: str | Path,
        instance_id: str = "default",
        *,
        retention_days: Mapping[str, int] | None = None,
    ) -> None:
        self._dir = Path(data_dir) / "control_log" / (instance_id or "default")
        self._retention = dict(retention_days or retention_from_options(None))
        self._lock = threading.Lock()
        self._last_t: dict[str, float | None] = {kind: None for kind in KINDS}
        self._last_plan_id: str | None = None
        self._last_params_hash: str | None = None
        self._meter: tuple[float, float] | None = None
        self._last_maintenance: date | None = None

    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def retention_days(self) -> dict[str, int]:
        return dict(self._retention)

    @property
    def last_plan_id(self) -> str | None:
        return self._last_plan_id

    @property
    def last_params_hash(self) -> str | None:
        return self._last_params_hash

    def set_retention(self, retention_days: Mapping[str, int]) -> None:
        self._retention = {key: max(1, int(retention_days[key])) for key in KINDS}

    def setup(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self.maintenance()
        for kind in KINDS:
            self._last_t[kind] = self._read_last_t(kind)
        self._last_plan_id = self._read_last_plan_id()
        self._last_params_hash = self._read_last_params_hash()
        self._meter = self._read_meter()

    def last_t(self, kind: str) -> float | None:
        return self._last_t.get(kind)

    def append(self, record: Mapping[str, Any]) -> None:
        """Append one record and then gzip or purge aged files."""

        kind = str(record.get("kind") or "")
        if kind not in KINDS:
            raise ValueError(f"unknown control log kind {kind!r}")
        payload = json_ready(dict(record))
        payload["schema"] = SCHEMA
        payload["kind"] = kind
        try:
            stamp = float(payload.get("t"))
        except (TypeError, ValueError):
            stamp = datetime.now(timezone.utc).timestamp()
            payload["t"] = stamp
        if not payload.get("t_iso"):
            payload["t_iso"] = _iso(stamp)
        day = datetime.fromtimestamp(stamp, tz=timezone.utc).date()
        line = json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
        with self._lock:
            path = self._write_path(day, kind)
            if path.suffix == ".gz":
                with gzip.open(path, "at", encoding="utf-8") as handle:
                    handle.write(line)
            else:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
            self._last_t[kind] = stamp
            if kind == "plan" and payload.get("status") == "accepted" and payload.get("plan_id"):
                self._last_plan_id = str(payload["plan_id"])
            if kind == "params" and payload.get("params_hash"):
                self._last_params_hash = str(payload["params_hash"])
            today = datetime.now(timezone.utc).date()
            if self._last_maintenance != today:
                self._maintenance_locked(today)
                self._last_maintenance = today

    def note_meter(self, t: float, energy_kwh: float | None) -> float | None:
        """Return average watts since the previous meter sample, when the gap is usable.

        The house meter resolves 0.1 kWh, so a step shorter than 30 minutes
        does not update the published power. The cursor is logging state only.
        """

        if energy_kwh is None:
            return None
        power: float | None = None
        commit = True
        with self._lock:
            prev = self._meter
            if prev is None:
                self._meter = (float(t), float(energy_kwh))
            else:
                dt_h = (float(t) - prev[0]) / 3600.0
                delta = float(energy_kwh) - prev[1]
                if dt_h >= 0.5 and delta >= -1e-9:
                    power = (max(0.0, delta) / dt_h) * 1000.0
                    self._meter = (float(t), float(energy_kwh))
                else:
                    commit = False
            if not commit:
                return power
            cursor = self._dir / "meter_cursor.json"
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
                cursor.write_text(
                    json.dumps({"t": float(t), "energy_kwh": float(energy_kwh)}),
                    encoding="utf-8",
                )
            except OSError:
                pass
        return power

    def maintenance(self, today: date | None = None) -> None:
        with self._lock:
            stamp = today or datetime.now(timezone.utc).date()
            self._maintenance_locked(stamp)
            if today is None:
                self._last_maintenance = stamp

    def iter_records(
        self,
        kind: str,
        start: float,
        end: float,
    ) -> Iterator[dict[str, Any]]:
        """Yield records of ``kind`` with ``t`` in ``[start, end]``, in time order."""

        if start > end:
            return
            yield  # pragma: no cover - makes this a generator
        start_day = datetime.fromtimestamp(start, tz=timezone.utc).date() - timedelta(days=1)
        end_day = datetime.fromtimestamp(end, tz=timezone.utc).date() + timedelta(days=1)
        for path in self._kind_files(kind):
            parsed = _FILE_RE.match(path.name)
            if parsed is None:
                continue
            day = date.fromisoformat(parsed.group(1))
            if day < start_day or day > end_day:
                continue
            yield from _records_in_file(path, start, end, time_key="t")

    def iter_export(
        self,
        spec: ExportQuery,
        *,
        rooms: list[tuple[str, str]] | None = None,
        sources: list[tuple[str, str]] | None = None,
        id_history_dir: Path | None = None,
    ) -> Iterator[str]:
        """Yield CSV or JSONL text, one line at a time, including the CSV header."""

        room_pairs = list(rooms or [])
        source_pairs = list(sources or [])
        if spec.fmt == "csv":
            headers = _headers_for(spec.kind, room_pairs, source_pairs)
            yield _csv_line(headers)
        if spec.start > spec.end:
            return
        rows = self._iter_export_records(
            spec,
            rooms=room_pairs,
            sources=source_pairs,
            id_history_dir=id_history_dir,
        )
        rows = _resample(rows, spec.resolution)
        if spec.fmt == "jsonl":
            for record in rows:
                yield json.dumps(json_ready(record), separators=(",", ":"), sort_keys=True) + "\n"
            return
        headers = _headers_for(spec.kind, room_pairs, source_pairs)
        for record in rows:
            flat = _flatten_export(spec.kind, record, room_pairs, source_pairs)
            yield _csv_line([flat.get(header) for header in headers])

    def _iter_export_records(
        self,
        spec: ExportQuery,
        *,
        rooms: list[tuple[str, str]],
        sources: list[tuple[str, str]],
        id_history_dir: Path | None,
    ) -> Iterator[dict[str, Any]]:
        if spec.kind == "id":
            yield from _iter_id_records(id_history_dir, spec.start, spec.end)
            return
        if spec.kind in {"plan", "params"}:
            yield from self.iter_records(spec.kind, spec.start, spec.end)
            return
        log_rows = (
            record
            for record in self.iter_records("tick", spec.start, spec.end)
            if _room_kept(record, spec.room)
        )
        id_rows = (
            _id_as_tick(record, rooms, sources)
            for record in _iter_id_records(id_history_dir, spec.start, spec.end)
        )
        if spec.room:
            id_rows = (record for record in id_rows if _room_kept(record, spec.room))
        yield from _merge_prefer_log(log_rows, id_rows)

    def _write_path(self, day: date, kind: str) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        plain = self._dir / f"{day.isoformat()}.{kind}.jsonl"
        gz_path = self._dir / f"{day.isoformat()}.{kind}.jsonl.gz"
        if gz_path.exists() and not plain.exists():
            return gz_path
        return plain

    def _kind_files(self, kind: str) -> list[Path]:
        if not self._dir.is_dir():
            return []
        found: list[Path] = []
        for path in self._dir.iterdir():
            match = _FILE_RE.match(path.name)
            if match and match.group(2) == kind:
                found.append(path)
        return sorted(found, key=lambda item: item.name)

    def _maintenance_locked(self, today: date) -> None:
        if not self._dir.is_dir():
            return
        plain_by_key: dict[tuple[date, str], Path] = {}
        gz_by_key: dict[tuple[date, str], Path] = {}
        for path in list(self._dir.iterdir()):
            match = _FILE_RE.match(path.name)
            if match is None:
                continue
            day = date.fromisoformat(match.group(1))
            kind = match.group(2)
            if match.group(3):
                gz_by_key[(day, kind)] = path
            else:
                plain_by_key[(day, kind)] = path
        keys = set(plain_by_key) | set(gz_by_key)
        for day, kind in keys:
            age = (today - day).days
            retention = int(self._retention.get(kind, 400))
            plain = plain_by_key.get((day, kind))
            gz_path = gz_by_key.get((day, kind))
            if age > retention:
                if plain is not None:
                    plain.unlink(missing_ok=True)
                if gz_path is not None:
                    gz_path.unlink(missing_ok=True)
                continue
            if plain is not None and age >= GZIP_AFTER_DAYS:
                _gzip_replace(plain, gz_path)

    def _read_last_t(self, kind: str) -> float | None:
        files = self._kind_files(kind)
        if not files:
            return None
        last: float | None = None
        for record in _records_in_file(files[-1], float("-inf"), float("inf"), time_key="t"):
            try:
                last = float(record["t"])
            except (KeyError, TypeError, ValueError):
                continue
        return last

    def _read_last_plan_id(self) -> str | None:
        files = self._kind_files("plan")
        if not files:
            return None
        found: str | None = None
        for record in _records_in_file(files[-1], float("-inf"), float("inf"), time_key="t"):
            if record.get("status") == "accepted" and record.get("plan_id"):
                found = str(record["plan_id"])
        return found

    def _read_last_params_hash(self) -> str | None:
        files = self._kind_files("params")
        if not files:
            return None
        found: str | None = None
        for record in _records_in_file(files[-1], float("-inf"), float("inf"), time_key="t"):
            if record.get("params_hash"):
                found = str(record["params_hash"])
        return found

    def _read_meter(self) -> tuple[float, float] | None:
        path = self._dir / "meter_cursor.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return float(payload["t"]), float(payload["energy_kwh"])
        except (OSError, ValueError, TypeError, KeyError):
            return None


def _gzip_replace(plain: Path, existing_gz: Path | None) -> None:
    target = plain.with_name(plain.name + ".gz")
    tmp = target.with_name(target.name + ".tmp")
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as handle:
            if existing_gz is not None and existing_gz.exists():
                with gzip.open(existing_gz, "rt", encoding="utf-8") as source:
                    for line in source:
                        handle.write(line)
            with plain.open("r", encoding="utf-8") as source:
                for line in source:
                    handle.write(line)
        tmp.replace(target)
        plain.unlink(missing_ok=True)
        if existing_gz is not None and existing_gz.exists() and existing_gz != target:
            existing_gz.unlink(missing_ok=True)
    except OSError:
        tmp.unlink(missing_ok=True)


def _open_text(path: Path) -> Iterator[str]:
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:  # type: ignore[operator]
        for line in handle:
            yield line


def _records_in_file(
    path: Path,
    start: float,
    end: float,
    *,
    time_key: str,
) -> Iterator[dict[str, Any]]:
    try:
        lines = _open_text(path)
    except OSError:
        return
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        try:
            stamp = float(record.get(time_key))
        except (TypeError, ValueError):
            continue
        if start <= stamp <= end:
            yield record


def _iter_id_records(
    directory: Path | None,
    start: float,
    end: float,
) -> Iterator[dict[str, Any]]:
    if directory is None or not directory.is_dir() or start > end:
        return
    start_day = datetime.fromtimestamp(start, tz=timezone.utc).date() - timedelta(days=1)
    end_day = datetime.fromtimestamp(end, tz=timezone.utc).date() + timedelta(days=1)
    files = sorted(
        path
        for path in directory.iterdir()
        if _ID_FILE_RE.match(path.name)
    )
    for path in files:
        match = _ID_FILE_RE.match(path.name)
        if match is None:
            continue
        day = date.fromisoformat(match.group(1))
        if day < start_day or day > end_day:
            continue
        for record in _records_in_file(path, start, end, time_key="timestamp"):
            record["origin"] = "id_history"
            record["t"] = record.get("timestamp")
            try:
                record["t_iso"] = _iso(float(record["timestamp"]))
            except (TypeError, ValueError):
                pass
            yield record


def _id_as_tick(
    record: Mapping[str, Any],
    rooms: list[tuple[str, str]],
    sources: list[tuple[str, str]],
) -> dict[str, Any]:
    """Project an id_history sample onto the tick columns it actually has."""

    y = list(record.get("y") or [])
    u = list(record.get("u") or [])
    y_pred = record.get("y_pred")
    solar = record.get("d_solar") if isinstance(record.get("d_solar"), Mapping) else {}
    windows = record.get("window_open") if isinstance(record.get("window_open"), Mapping) else {}
    room_rows: dict[str, Any] = {}
    for index, (slug, name) in enumerate(rooms):
        room_rows[slug] = {
            "y_fused": y[index] if index < len(y) else None,
            "x_hat": None,
            "y_pred_1step": (
                y_pred[index]
                if isinstance(y_pred, list) and index < len(y_pred)
                else None
            ),
            "d_solar": solar.get(name, solar.get(slug)),
            "window_open": windows.get(name, windows.get(slug)),
        }
    source_rows: dict[str, Any] = {}
    for index, (slug, _name) in enumerate(sources):
        source_rows[slug] = {"u_applied": u[index] if index < len(u) else None}
    try:
        stamp = float(record.get("timestamp"))
    except (TypeError, ValueError):
        stamp = None
    return {
        "schema": SCHEMA,
        "kind": "tick",
        "origin": "id_history",
        "t": stamp,
        "t_iso": record.get("t_iso"),
        "dt_actual": None,
        "d_outdoor": record.get("d_outdoor"),
        "rooms": room_rows,
        "sources": source_rows,
    }


def _merge_prefer_log(
    log_rows: Iterator[dict[str, Any]],
    id_rows: Iterator[dict[str, Any]],
) -> Iterator[dict[str, Any]]:
    log = next(log_rows, None)
    ident = next(id_rows, None)
    while log is not None or ident is not None:
        if ident is None:
            yield log  # type: ignore[misc]
            log = next(log_rows, None)
            continue
        if log is None:
            yield ident
            ident = next(id_rows, None)
            continue
        try:
            log_t = float(log.get("t"))
            id_t = float(ident.get("t"))
        except (TypeError, ValueError):
            yield log
            log = next(log_rows, None)
            continue
        if abs(log_t - id_t) <= 1.0:
            yield log
            log = next(log_rows, None)
            ident = next(id_rows, None)
        elif log_t < id_t:
            yield log
            log = next(log_rows, None)
        else:
            yield ident
            ident = next(id_rows, None)


def _headers_for(
    kind: str,
    rooms: list[tuple[str, str]],
    sources: list[tuple[str, str]],
) -> list[str]:
    if kind == "params":
        return [column["csv_header"] for column in _column_docs(_PARAMS_COLUMNS)]
    if kind == "id":
        return [column["csv_header"] for column in _column_docs(_ID_COLUMNS)]
    return [column["csv_header"] for column in tick_columns(rooms, sources)]


def _flatten_export(
    kind: str,
    record: Mapping[str, Any],
    rooms: list[tuple[str, str]],
    sources: list[tuple[str, str]],
) -> dict[str, Any]:
    if kind == "tick":
        return _flatten_tick(record, rooms, sources)
    if kind == "id":
        return {
            "t_s": record.get("timestamp", record.get("t")),
            "t_iso": record.get("t_iso"),
            "y_json": _json_cell(record.get("y")),
            "u_json": _json_cell(record.get("u")),
            "d_outdoor_degC": record.get("d_outdoor"),
            "d_solar_json": _json_cell(record.get("d_solar")),
            "y_pred_json": _json_cell(record.get("y_pred")),
            "window_open_json": _json_cell(record.get("window_open")),
            "origin": "id_history",
        }
    return {
        "t_s": record.get("t"),
        "t_iso": record.get("t_iso"),
        "params_hash": record.get("params_hash"),
        "app_version": record.get("app_version"),
        "schema": record.get("schema"),
        "rooms_json": _json_cell(record.get("rooms")),
        "sources_json": _json_cell(record.get("sources")),
        "provenance_json": _json_cell(record.get("provenance")),
    }


def _json_cell(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(json_ready(value), sort_keys=True, separators=(",", ":"))
