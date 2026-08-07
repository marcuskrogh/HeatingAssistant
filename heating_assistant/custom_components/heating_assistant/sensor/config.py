"""Controller configuration sensor for Heating Assistant."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..coordinator import HeatingAssistantCoordinator
from ..const import DOMAIN
from ..naming import slugify
from .base import _LiveValueSensorMixin

class ControllerConfigSensor(_LiveValueSensorMixin, CoordinatorEntity, SensorEntity):
    """Exposes current controller tuning and estimation parameters as attributes.

    The frontend reads this entity reactively via state subscription to populate
    the Controller Tuning and System Identification pages.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:tune-vertical"

    def __init__(self, coordinator: HeatingAssistantCoordinator) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._attr_name = "Heating Assistant \u2013 Controller Config"
        self._attr_unique_id = f"{DOMAIN}_controller_config"

    @property
    def available(self) -> bool:
        # Tuning parameters live on the coordinator object from __init__ and
        # are not produced by the periodic update cycle.  Always return True
        # so that hass.states always carries the tuning attributes even when
        # an MPC solve or data-fetch step fails and last_update_success is False.
        return True

    @property
    def native_value(self) -> str:
        return "active"

    @property
    def extra_state_attributes(self) -> dict:
        c = self._coordinator

        # Core tuning parameters — single source via coordinator snapshot.
        attrs: dict = dict(c.get_controller_config_snapshot())
        attrs.update({
            "sigma_w": c._sigma_w,
            "sigma_v": c._sigma_v,
            "sigma_b": c._sigma_b,
            "identification_horizon_hours": float(
                getattr(c, "_identification_horizon_hours", 6.0)
            ),
            # Heater power-scale factors — identified vs applied
            "identified_heater_scales": dict(
                getattr(c, "_last_identified_heater_scales", {})
            ),
            "current_heater_scales": {
                src.name: {
                    "power_scale": round(float(getattr(src, "power_scale", 1.0)), 4),
                    "room": getattr(src, "room", None),
                    "room_slug": slugify(getattr(src, "room", "") or ""),
                }
                for src in getattr(c, "heat_sources", [])
            },
        })

        # Schedule, setpoint and room data — requires model to be initialised.
        try:
            schedules = c.serialize_room_schedules()
            room_setpoints: dict = {}
            room_comfort_offsets: dict = {}
            # ``room_enabled`` reflects the user's manual on/off toggle, while
            # ``room_active`` is the *effective* state (user toggle AND no active
            # off-schedule).  The dashboard uses ``room_active`` to drive the
            # clear "OFF" state on the climate cards and ``room_enabled`` to
            # reflect the user's intent on the power toggle.
            room_enabled: dict = {}
            room_active: dict = {}
            for rn in c.model.room_names:
                room_setpoints[slugify(rn)] = c.model.rooms[rn].setpoint
                room_comfort_offsets[slugify(rn)] = c._room_comfort_offset.get(rn, 2.0)
                room_enabled[slugify(rn)] = bool(c._room_enabled.get(rn, True))
                room_active[slugify(rn)] = bool(c.is_room_enabled(rn))
            attrs["room_schedules"] = schedules
            attrs["room_setpoints"] = room_setpoints
            attrs["room_comfort_offsets"] = room_comfort_offsets
            attrs["room_enabled"] = room_enabled
            attrs["room_active"] = room_active
        except Exception:
            pass

        # Parameter estimation history from the persisted snapshot.
        try:
            snap = c.estimated_params_snapshot
            if snap and isinstance(snap, dict) and "history" in snap:
                attrs["parameter_history"] = snap["history"]
        except Exception:
            pass

        return attrs
