# Iterate: Mode-general Next Compute and MPC Load KPIs

## Prior work
- Task: SWD-532
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/676 (`b07c39d8`)
- Spec context: docs/agents/ITERATE-nmpc-equiv-lmpc.md

## Problem
- Overview and room KPIs still show Next Control and Next NMPC as two rings.
- That split matched the old two-rate grid. Linear and Nonlinear now share one
  sample interval, so the pair is misleading.
- NMPC Load is Nonlinear-only; System Status still has a separate NMPC
  interval and a 2 s P-tracker load budget.

## Clarifications
- One Next Compute ring. Computing overlay if the planner or the apply cycle
  is busy.
- MPC Load is last planner solve versus 10% of the sample interval.
- Linear uses the control-cycle duration; Nonlinear uses the NLP duration.

## Acceptance criteria
- Overview and room KPI grids have one Next Compute countdown.
- MPC Load gauge and expand copy are mode-general.
- System Status MPC card uses sample interval + planner load (no extra NMPC
  interval row, no 2 s regulator budget).
- Tests, CalVer, changelog, App package sync.

## Out of scope
- Tuning cards or solver math.
- Restoring a second timing ring.

## Work packages
1. Unify Next Compute ring and MPC Load KPIs (SWD-537).
2. Tests, CalVer, changelog, App sync (SWD-538).

## Tracker
- Task: SWD-536
- Relates: SWD-532
- Sub-tasks: SWD-537, SWD-538
- Branch: `cursor/swd-536-kpi-mpc-general-58e6`

## Next
`/review-fix SWD-536` — Review and auto-fix (single pass)
