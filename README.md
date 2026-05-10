# Heating Assistant

> **A Home Assistant custom integration that brings model-based (MPC) temperature control to every room of your home.**

Heating Assistant replaces simple on/off or PID thermostats with a physics-based predictive controller.  It models your house room-by-room, accounts for heat flowing between rooms and to the outdoors, factors in solar heat gain through each window, and computes optimal heater set-points by looking ahead over a configurable prediction horizon.  The result is tighter temperature tracking and lower energy consumption compared to reactive control strategies.

---

## Table of Contents

1. [Features](#1-features)
2. [System Architecture](#2-system-architecture)
   - 2.1 [File layout](#21-file-layout)
   - 2.2 [Data flow](#22-data-flow)
3. [Physics and Mathematical Models](#3-physics-and-mathematical-models)
   - 3.1 [Lumped RC thermal model](#31-lumped-rc-thermal-model)
   - 3.2 [State-space matrix form](#32-state-space-matrix-form)
   - 3.3 [Numerical integration](#33-numerical-integration)
   - 3.4 [Solar heat gain model](#34-solar-heat-gain-model)
   - 3.5 [Heat source models](#35-heat-source-models)
4. [Model Predictive Controller](#4-model-predictive-controller)
   - 4.1 [Overview](#41-overview)
   - 4.2 [State estimation — Kalman filter](#42-state-estimation--kalman-filter)
   - 4.3 [Optimal control problem — batch QP](#43-optimal-control-problem--batch-qp)
   - 4.4 [Disturbance forecasts](#44-disturbance-forecasts)
   - 4.5 [Control cycle](#45-control-cycle)
5. [Home Assistant Integration](#5-home-assistant-integration)
   - 5.1 [Platforms and entities](#51-platforms-and-entities)
   - 5.2 [Heater entity dispatch](#52-heater-entity-dispatch)
   - 5.3 [Update cadence](#53-update-cadence)
6. [Requirements and Compatibility](#6-requirements-and-compatibility)
7. [Installation](#7-installation)
   - 7.1 [Manual installation](#71-manual-installation)
   - 7.2 [HACS installation (future)](#72-hacs-installation-future)
8. [Setting Up Your First Heating System](#8-setting-up-your-first-heating-system)
   - 8.1 [Prerequisites](#81-prerequisites)
   - 8.2 [Step 1 – Run the UI setup wizard](#82-step-1--run-the-ui-setup-wizard)
   - 8.3 [Step 2 – Plan your room topology](#83-step-2--plan-your-room-topology)
   - 8.4 [Step 3 – Identify your HA entities](#84-step-3--identify-your-ha-entities)
   - 8.5 [Step 4 – Estimate thermal parameters](#85-step-4--estimate-thermal-parameters)
   - 8.6 [Step 5 – Write the YAML configuration](#86-step-5--write-the-yaml-configuration)
   - 8.7 [Step 6 – Restart Home Assistant](#87-step-6--restart-home-assistant)
   - 8.8 [Step 7 – Verify entities are created](#88-step-7--verify-entities-are-created)
   - 8.9 [Step 8 – Set your temperature setpoints](#89-step-8--set-your-temperature-setpoints)
   - 8.10 [Step 9 – Confirm heater control is active](#810-step-9--confirm-heater-control-is-active)
   - 8.11 [Step 10 – Monitor and tune](#811-step-10--monitor-and-tune)
9. [Setup Wizard](#9-setup-wizard)
10. [Configuration Reference](#10-configuration-reference)
    - 10.1 [Top-level keys](#101-top-level-keys)
    - 10.2 [Room block (`rooms`)](#102-room-block-rooms)
    - 10.3 [Connection block (`connections`)](#103-connection-block-connections)
    - 10.4 [Window block (`windows`)](#104-window-block-windows)
    - 10.5 [Heat source block (`heat_sources`)](#105-heat-source-block-heat_sources)
    - 10.6 [Comfort schedule block (`schedule`)](#106-comfort-schedule-block-schedule)
11. [Complete Configuration Examples](#11-complete-configuration-examples)
    - 11.1 [Studio apartment – single room, one electric heater](#111-studio-apartment--single-room-one-electric-heater)
    - 11.2 [Two-bedroom flat – rooms with heat pump and supplemental heater](#112-two-bedroom-flat--rooms-with-heat-pump-and-supplemental-heater)
    - 11.3 [Full house – five rooms, heat pump, and solar-facing windows](#113-full-house--five-rooms-heat-pump-and-solar-facing-windows)
    - 11.4 [Multiple temperature sensors per room](#114-multiple-temperature-sensors-per-room)
    - 11.5 [Comfort schedules – sleep mode and weekday setback](#115-comfort-schedules--sleep-mode-and-weekday-setback)
12. [Entity Reference](#12-entity-reference)
    - 12.1 [Climate entities](#121-climate-entities)
    - 12.2 [Sensor entities – predicted temperature](#122-sensor-entities--predicted-temperature)
    - 12.3 [Sensor entities – heating power](#123-sensor-entities--heating-power)
    - 12.4 [Sensor entities – solar gain](#124-sensor-entities--solar-gain)
    - 12.5 [Sensor entities – temperature forecast](#125-sensor-entities--temperature-forecast)
    - 12.6 [Sensor entities – heat loss](#126-sensor-entities--heat-loss)
    - 12.7 [Sensor entities – energy balance](#127-sensor-entities--energy-balance)
    - 12.8 [Sensor entities – control action](#128-sensor-entities--control-action)
    - 12.9 [Sensor entities – heat pump COP](#129-sensor-entities--heat-pump-cop)
    - 12.10 [Sensor entities – outdoor temperature](#1210-sensor-entities--outdoor-temperature)
    - 12.11 [Sensor entities – system summary](#1211-sensor-entities--system-summary)
    - 12.12 [Sensor entities – heating plan](#1212-sensor-entities--heating-plan)
    - 12.13 [Sensor entities – solar forecast](#1213-sensor-entities--solar-forecast)
    - 12.14 [Sensor entities – outdoor temperature forecast](#1214-sensor-entities--outdoor-temperature-forecast)
13. [Advanced Visualisation and Setup Tools](#13-advanced-visualisation-and-setup-tools)
    - 13.1 [Visualisation sensors overview](#131-visualisation-sensors-overview)
    - 13.2 [Temperature forecast trajectory](#132-temperature-forecast-trajectory)
    - 13.3 [Heat loss analysis](#133-heat-loss-analysis)
    - 13.4 [Energy balance](#134-energy-balance)
    - 13.5 [System efficiency summary](#135-system-efficiency-summary)
    - 13.6 [Heating plan forecast](#136-heating-plan-forecast)
    - 13.7 [Solar gain forecast](#137-solar-gain-forecast)
    - 13.8 [Outdoor temperature forecast](#138-outdoor-temperature-forecast)
    - 13.9 [Diagnostics panel](#139-diagnostics-panel)
    - 13.10 [Setup service – simulate thermal response](#1310-setup-service--simulate-thermal-response)
    - 13.11 [Setup service – estimate parameters](#1311-setup-service--estimate-parameters)
    - 13.12 [Setup service – estimate parameters (ML)](#1312-setup-service--estimate-parameters-ml)
    - 13.13 [Diagnostic service – analyze model fit](#1313-diagnostic-service--analyze-model-fit)
    - 13.14 [Diagnostic service – validate parameters](#1314-diagnostic-service--validate-parameters)
    - 13.15 [Diagnostic service – controller performance report](#1315-diagnostic-service--controller-performance-report)
    - 13.16 [Diagnostic service – run open-loop simulation](#1316-diagnostic-service--run-open-loop-simulation)
    - 13.17 [Lovelace dashboard – board and card reference](#1317-lovelace-dashboard--board-and-card-reference)
        - 13.17.1 [Prerequisites](#13171-prerequisites)
        - 13.17.2 [Dashboard structure – board with room subboards](#13172-dashboard-structure--board-with-room-subboards)
        - 13.17.3 [MPC predicted temperature card](#13173-mpc-predicted-temperature-card)
        - 13.17.4 [MPC control input card](#13174-mpc-control-input-card)
        - 13.17.5 [Disturbance forecast card](#13175-disturbance-forecast-card)
        - 13.17.6 [Room performance card](#13176-room-performance-card)
        - 13.17.7 [System overview card](#13177-system-overview-card)
        - 13.17.8 [Complete room subboard example](#13178-complete-room-subboard-example)
14. [Thermal Model Parameter Estimation Guide](#14-thermal-model-parameter-estimation-guide)
    - 14.1 [Thermal mass `thermal_mass`](#141-thermal-mass-thermal_mass)
    - 14.2 [External thermal resistance `r_external`](#142-external-thermal-resistance-r_external)
    - 14.3 [Inter-room thermal resistance `r_value`](#143-inter-room-thermal-resistance-r_value)
    - 14.4 [Window orientation and tilt](#144-window-orientation-and-tilt)
    - 14.5 [MPC regulator tuning](#145-mpc-regulator-tuning)
        - 14.5.1 [Overview of tunable parameters](#1451-overview-of-tunable-parameters)
        - 14.5.2 [Diagnosing and correcting oscillations](#1452-diagnosing-and-correcting-oscillations)
        - 14.5.3 [Step-by-step detuning procedure](#1453-step-by-step-detuning-procedure)
        - 14.5.4 [Effect of `smoothing_weight` on heat pump short-cycling](#1454-effect-of-smoothing_weight-on-heat-pump-short-cycling)
        - 14.5.5 [Quick reference — tuning cheat sheet](#1455-quick-reference--tuning-cheat-sheet)
        - 14.5.6 [Live tuning chart](#1456-live-tuning-chart)
        - 14.5.7 [Monitoring MPC performance with the performance sensor](#1457-monitoring-mpc-performance-with-the-performance-sensor)
15. [Developer Guide](#15-developer-guide)
    - 15.1 [Repository layout](#151-repository-layout)
    - 15.2 [Running the tests](#152-running-the-tests)
    - 15.3 [Performance benchmarks](#153-performance-benchmarks)
    - 15.4 [Adding a new heat source type](#154-adding-a-new-heat-source-type)
    - 15.5 [Extending the solar model](#155-extending-the-solar-model)
16. [Troubleshooting](#16-troubleshooting)
17. [Roadmap](#17-roadmap)
18. [References](#18-references)

---

## 1. Features

| Feature | Detail |
|---------|--------|
| **Room-by-room thermal model** | Each room is an independent RC thermal node.  Heat flows between adjacent rooms and to the outdoors through configurable thermal resistances. |
| **Solar heat gain disturbances** | Every window is modelled individually: area, compass orientation, and tilt angle feed a clear-sky solar irradiance pipeline to produce a time-varying heat gain in Watts. |
| **Electric heater support** | Resistive heaters and infrared panels modelled as `Q_thermal = P_electrical × η`.  Efficiency is configurable. |
| **Air-source heat pump support** | Temperature-dependent COP based on Carnot scaling.  The pump shuts off automatically below a configurable outdoor temperature floor to prevent defrost damage.  Offset-based setpoint control (`max_temp_offset`) lets the heat pump modulate output via the gap between its internal sensor and the target temperature. |
| **Multiple heat sources per room** | Any number of heaters and/or heat pumps can be assigned to the same room; the controller optimises them jointly. |
| **Turn-off deadband** | Two-threshold Schmitt-trigger hysteresis around the setpoint (width = 2 × `turn_off_deadband`).  Passive cooling engages only when `room_temp > setpoint + deadband`, and disengages only when `room_temp < setpoint − deadband`.  Between the thresholds the current mode is held, preventing rapid toggling. |
| **Receding-horizon NMPC** | Each control cycle the controller solves a nonlinear program (NLP) over the prediction horizon to find the continuous input sequence that minimises a cost of temperature tracking error, energy use, and input rate-of-change (Δu smoothing).  Inputs are applied via zero-order hold. |
| **CD-EKF state estimation** | A continuous-discrete Extended Kalman Filter (CD-EKF) integrates the nonlinear thermal SDE and linearised Riccati ODE between measurements, providing the minimum-variance state estimate for the nonlinear house thermal model. |
| **Generic MBC framework** | The controller is built on the `mbc` (model-based control) package, providing `ContinuousDiscreteModel`, `ContinuousDiscreteEKF`, and `CDTrackingOptimalControlProblem` components that can be reused for any continuous-discrete nonlinear system. |
| **HA climate entities** | One `climate.*` entity per room exposes setpoint, current temperature, HVAC mode and action in the standard HA interface.  Heat-pump-equipped rooms additionally advertise `heat_cool` mode and report `cooling` as the HVAC action while the integration drives the heat pump in dry / fan-only mode. |
| **Cooling-aware visualisation** | Cooling capacity is derived from a separate `cooling_cop` (EER) so the advanced visualisation no longer treats the rated *heating* output as the cooling capability.  Per-room Heating Power, Heating Plan, and Energy Balance sensors expose signed power (positive = heating, negative = cooling) so a single ApexCharts series shows both modes. |
| **HA sensor entities** | Predicted temperature and active heating power sensors per room, with model metadata exposed as state attributes. |
| **Advanced visualisation sensors** | Temperature forecast trajectory, heat loss breakdown, energy balance, and system efficiency sensors provide deep insight into system operation.  Forecast data includes a "now" bridge point for seamless connection between recorder history and MPC predictions, and setpoints are included in every forecast entry. |
| **Weather forecast integration** | Optionally configure a HA weather entity (e.g. Met.no, OpenWeatherMap) to provide outdoor temperature forecasts.  The controller interpolates the forecast to each MPC horizon step for improved prediction accuracy during temperature transitions. |
| **Comfort schedule (sleep / setback)** | Each room can declare time-of-day periods that lower the setpoint or completely switch off heat sources during the night, while the user is at work, etc.  The MPC's prediction horizon naturally produces preheat — the room is back at the comfort setpoint when the next period starts.  Optional frost-protection floor prevents pipes from freezing during off periods.  A runtime service can suspend or resume the schedule for one-off exceptions (e.g. staying up late). |
| **Setup assistance services** | `simulate_thermal_response` and `estimate_parameters` services help you verify and tune your configuration by running simulations and back-calculating thermal parameters. |
| **Diagnostics platform** | Full system state dump accessible via the HA diagnostics panel for troubleshooting — includes model matrices, predictions, heat flows, and steady-state analysis. |
| **Flexible heater entity control** | Automatically dispatches to `switch.*`, `number.*`, or `climate.*` entities depending on the HA domain of each configured heater. |
| **YAML + UI config** | Room topology and heat sources are declared in `configuration.yaml`.  Site-level settings (location, time step, horizon) are configured through the HA UI setup wizard. |

---

## 2. System Architecture

### 2.1 File layout

```
custom_components/heating_assistant/
│
├── manifest.json          Integration metadata: name, version, requirements, iot_class
├── translations/
│   └── en.json            English strings for the UI setup wizard
│
├── const.py               All shared string keys and numeric defaults
│
├── __init__.py            Integration entry-point
│                          • Registers the YAML CONFIG_SCHEMA (rooms, heat_sources, …)
│                          • async_setup()       – stores YAML config in hass.data
│                          • async_setup_entry() – creates coordinator, forwards to platforms
│                          • async_unload_entry()– tears down on removal
│
├── config_flow.py         UI wizard (HeatingAssistantConfigFlow)
│                          • Step "user": latitude, longitude, outdoor sensor, update_interval, horizon
│                          • Options flow: outdoor sensor, update_interval, horizon (post-install edit)
│
├── coordinator.py         HeatingAssistantCoordinator (DataUpdateCoordinator)
│                          • Builds HouseModel and heat sources from config
│                          • _async_update_data() called every UPDATE_INTERVAL seconds
│                          • Reads sensor states → runs MPC → writes heater actions
│
├── thermal_model.py       Physics: lumped RC model
│                          • RoomConnection, Window, Room  (dataclasses)
│                          • HouseModel: step(), predict(), state-space matrices
│                          • compute_heat_flows(): per-room heat loss breakdown
│                          • time_constant(), steady_state_temperature(): setup helpers
│
├── solar_model.py         Physics: clear-sky solar irradiance pipeline
│                          • solar_angles(), clear_sky_dni(), clear_sky_dhi()
│                          • angle_of_incidence(), window_solar_gain(), room_solar_gains()
│
├── heat_sources.py        Physics: heater models
│                          • HeatSource (ABC), ElectricHeater, HeatPump
│                          • _cop_at_temp() helper (Carnot COP correction)
│
├── controller.py          MPC controller (application classes wrapping mbc framework)
│                          • HouseThermalSDE(ContinuousDiscreteModel): nonlinear CD-SDE
│                          •   f(x,u,d,p,t): drift with heat-pump COP nonlinearity
│                          •   Analytic Jacobians dfdx, dhmdx for EKF efficiency
│                          • HeatingMPCController: application facade (coordinator API)
│                          •   → uses ContinuousDiscreteEKF from mbc.estimation
│                          •   → uses CDTrackingOptimalControlProblem from mbc.control
│
├── parameter_estimator.py ML thermal parameter estimation
│                          • KalmanMLEstimator: maximum-likelihood thermal parameter fitting
│                          • Uses Nelder–Mead optimisation over Kalman PED log-likelihood
│                          • estimate(): returns {room: {thermal_mass, r_external}}
│
├── model_diagnostics.py   Analysis tools for tuning and validation
│                          • Prediction-error analysis (RMSE, MAE, R², bias)
│                          • Residual autocorrelation (Ljung–Box Q test)
│                          • Open-loop multi-step simulation
│                          • Controller performance report
│
├── diagnostics.py         HA diagnostics platform
│                          • async_get_config_entry_diagnostics(): full system state dump
│
├── services.yaml          Service definitions (simulate, estimate, analyze, validate, …)
│
├── climate.py             HA climate platform
│                          • RoomClimateEntity per room
│                          • Modes: heat_cool (heat-pump rooms) / heat / off
│                          • Actions: heating / cooling / idle
│                          • Setpoint range: 5 °C – 30 °C, step 0.5 °C
│
├── button.py              HA button platform
│                          • EstimateParametersButton: triggers ML parameter estimation
│                            with one press; applies results and posts a notification
│
└── sensor.py              HA sensor platform
                           • PredictedTemperatureSensor per room         [°C]
                           • HeatingPowerSensor per room                 [W]
                           • SolarGainSensor per room                    [W]
                           • TemperatureForecastSensor per room          [°C]
                           • HeatLossSensor per room                     [W]
                           • EnergyBalanceSensor per room                [W]
                           • HeatingPlanSensor per room                  [W]
                           • SolarForecastSensor per room                [W]
                           • PredictionErrorSensor per room              [°C]
                           • ModelFitQualitySensor per room              [–]
                           • ParameterConfidenceSensor per room          [–]
                           • OpenLoopRMSESensor per room                 [°C]
                           • KalmanInnovationSensor per room             [°C]
                           • ResidualACFSensor per room                  [–]
                           • ControlActionSensor per heat source         [%]
                           • HeatPumpCOPSensor per heat pump             [–]
                           • OutdoorTemperatureSensor system-wide        [°C]
                           • OutdoorForecastSensor system-wide           [°C]
                           • SystemEfficiencySensor system-wide          [W]
```

### 2.2 Data flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Home Assistant state machine                                            │
│                                                                          │
│   sensor.outdoor_temp ──┐                                                │
│   weather.forecast_*  ──┤  (optional: weather forecast)                   │
│   sensor.room_A_temp ───┤                                                │
│   sensor.room_B_temp ───┤                                                │
│   …                     │                                                │
│                          ▼                                               │
│        ┌─────────────────────────────┐                                   │
│        │  HeatingAssistantCoordinator │  (every 60 s)                    │
│        │                             │                                   │
│        │  1. Read sensor states      │                                   │
│        │  2. Update HouseModel temps │                                   │
│        │  3. Call HeatingMPCController │                                   │
│        │  4. Write heater actions    │──► switch.heater  (turn_on/off)   │
│        │  5. Notify platforms        │──► number.heater  (set_value)     │
│        └────────────┬────────────────┘──► climate.hp     (set_hvac_mode) │
│                     │                                                    │
│          ┌──────────┴──────────┐                                         │
│          ▼                     ▼                                         │
│  climate.heating_assistant_*  sensor.heating_assistant_*                 │
│  (setpoint, current temp,     (predicted temp, heating power             │
│   HVAC mode/action)            per room)                                 │
└─────────────────────────────────────────────────────────────────────────┘

Inside HeatingMPCController.compute():

  solar_model ──► solar_gain[room, k]  for k = 0…N-1
  weather/persistence ──► outdoor_temp[k]  for k = 0…N-1

  D = disturbance forecast matrix (N × nd)
  y = current room temperatures (measurement vector)

  ┌─ ContinuousDiscreteEKF.step(y, u, d, p, t)  →  x̂ (state estimate)
  │    predict: integrate dx/dt = f(x,u,d,p,t) and dP/dt = FP + PFᵀ + σσᵀ
  │             over [tₖ₋₁, tₖ] using Euler sub-steps
  │    update:  K = P⁻ Hᵀ (H P⁻ Hᵀ + Rₘ)⁻¹
  │             x̂ = x̂⁻ + K (y − hm(x̂⁻)),  P = (I − K H) P⁻
  │
  ├─ CDTrackingOptimalControlProblem.solve(x̂, D)
  │    NLP:  min  Σ ‖z[k] − z_ref‖²_Q + ‖u[k]‖²_R + ‖Δu[k]‖²_S
  │               + ρ_z (soft constraint violation penalty)
  │          s.t.  0 ≤ u ≤ 1  (hard input box)
  │    solve via L-BFGS-B (scipy.optimize)
  │
  └─ apply u*[0] to heat sources (receding horizon)
```

---

## 3. Physics and Mathematical Models

### 3.1 Lumped RC thermal model

Each room is treated as a single, well-mixed thermal node — the **lumped-parameter** (or RC) approximation.  This is standard practice for building energy simulation at the room level and is described in detail in ISO 13790 and ASHRAE 90.1.

The energy balance for room *i* is:

$$C_i \cdot \frac{dT_i}{dt} = Q_{\text{heater},i}(t) + \sum_{j \in \text{adj}(i)} \frac{T_j(t) - T_i(t)}{R_{ij}} + \frac{T_{\text{outdoor}}(t) - T_i(t)}{R_{i,\text{ext}}} + Q_{\text{solar},i}(t)$$

**Symbol table**

| Symbol | Unit | Meaning |
|--------|------|---------|
| $C_i$ | J/K | Effective thermal mass (heat capacity) of room *i*.  Includes air, furniture, walls. |
| $T_i$ | °C | Current (lumped) temperature of room *i*. |
| $T_{\text{outdoor}}$ | °C | Outdoor air temperature (read from a HA sensor). |
| $R_{ij}$ | K/W | Thermal resistance of the wall, floor, or ceiling between rooms *i* and *j*. |
| $R_{i,\text{ext}}$ | K/W | Total thermal resistance between room *i* and the outdoor environment (walls + roof + ground). |
| $Q_{\text{heater},i}$ | W | Sum of thermal power output from all heaters assigned to room *i*. |
| $Q_{\text{solar},i}$ | W | Solar heat gain through all windows of room *i*. |

### 3.2 State-space matrix form

For a house with *n* rooms, the set of coupled ODEs is assembled once at startup into a compact matrix form:

$$\mathbf{C} \cdot \frac{d\mathbf{T}}{dt} = \mathbf{A} \cdot \mathbf{T} + \mathbf{B}_{\text{ext}} \cdot T_{\text{outdoor}} + \mathbf{Q}(t)$$

where:
- $\mathbf{C}$ is an *n*-vector of thermal masses (diagonal of the capacitance matrix).
- $\mathbf{T}$ is the *n*-vector of room temperatures.
- $\mathbf{A}$ is an $n \times n$ conductance matrix:
  - off-diagonal element $A_{ij} = +1/R_{ij}$ (heat flowing in from room *j*)
  - diagonal element $A_{ii} = -1/R_{i,\text{ext}} - \sum_j 1/R_{ij}$ (total heat flowing out)
- $\mathbf{B}_{\text{ext}}$ is an *n*-vector of outdoor conductances ($1/R_{i,\text{ext}}$ for each room).
- $\mathbf{Q}(t)$ is the *n*-vector of heater power plus solar gains.

This representation makes each `step()` call a simple vector-matrix multiply — fast even for large houses.

### 3.3 Continuous-discrete integration

The MPC controller treats the thermal model as a **continuous-discrete stochastic differential equation (CD-SDE)** and integrates it using **explicit Euler with sub-steps** (the *n_int_steps* parameter, default 10 sub-steps per sampling interval).  Given the nonlinear continuous-time drift:

$$\dot{\mathbf{x}}(t) = \mathbf{f}(\mathbf{x}, \mathbf{u}, \mathbf{d}, t) = \mathbf{F}\,\mathbf{x} + \mathbf{G}_u(T_{\text{out}})\,\mathbf{u} + \mathbf{G}_d\,\mathbf{d}$$

with $\mathbf{F} = \mathbf{C}_{\text{cap}}^{-1}\,\mathbf{A}$, the state is propagated over each sub-step $h = dt / n_{\text{int\_steps}}$ as:

$$\mathbf{x}(t + h) \approx \mathbf{x}(t) + h\,\mathbf{f}(\mathbf{x}(t), \mathbf{u}, \mathbf{d}, t)$$

This handles the nonlinearity in $\mathbf{G}_u(T_{\text{out}})$ (heat-pump COP varying with outdoor temperature) correctly without linearising or discretising the model into a fixed matrix form.  The CD-EKF propagates both the mean state and the error covariance matrix using the same Euler sub-stepping approach.

The `HouseModel.step()` and `HouseModel.predict()` methods also use forward Euler and are used for the `simulate_thermal_response` service and diagnostics.

For typical residential buildings (large thermal masses, slow dynamics) a control time step `update_interval ≤ 900 s` (15 minutes) gives accurate results with the default 10 integration sub-steps.  The default `update_interval = 900 s` is a good balance between prediction accuracy and computational load.

### 3.4 Solar heat gain model

The solar disturbance pipeline converts geographic location + time + window geometry into watts of heat entering each room.

#### Step 1 — Solar position

The sun's position is expressed as **altitude** α (angle above the horizon, radians) and **azimuth** A (degrees clockwise from North).

1. **Day-of-year** *n* is extracted from the datetime.
2. **Equation of time** (Spencer 1971) corrects for the eccentricity and obliquity of Earth's orbit:

   $$B = \frac{360}{365} \cdot (n - 81) \quad [\text{degrees}]$$

   $$\text{EoT [min]} = 9.87 \sin(2B) - 7.53 \cos(B) - 1.5 \sin(B)$$

3. **Solar declination** (Cooper equation):

   $$\delta = 23.45° \cdot \sin\!\left(\frac{360}{365} \cdot (n - 81)\right) \quad [\text{converted to radians}]$$

4. **Apparent solar time** corrects UTC for longitude and EoT.
5. **Hour angle** $\omega = 15° \times (\text{solar time} - 12)$ [degrees → radians].
6. **Altitude** from the spherical-trigonometry formula:

   $$\sin \alpha = \sin \varphi \cdot \sin \delta + \cos \varphi \cdot \cos \delta \cdot \cos \omega$$

   where $\varphi$ is the geographic latitude.
7. **Azimuth** from the clockwise-from-South formula, then converted to clockwise-from-North.

#### Step 2 — Clear-sky Direct Normal Irradiance (DNI)

The extra-terrestrial irradiance is corrected for the Earth–Sun distance:

$$G_{on} = 1361 \cdot \left(1 + 0.033 \cdot \cos\!\left(\frac{360 \cdot n}{365}\right)\right) \quad [\text{W/m}^2]$$

The air-mass number uses the Kasten & Young (1989) formula which avoids a singularity at the horizon:

$$am = \frac{1}{\sin \alpha + 0.50572 \cdot (\alpha_{\text{deg}} + 0.07628)^{-1.6364}}$$

Atmospheric transmittance (Meinel & Meinel approximation):

$$\tau_b = 0.56 \cdot \left(e^{-0.65 \cdot am} + e^{-0.095 \cdot am}\right)$$

$$\text{DNI} = G_{on} \cdot \tau_b \quad [\text{W/m}^2]$$

#### Step 3 — Diffuse Horizontal Irradiance (DHI)

A simplified isotropic model:

$$\text{GHI} = \text{DNI} \cdot \sin \alpha$$

$$\text{DHI} = 0.1 \cdot \text{GHI} \quad [\text{W/m}^2]$$

#### Step 4 — Angle of incidence on the window

The angle $\theta$ between the direct beam and the surface normal is:

$$\cos \theta = \cos \alpha \cdot \cos \gamma \cdot \sin \beta + \sin \alpha \cdot \cos \beta$$

where $\beta$ is the surface tilt from horizontal (90° = vertical) and $\gamma$ is the relative azimuth (sun azimuth − surface azimuth).

#### Step 5 — Irradiance on the window

$$I_{\text{direct}} = \max(0,\; \text{DNI} \cdot \cos \theta)$$

$$I_{\text{diffuse}} = \text{DHI} \cdot \frac{1 + \cos \beta}{2} \quad \text{(Liu and Jordan isotropic sky model)}$$

$$I_{\text{window}} = I_{\text{direct}} + I_{\text{diffuse}} \quad [\text{W/m}^2]$$

#### Step 6 — Solar heat gain

$$Q_{\text{solar}} = \text{SHGC} \cdot \text{area} \cdot I_{\text{window}} \quad [\text{W}]$$

The **Solar Heat Gain Coefficient** SHGC = 0.6 is the default (typical clear double glazing).  This constant is defined in `solar_model.py` as `DEFAULT_SHGC` and can be changed at the module level if your windows have a different specification.

### 3.5 Heat source models

#### Electric heater

$$Q_{\text{thermal}} = P_{\max} \cdot u \cdot \eta$$

- $P_{\max}$ — rated maximum electrical (= thermal) power [W].
- $u$ — control signal in $[0, 1]$ (fractional output chosen by the MPC controller).
- $\eta$ — efficiency, default 1.0.  For a purely resistive heater $\eta = 1.0$ exactly (all electrical energy becomes heat).  Infrared heaters can be specified with $\eta < 1$ if a fraction is radiated outside the thermal envelope.

The outdoor temperature has no effect on an electric heater's output.

#### Air-source heat pump

The electrical input power is fixed at $P_{\text{elec}} = P_{\max} / \text{COP}_{\text{rated}}$.  The actual thermal output is:

$$\text{COP}(T_{\text{outdoor}}) = \max\!\left(1,\; \text{COP}_{\text{rated}} \cdot \frac{\text{COP}_{\text{Carnot}}(T_{\text{outdoor}})}{\text{COP}_{\text{Carnot}}(T_{\text{ref}})}\right)$$

$$Q_{\text{thermal}} = P_{\text{elec}} \cdot u \cdot \text{COP}(T_{\text{outdoor}}) = \frac{P_{\max}}{\text{COP}_{\text{rated}}} \cdot u \cdot \text{COP}(T_{\text{outdoor}})$$

The Carnot COP at temperature $T_{\text{outdoor}}$ is computed assuming a fixed **supply temperature of 35 °C** (typical for low-temperature underfloor or radiator systems):

$$T_{\text{supply}} = 35 + 273.15 \quad [\text{K}]$$

$$\text{COP}_{\text{Carnot}}(T) = \frac{T_{\text{supply}}}{\max(T_{\text{supply}} - T,\; 1)}$$

The `max(…, 1)` guard ensures the COP never falls below 1.0 (even in extreme cold, a heat pump is at least as efficient as direct electric heating).

**Minimum outdoor temperature:** if `T_outdoor < −20 °C` the heat pump shuts off completely (`COP = 0`) to represent the compressor lock-out that real units implement to avoid defrost damage.  This threshold is hardcoded and not currently configurable via YAML.

**Offset-based setpoint control:** when the heat pump is connected via a `climate.*` entity, Heating Assistant reads the heat pump's own internal temperature sensor (`current_temperature` attribute) and sets the heat pump's target temperature to:

$$T_{\text{target}} = T_{\text{hp,internal}} + \text{fraction} \times \text{max temp offset}$$

where `max_temp_offset` (default 5 °C) is the maximum temperature differential at full power.  This makes the heat pump modulate its own output based on the gap between the setpoint it receives and its own temperature reading.  If the heat pump's internal temperature is unavailable, the HA room temperature is used as a fallback.

**Hysteresis deadband:** the integration tracks a per-heat-pump cooling/heating state and uses separate thresholds for mode transitions, so a small temperature fluctuation near the setpoint cannot cause rapid toggling:

| Transition | Temperature condition | Result |
|:---|:---|:---|
| heating/idle → cooling | `room_temp > setpoint + turn_off_deadband` | Enter cooling mode |
| cooling → heating/idle | `room_temp < setpoint − turn_off_deadband` | Exit cooling mode |
| (no transition) | `setpoint − deadband ≤ room_temp ≤ setpoint + deadband` | **Hold** current mode |

Once in either mode, the unit stays there until the temperature crosses the opposite threshold.  Within the dead-band window (width = 2 × `turn_off_deadband`) the mode is locked.

**Dispatch inside each mode** (checked only after hysteresis resolves the mode):

| Mode | MPC fraction | Action |
|:---|:---:|:---|
| Cooling | any | Switch to `dry` (or `fan_only`); target = T_hp_internal − (1 °C + overshoot) |
| Heating/idle | `> 0` | Heat mode; target = T_hp_internal + fraction × max_temp_offset |
| Heating/idle | `= 0` | Idle heat mode; target = T_hp_internal (no offset — HP produces near-zero heat) |

The heat pump **never turns fully off** via HVAC mode in normal operation.  When cooling, the idle setpoint offset grows with overshoot: `target = T_hp_internal − (1.0 + overshoot)` where `overshoot = max(0, room_temp − setpoint)`.  This keeps the cooling effect proportional to how far above the setpoint the room is.

**Cooling mode (dry/dehumidify):** when the room temperature exceeds the setpoint, the heat pump automatically switches to a gentle cooling mode to help bring the temperature down. The integration prefers "dry" (dehumidify) mode if available, which provides passive cooling without running the compressor at full cooling capacity. If "dry" mode is not supported, it falls back to "fan_only" mode.

When in cooling mode, the heat pump actively removes heat from the room. The cooling capacity exposed to the thermal model and the advanced visualisation sensors is **derived from the cooling COP (EER), not from the heating thermal max**. Specifically:

```
electric_max          = max_power / cop_rated         [rated electrical input, W]
cooling_capacity_max  = electric_max × cooling_cop    [rated heat removal, W]
cooling_power         = − cooling_capacity_max × cooling_efficiency × power_scale
```

This corrects an earlier bug where a 6.6 kW *heating* heat pump was reported as having 6.6 kW of cooling capability — the cooling capacity is now the physically correct value (typically ≈ 70 % of the heating max for a heat pump where `cop_rated = 3.5` and `cooling_cop = 2.5`).

Two new heat-source configuration keys control the cooling cycle:

| Key | Default | Description |
|-----|---------|-------------|
| `cooling_cop` | `2.5` | Rated cooling coefficient of performance (EER) used when the heat pump is in dry / fan-only / cool mode. |
| `cooling_efficiency` | `1.0` | Fraction (0–1) of the rated cooling capacity actually delivered.  Lower the value (e.g. `0.4`) when relying on `dry` mode for gentle dehumidification. |

The signed cooling power is exposed as a negative value on the per-room **Heating Power**, **Heating Plan**, and **Energy Balance** sensors, and the per-room **Climate** entity reports `HVACAction.COOLING` (instead of `idle`) whenever a heat pump in the room is removing heat.  Heat-pump-equipped rooms also advertise `HVACMode.HEAT_COOL` so the HA frontend renders the cooling state with the correct icon.

**Example COP curve** (COP_rated = 3.5, T_ref = 7 °C):

| Outdoor temp | COP (approx.) |
|:---:|:---:|
| 15 °C | 4.1 |
| 7 °C | 3.5 (rated) |
| 0 °C | 3.0 |
| −7 °C | 2.6 |
| −15 °C | 2.1 |
| −20 °C | 0.0 (shut-off) |

---

## 4. Model Predictive Controller

### 4.1 Overview

The controller (`controller.py`) implements a **nonlinear model predictive control (NMPC)** architecture built on the `mbc` (model-based control) package:

| Component | Class (from `mbc`) | Role |
|-----------|-------|------|
| **System model** | `ContinuousDiscreteModel` (ABC) | Defines the continuous-discrete SDE: `dx = f(x,u,d,p,t)dt + σdw`, `ym = hm(x,...)`. |
| **State estimator** | `ContinuousDiscreteEKF` | CD-EKF: integrates the nonlinear drift and linearised Riccati ODE between measurement steps using Euler sub-stepping. |
| **Optimal control** | `CDTrackingOptimalControlProblem` | NLP formulation of the receding-horizon tracking problem.  Propagates the predicted trajectory via Euler integration and solves via L-BFGS-B (scipy). |
| **MPC policy** | `CDNMPCController` | Orchestrates estimate → optimise → apply at each step. |

The house-heating application provides two classes in `controller.py`:

| Class | Role |
|-------|------|
| `HouseThermalSDE` | Concrete `ContinuousDiscreteModel` wrapping `HouseModel` and `HeatSource` objects.  The nonlinearity arises from the heat-pump COP varying with outdoor temperature through `G_u(T_out)`. |
| `HeatingMPCController` | Application facade.  Builds `HouseThermalSDE`, `ContinuousDiscreteEKF`, and `CDTrackingOptimalControlProblem`; adds solar/outdoor forecasting; applies source set-points; exposes visualisation properties for the coordinator. |

At each control step the `HeatingMPCController`:

1. Reads room temperatures from HA sensors (measurement vector **y**).
2. Builds an *N*-step disturbance forecast **D** (outdoor temperature + solar gains).
3. Runs the CD-EKF to obtain the state estimate **x̂**.
4. Solves the NLP to find the optimal continuous input sequence **U***.
5. Applies only the **first step** u*[0] of the optimal sequence (receding horizon).

### 4.2 State estimation — Continuous-Discrete EKF

The state estimator is a **Continuous-Discrete Extended Kalman Filter (CD-EKF)** from the `mbc` package (`mbc.estimation.ContinuousDiscreteEKF`).  Between consecutive measurement times $t_{k-1}$ and $t_k$ the filter integrates the continuous-time mean and covariance using Euler sub-steps:

**Prediction (continuous-time integration over $[t_{k-1}, t_k]$):**

$$\frac{d\hat{\mathbf{x}}}{dt} = \mathbf{f}(\hat{\mathbf{x}}, \mathbf{u}, \mathbf{d}, \mathbf{p}, t)$$

$$\frac{d\mathbf{P}}{dt} = \mathbf{F}\,\mathbf{P} + \mathbf{P}\,\mathbf{F}^\top + \boldsymbol{\sigma}\,\boldsymbol{\sigma}^\top \qquad \text{(continuous Riccati ODE)}$$

where $\mathbf{F} = \partial\mathbf{f}/\partial\mathbf{x}$ is the analytic state Jacobian (constant for the linear drift, so this reduces to a Lyapunov ODE).

**Update (at measurement time $t_k$):**

$$\mathbf{S}[k] = \mathbf{H}\,\mathbf{P}^{-}[k]\,\mathbf{H}^\top + \mathbf{R}_m \qquad \text{(innovation covariance)}$$

$$\mathbf{K}[k] = \mathbf{P}^{-}[k]\,\mathbf{H}^\top\,\mathbf{S}[k]^{-1} \qquad \text{(Kalman gain)}$$

$$\hat{\mathbf{x}}[k] = \hat{\mathbf{x}}^{-}[k] + \mathbf{K}[k]\bigl(\mathbf{y}[k] - \mathbf{h}_m(\hat{\mathbf{x}}^{-}[k])\bigr) \qquad \text{(corrected estimate)}$$

$$\mathbf{P}[k] = \bigl(\mathbf{I} - \mathbf{K}[k]\,\mathbf{H}\bigr)\,\mathbf{P}^{-}[k] \qquad \text{(posterior covariance)}$$

| Symbol | Meaning |
|--------|---------|
| $\boldsymbol{\sigma}$ | Diffusion matrix — $\sigma_w \mathbf{I}$ for isotropic process noise (default $\sigma_w = 0.1$ K/√s). |
| $\mathbf{R}_m$ | Measurement noise covariance — $\sigma_v^2 \mathbf{I}$ (default $\sigma_v = 0.5$ K). |
| $\mathbf{P}$ | State error covariance — propagated at every step; determines the Kalman gain. |
| $\mathbf{H} = \partial\mathbf{h}_m/\partial\mathbf{x}$ | Observation Jacobian — identity matrix for full-state observation. |

For the house thermal system with full-state observation ($\mathbf{h}_m = \mathbf{I}$, one temperature sensor per room), the Kalman gain converges quickly to a value that weights measurements heavily relative to the model prediction.  The filter provides robustness against temporary sensor noise and gradual model drift.

### 4.3 Optimal control problem — nonlinear tracking NLP

The cost function over the prediction horizon *N* is:

$$J(\mathbf{U}) = \sum_{k=0}^{N-2} \left\lVert \mathbf{z}[k{+}1] - \mathbf{z}_{\text{ref}} \right\rVert_{\mathbf{Q}}^2 + \left\lVert \mathbf{z}[N] - \mathbf{z}_{\text{ref}} \right\rVert_{\mathbf{P}}^2 + \sum_{k=0}^{N-1} \left( \left\lVert \mathbf{u}[k] \right\rVert_{\mathbf{R}}^2 + \left\lVert \Delta\mathbf{u}[k] \right\rVert_{\mathbf{S}}^2 \right) + \rho_z \sum_{k=1}^{N} \left\lVert \max(0, \mathbf{z}[k] - \mathbf{z}_{\max}) \right\rVert^2 + \left\lVert \max(0, \mathbf{z}_{\min} - \mathbf{z}[k]) \right\rVert^2$$

where $\Delta\mathbf{u}[k] = \mathbf{u}[k] - \mathbf{u}[k{-}1]$ (with $\mathbf{u}[-1]$ equal to the previous step's applied input):

| Symbol | Value / meaning |
|--------|----------------|
| $\mathbf{z}[k] = \mathbf{g}(\mathbf{x}[k])$ | Controlled output — room temperatures — at step *k* |
| $\mathbf{z}_{\text{ref}}$ | Reference (room setpoints) |
| $\mathbf{Q}$ | Stage output tracking cost (default: $\mathbf{I}$) |
| $\mathbf{P}$ | Terminal output tracking cost (`terminal_weight` × $\mathbf{Q}$, default: $100 \cdot \mathbf{I}$).  A large value strongly encourages the predicted trajectory to reach the setpoint by the end of the horizon, significantly improving steady-state tracking. |
| $\mathbf{R}$ | Input cost (`energy_weight` × $\mathbf{I}$, default: $0.01 \cdot \mathbf{I}$) |
| $\mathbf{S}$ | Input rate-of-change cost (`smoothing_weight` × $\mathbf{I}$, default: $0.1 \cdot \mathbf{I}$).  Set `smoothing_weight` to `0.0` to disable. |
| $\mathbf{z}_{\min}, \mathbf{z}_{\max}$ | Soft output constraint bounds: $\mathbf{z}_{\text{ref}} \pm \delta$ where $\delta$ = `constraint_offset` (default 2.0 °C) |
| $\rho_z$ | Soft constraint penalty weight (default: $10^4$) |
| $\mathbf{u}[k]$ | Input vector (continuous fractions $\in [0, 1]$) |

The predicted state trajectory is propagated using Euler sub-stepping of the nonlinear drift $\mathbf{f}(\mathbf{x}, \mathbf{u}, \mathbf{d}, \mathbf{p}, t)$ over each sampling interval.  The NLP is solved via **L-BFGS-B** (from `scipy.optimize`) with box constraints $0 \le \mathbf{u}[k] \le 1$.

The **terminal cost** $\mathbf{P}$ is the key mechanism for achieving setpoint tracking.  Without a large terminal weight the optimizer has weak incentive to drive the state to the reference by the end of the horizon — it can minimise total cost by spreading the error across all stages without converging.  Setting $\mathbf{P} = \lambda \mathbf{Q}$ with $\lambda \gg 1$ (default $\lambda = 100$) is equivalent to approximating the infinite-horizon cost and forces the optimal trajectory to converge to the setpoint well within the horizon.

The input cost $\mathbf{R}$ softly discourages running heaters when the room is close to setpoint.  Increasing `energy_weight` makes the controller more energy-conservative at the expense of tighter temperature tracking.

The smoothing cost $\mathbf{S}$ penalises *changes* in the control input from one step to the next.  This prevents the controller from toggling heaters on and off aggressively, resulting in more stable actuator commands and less wear on compressor-based heat sources.  Increasing `smoothing_weight` makes the controller more reluctant to change its actions between time steps.

**On-off sources** (e.g. `switch.*` entities) are modelled with a duty-cycle relaxation: the NMPC optimises the continuous fraction $u \in [0, 1]$, interpreted as the proportion of the sampling interval `update_interval` for which the source is active.  The coordinator maps this fraction to on/off commands.

### 4.4 Disturbance forecasts

The controller builds a disturbance forecast matrix $\mathbf{D} \in \mathbb{R}^{N \times n_d}$ before solving the NLP:

| Disturbance | Forecast method |
|-------------|----------------|
| **Outdoor temperature** | If a `weather_entity` is configured (e.g. the Met.no integration), the controller uses the weather forecast temperatures interpolated to each horizon step.  Otherwise, it falls back to persistence: the current measured value is held constant for all horizon steps.  Configure `outdoor_temp_entity` for the current measurement and `weather_entity` for the forecast. |
| **Solar gains** | The solar position model is evaluated at times `now + (k+1)·update_interval` for each horizon step `k = 0, …, N−1`, matching the end of each prediction interval.  This uses the deterministic orbital equations and produces an accurate prediction of how solar irradiance through each window will evolve over the next `N·update_interval` seconds.  The same time convention is used for the outdoor temperature forecast so that both disturbance components are evaluated consistently. |

### 4.5 Control cycle

Each call to `HeatingMPCController.compute()` follows this sequence:

```
compute(outdoor_temp, solar_gains=None, now=None, outdoor_forecast=None)
│
├─ if solar_gains is None: compute from solar model
├─ if outdoor_forecast provided: use weather forecast
│  else: _forecast_outdoor(outdoor_temp)  → list of N floats (persistence)
├─ _forecast_solar(now)                → list of N {room: W} dicts
├─ Build D ∈ ℝ^(N × nd) from forecasts
│
├─ ContinuousDiscreteEKF.step(y, u, d, p, t)  → x̂  (state estimate)
│   ├─ predict: integrate dx/dt = f(x,u,d,p,t) and dP/dt = FP+PFᵀ+σσᵀ
│   └─ update:  K = P⁻Hᵀ(HP⁻Hᵀ+Rm)⁻¹,  x̂ = x̂⁻ + K(y − hm(x̂⁻))
│
├─ CDTrackingOptimalControlProblem.solve(x̂, D)
│   ├─ propagate: x[k+1] = x[k] + h·f(x[k],u[k],d[k],p,t)  (Euler sub-steps)
│   ├─ NLP:  min Σ ‖z[k]-z_ref‖²_Q + ‖u[k]‖²_R + ‖Δu[k]‖²_S + ρ_z·violation
│   └─ solve via L-BFGS-B (scipy.optimize)  s.t.  0 ≤ u ≤ 1
│
└─ Apply u*[0] to heat sources (receding horizon)
   Return {source_name: fraction}
```

---

## 5. Home Assistant Integration

### 5.1 Platforms and entities

Heating Assistant registers three HA platforms: **climate**, **sensor**, and **button**.

For each room declared in `configuration.yaml` the integration creates:

| Entity ID | Platform | State | Attributes |
|-----------|----------|-------|------------|
| `climate.heating_assistant_<room_name>` | climate | HVAC mode (`heat_cool` / `heat` / `off`) | current_temperature, target_temperature, hvac_action |
| `sensor.heating_assistant_<room_name>_predicted_temperature` | sensor | Temperature in °C | setpoint, thermal_mass, r_external |
| `sensor.heating_assistant_<room_name>_heating_power` | sensor | Total heating power in W | Per-source breakdown by source name |
| `sensor.heating_assistant_<room_name>_solar_gain` | sensor | Current solar heat gain in W | window_count, total_window_area |
| `sensor.heating_assistant_<room_name>_temperature_forecast` | sensor | End-of-horizon temperature in °C | trajectory, forecast (timestamped), setpoint, horizon_steps |
| `sensor.heating_assistant_<room_name>_temperature_prediction` | sensor | Same data as `_temperature_forecast`; stable availability for dashboards | trajectory, forecast (timestamped), setpoint, horizon_steps |
| `sensor.heating_assistant_<room_name>_heat_loss` | sensor | Total heat loss in W | external_loss, per-room flows, outdoor_temp |
| `sensor.heating_assistant_<room_name>_energy_balance` | sensor | Net energy flow in W | heating_power, solar_gain, losses breakdown |
| `sensor.heating_assistant_<room_name>_heating_plan` | sensor | Current planned heating power in W | forecast (timestamped), horizon_steps |
| `sensor.heating_assistant_<room_name>_heating_plan_prediction` | sensor | Same data as `_heating_plan`; stable availability for dashboards | forecast (timestamped), horizon_steps |
| `sensor.heating_assistant_<room_name>_solar_forecast` | sensor | Current predicted solar gain in W | forecast (timestamped), horizon_steps, window_count |
| `sensor.heating_assistant_<room_name>_solar_power_prediction` | sensor | Same data as `_solar_forecast`; stable availability for dashboards | forecast (timestamped), horizon_steps, window_count |
| `sensor.heating_assistant_outdoor_temperature_forecast` | sensor | Current outdoor temperature in °C | forecast (timestamped), horizon_steps |
| `sensor.heating_assistant_outdoor_temperature_prediction` | sensor | Same data as `_outdoor_temperature_forecast`; stable availability for dashboards | forecast (timestamped), horizon_steps |

> **Note – prediction vs. forecast/plan entities.**  The `_prediction`
> entities expose identical data to their `_forecast` / `_heating_plan`
> counterparts, but their availability is decoupled from the coordinator's
> last-update status.  Use the `_prediction` variants in dashboards so the
> trajectory keeps rendering even if a transient coordinator refresh
> failure briefly flips the original sensors to `unavailable`.

One system-wide button entity is also created:

| Entity ID | Platform | Action |
|-----------|----------|--------|
| `button.heating_assistant_estimate_parameters` | button | Triggers ML thermal parameter estimation; applies results and posts a persistent notification |

**Climate entity behaviour:**

- **`target_temperature`** — the room setpoint.  Updated via `async_set_temperature()`.  Adjustable range: 5 °C – 30 °C in 0.5 °C steps.
- **`current_temperature`** — the latest room temperature (measured via `temp_sensor` if configured, otherwise the model's internal state).
- **`hvac_modes`** — rooms served by a heat pump advertise `[heat_cool, heat, off]`; heat-only rooms advertise `[heat, off]`.
- **`hvac_action`** — `heating` when any heater in the room is producing heat; `cooling` when a heat pump is actively removing heat; `idle` otherwise.
- **Setting mode to `off`** immediately sets the room setpoint to 5 °C (frost protection).
- **Setting mode to `heat`** restores the default setpoint (21 °C) if the current setpoint is at the frost-protection floor.

### 5.2 Heater entity dispatch

When the coordinator applies actions it inspects the HA domain of each `heater_entity` and calls the appropriate service:

| HA domain | Service called | Payload |
|-----------|---------------|---------|
| `switch` | `switch.turn_on` / `switch.turn_off` | `entity_id` — turns on if fraction > 0.5 |
| `number` | `number.set_value` | `value = round(fraction × 100)` (0–100) |
| `climate` (non-heat-pump) | `climate.set_hvac_mode` + `climate.set_temperature` | Cooling-protected setpoint control (see below) |
| `climate` (heat pump) | `climate.set_hvac_mode` + `climate.set_temperature` | Three-state control with fan mode (see below) |

**Heat pump climate entity control**

Heat pumps connected via `climate.*` entities use an offset-based control strategy with fan mode for cooling:

| MPC fraction | Room temperature | HVAC mode | Temperature setpoint |
|:---:|:---|:---:|:---|
| `> 0` | — | `heat` | `T_hp_internal + fraction × max_temp_offset` |
| `= 0` | `≤ setpoint` | `heat` | `T_hp_internal − idle_offset` (idle — setpoint below internal temp to prevent heating) |
| `= 0` | `> setpoint` | `dry` (preferred) or `fan_only` (fallback) | — (gentle dehumidification / air recirculation without full compressor cooling) |

When the room temperature exceeds the setpoint and no heating is required, the heat pump is switched to a gentle cooling mode.  The integration prefers `dry` (dehumidify) mode, which provides passive cooling without running the compressor at full capacity.  If the heat pump entity does not support `dry`, it falls back to `fan_only`.  Either way the compressor does not engage at full cooling power, avoiding unnecessary energy use while promoting air movement.

The heat pump's own internal temperature (`current_temperature` attribute on the climate entity) is read each cycle.  If unavailable, the HA room temperature from the configured `temp_sensor` is used as a fallback.

**Non-heat-pump climate entity control (e.g. electric heaters with built-in thermostat)**

Electric heaters (or other non-heat-pump sources) connected via `climate.*` entities include cooling protection to prevent the heater from firing when the room is already above the setpoint:

| MPC fraction | Room temperature | HVAC mode | Temperature setpoint |
|:---:|:---|:---:|:---|
| `> 0` | `≤ setpoint` | `heat` | Room setpoint (normal heating) |
| `> 0` | `> setpoint` | `heat` | `T_entity_internal − idle_offset` (cooling protection override) |
| `= 0` | — | `heat` | `T_entity_internal − idle_offset` (idle — no heating) |
| — | — (room disabled) | `off` | — |

The key safety feature is the **cooling protection override**: if the HA room sensor indicates the room is warmer than the setpoint, the entity's internal setpoint is always placed below the entity's own internal temperature reading (`current_temperature` attribute).  This guarantees the heater's built-in thermostat will not fire, even when the entity's internal sensor disagrees with HeatingAssistant's room sensor.

If a `heater_entity` is not specified for a source, the controller still runs and stores the computed fraction but no HA service call is made (useful for simulation/testing).

### 5.3 Update cadence

The `HeatingAssistantCoordinator` inherits from `DataUpdateCoordinator` with:

```python
update_interval = timedelta(seconds=UPDATE_INTERVAL)   # UPDATE_INTERVAL = 60 s
```

Every 60 seconds:
1. All room temperature sensors are polled from the HA state machine.
2. The outdoor temperature sensor is polled.
3. The MPC controller runs (`HeatingMPCController.compute()`).
4. Heater entities are updated via HA services.
5. All subscribed climate and sensor entities are notified to refresh their state.

---

## 6. Requirements and Compatibility

| Requirement | Minimum version |
|-------------|----------------|
| Home Assistant | 2023.1 |
| Python | 3.10 |
| numpy | 1.21.0 |
| scipy | 1.9.0 |
| cvxopt | 1.3.0 |
| mbc | latest (from GitHub) |

All Python dependencies are listed in `manifest.json` under `requirements` and Home Assistant will install them automatically into the HA virtual environment on first load.  The `mbc` package is installed directly from GitHub (`git+https://github.com/marcuskrogh/mbc.git`) as it is not yet published on PyPI.

No cloud connectivity is required.  The integration is classified as `iot_class: local_push` — it pushes commands directly to local HA entities.

---

## 7. Installation

### 7.1 Manual installation

1. **Download** (or clone) this repository.

2. **Copy the integration** into your Home Assistant configuration directory:

   ```bash
   cp -r custom_components/heating_assistant \
         /path/to/homeassistant/config/custom_components/heating_assistant
   ```

   On a standard Home Assistant OS installation the config directory is `/config/`.  
   On a Home Assistant Container (Docker) installation it is whichever directory you mounted as `/config`.

3. **Verify the directory structure:**

   ```
   config/
   └── custom_components/
       └── heating_assistant/
           ├── __init__.py
           ├── manifest.json
           ├── const.py
           ├── config_flow.py
           ├── coordinator.py
           ├── thermal_model.py
           ├── solar_model.py
           ├── heat_sources.py
           ├── controller.py
           ├── optimal_control.py
           ├── state_estimator.py
           ├── parameter_estimator.py
           ├── model_diagnostics.py
           ├── climate.py
           ├── sensor.py
           ├── button.py
           ├── diagnostics.py
           ├── services.yaml
           └── translations/
               └── en.json
   ```

4. **Restart Home Assistant** (Settings → System → Restart, or `ha core restart` via CLI).

5. **Add the integration** through the UI:  
   Settings → Devices & Services → + Add Integration → search "Heating Assistant" → follow the wizard.

6. **Add the YAML configuration** to your `configuration.yaml` (see [Section 10](#10-configuration-reference) and [Section 11](#11-complete-configuration-examples)).

7. **Restart Home Assistant again** to load the room and heat-source topology from YAML.

### 7.2 HACS installation (future)

HACS support is planned.  Once added, the integration will appear in the HACS store under *Integrations* and can be installed with a single click.

---

## 8. Setting Up Your First Heating System

This section walks you through every step required to go from a freshly installed integration to a fully functioning, room-by-room heating system.  Work through the steps in order — each one builds on the previous.

### 8.1 Prerequisites

Before you begin, confirm the following are in place:

- **Integration installed** — the `custom_components/heating_assistant/` folder is in your HA config directory and HA has been restarted (see [Section 7](#7-installation)).
- **Outdoor temperature sensor** — a HA sensor entity that measures outdoor air temperature (e.g. from OpenWeatherMap, Météo-France, a Netatmo weather station, or any local sensor).  Note the entity ID (e.g. `sensor.openweathermap_temperature`).
- **Weather integration (optional)** — a HA weather entity providing temperature forecasts (e.g. `weather.forecast_home` from the Met.no integration).  This enables the controller to predict outdoor temperature changes instead of assuming the current value stays constant.
- **Room temperature sensor(s)** — at least one temperature sensor per room you want to control.  Note the entity ID for each (e.g. `sensor.living_room_temperature`).
- **Controllable heater entity/entities** — each heater must already be reachable in HA as a `switch.*`, `number.*`, or `climate.*` entity.  Note the entity ID for each (e.g. `switch.bedroom_heater`, `climate.mitsubishi_hp`).

---

### 8.2 Step 1 – Run the UI setup wizard

1. In Home Assistant, go to **Settings → Devices & Services**.
2. Click **+ Add Integration** (bottom-right).
3. Search for **Heating Assistant** and select it.
4. Fill in the form:

   | Field | What to enter |
   |-------|---------------|
   | **Latitude** | Your site latitude (pre-filled from HA settings — verify it is correct). |
   | **Longitude** | Your site longitude (pre-filled from HA settings — verify it is correct). |
   | **Outdoor temperature sensor entity ID** | The entity ID of your outdoor sensor, e.g. `sensor.openweathermap_temperature`.  Leave blank to use the 5 °C fallback (not recommended for real use). |
   | **Weather entity ID** | The entity ID of a HA weather entity, e.g. `weather.forecast_home`.  Leave blank to use the persistence forecast (current outdoor temperature assumed constant).  Recommended for improved prediction accuracy. |
   | **Control time step (update_interval)** | Leave at `900` (15 minutes) unless you have a specific reason to change it. This is both the OCP ZOH duration and the EKF/coordinator update period. |
   | **MPC prediction horizon** | Leave at `6` (90-minute lookahead at update_interval = 900 s).  Increase to `8`–`12` for buildings with high thermal mass. |

5. Click **Submit**.

The integration entry is now created, but no rooms or heaters are defined yet — that happens in the YAML steps below.

---

### 8.3 Step 2 – Plan your room topology

Draw a quick sketch of your home and answer these questions for each room you want to control:

- **What is the room name?**  Choose a short identifier made of letters, digits, and underscores only (e.g. `living_room`, `bedroom_1`).  This name will appear in entity IDs.
- **Which other rooms share a wall, floor, or ceiling with it?**  These become `connections` entries.
- **Does it have windows?**  If so, note the total glazed area (m²) and the compass direction each window faces.
- **What type of heater(s) does it have?**  Electric panel heater (→ `electric_heater`) or air-source heat pump (→ `heat_pump`)?
- **What is the heater's maximum power output?**  Check the device label or datasheet for the watt rating.

---

### 8.4 Step 3 – Identify your HA entities

For each room, open **Settings → Entities** in HA and confirm:

- The **room temperature sensor** entity ID (e.g. `sensor.living_room_temperature`).
- The **heater control** entity ID (e.g. `switch.living_room_heater`, `number.panel_heater_power`, or `climate.heat_pump`).

> **Tip:** Click the entity and look at the *Entity ID* field in the entity details dialog — use exactly that string in your YAML.

---

### 8.5 Step 4 – Estimate thermal parameters

For each room, estimate two key parameters.  If you are unsure, start with the defaults and refine later once the system is running (detailed guidance in [Section 13](#13-thermal-model-parameter-estimation-guide)):

| Parameter | Key | Rough starting point |
|-----------|-----|---------------------|
| Thermal mass | `thermal_mass` | `4 000 × floor_area_m²` [J/K] |
| External thermal resistance | `r_external` | `0.05` for a typical post-1980 house; `0.03` for modern; `0.10` for older poorly insulated building |

For inter-room connections, a good default `r_value` is:
- `0.1`–`0.2` for an open doorway or archway
- `0.2`–`0.5` for a closed interior door
- `0.3`–`0.6` for a solid brick or concrete wall

---

### 8.6 Step 5 – Write the YAML configuration

Open your `configuration.yaml` file (located in your HA config directory) and add a `heating_assistant:` block.  Below is a minimal template — expand it to match your home:

```yaml
heating_assistant:
  # Optional: override the outdoor sensor set in the UI wizard
  # outdoor_temp_entity: sensor.openweathermap_temperature
  # Optional: enable weather forecast for outdoor temperature predictions
  # weather_entity: weather.forecast_home

  rooms:
    - name: living_room                          # unique identifier
      thermal_mass: 8000000                      # J/K
      r_external: 0.04                           # K/W
      setpoint: 21.0                             # °C default target
      temp_sensor: sensor.living_room_temperature

    - name: bedroom
      thermal_mass: 4000000
      r_external: 0.05
      setpoint: 19.0
      temp_sensor: sensor.bedroom_temperature
      connections:
        - room: living_room   # shared wall with the living room
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

Key rules to remember:

- Every `name` under `rooms` and `heat_sources` must be unique.
- The `room` key of each heat source must exactly match a room `name`.
- Only use `heater_entity` domains `switch`, `number`, or `climate`.
- Use only letters, digits, and underscores in `name` values (no spaces).

See [Section 10](#10-configuration-reference) for the full field reference and [Section 11](#11-complete-configuration-examples) for more complete examples.

---

### 8.7 Step 6 – Restart Home Assistant

Save `configuration.yaml` and restart HA to load the room topology:

- **UI:** Settings → System → Restart → Restart Home Assistant.
- **CLI:** `ha core restart`

Wait for HA to finish restarting (typically 30–90 seconds depending on your hardware).

> **If HA fails to start**, open **Settings → System → Logs** and search for `heating_assistant`.  The most common cause is a YAML syntax error (wrong indentation, missing required key, or a `room` reference that does not match any room `name`).

---

### 8.8 Step 7 – Verify entities are created

Once HA has restarted:

1. Go to **Settings → Entities** and search for `heating_assistant`.
2. You should see multiple entity types for each room you defined:

   | Entity ID pattern | What it is |
   |-------------------|------------|
   | `climate.heating_assistant_<room_name>` | Thermostat — set your target temperature here |
   | `sensor.heating_assistant_<room_name>_predicted_temperature` | Model-predicted temperature [°C] |
   | `sensor.heating_assistant_<room_name>_heating_power` | Current total heating power [W] |
   | `sensor.heating_assistant_<room_name>_solar_gain` | Current solar heat gain [W] |
   | `sensor.heating_assistant_<room_name>_temperature_forecast` | MPC temperature trajectory [°C] |
   | `sensor.heating_assistant_<room_name>_temperature_prediction` | Same data as `_temperature_forecast`, recommended for dashboards [°C] |
   | `sensor.heating_assistant_<room_name>_heat_loss` | Heat loss breakdown [W] |
   | `sensor.heating_assistant_<room_name>_energy_balance` | Net energy flow [W] |
   | `sensor.heating_assistant_<room_name>_heating_plan` | Planned heating schedule [W] |
   | `sensor.heating_assistant_<room_name>_heating_plan_prediction` | Same data as `_heating_plan`, recommended for dashboards [W] |
   | `sensor.heating_assistant_<room_name>_solar_forecast` | Predicted solar gain schedule [W] |
   | `sensor.heating_assistant_<room_name>_solar_power_prediction` | Same data as `_solar_forecast`, recommended for dashboards [W] |
   | `sensor.heating_assistant_outdoor_temperature_forecast` | Outdoor temperature forecast over the MPC horizon [°C] |
   | `sensor.heating_assistant_outdoor_temperature_prediction` | Same data as `_outdoor_temperature_forecast`, recommended for dashboards [°C] |
   | `button.heating_assistant_estimate_parameters` | One-press ML parameter estimation |
   | `sensor.heating_assistant_mpc_performance` | MPC solver performance statistics (solve time, tracking error) |

3. If entities are **missing**, check the HA log for errors under the `heating_assistant` integration.  The most common cause is a room or heat source configuration error in `configuration.yaml`.

---

### 8.9 Step 8 – Set your temperature setpoints

The climate entities are now controllable from anywhere in HA:

- **Lovelace dashboard:** Add a *Thermostat* card and point it at `climate.heating_assistant_<room_name>`.  Use the dial to set the desired temperature.
- **Developer Tools → Services:** Call `climate.set_temperature` with `entity_id` and `temperature`.
- **Automations:** Use `climate.set_temperature` in automations to schedule temperature changes.

Setpoint range: 5 °C (frost protection) to 30 °C, adjustable in 0.5 °C steps.

> **Note:** The `setpoint` values in `configuration.yaml` are only the initial defaults loaded at startup.  After the first start, setpoints are persisted in HA's entity registry and changes made through the UI or automations take immediate effect without requiring a restart.

---

### 8.10 Step 9 – Confirm heater control is active

After the first full coordinator update cycle (up to 60 seconds after startup):

1. Check `sensor.heating_assistant_<room_name>_heating_power`.  If the room is below setpoint it should show a positive value (W).
2. Verify the linked heater entity has changed state — e.g. a `switch.*` heater should be `on` if the controller decided to heat.
3. If the room is already at or above setpoint, the controller may correctly output 0 W.  Temporarily raise the setpoint by a degree or two to test the response.

If heaters are not responding, check:
- The `heater_entity` value is the correct HA entity ID (not the friendly name).
- The entity domain is `switch`, `number`, or `climate`.
- The entity is not in an `unavailable` state.
- The HA log for any `heating_assistant` errors during the update cycle.

---

### 8.11 Step 10 – Monitor and tune

Over the first few days of operation, observe the system and refine your parameters:

| Observation | Likely cause | Action |
|-------------|-------------|--------|
| Room consistently undershoots setpoint | `r_external` too high (overestimates heat loss) **or** `thermal_mass` too low | Decrease `r_external` or increase `thermal_mass` |
| Room consistently overshoots setpoint | `r_external` too low **or** `thermal_mass` too high | Increase `r_external` or decrease `thermal_mass` |
| Predicted temperature diverges quickly from actual | Wrong `thermal_mass` or `r_external` | Compare steady-state heat loss empirically (see [Section 14.2](#142-external-thermal-resistance-r_external)) |
| Temperature oscillates (undershoot then overshoot) | Horizon too short or `smoothing_weight` too low | Increase `horizon` (e.g. from `6` to `8`) and/or increase `smoothing_weight` |
| Heater runs at full power then cuts out abruptly | `energy_weight` too low | Increase `energy_weight` (e.g. from `0.01` to `0.05`) |
| Solar gain is always zero | Wrong `latitude`/`longitude` or wrong window `orientation` | Verify coordinates; remember `orientation: 0` = North, `180` = South |

After any change to `configuration.yaml` (rooms, heat sources, or top-level keys), **restart HA** for the changes to take effect.

Refer to [Section 14](#14-thermal-model-parameter-estimation-guide) for detailed guidance on estimating thermal parameters, [Section 14.5](#145-mpc-regulator-tuning) for controller tuning guidance, and to [Section 16](#16-troubleshooting) for a full list of known issues and their solutions.

---

## 9. Setup Wizard

After installation, navigate to **Settings → Devices & Services → + Add Integration** and search for **Heating Assistant**.  A single-step form will appear:

| Field | Default | Description |
|-------|---------|-------------|
| **Latitude** | HA configured latitude | Site latitude in decimal degrees (positive = North). Used to compute solar position. |
| **Longitude** | HA configured longitude | Site longitude in decimal degrees (positive = East). Used to compute solar position. |
| **Outdoor temperature sensor entity ID** | *(empty)* | The entity ID of a HA temperature sensor that measures outdoor air temperature (e.g. `sensor.openweathermap_temperature`, `sensor.netatmo_outdoor_temperature`).  If left blank the controller uses a fallback of 5 °C — configure this for accurate operation. |
| **Weather entity ID** | *(empty)* | The entity ID of a HA weather entity (e.g. `weather.forecast_home` from the Met.no integration).  When configured, the controller uses the weather forecast to predict outdoor temperature changes over the MPC horizon instead of assuming the current temperature stays constant.  This significantly improves prediction accuracy during temperature transitions (e.g. overnight cooling, morning warm-up). |
| **Control time step (update_interval)** | 900 | Interval in seconds at which the MPC controller re-solves and applies actions; serves as the OCP ZOH duration, the EKF measurement step, and the coordinator update period.  Range: 60–3600.  Default 900 s = 15 minutes. |
| **MPC prediction horizon** | 6 | Number of update_interval steps to look ahead.  At update_interval=900 s, horizon=6 means 90 minutes of prediction. Range: 1–24. |

After saving, the integration entry is created.  The room topology and heat-source configuration still need to be added to `configuration.yaml`.

To **edit** the outdoor sensor, weather entity, update_interval, or horizon after installation:  
Settings → Devices & Services → Heating Assistant → Configure.

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
| `update_interval` | int | No | `900` | Control time step [s]: sets the OCP ZOH duration, the EKF measurement step, and how often the coordinator re-solves.  Range 60–3600. |
| `horizon` | int | No | `6` | MPC prediction horizon [steps].  Range 1–24. |
| `energy_weight` | float | No | `0.01` | Weight on the input cost ‖**u**‖² in the MPC objective.  Higher values make the controller more conservative about running heaters, reducing overshoot at the expense of slightly slower heating.  Typical range: `0.001`–`0.5`.  See [Section 14.5](#145-mpc-regulator-tuning). |
| `smoothing_weight` | float | No | `0.1` | Weight on the input rate-of-change cost ‖Δ**u**‖² in the MPC objective.  Higher values strongly penalise rapid changes in heater output between consecutive time steps, dampening oscillations and reducing actuator wear.  Set to `0.0` to disable.  Typical range: `0.0`–`2.0`.  See [Section 14.5](#145-mpc-regulator-tuning). |
| `constraint_offset` | float | No | `2.0` | Symmetric soft output constraint band [°C] around the setpoint: the controller keeps predicted room temperatures within `[setpoint − δ, setpoint + δ]`.  Violations are penalised but not forbidden.  Decrease for tighter tracking; increase if the QP solver reports infeasibility. |
| `terminal_weight` | float | No | `100.0` | Terminal cost multiplier λ: **P** = λ × **Q**.  A large value forces the predicted trajectory to converge to the setpoint by the end of the horizon, dramatically improving steady-state tracking.  Increase to 200–500 if the controller still crosses or misses the setpoint; decrease toward 10–20 if you prefer softer convergence with more energy-aware shaping over the horizon.  Must be ≥ 1. |
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
| `thermal_mass` | float | No | `5 000 000` | Effective heat capacity of the room [J/K].  Includes air mass, furniture, interior walls, and a fraction of the exterior walls.  See [Section 14.1](#141-thermal-mass-thermal_mass) for guidance. |
| `r_external` | float | No | `0.05` | Thermal resistance from the room to the outdoor environment [K/W].  Represents the sum of all paths to the outside: exterior walls, roof, ground, and infiltration.  See [Section 14.2](#142-external-thermal-resistance-r_external) for guidance. |
| `setpoint` | float | No | `21.0` | Initial desired temperature [°C].  Can be overridden at runtime by the `climate.*` entity. |
| `temp_sensor` | string | No | — | Entity ID of a single HA sensor that measures the actual room temperature.  If provided, this value is used to correct the model state at each update cycle.  Without a sensor, the model runs in open-loop (simulation-only) mode.  Cannot be combined with `temp_sensors`. |
| `temp_sensors` | list of strings | No | — | List of HA sensor entity IDs for the room.  The coordinator reads all of them at each update cycle and uses their **arithmetic mean** as the measured room temperature.  Useful when the room is large or has significant temperature gradients.  Cannot be combined with `temp_sensor`. |
| `connections` | list | No | `[]` | List of thermal connections to adjacent rooms. |
| `windows` | list | No | `[]` | List of window definitions for solar gain calculation. |
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
| `r_value` | float | **Yes** | — | Thermal resistance between the two rooms [K/W].  See [Section 14.3](#143-inter-room-thermal-resistance-r_value) for guidance. |

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

---

## 12. Entity Reference

### 12.1 Climate entities

**Entity ID format:** `climate.heating_assistant_<room_name>`

| Attribute | Value | Notes |
|-----------|-------|-------|
| `state` (`hvac_mode`) | `heat_cool`, `heat`, or `off` | `heat_cool` is advertised for rooms with a heat pump; `heat` for heat-only rooms; `off` for frost-protection mode |
| `current_temperature` | float [°C] | Latest room temperature from sensor or model |
| `temperature` | float [°C] | Current setpoint (read by Lovelace thermostat cards) |
| `hvac_action` | `heating`, `cooling`, or `idle` | `heating` when any source in the room is producing heat; `cooling` when a heat pump is actively removing heat; `idle` otherwise |
| `min_temp` | 5.0 | Frost-protection floor |
| `max_temp` | 30.0 | Maximum allowed setpoint |
| `target_temp_step` | 0.5 | Resolution for the thermostat dial |

**Service calls:**

```yaml
# Set a new target temperature
service: climate.set_temperature
target:
  entity_id: climate.heating_assistant_living_room
data:
  temperature: 22.0

# Switch a room to frost-protection mode
service: climate.set_hvac_mode
target:
  entity_id: climate.heating_assistant_bedroom_1
data:
  hvac_mode: "off"
```

### 12.2 Sensor entities – predicted temperature

**Entity ID format:** `sensor.heating_assistant_<room_name>_predicted_temperature`

| Property | Value |
|----------|-------|
| Device class | `temperature` |
| State class | `measurement` |
| Unit | °C |
| Value | Model temperature rounded to 2 decimal places |

**State attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `setpoint` | float | Current room setpoint [°C] |
| `thermal_mass` | float | Configured thermal mass [J/K] |
| `r_external` | float | Configured external thermal resistance [K/W] |

### 12.3 Sensor entities – heating power

**Entity ID format:** `sensor.heating_assistant_<room_name>_heating_power`

| Property | Value |
|----------|-------|
| Device class | `power` |
| State class | `measurement` |
| Unit | W |
| Value | Sum of `current_power` across all sources in the room, rounded to 1 decimal |

**State attributes:**  one attribute per heat source in the room, keyed by source `name`, giving that source's individual `current_power` [W].

### 12.4 Sensor entities – solar gain

**Entity ID format:** `sensor.heating_assistant_<room_name>_solar_gain`

| Property | Value |
|----------|-------|
| Device class | `power` |
| State class | `measurement` |
| Unit | W |
| Icon | `mdi:white-balance-sunny` |
| Value | Current solar heat gain through room windows, rounded to 1 decimal |

**State attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `window_count` | int | Number of windows configured for this room |
| `total_window_area` | float | Total glazed area [m²] |

### 12.5 Sensor entities – temperature forecast

**Entity ID format:** `sensor.heating_assistant_<room_name>_temperature_forecast`

> A second entity, `sensor.heating_assistant_<room_name>_temperature_prediction`, exposes the *same* trajectory data and is the entity used by the advanced visualisation dashboards in §14.  It overrides `available` to remain populated even when a transient coordinator update raises `UpdateFailed`, so dashboards never lose the predicted trace.

| Property | Value |
|----------|-------|
| Device class | `temperature` |
| State class | `measurement` |
| Unit | °C |
| Icon | `mdi:chart-line` |
| Value | Predicted temperature at the end of the MPC horizon |

**State attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `trajectory` | list[float] | Predicted temperatures for each horizon step [°C] |
| `forecast` | list[dict] | Timestamped forecast entries.  Each dict contains `time` (ISO-8601 string), `temperature` (°C), `heating_power` (W), `solar_gain` (W), and `outdoor_temp` (°C).  Suitable for `apexcharts-card` and similar community dashboard cards. |
| `setpoint` | float | Current room setpoint [°C] |
| `constraint_offset` | float | Symmetric offset δ around the setpoint for soft output constraints [°C].  The MPC keeps the predicted temperature within `[setpoint − δ, setpoint + δ]`.  Use this attribute to draw constraint bands on dashboard charts. |
| `current_temperature` | float | Current room temperature [°C] |
| `horizon_steps` | int | Number of prediction steps |
| `step_seconds` | float | Time step duration [s] |
| `horizon_minutes` | float | Total prediction horizon [min] |

### 12.6 Sensor entities – heat loss

**Entity ID format:** `sensor.heating_assistant_<room_name>_heat_loss`

| Property | Value |
|----------|-------|
| Device class | `power` |
| State class | `measurement` |
| Unit | W |
| Icon | `mdi:thermometer-minus` |
| Value | Total instantaneous heat loss [W] (positive = losing heat) |

**State attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `external_loss` | float | Heat flow to outdoors [W] |
| `<room_name>` | float | Heat flow to/from each connected room [W] (positive = losing heat to that room) |
| `total_loss` | float | Sum of all loss components [W] |
| `outdoor_temp` | float | Current outdoor temperature [°C] |
| `room_temp` | float | Current room temperature [°C] |

### 12.7 Sensor entities – energy balance

**Entity ID format:** `sensor.heating_assistant_<room_name>_energy_balance`

| Property | Value |
|----------|-------|
| Device class | `power` |
| State class | `measurement` |
| Unit | W |
| Icon | `mdi:scale-balance` |
| Value | Net energy flow [W] (positive = room gaining energy, negative = losing) |

**State attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `heating_power` | float | Total active heating power [W] |
| `solar_gain` | float | Solar heat gain [W] |
| `external_heat_loss` | float | Heat loss to outdoors [W] |
| `inter_room_heat_exchange` | float | Net heat exchange with connected rooms [W] |
| `total_heat_loss` | float | Total heat loss [W] |
| `net_energy_flow` | float | Net energy flow = heating + solar − loss [W] |
| `room_temperature` | float | Current room temperature [°C] |
| `setpoint` | float | Current room setpoint [°C] |

### 12.8 Sensor entities – control action

**Entity ID format:** `sensor.heating_assistant_<source_name>_control_action`

| Property | Value |
|----------|-------|
| State class | `measurement` |
| Unit | % |
| Icon | `mdi:tune-vertical` |
| Value | MPC control action [0–100 %] |

**State attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `room` | str | Room this source heats |
| `max_power` | float | Maximum thermal output [W] |
| `current_power` | float | Current thermal output [W] |

### 12.9 Sensor entities – heat pump COP

**Entity ID format:** `sensor.heating_assistant_<source_name>_cop`

| Property | Value |
|----------|-------|
| State class | `measurement` |
| Icon | `mdi:heat-pump-outline` |
| Value | Current COP at the current outdoor temperature |

**State attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `cop_rated` | float | Rated COP at reference temperature |
| `cop_temp_ref` | float | Reference outdoor temperature [°C] |
| `min_power` | float | Minimum thermal output before shutdown [W] |
| `max_temp_offset` | float | Maximum temperature offset at full power [°C] |
| `turn_off_deadband` | float | Hysteresis dead-band half-width [°C] (enter cooling above setpoint + deadband, exit below setpoint − deadband) |
| `outdoor_temp` | float | Current outdoor temperature [°C] |

### 12.10 Sensor entities – outdoor temperature

**Entity ID format:** `sensor.heating_assistant_outdoor_temperature`

| Property | Value |
|----------|-------|
| Device class | `temperature` |
| State class | `measurement` |
| Unit | °C |
| Value | Outdoor temperature as read by the integration |

### 12.11 Sensor entities – system summary

**Entity ID format:** `sensor.heating_assistant_system_summary`

| Property | Value |
|----------|-------|
| Device class | `power` |
| State class | `measurement` |
| Unit | W |
| Icon | `mdi:home-thermometer` |
| Value | Total heating power across all sources [W] |

**State attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `total_heating_power` | float | Total thermal output from all sources [W] |
| `total_solar_gain` | float | Total solar gain across all rooms [W] |
| `total_heat_loss` | float | Total heat loss across all rooms [W] |
| `net_energy_flow` | float | System-wide net energy flow [W] |
| `effective_system_cop` | float | Effective COP (thermal output ÷ electrical input) |
| `electrical_input_estimate` | float | Estimated total electrical input [W] |
| `active_sources` | int | Number of currently active heat sources |
| `total_sources` | int | Total number of configured heat sources |
| `room_heating_power` | dict | Per-room heating power breakdown |
| `outdoor_temperature` | float | Current outdoor temperature [°C] |

### 12.12 Sensor entities – heating plan

**Entity ID format:** `sensor.heating_assistant_<room_name>_heating_plan`

> A second entity, `sensor.heating_assistant_<room_name>_heating_plan_prediction`, exposes the *same* schedule data and is the entity used by the advanced visualisation dashboards in §14.  It remains available across coordinator update failures.

| Property | Value |
|----------|-------|
| Device class | `power` |
| State class | `measurement` |
| Unit | W |
| Icon | `mdi:radiator` |
| Value | Planned heating power for the first (current) MPC horizon step [W] |

**State attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `forecast` | list[dict] | Timestamped heating schedule.  Each dict contains `time` (ISO-8601 string) and `heating_power` (W).  Suitable for `apexcharts-card` to display the controller's planned heat output over the horizon. |
| `horizon_steps` | int | Number of schedule steps |
| `step_seconds` | float | Time step duration [s] |

### 12.13 Sensor entities – solar forecast

**Entity ID format:** `sensor.heating_assistant_<room_name>_solar_forecast`

> A second entity, `sensor.heating_assistant_<room_name>_solar_power_prediction`, exposes the *same* solar forecast data and is the entity used by the advanced visualisation dashboards in §14.  It remains available across coordinator update failures.

| Property | Value |
|----------|-------|
| Device class | `power` |
| State class | `measurement` |
| Unit | W |
| Icon | `mdi:weather-sunny-alert` |
| Value | Predicted solar gain for the first (current) MPC horizon step [W] |

**State attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `forecast` | list[dict] | Timestamped solar gain forecast.  Each dict contains `time` (ISO-8601 string) and `solar_gain` (W).  The solar position model evaluates each horizon step's time so this gives an accurate view of expected solar irradiance entering the room. |
| `horizon_steps` | int | Number of forecast steps |
| `step_seconds` | float | Time step duration [s] |
| `window_count` | int | Number of windows configured for this room |
| `total_window_area` | float | Total glazed area [m²] |

### 12.14 Sensor entities – outdoor temperature forecast

**Entity ID format:** `sensor.heating_assistant_outdoor_temperature_forecast`

> A second entity, `sensor.heating_assistant_outdoor_temperature_prediction`, exposes the *same* outdoor temperature forecast data and is the entity used by the advanced visualisation dashboards in §14.  It remains available across coordinator update failures.

| Property | Value |
|----------|-------|
| Device class | `temperature` |
| State class | `measurement` |
| Unit | °C |
| Icon | `mdi:thermometer-lines` |
| Value | Current outdoor temperature [°C] |

**State attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `forecast` | list[dict] | Timestamped outdoor temperature forecast.  Each dict contains `time` (ISO-8601 string) and `outdoor_temp` (°C).  The first entry is at "now" with the current measured outdoor temperature; subsequent entries use the MPC outdoor forecast (from the configured weather entity, or persistence if none is configured). |
| `horizon_steps` | int | Number of forecast steps |
| `step_seconds` | float | Time step duration [s] |
| `horizon_minutes` | float | Total prediction horizon [min] |

---

## 13. Advanced Visualisation and Setup Tools

This section describes the advanced visualisation sensors and setup assistance services that help you understand, monitor, and tune your heating system.

### 13.1 Visualisation sensors overview

In addition to the basic sensors (predicted temperature, heating power, solar gain), the integration creates a family of advanced sensors that provide deep insight into system operation:

| Sensor | Per-room | Purpose |
|--------|:--------:|---------|
| **Temperature Forecast** | ✓ | MPC-predicted temperature trajectory over the prediction horizon, plus a timestamped `forecast` attribute for charting |
| **Heat Loss** | ✓ | Instantaneous heat-loss breakdown (external + inter-room components) |
| **Energy Balance** | ✓ | Net energy flow: heating + solar − losses (signed: positive = warming, negative = cooling) |
| **Heating Plan** | ✓ | Planned signed power schedule over the full MPC horizon (positive = heating, negative = cooling), as a timestamped `forecast` attribute |
| **Solar Forecast** | ✓ | Predicted solar heat gain over the full MPC horizon, as a timestamped `forecast` attribute |
| **Outdoor Temperature Forecast** | ✗ (1 total) | Outdoor temperature forecast over the full MPC horizon — uses weather entity when configured, falls back to persistence otherwise |
| **System Summary** | ✗ (1 total) | Aggregate system metrics: total power, COP, active sources |
| **Prediction Error** | ✓ | One-step Kalman residual (signed °C) with rolling RMSE / MAE / bias attributes |
| **Model Fit Quality** | ✓ | R² of the one-step prediction; full residual statistics in attributes |
| **Parameter Confidence** | ✓ | 0–100 score covering thermal-mass / R-external / time-constant validity |
| **Open-Loop RMSE** | ✓ | Multi-step free-run prediction RMSE — the genuine model-quality metric |
| **Kalman Innovation** | ✓ | Innovation series with consistency flag for filter tuning |
| **Residual ACF** | ✓ | Lag-0…20 autocorrelation of residuals + 95 % confidence band + Ljung-Box Q |

All sensors update every coordinator cycle (default 60 seconds) and expose detailed breakdowns as state attributes that can be plotted in Lovelace dashboards.

The diagnostic sensors (Prediction Error, Model Fit Quality, Parameter Confidence, Open-Loop RMSE, Kalman Innovation, Residual ACF) are documented in detail — including ready-to-paste ApexCharts cards — in [`MODEL_FIT_GUIDE.md`](MODEL_FIT_GUIDE.md).  All forecast and diagnostic attributes emit ISO-8601 timestamp strings; use `new Date(e.time).getTime()` (not `e.time * 1000`) in your `data_generator` expressions.

### 13.2 Temperature forecast trajectory

The **Temperature Forecast** sensor shows what the MPC controller *predicts* will happen to the room temperature over the prediction horizon (e.g. the next 90 minutes at default settings).

- **State:** predicted temperature at the end of the horizon [°C]
- **`trajectory` attribute:** list of predicted temperatures at each time step, enabling a multi-point chart
- **`forecast` attribute:** timestamped list of dicts — each entry contains `time` (ISO-8601 UTC), `temperature` (°C), `heating_power` (W), `solar_gain` (W), `outdoor_temp` (°C), and `setpoint` (°C).  The first entry is timestamped at "now" and contains the current measured values, so the forecast trace connects seamlessly to the HA recorder history with no gap.  The `setpoint` field is included in every entry (both the "now" entry and future steps), allowing dashboard cards to plot the setpoint reference line across the full time range.

This is useful for:
- Verifying that the model's predictions are reasonable
- Understanding whether the controller expects a room to warm up, cool down, or remain stable
- Identifying rooms where the model is inaccurate (compare trajectory vs. actual measured temperature over time)

### 13.3 Heat loss analysis

The **Heat Loss** sensor quantifies *where* each room is losing (or gaining) heat at any given moment.

- **State:** total heat loss [W] (positive = room is losing heat)
- **Attributes:** breakdown by component — `external_loss` (to outdoors), plus one entry per connected room

This is useful for:
- Identifying the biggest sources of heat loss (poor insulation vs. open doorways)
- Understanding why a room is slow to heat up
- Comparing rooms to see which has the most aggressive heat loss

### 13.4 Energy balance

The **Energy Balance** sensor computes the net energy flow for each room: **heating power + solar gain − total heat loss**.

- **State:** net energy flow [W] (positive = room is warming, negative = cooling)
- **Attributes:** detailed breakdown of all energy terms

This is the key sensor for understanding *why* a room's temperature is changing. A positive net balance means the room is warming; negative means it is cooling even with heaters running.

### 13.5 System efficiency summary

The **System Summary** sensor provides aggregate metrics for the entire heating installation.

- **State:** total heating power across all sources [W]
- **Key attributes:**
  - `effective_system_cop` — overall thermal output divided by estimated electrical input (accounts for heat pump COP)
  - `net_energy_flow` — system-wide heating + solar − losses [W]
  - `room_heating_power` — per-room heating power breakdown

### 13.6 Heating plan forecast

The **Heating Plan** sensor shows the controller's *intended* schedule for each room over the full MPC horizon.

- **State:** planned signed power for the current step [W]
- **`forecast` attribute:** timestamped list of dicts — each entry contains `time` (ISO-8601 UTC) and `heating_power` (W).  Positive = heating, negative = cooling (heat removal when a heat pump is in dry / fan-only / cool mode).  The first entry is at "now" with the current actual signed power, providing a seamless connection to the HA recorder history.

This is useful for:
- Seeing in advance whether the controller intends to pre-heat a room before the setpoint is needed
- Comparing the planned heating schedule against actual solar gain to understand how the controller balances the two
- Verifying that the `energy_weight` is not making the controller too reluctant to heat
- Verifying that the cooling capacity reported in the forecast is consistent with `cooling_cop × (max_power / cop_rated)` rather than the heating thermal max (the previous-version bug was that cooling traces went all the way down to `−max_power`)

### 13.7 Solar gain forecast

The **Solar Forecast** sensor shows the deterministic solar heat-gain prediction for each room over the full MPC horizon.

- **State:** predicted solar gain for the current step [W]
- **`forecast` attribute:** timestamped list of dicts — each entry contains `time` (ISO-8601 UTC) and `solar_gain` (W).  The first entry is at "now" with the current actual solar gain, providing a seamless connection to the HA recorder history.

Because the solar position model is fully deterministic, this forecast is exact (assuming clear skies) and reflects the sun's trajectory over the coming horizon period.  This is useful for:
- Confirming that the solar model is producing sensible predictions for your location and window orientations
- Understanding why the controller is choosing to heat less in rooms with south-facing windows
- Identifying the peak solar gain time of day for each room

### 13.8 Outdoor temperature forecast

The **Outdoor Temperature Forecast** sensor exposes the outdoor temperature prediction the MPC controller uses when planning ahead.  It is a system-wide (not per-room) sensor with entity ID `sensor.heating_assistant_outdoor_temperature_forecast`.

- **State:** current outdoor temperature [°C] (same source as `sensor.heating_assistant_outdoor_temperature`)
- **`forecast` attribute:** timestamped list of dicts — each entry contains `time` (ISO-8601 UTC) and `outdoor_temp` (°C).  The first entry is at "now" with the current measured outdoor temperature; subsequent entries cover each MPC horizon step.

**How the forecast is populated:**

When a `weather_entity` is configured (e.g. `weather.forecast_home` from Met.no), the coordinator retrieves the hourly forecast using the `weather.get_forecasts` service introduced in HA 2023.9.  For older HA versions, it falls back to reading the deprecated `forecast` state attribute.  In both cases the raw hourly forecast entries are linearly interpolated to the MPC time grid so there is a value for every horizon step.

When no weather entity is configured, a persistence forecast is used: the current outdoor temperature is repeated for every step.  Configure a weather entity (see [Section 8.2](#82-step-1--run-the-ui-setup-wizard)) for improved prediction accuracy.

This sensor is useful for:
- Verifying that the weather forecast is being picked up correctly — the `outdoor_temp` values in the `forecast` attribute should vary over time when a weather entity is configured, not be constant
- Understanding why the controller is pre-heating (or not) in anticipation of a cold front
- Showing the outdoor forecast alongside the room temperature forecast and solar gain on the disturbance card (§ 13.18.5)

### 13.9 Diagnostics panel

The integration includes a full **HA diagnostics platform**.  Access it via:

> **Settings → Devices & Services → Heating Assistant → ⋮ (three dots) → Download diagnostics**

The diagnostics dump includes:

- **Room configuration:** thermal mass, R-values, time constants, connections, windows
- **Heat source details:** type, power, COP, current state
- **Heat flow breakdown:** per-room heat loss/gain components
- **Prediction trajectory:** MPC-predicted temperatures for each future step
- **Solar gains:** current solar heat gain per room
- **Steady-state analysis:** predicted steady-state temperatures at −10 °C, 0 °C, and 5 °C outdoor temperature using maximum heating power
- **Controller parameters:** horizon, update_interval, latitude, longitude

This is invaluable for troubleshooting or sharing your system configuration with others.

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

The estimator uses a **continuous-discrete Extended Kalman Filter (CD-EKF)** prediction-error decomposition (PED) to evaluate the Gaussian log-likelihood of each candidate parameter set.  The CD-EKF handles the nonlinear heat-pump COP dynamics directly in continuous time, integrating the state and covariance between discrete measurements using forward Euler with sub-stepping.  A multi-start Nelder–Mead optimizer searches the parameter space, with automatic **identifiability gating** to exclude parameters that the data cannot constrain (e.g., heater scales when heating fraction is constant, inter-room resistances when adjacent rooms track each other closely).

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
| `segment_length` | int | `30` | Number of history steps per segment (each step ≈ 60 s).  Default 30 = 30-minute free-run window. |

**Rule of thumb:** open-loop RMSE < 0.2 °C over 30 steps is excellent; > 0.5 °C suggests the model should be re-estimated.

### 13.17 Lovelace dashboard – board and card reference

This section provides a complete set of Lovelace card configurations for building an MPC-style monitoring dashboard.  The cards follow the standard model predictive control visualisation layout used in industry and academia:

1. **Predicted output** – temperature trajectory with setpoint reference and soft constraint band
2. **Control input** – planned heating power over the prediction horizon (step function)
3. **Disturbances** – outdoor temperature and solar gain forecasts

Each chart displays **historical recorder data** to the left of the "Now" line and **MPC predictions** to the right.  The forecast data includes a data point at the current time ("now") with the current measured values, ensuring that the predicted traces connect seamlessly to the recorder history with no gap.  The history window is twice the prediction horizon (default 6 h history + 3 h forecast = 9 h total) so you can visually assess how well the model tracks reality before examining the upcoming plan.

Together, these three panels give a complete picture of what the controller sees, what it plans to do, and why.

#### 13.17.1 Prerequisites

All forecast charts below use [apexcharts-card](https://github.com/RomRider/apexcharts-card), a popular HACS community card.  Install it via **HACS → Frontend → Search "apexcharts-card" → Install** and refresh your browser before using the examples.

#### 13.17.2 Dashboard structure – board with room subboards

Create a top-level **Heating Assistant** dashboard with a navigation view for the system overview and one subview for each room.  This mirrors the MPC structure: the overview shows system-wide metrics while each room subview shows the full MPC triplet (output, input, disturbances).

**Step 1 – Create the dashboard:**

> **Settings → Dashboards → Add Dashboard**
> - Title: *Heating Assistant*
> - Icon: `mdi:home-thermometer`

**Step 2 – Add the system overview view** (default view):

Add the system overview card (§ 13.18.7) and one compact status card per room.

**Step 3 – Add a subview for each room:**

> In the dashboard editor, click **+ Add View** for each room:
> - View type: *Panel* (single column, full width) or *Sections* for multi-column
> - Title: Room name (e.g. *Living Room*)
> - Icon: `mdi:sofa` / `mdi:bed` / etc.
> - Toggle **Subview** on – this makes the view accessible via navigation cards on the overview

In each room subview, add the three MPC cards below (§ 13.18.3 – § 13.18.5) arranged vertically so the time axes align, plus the room performance card (§ 13.18.6).

**Step 4 – Add navigation cards** to the overview view so you can click through to each room subview.

#### 13.17.3 MPC predicted temperature card

This is the primary MPC output visualisation.  It shows:
- **History** (left of Now): the measured room temperature from the HA recorder (solid line)
- **Prediction** (right of Now): the MPC-predicted temperature trajectory (solid line)
- The current setpoint as a step reference line
- The soft constraint band `[setpoint − δ, setpoint + δ]` as a shaded region

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Living Room – Predicted Temperature
  show_states: true
graph_span: 9h
span:
  start: minute
  offset: '-6h'
now:
  show: true
  label: Now
  color: '#424242'
yaxis:
  - id: temp
    apex_config:
      title:
        text: Temperature (°C)
      tickAmount: 5
series:
  # ── History: measured temperature (from HA recorder) ─────────────────
  - entity: sensor.heating_assistant_living_room_predicted_temperature
    name: Measured
    yaxis_id: temp
    color: '#0D47A1'
    stroke_width: 2
    curve: smooth
    float_precision: 2
    extend_to: now
    group_by:
      func: raw
      fill: last
    show:
      in_header: true
  # ── Forecast: constraint upper bound ─────────────────────────────────
  - entity: sensor.heating_assistant_living_room_temperature_prediction
    name: Constraint Upper
    data_generator: |
      const fc = entity.attributes.forecast;
      const sp = entity.attributes.setpoint;
      const co = entity.attributes.constraint_offset;
      if (!fc || sp == null || co == null) return [];
      return fc.map(f => [new Date(f.time).getTime(), sp + co]);
    yaxis_id: temp
    color: '#90CAF9'
    stroke_width: 1
    curve: stepline
    opacity: 0.5
    show:
      legend_value: false
      in_header: false
  # ── Forecast: constraint lower bound ─────────────────────────────────
  - entity: sensor.heating_assistant_living_room_temperature_prediction
    name: Constraint Lower
    data_generator: |
      const fc = entity.attributes.forecast;
      const sp = entity.attributes.setpoint;
      const co = entity.attributes.constraint_offset;
      if (!fc || sp == null || co == null) return [];
      return fc.map(f => [new Date(f.time).getTime(), sp - co]);
    yaxis_id: temp
    color: '#90CAF9'
    stroke_width: 1
    curve: stepline
    opacity: 0.5
    show:
      legend_value: false
      in_header: false
  # ── Forecast: setpoint reference ─────────────────────────────────────
  - entity: sensor.heating_assistant_living_room_temperature_prediction
    name: Setpoint
    data_generator: |
      const fc = entity.attributes.forecast;
      const sp = entity.attributes.setpoint;
      if (!fc || sp == null) return [];
      return fc.map(f => [new Date(f.time).getTime(), sp]);
    yaxis_id: temp
    color: '#F44336'
    stroke_width: 2
    curve: stepline
    float_precision: 1
    show:
      in_header: true
  # ── Forecast: predicted temperature trajectory ───────────────────────
  - entity: sensor.heating_assistant_living_room_temperature_prediction
    name: Predicted
    data_generator: |
      const fc = entity.attributes.forecast;
      if (!fc) return [];
      return fc.map(f => [new Date(f.time).getTime(), f.temperature]);
    yaxis_id: temp
    color: '#1E88E5'
    stroke_width: 3
    curve: smooth
    float_precision: 2
    show:
      in_header: true
```

> **Tip:** Replace `living_room` with your room's entity suffix throughout.

#### 13.17.4 MPC control input card

Shows the controller's planned heating power as a step chart – the standard control input representation for zero-order-hold MPC.  Historical actual heating power from the recorder is shown to the left of the "Now" line for comparison.

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Living Room – Planned Heating Power
  show_states: true
graph_span: 9h
span:
  start: minute
  offset: '-6h'
now:
  show: true
  label: Now
  color: '#424242'
yaxis:
  - id: power
    min: 0
    apex_config:
      title:
        text: Heating Power (W)
      tickAmount: 4
series:
  # ── History: actual heating power (from HA recorder) ─────────────────
  - entity: sensor.heating_assistant_living_room_heating_power
    name: Actual Heating
    yaxis_id: power
    type: area
    curve: stepline
    color: '#BF360C'
    opacity: 0.2
    stroke_width: 2
    float_precision: 0
    extend_to: now
    group_by:
      func: raw
      fill: last
    show:
      in_header: true
  # ── Forecast: planned heating power ──────────────────────────────────
  - entity: sensor.heating_assistant_living_room_heating_plan_prediction
    name: Planned Heating
    data_generator: |
      const fc = entity.attributes.forecast;
      if (!fc) return [];
      return fc.map(f => [new Date(f.time).getTime(), f.heating_power]);
    yaxis_id: power
    type: area
    curve: stepline
    color: '#E65100'
    opacity: 0.35
    stroke_width: 2
    float_precision: 0
    show:
      in_header: true
```

#### 13.17.5 Disturbance forecast card

Shows the external disturbances the MPC controller accounts for: outdoor temperature and solar heat gain through windows.  Dual y-axes keep both signals readable.  Actual recorder history is shown to the left of the "Now" line alongside the forecasts to the right.

The outdoor temperature forecast is read from `sensor.heating_assistant_outdoor_temperature_prediction` (see § 13.8), which exposes a dedicated `forecast` attribute.  When a weather entity is configured, this forecast varies over the horizon; otherwise it is a flat persistence forecast equal to the current outdoor temperature.

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Living Room – Disturbance Forecast
  show_states: true
graph_span: 9h
span:
  start: minute
  offset: '-6h'
now:
  show: true
  label: Now
  color: '#424242'
yaxis:
  - id: temp
    apex_config:
      title:
        text: Outdoor Temp (°C)
  - id: power
    opposite: true
    min: 0
    apex_config:
      title:
        text: Solar Gain (W)
series:
  # ── History: actual outdoor temperature (from HA recorder) ───────────
  - entity: sensor.heating_assistant_outdoor_temperature
    name: Outdoor (actual)
    yaxis_id: temp
    color: '#37474F'
    stroke_width: 2
    curve: smooth
    float_precision: 1
    extend_to: now
    group_by:
      func: raw
      fill: last
    show:
      in_header: true
  # ── History: actual solar gain (from HA recorder) ────────────────────
  - entity: sensor.heating_assistant_living_room_solar_gain
    name: Solar (actual)
    yaxis_id: power
    type: area
    color: '#FF8F00'
    opacity: 0.25
    stroke_width: 2
    float_precision: 0
    extend_to: now
    group_by:
      func: raw
      fill: last
    show:
      in_header: true
  # ── Forecast: outdoor temperature ────────────────────────────────────
  - entity: sensor.heating_assistant_outdoor_temperature_prediction
    name: Outdoor (forecast)
    data_generator: |
      const fc = entity.attributes.forecast;
      if (!fc) return [];
      return fc.map(f => [new Date(f.time).getTime(), f.outdoor_temp ?? null]);
    yaxis_id: temp
    color: '#78909C'
    stroke_width: 2
    curve: smooth
    float_precision: 1
    show:
      in_header: true
  # ── Forecast: solar gain ─────────────────────────────────────────────
  - entity: sensor.heating_assistant_living_room_solar_power_prediction
    name: Solar (forecast)
    data_generator: |
      const fc = entity.attributes.forecast;
      if (!fc) return [];
      return fc.map(f => [new Date(f.time).getTime(), f.solar_gain]);
    yaxis_id: power
    type: area
    color: '#FFC107'
    opacity: 0.4
    stroke_width: 2
    float_precision: 0
    show:
      in_header: true
```

> **Tip:** Replace `living_room` with your room's entity suffix in the solar gain series.

#### 13.17.6 Room performance card

An entities card summarising the room's current state – useful at the top of each room subview.

```yaml
type: entities
title: Living Room – Current State
entities:
  - entity: climate.heating_assistant_living_room
    name: Thermostat
  - entity: sensor.heating_assistant_living_room_predicted_temperature
    name: Predicted Temperature
  - entity: sensor.heating_assistant_living_room_heating_power
    name: Heating Power
  - entity: sensor.heating_assistant_living_room_solar_gain
    name: Solar Gain
  - entity: sensor.heating_assistant_living_room_heat_loss
    name: Heat Loss
  - entity: sensor.heating_assistant_living_room_energy_balance
    name: Net Energy Balance
```

#### 13.17.7 System overview card

Place this on the main overview view for a system-wide summary.

```yaml
type: entities
title: Heating System Overview
entities:
  - entity: sensor.heating_assistant_system_summary
    name: Total Heating Power
  - entity: sensor.heating_assistant_outdoor_temperature
    name: Outdoor Temperature (measured)
  - entity: sensor.heating_assistant_outdoor_temperature_prediction
    name: Outdoor Temperature (forecast)
```

For systems with heat pumps, add:

```yaml
type: entities
title: Heat Pump Status
entities:
  - entity: sensor.heating_assistant_<source_name>_control_action
    name: Control Action
  - entity: sensor.heating_assistant_<source_name>_cop
    name: COP
```

#### 13.17.8 Complete room subboard example

Below is a complete vertical-stack card that combines all MPC panels for a single room.  Add this as the only card in a room subview configured with *Panel* view type for a clean full-width layout.

```yaml
type: vertical-stack
cards:
  # ── Room status ──────────────────────────────────────────────────────
  - type: entities
    title: Living Room – Current State
    entities:
      - entity: climate.heating_assistant_living_room
        name: Thermostat
      - entity: sensor.heating_assistant_living_room_predicted_temperature
        name: Predicted Temperature
      - entity: sensor.heating_assistant_living_room_energy_balance
        name: Net Energy Balance

  # ── MPC output: predicted temperature trajectory ─────────────────────
  - type: custom:apexcharts-card
    header:
      show: true
      title: Predicted Temperature
      show_states: true
    graph_span: 9h
    span:
      start: minute
      offset: '-6h'
    now:
      show: true
      label: Now
      color: '#424242'
    yaxis:
      - id: temp
        apex_config:
          title:
            text: Temperature (°C)
          tickAmount: 5
    series:
      # ── History: measured temperature (from HA recorder) ─────────────
      - entity: sensor.heating_assistant_living_room_predicted_temperature
        name: Measured
        yaxis_id: temp
        color: '#0D47A1'
        stroke_width: 2
        curve: smooth
        float_precision: 2
        extend_to: now
        group_by:
          func: raw
          fill: last
        show:
          in_header: true
      # ── Forecast: constraint upper bound ─────────────────────────────
      - entity: sensor.heating_assistant_living_room_temperature_prediction
        name: Constraint Upper
        data_generator: |
          const fc = entity.attributes.forecast;
          const sp = entity.attributes.setpoint;
          const co = entity.attributes.constraint_offset;
          if (!fc || sp == null || co == null) return [];
          return fc.map(f => [new Date(f.time).getTime(), sp + co]);
        yaxis_id: temp
        color: '#1565C0'
        stroke_width: 1
        show:
          legend_value: false
          in_header: false
      # ── Forecast: constraint lower bound ─────────────────────────────
      - entity: sensor.heating_assistant_living_room_temperature_prediction
        name: Constraint Lower
        data_generator: |
          const fc = entity.attributes.forecast;
          const sp = entity.attributes.setpoint;
          const co = entity.attributes.constraint_offset;
          if (!fc || sp == null || co == null) return [];
          return fc.map(f => [new Date(f.time).getTime(), sp - co]);
        yaxis_id: temp
        color: '#1565C0'
        stroke_width: 1
        show:
          legend_value: false
          in_header: false
      # ── Forecast: setpoint reference ─────────────────────────────────
      - entity: sensor.heating_assistant_living_room_temperature_prediction
        name: Setpoint
        data_generator: |
          const fc = entity.attributes.forecast;
          const sp = entity.attributes.setpoint;
          if (!fc || sp == null) return [];
          return fc.map(f => [new Date(f.time).getTime(), sp]);
        yaxis_id: temp
        color: '#F44336'
        stroke_width: 2
        curve: stepline
        float_precision: 1
        show:
          in_header: true
      # ── Forecast: predicted temperature trajectory ───────────────────
      - entity: sensor.heating_assistant_living_room_temperature_prediction
        name: Predicted
        data_generator: |
          const fc = entity.attributes.forecast;
          if (!fc) return [];
          return fc.map(f => [new Date(f.time).getTime(), f.temperature]);
        yaxis_id: temp
        color: '#1E88E5'
        stroke_width: 3
        curve: smooth
        float_precision: 2
        show:
          in_header: true

  # ── MPC input: planned heating power ─────────────────────────────────
  - type: custom:apexcharts-card
    header:
      show: true
      title: Planned Heating Power
      show_states: true
    graph_span: 9h
    span:
      start: minute
      offset: '-6h'
    now:
      show: true
      label: Now
      color: '#424242'
    yaxis:
      - id: power
        min: 0
        apex_config:
          title:
            text: Heating Power (W)
          tickAmount: 4
    series:
      # ── History: actual heating power (from HA recorder) ─────────────
      - entity: sensor.heating_assistant_living_room_heating_power
        name: Actual Heating
        yaxis_id: power
        type: area
        curve: stepline
        color: '#BF360C'
        opacity: 0.2
        stroke_width: 2
        float_precision: 0
        extend_to: now
        group_by:
          func: raw
          fill: last
        show:
          in_header: true
      # ── Forecast: planned heating power ──────────────────────────────
      - entity: sensor.heating_assistant_living_room_heating_plan_prediction
        name: Planned Heating
        data_generator: |
          const fc = entity.attributes.forecast;
          if (!fc) return [];
          return fc.map(f => [new Date(f.time).getTime(), f.heating_power]);
        yaxis_id: power
        type: area
        curve: stepline
        color: '#E65100'
        opacity: 0.35
        stroke_width: 2
        float_precision: 0
        show:
          in_header: true

  # ── MPC disturbances: outdoor temperature + solar gain ───────────────
  - type: custom:apexcharts-card
    header:
      show: true
      title: Disturbance Forecast
      show_states: true
    graph_span: 9h
    span:
      start: minute
      offset: '-6h'
    now:
      show: true
      label: Now
      color: '#424242'
    yaxis:
      - id: temp
        apex_config:
          title:
            text: Outdoor Temp (°C)
      - id: power
        opposite: true
        min: 0
        apex_config:
          title:
            text: Solar Gain (W)
    series:
      # ── History: actual outdoor temperature (from HA recorder) ───────
      - entity: sensor.heating_assistant_outdoor_temperature
        name: Outdoor (actual)
        yaxis_id: temp
        color: '#37474F'
        stroke_width: 2
        curve: smooth
        float_precision: 1
        extend_to: now
        group_by:
          func: raw
          fill: last
        show:
          in_header: true
      # ── History: actual solar gain (from HA recorder) ────────────────
      - entity: sensor.heating_assistant_living_room_solar_gain
        name: Solar (actual)
        yaxis_id: power
        type: area
        color: '#FF8F00'
        opacity: 0.25
        stroke_width: 2
        float_precision: 0
        extend_to: now
        group_by:
          func: raw
          fill: last
        show:
          in_header: true
      # ── Forecast: outdoor temperature ────────────────────────────────
      - entity: sensor.heating_assistant_outdoor_temperature_prediction
        name: Outdoor (forecast)
        data_generator: |
          const fc = entity.attributes.forecast;
          if (!fc) return [];
          return fc.map(f => [new Date(f.time).getTime(), f.outdoor_temp ?? null]);
        yaxis_id: temp
        color: '#78909C'
        stroke_width: 2
        curve: smooth
        float_precision: 1
        show:
          in_header: true
      # ── Forecast: solar gain ─────────────────────────────────────────
      - entity: sensor.heating_assistant_living_room_solar_power_prediction
        name: Solar (forecast)
        data_generator: |
          const fc = entity.attributes.forecast;
          if (!fc) return [];
          return fc.map(f => [new Date(f.time).getTime(), f.solar_gain]);
        yaxis_id: power
        type: area
        color: '#FFC107'
        opacity: 0.4
        stroke_width: 2
        float_precision: 0
        show:
          in_header: true
```

> **Adapting for other rooms:** Duplicate this vertical-stack card for each room subview and replace every occurrence of `living_room` with the room's entity suffix (e.g. `bedroom`, `kitchen`).  The example uses `graph_span: 9h` with `offset: '-6h'`, giving 6 h of recorder history before *Now*.  The MPC forecast appears after *Now*: with the default settings (`dt: 900`, `horizon: 6`) the prediction spans **90 minutes**.  To size the window exactly to your horizon, use **history = 2 × horizon** and **total span = 3 × horizon** — for the defaults that gives `graph_span: 4h30m` with `offset: '-3h'`; for `horizon: 12` use `graph_span: 9h` with `offset: '-6h'`.

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
| **Prediction horizon** | `horizon` | `6` steps | How many time steps ahead the controller plans.  Longer horizons give the controller more room to "see" the thermal inertia of the building and act proactively. |
| **Terminal weight** | `terminal_weight` | `100` | Multiplier λ on the terminal tracking cost **P** = λ**Q**.  A large value (≥ 50) forces the predicted trajectory to reach the setpoint by the end of the horizon, which is the primary mechanism for steady-state tracking.  Increase to 200–500 if the controller still crosses or misses the setpoint. |
| **Energy weight** | `energy_weight` | `0.01` | Weight on ‖**u**‖² — penalises running heaters.  Increase to make the controller more conservative (less aggressive heating). |
| **Smoothing weight** | `smoothing_weight` | `0.1` | Weight on ‖Δ**u**‖² — penalises changing the heater output from one step to the next.  Increase to dampen oscillations and reduce actuator wear. |
| **Constraint offset** | `constraint_offset` | `2.0 °C` | Half-width of the soft temperature band around the setpoint.  Does not directly affect oscillations but controls how strictly the constraint is enforced. |

All parameters are set under the top-level `heating_assistant:` key in `configuration.yaml`.  Restart Home Assistant after any change.

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

*Fix:* Re-estimate `thermal_mass` and `r_external` using the empirical method in [Section 14.1](#141-thermal-mass-thermal_mass) and [Section 14.2](#142-external-thermal-resistance-r_external), or run the `estimate_parameters` service (see [Section 13.11](#1311-setup-service--estimate-parameters)) or the automatic ML estimation service (see [Section 13.12](#1312-setup-service--estimate-parameters-ml)).

#### 14.5.3 Step-by-step detuning procedure

If you are experiencing oscillations, follow these steps in order:

1. **Check the predicted temperature sensor** (`sensor.heating_assistant_<room>_temperature_prediction`).  If the MPC prediction closely tracks the oscillation, the problem is in the controller weights.  If the prediction is smooth but the actual temperature oscillates, the issue is in the thermal model parameters.

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

The Controller-Tuning ApexCharts card in [`MODEL_FIT_GUIDE.md`](MODEL_FIT_GUIDE.md#apex-charts-card-controller-tuning-live-view) overlays the measured temperature, the MPC prediction, the setpoint, and the prediction error in a single time-aligned figure.  Add it to a room subview while iterating on the cost weights — the chart instantly reveals whether a tweak improved tracking or just hid an underlying model-fit problem.

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

**Typical solve times** at the default settings (`horizon = 6`, `update_interval = 900 s`, `n_int_steps = 10`) are 0.05–0.3 s depending on the number of rooms and CPU speed.  If `max_solve_time_s` approaches the `update_interval` (e.g. 900 s), consider reducing `horizon` or `n_int_steps`.

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

## 15. Developer Guide

### 15.1 Repository layout

```
HeatingAssistant/
├── custom_components/
│   └── heating_assistant/     ← HA integration (described above)
├── tests/
│   ├── __init__.py
│   ├── test_thermal_model.py     ← 11 tests: construction, step, predict, inter-room flow
│   ├── test_solar_model.py       ← 13 tests: angles, DNI, incidence, window gain
│   ├── test_heat_sources.py      ← 31 tests: electric, heat pump, COP curve, cooling, deadband
│   ├── test_controller.py        ← 44 tests: HouseThermalSDE, CD-EKF, CDTrackingOCP, HeatingMPCController
│   ├── test_climate.py           ←  9 tests: HVAC mode/action, heat pump cooling
│   ├── test_coordinator_apply_actions.py ← 31 tests: climate/switch/number dispatch, deadband, cooling
│   ├── test_model_diagnostics.py ← 25 tests: fit metrics, residuals, parameter validation, performance
│   ├── test_parameter_estimator.py ← 21 tests: Nelder-Mead, KalmanMLEstimator, joint identification
│   ├── test_visualisation.py     ← 47 tests: heat flows, time constant, predictions, forecast sensors
│   └── test_performance.py       ←  6 benchmarks: MPC and parameter-estimation run-times (3 slow)
├── BENCHMARKS.md              ← Latest performance benchmark results (auto-generated)
├── .gitignore
└── README.md
```

### 15.2 Running the tests

Install the required packages once:

```bash
pip install numpy scipy cvxopt homeassistant pytest voluptuous pytest-asyncio
pip install mbc @ git+https://github.com/marcuskrogh/mbc.git
```

Run the full test suite (skipping slow benchmarks):

```bash
python -m pytest tests/ -v -m "not slow"
```

Expected output: **235 tests pass** (3 slow parameter-estimation benchmarks deselected; 238 total including them).

Run a single test module:

```bash
python -m pytest tests/test_thermal_model.py -v
python -m pytest tests/test_solar_model.py -v
python -m pytest tests/test_heat_sources.py -v
python -m pytest tests/test_controller.py -v
python -m pytest tests/test_climate.py -v
python -m pytest tests/test_coordinator_apply_actions.py -v
python -m pytest tests/test_model_diagnostics.py -v
python -m pytest tests/test_parameter_estimator.py -v
python -m pytest tests/test_visualisation.py -v
python -m pytest tests/test_climate.py -v
python -m pytest tests/test_parameter_estimator.py -v
python -m pytest tests/test_model_diagnostics.py -v
```

### 15.3 Performance benchmarks

Run-time benchmarks for the active control step and parameter estimation routines are in `tests/test_performance.py`.  They cover three representative house configurations (studio, two-bedroom flat, full five-room house) and write results to `BENCHMARKS.md` in the repository root.

```bash
# Run all six benchmarks and regenerate BENCHMARKS.md
python -m pytest tests/test_performance.py -v -s
```

The three parameter-estimation benchmarks (Nelder-Mead + Kalman filter) are marked `@pytest.mark.slow` because they take 14–500 seconds each.  They are excluded from the normal test run by `-m "not slow"`.

Latest results are in [`BENCHMARKS.md`](BENCHMARKS.md).

### 15.4 Adding a new heat source type

1. **Add a new constant** in `const.py`:
   ```python
   SOURCE_TYPE_BOILER = "gas_boiler"
   ```

2. **Implement the model** in `heat_sources.py` by subclassing `HeatSource` and implementing `thermal_power()`:
   ```python
   class GasBoiler(HeatSource):
       def thermal_power(self, setpoint_fraction, outdoor_temp=0.0):
           return self.max_power * setpoint_fraction * self.efficiency
   ```

3. **Register the type** in `coordinator.py` inside `build_heat_sources()`:
   ```python
   elif src_type == SOURCE_TYPE_BOILER:
       sources.append(GasBoiler(...))
   ```

4. **Extend the YAML schema** in `__init__.py` inside `_SOURCE_SCHEMA` — add any new optional keys.

5. **Write tests** for the new class in `tests/test_heat_sources.py`.

### 15.5 Extending the solar model

The solar model in `solar_model.py` uses a **clear-sky** approximation (no clouds).  To add cloud cover correction:

1. Accept a `cloud_fraction` parameter (0–1) in `window_solar_gain()`.
2. Scale the DNI and DHI by `(1 − cloud_fraction)`.
3. In the coordinator, read a cloud-cover sensor entity and pass it to the solar model.

To use a measured irradiance sensor instead of a computed one:

1. Add an optional `irradiance_entity` key to the window configuration.
2. In the coordinator, read that entity's state and inject it directly into `window_solar_gain()` bypassing the model.

---

## 16. Troubleshooting

**Integration does not appear in the Add Integration search**

- Confirm the `custom_components/heating_assistant/` directory exists inside your HA config folder and contains `manifest.json`.
- Check the HA log (Settings → System → Logs) for import errors.  The most common cause is a dependency installation failure (`numpy`, `scipy`, `cvxopt`, or the `mbc` package); HA should install them automatically on first load but this can fail on restricted environments or if the system lacks internet access to GitHub (required for the `mbc` package).  In such cases, pre-install the dependencies manually: `pip install numpy scipy cvxopt` and `pip install git+https://github.com/marcuskrogh/mbc.git`.

**Rooms show no entities after adding the integration**

- Room and heat-source configuration comes from `configuration.yaml`, not the UI wizard.  Make sure you have added a `heating_assistant:` block with at least one `rooms` entry and restarted HA after saving.
- Check the HA log for YAML validation errors (malformed indentation, missing required keys, etc.).

**Room temperature sensor shows `unavailable`**

- Verify the `temp_sensor` entity ID is correct by checking it in Settings → Entities.
- If the sensor is unavailable at startup the model initialises with `temperature: 20.0` °C and self-corrects when the sensor comes back online.

**Heater entities are not being controlled**

- Confirm the `heater_entity` value is the correct HA entity ID (not the friendly name).
- Check that the entity domain is `switch`, `number`, or `climate`.  Other domains are not yet supported.
- Verify the entity is available (not `unavailable`); the coordinator skips entities whose state is `None`.

**Controller always outputs 0 (no heating)**

- Check that `outdoor_temp_entity` is configured and returning a plausible value.  If the fallback 5 °C is too warm relative to the room setpoints, the controller may decide no heating is needed.
- Increase the room `setpoint` temporarily to test that heaters respond.

**Solar gain seems too high or zero all the time**

- Confirm `latitude` and `longitude` are set correctly (wrong hemisphere or longitude can cause the sun to always appear below the horizon or at the wrong time).
- Check `orientation` values — 0 = North, 180 = South.  A common mistake is using meteorological convention (0 = South) instead of the navigation convention (0 = North) used here.

**Temperature oscillates (undershoot/overshoot)**

- Increase `smoothing_weight` (default `0.1`) — a higher value penalises rapid changes in the control input, dampening oscillations.  Try `0.5`, then `1.0`.  See [Section 14.5.2](#1452-diagnosing-and-correcting-oscillations) for a step-by-step guide.
- Increase `horizon` — a longer prediction horizon helps the controller anticipate the thermal inertia of the room and reduces overcorrection.
- Check `energy_weight` — if the energy penalty is too high, the controller heats too little, causing undershoot followed by overcorrection.  Try reducing from `0.01` to `0.005`.
- Reduce `update_interval` — a shorter time step gives the controller more opportunities to correct.

**Heat pump turns on and off too frequently (short-cycling)**

- Increase `smoothing_weight` — the rate-of-change penalty in the MPC cost function discourages the controller from toggling between heating and not-heating across consecutive time steps.  See [Section 14.5.4](#1454-effect-of-smoothing_weight-on-heat-pump-short-cycling).
- Increase `turn_off_deadband` on the heat pump source (default `1.0` °C) — this keeps the heat pump in heat mode until the room exceeds the setpoint by the configured deadband.  Try `1.5` or `2.0` °C if the compressor still cycles too often.

---

## 17. Roadmap

- [x] **Weather-API outdoor temperature forecast** — the controller can use a HA weather entity (e.g. Met.no, OpenWeatherMap) for multi-hour outdoor temperature forecasts instead of the persistence assumption.  Configure `weather_entity` in YAML or the UI wizard.
- [x] **Cooling mode** — heat-pump rooms expose `heat_cool` HVAC mode; two-threshold hysteresis (`turn_off_deadband`) governs mode switching.  MPC-requested active cooling (`fraction < 0`) uses `cool`/`dry`/`fan_only` with `target_temperature_cooling()`; passive cooling activates above `setpoint + deadband` and exits below `setpoint − deadband`.  Signed heating power (positive = heating, negative = cooling) is tracked throughout.
- [x] **Adaptive parameter estimation** — `estimate_parameters_ml` service uses CD-EKF maximum-likelihood estimation to jointly identify `thermal_mass`, `r_external`, `internal_gain`, heater power-scale factors, and inter-room resistances from accumulated operating history.  A one-press button entity triggers the full ML estimation pipeline.
- [x] **Model diagnostics** — `analyze_model_fit`, `validate_parameters`, `controller_performance_report`, and `run_open_loop_simulation` services provide comprehensive insight into model quality and controller behaviour.  Per-room diagnostic sensors (Prediction Error, Model Fit Quality, Parameter Confidence, Open-Loop RMSE, Kalman Innovation, Residual ACF) update every cycle.
- [ ] **Comfort schedule support** — define day/night/away setpoint profiles per room on a weekly timetable.
- [ ] **Energy price optimisation** — weight the energy cost term in the MPC by the time-of-use electricity tariff so the controller pre-heats the house before peak pricing periods.
- [ ] **GUI room editor** — add config-flow steps for defining rooms and heat sources through the UI, eliminating the YAML requirement.
- [ ] **Measured irradiance override** — allow a solar irradiance sensor to replace the clear-sky model for greater accuracy on cloudy days.
- [ ] **HACS integration** — publish to the HACS default repository for one-click installation.

---

## 18. References

1. ISO 13790:2008 — *Energy performance of buildings – Calculation of energy use for space heating and cooling.*
2. Duffie, J. A. & Beckman, W. A. (2013) — *Solar Engineering of Thermal Processes*, 4th edition, Wiley.
3. ASHRAE Fundamentals Handbook (2021), Chapter 14 — *Climatic Design Information.*
4. Kasten, F. & Young, A. T. (1989) — "Revised optical air mass tables and approximation formula", *Applied Optics*, 28(22), 4735–4738.
5. Spencer, J. W. (1971) — "Fourier series representation of the position of the sun", *Search*, 2(5), 172.
6. Cooper, P. I. (1969) — "The absorption of radiation in solar stills", *Solar Energy*, 12(3), 333–346.
7. Liu, B. Y. H. & Jordan, R. C. (1960) — "The interrelationship and characteristic distribution of direct, diffuse and total solar radiation", *Solar Energy*, 4(3), 1–19.
8. Mayne, D. Q. et al. (2000) — "Constrained model predictive control: Stability and optimality", *Automatica*, 36(6), 789–814.
9. Jazwinski, A. H. (1970) — *Stochastic Processes and Filtering Theory*, Academic Press.  (Continuous-Discrete Kalman filtering theory.)
10. Kristensen, N. R. et al. (2004) — "Parameter estimation in stochastic grey-box models", *Automatica*, 40(2), 225–237.  (CD-EKF prediction-error decomposition / ML identification.)

