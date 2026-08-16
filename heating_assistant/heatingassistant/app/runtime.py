"""Application runtime for the MQTT-based Heating Assistant architecture."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from heatingassistant.app.forecast_payload import build_app_forecast_payload
from heatingassistant.app.disturbance_forecasts import build_mpc_disturbance_inputs
from heatingassistant.app.system_health import evaluate_system_health
from heatingassistant.app.id_history_health import evaluate_id_history_health
from heatingassistant.app.actuation import (
    climate_write_payload,
    coerce_climate_attrs,
    number_write_payload,
    switch_write_payload,
)
from heatingassistant.app import sysid_services
from heatingassistant.app.plot_history import PlotHistoryStore
from heatingassistant.app import core_restart
from heatingassistant.app import window_override as window_ov
from heatingassistant.fusion.averaging import average_numeric_tags
from heatingassistant.engine.control_loop import ControlEngine
from heatingassistant.engine import const
from heatingassistant.engine.datasets import DatasetStore
from heatingassistant.engine.electricity_price import (
    align_prices_to_horizon,
    collect_price_series,
)
from heatingassistant.engine.history.store import IdentificationHistoryStore
from heatingassistant.engine.naming import room_slug
from heatingassistant.engine.parameter_lifecycle import (
    PARAMETER_HISTORY_KEY,
    estimated_params_snapshot,
    restore_estimated_parameters,
)
from heatingassistant.engine.heat_sources import HeatSource
from heatingassistant.engine.schedule import EffectiveControlParams
from heatingassistant.engine.schedule_control import (
    ControlTrajectory,
    compute_control_trajectory,
    resolve_room_effective_params,
)

from heatingassistant.mqtt.bridge import InMemoryMqttBus, MqttBus, Unsubscribe
from heatingassistant.mqtt.supervisor import (
    apply_supervisor_mqtt_discovery,
    get_last_discovery_error,
)
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

_logger = logging.getLogger(__name__)

# Keep ~48h of update_interval samples in memory for Ingress plots (SWD-269/277).
# Durable copy lives under ``<data_dir>/plot_history/`` (SWD-281).
_HISTORY_MAX_SAMPLES = 12_000
_HISTORY_MIN_INTERVAL_S = 5.0

# Synthetic sensor the room Price plot reads via /api/history (SWD-284).
_ELECTRICITY_PRICE_ENTITY = "sensor.heating_assistant_electricity_price"

# Retry Supervisor MQTT discovery while disconnected (SWD-273).
_MQTT_DISCOVERY_RETRY_INITIAL_S = 2.0
_MQTT_DISCOVERY_RETRY_MAX_S = 60.0


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
        self.dataset_store = DatasetStore(self.data_dir, entry_id=self.instance_id)
        self.dataset_store.load()
        self.mqtt_broker = self.options.get("mqtt_broker")
        self.bus = bus or InMemoryMqttBus()
        # Derive MQTT tags/bindings from configured HA entity IDs (Ingress UI
        # stores entity_ids; the thin HA bridge consumes the bindings map).
        self._apply_entity_wiring()
        self.bindings = self._load_bindings()
        save_config(self.data_dir, self.options)
        self.tag_values: dict[str, Any] = dict(self.state.get("tag_values") or {})
        self.tag_statuses: dict[str, str] = dict(self.state.get("tag_statuses") or {})
        self.tag_attributes: dict[str, dict[str, Any]] = {
            str(key): dict(value)
            for key, value in dict(self.state.get("tag_attributes") or {}).items()
            if isinstance(value, Mapping)
        }
        self.room_temperatures: dict[str, float | None] = dict(
            self.state.get("room_temperatures") or {}
        )
        self.actuator_outputs: dict[str, float] = dict(self.state.get("actuator_outputs") or {})
        self.control_engine = ControlEngine(self.options)
        self._restore_estimated_parameters()
        self.sysid_results: dict[str, Any] = {}
        self.open_loop_results: dict[str, Any] = {}
        self._last_identified_heater_scales: dict[str, float] = {}
        # HA entity catalog published by the thin bridge for Ingress pickers
        # (SWD-271). Not persisted — refreshed over MQTT when the bridge starts.
        self._ha_entity_catalog: list[dict[str, str]] = []
        self._subscriptions: list[Unsubscribe] = []
        self._started = False
        self._started_monotonic: float | None = None
        self._history: dict[str, deque[dict[str, Any]]] = {}
        self._history_last_ts: float = 0.0
        self._history_buffer: deque[dict[str, Any]] = deque(
            maxlen=const.HISTORY_BUFFER_SIZE
        )
        self._id_history_last_ts: float = 0.0
        self._id_history_disk_last_ts: float = 0.0
        self._id_history_append_failure_streak: int = 0
        self._id_history_last_append_ok: bool | None = None
        self._last_control_ts: float | None = self._coerce_number(self.state.get("last_control_ts"))
        self._last_control_duration_s: float = float(
            self._coerce_number(self.state.get("last_control_duration_s")) or 0.0
        )
        self._last_control_trajectory: ControlTrajectory | None = None
        self._last_effective_params: dict[str, EffectiveControlParams] = {}
        self._energy_total_wh: dict[str, float] = {
            str(key): float(value)
            for key, value in dict(self.state.get("energy_total_wh") or {}).items()
            if self._coerce_number(value) is not None
        }
        # Do not restore the energy clock from disk — integrating across App
        # downtime would spike Daily Energy on the first control cycle (SWD-269).
        self._energy_last_ts: float | None = None
        # Retry Supervisor discovery when we started without a live MQTT session
        # and credentials were blank / supervisor-sourced (SWD-273).
        self._mqtt_discovery_retry = self._should_retry_mqtt_discovery(self.options)
        self._mqtt_discovery_stop = threading.Event()
        self._mqtt_discovery_thread: threading.Thread | None = None
        # Wall-clock history + control when tag events are quiet (SWD-276).
        self._ticker_stop = threading.Event()
        self._ticker_thread: threading.Thread | None = None
        self._control_lock = threading.Lock()
        self._history_lock = threading.Lock()
        # Open-window / door override (SWD-298): state machine + debounce timers.
        self._window_tags: dict[str, list[str]] = {}
        self._window_tag_to_room: dict[str, str] = {}
        self._window_state: dict[str, str] = {}
        self._window_state_since: dict[str, datetime] = {}
        self._window_pending: dict[str, asyncio.Task[Any]] = {}
        self._rebuild_window_maps()
        # Durable plot + identification history on the App data volume (SWD-281).
        plot_hours = float(
            self.options.get(const.CONF_PLOT_HISTORY_HOURS, const.DEFAULT_PLOT_HISTORY_HOURS)
        )
        self._plot_history_store = PlotHistoryStore(
            self.data_dir,
            retention_days=PlotHistoryStore.retention_days_for_plot_hours(plot_hours),
        )
        id_days = int(
            self.options.get(
                const.CONF_PARAMETER_ESTIMATION_HISTORY_DAYS,
                const.DEFAULT_PARAMETER_ESTIMATION_HISTORY_DAYS,
            )
        )
        self.id_history_store = IdentificationHistoryStore(
            entry_id=self.instance_id,
            retention_days=id_days,
            data_dir=self.data_dir,
        )
        self._restore_durable_history(plot_hours=plot_hours)
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
        self._subscriptions.append(
            self.bus.subscribe(
                core_restart.command_topic(self.instance_id),
                self._handle_core_restart_command,
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
        self._started_monotonic = time.monotonic()
        self._start_mqtt_discovery_retry()
        self._start_background_ticker()
        await self._publish_startup_metadata()

    async def _on_mqtt_connected(self) -> None:
        """Republish retained metadata after (re)connect."""

        await self._publish_startup_metadata()

    @staticmethod
    def _blank_mqtt_value(value: Any) -> bool:
        return value is None or (isinstance(value, str) and not value.strip())

    @classmethod
    def _should_retry_mqtt_discovery(cls, options: Mapping[str, Any]) -> bool:
        """Retry only while the credential pair is still blank."""

        return cls._blank_mqtt_value(options.get("mqtt_username")) and cls._blank_mqtt_value(
            options.get("mqtt_password")
        )

    def _start_mqtt_discovery_retry(self) -> None:
        """Background thread: rediscover Mosquitto creds while disconnected."""

        if not self._mqtt_discovery_retry:
            return
        if self._mqtt_discovery_thread is not None and self._mqtt_discovery_thread.is_alive():
            return
        reconfigure = getattr(self.bus, "reconfigure", None)
        if not callable(reconfigure):
            return
        self._mqtt_discovery_stop.clear()
        self._mqtt_discovery_thread = threading.Thread(
            target=self._mqtt_discovery_retry_loop,
            name="heatingassistant-mqtt-discovery",
            daemon=True,
        )
        self._mqtt_discovery_thread.start()

    def _mqtt_discovery_retry_loop(self) -> None:
        delay = _MQTT_DISCOVERY_RETRY_INITIAL_S
        while not self._mqtt_discovery_stop.is_set():
            if self._mqtt_connected():
                # Spec: retry only while disconnected — wait longer when healthy.
                if self._mqtt_discovery_stop.wait(timeout=_MQTT_DISCOVERY_RETRY_MAX_S):
                    return
                continue
            if not self._should_retry_mqtt_discovery(self.options):
                return
            if self._mqtt_discovery_stop.wait(timeout=delay):
                return
            if self._mqtt_connected() or not self._should_retry_mqtt_discovery(self.options):
                delay = _MQTT_DISCOVERY_RETRY_INITIAL_S
                continue
            if not self._try_apply_supervisor_mqtt_discovery():
                delay = min(delay * 2.0, _MQTT_DISCOVERY_RETRY_MAX_S)
                continue
            delay = _MQTT_DISCOVERY_RETRY_INITIAL_S

    def _try_apply_supervisor_mqtt_discovery(self) -> bool:
        """Fetch Supervisor MQTT details and reconfigure the bus when useful."""

        prior = {
            "mqtt_broker": self.options.get("mqtt_broker"),
            "mqtt_port": self.options.get("mqtt_port"),
            "mqtt_username": self.options.get("mqtt_username"),
            "mqtt_password": self.options.get("mqtt_password"),
            "mqtt_ssl": self.options.get("mqtt_ssl", False),
        }
        both_blank = self._blank_mqtt_value(prior.get("mqtt_username")) and self._blank_mqtt_value(
            prior.get("mqtt_password")
        )
        if not both_blank:
            # Non-blank pair already present — never blank-and-rediscover over it.
            return False

        # Force a fresh discovery pass so merge takes the full Supervisor
        # endpoint (host/port/ssl/user/pass).
        probe = dict(self.options)
        probe["mqtt_username"] = ""
        probe["mqtt_password"] = ""
        merged = apply_supervisor_mqtt_discovery(probe, fallback=prior)
        if merged.get("mqtt_source") != "supervisor":
            return False

        changed = any(
            merged.get(key) != prior.get(key)
            for key in (
                "mqtt_broker",
                "mqtt_port",
                "mqtt_username",
                "mqtt_password",
                "mqtt_ssl",
            )
        )
        has_creds = not self._blank_mqtt_value(merged.get("mqtt_username"))
        if not has_creds:
            return False
        if not changed and not self._blank_mqtt_value(prior.get("mqtt_username")):
            return False

        self.options["mqtt_broker"] = merged.get("mqtt_broker")
        self.options["mqtt_port"] = merged.get("mqtt_port", 1883)
        self.options["mqtt_username"] = merged.get("mqtt_username") or ""
        self.options["mqtt_password"] = merged.get("mqtt_password") or ""
        self.options["mqtt_ssl"] = bool(merged.get("mqtt_ssl", False))
        self.options["mqtt_source"] = "supervisor"
        self.mqtt_broker = self.options.get("mqtt_broker")
        save_config(self.data_dir, self.options)

        reconfigure = getattr(self.bus, "reconfigure", None)
        if not callable(reconfigure):
            return False
        try:
            reconfigure(
                host=str(self.options.get("mqtt_broker") or ""),
                port=int(self.options.get("mqtt_port") or 1883),
                username=str(self.options.get("mqtt_username") or "") or None,
                password=str(self.options.get("mqtt_password") or ""),
                ssl=bool(self.options.get("mqtt_ssl", False)),
            )
        except Exception:
            _logger.exception("Failed to reconfigure MQTT bus after Supervisor discovery")
            return False
        _logger.info(
            "Applied Supervisor MQTT discovery retry for %s:%s (user=%s)",
            self.options.get("mqtt_broker"),
            self.options.get("mqtt_port"),
            self.options.get("mqtt_username") or "(none)",
        )
        return True

    def _history_tick_interval_s(self) -> float:
        """Seconds between history samples — matches control update_interval."""

        update = float(self.options.get("update_interval", const.DEFAULT_UPDATE_INTERVAL))
        return max(_HISTORY_MIN_INTERVAL_S, update)

    def _control_tick_interval_s(self) -> float:
        """Seconds between background control cycles."""

        update = float(self.options.get("update_interval", const.DEFAULT_UPDATE_INTERVAL))
        return max(30.0, update)

    def _start_background_ticker(self) -> None:
        """Start wall-clock history + control when MQTT tag events are quiet."""

        if self._ticker_thread is not None and self._ticker_thread.is_alive():
            return
        self._ticker_stop.clear()
        self._ticker_thread = threading.Thread(
            target=self._background_ticker_loop,
            name="heatingassistant-wall-clock-ticker",
            daemon=True,
        )
        self._ticker_thread.start()

    def _background_ticker_loop(self) -> None:
        """Record history and run control without relying on Ingress or tag spam."""

        history_every = self._history_tick_interval_s()
        control_every = self._control_tick_interval_s()
        next_history = time.time() + history_every
        # First control soon after start so energy/actuators move without waiting
        # a full update_interval when tags are silent.
        next_control = time.time() + min(control_every, history_every)
        while not self._ticker_stop.is_set():
            now = time.time()
            if now >= next_history:
                try:
                    self._record_history_samples()
                except Exception:
                    _logger.exception("Wall-clock history sample failed")
                try:
                    # Same cadence as plot history so estimation memory does not
                    # stall when control is quiet (SWD-318 Option B).
                    self._record_identification_sample(now)
                except Exception:
                    _logger.exception("Wall-clock identification sample failed")
                history_every = self._history_tick_interval_s()
                next_history = now + history_every
            if now >= next_control:
                last = self._last_control_ts
                if last is not None and (now - last) < (control_every * 0.5):
                    # MQTT tag path already ran control recently — skip.
                    next_control = last + control_every
                else:
                    try:
                        asyncio.run(self.run_control_cycle())
                    except Exception:
                        _logger.exception("Wall-clock control cycle failed")
                    control_every = self._control_tick_interval_s()
                    next_control = time.time() + control_every
            sleep_for = max(0.2, min(next_history, next_control) - time.time())
            if self._ticker_stop.wait(timeout=sleep_for):
                return

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
        try:
            await self.publish_core_restart_required()
        except Exception:
            _logger.exception(
                "Failed to publish restart-required MQTT update; will retry on connect"
            )

    async def stop(self) -> None:
        """Unsubscribe from MQTT topics and stop background workers."""

        self._ticker_stop.set()
        if self._ticker_thread is not None:
            self._ticker_thread.join(timeout=2)
            self._ticker_thread = None
        self._mqtt_discovery_stop.set()
        if self._mqtt_discovery_thread is not None:
            self._mqtt_discovery_thread.join(timeout=2)
            self._mqtt_discovery_thread = None
        for unsubscribe in self._subscriptions:
            unsubscribe()
        self._subscriptions.clear()
        self._cancel_all_window_timers()
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

    async def publish_core_restart_required(self) -> None:
        """Publish or clear the Settings Restart required MQTT Update entity."""

        stamp = core_restart.read_stamp(self.data_dir)
        discovery = core_restart.discovery_topic()
        if stamp is None:
            await self.bus.publish(discovery, "", qos=DEFAULT_QOS, retain=True)
            await self.bus.publish(
                core_restart.state_topic(self.instance_id),
                "",
                qos=DEFAULT_QOS,
                retain=True,
            )
            return
        await self.bus.publish(
            discovery,
            json.dumps(core_restart.discovery_payload(self.instance_id), sort_keys=True),
            qos=DEFAULT_QOS,
            retain=True,
        )
        await self.bus.publish(
            core_restart.state_topic(self.instance_id),
            json.dumps(
                core_restart.state_payload(
                    from_version=stamp["from_version"],
                    to_version=stamp["to_version"],
                ),
                sort_keys=True,
            ),
            qos=DEFAULT_QOS,
            retain=True,
        )

    async def _handle_core_restart_command(
        self,
        topic: str,
        payload: str | bytes,
        qos: int,
        retain: bool,
    ) -> None:
        """Restart Home Assistant Core when Settings Install is pressed."""

        del qos, retain
        if topic != core_restart.command_topic(self.instance_id):
            return
        text = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
        if text.strip() != core_restart.PAYLOAD_INSTALL:
            return
        if core_restart.read_stamp(self.data_dir) is None:
            return
        _logger.info("Settings requested Home Assistant Core restart after thin-bridge sync")
        if not core_restart.request_core_restart():
            _logger.warning("Core restart request failed; leave Restart required on Settings")
            return
        # Core restart does not restart this App. Clear the Settings card now.
        core_restart.clear_stamp(self.data_dir)
        await self.publish_core_restart_required()

    async def publish_actuator_outputs(self) -> None:
        """Publish the latest App -> HA actuator tag values.

        Internal ``actuator_outputs`` stay as MPC fractions in ``[-1, 1]``.
        Domain-specific HA write payloads (climate mode/setpoint, number %,
        switch bool) are derived at publish time (SWD-280).
        """

        for tag, value in sorted(self.actuator_outputs.items()):
            command = self._actuator_write_value(tag, float(value))
            await self.bus.publish(
                tag_out(self.instance_id, tag),
                MqttTagPayload(value=command, status="GOOD").encode(),
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
        previous_value = self.tag_values.get(parsed.tag)
        self.update_tag(parsed.tag, tag_payload)
        # Climate heater feedback tags only refresh anchoring inputs. Re-running
        # MPC here would thrash: write setpoint → HA state change → tag/in →
        # compute → write again (SWD-280 review-fix).
        if self._is_heater_state_feedback_tag(parsed.tag):
            return
        # Window/door contacts drive the override state machine + debounce timers
        # without a full MPC cycle (SWD-298).
        if self._is_window_tag(parsed.tag):
            await self._on_window_tag_changed(parsed.tag, previous_value, tag_payload.value)
            return
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
        if payload.status == "BAD":
            self.tag_attributes.pop(tag, None)
        elif payload.attributes is not None:
            # Explicit empty dict clears prior forecast attrs (SWD-278).
            self.tag_attributes[tag] = dict(payload.attributes)
        else:
            # GOOD scalar-only update: drop stale day-ahead / weather attrs.
            self.tag_attributes.pop(tag, None)
        self._recompute_room_temperatures()
        self._record_history_samples()
        # Plot writers also feed ID history (SWD-318); interval gate dedupes
        # against control-cycle samples.
        self._record_identification_sample()
        self._save_runtime_state()

    async def run_control_cycle(self) -> dict[str, float]:
        """Compute and publish actuator outputs for the current runtime state."""

        if not self._control_lock.acquire(blocking=False):
            # Another cycle (MQTT tag or ticker) is already running.
            return dict(self.actuator_outputs)
        try:
            started = time.time()
            self._recompute_room_temperatures()
            outdoor = self._outdoor_temperature()
            disturbances = self._mpc_disturbance_inputs(outdoor)
            schedule_ctx = self._schedule_control_context()
            self._apply_window_q_inflation()
            outputs = self.control_engine.compute_actions(
                self.room_temperatures,
                outdoor,
                schedule_ctx["setpoints"],
                comfort_offsets=schedule_ctx["comfort_offsets"],
                control_trajectory=schedule_ctx["trajectory"],
                disabled_sources=schedule_ctx["disabled_sources"],
                now=schedule_ctx["now_utc"],
                **disturbances,
            )
            self._last_control_trajectory = schedule_ctx["trajectory"]
            self._last_effective_params = schedule_ctx["effective"]
            self.actuator_outputs = dict(outputs)
            self._clamp_window_override_actuators()
            now = time.time()
            self._accumulate_energy(now)
            self._last_control_ts = now
            self._last_control_duration_s = max(0.0, now - started)
            # Gate on update_interval — do not force a sample every control/tag.
            pending_plot = self._record_history_samples(persist=False)
            if pending_plot:
                await asyncio.to_thread(
                    self._plot_history_store.append_samples, pending_plot
                )
                await asyncio.to_thread(self._plot_history_store.purge_old)
            id_record = self._take_identification_sample(now)
            if id_record is not None:
                try:
                    await self.id_history_store.async_append(id_record)
                except Exception:
                    _logger.exception(
                        "Failed to persist identification history record"
                    )
                    self._note_identification_append_failure()
                else:
                    self._commit_identification_sample(id_record)
                    try:
                        await self.id_history_store.async_purge_old()
                    except Exception:
                        _logger.exception(
                            "Failed to purge old identification history"
                        )
            self._save_runtime_state()
            await self._best_effort_mqtt(self.publish_actuator_outputs(), "actuator outputs")
            await self._best_effort_mqtt(self.publish_status(), "status")
            return dict(self.actuator_outputs)
        finally:
            self._control_lock.release()

    async def update_config(self, updates: Mapping[str, Any]) -> dict[str, Any]:
        """Persist config updates and rebuild runtime-derived state."""

        self.options = {**self.options, **dict(updates)}
        self.instance_id = str(self.options.get("instance_id") or "default")
        if getattr(getattr(self, "dataset_store", None), "path", None) is not None:
            expected_name = f"{self.instance_id}.json"
            if self.dataset_store.path.name != expected_name:
                self.dataset_store = DatasetStore(self.data_dir, entry_id=self.instance_id)
                self.dataset_store.load()
        self.mqtt_broker = self.options.get("mqtt_broker")
        self._apply_entity_wiring()
        self.bindings = self._load_bindings()
        save_config(self.data_dir, self.options)
        self._rebuild_window_maps()
        self.control_engine.update_config(self.options)
        self._restore_estimated_parameters()
        self._sync_history_retention()
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
        self._restore_estimated_parameters()
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
            sysid_handler = {
                "estimate_parameters_ml": sysid_services.handle_estimate_parameters_ml,
                "get_pe_coverage": sysid_services.handle_get_pe_coverage,
                "get_pe_inputs": sysid_services.handle_get_pe_inputs,
                "run_sysid_simulation": sysid_services.handle_run_sysid_simulation,
                "run_open_loop_simulation": sysid_services.handle_run_open_loop_simulation,
                "store_identified_parameters": sysid_services.handle_store_identified_parameters,
                "update_estimation_params": sysid_services.handle_update_estimation_params,
                "delete_parameter_history": sysid_services.handle_delete_parameter_history,
                "create_dataset": sysid_services.handle_create_dataset,
                "delete_dataset": sysid_services.handle_delete_dataset,
            }.get(service)
            if sysid_handler is not None:
                return await sysid_handler(self, payload)
            # Mutating sysid/experiment services are accepted as no-ops until the
            # App runtime owns those stores.
            return {"accepted": True, "domain": domain, "service": service}

        if domain == "climate" and service in {"set_temperature", "turn_on", "turn_off"}:
            return await self._apply_climate_service(service, payload)

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

    def _parameter_history_for_ui(self) -> list[dict[str, Any]]:
        """Return parameter history with room keys slugified for the panel."""

        estimated_snapshot = self.options.get(const.CONF_ESTIMATED_PARAMS)
        parameter_history = self.options.get(PARAMETER_HISTORY_KEY)
        if not isinstance(parameter_history, list):
            parameter_history = []
            if isinstance(estimated_snapshot, Mapping):
                snapshot_history = estimated_snapshot.get("history")
                if isinstance(snapshot_history, list):
                    parameter_history = [
                        dict(item) for item in snapshot_history if isinstance(item, Mapping)
                    ]
        ui_history: list[dict[str, Any]] = []
        for entry in parameter_history:
            if not isinstance(entry, Mapping):
                continue
            item = dict(entry)
            rooms = item.get("rooms")
            if isinstance(rooms, Mapping):
                item["rooms"] = {
                    self._room_slug(str(room_name)): (
                        dict(room_data) if isinstance(room_data, Mapping) else room_data
                    )
                    for room_name, room_data in rooms.items()
                }
            ui_history.append(item)
        return ui_history

    def controller_config(self) -> dict[str, Any]:
        """Return the industrial panel's controller-configuration snapshot."""

        parameter_history = self._parameter_history_for_ui()
        current_heater_scales: dict[str, dict[str, Any]] = {}
        for source in self.control_engine.heat_sources:
            name = getattr(source, "name", None)
            if name is None:
                continue
            room_name = str(getattr(source, "room", ""))
            current_heater_scales[str(name)] = {
                "room_slug": self._room_slug(room_name),
                "power_scale": float(getattr(source, "power_scale", 1.0)),
            }
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
            "parameter_history": parameter_history,
            "sigma_w": float(self.options.get(const.CONF_SIGMA_W, const.DEFAULT_SIGMA_W)),
            "sigma_v": float(self.options.get(const.CONF_SIGMA_V, const.DEFAULT_SIGMA_V)),
            "parameter_estimation_horizon_hours": float(
                self.options.get(
                    const.CONF_PARAMETER_ESTIMATION_HORIZON_HOURS,
                    const.DEFAULT_PARAMETER_ESTIMATION_HORIZON_HOURS,
                )
            ),
            "current_heater_scales": current_heater_scales,
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
                const.CONF_PARAMETER_ESTIMATION_HISTORY_DAYS: int(
                    self.options.get(
                        const.CONF_PARAMETER_ESTIMATION_HISTORY_DAYS,
                        const.DEFAULT_PARAMETER_ESTIMATION_HISTORY_DAYS,
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
        """Return MPC trajectories for Ingress plots (SWD-277/278)."""

        snapshot = self.control_engine.forecast_snapshot()
        price_tag = self.options.get("price_tag") or "energy_price"
        if not isinstance(price_tag, str) or not price_tag:
            price_tag = "energy_price"
        energy_price = self._coerce_number(self.tag_values.get(price_tag))
        if energy_price is None and price_tag != "energy_price":
            energy_price = self._coerce_number(self.tag_values.get("energy_price"))
        outdoor_temp = self._outdoor_temperature()
        dt = float(snapshot.get("dt") or self.options.get("update_interval", const.DEFAULT_UPDATE_INTERVAL))
        n_pred = len(list(snapshot.get("predictions") or []))
        if plot_forecast_hours is not None and dt > 0:
            target_steps = max(0, int(round(float(plot_forecast_hours) * 3600.0 / dt)))
        else:
            target_steps = n_pred
        traj_steps = max(n_pred, target_steps, 1) + 1  # cover now + futures
        trajectory = self._build_control_trajectory(n_steps=traj_steps, dt_seconds=dt)
        return build_app_forecast_payload(
            rooms=self._rooms(),
            room_temperatures=self.room_temperatures,
            outdoor_temp=outdoor_temp,
            energy_price=energy_price,
            snapshot=snapshot,
            plot_forecast_hours=plot_forecast_hours,
            room_power_meta=self.control_engine.room_power_meta(outdoor_temp),
            control_trajectory=trajectory,
        )

    def preview_tuning_forecast(
        self,
        tuning_params: Mapping[str, Any] | None = None,
        plot_forecast_hours: float | None = None,
    ) -> dict[str, Any]:
        """One-off MPC solve with proposed tuning params (not applied).

        Returns the same payload shape as :meth:`forecasts`, or
        ``{"error": "..."}`` when a required input is missing.
        """

        overrides = {
            key: value
            for key, value in dict(tuning_params or {}).items()
            if key
            not in {
                "type",
                "id",
                "plot_forecast_hours",
                "plot_forecast_steps",
            }
            and value is not None
        }
        outdoor_temp = self._outdoor_temperature()
        if outdoor_temp is None:
            return {"error": "outdoor_temperature_unavailable"}

        preview_horizon = int(
            overrides.get(
                const.CONF_HORIZON,
                self.options.get("horizon", const.DEFAULT_HORIZON),
            )
        )
        preview_dt = float(
            overrides.get(
                const.CONF_UPDATE_INTERVAL,
                self.options.get("update_interval", const.DEFAULT_UPDATE_INTERVAL),
            )
        )
        # Serialize against live control cycles — preview mutates shared model
        # comfort/temps briefly and must not race actuator publishes.
        if not self._control_lock.acquire(blocking=True, timeout=30.0):
            return {"error": "controller_busy"}
        try:
            disturbances = self._mpc_disturbance_inputs(
                outdoor_temp,
                horizon=preview_horizon,
                dt_s=preview_dt,
            )
            snapshot = self.control_engine.preview_tuning_forecast(
                overrides,
                self.room_temperatures,
                outdoor_temp,
                self._setpoints(),
                **disturbances,
            )
            if snapshot.get("error"):
                return snapshot

            rooms = [dict(room) for room in self._rooms()]
            comfort_override = overrides.get(const.CONF_COMFORT_OFFSET)
            if comfort_override is not None:
                preview_comfort = float(comfort_override)
                for room in rooms:
                    room["comfort_offset"] = preview_comfort

            price_tag = self.options.get("price_tag") or "energy_price"
            if not isinstance(price_tag, str) or not price_tag:
                price_tag = "energy_price"
            energy_price = self._coerce_number(self.tag_values.get(price_tag))
            if energy_price is None and price_tag != "energy_price":
                energy_price = self._coerce_number(self.tag_values.get("energy_price"))

            # When previewing a global comfort_offset, hold that draft band flat
            # (SWD-285) — skip schedule trajectory so rooms[] overrides win.
            trajectory = None
            if comfort_override is None:
                trajectory = self._build_control_trajectory(
                    n_steps=max(preview_horizon, 1) + 1,
                    dt_seconds=preview_dt,
                )

            return build_app_forecast_payload(
                rooms=rooms,
                room_temperatures=self.room_temperatures,
                outdoor_temp=outdoor_temp,
                energy_price=energy_price,
                snapshot=snapshot,
                plot_forecast_hours=plot_forecast_hours,
                room_power_meta=self.control_engine.room_power_meta(outdoor_temp),
                control_trajectory=trajectory,
            )
        finally:
            self._control_lock.release()

    def _mpc_disturbance_inputs(
        self,
        outdoor_temp: float | None,
        *,
        horizon: int | None = None,
        dt_s: float | None = None,
    ) -> dict[str, Any]:
        """Build outdoor / solar / price series for the next control compute."""

        use_horizon = int(
            horizon
            if horizon is not None
            else self.options.get("horizon", const.DEFAULT_HORIZON)
        )
        use_dt = float(
            dt_s
            if dt_s is not None
            else self.options.get("update_interval", const.DEFAULT_UPDATE_INTERVAL)
        )
        weather_tag = self.options.get("weather_tag") or "weather_forecast"
        if not isinstance(weather_tag, str) or not weather_tag:
            weather_tag = "weather_forecast"
        price_tag = self.options.get("price_tag") or "energy_price"
        if not isinstance(price_tag, str) or not price_tag:
            price_tag = "energy_price"
        solar_tag = self.options.get("solar_radiation_tag") or "solar_radiation"
        if not isinstance(solar_tag, str) or not solar_tag:
            solar_tag = "solar_radiation"
        net = float(
            self.options.get(const.CONF_PRICE_NET_TARIFF, const.DEFAULT_PRICE_NET_TARIFF) or 0.0
        )
        surcharge = float(
            self.options.get(
                const.CONF_PRICE_SPOT_SURCHARGE, const.DEFAULT_PRICE_SPOT_SURCHARGE
            )
            or 0.0
        )
        return build_mpc_disturbance_inputs(
            outdoor_temp=outdoor_temp,
            weather_attrs=self.tag_attributes.get(weather_tag),
            price_value=self._coerce_number(self.tag_values.get(price_tag)),
            price_attrs=self.tag_attributes.get(price_tag),
            solar_value=self._coerce_number(self.tag_values.get(solar_tag)),
            solar_attrs=self.tag_attributes.get(solar_tag),
            horizon=use_horizon,
            dt_s=use_dt,
            price_adder=net + surcharge,
        )

    def datasets(self, room_slug: str | None = None) -> list[dict[str, Any]]:
        """Return persisted system-identification dataset metadata."""

        metas = self.dataset_store.list_meta(room_slug)
        return sysid_services.annotate_datasets_with_coverage(self, metas)

    def dataset(self, dataset_id: str) -> dict[str, Any] | None:
        """Return one persisted system-identification dataset when present."""

        return self.dataset_store.get(dataset_id)

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
            "mqtt_ssl": bool(self.options.get("mqtt_ssl", False)),
            "mqtt_username": self.options.get("mqtt_username") or "",
            "mqtt_last_error": self._mqtt_last_error(),
            "mqtt_discovery_error": get_last_discovery_error(),
            "supervisor_token_present": bool(os.environ.get("SUPERVISOR_TOKEN")),
            "hass_states": self.hass_states(),
        }

    @property
    def history_buffer(self) -> deque[dict[str, Any]]:
        """Identification observation ring restored from / persisted to JSONL."""

        return self._history_buffer

    def history(
        self,
        *,
        entity_ids: Iterable[str] | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return entity history in HA ``history_during_period`` shape.

        Samples are kept in memory for Ingress plots and durably mirrored under
        ``<data_dir>/plot_history/`` so App updates do not clear room charts.
        """

        wanted = {eid for eid in (entity_ids or []) if isinstance(eid, str) and eid}
        start = float(start_ts) if start_ts is not None else None
        end = float(end_ts) if end_ts is not None else None
        result: dict[str, list[dict[str, Any]]] = {}
        with self._history_lock:
            items = list(self._history.items())
            snapshots = {
                entity_id: [dict(sample) for sample in samples]
                for entity_id, samples in items
            }
        for entity_id, samples in snapshots.items():
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

        # SWD-284: backfill Price history from day-ahead attrs when the ring is
        # empty/sparse so the room plot shows historical Price immediately.
        if not wanted or _ELECTRICITY_PRICE_ENTITY in wanted:
            synthesized = self._synthesize_price_history(start_ts=start, end_ts=end)
            if synthesized:
                ring = result.get(_ELECTRICITY_PRICE_ENTITY, [])
                first_syn = float(synthesized[0]["lu"])
                last_syn = float(synthesized[-1]["lu"])
                before = [
                    sample
                    for sample in ring
                    if float(sample["lu"]) < first_syn - 1e-6
                ]
                after = [
                    sample
                    for sample in ring
                    if float(sample["lu"]) > last_syn + 1e-6
                ]
                result[_ELECTRICITY_PRICE_ENTITY] = before + synthesized + after
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
        total_power = 0.0
        for room in self._rooms():
            name = room.get("name")
            if isinstance(name, str) and name:
                total_power += self._room_power(name)
        health = self.system_health()
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
                "system_quality": health["quality"],
                "issue_summary": health.get("issue_summary"),
                "uptime_s": health.get("uptime_s"),
                "entity_catalog_count": health.get("entity_catalog_count"),
                "bindings_count": health.get("bindings_count"),
                "id_history": self.id_history_health(now_ts),
                "has_heat_pump": any(
                    str(source.get("type", "")).lower() == "heat_pump"
                    for source in self._heat_sources()
                    if isinstance(source, Mapping)
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

        # SWD-284: publish electricity price so plot history + live extend work.
        price_value = self._electricity_price_value()
        price_attrs = self._electricity_price_state_attrs()
        states[_ELECTRICITY_PRICE_ENTITY] = self._ha_state(
            _ELECTRICITY_PRICE_ENTITY,
            "unknown" if price_value is None else round(float(price_value), 5),
            price_attrs,
            now,
        )

        setpoints = self._setpoints()
        schedules = self.schedules()
        now_local = self._schedule_now_local()
        effective_by_room = self._resolve_effective_params(now_local=now_local)
        solar_gains = self._applied_solar_gains()
        for room in self._rooms():
            name = room.get("name")
            if not isinstance(name, str) or not name:
                continue
            slug = self._room_slug(name)
            temperature = self.room_temperatures.get(name)
            base_setpoint = setpoints.get(name, self._coerce_number(room.get("setpoint")) or 21.0)
            effective = effective_by_room.get(name)
            if effective is None:
                offset = self._coerce_number(room.get("comfort_offset"))
                if offset is None:
                    offset = float(self.options.get("comfort_offset", const.DEFAULT_COMFORT_OFFSET))
                setpoint = float(base_setpoint)
                schedule_heating = True
            else:
                setpoint = float(effective.setpoint)
                offset = float(effective.comfort_offset)
                schedule_heating = bool(effective.enabled)
            enabled = self._room_enabled(room) and schedule_heating
            schedule = schedules.get(slug) or {"enabled": True, "periods": []}
            power = self._room_power(name)
            energy_wh = float(self._energy_total_wh.get(slug, 0.0))
            try:
                solar_gain = float(solar_gains.get(name, 0.0) or 0.0)
            except (TypeError, ValueError):
                solar_gain = 0.0
            windows = room.get("windows") if isinstance(room.get("windows"), list) else []
            total_window_area = 0.0
            for window in windows:
                if not isinstance(window, Mapping):
                    continue
                area = self._coerce_number(window.get("area"))
                if area is not None:
                    total_window_area += float(area)

            states[f"sensor.heating_assistant_{slug}_temperature_measured"] = self._ha_state(
                f"sensor.heating_assistant_{slug}_temperature_measured",
                "unknown" if temperature is None else temperature,
                {"room": name, "unit_of_measurement": "°C"},
                now,
            )
            filtered_attrs: dict[str, Any] = {
                "room": name,
                "unit_of_measurement": "°C",
                "comfort_deviation": None
                if temperature is None
                else abs(float(temperature) - float(setpoint)),
                "time_in_range_pct_24h": None,
            }
            filtered_attrs.update(self._live_room_thermal_attrs(name))
            states[f"sensor.heating_assistant_{slug}_temperature_filtered"] = self._ha_state(
                f"sensor.heating_assistant_{slug}_temperature_filtered",
                "unknown" if temperature is None else temperature,
                filtered_attrs,
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
            sysid_attrs = sysid_services.sysid_sensor_attrs(self, name)
            states[f"sensor.heating_assistant_{slug}_sysid_simulation"] = self._ha_state(
                f"sensor.heating_assistant_{slug}_sysid_simulation",
                "unknown" if sysid_attrs.get("rmse") is None else sysid_attrs["rmse"],
                sysid_attrs,
                now,
            )
            open_loop_attrs = sysid_services.open_loop_sensor_attrs(self, name)
            states[f"sensor.heating_assistant_{slug}_open_loop_rmse"] = self._ha_state(
                f"sensor.heating_assistant_{slug}_open_loop_rmse",
                "unknown"
                if open_loop_attrs.get("open_loop_rmse") is None
                else open_loop_attrs["open_loop_rmse"],
                open_loop_attrs,
                now,
            )
            fit_state, fit_attrs = sysid_services.model_fit_quality_sensor(self, name)
            states[f"sensor.heating_assistant_{slug}_model_fit_quality"] = self._ha_state(
                f"sensor.heating_assistant_{slug}_model_fit_quality",
                fit_state,
                fit_attrs,
                now,
            )
            conf_state, conf_attrs = sysid_services.parameter_confidence_sensor(self, name)
            states[f"sensor.heating_assistant_{slug}_parameter_confidence"] = self._ha_state(
                f"sensor.heating_assistant_{slug}_parameter_confidence",
                conf_state,
                conf_attrs,
                now,
            )
            states[f"sensor.heating_assistant_{slug}_solar_gain_measured"] = self._ha_state(
                f"sensor.heating_assistant_{slug}_solar_gain_measured",
                round(float(solar_gain), 1),
                {
                    "room": name,
                    "unit_of_measurement": "W",
                    "window_count": len(windows),
                    "total_window_area": round(total_window_area, 2),
                },
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
            if name in self._window_tags:
                states[f"sensor.heating_assistant_{slug}_window_state"] = self._ha_state(
                    f"sensor.heating_assistant_{slug}_window_state",
                    self.get_window_state(name),
                    {
                        "room": name,
                        "override_active": self.is_window_override_active(name),
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
        # or already-bound live values. Mark catalog-backed states so the UI can
        # tell a full HA catalog apart from binding stubs alone.
        for item in self._ha_entity_catalog:
            entity_id = item["entity_id"]
            if entity_id in states:
                # Prefer live friendly names / units from the catalog when the
                # binding stub only has the raw entity_id as its name.
                attrs = states[entity_id].setdefault("attributes", {})
                attrs["heating_assistant_catalog"] = True
                if attrs.get("friendly_name") in (None, "", entity_id):
                    attrs["friendly_name"] = item["name"]
                if item.get("unit") and not attrs.get("unit_of_measurement"):
                    attrs["unit_of_measurement"] = item["unit"]
                continue
            attrs = {
                "friendly_name": item["name"],
                "heating_assistant_catalog": True,
            }
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

        health = self.system_health()
        return {
            "instance_id": self.instance_id,
            "mqtt_broker": self.mqtt_broker,
            "mqtt_port": self.options.get("mqtt_port", 1883),
            "mqtt_username": self.options.get("mqtt_username") or "",
            "mqtt_ssl": bool(self.options.get("mqtt_ssl", False)),
            "mqtt_source": self.options.get("mqtt_source") or "options",
            "mqtt_connected": self._mqtt_connected(),
            "mqtt_last_error": self._mqtt_last_error(),
            "mqtt_discovery_error": get_last_discovery_error(),
            "supervisor_token_present": bool(os.environ.get("SUPERVISOR_TOKEN")),
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
            "quality": health["quality"],
            "issue_summary": health.get("issue_summary"),
            "system_health": health,
            "ts": time.time(),
        }

    def system_health(self) -> dict[str, Any]:
        """Compute overall SystemQuality and module breakdown for Ingress."""

        uptime_s = None
        if self._started and self._started_monotonic is not None:
            uptime_s = max(0.0, time.monotonic() - self._started_monotonic)
        update_interval = float(
            self.options.get("update_interval", const.DEFAULT_UPDATE_INTERVAL)
        )
        return evaluate_system_health(
            mqtt_connected=self._mqtt_connected(),
            mqtt_last_error=self._mqtt_last_error(),
            mqtt_discovery_error=get_last_discovery_error(),
            api_reachable=True,
            tag_statuses=self.tag_statuses,
            control_mode=self.control_engine.mode,
            fallback_reason=self.control_engine.fallback_reason,
            bindings_count=len(self.bindings),
            entity_catalog_count=len(self._ha_entity_catalog),
            started=self._started,
            uptime_s=uptime_s,
            last_control_duration_s=self._last_control_duration_s,
            last_control_ts=self._last_control_ts,
            update_interval_s=update_interval,
        )

    def id_history_health(self, now_ts: float | None = None) -> dict[str, Any]:
        """Card-only ID history health (does not affect overall system_health)."""

        update_interval = float(
            self.options.get("update_interval", const.DEFAULT_UPDATE_INTERVAL)
        )
        return evaluate_id_history_health(
            now_ts=float(now_ts if now_ts is not None else time.time()),
            update_interval_s=update_interval,
            buffer_last_ts=float(self._id_history_last_ts or 0.0),
            disk_last_ts=float(self._id_history_disk_last_ts or 0.0),
            append_failure_streak=int(self._id_history_append_failure_streak),
            last_append_ok=self._id_history_last_append_ok,
        )

    def _mqtt_connected(self) -> bool:
        mqtt_connected = getattr(self.bus, "connected", None)
        if callable(mqtt_connected):
            mqtt_connected = mqtt_connected()
        elif mqtt_connected is None:
            # In-memory bus is always "connected" for local/dev.
            mqtt_connected = True
        return bool(mqtt_connected)

    def _mqtt_last_error(self) -> str | None:
        last_error = getattr(self.bus, "last_error", None)
        if callable(last_error):
            last_error = last_error()
        if isinstance(last_error, str) and last_error.strip():
            return last_error
        return None

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
                    # Climate heat pumps need inbound feedback (internal temp /
                    # hvac_modes) so the App can anchor logit setpoints (SWD-280).
                    if heater_entity.strip().split(".", 1)[0] == "climate":
                        state_tag = bind(heater_entity, "in", f"{tag}_state")
                        if state_tag:
                            source_cfg["state_tag"] = state_tag
                        else:
                            source_cfg.pop("state_tag", None)
                    else:
                        source_cfg.pop("state_tag", None)
            else:
                source_cfg.setdefault("output_tag", preferred)
                tag_name = source_cfg.get("output_tag")
                if isinstance(tag_name, str) and tag_name and tag_name not in used_tags:
                    used_tags.add(tag_name)
                source_cfg.pop("state_tag", None)
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
        """Return outdoor °C from the dedicated sensor, else weather fallback.

        Mirrors classic ``read_outdoor_temp``: prefer ``outdoor_temp_*``, then
        the weather entity's published temperature on ``weather_tag``.
        """

        tag = self.options.get("outdoor_temp_tag") or self.options.get("outdoor_tag")
        if not isinstance(tag, str) or not tag:
            outdoor_entity = self.options.get(const.CONF_OUTDOOR_TEMP_ENTITY)
            if isinstance(outdoor_entity, str) and outdoor_entity:
                for binding in self.bindings:
                    if binding.direction == "in" and binding.entity_id == outdoor_entity:
                        tag = binding.tag
                        break
        if isinstance(tag, str) and tag:
            outdoor = self._coerce_number(self.tag_values.get(tag))
            if outdoor is not None:
                return outdoor

        weather_tag = self.options.get("weather_tag")
        if isinstance(weather_tag, str) and weather_tag:
            return self._coerce_number(self.tag_values.get(weather_tag))
        weather_entity = self.options.get(const.CONF_WEATHER_ENTITY)
        if isinstance(weather_entity, str) and weather_entity:
            for binding in self.bindings:
                if binding.direction == "in" and binding.entity_id == weather_entity:
                    return self._coerce_number(self.tag_values.get(binding.tag))
        return None

    def _price_tag(self) -> str:
        """Return the configured electricity price tag (defaults to energy_price)."""

        price_tag = self.options.get("price_tag") or "energy_price"
        if not isinstance(price_tag, str) or not price_tag:
            return "energy_price"
        return price_tag

    def _price_adder(self) -> float:
        """Net tariff + spot surcharge applied to raw spot for display / MPC."""

        net = float(
            self.options.get(const.CONF_PRICE_NET_TARIFF, const.DEFAULT_PRICE_NET_TARIFF)
            or 0.0
        )
        surcharge = float(
            self.options.get(
                const.CONF_PRICE_SPOT_SURCHARGE, const.DEFAULT_PRICE_SPOT_SURCHARGE
            )
            or 0.0
        )
        return net + surcharge

    def _electricity_price_value(self) -> float | None:
        """Current electricity price for the synthetic sensor / live chart extend.

        Prefers day-ahead series at ``now`` (with tariff adder) so Price matches
        the first Price Forecast step; falls back to the scalar tag value.
        """

        price_tag = self._price_tag()
        raw = self._coerce_number(self.tag_values.get(price_tag))
        if raw is None and price_tag != "energy_price":
            raw = self._coerce_number(self.tag_values.get("energy_price"))
        attrs = dict(self.tag_attributes.get(price_tag) or {})
        adder = self._price_adder()
        now = datetime.now(timezone.utc)
        series = collect_price_series(attrs, now)
        if series:
            aligned = align_prices_to_horizon(series, now, 1, 900.0, adder)
            if aligned:
                return float(aligned[0])
        if raw is None:
            return None
        # Match build_price_forecast scalar fallback (adder + non-negative clamp).
        return max(0.0, float(raw) + adder)

    def _electricity_price_state_attrs(self) -> dict[str, Any]:
        """Attributes for the synthetic electricity price sensor."""

        price_tag = self._price_tag()
        src = dict(self.tag_attributes.get(price_tag) or {})
        attrs: dict[str, Any] = {}
        unit = src.get("unit_of_measurement") or src.get("unit")
        if isinstance(unit, str) and unit:
            attrs["unit_of_measurement"] = unit
        attrs["price_tag"] = price_tag
        return attrs

    def _synthesize_price_history(
        self, *, start_ts: float | None, end_ts: float | None
    ) -> list[dict[str, Any]]:
        """Build stepped Price samples from day-ahead attrs for the plot window.

        Used when the history ring has never recorded ``electricity_price``
        (SWD-284) so historical Price appears immediately left of NOW.
        """

        price_tag = self._price_tag()
        attrs = dict(self.tag_attributes.get(price_tag) or {})
        now = datetime.now(timezone.utc)
        series = collect_price_series(attrs, now)
        if not series:
            return []

        adder = self._price_adder()
        now_ts = now.timestamp()
        end = now_ts if end_ts is None else min(float(end_ts), now_ts)
        start = float(start_ts) if start_ts is not None else end - 12 * 3600.0
        if end < start:
            return []

        # Collapse same-instant duplicates by priority, then prefer known
        # day-ahead (today/tomorrow) over Carnot forecast — matches MPC lookup.
        by_start: dict[float, tuple[Any, ...]] = {}
        for timed in series:
            key = timed[0].timestamp()
            prev = by_start.get(key)
            if prev is None or timed[2] > prev[2]:
                by_start[key] = timed
        collapsed = list(by_start.values())
        known = [timed for timed in collapsed if timed[3] in {"today", "tomorrow"}]
        sorted_series = sorted(known or collapsed, key=lambda timed: timed[0])
        samples: list[dict[str, Any]] = []

        # Zero-order hold at the window start so stepped charts fill from the left.
        active: float | None = None
        for start_dt, price, _priority, _source in sorted_series:
            if start_dt.timestamp() <= start:
                active = max(0.0, float(price) + adder)
            else:
                break
        if active is not None:
            samples.append({"s": str(round(active, 5)), "lu": float(start)})

        for start_dt, price, _priority, _source in sorted_series:
            ts = start_dt.timestamp()
            if ts <= start or ts > end:
                continue
            display = max(0.0, float(price) + adder)
            point = {"s": str(round(display, 5)), "lu": float(ts)}
            if samples and abs(float(samples[-1]["lu"]) - ts) < 1e-6:
                samples[-1] = point
            else:
                samples.append(point)
        return samples

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

    def _schedule_now_local(self) -> datetime:
        """Wall-clock local time for schedule window matching."""

        return datetime.now().astimezone()

    def _resolve_effective_params(
        self, *, now_local: datetime | None = None
    ) -> dict[str, EffectiveControlParams]:
        """Resolve schedule-aware setpoint / comfort for every configured room."""

        now_local = now_local or self._schedule_now_local()
        schedules = self.schedules()
        base_setpoints = self._setpoints()
        default_global = float(
            self.options.get("comfort_offset", const.DEFAULT_COMFORT_OFFSET)
        )
        result: dict[str, EffectiveControlParams] = {}
        for room in self._rooms():
            name = room.get("name")
            if not isinstance(name, str) or not name:
                continue
            slug = self._room_slug(name)
            base = base_setpoints.get(
                name, self._coerce_number(room.get("setpoint")) or const.DEFAULT_SETPOINT
            )
            room_offset = self._coerce_number(room.get("comfort_offset"))
            default_offset = (
                float(room_offset) if room_offset is not None else default_global
            )
            result[name] = resolve_room_effective_params(
                schedule_payload=schedules.get(slug),
                base_setpoint=float(base),
                measured_temp=self.room_temperatures.get(name),
                now=now_local,
                default_comfort_offset=default_offset,
            )
        return result

    def _build_control_trajectory(
        self,
        *,
        n_steps: int,
        dt_seconds: float,
        now_local: datetime | None = None,
        current_effective: Mapping[str, EffectiveControlParams] | None = None,
    ) -> ControlTrajectory:
        """Project schedule setpoints / comfort corridors over ``n_steps``."""

        now_local = now_local or self._schedule_now_local()
        effective = current_effective or self._resolve_effective_params(
            now_local=now_local
        )
        base_setpoints = self._setpoints()
        default_global = float(
            self.options.get("comfort_offset", const.DEFAULT_COMFORT_OFFSET)
        )
        default_offsets: dict[str, float] = {}
        room_enabled: dict[str, bool] = {}
        for room in self._rooms():
            name = room.get("name")
            if not isinstance(name, str) or not name:
                continue
            room_offset = self._coerce_number(room.get("comfort_offset"))
            default_offsets[name] = (
                float(room_offset) if room_offset is not None else default_global
            )
            room_enabled[name] = self._room_enabled(room)
            base_setpoints.setdefault(
                name,
                float(self._coerce_number(room.get("setpoint")) or const.DEFAULT_SETPOINT),
            )
        return compute_control_trajectory(
            rooms=self._rooms(),
            schedules_by_slug=self.schedules(),
            room_slug_fn=self._room_slug,
            base_setpoints=base_setpoints,
            default_comfort_offsets=default_offsets,
            room_enabled=room_enabled,
            now_local=now_local,
            n_steps=n_steps,
            dt_seconds=dt_seconds,
            current_effective=effective,
        )

    def _schedule_control_context(self) -> dict[str, Any]:
        """Build setpoints, comfort offsets, trajectory, and disabled sources."""

        now_local = self._schedule_now_local()
        now_utc = now_local.astimezone(timezone.utc)
        effective = self._resolve_effective_params(now_local=now_local)
        dt = float(self.options.get("update_interval", const.DEFAULT_UPDATE_INTERVAL))
        horizon = int(self.options.get("horizon", const.DEFAULT_HORIZON))
        trajectory = self._build_control_trajectory(
            n_steps=max(horizon, 1),
            dt_seconds=dt,
            now_local=now_local,
            current_effective=effective,
        )
        setpoints = {
            name: float(params.setpoint) for name, params in effective.items()
        }
        comfort_offsets = {
            name: float(params.comfort_offset) for name, params in effective.items()
        }
        disabled_rooms = {
            name
            for name, params in effective.items()
            if not params.enabled
        }
        for room in self._rooms():
            name = room.get("name")
            if isinstance(name, str) and name and not self._room_enabled(room):
                disabled_rooms.add(name)
        disabled_sources: set[str] = set()
        for source in self.control_engine.heat_sources:
            if source.room in disabled_rooms:
                disabled_sources.add(source.name)
            elif self.is_window_override_active(source.room):
                disabled_sources.add(source.name)
        return {
            "now_local": now_local,
            "now_utc": now_utc,
            "effective": effective,
            "setpoints": setpoints,
            "comfort_offsets": comfort_offsets,
            "trajectory": trajectory,
            "disabled_sources": disabled_sources,
        }

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

    async def _apply_climate_service(
        self, service: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Map HA climate entity services onto room setpoint / enablement config.

        The Ingress panel climate cards historically call ``climate.set_temperature``
        / ``turn_on`` / ``turn_off``. After the App rewrite those were accepted as
        no-ops, so UI edits snapped back on the next state refresh (SWD-288).
        """

        entity_ids = self._climate_entity_ids(payload.get("entity_id"))
        if not entity_ids:
            raise ValueError("entity_id is required")

        config: dict[str, Any] | None = None
        for entity_id in entity_ids:
            room_name = self._climate_entity_room_name(entity_id)
            if service == "set_temperature":
                if "temperature" not in payload:
                    raise ValueError("temperature is required")
                config = await self._update_room_value(
                    {"room_name": room_name, "setpoint": payload["temperature"]},
                    "setpoint",
                )
            elif service == "turn_on":
                config = await self._set_room_enabled(
                    {"room_name": room_name, "enabled": True}
                )
            else:  # turn_off
                config = await self._set_room_enabled(
                    {"room_name": room_name, "enabled": False}
                )
        return {"config": config}

    @staticmethod
    def _climate_entity_ids(entity_id: Any) -> list[str]:
        if isinstance(entity_id, str) and entity_id:
            return [entity_id]
        if isinstance(entity_id, list):
            return [str(item) for item in entity_id if item]
        return []

    @classmethod
    def _climate_entity_room_name(cls, entity_id: str) -> str:
        prefix = "climate.heating_assistant_"
        if not entity_id.startswith(prefix):
            raise ValueError(f"unsupported climate entity {entity_id!r}")
        slug = entity_id[len(prefix) :]
        if not slug:
            raise ValueError(f"unsupported climate entity {entity_id!r}")
        return slug

    def _room_power(self, room_name: str) -> float:
        """Return commanded thermal power [W] for a room (not raw fraction)."""

        outdoor = self._outdoor_temperature()
        outdoor_f = float(outdoor) if outdoor is not None else 0.0
        sources_by_name = {src.name: src for src in self.control_engine.heat_sources}
        total = 0.0
        for source_cfg in self._heat_sources():
            if source_cfg.get("room") != room_name:
                continue
            tag = source_cfg.get("output_tag")
            if not isinstance(tag, str):
                continue
            fraction = self._coerce_number(self.actuator_outputs.get(tag))
            if fraction is None:
                continue
            name = source_cfg.get("name")
            source = sources_by_name.get(name) if isinstance(name, str) else None
            if source is None:
                continue
            total += self._source_display_power(source, float(fraction), outdoor_f)
        return total

    def _applied_solar_gains(self) -> dict[str, float]:
        """Return applied current-step solar gains [W] per room (SWD-297)."""

        disturbances = self._mpc_disturbance_inputs(self._outdoor_temperature())
        return self.control_engine.applied_solar_gains(
            now=datetime.now(timezone.utc),
            cloud_cover_now=disturbances.get("cloud_cover_now"),
            ghi_now=disturbances.get("ghi_now"),
        )

    @staticmethod
    def _source_display_power(
        source: HeatSource, fraction: float, outdoor_temp: float
    ) -> float:
        if hasattr(source, "display_smooth_thermal_power"):
            return float(source.display_smooth_thermal_power(fraction, outdoor_temp))
        return float(source.display_thermal_power(fraction, outdoor_temp))

    def _actuator_write_value(self, tag: str, fraction: float) -> Any:
        """Map an MPC fraction to the HA write payload for ``tag``."""

        binding = next(
            (b for b in self.bindings if b.tag == tag and b.direction == "out"),
            None,
        )
        if binding is None:
            return fraction
        domain = binding.entity_id.split(".", 1)[0]
        source_cfg = self._source_cfg_for_output_tag(tag)
        enabled = self._source_actuation_enabled(source_cfg)
        if domain == "number":
            return number_write_payload(fraction, enabled=enabled)
        if domain == "switch":
            return switch_write_payload(fraction, enabled=enabled)
        if domain == "climate":
            return self._climate_write_value(source_cfg, fraction, enabled=enabled)
        return fraction

    def _source_cfg_for_output_tag(self, tag: str) -> Mapping[str, Any] | None:
        for source in self._heat_sources():
            if source.get("output_tag") == tag:
                return source
        return None

    def _is_heater_state_feedback_tag(self, tag: str) -> bool:
        for source in self._heat_sources():
            if source.get("state_tag") == tag:
                return True
        return False

    def _is_window_tag(self, tag: str) -> bool:
        return tag in self._window_tag_to_room

    def get_window_state(self, room_name: str) -> str:
        """Return the window override state for a room."""

        return window_ov.get_window_state(self._window_state, room_name)

    def is_window_override_active(self, room_name: str) -> bool:
        """Return True while heaters for ``room_name`` must stay off."""

        return window_ov.is_window_override_active(self._window_state, room_name)

    def _rebuild_window_maps(self) -> None:
        """Refresh room↔window-tag maps after config / wiring changes."""

        self._window_tags = window_ov.build_window_tag_map(self._rooms())
        self._window_tag_to_room = window_ov.build_tag_to_room(self._window_tags)
        # Drop state for rooms that no longer have sensors; keep others.
        known = set(self._window_tags)
        for room_name in list(self._window_state):
            if room_name not in known:
                self._window_state.pop(room_name, None)
                self._window_state_since.pop(room_name, None)
                self._cancel_window_timer(room_name)
        for room_name in known:
            self._window_state.setdefault(room_name, "closed")

    def _window_debounce_s(self) -> float:
        return float(
            self.options.get(
                "window_open_debounce", const.DEFAULT_WINDOW_OPEN_DEBOUNCE
            )
        )

    def _window_settle_s(self) -> float:
        return float(
            self.options.get(
                "window_open_close_settle", const.DEFAULT_WINDOW_OPEN_CLOSE_SETTLE
            )
        )

    def _window_q_inflation(self) -> float:
        return float(
            self.options.get(
                "window_open_q_inflation", const.DEFAULT_WINDOW_OPEN_Q_INFLATION
            )
        )

    def _cancel_window_timer(self, room_name: str) -> None:
        task = self._window_pending.pop(room_name, None)
        if task is not None and not task.done():
            task.cancel()

    def _cancel_all_window_timers(self) -> None:
        for room_name in list(self._window_pending):
            self._cancel_window_timer(room_name)

    def _schedule_window_timer(
        self, room_name: str, delay_s: float, coro_factory: Any
    ) -> None:
        """Schedule a one-shot debounce/settle callback for ``room_name``."""

        self._cancel_window_timer(room_name)

        async def _runner() -> None:
            try:
                await asyncio.sleep(max(0.0, float(delay_s)))
                await coro_factory()
            except asyncio.CancelledError:
                raise
            finally:
                current = self._window_pending.get(room_name)
                if current is asyncio.current_task():
                    self._window_pending.pop(room_name, None)

        self._window_pending[room_name] = asyncio.create_task(
            _runner(), name=f"window-override-{room_name}"
        )

    async def _on_window_tag_changed(
        self, tag: str, previous_value: Any, new_value: Any
    ) -> None:
        """Advance the window state machine from a contact tag edge."""

        room_name = self._window_tag_to_room.get(tag)
        if room_name is None:
            return
        new_open = window_ov.tag_is_open(new_value)
        old_open = window_ov.tag_is_open(previous_value)
        if new_open == old_open:
            return

        now_utc = window_ov.utcnow()
        tags = self._window_tags.get(room_name, [])
        any_open_now = window_ov.any_tag_open(self.tag_values, tags)
        current = self.get_window_state(room_name)

        if new_open:
            if current in ("open", "pending_open"):
                return
            if current == "pending_closed":
                self._cancel_window_timer(room_name)
                window_ov.set_window_state(
                    self._window_state, self._window_state_since, room_name, "open", now_utc
                )
                await self.push_window_override()
                return
            # closed → pending_open + debounce
            self._cancel_window_timer(room_name)
            window_ov.set_window_state(
                self._window_state,
                self._window_state_since,
                room_name,
                "pending_open",
                now_utc,
            )

            async def _advance_open(_rn: str = room_name) -> None:
                if self.get_window_state(_rn) != "pending_open":
                    return
                tags_in = self._window_tags.get(_rn, [])
                if window_ov.any_tag_open(self.tag_values, tags_in):
                    window_ov.set_window_state(
                        self._window_state,
                        self._window_state_since,
                        _rn,
                        "open",
                        window_ov.utcnow(),
                    )
                    _logger.info(
                        "Window debounce elapsed for %s — heater override active",
                        _rn,
                    )
                await self.push_window_override()

            self._schedule_window_timer(
                room_name, self._window_debounce_s(), _advance_open
            )
            return

        # Sensor closed
        if any_open_now:
            return
        if current == "pending_open":
            self._cancel_window_timer(room_name)
            window_ov.set_window_state(
                self._window_state,
                self._window_state_since,
                room_name,
                "closed",
                now_utc,
            )
            await self.push_window_override()
            return
        if current == "open":
            self._cancel_window_timer(room_name)
            window_ov.set_window_state(
                self._window_state,
                self._window_state_since,
                room_name,
                "pending_closed",
                now_utc,
            )

            async def _advance_closed(_rn: str = room_name) -> None:
                if self.get_window_state(_rn) != "pending_closed":
                    return
                tags_in = self._window_tags.get(_rn, [])
                if not window_ov.any_tag_open(self.tag_values, tags_in):
                    window_ov.set_window_state(
                        self._window_state,
                        self._window_state_since,
                        _rn,
                        "closed",
                        window_ov.utcnow(),
                    )
                    _logger.info(
                        "Window settle elapsed for %s — heater override cleared",
                        _rn,
                    )
                await self.push_window_override()

            self._schedule_window_timer(
                room_name, self._window_settle_s(), _advance_closed
            )

    async def push_window_override(self) -> None:
        """Clamp / restore actuators for window override without an MPC solve."""

        shadow = self.control_engine.mpc_actions_by_tag()
        source_tags = getattr(self.control_engine, "_source_output_tags", {}) or {}
        for source in self.control_engine.heat_sources:
            tag = source_tags.get(source.name, source.name)
            if self.is_window_override_active(source.room):
                self.actuator_outputs[tag] = 0.0
                continue
            # Resume closed rooms at the unconstrained MPC shadow when available.
            if tag in shadow:
                room_ok = True
                for room in self._rooms():
                    name = room.get("name")
                    if isinstance(name, str) and name == source.room:
                        room_ok = self._room_enabled(room)
                        break
                if room_ok and bool(self.options.get("system_enabled", False)):
                    self.actuator_outputs[tag] = float(shadow[tag])
        self._save_runtime_state()
        await self._best_effort_mqtt(self.publish_actuator_outputs(), "window override")

    def _clamp_window_override_actuators(self) -> None:
        """Force override rooms to 0 in the latest actuator map."""

        source_tags = getattr(self.control_engine, "_source_output_tags", {}) or {}
        for source in self.control_engine.heat_sources:
            if not self.is_window_override_active(source.room):
                continue
            tag = source_tags.get(source.name, source.name)
            self.actuator_outputs[tag] = 0.0

    def _apply_window_q_inflation(self) -> None:
        """Inflate EKF process noise for rooms under window override."""

        controller = getattr(self.control_engine, "_controller", None)
        if controller is None:
            return
        setter = getattr(controller, "set_room_process_noise_covariance_scales", None)
        if not callable(setter):
            return
        inflation = self._window_q_inflation()
        scales = {
            room_name: (
                inflation if self.is_window_override_active(room_name) else 1.0
            )
            for room_name in self.control_engine.model.room_names
        }
        setter(scales)
        window_setter = getattr(controller, "set_window_open", None)
        if callable(window_setter):
            flags = {
                room_name: self.is_window_override_active(room_name)
                for room_name in self.control_engine.model.room_names
            }
            window_setter(flags)

    def _source_actuation_enabled(self, source_cfg: Mapping[str, Any] | None) -> bool:
        if not bool(self.options.get("system_enabled", False)):
            return False
        if source_cfg is None:
            return True
        room_name = source_cfg.get("room")
        if not isinstance(room_name, str) or not room_name:
            return True
        if self.is_window_override_active(room_name):
            return False
        for room in self._rooms():
            name = room.get("name")
            if isinstance(name, str) and name == room_name:
                return self._room_enabled(room)
        return True

    def _climate_write_value(
        self,
        source_cfg: Mapping[str, Any] | None,
        fraction: float,
        *,
        enabled: bool,
    ) -> dict[str, Any]:
        outdoor = self._outdoor_temperature()
        outdoor_f = float(outdoor) if outdoor is not None else 0.0
        source = self._heat_source_object(source_cfg)
        room_name = source_cfg.get("room") if source_cfg else None
        room_temp = (
            self.room_temperatures.get(room_name)
            if isinstance(room_name, str)
            else None
        )
        setpoints = self._setpoints()
        room_setpoint = setpoints.get(room_name) if isinstance(room_name, str) else None

        state_tag = source_cfg.get("state_tag") if source_cfg else None
        attrs = coerce_climate_attrs(
            self.tag_attributes.get(state_tag) if isinstance(state_tag, str) else None
        )
        internal_temp = self._coerce_number(attrs.get("current_temperature"))
        supported = attrs.get("hvac_modes")
        if not isinstance(supported, (list, tuple)):
            supported = None

        if source is None:
            # Config present but engine has not built the source yet — still
            # turn climate off when disabled; otherwise pass a simple offset.
            if not enabled:
                return {"hvac_mode": "off"}
            base = (
                internal_temp
                if internal_temp is not None
                else (float(room_temp) if room_temp is not None else 21.0)
            )
            return {
                "hvac_mode": "heat_cool" if fraction < 0.0 else "heat",
                "temperature": base + (3.0 * float(fraction)),
            }

        return climate_write_payload(
            source,
            fraction,
            enabled=enabled,
            internal_temp=internal_temp,
            outdoor_temp=outdoor_f,
            room_temp=float(room_temp) if room_temp is not None else None,
            room_setpoint=float(room_setpoint) if room_setpoint is not None else None,
            supported_modes=supported,
        )

    def _heat_source_object(
        self, source_cfg: Mapping[str, Any] | None
    ) -> HeatSource | None:
        if source_cfg is None:
            return None
        name = source_cfg.get("name")
        if not isinstance(name, str) or not name:
            return None
        for source in self.control_engine.heat_sources:
            if source.name == name:
                return source
        return None

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
        return room_slug(name)

    def _restore_estimated_parameters(self) -> None:
        """Re-apply persisted estimated params after ControlEngine rebuilds."""

        snapshot = estimated_params_snapshot(self.options)
        if not snapshot:
            return
        restore_estimated_parameters(
            self.control_engine.model,
            self.control_engine.heat_sources,
            snapshot,
        )

    def _live_room_thermal_attrs(self, room_name: str) -> dict[str, Any]:
        """Return live thermal-model attrs for the sysid form / filtered sensor."""

        rooms = getattr(getattr(self.control_engine, "model", None), "rooms", {}) or {}
        room = rooms.get(room_name)
        if room is None:
            return {}
        attrs: dict[str, Any] = {}
        for key in (
            "thermal_mass",
            "r_external",
            "internal_gain",
            "solar_scale",
            "c_air_fraction",
            "r_aw_fraction",
        ):
            value = getattr(room, key, None)
            if value is None:
                continue
            try:
                attrs[key] = float(value)
            except (TypeError, ValueError):
                continue
        return attrs

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
        self.state["tag_attributes"] = {
            key: dict(value) for key, value in self.tag_attributes.items()
        }
        self.state["room_temperatures"] = dict(self.room_temperatures)
        self.state["actuator_outputs"] = dict(self.actuator_outputs)
        self.state["last_control_ts"] = self._last_control_ts
        self.state["last_control_duration_s"] = self._last_control_duration_s
        self.state["energy_total_wh"] = dict(self._energy_total_wh)
        self.state["energy_last_ts"] = self._energy_last_ts
        self.state["config"] = dict(self.options)
        save_state(self.data_dir, self.state)

    def _restore_durable_history(self, *, plot_hours: float) -> None:
        """Load plot + identification history from the App data volume (SWD-281)."""

        try:
            self._plot_history_store.setup()
            restored = self._plot_history_store.load_recent(
                hours_back=max(plot_hours, const.DEFAULT_PLOT_HISTORY_HOURS),
                max_per_entity=_HISTORY_MAX_SAMPLES,
            )
            with self._history_lock:
                self._history = restored
                if restored:
                    last_ts = 0.0
                    for samples in restored.values():
                        if samples:
                            last_ts = max(last_ts, float(samples[-1]["lu"]))
                    if last_ts > 0.0:
                        self._history_last_ts = last_ts
            if restored:
                _logger.info(
                    "Restored plot history for %d entities from App data volume",
                    len(restored),
                )
        except Exception:
            _logger.exception("Failed to restore plot history from App data volume")

        try:
            self.id_history_store.setup()
            rebuilt = self.id_history_store.query_recent(const.HISTORY_BUFFER_SIZE)
            if rebuilt:
                self._history_buffer.extend(rebuilt[-const.HISTORY_BUFFER_SIZE :])
                try:
                    self._id_history_last_ts = float(self._history_buffer[-1].get("timestamp") or 0.0)
                except (TypeError, ValueError, IndexError):
                    self._id_history_last_ts = 0.0
                self._id_history_disk_last_ts = float(self._id_history_last_ts)
                self._id_history_last_append_ok = True
                self._id_history_append_failure_streak = 0
                _logger.info(
                    "Restored %d identification history steps from JSONL store",
                    len(self._history_buffer),
                )
        except Exception:
            _logger.exception("Failed to restore identification history from JSONL")

    def _sync_history_retention(self) -> None:
        """Refresh store retention from the current options."""

        plot_hours = float(
            self.options.get(const.CONF_PLOT_HISTORY_HOURS, const.DEFAULT_PLOT_HISTORY_HOURS)
        )
        self._plot_history_store.update_retention_days(
            PlotHistoryStore.retention_days_for_plot_hours(plot_hours)
        )
        id_days = int(
            self.options.get(
                const.CONF_PARAMETER_ESTIMATION_HISTORY_DAYS,
                const.DEFAULT_PARAMETER_ESTIMATION_HISTORY_DAYS,
            )
        )
        self.id_history_store.update_retention_days(id_days)

    def _record_history_samples(
        self, *, force: bool = False, persist: bool = True
    ) -> list[dict[str, Any]]:
        """Append current synthetic entity values into the history ring + JSONL.

        Samples are gated to ``update_interval`` so Ingress plots align with the
        control cadence (SWD-277). ``force=True`` is reserved for init/config.
        Durable appends keep room charts alive across App updates (SWD-281).

        When ``persist=False``, durable writes are skipped and the pending samples
        are returned so an async caller can offload them via ``asyncio.to_thread``.
        """

        now = time.time()
        interval = self._history_tick_interval_s()
        # Build states outside the lock — only the ring mutation needs serialization.
        states = self.hass_states()
        persisted: list[dict[str, Any]] = []
        with self._history_lock:
            if not force and (now - self._history_last_ts) < interval:
                return []
            self._history_last_ts = now
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
                sample = {"s": str(raw), "lu": now}
                bucket = self._history.setdefault(
                    entity_id, deque(maxlen=_HISTORY_MAX_SAMPLES)
                )
                bucket.append(sample)
                persisted.append({"entity_id": entity_id, "s": sample["s"], "lu": now})
        if persist and persisted:
            try:
                self._plot_history_store.append_samples(persisted)
                self._plot_history_store.purge_old()
            except Exception:
                _logger.exception("Failed to persist plot history samples")
            return []
        return persisted

    def _take_identification_sample(
        self, now: float | None = None, *, force: bool = False
    ) -> dict[str, Any] | None:
        """Build one identification observation for durable-first write.

        Does **not** mutate ``_history_buffer`` or ``_id_history_last_ts`` —
        callers must durable-append first, then
        :meth:`_commit_identification_sample` (SWD-318). Returns ``None`` when
        gated by ``update_interval`` or unwired / no measured temps.
        """

        now_ts = float(now if now is not None else time.time())
        interval = self._history_tick_interval_s()
        if not force and (now_ts - self._id_history_last_ts) < interval:
            return None

        room_names = [
            name
            for room in self._rooms()
            if isinstance((name := room.get("name")), str) and name
        ]
        if not room_names:
            return None
        # Do not seed empty/unknown ticks into ID history (startup before MQTT).
        if not any(self.room_temperatures.get(name) is not None for name in room_names):
            return None

        y: list[float] = []
        for name in room_names:
            temp = self.room_temperatures.get(name)
            if temp is None:
                temp = self._coerce_number(
                    getattr(self.control_engine.model.rooms.get(name), "temperature", None)
                )
            y.append(float(temp) if temp is not None else 0.0)

        u: list[float] = []
        source_tags = getattr(self.control_engine, "_source_output_tags", {}) or {}
        for source in self.control_engine.heat_sources:
            tag = source_tags.get(source.name, source.name)
            value = self._coerce_number(self.actuator_outputs.get(tag))
            if value is None:
                value = self._coerce_number(self.tag_values.get(tag))
            u.append(float(value) if value is not None else 0.0)

        outdoor = self._outdoor_temperature()
        if outdoor is None:
            outdoor = 0.0

        solar = self.control_engine.applied_solar_gains()
        for name in room_names:
            if name not in solar:
                solar[name] = 0.0

        y_pred_aligned = None
        if self._history_buffer:
            y_pred_aligned = self._history_buffer[-1].get("y_pred_for_next")

        forecast = self.control_engine.forecast_snapshot()
        filtered = forecast.get("filtered_temperatures") or {}
        predictions = forecast.get("predictions") or []
        if predictions and isinstance(predictions[0], Mapping):
            y_pred_for_next = [
                float(predictions[0].get(name, y[idx] if idx < len(y) else 0.0) or 0.0)
                for idx, name in enumerate(room_names)
            ]
        elif isinstance(filtered, Mapping) and filtered:
            y_pred_for_next = [
                float(filtered.get(name, y[idx] if idx < len(y) else 0.0) or 0.0)
                for idx, name in enumerate(room_names)
            ]
        else:
            y_pred_for_next = list(y)

        return {
            "y": y,
            "y_pred": y_pred_aligned,
            "y_pred_for_next": y_pred_for_next,
            "u": u,
            "d_outdoor": float(outdoor),
            "d_solar": solar,
            "window_open": {
                name: self.is_window_override_active(name) for name in room_names
            },
            "timestamp": now_ts,
        }

    def _commit_identification_sample(self, record: Mapping[str, Any]) -> None:
        """Buffer + advance gate after a successful durable append (SWD-318)."""

        payload = dict(record)
        now_ts = float(payload.get("timestamp") or time.time())
        payload["timestamp"] = now_ts
        self._history_buffer.append(payload)
        self._id_history_last_ts = now_ts
        self._id_history_disk_last_ts = now_ts
        self._id_history_append_failure_streak = 0
        self._id_history_last_append_ok = True

    def _note_identification_append_failure(self) -> None:
        """Record a durable ID append failure for System Status (SWD-317)."""

        self._id_history_append_failure_streak = int(
            self._id_history_append_failure_streak
        ) + 1
        self._id_history_last_append_ok = False

    def _record_identification_sample(
        self, now: float | None = None, *, force: bool = False
    ) -> None:
        """Sync path: durable JSONL append, then buffer (ticker / update_tag)."""

        record = self._take_identification_sample(now, force=force)
        if record is None:
            return
        try:
            self.id_history_store.append(record)
        except Exception:
            _logger.exception("Failed to persist identification history record")
            self._note_identification_append_failure()
            return
        self._commit_identification_sample(record)
        try:
            self.id_history_store.purge_old()
        except Exception:
            _logger.exception("Failed to purge old identification history")

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
