# Implementation plan: 1R1C live plant for control

## Summary
- Replace production 2R2C (hidden `Tw`) with 1R1C air-node dynamics for
  PE, CD-Kalman, NMPC, and P.
- Drop wall estimation, wall plot series, and PE splits / `Tw0`.
- Keep heater map, emitter `phi`, slow bias `b`, solar scale, `R_ij` as
  air–air.

## Scope / Decisions / Constraints
**In**
- `HouseModel` / `Room`: one state per room; `C = thermal_mass`; outdoor
  UA on air (infiltration + sky + bridge); all solar and facade share on
  air; connections on air.
- `HouseThermalSDE`: `nx_phys = n`; `hm = Ta + b`; no wall `sigma` row.
- PE: `theta` without `c_air_fraction`, `r_aw_fraction`, `Tw0`.
- NMPC roll and `x0` without wall block; P-law unchanged.
- App: remove Wall / Wall Forecast from room and tuning charts; wall
  entity may stay unused or stop being published.
- Dual tree, tests, CalVer, changelog.
- Ignore-on-load for old split / wall config keys (slab-era pattern).

**Out**
- UKF / MHE / `Kw = 0` as the primary fix (obsolete once `Tw` is gone).
- Re-identifying historical 2R2C datasets as 2R2C.
- Occupancy as a new state.
- Changing NMPC timing triple, `Kp`, accept/reject, watchdog.

**Decisions**
- Class is **rework**: same control job (air comfort + price), new plant
  implementation. Parity is closed-loop air tracking feasibility, not
  2R2C wall RMSE.
- User chose option A (1R1C + `phi` + bias). Simpler over physically
  richer.

## Classification
- Class: rework
- Confidence: high
- Why: intentional plant swap for control; comfort/`T_ref` tracking must not collapse

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
- Rationale: rework + comparative bar; blast radius is the whole control
  stack so review is full/multiagent; model already written this Task

## Inputs
- Model: `docs/agents/MODEL-1r1c.md`
- Research: `docs/agents/RESEARCH-pe-effectiveness.md` (hidden `Tw` is
  why PE is poor; not a reason to keep 2R2C for control)
- Prior: `docs/agents/MODEL-state-estimation.md` superseded

## Pass criteria
- `HouseModel` / SDE physical state length is `n` rooms, not `2n`.
- With constant `Q` and no solar, `Ta` steady state is `Tout + Q * R_ext`
  within 0.05 K (existing 1R1C invariant).
- CD-Kalman `hm` observes only air (+ offset); no wall series in EKF
  visualisation payload used by the room Temperature plot.
- PE result dict has no `t_wall_initial` / split-fraction decisions;
  a fit on a single-room heater step still returns finite `C`, `R`.
- Existing NMPC+P path still produces in-bound `u` from `Ta_hat` vs
  `T_ref` on a one-room fixture (comparative: no crash; P still reduces
  `|T_ref - Ta|` vs `Kp = 0` on a step).
- Fast unit tests that encoded 2R2C layout are updated and pass.

## Work packages
1. 1R1C `HouseModel` + `HouseThermalSDE` (SWD-570 core)
2. PE + NMPC/P + App wall-plot removal + tests/CalVer/changelog/App sync

## Open items
- Whether HA still registers `temperature_wall` entities (prefer stop
  publishing; leave orphaned sensors harmless).

## Tracker
- Provider: jira
- Task: [SWD-570](https://marcusknielsen.atlassian.net/browse/SWD-570)
- Relates: [SWD-564](https://marcusknielsen.atlassian.net/browse/SWD-564)
- Classification: rework
- Workflow: parity-iterative
- Branch: `cursor/constrained-cdkf-wall-5de1`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/691

## Next
`/implement SWD-570` — Build the 1R1C plant to ARCHITECTURE.md
