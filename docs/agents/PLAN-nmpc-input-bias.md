# Implementation plan: NMPC input bias must step on each accepted plan

## Summary
- Room-view heater power looks like a first-order lag on every slow NMPC
  iterate. The P feedforward `u_ref` (input bias) is installed on accept,
  but the applied command keeps the previous P value until the next
  15-minute EKF+P tick, and the NLP warm-start is the unshifted last `U*`.
- Fix: recede the warm-start by one slow step; re-evaluate P (no EKF
  predict) when a plan is accepted so `u_ref` is on the actuators immediately.

## Scope / Decisions / Constraints
**In**
- `u_ref` is zero-order hold of accepted `U*[n]`. On accept it must become
  the current hold (`U*[0]` at plan origin) as a step, not a lag.
- NLP warm-start for the next slow solve is the receding-horizon shift
  `[U[1], …, U[-1], U[-1]]`, not the unshifted `U*`.
- On accept, run P only (`u = clip(u_ref + K_p (T_ref − T_hat))`) and
  publish actuators. Do **not** run `compute()` / EKF predict (that would
  integrate an extra `T_s`).
- Fast 15-minute EKF+P ticks unchanged. No extra P while the NLP is running.
- Tests, CalVer, changelog, App package sync.

**Out**
- Changing ROM weight, `K_p`, or the OCP cost.
- Plotting emitter-filter `φ` as Measured Power.
- Reintroducing the linearised QP.

**Decisions**
- Class is a **bug**: expected behaviour is a step in the feedforward at
  each accepted plan; the first-order look is wrong.
- SWD-426 still holds: no full control cycle after NLP. This adds P-only
  apply on accept.

## Classification
- Class: bug
- Confidence: high
- Why: applied `u` and planned `U*` lag on each slow iterate because the
  bias is not applied at accept and the warm-start does not recede

## Workflow
- Template: fix-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none
  - sandbox: none
- Chain: implement → review-fix → ship
- Rationale: contained controller/runtime fix; unit tests cover the
  accept-time P step and shifted warm-start.

## Inputs
- Model: `docs/agents/MODEL-nmpc-p-ff.md` (`u = u_ref + K_p (T_ref − T_hat)`)
- Screenshot: room-view Measured / Planned Power (2026-08-28)

## Acceptance criteria
1. After `apply_nmpc_result` / `set_accepted_path`, `_p_command_vector`
   uses the new `U*[0]` even before the next `compute()`.
2. Accept updates `_u_prev` and ControlEngine tag outputs to that P
   command; runtime publishes actuators without `run_control_cycle`.
3. `_nmpc_warm` after accept is the slow plan shifted by one interval.
4. Worker still does not call `run_control_cycle` on accept or reject.
5. Fast suite passes. CalVer and App package synced.

## Work packages
1. Shift NLP warm-start; P-only apply on accept (controller + runtime)
2. Tests, CalVer, changelog, App sync

## Open items
- Jira MCP was unavailable in this environment; continuity is the markdown
  mirror and this PLAN. Create/link a SWD Task on ship if needed.

## Tracker
- Provider: jira (MCP unavailable this run)
- Story: —
- Task: nmpc-input-bias (mirror)
- Sub-tasks: —
- Branch: `cursor/nmpc-input-bias-a8a3`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/635
- Classification: bug
- Workflow: fix-fast

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/635 (`ff1449e`)
