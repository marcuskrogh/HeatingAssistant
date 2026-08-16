# Heating Assistant MQTT Topics

Topic root: `heatingassistant`

All tag payloads use QoS 1 and JSON:

```json
{"value": 21.5, "status": "GOOD", "reason": null, "ts": 1700000000.0}
```

`status` is one of `GOOD`, `BAD`, or `UNCERTAIN`. The App averages only numeric
`GOOD` values; `BAD`, `UNCERTAIN`, and `null` values are ignored.

| Topic | Retained | Publisher | Consumer | Purpose |
|---|---:|---|---|---|
| `heatingassistant/{instance_id}/tag/{tag}/in` | No | HA thin bridge | App | HA entity state telemetry for a bound tag. |
| `heatingassistant/{instance_id}/tag/{tag}/out` | No | App | HA thin bridge | App command/value for a bound HA entity. |
| `heatingassistant/{instance_id}/cmd/{name}` | No | App or operator | App | Instance command channel such as reload or Core restart. |
| `heatingassistant/{instance_id}/cmd/core_restart` | No | HA MQTT Update | App | Settings **Restart required** Install → Supervisor Core restart. |
| `heatingassistant/{instance_id}/restart/state` | Yes | App | HA MQTT Update | Installed vs disk versions while a Core restart is pending. |
| `homeassistant/update/heatingassistant_restart/config` | Yes | App | HA MQTT discovery | Restart required card on the Settings updates list. |
| `heatingassistant/{instance_id}/status` | Yes | App | HA bridge/operator | Runtime health and current room summary. |
| `heatingassistant/{instance_id}/bindings` | Yes | App | HA thin bridge | Binding map for HA entity/tag bridge setup. |
| `heatingassistant/{instance_id}/entities` | Yes | HA thin bridge | App | Searchable HA entity catalog for Ingress pickers. |

Bindings are retained JSON with this shape:

```json
{
  "bindings": [
    {"tag": "living_temp_1", "entity_id": "sensor.living_temp_1", "direction": "in"},
    {"tag": "living_setpoint", "entity_id": "number.living_setpoint", "direction": "out"}
  ]
}
```

Direction `in` means HA publishes entity state to the App. Direction `out` means
the thin bridge subscribes to App tag output and writes to the HA entity. The
thin bridge supports sensor reads, `switch.turn_on`/`switch.turn_off`,
`number.set_value`, and climate writes via App payloads shaped as
`{"hvac_mode": "cool"|"heat"|"heat_cool"|"off", "temperature": 21.5}`
(SWD-280). Internal App actuator state remains an MPC fraction in `[-1, 1]`;
domain-specific HA commands are derived at publish time. Climate heaters also
auto-bind an inbound `{output_tag}_state` tag so the App receives
`current_temperature` / `hvac_modes` for setpoint anchoring.

When you configure rooms / heat sources / environment sensors in the Ingress UI
with Home Assistant entity IDs (`temp_sensors`, `heater_entity`,
`outdoor_temp_entity`, …), the App auto-derives the bindings map and matching
`temp_tags` / `output_tag` / `outdoor_temp_tag` values, then publishes the
retained bindings topic so the thin integration starts bridging those entities.

The thin HA bridge also publishes a retained **entity catalog** on
`heatingassistant/{instance_id}/entities` (picker domains: `sensor`, `weather`,
`binary_sensor`, `switch`, `climate`, `number`, `input_boolean`) so Ingress
entity selectors can search by friendly name or entity ID. Manual entity-ID
entry remains available as a fallback when MQTT has not delivered the catalog
yet.
