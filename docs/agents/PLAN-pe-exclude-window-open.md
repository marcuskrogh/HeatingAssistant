# Implementation plan: Exclude open door/window samples from Parameter Estimation

## Summary
- When heater override is active for a room (door/window open past
  `window_open_debounce`), flag that room in ID history and **exclude** its
  samples from offline Parameter Estimation / identification fits.
- Other rooms keep contributing; live control filtering is unchanged.
- Excluded points are simply omitted (chart gaps) — no badges or callouts.

## Scope / Decisions / Constraints
**In**
- Ensure ID history records `window_open[room] = true` only when
  `is_window_override_active` (states `open` / `pending_closed`) — same gate
  as heater shutoff (App already writes this in `_take_identification_sample`).
- Ensure all offline PE / ID paths that consume ID history honor the per-room
  mask: open-loop objective / Kalman ML fit, open-loop diagnostics, sysid /
  initial-state estimation displays that score measurements.
- Per-room residual drop (and existing neighbour pin semantics where already
  implemented in sensitivity) so open-window dynamics do not bias C / R / α /
  R_ij.
- PE charts: simply remove excluded measured/predicted points for that room
  (gaps OK).
- Regression tests proving corrupted open-window temperatures do not change
  the offline fit objective (revive/adapt `tests/test_window_data_quality.py`
  off the SWD-262 module skip where practical).
- App package sync if code changes land under `heatingassistant/`.

**Out**
- Changing heater override / debounce / settle behaviour (SWD-298).
- Modelling air-exchange / open-window plant dynamics in the thermal model.
- Whole-house timestep drops when any contact is open.
- Excluding brief `pending_open` intervals.
- Changing live CD-EKF measurement updates (keep Q inflation while override
  active).
- Extra UI badges, legends, or callouts for excluded windows.
- Redesigning PE UX beyond silent omission of points.

**Decisions**
- Flag criterion = heater override active only (not raw contact / `pending_open`).
- Exclusion = that room only; series may be gappy / chopped.
- Offline PE / ID fits only; live filtering unchanged.
- Prefer fixing gaps in the existing mask pipeline over inventing a second
  exclusion mechanism (engine already carries `window_open` through
  `history_std` + sensitivity / diagnostics / sysid).

**Constraints**
- Preserve SWD-298 heater clamp + Q inflation behaviour.
- Legacy history records without `window_open` default to closed (all used).

## Classification
- Class: tweak
- Confidence: high
- Why: small intentional PE behaviour delta on an existing flag/mask path;
  not a heater-override defect and not a new product surface

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
- Rationale: localized estimation/history concern; existing THEORY + SWD-298
  flags; no new layers or schema migration

## Inputs
- Research: none
- Model: none
- Prior: SWD-298 App window override + `window_open` history fields; THEORY
  open-window override; `estimation/sensitivity.py` per-room pin/drop

## Acceptance criteria
1. While a room’s heater override is active, new ID-history samples flag
   `window_open[room] = true` for that room only.
2. Offline Parameter Estimation / identification fits that consume ID history
   do **not** score that room’s residuals for flagged samples (corrupted
   open-window temperatures must not change the fit objective vs an otherwise
   identical closed-window series).
3. Other rooms’ samples at the same timestamps remain eligible for the fit.
4. Samples during `pending_open` (before shutoff) remain eligible.
5. Live control filtering still uses measurements; Q inflation while override
   active unchanged.
6. PE / sysid charts simply omit excluded points for that room (gaps); no new
   badges.
7. Focused regression tests cover mask carry-through + objective exclusion;
   version bump + App sync only if runtime code changes.

## Work packages
1. Audit App flag → history_std → offline PE/sysid/diagnostics paths; close any
   hole that still applies override-active room measurements; revive/adapt
   window data-quality tests; sync App package if needed.

## Open items
- None for definition (implement may discover a specific path still ignoring
  the mask — fix under this Task).

## Tracker
- Provider: jira
- Story: —
- Task: SWD-322
- Sub-tasks: —
- Branch: `cursor/swd-322-pe-exclude-window-open-f7b1`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/610
- Classification: tweak
- Workflow: delta-fast

## Next
`/implement SWD-322` — Enforce per-room PE exclusion for override-active
samples per PLAN.md (same branch/PR); or `/ship SWD-322` for remaining chain
