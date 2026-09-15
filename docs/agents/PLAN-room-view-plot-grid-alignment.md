# Implementation plan: Align room view plot grids

## Summary
- Room view stacks three Chart.js plots (Temperature, Heating Power & Price, Disturbances). Vertical time-grid lines and the NOW marker do not share one horizontal plot box, so the same wall-clock time sits at different x positions.
- Cause: Temperature has a left Y-axis only; the other two reserve a right Y-axis (and different left tick/title widths). Chart.js then shrinks `chartArea` independently. Shared `x.min`/`x.max` is not enough if the plot box differs.
- Fix: keep three plots; lock the same `chartArea.left` and `chartArea.right` (and the same time domain) across the three room-view instances so ticks, grid, and NOW coincide on desktop and mobile.

## Scope / Decisions / Constraints
**In**
- Room view stacked time plots in `heatingassistant/app/static/js/pages/room-detail.js` and `TimeSeriesChart` (`heatingassistant/app/static/js/components/time-series-chart.js`), including `nowLinePlugin` and x-grid.
- Shared time domain if the three charts can currently auto-scale x independently.
- Dual-tree App package sync, CalVer, changelog, cache-bust query on touched JS.
- Browser check on a desktop viewport and a mobile viewport (≤768px), exercising the room view (not a single screenshot).

**Out**
- Series data, forecast/history fetch, MPC, experiment-band semantics.
- Merging the three series into one chart unless the shared plot-box approach fails (then a single aligned section is allowed; alignment is the bar).
- Tuning preview and sysid stacked charts as pass criteria (a shared helper may apply there with no extra product work; do not expand the Task to restyle those pages).

**Decisions**
- Keep three titled cards. Do not require a combined chart.
- Dual Y-axes stay (kW/Price, °C/kW). They must not change the time-axis width relative to Temperature.
- NOW is drawn from the same x-scale mapping as the vertical grid; once plot boxes match, NOW matches.
- Resize and orientation change must re-lock the shared box (existing resize listeners stay).

**Constraints**
- Dual tree: edit `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.
- Product copy must not include tracker keys.
- Do not invent a new chart library.

## Classification
- Class: bug
- Confidence: high
- Why: expected aligned time grids; current layout is wrong; correct behaviour is known from the screenshot and operator ask

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
- Rationale: localized Chart.js layout; test and harden stay on; focused sequential review is enough; no inspect-loop isolation needed because production room view is the bar

## Inputs
- Research: none
- Model: none
- Sandbox: none
- Evidence: room-view screenshot (Project store `media/room-view-grid-misalignment.png`)

## Pass criteria
- On room view, a vertical ruler at NOW intersects the same wall-clock x on Temperature, Heating Power & Price, and Disturbances (desktop).
- The same holds for at least one shared hour tick (grid line), not only NOW.
- The same alignment holds at a mobile viewport (≤768px wide), after load and after a resize.
- Dual Y-axes still display on the lower two plots; series and tooltips (desktop) still work.
- Layout helper (or equivalent) has an automated check that a left-only chart and a y+y2 chart in one align group end with equal `chartArea.left` and `chartArea.right`.

## Work packages
1. SWD-552 — Share plot-box time axis across room view charts
2. SWD-553 — Tests, CalVer, changelog, App sync, browser verify

## Open items
- none

## Tracker
- Provider: jira
- Story: —
- Task: SWD-551
- Sub-tasks: SWD-552, SWD-553
- Branch: cursor/swd-551-room-plot-grid-4b29
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/685
- Classification: bug
- Workflow: fix-fast

## Next
`/implement SWD-551` — Build to ARCHITECTURE.md (same branch)
