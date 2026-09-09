# Iterate: Nonlinear MPC on the same sample grid as Linear

## Prior work
- Task: SWD-522 / SWD-526
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/672 (`4cc5a506`),
  https://github.com/marcuskrogh/HeatingAssistant/pull/673 (`257d7c4b`)
- Spec context: docs/agents/PLAN-mpc-mode-choice.md,
  docs/agents/ITERATE-tuning-mode-cards.md

## Problem
- After the inner tracking loop was removed, Nonlinear still used a coarser
  NLP grid (Plan period, zero-order hold of `U*` for several samples).
- That two-rate hold is not a nonlinear equivalent of Linear MPC.

## Clarifications
- Nonlinear is the same receding-horizon problem as Linear except nonlinear
  dynamics and NLP vs QP (and compute cost).
- Linear-only QP cost terms stay Linear-only.
- Last command may still be held while an NLP is computing.
- On a 15 min sample grid, Nonlinear solves in about 30–60 s and Linear in
  about 1 s. A ~60 s NLP is acceptable (inside the sample; hold only for
  that overrun).

## Acceptance criteria
- Both modes use Sample interval + Look-ahead (hours). No Plan period field.
- Persist `update_interval` = sample; `horizon` = look-ahead × 3600 / dt;
  `nmpc_period` = dt; `nmpc_fast_substeps` = 1; `nmpc_horizon_h` = look-ahead.
- Stored two-rate triples coerce to one decision per sample, keeping dt and
  look-ahead hours.
- NMPC is due every sample.
- Tests, TUNING.md, CalVer, changelog, App package sync.

## Out of scope
- Unifying Linear-only QP costs into the Nonlinear objective.
- Restoring the P/PID tracker.
- Merging SWD-529 / PR #675 (naming-only; superseded).

## Work packages
1. Single-rate NMPC + shared Timing UI (SWD-533).
2. Tests, docs, CalVer (SWD-534).

## Tracker
- Task: SWD-532
- Relates: SWD-522, SWD-526
- Sub-tasks: SWD-533, SWD-534

- Branch: `cursor/swd-532-nmpc-equiv-lmpc-58e6`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/676

## Next
`/ship SWD-532` — Closeout after CI (review-fix CLEAN)
