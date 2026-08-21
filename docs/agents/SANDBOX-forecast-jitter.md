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
  - `python3 sandbox/forecast-jitter/harness.py --tag 04 --surfaces --price peaked`
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
    on that slow grid. Iteration 4 adds the **Tuning preview** neighbour:
    `preview_tuning_forecast` re-solves the NLP on a throwaway controller.
    Room view reads `forecast_snapshot` after live `compute()` (EKF+P,
    then remaining `U*` from `_nmpc_k`, rolled from the current EKF).
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
  - **Chart.js interpolation** — Forecast datasets use `tension: 0.2` on
    both pages; Planned Power is `stepped`. Secondary to the two backend
    paths.

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
- Iteration 4 hypothesis: room-view Forecast and Tuning preview are
  the same series. Bar: max `|T_room − T_preview|` after 8 fast ticks
  comparable to integrator error (23 mK), not kelvin-scale.
- Scenario: the representative map above.

## Promote map
- Production targets (only after accept):
  - `n_int_steps` default in `heatingassistant/engine/controller/facade.py`
    and `heatingassistant/engine/estimation/model_build.py`.
  - `heatingassistant/engine/controller/facade.py` (`_forecast_U` /
    `compute()` remaining-`U*` roll vs freeze-`t_ref`; shift remaining `U*`
    and re-roll from current `x_hat`).
  - `heatingassistant/engine/control_loop.py` (`preview_tuning_forecast`
    vs `_cache_controller_forecast`).
- Copy notes: do not change the 2 h / 8 / 36 h timing triple from this
  sandbox. Iterations 1–3 are not a promote (`n_int`, ROM).

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | n_int = 1 / 10 / 40, flat price | `inspect/01_*` | n_int=40 smoother; not the live 2.5 K swing |
| 2 | peaked price; s_rom = 0.05 / 0.1 / 1 / 5, n_int=10 | `inspect/02_*` | higher ROM does not move the path |
| 3 | freeze production `U*` (n_int=10 solve); re-roll T at n_int = 1 / 10 / 40 / 100 | `inspect/03_*` | n_int=10 matches n_int=100 to 23 mK; 15 min wiggles are the plant, not Euler error |
| 4 | room-view live cache vs Tuning preview re-solve (8×15 min ticks, peaked price) | `inspect/04_*` | two different paths; max \|T\| error 1.88 K; Planned Power is a different staircase |
| 5 | operator: resim is allowed; unshifted `U*` replay is not | production `_forecast_U` | **accept remaining-`U*` resim** — freeze-`t_ref` rejected |

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

### Iteration 4 numbers (room view vs Tuning preview)

They are not the same prediction. Tuning **Preview Controller Behaviour**
calls `preview_tuning_forecast`: new controller, copy EKF, **re-solve the
NLP**, apply, one `compute()`. Room view calls `forecasts` → last
`forecast_snapshot`. Every 15 min `compute()` **replaces** the OCP air
path with `_compute_nonlinear_predictions(_forecast_U)`. `_forecast_U`
returns the last accepted `U*` from slow index 0; it does not shift by
`_nmpc_k`.

Command: `python3 sandbox/forecast-jitter/harness.py --tag 04 --surfaces --price peaked`

| Series | max \|ΔT\| [K] | max \|T − T_preview\| [K] |
|--------|----------------|---------------------------|
| apply `T_ref` (held plan) | 0.19 | 1.12 |
| preview's extra `compute()` | 0.22 | 1.15 |
| room view after 8 ticks (2 h) | 0.25 | 1.88 |
| Tuning preview re-solve | 0.57 | 0 |

Held-plan Planned Power stays the original staircase. Preview picks a
different `U*` (deeper cooling for the first ~10 h). The UI payload
matches the engine snapshot on each path, so Chart.js is not inventing
the split. Forecast lines still use `tension: 0.2` on both pages
(secondary overshoot).

### Iteration 5 (promote)

Operator: room view may resimulate, but it cannot feasibly be this
different from the NMPC path. Freeze-`t_ref` is the wrong fix.
Production `_forecast_U` now returns remaining `U_fast[_nmpc_k:]`
(2 h ZOH shifted to now); `compute()` re-rolls that sequence from the
current EKF with the same implicit-Euler `n_int` as the OCP.

## Role in pipeline
Promotion input for `/implement`. Supportive isolation — not production
source.

## Tracker
- Task: [SWD-417](https://marcusknielsen.atlassian.net/browse/SWD-417)
- Relates: [SWD-414](https://marcusknielsen.atlassian.net/browse/SWD-414)
- Artifact: `docs/agents/SANDBOX-forecast-jitter.md`
- Branch: `cursor/swd-417-forecast-jitter-ce1e`
- PR: (implement opens)

## Next
`/review-fix SWD-417` — remaining-`U*` resim on room-view Forecast
