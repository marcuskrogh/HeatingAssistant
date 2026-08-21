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
  - `python3 sandbox/preview-vs-room/harness.py --tag 01`
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
  - **Path** — Tuning preview re-solved a throwaway NLP; room view published
    remaining `U*` from `_nmpc_k`. After the candidate, matching draft
    weights reuse `forecast_snapshot()`.
  - **Baseline** — production before the short-circuit (iteration 1).
- How reproduced: wrap `ControlEngine`; no production edits in the harness.
  Iteration 2 measures the promoted short-circuit in production.
- Gaps:
  - **Live household EKF wall state** — named. Synthetic afternoon solar and
    a 12 h history stand in.
  - **HA history entities** — named. History arrays use the same `{s, lu}`
    shape Ingress charts consume.

## Bar
- Visual: both pages' Forecast and Planned Power series overlay when Tuning
  sliders match the live controller.
- Measure (support): max `|T_room − T_preview|` after 7 fast ticks comparable
  to integrator error (~0.02 K), not kelvin-scale.

## Promote map
- Production targets:
  - `heatingassistant/engine/control_loop.py`
    (`preview_tuning_forecast` reuses the live snapshot when draft weights
    and timing match the installed controller).
- Copy notes: do not change the 2 h / 8 / 36 h timing triple. Unapplied
  slider changes still re-solve.

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | initial extract: live remaining-`U*` vs Tuning re-solve | `inspect/01_*` | **delta:** same sliders must plot the live remaining plan |
| 2 | matching-params preview returns `forecast_snapshot()` | `inspect/02_*` | **accept** — max \|ΔT\| 0.000 K, max \|ΔP\| 0 W |

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
source. This session also promoted the accepted short-circuit.

## Tracker
- Task: [SWD-431](https://marcusknielsen.atlassian.net/browse/SWD-431)
- Relates: [SWD-417](https://marcusknielsen.atlassian.net/browse/SWD-417)
- Artifact: `docs/agents/SANDBOX-preview-room-plots.md`
- Branch: `cursor/swd-431-preview-room-plots-ce1e`
- PR: —

## Next
`/review-fix SWD-431` — Review and auto-fix on the delivery PR
