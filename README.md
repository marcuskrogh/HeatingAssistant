# HeatingAssistant
A home assistant integration that configures controlled smart heating for your house, apartment, or room.

## Overview

**Heating Assistant** is a custom [Home Assistant](https://www.home-assistant.io/) integration that
implements a **model-based (MPC) control solution** for heating and cooling in your smart home.

Key features:
- **Room-by-room thermal model** – each room is represented as an RC lumped-parameter circuit with a thermal mass and a thermal resistance to the outside.  Heat flows between adjacent rooms through configurable inter-room thermal resistances.
- **Heat source models** – supports electric resistive heaters and air-source heat pumps (with Carnot-based temperature-dependent COP).
- **Solar heat gain** – disturbance model that computes the solar irradiance on each window based on its area, orientation, tilt, and the current time/location (clear-sky model).
- **Receding-horizon (MPC) controller** – each control cycle the controller predicts the house temperature evolution over a configurable horizon and selects the heater set-points that minimise a weighted sum of temperature tracking error and energy consumption.

---

## Architecture

```
custom_components/heating_assistant/
├── __init__.py          Integration entry-point; YAML schema; set-up / tear-down
├── manifest.json        HA integration metadata
├── const.py             Shared constants and configuration keys
├── config_flow.py       UI wizard for initial set-up and options
├── coordinator.py       DataUpdateCoordinator – wires model, controller and HA state
├── thermal_model.py     Lumped RC thermal model (rooms, connections, windows)
├── solar_model.py       Clear-sky solar irradiance and window heat-gain calculation
├── heat_sources.py      ElectricHeater and HeatPump models
├── controller.py        Receding-horizon MPC controller
├── climate.py           HA climate platform (one entity per room)
├── sensor.py            HA sensor platform (predicted temperature, heating power)
└── translations/
    └── en.json          English UI strings
```

---

## Installation

1. Copy the `custom_components/heating_assistant` directory into your Home Assistant
   `config/custom_components/` folder.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration** and search for
   **Heating Assistant**.
4. Follow the setup wizard (site location, outdoor temperature sensor, control time step).
5. Add the room and heat-source configuration to `configuration.yaml` (see below).
6. Restart Home Assistant again to pick up the YAML configuration.

---

## Configuration

Room geometry, inter-room connections, windows, and heat sources are configured in
`configuration.yaml`.  The integration setup wizard only asks for site-level settings
(latitude/longitude, outdoor sensor, time step).

### Minimal example

```yaml
heating_assistant:
  outdoor_temp_entity: sensor.outdoor_temperature   # HA entity providing outdoor °C

  rooms:
    - name: living_room
      thermal_mass: 8000000       # J/K  (heat capacity of room contents + structure)
      r_external: 0.04            # K/W  (thermal resistance to outdoor environment)
      setpoint: 21.0              # °C   (desired temperature)
      temp_sensor: sensor.living_room_temperature
      connections:
        - room: kitchen
          r_value: 0.2            # K/W  (thermal resistance between rooms)
      windows:
        - area: 3.0               # m²
          orientation: 180        # degrees clockwise from North  (180 = South)
          tilt: 90                # degrees from horizontal (90 = vertical)

    - name: kitchen
      thermal_mass: 4000000
      r_external: 0.06
      setpoint: 20.0
      temp_sensor: sensor.kitchen_temperature
      connections:
        - room: living_room
          r_value: 0.2

  heat_sources:
    - name: living_room_heater
      type: electric_heater
      room: living_room
      max_power: 2000             # W (rated electrical / thermal power)
      heater_entity: switch.living_room_heater

    - name: heat_pump
      type: heat_pump
      room: living_room
      max_power: 5000             # W (rated thermal output)
      cop_rated: 3.5              # COP at the reference outdoor temperature
      cop_temp_ref: 7.0           # °C reference outdoor temperature for cop_rated
      heater_entity: climate.living_room_heat_pump
```

### Full configuration reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `outdoor_temp_entity` | string | — | HA entity ID of the outdoor temperature sensor |
| `latitude` | float | HA setting | Site latitude in degrees (positive = North) |
| `longitude` | float | HA setting | Site longitude in degrees (positive = East) |
| `dt` | int | `900` | Control time step in seconds (60–3600) |
| `horizon` | int | `6` | MPC prediction horizon (number of `dt` steps) |

#### Room options

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `name` | ✔ | — | Unique room identifier |
| `thermal_mass` | | `5 000 000` J/K | Effective heat capacity of the room |
| `r_external` | | `0.05` K/W | Thermal resistance to outdoor air |
| `setpoint` | | `21.0` °C | Desired room temperature |
| `temp_sensor` | | — | HA sensor entity providing measured room temperature |
| `connections` | | `[]` | List of `{room, r_value}` inter-room connections |
| `windows` | | `[]` | List of `{area, orientation, tilt}` windows |

#### Heat source options

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `name` | ✔ | — | Unique source identifier |
| `type` | ✔ | — | `electric_heater` or `heat_pump` |
| `room` | ✔ | — | Name of the room this source heats |
| `max_power` | ✔ | — | Rated maximum thermal output [W] |
| `heater_entity` | | — | HA entity to control (switch/number/climate) |
| `efficiency` | | `1.0` | For electric heaters – fraction of electrical energy converted to heat |
| `cop_rated` | | `3.5` | Heat pump rated COP |
| `cop_temp_ref` | | `7.0` °C | Outdoor temperature at which `cop_rated` applies |

---

## Entities created

For each configured room the integration creates:

| Entity | Platform | Description |
|--------|----------|-------------|
| `climate.heating_assistant_<room>` | climate | Set-point control; current temperature; HVAC mode |
| `sensor.heating_assistant_<room>_predicted_temperature` | sensor | Model temperature [°C] |
| `sensor.heating_assistant_<room>_heating_power` | sensor | Total active heater output [W] |

---

## Thermal model

Each room _i_ obeys the lumped RC energy balance:

```
C_i · dT_i/dt = Q_heater_i
               + Σ_j  (T_j − T_i) / R_ij     ← inter-room heat flow
               + (T_outdoor − T_i) / R_i,ext  ← fabric heat loss / gain
               + Q_solar_i                     ← solar gain through windows
```

where
- **C_i** – thermal mass [J/K]
- **R_ij** – thermal resistance between rooms i and j [K/W]
- **R_i,ext** – thermal resistance to the outdoor environment [K/W]
- **Q_solar_i** – solar heat gain through the room's windows [W]

The model is integrated with an explicit (forward) Euler method at step `dt`.
For typical residential buildings a step of 15 minutes (900 s) gives accurate results.

### Solar heat gain model

Solar gains are computed using:
1. **Solar position** – declination, equation of time, hour angle → altitude and azimuth.
2. **Clear-sky DNI** – ASHRAE simple clear-sky direct normal irradiance estimate.
3. **Isotropic-sky diffuse** – Liu & Jordan model.
4. **Angle of incidence** – projection of beam radiation onto the tilted window surface.
5. **Solar heat gain coefficient (SHGC)** – default 0.6 (typical double glazing).

### Heat pump COP model

The heat pump COP is estimated from the rated COP and the Carnot efficiency ratio:

```
COP(T_outdoor) = COP_rated × COP_Carnot(T_outdoor) / COP_Carnot(T_ref)
```

where the Carnot COP is computed assuming a fixed supply temperature of 35 °C
(typical for low-temperature radiator systems or underfloor heating).

---

## Controller

The **receding-horizon MPC controller** runs at each `UPDATE_INTERVAL` (60 s by default):

1. Read current room temperatures from configured sensor entities.
2. Forecast disturbances (outdoor temperature: persistence; solar gains: solar model).
3. For each room, evaluate all combinations of discrete power levels
   `{0 %, 33 %, 67 %, 100 %}` across that room's heat sources over the prediction horizon.
4. Select the action that minimises the discounted cost:

   ```
   J = Σ_{k=0}^{N-1}  γ^k · [ (T_sp − T_k)²  +  λ · Σ_s u_s ]
   ```

   where γ = 0.9 (discount factor), λ = `energy_weight` (default 0.01), and the
   asymmetric tracking term applies a 50 % penalty when the room is above set-point
   (light cooling mode).

5. Apply only the first-step action (receding horizon).
6. Write set-points back to the configured heater entities.

---

## Development

### Running tests

```bash
pip install numpy homeassistant pytest
python -m pytest tests/ -v
```

### Project structure

See the [Architecture](#architecture) section above.

---

## Roadmap

- [ ] Weather-API outdoor temperature forecast (replaces persistence assumption)
- [ ] Comfort scheduling (day/night/away set-point profiles)
- [ ] Energy price optimisation (time-of-use tariffs)
- [ ] GUI room editor in the config flow
- [ ] Cooling mode support (reversible heat pump)
