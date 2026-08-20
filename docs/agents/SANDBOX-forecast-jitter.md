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
  - `python3 sandbox/forecast-jitter/harness.py --tag 03 --fixed-u`
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
    Forecast roll; `smoothing_weight` is `MeanOcp.s_rom`. Two-hour ZOH is
    the **control sampling interval**; temperature is implicit-Euler
    substepped inside each 15 min tick. Iteration 3 freezes production
    `U*` and re-rolls that same sequence (`_compute_nonlinear_predictions`
    / per-substep `implicit_euler_step`).
  - **Baseline** — `n_int_steps=10`; App ROM fallback 0.05; engine/Tuning
    ROM default 0.1.
- How reproduced: wrap `ControlEngine`; no production edits.
- Gaps:
  - **Live EKF wall state and outdoor series** — named. Peaked synthetic
    prices did not recreate the live −1.5 kW pulses. The live ~2.5 K
    Forecast swing is still not reproduced.
  - **OCP Jacobian** — named. Frozen-`U*` tests the forward map only.
  - **Chart.js interpolation** — engine snapshot only.

## Bar
- Metrics: max |ΔT| / RMS ΔT on consecutive 15 min Forecast points;
  max |ΔP| between 2 h holds; median U hold (h).
- Iteration 1 hypothesis: `n_int_steps` 10 → 40 cuts jitter enough to
  promote.
- Iteration 2 hypothesis: higher `smoothing_weight` damps price-driven
  Planned Power jumps enough to promote.
- Iteration 3 hypothesis: production Forecast `T` at `n_int=10` is an
  inaccurate discretisation of the same frozen `U*` relative to a
  high-fidelity substepped roll (`n_int=100`). Bar: max `|T_10 − T_100|`
  comparable to the 15 min `|ΔT|` (~0.8 K here, ~2.5 K live).
- Scenario: the representative map above.

## Promote map
- Production targets (only after accept):
  - `n_int_steps` default in `heatingassistant/engine/controller/facade.py`
    and `heatingassistant/engine/estimation/model_build.py`.
  - `smoothing_weight` / Tuning “Output Smoothing” (App options fallback
    0.05 in `heatingassistant/app/runtime.py`).
- Copy notes: do not change the 2 h / 8 / 36 h timing triple from this
  sandbox. Iterations 1–3 are not a promote (`n_int`, ROM).

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | n_int = 1 / 10 / 40, flat price | `inspect/01_*` | n_int=40 smoother; not the live 2.5 K swing |
| 2 | peaked price; s_rom = 0.05 / 0.1 / 1 / 5, n_int=10 | `inspect/02_*` | higher ROM does not move the path |
| 3 | freeze production `U*` (n_int=10 solve); re-roll T at n_int = 1 / 10 / 40 / 100 | `inspect/03_*` | n_int=10 matches n_int=100 to 23 mK; 15 min wiggles are the plant, not Euler error |

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
ticks never reach U.

### Iteration 3 numbers (frozen `U*` from n_int=10 solve)

Two-hour ZOH is the control sampling interval, not the temperature
discretisation. Forecast points are 15 min samples of a substepped
implicit-Euler roll (`n_int` steps inside each 900 s tick). A jagged
Forecast versus a faithful high-fidelity roll of the same `U*` would be
NMPC simulation error.

Command: `python3 sandbox/forecast-jitter/harness.py --tag 03 --fixed-u`

| n_int (roll) | max \|ΔT\| [K] | max \|T − T_100\| [K] | max \|T − T_OCP\| [K] |
|--------------|----------------|-----------------------|-----------------------|
| 1 | 0.66 | 0.191 | 0.168 |
| 10 (production) | 0.80 | 0.023 | 0 |
| 40 | 0.81 | 0.004 | 0.019 |
| 100 (reference) | 0.82 | 0 | 0.023 |

`U*` is the same 2 h cooling staircase (−0.19 to −0.57 kW) in every roll.
The published OCP air path matches the independent n_int=10 roll exactly.
Production n_int=10 vs n_int=100 is 23 mK worst-case, 3.5 mK RMS — far
below the 0.80 K 15 min steps that survive at n_int=100. Those 15 min
knots sit on the dense substep curve. Raising `n_int` does not remove
the wiggles and is not a promote for Forecast accuracy.

The live ~2.5 K swing is still not in this scenario.

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
(frozen-`U*` n_int=10 is accurate; not a promote)
