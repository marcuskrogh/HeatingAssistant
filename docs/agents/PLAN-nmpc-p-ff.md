# Implementation plan: Production NMPC + P tracker

## Summary
- Replace the linearised quadratic program (QP) in the happy path with a
  two-rate controller: slow nonlinear model predictive control (NMPC) plus
  fast proportional (P) tracking with feedforward.
- Defaults: slow NMPC every **2 h**, **8** fast substeps per slow interval
  (EKF then P every **15 min**, derived), look-ahead **36 h**.
- SciPy SLSQP with an analytic cost Jacobian. NLP on a worker thread.
  Robust accept/reject of the plan. Five hours of consecutive rejects
  turn heaters off and raise a persistent Home Assistant notification.

## Scope / Decisions / Constraints
**In**
- Production two-rate law on `HouseThermalSDE` (certainty-equivalent mean
  ODE in the OCP; process noise stays EKF-only).
- Slow OCP: single shooting in `U`; discrete map `F` = implicit Euler of
  `f` (same family as the nonlinear forecast); analytic `dJ/dU` from
  production `dfdx` / `dfdu`. Finite-difference Jacobians are not the
  happy path.
- Cost: same purpose as today’s QP — soft comfort **zone**, input
  rate-of-move (ROM), electricity-price term. No extra setpoint pull.
  ROM `u_{-1}` is the last **applied** P command.
- Fast law every `T_s`: EKF predict with the actual P command, then
  `u = clip(u_ref + K_p (T_ref − T_hat_a), u_min, u_max)`.
  `u_ref` is zero-order hold of `u*_n`. `T_ref` is the mean air path
  from rolling `F` under that `u*` (not linear interpolation).
- Timing is a **substepping triple**, not three independent integers:
  `nmpc_period` (slow cadence, default 7200 s), `nmpc_fast_substeps`
  (fast ticks per slow interval, default **8**), `nmpc_horizon_h`
  (look-ahead, default **36**). Derive `T_s = nmpc_period /
  nmpc_fast_substeps` (900 s) and slow steps `N = nmpc_horizon_h /
  (nmpc_period / 3600)` (18). Reject or snap config that does not
  divide exactly. `update_interval` and step-count `horizon` become
  derived (EKF/P/history still use `T_s`). Tuning shows the triple;
  sample interval is read-only. Implicit-Euler `n_int_steps` inside
  each fast tick stays the existing integrator setting (not this M).
- Default `K_p` = **0.1 per kelvin** on each heater (configurable;
  operator notes this may be slow). One gain for heat and cool.
- NLP: SciPy SLSQP in the App process (not Home Assistant Core).
  maxiter 200, wall-clock timeout 60 s. Warm-start from last accepted
  `U*` when it exists.
- **Accept** `U*` when every entry is in bounds, `J` is finite, and `J`
  is in-band versus the same problem at `u = 0` (`J < 1e-3 * J(u=0)`).
  Covers SciPy success, iteration cap, and timeout **if** a usable
  vector was returned.
- **Reject** otherwise (NaN, out of bounds, `J` still near the zero-heat
  cost). Keep the last accepted path. No path (startup or after
  watchdog) → `u = 0`. EKF still runs.
- Five hours of **consecutive rejects** (timeouts included) → every
  heater `u = 0` plus a persistent Home Assistant notification via the
  thin MQTT bridge. An accepted NLP resets the timer and dismisses the
  notice. Later accepted plans resume P.
- Worker thread (`asyncio.to_thread` / executor, same idea as parameter
  estimation). Do not call the NLP inline from `run_control_cycle`.
  Fast EKF+P on the derived `T_s` ticker uses the last accepted path
  while the worker runs.
- Forecast / heating schedule in the App comes from the nonlinear
  rollout under the accepted plan, not from the linearised QP.
- Tuning: NMPC period, fast substeps, look-ahead hours. Heater editor:
  P gain field.
- Dual tree: edit `heatingassistant/`, then
  `scripts/sync-ha-app-package.sh`. CalVer + changelog.

**Out**
- Linearised QP as a runtime fallback.
- Copying `sandbox/nmpc-p-ff/` into production.
- I-term or D-term on the fast loop.
- Split-range / several independent laws in one room (OCP still has one
  `u` per configured source, as today).
- CasADi / IPOPT.
- NMPC on the Home Assistant Core event loop.
- Closed-loop comfort bake-off vs the old QP (sandbox waived; not a
  ship bar).
- Changing parameter estimation.

**Decisions**
- Class is a **feature** (new happy-path control law), not a rework:
  we are not holding parity with the linearised QP.
- `K_p` default 0.1 /K ships; users can raise it.
- Timing defaults: 2 h slow, 8 fast substeps, 36 h look-ahead (`T_s`
  derived). Old independent `update_interval` / `horizon` (steps) are
  not the primary NMPC knobs.
- In-band bar uses `J(u=0)` so false SciPy “success” at zero heat is
  rejected.

**Constraints**
- `SWD-254`: compute stays in the App process.
- Integer grid by construction: `T_s` and `N` are derived from the
  triple.
- Worker shares the process (GIL); not a second OS process.

## Classification
- Class: feature
- Confidence: high
- Why: new two-rate product control path replacing the linearised QP;
  scope, fail behaviour, and timing were aligned in this define session

## Workflow
- Template: feature-standard
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none
  - sandbox: none
- Chain: implement → review-fix → ship
- Rationale: model and sandbox already landed on this branch; one
  control-path slice with unit tests. Efficiency-first: not
  feature-heavy / multiagent.

## Inputs
- Model: `docs/agents/MODEL-nmpc-p-ff.md`
- Sandbox: `docs/agents/SANDBOX-nmpc-p-ff.md` (accepted: 2 h, analytic
  Jacobian, worker thread; inspect `sandbox/nmpc-p-ff/inspect/03_report.md`)
- Map: `docs/ROADMAP.md`

## Acceptance criteria
1. `compute_actions` / `HeatingMPCController.compute` does not solve a
   linearised QP on the happy path.
2. Fast loop every derived `T_s` (default 15 min = 2 h / 8): EKF then P
   with `u_ref` and `T_ref` from the last accepted slow plan. Default
   `K_p` = 0.1 /K.
3. Slow OCP default period 2 h, look-ahead 36 h, analytic Jacobian,
   SLSQP. Worker thread; Ingress/MQTT stay live during a solve.
4. Accept/reject as specified; last path held on reject; no path →
   `u = 0`.
5. Five hours of consecutive rejects → all heaters `u = 0` and a
   persistent notification; success resets that state.
6. Tuning exposes `nmpc_period`, `nmpc_fast_substeps`, `nmpc_horizon_h`
   with defaults 2 h / 8 / 36 h. `T_s` is derived. Non-dividing values
   are rejected.
7. Tests cover P-law clip, accept vs zero-heat reject, timeout/reject,
   watchdog elapsed time, and worker off the asyncio loop. CalVer and
   App package synced.

## Work packages
1. Mean OCP + analytic Jacobian + accept/reject (replace QP path) — [SWD-396](https://marcusknielsen.atlassian.net/browse/SWD-396)
2. Fast P + heater `K_p`; EKF uses applied `u` — [SWD-397](https://marcusknielsen.atlassian.net/browse/SWD-397)
3. NLP worker thread + 5 h fail watchdog + notify — [SWD-398](https://marcusknielsen.atlassian.net/browse/SWD-398)
4. Timing triple (2 h / 8 substeps / 36 h), Tuning UI, tests, CalVer, App sync — [SWD-399](https://marcusknielsen.atlassian.net/browse/SWD-399)

## Open items
- Persistent-notification copy: "Heating plan unavailable" / five hours without a usable plan.
- MQTT `heatingassistant/<instance>/cmd/notify` with `action` `create` or `dismiss` is implemented.

## Tracker
- Provider: jira
- Story: [SWD-392](https://marcusknielsen.atlassian.net/browse/SWD-392)
- Task: [SWD-395](https://marcusknielsen.atlassian.net/browse/SWD-395)
- Sub-tasks: [SWD-396](https://marcusknielsen.atlassian.net/browse/SWD-396),
  [SWD-397](https://marcusknielsen.atlassian.net/browse/SWD-397),
  [SWD-398](https://marcusknielsen.atlassian.net/browse/SWD-398),
  [SWD-399](https://marcusknielsen.atlassian.net/browse/SWD-399)
- Branch: `cursor/swd-395-nmpc-p-tracker-46be`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/622
- Classification: feature
- Workflow: feature-standard

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/622
