# Sandbox: Room-view temperature forecast oscillates while planned power is smooth

## Element
Room-view Forecast resim (`_compute_nonlinear_predictions` /
`roll_fast_air_path`) after an accepted two-hour NMPC plan. Operator
screenshot (22 Aug 2026, setpoint 23.5 °C): Planned Power is a regular
cooling curve (~0 to −1.5 kW) while Forecast jumps ~22.5–24.5 °C, then
drops toward 20 °C at the far right.

## Kind
measure

## Isolation
- Path: `sandbox/forecast-oscillation/`
- Harness:
  - `python3 sandbox/forecast-oscillation/harness.py --tag 01`
  - `python3 sandbox/forecast-oscillation/harness.py --tag 02`
  - `python3 sandbox/forecast-oscillation/harness.py --tag 03`
- Inspectables: `sandbox/forecast-oscillation/inspect/`

## Representativeness
- Relevant areas:
  - **Runtime** — production `ControlEngine` / `HeatingMPCController`
    (`_forecast_U`, `_publish_plan_rollout`, `_compute_nonlinear_predictions`,
    `_forecast_solar`, `select_ghi_for_step`). Same App-process plant as live.
  - **Data** — summer living room: T0 23.8 °C, setpoint 23.5 °C, heat-pump
    heat/cool 7 kW, Copenhagen lat/lon, high south solar, peaked price,
    2 h / 8 / 36 h timing, NOW = `2026-08-22T05:54:00Z` (screenshot).
    Iteration 3 forces the screenshot power envelope through that path.
  - **Neighbours** — production `HouseThermalSDE` + `step_hold` (implicit
    Euler, `n_int` substeps). Independent `solve_ivp` RK45 on the same
    ZOH U/d. Ingress payload (`build_app_forecast_payload`) including the
    NOW bridge sample. Chart.js cubic extra estimated via `CubicSpline`.
  - **Path** — remaining `U*` is 2 h ZOH (`np.repeat`). Forecast T is 15 min
    air-node samples. Planned Power is leftover `U*` with one outdoor sample
    per hold (cooling: display watts = plant watts). GHI is interpolated onto
    the fast grid; `_forecast_solar` then calls `select_ghi_for_step`.
  - **Baseline** — production remaining-`U*` resim (`n_int=10`) after
    SWD-417 / SWD-431.
- How reproduced: wrap `ControlEngine`; no production edits.
- Gaps:
  - **Live household GHI series** — named. `compute_ghi_series` writes
    `None` only *outside* coverage (a tail), not every other 15 min slot.
    The ~2 K / 78-flip reproduction needs `None` slots on the fast grid.
    A 24 h Open-Meteo GHI vs 36 h horizon *does* produce a None tail.
    Sparse HA irradiance attrs (missing hours kept as holes instead of a
    timestamped series) would interleave. Not captured from the live box.
  - **Live EKF wall state and identified C** — named. `ControlEngine._build_house_model`
    ignores room-config `c_air_fraction` (always 0.05). Wall+5 K and a
    `c_air` sweep did not by themselves make a smooth U violent.

## Bar
- Metrics: max |ΔT| / sign-flips on consecutive 15 min Forecast points;
  night vs day split; production vs RK45; payload time monotonicity.
- Live bar: ~2 K p-p, many 15 min reversals, while Planned Power looks
  regular.
- Integrator bar: frozen U/d must not ring; vs RK45 ≪ 15 min |ΔT|.
- Application bar: the same smooth U the operator sees must not produce
  live-scale reversals once U, d, and GHI are applied as production does.
- Scenario: the representative map above.

## Promote map
- Production targets (accept):
  - `heatingassistant/engine/solar_forecast.py` (`select_ghi_for_step`)
  - `heatingassistant/engine/controller/facade.py` (`_forecast_solar`:
    `k >= 1` must pass `fallback=None`, not `ghi_now`)
  - `tests/test_controller.py` (`test_forecast_solar_per_step_fallback`
    currently encodes the leak)
  - App dual-tree copy via `scripts/sync-ha-app-package.sh` if the engine
    module is shipped in the App package
- Copy notes: when `ghi_forecast[k]` is `None` or out of range, intensity
  must take the cloud/clear-sky path (`ghi=None` in `_intensity_dni_dhi`).
  Only `k = 0` uses measured `ghi_now`. Do not persist a daytime GHI into
  later steps. Do not change the 2 h / 8 / 36 h timing triple. Do not
  raise `n_int` for this. Chart.js `tension: 0.2` is not this bug (~8 mK).

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | initial extract: production remaining-`U*` + RK45 + energy residual | `inspect/01_*` | integrator stable; live 2 K not reproduced |
| 2 | hold d on the 2 h U grid (MODEL `d_n`) | `inspect/02_*` | **reject** — T flips almost unchanged; solar becomes a worse staircase |
| 3 | force screenshot U (smooth / 2 h stairs / large steps / const / short dip); GHI None tail; interleaved GHI None; `c_air` sweep | `inspect/03_*` | **accept GHI `None` → analytical, not `ghi_now`** |

### Iteration 1 numbers (production NMPC U*)

| Check | Result |
|-------|--------|
| `HouseThermalSDE._F` | real, negative (`−1.2e-3`, `−4.1e-6`); no ring |
| frozen U=−0.22, d const | 0 sign flips; vs RK45 73 mK |
| remaining `U*` vs same-path resim | 0 K |
| remaining `U*` vs RK45 | 17 mK |
| energy residual | RMS ~4 W, max ~32 W |
| payload times | monotonic, dt = 900 s |
| production T | max \|ΔT\| 0.56 K, 24 flips, T ∈ [21.5, 24.3] °C |
| production P | max \|ΔP\| 0.30 kW, 0 flips, U ∈ [−0.56, 0.08] kW |

The discrete map is not diverging. Synthetic NMPC did not reach the live
−1.5 kW dip.

### Iteration 2 numbers (mean-hold d)

| Series | max \|ΔT\| | flips |
|--------|------------|-------|
| production 15 min d | 0.559 K | 24 |
| candidate 2 h mean d | 0.490 K | 22 |
| vs production | 0.551 K envelope | — |

Holding d on the U grid does not remove T reversals. Solar becomes a 2 h
staircase (Δq 503 W). Rejected.

Night hours inside a 2 h U hold: first 15 min air overshoot, then settle
until the next U step. That is 2R2C air–wall dynamics (`C_air = 0.05 C`,
τ_air ≈ 14 min ≈ one fast tick), matched by RK45. It is not 15 min
alternating physics.

### Iteration 3 numbers (forced screenshot U + GHI)

UA_fast ≈ 300 W/K ⇒ ~3.3 K per kW step of air-node quasi-steady. q_cool =
5000 W. Display P vs plant Q: 0.05 W (cooling).

| U profile (production d) | max \|ΔT\| | flips | night max \|ΔT\| |
|--------------------------|------------|-------|------------------|
| 15 min smooth bowl to −1.5 kW | 0.571 K | 2 | 0.288 K (0 flips) |
| 2 h stairs of the same bowl | 0.537 K | 14 | 0.308 K |
| large 2 h steps to −1.5 kW | 1.212 K | 9 | 0.295 K |
| constant −1.5 kW | 1.142 K | 2 | 0.268 K |
| U = 0 (disturbances only) | 0.301 K | 3 | 0.063 K |
| short 4 h dip to −1.5 kW (stairs) | 1.582 K | — | — |

RK45 vs implicit Euler on 2 h stairs: 17 mK. Cubic spline extra vs 15 min
knots: 8 mK. Payload times monotonic; NOW-bridge jump −0.49 K once.

| GHI defect | max \|ΔT\| | flips |
|------------|------------|-------|
| 24 h GHI then `None` tail (`ghi_now` leak) | envelope vs stairs **2.14 K** | (tail) |
| **every other step `None` → `ghi_now`, smooth U** | **1.94 K** | **78** |
| same holes, U = 0 | **1.94 K** | **85** |
| q_solar with those holes | Δq **1739 W** | — |

A regular U does **not** make Forecast violent. The production GHI
fallback does: `select_ghi_for_step(..., fallback=ghi_now)` keeps a
stale daytime irradiance on the GHI path whenever a step is `None`,
against both `_forecast_solar` and `select_ghi_for_step` docstrings
(analytical / cloud path). That is the only in-path defect that
reproduced live-scale 15 min reversals with a smooth input.

`c_air_fraction` 0.01 / 0.05 / 0.20 on 2 h stairs: 0.81 / 0.54 / 0.40 K.
Config is ignored by `ControlEngine._build_house_model`; not this
oscillation.

## Role in pipeline
Promotion input for `/implement`. Supportive isolation — not production
source. Post-merge inspect-loop instead of `/iterate`.

## Tracker
- Task: [SWD-432](https://marcusknielsen.atlassian.net/browse/SWD-432)
- Relates: [SWD-431](https://marcusknielsen.atlassian.net/browse/SWD-431), [SWD-417](https://marcusknielsen.atlassian.net/browse/SWD-417)
- Artifact: `docs/agents/SANDBOX-forecast-oscillation.md`
- Branch: `cursor/swd-432-forecast-oscillation-be46`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/634

## Next
`/review-fix SWD-432` — Review and auto-fix per Workflow binding
