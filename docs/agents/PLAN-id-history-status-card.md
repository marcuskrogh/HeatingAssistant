# Implementation plan: ID history health card on System Status

## Summary
- Add a System Status **card** for identification-history health so estimation
  memory gaps are visible without implying controller failure.
- Expose last sample age, last durable-append outcome, and buffer–disk lag to
  the panel; colour rows locally.
- Do **not** feed into overall `system_health` / nav live-dot (reserved for the
  active controller).

## Scope / Decisions / Constraints
**In**
- Runtime counters / timestamps for ID sample commits and durable append
  success/failure (including consecutive failure streak)
- Publish ID-history health fields on `hass_states` (e.g. system_summary attrs
  or a dedicated sensor) for the Ingress panel
- System Status page: new card with three rows + local warning/error colouring
- Focused tests for thresholds and “overall quality unchanged”
- Sync App package copy

**Out**
- Changing overall `system_health` quality / `issue_summary` / live-dot from ID
  history (explicitly reserved for active controller)
- MPC / control algorithm changes
- Plot-history redesign; further write/load path work (SWD-318 / SWD-320 Done)

**Decisions**
- Card only — never worsens overall quality via ID history
- Rows: (1) last ID sample age, (2) last durable-append outcome,
  (3) buffer vs disk lag
- **Warning = duration:** last sample age > `2 × update_interval`
- **Error = failure:** `3` consecutive durable-append failures (streak resets
  on success)
- Buffer–disk lag: show `|buffer_last_ts − disk_last_ts|`; apply the same
  **duration** warning when lag > `2 × update_interval` (informational otherwise)
- Track failure streak in runtime on append fail paths (sync + async)

## Classification
- Class: tweak
- Confidence: high
- Why: small intentional observability delta; not a defect fix; too light for a
  full feature slice

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
- Rationale: localized runtime + System Status UI with focused regressions

## Inputs
- Research: docs/agents/RESEARCH-estimation-history-hole.md (SWD-319)
- Model: none
- Prior: SWD-318 durable-first writers; SWD-320 horizon JSONL merge

## Acceptance criteria
- System Status shows an ID history card with age, last append outcome, and
  buffer–disk lag
- Age (and lag) warn when older/larger than `2 × update_interval`
- Append outcome shows error after 3 consecutive durable failures; success
  clears the streak
- Overall `system_health.quality` / live-dot unchanged by ID history alone
- Focused tests pass; App package in sync

## Work packages
1. ID history health metrics + System Status card + tests (+ sync) — SWD-317

## Open items
- none

## Tracker
- Provider: jira
- Story: SWD-316
- Task: SWD-317
- Sub-tasks: —
- Branch: cursor/swd-317-id-history-status-card-2dd4
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/605
- Classification: tweak
- Workflow: delta-fast

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/605
