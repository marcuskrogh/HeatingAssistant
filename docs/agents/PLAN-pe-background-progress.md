# Implementation plan: Background PE progress across Overview, Tuning, and nav

## Summary
- Parameter estimation already runs as a background job. Closing the Fitting
  the model overlay must hide it without stopping the fit, so the operator can
  use Overview, Controller Tuning, and other pages while it continues.
- Those pages and the panel nav show that a fit is running and reopen the
  overlay. Starting another estimation is blocked until the running job is
  stopped explicitly.

## Scope / Decisions / Constraints
**In**
- Panel-owned PE overlay session (survives route changes).
- Close (X) dismisses overlay; does not cancel.
- Stop control on the overlay (and page banners) cancels the job; parameters
  are not applied.
- In-progress banners on Overview, Controller Tuning, and Parameter Estimation
  (index + room detail); click reopens the overlay.
- Compact Estimating chip only in `panel-nav` controls; click reopens the
  overlay. Hidden when idle. Not in the HA `#top-bar`.
- Overlay shows two log plots with the same canvas style: fit error (η vs
  η-tol) and optimiser convergence (L-BFGS-B relative cost drop vs ftol).
  Each plot has a title so they are distinguishable.
- Identification start path refuses a second start while `status === running`
  and opens the existing overlay instead.

**Out**
- PE algorithm, time cap, apply-on-timeout, or changing ftol/gtol values.
- Extra job payload fields beyond publishing `ftol` / `rel_red` for the plot.
- Auto-opening the overlay when a dismissed job finishes.
- Starting more than one concurrent PE worker (backend already returns the
  running snapshot).

**Decisions**
- Class is **feature**: multi-surface operator flow, not a defect fix.
- Overlay lives on the industrial panel shadow root, not on the Identification
  page, so leaving Parameter Estimation does not destroy the dialog host.
- Close while running no longer cancels (changes SWD-504). Stop is the cancel
  path.
- Indicators are visible only while `job.status === running`.
- Product copy must not include tracker keys.

**Constraints**
- Dual tree: edit `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.
- Keep overlay stay-open-on-finish behaviour from SWD-504 when the overlay is
  visible.

## Classification
- Class: feature
- Confidence: high
- Why: new operator flow across overlay, pages, and nav; exclusive job control

## Workflow
- Template: feature-standard
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
- Rationale: localized panel UI + existing PE job API; no new layers; test/harden are the floor

## Inputs
- Research: none
- Model: none
- Sandbox: docs/agents/SANDBOX-pe-progress.md (prior popup)
- Prior: SWD-504, SWD-453

## Pass criteria
- Closing the overlay while a job is running hides the dialog and does not call
  cancel; `get_pe_job` stays `running`.
- A Stop control requests cancel; the job ends `cancelled` and parameters are
  not applied.
- Overview, Controller Tuning, and Parameter Estimation show an in-progress
  control while running; activating it shows the overlay.
- Panel nav shows an Estimating chip while running; activating it shows the
  overlay. The HA top bar does not.
- The overlay paints two titled log plots (Fit error and Optimiser
  convergence) with a dashed tolerance line matching the existing RMS style.
- Run recommended estimation (and equivalent start) does not start a second job
  while one is running; it surfaces the running overlay instead.
- Overlay and chips survive navigating away from Parameter Estimation.
- Focused tests pass; CalVer bump; changelog; App package in sync.

## Work packages
1. SWD-574 — Panel PE session, dismiss overlay, banners, nav, start-guard
2. SWD-575 — Tests, CalVer, changelog, App sync

## Open items
- none

## Tracker
- Provider: jira
- Story: —
- Task: SWD-573
- Sub-tasks: SWD-574, SWD-575
- Relates: SWD-504
- Branch: cursor/swd-573-pe-background-progress-6f70
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/694
- Classification: feature
- Workflow: feature-standard

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/694 (`843739e4`)
