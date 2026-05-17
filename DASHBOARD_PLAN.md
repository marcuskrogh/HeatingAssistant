# Out-of-the-box Dashboard – Implementation Plan

This document plans an out-of-the-box Lovelace dashboard for the Heating
Assistant integration. Today the integration exposes a rich set of entities
(per-room temperatures, forecasts, MPC plans, fit metrics, parameter
confidence) and `README §13.17` describes the cards you *could* build by
hand. The goal of this plan is to ship a complete, auto-generated dashboard
the moment the integration is installed – with per-room visualisations,
diagnostics, parameter-estimation tools, and analysis surfaces – and to
identify the small set of data gaps we need to fill so those surfaces work.

The plan is grounded in the current code:

- Per-room entities follow `sensor.heating_assistant_{room}_{metric}`;
  per-source entities follow `sensor.heating_assistant_{source}_{metric}`;
  system entities use `sensor.heating_assistant_{metric}`
  (`custom_components/heating_assistant/sensor.py`).
- Forecast sensors already publish timestamped `forecast` attribute arrays
  designed for `apexcharts-card`'s `data_generator`
  (e.g. `sensor.py:756–837`, `sensor.py:1066`, `sensor.py:1150`).
- Coordinator config (`coordinator.py`) exposes the room / heat-source
  topology that the dashboard generator needs to enumerate.
- Diagnostics already compute RMSE/MAE/R²/bias/ACF and parameter
  identifiability flags (`model_diagnostics.py`,
  `parameter_estimator.py`), and buttons exist for ML estimation /
  reset (`button.py`).

---

## 1. Goals and non-goals

**Goals**

1. Zero-config install: the dashboard appears automatically after the
   first `config_entry` is set up. No HACS copy-paste required for the
   default experience.
2. Per-room subviews with full MPC visualisation (output / input /
   disturbances), heat-balance breakdown, and model-fit diagnostics.
3. A dedicated diagnostics surface for parameter estimation
   (identifiability, confidence, log-likelihood landscape, residual
   whiteness) and a one-click "Estimate parameters" flow.
4. A system-wide overview with comfort status, energy use, COP, MPC
   solver health, and a multi-room heatmap.
5. Graceful degradation when optional community cards (Plotly, Sankey,
   Mushroom) are missing – never break the dashboard, just hide the
   advanced panel and surface a hint.
6. Re-generation is idempotent: editing rooms in `configuration.yaml`
   regenerates the dashboard without clobbering user customisations.

**Non-goals (for v1)**

- Bundling a Frontend JavaScript build pipeline. We will *not* ship a
  custom Lovelace strategy or custom card in v1; see §10 for the
  stretch-goal path.
- Energy-dashboard integration (kWh ↔ Home Assistant Energy panel) –
  worth doing, but tracked as a separate workstream.
- Mobile-specific layouts beyond the responsiveness HA already gives us
  via the `sections` view.

---

## 2. Distribution mechanism – how the dashboard ships

Three plausible mechanisms; recommendation is **Option B**, with A as
fallback and C as a v2 stretch goal.

| Option | Mechanism | Pros | Cons |
|---|---|---|---|
| **A. Static YAML recipe** | Ship `dashboards/heating_assistant.yaml` and document the install steps. | Trivial; no Python changes. | Users must copy-paste and substitute room names; no auto-discovery; drifts as rooms change. |
| **B. Python auto-register** *(recommended)* | On `async_setup_entry`, build a Lovelace dashboard config dict from `coordinator.config["rooms"]` and register it via Lovelace's storage collection (`hass.data["lovelace"].dashboards`). | True out-of-the-box; respects room topology; user can still edit in UI; idempotent. | Touches Home Assistant internals (`hass.data["lovelace"]`) which are semi-private; we need a feature flag + a `regenerate` service. |
| **C. Custom Lovelace strategy (JS)** | Ship a JS strategy module via `frontend.add_extra_js_url`; the dashboard YAML is a 5-line `strategy:` reference and the JS regenerates the layout at render time. | Always reflects live entities; zero re-registration needed; can render advanced cards (e.g. WebGL heatmap). | Requires a JS toolchain in the repo; harder to test; users on restrictive networks can't always load extra modules. |

**Plan**

- v1 implements **B**: a generator in
  `custom_components/heating_assistant/dashboard.py` that produces the
  Lovelace dict, plus a thin registration helper called from
  `async_setup_entry`.
- A `heating_assistant.regenerate_dashboard` service (and a matching
  button) re-runs the generator on demand and supports a `dry_run`
  mode that returns the YAML so power-users can copy it into their own
  dashboards.
- A config-entry option `dashboard.auto_install` (default `True`)
  gates the auto-registration so users who manage Lovelace in YAML
  mode can opt out.
- v1 also writes the same YAML to `<config>/dashboards/heating_assistant.yaml`
  so users in YAML mode can include it via `lovelace:` config.
- v2 stretch: replace the static generator with a JS strategy
  (Option C) once frontend tooling is in place.

---

## 3. Top-level dashboard structure

```
Heating Assistant   (sidebar entry, mdi:home-thermometer)
├── Overview               (default view, type: sections)
├── 🏠 Living Room         (subview, sections)
├── 🛏  Bedroom 1           (subview, sections)
├── …                       (one subview per room)
├── 🔧 Diagnostics         (view, sections)
├── 📈 Analysis            (view, sections)
└── ⚙  Settings & Services (view)
```

Rationale: this mirrors the structure already documented in
`README §13.17.2` ("board with room subboards"), so users coming from
the manual recipe see something familiar.

We use the modern **sections** layout (HA 2024.3+) instead of `panel`
or `masonry` so cards reflow on narrow screens and dashboard editing
in the UI stays usable.

---

## 4. Overview view

Purpose: at-a-glance comfort + energy + controller-health summary, with
navigation tiles into the per-room subviews.

| Section | Cards | Bound entities |
|---|---|---|
| **Comfort status** | One `tile` per room showing current vs. setpoint and a warning icon when `\|measured − setpoint\| > constraint_offset`. Tap-action navigates to the room subview. | `climate.heating_assistant_{room}_climate`, `sensor.heating_assistant_{room}_temperature_filtered`, `sensor.heating_assistant_{room}_setpoint` |
| **System totals** | `apexcharts-card` stacked area: heating power per source over last 24 h, with forecast extending right of Now. Number cards for total power, effective COP, schedule-active count. | `sensor.heating_assistant_system_summary` (already carries per-room breakdown in attributes), per-source `…_control_action` |
| **All-rooms temperature heatmap** | `plotly-graph-card` (optional dependency, hidden if missing): rows=rooms, x=time, colour=measured-minus-setpoint deviation. Highlights uncomfortable rooms instantly. | per-room `…_temperature_filtered` and `…_setpoint` (history via `recorder`) |
| **Weather strip** | Outdoor temperature line + solar gain (summed across rooms) over the MPC horizon. | `sensor.heating_assistant_outdoor_temperature_measured`, `…_outdoor_temperature_forecast`, per-room `…_solar_gain_forecast` |
| **Controller health** | Solve-time mini-graph + last successful update timestamp + last estimation timestamp + a coloured chip when `weather_forecast_status.quality` is degraded. | `sensor.heating_assistant_mpc_performance`, `…_estimated_parameters_status`, `…_weather_forecast_status` |

Design note: the heatmap is the most informative single visualisation
for a multi-room house, but it depends on `plotly-graph-card`. The
generator should emit it inside a `conditional` card keyed on a
small `sensor.heating_assistant_capabilities` (see §8) so that the
slot disappears cleanly when the dependency is absent.

---

## 5. Per-room subview

This is the heart of the dashboard. Each room subview is generated from
the same template, parameterised by `room_id`.

**Layout** (three sections, top-to-bottom on mobile, side-by-side on
desktop):

### 5.1 Control & comfort (left column)

- `thermostat` card → `climate.heating_assistant_{room}_climate`
- Schedule strip: timeline showing today's comfort periods (active /
  off / frost-protection). Uses the `schedule` attribute already
  consumable from the room config; rendered via `custom:timeline-card`
  if available, otherwise a flat text list.
- "Hold for 2 h / 4 h / clear" quick-action buttons (one-shot
  `climate.set_temperature` calls).

### 5.2 MPC triplet (centre / right column)

This is the canonical MPC dashboard layout. We re-use the YAML
already documented in `README §13.17.3–§13.17.5` but generated
programmatically so room names are filled in.

1. **Predicted temperature** – `apexcharts-card`:
   - history of `…_temperature_measured` (dots) and `…_temperature_filtered` (line)
   - forecast trace from the `forecast` attribute of
     `…_temperature_forecast`
   - dashed setpoint step (from `…_setpoint` value and its `forecast`
     attribute)
   - shaded soft-constraint band from `…_constraint_lower` / `_upper`
2. **Control input** – `apexcharts-card` step plot:
   - history of per-source `…_control_action` for sources whose
     `room` matches the current room (look-up via coordinator config)
   - forecast trace from `…_heating_power_forecast`
3. **Disturbances** – `apexcharts-card` dual-axis:
   - outdoor temperature history + forecast
   - solar-gain history + forecast for *this* room
   - inter-room heat flow (sum over connections) as a thin overlay

All three charts share `graph_span: 9h` and the same `span.start` so
their time axes align – matches the README contract.

### 5.3 Heat balance & diagnostics (bottom row)

- **Heat-flow Sankey** (`sankey-chart-card`, optional): sources →
  room → losses (outdoor, inter-room) + solar gain. Computed from
  current values of `…_heating_power_measured`, `…_solar_gain_measured`,
  `…_heat_loss`, and the per-connection heat flow (new attribute, see §8).
  Falls back to a stacked-bar `apexcharts` if the Sankey card is
  missing.
- **Model fit gauges**:
  - R² gauge from `…_model_fit_quality` (green ≥ 0.8, amber 0.5–0.8, red < 0.5)
  - RMSE number from the same sensor's attribute
  - Bias number (signed) – non-zero bias is the clearest indicator of
    parameter mis-estimation.
- **Residual diagnostics** (`apexcharts-card`):
  - Time-series of one-step `prediction_error`
  - Residual histogram (only if `plotly-graph-card` is present)
  - Lag-1..lag-N autocorrelation bars (uses `residual_acf` attribute;
    we need to extend that to the full ACF array – see §8)
- **Parameter card**: compact table of estimated `thermal_mass`,
  `r_external`, per-connection `r_ij`, `q_internal`, per-source
  `heater_scale`, with a coloured pill for each `is_*_identified`
  flag from `…_parameter_confidence`. Tap-action opens the
  Diagnostics view scoped to this room.

---

## 6. Diagnostics view

Purpose: support a deliberate "run estimation → inspect fit → accept
or reset" workflow that today requires copy-pasting service calls.

| Section | Card | Notes |
|---|---|---|
| **Estimation workflow** | Big primary button → `button.heating_assistant_estimate_parameters`, with status text from its attributes (`ready`, `history_steps`, `min_steps_required`). A secondary "Dry run" button calls `heating_assistant.estimate_parameters_ml` with `apply: false` (already supported). A destructive "Reset" calls the reset button. | One-click flow replaces today's YAML-service-call dance. |
| **Per-room fit matrix** | Grid: one row per room, columns R², RMSE, bias, lag-1 ACF, open-loop RMSE, last-estimated-at. Each cell colour-coded against thresholds. | `…_model_fit_quality`, `…_open_loop_rmse`, `…_estimated_parameters_status` |
| **Identifiability matrix** | Grid: rows = rooms, columns = parameters (`C`, `R_ext`, `R_ij`, `Q_int`, `alpha_s`), cells coloured by `is_*_identified` flag plus a tooltip with the variance-threshold ratio. | `…_parameter_confidence` (needs minor extension, §8) |
| **Log-likelihood landscape** | `plotly-graph-card` 2-D contour around the current MLE for a chosen room, optionally showing the trajectory of the last optimisation run. | Requires a new on-demand service `heating_assistant.compute_loglik_slice` returning a JSON payload (see §8). Mirrors `plots/fig6_ll_surface.png`. |
| **Open-loop simulation** | Card with two buttons (run / run + plot) calling the existing `run_open_loop_simulation` service, and an `apexcharts` chart of measured vs. simulated for the last 24 h. | Service already exists; we just need to surface its results via a new sensor `…_open_loop_trace` (array attribute). |
| **Residual whiteness panel** | Per-room: ACF bar chart + Ljung–Box p-value badge. | Computed in `model_diagnostics.py`; expose Ljung–Box stat as new attribute. |

---

## 7. Analysis view

Long-horizon retrospection rather than live MPC monitoring.

- **Energy by room/source** – stacked bar over selectable period
  (day / week / month). Driven by `utility_meter` helpers auto-created
  by the integration for each heat source (new wiring – see §8).
- **Setpoint adherence** – % of time per room inside the
  `[setpoint − δ, setpoint + δ]` band, with breakdowns by hour-of-day
  and by day-of-week. Mostly a `statistics` sensor + `apexcharts`.
- **Estimation history** – table of past parameter estimations
  (`estimated_at`, log-likelihood, residual stats, whether applied).
  Today only the *latest* result is exposed; add a small rolling
  buffer (size N=20) to `…_estimated_parameters_status` attributes.
- **Free-run drift analysis** – chart of open-loop RMSE vs. horizon
  step for each room, to expose systematic model error growing with
  prediction time.
- **What-if simulator** – form (`input_number` + `input_select`)
  driving the existing `simulate_thermal_response` service; result
  plotted as a chart. Lets users explore "what happens if I leave the
  heating off for 6 h?" without committing.

---

## 8. Data gaps – new entities and services we need

Most cards above already have entities. The plan adds a small set of
new surfaces, all backwards-compatible:

| Need | New entity / service | File |
|---|---|---|
| Multi-lag residual ACF for ACF bar chart | Extend `ResidualACFSensor` to publish a `lags` attribute (array of `{lag, acf}`) and `ljung_box_p`. | `sensor.py`, `model_diagnostics.py` |
| Per-connection inter-room heat flow for Sankey | New attribute `connection_flows` on `…_heat_loss` sensor: `[{to_room, watts}, …]`. | `sensor.py`, `coordinator.py` |
| Capability detection for conditional cards | New `binary_sensor.heating_assistant_capabilities` with attributes `apexcharts`, `plotly`, `sankey`, populated by reading `hass.data["lovelace_resources"]`. Generator uses these to gate optional cards. | new `binary_sensor.py` |
| Log-likelihood slice for the LL contour card | New service `heating_assistant.compute_loglik_slice(room, param_x, param_y, grid)` returning a 2-D array via service response. | `parameter_estimator.py`, `services.yaml` |
| Open-loop trace exposed for plotting | New sensor `…_open_loop_trace` with `measured[]` and `simulated[]` array attributes, updated by the open-loop service. | `sensor.py` |
| Per-source energy meters | Auto-register a `utility_meter` helper per heat source on integration setup (or expose cumulative kWh sensor with `state_class: total_increasing`). | `coordinator.py` (or new `energy.py`) |
| Estimation history buffer | Keep last N estimation results in coordinator data; expose as `estimation_history` attribute on `…_estimated_parameters_status`. | `coordinator.py`, `sensor.py` |
| Regeneration trigger | New service `heating_assistant.regenerate_dashboard(dry_run: bool)` and matching `button.heating_assistant_regenerate_dashboard`. | new `dashboard.py`, `services.yaml`, `button.py` |

None of these change existing entity IDs or attribute shapes, so users
who already wrote their own dashboards keep working.

---

## 9. Custom-card dependency strategy

We rely on these community cards:

| Card | Status | Used for | Fallback if missing |
|---|---|---|---|
| `apexcharts-card` | **required** | All time-series + forecast charts. | None – emit a markdown card with HACS install instructions and a `history-graph` placeholder. |
| `plotly-graph-card` | optional | Heatmap, residual histogram, LL contour. | Hide section; show "Install Plotly card to enable" hint. |
| `sankey-chart-card` | optional | Heat-balance Sankey. | Stacked-bar `apexcharts` alternative. |
| `mushroom-cards` | optional | Compact comfort tiles. | Built-in `tile` card. |
| `timeline-card` | optional | Schedule strip. | Plain `markdown` summary. |

The `binary_sensor.heating_assistant_capabilities` (see §8) gives the
generator the runtime knowledge of which cards are available. The
dashboard repository should include a top-of-page **Setup health card**
that nudges users to install missing-but-recommended dependencies.

---

## 10. Implementation phases

The work decomposes into four small, independently shippable PRs.

**Phase 1 – generator + Phase-A install (1–2 days)**

- Add `custom_components/heating_assistant/dashboard.py` containing
  pure functions `build_overview_view`, `build_room_view`,
  `build_diagnostics_view`, `build_analysis_view`,
  `build_dashboard(coordinator_config) -> dict`.
- Unit-test the generator: snapshot tests against fixture configs
  (1-room, 3-room, 1-room-no-windows, no-heat-source). The dict it
  produces must validate against a minimal Lovelace schema in tests.
- Ship a CLI `python -m custom_components.heating_assistant.dashboard
  > heating_assistant.yaml` so users in YAML mode can grab the
  generated file.

**Phase 2 – auto-registration (1–2 days)**

- Wire the generator into `async_setup_entry`: register a Lovelace
  storage dashboard named `heating-assistant` if the option is
  enabled and one with the same URL path doesn't already exist.
- Add `heating_assistant.regenerate_dashboard` service and button.
- Add a config-entry option `dashboard.auto_install` (default
  `True`).
- Integration test against a `pytest-homeassistant-custom-component`
  harness: install integration, assert `lovelace.dashboards` contains
  our entry, assert the YAML round-trips.

**Phase 3 – data-gap fills (2–3 days)**

- Implement the new sensors / attributes / services from §8.
- Update generator to consume them.
- Tests for each new attribute and the new services.

**Phase 4 – polish + docs (1 day)**

- Replace `README §13.17` "build your own" walkthrough with "the
  dashboard auto-installs; here's how to customise / regenerate /
  opt-out", keeping the per-card YAML reference as an appendix.
- Screenshots in `plots/` for each view (regenerate from the fig
  scripts to keep the existing aesthetic).
- Add a "Diagnostics dashboard" troubleshooting subsection.

**Stretch (post-v1)** – Phase 5: JS Lovelace strategy

- Move the Python generator behind a JS strategy module shipped via
  `frontend.add_extra_js_url`. Keep the Python generator as a
  fallback for users with `Content-Security-Policy` restrictions and
  for YAML-mode users.

---

## 11. Testing strategy

- **Generator unit tests**: snapshot Lovelace dicts for a small matrix
  of room configurations (no rooms ⇒ disabled dashboard; one room; one
  room with multiple sources; rooms with no windows; rooms with no
  schedule). Snapshots live under `tests/dashboard/snapshots/`.
- **Schema validation**: assert each card dict matches the relevant
  Lovelace card schema (we can use `voluptuous` schemas pulled from
  the HA frontend integration as references, or hand-rolled minimal
  validators per card type).
- **Auto-install integration test**: spin up `homeassistant` via
  `pytest-homeassistant-custom-component`, set up a config entry,
  assert `frontend.dashboards` contains our entry and `config_entries`
  are not duplicated on re-setup.
- **Capability gating**: assert the `plotly`/`sankey`/`mushroom`
  branches are absent when the matching resource is missing.
- **Smoke render**: a manual checklist in `tests/manual/dashboard.md`
  – install, take screenshots of each view, compare to the reference
  in `plots/`.

---

## 12. Open questions for the user

Before kicking off Phase 1 it would help to have direction on:

1. **Default-on vs opt-in.** Should the dashboard auto-install on
   integration setup (Option B default), or only after the user calls
   `regenerate_dashboard` once? Default-on is the "out of the box"
   promise but it does mutate Lovelace storage.
2. **Custom-card dependencies.** Are we comfortable making
   `apexcharts-card` a hard prerequisite (no MPC charts work without
   it) and showing an in-dashboard install prompt, or do we want a
   built-in-only fallback that uses `history-graph` (loses the
   forecast overlay)?
3. **Scope of analysis view.** Is the Phase-1 analysis view (energy,
   setpoint adherence, estimation history, free-run drift) the right
   coverage, or do you want the what-if simulator earlier / later?
4. **JS strategy timing.** Should we plan for the v2 JS-strategy
   stretch now (and structure the Python generator to mirror its
   data flow) or keep that decision deferred?

---

## 13. Quick wins worth doing first

If we want a visible improvement landed in one PR before the bigger
plan above, the highest-value standalone changes are:

- The **estimation workflow card** (one button, status text, dry-run
  toggle). It replaces the most-asked-about service-call dance.
- Multi-lag **ACF + Ljung–Box** exposure on `ResidualACFSensor` – it's
  ~30 lines and unlocks the residual whiteness panel.
- The **`regenerate_dashboard` dry-run mode** – ship just the
  generator behind a service that returns YAML in the response. Users
  can then paste it manually, exactly like the README recipe today,
  but auto-populated for their room set. This delivers most of the
  "out of the box" value without touching Lovelace storage.

These three together would land in roughly one Phase-1-sized PR and
provide a stable foundation for the full auto-install in Phase 2.
