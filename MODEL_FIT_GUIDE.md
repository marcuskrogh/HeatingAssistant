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

## Diagnostic Services

Three new services provide detailed analysis reports via persistent notifications:

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

Add the diagnostic sensors to your Lovelace dashboard:

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

5. **Check controller configuration:**
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
