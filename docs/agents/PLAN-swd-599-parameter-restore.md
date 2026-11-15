# Implementation plan: restored parameters reach the planner

## Summary
- Stored identified parameters were applied to the house model after the controller was built, and nothing copied them into the estimator or the planner.
- The controller is now built from the restored model, and a later restore refreshes the copied matrices without replacing the estimator state.
- The Living Room November fit replaces an active 26 Sep snapshot. The 26 Sep snapshot stays in history.

## Scope / Decisions / Constraints
- In: propagation of restored or changed parameters; Living Room fit from 8–12 Nov 2026; keep the 26 Sep set revertable; the two isolated tests.
- Out: estimator time-advance, a bias state, retuning, Heating Lab, a release, and any contact with the live house.
- The heat pump `min_power` field does not change thermal output, so the minimum output is `internal_gain`.
- Version stays 2026.11.2. This pull request is not a release.

## Classification
- Class: bug
- Confidence: high
- Why: the planner was running on room-config defaults because the restore happened after the controller copy. The correct behaviour is that the estimator and planner use the restored parameters.

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
  - review.lasers: bundled
  - side_paths: none
  - sandbox: none
- Chain: architect → implement → test → restructure → review → ship
- Rationale: one known-wrong path and a contained parameter snapshot. Ship waits for review. This pull request is not merged from this task.

## Inputs
- Research: —
- Model: —
- Sandbox: —

## Pass criteria
- After restore, estimator and planner capacitance and drift match the house model, including the November heat loss of 110.12 W/K.
- The 13 Nov replay root-mean-square error is at most 1.0 K, and the night rate is within 0.1 K/h of the measured rate.
- Refreshing parameters does not replace the estimator state.
- An active 26 Sep Living Room snapshot is replaced by the November fit, and history index 0 restores the 26 Sep capacity to the planner.
- Fast suite passes. No release.

## Work packages
1. Refresh estimator and planner matrices when parameters are restored.
2. Apply the November Living Room fit and keep the 26 Sep set in history.

## Open items
- The minimum-output versus offset-gain split is uncertain. 1550 W is the constant used here. The hold-out fit's constant was 1476.7 W.
- The house config is not edited by this pull request. The replacement runs when that app next starts.

## Tracker
- Provider: jira
- Story: SWD-595
- Task: SWD-599
- Sub-tasks: —
- Branch: cursor/swd-599-restore-params-9bcd
- PR: open, not merged
- Classification: bug
- Workflow: fix-fast
