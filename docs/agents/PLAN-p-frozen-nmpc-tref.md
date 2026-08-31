# Implementation plan: P tracks the original NMPC trajectory for 2 h

## Summary
- The proportional (P) controller must track the last accepted nonlinear
  model predictive control (NMPC) path `(T_ref, u_ref)` for the whole slow
  interval (default 2 h).
- P is the regulator. New outdoor / solar / wind during the window must
  move the tracking error, not the reference.
- Room-view Forecast may still resimulate leftover `U*` with current
  disturbances. That series is display only. It must not become the P
  reference.

## Scope / Decisions / Constraints
**In**
- `_nmpc_T_ref` and `_nmpc_U` are installed only in `set_accepted_path`
  (accept) and cleared on watchdog reject. Store copies so later mutation
  of the solver arrays cannot move the P reference.
- `_p_command_vector` reads that frozen path at the wall-clock fast index.
  It does not read `_predictions` or `rebuild_forecast_from_plan` air.
- `rebuild_forecast_from_plan` / `_publish_plan_rollout` stay plot-only.
- Regression: after `compute()` with a changed outdoor/solar forecast,
  `_nmpc_T_ref` equals the accept-time path and the P command uses that
  `T_ref`, not the resimulated Forecast sample.
- Dual tree: edit `heatingassistant/`, then
  `scripts/sync-ha-app-package.sh`. CalVer + changelog only if the P
  reference was actually being overwritten (user-visible control change).

**Out**
- Changing Forecast / Planned Power plot policy (SWD-417 / SWD-431
  remaining-`U*` resim stays).
- Changing `K_p`, deadband, idle NMPC retry, or the slow NLP.
- Re-solving NMPC inside the 2 h window for a non-idle plan.

**Decisions**
- Class is a **bug**: expected hierarchical behaviour is known from the
  model (`docs/agents/MODEL-nmpc-p-ff.md`). If the P reference follows
  updated disturbances, that is wrong.
- Investigation found `_nmpc_T_ref` is already only written on accept.
  The work is to freeze copies at that seam and lock the split with a
  test so Forecast resim cannot leak into P.

**Constraints**
- Dual tree as above.
- Product copy must not include tracker keys.

## Classification
- Class: bug
- Confidence: high
- Why: P must track the accept-time NMPC path for the slow interval;
  retargeting a disturbance-updated air path is a known defect if present

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
  - review.lasers: sequential
  - side_paths: none
  - sandbox: none
- Chain: architect → implement → test → restructure → review → ship
- Rationale: contained controller seam; unit test pins P vs Forecast.
  Test and harden stay on (catalog floor). Localized, so focused single
  review.

## Inputs
- Model: `docs/agents/MODEL-nmpc-p-ff.md` (fast law tracks solve-time
  `T_ref` under accepted `U*`)
- Prior plot split: SWD-417 / SWD-431 Forecast resim (not freeze-`T_ref`
  on the chart)

## Acceptance criteria
1. After `set_accepted_path`, `_nmpc_T_ref` is a copy of the NMPC air
   path, not a view of the solver buffer.
2. `compute()` / `rebuild_forecast_from_plan` with new outdoor/solar
   must not change `_nmpc_T_ref` or `_nmpc_U`.
3. `_p_command_vector` after those calls equals
   `p_command(u_ref, T_ref_accept[k], T_hat, …)`, not
   `p_command(u_ref, Forecast[0], T_hat, …)` when Forecast has moved.
4. Forecast may still differ from the frozen `T_ref` (plot resim).
5. Fast suite for the touched path passes.

## Work packages
1. Freeze P `T_ref` / `U*` copies at accept (SWD-466)
2. Tests, CalVer/changelog/App sync if product behaviour changed (SWD-467)

## Open items
- None

## Tracker
- Provider: jira
- Story: —
- Task: SWD-465
- Sub-tasks: SWD-466, SWD-467
- Branch: `cursor/swd-465-p-frozen-tref-105a`
- PR: —
- Classification: bug
- Workflow: fix-fast

## Next
`/implement SWD-465` — Build to ARCHITECTURE.md (same branch)
