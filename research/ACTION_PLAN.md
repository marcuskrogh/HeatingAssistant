# Action Plan: Targeted Control System Improvements

Three focused improvements with strong literature support and direct, measurable benefit to the HeatingAssistant system as currently implemented. Each is self-contained and reuses existing infrastructure.

---

## Improvement 1 — Periodic Rolling Parameter Re-Estimation

### Problem

`sysid.py` triggers the CD-EKF PED estimator once after 14 days of history accumulate and is not scheduled to run again automatically. Building thermal properties are time-varying: solar gain fractions change with sun angles across seasons, infiltration rates change with wind exposure, internal heat gains shift with occupancy patterns, and insulation performance degrades gradually. A model calibrated in January is measurably inaccurate by April without re-estimation.

Paper #2 (arXiv:2508.09118) shows that MLE RC-network estimation degrades when the training window does not match current operating conditions. Paper #8 (PMC:11798724, 2025) quantifies this drift and recommends periodic batch re-estimation over a rolling window as the most practical mitigation. Paper #7 (arXiv:1512.08169) demonstrates through simulation that periodic online re-estimation directly translates to improved MPC energy savings and comfort satisfaction, not just better model fit.

### What to Change

**`coordinator.py`** — add a `rolling_estimation_interval_days` config option (default: 7). After the initial identification, the coordinator should schedule re-estimation every `rolling_estimation_interval_days` days, using the most recent `identification_window_days` of history (default: 21 days, per paper #9's finding that 12–21 days gives optimal accuracy).

**`sysid.py`** — expose a `run_if_due()` method that returns early if fewer than `rolling_estimation_interval_days` have elapsed since the last run. Currently only `run()` exists (unconditional trigger). Add a `last_run_timestamp` field persisted to HA storage.

**`parameter_estimator.py`** — no algorithmic changes required. The existing CD-EKF PED + IPOPT pipeline is correct; it only needs to be called more frequently.

**Guard against regressions:** After each re-estimation, compare the new fit's innovation RMS to the previous one. If the new fit is worse by more than 20% (configurable), retain the previous parameter set and raise `binary_sensor.heating_assistant_estimation_regression_detected`. This prevents a noisy short window from overwriting a good long-run calibration.

### Expected Benefit

- Maintains model accuracy through seasonal transitions without manual re-runs
- Directly improves MPC prediction horizon accuracy, which drives setpoint tracking and pre-heating decisions
- Zero new algorithms, zero new dependencies — reuses all existing estimation infrastructure

**Literature:** papers #2, #3, #7, #8, #9 in RESEARCH_LITERATURE.md.

---

## Improvement 2 — Heat Pump COP via Manufacturer Data Lookup

### Problem

`heat_sources.py` computes heat pump COP using a Carnot-derived formula:

```
COP(T_out) = max(1, COP_rated × COP_Carnot(T_out) / COP_Carnot(T_ref))
```

This has two well-documented failure modes. First, it **over-predicts COP at extreme cold** (paper #13): Carnot scaling assumes ideal thermodynamic efficiency, but real compressors degrade non-linearly below −5°C, and defrost cycles near 0°C consume 10–15% of compressor energy with no useful heat output. Second, it does not capture the **efficiency benefit at partial load**: modern inverter-driven units are significantly more efficient at 40–60% capacity than at full output, meaning the MPC systematically over-estimates the energy cost of light heating and may underuse the heat pump.

Paper #11 (arXiv:2402.07032, field study) directly addresses this: in a real residential deployment with an air-to-air heat pump operating to −15°C, they fit a **quadratic COP curve to manufacturer performance data** as a function of outdoor temperature and report it provides sufficient accuracy for MPC with ~20% energy savings. Paper #14 (arXiv:2409.04884) makes the same design choice and the same recommendation. Paper #12 (arXiv:2504.04182) uses piecewise-linear manufacturer data approximations and shows improved prediction versus Carnot.

### What to Change

**`heat_sources.py` — `HeatPump` class:**

Add an optional `cop_table` configuration parameter: a list of `[T_out_°C, COP]` pairs taken directly from the heat pump's datasheet (typically 5–8 points from −20°C to +15°C at nominal capacity). If provided:

```python
# In HeatPump.__init__:
if cop_table:
    self._cop_temps, self._cop_values = zip(*sorted(cop_table))

# In HeatPump._carnot_cop() (rename to _lookup_cop()):
if hasattr(self, '_cop_temps'):
    return float(np.interp(T_out, self._cop_temps, self._cop_values,
                           left=self._cop_values[0], right=0.0))
# else: existing Carnot formula as fallback
```

The `right=0.0` clamps COP to zero below the lowest table entry, preserving the existing cold-weather shut-off behaviour.

**`config_flow.py`** — add a `cop_table` field to the heat pump configuration step (optional; if omitted, Carnot fallback is used unchanged). The UI should accept either a JSON list or a comma-separated string of `T:COP` pairs.

**`sensor.py`** — add `sensor.heating_assistant_heat_pump_cop_<room>` publishing the instantaneous computed COP. This already exists conceptually in the diagnostics; it should be surfaced as a dedicated sensor so users can validate their table against measured electricity consumption.

### Expected Benefit

- More accurate energy predictions at cold outdoor temperatures, where control decisions (pre-heating vs. resistance backup) are most consequential
- Correct modelling of the defrost region (0°C ± 5°C) where Carnot over-predicts by 15–25%
- Zero change to MPC formulation; the COP enters as a scalar multiplier in `thermal_power()`, so the QP structure is unchanged
- Backward-compatible: existing deployments without a `cop_table` behave identically to today

**Literature:** papers #11, #12, #13, #14 in RESEARCH_LITERATURE.md.

---

## Improvement 3 — Adaptive Weather Forecast Bias Correction

### Problem

The outdoor temperature forecast from the configured `weather_entity` is passed directly to the MPC as the disturbance sequence `d[k]` over the horizon. NWP models (including Met.no, OpenWeatherMap, and most Home Assistant weather integrations) have well-documented systematic biases that vary by time of day, season, and local geography — consistently running 1–2°C warm or cold in particular conditions.

This matters for the MPC because: (a) a persistent warm bias causes the controller to under-heat (predicting the outdoor temperature will be warmer than it is, so less pre-heating is scheduled); (b) a persistent cold bias over-heats, wasting energy. The bias is *correlated over multiple consecutive forecast steps*, so it compounds across the planning horizon rather than averaging out.

Paper #15 (arXiv:2412.09238, 2024) shows that a simple adaptive disturbance correction applied to MPC forecasts reduces comfort violations and energy waste compared to using raw NWP output. The correction is a rolling estimate of recent forecast error — computationally trivial and directly implementable in HeatingAssistant's existing `history_window` infrastructure. Papers #17 and #18 confirm that NWP temperature biases are systematic and learnable with simple post-processing.

### What to Change

**`history_window.py`** — the ring buffer already stores measured outdoor temperatures. Extend it to also store the corresponding 1-step-ahead forecast value that was active at each timestamp (i.e., what the weather entity predicted for this hour at the previous cycle). This requires storing one additional value per timestep.

**`weather.py`** / **`coordinator.py`** — implement an `OutdoorForecastBiasEstimator`:

```python
class OutdoorForecastBiasEstimator:
    """Exponential moving average of (forecast - actual) at each lead step."""
    def __init__(self, n_steps, alpha=0.05):
        self.bias = np.zeros(n_steps)   # bias[k] = E[forecast_k - actual_k]
        self.alpha = alpha              # EMA decay (~72 h half-life at alpha=0.05, 15-min steps)

    def update(self, forecast_1step_ago: float, actual: float):
        error = forecast_1step_ago - actual
        self.bias[0] = (1 - self.alpha) * self.bias[0] + self.alpha * error
        # Shift correction: assume bias decays toward zero over horizon
        for k in range(1, len(self.bias)):
            self.bias[k] = self.bias[0] * np.exp(-k / len(self.bias))

    def correct(self, raw_forecast: np.ndarray) -> np.ndarray:
        return raw_forecast - self.bias[:len(raw_forecast)]
```

Call `estimator.update()` at each control cycle with the previous step's 1-step forecast and the current measured outdoor temperature. Apply `estimator.correct()` to the horizon forecast before passing to the QP.

The `alpha=0.05` default corresponds to a ~72-hour adaptation time constant at 15-minute control cycles. This is fast enough to track intra-day forecast bias patterns while being slow enough not to chase measurement noise.

**`sensor.py`** — publish `sensor.heating_assistant_forecast_bias_K` (the current bias estimate at step 1, in Kelvin) as a diagnostic. A persistent non-zero value indicates a systematic weather entity error that the correction is actively compensating.

### Expected Benefit

- Reduces systematic MPC horizon errors driven by NWP bias, most impactful in the first 3–6 hours of the horizon where control decisions are made
- Directly improves comfort satisfaction during cold snaps (where warm-biased NWP forecasts cause under-heating)
- Lightweight: one float updated per cycle, no matrix operations, no external dependencies
- The bias sensor gives users actionable feedback: a persistent +2 K bias means their weather entity consistently over-predicts outdoor temperature

**Literature:** papers #15, #16, #17, #18 in RESEARCH_LITERATURE.md.
