# Architecture & Developer Guide

> The internal architecture of Heating Assistant — file layout, data flow, the
> repository structure, how to run the test suite and benchmarks, and how to
> extend the integration with new heat sources or solar inputs.

For the mathematics implemented by these modules, see [Physics, Models &
Control Theory](THEORY.md). For the planned evolution of the codebase, see the
[Roadmap](ROADMAP.md).

**Contents**

- [2. System Architecture](#2-system-architecture) — file layout & data flow
- [15. Developer Guide](#15-developer-guide) — tests, benchmarks, extension points

---

## 2. System Architecture

### 2.1 File layout

```
custom_components/heating_assistant/
│
├── manifest.json          Integration metadata: name, version, requirements, iot_class
├── translations/
│   └── en.json            English strings for the UI setup wizard
├── strings.json           Default strings (fallback for translations)
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
│                          • Options flow: reconfigure site settings post-install
│
├── _options_flow.py       Options flow helpers (room/source/schedule editors)
│
├── coordinator.py         HeatingAssistantCoordinator (DataUpdateCoordinator)
│                          • Builds HouseModel and heat sources from config
│                          • _async_update_data() called every UPDATE_INTERVAL seconds
│                          • Reads sensor states → runs MPC → writes heater actions
│
├── thermal_model.py       Physics: lumped 2R2C model (fast air + slow wall node)
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
│                          •   → uses CDLinearizedMPCController from mbc.control
│
├── integrator.py          Numerical integration (implicit Euler)
│                          • Shared integration helper for thermal model stepping
│
├── parameter_estimator.py ML thermal parameter estimation
│                          • KalmanMLEstimator: maximum-likelihood thermal parameter fitting
│                          • CD-EKF prediction-error decomposition log-likelihood
│                          • estimate(): returns {room: {thermal_mass, r_external}}
│
├── model_diagnostics.py   Analysis tools for tuning and validation
│                          • Prediction-error analysis (RMSE, MAE, R², bias)
│                          • Residual autocorrelation (Ljung–Box Q test)
│                          • Open-loop multi-step simulation
│                          • Controller performance report
│
├── sysid.py               System identification via EKF
│                          • EKF reconstruction over history window
│                          • One-step-ahead predictions with confidence bands
│
├── weather.py             Weather forecast integration
│                          • Reads outdoor temperature from HA sensor
│                          • Fetches forecasts via weather.get_forecasts service
│                          • Cloud cover mapping from weather conditions
│
├── schedule.py            Comfort schedules (time-of-day setback)
│                          • RoomSchedule: named periods with start/end/days/mode
│                          • active_period(), effective_setpoint(), is_enabled()
│
├── ground_temp.py         Ground temperature model (sinusoidal annual cycle)
│
├── yaml_merge.py          YAML configuration merge utilities
│
├── dashboard.py           Lovelace dashboard generator
│                          • Classic dashboard: overview + per-room subviews + diagnostics
│                          • Industrial dashboard: process-control style UI
│
├── diagnostics.py         HA diagnostics platform
│                          • async_get_config_entry_diagnostics(): full system state dump
│
├── services.yaml          Service definitions (12 services)
│
├── climate.py             HA climate platform
│                          • RoomClimateEntity per room
│                          • Modes: heat_cool (heat-pump rooms) / heat / off
│                          • Actions: heating / cooling / idle
│                          • Setpoint range: 5 °C – 30 °C, step 0.5 °C
│
├── button.py              HA button platform
│                          • EstimateParametersButton: triggers ML parameter estimation
│                          • ResetParametersButton: reverts to configured defaults
│
├── sensor.py              HA sensor platform (100+ entities)
│                          • PredictedTemperatureSensor per room         [°C]
│                          • HeatingPowerSensor per room                 [W]
│                          • SolarGainSensor per room                    [W]
│                          • TemperatureForecastSensor per room          [°C]
│                          • HeatLossSensor per room                     [W]
│                          • EnergyBalanceSensor per room                [W]
│                          • HeatingPlanSensor per room                  [W]
│                          • SolarForecastSensor per room                [W]
│                          • PredictionErrorSensor per room              [°C]
│                          • ModelFitQualitySensor per room              [–]
│                          • ParameterConfidenceSensor per room          [–]
│                          • OpenLoopRMSESensor per room                 [°C]
│                          • KalmanInnovationSensor per room             [°C]
│                          • ResidualACFSensor per room                  [–]
│                          • ControlActionSensor per heat source         [%]
│                          • HeatPumpCOPSensor per heat pump             [–]
│                          • OutdoorTemperatureSensor system-wide        [°C]
│                          • OutdoorForecastSensor system-wide           [°C]
│                          • SystemEfficiencySensor system-wide          [W]
│                          • MPCPerformanceSensor system-wide            [–]
│
└── www/                   Frontend assets (industrial dashboard)
    ├── js/                Custom web components (gauges, KPI cards, charts)
    ├── css/industrial.css Industrial dashboard styling
    └── vendor/            Bundled Chart.js library
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
│        │  HeatingAssistantCoordinator │  (every 900 s / 15 min)          │
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
  │             over [tₖ₋₁, tₖ] using implicit-Euler sub-steps
  │    update:  K = P⁻ Hᵀ (H P⁻ Hᵀ + Rₘ)⁻¹
  │             x̂ = x̂⁻ + K (y − hm(x̂⁻)),  P = (I − K H) P⁻
  │
  ├─ CDLinearizedMPCController.solve(x̂, D)
  │    Linearize → ZOH-discretize → batch convex QP:
  │          min  Σ ‖z[k] − z_ref‖²_Q + ‖u[k]‖²_R + ‖Δu[k]‖²_S
  │               + ρ_z (soft constraint violation penalty)
  │          s.t.  0 ≤ u ≤ 1  (hard input box)
  │    solve via OSQP/HiGHS
  │
  └─ apply u*[0] to heat sources (receding horizon)
```


## 15. Developer Guide

### 15.1 Repository layout

```
HeatingAssistant/
├── custom_components/
│   └── heating_assistant/     ← HA integration (described above)
│       └── www/               ← Frontend assets (industrial dashboard)
├── tests/
│   ├── __init__.py
│   ├── conftest.py              ← Shared pytest fixtures
│   ├── test_thermal_model.py    ← Physics model validation
│   ├── test_solar_model.py      ← Solar irradiance pipeline
│   ├── test_heat_sources.py     ← Electric heater, heat pump, COP curve, cooling
│   ├── test_controller.py       ← HouseThermalSDE, CD-EKF, MPC solver
│   ├── test_climate.py          ← HVAC mode/action, heat pump cooling
│   ├── test_coordinator_apply_actions.py ← Heater dispatch logic
│   ├── test_model_diagnostics.py ← Fit metrics, residuals, parameter validation
│   ├── test_parameter_estimator.py ← ML identification
│   ├── test_estimation_button.py ← Button entity triggers
│   ├── test_persist_estimated_params.py ← Parameter persistence across restarts
│   ├── test_visualisation.py    ← Heat flows, time constant, predictions
│   ├── test_visualisation_sensors.py ← Forecast sensor entities
│   ├── test_prediction_sensors.py ← Prediction sensor generation
│   ├── test_sensor_metadata.py  ← Entity metadata validation
│   ├── test_schedule.py         ← Comfort schedule logic
│   ├── test_schedule_awareness.py ← MPC schedule-awareness
│   ├── test_weather_module.py   ← Forecast handling
│   ├── test_weather_status.py   ← Weather entity status
│   ├── test_window_override.py  ← Window-open state machine
│   ├── test_dashboard.py        ← Lovelace generation
│   ├── test_dashboard_auto_write.py ← Auto-write dashboard on setup
│   ├── test_integrator.py       ← Implicit Euler integration
│   ├── test_infiltration.py     ← Infiltration model
│   ├── test_2r2c.py             ← Future 2R2C envelope model
│   ├── test_slab_ufh.py         ← Future slab/UFH model
│   ├── test_emitter_filter.py   ← Emitter time constant filter
│   ├── test_config_ui_options.py ← Config flow UI tests
│   ├── test_options_flow_helpers.py ← Options flow helper tests
│   ├── test_init_reload.py      ← Integration reload tests
│   ├── test_init_setup_entry.py ← Setup entry tests
│   ├── test_init_yaml_merge.py  ← YAML merge tests
│   ├── test_finishing_pass.py   ← Final validation pass
│   ├── test_performance.py      ← Benchmarks: MPC and estimation run-times
│   └── plot_model_fit.py        ← Plotting utility for model fit visualisation
├── benchmarks/                ← Performance benchmark scripts
├── BENCHMARKS.md              ← Latest performance benchmark results
├── .gitignore
└── README.md
```

### 15.2 Running the tests

Install the required packages once:

```bash
pip install numpy scipy highspy osqp homeassistant pytest voluptuous pytest-asyncio
pip install mbc @ git+https://github.com/marcuskrogh/mbc.git
```

Run the full test suite (skipping slow benchmarks):

```bash
python -m pytest tests/ -v -m "not slow"
```

Expected output: all tests pass (slow parameter-estimation benchmarks deselected with `-m "not slow"`).

Run a single test module:

```bash
python -m pytest tests/test_thermal_model.py -v
python -m pytest tests/test_controller.py -v
python -m pytest tests/test_heat_sources.py -v
```

### 15.3 Performance benchmarks

Run-time benchmarks for the active control step and parameter estimation routines are in `tests/test_performance.py`.  They cover three representative house configurations (studio, two-bedroom flat, full five-room house) and write results to `BENCHMARKS.md` in the repository root.

```bash
# Run all six benchmarks and regenerate BENCHMARKS.md
python -m pytest tests/test_performance.py -v -s
```

The three parameter-estimation benchmarks are marked `@pytest.mark.slow` because they take 14–500 seconds each.  They are excluded from the normal test run by `-m "not slow"`.

Latest results are in [`BENCHMARKS.md`](../BENCHMARKS.md).

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
