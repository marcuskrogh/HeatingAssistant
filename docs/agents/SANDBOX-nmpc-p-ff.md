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
  (`--only "2 h" --maxiter 80 --tag 03 --analytic --timeout 300` for iteration 3)
- Inspectables: `sandbox/nmpc-p-ff/inspect/`

## Representativeness
- Relevant areas:
  - **Runtime** — SciPy NLP in the App process (not Home Assistant Core).
    Wall-clock on this Cloud VM is a proxy for App-host CPU, not a HAOS
    device.
  - **Data** — 36 h look-ahead of outdoor temperature, solar, and
    electricity price at 15 min; comfort zone; one heater per room;
    heat/cool `φ(u)` maps.
  - **Neighbours** — implicit Euler of `HouseThermalSDE.f`; inter-room
    `R_ij` in one house-level OCP.
  - **Path** — wrap production `HouseThermalSDE` + `implicit_euler_substeps`
    and production `HeatingLinearisedMPC` as baseline.
  - **Baseline** — current `HeatingLinearisedMPC`.
- How reproduced: synthetic two-room heat-pump house; SciPy SLSQP;
  production implicit Euler; QP at N=100 and N=144.
- Gaps:
  - **Live recorded household traces** — waived for the solve-time verdict.
  - **HAOS App CPU vs this VM** — absolute seconds may not transfer.
  - **Closed-loop EKF+P** — not measured yet.
  - **SciPy flavour** — SLSQP; analytic `dJ/dU` via production `dfdx`/`dfdu`
    chained through implicit Euler (iteration 3).

## Bar
- Metrics: solve-time cold/warm, nfev, success vs maxiter.
- Iteration 2 (operator: 2 h, avoid maxiter):
  - QP N=144: 0.72 s
  - 2 h NMPC cold: **94 s**, 47 iterations, **success** (cap was 80)
  - 2 h NMPC warm: 162 s, hit maxiter 80 while polishing (J 0.83 → 0.68)
- Iteration 3 (analytic Jacobian, 2 h, maxiter 80):
  - Analytic vs FD: max relative error **1.3e-5**
  - Cold: **22 s**, 80 iters, J=0.81 (hit cap but already in-band)
  - Warm: **7.7 s**, 26 iters, **success**
  - Finite-difference cold was 94 s; analytic is about 4× faster and
    warm-start works.

## App concurrency (production wiring)
Today `run_control_cycle` calls `compute_actions` **inline** on whatever
thread owns the cycle:

- MQTT tag path and HTTP config run it on the **App asyncio loop**
  (`heatingassistant/app/runtime.py`). A 90 s NLP there would stall
  Ingress and MQTT callbacks for that duration.
- The wall-clock ticker is a **separate daemon thread**, but it is not
  the only caller.
- Home Assistant Core is a **different process** — NMPC in the App does
  not freeze Core (`SWD-254`).

Parameter estimation already offloads with `asyncio.to_thread` /
`run_in_executor`. NMPC should do the same: worker thread for the NLP;
EKF then P every 15 min on the ticker using the last path; hold that
path until the worker returns (or fail/timeout rules).

A worker thread is parallel with Ingress/MQTT. It still shares the
Python process (GIL); it is not a second OS process. That is enough to
keep the App responsive. A process pool is optional later.

## Promote map
- Production targets (after accept → `/define SWD-395`):
  - Default `T_NMPC` = 2 h (`N = 18` at `T_H` = 36 h).
  - NLP on a worker thread; do not call it inline from `run_control_cycle`.
  - Fast loop: EKF then P every `T_s` = 15 min from the last path.
  - Analytic `dJ/dU` from production `dfdx`/`dfdu` through implicit Euler.
    Do not ship finite-difference Jacobians as the happy path.
  - `heatingassistant/engine/controller/facade.py`,
    `heatingassistant/engine/control_loop.py`, heater `K_p`, App timeout
    = fail, 5 h → `u = 0` + notify.
- Copy notes: promote NLP + P law + executor wiring; do not copy the
  isolation harness.

## Iterations
| N | Change | Inspectable | Verdict |
|---|--------|-------------|---------|
| 1 | SLSQP single shooting, all three brackets | sandbox/nmpc-p-ff/inspect/01_report.md | delta: operator wants 2 h (dislikes maxiter on 1 h) |
| 2 | lock 2 h; re-time with maxiter 80 | sandbox/nmpc-p-ff/inspect/02_report.md | delta: supply analytic derivatives |
| 3 | analytic dJ/dU via dfdx/dfdu | sandbox/nmpc-p-ff/inspect/03_report.md | **accept** — 2 h, analytic Jacobian, worker thread |

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
`/implement SWD-395` — build per `docs/agents/PLAN-nmpc-p-ff.md`.
