# Iterate: Finish 1R1C surfaces after SWD-570

## Prior work
- Task: SWD-570
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/691
- Spec context: `docs/agents/PLAN-1r1c-control.md`, `docs/agents/MODEL-1r1c.md`

## Problem
SWD-570 swapped the live plant (HouseModel, SDE, PE θ) to 1R1C air nodes.
Leftover 2R2C surfaces remain on Identification, room charts, config, and
simulation ICs:

- Identification still shows Envelope Split (2R2C) and Tw0.
- Room Temperature chart still has Wall / Wall Forecast code; history still
  fetches `temperature_wall`.
- Identification EKF/open-loop charts still draw Predicted (wall).
- Open-loop / reconstruction can write Tw0 into `x[n:]`, which on 1R1C is
  emitter lag, not a wall.

Live θ is already C, R, q_int, solar scale, heater α, R_ij, ua_open.

## Clarifications
- User asked to verify the whole toolbox and update what was missed.
- Do not restore a P tracker or a hidden wall state.

## Acceptance criteria
- Identification Model Parameters: C, R, Q_int, solar scale, ua_open. No
  2R2C split or Tw0 fields.
- Room view Temperature plot: air (filtered/measured) + forecast only; no
  Wall / Wall Forecast series.
- Identification reconstruction and open-loop plots: air only.
- Wall IC is not written into 1R1C states (`nx_phys == n`).
- Config room editor does not expose unused air-mass / film fraction.

## Out of scope
- Restoring P control.
- Re-fitting historical 2R2C datasets as 2R2C.
- Deleting every unused `c_air_fraction` attribute on Room (ignore-on-load
  stays).

## Work packages
1. Strip 2R2C PE UI, plots, config fields
2. Guard wall IC on 1R1C; tests; dual-tree sync

## Tracker
- Task: SWD-572
- Relates: SWD-570

## Next
`/review-fix SWD-572` — leftover 1R1C surfaces implemented; review the delivery PR
