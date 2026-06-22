# Dashboards & Lovelace Cards

> How to visualise Heating Assistant — the auto-generated dashboards and a
> library of hand-built ApexCharts / native Lovelace cards for predicted
> temperature, control input, disturbance forecasts and system overview.

Most users can start with the out-of-the-box dashboard (see
[13.17.0](#13170-out-of-the-box-dashboard)); the manual card recipes below are
for building a custom view. These cards read the sensors documented in the
[Entities reference](ENTITIES.md). See also the live model-fit cards in
[`MODEL_FIT_GUIDE.md`](../MODEL_FIT_GUIDE.md).

---

### 13.17 Lovelace dashboard – board and card reference

#### 13.17.0 Out-of-the-box dashboard

Heating Assistant ships a Lovelace dashboard generator. Nothing else to install
on the Python side: the integration writes
`<config>/dashboards/heating_assistant.yaml` automatically the first time the
config entry sets up and, on Home Assistant versions that expose the
Lovelace storage API, also registers a **Heating Assistant** entry in the
sidebar pointing at that file. If the auto-registration step is skipped
(unusual – older HA, custom Lovelace setups), a persistent notification
explains the two-click path to add the dashboard manually.

> Prerequisite: the `apexcharts-card` HACS frontend card (every MPC chart on
> the auto-generated dashboard depends on it).

What you get out of the box:

* **Overview** view – per-room comfort tiles, a stacked 24 h system-power
  chart, cumulative kWh per heat source, an MPC / weather health glance card
  and an outdoor-temperature + solar-gain strip.
* **Per-room subview** – a thermostat card, the MPC triplet (predicted
  temperature with setpoint band, planned heating power, disturbance
  forecasts), model-fit gauges (R² + RMSE/MAE/bias/ACF/open-loop-RMSE), a
  residual time-series, the free-run open-loop trace and a compact estimated-
  parameter table. Comfort-schedule controls appear when the room has a
  schedule configured.
* **Diagnostics** view – one-click ML estimation (including a dry-run
  button), per-room fit matrix, residual-whiteness chips (lag-1 ACF +
  Ljung-Box), open-loop validation panel that wires to
  `run_open_loop_simulation` / `analyze_model_fit`, parameter-identifiability
  chips, **Compute slice** buttons per room that fire the new
  `compute_loglik_slice` service for a (log C, log R_ext) likelihood
  landscape, and a rolling Estimation-history table (last 20 runs).
* **Settings & Services** view – regenerate dashboard, reload integration,
  per-room schedule toggles.

Useful services exposed for the dashboard:

* `heating_assistant.regenerate_dashboard` – returns the YAML in the response
  (`dry_run: true`) or writes it to `<config>/dashboards/`.
* `heating_assistant.compute_loglik_slice` – evaluates the CD-EKF
  log-likelihood on an `n_grid × n_grid` grid around the room's MLE.
* `heating_assistant.run_open_loop_simulation`,
  `heating_assistant.analyze_model_fit` – existing diagnostics.

If you delete the auto-generated file the integration won't recreate it on
restart (a one-shot marker remembers it has already written the starter
dashboard). To opt back in just call `regenerate_dashboard` again.

#### 13.17.1 Manual / hand-built cards

The card recipes below remain useful if you want to compose your own
dashboard, embed a Heating Assistant chart in an existing Lovelace view, or
add advanced cards (Plotly contours, Sankey flow diagrams) that the v1
generator doesn't yet emit.

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
- **History** (left of Now): filtered estimate y(k|k) (solid blue line) and actual measurements y(k) (red dots, marker size 4)
- **Prediction** (right of Now): the MPC-predicted temperature trajectory (solid line)
- A dashed setpoint step line visible across both history and forecast windows
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
  # ── History: filtered estimate y(k|k) (historical recorder values) ─────
  - entity: sensor.heating_assistant_living_room_temperature_filtered
    name: Filtered estimate (y(k|k))
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
  # ── History: actual averaged measurement y(k) ─────────────────────────
  - entity: sensor.heating_assistant_living_room_temperature_measured
    name: Actual measurement (y(k))
    type: scatter
    yaxis_id: temp
    color: '#E53935'
    stroke_width: 0
    float_precision: 2
    extend_to: now
    group_by:
      func: raw
      fill: 'null'
    show:
      in_header: false
  # ── History: setpoint (recorder history, ends at "Now") ──────────────
  - entity: sensor.heating_assistant_living_room_setpoint
    name: Setpoint
    yaxis_id: temp
    color: '#F44336'
    stroke_width: 2
    stroke_dash: 5
    curve: stepline
    float_precision: 1
    extend_to: now
    group_by:
      func: raw
      fill: last
    show:
      in_header: false
  # ── Forecast: setpoint over the MPC horizon ──────────────────────────
  - entity: sensor.heating_assistant_living_room_setpoint
    name: Setpoint (forecast)
    data_generator: |
      const fc = entity.attributes.forecast;
      if (!fc) return [];
      return fc.map(f => [new Date(f.time).getTime(), f.setpoint]);
    yaxis_id: temp
    color: '#F44336'
    stroke_width: 2
    stroke_dash: 5
    curve: stepline
    float_precision: 1
    show:
      legend_value: false
      in_header: false
  # ── Forecast: constraint upper bound ─────────────────────────────────
  - entity: sensor.heating_assistant_living_room_constraint_upper
    name: Constraint Upper
    data_generator: |
      const fc = entity.attributes.forecast;
      if (!fc) return [];
      return fc.map(f => [new Date(f.time).getTime(), f.constraint_upper ?? null]);
    yaxis_id: temp
    color: '#90CAF9'
    stroke_width: 1
    curve: stepline
    opacity: 0.5
    show:
      legend_value: false
      in_header: false
  # ── Forecast: constraint lower bound ─────────────────────────────────
  - entity: sensor.heating_assistant_living_room_constraint_lower
    name: Constraint Lower
    data_generator: |
      const fc = entity.attributes.forecast;
      if (!fc) return [];
      return fc.map(f => [new Date(f.time).getTime(), f.constraint_lower ?? null]);
    yaxis_id: temp
    color: '#90CAF9'
    stroke_width: 1
    curve: stepline
    opacity: 0.5
    show:
      legend_value: false
      in_header: false
  # ── Forecast: predicted temperature trajectory ───────────────────────
  - entity: sensor.heating_assistant_living_room_temperature_forecast
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

> **Tip:** Replace `living_room` with your room's entity suffix throughout — every series references HeatingAssistant-owned sensors, so no per-installation entity substitutions are needed.  The setpoint/constraint *forecast* series use a `data_generator` against each sensor's `forecast` attribute so the line is anchored to the MPC horizon (rather than extending the historical recorder value indefinitely).

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
  - entity: sensor.heating_assistant_living_room_heating_power_measured
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
  - entity: sensor.heating_assistant_living_room_heating_power_forecast
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

The outdoor temperature forecast is read from `sensor.heating_assistant_outdoor_temperature_forecast` (see § 13.8), which exposes a dedicated `forecast` attribute.  When a weather entity is configured, this forecast varies over the horizon; otherwise it is a flat persistence forecast equal to the current outdoor temperature.

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
  - entity: sensor.heating_assistant_outdoor_temperature_measured
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
  - entity: sensor.heating_assistant_living_room_solar_gain_measured
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
  - entity: sensor.heating_assistant_outdoor_temperature_forecast
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
  - entity: sensor.heating_assistant_living_room_solar_gain_forecast
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
  - entity: sensor.heating_assistant_living_room_temperature_filtered
    name: Predicted Temperature
  - entity: sensor.heating_assistant_living_room_heating_power_measured
    name: Heating Power
  - entity: sensor.heating_assistant_living_room_solar_gain_measured
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
  - entity: sensor.heating_assistant_outdoor_temperature_measured
    name: Outdoor Temperature (measured)
  - entity: sensor.heating_assistant_outdoor_temperature_forecast
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
      - entity: sensor.heating_assistant_living_room_temperature_filtered
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
      # ── History: filtered estimate y(k|k) (historical recorder values) ─
      - entity: sensor.heating_assistant_living_room_temperature_filtered
        name: Filtered estimate (y(k|k))
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
      # ── History: actual averaged measurement y(k) ────────────────────
      - entity: sensor.heating_assistant_living_room_temperature_measured
        name: Actual measurement (y(k))
        type: scatter
        yaxis_id: temp
        color: '#E53935'
        stroke_width: 0
        float_precision: 2
        extend_to: now
        group_by:
          func: raw
          fill: 'null'
        show:
          in_header: false
      # ── History: setpoint (recorder history up to "Now") ─────────────
      - entity: sensor.heating_assistant_living_room_setpoint
        name: Setpoint
        yaxis_id: temp
        color: '#F44336'
        stroke_width: 2
        stroke_dash: 5
        curve: stepline
        float_precision: 1
        extend_to: now
        group_by:
          func: raw
          fill: last
        show:
          in_header: false
      # ── Forecast: setpoint across the MPC horizon ─────────────────────
      - entity: sensor.heating_assistant_living_room_setpoint
        name: Setpoint (forecast)
        data_generator: |
          const fc = entity.attributes.forecast;
          if (!fc) return [];
          return fc.map(f => [new Date(f.time).getTime(), f.setpoint]);
        yaxis_id: temp
        color: '#F44336'
        stroke_width: 2
        stroke_dash: 5
        curve: stepline
        float_precision: 1
        show:
          legend_value: false
          in_header: false
      # ── Forecast: constraint upper bound ─────────────────────────────
      - entity: sensor.heating_assistant_living_room_constraint_upper
        name: Constraint Upper
        data_generator: |
          const fc = entity.attributes.forecast;
          if (!fc) return [];
          return fc.map(f => [new Date(f.time).getTime(), f.constraint_upper ?? null]);
        yaxis_id: temp
        color: '#1565C0'
        stroke_width: 1
        curve: stepline
        show:
          legend_value: false
          in_header: false
      # ── Forecast: constraint lower bound ─────────────────────────────
      - entity: sensor.heating_assistant_living_room_constraint_lower
        name: Constraint Lower
        data_generator: |
          const fc = entity.attributes.forecast;
          if (!fc) return [];
          return fc.map(f => [new Date(f.time).getTime(), f.constraint_lower ?? null]);
        yaxis_id: temp
        color: '#1565C0'
        stroke_width: 1
        curve: stepline
        show:
          legend_value: false
          in_header: false
      # ── Forecast: predicted temperature trajectory ───────────────────
      - entity: sensor.heating_assistant_living_room_temperature_forecast
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
      - entity: sensor.heating_assistant_living_room_heating_power_measured
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
      - entity: sensor.heating_assistant_living_room_heating_power_forecast
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
      - entity: sensor.heating_assistant_outdoor_temperature_measured
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
      - entity: sensor.heating_assistant_living_room_solar_gain_measured
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
      - entity: sensor.heating_assistant_outdoor_temperature_forecast
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
      - entity: sensor.heating_assistant_living_room_solar_gain_forecast
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

> **Adapting for other rooms:** Duplicate this vertical-stack card for each room subview and replace every occurrence of `living_room` with the room's entity suffix (e.g. `bedroom`, `kitchen`).  The example uses `graph_span: 9h` with `offset: '-6h'`, giving 6 h of recorder history before *Now*.  The MPC forecast appears after *Now*: with the default settings (`dt: 900`, `horizon: 100`) the prediction spans **25 hours**.  For shorter horizons (e.g. `horizon: 6`), the prediction spans 90 minutes — size the window as **history = 2 × horizon** and **total span = 3 × horizon**.

---

