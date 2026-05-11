# Model Fit Identification Tools

This document describes the model fit identification and validation tools introduced in Heating Assistant to help users assess the quality of their thermal model, validate parameter estimates, and evaluate controller performance.

## Overview

The model fit identification tools provide:

1. **Goodness-of-fit metrics** – How well the thermal model predictions match measured temperatures
2. **Parameter validation** – Whether thermal parameters are physically reasonable
3. **Controller performance analysis** – How well the MPC controller tracks setpoints
4. **Diagnostic visualizations** – Sensor entities for real-time monitoring in Home Assistant

These tools are essential for:
- Verifying that parameter estimation has produced valid results
- Identifying when model reconfiguration is needed
- Tuning MPC controller parameters
- Troubleshooting unexpected heating behavior

---

## Diagnostic Sensors

Three new sensor entities are automatically created for each room to provide real-time model fit monitoring:

### 1. Prediction Error Sensor

**Entity:** `sensor.heating_assistant_<room_name>_prediction_error`

Reports the current prediction error (residual) in °C:
- **Positive error** = model over-predicts (predicts warmer than actual)
- **Negative error** = model under-predicts (predicts colder than actual)

**Attributes:**
- `recent_errors` – List of the last 50 prediction errors
- `rmse` – Root mean squared error over recent history
- `mae` – Mean absolute error
- `bias` – Mean error (systematic bias)
- `max_error` – Maximum absolute error
- `n_samples` – Number of samples in the analysis

**Use case:** Monitor prediction quality in real-time. Large or persistent errors indicate model misconfiguration.

---

### 2. Model Fit Quality Sensor

**Entity:** `sensor.heating_assistant_<room_name>_model_fit_quality`

Reports the R² (coefficient of determination) score [0-1]:
- **1.0** = perfect fit
- **0.0** = no better than mean prediction
- **< 0** = worse than mean prediction

**Attributes:**
- `r_squared` – Coefficient of determination
- `rmse` – Root mean squared error [°C]
- `mae` – Mean absolute error [°C]
- `bias` – Systematic prediction bias [°C]
- `max_error` – Maximum absolute error [°C]
- `residual_std` – Standard deviation of residuals [°C]
- `residual_autocorr_lag1` – Lag-1 autocorrelation of residuals (should be near 0)
- `n_samples` – Number of samples used

**Use case:** Assess overall model quality. R² > 0.9 indicates excellent fit. R² < 0.7 suggests model problems.

---

### 3. Parameter Confidence Sensor

**Entity:** `sensor.heating_assistant_<room_name>_parameter_confidence`

Reports a confidence score [0-100]:
- **100** = all parameters are in physically valid ranges
- **0** = parameters are outside valid ranges

**Attributes:**
- `thermal_mass` – Current thermal mass [J/K]
- `r_external` – Current external resistance [K/W]
- `time_constant_hours` – Thermal time constant [hours]
- `mass_valid` – Whether thermal_mass is in valid range
- `r_external_valid` – Whether r_external is in valid range
- `time_constant_valid` – Whether time constant is reasonable
- `warnings` – List of validation warnings

**Use case:** Verify parameter estimation results. Scores < 100 indicate parameters that may need adjustment.

---

### 4. Open-Loop RMSE Sensor

**Entity:** `sensor.heating_assistant_<room_name>_open_loop_rmse`

Reports the RMSE of multi-step open-loop (free-run) simulations [°C]:
- **< 0.2°C** → excellent model accuracy
- **0.2–0.5°C** → acceptable; consider re-estimating parameters
- **> 0.5°C** → poor model; re-run `estimate_parameters_ml`

**Attributes:**
- `open_loop_rmse` — RMSE over all segments [°C]
- `open_loop_mae` — MAE over all segments [°C]
- `simulation` — list of `{time, measured, predicted}` for each step (Apex Charts ready)
- `segment_length_steps` — steps per free-run window
- `n_segments` — number of windows used

**Use case:** Continuously monitor whether the model drifts over multi-step predictions.  Unlike the one-step Kalman predictions (which are trivially accurate because the filter drives innovations to zero), open-loop RMSE measures genuine model quality.

---

### 5. Kalman Innovation Sensor

**Entity:** `sensor.heating_assistant_<room_name>_kalman_innovation`

Reports the most recent Kalman innovation ν = y − Cŷ [°C]:
- Near zero in a well-tuned filter
- Large values indicate model mismatch or incorrect noise covariances

**Attributes:**
- `innovations` — list of `{time, value}` for the recent history
- `mean` — sample mean of recent innovations (should ≈ 0)
- `std` — standard deviation of recent innovations
- `autocorr_lag1` — lag-1 autocorrelation (should ≈ 0 for a consistent filter)
- `is_consistent` — `true` if the filter passes basic consistency checks

**Use case:** Diagnose Kalman filter tuning.  Persistent non-zero mean indicates systematic model bias; high autocorrelation indicates missing dynamics.

---

### 6. Residual ACF Sensor

**Entity:** `sensor.heating_assistant_<room_name>_residual_acf`

Reports the lag-1 autocorrelation of aligned prediction residuals [dimensionless]:
- **|ρ₁| < confidence bound** → residuals are white noise (good)
- **|ρ₁| ≥ confidence bound** → residuals are autocorrelated (model missing dynamics)

**Attributes:**
- `acf` — full ACF at lags 0…20
- `lags` — corresponding lag indices
- `confidence_bound` — ±1.96/√n approximate 95% confidence interval
- `ljung_box_stat` — Ljung-Box Q statistic (large values indicate non-whiteness)

**Use case:** Formal statistical test for residual whiteness after parameter estimation.

---

## Diagnostic Services

Three existing services provide detailed analysis reports, and two new services
cover inter-room resistance estimation and open-loop simulation:

### 1. `heating_assistant.analyze_model_fit`

Performs comprehensive model fit analysis for all rooms (or a specific room).

**Service data:**
- `room_name` (optional) – Analyze a specific room; if omitted, analyzes all rooms

**Returns:**
- R² score, RMSE, MAE, bias for each room
- Residual statistics including autocorrelation
- Number of data samples used

**Example usage:**
```yaml
service: heating_assistant.analyze_model_fit
data:
  room_name: living_room  # Optional
```

**Interpreting results:**
- **R² > 0.9:** Excellent model fit
- **R² 0.7-0.9:** Good fit, minor adjustments may help
- **R² < 0.7:** Poor fit, check parameters
- **Bias ≠ 0:** Systematic error (model consistently over/under-predicts)
- **High autocorr:** Model not capturing dynamics (may need different time constant)

---

### 2. `heating_assistant.validate_parameters`

Validates the physical reasonableness of thermal parameters.

**Service data:**
- `room_name` (optional) – Validate a specific room; if omitted, validates all rooms

**Returns:**
- Parameter values and validity flags for each room
- Thermal time constant
- Detailed warnings for any issues

**Example usage:**
```yaml
service: heating_assistant.validate_parameters
data: {}  # Validates all rooms
```

**Parameter bounds:**

| Parameter | Minimum | Maximum | Notes |
|-----------|---------|---------|-------|
| `thermal_mass` | 10 kJ/K | 500 MJ/K | Typical: 3-10 MJ/K for a room |
| `r_external` | 0.00001 K/W | 10 K/W | Typical: 0.01-0.2 K/W |
| `time_constant` | 0.1 hours | 100 hours | Typical: 1-20 hours |

**Common warnings:**
- **Low thermal mass:** Room may be too small, or estimation failed
- **Low R external:** Very poor insulation (heat escapes quickly)
- **High R external:** Unrealistically good insulation
- **Long time constant:** Room responds very slowly to heating
- **Short time constant:** Room heats/cools very quickly

---

### 3. `heating_assistant.controller_performance_report`

Analyzes MPC controller performance for setpoint tracking.

**Service data:**
- `room_name` (optional) – Analyze a specific room; if omitted, analyzes all rooms

**Returns:**
- Mean tracking error and standard deviation
- Time spent above/below setpoint
- Time in deadband (±0.5°C of setpoint)
- Maximum overshoot and undershoot

**Example usage:**
```yaml
service: heating_assistant.controller_performance_report
data:
  room_name: bedroom
```

**Interpreting results:**
- **Mean error ≈ 0:** Good tracking
- **Mean error > 0:** Room consistently too warm
- **Mean error < 0:** Room consistently too cold
- **High std:** Large temperature swings (may need tuning)
- **Time in deadband > 70%:** Excellent performance
- **Time in deadband < 30%:** Poor performance, check tuning

**Tuning recommendations:**

| Issue | Suggested fix |
|-------|--------------|
| Too much oscillation | Increase `smoothing_weight` (default: 0.1) |
| Slow to reach setpoint | Decrease `energy_weight` (default: 0.01) |
| Overshoots setpoint | Decrease `constraint_offset` (default: 2.0) |
| Undershoots setpoint | Increase heater `max_power` or check parameters |

---

### 4. `heating_assistant.estimate_inter_room_resistances`

Extends parameter estimation with a second stage that fits the thermal
resistance between adjacent rooms (R_ij).

**Service data:**
- `apply_parameters` (optional, default `true`) — apply estimated R_ij immediately
- `min_temp_diff_std` (optional, default `0.3`) — identifiability threshold [°C]

**How it works:**

The service first checks each connected room pair for identifiability: if the
standard deviation of their temperature difference over the history buffer
exceeds `min_temp_diff_std`, the pair is included in the stage-2 fit.
Room pairs with similar temperatures at all times carry no information about
the resistance between them and are skipped.

**Example usage:**
```yaml
service: heating_assistant.estimate_inter_room_resistances
data:
  apply_parameters: true
  min_temp_diff_std: 0.3
```

**Interpreting results:**
- `estimated_inter_room_r` — estimated R_ij [K/W] for each identified pair
- `identifiable_connections` — which pairs were included in stage 2
- `stage2_converged` — whether the Nelder-Mead optimisation converged

**When to use:**
Run after `estimate_parameters_ml` when two or more rooms maintain different
temperatures (e.g., bedroom cooler than living room).  If rooms are always at
the same temperature, R_ij is not identifiable and stage 2 will be skipped.

---

### 5. `heating_assistant.run_open_loop_simulation`

Evaluates model quality using multi-step free-run simulation — the true test
of whether the thermal model predicts temperature evolution correctly.

**Service data:**
- `room_name` (optional) — simulate a specific room; if omitted, all rooms
- `segment_length` (optional, default `30`) — steps per free-run window

**How it works:**

The service slides non-overlapping windows of `segment_length` steps across
the history buffer.  At each window start the model is initialised from the
measured temperature; it is then propagated forward using the recorded control
inputs and disturbances **without any Kalman correction**.  Open-loop errors
accumulate over the window, exposing genuine model drift.

**Example usage:**
```yaml
service: heating_assistant.run_open_loop_simulation
data:
  segment_length: 30  # 30 steps = 30 min at 60 s/step
```

**Interpreting results:**
- Open-loop RMSE < 0.2°C over 30 steps → excellent model
- Open-loop RMSE 0.2–0.5°C → acceptable; consider re-estimating parameters
- Open-loop RMSE > 0.5°C → poor model; re-run `estimate_parameters_ml`

Results are also exposed continuously on the `OpenLoopRMSESensor` entity for
each room (see [Diagnostic Sensors](#diagnostic-sensors) below).

---

## Understanding Prediction Error Semantics

The **Prediction Error Sensor** and the **Model Fit Quality Sensor** report
*aligned* one-step-ahead prediction errors.  Understanding the alignment is
important for correctly interpreting the numbers.

### What "aligned" means

At each control update (step k) the MPC computes a one-step-ahead temperature
prediction *for the next step* (k+1).  This prediction is stored alongside the
measurement that will arrive at step k+1, so that the residual

    ε[k+1] = ŷ[k+1|k] − y[k+1]

is always computed between a prediction and the measurement it refers to.

An earlier (unaligned) implementation compared the prediction-for-k+1 against
the measurement *at k*, which produces a spuriously small error and makes the
diagnostics meaningless.

### Implications

- The first history record has no prior prediction, so `y_pred` is `None` for
  that record.  Sensors skip `None` records when computing statistics.
- After an HA restart the alignment resets; the first new record after
  restart will again have `y_pred = None`.
- The prediction error reflects the quality of the **discrete-time model used
  by the Kalman filter**, not the quality of the MPC optimisation itself.

---

## Open-Loop Simulation

### Why Kalman one-step errors are not enough

The Kalman filter is specifically designed to minimise one-step prediction
errors by continuously correcting the state estimate.  After a well-tuned
filter is running, one-step errors will always be small regardless of whether
the underlying thermal model is accurate.

To test the model itself — which is what the MPC relies on for its multi-step
planning horizon — you need to run the model **without Kalman corrections**
for several steps and see how far the free-run prediction drifts from reality.

### Open-loop simulation algorithm

1. Slide non-overlapping windows of `segment_length` steps over the history.
2. At each window start, initialise the model from the measured temperature.
3. Propagate forward using recorded control inputs and disturbances, **no state
   correction**.
4. Collect `(predicted, measured)` pairs at each step.
5. Compute RMSE and MAE over all windows and rooms.

### Interpretation guide

| Open-loop RMSE (30 steps = 30 min) | Interpretation |
|------------------------------------|----------------|
| < 0.2°C | Excellent — model accurately predicts temperature evolution |
| 0.2–0.5°C | Acceptable — minor model uncertainty |
| > 0.5°C | Poor — re-run `estimate_parameters_ml` |
| > 1.0°C | Very poor — check sensor accuracy and room configuration |

### Apex Charts card: Open-Loop RMSE history

Monitor open-loop RMSE over time as parameters are re-estimated:

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Open-Loop RMSE - Living Room
  show_states: true
graph_span: 12h
yaxis:
  - id: rmse
    min: 0
    apex_config:
      title:
        text: RMSE (°C)
      decimalsInFloat: 3
series:
  - entity: sensor.heating_assistant_living_room_open_loop_rmse
    name: Open-loop RMSE
    type: line
    stroke_width: 2
    color: "#FF9800"
    yaxis_id: rmse
    show:
      in_header: raw
```

### Apex Charts card: Open-loop simulation trajectory

Visualise the free-run simulation vs measured for the most recent segment:

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Open-Loop Simulation - Living Room
  show_states: true
graph_span: 2h
yaxis:
  - id: temp
    apex_config:
      title:
        text: Temperature (°C)
series:
  - entity: sensor.heating_assistant_living_room_open_loop_rmse
    name: Measured
    color: "#2196F3"
    stroke_width: 2
    yaxis_id: temp
    data_generator: |
      // simulation entries use ISO-8601 strings under "time"
      const sim = entity.attributes.simulation || [];
      return sim.map(e => [new Date(e.time).getTime(), e.measured]);

  - entity: sensor.heating_assistant_living_room_open_loop_rmse
    name: Open-loop predicted
    color: "#FF9800"
    stroke_width: 2
    opacity: 0.85
    yaxis_id: temp
    data_generator: |
      const sim = entity.attributes.simulation || [];
      return sim.map(e => [new Date(e.time).getTime(), e.predicted]);
```

> **Note:** prior versions of this card multiplied `e.time * 1000` because
> the sensor used to emit Unix-epoch seconds; the open-loop sensor now
> emits ISO-8601 strings directly, so the multiplication has been
> removed.

---

### Apex Charts card: Kalman innovation history

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Kalman Innovation - Living Room
  show_states: true
graph_span: 2h
yaxis:
  - id: innov
    apex_config:
      title:
        text: Innovation ν (°C)
      decimalsInFloat: 3
series:
  - entity: sensor.heating_assistant_living_room_kalman_innovation
    name: Innovation ν
    type: line
    stroke_width: 2
    color: "#9C27B0"
    yaxis_id: innov
    data_generator: |
      const ts = entity.attributes.innovations || [];
      return ts.map(e => [new Date(e.time).getTime(), e.value]);
  - entity: sensor.heating_assistant_living_room_kalman_innovation
    name: Zero
    type: line
    stroke_width: 1
    color: grey
    yaxis_id: innov
    transform: return 0;
```

Add consistency indicators:

```yaml
type: entities
title: Kalman Filter Status - Living Room
entities:
  - entity: sensor.heating_assistant_living_room_kalman_innovation
    name: Latest innovation
  - entity: sensor.heating_assistant_living_room_kalman_innovation
    type: attribute
    attribute: mean
    name: Mean innovation
    suffix: " °C"
  - entity: sensor.heating_assistant_living_room_kalman_innovation
    type: attribute
    attribute: std
    name: Innovation std
    suffix: " °C"
  - entity: sensor.heating_assistant_living_room_kalman_innovation
    type: attribute
    attribute: autocorr_lag1
    name: Autocorr lag-1
  - entity: sensor.heating_assistant_living_room_kalman_innovation
    type: attribute
    attribute: is_consistent
    name: Filter consistent?
```

**What to look for:**
- ✓ Mean innovation ≈ 0 → no systematic model bias
- ✓ `is_consistent = true` → Kalman noise covariances are well-tuned
- ⚠ |autocorr_lag1| > 0.3 → model is missing some dynamics; re-estimate or reconfigure

---

### Apex Charts card: Residual autocorrelation function (ACF)

The `ResidualACFSensor` exposes the lag-0…20 autocorrelation of the
one-step prediction residuals together with a 95 % white-noise confidence
band (`confidence_bound`).  Plotting these as a stem-and-band chart turns
the abstract Ljung-Box statistic into something a user can read at a
glance: bars within the band are statistically indistinguishable from
zero (a healthy model); bars poking outside the band indicate the model
is leaving structure on the table and parameter estimation should be
re-run.

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Residual ACF - Living Room
  show_states: true
chart_type: bar
graph_span: 1m   # ACF is lag-indexed, not time-indexed
yaxis:
  - id: rho
    min: -1
    max: 1
    apex_config:
      title:
        text: Autocorrelation ρ(k)
series:
  - entity: sensor.heating_assistant_living_room_residual_acf
    name: ACF
    yaxis_id: rho
    data_generator: |
      const acf = entity.attributes.acf || [];
      const lags = entity.attributes.lags || acf.map((_, i) => i);
      // Use the lag index as the (categorical-equivalent) timestamp so
      // apexcharts plots one bar per lag.
      return acf.map((v, i) => [lags[i], v]);
  - entity: sensor.heating_assistant_living_room_residual_acf
    name: Upper 95% CI
    type: line
    color: red
    stroke_width: 1
    yaxis_id: rho
    data_generator: |
      const lags = entity.attributes.lags || [];
      const ci = entity.attributes.confidence_bound ?? 0;
      return lags.map(k => [k, ci]);
  - entity: sensor.heating_assistant_living_room_residual_acf
    name: Lower 95% CI
    type: line
    color: red
    stroke_width: 1
    yaxis_id: rho
    data_generator: |
      const lags = entity.attributes.lags || [];
      const ci = entity.attributes.confidence_bound ?? 0;
      return lags.map(k => [k, -ci]);
```

Companion attribute card:

```yaml
type: entities
title: Residual whiteness - Living Room
entities:
  - entity: sensor.heating_assistant_living_room_residual_acf
    name: Lag-1 autocorrelation ρ(1)
  - entity: sensor.heating_assistant_living_room_residual_acf
    type: attribute
    attribute: confidence_bound
    name: 95 % white-noise band
  - entity: sensor.heating_assistant_living_room_residual_acf
    type: attribute
    attribute: ljung_box_stat
    name: Ljung-Box Q
```

**What to look for:**
- ✓ All bars (except lag-0, which is always 1) sit between ±confidence_bound → residuals are white noise
- ⚠ One or more bars escape the band → re-run `estimate_parameters_ml`
- ⚠ Ljung-Box Q growing over time → systematic model mismatch

---

### Apex Charts card: Controller tuning live view

Combine the planned heating power, the room temperature, and the
prediction error in a single time-aligned card to quickly judge whether
controller cost weights need adjustment.  This is the most useful chart
when interactively tuning `energy_weight`, `smoothing_weight`, and the
prediction `horizon`.

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Controller Tuning - Living Room
  show_states: true
graph_span: 6h
span:
  start: minute
  offset: '-4h'
now:
  show: true
  label: Now
yaxis:
  - id: temp
    apex_config:
      title:
        text: Temperature (°C)
  - id: err
    opposite: true
    apex_config:
      title:
        text: Prediction error (°C)
      decimalsInFloat: 3
  - id: power
    show: false
series:
  - entity: sensor.heating_assistant_living_room_temperature_filtered
    name: Measured / model temp
    yaxis_id: temp
    color: '#1E88E5'
    stroke_width: 2
    extend_to: now
    group_by: { func: raw, fill: last }
  - entity: sensor.heating_assistant_living_room_temperature_forecast
    name: MPC prediction
    yaxis_id: temp
    color: '#0D47A1'
    stroke_width: 2
    data_generator: |
      const fc = entity.attributes.forecast || [];
      return fc.map(e => [new Date(e.time).getTime(), e.temperature]);
  - entity: sensor.heating_assistant_living_room_temperature_forecast
    name: Setpoint
    yaxis_id: temp
    color: '#43A047'
    stroke_width: 1
    curve: stepline
    data_generator: |
      const fc = entity.attributes.forecast || [];
      return fc.map(e => [new Date(e.time).getTime(), e.setpoint]);
  - entity: sensor.heating_assistant_living_room_prediction_error
    name: Prediction error
    yaxis_id: err
    color: '#9C27B0'
    stroke_width: 1
    extend_to: now
    group_by: { func: raw, fill: last }
```

**Tuning interpretation:**
| Symptom on chart | Suggested change |
|------------------|------------------|
| MPC prediction always below setpoint by a fixed bias | ↓ `energy_weight` |
| Planned power swings between 0 and 100 % each step | ↑ `smoothing_weight` |
| Setpoint reached only at the very end of the horizon | ↑ `horizon` |
| Temperature oscillates around setpoint with > 0.5 °C swings | ↑ `smoothing_weight`, then ↑ `horizon` |
| Prediction error consistently > ±0.3 °C | Re-run `estimate_parameters_ml` (model issue, not controller) |

---

## Integration with Home Assistant Diagnostics

Model fit diagnostics are automatically included in the Home Assistant diagnostics download:

**Settings → Devices & Services → Heating Assistant → [three dots] → Download diagnostics**

The diagnostics JSON now includes a `model_fit_diagnostics` section with:
- Model fit metrics for each room
- Parameter validation results
- Controller performance metrics
- Number of data samples available

This is useful for:
- Sharing system state when reporting issues
- Tracking model quality over time
- Documenting parameter estimation results

---

## Best Practices

### 1. After Parameter Estimation

Always run these services after parameter estimation:

```yaml
# 1. Validate parameters
service: heating_assistant.validate_parameters

# 2. Analyze model fit
service: heating_assistant.analyze_model_fit

# 3. Check controller performance
service: heating_assistant.controller_performance_report
```

Look for:
- ✓ All parameters valid (confidence = 100%)
- ✓ R² > 0.8 for all rooms
- ✓ Time in deadband > 50%

### 2. Monitoring in Dashboards

Add the diagnostic sensors to your Lovelace dashboard. The following examples use the popular **ApexCharts card** (available via HACS) for rich visualizations, but basic entity cards work too.

#### Basic Diagnostic Overview

Simple entity card showing current fit metrics:

```yaml
type: entities
title: Model Fit Diagnostics - Living Room
entities:
  - entity: sensor.heating_assistant_living_room_model_fit_quality
    name: R² Score
  - entity: sensor.heating_assistant_living_room_prediction_error
    name: Current Error
  - entity: sensor.heating_assistant_living_room_parameter_confidence
    name: Parameter Confidence
```

#### Temperature Forecast with MPC Constraints

Visualize the predicted temperature trajectory, setpoint, and constraint bounds.
The historical (left of *Now*) trace is plotted directly from the HA recorder
for the room temperature sensor — **no `data_generator` is needed** for that
series, otherwise apexcharts will try to read a `forecast` attribute that does
not exist on a plain temperature sensor.

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Temperature Forecast - Living Room
  show_states: true
graph_span: 4h
span:
  start: minute
now:
  show: true
  label: Now
yaxis:
  - id: temp
    apex_config:
      title:
        text: Temperature (°C)
series:
  # Historical averaged measurement (no data_generator needed)
  - entity: sensor.heating_assistant_living_room_temperature_measured
    name: Measured
    type: line
    stroke_width: 2
    color: blue
    extend_to: now
    yaxis_id: temp
    group_by:
      func: raw
      fill: last

  # MPC predicted trajectory (from forecast attribute, future-only)
  - entity: sensor.heating_assistant_living_room_temperature_forecast
    name: Predicted
    type: line
    stroke_width: 2
    color: orange
    yaxis_id: temp
    data_generator: |
      const fc = entity.attributes.forecast || [];
      return fc.map(e => [new Date(e.time).getTime(), e.temperature]);

  # Setpoint (plain sensor — no data_generator)
  - entity: sensor.heating_assistant_living_room_setpoint
    name: Setpoint
    type: line
    stroke_width: 1
    color: green
    curve: stepline
    yaxis_id: temp
    extend_to: end
    group_by:
      func: raw
      fill: last

  # Upper constraint bound (plain sensor — no data_generator)
  - entity: sensor.heating_assistant_living_room_constraint_upper
    name: Max Constraint
    type: line
    stroke_width: 1
    color: red
    opacity: 0.3
    curve: stepline
    yaxis_id: temp
    extend_to: end
    group_by:
      func: raw
      fill: last

  # Lower constraint bound (plain sensor — no data_generator)
  - entity: sensor.heating_assistant_living_room_constraint_lower
    name: Min Constraint
    type: line
    stroke_width: 1
    color: red
    opacity: 0.3
    curve: stepline
    yaxis_id: temp
    extend_to: end
    group_by:
      func: raw
      fill: last
```

This card shows:
- **Blue line**: Historical measured temperature
- **Orange line**: MPC predicted trajectory
- **Green line**: Setpoint
- **Red lines**: MPC constraint bounds (setpoint ± constraint_offset)

**What to look for:**
- ✓ Predicted trajectory should stay within constraint bounds
- ✓ Prediction should track setpoint without excessive overshoot
- ⚠ Trajectory exceeding constraints → increase `constraint_offset`
- ⚠ Slow convergence to setpoint → decrease `energy_weight`

---

#### Heating / Cooling Power Plan

Visualize the MPC's planned schedule.  Heating power is positive; cooling
(heat removal) is plotted as a negative value, so this single card shows
both modes with a zero-line reference.

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Heating Power Plan - Living Room
  show_states: true
graph_span: 4h
span:
  start: minute
now:
  show: true
  label: Now
yaxis:
  - id: power
    apex_config:
      title:
        text: Power (W) — negative = cooling
series:
  # Current / historical heating (or cooling, signed) power
  - entity: sensor.heating_assistant_living_room_heating_power_measured
    name: Actual
    type: area
    curve: stepline
    color: orange
    opacity: 0.25
    yaxis_id: power
    extend_to: now
    group_by:
      func: raw
      fill: last

  # Planned heating power (signed: positive = heat, negative = cool)
  - entity: sensor.heating_assistant_living_room_heating_power_forecast
    name: Planned
    type: area
    curve: stepline
    color: red
    opacity: 0.4
    yaxis_id: power
    data_generator: |
      const fc = entity.attributes.forecast || [];
      return fc.map(e => [new Date(e.time).getTime(), e.heating_power]);

  # Zero reference line
  - entity: sensor.heating_assistant_living_room_heating_power_measured
    name: Zero
    type: line
    color: grey
    stroke_width: 1
    yaxis_id: power
    transform: return 0;
```

**What to look for:**
- ✓ Smooth power transitions → good `smoothing_weight`
- ⚠ Rapid on/off cycling → increase `smoothing_weight`
- ⚠ Consistently zero power when below setpoint → check parameters or increase heater `max_power`
- ⚠ Negative-going planned power means cooling is being engaged (only when a heat-pump source is configured for the room)

---

#### Prediction Error History

Monitor prediction errors over time to assess model fit quality:

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Prediction Error - Living Room
  show_states: true
graph_span: 2h
series:
  - entity: sensor.heating_assistant_living_room_prediction_error
    name: Prediction Error
    type: line
    stroke_width: 2
    color: purple
    show:
      in_header: raw

  # Zero reference line
  - entity: sensor.heating_assistant_living_room_prediction_error
    name: Zero Error
    type: line
    stroke_width: 1
    color: gray
    transform: return 0;
```

Add attribute indicators:

```yaml
type: entities
title: Prediction Error Statistics - Living Room
entities:
  - entity: sensor.heating_assistant_living_room_prediction_error
    type: attribute
    attribute: rmse
    name: RMSE
    suffix: " °C"
  - entity: sensor.heating_assistant_living_room_prediction_error
    type: attribute
    attribute: mae
    name: MAE
    suffix: " °C"
  - entity: sensor.heating_assistant_living_room_prediction_error
    type: attribute
    attribute: bias
    name: Bias
    suffix: " °C"
  - entity: sensor.heating_assistant_living_room_prediction_error
    type: attribute
    attribute: max_error
    name: Max Error
    suffix: " °C"
```

**What to look for:**
- ✓ Errors centered around zero (no systematic bias)
- ✓ RMSE < 0.3°C → excellent fit
- ⚠ Consistent positive/negative bias → parameter estimation issue
- ⚠ RMSE > 0.5°C → poor fit, re-estimate parameters

---

#### Model Fit Quality Overview

Create a glance card for quick fit assessment:

```yaml
type: glance
title: Model Fit Quality
columns: 3
entities:
  - entity: sensor.heating_assistant_living_room_model_fit_quality
    name: R² Score
  - entity: sensor.heating_assistant_living_room_prediction_error
    name: Current Error
  - entity: sensor.heating_assistant_living_room_parameter_confidence
    name: Parameter Confidence
```

Or a more detailed metrics card:

```yaml
type: entities
title: Model Fit Metrics - Living Room
entities:
  - type: section
    label: Fit Quality
  - entity: sensor.heating_assistant_living_room_model_fit_quality
    name: R² Score
  - entity: sensor.heating_assistant_living_room_model_fit_quality
    type: attribute
    attribute: rmse
    name: RMSE
    suffix: " °C"
  - entity: sensor.heating_assistant_living_room_model_fit_quality
    type: attribute
    attribute: mae
    name: MAE
    suffix: " °C"
  - entity: sensor.heating_assistant_living_room_model_fit_quality
    type: attribute
    attribute: bias
    name: Bias
    suffix: " °C"
  - entity: sensor.heating_assistant_living_room_model_fit_quality
    type: attribute
    attribute: residual_autocorr_lag1
    name: Autocorrelation
  - type: section
    label: Parameters
  - entity: sensor.heating_assistant_living_room_parameter_confidence
    name: Confidence
  - entity: sensor.heating_assistant_living_room_temperature_filtered
    type: attribute
    attribute: thermal_mass
    name: Thermal Mass
    suffix: " J/K"
  - entity: sensor.heating_assistant_living_room_temperature_filtered
    type: attribute
    attribute: r_external
    name: R External
    suffix: " K/W"
  - entity: sensor.heating_assistant_living_room_parameter_confidence
    type: attribute
    attribute: time_constant_hours
    name: Time Constant
    suffix: " hours"
```

---

#### Multi-Room Performance Comparison

Compare model fit quality across all rooms:

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Model Fit Quality - All Rooms
graph_span: 12h
series:
  - entity: sensor.heating_assistant_living_room_model_fit_quality
    name: Living Room
    type: line
    stroke_width: 2
  - entity: sensor.heating_assistant_bedroom_model_fit_quality
    name: Bedroom
    type: line
    stroke_width: 2
  - entity: sensor.heating_assistant_kitchen_model_fit_quality
    name: Kitchen
    type: line
    stroke_width: 2
```

Or a bar chart for current R² scores:

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Current R² Scores by Room
chart_type: bar
series:
  - entity: sensor.heating_assistant_living_room_model_fit_quality
    name: Living Room
    color: blue
  - entity: sensor.heating_assistant_bedroom_model_fit_quality
    name: Bedroom
    color: green
  - entity: sensor.heating_assistant_kitchen_model_fit_quality
    name: Kitchen
    color: orange
```

---

#### Complete Dashboard Example

Here's a full dashboard view combining all elements:

```yaml
title: Heating Assistant - Model Diagnostics
path: heating-diagnostics
cards:
  # Row 1: Overview
  - type: glance
    title: System Overview
    columns: 4
    entities:
      - entity: sensor.heating_assistant_living_room_model_fit_quality
        name: Living Room R²
      - entity: sensor.heating_assistant_bedroom_model_fit_quality
        name: Bedroom R²
      - entity: sensor.heating_assistant_kitchen_model_fit_quality
        name: Kitchen R²
      - entity: sensor.heating_assistant_outdoor_temperature_measured
        name: Outdoor Temp

  # Row 2: Living Room Detailed Analysis
  - type: horizontal-stack
    cards:
      # Temperature forecast
      - type: custom:apexcharts-card
        header:
          show: true
          title: Living Room - Temperature
        graph_span: 4h
        span:
          start: minute
        now:
          show: true
          label: Now
        series:
          - entity: sensor.heating_assistant_living_room_temperature_measured
            name: Measured
            type: line
            stroke_width: 2
            color: blue
            show:
              in_header: before_now
          - entity: sensor.heating_assistant_living_room_temperature_forecast
            name: Predicted
            type: line
            stroke_width: 2
            color: orange
            data_generator: |
              return entity.attributes.forecast.map((entry) => {
                return [new Date(entry.time).getTime(), entry.temperature];
              });
            show:
              in_header: after_now
          - entity: sensor.heating_assistant_living_room_setpoint
            name: Setpoint
            type: line
            stroke_width: 1
            color: green
            curve: stepline
            extend_to: end
            group_by:
              func: raw
              fill: last

      # Heating plan
      - type: custom:apexcharts-card
        header:
          show: true
          title: Living Room - Heating Plan
        graph_span: 4h
        span:
          start: minute
        now:
          show: true
          label: Now
        series:
          - entity: sensor.heating_assistant_living_room_heating_power_measured
            name: Current
            type: column
            color: orange
            show:
              in_header: before_now
          - entity: sensor.heating_assistant_living_room_heating_power_forecast
            name: Planned
            type: column
            color: red
            opacity: 0.6
            data_generator: |
              return entity.attributes.forecast.map((entry) => {
                return [new Date(entry.time).getTime(), entry.heating_power];
              });
            show:
              in_header: after_now

  # Row 3: Model Fit Details
  - type: horizontal-stack
    cards:
      # Prediction error
      - type: custom:apexcharts-card
        header:
          show: true
          title: Living Room - Prediction Error
        graph_span: 2h
        series:
          - entity: sensor.heating_assistant_living_room_prediction_error
            name: Error
            type: line
            stroke_width: 2
            color: purple
          - entity: sensor.heating_assistant_living_room_prediction_error
            name: Zero
            type: line
            stroke_width: 1
            color: gray
            transform: return 0;

      # Fit metrics
      - type: entities
        title: Living Room - Fit Metrics
        entities:
          - entity: sensor.heating_assistant_living_room_model_fit_quality
            name: R² Score
          - entity: sensor.heating_assistant_living_room_model_fit_quality
            type: attribute
            attribute: rmse
            name: RMSE
            suffix: " °C"
          - entity: sensor.heating_assistant_living_room_model_fit_quality
            type: attribute
            attribute: bias
            name: Bias
            suffix: " °C"
          - entity: sensor.heating_assistant_living_room_parameter_confidence
            name: Parameter Confidence
          - entity: sensor.heating_assistant_living_room_parameter_confidence
            type: attribute
            attribute: time_constant_hours
            name: Time Constant
            suffix: " h"
```

---

Create alert automations:

```yaml
automation:
  - alias: "Alert on Poor Model Fit"
    trigger:
      - platform: numeric_state
        entity_id: sensor.heating_assistant_living_room_model_fit_quality
        below: 0.7
        for:
          hours: 2
    action:
      - service: notify.mobile_app
        data:
          message: "Heating model fit quality is poor. Consider re-estimating parameters."
```

### 3. Troubleshooting Poor Fit

If R² < 0.7:

1. **Check measurement quality:**
   - Are temperature sensors accurate?
   - Are they in representative locations?
   - Multiple sensors per room improve estimates

2. **Check parameter reasonableness:**
   - Run `validate_parameters` service
   - Verify thermal_mass and r_external are realistic
   - Time constant should be 1-20 hours for typical rooms

3. **Check for external factors:**
   - Unmodeled heat sources (appliances, people, pets)
   - Window opening/closing not accounted for
   - Air leakage or drafts

4. **Re-estimate parameters:**
   - Use the ML estimation button or service
   - Ensure sufficient data (30+ samples, ~30 minutes)
   - Perform estimation during stable weather

5. **Check open-loop RMSE:**
   - Run `heating_assistant.run_open_loop_simulation`
   - Open-loop RMSE > 0.5°C → model parameters need re-estimation even if
     one-step Kalman errors look small (Kalman corrections mask single-step errors)
   - After re-estimating, verify open-loop RMSE has dropped

6. **Check controller configuration:**
   - Verify `outdoor_temp_entity` is correct
   - Check `weather_entity` for forecast quality
   - Ensure solar gain modeling is accurate

---

## Technical Details

### Goodness-of-Fit Metrics

**Root Mean Squared Error (RMSE):**
```
RMSE = sqrt(mean((predicted - measured)²))
```
Lower is better. Units: °C.

**Mean Absolute Error (MAE):**
```
MAE = mean(|predicted - measured|)
```
Lower is better. Units: °C.

**Coefficient of Determination (R²):**
```
R² = 1 - (SS_residual / SS_total)
where SS_residual = Σ(predicted - measured)²
      SS_total = Σ(measured - mean(measured))²
```
Range: (-∞, 1]. Higher is better. 1.0 = perfect fit.

**Bias:**
```
Bias = mean(predicted - measured)
```
Should be near zero. Positive = over-prediction, negative = under-prediction.

### Residual Autocorrelation

Lag-1 autocorrelation measures correlation between consecutive residuals:
```
ρ(1) = corr(residuals[:-1], residuals[1:])
```

- **ρ ≈ 0:** Good (residuals are white noise, model captures dynamics)
- **|ρ| > 0.3:** Poor (model missing some dynamics)

### Parameter Validation Bounds

Based on physical principles and typical building properties:

- **Thermal mass:** Heat capacity of air, furniture, walls, floors
  - Minimum: Small, empty, well-insulated room
  - Maximum: Large room with heavy thermal mass (concrete, brick)

- **External resistance:** Thermal resistance to outdoors
  - Minimum: Very poor insulation, large surface area
  - Maximum: Excellent insulation, small surface area

- **Time constant:** τ = R × C (response speed)
  - Short τ: Responds quickly (lightweight, poor insulation)
  - Long τ: Responds slowly (heavy thermal mass, good insulation)

---

## Examples

### Example 1: Good Model Fit

```
Model Fit Quality: 0.95
RMSE: 0.18°C
MAE: 0.14°C
Bias: +0.02°C
Residual autocorr: 0.08

Interpretation:
✓ Excellent fit (R² > 0.9)
✓ Small errors (< 0.2°C)
✓ No systematic bias
✓ Low autocorrelation (model captures dynamics)
Action: None required
```

### Example 2: Systematic Bias

```
Model Fit Quality: 0.88
RMSE: 0.45°C
MAE: 0.42°C
Bias: +0.40°C
Residual autocorr: 0.05

Interpretation:
⚠ Model consistently over-predicts by 0.4°C
⚠ May indicate:
  - thermal_mass too high
  - r_external too high
  - unmodeled heat loss (air leakage, etc.)
Action: Re-estimate parameters or check for drafts
```

### Example 3: Missing Dynamics

```
Model Fit Quality: 0.72
RMSE: 0.62°C
MAE: 0.48°C
Bias: -0.05°C
Residual autocorr: 0.42

Interpretation:
⚠ Poor fit with high autocorrelation
⚠ Model not capturing room dynamics correctly
⚠ May indicate:
  - Time constant incorrect
  - Inter-room connections misconfigured
  - Heating source model incorrect
Action: Check thermal_mass and r_external, verify connections
```

### Example 4: Invalid Parameters

```
Parameter Confidence: 33.3%
Thermal mass: 800,000 J/K (⚠ unusually low)
R external: 0.08 K/W (✓)
Time constant: 0.02 hours (⚠ unusually short)

Warnings:
- Thermal mass 800000 J/K is unusually low (< 10000 J/K)
- Time constant 0.02 hours is unusually short (< 0.1 hours)

Interpretation:
⚠ Parameter estimation likely failed
Action:
1. Check temperature sensor accuracy
2. Verify heater max_power is correct
3. Re-run parameter estimation with more data
```

---

## FAQ

**Q: What is a good R² score?**
A: R² > 0.9 is excellent, 0.7-0.9 is good, < 0.7 indicates problems.

**Q: How much data is needed for accurate metrics?**
A: At least 30 samples (~30 minutes at 60s update interval). More is better—several hours provides more reliable estimates.

**Q: Why is my residual autocorrelation high?**
A: High autocorrelation suggests the model is missing some dynamics. Check that thermal_mass and r_external are accurate, and that inter-room connections are properly configured.

**Q: What if parameter confidence is low?**
A: Low confidence indicates parameters outside physically reasonable ranges. Re-run parameter estimation with more data, or manually adjust parameters based on building characteristics.

**Q: Can I export diagnostic data?**
A: Yes, use the Home Assistant diagnostics download feature. The JSON file includes all model fit metrics.

**Q: How often should I check model fit?**
A: After initial setup, weekly. After parameter re-estimation, immediately. Add sensors to your dashboard for continuous monitoring.

---

## See Also

- [Parameter Estimation Guide](parameter_estimation.md)
- [MPC Controller Tuning](controller_tuning.md)
- [Thermal Model Configuration](thermal_model.md)
- [Home Assistant Diagnostics](https://www.home-assistant.io/docs/configuration/troubleshooting/#download-diagnostics)
