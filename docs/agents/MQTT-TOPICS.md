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
| `heatingassistant/{instance_id}/cmd/{name}` | No | App or operator | App | Instance command channel such as reload. |
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
initial thin bridge supports sensor reads, `switch.turn_on`/`switch.turn_off`,
`number.set_value`, and basic climate setpoint/HVAC mode writes.

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
