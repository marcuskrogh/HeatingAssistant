# Services Reference

> The setup, diagnostic and system-identification services Heating Assistant
> registers. Each is callable from **Developer Tools → Actions**, automations,
> or the bundled dashboards.

These services help you verify configuration, estimate thermal parameters, run
open-loop validation, and manage identification datasets. The authoritative
field definitions live in
[`custom_components/heating_assistant/services.yaml`](../custom_components/heating_assistant/services.yaml);
this page documents the most important ones in context. For the sensors many of
these services write to, see the [Entities reference](ENTITIES.md); for tuning
guidance, see the [Parameter Estimation & Tuning guide](TUNING.md).

---

### 13.10 Setup service – simulate thermal response

Service: `heating_assistant.simulate_thermal_response`

This service runs a standalone thermal simulation to show how a room responds to constant heating power.  It helps answer the question: *"Is my heater powerful enough to keep this room at 21 °C when it is −10 °C outside?"*

**Service data:**

| Field | Type | Description |
|-------|------|-------------|
| `room_name` | string | Room to simulate |
| `initial_temp` | float | Starting temperature [°C] |
| `outdoor_temp` | float | Outdoor temperature [°C] |
| `heating_power` | float | Constant heating power [W] |
| `duration_hours` | float | Simulation duration [hours] |

**Example call (Developer Tools → Services):**

```yaml
service: heating_assistant.simulate_thermal_response
data:
  room_name: "living_room"
  initial_temp: 10.0
  outdoor_temp: -5.0
  heating_power: 3000
  duration_hours: 12
```

**Result:** A persistent notification is created containing:
- Temperature trajectory (sampled every 5 minutes)
- Final temperature reached
- Steady-state temperature (what the room would reach if heated indefinitely)
- Time constant (how quickly the room responds — 63 % of final value in 1 τ)

An event `heating_assistant_simulation_result` is also fired, which automations can consume.

### 13.11 Setup service – estimate parameters

Service: `heating_assistant.estimate_parameters`

This service uses **maximum likelihood estimation (MLE)** to identify thermal model parameters from historical operational data collected by the MPC controller.  The estimator jointly optimizes:

- **Thermal mass** `thermal_mass` [J/K] for each room
- **External thermal resistance** `r_external` [K/W] for each room
- **Internal heat gain** `internal_gain` [W] for each room (constant sources like appliances, occupants, etc.)
- **Heater power-scale** correction factors for each heat source (to account for miscalibration or efficiency degradation)
- **Inter-room thermal resistance** `r_value` [K/W] for connections with sufficient temperature-difference variation

The estimator uses a **continuous-discrete Extended Kalman Filter (CD-EKF)** prediction-error decomposition (PED) to evaluate the Gaussian log-likelihood of each candidate parameter set.  The CD-EKF handles the nonlinear heat-pump COP dynamics directly in continuous time, integrating the state and covariance between discrete measurements using implicit-Euler sub-stepping.  A multi-start optimizer searches the parameter space, with automatic **identifiability gating** to exclude parameters that the data cannot constrain (e.g., heater scales when heating fraction is constant, inter-room resistances when adjacent rooms track each other closely).

**When to use:**

- After the system has been running for at least **8–12 hours** with normal heating activity (the more data, the better).
- When you suspect your initial parameter guesses are significantly wrong (the MPC predictions consistently deviate from measured temperatures).
- To refine parameters after major changes (new insulation, furniture rearrangement, heater replacement).

**How it works:**

1. The service reads the accumulated **history buffer** (up to 480 time-steps = 8 hours at 1-minute sampling).
2. For each candidate parameter set, the **continuous-discrete Extended Kalman filter (CD-EKF)** forward-propagates the state estimate and covariance through the history by integrating the continuous-time drift ODE and linearised Riccati ODE between discrete measurements, computing the innovation log-likelihood at each measurement step.
3. The optimizer searches for the parameter set that maximizes this likelihood, subject to:
   - Physical bounds (thermal masses in [10 kJ/K, 500 MJ/K], resistances in [10 µK/W, 10 K/W], etc.)
   - Gaussian regularization shrinking parameters toward their prior (the current configured values) to prevent overfitting when data quality is poor.
4. The service returns the optimized parameters as a persistent notification (copy-paste into `configuration.yaml` to apply them).

**Service data:**

The service accepts optional parameters to control the estimation:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `regularization` | float | `1.0` | Weight of the Gaussian prior shrinking parameters toward their current values.  Increase (e.g. `10.0`) if estimates are unstable; decrease (e.g. `0.1`) if you want the data to dominate. |

**Example call:**

```yaml
service: heating_assistant.estimate_parameters
data:
  regularization: 1.0
```

**Result:** A persistent notification listing:

- **Estimated parameters** for each room: `thermal_mass`, `r_external`, `internal_gain`
- **Estimated heater scales** for sources whose duty-cycle varied enough during the window
- **Estimated inter-room resistances** for connections with sufficient temperature-difference variation
- **Estimated solar scales** for rooms whose recorded solar gain varied enough during the window (gate: std ≥ 30 W) — corrects the configured window/preset aperture for shading, curtains, and preset error
- **Estimated envelope splits** (`c_air_fraction`, `r_aw_fraction` of the 2R2C model) for rooms with an excited heat source — these are held on a tight prior leash and only move when the data carry real fast/slow excitation
- **Log-likelihood** of the optimized model (higher is better; compare before/after to assess improvement)
- **Convergence status** and identifiability diagnostics

**Tips for good results:**

- **Excite the system**: ensure heaters turn on and off during the observation window (vary setpoints or let the MPC cycle naturally).
- **Vary outdoor conditions**: estimation works best when outdoor temperature changes by at least 5–10 °C over the window.
- **Check identifiability**: if a parameter is flagged as "not identifiable", it means the data didn't contain enough information to constrain it—keep the system running longer or manually vary the relevant input.
- **Start with weak regularization** (`0.1`) if you have high-quality data (12+ hours, lots of heating cycles); use strong regularization (`10.0`) if data is noisy or sparse.

### 13.12 Setup service – estimate parameters (ML)

Service: `heating_assistant.estimate_parameters_ml`

This service fits `thermal_mass` and `r_external` for all rooms simultaneously using **maximum-likelihood optimisation** of the Kalman-filter prediction-error decomposition (PED) log-likelihood.  Unlike the manual `estimate_parameters` service, this approach uses the automatically accumulated rolling history buffer so no separate heating experiment is needed.  At least 30 history steps (~30 minutes of operation) must be present for meaningful estimates.

**Service data:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `apply_parameters` | bool | `true` | When `true` the estimated values are applied to the running model immediately.  Set `false` for a dry-run report without modifying anything. |

**Example call:**

```yaml
service: heating_assistant.estimate_parameters_ml
data:
  apply_parameters: true
```

**Result:** A persistent notification with per-room estimated `thermal_mass` and `r_external`, log-likelihood improvement, and — for room pairs with sufficient temperature-difference variance — estimated inter-room thermal resistances $R_{ij}$.  Inter-room resistances are fitted in the same joint optimisation as the per-room parameters; no separate service call is needed.

### 13.13 Diagnostic service – analyze model fit

Service: `heating_assistant.analyze_model_fit`

Runs a comprehensive model-fit analysis using the history buffer.  Computes RMSE, MAE, R², bias, residual autocorrelation (Ljung–Box Q test), and identifies whether model parameters need adjustment.

**Service data:**

| Field | Type | Description |
|-------|------|-------------|
| `room_name` | string | Optional room name.  If omitted, all rooms are analysed. |

### 13.14 Diagnostic service – validate parameters

Service: `heating_assistant.validate_parameters`

Validates the physical reasonableness of the current `thermal_mass` and `r_external` values: checks that they are within expected ranges, computes thermal time constants, and warns about extreme or potentially invalid values.

**Service data:**

| Field | Type | Description |
|-------|------|-------------|
| `room_name` | string | Optional room name.  If omitted, all rooms are validated. |

### 13.15 Diagnostic service – controller performance report

Service: `heating_assistant.controller_performance_report`

Generates a detailed setpoint-tracking report: tracking errors, overshoot/undershoot statistics, time spent above/below setpoint, and an overall control quality score.  Useful for deciding whether `energy_weight`, `smoothing_weight`, or `constraint_offset` need adjustment.

**Service data:**

| Field | Type | Description |
|-------|------|-------------|
| `room_name` | string | Optional room name.  If omitted, all rooms are included. |

### 13.16 Diagnostic service – run open-loop simulation

Service: `heating_assistant.run_open_loop_simulation`

Evaluates model quality by running a free-run (open-loop) multi-step simulation over the history buffer — without Kalman correction at each step — and comparing predictions against observed temperatures.  This is a much stricter test than one-step-ahead Kalman prediction error because accumulated drift is exposed.

**Service data:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `room_name` | string | — | Optional room name.  If omitted, all rooms are simulated. |
| `segment_length` | int | `30` | Number of history steps per segment (each step = one coordinator cycle, i.e. `update_interval` seconds).  Default 30 steps. |

**Rule of thumb:** open-loop RMSE < 0.2 °C over 30 steps is excellent; > 0.5 °C suggests the model should be re-estimated.

