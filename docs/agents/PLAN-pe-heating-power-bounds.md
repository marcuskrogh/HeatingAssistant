# Implementation plan: PE Heating Input stays inside configured power

## Summary
- Parameter-estimation Heating Input was rebuilt from the stored command
  with the identified heater scale (`power_scale`).
- Room view uses configured display capacity and did not show 10–12 kW.
- The series on this page now uses that same configured power map.
  Partial commands stay proportional. The trace is not clipped to the maximum.

## Scope / Decisions / Constraints
- In: `identification_aux_series` (the series `getPeInputs` returns when a
  dataset is loaded).
- Out: the plant model, the estimator's use of `power_scale`, and stored
  history records. Those records keep the command fraction.
- Cooling still uses cooling capacity, not negative heating capacity.

## Classification
- Class: bug
- Confidence: high
- Why: the plotted watts did not match the configured heat-pump limits.

## Workflow
- Template: fix-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - test.mode: dedicated
  - harden.mode: skip
  - review.mode: single
  - review.depth: focused
  - review.lasers: bundled
  - side_paths: none
  - sandbox: none
- Chain: implement → test → review → ship
- Rationale: the conversion is one function and the correct map already
  exists for room view.

## Pass criteria
- A heat pump with `power_scale` 2 and `max_power` 6300 W plots the
  configured map: full command at rated capacity (about 6300 W), half
  command at half of that. Half command is not `min(scaled power, 6300 W)`.
- A negative command plots configured cooling capacity on that same map,
  not scaled cooling and not negative heating capacity.
- Calendar version is the current month.

## Work packages
1. Display-power conversion, tests, calendar version, App sync.

## Open items
- None.

## Tracker
- Provider: jira
- Task: SWD-585
- Branch: cursor/pe-heating-power-bounds-718a
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/703
- Classification: bug
- Workflow: fix-fast
