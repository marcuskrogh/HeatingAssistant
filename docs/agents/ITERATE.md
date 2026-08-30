# Iterate: Ingress LOAD ERROR — extendDatasetToNow not found

## Prior work
- Task: [SWD-445](https://marcusknielsen.atlassian.net/browse/SWD-445)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/642
- Spec context: `docs/agents/ADOPT.md` (Ingress panel split)

## Problem
- After the SWD-445 page-detail split, the Heating Assistant app UI fails to
  load with `LOAD ERROR — Importing binding name 'extendDatasetToNow' is not found.`
- `room-detail-history.js` still named-imports `extendDatasetToNow` from
  `time-series-chart.js`. That helper now lives on `charts/room-charts.js`.

## Acceptance criteria
- `room-detail-history.js` imports `extendDatasetToNow` from `room-charts.js`.
- Named ESM imports under `heatingassistant/app/static/js` resolve to exports
  on the target module.
- Cache-bust token on the dashboard entry and the history module is 144.
- Tests, CalVer 2026.08.34, changelog, App sync.

## Out of scope
- Further panel splits.
- Moving `extendDatasetToNow` back onto `time-series-chart.js`.

## Work packages
1. Import `extendDatasetToNow` from room-charts (SWD-448)
2. Tests, CalVer, changelog, App sync for panel import (SWD-449)

## Tracker
- Task: [SWD-447](https://marcusknielsen.atlassian.net/browse/SWD-447)
- Relates: [SWD-445](https://marcusknielsen.atlassian.net/browse/SWD-445)
- Sub-tasks: [SWD-448](https://marcusknielsen.atlassian.net/browse/SWD-448),
  [SWD-449](https://marcusknielsen.atlassian.net/browse/SWD-449)
- Branch: `cursor/swd-447-extend-dataset-import-9922`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/645

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/645 (`d82931e`)
