# Heating Assistant

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant%20OS-App-41BDF5.svg)](https://www.home-assistant.io/)
[![Release](https://img.shields.io/github/v/release/marcuskrogh/HeatingAssistant?include_prereleases)](https://github.com/marcuskrogh/HeatingAssistant/releases)

Heating Assistant is a **Home Assistant OS App** that replaces on/off and PID
thermostats with **model-predictive control (MPC)**. It builds a physics-based,
room-by-room model of your home, plans hours ahead, and drives your heaters for
comfort at the lowest energy use — entirely locally.

Configuration and monitoring happen in the App’s **Ingress panel**. A thin
Home Assistant integration only bridges your existing sensors and heater
entities over MQTT.

## Features

- Room-by-room thermal model with heat exchange between rooms
- Receding-horizon MPC with continuous-discrete state estimation
- Mixed heat sources (electric, hydronic, oil, gas, underfloor, heat pumps, and more)
- Solar gain and optional weather-aware outdoor forecasts
- Comfort schedules with pre-heat and frost protection
- Built-in parameter estimation and controller tuning
- Local and private (`iot_class: local_push`)

## Requirements

- **Home Assistant OS** (Supervisor / Apps)
- **Mosquitto** broker App (or equivalent MQTT broker)
- One room temperature sensor per controlled room
- One or more controllable heaters already in HA (`switch`, `number`, or `climate`)
- Outdoor temperature sensor (recommended); weather entity (optional)

## Installation

1. Install the **Mosquitto** broker App (if you do not already have an MQTT broker).
2. In Home Assistant, add this GitHub repository as a custom App repository:

   ```text
   https://github.com/marcuskrogh/HeatingAssistant
   ```

3. Install **HeatingAssistant** from the Apps store and start it.
4. After the App syncs the thin integration, **Settings** shows a
   **Restart required** repair (separate from the Updates list). Open it and
   restart Home Assistant Core.
5. Go to **Settings → Devices & services → Add integration**, search for
   **Heating Assistant**, and set the **App instance ID** to match the App
   options (default: `default`).
6. Open **HeatingAssistant** from the sidebar (Ingress).

The App listens on Ingress and optional host port **8100**.

## First setup

All setup is in the Ingress panel (**Configuration**):

1. **Environment** — outdoor temperature, optional weather and electricity price sensors.
2. **Rooms** — name, temperature sensor(s), thermal presets; optional windows and door/window sensors.
3. **Heat sources** — type, room, rated power, and the HA entity that drives the device.
4. Set each room’s target temperature on the Overview / room view.
5. Press **START** in the panel nav so the controller can run.

Give the controller one control cycle (default **15 minutes**) to plan and actuate.

## Panel

| Page | Purpose |
|------|---------|
| **Overview** | Room comfort tiles, system strip, controller KPIs |
| **Room detail** | Predictions, heating plan, disturbances, model fit |
| **Schedules** | Comfort / setback periods |
| **Tuning** | Controller behaviour |
| **Parameter estimation** | Learn thermal parameters from history |
| **System status** | MQTT / API health, bindings, MPC ops |
| **Configuration** | Rooms, heat sources, environment, display |

## Documentation

| Document | Contents |
|----------|----------|
| [Configuration](../docs/CONFIGURATION.md) | Panel settings reference |
| [Tuning](../docs/TUNING.md) | Parameter estimation and MPC tuning |
| [Theory](../docs/THEORY.md) | Thermal model, solar, MPC, estimator |
| [Development](../docs/DEVELOPMENT.md) | Architecture, versioning, tests |

## Troubleshooting

**App panel empty or MQTT disconnected**
- Confirm Mosquitto is running and the App has `mqtt:need` credentials.
- Confirm the thin integration is loaded after a Core restart and the instance ID matches.

**Room not heating**
- Check the room sensor and heater entity are available in HA.
- Confirm **START** is active (`system_enabled`).
- Raise the setpoint briefly to verify the heater responds.

**Solar gain looks wrong**
- Check window area and orientation (compass direction the window faces).
- Site latitude/longitude default to `0.0` in the App until set in Configuration;
  set them when accurate solar modelling matters.
- Confirm a **weather** entity is set under Environment so cloud cover can
  scale the clear-sky model. Optional solar-irradiance sensors are not
  required; they replace the cloud-scaled model only when configured.
- Sudden weather or irradiance steps are low-pass filtered (default 30 min;
  Environment → Solar model) so the Solar Gain plot does not jump in one
  sample. Set the time constant to 0 to disable.

## Contributing

Pull requests are welcome on
[GitHub](https://github.com/marcuskrogh/HeatingAssistant). See
[Development](../docs/DEVELOPMENT.md) for layout, tests, and packaging.

## License

MIT — see [LICENSE](../LICENSE).
