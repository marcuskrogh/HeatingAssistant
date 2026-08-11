# Implementation plan: resolve_history(horizon) merges id_history JSONL

## Summary
- Default estimation/EKF horizon load reads only `_history_buffer` and never
  `id_history` JSONL, so durable samples can be invisible on the horizon path.
- Route `horizon_hours` through the same `[end − horizon, end]` + JSONL merge
  already used by explicit `window_start`/`window_end` (Option A).

## Scope / Decisions / Constraints
**In**
- `heatingassistant/app/sysid_services.py` — `resolve_history` horizon branch
- Sync App package copy if required by repo lock (`heating_assistant/`)
- Regression test: JSONL-only records appear under `horizon_hours` when buffer
  lacks them (and still appear when buffer is short)

**Out**
- ID sample write cadence / durable-first append (SWD-318)
- System Status ID health (SWD-317)
- Plot history, controller/MPC, CD-EKF math
- Changing explicit window-mode behaviour (already correct)

**Decisions**
- Option A: for `horizon_hours`, set `end` from last buffer timestamp (or now if
  empty), `start = end − horizon`, then reuse the existing window merge path
  (buffer + `async_query_range`)
- Empty / non-positive horizon: keep current safe behaviour (no spurious full scan)
- No new public API; callers of `horizon_hours` unchanged

## Classification
- Class: bug
- Confidence: high
- Why: load path is wrong vs intended durable history; correct behaviour known

## Workflow
- Template: fix-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none (research already Done as SWD-319)
- Chain: implement → review-fix → ship
- Rationale: localized defect in one function + focused regression tests

## Inputs
- Research: docs/agents/RESEARCH-estimation-history-hole.md (SWD-319)
- Model: none

## Acceptance criteria
- `resolve_history(..., horizon_hours=H)` returns the same durable coverage as
  an equivalent `window_start`/`window_end` for `[end−H, end]`
- Records present only in `id_history` JSONL (not in buffer) appear in horizon
  results
- Existing window / dataset resolve paths unchanged
- Focused tests pass; App package stays in sync if touched

## Work packages
1. Horizon→window merge in `resolve_history` + regression test (+ sync) — SWD-320

## Open items
- none (incident H-write vs H-load forensics remain on SWD-318 / operator check)

## Tracker
- Provider: jira
- Story: SWD-316
- Task: SWD-320
- Sub-tasks: —
- Branch: cursor/swd-320-resolve-history-horizon-jsonl-2dd4
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/602
- Classification: bug
- Workflow: fix-fast

## Next
`/implement SWD-320` — Build per PLAN.md workflow binding (same branch/PR)
