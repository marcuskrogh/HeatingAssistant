# Action Plan: Control System Parameter & Configuration Improvements

Derived from [`research/LITERATURE.md`](../research/LITERATURE.md). Each action is self-contained, reuses existing infrastructure where possible, and includes literature citations, concrete file targets, acceptance criteria, and suggested priority.

---

## Action 1 — Periodic Rolling Parameter Re-Estimation

**Priority:** High  
**Labels:** Parameter Estimation, MPC, Improvement  
**Literature:** LITERATURE.md §2 (#2, #3, #13–16)

### Problem

`sysid.py` triggers CD-EKF PED once after ~14 days of history and is not scheduled again. Building thermal properties drift seasonally (solar angles, infiltration, occupancy, internal gains). Paper #2 shows MLE RC estimation degrades when the training window no longer matches current conditions.

### Changes

| File | Change |
|------|--------|
| `coordinator.py` | Add `rolling_estimation_interval_days` (default **7**). After initial ID, call re-estimation on schedule using `identification_window_days` (default **21**) of recent history. |
| `sysid.py` | Add `last_run_timestamp` (HA storage). Implement `run_if_due()` that skips if interval not elapsed. |
| `parameter_estimator.py` | No algorithm change — call more frequently. |
| `binary_sensor.py` / `sensor.py` | Raise `binary_sensor.heating_assistant_estimation_regression_detected` when new innovation RMS is >20% worse than previous (configurable). |

### Acceptance criteria

- [ ] Re-estimation runs automatically every 7 days after initial success
- [ ] Regression guard rejects worse fits and preserves previous parameters
- [ ] Options exposed in config flow / tuning page
- [ ] Unit tests for `run_if_due()` timing and regression guard

### Expected benefit

Maintains model accuracy through seasonal transitions; improves MPC horizon predictions for pre-heat and comfort decisions.

---

## Action 2 — Heat Pump COP via Manufacturer Data Lookup

**Priority:** High  
**Labels:** Heat Pump, MPC, Improvement  
**Literature:** LITERATURE.md §3 (#20–23)

### Problem

`heat_sources.py` uses Carnot-scaled COP which over-predicts at extreme cold and ignores partial-load efficiency. Field studies (#20, #23) fit quadratic or piecewise-linear curves from manufacturer datasheets.

### Changes

| File | Change |
|------|--------|
| `heat_sources.py` | Add optional `cop_table: list[[T_°C, COP]]`. Interpolate with `np.interp`; `right=0.0` below table minimum. Fall back to Carnot when absent. |
| `const.py` | Add `CONF_SOURCE_COP_TABLE`. |
| `config_flow.py` | Optional `cop_table` field (JSON list or `T:COP` comma string). Validate ≥2 points, monotonic temps, positive COP. |
| `sensor.py` | `sensor.heating_assistant_heat_pump_cop_<room>` diagnostic. |

### Acceptance criteria

- [ ] Backward compatible: no `cop_table` → identical behaviour to today
- [ ] COP at −15 °C from table is measurably lower than Carnot for typical units
- [ ] Config flow validation rejects malformed tables
- [ ] Unit tests for interpolation, clamping, fallback

### Expected benefit

Accurate energy cost in MPC at cold outdoor temperatures where pre-heat vs backup decisions matter most.

---

## Action 3 — Adaptive Weather Forecast Bias Correction

**Priority:** Medium  
**Labels:** Weather Forecast, MPC, Improvement  
**Literature:** LITERATURE.md §4 (#25–28)

### Problem

Weather entity outdoor temperature is passed directly to the MPC disturbance sequence. NWP models have systematic 1–2 °C biases that compound over the horizon. Paper #25 shows simple rolling bias correction reduces comfort violations.

### Changes

| File | Change |
|------|--------|
| `history_window.py` | Store 1-step-ahead forecast alongside measured outdoor temp per timestep. |
| `weather.py` (new or extend) | `OutdoorForecastBiasEstimator`: EMA of `(forecast − actual)` with `alpha=0.05` (~72 h half-life at 15 min). Horizon bias decays: `bias[k] = bias[0] · exp(−k/N)`. |
| `coordinator.py` | Update estimator each cycle; apply `correct()` to horizon before QP. |
| `sensor.py` | `sensor.heating_assistant_forecast_bias_K` diagnostic. |

### Acceptance criteria

- [ ] Bias estimate converges on synthetic constant +2 °C forecast error within ~3 days
- [ ] Corrected forecast used in MPC solve
- [ ] Diagnostic sensor shows current 1-step bias
- [ ] No change when weather entity unavailable (graceful disable)

### Expected benefit

Reduces systematic under-heating from warm-biased forecasts and over-heating from cold-biased forecasts.

---

## Action 4 — Horizon Auto-Sizing & Documentation

**Priority:** Medium  
**Labels:** MPC, Improvement  
**Literature:** LITERATURE.md §5 (#32–37)

### Problem

`DEFAULT_HORIZON=100` at 900 s ≈ 25 h is reasonable but undocumented. Literature consensus is **18–24 h** (one diurnal cycle). Horizon is not linked to available weather or price forecast length — MPC may plan beyond reliable forecast data or stop short of price schedule.

### Changes

| File | Change |
|------|--------|
| `const.py` | Add `DEFAULT_HORIZON_HOURS = 24.0` as semantic default; derive step count from `update_interval`. Keep `DEFAULT_HORIZON=96` (24 h at 15 min) or round from hours. |
| `coordinator.py` | Add `horizon_mode: "fixed" | "auto"`. In auto mode: `horizon = min(available_weather_steps, available_price_steps, max_horizon_steps)`. |
| `docs/CONFIGURATION.md`, `docs/TUNING.md` | Document: "Set horizon to cover ≥1 full day and ≥3× slowest room time constant." |
| Tuning UI (`tuning-controller.js`) | Show horizon in **hours** alongside steps. Preset buttons: 12 h / 18 h / 24 h / 36 h. |

### Recommended defaults (from literature)

| Setting | Current | Proposed | Rationale |
|---------|---------|----------|-----------|
| Horizon | 100 steps (25 h) | **96 steps (24 h)** or auto | Matches #10, #32, #33 (24 h standard) |
| Min horizon | — | 48 steps (12 h) | Below this, pre-heat window too short for high-mass rooms |
| Max horizon | 200 steps | 144 steps (36 h) | Beyond 36 h, forecast error dominates (#35) |

### Acceptance criteria

- [ ] Auto mode caps horizon to shortest available forecast
- [ ] Tuning page shows hours and steps
- [ ] Documentation explains diurnal-cycle rationale
- [ ] No regression for existing fixed-horizon installs

### Expected benefit

Better out-of-box pre-heat behaviour; avoids planning on stale/extrapolated forecast tail.

---

## Action 5 — Comfort Corridor Guidance & Schedule-Aware Presets

**Priority:** Medium  
**Labels:** MPC, Improvement  
**Literature:** LITERATURE.md §6 (#38–44)

### Problem

`DEFAULT_COMFORT_OFFSET=2.0` °C gives a ±2 °C (4 °C total) band. Literature occupied-mode bounds are tighter (±0.5–1.5 °C, papers #38–39). Eco/setback periods use wider bands (±1.5–4 °C). Users lack guidance; schedule periods can override setpoint but comfort-offset presets are not documented.

### Changes

| File | Change |
|------|--------|
| `const.py` | Add `COMFORT_OFFSET_COMFORT = 1.0`, `COMFORT_OFFSET_ECO = 2.0`, `COMFORT_OFFSET_SETBACK = 3.0` as named presets. |
| Schedule schema | Document `schedule_comfort_offset` per period; suggest 1.0 °C for `mode=comfort`, 3.0 °C for `mode=off` (frost protection). |
| `docs/TUNING.md` | New section "Comfort corridor sizing": table mapping offset → expected violation rate vs energy. |
| Tuning / schedule UI | Preset selector: Tight (±1 °C) / Standard (±2 °C) / Relaxed (±3 °C) with literature tooltip. |
| `controller.py` | Optional: asymmetric corridor via separate `comfort_offset_low` / `comfort_offset_high` (future; document as stretch goal). |

### Recommended defaults

| Context | `comfort_offset` | Total band | Source |
|---------|-----------------|------------|--------|
| Occupied / comfort schedule | **1.0 °C** | 2 °C | Papers #38, #39 occupied |
| Default (no schedule) | **2.0 °C** | 4 °C | Current — keep as default |
| Eco / away / night setback | **2.5–3.0 °C** | 5–6 °C | Paper #39 unoccupied |
| Experiment excitation | 1000 °C | — | Already implemented |

### Acceptance criteria

- [ ] TUNING.md documents corridor sizing with literature references
- [ ] Schedule editor offers comfort-offset presets per period
- [ ] Default remains 2.0 °C (no breaking change)
- [ ] Dashboard shows effective corridor on room plots

### Expected benefit

Tighter comfort during occupied hours without global retuning; wider bands during setback save energy.

---

## Action 6 — MPC Weight Tuning Guidance & Scaled Defaults

**Priority:** Lower  
**Labels:** MPC, Improvement  
**Literature:** LITERATURE.md §7 (#45–48)

### Problem

Weights (`energy_weight`, `smoothing_weight`, `soft_constraint_weight`, `terminal_weight`, `tracking_weight`) require expert tuning. Literature (#45, #46) recommends scaling by expected operating ranges before selecting relative weights. Current defaults work but are not justified in UI.

### Changes

| File | Change |
|------|--------|
| `docs/TUNING.md` | Add "MPC weight scaling" section: normalize Q by (1 °C)², R by (max_power)², show equivalent violation cost. |
| Tuning UI | "Guided tuning" panel: sliders with live preview of predicted corridor violation cost vs energy cost at current outdoor temp. |
| `const.py` | Consider raising `DEFAULT_SMOOTHING_WEIGHT` to **0.2** for heat-pump-primary installs (Paper #41: smoothing essential for HP). Gate behind heat-source-type detection in coordinator. |
| `controller.py` | Log effective ρ/Q ratio in diagnostics for post-hoc tuning. |

### Weight relationship guide (for documentation)

```
Effective comfort violation cost per °C² ≈ soft_constraint_weight
Effective energy cost per W² ≈ energy_weight × (1/COP)²
Terminal pull ≈ terminal_weight × tracking_weight (tracking_weight=0 → terminal inactive on tracking)
```

When `tracking_weight=0`, terminal cost applies only through soft-bound terminal slack — document this interaction.

### Acceptance criteria

- [ ] TUNING.md explains each weight with literature-backed ranges
- [ ] Tuning page shows restart-required vs live weights (extends CON-11)
- [ ] Diagnostics expose effective weight ratios

### Expected benefit

Faster successful tuning for new users; fewer support cases from infeasible QP (ρ too low) or sluggish response (S too high).

---

## Action 7 — EKF Noise Auto-Calibration

**Priority:** Lower  
**Labels:** Parameter Estimation, MPC, Improvement  
**Literature:** LITERATURE.md §8 (#49–51)

### Problem

`sigma_w=0.1`, `sigma_v=0.5`, `sigma_b=0.002` are fixed. Innovation sequence should inform σ_v; model confidence after estimation should inform σ_w.

### Changes

| File | Change |
|------|--------|
| `controller.py` | Track innovation RMS in rolling buffer; suggest σ_v ≈ 1.1 × innovation_std. |
| `coordinator.py` | After successful parameter estimation, optionally scale σ_w down (more trust in model) or up (poor fit). |
| `docs/TUNING.md` | Document σ_w/σ_v/σ_b semantics with Paper #19 (15 min sampling) validation. |
| Tuning UI | "Auto-tune EKF noise" button: set σ_v from last 24 h innovations. |

### Acceptance criteria

- [ ] σ_v suggestion within 20% of measured innovation std on synthetic test
- [ ] Auto-tune is opt-in; manual values preserved by default
- [ ] Unit test for innovation statistics computation

### Expected benefit

Better EKF tracking after sensor changes or model updates without manual noise retuning.

---

## Implementation Roadmap

```
Phase 1 (High impact, low risk)
├── Action 1: Rolling re-estimation
├── Action 2: COP manufacturer table
└── Action 3: Weather bias correction

Phase 2 (Configuration & UX)
├── Action 4: Horizon auto-sizing
└── Action 5: Comfort corridor presets

Phase 3 (Tuning & diagnostics)
├── Action 6: MPC weight guidance
└── Action 7: EKF noise auto-calibration
```

## Linear Issue Mapping

| Action | Parent issue | Sub-issues (implementation flow) |
|--------|-------------|----------------------------------|
| 1 | Periodic rolling parameter re-estimation | Config options → `run_if_due()` → coordinator schedule → regression guard |
| 2 | Heat pump COP via manufacturer data | `cop_table` in heat_sources → config flow → COP diagnostic sensor |
| 3 | Adaptive weather forecast bias correction | history_window storage → bias estimator → MPC integration → bias sensor |
| 4 | Horizon auto-sizing & documentation | const defaults → auto mode in coordinator → UI hours display → docs |
| 5 | Comfort corridor guidance & presets | const presets → schedule UI → TUNING.md → dashboard corridor display |
| 6 | MPC weight tuning guidance | TUNING.md → diagnostics → guided tuning UI |
| 7 | EKF noise auto-calibration | innovation tracking → auto-tune service → UI button |

## Cross-References

- Prior research files: `research/RESEARCH_LITERATURE.md`, `research/ACTION_PLAN.md` (superseded by this document for planning)
- Configuration reference: `docs/CONFIGURATION.md`
- Tuning guide: `docs/TUNING.md`
