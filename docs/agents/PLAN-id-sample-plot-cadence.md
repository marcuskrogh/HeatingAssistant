# Implementation plan: Align ID sample write with plot cadence

## Summary
- Identification samples are written only inside `run_control_cycle`, while plot
  history also writes from the wall-clock ticker and MQTT `update_tag`.
- Option B: take ID samples on the same writers as plot history (ticker +
  `update_tag`, plus existing control path), gated by `update_interval`.
- Durable-first append: write JSONL first, then append to `_history_buffer`
  only on success.

## Scope / Decisions / Constraints
**In**
- `heatingassistant/app/runtime.py` — ID sample from ticker history tick and
  `update_tag`; keep control-cycle sample with shared interval gate
- Durable-first: `id_history_store.append` / `async_append` before
  `_history_buffer.append`; do not advance `_id_history_last_ts` on disk failure
- Sync App package copy; regression tests (ticker/tag ID append; control still
  gated; durable-first does not buffer on append failure)

**Out**
- System Status ID health (SWD-317)
- Horizon load path (SWD-320 Done)
- MPC/control algorithm changes; plot history redesign

**Decisions**
- Option B: ID writers = ticker + `update_tag` + control (interval gate dedupes)
- Reuse `_take_identification_sample` / `_record_identification_sample` with
  durable-first ordering
- Sync ticker/tag paths use sync store append; control may keep async append
  with the same durable-first semantics
- Skip when no measured room temps (existing gate)

## Classification
- Class: bug
- Confidence: high
- Why: write-path asymmetry can omit estimation memory while plots advance

## Workflow
- Template: fix-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none
- Chain: implement → review-fix → ship
- Rationale: localized runtime write-path fix with focused regressions

## Inputs
- Research: docs/agents/RESEARCH-estimation-history-hole.md (SWD-319)
- Model: none

## Acceptance criteria
- Wall-clock ticker records an ID sample when temps are available and the
  interval gate allows (even if control does not run that tick)
- `update_tag` records an ID sample under the same gate
- Control-cycle ID path still works; no double samples within one interval
- On JSONL append failure: buffer unchanged and `_id_history_last_ts` not advanced
- Focused tests pass; App package in sync

## Work packages
1. ID writers on ticker + update_tag + durable-first + tests (+ sync) — SWD-318

## Open items
- none

## Tracker
- Provider: jira
- Story: SWD-316
- Task: SWD-318
- Sub-tasks: —
- Branch: cursor/swd-318-id-sample-plot-cadence-2dd4
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/603
- Classification: bug
- Workflow: fix-fast

## Next
`/implement SWD-318` — Build per PLAN.md workflow binding (same branch/PR)
