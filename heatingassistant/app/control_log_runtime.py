"""Observe-only control-log writes for HeatingRuntime.

Failures here are logged and swallowed. Nothing in this module publishes
commands or changes estimator, planner, or actuator state.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from heatingassistant import __version__
from heatingassistant.app.control_log import (
    SCHEMA,
    ControlLogStore,
    ExportQuery,
    export_schema,
    json_ready,
    parse_export_query,
    plan_status,
    retention_from_options,
)
from heatingassistant.app.series import filtered_room_temperature, room_heat_loss_w
from heatingassistant.engine import const
from heatingassistant.engine.naming import room_slug
from heatingassistant.engine.parameter_lifecycle import estimated_params_snapshot

_logger = logging.getLogger(__name__)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _slug(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return room_slug(value)


class ControlLogMixin:
    """Durable tick, plan, and parameter records plus the export read API."""

    control_log: ControlLogStore

    def _init_control_log(self) -> None:
        self.control_log = ControlLogStore(
            self.data_dir,
            self.instance_id,
            retention_days=retention_from_options(self.options),
        )
        try:
            self.control_log.setup()
        except Exception:
            _logger.exception("Control log setup failed")
        self._record_control_log_params()

    def _bind_control_log(self) -> None:
        current = getattr(self, "control_log", None)
        expected = Path(self.data_dir) / "control_log" / self.instance_id
        if current is not None and current.directory == expected:
            current.set_retention(retention_from_options(self.options))
            return
        self._init_control_log()

    def export_schema(self) -> dict[str, Any]:
        rooms, sources = self._control_log_pairs()
        id_days = int(
            self.options.get(
                const.CONF_PARAMETER_ESTIMATION_HISTORY_DAYS,
                const.DEFAULT_PARAMETER_ESTIMATION_HISTORY_DAYS,
            )
        )
        return export_schema(
            rooms=rooms,
            sources=sources,
            retention_days=self.control_log.retention_days,
            id_retention_days=id_days,
        )

    def open_export(self, query: Mapping[str, list[str]] | None) -> tuple[str, Any]:
        """Return ``(content_type, line_iterator)``. Raises ValueError."""

        id_days = int(
            self.options.get(
                const.CONF_PARAMETER_ESTIMATION_HISTORY_DAYS,
                const.DEFAULT_PARAMETER_ESTIMATION_HISTORY_DAYS,
            )
        )
        spec = parse_export_query(
            query,
            retention_days=self.control_log.retention_days,
            id_retention_days=id_days,
        )
        content_type = (
            "text/csv; charset=utf-8"
            if spec.fmt == "csv"
            else "application/x-ndjson; charset=utf-8"
        )
        rooms, sources = self._control_log_pairs()
        return content_type, self.control_log.iter_export(
            spec,
            rooms=rooms,
            sources=sources,
            id_history_dir=self._id_history_dir(),
        )

    def _id_history_dir(self) -> Path:
        return Path(self.data_dir) / "id_history" / self.instance_id

    def _control_log_pairs(self) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        rooms: list[tuple[str, str]] = []
        for room in self._rooms():
            name = room.get("name")
            slug = _slug(name)
            if slug and isinstance(name, str):
                rooms.append((slug, name))
        sources: list[tuple[str, str]] = []
        for source in self._heat_sources():
            name = source.get("name")
            slug = _slug(name)
            if slug and isinstance(name, str):
                sources.append((slug, name))
        return rooms, sources

    def _record_control_log_tick(self, now: float) -> None:
        try:
            self._write_control_log_tick(float(now))
        except Exception:
            _logger.exception("Control log tick failed")

    def _write_control_log_tick(self, now: float) -> None:
        store = self.control_log
        previous = store.last_t("tick")
        dt_actual = None if previous is None else float(now) - float(previous)
        record = self._build_tick_record(now, dt_actual)
        store.append(record)
        self._record_control_log_params(now=now)

    def _build_tick_record(self, now: float, dt_actual: float | None) -> dict[str, Any]:
        outdoor = _finite(self._outdoor_temperature())
        price = _finite(self._electricity_price_value())
        controller = getattr(self.control_engine, "_controller", None)
        innovation = list(getattr(controller, "last_innovation", None) or [])
        predictions = []
        try:
            predictions = list(self.control_engine.forecast_snapshot().get("predictions") or [])
        except Exception:
            predictions = []
        first_pred = predictions[0] if predictions and isinstance(predictions[0], Mapping) else {}
        q_model: dict[str, float] = {}
        q_display: dict[str, float] = {}
        phi_by_source: dict[str, float] = {}
        p_diag: dict[str, float] = {}
        u_planned: dict[str, float] = {}
        if controller is not None:
            q_model, q_display = self._control_log_powers(controller, outdoor)
            phi_by_source = self._control_log_phi(controller)
            p_diag = self._control_log_p_diag(controller)
            u_planned = self._control_log_u_planned0(controller)

        energy_kwh, p_el = self._control_log_meter(now)
        try:
            solar_gains = dict(self._applied_solar_gains() or {})
        except Exception:
            solar_gains = {}
        rooms: dict[str, Any] = {}
        for room in self._rooms():
            name = room.get("name")
            slug = _slug(name)
            if not slug or not isinstance(name, str):
                continue
            y_fused = _finite(self.room_temperatures.get(name))
            x_hat = filtered_room_temperature(self, name)
            window_open = bool(self.is_window_override_active(name))
            setpoint, offset = self._control_log_setpoint(name)
            sensors: dict[str, float] = {}
            for tag in self._room_temp_tags(room):
                value = _finite(self.tag_values.get(tag))
                if value is not None:
                    sensors[tag] = value
            index = self._control_log_room_index(name)
            innov = innovation[index] if index is not None and index < len(innovation) else None
            rooms[slug] = {
                "name": name,
                "y_sensors": sensors,
                "y_fused": y_fused,
                "x_hat": x_hat,
                "P_Ta": p_diag.get(name),
                "innovation": _finite(innov),
                "y_pred_1step": _finite(first_pred.get(name)) if isinstance(first_pred, Mapping) else None,
                "q_model_W": q_model.get(name),
                "q_model_display_W": q_display.get(name),
                "heat_loss_model_W": room_heat_loss_w(
                    self,
                    name,
                    x_hat if x_hat is not None else y_fused,
                    outdoor,
                    window_open=window_open,
                ),
                "setpoint": setpoint,
                "lo": None if setpoint is None or offset is None else setpoint - offset,
                "hi": None if setpoint is None or offset is None else setpoint + offset,
                "window_open": window_open,
                "d_solar": _finite(solar_gains.get(name)),
            }

        sources: dict[str, Any] = {}
        for source_cfg in self._heat_sources():
            name = source_cfg.get("name")
            slug = _slug(name)
            if not slug or not isinstance(name, str):
                continue
            tag = source_cfg.get("output_tag")
            fraction = None
            if isinstance(tag, str):
                fraction = _finite(self.actuator_outputs.get(tag))
            payload = self._control_log_write_payload(source_cfg, fraction)
            internal = _finite((payload or {}).get("_internal_temp"))
            target = _finite((payload or {}).get("temperature"))
            offset = None
            if internal is not None and target is not None:
                offset = target - internal
            meter_power = p_el if str(source_cfg.get("type", "")).lower() == "heat_pump" else None
            meter_energy = energy_kwh if meter_power is not None or str(source_cfg.get("type", "")).lower() == "heat_pump" else None
            sources[slug] = {
                "name": name,
                "u_applied": fraction,
                "u_planned0": u_planned.get(name),
                "phi": phi_by_source.get(name),
                "hp_target_written": target,
                "hp_internal_temp": internal,
                "hp_offset": offset,
                "hvac_mode": None if payload is None else payload.get("hvac_mode"),
                "p_el_metered_W": meter_power,
                "energy_kwh": meter_energy,
            }
        return {
            "schema": SCHEMA,
            "kind": "tick",
            "origin": "control_log",
            "t": now,
            "dt_actual": dt_actual,
            "d_outdoor": outdoor,
            "price": price,
            "plan_id": self.control_log.last_plan_id,
            "params_hash": self.control_log.last_params_hash,
            "app_version": __version__,
            "rooms": rooms,
            "sources": sources,
        }

    def _control_log_setpoint(self, room_name: str) -> tuple[float | None, float | None]:
        try:
            effective = self._resolve_effective_params().get(room_name)
        except Exception:
            effective = None
        if effective is not None:
            return _finite(getattr(effective, "setpoint", None)), _finite(
                getattr(effective, "comfort_offset", None)
            )
        for room in self._rooms():
            if room.get("name") != room_name:
                continue
            setpoint = _finite(room.get("setpoint"))
            if setpoint is None:
                setpoint = 21.0
            offset = _finite(room.get("comfort_offset"))
            if offset is None:
                offset = _finite(self.options.get("comfort_offset", const.DEFAULT_COMFORT_OFFSET))
            return setpoint, offset
        return None, None

    def _control_log_room_index(self, room_name: str) -> int | None:
        model = getattr(self.control_engine, "model", None)
        names = list(getattr(model, "room_names", []) or [])
        if room_name in names:
            return names.index(room_name)
        return None

    def _control_log_powers(
        self,
        controller: Any,
        outdoor: float | None,
    ) -> tuple[dict[str, float], dict[str, float]]:
        outdoor_f = 0.0 if outdoor is None else float(outdoor)
        try:
            system = controller._system
            u_prev = controller._mpc._u_prev
            model = system.heating_powers(u_prev, outdoor_f)
            display = system.display_heating_powers(u_prev, outdoor_f)
            return (
                {str(key): float(value) for key, value in dict(model).items()},
                {str(key): float(value) for key, value in dict(display).items()},
            )
        except Exception:
            return {}, {}

    def _control_log_phi(self, controller: Any) -> dict[str, float]:
        try:
            system = controller._system
            x_hat = controller._ekf.x_hat
            nx = int(system._nx_phys)
            found: dict[str, float] = {}
            for index, source in enumerate(system._sources):
                slot = int(system._filter_idx_for_source[index])
                if slot < 0:
                    continue
                found[str(source.name)] = float(x_hat[nx + slot])
            return found
        except Exception:
            return {}

    def _control_log_p_diag(self, controller: Any) -> dict[str, float]:
        try:
            matrix = controller._ekf.P
            names = list(controller._system._room_list)
            return {str(name): float(matrix[index, index]) for index, name in enumerate(names)}
        except Exception:
            return {}

    def _control_log_u_planned0(self, controller: Any) -> dict[str, float]:
        try:
            plan = controller._nmpc_U
            if plan is None:
                return {}
            names = [source.name for source in controller._sources]
            return {
                str(name): float(plan[0, index])
                for index, name in enumerate(names)
                if plan.shape[0] > 0 and index < plan.shape[1]
            }
        except Exception:
            return {}

    def _control_log_write_payload(
        self,
        source_cfg: Mapping[str, Any],
        fraction: float | None,
    ) -> dict[str, Any] | None:
        if fraction is None:
            return None
        try:
            enabled = bool(self._source_actuation_enabled(source_cfg))
            payload = dict(
                self._climate_write_value(source_cfg, float(fraction), enabled=enabled)
            )
        except Exception:
            return None
        state_tag = source_cfg.get("state_tag")
        attrs = self.tag_attributes.get(state_tag) if isinstance(state_tag, str) else None
        if isinstance(attrs, Mapping):
            payload["_internal_temp"] = attrs.get("current_temperature")
        return payload

    def _control_log_meter(self, now: float) -> tuple[float | None, float | None]:
        entity = str(
            self.options.get("heat_pump_energy_entity") or const.HEAT_PUMP_ENERGY_ENTITY
        )
        reading = self._control_log_entity_reading(entity)
        try:
            power = self.control_log.note_meter(now, reading)
        except Exception:
            _logger.exception("Control log meter cursor failed")
            power = None
        return reading, power

    def _control_log_entity_reading(self, entity_id: str) -> float | None:
        for binding in self.bindings:
            if binding.direction == "in" and binding.entity_id == entity_id:
                value = _finite(self.tag_values.get(binding.tag))
                if value is not None:
                    return value
        return _finite(self.tag_values.get(entity_id))

    def _record_control_log_params(self, now: float | None = None) -> None:
        try:
            self._write_control_log_params(now)
        except Exception:
            _logger.exception("Control log parameter record failed")

    def _write_control_log_params(self, now: float | None) -> None:
        snapshot = self._control_log_param_snapshot()
        encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
        if digest == self.control_log.last_params_hash:
            return
        stamp = float(now if now is not None else time.time())
        self.control_log.append(
            {
                "schema": SCHEMA,
                "kind": "params",
                "t": stamp,
                "params_hash": digest,
                "rooms": snapshot["rooms"],
                "sources": snapshot["sources"],
                "provenance": snapshot["provenance"],
                "app_version": __version__,
            }
        )

    def _control_log_param_snapshot(self) -> dict[str, Any]:
        rooms: dict[str, Any] = {}
        model_rooms = getattr(getattr(self.control_engine, "model", None), "rooms", {}) or {}
        for name, room in dict(model_rooms).items():
            r_external = _finite(getattr(room, "r_external", None))
            thermal_mass = _finite(getattr(room, "thermal_mass", None))
            tau_h = None
            if r_external is not None and thermal_mass is not None and r_external > 0:
                tau_h = thermal_mass * r_external / 3600.0
            rooms[str(name)] = {
                "thermal_mass": thermal_mass,
                "r_external": r_external,
                "ua": None if r_external is None or r_external <= 0 else 1.0 / r_external,
                "internal_gain": _finite(getattr(room, "internal_gain", None)),
                "ua_open": _finite(getattr(room, "ua_open", None)),
                "solar_scale": _finite(getattr(room, "solar_scale", None)),
                "tau_h": tau_h,
            }
        sources: dict[str, Any] = {}
        for source in getattr(self.control_engine, "heat_sources", []) or []:
            sources[str(getattr(source, "name", ""))] = {
                "type": type(source).__name__,
                "room": getattr(source, "room", None),
                "max_power": _finite(getattr(source, "max_power", None)),
                "min_power": _finite(getattr(source, "min_power", None)),
                "cop_rated": _finite(getattr(source, "cop_rated", None)),
                "power_scale": _finite(getattr(source, "power_scale", None)),
                "max_temp_offset": _finite(getattr(source, "max_temp_offset", None)),
                "delta_sat": _finite(getattr(source, "delta_sat", None)),
            }
        provenance = estimated_params_snapshot(self.options) or {"source": "configured"}
        return {
            "rooms": json_ready(rooms),
            "sources": json_ready(sources),
            "provenance": json_ready(provenance),
        }

    def _record_control_log_plan(
        self,
        result: Mapping[str, Any] | None,
        *,
        applied: bool,
        watchdog_before: bool,
        watchdog_after: bool,
        now: float,
        epoch: float | None = None,
    ) -> None:
        try:
            self._write_control_log_plan(
                result,
                applied=applied,
                watchdog_before=watchdog_before,
                watchdog_after=watchdog_after,
                now=float(now),
                epoch=epoch,
            )
        except Exception:
            _logger.exception("Control log plan record failed")

    def _write_control_log_plan(
        self,
        result: Mapping[str, Any] | None,
        *,
        applied: bool,
        watchdog_before: bool,
        watchdog_after: bool,
        now: float,
        epoch: float | None,
    ) -> None:
        payload = dict(result or {})
        status = plan_status(
            result=payload,
            applied=applied,
            watchdog_before=watchdog_before,
            watchdog_after=watchdog_after,
        )
        horizon_s = 0.0
        try:
            horizon_s = float(self.control_engine.forecast_snapshot().get("horizon") or 0.0)
            horizon_s *= float(self.control_engine.forecast_snapshot().get("dt") or 0.0)
        except Exception:
            horizon_s = 0.0
        if horizon_s <= 0.0:
            try:
                horizon_s = float(self._derived_update_interval()) * float(
                    self.options.get(const.CONF_HORIZON, 1) or 1
                )
            except Exception:
                horizon_s = 0.0
        plan_id = f"{int(now)}-{status}"
        x0 = None
        controller = getattr(self.control_engine, "_controller", None)
        if controller is not None:
            try:
                x0 = json_ready(controller._ekf.x_hat)
            except Exception:
                x0 = None
        self.control_log.append(
            {
                "schema": SCHEMA,
                "kind": "plan",
                "plan_id": plan_id,
                "t": now,
                "t_start": now,
                "t_end": now + horizon_s,
                "epoch": epoch,
                "status": status,
                "cost": _finite(payload.get("fun")),
                "cost_zero": _finite(payload.get("cost_zero")),
                "nit": payload.get("nit"),
                "U": json_ready(payload.get("u_star")),
                "T_ref": json_ready(payload.get("t_ref")),
                "x0": x0,
                "message": None if payload.get("message") is None else str(payload.get("message"))[:500],
                "app_version": __version__,
            }
        )


def content_type_for(spec: ExportQuery) -> str:
    if spec.fmt == "csv":
        return "text/csv; charset=utf-8"
    return "application/x-ndjson; charset=utf-8"
