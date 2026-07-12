"""Reusable coordinator and hass stubs aligned with the post-refactor package layout."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

from custom_components.heating_assistant.coordinator import HeatingAssistantCoordinator


def make_hass_stub(
    *,
    temp_states: Optional[Dict[str, str]] = None,
    config_dir: str = "/tmp/ha_test_config",
) -> SimpleNamespace:
    """Minimal Home Assistant stand-in with config dir and state reads."""
    hass = SimpleNamespace()
    hass.config = SimpleNamespace(config_dir=config_dir)
    hass.services = SimpleNamespace(
        async_call=AsyncMock(),
        async_register=MagicMock(),
        has_service=MagicMock(return_value=True),
    )
    hass.config_entries = SimpleNamespace(
        async_update_entry=MagicMock(),
        async_forward_entry_setups=AsyncMock(),
        async_unload_platforms=AsyncMock(return_value=True),
        async_reload=AsyncMock(),
        async_entries=MagicMock(return_value=[]),
        async_get_entry=MagicMock(return_value=None),
    )
    hass.http = SimpleNamespace(async_register_static_paths=AsyncMock())
    hass.async_add_executor_job = AsyncMock(
        side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs)
    )
    hass.async_create_task = MagicMock()

    states: Dict[str, str] = dict(temp_states or {})

    def _get_state(entity_id: str):
        if entity_id not in states:
            return None
        return SimpleNamespace(state=states[entity_id], attributes={})

    hass.states = SimpleNamespace(get=_get_state)
    return hass


def wire_room_enablement(
    coord: Any,
    *,
    enabled: Optional[Dict[str, bool]] = None,
) -> None:
    """Attach ``is_room_enabled`` matching coordinator enablement maps."""
    enabled = enabled or {}
    room_enabled = getattr(coord, "_room_enabled", None) or {}
    if not room_enabled and coord.model is not None:
        room_enabled = {name: True for name in coord.model.room_names}

    def _is_room_enabled(room_name: str) -> bool:
        if room_name in enabled:
            return enabled[room_name]
        if room_name in room_enabled:
            return bool(room_enabled[room_name])
        return True

    coord._room_enabled = {**room_enabled, **enabled}
    coord.is_room_enabled = _is_room_enabled


def make_minimal_coordinator(
    *,
    room_names: Optional[List[str]] = None,
    temp_sensors: Optional[Dict[str, List[str]]] = None,
    outdoor_entity: Optional[str] = None,
    weather_entity: Optional[str] = None,
    horizon: int = 6,
    dt: int = 900,
) -> HeatingAssistantCoordinator:
    """Skeleton coordinator with attributes required by mpc_cycle and sensors."""
    names = room_names or ["living_room"]
    temp_sensors = temp_sensors or {n: [f"sensor.{n}_temp"] for n in names}

    coord = object.__new__(HeatingAssistantCoordinator)
    coord.hass = make_hass_stub()
    coord._temp_sensors = temp_sensors
    coord._outdoor_entity = outdoor_entity
    coord._weather_entity = weather_entity
    coord._solar_radiation_entity = None
    coord._price_entity = None
    coord._horizon = horizon
    coord._update_interval_s = dt
    coord._latitude = 55.0
    coord._longitude = 12.0
    coord._ground_albedo = 0.2
    coord._tracking_weight = 1.0
    coord._energy_weight = 0.1
    coord._smoothing_weight = 0.05
    coord._soft_constraint_weight = 10.0
    coord._soft_constraint_linear_weight = 0.0
    coord._terminal_weight = 1.0
    coord._energy_price_weight = 0.0
    coord._sigma_w = 0.1
    coord._sigma_v = 0.5
    coord._sigma_b = 0.002
    coord._price_net_tariff = 0.0
    coord._price_spot_surcharge = 0.0
    coord._weather_warn_thresholds = {3, 10}
    coord.weather_consecutive_failures = 0
    coord.weather_last_error = None
    coord.weather_last_error_at = None
    coord.weather_last_success_at = None
    coord.solar_fc_consecutive_failures = 0
    coord.solar_fc_last_error = None
    coord.solar_fc_last_error_at = None
    coord.solar_fc_last_success_at = None
    coord._window_sensors = {}
    coord._window_state = {n: "closed" for n in names}
    coord._window_state_since = {}
    coord._window_open_debounce = 60.0
    coord._window_open_close_settle = 300.0
    coord._window_open_q_inflation = 100.0
    coord._rooms_ever_measured = set(names)
    coord.measured_temperatures = {}
    coord.filtered_temperatures = {}
    coord.solar_gains = {}
    coord.heat_flows = {}
    coord.cloud_cover = None
    coord._cloud_cover_filtered = None
    coord.ghi_now = None
    coord.outdoor_temp = None
    coord._last_valid_outdoor_temp = None
    coord._outdoor_temp_startup_failures = 0
    coord.predictions = []
    coord.linearised_predictions = []
    coord.outdoor_forecast = []
    coord.solar_forecast = []
    coord.heating_schedule = []
    coord.price_forecast = []
    coord.actions = {}
    coord.heat_sources = []
    coord._sources_by_room = {}
    coord._room_schedule = {}
    coord._base_setpoint = {}
    coord._effective_setpoint = {}
    coord._schedule_disabled = {}
    coord._schedule_enabled = {n: True for n in names}
    coord._room_comfort_offset = {n: 2.0 for n in names}
    coord._system_enabled = True
    coord.now_utc = datetime.now(tz=timezone.utc)
    coord.last_update_success = True

    rooms = {
        name: SimpleNamespace(
            temperature=20.0,
            setpoint=21.0,
            comfort_offset=2.0,
            windows=[],
            internal_gain=0.0,
        )
        for name in names
    }
    coord.model = SimpleNamespace(
        room_names=list(names),
        rooms=rooms,
        temperatures={name: rooms[name].temperature for name in names},
        compute_heat_flows=MagicMock(return_value={"total": 1.0}),
        _C=[5e6, 5e6],
        _A=[[0.0]],
        _B_ext=[0.0],
        _B_ground=[0.0],
        rebuild_derived_parameters=MagicMock(),
    )
    wire_room_enablement(coord)
    coord.sources_for_room = lambda r: [
        s for s in coord.heat_sources if getattr(s, "room", None) == r
    ]
    return coord


