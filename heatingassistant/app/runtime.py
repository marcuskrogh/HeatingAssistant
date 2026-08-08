"""Application runtime for the MQTT-based Heating Assistant architecture."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
import re
import time
from pathlib import Path
from typing import Any

from heatingassistant.fusion.averaging import average_numeric_tags
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.engine import const
import logging

from heatingassistant.mqtt.bridge import InMemoryMqttBus, MqttBus, Unsubscribe

_logger = logging.getLogger(__name__)
from heatingassistant.mqtt.topics import (
    DEFAULT_QOS,
    MqttTagPayload,
    bindings as bindings_topic,
    entities as entities_topic,
    parse_tag_topic,
    status as status_topic,
    tag_in,
    tag_out,
)
from heatingassistant.persistence import load_config, load_state, save_config, save_state

# Keep ~48h of 15s samples in memory for Ingress plots (SWD-269).
_HISTORY_MAX_SAMPLES = 12_000
_HISTORY_MIN_INTERVAL_S = 5.0


@dataclass(frozen=True)
class Binding:
    """A bridge binding between a HA entity and an App tag."""

    tag: str
    entity_id: str
    direction: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Binding":
        tag = data.get("tag")
        entity_id = data.get("entity_id")
        direction = data.get("direction")
        if not isinstance(tag, str) or not tag:
            raise ValueError("binding tag must be a non-empty string")
        if not isinstance(entity_id, str) or not entity_id:
            raise ValueError("binding entity_id must be a non-empty string")
        if direction not in {"in", "out"}:
            raise ValueError("binding direction must be 'in' or 'out'")
        return cls(tag=tag, entity_id=entity_id, direction=direction)

    def to_dict(self) -> dict[str, str]:
        return {
            "tag": self.tag,
            "entity_id": self.entity_id,
            "direction": self.direction,
        }


class HeatingRuntime:
    """Owns App state and MQTT-facing contract data."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        bus: MqttBus | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.options = dict(options) if options is not None else load_config(self.data_dir)
        self.state = load_state(self.data_dir)
        self.instance_id = str(self.options.get("instance_id") or "default")
        self.mqtt_broker = self.options.get("mqtt_broker")
        self.bus = bus or InMemoryMqttBus()
        # Derive MQTT tags/bindings from configured HA entity IDs (Ingress UI
        # stores entity_ids; the thin HA bridge consumes the bindings map).
        self._apply_entity_wiring()
        self.bindings = self._load_bindings()
        save_config(self.data_dir, self.options)
        self.tag_values: dict[str, Any] = dict(self.state.get("tag_values") or {})
        self.tag_statuses: dict[str, str] = dict(self.state.get("tag_statuses") or {})
        self.room_temperatures: dict[str, float | None] = dict(
            self.state.get("room_temperatures") or {}
        )
        self.actuator_outputs: dict[str, float] = dict(self.state.get("actuator_outputs") or {})
        self.control_engine = ControlEngine(self.options)
        # HA entity catalog published by the thin bridge for Ingress pickers
        # (SWD-271). Not persisted — refreshed over MQTT when the bridge starts.
        self._ha_entity_catalog: list[dict[str, str]] = []
        self._subscriptions: list[Unsubscribe] = []
        self._started = False
        self._history: dict[str, deque[dict[str, Any]]] = {}
        self._history_last_ts: float = 0.0
        self._last_control_ts: float | None = self._coerce_number(self.state.get("last_control_ts"))
        self._last_control_duration_s: float = float(
            self._coerce_number(self.state.get("last_control_duration_s")) or 0.0
        )
        self._energy_total_wh: dict[str, float] = {
            str(key): float(value)
            for key, value in dict(self.state.get("energy_total_wh") or {}).items()
            if self._coerce_number(value) is not None
        }
        # Do not restore the energy clock from disk — integrating across App
        # downtime would spike Daily Energy on the first control cycle (SWD-269).
        self._energy_last_ts: float | None = None
        self._recompute_room_temperatures()
        self._record_history_samples(force=True)

    async def start(self) -> None:
        """Subscribe to inbound tags and publish retained App metadata.

        MQTT publish failures must not prevent Ingress HTTP from coming up —
        the broker may still be starting. Retained metadata is republished when
        the Paho bus reports a successful connect.
        """

        if self._started:
            return
        self._subscriptions.append(
            self.bus.subscribe(
                f"heatingassistant/{self.instance_id}/tag/+/in",
                self._handle_tag_message,
            )
        )
        self._subscriptions.append(
            self.bus.subscribe(
                entities_topic(self.instance_id),
                self._handle_entities_catalog_message,
            )
        )
        # Replay retained catalog for in-memory bus / late subscribers.
        replay = getattr(self.bus, "replay_retained", None)
        if callable(replay):
            await replay(entities_topic(self.instance_id), self._handle_entities_catalog_message)
        add_connect = getattr(self.bus, "add_connect_handler", None)
        if callable(add_connect):
            add_connect(self._on_mqtt_connected)
        self._started = True
        await self._publish_startup_metadata()

    async def _on_mqtt_connected(self) -> None:
        """Republish retained metadata after (re)connect."""

        await self._publish_startup_metadata()

    async def _publish_startup_metadata(self) -> None:
        """Best-effort bindings / control / status publish for Ingress readiness."""

        try:
            await self.publish_bindings()
        except Exception:
            _logger.exception("Failed to publish MQTT bindings; will retry on connect")
        try:
            await self.run_control_cycle()
        except Exception:
            _logger.exception("Control cycle failed during runtime start")
        try:
            await self.publish_status()
        except Exception:
            _logger.exception("Failed to publish MQTT status; will retry on connect")

    async def stop(self) -> None:
        """Unsubscribe from MQTT topics."""

        for unsubscribe in self._subscriptions:
            unsubscribe()
        self._subscriptions.clear()
        self._started = False
        close = getattr(self.bus, "close", None)
        if callable(close):
            close()

    async def publish_bindings(self) -> None:
        """Publish the retained bridge binding map."""

        await self.bus.publish(
            bindings_topic(self.instance_id),
            json.dumps({"bindings": self.binding_dicts()}, sort_keys=True),
            qos=DEFAULT_QOS,
            retain=True,
        )

    async def publish_status(self) -> None:
        """Publish retained runtime status."""

        await self.bus.publish(
            status_topic(self.instance_id),
            json.dumps(self.status(), sort_keys=True),
            qos=DEFAULT_QOS,
            retain=True,
        )

    async def publish_actuator_outputs(self) -> None:
        """Publish the latest App -> HA actuator tag values."""

        for tag, value in sorted(self.actuator_outputs.items()):
            await self.bus.publish(
                tag_out(self.instance_id, tag),
                MqttTagPayload(value=value, status="GOOD").encode(),
                qos=DEFAULT_QOS,
                retain=True,
            )

    async def _best_effort_mqtt(self, coro: Any, what: str) -> None:
        """Run an MQTT side-effect without failing the HTTP/config path."""

        try:
            await coro
        except Exception:
            _logger.exception("Best-effort MQTT %s failed; will retry on connect", what)

    async def _handle_tag_message(
        self,
        topic: str,
        payload: str | bytes,
        qos: int,
        retain: bool,
    ) -> None:
        parsed = parse_tag_topic(topic)
        if parsed is None or parsed.instance_id != self.instance_id or parsed.direction != "in":
            return
        tag_payload = MqttTagPayload.decode(payload)
        self.update_tag(parsed.tag, tag_payload)
        await self.run_control_cycle()

    async def _handle_entities_catalog_message(
        self,
        topic: str,
        payload: str | bytes,
        qos: int,
        retain: bool,
    ) -> None:
        """Accept a retained HA entity catalog from the thin bridge (SWD-271)."""

        if topic != entities_topic(self.instance_id):
            return
        try:
            if isinstance(payload, (bytes, bytearray)):
                payload = payload.decode("utf-8")
            data = json.loads(payload)
            raw = data.get("entities", data) if isinstance(data, dict) else data
            if not isinstance(raw, list):
                raise ValueError("entities catalog must be a list")
        except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            _logger.warning("Ignoring invalid HA entity catalog payload")
            return

        catalog: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            entity_id = item.get("entity_id")
            if not isinstance(entity_id, str) or "." not in entity_id:
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name:
                name = entity_id
            entry: dict[str, str] = {
                "entity_id": entity_id,
                "name": name,
                "state": str(item.get("state", "unknown")),
            }
            unit = item.get("unit")
            if isinstance(unit, str) and unit:
                entry["unit"] = unit
            catalog.append(entry)
        self._ha_entity_catalog = catalog
        _logger.debug("Loaded HA entity catalog (%d entities)", len(catalog))

    def update_tag(self, tag: str, payload: MqttTagPayload) -> None:
        """Store a tag payload and recompute any affected room averages."""

        self.tag_values[tag] = payload.value
        self.tag_statuses[tag] = payload.status
        self._recompute_room_temperatures()
        self._record_history_samples()
        self._save_runtime_state()

    async def run_control_cycle(self) -> dict[str, float]:
        """Compute and publish actuator outputs for the current runtime state."""

        started = time.time()
        self._recompute_room_temperatures()
        outputs = self.control_engine.compute_actions(
            self.room_temperatures,
            self._outdoor_temperature(),
            self._setpoints(),
        )
        self.actuator_outputs = dict(outputs)
        now = time.time()
        self._accumulate_energy(now)
        self._last_control_ts = now
        self._last_control_duration_s = max(0.0, now - started)
        self._record_history_samples(force=True)
        self._save_runtime_state()
        await self._best_effort_mqtt(self.publish_actuator_outputs(), "actuator outputs")
        await self._best_effort_mqtt(self.publish_status(), "status")
        return self.actuator_outputs

    async def update_config(self, updates: Mapping[str, Any]) -> dict[str, Any]:
        """Persist config updates and rebuild runtime-derived state."""

        self.options = {**self.options, **dict(updates)}
        self.instance_id = str(self.options.get("instance_id") or "default")
        self.mqtt_broker = self.options.get("mqtt_broker")
        self._apply_entity_wiring()
        self.bindings = self._load_bindings()
        save_config(self.data_dir, self.options)
        self.control_engine.update_config(self.options)
        self._recompute_room_temperatures()
        self._record_history_samples(force=True)
        self._save_runtime_state()
        # Persist locally even when Mosquitto is down (SWD-269) — Ingress must
        # not 502 on Controller Tuning Apply / config writes.
        await self._best_effort_mqtt(self.publish_bindings(), "bindings")
        await self.run_control_cycle()
        return dict(self.options)

    async def update_schedule(
        self,
        room_name: str,
        *,
        periods: list[Mapping[str, Any]] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Persist one room schedule in App config for dashboard edits."""

        slug = self._room_slug(room_name)
        if not slug:
            raise ValueError("room_name must be a non-empty string")

        schedules = self.schedules()
        current = dict(schedules.get(slug) or {"enabled": True, "periods": []})
        if periods is not None:
            if not isinstance(periods, list):
                raise ValueError("periods must be a list")
            current["periods"] = [dict(period) for period in periods if isinstance(period, Mapping)]
        if enabled is not None:
            current["enabled"] = bool(enabled)

        schedules[slug] = current
        self.options["schedules"] = schedules
        save_config(self.data_dir, self.options)
        self.control_engine.update_config(self.options)
        self._save_runtime_state()
        await self.run_control_cycle()
        return self.schedules()

    async def apply_service(self, domain: str, service: str, data: Mapping[str, Any]) -> dict[str, Any]:
        """Apply dashboard service calls through the App configuration API."""

        payload = dict(data)
        if domain == "heating_assistant":
            if service == "update_room_schedule":
                return {
                    "room_schedules": await self.update_schedule(
                        str(payload.get("room_name") or ""),
                        periods=payload.get("periods") if isinstance(payload.get("periods"), list) else [],
                    )
                }
            if service == "set_schedule_enabled":
                return {
                    "room_schedules": await self.update_schedule(
                        str(payload.get("room_name") or ""),
                        enabled=bool(payload.get("enabled")),
                    )
                }
            if service == "update_ui_settings":
                return {"ui_settings": await self.update_ui_settings(payload)}
            if service == "update_rooms":
                rooms = payload.get("rooms")
                if not isinstance(rooms, list):
                    raise ValueError("rooms must be a list")
                return {"config": await self.update_config({"rooms": rooms})}
            if service == "update_heat_sources":
                sources = payload.get("heat_sources")
                if not isinstance(sources, list):
                    raise ValueError("heat_sources must be a list")
                return {"config": await self.update_config({"heat_sources": sources})}
            if service in {"update_system_config", "update_system_params", "update_controller_tuning"}:
                return {"config": await self.update_config(payload)}
            if service == "set_system_enabled":
                return {"config": await self.update_config({"system_enabled": bool(payload.get("enabled"))})}
            if service == "set_room_setpoint":
                return {"config": await self._update_room_value(payload, "setpoint")}
            if service == "set_room_comfort_offset":
                return {"config": await self._update_room_value(payload, "comfort_offset")}
            if service == "set_room_enabled":
                return {"config": await self._set_room_enabled(payload)}
            # Mutating sysid/experiment services are accepted as no-ops until the
            # App runtime owns those stores.
            return {"accepted": True, "domain": domain, "service": service}

        if domain == "climate" and service in {"set_temperature", "turn_on", "turn_off"}:
            return {"accepted": True, "domain": domain, "service": service}

        raise ValueError(f"unsupported service {domain}.{service}")

    async def update_ui_settings(self, updates: Mapping[str, Any]) -> dict[str, float]:
        """Persist dashboard display-window settings."""

        allowed = {const.CONF_PLOT_HISTORY_HOURS, const.CONF_PLOT_FORECAST_HOURS}
        next_updates: dict[str, float] = {}
        for key in allowed:
            if key in updates:
                next_updates[key] = float(updates[key])
        if next_updates:
            await self.update_config(next_updates)
        return self.ui_settings()

    async def update_bindings(self, bindings: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
        """Persist bridge bindings and publish the retained binding map."""

        self.options["bindings"] = [dict(item) for item in bindings]
        self.bindings = self._load_bindings()
        save_config(self.data_dir, self.options)
        self._save_runtime_state()
        await self._best_effort_mqtt(self.publish_bindings(), "bindings")
        await self._best_effort_mqtt(self.publish_status(), "status")
        return self.binding_dicts()
    def room_temperature(self, room_name: str) -> float | None:
        """Return the current fused temperature for a room."""

        return self.room_temperatures.get(room_name)

    def binding_dicts(self) -> list[dict[str, str]]:
        return [binding.to_dict() for binding in self.bindings]

    def config(self) -> dict[str, Any]:
        """Return the persisted App configuration."""

        return dict(self.options)

    def schedules(self) -> dict[str, dict[str, Any]]:
        """Return schedule payloads keyed by room slug, matching panel WS shape."""

        source = self.options.get("schedules", self.options.get("room_schedules", {}))
        schedules: dict[str, dict[str, Any]] = {}
        if isinstance(source, Mapping):
            for key, value in source.items():
                slug = self._room_slug(str(key))
                if slug:
                    schedules[slug] = self._normalise_schedule(value)

        for room in self._rooms():
            name = room.get("name")
            if not isinstance(name, str) or not name:
                continue
            slug = self._room_slug(name)
            room_schedule = room.get("schedule")
            if slug not in schedules and room_schedule is not None:
                schedules[slug] = self._normalise_schedule(room_schedule)
            schedules.setdefault(slug, {"enabled": self._room_enabled(room), "periods": []})
        return schedules

    def controller_config(self) -> dict[str, Any]:
        """Return the industrial panel's controller-configuration snapshot."""

        config = {
            "comfort_offset": float(self.options.get("comfort_offset", const.DEFAULT_COMFORT_OFFSET)),
            "tracking_weight": float(self.options.get("tracking_weight", 1.0)),
            "energy_weight": float(self.options.get("energy_weight", 1.0)),
            "energy_price_weight": float(
                self.options.get("energy_price_weight", const.DEFAULT_ENERGY_PRICE_WEIGHT)
            ),
            "smoothing_weight": float(self.options.get("smoothing_weight", 0.05)),
            "soft_constraint_weight": float(self.options.get("soft_constraint_weight", 10.0)),
            "soft_constraint_linear_weight": float(
                self.options.get("soft_constraint_linear_weight", 0.0)
            ),
            "terminal_weight": float(self.options.get("terminal_weight", 1.0)),
            "horizon": int(self.options.get("horizon", const.DEFAULT_HORIZON)),
            "update_interval": int(
                self.options.get("update_interval", const.DEFAULT_UPDATE_INTERVAL)
            ),
            "window_open_debounce": int(
                self.options.get("window_open_debounce", const.DEFAULT_WINDOW_OPEN_DEBOUNCE)
            ),
            "window_open_close_settle": int(
                self.options.get(
                    "window_open_close_settle", const.DEFAULT_WINDOW_OPEN_CLOSE_SETTLE
                )
            ),
            "window_open_q_inflation": float(
                self.options.get(
                    "window_open_q_inflation", const.DEFAULT_WINDOW_OPEN_Q_INFLATION
                )
            ),
            "room_schedules": self.schedules(),
            "room_comfort_offsets": self._room_comfort_offsets(),
            "room_enabled": self._room_enabled_map(),
            "room_active": self._room_enabled_map(),
            "system_enabled": bool(self.options.get("system_enabled", False)),
        }
        return config

    def ui_settings(self) -> dict[str, float]:
        """Return dashboard plotting window settings."""

        return {
            const.CONF_PLOT_HISTORY_HOURS: float(
                self.options.get(
                    const.CONF_PLOT_HISTORY_HOURS, const.DEFAULT_PLOT_HISTORY_HOURS
                )
            ),
            const.CONF_PLOT_FORECAST_HOURS: float(
                self.options.get(
                    const.CONF_PLOT_FORECAST_HOURS, const.DEFAULT_PLOT_FORECAST_HOURS
                )
            ),
        }

    def model_config(self) -> dict[str, Any]:
        """Return editable model/system configuration for the panel config pages."""

        system = {
            const.CONF_OUTDOOR_TEMP_ENTITY: self.options.get(
                const.CONF_OUTDOOR_TEMP_ENTITY, ""
            ),
            const.CONF_WEATHER_ENTITY: self.options.get(const.CONF_WEATHER_ENTITY, ""),
            const.CONF_SOLAR_RADIATION_ENTITY: self.options.get(
                const.CONF_SOLAR_RADIATION_ENTITY, ""
            ),
            const.CONF_PRICE_ENTITY: self.options.get(const.CONF_PRICE_ENTITY, ""),
            const.CONF_LATITUDE: self.options.get(const.CONF_LATITUDE, 0.0),
            const.CONF_LONGITUDE: self.options.get(const.CONF_LONGITUDE, 0.0),
        }
        return {
            "rooms": [dict(room) for room in self._rooms()],
            "heat_sources": [dict(source) for source in self._heat_sources()],
            "system": system,
            "ui_settings": self.ui_settings(),
            "system_params": {
                const.CONF_IDENTIFICATION_HISTORY_DAYS: int(
                    self.options.get(
                        const.CONF_IDENTIFICATION_HISTORY_DAYS,
                        const.DEFAULT_IDENTIFICATION_HISTORY_DAYS,
                    )
                )
            },
            "enums": {
                "floor_types": list(const.FLOOR_TYPE_DEFAULTS.keys()),
                "facade_colours": list(const.FACADE_COLOUR_TO_ABSORPTANCE.keys()),
                "solar_exposures": list(const.SOLAR_EXPOSURE_TO_APERTURE.keys()),
                "envelope_tightness": list(
                    const.ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION.keys()
                ),
                "envelope_tightness_map": dict(
                    const.ENVELOPE_TIGHTNESS_TO_INFILTRATION_FRACTION
                ),
                "source_types": list(const.ALL_SOURCE_TYPES),
                "hvac_modes": [
                    const.SOURCE_HVAC_MODE_HEAT,
                    const.SOURCE_HVAC_MODE_COOL,
                    const.SOURCE_HVAC_MODE_HEAT_COOL,
                ],
            },
        }

    def forecasts(self, plot_forecast_hours: float | None = None) -> dict[str, Any]:
        """Return an empty but structured forecast payload for chart callers."""

        return {
            "rooms": {},
            "price_forecast": [],
            "plot_forecast_hours": plot_forecast_hours,
        }

    def datasets(self, room_slug: str | None = None) -> list[dict[str, Any]]:
        """Return persisted dataset metadata when configured, otherwise an empty list."""

        source = self.options.get("datasets", [])
        if not isinstance(source, list):
            return []
        datasets = [dict(item) for item in source if isinstance(item, Mapping)]
        if room_slug:
            datasets = [item for item in datasets if item.get("room_slug") == room_slug]
        return datasets

    def dataset(self, dataset_id: str) -> dict[str, Any] | None:
        """Return one persisted dataset when present."""

        for item in self.datasets():
            if item.get("id") == dataset_id:
                return item
        return None

    def experiments(self) -> list[dict[str, Any]]:
        """Return persisted experiments when configured, otherwise an empty list."""

        source = self.options.get("experiments", [])
        if not isinstance(source, list):
            return []
        return [dict(item) for item in source if isinstance(item, Mapping)]

    def state_snapshot(self) -> dict[str, Any]:
        """Return persisted runtime state plus current derived fields."""

        return {
            **dict(self.state),
            "tag_values": dict(self.tag_values),
            "tag_statuses": dict(self.tag_statuses),
            "room_temperatures": dict(self.room_temperatures),
            "actuator_outputs": dict(self.actuator_outputs),
            "control": self._control_status(),
            "mqtt_connected": self._mqtt_connected(),
            "mqtt_broker": self.mqtt_broker,
            "mqtt_source": self.options.get("mqtt_source") or "options",
            "hass_states": self.hass_states(),
        }

    def history(
        self,
        *,
        entity_ids: Iterable[str] | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return in-memory entity history in HA ``history_during_period`` shape."""

        wanted = {eid for eid in (entity_ids or []) if isinstance(eid, str) and eid}
        start = float(start_ts) if start_ts is not None else None
        end = float(end_ts) if end_ts is not None else None
        result: dict[str, list[dict[str, Any]]] = {}
        for entity_id, samples in self._history.items():
            if wanted and entity_id not in wanted:
                continue
            filtered: list[dict[str, Any]] = []
            for sample in samples:
                lu = float(sample["lu"])
                if start is not None and lu < start:
                    continue
                if end is not None and lu > end:
                    continue
                filtered.append({"s": sample["s"], "lu": lu})
            if filtered:
                result[entity_id] = filtered
        return result

    def hass_states(self) -> dict[str, dict[str, Any]]:
        """Build minimal Home Assistant-like states for the custom panel."""

        now_ts = time.time()
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now_ts))
        states: dict[str, dict[str, Any]] = {}

        states["sensor.heating_assistant_controller_config"] = self._ha_state(
            "sensor.heating_assistant_controller_config",
            "ok",
            self.controller_config(),
            now,
        )
        total_power = sum(self.actuator_outputs.values()) if self.actuator_outputs else 0.0
        states["sensor.heating_assistant_system_summary"] = self._ha_state(
            "sensor.heating_assistant_system_summary",
            str(total_power),
            {
                "system_enabled": bool(self.options.get("system_enabled", False)),
                "control_mode": self.control_engine.mode,
                "fallback_reason": self.control_engine.fallback_reason,
                "comfort_index_pct": None,
                "total_heating_power": total_power,
                "mqtt_connected": self._mqtt_connected(),
                "has_heat_pump": any(
                    str(source.get("type", "")).lower() == "heat_pump"
                    for source in self._heat_sources()
                ),
            },
            now,
        )

        update_interval = float(
            self.options.get("update_interval", const.DEFAULT_UPDATE_INTERVAL)
        )
        mean_error = self._mean_tracking_error()
        states["sensor.heating_assistant_mpc_performance"] = self._ha_state(
            "sensor.heating_assistant_mpc_performance",
            self._last_control_duration_s,
            {
                "last_run_ts": self._last_control_ts,
                "dt_s": update_interval,
                "mean_tracking_error": mean_error,
                "unit_of_measurement": "s",
            },
            now,
        )

        outdoor = self._outdoor_temperature()
        states["sensor.heating_assistant_outdoor_temperature_measured"] = self._ha_state(
            "sensor.heating_assistant_outdoor_temperature_measured",
            "unknown" if outdoor is None else outdoor,
            {"unit_of_measurement": "°C"},
            now,
        )

        setpoints = self._setpoints()
        schedules = self.schedules()
        for room in self._rooms():
            name = room.get("name")
            if not isinstance(name, str) or not name:
                continue
            slug = self._room_slug(name)
            temperature = self.room_temperatures.get(name)
            setpoint = setpoints.get(name, self._coerce_number(room.get("setpoint")) or 21.0)
            offset = self._coerce_number(room.get("comfort_offset"))
            if offset is None:
                offset = float(self.options.get("comfort_offset", const.DEFAULT_COMFORT_OFFSET))
            enabled = self._room_enabled(room)
            schedule = schedules.get(slug, {"enabled": enabled, "periods": []})
            power = self._room_power(name)
            energy_wh = float(self._energy_total_wh.get(slug, 0.0))

            states[f"sensor.heating_assistant_{slug}_temperature_measured"] = self._ha_state(
                f"sensor.heating_assistant_{slug}_temperature_measured",
                "unknown" if temperature is None else temperature,
                {"room": name, "unit_of_measurement": "°C"},
                now,
            )
            states[f"sensor.heating_assistant_{slug}_temperature_filtered"] = self._ha_state(
                f"sensor.heating_assistant_{slug}_temperature_filtered",
                "unknown" if temperature is None else temperature,
                {
                    "room": name,
                    "unit_of_measurement": "°C",
                    "comfort_deviation": None
                    if temperature is None
                    else abs(float(temperature) - float(setpoint)),
                    "time_in_range_pct_24h": None,
                },
                now,
            )
            states[f"sensor.heating_assistant_{slug}_setpoint"] = self._ha_state(
                f"sensor.heating_assistant_{slug}_setpoint",
                setpoint,
                {"room": name, "unit_of_measurement": "°C"},
                now,
            )
            states[f"sensor.heating_assistant_{slug}_constraint_lower"] = self._ha_state(
                f"sensor.heating_assistant_{slug}_constraint_lower",
                setpoint - offset,
                {"room": name, "unit_of_measurement": "°C"},
                now,
            )
            states[f"sensor.heating_assistant_{slug}_constraint_upper"] = self._ha_state(
                f"sensor.heating_assistant_{slug}_constraint_upper",
                setpoint + offset,
                {"room": name, "unit_of_measurement": "°C"},
                now,
            )
            states[f"sensor.heating_assistant_{slug}_heating_power_measured"] = self._ha_state(
                f"sensor.heating_assistant_{slug}_heating_power_measured",
                power,
                {"room": name, "unit_of_measurement": "W"},
                now,
            )
            states[f"sensor.heating_assistant_{slug}_solar_gain_measured"] = self._ha_state(
                f"sensor.heating_assistant_{slug}_solar_gain_measured",
                0.0,
                {"room": name, "unit_of_measurement": "W"},
                now,
            )
            states[f"sensor.heating_assistant_{slug}_heat_loss"] = self._ha_state(
                f"sensor.heating_assistant_{slug}_heat_loss",
                0.0,
                {"room": name, "unit_of_measurement": "W"},
                now,
            )
            states[f"sensor.heating_assistant_{slug}_energy_total"] = self._ha_state(
                f"sensor.heating_assistant_{slug}_energy_total",
                energy_wh / 1000.0,
                {
                    "room": name,
                    "unit_of_measurement": "kWh",
                    "state_class": "total_increasing",
                },
                now,
            )
            states[f"climate.heating_assistant_{slug}"] = self._ha_state(
                f"climate.heating_assistant_{slug}",
                "heat" if enabled else "off",
                {
                    "friendly_name": name,
                    "current_temperature": temperature,
                    "temperature": setpoint,
                    "hvac_modes": ["off", "heat"],
                    "supported_features": 1,
                    "schedule": schedule,
                },
                now,
            )

        # Expose configured HA entities (from bindings) so the Ingress entity
        # picker can at least re-select already-wired IDs after a refresh.
        for binding in self.bindings:
            if binding.entity_id in states:
                continue
            value = self.tag_values.get(binding.tag)
            if value is None and binding.direction == "out":
                value = self.actuator_outputs.get(binding.tag)
            states[binding.entity_id] = self._ha_state(
                binding.entity_id,
                "unknown" if value is None else value,
                {
                    "friendly_name": binding.entity_id,
                    "heating_assistant_tag": binding.tag,
                    "heating_assistant_direction": binding.direction,
                },
                now,
            )

        # Merge the thin-bridge HA entity catalog so Ingress pickers can search
        # all configured HA entities (SWD-271). Do not overwrite App synthetics
        # or already-bound live values.
        for item in self._ha_entity_catalog:
            entity_id = item["entity_id"]
            if entity_id in states:
                # Prefer live friendly names / units from the catalog when the
                # binding stub only has the raw entity_id as its name.
                attrs = states[entity_id].setdefault("attributes", {})
                if attrs.get("friendly_name") in (None, "", entity_id):
                    attrs["friendly_name"] = item["name"]
                if item.get("unit") and not attrs.get("unit_of_measurement"):
                    attrs["unit_of_measurement"] = item["unit"]
                continue
            attrs = {"friendly_name": item["name"]}
            if item.get("unit"):
                attrs["unit_of_measurement"] = item["unit"]
            states[entity_id] = self._ha_state(
                entity_id,
                item.get("state", "unknown"),
                attrs,
                now,
            )
        return states

    def status(self) -> dict[str, Any]:
        """Expose a compact health/status snapshot for HTTP and MQTT."""

        return {
            "instance_id": self.instance_id,
            "mqtt_broker": self.mqtt_broker,
            "mqtt_port": self.options.get("mqtt_port", 1883),
            "mqtt_username": self.options.get("mqtt_username") or "",
            "mqtt_source": self.options.get("mqtt_source") or "options",
            "mqtt_connected": self._mqtt_connected(),
            "bindings_count": len(self.bindings),
            "control": self._control_status(),
            "actuator_outputs": dict(self.actuator_outputs),
            "rooms": [
                {
                    "name": room.get("name"),
                    "temp_tags": self._room_temp_tags(room),
                    "temperature": self.room_temperatures.get(str(room.get("name"))),
                }
                for room in self._rooms()
                if room.get("name") is not None
            ],
            "started": self._started,
            "status": "ok",
            "ts": time.time(),
        }

    def _mqtt_connected(self) -> bool:
        mqtt_connected = getattr(self.bus, "connected", None)
        if callable(mqtt_connected):
            mqtt_connected = mqtt_connected()
        elif mqtt_connected is None:
            # In-memory bus is always "connected" for local/dev.
            mqtt_connected = True
        return bool(mqtt_connected)

    def _control_status(self) -> dict[str, Any]:
        return {
            "mode": self.control_engine.mode,
            "fallback_reason": self.control_engine.fallback_reason,
            "last_run_ts": self._last_control_ts,
            "last_duration_s": self._last_control_duration_s,
        }
    def _load_bindings(self) -> list[Binding]:
        source = self.options.get("bindings", self.state.get("bindings", []))
        if isinstance(source, Mapping):
            source = source.get("bindings", [])
        if not isinstance(source, Iterable) or isinstance(source, (str, bytes)):
            raise ValueError("bindings must be a list or {'bindings': list}")
        return [Binding.from_mapping(item) for item in source if isinstance(item, Mapping)]

    def _apply_entity_wiring(self) -> None:
        """Derive MQTT tags + bindings from HA entity IDs in the model config.

        The Ingress Configuration UI stores Home Assistant entity IDs
        (``temp_sensors``, ``heater_entity``, ``outdoor_temp_entity``, …). The
        thin HA integration bridges entities via the retained MQTT bindings
        map, and the App averages room temperatures via ``temp_tags``. This
        keeps those three views in sync whenever config is loaded or saved.
        """

        previous: dict[tuple[str, str], str] = {}
        raw_bindings = self.options.get("bindings", [])
        if isinstance(raw_bindings, Mapping):
            raw_bindings = raw_bindings.get("bindings", [])
        if isinstance(raw_bindings, list):
            for item in raw_bindings:
                if not isinstance(item, Mapping):
                    continue
                entity_id = item.get("entity_id")
                direction = item.get("direction")
                tag = item.get("tag")
                if (
                    isinstance(entity_id, str)
                    and entity_id
                    and isinstance(tag, str)
                    and tag
                    and direction in {"in", "out"}
                ):
                    previous[(entity_id, direction)] = tag

        bindings: list[dict[str, str]] = []
        used_tags: set[str] = set()

        def bind(entity_id: Any, direction: str, preferred_tag: str) -> str | None:
            if not isinstance(entity_id, str):
                return None
            eid = entity_id.strip()
            if not eid or "." not in eid:
                return None
            preferred = preferred_tag.strip() or self._entity_tag(eid)
            tag = previous.get((eid, direction), preferred)
            if not tag:
                tag = preferred
            base = tag
            suffix = 2
            while tag in used_tags:
                tag = f"{base}_{suffix}"
                suffix += 1
            used_tags.add(tag)
            bindings.append({"tag": tag, "entity_id": eid, "direction": direction})
            return tag

        rooms_out: list[dict[str, Any]] = []
        for room in self._rooms():
            room_cfg = dict(room)
            slug = self._room_slug(str(room_cfg.get("name") or "")) or "room"
            sensors = self._room_temp_sensor_entities(room_cfg)
            if sensors:
                temp_tags: list[str] = []
                for index, entity_id in enumerate(sensors, start=1):
                    tag = bind(entity_id, "in", f"{slug}_temp_{index}")
                    if tag:
                        temp_tags.append(tag)
                if temp_tags:
                    room_cfg["temp_tags"] = temp_tags
                else:
                    room_cfg.pop("temp_tags", None)
                    room_cfg.pop("temp_tag", None)
            else:
                # Tag-only configs (no HA entity IDs) keep their temp_tags and
                # any matching inbound bindings already present.
                for tag in self._explicit_temp_tags(room_cfg):
                    entity_id = next(
                        (
                            eid
                            for (eid, direction), existing in previous.items()
                            if direction == "in" and existing == tag
                        ),
                        None,
                    )
                    if entity_id:
                        bind(entity_id, "in", tag)
                    else:
                        # Preserve the tag name even without an entity binding.
                        if tag not in used_tags:
                            used_tags.add(tag)

            window_entities = self._string_list(room_cfg.get("window_sensors"))
            if window_entities:
                window_tags: list[str] = []
                for index, entity_id in enumerate(window_entities, start=1):
                    tag = bind(entity_id, "in", f"{slug}_window_{index}")
                    if tag:
                        window_tags.append(tag)
                if window_tags:
                    room_cfg["window_tags"] = window_tags
                else:
                    room_cfg.pop("window_tags", None)
            rooms_out.append(room_cfg)
        self.options["rooms"] = rooms_out

        sources_out: list[dict[str, Any]] = []
        for source in self._heat_sources():
            source_cfg = dict(source)
            name = source_cfg.get("name")
            source_slug = (
                self._room_slug(str(name)) if isinstance(name, str) and name else "heater"
            )
            preferred = source_cfg.get("output_tag")
            if not isinstance(preferred, str) or not preferred.strip():
                preferred = f"{source_slug}_heat"
            heater_entity = source_cfg.get("heater_entity")
            if isinstance(heater_entity, str) and heater_entity.strip():
                tag = bind(heater_entity, "out", preferred)
                if tag:
                    source_cfg["output_tag"] = tag
            else:
                source_cfg.setdefault("output_tag", preferred)
                tag_name = source_cfg.get("output_tag")
                if isinstance(tag_name, str) and tag_name and tag_name not in used_tags:
                    used_tags.add(tag_name)
            sources_out.append(source_cfg)
        self.options["heat_sources"] = sources_out

        outdoor_entity = self.options.get(const.CONF_OUTDOOR_TEMP_ENTITY)
        if isinstance(outdoor_entity, str) and outdoor_entity.strip():
            outdoor_tag = bind(outdoor_entity, "in", "outdoor_temp")
            if outdoor_tag:
                self.options["outdoor_temp_tag"] = outdoor_tag
        else:
            self.options.pop("outdoor_temp_tag", None)

        weather_entity = self.options.get(const.CONF_WEATHER_ENTITY)
        if isinstance(weather_entity, str) and weather_entity.strip():
            weather_tag = bind(weather_entity, "in", "weather_forecast")
            if weather_tag:
                self.options["weather_tag"] = weather_tag
        else:
            self.options.pop("weather_tag", None)

        solar_entity = self.options.get(const.CONF_SOLAR_RADIATION_ENTITY)
        if isinstance(solar_entity, str) and solar_entity.strip():
            solar_tag = bind(solar_entity, "in", "solar_radiation")
            if solar_tag:
                self.options["solar_radiation_tag"] = solar_tag
        else:
            # Cleared from Environment UI (SWD-271) — drop derived tag/binding.
            self.options.pop("solar_radiation_tag", None)
            self.options[const.CONF_SOLAR_RADIATION_ENTITY] = ""

        price_entity = self.options.get(const.CONF_PRICE_ENTITY)
        if isinstance(price_entity, str) and price_entity.strip():
            price_tag = bind(price_entity, "in", "energy_price")
            if price_tag:
                self.options["price_tag"] = price_tag
        else:
            self.options.pop("price_tag", None)

        # Known system tags that are fully regenerated from entity fields —
        # never keep a stale leftover if the entity was cleared.
        regenerated_system_tags = {
            "outdoor_temp",
            "weather_forecast",
            "solar_radiation",
            "energy_price",
        }

        # Keep any leftover explicit bindings (e.g. set via /api/bindings) that
        # were not regenerated from entity fields.
        for (entity_id, direction), tag in previous.items():
            if any(item["entity_id"] == entity_id and item["direction"] == direction for item in bindings):
                continue
            if tag in regenerated_system_tags and tag not in used_tags:
                continue
            if tag in used_tags and not any(item["tag"] == tag for item in bindings):
                # Tag already reserved by a regenerated binding under another entity.
                continue
            if tag not in used_tags:
                used_tags.add(tag)
            bindings.append({"tag": tag, "entity_id": entity_id, "direction": direction})

        self.options["bindings"] = bindings

    def _rooms(self) -> list[Mapping[str, Any]]:
        rooms = self.options.get("rooms", [])
        if not isinstance(rooms, list):
            return []
        return [room for room in rooms if isinstance(room, Mapping)]

    def _heat_sources(self) -> list[Mapping[str, Any]]:
        sources = self.options.get("heat_sources", [])
        if not isinstance(sources, list):
            return []
        return [source for source in sources if isinstance(source, Mapping)]

    def _room_temp_tags(self, room: Mapping[str, Any]) -> list[str]:
        tags = self._explicit_temp_tags(room)
        if tags:
            return tags
        # Resolve from configured HA entity IDs via the bindings map.
        entity_to_tag = {
            binding.entity_id: binding.tag
            for binding in self.bindings
            if binding.direction == "in"
        }
        return [
            entity_to_tag[entity_id]
            for entity_id in self._room_temp_sensor_entities(room)
            if entity_id in entity_to_tag
        ]

    @staticmethod
    def _explicit_temp_tags(room: Mapping[str, Any]) -> list[str]:
        temp_tags = room.get("temp_tags")
        if isinstance(temp_tags, list):
            tags = [tag for tag in temp_tags if isinstance(tag, str) and tag]
            if tags:
                return tags
        temp_tag = room.get("temp_tag")
        if isinstance(temp_tag, str) and temp_tag:
            return [temp_tag]
        return []

    @staticmethod
    def _room_temp_sensor_entities(room: Mapping[str, Any]) -> list[str]:
        sensors = HeatingRuntime._string_list(room.get("temp_sensors"))
        single = room.get("temp_sensor")
        if isinstance(single, str) and single.strip() and single.strip() not in sensors:
            sensors.insert(0, single.strip())
        return sensors

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        if not isinstance(value, list):
            return []
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    @staticmethod
    def _entity_tag(entity_id: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", entity_id.strip().lower()).strip("_")

    def _outdoor_temperature(self) -> float | None:
        tag = self.options.get("outdoor_temp_tag") or self.options.get("outdoor_tag")
        if not isinstance(tag, str) or not tag:
            outdoor_entity = self.options.get(const.CONF_OUTDOOR_TEMP_ENTITY)
            if isinstance(outdoor_entity, str) and outdoor_entity:
                for binding in self.bindings:
                    if binding.direction == "in" and binding.entity_id == outdoor_entity:
                        tag = binding.tag
                        break
        if not isinstance(tag, str) or not tag:
            return None
        return self._coerce_number(self.tag_values.get(tag))

    def _setpoints(self) -> dict[str, float]:
        setpoints: dict[str, float] = {}
        for room in self._rooms():
            name = room.get("name")
            if not isinstance(name, str) or not name:
                continue
            setpoint_tag = room.get("setpoint_tag")
            value = (
                self._coerce_number(self.tag_values.get(setpoint_tag))
                if isinstance(setpoint_tag, str)
                else None
            )
            if value is None:
                value = self._coerce_number(room.get("setpoint"))
            if value is not None:
                setpoints[name] = value
        return setpoints

    async def _update_room_value(
        self, payload: Mapping[str, Any], field: str
    ) -> dict[str, Any]:
        room_name = str(payload.get("room_name") or "")
        if field not in payload:
            raise ValueError(f"{field} is required")
        value = float(payload[field])
        rooms = [dict(room) for room in self._rooms()]
        updated = False
        for room in rooms:
            if self._room_slug(str(room.get("name") or "")) == self._room_slug(room_name):
                room[field] = value
                updated = True
                break
        if not updated:
            raise ValueError(f"unknown room {room_name!r}")
        return await self.update_config({"rooms": rooms})

    async def _set_room_enabled(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        room_name = str(payload.get("room_name") or "")
        enabled = bool(payload.get("enabled"))
        rooms = [dict(room) for room in self._rooms()]
        updated = False
        for room in rooms:
            if self._room_slug(str(room.get("name") or "")) == self._room_slug(room_name):
                room["enabled"] = enabled
                updated = True
                break
        if not updated:
            raise ValueError(f"unknown room {room_name!r}")
        return await self.update_config({"rooms": rooms})

    def _room_power(self, room_name: str) -> float:
        total = 0.0
        for source in self._heat_sources():
            if source.get("room") != room_name:
                continue
            tag = source.get("output_tag")
            if isinstance(tag, str):
                value = self._coerce_number(self.actuator_outputs.get(tag))
                if value is not None:
                    total += value
        return total

    def _room_comfort_offsets(self) -> dict[str, float]:
        offsets: dict[str, float] = {}
        default = float(self.options.get("comfort_offset", const.DEFAULT_COMFORT_OFFSET))
        for room in self._rooms():
            name = room.get("name")
            if not isinstance(name, str) or not name:
                continue
            value = self._coerce_number(room.get("comfort_offset"))
            offsets[self._room_slug(name)] = value if value is not None else default
        return offsets

    def _room_enabled_map(self) -> dict[str, bool]:
        enabled: dict[str, bool] = {}
        for room in self._rooms():
            name = room.get("name")
            if isinstance(name, str) and name:
                enabled[self._room_slug(name)] = self._room_enabled(room)
        return enabled

    @staticmethod
    def _room_enabled(room: Mapping[str, Any]) -> bool:
        return bool(room.get("enabled", True))

    @staticmethod
    def _room_slug(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
        return re.sub(r"_+", "_", slug)

    @staticmethod
    def _normalise_schedule(value: Any) -> dict[str, Any]:
        if isinstance(value, list):
            return {"enabled": True, "periods": [dict(p) for p in value if isinstance(p, Mapping)]}
        if isinstance(value, Mapping):
            periods = value.get("periods", [])
            if not isinstance(periods, list):
                periods = []
            return {
                "enabled": bool(value.get("enabled", True)),
                "periods": [dict(p) for p in periods if isinstance(p, Mapping)],
            }
        return {"enabled": True, "periods": []}

    @staticmethod
    def _ha_state(
        entity_id: str,
        state: Any,
        attributes: Mapping[str, Any],
        now: str,
    ) -> dict[str, Any]:
        return {
            "entity_id": entity_id,
            "state": str(state),
            "attributes": dict(attributes),
            "last_changed": now,
            "last_updated": now,
            "context": {"id": "app-runtime", "parent_id": None, "user_id": None},
        }

    def _recompute_room_temperatures(self) -> None:
        for room in self._rooms():
            name = room.get("name")
            if not isinstance(name, str) or not name:
                continue
            temp_tags = self._room_temp_tags(room)
            values = {tag: self._coerce_number(self.tag_values.get(tag)) for tag in temp_tags}
            statuses = {tag: self.tag_statuses.get(tag) for tag in temp_tags}
            self.room_temperatures[name] = average_numeric_tags(values, statuses)

    @staticmethod
    def _coerce_number(value: Any) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def _save_runtime_state(self) -> None:
        self.state["bindings"] = self.binding_dicts()
        self.state["tag_values"] = dict(self.tag_values)
        self.state["tag_statuses"] = dict(self.tag_statuses)
        self.state["room_temperatures"] = dict(self.room_temperatures)
        self.state["actuator_outputs"] = dict(self.actuator_outputs)
        self.state["last_control_ts"] = self._last_control_ts
        self.state["last_control_duration_s"] = self._last_control_duration_s
        self.state["energy_total_wh"] = dict(self._energy_total_wh)
        self.state["energy_last_ts"] = self._energy_last_ts
        self.state["config"] = dict(self.options)
        save_state(self.data_dir, self.state)

    def _record_history_samples(self, *, force: bool = False) -> None:
        """Append current synthetic entity values into the in-memory history ring."""

        now = time.time()
        if not force and (now - self._history_last_ts) < _HISTORY_MIN_INTERVAL_S:
            return
        self._history_last_ts = now
        states = self.hass_states()
        for entity_id, state in states.items():
            if not entity_id.startswith("sensor.heating_assistant_"):
                continue
            if entity_id.endswith("_model_fit_quality"):
                continue
            raw = state.get("state")
            if raw in {None, "unknown", "unavailable"}:
                continue
            try:
                float(raw)
            except (TypeError, ValueError):
                continue
            bucket = self._history.setdefault(
                entity_id, deque(maxlen=_HISTORY_MAX_SAMPLES)
            )
            bucket.append({"s": str(raw), "lu": now})

    def _accumulate_energy(self, now: float) -> None:
        """Integrate room heating power into cumulative energy totals."""

        previous = self._energy_last_ts
        self._energy_last_ts = now
        if previous is None or now <= previous:
            return
        # Cap gaps so a stalled loop / clock jump cannot invent kWh.
        max_dt_s = max(
            2.0 * float(self.options.get("update_interval", const.DEFAULT_UPDATE_INTERVAL)),
            60.0,
        )
        dt_s = min(now - previous, max_dt_s)
        dt_h = dt_s / 3600.0
        for room in self._rooms():
            name = room.get("name")
            if not isinstance(name, str) or not name:
                continue
            slug = self._room_slug(name)
            power_w = self._room_power(name)
            self._energy_total_wh[slug] = float(self._energy_total_wh.get(slug, 0.0)) + (
                power_w * dt_h
            )

    def _mean_tracking_error(self) -> float | None:
        errors: list[float] = []
        setpoints = self._setpoints()
        for room in self._rooms():
            name = room.get("name")
            if not isinstance(name, str) or not name:
                continue
            temperature = self.room_temperatures.get(name)
            if temperature is None:
                continue
            setpoint = setpoints.get(name)
            if setpoint is None:
                continue
            errors.append(abs(float(temperature) - float(setpoint)))
        if not errors:
            return None
        return sum(errors) / len(errors)

async def publish_tag_in(
    runtime: HeatingRuntime,
    tag: str,
    value: Any,
    *,
    status: str = "GOOD",
    reason: str | None = None,
    ts: float | None = None,
) -> None:
    """Test helper that publishes a tag/in payload through the runtime bus."""

    await runtime.bus.publish(
        tag_in(runtime.instance_id, tag),
        MqttTagPayload(value=value, status=status, reason=reason, ts=ts).encode(),
        qos=DEFAULT_QOS,
    )
