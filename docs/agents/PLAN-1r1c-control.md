# Implementation plan: 1R1C live plant for control

## Summary
- Replace production 2R2C (hidden `Tw`) with 1R1C air-node dynamics for
  PE, CD-Kalman, and receding-horizon MPC (Nonlinear default, Linear
  optional).
- **Control law is already NMPC-only.** Each sample: EKF, planner,
  `u = U*[k]`. There is no P tracker. This Task must not reintroduce
  `u_ref + Kp*(T_ref - Ta)`.
- Drop wall estimation, wall plot series, and PE splits / `Tw0`.
- Refresh **current** control docs so agents stop reading the old
  two-layer NMPC+P map as live spec (`docs/agents/CONTROL.md` is the pointer).

## Scope / Decisions / Constraints
**In**
- `HouseModel` / `Room`: one state per room; `C = thermal_mass`; outdoor
  UA on air; all solar on air; `R_ij` air–air.
- `HouseThermalSDE`: `nx_phys = n`; `hm = Ta + b`; no wall `sigma`.
- PE: `theta` without `c_air_fraction`, `r_aw_fraction`, `Tw0`.
- NMPC/Linear: `x0` without wall; applied input remains `U*[k]`.
- App: remove Wall / Wall Forecast from room and tuning charts.
- **Docs (current-facing):** `docs/agents/CONTROL.md` (canonical loop);
  `docs/ROADMAP.md` and `docs/agents/MODEL-nmpc-p-ff.md` marked
  historical; `docs/THEORY.md` §4 overview matches the live loop; after
  the plant swap, `docs/THEORY.md` §3 and `docs/TUNING.md` EKF row drop
  2R2C wall noise. Do **not** rewrite shipped historical PLAN files as
  if the P tracker never existed — stamp them, do not delete.
- Dual tree, tests, CalVer, changelog.
- Ignore-on-load for old split / wall config keys.

**Out**
- Restoring the two-layer P tracker.
- UKF / MHE / hard `Kw = 0` as the primary fix.
- Re-identifying historical 2R2C datasets as 2R2C.
- Occupancy as a new state.
- Changing NMPC sample interval, look-ahead, accept/reject, watchdog.

**Decisions**
- Class is **rework**: same job (air comfort + price via MPC), new plant.
- User chose 1R1C. Live control is NMPC (or Linear), not NMPC+P.
- Stale two-rate docs caused the mis-spec; fixing that is in this Task.

## Classification
- Class: rework
- Confidence: high
- Why: intentional plant swap; MPC air tracking must still run

## Workflow
- Template: parity-iterative
- Parameters:
  - implement.mode: single
  - implement.verify: comparative
  - implement.iteration: until-bar
  - test.mode: dedicated
  - harden.mode: dedicated
  - review.mode: multiagent
  - review.depth: full
  - review.lasers: sequential
  - side_paths: none
  - sandbox: none
- Chain: architect → implement → test → restructure → review → ship
- Rationale: rework + comparative bar; docs refresh is in-scope so the
  next agent does not revive P-control from ROADMAP

## Inputs
- Model: `docs/agents/MODEL-1r1c.md`
- Control loop: `docs/agents/CONTROL.md` (live); SWD-532 iterate
- Research: `docs/agents/RESEARCH-pe-effectiveness.md`
- Prior: `docs/agents/MODEL-state-estimation.md` superseded

## Pass criteria
- `HouseModel` / SDE physical state length is `n` rooms, not `2n`.
- With constant `Q` and no solar, `Ta` steady state is `Tout + Q * R_ext`
  within 0.05 K.
- CD-Kalman `hm` observes only air (+ offset); no Wall series on the room
  Temperature plot.
- PE result dict has no `t_wall_initial` / split-fraction decisions.
- One-room fixture: NMPC (or Linear) still returns in-bound `U*` and the
  applied command is `U*[k]`, not a `Kp` law. Comparative: no crash;
  replanning still moves `u` when `Ta` is off comfort.
- `docs/agents/CONTROL.md` states the live loop; ROADMAP and MODEL-nmpc-p-ff
  are marked historical; THEORY §4 does not present P-tracking as live.
- Fast unit tests that encoded 2R2C layout are updated and pass.

## Work packages
1. Canonical control docs (`docs/agents/CONTROL.md`, ROADMAP/THEORY/MODEL stamps) +
   1R1C `HouseModel` + `HouseThermalSDE`
2. PE + NMPC `x0`/roll + App wall-plot removal + THEORY §3 / TUNING EKF
   row + tests/CalVer/changelog/App sync

## Open items
- HA `temperature_wall` entities: stop publishing (done in
  `runtime_states`).

## Tracker
- Provider: jira
- Task: [SWD-570](https://marcusknielsen.atlassian.net/browse/SWD-570)
- Relates: [SWD-564](https://marcusknielsen.atlassian.net/browse/SWD-564),
  [SWD-532](https://marcusknielsen.atlassian.net/browse/SWD-532)
- Classification: rework
- Workflow: parity-iterative
- Branch: `cursor/constrained-cdkf-wall-5de1`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/691

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/691
