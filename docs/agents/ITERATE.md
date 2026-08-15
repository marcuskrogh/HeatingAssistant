# Iterate: Clickable PE category guides for low-comfort identification data

## Prior work
- Task: [SWD-335](https://marcusknielsen.atlassian.net/browse/SWD-335)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/614
- Spec context: `docs/agents/PLAN-pe-robust-open-loop.md`

## Problem
- The Parameter Estimation room page shows Envelope / Heater / Solar / Open UA
  tiles (grey Not set → teal Supplied) but they are not interactive.
- Nothing explains how to generate data for each category, or how to operate
  Save Current Window → stored dataset → Use so a tile can turn teal.
- Users therefore cannot collect covering datasets without guessing.

## Clarifications
- Comfort-first: piggyback on ordinary household behaviour (overnight closed
  windows, existing heater cycling, a daytime stretch, a short planned airing).
  Do not instruct large comfort-band violations or long winter openings.
- Guides are short operational recipes, not PE essays.
- Heater means heater power scale: this room's heater output (off vs on)
  must change so the estimator can fit delivered heat versus rated power.

## Acceptance criteria
- Category tiles are clickable and keyboard-accessible. Clicking a tile opens
  (or closes) a short inline how-to for that category; one guide is open at a
  time.
- Each guide states why the category matters, a low-comfort recipe, the
  duration / identifiability target, and the store path: set the estimation
  window to that period → Save Current Window → Use the new set.
- Open UA N/A still explains that no window/door contact is configured and the
  category is not required for recommended estimation.
- Save Current Window previews which categories the configured live window
  would cover, using the existing coverage API.
- Clicking a tile highlights stored datasets that already cover that category.
- Stored Datasets starts open so the category row is visible without hunting.
- Focused tests cover the new UI seams; existing coverage tests stay green.
- CalVer bump + App package sync.

## Out of scope
- PE algorithm, category thresholds, or which samples enter the fit.
- Automated experiments / forced setpoint ramps.
- Occupancy day-gate / day-q.
- A separate docs page or long in-app essays.

## Work packages
1. Clickable tiles, comfort-first inline guides, dataset highlight, open Stored Datasets by default.
2. Live-window coverage preview on Save Current Window.
3. Tests, cache-bust, CalVer, App package sync.

## Tracker
- Task: [SWD-343](https://marcusknielsen.atlassian.net/browse/SWD-343)
- Relates: [SWD-335](https://marcusknielsen.atlassian.net/browse/SWD-335)
- Branch: `cursor/swd-343-pe-category-guides-2d07`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/615

## Next
`/review-fix SWD-343` — Review and auto-fix (single pass)
