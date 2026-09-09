# Iterate: Tuning mode cards, Apply gate, and solver knobs

## Prior work
- Task: SWD-522
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/672
- Spec context: docs/agents/PLAN-mpc-mode-choice.md

## Problem
- Mode cards read as one long title and a faint active border, so Linear vs
  Nonlinear and which planner is live are easy to miss.
- Clicking a card looks like the live solver changed; the operator should
  Apply Changes before the running planner switches.
- Nonlinear timing still exposes Fast substeps, which is an internal grid
  count rather than an operator setting.

## Clarifications
- Sample interval stays the operator knob for how often the held nonlinear
  plan is applied. Fast substeps is derived (plan period / sample interval)
  and is not shown as a labelled field.
- Preview may use the drafted mode; the live controller changes only on Apply.

## Acceptance criteria
- Each card shows **Linear** or **Nonlinear**, then **model predictive
  control**, then a short effect description.
- In-use vs drafted (not yet applied) is visually distinct; Apply Changes
  is required to switch the live solver.
- Shared live weights and per-mode timing use operator-facing labels and
  hints. Nonlinear Fast substeps is not a labelled Tuning field.
- Tests, CalVer, changelog, App package sync.

## Out of scope
- Changing the NMPC two-rate engine contract or config key names.
- Restoring the two-layer P tracker or Regulator Load KPI.

## Work packages
1. Mode cards and Apply-to-switch (SWD-527).
2. Solver knobs, copy, tests, CalVer (SWD-528).

## Tracker
- Task: SWD-526
- Relates: SWD-522
- Sub-tasks: SWD-527, SWD-528
- Branch: `cursor/swd-526-tuning-mode-cards-58e6`

## Next
`/review-fix SWD-526` — Review and auto-fix on the new delivery PR
