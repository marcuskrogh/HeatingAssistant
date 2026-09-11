# Implementation plan: NMPC schedule horizon preheat

## Summary
- Comfort and off periods are already projected in `ControlTrajectory`, but the NMPC (and linear) horizon does not use them as anticipatory constraints.
- Off steps currently keep a frost ± default-offset band, so preheating is a cost violation. The live loop also zeros heaters for schedule-off rooms, so an accepted preheat plan cannot run until the comfort window starts.

## Scope / Decisions / Constraints
- In: `compute_control_trajectory` frost floors; `_comfort_bounds_fast` / linear band from enabled steps; `_schedule_control_context` grid + `disabled_sources`; Tuning preview `solve_nmpc(..., control_trajectory=...)`.
- Out: Schedule UI; frost-protection live trip; window-open override (still zeros heaters); experiment input clamps.
- Decision: **off** on the horizon means frost floor only (open upper bound). Heaters stay commandable so NMPC can preheat. Plots may still hide corridors when `enabled` is false.
- Constraint: Trajectory `n_steps` and `dt_seconds` must match the NMPC sample grid (`n_fast`, `dt_s`).

## Classification
- Class: bug
- Confidence: high
- Why: Anticipatory preheat is specified and missing; expected bounds and actuation are known.

## Workflow
- Template: fix-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - test.mode: dedicated
  - harden.mode: dedicated
  - review.mode: single
  - review.depth: focused
  - review.lasers: sequential
  - side_paths: none
  - sandbox: none
- Chain: architect → implement → test → restructure → review → ship
- Rationale: Contained control-path defect; no new layers.

## Inputs
- Research: none
- Model: none
- Sandbox: none

## Pass criteria
- Off-period horizon steps have `t_min = frost_floor` and `t_max` well above comfort (not frost ± offset).
- Upcoming comfort-period steps use that period's setpoint ± comfort offset.
- Schedule-off rooms are not in `disabled_sources` (window override and room `enabled: false` still are).
- NMPC / preview solve receives a trajectory on the same `dt` and `n_fast` as the NLP.
- Spec-lock tests fail if any of the above regress.

## Work packages
1. Engine: off-period bounds + grid + do not zero schedule-off heaters — SWD-546
2. Tests, CalVer, changelog, App sync — SWD-547

## Open items
- none

## Tracker
- Provider: jira
- Story: —
- Task: SWD-545
- Sub-tasks: SWD-546, SWD-547
- Branch: `cursor/nmpc-schedule-horizon-605a` (SWD-545)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/683
- Classification: bug
- Workflow: fix-fast

## Next
`/test SWD-545` — Dedicated testing phase on the bound chain (same branch/PR)
