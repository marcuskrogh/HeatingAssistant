# Implementation plan: DISTURBANCES history as Measured-style points

## Summary
- On room DISTURBANCES, plot historical Outdoor Temperature and Solar Gain as
  discrete Measured-style points instead of continuous lines.
- Forecast series remain dashed continuous lines.

## Scope / Decisions / Constraints
**In**
- `buildDisturbanceChart` in `heatingassistant/app/static/js/charts/room-charts.js`
- Outdoor history → Measured-style points (`#90a4ae`)
- Solar history → Measured-style points (`#ffd54f`, `yAxisID: 'y2'`); drop fill
- Focused regression test on dataset options
- Sync App package copy

**Out**
- Physical outdoor sensor
- Interpolating history to look like forecasts
- Backend resampling

**Decisions**
- Match Measured options from `buildTemperatureChart`:
  `borderWidth: 0`, `pointRadius: 3`, `pointHoverRadius: 5`,
  `pointBackgroundColor` / `pointBorderColor`, `showLine: false`
- Forecast Outdoor / Solar Gain Forecast unchanged (dashed lines; forecast-only
  solar fill retained)

## Classification
- Class: tweak
- Confidence: high
- Why: localized chart styling delta with clear reference pattern

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

## Tracker
- Key: SWD-321
- Branch: `cursor/swd-321-disturbances-history-points-53c4`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/607

## Next
`/review-fix SWD-321` — Focused review → fix-forward → CLEAN
