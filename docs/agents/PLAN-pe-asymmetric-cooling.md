# Implementation plan: PE historical cooling power uses heating capacity

## Summary
- Parameter Estimation **Heating Input** plots stored heater power down to
  about −7000 W. That is the heating thermal max with a negative command.
- The heat-pump setup is asymmetric: cooling capacity is
  `electric_max × cooling_cop` (about −3500 W), not `−max_power`.
- Live control already maps `u < 0` through `smooth_thermal_power`. PE
  charts call `thermal_power(u)`, which scales heating capacity by the
  signed fraction.

## Scope / Decisions / Constraints
**In**
- `identification_aux_series` converts stored `u` to watts with the same
  piecewise heating/cooling map the plant uses (`smooth_thermal_power`
  when the source can cool).
- `HeatPump.thermal_power` and `GroundSourceHeatPump.thermal_power` use
  cooling capacity for negative fractions so other callers cannot
  overstate heat removal.
- GSHP gains `smooth_thermal_power` so a cooling-capable GSHP matches
  the air-source map (SDE already calls that method when `can_cool`).
- Tests, CalVer `2026.08.37`, changelog, App package sync.

**Out**
- Changing NMPC / P actuation or room-view Planned Power (those already
  use `smooth_thermal_power` / `display_smooth_thermal_power`).
- Rewriting stored ID-history records (they store command `u`, not watts).
- Changing PE optimiser structure (C, R, α) beyond using the correct
  thermal map on the existing `u` series.

**Decisions**
- Class is a **bug**: displayed (and `thermal_power`) cooling watts are
  wrong for an already-configured asymmetric heat pump.
- PE charts keep applying identified `power_scale` (existing heater-scale
  tests). Rated display power stays on room plots via
  `display_smooth_thermal_power`.
- Electric heaters and other heat-only sources stay linear.

## Classification
- Class: bug
- Confidence: high
- Why: expected cooling watts are the configured cooling capacity; PE
  history currently plots −heating capacity

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
- Chain: implement → test → harden → review-fix → ship
- Rationale: contained heat-source / PE-series conversion; unit tests
  cover u = −1. Test and harden stay on (catalog floor). Localized, so
  focused single review.

## Inputs
- Screenshot: Parameter Estimation Heating Input, Heating Power trough
  at −7000 W while cooling should be about −3500 W
- `HeatPump.cooling_power` docstring: cooling must not inherit heating
  `max_power`

## Acceptance criteria
1. For a heat pump with typical COP/EER, PE Heating Input at `u = −1`
   equals `cooling_power` (about −3500 W for a 5 kW / COP 3.5 / EER 2.5
   unit), not `−max_power`.
2. Heating (`u ≥ 0`) PE series is unchanged (COP-limited heating watts,
   including `power_scale`).
3. Electric-heater PE aux series still scale as `max_power × u × scale`.
4. Fast suite passes. CalVer 2026.08.37; App package synced.

## Work packages
1. Asymmetric thermal map for PE series + heat-pump `thermal_power` (SWD-460)
2. Tests, CalVer, changelog, App sync (SWD-461)

## Open items
- None. Existing stored datasets already hold `u`; only the watt
  conversion is wrong.

## Tracker
- Provider: jira
- Story: —
- Task: SWD-459
- Sub-tasks: SWD-460, SWD-461
- Branch: cursor/swd-459-pe-asymmetric-cooling-b2b6
- PR: (draft, opened with this plan)
- Classification: bug
- Workflow: fix-fast

## Next
`/test SWD-459` — Dedicated testing phase after implement (same branch/PR)
