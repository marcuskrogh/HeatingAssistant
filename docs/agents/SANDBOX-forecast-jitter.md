# Sandbox: Forecast temperature jitter vs integrator substeps

## Element
Room-view Forecast (nonlinear air path) and Planned Power after an
accepted two-hour NMPC plan. Operator reports high-frequency temperature
jitter (~22.5–25.0 °C) and hypothesises that implicit-Euler substeps in
the EKF/OCP simulation (`n_int_steps`, default 10 per 15 min tick) are
too low.

## Kind
measure

## Isolation
- Path: `sandbox/forecast-jitter/`
- Harness: `python3 sandbox/forecast-jitter/harness.py --tag 01`
- Inspectables: `sandbox/forecast-jitter/inspect/`

## Representativeness
- Relevant areas:
  - **Runtime** — production `ControlEngine.solve_nmpc_blocking` (SciPy
    SLSQP, same App-process NLP as live).
  - **Data** — in-band summer living room: T0 25.2 °C, setpoint 23.5 °C,
    comfort ±2 °C, heat-pump heat/cool, Copenhagen lat/lon, high south
    solar, 2 h / 8 fast / 36 h timing. Matches the Aug 21 room-view
    screenshot’s band and NOW, not the live EKF wall state or price
    series.
  - **Neighbours** — production `HouseThermalSDE` + `implicit_euler_substeps`;
    Forecast is the OCP `t_ref` / nonlinear rollout sampled every 15 min
    (inner Euler substeps are not plotted).
  - **Path** — same `n_int_steps` knob used by EKF `n_steps`, `MeanOcp`,
    and `_compute_nonlinear_predictions`.
  - **Baseline** — production default `n_int_steps=10`.
- How reproduced: wrap `ControlEngine`; patch `n_int_steps` on the
  controller, both SDEs, and EKF params; solve and plot Forecast +
  Planned Power.
- Gaps:
  - **Live household EKF wall state, price, and outdoor series** — named.
    Synthetic cooling is a 2 h staircase at −0.2 to −0.57 kW, not the
    live −1.5 kW pulses. Amplitude of live 22.5–25 °C swings is not
    reproduced; the *shape* of 15 min kinks is.
  - **Chart.js interpolation / dataset index** — not in this harness
    (engine snapshot only).

## Bar
- Metrics: max |ΔT| and RMS ΔT between consecutive 15 min Forecast
  points; sign-flips of ΔT; median U hold (h).
- Hypothesis: raising `n_int_steps` from 10 to 40 cuts Forecast jitter
  enough to promote.
- Scenario: the representative map above.

## Promote map
- Production targets (only after accept):
  - `HeatingMPCController` / `HouseThermalSDE` default `n_int_steps`
    (today 10) in `heatingassistant/engine/controller/facade.py` and
    `heatingassistant/engine/estimation/model_build.py`.
  - Same knob is EKF `ContinuousDiscreteEKFParams.n_steps`.
- Copy notes: do not change the 2 h / 8 / 36 h timing triple. If the
  analytic `dJ/dU` chain (`MeanOcp._h_sub` × `n_int`) is the real
  chatter source, that is a separate production fix — not this knob
  alone.

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | initial extract: n_int = 1 / 10 / 40 on production NMPC | `sandbox/forecast-jitter/inspect/01_forecast.png`, `01_dT.png`, `01_report.json` | delta: waiting — n_int=40 is smoother; n_int=10 is not the live 2.5 K swing |

### Iteration 1 numbers

| n_int | max \|ΔT\| [K] | RMS ΔT [K] | ΔT sign-flips | U hold [h] |
|-------|----------------|------------|---------------|------------|
| 1 | 0.64 | 0.107 | 18 | 2.0 |
| 10 (baseline) | 0.80 | 0.116 | 20 | 2.0 |
| 40 | 0.45 | 0.090 | 14 | 2.0 |

U is already a 2 h zero-order hold. `n_int=10` is slightly *worse* than
`n_int=1` on max |ΔT|. `n_int=40` damps the 15 min kinks (~44% on max
|ΔT|) and picks a different cooling staircase. That is consistent with
integrator *and* analytic-Jacobian scaling (`_h_sub = dt / n_int`)
changing the NLP, not with an unstable EKF.

Live 22.5–25.0 °C chatter is larger than this synthetic’s 0.8 K steps.
Bumping substeps alone would not match that screenshot.

## Role in pipeline
Promotion input for `/implement`. Supportive isolation — not production
source. Post-merge inspect-loop instead of `/iterate` because each turn
needs the Forecast plot.

## Tracker
- Task: [SWD-417](https://marcusknielsen.atlassian.net/browse/SWD-417)
- Relates: [SWD-414](https://marcusknielsen.atlassian.net/browse/SWD-414)
- Artifact: `docs/agents/SANDBOX-forecast-jitter.md`
- Branch: `cursor/swd-417-forecast-jitter-ce1e`
- PR: — (sandbox never opens a PR)

## Next
`/sandbox SWD-417` — next inspect turn after the operator names a delta,
or `/implement SWD-417` if they accept a promote (not recommended from
iteration 1 alone)
