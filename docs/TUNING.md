# Parameter Estimation & Controller Tuning

> How to estimate the thermal parameters the model needs (thermal mass,
> external resistance, inter-room resistance, window geometry), how the
> automatic ML identification persists its results, and how to tune the MPC
> regulator to eliminate oscillation and short-cycling.

Accurate parameters lead to accurate predictions and better control. Start with
the rough starting points in the [Quick start](../README.md#quick-start), then
refine with the empirical and machine-learning methods below. For the theory
behind the models these parameters feed, see [Physics, Models & Control
Theory](THEORY.md); for the services referenced here, see the [Services
reference](SERVICES.md).

**Contents**

- [14. Thermal Model Parameter Estimation Guide](#14-thermal-model-parameter-estimation-guide)
- [14.5 MPC regulator tuning](#145-mpc-regulator-tuning)
- [13.18 Estimated parameters – persistence and dashboard card](#1318-estimated-parameters--persistence-and-dashboard-card)

---

## 14. Thermal Model Parameter Estimation Guide

Accurate parameters lead to accurate predictions and better control.  This section gives practical guidance on how to estimate them.

### 14.1 Thermal mass `thermal_mass`

The effective thermal mass captures how much energy must be added (or removed) to change the room's temperature by 1 K.  It includes:

- **Air mass:**  $\rho_{\text{air}} \times V_{\text{room}} \times c_{p,\text{air}} \approx 1.2\;\text{kg/m}^3 \times V \times 1005\;\text{J/(kg·K)} \approx 1200 \times V\;\text{J/K}$
- **Furniture and contents:**  roughly 0.5–1 × air mass for a furnished room
- **Interior wall surface layers:**  the inner few centimetres of plasterboard, brick, or timber absorb/release heat on the timescale of hours
- **Floor and ceiling finishes**

**Typical values:**

| Room type | Thermal mass (J/K) |
|-----------|:-----------------:|
| Small bedroom (15 m², light furnishing) | 2 – 4 × 10⁶ |
| Medium living room (25 m², typical furnishing) | 5 – 8 × 10⁶ |
| Large open-plan kitchen/living (40 m²) | 8 – 15 × 10⁶ |
| Brick-built room with tiled floor | add 20–50 % to above |

**Quick estimate:** start with `thermal_mass ≈ 4000 × floor_area_m2` (in J/K) and adjust based on construction type and observation.

### 14.2 External thermal resistance `r_external`

The external thermal resistance describes the overall thermal barrier between the room and the outdoors.  It is the reciprocal of the overall heat-transfer coefficient multiplied by area: $R = 1 / (U \times A_{\text{total}})$.

Alternatively you can measure it empirically: run the room at a steady temperature with no solar gain (night, overcast) and observe the steady-state heater power $Q$ [W] and the indoor–outdoor temperature difference $\Delta T$ [K].  Then $R_{\text{ext}} \approx \Delta T / Q$ [K/W].

**Typical values:**

| Building type | r_external (K/W) per room |
|---------------|:-------------------------:|
| Modern well-insulated house (2020s build) | 0.02 – 0.04 |
| Post-1980s double-glazed house | 0.04 – 0.07 |
| Pre-1970 poorly insulated house | 0.07 – 0.15 |
| Modern flat / apartment (interior rooms) | 0.1 – 0.3 |

### 14.3 Inter-room thermal resistance `r_value`

This represents the thermal conductance of the wall, floor, ceiling, or doorway between two adjacent rooms.  Higher `r_value` means less heat exchange.

**Rough guide:**

| Boundary type | r_value (K/W) |
|---------------|:-------------:|
| Open doorway / archway | 0.05 – 0.15 |
| Interior door (often open) | 0.1 – 0.2 |
| Interior door (usually closed) | 0.2 – 0.5 |
| Lightweight plasterboard partition | 0.15 – 0.3 |
| Brick or concrete interior wall | 0.3 – 0.6 |
| Insulated floor/ceiling between flats | 0.5 – 1.5 |

### 14.4 Window orientation and tilt

The `orientation` key is the compass bearing of the **outward-facing normal** of the window, measured clockwise from North.

| Direction | Value |
|-----------|:-----:|
| North | 0 |
| North-East | 45 |
| East | 90 |
| South-East | 135 |
| South | 180 |
| South-West | 225 |
| West | 270 |
| North-West | 315 |

For a roof window pitched towards the South at 30° from horizontal, use `orientation: 180` and `tilt: 30`.

### 14.5 MPC regulator tuning

The MPC controller solves a quadratic program at each update cycle.  Its behaviour is determined by three cost weights and the prediction horizon.  This section explains how to diagnose common problems and what to adjust.

#### 14.5.1 Overview of tunable parameters

| Parameter | Config key | Default | Effect |
|-----------|-----------|---------|--------|
| **Prediction horizon** | `horizon` | `100` steps | How many time steps ahead the controller plans.  Longer horizons give the controller more room to "see" the thermal inertia of the building and act proactively. |
| **Terminal weight** | `terminal_weight` | `100` | Multiplier λ on the terminal tracking cost **P** = λ**Q**.  A large value (≥ 50) forces the predicted trajectory to reach the setpoint by the end of the horizon, which is the primary mechanism for steady-state tracking.  Increase to 200–500 if the controller still crosses or misses the setpoint. |
| **Energy weight** | `energy_weight` | `0.01` | Weight on ‖**u**‖² — penalises running heaters.  Increase to make the controller more conservative (less aggressive heating). |
| **Smoothing weight** | `smoothing_weight` | `0.1` | Weight on ‖Δ**u**‖² — penalises changing the heater output from one step to the next.  Increase to dampen oscillations and reduce actuator wear. |
| **Constraint offset** | `constraint_offset` | `2.0 °C` | Half-width of the soft temperature band around the setpoint.  Does not directly affect oscillations but controls how strictly the constraint is enforced. |
| **EKF process noise** | `sigma_w` | `0.1` | Process-noise level for the thermal state model. Higher values make the filter react faster to unmodelled disturbances. |
| **EKF measurement noise** | `sigma_v` | `0.5` | Measurement-noise level for room temperature sensors. Higher values trust sensors less and model predictions more. |
| **EKF offset noise** | `sigma_b` | `0.002` | Process noise for the integrated offset state (model-mismatch compensation). Higher values let offset correction adapt faster. |

These parameters can be tuned from the integration setup/options UI, and can also be set under the top-level `heating_assistant:` key in `configuration.yaml`.

#### 14.5.2 Diagnosing and correcting oscillations

Oscillations appear as repeated undershoot/overshoot cycles around the setpoint in the room temperature history.  The most common causes and fixes are:

**Cause 1 — Prediction horizon too short**

When `horizon` is small (e.g. 2–4 steps at 15-minute intervals = only 30–60 minutes of lookahead), the controller does not see far enough ahead to account for the building's thermal lag.  It heats aggressively to hit the setpoint within the short window, overshoots, then cuts off heating, undershoots, and repeats.

*Fix:* Increase `horizon`.  Start with 6–8 steps (90–120 min at `update_interval = 900 s`).  The computational cost scales roughly as O(N²), so avoid very large horizons (> 24).

```yaml
heating_assistant:
  horizon: 8
```

**Cause 2 — Smoothing weight too low**

With a low `smoothing_weight` the controller is free to swing the heating fraction between 0 and 1 from one 15-minute step to the next.  This produces bang-bang-like behaviour that generates oscillations.

*Fix:* Increase `smoothing_weight`.  The default is `0.1`; try values in the range `0.5`–`2.0` if oscillations persist.  A value of `1.0` penalises a full 0→1 step change as heavily as a 1 °C tracking error.

```yaml
heating_assistant:
  smoothing_weight: 1.0
```

**Cause 3 — Energy weight too high**

A very high `energy_weight` forces the controller to keep heating to a minimum.  The room cools below setpoint, triggering a burst of full-power heating, which overshoots, causing a repeated cycle.

*Fix:* Reduce `energy_weight`.  The default is `0.01`; values below `0.001` are rarely needed.  If you notice abrupt full-power bursts followed by long off periods, decrease `energy_weight`.

```yaml
heating_assistant:
  energy_weight: 0.005
```

**Cause 4 — Incorrect thermal parameters**

If `thermal_mass` is underestimated the model predicts the room heats and cools faster than it actually does, leading to oscillatory corrections.  If `r_external` is wrong the steady-state balance is off, producing drift.

*Fix:* Re-estimate `thermal_mass` and `r_external` using the empirical method in [Section 14.1](#141-thermal-mass-thermal_mass) and [Section 14.2](#142-external-thermal-resistance-r_external), or run the `estimate_parameters` service (see [Section 13.11](SERVICES.md#1311-setup-service--estimate-parameters)) or the automatic ML estimation service (see [Section 13.12](SERVICES.md#1312-setup-service--estimate-parameters-ml)).

#### 14.5.3 Step-by-step detuning procedure

If you are experiencing oscillations, follow these steps in order:

1. **Check the predicted temperature sensor** (`sensor.heating_assistant_<room>_temperature_forecast`).  If the MPC prediction closely tracks the oscillation, the problem is in the controller weights.  If the prediction is smooth but the actual temperature oscillates, the issue is in the thermal model parameters.

2. **Increase `smoothing_weight` in steps** — try `0.5`, then `1.0`, then `2.0`.  After each change, restart HA and observe the system for one to two hours.  The oscillation amplitude should decrease.  Stop when the response is acceptably smooth.

3. **If oscillations persist, increase `horizon`** — try `8`, then `10`.  This gives the controller enough lookahead to ride out the room's thermal lag without overshooting.

4. **Check `energy_weight`** — if the heater makes aggressive on/off transitions, try reducing `energy_weight` from `0.01` to `0.005`.

5. **Verify thermal model parameters** — if the MPC predictions do not match actual temperatures, correct `thermal_mass` and `r_external` before tuning controller weights.

#### 14.5.4 Effect of `smoothing_weight` on heat pump short-cycling

Heat pumps are particularly sensitive to rapid on/off commands because each compressor start causes mechanical wear and a brief efficiency dip.  In addition to the heat pump's own `turn_off_deadband` parameter, increasing `smoothing_weight` at the controller level discourages the MPC from requesting large changes in the heating fraction between consecutive steps.

Recommended starting point for a heat pump installation:

```yaml
heating_assistant:
  smoothing_weight: 0.5   # penalises rapid changes; reduce short-cycling
  horizon: 8              # longer lookahead reduces the need for rapid corrections
```

If the compressor still short-cycles after increasing `smoothing_weight`, also increase `turn_off_deadband` on the heat pump source (default `1.0 °C`; try `1.5`–`2.0 °C`).

#### 14.5.5 Quick reference — tuning cheat sheet

| Symptom | Primary fix | Secondary fix |
|---------|-------------|---------------|
| MPC trajectory does not reach setpoint over the horizon | ↑ `terminal_weight` (try 200 → 500) | ↑ `horizon` (try 8 → 10) |
| Oscillating temperature (repeated undershoot/overshoot) | ↑ `smoothing_weight` (try 0.5 → 1.0 → 2.0) | ↑ `horizon` (try 8 → 10) |
| Heater runs at 100 % then cuts off abruptly | ↑ `energy_weight` (try 0.02 → 0.05) | ↑ `horizon` |
| Room never quite reaches setpoint | ↑ `terminal_weight` (try 200 → 500) | ↓ `energy_weight` (try 0.005 → 0.001) |
| Heat pump compressor short-cycling | ↑ `smoothing_weight` + ↑ `turn_off_deadband` | ↑ `horizon` |
| Sluggish response / room heats too slowly | ↓ `energy_weight` or ↓ `smoothing_weight` | — |
| Temperature tracks setpoint but with slow drift | Correct `r_external` or `thermal_mass` | — |
| Climate entity stuck on `idle` while room is being cooled | Update to the latest version — the entity now reports `cooling` and supports `heat_cool` mode for heat-pump rooms | — |
| Cooling power on the Heating Plan sensor reaches `−max_power` | Update — cooling capacity is now derived from `cooling_cop × (max_power / cop_rated)` and the per-source `cooling_efficiency` further modulates it | Set `cooling_efficiency` to 0.3–0.5 if you only use `dry` mode |

#### 14.5.6 Live tuning chart

The Controller-Tuning ApexCharts card in [`MODEL_FIT_GUIDE.md`](../MODEL_FIT_GUIDE.md#apex-charts-card-controller-tuning-live-view) overlays the measured temperature, the MPC prediction, the setpoint, and the prediction error in a single time-aligned figure.  Add it to a room subview while iterating on the cost weights — the chart instantly reveals whether a tweak improved tracking or just hid an underlying model-fit problem.

#### 14.5.7 Monitoring MPC performance with the performance sensor

The **MPC Performance** sensor (`sensor.heating_assistant_mpc_performance`) exposes key computational and control-quality metrics that you can display on your dashboard:

| Attribute | Description |
|-----------|-------------|
| `last_solve_time_s` | Wall-clock time [s] of the most recent OCP solve |
| `mean_solve_time_s` | Rolling mean solve time [s] over the last 100 solves |
| `max_solve_time_s` | Maximum solve time [s] in the rolling history |
| `n_solves` | Number of solves recorded in the rolling buffer |
| `mean_tracking_error` | Mean absolute deviation of all rooms from their setpoints [°C] |
| `max_tracking_error` | Maximum absolute deviation across all rooms [°C] |
| `current_tracking_errors` | Per-room absolute tracking error [°C] |
| `terminal_weight` | Terminal cost multiplier λ currently in effect |
| `recent_solve_times_s` | List of the last 50 solve times [s] for sparkline charts |

**Typical solve times** at the default settings (`horizon = 100`, `update_interval = 900 s`, `n_int_steps = 10`) are 0.05–0.3 s depending on the number of rooms and CPU speed.  If `max_solve_time_s` approaches the `update_interval` (e.g. 900 s), consider reducing `horizon` or `n_int_steps`.

**Example ApexCharts card for MPC performance:**

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: MPC Solver Performance
graph_span: 2h
series:
  - entity: sensor.heating_assistant_mpc_performance
    attribute: recent_solve_times_s
    type: bar
    name: Solve time [s]
    data_generator: |
      // Assumes coordinator update_interval = 900 s (one solve per 15 minutes).
      // Adjust 900000 to match your update_interval if you changed it.
      return entity.attributes.recent_solve_times_s.map((v, i) => [
        new Date(Date.now() - (entity.attributes.recent_solve_times_s.length - 1 - i) * 900000).getTime(),
        v
      ]);
```

**Example entities card for tracking quality:**

```yaml
type: entities
title: MPC Tracking Quality
entities:
  - entity: sensor.heating_assistant_mpc_performance
    name: Last solve time
    attribute: last_solve_time_s
    suffix: s
  - entity: sensor.heating_assistant_mpc_performance
    name: Mean solve time
    attribute: mean_solve_time_s
    suffix: s
  - entity: sensor.heating_assistant_mpc_performance
    name: Max solve time
    attribute: max_solve_time_s
    suffix: s
  - entity: sensor.heating_assistant_mpc_performance
    name: Mean tracking error
    attribute: mean_tracking_error
    suffix: °C
  - entity: sensor.heating_assistant_mpc_performance
    name: Terminal weight (λ)
    attribute: terminal_weight
```

---

---

## 13.18 Estimated parameters – persistence and dashboard card

This section describes how the integration persists identified thermal parameters across restarts and provides a ready-made Lovelace card to inspect them.

---

### 13.18.1 What parameters are estimated and why they matter

When you run `heating_assistant.estimate_parameters_ml` (or press the **Estimate Parameters** button), the Kalman-filter ML estimator identifies up to four classes of parameter for each room / heat source in a single joint optimisation:

| Parameter | Entity attribute | Physical meaning |
|-----------|-----------------|-----------------|
| `thermal_mass` | `rooms.<room>.thermal_mass` | Energy required to heat the room by 1 K [J/K].  Determines how quickly the room heats up and how long it stays warm. |
| `r_external` | `rooms.<room>.r_external` | Thermal resistance to the outdoors [K/W].  Smaller = leakier room (more heating required in cold weather). |
| `internal_gain` | `rooms.<room>.internal_gain` | Steady-state background heat input not from the controllable source [W] — body heat, appliances, solar leakage.  Non-zero offset removes a systematic bias. |
| `power_scale` | `sources.<source>.power_scale` | Multiplier on the nominal heat-source rating [–].  1.0 = exactly as rated; 1.3 = delivers 30 % more heat than the nominal specification. |

The validity of the first two parameters is summarised by the **Parameter Confidence** sensor (0–100 %).

---

### 13.18.2 Persistence across Home Assistant restarts

Estimated parameters are **automatically persisted** in the integration's config-entry data (`entry.data["estimated_params"]`) every time a successful ML estimation is applied.  On the next full HA restart the coordinator reads this snapshot before building the MPC controller, so you start exactly where you left off — no waiting for 30+ more history steps.

The history buffer is also saved to HA's [Storage helper](https://developers.home-assistant.io/docs/dev_101_services) (`<config>/storage/heating_assistant_history_<entry_id>`) on clean shutdown, so the estimator has data to work with immediately after a restart.

> **Note:** Parameters are stored per config-entry.  If you delete and re-create the integration entry you will lose the persisted estimation and need to start over.

---

### 13.18.3 Resetting to defaults

Press the **Reset Parameters** button (`button.heating_assistant_reset_parameters`) to discard the persisted estimation and revert every room and source to its configured (YAML / default) values.  The snapshot is removed from `entry.data` so subsequent restarts also use the defaults.

Use this when:
- A parameter set turned out to be wrong (e.g. estimated from an atypical heating experiment).
- You want to re-run the estimation after changing the physical setup (e.g. new radiator, improved insulation).

---

### 13.18.4 New sensor entities

Three new sensor entities are created by this feature:

#### `sensor.heating_assistant_estimated_parameters_status`

System-wide anchor sensor.  State is `"estimated"` when a persisted snapshot exists and `"default"` otherwise.

| Attribute | Description |
|-----------|-------------|
| `rooms` | Dict — per-room `{thermal_mass, r_external, internal_gain, is_estimated}` |
| `sources` | Dict — per-source `{power_scale, is_estimated}` |
| `connections` | Dict — per-pair `{r_value, is_estimated}` |
| `estimated_at` | ISO-8601 timestamp of the last successful run |
| `log_likelihood` | Log-likelihood value at the optimum |
| `n_rooms_estimated` | Number of rooms whose parameters were estimated |
| `n_sources_estimated` | Number of sources whose scale was estimated |

#### `sensor.heating_assistant_<source>_heater_scale`

Per heat source.  State is the power-scale factor as a percentage (100 % = nominal, 130 % = 30 % above nominal).

| Attribute | Description |
|-----------|-------------|
| `power_scale` | Raw scale factor |
| `max_power` | Nominal maximum thermal power [W] |
| `is_estimated` | `true` if this value came from an ML run |
| `estimated_at` | ISO-8601 timestamp |

#### `sensor.heating_assistant_<room>_parameter_confidence` (extended)

Two new attributes were added to the existing confidence sensor:

| New attribute | Description |
|---------------|-------------|
| `internal_gain` | Currently active internal-gain estimate [W] |
| `is_estimated` | `true` if thermal_mass/r_external came from ML estimation |
| `estimated_at` | ISO-8601 timestamp |

---

### 13.18.5 Lovelace card – estimated parameters overview

The following card gives a single-glance view of all estimated parameters, their physical validity, and the estimation provenance.  Replace `living_room` and `heater` with your actual room/source names.

```yaml
type: vertical-stack
cards:

  # ── Status banner ────────────────────────────────────────────────────────
  - type: entities
    title: Estimated Parameters
    entities:
      - entity: sensor.heating_assistant_estimated_parameters_status
        name: Status
        secondary_info: last-changed
      - type: divider

      # ── Per-room confidence scores ───────────────────────────────────────
      # Duplicate this block for each room in your configuration.
      - entity: sensor.heating_assistant_living_room_parameter_confidence
        name: living_room – confidence
        secondary_info: last-changed

  # ── Markdown summary (reads from the status sensor attributes) ───────────
  - type: markdown
    title: Active parameter values
    content: >
      {% set s = state_attr(
           'sensor.heating_assistant_estimated_parameters_status', 'rooms') %}
      {% set src = state_attr(
           'sensor.heating_assistant_estimated_parameters_status', 'sources') %}
      {% set ts = state_attr(
           'sensor.heating_assistant_estimated_parameters_status', 'estimated_at') %}
      {% set ll = state_attr(
           'sensor.heating_assistant_estimated_parameters_status', 'log_likelihood') %}

      **Estimation:** {{ states('sensor.heating_assistant_estimated_parameters_status') }}
      {%- if ts %} · estimated {{ ts[:10] }}{%- endif %}
      {%- if ll is not none %} · log-lik {{ ll | round(1) }}{%- endif %}

      ---

      | Room | Thermal mass | R external | Internal gain | Estimated? |
      |------|-------------|-----------|--------------|------------|
      {% for room, p in s.items() -%}
      | {{ room }} | {{ p.thermal_mass | int | string }} J/K
      | {{ p.r_external | round(5) }} K/W
      | {{ p.internal_gain | round(1) }} W
      | {{ '✓' if p.is_estimated else '–' }} |
      {% endfor %}

      ---

      | Source | Power scale | Estimated? |
      |--------|------------|------------|
      {% for src_name, sp in src.items() -%}
      | {{ src_name }} | {{ (sp.power_scale * 100) | round(1) }} %
      | {{ '✓' if sp.is_estimated else '–' }} |
      {% endfor %}

  # ── Buttons ──────────────────────────────────────────────────────────────
  - type: horizontal-stack
    cards:
      - type: button
        name: Estimate Parameters
        icon: mdi:chart-bell-curve-cumulative
        tap_action:
          action: toggle
        entity: button.heating_assistant_estimate_parameters

      - type: button
        name: Reset to Defaults
        icon: mdi:restore
        tap_action:
          action: toggle
        entity: button.heating_assistant_reset_parameters
```

> **Tip:** The markdown card template works out-of-the-box for any number of rooms and sources because it iterates over the `rooms` / `sources` dicts from the status sensor attributes.  If you prefer individual entity rows, use the `sensor.heating_assistant_<room>_parameter_confidence` and `sensor.heating_assistant_<source>_heater_scale` sensors in a standard `entities` card.

---

### 13.18.6 Interpreting the parameter validity flags

The **Parameter Confidence** sensor raises warnings (and lowers the score below 100 %) in these situations:

| Warning | Likely cause |
|---------|-------------|
| `thermal_mass out of range` | Value below ~50 000 J/K or above ~200 000 000 J/K — probably a bad estimation with too little data. |
| `r_external out of range` | Value below 0.001 K/W (implausibly good insulation) or above 1.0 K/W (implausibly leaky). |
| `time_constant out of range` | Computed time constant τ = R × C outside 1–500 hours — indicates a parameter combination that is physically unrealistic. |

A score of **100 %** means all three checks pass.  A score below 100 % does not mean the model will not work, but it is a signal to inspect the values (run `heating_assistant.validate_parameters` for a detailed report) and possibly re-estimate with more data.

---

