# Sandbox: Controller Tuning preview vs room-view HA plots

## Element
Controller Tuning **Preview Controller Behaviour** and room-view Forecast /
Planned Power for the same living room, rendered with the Home Assistant
Ingress chart stack (`industrial.css`, `TimeSeriesChart`, `room-charts.js`).

## Kind
visual

## Isolation
- Path: `sandbox/preview-vs-room/`
- Harness:
  - `python3 sandbox/preview-vs-room/harness.py --tag 04`
  - `python3 sandbox/preview-vs-room/serve.py --port 8765`
- Inspectables: `sandbox/preview-vs-room/inspect/`
- Page: `http://127.0.0.1:8765/`

## Representativeness
- Relevant areas:
  - **Runtime** — production `ControlEngine.solve_nmpc_blocking`,
    `apply_nmpc_result`, `compute_actions` with `sync_fast_index`, and
    `preview_tuning_forecast`.
  - **Data** — in-band summer living room: heat-pump heat/cool 7 kW, setpoint
    23.5 °C, comfort ±2 °C, Copenhagen lat/lon, high south solar, peaked
    price, 2 h / 8 / 36 h timing. Snapshot is 7 fast ticks (~1.75 h) into
    the installed plan (`k=8` after the last publish increment).
  - **Neighbours** — production `build_app_forecast_payload`, Chart.js 4.4.7,
    `nowLinePlugin`, forecast-only Tuning charts vs history+forecast room
    charts, industrial dark theme and room selector.
  - **Path** — Room view resimulates leftover `U*` from the current EKF with
    the OCP hold (`step_hold` / `roll_fast_air_path`: implicit Euler, `n_int`
    substeps, U and d held on `_control_system`). Outdoor/solar are the
    current forecasts; wind is the horizon mean, as in the NLP. Matching
    Tuning sliders reuse that snapshot; unapplied slider changes still
    re-solve.
  - **Baseline** — production before the short-circuit (iteration 1).
- How reproduced: wrap `ControlEngine`; no production edits in the harness.
  Iteration 2 measures the promoted short-circuit in production.
  Iteration 4 measures remaining-`U*` OCP-step resim (not freeze-`T_ref`).
- Gaps:
  - **Live household EKF wall state** — named. Synthetic afternoon solar and
    a 12 h history stand in.
  - **HA history entities** — named. History arrays use the same `{s, lu}`
    shape Ingress charts consume.

## Bar
- Visual: both pages' Forecast and Planned Power series overlay when Tuning
  sliders match the live controller.
- Measure (support): plotted T vs `roll_fast_air_path` of leftover `U*` from
  the current EKF, same `x0` / `d` / plant step as `MeanOcp`, max `|ΔT|`
  ~0 (integrator identity, ~1e-12). Frozen solve-time `T_ref[k:]` is **not**
  the bar — updated outdoor/solar/wind must be allowed to move the plot.

## Promote map
- Production targets:
  - `heatingassistant/engine/nmpc_ocp.py`
    (`step_hold`, `roll_fast_air_path`; `MeanOcp` uses `step_hold`).
  - `heatingassistant/engine/controller/facade.py`
    (Forecast = remaining-`U*` resim with that hold from the current EKF;
    Planned Power = remaining `U*` with 2 h outdoor ZOH).
  - `heatingassistant/engine/control_loop.py`
    (`preview_tuning_forecast` reuses the live snapshot when draft weights
    and timing match the installed controller).
- Copy notes: do not change the 2 h / 8 / 36 h timing triple. Unapplied
  slider changes still re-solve. Do not plot shifted `T_ref`. Do not replay
  `U*` from slow index 0 against a later state.

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | initial extract: live remaining-`U*` vs Tuning re-solve | `inspect/01_*` | **delta:** same sliders must plot the live remaining plan |
| 2 | matching-params preview returns `forecast_snapshot()` | `inspect/02_*` | overlay — does not by itself show the optimiser air path |
| 3 | room Forecast is remaining OCP `T_ref` (n_int substeps) | `inspect/03_*` | **reject** — freeze-`T_ref` ignores updated disturbances |
| 4 | remaining-`U*` resim with OCP `step_hold` from current EKF | `inspect/04_*` | **accept** — vs resim max \|ΔT\| **0 K**; vs frozen `T_ref[k:]` **1.30 K**; matching preview still 0.000 K |

### Iteration 4 numbers (OCP-accuracy remaining resim)

| Series | max \|ΔT\| [K] | rms ΔT [K] |
|--------|----------------|------------|
| room view vs remaining-`U*` `roll_fast_air_path` | 0.000 | 0.000 |
| room view vs frozen `T_ref[k:]` | 1.30 | 1.03 |
| room view vs matching Tuning preview | 0.000 | 0.000 |

Room-view Forecast is leftover planner power re-simulated from the current
estimator with the same implicit-Euler hold the optimiser uses. Weather
that has changed since the solve still moves the plot. Planned Power is
leftover `U*` with two-hour holds.

### Iteration 3 numbers (freeze-`T_ref`, withdrawn)

| Series | max \|ΔT\| [K] | rms ΔT [K] |
|--------|----------------|------------|
| room view vs remaining OCP `T_ref` | 0.000 | 0.000 |
| room view vs matching Tuning preview | 0.000 | 0.000 |

Frozen `T_ref[k:]` matches the NLP only while disturbances and the EKF
stay at solve-time values. That is the wrong Forecast once outdoor/solar/wind
update.

### Iteration 1 numbers (production split)

| Series | max \|ΔT\| [K] | rms ΔT [K] | max \|ΔP\| [W] |
|--------|----------------|------------|----------------|
| room view remaining `U*` vs Tuning re-solve | 3.09 | 1.285 | 516 |

`k=8` after seven 15-minute ticks. Remaining `U*` is a mild cooling hold
(~0.04). Preview's extra NLP heats later in the horizon (up to +388 W) and
the air path diverges by 3 K.

### Iteration 2 numbers (matching snapshot)

| Series | max \|ΔT\| [K] | rms ΔT [K] | max \|ΔP\| [W] |
|--------|----------------|------------|----------------|
| room view vs Tuning (same sliders) | 0.000 | 0.000 | 0.0 |

Layout still differs on purpose: Tuning is forecast-only (solid Forecast);
room view keeps 12 h history and a dashed Forecast. The prediction samples
are the same series.

## Role in pipeline
Promotion input for `/implement`. Supportive isolation — not production
source. This session promoted remaining-`U*` OCP-step resim (iteration 4).
Freeze-`T_ref` (iteration 3) was withdrawn.

## Tracker
- Task: [SWD-431](https://marcusknielsen.atlassian.net/browse/SWD-431)
- Relates: [SWD-417](https://marcusknielsen.atlassian.net/browse/SWD-417)
- Artifact: `docs/agents/SANDBOX-preview-room-plots.md`
- Branch: `cursor/swd-431-preview-room-plots-ce1e`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/633

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/633
