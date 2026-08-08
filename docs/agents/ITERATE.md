# Iterate: Ingress entity picker cannot wire HA room sensors

## Prior work
- Task: SWD-266
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/550
- Spec context: docs/agents/PLAN-haos-app-mqtt.md (bindings via App UI)

## Problem
After Ingress UI boots (`API connected — 3 entities`), Configuration → Rooms →
Add temperature sensors only lists HeatingAssistant's own synthetic sensors
(`sensor.heating_assistant_*`). Real Home Assistant temperature entities are
missing, so rooms cannot be wired to the App.

Root cause:
1. Ingress uses `HeatingAssistantAppHassShim`, which only exposes App-synthesized
   states — not the full HA entity registry (data plane is MQTT-only by design).
2. Saving `temp_sensors` / `heater_entity` / outdoor entities did not auto-derive
   MQTT `bindings` + room `temp_tags` for the thin HA bridge.

## Acceptance criteria
1. Entity picker allows typing a HA entity ID (e.g. `sensor.living_room_temperature`)
   when the full HA state list is unavailable (Ingress/shim).
2. Saving room / heat-source / environment config derives MQTT bindings
   (`in` for sensors, `out` for actuators) and room `temp_tags` from configured
   entity IDs.
3. App publishes the bindings map so the thin HA integration bridges those entities.
4. Regression tests cover binding derivation from `temp_sensors` and free-text
   entity entry path.
5. Version bump so Supervisor offers Update.

## Out of scope
- Fetching the full HA entity registry into the App over the Supervisor API.
- Expanding thin-bridge climate write support beyond current switch/number.

## Work packages
1. Entity picker free-text entry + Ingress hint copy
2. Runtime `_apply_entity_wiring` (bindings + temp_tags from entity IDs)
3. Tests + version bump 2.0.6 + sync App package
4. PR / ship handoff

## Tracker
- Task: SWD-267
- Relates: SWD-266 / SWD-255
- Branch: `cursor/swd-267-ha-entity-wiring-5d31`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/551

## Shipped
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/551 (merged)
- Version: **2.0.6** — typed HA entity IDs in Ingress picker + auto MQTT bindings/temp_tags.

## Setup guide (operator)
1. Update the HeatingAssistant App to **2.0.6** and restart if Supervisor asks.
2. Ensure Mosquitto is running and the thin `heating_assistant` integration is
   loaded (Core restart after App sync if needed).
3. Open HeatingAssistant → Configuration → Rooms → edit a room.
4. Under **Temperature sensors**, click **+ Add**, type your HA entity ID
   (e.g. `sensor.living_room_temperature`), then **Use entity ID** (or press Enter).
5. Save the room. Repeat for outdoor temperature under Environment, and for
   heaters under Heat Sources.
6. The App publishes MQTT bindings; the thin integration bridges those entities
   into App tags. Multi-sensor rooms are averaged in the App.

## Next
Done
