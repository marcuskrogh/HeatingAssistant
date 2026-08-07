# Heating Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2023.1%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![Release](https://img.shields.io/github/v/release/marcuskrogh/HeatingAssistant?include_prereleases)](https://github.com/marcuskrogh/HeatingAssistant/releases)

Heating Assistant is a Home Assistant integration that replaces simple on/off
and PID thermostats with **model-predictive control (MPC)**. It builds a
physics-based, room-by-room model of your home, predicts how each room will
behave over the next several hours, and continuously computes the heater
settings that keep every room comfortable for the least energy.

It accounts for heat flowing between rooms and to the outdoors, solar gain
through your windows, and the weather forecast — so it pre-heats before you wake,
coasts on free solar gain, and avoids the overshoot-and-recover cycle of a
reactive thermostat. It drives a range of heaters — electric panels, hydronic
and oil radiators, electric and hydronic underfloor heating, gas heaters, and
air-source heat pumps — and runs entirely locally.

**Everything is configured through the Home Assistant UI** — there is no YAML to
edit. The integration adds its own **Heating Assistant** panel to the sidebar
for setup, monitoring and tuning.

<!-- Maintainer: add a screenshot of the Heating Assistant panel here, e.g.
     ![Heating Assistant panel](docs/images/overview.png) -->

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Setting up your first home](#setting-up-your-first-home)
- [Configuration](#configuration)
- [The Heating Assistant dashboard](#the-heating-assistant-dashboard)
- [Entities and services](#entities-and-services)
- [Tuning and accuracy](#tuning-and-accuracy)
- [Troubleshooting](#troubleshooting)
- [Documentation](#documentation)
- [Contributing](#contributing)

## Features

- **Room-by-room thermal model.** Each room is a two-node (2R2C) thermal
  circuit; heat flows between adjacent rooms and to the outdoors.
- **Predictive control.** A receding-horizon MPC plans hours ahead, so it
  pre-heats ahead of comfort periods and rides out the building's thermal lag
  instead of chasing it.
- **State estimation.** A continuous-discrete Extended Kalman Filter
  reconstructs the unmeasured wall temperature and smooths sensor noise.
- **Mixed heat sources.** A range of types — electric heaters, hydronic and oil
  radiators, electric and hydronic underfloor heating, gas heaters, generic
  thermostats, and air-source heat pumps (temperature-dependent COP, with
  cooling support) — can be combined in the same room. Each drives a `switch`,
  `number` or `climate` entity.
- **Solar gain.** Per-window solar modelling from your location and window
  geometry, optionally driven by a measured irradiance forecast.
- **Weather-aware.** An optional weather entity sharpens the outdoor-temperature
  forecast over the planning horizon.
- **Comfort schedules.** Per-room sleep, setback and away periods with automatic
  pre-heat and frost protection.
- **Self-tuning.** Built-in maximum-likelihood identification can estimate your
  rooms' thermal parameters from normal operating history.
- **Built-in dashboard.** A dedicated sidebar panel for setup, live monitoring,
  diagnostics and controller tuning — no Lovelace cards to assemble.
- **Local and private.** No cloud account or internet connection is required at
  runtime (`iot_class: local_push`).

## How it works

Every control cycle (15 minutes by default) the integration:

1. **Reads** each room's temperature and the outdoor temperature from your
   Home Assistant sensors.
2. **Estimates** the full thermal state — including the unmeasured wall mass —
   with a continuous-discrete EKF.
3. **Forecasts** the disturbances over the horizon: outdoor temperature (from a
   weather entity, or held constant) and per-window solar gain.
4. **Optimises** the heater settings by solving a model-predictive control
   problem, and applies the first step (receding horizon).
5. **Drives** your heater entities and publishes predicted-temperature and
   heating-plan sensors.

For the physics and the controller, see
[Physics, Models & Control Theory](docs/THEORY.md).

## Requirements

- Home Assistant **2023.1** or newer.
- One **room temperature sensor** per room you want to control (any HA
  `sensor.*` reporting °C).
- One or more **controllable heaters**, each already available in HA as a
  `switch.*`, `number.*`, or `climate.*` entity.
- An **outdoor temperature sensor** (recommended). A **weather entity**
  (e.g. Met.no) is optional but improves accuracy.

Python dependencies (`numpy`, `scipy`, `highspy`, and the `mbc` model-based
control package) are declared in `manifest.json` and installed automatically by
Home Assistant on first start. `mbc` is fetched from GitHub, so the host needs
outbound internet access the first time the integration loads.

## Installation

### Home Assistant OS App (SWD-255)

The HAOS App packaging skeleton is available as a custom Home Assistant App
repository for the SWD-255 transition work. Install the Mosquitto broker App
first (or provide an equivalent MQTT broker), then add this repository URL in
Home Assistant:

```text
https://github.com/marcuskrogh/HeatingAssistant
```

Install **HeatingAssistant** from the Apps store entry. The App exposes Ingress
and optional host port **8099**. On start it syncs its bundled
`custom_components/heating_assistant` integration into Home Assistant's config
share; restart Home Assistant Core after the sync notification so Core loads the
integration copy.

### HACS (recommended)

This integration is not yet in the default HACS store, so add it as a custom
repository:

1. In Home Assistant, open **HACS → Integrations**.
2. Open the **⋮** menu (top-right) → **Custom repositories**.
3. Add the repository URL `https://github.com/marcuskrogh/HeatingAssistant`
   and select the category **Integration**.
4. Find **Heating Assistant** in the list and click **Download**.
5. **Restart Home Assistant.**

### Manual

1. Download the latest release (or clone this repository).
2. Copy the `custom_components/heating_assistant` folder into your Home
   Assistant configuration directory so you end up with:

   ```text
   <config>/custom_components/heating_assistant/
   ```

   On Home Assistant OS the config directory is `/config`; on the Container
   image it is whatever you mounted as `/config`.
3. **Restart Home Assistant.**

After restarting (either method), continue with
[Setting up your first home](#setting-up-your-first-home).

## Setting up your first home

Heating Assistant is configured in the UI: the initial wizard collects
site-level settings, and the rest of the setup happens on the **Configuration**
page of the integration's own sidebar panel. The example below sets up a single
room with one heater; repeat the room/heater steps for the rest of your home.

### 1. Add the integration

1. Go to **Settings → Devices & services → + Add integration**.
2. Search for **Heating Assistant** and select it.
3. On the welcome screen, confirm or fill in:
   - **Location** — latitude and longitude (pre-filled from Home Assistant);
     used for the solar-gain model.
   - **Outdoor temperature sensor** — your outdoor `sensor.*` (recommended).
   - **Weather entity** *(optional)* — e.g. `weather.forecast_home`, for an
     outdoor-temperature forecast over the planning horizon.
   - **Control interval** — how often the controller re-plans (default
     **15 min**; leave as-is unless you have a reason to change it).
4. Submit. Home Assistant creates the integration and adds a **Heating
   Assistant** entry to the sidebar. Open it and go to the **Configuration**
   page for the rest of the setup.

### 2. Add a room

On the panel's **Configuration → Rooms** page, click **+ Add room** and fill in:

- **Room name** — e.g. *Living Room* (becomes the climate entity name).
- **Temperature sensor(s)** — the room's temperature `sensor.*` (add more than
  one to average them).
- **Thermal model** — pick a **room size** and **insulation/age** preset; these
  seed the room's thermal mass and resistance. You can override the numbers, or
  let the integration learn them later (see [Tuning and
  accuracy](#tuning-and-accuracy)).

Save the room. (The room's target temperature isn't set here — you set it on the
climate card in step 5.)

> **Tip — connected rooms.** If two rooms share a wall and noticeably affect
> each other, add an inter-room connection in the room editor so the model
> accounts for the heat exchange. This is optional; start without it.

### 3. Add a heat source

On **Configuration → Heat Sources**, click **+ Add heat source** and fill in:

- **Display name** — e.g. *Living Room Heater*.
- **Type** — the heater type: electric heater, hydronic or oil radiator,
  electric or hydronic underfloor heating, gas heater, generic thermostat, or
  heat pump.
- **Room** — the room it heats.
- **Maximum power** — the heater's rated output in watts.
- **Driven entity** — the entity that controls the device (a `switch`, `number`
  or `climate` entity; for a heat pump, pick its `climate` entity).
- For a heat pump, set the rated COP and reference temperature from its
  datasheet under the performance fields.

Save the heat source.

### 4. (Optional) Add windows

For accurate solar gain, open the room again under **Configuration → Rooms**,
expand **Solar gain → Windows**, and add each window with its **area** and
**orientation** (compass direction it faces). You can skip this and add windows
later.

### 5. Set the temperature and let it run

1. Set the room's target temperature — on the room tile/climate card in the
   **Heating Assistant** panel, or with a standard **Thermostat** card pointing
   at `climate.heating_assistant_<room>`.
2. Give the controller one full cycle (up to 15 minutes) to read the sensors,
   plan, and command your heater.
3. Use the panel's **Overview** and **Room detail** pages to watch the predicted
   temperature, the heating plan, and the live model fit.

That's a working single-room setup. Repeat steps 2–4 for each additional room,
then refine parameters with the [Tuning and accuracy](#tuning-and-accuracy)
guide once you have a day or two of history.

## Configuration

All configuration lives in the UI. The main surface is the **Configuration**
page of the Heating Assistant sidebar panel, which has sections for:

- **Rooms** — sensors, thermal-model presets/overrides, solar gain and windows,
  and inter-room connections.
- **Heat Sources** — type, room, power, the driven entity and performance.
- **Environment & Site** — outdoor, weather, solar-irradiance and price sensors,
  and the site location.
- **System Parameters** and **Display & Plots** — history retention and the
  panel's plot windows.

Comfort **schedules** and **controller tuning** have their own panel pages
(**Schedules** and **Tuning**). Site settings can also be edited from the
initial wizard via **Reconfigure** on the integration card, and a subset of
settings (general & sensor settings, rooms, windows, heat sources) is available
through **Settings → Devices & services → Heating Assistant → Configure**.
Changes take effect without restarting Home Assistant.

For a field-by-field reference of every room, window, heat-source and schedule
setting, see the **[Configuration Reference](docs/CONFIGURATION.md)**.

## The Heating Assistant dashboard

The integration ships its own web UI as a sidebar panel (**Heating Assistant**)
— this is the intended way to monitor and tune the system, so there are no
Lovelace cards to build by hand. Its pages are:

- **Overview** — comfort tiles per room, system power and energy, and a
  health summary.
- **Room detail** — predicted temperature with the comfort band, the planned
  heating power, disturbance forecasts, and model-fit diagnostics.
- **System identification** — run parameter estimation and review the model fit.
- **Tuning** — adjust the controller's behaviour.
- **Schedules** — per-room comfort/setback periods.
- **Configuration** — manage rooms, heat sources, sensors and site settings.

Every value shown in the panel is also available as a normal Home Assistant
entity (see below), so you can still build your own Lovelace dashboard from the
climate and sensor entities if you prefer. Optional, ready-made card recipes are
collected in [the dashboard & custom cards](docs/DASHBOARDS.md).

## Entities and services

For each room the integration creates a `climate.*` thermostat plus a family of
sensors — measured and Kalman-filtered temperature, setpoint and comfort-band
bounds, heating power, solar gain, heat loss, energy balance, and timestamped
forecast trajectories. System-wide sensors cover outdoor temperature, controller
performance and overall efficiency.

It also registers services for setup and diagnostics — simulate a room's
response, estimate thermal parameters, analyse model fit, run open-loop
validation, manage identification datasets, and update tuning at runtime.

- **[Entities & Sensor Reference](docs/ENTITIES.md)** — every entity and its
  attributes, and how controller output is dispatched to your heaters.
- **[Services Reference](docs/SERVICES.md)** — all setup, diagnostic and
  system-identification services.

## Tuning and accuracy

Good predictions need reasonable thermal parameters. You can let the built-in
maximum-likelihood identifier learn them from your normal operating history
(from the panel's identification page, or the `estimate_parameters_ml` service),
or set them yourself. If a room oscillates, a heat pump short-cycles, or a room
never quite reaches setpoint, the controller weights can be adjusted from
**Configure → Control behaviour** or the panel's tuning page.

See **[Parameter Estimation & Controller Tuning](docs/TUNING.md)** for the full
guide and a tuning cheat-sheet.

## Troubleshooting

**Heating Assistant doesn't appear in the Add integration list**
- Confirm `custom_components/heating_assistant/` exists in your config folder
  and contains `manifest.json`, then restart Home Assistant.
- Check **Settings → System → Logs** for import errors. The most common cause is
  a failed dependency install (`numpy`, `scipy`, `highspy`, or `mbc`) — on
  restricted hosts, pre-install them with `pip install numpy scipy highspy` and
  `pip install "mbc @ git+https://github.com/marcuskrogh/mbc.git"`.

**A room isn't being heated**
- Check the room's temperature sensor is reporting and isn't `unavailable`.
- Confirm the heat source's **heater entity** is correct and controllable, and
  that its domain is `switch`, `number`, or `climate`.
- If the room is already at or above setpoint the controller may correctly do
  nothing — raise the setpoint briefly to confirm the heater responds.

**The controller never heats**
- Verify the outdoor temperature sensor reports a plausible value; without one,
  a warm fallback can make the controller decide no heating is needed.

**Solar gain looks wrong (always zero, or too high)**
- Check the site latitude/longitude, and that each window's **orientation** is
  the compass direction it faces.

**A room oscillates, or a heat pump short-cycles**
- Increase the controller's smoothing and/or horizon; for heat pumps also
  increase the turn-off deadband. See the
  [tuning guide](docs/TUNING.md#145-mpc-regulator-tuning).

## Documentation

| Document | Contents |
|----------|----------|
| [Configuration Reference](docs/CONFIGURATION.md) | Every room, window, heat-source and schedule setting. |
| [Entities & Sensor Reference](docs/ENTITIES.md) | All entities, their attributes, and heater dispatch. |
| [Services Reference](docs/SERVICES.md) | Setup, diagnostic and system-identification services. |
| [Parameter Estimation & Tuning](docs/TUNING.md) | Estimating thermal parameters and tuning the controller. |
| [The dashboard & custom cards](docs/DASHBOARDS.md) | The built-in panel, plus optional Lovelace card recipes. |
| [Physics, Models & Control Theory](docs/THEORY.md) | The thermal model, solar pipeline, MPC and state estimator. |
| [Architecture & Developer Guide](docs/DEVELOPMENT.md) | File layout, data flow, tests and extension points. |
| [Roadmap](docs/ROADMAP.md) | Planned evolution of the control software. |

## Contributing

Issues and pull requests are welcome on
[GitHub](https://github.com/marcuskrogh/HeatingAssistant). For development setup,
the test suite, and architecture details, see the
[Architecture & Developer Guide](docs/DEVELOPMENT.md).

## License

See the [repository](https://github.com/marcuskrogh/HeatingAssistant) for
current licensing terms.
