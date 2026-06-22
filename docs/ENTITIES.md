# Entities & Sensor Reference

> Every entity Heating Assistant creates: the per-room climate thermostat, the
> measured/filtered/forecast sensors, the advanced visualisation sensors, plus
> how the integration maps controller output onto your heater entities.

For the services these entities expose, see the [Services
reference](SERVICES.md); for building dashboards from them, see
[Dashboards & Lovelace Cards](DASHBOARDS.md). For installation and a quick
overview, see the [main README](../README.md).

**Contents**

- [5. Home Assistant Integration](#5-home-assistant-integration) — platforms, heater dispatch, update cadence
- [12. Entity Reference](#12-entity-reference) — per-entity attributes
- [13.1–13.9 Advanced visualisation sensors](#131-visualisation-sensors-overview)

---

## 5. Home Assistant Integration

### 5.1 Platforms and entities

Heating Assistant registers three HA platforms: **climate**, **sensor**, and **button**.

For each room declared in `configuration.yaml` the integration creates:

| Entity ID | Platform | State | Attributes |
|-----------|----------|-------|------------|
| `climate.heating_assistant_<room_name>` | climate | HVAC mode (`heat_cool` / `heat` / `off`) | current_temperature, target_temperature, hvac_action |
| `sensor.heating_assistant_<room_name>_temperature_measured` | sensor | Averaged room temperature measurement in °C | – |
| `sensor.heating_assistant_<room_name>_temperature_filtered` | sensor | Kalman-filtered state estimate x̂⁺ in °C | thermal_mass, r_external |
| `sensor.heating_assistant_<room_name>_setpoint` | sensor | Active setpoint in °C | forecast (timestamped, per-step setpoint over the MPC horizon) |
| `sensor.heating_assistant_<room_name>_window_state` | sensor | Open-window state machine state (`closed` / `pending_open` / `open` / `pending_closed`) | – |
| `sensor.heating_assistant_<room_name>_constraint_upper` | sensor | Soft-constraint upper bound (setpoint + offset) in °C | forecast (timestamped, per-step constraint_upper over the MPC horizon) |
| `sensor.heating_assistant_<room_name>_constraint_lower` | sensor | Soft-constraint lower bound (setpoint − offset) in °C | forecast (timestamped, per-step constraint_lower over the MPC horizon) |
| `sensor.heating_assistant_<room_name>_heating_power_measured` | sensor | Total active heating power in W | Per-source breakdown by source name |
| `sensor.heating_assistant_<room_name>_solar_gain_measured` | sensor | Current solar heat gain in W | window_count, total_window_area |
| `sensor.heating_assistant_<room_name>_temperature_forecast` | sensor | End-of-horizon MPC predicted temperature in °C | trajectory, forecast (timestamped), setpoint, horizon_steps |
| `sensor.heating_assistant_<room_name>_heating_power_forecast` | sensor | Current planned heating power in W | forecast (timestamped), horizon_steps |
| `sensor.heating_assistant_<room_name>_solar_gain_forecast` | sensor | Current predicted solar gain in W | forecast (timestamped), horizon_steps, window_count |
| `sensor.heating_assistant_<room_name>_heat_loss` | sensor | Total heat loss in W | external_loss, per-room flows, outdoor_temp |
| `sensor.heating_assistant_<room_name>_energy_balance` | sensor | Net energy flow in W | heating_power, solar_gain, losses breakdown |
| `sensor.heating_assistant_outdoor_temperature_measured` | sensor | Current outdoor temperature in °C | – |
| `sensor.heating_assistant_outdoor_temperature_forecast` | sensor | Outdoor temperature forecast over the MPC horizon in °C | forecast (timestamped), horizon_steps |

> **Sensor naming convention.**  All per-room and global sensors use the
> suffix pattern `_measured` (raw scalar at "now"), `_filtered` (Kalman
> state estimator output), or `_forecast` (future trajectory; state is the
> end-of-horizon value, the full series is on the `forecast` attribute).
> The forecast sensors override `available` to remain populated across
> transient coordinator-update failures so dashboards keep rendering the
> cached trajectory.

> **Failure visibility.**  When the MPC solver fails the coordinator clears
> the `_filtered` state and every `_forecast` series instead of fabricating
> a thermal-model trajectory.  Those sensors report `unknown` until the next
> successful solve, so the apexcharts trace shows a visible gap at the
> failure point — `_measured` sensors keep flowing (they're independent of
> the MPC), and applied heater commands stay safe (the last computed action
> is held).  If a dashboard's predicted/heating-plan/solar-forecast line
> disappears for one or more cycles, that's the signal a solve failed; the
> integration log records the exception with a `WARNING`.

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
update_interval = timedelta(seconds=UPDATE_INTERVAL)   # UPDATE_INTERVAL = 900 s (15 min)
```

Every 900 seconds (15 minutes):
1. All room temperature sensors are polled from the HA state machine.
2. The outdoor temperature sensor is polled.
3. The MPC controller runs (`HeatingMPCController.compute()`).
4. Heater entities are updated via HA services.
5. All subscribed climate and sensor entities are notified to refresh their state.

---

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

### 12.2 Sensor entities – temperature (measured and filtered)

The integration exposes two temperature scalars per room:

- `sensor.heating_assistant_<room_name>_temperature_measured` — the averaged raw measurement from the configured `temp_sensor`(s).  Use this on dashboards instead of your own room sensor entity so multi-sensor rooms don't need a template helper.
- `sensor.heating_assistant_<room_name>_temperature_filtered` — the Kalman-filtered state estimate x̂⁺ after each coordinator cycle.  Smoother than the measurement and what the MPC actually sees.

| Property | Value |
|----------|-------|
| Device class | `temperature` |
| State class | `measurement` |
| Unit | °C |
| Value | Averaged measurement (measured) / EKF estimate (filtered), rounded to 2 decimal places |

**State attributes** (filtered only): `thermal_mass` (J/K), `r_external` (K/W).

### 12.3 Sensor entities – setpoint and constraint bounds

The active setpoint and the MPC soft-constraint band are each exposed as their own per-room sensor so dashboards can plot them as ordinary series:

- `sensor.heating_assistant_<room_name>_setpoint`
- `sensor.heating_assistant_<room_name>_constraint_upper` (setpoint + `constraint_offset`)
- `sensor.heating_assistant_<room_name>_constraint_lower` (setpoint − `constraint_offset`)

| Property | Value |
|----------|-------|
| Device class | `temperature` |
| State class | `measurement` |
| Unit | °C |
| Value | Setpoint, or setpoint ± soft-constraint offset, rounded to 2 decimal places |

**State attributes (all three sensors):**

| Attribute | Type | Description |
|-----------|------|-------------|
| `forecast` | list[dict] | Timestamped trajectory across the MPC horizon (N+1 entries spaced by `step_seconds`).  Each entry has `time` (ISO-8601 UTC) plus a field named after the sensor (`setpoint`, `constraint_upper`, or `constraint_lower`).  Drives apexcharts `data_generator` series so the line is anchored to the forecast window instead of relying on `extend_to`. |
| `horizon_steps` | int | OCP horizon length (N). |
| `step_seconds` | float | Time step duration. |

### 12.4 Sensor entities – heating power (measured)

**Entity ID format:** `sensor.heating_assistant_<room_name>_heating_power_measured`

| Property | Value |
|----------|-------|
| Device class | `power` |
| State class | `measurement` |
| Unit | W |
| Value | Sum of `current_power` across all sources in the room, rounded to 1 decimal |

**State attributes:**  one attribute per heat source in the room, keyed by source `name`, giving that source's individual `current_power` [W].

### 12.4b Sensor entities – solar gain (measured)

**Entity ID format:** `sensor.heating_assistant_<room_name>_solar_gain_measured`

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

This sensor stays `available` across transient coordinator-update failures so dashboards keep rendering the cached trajectory; it declares no `device_class` / `state_class` so HA's strict sensor validator accepts forecast values (predictions must not be ingested into long-term statistics).

| Property | Value |
|----------|-------|
| Device class | – |
| State class | – |
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

### 12.10 Sensor entities – outdoor temperature (measured)

**Entity ID format:** `sensor.heating_assistant_outdoor_temperature_measured`

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

### 12.12 Sensor entities – heating power forecast

**Entity ID format:** `sensor.heating_assistant_<room_name>_heating_power_forecast`

Stays `available` across transient coordinator-update failures; declares no `device_class` / `state_class` so HA's strict sensor validator accepts forecast values.

| Property | Value |
|----------|-------|
| Device class | – |
| State class | – |
| Unit | W |
| Icon | `mdi:radiator` |
| Value | Planned heating power for the first (current) MPC horizon step [W] |

**State attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `forecast` | list[dict] | Timestamped heating schedule.  Each dict contains `time` (ISO-8601 string) and `heating_power` (W).  Suitable for `apexcharts-card` to display the controller's planned heat output over the horizon. |
| `horizon_steps` | int | Number of schedule steps |
| `step_seconds` | float | Time step duration [s] |

### 12.13 Sensor entities – solar gain forecast

**Entity ID format:** `sensor.heating_assistant_<room_name>_solar_gain_forecast`

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

Stays `available` across transient coordinator-update failures; declares no `device_class` / `state_class` so HA's strict sensor validator accepts forecast values.

| Property | Value |
|----------|-------|
| Device class | – |
| State class | – |
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

All sensors update every coordinator cycle (default 900 seconds / 15 minutes) and expose detailed breakdowns as state attributes that can be plotted in Lovelace dashboards.

The diagnostic sensors (Prediction Error, Model Fit Quality, Parameter Confidence, Open-Loop RMSE, Kalman Innovation, Residual ACF) are documented in detail — including ready-to-paste ApexCharts cards — in [`MODEL_FIT_GUIDE.md`](../MODEL_FIT_GUIDE.md).  All forecast and diagnostic attributes emit ISO-8601 timestamp strings; use `new Date(e.time).getTime()` (not `e.time * 1000`) in your `data_generator` expressions.

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

- **State:** current outdoor temperature [°C] (same source as `sensor.heating_assistant_outdoor_temperature_measured`)
- **`forecast` attribute:** timestamped list of dicts — each entry contains `time` (ISO-8601 UTC) and `outdoor_temp` (°C).  The first entry is at "now" with the current measured outdoor temperature; subsequent entries cover each MPC horizon step.

**How the forecast is populated:**

When a `weather_entity` is configured (e.g. `weather.forecast_home` from Met.no), the coordinator retrieves the hourly forecast using the `weather.get_forecasts` service introduced in HA 2023.9.  For older HA versions, it falls back to reading the deprecated `forecast` state attribute.  In both cases the raw hourly forecast entries are linearly interpolated to the MPC time grid so there is a value for every horizon step.

When no weather entity is configured, a persistence forecast is used: the current outdoor temperature is repeated for every step.  Configure a weather entity (see the [Quick start](../README.md#quick-start)) for improved prediction accuracy.

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

