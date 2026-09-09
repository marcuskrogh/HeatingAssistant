# Implementation plan: Linear or nonlinear MPC on Tuning

## Summary
- Replace the single two-rate happy path with an exclusive **controller
  mode** on Controller Tuning: linear model predictive control or
  nonlinear model predictive control.
- Shared penalty / comfort knobs keep their current values when the
  operator switches modes. Mode-specific timing knobs stay stored so
  switching back does not require a retune.
- Default mode is nonlinear (`nmpc`), matching the current planner.

## Scope / Decisions / Constraints
**In**
- Config key `mpc_mode`: `linear` | `nmpc`. Missing / unknown → `nmpc`.
- Tuning page layout:
  1. Two exclusive selection cards (linear left, nonlinear right) with
     short copy.
  2. Existing Apply Changes / Reset bar.
  3. Shared live weights (always visible).
  4. Mode-specific timing knobs for the active card.
- Linear: successive-linearisation QP (`HeatingLinearisedMPC.step`) each
  sample interval. No NMPC worker. Sample interval and step horizon are
  independent knobs (`update_interval`, `horizon`).
- Nonlinear: SciPy NMPC worker applies remaining planned ``U*`` as
  zero-order hold (plus clamps / disabled sources / watchdog ``u = 0``).
  No two-layer P/PID on ``T_ref``. Heat-source ``p_gain`` (that tracker)
  is removed. Inner climate actuation stays: commanded fraction maps to
  a heater setpoint relative to the unit's internal temperature
  (``target_temperature`` / ``max_temp_offset``).
- Persist both timing sets. Apply rebuilds the controller when mode or
  restart knobs change.
- Preview uses the selected mode.
- Compute stays in the App process (`SWD-254`).
- Dual tree: `heatingassistant/` then `scripts/sync-ha-app-package.sh`.
- Tests, CalVer, changelog, App package sync.

**Out**
- Dual-mode running both planners at once.
- Linear QP as a silent fallback while NMPC is selected.
- CasADi / IPOPT.
- Changing parameter estimation.
- Changing inner heater setpoint-vs-internal-temperature actuation.

**Decisions**
- Class is **feature**: new operator-facing mode plus a restored linear
  control path.
- Shared knobs: comfort offset, tracking / energy / price / smoothing
  weights, quadratic and linear comfort penalties, terminal weight,
  window-detection knobs.
- Linear-only: sample interval, prediction horizon (steps).
- NMPC-only: period, fast substeps, look-ahead hours, derived sample
  interval. No Tuning knobs for a two-layer tracker.
- Default `nmpc` so existing installs keep the nonlinear planner.
- Do not overwrite stored linear interval/horizon with derived NMPC
  `T_s` / `n_fast`.

**Constraints**
- Product copy must not include tracker keys.
- Linear QP must not run on the Home Assistant Core event loop.

## Classification
- Class: feature
- Confidence: high
- Why: new exclusive planner choice and Tuning UX; linear path restored
  as a first-class mode, not a bugfix

## Workflow
- Template: feature-standard
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none
- Chain: implement → review-fix → ship
- Rationale: one control+UI slice; linear QP already exists as EKF glue;
  efficiency-first single implement

## Inputs
- Research: none
- Model: `docs/agents/MODEL-nmpc-p-ff.md` (historical two-layer tracker;
  nonlinear mode now holds ``U*`` without the P law)
- Prior: SWD-395 hierarchical NMPC+P (tracker removed); SWD-238 dual-mode
  (removed by SWD-254 because NMPC ran on Core — compute stays in the App)

## Acceptance criteria
1. Tuning shows two exclusive cards: linear (lower compute, linearization
   error) and nonlinear (higher compute, longer solves).
2. Apply persists `mpc_mode`. Only one planner is active.
3. Shared weights keep their values across a mode switch without retune.
4. Linear mode solves the QP each sample and does not start the NMPC
   worker.
5. Nonlinear mode keeps the NMPC worker, accept/reject, and watchdog.
   Commands follow remaining ``U*`` (ZOH), not ``u_ref + K_p (T_ref − T)``.
   With no accepted plan, heaters stay at ``u = 0``.
6. Preview overlay uses the selected mode.
7. Missing `mpc_mode` on disk behaves as `nmpc`.
8. Room Regulator Load KPI and two-layer tracker Tuning knobs are gone.
   Heat-source ``p_gain`` is gone. Inner heater setpoint mapping stays.

## Work packages
1. Engine: `mpc_mode`, timing isolation, linear QP vs NMPC worker.
2. UI: mode cards, shared vs mode-specific Tuning fields.
3. Tests, CalVer, changelog, App sync.

## Open items
- None. Two-layer NMPC tracker and its KPIs are in scope of this Task.

## Tracker
- Provider: jira
- Story: —
- Task: SWD-522
- Sub-tasks: SWD-523 (engine), SWD-524 (UI), SWD-525 (tests/CalVer)
- Branch: `cursor/swd-522-mpc-mode-choice-58e6`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/672
- Classification: feature
- Workflow: feature-standard

## Next
Done
