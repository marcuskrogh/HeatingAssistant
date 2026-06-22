# Configuration Reference

> A field-by-field reference for every Heating Assistant setting — rooms,
> windows, heat sources, comfort schedules, and the global/control settings.

**Heating Assistant is configured entirely in the Home Assistant UI; there is no
`configuration.yaml` to edit.** The configuration surfaces are:

- **The Heating Assistant sidebar panel** — the primary surface. Its
  **Configuration** page covers rooms, heat sources, environment/site and system
  parameters; comfort **schedules** and **controller tuning** are on the panel's
  **Schedules** and **Tuning** pages.
- **Settings → Devices & services → Heating Assistant → Configure** — a subset:
  general & sensor settings, and the room, window and heat-source editors.

This page documents what each setting means and its default. For a guided
walkthrough of a first install, see [Setting up your first
home](../README.md#setting-up-your-first-home). For help choosing thermal
parameter values, see the [Parameter Estimation & Tuning guide](TUNING.md).

> **Note on naming.** The tables below use each setting's internal name (e.g.
> `thermal_mass`). In the UI these appear as friendly labels with inline help
> (e.g. *Thermal mass*); the names are what you would see in diagnostics,
> service calls, and the panel's Configuration page.

**Contents**

- [10. Configuration Reference](#10-configuration-reference)
- [11. Example home layouts](#11-example-home-layouts)

---

## 10. Configuration Reference

The settings group into the **global/control settings** (the panel's
*Environment & Site*, *System Parameters* and *Tuning* pages) and the per-room
**room**, **connection**, **window**, **heat source** and **schedule** settings
(the panel's *Rooms*, *Heat Sources* and *Schedules* pages).

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
| `mpc_solver` | string | No | `qp` | Solver mode for the linearized MPC. The convex QP is solved via OSQP/HiGHS; legacy values (e.g. `ipopt`, `slsqp`) are accepted but ignored. |
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

Each room is added on the panel's **Configuration → Rooms** page (or via
**Configure → Manage rooms** in the integration options). Its settings:

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `name` | string | **Yes** | — | Unique identifier for the room.  Used to match heat sources, connections, and HA entity IDs.  Use only letters, digits, and underscores (no spaces). |
| `thermal_mass` | float | No | `5 000 000` | Effective heat capacity of the room [J/K].  Includes air mass, furniture, interior walls, and a fraction of the exterior walls.  See [Section 14.1](TUNING.md#141-thermal-mass-thermal_mass) for guidance. |
| `r_external` | float | No | `0.05` | Thermal resistance from the room to the outdoor environment [K/W].  Represents the sum of all paths to the outside: exterior walls, roof, ground, and infiltration.  See [Section 14.2](TUNING.md#142-external-thermal-resistance-r_external) for guidance. |
| `setpoint` | float | No | `22.0` | Target temperature [°C]. Set per room from its climate card (not the room editor); persisted and adjustable at runtime. |
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

Each connection represents a wall, door, floor or ceiling shared with another
room. Add connections from a room's editor.

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `room` | string | **Yes** | — | The `name` of the adjacent room.  The thermal model treats connections symmetrically — you only need to declare each connection once (adding it on both rooms is harmless). |
| `r_value` | float | **Yes** | — | Thermal resistance between the two rooms [K/W].  See [Section 14.3](TUNING.md#143-inter-room-thermal-resistance-r_value) for guidance. |

### 10.4 Window block (`windows`)

Each window (or glazed door) is added in the room editor under **Solar gain →
Windows** (panel), or via **Configure → Windows** in the integration options.

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `area` | float | **Yes** | — | Total glazed area of the window [m²].  For double windows, sum both panes. |
| `orientation` | float | **Yes** | — | Direction the window faces, in degrees **clockwise from North**.  0° = North, 90° = East, 180° = South, 270° = West.  A south-facing window in the Northern hemisphere receives the most direct solar gain in winter. |
| `tilt` | float | No | `90.0` | Angle of the window surface from the horizontal [°].  90° = vertical wall window (most common).  0° = horizontal skylight.  Roof windows are typically 30°–60°. |

### 10.5 Heat source block (`heat_sources`)

Each controllable heating device is added on the panel's **Configuration →
Heat Sources** page (or via **Configure → Heat sources** in the integration
options). Heat-pump-specific fields appear once the type is set to *Heat pump*.

**Common settings (all types)**

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `name` | string | **Yes** | — | Unique identifier for this heat source.  Must be unique across all heat sources. |
| `type` | string | **Yes** | — | Source type. One of `electric_heater`, `hydronic_radiator`, `oil_radiator`, `electric_floor_heating`, `hydronic_floor_heating`, `gas_heater`, `generic_thermostat`, or `heat_pump`. The type sets sensible defaults (e.g. the emitter time constant); heat pumps additionally model a temperature-dependent COP and cooling. |
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

Each room may have a schedule of named time-of-day periods, managed from the panel's **Schedules** page. Use it to lower the setpoint
when nobody is home (setback) or to switch the heat off entirely while you sleep,
with the controller automatically warming the room back up before you wake. A
period is matched purely on the local clock — no presence sensor or automation
required. Each period has these fields:

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

## 11. Example home layouts

These examples show how to translate a few real homes into rooms, connections,
windows and heat sources. Everything below is entered through the UI editors
(**Configure → Manage rooms / Windows / Heat sources / Schedule**) — there is no
file to edit. The starting parameter values come from the
[room size / building-age presets](#10-configuration-reference) and can be
refined later (see the [Tuning guide](TUNING.md)).

### 11.1 Studio apartment — one room, one heater

The simplest case: a single room with one window and a plug-in electric heater
on a smart switch.

- **Room** *Studio* — temperature sensor `sensor.studio_thermometer`,
  setpoint 21 °C, an older/poorly-insulated preset.
- **Window** — 1.5 m², facing **East** (90°).
- **Heat source** *Studio heater* — electric heater, 1500 W, controlled by
  `switch.studio_smart_plug`.

### 11.2 Two-bedroom flat — heat pump plus a backup heater

An open-plan living/kitchen with a wall heat pump and a backup panel heater,
plus a separate bedroom. The two rooms share a doorway.

- **Room** *Living/Kitchen* — large/modern preset, setpoint 21 °C; windows
  facing **South** (large) and **East**. **Connection** to *Bedroom*
  (interior door, `r_value` ≈ 0.3).
- **Room** *Bedroom* — setpoint 19 °C; one window facing **West**.
- **Heat sources** — a **heat pump** (`climate.*` entity, rated COP from its
  datasheet) and a backup **electric heater** in the living/kitchen; a small
  **electric heater** in the bedroom.

### 11.3 Whole house — central hallway, heat pump, solar windows

A detached house with a hallway connecting the living room, kitchen and two
bedrooms. The heat pump serves the main living space; each bedroom has a panel
heater.

- **Hallway** — mostly interior walls (high `r_external`); **connections** to
  the living room, kitchen and both bedrooms.
- **Living room** — well-insulated preset; large **South**-facing glazing and a
  **West** patio door; heat pump.
- **Kitchen**, **Bedroom 1**, **Bedroom 2** — each with its sensor, a window,
  and (bedrooms) a panel heater.

Connections only need to be declared once per room pair.

### 11.4 Rooms with several temperature sensors

Large or irregular rooms can have noticeable temperature gradients. In a room's
editor you can attach **multiple temperature sensors** (e.g. one at each end of
an open-plan space, or three around a busy kitchen); the controller averages
their readings before feeding the model. This also adds resilience — if one
sensor drops out, the average of the rest is used.

### 11.5 Comfort schedules — sleep and setback

Add periods on the panel's **Schedules** page for each room:

- **Living room** — a weekday *eco* setback (e.g. 18 °C, 08:30–16:00, Mon–Fri)
  and an overnight *off* period (22:30–05:30, frost protection 12 °C). The MPC
  pre-heats before the morning so the room is warm when the off period ends.
- **Bedrooms** — *off* overnight with a higher frost-protection floor; an
  optional daytime setback.
- **Bathroom** — a warm *comfort* period for the morning peak only, idling
  cooler the rest of the day.

To override a schedule for one evening, call the
`heating_assistant.set_schedule_enabled` service with the room and
`enabled: false`; it resumes on the next restart or when you re-enable it.
