# Implementation plan: Show MPC Load above 100% on overrun

## Summary
- Overview MPC Load clamps the displayed percentage at 100% even when the
  last planner solve exceeds 10% of the sample interval.
- Live example: last solve 108.80 s, load budget 90 s, headline and detail
  LOAD both show 100%. True load is (108.80 / 90) * 100 ≈ 121%.
- Remove the display cap. Gauge bar fill may stay full when over 100%.

## Scope / Decisions / Constraints
**In**
- `mpcLoadPercent` in `heatingassistant/app/static/js/kpi-engine.js`.
- Overview headline, expand-detail LOAD row, System Status MPC load.
- Tests and App package sync.

**Out**
- Load budget formula (still 10% of the sample interval).
- Gauge bar geometry (fill still saturates at the 0–100 scale).
- Planner timeouts, NMPC max compute, or health severity thresholds.
- Restoring a P tracker.

**Decisions**
- Class is **tweak**: intentional display change; computation of duration /
  budget is unchanged.
- Displayed percent is `duration / budget * 100` with no `Math.min(100, …)`.
- Gauge `max` stays 100 so the bar is full at and above 100%; `format` still
  receives the unclamped value.

**Constraints**
- Dual tree: edit `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.
- Product copy must not include tracker keys.

## Classification
- Class: tweak
- Confidence: high
- Why: one-line display formula; bar fill and budget stay as they are

## Workflow
- Template: delta-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none
- Chain: implement → review-fix → ship
- Rationale: localized KPI math; existing harness plus a source lock is enough

## Inputs
- Research: none
- Model: none

## Acceptance criteria
- 108.8 s last solve and 90 s budget display about 121%, not 100%, on the
  Overview headline and the expand-detail LOAD row.
- Over-budget values (e.g. 800 s vs 720 s budget) display above 100%.
- Under-budget values are unchanged (e.g. 24.7 s of 720 s ≈ 3%).
- Gauge bar does not grow past full width when load exceeds 100%.
- Linear and Nonlinear still share this card.

## Work packages
1. Uncap `mpcLoadPercent`, lock tests, dual-tree sync

## Open items
- none

## Tracker
- Provider: jira
- Story: —
- Task: SWD-571
- Sub-tasks: —
- Branch: cursor/mpc-load-uncap-5de1
- PR: (draft after first push)
- Classification: tweak
- Workflow: delta-fast

## Next
`/review-fix SWD-571` — implement complete; focused review on the uncap
