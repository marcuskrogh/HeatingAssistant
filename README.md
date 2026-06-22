# Heating Assistant

> **A Home Assistant custom integration that brings model-predictive (MPC),
> room-by-room temperature control to your home.**

![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2023.1%2B-41BDF5?logo=home-assistant&logoColor=white)
![Version](https://img.shields.io/badge/version-1.0.1-blue)
![IoT class](https://img.shields.io/badge/IoT%20class-local%20push-success)

Heating Assistant replaces simple on/off or PID thermostats with a physics-based
predictive controller. It models your house room-by-room, accounts for heat
flowing between rooms and to the outdoors, factors in solar gain through each
window, and computes optimal heater set-points by looking ahead over a
configurable prediction horizon. The result is tighter temperature tracking and
lower energy use than reactive control — with automatic pre-heating before
comfort periods and graceful handling of heat pumps, electric heaters and
hydronic radiators.

Everything runs **locally** (`iot_class: local_push`); no cloud account is
required.

---

## Highlights

| | |
|---|---|
| 🏠 **Room-by-room model** | Each room is a two-node (2R2C) thermal circuit; heat flows between adjacent rooms and to the outdoors. |
| 🔮 **Receding-horizon MPC** | Each cycle solves a convex optimisation over the horizon to track setpoints while minimising energy and actuator wear. |
| 🌡️ **State estimation** | A continuous-discrete Extended Kalman Filter reconstructs the unmeasured wall temperature and filters sensor noise. |
| ☀️ **Solar gain** | Per-window solar modelling from your location and window geometry, optionally driven by a measured irradiance forecast. |
| ♨️ **Mixed heat sources** | Electric heaters, infrared panels, hydronic radiators and air-source heat pumps (temperature-dependent COP, cooling-aware). |
| 🌤️ **Weather forecasts** | Optional HA weather entity sharpens outdoor-temperature predictions over the horizon. |
| 😴 **Comfort schedules** | Per-room sleep / setback / away periods with automatic pre-heat and frost protection. |
| 📊 **Rich entities & dashboards** | Climate thermostats, measured/filtered/forecast sensors, an auto-generated Lovelace dashboard, and diagnostics. |
| 🧪 **Self-tuning** | Built-in maximum-likelihood identification estimates your thermal parameters from normal operating history. |

See the [full feature list and theory](docs/THEORY.md) for the mathematics
behind each of these.

---

## How it works

Every control cycle (default: every 15 minutes) the integration:

1. **Reads** room temperatures and the outdoor temperature from your HA sensors.
2. **Estimates** the full thermal state (including the unmeasured wall mass) with
   a continuous-discrete EKF.
3. **Forecasts** disturbances over the horizon — outdoor temperature (from a
   weather entity or persistence) and per-window solar gain.
4. **Optimises** the heater set-points by solving a model-predictive control
   problem, then applies only the first step (receding horizon).
5. **Dispatches** the result to your `switch`, `number` or `climate` heater
   entities and publishes predicted-temperature / heating-plan sensors.

For the physics, the controller formulation and the state estimator, read
[Physics, Models & Control Theory](docs/THEORY.md).

---

## Requirements

| Requirement | Minimum |
|-------------|---------|
| Home Assistant | 2023.1 |
| Python | 3.10 |
| `apexcharts-card` (HACS frontend card) | recommended — used by the dashboards |

Python dependencies (`numpy`, `scipy`, `highspy`, and the `mbc` model-based
control package) are declared in `manifest.json` and installed automatically by
Home Assistant on first load. The `mbc` package is fetched from GitHub, so the
HA host needs outbound internet access on first install. See
[Troubleshooting](#troubleshooting) if dependency installation fails.

---

## Installation

### Manual installation

1. Download or clone this repository.
2. Copy the integration into your Home Assistant config directory:

   ```bash
   cp -r custom_components/heating_assistant \
         /path/to/homeassistant/config/custom_components/heating_assistant
   ```

   On Home Assistant OS the config directory is `/config/`; on the Container
   image it is whatever you mounted as `/config`.
3. **Restart Home Assistant.**
4. Go to **Settings → Devices & Services → + Add Integration**, search for
   **Heating Assistant**, and complete the setup wizard (location, outdoor
   sensor, optional weather entity, control step, horizon).
5. Add your room/heat-source topology to `configuration.yaml` (see
   [Quick start](#quick-start) below).
6. **Restart Home Assistant again** to load the YAML topology.

### HACS installation

HACS support is planned. Once published, the integration will be installable
from the HACS *Integrations* store with a single click.

---

## Quick start

The UI wizard configures **site-level** settings; your **rooms and heaters** are
declared in `configuration.yaml`. A minimal two-room setup:

```yaml
heating_assistant:
  # Optional overrides (otherwise taken from the UI wizard):
  # outdoor_temp_entity: sensor.openweathermap_temperature
  # weather_entity: weather.forecast_home      # enables outdoor-temp forecasting

  rooms:
    - name: living_room                          # unique id; letters/digits/_ only
      thermal_mass: 8000000                      # J/K  (≈ 4000 × floor area m²)
      r_external: 0.04                           # K/W  (lower = better insulated)
      setpoint: 21.0                             # °C   default target
      temp_sensor: sensor.living_room_temperature

    - name: bedroom
      thermal_mass: 4000000
      r_external: 0.05
      setpoint: 19.0
      temp_sensor: sensor.bedroom_temperature
      connections:
        - room: living_room                      # shared wall
          r_value: 0.3

  heat_sources:
    - name: living_room_heater
      type: electric_heater
      room: living_room
      max_power: 2000                            # W
      heater_entity: switch.living_room_heater

    - name: bedroom_heater
      type: electric_heater
      room: bedroom
      max_power: 1000
      heater_entity: switch.bedroom_heater
```

Rules to remember:

- Every `name` under `rooms` and `heat_sources` must be unique.
- Each heat source's `room` must match a room `name` exactly.
- `heater_entity` must be a `switch.*`, `number.*` or `climate.*` entity.

**Rough starting parameters** (refine later — the integration can identify them
for you, see [Tuning](docs/TUNING.md)):

| Parameter | Starting point |
|-----------|----------------|
| `thermal_mass` | `4000 × floor_area_m²` J/K |
| `r_external` | `0.03` modern · `0.05` post-1980 · `0.10` older/poorly insulated |
| `r_value` (connection) | `0.1–0.2` open doorway · `0.2–0.5` closed door · `0.3–0.6` solid wall |

After restarting, set target temperatures on the `climate.heating_assistant_*`
entities (a Thermostat card, an automation, or `climate.set_temperature`). Give
the controller one full cycle (up to 15 min) to act.

➡️ Full field reference and richer examples (heat pumps, multi-sensor rooms,
comfort schedules, solar windows): **[Configuration Reference &
Examples](docs/CONFIGURATION.md)**.

---

## Entities, services & dashboards

For each room the integration creates a `climate.*` thermostat plus a family of
sensors — measured and Kalman-filtered temperature, setpoint and comfort-corridor
bounds, heating power, solar gain, heat loss, energy balance, and timestamped
forecast trajectories for charting. System-wide sensors cover outdoor
temperature, the MPC performance, and overall efficiency.

- **[Entities & Sensor Reference](docs/ENTITIES.md)** — every entity, its
  attributes, and how controller output is dispatched to your heaters.
- **[Services Reference](docs/SERVICES.md)** — setup, diagnostic and
  system-identification services (simulate response, estimate parameters,
  analyse model fit, run open-loop validation, manage datasets, and more).
- **[Dashboards & Lovelace Cards](docs/DASHBOARDS.md)** — the auto-generated
  dashboard plus hand-built MPC chart recipes.

The integration writes a starter Lovelace dashboard to
`<config>/dashboards/heating_assistant.yaml` automatically on first setup (the
`apexcharts-card` frontend card is required for its charts).

---

## Tuning & accuracy

Good predictions need reasonable parameters. You can estimate them empirically,
or let the built-in maximum-likelihood identifier learn `thermal_mass`,
`r_external`, internal gains, solar scale and more from your normal operating
history. If the temperature oscillates, short-cycles a heat pump, or never quite
reaches setpoint, the regulator weights can be adjusted.

➡️ **[Parameter Estimation & Controller Tuning](docs/TUNING.md)** — empirical and
ML estimation, a tuning cheat-sheet, and how the estimated parameters persist.

---

## Troubleshooting

**Integration does not appear in the Add Integration search**
- Confirm `custom_components/heating_assistant/` exists in your HA config folder
  and contains `manifest.json`.
- Check **Settings → System → Logs** for import errors. The most common cause is
  a dependency-install failure (`numpy`, `scipy`, `highspy`, or `mbc`). On
  restricted hosts, pre-install them: `pip install numpy scipy highspy` and
  `pip install "mbc @ git+https://github.com/marcuskrogh/mbc.git"`.

**Rooms show no entities after adding the integration**
- Room and heat-source topology comes from `configuration.yaml`, not the wizard.
  Add a `heating_assistant:` block with at least one room and restart HA.
- Check the log for YAML validation errors (indentation, missing keys, or a
  `room` reference that doesn't match any room `name`).

**Heater entities are not being controlled**
- Use the exact HA entity ID (not the friendly name); the domain must be
  `switch`, `number`, or `climate`; the entity must not be `unavailable`.

**Controller always outputs 0 (no heating)**
- Verify `outdoor_temp_entity` returns a plausible value. Temporarily raise a
  room `setpoint` to confirm heaters respond.

**Solar gain is always zero or implausibly high**
- Check `latitude`/`longitude`, and remember `orientation` is degrees clockwise
  from **North** (0 = N, 90 = E, 180 = S, 270 = W).

**Temperature oscillates, or a heat pump short-cycles**
- Increase `smoothing_weight` and/or `horizon`; for heat pumps also increase
  `turn_off_deadband`. See the [tuning guide](docs/TUNING.md#145-mpc-regulator-tuning).

---

## Documentation

| Document | What's inside |
|----------|---------------|
| [Physics, Models & Control Theory](docs/THEORY.md) | The thermal model, solar pipeline, heat-source models, MPC formulation and the CD-EKF state estimator. |
| [Configuration Reference & Examples](docs/CONFIGURATION.md) | Every YAML key, plus worked examples from a studio to a five-room house. |
| [Entities & Sensor Reference](docs/ENTITIES.md) | All entities, their attributes, and heater dispatch. |
| [Services Reference](docs/SERVICES.md) | Setup, diagnostic and system-identification services. |
| [Dashboards & Lovelace Cards](docs/DASHBOARDS.md) | Auto-generated dashboard and custom card recipes. |
| [Parameter Estimation & Tuning](docs/TUNING.md) | Estimating thermal parameters and tuning the controller. |
| [Architecture & Developer Guide](docs/DEVELOPMENT.md) | File layout, data flow, tests, benchmarks, extension points. |
| [Roadmap](docs/ROADMAP.md) | Planned evolution of the control software. |
| [`MODEL_FIT_GUIDE.md`](MODEL_FIT_GUIDE.md) | Diagnostic sensors and live model-fit dashboard cards. |
| [`BENCHMARKS.md`](BENCHMARKS.md) | Latest performance benchmark results. |

---

## Contributing

Issues and pull requests are welcome on
[GitHub](https://github.com/marcuskrogh/HeatingAssistant). For development setup,
running the test suite, and architecture details, see the
[Architecture & Developer Guide](docs/DEVELOPMENT.md).

## License

See the [repository](https://github.com/marcuskrogh/HeatingAssistant) for
current licensing terms.
