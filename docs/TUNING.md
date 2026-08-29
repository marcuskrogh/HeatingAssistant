# Parameter Estimation & Controller Tuning

How to estimate thermal parameters (thermal mass, external resistance, inter-room
resistance, window geometry), run automatic ML identification from the panel, and
tune the MPC regulator to eliminate oscillation and short-cycling.

For the theory behind these models, see [Physics, Models & Control Theory](THEORY.md).

**Contents**

- [Thermal model parameters](#thermal-model-parameters)
- [Automatic parameter estimation](#automatic-parameter-estimation)
- [MPC controller tuning](#mpc-controller-tuning)

---

## Thermal model parameters

Accurate parameters lead to accurate predictions and better control. Use the tables
below as starting points, then refine with the **Parameter estimation** page or
manual overrides in **Configuration → Rooms**.

### Thermal mass `thermal_mass`

The effective thermal mass captures how much energy must be added (or removed) to
change the room's temperature by 1 K. It includes:

- **Air mass:** $\rho_{\text{air}} \times V_{\text{room}} \times c_{p,\text{air}} \approx 1.2\;\text{kg/m}^3 \times V \times 1005\;\text{J/(kg·K)} \approx 1200 \times V\;\text{J/K}$
- **Furniture and contents:** roughly 0.5–1 × air mass for a furnished room
- **Interior wall surface layers:** the inner few centimetres of plasterboard, brick, or timber absorb/release heat on the timescale of hours
- **Floor and ceiling finishes**

**Typical values:**

| Room type | Thermal mass (J/K) |
|-----------|:-----------------:|
| Small bedroom (15 m², light furnishing) | 2 – 4 × 10⁶ |
| Medium living room (25 m², typical furnishing) | 5 – 8 × 10⁶ |
| Large open-plan kitchen/living (40 m²) | 8 – 15 × 10⁶ |
| Brick-built room with tiled floor | add 20–50 % to above |

**Quick estimate:** start with `thermal_mass ≈ 4000 × floor_area_m2` (in J/K) and
adjust based on construction type and observation.

### External thermal resistance `r_external`

The external thermal resistance describes the overall thermal barrier between the
room and the outdoors: $R = 1 / (U \times A_{\text{total}})$.

You can also measure it empirically: run the room at a steady temperature with no
solar gain (night, overcast) and observe the steady-state heater power $Q$ [W] and
the indoor–outdoor temperature difference $\Delta T$ [K]. Then
$R_{\text{ext}} \approx \Delta T / Q$ [K/W].

**Typical values:**

| Building type | r_external (K/W) per room |
|---------------|:-------------------------:|
| Modern well-insulated house (2020s build) | 0.02 – 0.04 |
| Post-1980s double-glazed house | 0.04 – 0.07 |
| Pre-1970 poorly insulated house | 0.07 – 0.15 |
| Modern flat / apartment (interior rooms) | 0.1 – 0.3 |

### Inter-room thermal resistance `r_value`

This represents the thermal conductance of the wall, floor, ceiling, or doorway
between two adjacent rooms. Higher `r_value` means less heat exchange.

| Boundary type | r_value (K/W) |
|---------------|:-------------:|
| Open doorway / archway | 0.05 – 0.15 |
| Interior door (often open) | 0.1 – 0.2 |
| Interior door (usually closed) | 0.2 – 0.5 |
| Lightweight plasterboard partition | 0.15 – 0.3 |
| Brick or concrete interior wall | 0.3 – 0.6 |
| Insulated floor/ceiling between flats | 0.5 – 1.5 |

### Window orientation and tilt

The `orientation` key is the compass bearing of the **outward-facing normal** of
the window, measured clockwise from North.

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

For a roof window pitched towards the South at 30° from horizontal, use
`orientation: 180` and `tilt: 30`.

---

## Automatic parameter estimation

Open **Parameter estimation** in the Ingress panel, select a room, and run ML
identification when you have enough heating history.
One day of data can cover every recommended category.
Several days of normal operation usually give a more reliable model.
The page also supports one-step EKF reconstruction, open-loop simulation,
and model-fit validation before you apply results.

The App service `estimate_parameters_ml` performs the same joint optimisation if
you call it programmatically; the panel is the supported workflow.

### What gets estimated

| Parameter | Physical meaning |
|-----------|------------------|
| `thermal_mass` | Energy required to heat the room by 1 K [J/K] |
| `r_external` | Thermal resistance to the outdoors [K/W] |
| `internal_gain` | Steady background heat not from the controllable source [W] |
| `power_scale` | Multiplier on the nominal heat-source rating (per source) |

Lock individual parameters on the room detail page if you want the estimator to
hold them fixed. **Parameter confidence** on each room tile summarises whether the
active values pass physical range checks.

### Persistence and reset

Successful ML runs are saved in the App configuration and reloaded on restart, so
you do not need to re-estimate after every reboot. Use **Reset to Defaults** on
the room's parameter page to discard stored estimates and revert to configured
presets.

Re-estimate when the physical setup changes (new radiator, insulation work, etc.)
or when confidence warnings persist after a week of normal heating.

### Interpreting confidence warnings

| Warning | Likely cause |
|---------|-------------|
| `thermal_mass out of range` | Value below ~50 000 J/K or above ~200 000 000 J/K — too little data or a bad run |
| `r_external out of range` | Below 0.001 K/W (implausibly good insulation) or above 1.0 K/W (implausibly leaky) |
| `time_constant out of range` | $\tau = R \times C$ outside 1–500 hours — unrealistic parameter combination |

A score below 100 % does not mean control will fail, but inspect the values and
consider more history or a re-run with problematic parameters locked.

---

## MPC controller tuning

The MPC controller solves a quadratic program at each update cycle. Adjust weights
and horizons on the panel **Tuning** page. Use **Preview** to overlay planned
temperature and power trajectories before **Apply Changes**.

Live penalty weights take effect on the next planning cycle. Changing **Sample
interval** or **Prediction horizon** rebuilds the MPC problem.

### Tunable parameters

| Parameter | Config key | Default | Effect |
|-----------|-----------|---------|--------|
| **Comfort offset** | `comfort_offset` | `2.0 °C` | Half-width of the soft comfort band around the setpoint |
| **Tracking weight** | `tracking_weight` | `0` | Setpoint tracking strength; `0` = band-only (zone) control |
| **Energy weight** | `energy_weight` | `0.01` | Penalises heater output — higher = more conservative heating |
| **Price sensitivity** | `energy_price_weight` | `1.0` | Scales electricity-price cost when a price sensor is configured |
| **Output smoothing** | `smoothing_weight` | `0.1` | Penalises changing heater output step-to-step — raise to damp oscillations |
| **P deadband (NMPC off)** | `p_deadband` | `1.0 °C` | Fast tracker stays off while the planner command is near zero and air is within this of the planned temperature |
| **NMPC-off gate** | `u_ref_gate` | `0.02` | Planner command (heater fraction) below this is treated as off; small preheat above the gate is tracked as usual |
| **Comfort band penalty (quadratic)** | `soft_constraint_weight` | `1000` | Quadratic penalty for leaving the comfort zone |
| **Comfort band penalty (linear)** | `soft_constraint_linear_weight` | `0` | Linear comfort-band penalty (`0` = disabled) |
| **Terminal weight** | `terminal_weight` | `100` | End-of-horizon tracking multiplier; raise (200–500) if the plan misses setpoint |
| **Sample interval** | `update_interval` | `900 s` | Re-planning cadence (rebuilds controller when changed) |
| **Prediction horizon** | `horizon` | `100` steps | Steps planned ahead (~25 h at 15 min); longer horizons see thermal lag |
| **EKF process noise** | `sigma_w` | `0.1` | On **Parameter estimation** — faster reaction to unmodelled disturbances when higher |
| **EKF measurement noise** | `sigma_v` | `0.5` | On **Parameter estimation** — higher trusts sensors less |

### Diagnosing oscillations

Oscillations appear as repeated undershoot/overshoot around the setpoint. Common
causes and fixes:

**Prediction horizon too short** — With only 30–60 minutes of lookahead (e.g. 2–4
steps at 15 min), the controller overshoots then cuts off, repeating the cycle.
Increase `horizon` if you have shortened it below roughly 8 steps (~2 h).

**Smoothing weight too low** — The controller can swing output between 0 and 1 each
step. Increase `smoothing_weight` (try `0.5` → `1.0` → `2.0`).

**Energy weight too high** — Minimum heating causes cool-down, then full-power bursts
and overshoot. Reduce `energy_weight` (e.g. toward `0.005`).

**Incorrect thermal parameters** — Underestimated `thermal_mass` makes the model
heat/cool too fast in simulation. Re-estimate on **Parameter estimation** or correct
manual values from the tables above.

### Step-by-step detuning procedure

1. On **Room detail**, compare measured temperature with the MPC forecast. If the
   forecast tracks the oscillation, tune controller weights; if the forecast is
   smooth but the room oscillates, fix thermal parameters first.

2. Increase `smoothing_weight` in steps (`0.5`, `1.0`, `2.0`). Allow one to two
   hours per change.

3. If needed, increase `horizon` (when it was shortened).

4. Check `energy_weight` if the heater makes abrupt on/off transitions.

5. Re-run parameter estimation if predictions do not match actual temperatures.

### Heat pump short-cycling

Heat pumps suffer from rapid on/off commands. Increase `smoothing_weight` (starting
around `0.5`) and ensure `horizon` is long enough that the planner does not need
aggressive last-minute corrections.

### Quick reference

| Symptom | Primary fix | Secondary fix |
|---------|-------------|---------------|
| MPC trajectory does not reach setpoint | ↑ `terminal_weight` (200 → 500) | ↑ `horizon` |
| Oscillating temperature | ↑ `smoothing_weight` (0.5 → 2.0) | ↑ `horizon` |
| Heater runs at 100 % then cuts off | ↓ `energy_weight` | ↑ `horizon` |
| Room never quite reaches setpoint | ↑ `terminal_weight` | ↓ `energy_weight` |
| Heat pump compressor short-cycling | ↑ `smoothing_weight` | ↑ `horizon` |
| Sluggish response | ↓ `energy_weight` or ↓ `smoothing_weight` | — |
| Slow drift despite smooth tracking | Correct `r_external` / `thermal_mass` | Re-estimate |

### Monitoring performance

The **Tuning** preview chart overlays measured temperature, MPC prediction,
setpoint, and planned power while you iterate. **Overview** and **System status**
show solve times and mean tracking error from the MPC performance sensor — typical
solve times at default settings are 0.05–0.3 s. If solves approach the sample
interval, reduce `horizon` or contact support if CPU is constrained.
