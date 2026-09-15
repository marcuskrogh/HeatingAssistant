# Implementation plan: Room-view wall temperature

## Summary
- Show the 2R2C wall/mass node on the room temperature plot so leftover wall heat is visible when diagnosing NMPC preheat.
- Historical series is the EKF wall estimate. Predicted series is the same U* plant roll as air forecast. No measurement markers.

## Scope / Decisions / Constraints
- Room-view temperature chart only (Tuning preview may show the forecast segment of the same series).
- Transparent gray line; solid history, dashed forecast (same pattern as filtered air).
- No planning-`x0` rewrite. Display only.
- Out of scope: estimator retune, Lovelace YAML, overview tiles.

## Classification
- Class: tweak
- Confidence: high
- Why: small intentional plot delta; expected behaviour is specified.

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
- Rationale: Localized chart + payload; cheapest binding that covers risk.

## Inputs
- Research: none
- Model: none

## Acceptance criteria
- Room temperature chart draws a gray wall series for history (EKF) and forecast (plan roll).
- No measured wall points.
- Forecast payload includes `wall_temperature` when the controller has a wall path.
- Synthetic `temperature_wall` entity is persisted in plot history like other plot sensors.

## Work packages
1. Persist wall estimate and forecast wall path — SWD-555
2. Room chart wall series, tests, CalVer — SWD-556

## Open items
- None.

## Tracker
- Provider: jira
- Task: [SWD-554](https://marcusknielsen.atlassian.net/browse/SWD-554)
- Sub-tasks: [SWD-555](https://marcusknielsen.atlassian.net/browse/SWD-555), [SWD-556](https://marcusknielsen.atlassian.net/browse/SWD-556)
- Relates: [SWD-548](https://marcusknielsen.atlassian.net/browse/SWD-548)
- Branch: `swd-554-room-wall-plot`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/686
- Classification: tweak
- Workflow: delta-fast

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/686
