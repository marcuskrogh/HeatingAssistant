# Implementation plan: Archive finished PE results below stored datasets

## Summary
- Parameter estimation already runs in the background. When a run finishes, the
  identified parameters must be stored even if the operator is not on the page
  or device, so they can load and apply later.
- A new Identification Results list sits under Stored Datasets. Each completed
  run shows its exit reason, final RMSE, and R². Load fills the form; Apply
  Parameters remains the live-model write. Results can be deleted.

## Scope / Decisions / Constraints
**In**
- Persist a completed PE job as a result catalog entry (parameter snapshot plus
  metrics), independent of Apply Parameters.
- Store when the job finishes by: optimiser convergence (cost or gradient),
  max iterations, max compute time, or data-fit plateau (η stale).
- Identification page section immediately below Stored Datasets, using the
  existing `store-row` look and feel.
- Each row shows: finished time, exit label, RMSE (°C), R², Load, Delete.
- Load copies that snapshot into the room parameter fields for review.
- Apply Parameters stays the only write to the live controller.
- Finishing does **not** auto-fill the room form, even if the overlay is open.
- Delete removes that catalog entry only (does not change the live model).

**Out**
- PE algorithm, ftol/gtol, overlay plot style.
- Auto-apply to the live controller on finish.
- Storing cancelled or crashed jobs.
- Replacing the existing applied `parameter_history` list on the room form
  (that list stays applied-set history).

**Decisions**
- Class is **feature**: new persist path and operator catalog.
- Overlay still stays open on finish (SWD-504/573); it does not populate
  fields. Operator uses Identification Results → Load → Apply Parameters.
- RMSE is the PE best RMS in °C (`rmse_c_best` / overlay RMS).
- R² is the coefficient of determination on measured vs identified-model
  temperatures for the same identification window
  (`compute_model_fit_metrics`: \(1 - SS_\mathrm{res}/SS_\mathrm{tot}\)).
- Catalog is house-level (joint PE) and shown on every room Identification
  page under Stored Datasets.
- Newest first. Cap at 25 entries so options stay bounded.
- Product copy must not include tracker keys.

**Constraints**
- Dual tree: edit `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.
- Keep `apply_parameters: false` on start; archive in the PE worker after a
  storeable exit, then `save_config`.

## Classification
- Class: feature
- Confidence: high
- Why: new persist catalog and Identification UI; not a defect fix

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
- Rationale: localized PE persist + Identification list; no new layers; test/harden are the floor

## Inputs
- Research: none
- Model: none
- Sandbox: none
- Prior: SWD-573, SWD-504, SWD-453

## Pass criteria
- A PE job that finishes by convergence, max iterations, max time, or η
  plateau writes one catalog entry with `exit_label`, RMSE, and R² without
  applying live parameters.
- A cancelled or crashed job does not add a catalog entry.
- `runAutoIdentification` / overlay finish does not populate room fields.
- Identification Results appears below Stored Datasets; rows use store-row
  chrome; exit, RMSE, and R² are visible on each row.
- Load fills the form from that snapshot; Apply Parameters still persists
  the live model.
- Delete removes that entry and the list updates.
- Focused tests pass; CalVer bump; changelog; App package in sync.

## Work packages
1. SWD-577 — Persist finished PE results with exit, RMSE, and R²
2. SWD-578 — UI: identification results list, load, and delete
3. SWD-579 — Tests, CalVer, changelog, App sync

## Open items
- none

## Tracker
- Provider: jira
- Story: —
- Task: SWD-576
- Sub-tasks: SWD-577, SWD-578, SWD-579
- Relates: SWD-573
- Branch: cursor/swd-576-pe-fit-results-6f70
- PR: —
- Classification: feature
- Workflow: feature-standard

## Next
`/architect SWD-576` — Shape persist + Identification list (same branch/PR)
