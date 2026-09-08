# Architecture: Overview health from current sensors only

## Shape
- Lives: `HeatingRuntime` quality maps in `heatingassistant/app/runtime.py`
  plus inbound leftover drop in `heatingassistant/app/runtime_wiring.py`.
  Aggregation stays in `heatingassistant/app/system_health.py`.
- Depends on: current room/environment options (`temp_tags`, `window_tags`,
  outdoor/weather/solar/price tags) and the HA entity catalog / live tag values.
- Seams: `_configured_sensor_tags`, `_refresh_configured_sensor_quality`,
  `_configured_tag_is_usable` — unit-tested via `HeatingRuntime.system_health`
  with `InMemoryMqttBus` (same pattern as SWD-385).
- Will not add: a health store, new MQTT topics, Overview UI cards, or a
  second quality enum.

## Neighbourhood
- Opened modules/boundaries: tag quality persistence, entity wiring leftovers,
  `evaluate_system_health` sensors module (payload only).
- Major refinement: none. Config-derived tag set replaces “all inbound
  bindings plus leftovers” as the health membership rule.

## Tracker
- Task: SWD-510
- Branch: swd-510-sensor-health-config-cycle

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/668
