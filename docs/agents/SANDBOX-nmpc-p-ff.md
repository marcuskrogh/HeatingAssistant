# Sandbox: Offline NMPC period + closed-loop P

## Element
Two-rate controller on the production 2R2C house plant: slow nonlinear
optimal control problem (OCP) on the mean ODE, plus fast proportional (P)
tracking with feedforward. Measure whether a feasible slow period
`T_NMPC` exists, and whether the pair beats today’s linearised quadratic
program (QP).

## Kind
measure

## Isolation
- Path: `sandbox/nmpc-p-ff/`
- Harness: not created — inspect-loop blocked until representativeness
  is complete or the named gaps are waived
- Inspectables: `sandbox/nmpc-p-ff/inspect/` (after iteration 1)

## Representativeness
- Relevant areas:
  - **Runtime** — SciPy nonlinear program (NLP) in the App process (not
    Home Assistant Core). Wall-clock solve time on this Cloud VM is a
    proxy for App-host CPU, not a HAOS device.
  - **Data** — 36 h look-ahead of outdoor temperature, solar, and
    electricity price at 15 min; comfort zone; one heater per room;
    heating-only and heat/cool `φ(u)` maps. Live occupancy, openings, and
    recorded household traces are production data the OCP/P will see.
  - **Neighbours** — continuous-discrete extended Kalman filter (CD-EKF)
    predict with the applied P command, then P on `T_hat`; implicit Euler
    of `HouseThermalSDE.f` (same family as the nonlinear forecast);
    inter-room `R_ij` in one house-level OCP.
  - **Path** — wrap production `HouseThermalSDE` + `implicit_euler_substeps`
    and production `HeatingLinearisedMPC` as baseline. Candidate NLP
    lives only under `sandbox/nmpc-p-ff/` (no production edits).
  - **Baseline** — current `HeatingLinearisedMPC` (setpoint-equilibrium
    linearisation + condensed QP), same plant, same disturbances, same
    cost purpose (soft zone, rate-of-move, energy-price).
- How reproduced (proposed, not yet run):
  - **Runtime** — in-process SciPy on this VM; record wall-clock per
    solve (cold and warm-start).
  - **Data** — synthetic two-room 2R2C via production `HouseModel` /
    `HouseThermalSDE`, one heater per room (at least one heat-pump map so
    the known QP failure mode can appear), 36 h outdoor/solar/price of
    household shape (same family as the PE household traces).
  - **Neighbours** — production CD-EKF + P at `T_s` = 15 min; production
    implicit Euler; two rooms with `R_ij` so the OCP is house-level.
  - **Path** — import production modules; candidate single-shooting NLP
    only in the isolation tree.
  - **Baseline** — call `HeatingLinearisedMPC.step` on the same scenario.
- Gaps:
  - **Live recorded household traces** (occupancy, openings, real
    weather/price) — not in iteration 1 unless required. This can move
    closed-loop comfort/cost; it is less likely to move the 15 min vs 1 h
    vs 2 h **solve-time** ranking.
  - **HAOS App CPU vs this VM** — absolute timeout seconds may not
    transfer; relative ranking of `T_NMPC` brackets should.

## Bar
- Metrics:
  - Solve-time reliability: median and p95 wall-clock, timeout/fail
    rate, warm-start vs cold, at `T_NMPC` ∈ {15 min, 1 h, 2 h}.
  - Closed-loop: comfort-band violation (K·h and peak °C), electrical
    energy cost, and whether the QP still commands max heat while the
    nonlinear rollout overheats.
- Scenario: the representative map above (not a stripped 1R1C or a toy
  QP). `T_s` = 15 min, `T_H` = 36 h.
- Tolerances (first cut, not product UX): a bracket **fails** if p95
  solve time is not well below that `T_NMPC` (no headroom for the next
  slow instant), or if closed-loop comfort/cost is worse than the QP
  without fixing the overheat failure. Exact timeout seconds and `K_p`
  stay sandbox choices after iteration 1.

## Promote map
- Production targets (after accept → `/define SWD-395`):
  - `heatingassistant/engine/controller/facade.py` — drop linearised QP
    from the happy path; NMPC path + nonlinear `T_ref` rollout.
  - `heatingassistant/engine/control_loop.py` — two-rate schedule
    (EKF then P every `T_s`; OCP every `T_NMPC`).
  - Heater config — one `K_p` (fraction/K).
  - App process — SciPy NLP with timeout = fail; last-path hold; 5 h →
    `u = 0` + persistent notification.
- Copy notes: promote accepted NLP + P law; do not copy the isolation
  harness. Linearised QP stays out of the happy path.

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| — | representativeness map only; inspect-loop not started | — | waiting: live-trace gap |

## Role in pipeline
Promotion input for `/define SWD-395` then `/implement`. Supportive
isolation — not production source. No sandbox PR.

## Tracker
- Task: [SWD-394](https://marcusknielsen.atlassian.net/browse/SWD-394)
- Relates: —
- Artifact: `docs/agents/SANDBOX-nmpc-p-ff.md`
- Branch: `cursor/swd-395-nmpc-p-tracker-46be`
- PR: — (sandbox never opens a PR)

## Next
`/sandbox SWD-394` — after the live-trace gap is waived or required; then
build the harness and run iteration 1.
