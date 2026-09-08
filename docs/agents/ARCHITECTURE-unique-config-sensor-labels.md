# Architecture: Unique config sensor labels

## Shape
- Lives: `heatingassistant/app/static/js/config/config-ui.js` (chip/picker
  label) and `custom_components/heating_assistant/bridge_manager.py` (Ingress
  catalog `name` so merged `friendly_name` is already unique).
- Depends on: Home Assistant `hass.states` plus optional `hass.entities` /
  `hass.devices` (panel); entity + device registry (thin bridge). No new
  services or persisted fields.
- Seams: pure `combineDeviceEntityName` / `combine_device_entity_name` plus
  `entityFriendlyName` / `entity_catalog_display_name` — tests pass a fake
  hass / registries without booting HA or the panel.
- Will not add: new config schema, new MQTT topics, new UI widgets, or a
  parallel entity-picker component.

## Neighbourhood
- Opened modules/boundaries: config entity selector (chips + modal picker);
  thin-bridge catalog publish used by Ingress `hass_states()` merge.
- Major refinement (or none): none — same helper, richer label source.

## Tracker
- Task: SWD-516
- Branch: swd-516-unique-config-sensor-labels

## Next
`/implement SWD-516` — Build to this shape
