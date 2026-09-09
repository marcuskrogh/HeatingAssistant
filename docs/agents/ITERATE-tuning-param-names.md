# Iterate: Align Linear and Nonlinear Tuning parameter names

## Prior work
- Task: SWD-526
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/673 (`257d7c4b`)
- Spec context: docs/agents/ITERATE-tuning-mode-cards.md

## Problem
- Linear timing says **Prediction horizon** in steps while Nonlinear says
  **Look-ahead** in hours, for the same idea: how far the plan covers.
- Sample interval is already shared in spirit, but field order and hints
  read as two different vocabularies.

## Clarifications
- Shared cost weights keep their current shared labels.
- Linear-only QP terms stay Linear-only (they are not identical on Nonlinear).
- Nonlinear **Plan period** stays — it has no Linear counterpart (Linear
  solves every sample interval).
- Linear look-ahead is shown in hours; stored `horizon` stays step count.

## Acceptance criteria
- Both planners show **Sample interval** (s) and **Look-ahead** (h) with the
  same labels.
- Linear does not show **Prediction horizon** or a step-count horizon field.
- Nonlinear still shows **Plan period** as the extra solve cadence.
- Tests, TUNING.md, CalVer, changelog, App package sync.

## Out of scope
- Changing NMPC or linear engine config keys.
- Restoring Fast substeps as a labelled field.

## Work packages
1. Unify Timing labels (SWD-530).
2. Tests, docs, CalVer (SWD-531).

## Tracker
- Task: SWD-529
- Relates: SWD-526
- Sub-tasks: SWD-530, SWD-531

- Branch: `cursor/swd-529-tuning-param-names-58e6`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/675

## Next
`/review-fix SWD-529` — Review and auto-fix (single pass)
