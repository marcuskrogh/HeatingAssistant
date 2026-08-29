"""HA entity-id to MQTT tag wiring for HeatingRuntime."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from heatingassistant.engine import const


def bind_ha_entity(
    entity_id: Any,
    direction: str,
    preferred_tag: str,
    *,
    previous: dict[tuple[str, str], str],
    used_tags: set[str],
    bindings: list[dict[str, str]],
    entity_tag,
) -> str | None:
    """Reserve a unique MQTT tag for one HA entity binding."""

    if not isinstance(entity_id, str):
        return None
    eid = entity_id.strip()
    if not eid or "." not in eid:
        return None
    preferred = preferred_tag.strip() or entity_tag(eid)
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


class WiringMixin:
    """Keep Ingress entity IDs, MQTT tags, and bindings aligned."""

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
            return bind_ha_entity(
                entity_id,
                direction,
                preferred_tag,
                previous=previous,
                used_tags=used_tags,
                bindings=bindings,
                entity_tag=self._entity_tag,
            )

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
