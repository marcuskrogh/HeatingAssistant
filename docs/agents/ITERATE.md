# Iterate: Room DISTURBANCES outdoor/solar history as solid lines

## Prior work
- Task: [SWD-321](https://marcusknielsen.atlassian.net/browse/SWD-321)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/607
- Spec context: `docs/agents/PLAN-disturbances-history-points.md`

## Problem
- After evaluating SWD-321, historical Outdoor Temperature and Solar Gain
  on the room DISTURBANCES chart as Measured-style points are too cluttered
  and messy.
- Restore those series to solid lines (pre-SWD-321 styling). Keep the grey
  outdoor and yellow solar colours. Forecasts stay dashed.

## Clarifications
- Restore solar history fill (`rgba(255,213,79,0.08)`) with the solid line,
  matching the pre-SWD-321 chart.
- Indoor Measured on the temperature chart stays points.

## Acceptance criteria
- Outdoor Temperature history is a solid `#90a4ae` line (`borderWidth: 2`),
  not `showLine: false` points.
- Solar Gain history is a solid `#ffd54f` line on `y2`, with the same fill
  as before SWD-321.
- Outdoor Forecast and Solar Gain Forecast stay dashed continuous lines.
- Indoor Measured remains points.
- Tests, CalVer 2026.08.32, changelog, App sync.

## Out of scope
- Indoor Measured styling.
- Forecast dash style.
- Backend resampling or interpolating history.

## Work packages
1. Restore DISTURBANCES outdoor/solar history to solid lines (SWD-435)
2. Tests, CalVer, changelog, App sync for DISTURBANCES lines (SWD-436)

## Tracker
- Task: [SWD-434](https://marcusknielsen.atlassian.net/browse/SWD-434)
- Relates: [SWD-321](https://marcusknielsen.atlassian.net/browse/SWD-321)
- Sub-tasks: [SWD-435](https://marcusknielsen.atlassian.net/browse/SWD-435),
  [SWD-436](https://marcusknielsen.atlassian.net/browse/SWD-436)
- Branch: `cursor/swd-434-disturbances-history-lines-071f`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/636

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/636 (`161ad7f`)
