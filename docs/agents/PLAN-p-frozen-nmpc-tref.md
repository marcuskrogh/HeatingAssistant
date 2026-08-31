# Implementation plan: P tracks the original NMPC trajectory for 2 h

## Summary
- The proportional (P) controller must track the last accepted nonlinear
  model predictive control (NMPC) **air trajectory** `T_ref(τ)` and the
  held input `u_ref` for the whole slow interval (default 2 h).
- `U*` is zero-order-held every 2 h. `T_ref` is **not** a two-hour
  constant: the OCP rolls the plant with fast-grid / integrator
  substeps, so the air path can vary inside each hold.
- P is the regulator. New outdoor / solar / wind during the window must
  move the tracking error, not retarget that trajectory.
- Room-view Forecast is leftover accept-time `T_ref` on the fast grid.
  Do not resim leftover `U*` with updated disturbances on each 15-minute
  tick, and do not flatten Forecast to a single temperature per hold.
  Planned Power stays leftover `U*` with one outdoor sample per 2 h hold.

## Scope / Decisions / Constraints
**In**
- `_nmpc_T_ref` and `_nmpc_U` are installed only in `set_accepted_path`
  (accept) and cleared on watchdog reject. Store copies so later mutation
  of the solver arrays cannot move the P reference or Forecast.
- `_p_command_vector` reads that accept-time trajectory at the wall-clock
  fast index `k` (`T_ref[k]`, not `T_ref[0]` held for 2 h).
  It does not read `_predictions`.
- `_publish_plan_rollout` / `rebuild_forecast_from_plan` plot remaining
  `T_ref` (`T_ref[k:]` padded) and leftover `U*`. Open-loop roll of `U*`
  is only the no-plan fallback.
- Regression: after `compute()` with a changed outdoor/solar forecast,
  `_nmpc_T_ref` equals the accept-time trajectory, P uses `T_ref[k]`, and
  Forecast temperatures equal the remaining fast-grid `T_ref` (varies
  inside a slow `U*` hold).
- Dual tree: edit `heatingassistant/`, then
  `scripts/sync-ha-app-package.sh`. Plot freeze is user-visible → CalVer
  + changelog.

**Out**
- Changing `K_p`, deadband, idle NMPC retry, or the slow NLP.
- Re-solving NMPC inside the 2 h window for a non-idle plan.
- Changing Planned Power leftover-`U*` / 2 h outdoor hold policy.

**Decisions**
- Class is a **bug**: expected hierarchical behaviour is known from the
  model (`docs/agents/MODEL-nmpc-p-ff.md`). If P or Forecast follows
  updated disturbances inside the slow interval, that is wrong.
- P already used the accept-time path. The remaining defect is Forecast
  resim on the room plot (SWD-417 / SWD-431 accepted that split; this
  Task reverses the plot policy).

**Constraints**
- Dual tree as above.
- Product copy must not include tracker keys.

## Classification
- Class: bug
- Confidence: high
- Why: P and room Forecast must show the accept-time NMPC path for the
  slow interval; retargeting a disturbance-updated air path is a defect

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
- Rationale: contained controller seam; unit tests pin P and Forecast
  to leftover `T_ref`. Test and harden stay on (catalog floor). Localized,
  so focused single review.

## Inputs
- Model: `docs/agents/MODEL-nmpc-p-ff.md` (fast law tracks solve-time
  `T_ref` under accepted `U*`)
- Prior plot split: SWD-417 / SWD-431 remaining-`U*` resim (reversed here
  for Forecast temperature)

## Acceptance criteria
1. After `set_accepted_path`, `_nmpc_T_ref` is a copy of the NMPC air
   path, not a view of the solver buffer.
2. `compute()` / `rebuild_forecast_from_plan` with new outdoor/solar
   must not change `_nmpc_T_ref` or `_nmpc_U`.
3. `_p_command_vector` after those calls equals
   `p_command(u_ref, T_ref_accept[k], T_hat, …)`.
4. Forecast temperatures equal remaining accept-time `T_ref` (`T_ref[k:]`
   padded) on the fast grid. The series may vary inside a 2 h `U*` hold.
   Changed outdoor/solar/wind must not move that series, and it must not
   collapse to a two-hour constant.
5. Planned Power remains leftover `U*` with one outdoor sample per hold.
6. Fast suite for the touched path passes. CalVer + changelog for the
   plot change.

## Work packages
1. Freeze P `T_ref` / `U*` copies at accept (SWD-466)
2. Tests, CalVer/changelog/App sync if product behaviour changed (SWD-467)
3. Room-view Forecast leftover `T_ref`; tests + CalVer (SWD-468)

## Open items
- None

## Tracker
- Provider: jira
- Story: —
- Task: SWD-465
- Sub-tasks: SWD-466, SWD-467, SWD-468
- Branch: `cursor/swd-465-p-frozen-tref-105a`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/651
- Classification: bug
- Workflow: fix-fast

## Next
`/review SWD-465` — Lasers then fix then code review
