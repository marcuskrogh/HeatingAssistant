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
11. [Complete Configuration Examples](#11-complete-configuration-examples)
    - 11.1 [Studio apartment – single room, one electric heater](#111-studio-apartment--single-room-one-electric-heater)
    - 11.2 [Two-bedroom flat – rooms with heat pump and supplemental heater](#112-two-bedroom-flat--rooms-with-heat-pump-and-supplemental-heater)
    - 11.3 [Full house – five rooms, heat pump, and solar-facing windows](#113-full-house--five-rooms-heat-pump-and-solar-facing-windows)
    - 11.4 [Multiple temperature sensors per room](#114-multiple-temperature-sensors-per-room)
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
13. [Advanced Visualisation and Setup Tools](#13-advanced-visualisation-and-setup-tools)
    - 13.1 [Visualisation sensors overview](#131-visualisation-sensors-overview)
    - 13.2 [Temperature forecast trajectory](#132-temperature-forecast-trajectory)
    - 13.3 [Heat loss analysis](#133-heat-loss-analysis)
    - 13.4 [Energy balance](#134-energy-balance)
    - 13.5 [System efficiency summary](#135-system-efficiency-summary)
    - 13.6 [Heating plan forecast](#136-heating-plan-forecast)
    - 13.7 [Solar gain forecast](#137-solar-gain-forecast)
    - 13.8 [Diagnostics panel](#138-diagnostics-panel)
    - 13.9 [Setup service – simulate thermal response](#139-setup-service--simulate-thermal-response)
    - 13.10 [Setup service – estimate parameters](#1310-setup-service--estimate-parameters)
    - 13.11 [Lovelace dashboard examples](#1311-lovelace-dashboard-examples)
14. [Thermal Model Parameter Estimation Guide](#14-thermal-model-parameter-estimation-guide)
    - 14.1 [Thermal mass `thermal_mass`](#141-thermal-mass-thermal_mass)
    - 14.2 [External thermal resistance `r_external`](#142-external-thermal-resistance-r_external)
    - 14.3 [Inter-room thermal resistance `r_value`](#143-inter-room-thermal-resistance-r_value)
    - 14.4 [Window orientation and tilt](#144-window-orientation-and-tilt)
15. [Developer Guide](#15-developer-guide)
    - 15.1 [Repository layout](#151-repository-layout)
    - 15.2 [Running the tests](#152-running-the-tests)
    - 15.3 [Adding a new heat source type](#153-adding-a-new-heat-source-type)
    - 15.4 [Extending the solar model](#154-extending-the-solar-model)
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
| **Turn-off deadband** | Heat pumps stay in heat mode (idling at the internal temperature) when the MPC says "no heat needed" but the room is still within a configurable deadband of the setpoint.  Only when the room exceeds *setpoint + deadband* does the unit actually turn off — dramatically reducing compressor short-cycling. |
| **Receding-horizon MPC** | Each control cycle the controller solves a quadratic program over the prediction horizon to find the continuous input sequence that minimises a cost of temperature tracking error, energy use, and input rate-of-change (Δu smoothing).  Inputs are applied via zero-order hold. |
| **Kalman filter state estimation** | A discrete-time Kalman filter fuses model predictions with sensor measurements, providing the minimum-variance state estimate under Gaussian noise assumptions. |
| **Generic MPC framework** | The controller is built on a generic framework (`LinearDiscreteModel`, `KalmanFilter`, `OptimalControlProblem`, `MPCController`) that can be reused for any linear discrete-time system. |
| **HA climate entities** | One `climate.*` entity per room exposes setpoint, current temperature, HVAC mode and action in the standard HA interface. |
| **HA sensor entities** | Predicted temperature and active heating power sensors per room, with model metadata exposed as state attributes. |
| **Advanced visualisation sensors** | Temperature forecast trajectory, heat loss breakdown, energy balance, and system efficiency sensors provide deep insight into system operation. |
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
│                          • Step "user": latitude, longitude, outdoor sensor, dt, horizon
│                          • Options flow: outdoor sensor, dt, horizon (post-install edit)
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
├── controller.py          MPC controller (generic framework + heating application)
│                          • LinearDiscreteModel (ABC): x[k+1] = A x[k] + B u[k] + E d[k]
│                          • KalmanFilter: discrete-time Kalman filter (state estimator)
│                          • OptimalControlProblem: batch QP over receding horizon
│                          • MPCController: generic MPC (KalmanFilter + OCP)
│                          • HouseThermalSystem: ZOH-discretised RC thermal model
│                          • HeatingMPCController: application facade (coordinator API)
│
├── diagnostics.py         HA diagnostics platform
│                          • async_get_config_entry_diagnostics(): full system state dump
│
├── services.yaml          Service definitions for setup assistance
│
├── climate.py             HA climate platform
│                          • RoomClimateEntity per room
│                          • Setpoint range: 5 °C – 30 °C, step 0.5 °C
│
└── sensor.py              HA sensor platform
                           • PredictedTemperatureSensor per room   [°C]
                           • HeatingPowerSensor per room           [W]
                           • SolarGainSensor per room              [W]
                           • TemperatureForecastSensor per room    [°C] (MPC trajectory + timestamped forecast)
                           • HeatLossSensor per room               [W]
                           • EnergyBalanceSensor per room          [W]
                           • HeatingPlanSensor per room            [W] (planned heating schedule)
                           • SolarForecastSensor per room          [W] (predicted solar gains)
                           • ControlActionSensor per heat source   [%]
                           • HeatPumpCOPSensor per heat pump
                           • OutdoorTemperatureSensor system-wide  [°C]
                           • SystemEfficiencySensor system-wide    [W]
```

### 2.2 Data flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Home Assistant state machine                                            │
│                                                                          │
│   sensor.outdoor_temp ──┐                                                │
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
  persistence  ──► outdoor_temp[k]     for k = 0…N-1

  D = disturbance forecast matrix (N × p)
  y = current room temperatures (measurement vector)

  ┌─ KalmanFilter.update(y, d[0])  →  x̂ (state estimate)
  │    predict: x̂⁻ = A x̂ + B u + E d,  P⁻ = A P Aᵀ + Q_w
  │    update:  K = P⁻ Cᵀ (C P⁻ Cᵀ + R_v)⁻¹
  │             x̂ = x̂⁻ + K (y − C x̂⁻),  P = (I − K C) P⁻
  │
  ├─ OptimalControlProblem.solve(x̂, D, x_ref)
  │    batch lift: X = Ψ x̂ + Γ U + Λ D
  │    QP:  min  Uᵀ H U + 2 fᵀ U   s.t.  0 ≤ u ≤ 1
  │    solve via projected gradient descent
  │
  └─ apply u[0] to heat sources (receding horizon)
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

### 3.3 Discretisation

The MPC controller uses **exact zero-order hold (ZOH) discretisation** of the continuous-time thermal model.  Given the continuous state-space form

$$\dot{\mathbf{T}} = \mathbf{F}\,\mathbf{T} + \mathbf{G}_u\,\mathbf{u} + \mathbf{G}_d\,\mathbf{d}$$

with $\mathbf{F} = \mathbf{C}_{\text{cap}}^{-1}\,\mathbf{A}$ (state matrix) and $\mathbf{G}_u$, $\mathbf{G}_d$ the input and disturbance matrices, the discrete-time matrices are computed as:

$$\mathbf{A}_d = \text{expm}(\mathbf{F} \cdot dt)$$

$$\mathbf{B}_d = \mathbf{F}^{-1}(\mathbf{A}_d - \mathbf{I}) \cdot \mathbf{G}_u$$

$$\mathbf{E}_d = \mathbf{F}^{-1}(\mathbf{A}_d - \mathbf{I}) \cdot \mathbf{G}_d$$

This gives the exact solution $\mathbf{T}[k{+}1] = \mathbf{A}_d\,\mathbf{T}[k] + \mathbf{B}_d\,\mathbf{u}[k] + \mathbf{E}_d\,\mathbf{d}[k]$ for piecewise-constant inputs held over each sampling interval $dt$.  Unlike forward Euler, ZOH discretisation is unconditionally stable and introduces no discretisation error for the assumed piecewise-constant input profile.

The `HouseModel.step()` and `HouseModel.predict()` methods still use forward Euler for fast multi-step rollouts (e.g. the `simulate_thermal_response` service).  The MPC controller exclusively uses ZOH for its prediction model.

For typical residential buildings (large thermal masses, slow dynamics) a step size `dt ≤ 900 s` (15 minutes) gives accurate results.  The default `dt = 900 s` is a good balance between accuracy and computational load.

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

$$I_{\text{diffuse}} = \text{DHI} \cdot \frac{1 + \cos \beta}{2} \quad$$ \text{(Liu \& Jordan isotropic sky model)}

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

**Minimum outdoor temperature:** if `T_outdoor < min_outdoor_temp` (default −20 °C) the heat pump shuts off completely (`COP = 0`) to represent the compressor lock-out that real units implement to avoid defrost damage.

**Offset-based setpoint control:** when the heat pump is connected via a `climate.*` entity, Heating Assistant reads the heat pump's own internal temperature sensor (`current_temperature` attribute) and sets the heat pump's target temperature to:

$$T_{\text{target}} = T_{\text{hp,internal}} + \text{fraction} \times \text{max temp offset}$$

where `max_temp_offset` (default 5 °C) is the maximum temperature differential at full power.  This makes the heat pump modulate its own output based on the gap between the setpoint it receives and its own temperature reading.  If the heat pump's internal temperature is unavailable, the HA room temperature is used as a fallback.

**Turn-off deadband:** to prevent aggressive on/off cycling of the compressor, heat pumps use a three-state control strategy based on the configurable `turn_off_deadband` parameter (default 1.0 °C):

| MPC fraction | Room temperature condition | Action |
|:---:|:---|:---|
| `> 0` | — | **Heat mode:** target = T_hp_internal + fraction × max_temp_offset |
| `= 0` | `room_temp ≤ setpoint + deadband` | **Idle:** stay in heat mode, set target = T_hp_internal (no offset — HP idles with minimal output) |
| `= 0` | `room_temp > setpoint + deadband` | **Off:** set HVAC mode to "off" |

This means the heat pump compressor keeps running (but produces minimal heat) when the room is near the setpoint, and only shuts down once the room is well above the target.  This dramatically reduces wear from short-cycling.

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

The controller (`controller.py`) implements a **generic model predictive control (MPC) framework** built on four composable components:

| Component | Class | Role |
|-----------|-------|------|
| **System model** | `LinearDiscreteModel` (ABC) | Defines the discrete-time dynamics `x[k+1] = A(d) x[k] + B(d) u[k] + E(d) d[k]`, `y[k] = C x[k]`.  Matrices may depend on disturbances (LPV). |
| **State estimator** | `KalmanFilter` | Discrete-time Kalman filter.  Fuses model predictions with sensor measurements to produce the minimum-variance state estimate. |
| **Optimal control** | `OptimalControlProblem` | Batch QP formulation of the receding-horizon regulator.  Lifts the problem to U-space and solves via projected gradient descent. |
| **MPC policy** | `MPCController` | Orchestrates estimate → optimise → apply at each step. |

The house-heating application provides two additional classes:

| Class | Role |
|-------|------|
| `HouseThermalSystem` | Concrete `LinearDiscreteModel` wrapping `HouseModel` and `HeatSource` objects.  ZOH-discretises the RC thermal model with LPV input matrix (heat-pump COP varies with outdoor temperature). |
| `HeatingMPCController` | Application facade.  Builds the system, estimator, and OCP; adds solar/outdoor forecasting; applies source set-points; exposes visualisation properties for the coordinator. |

At each control step the `HeatingMPCController`:

1. Reads room temperatures from HA sensors (measurement vector **y**).
2. Builds an *N*-step disturbance forecast **D** (outdoor temperature + solar gains).
3. Runs the Kalman filter to obtain the state estimate **x̂**.
4. Solves the quadratic program to find the optimal continuous input sequence **U***.
5. Applies only the **first step** u*[0] of the optimal sequence (receding horizon).

### 4.2 State estimation — Kalman filter

The state estimator is a standard **discrete-time Kalman filter**.  At each time step *k*:

**Prediction:**

$$\hat{\mathbf{x}}^{-}[k] = \mathbf{A}\,\hat{\mathbf{x}}[k{-}1] + \mathbf{B}\,\mathbf{u}[k{-}1] + \mathbf{E}\,\mathbf{d}[k{-}1]$$

$$\mathbf{P}^{-}[k] = \mathbf{A}\,\mathbf{P}[k{-}1]\,\mathbf{A}^\top + \mathbf{Q}_w$$

**Update (correction):**

$$\mathbf{S}[k] = \mathbf{C}\,\mathbf{P}^{-}[k]\,\mathbf{C}^\top + \mathbf{R}_v \qquad \text{(innovation covariance)}$$

$$\mathbf{K}[k] = \mathbf{P}^{-}[k]\,\mathbf{C}^\top\,\mathbf{S}[k]^{-1} \qquad \text{(Kalman gain)}$$

$$\hat{\mathbf{x}}[k] = \hat{\mathbf{x}}^{-}[k] + \mathbf{K}[k]\bigl(\mathbf{y}[k] - \mathbf{C}\,\hat{\mathbf{x}}^{-}[k]\bigr) \qquad \text{(corrected state estimate)}$$

$$\mathbf{P}[k] = \bigl(\mathbf{I} - \mathbf{K}[k]\,\mathbf{C}\bigr)\,\mathbf{P}^{-}[k] \qquad \text{(posterior covariance)}$$

| Symbol | Meaning |
|--------|---------|
| $\mathbf{Q}_w$ | Process noise covariance — models unmodelled disturbances and model mismatch (default: $0.01 \cdot \mathbf{I}$). |
| $\mathbf{R}_v$ | Measurement noise covariance — models sensor noise (default: $0.1 \cdot \mathbf{I}$). |
| $\mathbf{P}$ | State error covariance — propagated at every step; determines the time-varying Kalman gain. |
| $\mathbf{K}[k]$ | Kalman gain — automatically balances trust in the model vs. the sensors. |

For the house thermal system with full-state observation ($\mathbf{C} = \mathbf{I}$, one temperature sensor per room), the Kalman gain converges quickly to a steady-state value that weights measurements heavily relative to the model prediction.  The filter provides robustness against temporary sensor noise and gradual model drift.

### 4.3 Optimal control problem — batch QP

The cost function over the prediction horizon *N* is:

$$J(\mathbf{U}) = \sum_{k=0}^{N-1} \left\lVert \mathbf{x}[k{+}1] - \mathbf{r} \right\rVert_{\mathbf{Q}}^2 + \left\lVert \mathbf{u}[k] \right\rVert_{\mathbf{R}}^2 + \left\lVert \Delta\mathbf{u}[k] \right\rVert_{\mathbf{S}}^2 + \left\lVert \mathbf{x}[N] - \mathbf{r} \right\rVert_{\mathbf{P}}^2$$

where $\Delta\mathbf{u}[k] = \mathbf{u}[k] - \mathbf{u}[k{-}1]$ (with $\mathbf{u}[-1]$ equal to the previous step's applied input):

| Symbol | Value / meaning |
|--------|----------------|
| $\mathbf{x}[k]$ | Predicted state (room temperatures) at step *k* |
| $\mathbf{r}$ | Reference (room setpoints) |
| $\mathbf{Q}$ | State tracking cost (default: $\mathbf{I}$) |
| $\mathbf{R}$ | Input cost — $\text{energy weight} \cdot \mathbf{I}$ (default: $0.01 \cdot \mathbf{I}$) |
| $\mathbf{S}$ | Input rate-of-change cost — $\text{smoothing weight} \cdot \mathbf{I}$ (default: $0.1 \cdot \mathbf{I}$).  Penalises rapid input changes, producing smoother actuator behaviour.  Set `smoothing_weight` to `0.0` to disable. |
| $\mathbf{P}$ | Terminal cost (default: $\mathbf{Q}$) |
| $\mathbf{u}[k]$ | Input vector (continuous fractions $\in [0, 1]$) |

The problem is lifted to the **batch form** using prediction matrices:

$$\mathbf{X} = \boldsymbol{\Psi}\,\mathbf{x}_0 + \boldsymbol{\Gamma}\,\mathbf{U} + \boldsymbol{\Lambda}\,\mathbf{D} \qquad \text{(predicted state trajectory)}$$

$$J = \mathbf{U}^\top \mathbf{H}\,\mathbf{U} + 2\,\mathbf{f}^\top \mathbf{U} + \text{const}$$

$$\mathbf{H} = \boldsymbol{\Gamma}^\top \bar{\mathbf{Q}}\,\boldsymbol{\Gamma} + \bar{\mathbf{R}} + \mathbf{D}_{\text{diff}}^\top \bar{\mathbf{S}}\,\mathbf{D}_{\text{diff}}, \qquad \mathbf{f} = \boldsymbol{\Gamma}^\top \bar{\mathbf{Q}}\bigl(\boldsymbol{\Psi}\,\mathbf{x}_0 + \boldsymbol{\Lambda}\,\mathbf{D} - \bar{\mathbf{r}}\bigr) + \mathbf{D}_{\text{diff}}^\top \bar{\mathbf{S}}\,\mathbf{d}_0$$

where $\mathbf{D}_{\text{diff}}$ is the block first-difference matrix and $\mathbf{d}_0 = [-\mathbf{u}_{\text{prev}}, 0, \ldots, 0]^\top$ encodes the previous applied input.

Subject to the box constraint $0 \le \mathbf{u}[k] \le 1$ (actuator limits), the QP is solved via **projected gradient descent** with step size $\alpha = 1 / \lambda_{\max}(\mathbf{H})$.

The input cost $\mathbf{R}$ softly discourages running heaters when the room is close to setpoint.  Increasing `energy_weight` makes the controller more energy-conservative at the expense of tighter temperature tracking.

The smoothing cost $\mathbf{S}$ penalises *changes* in the control input from one step to the next.  This prevents the controller from toggling heaters on and off aggressively, resulting in more stable actuator commands and less wear on compressor-based heat sources.  Increasing `smoothing_weight` makes the controller more reluctant to change its actions between time steps.

**On-off sources** (e.g. `switch.*` entities) are modelled with a duty-cycle relaxation: the MPC optimises the continuous fraction $u \in [0, 1]$, interpreted as the proportion of the sampling interval $dt$ for which the source is active.  The coordinator maps this fraction to on/off commands.

### 4.4 Disturbance forecasts

The controller builds a disturbance forecast matrix $\mathbf{D} \in \mathbb{R}^{N \times p}$ before solving the QP:

| Disturbance | Forecast method |
|-------------|----------------|
| **Outdoor temperature** | Persistence: the current measured value is held constant for all horizon steps.  A weather API integration is on the roadmap. |
| **Solar gains** | The solar position model is evaluated at times `now + k·dt` for each horizon step `k`.  This uses the deterministic orbital equations and produces an accurate prediction of how solar irradiance through each window will evolve over the next `N·dt` seconds. |

### 4.5 Control cycle

Each call to `HeatingMPCController.compute()` follows this sequence:

```
compute(outdoor_temp, solar_gains=None, now=None)
│
├─ if solar_gains is None: compute from solar model
├─ _forecast_outdoor(outdoor_temp)     → list of N floats
├─ _forecast_solar(now)                → list of N {room: W} dicts
├─ Build D ∈ ℝ^(N × p) from forecasts
│
├─ KalmanFilter.update(y, d[0])        → x̂  (state estimate)
│   ├─ predict:  x̂⁻ = A x̂ + B u + E d,  P⁻ = A P Aᵀ + Q_w
│   └─ update:   K = P⁻ Cᵀ S⁻¹,  x̂ = x̂⁻ + K (y − C x̂⁻)
│
├─ OptimalControlProblem.solve(x̂, D, x_ref)
│   ├─ batch lift:  X = Ψ x̂ + Γ U + Λ D
│   ├─ QP:  min Uᵀ H U + 2 fᵀ U  s.t. 0 ≤ u ≤ 1
│   └─ solve via projected gradient descent
│
└─ Apply u*[0] to heat sources (receding horizon)
   Return {source_name: fraction}
```

---

## 5. Home Assistant Integration

### 5.1 Platforms and entities

Heating Assistant registers two HA platforms: **climate** and **sensor**.

For each room declared in `configuration.yaml` the integration creates:

| Entity ID | Platform | State | Attributes |
|-----------|----------|-------|------------|
| `climate.heating_assistant_<room_name>` | climate | HVAC mode (`heat` / `off`) | current_temperature, target_temperature |
| `sensor.heating_assistant_<room_name>_predicted_temperature` | sensor | Temperature in °C | setpoint, thermal_mass, r_external |
| `sensor.heating_assistant_<room_name>_heating_power` | sensor | Total heating power in W | Per-source breakdown by source name |
| `sensor.heating_assistant_<room_name>_solar_gain` | sensor | Current solar heat gain in W | window_count, total_window_area |
| `sensor.heating_assistant_<room_name>_temperature_forecast` | sensor | End-of-horizon temperature in °C | trajectory, forecast (timestamped), setpoint, horizon_steps |
| `sensor.heating_assistant_<room_name>_heat_loss` | sensor | Total heat loss in W | external_loss, per-room flows, outdoor_temp |
| `sensor.heating_assistant_<room_name>_energy_balance` | sensor | Net energy flow in W | heating_power, solar_gain, losses breakdown |
| `sensor.heating_assistant_<room_name>_heating_plan` | sensor | Current planned heating power in W | forecast (timestamped), horizon_steps |
| `sensor.heating_assistant_<room_name>_solar_forecast` | sensor | Current predicted solar gain in W | forecast (timestamped), horizon_steps, window_count |

**Climate entity behaviour:**

- **`target_temperature`** — the room setpoint.  Updated via `async_set_temperature()`.  Adjustable range: 5 °C – 30 °C in 0.5 °C steps.
- **`current_temperature`** — the latest room temperature (measured via `temp_sensor` if configured, otherwise the model's internal state).
- **`hvac_mode` = `heat`** when at least one heater in the room has `current_power > 0`; otherwise `off`.
- **Setting mode to `off`** immediately sets the room setpoint to 5 °C (frost protection).
- **Setting mode to `heat`** restores the default setpoint (21 °C) if the current setpoint is at the frost-protection floor.

### 5.2 Heater entity dispatch

When the coordinator applies actions it inspects the HA domain of each `heater_entity` and calls the appropriate service:

| HA domain | Service called | Payload |
|-----------|---------------|---------|
| `switch` | `switch.turn_on` / `switch.turn_off` | `entity_id` — turns on if fraction > 0.5 |
| `number` | `number.set_value` | `value = round(fraction × 100)` (0–100) |
| `climate` (non-heat-pump) | `climate.set_hvac_mode` + `climate.set_temperature` | `hvac_mode = "heat"` + room setpoint if fraction > 0, else `"off"` |
| `climate` (heat pump) | `climate.set_hvac_mode` + `climate.set_temperature` | Three-state deadband control (see below) |

**Heat pump climate entity control (deadband strategy)**

Heat pumps connected via `climate.*` entities use an offset-based, deadband-aware control strategy to avoid aggressive compressor cycling:

| MPC fraction | Room temperature | HVAC mode | Temperature setpoint |
|:---:|:---|:---:|:---|
| `> 0` | — | `heat` | `T_hp_internal + fraction × max_temp_offset` |
| `= 0` | `≤ setpoint + turn_off_deadband` | `heat` | `T_hp_internal` (idle — no offset, minimal output) |
| `= 0` | `> setpoint + turn_off_deadband` | `off` | — |

The heat pump's own internal temperature (`current_temperature` attribute on the climate entity) is read each cycle.  If unavailable, the HA room temperature from the configured `temp_sensor` is used as a fallback.

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

`numpy` is the only third-party Python dependency.  It is listed in `manifest.json` under `requirements` and Home Assistant will install it automatically into the HA virtual environment on first load.

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
           ├── climate.py
           ├── sensor.py
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
   | **Control time step (dt)** | Leave at `900` (15 minutes) unless you have a specific reason to change it. |
   | **MPC prediction horizon** | Leave at `6` (90-minute lookahead at dt = 900 s).  Increase to `8`–`12` for buildings with high thermal mass. |

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
   | `sensor.heating_assistant_<room_name>_heat_loss` | Heat loss breakdown [W] |
   | `sensor.heating_assistant_<room_name>_energy_balance` | Net energy flow [W] |
   | `sensor.heating_assistant_<room_name>_heating_plan` | Planned heating schedule [W] |
   | `sensor.heating_assistant_<room_name>_solar_forecast` | Predicted solar gain schedule [W] |

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
| Predicted temperature diverges quickly from actual | Wrong `thermal_mass` or `r_external` | Compare steady-state heat loss empirically (see [Section 13.2](#132-external-thermal-resistance-r_external)) |
| Temperature oscillates (undershoot then overshoot) | Horizon too short | Increase `horizon` (e.g. from `6` to `8`) |
| Heater runs at full power then cuts out abruptly | `energy_weight` too low | No direct config key yet; increase `horizon` as an alternative |
| Solar gain is always zero | Wrong `latitude`/`longitude` or wrong window `orientation` | Verify coordinates; remember `orientation: 0` = North, `180` = South |

After any change to `configuration.yaml` (rooms, heat sources, or top-level keys), **restart HA** for the changes to take effect.

Refer to [Section 13](#13-thermal-model-parameter-estimation-guide) for detailed guidance on estimating thermal parameters, and to [Section 15](#15-troubleshooting) for a full list of known issues and their solutions.

---

## 9. Setup Wizard

After installation, navigate to **Settings → Devices & Services → + Add Integration** and search for **Heating Assistant**.  A single-step form will appear:

| Field | Default | Description |
|-------|---------|-------------|
| **Latitude** | HA configured latitude | Site latitude in decimal degrees (positive = North). Used to compute solar position. |
| **Longitude** | HA configured longitude | Site longitude in decimal degrees (positive = East). Used to compute solar position. |
| **Outdoor temperature sensor entity ID** | *(empty)* | The entity ID of a HA temperature sensor that measures outdoor air temperature (e.g. `sensor.openweathermap_temperature`, `sensor.netatmo_outdoor_temperature`).  If left blank the controller uses a fallback of 5 °C — configure this for accurate operation. |
| **Control time step (dt)** | 900 | Interval in seconds at which the MPC controller advances its simulation.  Range: 60–3600.  Default 900 s = 15 minutes. |
| **MPC prediction horizon** | 6 | Number of dt steps to look ahead.  At dt=900 s, horizon=6 means 90 minutes of prediction. Range: 1–24. |

After saving, the integration entry is created.  The room topology and heat-source configuration still need to be added to `configuration.yaml`.

To **edit** the outdoor sensor, dt, or horizon after installation:  
Settings → Devices & Services → Heating Assistant → Configure.

---

## 10. Configuration Reference

All room, window, and heat-source configuration is declared in `configuration.yaml` under the `heating_assistant:` key.

```yaml
heating_assistant:
  outdoor_temp_entity: ...
  latitude: ...
  longitude: ...
  dt: ...
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
| `latitude` | float | No | HA / wizard setting | Site latitude [°].  Overrides the wizard value. |
| `longitude` | float | No | HA / wizard setting | Site longitude [°].  Overrides the wizard value. |
| `dt` | int | No | `900` | Control time step [s].  Range 60–3600. |
| `horizon` | int | No | `6` | MPC prediction horizon [steps].  Range 1–24. |
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
| `thermal_mass` | float | No | `5 000 000` | Effective heat capacity of the room [J/K].  Includes air mass, furniture, interior walls, and a fraction of the exterior walls.  See [Section 13.1](#131-thermal-mass-thermal_mass) for guidance. |
| `r_external` | float | No | `0.05` | Thermal resistance from the room to the outdoor environment [K/W].  Represents the sum of all paths to the outside: exterior walls, roof, ground, and infiltration.  See [Section 13.2](#132-external-thermal-resistance-r_external) for guidance. |
| `setpoint` | float | No | `21.0` | Initial desired temperature [°C].  Can be overridden at runtime by the `climate.*` entity. |
| `temp_sensor` | string | No | — | Entity ID of a single HA sensor that measures the actual room temperature.  If provided, this value is used to correct the model state at each update cycle.  Without a sensor, the model runs in open-loop (simulation-only) mode.  Cannot be combined with `temp_sensors`. |
| `temp_sensors` | list of strings | No | — | List of HA sensor entity IDs for the room.  The coordinator reads all of them at each update cycle and uses their **arithmetic mean** as the measured room temperature.  Useful when the room is large or has significant temperature gradients.  Cannot be combined with `temp_sensor`. |
| `connections` | list | No | `[]` | List of thermal connections to adjacent rooms. |
| `windows` | list | No | `[]` | List of window definitions for solar gain calculation. |

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
| `r_value` | float | **Yes** | — | Thermal resistance between the two rooms [K/W].  See [Section 13.3](#133-inter-room-thermal-resistance-r_value) for guidance. |

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
| `turn_off_deadband` | float | No | `1.0` | Temperature [°C] above the room setpoint before the heat pump actually turns off.  When the MPC outputs zero heating demand but the room is still within this deadband of the setpoint, the heat pump stays in heat mode at idle (target = internal temperature, no offset) instead of turning off.  This prevents aggressive compressor short-cycling.  Increase this value if you notice the compressor toggling frequently. |

---

## 11. Complete Configuration Examples

### 11.1 Studio apartment – single room, one electric heater

A single-room installation with one window and a direct plug-in electric heater controlled via a smart plug (switch entity).

```yaml
heating_assistant:
  outdoor_temp_entity: sensor.openweathermap_temperature

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
  dt: 900       # 15-minute MPC steps
  horizon: 8    # 2-hour lookahead

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
      turn_off_deadband: 1.0  # °C above setpoint before actual turn-off
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
  dt: 900
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
      turn_off_deadband: 1.0  # °C above setpoint before actual turn-off
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
  dt: 900
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

---

## 12. Entity Reference

### 12.1 Climate entities

**Entity ID format:** `climate.heating_assistant_<room_name>`

| Attribute | Value | Notes |
|-----------|-------|-------|
| `state` | `heat` or `off` | `heat` when any heater in the room has `current_power > 0` |
| `current_temperature` | float [°C] | Latest room temperature from sensor or model |
| `temperature` | float [°C] | Current setpoint (read by Lovelace thermostat cards) |
| `hvac_action` | `heating` or `idle` | Mirrors the HVAC mode |
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
| `turn_off_deadband` | float | Temperature above setpoint before actual turn-off [°C] |
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

---

## 13. Advanced Visualisation and Setup Tools

This section describes the advanced visualisation sensors and setup assistance services that help you understand, monitor, and tune your heating system.

### 13.1 Visualisation sensors overview

In addition to the basic sensors (predicted temperature, heating power, solar gain), the integration creates six advanced sensor types that provide deep insight into system operation:

| Sensor | Per-room | Purpose |
|--------|:--------:|---------|
| **Temperature Forecast** | ✓ | MPC-predicted temperature trajectory over the prediction horizon, plus a timestamped `forecast` attribute for charting |
| **Heat Loss** | ✓ | Instantaneous heat-loss breakdown (external + inter-room components) |
| **Energy Balance** | ✓ | Net energy flow: heating + solar − losses |
| **Heating Plan** | ✓ | Planned heating power schedule over the full MPC horizon, as a timestamped `forecast` attribute |
| **Solar Forecast** | ✓ | Predicted solar heat gain over the full MPC horizon, as a timestamped `forecast` attribute |
| **System Summary** | ✗ (1 total) | Aggregate system metrics: total power, COP, active sources |

All sensors update every coordinator cycle (default 60 seconds) and expose detailed breakdowns as state attributes that can be plotted in Lovelace dashboards.

### 13.2 Temperature forecast trajectory

The **Temperature Forecast** sensor shows what the MPC controller *predicts* will happen to the room temperature over the prediction horizon (e.g. the next 90 minutes at default settings).

- **State:** predicted temperature at the end of the horizon [°C]
- **`trajectory` attribute:** list of predicted temperatures at each time step, enabling a multi-point chart
- **`forecast` attribute:** timestamped list of dicts — each entry contains `time` (ISO-8601 UTC), `temperature` (°C), `heating_power` (W), `solar_gain` (W), and `outdoor_temp` (°C).  This combined attribute lets a single dashboard card show all forecast signals on one chart.

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

The **Heating Plan** sensor shows the controller's *intended* heating schedule for each room over the full MPC horizon.

- **State:** planned heating power for the current step [W]
- **`forecast` attribute:** timestamped list of dicts — each entry contains `time` (ISO-8601 UTC) and `heating_power` (W).

This is useful for:
- Seeing in advance whether the controller intends to pre-heat a room before the setpoint is needed
- Comparing the planned heating schedule against actual solar gain to understand how the controller balances the two
- Verifying that the `energy_weight` is not making the controller too reluctant to heat

### 13.7 Solar gain forecast

The **Solar Forecast** sensor shows the deterministic solar heat-gain prediction for each room over the full MPC horizon.

- **State:** predicted solar gain for the current step [W]
- **`forecast` attribute:** timestamped list of dicts — each entry contains `time` (ISO-8601 UTC) and `solar_gain` (W).

Because the solar position model is fully deterministic, this forecast is exact (assuming clear skies) and reflects the sun's trajectory over the coming horizon period.  This is useful for:
- Confirming that the solar model is producing sensible predictions for your location and window orientations
- Understanding why the controller is choosing to heat less in rooms with south-facing windows
- Identifying the peak solar gain time of day for each room

### 13.8 Diagnostics panel

The integration includes a full **HA diagnostics platform**.  Access it via:

> **Settings → Devices & Services → Heating Assistant → ⋮ (three dots) → Download diagnostics**

The diagnostics dump includes:

- **Room configuration:** thermal mass, R-values, time constants, connections, windows
- **Heat source details:** type, power, COP, current state
- **Heat flow breakdown:** per-room heat loss/gain components
- **Prediction trajectory:** MPC-predicted temperatures for each future step
- **Solar gains:** current solar heat gain per room
- **Steady-state analysis:** predicted steady-state temperatures at −10 °C, 0 °C, and 5 °C outdoor temperature using maximum heating power
- **Controller parameters:** horizon, dt, latitude, longitude

This is invaluable for troubleshooting or sharing your system configuration with others.

### 13.9 Setup service – simulate thermal response

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

### 13.10 Setup service – estimate parameters

Service: `heating_assistant.estimate_parameters`

This service back-calculates `thermal_mass` and `r_external` from an observed heating experiment.  You heat a room with known power and known outdoor temperature, record the start and end temperatures and the duration, and the service estimates the thermal parameters.

**Service data:**

| Field | Type | Description |
|-------|------|-------------|
| `room_name` | string | Room name |
| `heating_power` | float | Power applied during the experiment [W] |
| `outdoor_temp` | float | Outdoor temperature during the experiment [°C] |
| `initial_temp` | float | Room temperature at the start [°C] |
| `final_temp` | float | Room temperature at the end [°C] |
| `duration_seconds` | float | Duration of the experiment [s] |

**Example call:**

```yaml
service: heating_assistant.estimate_parameters
data:
  room_name: "bedroom"
  heating_power: 1500
  outdoor_temp: 3.0
  initial_temp: 14.0
  final_temp: 18.0
  duration_seconds: 7200
```

**Result:** A persistent notification with estimated `thermal_mass` and `r_external`, compared to the current configuration values.

### 13.11 Lovelace dashboard examples

Below are example Lovelace card configurations for visualising the advanced sensors.

#### Setting up apexcharts-card (HACS)

The timestamped `forecast` attributes on `TemperatureForecastSensor`, `HeatingPlanSensor`, and `SolarForecastSensor` are designed to be consumed directly by [apexcharts-card](https://github.com/RomRider/apexcharts-card), a popular HACS community card.  Install it via HACS → Frontend → apexcharts-card before using the examples below.

**Temperature + heating plan + solar forecast on one chart (apexcharts-card):**

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Living Room Forecast
graph_span: 2h
now:
  show: true
  label: Now
series:
  - entity: sensor.heating_assistant_living_room_temperature_forecast
    name: Temperature (°C)
    attribute: forecast
    data_generator: |
      return entity.attributes.forecast.map(f => [new Date(f.time).getTime(), f.temperature]);
    yaxis_id: temp
  - entity: sensor.heating_assistant_living_room_temperature_forecast
    name: Heating Power (W)
    attribute: forecast
    data_generator: |
      return entity.attributes.forecast.map(f => [new Date(f.time).getTime(), f.heating_power ?? 0]);
    yaxis_id: power
    type: area
    opacity: 0.3
  - entity: sensor.heating_assistant_living_room_temperature_forecast
    name: Solar Gain (W)
    attribute: forecast
    data_generator: |
      return entity.attributes.forecast.map(f => [new Date(f.time).getTime(), f.solar_gain ?? 0]);
    yaxis_id: power
    type: area
    opacity: 0.2
yaxis:
  - id: temp
    apex_config:
      title:
        text: Temperature (°C)
  - id: power
    opposite: true
    apex_config:
      title:
        text: Power (W)
```

**Heating plan schedule (apexcharts-card):**

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Living Room Heating Plan
graph_span: 2h
now:
  show: true
  label: Now
series:
  - entity: sensor.heating_assistant_living_room_heating_plan
    name: Planned Heating (W)
    attribute: forecast
    data_generator: |
      return entity.attributes.forecast.map(f => [new Date(f.time).getTime(), f.heating_power]);
    type: column
```

**Solar gain forecast (apexcharts-card):**

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Living Room Solar Forecast
graph_span: 8h
now:
  show: true
  label: Now
series:
  - entity: sensor.heating_assistant_living_room_solar_forecast
    name: Predicted Solar Gain (W)
    attribute: forecast
    data_generator: |
      return entity.attributes.forecast.map(f => [new Date(f.time).getTime(), f.solar_gain]);
    type: area
    color: '#FFC107'
    opacity: 0.5
```

**Temperature forecast chart (using mini-graph-card):**

```yaml
type: custom:mini-graph-card
entities:
  - entity: sensor.heating_assistant_living_room_predicted_temperature
    name: Current
  - entity: sensor.heating_assistant_living_room_temperature_forecast
    name: Forecast
name: Living Room Temperature Forecast
hours_to_show: 4
```

**Energy balance overview (using entities card):**

```yaml
type: entities
title: Living Room Energy Balance
entities:
  - entity: sensor.heating_assistant_living_room_energy_balance
    name: Net Energy Flow
  - entity: sensor.heating_assistant_living_room_heating_power
    name: Heating Power
  - entity: sensor.heating_assistant_living_room_solar_gain
    name: Solar Gain
  - entity: sensor.heating_assistant_living_room_heat_loss
    name: Heat Loss
```

**System summary (using entities card):**

```yaml
type: entities
title: Heating System Summary
entities:
  - entity: sensor.heating_assistant_system_summary
    name: Total Heating Power
  - entity: sensor.heating_assistant_outdoor_temperature
    name: Outdoor Temperature
```

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

---

## 15. Developer Guide

### 15.1 Repository layout

```
HeatingAssistant/
├── custom_components/
│   └── heating_assistant/     ← HA integration (described above)
├── tests/
│   ├── __init__.py
│   ├── test_thermal_model.py  ← 11 tests: construction, step, predict, inter-room flow
│   ├── test_solar_model.py    ← 13 tests: angles, DNI, incidence, window gain
│   ├── test_heat_sources.py   ← 20 tests: electric, heat pump, COP curve, deadband
│   ├── test_controller.py     ← 16 tests: actions, fractions, heating/off, smoothing
│   ├── test_coordinator_apply_actions.py ← 13 tests: climate/switch/number dispatch, deadband
│   └── test_visualisation.py  ← 27 tests: heat flows, time constant, steady state, predictions, forecast sensors
├── .gitignore
└── README.md
```

### 15.2 Running the tests

Install the required packages once:

```bash
pip install numpy homeassistant pytest voluptuous pytest-asyncio
```

Run the full test suite:

```bash
python -m pytest tests/ -v
```

Expected output: **122 tests pass**.

Run a single test module:

```bash
python -m pytest tests/test_thermal_model.py -v
python -m pytest tests/test_solar_model.py -v
python -m pytest tests/test_heat_sources.py -v
python -m pytest tests/test_controller.py -v
python -m pytest tests/test_coordinator_apply_actions.py -v
python -m pytest tests/test_visualisation.py -v
```

### 15.3 Adding a new heat source type

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

### 15.4 Extending the solar model

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
- Check the HA log (Settings → System → Logs) for import errors.  The most common cause is a missing `numpy` installation; HA should install it automatically but this can fail on restricted environments.

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

- Increase `horizon` — a longer prediction horizon helps the controller anticipate the thermal inertia of the room.
- Reduce `energy_weight` — if the energy penalty is too high, the controller may heat too little, causing undershoot; then overheat on the next cycle.
- Reduce `dt` — finer time steps give the controller more opportunities to correct.
- Increase `smoothing_weight` (default `0.1`) — a higher value penalises rapid changes in the control input, resulting in smoother actuator commands.

**Heat pump turns on and off too frequently (short-cycling)**

- Increase `turn_off_deadband` (default `1.0` °C) — this keeps the heat pump in heat mode (idling at the internal temperature) until the room exceeds the setpoint by the configured deadband.  Try `1.5` or `2.0` °C if the compressor still cycles too often.
- Increase `smoothing_weight` — the rate-of-change penalty in the MPC cost function discourages the controller from toggling between heating and not-heating across consecutive time steps.

---

## 17. Roadmap

- [ ] **Weather-API outdoor temperature forecast** — replace the persistence assumption with a multi-hour forecast from an integrated HA weather entity.
- [ ] **Comfort schedule support** — define day/night/away setpoint profiles per room on a weekly timetable.
- [ ] **Energy price optimisation** — weight the energy cost term in the MPC by the time-of-use electricity tariff so the controller pre-heats the house before peak pricing periods.
- [ ] **GUI room editor** — add config-flow steps for defining rooms and heat sources through the UI, eliminating the YAML requirement.
- [ ] **Cooling mode** — extend heat pump entities to support `cool` HVAC mode for reversible (air-conditioning) heat pumps.
- [ ] **Measured irradiance override** — allow a solar irradiance sensor to replace the clear-sky model for greater accuracy on cloudy days.
- [ ] **HACS integration** — publish to the HACS default repository for one-click installation.
- [ ] **Adaptive parameter estimation** — implement a recursive least-squares estimator to fine-tune `thermal_mass` and `r_external` from measured temperature trajectories.

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

