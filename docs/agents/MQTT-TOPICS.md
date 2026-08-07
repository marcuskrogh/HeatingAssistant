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
