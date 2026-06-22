# Action Plan: Feature Improvements for HeatingAssistant Temperature Control

Based on the research literature in RESEARCH_LITERATURE.md and the current HeatingAssistant implementation (2R2C thermal model, CD-EKF state estimator, linearised MPC with QP, CD-EKF PED parameter estimation).

Priorities: **P1** = high impact / near-term, **P2** = medium impact / medium-term, **P3** = longer-term / research-grade.

---

## Initiative A — Stochastic & Uncertainty-Aware MPC

**Motivation:** HeatingAssistant's current MPC formulation uses deterministic disturbance forecasts (outdoor temperature, solar) and fixed process-noise parameters (`sigma_w`, `sigma_b`). Forecast errors accumulate over the horizon, yet constraints are treated as hard. Papers #22–#26 in RESEARCH_LITERATURE.md show that propagating forecast uncertainty into the QP as chance constraints reduces comfort violations significantly without the brittleness of hard bounds.

### A1 — Forecast Uncertainty Propagation (P1)

**What:** Extend the disturbance forecast pipeline to produce confidence intervals (mean + variance) for outdoor temperature and solar gains at each horizon step, and propagate these variances into the QP's soft-constraint tightening.

**How:**
- Replace persistence fallback with an adaptive Gaussian noise model: estimate forecast-error variance from recent forecast vs. actual deltas stored in `history_window`.
- Pass `d_mean[k]` and `d_var[k]` to `CDLinearizedMPCController`; tighten comfort corridor by `α·sqrt(d_var[k])` at each step (one-sided Gaussian chance constraint with configurable confidence level, e.g. 95%).
- Expose `forecast_confidence_level` (default 0.95) as a tunable parameter in the dashboard.

**Files:** `controller.py` (QP formulation), `coordinator.py` (forecast build), `history_window.py` (store forecast deltas).

**Validation:** Compare comfort-violation rates and energy use before/after on logged history. See papers #22, #25.

---

### A2 — Weather-Forecast Error Correction (P2)

**What:** Learn a systematic bias correction for the configured weather entity's outdoor temperature and solar irradiance forecasts using a rolling online least-squares correction.

**How:**
- In `weather.py` / `coordinator.py`, store the last N (e.g. 72) hour-ahead forecast vs. actual pairs.
- Fit a simple affine correction `T_corrected = a·T_forecast + b` updated each cycle.
- Apply correction before passing disturbances to the controller; publish corrected forecast vs. raw forecast as diagnostic sensors.

**Files:** `weather.py`, `coordinator.py`, new `forecast_correction.py`.

**Validation:** RMSE of corrected vs. raw forecast on rolling holdout.

---

## Initiative B — Improved Solar Gain Forecasting

**Motivation:** HeatingAssistant has a detailed analytical clear-sky model but relies on external sensors or Open-Meteo for measured irradiance. Short-term cloud-cover prediction quality directly affects MPC horizon accuracy. Papers #41–#43 show that lightweight ML nowcasting models substantially outperform persistence.

### B1 — Local Solar Nowcasting via Cloud-Cover Trend (P2)

**What:** Supplement the clear-sky model with a simple extrapolative cloud-cover trend model that uses the last 3–6 hours of cloud-cover history to predict the next 2–4 hours, rather than holding cloud cover constant.

**How:**
- Extend `solar_model.py` to accept an `cloud_forecast` time series (not just a scalar).
- In `coordinator.py`, maintain a rolling cloud-cover history in `history_window` and fit a linear/autoregressive trend to produce a short-horizon cloud forecast.
- Blend with weather entity's cloud forecast (if available) after horizon step 4.

**Files:** `solar_model.py`, `coordinator.py`, `history_window.py`.

**Validation:** Compare predicted vs. actual solar gains over 1-hour and 4-hour horizons.

---

### B2 — Solar Forecast Quality Sensor & Fallback Logic (P1)

**What:** Publish a `sensor.heating_assistant_solar_forecast_quality` entity that reports the recent mean-absolute-error of solar gain predictions vs. actuals, and automatically adjusts the process-noise inflation on the solar gain term when quality degrades.

**Files:** `sensor.py`, `coordinator.py`, `solar_model.py`.

---

## Initiative C — Enhanced Heat Pump Model

**Motivation:** HeatingAssistant's COP curve uses a Carnot-derived model (COP depends only on outdoor temperature). Real variable-speed heat pumps exhibit COP dependence on compressor speed, supply temperature, part-load ratio, and defrost cycles. Paper #38 (field study) and #39 show that even a simple quadratic fit to manufacturer data significantly improves energy prediction accuracy.

### C1 — Manufacturer Data COP Curve Import (P1)

**What:** Allow users to supply a manufacturer COP table (outdoor temp vs. COP at nominal + min + max compressor speed) and fit a piecewise-linear or quadratic interpolant, replacing the Carnot approximation.

**How:**
- Add `cop_table` configuration option to `HeatPump` in `heat_sources.py` (list of `[T_out, COP]` pairs from datasheet).
- If provided, replace `_carnot_cop()` with a `numpy.interp` / `scipy.interpolate.interp1d` lookup.
- Keep Carnot as fallback when no table provided.
- Publish `sensor.heating_assistant_heat_pump_cop_<room>` as a diagnostic sensor.

**Files:** `heat_sources.py`, `sensor.py`.

**Validation:** Energy prediction vs. actual electricity consumption on history data.

---

### C2 — Part-Load Ratio and Defrost Efficiency Penalty (P2)

**What:** Add a part-load ratio correction to the COP model. At low fractional setpoints (u << 1), a variable-speed heat pump's efficiency is higher; at very low u the pump may cycle, reducing effective COP. Also add a defrost efficiency penalty at outdoor temperatures near 0°C (−3°C to +5°C).

**How:**
- Add `plr_correction_factor(u)` method to `HeatPump`; default to identity (no change); optionally configurable as quadratic polynomial coefficients.
- Add `defrost_penalty(T_out)` returning a reduction fraction (e.g. 0.85 at 0°C, 1.0 elsewhere); apply to thermal output in `thermal_power()`.
- Expose polynomial coefficients and defrost penalty temperature range as configuration options.

**Files:** `heat_sources.py`.

---

## Initiative D — Online / Continuous Parameter Estimation

**Motivation:** HeatingAssistant's parameter estimator currently runs as a batch job after 14 days of history. If the building changes (new insulation, furniture rearrangement, radiator replacement), the model can remain miscalibrated for weeks. Papers #11, #14, #16 show that online or rolling estimation with Kalman augmentation detects parameter drift much faster.

### D1 — Rolling Parameter Re-Estimation with Shorter Windows (P1)

**What:** Add a `rolling_estimation_window_days` option (default: 14, minimum: 3) and an `estimation_interval_days` option (default: 7) so that parameter estimation runs weekly on the most recent 14 days rather than only once.

**How:**
- In `sysid.py`, expose a trigger mode: periodic (interval-based) vs. on-demand.
- In `coordinator.py`, schedule rolling sysid based on `estimation_interval_days`.
- Store previous parameter set; if new fit degrades RMS by >20% vs. last, fall back to previous and raise a warning sensor.

**Files:** `sysid.py`, `coordinator.py`, `sensor.py`.

---

### D2 — Parameter Drift Detection Sensor (P2)

**What:** Publish `sensor.heating_assistant_model_drift_<room>` comparing the rolling one-day-ahead prediction error vs. the long-run average. A rising trend indicates model drift and should prompt re-estimation.

**How:**
- In `model_diagnostics.py`, compute rolling 24h RMSE and compare to 30-day median; publish ratio.
- Add a `binary_sensor.heating_assistant_model_drift_detected` that fires when ratio > 1.5.

**Files:** `model_diagnostics.py`, `sensor.py`.

---

### D3 — Augmented Kalman Filter for Slow Parameter Tracking (P3)

**What:** Add an optional augmented EKF mode where slowly drifting parameters (R_external, C, solar_scale) are appended to the state vector with random-walk dynamics and tracked continuously between batch estimation runs.

**How:**
- In `controller.py` / `HouseThermalSDE`, optionally augment state with [R_ext, C, s_solar] per room as slow random-walk states.
- Process noise on augmented states set very low (e.g. σ_param = 1e-6/√s).
- Batch ML estimation used to reset prior at each periodic run.

**Files:** `controller.py`, `thermal_model.py`.

**Research basis:** Papers #15, #16; standard augmented-EKF approach.

---

## Initiative E — Demand Response & Grid Flexibility API

**Motivation:** HeatingAssistant's thermal mass can act as a virtual battery for the grid. Papers #33–#37 quantify the flexibility available from building thermal inertia. Currently HeatingAssistant has a `price_entity` for cost-aware pre-heating but no way to report its available flexibility to an aggregator or Home Assistant's energy management.

### E1 — Flexibility Envelope Sensor (P2)

**What:** Compute and publish the building's thermal flexibility envelope at each control cycle: how much heating can be shifted forward or deferred (in kWh) over the next 2 hours while keeping all rooms within ±1°C of setpoint.

**How:**
- After MPC solves the QP, solve two additional QPs: one minimising energy over the next 8 steps (maximum deferral) and one maximising energy (maximum pre-heat), both with comfort constraints.
- Publish as `sensor.heating_assistant_flexibility_kWh_up` and `..._down`.

**Files:** `controller.py`, `sensor.py`.

**Research basis:** Papers #34, #35.

---

### E2 — Demand Response Event Handling (P2)

**What:** Add a Home Assistant service `heating_assistant.demand_response_event` that accepts a duration (minutes) and a power-reduction (kW) target and temporarily relaxes the comfort setpoint within a configurable tolerance band to shed load.

**How:**
- In `__init__.py`, register the service.
- In `coordinator.py`, temporarily increase `energy_weight` and widen the comfort corridor in the MPC objective for the event duration.
- Restore original parameters after the event with a soft recovery ramp.

**Files:** `__init__.py`, `coordinator.py`, `controller.py`.

---

## Initiative F — Dynamic Occupancy & Adaptive Schedules

**Motivation:** HeatingAssistant's comfort schedules are fully static (time-of-day / day-of-week). Papers #17, #44, #45 show that even simple occupancy signals (motion sensors, CO2) dramatically improve energy savings by avoiding heating empty rooms.

### F1 — Motion / Presence Sensor Integration (P1)

**What:** Allow per-room `presence_sensors` (binary sensors: motion detectors, Bluetooth trackers, Nest/HA presence) to dynamically override the scheduled mode: if absence detected for longer than `absence_timeout_minutes`, switch to eco/setback mode; reactivate to comfort mode on presence.

**How:**
- Add `presence_sensors` list config per room (analogous to `temp_sensors`).
- Add `absence_timeout_minutes` (default: 30) and `pre_heat_on_return_minutes` (default: 20) parameters.
- In `coordinator.py`, maintain per-room presence state machine and feed into setpoint selection logic in `schedule.py`.

**Files:** `coordinator.py`, `schedule.py`, `config_flow.py`.

---

### F2 — Adaptive Schedule Learning from Historical Patterns (P3)

**What:** Analyse occupancy/temperature history to automatically suggest or update room comfort schedule periods (clustering of observed "heating demand" windows).

**How:**
- Weekly batch job in `sysid.py` that clusters heater duty-cycle history by time-of-day/week using k-means.
- Propose updated schedule in the dashboard; user confirms before applying.

**Files:** `sysid.py`, `schedule.py`, `dashboard.py`.

---

## Initiative G — Thermal Comfort Index (PMV/PPD) Setpoints

**Motivation:** HeatingAssistant targets a temperature setpoint but not a comfort index. Papers #44, #45, #47 show that PMV-based control can achieve the same or better comfort with less energy by accounting for clothing, humidity, and activity level.

### G1 — PMV-Derived Effective Setpoint (P2)

**What:** Add an optional `comfort_model: pmv` mode per room that converts the user-specified comfort level (e.g. "comfortable") to an equivalent PMV=0 air temperature, accounting for configurable `mean_radiant_temperature_offset`, `humidity_percent`, `metabolic_rate`, and `clothing_level`.

**How:**
- Implement Fanger PMV equation in a new `comfort_model.py` module.
- At each cycle, compute `T_air_target` from `PMV_target=0` using Newton iteration (MRT estimated from wall temperature via EKF output).
- Use computed `T_air_target` as the room setpoint fed into the MPC objective.

**Files:** new `comfort_model.py`, `coordinator.py`, `schedule.py`, `config_flow.py`.

**Research basis:** Papers #44, #45; ISO 7730.

---

## Initiative H — Model Order Selection & Higher-Order Room Models

**Motivation:** The 2R2C model is validated as near-optimal for typical residential buildings (paper #2), but rooms with heavy underfloor-heated slabs or phase-change-material walls may need a 3R3C or 4R3C model. Paper #9 provides a hybrid approach for adding wall nodes.

### H1 — Optional 3-Node (3R3C) Room Model for Heavyweight Construction (P3)

**What:** Add a third node (`T_slab`) to represent high-mass underfloor-heated slabs or heavy concrete walls, with corresponding parameters `C_slab` and `R_slab_wall`.

**How:**
- In `thermal_model.py`, allow `room_model_order: 2` (default, current 2R2C) or `3` (adds slab node).
- The 3-node model is constructed by extending the state-space matrices; heat input from hydronic underfloor heating routes to the slab node rather than the air node.
- Parameter estimator extended to include `C_slab`, `R_slab_wall`.

**Files:** `thermal_model.py`, `parameter_estimator.py`, `heat_sources.py`.

**Research basis:** Papers #2, #9.

---

## Initiative I — Improved Cold-Start / New Building Setup

**Motivation:** New users currently wait 14 days for the first parameter estimation. Papers #48 (building archetype forecasting) and the 960-building dataset (#53) suggest that reasonable priors can be derived from building type and construction year.

### I1 — Archetype-Based Parameter Priors for Cold Start (P2)

**What:** During initial setup (`config_flow.py`), ask the user for `construction_year`, `building_type` (detached house / flat / semi-detached), and `wall_type` (cavity wall / solid wall / insulated). Map these to informed priors for `C`, `R_external`, and `Q_int` (a lookup table derived from building-physics literature).

**How:**
- Add a `building_archetype` step to the config flow.
- In `parameter_estimator.py`, replace the current uniform priors with archetype-derived Gaussian priors (mean + std).
- Allow estimation to run on as little as 3 days of data when using archetype priors.

**Files:** `config_flow.py`, `parameter_estimator.py`, new `archetypes.py`.

**Research basis:** Papers #48, #53.

---

## Initiative J — Dashboard & Diagnostics Improvements

### J1 — Flexibility & Demand Response Visualisation (P2)

**What:** Add a "Grid Flexibility" panel to the dashboard showing: current flexibility envelope (kWh up/down), recent price-aware pre-heating actions, and cumulative cost savings vs. a naive thermostat baseline.

**Files:** `dashboard.py`, `www/` (TypeScript/React frontend).

---

### J2 — Parameter Uncertainty & Identifiability Visualisation (P2)

**What:** The parameter estimator already computes innovation covariances; surface per-parameter confidence intervals (e.g. 90% CI on C, R_ext) and identifiability scores in the dashboard's "Model Fit" page.

**Files:** `parameter_estimator.py`, `model_diagnostics.py`, `dashboard.py`.

---

## Prioritisation Summary

| ID | Title | Priority | Initiative |
|----|-------|----------|------------|
| A1 | Forecast uncertainty propagation to QP chance constraints | P1 | A |
| B2 | Solar forecast quality sensor & adaptive noise inflation | P1 | B |
| C1 | Manufacturer COP table import for heat pumps | P1 | C |
| D1 | Rolling parameter re-estimation (weekly) | P1 | D |
| F1 | Motion/presence sensor integration for adaptive setback | P1 | F |
| A2 | Weather forecast bias correction | P2 | A |
| B1 | Local solar nowcasting via cloud-cover trend | P2 | B |
| C2 | Part-load ratio and defrost penalty in COP model | P2 | C |
| D2 | Parameter drift detection sensor | P2 | D |
| E1 | Flexibility envelope sensor (kWh up/down) | P2 | E |
| E2 | Demand response event service | P2 | E |
| G1 | PMV-derived effective setpoint | P2 | G |
| I1 | Archetype-based parameter priors for cold start | P2 | I |
| J1 | Grid flexibility dashboard panel | P2 | J |
| J2 | Parameter uncertainty / identifiability visualisation | P2 | J |
| D3 | Augmented EKF for slow parameter tracking | P3 | D |
| F2 | Adaptive schedule learning from patterns | P3 | F |
| H1 | Optional 3-node room model (3R3C) | P3 | H |
