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
- Harness: `python3 sandbox/nmpc-p-ff/harness.py`
- Inspectables: `sandbox/nmpc-p-ff/inspect/`

## Representativeness
- Relevant areas:
  - **Runtime** — SciPy NLP in the App process (not Home Assistant Core).
    Wall-clock on this Cloud VM is a proxy for App-host CPU, not a HAOS
    device.
  - **Data** — 36 h look-ahead of outdoor temperature, solar, and
    electricity price at 15 min; comfort zone; one heater per room;
    heat/cool `φ(u)` maps.
  - **Neighbours** — implicit Euler of `HouseThermalSDE.f` (same family
    as the nonlinear forecast); inter-room `R_ij` in one house-level OCP.
  - **Path** — wrap production `HouseThermalSDE` + `implicit_euler_substeps`
    and production `HeatingLinearisedMPC` as baseline. Candidate NLP
    lives only under `sandbox/nmpc-p-ff/`.
  - **Baseline** — current `HeatingLinearisedMPC` (setpoint-equilibrium
    linearisation + condensed QP), same plant, same disturbances, same
    cost purpose (soft zone, rate-of-move, energy-price).
- How reproduced:
  - **Runtime** — in-process SciPy SLSQP on this VM; cold and warm-start
    wall-clock.
  - **Data** — synthetic two-room 2R2C via production `HouseModel` /
    `HouseThermalSDE`, one heat pump per room (`heat_cool`), 36 h
    outdoor/solar/price of household shape.
  - **Neighbours** — production implicit Euler, 10 sub-steps per 15 min;
    two rooms with `R_ij`.
  - **Path** — import production modules; single-shooting NLP only in
    the isolation tree.
  - **Baseline** — `HeatingMPCController.compute` (linearised QP) at
    N=100 and N=144.
- Gaps:
  - **Live recorded household traces** — **waived** for the approximate
    solve-time verdict (operator, 2026-08-19).
  - **HAOS App CPU vs this VM** — absolute timeout seconds may not
    transfer; relative ranking of `T_NMPC` brackets should.
  - **Closed-loop EKF+P** — not in iteration 1 (solve-time only).
  - **SciPy flavour** — SLSQP only this iteration; finite-difference
    Jacobian is brittle (false success at u=0 for some eps). trust-constr
    and analytic Jacobians are a possible delta.

## Bar
- Metrics:
  - Solve-time reliability: wall-clock cold/warm, nfev, success vs
    maxiter, at `T_NMPC` ∈ {15 min, 1 h, 2 h}.
  - Closed-loop comfort/cost vs QP: **not measured in iteration 1**.
- Scenario: synthetic two-room heat-pump house, `T_s` = 15 min,
  `T_H` = 36 h, production `f` + implicit Euler.
- Iteration 1 reading (solves that actually cut J from ~3.5e6 to ~1):
  - QP N=144: 0.7 s
  - 15 min NMPC: 112 s cold / 56 s warm (8 iters, success)
  - 1 h NMPC: 77 s cold / 78 s warm (20 iters, still improving)
  - 2 h NMPC: 24 s cold / 5.7 s warm (13 iters, success)
  - All three sit well below their own period on this VM. 15 min NMPC
    uses a large fraction of a 15 min ticker.

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
| 1 | initial extract: SLSQP single shooting, solve-time only; live traces waived | sandbox/nmpc-p-ff/inspect/01_report.md | waiting: accept / delta / sandbox-only end |

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
`/sandbox SWD-394` — after operator accept, named delta, or sandbox-only end.
