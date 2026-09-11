# Implementation plan: Configurable NMPC max compute time

## Summary
- Each nonlinear MPC solve is hard-capped at **60 seconds** (`NMPC_TIMEOUT_S` in `nmpc_ocp.py`). That is why live compute time sits so close to one minute.
- Expose the cap as `nmpc_max_compute_s` (default 60) on **Controller Tuning** for Nonlinear, persist it, and use it for live solves and Tuning preview.

## Scope / Decisions / Constraints
**In**
- Config key `nmpc_max_compute_s`, default **60 s** (same as today’s hard cap).
- Controller Tuning: Nonlinear-only live field **Max compute time** (seconds).
- Persist via existing `update_controller_tuning` / App options.
- Live NMPC and Tuning preview both honor the stored cap.
- Missing, non-finite, or `<= 0` values fall back to 60 s (explicit `timeout_s=` on `solve_nmpc` stays as passed, for tests).
- Tests, CalVer `2026.09.23`, changelog, TUNING.md, App package sync.

**Out**
- Changing SciPy `maxiter`, PE `pe_max_compute_s`, or Linear QP time.
- Unlimited / zero-means-off semantics.

## Classification
- Class: tweak
- Confidence: high
- Why: small intentional behaviour delta — expose an existing solver cap; not a defect.

## Workflow
- Template: delta-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - test.mode: dedicated
  - harden.mode: dedicated
  - review.mode: single
  - review.depth: focused
  - review.lasers: sequential
  - side_paths: none
  - sandbox: none
- Chain: architect → implement → test → restructure → review → ship
- Rationale: Localized config+UI+solver option; test/harden stay the floor.

## Inputs
- Research: none
- Model: none
- Sandbox: none

## Pass criteria
- Default NMPC wall-clock cap remains 60 s when the key is absent.
- A stored `nmpc_max_compute_s` is the timeout used by `HeatingMPCController.solve_nmpc` when `timeout_s` is omitted.
- Controller Tuning shows **Max compute time** for Nonlinear and hides it for Linear.
- `controller_config` includes `nmpc_max_compute_s`.
- Invalid / non-positive stored values coerce to 60 s.

## Work packages
1. Wire config, solver, preview, and Tuning field (SWD-543).
2. Tests, CalVer, changelog, App sync (SWD-544).

## Open items
- None — Cloud Agent proceeds from the user’s “if this is the case, make it configurable on the tuning page.”

## Tracker
- Provider: jira
- Task: SWD-542
- Sub-tasks: SWD-543, SWD-544
- Branch: `cursor/nmpc-max-compute-0f2f`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/682
- Classification: tweak
- Workflow: delta-fast

## Next
`/architect SWD-542` — Shape stamp then implement on the same branch
