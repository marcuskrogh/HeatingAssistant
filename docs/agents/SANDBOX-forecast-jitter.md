# Sandbox: Forecast temperature jitter vs integrator substeps

## Element
Room-view Forecast (nonlinear air path) and Planned Power after an
accepted two-hour NMPC plan. Operator reports high-frequency temperature
jitter (~22.5–25.0 °C). Hypotheses: (1) implicit-Euler `n_int_steps`
too low; (2) price penalty chases price and ROM (`smoothing_weight`)
should damp it.

## Kind
measure

## Isolation
- Path: `sandbox/forecast-jitter/`
- Harness:
  - `python3 sandbox/forecast-jitter/harness.py --tag 01`
  - `python3 sandbox/forecast-jitter/harness.py --tag 02 --price peaked --smoothing 0.05,0.1,1.0,5.0`
- Inspectables: `sandbox/forecast-jitter/inspect/`

## Representativeness
- Relevant areas:
  - **Runtime** — production `ControlEngine.solve_nmpc_blocking` (SciPy
    SLSQP, same App-process NLP as live).
  - **Data** — in-band summer living room: T0 25.2 °C, setpoint 23.5 °C,
    comfort ±2 °C, heat-pump heat/cool, Copenhagen lat/lon, high south
    solar, 2 h / 8 fast / 36 h timing. Iteration 2 adds a two-peak price
    forecast (~1.2–2.6) like the room-view Price Forecast.
  - **Neighbours** — production `HouseThermalSDE` + `implicit_euler_substeps`.
    Forecast is the OCP air path sampled every 15 min. Price enters only
    as a 2 h mean (`mean_price_slow`). ROM penalises \(\|u_n-u_{n-1}\|^2\)
    on that slow grid.
  - **Path** — `n_int_steps` shared by EKF, `MeanOcp`, and the nonlinear
    Forecast roll; `smoothing_weight` is `MeanOcp.s_rom`.
  - **Baseline** — `n_int_steps=10`; App ROM fallback 0.05; engine/Tuning
    ROM default 0.1.
- How reproduced: wrap `ControlEngine`; no production edits.
- Gaps:
  - **Live EKF wall state and outdoor series** — named. Peaked synthetic
    prices did not recreate the live −1.5 kW pulses.
  - **Chart.js interpolation** — engine snapshot only.

## Bar
- Metrics: max |ΔT| / RMS ΔT on consecutive 15 min Forecast points;
  max |ΔP| between 2 h holds; median U hold (h).
- Iteration 1 hypothesis: `n_int_steps` 10 → 40 cuts jitter enough to
  promote.
- Iteration 2 hypothesis: higher `smoothing_weight` damps price-driven
  Planned Power jumps enough to promote.
- Scenario: the representative map above.

## Promote map
- Production targets (only after accept):
  - `n_int_steps` default in `heatingassistant/engine/controller/facade.py`
    and `heatingassistant/engine/estimation/model_build.py`.
  - `smoothing_weight` / Tuning “Output Smoothing” (App options fallback
    0.05 in `heatingassistant/app/runtime.py`).
- Copy notes: do not change the 2 h / 8 / 36 h timing triple from this
  sandbox. Neither iteration is a promote.

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | n_int = 1 / 10 / 40, flat price | `inspect/01_*` | n_int=40 smoother; not the live 2.5 K swing |
| 2 | peaked price; s_rom = 0.05 / 0.1 / 1 / 5, n_int=10 | `inspect/02_*` | higher ROM does not move the path |

### Iteration 1 numbers (flat price)

| n_int | max \|ΔT\| [K] | RMS ΔT [K] | ΔT sign-flips | U hold [h] |
|-------|----------------|------------|---------------|------------|
| 1 | 0.64 | 0.107 | 18 | 2.0 |
| 10 (baseline) | 0.80 | 0.116 | 20 | 2.0 |
| 40 | 0.45 | 0.090 | 14 | 2.0 |

### Iteration 2 numbers (peaked price, n_int=10)

| s_rom | max \|ΔT\| [K] | max 2 h ΔP [kW] | U hold [h] |
|-------|----------------|-----------------|------------|
| 0.05 (App fallback) | 0.193 | 0.059 | 2.0 |
| 0.1 (engine / Tuning) | 0.193 | 0.059 | 2.0 |
| 1.0 | 0.193 | 0.059 | 2.0 |
| 5.0 | 0.193 | 0.058 | 2.0 |

Four traces overlay. Comfort dominates ROM; Δu between 2 h blocks is
already ~0.06 kW. Price is averaged onto those blocks, so 15 min price
ticks never reach U. Remaining Forecast kinks are 15 min solar /
integration plus 2 h hold edges.

Raising output smoothing live is safe and is unlikely to change this
plot. It would matter only if Planned Power were already jumping by
large 2 h steps.

## Role in pipeline
Promotion input for `/implement`. Supportive isolation — not production
source.

## Tracker
- Task: [SWD-417](https://marcusknielsen.atlassian.net/browse/SWD-417)
- Relates: [SWD-414](https://marcusknielsen.atlassian.net/browse/SWD-414)
- Artifact: `docs/agents/SANDBOX-forecast-jitter.md`
- Branch: `cursor/swd-417-forecast-jitter-ce1e`
- PR: — (sandbox never opens a PR)

## Next
`/sandbox SWD-417` — next inspect turn after the operator names a delta
(higher ROM is not a promote from iteration 2)
