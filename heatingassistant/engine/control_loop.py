"""App-facing control loop wrapper for the HA-independent engine."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import logging
import threading
from typing import Any

from . import const
from .heat_sources import ElectricHeater, GenericThermostat, HeatPump, HeatSource
from .thermal_model import HouseModel, Room, RoomConnection, Window

_LOGGER = logging.getLogger(__name__)

# MPC tuning keys accepted by Controller Tuning preview (exclude window detection).
_PREVIEW_TUNING_KEYS = frozenset(
    {
        const.CONF_COMFORT_OFFSET,
        const.CONF_TRACKING_WEIGHT,
        const.CONF_ENERGY_WEIGHT,
        const.CONF_ENERGY_PRICE_WEIGHT,
        const.CONF_SMOOTHING_WEIGHT,
        const.CONF_SOFT_CONSTRAINT_WEIGHT,
        const.CONF_SOFT_CONSTRAINT_LINEAR_WEIGHT,
        const.CONF_TERMINAL_WEIGHT,
        const.CONF_UPDATE_INTERVAL,
        const.CONF_HORIZON,
    }
)


def _snapshot_from_controller(
    controller: Any,
    *,
    dt: float,
    horizon: int,
    compute_ts: datetime | None = None,
) -> dict[str, Any]:
    """Build a forecast snapshot dict from a one-off controller solve."""

    filtered: dict[str, float] = {}
    raw_filtered = getattr(controller, "filtered_temperatures", None) or {}
    if isinstance(raw_filtered, Mapping):
        for key, value in raw_filtered.items():
            try:
                filtered[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
    return {
        "mode": "mpc",
        "compute_ts": compute_ts or datetime.now(timezone.utc),
        "predictions": [dict(item) for item in list(getattr(controller, "predictions", []) or [])],
        "linearised_predictions": [
            dict(item)
            for item in list(getattr(controller, "linearised_predictions", []) or [])
        ],
        "heating_schedule": [
            dict(item) for item in list(getattr(controller, "heating_schedule", []) or [])
        ],
        "outdoor_forecast": [
            float(value) for value in list(getattr(controller, "outdoor_forecast", []) or [])
        ],
        "solar_forecast": [
            dict(item) for item in list(getattr(controller, "solar_forecast", []) or [])
        ],
        "price_forecast": [
            float(value) for value in list(getattr(controller, "price_forecast", []) or [])
        ],
        "filtered_temperatures": filtered,
        "dt": float(dt),
        "horizon": int(horizon),
    }


class ControlEngine:
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
        self.update_config(config or {})

    def update_config(self, config: Mapping[str, Any]) -> None:
        """Rebuild model/controller state from an App config dictionary."""

        self.config = dict(config)
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
    ) -> dict[str, float]:
        """Return MQTT output tag values for the current room temperatures."""

        setpoints = dict(setpoints or {})
        outdoor = _as_float(outdoor_temp, 0.0)
        self._apply_measurements(room_temps, setpoints)

        if self._controller is not None and self.heat_sources:
            try:
                # Do not force solar_gains=0 — let the controller compute
                # geometric / GHI-driven gains (SWD-278).
                actions = self._controller.compute(
                    outdoor,
                    solar_gains=None,
                    now=datetime.now(timezone.utc),
                    outdoor_forecast=outdoor_forecast,
                    cloud_forecast=cloud_forecast,
                    cloud_cover_now=cloud_cover_now,
                    ghi_forecast=ghi_forecast,
                    ghi_now=ghi_now,
                    price_forecast=price_forecast,
                )
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
                "dt": float(self.config.get("update_interval", const.DEFAULT_UPDATE_INTERVAL)),
                "horizon": int(self.config.get("horizon", const.DEFAULT_HORIZON)),
            }

    def preview_tuning_forecast(
        self,
        overrides: Mapping[str, Any] | None,
        room_temps: Mapping[str, float | None],
        outdoor_temp: float,
        setpoints: Mapping[str, float] | None = None,
        *,
        outdoor_forecast: list[float] | None = None,
        cloud_forecast: list[float] | None = None,
        cloud_cover_now: float | None = None,
        ghi_forecast: list[float | None] | None = None,
        ghi_now: float | None = None,
        price_forecast: list[float] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Run a one-off MPC solve with proposed tuning parameters.

        Does not mutate the live forecast caches used by room views. Room
        temperatures / setpoints on the shared model are updated to the supplied
        measurements (same as a normal compute). ``comfort_offset`` overrides are
        applied temporarily and restored afterwards.
        """

        ov = {
            key: value
            for key, value in dict(overrides or {}).items()
            if key in _PREVIEW_TUNING_KEYS and value is not None
        }
        preview_dt = float(
            ov.get(
                const.CONF_UPDATE_INTERVAL,
                self.config.get("update_interval", const.DEFAULT_UPDATE_INTERVAL),
            )
        )
        preview_horizon = int(
            ov.get(const.CONF_HORIZON, self.config.get("horizon", const.DEFAULT_HORIZON))
        )

        comfort_override = ov.get(const.CONF_COMFORT_OFFSET)
        saved_comfort: dict[str, float] = {}
        if comfort_override is not None:
            preview_comfort = float(comfort_override)
            for name, room in self.model.rooms.items():
                saved_comfort[name] = float(
                    getattr(room, "comfort_offset", const.DEFAULT_COMFORT_OFFSET)
                )
                room.comfort_offset = preview_comfort

        try:
            try:
                preview_ctrl = self._build_controller_from_config(
                    {**self.config, **ov},
                    horizon=preview_horizon,
                    dt=preview_dt,
                )
            except Exception as exc:
                _LOGGER.warning(
                    "preview_tuning_forecast: controller build failed: %s", exc
                )
                return {"error": "controller_unavailable"}
            if preview_ctrl is None:
                return {"error": "controller_unavailable"}

            if self._controller is not None:
                try:
                    x_hat, P = self._controller.ekf_state
                    preview_ctrl.restore_ekf_state(x_hat, P)
                except Exception:  # pragma: no cover - defensive
                    _LOGGER.debug(
                        "preview_tuning_forecast: could not copy EKF state",
                        exc_info=True,
                    )

            self._apply_measurements(room_temps, dict(setpoints or {}))
            compute_now = now or datetime.now(timezone.utc)
            preview_ctrl.compute(
                outdoor_temp,
                solar_gains=None,
                now=compute_now,
                outdoor_forecast=outdoor_forecast,
                cloud_forecast=cloud_forecast,
                cloud_cover_now=cloud_cover_now,
                ghi_forecast=ghi_forecast,
                ghi_now=ghi_now,
                price_forecast=price_forecast,
                run_optimization=True,
            )
            return _snapshot_from_controller(
                preview_ctrl,
                dt=preview_dt,
                horizon=preview_horizon,
                compute_ts=compute_now,
            )
        finally:
            for name, value in saved_comfort.items():
                room = self.model.rooms.get(name)
                if room is not None:
                    room.comfort_offset = value

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
        predictions = [dict(item) for item in list(getattr(controller, "predictions", []) or [])]
        linearised = [
            dict(item) for item in list(getattr(controller, "linearised_predictions", []) or [])
        ]
        heating_schedule = [
            dict(item) for item in list(getattr(controller, "heating_schedule", []) or [])
        ]
        outdoor_forecast = [
            float(value) for value in list(getattr(controller, "outdoor_forecast", []) or [])
        ]
        solar_forecast = [
            dict(item) for item in list(getattr(controller, "solar_forecast", []) or [])
        ]
        price_forecast = [
            float(value) for value in list(getattr(controller, "price_forecast", []) or [])
        ]
        filtered: dict[str, float] = {}
        raw_filtered = getattr(controller, "filtered_temperatures", None) or {}
        if isinstance(raw_filtered, Mapping):
            for key, value in raw_filtered.items():
                try:
                    filtered[str(key)] = float(value)
                except (TypeError, ValueError):
                    continue
        with self._forecast_lock:
            self._last_predictions = predictions
            self._last_linearised_predictions = linearised
            self._last_heating_schedule = heating_schedule
            self._last_outdoor_forecast = outdoor_forecast
            self._last_solar_forecast = solar_forecast
            self._last_price_forecast = price_forecast
            self._last_filtered_temperatures = filtered
            self._last_compute_ts = datetime.now(timezone.utc)

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

    def _try_build_controller(self) -> Any:
        if not self.heat_sources or not self.model.rooms:
            self.mode = "proportional"
            self.fallback_reason = "no heat sources or rooms configured"
            return None

        try:
            controller = self._build_controller_from_config(self.config)
        except Exception as exc:  # pragma: no cover - exercised when optional deps differ
            self.mode = "proportional"
            self.fallback_reason = f"controller unavailable: {exc}"
            _LOGGER.info("HeatingAssistant engine using fallback control: %s", exc)
            return None

        if controller is None:
            self.mode = "proportional"
            self.fallback_reason = "no heat sources or rooms configured"
            return None

        self.mode = "mpc"
        self.fallback_reason = None
        return controller

    def _build_controller_from_config(
        self,
        config: Mapping[str, Any],
        *,
        horizon: int | None = None,
        dt: float | None = None,
    ) -> Any:
        """Construct an MPC controller from a config mapping (may raise)."""

        if not self.heat_sources or not self.model.rooms:
            return None

        from .controller.factory import (  # noqa: PLC0415
            ControllerBuildConfig,
            build_mpc_controller,
        )

        preview_dt = float(
            dt
            if dt is not None
            else config.get("update_interval", const.DEFAULT_UPDATE_INTERVAL)
        )
        build_config = ControllerBuildConfig(
            model=self.model,
            heat_sources=self.heat_sources,
            horizon=int(
                horizon
                if horizon is not None
                else config.get("horizon", const.DEFAULT_HORIZON)
            ),
            dt=preview_dt,
            measurement_dt=float(
                config.get("measurement_dt", preview_dt)
            ),
            latitude=float(config.get("latitude", 0.0)),
            longitude=float(config.get("longitude", 0.0)),
            tracking_weight=float(
                config.get("tracking_weight", const.DEFAULT_TRACKING_WEIGHT)
            ),
            energy_weight=float(
                config.get("energy_weight", const.DEFAULT_ENERGY_WEIGHT)
            ),
            smoothing_weight=float(
                config.get("smoothing_weight", const.DEFAULT_SMOOTHING_WEIGHT)
            ),
            soft_constraint_weight=float(
                config.get(
                    "soft_constraint_weight",
                    const.DEFAULT_SOFT_CONSTRAINT_WEIGHT,
                )
            ),
            soft_constraint_linear_weight=float(
                config.get(
                    "soft_constraint_linear_weight",
                    const.DEFAULT_SOFT_CONSTRAINT_LINEAR_WEIGHT,
                )
            ),
            terminal_weight=float(
                config.get("terminal_weight", const.DEFAULT_TERMINAL_WEIGHT)
            ),
            sigma_w=float(config.get("sigma_w", const.DEFAULT_SIGMA_W)),
            sigma_v=float(config.get("sigma_v", const.DEFAULT_SIGMA_V)),
            sigma_b=float(config.get("sigma_b", const.DEFAULT_SIGMA_B)),
            energy_price_weight=float(
                config.get(
                    "energy_price_weight",
                    const.DEFAULT_ENERGY_PRICE_WEIGHT,
                )
            ),
            albedo=float(config.get("ground_albedo", const.DEFAULT_GROUND_ALBEDO)),
        )
        return build_mpc_controller(build_config)

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
