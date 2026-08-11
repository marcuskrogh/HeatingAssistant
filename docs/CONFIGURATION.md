# Configuration

Heating Assistant is configured in the **Ingress panel**. There is no YAML to
edit for rooms, heaters, or schedules. The thin Home Assistant integration only
asks for an **App instance ID** so it can bridge your HA entities over MQTT.

For a first install walkthrough, see the [main README](../README.md). For
parameter guidance, see [Tuning](TUNING.md).

## Surfaces

| Surface | What it configures |
|---------|-------------------|
| Panel → **Configuration** | Rooms, heat sources, environment, system parameters, display |
| Panel → **Schedules** | Comfort / setback periods |
| Panel → **Tuning** | Controller weights and horizons |
| Panel → **Parameter estimation** | ML identification and apply |
| HA → Add integration | App instance ID only (must match App options) |

Changes in the panel take effect without restarting Home Assistant Core.

## Environment

| Setting | Notes |
|---------|-------|
| Outdoor temperature sensor | Recommended HA `sensor.*` (°C) |
| Weather entity | Optional; improves outdoor forecast over the horizon |
| Electricity price sensor | Optional; used for price-aware plots / planning inputs |
| Site latitude / longitude | Used by the solar model (App defaults until set) |
| Control interval | Default **900 s** (15 min) |

## Rooms

| Setting | Notes |
|---------|-------|
| Name | Display name; also used for room slug / climate id in the panel |
| Temperature sensor(s) | One or more HA sensors; multiple are averaged |
| Thermal presets | Room size + construction / insulation seed mass and resistance |
| Thermal mass / `r_external` | Optional numeric overrides |
| Windows | Area + orientation (compass facing) for solar gain |
| Window / door sensors | Optional; open-window override turns heating down after debounce |
| Inter-room connections | Optional shared-wall heat exchange |

Target temperature and comfort band are set on the room / Overview climate
controls, not in the room editor.

## Heat sources

Supported types include electric heaters, electric / hydronic underfloor,
hydronic and oil radiators, gas heaters, generic thermostats, air- and
ground-source heat pumps, pellet stoves, oil boilers, and electric storage
heaters.

| Setting | Notes |
|---------|-------|
| Display name | Label in the panel |
| Type | Heater model |
| Room | Room this source heats |
| Maximum power | Rated output [W] |
| Driven entity | HA `switch`, `number`, or `climate` entity |
| Heat-pump fields | Datasheet power / COP (and cooling when enabled) |

## Schedules

Per-room periods for comfort, setback, sleep, and away. The controller
pre-heats into comfort periods. Frost protection applies when configured.

## System parameters and display

| Area | Examples |
|------|----------|
| System parameters | History retention, control-related defaults |
| Display & plots | Plot windows used by the panel charts |

## Related

- [Tuning](TUNING.md) — thermal parameters and MPC weights
- [Theory](THEORY.md) — models behind the settings
- [Development](DEVELOPMENT.md) — App vs thin bridge architecture
