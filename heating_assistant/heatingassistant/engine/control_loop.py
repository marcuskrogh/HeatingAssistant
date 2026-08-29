"""App-facing control loop wrapper for the HA-independent engine."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import logging
import threading
from typing import Any

from . import const
from .control_engine_build import BuildMixin
from .control_engine_preview import (  # noqa: F401
    PreviewMixin,
    _PREVIEW_TUNING_KEYS,
    _PREVIEW_WEIGHT_DEFAULTS,
    _snapshot_from_controller,
)
from .heat_sources import ElectricHeater, GenericThermostat, HeatPump, HeatSource
from .nmpc_p import require_non_negative_p_gating
from .nmpc_timing import timing_from_options
from .thermal_model import HouseModel, Room, RoomConnection, Window

_LOGGER = logging.getLogger(__name__)


def reject_negative_p_gating_knobs(mapping: Mapping[str, Any]) -> None:
    """Raise if live P-deadband or NMPC-off gate knobs are negative."""

    require_non_negative_p_gating(
        float(mapping[const.CONF_P_DEADBAND])
        if const.CONF_P_DEADBAND in mapping
        else 0.0,
        float(mapping[const.CONF_U_REF_GATE])
        if const.CONF_U_REF_GATE in mapping
        else 0.0,
    )


class ControlEngine(BuildMixin, PreviewMixin):
    """Build and step the control model from App configuration.

    The wrapper keeps the App runtime independent from the full HA coordinator.
    It uses the copied MPC controller when it can be imported and constructed;
    otherwise it exposes the same ``step``/``compute_actions`` contract through
    a small proportional controller so MQTT I/O and persistence stay live.
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = {}
        self.model: HouseModel = HouseModel([])
        self.heat_sources: list[HeatSource] = []
        self.mode = "proportional"
        self.fallback_reason: str | None = None
        self._controller: Any = None
        self._source_output_tags: dict[str, str] = {}
        self._room_output_tags: dict[str, list[str]] = {}
        self._last_predictions: list[dict[str, float]] = []
        self._last_linearised_predictions: list[dict[str, float]] = []
        self._last_heating_schedule: list[dict[str, float]] = []
        self._last_outdoor_forecast: list[float] = []
        self._last_solar_forecast: list[dict[str, float]] = []
        self._last_price_forecast: list[float] = []
        self._last_filtered_temperatures: dict[str, float] = {}
        self._last_compute_ts: datetime | None = None
        self._forecast_lock = threading.Lock()
        self._nmpc_last_kwargs: dict[str, Any] = {}
        self._nmpc_worker_kwargs: dict[str, Any] = {}
        self._last_p_actions: dict[str, float] = {}
        self.update_config(config or {})

    def update_config(self, config: Mapping[str, Any]) -> None:
        """Rebuild model/controller state from an App config dictionary."""

        reject_negative_p_gating_knobs(config)
        self.config = dict(config)
        try:
            timing = self._nmpc_timing(self.config)
            self.config[const.CONF_UPDATE_INTERVAL] = timing.dt_s
            self.config[const.CONF_HORIZON] = timing.n_fast
            self.config[const.CONF_NMPC_PERIOD] = timing.period_s
            self.config[const.CONF_NMPC_FAST_SUBSTEPS] = timing.fast_substeps
            self.config[const.CONF_NMPC_HORIZON_H] = timing.horizon_h
        except ValueError:
            _LOGGER.warning("Invalid NMPC timing triple in config; controller build may fail")
        self.model = _build_house_model(_list_of_mappings(self.config.get("rooms")))
        self.heat_sources = _build_heat_sources(
            _list_of_mappings(self.config.get("heat_sources"))
        )
        self._source_output_tags = _source_output_tags(self.config, self.heat_sources)
        self._room_output_tags = _room_output_tags(self.config)
        self._controller = self._try_build_controller()

    def step(self, inputs: Mapping[str, Any]) -> dict[str, float]:
        """Compute actuator outputs from a generic input payload."""

        room_temps = _float_mapping(inputs.get("room_temps") or inputs.get("room_temperatures"))
        outdoor_temp = _as_float(inputs.get("outdoor_temp"), 0.0)
        setpoints = _float_mapping(inputs.get("setpoints"))
        return self.compute_actions(room_temps, outdoor_temp, setpoints)

    def compute_actions(
        self,
        room_temps: Mapping[str, float | None],
        outdoor_temp: float | None,
        setpoints: Mapping[str, float] | None = None,
        *,
        outdoor_forecast: list[float] | None = None,
        cloud_forecast: list[float] | None = None,
        cloud_cover_now: float | None = None,
        ghi_forecast: list[float | None] | None = None,
        ghi_now: float | None = None,
        price_forecast: list[float] | None = None,
        comfort_offsets: Mapping[str, float] | None = None,
        control_trajectory: Any | None = None,
        disabled_sources: set[str] | None = None,
        now: datetime | None = None,
    ) -> dict[str, float]:
        """Return MQTT output tag values for the current room temperatures."""

        setpoints = dict(setpoints or {})
        outdoor = _as_float(outdoor_temp, 0.0)
        self._apply_measurements(room_temps, setpoints)
        if comfort_offsets:
            for name, offset in comfort_offsets.items():
                room = self.model.rooms.get(name)
                if room is not None:
                    room.comfort_offset = float(offset)

        if self._controller is not None and self.heat_sources:
            try:
                # Do not force solar_gains=0 — let the controller compute
                # geometric / GHI-driven gains (SWD-278).
                actions = self._controller.compute(
                    outdoor,
                    solar_gains=None,
                    now=now or datetime.now(timezone.utc),
                    outdoor_forecast=outdoor_forecast,
                    cloud_forecast=cloud_forecast,
                    cloud_cover_now=cloud_cover_now,
                    ghi_forecast=ghi_forecast,
                    ghi_now=ghi_now,
                    price_forecast=price_forecast,
                    control_trajectory=control_trajectory,
                    disabled_sources=disabled_sources,
                )
                self._nmpc_last_kwargs = {
                    "outdoor_temp": outdoor,
                    "now": now or datetime.now(timezone.utc),
                    "outdoor_forecast": outdoor_forecast,
                    "cloud_forecast": cloud_forecast,
                    "cloud_cover_now": cloud_cover_now,
                    "ghi_forecast": ghi_forecast,
                    "ghi_now": ghi_now,
                    "price_forecast": price_forecast,
                    "control_trajectory": control_trajectory,
                }
                self._cache_controller_forecast(self._controller)
                self.mode = "mpc"
                self.fallback_reason = None
                return self._actions_to_tags(actions)
            except Exception as exc:  # pragma: no cover - depends on optional MPC stack
                self.mode = "proportional"
                self.fallback_reason = f"controller compute failed: {exc}"
                self._clear_controller_forecast()
                _LOGGER.warning("HeatingAssistant engine MPC compute failed: %s", exc)

        self._clear_controller_forecast()
        return self._fallback_actions(room_temps, setpoints, outdoor)

    def forecast_snapshot(self) -> dict[str, Any]:
        """Return the last MPC trajectories for Ingress forecast plots."""

        with self._forecast_lock:
            return {
                "mode": self.mode,
                "compute_ts": self._last_compute_ts,
                "predictions": [dict(item) for item in self._last_predictions],
                "linearised_predictions": [
                    dict(item) for item in self._last_linearised_predictions
                ],
                "heating_schedule": [dict(item) for item in self._last_heating_schedule],
                "outdoor_forecast": list(self._last_outdoor_forecast),
                "solar_forecast": [dict(item) for item in self._last_solar_forecast],
                "price_forecast": list(self._last_price_forecast),
                "filtered_temperatures": dict(self._last_filtered_temperatures),
                "dt": float(self._derived_dt()),
                "horizon": int(self._derived_horizon()),
            }

    def mpc_actions_by_tag(self) -> dict[str, float]:
        """Unconstrained MPC optimum mapped to output tags (window resume)."""

        controller = self._controller
        if controller is None:
            return {}
        raw = getattr(controller, "mpc_actions", None)
        if raw is None:
            return {}
        actions = dict(raw() if callable(raw) else raw)
        return self._actions_to_tags(actions)

    def _nmpc_timing(self, config: Mapping[str, Any] | None = None):
        return timing_from_options(
            config if config is not None else self.config,
            default_period=const.DEFAULT_NMPC_PERIOD,
            default_substeps=const.DEFAULT_NMPC_FAST_SUBSTEPS,
            default_horizon_h=const.DEFAULT_NMPC_HORIZON_H,
        )

    def _derived_dt(self, config: Mapping[str, Any] | None = None) -> float:
        try:
            return float(self._nmpc_timing(config).dt_s)
        except ValueError:
            return float(
                (config or self.config).get(
                    "update_interval", const.DEFAULT_UPDATE_INTERVAL
                )
            )

    def _derived_horizon(self, config: Mapping[str, Any] | None = None) -> int:
        try:
            return int(self._nmpc_timing(config).n_fast)
        except ValueError:
            return int(
                (config or self.config).get("horizon", const.DEFAULT_HORIZON)
            )

    def nmpc_due(self) -> bool:
        controller = self._controller
        return bool(controller is not None and getattr(controller, "nmpc_due", False))

    def nmpc_plan_idle(self) -> bool:
        controller = self._controller
        if controller is None:
            return False
        idle = getattr(controller, "nmpc_plan_idle", None)
        if callable(idle):
            return bool(idle())
        return False

    def mark_nmpc_busy(self) -> None:
        """Mark the NLP in-flight and freeze the worker's EKF / input snapshot."""

        controller = self._controller
        kwargs = dict(self._nmpc_last_kwargs)
        if controller is not None:
            x_hat = getattr(getattr(controller, "_ekf", None), "x_hat", None)
            if x_hat is not None:
                kwargs["x0"] = x_hat.copy()
            u_prev = getattr(controller, "_u_prev", None)
            if u_prev is not None:
                kwargs["u_prev"] = u_prev.copy()
            controller._nmpc_busy = True
        self._nmpc_worker_kwargs = kwargs

    def solve_nmpc_blocking(self) -> dict[str, Any]:
        """Run the slow NLP on the caller thread (worker / tests)."""
        controller = self._controller
        if controller is None:
            return {"accepted": False, "fun": float("nan"), "u_star": None}
        kwargs = dict(self._nmpc_worker_kwargs or self._nmpc_last_kwargs)
        return controller.solve_nmpc(**kwargs)

    def apply_nmpc_result(
        self,
        result: Mapping[str, Any],
        *,
        plan_epoch: float | None = None,
        now: float | None = None,
    ) -> bool:
        controller = self._controller
        if controller is None:
            return False
        applied = bool(
            controller.apply_nmpc_result(
                dict(result), plan_epoch=plan_epoch, now=now
            )
        )
        if applied:
            self._cache_controller_forecast(controller)
            actions = controller.refresh_p_command()
            self._last_p_actions = self._actions_to_tags(actions)
        return applied

    def consume_watchdog_notification(self) -> str | None:
        controller = self._controller
        if controller is None:
            return None
        return controller.consume_watchdog_notification()

    def applied_solar_gains(
        self,
        *,
        now: datetime | None = None,
        cloud_cover_now: float | None = None,
        ghi_now: float | None = None,
    ) -> dict[str, float]:
        """Return current-step solar gains [W] per room (applied / measured).

        Prefers ``solar_forecast[0]`` from the last MPC compute — that step is
        built with the same GHI/cloud inputs as the applied disturbance
        (``_current_solar``). Falls back to a live geometric compute when the
        forecast cache is empty but the controller is available (SWD-297).
        """

        with self._forecast_lock:
            steps = [dict(item) for item in self._last_solar_forecast]
        if steps:
            first = steps[0]
            out: dict[str, float] = {}
            for name in self.model.rooms:
                try:
                    out[name] = float(first.get(name, 0.0) or 0.0)
                except (TypeError, ValueError):
                    out[name] = 0.0
            return out

        controller = self._controller
        if controller is not None and hasattr(controller, "_current_solar"):
            try:
                return {
                    str(name): float(value)
                    for name, value in dict(
                        controller._current_solar(
                            now or datetime.now(timezone.utc),
                            cloud_cover=cloud_cover_now,
                            ghi=ghi_now,
                        )
                    ).items()
                }
            except Exception:  # pragma: no cover - defensive
                _LOGGER.debug(
                    "applied_solar_gains live compute failed", exc_info=True
                )
        return {name: 0.0 for name in self.model.rooms}

    def room_power_meta(
        self, outdoor_temp: float | None = None
    ) -> dict[str, dict[str, float]]:
        """Configured / rated heating capacity per room name for plot bounds."""

        outdoor = float(outdoor_temp) if outdoor_temp is not None else 0.0
        by_room: dict[str, dict[str, float]] = {}
        for source in self.heat_sources:
            room = str(source.room)
            bucket = by_room.setdefault(
                room,
                {
                    "max_power": 0.0,
                    "current_rated_max_power": 0.0,
                    "max_cooling_power": 0.0,
                },
            )
            bucket["max_power"] += float(source.max_power)
            try:
                rated = float(source.rated_heating_capacity(outdoor))
            except Exception:  # pragma: no cover - defensive
                rated = float(source.max_power)
            bucket["current_rated_max_power"] += rated
            cooling = getattr(source, "max_cooling_power", None)
            if cooling is None:
                cooling = getattr(source, "rated_cooling_power", None)
            if cooling is None:
                cooling = getattr(source, "rated_cooling_capacity", None)
                if callable(cooling):
                    try:
                        cooling = cooling(outdoor)
                    except Exception:  # pragma: no cover
                        cooling = 0.0
            if cooling is not None:
                try:
                    bucket["max_cooling_power"] += abs(float(cooling))
                except (TypeError, ValueError):
                    pass
        for meta in by_room.values():
            meta["current_max_power"] = meta["current_rated_max_power"]
            for key, value in list(meta.items()):
                meta[key] = round(float(value), 1)
        return by_room

    def _cache_controller_forecast(self, controller: Any) -> None:
        snap = _snapshot_from_controller(
            controller,
            dt=self._derived_dt(),
            horizon=self._derived_horizon(),
            compute_ts=datetime.now(timezone.utc),
        )
        with self._forecast_lock:
            self._last_predictions = snap["predictions"]
            self._last_linearised_predictions = snap["linearised_predictions"]
            self._last_heating_schedule = snap["heating_schedule"]
            self._last_outdoor_forecast = snap["outdoor_forecast"]
            self._last_solar_forecast = snap["solar_forecast"]
            self._last_price_forecast = snap["price_forecast"]
            self._last_filtered_temperatures = snap["filtered_temperatures"]
            self._last_compute_ts = snap["compute_ts"]

    def _clear_controller_forecast(self) -> None:
        with self._forecast_lock:
            self._last_predictions = []
            self._last_linearised_predictions = []
            self._last_heating_schedule = []
            self._last_outdoor_forecast = []
            self._last_solar_forecast = []
            self._last_price_forecast = []
            self._last_filtered_temperatures = {}
            self._last_compute_ts = None

    def _apply_measurements(
        self,
        room_temps: Mapping[str, float | None],
        setpoints: Mapping[str, float],
    ) -> None:
        for name, room in self.model.rooms.items():
            temp = room_temps.get(name)
            if temp is not None:
                room.temperature = float(temp)
            room.setpoint = float(setpoints.get(name, room.setpoint))

    def _actions_to_tags(self, actions: Mapping[str, Any]) -> dict[str, float]:
        outputs: dict[str, float] = {}
        for source_name, value in actions.items():
            tag = self._source_output_tags.get(str(source_name), str(source_name))
            outputs[tag] = _clamp(_as_float(value, 0.0), -1.0, 1.0)
        return outputs

    def _fallback_actions(
        self,
        room_temps: Mapping[str, float | None],
        setpoints: Mapping[str, float],
        outdoor_temp: float,
    ) -> dict[str, float]:
        outputs: dict[str, float] = {}

        for source in self.heat_sources:
            temp = room_temps.get(source.room)
            if temp is None:
                continue
            setpoint = setpoints.get(source.room, _room_setpoint(self.config, source.room))
            fraction = _proportional_fraction(float(temp), setpoint)
            tag = self._source_output_tags.get(source.name, source.name)
            outputs[tag] = fraction
            source.set_power(fraction, outdoor_temp)

        if outputs:
            return outputs

        for room, tags in self._room_output_tags.items():
            temp = room_temps.get(room)
            if temp is None:
                continue
            fraction = _proportional_fraction(
                float(temp),
                setpoints.get(room, _room_setpoint(self.config, room)),
            )
            for tag in tags:
                outputs[tag] = fraction

        return outputs


def _build_house_model(rooms_cfg: list[Mapping[str, Any]]) -> HouseModel:
    known_rooms = {str(room.get("name")) for room in rooms_cfg if room.get("name")}
    rooms: list[Room] = []
    for room_cfg in rooms_cfg:
        name = room_cfg.get("name")
        if not isinstance(name, str) or not name:
            continue
        connections: list[RoomConnection] = []
        for connection in _list_of_mappings(room_cfg.get("connections")):
            target = connection.get("room")
            if target not in known_rooms:
                continue
            r_value = _as_float(connection.get("r_value"), 0.0)
            if r_value > 0.0:
                connections.append(RoomConnection(connected_room=str(target), r_value=r_value))

        windows: list[Window] = []
        for window in _list_of_mappings(room_cfg.get("windows")):
            area = _as_float(window.get("area"), 0.0)
            if area <= 0.0:
                continue
            windows.append(
                Window(
                    area=area,
                    orientation=_as_float(window.get("orientation"), 180.0),
                    tilt=_as_float(window.get("tilt"), const.DEFAULT_WINDOW_TILT),
                )
            )

        # Option A (quick estimate): map solar_exposure → effective aperture.
        # Without this, rooms with High/Medium/Low exposure but no windows
        # silently get aperture 0 and solar gain stays flat at zero (SWD-282).
        exposure_raw = room_cfg.get(const.CONF_SOLAR_EXPOSURE, const.DEFAULT_SOLAR_EXPOSURE)
        exposure_key = str(exposure_raw or const.DEFAULT_SOLAR_EXPOSURE).strip().lower()
        aperture = float(
            const.SOLAR_EXPOSURE_TO_APERTURE.get(
                exposure_key,
                const.SOLAR_EXPOSURE_TO_APERTURE[const.DEFAULT_SOLAR_EXPOSURE],
            )
        )
        facing = _as_float(
            room_cfg.get(const.CONF_SOLAR_FACING), const.DEFAULT_SOLAR_FACING
        )

        rooms.append(
            Room(
                name=name,
                thermal_mass=_as_float(room_cfg.get("thermal_mass"), const.DEFAULT_THERMAL_MASS),
                r_external=_as_float(room_cfg.get("r_external"), const.DEFAULT_R_EXTERNAL),
                connections=connections,
                windows=windows,
                setpoint=_as_float(room_cfg.get("setpoint"), const.DEFAULT_SETPOINT),
                comfort_offset=_as_float(
                    room_cfg.get("comfort_offset"), const.DEFAULT_COMFORT_OFFSET
                ),
                temperature=_as_float(room_cfg.get("temperature"), const.DEFAULT_SETPOINT),
                solar_exposure_aperture=aperture,
                solar_facing=facing,
            )
        )
    return HouseModel(rooms)


def _build_heat_sources(sources_cfg: list[Mapping[str, Any]]) -> list[HeatSource]:
    sources: list[HeatSource] = []
    for source_cfg in sources_cfg:
        name = source_cfg.get("name")
        room = source_cfg.get("room")
        if not isinstance(name, str) or not name or not isinstance(room, str) or not room:
            continue
        source_type = str(source_cfg.get("type") or const.SOURCE_TYPE_ELECTRIC)
        max_power = _as_float(source_cfg.get("max_power"), 1000.0)
        emitter_tau = _as_float(
            source_cfg.get("emitter_time_constant"),
            const.SOURCE_TYPE_TO_DEFAULT_EMITTER_TAU.get(source_type, 0.0),
        )
        common = {
            "name": name,
            "room": room,
            "max_power": max_power,
            "heater_entity": source_cfg.get("heater_entity"),
            "power_scale": _as_float(source_cfg.get("power_scale"), 1.0),
            "emitter_time_constant": emitter_tau,
        }
        p_gain = _as_float(source_cfg.get(const.CONF_P_GAIN), const.DEFAULT_P_GAIN)
        try:
            if source_type == const.SOURCE_TYPE_HEAT_PUMP:
                sources.append(
                    HeatPump(
                        **common,
                        cop_rated=_as_float(source_cfg.get("cop_rated"), const.DEFAULT_COP_RATED),
                        cop_temp_ref=_as_float(
                            source_cfg.get("cop_temp_ref"), const.DEFAULT_COP_TEMP_REF
                        ),
                        min_power=_as_float(source_cfg.get("min_power"), const.DEFAULT_MIN_POWER),
                        max_temp_offset=_as_float(
                            source_cfg.get("max_temp_offset"),
                            const.DEFAULT_MAX_TEMP_OFFSET,
                        ),
                        delta_sat=_as_float(
                            source_cfg.get("delta_sat"), const.DEFAULT_DELTA_SAT
                        ),
                        hvac_mode=str(
                            source_cfg.get("hvac_mode") or const.DEFAULT_SOURCE_HVAC_MODE
                        ),
                        cooling_cop=_as_float(
                            source_cfg.get("cooling_cop"), const.DEFAULT_COOLING_COP
                        ),
                        cooling_efficiency=_as_float(
                            source_cfg.get("cooling_efficiency"),
                            const.DEFAULT_COOLING_EFFICIENCY,
                        ),
                        heating_efficiency=_as_float(
                            source_cfg.get("heating_efficiency"),
                            const.DEFAULT_EFFICIENCY,
                        ),
                    )
                )
            elif source_type == const.SOURCE_TYPE_GENERIC_THERMOSTAT:
                sources.append(
                    GenericThermostat(
                        **common,
                        max_temp_offset=_as_float(
                            source_cfg.get("max_temp_offset"),
                            const.DEFAULT_MAX_TEMP_OFFSET,
                        ),
                    )
                )
            else:
                sources.append(
                    ElectricHeater(
                        **common,
                        efficiency=_as_float(source_cfg.get("efficiency"), const.DEFAULT_EFFICIENCY),
                        max_temp_offset=_as_float(
                            source_cfg.get("max_temp_offset"),
                            const.DEFAULT_MAX_TEMP_OFFSET,
                        ),
                    )
                )
        except (TypeError, ValueError) as exc:
            _LOGGER.warning("Skipping invalid heat source %r: %s", source_cfg, exc)
        else:
            sources[-1].p_gain = p_gain
    return sources


def _source_output_tags(
    config: Mapping[str, Any],
    sources: list[HeatSource],
) -> dict[str, str]:
    tags: dict[str, str] = {}
    source_configs = {str(item.get("name")): item for item in _list_of_mappings(config.get("heat_sources"))}
    for source in sources:
        source_cfg = source_configs.get(source.name, {})
        tag = _first_string(
            source_cfg,
            ("output_tag", "control_tag", "tag", "mqtt_tag", "actuator_tag"),
        )
        tags[source.name] = tag or source.name
    return tags


def _room_output_tags(config: Mapping[str, Any]) -> dict[str, list[str]]:
    tags: dict[str, list[str]] = {}
    for room_cfg in _list_of_mappings(config.get("rooms")):
        name = room_cfg.get("name")
        if not isinstance(name, str) or not name:
            continue
        room_tags = _string_list(room_cfg.get("output_tags") or room_cfg.get("actuator_tags"))
        direct = _first_string(
            room_cfg,
            ("output_tag", "control_tag", "actuator_tag", "heater_tag"),
        )
        if direct:
            room_tags.append(direct)
        if room_tags:
            tags[name] = room_tags
    return tags


def _room_setpoint(config: Mapping[str, Any], room_name: str) -> float:
    for room in _list_of_mappings(config.get("rooms")):
        if room.get("name") == room_name:
            return _as_float(room.get("setpoint"), const.DEFAULT_SETPOINT)
    return const.DEFAULT_SETPOINT


def _proportional_fraction(temp: float, setpoint: float) -> float:
    error = float(setpoint) - float(temp)
    if error <= 0.0:
        return 0.0
    return _clamp(error / 3.0, 0.0, 1.0)


def _float_mapping(value: Any) -> dict[str, float | None]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): (None if raw is None else _as_float(raw, 0.0))
        for key, raw in value.items()
    }


def _list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _first_string(source: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _as_float(value: Any, default: float) -> float:
    if value is None or isinstance(value, bool):
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
