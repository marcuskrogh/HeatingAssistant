"""Home Assistant-like panel state assembly for HeatingRuntime."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from heatingassistant.app import sysid_services
from heatingassistant.app.runtime_const import _ELECTRICITY_PRICE_ENTITY


class HassStatesMixin:
    """Build the synthetic entity map Ingress reads via hass_states()."""

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

        update_interval = float(self._derived_update_interval())
        nmpc_period_s = float(self._nmpc_period_s())
        mean_error = self._mean_tracking_error()
        states["sensor.heating_assistant_mpc_performance"] = self._ha_state(
            "sensor.heating_assistant_mpc_performance",
            self._last_control_duration_s,
            {
                "last_run_ts": self._last_nmpc_ts,
                "dt_s": update_interval,
                "last_nmpc_ts": self._last_nmpc_ts,
                "nmpc_period_s": nmpc_period_s,
                "nmpc_computing": bool(self._nmpc_computing),
                "control_computing": bool(self._control_computing),
                "nmpc_result_ts": self._nmpc_result_ts,
                "last_control_ran_ts": self._last_control_ran_ts,
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
                current_state = states[entity_id].get("state")
                if current_state in {None, "", "unknown", "unavailable"}:
                    catalog_state = item.get("state")
                    if self._usable_catalog_value(catalog_state) is not None:
                        states[entity_id]["state"] = str(catalog_state)
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
