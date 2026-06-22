# Configuration Reference & Examples

> The complete YAML configuration reference for Heating Assistant, followed by
> full worked examples ranging from a single-room studio to a five-room house
> with comfort schedules.

All room, window, heat-source and schedule configuration is declared in
`configuration.yaml` under the `heating_assistant:` key. Site-level settings
(location, control step, horizon) are set through the UI setup wizard — see the
[main README](../README.md#quick-start). For guidance on choosing thermal
parameter values, see the [Parameter Estimation & Tuning guide](TUNING.md).

**Contents**

- [10. Configuration Reference](#10-configuration-reference)
- [11. Complete Configuration Examples](#11-complete-configuration-examples)

---

## 10. Configuration Reference

All room, window, and heat-source configuration is declared in `configuration.yaml` under the `heating_assistant:` key.

```yaml
heating_assistant:
  outdoor_temp_entity: ...
  weather_entity: ...
  latitude: ...
  longitude: ...
  update_interval: ...
  horizon: ...
  rooms:
    - ...
  heat_sources:
    - ...
```

### 10.1 Top-level keys

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `outdoor_temp_entity` | string | No | — | Entity ID of an outdoor temperature sensor.  Overrides the value set in the UI wizard. |
| `weather_entity` | string | No | — | Entity ID of a HA weather entity (e.g. `weather.forecast_home`) providing temperature forecasts.  When set, the controller uses the weather forecast for outdoor temperature predictions over the MPC horizon instead of assuming the current value is constant.  Works with any HA weather integration that exposes a `forecast` attribute (Met.no, OpenWeatherMap, AccuWeather, etc.). |
| `latitude` | float | No | HA / wizard setting | Site latitude [°].  Overrides the wizard value. |
| `longitude` | float | No | HA / wizard setting | Site longitude [°].  Overrides the wizard value. |
| `ground_albedo` | float | No | `0.2` | Site-level ground reflectance for the ground-reflected solar component (§3.4 Step 5).  Grass/soil ≈ 0.2; raise toward 0.7–0.8 for sites with persistent winter snow cover. |
| `update_interval` | int | No | `900` | Control time step [s]: sets the OCP ZOH duration, the EKF measurement step, and how often the coordinator re-solves.  Range 60–3600. |
| `horizon` | int | No | `100` | MPC prediction horizon [steps].  Range 1–200. |
| `energy_weight` | float | No | `0.01` | Weight on the input cost ‖**u**‖² in the MPC objective.  Higher values make the controller more conservative about running heaters, reducing overshoot at the expense of slightly slower heating.  Typical range: `0.001`–`0.5`.  See [Section 14.5](TUNING.md#145-mpc-regulator-tuning). |
| `smoothing_weight` | float | No | `0.1` | Weight on the input rate-of-change cost ‖Δ**u**‖² in the MPC objective.  Higher values strongly penalise rapid changes in heater output between consecutive time steps, dampening oscillations and reducing actuator wear.  Set to `0.0` to disable.  Typical range: `0.0`–`2.0`.  See [Section 14.5](TUNING.md#145-mpc-regulator-tuning). |
| `constraint_offset` | float | No | `2.0` | Symmetric soft output constraint band [°C] around the setpoint: the controller keeps predicted room temperatures within `[setpoint − δ, setpoint + δ]`.  Violations are penalised but not forbidden.  Decrease for tighter tracking; increase if the solver reports infeasibility. |
| `terminal_weight` | float | No | `100.0` | Terminal cost multiplier λ: **P** = λ × **Q**.  A large value forces the predicted trajectory to converge to the setpoint by the end of the horizon, dramatically improving steady-state tracking.  Increase to 200–500 if the controller still crosses or misses the setpoint; decrease toward 10–20 if you prefer softer convergence with more energy-aware shaping over the horizon.  Must be ≥ 1. |
| `mpc_solver` | string | No | `cvxopt` | QP solver backend for the linearized MPC.  The default uses CVXOPT for the batch convex QP. |
| `mpc_analytic_derivatives` | bool | No | `true` | Enables analytical-derivative plumbing when supported by the installed `mbc` backend. Unsupported hooks automatically fall back to numerical derivatives. |
| `sigma_w` | float | No | `0.1` | EKF process-noise standard deviation [K/√s]. Increase when the thermal model is too “stiff” and does not adapt quickly enough to disturbances. UI/YAML range: `1e-6`–`10.0`. |
| `sigma_v` | float | No | `0.5` | EKF measurement-noise standard deviation [K]. Increase when room sensors are noisy/spiky; decrease when sensors are stable and you want tighter measurement tracking. UI/YAML range: `1e-6`–`10.0`. |
| `sigma_b` | float | No | `0.002` | EKF offset-state process-noise standard deviation [K/√s] for the integrated model-mismatch state. Increase to let the offset term adapt faster to persistent bias. UI/YAML range: `1e-8`–`1.0`. |
| `window_open_debounce` | int | No | `60` | Time [s] a configured room window/door sensor must stay `on` before the room enters window-open override. |
| `window_open_close_settle` | int | No | `30` | Time [s] all configured room window/door sensors must stay `off` before leaving window-open override. |
| `window_open_q_inflation` | float | No | `10.0` | Covariance multiplier applied to EKF process noise for rooms currently in window-open override. Must be ≥ 1.0. |
| `rooms` | list | No | `[]` | List of room definitions (see below). |
| `heat_sources` | list | No | `[]` | List of heat source definitions (see below). |

### 10.2 Room block (`rooms`)

Each entry in the `rooms` list fully describes one room.

```yaml
rooms:
  - name: living_room           # required – unique identifier
    thermal_mass: 8000000       # J/K  – optional
    r_external: 0.04            # K/W  – optional
    setpoint: 21.0              # °C   – optional
    temp_sensor: sensor.living_room_temperature  # optional – single sensor
    # OR use a list of sensors whose readings are averaged:
    # temp_sensors:
    #   - sensor.living_room_temp_north
    #   - sensor.living_room_temp_south
    connections:                # optional
      - ...
    windows:                    # optional
      - ...
```

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `name` | string | **Yes** | — | Unique identifier for the room.  Used to match heat sources, connections, and HA entity IDs.  Use only letters, digits, and underscores (no spaces). |
| `thermal_mass` | float | No | `5 000 000` | Effective heat capacity of the room [J/K].  Includes air mass, furniture, interior walls, and a fraction of the exterior walls.  See [Section 14.1](TUNING.md#141-thermal-mass-thermal_mass) for guidance. |
| `r_external` | float | No | `0.05` | Thermal resistance from the room to the outdoor environment [K/W].  Represents the sum of all paths to the outside: exterior walls, roof, ground, and infiltration.  See [Section 14.2](TUNING.md#142-external-thermal-resistance-r_external) for guidance. |
| `setpoint` | float | No | `21.0` | Initial desired temperature [°C].  Can be overridden at runtime by the `climate.*` entity. |
| `comfort_corridor_low` | float | No | `setpoint - constraint_offset` | Lower comfort bound [°C] used by the MPC soft-corridor objective. |
| `comfort_corridor_high` | float | No | `setpoint + constraint_offset` | Upper comfort bound [°C] used by the MPC soft-corridor objective. |
| `temp_sensor` | string | No | — | Entity ID of a single HA sensor that measures the actual room temperature.  If provided, this value is used to correct the model state at each update cycle.  Without a sensor, the model runs in open-loop (simulation-only) mode.  Cannot be combined with `temp_sensors`. |
| `temp_sensors` | list of strings | No | — | List of HA sensor entity IDs for the room.  The coordinator reads all of them at each update cycle and uses their **arithmetic mean** as the measured room temperature.  Useful when the room is large or has significant temperature gradients.  Cannot be combined with `temp_sensor`. |
| `window_sensors` | list of strings | No | `[]` | Optional list of `binary_sensor.*` entity IDs (windows/doors). The room enters override `open` when any listed sensor stays `on` for `window_open_debounce`; while open, room heat-source commands are clamped to zero and the EKF process-noise covariance is inflated. |
| `connections` | list | No | `[]` | List of thermal connections to adjacent rooms. |
| `windows` | list | No | `[]` | List of window definitions for solar gain calculation. |
| `c_air_fraction` | float | No | `0.05` | Share of `thermal_mass` attributed to the fast **air node** of the 2R2C model (§3.1).  The remainder is the wall/mass node.  Refined per room by the parameter estimator when its heat sources show enough excitation; leave at the default otherwise. |
| `r_aw_fraction` | float | No | `0.05` | Share of the conductive envelope path attributed to the internal air↔wall resistance $R_{aw}$ (§3.1).  Also refined by the estimator when identifiable. |
| `solar_scale` | float | No | `1.0` | Multiplicative correction on the room's modelled solar gain.  Normally you never set this by hand — the parameter estimator identifies it from data (shading, curtains, preset error all land here).  Persisted with the other estimated parameters. |
| `schedule` | list | No | `[]` | Optional comfort schedule — a list of time-of-day periods that override the room's setpoint or switch its heat sources off (sleep / setback / away).  See [Section 10.6](#106-comfort-schedule-block-schedule). |

### 10.3 Connection block (`connections`)

Each connection represents a wall, door, floor or ceiling shared with another room.

```yaml
connections:
  - room: kitchen       # required – must match the name of another room
    r_value: 0.2        # K/W – required
```

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `room` | string | **Yes** | — | The `name` of the adjacent room.  Connections are directional in the YAML but the thermal model treats them symmetrically — you do NOT need to repeat the entry in both rooms.  (The matrix is built correctly even if only one side is declared, but declaring both sides is also harmless.) |
| `r_value` | float | **Yes** | — | Thermal resistance between the two rooms [K/W].  See [Section 14.3](TUNING.md#143-inter-room-thermal-resistance-r_value) for guidance. |

### 10.4 Window block (`windows`)

Each window (or glazed door) is a separate entry.

```yaml
windows:
  - area: 3.0           # m²  – required
    orientation: 180    # degrees – required (clockwise from North: 0=N, 90=E, 180=S, 270=W)
    tilt: 90            # degrees – optional (default 90 = vertical)
```

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `area` | float | **Yes** | — | Total glazed area of the window [m²].  For double windows, sum both panes. |
| `orientation` | float | **Yes** | — | Direction the window faces, in degrees **clockwise from North**.  0° = North, 90° = East, 180° = South, 270° = West.  A south-facing window in the Northern hemisphere receives the most direct solar gain in winter. |
| `tilt` | float | No | `90.0` | Angle of the window surface from the horizontal [°].  90° = vertical wall window (most common).  0° = horizontal skylight.  Roof windows are typically 30°–60°. |

### 10.5 Heat source block (`heat_sources`)

Each entry describes one controllable heating device.

```yaml
heat_sources:
  - name: living_room_heater    # required – unique identifier
    type: electric_heater       # required – "electric_heater" or "heat_pump"
    room: living_room           # required – must match a room name
    max_power: 2000             # W – required
    heater_entity: switch.living_room_heater  # optional
    efficiency: 1.0             # optional (electric_heater only)
```

```yaml
heat_sources:
  - name: heat_pump
    type: heat_pump
    room: living_room
    max_power: 5000             # W thermal – required
    heater_entity: climate.living_room_heat_pump  # optional
    cop_rated: 3.5              # optional (heat_pump only)
    cop_temp_ref: 7.0           # °C – optional (heat_pump only)
    min_power: 800              # W thermal – optional (heat_pump only)
    max_temp_offset: 5.0        # °C – optional (heat_pump only)
    turn_off_deadband: 1.0      # °C – optional (heat_pump only)
    cooling_cop: 2.5            # rated EER for cooling – optional (heat_pump only)
    cooling_efficiency: 1.0     # 0–1, fraction of cooling capacity actually used – optional
```

**Common keys (all types)**

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `name` | string | **Yes** | — | Unique identifier for this heat source.  Must be unique across all heat sources. |
| `type` | string | **Yes** | — | Source type.  Must be `electric_heater` or `heat_pump`. |
| `room` | string | **Yes** | — | Name of the room this source heats.  Must match a room `name`. |
| `max_power` | float | **Yes** | — | Maximum **thermal** output power [W].  For an electric heater this equals the rated electrical input.  For a heat pump this is the rated thermal output at `cop_temp_ref` conditions. |
| `heater_entity` | string | No | — | HA entity ID to control.  Supported domains: `switch`, `number`, `climate`.  If omitted, the controller computes the optimal action but does not issue any HA service call. |

**Electric heater additional keys**

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `efficiency` | float | No | `1.0` | Fraction of electrical energy converted to useful room heat.  Must be in (0, 1].  For a purely resistive panel heater use 1.0.  For an infrared heater aimed partly at an exterior window you may reduce this slightly. |

**Heat pump additional keys**

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `cop_rated` | float | No | `3.5` | Coefficient of Performance at the reference outdoor temperature.  Check your heat pump's datasheet for the value at the EN 14511 test point (usually A7/W35, i.e. 7 °C outdoor, 35 °C supply). |
| `cop_temp_ref` | float | No | `7.0` | Outdoor temperature [°C] at which `cop_rated` was measured.  Default matches the EN 14511 A7/W35 test condition. |
| `min_power` | float | No | `0.0` | Minimum thermal output [W] below which the heat pump shuts off entirely.  Real inverter-driven heat pumps have a lower modulation limit (often 20–30 % of rated capacity); if the optimal control signal would produce a positive output below this threshold the integration forces the unit off instead.  Set this to your unit's minimum continuous output to prevent short-cycling. |
| `max_temp_offset` | float | No | `5.0` | Maximum temperature offset [°C] added to the heat pump's internal temperature at full power.  When the heat pump is controlled via a `climate.*` entity, the integration sets `target = T_internal + fraction × max_temp_offset`.  Larger values give the heat pump a bigger temperature gap to ramp up against; smaller values limit maximum output.  The default of 5 °C works well for most underfloor and radiator systems. |
| `turn_off_deadband` | float | No | `1.0` | Half-width [°C] of the hysteresis dead-band around the setpoint.  The heat pump switches to passive cooling (`dry` / `fan_only`) only when `room_temp > setpoint + turn_off_deadband`, and exits cooling only when `room_temp < setpoint − turn_off_deadband`.  Within the dead-band (width = 2 × this value) the current mode is held, preventing toggling from small temperature fluctuations.  The compressor never cycles fully off during normal operation — when not in cooling mode it idles in heat mode.  Increase this value if you observe nuisance mode switching near the setpoint. |
| `cooling_cop` | float | No | `2.5` | Rated cooling COP / EER used to compute cooling capacity in dry / fan-only / cool mode.  The heat-removal capacity is `(max_power / cop_rated) × cooling_cop`, i.e. it scales with the **electrical** input rather than the heating thermal max.  Typical air-source heat pumps: 2.5–3.5.  Look up the value at the EN 14511 cooling test point (A35/W18 or A35/W7). |
| `cooling_efficiency` | float | No | `1.0` | Fraction (0–1) of the rated cooling capacity actually delivered when the integration switches the heat pump to cooling.  Use values around 0.3–0.5 if you rely on `dry` (dehumidify) mode for gentle cooling, or leave at 1.0 if the device runs at full cooling capacity. |

### 10.6 Comfort schedule block (`schedule`)

Each room may declare a `schedule` list of named time-of-day periods.  Use this to lower the setpoint when nobody is home (setback) or to switch off the heat source entirely while you sleep, with the controller automatically warming the room back up before you wake.  A period is matched purely on the local clock — no presence sensor, no automation glue required.

```yaml
schedule:
  - name: night
    start: "22:00"
    end: "04:00"
    mode: off                # turn the room's heat sources off
    frost_protection: 12.0   # never let it drop below this (°C)
  - name: workday_eco
    start: "08:30"
    end: "16:00"
    days: [mon, tue, wed, thu, fri]
    setpoint: 18.0           # lower setpoint while at work
```

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `name` | string | No | `period_<n>` | Friendly label used in diagnostics and notifications. |
| `start` | string | **Yes** | — | Local start time as `HH:MM` (24-hour) — inclusive. |
| `end` | string | **Yes** | — | Local end time as `HH:MM` (24-hour) — exclusive.  When `end` is earlier than (or equal to) `start` the period **wraps past midnight** (e.g. `22:00` → `04:00` covers the night). |
| `days` | list of strings | No | every day | Optional weekday filter using short names: `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun`.  For wrapping periods the filter is applied to the **start** day, so a `sun` period from `23:00` → `08:00` covers Monday morning too. |
| `mode` | string | No | `comfort` | `comfort` keeps the heat sources running and tracks the period's setpoint.  `off` turns the room's heat sources off for the duration of the period. |
| `setpoint` | float | No | room base setpoint | Setpoint in °C while a `comfort` period is active.  Ignored when `mode: off`. |
| `frost_protection` | float | No | `12.0` | Safety floor in °C enforced while a `mode: off` period is active.  When the measured room temperature drops to this value the heat is briefly re-enabled to defend the floor — pipes never freeze, even on the coldest nights. |

**How it works**

Every coordinator update tick the integration evaluates each room's schedule:

1. The first matching period (in declaration order) wins, so list more specific rules (e.g. a workday-only eco period) before broader ones (e.g. an everyday night period).
2. For `comfort` periods, the room's live setpoint becomes the period's `setpoint` (or the room's base setpoint if omitted).  The MPC tracks the new reference like any user-initiated change.
3. For `off` periods, the room is marked disabled and any configured heaters are commanded off — exactly the same path used when you toggle the climate entity to OFF in the UI.  Frost protection re-enables heating only if the measurement drops to the configured floor.
4. When no period matches, the room reverts to its **base setpoint** — the value last set via the climate entity (or the `setpoint` field in the room block).

| Scenario | MPC horizon reference used for optimisation | Heater execution |
|---|---|---|
| Current or future `comfort` period with setpoint/offset/weights override | Uses the period's schedule-projected setpoint, comfort corridor and Q/R multipliers per step. | Normal MPC actuation. |
| Future `off` period | **No new off-target is introduced**; controller carries forward the last comfort reference through off steps (no anticipatory preheat/cool toward an off window). | Source outputs are forced to 0 during off execution, except frost-protection floor recovery. |
| Schedule suspended via `set_schedule_enabled: false` | Uses current effective values as a flat trajectory (schedule projection bypassed). | Room follows normal enabled/manual logic without schedule-driven disable. |

**Preheat is automatic**

Because the controller is an MPC with a prediction horizon, it sees upcoming setpoint changes and starts heating **before** the next comfort period begins.  How early depends on the horizon (`horizon` × `update_interval`).  At the default settings (6 steps × 15 min = 1.5 h of look-ahead) the room is typically warm by the time the schedule transitions.  For longer preheat windows, increase `horizon` and/or shorten `update_interval`.

If you need the room ready earlier than the horizon allows, declare an explicit "preheat" period that ends at the comfort time:

```yaml
schedule:
  - name: night
    start: "22:00"
    end: "05:00"
    mode: off
  - name: morning_preheat
    start: "05:00"
    end: "07:00"
    setpoint: 21.0          # treat preheat as the comfort target
```

**Manual overrides**

Users keep full control of the schedule:

* Adjusting the setpoint via the `climate.*` entity updates the room's **base setpoint** — the value used outside any active period.  During an active period the change still takes effect but is overwritten when the schedule re-evaluates on the next tick.
* The `heating_assistant.set_schedule_enabled` service suspends or resumes the schedule for a single room (or every room) at runtime.  Use it for one-off exceptions like staying up late, working from home, or hosting guests.  Schedule state is in-memory and resets to enabled on Home Assistant restart.

```yaml
# Skip tonight's "off" period for the bedroom (e.g. an unwell child)
service: heating_assistant.set_schedule_enabled
data:
  room_name: bedroom
  enabled: false

# Resume scheduling for the whole house
service: heating_assistant.set_schedule_enabled
data:
  enabled: true
```

---

## 11. Complete Configuration Examples

### 11.1 Studio apartment – single room, one electric heater

A single-room installation with one window and a direct plug-in electric heater controlled via a smart plug (switch entity).

```yaml
heating_assistant:
  outdoor_temp_entity: sensor.openweathermap_temperature
  weather_entity: weather.forecast_home         # optional: enables weather-based outdoor temp forecast

  rooms:
    - name: studio
      thermal_mass: 3000000   # ~3 MJ/K for a small furnished room
      r_external: 0.08        # K/W – older building, poor insulation
      setpoint: 21.0
      temp_sensor: sensor.studio_thermometer
      windows:
        - area: 1.5
          orientation: 90     # East-facing window
          tilt: 90

  heat_sources:
    - name: studio_heater
      type: electric_heater
      room: studio
      max_power: 1500
      heater_entity: switch.studio_smart_plug
```

### 11.2 Two-bedroom flat – rooms with heat pump and supplemental heater

A two-bedroom apartment with an open-plan living/kitchen area and one separate bedroom.  The living area has a wall-mounted heat pump (exposed as a `climate.*` entity in HA) and a backup electric panel heater on a smart switch.  The bedroom has a small electric heater only.  The two rooms are connected through a doorway.

```yaml
heating_assistant:
  outdoor_temp_entity: sensor.netatmo_outdoor_temperature
  update_interval: 900  # 15-minute control step (OCP ZOH = EKF step = coordinator period)
  horizon: 8            # 2-hour lookahead

  rooms:
    - name: living_kitchen
      thermal_mass: 10000000  # large open-plan area
      r_external: 0.03        # modern well-insulated building
      setpoint: 21.0
      temp_sensor: sensor.living_room_temperature
      connections:
        - room: bedroom
          r_value: 0.3        # interior wall + door
      windows:
        - area: 4.0
          orientation: 180    # large south-facing window
          tilt: 90
        - area: 1.2
          orientation: 90     # east kitchen window
          tilt: 90

    - name: bedroom
      thermal_mass: 5000000
      r_external: 0.05
      setpoint: 19.0          # slightly cooler setpoint for sleeping
      temp_sensor: sensor.bedroom_temperature
      connections:
        - room: living_kitchen
          r_value: 0.3
      windows:
        - area: 1.8
          orientation: 270    # west-facing bedroom window
          tilt: 90

  heat_sources:
    - name: hp_living
      type: heat_pump
      room: living_kitchen
      max_power: 4500         # 4.5 kW thermal at A7/W35
      cop_rated: 4.0
      cop_temp_ref: 7.0
      min_power: 900          # unit cannot modulate below 20 % of rated capacity
      max_temp_offset: 5.0    # °C offset at full power
      turn_off_deadband: 1.0  # °C above setpoint before switching to cooling mode
      heater_entity: climate.mitsubishi_hp

    - name: backup_heater_living
      type: electric_heater
      room: living_kitchen
      max_power: 1500
      heater_entity: switch.living_backup_heater

    - name: bedroom_heater
      type: electric_heater
      room: bedroom
      max_power: 1000
      heater_entity: switch.bedroom_heater
```

### 11.3 Full house – five rooms, heat pump, and solar-facing windows

A detached house with a central hallway connecting all other rooms, a ground-floor living room and kitchen, and two upstairs bedrooms.  The heat pump serves the main living space; each bedroom has a panel heater.

```yaml
heating_assistant:
  outdoor_temp_entity: sensor.weather_station_outdoor
  latitude: 55.68    # Copenhagen
  longitude: 12.57
  update_interval: 900
  horizon: 6

  rooms:
    - name: hallway
      thermal_mass: 2000000
      r_external: 0.1        # mostly interior walls, small external area
      setpoint: 18.0
      temp_sensor: sensor.hallway_temp
      connections:
        - room: living_room
          r_value: 0.25
        - room: kitchen
          r_value: 0.4
        - room: bedroom_1
          r_value: 0.3
        - room: bedroom_2
          r_value: 0.3

    - name: living_room
      thermal_mass: 9000000
      r_external: 0.03
      setpoint: 21.0
      temp_sensor: sensor.living_temp
      connections:
        - room: hallway
          r_value: 0.25
        - room: kitchen
          r_value: 0.2       # open archway
      windows:
        - area: 5.0
          orientation: 180   # south-facing bay window
          tilt: 90
        - area: 1.0
          orientation: 270   # west patio door
          tilt: 90

    - name: kitchen
      thermal_mass: 5000000
      r_external: 0.05
      setpoint: 20.0
      temp_sensor: sensor.kitchen_temp
      connections:
        - room: hallway
          r_value: 0.4
        - room: living_room
          r_value: 0.2
      windows:
        - area: 1.5
          orientation: 90    # east-facing kitchen window

    - name: bedroom_1
      thermal_mass: 4000000
      r_external: 0.04
      setpoint: 19.0
      temp_sensor: sensor.bedroom1_temp
      connections:
        - room: hallway
          r_value: 0.3
      windows:
        - area: 2.0
          orientation: 180   # south bedroom window
          tilt: 90

    - name: bedroom_2
      thermal_mass: 3500000
      r_external: 0.045
      setpoint: 19.0
      temp_sensor: sensor.bedroom2_temp
      connections:
        - room: hallway
          r_value: 0.3
      windows:
        - area: 1.5
          orientation: 0     # north bedroom window – little solar gain
          tilt: 90

  heat_sources:
    - name: main_heat_pump
      type: heat_pump
      room: living_room
      max_power: 7000
      cop_rated: 3.8
      cop_temp_ref: 7.0
      min_power: 1400         # ~20 % of rated – prevents short-cycling
      max_temp_offset: 5.0    # °C offset at full power
      turn_off_deadband: 1.0  # °C above setpoint before switching to cooling mode
      heater_entity: climate.daikin_hp

    - name: bedroom1_heater
      type: electric_heater
      room: bedroom_1
      max_power: 1000
      heater_entity: switch.bedroom1_heater

    - name: bedroom2_heater
      type: electric_heater
      room: bedroom_2
      max_power: 800
      heater_entity: switch.bedroom2_heater
```

### 11.4 Multiple temperature sensors per room

Large or irregularly shaped rooms often have noticeable temperature gradients — one corner near a radiator can read 2–3 °C warmer than the opposite wall.  Using a single sensor introduces a systematic bias into the model correction step.  By listing several sensors under `temp_sensors`, the coordinator automatically averages their readings before feeding the value to the thermal model.

The same mechanism can also be used when you have redundant sensors and want to guard against a single sensor going offline (the average of the remaining valid readings is used).

#### 11.4.1 Open-plan living/dining room with two sensors

A large open-plan space has one sensor mounted near the dining area (north wall) and another near the sofa/TV area (south wall, closer to the heat pump).  The average of the two sensors gives a more representative room temperature.

```yaml
heating_assistant:
  outdoor_temp_entity: sensor.outdoor_temperature

  rooms:
    - name: living_dining
      thermal_mass: 12000000    # large open-plan space
      r_external: 0.03
      setpoint: 21.0
      temp_sensors:             # averaged by the coordinator
        - sensor.living_dining_temp_north
        - sensor.living_dining_temp_south
      windows:
        - area: 5.0
          orientation: 180      # south-facing glazing
          tilt: 90

  heat_sources:
    - name: hp_living
      type: heat_pump
      room: living_dining
      max_power: 5000
      cop_rated: 4.0
      cop_temp_ref: 7.0
      min_power: 1000         # unit cannot modulate below 1 kW thermal
      max_temp_offset: 5.0
      turn_off_deadband: 1.0
      heater_entity: climate.living_heat_pump
```

#### 11.4.2 Full house with mixed single- and multi-sensor rooms

This example combines rooms that use a single `temp_sensor` with rooms that use multiple sensors under `temp_sensors`.  The kitchen uses three sensors — one at counter height near the window, one above the stove, and one at seating height — to capture the wider temperature spread in a heavily used cooking space.

```yaml
heating_assistant:
  outdoor_temp_entity: sensor.weather_station_outdoor
  latitude: 55.68
  longitude: 12.57
  update_interval: 900
  horizon: 6

  rooms:
    - name: hallway
      thermal_mass: 2000000
      r_external: 0.1
      setpoint: 18.0
      temp_sensor: sensor.hallway_temp   # single sensor is fine for a small hallway
      connections:
        - room: living_room
          r_value: 0.25
        - room: kitchen
          r_value: 0.4
        - room: bedroom_1
          r_value: 0.3
        - room: bedroom_2
          r_value: 0.3

    - name: living_room
      thermal_mass: 9000000
      r_external: 0.03
      setpoint: 21.0
      temp_sensors:            # two sensors: one at each end of the room
        - sensor.living_room_temp_east
        - sensor.living_room_temp_west
      connections:
        - room: hallway
          r_value: 0.25
        - room: kitchen
          r_value: 0.2
      windows:
        - area: 5.0
          orientation: 180
          tilt: 90

    - name: kitchen
      thermal_mass: 5000000
      r_external: 0.05
      setpoint: 20.0
      temp_sensors:            # three sensors averaged for representative reading
        - sensor.kitchen_temp_window
        - sensor.kitchen_temp_stove
        - sensor.kitchen_temp_table
      connections:
        - room: hallway
          r_value: 0.4
        - room: living_room
          r_value: 0.2
      windows:
        - area: 1.5
          orientation: 90

    - name: bedroom_1
      thermal_mass: 4000000
      r_external: 0.04
      setpoint: 19.0
      temp_sensor: sensor.bedroom1_temp
      connections:
        - room: hallway
          r_value: 0.3
      windows:
        - area: 2.0
          orientation: 180
          tilt: 90

    - name: bedroom_2
      thermal_mass: 3500000
      r_external: 0.045
      setpoint: 19.0
      temp_sensor: sensor.bedroom2_temp
      connections:
        - room: hallway
          r_value: 0.3
      windows:
        - area: 1.5
          orientation: 0
          tilt: 90

  heat_sources:
    - name: main_heat_pump
      type: heat_pump
      room: living_room
      max_power: 7000
      cop_rated: 3.8
      cop_temp_ref: 7.0
      min_power: 1400         # ~20 % of rated – prevents short-cycling
      max_temp_offset: 5.0
      turn_off_deadband: 1.0
      heater_entity: climate.daikin_hp

    - name: kitchen_heater
      type: electric_heater
      room: kitchen
      max_power: 1200
      heater_entity: switch.kitchen_heater

    - name: bedroom1_heater
      type: electric_heater
      room: bedroom_1
      max_power: 1000
      heater_entity: switch.bedroom1_heater

    - name: bedroom2_heater
      type: electric_heater
      room: bedroom_2
      max_power: 800
      heater_entity: switch.bedroom2_heater
```

### 11.5 Comfort schedules – sleep mode and weekday setback

The same two-bedroom flat from [Section 11.2](#112-two-bedroom-flat--rooms-with-heat-pump-and-supplemental-heater), augmented with comfort schedules:

* the **living room** runs an eco setback while the household is at work and switches off entirely overnight (heat returns automatically before the morning routine thanks to the MPC's preheat);
* the **bedrooms** stay cool during the day and switch off during sleep hours;
* the **bathroom** keeps a comfort temperature in the morning peak only.

```yaml
heating_assistant:
  outdoor_temp_entity: sensor.openweathermap_temperature
  weather_entity: weather.forecast_home
  horizon: 8                # 8 × 15 min = 2 h preheat look-ahead

  rooms:
    - name: living_room
      thermal_mass: 8000000
      r_external: 0.04
      setpoint: 21.0                   # comfort setpoint (used outside any period)
      temp_sensor: sensor.living_room_temperature
      schedule:
        - name: workday_eco
          start: "08:30"
          end: "16:00"
          days: [mon, tue, wed, thu, fri]
          setpoint: 18.0               # gentle setback while at work
        - name: night
          start: "22:30"
          end: "05:30"
          mode: off                    # heat source completely off
          frost_protection: 12.0

    - name: bedroom_1
      thermal_mass: 4000000
      r_external: 0.05
      setpoint: 19.0
      temp_sensor: sensor.bedroom1_temperature
      schedule:
        - name: night
          start: "22:00"
          end: "06:00"
          mode: off
          frost_protection: 14.0       # bedrooms typically need a higher floor
        - name: daytime_eco
          start: "08:00"
          end: "20:00"
          setpoint: 17.0               # rarely used during the day

    - name: bathroom
      thermal_mass: 2500000
      r_external: 0.06
      setpoint: 19.0                   # gentle baseline, used outside the morning peak
      temp_sensor: sensor.bathroom_temperature
      schedule:
        - name: morning_peak
          start: "06:30"
          end: "08:30"
          setpoint: 22.0               # warm towel-rail temperature for showers
        - name: night
          start: "22:30"
          end: "05:30"
          mode: off
          frost_protection: 12.0

  heat_sources:
    - name: living_room_hp
      type: heat_pump
      room: living_room
      max_power: 5000
      heater_entity: climate.living_room_heat_pump
    - name: bedroom1_heater
      type: electric_heater
      room: bedroom_1
      max_power: 1500
      heater_entity: switch.bedroom1_heater
    - name: bathroom_heater
      type: electric_heater
      room: bathroom
      max_power: 800
      heater_entity: switch.bathroom_heater
```

What this configuration achieves:

* Between 22:30 and 05:30 the living-room heat pump and bathroom heater stop running — no electricity is drawn unless the indoor temperature drops to the frost-protection floor.
* The MPC sees the upcoming 05:30 transition through its 2-hour horizon and starts heating around 03:30–04:00, so the room is back at 21 °C when the schedule wakes up.
* Weekdays from 08:30–16:00 the living-room setpoint drops to 18 °C; the controller saves energy without letting the room cool past the eco target.
* The bathroom is fully comfortable from 06:30–08:30 and idles around 19 °C the rest of the day.

To override a schedule for a single evening — e.g. you decide to stay in the living room past 22:30 — call:

```yaml
service: heating_assistant.set_schedule_enabled
data:
  room_name: living_room
  enabled: false
```

The schedule resumes at the next Home Assistant restart, or when you call the same service with `enabled: true`.

